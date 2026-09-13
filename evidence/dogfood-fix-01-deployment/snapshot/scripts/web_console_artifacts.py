"""P6 Artifact Center pure layer for the v1.3 Web Console.

Deterministic, offline half of the read-only Artifact Center: artifact path
normalization, format classification for exactly the v1.3.0 preview formats,
fail-closed query parsing, the artifact index/catalog projection over
authoritative completion-ledger receipts plus bounded walk results, bounded
preview documents, and the typed artifact-feedback schema that reuses the P5
formal intervention surface (`web_console_control.ControlRequestError`).

Like `web_console_history.py` and `web_console_control.py`, this module never
touches the filesystem, the clock, randomness, or subprocesses — the HTTP
layer in `web_console_server.py` performs the bounded status probe, the
`feedback --json` subprocess, the authorized-root walk, the bounded hash and
preview reads, and delegates the one mutation (artifact-triggered STEER /
AUDIT) through the existing P5 intervention machinery.

Provenance honesty classes are structural: `ledger` bindings come only from
completion-ledger entries whose integrity the v1.2 control plane verified
(`integrity == "OK"`), `unbound` means a file exists under an authorized
publication root with no authoritative receipt. Untrusted ledger entries
never bind provenance and are surfaced in the honesty block. Filenames are
never treated as production proof.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import urllib.parse
import csv as csv_module

from web_console_control import ControlRequestError

ARTIFACTS_SCHEMA_VERSION = 1

# The publication roots the Runtime itself authorizes for Executor output
# (spec §5 / EXECUTOR-FENCE-V1). Artifact discovery never leaves them.
AUTHORIZED_ROOTS = ("workspace", "evidence", "reports")

FORMAT_MARKDOWN = "markdown"
FORMAT_TEXT = "text"
FORMAT_LOG = "log"
FORMAT_CODE = "code"
FORMAT_PNG = "png"
FORMAT_JPEG = "jpeg"
FORMAT_WEBP = "webp"
FORMAT_CSV = "csv"
FORMAT_JSON = "json"
FORMAT_PDF = "pdf"
FORMAT_UNSUPPORTED = "unsupported"
KNOWN_FORMATS = (FORMAT_MARKDOWN, FORMAT_TEXT, FORMAT_LOG, FORMAT_CODE,
                 FORMAT_PNG, FORMAT_JPEG, FORMAT_WEBP, FORMAT_CSV,
                 FORMAT_JSON, FORMAT_PDF, FORMAT_UNSUPPORTED)

CODE_EXTENSIONS = frozenset((
    ".py", ".js", ".ts", ".jsx", ".tsx", ".c", ".h", ".cpp", ".hpp", ".cc",
    ".java", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".ps1", ".psm1",
    ".bat", ".cmd", ".sql", ".r", ".swift", ".kt", ".scala", ".toml",
    ".yaml", ".yml", ".ini", ".cfg",
))
EXTENSION_FORMATS = {
    ".md": FORMAT_MARKDOWN, ".markdown": FORMAT_MARKDOWN,
    ".txt": FORMAT_TEXT, ".log": FORMAT_LOG,
    ".png": FORMAT_PNG, ".jpg": FORMAT_JPEG, ".jpeg": FORMAT_JPEG,
    ".webp": FORMAT_WEBP, ".csv": FORMAT_CSV, ".json": FORMAT_JSON,
    ".pdf": FORMAT_PDF,
}

MEDIA_TYPES = {
    FORMAT_PNG: "image/png",
    FORMAT_JPEG: "image/jpeg",
    FORMAT_WEBP: "image/webp",
    FORMAT_PDF: "application/pdf",
}

# Windows reserved device names (any case, optionally with an extension).
DEVICE_NAMES = frozenset(
    ("CON", "PRN", "AUX", "NUL", "COM0", "COM1", "COM2", "COM3", "COM4",
     "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4",
     "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"))

MAX_PATH_CHARS = 512
MAX_TASK_FILTER_CHARS = 120
MAX_SEARCH_CHARS = 120
MESSAGE_ID_MAX = 999999999
DEFAULT_PAGE = 1
DEFAULT_PAGE_SIZE = 20
MAX_PAGE = 10_000_000
MIN_PAGE_SIZE = 1
MAX_PAGE_SIZE = 100
MAX_LISTED_EXAMPLES = 10
MAX_REPUBLISHED_LISTED = 20

# Catalog feedback (multi-artifact) bounds. One feedback item may reference
# several selected artifacts; references are paths only, never content.
MAX_FEEDBACK_PATHS = 8

# Bounded preview limits (per format and aggregate). Rationale: previews are
# for human reading, never for relaying whole files; the caps keep one
# request well under any transport or memory concern and are enforced before
# content is interpreted.
MAX_PREVIEW_BYTES = 256 * 1024
MAX_PREVIEW_CHARS = 100_000
MAX_PREVIEW_LINES = 5000
MAX_PREVIEW_ROWS = 200
MAX_PREVIEW_COLUMNS = 64
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PDF_BYTES = 32 * 1024 * 1024

MAGIC_PNG = b"\x89PNG\r\n\x1a\n"
MAGIC_JPEG = b"\xff\xd8\xff"
MAGIC_PDF = b"%PDF-"

TEXT_FORMATS = (FORMAT_MARKDOWN, FORMAT_TEXT, FORMAT_LOG, FORMAT_CODE)

QUERY_STATUS_VALUES = ("ledger", "unbound", "missing")


class ArtifactPathError(ValueError):
    """A path that is not a normalized project-relative publication path."""


class ArtifactsQueryError(ValueError):
    """An artifact query parameter failed fail-closed validation."""


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def normalize_artifact_path(value) -> str:
    """Validate one project-relative publication path (fail closed).

    Accepts only `workspace/…`, `evidence/…`, or `reports/…` POSIX-style
    relative paths with no traversal, no absolute/UNC/drive forms, no
    backslashes, no Windows device names, no control characters or invalid
    encodings, and a bounded length. The path is returned unchanged.
    """
    if not isinstance(value, str) or not value or value != value.strip():
        raise ArtifactPathError(
            "artifact path must be a non-empty string without surrounding "
            "whitespace")
    if len(value) > MAX_PATH_CHARS:
        raise ArtifactPathError(
            f"artifact path exceeds {MAX_PATH_CHARS} characters")
    if "\\" in value or value.startswith("/") or "//" in value:
        raise ArtifactPathError(
            "artifact path must be project-relative with forward slashes")
    if re.match(r"^[A-Za-z]:", value):
        raise ArtifactPathError("artifact path must not contain a drive")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ArtifactPathError(
            "artifact path is not valid UTF-8") from exc
    for char in value:
        if unicodedata.category(char) == "Cc":
            raise ArtifactPathError(
                "artifact path must not contain control characters")
    segments = value.split("/")
    for segment in segments:
        if not segment or segment in (".", ".."):
            raise ArtifactPathError(
                "artifact path must not contain empty or dot segments")
        base = segment.split(".")[0].upper()
        if base in DEVICE_NAMES:
            raise ArtifactPathError(
                f"artifact path must not contain the device name {base!r}")
    if segments[0] not in AUTHORIZED_ROOTS:
        raise ArtifactPathError(
            "artifact path must live under one of the authorized publication "
            "roots: " + ", ".join(AUTHORIZED_ROOTS))
    return value


def artifact_id_for_path(path: str) -> str:
    """Stable, path-disclosing-free artifact identifier (SHA-256 of path)."""
    normalized = normalize_artifact_path(path)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def root_of_path(path: str) -> str:
    return normalize_artifact_path(path).split("/", 1)[0]


def classify_format(path: str) -> str:
    """Classify by extension for exactly the v1.3.0 preview formats.

    Active-content formats (HTML/SVG/XML) are deliberately unsupported: they
    are metadata-only and are never rendered or served as documents.
    """
    basename = path.rsplit("/", 1)[-1]
    if "." not in basename:
        return FORMAT_UNSUPPORTED
    extension = "." + basename.rsplit(".", 1)[1].lower()
    if extension in EXTENSION_FORMATS:
        return EXTENSION_FORMATS[extension]
    if extension in CODE_EXTENSIONS:
        return FORMAT_CODE
    return FORMAT_UNSUPPORTED


def magic_matches(format_name: str, header: bytes) -> bool:
    """True when the leading bytes match the claimed format's magic."""
    if format_name == FORMAT_PNG:
        return header.startswith(MAGIC_PNG)
    if format_name == FORMAT_JPEG:
        return header.startswith(MAGIC_JPEG)
    if format_name == FORMAT_WEBP:
        return (len(header) >= 12 and header[:4] == b"RIFF"
                and header[8:12] == b"WEBP")
    if format_name == FORMAT_PDF:
        return header.startswith(MAGIC_PDF)
    return False


def parse_artifacts_query(query: str) -> dict:
    """Validate the artifact catalog query string; normalized parameters."""
    raw = urllib.parse.parse_qs(query if isinstance(query, str) else "",
                                keep_blank_values=True)
    allowed = {"page", "page_size", "root", "type", "message_id", "task",
               "q", "status"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise ArtifactsQueryError(
            f"unknown artifact query parameter(s): {', '.join(unknown)}")
    params = {"page": DEFAULT_PAGE, "page_size": DEFAULT_PAGE_SIZE,
              "root": None, "type": None, "message_id": None, "task": None,
              "q": "", "status": None}
    for key, values in raw.items():
        if len(values) != 1:
            raise ArtifactsQueryError(
                f"artifact query parameter {key!r} must appear exactly once")
        value = values[0]
        if key == "page":
            if not re.fullmatch(r"[0-9]{1,8}", value):
                raise ArtifactsQueryError("page must be a positive integer")
            params["page"] = int(value)
            if not 1 <= params["page"] <= MAX_PAGE:
                raise ArtifactsQueryError(
                    f"page must be between 1 and {MAX_PAGE}")
        elif key == "page_size":
            if not re.fullmatch(r"[0-9]{1,4}", value):
                raise ArtifactsQueryError(
                    "page_size must be a positive integer")
            params["page_size"] = int(value)
            if not MIN_PAGE_SIZE <= params["page_size"] <= MAX_PAGE_SIZE:
                raise ArtifactsQueryError(
                    f"page_size must be between {MIN_PAGE_SIZE} and "
                    f"{MAX_PAGE_SIZE}")
        elif key == "root":
            if value not in AUTHORIZED_ROOTS:
                raise ArtifactsQueryError(
                    "root must be one of: " + ", ".join(AUTHORIZED_ROOTS))
            params["root"] = value
        elif key == "type":
            if value not in KNOWN_FORMATS:
                raise ArtifactsQueryError(
                    "type must be one of: " + ", ".join(KNOWN_FORMATS))
            params["type"] = value
        elif key == "message_id":
            if not re.fullmatch(r"[0-9]{1,9}", value):
                raise ArtifactsQueryError(
                    "message_id must be an integer in 0..999999999")
            params["message_id"] = int(value)
        elif key == "task":
            value = value.strip()
            if len(value) > MAX_TASK_FILTER_CHARS:
                raise ArtifactsQueryError(
                    f"task must be at most {MAX_TASK_FILTER_CHARS} characters")
            params["task"] = value or None
        elif key == "q":
            value = value.strip()
            if len(value) > MAX_SEARCH_CHARS:
                raise ArtifactsQueryError(
                    f"q must be at most {MAX_SEARCH_CHARS} characters")
            params["q"] = value
        elif key == "status":
            if value not in QUERY_STATUS_VALUES:
                raise ArtifactsQueryError(
                    "status must be one of: "
                    + ", ".join(QUERY_STATUS_VALUES))
            params["status"] = value
    return params


def _bounded_examples(items):
    return items[:MAX_LISTED_EXAMPLES]


def _best_binding(occurrences):
    """The provenance of the highest producing MESSAGE_ID (deterministic
    regardless of ledger iteration order) plus its receipt content hash."""
    best_mid = max(mid for mid, _, _ in occurrences)
    for mid, provenance, sha256 in occurrences:
        if mid == best_mid:
            return provenance, sha256
    raise AssertionError("unreachable: best_mid comes from occurrences")


def build_artifact_index(ledger_entries, files) -> dict:
    """Join authoritative receipt provenance with the bounded walk results.

    `ledger_entries` is the parsed `feedback --json` list (v1.2 control
    plane); `files` is the walk result over the authorized publication roots,
    a list of `{"path": <project-relative>, "size_bytes": int | None}`.

    Returns `{"index": {artifact_id: record}, "honesty": {...}}`. Only
    ledger entries the control plane verified (`integrity == "OK"`) bind
    provenance; a path republished by a later round binds to the highest
    producing MESSAGE_ID with the earlier rounds listed in `republished_by`.
    """
    unusable_paths = []
    unusable_total = 0
    untrusted_entries = []
    untrusted_total = 0
    notes = []
    bindings = {}
    if not isinstance(ledger_entries, list):
        ledger_entries = []
        notes.append("the completion-ledger document was not a list; no "
                     "receipt provenance is bound")
    for entry in ledger_entries:
        if not isinstance(entry, dict):
            untrusted_total += 1
            untrusted_entries.append({"message_id": None,
                                      "integrity": None,
                                      "ledger_file": None})
            continue
        integrity = entry.get("integrity")
        message_id = entry.get("MESSAGE_ID")
        if integrity != "OK":
            untrusted_total += 1
            if len(untrusted_entries) < MAX_LISTED_EXAMPLES:
                untrusted_entries.append({
                    "message_id": message_id if _is_int(message_id) else None,
                    "integrity": integrity if isinstance(integrity, str)
                    else None,
                    "ledger_file": entry.get("ledger_file")
                    if isinstance(entry.get("ledger_file"), str) else None,
                })
            continue
        receipt = entry.get("RECEIPT")
        if not isinstance(receipt, dict):
            untrusted_total += 1
            untrusted_entries.append({
                "message_id": message_id if _is_int(message_id) else None,
                "integrity": "RECEIPT_UNUSABLE", "ledger_file": None})
            continue
        published = receipt.get("PUBLISHED_PATHS")
        if not isinstance(published, list):
            notes.append(f"receipt for MESSAGE_ID {message_id!s} carries no "
                         "usable PUBLISHED_PATHS list")
            continue
        seen_in_receipt = set()
        for item in published:
            path = item.get("path") if isinstance(item, dict) else None
            sha256 = item.get("sha256") if isinstance(item, dict) else None
            try:
                normalized = normalize_artifact_path(path)
            except ArtifactPathError:
                unusable_total += 1
                if len(unusable_paths) < MAX_LISTED_EXAMPLES:
                    unusable_paths.append({
                        "message_id": message_id if _is_int(message_id)
                        else None,
                        "path": path if isinstance(path, str) else None})
                continue
            if not isinstance(sha256, str) or not re.fullmatch(
                    r"[0-9a-f]{64}", sha256):
                unusable_total += 1
                if len(unusable_paths) < MAX_LISTED_EXAMPLES:
                    unusable_paths.append({
                        "message_id": message_id if _is_int(message_id)
                        else None,
                        "path": normalized})
                continue
            if normalized in seen_in_receipt:
                notes.append(f"receipt for MESSAGE_ID {message_id!s} lists "
                             f"{normalized!r} more than once")
            seen_in_receipt.add(normalized)
            provenance = {
                "message_id": message_id if _is_int(message_id) else None,
                "task_id": entry.get("TASK_ID")
                if isinstance(entry.get("TASK_ID"), str) else None,
                "stage_id": entry.get("STAGE_ID")
                if isinstance(entry.get("STAGE_ID"), str) else None,
                "attempt": entry.get("ATTEMPT")
                if _is_int(entry.get("ATTEMPT")) else None,
                "producer": receipt.get("EXECUTOR_MODEL_FAMILY")
                if isinstance(receipt.get("EXECUTOR_MODEL_FAMILY"), str)
                else None,
                "committed_at": entry.get("COMMITTED_AT")
                if isinstance(entry.get("COMMITTED_AT"), str) else None,
                "receipt_created_at": receipt.get("CREATED_AT")
                if isinstance(receipt.get("CREATED_AT"), str) else None,
                "completion_status": entry.get("STATUS")
                if isinstance(entry.get("STATUS"), str) else None,
                "completion_integrity": integrity,
                "ledger_file": entry.get("ledger_file")
                if isinstance(entry.get("ledger_file"), str) else None,
            }
            bindings.setdefault(normalized, []).append(
                (provenance["message_id"] if provenance["message_id"]
                 is not None else -1, provenance, sha256))

    index = {}
    for item in files if isinstance(files, list) else []:
        if not isinstance(item, dict):
            unusable_total += 1
            continue
        try:
            normalized = normalize_artifact_path(item.get("path"))
        except ArtifactPathError:
            unusable_total += 1
            continue
        size = item.get("size_bytes")
        size = size if _is_int(size) and size >= 0 else None
        occurrences = bindings.get(normalized)
        if occurrences:
            provenance, sha256 = _best_binding(occurrences)
            best_mid = provenance["message_id"] or -1
            republished = sorted({mid for mid, _, _ in occurrences
                                  if mid != best_mid})
            provenance = {**provenance,
                          "republished_by": republished[:MAX_REPUBLISHED_LISTED]}
            provenance_class = "ledger"
        else:
            provenance = {"message_id": None, "task_id": None,
                          "stage_id": None, "attempt": None,
                          "producer": None, "committed_at": None,
                          "receipt_created_at": None,
                          "completion_status": None,
                          "completion_integrity": None,
                          "ledger_file": None, "republished_by": []}
            provenance_class = "unbound"
            sha256 = None
        index[artifact_id_for_path(normalized)] = {
            "artifact_id": artifact_id_for_path(normalized),
            "path": normalized,
            "name": normalized.rsplit("/", 1)[-1],
            "root": normalized.split("/", 1)[0],
            "format": classify_format(normalized),
            "size_bytes": size,
            "availability": "present",
            "expected_sha256": sha256,
            "provenance": {**provenance, "class": provenance_class},
        }
    # Receipt-bound paths with no file under the authorized roots are still
    # cataloged: their honest availability is "missing".
    for normalized, occurrences in bindings.items():
        aid = artifact_id_for_path(normalized)
        if aid in index:
            continue
        provenance, sha256 = _best_binding(occurrences)
        best_mid = provenance["message_id"] or -1
        republished = sorted({mid for mid, _, _ in occurrences
                              if mid != best_mid})
        index[aid] = {
            "artifact_id": aid,
            "path": normalized,
            "name": normalized.rsplit("/", 1)[-1],
            "root": normalized.split("/", 1)[0],
            "format": classify_format(normalized),
            "size_bytes": None,
            "availability": "missing",
            "expected_sha256": sha256,
            "provenance": {**provenance,
                           "republished_by":
                           republished[:MAX_REPUBLISHED_LISTED],
                           "class": "ledger"},
        }
    honesty = {
        "unusable_publication_paths": {
            "count": unusable_total,
            "examples": _bounded_examples(unusable_paths)},
        "untrusted_ledger_entries": {
            "count": untrusted_total,
            "examples": _bounded_examples(untrusted_entries)},
        "notes": notes,
    }
    return {"index": index, "honesty": honesty}


def project_catalog(index, honesty, params: dict, *,
                    generated_at: str) -> dict:
    """Filter, order, and paginate the artifact index deterministically."""
    records = list(index.values())
    if params["root"] is not None:
        records = [record for record in records
                   if record["root"] == params["root"]]
    if params["type"] is not None:
        records = [record for record in records
                   if record["format"] == params["type"]]
    if params["message_id"] is not None:
        records = [record for record in records
                   if record["provenance"]["message_id"]
                   == params["message_id"]]
    if params["task"]:
        needle = params["task"].casefold()
        records = [record for record in records
                   if (record["provenance"]["task_id"] or "").casefold()
                   .find(needle) >= 0]
    if params["status"] is not None:
        if params["status"] == "missing":
            records = [record for record in records
                       if record["availability"] == "missing"]
        else:
            records = [record for record in records
                       if record["availability"] == "present"
                       and record["provenance"]["class"] == params["status"]]
    if params["q"]:
        needle = params["q"].casefold()
        records = [record for record in records
                   if needle in record["path"].casefold()
                   or needle in record["name"].casefold()]
    records.sort(key=lambda record: record["path"])
    total = len(records)
    page_size = params["page_size"]
    pages = max(1, -(-total // page_size))
    page = params["page"]
    start = (page - 1) * page_size
    return {
        "schema_version": ARTIFACTS_SCHEMA_VERSION,
        "generated_at": generated_at,
        "query": {key: params[key] for key in
                  ("page", "page_size", "root", "type", "message_id", "task",
                   "q", "status")},
        "totals": {"artifacts": total, "pages": pages,
                   "index_size": len(index)},
        "artifacts": records[start:start + page_size],
        "honesty": honesty if isinstance(honesty, dict) else {},
    }


def project_artifact_detail(record: dict, *, size_bytes, actual_sha256) -> dict:
    """Compose one artifact detail with an honest verification verdict.

    `size_bytes` is the current stat result (None when the file is gone);
    `actual_sha256` is the bounded recompute (None when unavailable).
    Verification is `verified` only when the recomputed hash matches the
    authoritative receipt hash; anything else is stated, never guessed.
    """
    record = dict(record)
    provenance = dict(record.get("provenance") or {})
    expected = record.get("expected_sha256")
    if record.get("availability") == "missing" and size_bytes is None:
        availability = "missing"
    elif size_bytes is None:
        availability = "missing"
    else:
        availability = "present"
    if expected is None:
        verification = "unverified"
    elif actual_sha256 is None:
        verification = "unavailable"
    elif actual_sha256 == expected:
        verification = "verified"
    else:
        verification = "hash_mismatch"
    record["size_bytes"] = size_bytes if _is_int(size_bytes) else None
    record["availability"] = availability
    record["verification"] = verification
    record["actual_sha256"] = actual_sha256 \
        if isinstance(actual_sha256, str) else None
    record["provenance"] = provenance
    return record


def _decode_bounded_utf8(raw: bytes):
    """Decode a bounded byte window strictly; None when it is not UTF-8.

    A multibyte character split by the byte cap is trimmed (up to 3 bytes)
    before the verdict; anything still undecodable is genuinely not UTF-8.
    """
    chunk = raw[:MAX_PREVIEW_BYTES]
    for trim in range(4):
        candidate = chunk[:len(chunk) - trim] if trim else chunk
        try:
            return candidate.decode("utf-8"), len(candidate)
        except UnicodeDecodeError:
            continue
    return None, len(chunk)


def project_preview(format_name: str, raw: bytes, *,
                    total_bytes) -> dict:
    """Compose the bounded read-only preview document for one artifact.

    The function never guesses: format claims are verified against magic
    bytes, text that is not UTF-8 says so, malformed JSON is surfaced as
    malformed, unsupported formats stay unsupported, and every truncation is
    explicit. Content is returned as inert data; rendering is text-only.
    """
    total = total_bytes if _is_int(total_bytes) and total_bytes >= 0 else None
    if format_name == FORMAT_UNSUPPORTED:
        return {"kind": "unavailable", "state": "UNSUPPORTED",
                "content": None, "media_type": None,
                "note": ("Unsupported format; metadata and provenance only. "
                         "No active content is ever rendered or served.")}
    if format_name in (FORMAT_PNG, FORMAT_JPEG, FORMAT_WEBP):
        if total is not None and total > MAX_IMAGE_BYTES:
            return {"kind": "image", "state": "TOO_LARGE", "content": None,
                    "media_type": MEDIA_TYPES[format_name],
                    "byte_size": total}
        if not magic_matches(format_name, raw):
            return {"kind": "image", "state": "FORMAT_MISMATCH",
                    "content": None, "media_type": MEDIA_TYPES[format_name],
                    "byte_size": len(raw)}
        return {"kind": "image", "state": "OK", "content": None,
                "media_type": MEDIA_TYPES[format_name], "byte_size": len(raw)}
    if format_name == FORMAT_PDF:
        if total is not None and total > MAX_PDF_BYTES:
            return {"kind": "pdf", "state": "TOO_LARGE", "content": None,
                    "media_type": "application/pdf", "byte_size": total}
        if not magic_matches(format_name, raw):
            return {"kind": "pdf", "state": "FORMAT_MISMATCH",
                    "content": None, "media_type": "application/pdf",
                    "byte_size": len(raw)}
        return {"kind": "pdf", "state": "OK", "content": None,
                "media_type": "application/pdf", "disposition": "attachment",
                "byte_size": len(raw),
                "note": ("PDF bytes are offered as a download only; in-browser "
                         "PDF rendering is not enabled because embedded PDF "
                         "scripts would violate the no-active-content rule.")}
    if format_name in TEXT_FORMATS:
        text, consumed = _decode_bounded_utf8(raw)
        if text is None:
            return {"kind": "text", "state": "NOT_UTF8", "content": None,
                    "total_bytes": total, "returned_bytes": None,
                    "total_lines": None, "returned_lines": None,
                    "truncated": False}
        truncated = len(raw) > MAX_PREVIEW_BYTES
        lines = text.split("\n")
        total_lines = len(lines)
        if total_lines > MAX_PREVIEW_LINES:
            lines = lines[:MAX_PREVIEW_LINES]
            truncated = True
        content = "\n".join(lines)
        if len(content) > MAX_PREVIEW_CHARS:
            content = content[:MAX_PREVIEW_CHARS]
            truncated = True
        return {"kind": "text",
                "state": "TRUNCATED" if truncated else "OK",
                "content": content,
                "total_bytes": total,
                "returned_bytes": consumed,
                "total_lines": total_lines,
                "returned_lines": len(lines),
                "truncated": truncated}
    if format_name == FORMAT_CSV:
        text, consumed = _decode_bounded_utf8(raw)
        if text is None:
            return {"kind": "table", "state": "NOT_UTF8", "rows": None,
                    "rows_returned": None, "total_bytes": total,
                    "returned_bytes": None, "truncated": False}
        truncated = len(raw) > MAX_PREVIEW_BYTES
        rows = []
        column_truncated = False
        for row in csv_module.reader(io.StringIO(text)):
            if len(rows) >= MAX_PREVIEW_ROWS:
                truncated = True
                break
            if len(row) > MAX_PREVIEW_COLUMNS:
                row = row[:MAX_PREVIEW_COLUMNS]
                column_truncated = True
            rows.append(row)
        if column_truncated:
            truncated = True
        return {"kind": "table",
                "state": "TRUNCATED" if truncated else "OK",
                "rows": rows, "rows_returned": len(rows),
                "total_bytes": total, "returned_bytes": consumed,
                "truncated": truncated}
    if format_name == FORMAT_JSON:
        text, consumed = _decode_bounded_utf8(raw)
        if text is None:
            return {"kind": "json", "state": "NOT_UTF8", "content": None,
                    "error": None, "total_bytes": total,
                    "returned_bytes": None, "truncated": False}
        try:
            value = json.loads(text)
        except ValueError as exc:
            return {"kind": "json", "state": "MALFORMED_JSON",
                    "content": None, "error": str(exc),
                    "total_bytes": total, "returned_bytes": consumed,
                    "truncated": False}
        content = json.dumps(value, ensure_ascii=False, indent=2,
                             sort_keys=True)
        truncated = len(content) > MAX_PREVIEW_CHARS
        if truncated:
            content = content[:MAX_PREVIEW_CHARS]
        return {"kind": "json",
                "state": "TRUNCATED" if truncated else "OK",
                "content": content, "error": None, "total_bytes": total,
                "returned_bytes": consumed, "truncated": truncated}
    return {"kind": "unavailable", "state": "UNSUPPORTED", "content": None,
            "media_type": None, "note": "Unsupported format."}


def validate_artifact_feedback_request(payload) -> dict:
    """Typed schema for artifact-triggered feedback (P5 intervention reuse).

    Exactly {mode, comment, message_id, artifact_paths, interrupt_current};
    artifact paths must normalize. Binding to the producing MESSAGE_ID and
    presence are checked separately against the fresh index by
    `check_feedback_binding`.
    """
    if not isinstance(payload, dict):
        raise ControlRequestError("the request body must be a JSON object")
    expected = {"mode", "comment", "message_id", "artifact_paths",
                "interrupt_current"}
    actual = set(payload)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ControlRequestError(
            "request schema mismatch "
            f"(missing={missing}, unknown={unknown})")
    mode = payload["mode"]
    if mode not in ("STEER", "AUDIT") or not isinstance(mode, str):
        raise ControlRequestError(
            "mode must be one of STEER, AUDIT", field="mode")
    comment = payload["comment"]
    if (not isinstance(comment, str) or not comment
            or comment != comment.strip()):
        raise ControlRequestError(
            "comment must be a non-empty string without surrounding "
            "whitespace", field="comment")
    if len(comment) > 4000:
        raise ControlRequestError(
            "comment exceeds 4000 characters", field="comment")
    for char in comment:
        if unicodedata.category(char) == "Cc" and char not in ("\t", "\n",
                                                               "\r"):
            raise ControlRequestError(
                "comment must not contain control characters",
                field="comment")
    message_id = payload["message_id"]
    if not _is_int(message_id) or not 0 <= message_id <= MESSAGE_ID_MAX:
        raise ControlRequestError(
            f"message_id must be an integer in 0..{MESSAGE_ID_MAX}",
            field="message_id")
    paths = payload["artifact_paths"]
    if not isinstance(paths, list) or not 1 <= len(paths) \
            <= MAX_FEEDBACK_PATHS:
        raise ControlRequestError(
            f"artifact_paths must be a list of 1..{MAX_FEEDBACK_PATHS} "
            "paths", field="artifact_paths")
    normalized_paths = []
    for path in paths:
        if not isinstance(path, str):
            raise ControlRequestError(
                "every artifact path must be a string",
                field="artifact_paths")
        normalized_paths.append(normalize_artifact_path(path))
    if len(set(normalized_paths)) != len(normalized_paths):
        raise ControlRequestError(
            "artifact_paths must not repeat a path",
            field="artifact_paths")
    interrupt = payload["interrupt_current"]
    if not isinstance(interrupt, bool):
        raise ControlRequestError(
            "interrupt_current must be a boolean",
            field="interrupt_current")
    return {"mode": mode, "comment": comment, "message_id": message_id,
            "artifact_paths": normalized_paths,
            "interrupt_current": interrupt}


def check_feedback_binding(request: dict, index: dict):
    """None when every referenced artifact is present and bound to the
    request's MESSAGE_ID by a verified receipt; a structured reason code
    otherwise."""
    for path in request["artifact_paths"]:
        record = index.get(artifact_id_for_path(path))
        if record is None or record.get("availability") != "present":
            return "ARTIFACT_UNAVAILABLE"
        provenance = record.get("provenance") or {}
        if provenance.get("class") != "ledger" \
                or provenance.get("message_id") != request["message_id"]:
            return "ARTIFACT_NOT_BOUND_TO_MESSAGE"
    return None


def compose_artifact_feedback_text(mode: str, message_id: int,
                                   paths, comment: str) -> str:
    """Compose the intervention instruction text: references, not dumps.

    Only the normalized artifact paths and the bounded user comment travel
    to the Supervisor; artifact contents and project history are never
    auto-injected (spec §20).
    """
    lines = [f"[Artifact feedback · {mode}] Round MESSAGE {message_id}.",
             "Artifacts referenced (paths only):"]
    lines.extend(f"- {path}" for path in paths)
    lines.extend(("", "User comment:", comment))
    return "\n".join(lines)
