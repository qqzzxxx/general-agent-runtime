"""Area 1 exploration judgment: pins semantics and delivery, not full prose.

The Runtime contract presents retry mechanics as Runtime-owned facts and leaves
continuation/method choice to evidence-based Supervisor judgment. Numeric
method/stage arithmetic is gone from the model-visible surface; the mechanical
identity/retry boundary in the delegation contract is what remains authoritative.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import ordinary_dispatch as od
import supervisor_context as ctx

REPO = Path(__file__).resolve().parents[1]
RULES = REPO / "control" / "CODEX_SUPERVISOR_RUNTIME.md"


def contract_text() -> str:
    return " ".join(RULES.read_text(encoding="utf-8-sig").split())


class Area1ContractSemanticsTests(unittest.TestCase):
    def test_retry_mechanics_are_runtime_owned_facts(self):
        text = contract_text()
        self.assertIn("Runtime-enforced budget", text)
        self.assertIn("tightest budget it has had", text)
        self.assertIn("renaming does not reset it", text)

    def test_continuation_is_evidence_based_judgment(self):
        text = contract_text()
        self.assertIn("your judgment from evidence", text)
        self.assertIn("reliable new information or real progress toward the outcome", text)
        self.assertIn("redirect or stop when attempts yield neither", text)
        self.assertIn("return to a previously abandoned method", text)
        # Anti-thrash intent survives the removal of the arithmetic.
        self.assertIn("Avoid unrelated work, repetitive retries and arbitrary stage plans", text)

    def test_exploration_heuristics_are_routed_through_the_taxonomy(self):
        text = contract_text()
        self.assertIn("reasoning you own, not project constraints", text)
        self.assertIn("constraints cite a source", text)
        self.assertIn("carry it forward only while the outcome still requires it", text)
        # User requirements and Human Decisions keep authoritative-constraint form.
        self.assertIn("changes only through that authority", text)

    def test_numeric_method_and_stage_arithmetic_is_gone(self):
        text = contract_text()
        for banned in ("at most two retries", "at most three materially different methods",
                       "After three stages", "Keep any tighter existing limit"):
            self.assertNotIn(banned, text)


class Area1MechanicalBoundaryTests(unittest.TestCase):
    """Identity and retry budgets stay delegated to code, not restated as counts."""

    def test_delegation_contract_keeps_identity_and_retry_boundary(self):
        text = " ".join(od.SUPERVISOR_CONTRACT.split())
        self.assertIn("Use stable logical_task/logical_stage names for the same work", text)
        self.assertIn("Runtime assigns the attempt number", text)
        self.assertIn("max_retries (integer 0..2; default 2)", text)


class Area1DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="area1-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.goal = self.root / "PROJECT_GOAL.md"
        self.memory = self.root / "RESEARCH_STATE.md"
        self.state_path = self.root / "project_state.json"
        self.goal.write_text("Improve the console; preserve its information and actions.",
                             encoding="utf-8")
        self.memory.write_text("No previous work.", encoding="utf-8")

    def test_area1_passage_delivers_verbatim_once_per_turn(self):
        state = {"status": "SUPERVISOR_TURN", "current_task": None,
                 "decision_history": [], "next_message_id": 700100}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        prompt = ctx.build(root=self.root, reason="ORCHESTRATOR_START", event={}, state=state,
                           state_path=self.state_path, memory_path=self.memory,
                           goal_path=self.goal, rules_path=RULES, profile=None,
                           goal_anchor="", scope="", human_block="", interventions=[],
                           receipt=None, receipt_path=None, fallback_brief=None,
                           now="2026-09-17T00:00:00+00:00", nonce="fixed")
        # The contract is delivered with its hard wrap; count on the normalized form.
        flat = " ".join(prompt.split())
        self.assertEqual(flat.count("Repeating a logical stage is a retry"), 1)
        self.assertEqual(flat.count("redirect or stop when attempts yield neither"), 1)
        self.assertEqual(flat.count("reasoning you own, not project constraints"), 1)
        for banned in ("at most three materially different methods",
                       "Keep any tighter existing limit"):
            self.assertNotIn(banned, prompt)
        manifest = prompt.context_manifest
        self.assertEqual(manifest["supervisor_rules"], {"delivery": "full", "truncated": False})
        self.assertIn("SUPERVISOR RUNTIME CONTRACT", manifest["section_characters"])


if __name__ == "__main__":
    unittest.main()
