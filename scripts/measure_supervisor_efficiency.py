"""Offline matched Phase 8/9 B3 context/read comparison; no model invocation."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import tempfile
from unittest.mock import patch

import ordinary_dispatch
import supervisor_context

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence/v1.4-supervisor-intelligence-efficiency"


def proposal():
    # Authored solely from the Goal and input names. Source-level behavior and
    # component inventories belong to the Executor's investigation.
    return {
        "logical_task": "console_product", "logical_stage": "redesign_implement_verify",
        "objective": "Improve the supplied AI Agent Project Console into a polished, clear and usable mature AI/developer product. Own the final experience, inspect it and improve material weaknesses before finishing.",
        "inputs": ["index.html", "styles.css"],
        "outputs": ["workspace/console/index.html", "workspace/console/styles.css", "evidence/console/"],
        "outcome_context": {
            "desired_outcome": "A mature console whose information and intended actions are clear and usable.",
            "quality_bar": ["Judge the overall experience and visual quality, supported by rendered and interaction evidence."],
            "hard_constraints": [{"constraint": "Preserve supplied information and intended actions.", "source": "PROJECT_GOAL.md Objective and Deliverable"}],
            "current_facts": ["PROJECT_GOAL.md names index.html and styles.css as the supplied inputs. Their implementation has not been inspected by the Supervisor."],
            "revisable_assumptions": ["Current layout, wording, navigation, DOM, file structure and implementation may change when meaning and intended behavior remain available."]},
        "acceptance_criteria": [
            "The overall rendered experience is coherent, polished, clear and usable as a mature AI/developer product.",
            "Inspect the supplied implementation, establish its information and intended actions, and substantiate their preservation in the result. Choose the design and method.",
            "Inspect representative desktop and narrow layouts and exercise intended interactions, including keyboard use. Improve material weaknesses before reporting completion; provide evidence and identify unverified checks."],
        "execution": {"autonomy": "HIGH", "read_paths": ["index.html", "styles.css"]}}


def report(output=None):
    spec = importlib.util.spec_from_file_location("phase8_context_frozen", EVIDENCE / "baseline/supervisor_context.py")
    phase8 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(phase8)
    paired = {}
    with tempfile.TemporaryDirectory(prefix="phase9-measure-") as temporary:
        root = Path(temporary)
        project = root / "projects/console-b3-001"
        project.mkdir(parents=True)
        shutil.copytree(ROOT / "profiles", root / "profiles")
        (root / "control").mkdir()
        goal = project / "PROJECT_GOAL.md"
        goal.write_bytes((EVIDENCE / "b3-inputs/PROJECT_GOAL.md").read_bytes())
        memory = project / "RESEARCH_STATE.md"
        memory.write_text("## Current facts\nPROJECT_GOAL.md names index.html and styles.css. Implementation has not yet been inspected.\n", encoding="utf-8")
        state_path = project / "project_state.json"
        state = {"status": "SUPERVISOR_TURN", "current_task": None, "next_message_id": 700100, "decision_history": []}
        state_path.write_text(json.dumps(state), encoding="utf-8")
        # Both builders receive identical Goal/state/memory and the same source
        # inventory. Reads are measured at the filesystem boundary, not inferred.
        for name in ("index.html", "styles.css"):
            shutil.copyfile(ROOT / "evidence/v1.4-supervisor-v2/ab-inputs" / name, project / name)
        for version, module in (("phase8", phase8), ("phase9", supervisor_context)):
            rules = root / "control/CODEX_SUPERVISOR_RUNTIME.md"
            source = EVIDENCE / "baseline/CODEX_SUPERVISOR_RUNTIME.md" if version == "phase8" else ROOT / "control/CODEX_SUPERVISOR_RUNTIME.md"
            rules.write_bytes(source.read_bytes())
            guide = EVIDENCE / "baseline/SUPERVISOR_GUIDANCE.md" if version == "phase8" else ROOT / "profiles/SOFTWARE_ENGINEERING/SUPERVISOR_GUIDANCE.md"
            profile = {"profile_id": "SOFTWARE_ENGINEERING", "profile_version": 1,
                       "supervisor_guidance": guide.read_text(encoding="utf-8")}
            reads = []
            original = Path.read_bytes
            def counted(path):
                data = original(path)
                reads.append({"path": path.relative_to(root).as_posix(), "bytes": len(data)})
                return data
            with patch.object(Path, "read_bytes", counted):
                prompt = module.build(root=root, reason="ORCHESTRATOR_START", event={}, state=state,
                    state_path=state_path, memory_path=memory, goal_path=goal, rules_path=rules,
                    profile=profile, goal_anchor="Goal hash verified by Runtime (offline fixture).", scope="Active project: projects/console-b3-001",
                    human_block="", interventions=[], receipt=None, receipt_path=None,
                    fallback_brief=None, now="2026-09-14T00:00:00+00:00", nonce="fixed")
            normalized = str(prompt).replace(str(root), "<RUNTIME>")
            paired[version] = {"prompt_characters": len(normalized), "prompt_utf8_bytes": len(normalized.encode("utf-8")),
                               "builder_read_count": len(reads), "builder_read_bytes": sum(r["bytes"] for r in reads),
                               "builder_reads": reads, "implementation_read_bytes": sum(r["bytes"] for r in reads if r["path"].endswith(("index.html", "styles.css")))}
            if output:
                output.mkdir(parents=True, exist_ok=True)
                (output / f"b3-bootstrap-{version}.txt").write_text(normalized, encoding="utf-8", newline="\n")
    archive = json.loads((EVIDENCE / "b3-inputs/first-dispatch-archive.json").read_text())
    actual_bytes = (EVIDENCE / "b3-inputs/first-dispatch.md").read_bytes()
    assert hashlib.sha256(actual_bytes).hexdigest() == archive["dispatch_sha256"]
    actual = json.loads(re.findall(r"```json\s*\n(.*?)\n```", actual_bytes.decode(), re.S)[0])
    authored = proposal()
    ordinary_dispatch.validate_proposal(authored)
    result = {"kind": "offline matched context construction and authored delegation; not a model run",
        "scenario": "B3 console-b3-001 initial product redesign", "contexts": paired,
        "b3_archive_sha256": archive["dispatch_sha256"],
        "b3_actual_autonomy": actual["EXECUTION"]["autonomy"],
        "phase9_authored_autonomy": authored["execution"]["autonomy"],
        "phase8_live_deep_read_count": None, "phase9_authored_pre_dispatch_deep_reads": 0,
        "provider_token_improvement": None, "live_latency_improvement": None, "live_model_quality_improvement": None,
        "limits": "Builder reads exclude upstream Goal verification/profile loading and later model reads. Both builders already avoid implementation reads. Helper read cost is tested separately. No live B3 source-read trace is assumed."}
    if output:
        (output / "comparison.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        (output / "b3-phase9-authored-proposal.json").write_text(json.dumps(authored, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(report(args.output), indent=2))
