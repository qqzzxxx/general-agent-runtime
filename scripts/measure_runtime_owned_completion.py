"""Offline Phase 3 comparison; character/required-operation counts, not telemetry."""
import json
from pathlib import Path

import audit_intelligence_overhead as audit
import executor_finish as finish
from measure_executor_entry import comparison

ROOT = Path(__file__).resolve().parents[1]
PHASE2 = "evidence/v1.4-executor-entry-adapter/context-comparison.json"
SNAPSHOT = "evidence/v1.4-runtime-owned-completion/phase2-executor-prompt.md"


def report():
    baseline = json.loads(audit.read(ROOT / PHASE2))
    before_prompt = audit.read(ROOT / SNAPSHOT)
    if audit.measure(before_prompt) != baseline["sources_after"]["control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md"]:
        raise ValueError("saved Phase 2 prompt does not bind the existing measurement")
    after_prompt = audit.read(ROOT / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md")
    semantic = {"artifacts": [{"path": "reports/summary.md", "role": "deliverable"},
                              {"path": "evidence/check.txt", "role": "evidence"}],
                "outcome": "PARTIAL", "findings": ["Two supplied sources agree."],
                "evidence": ["evidence/check.txt: comparison; input source A and source B"],
                "limitations": ["A third source was unavailable."],
                "completion": {"Acceptance self-check": ["Cross-check complete for two of three sources"],
                               "Suggested memory updates": ["Third source remains unverified"]}}
    identity = {"MESSAGE_ID": 700110, "TASK_ID": "summary", "STAGE_ID": "check-evidence",
                "ATTEMPT": 1, "NONCE": "<NONCE>"}
    # Same semantic receipt, paths and artifact roles on both sides. Old fields
    # and publication calls model mandatory Executor authoring, not observed text.
    receipt = finish.semantic_receipt(semantic, identity)
    staging = {"COMPLETION_STAGING_SCHEMA_VERSION": 1, **identity, "PROJECT_ID": "synthetic",
               "STATUS": "STAGING_READY", "CREATED_AT": "<ISO_TIMESTAMP>", "RECEIPT": receipt,
               "EVIDENCE": [{"path": "evidence/check.txt", "sha256": "e" * 64}],
               "DELIVERABLES": [{"path": "reports/summary.md", "sha256": "d" * 64}]}
    args = ('--message-id 700110 --task-id "summary" --stage-id "check-evidence" '
            '--attempt 1 --nonce "<NONCE>" --claim-token "<TOKEN>"')
    old_calls = [f'python scripts/executor_fence.py publish {args} --path "{item["path"]}" --sha256 {item["sha256"]}'
                 for item in staging["DELIVERABLES"] + staging["EVIDENCE"]]
    old_calls.append('python scripts/executor_completion.py commit --staging-dir "<STAGING_DIR>" --claim-token "<TOKEN>"')
    new_call = 'python scripts/executor_finish.py --claim-token "<TOKEN>" --result "finish.json"'
    return {"schema": "RUNTIME-OWNED-COMPLETION-OFFLINE-V1", "baseline": PHASE2,
            "baseline_prompt_snapshot": SNAPSHOT,
            "measurement": "BOM-stripped LF-normalized Unicode characters, same Phase 0 ROOT substitution; no token conversion",
            "permanent_template_characters": comparison(len(before_prompt), len(after_prompt)),
            "bound_permanent_prompt_characters": comparison(
                len(before_prompt.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL)),
                len(after_prompt.replace("<RUNTIME_ROOT>", audit.ROOT_LABEL))),
            "task_equivalent_result_characters": comparison(len(audit.compact(staging)), len(audit.compact(semantic))),
            "task_equivalent_result_and_commands_characters": comparison(
                len(audit.compact(staging)) + sum(map(len, old_calls)), len(audit.compact(semantic)) + len(new_call)),
            "sources_after": {"permanent_prompt": audit.measure(after_prompt)},
            "fixtures": {"before_staging": staging, "before_commands": old_calls,
                         "after_result": semantic, "after_command": new_call},
            "executor_required_operations": {
                "whole_stage_phase0": "N + C + 3", "whole_stage_phase2": "N + C + 1",
                "whole_stage_phase3": "C + 1",
                "variables": "N published files; C baseline explicit checks including the entry-owned initial check; C >= 1. Later checkpoints retained.",
                "finish_helper_executions": {"before": "N + 1", "after": 1},
                "finish_identity_argument_values": {"before": "5N", "after": 0},
                "executor_artifact_hashes": {"before": "N (plus optional rechecks)", "after": 0},
                "executor_staging_packages": comparison(1, 0),
                "executor_identity_copies_in_result": comparison(2, 0),
                "executor_machine_hash_manifests": comparison(2, 0),
                "runtime_publications": "N on first success; existing matching records verified on recovery",
                "unchanged": "Claim ownership, work checkpoints, canonical path limits, per-file publication, commit/consume/seal, FV judgments."},
            "limitations": [
                "Static source and synthetic authoring surface; not captured Executor input/output or model turns.",
                "Commands use identical root-relative helper spelling for comparison; installed prompt uses absolute Runtime helper paths.",
                "No total tool-output saving claimed; entry still returns attempt/path/capability metadata.",
                "Runtime still performs hashes, publication checks, staging and commit, plus frozen preparation IO.",
                "Task wire and Supervisor context assembly are unchanged; only the exact Runtime FV packaging instruction is translated in the entry view.",
                "No providers, live project state or Executor automation are invoked."],
            "provider_token_savings": None, "latency_savings_seconds": None}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
