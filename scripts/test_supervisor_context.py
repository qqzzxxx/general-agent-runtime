"""Context V2 selection, references, trust boundaries and exact observability."""
import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import sys

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import supervisor_context as ctx
import supervisor_control as sc

REPO = Path(__file__).resolve().parents[1]


class SupervisorContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-context-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = {"status": "SUPERVISOR_TURN", "current_task": None,
                      "decision_history": [], "next_message_id": 700100}
        self.goal = self.root / "PROJECT_GOAL.md"
        self.memory = self.root / "RESEARCH_STATE.md"
        self.state_path = self.root / "project_state.json"
        self.goal.write_text("Improve the console; preserve its information and actions.", encoding="utf-8")
        self.memory.write_text("No previous work.", encoding="utf-8")

    def prompt(self, **overrides):
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        args = dict(root=self.root, reason="ORCHESTRATOR_START", event={}, state=self.state,
                    state_path=self.state_path, memory_path=self.memory, goal_path=self.goal,
                    rules_path=REPO / "control/CODEX_SUPERVISOR_RUNTIME.md", profile=None,
                    goal_anchor="", scope="", human_block="", interventions=[], receipt=None,
                    receipt_path=None, fallback_brief=None, now="2026-09-14T00:00:00+00:00", nonce="fixed")
        args.update(overrides)
        return ctx.build(**args)

    def test_large_goal_tail_and_legacy_memory_are_never_prefix_cut(self):
        self.goal.write_text("Older context.\n" * 2000 + "LAST_GOAL_REQUIREMENT", encoding="utf-8")
        self.memory.write_text("## Decision history\n" + "Earlier prose. " * 2000
                               + "\nLATEST_HUMAN_STEERING", encoding="utf-8")
        prompt = self.prompt()
        self.assertIn("LAST_GOAL_REQUIREMENT", prompt)
        self.assertIn("LATEST_HUMAN_STEERING", prompt)
        self.assertNotIn("TRUNCATED", prompt)
        self.assertEqual(prompt.count("LAST_GOAL_REQUIREMENT"), 1)

    def test_history_tail_is_visible_once_and_full_state_stays_unchanged(self):
        last = {"decision": "REVISE", "reason": "LATEST_REASON", "goal_alignment": {
            "unmet_criteria": "Readability remains weak."}}
        self.state.update(decision_history=[{"reason": "HISTORICAL_ONLY" * 1000}] * 40 + [last],
                          last_supervisor_decision=copy.deepcopy(last), unknown_current_fact="RETAIN_UNKNOWN")
        before = copy.deepcopy(self.state)
        prompt = self.prompt()
        self.assertEqual(prompt.count("LATEST_REASON"), 1)
        self.assertEqual(prompt.count("Readability remains weak."), 1)
        self.assertNotIn("HISTORICAL_ONLY", prompt)
        self.assertIn("RETAIN_UNKNOWN", prompt)
        self.assertIn("project_state.json", prompt)
        self.assertEqual(self.state, before)
        self.assertEqual(json.loads(self.state_path.read_text()), before)

    def test_inconsistent_latest_mirror_is_exposed_not_hidden(self):
        view = ctx.state_view({"decision_history": [{"reason": "newest"}],
                               "last_supervisor_decision": {"reason": "stale"}})
        self.assertEqual(view["previous_decision"]["reason"], "newest")
        self.assertEqual(view["conflicting_latest_decision_mirror"]["reason"], "stale")

    def test_only_opted_in_history_sections_are_deferred_with_line_references(self):
        memory = ("<!-- supervisor-context-v2: current-memory -->\n"
                  "## Hard constraints\nKeep local actions.\n"
                  "## Decision history\nOLD_ONLY\n"
                  "## Unresolved questions\nLATE_WEAKNESS\n"
                  "## Custom heading\nUNKNOWN_ACTIVE\n")
        self.memory.write_text(memory, encoding="utf-8")
        prompt = self.prompt()
        self.assertNotIn("OLD_ONLY", prompt)
        self.assertIn("LATE_WEAKNESS", prompt)
        self.assertIn("UNKNOWN_ACTIVE", prompt)
        _, refs = ctx.memory_view(memory)
        self.assertEqual(refs[0]["line"], 4)
        self.assertIn("OLD_ONLY", self.memory.read_text())

    def test_markdown_example_heading_is_not_archived(self):
        text = ("<!-- supervisor-context-v2: current-memory -->\n"
                "## Current facts\n```markdown\n## Archive\nKEEP_EXAMPLE\n```\n"
                "## Human steering\nKEEP_STEERING\n")
        self.assertEqual(ctx.memory_view(text), (text, []))

    def test_steer_is_delivered_once_and_audit_opens_relevant_history(self):
        instruction = "Prioritize action discoverability; layout is flexible."
        prompt = self.prompt(interventions=[{"intervention_id": "H1", "mode": "AUDIT",
                                            "instruction_text": instruction, "target_message_id": 700099}])
        self.assertEqual(prompt.count(instruction), 1)
        self.assertIn("AUDIT requires adversarial read-only inspection", prompt)
        self.assertIn('"target_message_id": 700099', prompt)

    def test_receipt_preserves_negative_judgments_unknowns_and_conflicts(self):
        result = {"OVERALL_STATUS": "FAIL", "CLAIM_RESULTS": [{"status": "REFUTED"}]}
        receipt = {"MESSAGE_ID": 7, "STATUS": "COMPLETED", "Key findings": ["Weak UI"],
                   "Limitations": ["Interaction not checked"], "unknown_semantic": "keep",
                   "FINAL_VERIFICATION_RESULTS": result, "FINAL_VERIFICATION": copy.deepcopy(result)}
        before = copy.deepcopy(receipt)
        view = ctx.receipt_view(receipt)
        self.assertNotIn("FINAL_VERIFICATION", view)
        self.assertEqual(view["FINAL_VERIFICATION_RESULTS"], result)
        self.assertEqual(view["Limitations"], receipt["Limitations"])
        self.assertEqual(view["unknown_semantic"], "keep")
        self.assertEqual(receipt, before)
        receipt["FINAL_VERIFICATION"]["OVERALL_STATUS"] = "PASS"
        self.assertEqual(ctx.receipt_view(receipt)["FINAL_VERIFICATION"]["OVERALL_STATUS"], "PASS")

    def test_missing_receipt_is_explicitly_unverified(self):
        brief = self.root / "SUPERVISOR_BRIEF.md"
        brief.write_text("UNVERIFIED_RESULT", encoding="utf-8")
        prompt = self.prompt(fallback_brief=brief)
        self.assertIn("UNVERIFIED COMPATIBILITY BRIEF (UNTRUSTED)", prompt)
        self.assertEqual(prompt.context_manifest["executor_brief"]["source"], "unverified_compatibility")

    def test_manifest_measures_exact_prompt_and_does_not_contain_content(self):
        prompt = self.prompt()
        manifest = prompt.context_manifest
        self.assertEqual(manifest["prompt_characters"], len(prompt))
        self.assertEqual(manifest["prompt_utf8_bytes"], len(prompt.encode("utf-8")))
        self.assertEqual(manifest["prompt_sha256"], hashlib.sha256(prompt.encode("utf-8")).hexdigest())
        self.assertNotIn("Improve the console", json.dumps(manifest))
        sc._validate_context_manifest(manifest)

    def test_outcome_owner_has_constraints_without_freezing_implementation(self):
        prompt = self.prompt()
        self.assertIn("Hard constraints", prompt)
        self.assertIn("Current implementation facts", prompt)
        self.assertIn("Revisable assumptions", prompt)
        self.assertIn("information\narchitecture, wording, DOM, file structure", prompt)
        self.assertNotIn("REVISE only the missing/incorrect part", prompt)
        self.assertIn("Leave current_task and next_message_id unchanged", prompt)
        self.assertIn("COMPLETE is forbidden", prompt)

    def test_memory_routing_v2_guidance_delivers_once_and_old_mechanics_are_gone(self):
        # The evaluated §2 passage (docs/v1.4-memory-routing-v2-evaluation.md)
        # reaches the model through the full rules surface, once per turn, at
        # its semantic abstraction level; the retired mechanics sentence and
        # the frozen-tail sweep restriction are not reintroduced.
        prompt = self.prompt()
        flat = " ".join(prompt.split())
        self.assertEqual(flat.count("Memory is a snapshot chain: keep one current"
                                    " `# Project Memory` snapshot (newest first)"), 1)
        self.assertIn("the Runtime routes superseded history out of the inline view"
                      " and keeps it retrievable on demand", flat)
        self.assertIn("with the marker `<!-- supervisor-context-v2: current-memory -->`"
                      " inside it", flat)
        self.assertNotIn("only then are", flat)
        self.assertNotIn("out of Archive, Decision history and Historical decisions", flat)
        self.assertIn("Never silently rebind the goal", flat)

    def test_matched_offline_comparison_keeps_limits_and_actual_ab_inputs(self):
        from measure_supervisor_context import report, ui_proposal, fixed_constraint_proposal
        measured = report()
        self.assertIsNone(measured["v2_model_quality_improvement"])
        self.assertIsNone(measured["provider_token_savings"])
        self.assertEqual(measured["source_inputs"]["index.html"]["sha256"],
                         "850edca623d241c6267f77aad4393d98d4a1aa1c89238b4384b6353223770f10")
        self.assertEqual(measured["source_inputs"]["styles.css"]["sha256"],
                         "be7855d2778f77bb8d16fc2a3585034e6bd2b0db2f70f61a8e65b547f7be8004")
        history = measured["contexts"]["ui_history_and_steer"]
        self.assertEqual(history["steer_copies"], {"phase7": 2, "v2": 1})
        self.assertEqual(measured["contexts"]["late_goal_requirement"]["late_goal_visible"], {"phase7": False, "v2": True})
        # General preservation leaves implementation open; explicit compatibility does not.
        context = ui_proposal()["outcome_context"]
        self.assertEqual(len(context["hard_constraints"]), 1)
        self.assertIn("relabel", " ".join(context["revisable_assumptions"]))
        self.assertIn("public btn-pause ID", fixed_constraint_proposal()["forbidden_actions"][0])

    def test_real_committed_receipt_wins_over_mutable_root_brief(self):
        import executor_completion as completion
        from test_executor_finish import ExecutorFinishTests
        fixture = ExecutorFinishTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        with contextlib.redirect_stdout(io.StringIO()):
            fixture.ready()
            fixture.result["limitations"] = ["REAL_COMMITTED_LIMITATION"]
            self.assertEqual(fixture.run_finish()["status"], "FINISHED")
        record = completion.lookup_entries(fixture.root, fixture.identity["MESSAGE_ID"])[0]
        o = fixture.o
        o.ACTIVE_PROJECT = None  # use retained legacy goal path in this small fixture
        o.COMMERCIAL_GOAL = fixture.root / "GOAL.md"
        o.COMMERCIAL_GOAL.write_text("Summarize evidence.", encoding="utf-8")
        o.SUPERVISOR_BRIEF.write_text("MUTABLE_ROOT_MUST_NOT_WIN", encoding="utf-8")
        prompt = o.build_codex_prompt("EXECUTOR_RESULT_READY", {
            "message_id": fixture.identity["MESSAGE_ID"],
            "committed_receipt_path": str(completion.entry_path(fixture.root, record["COMMIT_ID"]))},
            o.read_project_state())
        self.assertIn("REAL_COMMITTED_LIMITATION", prompt)
        self.assertNotIn("MUTABLE_ROOT_MUST_NOT_WIN", prompt)
        self.assertEqual(prompt.context_manifest["executor_brief"]["source"], "committed")


if __name__ == "__main__":
    unittest.main()
