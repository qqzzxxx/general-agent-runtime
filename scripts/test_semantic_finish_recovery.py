"""Phase 7: real V2 owner correction before the durable finish boundary."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import executor_completion as completion
import executor_entry as entry
import executor_fence as fence
import executor_finish as finish
import executor_inspect as inspect
import executor_work as work
import ordinary_dispatch as od
import supervisor_control as control
import test_fv_one_pass as fixtures
from test_executor_contract import semantic


class SemanticFinishRecoveryTests(unittest.TestCase):
    setUp = fixtures.FVOnePassTests.setUp
    decide = fixtures.FVOnePassTests.decide
    receipt = fixtures.FVOnePassTests.receipt
    accept = fixtures.FVOnePassTests.accept

    def start(self):
        self.task["EXECUTOR_PROTOCOL"] = list(od.EXECUTOR_PROTOCOL)
        self.task["ACCEPTANCE_CRITERIA"] = ["Verify every fixed claim honestly."]
        self.decide()
        ready = entry.enter(self.root, contract_version=2)
        self.assertEqual(ready["status"], "READY", ready)
        self.session = ready["session"]
        self.identity = completion.entry_identity(self.task)
        self.project = self.h.project
        self.candidate = Path(ready["contract"]["locations"]["work"])
        self.commit_id = completion.commit_id_for(self.task["MESSAGE_ID"], self.task["NONCE"])
        self.plan = self.root / f"handoff/executor_finishes/{self.commit_id}.json"
        self.result = semantic()
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        rows = copy.deepcopy(judgments["CLAIM_RESULTS"])
        for row in rows:
            row.update(evidence_pointers=["task-provided evidence"], auditor_note="Checked the fixed criterion.")
        self.result["verification"] = {"overall_status": "PASS", "claims": rows}
        projected = json.dumps(ready)
        for name in ("FINAL_VERIFICATION_RESULTS", "CLAIM_RESULTS", "OVERALL_STATUS", "CLAIMS_HASH"):
            self.assertNotIn(name, projected)
        return ready

    def do(self, op="finish", **kwargs):
        return work.perform(self.root, session=self.session,
                            request={"op": op, **(kwargs or ({"result": self.result} if op == "finish" else {}))})

    def cli(self, result=None):
        process = subprocess.run(
            [sys.executable, work.__file__, "--root", str(self.root), "--session", self.session],
            input=json.dumps({"op": "finish", "result": result or self.result}),
            text=True, capture_output=True, timeout=30)
        self.assertEqual(process.stderr, "")
        return json.loads(process.stdout)

    def no_effects(self):
        self.assertFalse(self.plan.exists())
        self.assertFalse((self.project / f"completion_staging/finish-{self.commit_id}").exists())
        self.assertFalse((self.root / f"handoff/executor_publications/{self.commit_id}").exists())
        self.assertFalse(completion.lookup_entries(self.root, self.task["MESSAGE_ID"]))
        self.assertFalse((self.root / "ZCODE_DONE.flag").exists())

    def test_invalid_then_corrected_same_live_owner_and_duplicate_wake(self):
        self.start()
        invalid = copy.deepcopy(self.result)
        invalid["verification"]["claims"].pop()
        rejected = self.do(result=invalid)
        self.assertEqual((rejected["status"], rejected["action"]),
                         ("INVALID_RESULT", "CORRECT_AND_RESUBMIT"), rejected)
        self.no_effects()
        claim_before = completion.load_claim(self.root, self.identity)[0]
        duplicate = entry.enter(self.root, contract_version=2)
        self.assertEqual(duplicate["status"], "DUPLICATE")
        self.assertNotIn("session", duplicate)
        resumed = entry.enter(self.root, resume_token=self.session, contract_version=2)
        self.assertTrue(resumed["contract"]["locations"]["candidate_work_open"])
        self.assertEqual(self.do("checkpoint")["status"], "OK")
        self.assertEqual(self.do("write", path="workspace/recheck.txt", text="rechecked")["status"], "OK")
        with fence.runtime_lock(self.root):
            self.assertEqual(inspect.context_locked(self.root, self.session)[1], self.identity)
        self.assertEqual(self.cli()["status"], "FINISHED")
        self.assertEqual(completion.load_claim(self.root, self.identity)[0], claim_before)
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertTrue(completion.entry_hashes_intact(record))
        self.assertEqual(record["RECEIPT"]["FINAL_VERIFICATION"]["CLAIM_RESULTS"],
                         self.result["verification"]["claims"])
        consumed, event = self.o.consume_executor_receipt(self.runtime)
        self.assertTrue(consumed)
        self.assertTrue(event["final_verification"]["mechanical_pass"])
        self.assertEqual(self.accept(record), (True, "PASS"))
        before = completion.entry_path(self.root, self.commit_id).read_bytes()
        changed = copy.deepcopy(self.result)
        changed["verification"]["overall_status"] = "FAIL"
        self.assertEqual(self.cli(changed)["action"], "STOP")
        self.assertEqual(completion.entry_path(self.root, self.commit_id).read_bytes(), before)

    def test_schema_failures_remain_correctable_without_promoting_negative_verdicts(self):
        self.start()
        variants = []
        for key, value in (("claims", []), ("overall_status", "YES"), ("POLICY_ID", "forged")):
            invalid = copy.deepcopy(self.result)
            invalid["verification"][key] = value
            variants.append(invalid)
        for key, value in (("status", "PASS"), ("checks", []), ("auditor_note", ""),
                           ("evidence_pointers", "prose"), ("MESSAGE_ID", 123)):
            invalid = copy.deepcopy(self.result)
            invalid["verification"]["claims"][0][key] = value
            variants.append(invalid)
        invalid = copy.deepcopy(self.result)
        invalid["completion"] = {"FINAL_VERIFICATION_RESULTS": self.receipt()["FINAL_VERIFICATION_RESULTS"]}
        variants.append(invalid)
        for invalid in variants:
            with self.subTest(invalid=invalid):
                response = self.do(result=invalid)
                self.assertEqual(response["action"], "CORRECT_AND_RESUBMIT", response)
                self.no_effects()
        self.result["verification"]["claims"][0]["status"] = "NOT_VERIFIABLE"
        self.result["verification"]["claims"][0]["checks"] = {}
        self.assertEqual(self.do()["status"], "FINISHED")
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertFalse(event["final_verification"]["mechanical_pass"])

    def test_old_invalid_cache_and_prevalidation_crash_recover_in_fresh_process(self):
        self.start()
        invalid = copy.deepcopy(self.result)
        invalid["verification"]["claims"] = []
        # Reproduce Phase 5's saved-but-never-validated result, with no new marker.
        (self.candidate / work.RESULT_PATH).write_text(finish.encoded(invalid), encoding="utf-8")
        with patch.object(finish, "finish", side_effect=SystemExit("before validation")):
            with self.assertRaises(SystemExit):
                self.do(result=invalid)
        self.no_effects()
        self.assertEqual(self.cli()["status"], "FINISHED")

    def test_valid_plan_crash_is_immutable_even_without_publication(self):
        self.start()
        original = completion._atomic_create
        def create(path, content):
            original(path, content)
            if path == self.plan:
                raise SystemExit("after validated plan")
        with patch.object(completion, "_atomic_create", side_effect=create):
            with self.assertRaises(SystemExit):
                self.do()
        before = self.plan.read_bytes()
        cached = (self.candidate / work.RESULT_PATH).read_bytes()
        changed = copy.deepcopy(self.result)
        changed["verification"]["overall_status"] = "FAIL"
        self.assertEqual(self.cli(changed)["action"], "STOP")
        self.assertEqual(self.do("write", path="workspace/late", text="late")["action"], "STOP")
        self.assertFalse(entry.enter(self.root, resume_token=self.session, contract_version=2)
                         ["contract"]["locations"]["candidate_work_open"])
        with fence.runtime_lock(self.root), self.assertRaisesRegex(ValueError, "inspection is closed"):
            inspect.context_locked(self.root, self.session)
        self.assertEqual((self.candidate / work.RESULT_PATH).read_bytes(), cached)
        self.assertEqual(self.cli()["status"], "FINISHED")
        self.assertEqual(self.plan.read_bytes(), before)
        self.assertEqual(len(completion.lookup_entries(self.root, self.task["MESSAGE_ID"])), 1)

    def test_missing_artifact_then_correction_and_publication_crash_replay(self):
        ready = self.start()
        relative = ready["contract"]["locations"]["publication_roots"][0] + "/check.txt"
        self.result["artifacts"] = [{"path": relative, "role": "evidence"}]
        self.assertEqual(self.do()["action"], "CORRECT_AND_RESUBMIT")
        self.no_effects()
        self.assertEqual(self.do("write", path=relative, text="verified evidence")["status"], "OK")
        original = fence.publish
        def publish(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("after publication")
        with patch.object(fence, "publish", side_effect=publish):
            with self.assertRaises(SystemExit):
                self.do()
        canonical = self.project / relative
        before = canonical.read_bytes()
        changed = copy.deepcopy(self.result)
        changed["verification"]["overall_status"] = "FAIL"
        self.assertEqual(self.cli(changed)["action"], "STOP")
        (self.candidate / relative).write_text("changed candidate", encoding="utf-8")
        self.assertEqual(self.cli()["action"], "STOP")
        self.assertEqual(canonical.read_bytes(), before)
        (self.candidate / relative).write_bytes(before)
        self.assertEqual(self.cli()["status"], "FINISHED")
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertTrue(completion.entry_hashes_intact(record))
        self.assertEqual(len(record["PUBLICATION_MANIFEST"]["publications"]), 1)
        self.assertEqual(canonical.read_bytes(), before)

    def test_invalid_owner_cannot_correct_after_revocation_or_expiry(self):
        self.start()
        invalid = copy.deepcopy(self.result)
        invalid["verification"]["claims"] = []
        self.assertEqual(self.do(result=invalid)["action"], "CORRECT_AND_RESUBMIT")
        cached = (self.candidate / work.RESULT_PATH).read_bytes()
        wrong = work.perform(self.root, session="wrong-owner", request={"op": "finish", "result": self.result})
        self.assertEqual(wrong["status"], "NOT_AUTHORIZED")
        # PICKUP-EXECUTION-LIFECYCLE: an owned finish is bounded by the
        # execution budget (CLAIMED_AT + MAX_TIME), so exhaust that clock.
        _, claim_path = completion.load_claim(self.root, self.identity)
        claim_data = json.loads(claim_path.joinpath("claim.json").read_text(encoding="utf-8-sig"))
        claim_data["CLAIMED_AT"] = "2000-01-01T00:00:00+00:00"
        claim_path.joinpath("claim.json").write_text(json.dumps(claim_data), encoding="utf-8")
        self.assertEqual(self.cli()["status"], "NOT_AUTHORIZED")
        self.assertEqual((self.candidate / work.RESULT_PATH).read_bytes(), cached)
        self.no_effects()

    def test_interruption_during_validation_does_not_offer_retry(self):
        self.start()
        original = finish.semantic_receipt
        def interrupt(*args):
            control.submit_intervention(self.root, b"interrupt verification", interrupt_current=True)
            return original(*args)
        invalid = copy.deepcopy(self.result)
        invalid["verification"]["claims"] = []
        with patch.object(finish, "semantic_receipt", side_effect=interrupt):
            result = self.do(result=invalid)
        self.assertEqual(result["status"], "NOT_AUTHORIZED", result)
        self.assertEqual(result["action"], "STOP")
        self.no_effects()

    def test_damaged_boundary_never_reopens_old_cache(self):
        self.start()
        cached = self.candidate / work.RESULT_PATH
        cached.write_text(finish.encoded(self.result), encoding="utf-8")
        for path in (self.plan,
                     self.project / f"completion_staging/finish-{self.commit_id}",
                     self.root / f"handoff/executor_publications/{self.commit_id}",
                     self.root / f"handoff/completion_ledger/{self.commit_id}.json"):
            with self.subTest(path=path):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("damaged", encoding="utf-8")
                changed = copy.deepcopy(self.result)
                changed["outcome"] = "FAILED"
                self.assertEqual(self.do(result=changed)["action"], "STOP")
                self.assertEqual(cached.read_text(encoding="utf-8"), finish.encoded(self.result))
                path.unlink()

    def test_legacy_fv_result_can_be_corrected_without_changing_compatibility_receipt(self):
        self.start()
        self.result.pop("verification")
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        self.result["completion"] = {"FINAL_VERIFICATION_RESULTS": copy.deepcopy(judgments)}
        self.result["completion"]["FINAL_VERIFICATION_RESULTS"]["CLAIM_RESULTS"].pop()
        self.assertEqual(self.do()["action"], "CORRECT_AND_RESUBMIT")
        self.result["completion"]["FINAL_VERIFICATION_RESULTS"] = judgments
        self.assertEqual(self.cli()["status"], "FINISHED")
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertEqual(record["RECEIPT"]["FINAL_VERIFICATION_RESULTS"], judgments)

    def test_valid_isolation_incident_is_committed_and_never_offered_as_schema_repair(self):
        self.start()
        self.result["verification"]["isolation_incident"] = True
        self.assertEqual(self.do()["status"], "FINISHED")
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertIs(record["RECEIPT"]["FINAL_VERIFICATION"]["ISOLATION_INCIDENT"], True)
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertFalse(event["final_verification"]["mechanical_pass"])
        self.result["verification"]["isolation_incident"] = False
        self.assertEqual(self.cli()["action"], "STOP")
        self.assertIs(completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
                      ["RECEIPT"]["FINAL_VERIFICATION"]["ISOLATION_INCIDENT"], True)


if __name__ == "__main__":
    unittest.main()
