"""Runtime-owned Executor completion commit helper (COMPLETION-SEAL-V1).

Trust model
-----------
The Executor may only produce a *candidate* result: a project-local completion
staging directory. It may never create the authoritative fact "this task is
complete". That fact is exclusively manufactured by this Runtime helper, which
mechanically validates the staging against the authorized dispatch, the
at-most-once claim, and the live lifecycle state, and then writes ONE
append-immutable ledger entry (hard-link compare-and-set) under
``handoff/completion_ledger/``.

Ledger lifecycle (monotonic, never reversible, restart-proof):

    COMPLETION_COMMITTED -> COMPLETION_CONSUMED -> COMPLETION_SEALED

Root ``SUPERVISOR_BRIEF.md`` / ``ZCODE_LAST_PROCESSED.txt`` / ``ZCODE_DONE.flag``
are DEMOTED to derived compatibility artifacts / wake hints. They are generated
by this helper (or the Orchestrator) from the committed entry, and the
Orchestrator refuses to treat them as completion truth without a matching
COMPLETION_COMMITTED ledger record.

Exit codes (independent of executor_claim.py semantics):
    0  COMPLETION_COMMITTED
    10 ALREADY_COMMITTED          (same identity committed again)
    11 COMPLETION_SEALED          (identity already consumed and/or sealed)
    12 COMPLETION_NOT_AUTHORIZED  (dispatch/project/lifecycle authorization)
    13 COMPLETION_CLAIM_MISMATCH  (claim missing or identity mismatch)
    14 INVALID_COMPLETION_STAGING (schema/hash/path violations)
    15 INTERNAL_ERROR             (unexpected helper failure)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]

COMPLETION_PROTOCOL_VERSION = 1
COMPLETION_STAGING_SCHEMA_VERSION = 1

EXIT_COMMITTED = 0
EXIT_ALREADY_COMMITTED = 10
EXIT_COMPLETION_SEALED = 11
EXIT_NOT_AUTHORIZED = 12
EXIT_CLAIM_MISMATCH = 13
EXIT_INVALID_STAGING = 14
EXIT_INTERNAL = 15

STATUS_COMMITTED = "COMPLETION_COMMITTED"
STATUS_CONSUMED = "COMPLETION_CONSUMED"
STATUS_SEALED = "COMPLETION_SEALED"
_LEDGER_STATUSES = (STATUS_COMMITTED, STATUS_CONSUMED, STATUS_SEALED)

IDENTITY_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")

# Raw root DONE classification (shared by Orchestrator and HUMAN_REVIEW resume).
CLASS_NO_RAW_SIGNAL = "NO_RAW_SIGNAL"
CLASS_COMMITTED_UNCONSUMED = "COMPLETION_COMMITTED_UNCONSUMED"
CLASS_SEALED_REPLAY = "KNOWN_SEALED_REPLAY"
CLASS_UNKNOWN_RAW = "UNKNOWN_RAW_COMPLETION"
CLASS_BINDING_MISMATCH = "COMPLETION_BINDING_MISMATCH"

PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
KEY_VALUE_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
FIRST_INTEGER = re.compile(r"-?\d+")
HEX64 = re.compile(r"^[0-9a-f]{64}$")

STAGING_MAX_BYTES = 256 * 1024
RECEIPT_MAX_BYTES = 64 * 1024
EVIDENCE_MAX_ENTRIES = 64
EVIDENCE_MAX_FILE_BYTES = 64 * 1024 * 1024


class CompletionError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical_json_sha256(value) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def nonce_digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]


def commit_id_for(message_id: int, nonce: str) -> str:
    return f"completion-{message_id}-{nonce_digest(nonce)}"


# ---------------------------------------------------------------------------
# Wire renderings (deterministic; the hash bindings are defined on these)
# ---------------------------------------------------------------------------

def render_brief_text(receipt: dict) -> str:
    """Canonical SUPERVISOR_BRIEF.md rendering of a committed receipt."""
    return "```json\n" + json.dumps(receipt, ensure_ascii=False, indent=2) + "\n```\n"


def render_brief_bytes(receipt: dict) -> bytes:
    return render_brief_text(receipt).encode("utf-8")


def last_processed_text(entry: dict) -> str:
    """v2 key=value processed-pointer (same wire format as executor_claim.py)."""
    lines = [
        f"MESSAGE_ID={int(entry['MESSAGE_ID'])}",
        f"TASK_ID={entry['TASK_ID']}",
        f"STAGE_ID={entry['STAGE_ID']}",
        f"ATTEMPT={int(entry['ATTEMPT'])}",
        f"NONCE={entry['NONCE']}",
    ]
    return "\n".join(lines) + "\n"


def done_flag_text(entry: dict) -> str:
    lines = [
        f"COMPLETION_COMMIT_ID={entry['COMMIT_ID']}",
        f"MESSAGE_ID={int(entry['MESSAGE_ID'])}",
        f"TASK_ID={entry['TASK_ID']}",
        f"STAGE_ID={entry['STAGE_ID']}",
        f"ATTEMPT={int(entry['ATTEMPT'])}",
        f"NONCE={entry['NONCE']}",
    ]
    return "\n".join(lines) + "\n"


def parse_done_identity(text: str) -> dict:
    """Tolerant parser for a raw root ZCODE_DONE.flag wake hint.

    The DONE flag is an untrusted wake signal, so this parser extracts whatever
    identity it declares without trusting it. KEY=VALUE lines win; a legacy
    free-form flag falls back to its first integer as MESSAGE_ID. Returns {}
    when no identity can be extracted at all.
    """
    if not isinstance(text, str):
        return {}
    identity: dict = {}
    for line in text.splitlines():
        match = KEY_VALUE_LINE.match(line.strip())
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if key == "COMPLETION_COMMIT_ID" or key in IDENTITY_KEYS:
            identity[key] = value
    if "MESSAGE_ID" in identity and re.fullmatch(r"\d+", identity["MESSAGE_ID"]):
        identity["MESSAGE_ID"] = int(identity["MESSAGE_ID"])
        if "ATTEMPT" in identity and re.fullmatch(r"\d+", identity["ATTEMPT"]):
            identity["ATTEMPT"] = int(identity["ATTEMPT"])
        return identity
    match = FIRST_INTEGER.search(text)
    if match:
        return {"MESSAGE_ID": int(match.group(0))}
    return {}


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------

def ledger_dir(root: Path) -> Path:
    return Path(root) / "handoff" / "completion_ledger"


def entry_path(root: Path, commit_id: str) -> Path:
    return ledger_dir(root) / f"{commit_id}.json"


def staged_archive_dir(root: Path, commit_id: str) -> Path:
    return ledger_dir(root) / "staged" / commit_id


def quarantine_dir(root: Path) -> Path:
    return Path(root) / "handoff" / "quarantine"


def _read_json_file(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def load_entry_file(path: Path):
    entry = _read_json_file(path)
    if not isinstance(entry, dict):
        return None
    if entry.get("COMPLETION_PROTOCOL_VERSION") != COMPLETION_PROTOCOL_VERSION:
        return None
    if entry.get("STATUS") not in _LEDGER_STATUSES:
        return None
    return entry


def lookup_entries(root: Path, message_id) -> list:
    """All parsed ledger entries for one MESSAGE_ID (at most one may exist)."""
    directory = ledger_dir(root)
    if not directory.is_dir() or isinstance(message_id, bool) or not isinstance(message_id, int):
        return []
    found = []
    for path in sorted(directory.glob(f"completion-{message_id}-*.json")):
        entry = load_entry_file(path)
        if entry is not None and entry.get("MESSAGE_ID") == message_id:
            found.append(entry)
    return found


def append_audit(root: Path, event: dict) -> None:
    """Best-effort append-only audit trail. Lifecycle decisions never depend on it."""
    try:
        directory = ledger_dir(root)
        directory.mkdir(parents=True, exist_ok=True)
        line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
        with (directory / "audit.jsonl").open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        pass


def save_entry(root: Path, entry: dict) -> None:
    """Runtime-owned status transition write (COMMITTED -> CONSUMED -> SEALED)."""
    target = entry_path(root, entry["COMMIT_ID"])
    payload = json.dumps(entry, ensure_ascii=False, indent=2) + "\n"
    temp = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)


def _atomic_create(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    with temp.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


# ---------------------------------------------------------------------------
# Identity helpers
# ---------------------------------------------------------------------------

def _identity_from_values(message_id, task_id, stage_id, attempt, nonce) -> dict:
    return {
        "MESSAGE_ID": message_id,
        "TASK_ID": task_id,
        "STAGE_ID": stage_id,
        "ATTEMPT": attempt,
        "NONCE": nonce,
    }


def _validated_identity(raw: dict, label: str) -> dict:
    missing = [key for key in IDENTITY_KEYS if key not in raw]
    if missing:
        raise CompletionError(EXIT_INVALID_STAGING, f"{label} missing identity fields: {missing}")
    message_id, attempt = raw["MESSAGE_ID"], raw["ATTEMPT"]
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 0:
        raise CompletionError(EXIT_INVALID_STAGING, f"{label} MESSAGE_ID must be an integer >= 0")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise CompletionError(EXIT_INVALID_STAGING, f"{label} ATTEMPT must be an integer >= 1")
    for key in ("TASK_ID", "STAGE_ID", "NONCE"):
        value = raw[key]
        if not isinstance(value, str) or not value or value != value.strip():
            raise CompletionError(
                EXIT_INVALID_STAGING,
                f"{label} {key} must be a non-empty string without surrounding whitespace",
            )
    return _identity_from_values(message_id, raw["TASK_ID"], raw["STAGE_ID"], attempt, raw["NONCE"])


def entry_identity(entry: dict) -> dict:
    return {key: entry.get(key) for key in IDENTITY_KEYS}


def identity_values_match(left: dict, right: dict) -> bool:
    """Compare identity fields of two dicts, tolerating lowercase mirrors."""
    def tv(container, key):
        if not isinstance(container, dict):
            return None
        return container.get(key, container.get(key.lower()))

    return all(tv(left, key) == tv(right, key) for key in IDENTITY_KEYS)


def partial_identity_binds(identity: dict, raw: dict) -> bool:
    """True when every identity field the RAW signal declares matches identity.

    A legacy free-form DONE flag may carry only MESSAGE_ID; a forged or stale
    replay that declares any field wrongly fails to bind.
    """
    for key in IDENTITY_KEYS:
        if key in raw and raw[key] != identity.get(key):
            return False
    return True


# ---------------------------------------------------------------------------
# Active project scope (same strict rules as the Orchestrator / preflight)
# ---------------------------------------------------------------------------

def resolve_active_project(root: Path) -> tuple:
    """Return (project_id | None, project_root). An invalid pointer fails closed."""
    root = Path(root)
    pointer_path = root / "control" / "ACTIVE_PROJECT.json"
    if not pointer_path.exists():
        return None, root
    pointer = _read_json_file(pointer_path)
    if not isinstance(pointer, dict):
        raise CompletionError(EXIT_NOT_AUTHORIZED, "ACTIVE_PROJECT.json is not a JSON object")
    if pointer.get("schema_version") != 1:
        raise CompletionError(EXIT_NOT_AUTHORIZED, "ACTIVE_PROJECT.json schema_version must be 1")
    pid = pointer.get("project_id")
    proot = pointer.get("project_root")
    if not isinstance(pid, str) or not PROJECT_ID_PATTERN.fullmatch(pid):
        raise CompletionError(EXIT_NOT_AUTHORIZED, f"unsafe ACTIVE_PROJECT project_id: {pid!r}")
    if proot != f"projects/{pid}":
        raise CompletionError(EXIT_NOT_AUTHORIZED, f"ACTIVE_PROJECT.project_root must be projects/{pid}")
    project_root = (root / "projects" / pid).resolve()
    try:
        project_root.relative_to((root / "projects").resolve())
    except ValueError:
        raise CompletionError(EXIT_NOT_AUTHORIZED, "ACTIVE_PROJECT.project_root escapes ROOT/projects")
    return pid, project_root


def project_state_path(root: Path, project_id) -> Path:
    """Same layout rule as the Orchestrator: isolated projects keep their state
    at projects/<id>/project_state.json; legacy mode at control/."""
    if project_id is None:
        return Path(root) / "control" / "project_state.json"
    return Path(root) / "projects" / project_id / "project_state.json"


def _active_project_id_from_root(root: Path):
    pointer = _read_json_file(Path(root) / "control" / "ACTIVE_PROJECT.json")
    if isinstance(pointer, dict):
        return pointer.get("project_id")
    return None


# ---------------------------------------------------------------------------
# Staging validation
# ---------------------------------------------------------------------------

STAGING_REQUIRED_KEYS = {
    "COMPLETION_STAGING_SCHEMA_VERSION", "MESSAGE_ID", "TASK_ID", "STAGE_ID",
    "ATTEMPT", "NONCE", "PROJECT_ID", "STATUS", "CREATED_AT", "RECEIPT",
}
STAGING_OPTIONAL_KEYS = {"EVIDENCE", "DELIVERABLES"}
EVIDENCE_ITEM_KEYS = {"path", "sha256"}


def _check_timestamp(value, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise CompletionError(EXIT_INVALID_STAGING, f"{label} must be a non-empty ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        raise CompletionError(EXIT_INVALID_STAGING, f"{label} is not a valid ISO-8601 timestamp")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CompletionError(EXIT_INVALID_STAGING, f"{label} must include a timezone")


def _check_evidence_list(items, project_root: Path, label: str) -> None:
    if items is None:
        return
    if not isinstance(items, list) or len(items) > EVIDENCE_MAX_ENTRIES:
        raise CompletionError(
            EXIT_INVALID_STAGING,
            f"{label} must be a list of at most {EVIDENCE_MAX_ENTRIES} entries",
        )
    for item in items:
        if not isinstance(item, dict) or set(item) != EVIDENCE_ITEM_KEYS:
            raise CompletionError(
                EXIT_INVALID_STAGING,
                f"{label} entries must be objects with exactly keys {sorted(EVIDENCE_ITEM_KEYS)}",
            )
        rel, digest = item["path"], item["sha256"]
        if not isinstance(rel, str) or not rel or rel != rel.strip():
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} path must be a non-empty string")
        if not isinstance(digest, str) or not HEX64.fullmatch(digest):
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} sha256 must be lowercase hex64")
        path = Path(rel)
        if (
            path.is_absolute()
            or path.drive
            or re.match(r"^[A-Za-z]:", rel)
            or rel.startswith(("/", "\\"))
        ):
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} path must be relative: {rel!r}")
        if any(part == ".." for part in path.parts):
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} path must not traverse upward: {rel!r}")
        target = (project_root / path).resolve()
        try:
            target.relative_to(Path(project_root).resolve())
        except ValueError:
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} path escapes the project root: {rel!r}")
        if not target.is_file():
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} file is missing: {rel!r}")
        if target.stat().st_size > EVIDENCE_MAX_FILE_BYTES:
            raise CompletionError(
                EXIT_INVALID_STAGING, f"{label} file exceeds {EVIDENCE_MAX_FILE_BYTES} bytes: {rel!r}"
            )
        if sha256_bytes(target.read_bytes()) != digest:
            raise CompletionError(EXIT_INVALID_STAGING, f"{label} sha256 mismatch: {rel!r}")


def load_and_validate_staging(staging_dir: Path, project_root: Path, expected_project_id) -> dict:
    """Strict mechanical staging validation. Raises CompletionError(14) on any defect."""
    staging_dir = Path(staging_dir)
    staging_file = staging_dir / "staging.json"
    if not staging_dir.is_dir() or not staging_file.is_file():
        raise CompletionError(EXIT_INVALID_STAGING, f"completion staging not found: {staging_file}")
    try:
        staging_dir.resolve().relative_to((Path(project_root) / "completion_staging").resolve())
    except ValueError:
        raise CompletionError(
            EXIT_INVALID_STAGING,
            "staging directory must live under <project_root>/completion_staging",
        )
    raw = staging_file.read_bytes()
    if len(raw) > STAGING_MAX_BYTES:
        raise CompletionError(EXIT_INVALID_STAGING, f"staging.json exceeds {STAGING_MAX_BYTES} bytes")
    payload = _read_json_file(staging_file)
    if not isinstance(payload, dict):
        raise CompletionError(EXIT_INVALID_STAGING, "staging.json must be a JSON object")
    unknown = sorted(set(payload) - STAGING_REQUIRED_KEYS - STAGING_OPTIONAL_KEYS)
    missing = sorted(STAGING_REQUIRED_KEYS - set(payload))
    if unknown or missing:
        raise CompletionError(
            EXIT_INVALID_STAGING,
            f"staging.json schema mismatch (missing={missing}, unknown={unknown})",
        )
    if payload["COMPLETION_STAGING_SCHEMA_VERSION"] != COMPLETION_STAGING_SCHEMA_VERSION:
        raise CompletionError(EXIT_INVALID_STAGING, "unsupported COMPLETION_STAGING_SCHEMA_VERSION")
    if payload["STATUS"] != "STAGING_READY":
        raise CompletionError(EXIT_INVALID_STAGING, "staging STATUS must be STAGING_READY")
    project_id = payload["PROJECT_ID"]
    if project_id is not None and (
        not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id)
    ):
        raise CompletionError(EXIT_INVALID_STAGING, "PROJECT_ID must be null or a safe project id")
    if project_id != expected_project_id:
        raise CompletionError(
            EXIT_NOT_AUTHORIZED,
            f"staging PROJECT_ID {project_id!r} does not match the active project {expected_project_id!r}",
        )
    _check_timestamp(payload["CREATED_AT"], "CREATED_AT")

    identity = _validated_identity(payload, "staging")
    receipt = payload["RECEIPT"]
    if not isinstance(receipt, dict):
        raise CompletionError(EXIT_INVALID_STAGING, "RECEIPT must be a JSON object")
    if len(canonical_json_sha256(receipt).encode("utf-8")) > RECEIPT_MAX_BYTES:
        raise CompletionError(EXIT_INVALID_STAGING, f"RECEIPT canonical form exceeds {RECEIPT_MAX_BYTES} bytes")
    if _validated_identity(receipt, "RECEIPT") != identity:
        raise CompletionError(EXIT_INVALID_STAGING, "RECEIPT identity does not match staging identity")
    if not isinstance(receipt.get("STATUS"), str) or not receipt["STATUS"].strip():
        raise CompletionError(EXIT_INVALID_STAGING, "RECEIPT.STATUS must be a non-empty string")
    _check_evidence_list(payload.get("EVIDENCE"), project_root, "EVIDENCE")
    _check_evidence_list(payload.get("DELIVERABLES"), project_root, "DELIVERABLES")
    payload["_staging_manifest_sha256"] = sha256_bytes(raw)
    return payload


# ---------------------------------------------------------------------------
# Authorization / claim / ledger checks
# ---------------------------------------------------------------------------

def read_runtime_state(root: Path):
    state = _read_json_file(Path(root) / "control" / "orchestrator_runtime.json")
    return state if isinstance(state, dict) else None


def _check_authorization(runtime: dict, identity: dict) -> None:
    authorized = runtime.get("authorized_dispatch")
    if not isinstance(authorized, dict):
        raise CompletionError(EXIT_NOT_AUTHORIZED, "no authorized_dispatch is registered")
    if authorized.get("schema_version") != 1:
        raise CompletionError(EXIT_NOT_AUTHORIZED, "authorized_dispatch schema mismatch")
    if not identity_values_match(authorized, identity):
        raise CompletionError(
            EXIT_NOT_AUTHORIZED, "authorized_dispatch identity does not match the staged completion"
        )
    retired = runtime.get("retired_message_ids")
    if isinstance(retired, list):
        for value in retired:
            try:
                if int(value) == identity["MESSAGE_ID"]:
                    raise CompletionError(EXIT_NOT_AUTHORIZED, "MESSAGE_ID is retired")
            except (TypeError, ValueError):
                continue


def load_claim(root: Path, identity: dict):
    """Return (claim_dict | None, claim_dir_path) using the canonical claim layout."""
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import executor_claim as claim_helper

    claim_path = claim_helper.claim_dir(Path(root), identity["MESSAGE_ID"], identity["NONCE"])
    if not claim_path.is_dir():
        return None, claim_path
    claim = _read_json_file(claim_path / "claim.json")
    return (claim if isinstance(claim, dict) else None), claim_path


def _check_claim(root: Path, identity: dict) -> dict:
    claim, claim_path = load_claim(root, identity)
    if claim is None:
        raise CompletionError(
            EXIT_CLAIM_MISMATCH,
            f"no at-most-once claim exists for this identity: {claim_path}",
        )
    if not identity_values_match(claim, identity):
        raise CompletionError(EXIT_CLAIM_MISMATCH, "claim identity does not match the staged completion")
    return claim


def _check_ledger_absent(root: Path, identity: dict) -> None:
    existing = lookup_entries(root, identity["MESSAGE_ID"])
    if not existing:
        return
    entry = existing[0]
    if not identity_values_match(entry, identity):
        raise CompletionError(
            EXIT_NOT_AUTHORIZED,
            "a different identity already owns a completion commit for this MESSAGE_ID",
        )
    if entry["STATUS"] == STATUS_COMMITTED:
        raise CompletionError(
            EXIT_ALREADY_COMMITTED, f"identity already committed: {entry['COMMIT_ID']}"
        )
    raise CompletionError(
        EXIT_COMPLETION_SEALED,
        f"identity already consumed/sealed; it can never commit again: "
        f"{entry['COMMIT_ID']} status={entry['STATUS']}",
    )


# ---------------------------------------------------------------------------
# Entry construction and integrity
# ---------------------------------------------------------------------------

def build_entry(staging: dict, project_id, claim: dict, root: Path) -> dict:
    identity = _validated_identity(staging, "staging")
    receipt = staging["RECEIPT"]
    commit_id = commit_id_for(identity["MESSAGE_ID"], identity["NONCE"])
    claim_ref = Path(root) / "handoff" / "executor_claims" / (
        f"{identity['MESSAGE_ID']}-{nonce_digest(identity['NONCE'])}.claim"
    )
    return {
        "COMPLETION_PROTOCOL_VERSION": COMPLETION_PROTOCOL_VERSION,
        "COMMIT_ID": commit_id,
        "STATUS": STATUS_COMMITTED,
        **identity,
        "PROJECT_ID": project_id,
        "CLAIM_DIR": claim_ref.relative_to(Path(root).resolve()).as_posix(),
        "CLAIM_IDENTITY_SHA256": canonical_json_sha256(_identity_from_values(
            claim.get("MESSAGE_ID"), claim.get("TASK_ID"), claim.get("STAGE_ID"),
            claim.get("ATTEMPT"), claim.get("NONCE"),
        )),
        "RECEIPT_SHA256": canonical_json_sha256(receipt),
        "BRIEF_SHA256": sha256_bytes(render_brief_bytes(receipt)),
        "STAGING_MANIFEST_SHA256": staging["_staging_manifest_sha256"],
        "COMMITTED_AT": now_iso(),
        "CONSUMED_AT": None,
        "SEALED_AT": None,
        "CONSUMED_ARCHIVE": None,
        "RECEIPT": receipt,
    }


def entry_hashes_intact(entry: dict) -> bool:
    """Recompute every receipt-derived binding stored in the entry."""
    receipt = entry.get("RECEIPT")
    if not isinstance(receipt, dict):
        return False
    try:
        if canonical_json_sha256(receipt) != entry.get("RECEIPT_SHA256"):
            return False
        if sha256_bytes(render_brief_bytes(receipt)) != entry.get("BRIEF_SHA256"):
            return False
        if _validated_identity(receipt, "RECEIPT") != entry_identity(entry):
            return False
    except CompletionError:
        return False
    return True


def publish_compatibility_artifacts(root: Path, entry: dict, *, include_done: bool = True) -> None:
    """Regenerate the DEMOTED root compatibility artifacts from a committed entry.

    Order preserved from the wire contract: brief -> last_processed -> DONE last.
    These files are derived views of the ledger entry and may always be safely
    rebuilt; they are never completion truth.
    """
    root = Path(root)
    _atomic_write(root / "SUPERVISOR_BRIEF.md", render_brief_text(entry["RECEIPT"]))
    _atomic_write(root / "ZCODE_LAST_PROCESSED.txt", last_processed_text(entry))
    if include_done:
        _atomic_write(root / "ZCODE_DONE.flag", done_flag_text(entry))


# ---------------------------------------------------------------------------
# Commit
# ---------------------------------------------------------------------------

def _check_live_lifecycle(root: Path, project_id, identity: dict) -> dict:
    """The commit is only legal while the Runtime is actually waiting for it."""
    state = _read_json_file(project_state_path(root, project_id))
    if not isinstance(state, dict):
        raise CompletionError(EXIT_NOT_AUTHORIZED, "project_state.json is missing or invalid")
    active_project_id = _active_project_id_from_root(root)
    if state.get("project_id") is not None and state.get("project_id") != active_project_id:
        raise CompletionError(
            EXIT_NOT_AUTHORIZED, "project_state.project_id does not match the active project"
        )
    if state.get("status") != "WAITING_EXECUTOR":
        raise CompletionError(
            EXIT_NOT_AUTHORIZED,
            f"project status must be WAITING_EXECUTOR for a completion commit, got {state.get('status')!r}",
        )
    if not identity_values_match(state.get("current_task") or {}, identity):
        raise CompletionError(EXIT_NOT_AUTHORIZED, "current_task identity does not match the staged completion")
    return state


def commit(root: Path, staging_dir: Path) -> int:
    """Validate a staging directory and durably commit the authoritative completion."""
    root = Path(root).resolve()
    project_id, project_root = resolve_active_project(root)
    if project_id is None:
        project_root = root

    runtime = read_runtime_state(root)
    if runtime is None:
        raise CompletionError(EXIT_NOT_AUTHORIZED, "orchestrator_runtime.json is missing or invalid")

    staging = load_and_validate_staging(staging_dir, project_root, project_id)
    identity = _validated_identity(staging, "staging")

    _check_authorization(runtime, identity)
    # The ledger check precedes the live-lifecycle check: once an identity has
    # committed (and especially once it is consumed/sealed), a second commit is
    # refused with the precise at-most-once exit codes regardless of what the
    # lifecycle has moved on to.
    _check_ledger_absent(root, identity)
    _check_live_lifecycle(root, project_id, identity)
    claim = _check_claim(root, identity)

    entry = build_entry(staging, project_id, claim, root)
    target = entry_path(root, entry["COMMIT_ID"])
    try:
        _atomic_create(target, json.dumps(entry, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        # A concurrent commit won the compare-and-set: classify the winner.
        _check_ledger_absent(root, identity)
        raise CompletionError(EXIT_ALREADY_COMMITTED, f"identity already committed: {entry['COMMIT_ID']}")

    append_audit(root, {
        "at": now_iso(), "event": "COMPLETION_COMMITTED", "actor": "executor_completion_helper",
        "commit_id": entry["COMMIT_ID"], "MESSAGE_ID": entry["MESSAGE_ID"],
        "NONCE": entry["NONCE"], "PROJECT_ID": project_id,
        "receipt_sha256": entry["RECEIPT_SHA256"], "brief_sha256": entry["BRIEF_SHA256"],
        "staging_manifest_sha256": entry["STAGING_MANIFEST_SHA256"],
        "staging_dir": str(staging_dir),
    })

    # Archive the staged evidence out of the Executor's reach (best effort; the
    # ledger entry and its manifest hash remain authoritative either way).
    try:
        archive_target = staged_archive_dir(root, entry["COMMIT_ID"])
        if not archive_target.exists():
            archive_target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staging_dir, archive_target)
    except Exception:
        append_audit(root, {
            "at": now_iso(), "event": "COMPLETION_STAGING_ARCHIVE_SKIPPED",
            "commit_id": entry["COMMIT_ID"], "staging_dir": str(staging_dir),
        })

    # Runtime-generated compatibility artifacts, wire order: brief -> pointer -> DONE last.
    publish_compatibility_artifacts(root, entry, include_done=True)

    print(
        f"COMPLETION_COMMITTED commit_id={entry['COMMIT_ID']} "
        f"receipt_sha256={entry['RECEIPT_SHA256']} brief_sha256={entry['BRIEF_SHA256']}"
    )
    return EXIT_COMMITTED


# ---------------------------------------------------------------------------
# Raw-root classification (shared by Orchestrator consume and HUMAN_REVIEW resume)
# ---------------------------------------------------------------------------

def classify_raw_completion(root: Path, *, active_project_id, done_path: Path, brief_path: Path) -> dict:
    """Classify a raw root DONE wake hint against the authoritative ledger.

    Returns a report dict; the CALLER decides the consequence (consume /
    quarantine / fail closed) so the Orchestrator and the resume tool can apply
    their own policies on identical mechanical facts.
    """
    root = Path(root)
    done_path, brief_path = Path(done_path), Path(brief_path)
    if not done_path.exists():
        return {"classification": CLASS_NO_RAW_SIGNAL, "entry": None, "raw_identity": None,
                "brief_diverges": False, "detail": "no DONE flag"}
    try:
        text = done_path.read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        text = ""
    raw_identity = parse_done_identity(text)
    message_id = raw_identity.get("MESSAGE_ID")
    if not isinstance(message_id, int):
        return {"classification": CLASS_UNKNOWN_RAW, "entry": None, "raw_identity": raw_identity,
                "brief_diverges": False, "detail": "DONE flag carries no parseable MESSAGE_ID"}
    entries = lookup_entries(root, message_id)
    if not entries:
        return {"classification": CLASS_UNKNOWN_RAW, "entry": None, "raw_identity": raw_identity,
                "brief_diverges": False,
                "detail": f"no authoritative completion record for MESSAGE_ID {message_id}"}
    match = None
    for entry in entries:
        if entry.get("PROJECT_ID") != active_project_id:
            continue
        if not partial_identity_binds(entry, raw_identity):
            continue
        declared = raw_identity.get("COMPLETION_COMMIT_ID")
        if declared is not None and declared != entry.get("COMMIT_ID"):
            continue
        match = entry
        break
    if match is None:
        return {"classification": CLASS_BINDING_MISMATCH, "entry": None, "raw_identity": raw_identity,
                "brief_diverges": False,
                "detail": "record exists for this MESSAGE_ID but the raw signal identity/project/commit_id does not bind to it"}
    brief_diverges = False
    if brief_path.exists():
        try:
            brief_diverges = sha256_bytes(brief_path.read_bytes()) != match.get("BRIEF_SHA256")
        except Exception:
            brief_diverges = True
    classification = (
        CLASS_COMMITTED_UNCONSUMED if match["STATUS"] == STATUS_COMMITTED else CLASS_SEALED_REPLAY
    )
    return {"classification": classification, "entry": match, "raw_identity": raw_identity,
            "brief_diverges": brief_diverges, "detail": "raw signal bound to the committed record"}


def quarantine_raw_completion_artifacts(root: Path, *, done_path: Path, brief_path: Path,
                                        label: str, message_id) -> str:
    """Move untrusted raw wake artifacts into handoff/quarantine/ for audit."""
    root = Path(root)
    target_dir = quarantine_dir(root) / f"completion-{label}-{message_id}-{uuid.uuid4().hex[:8]}"
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = []
    for path in (done_path, brief_path):
        path = Path(path)
        if path.exists():
            try:
                os.replace(path, target_dir / path.name)
                moved.append(path.name)
            except Exception:
                pass
    append_audit(root, {
        "at": now_iso(), "event": "COMPLETION_ARTIFACTS_QUARANTINED", "label": label,
        "MESSAGE_ID": message_id, "moved": moved, "quarantine_dir": str(target_dir),
    })
    return str(target_dir)


def seal_predicate(entry: dict, state) -> bool:
    """True when a CONSUMED completion is no longer the pending lifecycle decision.

    The Supervisor lifecycle decision for a consumed receipt is committed when
    project_state no longer binds that identity to a WAITING_EXECUTOR wait.
    """
    if entry.get("STATUS") != STATUS_CONSUMED:
        return False
    if not isinstance(state, dict):
        return False
    if state.get("status") != "WAITING_EXECUTOR":
        return True
    return not identity_values_match(state.get("current_task") or {}, entry)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cmd_commit(args) -> int:
    return commit(Path(args.root).resolve(), Path(args.staging_dir).resolve())


def _cmd_status(args) -> int:
    root = Path(args.root).resolve()
    entries = lookup_entries(root, args.message_id)
    if not entries:
        print(f"NO_COMPLETION_RECORD message_id={args.message_id}")
        return 1
    for entry in entries:
        print(json.dumps(
            {key: entry.get(key) for key in (
                "COMMIT_ID", "STATUS", "MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT",
                "NONCE", "PROJECT_ID", "RECEIPT_SHA256", "BRIEF_SHA256",
                "COMMITTED_AT", "CONSUMED_AT", "SEALED_AT")},
            ensure_ascii=False))
    return 0


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        failures = []

        def check(name, condition):
            if not condition:
                failures.append(name)

        # -- synthetic runtime: authorized dispatch + claim + staging -------------
        message_id, task_id, stage_id, attempt, nonce = 800001, "T-SEAL", "S-SEAL", 1, "nonce-seal-1"
        identity = _identity_from_values(message_id, task_id, stage_id, attempt, nonce)
        (root / "ZCODE_LAST_PROCESSED.txt").write_text("800000\n", encoding="utf-8")
        (root / "control").mkdir(parents=True)
        project_root = root / "projects" / "seal-selftest"
        (project_root / "evidence").mkdir(parents=True)
        (project_root / "evidence" / "note.txt").write_text("evidence-bytes\n", encoding="utf-8")
        (root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps({
            "schema_version": 1, "project_id": "seal-selftest", "project_root": "projects/seal-selftest",
        }), encoding="utf-8")
        (project_root / "project_state.json").write_text(json.dumps({
            "schema_version": 2, "project_id": "seal-selftest",
            "status": "WAITING_EXECUTOR", "current_task": dict(identity),
        }), encoding="utf-8")
        task = {"PROTOCOL_VERSION": 2, **identity, "OBJECTIVE": "selftest", "OUTPUTS": []}
        (root / "TO_ZCODE.md").write_text(
            "```json\n" + json.dumps(task, indent=2) + "\n```\n", encoding="utf-8")
        (root / "control" / "orchestrator_runtime.json").write_text(json.dumps({
            "authorized_dispatch": {
                "schema_version": 1, **identity,
                "TO_ZCODE_SHA256": sha256_bytes((root / "TO_ZCODE.md").read_bytes()),
                "AUTHORIZED_AT": now_iso(),
            },
            "retired_message_ids": [],
        }), encoding="utf-8")

        sys_path = str(Path(__file__).resolve().parent)
        if sys_path not in sys.path:
            sys.path.insert(0, sys_path)
        import executor_claim as claim_helper
        check("claim_acquired",
              claim_helper.acquire(root, message_id, task_id, stage_id, attempt, nonce)
              == claim_helper.EXIT_ACQUIRED)

        receipt = {
            "PROTOCOL_VERSION": 2, **identity, "STATUS": "COMPLETED",
            "Key findings": ["selftest"], "Evidence pointers": ["evidence/note.txt"],
        }

        def make_staging(name: str, receipt_payload: dict) -> Path:
            staging_dir = project_root / "completion_staging" / name
            staging_dir.mkdir(parents=True, exist_ok=True)
            staging = {
                "COMPLETION_STAGING_SCHEMA_VERSION": COMPLETION_STAGING_SCHEMA_VERSION,
                **identity, "PROJECT_ID": "seal-selftest", "STATUS": "STAGING_READY",
                "CREATED_AT": now_iso(), "RECEIPT": receipt_payload,
                "EVIDENCE": [{
                    "path": "evidence/note.txt",
                    "sha256": sha256_bytes((project_root / "evidence" / "note.txt").read_bytes()),
                }],
            }
            (staging_dir / "staging.json").write_text(
                json.dumps(staging, indent=2), encoding="utf-8")
            return staging_dir

        staging_dir = make_staging(f"stage-{message_id}-deadbeef", receipt)
        check("first_commit_exit_0", commit(root, staging_dir) == EXIT_COMMITTED)

        entry_file = entry_path(root, commit_id_for(message_id, nonce))
        check("ledger_entry_exists", entry_file.is_file())
        entry = load_entry_file(entry_file)
        check("entry_status_committed", entry is not None and entry["STATUS"] == STATUS_COMMITTED)
        check("brief_rendered",
              (root / "SUPERVISOR_BRIEF.md").read_text(encoding="utf-8") == render_brief_text(receipt))
        check("pointer_written",
              (root / "ZCODE_LAST_PROCESSED.txt").read_text(encoding="utf-8") == last_processed_text(entry))
        check("done_last",
              (root / "ZCODE_DONE.flag").read_text(encoding="utf-8") == done_flag_text(entry))
        check("staging_archived", staged_archive_dir(root, entry["COMMIT_ID"]).is_dir())
        check("entry_hashes_intact", entry_hashes_intact(entry))

        # A second commit for the same identity is permanently refused (10).
        duplicate = make_staging(f"stage-{message_id}-replay", receipt)
        try:
            duplicate_exit = commit(root, duplicate)
        except CompletionError as exc:
            duplicate_exit = exc.code
        check("duplicate_commit_rejected_10", duplicate_exit == EXIT_ALREADY_COMMITTED)

        # Classifier: fresh runtime-generated DONE on a COMMITTED identity.
        done_path, brief_path = root / "ZCODE_DONE.flag", root / "SUPERVISOR_BRIEF.md"
        report = classify_raw_completion(root, active_project_id="seal-selftest",
                                         done_path=done_path, brief_path=brief_path)
        check("classify_committed", report["classification"] == CLASS_COMMITTED_UNCONSUMED)
        check("classify_no_divergence", report["brief_diverges"] is False)

        # CONSUMED identity with a recreated raw DONE -> sealed replay.
        entry["STATUS"] = STATUS_CONSUMED
        entry["CONSUMED_AT"] = now_iso()
        save_entry(root, entry)
        report = classify_raw_completion(root, active_project_id="seal-selftest",
                                         done_path=done_path, brief_path=brief_path)
        check("classify_sealed_replay", report["classification"] == CLASS_SEALED_REPLAY)

        # Rewritten root brief diverges from the committed receipt.
        brief_path.write_text("tampered\n", encoding="utf-8")
        report = classify_raw_completion(root, active_project_id="seal-selftest",
                                         done_path=done_path, brief_path=brief_path)
        check("classify_brief_divergence",
              report["classification"] == CLASS_SEALED_REPLAY and report["brief_diverges"])

        # Raw DONE with no authoritative record -> unknown, fail closed.
        done_path.write_text("999999 done\n", encoding="utf-8")
        report = classify_raw_completion(root, active_project_id="seal-selftest",
                                         done_path=done_path, brief_path=brief_path)
        check("classify_unknown_raw", report["classification"] == CLASS_UNKNOWN_RAW)

        if failures:
            print(f"COMPLETION_SELFTEST_FAILED: {failures}", file=sys.stderr)
            return 1
        print("EXECUTOR_COMPLETION_SELFTEST: OK")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Runtime-owned authoritative completion commit helper.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    com = sub.add_parser("commit", help="Commit a completion staging directory (at-most-once).")
    com.add_argument("--root", default=str(DEFAULT_ROOT))
    com.add_argument("--staging-dir", required=True)

    stat = sub.add_parser("status", help="Show the authoritative completion record for a MESSAGE_ID.")
    stat.add_argument("--root", default=str(DEFAULT_ROOT))
    stat.add_argument("--message-id", type=int, required=True)

    sub.add_parser("selftest")
    ns = parser.parse_args()

    try:
        if ns.cmd == "selftest":
            return selftest()
        if ns.cmd == "status":
            return _cmd_status(ns)
        return _cmd_commit(ns)
    except CompletionError as exc:
        print(f"COMPLETION_REJECTED code={exc.code} reason={exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # pragma: no cover - defensive
        print(f"COMPLETION_ERROR internal={type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
