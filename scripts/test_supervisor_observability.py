"""P8 Supervisor-turn observability tests for the Runtime Core.

Covers the SUPERVISOR-TURN-OBSERVABILITY-V1 turn record written by the
control plane at every Supervisor turn boundary, the explicit queued
Supervisor configuration contract (control/supervisor_config.json consumed
only at turn boundaries), and the orchestrator's bounded observation
(effective model/effort, reported token usage, context manifest).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import supervisor_control as sc
import orchestrator as orchestrator_module


def wire(task: dict) -> bytes:
    return (f"MESSAGE_ID: {task['MESSAGE_ID']}\n\n```json\n" +
            json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n").encode("utf-8")


RECORD_SCHEMA = "SUPERVISOR-TURN-OBSERVABILITY-V1"
RECORD_KEYS = {
    "schema", "schema_version", "turn_id", "PROJECT_ID", "invocation",
    "started_at", "finished_at", "duration_seconds", "supervisor_config",
    "usage", "context_manifest", "decision", "dispatch_linkage",
    "intervention_ids", "outcome",
}


class SupervisorObservabilityTests(unittest.TestCase):
    PROJECT = "observability-fixture"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-observability-")
        self.root = Path(self.temp.name)
        self.control = self.root / "control"
        self.project = self.root / "projects" / self.PROJECT
        for path in (self.control, self.project, self.root / "handoff" / "executor_claims",
                     self.root / "handoff" / "completion_ledger",
                     self.root / "handoff" / "quarantine"):
            path.mkdir(parents=True, exist_ok=True)
        self._json(self.control / "ACTIVE_PROJECT.json", {
            "schema_version": 1, "project_id": self.PROJECT,
            "project_root": f"projects/{self.PROJECT}",
        })
        self.task = self.make_task(700130, "nonce-700130")
        self.state = {
            "schema_version": 1, "project_id": self.PROJECT,
            "status": "SUPERVISOR_TURN", "current_task": None,
            "next_message_id": 700130, "decision_history": [],
        }
        self.runtime = {
            "schema_version": 2, "status": "RUNNING",
            "authorized_dispatch": None, "retired_message_ids": [],
            "last_consumed_message_id": 700129,
        }
        self.save_state()
        self.save_runtime()

    def tearDown(self):
        self.temp.cleanup()

    # -- fixture helpers ------------------------------------------------------

    def _json(self, path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")

    def save_state(self):
        self._json(self.project / "project_state.json", self.state)

    def save_runtime(self):
        self._json(self.control / "orchestrator_runtime.json", self.runtime)

    def read_state(self):
        return json.loads(
            (self.project / "project_state.json").read_text(encoding="utf-8"))

    @staticmethod
    def make_task(message_id: int, nonce: str):
        return {
            "MESSAGE_ID": message_id, "TASK_ID": f"T-{message_id}",
            "STAGE_ID": f"S-{message_id}", "ATTEMPT": 1, "NONCE": nonce,
            "OBJECTIVE": "exact bytes fixture", "OUTPUTS": [],
            "CLAIM_PROTOCOL_VERSION": 1,
            "EXECUTOR_PROTOCOL": ["python scripts/executor_claim.py acquire before work"],
            "ISSUED_AT": "2026-09-12T00:00:00+00:00", "MAX_TIME": 3600,
            "SCHEDULER_GRACE_SECONDS": 3600,
        }

    def observation(self, **overrides):
        value = {
            "model": "gpt-5.6-sol", "reasoning_effort": "high",
            "elapsed_seconds": 42.5,
            "usage": {"reported": False, "input_tokens": None,
                      "output_tokens": None, "total_tokens": None,
                      "source": None,
                      "note": "the Codex environment did not report token "
                              "usage for this turn"},
            "context_manifest": {
                "project_state": {"path": "projects/x/project_state.json",
                                  "truncated": False},
                "research_state": {"path": "RESEARCH_STATE.md",
                                   "truncated": True},
            },
        }
        value.update(overrides)
        return value

    def commit_turn(self, *, status="WAITING_EXECUTOR", task=None,
                    decision="CONTINUE", observation="default"):
        """Run one real begin/finish Supervisor turn against the fixture."""
        task = task or self.task
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        state = self.read_state()
        entry = {"decision": decision, "reason": f"fixture {decision.lower()}"}
        state["decision_history"].append(entry)
        state["last_supervisor_decision"] = dict(entry)
        state["status"] = status
        state["current_task"] = ({k: task[k] for k in sc.IDENTITY_KEYS}
                                 if status == "WAITING_EXECUTOR" else None)
        self._json(self.project / "project_state.json", state)
        if status == "WAITING_EXECUTOR":
            (self.root / "TO_ZCODE.md").write_bytes(wire(task))
        observation_value = (self.observation()
                             if observation == "default" else observation)
        kwargs = {}
        if observation_value is not None:
            kwargs["observation"] = observation_value
        sc.finish_supervisor_turn(self.root, turn, processed=True, **kwargs)
        return turn

    def turn_record(self, turn_id: str):
        path = sc.supervisor_turn_record_path(self.root, turn_id)
        return json.loads(path.read_text(encoding="utf-8"))

    # -- queued Supervisor configuration --------------------------------------

    def test_queue_writes_pending_with_revision_and_keeps_active_empty(self):
        result = sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        self.assertTrue(result["queued"])
        self.assertFalse(result["turn_in_flight"])
        doc = sc.load_supervisor_config(self.root)
        self.assertEqual(doc["PROJECT_ID"], self.PROJECT)
        self.assertIsNone(doc["active"])
        self.assertEqual(doc["pending"]["model"], "gpt-6")
        self.assertEqual(doc["pending"]["reasoning_effort"], "HIGH")
        self.assertEqual(doc["pending"]["config_revision"], 1)
        self.assertTrue(doc["pending"]["queued_at"])

    def test_queue_latest_wins_and_increments_revision(self):
        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        second = sc.queue_supervisor_config(
            self.root, {"model": "gpt-5.6-sol", "reasoning_effort": "LOW"})
        self.assertEqual(second["pending"]["config_revision"], 2)
        doc = sc.load_supervisor_config(self.root)
        self.assertEqual(doc["pending"]["model"], "gpt-5.6-sol")
        self.assertEqual(doc["pending"]["reasoning_effort"], "LOW")

    def test_queue_reports_turn_in_flight_truthfully(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        result = sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        self.assertTrue(result["turn_in_flight"])
        doc = sc.load_supervisor_config(self.root)
        self.assertIsNone(doc["active"],
                          "an active turn must never be replaced mid-flight")
        self.assertEqual(doc["pending"]["model"], "gpt-6")
        sc.finish_supervisor_turn(self.root, turn, processed=False)

    def test_queue_rejects_invalid_payload_matrix(self):
        bad_payloads = [
            {"model": "gpt-6"},
            {"reasoning_effort": "HIGH"},
            {"model": "gpt-6", "reasoning_effort": "HIGH", "extra": 1},
            {"model": "", "reasoning_effort": "HIGH"},
            {"model": "   ", "reasoning_effort": "HIGH"},
            {"model": "x" * 81, "reasoning_effort": "HIGH"},
            {"model": "gpt-6\nbad", "reasoning_effort": "HIGH"},
            {"model": 6, "reasoning_effort": "HIGH"},
            {"model": "gpt-6", "reasoning_effort": "ULTRA"},
            {"model": "gpt-6", "reasoning_effort": "high"},
            {"model": "gpt-6", "reasoning_effort": None},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(sc.ControlError):
                    sc.queue_supervisor_config(self.root, payload)
        self.assertFalse(
            sc.supervisor_config_path(self.root).exists(),
            "a refused change must never write configuration state")

    def test_queue_rejects_on_stop(self):
        (self.control / "STOP").write_bytes(b"STOP")
        with self.assertRaises(sc.ControlError):
            sc.queue_supervisor_config(
                self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        self.assertFalse(sc.supervisor_config_path(self.root).exists())

    def test_queue_without_active_project_is_refused(self):
        (self.control / "ACTIVE_PROJECT.json").unlink()
        with self.assertRaises(sc.ControlError):
            sc.queue_supervisor_config(
                self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})

    def test_queue_with_mismatched_project_is_refused(self):
        with self.assertRaises(sc.ControlError):
            sc.queue_supervisor_config(
                self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"},
                project_id="some-other-project")

    def test_begin_consumes_pending_into_turn_snapshot(self):
        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "MEDIUM"})
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        snapshot = turn["supervisor_config"]
        self.assertEqual(snapshot["source"], "queued")
        self.assertEqual(snapshot["model"], "gpt-6")
        self.assertEqual(snapshot["reasoning_effort"], "MEDIUM")
        self.assertEqual(snapshot["config_revision"], 1)
        doc = sc.load_supervisor_config(self.root)
        self.assertIsNone(doc["pending"])
        self.assertEqual(doc["active"]["model"], "gpt-6")
        self.assertEqual(doc["active"]["source_turn_id"], turn["turn_id"])
        self.assertTrue(doc["active"]["applied_at"])
        sc.finish_supervisor_turn(self.root, turn, processed=False)
        # The consumed change stays active: the NEXT turn boundary still
        # reports it as the applied queued configuration.
        later = sc.begin_supervisor_turn(self.root, self.PROJECT)
        self.assertEqual(later["supervisor_config"]["source"], "queued")
        self.assertEqual(later["supervisor_config"]["model"], "gpt-6")
        self.assertEqual(later["supervisor_config"]["reasoning_effort"],
                         "MEDIUM")
        sc.finish_supervisor_turn(self.root, later, processed=False)

    def test_begin_without_pending_reports_fixed_policy_snapshot(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        snapshot = turn["supervisor_config"]
        self.assertEqual(snapshot["source"], "fixed_policy")
        self.assertIsNone(snapshot["model"])
        self.assertIsNone(snapshot["reasoning_effort"])
        self.assertIsNone(snapshot["config_revision"])
        sc.finish_supervisor_turn(self.root, turn, processed=False)

    def test_paused_begin_does_not_consume_pending(self):
        sc.set_pause(self.root, False)
        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        with self.assertRaises(sc.ControlError):
            sc.begin_supervisor_turn(self.root, self.PROJECT)
        doc = sc.load_supervisor_config(self.root)
        self.assertIsNotNone(doc["pending"],
                             "a deferred turn is not an eligible turn")
        self.assertIsNone(doc["active"])
        # This fixture has no scheduler installation. Resume cannot merely
        # clear the pause flag, and must preserve the queued configuration.
        with self.assertRaisesRegex(sc.ControlError, "orchestrator.py is missing"):
            sc.resume(self.root)
        self.assertEqual(sc.pause_status(self.root), "PAUSED")
        self.assertEqual(sc.load_supervisor_config(self.root), doc)

    # -- turn records ----------------------------------------------------------

    def test_finish_writes_record_with_observation_and_linkage(self):
        turn = self.commit_turn(status="WAITING_EXECUTOR",
                                decision="CONTINUE")
        record = self.turn_record(turn["turn_id"])
        self.assertEqual(record["schema"], RECORD_SCHEMA)
        self.assertEqual(record["schema_version"], 1)
        self.assertEqual(set(record), RECORD_KEYS)
        self.assertEqual(record["PROJECT_ID"], self.PROJECT)
        self.assertEqual(record["invocation"], turn["invocation"])
        self.assertTrue(record["started_at"])
        self.assertTrue(record["finished_at"])
        self.assertEqual(record["duration_seconds"], 42.5)
        self.assertEqual(record["supervisor_config"]["source"], "fixed_policy")
        self.assertEqual(record["supervisor_config"]["model"], "gpt-5.6-sol")
        self.assertEqual(record["supervisor_config"]["reasoning_effort"],
                         "high")
        self.assertEqual(record["usage"]["reported"], False)
        self.assertEqual(record["context_manifest"],
                         self.observation()["context_manifest"])
        self.assertTrue(record["decision"]["committed"])
        self.assertEqual(record["decision"]["decision_summary"],
                         "fixture continue")
        self.assertEqual(record["decision"]["resulting_status"],
                         "WAITING_EXECUTOR")
        self.assertEqual(record["decision"]["decision_history_index"], 0)
        self.assertTrue(record["decision"]["receipt_file"])
        self.assertTrue(record["decision"]["receipt_sha256"])
        receipt = json.loads(
            (self.root / record["decision"]["receipt_file"])
            .read_text(encoding="utf-8"))
        self.assertEqual(receipt["turn_id"], turn["turn_id"])
        self.assertEqual(record["dispatch_linkage"]["MESSAGE_ID"], 700130)
        self.assertEqual(record["dispatch_linkage"]["TASK_ID"], "T-700130")
        self.assertEqual(record["dispatch_linkage"]["STAGE_ID"], "S-700130")
        self.assertTrue(record["dispatch_linkage"]["dispatch_sha256"])
        self.assertEqual(record["intervention_ids"], [])
        self.assertTrue(record["outcome"]["committed"])
        self.assertFalse(record["outcome"]["recovered_after_crash"])

    def test_finish_rejects_model_usage_and_preserves_queued_profile(self):
        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        usage = {"reported": True, "input_tokens": 1200, "output_tokens": 340,
                 "total_tokens": 1540, "source": "codex_output_token_usage",
                 "note": None}
        turn = self.commit_turn(
            status="COMPLETE", decision="CONTINUE",
            observation=self.observation(model="gpt-6",
                                         reasoning_effort="high",
                                         usage=usage))
        record = self.turn_record(turn["turn_id"])
        self.assertEqual(record["supervisor_config"]["source"], "queued")
        self.assertEqual(record["supervisor_config"]["model"], "gpt-6")
        self.assertEqual(record["supervisor_config"]["reasoning_effort"],
                         "HIGH")
        self.assertEqual(record["supervisor_config"]["config_revision"], 1)
        self.assertFalse(record["usage"]["reported"])
        self.assertIsNone(record["dispatch_linkage"],
                          "a terminal decision dispatches nothing")

    def test_finish_without_observation_reports_usage_unavailable(self):
        turn = self.commit_turn(status="COMPLETE", observation=None)
        record = self.turn_record(turn["turn_id"])
        self.assertEqual(record["usage"]["reported"], False)
        self.assertIsNone(record["usage"]["input_tokens"])
        self.assertIsNone(record["usage"]["total_tokens"])
        self.assertTrue(record["usage"]["note"])
        self.assertIsNone(record["duration_seconds"])
        self.assertIsNone(record["supervisor_config"]["model"])

    def test_finish_blocked_turn_writes_uncommitted_record(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        sc.finish_supervisor_turn(self.root, turn, processed=False)
        record = self.turn_record(turn["turn_id"])
        self.assertFalse(record["decision"]["committed"])
        self.assertIsNone(record["decision"]["receipt_file"])
        self.assertIsNone(record["decision"]["resulting_status"])
        self.assertFalse(record["outcome"]["committed"])

    def test_record_is_create_only_across_recovery(self):
        # Crash window: the record was durably written but the in-flight
        # accounting did not finish. Recovery must commit the receipt yet
        # never overwrite the existing record.
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        path = sc.supervisor_turn_record_path(self.root, turn["turn_id"])
        marker = {"schema": RECORD_SCHEMA, "schema_version": 1,
                  "turn_id": turn["turn_id"], "marker": "pre-crash record"}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marker), encoding="utf-8")
        state = self.read_state()
        entry = {"decision": "CONTINUE", "reason": "crash-window decision"}
        state["decision_history"].append(entry)
        state["last_supervisor_decision"] = dict(entry)
        state["status"] = "COMPLETE"
        state["current_task"] = None
        self._json(self.project / "project_state.json", state)
        result = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(result["decision_committed"])
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), marker)

    def test_reconcile_recovery_writes_recovered_record(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        state = self.read_state()
        entry = {"decision": "CONTINUE", "reason": "crash-window decision"}
        state["decision_history"].append(entry)
        state["last_supervisor_decision"] = dict(entry)
        state["status"] = "COMPLETE"
        state["current_task"] = None
        self._json(self.project / "project_state.json", state)
        result = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(result["decision_committed"])
        record = self.turn_record(turn["turn_id"])
        self.assertTrue(record["outcome"]["recovered_after_crash"])
        self.assertTrue(record["decision"]["committed"])
        self.assertEqual(record["decision"]["decision_summary"],
                         "crash-window decision")
        self.assertEqual(record["usage"]["reported"], False)

    def test_invalid_observation_fails_closed_before_receipt(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        state = self.read_state()
        entry = {"decision": "CONTINUE", "reason": "fixture continue"}
        state["decision_history"].append(entry)
        state["last_supervisor_decision"] = dict(entry)
        state["status"] = "COMPLETE"
        self._json(self.project / "project_state.json", state)
        bad = self.observation(usage={"reported": True, "input_tokens": None,
                                      "output_tokens": None,
                                      "total_tokens": None, "source": "x",
                                      "note": None})
        with self.assertRaises(sc.ControlError):
            sc.finish_supervisor_turn(self.root, turn, processed=True,
                                      observation=bad)
        self.assertFalse(
            sc.supervisor_turn_record_path(self.root, turn["turn_id"])
            .exists())
        self.assertTrue(
            sc.load_control(self.root).get("inflight_supervisor_turn"),
            "a refused observation must not consume the turn")
        # The turn remains retryable with a valid observation.
        self.assertFalse(sc.finish_supervisor_turn(
            self.root, turn, processed=True,
            observation=self.observation()))

    def test_observation_context_manifest_is_bounded(self):
        # Deeper nesting than one dict level is refused.
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        deep = {"a": {"b": {"c": {"d": 1}}}}
        with self.assertRaises(sc.ControlError):
            sc.finish_supervisor_turn(self.root, turn, processed=False,
                                      observation=self.observation(
                                          context_manifest=deep))
        self.assertTrue(
            sc.load_control(self.root).get("inflight_supervisor_turn"))
        sc.reconcile_inflight_turn(self.root)
        # An oversized string value is refused.
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        with self.assertRaises(sc.ControlError):
            sc.finish_supervisor_turn(
                self.root, turn, processed=False,
                observation=self.observation(
                    context_manifest={"note": "x" * 301}))
        sc.reconcile_inflight_turn(self.root)
        # A list beyond 32 entries is refused.
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        with self.assertRaises(sc.ControlError):
            sc.finish_supervisor_turn(
                self.root, turn, processed=False,
                observation=self.observation(context_manifest={
                    "interventions": [{"intervention_id": f"H-{n:03d}",
                                       "mode": "STEER"} for n in range(33)]}))
        sc.reconcile_inflight_turn(self.root)
        # A valid 32-entry list is accepted verbatim.
        valid = {"interventions": [{"intervention_id": f"H-{n:03d}",
                                    "mode": "STEER"} for n in range(32)]}
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        sc.finish_supervisor_turn(self.root, turn, processed=False,
                                  observation=self.observation(
                                      context_manifest=valid))
        record = self.turn_record(turn["turn_id"])
        self.assertEqual(record["context_manifest"], valid)

    def test_record_lives_under_control_turns(self):
        turn = self.commit_turn(status="COMPLETE")
        path = sc.supervisor_turn_record_path(self.root, turn["turn_id"])
        self.assertEqual(path.parent, self.root / "control" / "supervisor_turns")
        self.assertEqual(path.name, f"{turn['turn_id']}.json")

    # -- CLI --------------------------------------------------------------------

    def test_cli_queue_supervisor_config_subprocess(self):
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / "supervisor_control.py"),
             "--root", str(self.root), "queue-supervisor-config",
             "--model", "gpt-6", "--effort", "HIGH", "--json"],
            capture_output=True, text=True, cwd=str(SCRIPTS), timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(completed.stdout)
        self.assertTrue(document["ok"])
        self.assertEqual(document["supervisor_config"]["pending"]["model"],
                         "gpt-6")
        self.assertFalse(document["supervisor_config"]["turn_in_flight"])

    def test_cli_queue_rejects_invalid_effort(self):
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(SCRIPTS / "supervisor_control.py"),
             "--root", str(self.root), "queue-supervisor-config",
             "--model", "gpt-6", "--effort", "ULTRA", "--json"],
            capture_output=True, text=True, cwd=str(SCRIPTS), timeout=120)
        self.assertEqual(completed.returncode, 2)
        document = json.loads(completed.stdout)
        self.assertFalse(document["ok"])


class SupervisorProfileTests(unittest.TestCase):
    """Orchestrator-side bounded helpers for one Supervisor turn."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-profile-")
        self.root = Path(self.temp.name)
        (self.root / "logs").mkdir(parents=True)
        (self.root / "control").mkdir(parents=True, exist_ok=True)
        self.o = self._configure_orchestrator()

    def tearDown(self):
        self.temp.cleanup()

    def _configure_orchestrator(self):
        o = orchestrator_module
        values = {
            "ROOT": self.root, "CONTROL": self.root / "control",
            "LOGS": self.root / "logs",
            "PROJECT_STATE": self.root / "projects" / "p" / "project_state.json",
            "RUNTIME_STATE": self.root / "control" / "orchestrator_runtime.json",
            "TO_ZCODE": self.root / "TO_ZCODE.md",
            "SUPERVISOR_BRIEF": self.root / "SUPERVISOR_BRIEF.md",
            "CODEX_LAST_OUTPUT": self.root / "CODEX_LAST_OUTPUT.txt",
        }
        for key, value in values.items():
            setattr(o, key, value)
        o.ACTIVE_PROJECT = None
        o.DESKTOP_NOTIFICATIONS_ENABLED = False
        o.USER_NOTIFICATION_CONSOLE_ENABLED = False
        return o

    def test_profile_for_turn_prefers_valid_queued_config(self):
        model, effort, source = self.o.supervisor_profile_for_turn(
            {"source": "queued", "model": "gpt-6",
             "reasoning_effort": "MEDIUM", "config_revision": 3}, {}, "X")
        self.assertEqual((model, effort, source), ("gpt-6", "medium", "queued"))

    def test_profile_for_turn_falls_back_to_fixed_policy(self):
        for snapshot in (None, {}, {"source": "fixed_policy"},
                         {"source": "queued", "model": "  ",
                          "reasoning_effort": "HIGH"},
                         {"source": "queued", "model": "gpt-6",
                          "reasoning_effort": "ULTRA"}):
            with self.subTest(snapshot=snapshot):
                model, effort, source = self.o.supervisor_profile_for_turn(
                    snapshot, {}, "X")
                self.assertEqual((model, effort, source),
                                 (self.o.SUPERVISOR_MODEL,
                                  self.o.SUPERVISOR_REASONING_EFFORT,
                                  "fixed_policy"))

    def test_usage_extraction_matrix(self):
        o = self.o
        output = o.CODEX_LAST_OUTPUT
        output.write_text(json.dumps({
            "token_usage": {"input_tokens": 100, "output_tokens": 20,
                            "total_tokens": 120}}), encoding="utf-8")
        usage = o.supervisor_turn_usage(output)
        self.assertFalse(usage["reported"], "final model JSON is never telemetry")

        cases = [
            ("missing file", None),
            ("not json", "definitely not json"),
            ("no usage block", json.dumps({"message": "hi"})),
            ("partial tokens", json.dumps(
                {"token_usage": {"input_tokens": 100}})),
            ("bool tokens", json.dumps(
                {"token_usage": {"input_tokens": True, "output_tokens": 2,
                                 "total_tokens": 3}})),
            ("negative tokens", json.dumps(
                {"token_usage": {"input_tokens": -1, "output_tokens": 2,
                                 "total_tokens": 1}})),
            ("string tokens", json.dumps(
                {"token_usage": {"input_tokens": "100", "output_tokens": 2,
                                 "total_tokens": 102}})),
        ]
        for label, content in cases:
            with self.subTest(case=label):
                if content is None:
                    output.unlink(missing_ok=True)
                else:
                    output.write_text(content, encoding="utf-8")
                usage = o.supervisor_turn_usage(output)
                self.assertEqual(usage["reported"], False)
                self.assertIsNone(usage["input_tokens"])
                self.assertIsNone(usage["output_tokens"])
                self.assertIsNone(usage["total_tokens"])
                self.assertTrue(usage["note"])

    def test_usage_oversized_output_is_not_reported(self):
        output = self.o.CODEX_LAST_OUTPUT
        output.write_text(json.dumps({
            "token_usage": {"input_tokens": 1, "output_tokens": 1,
                            "total_tokens": 2}}) + " " * (512 * 1024 + 1),
            encoding="utf-8")
        usage = self.o.supervisor_turn_usage(output)
        self.assertFalse(usage["reported"])

    def test_context_manifest_records_provided_classes(self):
        o = self.o
        (o.ROOT / "RESEARCH_STATE.md").write_text(
            "x" * 20000, encoding="utf-8")
        goal = o.ROOT / "PROJECT_GOAL.md"
        goal.write_text("# Goal\n\n## Objective\n\nwin\n", encoding="utf-8")
        state = {"project_id": "p", "project_type": "GENERAL"}
        manifest = o.supervisor_turn_context_manifest(
            reason="EXECUTOR_RESULT_READY",
            event={"committed_receipt_path": "x"},
            goal_state=state, goal_path=goal,
            interventions=[{"intervention_id": "H-001",
                            "mode": "AUDIT"}],
            human_decision=None,
            supervisor_rules_path=o.ROOT / "control" / "rules.md",
            project_state_path=o.PROJECT_STATE,
            research_state_path=o.ROOT / "RESEARCH_STATE.md",
            executor_brief_available=True,
        )
        for key in ("supervisor_rules", "project_state", "research_state",
                    "project_goal", "profile", "mechanical_event",
                    "interventions", "executor_brief"):
            self.assertIn(key, manifest)
        self.assertTrue(manifest["research_state"]["truncated"])
        self.assertFalse(manifest["project_goal"]["truncated"])
        self.assertEqual(manifest["interventions"],
                         [{"intervention_id": "H-001", "mode": "AUDIT"}])
        self.assertEqual(manifest["executor_brief"]["source"], "provided")
        self.assertEqual(manifest["mechanical_event"]["reason"],
                         "EXECUTOR_RESULT_READY")

    def test_invoke_codex_records_observation_with_queued_config(self):
        o = self.o
        runtime = {
            "schema_version": 2, "status": "RUNNING",
            "authorized_dispatch": None, "retired_message_ids": [],
            "last_consumed_message_id": 0,
        }
        o.RUNTIME_STATE.write_text(json.dumps(runtime), encoding="utf-8")

        state_path = o.PROJECT_STATE
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "schema_version": 1, "project_id": "p",
            "status": "SUPERVISOR_TURN", "current_task": None,
            "next_message_id": 1, "decision_history": [],
        }
        state_path.write_text(json.dumps(state), encoding="utf-8")
        # The Runtime configuration contract binds to the active project.
        (o.CONTROL / "ACTIVE_PROJECT.json").write_text(json.dumps(
            {"schema_version": 1, "project_id": "p",
             "project_root": "projects/p"}), encoding="utf-8")

        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "MEDIUM"})

        usage_document = {"token_usage": {"input_tokens": 1500,
                                          "output_tokens": 450,
                                          "total_tokens": 1950}}

        def fake_codex_run(cmd, cwd=None, input=None, timeout=None, stdout=None):
            self.assertIn("gpt-6", cmd)
            self.assertIn("model_reasoning_effort=medium", cmd)
            # The model "writes" its decision result exactly like the real
            # Supervisor does: through the project state file.
            current = json.loads(state_path.read_text(encoding="utf-8"))
            entry = {"decision": "CONTINUE",
                     "reason": "queued-config observability probe"}
            current["decision_history"].append(entry)
            current["last_supervisor_decision"] = dict(entry)
            current["status"] = "COMPLETE"
            current["current_task"] = None
            state_path.write_text(json.dumps(current), encoding="utf-8")
            o.CODEX_LAST_OUTPUT.write_text(
                json.dumps(usage_document), encoding="utf-8")
            self.assertIn("--json", cmd)
            stdout.write((json.dumps({"type": "thread.started", "thread_id": "0199a213-81c0-7800-8aa1-bbab2a035a53"}) + "\n"
                          + json.dumps({"type": "turn.started"}) + "\n"
                          + json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1500,
                              "cached_input_tokens": 1000, "output_tokens": 450,
                              "reasoning_output_tokens": 20}}) + "\n").encode())
            return mock.Mock(returncode=0)

        with mock.patch.object(o, "find_codex", lambda: "codex-stub"), \
                mock.patch.object(o.subprocess, "run",
                                  side_effect=fake_codex_run):
            o.invoke_codex(runtime, "EXECUTOR_RESULT_READY", None)

        records = sorted((self.root / "control" / "supervisor_turns")
                         .glob("*.json"))
        self.assertEqual(len(records), 1)
        record = json.loads(records[0].read_text(encoding="utf-8"))
        self.assertEqual(record["schema"], RECORD_SCHEMA)
        self.assertEqual(record["supervisor_config"]["source"], "queued")
        self.assertEqual(record["supervisor_config"]["model"], "gpt-6")
        self.assertEqual(record["supervisor_config"]["reasoning_effort"],
                         "MEDIUM")
        self.assertTrue(record["usage"]["reported"])
        self.assertIsNone(record["usage"]["total_tokens"])
        self.assertEqual(record["usage"]["cached_input_tokens"], 1000)
        self.assertTrue(record["decision"]["committed"])
        self.assertEqual(record["decision"]["decision_summary"],
                         "queued-config observability probe")
        self.assertIsInstance(record["duration_seconds"], float)
        self.assertIn("supervisor_rules", record["context_manifest"])
        self.assertIn("project_state", record["context_manifest"])
        doc = sc.load_supervisor_config(self.root)
        self.assertIsNone(doc["pending"])
        self.assertEqual(doc["active"]["model"], "gpt-6")


if __name__ == "__main__":
    unittest.main()
