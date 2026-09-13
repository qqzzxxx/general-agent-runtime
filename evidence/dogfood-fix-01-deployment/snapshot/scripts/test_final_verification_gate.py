import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator_v15", MODULE_PATH)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


class FinalVerificationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-v15-gate-")
        root = Path(self.temp.name)
        o.ROOT = root
        o.CONTROL = root / "control"
        o.LOGS = root / "logs"
        o.HANDOFF_ARCHIVE = root / "handoff" / "archive"
        o.REPORTS = root / "reports"
        o.PROJECT_STATE = o.CONTROL / "project_state.json"
        o.RUNTIME_STATE = o.CONTROL / "orchestrator_runtime.json"
        o.SUPERVISOR_RULES = o.CONTROL / "CODEX_SUPERVISOR_RUNTIME.md"
        o.INFRA_PLAN = o.CONTROL / "INFRA_TEST_PLAN.md"
        o.RESEARCH_STATE = root / "RESEARCH_STATE.md"
        o.COMMERCIAL_GOAL = o.CONTROL / "CROSS_BORDER_GOAL.md"
        o.TO_ZCODE = root / "TO_ZCODE.md"
        o.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
        o.ZCODE_DONE = root / "ZCODE_DONE.flag"
        o.ZCODE_LAST_PROCESSED = root / "ZCODE_LAST_PROCESSED.txt"
        o.STOP_FLAG = o.CONTROL / "STOP"
        o.HUMAN_REVIEW_FLAG = o.CONTROL / "HUMAN_REVIEW"
        o.LOCK_FILE = o.CONTROL / ".orchestrator.lock"
        o.CODEX_LAST_OUTPUT = root / "CODEX_LAST_OUTPUT.txt"
        o.USER_ATTENTION = o.CONTROL / "USER_ATTENTION.json"
        o.USER_STATUS_REPORT = o.REPORTS / "USER_STATUS.md"
        for p in [o.CONTROL, o.LOGS, o.HANDOFF_ARCHIVE, o.REPORTS]:
            p.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp.cleanup()

    def claims(self):
        return [
            {
                "claim_id": "C1",
                "claim": "Exact patent identifier is relevant.",
                "claim_type": "IP",
                "decision_impact": "HIGH",
                "evidence_pointers": ["patent"],
                "verification_standard": "authoritative metadata",
            },
            {
                "claim_id": "C2",
                "claim": "Unit economics recompute.",
                "claim_type": "ECONOMICS",
                "decision_impact": "HIGH",
                "evidence_pointers": ["economics"],
                "verification_standard": "mechanical recomputation",
            },
            {
                "claim_id": "C3",
                "claim": "Customer pain is independently corroborated.",
                "claim_type": "CUSTOMER_PAIN",
                "decision_impact": "MEDIUM",
                "evidence_pointers": ["reviews"],
                "verification_standard": "independent underlying sources",
            },
        ]

    def state(self, status="WAITING_EXECUTOR"):
        return {
            "status": status,
            "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V2",
            "commercial_authorized": True,
            "started_at": "2030-01-02T00:00:00+00:00",
            "current_task": None,
        }

    def task(self):
        claims = self.claims()
        h = o.canonical_claims_hash(claims)
        return {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "TASK_KIND": "FINAL_VERIFICATION",
            "MESSAGE_ID": 800001,
            "TASK_ID": "FINAL_DECISION_VERIFICATION",
            "STAGE_ID": "CONSEQUENTIAL_CLAIMS_GATE",
            "ATTEMPT": 1,
            "NONCE": "nonce-v15",
            "OBJECTIVE": "verify",
            "OUTPUTS": [],
            "FINAL_VERIFICATION_GATE": {
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": h,
                "CLAIM_COUNT": len(claims),
                "CRITICAL_CLAIMS": claims,
            },
        }

    def passing_brief(self):
        task = self.task()
        h = task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"]
        return {
            "FINAL_VERIFICATION": {
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": h,
                "OVERALL_STATUS": "PASS",
                "CLAIM_RESULTS": [
                    {
                        "claim_id": "C1",
                        "status": "SUPPORTED",
                        "checks": {
                            "authoritative_identifier_check": "PASS",
                            "authoritative_source_pointers": ["USPTO"],
                        },
                    },
                    {
                        "claim_id": "C2",
                        "status": "SUPPORTED",
                        "checks": {
                            "mechanical_recalculation": "PASS",
                            "calculation_artifact": "workspace/recalc.json",
                        },
                    },
                    {
                        "claim_id": "C3",
                        "status": "PARTIALLY_SUPPORTED",
                        "checks": {
                            "source_independence_check": "PASS",
                            "independent_underlying_sources": 2,
                        },
                    },
                ],
            }
        }

    def test_claim_count_must_be_3_to_8(self):
        ok, _ = o.validate_critical_claims(self.claims()[:2])
        self.assertFalse(ok)
        ok, _ = o.validate_critical_claims(self.claims())
        self.assertTrue(ok)

    def test_dispatch_exact_claim_hash_passes(self):
        task = self.task()
        state = self.state()
        claims = task["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"]
        state["final_verification"] = {
            "policy_version": 1,
            "required": True,
            "status": "PENDING",
            "critical_claims": claims,
            "claims_hash": o.canonical_claims_hash(claims),
        }
        o.validate_final_verification_dispatch(state, task)

    def test_dispatch_tampered_hash_fails(self):
        task = self.task()
        state = self.state()
        claims = task["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"]
        state["final_verification"] = {
            "policy_version": 1,
            "required": True,
            "status": "PENDING",
            "critical_claims": claims,
            "claims_hash": o.canonical_claims_hash(claims),
        }
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = "tampered"
        with self.assertRaises(RuntimeError):
            o.validate_final_verification_dispatch(state, task)

    def test_passing_receipt_passes_mechanical_envelope(self):
        result = o.evaluate_final_verification_receipt(self.task(), self.passing_brief())
        self.assertTrue(result["mechanical_pass"], result["issues"])

    def test_ip_without_authoritative_pointer_fails(self):
        brief = self.passing_brief()
        brief["FINAL_VERIFICATION"]["CLAIM_RESULTS"][0]["checks"]["authoritative_source_pointers"] = []
        result = o.evaluate_final_verification_receipt(self.task(), brief)
        self.assertFalse(result["mechanical_pass"])

    def test_economics_without_calculation_artifact_fails(self):
        brief = self.passing_brief()
        brief["FINAL_VERIFICATION"]["CLAIM_RESULTS"][1]["checks"]["calculation_artifact"] = ""
        result = o.evaluate_final_verification_receipt(self.task(), brief)
        self.assertFalse(result["mechanical_pass"])

    def test_pain_duplicate_underlying_source_fails(self):
        brief = self.passing_brief()
        brief["FINAL_VERIFICATION"]["CLAIM_RESULTS"][2]["checks"]["independent_underlying_sources"] = 1
        result = o.evaluate_final_verification_receipt(self.task(), brief)
        self.assertFalse(result["mechanical_pass"])

    def test_legacy_completed_run_is_grandfathered(self):
        state = self.state("COMPLETE")
        state["started_at"] = "2029-01-01T00:00:00+00:00"
        runtime = {"final_verification_enforce_after": "2030-01-01T00:00:00+00:00"}
        allowed, _ = o.final_verification_terminal_check(runtime, state)
        self.assertTrue(allowed)

    def test_future_complete_without_gate_is_blocked(self):
        state = self.state("COMPLETE")
        runtime = {
            "final_verification_enforce_after": "2030-01-01T00:00:00+00:00",
            "final_verification_gate_blocks": 0,
        }
        o.atomic_json(o.PROJECT_STATE, state)
        new_state, event = o.enforce_terminal_verification_gate(runtime, state, "test")
        self.assertEqual(new_state["status"], "SUPERVISOR_TURN")
        self.assertEqual(event["type"], "FINAL_VERIFICATION_GATE_REQUIRED")

    def test_exact_accepted_pass_allows_complete(self):
        task = self.task()
        claims = task["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"]
        h = o.canonical_claims_hash(claims)
        state = self.state("COMPLETE")
        state["final_verification"] = {
            "policy_version": 1,
            "required": True,
            "status": "PASS",
            "critical_claims": claims,
            "claims_hash": h,
            "verification_message_id": 800001,
            "verification_receipt_sha256": "abc",
        }
        runtime = {
            "final_verification_enforce_after": "2030-01-01T00:00:00+00:00",
            "last_final_verification_message_id": 800001,
            "last_final_verification_receipt_sha256": "abc",
            "last_final_verification_claims_hash": h,
            "last_final_verification_overall_status": "PASS",
            "last_final_verification_mechanical_pass": True,
            "last_consumed_message_id": 800001,
            "last_consumed_brief_sha256": "abc",
        }
        allowed, reason = o.final_verification_terminal_check(runtime, state)
        self.assertTrue(allowed, reason)

    def test_newer_executor_result_invalidates_old_pass(self):
        task = self.task()
        claims = task["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"]
        h = o.canonical_claims_hash(claims)
        state = self.state("COMPLETE")
        state["final_verification"] = {
            "policy_version": 1,
            "required": True,
            "status": "PASS",
            "critical_claims": claims,
            "claims_hash": h,
            "verification_message_id": 800001,
            "verification_receipt_sha256": "abc",
        }
        runtime = {
            "final_verification_enforce_after": "2030-01-01T00:00:00+00:00",
            "last_final_verification_message_id": 800001,
            "last_final_verification_receipt_sha256": "abc",
            "last_final_verification_claims_hash": h,
            "last_final_verification_overall_status": "PASS",
            "last_final_verification_mechanical_pass": True,
            "last_consumed_message_id": 800002,
            "last_consumed_brief_sha256": "newer",
        }
        allowed, reason = o.final_verification_terminal_check(runtime, state)
        self.assertFalse(allowed)
        self.assertIn("newer Executor receipt", reason)

    def test_reliability_fixes_still_present(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        for marker in [
            "replay_consumed_receipt_event",
            "consecutive_noop_codex_turns",
            "_lock_owner_dead",
            "dispatch_registered_at",
            "FIX-F06",
            "FIX-F18",
        ]:
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
