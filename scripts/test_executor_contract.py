"""Phase 4: real sealed dispatch, V2 projection, mediated operations and outcome work."""
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
import executor_contract as contract
import executor_entry as entry
import executor_fence as fence
import executor_work as work
import ordinary_dispatch as od
import supervisor_control as sc
import test_executor_fence as fixtures
import test_fv_one_pass as fv_fixtures


def semantic(outcome="COMPLETED", artifacts=None):
    return {"artifacts": artifacts or [], "outcome": outcome, "findings": [],
            "evidence": [], "limitations": [], "completion": {}}


def task_fields():
    return {"OBJECTIVE": "Build a clear sign-up form.", "INPUTS": ["evidence/brief.txt"],
            "OUTPUTS": ["workspace/signup.html"],
            "ACCEPTANCE_CRITERIA": ["Every input has an associated label.",
                                    "Submit action has a descriptive name."],
            "EXECUTOR_PROTOCOL": list(od.EXECUTOR_PROTOCOL),
            "EXECUTION": {"autonomy": "HIGH"}}


class ExecutorContractTests(unittest.TestCase):
    setUp = fixtures.ExecutorFenceTests.setUp
    timeout = fixtures.ExecutorFenceTests.timeout

    def dispatch(self, message=700110, attempt=1, fields=None):
        # Modify task semantics before the real decision archive/seal is created.
        original = fixtures.wire
        with patch.object(fixtures, "wire", side_effect=lambda task: original(
                {**task, **(task_fields() if fields is None else fields)})):
            identity = fixtures.ExecutorFenceTests.dispatch(self, message, attempt)
        return identity

    def start(self, fields=None):
        self.identity = self.dispatch(fields=fields)
        self.ready = entry.enter(self.root, contract_version=2)
        self.assertEqual(self.ready["status"], "READY", self.ready)
        self.session = self.ready["session"]
        self.work = fence.attempt_root(self.project, self.identity)
        return self.ready

    def do(self, op, **kwargs):
        return work.perform(self.root, session=self.session, request={"op": op, **kwargs})

    def test_v2_is_outcome_only_and_wire_stays_immutable(self):
        self.start()
        before = self.o.TO_ZCODE.read_bytes()
        brief = self.ready["contract"]
        self.assertEqual(brief["outcome"], task_fields()["OBJECTIVE"])
        self.assertEqual(brief["acceptance_criteria"], task_fields()["ACCEPTANCE_CRITERIA"])
        for name in (*fence.IDENTITY_KEYS, "MAX_RETRIES", "EXECUTOR_PROTOCOL", "completion_staging"):
            self.assertNotIn(name, json.dumps(self.ready))
        self.assertEqual(set(self.ready), {"schema_version", "status", "action", "session", "contract"})
        self.assertEqual(entry.enter(self.root, contract_version=2)["status"], "DUPLICATE")
        self.assertEqual(entry.enter(self.root, resume_token=self.session, contract_version=2), self.ready)
        self.assertEqual(self.o.TO_ZCODE.read_bytes(), before)

    def test_autonomy_never_changes_permissions_and_unknown_values_fail(self):
        views = [contract.project({**task_fields(), "EXECUTION": {"autonomy": level}})
                 for level in ("LOW", "NORMAL", "HIGH")]
        self.assertEqual(views[0]["capabilities"], views[1]["capabilities"])
        self.assertEqual(views[1]["capabilities"], views[2]["capabilities"])
        self.assertEqual(contract.project({k: v for k, v in task_fields().items() if k != "EXECUTION"})["autonomy"]["level"], "NORMAL")
        for value in ({"autonomy": "UNLIMITED"}, {"autonomy": True}, {"capabilities": {"network": "read"}},
                      {"capabilities": {"shell": "sandboxed"}}, {"capabilities": {"browser": "local"}},
                      {"capabilities": {"gui": "full"}}, {"capabilities": {"filesystem": "host"}},
                      {"capabilities": {"unknown": "none"}}, {"os_sandbox": True}, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                contract.validate_execution(value)

    def test_custom_protocol_entries_are_stamped_out_at_decision_commit(self):
        # F-003: the wire protocol is Runtime-owned text that the template
        # mandates copying unchanged. A Supervisor candidate that appends
        # custom protocol text (or copies the canonical array with drift) is
        # re-stamped to the canonical array at decision commit, so the custom
        # text never reaches any Executor view and the V2 entry can claim a
        # dispatch that would previously dead-end as NOT_AUTHORIZED.
        fields = {**task_fields(), "CUSTOM_CONTEXT": {"requirement": "Do not truncate this."}}
        self.assertEqual(contract.project(fields)["context"]["task_data"],
                         {"CUSTOM_CONTEXT": fields["CUSTOM_CONTEXT"]})
        fields["EXECUTOR_PROTOCOL"].append("Call an arbitrary legacy protocol helper.")
        identity = self.dispatch(fields=fields)
        entered = entry.enter(self.root, contract_version=2)
        self.assertEqual(entered["status"], "READY")
        self.assertEqual(entered["contract"]["context"]["task_data"],
                         {"CUSTOM_CONTEXT": fields["CUSTOM_CONTEXT"]})
        published = sc._parse_dispatch_bytes((self.root / "TO_ZCODE.md").read_bytes())
        self.assertEqual(published["EXECUTOR_PROTOCOL"], list(od.EXECUTOR_PROTOCOL))

    def test_missing_acceptance_and_bad_capability_do_not_claim(self):
        for index, extra in enumerate(({"ACCEPTANCE_CRITERIA": []}, {"EXECUTION": {"capabilities": {"shell": "allow"}}})):
            identity = self.dispatch(700110 + index, index + 1, {**task_fields(), **extra})
            result = entry.enter(self.root, contract_version=2)
            self.assertEqual(result["action"], "STOP", result)
            self.assertNotIn("session", result)
            self.assertFalse(claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).exists())

    def test_filesystem_explore_copy_write_read_and_finish(self):
        self.start()
        (self.project / "evidence").mkdir(exist_ok=True)
        (self.project / "evidence/brief.txt").write_text("Label every field.", encoding="utf-8")
        self.assertEqual(self.do("list", area="project", path="evidence")["entries"],
                         [{"name": "brief.txt", "kind": "file"}])
        self.assertEqual(self.do("read", area="project", path="evidence/brief.txt")["text"], "Label every field.")
        self.assertEqual(self.do("copy", area="project", path="evidence/brief.txt", destination="workspace/brief.txt")["status"], "OK")
        self.assertEqual(self.do("write", path="workspace/signup.html", text="<form>Draft</form>")["status"], "OK")
        self.assertEqual(self.do("read", area="work", path="workspace/signup.html")["text"], "<form>Draft</form>")
        self.assertFalse((self.project / "workspace/signup.html").exists())
        result = semantic("PARTIAL", [{"path": "workspace/signup.html", "role": "deliverable"}])
        result["limitations"] = ["Form still needs labels; browser inspection unavailable."]
        self.assertEqual(self.do("finish", result=result)["status"], "FINISHED")
        self.assertEqual((self.project / "workspace/signup.html").read_text(), "<form>Draft</form>")
        record = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]
        self.assertEqual(record["RECEIPT"]["STATUS"], "PARTIAL")
        self.assertTrue(completion.entry_hashes_intact(record))
        self.assertEqual(self.do("write", path="workspace/late.txt", text="late")["action"], "STOP")

    def test_read_only_policy_can_report_but_not_write_copy_or_publish(self):
        self.start({**task_fields(), "EXECUTION": {"capabilities": {"filesystem": "read"}}})
        for op, args in (("write", {"path": "workspace/x", "text": "x"}),
                         ("copy", {"area": "project", "path": "evidence/x", "destination": "workspace/x"})):
            self.assertEqual(self.do(op, **args)["action"], "STOP")
        (self.work / "workspace/bypass.txt").write_text("unmediated")
        self.assertEqual(self.do("finish", result=semantic(artifacts=[{"path": "workspace/bypass.txt", "role": "deliverable"}]))["action"], "STOP")
        self.assertEqual(self.do("finish", result=semantic("BLOCKED"))["status"], "FINISHED")
        self.assertFalse((self.project / "workspace/bypass.txt").exists())

    def test_explicit_input_paths_and_reserved_authority_paths(self):
        self.start({**task_fields(), "EXECUTION": {"read_paths": ["inputs/brief.txt"]}})
        (self.project / "inputs").mkdir()
        (self.project / "inputs/brief.txt").write_text("Scoped")
        (self.project / "inputs/private.txt").write_text("Unrelated")
        self.assertEqual(self.do("read", area="project", path="inputs/brief.txt")["text"], "Scoped")
        for path in ("inputs/private.txt", "inputs/brief.txt.bak", "inputs", "project_state.json",
                     "../control/orchestrator_runtime.json", "attempt_workspaces", "reports/USER_STATUS.md"):
            self.assertEqual(self.do("read", area="project", path=path)["action"], "STOP")
        for path in ("../outside", "C:/foo", "control", "handoff", "project_state.json",
                     "research_state.md", "scripts", "reports/USER_STATUS.md", "inputs/NUL", "inputs/a:stream"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                contract.validate_execution({"read_paths": [path]})

    def test_paths_and_links_are_rejected_without_canonical_writes(self):
        self.start()
        for path in ("../x", "workspace/../../x", "workspace/a:stream", "workspace/NUL",
                     "workspace/a.", "workspace\\x", "reports/USER_STATUS.md", "control/x"):
            self.assertEqual(self.do("write", path=path, text="x")["action"], "STOP")
        original = self.work / "workspace/original"
        original.write_text("intact")
        os.link(original, self.work / "workspace/link")
        self.assertEqual(self.do("write", path="workspace/link", text="bad")["action"], "STOP")
        self.assertEqual(self.do("read", area="work", path="workspace/link")["action"], "STOP")
        self.assertEqual(original.read_text(), "intact")

    def test_unsupported_operations_and_bounded_requests_fail_closed(self):
        self.start()
        for request in ({"op": "shell", "command": "echo unsafe"}, {"op": "browser", "url": "https://example.org"},
                        {"op": "gui"}, {"op": "network"}, {"op": "write", "path": "workspace/x", "text": "x", "autonomy": "HIGH"},
                        {"op": []}, None):
            result = work.perform(self.root, session=self.session, request=request)
            self.assertEqual(result["action"], "STOP", result)
        self.assertEqual(self.do("write", path="workspace/large", text="x" * (work.MAX_TEXT_BYTES + 1))["action"], "STOP")
        for raw in (b'{"op":"read","op":"write"}', b'{"value":NaN}', b'[]' * work.MAX_REQUEST_BYTES):
            with self.assertRaises(ValueError):
                work.parse_request(raw)
        self.assertFalse((self.work / "workspace/x").exists())

    def test_no_secret_in_content_or_error_and_no_token_recovery(self):
        self.start()
        result = self.do("write", path="workspace/x", text=self.session)
        self.assertEqual(result["action"], "STOP")
        self.assertNotIn(self.session, json.dumps(result))
        result = work.perform(self.root, session="wrong-token", request={"op": "write", "path": "workspace/x", "text": "x"})
        self.assertEqual(result["status"], "NOT_AUTHORIZED")
        self.assertFalse((self.work / "workspace/x").exists())

    def test_unavailable_input_allows_alternative_but_never_scope_expansion(self):
        self.start()
        missing = self.do("read", area="project", path="evidence/missing.txt")
        self.assertEqual(missing["status"], "UNAVAILABLE")
        self.assertEqual(missing["action"], "CONTINUE")
        self.assertNotIn("text", missing)
        (self.project / "evidence").mkdir(exist_ok=True)
        (self.project / "evidence/alternative.txt").write_text("Available evidence")
        self.assertEqual(self.do("read", area="project", path="evidence/alternative.txt")["text"], "Available evidence")
        self.assertEqual(self.do("read", area="project", path="inputs/unpermitted.txt")["action"], "STOP")
        (self.root / "control/STOP").write_text("stop")
        self.assertEqual(self.do("read", area="project", path="evidence/missing.txt")["status"], "NOT_AUTHORIZED")

    def test_retirement_supersession_and_stop_revoke_operations(self):
        self.start()
        self.do("write", path="workspace/first", text="first")
        self.timeout()
        self.assertEqual(self.do("write", path="workspace/first", text="stale")["status"], "NOT_AUTHORIZED")
        self.dispatch(700111, 2)
        self.assertEqual(self.do("read", area="work", path="workspace/first")["status"], "NOT_AUTHORIZED")
        self.assertEqual((self.work / "workspace/first").read_text(), "first")
        fresh = entry.enter(self.root, contract_version=2)
        self.assertEqual(fresh["status"], "READY")
        self.session = fresh["session"]
        (self.root / "control/STOP").write_text("stop")
        self.assertEqual(self.do("write", path="workspace/x", text="x")["status"], "NOT_AUTHORIZED")

    def test_expiry_during_write_is_rechecked_before_replace(self):
        self.start()
        real_check = fence.check_locked
        def check(*args, **kwargs):
            if list((self.work / "workspace").glob("*.tmp")):
                raise fence.FenceError("attempt_expired")
            return real_check(*args, **kwargs)
        with patch.object(fence, "check_locked", side_effect=check):
            self.assertEqual(self.do("write", path="workspace/x", text="x")["status"], "NOT_AUTHORIZED")
        self.assertFalse(list((self.work / "workspace").iterdir()))

    def test_safe_pause_allows_owner_but_explicit_interrupt_revokes(self):
        self.start()
        sc.set_pause(self.root)
        self.assertEqual(self.do("write", path="workspace/paused.txt", text="owner continues")["status"], "OK")
        sc.submit_intervention(self.root, b"stop this attempt", interrupt_current=True)
        self.assertEqual(self.do("write", path="workspace/paused.txt", text="late")["status"], "NOT_AUTHORIZED")

    def test_finish_crash_before_validation_keeps_work_correctable(self):
        self.start()
        result = semantic("BLOCKED")
        with patch.object(work.finish, "finish", side_effect=SystemExit("crash")):
            with self.assertRaises(SystemExit):
                self.do("finish", result=result)
        self.assertEqual(self.do("write", path="workspace/x", text="corrected")["action"], "CONTINUE")
        self.assertEqual(self.do("finish", result=semantic("COMPLETED"))["status"], "FINISHED")

    def test_semantic_schema_rejection_and_fv_on_ordinary_task_are_correctable(self):
        self.start()
        invalid = semantic()
        invalid.pop("findings")
        rejected = self.do("finish", result=invalid)
        self.assertEqual(rejected["action"], "CORRECT_AND_RESUBMIT", rejected)
        invalid = semantic()
        invalid["verification"] = {"overall_status": "PASS", "claims": []}
        rejected = self.do("finish", result=invalid)
        self.assertEqual(rejected["action"], "CORRECT_AND_RESUBMIT", rejected)
        self.assertIn("authorized FV task", rejected["reason"])
        self.assertEqual(self.do("finish", result=semantic())["status"], "FINISHED")

    def test_cli_request_stdin_and_versioned_entry(self):
        self.start()
        process = subprocess.run([sys.executable, work.__file__, "--root", str(self.root), "--session", self.session],
                                 input=json.dumps({"op": "write", "path": "workspace/cli.txt", "text": "cli"}),
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr + process.stdout)
        self.assertEqual(json.loads(process.stdout)["status"], "OK")
        process = subprocess.run([sys.executable, entry.__file__, "--root", str(self.root), "--contract-version", "2"],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(json.loads(process.stdout)["status"], "DUPLICATE")
        self.assertEqual(process.returncode, 10)

    def test_cli_opaque_tokens_with_leading_dash_are_values(self):
        with patch.object(claim.secrets, "token_urlsafe", return_value="-opaque-test-token"):
            self.start()
        process = subprocess.run([sys.executable, entry.__file__, "--root", str(self.root),
                                  "--resume-token", self.session, "--contract-version", "2"],
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["session"], self.session)
        process = subprocess.run([sys.executable, work.__file__, "--root", str(self.root), "--session", self.session],
                                 input=json.dumps({"op": "finish", "result": semantic("BLOCKED")}),
                                 capture_output=True, text=True, timeout=30)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)["status"], "FINISHED")

    def test_scripted_ui_comparison_keeps_v1_iteration_control_and_evidence_limits(self):
        import measure_executor_contract as measure
        report = measure.report()
        runs = report["scripted_ui_runs"]
        self.assertEqual(runs["v1_stage_only_control"]["outcome"], "PARTIAL")
        v1, v2 = runs["v1_iterative_control"], runs["v2_high_iterative"]
        self.assertEqual(v1["published_source"], v2["published_source"])
        self.assertEqual(v1["semantic_result"], v2["semantic_result"])
        self.assertFalse(all(v2["trace"][0]["inspection"].values()))
        self.assertTrue(all(v2["trace"][1]["inspection"].values()))
        self.assertEqual(v1["explicit_client_fence_calls"], 3)
        self.assertEqual(v2["explicit_client_fence_calls"], 0)
        self.assertTrue(v2["provenance_intact"])
        self.assertIsNone(report["model_quality_improvement"])
        self.assertIsNone(report["provider_token_savings"])
        prompt = (Path(__file__).resolve().parents[1] / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md").read_text(encoding="utf-8")
        for removed in ("--message-id", "--nonce", "executor_fence.py", "--sha256", "--staging-dir"):
            self.assertNotIn(removed, prompt)
        self.assertIn("--contract-version 2", prompt)
        self.assertIn("inspect", prompt)


class V2VerificationTests(unittest.TestCase):
    setUp = fv_fixtures.FVOnePassTests.setUp
    decide = fv_fixtures.FVOnePassTests.decide
    receipt = fv_fixtures.FVOnePassTests.receipt
    accept = fv_fixtures.FVOnePassTests.accept

    def test_semantic_projection_and_negative_judgment_survive_commit(self):
        self.task["EXECUTOR_PROTOCOL"] = list(od.EXECUTOR_PROTOCOL)
        self.task["ACCEPTANCE_CRITERIA"] = ["Check every fixed claim against its policy standard."]
        self.decide()
        ready = entry.enter(self.root, contract_version=2)
        self.assertEqual(ready["status"], "READY", ready)
        verification = ready["contract"]["context"]["verification"]
        self.assertEqual(verification["claims"], self.prepared["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"])
        for key in ("POLICY_SHA256", "CLAIMS_HASH", "TASK_IDENTITY", "POLICY_ID"):
            self.assertNotIn(key, json.dumps(ready))
        judgments = self.receipt()["FINAL_VERIFICATION_RESULTS"]
        judgments["CLAIM_RESULTS"][0]["status"] = "UNSUPPORTED"
        result = semantic("PARTIAL")
        result["completion"] = {"FINAL_VERIFICATION_RESULTS": judgments}
        done = work.perform(self.root, session=ready["session"], request={"op": "finish", "result": result})
        self.assertEqual(done["status"], "FINISHED", done)
        record = completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertEqual(record["RECEIPT"]["FINAL_VERIFICATION_RESULTS"], judgments)
        self.assertFalse(self.accept(record)[0])

    def test_v2_rejects_unsupported_fv_mode_without_claiming(self):
        self.task["EXECUTOR_PROTOCOL"] = list(od.EXECUTOR_PROTOCOL)
        self.task["ACCEPTANCE_CRITERIA"] = ["Verify claims against their fixed policy."]
        self.decide()
        # Projection test: changing the mode cannot grant a sandbox capability.
        prepared = json.loads(json.dumps(self.prepared))
        prepared["FINAL_VERIFICATION_GATE"]["EXECUTION_MODE"] = "SANDBOX_DESTRUCTIVE"
        with self.assertRaisesRegex(ValueError, "cannot provide"):
            contract.project(prepared)
        self.assertFalse(claim.claim_dir(self.root, self.task["MESSAGE_ID"], self.task["NONCE"]).exists())


class InputPathAuthorityBoundaryTests(unittest.TestCase):
    """Authority stays mechanical; information availability moves to projection.

    Incident: an ACADEMIC_RESEARCH Supervisor proposal placed RESEARCH_STATE.md in
    execution.read_paths and the Executor contract correctly failed closed. The
    boundary stays strict; the delegation contract instead teaches distilling such
    information into proposal inputs, which the Runtime projects into the task
    brief verbatim.
    """

    def test_reserved_authority_paths_are_rejected_and_named(self):
        for relative in ("RESEARCH_STATE.md", "research_state.md", "RESEARCH_STATE.MD",
                         "project_state.json", "control/orchestrator_runtime.json",
                         "logs/orchestrator.jsonl", "handoff/PROTOCOL.md",
                         "scripts/executor_entry.py", "orchestrator.py"):
            with self.subTest(relative=relative):
                with self.assertRaises(ValueError) as ctx:
                    contract.validate_input_path(relative)
                self.assertIn("Runtime authority is not an input capability",
                              str(ctx.exception))
                self.assertIn(relative, str(ctx.exception))

    def test_project_working_scope_paths_stay_authorized(self):
        for relative in ("workspace/baseline.py", "evidence/run1/metrics.json",
                         "reports/summary.md", "PROJECT_GOAL.md", "docs/notes.md"):
            with self.subTest(relative=relative):
                contract.validate_input_path(relative)

    def test_execution_policy_rejects_authority_read_paths(self):
        with self.assertRaisesRegex(ValueError,
                                    "Runtime authority is not an input capability"):
            contract.validate_execution({"autonomy": "NORMAL",
                                         "read_paths": ["workspace", "RESEARCH_STATE.md"]})

    def test_distilled_inputs_are_projected_into_task_context(self):
        task = task_fields()
        task["EXECUTION"] = {"autonomy": "NORMAL",
                             "read_paths": ["workspace", "evidence", "reports"]}
        task["INPUTS"] = [
            "RESEARCH_STATE.md 已验证结论摘要（Supervisor 蒸馏，Runtime 原样投影进任务简报）"]
        projected = contract.project(task)
        self.assertEqual(projected["context"]["inputs"], task["INPUTS"])
        self.assertEqual(projected["capabilities"]["filesystem"]["project_read_paths"],
                         ["workspace", "evidence", "reports"])


if __name__ == "__main__":
    unittest.main()
