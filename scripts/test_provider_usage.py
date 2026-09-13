"""Dogfood Fix 04: real stdout transport and durable Runtime recovery tests."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import provider_usage as usage
import supervisor_control as sc
import web_console_supervisor as console
import test_supervisor_observability as core_fixture
import test_web_console_supervisor as console_fixture


def events(fields):
    return [{"type": "thread.started", "thread_id": str(uuid.uuid4())},
            {"type": "turn.started"},
            {"type": "item.completed", "item": {"type": "agent_message",
             "text": '{"token_usage":{"input_tokens":999999}}'}},
            {"type": "turn.completed", "usage": fields}]


class ProviderUsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="provider-usage-")
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.turn = {"turn_id": "supervisor-turn-" + uuid.uuid4().hex, "PROJECT_ID": "p"}

    def captured(self):
        return usage.read_usage(self.root, self.turn["turn_id"], "p")

    def emit(self, stream_events, exitcode=0, timeout=10, sleep=0):
        wire = "".join(json.dumps(e) + "\n" for e in stream_events).encode()
        code = "import sys,time;sys.stdout.buffer.write(sys.stdin.buffer.read());sys.stdout.flush();time.sleep(" + str(sleep) + ");sys.exit(" + str(exitcode) + ")"
        with usage.stdout_capture(self.root, self.turn) as stdout:
            return subprocess.run([sys.executable, "-c", code], input=wire, stdout=stdout, timeout=timeout)

    def test_real_subprocess_transport_reports_only_supplied_fields(self):
        self.assertEqual(self.emit(events({"input_tokens": 1200, "cached_input_tokens": 800,
                                          "output_tokens": 45, "reasoning_output_tokens": 0})).returncode, 0)
        value = self.captured()
        self.assertEqual(value["status"], "provider_reported")
        self.assertEqual(value["cached_input_tokens"], 800)
        self.assertEqual(value["reasoning_output_tokens"], 0)
        self.assertIsNone(value["total_tokens"])
        self.assertNotIn("999999", "".join(p.read_text() for p in self.root.rglob("*.json")))

    def test_partial_fields_and_unknown_extensions_are_not_reinterpreted(self):
        self.emit(events({"output_tokens": 6, "future_tokens": 100, "authorization": "DO_NOT_STORE"}))
        value = self.captured()
        self.assertTrue(value["reported"])
        self.assertIsNone(value["input_tokens"])
        self.assertNotIn("future_tokens", value)
        self.assertNotIn("DO_NOT_STORE", "".join(p.read_text() for p in self.root.rglob("*.json")))

    def test_malformed_unknown_only_and_missing_usage(self):
        for fields in (None, {}, {"future_tokens": 7}, {"input_tokens": True},
                       {"input_tokens": -1}, {"input_tokens": "7"},
                       {"input_tokens": 2, "output_tokens": None}):
            with self.subTest(fields=fields):
                self.turn["turn_id"] = "supervisor-turn-" + uuid.uuid4().hex
                self.emit(events(fields))
                self.assertFalse(self.captured()["reported"])

    def test_model_prose_without_transport_usage_is_unavailable(self):
        self.emit(events({})[:-1])
        self.assertFalse(self.captured()["reported"])

    def test_failure_and_timeout_preserve_already_reported_usage(self):
        self.assertEqual(self.emit(events({"input_tokens": 12}), exitcode=3).returncode, 3)
        self.assertEqual(self.captured()["input_tokens"], 12)
        self.turn["turn_id"] = "supervisor-turn-" + uuid.uuid4().hex
        with self.assertRaises(subprocess.TimeoutExpired):
            self.emit(events({"input_tokens": 15}), timeout=1, sleep=20)
        self.assertEqual(self.captured()["input_tokens"], 15)

    def test_timeout_before_completed_event_is_unavailable(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.emit(events({})[:-1], timeout=1, sleep=20)
        self.assertFalse(self.captured()["reported"])

    def test_storage_failure_does_not_change_process_result(self):
        with mock.patch.object(usage, "write_once", side_effect=OSError("disk unavailable")):
            self.assertEqual(self.emit(events({"input_tokens": 7})).returncode, 0)
        self.assertFalse(self.captured()["reported"])

    def test_duplicate_event_is_never_added_twice(self):
        emitted = events({"input_tokens": 17})
        self.emit(emitted + [emitted[-1]])
        self.assertEqual(self.captured()["input_tokens"], 17)

    def test_bad_order_duplicate_keys_truncation_and_large_items(self):
        for wire in (b'{"type":"turn.completed","usage":{"input_tokens":10}}\n',
                     b'{"type":"thread.started","type":"turn.completed"}\n',
                     b'{"type":"turn.completed"'):
            self.turn["turn_id"] = "supervisor-turn-" + uuid.uuid4().hex
            with usage.stdout_capture(self.root, self.turn) as stdout:
                stdout.write(wire)
            self.assertFalse(self.captured()["reported"])
        self.turn["turn_id"] = "supervisor-turn-" + uuid.uuid4().hex
        self.emit([{"type": "item.completed", "item": {"text": "x" * (usage.MAX_LINE + 1)}}]
                  + events({"input_tokens": 3}))
        self.assertEqual(self.captured()["input_tokens"], 3)

    def test_sidecar_and_turn_identity_integrity(self):
        self.emit(events({"input_tokens": 19}))
        record = console_fixture.sample_record(turn_id=self.turn["turn_id"], PROJECT_ID="p", usage=self.captured())
        path = console_fixture.write_record(self.root, record)
        self.assertTrue(console.load_turn_record(path)["usage"]["reported"])
        _, evidence = usage.paths(self.root, self.turn["turn_id"])
        data = json.loads(evidence.read_text()); data["usage"]["input_tokens"] += 1
        evidence.write_text(json.dumps(data))
        self.assertFalse(console.load_turn_record(path)["usage"]["reported"])
        other = path.with_name("supervisor-turn-other.json")
        shutil.copyfile(path, other)
        with self.assertRaises(console.TurnRecordError):
            console.load_turn_record(other)

    def test_historical_records_are_read_only_and_unavailable(self):
        for reported in (False, True):
            record = console_fixture.sample_record(turn_id="legacy-" + str(reported))
            if reported:
                record["usage"] = {"reported": True, "input_tokens": 100, "output_tokens": 10,
                                   "total_tokens": 110, "source": "codex_output_token_usage", "note": None}
            path = console_fixture.write_record(self.root, record)
            original = path.read_bytes()
            self.assertFalse(console.load_turn_record(path)["usage"]["reported"])
            self.assertEqual(path.read_bytes(), original)


class RecoveryAccountingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = core_fixture.SupervisorObservabilityTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root = self.fixture.root

    def invoke(self, count=100):
        turn = sc.begin_supervisor_turn(self.root, self.fixture.PROJECT)
        wire = "".join(json.dumps(e) + "\n" for e in events({"input_tokens": count,
                                      "output_tokens": 5, "cached_input_tokens": 70}))
        with usage.stdout_capture(self.root, turn) as stdout:
            subprocess.run([sys.executable, "-c", "print(" + repr(wire) + ",end='')"],
                           stdout=stdout, check=True)
        return turn

    def decide(self):
        state = self.fixture.read_state()
        entry = {"decision": "CONTINUE", "reason": "telemetry recovery test"}
        state["decision_history"].append(entry)
        state["last_supervisor_decision"] = dict(entry)
        state["status"] = "COMPLETE"
        self.fixture._json(self.fixture.project / "project_state.json", state)

    def summary(self):
        return console.build_usage_document(self.root, generated_at="now")["usage"]

    def test_crash_before_decision_commit_recovers_one_invocation(self):
        turn = self.invoke(); self.decide()
        result = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(result["decision_committed"])
        original = sc.supervisor_turn_record_path(self.root, turn["turn_id"]).read_bytes()
        for _ in range(3):
            self.assertIsNone(sc.reconcile_inflight_turn(self.root))
        self.assertEqual(self.summary()["totals"]["input_tokens"], 100)
        self.assertEqual(sc.supervisor_turn_record_path(self.root, turn["turn_id"]).read_bytes(), original)

    def test_crash_after_receipt_commit_recovery_keeps_usage_once(self):
        turn = self.invoke(); self.decide()
        def crash(point):
            if point == "supervisor_after_decision_receipt":
                raise SystemExit("simulated crash")
        with mock.patch.object(sc, "_failure_point", side_effect=crash):
            with self.assertRaises(SystemExit):
                sc.finish_supervisor_turn(self.root, turn, processed=True)
        sc.reconcile_inflight_turn(self.root)
        self.assertEqual(self.summary()["totals"]["input_tokens"], 100)
        self.assertEqual(self.summary()["turns_with_reported_usage"], 1)

    def test_second_real_invocation_after_no_decision_is_counted_separately(self):
        first = self.invoke(100)
        self.assertFalse(sc.reconcile_inflight_turn(self.root)["decision_committed"])
        second = self.invoke(200); self.decide()
        sc.finish_supervisor_turn(self.root, second, processed=True)
        self.assertNotEqual(first["turn_id"], second["turn_id"])
        self.assertNotEqual(usage.read_usage(self.root, first["turn_id"], self.fixture.PROJECT)["execution_id"],
                            usage.read_usage(self.root, second["turn_id"], self.fixture.PROJECT)["execution_id"])
        self.assertEqual(self.summary()["totals"]["input_tokens"], 300)
        self.assertEqual(self.summary()["turns_with_reported_usage"], 2)

    def test_zcode_unavailable_without_fabricated_success_fixture(self):
        self.fixture.commit_turn()
        task = self.fixture.task
        data = core_fixture.wire(task)
        origin_record = sc._read_json(sc.candidate_origin_path(self.root), None)
        origin = {key: origin_record[key] for key in (
            "originating_control_revision", "supervisor_turn_id", "decision_receipt_sha256")}
        binding = sc.archive_dispatch(self.root, self.fixture.PROJECT, task, data, origin=origin)
        sc.seal_dispatch_authorization(self.root, {
            "schema_version": 1, **{key: task[key] for key in sc.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": usage.hashlib.sha256(data).hexdigest(),
            "PROJECT_ID": self.fixture.PROJECT, "AUTHORIZED_AT": "2026-09-13T00:00:00+00:00",
            "SUPERVISOR_CONTROL_ORIGIN": origin,
            "SUPERVISOR_DISPATCH_ARCHIVE": {key: binding[key] for key in (
                "schema_version", "metadata_file", "archive_file", "authorization_file", "dispatch_sha256")}})
        # Model-authored receipt extensions cannot become provider telemetry.
        self.fixture._json(self.root / "ZCODE_RECEIPT.json", {"token_usage": {"input_tokens": 999999}})
        result = self.summary()["zcode_usage"]
        self.assertEqual(result["rounds_total"], 1)
        self.assertFalse(result["reported"])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["rounds_reported"], 0)
        self.assertIsNone(result["input_tokens"])


if __name__ == "__main__":
    unittest.main()
