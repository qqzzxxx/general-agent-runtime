"""Area 2 ambiguity/human-review: pins semantics and delivery, not full prose.

The GENERAL profile's escalation-only line was replaced by the evaluated
evidence-first wording (docs/v1.4-area2-ambiguity-human-review-evaluation.md
§2, byte-identical promotion, guidance SHA 62c1e0c7…): resolve ambiguity with
evidence, bounded investigation or experimental work; HUMAN_REVIEW for
consequential persistent ambiguity or decisions needing human authority or
judgment. The recorded coupling dependency is pinned here too: the contract's
"Unresolved consequential uncertainty needs evidence, work or Human Review"
(§Context and memory) is the load-bearing carrier of the tier — any change
that weakens it must be re-evaluated against Area 2.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import supervisor_context as ctx
import orchestrator as orch

RULES = REPO / "control" / "CODEX_SUPERVISOR_RUNTIME.md"
CONTRACT_SENTENCE = ("Unresolved consequential uncertainty needs evidence, work "
                     "or Human Review")


def guidance() -> str:
    return orch.load_profile("GENERAL")["supervisor_guidance"]


class Area2SemanticsTests(unittest.TestCase):
    def test_ambiguity_line_states_evidence_first_tiers(self):
        text = guidance()
        self.assertIn("Material ambiguity the goal does not resolve", text)
        self.assertIn("try to resolve it with evidence, bounded investigation "
                      "or experimental work", text)
        self.assertIn("choose HUMAN_REVIEW when the ambiguity is consequential "
                      "and persists", text)
        self.assertIn("decision needs human authority or judgment", text)
        self.assertIn("Do not guess.", text)

    def test_escalation_only_line_is_absent(self):
        self.assertNotIn("HUMAN_REVIEW instead of guessing", guidance())

    def test_line_is_final_core_bullet_before_fv_policy(self):
        text = guidance()
        stop = text.find("Stop when the stated completion criteria")
        ambiguity = text.find("Material ambiguity the goal does not resolve")
        fv = text.find("Final Verification (policy")
        self.assertGreater(ambiguity, stop)
        self.assertLess(ambiguity, fv)

    def test_general_is_the_only_profile_carrying_an_ambiguity_line(self):
        for profile_id in sorted(orch.SUPPORTED_PROFILES):
            text = orch.load_profile(profile_id)["supervisor_guidance"]
            if profile_id == "GENERAL":
                self.assertIn("Material ambiguity", text)
            else:
                self.assertNotIn("Material ambiguity", text,
                                 f"{profile_id} must not carry an ambiguity line")


class Area2DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="area2-delivery-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.goal = self.root / "PROJECT_GOAL.md"
        self.memory = self.root / "RESEARCH_STATE.md"
        self.state_path = self.root / "project_state.json"
        self.goal.write_text("Deliver the study; keep the evidence chain.",
                             encoding="utf-8")
        self.memory.write_text("No previous work.", encoding="utf-8")
        self.state = {"status": "SUPERVISOR_TURN", "current_task": None,
                      "decision_history": [], "next_message_id": 700100}
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")
        self.profile = orch.load_profile("GENERAL")

    def test_area2_line_delivers_verbatim_once_per_turn(self):
        prompt = ctx.build(root=self.root, reason="ORCHESTRATOR_START", event={},
                           state=self.state, state_path=self.state_path,
                           memory_path=self.memory, goal_path=self.goal,
                           rules_path=RULES, profile=self.profile,
                           goal_anchor="", scope="", human_block="",
                           interventions=[], receipt=None, receipt_path=None,
                           fallback_brief=None,
                           now="2026-09-17T00:00:00+00:00", nonce="fixed")
        flat = " ".join(prompt.split())
        self.assertIn("=== PROJECT PROFILE ===", prompt)
        self.assertEqual(flat.count("try to resolve it with evidence, bounded "
                                    "investigation or experimental work"), 1)
        self.assertEqual(flat.count("judgment. Do not guess."), 1)
        self.assertEqual(flat.count("HUMAN_REVIEW instead of guessing"), 0)
        # Coupling dependency: the contract still carries the authoritative tier.
        self.assertEqual(flat.count(CONTRACT_SENTENCE), 1)
        # Baseline coexistence: Area 1 and Area 3 passages still deliver once.
        self.assertEqual(flat.count("Exploration judgments are reasoning you own"), 1)
        self.assertEqual(flat.count("arise from this task's own semantics"), 1)
        manifest = prompt.context_manifest
        self.assertEqual(manifest["supervisor_rules"],
                         {"delivery": "full", "truncated": False})


if __name__ == "__main__":
    unittest.main()
