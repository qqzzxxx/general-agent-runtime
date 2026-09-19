"""Offline Phase 2 source/view census against the saved Phase 0 baseline.

Counts normalized characters, never model tokens, tool calls or elapsed latency.
Reads repository sources only; no live Runtime state, claims or providers.
"""
import json
from pathlib import Path
import re

import audit_intelligence_overhead as audit
import executor_entry as entry

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "evidence/v1.4-intelligence-overhead-audit/context-census.json"


def comparison(before: int, after: int) -> dict:
    return {"before": before, "after": after, "reduction": before - after,
            "reduction_percent": round(100 * (before - after) / before, 2)}


def report() -> dict:
    baseline = json.loads(audit.read(ROOT / BASELINE))
    prompt_path = "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md"
    reference_path = "docs/STALE_WORKER_FENCING.md"
    prompt = audit.read(ROOT / prompt_path)
    bound = prompt.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL)
    template = json.loads(re.findall(r"```json\s*\n(.*?)\n```", audit.read(
        ROOT / "control/EXECUTOR_TASK_TEMPLATE.md"), re.S)[0])
    before_task = baseline["executor_template_payload"]
    # The compatibility wire is deliberately unchanged. Refuse a misleading
    # baseline comparison if a later phase changes this synthetic fixture.
    if audit.wire_metrics(template) != before_task:
        raise ValueError("Phase 0 task fixture changed; choose an explicit task-equivalent baseline")
    after_task = entry.task_view(template)
    old_prompt = baseline["sources"][prompt_path]["characters"]
    old_bound = baseline["executor_bound_automation"]["characters"]
    old_reference = baseline["sources"][reference_path]["characters"]
    return {"schema": "EXECUTOR-ENTRY-OFFLINE-V1", "baseline": BASELINE,
            "measurement": "BOM-stripped, LF-normalized Unicode characters; no token conversion",
            "permanent_template_characters": comparison(old_prompt, len(prompt)),
            "bound_permanent_prompt_characters": comparison(old_bound, len(bound)),
            "required_fencing_reference_characters": comparison(old_reference, 0),
            "static_reading_surface_characters": comparison(old_bound + old_reference, len(bound)),
            "synthetic_task_payload_characters": comparison(
                before_task["compact_json"]["characters"], len(audit.compact(after_task))),
            "synthetic_task_protocol_entries": comparison(before_task["protocol_entries"],
                                                          len(after_task.get("EXECUTOR_PROTOCOL", []))),
            "sources_after": {prompt_path: audit.measure(prompt), "bound_prompt": audit.measure(bound)},
            "task_view_fixture": after_task,
            "executor_required_operations": {
                "startup_helper_executions": comparison(3, 1),
                "startup_protocol_file_reads": comparison(3, 0),
                "startup_identity_arguments_authored": comparison(15, 0),
                "before_startup": ["read inbox and extract identity", "acquire", "read active project",
                                   "prepare", "initial check", "read fencing reference"],
                "after_startup": ["executor_entry.py; READY/0 or stop"],
                "whole_stage_before": "N + C + 3",
                "whole_stage_after": "N + C + 1",
                "variables": "N published files; C baseline explicit checks including the initial check; C >= 1.",
                "unchanged": "N publications, C-1 later checkpoints (check or resume entry), hashes, staging and one completion commit."},
            "limitations": [
                "Counts expected repository source-reading surface, not captured ZCode input.",
                "Task view excludes the attempt credential/path/capability result envelope; no total tool-output saving claimed.",
                "Runtime still reads immutable wire/archive and active project internally. Old wire protocol stays for compatibility.",
                "Only exact canonical protocol strings are removed; custom instructions and FV fields remain untruncated.",
                "Helper executions can share a shell call; they are not model turns. No-work still wakes the existing automation.",
                "Publication/completion instructions remain in the permanent prompt; no Phase 3 packaging change."],
            "provider_token_savings": None, "latency_savings_seconds": None}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
