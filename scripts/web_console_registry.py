"""Console-owned Runtime Registry for the v1.3 Web Console (P2).

A small persistent store of Runtime Roots that a human explicitly added to
this Web Console installation. The Registry is owned by the Console: it lives
under the Console's non-authoritative data directory and never reads or
writes any managed Runtime's authoritative control, handoff, project-state,
or Goal Anchor data.

Design invariants:

- Explicit registration only. Nothing here scans drives or directories to
  discover Runtimes, and Runtime creation/template copy is out of scope.
- Opaque stable IDs. Entries are identified by a server-generated random
  16-hex-character ID. A request's Runtime Root is resolved exclusively by
  looking up that exact ID in the persisted store; a filesystem path is never
  accepted as a selector, and no path is ever derived from an ID fragment.
- Remove means forget. Removing an entry drops only the Registry record; the
  registered Runtime tree is never deleted, moved, or modified.
- Validation fails closed. Adding or revalidating a root requires an existing
  directory containing `scripts/supervisor_control.py` that answers a bounded
  `status --json` probe with a supported `schema_version`. Duplicate
  canonical roots, invalid labels, and malformed payloads are refused with
  bounded structured errors that do not leak file contents.
- Storage is atomic and never silently reset. Updates write a temp file,
  fsync, and `os.replace` the JSON document. A corrupted store makes every
  Registry operation fail closed with REGISTRY_CORRUPT; it is never
  overwritten or reset behind the user's back. In-process updates are
  serialized by a lock, so concurrent conflicting updates are ordered rather
  than interleaved.

The only subprocess this module ever launches is the bounded read-only
control-plane probe. No shell is involved and no arbitrary command can be
expressed through the Registry API.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path

REGISTRY_SCHEMA_VERSION = 1
REGISTRY_FILE_NAME = "runtime_registry.json"
LABEL_MAX_CHARS = 120
ROOT_MAX_CHARS = 2048
ID_HEX_CHARS = 16
ID_PATTERN = re.compile(r"^[0-9a-f]{%d}$" % ID_HEX_CHARS)
SUPPORTED_STATUS_SCHEMA_VERSIONS = {1}
CONTROL_SCRIPT_RELATIVE = Path("scripts") / "supervisor_control.py"
STDOUT_ERROR_HEAD_BYTES = 2000

HTTP_STATUS_BY_CODE = {
    "REGISTRY_CORRUPT": 500,
    "REGISTRY_IO_ERROR": 500,
    "REGISTRY_INVALID_PAYLOAD": 400,
    "REGISTRY_INVALID_LABEL": 400,
    "REGISTRY_INVALID_ROOT": 400,
    "REGISTRY_ROOT_NOT_FOUND": 400,
    "REGISTRY_ROOT_NOT_A_DIRECTORY": 400,
    "REGISTRY_NOT_A_RUNTIME_ROOT": 400,
    "REGISTRY_UNSUPPORTED_SCHEMA": 400,
    "REGISTRY_DUPLICATE_ROOT": 409,
    "RUNTIME_UNKNOWN": 404,
}

_ENTRY_KEYS = {"id", "root", "label", "added_at", "updated_at", "validation"}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RegistryError(Exception):
    """Fail-closed Registry refusal with a stable machine-readable code."""

    def __init__(self, code: str, message: str, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail
        self.http_status = HTTP_STATUS_BY_CODE.get(code, 400)


def _copy_entry(entry: dict) -> dict:
    copied = dict(entry)
    copied["validation"] = dict(entry["validation"])
    return copied


def _validate_label(label) -> str:
    if not isinstance(label, str) or not label or label.strip() != label:
        raise RegistryError(
            "REGISTRY_INVALID_LABEL",
            "label must be a non-empty string without leading or trailing "
            "whitespace")
    if len(label) > LABEL_MAX_CHARS:
        raise RegistryError(
            "REGISTRY_INVALID_LABEL",
            f"label exceeds {LABEL_MAX_CHARS} characters",
            {"max_chars": LABEL_MAX_CHARS, "length": len(label)})
    if any(unicodedata.category(char) == "Cc" for char in label):
        raise RegistryError(
            "REGISTRY_INVALID_LABEL",
            "label must not contain control characters")
    return label


def _root_key(path: Path) -> str:
    """Canonical, comparison-stable key for a Runtime Root on this platform."""
    return os.path.normcase(str(path))


class RuntimeRegistry:
    """Persistent Registry of explicitly added Runtime Roots."""

    def __init__(self, data_dir: Path, control_timeout: float = 10.0):
        self.data_dir = Path(data_dir)
        self.control_timeout = control_timeout
        self._lock = threading.RLock()

    # -- storage -------------------------------------------------------------

    @property
    def file_path(self) -> Path:
        return self.data_dir / REGISTRY_FILE_NAME

    def _load(self) -> dict:
        try:
            raw = self.file_path.read_bytes()
        except FileNotFoundError:
            return {"schema_version": REGISTRY_SCHEMA_VERSION,
                    "updated_at": None, "runtimes": {}}
        except OSError as exc:
            raise RegistryError(
                "REGISTRY_IO_ERROR",
                "the Registry storage could not be read",
                {"file": REGISTRY_FILE_NAME, "reason": str(exc)}) from exc

        def corrupt(reason) -> RegistryError:
            return RegistryError(
                "REGISTRY_CORRUPT",
                "the Registry storage is corrupt; it is never reset "
                "automatically and needs human inspection",
                {"file": REGISTRY_FILE_NAME, "reason": reason})

        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise corrupt(f"not valid UTF-8 JSON: {exc}") from exc
        if (not isinstance(document, dict)
                or document.get("schema_version") != REGISTRY_SCHEMA_VERSION
                or not isinstance(document.get("runtimes"), dict)):
            raise corrupt("document structure is not a v1 Registry")
        for entry_id, entry in document["runtimes"].items():
            if (not isinstance(entry_id, str) or not ID_PATTERN.match(entry_id)
                    or not isinstance(entry, dict)
                    or set(entry) != _ENTRY_KEYS
                    or entry.get("id") != entry_id
                    or not isinstance(entry.get("root"), str)
                    or not isinstance(entry.get("label"), str)
                    or not isinstance(entry.get("added_at"), str)
                    or not isinstance(entry.get("updated_at"), str)
                    or not isinstance(entry.get("validation"), dict)):
                raise corrupt(f"entry {entry_id!r} is not a valid v1 record")
        return document

    def _save(self, document: dict) -> None:
        document["updated_at"] = utc_now_iso()
        path = self.file_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("wb") as handle:
                handle.write(json.dumps(
                    document, ensure_ascii=False, indent=2).encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        except OSError as exc:
            raise RegistryError(
                "REGISTRY_IO_ERROR",
                "the Registry storage could not be written",
                {"file": REGISTRY_FILE_NAME, "reason": str(exc)}) from exc
        finally:
            temp.unlink(missing_ok=True)

    # -- validation ----------------------------------------------------------

    @staticmethod
    def _canonical_root(root) -> tuple[Path, str]:
        if (not isinstance(root, str) or not root or not root.strip()
                or len(root) > ROOT_MAX_CHARS):
            raise RegistryError(
                "REGISTRY_INVALID_ROOT",
                f"root must be a non-empty path string of at most "
                f"{ROOT_MAX_CHARS} characters")
        path = Path(root)
        if not path.is_absolute():
            raise RegistryError(
                "REGISTRY_INVALID_ROOT",
                "root must be an absolute path")
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise RegistryError(
                "REGISTRY_INVALID_ROOT",
                "root could not be resolved on this filesystem",
                {"reason": str(exc)}) from exc
        return resolved, _root_key(resolved)

    def _validate_new_root(self, root) -> tuple[Path, str]:
        resolved, key = self._canonical_root(root)
        if not resolved.exists():
            raise RegistryError(
                "REGISTRY_ROOT_NOT_FOUND",
                "the Runtime Root does not exist",
                {"root": str(resolved)})
        if not resolved.is_dir():
            raise RegistryError(
                "REGISTRY_ROOT_NOT_A_DIRECTORY",
                "the Runtime Root is a file, not a directory",
                {"root": str(resolved)})
        return resolved, key

    def _probe(self, root: Path) -> dict:
        """Bounded read-only status probe deciding runtime compatibility."""
        command = [sys.executable, str(root / CONTROL_SCRIPT_RELATIVE),
                   "--root", str(root), "status", "--json"]
        try:
            completed = subprocess.run(
                command, capture_output=True, cwd=str(root),
                timeout=self.control_timeout)
        except subprocess.TimeoutExpired:
            raise RegistryError(
                "REGISTRY_NOT_A_RUNTIME_ROOT",
                "the candidate Runtime did not answer a bounded read-only "
                "status probe",
                {"root": str(root),
                 "timeout_seconds": self.control_timeout}) from None
        except OSError as exc:
            raise RegistryError(
                "REGISTRY_NOT_A_RUNTIME_ROOT",
                "the candidate Runtime status probe could not be launched",
                {"root": str(root), "reason": str(exc)}) from exc
        stdout = completed.stdout.decode("utf-8", errors="replace")
        # An incompatible schema is deterministically knowable from the
        # status document itself, whatever exit code accompanied it.
        try:
            emitted = json.loads(stdout)
        except ValueError:
            emitted = None
        if isinstance(emitted, dict):
            emitted_version = emitted.get("schema_version")
            if (isinstance(emitted_version, int)
                    and not isinstance(emitted_version, bool)
                    and emitted_version
                    not in SUPPORTED_STATUS_SCHEMA_VERSIONS):
                raise RegistryError(
                    "REGISTRY_UNSUPPORTED_SCHEMA",
                    "the candidate Runtime reports an unsupported "
                    "control-plane schema version",
                    {"root": str(root),
                     "status_schema_version": emitted_version,
                     "supported": sorted(SUPPORTED_STATUS_SCHEMA_VERSIONS)})
        if completed.returncode != 0:
            raise RegistryError(
                "REGISTRY_NOT_A_RUNTIME_ROOT",
                "the candidate Runtime did not answer a read-only status "
                "probe successfully",
                {"root": str(root), "exit_code": completed.returncode,
                 "stdout_head": stdout[:STDOUT_ERROR_HEAD_BYTES],
                 "stderr_head": completed.stderr.decode(
                     "utf-8", errors="replace")[:STDOUT_ERROR_HEAD_BYTES]})
        if not isinstance(emitted, dict):
            raise RegistryError(
                "REGISTRY_NOT_A_RUNTIME_ROOT",
                "the candidate Runtime status probe did not produce one JSON "
                "document")
        version = emitted.get("schema_version")
        if (not isinstance(version, int) or isinstance(version, bool)
                or version not in SUPPORTED_STATUS_SCHEMA_VERSIONS):
            raise RegistryError(
                "REGISTRY_UNSUPPORTED_SCHEMA",
                "the candidate Runtime reports an unsupported control-plane "
                "schema version",
                {"root": str(root),
                 "status_schema_version": version,
                 "supported": sorted(SUPPORTED_STATUS_SCHEMA_VERSIONS)})
        return {"status_schema_version": version}

    def _full_validation(self, root: Path) -> dict:
        """Run every compatibility check; return a passing validation record."""
        if not root.exists():
            raise RegistryError(
                "REGISTRY_ROOT_NOT_FOUND", "the Runtime Root does not exist",
                {"root": str(root)})
        if not root.is_dir():
            raise RegistryError(
                "REGISTRY_ROOT_NOT_A_DIRECTORY",
                "the Runtime Root is a file, not a directory",
                {"root": str(root)})
        if not (root / CONTROL_SCRIPT_RELATIVE).is_file():
            raise RegistryError(
                "REGISTRY_NOT_A_RUNTIME_ROOT",
                "the directory does not contain a Runtime control plane",
                {"root": str(root),
                 "expected": CONTROL_SCRIPT_RELATIVE.as_posix()})
        started = time.monotonic()
        probe = self._probe(root)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {"ok": True, "checked_at": utc_now_iso(),
                "status_schema_version": probe["status_schema_version"],
                "probe_elapsed_ms": elapsed_ms}

    def _failed_validation(self, exc: RegistryError) -> dict:
        return {"ok": False, "checked_at": utc_now_iso(),
                "failure_code": exc.code}

    # -- operations ----------------------------------------------------------

    def list_entries(self) -> list:
        with self._lock:
            document = self._load()
            entries = sorted(document["runtimes"].values(),
                             key=lambda item: (item["added_at"], item["id"]))
            return [_copy_entry(entry) for entry in entries]

    def get(self, runtime_id) -> dict:
        with self._lock:
            document = self._load()
            checked = self._checked_id(runtime_id)
            entry = document["runtimes"].get(checked)
            if entry is None:
                raise RegistryError(
                    "RUNTIME_UNKNOWN",
                    "no registered Runtime has this ID")
            return _copy_entry(entry)

    @staticmethod
    def _checked_id(runtime_id) -> str:
        if (not isinstance(runtime_id, str)
                or not ID_PATTERN.match(runtime_id)):
            raise RegistryError(
                "RUNTIME_UNKNOWN",
                "runtime IDs are opaque server-generated values")
        return runtime_id

    def add(self, root, label) -> dict:
        with self._lock:
            document = self._load()
            _validate_label(label)
            resolved, key = self._validate_new_root(root)
            for entry in document["runtimes"].values():
                if _root_key(Path(entry["root"])) == key:
                    raise RegistryError(
                        "REGISTRY_DUPLICATE_ROOT",
                        "this Runtime Root is already registered",
                        {"existing_id": entry["id"]})
            validation = self._full_validation(resolved)
            runtime_id = secrets.token_hex(ID_HEX_CHARS // 2)
            while runtime_id in document["runtimes"]:
                runtime_id = secrets.token_hex(ID_HEX_CHARS // 2)
            now = utc_now_iso()
            entry = {"id": runtime_id, "root": str(resolved), "label": label,
                     "added_at": now, "updated_at": now,
                     "validation": validation}
            document["runtimes"][runtime_id] = entry
            self._save(document)
            return _copy_entry(entry)

    def rename(self, runtime_id, label) -> dict:
        with self._lock:
            document = self._load()
            entry = document["runtimes"].get(self._checked_id(runtime_id))
            if entry is None:
                raise RegistryError(
                    "RUNTIME_UNKNOWN", "no registered Runtime has this ID")
            _validate_label(label)
            entry["label"] = label
            entry["updated_at"] = utc_now_iso()
            self._save(document)
            return _copy_entry(entry)

    def revalidate(self, runtime_id) -> dict:
        with self._lock:
            document = self._load()
            entry = document["runtimes"].get(self._checked_id(runtime_id))
            if entry is None:
                raise RegistryError(
                    "RUNTIME_UNKNOWN", "no registered Runtime has this ID")
            try:
                entry["validation"] = self._full_validation(
                    Path(entry["root"]))
            except RegistryError as exc:
                entry["validation"] = self._failed_validation(exc)
                self._save(document)
                raise
            entry["updated_at"] = utc_now_iso()
            self._save(document)
            return _copy_entry(entry)

    def remove(self, runtime_id) -> dict:
        """Drop only the Registry record; the Runtime tree is never touched."""
        with self._lock:
            document = self._load()
            checked = self._checked_id(runtime_id)
            entry = document["runtimes"].pop(checked, None)
            if entry is None:
                raise RegistryError(
                    "RUNTIME_UNKNOWN", "no registered Runtime has this ID")
            self._save(document)
            return _copy_entry(entry)
