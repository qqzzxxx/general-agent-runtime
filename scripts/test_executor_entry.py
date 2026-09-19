"""Offline entry tests using the real dispatch/archive/claim/fence lifecycle."""
from concurrent.futures import ThreadPoolExecutor
import contextlib
import hashlib
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

import executor_claim as claim
import executor_completion as completion
import executor_entry as entry
import executor_fence as fence
import ordinary_dispatch
import test_executor_fence as fixtures


class ExecutorEntryTests(unittest.TestCase):
    setUp = fixtures.ExecutorFenceTests.setUp
    dispatch = fixtures.ExecutorFenceTests.dispatch
    timeout = fixtures.ExecutorFenceTests.timeout
    staging = fixtures.ExecutorFenceTests.staging

    def enter(self, **kwargs):
        # The API must not emit internal CLI messages or secrets.
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = entry.enter(self.root, **kwargs)
        self.assertEqual(output.getvalue(), "")
        return result

    def assert_stopped(self, result, status=None):
        if status:
            self.assertEqual(result["status"], status, result)
        self.assertEqual(result["action"], "STOP", result)
        self.assertEqual(set(result), {"schema_version", "status", "action", "reason"})

    def cli(self, *args):
        process = subprocess.run([sys.executable, str(Path(entry.__file__)),
                                  "--root", str(self.root), *args],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.stderr, "", process.stderr)
        result = json.loads(process.stdout)
        self.assertEqual(process.returncode, entry.EXIT_CODES[result["status"]])
        return result

    def test_success_is_task_oriented_and_existing_completion_works(self):
        identity = self.dispatch()
        result = self.enter()
        self.assertEqual(result["status"], "READY", result)
        self.assertEqual(result["task"]["OBJECTIVE"], "synthetic U1-U3")
        self.assertTrue(all(k not in result["task"] for k in fence.IDENTITY_KEYS))
        self.assertEqual({k: result["attempt"][k] for k in identity}, identity)
        token = result["attempt"]["claim_token"]
        self.assertFalse(result["capabilities"]["os_sandbox"])
        work = Path(result["paths"]["attempt_workspace"])
        for name in ("workspace", "evidence", "reports"):
            self.assertTrue((work / name).is_dir())
        self.assertEqual(work, fence.check(self.root, identity, claim_token=token))
        self.assertFalse((self.project / "completion_staging").exists())
        candidate = work / "workspace/result.txt"
        candidate.write_text("checked", encoding="utf-8")
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        fence.publish(self.root, identity, "workspace/result.txt", digest, claim_token=token)
        with contextlib.redirect_stdout(io.StringIO()):
            code = completion.commit(self.root, self.staging(identity, [
                {"path": "workspace/result.txt", "sha256": digest}]), claim_token=token)
        self.assertEqual(code, 0)
        self.assert_stopped(self.enter())
        self.assert_stopped(self.enter(resume_token=token))

    def test_no_work_and_unregistered_candidate(self):
        self.o.save_runtime(self.runtime)
        self.assert_stopped(self.enter(), "NO_WORK")
        (self.root / "TO_ZCODE.md").write_text("candidate", encoding="utf-8")
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assertFalse((self.root / "handoff/executor_claims").exists())

    def test_missing_or_corrupt_runtime_never_becomes_no_work(self):
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.o.RUNTIME_STATE.write_text("broken", encoding="utf-8")
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")

    def test_duplicate_does_not_expose_owner_or_prepare_again(self):
        identity = self.dispatch()
        owner = self.enter()
        token = owner["attempt"]["claim_token"]
        with patch.object(fence, "prepare", side_effect=AssertionError("duplicate prepared")):
            result = self.enter()
        self.assert_stopped(result, "DUPLICATE")
        self.assertNotIn(token, json.dumps(result))
        metadata = claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]) / "claim.json"
        self.assertNotIn(token, metadata.read_text())

    def test_retired_and_processed_are_stale(self):
        identity = self.dispatch()
        self.timeout()
        self.assert_stopped(self.enter(), "STALE_TASK")
        self.dispatch(700111, 2)
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text("700111\n", encoding="utf-8")
        self.assert_stopped(self.enter(), "STALE_TASK")
        self.assertFalse(claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).exists())

    def test_authorization_damage_and_missing_archive_fail_before_claim(self):
        self.dispatch()
        archive = self.runtime["authorized_dispatch"]["SUPERVISOR_DISPATCH_ARCHIVE"]
        (self.root / archive["authorization_file"]).unlink()
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assertFalse((self.root / "handoff/executor_claims").exists())

    def test_inbox_damage_missing_and_identity_change_fail_closed(self):
        self.dispatch()
        raw = self.o.TO_ZCODE.read_bytes()
        for data in (raw + b"\n", raw.replace(b"700110", b"700999"), b"broken"):
            self.o.TO_ZCODE.write_bytes(data)
            self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.o.TO_ZCODE.unlink()
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assertFalse((self.root / "handoff/executor_claims").exists())

    def test_restart_requires_retained_owner_token(self):
        identity = self.dispatch()
        first = self.cli()
        token = first["attempt"]["claim_token"]
        claim_path = claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]) / "claim.json"
        original = claim_path.read_bytes()
        work = Path(first["paths"]["attempt_workspace"])
        (work / "workspace/progress.txt").write_text("resume here", encoding="utf-8")
        self.assert_stopped(self.cli(), "DUPLICATE")
        resumed = self.cli("--resume-token", token)
        self.assertEqual(resumed["status"], "READY", resumed)
        self.assertTrue(resumed["resumed"])
        self.assertEqual(first["attempt"], resumed["attempt"])
        self.assertEqual(first["paths"], resumed["paths"])
        self.assertEqual(claim_path.read_bytes(), original)
        self.assertEqual((work / "workspace/progress.txt").read_text(), "resume here")

    def test_wrong_empty_or_lost_token_never_acquires(self):
        self.dispatch()
        for token in ("wrong-token", "", "x" * 257):
            self.assert_stopped(self.enter(resume_token=token), "NOT_AUTHORIZED")
        self.assertFalse((self.root / "handoff/executor_claims").exists())
        owner = self.enter()
        self.assert_stopped(self.enter(resume_token="wrong-token"), "NOT_AUTHORIZED")
        self.assert_stopped(self.enter(), "DUPLICATE")
        self.assertEqual(self.enter(resume_token=owner["attempt"]["claim_token"])["status"], "READY")

    def test_old_owner_cannot_adopt_superseding_unclaimed_or_claimed_task(self):
        self.dispatch()
        token = self.enter()["attempt"]["claim_token"]
        new = self.dispatch(700111, 2)
        self.assert_stopped(self.enter(resume_token=token), "NOT_AUTHORIZED")
        self.assertFalse(claim.claim_dir(self.root, new["MESSAGE_ID"], new["NONCE"]).exists())
        self.assertEqual(self.enter()["status"], "READY")
        self.assert_stopped(self.enter(resume_token=token), "NOT_AUTHORIZED")

    def test_pause_blocks_new_claim(self):
        self.dispatch()
        import supervisor_control as sc
        sc.set_pause(self.root)
        self.assert_stopped(self.enter())

    def test_safe_pause_preserves_running_owner_resume(self):
        self.dispatch()
        token = self.enter()["attempt"]["claim_token"]
        import supervisor_control as sc
        sc.set_pause(self.root)
        self.assertEqual(self.enter(resume_token=token)["status"], "READY")

    def test_invalid_project_pointer_cannot_create_claim_or_view(self):
        self.dispatch()
        self.o.atomic_json(self.o.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": "synthetic", "project_root": "../other"})
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assertFalse((self.root / "handoff/executor_claims").exists())

    def test_hardlink_in_attempt_path_is_rejected(self):
        identity = self.dispatch()
        work = fence.attempt_root(self.project, identity)
        work.mkdir(parents=True)
        source = work / "scratch"
        source.write_text("preserve", encoding="utf-8")
        os.link(source, work / "workspace")
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assertEqual(source.read_text(), "preserve")

    def test_malformed_processed_pointer_blocks_entry_and_resume(self):
        self.dispatch()
        token = self.enter()["attempt"]["claim_token"]
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text("invalid", encoding="utf-8")
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assert_stopped(self.enter(resume_token=token), "NOT_AUTHORIZED")

    def test_incomplete_claim_after_crash_cannot_be_recovered(self):
        identity = self.dispatch()
        claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).mkdir(parents=True)
        self.assert_stopped(self.enter(), "DUPLICATE")
        self.assert_stopped(self.enter(resume_token="unknown"), "NOT_AUTHORIZED")

    def test_legacy_tokenless_attempt_is_not_upgraded_or_acquired(self):
        identity = self.dispatch()
        self.runtime["authorized_dispatch"].pop("FENCE_VERSION")
        self.o.save_runtime(self.runtime)
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        self.assertFalse(claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).exists())

    def test_archive_less_existing_owner_can_resume_without_new_acquisition(self):
        self.dispatch()
        token = self.enter()["attempt"]["claim_token"]
        for key in ("SUPERVISOR_DISPATCH_ARCHIVE", "SUPERVISOR_CONTROL_ORIGIN"):
            self.runtime["authorized_dispatch"].pop(key)
        self.o.save_runtime(self.runtime)
        self.o.register_dispatched_task(self.runtime, self.state, allow_same_identity=True)
        self.assert_stopped(self.enter(), "NOT_AUTHORIZED")
        with patch.object(claim, "acquire_locked", side_effect=AssertionError("resume acquired")):
            self.assertEqual(self.enter(resume_token=token)["status"], "READY")

    def test_stop_and_expiry_revoke_resume(self):
        self.dispatch()
        token = self.enter()["attempt"]["claim_token"]
        (self.root / "control/STOP").touch()
        self.assert_stopped(self.enter(resume_token=token), "NOT_AUTHORIZED")
        (self.root / "control/STOP").unlink()
        self.timeout()
        self.assert_stopped(self.enter(resume_token=token), "STALE_TASK")

    def test_prepare_failure_retains_claim_without_returning_token(self):
        identity = self.dispatch()
        with patch.object(fence, "prepare", side_effect=OSError("disk unavailable")):
            self.assert_stopped(self.enter(), "ERROR")
        self.assertTrue(claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).exists())
        self.assert_stopped(self.enter(), "DUPLICATE")

    def test_final_check_catches_supersession_during_prepare(self):
        self.dispatch()
        original = fence.prepare
        def replaced(*args, **kwargs):
            path = original(*args, **kwargs)
            self.dispatch(700111, 2)
            return path
        with patch.object(fence, "prepare", side_effect=replaced):
            self.assert_stopped(self.enter(), "NOT_AUTHORIZED")

    def test_concurrent_processes_have_one_owner(self):
        self.dispatch()
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.cli(), range(6)))
        self.assertEqual([r["status"] for r in results].count("READY"), 1, results)
        self.assertEqual([r["status"] for r in results].count("DUPLICATE"), 5, results)
        for result in results:
            if result["status"] != "READY":
                self.assert_stopped(result)

    def test_projection_preserves_semantics_custom_rules_and_fv_gate(self):
        gate = {"CONTRACT_VERSION": 1, "CLAIMS_HASH": "a" * 64,
                "POLICY_SNAPSHOT": {"statuses": ["PASS", "FAIL", "INCONCLUSIVE"]}}
        task = {"MESSAGE_ID": 700110, "OBJECTIVE": "Verify", "INPUTS": ["evidence/source"],
                "FINAL_VERIFICATION_GATE": gate, "TASK_KIND": "FINAL_VERIFICATION",
                "STOP_CONDITIONS": ["Missing evidence"], "CUSTOM_FIELD": ["keep"],
                "EXECUTOR_PROTOCOL": [*ordinary_dispatch.EXECUTOR_PROTOCOL, "Custom FV result rule"]}
        view = entry.task_view(task)
        self.assertEqual(view["FINAL_VERIFICATION_GATE"], gate)
        self.assertEqual(view["EXECUTOR_PROTOCOL"], ["Custom FV result rule"])
        self.assertEqual(view["CUSTOM_FIELD"], ["keep"])
        self.assertEqual(view["STOP_CONDITIONS"], ["Missing evidence"])
        view["FINAL_VERIFICATION_GATE"]["CONTRACT_VERSION"] = 99
        self.assertEqual(gate["CONTRACT_VERSION"], 1)
        with self.assertRaises(ValueError):
            entry.task_view({"EXECUTOR_PROTOCOL": {"CLAIM": "nested"}})


if __name__ == "__main__":
    unittest.main()
