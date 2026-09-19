"""Audited HUMAN_REVIEW -> SUPERVISOR_TURN resume mechanism.

This control-plane tool has two explicit phases:

* prepare: bind human-authored decision content to the exact current project-state hash;
* apply: validate the immutable receipt and quiescent Runtime, archive it, and perform
  only the HUMAN_REVIEW -> SUPERVISOR_TURN project lifecycle transition.

It never creates or edits TO_ZCODE.md, allocates a MESSAGE_ID, chooses a stage,
invokes Codex/ZCode, or changes research conclusions.
"""
from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import importlib.util
import json
import re
import sys
import uuid
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_CONFLICT = 3
EXIT_STALE_OR_DUPLICATE = 4
EXIT_INTERNAL = 5


class ResumeError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def load_runtime_module(root: Path):
    orchestrator = root / "orchestrator.py"
    if not orchestrator.is_file():
        raise ResumeError(EXIT_INTERNAL, f"runtime orchestrator.py not found: {orchestrator}")
    spec = importlib.util.spec_from_file_location(
        f"human_review_runtime_{uuid.uuid4().hex}", orchestrator
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind_runtime_paths(module, root)
    return module


def bind_runtime_paths(m, root: Path) -> None:
    """Use the active Runtime's own helpers while preserving project isolation."""
    root = root.resolve()
    m.ROOT = root
    m.CONTROL = root / "control"
    m.LOGS = root / "logs"
    m.HANDOFF_ARCHIVE = root / "handoff" / "archive"
    m.REPORTS = root / "reports"
    m.PROJECT_STATE = m.CONTROL / "project_state.json"
    m.RUNTIME_STATE = m.CONTROL / "orchestrator_runtime.json"
    m.SUPERVISOR_RULES = m.CONTROL / "CODEX_SUPERVISOR_RUNTIME.md"
    m.RESEARCH_STATE = root / "RESEARCH_STATE.md"
    m.COMMERCIAL_GOAL = m.CONTROL / "CROSS_BORDER_GOAL.md"
    m.TO_ZCODE = root / "TO_ZCODE.md"
    m.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
    m.ZCODE_DONE = root / "ZCODE_DONE.flag"
    m.ZCODE_LAST_PROCESSED = root / "ZCODE_LAST_PROCESSED.txt"
    m.STOP_FLAG = m.CONTROL / "STOP"
    m.HUMAN_REVIEW_FLAG = m.CONTROL / "HUMAN_REVIEW"
    m.LOCK_FILE = m.CONTROL / ".orchestrator.lock"
    m.CODEX_LAST_OUTPUT = root / "CODEX_LAST_OUTPUT.txt"
    m.USER_ATTENTION = m.CONTROL / "USER_ATTENTION.json"
    m.USER_STATUS_REPORT = root / "reports" / "USER_STATUS.md"
    m.ACTIVE_PROJECT_FILE = m.CONTROL / "ACTIVE_PROJECT.json"
    m.PROFILES_DIR = root / "profiles"
    m.ACTIVE_PROJECT = None


def read_json_strict(path: Path, label: str) -> dict:
    if path.is_file() and path.stat().st_size > 128 * 1024:
        raise ResumeError(EXIT_VALIDATION, f"{label} exceeds 128 KiB: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise ResumeError(EXIT_VALIDATION, f"{label} is missing or invalid: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ResumeError(EXIT_VALIDATION, f"{label} must be a JSON object: {path}")
    return value


def validate_decision_payload(payload: dict) -> dict:
    required = {"decision_content", "constraints_verbatim"}
    missing = sorted(required - set(payload))
    unknown = sorted(set(payload) - required)
    if missing or unknown:
        raise ResumeError(
            EXIT_VALIDATION,
            f"human decision input schema mismatch (missing={missing}, unknown={unknown})",
        )
    content = payload.get("decision_content")
    constraints = payload.get("constraints_verbatim")
    if not isinstance(content, str) or not content.strip() or len(content) > 24000:
        raise ResumeError(EXIT_VALIDATION, "decision_content must be a non-empty string")
    if (
        not isinstance(constraints, list)
        or not constraints
        or len(constraints) > 64
        or any(not isinstance(item, str) or not item.strip() for item in constraints)
        or sum(len(item) for item in constraints if isinstance(item, str)) > 32000
    ):
        raise ResumeError(
            EXIT_VALIDATION,
            "constraints_verbatim must be a non-empty list of non-empty strings",
        )
    return payload


def _activate_isolated_project(m, requested_project_id: str | None = None):
    try:
        active = m.activate_project_scope()
    except Exception as exc:
        raise ResumeError(EXIT_CONFLICT, f"active project validation failed: {exc}") from exc
    if active is None:
        raise ResumeError(EXIT_CONFLICT, "HUMAN_REVIEW resume requires ACTIVE_PROJECT.json")
    if requested_project_id is not None and active["project_id"] != requested_project_id:
        raise ResumeError(
            EXIT_CONFLICT,
            f"active project mismatch: requested={requested_project_id!r}, active={active['project_id']!r}",
        )
    state = m.read_project_state()
    if state.get("project_id") != active["project_id"]:
        raise ResumeError(
            EXIT_CONFLICT,
            "active project mismatch: ACTIVE_PROJECT.project_id does not match project_state.project_id",
        )
    return active, state


def _require_review_snapshot(m, state: dict) -> None:
    meta = state.get("human_review_resume")
    if isinstance(meta, dict) and meta.get("status") == "PENDING_SUPERVISOR_REVIEW":
        raise ResumeError(
            EXIT_STALE_OR_DUPLICATE,
            "duplicate resume: project already carries a pending Human Decision",
        )
    status = state.get("status")
    if status != "HUMAN_REVIEW":
        raise ResumeError(
            EXIT_CONFLICT,
            f"project status must be HUMAN_REVIEW, got {status!r}; conflict terminal or lifecycle state",
        )
    if state.get("current_task") is not None:
        raise ResumeError(EXIT_CONFLICT, "current_task must be exactly null for HUMAN_REVIEW resume")
    try:
        # A prior CONSUMED receipt and its append-only ledger are valid history and
        # must not block a later, genuinely new HUMAN_REVIEW cycle.
        m.load_verified_human_decision_for_supervisor(state)
    except RuntimeError as exc:
        raise ResumeError(EXIT_CONFLICT, f"invalid Human Decision lifecycle history: {exc}") from exc


def _acquire_resume_lock(m) -> None:
    try:
        m.acquire_lock()
    except Exception as exc:
        raise ResumeError(EXIT_CONFLICT, f"Runtime lock unavailable: {exc}") from exc


def prepare_receipt(
    m,
    *,
    project_id: str,
    decision_payload: dict,
    receipt_out: Path,
) -> dict:
    validate_decision_payload(decision_payload)
    _acquire_resume_lock(m)
    try:
        active, state = _activate_isolated_project(m, project_id)
        _require_review_snapshot(m, state)
        if receipt_out.exists():
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, f"receipt output already exists: {receipt_out}")
        receipt = {
            "schema_version": m.HUMAN_DECISION_RECEIPT_SCHEMA_VERSION,
            "receipt_id": f"human-decision-{uuid.uuid4().hex}",
            "project_id": active["project_id"],
            "previous_status": "HUMAN_REVIEW",
            "human_decision": copy.deepcopy(decision_payload),
            "submitted_at": m.stamp(),
            "previous_project_state_sha256": m.sha256(m.PROJECT_STATE),
            "previous_human_review_flag_sha256": (
                m.sha256(m.HUMAN_REVIEW_FLAG) if m.HUMAN_REVIEW_FLAG.exists() else None
            ),
            "receipt_sha256": "",
        }
        receipt["receipt_sha256"] = m.human_decision_receipt_hash(receipt)
        m.validate_human_decision_receipt(
            receipt,
            expected_project_id=active["project_id"],
            expected_previous_state_sha256=receipt["previous_project_state_sha256"],
        )
        m.atomic_create(
            receipt_out,
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        )
        reread = read_json_strict(receipt_out, "prepared human decision receipt")
        m.validate_human_decision_receipt(
            reread,
            expected_project_id=active["project_id"],
            expected_previous_state_sha256=receipt["previous_project_state_sha256"],
        )
        return reread
    finally:
        m.release_lock()


_CLAIM_HELPER = None


def _load_claim_helper():
    """Load scripts/executor_claim.py by path.

    It owns the canonical ZCODE_LAST_PROCESSED.txt wire parser shared across
    the Runtime; resume must not keep a second parsing dialect of its own.
    """
    global _CLAIM_HELPER
    if _CLAIM_HELPER is None:
        path = Path(__file__).resolve().with_name("executor_claim.py")
        if not path.is_file():
            raise ResumeError(EXIT_INTERNAL, f"executor_claim.py not found: {path}")
        spec = importlib.util.spec_from_file_location(
            f"executor_claim_helper_{uuid.uuid4().hex}", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CLAIM_HELPER = module
    return _CLAIM_HELPER


_COMPLETION_HELPER = None


def _load_completion_helper():
    """Load scripts/executor_completion.py (the Runtime completion ledger module)."""
    global _COMPLETION_HELPER
    if _COMPLETION_HELPER is None:
        path = Path(__file__).resolve().with_name("executor_completion.py")
        if not path.is_file():
            raise ResumeError(EXIT_INTERNAL, f"executor_completion.py not found: {path}")
        spec = importlib.util.spec_from_file_location(
            f"executor_completion_helper_{uuid.uuid4().hex}", path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMPLETION_HELPER = module
    return _COMPLETION_HELPER


def _handle_raw_done_signal(m, runtime: dict) -> None:
    """COMPLETION-SEAL-V1: classify a raw root DONE hint against the ledger.

    A. authoritative COMPLETION_COMMITTED and unconsumed -> genuine unfinished
       completion: resume fails closed.
    B. identity already CONSUMED/SEALED (recognizable replay) -> known stale
       replay: quarantine the raw artifacts, audit, and do not block the resume.
    C. no authoritative record for the raw identity -> UNKNOWN_RAW_COMPLETION:
       fail closed.
    D. a record exists but the raw identity/brief hash does not bind to it
       (late republish with rewritten content) -> fail closed.
    """
    completion = _load_completion_helper()
    report = completion.classify_raw_completion(
        m.ROOT,
        active_project_id=m._active_project_id(),
        done_path=m.ZCODE_DONE,
        brief_path=m.SUPERVISOR_BRIEF,
    )
    classification = report["classification"]
    if classification == completion.CLASS_NO_RAW_SIGNAL:
        return
    raw_msg = (report.get("raw_identity") or {}).get("MESSAGE_ID")

    if classification == completion.CLASS_COMMITTED_UNCONSUMED:
        raise ResumeError(
            EXIT_CONFLICT,
            "genuine unfinished completion: authoritative COMPLETION_COMMITTED record "
            f"{(report.get('entry') or {}).get('COMMIT_ID')} for MESSAGE_ID {raw_msg} "
            "is not consumed yet",
        )
    if classification == completion.CLASS_SEALED_REPLAY:
        if report["brief_diverges"]:
            raise ResumeError(
                EXIT_CONFLICT,
                "late republish rejected: root SUPERVISOR_BRIEF.md hash diverges from the "
                "authoritative committed receipt of a consumed/sealed identity; "
                "manual review required",
            )
        completion.quarantine_raw_completion_artifacts(
            m.ROOT,
            done_path=m.ZCODE_DONE,
            brief_path=m.SUPERVISOR_BRIEF,
            label="HUMAN_REVIEW_SEALED_REPLAY",
            message_id=raw_msg,
        )
        _repair_rewound_processed_pointer(m, runtime, completion)
        return
    if classification == completion.CLASS_BINDING_MISMATCH and report["brief_diverges"]:
        raise ResumeError(
            EXIT_CONFLICT,
            "late republish rejected: root SUPERVISOR_BRIEF.md hash diverges from the "
            "authoritative committed receipt; manual review required",
        )
    raise ResumeError(
        EXIT_CONFLICT,
        f"unconsumed ZCODE_DONE.flag exists and is not a recoverable replay: "
        f"{classification} ({report['detail']})",
    )


def _repair_rewound_processed_pointer(m, runtime: dict, completion) -> None:
    """Rebuild the derived processed pointer after a quarantined sealed replay.

    A late replay may rewind ZCODE_LAST_PROCESSED.txt. The pointer is a derived
    compatibility artifact: quarantine the rewound copy and regenerate it from the
    authoritative consumed authorization so the remaining quiescence checks run
    against Runtime-generated state only.
    """
    dispatched = runtime.get("last_dispatched_message_id")
    consumed = runtime.get("last_consumed_message_id")
    authorization = runtime.get("authorized_dispatch")
    if not (
        isinstance(dispatched, int)
        and dispatched == consumed
        and isinstance(authorization, dict)
        and m.ZCODE_LAST_PROCESSED.is_file()
    ):
        return
    try:
        pointer = _load_claim_helper().parse_last_processed_identity(
            m.ZCODE_LAST_PROCESSED.read_text(encoding="utf-8-sig", errors="replace")
        )
    except ValueError:
        pointer = None
    if isinstance(pointer, dict) and pointer.get("MESSAGE_ID") == consumed:
        return
    rewind_dir = m.ROOT / "handoff" / "quarantine" / (
        f"completion-rewound-pointer-{consumed}-{uuid.uuid4().hex[:8]}"
    )
    rewind_dir.mkdir(parents=True, exist_ok=True)
    m.os.replace(m.ZCODE_LAST_PROCESSED, rewind_dir / m.ZCODE_LAST_PROCESSED.name)
    m.atomic_write(
        m.ZCODE_LAST_PROCESSED,
        completion.last_processed_text({
            key: authorization.get(key) for key in completion.IDENTITY_KEYS
        }),
    )
    completion.append_audit(m.ROOT, {
        "at": completion.now_iso(),
        "event": "COMPLETION_REWOUND_POINTER_REPAIRED",
        "actor": "human_review_resume",
        "MESSAGE_ID": consumed,
        "quarantine_dir": str(rewind_dir),
    })


def _read_last_processed(path: Path) -> dict:
    """Parse ZCODE_LAST_PROCESSED.txt via the Runtime canonical parser.

    Returns the identity dict: {"MESSAGE_ID": int} for the legacy numeric
    pointer, or the full stable identity for the v2 key=value wire format.
    """
    if not path.is_file():
        raise ResumeError(EXIT_CONFLICT, "ZCODE_LAST_PROCESSED.txt is missing")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    try:
        return _load_claim_helper().parse_last_processed_identity(text)
    except ValueError as exc:
        raise ResumeError(EXIT_CONFLICT, f"ZCODE_LAST_PROCESSED.txt is malformed: {exc}") from exc


def _validate_consumed_archive(m, runtime: dict, message_id: int) -> None:
    brief_hash = runtime.get("last_consumed_brief_sha256")
    if not isinstance(brief_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", brief_hash):
        raise ResumeError(EXIT_CONFLICT, "last consumed Executor receipt hash is missing or invalid")
    pattern = f"brief-{message_id}-*-consumed-{brief_hash[:12]}.md"
    matches = list(m.HANDOFF_ARCHIVE.glob(pattern))
    valid = [path for path in matches if m.sha256(path) == brief_hash]
    if len(valid) != 1:
        raise ResumeError(
            EXIT_CONFLICT,
            "last Executor task is not backed by exactly one hash-matching consumed archive",
        )


def _validate_no_incomplete_claims(m, last_consumed: int) -> None:
    claims_root = m.ROOT / "handoff" / "executor_claims"
    if not claims_root.exists():
        return
    for path in claims_root.iterdir():
        if not path.is_dir():
            continue
        match = re.match(r"^(\d+)-", path.name)
        if not match:
            raise ResumeError(EXIT_CONFLICT, f"malformed Executor claim directory: {path.name}")
        message_id = int(match.group(1))
        if message_id > last_consumed:
            raise ResumeError(
                EXIT_CONFLICT,
                f"unfinished Executor attempt detected: claim MESSAGE_ID {message_id} > consumed {last_consumed}",
            )


def _validate_last_claim(m, runtime: dict, message_id: int) -> None:
    required_from = runtime.get("claim_protocol_required_from_message_id")
    if isinstance(required_from, bool) or not isinstance(required_from, int) or message_id < required_from:
        return
    matches = list((m.ROOT / "handoff" / "executor_claims").glob(f"{message_id}-*.claim"))
    valid = []
    for path in matches:
        claim = m.read_json(path / "claim.json")
        if (
            isinstance(claim, dict)
            and claim.get("MESSAGE_ID") == message_id
            and claim.get("NONCE") == runtime.get("last_consumed_nonce")
        ):
            valid.append(path)
    if len(valid) != 1:
        raise ResumeError(
            EXIT_CONFLICT,
            "last Executor task is not backed by exactly one matching at-most-once claim",
        )


def _assert_pre_dispatch_quiescent(m, runtime: dict) -> bool:
    """Mechanical proof that this Runtime never published an Executor task.

    HUMAN_REVIEW quiescence has two legal shapes: post-task (the previous task
    was dispatched AND fully consumed/sealed) and pre-dispatch (no task was ever
    published, so the bootstrap compatibility consumed pointer is the only
    Executor history). This proof establishes the pre-dispatch shape from every
    observable surface; any counter-evidence returns False so the caller falls
    through to the strict post-task checks and fails closed. Hard corruption
    (e.g. a malformed processed pointer) still raises.
    """
    if runtime.get("last_dispatched_nonce") is not None:
        return False
    if runtime.get("authorized_dispatch") is not None:
        return False
    # Any retired identity proves a MESSAGE_ID was once issued to an Executor.
    if runtime.get("retired_message_ids"):
        return False
    # No live candidate inbox and no raw completion hint.
    if m.TO_ZCODE.exists() or m.ZCODE_DONE.exists():
        return False
    # No authorized current task in the authoritative project state.
    if m.read_project_state().get("current_task") is not None:
        return False
    # No active Executor claim.
    claims_root = m.ROOT / "handoff" / "executor_claims"
    if claims_root.exists() and any(path.is_dir() for path in claims_root.iterdir()):
        return False
    # No staged or unconsumed completion ledger entry.
    completion = _load_completion_helper()
    ledger = completion.ledger_dir(m.ROOT)
    if ledger.is_dir():
        staged = ledger / "staged"
        if staged.is_dir() and any(staged.iterdir()):
            return False
        for path in ledger.glob("completion-*.json"):
            entry = completion.load_entry_file(path)
            if entry is None:
                continue
            if entry.get("STATUS") not in (completion.STATUS_CONSUMED,
                                           completion.STATUS_SEALED):
                return False
    # A derived processed pointer is tolerated pre-dispatch only when it agrees
    # with the compatibility consumed pointer; absence is fine (nothing was ever
    # published, so nothing had to be processed).
    if m.ZCODE_LAST_PROCESSED.is_file():
        if _read_last_processed(m.ZCODE_LAST_PROCESSED).get("MESSAGE_ID") != runtime.get(
                "last_consumed_message_id"):
            return False
    return True


def _retirement_authority_reasons() -> set:
    """Retirement reasons that can close a dispatch without Executor work.

    SUPERSEDED is deliberately absent: a superseded dispatch had a successor
    published after it, so `last_dispatched_message_id` pointing at it would
    mean a rewound or contradictory Runtime pointer, never a legal shape.
    """
    return {
        "PAUSE_BEFORE_CLAIM",
        "PAUSE_INTERRUPT",
        "HUMAN_INTERVENTION_BEFORE_CLAIM",
        "HUMAN_INTERVENTION_INTERRUPT",
        "EXECUTOR_TIMEOUT",
    }


def _retirement_records_for(runtime: dict, message_id: int, nonce: str) -> list:
    """Identity-bound retirement records for one dispatched identity."""
    return [
        entry for entry in (runtime.get("executor_retirements") or [])
        if isinstance(entry, dict)
        and entry.get("MESSAGE_ID") == message_id
        and entry.get("NONCE") == nonce
    ]


def _verify_retired_authorization_chain(m, runtime: dict, dispatched: int,
                                        dispatched_nonce: str) -> bool:
    """Shared retired-dispatch proof: authorization integrity and closed window.

    Returns True only when the authorized_dispatch is identity-bound to the
    retired identity, its archived dispatch documents verify byte-exactly
    against the authorized inbox hash, and the authorization window is provably
    closed: expired in the authoritative record, or the retirement provably
    removed the live inbox into quarantine with the exact dispatch hash.
    """
    authorization = runtime.get("authorized_dispatch")
    if not isinstance(authorization, dict):
        return False
    if authorization.get("MESSAGE_ID") != dispatched \
            or authorization.get("NONCE") != dispatched_nonce:
        return False
    expected_inbox_hash = authorization.get("TO_ZCODE_SHA256")
    if not isinstance(expected_inbox_hash, str) \
            or not re.fullmatch(r"[0-9a-f]{64}", expected_inbox_hash):
        return False
    archive_meta = authorization.get("SUPERVISOR_DISPATCH_ARCHIVE")
    if not isinstance(archive_meta, dict) \
            or archive_meta.get("dispatch_sha256") != expected_inbox_hash:
        return False
    archive_documents = {}
    for key in ("metadata_file", "archive_file", "authorization_file"):
        relative = archive_meta.get(key)
        if not isinstance(relative, str) or not relative:
            return False
        path = m.ROOT / relative
        if not path.is_file():
            return False
        archive_documents[key] = path
    try:
        authorization_archive = json.loads(
            archive_documents["authorization_file"].read_text(encoding="utf-8-sig"))
        metadata_archive = json.loads(
            archive_documents["metadata_file"].read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError):
        return False
    if not isinstance(authorization_archive, dict) or not isinstance(metadata_archive, dict):
        return False
    # The archived documents bind the identity, and the archived dispatch
    # brief itself must be the exact retired inbox bytes (dispatch_sha256 ==
    # TO_ZCODE_SHA256 == sha256(archive_file)).
    if authorization_archive.get("MESSAGE_ID") != dispatched \
            or authorization_archive.get("NONCE") != dispatched_nonce:
        return False
    if metadata_archive.get("MESSAGE_ID") != dispatched:
        return False
    for document in (authorization_archive, metadata_archive):
        archived_hash = document.get("dispatch_sha256")
        if archived_hash is None:
            archived_hash = document.get("TO_ZCODE_SHA256")
        if archived_hash != expected_inbox_hash:
            return False
    try:
        if m.sha256(archive_documents["archive_file"]) != expected_inbox_hash:
            return False
    except OSError:
        return False

    authorization_expired = False
    expires_at = authorization.get("EXPIRES_AT")
    if isinstance(expires_at, str):
        try:
            deadline = m.parse_time(expires_at)
        except (ValueError, TypeError):
            deadline = None
        authorization_expired = deadline is not None and m.utc_now() >= deadline
    quarantine_hash = None
    quarantine_root = m.ROOT / "handoff" / "quarantine"
    if quarantine_root.is_dir():
        quarantine_hashes = set()
        for path in quarantine_root.glob(f"to-zcode-{dispatched}-*"):
            try:
                quarantine_hashes.add(m.sha256(path))
            except OSError:
                return False
        if len(quarantine_hashes) > 1 or \
                quarantine_hashes - {expected_inbox_hash}:
            return False
        if quarantine_hashes:
            quarantine_hash = expected_inbox_hash
    if quarantine_hash is None and not authorization_expired:
        return False
    return True


def _assert_retired_unclaimed_quiescent(m, runtime: dict) -> bool:
    """Mechanical proof that the last dispatch was retired with no live work.

    HUMAN_REVIEW quiescence has three legal shapes: post-task (the previous
    task was dispatched AND fully consumed/sealed), pre-dispatch (no task was
    ever published), and retired-unclaimed (the last dispatch was authoritatively
    retired — pause/intervention/timeout — before any claimable Executor work,
    so it can never be consumed). This proof establishes the third shape from
    every observable surface: the dispatched identity must carry a full
    identity-bound retirement record, its authorization must be verifiable
    against its dispatch archive with a closed authorization window, no claim,
    attempt workspace, completion staging, ledger entry, or raw completion hint
    may exist for it or for any identity beyond the consumed chain, and the
    consumed chain below it must still verify. Any counter-evidence returns
    False so the caller falls through to the strict post-task checks and fails
    closed; a bare `retired_message_ids` entry or a `dispatched != consumed`
    inequality alone is never sufficient.
    """
    dispatched = runtime.get("last_dispatched_message_id")
    consumed = runtime.get("last_consumed_message_id")
    if isinstance(dispatched, bool) or not isinstance(dispatched, int):
        return False
    if isinstance(consumed, bool) or not isinstance(consumed, int):
        return False
    if dispatched <= consumed:
        return False
    dispatched_nonce = runtime.get("last_dispatched_nonce")
    consumed_nonce = runtime.get("last_consumed_nonce")
    if not isinstance(dispatched_nonce, str) or not dispatched_nonce:
        return False
    if not isinstance(consumed_nonce, str) or not consumed_nonce:
        return False
    try:
        state = m.read_project_state()
    except Exception:
        return False
    if state.get("status") != "HUMAN_REVIEW" or state.get("current_task") is not None:
        return False

    # 1. The dispatched identity must be authoritatively retired with exactly
    #    one full identity-bound retirement record.
    retired = runtime.get("retired_message_ids")
    if not isinstance(retired, list) or dispatched not in retired:
        return False
    retirements = _retirement_records_for(runtime, dispatched, dispatched_nonce)
    if len(retirements) != 1:
        return False
    if retirements[0].get("REASON") not in _retirement_authority_reasons():
        return False

    # 2-3. The authorization must be intact, identity-bound, verifiable
    #    against its archived dispatch documents, and its window provably
    #    closed (shared with the claimed-timeout reconciliation proof).
    if not _verify_retired_authorization_chain(m, runtime, dispatched, dispatched_nonce):
        return False

    # 4. No live inbox and no raw completion hint.
    if m.TO_ZCODE.exists() or m.ZCODE_DONE.exists():
        return False

    # 5. No Executor claim at or beyond the consumed chain — the retired
    #    dispatch itself included (a claim would mean claimed-but-unconsumed).
    claims_root = m.ROOT / "handoff" / "executor_claims"
    if claims_root.exists():
        for path in claims_root.iterdir():
            if not path.is_dir():
                continue
            match = re.match(r"^(\d+)-", path.name)
            if not match or int(match.group(1)) > consumed:
                return False

    # 6. No attempt workspace at or beyond the consumed chain.
    try:
        active = m.read_json(m.ACTIVE_PROJECT_FILE)
        project_root = m.ROOT / str(active.get("project_root") or "")
    except Exception:
        return False
    if not project_root.is_dir():
        return False
    workspaces = project_root / "attempt_workspaces"
    if workspaces.is_dir():
        for path in workspaces.iterdir():
            match = re.match(r"^(\d+)-", path.name) if path.is_dir() else None
            if match is None or int(match.group(1)) > consumed:
                return False

    # 7. No completion staging bound to the retired dispatch.
    staging_root = project_root / "completion_staging"
    if staging_root.is_dir():
        for path in staging_root.iterdir():
            if path.name.startswith(f"{dispatched}-"):
                return False
            staging_document = m.read_json(path / "staging.json", default=None) \
                if (path / "staging.json").is_file() else None
            if isinstance(staging_document, dict) \
                    and staging_document.get("MESSAGE_ID") == dispatched:
                return False

    # 8. No completion ledger entry, staged result, finish, or publication for
    #    the retired dispatch; nothing unconsumed or staged anywhere.
    completion = _load_completion_helper()
    ledger = completion.ledger_dir(m.ROOT)
    if ledger.is_dir():
        staged = ledger / "staged"
        if staged.is_dir():
            for path in staged.iterdir():
                match = re.match(r"^completion-(\d+)-", path.name)
                if not match:
                    return False
                staged_id = int(match.group(1))
                if staged_id == dispatched:
                    return False
                # A staged copy of an identity the authoritative ledger already
                # sealed or consumed is a historical archive, not live work.
                sealed = [
                    entry for entry in completion.lookup_entries(m.ROOT, staged_id)
                    if entry.get("STATUS") in (completion.STATUS_CONSUMED,
                                               completion.STATUS_SEALED)
                ]
                if not sealed:
                    return False
        for path in ledger.glob("completion-*.json"):
            entry = completion.load_entry_file(path)
            if entry is None:
                continue
            if entry.get("MESSAGE_ID") == dispatched:
                return False
            if entry.get("STATUS") not in (completion.STATUS_CONSUMED,
                                           completion.STATUS_SEALED):
                return False
    for name in ("executor_finishes", "executor_publications"):
        directory = m.ROOT / "handoff" / name
        if directory.is_dir() and any(directory.glob(f"completion-{dispatched}-*")):
            return False

    # 9. The consumed chain below the retired dispatch must still verify.
    try:
        last_processed = _read_last_processed(m.ZCODE_LAST_PROCESSED)
        if last_processed.get("MESSAGE_ID") != consumed:
            return False
        if "NONCE" in last_processed and last_processed.get("NONCE") != consumed_nonce:
            return False
        _validate_consumed_archive(m, runtime, consumed)
        _validate_last_claim(m, runtime, consumed)
        _validate_no_incomplete_claims(m, consumed)
    except ResumeError:
        return False
    return True


CLAIMED_TIMEOUT_RECONCILIATION_SCHEMA_VERSION = 1


def _claimed_timeout_reconciliations(runtime: dict) -> list:
    records = runtime.get("claimed_timeout_reconciliations")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def _assert_claimed_timeout_reconciled(m, runtime: dict, *,
                                       require_record: bool) -> bool:
    """CLAIMED-TIMEOUT-RECONCILIATION-V1: mechanical proof that the last
    dispatched identity is a Runtime-retired claimed timeout with zero
    committed completion, reconciled into a quiescent historical state.

    HUMAN_REVIEW quiescence has a fourth legal shape beyond post-task,
    pre-dispatch, and retired-unclaimed: the last dispatch was claimed by an
    Executor, its claimed execution budget expired, and the Runtime retired it
    (EXECUTOR_TIMEOUT) with no completion ever committed. That identity can
    never be consumed, so the strict `dispatched == consumed` chain can never
    close; instead the Runtime owns an explicit reconciliation record after
    proving, from authoritative state only, that the attempt's execution
    ownership is dead. With `require_record` the proof also re-verifies the
    record and its evidence bindings (claim hash, workspace presence, reason,
    retirement identity) fresh from disk, so Apply accepts the shape only when
    Runtime-owned evidence proves the reconciliation. Any counter-evidence —
    an unretired or ambiguous claim, a live or staged completion, an
    unconsumed ledger entry anywhere, an open authorization window, a
    tampered claim — returns False so the caller fails closed; nothing here
    ages out, rewrites history, or derives from filenames or model judgment.
    """
    dispatched = runtime.get("last_dispatched_message_id")
    consumed = runtime.get("last_consumed_message_id")
    if isinstance(dispatched, bool) or not isinstance(dispatched, int):
        return False
    if isinstance(consumed, bool) or not isinstance(consumed, int):
        return False
    if dispatched <= consumed:
        return False
    dispatched_nonce = runtime.get("last_dispatched_nonce")
    if not isinstance(dispatched_nonce, str) or not dispatched_nonce:
        return False
    try:
        state = m.read_project_state()
    except Exception:
        return False
    if state.get("status") != "HUMAN_REVIEW" or state.get("current_task") is not None:
        return False

    # 1. Authoritative identity-bound retirement of the claimed attempt, and
    #    the Runtime-armed claimed execution timeout for exactly this nonce:
    #    the claimed-timeout signature, not an unrelated pause or supersession.
    retired = runtime.get("retired_message_ids")
    if not isinstance(retired, list) or dispatched not in retired:
        return False
    retirements = _retirement_records_for(runtime, dispatched, dispatched_nonce)
    if len(retirements) != 1:
        return False
    retirement = retirements[0]
    if retirement.get("REASON") not in _retirement_authority_reasons():
        return False
    if runtime.get("timeout_notified_for_nonce") != dispatched_nonce:
        return False

    # 2-3. Authorization integrity and a provably closed window (shared proof).
    if not _verify_retired_authorization_chain(m, runtime, dispatched, dispatched_nonce):
        return False

    # 4. No raw completion hint anywhere. The historical dispatch inbox may
    #    remain (a claimed timeout is fenced by the retirement record, the
    #    armed timeout nonce, and the closed window — not by removing the
    #    document), but a live inbox must still be the exact retired bytes.
    if m.ZCODE_DONE.exists():
        return False
    authorization = runtime.get("authorized_dispatch")
    if m.TO_ZCODE.exists():
        try:
            if m.sha256(m.TO_ZCODE) != authorization.get("TO_ZCODE_SHA256"):
                return False
        except OSError:
            return False

    # 5. Zero committed completion for the retired identity: completion and
    #    consumption semantics stay authoritative (class D refuses here).
    completion = _load_completion_helper()
    if completion.lookup_entries(m.ROOT, dispatched):
        return False

    try:
        active = m.read_json(m.ACTIVE_PROJECT_FILE)
        project_root = m.ROOT / str(active.get("project_root") or "")
    except Exception:
        return False
    if not project_root.is_dir():
        return False

    # 6. No completion staging bound to the retired dispatch.
    staging_root = project_root / "completion_staging"
    if staging_root.is_dir():
        for path in staging_root.iterdir():
            if path.name.startswith(f"{dispatched}-"):
                return False
            staging_document = m.read_json(path / "staging.json", default=None) \
                if (path / "staging.json").is_file() else None
            if isinstance(staging_document, dict) \
                    and staging_document.get("MESSAGE_ID") == dispatched:
                return False

    # 7. No finish or publication artifacts for the retired dispatch.
    for name in ("executor_finishes", "executor_publications"):
        directory = m.ROOT / "handoff" / name
        if directory.is_dir() and any(directory.glob(f"completion-{dispatched}-*")):
            return False

    # 8. The completion ledger holds no unconsumed work anywhere.
    ledger = completion.ledger_dir(m.ROOT)
    if ledger.is_dir():
        staged = ledger / "staged"
        if staged.is_dir():
            for path in staged.iterdir():
                match = re.match(r"^completion-(\d+)-", path.name)
                if not match:
                    return False
                staged_id = int(match.group(1))
                if staged_id == dispatched:
                    return False
                sealed = [
                    entry for entry in completion.lookup_entries(m.ROOT, staged_id)
                    if entry.get("STATUS") in (completion.STATUS_CONSUMED,
                                               completion.STATUS_SEALED)
                ]
                if not sealed:
                    return False
        for path in ledger.glob("completion-*.json"):
            entry = completion.load_entry_file(path)
            if entry is None:
                continue
            if entry.get("STATUS") not in (completion.STATUS_CONSUMED,
                                           completion.STATUS_SEALED):
                return False

    # 9. Exactly one claim beyond the consumed chain: the identity-bound claim
    #    of the reconciled attempt itself, at the canonical claim layout, with
    #    a claim record that binds the retired identity. It is preserved
    #    provenance, never re-armed authority.
    claim_helper = _load_claim_helper()
    expected_claim_dir = claim_helper.claim_dir(m.ROOT, dispatched, dispatched_nonce)
    dispatched_claim_dirs = []
    claims_root = m.ROOT / "handoff" / "executor_claims"
    if claims_root.exists():
        for path in claims_root.iterdir():
            if not path.is_dir():
                continue
            match = re.match(r"^(\d+)-", path.name)
            if not match:
                return False
            message_id = int(match.group(1))
            if message_id <= consumed:
                continue
            if message_id != dispatched:
                return False
            dispatched_claim_dirs.append(path)
    if len(dispatched_claim_dirs) != 1 \
            or dispatched_claim_dirs[0] != expected_claim_dir:
        return False
    claim_document = m.read_json(expected_claim_dir / "claim.json", default=None)
    if not isinstance(claim_document, dict) \
            or claim_document.get("MESSAGE_ID") != dispatched \
            or claim_document.get("NONCE") != dispatched_nonce:
        return False

    # 10. Attempt workspaces beyond the consumed chain: at most the retired
    #     attempt's own preserved workspace.
    workspaces = project_root / "attempt_workspaces"
    workspace_path = workspaces / expected_claim_dir.stem
    if workspaces.is_dir():
        for path in workspaces.iterdir():
            match = re.match(r"^(\d+)-", path.name) if path.is_dir() else None
            if match is None or int(match.group(1)) > consumed:
                if path != workspace_path:
                    return False

    # 11. Consumed-chain consistency below the reconciled identity, verified
    #     as far as the authoritative record reaches. A legacy migrated chain
    #     carries no nonce/brief binding; those bindings are checked whenever
    #     the Runtime state actually carries them.
    try:
        if m.ZCODE_LAST_PROCESSED.is_file():
            last_processed = _read_last_processed(m.ZCODE_LAST_PROCESSED)
            if last_processed.get("MESSAGE_ID") != consumed:
                return False
            if "NONCE" in last_processed \
                    and runtime.get("last_consumed_nonce") is not None \
                    and last_processed.get("NONCE") != runtime.get("last_consumed_nonce"):
                return False
        _validate_last_claim(m, runtime, consumed)
        brief_hash = runtime.get("last_consumed_brief_sha256")
        if isinstance(brief_hash, str) and re.fullmatch(r"[0-9a-f]{64}", brief_hash):
            _validate_consumed_archive(m, runtime, consumed)
    except ResumeError:
        return False

    # 12. The Runtime-owned reconciliation record: exactly one, schema-bound,
    #     with its evidence bindings still true on disk right now.
    if not require_record:
        return True
    records = _claimed_timeout_reconciliations(runtime)
    matching = [
        record for record in records
        if record.get("MESSAGE_ID") == dispatched
        and record.get("NONCE") == dispatched_nonce
    ]
    if len(matching) != 1:
        return False
    record = matching[0]
    if record.get("schema_version") != CLAIMED_TIMEOUT_RECONCILIATION_SCHEMA_VERSION:
        return False
    if record.get("REASON") != retirement.get("REASON") \
            or record.get("RETIRED_AT") != retirement.get("RETIRED_AT"):
        return False
    if record.get("CLAIM_DIR") != expected_claim_dir.relative_to(m.ROOT).as_posix():
        return False
    try:
        claim_bytes = (expected_claim_dir / "claim.json").read_bytes()
    except OSError:
        return False
    if record.get("CLAIM_JSON_SHA256") != hashlib.sha256(claim_bytes).hexdigest():
        return False
    expected_workspace = (
        f"attempt_workspaces/{workspace_path.name}" if workspace_path.is_dir() else None
    )
    if record.get("ATTEMPT_WORKSPACE") != expected_workspace:
        return False
    if not isinstance(record.get("RECONCILED_AT"), str) or not record["RECONCILED_AT"]:
        return False
    return True


def reconcile_claimed_timeout(m, *, project_id: str | None = None) -> dict:
    """CLAIMED-TIMEOUT-RECONCILIATION-V1: reconcile a retired claimed timeout
    with zero committed completion into a quiescent, reviewable historical
    state so the prepared Human Decision becomes legally applicable.

    This control-plane transaction is read-only except for two Runtime-owned
    writes: the append-only `claimed_timeout_reconciliations` record in the
    authoritative orchestrator runtime state, and the completion audit event.
    It never touches project_state.json, the HUMAN_REVIEW flag, the dispatch
    inbox, the claim directory, or the attempt workspace, so a prepared
    receipt's exact-state binding stays intact. Reconciliation removes
    obsolete execution ownership; it does not rewrite history, fabricate a
    completion, or consume anything.
    """
    _acquire_resume_lock(m)
    try:
        active, state = _activate_isolated_project(m, project_id)
        if state.get("status") != "HUMAN_REVIEW":
            raise ResumeError(
                EXIT_CONFLICT,
                "project status must be HUMAN_REVIEW for claimed-timeout "
                f"reconciliation, got {state.get('status')!r}",
            )
        if state.get("current_task") is not None:
            raise ResumeError(
                EXIT_CONFLICT,
                "current_task must be exactly null for claimed-timeout reconciliation",
            )
        if m.STOP_FLAG.exists():
            raise ResumeError(
                EXIT_CONFLICT,
                "control/STOP exists; claimed-timeout reconciliation is forbidden",
            )
        runtime = read_json_strict(m.RUNTIME_STATE, "orchestrator runtime")
        if runtime.get("status") != "HUMAN_REVIEW":
            raise ResumeError(
                EXIT_CONFLICT,
                "orchestrator runtime status must be HUMAN_REVIEW, got "
                f"{runtime.get('status')!r}",
            )
        if not _assert_claimed_timeout_reconciled(m, runtime, require_record=False):
            raise ResumeError(
                EXIT_CONFLICT,
                "claimed timeout is not mechanically reconcilable from "
                "authoritative Runtime state; refusing fail-closed",
            )
        dispatched = runtime["last_dispatched_message_id"]
        dispatched_nonce = runtime["last_dispatched_nonce"]
        existing = [
            record for record in _claimed_timeout_reconciliations(runtime)
            if record.get("MESSAGE_ID") == dispatched
            and record.get("NONCE") == dispatched_nonce
        ]
        if existing:
            return {
                "event": "CLAIMED_TIMEOUT_ALREADY_RECONCILED",
                "project_id": active["project_id"],
                "message_id": dispatched,
                "reconciliation_id": existing[0].get("RECONCILIATION_ID"),
                "duplicate": True,
            }
        retirement = _retirement_records_for(runtime, dispatched, dispatched_nonce)[0]
        claim_helper = _load_claim_helper()
        claim_dir_path = claim_helper.claim_dir(m.ROOT, dispatched, dispatched_nonce)
        claim_bytes = (claim_dir_path / "claim.json").read_bytes()
        project_root = active["project_root"]
        workspace_path = (
            project_root / "attempt_workspaces" / claim_dir_path.stem
        )
        record = {
            "schema_version": CLAIMED_TIMEOUT_RECONCILIATION_SCHEMA_VERSION,
            "RECONCILIATION_ID": (
                f"claimed-timeout-reconcile-{dispatched}-"
                f"{hashlib.sha256(dispatched_nonce.encode('utf-8')).hexdigest()[:12]}"
            ),
            "MESSAGE_ID": dispatched,
            "TASK_ID": retirement.get("TASK_ID"),
            "STAGE_ID": retirement.get("STAGE_ID"),
            "ATTEMPT": retirement.get("ATTEMPT"),
            "NONCE": dispatched_nonce,
            "REASON": retirement.get("REASON"),
            "RETIRED_AT": retirement.get("RETIRED_AT"),
            "CLAIM_DIR": claim_dir_path.relative_to(m.ROOT).as_posix(),
            "CLAIM_JSON_SHA256": hashlib.sha256(claim_bytes).hexdigest(),
            "CLAIM_TOKEN_SHA256": claim_document_token(claim_dir_path),
            "ATTEMPT_WORKSPACE": (
                workspace_path.relative_to(project_root).as_posix()
                if workspace_path.is_dir() else None
            ),
            "COMMITTED_COMPLETIONS": 0,
            "PROJECT_ID": active["project_id"],
            "RECONCILED_AT": m.stamp(),
        }
        runtime.setdefault("claimed_timeout_reconciliations", []).append(record)
        m.save_runtime(runtime)
        completion = _load_completion_helper()
        completion.append_audit(m.ROOT, {
            "at": completion.now_iso(),
            "event": "CLAIMED_TIMEOUT_RECONCILED",
            "actor": "human_review_resume",
            "MESSAGE_ID": dispatched,
            "NONCE": dispatched_nonce,
            "REASON": retirement.get("REASON"),
            "CLAIM_DIR": record["CLAIM_DIR"],
            "RECONCILIATION_ID": record["RECONCILIATION_ID"],
        })
        reread = read_json_strict(m.RUNTIME_STATE, "orchestrator runtime")
        if not _assert_claimed_timeout_reconciled(m, reread, require_record=True):
            raise ResumeError(
                EXIT_INTERNAL,
                "reconciliation read-back did not commit a verifiable record",
            )
        return {
            "event": "CLAIMED_TIMEOUT_RECONCILED",
            "project_id": active["project_id"],
            "message_id": dispatched,
            "reconciliation_id": record["RECONCILIATION_ID"],
            "claim_dir": record["CLAIM_DIR"],
            "duplicate": False,
        }
    finally:
        m.release_lock()


def claim_document_token(claim_dir_path: Path) -> str | None:
    """The preserved claim token binding, copied for provenance only."""
    document = read_json_strict(claim_dir_path / "claim.json", "executor claim record") \
        if (claim_dir_path / "claim.json").is_file() else None
    if isinstance(document, dict):
        token = document.get("CLAIM_TOKEN_SHA256")
        if isinstance(token, str):
            return token
    return None


def validate_runtime_quiescent(m, runtime: dict) -> None:
    if runtime.get("status") != "HUMAN_REVIEW":
        raise ResumeError(
            EXIT_CONFLICT,
            f"orchestrator runtime status must be HUMAN_REVIEW, got {runtime.get('status')!r}",
        )
    if m.STOP_FLAG.exists():
        raise ResumeError(EXIT_CONFLICT, "control/STOP exists; HUMAN_REVIEW resume is forbidden")
    _handle_raw_done_signal(m, runtime)

    dispatched = runtime.get("last_dispatched_message_id")
    consumed = runtime.get("last_consumed_message_id")
    if dispatched is None and _assert_pre_dispatch_quiescent(m, runtime):
        # HUMAN_REVIEW that predates the first dispatch: the never-dispatched
        # proof above is complete, so there is no in-flight Executor work and
        # the bootstrap compatibility consumed pointer needs no task chain.
        return
    if _assert_retired_unclaimed_quiescent(m, runtime):
        # HUMAN_REVIEW whose last dispatch was retired before any claimable
        # Executor work (pause/intervention/timeout): the proof above is
        # complete, so the retired dispatch needs no consumed chain of its own.
        return
    if _assert_claimed_timeout_reconciled(m, runtime, require_record=True):
        # CLAIMED-TIMEOUT-RECONCILIATION-V1: HUMAN_REVIEW whose last dispatch
        # was claimed, timed out, and was retired with zero committed
        # completion, and whose execution ownership the Runtime has provably
        # reconciled into a quiescent historical record. The preserved claim
        # and attempt workspace are provenance only; a late commit stays
        # fenced by the retirement/timeout bindings in the completion path.
        return
    if (
        isinstance(dispatched, bool)
        or not isinstance(dispatched, int)
        or isinstance(consumed, bool)
        or not isinstance(consumed, int)
        or dispatched != consumed
    ):
        raise ResumeError(
            EXIT_CONFLICT,
            f"previous Executor task is not fully consumed: dispatched={dispatched!r}, consumed={consumed!r}",
        )
    if runtime.get("last_dispatched_nonce") != runtime.get("last_consumed_nonce"):
        raise ResumeError(EXIT_CONFLICT, "previous Executor nonce is not fully consumed")
    last_processed = _read_last_processed(m.ZCODE_LAST_PROCESSED)
    if last_processed.get("MESSAGE_ID") != consumed:
        raise ResumeError(EXIT_CONFLICT, "ZCODE_LAST_PROCESSED does not match last consumed MESSAGE_ID")

    authorization = runtime.get("authorized_dispatch")
    if not isinstance(authorization, dict):
        raise ResumeError(EXIT_CONFLICT, "authorized_dispatch is missing for the last Executor task")
    if authorization.get("MESSAGE_ID") != dispatched:
        raise ResumeError(EXIT_CONFLICT, "authorized_dispatch does not match last dispatched MESSAGE_ID")
    if authorization.get("NONCE") != runtime.get("last_dispatched_nonce"):
        raise ResumeError(EXIT_CONFLICT, "authorized_dispatch does not match last dispatched NONCE")
    # A v2 key=value pointer carries the full stable identity: bind every field
    # to the authoritative consumed authorization (already verified above to
    # equal the dispatched and consumed MESSAGE_ID/NONCE). A legacy numeric
    # pointer carries no identity fields, so only MESSAGE_ID is comparable.
    if "TASK_ID" in last_processed:
        for key in ("TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE"):
            if last_processed.get(key) != authorization.get(key):
                raise ResumeError(
                    EXIT_CONFLICT,
                    "ZCODE_LAST_PROCESSED identity does not match the consumed "
                    f"authorization: {key}",
                )
    # F-005: when the last task is fully consumed and sealed, a MISSING live
    # inbox is a legitimate finalize artifact (for example, exhausting the
    # Supervisor decision budget finalizes HUMAN_REVIEW and removes the stale
    # inbox), not a tamper signal. The archived dispatch bytes remain the
    # authoritative binding and are proven by _validate_consumed_archive
    # below; only a present-but-mismatched inbox is still rejected.
    expected_inbox_hash = authorization.get("TO_ZCODE_SHA256")
    if (
        not isinstance(expected_inbox_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_inbox_hash)
        or (m.TO_ZCODE.is_file() and m.sha256(m.TO_ZCODE) != expected_inbox_hash)
    ):
        raise ResumeError(EXIT_CONFLICT, "last authorized TO_ZCODE.md is missing or hash-mismatched")
    _validate_consumed_archive(m, runtime, consumed)
    _validate_last_claim(m, runtime, consumed)
    _validate_no_incomplete_claims(m, consumed)


def _archive_review_flag(m, receipt: dict, archive_dir: Path) -> None:
    expected = receipt.get("previous_human_review_flag_sha256")
    actual = m.sha256(m.HUMAN_REVIEW_FLAG) if m.HUMAN_REVIEW_FLAG.exists() else None
    if actual != expected:
        raise ResumeError(EXIT_STALE_OR_DUPLICATE, "HUMAN_REVIEW flag changed after receipt preparation")
    if actual is None:
        return
    raw = m.HUMAN_REVIEW_FLAG.read_bytes()
    record = {
        "schema_version": 1,
        "source": "control/HUMAN_REVIEW",
        "source_sha256": actual,
        "content_base64": base64.b64encode(raw).decode("ascii"),
        "archived_by_receipt_id": receipt["receipt_id"],
    }
    m.atomic_create(
        archive_dir / f"{receipt['receipt_id']}-human-review-flag.json",
        json.dumps(record, ensure_ascii=False, indent=2) + "\n",
    )


def apply_receipt(m, *, receipt_path: Path) -> dict:
    receipt = read_json_strict(receipt_path, "human decision receipt")
    m.validate_human_decision_receipt(receipt)
    _acquire_resume_lock(m)
    try:
        active, _ = _activate_isolated_project(m)
        state_raw = m.PROJECT_STATE.read_bytes()
        state_hash = hashlib.sha256(state_raw).hexdigest()
        try:
            state = json.loads(state_raw.decode("utf-8-sig"))
        except Exception as exc:
            raise ResumeError(EXIT_CONFLICT, "project_state changed to invalid JSON during resume") from exc
        if not isinstance(state, dict) or state.get("project_id") != active["project_id"]:
            raise ResumeError(EXIT_CONFLICT, "active project and exact project_state snapshot mismatch")
        if receipt.get("project_id") != active["project_id"]:
            raise ResumeError(
                EXIT_CONFLICT,
                f"wrong project: receipt={receipt.get('project_id')!r}, active={active['project_id']!r}",
            )
        _require_review_snapshot(m, state)
        try:
            m.validate_human_decision_receipt(
                receipt,
                expected_project_id=active["project_id"],
                expected_previous_state_sha256=state_hash,
            )
        except RuntimeError as exc:
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, str(exc)) from exc

        runtime_raw = m.RUNTIME_STATE.read_bytes()
        runtime_hash = hashlib.sha256(runtime_raw).hexdigest()
        try:
            runtime = json.loads(runtime_raw.decode("utf-8-sig"))
        except Exception as exc:
            raise ResumeError(EXIT_CONFLICT, "orchestrator runtime is invalid JSON") from exc
        if not isinstance(runtime, dict):
            raise ResumeError(EXIT_CONFLICT, "orchestrator runtime must be a JSON object")
        validate_runtime_quiescent(m, runtime)
        inbox_hash = m.sha256(m.TO_ZCODE) if m.TO_ZCODE.exists() else None
        next_message_id = state.get("next_message_id")
        last_supervisor_decision = state.get("last_supervisor_decision")

        archive_dir = active["project_root"] / "human_decisions" / "archive"
        archive_name = f"{receipt['receipt_id']}-{receipt['receipt_sha256']}.json"
        archive_path = archive_dir / archive_name
        if archive_path.exists():
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, "duplicate resume receipt is already archived")
        m.atomic_create(
            archive_path,
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        )
        archived = read_json_strict(archive_path, "archived human decision receipt")
        m.validate_human_decision_receipt(
            archived,
            expected_project_id=active["project_id"],
            expected_previous_state_sha256=state_hash,
        )
        receipt_file_hash = m.sha256(archive_path)
        _archive_review_flag(m, receipt, archive_dir)

        # Recheck both authoritative states immediately before the lifecycle commit.
        if m.sha256(m.PROJECT_STATE) != state_hash:
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, "project_state changed during resume")
        if m.sha256(m.RUNTIME_STATE) != runtime_hash:
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, "orchestrator runtime changed during resume")
        current_flag_hash = m.sha256(m.HUMAN_REVIEW_FLAG) if m.HUMAN_REVIEW_FLAG.exists() else None
        if current_flag_hash != receipt.get("previous_human_review_flag_sha256"):
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, "HUMAN_REVIEW flag changed during resume")
        if inbox_hash != (m.sha256(m.TO_ZCODE) if m.TO_ZCODE.exists() else None):
            raise ResumeError(EXIT_STALE_OR_DUPLICATE, "TO_ZCODE.md changed during resume")

        # The receipt archive already preserves a matching flag before it is cleared.
        m.HUMAN_REVIEW_FLAG.unlink(missing_ok=True)
        resumed_at = m.stamp()
        new_state = copy.deepcopy(state)
        new_state["status"] = "SUPERVISOR_TURN"
        new_state["updated_at"] = resumed_at
        new_state["human_review_resume"] = {
            "schema_version": m.HUMAN_DECISION_RESUME_SCHEMA_VERSION,
            "status": "PENDING_SUPERVISOR_REVIEW",
            "transition": "HUMAN_REVIEW_TO_SUPERVISOR_TURN",
            "project_id": active["project_id"],
            "receipt_id": receipt["receipt_id"],
            "receipt_sha256": receipt["receipt_sha256"],
            "receipt_file_sha256": receipt_file_hash,
            "receipt_path": archive_path.relative_to(active["project_root"]).as_posix(),
            "previous_status": "HUMAN_REVIEW",
            "previous_project_state_sha256": state_hash,
            "resumed_at": resumed_at,
        }
        m.atomic_json(m.PROJECT_STATE, new_state)
        committed = m.read_project_state()
        if committed.get("status") != "SUPERVISOR_TURN":
            raise ResumeError(EXIT_INTERNAL, "project_state read-back did not commit SUPERVISOR_TURN")
        if committed.get("current_task") is not None:
            raise ResumeError(EXIT_INTERNAL, "resume unexpectedly changed current_task")
        if committed.get("next_message_id") != next_message_id:
            raise ResumeError(EXIT_INTERNAL, "resume unexpectedly changed next_message_id")
        if committed.get("last_supervisor_decision") != last_supervisor_decision:
            raise ResumeError(EXIT_INTERNAL, "resume unexpectedly changed last_supervisor_decision")
        m.load_verified_human_decision_for_supervisor(committed)

        new_runtime = copy.deepcopy(runtime)
        new_runtime["status"] = "SUPERVISOR_TURN"
        new_runtime["consecutive_codex_without_executor"] = 0
        new_runtime["last_human_decision_receipt_id"] = receipt["receipt_id"]
        new_runtime["last_human_decision_receipt_sha256"] = receipt["receipt_sha256"]
        new_runtime["last_human_review_resume_at"] = resumed_at
        # Re-arm the durable Supervisor event: the verified receipt is the
        # human-authorized successor of any stale or exhausted event, and the
        # scheduler services the owned durable event before any new invocation.
        resume_event = m.human_decision_resume_event(new_state)
        if resume_event is not None:
            new_runtime["pending_supervisor_event"] = {
                "reason": "HUMAN_DECISION_RESUME",
                "event": resume_event,
                "recorded_at": resumed_at,
                "decision_attempts": 0,
                "retry_exhausted": False,
                "rearmed_by_receipt": receipt["receipt_id"],
            }
        m.save_runtime(new_runtime)

        if inbox_hash != (m.sha256(m.TO_ZCODE) if m.TO_ZCODE.exists() else None):
            raise ResumeError(EXIT_INTERNAL, "resume modified TO_ZCODE.md")
        return {
            "event": "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR",
            "project_id": active["project_id"],
            "previous_status": "HUMAN_REVIEW",
            "status": "SUPERVISOR_TURN",
            "receipt_id": receipt["receipt_id"],
            "receipt_sha256": receipt["receipt_sha256"],
            "receipt_archive": str(archive_path),
            "next_message_id_unchanged": next_message_id,
            "executor_dispatched": False,
        }
    finally:
        m.release_lock()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or apply an audited HUMAN_REVIEW resume receipt."
    )
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    prepare = sub.add_parser("prepare", help="Bind human decision content to current state")
    prepare.add_argument("--project-id", required=True)
    prepare.add_argument("--decision-file", required=True)
    prepare.add_argument("--receipt-out", required=True)

    apply = sub.add_parser("apply", help="Validate and commit HUMAN_REVIEW -> SUPERVISOR_TURN")
    apply.add_argument("--receipt", required=True)

    reconcile = sub.add_parser(
        "reconcile-claimed-timeout",
        help="Reconcile a retired claimed timeout with no committed completion",
    )
    reconcile.add_argument("--project-id", default=None)

    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        m = load_runtime_module(root)
        if args.command == "prepare":
            payload = validate_decision_payload(
                read_json_strict(Path(args.decision_file).resolve(), "human decision input")
            )
            result = prepare_receipt(
                m,
                project_id=args.project_id,
                decision_payload=payload,
                receipt_out=Path(args.receipt_out).resolve(),
            )
            event = {
                "event": "HUMAN_DECISION_RECEIPT_PREPARED",
                "project_id": result["project_id"],
                "receipt_id": result["receipt_id"],
                "receipt_sha256": result["receipt_sha256"],
                "previous_project_state_sha256": result["previous_project_state_sha256"],
                "receipt_path": str(Path(args.receipt_out).resolve()),
                "project_status_changed": False,
            }
        elif args.command == "reconcile-claimed-timeout":
            event = reconcile_claimed_timeout(m, project_id=args.project_id)
        else:
            event = apply_receipt(m, receipt_path=Path(args.receipt).resolve())
        print(json.dumps(event, ensure_ascii=False, indent=2))
        return EXIT_OK
    except ResumeError as exc:
        print(f"HUMAN_REVIEW_RESUME_FAILED: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:
        print(f"HUMAN_REVIEW_RESUME_FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
