"""Phase 6: native candidates do not confer Runtime authority or completion.

Private synthetic runtimes. These are protocol tests, not a live ZCode model run.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import executor_capabilities as cap
import executor_claim as claim
import executor_completion as completion
import executor_contract as contract
import executor_entry as entry
import executor_fence as fence
import executor_work as work
import ordinary_dispatch as od
import supervisor_control as sc
import test_executor_contract as fixtures


def digest_tree(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}


class HostNativeTests(unittest.TestCase):
    setUp = fixtures.ExecutorContractTests.setUp
    dispatch = fixtures.ExecutorContractTests.dispatch
    start = fixtures.ExecutorContractTests.start
    do = fixtures.ExecutorContractTests.do
    timeout = fixtures.ExecutorContractTests.timeout

    def test_host_default_is_permission_not_availability_or_attestation(self):
        views = [contract.project({**fixtures.task_fields(), "EXECUTION": {"autonomy": level}})
                 for level in ("LOW", "NORMAL", "HIGH")]
        self.assertEqual(views[0]["capabilities"], views[2]["capabilities"])
        for name in ("shell", "browser", "network", "gui", "vision", "other"):
            self.assertEqual(views[0]["capabilities"][name]["mode"], "host")
            self.assertTrue(views[0]["capabilities"][name]["task_work_authorized"])
            self.assertEqual(views[0]["capabilities"][name]["available"], "session_dependent")
            self.assertEqual(views[0]["capabilities"][name]["assurance"], "cooperative_unverified")
        network_utility = views[0]["capabilities"]["utilities"]["network"]
        self.assertFalse(network_utility["available"])
        self.assertIn("host permission is described separately", network_utility["reason"])
        self.assertNotIn("Explicit task restriction", network_utility["reason"])

    def test_explicit_legacy_restrictions_and_task_semantics_survive(self):
        task = fixtures.task_fields()
        task.update(FORBIDDEN_ACTIONS=["Do not send email.", "Do not change live data."],
                    STOP_CONDITIONS=["Stop if authentication is required."])
        policy = {"autonomy": "HIGH", "capabilities": {"shell": "none", "network": "https_get",
                  "browser": "render", "gui": "none", "vision": "none", "other": "none"},
                  "network_urls": ["https://example.com/"], "read_paths": ["evidence/brief.txt"]}
        task["EXECUTION"] = policy
        view = contract.project(task)
        for name, mode in policy["capabilities"].items():
            self.assertEqual(view["capabilities"][name]["mode"], mode)
        self.assertEqual(view["boundaries"]["forbidden_actions"], task["FORBIDDEN_ACTIONS"])
        self.assertEqual(view["boundaries"]["stop_conditions"], task["STOP_CONDITIONS"])
        self.assertEqual(view["capabilities"]["utilities"]["network"]["urls"], policy["network_urls"])

    def test_native_subprocess_candidate_then_runtime_publication(self):
        self.start()
        locations = self.ready["contract"]["locations"]
        self.assertEqual(Path(locations["work"]), self.work)
        self.assertEqual(Path(locations["project_inputs"]), self.project)
        self.assertTrue(locations["candidate_work_open"])
        before_control = digest_tree(self.root / "control")
        before_history = digest_tree(self.root / "handoff")
        self.assertEqual(self.do("checkpoint")["status"], "OK")
        # A real independent host shell process, no executor_work write operation.
        process = subprocess.run([sys.executable, "-c",
            "from pathlib import Path; Path('workspace/signup.html').write_text('<button>Create account</button>')"],
            cwd=locations["work"], capture_output=True, text=True, timeout=10)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertFalse((self.project / "workspace/signup.html").exists())
        self.assertEqual(digest_tree(self.root / "control"), before_control)
        self.assertEqual(digest_tree(self.root / "handoff"), before_history)
        self.assertEqual(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]), [])
        result = fixtures.semantic("PARTIAL", [{"path": "workspace/signup.html", "role": "deliverable"}])
        result["limitations"] = ["No browser/visual check in this subprocess protocol test."]
        result["completion"] = {"Suggested next task": "This is only a recommendation, never dispatch authority."}
        self.assertEqual(self.do("finish", result=result)["status"], "FINISHED")
        self.assertEqual((self.work / "workspace/signup.html").read_bytes(),
                         (self.project / "workspace/signup.html").read_bytes())
        record = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]
        self.assertTrue(completion.entry_hashes_intact(record))
        self.assertEqual(completion.read_runtime_state(self.root)["authorized_dispatch"]["MESSAGE_ID"], self.identity["MESSAGE_ID"])
        self.assertEqual(self.do("checkpoint")["action"], "STOP")
        self.assertEqual(self.do("finish", result=result)["action"], "STOP")
        self.assertEqual(len(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])), 1)

    def test_protected_paths_and_control_operations_never_become_utilities(self):
        self.start()
        before_control, before_history = digest_tree(self.root / "control"), digest_tree(self.root / "handoff")
        for path in ("control/orchestrator_runtime.json", "handoff/completion_ledger/fake.json",
                     "project_state.json", "RESEARCH_STATE.md", "TO_ZCODE.md", "ZCODE_DONE.flag",
                     "reports/USER_STATUS.md", "../control/STOP"):
            self.assertEqual(self.do("write", path=path, text="forged")["action"], "STOP")
            self.assertEqual(self.do("read", area="project", path=path)["action"], "STOP")
            with self.assertRaises(fence.FenceError):
                fence.publish(self.root, self.identity, path, "0" * 64, claim_token=self.session)
            self.assertEqual(self.do("finish", result=fixtures.semantic(artifacts=[{"path": path, "role": "deliverable"}]))["action"], "STOP")
        for op in ("authorize", "dispatch", "create_task", "complete_task", "supervisor", "set_fv_state"):
            self.assertEqual(self.do(op)["action"], "STOP")
        self.assertEqual(digest_tree(self.root / "control"), before_control)
        self.assertEqual(digest_tree(self.root / "handoff"), before_history)

    def test_unregistered_inbox_and_host_claim_cannot_authorize_entry(self):
        identity = self.dispatch()
        runtime = completion.read_runtime_state(self.root)
        runtime["authorized_dispatch"] = None
        completion._atomic_write(self.root / "control/orchestrator_runtime.json", json.dumps(runtime))
        result = entry.enter(self.root, contract_version=2)
        self.assertEqual(result["action"], "STOP")
        self.assertNotIn("session", result)
        self.assertFalse(claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).exists())

    def test_stale_native_writer_cannot_publish_or_adopt_successor(self):
        self.start()
        old_work, old_session = self.work, self.session
        self.timeout()
        self.assertEqual(self.do("checkpoint")["action"], "STOP")
        self.dispatch(700111, 2)
        before = digest_tree(self.root / "handoff")
        # Honest boundary: a same-user writer can still touch its stale scratch.
        (old_work / "workspace/late.txt").write_text("late host output")
        self.assertEqual(self.do("finish", result=fixtures.semantic(artifacts=[{"path": "workspace/late.txt", "role": "deliverable"}]))["action"], "STOP")
        self.assertEqual(entry.enter(self.root, resume_token=old_session, contract_version=2)["action"], "STOP")
        self.assertEqual(digest_tree(self.root / "handoff"), before)
        self.assertFalse((self.project / "workspace/late.txt").exists())
        self.assertEqual(entry.enter(self.root, contract_version=2)["status"], "READY")

    def test_safe_pause_interrupt_finish_freeze_and_no_false_host_prerequisite(self):
        with patch.object(cap, "browser_dependencies", return_value={"node": None, "browser": None}):
            self.start()
            sc.set_pause(self.root)
            self.assertEqual(self.do("checkpoint")["status"], "OK")
            self.do("write", path="workspace/x.html", text="<p>hello</p>")
            self.assertEqual(self.do("render", area="work", path="workspace/x.html", width=375,
                                     height=812, screenshot="evidence/x.png")["status"], "UNAVAILABLE")
            self.assertEqual(self.do("checkpoint")["status"], "OK")
        sc.submit_intervention(self.root, b"interrupt", interrupt_current=True)
        self.assertEqual(self.do("checkpoint")["action"], "STOP")

    def test_finish_freeze_is_visible_to_native_resume(self):
        self.start()
        original = completion._atomic_create
        def create(path, content):
            original(path, content)
            if path.parent.name == "executor_finishes":
                raise SystemExit("validated plan is durable")
        with patch.object(completion, "_atomic_create", side_effect=create):
            with self.assertRaises(SystemExit):
                self.do("finish", result=fixtures.semantic("PARTIAL"))
        ready = entry.enter(self.root, resume_token=self.session, contract_version=2)
        self.assertFalse(ready["contract"]["locations"]["candidate_work_open"])
        self.assertEqual(self.do("checkpoint")["action"], "STOP")
        self.assertEqual(self.do("finish", result=fixtures.semantic("PARTIAL"))["status"], "FINISHED")

    def test_filesystem_read_restriction_also_blocks_low_level_native_publication(self):
        self.start({**fixtures.task_fields(), "EXECUTION": {"capabilities": {"filesystem": "read"}}})
        path = self.work / "workspace/x"
        path.write_text("unmediated")
        with self.assertRaisesRegex(fence.FenceError, "workspace capability"):
            fence.publish(self.root, self.identity, "workspace/x", hashlib.sha256(path.read_bytes()).hexdigest(), claim_token=self.session)
        self.assertFalse((self.project / "workspace/x").exists())


class HostVerificationTests(unittest.TestCase):
    setUp = fixtures.V2VerificationTests.setUp
    decide = fixtures.V2VerificationTests.decide
    receipt = fixtures.V2VerificationTests.receipt
    accept = fixtures.V2VerificationTests.accept

    def start(self):
        self.task["EXECUTOR_PROTOCOL"] = list(od.EXECUTOR_PROTOCOL)
        self.task["ACCEPTANCE_CRITERIA"] = ["Inspect fixed claims without modifying the deliverables."]
        self.decide()
        self.ready = entry.enter(self.root, contract_version=2)
        self.assertEqual(self.ready["status"], "READY", self.ready)
        self.work = Path(self.ready["contract"]["locations"]["work"])
        self.session = self.ready["session"]
        self.identity = {k: self.task[k] for k in fence.IDENTITY_KEYS}

    def test_fv_can_inspect_native_and_publish_only_new_verification_evidence(self):
        self.start()
        view = self.ready["contract"]
        self.assertTrue(view["context"]["verification"]["deliverables_read_only"])
        self.assertEqual(view["capabilities"]["browser"]["mode"], "host")
        source = self.h.project / "workspace/product.txt"
        source.parent.mkdir(exist_ok=True)
        source.write_text("fixed verified deliverable")
        before = source.read_bytes()
        evidence = view["locations"]["publication_roots"][0] + "/inspection.txt"
        path = self.work / evidence
        path.parent.mkdir(parents=True)
        path.write_text(source.read_text())
        result = fixtures.semantic("PARTIAL", [{"path": evidence, "role": "evidence"}])
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        judgments["CLAIM_RESULTS"][0]["status"] = "UNSUPPORTED"
        result["completion"] = {"FINAL_VERIFICATION_RESULTS": judgments}
        done = work.perform(self.root, session=self.session, request={"op": "finish", "result": result})
        self.assertEqual(done["status"], "FINISHED", done)
        self.assertEqual(source.read_bytes(), before)
        record = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]
        self.assertTrue(completion.entry_hashes_intact(record))
        self.assertFalse(self.accept(record)[0])
        self.assertEqual(self.h.read_state()["final_verification"]["status"], "PENDING")

    def test_fv_cannot_publish_deliverable_even_through_low_level_host_call(self):
        self.start()
        target = self.h.project / "workspace/product.txt"
        target.parent.mkdir(exist_ok=True)
        target.write_text("original")
        source = self.work / "workspace/product.txt"
        source.write_text("changed")
        with self.assertRaisesRegex(fence.FenceError, "FV publication"):
            fence.publish(self.root, self.identity, "workspace/product.txt",
                          hashlib.sha256(source.read_bytes()).hexdigest(), claim_token=self.session)
        result = fixtures.semantic(artifacts=[{"path": "workspace/product.txt", "role": "deliverable"}])
        result["completion"] = {"FINAL_VERIFICATION_RESULTS": self.receipt()["FINAL_VERIFICATION_RESULTS"]}
        done = work.perform(self.root, session=self.session, request={"op": "finish", "result": result})
        self.assertEqual(done["action"], "STOP")
        self.assertNotEqual(done["status"], "FINISHED")
        self.assertEqual(target.read_text(), "original")
        self.assertEqual(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"]), [])

    def test_fv_namespace_collision_cannot_overwrite_existing_evidence(self):
        self.start()
        relative = self.ready["contract"]["locations"]["publication_roots"][0] + "/existing.txt"
        target, source = self.h.project / relative, self.work / relative
        target.parent.mkdir(parents=True)
        source.parent.mkdir(parents=True)
        target.write_text("original")
        source.write_text("replacement")
        with self.assertRaisesRegex(fence.FenceError, "cannot replace"):
            fence.publish(self.root, self.identity, relative, hashlib.sha256(source.read_bytes()).hexdigest(), claim_token=self.session)
        self.assertEqual(target.read_text(), "original")

    def test_fv_target_appearing_during_publication_io_is_not_replaced(self):
        self.start()
        relative = self.ready["contract"]["locations"]["publication_roots"][0] + "/race.txt"
        target, source = self.h.project / relative, self.work / relative
        source.parent.mkdir(parents=True)
        source.write_text("candidate")
        real_fsync = fence.os.fsync
        def occupy(handle):
            real_fsync(handle)
            target.write_text("existing verified bytes")
        with patch.object(fence.os, "fsync", side_effect=occupy):
            with self.assertRaises(FileExistsError):
                fence.publish(self.root, self.identity, relative, hashlib.sha256(source.read_bytes()).hexdigest(), claim_token=self.session)
        self.assertEqual(target.read_text(), "existing verified bytes")
        self.assertFalse(fence.publication_record(self.root, self.identity, relative).exists())
        self.assertEqual(list(target.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
