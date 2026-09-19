"""Bootstrap V3: real entry/finish gates with conditional, Runtime-owned guidance."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import executor_claim as claim
import executor_completion as completion
import executor_contract as contract
import executor_entry as entry
import executor_fence as fence
import executor_work as work
import supervisor_control as control
import web_console_setup as setup
import test_executor_contract as fixtures
import test_host_native_executor as native
import test_semantic_finish_recovery as fv
from measure_executor_bootstrap import implementation_task, size

ROOT = Path(__file__).resolve().parents[1]


class BootstrapTests(unittest.TestCase):
    setUp = fixtures.ExecutorContractTests.setUp
    dispatch = fixtures.ExecutorContractTests.dispatch
    start = fixtures.ExecutorContractTests.start
    do = fixtures.ExecutorContractTests.do
    timeout = fixtures.ExecutorContractTests.timeout

    def cli(self, session=None):
        args = [sys.executable, entry.__file__, "--root", str(self.root), "--contract-version", "2"]
        if session is not None:
            args += ["--resume-token", session]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.stderr, "")
        return result.returncode, json.loads(result.stdout)

    def test_rendered_bootstrap_has_only_universal_rules_and_no_template_residue(self):
        template = (ROOT / "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md").read_text(encoding="utf-8")
        rendered = setup.render_zcode_prompt(template, str(self.root))
        self.assertIn(f'python "{self.root}/scripts/executor_entry.py" --contract-version 2', rendered)
        # The copyable prompt is plain text: no Markdown fence or language
        # tag may come back (2026-09-16 wizard UX cleanup), while every
        # semantic rule above stays pinned verbatim.
        self.assertNotIn("```", template)
        self.assertNotIn("```", rendered)
        for required in ("READY / exit 0", "--resume-token", "never in files or results", "across all host tools",
                         "publication", "completion", "owned background processes"):
            self.assertIn(required, rendered)
        for deferred in ("Replace ", "<RUNTIME_ROOT>", "INVALID_RESULT", "CORRECT_AND_RESUBMIT", "immutable",
                         "executor_work.py", "LOW", "HIGH", "screenshot", "claims covering", "artifacts"):
            self.assertNotIn(deferred, rendered)

    def test_ordinary_ready_cli_and_resume_preserve_session_and_sealed_task(self):
        identity = self.dispatch(fields=implementation_task())
        inbox = self.o.TO_ZCODE.read_bytes()
        code, ready = self.cli()
        self.assertEqual((code, ready["status"]), (0, "READY"))
        brief = ready["contract"]
        self.assertEqual(brief["autonomy"]["level"], "NORMAL")
        self.assertIn("implementation", brief["guidance"])
        self.assertNotIn("ui", brief["guidance"])
        self.assertNotIn("verification", brief["context"])
        self.assertNotIn("verification", brief["runtime"]["finish"]["fields"])
        self.assertEqual(set(brief["runtime"]["finish"]["fields"]), work.finish.RESULT_KEYS)
        self.assertIn(str(self.root / "scripts/executor_work.py"), brief["runtime"]["command"])
        self.assertNotIn("utilities", brief["capabilities"])
        self.assertNotIn("recovery", brief["runtime"])
        self.assertNotIn("CORRECT_AND_RESUBMIT", json.dumps(brief))
        self.assertEqual(self.cli(ready["session"]), (0, ready))
        self.assertEqual(self.o.TO_ZCODE.read_bytes(), inbox)
        self.assertEqual(completion.load_claim(self.root, identity)[0]["MESSAGE_ID"], identity["MESSAGE_ID"])
        self.assertNotIn(ready["session"], json.dumps(brief))

    def test_high_host_ui_guidance_does_not_require_runtime_browser(self):
        with patch.object(native.cap, "browser_dependencies", return_value={"node": None, "browser": None}):
            brief = self.start()["contract"]
        self.assertEqual(brief["autonomy"]["level"], "HIGH")
        self.assertIn("iterate", brief["autonomy"]["working_style"])
        self.assertIn("pixels", brief["guidance"]["ui"])
        self.assertIn("failure states", brief["guidance"]["ui"])
        self.assertEqual(brief["capabilities"]["browser"]["available"], "session_dependent")
        self.assertEqual(self.do("checkpoint")["status"], "OK")

    def test_generic_and_read_only_tasks_do_not_get_implementation_hints(self):
        task = implementation_task()
        task.update(OBJECTIVE="Summarize the supplied evidence.", OUTPUTS=["reports/summary.md"],
                    ACCEPTANCE_CRITERIA=["State supported findings and uncertainties."])
        self.assertNotIn("guidance", contract.project(task))
        task = implementation_task()
        task["EXECUTION"] = {"capabilities": {"filesystem": "read"}}
        self.assertNotIn("guidance", contract.project(task))
        view = contract.project(task)
        self.assertIn("Empty array", contract.finish_contract(view)["fields"]["artifacts"])

    def test_no_work_duplicate_stale_are_quiet_stop_without_contract_or_session(self):
        self.o.save_runtime(self.runtime)
        self.assertEqual(self.cli(), (10, entry.enter(self.root, contract_version=2)))
        self.assertEqual(self.cli()[1]["status"], "NO_WORK")
        self.start(implementation_task())
        code, duplicate = self.cli()
        self.assertEqual((code, duplicate["status"]), (10, "DUPLICATE"))
        self.timeout()
        code, stale = self.cli(self.session)
        self.assertEqual((code, stale["status"]), (11, "STALE_TASK"))
        for stopped in (duplicate, stale):
            self.assertEqual(set(stopped), {"schema_version", "status", "action", "reason"})
            self.assertEqual(stopped["action"], "STOP")

    def test_utility_help_is_live_scoped_metadata_and_has_no_work_effects(self):
        self.start(implementation_task())
        before = native.digest_tree(self.root)
        help_result = self.do("help", topic="utilities")
        self.assertEqual(help_result["status"], "OK")
        self.assertEqual(set(help_result["requests"]), {"read", "list", "copy", "write", "render"})
        self.assertNotIn("fetch", help_result["requests"])
        self.assertEqual(help_result["utilities"]["filesystem"]["max_copy_bytes"], 67108864)
        self.assertEqual(native.digest_tree(self.root), before)
        control.submit_intervention(self.root, b"interrupt", interrupt_current=True)
        for op in ({"op": "checkpoint"}, {"op": "help", "topic": "utilities"}):
            response = work.perform(self.root, session=self.session, request=op)
            self.assertEqual(response["action"], "STOP")
            self.assertNotIn("utilities", response)
        self.assertNotEqual(self.cli(self.session)[0], 0)

    def test_bounded_modes_and_urls_survive_deferring_utility_help(self):
        task = implementation_task()
        task["EXECUTION"] = {"capabilities": {"browser": "render", "network": "https_get", "shell": "none"},
                             "network_urls": ["https://example.com/"], "read_paths": ["evidence/brief.txt"]}
        brief = self.start(task)["contract"]
        caps = brief["capabilities"]
        self.assertEqual(caps["shell"]["mode"], "none")
        self.assertIn("scripts", caps["browser"]["limitation"])
        self.assertEqual(caps["network"]["urls"], task["EXECUTION"]["network_urls"])
        self.assertEqual(caps["filesystem"]["project_read_paths"], ["evidence/brief.txt"])
        self.assertEqual(self.do("help", topic="utilities")["utilities"]["network"]["urls"], caps["network"]["urls"])
        self.assertEqual(self.do("read", area="project", path="workspace/private.txt")["action"], "STOP")
        host = contract.project({**task, "EXECUTION": {**task["EXECUTION"], "capabilities": {"network": "host"}}}, include_utilities=False)
        self.assertEqual(host["capabilities"]["network"]["urls"], caps["network"]["urls"])

    def test_unavailable_guidance_only_on_unsuccessful_observation(self):
        self.start(implementation_task())
        self.assertNotIn("guidance", self.do("checkpoint"))
        failed = self.do("read", area="project", path="evidence/missing.txt")
        self.assertEqual((failed["status"], failed["action"]), ("UNAVAILABLE", "CONTINUE"))
        self.assertIn("another permitted method", failed["guidance"])

    def test_ordinary_invalid_result_projects_correction_without_fv(self):
        self.start(implementation_task())
        rejected = self.do("finish", result={})
        self.assertEqual((rejected["status"], rejected["action"]), ("INVALID_RESULT", "CORRECT_AND_RESUBMIT"))
        self.assertIn("same retained session", rejected["recovery"])
        self.assertNotIn("FV", rejected["recovery"])
        self.assertNotIn("verification", rejected["result_contract"]["fields"])
        self.assertTrue(entry.enter(self.root, resume_token=self.session, contract_version=2)["contract"]["locations"]["candidate_work_open"])
        self.assertEqual(self.do("finish", result=fixtures.semantic("PARTIAL"))["status"], "FINISHED")
        self.assertEqual(len(completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])), 1)

    def test_immutable_replay_is_projected_only_after_durable_boundary(self):
        self.start(implementation_task())
        result = fixtures.semantic("PARTIAL")
        original = completion._atomic_create
        def interrupt(path, content):
            original(path, content)
            if path.parent.name == "executor_finishes":
                raise SystemExit("interrupted after durable plan")
        with patch.object(completion, "_atomic_create", side_effect=interrupt), self.assertRaises(SystemExit):
            self.do("finish", result=result)
        resumed = entry.enter(self.root, resume_token=self.session, contract_version=2)
        brief = resumed["contract"]
        self.assertFalse(brief["locations"]["candidate_work_open"])
        self.assertEqual(resumed["session"], self.session)
        self.assertIn("identical previously submitted result", brief["runtime"]["recovery"]["instruction"])
        self.assertNotIn("guidance", brief)
        self.assertNotIn("checkpoint", brief["runtime"])
        self.assertNotIn("utility_help", brief["runtime"])
        self.assertEqual(self.do("help", topic="utilities")["action"], "STOP")
        self.assertEqual(self.do("checkpoint")["action"], "STOP")
        self.assertEqual(self.do("finish", result=fixtures.semantic("FAILED"))["action"], "STOP")
        self.assertEqual(self.do("finish", result=result)["status"], "FINISHED")

    def test_unicode_surface_accounting_counts_utf8_bytes(self):
        self.assertEqual(size("中文é"), {"characters": 3, "utf8_bytes": 8})


class VerificationBootstrapTests(unittest.TestCase):
    setUp = fv.SemanticFinishRecoveryTests.setUp
    decide = fv.SemanticFinishRecoveryTests.decide
    receipt = fv.SemanticFinishRecoveryTests.receipt
    start = fv.SemanticFinishRecoveryTests.start
    do = fv.SemanticFinishRecoveryTests.do

    def test_fv_fixed_claims_schema_read_only_and_acceptance_separation(self):
        ready = self.start()
        brief = ready["contract"]
        self.assertNotIn("guidance", brief)
        verification = brief["context"]["verification"]
        self.assertTrue(verification["deliverables_read_only"])
        self.assertEqual(verification["claims"], self.prepared["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"])
        self.assertIn("Final Acceptance", verification["result_requirements"])
        self.assertIn("verification", brief["runtime"]["finish"]["fields"])
        self.assertTrue(all("/verification/" in root for root in brief["locations"]["publication_roots"]))
        invalid = copy.deepcopy(self.result)
        invalid["verification"]["claims"].pop()
        rejected = self.do(result=invalid)
        self.assertEqual(rejected["action"], "CORRECT_AND_RESUBMIT")
        self.assertIn("negative judgments fixed", rejected["recovery"])
        self.assertIn("verification", rejected["result_contract"]["fields"])
        self.assertEqual(self.do()["status"], "FINISHED")
        self.assertEqual(self.h.read_state()["final_verification"]["status"], "PENDING")
        self.assertNotEqual(self.h.read_state()["status"], "COMPLETE")

    def test_ui_verification_projects_inspection_without_implementation_or_repair(self):
        self.task["OBJECTIVE"] = "Verify the website UI against fixed claims."
        brief = self.start()["contract"]
        self.assertIn("ui", brief["guidance"])
        self.assertNotIn("implementation", brief["guidance"])
        self.assertNotIn("Revise", brief["guidance"]["ui"])


if __name__ == "__main__":
    unittest.main()
