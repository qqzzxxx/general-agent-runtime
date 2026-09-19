"""Runtime-owned FV preparation and receipt binding; never authors a verdict."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path


RESULT_PROTOCOL = (
        "FV contract v1: stage RECEIPT.FINAL_VERIFICATION_RESULTS with OVERALL_STATUS "
        "(PASS/FAIL/INCONCLUSIVE), exact CLAIM_RESULTS (claim_id, status, checks, evidence_pointers, "
        "auditor_note), and SANDBOX/ISOLATION_INCIDENT when applicable. "
        "Use the immutable FINAL_VERIFICATION_GATE claims and POLICY_SNAPSHOT. "
        "Runtime supplies FINAL_VERIFICATION identity/hash/policy metadata at completion commit. "
        "Do not substitute a prose report for structured results.")


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


def record_prepared_identity(root, runtime, identity, claims_hash, prepared_at):
    """HUMAN-DECISION-FV-BRIDGE-V1 gate provenance.

    Durably record that this exact (MESSAGE_ID, CLAIMS_HASH) gate was
    Runtime-prepared. Dispatch validation rejects any gate-carrying
    FINAL_VERIFICATION task without such a record, making "only the Runtime
    constructs gates" mechanical on every path instead of a model-side promise.
    Idempotent on crash replay (deduplicated by identity) and bounded to the
    most recent 50 preparations.
    """
    record = {
        "message_id": int(identity["MESSAGE_ID"]),
        "task_id": identity.get("TASK_ID"),
        "stage_id": identity.get("STAGE_ID"),
        "claims_hash": claims_hash,
        "prepared_at": prepared_at,
    }
    entries = [
        entry for entry in (runtime.get("final_verification_prepared_identities") or [])
        if isinstance(entry, dict)
    ]
    if not any(
        entry.get("message_id") == record["message_id"]
        and str(entry.get("claims_hash") or "") == record["claims_hash"]
        for entry in entries
    ):
        entries.append(record)
    runtime["final_verification_prepared_identities"] = entries[-50:]
    path = Path(root) / "control" / "orchestrator_runtime.json"
    tmp = path.with_name(path.name + ".prepare.tmp")
    tmp.write_bytes(
        json.dumps(runtime, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")
    os.replace(tmp, path)
    return record


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
    task.setdefault("EXECUTOR_PROTOCOL", []).append(RESULT_PROTOCOL)
    o.validate_final_verification_dispatch(state, task)
    # Provenance is part of gate authorship: record it only after the fully
    # constructed task passed dispatch validation.
    record_prepared_identity(root, runtime, identity, claims_hash, o.stamp())
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


def semantic_results(verification):
    """Translate substantive judgments; identity/policy/envelopes stay internal.

    Keep the legacy completion API unchanged. Never infer a passing judgment or
    fill absent claim rows, checks, evidence, or auditor observations.
    """
    mapping = {"overall_status": "OVERALL_STATUS", "claims": "CLAIM_RESULTS",
               "sandbox": "SANDBOX", "isolation_incident": "ISOLATION_INCIDENT"}
    if (not isinstance(verification, dict)
            or not {"overall_status", "claims"} <= set(verification)
            or set(verification) - mapping.keys()):
        raise ValueError("verification requires overall_status and claims; only sandbox and isolation_incident are optional")
    rows = verification["claims"]
    required = {"claim_id", "status", "checks", "evidence_pointers", "auditor_note"}
    if not isinstance(rows, list):
        raise ValueError("verification claims must be an array")
    for row in rows:
        if (not isinstance(row, dict) or set(row) != required
                or not isinstance(row["evidence_pointers"], list)
                or any(not isinstance(p, str) or not p.strip() for p in row["evidence_pointers"])
                or not isinstance(row["auditor_note"], str) or not row["auditor_note"].strip()):
            raise ValueError("verification claim requires claim_id, status, checks, evidence_pointers (strings), and nonempty auditor_note")
    if "sandbox" in verification and not isinstance(verification["sandbox"], dict):
        raise ValueError("verification sandbox must be an object")
    if "isolation_incident" in verification and type(verification["isolation_incident"]) is not bool:
        raise ValueError("verification isolation_incident must be boolean")
    return {mapping[key]: copy.deepcopy(value) for key, value in verification.items()}


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
    # FV-REQUIRED-CHECKS-V1: require the bound policy's per-claim-type check
    # fields to be PRESENT for claims reported as SUPPORTED or
    # PARTIALLY_SUPPORTED — those are the verdicts that can be accepted, so
    # omitting the policy-required fields would doom the receipt to mechanical
    # failure after staging (F-004). Negative verdicts (WEAK, UNSUPPORTED,
    # CONTRADICTED, NOT_VERIFIABLE) are exempt: an honest "could not verify"
    # may have no evidence fields to cite, and the orchestrator's mechanical
    # evaluation still owns the value semantics.
    gate_claims = {c.get("claim_id"): c for c in gate.get("CRITICAL_CLAIMS", [])
                   if isinstance(c, dict)}
    required_by_type = policy.get("claim_types") or {}
    affirmative = {"SUPPORTED", "PARTIALLY_SUPPORTED"}
    issues = []
    for row in rows:
        if str(row.get("status") or "").upper() not in affirmative:
            continue
        claim = gate_claims.get(row.get("claim_id")) or {}
        rules = (required_by_type.get(str(claim.get("claim_type") or "").upper())
                 or {}).get("required_checks", [])
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}
        for rule in rules:
            if rule.get("field") not in checks:
                issues.append(f"{row.get('claim_id')}: missing policy-required "
                              f"check field {rule.get('field')!r}")
    if issues:
        raise RuntimeError(
            "FV result checks omit policy-required fields: " + "; ".join(issues))
    result = copy.deepcopy(receipt)
    result["FINAL_VERIFICATION"] = {
        **copy.deepcopy(results),
        **{k: gate[k] for k in ("POLICY_ID", "POLICY_VERSION", "CLAIMS_HASH", "POLICY_SHA256", "EXECUTION_MODE")},
        "TASK_IDENTITY": {k: task[k] for k in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")},
        "DISPATCH_SHA256": authorization["TO_ZCODE_SHA256"],
    }
    return result
