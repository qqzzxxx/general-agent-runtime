"""Matched bootstrap/READY surface census using private, sealed Runtime fixtures.

Run --phase before before editing, then --phase after. No automation or model run.
Only temporary fixture roots and opaque sessions are normalized in READY output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import test_executor_contract as ordinary
import test_semantic_finish_recovery as verification

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "evidence/v1.4-executor-bootstrap-v3"
SOURCES = ["control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md", "scripts/executor_entry.py",
           "scripts/executor_contract.py", "scripts/executor_work.py", "scripts/executor_inspect.py"]


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def size(text):
    return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8"))}


def implementation_task():
    return {**ordinary.task_fields(), "OBJECTIVE": "Implement a UTF-8 text summary command.",
            "INPUTS": ["evidence/brief.txt"], "OUTPUTS": ["workspace/summarize.py"],
            "ACCEPTANCE_CRITERIA": ["Summarize valid UTF-8 input; report malformed input clearly."],
            "EXECUTION": {"autonomy": "NORMAL"}}


def capture(phase, output=OUTPUT):
    output.mkdir(parents=True, exist_ok=True)
    template = (ROOT / SOURCES[0]).read_bytes().decode("utf-8-sig")
    rendered = template.replace("<RUNTIME_ROOT>", str(ROOT))
    (output / f"{phase}-static.txt").write_bytes(rendered.encode("utf-8"))
    result = {"phase": phase, "runtime_path": str(ROOT), "static": size(rendered),
              "sources": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in SOURCES},
              "roles": {}}
    for role in ("implementation", "high_host_ui", "final_verification"):
        fixture = (verification.SemanticFinishRecoveryTests() if role == "final_verification"
                   else ordinary.ExecutorContractTests())
        fixture.setUp()
        try:
            ready = (fixture.start() if role != "implementation" else fixture.start(implementation_task()))
            raw = compact(ready).replace(fixture.session, "<SESSION>")
            raw = raw.replace(json.dumps(str(fixture.root))[1:-1], json.dumps(str(ROOT))[1:-1])
            ready = json.loads(raw)
            contract = compact(ready["contract"])
            result["roles"][role] = {"contract": size(contract), "ready": size(raw),
                "bootstrap_plus_contract": size(rendered + contract), "bootstrap_plus_ready": size(rendered + raw)}
            (output / f"{phase}-{role}-ready.json").write_text(
                json.dumps(ready, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        finally:
            fixture.doCleanups()
    (output / f"{phase}-measurement.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(capture(args.phase, args.output), indent=2))
