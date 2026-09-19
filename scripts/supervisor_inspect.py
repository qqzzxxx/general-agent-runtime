"""Bounded, read-only text inspection for a Supervisor decision question.

No authority, state update, recursive census, full-file hashing, or telemetry write.
The Runtime observes completed helper outputs through the existing CLI stream.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

SCHEMA = "SUPERVISOR-DEEP-READ-V1"
REASONS = ("ambiguity", "conflict", "regression", "scope_drift",
           "insufficient_evidence", "fv_disagreement", "interface_constraint",
           "history", "audit", "contract")
MAX_BYTES = 65536


def inspect(root, path, reason, offset=0, limit=8192):
    if reason not in REASONS:
        raise ValueError("a supported decision reason is required")
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_BYTES:
        raise ValueError("invalid byte range")
    root = Path(root).resolve(strict=True)
    target = (root / path).resolve(strict=True)
    target.relative_to(root)  # includes symlink/junction escapes
    if not target.is_file():
        raise ValueError("target must be a regular file")
    with target.open("rb") as stream:
        before = os.fstat(stream.fileno())
        stream.seek(offset)
        data = stream.read(limit)
        after = os.fstat(stream.fileno())
    return {"schema": SCHEMA, "status": "READ", "reason": reason,
            "path": target.relative_to(root).as_posix(), "offset": offset,
            "bytes_read": len(data), "file_bytes": after.st_size,
            "next_offset": offset + len(data),
            "has_more": offset + len(data) < after.st_size,
            "changed_during_read": (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns),
            "slice_sha256": hashlib.sha256(data).hexdigest(),
            "encoding": "utf-8 with replacement at invalid/split characters",
            "content": data.decode("utf-8", errors="replace")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", required=True)
    parser.add_argument("--reason", choices=REASONS, required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=8192)
    args = parser.parse_args()
    try:
        result = inspect(Path.cwd(), args.path, args.reason, args.offset, args.limit)
    except (OSError, ValueError) as exc:
        result = {"schema": SCHEMA, "status": "ERROR", "reason": args.reason,
                  "bytes_read": 0, "error": type(exc).__name__}
    print(json.dumps(result, ensure_ascii=True))
    return 0 if result["status"] == "READ" else 1


if __name__ == "__main__":
    raise SystemExit(main())
