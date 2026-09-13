from __future__ import annotations

import hashlib
import importlib
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import io
import contextlib
from pathlib import Path
from unittest import mock


SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import executor_claim
import executor_completion
import supervisor_control as sc
import orchestrator as orchestrator_module


def wire(task: dict) -> bytes:
    return (f"MESSAGE_ID: {task['MESSAGE_ID']}\n\n```json\n" +
            json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n").encode("utf-8")


class SupervisorControlTests(unittest.TestCase):
    PROJECT = "control-fixture"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-control-")
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
        self.task = self.make_task(700120, "nonce-700120")
        self.state = {
            "schema_version": 1, "project_id": self.PROJECT,
            "status": "SUPERVISOR_TURN", "current_task": None,
            "next_message_id": 700120, "decision_history": [],
        }
        self.runtime = {
            "schema_version": 2, "status": "RUNNING",
            "authorized_dispatch": None, "retired_message_ids": [],
            "last_consumed_message_id": 700119,
        }
        self.save_state()
        self.save_runtime()

    def tearDown(self):
        self.temp.cleanup()

    def _json(self, path: Path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def save_state(self):
        self._json(self.project / "project_state.json", self.state)

    def save_runtime(self):
        self._json(self.control / "orchestrator_runtime.json", self.runtime)

    def read_state(self):
        return json.loads((self.project / "project_state.json").read_text(encoding="utf-8"))

    def read_runtime(self):
        return json.loads((self.control / "orchestrator_runtime.json").read_text(encoding="utf-8"))

    def configured_orchestrator(self):
        o = importlib.reload(orchestrator_module)
        values = {
            "ROOT": self.root, "CONTROL": self.control, "LOGS": self.root / "logs",
            "HANDOFF_ARCHIVE": self.root / "handoff" / "archive",
            "REPORTS": self.root / "reports", "PROJECT_STATE": self.project / "project_state.json",
            "RUNTIME_STATE": self.control / "orchestrator_runtime.json",
            "TO_ZCODE": self.root / "TO_ZCODE.md",
            "SUPERVISOR_BRIEF": self.root / "SUPERVISOR_BRIEF.md",
            "ZCODE_DONE": self.root / "ZCODE_DONE.flag",
            "ZCODE_LAST_PROCESSED": self.root / "ZCODE_LAST_PROCESSED.txt",
            "STOP_FLAG": self.control / "STOP", "HUMAN_REVIEW_FLAG": self.control / "HUMAN_REVIEW",
            "LOCK_FILE": self.control / ".orchestrator.lock",
            "ACTIVE_PROJECT_FILE": self.control / "ACTIVE_PROJECT.json",
            "USER_ATTENTION": self.control / "USER_ATTENTION.json",
            "USER_STATUS_REPORT": self.root / "reports" / "USER_STATUS.md",
            "CODEX_LAST_OUTPUT": self.root / "CODEX_LAST_OUTPUT.txt",
        }
        for key, value in values.items():
            setattr(o, key, value)
        o.ACTIVE_PROJECT = {"project_id": self.PROJECT,
                            "project_root": f"projects/{self.PROJECT}"}
        o.DESKTOP_NOTIFICATIONS_ENABLED = False
        o.USER_NOTIFICATION_CONSOLE_ENABLED = False
        for path in (o.LOGS, o.HANDOFF_ARCHIVE, o.REPORTS):
            path.mkdir(parents=True, exist_ok=True)
        return o

    @staticmethod
    def make_task(message_id: int, nonce: str):
        return {
            "MESSAGE_ID": message_id, "TASK_ID": f"T-{message_id}",
            "STAGE_ID": f"S-{message_id}", "ATTEMPT": 1, "NONCE": nonce,
            "OBJECTIVE": "exact bytes fixture", "OUTPUTS": [],
            "CLAIM_PROTOCOL_VERSION": 1,
            "EXECUTOR_PROTOCOL": ["python scripts/executor_claim.py acquire before work"],
            "ISSUED_AT": "2026-09-11T00:00:00+00:00", "MAX_TIME": 3600,
            "SCHEDULER_GRACE_SECONDS": 3600,
        }

    def archive(self, task=None, data=None, project=None, *, authorized=False):
        task = task or self.task
        data = data or wire(task)
        project_id = self.PROJECT if project is None else project
        origin = None
        if authorized:
            origin = {
                "originating_control_revision": sc.load_control(self.root)["revision"],
                "supervisor_turn_id": f"fixture-turn-{task['MESSAGE_ID']}",
                "decision_receipt_sha256": "a" * 64,
            }
        binding = sc.archive_dispatch(self.root, project_id, task, data, origin=origin)
        if authorized:
            auth = {
                "schema_version": 1, **{k: task[k] for k in sc.IDENTITY_KEYS},
                "TO_ZCODE_SHA256": hashlib.sha256(data).hexdigest(),
                "PROJECT_ID": project_id, "AUTHORIZED_AT": "2026-09-11T00:00:00+00:00",
                "SUPERVISOR_CONTROL_ORIGIN": origin,
                "SUPERVISOR_DISPATCH_ARCHIVE": {
                    key: binding[key] for key in (
                        "schema_version", "metadata_file", "archive_file",
                        "authorization_file", "dispatch_sha256"
                    )
                },
            }
            sc.seal_dispatch_authorization(self.root, auth)
        return binding

    def commit_decision(self, *, status="WAITING_EXECUTOR", task=None, decision="CONTINUE"):
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
        self.assertFalse(sc.finish_supervisor_turn(self.root, turn, processed=True))
        return sc._read_json(sc.candidate_origin_path(self.root), None)

    def authorize_fixture(self, *, claimed=False):
        data = wire(self.task)
        origin_record = self.commit_decision()
        binding = sc.archive_dispatch(self.root, self.PROJECT, self.task, data, origin={
            key: origin_record[key] for key in (
                "originating_control_revision", "supervisor_turn_id", "decision_receipt_sha256"
            )
        })
        self.runtime["authorized_dispatch"] = {
            "schema_version": 1, **{k: self.task[k] for k in sc.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": hashlib.sha256(data).hexdigest(),
            "PROJECT_ID": self.PROJECT, "FENCE_VERSION": 1,
            "EXPIRES_AT": "2099-01-01T00:00:00+00:00",
            "AUTHORIZED_AT": "2026-09-11T00:00:00+00:00",
            "SUPERVISOR_CONTROL_ORIGIN": {
                key: origin_record[key] for key in (
                    "originating_control_revision", "supervisor_turn_id", "decision_receipt_sha256"
                )
            },
            "SUPERVISOR_DISPATCH_ARCHIVE": {
                key: binding[key] for key in (
                    "schema_version", "metadata_file", "archive_file", "authorization_file",
                    "dispatch_sha256"
                )
            },
        }
        sc.seal_dispatch_authorization(self.root, self.runtime["authorized_dispatch"])
        self.state = self.read_state()
        self.save_state()
        self.save_runtime()
        if claimed:
            claim = executor_claim.claim_dir(self.root, self.task["MESSAGE_ID"], self.task["NONCE"])
            claim.mkdir(parents=True)
            self._json(claim / "claim.json", {k: self.task[k] for k in sc.IDENTITY_KEYS})

    def completion_entry(self, status=executor_completion.STATUS_SEALED):
        receipt = {**{k: self.task[k] for k in sc.IDENTITY_KEYS},
                   "STATUS": "COMPLETED", "Key findings": ["authoritative"]}
        commit_id = executor_completion.commit_id_for(self.task["MESSAGE_ID"], self.task["NONCE"])
        entry = {
            "COMPLETION_PROTOCOL_VERSION": 1, "COMMIT_ID": commit_id,
            "STATUS": status,
            **{k: self.task[k] for k in sc.IDENTITY_KEYS}, "PROJECT_ID": self.PROJECT,
            "CLAIM_DIR": "handoff/executor_claims/x.claim",
            "CLAIM_IDENTITY_SHA256": "0" * 64,
            "RECEIPT_SHA256": executor_completion.canonical_json_sha256(receipt),
            "BRIEF_SHA256": executor_completion.sha256_bytes(
                executor_completion.render_brief_bytes(receipt)),
            "STAGING_MANIFEST_SHA256": "1" * 64,
            "COMMITTED_AT": "2026-09-11T01:00:00+00:00",
            "CONSUMED_AT": ("2026-09-11T01:01:00+00:00"
                            if status != executor_completion.STATUS_COMMITTED else None),
            "SEALED_AT": ("2026-09-11T01:02:00+00:00"
                          if status == executor_completion.STATUS_SEALED else None),
            "CONSUMED_ARCHIVE": None, "RECEIPT": receipt,
        }
        self._json(executor_completion.entry_path(self.root, commit_id), entry)
        return entry

    def test_archive_exact_bytes_idempotent_and_restart_visible(self):
        data = wire(self.task) + b"<!-- exact trailing bytes -->\r\n"
        first = self.archive(data=data)
        second = self.archive(data=data)
        self.assertEqual(first["metadata_file"], second["metadata_file"])
        self.assertEqual((self.root / first["archive_file"]).read_bytes(), data)
        self.assertEqual(first["dispatch_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(sc.find_dispatch(self.root, 700120, self.PROJECT)["integrity"],
                         "UNAUTHORIZED")

    def test_archive_identity_with_different_bytes_fails_closed(self):
        self.archive()
        with self.assertRaisesRegex(sc.ControlError, "collision|conflict"):
            self.archive(data=wire(self.task) + b"changed")

    def test_retry_new_message_is_a_separate_record(self):
        retry = self.make_task(700121, "nonce-700121")
        self.archive()
        self.archive(retry, wire(retry))
        self.assertEqual([r["MESSAGE_ID"] for r in sc.list_dispatches(self.root, self.PROJECT)],
                         [700120, 700121])

    def test_multi_project_attribution_isolated(self):
        other = "other-project"
        (self.root / "handoff" / "supervisor_dispatch_archive" / other).mkdir(parents=True)
        self.archive()
        self.archive(project=other)
        self.assertEqual(len(sc.list_dispatches(self.root, self.PROJECT)), 1)
        self.assertEqual(len(sc.list_dispatches(self.root, other)), 1)

    def test_query_detects_hash_mismatch_and_does_not_repair(self):
        binding = self.archive()
        path = self.root / binding["archive_file"]
        path.write_bytes(b"tampered")
        before = path.read_bytes()
        self.assertEqual(sc.find_dispatch(self.root, 700120, self.PROJECT)["integrity"],
                         "CORRUPT")
        self.assertEqual(path.read_bytes(), before)

    def test_specific_query_reports_corrupt_metadata_as_integrity_failure(self):
        binding = self.archive()
        (self.root / binding["metadata_file"]).write_text("{broken", encoding="utf-8")
        self.assertEqual(sc.find_dispatch(self.root, 700120, self.PROJECT)["integrity"],
                         "CORRUPT")

    def test_authorization_archives_before_save_and_binds_same_hash(self):
        self.commit_decision()
        o = importlib.reload(orchestrator_module)
        o.ROOT, o.CONTROL = self.root, self.control
        o.RUNTIME_STATE = self.control / "orchestrator_runtime.json"
        o.TO_ZCODE = self.root / "TO_ZCODE.md"
        o.PROJECT_STATE = self.project / "project_state.json"
        o.ACTIVE_PROJECT = {"project_id": self.PROJECT, "project_root": self.project}
        self.state = self.read_state()
        task = o.register_dispatched_task(self.runtime, self.state)
        auth = self.read_runtime()["authorized_dispatch"]
        self.assertEqual(task["MESSAGE_ID"], 700120)
        self.assertEqual(auth["TO_ZCODE_SHA256"], auth["SUPERVISOR_DISPATCH_ARCHIVE"]["dispatch_sha256"])
        self.assertTrue(sc.verify_archive_binding(self.root, auth)[0])

    def test_archive_failure_prevents_authorization(self):
        self.commit_decision()
        o = importlib.reload(orchestrator_module)
        o.ROOT, o.CONTROL = self.root, self.control
        o.RUNTIME_STATE = self.control / "orchestrator_runtime.json"
        o.TO_ZCODE = self.root / "TO_ZCODE.md"
        o.PROJECT_STATE = self.project / "project_state.json"
        o.ACTIVE_PROJECT = {"project_id": self.PROJECT, "project_root": self.project}
        self.state = self.read_state()
        with mock.patch.object(sc, "archive_dispatch", side_effect=OSError("disk full")):
            o._SUPERVISOR_CONTROL_HELPER = sc
            with self.assertRaisesRegex(OSError, "disk full"):
                o.register_dispatched_task(self.runtime, self.state)
        self.assertIsNone(self.read_runtime()["authorized_dispatch"])

    def test_rejected_candidate_never_archived(self):
        o = importlib.reload(orchestrator_module)
        o.ROOT, o.CONTROL = self.root, self.control
        o.RUNTIME_STATE = self.control / "orchestrator_runtime.json"
        o.TO_ZCODE = self.root / "TO_ZCODE.md"
        o.PROJECT_STATE = self.project / "project_state.json"
        o.ACTIVE_PROJECT = {"project_id": self.PROJECT, "project_root": self.project}
        bad = dict(self.task); bad.pop("OBJECTIVE")
        self.state.update(status="WAITING_EXECUTOR", current_task={k: bad[k] for k in sc.IDENTITY_KEYS})
        self.save_state(); (self.root / "TO_ZCODE.md").write_bytes(wire(bad))
        with self.assertRaises(RuntimeError):
            o.register_dispatched_task(self.runtime, self.state)
        self.assertEqual(sc.list_dispatches(self.root, self.PROJECT), [])

    def test_claim_rejects_broken_archive_binding(self):
        self.authorize_fixture()
        auth = self.runtime["authorized_dispatch"]
        (self.root / auth["SUPERVISOR_DISPATCH_ARCHIVE"]["archive_file"]).write_bytes(b"bad")
        ok, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertFalse(ok)
        self.assertIn("dispatch_archive_hash_mismatch", reason)

    def test_claim_rejects_required_archive_binding_removed(self):
        self.authorize_fixture()
        self.runtime["supervisor_dispatch_archive_required_from_message_id"] = 700120
        self.runtime["authorized_dispatch"].pop("SUPERVISOR_DISPATCH_ARCHIVE")
        self.save_runtime()
        ok, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertFalse(ok)
        self.assertEqual(reason, "dispatch_archive_binding_missing")

    def test_claim_rejects_missing_authorization_seal(self):
        self.authorize_fixture()
        self.runtime["supervisor_dispatch_archive_required_from_message_id"] = 700120
        self.save_runtime()
        binding = self.runtime["authorized_dispatch"]["SUPERVISOR_DISPATCH_ARCHIVE"]
        (self.root / binding["authorization_file"]).unlink()
        ok, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertFalse(ok)
        self.assertEqual(reason, "dispatch_archive_authorization_seal_invalid")

    def test_authorization_seal_is_idempotent(self):
        self.authorize_fixture()
        auth = self.runtime["authorized_dispatch"]
        first = sc.seal_dispatch_authorization(self.root, auth)
        second = sc.seal_dispatch_authorization(self.root, auth)
        self.assertEqual(first, second)

    def test_feedback_comes_from_ledger_not_root_hint(self):
        self.completion_entry()
        (self.root / "SUPERVISOR_BRIEF.md").write_text("fake root hint", encoding="utf-8")
        feedback = sc.find_feedback(self.root, 700120, self.PROJECT)
        self.assertEqual(feedback["integrity"], "OK")
        self.assertEqual(feedback["RECEIPT"]["Key findings"], ["authoritative"])

    def test_steer_injected_and_consumed_exactly_once(self):
        submitted = sc.submit_intervention(self.root, "verbatim α".encode(), "STEER")
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        self.assertEqual(turn["interventions"][0]["instruction_text"], "verbatim α")
        state = self.read_state()
        decision = {"decision": "BLOCKED", "reason": "applied intervention"}
        state["decision_history"].append(decision)
        state["last_supervisor_decision"] = dict(decision)
        state["status"] = "BLOCKED"
        state["current_task"] = None
        self._json(self.project / "project_state.json", state)
        self.assertFalse(sc.finish_supervisor_turn(self.root, turn, processed=True))
        self.assertEqual(sc.begin_supervisor_turn(self.root, self.PROJECT)["interventions"], [])
        # Clean up the second empty in-flight turn as a normal no-write retry.
        sc.finish_supervisor_turn(self.root, sc.load_control(self.root)["inflight_supervisor_turn"], processed=False)
        self.assertEqual(submitted["intervention_id"], sc.list_interventions(self.root, self.PROJECT)[0]["intervention_id"])

    def test_audit_target_resolves_dispatch_completion_and_later_history(self):
        self.archive(authorized=True); self.completion_entry()
        later = self.make_task(700121, "later"); self.archive(later, wire(later), authorized=True)
        sc.submit_intervention(self.root, b"audit old work", "AUDIT", 700120)
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        item = turn["interventions"][0]
        self.assertEqual(item["mode"], "AUDIT")
        self.assertIn("exact_dispatch", item["target_dispatch"])
        self.assertEqual(item["target_executor_feedback"]["MESSAGE_ID"], 700120)
        self.assertEqual(item["later_dispatches"][0]["MESSAGE_ID"], 700121)
        self.assertEqual(sc.find_dispatch(self.root, 700120, self.PROJECT)["integrity"],
                         "AUTHORIZED_VALID")

    def test_unclaimed_or_preauthorization_candidate_is_retired(self):
        self.state.update(status="WAITING_EXECUTOR", current_task={k: self.task[k] for k in sc.IDENTITY_KEYS})
        self.save_state(); (self.root / "TO_ZCODE.md").write_bytes(wire(self.task))
        result = sc.submit_intervention(self.root, b"brake")
        self.assertEqual(result["disposition"], "UNCLAIMED_TASK_RETIRED")
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")
        self.assertIn(700120, self.read_runtime()["retired_message_ids"])
        self.assertFalse((self.root / "TO_ZCODE.md").exists())

    def test_running_stage_holds_intervention_until_next_turn(self):
        self.authorize_fixture(claimed=True)
        result = sc.submit_intervention(self.root, b"after current")
        self.assertEqual(result["disposition"], "PENDING_AFTER_CURRENT_STAGE")
        self.assertEqual(self.read_state()["status"], "WAITING_EXECUTOR")
        self.assertNotIn(700120, self.read_runtime()["retired_message_ids"])

    def test_explicit_intervention_interrupt_retires_running_stage(self):
        self.authorize_fixture(claimed=True)
        result = sc.submit_intervention(self.root, b"stop current", interrupt_current=True)
        self.assertEqual(result["disposition"], "CURRENT_TASK_RETIRED")
        self.assertIn(700120, self.read_runtime()["retired_message_ids"])
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")

    def test_historical_claim_is_not_misclassified_running_and_complete_reopens(self):
        self.authorize_fixture(claimed=True)
        completed = self.read_state(); completed["status"] = "COMPLETE"; completed["current_task"] = None
        self._json(self.project / "project_state.json", completed)
        result = sc.submit_intervention(self.root, b"correct completed history", "AUDIT", 700120)
        self.assertEqual(result["disposition"], "COMPLETE_REOPENED_FOR_SUPERVISOR")
        self.assertNotIn(700120, self.read_runtime()["retired_message_ids"])
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")

    def test_intervention_cannot_bypass_stop(self):
        (self.control / "STOP").write_text("USER_STOP", encoding="utf-8")
        with self.assertRaisesRegex(sc.ControlError, "STOP"):
            sc.submit_intervention(self.root, b"do not reopen")
        self.assertEqual(sc.list_interventions(self.root, self.PROJECT), [])

    def test_concurrent_control_revision_marks_candidate_stale(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        control = sc.load_control(self.root); control["revision"] += 1; sc.save_control(self.root, control)
        self.assertTrue(sc.finish_supervisor_turn(self.root, turn, processed=True))

    def test_inflight_recovery_consumes_only_when_decision_changed_state(self):
        sc.submit_intervention(self.root, b"recover me")
        sc.begin_supervisor_turn(self.root, self.PROJECT)
        changed = self.read_state()
        decision = {"decision": "BLOCKED", "reason": "durable fixture"}
        changed["last_supervisor_decision"] = dict(decision)
        changed["decision_history"].append(decision)
        changed["status"] = "BLOCKED"
        changed["current_task"] = None
        self._json(self.project / "project_state.json", changed)
        result = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(result["decision_committed"])
        self.assertEqual(sc.pending_interventions(self.root, self.PROJECT), [])

    def test_inflight_recovery_requeues_when_no_decision_was_written(self):
        sc.submit_intervention(self.root, b"retry after crash")
        sc.begin_supervisor_turn(self.root, self.PROJECT)
        result = sc.reconcile_inflight_turn(self.root)
        self.assertFalse(result["decision_committed"])
        self.assertEqual(len(sc.pending_interventions(self.root, self.PROJECT)), 1)

    def test_pause_idle_and_resume(self):
        result = sc.set_pause(self.root)
        self.assertEqual(result["status"], "PAUSED")
        resumed = sc.resume(self.root)
        self.assertEqual(resumed["current"]["status"], "RUNNING")
        self.assertFalse((self.control / "STOP").exists())

    def test_pause_unclaimed_retires_before_execution(self):
        self.authorize_fixture()
        result = sc.set_pause(self.root)
        self.assertEqual(result["disposition"], "PAUSED_UNCLAIMED_RETIRED")
        self.assertIn(700120, self.read_runtime()["retired_message_ids"])
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")

    def test_safe_pause_running_and_explicit_interrupt(self):
        self.authorize_fixture(claimed=True)
        safe = sc.set_pause(self.root)
        self.assertEqual(safe["status"], "PENDING_AFTER_CURRENT_STAGE")
        self.assertNotIn(700120, self.read_runtime()["retired_message_ids"])
        interrupted = sc.set_pause(self.root, interrupt_current=True)
        self.assertEqual(interrupted["status"], "PAUSED")
        self.assertIn(700120, self.read_runtime()["retired_message_ids"])

    def test_pause_does_not_retire_a_historical_permanent_claim(self):
        self.authorize_fixture(claimed=True)
        completed = self.read_state(); completed["status"] = "COMPLETE"; completed["current_task"] = None
        self._json(self.project / "project_state.json", completed)
        result = sc.set_pause(self.root)
        self.assertEqual(result["disposition"], "PAUSED_IDLE")
        self.assertNotIn(700120, self.read_runtime()["retired_message_ids"])

    def test_resume_does_not_bypass_stop_or_human_review(self):
        (self.control / "STOP").write_text("USER_STOP", encoding="utf-8")
        with self.assertRaisesRegex(sc.ControlError, "STOP"):
            sc.resume(self.root)
        (self.control / "STOP").unlink()
        self.state["status"] = "HUMAN_REVIEW"; self.save_state()
        with self.assertRaisesRegex(sc.ControlError, "HUMAN_REVIEW"):
            sc.resume(self.root)

    def test_status_and_timeline_are_machine_readable(self):
        self.archive(); self.completion_entry()
        status = sc.current_status(self.root)
        timeline = sc.combined_timeline(self.root, self.PROJECT)
        self.assertEqual(status["PROJECT_ID"], self.PROJECT)
        self.assertCountEqual([event["type"] for event in timeline],
                              ["SUPERVISOR_DISPATCH", "EXECUTOR_COMPLETION"])

    def test_cli_status_json_is_valid(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = sc.main(["--root", str(self.root), "status", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["PROJECT_ID"], self.PROJECT)

    def test_finish_records_supervisor_result_for_audit_history(self):
        sc.submit_intervention(self.root, b"audit result", "AUDIT")
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        self.state["last_supervisor_decision"] = {"decision": "REVISE", "reason": "audit"}
        self.state["decision_history"] = [{"decision": "REVISE", "reason": "audit"}]
        self.state["status"] = "BLOCKED"
        self.state["current_task"] = None
        self.save_state()
        sc.finish_supervisor_turn(self.root, turn, processed=True)
        record = sc.list_interventions(self.root, self.PROJECT)[0]
        self.assertEqual(record["supervisor_result"]["last_supervisor_decision"]["decision"],
                         "REVISE")

    # ---------------------------------------------------------- Astra regressions

    def test_astra_high1_restart_never_authorizes_stale_pre_intervention_candidate(self):
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        sc.submit_intervention(self.root, b"new human direction")
        stale = self.read_state()
        decision = {"decision": "CONTINUE", "reason": "stale turn"}
        stale.update(status="WAITING_EXECUTOR",
                     current_task={k: self.task[k] for k in sc.IDENTITY_KEYS},
                     decision_history=[decision], last_supervisor_decision=dict(decision))
        self._json(self.project / "project_state.json", stale)
        data = wire(self.task)
        (self.root / "TO_ZCODE.md").write_bytes(data)
        origin = {"originating_control_revision": 0,
                  "supervisor_turn_id": turn["turn_id"],
                  "decision_receipt_sha256": "b" * 64}
        binding = sc.archive_dispatch(self.root, self.PROJECT, self.task, data, origin=origin)
        self.runtime["authorized_dispatch"] = {
            "schema_version": 1, **{k: self.task[k] for k in sc.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": hashlib.sha256(data).hexdigest(), "PROJECT_ID": self.PROJECT,
            "FENCE_VERSION": 1, "EXPIRES_AT": "2099-01-01T00:00:00+00:00",
            "AUTHORIZED_AT": "2026-09-11T00:00:00+00:00",
            "SUPERVISOR_CONTROL_ORIGIN": origin,
            "SUPERVISOR_DISPATCH_ARCHIVE": {key: binding[key] for key in (
                "schema_version", "metadata_file", "archive_file", "authorization_file",
                "dispatch_sha256")},
        }
        sc.seal_dispatch_authorization(self.root, self.runtime["authorized_dispatch"])
        self.save_runtime()

        recovered = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(recovered["stale"])
        self.assertFalse(recovered["decision_committed"])
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")
        self.assertIn(self.task["MESSAGE_ID"], self.read_runtime()["retired_message_ids"])
        self.assertNotEqual(executor_claim.acquire(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS]), executor_claim.EXIT_ACQUIRED)
        self.assertEqual(len(sc.pending_interventions(self.root, self.PROJECT)), 1)

    def test_astra_high2_pause_commit_blocks_turn_registration_and_claim(self):
        self.authorize_fixture()

        def crash(name):
            if name == "pause_after_control_commit":
                raise SystemExit("simulated crash")

        with mock.patch.object(sc, "_failure_point", side_effect=crash):
            with self.assertRaises(SystemExit):
                sc.set_pause(self.root)
        self.assertEqual(sc.pause_status(self.root), "PAUSED")
        with self.assertRaisesRegex(sc.ControlError, "paused"):
            sc.begin_supervisor_turn(self.root, self.PROJECT)
        ok, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertFalse(ok)
        self.assertIn(reason, {"runtime_paused", "message_id_retired"})
        self.assertIn(self.task["MESSAGE_ID"], self.read_runtime()["retired_message_ids"])
        o = self.configured_orchestrator()
        with self.assertRaisesRegex(RuntimeError, "paused"):
            o.register_dispatched_task(self.read_runtime(), self.read_state(),
                                       allow_same_identity=True)

    def test_astra_high1_committed_intervention_event_not_replayed_after_crash(self):
        event = {"type": "EXECUTOR_RESULT_READY", "message_id": 700119}
        self.runtime["pending_supervisor_event"] = {
            "reason": "EXECUTOR_RESULT_READY", "event": event,
            "recorded_at": "2026-09-11T00:00:00+00:00",
        }
        self.save_runtime()
        sc.submit_intervention(self.root, b"one corrective decision")
        turn = sc.begin_supervisor_turn(
            self.root, self.PROJECT,
            {"reason": "EXECUTOR_RESULT_READY", "event": event})
        state = self.read_state()
        decision = {"decision": "BLOCKED", "reason": "committed correction"}
        state.update(status="BLOCKED", current_task=None,
                     decision_history=[decision], last_supervisor_decision=dict(decision))
        self._json(self.project / "project_state.json", state)

        def crash(name):
            if name == "supervisor_after_event_consumed":
                raise SystemExit(name)

        with mock.patch.object(sc, "_failure_point", side_effect=crash):
            with self.assertRaises(SystemExit):
                sc.finish_supervisor_turn(self.root, turn, processed=True)
        recovered = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(recovered["decision_committed"])
        self.assertIsNone(self.read_runtime().get("pending_supervisor_event"))
        self.assertEqual(len(self.read_state()["decision_history"]), 1)
        records = sc.list_interventions(self.root, self.PROJECT)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["status"], "CONSUMED")

    def test_astra_high3_intervention_transaction_recovers_every_durable_boundary(self):
        points = [
            "intervention_after_journal", "intervention_after_control_revision",
            "intervention_after_instruction", "intervention_after_metadata",
            "intervention_after_pending_status", "intervention_after_project_state",
            "intervention_after_runtime_state", "intervention_after_control_bookkeeping",
            "intervention_after_commit_receipt",
        ]
        for point in points:
            with self.subTest(point=point):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    fixture.state.update(
                        status="WAITING_EXECUTOR",
                        current_task={k: fixture.task[k] for k in sc.IDENTITY_KEYS})
                    fixture.save_state()
                    (fixture.root / "TO_ZCODE.md").write_bytes(wire(fixture.task))

                    def crash(name):
                        if name == point:
                            raise SystemExit(point)

                    with mock.patch.object(sc, "_failure_point", side_effect=crash):
                        with self.assertRaises(SystemExit):
                            sc.submit_intervention(fixture.root, "精确 🧭".encode("utf-8"))
                    sc.reconcile_control_transactions(fixture.root)
                    control = sc.load_control(fixture.root)
                    records = sc.list_interventions(fixture.root, fixture.PROJECT)
                    self.assertEqual(control["revision"], 1)
                    self.assertEqual(len(records), 1)
                    self.assertEqual(records[0]["instruction_text"], "精确 🧭")
                    self.assertEqual(records[0]["status"], "PENDING")
                    self.assertEqual(fixture.read_state()["status"], "SUPERVISOR_TURN")
                    self.assertEqual(fixture.read_runtime()["retired_message_ids"], [700120])
                    self.assertEqual(len(list((fixture.root / "handoff" /
                                              "supervisor_interventions" /
                                              fixture.PROJECT).glob("intervention-*"))), 1)
                finally:
                    fixture.tearDown()

    def test_astra_high4_stale_runtime_save_cannot_erase_retirement(self):
        self.authorize_fixture()
        stale_runtime = json.loads(json.dumps(self.read_runtime()))
        stale_state = json.loads(json.dumps(self.read_state()))
        result = sc.submit_intervention(self.root, b"retire before claim")
        self.assertEqual(result["disposition"], "UNCLAIMED_TASK_RETIRED")
        o = self.configured_orchestrator()
        stale_runtime["status"] = "RUNNING"
        o.save_runtime(stale_runtime)
        persisted = self.read_runtime()
        self.assertIn(700120, persisted["retired_message_ids"])
        self.assertEqual(len(persisted["executor_retirements"]), 1)
        with self.assertRaises(RuntimeError):
            o.register_dispatched_task(stale_runtime, stale_state, allow_same_identity=True)
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")
        self.assertIn(700120, self.read_runtime()["retired_message_ids"])

    def test_astra_high5_both_interrupt_completion_orders_for_pause_and_intervention(self):
        commands = {
            "intervention": lambda fixture: sc.submit_intervention(
                fixture.root, b"interrupt", interrupt_current=True),
            "pause": lambda fixture: sc.set_pause(fixture.root, interrupt_current=True),
        }
        for name, command in commands.items():
            with self.subTest(command=name, order="interrupt-first"):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    fixture.authorize_fixture(claimed=True)
                    command(fixture)
                    with self.assertRaises(executor_completion.CompletionError):
                        executor_completion._check_authorization(
                            fixture.read_runtime(), fixture.task)
                    self.assertFalse(executor_completion.lookup_entries(
                        fixture.root, fixture.task["MESSAGE_ID"]))
                    self.assertEqual(fixture.read_state()["status"], "SUPERVISOR_TURN")
                finally:
                    fixture.tearDown()
            with self.subTest(command=name, order="completion-first"):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    fixture.authorize_fixture(claimed=True)
                    entry = fixture.completion_entry(executor_completion.STATUS_COMMITTED)
                    executor_completion.publish_compatibility_artifacts(
                        fixture.root, entry, include_done=True)
                    result = command(fixture)
                    self.assertEqual(result["disposition"], "COMPLETION_COMMITTED_WINS")
                    self.assertNotIn(700120, fixture.read_runtime()["retired_message_ids"])
                    self.assertEqual(fixture.read_state()["status"], "WAITING_EXECUTOR")
                    self.assertFalse(sc.current_status(fixture.root)["active_task_claimed"])
                    o = fixture.configured_orchestrator()
                    seen, event = o.consume_executor_receipt(o.load_runtime())
                    self.assertTrue(seen)
                    self.assertEqual(event["message_id"], 700120)
                    self.assertEqual(executor_completion.lookup_entries(
                        fixture.root, 700120)[0]["STATUS"],
                        executor_completion.STATUS_CONSUMED)
                finally:
                    fixture.tearDown()

    def test_astra_high5_pause_interrupt_recovers_every_durable_boundary(self):
        points = [
            "pause_after_control_commit", "pause_after_project_state",
            "pause_after_runtime_state", "pause_after_transaction_commit",
        ]
        for point in points:
            with self.subTest(point=point):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    fixture.authorize_fixture(claimed=True)

                    def crash(name):
                        if name == point:
                            raise SystemExit(point)

                    with mock.patch.object(sc, "_failure_point", side_effect=crash):
                        with self.assertRaises(SystemExit):
                            sc.set_pause(fixture.root, interrupt_current=True)
                    sc.reconcile_control_transactions(fixture.root)
                    runtime = fixture.read_runtime()
                    self.assertEqual(runtime["retired_message_ids"], [700120])
                    self.assertEqual(len(runtime["executor_retirements"]), 1)
                    self.assertEqual(fixture.read_state()["status"], "SUPERVISOR_TURN")
                    self.assertNotIn(
                        "transaction", sc.load_control(fixture.root)["pause"])
                    with self.assertRaises(executor_completion.CompletionError):
                        executor_completion._check_authorization(runtime, fixture.task)
                finally:
                    fixture.tearDown()

    def test_astra_high5_intervention_interrupt_recovers_every_durable_boundary(self):
        points = [
            "intervention_after_journal", "intervention_after_control_revision",
            "intervention_after_instruction", "intervention_after_metadata",
            "intervention_after_pending_status", "intervention_after_project_state",
            "intervention_after_runtime_state", "intervention_after_control_bookkeeping",
            "intervention_after_commit_receipt",
        ]
        for point in points:
            with self.subTest(point=point):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    fixture.authorize_fixture(claimed=True)

                    def crash(name):
                        if name == point:
                            raise SystemExit(point)

                    with mock.patch.object(sc, "_failure_point", side_effect=crash):
                        with self.assertRaises(SystemExit):
                            sc.submit_intervention(
                                fixture.root, b"interrupt", interrupt_current=True)
                    sc.reconcile_control_transactions(fixture.root)
                    runtime = fixture.read_runtime()
                    self.assertEqual(runtime["retired_message_ids"], [700120])
                    self.assertEqual(len(runtime["executor_retirements"]), 1)
                    self.assertEqual(fixture.read_state()["status"], "SUPERVISOR_TURN")
                    self.assertEqual(len(sc.pending_interventions(
                        fixture.root, fixture.PROJECT)), 1)
                    with self.assertRaises(executor_completion.CompletionError):
                        executor_completion._check_authorization(runtime, fixture.task)
                finally:
                    fixture.tearDown()

    def test_astra_high6_lowercase_identity_preserves_running_and_rejects_malformed(self):
        for command in ("intervention", "pause"):
            for casing in ("uppercase", "lowercase"):
                with self.subTest(command=command, casing=casing):
                    fixture = self.__class__("test_pause_idle_and_resume")
                    fixture.setUp()
                    try:
                        fixture.authorize_fixture(claimed=True)
                        if casing == "lowercase":
                            state = fixture.read_state()
                            state["current_task"] = {
                                key.lower(): fixture.task[key] for key in sc.IDENTITY_KEYS}
                            fixture._json(fixture.project / "project_state.json", state)
                        result = (sc.submit_intervention(fixture.root, b"safe")
                                  if command == "intervention" else sc.set_pause(fixture.root))
                        self.assertIn("PENDING_AFTER_CURRENT_STAGE", result["disposition"])
                        self.assertNotIn(700120, fixture.read_runtime()["retired_message_ids"])
                        self.assertEqual(fixture.read_state()["status"], "WAITING_EXECUTOR")
                    finally:
                        fixture.tearDown()
        self.state.update(status="WAITING_EXECUTOR", current_task={
            "message_id": None, "task_id": "T", "stage_id": "S", "attempt": 1,
            "nonce": "n"})
        self.save_state()
        with self.assertRaisesRegex(sc.ControlError, "MESSAGE_ID"):
            sc.submit_intervention(self.root, b"must fail")
        self.assertEqual(self.read_runtime()["retired_message_ids"], [])
        self.assertEqual(sc.list_interventions(self.root, self.PROJECT), [])

    def test_astra_high8_legacy_authorization_migration_and_running_policy(self):
        data = wire(self.task)
        (self.root / "TO_ZCODE.md").write_bytes(data)
        self.state.update(status="WAITING_EXECUTOR",
                          current_task={k: self.task[k] for k in sc.IDENTITY_KEYS})
        self.save_state()
        legacy = {
            "schema_version": 1, **{k: self.task[k] for k in sc.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": hashlib.sha256(data).hexdigest(),
            "AUTHORIZED_AT": "2026-09-11T00:00:00+00:00", "FENCE_VERSION": 1,
            "PROJECT_ID": self.PROJECT, "EXPIRES_AT": "2099-01-01T00:00:00+00:00",
        }
        self.runtime["authorized_dispatch"] = legacy
        self.save_runtime()
        ok, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertFalse(ok)
        self.assertEqual(reason, "dispatch_archive_binding_missing")

        # Explicit re-registration before v1.2 startup initialization migrates it.
        sc.control_path(self.root).unlink(missing_ok=True)
        o = self.configured_orchestrator()
        migrated = dict(self.runtime)
        o.register_dispatched_task(migrated, self.read_state(), allow_same_identity=True)
        self.assertTrue(sc.verify_archive_binding(
            self.root, self.read_runtime()["authorized_dispatch"])[0])
        ok, _ = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS])
        self.assertTrue(ok)

        # An already-existing permanent legacy claim is completion-only recovery.
        claim_path = executor_claim.claim_dir(self.root, self.task["MESSAGE_ID"], self.task["NONCE"])
        claim_path.mkdir(exist_ok=True)
        token = "legacy-owner-token"
        self._json(claim_path / "claim.json", {
            **{k: self.task[k] for k in sc.IDENTITY_KEYS},
            "CLAIM_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest(),
        })
        recovered_runtime = self.read_runtime()
        recovered_runtime["authorized_dispatch"].pop("SUPERVISOR_DISPATCH_ARCHIVE")
        recovered_runtime["authorized_dispatch"].pop("SUPERVISOR_CONTROL_ORIGIN")
        self._json(self.control / "orchestrator_runtime.json", recovered_runtime)
        # The compatibility override itself is not authority; a matching
        # permanent pre-upgrade claim must actually exist.
        claim_path = executor_claim.claim_dir(
            self.root, self.task["MESSAGE_ID"], self.task["NONCE"])
        shutil.rmtree(claim_path)
        ok, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS],
            allow_legacy_claimed_recovery=True)
        self.assertFalse(ok)
        self.assertEqual(reason, "legacy_claim_missing_or_invalid")
        claim_path.mkdir(exist_ok=True)
        self._json(claim_path / "claim.json", {
            **{k: self.task[k] for k in sc.IDENTITY_KEYS},
            "CLAIM_TOKEN_SHA256": hashlib.sha256(token.encode()).hexdigest(),
        })
        ok, _ = executor_claim.verify_authorized_dispatch(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS],
            allow_legacy_claimed_recovery=True)
        self.assertTrue(ok)
        self.assertNotEqual(executor_claim.acquire(
            self.root, *[self.task[k] for k in sc.IDENTITY_KEYS]),
            executor_claim.EXIT_ACQUIRED)

    def test_astra_high7_completed_safe_pause_restart_resumes_deferred_once(self):
        self.authorize_fixture(claimed=True)
        self.assertEqual(sc.set_pause(self.root)["status"], "PENDING_AFTER_CURRENT_STAGE")
        entry = self.completion_entry(executor_completion.STATUS_COMMITTED)
        executor_completion.publish_compatibility_artifacts(self.root, entry, include_done=True)
        o = self.configured_orchestrator()
        runtime = o.load_runtime()
        seen, event = o.consume_executor_receipt(runtime)
        self.assertTrue(seen)
        sc.settle_pause(self.root, "CURRENT_STAGE_COMPLETED")
        runtime["status"] = "PAUSED"
        runtime["paused_deferred_event"] = event
        o.save_runtime(runtime)
        self.assertFalse(sc.current_status(self.root)["active_task_claimed"])
        self.assertEqual(sc.resume(self.root)["current"]["status"], "RUNNING")

        calls = []

        def supervisor(runtime_value, reason, event_value):
            calls.append((reason, event_value["message_id"]))
            state = o.read_project_state()
            state["status"] = "BLOCKED"
            state["current_task"] = None
            o.atomic_json(o.PROJECT_STATE, state)

        with mock.patch.object(o, "invoke_codex", side_effect=supervisor), \
             mock.patch.object(o, "enforce_terminal_verification_gate",
                               side_effect=lambda runtime_value, state, source: (state, None)), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 5)
        self.assertEqual(calls, [("EXECUTOR_RESULT_READY", 700120)])
        self.assertIsNone(o.load_runtime().get("paused_deferred_event"))
        self.assertEqual(executor_completion.lookup_entries(
            self.root, 700120)[0]["STATUS"], executor_completion.STATUS_SEALED)
        claim_dirs = list((self.root / "handoff" / "executor_claims").glob("*.claim"))
        self.assertEqual(len(claim_dirs), 1)

    def test_astra_medium1_only_valid_decision_transaction_consumes_intervention(self):
        mutations = {
            "successful_noop": lambda state: state,
            "timestamp_only": lambda state: {**state, "updated_at": "2099-01-01T00:00:00+00:00"},
            "partial_invalid": lambda state: {
                **state, "last_supervisor_decision": {"decision": "CONTINUE"}},
        }
        for name, mutate in mutations.items():
            with self.subTest(case=name):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    sc.submit_intervention(fixture.root, b"must remain pending")
                    turn = sc.begin_supervisor_turn(fixture.root, fixture.PROJECT)
                    fixture._json(fixture.project / "project_state.json",
                                  mutate(fixture.read_state()))
                    self.assertTrue(sc.finish_supervisor_turn(
                        fixture.root, turn, processed=True))
                    self.assertEqual(len(sc.pending_interventions(
                        fixture.root, fixture.PROJECT)), 1)
                finally:
                    fixture.tearDown()

        sc.submit_intervention(self.root, b"invalid candidate remains pending")
        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        state = self.read_state()
        decision = {"decision": "CONTINUE", "reason": "invalid candidate"}
        state.update(status="WAITING_EXECUTOR",
                     current_task={k: self.task[k] for k in sc.IDENTITY_KEYS},
                     decision_history=[decision], last_supervisor_decision=dict(decision))
        self._json(self.project / "project_state.json", state)
        (self.root / "TO_ZCODE.md").write_text("```json\n{}\n```\n", encoding="utf-8")
        self.assertTrue(sc.finish_supervisor_turn(self.root, turn, processed=True))
        self.assertEqual(len(sc.pending_interventions(self.root, self.PROJECT)), 1)
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")

    def test_astra_medium1_crash_before_and_after_decision_receipt(self):
        sc.submit_intervention(self.root, b"retry before commit")
        sc.begin_supervisor_turn(self.root, self.PROJECT)
        recovered = sc.reconcile_inflight_turn(self.root)
        self.assertFalse(recovered["decision_committed"])
        self.assertEqual(len(sc.pending_interventions(self.root, self.PROJECT)), 1)

        turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        state = self.read_state()
        decision = {"decision": "BLOCKED", "reason": "committed exactly once"}
        state.update(status="BLOCKED", current_task=None,
                     decision_history=[decision], last_supervisor_decision=dict(decision))
        self._json(self.project / "project_state.json", state)

        def crash(name):
            if name == "supervisor_after_decision_receipt":
                raise SystemExit("crash after decision receipt")

        with mock.patch.object(sc, "_failure_point", side_effect=crash):
            with self.assertRaises(SystemExit):
                sc.finish_supervisor_turn(self.root, turn, processed=True)
        recovered = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(recovered["decision_committed"])
        self.assertEqual(sc.pending_interventions(self.root, self.PROJECT), [])
        record = sc.list_interventions(self.root, self.PROJECT)[0]
        self.assertEqual(record["status"], "CONSUMED")
        self.assertEqual(record["supervisor_result"]["last_supervisor_decision"]["decision"],
                         "BLOCKED")

    def test_astra_medium2_stale_complete_and_blocked_requeue_new_intervention(self):
        for terminal in ("COMPLETE", "BLOCKED"):
            with self.subTest(status=terminal):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    turn = sc.begin_supervisor_turn(fixture.root, fixture.PROJECT)
                    sc.submit_intervention(fixture.root, b"newer direction")
                    state = fixture.read_state()
                    decision = {"decision": terminal, "reason": "stale terminal"}
                    state.update(status=terminal, current_task=None,
                                 decision_history=[decision],
                                 last_supervisor_decision=dict(decision))
                    fixture._json(fixture.project / "project_state.json", state)
                    self.assertTrue(sc.finish_supervisor_turn(
                        fixture.root, turn, processed=True))
                    self.assertEqual(fixture.read_state()["status"], "SUPERVISOR_TURN")
                    self.assertEqual(len(sc.pending_interventions(
                        fixture.root, fixture.PROJECT)), 1)
                finally:
                    fixture.tearDown()

    def test_astra_medium3_task_query_enforces_full_archive_trust_boundary(self):
        binding = self.archive(authorized=True)
        self.assertEqual(sc.find_dispatch(
            self.root, 700120, self.PROJECT)["integrity"], "AUTHORIZED_VALID")
        before = {path: path.read_bytes() for path in
                  (self.root / "handoff" / "supervisor_dispatch_archive").rglob("*")
                  if path.is_file()}
        sc.list_dispatches(self.root, self.PROJECT)
        self.assertEqual(before, {path: path.read_bytes() for path in
                                 (self.root / "handoff" /
                                  "supervisor_dispatch_archive").rglob("*")
                                 if path.is_file()})

        cases = ("missing_seal", "bad_schema", "path_escape", "bad_sha", "bad_identity")
        for case in cases:
            with self.subTest(case=case):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    item = fixture.archive(authorized=True)
                    meta_path = fixture.root / item["metadata_file"]
                    seal_path = fixture.root / item["authorization_file"]
                    if case == "missing_seal":
                        seal_path.unlink()
                        expected = "UNAUTHORIZED"
                    else:
                        meta = json.loads(meta_path.read_text(encoding="utf-8"))
                        if case == "bad_schema":
                            meta["unexpected"] = True
                        elif case == "path_escape":
                            sentinel = fixture.root / "sentinel.txt"
                            sentinel.write_text("MUST_NOT_APPEAR", encoding="utf-8")
                            meta["archive_file"] = "sentinel.txt"
                        elif case == "bad_sha":
                            meta["dispatch_sha256"] = "0" * 64
                        elif case == "bad_identity":
                            meta["TASK_ID"] = "other"
                        fixture._json(meta_path, meta)
                        expected = "CORRUPT"
                    record = sc.find_dispatch(fixture.root, 700120, fixture.PROJECT)
                    self.assertEqual(record["integrity"], expected)
                    self.assertNotIn("exact_dispatch", record)
                    self.assertNotIn("MUST_NOT_APPEAR", json.dumps(record))
                finally:
                    fixture.tearDown()

        incomplete_dir = (self.root / "handoff" / "supervisor_dispatch_archive" /
                          self.PROJECT)
        (incomplete_dir / "dispatch-700999-aaaaaaaaaaaaaaaaaaaaaaaa.md").write_text(
            "orphan", encoding="utf-8")
        incomplete = next(record for record in sc.list_dispatches(self.root, self.PROJECT)
                          if record.get("MESSAGE_ID") == 700999)
        self.assertEqual(incomplete["integrity"], "INCOMPLETE")

    def test_astra_medium4_and_5_actual_powershell_wrappers_preserve_text_and_json(self):
        shells = [name for name in ("powershell", "pwsh") if shutil.which(name)]
        if not shells:
            self.skipTest("PowerShell is unavailable")
        wrapper = REPO / "REQUEST_SUPERVISOR_INTERVENTION.ps1"
        resume_wrapper = REPO / "RESUME_AGENT_SYSTEM.ps1"
        (self.root / "scripts").mkdir(exist_ok=True)
        for name in ("supervisor_control.py", "executor_fence.py", "executor_completion.py",
                     "executor_claim.py"):
            shutil.copy2(SCRIPTS / name, self.root / "scripts" / name)
        shutil.copy2(wrapper, self.root / wrapper.name)
        shutil.copy2(resume_wrapper, self.root / resume_wrapper.name)
        payloads = [
            'Keep "A" and \'B\' intact',
            "line one\r\nline two\n末尾 空格  ",
            "中文与 emoji 🧭😀 trailing\\",
        ]
        for shell in shells:
            for index, payload in enumerate(payloads):
                with self.subTest(shell=shell, payload=index):
                    env = dict(os.environ)
                    env["GAR_INTERVENTION_TEXT"] = payload
                    command = (f"& '{self.root / wrapper.name}' "
                               "-Text $env:GAR_INTERVENTION_TEXT -Json")
                    result = subprocess.run(
                        [shell, "-NoProfile", "-Command", command],
                        cwd=self.root, env=env, capture_output=True, timeout=30)
                    self.assertEqual(result.returncode, 0,
                                     result.stderr.decode(errors="replace"))
                    parsed = json.loads(result.stdout.decode("ascii"))
                    record = next(item for item in sc.list_interventions(
                        self.root, self.PROJECT)
                                  if item["intervention_id"] == parsed["intervention_id"])
                    self.assertEqual(record["instruction_text"], payload)
            instruction = self.root / f"instruction-{shell}.txt"
            exact = "file \"quote\"\n文件 🧪\\".encode("utf-8")
            instruction.write_bytes(exact)
            result = subprocess.run(
                [shell, "-NoProfile", "-File", str(self.root / wrapper.name),
                 "-InstructionFile", str(instruction), "-Json"],
                cwd=self.root, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            parsed = json.loads(result.stdout.decode("ascii"))
            record = next(item for item in sc.list_interventions(self.root, self.PROJECT)
                          if item["intervention_id"] == parsed["intervention_id"])
            self.assertEqual((self.root / record["instruction_file"]).read_bytes(), exact)

            sc.set_pause(self.root)
            result = subprocess.run(
                [shell, "-NoProfile", "-File", str(self.root / resume_wrapper.name),
                 "-Json", "-NoStart"], cwd=self.root, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertIsInstance(json.loads(result.stdout.decode("ascii")), dict)

            # Error paths retain the same one-document machine contract.
            review_flag = self.root / "control" / "HUMAN_REVIEW"
            review_flag.write_text("fixture\n", encoding="utf-8")
            result = subprocess.run(
                [shell, "-NoProfile", "-File", str(self.root / resume_wrapper.name),
                 "-Json", "-NoStart"], cwd=self.root, capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 2)
            error_doc = json.loads(result.stdout.decode("ascii"))
            self.assertFalse(error_doc["ok"])
            review_flag.unlink()

    def test_astra_medium5_json_is_ascii_safe_one_document_under_gbk(self):
        sc.submit_intervention(self.root, "非BMP 😀 exact".encode("utf-8"))
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp936"
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "supervisor_control.py"),
            "--root", str(self.root), "interventions", "--json",
        ], capture_output=True, env=env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertTrue(result.stdout.isascii())
        parsed = json.loads(result.stdout.decode("ascii"))
        self.assertEqual(parsed[0]["instruction_text"], "非BMP 😀 exact")

        stream = io.StringIO()
        with mock.patch("sys.stdout", stream):
            code = sc.main([
                "--root", str(self.root), "feedback", "--message-id", "999999", "--json"
            ])
        self.assertEqual(code, 2)
        error_doc = json.loads(stream.getvalue())
        self.assertFalse(error_doc["ok"])
        stream.getvalue().encode("ascii")

    def test_second_recheck_R1_committed_receipt_survives_newer_revision_without_replay(self):
        old_event = {"type": "INTERVENTION_EVENT", "sequence": "A"}
        self.runtime["pending_supervisor_event"] = {
            "reason": "SUPERVISOR_TURN", "event": old_event,
            "recorded_at": "2026-09-11T00:00:00+00:00",
            "decision_attempts": 0, "retry_exhausted": False,
        }
        self.save_runtime()
        request_a = sc.submit_intervention(self.root, b"intervention A")
        turn = sc.begin_supervisor_turn(
            self.root, self.PROJECT,
            {"reason": "SUPERVISOR_TURN", "event": old_event},
        )
        state = self.read_state()
        decision = {"decision": "CONTINUE", "reason": "A committed"}
        state.update(
            status="WAITING_EXECUTOR",
            current_task={key: self.task[key] for key in sc.IDENTITY_KEYS},
            decision_history=[decision], last_supervisor_decision=dict(decision),
        )
        self._json(self.project / "project_state.json", state)
        (self.root / "TO_ZCODE.md").write_bytes(wire(self.task))

        def crash_after_receipt(name):
            if name == "supervisor_after_decision_receipt":
                raise SystemExit(name)

        with mock.patch.object(sc, "_failure_point", side_effect=crash_after_receipt):
            with self.assertRaises(SystemExit):
                sc.finish_supervisor_turn(self.root, turn, processed=True)

        request_b = sc.submit_intervention(self.root, b"intervention B")
        recovered = sc.reconcile_inflight_turn(self.root)
        self.assertTrue(recovered["decision_committed"])
        self.assertTrue(recovered["stale"])
        self.assertTrue(recovered["recovered_committed_receipt"])
        records = {item["intervention_id"]: item for item in
                   sc.list_interventions(self.root, self.PROJECT)}
        self.assertEqual(records[request_a["intervention_id"]]["status"], "CONSUMED")
        self.assertEqual(records[request_b["intervention_id"]]["status"], "PENDING")
        self.assertIsNone(self.read_runtime().get("pending_supervisor_event"))
        self.assertFalse((self.root / "TO_ZCODE.md").exists())

        next_turn = sc.begin_supervisor_turn(self.root, self.PROJECT)
        self.assertEqual(next_turn["intervention_ids"], [request_b["intervention_id"]])
        self.assertNotIn(request_a["intervention_id"], next_turn["intervention_ids"])
        sc.finish_supervisor_turn(self.root, next_turn, processed=False)
        o = self.configured_orchestrator()
        with self.assertRaises(RuntimeError):
            o.register_dispatched_task(o.load_runtime(), o.read_project_state())

    def test_second_recheck_R2_completion_event_durable_across_pause_resume_window(self):
        self.authorize_fixture(claimed=True)
        entry = self.completion_entry(executor_completion.STATUS_COMMITTED)
        executor_completion.publish_compatibility_artifacts(self.root, entry, include_done=True)
        o = self.configured_orchestrator()
        runtime = o.load_runtime()
        seen, event = o.consume_executor_receipt(runtime)
        self.assertTrue(seen)
        self.assertIsNotNone(event)
        sc.set_pause(self.root)
        with mock.patch.object(o, "goal_anchor_gate", return_value=True):
            o.invoke_codex(runtime, "EXECUTOR_RESULT_READY", event)
        pending = o.load_runtime().get("pending_supervisor_event")
        self.assertEqual(pending["event"], event)

        sc.resume(self.root)  # resume wins before the next loop iteration
        calls = []

        def supervisor_result(*_args, **_kwargs):
            calls.append("Supervisor")
            state = o.read_project_state()
            decision = {"decision": "BLOCKED", "reason": "completion accounted"}
            state["decision_history"].append(decision)
            state.update(status="BLOCKED", current_task=None,
                         last_supervisor_decision=dict(decision))
            o.atomic_json(o.PROJECT_STATE, state)
            return mock.Mock(returncode=0)

        with mock.patch.object(o, "goal_anchor_gate", return_value=True), \
             mock.patch.object(o, "build_codex_prompt", return_value="fixture prompt"), \
             mock.patch.object(o, "find_codex", return_value="codex"), \
             mock.patch.object(o.subprocess, "run", side_effect=supervisor_result):
            o.invoke_codex(runtime, "EXECUTOR_RESULT_READY", event)
        self.assertEqual(calls, ["Supervisor"])
        self.assertIsNone(o.load_runtime().get("pending_supervisor_event"))
        self.assertEqual(len(list((self.root / "handoff" / "executor_claims").glob("*.claim"))), 1)
        self.assertEqual(executor_completion.lookup_entries(
            self.root, self.task["MESSAGE_ID"])[0]["STATUS"],
            executor_completion.STATUS_CONSUMED)

    def test_second_recheck_R3_invalid_dispatch_never_consumes_intervention(self):
        cases = {
            "missing_OBJECTIVE": lambda task: task.pop("OBJECTIVE"),
            "invalid_protocol_fence": lambda task: task.update(CLAIM_PROTOCOL_VERSION=2),
            "invalid_FV_fields": lambda task: task.update(FINAL_VERIFICATION_GATE={}),
            "reused_stale_identity": lambda task: None,
            "other_required_OUTPUTS": lambda task: task.pop("OUTPUTS"),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    task = dict(fixture.task)
                    mutate(task)
                    if name == "reused_stale_identity":
                        fixture.runtime["last_consumed_message_id"] = task["MESSAGE_ID"]
                    if name == "invalid_protocol_fence":
                        fixture.runtime["claim_protocol_required_from_message_id"] = task["MESSAGE_ID"]
                    fixture.save_runtime()
                    request = sc.submit_intervention(fixture.root, b"must not be consumed")
                    turn = sc.begin_supervisor_turn(fixture.root, fixture.PROJECT)
                    state = fixture.read_state()
                    decision = {"decision": "CONTINUE", "reason": name}
                    state.update(
                        status="WAITING_EXECUTOR",
                        current_task={key: task[key] for key in sc.IDENTITY_KEYS},
                        decision_history=[decision], last_supervisor_decision=dict(decision),
                    )
                    fixture._json(fixture.project / "project_state.json", state)
                    (fixture.root / "TO_ZCODE.md").write_bytes(wire(task))
                    o = fixture.configured_orchestrator()
                    retry = sc.finish_supervisor_turn(
                        fixture.root, turn, processed=True,
                        candidate_validator=o.validate_supervisor_candidate_snapshot,
                    )
                    self.assertTrue(retry)
                    record = sc.list_interventions(fixture.root, fixture.PROJECT)[0]
                    self.assertEqual(record["intervention_id"], request["intervention_id"])
                    self.assertEqual(record["status"], "PENDING")
                    self.assertFalse(sc.decision_receipt_path(
                        fixture.root, turn["turn_id"]).exists())
                finally:
                    fixture.tearDown()

    def test_second_recheck_R4_archive_payload_identity_must_match_metadata_and_seal(self):
        binding = self.archive(authorized=True)
        meta_path = self.root / binding["metadata_file"]
        seal_path = self.root / binding["authorization_file"]
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        seal = json.loads(seal_path.read_text(encoding="utf-8"))
        meta["TASK_ID"] = "T-METADATA-AND-SEAL"
        seal["TASK_ID"] = "T-METADATA-AND-SEAL"
        self._json(meta_path, meta)
        self._json(seal_path, seal)
        record = sc.find_dispatch(self.root, self.task["MESSAGE_ID"], self.PROJECT)
        self.assertEqual(record["integrity"], "CORRUPT")
        self.assertIn("payload identity", record["error"])

    def test_second_recheck_R5_json_resume_launch_is_bounded_single_document(self):
        shells = [name for name in ("powershell", "pwsh") if shutil.which(name)]
        if not shells:
            self.skipTest("PowerShell is unavailable")
        (self.root / "scripts").mkdir(exist_ok=True)
        for name in ("supervisor_control.py", "executor_fence.py", "executor_completion.py",
                     "executor_claim.py"):
            shutil.copy2(SCRIPTS / name, self.root / "scripts" / name)
        wrapper = self.root / "RESUME_AGENT_SYSTEM.ps1"
        shutil.copy2(REPO / wrapper.name, wrapper)
        start = self.root / "START_AGENT_SYSTEM.ps1"

        for shell in shells:
            with self.subTest(shell=shell, schedule="long_running_start"):
                start.write_text("Start-Sleep -Seconds 10\nexit 0\n", encoding="utf-8")
                sc.set_pause(self.root)
                started = time.monotonic()
                result = subprocess.run(
                    [shell, "-NoProfile", "-File", str(wrapper), "-Json"],
                    cwd=self.root, capture_output=True, timeout=4,
                )
                elapsed = time.monotonic() - started
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                self.assertLess(elapsed, 3.0)
                document = json.loads(result.stdout.decode(errors="strict"))
                self.assertEqual(document["startup"]["status"], "LAUNCHED")
                self.assertEqual(len([line for line in result.stdout.splitlines() if line.strip()]), 1)
                os.kill(int(document["startup"]["pid"]), signal.SIGTERM)
                time.sleep(0.2)

            with self.subTest(shell=shell, schedule="failed_start"):
                start.unlink()
                sc.set_pause(self.root)
                result = subprocess.run(
                    [shell, "-NoProfile", "-File", str(wrapper), "-Json"],
                    cwd=self.root, capture_output=True, timeout=4,
                )
                document = json.loads(result.stdout.decode(errors="strict"))
                self.assertEqual(result.returncode, 1)
                self.assertFalse(document["ok"])
                self.assertEqual(document["startup"]["status"], "FAILED")
                self.assertEqual(len([line for line in result.stdout.splitlines() if line.strip()]), 1)

            with self.subTest(shell=shell, schedule="existing_owner"):
                start.write_text("exit 0\n", encoding="utf-8")
                self._json(self.control / ".orchestrator.lock", {
                    "pid": os.getpid(), "started_at": "2099-01-01T00:00:00+00:00",
                })
                sc.set_pause(self.root)
                result = subprocess.run(
                    [shell, "-NoProfile", "-File", str(wrapper), "-Json"],
                    cwd=self.root, capture_output=True, timeout=4,
                )
                document = json.loads(result.stdout.decode(errors="strict"))
                self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
                self.assertEqual(document["startup"]["status"], "EXISTING_OWNER")
                self.assertEqual(len([line for line in result.stdout.splitlines() if line.strip()]), 1)
                (self.control / ".orchestrator.lock").unlink()

    def test_final_recheck_N1_committed_completion_wins_before_restart_registration(self):
        # Drive the complete production lifecycle: committed Supervisor decision ->
        # registration/archive authorization -> claim token -> intervention ->
        # fenced completion commit -> orchestrator main() restart recovery.
        o = self.configured_orchestrator()
        self.task["ISSUED_AT"] = o.stamp()
        self.commit_decision()
        runtime = o.load_runtime()
        o.register_dispatched_task(runtime, o.read_project_state())
        dispatches_before = sc.list_dispatches(self.root, self.PROJECT)

        claim_output = io.StringIO()
        with contextlib.redirect_stdout(claim_output):
            claim_code = executor_claim.acquire(
                self.root, *[self.task[key] for key in sc.IDENTITY_KEYS]
            )
        self.assertEqual(claim_code, executor_claim.EXIT_ACQUIRED)
        claim_token = claim_output.getvalue().split("claim_token=", 1)[1].strip()

        request = sc.submit_intervention(self.root, b"apply after current attempt")
        self.assertEqual(request["disposition"], "PENDING_AFTER_CURRENT_STAGE")
        self.assertEqual(self.read_state()["status"], "WAITING_EXECUTOR")

        identity = {key: self.task[key] for key in sc.IDENTITY_KEYS}
        staging = self.project / "completion_staging" / "final-recheck-n1"
        staging.mkdir(parents=True)
        self._json(staging / "staging.json", {
            "COMPLETION_STAGING_SCHEMA_VERSION":
                executor_completion.COMPLETION_STAGING_SCHEMA_VERSION,
            **identity, "PROJECT_ID": self.PROJECT,
            "STATUS": "STAGING_READY", "CREATED_AT": o.stamp(),
            "RECEIPT": {**identity, "STATUS": "COMPLETED",
                        "Key findings": ["authoritative production-path completion"]},
            "DELIVERABLES": [],
        })
        self.assertEqual(
            executor_completion.commit(self.root, staging, claim_token=claim_token),
            executor_completion.EXIT_COMMITTED,
        )

        invocations = []
        injected_intervention_ids = []

        def build_prompt(reason, event, *_args, **_kwargs):
            invocations.append((reason, event))
            injected_intervention_ids.extend(
                item["intervention_id"]
                for item in (_kwargs.get("control_turn") or {}).get("interventions", [])
            )
            return "fixture prompt"

        def supervisor_result(*_args, **_kwargs):
            state = o.read_project_state()
            decision = {"decision": "BLOCKED", "reason": "boundary intervention applied"}
            state["decision_history"].append(decision)
            state.update(status="BLOCKED", current_task=None,
                         last_supervisor_decision=dict(decision))
            o.atomic_json(o.PROJECT_STATE, state)
            return mock.Mock(returncode=0)

        with mock.patch.object(o, "goal_anchor_gate", return_value=True), \
             mock.patch.object(o, "build_codex_prompt", side_effect=build_prompt), \
             mock.patch.object(o, "find_codex", return_value="codex"), \
             mock.patch.object(o.subprocess, "run", side_effect=supervisor_result), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 5)

        self.assertEqual(len(invocations), 1)
        self.assertEqual(invocations[0][0], "EXECUTOR_RESULT_READY")
        self.assertEqual(invocations[0][1]["message_id"], self.task["MESSAGE_ID"])
        self.assertEqual(injected_intervention_ids, [request["intervention_id"]])
        entry = executor_completion.lookup_entries(self.root, self.task["MESSAGE_ID"])[0]
        self.assertEqual(entry["STATUS"], executor_completion.STATUS_SEALED)
        audit = (self.root / "handoff" / "completion_ledger" / "audit.jsonl")
        audit_text = audit.read_text(encoding="utf-8")
        self.assertNotIn("ORPHAN_COMPLETION", audit_text)
        self.assertNotIn("COMPLETION_ORPHAN", audit_text)
        self.assertNotIn("dispatch_repair", o.read_project_state())
        self.assertEqual(o.load_runtime().get("dispatch_validation_repair_used"), 0)
        self.assertIsNone(o.load_runtime().get("last_dispatch_validation_error"))
        self.assertEqual(list((self.root / "handoff" / "quarantine").iterdir()), [])
        self.assertEqual(sc.list_dispatches(self.root, self.PROJECT), dispatches_before)
        self.assertEqual(
            len(list((self.root / "handoff" / "executor_claims").glob("*.claim"))), 1
        )
        self.assertNotIn(self.task["MESSAGE_ID"],
                         o.load_runtime().get("retired_message_ids", []))
        record = next(item for item in sc.list_interventions(self.root, self.PROJECT)
                      if item["intervention_id"] == request["intervention_id"])
        self.assertEqual(record["status"], "CONSUMED")

        # A second restart is quiet: the sealed completion and its event do not replay.
        with mock.patch.object(o.subprocess, "run",
                               side_effect=AssertionError("completion replayed")), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 5)
        self.assertEqual(len(invocations), 1)
        self.assertEqual(executor_completion.lookup_entries(
            self.root, self.task["MESSAGE_ID"])[0]["STATUS"],
            executor_completion.STATUS_SEALED)

    def test_second_recheck_N2_stale_stop_cannot_be_reopened_by_pending_retry(self):
        event = {"type": "SUPERVISOR_TURN", "source": "stale-stop"}
        turn = sc.begin_supervisor_turn(
            self.root, self.PROJECT,
            {"reason": "SUPERVISOR_TURN", "event": event},
        )
        sc.submit_intervention(self.root, b"arrived during Supervisor")
        state = self.read_state()
        decision = {"decision": "STOPPED", "reason": "stale but terminal"}
        state.update(status="STOPPED", current_task=None,
                     decision_history=[decision], last_supervisor_decision=dict(decision))
        self._json(self.project / "project_state.json", state)
        self.assertTrue(sc.finish_supervisor_turn(self.root, turn, processed=True))
        runtime = self.read_runtime()
        runtime["pending_supervisor_event"] = {
            "reason": "SUPERVISOR_TURN", "event": event,
            "recorded_at": "2026-09-11T00:00:00+00:00",
            "decision_attempts": 1, "retry_exhausted": False,
        }
        self._json(self.control / "orchestrator_runtime.json", runtime)

        o = self.configured_orchestrator()
        with mock.patch.object(o, "invoke_codex") as invoke, \
             mock.patch.object(o, "register_dispatched_task") as register, \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 5)
        invoke.assert_not_called()
        register.assert_not_called()
        self.assertIsNone(o.load_runtime().get("authorized_dispatch"))
        self.assertEqual(o.read_project_state()["status"], "STOPPED")
        self.assertEqual(list((self.root / "handoff" / "executor_claims").glob("*.claim")), [])

    def test_second_recheck_N3_invalid_results_have_restart_persistent_retry_budget(self):
        cases = ("noop", "malformed", "invalid_candidate")
        for case in cases:
            with self.subTest(case=case):
                fixture = self.__class__("test_pause_idle_and_resume")
                fixture.setUp()
                try:
                    request = sc.submit_intervention(
                        fixture.root, f"audit {case}".encode("utf-8")
                    )
                    o = fixture.configured_orchestrator()
                    event = {"type": "SUPERVISOR_TURN", "case": case}
                    calls = []

                    def invalid_result(*_args, **_kwargs):
                        calls.append(case)
                        state = o.read_project_state()
                        if case == "malformed":
                            state["last_supervisor_decision"] = {"decision": "CONTINUE"}
                            o.atomic_json(o.PROJECT_STATE, state)
                        elif case == "invalid_candidate":
                            task = dict(fixture.task)
                            task.pop("OBJECTIVE")
                            decision = {"decision": "CONTINUE", "reason": "bad candidate"}
                            state["decision_history"].append(decision)
                            state.update(
                                status="WAITING_EXECUTOR",
                                current_task={key: task[key] for key in sc.IDENTITY_KEYS},
                                last_supervisor_decision=dict(decision),
                            )
                            o.atomic_json(o.PROJECT_STATE, state)
                            o.TO_ZCODE.write_bytes(wire(task))
                        return mock.Mock(returncode=0)

                    with mock.patch.object(o, "goal_anchor_gate", return_value=True), \
                         mock.patch.object(o, "build_codex_prompt", return_value="fixture prompt"), \
                         mock.patch.object(o, "find_codex", return_value="codex"), \
                         mock.patch.object(o.subprocess, "run", side_effect=invalid_result):
                        runtime = o.load_runtime()
                        o.invoke_codex(runtime, "SUPERVISOR_TURN", event)
                        first = o.load_runtime()["pending_supervisor_event"]
                        self.assertEqual(first["decision_attempts"], 1)
                        self.assertFalse(first["retry_exhausted"])

                        # Simulated restart: discard every in-memory object and reload
                        # the durable event/counter before the second attempt.
                        runtime = o.load_runtime()
                        o.invoke_codex(runtime, "SUPERVISOR_TURN", event)
                    pending = o.load_runtime()["pending_supervisor_event"]
                    self.assertEqual(pending["decision_attempts"], 2)
                    self.assertEqual(calls, [case, case])
                    record = next(item for item in sc.list_interventions(
                        fixture.root, fixture.PROJECT)
                                      if item["intervention_id"] == request["intervention_id"])
                    self.assertEqual(record["status"], "PENDING")
                    if case == "invalid_candidate":
                        self.assertEqual(o.load_runtime()["dispatch_validation_repair_used"], 1)
                        self.assertFalse(o.TO_ZCODE.exists())
                    self.assertTrue(pending["retry_exhausted"])
                    self.assertEqual(o.read_project_state()["status"], "HUMAN_REVIEW")
                    with self.assertRaisesRegex(sc.ControlError, "HUMAN_REVIEW"):
                        sc.begin_supervisor_turn(fixture.root, fixture.PROJECT)
                finally:
                    fixture.tearDown()

    def test_final_recheck_N3_second_invalid_candidate_immediately_enters_human_review(self):
        request = sc.submit_intervention(self.root, b"preserve this auditable input")
        o = self.configured_orchestrator()
        calls = []

        def invalid_candidate(*_args, **_kwargs):
            calls.append(len(calls) + 1)
            state = o.read_project_state()
            task = dict(self.task)
            task.pop("OBJECTIVE")
            decision = {
                "decision": "CONTINUE",
                "reason": f"invalid candidate attempt {len(calls)}",
            }
            state["decision_history"].append(decision)
            state.update(
                status="WAITING_EXECUTOR",
                current_task={key: task[key] for key in sc.IDENTITY_KEYS},
                last_supervisor_decision=dict(decision),
            )
            o.atomic_json(o.PROJECT_STATE, state)
            o.TO_ZCODE.write_bytes(wire(task))
            return mock.Mock(returncode=0)

        with mock.patch.object(o, "goal_anchor_gate", return_value=True), \
             mock.patch.object(o, "build_codex_prompt", return_value="fixture prompt"), \
             mock.patch.object(o, "find_codex", return_value="codex"), \
             mock.patch.object(o.subprocess, "run", side_effect=invalid_candidate), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            # Both invalid results and the transition to HUMAN_REVIEW happen in
            # this single main() execution. No exception/restart bridge is allowed.
            self.assertEqual(o.main(), 3)

        state = o.read_project_state()
        runtime = o.load_runtime()
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(calls, [1, 2])
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertEqual(runtime["status"], "HUMAN_REVIEW")
        self.assertTrue(pending["retry_exhausted"])
        self.assertEqual(pending["decision_attempts"], 2)
        self.assertEqual(state["supervisor_retry_failure"]["decision_attempts"], 2)
        self.assertEqual(len(state["decision_history"]), 2)
        self.assertIsNone(state["current_task"])
        self.assertIsNone(runtime.get("authorized_dispatch"))
        self.assertEqual(sc.list_dispatches(self.root, self.PROJECT), [])
        self.assertEqual(list((self.root / "handoff" / "executor_claims").glob("*.claim")), [])
        self.assertFalse(o.TO_ZCODE.exists())
        record = next(item for item in sc.list_interventions(self.root, self.PROJECT)
                      if item["intervention_id"] == request["intervention_id"])
        self.assertEqual(record["status"], "PENDING")
        self.assertNotEqual(runtime["status"], "ORCHESTRATOR_ERROR")

        # Restart stays bounded and cannot call the Supervisor or authorize work.
        with mock.patch.object(o.subprocess, "run",
                               side_effect=AssertionError("third Supervisor call")), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 3)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(o.read_project_state()["status"], "HUMAN_REVIEW")
        self.assertIsNone(o.load_runtime().get("authorized_dispatch"))
        self.assertEqual(sc.list_dispatches(self.root, self.PROJECT), [])

    def test_final_recheck_N3_first_restart_finalizes_crashed_second_invalid_candidate(self):
        request = sc.submit_intervention(
            self.root, b"preserve this input across exhausted startup recovery"
        )
        o = self.configured_orchestrator()
        calls = []

        def invalid_candidate(*_args, **_kwargs):
            calls.append(len(calls) + 1)
            state = o.read_project_state()
            task = dict(self.task)
            task.pop("OBJECTIVE")
            decision = {
                "decision": "CONTINUE",
                "reason": f"invalid recovered candidate attempt {len(calls)}",
            }
            state["decision_history"].append(decision)
            state.update(
                status="WAITING_EXECUTOR",
                current_task={key: task[key] for key in sc.IDENTITY_KEYS},
                last_supervisor_decision=dict(decision),
            )
            o.atomic_json(o.PROJECT_STATE, state)
            o.TO_ZCODE.write_bytes(wire(task))
            return mock.Mock(returncode=0)

        control = o._supervisor_control_helper()
        finish_supervisor_turn = control.finish_supervisor_turn
        accounting_calls = []

        class SimulatedProcessDeath(BaseException):
            pass

        def die_before_second_result_accounting(*args, **kwargs):
            accounting_calls.append(len(accounting_calls) + 1)
            if len(accounting_calls) == 2:
                raise SimulatedProcessDeath()
            return finish_supervisor_turn(*args, **kwargs)

        with mock.patch.object(o, "goal_anchor_gate", return_value=True), \
             mock.patch.object(o, "build_codex_prompt", return_value="fixture prompt"), \
             mock.patch.object(o, "find_codex", return_value="codex"), \
             mock.patch.object(o.subprocess, "run", side_effect=invalid_candidate), \
             mock.patch.object(control, "finish_supervisor_turn",
                               side_effect=die_before_second_result_accounting), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            with self.assertRaises(SimulatedProcessDeath):
                o.main()

        crashed_runtime = o.load_runtime()
        crashed_pending = crashed_runtime["pending_supervisor_event"]
        self.assertEqual(calls, [1, 2])
        self.assertEqual(accounting_calls, [1, 2])
        self.assertEqual(crashed_pending["decision_attempts"], 2)
        self.assertFalse(crashed_pending["retry_exhausted"])
        self.assertEqual(crashed_runtime["codex_invocations"], 2)
        self.assertIsNotNone(control.load_control(self.root)["inflight_supervisor_turn"])

        # The first production startup recovery must contain dispatch-repair
        # exhaustion and commit the existing bounded HUMAN_REVIEW transition.
        with mock.patch.object(o.subprocess, "run",
                               side_effect=AssertionError("third Supervisor call")), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 3)

        state = o.read_project_state()
        runtime = o.load_runtime()
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertEqual(runtime["status"], "HUMAN_REVIEW")
        self.assertTrue(pending["retry_exhausted"])
        self.assertEqual(pending["decision_attempts"], 2)
        self.assertEqual(state["supervisor_retry_failure"]["decision_attempts"], 2)
        self.assertIsNone(state["current_task"])
        self.assertIsNone(runtime.get("authorized_dispatch"))
        self.assertEqual(sc.list_dispatches(self.root, self.PROJECT), [])
        self.assertEqual(
            list((self.root / "handoff" / "executor_claims").glob("*.claim")), []
        )
        self.assertFalse(o.TO_ZCODE.exists())
        self.assertNotEqual(runtime["status"], "ORCHESTRATOR_ERROR")
        record = next(item for item in sc.list_interventions(self.root, self.PROJECT)
                      if item["intervention_id"] == request["intervention_id"])
        self.assertEqual(record["status"], "PENDING")
        self.assertEqual(
            record["instruction_text"],
            "preserve this input across exhausted startup recovery",
        )

        # A later restart remains bounded and performs no model call or dispatch.
        with mock.patch.object(o.subprocess, "run",
                               side_effect=AssertionError("Supervisor called after review")), \
             mock.patch.object(o, "emit_user_notification", return_value=False):
            self.assertEqual(o.main(), 3)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(o.read_project_state()["status"], "HUMAN_REVIEW")
        self.assertTrue(o.load_runtime()["pending_supervisor_event"]["retry_exhausted"])
        self.assertEqual(o.load_runtime()["pending_supervisor_event"]["decision_attempts"], 2)
        self.assertIsNone(o.load_runtime().get("authorized_dispatch"))
        self.assertEqual(sc.list_dispatches(self.root, self.PROJECT), [])
        self.assertEqual(
            list((self.root / "handoff" / "executor_claims").glob("*.claim")), []
        )
        record = next(item for item in sc.list_interventions(self.root, self.PROJECT)
                      if item["intervention_id"] == request["intervention_id"])
        self.assertEqual(record["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
