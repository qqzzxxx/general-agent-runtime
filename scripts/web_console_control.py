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
import json
import re
import unicodedata

CONTROLS_SCHEMA_VERSION = 1
AUTO_RESUME_SCHEMA_VERSION = 1
INTERVENTION_MODES = ("STEER", "AUDIT")
PAUSE_MODES = ("SAFE", "INTERRUPT_CURRENT")
COMMENT_MAX_CHARS = 4000
MESSAGE_ID_MAX = 999999999
PROJECT_ID_MAX_CHARS = 120
HUMAN_REVIEW_REASON_MAX_BYTES = 64 * 1024
PENDING_INTERVENTION_LIMIT = 50
# The only pending Supervisor event reason that proves a Human Decision was
# formally applied and is still waiting to be serviced by the Scheduler.
HUMAN_DECISION_RESUME_REASON = "HUMAN_DECISION_RESUME"
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


def prepared_decision_summary(stored) -> dict | None:
    """Shape one stored prepared Human Decision receipt for the controls
    document, or None when it is not a usable receipt record.

    The Console shows the user exactly what its own prepare step stored so
    the Apply step can echo the same receipt_id/receipt_sha256 pair back
    verbatim; the Runtime helper re-validates the full receipt at apply
    time, so this summary validates identity fields only and never derives
    or recomputes any binding.
    """
    if not isinstance(stored, dict):
        return None
    receipt_id = stored.get("receipt_id")
    if not isinstance(receipt_id, str) \
            or not RECEIPT_ID_PATTERN.fullmatch(receipt_id):
        return None
    receipt_sha256 = stored.get("receipt_sha256")
    if not isinstance(receipt_sha256, str) \
            or not SHA256_PATTERN.fullmatch(receipt_sha256):
        return None
    submitted_at = stored.get("submitted_at")
    if not isinstance(submitted_at, str):
        submitted_at = None
    return {"receipt_id": receipt_id, "receipt_sha256": receipt_sha256,
            "submitted_at": submitted_at}


def _pause_facts(pause) -> tuple[dict, list]:
    notes = []
    facts = {"present": False, "state": "NONE", "mode": None,
             "requested_at": None, "paused_at": None, "resumed_at": None,
             "disposition": None, "superseded_by_completion": False,
             "parked_dispatch": None}
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
    # QUOTA-PAUSE-PARK-V1: a parked dispatch is paused logical work, not a
    # failure. The Console shows it as waiting, never as an error.
    parked = pause.get("parked_dispatch")
    if isinstance(parked, dict) and isinstance(parked.get("MESSAGE_ID"), int) \
            and not isinstance(parked.get("MESSAGE_ID"), bool):
        parked_at = parked.get("PARKED_AT")
        expires_at = parked.get("EXPIRES_AT")
        converted_at = parked.get("CONVERTED_AT")
        task_id = parked.get("TASK_ID")
        facts["parked_dispatch"] = {
            "message_id": parked["MESSAGE_ID"],
            "task_id": task_id if isinstance(task_id, str) else None,
            "parked_at": parked_at if isinstance(parked_at, str) else None,
            "expires_at": expires_at if isinstance(expires_at, str) else None,
            "converted_at": converted_at if isinstance(converted_at, str) else None,
        }
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


# -- Human Review Decision Brief (projection only) ---------------------------
#
# 2026-09-16 follow-up: entering HUMAN_REVIEW surfaced only a generic
# "decision required" line plus the mechanical control/HUMAN_REVIEW flag
# text, so the operator had to read project_state.json by hand to learn what
# happened, which results still hold, and what the Supervisor suggests. The
# brief below projects the authoritative committed decision record
# (decision_history / last_supervisor_decision, including the GOAL-ANCHOR-V1
# goal_alignment contract) verbatim into the controls document. It adds no
# judgment: unknown is "unavailable", text is only ever bounded, and the
# projection is never written back to the Runtime.

DECISION_BRIEF_SCHEMA_VERSION = 1
# Bounds mirror the Runtime's own committed-record limits in orchestrator.py
# (reason <= 12000 chars, goal_alignment fields <= 2000, scope <= 2048,
# task/stage ids <= 512). Longer legacy or hand-edited values are truncated
# and flagged, never interpreted.
DECISION_BRIEF_REASON_MAX = 12000
DECISION_BRIEF_ALIGNMENT_MAX = 2000
DECISION_BRIEF_SCOPE_MAX = 2048
DECISION_BRIEF_IDENTITY_MAX = 512
DECISION_BRIEF_RAW_JSON_MAX_BYTES = 64 * 1024
DECISION_BRIEF_GOAL_ALIGNMENT_FIELDS = (
    "original_objective",
    "unmet_criteria",
    "latest_result",
    "next_action_alignment",
    "scope_drift",
    "method",
)


def _brief_text(value, limit: int) -> str | None:
    """One bounded non-empty string, or None; longer input is truncated."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    return text if len(text) <= limit else text[:limit]


def _brief_message_id(value) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


def _latest_human_review_decision(state: dict) -> tuple:
    """Latest committed HUMAN_REVIEW decision record, latest history entry
    first, falling back to the legacy last_supervisor_decision mirror.

    Latest-wins is what binds the brief to the CURRENT review: a new review
    with a different task identity replaces the projected brief instead of
    leaking a previous one.
    """
    history = state.get("decision_history")
    if isinstance(history, list):
        for index in range(len(history) - 1, -1, -1):
            entry = history[index]
            if isinstance(entry, dict) \
                    and entry.get("decision") == "HUMAN_REVIEW":
                return entry, "decision_history", index, \
                    index == len(history) - 1
    last = state.get("last_supervisor_decision")
    if isinstance(last, dict) and last.get("decision") == "HUMAN_REVIEW":
        return last, "last_supervisor_decision", None, None
    return None, None, None, None


def _brief_trigger_task(state: dict, record: dict | None,
                        history_index: int | None) -> dict:
    """Identity of the task this review is about.

    The HUMAN_REVIEW decision record usually carries no task identity, so the
    trigger is projected mechanically: the record's own identity when bound,
    otherwise the most recent earlier decision that authorized a task. Both
    are labeled with their exact source; nothing is inferred beyond them.
    """
    trigger = {"available": False, "source": None,
               "from_decision_index": None, "message_id": None,
               "task_id": None, "stage_id": None,
               "authorized_by": None, "authorized_at": None}
    if isinstance(record, dict) and (
            _brief_message_id(record.get("message_id")) is not None
            or _brief_text(record.get("task_id"),
                           DECISION_BRIEF_IDENTITY_MAX) is not None):
        trigger.update({
            "available": True, "source": "decision_record",
            "message_id": _brief_message_id(record.get("message_id")),
            "task_id": _brief_text(record.get("task_id"),
                                   DECISION_BRIEF_IDENTITY_MAX),
            "stage_id": _brief_text(record.get("stage_id"),
                                    DECISION_BRIEF_IDENTITY_MAX),
        })
        return trigger
    history = state.get("decision_history")
    if not isinstance(history, list):
        return trigger
    limit = history_index if isinstance(history_index, int) else len(history)
    for index in range(limit - 1, -1, -1):
        entry = history[index]
        if not isinstance(entry, dict):
            continue
        message_id = _brief_message_id(entry.get("message_id"))
        task_id = _brief_text(entry.get("task_id"), DECISION_BRIEF_IDENTITY_MAX)
        if message_id is None and task_id is None:
            continue
        trigger.update({
            "available": True, "source": "previous_authorized_decision",
            "from_decision_index": index, "message_id": message_id,
            "task_id": task_id,
            "stage_id": _brief_text(entry.get("stage_id"),
                                    DECISION_BRIEF_IDENTITY_MAX),
            "authorized_by": _brief_text(entry.get("decision"),
                                         DECISION_BRIEF_IDENTITY_MAX),
            "authorized_at": _brief_text(entry.get("at"),
                                         DECISION_BRIEF_REASON_MAX),
        })
        return trigger
    return trigger


def human_review_decision_brief(state) -> dict:
    """Project the authoritative Supervisor HUMAN_REVIEW decision record.

    `state` is the bounded server-side read of the active project's
    project_state.json (None when absent, oversized, or unparsable). The
    brief only re-labels and bounds what the Runtime already committed; it
    never summarizes, paraphrases, invents, or writes back. Every content
    field is None (rendered "unavailable") unless the authoritative record
    carries it. An optional dedicated `suggestion` field is surfaced verbatim
    when a record carries one and is reported as explicitly absent otherwise
    — the Console never derives a suggestion from other fields.
    """
    brief = {
        "schema_version": DECISION_BRIEF_SCHEMA_VERSION,
        "state_available": isinstance(state, dict),
        "state_in_human_review": isinstance(state, dict)
        and state.get("status") == "HUMAN_REVIEW",
        "inactive_reason": None,
        "available": False,
        "decision_record": {"found": False, "source": None,
                            "history_index": None,
                            "is_latest_history_entry": None,
                            "decision": None, "committed_at": None,
                            "scope": None, "message_id": None,
                            "task_id": None, "stage_id": None},
        "reason": None,
        "trigger_task": {"available": False, "source": None,
                         "from_decision_index": None, "message_id": None,
                         "task_id": None, "stage_id": None,
                         "authorized_by": None, "authorized_at": None},
        "alignment_available": False,
        "alignment_malformed": False,
        "goal_alignment": {name: None
                           for name in DECISION_BRIEF_GOAL_ALIGNMENT_FIELDS},
        "suggestion": {"available": False, "text": None},
        "truncated_fields": [],
        "raw_decision_available": False,
        "raw_decision_unavailable_reason": None,
        "raw_decision": None,
    }
    if not isinstance(state, dict):
        brief["inactive_reason"] = "PROJECT_STATE_UNAVAILABLE"
        return brief
    if state.get("status") != "HUMAN_REVIEW":
        # Not in review (or a stale brief would otherwise leak): nothing is
        # projected, even when old HUMAN_REVIEW decisions remain in history.
        brief["inactive_reason"] = "PROJECT_NOT_IN_HUMAN_REVIEW"
        return brief
    record, source, index, is_latest = _latest_human_review_decision(state)
    if record is None:
        # In HUMAN_REVIEW with no structured decision record (legacy or
        # hand-edited state): stay honest — the brief stays unavailable.
        return brief
    brief["available"] = True
    entry = brief["decision_record"]
    entry.update({
        "found": True, "source": source, "history_index": index,
        "is_latest_history_entry": is_latest,
        "decision": _brief_text(record.get("decision"),
                                DECISION_BRIEF_IDENTITY_MAX),
        "committed_at": _brief_text(record.get("at"),
                                    DECISION_BRIEF_REASON_MAX),
        "scope": _brief_text(record.get("scope"), DECISION_BRIEF_SCOPE_MAX),
        "message_id": _brief_message_id(record.get("message_id")),
        "task_id": _brief_text(record.get("task_id"),
                               DECISION_BRIEF_IDENTITY_MAX),
        "stage_id": _brief_text(record.get("stage_id"),
                                DECISION_BRIEF_IDENTITY_MAX),
    })
    truncated = []
    for key, bound in (("scope", DECISION_BRIEF_SCOPE_MAX),):
        value = record.get(key)
        if isinstance(value, str) and value.strip() \
                and len(value.strip()) > bound:
            truncated.append(f"decision_record.{key}")
    reason = _brief_text(record.get("reason"), DECISION_BRIEF_REASON_MAX)
    if isinstance(record.get("reason"), str) \
            and len(record["reason"].strip()) > DECISION_BRIEF_REASON_MAX:
        truncated.append("decision_record.reason")
    brief["reason"] = reason
    brief["trigger_task"] = _brief_trigger_task(state, record, index)
    if "goal_alignment" in record \
            and not isinstance(record.get("goal_alignment"), dict):
        brief["alignment_malformed"] = True
    alignment = record.get("goal_alignment")
    if isinstance(alignment, dict):
        fields = brief["goal_alignment"]
        for name in DECISION_BRIEF_GOAL_ALIGNMENT_FIELDS:
            fields[name] = _brief_text(alignment.get(name),
                                       DECISION_BRIEF_ALIGNMENT_MAX)
            if isinstance(alignment.get(name), str) \
                    and len(alignment.get(name).strip()) \
                    > DECISION_BRIEF_ALIGNMENT_MAX:
                truncated.append(f"goal_alignment.{name}")
        brief["alignment_available"] = all(
            fields[name] is not None
            for name in DECISION_BRIEF_GOAL_ALIGNMENT_FIELDS)
    suggestion = _brief_text(record.get("suggestion"),
                             DECISION_BRIEF_ALIGNMENT_MAX)
    brief["suggestion"] = {"available": suggestion is not None,
                           "text": suggestion}
    if isinstance(record.get("suggestion"), str) \
            and len(record["suggestion"].strip()) \
            > DECISION_BRIEF_ALIGNMENT_MAX:
        truncated.append("decision_record.suggestion")
    brief["truncated_fields"] = truncated
    try:
        encoded = json.dumps(record, ensure_ascii=False)
    except (TypeError, ValueError):
        brief["raw_decision_unavailable_reason"] = "RAW_RECORD_NOT_SERIALIZABLE"
        return brief
    if len(encoded.encode("utf-8")) > DECISION_BRIEF_RAW_JSON_MAX_BYTES:
        brief["raw_decision_unavailable_reason"] = \
            "RAW_RECORD_EXCEEDS_DISPLAY_BOUND"
        return brief
    brief["raw_decision_available"] = True
    brief["raw_decision"] = record
    return brief


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


# -- Automatic resume after a formal Human Decision Apply --------------------

def _blocked(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def auto_resume_gate_blocked(code: str, message: str) -> dict:
    """One canonical refused-gate document for a reason the HTTP layer
    established outside the pure fact inputs (e.g. another start holding the
    in-process start lock)."""
    return {"schema_version": AUTO_RESUME_SCHEMA_VERSION, "allowed": False,
            "receipt_id": None, "project_id": None,
            "blocked": [_blocked(code, message)]}


def evaluate_auto_resume_gate(status, runtime_document, *, live_owner,
                              owner_error,
                              expected_receipt_id=None) -> dict:
    """Mechanical safety gate for starting the Runtime after a Human
    Decision Apply.

    Inputs are authoritative Runtime facts only, gathered by the HTTP layer:
    `status` is one `supervisor_control status --json` document (None when
    that probe failed), `runtime_document` is the bounded read of the
    Runtime's own `control/orchestrator_runtime.json` (None when unusable),
    `live_owner`/`owner_error` are the Runtime's own scheduler-ownership
    liveness verdict for `control/.orchestrator.lock` (a dict when a live
    owner exists, None otherwise, with `owner_error` set when liveness can
    not be proven), and `expected_receipt_id` binds the gate to the exact
    receipt the caller just applied (None on the retry path, which binds to
    whatever receipt the persisted event carries).

    The gate proves, fail-closed: the apply is really committed (project is
    SUPERVISOR_TURN, HUMAN_REVIEW is gone), the durable
    HUMAN_DECISION_RESUME event is persisted and bound to that receipt and
    to the active project, control/STOP is absent, and no live Orchestrator
    owner exists. Anything that cannot be proven is a refusal — the apply
    itself is never rolled back and the Runtime is never started twice:
    ownership is ultimately arbitrated by orchestrator.py's exclusive-create
    lock, which this gate only refuses to race against.
    """
    blocked = []
    project_id = None
    if not isinstance(status, dict) or not status:
        blocked.append(_blocked(
            "STATUS_UNAVAILABLE",
            "the Runtime status document is unavailable; the mechanical "
            "safety proof cannot be established"))
        status = {}
    else:
        if status.get("stop") is True:
            blocked.append(_blocked(
                "STOP_PRESENT",
                "control/STOP exists; an automatic resume is forbidden and "
                "STOP needs its documented recovery path"))
        if status.get("human_review") is True:
            blocked.append(_blocked(
                "HUMAN_REVIEW_ACTIVE",
                "HUMAN_REVIEW is still active; the decision apply is not "
                "proven"))
        project_status = status.get("project_status")
        if project_status != "SUPERVISOR_TURN":
            blocked.append(_blocked(
                "PROJECT_NOT_SUPERVISOR_TURN",
                "the project has not committed SUPERVISOR_TURN (reported: "
                f"{project_status!r}); the decision apply is not proven"))
        project_id = status.get("PROJECT_ID")
        if not isinstance(project_id, str) or not project_id:
            blocked.append(_blocked(
                "PROJECT_ID_UNAVAILABLE",
                "the active project identity is unavailable"))
            project_id = None

    event = runtime_document.get("pending_supervisor_event") \
        if isinstance(runtime_document, dict) else None
    event_inner = event.get("event") if isinstance(event, dict) else None
    if not isinstance(runtime_document, dict):
        blocked.append(_blocked(
            "RESUME_EVENT_UNAVAILABLE",
            "control/orchestrator_runtime.json is unavailable or unusable; "
            "the persisted resume event cannot be proven"))
        event_inner = None
    elif not (isinstance(event, dict)
              and event.get("reason") == HUMAN_DECISION_RESUME_REASON
              and isinstance(event_inner, dict)
              and isinstance(event_inner.get("receipt_id"), str)
              and isinstance(event_inner.get("project_id"), str)):
        blocked.append(_blocked(
            "RESUME_EVENT_MISSING",
            "no persisted HUMAN_DECISION_RESUME pending event exists; the "
            "applied decision was already consumed or never committed"))
        event_inner = None

    receipt_id = None
    if isinstance(event_inner, dict):
        receipt_id = event_inner["receipt_id"]
        if expected_receipt_id is not None \
                and receipt_id != expected_receipt_id:
            blocked.append(_blocked(
                "RECEIPT_MISMATCH",
                "the persisted resume event belongs to receipt "
                f"{receipt_id!r}, not to the receipt just applied "
                f"({expected_receipt_id!r})"))
        if isinstance(project_id, str) \
                and event_inner["project_id"] != project_id:
            blocked.append(_blocked(
                "PROJECT_MISMATCH",
                "the active project does not match the project bound to the "
                f"persisted decision event ({event_inner['project_id']!r})"))

    if owner_error is not None:
        blocked.append(_blocked(
            "OWNER_UNVERIFIABLE",
            "scheduler ownership cannot be verified; failing closed "
            f"({owner_error})"))
    elif isinstance(live_owner, dict):
        blocked.append(_blocked(
            "LIVE_OWNER",
            "an Orchestrator owner is already live (pid "
            f"{live_owner.get('pid')!r}); it is servicing the decision and "
            "a second start is not allowed"))
    return {"schema_version": AUTO_RESUME_SCHEMA_VERSION,
            "allowed": not blocked, "receipt_id": receipt_id,
            "project_id": project_id if isinstance(project_id, str) else None,
            "blocked": blocked}


def applied_resume_facts(gate: dict) -> dict:
    """Project one auto-resume gate verdict for the controls document.

    `event_pending` is True exactly when the durable resume event is
    readable — that is the fact "a Human Decision was formally applied and
    has not been serviced yet". `owner_live` distinguishes "the Runtime is
    already running and will consume it" from "the Runtime is not resumed",
    so the UI can show an informational state instead of demanding action.
    """
    if not isinstance(gate, dict):
        # Nothing provable at all: never claim the event exists.
        return {"schema_version": AUTO_RESUME_SCHEMA_VERSION,
                "event_pending": False, "receipt_id": None,
                "project_id": None, "auto_resume_allowed": False,
                "owner_live": False,
                "blocked": [_blocked(
                    "GATE_UNAVAILABLE",
                    "the auto-resume gate returned no verdict")]}
    if not gate.get("receipt_id"):
        # Without the event's receipt binding the event is not proven.
        codes = {item.get("code") for item in gate.get("blocked", [])
                 if isinstance(item, dict)}
        if not codes & {"RESUME_EVENT_UNAVAILABLE", "RESUME_EVENT_MISSING"}:
            gate = {**gate, "allowed": False, "receipt_id": None,
                    "blocked": [*gate.get("blocked", []), _blocked(
                        "RESUME_EVENT_UNAVAILABLE",
                        "the persisted resume event is not proven")]}
    codes = {item.get("code") for item in gate.get("blocked", [])
             if isinstance(item, dict)}
    return {
        "schema_version": AUTO_RESUME_SCHEMA_VERSION,
        "event_pending": not codes & {"RESUME_EVENT_UNAVAILABLE",
                                      "RESUME_EVENT_MISSING"},
        "receipt_id": gate.get("receipt_id"),
        "project_id": gate.get("project_id"),
        "auto_resume_allowed": gate.get("allowed") is True,
        "owner_live": "LIVE_OWNER" in codes,
        "blocked": [item for item in gate.get("blocked", [])
                    if isinstance(item, dict)],
    }
