"""P5 Human Control pure layer for the v1.3 Web Console.

This module holds the deterministic, offline half of the mutation-capable
Human Control surface: typed fail-closed request schemas, the pending-controls
projection over authoritative Runtime facts, the STOP-challenge evaluation,
and the HUMAN_REVIEW presentation block. It never touches the filesystem, the
clock, randomness, or subprocesses — the HTTP layer in
`web_console_server.py` owns those and calls Runtime mutations only through
the existing formal v1.2 control entry points (`supervisor_control.py pause |
resume | intervene`, `STOP_AGENT_SYSTEM.ps1`, and
`resume_human_review.py prepare | apply`).

The Console never writes project_state.json, intervention files, STOP
artifacts, claim/fence state, completion ledgers, or Human Decision receipts
itself; every mutation is delegated verbatim to the Runtime helpers.
"""
from __future__ import annotations

import hmac
import re
import unicodedata

CONTROLS_SCHEMA_VERSION = 1
INTERVENTION_MODES = ("STEER", "AUDIT")
PAUSE_MODES = ("SAFE", "INTERRUPT_CURRENT")
COMMENT_MAX_CHARS = 4000
MESSAGE_ID_MAX = 999999999
PROJECT_ID_MAX_CHARS = 120
HUMAN_REVIEW_REASON_MAX_BYTES = 64 * 1024
PENDING_INTERVENTION_LIMIT = 50
CHALLENGE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
TOKEN_PATTERN = re.compile(r"^[0-9a-f]{32}$")
RECEIPT_ID_PATTERN = re.compile(r"^human-decision-[0-9a-f]{32}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

# Categories surfaced by the Pending Controls view (facts only).
CATEGORY_PENDING = "pending"
CATEGORY_CONSUMED = "consumed"
CATEGORY_NEEDS_RECOVERY = "needs_recovery"
CATEGORY_FAILED = "failed"
CATEGORY_OTHER = "other"


class ControlRequestError(ValueError):
    """A request body that fails closed before any Runtime operation."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


def _require_object(payload) -> None:
    if not isinstance(payload, dict):
        raise ControlRequestError("the request body must be a JSON object")


def _require_exact_keys(payload: dict, keys: tuple) -> None:
    actual = set(payload)
    expected = set(keys)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ControlRequestError(
            "request schema mismatch "
            f"(missing={missing}, unknown={unknown})")


def _require_text(payload: dict, key: str, *, max_chars: int,
                  allow_empty: bool = False,
                  multiline: bool = False) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ControlRequestError(f"{key} must be a string", field=key)
    if not value and not allow_empty:
        raise ControlRequestError(f"{key} must not be empty", field=key)
    if value != value.strip() and not allow_empty:
        raise ControlRequestError(
            f"{key} must not have leading or trailing whitespace", field=key)
    if len(value) > max_chars:
        raise ControlRequestError(
            f"{key} exceeds {max_chars} characters", field=key)
    allowed_control = {"\t", "\n", "\r"} if multiline else set()
    for char in value:
        if unicodedata.category(char) == "Cc" and char not in allowed_control:
            raise ControlRequestError(
                f"{key} must not contain control characters", field=key)
    return value


def _require_bool(payload: dict, key: str) -> bool:
    value = payload[key]
    if not isinstance(value, bool):
        raise ControlRequestError(f"{key} must be a boolean", field=key)
    return value


def validate_pause_request(payload) -> dict:
    """Exactly {"mode": "SAFE" | "INTERRUPT_CURRENT"}."""
    _require_object(payload)
    _require_exact_keys(payload, ("mode",))
    mode = payload["mode"]
    if mode not in PAUSE_MODES or not isinstance(mode, str):
        raise ControlRequestError(
            "mode must be one of " + ", ".join(PAUSE_MODES), field="mode")
    return {"mode": mode}


def validate_resume_request(payload) -> dict:
    """Exactly {} — Resume takes no parameters."""
    _require_object(payload)
    _require_exact_keys(payload, ())
    return {}


def validate_intervention_request(payload) -> dict:
    """Exactly {mode, comment, target_message_id, interrupt_current}."""
    _require_object(payload)
    _require_exact_keys(payload, ("mode", "comment", "target_message_id",
                                  "interrupt_current"))
    mode = payload["mode"]
    if mode not in INTERVENTION_MODES or not isinstance(mode, str):
        raise ControlRequestError(
            "mode must be one of " + ", ".join(INTERVENTION_MODES),
            field="mode")
    comment = _require_text(payload, "comment",
                            max_chars=COMMENT_MAX_CHARS, multiline=True)
    target = payload["target_message_id"]
    if target is not None:
        if isinstance(target, bool) or not isinstance(target, int) \
                or not 0 <= target <= MESSAGE_ID_MAX:
            raise ControlRequestError(
                f"target_message_id must be null or an integer in "
                f"0..{MESSAGE_ID_MAX}", field="target_message_id")
    interrupt = _require_bool(payload, "interrupt_current")
    return {"mode": mode, "comment": comment, "target_message_id": target,
            "interrupt_current": interrupt}


def validate_stop_confirm_request(payload) -> dict:
    """Exactly {challenge_id, confirmation_token, project_id}."""
    _require_object(payload)
    _require_exact_keys(payload, ("challenge_id", "confirmation_token",
                                  "project_id"))
    challenge_id = _require_text(payload, "challenge_id", max_chars=32)
    token = _require_text(payload, "confirmation_token", max_chars=32)
    if not CHALLENGE_ID_PATTERN.fullmatch(challenge_id):
        raise ControlRequestError("challenge_id is malformed",
                                  field="challenge_id")
    if not TOKEN_PATTERN.fullmatch(token):
        raise ControlRequestError("confirmation_token is malformed",
                                  field="confirmation_token")
    project_id = _require_text(payload, "project_id",
                               max_chars=PROJECT_ID_MAX_CHARS)
    return {"challenge_id": challenge_id,
            "confirmation_token": token, "project_id": project_id}


def validate_human_decision_apply_request(payload) -> dict:
    """Exactly {receipt_id, receipt_sha256} — binds the confirmation to the
    exact prepared receipt."""
    _require_object(payload)
    _require_exact_keys(payload, ("receipt_id", "receipt_sha256"))
    receipt_id = _require_text(payload, "receipt_id", max_chars=48)
    if not RECEIPT_ID_PATTERN.fullmatch(receipt_id):
        raise ControlRequestError("receipt_id is malformed", field="receipt_id")
    receipt_sha256 = _require_text(payload, "receipt_sha256", max_chars=64)
    if not SHA256_PATTERN.fullmatch(receipt_sha256):
        raise ControlRequestError("receipt_sha256 must be 64 hex characters",
                                  field="receipt_sha256")
    return {"receipt_id": receipt_id, "receipt_sha256": receipt_sha256}


def _pause_facts(pause) -> tuple[dict, list]:
    notes = []
    facts = {"present": False, "state": "NONE", "mode": None,
             "requested_at": None, "paused_at": None, "resumed_at": None,
             "disposition": None, "superseded_by_completion": False}
    if pause is None:
        return facts, notes
    if not isinstance(pause, dict):
        notes.append("the Runtime reported an unusable pause record; pause "
                     "facts are unavailable")
        facts["state"] = "UNAVAILABLE"
        return facts, notes
    status = pause.get("status")
    if status == "RUNNING" or status is None:
        facts["resumed_at"] = pause.get("resumed_at") \
            if isinstance(pause.get("resumed_at"), str) else None
        return facts, notes
    facts["present"] = True
    mode = pause.get("mode")
    facts["mode"] = mode if isinstance(mode, str) else None
    for key in ("requested_at", "paused_at"):
        value = pause.get(key)
        facts[key] = value if isinstance(value, str) else None
    disposition = pause.get("disposition")
    if isinstance(disposition, str):
        facts["disposition"] = disposition
        facts["superseded_by_completion"] = \
            disposition == "COMPLETION_COMMITTED_WINS"
    if status == "PENDING_AFTER_CURRENT_STAGE":
        facts["state"] = "PENDING"
    elif status == "PAUSED":
        facts["state"] = "APPLIED"
    else:
        facts["state"] = "UNAVAILABLE"
        notes.append(f"the Runtime reported an unknown pause status "
                     f"{status!r}; pause facts are unavailable")
    return facts, notes


def _classify_intervention(record) -> tuple[str | None, bool]:
    """Return (category, malformed). Categories are facts from the record."""
    if not isinstance(record, dict):
        return None, True
    integrity = record.get("integrity")
    if integrity not in (None, "OK"):
        return CATEGORY_FAILED, False
    status = record.get("status")
    if status in ("PENDING", "INJECTED"):
        return CATEGORY_PENDING, False
    if status == "CONSUMED":
        return CATEGORY_CONSUMED, False
    if status == "RECOVERY_REQUIRED":
        return CATEGORY_NEEDS_RECOVERY, False
    return CATEGORY_OTHER, False


def project_pending_controls(status, interventions) -> dict:
    """Project the Pending Controls document from authoritative facts.

    `status` is one `supervisor_control status --json` document and
    `interventions` is the list from `supervisor_control interventions
    --json` (None when that source failed). Nothing is inferred beyond those
    documents: unknown values surface as honest unavailability, malformed
    records are counted in the honesty block, and success is never inferred
    from root compatibility flags or UI-local state.
    """
    notes: list[str] = []
    malformed = 0
    if not isinstance(status, dict):
        raise ValueError("status must be the parsed control-plane document")
    project_id = status.get("PROJECT_ID")
    pause_facts, pause_notes = _pause_facts(status.get("pause"))
    notes.extend(pause_notes)

    items = []
    counts = {"pending": 0, "consumed": 0, "superseded": 0, "failed": 0,
              "needs_recovery": 0, "other": 0}
    if pause_facts["superseded_by_completion"]:
        counts["superseded"] += 1
    available = interventions is not None
    valid: list[tuple[dict, str]] = []
    total = 0
    if not available:
        notes.append("the interventions query did not answer; intervention "
                     "facts are unavailable")
    elif not isinstance(interventions, list):
        malformed += 1
        notes.append("the interventions query returned a non-list document; "
                     "intervention facts are unavailable")
    else:
        for record in interventions:
            category, bad = _classify_intervention(record)
            if bad:
                malformed += 1
                notes.append("one intervention record was malformed and is "
                             "surfaced only as a honesty note")
                continue
            counts[category] += 1
            valid.append((record, category))
        total = len(valid)
        valid.sort(key=lambda pair: str(pair[0].get("submitted_at") or ""),
                   reverse=True)
        for record, category in valid[:PENDING_INTERVENTION_LIMIT]:
            consumed_at = record.get("consumed_at")
            items.append({
                "intervention_id": record.get("intervention_id")
                if isinstance(record.get("intervention_id"), str) else None,
                "mode": record.get("mode")
                if isinstance(record.get("mode"), str) else None,
                "status": record.get("status")
                if isinstance(record.get("status"), str) else None,
                "integrity": record.get("integrity")
                if isinstance(record.get("integrity"), str) else None,
                "category": category,
                "submitted_at": record.get("submitted_at")
                if isinstance(record.get("submitted_at"), str) else None,
                "target_message_id": record.get("target_message_id")
                if isinstance(record.get("target_message_id"), int)
                and not isinstance(record.get("target_message_id"), bool)
                else None,
                "interrupt_current": record.get("interrupt_current")
                if isinstance(record.get("interrupt_current"), bool)
                else None,
                "consumed_at": consumed_at
                if isinstance(consumed_at, str) else None,
            })
        if total > PENDING_INTERVENTION_LIMIT:
            notes.append(
                f"the intervention history was truncated to the "
                f"{PENDING_INTERVENTION_LIMIT} most recent records "
                f"({total} total)")
    return {
        "schema_version": CONTROLS_SCHEMA_VERSION,
        "project_id": project_id if isinstance(project_id, str) else None,
        "pause": pause_facts,
        "stop": {"applied": status.get("stop") is True},
        "interventions": {"available": available, "total": total,
                          "relayed": len(items),
                          "truncated": total > PENDING_INTERVENTION_LIMIT,
                          "items": items},
        "counts": counts,
        "honesty": {"notes": notes, "malformed_records": malformed},
    }


def human_review_presentation(status, reason_text, *, truncated: bool) -> dict:
    """Compose the HUMAN_REVIEW presentation block from authoritative facts.

    `reason_text` is the bounded server-side read of the Runtime's
    control/HUMAN_REVIEW flag file (None when it is absent or unreadable).
    The presentation never claims a task is or is not running beyond what the
    status document reports.
    """
    active = isinstance(status, dict) and status.get("human_review") is True
    active_task = status.get("active_task") \
        if isinstance(status, dict) else None
    claimed = isinstance(status, dict) and status.get("active_task_claimed") \
        is True
    task_present = isinstance(active_task, dict)
    message_id = active_task.get("MESSAGE_ID") \
        if task_present and isinstance(active_task.get("MESSAGE_ID"), int) \
        and not isinstance(active_task.get("MESSAGE_ID"), bool) else None
    diagnostic = status.get("human_review_reason") if isinstance(status, dict) else None
    diagnostic = diagnostic if active and isinstance(diagnostic, dict) else {}
    raw_error = diagnostic.get("raw_error")
    raw_error = raw_error if isinstance(raw_error, str) and raw_error.strip() else reason_text
    summary = None
    summary_en = None
    if isinstance(raw_error, str) and raw_error.strip():
        stage = diagnostic.get("stage")
        if stage == "FINAL_VERIFICATION_DISPATCH":
            summary = "最终验证任务在执行授权前校验失败，自动恢复次数已用尽，因此暂停。需要人工检查失败原因并决定后续操作；尚未完成最终验证。"
            summary_en = "Final Verification dispatch failed validation before execution authorization and automatic recovery was exhausted. Human review is required to decide the next action; Final Verification has not completed."
        elif stage == "DISPATCH_CANDIDATE_VALIDATION":
            summary = "任务在执行授权前校验失败，自动恢复次数已用尽，因此暂停。需要人工检查原因并决定后续操作。"
            summary_en = "Task dispatch failed validation before execution authorization and automatic recovery was exhausted. Human review is required to decide the next action."
        else:
            summary = "Runtime 已暂停并等待人工决策。请检查停止原因，再决定后续操作。"
            summary_en = "Runtime has stopped for human review. Inspect the recorded reason and decide the next action."
    return {
        "active": active,
        "reason": {"available": isinstance(raw_error, str), "text": raw_error,
                   "summary": summary, "summary_en": summary_en,
                   "stage": diagnostic.get("stage"),
                   "requires_human_action": active,
                   "source": diagnostic.get("source") or "control/HUMAN_REVIEW",
                   "raw_error": raw_error, "truncated": bool(truncated)},
        "authorized_task": {"present": task_present, "claimed": claimed,
                            "message_id": message_id},
        "project_status": status.get("project_status")
        if isinstance(status, dict)
        and isinstance(status.get("project_status"), str) else None,
    }


def evaluate_stop_challenge(challenge, *, runtime_id: str, request: dict,
                            now: float, project_id: str,
                            project_state_sha256: str,
                            stop_applied: bool) -> tuple[bool, str | None]:
    """Evaluate one STOP confirmation against its issued challenge.

    Every mismatch fails closed with a structured reason. The order is
    deliberate: existence/ownership first (no information leak across
    Runtimes), then replay, expiry, token, project binding, terminal state,
    and finally the project-state binding captured at issuance.
    """
    if not isinstance(challenge, dict) \
            or challenge.get("runtime_id") != runtime_id \
            or request.get("challenge_id") != challenge.get("challenge_id"):
        return False, "CHALLENGE_UNKNOWN"
    if challenge.get("used") is True:
        return False, "CHALLENGE_ALREADY_USED"
    expires_at = challenge.get("expires_at")
    if not isinstance(expires_at, (int, float)) or isinstance(expires_at, bool) \
            or now > expires_at:
        return False, "CHALLENGE_EXPIRED"
    token = request.get("confirmation_token", "")
    expected_token = challenge.get("token", "")
    if not isinstance(token, str) or not isinstance(expected_token, str) \
            or not hmac.compare_digest(token, expected_token):
        return False, "TOKEN_MISMATCH"
    if request.get("project_id") != challenge.get("project_id") \
            or project_id != challenge.get("project_id"):
        return False, "PROJECT_MISMATCH"
    if stop_applied:
        return False, "STOP_ALREADY_APPLIED"
    if project_state_sha256 != challenge.get("project_state_sha256"):
        return False, "STATE_CHANGED"
    return True, None
