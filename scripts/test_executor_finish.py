"""Phase 3 semantic finish through real dispatch, fence, ledger and FV gates."""
import contextlib
from concurrent.futures import ThreadPoolExecutor
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

SCRIPTS = str(Path(__file__).resolve().parent)
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import executor_completion as completion
import executor_entry as entry
import executor_fence as fence
import executor_finish as finish
import supervisor_control as control
import test_executor_fence as fixtures
import test_fv_one_pass as fv_fixtures


def semantic(outcome="COMPLETED"):
    return {"artifacts": [], "outcome": outcome, "findings": ["Measured result"],
            "evidence": ["task-provided input: source.txt"], "limitations": [],
            "completion": {"Acceptance self-check": {"criterion": "met"},
                           "Suggested memory updates": ["Retain this finding"]}}


class ExecutorFinishTests(unittest.TestCase):
    setUp = fixtures.ExecutorFenceTests.setUp
    dispatch = fixtures.ExecutorFenceTests.dispatch
    timeout = fixtures.ExecutorFenceTests.timeout

    def ready(self):
        self.identity = self.dispatch()
        view = entry.enter(self.root)
        self.assertEqual(view["status"], "READY", view)
        self.token = view["attempt"]["claim_token"]
        self.work = Path(view["paths"]["attempt_workspace"])
        self.result = semantic()
        return view

    def artifact(self, relative="reports/result.txt", role="deliverable", text="checked"):
        path = self.work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        self.result["artifacts"].append({"path": relative, "role": role})
        return path

    def write_result(self):
        (self.work / "finish.json").write_text(json.dumps(self.result), encoding="utf-8")

    def run_finish(self):
        self.write_result()
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = finish.finish(self.root, claim_token=self.token, result_path="finish.json")
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(result["action"], "STOP")
        self.assertNotIn(self.token, json.dumps(result))
        return result

    def cli(self):
        process = subprocess.run([sys.executable, str(Path(finish.__file__)), "--root", str(self.root),
                                  "--claim-token", self.token, "--result", "finish.json"],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.stderr, "")
        result = json.loads(process.stdout)
        self.assertEqual(process.returncode, finish.EXIT_CODES[result["status"]])
        return result

    def assert_no_finish(self):
        self.assertFalse(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]))
        self.assertFalse((self.root / "handoff/executor_finishes").exists())
        self.assertFalse((self.project / "reports/result.txt").exists())

    def test_success_derives_provenance_and_receipt_then_consumes_and_seals(self):
        self.ready()
        self.artifact(role="both")
        result = self.run_finish()
        self.assertEqual(result["status"], "FINISHED", result)
        record = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]
        self.assertTrue(completion.entry_hashes_intact(record))
        self.assertEqual(record["RECEIPT"]["STATUS"], "COMPLETED")
        self.assertEqual(record["RECEIPT"]["Key findings"], self.result["findings"])
        self.assertEqual(record["RECEIPT"]["Suggested memory updates"], ["Retain this finding"])
        manifest = record["PUBLICATION_MANIFEST"]["publications"]
        self.assertEqual(len(manifest), 1)
        self.assertEqual(manifest[0]["sha256"], completion.sha256_bytes(b"checked"))
        archive = completion.staged_archive_dir(self.root, record["COMMIT_ID"]) / "staging.json"
        staged = json.loads(archive.read_text())
        self.assertEqual(staged["EVIDENCE"], staged["DELIVERABLES"])
        self.assertEqual(self.run_finish()["status"], "ALREADY_FINISHED")
        consumed, _ = self.o.consume_executor_receipt(self.runtime)
        self.assertTrue(consumed)
        self.assertEqual(self.run_finish()["status"], "SEALED")
        self.state.update(status="BLOCKED", current_task=None)
        self.o.atomic_json(self.o.PROJECT_STATE, self.state)
        self.o.reconcile_completion_ledger(self.runtime, self.state)
        self.o.seal_completions(self.runtime, self.state)
        sealed = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]
        self.assertEqual(sealed["STATUS"], completion.STATUS_SEALED)
        before = completion.entry_path(self.root, record["COMMIT_ID"]).read_bytes()
        self.assertEqual(self.cli()["status"], "SEALED")
        self.assertEqual(completion.entry_path(self.root, record["COMMIT_ID"]).read_bytes(), before)

    def test_semantic_failure_and_partial_outcomes_are_never_upgraded(self):
        for index, outcome in enumerate(("PARTIAL", "FAILED", "BLOCKED", "INCONCLUSIVE", "ABORTED_BY_USER")):
            with self.subTest(outcome=outcome):
                self.identity = self.dispatch(700110 + index, index + 1)
                view = entry.enter(self.root)
                self.token = view["attempt"]["claim_token"]
                self.work = Path(view["paths"]["attempt_workspace"])
                self.result = semantic(outcome)
                self.result["limitations"] = ["Required input unavailable"]
                if outcome == "PARTIAL":
                    self.artifact()
                self.assertEqual(self.run_finish()["status"], "FINISHED")
                receipt = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]["RECEIPT"]
                self.assertEqual(receipt["STATUS"], outcome)
                self.assertEqual(receipt["Limitations"], ["Required input unavailable"])

    def test_invalid_semantics_reserve_and_publish_nothing(self):
        self.ready()
        self.artifact()
        original = copy.deepcopy(self.result)
        for field, value in (("outcome", "PASS"), ("findings", "prose"), ("evidence", [None]),
                             ("completion", {"MESSAGE_ID": 9}), ("completion", {"FINAL_VERIFICATION": {}}),
                             ("completion", {"note": self.token}), ("completion", {"number": float("nan")}),
                             ("completion", {"huge": "x" * 65536})):
            with self.subTest(field=field, value=str(value)[:50]):
                self.result = copy.deepcopy(original)
                self.result[field] = value
                self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
                self.assert_no_finish()

    def test_missing_second_output_blocks_first_publication(self):
        self.ready()
        self.artifact()
        self.result["artifacts"].append({"path": "evidence/missing", "role": "evidence"})
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()

    def test_paths_aliases_links_and_duplicates_fail_closed(self):
        self.ready()
        self.artifact()
        baseline = copy.deepcopy(self.result)
        for relative in ("../outside", "C:/outside", "reports/USER_STATUS.md", "control/x", "reports/aux.txt",
                         "reports/x:stream", "reports/x.", "reports\\x", "Reports/result.txt", "reports/RESULT.txt"):
            with self.subTest(relative=relative):
                self.result = copy.deepcopy(baseline)
                self.result["artifacts"].append({"path": relative, "role": "deliverable"})
                self.assertNotEqual(self.run_finish()["status"], "FINISHED")
                self.assert_no_finish()
        self.result = baseline
        os.link(self.work / "reports/result.txt", self.work / "linked")
        self.assertNotEqual(self.run_finish()["status"], "FINISHED")
        self.assert_no_finish()

    def test_result_json_duplicates_and_input_path_escape(self):
        self.ready()
        (self.work / "finish.json").write_text('{"outcome":"FAILED","outcome":"COMPLETED"}')
        self.assertEqual(self.cli()["status"], "INVALID_RESULT")
        self.assertEqual(finish.finish(self.root, claim_token=self.token,
                                      result_path="../finish.json")["status"], "NOT_AUTHORIZED")
        self.assert_no_finish()

    def test_wrong_token_stop_expiry_and_supersession(self):
        self.ready()
        self.artifact()
        self.write_result()
        self.assertEqual(finish.finish(self.root, claim_token="wrong", result_path="finish.json")["status"], "NOT_AUTHORIZED")
        (self.root / "control/STOP").touch()
        self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        (self.root / "control/STOP").unlink()
        self.timeout()
        self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        self.dispatch(700111, 2)
        self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        self.assert_no_finish()

    def test_control_revision_and_archive_tampering(self):
        self.ready()
        self.artifact()
        auth = self.runtime["authorized_dispatch"]
        archive = self.root / auth["SUPERVISOR_DISPATCH_ARCHIVE"]["archive_file"]
        original = archive.read_bytes()
        archive.write_bytes(original + b"\n")
        self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        archive.write_bytes(original)
        control.submit_intervention(self.root, b"interrupt current work", interrupt_current=True)
        self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        self.assert_no_finish()

    def test_safe_pause_preserves_existing_owner_finish_authority(self):
        self.ready()
        self.artifact()
        control.set_pause(self.root)
        self.assertEqual(self.run_finish()["status"], "FINISHED")

    def test_fault_boundaries_replay_identical_plan_in_fresh_process(self):
        # Each invocation uses a new real attempt, not a mock ledger or fresh claim.
        for index, boundary in enumerate(("plan", "publication", "staging", "ledger")):
            with self.subTest(boundary=boundary):
                self.identity = self.dispatch(700110 + index, index + 1)
                view = entry.enter(self.root)
                self.token = view["attempt"]["claim_token"]
                self.work = Path(view["paths"]["attempt_workspace"])
                self.result = semantic()
                self.artifact()
                original_create, original_publish = completion._atomic_create, fence.publish
                def create(path, content):
                    original_create(path, content)
                    matching = ((boundary == "plan" and path.parent.name == "executor_finishes")
                                or (boundary == "staging" and path.name == "staging.json")
                                or (boundary == "ledger" and path.parent.name == "completion_ledger"))
                    if matching:
                        raise SystemExit("simulated crash")
                def publish(*args, **kwargs):
                    result = original_publish(*args, **kwargs)
                    if boundary == "publication":
                        raise SystemExit("simulated crash")
                    return result
                with patch.object(completion, "_atomic_create", side_effect=create), patch.object(fence, "publish", side_effect=publish):
                    with self.assertRaises(SystemExit):
                        self.run_finish()
                plan_path = next((self.root / "handoff/executor_finishes").glob(
                    f"completion-{self.identity['MESSAGE_ID']}-*.json"))
                before = plan_path.read_bytes()
                replay = self.cli()
                self.assertEqual(replay["status"], "ALREADY_FINISHED" if boundary == "ledger" else "FINISHED", replay)
                self.assertEqual(plan_path.read_bytes(), before)
                self.assertEqual(len(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])), 1)

    def test_crash_between_file_replace_and_record_is_recoverable(self):
        self.ready()
        self.artifact()
        original = completion._atomic_write
        def write(path, content):
            if "executor_publications" in path.parts:
                raise SystemExit("after canonical replace")
            return original(path, content)
        with patch.object(completion, "_atomic_write", side_effect=write):
            with self.assertRaises(SystemExit):
                self.run_finish()
        self.assertTrue((self.project / "reports/result.txt").exists())
        self.assertFalse(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]))
        self.assertEqual(self.cli()["status"], "FINISHED")

    def test_changed_package_after_preparation_is_rejected(self):
        self.ready()
        candidate = self.artifact()
        with patch.object(fence, "publish", side_effect=SystemExit("before publication")):
            with self.assertRaises(SystemExit):
                self.run_finish()
        self.result["outcome"] = "FAILED"
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.result["outcome"] = "COMPLETED"
        candidate.write_text("changed")
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assertFalse((self.project / "reports/result.txt").exists())
        self.assertFalse(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]))

    def test_retirement_after_partial_publication_cannot_replay(self):
        self.ready()
        self.artifact()
        original = fence.publish
        def publish(*args, **kwargs):
            original(*args, **kwargs)
            raise SystemExit("interrupted")
        with patch.object(fence, "publish", side_effect=publish):
            with self.assertRaises(SystemExit):
                self.run_finish()
        before = (self.project / "reports/result.txt").read_bytes()
        self.timeout()
        self.assertEqual(self.cli()["status"], "NOT_AUTHORIZED")
        self.assertEqual((self.project / "reports/result.txt").read_bytes(), before)
        self.assertFalse(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]))

    def test_damaged_ledger_and_staging_never_cause_republication(self):
        self.ready()
        self.artifact()
        commit_id = completion.commit_id_for(self.identity["MESSAGE_ID"], self.identity["NONCE"])
        target = completion.entry_path(self.root, commit_id)
        target.parent.mkdir(parents=True)
        target.write_text("invalid")
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()
        target.unlink()
        stage = self.project / f"completion_staging/finish-{commit_id}/staging.json"
        stage.parent.mkdir(parents=True)
        stage.write_text("{}")
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()

    def test_completion_hint_failure_does_not_recommit_and_runtime_recovers(self):
        self.ready()
        self.artifact()
        with patch.object(completion, "publish_compatibility_artifacts", side_effect=OSError("disk")):
            self.assertEqual(self.run_finish()["status"], "ERROR")
        self.assertEqual(self.cli()["status"], "ALREADY_FINISHED")
        self.assertFalse((self.root / "ZCODE_DONE.flag").exists())
        self.o.reconcile_completion_ledger(self.runtime, self.state)
        consumed, _ = self.o.consume_executor_receipt(self.runtime)
        self.assertTrue(consumed)

    def test_concurrent_finish_processes_commit_once_without_republishing(self):
        self.ready()
        self.artifact()
        self.write_result()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.cli(), range(4)))
        self.assertEqual([r["status"] for r in results].count("FINISHED"), 1, results)
        self.assertEqual([r["status"] for r in results].count("ALREADY_FINISHED"), 3, results)
        self.assertEqual(len(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])), 1)

    def test_candidate_mutation_between_hash_and_publish_is_rejected(self):
        self.ready()
        candidate = self.artifact()
        original = fence.publish
        def mutate(*args, **kwargs):
            candidate.write_text("changed")
            return original(*args, **kwargs)
        with patch.object(fence, "publish", side_effect=mutate):
            self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        self.assertFalse((self.project / "reports/result.txt").exists())
        self.assertFalse(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]))

    def test_expiry_during_snapshot_prevents_reservation_and_publication(self):
        self.ready()
        self.artifact()
        original = finish.artifact_manifests
        def expire(*args, **kwargs):
            manifests = original(*args, **kwargs)
            # PICKUP-EXECUTION-LIFECYCLE: the post-IO recheck enforces the
            # execution budget (CLAIMED_AT + MAX_TIME); exhaust that clock.
            _, claim_path = completion.load_claim(self.root, self.identity)
            claim_data = json.loads(
                claim_path.joinpath("claim.json").read_text(encoding="utf-8-sig"))
            claim_data["CLAIMED_AT"] = "2000-01-01T00:00:00+00:00"
            claim_path.joinpath("claim.json").write_text(
                json.dumps(claim_data), encoding="utf-8")
            return manifests
        with patch.object(finish, "artifact_manifests", side_effect=expire):
            self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        self.assert_no_finish()

    def test_unlisted_or_corrupt_existing_publication_blocks_new_outputs(self):
        self.ready()
        self.artifact()
        candidate = self.artifact("evidence/old.txt", "evidence")
        fence.publish(self.root, self.identity, "evidence/old.txt",
                      completion.sha256_bytes(candidate.read_bytes()), claim_token=self.token)
        self.result["artifacts"].pop()
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()
        self.result["artifacts"].append({"path": "evidence/old.txt", "role": "evidence"})
        (self.project / "evidence/old.txt").write_text("tampered")
        self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()

    def test_manifest_and_byte_limits_are_enforced_before_mutation(self):
        self.ready()
        self.artifact()
        with patch.object(completion, "EVIDENCE_MAX_FILE_BYTES", 3):
            self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()
        with patch.object(completion, "EVIDENCE_MAX_ENTRIES", 0):
            self.assertEqual(self.run_finish()["status"], "INVALID_RESULT")
        self.assert_no_finish()

    def test_staging_link_cannot_redirect_runtime_writes(self):
        self.ready()
        candidate = self.artifact()
        commit_id = completion.commit_id_for(self.identity["MESSAGE_ID"], self.identity["NONCE"])
        stage = self.project / f"completion_staging/finish-{commit_id}/staging.json"
        stage.parent.mkdir(parents=True)
        os.link(candidate, stage)
        self.assertEqual(self.run_finish()["status"], "NOT_AUTHORIZED")
        self.assert_no_finish()
        self.assertEqual(candidate.read_text(), "checked")

    def test_offline_census_and_prompt_match_finish_contract(self):
        import measure_runtime_owned_completion as measure
        report = measure.report()
        self.assertEqual(report["executor_required_operations"]["whole_stage_phase3"], "C + 1")
        self.assertIsNone(report["provider_token_savings"])
        after = report["fixtures"]["after_result"]
        identity = report["fixtures"]["before_staging"]
        self.assertEqual(finish.semantic_receipt(after, completion.entry_identity(identity)), identity["RECEIPT"])
        prompt = (Path(__file__).resolve().parents[1] / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md").read_text(encoding="utf-8")
        # Bootstrap V3 defers V2 semantic finish instructions to live READY.
        self.assertNotIn("executor_work.py", prompt)
        self.assertNotIn('"op":"finish"', prompt)
        import test_executor_contract as contract_fixtures
        owner = contract_fixtures.ExecutorContractTests()
        owner.setUp()
        self.addCleanup(owner.doCleanups)
        runtime = owner.start()["contract"]["runtime"]
        self.assertIn("executor_work.py", runtime["command"])
        self.assertEqual(runtime["finish"]["request"]["op"], "finish")
        self.assertEqual(set(runtime["finish"]["fields"]), finish.RESULT_KEYS)
        self.assertNotIn("--sha256", prompt)
        self.assertNotIn("--staging-dir", prompt)
        self.assertNotIn("correct it only", prompt)


class FinishFinalVerificationTests(unittest.TestCase):
    setUp = fv_fixtures.FVOnePassTests.setUp
    decide = fv_fixtures.FVOnePassTests.decide
    receipt = fv_fixtures.FVOnePassTests.receipt
    accept = fv_fixtures.FVOnePassTests.accept

    def submit(self, results):
        view = entry.enter(self.root)
        self.assertEqual(view["status"], "READY", view)
        work = Path(view["paths"]["attempt_workspace"])
        result = semantic()
        result["completion"] = {"FINAL_VERIFICATION_RESULTS": results}
        (work / "finish.json").write_text(json.dumps(result), encoding="utf-8")
        return finish.finish(self.root, claim_token=view["attempt"]["claim_token"], result_path="finish.json")

    def test_fv_success_consumes_and_passes_existing_acceptance_gate(self):
        self.decide()
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        self.assertEqual(self.submit(judgments)["status"], "FINISHED")
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertEqual(record["RECEIPT"]["FINAL_VERIFICATION_RESULTS"], judgments)
        consumed, event = self.o.consume_executor_receipt(self.runtime)
        self.assertTrue(consumed)
        self.assertTrue(event["final_verification"]["mechanical_pass"])
        self.assertEqual(self.accept(record), (True, "PASS"))
        self.assertEqual(self.h.read_state()["status"], "WAITING_EXECUTOR")

    def test_fv_negative_judgment_cannot_be_promoted_by_overall_pass(self):
        self.decide()
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        judgments["CLAIM_RESULTS"][0]["status"] = "CONTRADICTED"
        judgments["CLAIM_RESULTS"][0]["checks"]["deliverable_check"] = "FAIL"
        self.assertEqual(self.submit(judgments)["status"], "FINISHED")
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertEqual(record["RECEIPT"]["FINAL_VERIFICATION_RESULTS"], judgments)
        _, event = self.o.consume_executor_receipt(self.runtime)
        self.assertFalse(event["final_verification"]["mechanical_pass"])
        self.assertFalse(self.accept(record)[0])

    def test_fv_invalid_claim_coverage_creates_no_preparation(self):
        self.decide()
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        judgments["CLAIM_RESULTS"].pop()
        self.assertEqual(self.submit(judgments)["status"], "INVALID_RESULT")
        self.assertFalse((self.root / "handoff/executor_finishes").exists())
        self.assertFalse(completion.lookup_entries(self.root, self.task["MESSAGE_ID"]))

    def test_fv_fail_and_inconclusive_finish_without_upgrading_judgment(self):
        for index, overall in enumerate(("FAIL", "INCONCLUSIVE")):
            with self.subTest(overall=overall):
                self.task.update(MESSAGE_ID=700120 + index, NONCE=f"finish-fv-{index}")
                self.decide()
                judgments = self.receipt(overall)["FINAL_VERIFICATION_RESULTS"]
                self.assertEqual(self.submit(judgments)["status"], "FINISHED")
                record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
                self.assertEqual(record["RECEIPT"]["FINAL_VERIFICATION"]["OVERALL_STATUS"], overall)
                _, event = self.o.consume_executor_receipt(self.runtime)
                self.assertFalse(event["final_verification"]["mechanical_pass"])
                self.assertFalse(self.accept(record)[0])

    def test_entry_translates_only_exact_fv_packaging_instruction(self):
        self.decide()
        import final_verification_contract as fv
        custom = "Custom RECEIPT.FINAL_VERIFICATION_RESULTS instructions remain verbatim"
        projected = entry.task_view({**self.prepared, "EXECUTOR_PROTOCOL": [fv.RESULT_PROTOCOL, custom]})
        self.assertIn("completion.FINAL_VERIFICATION_RESULTS in the semantic finish result",
                      projected["EXECUTOR_PROTOCOL"][0])
        self.assertEqual(projected["EXECUTOR_PROTOCOL"][1], custom)
        self.assertEqual(projected["FINAL_VERIFICATION_GATE"], self.prepared["FINAL_VERIFICATION_GATE"])


if __name__ == "__main__":
    unittest.main()
