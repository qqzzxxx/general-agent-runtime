"""Task-equivalent offline Phase 1 comparison against the saved Phase 0 census.

Run from the Runtime root: python scripts/measure_runtime_owned_dispatch.py
No provider call, Runtime mutation, token conversion or latency inference.
"""
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import audit_intelligence_overhead as audit


def report():
    baseline = json.loads(audit.read(ROOT / "evidence/v1.4-intelligence-overhead-audit/context-census.json"))
    after = audit.synthetic_prompts()
    before = baseline["supervisor_synthetic"]
    contexts = {}
    for name in after:
        old, new = before[name]["total"]["characters"], after[name]["total"]["characters"]
        contexts[name] = {"before_characters": old, "after_characters": new,
                          "reduction_characters": old - new,
                          "reduction_percent": round(100 * (old - new) / old, 2),
                          "before_sections": before[name]["sections"],
                          "after_sections": after[name]["sections"]}
    template = json.loads(re.findall(r"```json\s*\n(.*?)\n```", audit.read(
        ROOT / "control/EXECUTOR_TASK_TEMPLATE.md"), re.S)[0])
    # Keep exactly the same work and task restrictions on both sides. Mechanical
    # values are representative fixed fixtures, not captured provider output.
    proposal = {"logical_task": "summary", "logical_stage": "check-evidence",
                "objective": "Produce a checked summary of the supplied evidence.",
                "inputs": ["evidence/source.txt"], "outputs": ["reports/summary.md"],
                "acceptance_criteria": ["Every finding cites supporting evidence."],
                "forbidden_actions": ["Do not modify the source evidence."],
                "stop_conditions": ["Stop if the supplied evidence is unreadable."],
                "max_time": 900, "max_retries": 2}
    template.update(MESSAGE_ID=700100, TASK_ID=proposal["logical_task"],
                    STAGE_ID=proposal["logical_stage"], NONCE="0" * 48,
                    ISSUED_AT=audit.FIXED_TIME)
    for key in ("objective", "inputs", "outputs", "acceptance_criteria", "stop_conditions", "max_time", "max_retries"):
        template[key.upper()] = proposal[key]
    template["FORBIDDEN_ACTIONS"] += proposal["forbidden_actions"]
    projection_keys = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE",
                       "ISSUED_AT", "MAX_TIME", "SCHEDULER_GRACE_SECONDS", "SUPERVISOR_REVIEW_EFFORT")
    old_output = {"executor_task": template,
                  "current_task": {k: template[k] for k in projection_keys},
                  "next_message_id": 700101,
                  "decision_identity": {k.lower(): template[k] for k in ("MESSAGE_ID", "TASK_ID", "STAGE_ID")}}
    new_output = {"ordinary_task_proposal": proposal}
    old_size, new_size = audit.measure(audit.compact(old_output)), audit.measure(audit.compact(new_output))
    return {"schema": "RUNTIME-OWNED-DISPATCH-OFFLINE-V1",
            "measurement": "LF-normalized Unicode characters / UTF-8 bytes; synthetic, not live model output",
            "baseline": "evidence/v1.4-intelligence-overhead-audit/context-census.json",
            "supervisor_context": contexts,
            "authored_dispatch_surface": {"before": old_size, "after": new_size,
                "reduction_characters": old_size["characters"] - new_size["characters"],
                "reduction_percent": round(100 * (old_size["characters"] - new_size["characters"]) / old_size["characters"], 2),
                "before_fixture": old_output, "after_fixture": new_output},
            "limitations": ["Output fixture includes one decision identity projection; identical decision rationale/history/memory excluded.",
                "No production workload distribution, provider/system prompts, tools or later turns measured.",
                "Executor protocol remains present in generated dispatches; no Executor context reduction claimed."],
            "provider_token_savings": None, "latency_savings_seconds": None}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
