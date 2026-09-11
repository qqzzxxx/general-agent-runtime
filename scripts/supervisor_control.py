"""Runtime-owned Supervisor control plane (SUPERVISOR-CONTROL-V1).

This module is intentionally usable both by the Orchestrator and by thin operator
commands.  Mutable operations serialize with Executor authorization/claim/completion
through EXECUTOR-FENCE-V1.  Query operations never take a write lock and never repair
state: corruption is reported, not hidden.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")
ARCHIVE_SCHEMA_VERSION = 1
CONTROL_SCHEMA_VERSION = 1
INTERVENTION_SCHEMA_VERSION = 1
INTERVENTION_TRANSACTION_SCHEMA_VERSION = 1
CANDIDATE_ORIGIN_SCHEMA_VERSION = 1
DECISION_RECEIPT_SCHEMA_VERSION = 1
INTERVENTION_MODES = ("STEER", "AUDIT")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ControlError(RuntimeError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def _failure_point(_name: str) -> None:
    """Test-only crash injection hook; production intentionally does nothing."""


def task_value(record: dict | None, key: str, default=None):
    if not isinstance(record, dict):
        return default
    if key in record:
        return record[key]
    return record.get(key.lower(), default)


def normalize_identity(record: dict | None, label: str = "identity") -> dict:
    identity = {key: task_value(record, key) for key in IDENTITY_KEYS}
    message_id, attempt = identity["MESSAGE_ID"], identity["ATTEMPT"]
    if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id < 0:
        raise ControlError(f"{label} MESSAGE_ID must be an integer >= 0")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ControlError(f"{label} ATTEMPT must be an integer >= 1")
    for key in ("TASK_ID", "STAGE_ID", "NONCE"):
        value = identity[key]
        if not isinstance(value, str) or not value or value != value.strip():
            raise ControlError(f"{label} {key} must be a non-empty trimmed string")
    return identity


def _read_json(path: Path, default=None):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        return value
    except FileNotFoundError:
        return default
    except Exception as exc:
        raise ControlError(f"invalid JSON: {path}: {exc}") from exc


def _atomic_write_bytes(path: Path, data: bytes, *, create_only: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if create_only:
            try:
                os.link(temp, path)
            except FileExistsError:
                raise
            finally:
                temp.unlink(missing_ok=True)
        else:
            os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _atomic_json(path: Path, value, *, create_only: bool = False) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    _atomic_write_bytes(path, data, create_only=create_only)


def resolve_active_project(root: Path) -> tuple[str | None, Path, Path]:
    """Return (project id, project root, project_state path), fail-closed."""
    root = Path(root).resolve()
    pointer_path = root / "control" / "ACTIVE_PROJECT.json"
    if not pointer_path.exists():
        return None, root, root / "control" / "project_state.json"
    pointer = _read_json(pointer_path)
    if not isinstance(pointer, dict) or set(pointer) != {
        "schema_version", "project_id", "project_root"
    } or pointer.get("schema_version") != 1:
        raise ControlError("ACTIVE_PROJECT.json schema mismatch")
    project_id = pointer.get("project_id")
    if not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id):
        raise ControlError("ACTIVE_PROJECT project_id is unsafe")
    expected = f"projects/{project_id}"
    if Path(str(pointer.get("project_root"))).as_posix() != expected:
        raise ControlError("ACTIVE_PROJECT project_root mismatch")
    project = (root / expected).resolve()
    if not project.is_relative_to((root / "projects").resolve()):
        raise ControlError("ACTIVE_PROJECT escapes Runtime projects")
    state_path = project / "project_state.json"
    if not state_path.is_file():
        raise ControlError("active project state is missing")
    return project_id, project, state_path


def project_key(project_id: str | None) -> str:
    return project_id if project_id is not None else "_legacy"


def archive_root(root: Path) -> Path:
    return Path(root) / "handoff" / "supervisor_dispatch_archive"


def _nonce_digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]


def archive_paths(root: Path, project_id: str | None, message_id: int, nonce: str) -> tuple[Path, Path]:
    stem = f"dispatch-{message_id}-{_nonce_digest(nonce)}"
    directory = archive_root(root) / project_key(project_id)
    return directory / f"{stem}.md", directory / f"{stem}.json"


def authorization_seal_path(root: Path, project_id: str | None,
                            message_id: int, nonce: str) -> Path:
    data_path, _ = archive_paths(root, project_id, message_id, nonce)
    return data_path.with_suffix(".authorized.json")


def _identity(record: dict) -> dict:
    return {key: task_value(record, key) for key in IDENTITY_KEYS}


def _same_identity(left: dict, right: dict) -> bool:
    try:
        return normalize_identity(left) == normalize_identity(right)
    except ControlError:
        return False


def archive_dispatch(root: Path, project_id: str | None, task: dict, dispatch_bytes: bytes,
                     archived_at: str | None = None, origin: dict | None = None) -> dict:
    """Durably preserve exact validated bytes before authorization becomes visible.

    Caller must hold the Runtime fence.  The exact byte file and self-describing
    metadata are the durable record; index views are rebuilt by scanning metadata.
    """
    identity = normalize_identity(task, "dispatch archive identity")
    message_id = identity["MESSAGE_ID"]
    nonce = identity["NONCE"]
    origin_fields = {
        "originating_control_revision": None,
        "supervisor_turn_id": None,
        "decision_receipt_sha256": None,
    }
    if origin is not None:
        revision = origin.get("originating_control_revision")
        turn_id = origin.get("supervisor_turn_id")
        receipt_hash = origin.get("decision_receipt_sha256")
        if (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
                or not isinstance(turn_id, str) or not turn_id
                or not isinstance(receipt_hash, str) or not HEX64.fullmatch(receipt_hash)):
            raise ControlError("dispatch archive Supervisor origin is invalid")
        origin_fields = {
            "originating_control_revision": revision,
            "supervisor_turn_id": turn_id,
            "decision_receipt_sha256": receipt_hash,
        }
    digest = sha256_bytes(dispatch_bytes)
    data_path, meta_path = archive_paths(root, project_id, message_id, nonce)

    # MESSAGE_ID is unique per project.  A different identity for the same id is
    # a collision, not a revision; revisions/retries require a fresh MESSAGE_ID.
    directory = data_path.parent
    if directory.is_dir():
        for existing_meta in directory.glob(f"dispatch-{message_id}-*.json"):
            existing = _read_json(existing_meta)
            if not isinstance(existing, dict):
                raise ControlError(f"dispatch archive metadata is corrupt: {existing_meta}")
            if not _same_identity(existing, identity):
                raise ControlError(f"dispatch archive MESSAGE_ID collision: {message_id}")
            if existing.get("dispatch_sha256") != digest:
                raise ControlError(f"dispatch archive identity/hash collision: {message_id}")
            for key, value in origin_fields.items():
                if value is not None and existing.get(key) != value:
                    raise ControlError(f"dispatch archive Supervisor origin collision: {message_id}")

    relative_data = data_path.relative_to(Path(root)).as_posix()
    metadata = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "PROJECT_ID": project_id,
        **identity,
        "archived_at": archived_at or now_iso(),
        "dispatch_sha256": digest,
        "archive_file": relative_data,
        **origin_fields,
    }

    if data_path.exists():
        if not data_path.is_file() or sha256_bytes(data_path.read_bytes()) != digest:
            raise ControlError(f"dispatch archive bytes conflict: {data_path}")
    else:
        _atomic_write_bytes(data_path, dispatch_bytes, create_only=True)

    if meta_path.exists():
        existing = _read_json(meta_path)
        stable_keys = {"schema_version", "PROJECT_ID", *IDENTITY_KEYS,
                       "dispatch_sha256", "archive_file", *origin_fields}
        if not isinstance(existing, dict) or any(existing.get(k) != metadata.get(k) for k in stable_keys):
            raise ControlError(f"dispatch archive metadata conflicts: {meta_path}")
        metadata = existing
    else:
        try:
            _atomic_json(meta_path, metadata, create_only=True)
        except Exception:
            # An exact orphan byte snapshot is safe and recoverable; it can never
            # authorize because the metadata binding was not returned/saved.
            raise
    return {
        **metadata,
        "metadata_file": meta_path.relative_to(Path(root)).as_posix(),
        "authorization_file": authorization_seal_path(
            root, project_id, message_id, nonce
        ).relative_to(Path(root)).as_posix(),
    }


def seal_dispatch_authorization(root: Path, authorization: dict) -> dict:
    """Make an archived authorization claimable; caller holds the fence lock."""
    binding = authorization.get("SUPERVISOR_DISPATCH_ARCHIVE")
    if not isinstance(binding, dict) or not isinstance(binding.get("authorization_file"), str):
        raise ControlError("authorization is missing its dispatch archive seal path")
    seal = {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        **{key: authorization.get(key) for key in IDENTITY_KEYS},
        "PROJECT_ID": authorization.get("PROJECT_ID"),
        "authorized_at": authorization.get("AUTHORIZED_AT"),
        "dispatch_sha256": authorization.get("TO_ZCODE_SHA256"),
        "metadata_file": binding.get("metadata_file"),
        "archive_file": binding.get("archive_file"),
        "originating_control_revision": authorization.get("SUPERVISOR_CONTROL_ORIGIN", {}).get(
            "originating_control_revision"),
        "supervisor_turn_id": authorization.get("SUPERVISOR_CONTROL_ORIGIN", {}).get(
            "supervisor_turn_id"),
        "decision_receipt_sha256": authorization.get("SUPERVISOR_CONTROL_ORIGIN", {}).get(
            "decision_receipt_sha256"),
    }
    path = Path(root) / binding["authorization_file"]
    if path.exists():
        existing = _read_json(path)
        if existing != seal:
            raise ControlError("dispatch authorization seal conflicts with existing history")
    else:
        _atomic_json(path, seal, create_only=True)
    return seal


def verify_archive_binding(root: Path, authorization: dict) -> tuple[bool, str]:
    binding = authorization.get("SUPERVISOR_DISPATCH_ARCHIVE")
    if not isinstance(binding, dict):
        return False, "dispatch_archive_binding_missing"
    meta_raw = binding.get("metadata_file")
    data_raw = binding.get("archive_file")
    seal_raw = binding.get("authorization_file")
    if not all(isinstance(item, str) for item in (meta_raw, data_raw, seal_raw)):
        return False, "dispatch_archive_paths_missing"
    try:
        identity = normalize_identity(authorization, "authorization identity")
    except ControlError:
        return False, "dispatch_archive_identity_invalid"
    project_id = authorization.get("PROJECT_ID")
    if project_id is not None and (
        not isinstance(project_id, str) or not PROJECT_ID_PATTERN.fullmatch(project_id)
    ):
        return False, "dispatch_archive_project_invalid"
    origin = authorization.get("SUPERVISOR_CONTROL_ORIGIN")
    if not isinstance(origin, dict):
        return False, "dispatch_archive_origin_missing"
    revision = origin.get("originating_control_revision")
    turn_id = origin.get("supervisor_turn_id")
    receipt_hash = origin.get("decision_receipt_sha256")
    if (isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
            or not isinstance(turn_id, str) or not turn_id
            or not isinstance(receipt_hash, str) or not HEX64.fullmatch(receipt_hash)):
        return False, "dispatch_archive_origin_invalid"
    root = Path(root).resolve()
    try:
        meta_path = (root / meta_raw).resolve()
        data_path = (root / data_raw).resolve()
        seal_path = (root / seal_raw).resolve()
        allowed = archive_root(root).resolve()
        if (not meta_path.is_relative_to(allowed) or not data_path.is_relative_to(allowed)
                or not seal_path.is_relative_to(allowed)):
            return False, "dispatch_archive_path_escape"
        expected_data, expected_meta = archive_paths(
            root, project_id, identity["MESSAGE_ID"], identity["NONCE"]
        )
        expected_seal = authorization_seal_path(
            root, project_id, identity["MESSAGE_ID"], identity["NONCE"]
        )
        if (meta_path != expected_meta.resolve() or data_path != expected_data.resolve()
                or seal_path != expected_seal.resolve()):
            return False, "dispatch_archive_path_identity_mismatch"
        metadata = _read_json(meta_path)
        seal = _read_json(seal_path)
        data = data_path.read_bytes()
    except Exception as exc:
        return False, f"dispatch_archive_unavailable:{type(exc).__name__}"
    if not isinstance(metadata, dict) or metadata.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
        return False, "dispatch_archive_metadata_invalid"
    required_seal = {
        "schema_version", *IDENTITY_KEYS, "PROJECT_ID", "authorized_at",
        "dispatch_sha256", "metadata_file", "archive_file",
        "originating_control_revision", "supervisor_turn_id",
        "decision_receipt_sha256",
    }
    if (not isinstance(seal, dict) or set(seal) != required_seal
            or seal.get("schema_version") != ARCHIVE_SCHEMA_VERSION
            or not _same_identity(seal, authorization)
            or seal.get("PROJECT_ID") != authorization.get("PROJECT_ID")
            or seal.get("dispatch_sha256") != authorization.get("TO_ZCODE_SHA256")
            or seal.get("metadata_file") != meta_raw or seal.get("archive_file") != data_raw
            or seal.get("originating_control_revision") != revision
            or seal.get("supervisor_turn_id") != turn_id
            or seal.get("decision_receipt_sha256") != receipt_hash):
        return False, "dispatch_archive_authorization_seal_invalid"
    required_metadata = {
        "schema_version", "PROJECT_ID", *IDENTITY_KEYS, "archived_at",
        "dispatch_sha256", "archive_file", "originating_control_revision",
        "supervisor_turn_id", "decision_receipt_sha256",
    }
    if set(metadata) != required_metadata:
        return False, "dispatch_archive_metadata_schema_invalid"
    if not _same_identity(metadata, authorization) or metadata.get("PROJECT_ID") != project_id:
        return False, "dispatch_archive_identity_mismatch"
    expected = authorization.get("TO_ZCODE_SHA256")
    actual = sha256_bytes(data)
    if (not isinstance(expected, str) or not HEX64.fullmatch(expected)
            or metadata.get("dispatch_sha256") != expected or actual != expected
            or metadata.get("archive_file") != data_raw
            or metadata.get("originating_control_revision") != revision
            or metadata.get("supervisor_turn_id") != turn_id
            or metadata.get("decision_receipt_sha256") != receipt_hash):
        return False, "dispatch_archive_hash_mismatch"
    try:
        payload_identity = normalize_identity(
            _parse_dispatch_bytes(data), "archived dispatch payload identity"
        )
    except ControlError:
        return False, "dispatch_archive_payload_invalid"
    if payload_identity != identity:
        return False, "dispatch_archive_payload_identity_mismatch"
    return True, "OK"


def list_dispatches(root: Path, project_id: str | None = None) -> list[dict]:
    """Return read-only archive trust classifications, including incomplete records."""
    root = Path(root).resolve()
    directory = archive_root(root) / project_key(project_id)
    if not directory.is_dir():
        return []
    stems = set()
    for path in directory.glob("dispatch-*"):
        name = path.name
        if name.endswith(".authorized.json"):
            stems.add(name[:-len(".authorized.json")])
        elif name.endswith(".json"):
            stems.add(name[:-len(".json")])
        elif name.endswith(".md"):
            stems.add(name[:-len(".md")])
    records = []
    for stem in sorted(stems):
        meta_path = directory / f"{stem}.json"
        data_path = directory / f"{stem}.md"
        seal_path = directory / f"{stem}.authorized.json"
        id_match = re.fullmatch(r"dispatch-(\d+)-[0-9a-f]{24}", stem)
        filename_id = int(id_match.group(1)) if id_match else None
        record = {
            "MESSAGE_ID": filename_id,
            "metadata_file": meta_path.relative_to(root).as_posix(),
            "archive_file": data_path.relative_to(root).as_posix(),
            "authorization_file": seal_path.relative_to(root).as_posix(),
            "integrity": "INCOMPLETE",
            "trust_status": "INCOMPLETE",
        }
        allowed_directory = directory.resolve()
        if any(not path.resolve().is_relative_to(allowed_directory)
               for path in (meta_path, data_path, seal_path)):
            record["integrity"] = "CORRUPT"
            record["trust_status"] = "CORRUPT"
            record["error"] = "archive record path escapes its project archive directory"
            records.append(record)
            continue
        if not meta_path.is_file() or not data_path.is_file():
            record["error"] = "archive metadata or exact-byte file is missing"
            records.append(record)
            continue
        try:
            value = _read_json(meta_path)
            if not isinstance(value, dict):
                raise ControlError("metadata is not an object")
            expected_keys = {
                "schema_version", "PROJECT_ID", *IDENTITY_KEYS, "archived_at",
                "dispatch_sha256", "archive_file", "originating_control_revision",
                "supervisor_turn_id", "decision_receipt_sha256",
            }
            if set(value) != expected_keys or value.get("schema_version") != ARCHIVE_SCHEMA_VERSION:
                raise ControlError("archive metadata schema is invalid")
            identity = normalize_identity(value, "archive metadata identity")
            if value.get("PROJECT_ID") != project_id or identity["MESSAGE_ID"] != filename_id:
                raise ControlError("archive metadata project/identity mismatch")
            expected_data, expected_meta = archive_paths(
                root, project_id, identity["MESSAGE_ID"], identity["NONCE"]
            )
            if meta_path.resolve() != expected_meta.resolve() or data_path.resolve() != expected_data.resolve():
                raise ControlError("archive filename does not bind its identity")
            declared_data = (root / str(value.get("archive_file"))).resolve()
            if (not declared_data.is_relative_to(archive_root(root).resolve())
                    or declared_data != data_path.resolve()):
                raise ControlError("archive_file escapes or does not bind this record")
            expected = value.get("dispatch_sha256")
            if not isinstance(expected, str) or not HEX64.fullmatch(expected):
                raise ControlError("archive dispatch_sha256 is invalid")
            data = data_path.read_bytes()
            actual = sha256_bytes(data)
            if actual != expected:
                raise ControlError("archive exact bytes do not match dispatch_sha256")
            payload_identity = normalize_identity(
                _parse_dispatch_bytes(data), "archived dispatch payload identity"
            )
            if payload_identity != identity:
                raise ControlError(
                    "archived dispatch payload identity does not match metadata"
                )
            record = {**value, **record}
            record["actual_sha256"] = actual
            if not seal_path.is_file():
                record["integrity"] = "UNAUTHORIZED"
                record["trust_status"] = "UNAUTHORIZED"
                record["error"] = "authorization seal is missing"
                records.append(record)
                continue
            seal = _read_json(seal_path)
            if not isinstance(seal, dict):
                raise ControlError("authorization seal is invalid")
            authorization = {
                "schema_version": 1,
                **identity,
                "PROJECT_ID": project_id,
                "TO_ZCODE_SHA256": expected,
                "AUTHORIZED_AT": seal.get("authorized_at"),
                "SUPERVISOR_CONTROL_ORIGIN": {
                    "originating_control_revision": value.get("originating_control_revision"),
                    "supervisor_turn_id": value.get("supervisor_turn_id"),
                    "decision_receipt_sha256": value.get("decision_receipt_sha256"),
                },
                "SUPERVISOR_DISPATCH_ARCHIVE": {
                    "schema_version": ARCHIVE_SCHEMA_VERSION,
                    "metadata_file": meta_path.relative_to(root).as_posix(),
                    "archive_file": data_path.relative_to(root).as_posix(),
                    "authorization_file": seal_path.relative_to(root).as_posix(),
                    "dispatch_sha256": expected,
                },
            }
            ok, reason = verify_archive_binding(root, authorization)
            if not ok:
                raise ControlError(reason)
            record["integrity"] = "AUTHORIZED_VALID"
            record["trust_status"] = "AUTHORIZED_VALID"
        except Exception as exc:
            record["integrity"] = "CORRUPT"
            record["trust_status"] = "CORRUPT"
            record["error"] = str(exc)
        records.append(record)
    return sorted(records, key=lambda r: (
        r.get("MESSAGE_ID") if isinstance(r.get("MESSAGE_ID"), int) else -1,
        str(r.get("archived_at", "")),
    ))


def find_dispatch(root: Path, message_id: int, project_id: str | None) -> dict:
    matches = [r for r in list_dispatches(root, project_id)
               if r.get("MESSAGE_ID") == message_id]
    if not matches:
        raise ControlError(f"no archived Supervisor dispatch for MESSAGE_ID={message_id}")
    if len(matches) != 1:
        raise ControlError(f"ambiguous Supervisor archive for MESSAGE_ID={message_id}")
    return matches[0]


def list_feedback(root: Path, project_id: str | None = None) -> list[dict]:
    root = Path(root).resolve()
    scripts = str(root / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import executor_completion as completion
    directory = completion.ledger_dir(root)
    records = []
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("completion-*.json")):
        entry = completion.load_entry_file(path)
        if entry is None:
            records.append({"ledger_file": path.relative_to(root).as_posix(),
                            "integrity": "INVALID_LEDGER_ENTRY"})
            continue
        if entry.get("PROJECT_ID") != project_id:
            continue
        record = dict(entry)
        record["ledger_file"] = path.relative_to(root).as_posix()
        record["integrity"] = "OK" if completion.entry_hashes_intact(entry) else "HASH_MISMATCH"
        records.append(record)
    return sorted(records, key=lambda r: (int(r.get("MESSAGE_ID", -1)), str(r.get("COMMITTED_AT", ""))))


def find_feedback(root: Path, message_id: int, project_id: str | None) -> dict:
    matches = [r for r in list_feedback(root, project_id)
               if r.get("MESSAGE_ID") == message_id]
    if not matches:
        directory = Path(root) / "handoff" / "completion_ledger"
        if any(directory.glob(f"completion-{message_id}-*.json")):
            raise ControlError(
                f"Executor completion ledger integrity is broken for MESSAGE_ID={message_id}"
            )
        raise ControlError(f"no authoritative Executor completion for MESSAGE_ID={message_id}")
    if len(matches) != 1:
        raise ControlError(f"ambiguous completion ledger for MESSAGE_ID={message_id}")
    return matches[0]


def control_path(root: Path) -> Path:
    return Path(root) / "control" / "supervisor_control.json"


def load_control(root: Path) -> dict:
    value = _read_json(control_path(root), None)
    if value is None:
        return {
            "schema_version": CONTROL_SCHEMA_VERSION,
            "revision": 0,
            "intervention_generation": 0,
            "pause": {"status": "RUNNING", "requested_at": None,
                      "mode": None, "resumed_at": None},
        }
    if not isinstance(value, dict) or value.get("schema_version") != CONTROL_SCHEMA_VERSION:
        raise ControlError("supervisor_control.json schema mismatch")
    if not isinstance(value.get("revision"), int) or not isinstance(value.get("intervention_generation"), int):
        raise ControlError("supervisor_control.json counters are invalid")
    return value


def save_control(root: Path, value: dict) -> None:
    value["updated_at"] = now_iso()
    _atomic_json(control_path(root), value)


def intervention_root(root: Path, project_id: str | None) -> Path:
    return Path(root) / "handoff" / "supervisor_interventions" / project_key(project_id)


def list_interventions(root: Path, project_id: str | None = None) -> list[dict]:
    root = Path(root).resolve()
    base = root / "handoff" / "supervisor_interventions"
    if not base.is_dir():
        return []
    directories = [base / project_key(project_id)]
    result = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for request_dir in directory.iterdir():
            if not request_dir.is_dir():
                continue
            try:
                meta = _read_json(request_dir / "intervention.json")
                status = _read_json(request_dir / "status.json", {}) or {}
                if meta is None:
                    txn = _read_json(request_dir / "transaction.json", None)
                    meta, data = _validate_intervention_transaction(txn)
                    record = {**meta, "status": "RECOVERY_REQUIRED", "integrity": "OK",
                              "instruction_text": data.decode("utf-8")}
                    result.append(record)
                    continue
                if not isinstance(meta, dict):
                    raise ControlError("intervention metadata is invalid")
                required = {"schema_version", "intervention_id", "PROJECT_ID", "mode",
                            "target_message_id", "interrupt_current", "submitted_at",
                            "instruction_sha256", "instruction_file"}
                if set(meta) != required or meta.get("schema_version") != INTERVENTION_SCHEMA_VERSION:
                    raise ControlError("intervention metadata schema is invalid")
                if meta.get("intervention_id") != request_dir.name or meta.get("PROJECT_ID") != project_id:
                    raise ControlError("intervention identity/project mismatch")
                instruction_path = (root / meta["instruction_file"]).resolve()
                if (not instruction_path.is_relative_to(base.resolve())
                        or instruction_path != (request_dir / "instruction.txt").resolve()):
                    raise ControlError("intervention instruction path escapes archive")
                data = instruction_path.read_bytes()
                actual = sha256_bytes(data)
                record = {**meta, **status, "integrity": (
                    "OK" if actual == meta.get("instruction_sha256") else "HASH_MISMATCH"
                )}
                record["instruction_text"] = data.decode("utf-8")
                receipt_raw = status.get("decision_receipt_file")
                receipt_hash = status.get("decision_receipt_sha256")
                if record["integrity"] == "OK" and receipt_raw is not None:
                    receipt_path = (root / str(receipt_raw)).resolve()
                    allowed = (root / "control" / "supervisor_decisions").resolve()
                    if not receipt_path.is_relative_to(allowed):
                        raise ControlError("intervention decision receipt path escapes control history")
                    receipt = _read_json(receipt_path, None)
                    if (not isinstance(receipt, dict)
                            or sha256_bytes(canonical_json_bytes(receipt)) != receipt_hash):
                        raise ControlError("intervention decision receipt hash mismatch")
                    record["supervisor_result"] = {
                        "last_supervisor_decision": receipt.get("decision"),
                        "decision_history_index": receipt.get("decision_history_index"),
                        "decision_sha256": receipt.get("decision_sha256"),
                    }
            except Exception as exc:
                record = {"intervention_dir": str(request_dir), "integrity": "ERROR", "error": str(exc)}
            result.append(record)
    return sorted(result, key=lambda r: str(r.get("submitted_at", "")))


def pending_interventions(root: Path, project_id: str | None) -> list[dict]:
    return [item for item in list_interventions(root, project_id)
            if item.get("status") in {"PENDING", "INJECTED", "RECOVERY_REQUIRED"}
            and item.get("integrity") == "OK"]


def _claim_exists(root: Path, identity: dict) -> bool:
    try:
        normalized = normalize_identity(identity, "claim identity")
        scripts = str(Path(root) / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import executor_completion as completion
        claim, path = completion.load_claim(root, normalized)
        return (path.is_dir() and isinstance(claim, dict)
                and completion.identity_values_match(claim, normalized))
    except Exception:
        return False


def _completion_for_identity(root: Path, identity: dict) -> dict | None:
    try:
        normalized = normalize_identity(identity, "completion identity")
        scripts = str(Path(root) / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import executor_completion as completion
        return next((entry for entry in completion.lookup_entries(
            root, normalized["MESSAGE_ID"]
        ) if completion.identity_values_match(entry, normalized)), None)
    except Exception:
        return None


def _is_retired(runtime: dict, identity: dict) -> bool:
    normalized = normalize_identity(identity, "retirement identity")
    retired = runtime.get("retired_message_ids") or []
    if not isinstance(retired, list) or any(type(value) is not int for value in retired):
        raise ControlError("retired_message_ids is malformed")
    return normalized["MESSAGE_ID"] in retired


def _active_lifecycle(root: Path, runtime: dict, active: dict | None,
                      current: dict | None) -> dict:
    if not (isinstance(active, dict) and isinstance(current, dict)
            and _same_identity(active, current)):
        return {"identity": None, "claimed": False, "completion": None,
                "retired": False, "running": False}
    identity = normalize_identity(active, "active task identity")
    completion = _completion_for_identity(root, identity)
    claimed = _claim_exists(root, identity)
    retired = _is_retired(runtime, identity)
    return {
        "identity": identity,
        "claimed": claimed,
        "completion": completion,
        "retired": retired,
        "running": claimed and completion is None and not retired,
    }


def _retire(runtime: dict, identity: dict, reason: str) -> None:
    normalized = normalize_identity(identity, "retirement identity")
    message_id = normalized["MESSAGE_ID"]
    retired = runtime.setdefault("retired_message_ids", [])
    if not isinstance(retired, list) or any(type(value) is not int for value in retired):
        raise ControlError("retired_message_ids is malformed")
    if message_id not in retired:
        retired.append(message_id)
        runtime.setdefault("executor_retirements", []).append({
            **normalized,
            "RETIRED_AT": now_iso(), "REASON": reason, "SUPERSEDED_BY": None,
        })


def _quarantine_current(root: Path, identity: dict, label: str) -> str | None:
    inbox = Path(root) / "TO_ZCODE.md"
    if not inbox.is_file():
        return None
    data = inbox.read_bytes()
    message_id = identity.get("MESSAGE_ID", "unknown")
    digest = sha256_bytes(data)
    target = (Path(root) / "handoff" / "quarantine" /
              f"to-zcode-{message_id}-{label.lower()}-{digest[:12]}.md")
    if target.exists() and target.read_bytes() != data:
        raise ControlError("quarantine collision")
    if not target.exists():
        _atomic_write_bytes(target, data, create_only=True)
    inbox.unlink(missing_ok=True)
    return target.relative_to(Path(root)).as_posix()


def _load_live(root: Path) -> tuple[str | None, Path, Path, dict, dict]:
    project_id, project, state_path = resolve_active_project(root)
    state = _read_json(state_path)
    runtime = _read_json(Path(root) / "control" / "orchestrator_runtime.json", {}) or {}
    if not isinstance(state, dict) or not isinstance(runtime, dict):
        raise ControlError("Runtime/project state is unavailable")
    return project_id, project, state_path, state, runtime


def _intervention_transaction_paths(directory: Path) -> tuple[Path, Path]:
    return directory / "transaction.json", directory / "committed.json"


def _intervention_plan(root: Path, state: dict, runtime: dict,
                       *, interrupt_current: bool, kind: str) -> dict:
    active = runtime.get("authorized_dispatch")
    current = state.get("current_task") if state.get("status") == "WAITING_EXECUTOR" else None
    active_view = _active_lifecycle(root, runtime, active, current)
    subject = active_view["identity"]
    if subject is None and isinstance(current, dict):
        subject = normalize_identity(current, "current task identity")
    prefix = "HUMAN_INTERVENTION" if kind == "INTERVENTION" else "PAUSE"
    if active_view["completion"] is not None:
        return {"action": "COMPLETION_WINS", "subject_identity": subject,
                "disposition": "COMPLETION_COMMITTED_WINS"}
    if active_view["running"] and not interrupt_current:
        return {"action": "PRESERVE_RUNNING", "subject_identity": subject,
                "disposition": ("PENDING_AFTER_CURRENT_STAGE" if kind == "INTERVENTION"
                                else "PAUSE_PENDING_AFTER_CURRENT_STAGE")}
    if subject is not None:
        return {
            "action": "RETIRE", "subject_identity": subject,
            "retirement_reason": (
                f"{prefix}_INTERRUPT" if interrupt_current
                else f"{prefix}_BEFORE_CLAIM"
            ),
            "disposition": (
                ("CURRENT_TASK_RETIRED" if kind == "INTERVENTION"
                 else "PAUSED_CURRENT_REVOKED") if active_view["running"]
                else ("UNCLAIMED_TASK_RETIRED" if kind == "INTERVENTION"
                      else "PAUSED_UNCLAIMED_RETIRED")
            ),
        }
    if kind == "INTERVENTION" and state.get("status") in {"COMPLETE", "BLOCKED"}:
        return {"action": "REOPEN_TERMINAL", "subject_identity": None,
                "previous_status": state["status"],
                "disposition": f"{state['status']}_REOPENED_FOR_SUPERVISOR"}
    return {"action": "NONE", "subject_identity": None,
            "disposition": "PENDING_NEXT_SUPERVISOR_TURN" if kind == "INTERVENTION"
            else "PAUSED_IDLE"}


def _validate_intervention_transaction(txn: dict) -> tuple[dict, bytes]:
    if not isinstance(txn, dict) or txn.get("schema_version") != INTERVENTION_TRANSACTION_SCHEMA_VERSION:
        raise ControlError("intervention transaction schema mismatch")
    meta = txn.get("intervention")
    if not isinstance(meta, dict) or meta.get("schema_version") != INTERVENTION_SCHEMA_VERSION:
        raise ControlError("intervention transaction metadata is invalid")
    try:
        data = base64.b64decode(txn["instruction_base64"], validate=True)
    except Exception as exc:
        raise ControlError("intervention transaction instruction encoding is invalid") from exc
    if not data or sha256_bytes(data) != meta.get("instruction_sha256"):
        raise ControlError("intervention transaction instruction hash mismatch")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError("intervention transaction instruction is not UTF-8") from exc
    from_revision, to_revision = txn.get("from_revision"), txn.get("to_revision")
    if (isinstance(from_revision, bool) or not isinstance(from_revision, int)
            or from_revision < 0 or to_revision != from_revision + 1):
        raise ControlError("intervention transaction revision binding is invalid")
    plan = txn.get("plan")
    if not isinstance(plan, dict) or plan.get("action") not in {
        "COMPLETION_WINS", "PRESERVE_RUNNING", "RETIRE", "REOPEN_TERMINAL", "NONE"
    }:
        raise ControlError("intervention transaction plan is invalid")
    if plan.get("subject_identity") is not None:
        normalize_identity(plan["subject_identity"], "transaction subject identity")
    return meta, data


def _apply_intervention_transaction_locked(root: Path, txn_path: Path) -> dict:
    """Idempotently finish one accepted transaction. Caller holds the fence."""
    txn = _read_json(txn_path)
    meta, data = _validate_intervention_transaction(txn)
    directory = txn_path.parent
    _, committed_path = _intervention_transaction_paths(directory)
    existing_commit = _read_json(committed_path, None)
    if isinstance(existing_commit, dict):
        return existing_commit

    active_project, _, state_path, state, runtime = _load_live(root)
    if meta.get("PROJECT_ID") != active_project:
        raise ControlError("pending intervention transaction belongs to another active project")
    control = load_control(root)
    from_revision, to_revision = txn["from_revision"], txn["to_revision"]
    if control["revision"] < from_revision or control["revision"] > to_revision:
        raise ControlError("intervention transaction conflicts with control revision")
    if control["revision"] == from_revision:
        control["revision"] = to_revision
        control["intervention_generation"] = max(
            int(control.get("intervention_generation", 0)) + 1,
            int(txn.get("intervention_generation", 0)),
        )
        control["last_intervention_id"] = meta["intervention_id"]
        save_control(root, control)
        _failure_point("intervention_after_control_revision")

    instruction_path = root / meta["instruction_file"]
    meta_path = directory / "intervention.json"
    status_path = directory / "status.json"
    if instruction_path.exists():
        if instruction_path.read_bytes() != data:
            raise ControlError("intervention instruction conflicts with transaction")
    else:
        _atomic_write_bytes(instruction_path, data, create_only=True)
        _failure_point("intervention_after_instruction")
    if meta_path.exists():
        if _read_json(meta_path) != meta:
            raise ControlError("intervention metadata conflicts with transaction")
    else:
        _atomic_json(meta_path, meta, create_only=True)
        _failure_point("intervention_after_metadata")
    if not status_path.exists():
        _atomic_json(status_path, {"status": "PENDING", "updated_at": meta["submitted_at"]},
                     create_only=True)
        _failure_point("intervention_after_pending_status")

    plan = txn["plan"]
    action = plan["action"]
    subject = plan.get("subject_identity")
    # If a completion committed before the intervention journal won the fence, the
    # completion is authoritative and retirement is deferred to the next boundary.
    if isinstance(subject, dict) and _completion_for_identity(root, subject) is not None:
        action = "COMPLETION_WINS"
    quarantine = None
    if action == "RETIRE":
        _retire(runtime, subject, plan["retirement_reason"])
        current = state.get("current_task") if state.get("status") == "WAITING_EXECUTOR" else None
        if isinstance(current, dict) and _same_identity(current, subject):
            quarantine = _quarantine_current(root, subject,
                                             "intervention")
            state["status"] = "SUPERVISOR_TURN"
            state["current_task"] = None
            state["updated_at"] = now_iso()
            _atomic_json(state_path, state)
            _failure_point("intervention_after_project_state")
        _atomic_json(root / "control" / "orchestrator_runtime.json", runtime)
        _failure_point("intervention_after_runtime_state")
    elif action == "REOPEN_TERMINAL" and state.get("status") in {"COMPLETE", "BLOCKED"}:
        state["status"] = "SUPERVISOR_TURN"
        state["current_task"] = None
        state["updated_at"] = now_iso()
        _atomic_json(state_path, state)
        _failure_point("intervention_after_project_state")

    disposition = ("COMPLETION_COMMITTED_WINS" if action == "COMPLETION_WINS"
                   else plan["disposition"])
    control = load_control(root)
    control["last_intervention_id"] = meta["intervention_id"]
    control["last_intervention_disposition"] = disposition
    if quarantine is not None:
        control["last_quarantined_candidate"] = quarantine
    save_control(root, control)
    _failure_point("intervention_after_control_bookkeeping")
    commit = {
        "schema_version": INTERVENTION_TRANSACTION_SCHEMA_VERSION,
        "intervention_id": meta["intervention_id"],
        "PROJECT_ID": meta["PROJECT_ID"],
        "committed_control_revision": to_revision,
        "committed_at": now_iso(),
        "disposition": disposition,
        "quarantine": quarantine,
    }
    _atomic_json(committed_path, commit, create_only=True)
    _failure_point("intervention_after_commit_receipt")
    return commit


def _apply_pause_transaction_locked(root: Path) -> dict | None:
    """Idempotently finish retirement/state writes after the durable pause commit."""
    control = load_control(root)
    pause = dict(control.get("pause") or {})
    txn = pause.get("transaction")
    if txn is None:
        return None
    if (not isinstance(txn, dict) or txn.get("schema_version") != 1
            or txn.get("control_revision") != control.get("revision")
            or txn.get("action") not in {
                "COMPLETION_WINS", "PRESERVE_RUNNING", "RETIRE", "NONE"
            }):
        raise ControlError("pause transaction is malformed")
    subject = txn.get("subject_identity")
    if subject is not None:
        subject = normalize_identity(subject, "pause transaction subject identity")

    _, _, state_path, state, runtime = _load_live(root)
    action = txn["action"]
    # A completion visible before PAUSE obtained the fence is authoritative. A
    # completion attempting to commit after this point first reconciles this
    # transaction and therefore observes the retirement.
    if subject is not None and _completion_for_identity(root, subject) is not None:
        action = "COMPLETION_WINS"
    quarantine = None
    if action == "RETIRE":
        _retire(runtime, subject, txn["retirement_reason"])
        quarantine = _quarantine_current(root, subject, "pause")
        current = state.get("current_task") if state.get("status") == "WAITING_EXECUTOR" else None
        if isinstance(current, dict) and _same_identity(current, subject):
            state["status"] = "SUPERVISOR_TURN"
            state["current_task"] = None
            state["updated_at"] = now_iso()
            _atomic_json(state_path, state)
            _failure_point("pause_after_project_state")

    runtime["status"] = ("PAUSE_PENDING_AFTER_CURRENT_STAGE"
                         if action in {"PRESERVE_RUNNING", "COMPLETION_WINS"}
                         else "PAUSED")
    _atomic_json(root / "control" / "orchestrator_runtime.json", runtime)
    _failure_point("pause_after_runtime_state")

    control = load_control(root)
    pause = dict(control.get("pause") or {})
    current_txn = pause.get("transaction")
    if current_txn != txn:
        raise ControlError("pause transaction changed during recovery")
    disposition = ("COMPLETION_COMMITTED_WINS" if action == "COMPLETION_WINS"
                   else txn["disposition"])
    pause["disposition"] = disposition
    pause["transaction_receipt"] = {
        **txn,
        "applied_action": action,
        "applied_at": now_iso(),
        "quarantine": quarantine,
    }
    pause.pop("transaction", None)
    if quarantine is not None:
        control["last_quarantined_candidate"] = quarantine
    control["pause"] = pause
    save_control(root, control)
    _failure_point("pause_after_transaction_commit")
    return pause


def reconcile_control_transactions_locked(root: Path) -> list[dict]:
    """Finish accepted intervention journals before any authority decision."""
    root = Path(root).resolve()
    _apply_pause_transaction_locked(root)
    base = root / "handoff" / "supervisor_interventions"
    if not base.is_dir():
        return []
    project_id, _, _ = resolve_active_project(root)
    directory = intervention_root(root, project_id)
    recovered = []
    if not directory.is_dir():
        return recovered
    for txn_path in sorted(directory.glob("intervention-*/transaction.json")):
        _, committed = _intervention_transaction_paths(txn_path.parent)
        if committed.exists():
            continue
        recovered.append(_apply_intervention_transaction_locked(root, txn_path))
    return recovered


def uncommitted_intervention_transactions(root: Path) -> list[str]:
    """Read-only fail-closed signal used by claim verification."""
    root = Path(root).resolve()
    base = root / "handoff" / "supervisor_interventions"
    if not base.is_dir():
        return []
    project_id, _, _ = resolve_active_project(root)
    directory = intervention_root(root, project_id)
    if not directory.is_dir():
        return []
    return [txn.parent.name for txn in sorted(directory.glob("intervention-*/transaction.json"))
            if not (txn.parent / "committed.json").is_file()]


def reconcile_control_transactions(root: Path) -> list[dict]:
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        return reconcile_control_transactions_locked(root)


def submit_intervention(root: Path, text_bytes: bytes, mode: str = "STEER",
                        target_message_id: int | None = None,
                        interrupt_current: bool = False) -> dict:
    root = Path(root).resolve()
    if not text_bytes or len(text_bytes) > 1024 * 1024:
        raise ControlError("intervention must contain 1..1048576 bytes")
    try:
        text_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ControlError("intervention must be valid UTF-8") from exc
    mode = str(mode).upper()
    if mode not in INTERVENTION_MODES:
        raise ControlError("Mode must be STEER or AUDIT")

    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        project_id, _, state_path, state, runtime = _load_live(root)
        if state.get("status") == "STOPPED" or (root / "control" / "STOP").exists():
            raise ControlError("STOP is terminal; intervention cannot bypass STOP")
        target = None
        if target_message_id is not None:
            target = find_dispatch(root, target_message_id, project_id)
            if target.get("integrity") != "AUTHORIZED_VALID":
                raise ControlError("target Supervisor dispatch archive integrity is broken")
        control = load_control(root)
        request_id = f"intervention-{uuid.uuid4().hex}"
        directory = intervention_root(root, project_id) / request_id
        instruction_path = directory / "instruction.txt"
        meta_path = directory / "intervention.json"
        status_path = directory / "status.json"
        submitted_at = now_iso()
        meta = {
            "schema_version": INTERVENTION_SCHEMA_VERSION,
            "intervention_id": request_id,
            "PROJECT_ID": project_id,
            "mode": mode,
            "target_message_id": target_message_id,
            "interrupt_current": bool(interrupt_current),
            "submitted_at": submitted_at,
            "instruction_sha256": sha256_bytes(text_bytes),
            "instruction_file": instruction_path.relative_to(root).as_posix(),
        }
        plan = _intervention_plan(root, state, runtime,
                                  interrupt_current=interrupt_current, kind="INTERVENTION")
        txn = {
            "schema_version": INTERVENTION_TRANSACTION_SCHEMA_VERSION,
            "intervention": meta,
            "instruction_base64": base64.b64encode(text_bytes).decode("ascii"),
            "from_revision": control["revision"],
            "to_revision": control["revision"] + 1,
            "intervention_generation": control["intervention_generation"] + 1,
            "plan": plan,
            "accepted_at": submitted_at,
        }
        txn_path, _ = _intervention_transaction_paths(directory)
        _atomic_json(txn_path, txn, create_only=True)
        _failure_point("intervention_after_journal")
        committed = _apply_intervention_transaction_locked(root, txn_path)
        return {**meta, "status": "PENDING", "disposition": committed["disposition"],
                "target": target}


def set_pause(root: Path, interrupt_current: bool = False) -> dict:
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        project_id, _, state_path, state, runtime = _load_live(root)
        control = load_control(root)
        plan = _intervention_plan(root, state, runtime,
                                  interrupt_current=interrupt_current, kind="PAUSE")
        action = plan["action"]
        disposition = plan["disposition"]
        if action == "COMPLETION_WINS":
            disposition = "COMPLETION_COMMITTED_WINS"
        control["revision"] += 1
        control["pause"] = {
            "status": ("PENDING_AFTER_CURRENT_STAGE"
                       if action in {"PRESERVE_RUNNING", "COMPLETION_WINS"} else "PAUSED"),
            "requested_at": now_iso(),
            "mode": "INTERRUPT_CURRENT" if interrupt_current else "SAFE",
            "resumed_at": None,
            "PROJECT_ID": project_id,
            "disposition": disposition,
            "transaction": {
                "schema_version": 1,
                "control_revision": control["revision"],
                "action": action,
                "subject_identity": plan.get("subject_identity"),
                "retirement_reason": plan.get("retirement_reason"),
                "disposition": disposition,
            },
        }
        # This is the pause commit point. Once durable, begin/register/claim all
        # reject new work even if the following retirement bookkeeping crashes.
        save_control(root, control)
        _failure_point("pause_after_control_commit")
        return _apply_pause_transaction_locked(root)


def resume(root: Path) -> dict:
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        _, _, _, state, _ = _load_live(root)
        if (root / "control" / "STOP").exists() or state.get("status") == "STOPPED":
            raise ControlError("STOP is terminal; remove/recover it through the documented STOP path")
        if ((root / "control" / "HUMAN_REVIEW").exists()
                or state.get("status") == "HUMAN_REVIEW"):
            raise ControlError("HUMAN_REVIEW requires RESUME_HUMAN_REVIEW.ps1")
        control = load_control(root)
        previous = dict(control.get("pause") or {})
        control["revision"] += 1
        control["pause"] = {"status": "RUNNING", "requested_at": previous.get("requested_at"),
                            "mode": previous.get("mode"), "resumed_at": now_iso()}
        save_control(root, control)
        return {"previous": previous, "current": control["pause"]}


def pause_status(root: Path) -> str:
    return str((load_control(root).get("pause") or {}).get("status") or "RUNNING")


def settle_pause(root: Path, disposition: str) -> dict:
    """Mark a previously requested pause mechanically reached."""
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        control = load_control(root)
        pause = dict(control.get("pause") or {})
        if pause.get("status") == "RUNNING":
            return pause
        pause["status"] = "PAUSED"
        pause["paused_at"] = now_iso()
        pause["disposition"] = disposition
        control["pause"] = pause
        save_control(root, control)
        return pause


def candidate_origin_path(root: Path) -> Path:
    return Path(root) / "control" / "supervisor_candidate.json"


def decision_receipt_path(root: Path, turn_id: str) -> Path:
    return Path(root) / "control" / "supervisor_decisions" / f"{turn_id}.json"


def initialize_control_plane_locked(root: Path) -> dict:
    """Durably mark the v1.2 authority boundary before startup recovery."""
    if not control_path(root).exists():
        value = load_control(root)
        value["initialized_at"] = now_iso()
        save_control(root, value)
    reconcile_control_transactions_locked(root)
    return load_control(root)


def initialize_control_plane(root: Path) -> dict:
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        return initialize_control_plane_locked(root)


def record_legacy_reregistration_origin_locked(root: Path, project_id: str | None,
                                                identity: dict,
                                                dispatch_bytes: bytes) -> dict:
    """Bind an explicit pre-control migration registration to exact bytes.

    Normal v1.2 startup initializes the control plane before registration, so this
    path is available only to an explicit direct migration/re-registration call.
    """
    normalized = normalize_identity(identity, "legacy re-registration identity")
    control = load_control(root)
    turn_id = f"legacy-reregistration-{uuid.uuid4().hex}"
    candidate = {**normalized, "dispatch_sha256": sha256_bytes(dispatch_bytes)}
    receipt = {
        "schema_version": DECISION_RECEIPT_SCHEMA_VERSION,
        "turn_id": turn_id, "PROJECT_ID": project_id,
        "originating_control_revision": control["revision"],
        "intervention_ids": [], "state_sha256_before": None,
        "state_sha256_after": None, "decision_history_index": None,
        "decision": {"decision": "LEGACY_REREGISTRATION",
                     "reason": "explicit v1.2 migration registration"},
        "decision_sha256": sha256_bytes(canonical_json_bytes({
            "decision": "LEGACY_REREGISTRATION",
            "reason": "explicit v1.2 migration registration",
        })),
        "resulting_status": "WAITING_EXECUTOR", "candidate": candidate,
        "committed_at": now_iso(), "migration": True,
    }
    receipt_file, receipt_hash = _write_decision_receipt(root, receipt)
    return _write_candidate_origin(
        root,
        {"revision": control["revision"], "turn_id": turn_id, "PROJECT_ID": project_id},
        candidate, receipt_file, receipt_hash,
    )


def _parse_dispatch_bytes(data: bytes) -> dict:
    try:
        text = data.decode("utf-8-sig")
        blocks = re.findall(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if len(blocks) != 1:
            raise ValueError("expected exactly one JSON fence")
        value = json.loads(blocks[0])
    except Exception as exc:
        raise ControlError(f"Supervisor candidate is not one valid UTF-8 JSON dispatch: {exc}") from exc
    if not isinstance(value, dict):
        raise ControlError("Supervisor candidate payload must be an object")
    return value


def _decision_transaction(root: Path, turn: dict) -> tuple[dict, dict | None]:
    """Validate one newly appended durable decision and optional exact candidate."""
    project_id, _, state_path = resolve_active_project(root)
    state_bytes = state_path.read_bytes()
    state = _read_json(state_path)
    if not isinstance(state, dict) or state.get("project_id") not in {None, project_id}:
        raise ControlError("Supervisor decision project state is invalid")
    history = state.get("decision_history")
    before_len = turn.get("decision_history_length_before")
    if not isinstance(history, list) or not isinstance(before_len, int) or len(history) != before_len + 1:
        raise ControlError("Supervisor decision must append exactly one decision_history entry")
    prefix_hash = sha256_bytes(canonical_json_bytes(history[:before_len]))
    if prefix_hash != turn.get("decision_history_prefix_sha256"):
        raise ControlError("Supervisor decision rewrote historical decision_history")
    decision = history[-1]
    last = state.get("last_supervisor_decision")
    if (not isinstance(decision, dict) or not isinstance(last, dict)
            or not isinstance(decision.get("decision"), str)
            or not decision.get("decision").strip()
            or last.get("decision") != decision.get("decision")):
        raise ControlError("Supervisor decision/history binding is invalid")
    if ("reason" in decision and "reason" in last
            and decision.get("reason") != last.get("reason")):
        raise ControlError("Supervisor decision reason does not match decision_history")
    status = state.get("status")
    if status not in {"WAITING_EXECUTOR", "COMPLETE", "BLOCKED", "STOPPED", "HUMAN_REVIEW"}:
        raise ControlError("Supervisor did not commit a valid lifecycle decision")
    candidate = None
    if status == "WAITING_EXECUTOR":
        current = state.get("current_task")
        identity = normalize_identity(current, "Supervisor current_task")
        inbox = Path(root) / "TO_ZCODE.md"
        data = inbox.read_bytes()
        payload = _parse_dispatch_bytes(data)
        if normalize_identity(payload, "Supervisor dispatch") != identity:
            raise ControlError("Supervisor dispatch/current_task identity mismatch")
        candidate = {
            **identity,
            "dispatch_sha256": sha256_bytes(data),
        }
    elif state.get("current_task") is not None:
        raise ControlError("terminal Supervisor decision must clear current_task")
    receipt = {
        "schema_version": DECISION_RECEIPT_SCHEMA_VERSION,
        "turn_id": turn["turn_id"],
        "PROJECT_ID": project_id,
        "originating_control_revision": turn["revision"],
        "intervention_ids": list(turn.get("intervention_ids") or []),
        "invocation": turn.get("invocation"),
        "state_sha256_before": turn.get("state_sha256_before"),
        "state_sha256_after": sha256_bytes(state_bytes),
        "decision_history_index": before_len,
        "decision": decision,
        "decision_sha256": sha256_bytes(canonical_json_bytes(decision)),
        "resulting_status": status,
        "candidate": candidate,
        "committed_at": now_iso(),
    }
    return receipt, candidate


def _write_decision_receipt(root: Path, receipt: dict) -> tuple[str, str]:
    path = decision_receipt_path(root, receipt["turn_id"])
    stable = dict(receipt)
    if path.exists():
        existing = _read_json(path)
        # committed_at is chosen by the first successful recovery attempt.
        for key in set(stable) - {"committed_at"}:
            if not isinstance(existing, dict) or existing.get(key) != stable.get(key):
                raise ControlError("Supervisor decision receipt conflicts with durable history")
        receipt = existing
    else:
        _atomic_json(path, receipt, create_only=True)
        _failure_point("supervisor_after_decision_receipt")
    return path.relative_to(Path(root)).as_posix(), sha256_bytes(canonical_json_bytes(receipt))


def _load_committed_decision_receipt(root: Path, turn: dict) -> dict | None:
    """Verify a receipt that crossed its durable commit point before a crash.

    A later control transaction is allowed to change project state and revision, so
    recovery cannot reconstruct the transaction from the *current* state first.  The
    create-only receipt is instead validated against the immutable in-flight turn and
    its own hashes.  When state still equals the receipt's post-state, reconstructing
    the transaction provides an additional exact cross-check.
    """
    path = decision_receipt_path(root, str(turn.get("turn_id") or ""))
    if not path.is_file():
        return None
    receipt = _read_json(path, None)
    required = {
        "schema_version", "turn_id", "PROJECT_ID",
        "originating_control_revision", "intervention_ids", "invocation",
        "state_sha256_before", "state_sha256_after", "decision_history_index",
        "decision", "decision_sha256", "resulting_status", "candidate",
        "committed_at",
    }
    if (not isinstance(receipt, dict) or set(receipt) != required
            or receipt.get("schema_version") != DECISION_RECEIPT_SCHEMA_VERSION):
        raise ControlError("committed Supervisor decision receipt schema is invalid")
    bindings = {
        "turn_id": turn.get("turn_id"),
        "PROJECT_ID": turn.get("PROJECT_ID"),
        "originating_control_revision": turn.get("revision"),
        "intervention_ids": list(turn.get("intervention_ids") or []),
        "invocation": turn.get("invocation"),
        "state_sha256_before": turn.get("state_sha256_before"),
        "decision_history_index": turn.get("decision_history_length_before"),
    }
    if any(receipt.get(key) != value for key, value in bindings.items()):
        raise ControlError("committed Supervisor decision receipt turn binding is invalid")
    decision = receipt.get("decision")
    if (not isinstance(decision, dict)
            or not isinstance(decision.get("decision"), str)
            or not decision.get("decision").strip()
            or receipt.get("decision_sha256") != sha256_bytes(canonical_json_bytes(decision))):
        raise ControlError("committed Supervisor decision receipt decision hash is invalid")
    if (not isinstance(receipt.get("state_sha256_after"), str)
            or not HEX64.fullmatch(receipt["state_sha256_after"])
            or not isinstance(receipt.get("committed_at"), str)
            or not receipt.get("committed_at")):
        raise ControlError("committed Supervisor decision receipt fields are invalid")
    status = receipt.get("resulting_status")
    candidate = receipt.get("candidate")
    if status == "WAITING_EXECUTOR":
        if (not isinstance(candidate, dict)
                or set(candidate) != {*IDENTITY_KEYS, "dispatch_sha256"}
                or not isinstance(candidate.get("dispatch_sha256"), str)
                or not HEX64.fullmatch(candidate["dispatch_sha256"])):
            raise ControlError("committed Supervisor candidate receipt is invalid")
        normalize_identity(candidate, "committed Supervisor candidate")
    elif status in {"COMPLETE", "BLOCKED", "STOPPED", "HUMAN_REVIEW"}:
        if candidate is not None:
            raise ControlError("committed terminal Supervisor receipt has a candidate")
    else:
        raise ControlError("committed Supervisor receipt lifecycle status is invalid")

    # Before any later control input mutates state, demand byte-for-byte transaction
    # reconstruction as well.  After a later revision, the turn-bound create-only
    # receipt remains the committed accounting source.
    try:
        _, _, state_path = resolve_active_project(root)
        if sha256_bytes(state_path.read_bytes()) == receipt["state_sha256_after"]:
            reconstructed, _ = _decision_transaction(root, turn)
            for key in required - {"committed_at"}:
                if reconstructed.get(key) != receipt.get(key):
                    raise ControlError(
                        "committed Supervisor decision receipt conflicts with project state"
                    )
    except ControlError:
        raise
    except Exception as exc:
        raise ControlError(
            f"committed Supervisor decision receipt verification failed: {exc}"
        ) from exc
    return receipt


def _write_candidate_origin(root: Path, turn: dict, candidate: dict,
                            receipt_file: str, receipt_hash: str) -> dict:
    value = {
        "schema_version": CANDIDATE_ORIGIN_SCHEMA_VERSION,
        **candidate,
        "PROJECT_ID": turn.get("PROJECT_ID"),
        "originating_control_revision": turn["revision"],
        "supervisor_turn_id": turn["turn_id"],
        "decision_receipt_file": receipt_file,
        "decision_receipt_sha256": receipt_hash,
        "status": "ELIGIBLE",
        "recorded_at": now_iso(),
    }
    _atomic_json(candidate_origin_path(root), value)
    _failure_point("supervisor_after_candidate_origin")
    return value


def verify_candidate_origin(root: Path, identity: dict, dispatch_bytes: bytes,
                            expected_revision: int | None = None) -> dict:
    """Require current, durable Supervisor provenance at the authorization boundary."""
    normalized = normalize_identity(identity, "candidate identity")
    control = load_control(root)
    if (control.get("pause") or {}).get("status") != "RUNNING":
        raise ControlError("Runtime is paused; new dispatch authorization is forbidden")
    value = _read_json(candidate_origin_path(root), None)
    if (not isinstance(value, dict)
            or value.get("schema_version") != CANDIDATE_ORIGIN_SCHEMA_VERSION
            or value.get("status") != "ELIGIBLE"
            or not _same_identity(value, normalized)
            or value.get("dispatch_sha256") != sha256_bytes(dispatch_bytes)):
        raise ControlError("Supervisor candidate has no valid identity/hash origin receipt")
    revision = value.get("originating_control_revision")
    if revision != control.get("revision") or (
        expected_revision is not None and revision != expected_revision
    ):
        raise ControlError("Supervisor candidate originating control revision is stale")
    receipt_raw = value.get("decision_receipt_file")
    receipt_hash = value.get("decision_receipt_sha256")
    if not isinstance(receipt_raw, str) or not isinstance(receipt_hash, str):
        raise ControlError("Supervisor candidate decision receipt binding is missing")
    receipt_path = (Path(root) / receipt_raw).resolve()
    allowed = (Path(root) / "control" / "supervisor_decisions").resolve()
    if not receipt_path.is_relative_to(allowed):
        raise ControlError("Supervisor candidate decision receipt path escapes control history")
    receipt = _read_json(receipt_path, None)
    if (not isinstance(receipt, dict)
            or sha256_bytes(canonical_json_bytes(receipt)) != receipt_hash
            or receipt.get("turn_id") != value.get("supervisor_turn_id")
            or receipt.get("originating_control_revision") != revision
            or receipt.get("candidate", {}).get("dispatch_sha256") != value.get("dispatch_sha256")
            or not _same_identity(receipt.get("candidate") or {}, normalized)):
        raise ControlError("Supervisor candidate decision receipt binding is invalid")
    return value


def _invalidate_candidate_locked(root: Path, reason: str) -> dict:
    _, _, state_path, state, runtime = _load_live(root)
    current = state.get("current_task") or {}
    invalidated = False
    quarantine = None
    if state.get("status") == "WAITING_EXECUTOR" and current:
        identity = normalize_identity(current, "candidate identity")
        completion = _completion_for_identity(root, identity)
        if completion is None:
            _retire(runtime, identity, reason)
            quarantine = _quarantine_current(root, identity, "stale-control-revision")
            state["status"] = "SUPERVISOR_TURN"
            state["current_task"] = None
            state["updated_at"] = now_iso()
            _atomic_json(state_path, state)
            _atomic_json(Path(root) / "control" / "orchestrator_runtime.json", runtime)
            invalidated = True
    elif state.get("status") in {"COMPLETE", "BLOCKED"}:
        state["status"] = "SUPERVISOR_TURN"
        state["current_task"] = None
        state["updated_at"] = now_iso()
        _atomic_json(state_path, state)
        invalidated = True
    origin = _read_json(candidate_origin_path(root), None)
    if isinstance(origin, dict) and origin.get("status") == "ELIGIBLE":
        origin["status"] = "INVALIDATED"
        origin["invalidated_at"] = now_iso()
        origin["invalidation_reason"] = reason
        _atomic_json(candidate_origin_path(root), origin)
    return {"invalidated": invalidated, "quarantine": quarantine}


def begin_supervisor_turn(root: Path, project_id: str | None,
                          invocation: dict | None = None) -> dict:
    """Snapshot pending human inputs and the revision a candidate must bind."""
    root = Path(root).resolve()
    if invocation is not None:
        if (not isinstance(invocation, dict)
                or not isinstance(invocation.get("reason"), str)
                or not invocation.get("reason")
                or invocation.get("event") is not None
                and not isinstance(invocation.get("event"), dict)):
            raise ControlError("Supervisor invocation binding is invalid")
        # Reject non-JSON/transient values before making the turn durable.
        invocation = json.loads(canonical_json_bytes(invocation).decode("utf-8"))
    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        control = load_control(root)
        if (control.get("pause") or {}).get("status") != "RUNNING":
            raise ControlError("Runtime is paused; Supervisor turn creation is deferred")
        if isinstance(control.get("inflight_supervisor_turn"), dict):
            raise ControlError("an unreconciled Supervisor turn is already in flight")
        items = pending_interventions(root, project_id)
        _, _, state_path = resolve_active_project(root)
        state = _read_json(state_path, {}) or {}
        if (root / "control" / "STOP").exists() or state.get("status") == "STOPPED":
            raise ControlError("STOPPED is terminal; Supervisor invocation is forbidden")
        if ((root / "control" / "HUMAN_REVIEW").exists()
                or state.get("status") == "HUMAN_REVIEW"):
            raise ControlError("HUMAN_REVIEW forbids automatic Supervisor invocation")
        history = state.get("decision_history")
        if not isinstance(history, list):
            raise ControlError("project_state.decision_history must be an array")
        turn_id = f"supervisor-turn-{uuid.uuid4().hex}"
        turn = {"turn_id": turn_id, "revision": control["revision"],
                "intervention_generation": control["intervention_generation"],
                 "PROJECT_ID": project_id,
                 "state_sha256_before": sha256_bytes(state_path.read_bytes()),
                 "decision_history_length_before": len(history),
                 "decision_history_prefix_sha256": sha256_bytes(canonical_json_bytes(history)),
                 "intervention_ids": [item.get("intervention_id") for item in items],
                 "invocation": invocation,
                 "started_at": now_iso()}
        control["inflight_supervisor_turn"] = turn
        save_control(root, control)
        for item in items:
            status_path = (intervention_root(root, project_id) /
                           item["intervention_id"] / "status.json")
            _atomic_json(status_path, {"status": "INJECTED", "injected_at": now_iso(),
                                       "turn_id": turn_id})
    enriched = []
    for item in items:
        target = None
        feedback = None
        target_id = item.get("target_message_id")
        if target_id is not None:
            target = find_dispatch(root, int(target_id), project_id)
            if target.get("integrity") != "AUTHORIZED_VALID":
                raise ControlError("target dispatch archive integrity is broken")
            target = {**target, "exact_dispatch": (
                root / target["archive_file"]
            ).read_text(encoding="utf-8")}
            try:
                feedback = find_feedback(root, int(target_id), project_id)
            except ControlError:
                feedback = None
            if feedback is not None and feedback.get("integrity") != "OK":
                raise ControlError("target Executor completion integrity is broken")
        downstream = []
        if target_id is not None:
            downstream = [record for record in list_dispatches(root, project_id)
                          if int(record.get("MESSAGE_ID", -1)) > int(target_id)]
        enriched.append({**item, "target_dispatch": target,
                         "target_executor_feedback": feedback,
                         "later_dispatches": downstream})
    return {**turn, "interventions": enriched}


def _set_turn_interventions(root: Path, turn: dict, *, consumed: bool,
                            receipt_file: str | None = None,
                            receipt_hash: str | None = None,
                            recovery: bool = False) -> None:
    for request_id in turn.get("intervention_ids") or []:
        if not isinstance(request_id, str):
            continue
        status_path = (intervention_root(root, turn.get("PROJECT_ID")) /
                       request_id / "status.json")
        current = _read_json(status_path, {}) or {}
        if consumed:
            if current.get("status") == "CONSUMED":
                continue
            _atomic_json(status_path, {
                "status": "CONSUMED",
                "injected_at": current.get("injected_at") or now_iso(),
                "consumed_at": now_iso(),
                "supervisor_revision": turn.get("revision"),
                "decision_receipt_file": receipt_file,
                "decision_receipt_sha256": receipt_hash,
                "recovered_after_crash": recovery,
            })
            _failure_point("supervisor_after_intervention_consumed")
        elif current.get("status") in {"PENDING", "INJECTED"}:
            _atomic_json(status_path, {
                "status": "PENDING", "updated_at": now_iso(),
                "requeued_after_crash" if recovery else "requeued_without_processing": True,
            })


def _consume_invocation_event_locked(root: Path, turn: dict) -> None:
    """Clear a retry/deferred event only after this turn's decision is durable."""
    invocation = turn.get("invocation")
    if not isinstance(invocation, dict):
        return
    runtime_path = Path(root) / "control" / "orchestrator_runtime.json"
    runtime = _read_json(runtime_path, {}) or {}
    if not isinstance(runtime, dict):
        raise ControlError("Runtime state is invalid while consuming Supervisor event")
    changed = False
    pending = runtime.get("pending_supervisor_event")
    if (isinstance(pending, dict)
            and pending.get("reason") == invocation.get("reason")
            and pending.get("event") == invocation.get("event")):
        runtime["pending_supervisor_event"] = None
        changed = True
    if (isinstance(invocation.get("event"), dict)
            and runtime.get("paused_deferred_event") == invocation.get("event")):
        runtime["paused_deferred_event"] = None
        changed = True
    if changed:
        _atomic_json(runtime_path, runtime)
        _failure_point("supervisor_after_event_consumed")


def _finish_supervisor_turn_locked(root: Path, turn: dict, *, processed: bool,
                                   recovery: bool = False,
                                   candidate_validator=None) -> dict:
    reconcile_control_transactions_locked(root)
    control = load_control(root)
    inflight = control.get("inflight_supervisor_turn")
    if not isinstance(inflight, dict) or inflight.get("turn_id") != turn.get("turn_id"):
        raise ControlError("Supervisor turn is not the current durable in-flight turn")
    # Receipt persistence is the decision commit point.  Detect it before looking at
    # the current revision: a newer intervention may legitimately advance revision
    # after the receipt was committed but before its accounting writes completed.
    existing_receipt = _load_committed_decision_receipt(root, turn)
    stale = (control.get("revision") != turn.get("revision")
             or (control.get("pause") or {}).get("status") != "RUNNING")
    committed = False
    error = None
    receipt_file = receipt_hash = None
    candidate = None
    candidate_validation_failed = False
    if existing_receipt is not None:
        receipt_file = decision_receipt_path(
            root, existing_receipt["turn_id"]
        ).relative_to(Path(root)).as_posix()
        receipt_hash = sha256_bytes(canonical_json_bytes(existing_receipt))
        candidate = existing_receipt.get("candidate")
        if candidate is not None and not stale:
            _write_candidate_origin(root, turn, candidate, receipt_file, receipt_hash)
        committed = True
    elif processed and not stale:
        try:
            receipt, candidate = _decision_transaction(root, turn)
            if candidate is not None and candidate_validator is not None:
                try:
                    _, _, state_path = resolve_active_project(root)
                    state = _read_json(state_path)
                    dispatch_bytes = (Path(root) / "TO_ZCODE.md").read_bytes()
                    if sha256_bytes(dispatch_bytes) != candidate.get("dispatch_sha256"):
                        raise ControlError("Supervisor candidate bytes changed before validation")
                    candidate_validator(state, _parse_dispatch_bytes(dispatch_bytes), dispatch_bytes)
                except Exception:
                    candidate_validation_failed = True
                    raise
            receipt_file, receipt_hash = _write_decision_receipt(root, receipt)
            if candidate is not None:
                _write_candidate_origin(root, turn, candidate, receipt_file, receipt_hash)
            committed = True
        except Exception as exc:
            error = str(exc)
    if committed:
        _set_turn_interventions(root, turn, consumed=True,
                                receipt_file=receipt_file, receipt_hash=receipt_hash,
                                recovery=recovery)
        _consume_invocation_event_locked(root, turn)
        if stale and candidate is not None:
            _invalidate_candidate_locked(
                root, "COMMITTED_DECISION_CANDIDATE_STALE_AFTER_NEWER_CONTROL"
            )
    else:
        _set_turn_interventions(root, turn, consumed=False, recovery=recovery)
        # A stale/invalid candidate or terminal decision has no authority. STOP and
        # HUMAN_REVIEW remain strict and are never reopened here.
        try:
            _, _, state_path = resolve_active_project(root)
            state = _read_json(state_path, {}) or {}
            changed_this_turn = sha256_bytes(state_path.read_bytes()) != turn.get("state_sha256_before")
            if (not candidate_validation_failed and state.get("status") == "WAITING_EXECUTOR") or (
                changed_this_turn and state.get("status") in {"COMPLETE", "BLOCKED"}
            ):
                _invalidate_candidate_locked(
                    root,
                    "STALE_SUPERVISOR_CONTROL_REVISION" if stale
                    else "INVALID_SUPERVISOR_DECISION_TRANSACTION",
                )
        except Exception as exc:
            if error is None:
                error = str(exc)
    control = load_control(root)
    current = control.get("inflight_supervisor_turn")
    if isinstance(current, dict) and current.get("turn_id") == turn.get("turn_id"):
        control["inflight_supervisor_turn"] = None
        control["last_supervisor_turn_result"] = {
            "turn_id": turn.get("turn_id"), "decision_committed": committed,
            "stale": stale, "recovered_committed_receipt": existing_receipt is not None,
            "candidate_validation_failed": candidate_validation_failed,
            "error": error, "finished_at": now_iso(),
        }
        save_control(root, control)
    return {"turn_id": turn.get("turn_id"), "decision_committed": committed,
            "stale": stale, "recovered_committed_receipt": existing_receipt is not None,
            "candidate_validation_failed": candidate_validation_failed,
            "error": error, "requires_retry": not committed}


def finish_supervisor_turn(root: Path, turn: dict, *, processed: bool,
                           candidate_validator=None) -> bool:
    """Consume inputs only with a validated durable decision; return retry required."""
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        return _finish_supervisor_turn_locked(
            root, turn, processed=processed, recovery=False,
            candidate_validator=candidate_validator,
        )["requires_retry"]


def reconcile_inflight_turn(root: Path, candidate_validator=None) -> dict | None:
    """Recover the model-finished/control-accounting crash window.

    If the project state changed while the exact same control revision was in
    flight, the Supervisor durably made a decision and its injected inputs are
    consumed. If not, the inputs return to PENDING for safe reinjection.
    """
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        control = load_control(root)
        turn = control.get("inflight_supervisor_turn")
        if not isinstance(turn, dict):
            return None
        return _finish_supervisor_turn_locked(
            root, turn, processed=True, recovery=True,
            candidate_validator=candidate_validator,
        )


def invalidate_candidate(root: Path, reason: str) -> dict:
    root = Path(root).resolve()
    import executor_fence
    with executor_fence.runtime_lock(root):
        reconcile_control_transactions_locked(root)
        return _invalidate_candidate_locked(root, reason)


def current_status(root: Path) -> dict:
    root = Path(root).resolve()
    project_id, _, state_path = resolve_active_project(root)
    state = _read_json(state_path, {}) or {}
    runtime = _read_json(root / "control" / "orchestrator_runtime.json", {}) or {}
    control = load_control(root)
    active = runtime.get("authorized_dispatch")
    current = state.get("current_task") if state.get("status") == "WAITING_EXECUTOR" else None
    active_current = (active if isinstance(active, dict) and isinstance(current, dict)
                      and _same_identity(active, current) else None)
    lifecycle = _active_lifecycle(root, runtime, active_current, current)
    return {
        "schema_version": 1,
        "PROJECT_ID": project_id,
        "runtime_status": runtime.get("status"),
        "project_status": state.get("status"),
        "pause": control.get("pause"),
        "active_task": active_current,
        "last_authorized_dispatch": active,
        "active_task_claimed": lifecycle["running"],
        "active_task_claim_recorded": lifecycle["claimed"],
        "active_task_completion_status": (
            lifecycle["completion"].get("STATUS") if lifecycle["completion"] else None
        ),
        "active_task_retired": lifecycle["retired"],
        "pending_interventions": len(pending_interventions(root, project_id)),
        "last_consumed_message_id": runtime.get("last_consumed_message_id"),
        "human_review": ((root / "control" / "HUMAN_REVIEW").exists()
                         or state.get("status") == "HUMAN_REVIEW"),
        "stop": (root / "control" / "STOP").exists(),
    }


def combined_timeline(root: Path, project_id: str | None) -> list[dict]:
    events = []
    for item in list_dispatches(root, project_id):
        events.append({"type": "SUPERVISOR_DISPATCH", "at": item.get("archived_at"),
                       "MESSAGE_ID": item.get("MESSAGE_ID"), "record": item})
    for item in list_feedback(root, project_id):
        events.append({"type": "EXECUTOR_COMPLETION", "at": item.get("COMMITTED_AT"),
                       "MESSAGE_ID": item.get("MESSAGE_ID"), "record": item})
    for item in list_interventions(root, project_id):
        events.append({"type": "HUMAN_INTERVENTION", "at": item.get("submitted_at"),
                       "MESSAGE_ID": item.get("target_message_id"), "record": item})
    return sorted(events, key=lambda e: (str(e.get("at") or ""), int(e.get("MESSAGE_ID") or -1)))


def _emit(value, as_json: bool) -> None:
    if as_json:
        # ASCII escaping keeps redirected output machine-safe under legacy GBK
        # and other non-Unicode Windows console encodings.
        print(json.dumps(value, ensure_ascii=True, indent=2))
        return
    if isinstance(value, list):
        for item in value:
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="General Agent Runtime Supervisor control plane")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("tasks", "feedback", "timeline", "interventions", "status"):
        child = sub.add_parser(name)
        child.add_argument("--json", action="store_true")
        if name in {"tasks", "feedback", "timeline", "interventions"}:
            child.add_argument("--last", type=int)
        if name in {"tasks", "feedback"}:
            child.add_argument("--message-id", type=int)
    request = sub.add_parser("intervene")
    source = request.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--instruction-file", type=Path)
    request.add_argument("--mode", choices=INTERVENTION_MODES, default="STEER")
    request.add_argument("--target-message-id", type=int)
    request.add_argument("--interrupt-current-task", action="store_true")
    request.add_argument("--json", action="store_true")
    pause = sub.add_parser("pause")
    pause.add_argument("--interrupt-current-task", action="store_true")
    pause.add_argument("--json", action="store_true")
    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--json", action="store_true")
    ns = parser.parse_args(argv)
    root = ns.root.resolve()
    try:
        project_id, _, _ = resolve_active_project(root)
        if ns.command == "tasks":
            if ns.message_id is not None:
                value = find_dispatch(root, ns.message_id, project_id)
                if value.get("integrity") != "AUTHORIZED_VALID":
                    if ns.json:
                        _emit(value, True)
                        return 2
                    raise ControlError(
                        f"ARCHIVE INTEGRITY FAILURE: {value.get('integrity')}"
                    )
                if ns.json:
                    value = {**value, "exact_dispatch": (root / value["archive_file"]).read_text(encoding="utf-8")}
                    _emit(value, True)
                else:
                    sys.stdout.buffer.write((root / value["archive_file"]).read_bytes())
                return 0
            value = list_dispatches(root, project_id)
        elif ns.command == "feedback":
            if ns.message_id is not None:
                value = find_feedback(root, ns.message_id, project_id)
                if value.get("integrity") != "OK":
                    raise ControlError("COMPLETION INTEGRITY FAILURE")
                value = value if ns.json else value.get("RECEIPT")
                _emit(value, ns.json)
                return 0
            value = list_feedback(root, project_id)
        elif ns.command == "timeline":
            value = combined_timeline(root, project_id)
        elif ns.command == "interventions":
            value = list_interventions(root, project_id)
        elif ns.command == "status":
            _emit(current_status(root), ns.json)
            return 0
        elif ns.command == "intervene":
            data = (ns.text.encode("utf-8") if ns.text is not None
                    else ns.instruction_file.read_bytes())
            _emit(submit_intervention(root, data, ns.mode, ns.target_message_id,
                                      ns.interrupt_current_task), ns.json)
            return 0
        elif ns.command == "pause":
            _emit(set_pause(root, ns.interrupt_current_task), ns.json)
            return 0
        elif ns.command == "resume":
            _emit(resume(root), ns.json)
            return 0
        else:
            raise ControlError("unknown command")
        if getattr(ns, "last", None) is not None:
            if ns.last < 1:
                raise ControlError("Last must be >= 1")
            value = value[-ns.last:]
        _emit(value, ns.json)
        return 0
    except (ControlError, OSError, ValueError) as exc:
        if getattr(ns, "json", False):
            _emit({"ok": False, "error": str(exc), "error_type": type(exc).__name__}, True)
        else:
            print(f"SUPERVISOR_CONTROL_ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
