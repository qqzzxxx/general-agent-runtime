"""Diagnostic per-invocation cost, independent of decision/usage authority.

Helper-output counts are cooperative observations, not an exhaustive host read
audit. Native history-pointer retrievals — byte-offset reads of the project
memory file at a routed utf8_offset — are counted separately, never as helper
reads. Persist only bounded counters, never commands, paths or evidence text.
"""
from __future__ import annotations

import json
import math
import re
import time

import provider_usage as usage
from supervisor_inspect import SCHEMA as READ_SCHEMA, MAX_BYTES, REASONS

SCHEMA = "SUPERVISOR-INTELLIGENCE-V1"

# Native pointer-retrieval form observed from real Supervisor sessions
# (2026-09-18 rollouts): when the helper is unavailable in the host shell,
# the model byte-slices the memory file at the routed offset. Recognition is
# deliberately tight to the observed idiom; unrecognized forms undercount
# fail-safe rather than inventing reads.
POINTER_RETRIEVAL = re.compile(r"readallbytes[\s\S]*::\s*utf8\s*\.\s*getstring", re.IGNORECASE)
MEMORY_FILE = "research_state.md"


class Capture:
    def __init__(self, provider_capture, manifest=None):
        self.provider = provider_capture
        self.manifest = manifest
        self.started = time.monotonic()
        self.ids = set()
        self.attempts = self.reads = self.bytes = 0
        self.pointer_reads = 0
        self.incomplete = False

    def accept(self, event):
        if event.get("type") != "item.completed":
            return
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            return
        command = item.get("command")
        if not isinstance(command, str):
            return
        helper = bool(re.search(r"(?:^|[\s/\\])supervisor_inspect\.py(?:[\s\"']|$)", command))
        pointer = (not helper and MEMORY_FILE in command.casefold()
                   and POINTER_RETRIEVAL.search(command))
        if not helper and not pointer:
            return
        ident = item.get("id")
        if not isinstance(ident, str) or not 1 <= len(ident) <= 120:
            self.incomplete = True
            return
        if ident in self.ids:
            return
        if len(self.ids) >= 4096:
            self.incomplete = True
            return
        self.ids.add(ident)
        if pointer:
            # The command form itself reports the outcome: exit 0 means the
            # routed history unit was byte-sliced and decoded successfully.
            if item.get("exit_code") == 0:
                self.pointer_reads += 1
            return
        self.attempts += 1
        try:
            result = json.loads(item.get("aggregated_output", ""), object_pairs_hook=usage.unique)
            if (result["schema"] != READ_SCHEMA or result["reason"] not in REASONS
                    or type(result["bytes_read"]) is not int
                    or not 0 <= result["bytes_read"] <= MAX_BYTES):
                raise ValueError("invalid helper output")
            if result["status"] == "READ" and item.get("exit_code") == 0:
                self.reads += 1
                self.bytes += result["bytes_read"]
            elif result["status"] != "ERROR" or result["bytes_read"] != 0:
                raise ValueError("incomplete helper output")
        except (ValueError, TypeError, KeyError):
            if item.get("exit_code") == 0:
                self.incomplete = True
            # A nonzero exit is a decisive wrapper failure, not uncertainty:
            # the helper never produced a read.

    def finish(self, stream_complete):
        if self.provider.disabled:
            try:
                if usage.read_json(self.provider.start_path) != self.provider.start:
                    return
            except (OSError, ValueError):
                return
        result = {"schema": SCHEMA, "invocation_sha256": usage.digest(self.provider.start),
                  "duration_seconds": round(time.monotonic() - self.started, 3),
                  "context_manifest": self.manifest,
                  "targeted_reads": {"source": "observed_helper_command_outputs",
                      "coverage": ("helper_and_history_pointer_forms" if self.pointer_reads
                                   else "helper_only"),
                      "attempts": self.attempts,
                      "reads": self.reads, "approximate_bytes": self.bytes,
                      "history_pointer_reads": self.pointer_reads,
                      "complete": stream_complete and not self.incomplete}}
        try:
            usage.write_once(self.provider.start_path.with_suffix(".intelligence.json"), result)
        except OSError:
            pass  # diagnostics must not affect lifecycle decisions


def read(root, turn_id, project_id):
    try:
        start_path, _ = usage.paths(root, turn_id)
        start = usage.read_json(start_path)
        result = usage.read_json(start_path.with_suffix(".intelligence.json"))
        if (start["turn_id"] != turn_id or start["PROJECT_ID"] != project_id
                or start.get("schema") != usage.SCHEMA
                or set(result) != {"schema", "invocation_sha256", "duration_seconds", "context_manifest", "targeted_reads"}
                or result["schema"] != SCHEMA
                or result["invocation_sha256"] != usage.digest(start)):
            return None
        duration = result["duration_seconds"]
        if type(duration) not in (int, float) or not math.isfinite(duration) or not 0 <= duration <= 2592000:
            return None
        counts = result["targeted_reads"]
        core = {"source", "coverage", "attempts", "reads", "approximate_bytes", "complete"}
        pointer_reads = counts.get("history_pointer_reads", 0) if isinstance(counts, dict) else None
        if (not isinstance(counts, dict) or not core <= set(counts)
                or set(counts) - core - {"history_pointer_reads"}
                or counts["source"] != "observed_helper_command_outputs"
                or counts["coverage"] not in ("helper_only", "helper_and_history_pointer_forms")
                or type(counts["complete"]) is not bool
                or any(type(counts[k]) is not int or counts[k] < 0 for k in ("attempts", "reads", "approximate_bytes"))
                or not counts["reads"] <= counts["attempts"] <= 4096
                or type(pointer_reads) is not int or not 0 <= pointer_reads <= 4096
                or counts["approximate_bytes"] > counts["reads"] * MAX_BYTES):
            return None
        import supervisor_control
        try:
            supervisor_control._validate_context_manifest(result["context_manifest"])
        except supervisor_control.ControlError:
            return None
        return result
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return None


def cache_split(reported_usage):
    """Codex cached_input_tokens is a subset of input_tokens; label derivation."""
    total = reported_usage.get("input_tokens")
    cached = reported_usage.get("cached_input_tokens")
    valid = (reported_usage.get("reported") is True and type(total) is int
             and type(cached) is int and 0 <= cached <= total)
    return {"cached_input_tokens": cached if reported_usage.get("reported") else None,
            "uncached_input_tokens": total - cached if valid else None,
            "uncached_source": "input_minus_cached" if valid else None}
