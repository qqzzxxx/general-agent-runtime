"""Runtime-owned Codex exec JSONL capture; never parse model-authored content.

One fresh exec process belongs to one begin_supervisor_turn UUID. Captures are
create-only, independent of decision success, and may be read during recovery.
Only allowlisted counters and identities leave the stdout pipe. No raw stream,
prompt, tool output, credentials, account totals, or estimated counts are stored.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import uuid

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens",
          "reasoning_output_tokens")
SOURCE = "codex_exec_json_turn_completed"
SCHEMA = "CODEX-EXEC-USAGE-V1"
ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
MAX_LINE = 1024 * 1024


def unavailable(note="No authoritative per-invocation usage was captured"):
    return {"reported": False, "input_tokens": None, "output_tokens": None,
            "total_tokens": None, "source": None, "note": note}


def canonical(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def counts(value):
    if not isinstance(value, dict) or not value:
        raise ValueError("missing usage")
    # Unknown fields have no established semantics. Never persist arbitrary
    # provider extensions, which might contain secrets, or infer their meaning.
    selected = {key: value[key] for key in FIELDS if key in value}
    if not selected or any(type(n) is not int or n < 0 for n in selected.values()):
        raise ValueError("invalid usage")
    return selected


def paths(root, turn_id):
    if not isinstance(turn_id, str) or not ID.fullmatch(turn_id):
        raise ValueError("invalid turn identity")
    directory = Path(root) / "control" / "supervisor_usage"
    return (directory / f"{turn_id}.invocation.json",
            directory / f"{turn_id}.json")


def write_once(path, value):
    # Atomic publication of a fully fsynced file; hard-link is create-only.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(canonical(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def read_json(path):
    if path.is_symlink() or path.stat().st_size > 16384:
        raise ValueError("invalid capture file")
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique)


def read_usage(root, turn_id, project_id):
    try:
        start_path, result_path = paths(root, turn_id)
        start, result = read_json(start_path), read_json(result_path)
        if (set(start) != {"schema", "turn_id", "PROJECT_ID", "execution_id"}
                or start["schema"] != SCHEMA or start["turn_id"] != turn_id
                or start["PROJECT_ID"] != project_id
                or str(uuid.UUID(start["execution_id"])) != start["execution_id"]
                or set(result) != {"schema", "invocation_sha256", "thread_id", "usage"}
                or result["schema"] != SCHEMA
                or result["invocation_sha256"] != digest(start)
                or str(uuid.UUID(result["thread_id"])) != result["thread_id"]
                or counts(result["usage"]) != result["usage"]):
            raise ValueError("unbound or malformed capture")
        return {"reported": True, "input_tokens": None, "output_tokens": None,
                "total_tokens": None, **result["usage"], "source": SOURCE,
                "note": None, "status": "provider_reported",
                "execution_id": start["execution_id"], "thread_id": result["thread_id"],
                "evidence_sha256": digest(result)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return unavailable()


class Capture:
    def __init__(self, root, turn):
        self.thread_id = None
        self.started = False
        self.done = False
        self.disabled = False
        self.start_path, self.result_path = paths(root, turn["turn_id"])
        self.start = {"schema": SCHEMA, "turn_id": turn["turn_id"],
                      "PROJECT_ID": turn.get("PROJECT_ID"),
                      "execution_id": str(uuid.uuid4())}
        try:
            write_once(self.start_path, self.start)
        except OSError:
            self.disabled = True

    def accept(self, line):
        if self.disabled or self.done:
            return
        try:
            event = json.loads(line, object_pairs_hook=unique)
            if not isinstance(event, dict):
                return
            kind = event.get("type")
            if kind == "thread.started":
                if self.thread_id is not None:
                    raise ValueError("multiple threads in fresh exec")
                self.thread_id = str(uuid.UUID(event["thread_id"]))
            elif kind == "turn.started":
                if self.started or self.thread_id is None:
                    raise ValueError("ambiguous turn")
                self.started = True
            elif kind == "turn.completed":
                if not self.started:
                    raise ValueError("completion without start")
                usage = counts(event.get("usage"))
                write_once(self.result_path, {
                    "schema": SCHEMA, "invocation_sha256": digest(self.start),
                    "thread_id": self.thread_id, "usage": usage})
                self.done = True
            elif kind == "turn.failed":
                self.disabled = True
            # item.* and error text are never interpreted as usage.
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.disabled = True

    def drain(self, stream):
        with stream:
            while True:
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    return
                if len(line) > MAX_LINE:
                    # Drain an oversized item without retaining its contents.
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(MAX_LINE + 1)
                    continue
                if line.endswith(b"\n"):
                    self.accept(line)


@contextlib.contextmanager
def stdout_capture(root, turn):
    """Keep subprocess.run timeout/exit semantics; durably capture while running.

    Storage failure disables telemetry only. The reader always drains the pipe
    so observation failure cannot deadlock the provider process.
    """
    try:
        capture = Capture(root, turn)
        read_fd, write_fd = os.pipe()
    except (OSError, ValueError, KeyError):
        yield None
        return
    reader = threading.Thread(target=capture.drain,
                              args=(os.fdopen(read_fd, "rb"),), daemon=True)
    writer = os.fdopen(write_fd, "wb", buffering=0)
    reader.start()
    try:
        yield writer
    finally:
        writer.close()
        # subprocess.run has already waited/killed the child. A descendant that
        # inherited stdout must not hold up Runtime timeout or STOP handling.
        reader.join(timeout=2)
