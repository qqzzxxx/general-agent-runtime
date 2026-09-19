"""Task-oriented Executor entry; cooperative same-user fencing, not an OS sandbox.

Fresh wakes call enter(root). Only the owner retaining its token may call
enter(root, resume_token=token). Tokens are never recovered from Runtime files.
All discovery, acquisition, preparation and projection share the fence mutex.
Runtime finish constructs publication and completion through the existing helpers.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path

import executor_claim as claim
import executor_completion as completion
import executor_fence as fence
import final_verification_contract as fv
import ordinary_dispatch
import supervisor_control as control

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
EXIT_CODES = {"READY": 0, "NO_WORK": 10, "DUPLICATE": 10,
              "STALE_TASK": 11, "NOT_AUTHORIZED": 12, "ERROR": 12}
WIRE_ONLY = {*fence.IDENTITY_KEYS, "PROTOCOL_VERSION", "CLAIM_PROTOCOL_VERSION",
             "ISSUED_AT", "EXECUTOR_MODEL_FAMILY", "SCHEDULER_GRACE_SECONDS",
             "SUPERVISOR_REVIEW_EFFORT", "EXECUTOR_PROTOCOL"}


def task_view(task: dict) -> dict:
    """Keep semantics and FV metadata intact; omit only known redundant mechanics.

    Unknown fields and custom protocol instructions are deliberately retained.
    The immutable wire/archive stays unchanged for old clients and completion.
    """
    result = copy.deepcopy({k: v for k, v in task.items() if k not in WIRE_ONLY})
    protocol = task.get("EXECUTOR_PROTOCOL", [])
    if not isinstance(protocol, list) or any(not isinstance(v, str) for v in protocol):
        raise ValueError("invalid_executor_protocol")
    extra = [v for v in protocol if v not in ordinary_dispatch.EXECUTOR_PROTOCOL]
    # Translate only the exact Runtime-generated FV packaging instruction; keep
    # its claim/check/judgment requirements and all custom instructions intact.
    extra = [v.replace("stage RECEIPT.FINAL_VERIFICATION_RESULTS",
                       "provide completion.FINAL_VERIFICATION_RESULTS in the semantic finish result")
             if v == fv.RESULT_PROTOCOL else v for v in extra]
    if extra:
        result["EXECUTOR_PROTOCOL"] = extra
    return result


def stopped(status: str, reason: str) -> dict:
    return {"schema_version": SCHEMA_VERSION, "status": status,
            "action": "STOP", "reason": reason}


def _enter(root: Path = DEFAULT_ROOT, *, resume_token: str | None = None,
          contract_version: int = 1) -> dict:
    """Return a capability-bearing view only after every existing gate succeeds.

    A token is proof of the original claim, never permission to claim the current
    inbox. Resume cannot acquire, rotate credentials, or fall back to fresh entry.
    Failures after acquisition intentionally retain the permanent claim.
    """
    root = Path(root).resolve()
    try:
        if type(contract_version) is not int or contract_version not in (1, 2):
            return stopped("NOT_AUTHORIZED", "unsupported_executor_contract")
        if resume_token is not None and (not isinstance(resume_token, str)
                                         or not 1 <= len(resume_token) <= 256):
            return stopped("NOT_AUTHORIZED", "invalid_resume_token")
        with fence.runtime_lock(root):
            control.reconcile_control_transactions_locked(root)
            runtime = completion.read_runtime_state(root)
            if runtime is None:
                return stopped("NOT_AUTHORIZED", "runtime_state_unavailable")
            auth = runtime.get("authorized_dispatch")
            if auth is None:
                if resume_token is not None or (root / "TO_ZCODE.md").exists():
                    return stopped("NOT_AUTHORIZED", "authorization_missing")
                return stopped("NO_WORK", "no_authorized_task")
            identity = completion._validated_identity(auth, "entry authorization")
            # These checks run for both acquisition and resume. A damaged pointer
            # must never become an excuse to claim again or return task contents.
            last = claim.read_last_processed(root)["MESSAGE_ID"]
            if identity["MESSAGE_ID"] <= last:
                return stopped("STALE_TASK", "already_processed")
            retired = runtime.get("retired_message_ids", [])
            if not isinstance(retired, list) or any(type(v) is not int for v in retired):
                return stopped("NOT_AUTHORIZED", "retired_message_ids_malformed")
            if (identity["MESSAGE_ID"] in retired
                    or runtime.get("timeout_notified_for_nonce") == identity["NONCE"]):
                return stopped("STALE_TASK", "attempt_retired_or_timed_out")
            # Enforce fenced acquisition even for archive-less/legacy candidates.
            # Resume keeps the existing, token-proven legacy completion recovery.
            fence.check_locked(root, identity, require_claim=resume_token is not None,
                               claim_token=resume_token)
            raw = (root / "TO_ZCODE.md").read_bytes()
            if hashlib.sha256(raw).hexdigest() != auth.get("TO_ZCODE_SHA256"):
                return stopped("NOT_AUTHORIZED", "inbox_hash_mismatch")
            task = control._parse_dispatch_bytes(raw)
            if completion._validated_identity(task, "entry task") != identity:
                return stopped("NOT_AUTHORIZED", "inbox_identity_mismatch")
            if contract_version == 2:
                import executor_contract
                view = executor_contract.project(task, check_host=True, include_utilities=False)
            else:
                view = task_view(task)
            token = resume_token
            if token is None:
                acquired = claim.acquire_locked(root, *[identity[k] for k in fence.IDENTITY_KEYS],
                                               fence=fence)
                if acquired.code != claim.EXIT_ACQUIRED:
                    status = {claim.EXIT_CLAIM_EXISTS: "DUPLICATE",
                              claim.EXIT_ALREADY_PROCESSED: "STALE_TASK"}.get(
                                  acquired.code, "NOT_AUTHORIZED")
                    return stopped(status, acquired.message)
                token = acquired.claim_token
            work = fence.prepare(root, identity, claim_token=token)
            pid, project = fence.check_locked(root, identity, claim_token=token)
            # Bind the returned semantics to the exact bytes checked above, even
            # if an uncooperative same-user writer replaced the inbox during IO.
            if (root / "TO_ZCODE.md").read_bytes() != raw:
                return stopped("NOT_AUTHORIZED", "inbox_changed_during_entry")
            staging = fence.safe_path(project, "completion_staging")
            if contract_version == 2:
                # Locations support native tools; they confer no authority beyond
                # this live attempt. Never expose staging or control paths.
                import executor_finish
                view["locations"] = {
                    "project_inputs": str(project), "work": str(work),
                    "candidate_work_open": not executor_finish.boundary_started(root, project, identity),
                    "publication_roots": ([f"evidence/verification/{work.name}", f"reports/verification/{work.name}"]
                                          if "verification" in view["context"] else ["workspace", "evidence", "reports"]),
                }
                executor_contract.bind_bootstrap(view, root)
                return {"schema_version": 2, "status": "READY", "action": "EXECUTE",
                        "session": token, "contract": view}
            return {"schema_version": SCHEMA_VERSION, "status": "READY", "action": "EXECUTE",
                    "resumed": resume_token is not None, "task": view,
                    "attempt": {**identity, "PROJECT_ID": pid,
                                "expires_at": auth["EXPIRES_AT"], "claim_token": token},
                    "paths": {"runtime_root": str(root), "project_root": str(project),
                              "attempt_workspace": str(work), "completion_staging_root": str(staging)},
                    "capabilities": {"candidate_writes": "attempt_workspace_only",
                                     "publication_roots": ["workspace/", "evidence/", "reports/"],
                                     "publication_excludes": ["reports/USER_STATUS.md"],
                                     "publication_max_file_bytes": completion.EVIDENCE_MAX_FILE_BYTES,
                                     "canonical_inputs": "read_only", "publication": "executor_finish.py",
                                     "completion": "executor_finish.py",
                                     "direct_canonical_writes": False, "os_sandbox": False}}
    except (fence.FenceError, completion.CompletionError, control.ControlError,
            ValueError, TypeError, KeyError, RuntimeError) as exc:
        return stopped("NOT_AUTHORIZED", str(exc))
    except OSError as exc:
        return stopped("ERROR", type(exc).__name__)


def enter(root: Path = DEFAULT_ROOT, *, resume_token: str | None = None,
          contract_version: int = 1) -> dict:
    result = _enter(root, resume_token=resume_token, contract_version=contract_version)
    if type(contract_version) is int and contract_version == 2:
        result["schema_version"] = 2
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--resume-token", default=None)
    parser.add_argument("--contract-version", type=int, choices=(1, 2), default=1)
    args = parser.parse_args(fence.token_cli_args(argv, "--resume-token"))
    result = enter(args.root, resume_token=args.resume_token, contract_version=args.contract_version)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return EXIT_CODES[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
