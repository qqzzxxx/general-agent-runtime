"""Offline Phase 7/8 context comparison and recorded A/B delegation audit.

Uses only saved evidence and private temporary files. No provider, automation,
live Runtime, network, browser or Executor work is invoked. V2 proposals below
are authored counterfactual examples, not sampled Supervisor decisions.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import ordinary_dispatch as od
import executor_contract

EVIDENCE = ROOT / "evidence/v1.4-supervisor-v2"
TIME = "2026-09-14T00:00:00+00:00"


def read(path):
    return path.read_text(encoding="utf-8-sig")


def metrics(text):
    return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def ui_proposal():
    return {
        "logical_task": "console_product", "logical_stage": "design_implement_validate",
        "objective": "Improve the supplied console into a polished, clear and usable AI/developer product. Own the final experience, inspect it and improve weak results before finishing.",
        "inputs": ["index.html", "styles.css"],
        "outputs": ["workspace/console/index.html", "workspace/console/styles.css",
                    "evidence/console/verification.md and representative final visual evidence"],
        "outcome_context": {
            "desired_outcome": "A mature product interface that makes project status, ongoing work and intended operator actions easy to understand and use.",
            "quality_bar": ["Judge the overall user experience as well as individual defects.",
                            "Inspect rendered desktop and small-screen experiences and exercise the existing actions; report actual evidence and remaining limits."],
            "hard_constraints": [{"constraint": "Preserve supplied information and functionality, including the intended actions and their meaning.",
                                  "source": "PROJECT_GOAL.md: Objective and Deliverable"}],
            "current_facts": ["The original is a static local HTML/CSS fixture with illustrative data and inline JavaScript for Pause, Resume, individual review and review-all; it has no backend.",
                              "Status, agents, progress, activity, artifacts, token usage and alerts are currently presented in tables on one page."],
            "revisable_assumptions": ["The current table layout, section order, navigation, labels, DOM and JavaScript structure are implementation choices. Reorganize, relabel or replace them when this improves the experience while retaining information and intended behavior.",
                                      "Details may move into discoverable views or disclosures. Choose the design and implementation; no specific component layout is prescribed."]},
        "acceptance_criteria": [
            "An operator can readily understand current project health, ongoing work, progress, budget and next actions; the overall result feels coherent and mature.",
            "All original information remains accessible and semantically faithful. Pause/Resume state changes, individual acknowledgement, review-all, disabled states and activity feedback remain usable; changes in layout, copy or implementation may support these outcomes.",
            "Rendered inspection and interaction checks substantiate the result. Resolve material visual, usability, keyboard/focus and responsive defects; identify checks that were not possible.",
            "Keep the illustrative/local nature clear; the interface must not imply that fixture interactions control a real Runtime."],
        "stop_conditions": ["Report an unresolved gap if preserving required information or action meaning conflicts with the goal after considering alternative designs."],
        "execution": {"autonomy": "HIGH", "read_paths": ["index.html", "styles.css"]}}


def fixed_constraint_proposal():
    return {"logical_task": "keyboard_fix", "logical_stage": "repair_verify",
        "objective": "Make the existing Pause button keyboard-operable without breaking its public integration.",
        "inputs": ["workspace/console.html"], "outputs": ["workspace/console.html"],
        "acceptance_criteria": ["Keyboard activation changes status and preserves the documented public ID."],
        "forbidden_actions": ["Do not rename the public btn-pause ID."],
        "outcome_context": {"desired_outcome": "An operable and compatible control", "quality_bar": ["Demonstrated keyboard behavior"],
            "hard_constraints": [{"constraint": "Keep the public btn-pause ID", "source": "Synthetic user requirement: external clients bind this ID"}],
            "current_facts": ["The control is a styled div"],
            "revisable_assumptions": ["Its internal implementation may change while the public ID remains stable"]},
        "execution": {"autonomy": "NORMAL"}}


def wire(path):
    return json.loads(re.findall(r"```json\s*\n(.*?)\n```", read(path), re.S)[0])


def report(output=None):
    spec = importlib.util.spec_from_file_location("phase8_measure_orchestrator", ROOT / "orchestrator.py")
    o = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(o)
    frozen = read(EVIDENCE / "phase7-prompt-builder.py")
    exec(compile(frozen.replace("def build_codex_prompt(", "def phase7_build_codex_prompt(", 1),
                 "phase7-prompt-builder.py", "exec"), o.__dict__)
    tree = ast.parse(read(EVIDENCE / "phase7-ordinary-dispatch.py"))
    old_contract = next(ast.literal_eval(node.value) for node in tree.body
                        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and
                        t.id == "SUPERVISOR_CONTRACT" for t in node.targets))
    old_rules = read(EVIDENCE / "phase7-runtime-contract.md")
    new_rules = read(ROOT / "control/CODEX_SUPERVISOR_RUNTIME.md")
    comparisons = {}
    prompts = {}
    with tempfile.TemporaryDirectory(prefix="supervisor-v2-measure-") as temporary:
        root = Path(temporary)
        project = root / "projects" / "comparison"
        project.mkdir(parents=True)
        (root / "control").mkdir()
        shutil.copytree(ROOT / "profiles", root / "profiles")
        o.ROOT = root
        o.PROFILES_DIR = root / "profiles"
        o.PROJECT_STATE = project / "project_state.json"
        o.RESEARCH_STATE = project / "RESEARCH_STATE.md"
        o.SUPERVISOR_RULES = root / "control/CODEX_SUPERVISOR_RUNTIME.md"
        o.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
        o.ACTIVE_PROJECT = {"project_id": "comparison", "project_root": project}
        o.stamp = lambda: TIME
        goal = project / "PROJECT_GOAL.md"
        for name in ("index.html", "styles.css"):
            shutil.copyfile(EVIDENCE / "ab-inputs" / name, project / name)
        for case in ("summary_bootstrap", "ui_product_bootstrap", "ui_history_and_steer",
                     "late_goal_requirement", "fv_result", "timeout", "dispatch_repair"):
            goal_text = read(EVIDENCE / "ab-inputs/PROJECT_GOAL.md") if case.startswith("ui_") else "# Goal\nProduce a checked summary of supplied evidence.\n"
            if case == "late_goal_requirement":
                goal_text += "Background detail.\n" * 900 + "\nLATE_SUCCESS_CRITERION: preserve source attribution.\n"
            goal.write_text(goal_text, encoding="utf-8")
            state = {"schema_version": 4, "project_id": "comparison", "project_type": "GENERAL",
                "profile": "GENERAL", "status": "SUPERVISOR_TURN", "phase": "GENERAL", "goal_file": "PROJECT_GOAL.md",
                "goal_anchor": {"schema_version": 1, "goal_path": "PROJECT_GOAL.md", "goal_sha256": hashlib.sha256(goal.read_bytes()).hexdigest(),
                                "bound_at": TIME, "provenance": "bootstrap"},
                "current_task": None, "next_message_id": 700100,
                "final_verification": {"required": True, "status": "NOT_STARTED", "policy_id": "GENERAL_FV_V1", "policy_version": 1},
                "last_supervisor_decision": None, "decision_history": []}
            if case.startswith("ui_"):
                # Both recorded A/B projects used this domain profile.
                state.update(project_type="SOFTWARE_ENGINEERING", profile="SOFTWARE_ENGINEERING",
                             phase="SOFTWARE_ENGINEERING")
                state["final_verification"]["policy_id"] = "SOFTWARE_ENGINEERING_FV_V1"
            memory = "# Memory\nNo decisions yet.\n"
            reason, event, turn = "ORCHESTRATOR_START", {}, None
            if case == "ui_history_and_steer":
                last = {"decision": "REVISE", "reason": "LATEST_DECISION_SENTINEL",
                    "goal_alignment": {"original_objective": "A polished usable product", "unmet_criteria": "Overall hierarchy remains weak",
                                       "latest_result": "Local controls work", "next_action_alignment": "Improve the experience", "scope_drift": "None", "method": "Reassess design"}}
                state.update(last_supervisor_decision=last, decision_history=[{"reason": "Old decision. " * 150}] * 30 + [last])
                memory = "<!-- supervisor-context-v2: current-memory -->\n## Hard constraints\nPreserve action meanings.\n## Decision history\n" + "Historical outcome. " * 1000 + "\n## Unresolved questions\nHierarchy remains weak.\n"
                reason = "EXECUTOR_RESULT_READY"
                turn = {"interventions": [{"intervention_id": "H-001", "mode": "STEER", "instruction_text": "Improve the overall experience; the current layout is not fixed."}]}
            if case == "fv_result":
                reason = "EXECUTOR_RESULT_READY"
                results = {"OVERALL_STATUS": "FAIL", "CLAIM_RESULTS": [{"claim_id": "C1", "status": "REFUTED", "auditor_note": "Attribution is missing."}]}
                receipt = {"MESSAGE_ID": 700099, "TASK_ID": "verify", "STAGE_ID": "check", "ATTEMPT": 1, "NONCE": "fixture",
                    "STATUS": "COMPLETED", "Key findings": ["Attribution is missing."], "Limitations": ["One source unavailable"],
                    "FINAL_VERIFICATION_RESULTS": results, "FINAL_VERIFICATION": copy.deepcopy(results)}
                path = root / "synthetic-completion.json"
                path.write_text(compact({"COMPLETION_PROTOCOL_VERSION": 1, "STATUS": "COMPLETION_CONSUMED", "RECEIPT": receipt}), encoding="utf-8")
                event = {"type": reason, "message_id": 700099, "committed_receipt_path": str(path)}
                o.SUPERVISOR_BRIEF.write_text("UNTRUSTED_ROOT_MUST_NOT_WIN", encoding="utf-8")
            if case == "timeout":
                reason, event = "EXECUTOR_TIMEOUT", {"message_id": 700099, "elapsed_seconds": 3600, "reason": "unfinished"}
            if case == "dispatch_repair":
                state["dispatch_repair"] = {"error": "invalid acceptance_criteria", "repair_attempt": 1, "max_repair_attempts": 1}
            o.PROJECT_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")
            o.RESEARCH_STATE.write_text(memory, encoding="utf-8")
            profile = o.resolve_profile(state)
            old_profile = copy.deepcopy(profile)
            old_profile["supervisor_guidance"] = read(EVIDENCE / f"phase7-{state['profile']}-supervisor.md")
            class FixedUUID:
                hex = "0" * 32
            with patch.object(o.uuid, "uuid4", return_value=FixedUUID()):
                o.SUPERVISOR_RULES.write_text(old_rules, encoding="utf-8")
                with patch.object(od, "SUPERVISOR_CONTRACT", old_contract), patch.object(o, "resolve_profile", return_value=old_profile):
                    old = o.phase7_build_codex_prompt(reason, event, state, control_turn=turn)
                o.SUPERVISOR_RULES.write_text(new_rules, encoding="utf-8")
                new = o.build_codex_prompt(reason, event, state, control_turn=turn)
            # Windows JSON paths and plain paths normalized, with no token conversion.
            def normalize(text):
                return str(text).replace(str(root).replace("\\", "\\\\"), "<RUNTIME_ROOT>").replace(str(root), "<RUNTIME_ROOT>").replace("\\", "/")
            old, new = normalize(old), normalize(new)
            old_size, new_size = metrics(old), metrics(new)
            comparisons[case] = {"phase7": old_size, "v2": new_size,
                "reduction_percent": round(100 * (len(old) - len(new)) / len(old), 2),
                "latest_decision_visible": {"phase7": "LATEST_DECISION_SENTINEL" in old, "v2": "LATEST_DECISION_SENTINEL" in new},
                "late_goal_visible": {"phase7": "LATE_SUCCESS_CRITERION" in old, "v2": "LATE_SUCCESS_CRITERION" in new},
                "steer_copies": {"phase7": old.count("Improve the overall experience; the current layout is not fixed."), "v2": new.count("Improve the overall experience; the current layout is not fixed.")}}
            prompts[case] = (old, new)

    semantic_keys = ("OBJECTIVE", "INPUTS", "OUTPUTS", "ACCEPTANCE_CRITERIA", "FORBIDDEN_ACTIONS", "STOP_CONDITIONS", "EXECUTION", "OUTCOME_CONTEXT")
    delegates = {}
    for label, filename in (("a_v13_recorded", "a-first-dispatch.md"), ("b2_phase7_recorded", "b2-first-dispatch.md")):
        task = wire(EVIDENCE / "ab-inputs" / filename)
        delegates[label] = {"kind": "recorded first implementation dispatch", "message_id": task["MESSAGE_ID"],
            "full_task": metrics(compact(task)), "semantic_surface": metrics(compact({k: task[k] for k in semantic_keys if k in task})),
            "acceptance_criteria_count": len(task["ACCEPTANCE_CRITERIA"]), "forbidden_actions_count": len(task["FORBIDDEN_ACTIONS"]),
            "executor_protocol_entries": len(task["EXECUTOR_PROTOCOL"])}
    proposal = od.validate_proposal(ui_proposal())
    # Exercise the unchanged Executor V2 presentation without claiming/dispatching.
    task = {k.upper(): v for k, v in proposal.items()}
    task["EXECUTOR_PROTOCOL"] = list(od.EXECUTOR_PROTOCOL)
    task["FORBIDDEN_ACTIONS"] = list(dict.fromkeys([
        *od.BASE_RESTRICTIONS, *proposal["forbidden_actions"]]))
    executor_view = executor_contract.project(task, check_host=False)
    delegates["v2_authored_example"] = {"kind": "counterfactual authored example, not a model run",
        "proposal": metrics(compact(proposal)), "semantic_surface": metrics(compact({k: task[k] for k in semantic_keys if k in task})),
        "executor_task_view": metrics(compact(executor_view)), "acceptance_criteria_count": len(proposal["acceptance_criteria"]),
        "forbidden_actions_count": len(task["FORBIDDEN_ACTIONS"]),
        "extra_forbidden_actions_authored": len(proposal["forbidden_actions"]),
        "executor_protocol_entries_authored": 0}
    fixed = od.validate_proposal(fixed_constraint_proposal())
    old_ui, new_ui = prompts["ui_product_bootstrap"]
    def section(text, start, end):
        return text.split(f"=== {start} ===\n", 1)[1].split(f"\n=== {end} ===", 1)[0]
    result = {"schema": "SUPERVISOR-V2-OFFLINE-COMPARISON", "contexts": comparisons,
        "delivered_contract_instructions": {
            "phase7": metrics(section(old_ui, "SUPERVISOR RUNTIME CONTRACT", "END RUNTIME CONTRACT")),
            "v2": metrics(section(new_ui, "SUPERVISOR RUNTIME CONTRACT", "END SUPERVISOR RUNTIME CONTRACT")
                          + section(new_ui, "ORDINARY OUTCOME DELEGATION", "END ORDINARY OUTCOME DELEGATION")),
            "note": "Delivered role/delegation contract bodies, excluding delimiters, Goal Anchor, profile, scope and closing instructions; includes meaningful semantic guidance, not pure protocol waste."},
        "delegation": delegates, "source_inputs": json.loads(read(EVIDENCE / "ab-inputs/sources.json")),
        "provider_token_savings": None, "latency_savings_seconds": None, "v2_model_quality_improvement": None,
        "limits": ["LF-normalized Unicode characters and UTF-8 bytes; runtime paths, turn time and nonce are fixed. Not provider tokens.",
                   "Same goal/state/memory/event per context pair; Phase 7 builder/contract/ordinary instructions and pre-change GENERAL/SOFTWARE_ENGINEERING guidance are frozen.",
                   "Bootstrap UI uses the exact shared Goal and original input bytes. Other cases are synthetic, including a serialized receipt for presentation only, not an acceptance/ledger test.",
                   "V2 examples are authored design cases; A/B dispatches are recorded model outputs. Counts do not establish causal behavior or visual quality.",
                   "Host prompts, tools, later source reads, model history, process overhead and Executor execution are excluded.",
                   "Long goals/legacy memory remain complete; V2 has no total context budget or claim of savings on every case."]}
    if output:
        output.mkdir(parents=True, exist_ok=True)
        (output / "comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for case, (old, new) in prompts.items():
            (output / f"{case}-phase7.txt").write_text(old, encoding="utf-8")
            (output / f"{case}-v2.txt").write_text(new, encoding="utf-8")
        (output / "ui-v2-proposal.json").write_text(json.dumps(proposal, indent=2) + "\n", encoding="utf-8")
        (output / "fixed-constraint-proposal.json").write_text(json.dumps(fixed, indent=2) + "\n", encoding="utf-8")
        (output / "ui-v2-executor-view.json").write_text(json.dumps(executor_view, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    print(json.dumps(report(args.output), ensure_ascii=False, indent=2))
