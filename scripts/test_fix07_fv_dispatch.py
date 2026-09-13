"""Fix 07: policy binding, pre-authorization recovery and review projection."""
import copy
import json
import unittest
from unittest import mock

import final_verification_contract as contract
import supervisor_control as sc
import executor_completion as completion
import test_fv_one_pass as one_pass
import test_supervisor_control as fixtures
import web_console_control as console


class DispatchContractTests(unittest.TestCase):
    def setUp(self):
        self.f = one_pass.FVOnePassTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.h, self.o, self.root = self.f.h, self.f.o, self.f.root

    def state(self):
        return self.h.read_state() | {
            "status": "WAITING_EXECUTOR",
            "last_supervisor_decision": {"decision": "FINAL_VERIFICATION"}}

    def policy(self, mode):
        path = self.root / "profiles/GENERAL/FINAL_VERIFICATION_POLICY.json"
        policy = json.loads(path.read_text(encoding="utf-8"))
        policy["execution_mode"] = mode
        self.h._json(path, policy)

    def test_policy_binds_modes_and_preserves_all_substantive_intent(self):
        for mode in ("LIVE_READ_ONLY", "SANDBOX_DESTRUCTIVE"):
            with self.subTest(mode=mode):
                self.policy(mode)
                task = copy.deepcopy(self.f.task)
                task["OBJECTIVE"] = "Adversarial independent review of the revised evidence"
                original = copy.deepcopy(task)
                state, prepared = contract.prepare(self.root, self.state(), task)
                self.assertEqual(task, original)
                self.assertEqual(prepared["FINAL_VERIFICATION_GATE"]["EXECUTION_MODE"], mode)
                for key in original.keys() - {"FINAL_VERIFICATION_REQUEST", "EXECUTOR_PROTOCOL"}:
                    self.assertEqual(prepared[key], original[key])
                self.assertEqual(state["final_verification"]["critical_claims"],
                                 original["FINAL_VERIFICATION_REQUEST"]["CRITICAL_CLAIMS"])
                self.o.validate_final_verification_dispatch(state, prepared)

    def test_missing_policy_mode_is_read_only_and_no_model_enum_is_required(self):
        state, task = contract.prepare(self.root, self.state(), self.f.task)
        self.assertEqual(task["FINAL_VERIFICATION_GATE"]["EXECUTION_MODE"], "LIVE_READ_ONLY")
        self.assertEqual(state["final_verification"]["status"], "PENDING")

    def test_invalid_and_conflicting_model_values_fail_closed_without_writes(self):
        before = self.o.PROJECT_STATE.read_bytes()
        for mode in ("ADVERSARIAL_INDEPENDENT_REVIEW", "REVERIFY", "SANDBOX_DESTRUCTIVE",
                     "live_read_only", "", None, False, [], {}):
            with self.subTest(mode=mode):
                task = copy.deepcopy(self.f.task)
                task["FINAL_VERIFICATION_REQUEST"]["EXECUTION_MODE"] = mode
                with self.assertRaisesRegex(RuntimeError, "omit EXECUTION_MODE"):
                    contract.prepare(self.root, self.state(), task)
                self.assertEqual(self.o.PROJECT_STATE.read_bytes(), before)
                self.assertFalse(sc.list_dispatches(self.root, self.h.PROJECT))
        self.f.task["FINAL_VERIFICATION_REQUEST"]["EXECUTION_MODE"] = "LIVE_READ_ONLY"
        self.f.decide()  # retained exact compatibility assertion

    def test_invalid_policy_mode_and_gate_override_fail_closed(self):
        for mode in (None, "", "REVERIFY", False, []):
            self.policy(mode)
            with self.assertRaisesRegex(RuntimeError, "policy execution_mode"):
                contract.prepare(self.root, self.state(), self.f.task)
        self.policy("LIVE_READ_ONLY")
        state, task = contract.prepare(self.root, self.state(), self.f.task)
        task["FINAL_VERIFICATION_GATE"]["EXECUTION_MODE"] = "SANDBOX_DESTRUCTIVE"
        with self.assertRaisesRegex(RuntimeError, "Runtime policy binding"):
            self.o.validate_final_verification_dispatch(state, task)

    def test_destructive_policy_still_requires_sandbox_evidence(self):
        self.policy("SANDBOX_DESTRUCTIVE")
        self.f.decide()
        self.f.stage(self.f.receipt())
        entry = self.f.commit()
        self.assertEqual(entry["RECEIPT"]["FINAL_VERIFICATION"]["EXECUTION_MODE"], "SANDBOX_DESTRUCTIVE")
        _, event = self.o.consume_executor_receipt(self.f.runtime)
        self.assertFalse(event["final_verification"]["mechanical_pass"])
        self.assertTrue(any("SANDBOX" in issue for issue in event["final_verification"]["issues"]))

    def run_recovery(self, second_mode):
        calls = []
        def model(*args, **kwargs):
            calls.append(len(calls) + 1)
            state = self.o.read_project_state()
            task = copy.deepcopy(self.f.task)
            task.update(MESSAGE_ID=700800 + len(calls), NONCE=f"fix07-{len(calls)}")
            mode = "ADVERSARIAL_INDEPENDENT_REVIEW" if len(calls) == 1 else second_mode
            if mode is not None:
                task["FINAL_VERIFICATION_REQUEST"]["EXECUTION_MODE"] = mode
            if len(calls) == 2:
                self.assertIn("omit EXECUTION_MODE", state["dispatch_repair"]["error"])
            decision = {"decision": "FINAL_VERIFICATION", "reason": "Verify revised claims"}
            state["decision_history"].append(decision)
            state.update(status="WAITING_EXECUTOR", last_supervisor_decision=decision,
                         current_task={k: task[k] for k in sc.IDENTITY_KEYS})
            self.h._json(self.o.PROJECT_STATE, state)
            self.o.TO_ZCODE.write_bytes(fixtures.wire(task))
            return mock.Mock(returncode=0)
        runtime = self.h.read_runtime()
        with mock.patch.object(self.o, "goal_anchor_gate", return_value=True), \
             mock.patch.object(self.o, "build_codex_prompt", return_value="fixture"), \
             mock.patch.object(self.o, "find_codex", return_value="codex"), \
             mock.patch.object(self.o.subprocess, "run", side_effect=model), \
             mock.patch.object(self.o, "emit_user_notification", return_value=False):
            for _ in range(2):
                self.o.invoke_codex(runtime, "SUPERVISOR_TURN")
        self.assertEqual(calls, [1, 2])
        records = [json.loads(p.read_text(encoding="utf-8"))
                   for p in (self.root / "control/supervisor_turns").glob("*.json")]
        failed = [r for r in records if r["outcome"]["candidate_validation_failed"]]
        self.assertIn("FV dispatch preparation", failed[0]["outcome"]["error"])
        return failed

    def test_bounded_recovery_can_omit_enum_and_bind_one_valid_candidate(self):
        failed = self.run_recovery(None)
        self.assertEqual(len(failed), 1)
        self.assertEqual(self.h.read_state()["status"], "WAITING_EXECUTOR")
        runtime = self.o.load_runtime()
        self.assertEqual(runtime["authorized_dispatch"]["FINAL_VERIFICATION_GATE"]["EXECUTION_MODE"], "LIVE_READ_ONLY")
        self.assertEqual(len(sc.list_dispatches(self.root, self.h.PROJECT)), 1)
        self.assertEqual(len(list((self.root / "handoff/executor_claims").glob("*.claim"))), 0)

    def test_repeated_invalid_model_input_exhausts_and_console_reason_is_available(self):
        failed = self.run_recovery("REVERIFY")
        self.assertEqual(len(failed), 2)
        self.assertEqual(self.h.read_state()["status"], "HUMAN_REVIEW")
        self.assertFalse(self.o.TO_ZCODE.exists())
        self.assertFalse(sc.list_dispatches(self.root, self.h.PROJECT))
        self.assertFalse((self.root / "control/HUMAN_REVIEW").exists())
        status = sc.current_status(self.root)
        review = console.human_review_presentation(status, None, truncated=False)
        self.assertTrue(review["reason"]["available"])
        self.assertTrue(review["reason"]["requires_human_action"])
        self.assertEqual(review["reason"]["stage"], "FINAL_VERIFICATION_DISPATCH")
        self.assertIn("授权前", review["reason"]["summary"])
        self.assertIn("REVERIFY", review["reason"]["raw_error"])
        self.assertFalse(review["authorized_task"]["present"])

    def test_completion_then_one_substantive_fv_then_final_acceptance_complete(self):
        # First commit/consume ordinary business work using the same real helpers.
        fv_task = copy.deepcopy(self.f.task)
        self.f.task.pop("TASK_KIND")
        self.f.task.pop("FINAL_VERIFICATION_REQUEST")
        self.h.commit_decision(task=self.f.task)
        runtime = self.h.read_runtime()
        self.o.register_dispatched_task(runtime, self.h.read_state())
        ordinary = {k: self.f.task[k] for k in sc.IDENTITY_KEYS} | {"STATUS": "COMPLETED", "SUMMARY": "Business revision complete"}
        self.f.stage(ordinary)
        self.f.commit()
        consumed, _ = self.o.consume_executor_receipt(runtime)
        self.assertTrue(consumed)
        self.f.task = fv_task | {"MESSAGE_ID": fv_task["MESSAGE_ID"] + 1, "NONCE": "fix07-substantive-fv"}
        self.f.decide()
        self.f.stage(self.f.receipt(), name="fix07-verification")
        entry = self.f.commit()
        _, event = self.o.consume_executor_receipt(self.f.runtime)
        self.assertTrue(event["final_verification"]["mechanical_pass"])
        turn = sc.begin_supervisor_turn(self.root, self.h.PROJECT)
        state = self.h.read_state()
        decision = {"decision": "FINAL_ACCEPTANCE", "reason": "Accept consumed FV evidence"}
        state["decision_history"].append(decision)
        state.update(last_supervisor_decision=decision, status="COMPLETE", current_task=None)
        state["final_verification"].update(status="PASS", verification_message_id=self.f.task["MESSAGE_ID"],
            verification_receipt_sha256=entry["BRIEF_SHA256"], verified_at=self.o.stamp())
        state, blocked = self.o.enforce_terminal_verification_gate(self.f.runtime, state, "fix07-fixture")
        self.assertIsNone(blocked)
        self.h._json(self.o.PROJECT_STATE, state)
        self.assertFalse(sc.finish_supervisor_turn(self.root, turn, processed=True))
        self.assertEqual(self.h.read_state()["status"], "COMPLETE")
        with mock.patch.object(self.o, "goal_anchor_gate", return_value=True), \
             mock.patch.object(self.o, "emit_user_notification", return_value=False), \
             mock.patch.object(self.o.subprocess, "run", side_effect=AssertionError("unexpected extra Agent invocation")):
            self.assertEqual(self.o.main(), 0)
        self.assertEqual(self.o.load_runtime()["status"], "COMPLETE")
        self.assertTrue(sc.current_status(self.root)["terminal_completion"]["valid"])
        self.assertEqual(len(sc.list_dispatches(self.root, self.h.PROJECT)), 2)
        self.assertEqual(len(completion.lookup_entries(self.root, self.f.task["MESSAGE_ID"])), 1)


class ReviewProjectionTests(unittest.TestCase):
    def test_runtime_fallback_preserves_actual_diagnostic_source(self):
        reason = sc._human_review_reason({"status": "HUMAN_REVIEW"}, {
            "last_supervisor_retry_failure": {"last_error": "FV dispatch preparation: bad request", "decision_attempts": 2}})
        self.assertEqual(reason["source"], "orchestrator_runtime.last_supervisor_retry_failure")
        self.assertEqual(reason["stage"], "FINAL_VERIFICATION_DISPATCH")

    def test_plain_blocked_reason_does_not_invent_retry_exhaustion(self):
        reason = sc._human_review_reason({"status": "HUMAN_REVIEW", "blocked_reason": "Need human approval before FINAL_VERIFICATION"}, {})
        review = console.human_review_presentation({"human_review": True, "human_review_reason": reason}, None, truncated=False)
        self.assertNotIn("已用尽", review["reason"]["summary"])
        self.assertEqual(reason["source"], "project_state.blocked_reason")
        self.assertEqual(review["reason"]["raw_error"], "Need human approval before FINAL_VERIFICATION")


if __name__ == "__main__":
    unittest.main()
