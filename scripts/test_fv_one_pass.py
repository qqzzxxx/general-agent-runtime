"""Dogfood Fix 03: production decision -> claim -> commit -> consume -> acceptance."""
import contextlib
import copy
import io
import json
import shutil
import unittest
from unittest import mock

import executor_claim as claim
import executor_completion as completion
import final_verification_contract as contract
import supervisor_control as sc
import test_supervisor_control as fixtures
import test_fv_identity_binding_fix as fv_fixtures


class FVOnePassTests(unittest.TestCase):
    def setUp(self):
        self.h = fixtures.SupervisorControlTests()
        self.h.setUp()
        self.addCleanup(self.h.tearDown)
        self.root = self.h.root
        shutil.copytree(fixtures.REPO / "profiles", self.root / "profiles")
        self.o = self.h.configured_orchestrator()
        self.o.PROFILES_DIR = self.root / "profiles"
        self.task = self.h.task
        self.task["ISSUED_AT"] = self.o.stamp()
        self.task.update(TASK_KIND="FINAL_VERIFICATION", FINAL_VERIFICATION_REQUEST={
            "CRITICAL_CLAIMS": copy.deepcopy(fv_fixtures.CLAIMS)})
        self.h.state.update(profile="GENERAL", final_verification={"required": True, "status": "NOT_STARTED"})
        self.h.save_state()

    def decide(self):
        turn = sc.begin_supervisor_turn(self.root, self.h.PROJECT)
        state = self.h.read_state()
        decision = {"decision": "FINAL_VERIFICATION", "reason": "Verify the four decision-critical claims"}
        state["decision_history"].append(decision)
        state.update(last_supervisor_decision=decision, status="WAITING_EXECUTOR",
                     current_task={k: self.task[k] for k in sc.IDENTITY_KEYS})
        self.h._json(self.o.PROJECT_STATE, state)
        self.o.TO_ZCODE.write_bytes(fixtures.wire(self.task))
        retry = sc.finish_supervisor_turn(self.root, turn, processed=True,
                                         candidate_validator=self.o.validate_supervisor_candidate_snapshot)
        self.assertFalse(retry, sc.load_control(self.root).get("last_supervisor_turn_result"))
        state = self.h.read_state()
        self.assertEqual(state["final_verification"]["status"], "PENDING")
        runtime = self.h.read_runtime()
        self.o.register_dispatched_task(runtime, state)
        self.runtime = runtime
        self.prepared = sc._parse_dispatch_bytes(self.o.TO_ZCODE.read_bytes())
        self.assertNotIn("FINAL_VERIFICATION_REQUEST", self.prepared)
        return state

    def receipt(self, overall="PASS"):
        legacy = fv_fixtures.FVIdentityBindingTests.receipt(self, self.task, overall)
        results = legacy.pop("FINAL_VERIFICATION")
        results.pop("POLICY_VERSION")
        results.pop("CLAIMS_HASH")
        legacy["FINAL_VERIFICATION_RESULTS"] = results
        return legacy

    def stage(self, receipt):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = claim.acquire(self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertEqual(code, claim.EXIT_ACQUIRED)
        self.token = output.getvalue().split("claim_token=")[1].strip()
        self.staging = self.h.project / "completion_staging" / "verification"
        self.staging.mkdir(parents=True)
        self.payload = {"COMPLETION_STAGING_SCHEMA_VERSION": 1,
                        **{k: self.task[k] for k in sc.IDENTITY_KEYS},
                        "PROJECT_ID": self.h.PROJECT, "STATUS": "STAGING_READY",
                        "CREATED_AT": self.o.stamp(), "RECEIPT": receipt}
        self.h._json(self.staging / "staging.json", self.payload)

    def commit(self):
        self.assertEqual(completion.commit(self.root, self.staging, claim_token=self.token), 0)
        return completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]

    def accept(self, entry):
        state = self.h.read_state()
        state.update(status="COMPLETE", current_task=None)
        state["final_verification"].update(status="PASS", verification_message_id=self.task["MESSAGE_ID"],
                                          verification_receipt_sha256=entry["BRIEF_SHA256"],
                                          verified_at=self.o.stamp())
        return self.o.final_verification_terminal_check(self.runtime, state)

    def test_new_project_one_round_no_pending_receipt_or_binding_repair(self):
        self.decide()
        authored = self.receipt()
        self.stage(authored)
        entry = self.commit()
        self.assertEqual(entry["RECEIPT"]["FINAL_VERIFICATION_RESULTS"], authored["FINAL_VERIFICATION_RESULTS"])
        self.assertEqual(entry["RECEIPT"]["FINAL_VERIFICATION"]["TASK_IDENTITY"],
                         {k: self.task[k] for k in sc.IDENTITY_KEYS})
        # Reproduce the real gate-less current_task with an FV marker that used
        # to win over the authoritative authorization gate.
        state = self.h.read_state()
        state["current_task"]["TASK_KIND"] = "FINAL_VERIFICATION"
        self.h._json(self.o.PROJECT_STATE, state)
        consumed, event = self.o.consume_executor_receipt(self.runtime)
        self.assertTrue(consumed)
        self.assertTrue(event["final_verification"]["mechanical_pass"])
        self.assertEqual(self.accept(entry), (True, "PASS"))
        self.assertEqual(self.h.read_state()["status"], "WAITING_EXECUTOR")  # Supervisor owns COMPLETE
        self.assertEqual(len(sc.list_dispatches(self.root, self.h.PROJECT)), 1)
        self.assertEqual(len(completion.lookup_entries(self.root, self.task["MESSAGE_ID"])), 1)
        self.assertEqual(self.runtime["last_dispatched_message_id"], self.task["MESSAGE_ID"])
        replay = self.o.replay_consumed_receipt_event(self.runtime, state["current_task"], state)
        self.assertTrue(replay["final_verification"]["mechanical_pass"])
        self.assertTrue(completion.entry_hashes_intact(completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]))
        # A separate Supervisor decision accepts the same receipt, with no Executor round.
        turn = sc.begin_supervisor_turn(self.root, self.h.PROJECT)
        state = self.h.read_state()
        decision = {"decision": "FINAL_ACCEPTANCE", "reason": "Accept the exact consumed verification"}
        state["decision_history"].append(decision)
        state.update(last_supervisor_decision=decision, status="COMPLETE", current_task=None)
        state["final_verification"].update(status="PASS", verification_message_id=self.task["MESSAGE_ID"],
                                          verification_receipt_sha256=entry["BRIEF_SHA256"], verified_at=self.o.stamp())
        state, blocked = self.o.enforce_terminal_verification_gate(self.runtime, state, "one-pass-test")
        self.assertIsNone(blocked)
        self.h._json(self.o.PROJECT_STATE, state)
        self.assertFalse(sc.finish_supervisor_turn(self.root, turn, processed=True))
        self.assertEqual(self.h.read_state()["status"], "COMPLETE")
        self.assertEqual(len(sc.list_dispatches(self.root, self.h.PROJECT)), 1)

    def test_old_failure_chain_is_reproduced_without_repairing_history(self):
        o = self.o
        task = fv_fixtures.FVIdentityBindingTests.task(self, self.task["MESSAGE_ID"])
        state = fv_fixtures.FVIdentityBindingTests.state_for(self, task, "PASS")
        with self.assertRaisesRegex(RuntimeError, "PENDING or REVERIFY"):
            o.validate_final_verification_dispatch(state, task)
        policy, _ = o._resolve_fv_policy_for_state(state)
        brief = fv_fixtures.FVIdentityBindingTests.receipt(self, task)
        absent = copy.deepcopy(brief)
        absent.pop("FINAL_VERIFICATION")
        self.assertIn("Receipt missing FINAL_VERIFICATION object", o.evaluate_final_verification_receipt(task, absent, policy)["issues"])
        gateless = {k: task[k] for k in sc.IDENTITY_KEYS} | {"TASK_KIND": "FINAL_VERIFICATION"}
        failed = o.evaluate_final_verification_receipt(gateless, brief, policy)
        self.assertFalse(failed["mechanical_pass"])
        self.assertEqual(failed["claims_hash"], "")
        self.assertTrue(o.evaluate_final_verification_receipt(task, brief, policy)["mechanical_pass"])

    def test_production_path_reproduces_three_round_chain_with_old_boundary_behavior(self):
        observed = []
        for round_index in range(3):
            self.task.update(MESSAGE_ID=700120 + round_index, NONCE=f"old-boundary-{round_index}")
            self.decide()
            legacy = fv_fixtures.FVIdentityBindingTests.receipt(self, self.task)
            if round_index == 0:
                legacy.pop("FINAL_VERIFICATION")  # 700102: report passed, envelope missing
            self.stage(legacy)
            # Emulate precisely the two replaced boundaries, keeping actual
            # decision commit, authorization archive, claim/fence, ledger, consume.
            with mock.patch.object(contract, "construct_receipt", side_effect=lambda root, auth, receipt: receipt):
                self.commit()
            state = self.h.read_state()
            state["current_task"]["TASK_KIND"] = "FINAL_VERIFICATION"
            if round_index == 2:
                state["current_task"]["FINAL_VERIFICATION_GATE"] = {
                    k: v for k, v in self.prepared["FINAL_VERIFICATION_GATE"].items()
                    if k not in {"CONTRACT_VERSION", "POLICY_SNAPSHOT", "POLICY_SHA256"}}
            self.h._json(self.o.PROJECT_STATE, state)
            policy, _ = self.o._resolve_fv_policy_for_state(state)
            def old_consumer(runtime, current_state, receipt):
                return self.o.evaluate_final_verification_receipt(current_state["current_task"], receipt, policy)
            with mock.patch.object(self.o, "evaluate_authorized_final_verification", side_effect=old_consumer):
                _, event = self.o.consume_executor_receipt(self.runtime)
            observed.append(event["final_verification"]["mechanical_pass"])
        self.assertEqual(observed, [False, False, True])
        self.assertEqual(len(sc.list_dispatches(self.root, self.h.PROJECT)), 3)

    def test_fail_and_inconclusive_are_committed_faithfully(self):
        self.decide()
        for overall in ("FAIL", "INCONCLUSIVE"):
            with self.subTest(overall=overall):
                receipt = self.receipt(overall)
                receipt["FINAL_VERIFICATION_RESULTS"]["CLAIM_RESULTS"][0]["status"] = "CONTRADICTED"
                wrapped = contract.construct_receipt(self.root, self.runtime["authorized_dispatch"], receipt)
                result = self.o.evaluate_authorized_final_verification(self.runtime, self.h.read_state(), wrapped)
                self.assertEqual(result["overall_status"], overall)
                self.assertFalse(result["mechanical_pass"])
                self.assertEqual(wrapped["FINAL_VERIFICATION"]["CLAIM_RESULTS"], receipt["FINAL_VERIFICATION_RESULTS"]["CLAIM_RESULTS"])
        self.stage(receipt)
        entry = self.commit()
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertEqual(event["final_verification"]["overall_status"], "INCONCLUSIVE")
        self.assertFalse(self.accept(entry)[0])

    def test_negative_per_claim_result_cannot_be_promoted_by_overall_pass(self):
        self.decide()
        receipt = self.receipt()
        receipt["FINAL_VERIFICATION_RESULTS"]["CLAIM_RESULTS"][0]["status"] = "CONTRADICTED"
        receipt["FINAL_VERIFICATION_RESULTS"]["CLAIM_RESULTS"][0]["checks"]["deliverable_check"] = "FAIL"
        self.stage(receipt)
        entry = self.commit()
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertEqual(entry["RECEIPT"]["FINAL_VERIFICATION"]["OVERALL_STATUS"], "PASS")
        self.assertFalse(event["final_verification"]["mechanical_pass"])
        self.assertFalse(self.accept(entry)[0])

    def test_ordinary_completion_receipt_is_unchanged(self):
        self.task.pop("FINAL_VERIFICATION_REQUEST")
        self.task.pop("TASK_KIND")
        self.h.commit_decision(task=self.task)
        self.runtime = self.h.read_runtime()
        self.o.register_dispatched_task(self.runtime, self.h.read_state())
        receipt = {k: self.task[k] for k in sc.IDENTITY_KEYS} | {"STATUS": "COMPLETED", "SUMMARY": "ordinary result"}
        self.stage(receipt)
        entry = self.commit()
        self.assertEqual(entry["RECEIPT"], receipt)
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertNotIn("final_verification", event)

    def test_missing_malformed_results_reject_before_commit_same_claim_can_correct(self):
        self.decide()
        self.stage(self.receipt())
        original = copy.deepcopy(self.payload)
        variants = [{}, {"OVERALL_STATUS": "PASS", "CLAIM_RESULTS": []},
                    {"OVERALL_STATUS": "PASS", "CLAIM_RESULTS": "bad"}]
        for results in variants:
            self.payload["RECEIPT"]["FINAL_VERIFICATION_RESULTS"] = results
            self.h._json(self.staging / "staging.json", self.payload)
            with self.assertRaises(completion.CompletionError):
                self.commit()
            self.assertEqual(completion.lookup_entries(self.root, self.task["MESSAGE_ID"]), [])
        self.h._json(self.staging / "staging.json", original)
        self.commit()

    def test_metadata_claim_identity_tampering_fails_closed(self):
        self.decide()
        auth = self.runtime["authorized_dispatch"]
        receipt = self.receipt()
        for key, value in (("POLICY_ID", "wrong"), ("POLICY_VERSION", "bad"), ("CLAIMS_HASH", "0" * 64),
                           ("POLICY_SHA256", "0" * 64), ("CRITICAL_CLAIMS", []), ("CONTRACT_VERSION", 2)):
            with self.subTest(key=key):
                damaged = copy.deepcopy(auth)
                damaged["FINAL_VERIFICATION_GATE"][key] = value
                with self.assertRaises(RuntimeError):
                    contract.construct_receipt(self.root, damaged, receipt)
        wrapped = contract.construct_receipt(self.root, auth, receipt)
        for key in ("POLICY_ID", "POLICY_VERSION", "CLAIMS_HASH", "POLICY_SHA256", "TASK_IDENTITY", "DISPATCH_SHA256"):
            bad = copy.deepcopy(wrapped)
            bad["FINAL_VERIFICATION"].pop(key)
            self.assertFalse(self.o.evaluate_authorized_final_verification(self.runtime, self.h.read_state(), bad)["mechanical_pass"])
        stale = copy.deepcopy(wrapped)
        stale["NONCE"] = "stale"
        self.assertFalse(self.o.evaluate_authorized_final_verification(self.runtime, self.h.read_state(), stale)["mechanical_pass"])
        for results in (None, {"OVERALL_STATUS": "PASS", "CLAIM_RESULTS": receipt["FINAL_VERIFICATION_RESULTS"]["CLAIM_RESULTS"] * 2},
                        receipt["FINAL_VERIFICATION_RESULTS"] | {"CLAIMS_HASH": "forged"}):
            with self.assertRaises(RuntimeError):
                contract.construct_receipt(self.root, auth, receipt | {"FINAL_VERIFICATION_RESULTS": results})

    def test_policy_is_pinned_across_profile_changes(self):
        self.decide()
        self.stage(self.receipt())
        policy_path = self.root / "profiles" / "GENERAL" / "FINAL_VERIFICATION_POLICY.json"
        policy_path.write_text("{}", encoding="utf-8")
        entry = self.commit()
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertTrue(event["final_verification"]["mechanical_pass"])
        self.assertTrue(completion.entry_hashes_intact(entry))

    def test_archive_tamper_blocks_commit(self):
        self.decide()
        self.stage(self.receipt())
        path = self.root / self.runtime["authorized_dispatch"]["SUPERVISOR_DISPATCH_ARCHIVE"]["archive_file"]
        path.write_bytes(path.read_bytes() + b"tampered")
        with self.assertRaises(completion.CompletionError):
            self.commit()
        self.assertEqual(completion.lookup_entries(self.root, self.task["MESSAGE_ID"]), [])

    def test_tampered_consumed_receipt_cannot_replay_pass(self):
        state = self.decide()
        self.stage(self.receipt())
        entry = self.commit()
        self.o.consume_executor_receipt(self.runtime)
        entry = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        entry["RECEIPT"]["FINAL_VERIFICATION"]["CLAIM_RESULTS"][0]["status"] = "CONTRADICTED"
        self.h._json(completion.entry_path(self.root, entry["COMMIT_ID"]), entry)
        self.assertIsNone(self.o.replay_consumed_receipt_event(self.runtime, state["current_task"], state))

    def test_prepare_recovery_after_state_write_uses_same_request_and_identity(self):
        state = self.h.read_state() | {"status": "WAITING_EXECUTOR", "last_supervisor_decision": {"decision": "FINAL_VERIFICATION"}}
        first_state, first_task = contract.prepare(self.root, state, self.task)
        recovered_state, recovered_task = contract.prepare(self.root, first_state, self.task)
        self.assertEqual((first_state, first_task), (recovered_state, recovered_task))

    def test_preparation_rejects_corrupt_state_and_stop(self):
        state = self.h.read_state() | {"status": "WAITING_EXECUTOR", "last_supervisor_decision": {"decision": "FINAL_VERIFICATION"}}
        for malformed in (False, 0, [], ""):
            with self.assertRaises(RuntimeError):
                contract.prepare(self.root, state | {"final_verification": malformed}, self.task)
        for field, value in (("claims_hash", "bad"), ("policy_id", "wrong"), ("policy_version", True),
                             ("claims_hash", []), ("critical_claims", [{"claim_id": "wrong"}])):
            bad = copy.deepcopy(state)
            bad["final_verification"][field] = value
            with self.assertRaises(RuntimeError):
                contract.prepare(self.root, bad, self.task)
        (self.root / "control" / "STOP").touch()
        with self.assertRaisesRegex(RuntimeError, "STOP/HUMAN_REVIEW"):
            contract.prepare(self.root, state, self.task)

    def test_human_review_and_retired_identity_block_preparation(self):
        state = self.h.read_state() | {"status": "WAITING_EXECUTOR", "last_supervisor_decision": {"decision": "FINAL_VERIFICATION"}}
        review = self.root / "control" / "HUMAN_REVIEW"
        review.touch()
        with self.assertRaisesRegex(RuntimeError, "STOP/HUMAN_REVIEW"):
            contract.prepare(self.root, state, self.task)
        review.unlink()
        runtime = self.h.read_runtime()
        runtime["retired_message_ids"] = [self.task["MESSAGE_ID"]]
        self.h._json(self.o.RUNTIME_STATE, runtime)
        with self.assertRaisesRegex(RuntimeError, "fresh, unclaimed"):
            contract.prepare(self.root, state, self.task)


if __name__ == "__main__":
    unittest.main()
