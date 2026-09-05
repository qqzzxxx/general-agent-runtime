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
    expected_inbox_hash = authorization.get("TO_ZCODE_SHA256")
    if (
        not isinstance(expected_inbox_hash, str)
        or not re.fullmatch(r"[0-9a-f]{64}", expected_inbox_hash)
        or not m.TO_ZCODE.is_file()
        or m.sha256(m.TO_ZCODE) != expected_inbox_hash
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
