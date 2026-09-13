"""P8 Supervisor-turn observability pure layer for the v1.3 Web Console.

This module holds the deterministic, offline half of the Supervisor-turn
observability surface (spec §6, §9, §10, §15, §22): strict validation of the
Runtime-owned SUPERVISOR-TURN-OBSERVABILITY-V1 turn records, bounded
list/detail projections with receipt-binding verification, the honest
project-level usage summary, and the active-versus-pending Supervisor
configuration document backed by the Runtime configuration contract
(`control/supervisor_config.json`, queued through the formal
`queue-supervisor-config` subcommand).

It never touches the clock, randomness, or subprocesses — the HTTP layer in
`web_console_server.py` owns those — and it never writes Runtime state:
every file here is read-only. Corrupt, malformed, hostile, or contradictory
input fails closed to explicit unavailability; nothing is ever guessed,
repaired, or silently skipped.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.parse
from pathlib import Path, PurePosixPath

import web_console_control

SUPERVISOR_SCHEMA_VERSION = 1
TURN_RECORD_SCHEMA = "SUPERVISOR-TURN-OBSERVABILITY-V1"

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100
MAX_OFFSET = 10_000_000
MAX_TURN_RECORD_BYTES = 1024 * 1024
MAX_RECEIPT_BYTES = 1024 * 1024
MAX_TURN_WALK = 5000
MAX_UNUSABLE_LISTED = 20
MAX_RECENT_REPORTED_TURNS = 5
TURN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

# The Runtime configuration contract's supported reasoning-effort set. This
# is deliberately the subset of the P7 suggestion vocabulary that the
# Runtime's Codex integration applies today; the parity is pinned by tests
# against supervisor_control.SUPERVISOR_CONFIG_EFFORTS.
SUPPORTED_REASONING_EFFORTS = ("LOW", "MEDIUM", "HIGH")
SUPPORTED_EFFORTS_NOTE = (
    "the Runtime's Codex integration applies LOW, MEDIUM, and HIGH today; "
    "other reasoning-effort suggestions are refused deterministically "
    "instead of being applied unverified")
FIXED_POLICY_NOTE = (
    "no queued configuration is active; the Runtime's fixed Supervisor "
    "policy applies and its fixed model/effort values are not reported "
    "through a configuration interface")
PENDING_NOTE = (
    "the change is queued and applies at the next eligible Supervisor turn "
    "boundary; an active Supervisor call is never interrupted, replaced, or "
    "mutated")
DRAFT_NOTE = (
    "Console-owned setup draft (non-authoritative); it only prefills the "
    "form and never changes the Runtime by itself")
ZCODE_USAGE_NOTE = (
    "ZCode usage is not reported; the Runtime has no reliable "
    "authoritative ZCode token usage source")

_TURN_KEYS = {
    "schema", "schema_version", "turn_id", "PROJECT_ID", "invocation",
    "started_at", "finished_at", "duration_seconds", "supervisor_config",
    "usage", "context_manifest", "decision", "dispatch_linkage",
    "intervention_ids", "outcome",
}
_USAGE_KEYS = {"reported", "input_tokens", "output_tokens", "total_tokens",
               "source", "note"}
_CONFIG_SNAPSHOT_KEYS = {"source", "config_revision", "model",
                         "reasoning_effort", "queued_at"}
_DECISION_KEYS = {"committed", "receipt_file", "receipt_sha256", "decision",
                  "decision_sha256", "decision_summary",
                  "decision_history_index", "resulting_status"}
_OUTCOME_KEYS = {"committed", "stale", "recovered_after_crash",
                 "candidate_validation_failed", "error"}
_LINKAGE_KEYS = {"MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE",
                 "dispatch_sha256"}


class TurnRecordError(ValueError):
    """A turn-list query, record, or detail request that fails closed."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bounded_text(value, limit: int) -> bool:
    return value is None or (isinstance(value, str)
                             and (value == "" or len(value) <= limit))


def _canonical_json_sha256(value) -> str:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_json_object(value, *, max_keys: int, max_bytes: int) -> bool:
    if value is None:
        return True
    if not isinstance(value, dict) or len(value) > max_keys:
        return False
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return False
    return len(encoded.encode("utf-8")) <= max_bytes


def _manifest_scalar(value) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    return isinstance(value, str) and 0 < len(value) <= 300


def _manifest_value_ok(value, *, depth: int) -> bool:
    if _manifest_scalar(value):
        return True
    limit = 32 if isinstance(value, list) else 16
    if not isinstance(value, (list, dict)) or len(value) > limit or depth > 2:
        return False
    items = value.values() if isinstance(value, dict) else value
    for item in items:
        if depth >= 2:
            if not _manifest_scalar(item):
                return False
        elif not _manifest_value_ok(item, depth=depth + 1):
            return False
    return True


def _parse_turns_query_value(query: str) -> dict:
    params = urllib.parse.parse_qs(query or "", keep_blank_values=True)
    unknown = sorted(set(params) - {"limit", "offset"})
    if unknown:
        raise TurnRecordError(
            "TURNS_QUERY_INVALID", f"unknown query parameters: {unknown}")
    limit_raw = params.get("limit", [str(DEFAULT_PAGE_SIZE)])[0]
    offset_raw = params.get("offset", ["0"])[0]
    try:
        limit = int(limit_raw)
        offset = int(offset_raw)
    except (TypeError, ValueError):
        raise TurnRecordError(
            "TURNS_QUERY_INVALID", "limit and offset must be integers") from None
    if not 1 <= limit <= MAX_PAGE_SIZE:
        raise TurnRecordError(
            "TURNS_QUERY_INVALID", f"limit must be 1..{MAX_PAGE_SIZE}")
    if not 0 <= offset <= MAX_OFFSET:
        raise TurnRecordError(
            "TURNS_QUERY_INVALID", f"offset must be 0..{MAX_OFFSET}")
    return {"limit": limit, "offset": offset}


def parse_turns_query(query: str) -> dict:
    return _parse_turns_query_value(query)


def _validate_usage_block(usage) -> None:
    if not isinstance(usage, dict) or set(usage) != _USAGE_KEYS:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record usage block is invalid")
    if not isinstance(usage["reported"], bool):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record usage reported must be a boolean")
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage[key]
        if value is not None and (not _is_int(value) or value < 0):
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                f"turn record usage {key} must be a non-negative integer or "
                "null")
    reported = usage["reported"]
    tokens = [usage[key] for key in ("input_tokens", "output_tokens",
                                     "total_tokens")]
    if reported:
        if all(value is None for value in tokens):
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                "a reported usage block must carry at least one token count")
        source = usage["source"]
        if not isinstance(source, str) or not source.strip() \
                or len(source) > 80:
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                "a reported usage block requires a bounded source")
    else:
        if any(value is not None for value in tokens) \
                or usage["source"] is not None:
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                "an unreported usage block must not carry token counts or a "
                "source")
    if not _bounded_text(usage["note"], 300):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record usage note is invalid")


def _validate_config_snapshot(snapshot) -> None:
    if not isinstance(snapshot, dict) \
            or set(snapshot) != _CONFIG_SNAPSHOT_KEYS:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record supervisor_config is invalid")
    if snapshot["source"] not in {None, "queued", "fixed_policy"}:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record supervisor_config source is "
                              "invalid")
    revision = snapshot["config_revision"]
    if revision is not None and (not _is_int(revision) or revision < 1):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record config revision is invalid")
    for key, limit in (("model", 120), ("reasoning_effort", 40)):
        value = snapshot[key]
        if value is not None and (not isinstance(value, str)
                                  or not value.strip() or len(value) > limit):
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                f"turn record supervisor_config {key} is invalid")
    if not _bounded_text(snapshot["queued_at"], 40):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record queued_at is invalid")


def _validate_decision_block(decision) -> None:
    if not isinstance(decision, dict) or set(decision) != _DECISION_KEYS:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record decision block is invalid")
    if not isinstance(decision["committed"], bool):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record decision committed must be a "
                              "boolean")
    for key in ("receipt_sha256", "decision_sha256"):
        value = decision[key]
        if value is not None and (not isinstance(value, str)
                                  or not HEX64.fullmatch(value)):
            raise TurnRecordError("TURN_RECORD_UNUSABLE",
                                  f"turn record {key} is invalid")
    if not _bounded_text(decision["receipt_file"], 200):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record receipt_file is invalid")
    if not _bounded_json_object(decision["decision"], max_keys=16,
                                max_bytes=16384):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record decision payload is invalid")
    if not _bounded_text(decision["decision_summary"], 4000):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record decision_summary is invalid")
    index = decision["decision_history_index"]
    if index is not None and (not _is_int(index) or index < 0):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record decision_history_index is invalid")
    if not _bounded_text(decision["resulting_status"], 40):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record resulting_status is invalid")


def _validate_outcome_block(outcome) -> None:
    if not isinstance(outcome, dict) or set(outcome) != _OUTCOME_KEYS:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record outcome block is invalid")
    for key in ("committed", "stale", "recovered_after_crash",
                "candidate_validation_failed"):
        if not isinstance(outcome[key], bool):
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                f"turn record outcome {key} must be a boolean")
    if not _bounded_text(outcome["error"], 800):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record outcome error is invalid")


def _validate_linkage(linkage) -> None:
    if linkage is None:
        return
    if not isinstance(linkage, dict) or set(linkage) != _LINKAGE_KEYS:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record dispatch_linkage is invalid")
    message_id = linkage["MESSAGE_ID"]
    if not _is_int(message_id) or message_id < 0:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record linkage MESSAGE_ID is invalid")
    attempt = linkage["ATTEMPT"]
    if not _is_int(attempt) or attempt < 1:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record linkage ATTEMPT is invalid")
    for key in ("TASK_ID", "STAGE_ID", "NONCE"):
        value = linkage[key]
        if not isinstance(value, str) or not value.strip() \
                or len(value) > 512:
            raise TurnRecordError("TURN_RECORD_UNUSABLE",
                                  f"turn record linkage {key} is invalid")
    if not isinstance(linkage["dispatch_sha256"], str) \
            or not HEX64.fullmatch(linkage["dispatch_sha256"]):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record linkage dispatch_sha256 is "
                              "invalid")


def load_turn_record(path: Path) -> dict:
    """Strictly load and validate one turn record; fail closed on anything
    malformed, oversized, or schema-divergent."""
    path = Path(path)
    try:
        if path.stat().st_size > MAX_TURN_RECORD_BYTES:
            raise TurnRecordError(
                "TURN_RECORD_UNUSABLE",
                f"turn record exceeds {MAX_TURN_RECORD_BYTES} bytes")
        record = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise TurnRecordError("TURN_NOT_FOUND",
                              f"turn record {path.name!r} does not exist")
    except TurnRecordError:
        raise
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              f"turn record {path.name!r} is unreadable: "
                              f"{exc}") from exc
    if not isinstance(record, dict):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "a turn record must be a JSON object")
    if record.get("schema") != TURN_RECORD_SCHEMA \
            or record.get("schema_version") != 1:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record schema is unsupported")
    if set(record) != _TURN_KEYS:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record key set is invalid")
    if not isinstance(record["turn_id"], str) \
            or not TURN_ID_PATTERN.fullmatch(record["turn_id"]):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record turn_id is invalid")
    project_id = record["PROJECT_ID"]
    if project_id is not None and (not isinstance(project_id, str)
                                   or len(project_id) > 64):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record PROJECT_ID is invalid")
    if not isinstance(record["finished_at"], str) \
            or not record["finished_at"]:
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record finished_at is required")
    if not _bounded_text(record["started_at"], 40):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record started_at is invalid")
    duration = record["duration_seconds"]
    if duration is not None and (isinstance(duration, bool)
                                 or not isinstance(duration, (int, float))
                                 or duration < 0):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record duration_seconds is invalid")
    if not _bounded_json_object(record["invocation"], max_keys=16,
                                max_bytes=8192):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record invocation is invalid")
    _validate_config_snapshot(record["supervisor_config"])
    _validate_usage_block(record["usage"])
    _validate_decision_block(record["decision"])
    _validate_linkage(record["dispatch_linkage"])
    outcome = record["outcome"]
    _validate_outcome_block(outcome)
    manifest = record["context_manifest"]
    if manifest is not None:
        if not isinstance(manifest, dict) or not manifest or len(manifest) > 16:
            raise TurnRecordError("TURN_RECORD_UNUSABLE",
                                  "turn record context_manifest is invalid")
        for key, value in manifest.items():
            if not isinstance(key, str) or not 1 <= len(key) <= 60 \
                    or not _manifest_value_ok(value, depth=1):
                raise TurnRecordError("TURN_RECORD_UNUSABLE",
                                      "turn record context_manifest is "
                                      "invalid")
    if not isinstance(record["intervention_ids"], list) or len(
            record["intervention_ids"]) > 64 or not all(
            isinstance(item, str) and 0 < len(item) <= 120
            for item in record["intervention_ids"]):
        raise TurnRecordError("TURN_RECORD_UNUSABLE",
                              "turn record intervention_ids are invalid")
    return record


def load_turn_records(root: Path) -> tuple[list, list, list]:
    """Boundedly read every valid turn record, newest first.

    Unusable records are counted and surfaced, never silently skipped and
    never repaired.
    """
    root = Path(root)
    directory = root / "control" / "supervisor_turns"
    records: list[dict] = []
    unusable: list[dict] = []
    honesty: list[str] = []
    if not directory.is_dir():
        if (root / "control" / "supervisor_decisions").is_dir():
            honesty.append(
                "no SUPERVISOR-TURN-OBSERVABILITY-V1 turn records exist yet; "
                "older Supervisor turns have decision receipts only")
        else:
            honesty.append("no Supervisor turn records exist yet")
        return records, unusable, honesty
    files = sorted(directory.glob("*.json"))
    if len(files) > MAX_TURN_WALK:
        honesty.append(
            f"the turn record walk was truncated at {MAX_TURN_WALK} files")
        files = files[:MAX_TURN_WALK]
    for path in files:
        try:
            records.append(load_turn_record(path))
        except TurnRecordError as exc:
            unusable.append({"file": path.name, "reason": str(exc)})
    if len(unusable) > MAX_UNUSABLE_LISTED:
        honesty.append(
            f"{len(unusable)} turn records were unusable; only the first "
            f"{MAX_UNUSABLE_LISTED} are listed")
        unusable = unusable[:MAX_UNUSABLE_LISTED]
    records.sort(key=lambda record: (record["finished_at"],
                                     record["started_at"] or "",
                                     record["turn_id"]), reverse=True)
    return records, unusable, honesty


def _receipt_file_problems(receipt_file) -> bool:
    if not isinstance(receipt_file, str) or not receipt_file:
        return True
    if "\\" in receipt_file or receipt_file.startswith("/"):
        return True
    parts = PurePosixPath(receipt_file).parts
    return (len(parts) != 3 or parts[0] != "control"
            or parts[1] != "supervisor_decisions"
            or not parts[2].endswith(".json"))


def verify_receipt_binding(root: Path, record: dict) -> tuple[str | None,
                                                             list[str]]:
    """Verify a record's decision-receipt binding against the receipt file.

    Returns the integrity verdict and any honesty notes. A hostile or
    out-of-bounds path is refused — never read — and surfaced.
    """
    notes: list[str] = []
    decision = record["decision"]
    receipt_file = decision["receipt_file"]
    receipt_sha256 = decision["receipt_sha256"]
    if receipt_file is None and receipt_sha256 is None:
        return None, notes
    if receipt_file is None or receipt_sha256 is None:
        notes.append(
            f"turn {record['turn_id']!r} has a partial receipt binding; "
            "treated as unavailable")
        return "RECEIPT_UNAVAILABLE", notes
    if _receipt_file_problems(receipt_file):
        notes.append(
            f"refused the receipt path {receipt_file!r} for turn "
            f"{record['turn_id']!r}: it is outside the bounded "
            "control/supervisor_decisions/ directory")
        return "RECEIPT_UNAVAILABLE", notes
    path = root / Path(*PurePosixPath(receipt_file).parts)
    try:
        if not path.is_file() or path.stat().st_size > MAX_RECEIPT_BYTES:
            return "RECEIPT_UNAVAILABLE", notes
        receipt = json.loads(path.read_text(encoding="utf-8-sig"))
        actual = _canonical_json_sha256(receipt)
    except Exception:
        return "RECEIPT_UNAVAILABLE", notes
    if not isinstance(receipt_sha256, str) \
            or not HEX64.fullmatch(receipt_sha256) \
            or actual != receipt_sha256:
        return "RECEIPT_MISMATCH", notes
    return "RECEIPT_VERIFIED", notes


def _config_presentation(snapshot: dict) -> dict:
    return {"source": snapshot.get("source"),
            "model": snapshot.get("model"),
            "reasoning_effort": snapshot.get("reasoning_effort"),
            "config_revision": snapshot.get("config_revision"),
            "queued_at": snapshot.get("queued_at")}


def _project_turn(record: dict, integrity, *, detail: bool) -> dict:
    decision = record["decision"]
    usage = record["usage"]
    linkage = record["dispatch_linkage"]
    projected = {
        "turn_id": record["turn_id"],
        "project_id": record["PROJECT_ID"],
        "invocation_reason": ((record["invocation"] or {}).get("reason")
                              if isinstance(record.get("invocation"), dict)
                              else None),
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "duration_seconds": record["duration_seconds"],
        "supervisor_config": _config_presentation(
            record["supervisor_config"]),
        "usage": {key: usage[key] for key in (
            "reported", "input_tokens", "output_tokens", "total_tokens",
            "source", "note")},
        "decision": {
            "committed": decision["committed"],
            "summary": decision["decision_summary"],
            "decision": ((decision["decision"] or {}).get("decision")
                         if isinstance(decision["decision"], dict) else None),
            "resulting_status": decision["resulting_status"],
            "history_index": decision["decision_history_index"],
        },
        "dispatch_linkage": None if linkage is None else {
            key: linkage[key] for key in _LINKAGE_KEYS},
        "intervention_ids": list(record["intervention_ids"]),
        "outcome": {key: record["outcome"][key] for key in (
            "committed", "stale", "recovered_after_crash",
            "candidate_validation_failed", "error")},
        "receipt": {"file": decision["receipt_file"],
                    "sha256": decision["receipt_sha256"],
                    "integrity": integrity},
    }
    if detail:
        projected["invocation"] = record["invocation"]
        projected["context_manifest"] = record["context_manifest"]
    return projected


def build_turns_document(root: Path, *, limit: int, offset: int,
                         generated_at: str) -> dict:
    records, unusable, honesty = load_turn_records(root)
    page = records[offset:offset + limit]
    projected = []
    for record in page:
        integrity, notes = verify_receipt_binding(root, record)
        honesty.extend(notes)
        projected.append(_project_turn(record, integrity, detail=False))
    totals = {
        "total_records": len(records),
        "total_unusable": len(unusable),
        "returned": len(projected),
        "offset": offset,
        "limit": limit,
        "with_reported_usage": sum(
            1 for record in records if record["usage"]["reported"]),
    }
    return {"schema_version": SUPERVISOR_SCHEMA_VERSION,
            "generated_at": generated_at,
            "record_schema": TURN_RECORD_SCHEMA,
            "turns": projected,
            "totals": totals,
            "unusable": unusable,
            "honesty": honesty}


def build_turn_detail_document(root: Path, turn_id: str) -> dict:
    if not isinstance(turn_id, str) or not TURN_ID_PATTERN.fullmatch(turn_id):
        raise TurnRecordError("TURN_ID_INVALID",
                              "the turn id is not a valid record identifier")
    path = Path(root) / "control" / "supervisor_turns" / f"{turn_id}.json"
    if not path.is_file():
        raise TurnRecordError(
            "TURN_NOT_FOUND",
            f"no Supervisor turn record exists for {turn_id!r}")
    try:
        record = load_turn_record(path)
    except TurnRecordError as exc:
        if exc.code == "TURN_NOT_FOUND":
            raise
        raise TurnRecordError(
            "TURN_RECORD_UNUSABLE",
            f"the turn record for {turn_id!r} is unusable: {exc}") from exc
    integrity, notes = verify_receipt_binding(root, record)
    return {"schema_version": SUPERVISOR_SCHEMA_VERSION,
            "turn": _project_turn(record, integrity, detail=True),
            "honesty": {"notes": notes}}


def build_usage_document(root: Path, *, generated_at: str) -> dict:
    records, unusable, honesty = load_turn_records(root)
    if unusable:
        honesty.append(
            f"{len(unusable)} turn records were unusable and are excluded "
            "from this summary; they are listed by the turns document")
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    coverage = {"input_tokens_reported": 0, "output_tokens_reported": 0,
                "total_tokens_reported": 0}
    turns_with_reported_usage = 0
    reported_turns: list[dict] = []
    highest = None
    for record in records:
        usage = record["usage"]
        if not usage["reported"]:
            continue
        turns_with_reported_usage += 1
        counted = False
        for key in totals:
            value = usage[key]
            if value is not None:
                totals[key] += value
                coverage[f"{key}_reported"] += 1
                counted = True
        if counted:
            reported_turns.append({"turn_id": record["turn_id"],
                                   "total_tokens": usage["total_tokens"]})
            if usage["total_tokens"] is not None and (
                    highest is None
                    or usage["total_tokens"] > highest["total_tokens"]):
                highest = {"turn_id": record["turn_id"],
                           "total_tokens": usage["total_tokens"]}
    presented_totals = {key: (value if coverage[f"{key}_reported"] else None)
                        for key, value in totals.items()}
    reported_turns = reported_turns[:MAX_RECENT_REPORTED_TURNS]
    summary = {
        "turns_total": len(records),
        "turns_with_reported_usage": turns_with_reported_usage,
        "turns_without_reported_usage": len(records)
        - turns_with_reported_usage,
        "totals": presented_totals,
        "coverage": coverage,
        "average_total_per_reported_turn": (
            round(presented_totals["total_tokens"]
                  / coverage["total_tokens_reported"], 1)
            if coverage["total_tokens_reported"] else None),
        "highest_total_turn": highest,
        "recent_reported_turns": reported_turns,
        "zcode_usage": {"reported": False, "input_tokens": None,
                        "output_tokens": None, "total_tokens": None,
                        "source": None, "note": ZCODE_USAGE_NOTE},
    }
    if turns_with_reported_usage == 0:
        honesty.append(
            "no Supervisor turn has reported token usage yet; totals are "
            "not reported rather than estimated")
    return {"schema_version": SUPERVISOR_SCHEMA_VERSION,
            "generated_at": generated_at,
            "usage": summary,
            "honesty": honesty}


# -- Supervisor configuration document ----------------------------------------

_CONFIG_DOC_KEYS = {"schema_version", "PROJECT_ID", "active", "pending",
                    "updated_at"}
_CONFIG_ACTIVE_KEYS = {"model", "reasoning_effort", "queued_at",
                       "config_revision", "applied_at", "source_turn_id"}
_CONFIG_PENDING_KEYS = {"model", "reasoning_effort", "queued_at",
                        "config_revision"}


def _validate_config_block(block, *, keys, label) -> bool:
    if not isinstance(block, dict) or set(block) != set(keys):
        return False
    for key in keys:
        if block.get(key) is None and key != "model":
            return False
    model = block["model"]
    if model is not None:
        if (not isinstance(model, str) or not model.strip()
                or len(model) > 80
                or any(unicodedata.category(char) == "Cc" for char in model)):
            return False
    if block["reasoning_effort"] not in SUPPORTED_REASONING_EFFORTS:
        return False
    revision = block["config_revision"]
    return _is_int(revision) and revision >= 1


def read_config_document(root: Path) -> dict:
    """Read the Runtime-owned configuration document, fail closed."""
    path = Path(root) / "control" / "supervisor_config.json"
    if not path.is_file():
        return {"state": "absent", "config": None, "reason": None}
    try:
        if path.stat().st_size > MAX_TURN_RECORD_BYTES:
            raise ValueError("the configuration document exceeds its bound")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return {"state": "unusable", "config": None,
                "reason": f"the configuration document is unreadable: {exc}"}
    if (not isinstance(value, dict)
            or value.get("schema_version") != 1
            or set(value) != _CONFIG_DOC_KEYS
            or (value["PROJECT_ID"] is not None
                and not isinstance(value["PROJECT_ID"], str))):
        return {"state": "unusable", "config": None,
                "reason": "the configuration document is malformed"}
    if value["active"] is not None and not _validate_config_block(
            value["active"], keys=_CONFIG_ACTIVE_KEYS, label="active"):
        return {"state": "unusable", "config": None,
                "reason": "the active configuration block is malformed"}
    if value["pending"] is not None and not _validate_config_block(
            value["pending"], keys=_CONFIG_PENDING_KEYS, label="pending"):
        return {"state": "unusable", "config": None,
                "reason": "the pending configuration block is malformed"}
    return {"state": "ok", "config": value, "reason": None}


def config_view(config_result: dict, *, capability,
                draft_supervisor) -> dict:
    """Honest active-versus-pending configuration presentation."""
    notes: list[str] = []
    state = config_result.get("state")
    config = config_result.get("config")
    active = {"configured": False, "source": "fixed_policy",
              "model": None, "reasoning_effort": None, "applied_at": None,
              "config_revision": None, "notes": [FIXED_POLICY_NOTE]}
    pending = None
    if state == "ok" and isinstance(config, dict):
        active_block = config.get("active")
        if isinstance(active_block, dict):
            active = {"configured": True, "source": "queued",
                      "model": active_block.get("model"),
                      "reasoning_effort": active_block.get(
                          "reasoning_effort"),
                      "applied_at": active_block.get("applied_at"),
                      "config_revision": active_block.get("config_revision"),
                      "notes": []}
        pending_block = config.get("pending")
        if isinstance(pending_block, dict):
            pending = {"model": pending_block.get("model"),
                       "reasoning_effort": pending_block.get(
                           "reasoning_effort"),
                       "queued_at": pending_block.get("queued_at"),
                       "config_revision": pending_block.get(
                           "config_revision"),
                       "applies": "next eligible Supervisor turn",
                       "notes": [PENDING_NOTE]}
    elif state == "unusable":
        notes.append(config_result.get("reason")
                     or "the configuration document is unusable")
    draft = {"available": False, "model": None, "reasoning_effort": None,
             "note": DRAFT_NOTE}
    if isinstance(draft_supervisor, dict):
        draft["available"] = True
        draft["model"] = draft_supervisor.get("model")
        draft["reasoning_effort"] = draft_supervisor.get("reasoning_effort")
    return {"schema_version": SUPERVISOR_SCHEMA_VERSION,
            "state": state,
            "active": active,
            "pending": pending,
            "capability": capability,
            "supported_reasoning_efforts": list(SUPPORTED_REASONING_EFFORTS),
            "supported_note": SUPPORTED_EFFORTS_NOTE,
            "draft": draft,
            "honesty": {"notes": notes}}


def validate_config_change_request(payload) -> dict:
    """Exactly {"model", "reasoning_effort"} in the supported value space."""
    web_console_control._require_object(payload)
    web_console_control._require_exact_keys(
        payload, ("model", "reasoning_effort"))
    model = payload["model"]
    if not isinstance(model, str) or not model.strip():
        raise web_console_control.ControlRequestError(
            "model must be a non-empty string", field="model")
    model = model.strip()
    if len(model) > 80:
        raise web_console_control.ControlRequestError(
            "model exceeds 80 characters", field="model")
    if any(unicodedata.category(char) == "Cc" for char in model):
        raise web_console_control.ControlRequestError(
            "model must not contain control characters", field="model")
    effort = payload["reasoning_effort"]
    if effort not in SUPPORTED_REASONING_EFFORTS:
        raise web_console_control.ControlRequestError(
            "reasoning_effort must be one of "
            + ", ".join(SUPPORTED_REASONING_EFFORTS),
            field="reasoning_effort")
    return {"model": model, "reasoning_effort": effort}
