"""Read-only Timeline/round projection for the v1.3 Web Console (P4).

A pure, offline layer between the v1.2 control plane's history documents and
the human-readable Timeline / Task Detail presentations.

Inputs (produced elsewhere by bounded `supervisor_control.py --json`
subprocesses; this module never touches the filesystem, the clock, randomness,
or subprocesses):

- the `timeline --json` merged event list
  (`[{type, at, MESSAGE_ID, record}, …]` with types `SUPERVISOR_DISPATCH`,
  `EXECUTOR_COMPLETION`, `HUMAN_INTERVENTION`);
- the `tasks --message-id <id> --json` dispatch record (which carries
  `exact_dispatch`, the exact archived TO_ZCODE text, only for
  `AUTHORIZED_VALID` archives);
- the `feedback --message-id <id> --json` completion-ledger entry (including
  the authoritative executor receipt);
- the `interventions --json` intervention records.

Hard contracts:

- Deterministic: the same inputs always produce the same output document.
- Fail closed on queries: unknown/duplicate/out-of-bounds parameters raise
  `TimelineQueryError` instead of being coerced.
- Facts only: statuses and timestamps come verbatim from the records; a
  duration is derived only from two authoritative ISO timestamps; no
  percentages, token usage, or invented timestamps exist anywhere in the
  output.
- Corrupt, contradictory, incomplete, and unbound records are surfaced
  through structured honesty blocks (bounded lists plus exact counts) —
  never silently skipped and never presented as trusted history.
"""
from __future__ import annotations

import math
import re
import urllib.parse
from datetime import datetime

TIMELINE_SCHEMA_VERSION = 1

TYPE_DISPATCH = "SUPERVISOR_DISPATCH"
TYPE_COMPLETION = "EXECUTOR_COMPLETION"
TYPE_INTERVENTION = "HUMAN_INTERVENTION"
KNOWN_EVENT_TYPES = (TYPE_DISPATCH, TYPE_COMPLETION, TYPE_INTERVENTION)

KIND_DISPATCH = "dispatch"
KIND_COMPLETION = "completion"
KIND_INTERVENTION = "intervention"
KIND_ALL = "all"
KNOWN_KINDS = (KIND_ALL, KIND_DISPATCH, KIND_COMPLETION, KIND_INTERVENTION)
KIND_TO_EVENT_TYPE = {
    KIND_DISPATCH: TYPE_DISPATCH,
    KIND_COMPLETION: TYPE_COMPLETION,
    KIND_INTERVENTION: TYPE_INTERVENTION,
}

ORDERS = ("newest", "oldest")

DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE = 10_000_000
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 100
MAX_SEARCH_CHARS = 120
MAX_UNUSABLE_LISTED = 20
MAX_UNBOUND_LISTED = 20
MAX_ROUND_INTERVENTIONS_LISTED = 10
MAX_ARTIFACTS_LISTED = 64
MAX_TEXT_RELAY_CHARS = 2000

_MAX_DISPATCHES_PER_ROUND = 1
_MAX_COMPLETIONS_PER_ROUND = 1


class TimelineQueryError(ValueError):
    """A timeline query parameter failed fail-closed validation."""


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value) -> bool:
    return isinstance(value, str)


def parse_timeline_query(query: str) -> dict:
    """Validate the timeline query string; return normalized parameters."""
    raw = urllib.parse.parse_qs(query if isinstance(query, str) else "",
                                keep_blank_values=True)
    allowed = {"page", "page_size", "order", "kind", "q"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise TimelineQueryError(
            f"unknown timeline query parameter(s): {', '.join(unknown)}")
    params = {"page": DEFAULT_PAGE, "page_size": DEFAULT_PAGE_SIZE,
              "order": "newest", "kind": KIND_ALL, "q": ""}
    for key, values in raw.items():
        if len(values) != 1:
            raise TimelineQueryError(
                f"timeline query parameter {key!r} must appear exactly once")
        value = values[0]
        if key == "page":
            if not re.fullmatch(r"[0-9]{1,8}", value):
                raise TimelineQueryError(
                    "page must be a positive integer")
            params["page"] = int(value)
            if not 1 <= params["page"] <= MAX_PAGE:
                raise TimelineQueryError(
                    f"page must be between 1 and {MAX_PAGE}")
        elif key == "page_size":
            if not re.fullmatch(r"[0-9]{1,4}", value):
                raise TimelineQueryError(
                    "page_size must be a positive integer")
            params["page_size"] = int(value)
            if not MIN_PAGE_SIZE <= params["page_size"] <= MAX_PAGE_SIZE:
                raise TimelineQueryError(
                    f"page_size must be between {MIN_PAGE_SIZE} and "
                    f"{MAX_PAGE_SIZE}")
        elif key == "order":
            if value not in ORDERS:
                raise TimelineQueryError(
                    "order must be 'newest' or 'oldest'")
            params["order"] = value
        elif key == "kind":
            if value not in KNOWN_KINDS:
                raise TimelineQueryError(
                    "kind must be one of: " + ", ".join(KNOWN_KINDS))
            params["kind"] = value
        elif key == "q":
            value = value.strip()
            if len(value) > MAX_SEARCH_CHARS:
                raise TimelineQueryError(
                    f"q must be at most {MAX_SEARCH_CHARS} characters")
            params["q"] = value
    return params


def _timestamp_of(record: dict) -> str | None:
    """The one authoritative display timestamp of a timeline record."""
    for key in ("archived_at", "COMMITTED_AT", "submitted_at"):
        value = record.get(key)
        if _is_str(value) and value:
            return value
    return None


def _bounded_text(value, limit: int = MAX_TEXT_RELAY_CHARS):
    if not _is_str(value):
        return None
    return value if len(value) <= limit else value[:limit]


class _Round:
    __slots__ = ("message_id", "dispatches", "completions", "interventions")

    def __init__(self, message_id: int):
        self.message_id = message_id
        self.dispatches = []
        self.completions = []
        self.interventions = []


def _collect_groups(events) -> tuple[dict, list, int, list, int, list]:
    """Split the raw timeline event list into per-round groups plus honesty
    lists. Returns (groups, unusable, unusable_total, unbound, unbound_total,
    notes). The lists are bounded; the totals are exact."""
    groups: dict[int, _Round] = {}
    unusable: list = []
    unusable_total = 0
    unbound: list = []
    unbound_total = 0
    if not isinstance(events, list):
        return groups, [{
            "reason_code": "TIMELINE_NOT_A_LIST", "message_id": None,
            "event_type": None,
        }], 1, unbound, 0, ["the control-plane timeline document was not a list"]
    for event in events:
        if not isinstance(event, dict):
            unusable_total += 1
            if len(unusable) < MAX_UNUSABLE_LISTED:
                unusable.append({"reason_code": "EVENT_NOT_AN_OBJECT",
                                 "message_id": None, "event_type": None})
            continue
        event_type = event.get("type")
        record = event.get("record")
        if event_type not in KNOWN_EVENT_TYPES:
            unusable_total += 1
            if len(unusable) < MAX_UNUSABLE_LISTED:
                unusable.append({"reason_code": "EVENT_TYPE_UNKNOWN",
                                 "message_id": _display_id(event.get("MESSAGE_ID")),
                                 "event_type":
                                     _bounded_text(event_type, 60)})
            continue
        if not isinstance(record, dict):
            unusable_total += 1
            if len(unusable) < MAX_UNUSABLE_LISTED:
                unusable.append({"reason_code": "EVENT_RECORD_INVALID",
                                 "message_id": _display_id(event.get("MESSAGE_ID")),
                                 "event_type": event_type})
            continue
        if event_type == TYPE_INTERVENTION:
            target = record.get("target_message_id")
            if target is None:
                unbound_total += 1
                if len(unbound) < MAX_UNBOUND_LISTED:
                    unbound.append({
                        "intervention_id":
                            _bounded_text(record.get("intervention_id"), 120),
                        "mode": _bounded_text(record.get("mode"), 40),
                        "submitted_at": _timestamp_of(record),
                    })
                continue
            message_id = target
        else:
            message_id = record.get("MESSAGE_ID")
        if not _is_int(message_id):
            unusable_total += 1
            if len(unusable) < MAX_UNUSABLE_LISTED:
                unusable.append({"reason_code": "EVENT_MESSAGE_ID_INVALID",
                                 "message_id": None,
                                 "event_type": event_type})
            continue
        group = groups.get(message_id)
        if group is None:
            group = groups[message_id] = _Round(message_id)
        if event_type == TYPE_DISPATCH:
            group.dispatches.append(record)
        elif event_type == TYPE_COMPLETION:
            group.completions.append(record)
        else:
            group.interventions.append(record)
    notes = []
    if unusable_total:
        notes.append(f"{unusable_total} timeline event(s) were unusable and "
                     "are surfaced in honesty.unusable_events")
    if unbound_total:
        notes.append(f"{unbound_total} intervention(s) target no MESSAGE_ID "
                     "and are surfaced in honesty.unbound_events")
    return groups, unusable, unusable_total, unbound, unbound_total, notes


def _display_id(value):
    return value if _is_int(value) else None


def _parse_iso(value):
    if not _is_str(value) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _identity_fields(record: dict | None) -> dict:
    if not isinstance(record, dict):
        return {"task_id": None, "stage_id": None, "attempt": None}
    return {
        "task_id": record.get("TASK_ID") if _is_str(record.get("TASK_ID")) else None,
        "stage_id": record.get("STAGE_ID") if _is_str(record.get("STAGE_ID")) else None,
        "attempt": record.get("ATTEMPT") if _is_int(record.get("ATTEMPT")) else None,
    }


def project_round(group: _Round) -> dict:
    """Project one round's grouped records into its bounded presentation."""
    contradictions = []
    notes = []
    dispatches = group.dispatches
    completions = group.completions
    if len(dispatches) > _MAX_DISPATCHES_PER_ROUND:
        contradictions.append("MULTIPLE_DISPATCH_RECORDS")
    if len(completions) > _MAX_COMPLETIONS_PER_ROUND:
        contradictions.append("MULTIPLE_COMPLETION_RECORDS")
    dispatch = dispatches[0] if dispatches else None
    completion = completions[0] if completions else None

    dispatch_integrity = None
    if isinstance(dispatch, dict):
        integrity = dispatch.get("integrity")
        if _is_str(integrity):
            dispatch_integrity = integrity

    completion_integrity = None
    completion_status = None
    completion_receipt_status = None
    if isinstance(completion, dict):
        integrity = completion.get("integrity")
        completion_integrity = integrity if _is_str(integrity) else None
        status = completion.get("STATUS")
        completion_status = status if _is_str(status) else None
        receipt = completion.get("RECEIPT")
        if isinstance(receipt, dict) and _is_str(receipt.get("STATUS")):
            completion_receipt_status = receipt.get("STATUS")

    # Status derivation (facts only; integrity problems outrank the happy path).
    if contradictions:
        status = "CONTRADICTORY_HISTORY"
    elif completion is not None and completion_integrity != "OK":
        status = "COMPLETION_UNTRUSTED"
    elif completion is not None and dispatch is not None:
        status = "COMPLETED"
    elif completion is not None:
        status = "COMPLETION_WITHOUT_DISPATCH"
    elif dispatch is not None and dispatch_integrity != "AUTHORIZED_VALID":
        status = "DISPATCH_UNTRUSTED"
    elif dispatch is not None:
        status = "DISPATCHED_NO_COMPLETION"
    else:
        status = "INTERVENTION_ONLY"

    # Identity prefers the dispatch record; a conflicting completion identity
    # is a surfaced contradiction, never silently merged.
    identity = {"message_id": group.message_id, **_identity_fields(dispatch)}
    if isinstance(completion, dict):
        completion_identity = _identity_fields(completion)
        for key in ("task_id", "stage_id", "attempt"):
            if completion_identity[key] is not None \
                    and identity[key] is not None \
                    and completion_identity[key] != identity[key]:
                contradictions.append(f"IDENTITY_MISMATCH_{key.upper()}")
            elif identity[key] is None:
                identity[key] = completion_identity[key]

    duration_seconds = None
    archived = _parse_iso(dispatch.get("archived_at")) \
        if isinstance(dispatch, dict) else None
    committed = _parse_iso(completion.get("COMMITTED_AT")) \
        if isinstance(completion, dict) else None
    if archived is not None and committed is not None and committed >= archived:
        duration_seconds = int((committed - archived).total_seconds())

    stamps = [stamp for stamp in (
        dispatch.get("archived_at") if isinstance(dispatch, dict) else None,
        completion.get("COMMITTED_AT") if isinstance(completion, dict) else None,
        completion.get("CONSUMED_AT") if isinstance(completion, dict) else None,
        *(item.get("submitted_at") for item in group.interventions
          if isinstance(item, dict)),
    ) if _is_str(stamp) and stamp]
    latest_event_at = max(stamps) if stamps else None

    if group.interventions:
        notes.append(f"{len(group.interventions)} human intervention(s) are "
                     "bound to this round")
    if dispatch is not None and dispatch_integrity != "AUTHORIZED_VALID":
        notes.append("the archived dispatch did not verify as AUTHORIZED_VALID; "
                     "its exact bytes are not trusted for display")

    return {
        "message_id": group.message_id,
        "identity": identity,
        "status": status,
        "has_dispatch": dispatch is not None,
        "has_completion": completion is not None,
        "dispatch_integrity": dispatch_integrity,
        "completion_integrity": completion_integrity,
        "completion_status": completion_status,
        "completion_receipt_status": completion_receipt_status,
        "intervention_count": len(group.interventions),
        "interventions": [_intervention_summary(item)
                          for item in group.interventions[
                              :MAX_ROUND_INTERVENTIONS_LISTED]],
        "archived_at": _bounded_text(dispatch.get("archived_at"), 40)
        if isinstance(dispatch, dict) else None,
        "committed_at": _bounded_text(completion.get("COMMITTED_AT"), 40)
        if isinstance(completion, dict) else None,
        "latest_event_at": latest_event_at,
        "duration_seconds": duration_seconds,
        "honesty": {"contradictions": contradictions, "notes": notes},
    }


def _intervention_summary(record: dict) -> dict:
    return {
        "intervention_id": _bounded_text(record.get("intervention_id"), 120),
        "mode": _bounded_text(record.get("mode"), 40),
        "status": _bounded_text(record.get("status"), 40),
        "submitted_at": _bounded_text(record.get("submitted_at"), 40),
        "integrity": _bounded_text(record.get("integrity"), 40),
    }


def project_timeline(events, params: dict, *, generated_at: str) -> dict:
    """Project the raw timeline event list into one bounded, paginated,
    filtered Timeline document (schema_version 1)."""
    groups, unusable, unusable_total, unbound, unbound_total, notes = \
        _collect_groups(events)
    rounds = [project_round(group) for group in groups.values()]

    kind = params["kind"]
    if kind != KIND_ALL:
        if kind == KIND_INTERVENTION:
            rounds = [round_ for round_ in rounds
                      if round_["intervention_count"] > 0]
        else:
            rounds = [round_ for round_ in rounds if round_["has_" + kind]]

    needle = params["q"].lower()
    if needle:
        rounds = [
            round_ for round_ in rounds
            if needle in " ".join((
                str(round_["message_id"]),
                round_["identity"]["task_id"] or "",
                round_["identity"]["stage_id"] or "",
            )).lower()
        ]

    # Timestamped rounds sort by their authoritative timestamp; rounds
    # without any authoritative timestamp always sort last (deterministic
    # and keeps the latest page stable), in both orders.
    timestamped = [round_ for round_ in rounds
                   if round_["latest_event_at"] is not None]
    stampless = sorted((round_ for round_ in rounds
                        if round_["latest_event_at"] is None),
                       key=lambda round_: round_["message_id"],
                       reverse=(params["order"] == "newest"))
    reverse = params["order"] == "newest"
    timestamped.sort(key=lambda round_: (round_["latest_event_at"],
                                         round_["message_id"]),
                     reverse=reverse)
    rounds = timestamped + stampless

    total_rounds = len(rounds)
    page_size = params["page_size"]
    total_pages = max(1, math.ceil(total_rounds / page_size))
    page = params["page"]
    start = (page - 1) * page_size
    visible = rounds[start:start + page_size]

    return {
        "schema_version": TIMELINE_SCHEMA_VERSION,
        "generated_at": generated_at,
        "query": {
            "page": page,
            "page_size": page_size,
            "order": params["order"],
            "kind": kind,
            "q": params["q"],
        },
        "totals": {
            "rounds": total_rounds,
            "pages": total_pages,
            "events_ingested": len(events) if isinstance(events, list) else 0,
            "unusable_events": unusable_total,
            "unbound_events": unbound_total,
        },
        "rounds": visible,
        "honesty": {
            "unusable_events": unusable,
            "unbound_events": unbound,
            "notes": list(notes),
        },
    }


# ---------------------------------------------------------------------------
# Task Detail (round) composition
# ---------------------------------------------------------------------------

def _control_note(result: dict, label: str) -> dict | None:
    failure = result.get("failure") if isinstance(result, dict) else None
    if failure == "TIMEOUT":
        return {"source": label, "code": f"{label.upper()}_TIMEOUT"}
    if failure == "LAUNCH_FAILED":
        return {"source": label, "code": f"{label.upper()}_LAUNCH_FAILED"}
    if failure == "OUTPUT_TOO_LARGE":
        return {"source": label, "code": f"{label.upper()}_OUTPUT_TOO_LARGE"}
    if failure == "UNPARSEABLE":
        return {"source": label, "code": f"{label.upper()}_UNPARSEABLE"}
    return None


def _dispatch_block(dispatch_result: dict, honesty_notes: list,
                    control_notes: list) -> dict:
    note = _control_note(dispatch_result, "dispatch")
    if note:
        control_notes.append(note)
    document = dispatch_result.get("document")
    if note or not isinstance(document, dict):
        if not note and document is not None:
            honesty_notes.append("the dispatch query returned unusable output")
        return {"available": False, "integrity": None, "trust_status": None,
                "exact_dispatch": None, "error": None}
    ok_false = document.get("ok") is False
    integrity = document.get("integrity") \
        if _is_str(document.get("integrity")) else None
    exact = document.get("exact_dispatch")
    if ok_false:
        return {"available": False, "integrity": None, "trust_status": None,
                "exact_dispatch": None, "error": None}
    if integrity != "AUTHORIZED_VALID":
        honesty_notes.append(
            "the archived dispatch did not verify as AUTHORIZED_VALID; its "
            "exact bytes are not relayed")
    return {
        "available": True,
        "integrity": integrity,
        "trust_status": document.get("trust_status")
        if _is_str(document.get("trust_status")) else integrity,
        "exact_dispatch": exact if _is_str(exact) else None,
        "error": _bounded_text(document.get("error")),
        "archived_at": _bounded_text(document.get("archived_at"), 40),
        "dispatch_sha256": document.get("dispatch_sha256")
        if _is_str(document.get("dispatch_sha256")) else None,
        "supervisor_turn_id": document.get("supervisor_turn_id")
        if _is_str(document.get("supervisor_turn_id")) else None,
        "decision_receipt_sha256": document.get("decision_receipt_sha256")
        if _is_str(document.get("decision_receipt_sha256")) else None,
        "originating_control_revision":
            document.get("originating_control_revision")
            if _is_int(document.get("originating_control_revision")) else None,
        "metadata_file": _bounded_text(document.get("metadata_file"), 300),
        "archive_file": _bounded_text(document.get("archive_file"), 300),
        "authorization_file": _bounded_text(document.get("authorization_file"),
                                            300),
    }


def _completion_block(completion_result: dict, honesty_notes: list,
                      control_notes: list) -> dict:
    note = _control_note(completion_result, "completion")
    if note:
        control_notes.append(note)
    document = completion_result.get("document")
    unavailable = {"available": False, "status": None, "integrity": None,
                   "committed_at": None, "consumed_at": None,
                   "sealed_at": None, "commit_id": None, "claim_dir": None,
                   "ledger_file": None, "receipt": None,
                   "receipt_status": None, "receipt_outcome": None}
    if note or not isinstance(document, dict) or document.get("ok") is False:
        if not note and document is not None and document.get("ok") is False:
            honesty_notes.append("the completion ledger has no usable entry "
                                 "for this MESSAGE_ID")
        return unavailable
    receipt = document.get("RECEIPT") \
        if isinstance(document.get("RECEIPT"), dict) else None
    status = document.get("STATUS") if _is_str(document.get("STATUS")) else None
    return {
        "available": True,
        "status": status,
        "integrity": document.get("integrity")
        if _is_str(document.get("integrity")) else None,
        "committed_at": _bounded_text(document.get("COMMITTED_AT"), 40),
        "consumed_at": _bounded_text(document.get("CONSUMED_AT"), 40),
        "sealed_at": _bounded_text(document.get("SEALED_AT"), 40),
        "commit_id": _bounded_text(document.get("COMMIT_ID"), 160),
        "claim_dir": _bounded_text(document.get("CLAIM_DIR"), 300),
        "ledger_file": _bounded_text(document.get("ledger_file"), 300),
        "receipt": receipt,
        "receipt_status": receipt.get("STATUS")
        if receipt is not None and _is_str(receipt.get("STATUS")) else None,
        "receipt_outcome": _bounded_text(receipt.get("OUTCOME"))
        if receipt is not None else None,
    }


def _interventions_block(interventions_result: dict, message_id: int,
                         honesty_notes: list, control_notes: list) -> list:
    note = _control_note(interventions_result, "interventions")
    if note:
        control_notes.append(note)
    document = interventions_result.get("document")
    if note or not isinstance(document, list):
        if not note and document is not None:
            honesty_notes.append("the interventions query returned unusable "
                                 "output")
        return []
    matched = []
    for record in document:
        if not isinstance(record, dict):
            continue
        if record.get("target_message_id") != message_id:
            continue
        matched.append({
            "intervention_id": _bounded_text(record.get("intervention_id"),
                                             120),
            "mode": _bounded_text(record.get("mode"), 40),
            "status": _bounded_text(record.get("status"), 40),
            "submitted_at": _bounded_text(record.get("submitted_at"), 40),
            "integrity": _bounded_text(record.get("integrity"), 40),
            "interrupt_current": record.get("interrupt_current")
            if isinstance(record.get("interrupt_current"), bool) else None,
            "instruction_text": _bounded_text(record.get("instruction_text")),
        })
        if len(matched) >= MAX_ROUND_INTERVENTIONS_LISTED:
            honesty_notes.append("the intervention list for this round is "
                                 "longer than the bounded display cap")
            break
    return matched


def _artifacts_block(document: dict) -> dict:
    from web_console_artifacts import verified_publications
    publications, reason = verified_publications(document if isinstance(document, dict) else {})
    return {
        "available": reason is None,
        "reason": reason,
        "paths": [{"path": item["path"], "sha256": item["sha256"]}
                  for item in publications[:MAX_ARTIFACTS_LISTED]],
        "note": reason or ("Verified Runtime publication metadata; historical bytes are not retained. "
                           "Current-content preview is available in Artifact Center only when its hash matches this publication."),
    }


def interpret_round_detail(message_id: int, *, dispatch_result: dict,
                           completion_result: dict,
                           interventions_result: dict,
                           decision: dict) -> dict:
    """Compose one Task Detail document from the bounded history sources.

    `decision` is composed separately by the server (it performs the single
    bounded, hash-verified decision-receipt read) and is relayed verbatim.
    """
    honesty_notes: list = []
    control_notes: list = []
    dispatch = _dispatch_block(dispatch_result, honesty_notes, control_notes)
    completion = _completion_block(completion_result, honesty_notes,
                                   control_notes)
    interventions = _interventions_block(interventions_result, message_id,
                                         honesty_notes, control_notes)

    found = dispatch["available"] or completion["available"] \
        or bool(interventions)

    identity = {"message_id": message_id, "task_id": None, "stage_id": None,
                "attempt": None, "nonce": None}
    contradictions = []
    document = dispatch_result.get("document")
    if dispatch["available"] and isinstance(document, dict):
        identity["task_id"] = document.get("TASK_ID") \
            if _is_str(document.get("TASK_ID")) else None
        identity["stage_id"] = document.get("STAGE_ID") \
            if _is_str(document.get("STAGE_ID")) else None
        identity["attempt"] = document.get("ATTEMPT") \
            if _is_int(document.get("ATTEMPT")) else None
        identity["nonce"] = document.get("NONCE") \
            if _is_str(document.get("NONCE")) else None
    completion_document = completion_result.get("document")
    if completion["available"] and isinstance(completion_document, dict):
        for record_key, identity_key in (("TASK_ID", "task_id"),
                                         ("STAGE_ID", "stage_id"),
                                         ("ATTEMPT", "attempt"),
                                         ("NONCE", "nonce")):
            value = completion_document.get(record_key)
            normalized = value if (_is_str(value) or _is_int(value)) else None
            if normalized is not None and identity[identity_key] is not None \
                    and normalized != identity[identity_key]:
                contradictions.append(f"IDENTITY_MISMATCH_{record_key}")
            elif identity[identity_key] is None and normalized is not None:
                identity[identity_key] = normalized

    if not found:
        honesty_notes.append("no authoritative history source has a record "
                             "for this MESSAGE_ID")
    return {
        "message_id": message_id,
        "found": found,
        "identity": identity,
        "dispatch": dispatch,
        "completion": completion,
        "interventions": interventions,
        "artifacts": _artifacts_block(completion_result.get("document") if not _control_note(completion_result, "completion") else {}),
        "decision": decision if isinstance(decision, dict) else
        {"available": False, "verified": False, "reason": "DECISION_UNAVAILABLE"},
        "honesty": {
            "notes": honesty_notes,
            "control": control_notes,
            "contradictions": contradictions,
        },
    }
