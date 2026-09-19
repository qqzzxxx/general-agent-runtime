"""Runtime-owned publication and completion from an owner's semantic result.

No new authority: preparation is a frozen candidate, publication uses the existing
fence, and only the existing completion ledger commits completion. All operations
share the Runtime mutex. This remains a cooperative same-user filesystem boundary.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import executor_completion as completion
import executor_fence as fence
import final_verification_contract as fv
import supervisor_control as control

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXIT_CODES = {"FINISHED": 0, "ALREADY_FINISHED": 10, "SEALED": 11,
              "NOT_AUTHORIZED": 12, "INVALID_RESULT": 14, "ERROR": 15}
RESULT_KEYS = {"artifacts", "outcome", "findings", "evidence", "limitations", "completion"}
RESERVED = {*completion.IDENTITY_KEYS, "PROJECT_ID", "PROTOCOL_VERSION", "STATUS",
            "CREATED_AT", "COMMIT_ID", "COMMITTED_AT", "CONSUMED_AT", "SEALED_AT",
            "RECEIPT", "EVIDENCE", "DELIVERABLES", "FINAL_VERIFICATION",
            "KEY FINDINGS", "EVIDENCE POINTERS", "LIMITATIONS", "CLAIMS_HASH",
            "POLICY_ID", "POLICY_VERSION", "TASK_IDENTITY", "CLAIM_DIR",
            "FENCE_VERSION", "SCHEMA_VERSION", "FINAL_VERIFICATION_GATE"}


def response(status, reason=None, commit_id=None):
    result = {"schema_version": 1, "status": status, "action": "STOP"}
    if reason:
        result["reason"] = reason
    if commit_id:
        result["commit_id"] = commit_id
    return result


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def read_json(base, relative):
    # Shares bounded reads, duplicate-key rejection and topology rules with provenance.
    _, result = fence.read_publication_json(base, relative)
    encoded(result)  # Reject non-finite JSON numbers, including nested values.
    return result


def semantic_receipt(result, identity):
    if (not isinstance(result, dict) or not RESULT_KEYS <= set(result)
            or set(result) - RESULT_KEYS - {"verification"}):
        raise ValueError("result requires artifacts, outcome, findings, evidence, limitations, completion; verification is optional")
    if result["outcome"] not in ("COMPLETED", "PARTIAL", "FAILED", "BLOCKED", "INCONCLUSIVE", "ABORTED_BY_USER"):
        raise ValueError("unsupported semantic outcome")
    for key in ("findings", "evidence", "limitations"):
        values = result[key]
        if (not isinstance(values, list) or len(values) > 100
                or any(not isinstance(v, str) or not v.strip() or len(v) > 16000 for v in values)):
            raise ValueError(f"{key} must be an array of at most 100 nonempty strings")
    extra = result["completion"]
    if not isinstance(extra, dict) or any(
            key.upper() in RESERVED or key.upper().endswith(("_SHA256", "_PROTOCOL_VERSION"))
            or key.upper().startswith(("CLAIM_TOKEN", "PUBLICATION_MANIFEST", "COMPLETION_"))
            for key in extra):
        raise ValueError("completion contains Runtime-owned envelope fields")
    receipt = {"PROTOCOL_VERSION": 2, **identity, "STATUS": result["outcome"],
               "Key findings": result["findings"], "Evidence pointers": result["evidence"],
               "Limitations": result["limitations"], **extra}
    if "verification" in result:
        if "FINAL_VERIFICATION_RESULTS" in extra:
            raise ValueError("provide verification or legacy FV results, not both")
        receipt["FINAL_VERIFICATION_RESULTS"] = fv.semantic_results(result["verification"])
    return receipt


def boundary_started(root, project, identity):
    """Conservative durable barrier check under the Runtime lock.

    The result file is a replaceable candidate, including old pre-validation
    caches. Never reopen a plan, staging, publication, or ledger on corruption.
    This is not an authority check; callers must separately prove live ownership.
    """
    commit_id = completion.commit_id_for(identity["MESSAGE_ID"], identity["NONCE"])
    for base, relative in (
            (root, f"handoff/executor_finishes/{commit_id}.json"),
            (project, f"completion_staging/finish-{commit_id}"),
            (root, f"handoff/executor_publications/{commit_id}")):
        if fence.safe_path(base, relative).exists():
            return True
    ledger = fence.safe_path(root, "handoff/completion_ledger")
    return any(ledger.glob(f"completion-{identity['MESSAGE_ID']}-*.json"))


def artifact_manifests(result, work, project):
    artifacts = result["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) > fence.MAX_PUBLICATIONS:
        raise ValueError("artifacts must be a list of at most 128 path/role objects")
    manifests = {"EVIDENCE": [], "DELIVERABLES": []}
    outputs, seen = {}, set()
    for item in artifacts:
        if not isinstance(item, dict) or set(item) != {"path", "role"}:
            raise ValueError("artifact requires exactly path and role")
        relative, role = item["path"], item["role"]
        if role not in ("evidence", "deliverable", "both"):
            raise ValueError("artifact role must be evidence, deliverable or both")
        source = fence.output_path(work, relative)
        target = fence.output_path(project, relative)
        key = relative.casefold()
        if key in seen or any(key.startswith(p + "/") or p.startswith(key + "/") for p in seen):
            raise ValueError("duplicate or overlapping artifact paths")
        seen.add(key)
        if target.exists() and not target.is_file():
            raise ValueError("canonical output is not a file")
        if any(p.exists() and not p.is_dir() for p in target.parents):
            raise ValueError("canonical output parent is not a directory")
        if not source.is_file():
            raise ValueError("artifact candidate is missing")
        with source.open("rb") as handle:
            data = handle.read(completion.EVIDENCE_MAX_FILE_BYTES + 1)
        if len(data) > completion.EVIDENCE_MAX_FILE_BYTES:
            raise ValueError("artifact candidate exceeds 64 MiB")
        digest = completion.sha256_bytes(data)
        outputs[relative] = digest
        for name in manifests:
            if role == "both" or role == {"EVIDENCE": "evidence", "DELIVERABLES": "deliverable"}[name]:
                manifests[name].append({"path": relative, "sha256": digest})
    if any(len(items) > completion.EVIDENCE_MAX_ENTRIES for items in manifests.values()):
        raise ValueError("at most 64 evidence and 64 deliverable entries are supported")
    return manifests, outputs


def finish(root: Path = DEFAULT_ROOT, *, claim_token, result_path: str) -> dict:
    """Finish current authorization only with its retained owner token; never acquire.

    The result remains correctable until the validated plan is created. A retained
    live owner may replay identical preparation after a process interruption.
    A newer dispatch cannot be adopted with an old token. Ledger replay is read-only.
    """
    root = Path(root).resolve()
    try:
        with fence.runtime_lock(root):
            control.reconcile_control_transactions_locked(root)
            runtime = completion.read_runtime_state(root)
            auth = (runtime or {}).get("authorized_dispatch")
            if not isinstance(auth, dict):
                return response("NOT_AUTHORIZED", "authorization_missing")
            identity = completion._validated_identity(auth, "finish authorization")
            fence.check_claim_owner(root, identity, claim_token)
            commit_id = completion.commit_id_for(identity["MESSAGE_ID"], identity["NONCE"])
            # Even a damaged ledger must prevent any new output writes. Do not
            # rely on lookup_entries, which intentionally skips malformed records.
            directory = fence.safe_path(root, "handoff/completion_ledger")
            existing = list(directory.glob(f"completion-{identity['MESSAGE_ID']}-*.json"))
            if existing:
                if len(existing) != 1:
                    raise ValueError("ambiguous completion ledger")
                entry = read_json(root, existing[0].relative_to(root).as_posix())
                if (not isinstance(entry, dict) or entry.get("COMMIT_ID") != commit_id
                        or completion.entry_identity(entry) != identity
                        or entry.get("PROJECT_ID") != auth.get("PROJECT_ID")
                        or entry.get("COMPLETION_PROTOCOL_VERSION") != 1
                        or not completion.entry_hashes_intact(entry)):
                    raise ValueError("completion ledger integrity failure")
                if entry.get("STATUS") not in completion._LEDGER_STATUSES:
                    raise ValueError("invalid completion ledger status")
                status = "ALREADY_FINISHED" if entry["STATUS"] == completion.STATUS_COMMITTED else "SEALED"
                return response(status, commit_id=commit_id)

            pid, project = fence.check_locked(root, identity, claim_token=claim_token)
            work = fence.attempt_root(project, identity)
            result = read_json(work, result_path)
            # The token is never allowed to leak into a receipt or durable plan.
            if claim_token in encoded(result):
                raise ValueError("owner token must not appear in semantic results")
            receipt = semantic_receipt(result, identity)
            # Uses the immutable archive, preserving FV judgments and legacy
            # commit behavior. Legacy FV envelopes stay on the compatibility API.
            if "SUPERVISOR_DISPATCH_ARCHIVE" in auth:
                task = fv.archived_task(root, auth)
            else:
                # Existing fenced, already-claimed legacy recovery still binds
                # the exact inbox. Inspect its kind; never infer ordinary work
                # just because its historical authorization lacks FV metadata.
                raw = (root / "TO_ZCODE.md").read_bytes()
                if completion.sha256_bytes(raw) != auth.get("TO_ZCODE_SHA256"):
                    raise fence.FenceError("inbox_hash_mismatch")
                task = control._parse_dispatch_bytes(raw)
            if (task.get("TASK_KIND") == "FINAL_VERIFICATION" or task.get("FINAL_VERIFICATION_GATE")):
                if ("SUPERVISOR_DISPATCH_ARCHIVE" not in auth
                        or (task.get("FINAL_VERIFICATION_GATE") or {}).get("CONTRACT_VERSION") != 1):
                    raise ValueError("legacy FV requires the existing completion compatibility API")
            elif "verification" in result or "FINAL_VERIFICATION_RESULTS" in receipt:
                raise ValueError("verification judgments require an authorized FV task")
            wrapped = fv.construct_receipt(root, auth, receipt)
            if len(encoded(wrapped).encode("utf-8")) > completion.RECEIPT_MAX_BYTES:
                raise ValueError("constructed receipt exceeds 64 KiB")
            manifests, outputs = artifact_manifests(result, work, project)
            # Do not silently include old publications omitted by this package.
            prior = fence.collect_locked(root, {**identity, "PROJECT_ID": pid})
            for relative in outputs:
                if fence.publication_policy(root, identity, relative):
                    # Replay of our own previously published evidence is read-only.
                    published = any(r["path"] == relative for r in prior["publications"])
                    if not published and fence.output_path(project, relative).exists():
                        raise ValueError("FV cannot replace an existing canonical file")
            for record in prior["publications"]:
                if outputs.get(record["path"]) != record["sha256"]:
                    raise ValueError("existing publication differs from finish package")
                fence.output_path(project, record["path"])
                completion._check_evidence_list(
                    [{"path": record["path"], "sha256": record["sha256"]}], project, "publication replay")
            staging = {"COMPLETION_STAGING_SCHEMA_VERSION": 1, **identity,
                       "PROJECT_ID": pid, "STATUS": "STAGING_READY",
                       "CREATED_AT": completion.now_iso(), "RECEIPT": receipt, **manifests}
            plan_relative = f"handoff/executor_finishes/{commit_id}.json"
            plan_path = fence.safe_path(root, plan_relative)
            plan = {"schema_version": 1, "dispatch_sha256": auth["TO_ZCODE_SHA256"],
                    "result_sha256": completion.canonical_json_sha256(result), "staging": staging}
            if plan_path.exists():
                saved = read_json(root, plan_relative)
                completion._check_timestamp(saved["staging"]["CREATED_AT"], "finish preparation time")
                staging["CREATED_AT"] = saved["staging"]["CREATED_AT"]
                if saved != plan:
                    raise ValueError("finish preparation differs: results or candidate bytes changed")
            if len(encoded(staging).encode("utf-8")) > completion.STAGING_MAX_BYTES:
                raise ValueError("constructed staging exceeds 256 KiB")
            if len(encoded(plan).encode("utf-8")) + 1 > completion.STAGING_MAX_BYTES:
                raise ValueError("finish preparation exceeds 256 KiB")
            staging_relative = f"completion_staging/finish-{commit_id}"
            stage_dir = fence.safe_path(project, staging_relative)
            stage_file = fence.safe_path(stage_dir, "staging.json")
            if stage_dir.exists() and not stage_dir.is_dir():
                raise ValueError("finish staging is not a directory")
            if stage_file.exists() and read_json(stage_dir, "staging.json") != staging:
                raise ValueError("Runtime finish staging changed")
            fence.check_locked(root, identity, claim_token=claim_token)
            if not plan_path.exists():
                completion._atomic_create(plan_path, encoded(plan) + "\n")
            # Hashes pin the snapshot across IO and replay. publish checks its own
            # copied snapshot and deadline again before each canonical replace.
            for relative, digest in outputs.items():
                record = next((r for r in prior["publications"] if r["path"] == relative), None)
                if record:
                    completion._check_evidence_list([{"path": relative, "sha256": digest}], project, "publication replay")
                else:
                    fence.publish(root, identity, relative, digest, claim_token=claim_token)
            fence.safe_path(project, staging_relative)
            fence.safe_path(stage_dir, "staging.json")
            if not stage_file.exists():
                completion._atomic_create(stage_file, encoded(staging) + "\n")
            completion.commit(root, stage_dir, claim_token=claim_token, emit=False)
            return response("FINISHED", commit_id=commit_id)
    except (fence.FenceError, control.ControlError) as exc:
        return response("NOT_AUTHORIZED", str(exc))
    except completion.CompletionError as exc:
        status = {10: "ALREADY_FINISHED", 11: "SEALED", 12: "NOT_AUTHORIZED",
                  13: "NOT_AUTHORIZED", 14: "INVALID_RESULT"}.get(exc.code, "ERROR")
        return response(status, str(exc))
    except (ValueError, TypeError, KeyError, RuntimeError, UnicodeError, RecursionError) as exc:
        return response("INVALID_RESULT", str(exc))
    except OSError as exc:
        return response("ERROR", type(exc).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--claim-token", required=True)
    parser.add_argument("--result", required=True, help="Forward-slash path relative to the attempt workspace")
    args = parser.parse_args(fence.token_cli_args(argv, "--claim-token"))
    result = finish(args.root, claim_token=args.claim_token, result_path=args.result)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
