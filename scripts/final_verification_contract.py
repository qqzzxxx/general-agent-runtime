"""Runtime-owned FV preparation and receipt binding; never authors a verdict."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def engine(root):
    # Separate module instance: fixture/profile scope must not rebind the running loop.
    spec = importlib.util.spec_from_file_location(
        "fv_contract_policy", Path(__file__).resolve().parents[1] / "orchestrator.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.PROFILES_DIR = Path(root) / "profiles"
    return module


def prepare(root, state, task):
    """Expand an explicit new Supervisor request before the decision commit point.

    No historical receipt or authorized task is modified. The caller holds the
    control/fence lock and persists both candidates before hashing the decision.
    """
    if "FINAL_VERIFICATION_REQUEST" not in task:
        return state, task
    import executor_completion as completion
    import supervisor_control as control
    runtime = completion.read_runtime_state(root) or {}
    identity = control.normalize_identity(task)
    if (state.get("status") != "WAITING_EXECUTOR"
            or (state.get("last_supervisor_decision") or {}).get("decision") != "FINAL_VERIFICATION"
            or task.get("TASK_KIND") != "FINAL_VERIFICATION"):
        raise RuntimeError("FV preparation requires a new FINAL_VERIFICATION Supervisor decision")
    if (identity["MESSAGE_ID"] <= max(int(runtime.get("last_consumed_message_id") or 0),
                                      int(runtime.get("last_dispatched_message_id") or 0))
            or identity["MESSAGE_ID"] in (runtime.get("retired_message_ids") or [])
            or completion.lookup_entries(root, identity["MESSAGE_ID"])
            or completion.load_claim(root, identity)[1].exists()):
        raise RuntimeError("FV preparation requires a fresh, unclaimed identity")
    if any((Path(root) / "control" / flag).exists() for flag in ("STOP", "HUMAN_REVIEW")):
        raise RuntimeError("FV preparation blocked by STOP/HUMAN_REVIEW")
    request = task["FINAL_VERIFICATION_REQUEST"]
    if (not isinstance(request, dict) or set(request) - {"CRITICAL_CLAIMS", "EXECUTION_MODE"}
            or "FINAL_VERIFICATION_GATE" in task):
        raise RuntimeError("Malformed FV request or conflicting authored gate")
    o = engine(root)
    policy, _ = o._resolve_fv_policy_for_state(state)
    mode = o.final_verification_policy_execution_mode(policy)
    # Compatibility input is an assertion, never a model-controlled permission.
    # Unknown, empty, null, or conflicting values fail before any files change.
    if "EXECUTION_MODE" in request and request["EXECUTION_MODE"] != mode:
        raise RuntimeError(
            f"FV request EXECUTION_MODE {request['EXECUTION_MODE']!r} conflicts with "
            f"Runtime policy binding {mode!r}; omit EXECUTION_MODE from the request. "
            "Keep verification method in OBJECTIVE and lifecycle in final_verification.status.")
    claims = request.get("CRITICAL_CLAIMS")
    ok, detail = o.validate_critical_claims(claims, policy["claim_count"]["min"], policy["claim_count"]["max"])
    if not ok:
        raise RuntimeError(detail)
    state, task = copy.deepcopy(state), copy.deepcopy(task)
    fv = state.get("final_verification")
    if fv is None:
        fv = {}
    if not isinstance(fv, dict) or fv.get("status") not in (None, "NOT_STARTED", "REQUIRED", "PENDING", "REVERIFY"):
        raise RuntimeError("FV request requires NOT_STARTED/PENDING/REVERIFY state")
    claims_hash = o.canonical_claims_hash(claims)
    # Supplied metadata is validated, never silently corrected.
    for key, expected in (("policy_id", policy["policy_id"]), ("policy_version", policy["policy_version"]),
                          ("claims_hash", claims_hash), ("critical_claims", claims)):
        unspecified = key not in fv or fv[key] is None or (key == "critical_claims" and fv[key] == [])
        if not unspecified and (type(fv[key]) is not type(expected) or fv[key] != expected):
            raise RuntimeError(f"FV request conflicts with state {key}")
    gate = {"CONTRACT_VERSION": 1, "POLICY_ID": policy["policy_id"],
            "POLICY_VERSION": policy["policy_version"], "CLAIMS_HASH": claims_hash,
            "CLAIM_COUNT": len(claims), "CRITICAL_CLAIMS": claims,
            "EXECUTION_MODE": mode,
            "POLICY_SNAPSHOT": policy, "POLICY_SHA256": digest(policy)}
    fv.update(required=True, status="PENDING", policy_id=policy["policy_id"],
              policy_version=policy["policy_version"], critical_claims=claims, claims_hash=claims_hash,
              verification_message_id=None, verification_receipt_sha256=None, verified_at=None)
    state["final_verification"] = fv
    task.pop("FINAL_VERIFICATION_REQUEST")
    task["FINAL_VERIFICATION_GATE"] = gate
    task.setdefault("EXECUTOR_PROTOCOL", []).append(
        "FV contract v1: stage RECEIPT.FINAL_VERIFICATION_RESULTS with OVERALL_STATUS "
        "(PASS/FAIL/INCONCLUSIVE), exact CLAIM_RESULTS (claim_id, status, checks, evidence_pointers, "
        "auditor_note), and SANDBOX/ISOLATION_INCIDENT when applicable. "
        "Use the immutable FINAL_VERIFICATION_GATE claims and POLICY_SNAPSHOT. "
        "Runtime supplies FINAL_VERIFICATION identity/hash/policy metadata at completion commit. "
        "Do not substitute a prose report for structured results.")
    o.validate_final_verification_dispatch(state, task)
    return state, task


def archived_task(root, authorization):
    import supervisor_control as control
    ok, reason = control.verify_archive_binding(root, authorization)
    if not ok:
        raise RuntimeError(f"FV archive binding invalid: {reason}")
    path = Path(root) / authorization["SUPERVISOR_DISPATCH_ARCHIVE"]["archive_file"]
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != authorization.get("TO_ZCODE_SHA256"):
        raise RuntimeError("FV archive changed while reading validated task")
    task = control._parse_dispatch_bytes(data)
    if control.normalize_identity(task) != control.normalize_identity(authorization):
        raise RuntimeError("FV archive identity mismatch")
    gate = task.get("FINAL_VERIFICATION_GATE")
    is_fv = task.get("TASK_KIND") == "FINAL_VERIFICATION" or isinstance(gate, dict)
    if "IS_FINAL_VERIFICATION" in authorization and authorization["IS_FINAL_VERIFICATION"] is not is_fv:
        raise RuntimeError("FV authorization kind differs from archived dispatch")
    if is_fv and "IS_FINAL_VERIFICATION" in authorization and authorization.get("FINAL_VERIFICATION_GATE") != gate:
        raise RuntimeError("FV authorization gate differs from archived dispatch")
    return task


def bound_policy(gate):
    if type(gate.get("CONTRACT_VERSION")) is not int or gate["CONTRACT_VERSION"] != 1:
        raise RuntimeError("Unsupported FV contract version")
    policy = gate.get("POLICY_SNAPSHOT")
    if (not isinstance(policy, dict) or digest(policy) != gate.get("POLICY_SHA256")
            or type(gate.get("POLICY_VERSION")) is not int
            or type(gate.get("CLAIM_COUNT")) is not int
            or policy.get("policy_id") != gate.get("POLICY_ID")
            or policy.get("policy_version") != gate.get("POLICY_VERSION")
            or digest(gate.get("CRITICAL_CLAIMS")) != gate.get("CLAIMS_HASH")
            or not isinstance(gate.get("CRITICAL_CLAIMS"), list)
            or len(gate["CRITICAL_CLAIMS"]) != gate.get("CLAIM_COUNT")):
        raise RuntimeError("FV immutable policy/claims binding invalid")
    return policy


def construct_receipt(root, authorization, receipt):
    """Only the fresh contract permits construction; legacy receipts stay exact."""
    if "SUPERVISOR_DISPATCH_ARCHIVE" not in authorization:
        return receipt
    task = archived_task(root, authorization)
    gate = task.get("FINAL_VERIFICATION_GATE") or {}
    if "CONTRACT_VERSION" not in gate:
        return receipt
    if authorization.get("IS_FINAL_VERIFICATION") is not True:
        raise RuntimeError("FV contract authorization kind missing")
    import supervisor_control as control
    if control.normalize_identity(receipt) != control.normalize_identity(task):
        raise RuntimeError("FV result identity differs from archived task")
    bound_policy(gate)
    results = receipt.get("FINAL_VERIFICATION_RESULTS")
    allowed = {"OVERALL_STATUS", "CLAIM_RESULTS", "SANDBOX", "ISOLATION_INCIDENT"}
    if ("FINAL_VERIFICATION" in receipt or not isinstance(results, dict)
            or set(results) - allowed or results.get("OVERALL_STATUS") not in {"PASS", "FAIL", "INCONCLUSIVE"}
            or not isinstance(results.get("CLAIM_RESULTS"), list)):
        raise RuntimeError("FV requires structured FINAL_VERIFICATION_RESULTS; protocol metadata is Runtime-owned")
    rows = results["CLAIM_RESULTS"]
    ids = [row.get("claim_id") for row in rows if isinstance(row, dict)]
    expected = [c["claim_id"] for c in gate["CRITICAL_CLAIMS"]]
    policy = gate["POLICY_SNAPSHOT"]
    if (len(ids) != len(rows) or any(not isinstance(cid, str) for cid in ids)
            or len(ids) != len(set(ids)) or set(ids) != set(expected)
            or any(row.get("status") not in policy["allowed_result_statuses"]
                   or not isinstance(row.get("checks"), dict) for row in rows)):
        raise RuntimeError("FV result claim coverage/status/checks malformed")
    result = copy.deepcopy(receipt)
    result["FINAL_VERIFICATION"] = {
        **copy.deepcopy(results),
        **{k: gate[k] for k in ("POLICY_ID", "POLICY_VERSION", "CLAIMS_HASH", "POLICY_SHA256", "EXECUTION_MODE")},
        "TASK_IDENTITY": {k: task[k] for k in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")},
        "DISPATCH_SHA256": authorization["TO_ZCODE_SHA256"],
    }
    return result
