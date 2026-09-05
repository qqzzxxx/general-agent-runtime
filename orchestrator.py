from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# G5A.5: Runtime Root is the installation location of the orchestrator itself —
# stable, cwd-independent, no environment overrides.
ROOT = Path(__file__).resolve().parent
CONTROL = ROOT / "control"
LOGS = ROOT / "logs"
HANDOFF_ARCHIVE = ROOT / "handoff" / "archive"
REPORTS = ROOT / "reports"

PROJECT_STATE = CONTROL / "project_state.json"
RUNTIME_STATE = CONTROL / "orchestrator_runtime.json"
SUPERVISOR_RULES = CONTROL / "CODEX_SUPERVISOR_RUNTIME.md"
RESEARCH_STATE = ROOT / "RESEARCH_STATE.md"
COMMERCIAL_GOAL = CONTROL / "CROSS_BORDER_GOAL.md"

TO_ZCODE = ROOT / "TO_ZCODE.md"
SUPERVISOR_BRIEF = ROOT / "SUPERVISOR_BRIEF.md"
ZCODE_DONE = ROOT / "ZCODE_DONE.flag"
ZCODE_LAST_PROCESSED = ROOT / "ZCODE_LAST_PROCESSED.txt"
STOP_FLAG = CONTROL / "STOP"
HUMAN_REVIEW_FLAG = CONTROL / "HUMAN_REVIEW"
LOCK_FILE = CONTROL / ".orchestrator.lock"
CODEX_LAST_OUTPUT = ROOT / "CODEX_LAST_OUTPUT.txt"
USER_ATTENTION = CONTROL / "USER_ATTENTION.json"
# G3: USER_STATUS.md is Runtime-owned and stays at ROOT\reports in every mode;
# REPORTS itself becomes project-scoped in isolated mode.
USER_STATUS_REPORT = ROOT / "reports" / "USER_STATUS.md"
# G1-D: basenames excluded from final-report fallback discovery (same values as
# the pre-G1 inline set; data moved out of discover_final_report, logic unchanged).
FINAL_REPORT_EXCLUDED_BASENAMES = {
    USER_STATUS_REPORT.name.lower(),
    "infrastructure_status.md",
    "v2_migration_audit.md",
}

# G2: static, versioned Project Profiles. Data only — the Core evaluates no domain
# rules of its own; it loads, schema-validates, and injects Profile content verbatim.
# profiles are runtime-owned static data and ship with the orchestrator file itself,
# so they resolve relative to the module location — deliberately NOT ROOT (project
# scopes may change, the runtime install does not).
PROFILES_DIR = Path(__file__).resolve().parent / "profiles"
SUPPORTED_PROFILES = ("GENERAL", "ACADEMIC_RESEARCH", "SOFTWARE_ENGINEERING", "BUSINESS_RESEARCH")
PROFILE_SCHEMA_VERSION = 1
PROFILE_REQUIRED_KEYS = {
    "profile_schema_version", "profile_id", "profile_version", "display_name",
    "supervisor_guidance_file", "executor_guidance_file", "final_verification_policy_id",
}

# G3: active project pointer. ACTIVE_PROJECT None = legacy single-project mode
# (root-level project layout, production V1.5 behavior). The pointer file lives in
# control\ and is the ONLY new Core input; its absence is legacy, its presence is
# validated strictly (fail closed — never a silent fallback to legacy).
ACTIVE_PROJECT_FILE = CONTROL / "ACTIVE_PROJECT.json"
ACTIVE_PROJECT = None  # set by activate_project_scope()
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# G4: declarative Final Verification policies. The Core understands field/op/value
# rules only; it has no knowledge of what any claim_type means. Operators are a
# fixed, tiny set — unknown operators fail closed at load time.
POLICY_SCHEMA_VERSION = 1
POLICY_REQUIRED_KEYS = {
    "policy_schema_version", "policy_id", "policy_version", "claim_count",
    "allowed_result_statuses", "impact_acceptance", "unknown_claim_type", "claim_types",
}
POLICY_OPERATORS = {"EQUALS", "NONEMPTY_STRING", "NONEMPTY_LIST", "INTEGER_GTE"}
POLICY_UNKNOWN_MODES = {"FAIL_CLOSED", "ENVELOPE_ONLY"}

POLL_SECONDS = 2.0
CODEX_TIMEOUT_SECONDS = 20 * 60
SUPERVISOR_MODEL = "gpt-5.6-sol"
SUPERVISOR_REASONING_EFFORT = "high"
DEFAULT_EXECUTOR_TIMEOUT_SECONDS = 45 * 60
# ZCode Desktop Scheduled Automation may only wake once per hour. Executor timeout
# must therefore include at least one full scheduler cycle in addition to stage MAX_TIME.
MIN_SCHEDULER_GRACE_SECONDS = 60 * 60
SUPERVISOR_TERMINAL = {"COMPLETE", "BLOCKED", "HUMAN_REVIEW", "STOPPED"}
# FIX-700102: a Supervisor dispatch that fails mechanical validation gets at most this
# many bounded Supervisor repair turns before the failure is terminal. Repair is a
# Supervisor turn, never an Executor stage; the validator itself is never loosened.
MAX_DISPATCH_VALIDATION_REPAIRS = 1
DESKTOP_NOTIFICATIONS_ENABLED = True
USER_NOTIFICATION_CONSOLE_ENABLED = True

# Production V1.5 — Final Verification Gate.
FINAL_VERIFICATION_POLICY_VERSION = 1
FINAL_VERIFICATION_CLAIM_MIN = 3
FINAL_VERIFICATION_CLAIM_MAX = 8
FINAL_VERIFICATION_TASK_KIND = "FINAL_VERIFICATION"
FINAL_VERIFICATION_ALLOWED_RESULT_STATUSES = {
    "SUPPORTED",
    "PARTIALLY_SUPPORTED",
    "WEAK",
    "UNSUPPORTED",
    "CONTRADICTED",
    "NOT_VERIFIABLE",
}

JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.S | re.I)

IDENTITY_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")
AUTHORIZED_DISPATCH_SCHEMA_VERSION = 1

# COMPLETION-SEAL-V1: authoritative completions live in the Runtime-owned ledger
# (handoff/completion_ledger/). The root SUPERVISOR_BRIEF.md / ZCODE_LAST_PROCESSED.txt
# / ZCODE_DONE.flag files are DEMOTED to derived compatibility artifacts and wake
# hints; they are never completion truth on their own.
_COMPLETION_HELPER = None


def _completion_helper():
    """Load scripts/executor_completion.py (the Runtime completion commit helper)."""
    global _COMPLETION_HELPER
    if _COMPLETION_HELPER is None:
        import importlib.util

        path = Path(__file__).resolve().parent / "scripts" / "executor_completion.py"
        spec = importlib.util.spec_from_file_location("executor_completion_runtime", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _COMPLETION_HELPER = module
    return _COMPLETION_HELPER


def _active_project_id():
    return ACTIVE_PROJECT.get("project_id") if isinstance(ACTIVE_PROJECT, dict) else None

# Human-review resume is a separate, human-originated control-plane receipt. It is
# deliberately not an Executor dispatch and contains no MESSAGE_ID/NONCE.
HUMAN_DECISION_RECEIPT_SCHEMA_VERSION = 1
HUMAN_DECISION_RESUME_SCHEMA_VERSION = 2
HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION = 1
HUMAN_DECISION_RECEIPT_ID_PATTERN = re.compile(
    r"^human-decision-[0-9a-f]{32}$"
)
HUMAN_DECISION_RECEIPT_REQUIRED_KEYS = {
    "schema_version",
    "receipt_id",
    "project_id",
    "previous_status",
    "human_decision",
    "submitted_at",
    "previous_project_state_sha256",
    "previous_human_review_flag_sha256",
    "receipt_sha256",
}
HUMAN_DECISION_BODY_REQUIRED_KEYS = {
    "decision_content",
    "constraints_verbatim",
}
HUMAN_DECISION_RESUME_BASE_KEYS = {
    "schema_version",
    "status",
    "transition",
    "project_id",
    "receipt_id",
    "receipt_sha256",
    "receipt_file_sha256",
    "receipt_path",
    "previous_status",
    "previous_project_state_sha256",
    "resumed_at",
}
HUMAN_DECISION_CONSUMED_KEYS = HUMAN_DECISION_RESUME_BASE_KEYS | {
    "consumed_at",
    "resulting_supervisor_decision",
    "resulting_lifecycle_state",
    "decision_sha256",
    "decision_identity",
}
HUMAN_DECISION_DECISION_IDENTITY_KEYS = {
    "kind",
    "decision_history_index",
    "sha256",
    "message_id",
    "task_id",
    "stage_id",
}
HUMAN_DECISION_SUPERVISOR_RESULT_KEYS = {
    "schema_version",
    "project_id",
    "receipt_id",
    "receipt_sha256",
    "previous_project_state_sha256",
    "supervisor_decision",
    "resulting_lifecycle_state",
    "project_state_patch",
    "executor_task",
}
HUMAN_DECISION_SUPERVISOR_DECISION_KEYS = {
    "decision",
    "at",
    "reason",
    "scope",
    "message_id",
    "task_id",
    "stage_id",
}
HUMAN_DECISION_ALLOWED_DECISIONS = {
    "CONTINUE",
    "REDIRECT",
    "CHANGE_METHOD",
    "REVISE",
    "STOP",
    "HUMAN_REVIEW",
}
HUMAN_DECISION_RESULTING_STATUSES = {
    "WAITING_EXECUTOR",
    "COMPLETE",
    "BLOCKED",
    "STOPPED",
    "HUMAN_REVIEW",
}
HUMAN_DECISION_RUNTIME_OWNED_STATE_KEYS = {
    "schema_version",
    "project_id",
    "project_type",
    "profile",
    "created_at",
    "started_at",
    "goal_file",
    "updated_at",
    "last_supervisor_decision",
    "decision_history",
    "human_review_resume",
    "human_decision_consumption_ledger",
}


def task_value(task: dict | None, key: str, default=None):
    """Read a task field from either the v2 uppercase wire schema or older lowercase state keys."""
    if not isinstance(task, dict):
        return default
    if key in task:
        return task[key]
    lower = key.lower()
    if lower in task:
        return task[lower]
    return default


def task_identity_matches(payload: dict, current: dict) -> bool:
    return all(payload.get(key) == task_value(current, key) for key in IDENTITY_KEYS)


def dispatch_candidate_paths() -> list[Path]:
    candidates = []
    exact = ROOT / "TO_ZCODE.md.tmp"
    if exact.exists():
        candidates.append(exact)
    for path in ROOT.glob("TO_ZCODE.md.*.tmp"):
        if path not in candidates:
            candidates.append(path)
    return candidates


def load_or_promote_dispatch(state: dict) -> dict:
    """Return the current task payload, promoting a validated Codex staging file if needed.

    Codex is instructed to publish atomically. In practice a turn can occasionally leave
    TO_ZCODE.md.tmp behind after updating state. The orchestrator may mechanically finish
    that rename only when the staged payload exactly matches Supervisor-owned current_task.
    This adds no research/business judgment and prevents a valid dispatch from becoming a
    fatal orchestration error.
    """
    current = state.get("current_task") or {}
    root_error = None
    try:
        payload = parse_json_fence(TO_ZCODE)
        if task_identity_matches(payload, current):
            return payload
        root_error = "root TO_ZCODE.md identity does not match current_task"
    except Exception as exc:
        root_error = str(exc)

    matching: list[tuple[Path, dict]] = []
    rejected: list[str] = []
    for candidate in dispatch_candidate_paths():
        try:
            payload = parse_json_fence(candidate)
        except Exception as exc:
            rejected.append(f"{candidate.name}: parse error: {exc}")
            continue
        if task_identity_matches(payload, current):
            matching.append((candidate, payload))
        else:
            rejected.append(f"{candidate.name}: identity mismatch")

    if len(matching) == 1:
        candidate, payload = matching[0]
        os.replace(candidate, TO_ZCODE)
        log(
            "Promoted validated staged Executor task",
            staged_file=candidate.name,
            message_id=payload.get("MESSAGE_ID"),
            task_id=payload.get("TASK_ID"),
            stage_id=payload.get("STAGE_ID"),
        )
        return payload
    if len(matching) > 1:
        names = [p.name for p, _ in matching]
        raise RuntimeError(f"Multiple staged TO_ZCODE candidates match current_task: {names}")

    detail = "; ".join(rejected) if rejected else "no staged task file found"
    raise RuntimeError(
        "No valid published Executor task matches current_task. "
        f"Root error: {root_error}. Staged candidates: {detail}"
    )


def validate_dispatch_payload(
    runtime: dict,
    state: dict,
    task: dict,
    *,
    allow_same_identity: bool = False,
) -> dict:
    """Mechanically validate a Supervisor-chosen task without authorizing it."""
    if not isinstance(task, dict):
        raise RuntimeError("Codex Executor task must be a JSON object")
    required = {"MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE", "OBJECTIVE", "OUTPUTS"}
    missing = sorted(required - task.keys())
    if missing:
        raise RuntimeError(f"Codex published malformed TO_ZCODE.md; missing {missing}")
    current = state.get("current_task") or {}
    for key in IDENTITY_KEYS:
        if task.get(key) != task_value(current, key):
            raise RuntimeError(f"TO_ZCODE/state mismatch for {key}")
    msg_id = int(task["MESSAGE_ID"])
    retired = runtime.get("retired_message_ids") or []
    if msg_id in {int(value) for value in retired}:
        raise RuntimeError(f"Codex attempted permanently retired MESSAGE_ID={msg_id}")

    validate_final_verification_dispatch(state, task)

    # FV-IDENTITY-BINDING-V1 anti-repeat guard: when a mechanically PASS receipt for
    # this exact claims hash is already the Runtime's freshest consumed receipt,
    # re-executing the identical verification cannot add information (same claims,
    # unchanged artifact) and only masks an internal bookkeeping/binding problem.
    # Fail closed: repair the acceptance binding to the recorded receipt, or escalate
    # to HUMAN_REVIEW. A newer consumed receipt (changed artifact) or a FAIL /
    # INCONCLUSIVE verification history re-enables ordinary re-verification unchanged.
    if is_final_verification_task(task):
        gate = task.get("FINAL_VERIFICATION_GATE") or {}
        repeat_claims_hash = str(gate.get("CLAIMS_HASH") or "")
        if repeat_claims_hash:
            for entry in runtime.get("final_verification_receipt_ledger") or []:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("claims_hash") or "") != repeat_claims_hash:
                    continue
                if entry.get("mechanical_pass") is not True:
                    continue
                if str(entry.get("overall_status") or "").upper() != "PASS":
                    continue
                if int(runtime.get("last_consumed_message_id") or -1) != int(entry.get("message_id") or -1):
                    continue
                raise RuntimeError(
                    "Identical Final Verification re-dispatch rejected: MESSAGE_ID "
                    f"{entry.get('message_id')} already consumed a mechanically PASS "
                    f"receipt for claims_hash {repeat_claims_hash}. Repair "
                    "final_verification to reference that MESSAGE_ID and its "
                    "verification_receipt_sha256 instead of re-executing the same "
                    "verification, or set HUMAN_REVIEW."
                )

    claim_required_from = int(runtime.get("claim_protocol_required_from_message_id") or 0)
    if claim_required_from and msg_id >= claim_required_from:
        if int(task.get("CLAIM_PROTOCOL_VERSION") or 0) != 1:
            raise RuntimeError(
                f"Executor claim protocol missing for MESSAGE_ID={msg_id}; required from {claim_required_from}"
            )
        protocol = task.get("EXECUTOR_PROTOCOL")
        if not isinstance(protocol, list) or not any(
            "executor_claim.py acquire" in str(item) for item in protocol
        ):
            raise RuntimeError(
                f"Executor claim acquisition instruction missing for MESSAGE_ID={msg_id}"
            )

    if msg_id <= int(runtime.get("last_consumed_message_id", 0)):
        raise RuntimeError(
            f"Codex attempted stale/reused MESSAGE_ID={msg_id}; "
            f"already consumed through {runtime.get('last_consumed_message_id')}"
        )
    same_identity = (
        msg_id == runtime.get("last_dispatched_message_id")
        and task["NONCE"] == runtime.get("last_dispatched_nonce")
    )
    if same_identity and not allow_same_identity:
        raise RuntimeError("Codex re-issued the exact same task identity")
    return task


def register_dispatched_task(runtime: dict, state: dict, *, allow_same_identity: bool = False) -> dict:
    """Validate and atomically authorize the exact published Executor dispatch.

    TO_ZCODE is deliberately published before authorization. The Executor claim helper
    requires the authorization record and rechecks the exact inbox hash/identity, so a
    crash before the final save leaves a visible task unclaimable, while a missing or
    changed inbox after the save is also unclaimable.
    """
    task = load_or_promote_dispatch(state)
    validate_dispatch_payload(
        runtime,
        state,
        task,
        allow_same_identity=allow_same_identity,
    )
    msg_id = int(task["MESSAGE_ID"])

    # Bind authorization to one exact post-validation inbox snapshot. A replacement
    # during validation is either rejected here or by the claim helper's hash check.
    dispatch_bytes, published_task = read_json_fence_snapshot(TO_ZCODE)
    if published_task != task:
        raise RuntimeError("TO_ZCODE.md changed during mechanical dispatch validation")

    authorized = {
        "schema_version": AUTHORIZED_DISPATCH_SCHEMA_VERSION,
        **{key: task[key] for key in IDENTITY_KEYS},
        "TO_ZCODE_SHA256": hashlib.sha256(dispatch_bytes).hexdigest(),
        "AUTHORIZED_AT": stamp(),
        "IS_FINAL_VERIFICATION": is_final_verification_task(task),
    }
    if authorized["IS_FINAL_VERIFICATION"]:
        # FV-IDENTITY-BINDING-V1: the Runtime's own validated authorization record is
        # the authoritative FV source. Supervisor-authored current_task carries only
        # identity keys, so consume-time FV detection must come from here — never from
        # transient project-state bookkeeping.
        authorized["FINAL_VERIFICATION_GATE"] = task["FINAL_VERIFICATION_GATE"]
    runtime["authorized_dispatch"] = authorized
    runtime["last_dispatched_message_id"] = msg_id
    runtime["last_dispatched_nonce"] = task["NONCE"]
    runtime["timeout_notified_for_nonce"] = None
    # FIX-F03: watchdog fallback timestamp owned by the orchestrator itself.
    runtime["dispatch_registered_at"] = stamp()
    runtime["dispatch_validation_repair_used"] = 0
    if isinstance(state.get("dispatch_repair"), dict):
        state.pop("dispatch_repair", None)
        state["updated_at"] = stamp()
        atomic_json(PROJECT_STATE, state)
    save_runtime(runtime)
    return task


def quarantine_dispatch_candidate(state: dict, detail: str) -> str | None:
    """Move a mechanically rejected dispatch candidate out of the Executor inbox.

    TO_ZCODE is published before authorization by design, so a candidate that fails
    validation would otherwise stay visible forever while authorization never covers
    it — exactly the 700102 incident, where every Scheduled Automation wakeup hit the
    same authorization_message_id_mismatch. Quarantining is mechanical: only the file
    whose identity matches Supervisor-owned current_task is moved, atomically, into
    handoff/quarantine/ for audit. The Executor then sees no inbox and silently ends,
    the same safe path as a cold start without a task. No content is ever rewritten.
    """
    inbox = TO_ZCODE
    if not inbox.exists():
        return None
    try:
        payload = parse_json_fence(inbox)
    except Exception:
        return None
    if not task_identity_matches(payload, state.get("current_task") or {}):
        return None
    quarantine_dir = ROOT / "handoff" / "quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(inbox.read_bytes()).hexdigest()[:12]
    target = quarantine_dir / f"dispatch-{payload.get('MESSAGE_ID')}-{digest}-rejected.md"
    os.replace(inbox, target)
    log(
        "Quarantined mechanically rejected dispatch candidate",
        message_id=payload.get("MESSAGE_ID"),
        task_id=payload.get("TASK_ID"),
        quarantined=str(target.relative_to(ROOT)),
        reason=detail[:400],
    )
    return str(target.relative_to(ROOT))


def handle_dispatch_registration_failure(runtime: dict, state: dict, exc: Exception) -> dict:
    """First mechanical rejection of a Supervisor-owned dispatch: one bounded repair turn.

    Fail-closed semantics are preserved: the invalid candidate is never authorized, the
    Executor never executes it, and the validation error is never loosened. The rejected
    candidate is quarantined out of the inbox FIRST — on the repair path and on the
    terminal path alike — so no rejected dispatch can stay visible and trap every future
    Scheduled Automation wakeup in the same authorization mismatch (the 700102 trap).
    The injected prompt then tells the Supervisor exactly which schema defect to fix. A
    second consecutive registration failure on the same repair chain re-raises and is
    terminal — no infinite retry.
    """
    quarantined = quarantine_dispatch_candidate(state, repr(exc))
    used = int(runtime.get("dispatch_validation_repair_used", 0) or 0)
    if used >= MAX_DISPATCH_VALIDATION_REPAIRS:
        runtime["last_dispatch_validation_error"] = repr(exc)[:800]
        runtime["last_quarantined_dispatch"] = quarantined
        save_runtime(runtime)
        log(
            "Dispatch validation failed again; terminal, no repair attempts left",
            attempts_used=used,
            quarantined=quarantined,
            error=repr(exc)[:400],
        )
        raise exc
    runtime["dispatch_validation_repair_used"] = used + 1
    runtime["last_dispatch_validation_error"] = repr(exc)[:800]
    runtime["last_quarantined_dispatch"] = quarantined
    save_runtime(runtime)
    state["status"] = "SUPERVISOR_TURN"
    state["dispatch_repair"] = {
        "error": repr(exc)[:800],
        "rejected_identity": {
            key: task_value(state.get("current_task") or {}, key) for key in IDENTITY_KEYS
        },
        "quarantined_dispatch": quarantined,
        "repair_attempt": used + 1,
        "max_repair_attempts": MAX_DISPATCH_VALIDATION_REPAIRS,
        "at": stamp(),
    }
    state["updated_at"] = stamp()
    atomic_json(PROJECT_STATE, state)
    log(
        "Dispatch validation failed; one bounded Supervisor repair turn armed",
        attempt=used + 1,
        max_attempts=MAX_DISPATCH_VALIDATION_REPAIRS,
        quarantined=quarantined,
        error=repr(exc)[:400],
    )
    return state


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp() -> str:
    return utc_now().isoformat(timespec="seconds")


def parse_duration_seconds(value, default: int) -> int:
    """Parse numeric seconds or simple human-readable durations without crashing the orchestrator."""
    if value is None:
        return int(default)
    if isinstance(value, bool):
        return int(default)
    if isinstance(value, (int, float)):
        return max(1, int(value))

    text = str(value).strip().lower()
    if not text:
        return int(default)

    try:
        return max(1, int(float(text)))
    except ValueError:
        pass

    match = re.fullmatch(
        r"([0-9]+(?:\.[0-9]+)?)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours)",
        text,
    )
    if not match:
        log("Invalid duration metadata; using safe default", raw_value=str(value), default_seconds=int(default))
        return int(default)

    amount = float(match.group(1))
    unit = match.group(2)
    multiplier = 1
    if unit in {"m", "min", "mins", "minute", "minutes"}:
        multiplier = 60
    elif unit in {"h", "hr", "hrs", "hour", "hours"}:
        multiplier = 3600
    return max(1, int(amount * multiplier))


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def atomic_json(path: Path, value: dict) -> None:
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def atomic_create(path: Path, content: str) -> None:
    """Atomically create an immutable artifact; never replace an existing target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # A hard link is an atomic create-if-absent on the same filesystem. Unlike
        # os.replace, it cannot overwrite an earlier immutable receipt.
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json_sha256(value: dict) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def human_decision_receipt_hash(receipt: dict) -> str:
    if not isinstance(receipt, dict):
        raise RuntimeError("human decision receipt must be a JSON object")
    payload = dict(receipt)
    payload.pop("receipt_sha256", None)
    return canonical_json_sha256(payload)


def _strict_timezone_timestamp(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"{field} must be a non-empty timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception as exc:
        raise RuntimeError(f"{field} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise RuntimeError(f"{field} must include a timezone")
    return value


def validate_human_decision_receipt(
    receipt: dict,
    *,
    expected_project_id: str | None = None,
    expected_previous_state_sha256: str | None = None,
) -> dict:
    """Validate a structured Human Decision receipt and its canonical self-hash."""
    if not isinstance(receipt, dict):
        raise RuntimeError("human decision receipt must be a JSON object")
    missing = sorted(HUMAN_DECISION_RECEIPT_REQUIRED_KEYS - set(receipt))
    unknown = sorted(set(receipt) - HUMAN_DECISION_RECEIPT_REQUIRED_KEYS)
    if missing or unknown:
        raise RuntimeError(
            f"human decision receipt schema mismatch (missing={missing}, unknown={unknown})"
        )
    if receipt.get("schema_version") != HUMAN_DECISION_RECEIPT_SCHEMA_VERSION:
        raise RuntimeError("human decision receipt schema_version must be 1")

    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not HUMAN_DECISION_RECEIPT_ID_PATTERN.fullmatch(receipt_id):
        raise RuntimeError("human decision receipt_id is invalid")
    project_id = receipt.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise RuntimeError("human decision project_id is invalid")
    if expected_project_id is not None and project_id != expected_project_id:
        raise RuntimeError(
            f"human decision project mismatch: receipt={project_id!r}, active={expected_project_id!r}"
        )
    if receipt.get("previous_status") != "HUMAN_REVIEW":
        raise RuntimeError("human decision previous_status must be HUMAN_REVIEW")

    decision = receipt.get("human_decision")
    if not isinstance(decision, dict):
        raise RuntimeError("human_decision must be a JSON object")
    body_missing = sorted(HUMAN_DECISION_BODY_REQUIRED_KEYS - set(decision))
    body_unknown = sorted(set(decision) - HUMAN_DECISION_BODY_REQUIRED_KEYS)
    if body_missing or body_unknown:
        raise RuntimeError(
            f"human_decision schema mismatch (missing={body_missing}, unknown={body_unknown})"
        )
    content = decision.get("decision_content")
    if not isinstance(content, str) or not content.strip() or len(content) > 24000:
        raise RuntimeError("human_decision.decision_content must be a non-empty string")
    constraints = decision.get("constraints_verbatim")
    if (
        not isinstance(constraints, list)
        or not constraints
        or len(constraints) > 64
        or any(not isinstance(item, str) or not item.strip() for item in constraints)
        or sum(len(item) for item in constraints if isinstance(item, str)) > 32000
    ):
        raise RuntimeError(
            "human_decision.constraints_verbatim must be a non-empty list of non-empty strings"
        )

    _strict_timezone_timestamp(receipt.get("submitted_at"), "submitted_at")
    previous_hash = receipt.get("previous_project_state_sha256")
    if not isinstance(previous_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", previous_hash):
        raise RuntimeError("previous_project_state_sha256 must be lowercase SHA-256 hex")
    if expected_previous_state_sha256 is not None and previous_hash != expected_previous_state_sha256:
        raise RuntimeError("stale project state: receipt hash does not match current project_state.json")
    flag_hash = receipt.get("previous_human_review_flag_sha256")
    if flag_hash is not None and (
        not isinstance(flag_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", flag_hash)
    ):
        raise RuntimeError("previous_human_review_flag_sha256 must be null or lowercase SHA-256 hex")

    claimed_hash = receipt.get("receipt_sha256")
    if not isinstance(claimed_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", claimed_hash):
        raise RuntimeError("receipt_sha256 must be lowercase SHA-256 hex")
    actual_hash = human_decision_receipt_hash(receipt)
    if not hmac.compare_digest(claimed_hash, actual_hash):
        raise RuntimeError("human decision receipt_sha256 mismatch")
    return receipt


def parse_json_fence(path: Path) -> dict:
    _, value = read_json_fence_snapshot(path)
    return value


def read_json_fence_snapshot(path: Path) -> tuple[bytes, dict]:
    """Read exact wire bytes once, then parse that same hashable snapshot."""
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    blocks = JSON_FENCE.findall(text)
    if len(blocks) != 1:
        raise ValueError(f"{path.name} must contain exactly one fenced JSON object")
    value = json.loads(blocks[0])
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} wire payload is not a JSON object")
    return raw, value


def log(message: str, **details) -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    record = {"at": stamp(), "message": message, **details}
    line = json.dumps(record, ensure_ascii=False)
    print(f"[{record['at']}] {message}", flush=True)
    with (LOGS / "orchestrator.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")



def _relative_display(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except Exception:
        return str(path)


def discover_final_report(state: dict) -> Path | None:
    """Mechanically locate the report path without interpreting report content."""
    candidate_keys = ("final_report", "final_report_path", "report", "report_path")
    scope_root = _active_project_root()  # G3: final_report lives in the active project
    for key in candidate_keys:
        raw = state.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = scope_root / path
        if ACTIVE_PROJECT is not None:
            try:
                resolved = path.resolve()
                resolved.relative_to(Path(scope_root).resolve())
            except Exception:
                raise RuntimeError(f"final_report escapes the project scope: {raw!r}")
            if resolved.is_file():
                return resolved
            continue
        if path.exists() and path.is_file():
            return path

    if not REPORTS.exists():
        return None

    candidates = [
        p for p in REPORTS.glob("*.md")
        if p.is_file() and p.name.lower() not in FINAL_REPORT_EXCLUDED_BASENAMES
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _notification_reason(state: dict, details: dict | None = None) -> str:
    details = details or {}
    if details.get("error"):
        return str(details["error"])
    if state.get("blocked_reason"):
        return str(state["blocked_reason"])
    last = state.get("last_supervisor_decision")
    if isinstance(last, dict) and last.get("reason"):
        return str(last["reason"])
    if HUMAN_REVIEW_FLAG.exists():
        try:
            text = HUMAN_REVIEW_FLAG.read_text(encoding="utf-8-sig", errors="replace").strip()
            if text:
                return text[:1200]
        except Exception:
            pass
    return ""


def build_user_notification(kind: str, state: dict, details: dict | None = None) -> dict:
    details = details or {}
    last = state.get("last_supervisor_decision")
    if not isinstance(last, dict):
        last = {}
    report = discover_final_report(state)

    status = str(state.get("status") or kind)
    kind_upper = kind.upper()
    if kind_upper == "COMPLETE":
        headline = "项目已完成"
        next_step = "本轮无人值守研究已结束；不会再自动派发新的 Executor 任务。查看最终报告后再决定是否启动下一阶段。"
    elif kind_upper == "HUMAN_REVIEW":
        headline = "需要人工决策"
        next_step = "自动化已暂停；请查看 USER_STATUS.md / HUMAN_REVIEW 标记并作出决定后再恢复。"
    elif kind_upper == "BLOCKED":
        headline = "项目已阻塞"
        next_step = "自动化已停止；请查看阻塞原因，决定是否更换方法、补充授权或结束项目。"
    elif kind_upper == "DEADLINE_REACHED":
        headline = "项目达到截止时间"
        next_step = "自动化因硬截止时间停止；请查看当前状态与已有报告。"
    elif kind_upper == "ORCHESTRATOR_ERROR":
        headline = "编排器发生不可恢复错误"
        next_step = "自动化已停止；请查看错误原因和 logs/orchestrator.jsonl 后再恢复。"
    elif kind_upper in {"STOPPED", "STOPPED_BY_USER"}:
        headline = "项目已停止"
        next_step = "当前自动化已停止；如需继续，应由用户显式恢复或启动新项目。"
    else:
        headline = f"项目状态：{kind_upper}"
        next_step = "请查看项目状态与报告。"

    current = state.get("current_task") if isinstance(state.get("current_task"), dict) else {}
    payload = {
        "notification_version": 1,
        "generated_at": stamp(),
        "event": kind_upper,
        "headline": headline,
        "project": state.get("project"),
        "phase": state.get("phase"),
        "project_status": status,
        "supervisor_decision": last.get("decision"),
        "decision_scope": last.get("scope"),
        "reason": _notification_reason(state, details),
        "final_report": _relative_display(report),
        "user_status_report": _relative_display(USER_STATUS_REPORT),
        "current_message_id": task_value(current, "MESSAGE_ID"),
        "current_task_id": task_value(current, "TASK_ID"),
        "current_stage_id": task_value(current, "STAGE_ID"),
        "next_step": next_step,
        # FIX-F18: a user STOP equally means no further automatic tasks.
        "no_further_executor_tasks": kind_upper in {"COMPLETE", "BLOCKED", "HUMAN_REVIEW", "DEADLINE_REACHED", "STOPPED", "STOPPED_BY_USER", "ORCHESTRATOR_ERROR"},
    }
    if details:
        payload["details"] = details
    return payload


def render_user_status_markdown(payload: dict) -> str:
    def val(key, fallback="-"):
        value = payload.get(key)
        return fallback if value in (None, "") else str(value)

    lines = [
        "# Agent Handshake 用户状态",
        "",
        f"- **生成时间**：{val('generated_at')}",
        f"- **事件**：{val('event')}",
        f"- **状态**：{val('headline')}",
        f"- **项目**：{val('project')}",
        f"- **阶段**：{val('phase')}",
        f"- **项目状态**：{val('project_status')}",
        f"- **Supervisor 决策**：{val('supervisor_decision')}",
        f"- **决策范围**：{val('decision_scope')}",
        f"- **最后任务**：MESSAGE_ID={val('current_message_id')} / {val('current_task_id')} / {val('current_stage_id')}",
        f"- **最终报告**：{val('final_report', '未发现或未记录')}",
        "",
        "## 为什么停在这里",
        "",
        val("reason", "未提供额外原因。"),
        "",
        "## 你现在该做什么",
        "",
        val("next_step"),
        "",
    ]
    if payload.get("no_further_executor_tasks"):
        lines += [
            "## 自动化状态",
            "",
            "**当前不会再自动派发新的 Executor 任务。** 如项目已 COMPLETE/STOPPED，可暂停 ZCode Scheduled Automation，避免无意义的空唤醒。",
            "",
        ]
    return "\n".join(lines)


def _powershell_single_quote(text: str) -> str:
    return text.replace("'", "''")


def launch_windows_desktop_notification(title: str, body: str) -> bool:
    """Best-effort, detached Windows tray balloon. Core notification does not depend on it."""
    if not DESKTOP_NOTIFICATIONS_ENABLED or os.name != "nt":
        return False
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        return False

    title = title.replace("\r", " ").replace("\n", " ")[:80]
    body = body.replace("\r", " ").replace("\n", " ")[:240]
    ps = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipIcon = [System.Windows.Forms.ToolTipIcon]::Info
$n.BalloonTipTitle = '{_powershell_single_quote(title)}'
$n.BalloonTipText = '{_powershell_single_quote(body)}'
$n.Visible = $true
$n.ShowBalloonTip(10000)
Start-Sleep -Seconds 12
$n.Dispose()
"""
    encoded = base64.b64encode(ps.encode("utf-16le")).decode("ascii")
    flags = 0
    flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
    flags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        subprocess.Popen(
            [powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        return True
    except Exception as exc:
        log("Desktop notification launch failed; file/console notification remains valid", error=repr(exc))
        return False


def _notification_key(payload: dict) -> str:
    stable = {
        "event": payload.get("event"),
        "project": payload.get("project"),
        "phase": payload.get("phase"),
        "project_status": payload.get("project_status"),
        "supervisor_decision": payload.get("supervisor_decision"),
        "decision_scope": payload.get("decision_scope"),
        "reason": payload.get("reason"),
        "final_report": payload.get("final_report"),
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def emit_user_notification(runtime: dict, kind: str, state: dict, details: dict | None = None) -> bool:
    """Persist + print + best-effort desktop notify exactly once per terminal event."""
    payload = build_user_notification(kind, state, details)
    key = _notification_key(payload)
    if runtime.get("last_user_notification_key") == key:
        return False

    try:
        # FIX-F06: the notification layer must never change the orchestration outcome.
        REPORTS.mkdir(parents=True, exist_ok=True)
        CONTROL.mkdir(parents=True, exist_ok=True)
        atomic_json(USER_ATTENTION, payload)
        atomic_write(USER_STATUS_REPORT, render_user_status_markdown(payload))
    except Exception as exc:
        log("User notification persistence failed; orchestration continues", error=repr(exc))

    if USER_NOTIFICATION_CONSOLE_ENABLED:
        width = 76
        print("\n" + "=" * width, flush=True)
        print(f"AGENT HANDSHAKE — {payload['headline']}", flush=True)
        print("=" * width, flush=True)
        print(f"Event: {payload['event']}", flush=True)
        print(f"Phase: {payload.get('phase') or '-'}", flush=True)
        print(f"Decision: {payload.get('supervisor_decision') or '-'}", flush=True)
        if payload.get("reason"):
            compact_reason = re.sub(r"\s+", " ", str(payload["reason"])).strip()
            print(f"Reason: {compact_reason[:500]}", flush=True)
        if payload.get("final_report"):
            print(f"Final report: {payload['final_report']}", flush=True)
        print(f"User status: {payload['user_status_report']}", flush=True)
        if payload.get("no_further_executor_tasks"):
            print("No further Executor tasks will be dispatched for this terminal state.", flush=True)
            print("You may pause ZCode Scheduled Automation.", flush=True)
        print("=" * width + "\n", flush=True)

    body = payload.get("reason") or payload.get("next_step") or payload.get("headline")
    desktop = launch_windows_desktop_notification(
        f"Agent Handshake: {payload['event']}",
        str(body),
    )

    runtime["last_user_notification_key"] = key
    runtime["last_user_notification_at"] = payload["generated_at"]
    runtime["last_user_notification_event"] = payload["event"]
    runtime["desktop_notification_launched"] = bool(desktop)
    save_runtime(runtime)
    log(
        "User terminal notification emitted",
        event=payload["event"],
        final_report=payload.get("final_report"),
        desktop_notification=bool(desktop),
    )
    return True



def canonical_claims_hash(claims: list[dict]) -> str:
    """Stable hash binding project state, verification task, and verification receipt."""
    canonical = json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _critical_claim_ids(claims: list[dict]) -> list[str]:
    result = []
    for claim in claims:
        if not isinstance(claim, dict):
            return []
        cid = str(claim.get("claim_id") or "").strip()
        if not cid:
            return []
        result.append(cid)
    return result


def validate_critical_claims(claims, min_count: int | None = None,
                             max_count: int | None = None) -> tuple[bool, str]:
    if not isinstance(claims, list):
        return False, "critical_claims must be a list"
    lo = FINAL_VERIFICATION_CLAIM_MIN if min_count is None else min_count
    hi = FINAL_VERIFICATION_CLAIM_MAX if max_count is None else max_count
    if not (lo <= len(claims) <= hi):
        return False, f"critical_claims count must be {lo}-{hi}; got {len(claims)}"

    ids = []
    for idx, claim in enumerate(claims, start=1):
        if not isinstance(claim, dict):
            return False, f"critical_claims[{idx}] is not an object"
        required = {
            "claim_id",
            "claim",
            "claim_type",
            "decision_impact",
            "evidence_pointers",
            "verification_standard",
        }
        missing = sorted(required - claim.keys())
        if missing:
            return False, f"critical_claims[{idx}] missing {missing}"

        cid = str(claim.get("claim_id") or "").strip()
        if not cid:
            return False, f"critical_claims[{idx}] has empty claim_id"
        ids.append(cid)

        impact = str(claim.get("decision_impact") or "").upper()
        if impact not in {"HIGH", "MEDIUM"}:
            return False, f"{cid}: decision_impact must be HIGH or MEDIUM"

        if not isinstance(claim.get("evidence_pointers"), list):
            return False, f"{cid}: evidence_pointers must be a list"

    if len(ids) != len(set(ids)):
        return False, "critical_claims contains duplicate claim_id values"
    return True, "OK"


def is_final_verification_task(task: dict | None) -> bool:
    if not isinstance(task, dict):
        return False
    if str(task.get("TASK_KIND") or "").upper() == FINAL_VERIFICATION_TASK_KIND:
        return True
    return isinstance(task.get("FINAL_VERIFICATION_GATE"), dict)


def authorized_final_verification_task(runtime: dict, brief: dict, msg_id: int) -> dict | None:
    """FV-IDENTITY-BINDING-V1: resolve the FV gate from the Runtime's own authorization
    record when Supervisor-authored current_task does not mirror it.

    The authoritative FV identity is the dispatch the Runtime itself validated and
    authorized (register_dispatched_task). A receipt consumes as Final Verification
    only when the authorization record matches the exact consumed identity and carries
    the validated gate. Any doubt returns None so the receipt is treated as an ordinary
    one (fail closed: the COMPLETE gate then rejects an unbound acceptance)."""
    authorization = runtime.get("authorized_dispatch")
    if not isinstance(authorization, dict) or authorization.get("IS_FINAL_VERIFICATION") is not True:
        return None
    if int(authorization.get("MESSAGE_ID") or -1) != int(msg_id):
        return None
    if not task_identity_matches(brief, authorization):
        return None
    gate = authorization.get("FINAL_VERIFICATION_GATE")
    if not isinstance(gate, dict):
        return None
    return {key: brief.get(key) for key in IDENTITY_KEYS} | {"FINAL_VERIFICATION_GATE": gate}


def final_verification_gate_enforced(runtime: dict, state: dict) -> bool:
    """G4: generic route first — an explicit final_verification.required=true gates ANY
    project regardless of domain. Otherwise the LEGACY V1.5 subject predicate
    (commercial Cross-Border, isolated adapter) keeps activation/grandfather
    semantics unchanged."""
    fv = state.get("final_verification")
    if isinstance(fv, dict) and fv.get("required") is True:
        return True
    if not _legacy_gate_subject(state):
        return False
    cutoff = parse_time(runtime.get("final_verification_enforce_after"))
    started = parse_time(state.get("started_at"))
    return bool(cutoff and started and started >= cutoff)


def validate_final_verification_dispatch(state: dict, task: dict) -> None:
    """Fail closed unless a verification dispatch exactly matches Supervisor-owned state."""
    if not is_final_verification_task(task):
        return

    gate = task.get("FINAL_VERIFICATION_GATE")
    if not isinstance(gate, dict):
        raise RuntimeError("FINAL_VERIFICATION task missing FINAL_VERIFICATION_GATE")
    if int(gate.get("POLICY_VERSION") or 0) != FINAL_VERIFICATION_POLICY_VERSION:
        raise RuntimeError("FINAL_VERIFICATION_GATE policy version mismatch")

    fv = state.get("final_verification")
    if not isinstance(fv, dict) or fv.get("required") is not True:
        raise RuntimeError("FINAL_VERIFICATION task published without required final_verification state")
    if str(fv.get("status") or "").upper() not in {"PENDING", "REVERIFY"}:
        raise RuntimeError("final_verification.status must be PENDING or REVERIFY at dispatch")

    policy, route = _resolve_fv_policy_for_state(state)  # G4: policy-aware validation
    claims = gate.get("CRITICAL_CLAIMS")
    ok, detail = validate_critical_claims(claims, policy["claim_count"]["min"],
                                          policy["claim_count"]["max"])
    if not ok:
        raise RuntimeError(f"Invalid FINAL_VERIFICATION critical claims: {detail}")

    computed = canonical_claims_hash(claims)
    if str(gate.get("CLAIMS_HASH") or "") != computed:
        raise RuntimeError("FINAL_VERIFICATION task CLAIMS_HASH mismatch")
    if int(gate.get("CLAIM_COUNT") or -1) != len(claims):
        raise RuntimeError("FINAL_VERIFICATION task CLAIM_COUNT mismatch")

    state_claims = fv.get("critical_claims")
    ok, detail = validate_critical_claims(state_claims, policy["claim_count"]["min"],
                                           policy["claim_count"]["max"])
    if not ok:
        raise RuntimeError(f"Invalid state final_verification critical claims: {detail}")
    state_hash = canonical_claims_hash(state_claims)
    if str(fv.get("claims_hash") or "") != state_hash:
        raise RuntimeError("project_state final_verification.claims_hash mismatch")
    if state_hash != computed:
        raise RuntimeError("FINAL_VERIFICATION task claim set does not match project_state")

    # G4: policy binding. A profile-routed project must bind POLICY_ID in the gate;
    # recorded policy ids/versions must all agree. Legacy V1.5 gates without POLICY_ID
    # keep their exact prior behavior.
    gate_policy_id = gate.get("POLICY_ID")
    if gate_policy_id is not None and str(gate_policy_id) != str(policy["policy_id"]):
        raise RuntimeError("FINAL_VERIFICATION_GATE POLICY_ID mismatch")
    if route == "profile" and gate_policy_id is None:
        raise RuntimeError("FINAL_VERIFICATION_GATE missing POLICY_ID for a profile-routed project")
    gate_policy_version = gate.get("POLICY_VERSION")
    if (gate_policy_version is not None
            and int(gate_policy_version) != int(policy["policy_version"])):
        raise RuntimeError("FINAL_VERIFICATION_GATE policy_version mismatch")
    state_policy_id = fv.get("policy_id")
    if state_policy_id is not None and str(state_policy_id) != str(policy["policy_id"]):
        raise RuntimeError("project_state final_verification.policy_id mismatch")
    state_policy_version = fv.get("policy_version")
    if (state_policy_version is not None
            and int(state_policy_version) != int(policy["policy_version"])):
        raise RuntimeError("project_state final_verification.policy_version mismatch")

    # It remains an ordinary Executor stage for concurrency safety.
    if int(task.get("CLAIM_PROTOCOL_VERSION") or 0) != 1:
        raise RuntimeError("FINAL_VERIFICATION task must use CLAIM_PROTOCOL_VERSION=1")


def _policy_rule_issue(cid: str, ctype: str, rule: dict) -> str:
    message = rule.get("message")
    if message:
        return f"{cid}: {ctype} {message}"
    return f"{cid}: {ctype} check failed: {rule['field']} {rule['op']}"


def evaluate_final_verification_receipt(current_task: dict, brief: dict, policy: dict | None = None) -> dict:
    """Mechanical acceptance envelope against the bound declarative policy.

    The Core evaluates coverage, impact acceptance, overall status and the policy's
    field/op/value rules. It performs no domain reasoning: what IP or ECONOMICS require
    lives entirely in the policy data. policy=None resolves to the LEGACY V1.5 default
    (BUSINESS_RESEARCH data) so pre-G4 call sites keep their exact behavior."""
    if policy is None:
        policy = _legacy_final_verification_policy()
    gate = current_task.get("FINAL_VERIFICATION_GATE") or {}
    claims = gate.get("CRITICAL_CLAIMS") or []
    expected_hash = str(gate.get("CLAIMS_HASH") or "")
    expected_ids = _critical_claim_ids(claims)
    claim_by_id = {str(c.get("claim_id")): c for c in claims if isinstance(c, dict)}

    result = {
        "is_final_verification": True,
        "mechanical_pass": False,
        "claims_hash": expected_hash,
        "overall_status": None,
        "claim_result_count": 0,
        "issues": [],
        "policy_id": policy["policy_id"],
        "policy_version": policy["policy_version"],
    }

    payload = brief.get("FINAL_VERIFICATION")
    if not isinstance(payload, dict):
        result["issues"].append("Receipt missing FINAL_VERIFICATION object")
        return result

    if int(payload.get("POLICY_VERSION") or 0) != FINAL_VERIFICATION_POLICY_VERSION:
        result["issues"].append("Receipt FINAL_VERIFICATION policy version mismatch")
    if str(payload.get("CLAIMS_HASH") or "") != expected_hash:
        result["issues"].append("Receipt CLAIMS_HASH does not match dispatched gate")

    overall = str(payload.get("OVERALL_STATUS") or "").upper()
    result["overall_status"] = overall
    if overall not in {"PASS", "FAIL", "INCONCLUSIVE"}:
        result["issues"].append("OVERALL_STATUS must be PASS, FAIL, or INCONCLUSIVE")

    rows = payload.get("CLAIM_RESULTS")
    if not isinstance(rows, list):
        result["issues"].append("CLAIM_RESULTS must be a list")
        return result
    result["claim_result_count"] = len(rows)

    seen = []
    row_by_id = {}
    for row in rows:
        if not isinstance(row, dict):
            result["issues"].append("CLAIM_RESULTS contains a non-object")
            continue
        cid = str(row.get("claim_id") or "").strip()
        if not cid:
            result["issues"].append("CLAIM_RESULTS row missing claim_id")
            continue
        seen.append(cid)
        row_by_id[cid] = row
        status = str(row.get("status") or "").upper()
        if status not in policy["allowed_result_statuses"]:
            result["issues"].append(f"{cid}: invalid status {status!r}")

    if len(seen) != len(set(seen)):
        result["issues"].append("CLAIM_RESULTS contains duplicate claim_id values")
    if set(seen) != set(expected_ids):
        result["issues"].append("CLAIM_RESULTS does not cover exactly the dispatched critical claims")

    claim_types = policy["claim_types"]
    unknown_mode = policy["unknown_claim_type"]
    for cid in expected_ids:
        claim = claim_by_id.get(cid) or {}
        row = row_by_id.get(cid)
        if not isinstance(row, dict):
            continue

        status = str(row.get("status") or "").upper()
        impact = str(claim.get("decision_impact") or "").upper()
        ctype = str(claim.get("claim_type") or "").upper()
        checks = row.get("checks") if isinstance(row.get("checks"), dict) else {}

        accepted = policy["impact_acceptance"].get(impact)
        if accepted is not None and status not in accepted:
            result["issues"].append(
                f"{cid}: {impact}-impact claim status {status!r} is not accepted")

        type_rules = claim_types.get(ctype)
        if type_rules is None:
            if unknown_mode == "FAIL_CLOSED":
                result["issues"].append(
                    f"{cid}: claim_type {ctype!r} is not in the active policy taxonomy")
            continue
        for rule in type_rules.get("required_checks", []):
            value = checks.get(rule["field"]) if isinstance(checks, dict) else None
            op = rule["op"]
            ok = False
            if op == "EQUALS":
                ok = str(value or "").upper() == str(rule["value"]).upper()
            elif op == "NONEMPTY_STRING":
                ok = bool(str(value or "").strip())
            elif op == "NONEMPTY_LIST":
                ok = isinstance(value, list) and len(value) > 0
            elif op == "INTEGER_GTE":
                try:
                    ok = int(value) >= int(rule["value"])
                except Exception:
                    ok = False
            if not ok:
                result["issues"].append(_policy_rule_issue(cid, ctype, rule))

    if overall != "PASS":
        result["issues"].append(f"Verifier overall status is {overall or 'MISSING'}, not PASS")

    result["mechanical_pass"] = not result["issues"]
    return result


def reconcile_final_verification_binding(runtime: dict, state: dict) -> bool:
    """FV-IDENTITY-BINDING-V1 bounded recovery: re-derive the Runtime's FV binding
    fields from its own receipt ledger when they disagree with an acceptance the
    ledger mechanically proves was consumed.

    Applies only when ALL of the following hold, so this can never approve an
    acceptance the Runtime did not itself verify:
    - project_state references one exact FV acceptance (message id + receipt sha256);
    - the Runtime's freshest consumed receipt is exactly that message id and hash;
    - the receipt ledger contains a mechanically PASS entry for that exact identity;
    - the runtime binding fields currently disagree with it (else this is a no-op).

    Ledger-less histories (e.g. receipts consumed before this fix existed) do not
    reconcile and keep failing closed. Ordinary receipts consumed later advance
    last_consumed_message_id, which disables reconciliation by design."""
    fv = state.get("final_verification")
    if not isinstance(fv, dict) or fv.get("required") is not True:
        return False
    ref_id = fv.get("verification_message_id")
    ref_receipt = str(fv.get("verification_receipt_sha256") or "")
    if ref_id is None or not ref_receipt:
        return False
    if int(runtime.get("last_consumed_message_id") or -1) != int(ref_id):
        return False
    if str(runtime.get("last_consumed_brief_sha256") or "") != ref_receipt:
        return False
    if (
        int(runtime.get("last_final_verification_message_id") or -1) == int(ref_id)
        and str(runtime.get("last_final_verification_receipt_sha256") or "") == ref_receipt
    ):
        return False
    entry = next((
        candidate for candidate in (runtime.get("final_verification_receipt_ledger") or [])
        if isinstance(candidate, dict)
        and int(candidate.get("message_id") or -1) == int(ref_id)
        and str(candidate.get("receipt_sha256") or "") == ref_receipt
        and str(fv.get("claims_hash") or "") in ("", str(candidate.get("claims_hash") or ""))
        and candidate.get("mechanical_pass") is True
        and str(candidate.get("overall_status") or "").upper() == "PASS"
    ), None)
    if entry is None:
        return False
    runtime["last_final_verification_message_id"] = int(entry["message_id"])
    runtime["last_final_verification_receipt_sha256"] = str(entry["receipt_sha256"])
    runtime["last_final_verification_claims_hash"] = entry.get("claims_hash")
    runtime["last_final_verification_overall_status"] = entry.get("overall_status")
    runtime["last_final_verification_mechanical_pass"] = True
    save_runtime(runtime)
    log(
        "Final Verification binding reconciled from receipt ledger",
        message_id=int(entry["message_id"]),
        claims_hash=entry.get("claims_hash"),
    )
    return True


def final_verification_terminal_check(runtime: dict, state: dict) -> tuple[bool, str]:
    """Whether a commercial project may mechanically enter COMPLETE."""
    if not final_verification_gate_enforced(runtime, state):
        return True, "legacy/noncommercial run is not gated"

    fv = state.get("final_verification")
    if not isinstance(fv, dict):
        return False, "final_verification object is missing"
    if fv.get("required") is not True:
        return False, "final_verification.required must be true"
    if int(fv.get("policy_version") or 0) != FINAL_VERIFICATION_POLICY_VERSION:
        return False, "final_verification policy version mismatch"
    if str(fv.get("status") or "").upper() != "PASS":
        return False, f"final_verification.status is {fv.get('status')!r}, not PASS"

    policy, _ = _resolve_fv_policy_for_state(state)  # G4: gate enforced ⇒ policy resolves
    claims = fv.get("critical_claims")
    ok, detail = validate_critical_claims(claims, policy["claim_count"]["min"],
                                           policy["claim_count"]["max"])
    if not ok:
        return False, detail
    computed_hash = canonical_claims_hash(claims)
    if str(fv.get("claims_hash") or "") != computed_hash:
        return False, "final_verification.claims_hash mismatch"

    # G4: recorded policy binding (state/final_acceptance) must match the resolved
    # policy so verification can never be reinterpreted under a different policy.
    if fv.get("policy_id") is not None or fv.get("policy_version") is not None:
        if fv.get("policy_id") is not None and str(fv["policy_id"]) != str(policy["policy_id"]):
            return False, "final_verification.policy_id does not match the resolved policy"
        if (fv.get("policy_version") is not None
                and int(fv["policy_version"]) != int(policy["policy_version"])):
            return False, "final_verification.policy_version does not match the resolved policy"

    msg_id = fv.get("verification_message_id")
    receipt_hash = str(fv.get("verification_receipt_sha256") or "")
    if msg_id is None or not receipt_hash:
        return False, "final_verification does not identify the accepted verification receipt"

    if int(msg_id) != int(runtime.get("last_final_verification_message_id") or -1):
        return False, "verification_message_id does not match runtime"
    if receipt_hash != str(runtime.get("last_final_verification_receipt_sha256") or ""):
        return False, "verification_receipt_sha256 does not match runtime"
    if computed_hash != str(runtime.get("last_final_verification_claims_hash") or ""):
        return False, "verified claim-set hash does not match runtime"
    if runtime.get("last_final_verification_mechanical_pass") is not True:
        return False, "last final-verification receipt did not pass mechanical checks"
    if str(runtime.get("last_final_verification_overall_status") or "").upper() != "PASS":
        return False, "last final-verification verifier status was not PASS"

    # Any new Executor result after verification invalidates the old approval.
    if int(runtime.get("last_consumed_message_id") or -1) != int(msg_id):
        return False, "a newer Executor receipt exists after final verification; re-verification required"
    if str(runtime.get("last_consumed_brief_sha256") or "") != receipt_hash:
        return False, "last consumed receipt is not the accepted final-verification receipt"

    return True, "PASS"


def enforce_terminal_verification_gate(runtime: dict, state: dict, source: str) -> tuple[dict, dict | None]:
    """Mechanical safety interlock: reject premature COMPLETE without doing business judgment."""
    if state.get("status") != "COMPLETE":
        return state, None

    # FV-IDENTITY-BINDING-V1: bounded ledger-backed recovery before rejecting. If the
    # ledger proves the referenced acceptance was consumed as PASS and is still the
    # freshest receipt, a lost/stale runtime binding is repaired here instead of
    # triggering another identical verification round.
    reconcile_final_verification_binding(runtime, state)
    allowed, reason = final_verification_terminal_check(runtime, state)
    if allowed:
        return state, None

    fv = state.get("final_verification")
    if not isinstance(fv, dict):
        fv = {}
    fv.setdefault("policy_version", FINAL_VERIFICATION_POLICY_VERSION)
    fv["required"] = True
    if str(fv.get("status") or "").upper() == "PASS":
        fv["status"] = "REVERIFY"
    else:
        fv.setdefault("status", "REQUIRED")
    fv["mechanical_gate_status"] = "BLOCKED"
    fv["gate_block_reason"] = reason
    fv["gate_blocked_at"] = stamp()
    state["final_verification"] = fv
    state["status"] = "SUPERVISOR_TURN"
    state["current_task"] = None
    state["updated_at"] = stamp()
    atomic_json(PROJECT_STATE, state)

    runtime["final_verification_gate_blocks"] = int(runtime.get("final_verification_gate_blocks", 0)) + 1
    runtime["last_final_verification_gate_block_reason"] = reason
    save_runtime(runtime)
    log("COMPLETE blocked by Final Verification Gate", source=source, reason=reason)

    gate_event = {
        "type": "FINAL_VERIFICATION_GATE_REQUIRED",
        "reason": reason,
        "policy_version": FINAL_VERIFICATION_POLICY_VERSION,
        "required_claims_min": FINAL_VERIFICATION_CLAIM_MIN,
        "required_claims_max": FINAL_VERIFICATION_CLAIM_MAX,
    }
    # FV-IDENTITY-BINDING-V1 anti-repeat guidance: when a mechanically PASS receipt
    # for the exact referenced claims hash is already the Runtime's freshest consumed
    # receipt, re-executing the same verification cannot change the outcome and an
    # identical re-dispatch is mechanically rejected. The Supervisor must repair the
    # acceptance binding to the authoritative receipt, or escalate to HUMAN_REVIEW.
    authoritative = None
    claims_hash = str(fv.get("claims_hash") or "")
    if claims_hash:
        authoritative = next((
            entry for entry in reversed(runtime.get("final_verification_receipt_ledger") or [])
            if isinstance(entry, dict)
            and str(entry.get("claims_hash") or "") == claims_hash
            and entry.get("mechanical_pass") is True
            and str(entry.get("overall_status") or "").upper() == "PASS"
        ), None)
    if authoritative is not None:
        gate_event["authoritative_final_verification"] = {
            "message_id": authoritative.get("message_id"),
            "receipt_sha256": authoritative.get("receipt_sha256"),
            "claims_hash": authoritative.get("claims_hash"),
        }
        gate_event["repeat_guard"] = (
            "A mechanically PASS Final Verification receipt for this claims_hash is "
            "already recorded (authoritative_final_verification). While it remains the "
            "freshest consumed Executor receipt, re-dispatching an identical "
            "FINAL_VERIFICATION task (same claims_hash) is mechanically rejected. Repair "
            "final_verification to reference exactly that MESSAGE_ID and "
            "verification_receipt_sha256 and re-attempt FINAL_ACCEPTANCE, or set "
            "HUMAN_REVIEW; do not re-execute the same verification."
        )
    return state, gate_event

def load_runtime() -> dict:
    runtime = read_json(RUNTIME_STATE)
    if isinstance(runtime, dict):
        return runtime
    legacy = read_json(ROOT / "ORCHESTRATOR_STATE.json", {}) or {}
    return {
        "schema_version": 2,
        "started_at": stamp(),
        "last_codex_run_at": None,
        "last_codex_reason": None,
        "last_consumed_message_id": int(legacy.get("last_reviewed_id", 602) or 602),
        "last_consumed_nonce": None,
        "last_consumed_brief_sha256": None,
        "last_dispatched_message_id": None,
        "last_dispatched_nonce": None,
        "authorized_dispatch": None,
        "retired_message_ids": [],
        "timeout_notified_for_nonce": None,
        "codex_invocations": 0,
        "executor_receipts_consumed": 0,
        "stale_receipts_ignored": 0,
        "protocol_errors": 0,
        "consecutive_codex_without_executor": 0,
        "last_user_notification_key": None,
        "last_user_notification_at": None,
        "last_user_notification_event": None,
        "desktop_notification_launched": False,
        "final_verification_policy_version": FINAL_VERIFICATION_POLICY_VERSION,
        "final_verification_enforce_after": None,
        "last_final_verification_message_id": None,
        "last_final_verification_receipt_sha256": None,
        "last_final_verification_claims_hash": None,
        "last_final_verification_overall_status": None,
        "last_final_verification_mechanical_pass": None,
        "final_verification_receipt_ledger": [],
        "final_verification_gate_blocks": 0,
        "last_final_verification_gate_block_reason": None,
        "dispatch_validation_repair_used": 0,
        "last_dispatch_validation_error": None,
        "last_quarantined_dispatch": None,
        "status": "RUNNING",
    }


def save_runtime(runtime: dict) -> None:
    runtime["updated_at"] = stamp()
    atomic_json(RUNTIME_STATE, runtime)


def read_project_state() -> dict:
    state = read_json(PROJECT_STATE)
    if not isinstance(state, dict):
        raise RuntimeError("control/project_state.json is missing or invalid")
    return state


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _lock_owner_dead(payload: dict) -> bool:
    """FIX-F04: True when the recorded lock owner no longer exists on this machine."""
    pid = payload.get("pid")
    if not isinstance(pid, int) or pid <= 0 or pid == os.getpid():
        return False
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        try:
            code = ctypes.c_ulong()
            if k32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return code.value != 259  # STILL_ACTIVE
            return False
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return False


def acquire_lock() -> None:
    CONTROL.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            with LOCK_FILE.open("x", encoding="utf-8") as f:
                f.write(json.dumps({"pid": os.getpid(), "started_at": stamp()}))
            return
        except FileExistsError:
            payload = read_json(LOCK_FILE, {}) or {}
            if attempt == 0 and _lock_owner_dead(payload):
                # FIX-F04: a hard crash (power loss/kill) skips the finally block and
                # leaves this lock behind. A dead owner's lock is safe to reclaim.
                log("Removing stale orchestrator lock left by a dead process", recorded=payload)
                LOCK_FILE.unlink(missing_ok=True)
                continue
            raise RuntimeError(
                "Another v2 orchestrator lock exists. Inspect control/.orchestrator.lock "
                f"before removing it. Recorded process: {payload}"
            )


def release_lock() -> None:
    try:
        LOCK_FILE.unlink()
    except FileNotFoundError:
        pass


def safe_read_text(path: Path, max_chars: int = 24000) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        return f"[UNAVAILABLE: {path.name}: {exc}]"
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[TRUNCATED BY ORCHESTRATOR: {len(text) - max_chars} chars omitted]\n"


def supervisor_effort(state: dict, reason: str) -> str:
    """All Codex Supervisor turns are permanently fixed to High reasoning."""
    return SUPERVISOR_REASONING_EFFORT


def _read_profile_text(path: Path, what: str) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        raise RuntimeError(f"Profile {what} unreadable: {path}: {exc}") from exc
    if not text.strip():
        raise RuntimeError(f"Profile {what} is empty: {path}")
    return text


def _validate_policy_document(doc, expected_policy_id: str) -> dict:
    """G4: strict mechanical validation of one declarative FV policy; fail closed."""
    if not isinstance(doc, dict):
        raise RuntimeError("Final Verification policy is missing or invalid")
    unknown = sorted(set(doc) - POLICY_REQUIRED_KEYS)
    missing = sorted(POLICY_REQUIRED_KEYS - set(doc))
    if unknown or missing:
        raise RuntimeError(f"policy schema mismatch (missing={missing}, unknown={unknown})")
    if doc.get("policy_schema_version") != POLICY_SCHEMA_VERSION:
        raise RuntimeError("policy_schema_version mismatch")
    policy_id = doc.get("policy_id")
    if not isinstance(policy_id, str) or not policy_id.strip():
        raise RuntimeError("policy_id must be a non-empty string")
    if expected_policy_id is not None and policy_id != expected_policy_id:
        raise RuntimeError(
            f"policy binding mismatch: policy_id {policy_id!r} != expected {expected_policy_id!r}")
    policy_version = doc.get("policy_version")
    if not isinstance(policy_version, int) or isinstance(policy_version, bool) or policy_version < 1:
        raise RuntimeError("policy_version must be a positive integer")
    claim_count = doc.get("claim_count")
    if not isinstance(claim_count, dict):
        raise RuntimeError("policy claim_count must be an object")
    cmin, cmax = claim_count.get("min"), claim_count.get("max")
    if not isinstance(cmin, int) or not isinstance(cmax, int) or cmin < 1 or cmin > cmax:
        raise RuntimeError("policy claim_count.min/max invalid")
    statuses = doc.get("allowed_result_statuses")
    if (not isinstance(statuses, list) or not statuses
            or not all(isinstance(s, str) and s.strip() for s in statuses)):
        raise RuntimeError("policy allowed_result_statuses must be a non-empty string list")
    impact = doc.get("impact_acceptance")
    if not isinstance(impact, dict):
        raise RuntimeError("policy impact_acceptance must be an object")
    for level in ("HIGH", "MEDIUM"):
        accepted = impact.get(level)
        if (not isinstance(accepted, list) or not accepted
                or not all(s in statuses for s in accepted)):
            raise RuntimeError(f"policy impact_acceptance.{level} must reference allowed statuses")
    unknown_mode = doc.get("unknown_claim_type")
    if unknown_mode not in POLICY_UNKNOWN_MODES:
        raise RuntimeError(f"policy unknown_claim_type must be one of {sorted(POLICY_UNKNOWN_MODES)}")
    claim_types = doc.get("claim_types")
    if not isinstance(claim_types, dict):
        raise RuntimeError("policy claim_types must be an object")
    for ctype, rules_def in claim_types.items():
        if not isinstance(ctype, str) or not ctype.strip():
            raise RuntimeError("policy claim_types contains an invalid type name")
        if not isinstance(rules_def, dict):
            raise RuntimeError(f"policy claim_types[{ctype}] must be an object")
        rules = rules_def.get("required_checks", [])
        if not isinstance(rules, list):
            raise RuntimeError(f"policy claim_types[{ctype}].required_checks must be a list")
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) - {"field", "op", "value", "message"}:
                raise RuntimeError(f"policy claim_types[{ctype}] has an invalid rule: {rule!r}")
            if not isinstance(rule.get("field"), str) or not rule["field"].strip():
                raise RuntimeError(f"policy claim_types[{ctype}] rule needs a field")
            if rule.get("op") not in POLICY_OPERATORS:
                raise RuntimeError(
                    f"policy claim_types[{ctype}] unknown operator {rule.get('op')!r}")
            if rule["op"] in {"EQUALS", "INTEGER_GTE"} and "value" not in rule:
                raise RuntimeError(f"policy claim_types[{ctype}] operator {rule['op']} needs a value")
            if rule["op"] == "INTEGER_GTE" and not isinstance(rule["value"], int):
                raise RuntimeError(f"policy claim_types[{ctype}] INTEGER_GTE value must be an integer")
            if "message" in rule and not isinstance(rule["message"], str):
                raise RuntimeError(f"policy claim_types[{ctype}] rule message must be a string")
    return doc


def load_policy_document(profile_dir: Path, expected_policy_id: str | None) -> dict:
    """G4: read + validate the profile FINAL_VERIFICATION_POLICY.json (containment-safe)."""
    policy_path = (profile_dir / "FINAL_VERIFICATION_POLICY.json").resolve()
    try:
        policy_path.relative_to(Path(profile_dir).resolve())
    except Exception:
        raise RuntimeError(f"policy path escapes the profile directory: {policy_path}")
    doc = read_json(policy_path)
    return _validate_policy_document(doc, expected_policy_id)


def _legacy_final_verification_policy() -> dict:
    """LEGACY V1.5 compatibility adapter (narrow, removable): commercial Cross-Border
    projects verify under the BUSINESS_RESEARCH policy data. No business strings live
    in the evaluator — this is the only place the legacy mapping exists."""
    return load_policy_document(PROFILES_DIR / "BUSINESS_RESEARCH", "BUSINESS_RESEARCH_FV_V1")


def _legacy_gate_subject(state: dict) -> bool:
    """LEGACY V1.5 gate subject predicate (isolated compatibility layer)."""
    return (bool(state.get("commercial_authorized"))
            and str(state.get("phase") or "").startswith("CROSS_BORDER_ECOMMERCE"))


def _resolve_fv_policy_for_state(state: dict) -> tuple[dict, str]:
    """G4: resolve the binding policy for a gated project. Profile route first; legacy
    V1.5 commercial projects map to the BUSINESS_RESEARCH policy; anything else with no
    resolvable policy fails closed."""
    profile = resolve_profile(state)
    if profile is not None:
        return profile["final_verification_policy"], "profile"
    if _legacy_gate_subject(state):
        return _legacy_final_verification_policy(), "legacy_v1_5"
    raise RuntimeError(
        "Final Verification policy cannot be resolved: project has no profile and is not "
        "a legacy V1.5 commercial run")


def load_profile(profile_id: str) -> dict:
    """G2: load and validate one static profile; fail closed on any inconsistency."""
    if profile_id not in SUPPORTED_PROFILES:
        raise RuntimeError(f"unsupported project profile: {profile_id!r}")
    profile_dir = (PROFILES_DIR / profile_id).resolve()
    try:
        profile_dir.relative_to(PROFILES_DIR.resolve())
    except Exception:
        raise RuntimeError(f"profile directory escapes the profiles root: {profile_id!r}")
    if not profile_dir.is_dir():
        raise RuntimeError(f"profile directory missing: {profile_dir}")
    profile = read_json(profile_dir / "PROFILE.json")
    if not isinstance(profile, dict):
        raise RuntimeError(f"profile PROFILE.json missing or invalid: {profile_dir}")
    unknown = sorted(set(profile) - PROFILE_REQUIRED_KEYS)
    missing = sorted(PROFILE_REQUIRED_KEYS - set(profile))
    if unknown or missing:
        raise RuntimeError(f"profile schema mismatch (missing={missing}, unknown={unknown})")
    if profile.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise RuntimeError("profile_schema_version mismatch")
    if profile.get("profile_id") != profile_id:
        raise RuntimeError("profile_id does not match its directory")
    for key in ("profile_version", "display_name", "final_verification_policy_id"):
        if not str(profile.get(key) or "").strip():
            raise RuntimeError(f"profile field {key} must be a non-empty string")
    guidance = {}
    for key, what in (("supervisor_guidance_file", "supervisor guidance"),
                      ("executor_guidance_file", "executor guidance")):
        name = profile.get(key)
        if not isinstance(name, str) or not name.strip():
            raise RuntimeError(f"profile field {key} must be a non-empty string")
        gpath = (profile_dir / name).resolve()
        try:
            gpath.relative_to(profile_dir)
        except Exception:
            raise RuntimeError(f"profile {what} path escapes the profile directory: {name!r}")
        guidance[key] = _read_profile_text(gpath, what)
    # G4: every profile binds exactly one Final Verification policy (data).
    policy = load_policy_document(profile_dir, profile["final_verification_policy_id"])
    return {
        "final_verification_policy": policy,
        "profile_id": profile_id,
        "display_name": profile["display_name"],
        "profile_version": profile["profile_version"],
        "final_verification_policy_id": profile["final_verification_policy_id"],
        "supervisor_guidance": guidance["supervisor_guidance_file"],
        "executor_guidance": guidance["executor_guidance_file"],
    }


def resolve_profile(state: dict | None) -> dict | None:
    """G2: None = legacy mode (no profile field) — production V1.5/G1 behavior preserved.
    An explicitly set profile must load and validate, or the run fails closed."""
    raw = state.get("profile") if isinstance(state, dict) else None
    if raw is None:
        return None
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("project_state.profile must be a non-empty profile id when set")
    if raw not in SUPPORTED_PROFILES:
        raise RuntimeError(f"unsupported project profile: {raw!r}")
    return load_profile(raw)


def render_profile_block(profile: dict | None) -> str:
    """G2: optional PROJECT PROFILE prompt block; empty string keeps legacy byte-equality."""
    if profile is None:
        return ""
    return (
        "\n\n=== PROJECT PROFILE ===\n"
        f"Profile: {profile['profile_id']} (v{profile['profile_version']})\n\n"
        "--- Supervisor guidance for this domain ---\n"
        f"{profile['supervisor_guidance'].strip()}\n\n"
        "--- Executor guidance (apply when writing task instructions, ACCEPTANCE_CRITERIA"
        " and FORBIDDEN_ACTIONS) ---\n"
        f"{profile['executor_guidance'].strip()}\n\n"
        "=== END PROJECT PROFILE ==="
    )


def load_active_project() -> dict | None:
    """G3: load control/ACTIVE_PROJECT.json. Missing file = legacy mode (None).
    Any present-but-invalid pointer fails closed; never a silent legacy fallback."""
    if not ACTIVE_PROJECT_FILE.exists():
        return None
    doc = read_json(ACTIVE_PROJECT_FILE)
    if not isinstance(doc, dict):
        raise RuntimeError(f"ACTIVE_PROJECT.json is missing or invalid: {ACTIVE_PROJECT_FILE}")
    required = {"schema_version", "project_id", "project_root"}
    unknown = sorted(set(doc) - required)
    missing = sorted(required - set(doc))
    if unknown or missing:
        raise RuntimeError(f"ACTIVE_PROJECT schema mismatch (missing={missing}, unknown={unknown})")
    if doc.get("schema_version") != 1:
        raise RuntimeError("ACTIVE_PROJECT schema_version must be 1")
    project_id = doc.get("project_id")
    project_root_raw = doc.get("project_root")
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise RuntimeError(f"ACTIVE_PROJECT.project_id is not a safe project id: {project_id!r}")
    if not isinstance(project_root_raw, str) or not project_root_raw.strip():
        raise RuntimeError("ACTIVE_PROJECT.project_root must be a relative 'projects/<id>' path")
    if Path(project_root_raw).is_absolute() or (":" in project_root_raw):
        raise RuntimeError(f"ACTIVE_PROJECT.project_root must not be absolute: {project_root_raw!r}")
    normalized = Path(project_root_raw).as_posix()
    if normalized != f"projects/{project_id}":
        raise RuntimeError(
            "ACTIVE_PROJECT.project_root must be exactly "
            f"'projects/{project_id}' (got {project_root_raw!r})")
    project_root = (ROOT / project_root_raw).resolve()
    try:
        project_root.relative_to((ROOT / "projects").resolve())
    except Exception:
        raise RuntimeError(
            f"ACTIVE_PROJECT.project_root escapes ROOT\\projects: {project_root_raw!r}")
    if not (project_root / "project_state.json").is_file():
        raise RuntimeError(f"active project has no project_state.json: {project_root}")
    return {"schema_version": 1, "project_id": project_id, "project_root": project_root}


def activate_project_scope() -> dict | None:
    """G3: rebind ONLY the project-scoped constants when an isolated project is active.

    Legacy mode rebinds nothing (root-level layout, production behavior). Runtime-owned
    paths — wire files, runtime state, lock, STOP/HUMAN_REVIEW, USER_ATTENTION,
    USER_STATUS, handoff archive, executor claims, logs, profiles — stay at ROOT in
    every mode, which is what keeps claim/receipt/watchdog/recovery semantics stable."""
    global ACTIVE_PROJECT, PROJECT_STATE, RESEARCH_STATE, REPORTS
    active = load_active_project()
    ACTIVE_PROJECT = active
    if active is not None:
        project_root = active["project_root"]
        PROJECT_STATE = project_root / "project_state.json"
        RESEARCH_STATE = project_root / "RESEARCH_STATE.md"
        REPORTS = project_root / "reports"
        log("Active project scope activated", project_id=active["project_id"],
            project_root=str(project_root))
    return active


def _active_project_root() -> Path:
    return ACTIVE_PROJECT["project_root"] if ACTIVE_PROJECT else ROOT


def _load_verified_human_decision_receipt(state: dict, meta: dict) -> dict:
    """Verify one pending/consumed lifecycle record against its immutable receipt."""
    if ACTIVE_PROJECT is None:
        raise RuntimeError("human-review resume receipts require an isolated active project")
    active_project_id = ACTIVE_PROJECT["project_id"]
    if state.get("project_id") != active_project_id:
        raise RuntimeError("active project and project_state.project_id mismatch")
    if meta.get("schema_version") != HUMAN_DECISION_RESUME_SCHEMA_VERSION:
        raise RuntimeError(
            f"human_review_resume.schema_version must be {HUMAN_DECISION_RESUME_SCHEMA_VERSION}"
        )
    if meta.get("project_id") != active_project_id:
        raise RuntimeError("human_review_resume.project_id does not match active project")
    if meta.get("transition") != "HUMAN_REVIEW_TO_SUPERVISOR_TURN":
        raise RuntimeError("human_review_resume transition is invalid")
    if meta.get("previous_status") != "HUMAN_REVIEW":
        raise RuntimeError("human_review_resume.previous_status must be HUMAN_REVIEW")
    _strict_timezone_timestamp(meta.get("resumed_at"), "human_review_resume.resumed_at")

    raw_path = meta.get("receipt_path")
    if not isinstance(raw_path, str) or not raw_path.strip() or Path(raw_path).is_absolute() or ":" in raw_path:
        raise RuntimeError("human_review_resume.receipt_path must be a relative project path")
    normalized = Path(raw_path).as_posix()
    if not normalized.startswith("human_decisions/archive/"):
        raise RuntimeError("human_review_resume.receipt_path must be under human_decisions/archive")
    project_root = _active_project_root().resolve()
    receipt_path = (project_root / raw_path).resolve()
    try:
        receipt_path.relative_to(project_root)
    except Exception as exc:
        raise RuntimeError("human_review_resume.receipt_path escapes the project root") from exc
    if not receipt_path.is_file():
        raise RuntimeError(f"archived human decision receipt is missing: {receipt_path}")
    if receipt_path.stat().st_size > 128 * 1024:
        raise RuntimeError("archived human decision receipt exceeds 128 KiB")

    file_hash = sha256(receipt_path)
    if not hmac.compare_digest(str(meta.get("receipt_file_sha256") or ""), file_hash):
        raise RuntimeError("archived human decision receipt file hash mismatch")
    receipt = read_json(receipt_path)
    validate_human_decision_receipt(receipt, expected_project_id=active_project_id)
    comparisons = {
        "receipt_id": receipt.get("receipt_id"),
        "receipt_sha256": receipt.get("receipt_sha256"),
        "previous_project_state_sha256": receipt.get("previous_project_state_sha256"),
    }
    for key, expected in comparisons.items():
        if meta.get(key) != expected:
            raise RuntimeError(f"human_review_resume.{key} does not match archived receipt")
    return receipt


def _decision_identity(entry: dict, history_index: int) -> dict:
    entry_hash = canonical_json_sha256(entry)
    return {
        "kind": "PROJECT_DECISION_HISTORY_ENTRY",
        "decision_history_index": history_index,
        "sha256": entry_hash,
        "message_id": entry.get("message_id"),
        "task_id": entry.get("task_id"),
        "stage_id": entry.get("stage_id"),
    }


def _validate_human_decision_status_pair(decision: str, resulting_status: str) -> None:
    if resulting_status == "WAITING_EXECUTOR":
        allowed = {"CONTINUE", "REDIRECT", "CHANGE_METHOD", "REVISE"}
    elif resulting_status == "HUMAN_REVIEW":
        allowed = {"HUMAN_REVIEW"}
    else:
        allowed = {"STOP"}
    if decision not in allowed:
        raise RuntimeError("Supervisor decision does not match resulting lifecycle state")


def _validate_consumed_human_decision_record(state: dict, record: dict) -> dict:
    missing = sorted(HUMAN_DECISION_CONSUMED_KEYS - set(record))
    unknown = sorted(set(record) - HUMAN_DECISION_CONSUMED_KEYS)
    if missing or unknown:
        raise RuntimeError(
            f"consumed human_review_resume schema mismatch (missing={missing}, unknown={unknown})"
        )
    if record.get("status") != "CONSUMED":
        raise RuntimeError("consumed Human Decision record must have status=CONSUMED")
    receipt = _load_verified_human_decision_receipt(state, record)
    _strict_timezone_timestamp(record.get("consumed_at"), "human_review_resume.consumed_at")
    decision = record.get("resulting_supervisor_decision")
    if decision not in HUMAN_DECISION_ALLOWED_DECISIONS:
        raise RuntimeError("consumed Human Decision has an invalid resulting Supervisor decision")
    resulting_status = record.get("resulting_lifecycle_state")
    if resulting_status not in HUMAN_DECISION_RESULTING_STATUSES:
        raise RuntimeError("consumed Human Decision has an invalid resulting lifecycle state")
    _validate_human_decision_status_pair(decision, resulting_status)
    claimed_decision_hash = record.get("decision_sha256")
    if not isinstance(claimed_decision_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", claimed_decision_hash):
        raise RuntimeError("consumed Human Decision decision_sha256 must be lowercase SHA-256 hex")

    identity = record.get("decision_identity")
    if not isinstance(identity, dict):
        raise RuntimeError("consumed Human Decision decision_identity must be an object")
    identity_missing = sorted(HUMAN_DECISION_DECISION_IDENTITY_KEYS - set(identity))
    identity_unknown = sorted(set(identity) - HUMAN_DECISION_DECISION_IDENTITY_KEYS)
    if identity_missing or identity_unknown:
        raise RuntimeError(
            f"Human Decision decision_identity schema mismatch "
            f"(missing={identity_missing}, unknown={identity_unknown})"
        )
    if identity.get("kind") != "PROJECT_DECISION_HISTORY_ENTRY":
        raise RuntimeError("Human Decision decision_identity.kind is invalid")
    history_index = identity.get("decision_history_index")
    history = state.get("decision_history")
    if (
        not isinstance(history, list)
        or isinstance(history_index, bool)
        or not isinstance(history_index, int)
        or history_index < 0
        or history_index >= len(history)
        or not isinstance(history[history_index], dict)
    ):
        raise RuntimeError("Human Decision decision_identity history index is invalid")
    entry = history[history_index]
    expected_identity = _decision_identity(entry, history_index)
    if identity != expected_identity:
        raise RuntimeError("Human Decision decision_identity does not match decision_history")
    if not hmac.compare_digest(claimed_decision_hash, expected_identity["sha256"]):
        raise RuntimeError("Human Decision decision_sha256 does not match decision_history")
    if entry.get("decision") != decision:
        raise RuntimeError("Human Decision resulting decision does not match decision_history")
    return receipt


def validate_human_decision_consumption_ledger(state: dict) -> list[dict]:
    """Validate immutable receipt bindings and exactly-once consumption identities."""
    ledger = state.get("human_decision_consumption_ledger")
    if ledger is None:
        return []
    if not isinstance(ledger, list):
        raise RuntimeError("human_decision_consumption_ledger must be an array")
    seen: set[tuple[str, str]] = set()
    for record in ledger:
        if not isinstance(record, dict):
            raise RuntimeError("human_decision_consumption_ledger entries must be objects")
        _validate_consumed_human_decision_record(state, record)
        identity = (str(record.get("receipt_id")), str(record.get("receipt_sha256")))
        if identity in seen:
            raise RuntimeError("duplicate Human Decision consumption ledger identity")
        seen.add(identity)
    return ledger


def load_verified_human_decision_for_supervisor(state: dict) -> dict | None:
    """Inject only a verified pending receipt; validate consumed history without injection."""
    ledger = validate_human_decision_consumption_ledger(state)
    meta = state.get("human_review_resume")
    if meta is None:
        return None
    if not isinstance(meta, dict):
        raise RuntimeError("project_state.human_review_resume must be an object")
    lifecycle = meta.get("status")
    if lifecycle == "CONSUMED":
        _validate_consumed_human_decision_record(state, meta)
        if not ledger or meta != ledger[-1]:
            raise RuntimeError("current consumed Human Decision must equal the latest ledger entry")
        return None
    if lifecycle != "PENDING_SUPERVISOR_REVIEW":
        raise RuntimeError("human_review_resume.status must be PENDING_SUPERVISOR_REVIEW or CONSUMED")

    missing = sorted(HUMAN_DECISION_RESUME_BASE_KEYS - set(meta))
    unknown = sorted(set(meta) - HUMAN_DECISION_RESUME_BASE_KEYS)
    if missing or unknown:
        raise RuntimeError(
            f"pending human_review_resume schema mismatch (missing={missing}, unknown={unknown})"
        )
    if state.get("status") != "SUPERVISOR_TURN":
        raise RuntimeError("pending Human Decision is only valid in SUPERVISOR_TURN")
    if state.get("current_task") is not None:
        raise RuntimeError("resumed SUPERVISOR_TURN must have current_task=null")
    if any(
        item.get("receipt_id") == meta.get("receipt_id")
        or item.get("receipt_sha256") == meta.get("receipt_sha256")
        for item in ledger
    ):
        raise RuntimeError("consumed Human Decision cannot return to pending")
    return _load_verified_human_decision_receipt(state, meta)


def human_decision_resume_event(state: dict) -> dict | None:
    receipt = load_verified_human_decision_for_supervisor(state)
    if receipt is None:
        return None
    return {
        "type": "HUMAN_DECISION_RESUME",
        "project_id": receipt["project_id"],
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "previous_status": receipt["previous_status"],
    }


def read_human_decision_supervisor_result(path: Path) -> dict:
    """Read the read-only Supervisor turn's sole structured output, strictly."""
    if not path.is_file():
        raise RuntimeError("Human Decision Supervisor result is missing")
    if path.stat().st_size > 512 * 1024:
        raise RuntimeError("Human Decision Supervisor result exceeds 512 KiB")
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        raise RuntimeError("Human Decision Supervisor result is malformed JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError("Human Decision Supervisor result must be a JSON object")
    missing = sorted(HUMAN_DECISION_SUPERVISOR_RESULT_KEYS - set(result))
    unknown = sorted(set(result) - HUMAN_DECISION_SUPERVISOR_RESULT_KEYS)
    if missing or unknown:
        raise RuntimeError(
            f"Human Decision Supervisor result schema mismatch "
            f"(missing={missing}, unknown={unknown})"
        )
    return result


def _normalized_human_supervisor_decision(value: dict) -> dict:
    if not isinstance(value, dict):
        raise RuntimeError("supervisor_decision must be a JSON object")
    missing = sorted(HUMAN_DECISION_SUPERVISOR_DECISION_KEYS - set(value))
    unknown = sorted(set(value) - HUMAN_DECISION_SUPERVISOR_DECISION_KEYS)
    if missing or unknown:
        raise RuntimeError(
            f"supervisor_decision schema mismatch (missing={missing}, unknown={unknown})"
        )
    decision = value.get("decision")
    if decision not in HUMAN_DECISION_ALLOWED_DECISIONS:
        raise RuntimeError("supervisor_decision.decision is invalid")
    _strict_timezone_timestamp(value.get("at"), "supervisor_decision.at")
    reason = value.get("reason")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 12000:
        raise RuntimeError("supervisor_decision.reason must be a non-empty bounded string")
    scope = value.get("scope")
    if scope is not None and (
        not isinstance(scope, str) or not scope.strip() or len(scope) > 2048
    ):
        raise RuntimeError("supervisor_decision.scope must be null or a non-empty bounded string")
    message_id = value.get("message_id")
    if message_id is not None and (
        isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 0
    ):
        raise RuntimeError("supervisor_decision.message_id must be null or a non-negative integer")
    for key in ("task_id", "stage_id"):
        item = value.get(key)
        if item is not None and (
            not isinstance(item, str) or not item.strip() or len(item) > 512
        ):
            raise RuntimeError(f"supervisor_decision.{key} must be null or a non-empty bounded string")
    entry = {
        "at": value["at"],
        "decision": decision,
        "reason": reason,
    }
    for key in ("scope", "message_id", "task_id", "stage_id"):
        if value.get(key) is not None:
            entry[key] = value[key]
    return entry


def _require_owned_orchestrator_lock() -> None:
    lock = read_json(LOCK_FILE)
    if not isinstance(lock, dict) or lock.get("pid") != os.getpid():
        raise RuntimeError("Human Decision consumption requires the owned Orchestrator lock")


def _render_executor_task(task: dict) -> str:
    return (
        f"MESSAGE_ID: {task['MESSAGE_ID']}\n"
        f"TASK_ID: {task['TASK_ID']}\n"
        f"STAGE_ID: {task['STAGE_ID']}\n\n"
        "```json\n"
        + json.dumps(task, ensure_ascii=False, indent=2)
        + "\n```\n"
    )


def commit_human_decision_supervisor_result(
    runtime: dict,
    state_before: dict,
    state_sha_before: str,
    result: dict,
) -> dict:
    """Atomically commit one validated decision and consume its Human Decision.

    If a task is selected, its wire file is published first but remains unclaimable
    until the existing post-commit dispatch authorization step. The single atomic
    PROJECT_STATE replacement below is the decision/consumption commit point.
    """
    _require_owned_orchestrator_lock()
    receipt = load_verified_human_decision_for_supervisor(state_before)
    if receipt is None:
        raise RuntimeError("Human Decision consumption requires one pending receipt")
    if not isinstance(state_sha_before, str) or sha256(PROJECT_STATE) != state_sha_before:
        raise RuntimeError("project_state changed before Human Decision result validation")
    if not isinstance(result, dict):
        raise RuntimeError("Human Decision Supervisor result must be a JSON object")
    missing_result = sorted(HUMAN_DECISION_SUPERVISOR_RESULT_KEYS - set(result))
    unknown_result = sorted(set(result) - HUMAN_DECISION_SUPERVISOR_RESULT_KEYS)
    if missing_result or unknown_result:
        raise RuntimeError(
            f"Human Decision Supervisor result schema mismatch "
            f"(missing={missing_result}, unknown={unknown_result})"
        )

    if result.get("schema_version") != HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION:
        raise RuntimeError("Human Decision Supervisor result has an unsupported schema_version")
    bindings = {
        "project_id": receipt["project_id"],
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "previous_project_state_sha256": state_sha_before,
    }
    for key, expected in bindings.items():
        if result.get(key) != expected:
            raise RuntimeError(f"Human Decision Supervisor result {key} binding mismatch")

    decision_entry = _normalized_human_supervisor_decision(result.get("supervisor_decision"))
    decision = decision_entry["decision"]
    resulting_status = result.get("resulting_lifecycle_state")
    if resulting_status not in HUMAN_DECISION_RESULTING_STATUSES:
        raise RuntimeError("Human Decision Supervisor resulting lifecycle state is invalid")

    state_patch = result.get("project_state_patch")
    if not isinstance(state_patch, dict):
        raise RuntimeError("project_state_patch must be a JSON object")
    forbidden = sorted(HUMAN_DECISION_RUNTIME_OWNED_STATE_KEYS & set(state_patch))
    if forbidden:
        raise RuntimeError(f"project_state_patch changes Runtime-owned keys: {forbidden}")
    required_patch = {"status", "current_task", "next_message_id"}
    missing_patch = sorted(required_patch - set(state_patch))
    if missing_patch:
        raise RuntimeError(f"project_state_patch is missing required keys: {missing_patch}")
    if state_patch.get("status") != resulting_status:
        raise RuntimeError("project_state_patch.status does not match resulting_lifecycle_state")

    history = state_before.get("decision_history")
    if not isinstance(history, list):
        raise RuntimeError("project_state.decision_history must be an array")
    ledger = validate_human_decision_consumption_ledger(state_before)
    candidate = json.loads(json.dumps(state_before, ensure_ascii=False))
    candidate.update(json.loads(json.dumps(state_patch, ensure_ascii=False)))
    candidate_history = json.loads(json.dumps(history, ensure_ascii=False))
    candidate_history.append(decision_entry)
    candidate["decision_history"] = candidate_history
    candidate["last_supervisor_decision"] = {
        "decision": decision,
        "scope": decision_entry.get("scope") or resulting_status,
        "reason": decision_entry["reason"],
    }

    executor_task = result.get("executor_task")
    _validate_human_decision_status_pair(decision, resulting_status)
    if resulting_status == "WAITING_EXECUTOR":
        if not isinstance(executor_task, dict):
            raise RuntimeError("WAITING_EXECUTOR requires one Executor task")
        current_task = candidate.get("current_task")
        if not isinstance(current_task, dict) or not task_identity_matches(executor_task, current_task):
            raise RuntimeError("Executor task identity does not match resulting current_task")
        previous_next = state_before.get("next_message_id")
        if isinstance(previous_next, bool) or not isinstance(previous_next, int):
            raise RuntimeError("pending Human Decision requires an integer next_message_id")
        if executor_task.get("MESSAGE_ID") != previous_next:
            raise RuntimeError("Executor task must use the exact pending next_message_id")
        if candidate.get("next_message_id") != previous_next + 1:
            raise RuntimeError("resulting next_message_id must advance exactly once")
        expected_task_identity = {
            "message_id": executor_task.get("MESSAGE_ID"),
            "task_id": executor_task.get("TASK_ID"),
            "stage_id": executor_task.get("STAGE_ID"),
        }
        for key, expected in expected_task_identity.items():
            if decision_entry.get(key) != expected:
                raise RuntimeError(f"Supervisor decision {key} does not bind the Executor task")
        validate_dispatch_payload(runtime, candidate, executor_task, allow_same_identity=False)
    else:
        if executor_task is not None:
            raise RuntimeError("terminal Supervisor decision must not return an Executor task")
        if candidate.get("current_task") is not None:
            raise RuntimeError("terminal Supervisor decision must clear current_task")
        if candidate.get("next_message_id") != state_before.get("next_message_id"):
            raise RuntimeError("terminal Supervisor decision must not allocate a MESSAGE_ID")
        if resulting_status == "COMPLETE":
            # FV-IDENTITY-BINDING-V1: bounded ledger-backed recovery before rejecting,
            # identical to enforce_terminal_verification_gate.
            reconcile_final_verification_binding(runtime, candidate)
            allowed, reason = final_verification_terminal_check(runtime, candidate)
            if not allowed:
                raise RuntimeError(f"COMPLETE rejected by Final Verification Gate: {reason}")

    history_index = len(candidate_history) - 1
    identity = _decision_identity(decision_entry, history_index)
    consumed_at = stamp()
    pending = state_before["human_review_resume"]
    consumed = {
        **json.loads(json.dumps(pending, ensure_ascii=False)),
        "status": "CONSUMED",
        "consumed_at": consumed_at,
        "resulting_supervisor_decision": decision,
        "resulting_lifecycle_state": resulting_status,
        "decision_sha256": identity["sha256"],
        "decision_identity": identity,
    }
    candidate["human_review_resume"] = consumed
    candidate["human_decision_consumption_ledger"] = [
        *json.loads(json.dumps(ledger, ensure_ascii=False)),
        json.loads(json.dumps(consumed, ensure_ascii=False)),
    ]
    candidate["updated_at"] = consumed_at

    # Existing at-most-once protocol: publish a selected task before state/authorization.
    # Until register_dispatched_task succeeds, executor_claim.py rejects this wire hash.
    if executor_task is not None:
        atomic_write(TO_ZCODE, _render_executor_task(executor_task))
        _, published = read_json_fence_snapshot(TO_ZCODE)
        if published != executor_task:
            raise RuntimeError("published Executor task changed before Human Decision commit")

    # The Orchestrator lock excludes resume/second Supervisor writers; this final hash
    # check also fails closed on uncoordinated stale-state mutation.
    if sha256(PROJECT_STATE) != state_sha_before:
        raise RuntimeError("project_state changed before Human Decision durable commit")
    atomic_json(PROJECT_STATE, candidate)
    committed = read_project_state()
    if committed != candidate:
        raise RuntimeError("Human Decision durable commit read-back mismatch")
    if load_verified_human_decision_for_supervisor(committed) is not None:
        raise RuntimeError("consumed Human Decision remained injectable after commit")
    return committed


def render_project_scope_block(active: dict | None) -> str:
    """G3: isolated-mode runtime scope metadata for the Supervisor; empty for legacy."""
    if active is None:
        return ""
    root_text = str(active["project_root"])
    return (
        "\n\n=== PROJECT RUNTIME SCOPE ===\n"
        f"PROJECT_ID: {active['project_id']}\n"
        f"PROJECT_ROOT: {root_text}\n"
        "All project-relative task paths (inputs, outputs, evidence, deliverables) resolve "
        "under PROJECT_ROOT.\n"
        "PROJECT STATE: project_state.json (in PROJECT_ROOT)\n"
        "PROJECT MEMORY: RESEARCH_STATE.md (in PROJECT_ROOT)\n"
        "PROJECT REPORTS: reports\\ (in PROJECT_ROOT)\n"
        "=== END PROJECT RUNTIME SCOPE ==="
    )


def resolve_goal_path(state: dict | None) -> Path:
    """G1-A: resolve the active project goal file.

    project_state.goal_file absent or null  -> legacy fallback (see COMMERCIAL_GOAL constant)
    (existing Cross-Border projects keep their exact behavior without migration).
    goal_file explicitly set but invalid (non-string, empty, escaping the project root,
    or nonexistent) -> fail closed with RuntimeError; never a silent fallback.
    """
    raw = state.get("goal_file") if isinstance(state, dict) else None
    project_root = _active_project_root()  # G3: isolated mode resolves against the project
    if raw is None:
        if ACTIVE_PROJECT is not None:
            required = project_root / "PROJECT_GOAL.md"
            if not required.is_file():
                raise RuntimeError(
                    "isolated project requires PROJECT_GOAL.md at the project root "
                    f"(missing: {required})")
            return required
        return COMMERCIAL_GOAL
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("project_state.goal_file must be a non-empty path string when set")
    path = Path(raw)
    if not path.is_absolute():
        path = project_root / path
    try:
        resolved = path.resolve()
        resolved.relative_to(Path(project_root).resolve())
    except Exception:
        raise RuntimeError(f"project_state.goal_file escapes the project root: {raw!r}")
    if not resolved.is_file():
        raise RuntimeError(f"project_state.goal_file does not exist: {raw!r}")
    return resolved


def build_codex_prompt(
    reason: str,
    event: dict | None = None,
    state: dict | None = None,
    *,
    state_sha256: str | None = None,
) -> str:
    """Build a compact Supervisor turn with the small authoritative files injected.

    Python is still only transport/scheduling: it does not summarize, rank, or interpret the
    commercial content. Injecting verbatim compressed state avoids repetitive shell reads and
    reduces Windows sandbox spawn failures and token overhead.
    """
    event_text = json.dumps(event or {}, ensure_ascii=False)
    now = stamp()
    dispatch_nonce_seed = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    state_text = safe_read_text(PROJECT_STATE, 18000)
    memory_text = safe_read_text(RESEARCH_STATE, 18000)
    rules_text = safe_read_text(SUPERVISOR_RULES, 14000)
    # G1-A: goal file resolved from project_state.goal_file (legacy fallback when absent).
    goal_state = state if state is not None else read_project_state()
    goal_path = resolve_goal_path(goal_state)
    profile_block = render_profile_block(resolve_profile(goal_state))  # G2
    scope_block = render_project_scope_block(ACTIVE_PROJECT)  # G3
    human_decision = load_verified_human_decision_for_supervisor(goal_state)
    human_decision_block = ""
    if human_decision is not None:
        bound_state_sha = state_sha256 or sha256(PROJECT_STATE)
        human_decision_text = json.dumps(human_decision, ensure_ascii=False, indent=2)
        human_decision_block = (
            "\n\n=== VERIFIED HUMAN DECISION RECEIPT ===\n"
            f"{human_decision_text}\n"
            "=== END VERIFIED HUMAN DECISION RECEIPT ===\n"
            "The Runtime mechanically verified this immutable human-originated receipt and "
            "its stale-state binding. It authorizes only a Supervisor review turn. It is not "
            "an Executor dispatch, allocates no MESSAGE_ID, and does not choose a research stage.\n\n"
            "=== HUMAN DECISION TRANSACTION OUTPUT CONTRACT ===\n"
            "This specific turn runs in a read-only sandbox. Do not create, edit, delete, or "
            "rename any Runtime, project, research-memory, report, or wire file. Reading the "
            "receipt and planning are not consumption. Return exactly one JSON object and no "
            "Markdown or prose. The Orchestrator alone validates and durably commits it.\n"
            f"schema_version: {HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION}\n"
            f"project_id: {human_decision['project_id']}\n"
            f"receipt_id: {human_decision['receipt_id']}\n"
            f"receipt_sha256: {human_decision['receipt_sha256']}\n"
            f"previous_project_state_sha256: {bound_state_sha}\n"
            "Required top-level keys are exactly: schema_version, project_id, receipt_id, "
            "receipt_sha256, previous_project_state_sha256, supervisor_decision, "
            "resulting_lifecycle_state, project_state_patch, executor_task.\n"
            "supervisor_decision must contain exactly: decision, at, reason, scope, "
            "message_id, task_id, stage_id. Use null for optional identities. decision must be "
            "CONTINUE, REDIRECT, CHANGE_METHOD, REVISE, STOP, or HUMAN_REVIEW.\n"
            "project_state_patch is a top-level merge patch and must include status, "
            "current_task, and next_message_id. Never include Runtime-owned identity/history "
            "fields: schema_version, project_id, project_type, profile, created_at, started_at, "
            "goal_file, updated_at, last_supervisor_decision, decision_history, "
            "human_review_resume, or human_decision_consumption_ledger.\n"
            "For WAITING_EXECUTOR, executor_task must be the complete v2 task object, "
            "current_task must bind the same identity, and the decision identity must bind the "
            "same message_id/task_id/stage_id. For a terminal/HUMAN_REVIEW decision, "
            "executor_task must be null, current_task must be null, and no MESSAGE_ID may be "
            "allocated.\n"
            "=== END HUMAN DECISION TRANSACTION OUTPUT CONTRACT ==="
        )
    goal_text = safe_read_text(goal_path, 12000) if goal_path.exists() else "[NO PROJECT GOAL FILE]"
    brief_text = "[NO EXECUTOR BRIEF FOR THIS TURN]"
    if reason in {"EXECUTOR_RESULT_READY", "MALFORMED_EXECUTOR_RECEIPT", "MALFORMED_EXECUTOR_SIGNAL"}:
        # COMPLETION-SEAL-V1: the Supervisor must review the committed receipt, not
        # whatever the mutable root compatibility file happens to contain.
        committed_path = (event or {}).get("committed_receipt_path") if isinstance(event, dict) else None
        entry = None
        if committed_path:
            entry = _completion_helper().load_entry_file(Path(committed_path))
        if entry is not None:
            brief_text = _completion_helper().render_brief_text(entry["RECEIPT"])
        elif SUPERVISOR_BRIEF.exists():
            brief_text = safe_read_text(SUPERVISOR_BRIEF, 22000)
    # G5A.5.2: scope-aware stage-dispatch and Final Verification instructions.
    # Legacy mode keeps the historical wording byte-for-byte; isolated mode uses the
    # injected PROJECT RUNTIME SCOPE and the active Profile's bound FV policy.
    if human_decision is not None:
        stage_instructions = (
            "Do not write project_state.json, RESEARCH_STATE.md, TO_ZCODE.md, reports, or any "
            "other file during this transaction turn. Express the complete bounded Supervisor "
            "decision and any selected Executor task only through the required JSON result."
        )
        fv_instructions = (
            "The Orchestrator will apply the active Profile's Final Verification gate to the "
            "returned candidate before any durable lifecycle commit."
        )
    elif ACTIVE_PROJECT is not None:
        stage_instructions = (
            "If another Executor stage is warranted: update the injected PROJECT STATE "
            "(project_state.json) and PROJECT MEMORY (RESEARCH_STATE.md) at the PROJECT ROOT "
            "named in the PROJECT RUNTIME SCOPE, then atomically publish the root "
            "TO_ZCODE.md using the v2 wire contract. You may use the provided "
            "TURN_TIME_UTC / DISPATCH_NONCE_SEED as mechanical timestamp/nonce material. "
            "EXECUTOR_PROTOCOL must be a flat JSON array of instruction strings."
        )
        fv_instructions = (
            "If final_verification.required=true, COMPLETE is forbidden until the active "
            "Profile's bound Final Verification policy has passed and FINAL_ACCEPTANCE is "
            "complete. Use the active Profile policy and its bound policy_id/policy_version: "
            "select 3-8 decision-critical claims within the Profile taxonomy, dispatch ONE "
            "bounded TASK_KIND=FINAL_VERIFICATION stage. Put POLICY_ID, POLICY_VERSION, "
            "CLAIMS_HASH, CLAIM_COUNT, and CRITICAL_CLAIMS inside the nested "
            "FINAL_VERIFICATION_GATE object (never at task top level). "
            "CRITICAL_CLAIMS element schema is mechanically enforced: EVERY element must be "
            "a JSON object containing EXACTLY these six lowercase keys — no uppercase "
            "variants, no aliases, no missing keys: "
            '{"claim_id": "C1", "claim": "Decision-critical factual claim.", '
            '"claim_type": "IP", "decision_impact": "HIGH", '
            '"evidence_pointers": ["evidence/example.txt"], '
            '"verification_standard": "Adversarially verify against the cited evidence."}. '
            "decision_impact must be HIGH or MEDIUM; evidence_pointers must be a JSON list "
            "of strings. CLAIMS_HASH is the canonical SHA-256 of the exact claim list: "
            "hash the JSON produced by json.dumps(claims, ensure_ascii=False, sort_keys=True, "
            "separators=(',', ':')). The same claim list and hash must also be stored in "
            "project_state.final_verification. A dispatch whose claims fail this schema is "
            "rejected, quarantined, and costs a bounded repair turn. On FAIL/INCONCLUSIVE "
            "apply only a narrow REVISE of the smallest affected claim/evidence segment "
            "before reverifying."
        )
    else:
        stage_instructions = (
            "If another Executor stage is warranted: update control/project_state.json and "
            "RESEARCH_STATE.md, then atomically publish root TO_ZCODE.md using the v2 wire "
            "contract. You may use the provided TURN_TIME_UTC / DISPATCH_NONCE_SEED as "
            "mechanical timestamp/nonce material."
        )
        fv_instructions = (
            "For a commercial run protected by Production V1.5 Final Verification Gate, "
            "COMPLETE is forbidden until the exact last accepted final-verification receipt "
            "has mechanically passed. When research is otherwise ready to stop, identify "
            "3-8 decision-critical claims, set final_verification=PENDING, and dispatch one "
            "bounded TASK_KIND=FINAL_VERIFICATION stage instead of completing. "
            "CRITICAL_CLAIMS element schema is mechanically enforced: EVERY element must be "
            "a JSON object containing EXACTLY these six lowercase keys — no uppercase "
            "variants, no aliases, no missing keys: "
            '{"claim_id": "C1", "claim": "Decision-critical factual claim.", '
            '"claim_type": "IP", "decision_impact": "HIGH", '
            '"evidence_pointers": ["evidence/example.txt"], '
            '"verification_standard": "Adversarially verify against the cited evidence."}. '
            "decision_impact must be HIGH or MEDIUM; evidence_pointers must be a JSON list "
            "of strings. CLAIMS_HASH is the canonical SHA-256 of the exact claim list "
            "(json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(',', ':'))), "
            "and the same claim list and hash must be stored in "
            "project_state.final_verification. After a PASS receipt, perform "
            "FINAL_ACCEPTANCE; only then may you set final_verification.status=PASS and "
            "COMPLETE. If the gate fails/inconclusive, REVISE only the smallest affected "
            "claim/evidence segment and reverify."
        )

    terminal_instructions = (
        "For this Human Decision transaction, do not write or update a report; identify any "
        "needed later work in the structured decision."
        if human_decision is not None
        else "If terminal and the gate is satisfied (or this is a grandfathered legacy run), "
        "write/update the appropriate high-level report and issue no task. Then exit."
    )

    # FIX-700102: when a published dispatch was mechanically rejected, the Supervisor
    # repair turn is told the precise validation error and the bounded-repair contract.
    repair_block = ""
    repair = goal_state.get("dispatch_repair")
    if isinstance(repair, dict):
        repair_identity = repair.get("rejected_identity") or {}
        repair_block = (
            "\n\n=== MECHANICAL DISPATCH REJECTION (bounded repair turn) ===\n"
            "Your previous Executor dispatch was published but REJECTED by mechanical "
            "Orchestrator validation. It was quarantined out of the inbox; no Executor "
            "executed it, no MESSAGE_ID was consumed, and nothing was authorized.\n"
            f"Validation error: {repair.get('error')}\n"
            f"Rejected dispatch identity: {json.dumps(repair_identity, ensure_ascii=False)}\n"
            f"Repair attempt: {repair.get('repair_attempt')} of "
            f"{repair.get('max_repair_attempts')} — a further rejected dispatch on this "
            "chain is terminal and stops the Runtime.\n"
            "Fix ONLY the validation defect named above: correct the exact schema/structure "
            "it lists (for FINAL_VERIFICATION critical claims see the required six lowercase "
            "keys in these instructions), keep project_state.json and TO_ZCODE.md mutually "
            "consistent, and republish root TO_ZCODE.md atomically with status "
            "WAITING_EXECUTOR. You may keep the rejected MESSAGE_ID/NONCE or allocate a "
            "fresh MESSAGE_ID; bind current_task and next_message_id accordingly. Do not "
            "change PROJECT_GOAL, prior evidence, the meaning of the claim set beyond the "
            "required schema, or the verification standard. Do not delete or rewrite "
            "history or audit artifacts.\n"
            "=== END MECHANICAL DISPATCH REJECTION ==="
        )

    return f"""
You are the Codex / GPT-5.6 Sol SUPERVISOR for this unattended dual-Agent project.
Complete exactly one Supervisor decision/dispatch turn, then exit. Do not chat with the user.

TURN_REASON: {reason}
MECHANICAL_EVENT: {event_text}
TURN_TIME_UTC: {now}
DISPATCH_NONCE_SEED: {dispatch_nonce_seed}

The orchestrator has mechanically injected the authoritative small files below verbatim.
Do NOT reread them with shell commands unless you have a concrete reason. Use tools primarily
to update state/publish a task or to inspect one precise evidence artifact when truly needed.

=== SUPERVISOR RUNTIME CONTRACT ===
{rules_text}
=== END RUNTIME CONTRACT ===

=== PROJECT STATE ===
{state_text}
=== END PROJECT STATE ===

=== COMPRESSED RESEARCH STATE ===
{memory_text}
=== END RESEARCH STATE ===

=== PROJECT GOAL ===
{goal_text}
=== END PROJECT GOAL ==={profile_block}{scope_block}{human_decision_block}

=== CURRENT EXECUTOR BRIEF ===
{brief_text}
=== END EXECUTOR BRIEF ===

Operate only inside {ROOT} (the active Runtime Root). You are the Supervisor, not the Executor.
Do not use browser/GUI/Computer Use, mouse/keyboard automation, ZCode CLI, or polling.
Do not execute GLM's stage yourself. Do not perform bulk web/data work.

{stage_instructions}

{fv_instructions}
{repair_block}

{terminal_instructions}
""".strip() + "\n"

def find_codex() -> str:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex CLI not found on PATH")
    return codex


def invoke_codex(runtime: dict, reason: str, event: dict | None = None) -> None:
    codex = find_codex()
    state_before = read_project_state()
    human_decision_turn = reason == "HUMAN_DECISION_RESUME"
    try:
        state_sha_before = sha256(PROJECT_STATE)
    except Exception:
        state_sha_before = None
    if human_decision_turn and state_sha_before is None:
        raise RuntimeError("Human Decision Supervisor turn requires a stable project-state hash")
    prompt = build_codex_prompt(
        reason,
        event,
        state_before,
        state_sha256=state_sha_before,
    )  # G1-A: goal_file aware
    started = time.monotonic()
    log("Starting Codex supervisor turn", reason=reason)
    effort = supervisor_effort(state_before, reason)
    log("Codex supervisor profile fixed", model=SUPERVISOR_MODEL, effort=effort, phase=state_before.get("phase"), reason=reason)

    cmd = [
        codex,
        "exec",
        "-m",
        SUPERVISOR_MODEL,
        "-C",
        str(ROOT),
        "--skip-git-repo-check",
    ]
    if human_decision_turn:
        result_dir = LOGS / "human_decision_supervisor_results"
        result_dir.mkdir(parents=True, exist_ok=True)
        receipt_id = str((event or {}).get("receipt_id") or "unbound")
        output_path = result_dir / f"{receipt_id}-{uuid.uuid4().hex}.json"
        # This turn has one output channel and no workspace-write capability. Python
        # performs the validated state/task publication after the model exits.
        cmd.extend(["--sandbox", "read-only", "--ephemeral"])
    else:
        output_path = CODEX_LAST_OUTPUT
        cmd.append("--approve-for-me")
    cmd.extend([
        "-c",
        f"model_reasoning_effort={effort}",
        "-c",
        "model_verbosity=low",
        "-o",
        str(output_path),
        "-",
    ])
    try:
        result = subprocess.run(
            cmd,
            cwd=str(ROOT),
            input=prompt.encode("utf-8"),
            timeout=CODEX_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex supervisor turn timed out after {CODEX_TIMEOUT_SECONDS}s") from exc

    elapsed = round(time.monotonic() - started, 2)
    runtime["codex_invocations"] = int(runtime.get("codex_invocations", 0)) + 1
    runtime["last_codex_run_at"] = stamp()
    runtime["last_codex_reason"] = reason
    save_runtime(runtime)

    if result.returncode != 0:
        raise RuntimeError(f"Codex exited with code {result.returncode}")

    if human_decision_turn:
        structured_result = read_human_decision_supervisor_result(output_path)
        state = commit_human_decision_supervisor_result(
            runtime,
            state_before,
            state_sha_before,
            structured_result,
        )
    else:
        state = read_project_state()
    log("Codex supervisor turn finished", reason=reason, elapsed_seconds=elapsed, project_status=state.get("status"))

    if state.get("status") == "SUPERVISOR_TURN":
        runtime["consecutive_codex_without_executor"] = int(runtime.get("consecutive_codex_without_executor", 0)) + 1
        save_runtime(runtime)
        if runtime["consecutive_codex_without_executor"] >= 2:
            raise RuntimeError("Codex returned SUPERVISOR_TURN twice without dispatching an Executor task or terminating; refusing a token-burning loop")
    else:
        runtime["consecutive_codex_without_executor"] = 0
        save_runtime(runtime)

    if state.get("status") == "WAITING_EXECUTOR":
        try:
            task = register_dispatched_task(runtime, state, allow_same_identity=False)
        except RuntimeError as exc:
            if "same task identity" in str(exc):
                try:
                    state_unchanged = state_sha_before is not None and sha256(PROJECT_STATE) == state_sha_before
                except Exception:
                    state_unchanged = False
                if not state_unchanged:
                    raise  # deliberate same-identity re-issue: real contract violation
                # FIX-F05: a no-op turn left state untouched; tolerate once and let the
                # watchdog re-engage instead of a terminal error. A dedicated counter is
                # required because invoke_codex resets consecutive_codex_without_executor
                # to 0 on every non-SUPERVISOR_TURN before this point, which would make
                # the two-strike escalation unreachable.
                runtime["consecutive_noop_codex_turns"] = int(runtime.get("consecutive_noop_codex_turns", 0) + 1)
                save_runtime(runtime)
                if runtime["consecutive_noop_codex_turns"] >= 2:
                    raise
                log(
                    "Codex turn changed nothing; tolerated once (watchdog remains armed)",
                    reason=reason,
                    count=runtime["consecutive_noop_codex_turns"],
                )
                return
            if human_decision_turn:
                # A failure after the durable Human Decision commit stays terminal: that
                # path owns its own strict fail-closed contract and the receipt is
                # already consumed; FIX-700102 does not relax it.
                raise
            # FIX-700102: a Supervisor-owned dispatch that fails mechanical validation is
            # recoverable, not fatal on first occurrence. Quarantine the candidate, arm
            # exactly one bounded Supervisor repair turn, and keep the validator strict.
            state = handle_dispatch_registration_failure(runtime, state, exc)
            return
        if runtime.get("consecutive_noop_codex_turns"):
            runtime["consecutive_noop_codex_turns"] = 0
            save_runtime(runtime)
        log(
            "Fresh Executor task published",
            message_id=task["MESSAGE_ID"],
            task_id=task["TASK_ID"],
            stage_id=task["STAGE_ID"],
            attempt=task["ATTEMPT"],
        )


def _mark_completion_consumed(
    completion,
    entry: dict,
    runtime: dict,
    *,
    aligned: bool,
    archive: str | None = None,
    final_verification_result: dict | None = None,
) -> None:
    """COMPLETION_COMMITTED -> COMPLETION_CONSUMED plus derived-pointer bookkeeping.

    The ledger transition is written FIRST: it is the authoritative consume proof.
    The runtime pointer fields are derived state; a crash between the two writes is
    repaired at startup by reconcile_completion_ledger (pointer recovery from the
    CONSUMED entry), so the receipt can never be consumed twice nor lost.
    """
    entry["STATUS"] = completion.STATUS_CONSUMED
    entry["CONSUMED_AT"] = completion.now_iso()
    entry["CONSUMED_ARCHIVE"] = archive
    completion.save_entry(ROOT, entry)
    completion.append_audit(ROOT, {
        "at": completion.now_iso(),
        "event": "COMPLETION_CONSUMED",
        "actor": "orchestrator",
        "commit_id": entry["COMMIT_ID"],
        "MESSAGE_ID": entry["MESSAGE_ID"],
        "NONCE": entry["NONCE"],
        "aligned_after_crash": aligned,
        "archive": archive,
    })
    runtime["last_consumed_message_id"] = int(entry["MESSAGE_ID"])
    runtime["last_consumed_nonce"] = entry["NONCE"]
    runtime["last_consumed_brief_sha256"] = entry["BRIEF_SHA256"]
    runtime["executor_receipts_consumed"] = int(runtime.get("executor_receipts_consumed", 0)) + 1
    if aligned:
        save_runtime(runtime)
        completion.publish_compatibility_artifacts(ROOT, entry, include_done=False)
        ZCODE_DONE.unlink(missing_ok=True)
        log(
            "Completion aligned to already-advanced bookkeeping (no second Supervisor event)",
            message_id=entry["MESSAGE_ID"],
            commit_id=entry["COMMIT_ID"],
        )
        return
    runtime["consecutive_codex_without_executor"] = 0
    if final_verification_result is not None:
        runtime["last_final_verification_message_id"] = entry["MESSAGE_ID"]
        runtime["last_final_verification_receipt_sha256"] = entry["BRIEF_SHA256"]
        runtime["last_final_verification_claims_hash"] = final_verification_result.get("claims_hash")
        runtime["last_final_verification_overall_status"] = final_verification_result.get("overall_status")
        runtime["last_final_verification_mechanical_pass"] = bool(final_verification_result.get("mechanical_pass"))
        ledger = [
            item for item in (runtime.get("final_verification_receipt_ledger") or [])
            if isinstance(item, dict)
        ]
        ledger.append({
            "message_id": entry["MESSAGE_ID"],
            "nonce": entry["NONCE"],
            "receipt_sha256": entry["BRIEF_SHA256"],
            "claims_hash": final_verification_result.get("claims_hash"),
            "overall_status": final_verification_result.get("overall_status"),
            "mechanical_pass": bool(final_verification_result.get("mechanical_pass")),
            "consumed_at": stamp(),
        })
        runtime["final_verification_receipt_ledger"] = ledger[-20:]
    save_runtime(runtime)
    # Runtime-regenerated compatibility artifacts (brief + processed pointer). The
    # wake hint is cleared last; the ledger entry alone carries the consume state.
    completion.publish_compatibility_artifacts(ROOT, entry, include_done=False)
    ZCODE_DONE.unlink(missing_ok=True)
    log(
        "Fresh Executor completion consumed from the authoritative ledger",
        message_id=entry["MESSAGE_ID"],
        task_id=entry["TASK_ID"],
        stage_id=entry["STAGE_ID"],
        status=(entry.get("RECEIPT") or {}).get("STATUS"),
        commit_id=entry["COMMIT_ID"],
        archive=archive,
        final_verification_evaluated=final_verification_result is not None,
    )


def seal_completions(runtime: dict, state: dict) -> int:
    """Advance CONSUMED completions to SEALED once their lifecycle decision is past.

    An entry whose identity no longer binds a WAITING_EXECUTOR wait has had its one
    Supervisor lifecycle decision; it can never drive the lifecycle again.
    """
    completion = _completion_helper()
    directory = completion.ledger_dir(ROOT)
    if not directory.is_dir():
        return 0
    if state.get("status") == "SUPERVISOR_TURN":
        return 0  # a decision is still in flight; defer sealing
    active_pid = _active_project_id()
    sealed = 0
    for path in sorted(directory.glob("completion-*.json")):
        entry = completion.load_entry_file(path)
        if entry is None or entry.get("PROJECT_ID") != active_pid:
            continue
        if not completion.seal_predicate(entry, state):
            continue
        entry["STATUS"] = completion.STATUS_SEALED
        entry["SEALED_AT"] = completion.now_iso()
        completion.save_entry(ROOT, entry)
        completion.append_audit(ROOT, {
            "at": completion.now_iso(),
            "event": "COMPLETION_SEALED",
            "actor": "orchestrator",
            "commit_id": entry["COMMIT_ID"],
            "MESSAGE_ID": entry["MESSAGE_ID"],
            "NONCE": entry["NONCE"],
        })
        log("Completion sealed", message_id=entry["MESSAGE_ID"], commit_id=entry["COMMIT_ID"])
        sealed += 1
    return sealed


def reconcile_completion_ledger(runtime: dict, state: dict) -> None:
    """Startup repair of derived state from the authoritative completion ledger.

    Restores runtime consume pointers from a CONSUMED entry whose bookkeeping
    write was interrupted, aligns the ledger when bookkeeping already advanced,
    repairs missing compatibility artifacts for a pending COMMITTED completion,
    seals decisions that are already past, and audits orphan commits.
    """
    completion = _completion_helper()
    directory = completion.ledger_dir(ROOT)
    if not directory.is_dir():
        return
    active_pid = _active_project_id()
    last_consumed = int(runtime.get("last_consumed_message_id", 0) or 0)
    changed_runtime = False
    for path in sorted(directory.glob("completion-*.json")):
        entry = completion.load_entry_file(path)
        if entry is None or entry.get("PROJECT_ID") != active_pid:
            continue
        message_id = int(entry["MESSAGE_ID"])
        if entry["STATUS"] == completion.STATUS_CONSUMED:
            if message_id > last_consumed:
                # The consume-mark survived; the derived-pointer write did not. No
                # Supervisor decision can have started yet (it runs only after the
                # pointer save), so recovering the pointers here is exactly-once safe.
                runtime["last_consumed_message_id"] = message_id
                runtime["last_consumed_nonce"] = entry["NONCE"]
                runtime["last_consumed_brief_sha256"] = entry["BRIEF_SHA256"]
                changed_runtime = True
                completion.append_audit(ROOT, {
                    "at": completion.now_iso(),
                    "event": "COMPLETION_CONSUME_POINTERS_RECOVERED",
                    "actor": "orchestrator",
                    "commit_id": entry["COMMIT_ID"],
                    "MESSAGE_ID": message_id,
                })
            elif state.get("status") != "SUPERVISOR_TURN" and completion.seal_predicate(entry, state):
                entry["STATUS"] = completion.STATUS_SEALED
                entry["SEALED_AT"] = completion.now_iso()
                completion.save_entry(ROOT, entry)
                completion.append_audit(ROOT, {
                    "at": completion.now_iso(),
                    "event": "COMPLETION_SEALED",
                    "actor": "orchestrator",
                    "commit_id": entry["COMMIT_ID"],
                    "MESSAGE_ID": message_id,
                })
                log("Completion sealed", message_id=message_id, commit_id=entry["COMMIT_ID"])
            continue
        if entry["STATUS"] == completion.STATUS_COMMITTED:
            if message_id <= last_consumed:
                # Derived bookkeeping already passed this identity: align the ledger
                # without emitting any lifecycle event.
                entry["STATUS"] = completion.STATUS_CONSUMED
                entry["CONSUMED_AT"] = completion.now_iso()
                completion.save_entry(ROOT, entry)
                completion.append_audit(ROOT, {
                    "at": completion.now_iso(),
                    "event": "COMPLETION_CONSUMED",
                    "actor": "orchestrator",
                    "commit_id": entry["COMMIT_ID"],
                    "MESSAGE_ID": message_id,
                    "aligned_after_crash": True,
                })
                continue
            current = state.get("current_task") or {}
            if state.get("status") == "WAITING_EXECUTOR" and task_identity_matches(entry, current):
                missing = [
                    name for name, path in (
                        ("SUPERVISOR_BRIEF.md", SUPERVISOR_BRIEF),
                        ("ZCODE_LAST_PROCESSED.txt", ZCODE_LAST_PROCESSED),
                        ("ZCODE_DONE.flag", ZCODE_DONE),
                    ) if not path.exists()
                ]
                if missing:
                    completion.publish_compatibility_artifacts(
                        ROOT, entry, include_done=not ZCODE_DONE.exists()
                    )
                    completion.append_audit(ROOT, {
                        "at": completion.now_iso(),
                        "event": "COMPLETION_ARTIFACTS_REPAIRED",
                        "actor": "orchestrator",
                        "commit_id": entry["COMMIT_ID"],
                        "MESSAGE_ID": message_id,
                        "repaired": missing,
                    })
                    log("Repaired completion compatibility artifacts",
                        message_id=message_id, repaired=missing)
            else:
                completion.append_audit(ROOT, {
                    "at": completion.now_iso(),
                    "event": "COMPLETION_ORPHAN_COMMITTED",
                    "actor": "orchestrator",
                    "commit_id": entry["COMMIT_ID"],
                    "MESSAGE_ID": message_id,
                    "project_status": state.get("status"),
                })
    if changed_runtime:
        save_runtime(runtime)


def consume_executor_receipt(runtime: dict) -> tuple[bool, dict | None]:
    """Consume an Executor completion EXCLUSIVELY through the authoritative ledger.

    The root DONE flag is only a wake hint. A completion is consumed when (a) a
    COMPLETION_COMMITTED ledger entry binds the exact identity, (b) the project is
    WAITING_EXECUTOR on that identity, and (c) the Runtime's authorized dispatch
    matches. Everything else — an unknown raw signal, a binding mismatch, or a
    late republish of an already consumed/sealed identity — is quarantined and
    audited: it is never consumed and never drives a Supervisor lifecycle decision.
    """
    if not ZCODE_DONE.exists():
        return False, None
    completion = _completion_helper()
    active_pid = _active_project_id()
    report = completion.classify_raw_completion(
        ROOT,
        active_project_id=active_pid,
        done_path=ZCODE_DONE,
        brief_path=SUPERVISOR_BRIEF,
    )
    classification = report["classification"]

    if classification == completion.CLASS_NO_RAW_SIGNAL:
        return False, None

    if classification in (
        completion.CLASS_UNKNOWN_RAW,
        completion.CLASS_BINDING_MISMATCH,
        completion.CLASS_SEALED_REPLAY,
    ):
        raw_msg = (report.get("raw_identity") or {}).get("MESSAGE_ID")
        completion.quarantine_raw_completion_artifacts(
            ROOT,
            done_path=ZCODE_DONE,
            brief_path=SUPERVISOR_BRIEF,
            label=classification,
            message_id=raw_msg,
        )
        if classification == completion.CLASS_SEALED_REPLAY:
            runtime["stale_receipts_ignored"] = int(runtime.get("stale_receipts_ignored", 0)) + 1
            log(
                "Ignored late republish of an already consumed/sealed completion",
                message_id=raw_msg,
                commit_id=(report.get("entry") or {}).get("COMMIT_ID"),
                brief_diverges=report["brief_diverges"],
            )
        else:
            runtime["protocol_errors"] = int(runtime.get("protocol_errors", 0)) + 1
            log(
                "Raw Executor completion signal rejected (fail closed)",
                classification=classification,
                message_id=raw_msg,
                detail=report["detail"],
            )
        save_runtime(runtime)
        return True, None

    entry = report["entry"]
    if not completion.entry_hashes_intact(entry):
        completion.quarantine_raw_completion_artifacts(
            ROOT,
            done_path=ZCODE_DONE,
            brief_path=SUPERVISOR_BRIEF,
            label="LEDGER_HASH_BROKEN",
            message_id=(report.get("raw_identity") or {}).get("MESSAGE_ID"),
        )
        runtime["protocol_errors"] = int(runtime.get("protocol_errors", 0)) + 1
        save_runtime(runtime)
        completion.append_audit(ROOT, {
            "at": completion.now_iso(),
            "event": "COMPLETION_LEDGER_HASH_BROKEN",
            "actor": "orchestrator",
            "commit_id": entry.get("COMMIT_ID"),
        })
        log("Ledger entry hash binding broken; raw completion signal rejected",
            commit_id=entry.get("COMMIT_ID"))
        return True, None

    msg_id = int(entry["MESSAGE_ID"])

    if msg_id <= int(runtime.get("last_consumed_message_id", 0) or 0):
        # Crash-window alignment: derived bookkeeping already passed this identity,
        # so the receipt must not trigger a second Supervisor decision.
        _mark_completion_consumed(completion, entry, runtime, aligned=True)
        return True, None

    state = read_project_state()
    current = state.get("current_task") or {}
    authorized = runtime.get("authorized_dispatch")
    if (
        state.get("status") != "WAITING_EXECUTOR"
        or not task_identity_matches(entry, current)
        or not (isinstance(authorized, dict) and task_identity_matches(authorized, entry))
    ):
        completion.quarantine_raw_completion_artifacts(
            ROOT,
            done_path=ZCODE_DONE,
            brief_path=SUPERVISOR_BRIEF,
            label="ORPHAN_COMPLETION",
            message_id=msg_id,
        )
        runtime["protocol_errors"] = int(runtime.get("protocol_errors", 0)) + 1
        save_runtime(runtime)
        completion.append_audit(ROOT, {
            "at": completion.now_iso(),
            "event": "COMPLETION_ORPHAN_WAKE_QUARANTINED",
            "actor": "orchestrator",
            "commit_id": entry["COMMIT_ID"],
            "MESSAGE_ID": msg_id,
            "project_status": state.get("status"),
        })
        log(
            "Committed completion is not bound to the pending lifecycle; wake hint quarantined",
            message_id=msg_id,
            project_status=state.get("status"),
            identity_match=task_identity_matches(entry, current),
        )
        return True, None

    receipt = entry["RECEIPT"]
    final_verification_result = None
    fv_task_view = current if is_final_verification_task(current) else None
    if fv_task_view is None:
        # FV-IDENTITY-BINDING-V1: current_task mirrors only identity keys by wire
        # contract, so FV-ness falls back to the Runtime's own authorization record.
        fv_task_view = authorized_final_verification_task(runtime, receipt, msg_id)
    if fv_task_view is not None:
        policy, _ = _resolve_fv_policy_for_state(state)  # G4
        final_verification_result = evaluate_final_verification_receipt(fv_task_view, receipt, policy)

    brief_hash = entry["BRIEF_SHA256"]
    archive = HANDOFF_ARCHIVE / f"brief-{msg_id}-{str(entry['NONCE'])[:12]}-consumed-{brief_hash[:12]}.md"
    HANDOFF_ARCHIVE.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        atomic_create(archive, completion.render_brief_text(receipt))

    _mark_completion_consumed(
        completion,
        entry,
        runtime,
        aligned=False,
        archive=str(archive.relative_to(ROOT)),
        final_verification_result=final_verification_result,
    )
    event = {
        "type": "EXECUTOR_RESULT_READY",
        "message_id": msg_id,
        "task_id": entry["TASK_ID"],
        "stage_id": entry["STAGE_ID"],
        "status": receipt.get("STATUS"),
        "brief_sha256": brief_hash,
        "archive": str(archive.relative_to(ROOT)),
        "commit_id": entry["COMMIT_ID"],
        "receipt_sha256": entry["RECEIPT_SHA256"],
        "committed_receipt_path": str(completion.entry_path(ROOT, entry["COMMIT_ID"])),
    }
    if final_verification_result is not None:
        event["final_verification"] = final_verification_result
    return True, event


def executor_timeout_event(runtime: dict, state: dict) -> dict | None:
    if state.get("status") != "WAITING_EXECUTOR":
        return None
    task = state.get("current_task") or {}
    nonce = task_value(task, "NONCE")
    if not nonce or runtime.get("timeout_notified_for_nonce") == nonce:
        return None

    # Recover timing metadata from the validated root task when Supervisor state is compact.
    issued_raw = task_value(task, "ISSUED_AT") or task_value(task, "DISPATCHED_AT")
    max_raw = task_value(task, "MAX_TIME")
    grace_raw = task_value(task, "SCHEDULER_GRACE_SECONDS")

    if issued_raw is None or max_raw is None or grace_raw is None:
        try:
            payload = parse_json_fence(TO_ZCODE)
            if task_identity_matches(payload, task):
                if issued_raw is None:
                    issued_raw = payload.get("ISSUED_AT") or payload.get("DISPATCHED_AT")
                if max_raw is None:
                    max_raw = payload.get("MAX_TIME")
                if grace_raw is None:
                    grace_raw = payload.get("SCHEDULER_GRACE_SECONDS")
        except Exception:
            pass

    issued = parse_time(issued_raw)
    if issued is None:
        # FIX-F03: a missing/garbage ISSUED_AT must not silently disarm the watchdog.
        issued = parse_time(runtime.get("dispatch_registered_at"))
        if issued is not None:
            log(
                "Task ISSUED_AT missing/unparseable; using orchestrator dispatch time as deadline base",
                raw_value=str(issued_raw),
            )
    if not issued:
        return None

    max_seconds = parse_duration_seconds(max_raw, DEFAULT_EXECUTOR_TIMEOUT_SECONDS)
    grace_seconds = parse_duration_seconds(grace_raw, MIN_SCHEDULER_GRACE_SECONDS)
    # Scheduled Automation may wake on a cadence, so always allow at least one full scheduler cycle.
    grace = max(grace_seconds, MIN_SCHEDULER_GRACE_SECONDS)

    deadline = issued + timedelta(seconds=max_seconds + grace)
    if utc_now() < deadline:
        return None
    runtime["timeout_notified_for_nonce"] = nonce
    save_runtime(runtime)
    return {
        "type": "EXECUTOR_TIMEOUT",
        "task_id": task_value(task, "TASK_ID"),
        "stage_id": task_value(task, "STAGE_ID"),
        "message_id": task_value(task, "MESSAGE_ID"),
        "attempt": task_value(task, "ATTEMPT"),
        "nonce": nonce,
        "issued_at": issued.isoformat(),
        "max_time_seconds": max_seconds,
        "scheduler_grace_seconds": grace,
        "deadline": deadline.isoformat(),
    }


def hard_runtime_deadline(state: dict) -> bool:
    deadline = parse_time(state.get("deadline_at"))
    return bool(deadline and utc_now() >= deadline)


def replay_consumed_receipt_event(runtime: dict, current: dict, state: dict | None = None) -> dict | None:
    """FIX-F17: rebuild an EXECUTOR_RESULT_READY event for an interrupted decision.

    Used when a restart finds current_task.MESSAGE_ID <= last_consumed_message_id:
    the receipt was consumed but the Supervisor turn after it never completed
    (crash between the consume-mark and the end of that Codex turn). The event is
    rebuilt from the authoritative ledger entry when one exists; pre-ledger
    history falls back to the consumed brief archive.
    """
    msg = task_value(current, "MESSAGE_ID")
    nonce = str(task_value(current, "NONCE") or "")
    completion = _completion_helper()
    for entry in completion.lookup_entries(ROOT, msg if isinstance(msg, int) else None):
        if entry["STATUS"] != completion.STATUS_CONSUMED:
            continue
        if not task_identity_matches(entry, current):
            continue
        event = {
            "type": "EXECUTOR_RESULT_READY",
            "message_id": entry["MESSAGE_ID"],
            "task_id": entry["TASK_ID"],
            "stage_id": entry["STAGE_ID"],
            "status": (entry.get("RECEIPT") or {}).get("STATUS"),
            "brief_sha256": entry["BRIEF_SHA256"],
            "archive": entry.get("CONSUMED_ARCHIVE"),
            "commit_id": entry["COMMIT_ID"],
            "committed_receipt_path": str(completion.entry_path(ROOT, entry["COMMIT_ID"])),
            "replayed_after_crash": True,
        }
        if is_final_verification_task(current):
            policy, _ = _resolve_fv_policy_for_state(state if state is not None else read_project_state())
            event["final_verification"] = evaluate_final_verification_receipt(
                current, entry.get("RECEIPT") or {}, policy
            )
        return event
    if not HANDOFF_ARCHIVE.exists():
        return None
    for path in sorted(HANDOFF_ARCHIVE.glob(f"brief-{msg}-*-consumed-*.md"), reverse=True):
        if nonce and nonce[:12] not in path.name:
            continue
        try:
            brief = parse_json_fence(path)
        except Exception:
            continue
        if task_identity_matches(brief, current):
            event = {
                "type": "EXECUTOR_RESULT_READY",
                "message_id": msg,
                "task_id": task_value(current, "TASK_ID"),
                "stage_id": task_value(current, "STAGE_ID"),
                "status": brief.get("STATUS"),
                "brief_sha256": sha256(path),
                "archive": str(path.relative_to(ROOT)),
                "replayed_after_crash": True,
            }
            if is_final_verification_task(current):
                policy, _ = _resolve_fv_policy_for_state(state if state is not None else read_project_state())
                event["final_verification"] = evaluate_final_verification_receipt(current, brief, policy)
            return event
    return None


def main() -> int:
    runtime = load_runtime()
    activate_project_scope()  # G3: legacy mode or validated isolated project
    acquire_lock()
    runtime["status"] = "RUNNING"
    save_runtime(runtime)
    log("Orchestrator v2 started")

    try:
        # Honor hard pause/stop conditions before any model invocation.
        state = read_project_state()
        if STOP_FLAG.exists():
            runtime["status"] = "STOPPED_BY_USER"
            save_runtime(runtime)
            log("control/STOP detected before startup; Codex was not invoked")
            emit_user_notification(runtime, "STOPPED_BY_USER", state)
            return 2
        if HUMAN_REVIEW_FLAG.exists() or state.get("status") == "HUMAN_REVIEW":
            runtime["status"] = "HUMAN_REVIEW"
            save_runtime(runtime)
            log("Human review present before startup; Codex was not invoked")
            emit_user_notification(runtime, "HUMAN_REVIEW", state)
            return 3
        if hard_runtime_deadline(state):
            runtime["status"] = "DEADLINE_REACHED"
            save_runtime(runtime)
            log("Project deadline already reached; Codex was not invoked")
            emit_user_notification(runtime, "DEADLINE_REACHED", state)
            return 4

        state, gate_event = enforce_terminal_verification_gate(runtime, state, "startup")
        if gate_event:
            invoke_codex(runtime, "FINAL_VERIFICATION_GATE_REQUIRED", gate_event)
            state = read_project_state()

        # COMPLETION-SEAL-V1: repair derived state from the authoritative completion
        # ledger before any lifecycle branch runs (pointer recovery, artifact repair,
        # sealing of decisions already past). The ledger is the source of truth.
        reconcile_completion_ledger(runtime, state)
        state = read_project_state()

        # Startup is lifecycle-aware. A restart while WAITING_EXECUTOR must never spend a
        # second Supervisor turn or duplicate-dispatch the stage that is already in flight.
        if state.get("status") == "SUPERVISOR_TURN":
            resume_event = human_decision_resume_event(state)
            if resume_event is not None:
                invoke_codex(runtime, "HUMAN_DECISION_RESUME", resume_event)
            else:
                invoke_codex(runtime, "ORCHESTRATOR_START", {"runtime": "v2", "shared_file_executor": True})
        elif state.get("status") == "WAITING_EXECUTOR":
            resume_msg = task_value((state.get("current_task") or {}), "MESSAGE_ID")
            if resume_msg is not None and int(resume_msg) <= int(runtime.get("last_consumed_message_id", 0) or 0):
                # FIX-F17: crash window between the consume-mark and the end of the
                # Supervisor turn. Replay the archived receipt instead of failing with
                # a misleading "stale/reused MESSAGE_ID" protocol error.
                event = replay_consumed_receipt_event(runtime, state.get("current_task") or {}, state)
                if event is None:
                    raise RuntimeError(
                        "Restart found current_task already consumed but no archived "
                        "brief matches it; manual repair required"
                    )
                log(
                    "Resuming consumed-but-unsupervised receipt via archive replay",
                    message_id=event["message_id"],
                    archive=event["archive"],
                )
                invoke_codex(runtime, "EXECUTOR_RESULT_READY", event)
            else:
                try:
                    task = register_dispatched_task(runtime, state, allow_same_identity=True)
                    log(
                        "Resuming existing Executor wait; Codex was not invoked",
                        message_id=task["MESSAGE_ID"],
                        task_id=task["TASK_ID"],
                        stage_id=task["STAGE_ID"],
                    )
                except RuntimeError as exc:
                    # FIX-700102: startup can meet the same mechanically rejected
                    # candidate (e.g. restart after ORCHESTRATOR_ERROR). Quarantine and
                    # arm the same bounded repair turn instead of crashing on startup.
                    state = handle_dispatch_registration_failure(runtime, state, exc)
                    log(
                        "Startup dispatch rejected; Supervisor repair turn armed",
                        error=repr(exc)[:400],
                    )

        while True:
            state = read_project_state()

            if STOP_FLAG.exists():
                runtime["status"] = "STOPPED_BY_USER"
                save_runtime(runtime)
                log("control/STOP detected; no new model calls will be made")
                emit_user_notification(runtime, "STOPPED_BY_USER", state)
                return 2

            if HUMAN_REVIEW_FLAG.exists() or state.get("status") == "HUMAN_REVIEW":
                runtime["status"] = "HUMAN_REVIEW"
                save_runtime(runtime)
                log("Human review requested; orchestration stopped")
                emit_user_notification(runtime, "HUMAN_REVIEW", state)
                return 3

            if hard_runtime_deadline(state):
                runtime["status"] = "DEADLINE_REACHED"
                save_runtime(runtime)
                log("24-hour project deadline reached; orchestration stopped")
                emit_user_notification(runtime, "DEADLINE_REACHED", state)
                return 4

            state, gate_event = enforce_terminal_verification_gate(runtime, state, "main_loop")
            if gate_event:
                invoke_codex(runtime, "FINAL_VERIFICATION_GATE_REQUIRED", gate_event)
                continue

            if state.get("status") in {"COMPLETE", "BLOCKED", "STOPPED"}:
                terminal_status = state.get("status")
                runtime["status"] = terminal_status
                save_runtime(runtime)
                log("Project reached terminal state", project_status=terminal_status, infrastructure_status=state.get("infrastructure_status"))
                emit_user_notification(runtime, terminal_status, state)
                return 0 if terminal_status == "COMPLETE" else 5

            signal_seen, event = consume_executor_receipt(runtime)
            if event:
                invoke_codex(runtime, event["type"], event)
                if event["type"] == "EXECUTOR_RESULT_READY":
                    # COMPLETION-SEAL-V1: the one Supervisor lifecycle decision for
                    # this completion has been made; seal the identity so it can
                    # never drive the lifecycle again.
                    seal_completions(runtime, read_project_state())
                continue
            if signal_seen:
                # A malformed signal was reported above and the next loop will let Codex act if needed.
                time.sleep(POLL_SECONDS)
                continue

            timeout_event = executor_timeout_event(runtime, state)
            if timeout_event:
                log("Executor timeout detected", **timeout_event)
                invoke_codex(runtime, "EXECUTOR_TIMEOUT", timeout_event)
                continue

            # If Codex intentionally left the lifecycle at SUPERVISOR_TURN, run it once; it must not busy-loop.
            if state.get("status") == "SUPERVISOR_TURN":
                resume_event = human_decision_resume_event(state)
                if resume_event is not None:
                    invoke_codex(runtime, "HUMAN_DECISION_RESUME", resume_event)
                else:
                    invoke_codex(runtime, "SUPERVISOR_TURN", {"source": "project_state"})
                continue

            # FIX-F01: an off-contract status is a Supervisor protocol violation.
            # Fail closed with a notification instead of silently polling forever.
            # (WAITING_EXECUTOR is the normal poll-and-wait state and must keep polling.)
            if state.get("status") != "WAITING_EXECUTOR":
                raise RuntimeError(
                    "project_state.status has an unknown value; refusing to poll forever: "
                    f"{state.get('status')!r}"
                )

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        runtime["status"] = "INTERRUPTED"
        save_runtime(runtime)
        log("Interrupted by console")
        return 130
    except Exception as exc:
        runtime["status"] = "ORCHESTRATOR_ERROR"
        runtime["last_error"] = repr(exc)
        save_runtime(runtime)
        log("Fatal orchestrator error", error=repr(exc))
        try:
            state = read_project_state()
        except Exception:
            state = {"project": "runtime-root", "phase": None, "status": "ORCHESTRATOR_ERROR"}
        emit_user_notification(runtime, "ORCHESTRATOR_ERROR", state, {"error": repr(exc)})
        return 1
    finally:
        release_lock()


if __name__ == "__main__":
    raise SystemExit(main())
