"""Area 3 task-specific restrictions: pins semantics and delivery, not full prose.

The delegation contract's optional-keys sentence is followed by a scoping
sentence: forbidden_actions/stop_conditions belong to restrictions arising from
the task's own semantics; Runtime-wide boundaries already reach the Executor and
need not be restated. Field mechanics, validation and BASE_RESTRICTIONS
prepending are unchanged (covered by test_ordinary_dispatch.py).
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
    return " ".join(od.SUPERVISOR_CONTRACT.split())


class Area3ScopingSemanticsTests(unittest.TestCase):
    def test_fields_are_scoped_to_task_semantics(self):
        text = contract_text()
        self.assertIn("Use forbidden_actions and stop_conditions for restrictions", text)
        self.assertIn("arise from this task's own semantics", text)
        self.assertIn("change how this task may pursue its outcome", text)
        self.assertIn("fixed identifiers or verification claims, output namespaces", text)

    def test_runtime_boundaries_are_released_permissively(self):
        text = contract_text()
        self.assertIn("already reach the Executor on every task", text)
        # Permission, not prohibition: restating is released, not banned.
        self.assertIn("they need not be restated here", text)
        self.assertNotIn("do not restate", text)

    def test_scoping_sentence_sits_between_keys_and_outcome_context(self):
        text = contract_text()
        keys = text.find("max_retries (integer 0..2; default 2).")
        scoping = text.find("Use forbidden_actions and stop_conditions")
        outcome = text.find("Optional outcome_context separates")
        self.assertGreater(scoping, keys)
        self.assertLess(scoping, outcome)

    def test_field_shape_and_identity_boundary_are_untouched(self):
        text = contract_text()
        self.assertEqual(text.count("Optional: forbidden_actions and stop_conditions"), 1)
        self.assertIn("Use stable logical_task/logical_stage names for the same work", text)
        self.assertIn("Runtime assigns the attempt number", text)


class Area3DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="area3-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.goal = self.root / "PROJECT_GOAL.md"
        self.memory = self.root / "RESEARCH_STATE.md"
        self.state_path = self.root / "project_state.json"
        self.goal.write_text("Extend the registry; keep identifiers stable.",
                             encoding="utf-8")
        self.memory.write_text("No previous work.", encoding="utf-8")

    def test_area3_passage_delivers_verbatim_once_per_turn(self):
        state = {"status": "SUPERVISOR_TURN", "current_task": None,
                 "decision_history": [], "next_message_id": 700100}
        self.state_path.write_text(json.dumps(state), encoding="utf-8")
        prompt = ctx.build(root=self.root, reason="ORCHESTRATOR_START", event={}, state=state,
                           state_path=self.state_path, memory_path=self.memory,
                           goal_path=self.goal, rules_path=RULES, profile=None,
                           goal_anchor="", scope="", human_block="", interventions=[],
                           receipt=None, receipt_path=None, fallback_brief=None,
                           now="2026-09-17T00:00:00+00:00", nonce="fixed")
        flat = " ".join(prompt.split())
        self.assertEqual(flat.count("arise from this task's own semantics"), 1)
        self.assertEqual(flat.count("they need not be restated here"), 1)
        self.assertEqual(flat.count("Optional: forbidden_actions and stop_conditions"), 1)
        manifest = prompt.context_manifest
        self.assertEqual(manifest["supervisor_rules"], {"delivery": "full", "truncated": False})
        self.assertIn("SUPERVISOR RUNTIME CONTRACT", manifest["section_characters"])


if __name__ == "__main__":
    unittest.main()
