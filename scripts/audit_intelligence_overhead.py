"""Offline Phase 0 context census; never invokes an Agent or changes Runtime state.

Run: python scripts/audit_intelligence_overhead.py
Prints sizes, hashes and historical durations, never prompts or claim tokens.
Synthetic prompt probes use the current builder in a private temporary directory.
Counts describe LF-normalized text, not provider tokens or hidden host context.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import statistics
import tempfile

REPO = Path(__file__).resolve().parents[1]
SNAPSHOT = Path("evidence/dogfood-fix-02-deployment/snapshot/control/supervisor_turns")
FIXED_TIME = "2026-09-14T00:00:00+00:00"
ROOT_LABEL = "<AUDIT_RUNTIME_ROOT>"


def measure(text: str) -> dict:
    return {"characters": len(text), "utf8_bytes": len(text.encode("utf-8")),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def compact(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def wire_metrics(payload: dict) -> dict:
    # Each field includes its JSON key and colon, but excludes outer braces/commas.
    fields = {key: len(compact(key) + ":" + compact(value))
              for key, value in payload.items()}
    return {"compact_json": measure(compact(payload)), "field_characters": fields,
            "framing_characters": len(payload) + 1,
            "protocol_entries": len(payload.get("EXECUTOR_PROTOCOL", []))}


def prompt_sections(prompt: str) -> dict:
    """Split only our controlled synthetic probes, not arbitrary Agent messages."""
    labels = {
        "runtime_contract": ("SUPERVISOR RUNTIME CONTRACT", "END RUNTIME CONTRACT"),
        "project_state": ("PROJECT STATE", "END PROJECT STATE"),
        "memory": ("COMPRESSED RESEARCH STATE", "END RESEARCH STATE"),
        "goal": ("PROJECT GOAL", "END PROJECT GOAL"),
        "goal_anchor": ("GOAL ANCHOR (GOAL-ANCHOR-V1)", "END GOAL ANCHOR"),
        "profile": ("PROJECT PROFILE", "END PROJECT PROFILE"),
        "scope": ("PROJECT RUNTIME SCOPE", "END PROJECT RUNTIME SCOPE"),
        "brief": ("CURRENT EXECUTOR BRIEF", "END EXECUTOR BRIEF"),
    }
    if "=== WHAT IS TRUE NOW ===" in prompt:
        # Phase 8 keeps this current-builder census usable; saved Phase 0/1
        # measurements remain historical. Compare V2 with the frozen Phase 7
        # builder using measure_supervisor_context.py.
        labels.update({
            "runtime_contract": ("SUPERVISOR RUNTIME CONTRACT", "END SUPERVISOR RUNTIME CONTRACT"),
            "project_state": ("WHAT IS TRUE NOW", "END WHAT IS TRUE NOW"),
            "memory": ("CURRENT PROJECT MEMORY", "END CURRENT PROJECT MEMORY"),
            "goal_anchor": ("GOAL INTEGRITY", "END GOAL INTEGRITY"),
            "brief": ("UNVERIFIED COMPATIBILITY BRIEF (UNTRUSTED)", "END UNVERIFIED COMPATIBILITY BRIEF (UNTRUSTED)"),
        })
    sections = {}
    for name, (start, end) in labels.items():
        marker = f"=== {start} ===\n"
        sections[name] = (prompt.split(marker, 1)[1].split(f"\n=== {end} ===", 1)[0]
                          if marker in prompt else "")
    sizes = {name: measure(value) for name, value in sections.items()}
    # Includes section delimiters, turn event/time/nonce, dispatch/FV instructions,
    # and any conditional intervention/repair instructions. Not pure waste.
    sizes["wrapper_and_conditional"] = {
        "characters": len(prompt) - sum(len(value) for value in sections.values()),
        "utf8_bytes": len(prompt.encode("utf-8")) - sum(
            len(value.encode("utf-8")) for value in sections.values())}
    return sizes


def synthetic_prompts() -> dict:
    spec = importlib.util.spec_from_file_location("overhead_audit_orchestrator",
                                                REPO / "orchestrator.py")
    o = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(o)
    results = {}
    with tempfile.TemporaryDirectory(prefix="overhead-audit-") as temporary:
        root = Path(temporary)
        project = root / "projects" / "audit-fixture"
        project.mkdir(parents=True)
        goal = project / "PROJECT_GOAL.md"
        goal.write_text("# Goal\n\nProduce a checked summary of the supplied evidence.\n",
                        encoding="utf-8")
        memory = project / "RESEARCH_STATE.md"
        memory.write_text("# Memory\n\nNo decisions yet.\n", encoding="utf-8")
        o.ROOT = root
        o.PROJECT_STATE = project / "project_state.json"
        o.RESEARCH_STATE = memory
        o.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
        o.ACTIVE_PROJECT = {"project_id": "audit-fixture", "project_root": project}
        o.stamp = lambda: FIXED_TIME
        # Fixed nonce in this isolated module only; production randomness unchanged.
        class FixedUUID:
            hex = "0" * 32
        from unittest.mock import patch
        state = {
            "schema_version": 4, "project_id": "audit-fixture",
            "project_type": "GENERAL", "profile": "GENERAL",
            "status": "SUPERVISOR_TURN", "phase": "GENERAL",
            "goal_file": "PROJECT_GOAL.md",
            "goal_anchor": {"schema_version": 1, "goal_path": "PROJECT_GOAL.md",
                            "goal_sha256": hashlib.sha256(goal.read_bytes()).hexdigest(),
                            "bound_at": FIXED_TIME, "provenance": "bootstrap"},
            "current_task": None, "next_message_id": 700100,
            "final_verification": {"required": True, "status": "NOT_STARTED",
                                   "policy_id": "GENERAL_FV_V1", "policy_version": 1},
            "last_supervisor_decision": None, "decision_history": [],
        }
        for scenario in ("bootstrap", "result", "steer", "history_tail"):
            reason, event, turn = "ORCHESTRATOR_START", {}, None
            if scenario != "bootstrap":
                reason = "EXECUTOR_RESULT_READY"
                # Deliberately tests the root fallback; authoritative receipt branch
                # is traced separately in the audit, not represented as a live run.
                o.SUPERVISOR_BRIEF.write_text(
                    '```json\n{"STATUS":"COMPLETED","KEY_FINDINGS":["Summary checked."]}\n```\n',
                    encoding="utf-8")
                event = {"type": reason, "message_id": 700100}
            if scenario == "steer":
                turn = {"interventions": [{"intervention_id": "audit-steer",
                        "mode": "STEER", "instruction_text": "Prioritize source quality."}]}
            if scenario == "history_tail":
                state["decision_history"] = [{"reason": "Earlier decision. " * 100}] * 20
                state["decision_history"].append({"reason": "LATEST_DECISION_SENTINEL"})
            o.PROJECT_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
            with patch.object(o.uuid, "uuid4", return_value=FixedUUID()):
                prompt = o.build_codex_prompt(reason, event, state, control_turn=turn)
            prompt = prompt.replace(str(root), ROOT_LABEL).replace("\\", "/")
            sections = prompt_sections(prompt)
            runtime_chars = sum(sections[key]["characters"] for key in (
                "runtime_contract", "goal_anchor", "scope", "wrapper_and_conditional"))
            results[scenario] = {"total": measure(prompt), "sections": sections,
                "runtime_instruction_surface_characters": runtime_chars,
                "runtime_instruction_surface_percent": round(100 * runtime_chars / len(prompt), 2),
                "state_source_characters": len(read(o.PROJECT_STATE)),
                "latest_history_sentinel_visible": "LATEST_DECISION_SENTINEL" in prompt,
                "steer_text_copies": prompt.count("Prioritize source quality.")}
    return results


def historical_durations(directory: Path) -> dict:
    """Read only this one shipped snapshot; never follow paths inside its records."""
    turns = []
    for path in sorted(directory.glob("*.json")):
        doc = json.loads(read(path))
        duration = doc.get("duration_seconds")
        if type(duration) not in (int, float) or duration < 0:
            continue
        turns.append({"turn_file": path.name, "seconds": duration,
                      "committed": doc.get("outcome", {}).get("committed"),
                      "candidate_validation_failed": doc.get("outcome", {}).get(
                          "candidate_validation_failed")})
    values = [row["seconds"] for row in turns]
    return {"source": SNAPSHOT.as_posix(), "sample": "historical pre-fix snapshot",
            "turns": turns, "count": len(values),
            "sum_seconds": round(sum(values), 2),
            "median_seconds": statistics.median(values) if values else None,
            "min_seconds": min(values) if values else None,
            "max_seconds": max(values) if values else None,
            "protocol_attributable_seconds": None}


def report() -> dict:
    paths = ["control/CODEX_SUPERVISOR_RUNTIME.md",
             "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md",
             "control/EXECUTOR_TASK_TEMPLATE.md", "docs/STALE_WORKER_FENCING.md",
             "control/FINAL_VERIFICATION_POLICY.md", "handoff/PROTOCOL.md"]
    paths += [path.relative_to(REPO).as_posix() for path in sorted(
        (REPO / "profiles").glob("*/*GUIDANCE.md"))]
    sources = {name: measure(read(REPO / name)) for name in paths}
    template = read(REPO / "control/EXECUTOR_TASK_TEMPLATE.md")
    payload = json.loads(re.findall(r"```json\s*\n(.*?)\n```", template, re.S)[0])
    automation = read(REPO / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md").replace(
        "<RUNTIME_ROOT>", ROOT_LABEL)
    return {"schema": "INTELLIGENCE-OVERHEAD-OFFLINE-V1",
            "measurement": "LF-normalized Unicode characters and UTF-8 bytes; no token estimates",
            "scope": "Repository sources and synthetic builder inputs; host prompts/tool history unknown",
            "provider_tokens": None, "executor_latency_seconds": None,
            "sources": sources, "executor_bound_automation": measure(automation),
            "executor_template_payload": wire_metrics(payload),
            "supervisor_synthetic": synthetic_prompts(),
            "historical_supervisor_durations": historical_durations(REPO / SNAPSHOT)}


if __name__ == "__main__":
    print(json.dumps(report(), ensure_ascii=False, indent=2))
