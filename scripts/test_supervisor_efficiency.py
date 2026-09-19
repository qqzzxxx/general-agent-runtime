"""Phase 9 offline decision sufficiency, escalation and cost recovery contracts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import measure_supervisor_efficiency as measure
import ordinary_dispatch
import provider_usage
import supervisor_inspect
import supervisor_intelligence as intelligence
import supervisor_control as control
import test_supervisor_context as context_fixture


class DecisionEfficiencyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = context_fixture.SupervisorContextTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_b3_initial_outcome_delegation_requires_no_implementation_reads(self):
        result = measure.report()
        before, after = result["contexts"]["phase8"], result["contexts"]["phase9"]
        self.assertEqual(after["implementation_read_bytes"], 0)
        self.assertLess(after["builder_read_bytes"], before["builder_read_bytes"])
        self.assertLess(after["builder_read_count"], before["builder_read_count"])
        # Phase 9 may exceed the frozen Phase 8 anchor only by the approved
        # growth of the delivered contract text (rules + profile guidance)
        # plus fixed construction slack; context routing itself adds none.
        frozen = measure.EVIDENCE / "baseline"
        contract_growth = (
            (measure.ROOT / "control/CODEX_SUPERVISOR_RUNTIME.md").stat().st_size
            - (frozen / "CODEX_SUPERVISOR_RUNTIME.md").stat().st_size
            + (measure.ROOT / "profiles/SOFTWARE_ENGINEERING/SUPERVISOR_GUIDANCE.md").stat().st_size
            - (frozen / "SUPERVISOR_GUIDANCE.md").stat().st_size)
        self.assertLess(after["prompt_utf8_bytes"],
                        before["prompt_utf8_bytes"] + contract_growth + 256)
        self.assertIsNone(result["phase8_live_deep_read_count"])
        for key in ("provider_token_improvement", "live_latency_improvement", "live_model_quality_improvement"):
            self.assertIsNone(result[key])
        proposal = ordinary_dispatch.validate_proposal(measure.proposal())
        self.assertEqual(proposal["execution"]["autonomy"], "HIGH")
        outcome = proposal["outcome_context"]
        self.assertEqual(set(outcome), {"desired_outcome", "quality_bar", "hard_constraints", "current_facts", "revisable_assumptions"})
        self.assertIn("PROJECT_GOAL.md", outcome["hard_constraints"][0]["source"])
        self.assertIn("DOM", outcome["revisable_assumptions"][0])
        self.assertNotIn("forbidden_actions", measure.proposal())

    def test_reference_only_paths_are_not_opened_even_if_missing(self):
        original = Path.read_bytes
        allowed = {self.fixture.goal, self.fixture.memory,
                   measure.ROOT / "control/CODEX_SUPERVISOR_RUNTIME.md"}
        def guarded(path):
            self.assertIn(path, allowed)
            return original(path)
        with patch.object(Path, "read_bytes", guarded):
            prompt = self.fixture.prompt(profile={"profile_id": "SOFTWARE_ENGINEERING", "profile_version": 1,
                "supervisor_guidance": "Keep sourced compatibility constraints."})
        self.assertIn("REFERENCED DEEP CONTEXT", prompt)
        self.assertIn("dispatch now", prompt)
        self.assertIn("supervisor_inspect.py", prompt)
        self.assertIn("FINAL_VERIFICATION_POLICY.json", prompt)
        self.assertEqual(prompt.context_manifest["builder_reads"]["files"], 3)

    def test_material_conflict_and_insufficient_evidence_remain_visible(self):
        self.fixture.state.update(decision_history=[{"reason": "public ID may be incompatible"}],
            last_supervisor_decision={"reason": "all clients compatible"})
        prompt = self.fixture.prompt(receipt={"STATUS": "COMPLETED", "Limitations": ["No interaction evidence"],
                                            "unknown": "possible scope drift"})
        for text in ("public ID may be incompatible", "all clients compatible", "No interaction evidence", "possible scope drift",
                     "conflicting evidence", "suspected regression", "insufficient result evidence"):
            self.assertIn(text, prompt)

    def test_negative_fv_and_audit_demand_inspection_without_changing_gates(self):
        prompt = self.fixture.prompt(receipt={"FINAL_VERIFICATION_RESULTS": {"OVERALL_STATUS": "FAIL"},
                "FINAL_VERIFICATION": {"OVERALL_STATUS": "PASS"}},
                interventions=[{"mode": "AUDIT", "instruction_text": "Inspect previous interface changes"}])
        for text in ('"FAIL"', '"PASS"', "AUDIT requires adversarial read-only inspection",
                     "FV failure/disagreement", "COMPLETE is forbidden", "independent FV"):
            self.assertIn(text, prompt)

    def test_deferred_history_is_available_to_resolve_a_retry_question(self):
        self.fixture.memory.write_text("<!-- supervisor-context-v2: current-memory -->\n"
            "## Unresolved questions\nCheck prior method failures before retry.\n"
            "## Decision history\nMETHOD_A_ALREADY_FAILED_THREE_TIMES\n", encoding="utf-8")
        prompt = self.fixture.prompt(reason="EXECUTOR_TIMEOUT")
        self.assertNotIn("METHOD_A_ALREADY_FAILED_THREE_TIMES", prompt)
        self.assertIn("Check prior method failures", prompt)
        result = supervisor_inspect.inspect(self.fixture.root, "RESEARCH_STATE.md", "history")
        self.assertIn("METHOD_A_ALREADY_FAILED_THREE_TIMES", result["content"])
        # Area 1 replaced numeric retry arithmetic with evidence-based
        # continuation judgment; a retry question must deliver that contract.
        flat = " ".join(prompt.split())
        self.assertIn("redirect or stop when attempts yield neither", flat)
        self.assertNotIn("at most two retries", flat)


class TargetedReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-deep-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "interface.md"
        self.data = b"public btn-pause ID must remain stable\n" + b"irrelevant implementation\n" * 10000
        self.path.write_bytes(self.data)

    def test_targeted_interface_evidence_changes_the_authored_constraint(self):
        # Synthetic decision, not a sampled model: establish an actual public
        # interface before constraining an otherwise free redesign.
        result = supervisor_inspect.inspect(self.root, "interface.md", "interface_constraint", limit=37)
        self.assertIn("btn-pause", result["content"])
        self.assertEqual(result["bytes_read"], 37)
        self.assertTrue(result["has_more"])
        self.assertEqual(result["slice_sha256"], hashlib.sha256(self.data[:37]).hexdigest())
        proposal = measure.proposal()
        proposal["outcome_context"]["hard_constraints"].append({"constraint": result["content"].strip(),
            "source": "interface.md bytes 0..36; slice sha256 " + result["slice_sha256"]})
        self.assertEqual(ordinary_dispatch.validate_proposal(proposal)["execution"]["autonomy"], "HIGH")
        self.assertEqual(self.path.read_bytes(), self.data)

    def test_all_escalation_reasons_can_inspect_evidence(self):
        for reason in supervisor_inspect.REASONS:
            with self.subTest(reason=reason):
                result = supervisor_inspect.inspect(self.root, "interface.md", reason, offset=7, limit=12)
                self.assertEqual(result["content"], self.data[7:19].decode())
                self.assertEqual(result["next_offset"], 19)

    def test_ranges_missing_files_escape_and_no_write(self):
        for args in (("interface.md", "enrich_dispatch", 0, 5), ("interface.md", "ambiguity", -1, 5),
                     ("interface.md", "ambiguity", 0, 65537), (".", "audit", 0, 8)):
            with self.assertRaises(ValueError):
                supervisor_inspect.inspect(self.root, *args)
        with self.assertRaises(FileNotFoundError):
            supervisor_inspect.inspect(self.root, "missing.md", "conflict")
        (self.root / "nested").mkdir()
        with self.assertRaises(ValueError):
            supervisor_inspect.inspect(self.root / "nested", self.path, "conflict")
        self.assertEqual(len(list(self.root.glob("*.md"))), 1)

    def test_cli_is_read_only_and_bounded(self):
        result = subprocess.run([sys.executable, str(measure.ROOT / "scripts/supervisor_inspect.py"),
            "--path", "interface.md", "--reason", "conflict", "--limit", "16"],
            cwd=self.root, capture_output=True, check=True)
        self.assertEqual(json.loads(result.stdout)["bytes_read"], 16)
        self.assertEqual(set(p.name for p in self.root.iterdir()), {"interface.md"})


class IntelligenceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-intelligence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.turn = {"turn_id": "supervisor-turn-" + uuid.uuid4().hex, "PROJECT_ID": "p"}
        self.manifest = {"prompt_characters": 5, "prompt_utf8_bytes": 7}

    def helper_event(self, **overrides):
        item = {"type": "command_execution", "id": "item_1", "exit_code": 0,
            "command": "python scripts/supervisor_inspect.py --path interface.md --reason conflict",
            "aggregated_output": json.dumps({"schema": supervisor_inspect.SCHEMA, "status": "READ",
                "bytes_read": 34, "reason": "conflict", "content": "DO_NOT_PERSIST"})}
        item.update(overrides)
        return {"type": "item.completed", "item": item}

    def capture(self, items, completed=True):
        with provider_usage.stdout_capture(self.root, self.turn, context_manifest=self.manifest) as stdout:
            events = [{"type": "thread.started", "thread_id": str(uuid.uuid4())}, {"type": "turn.started"}] + items
            if completed:
                events.append({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 5}})
            for event in events:
                stdout.write((json.dumps(event) + "\n").encode())
        return intelligence.read(self.root, self.turn["turn_id"], "p")

    def test_capture_deduplicates_reads_and_keeps_usage_separate(self):
        event = self.helper_event()
        record = self.capture([event, event, self.helper_event(id="item_2")])
        self.assertEqual(record["targeted_reads"]["reads"], 2)
        self.assertEqual(record["targeted_reads"]["approximate_bytes"], 68)
        self.assertEqual(record["context_manifest"], self.manifest)
        self.assertGreaterEqual(record["duration_seconds"], 0)
        self.assertNotIn("DO_NOT_PERSIST", "".join(p.read_text() for p in self.root.rglob("*.json")))
        usage = provider_usage.read_usage(self.root, self.turn["turn_id"], "p")
        self.assertEqual(intelligence.cache_split(usage)["uncached_input_tokens"], 40)

    def test_model_prose_native_reads_and_missing_usage_are_not_invented(self):
        record = self.capture([self.helper_event(type="agent_message"), self.helper_event(command="cat interface.md")], completed=False)
        self.assertEqual(record["targeted_reads"]["reads"], 0)
        self.assertEqual(record["targeted_reads"]["coverage"], "helper_only")
        self.assertFalse(provider_usage.read_usage(self.root, self.turn["turn_id"], "p")["reported"])
        self.assertIsNone(intelligence.cache_split({"reported": True, "input_tokens": 100})["uncached_input_tokens"])
        self.assertIsNone(intelligence.cache_split({"reported": True, "input_tokens": 10, "cached_input_tokens": 20})["uncached_input_tokens"])

    def test_malformed_helper_output_does_not_poison_provider_usage(self):
        record = self.capture([self.helper_event(aggregated_output="truncated")])
        self.assertEqual(record["targeted_reads"]["attempts"], 1)
        self.assertFalse(record["targeted_reads"]["complete"])
        self.assertTrue(provider_usage.read_usage(self.root, self.turn["turn_id"], "p")["reported"])

    # Real Supervisor-session evidence (codex rollout 2026-09-18T00-10-58,
    # Memory Routing V2 evaluation): the pwsh-wrapped helper call failed
    # inside the wrapper (python not on the pwsh PATH), and the Supervisor
    # then byte-sliced the memory file natively at the routed utf8_offset —
    # the successful history inspection the capture must represent.

    PWSH_HELPER_FAILURE = {"type": "command_execution",
        "id": "exec-9770e1f7-8ac1-4f86-8387-6d19dd476dc3", "exit_code": 1,
        "command": "pwsh.exe -Command python scripts/supervisor_inspect.py"
                   " --path projects/mr2-ecg-001/RESEARCH_STATE.md"
                   " --reason history --offset 1986 --limit 10400",
        "aggregated_output": "python : 无法将“python”项识别为 cmdlet、函数、脚本文件或可运行程序的名称。\r\n"
                             "+ python scripts/supervisor_inspect.py --path projects/mr2-ecg-001/RESE ...\r\n"
                             "    + CategoryInfo          : ObjectNotFound: (python:String) [], CommandNotFoundException\r\n"}

    PWSH_POINTER_READ_FIRST = {"type": "command_execution",
        "id": "exec-3cfac439-8ee9-40f7-9d05-cb4f752027e5", "exit_code": 0,
        "command": "pwsh.exe -Command $p='C:\\general-agent-runtime-mr2ab-v2\\projects\\mr2-ecg-001\\RESEARCH_STATE.md';"
                   " $b=[System.IO.File]::ReadAllBytes($p);"
                   " [System.Text.Encoding]::UTF8.GetString($b,1986,[Math]::Min(10400,$b.Length-1986))",
        "aggregated_output": "## Historical memory before 700113 final metrics assembly\n以下原文完整保留。\n"}

    PWSH_POINTER_READ_SECOND = {"type": "command_execution",
        "id": "exec-ce5f73e1-e229-419e-aa8d-2769ad1b6002", "exit_code": 0,
        "command": "pwsh.exe -Command $p='C:\\general-agent-runtime-mr2ab-v2\\projects\\mr2-ecg-001\\RESEARCH_STATE.md';"
                   " $b=[System.IO.File]::ReadAllBytes($p);"
                   " [System.Text.Encoding]::UTF8.GetString($b,12578,[Math]::Min(3970,$b.Length-12578))",
        "aggregated_output": "## Historical memory before 700104 sweep stage close\n以下原文完整保留。\n"}

    def wrapped(self, item):
        return {"type": "item.completed", "item": item}

    def test_real_pwsh_wrapper_failure_is_decisive_not_uncertain(self):
        record = self.capture([self.wrapped(self.PWSH_HELPER_FAILURE)])
        self.assertEqual(record["targeted_reads"]["attempts"], 1)
        self.assertEqual(record["targeted_reads"]["reads"], 0)
        self.assertEqual(record["targeted_reads"]["history_pointer_reads"], 0)
        self.assertTrue(record["targeted_reads"]["complete"])

    def test_real_native_pointer_history_retrieval_is_recorded_as_successful(self):
        # A failed pointer-form command (nonzero exit) decisively read nothing.
        failed = self.wrapped(dict(self.PWSH_POINTER_READ_FIRST,
                                   id="exec-pointer-failed-1", exit_code=1))
        record = self.capture([failed, self.wrapped(self.PWSH_POINTER_READ_FIRST),
                               self.wrapped(self.PWSH_POINTER_READ_SECOND)])
        reads = record["targeted_reads"]
        self.assertEqual(reads["attempts"], 0)
        self.assertEqual(reads["reads"], 0)
        self.assertEqual(reads["history_pointer_reads"], 2)
        self.assertEqual(reads["coverage"], "helper_and_history_pointer_forms")
        self.assertTrue(reads["complete"])

    def test_real_gap_turn_sequence_records_the_successful_history_retrieval(self):
        # The exact V2-evaluation observability gap: one failed wrapped helper
        # attempt plus two successful native pointer retrievals.
        record = self.capture([self.wrapped(self.PWSH_HELPER_FAILURE),
                               self.wrapped(self.PWSH_POINTER_READ_FIRST),
                               self.wrapped(self.PWSH_POINTER_READ_SECOND)])
        reads = record["targeted_reads"]
        self.assertEqual((reads["attempts"], reads["reads"]), (1, 0))
        self.assertEqual(reads["history_pointer_reads"], 2)
        self.assertTrue(reads["complete"])

    def test_routine_native_memory_reads_are_not_invented_as_retrievals(self):
        # Full-file or line-range reads of the memory file are routine
        # reorganization reads, not offset-addressed history retrieval.
        routine = {"type": "command_execution", "id": "exec-routine-1", "exit_code": 0,
                   "command": "pwsh.exe -Command Get-Content -Raw -Encoding UTF8 projects/mr2-ecg-001/RESEARCH_STATE.md",
                   "aggregated_output": "# Project Memory\n…\n"}
        record = self.capture([self.wrapped(routine)])
        reads = record["targeted_reads"]
        self.assertEqual((reads["attempts"], reads["reads"], reads["history_pointer_reads"]), (0, 0, 0))
        self.assertEqual(reads["coverage"], "helper_only")
        self.assertTrue(reads["complete"])

    def test_legacy_intelligence_records_without_pointer_counter_stay_valid(self):
        record = self.capture([])
        path = provider_usage.paths(self.root, self.turn["turn_id"])[0].with_suffix(".intelligence.json")
        legacy = dict(record)
        legacy["targeted_reads"] = {k: v for k, v in record["targeted_reads"].items()
                                    if k != "history_pointer_reads"}
        path.write_text(json.dumps(legacy), encoding="utf-8")
        reread = intelligence.read(self.root, self.turn["turn_id"], "p")
        self.assertIsNotNone(reread)
        self.assertEqual(reread["targeted_reads"]["coverage"], "helper_only")

    def test_timeout_records_prompt_and_duration_without_fabricating_usage(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            with provider_usage.stdout_capture(self.root, self.turn, context_manifest=self.manifest) as stdout:
                subprocess.run([sys.executable, "-c", "import time; time.sleep(10)"], stdout=stdout, timeout=.2)
        record = intelligence.read(self.root, self.turn["turn_id"], "p")
        self.assertEqual(record["context_manifest"], self.manifest)
        self.assertGreaterEqual(record["duration_seconds"], .2)
        self.assertFalse(record["targeted_reads"]["complete"])

    def test_malformed_unbound_or_replayed_observations_cannot_change_authority(self):
        original = self.capture([])
        self.assertIsNone(intelligence.read(self.root, self.turn["turn_id"], "other"))
        path = provider_usage.paths(self.root, self.turn["turn_id"])[0].with_suffix(".intelligence.json")
        for mutation in ({"targeted_reads": {}}, {"duration_seconds": float("nan")}, {"context_manifest": {"bad": ["x"] * 100}}):
            path.write_text(json.dumps({**original, **mutation}))
            self.assertIsNone(intelligence.read(self.root, self.turn["turn_id"], "p"))
        path.write_text(json.dumps(original))
        data = path.read_bytes()
        self.capture([self.helper_event()])
        self.assertEqual(path.read_bytes(), data)


if __name__ == "__main__":
    unittest.main()
