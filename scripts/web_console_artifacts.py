"""P6 Artifact Center pure layer for the v1.3 Web Console.

Deterministic, offline half of the read-only Artifact Center: artifact path
normalization, format classification for exactly the v1.3.0 preview formats,
fail-closed query parsing, the artifact index/catalog projection over
authoritative completion publication manifests plus bounded walk results, bounded
preview documents, and the typed artifact-feedback schema that reuses the P5
formal intervention surface (`web_console_control.ControlRequestError`).

Like `web_console_history.py` and `web_console_control.py`, this module never
touches the filesystem, the clock, randomness, or subprocesses — the HTTP
layer in `web_console_server.py` performs the bounded status probe, the
`feedback --json` subprocess, the authorized-root walk, the bounded hash and
preview reads, and delegates the one mutation (artifact-triggered STEER /
AUDIT) through the existing P5 intervention machinery.

Provenance honesty classes are structural: `ledger` bindings come only from
Runtime publication projections whose completion and publication integrity the control plane verified
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
from datetime import datetime

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

# Runtime publication records (executor_fence.publish →
# handoff/executor_publications/<commit_id>/<sha256(path)>.json). The exact
# key set is the Runtime's own RECORD_KEYS contract; the Console never
# accepts a looser shape.
PUBLICATION_RECORD_KEYS = frozenset((
    "MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE", "PROJECT_ID",
    "path", "sha256", "PUBLISHED_AT"))
MAX_PUBLICATION_RECORDS = 128


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


def artifact_id_for_publication(path: str, commit_id: str) -> str:
    """Stable identity of a path's publication in one completion, not its bytes."""
    return hashlib.sha256((commit_id + "\0" + normalize_artifact_path(path)).encode()).hexdigest()


def verified_publications(entry):
    """Consume only the control plane's verified publication projection.

    Fail the entire round closed on malformed or ambiguous metadata; never
    reinterpret Executor-authored receipt fields as Runtime publication proof.
    """
    projection = entry.get("artifact_provenance")
    if entry.get("integrity") != "OK":
        return [], "completion integrity is not OK"
    if not isinstance(projection, dict) or projection.get("integrity") != "OK":
        return [], "authoritative publication evidence is unavailable or untrusted"
    raw = projection.get("publications")
    if not isinstance(raw, list) or len(raw) > 128:
        return [], "invalid publication list"
    if (not _is_int(entry.get("MESSAGE_ID")) or not _is_int(entry.get("ATTEMPT"))
            or not all(isinstance(entry.get(k), str) and entry[k]
                       for k in ("TASK_ID", "STAGE_ID", "NONCE", "COMMIT_ID"))):
        return [], "incomplete publication identity"
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            return [], "invalid publication record"
        try:
            path = normalize_artifact_path(item.get("path"))
        except ArtifactPathError:
            return [], "invalid publication path"
        if (path.casefold() in seen
                or not isinstance(item.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
                or any(type(item.get(k)) is not type(entry.get(k)) or item.get(k) != entry.get(k)
                       for k in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE", "PROJECT_ID"))):
            return [], "ambiguous publication path, hash or identity"
        seen.add(path.casefold())
    return raw, None


def verified_runtime_publication_records(entries, identity) -> tuple:
    """Validate raw Runtime publication records against one dispatch identity.

    The Runtime fence writes one record per published artifact under
    `handoff/executor_publications/<commit_id>/` at publish time — the same
    single authoritative source a verified completion later seals into its
    publication manifest, read here before that seal exists. `entries` is a
    list of {"name": filename, "record": parsed JSON} pairs as read by the
    HTTP layer; `identity` is the archived dispatch's identity plus
    PROJECT_ID. Every check mirrors the Runtime's own fence validation
    (exact schema, identity/project binding, authorized path, hash shape,
    timezone-aware timestamp, filename binding); any failure refuses the
    whole set instead of binding a subset. Returns (publications, reason)
    where each publication is {path, sha256, published_at, task_id,
    stage_id, attempt}.
    """
    if (not isinstance(identity, dict)
            or not _is_int(identity.get("MESSAGE_ID"))
            or not _is_int(identity.get("ATTEMPT"))
            or not isinstance(identity.get("PROJECT_ID"), str)
            or not all(isinstance(identity.get(key), str) and identity[key]
                       for key in ("TASK_ID", "STAGE_ID", "NONCE"))):
        return [], "incomplete dispatch identity"
    if not isinstance(entries, list) or len(entries) > MAX_PUBLICATION_RECORDS:
        return [], "invalid publication record list"
    seen = set()
    publications = []
    for entry in entries:
        if not isinstance(entry, dict):
            return [], "invalid publication record"
        record = entry.get("record")
        if (not isinstance(record, dict)
                or set(record) != PUBLICATION_RECORD_KEYS):
            return [], "publication record schema mismatch"
        if (type(record.get("MESSAGE_ID")) is not int
                or record["MESSAGE_ID"] != identity["MESSAGE_ID"]
                or type(record.get("ATTEMPT")) is not int
                or record["ATTEMPT"] != identity["ATTEMPT"]
                or any(not isinstance(record.get(key), str)
                       or record[key] != identity[key]
                       for key in ("TASK_ID", "STAGE_ID", "NONCE"))
                or record["PROJECT_ID"] != identity["PROJECT_ID"]):
            return [], "publication record identity mismatch"
        try:
            path = normalize_artifact_path(record.get("path"))
        except ArtifactPathError:
            return [], "invalid publication record path"
        if (not isinstance(record.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", record["sha256"])):
            return [], "invalid publication record hash"
        if not _is_tz_aware_timestamp(record.get("PUBLISHED_AT")):
            return [], "invalid publication record timestamp"
        expected_name = hashlib.sha256(
            path.encode("utf-8")).hexdigest() + ".json"
        if entry.get("name") != expected_name:
            return [], "publication record filename binding mismatch"
        if path.casefold() in seen:
            return [], "ambiguous duplicate publication path"
        seen.add(path.casefold())
        publications.append({
            "path": path, "sha256": record["sha256"],
            "published_at": record["PUBLISHED_AT"],
            "task_id": record["TASK_ID"], "stage_id": record["STAGE_ID"],
            "attempt": record["ATTEMPT"],
        })
    return publications, None


def _is_tz_aware_timestamp(value) -> bool:
    """Mirror the Runtime's timestamp check: non-empty ISO-8601 with a timezone."""
    if not isinstance(value, str) or not value.strip():
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def build_artifact_index(ledger_entries, files) -> dict:
    """One catalog record per verified (completion, path), plus unbound files."""
    notes, untrusted, unusable = [], [], []
    entries = ledger_entries if isinstance(ledger_entries, list) else []
    counts = {}
    for entry in entries:
        if isinstance(entry, dict) and _is_int(entry.get("MESSAGE_ID")):
            mid = entry["MESSAGE_ID"]
            counts[mid] = counts.get(mid, 0) + 1
    present = {}
    for item in files if isinstance(files, list) else []:
        try:
            path = normalize_artifact_path(item.get("path"))
            size = item.get("size_bytes")
            present[path] = size if _is_int(size) and size >= 0 else None
        except (ArtifactPathError, AttributeError):
            unusable.append({"path": None})
    index, bound = {}, set()
    for entry in entries:
        if not isinstance(entry, dict):
            untrusted.append({"message_id": None})
            continue
        mid = entry.get("MESSAGE_ID")
        publications, reason = verified_publications(entry)
        if counts.get(mid, 0) > 1:
            publications, reason = [], "ambiguous completion MESSAGE_ID"
        if reason:
            notes.append(f"MESSAGE_ID {mid}: {reason}")
            if entry.get("integrity") != "OK":
                untrusted.append({"message_id": mid, "integrity": entry.get("integrity")})
            else:
                unusable.append({"message_id": mid})
            continue
        receipt = entry.get("RECEIPT") or {}
        for publication in publications:
            path = publication["path"]
            aid = artifact_id_for_publication(path, entry["COMMIT_ID"])
            bound.add(path)
            index[aid] = {
                "artifact_id": aid, "path": path, "name": path.rsplit("/", 1)[-1],
                "root": path.split("/", 1)[0], "format": classify_format(path),
                "size_bytes": present.get(path),
                "availability": "present" if path in present else "missing",
                "expected_sha256": publication["sha256"],
                "provenance": {
                    "class": "ledger", "message_id": mid, "task_id": entry["TASK_ID"],
                    "stage_id": entry["STAGE_ID"], "attempt": entry["ATTEMPT"],
                    "commit_id": entry["COMMIT_ID"], "project_id": entry.get("PROJECT_ID"),
                    "producer": receipt.get("EXECUTOR_MODEL_FAMILY"),
                    "committed_at": entry.get("COMMITTED_AT"),
                    "published_at": publication.get("PUBLISHED_AT"),
                    "receipt_created_at": receipt.get("CREATED_AT"),
                    "completion_status": entry.get("STATUS"),
                    "completion_integrity": entry["integrity"],
                    "ledger_file": entry.get("ledger_file"),
                    "source": entry["artifact_provenance"].get("source"),
                    "historical_content_retained": False,
                },
            }
    for path, size in present.items():
        if path in bound:
            continue
        aid = artifact_id_for_path(path)
        index[aid] = {
            "artifact_id": aid, "path": path, "name": path.rsplit("/", 1)[-1],
            "root": path.split("/", 1)[0], "format": classify_format(path),
            "size_bytes": size, "availability": "present", "expected_sha256": None,
            "provenance": {"class": "unbound", "message_id": None, "task_id": None,
                           "stage_id": None, "attempt": None},
        }
    return {"index": index, "honesty": {
        "unusable_publication_paths": {"count": len(unusable), "examples": _bounded_examples(unusable)},
        "untrusted_ledger_entries": {"count": len(untrusted), "examples": _bounded_examples(untrusted)},
        "notes": sorted(notes),
    }}


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
    records.sort(key=lambda record: (record["path"], record["artifact_id"]))
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
        records = [r for r in index.values() if r["path"] == path
                   and r.get("availability") == "present"]
        if not records:
            return "ARTIFACT_UNAVAILABLE"
        matches = [r for r in records if r["provenance"].get("class") == "ledger"
                   and r["provenance"].get("message_id") == request["message_id"]]
        if len(matches) != 1:
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
