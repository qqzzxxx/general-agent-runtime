"""Focused coverage for ORPHAN-SUPERVISOR-EVENT-RECONCILIATION-V1.

Behavioral classes pinned here, per the retirement brief:

* Confirmed orphan — the real stale-event shape from the pre-start hygiene
  audit (bare legacy EXECUTOR_RESULT_READY, no identity, empty work state)
  is mechanically classified ORPHAN, retired with durable provenance, and
  no longer owns startup (first Supervisor invocation becomes
  ORCHESTRATOR_START).
* Valid live event — an identity-bearing event backed by a current task /
  claim / completion ledger stays LIVE, is preserved by retire, and the
  runtime is byte-identical afterwards.
* Ambiguous / partially bound — identity-bearing without a referent,
  already-serviced, terminal-audit, unknown-reason, or any live work source
  present stays UNCERTAIN and is never retired.
* Idempotence / restart — repeated reconciliation does not duplicate
  retirement records, and a stale snapshot cannot resurrect a retired event
  through save_runtime.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
RUNTIME_ROOT = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import executor_claim
import supervisor_control as sc

RECON_PATH = SCRIPTS / "supervisor_event_reconciliation.py"
_spec = importlib.util.spec_from_file_location(
    "supervisor_event_reconciliation_under_test", RECON_PATH)
recon = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(recon)

PROJECT_ID = "recon-test"
LIVE_ID = 700201


class _AbortTurn(Exception):
    """Raised by the startup-simulation recorder after the first invocation."""

# The exact stale-event shape recorded in the pre-start hygiene audit
# (docs/v1.4-pre-start-runtime-hygiene.md): Runtime-global, predates the
# migration, binds no identity, never serviced.
REAL_STALE_PENDING = {
    "reason": "EXECUTOR_RESULT_READY",
    "event": {"type": "EXECUTOR_RESULT_READY"},
    "recorded_at": "2026-09-15T07:23:04+00:00",
    "decision_attempts": 0,
    "retry_exhausted": False,
}


def _base_runtime(pending=None):
    runtime = {
        "schema_version": 2,
        "started_at": "2026-09-15T07:23:04+00:00",
        "status": "RUNNING",
        "last_consumed_message_id": 602,
        "last_consumed_nonce": None,
        "last_consumed_brief_sha256": None,
        "last_dispatched_message_id": None,
        "last_dispatched_nonce": None,
        "authorized_dispatch": None,
        "retired_message_ids": [],
        "pending_supervisor_event": pending,
        "codex_invocations": 0,
        "executor_receipts_consumed": 0,
        "stale_receipts_ignored": 0,
        "protocol_errors": 0,
        "consecutive_codex_without_executor": 0,
        "final_verification_policy_version": 1,
        "final_verification_enforce_after": None,
        "last_final_verification_message_id": None,
        "last_final_verification_receipt_sha256": None,
        "last_final_verification_claims_hash": None,
        "last_final_verification_overall_status": None,
        "last_final_verification_mechanical_pass": None,
        "final_verification_receipt_ledger": [],
        "final_verification_gate_blocks": 0,
        "last_final_verification_gate_block_reason": None,
        "goal_anchor_binding": None,
        "goal_anchor_failures": 0,
        "last_goal_anchor_failure": None,
    }
    return runtime


def _base_state():
    return {
        "schema_version": 2,
        "project_id": PROJECT_ID,
        "status": "SUPERVISOR_TURN",
        "current_task": None,
        "decision_history": [],
        "human_review_resume": None,
        "next_message_id": 603,
        "phase": "EXECUTION",
        "profile": "GENERAL",
        "created_at": "2026-09-18T00:00:00+00:00",
        "updated_at": "2026-09-18T00:00:00+00:00",
        "final_verification": {
            "policy_version": 1, "required": True, "status": "NOT_STARTED",
            "policy_id": "ACADEMIC_FV_V1", "critical_claims": [],
            "claims_hash": None, "verification_message_id": None,
            "verification_receipt_sha256": None,
        },
    }


def _live_identity():
    return {"MESSAGE_ID": LIVE_ID, "TASK_ID": "task-fixture",
            "STAGE_ID": "stage-fixture", "ATTEMPT": 1,
            "NONCE": "nonce-fixture-live"}


def _live_current_task():
    identity = _live_identity()
    return {**identity, "OBJECTIVE": "fixture", "MAX_TIME": 600}


def _live_pending_event():
    identity = _live_identity()
    return {
        "reason": "EXECUTOR_RESULT_READY",
        "event": {
            "type": "EXECUTOR_RESULT_READY",
            "message_id": identity["MESSAGE_ID"],
            "task_id": identity["TASK_ID"],
            "stage_id": identity["STAGE_ID"],
            "attempt": identity["ATTEMPT"],
            "nonce": identity["NONCE"],
            "status": "SUCCESS",
            "brief_sha256": "ab" * 32,
            "archive": "handoff/archive/fixture-brief.md",
            "commit_id": "commit-fixture",
            "receipt_sha256": "cd" * 32,
            "committed_receipt_path":
                f"handoff/completion_ledger/completion-{LIVE_ID}-fixture.json",
        },
        "recorded_at": "2026-09-18T01:00:00+00:00",
        "decision_attempts": 0,
        "retry_exhausted": False,
    }


class SupervisorEventReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-event-recon-")
        self.root = Path(self.temp.name)
        self.m = recon.load_runtime_module(RUNTIME_ROOT, self.root)
        self.m.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        for path in (self.m.CONTROL, self.m.LOGS, self.m.HANDOFF_ARCHIVE,
                     self.root / "handoff" / "executor_claims",
                     self.root / "handoff" / "quarantine",
                     self.root / "handoff" / "completion_ledger" / "staged"):
            path.mkdir(parents=True, exist_ok=True)
        self.project_root = self.root / "projects" / PROJECT_ID
        self.project_root.mkdir(parents=True)
        (self.project_root / "PROJECT_GOAL.md").write_text(
            "# Fixture goal\n", encoding="utf-8")
        (self.project_root / "RESEARCH_STATE.md").write_text(
            "# Fixture memory\n", encoding="utf-8")
        self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": PROJECT_ID,
            "project_root": f"projects/{PROJECT_ID}"})
        self.m.PROJECT_STATE = self.project_root / "project_state.json"
        self._write_runtime(_base_runtime(dict(REAL_STALE_PENDING)))
        self._write_state(_base_state())
        self.m.activate_project_scope()

    def tearDown(self):
        self.temp.cleanup()

    # -- helpers ---------------------------------------------------------

    def _read_runtime(self):
        return json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8-sig"))

    def _write_runtime(self, runtime):
        self.m.atomic_json(self.m.RUNTIME_STATE, runtime)

    def _write_state(self, state):
        self.m.atomic_json(self.m.PROJECT_STATE, state)

    def _classify(self):
        return self.m.classify_pending_supervisor_event(
            self._read_runtime(), self.m.read_project_state())

    def _retire(self):
        return self.m.reconcile_pending_supervisor_event(retire=True)

    def _add_live_backing(self, with_ledger=True, with_claim=True):
        """Realistically construct the authoritative backing for a live event."""
        identity = _live_identity()
        state = _base_state()
        state["status"] = "WAITING_EXECUTOR"
        state["current_task"] = _live_current_task()
        self._write_state(state)
        runtime = self._read_runtime()
        runtime["authorized_dispatch"] = {
            "schema_version": 1, **identity, "PROJECT_ID": PROJECT_ID,
            "TO_ZCODE_SHA256": "ef" * 32, "AUTHORIZED_AT":
                "2026-09-18T00:30:00+00:00", "FENCE_VERSION": 1,
        }
        self._write_runtime(runtime)
        if with_claim:
            claim_dir = executor_claim.claim_dir(
                self.root, identity["MESSAGE_ID"], identity["NONCE"])
            claim_dir.mkdir(parents=True, exist_ok=True)
            self.m.atomic_json(claim_dir / "claim.json", {
                "CLAIM_PROTOCOL_VERSION": 1, **identity,
                "CLAIMED_AT": "2026-09-18T00:31:00+00:00"})
        if with_ledger:
            self.m.atomic_json(
                self.m.ROOT / "handoff" / "completion_ledger"
                / f"completion-{LIVE_ID}-fixture.json",
                {"COMPLETION_PROTOCOL_VERSION": 1,
                 "STATUS": "COMPLETION_COMMITTED",
                 "MESSAGE_ID": identity["MESSAGE_ID"],
                 "TASK_ID": identity["TASK_ID"],
                 "STAGE_ID": identity["STAGE_ID"],
                 "ATTEMPT": identity["ATTEMPT"], "NONCE": identity["NONCE"],
                 "COMMIT_ID": "commit-fixture",
                 "RECEIPT_SHA256": "cd" * 32, "BRIEF_SHA256": "ab" * 32,
                 "PROJECT_ID": PROJECT_ID})
        pending = _live_pending_event()
        runtime = self._read_runtime()
        runtime["pending_supervisor_event"] = pending
        self._write_runtime(runtime)
        return pending

    # -- confirmed orphan -------------------------------------------------

    def test_real_stale_shape_is_orphan(self):
        report = self._classify()
        self.assertEqual(report["classification"], "ORPHAN")
        self.assertTrue(report["retireable"])
        failed = [c["check"] for c in report["checks"] if not c["passed"]]
        self.assertEqual(failed, [])
        expected_checks = {
            "not_retry_exhausted", "never_entered_decision_machinery",
            "reason_contract", "event_binds_no_identity",
            "no_current_executor_stage", "no_authorized_dispatch",
            "completion_ledger_empty", "executor_claims_empty",
            "no_deferred_completion_event", "no_pending_executor_timeout",
            "no_live_final_verification", "no_human_decision_pending",
            "no_raw_executor_signal", "no_terminal_guard_flags"}
        self.assertEqual({c["check"] for c in report["checks"]},
                         expected_checks)

    def test_orphan_retirement_provenance_and_runtime_state(self):
        before = self._read_runtime()
        report = self._retire()
        self.assertTrue(report["changed"])
        after = self._read_runtime()
        self.assertIsNone(after["pending_supervisor_event"])
        records = after.get("retired_supervisor_events")
        self.assertIsInstance(records, list)
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["mechanism"],
                         "ORPHAN-SUPERVISOR-EVENT-RECONCILIATION-V1")
        self.assertEqual(record["classification"], "ORPHAN")
        self.assertEqual(record["reason"], "EXECUTOR_RESULT_READY")
        self.assertEqual(record["originally_recorded_at"],
                         REAL_STALE_PENDING["recorded_at"])
        self.assertEqual(record["retired_event"], REAL_STALE_PENDING)
        self.assertTrue(record["retired_at"] >= record["originally_recorded_at"])
        self.assertEqual(record["classification_evidence"], report["checks"])
        untouched = {k: v for k, v in after.items() if k not in (
            "pending_supervisor_event", "retired_supervisor_events",
            "updated_at")}
        before_untouched = {k: v for k, v in before.items() if k not in (
            "pending_supervisor_event", "retired_supervisor_events",
            "updated_at")}
        self.assertEqual(untouched, before_untouched)
        log_text = (self.m.LOGS / "orchestrator.jsonl").read_text(
            encoding="utf-8")
        self.assertIn("Retired orphan pending Supervisor event", log_text)

    # -- valid live event -------------------------------------------------

    def test_live_event_backed_by_authority_is_preserved(self):
        pending = self._add_live_backing()
        before_bytes = self.m.RUNTIME_STATE.read_bytes()
        report = self._classify()
        self.assertEqual(report["classification"], "LIVE")
        self.assertFalse(report["retireable"])
        self.assertIn("current_task", report["referents"])
        self.assertIn("authorized_dispatch", report["referents"])
        self.assertIn("executor_claim", report["referents"])
        self.assertIn("completion_ledger", report["referents"])
        result = self._retire()
        self.assertFalse(result["changed"])
        self.assertTrue(result["preserved"])
        self.assertEqual(self.m.RUNTIME_STATE.read_bytes(), before_bytes)
        after = self._read_runtime()
        self.assertEqual(after["pending_supervisor_event"], pending)

    def test_live_event_only_ledger_referent_still_live(self):
        self._add_live_backing(with_claim=False)
        runtime = self._read_runtime()
        runtime["authorized_dispatch"] = None
        state = _base_state()
        state["status"] = "SUPERVISOR_TURN"
        state["current_task"] = None
        self._write_state(state)
        self._write_runtime(runtime)
        report = self._classify()
        self.assertEqual(report["classification"], "LIVE")
        self.assertEqual(report["referents"], ["completion_ledger"])

    # -- ambiguous / uncertain -------------------------------------------

    def test_identity_event_without_referent_is_uncertain(self):
        runtime = self._read_runtime()
        runtime["pending_supervisor_event"] = _live_pending_event()
        self._write_runtime(runtime)
        report = self._classify()
        self.assertEqual(report["classification"], "UNCERTAIN")
        result = self._retire()
        self.assertFalse(result["changed"])
        self.assertEqual(self._read_runtime()["pending_supervisor_event"],
                         _live_pending_event())

    def test_partial_binding_is_uncertain(self):
        runtime = self._read_runtime()
        runtime["pending_supervisor_event"] = {
            "reason": "EXECUTOR_RESULT_READY",
            "event": {"type": "EXECUTOR_RESULT_READY",
                      "message_id": LIVE_ID},
            "recorded_at": "2026-09-18T01:00:00+00:00",
            "decision_attempts": 0, "retry_exhausted": False}
        self._write_runtime(runtime)
        self.assertEqual(self._classify()["classification"], "UNCERTAIN")

    def test_uncertain_guards(self):
        cases = {}
        runtime = self._read_runtime()
        pending = dict(REAL_STALE_PENDING)
        pending["decision_attempts"] = 3
        cases["already_serviced"] = dict(runtime, pending_supervisor_event=pending)
        exhausted = dict(REAL_STALE_PENDING)
        exhausted["retry_exhausted"] = True
        cases["retry_exhausted_audit_owned"] = dict(
            runtime, pending_supervisor_event=exhausted)
        unknown = dict(REAL_STALE_PENDING)
        unknown["reason"] = "SOME_FUTURE_REASON"
        unknown["event"] = {"type": "SOME_FUTURE_REASON"}
        cases["unknown_reason"] = dict(runtime, pending_supervisor_event=unknown)
        cases["authorized_dispatch_present"] = dict(
            runtime, pending_supervisor_event=dict(REAL_STALE_PENDING),
            authorized_dispatch={"schema_version": 1, "MESSAGE_ID": 5})
        cases["deferred_event_present"] = dict(
            runtime, pending_supervisor_event=dict(REAL_STALE_PENDING),
            paused_deferred_event={"type": "EXECUTOR_RESULT_READY",
                                   "message_id": LIVE_ID})
        cases["pending_timeout_present"] = dict(
            runtime, pending_supervisor_event=dict(REAL_STALE_PENDING),
            pending_executor_timeout={"type": "EXECUTOR_TIMEOUT"})
        cases["fv_ledger_present"] = dict(
            runtime, pending_supervisor_event=dict(REAL_STALE_PENDING),
            final_verification_receipt_ledger=[{"receipt_id": "x"}])
        for name, mutated in cases.items():
            with self.subTest(case=name):
                self._write_runtime(mutated)
                report = self._classify()
                self.assertEqual(report["classification"], "UNCERTAIN",
                                 msg=json.dumps(report["checks"]))
                result = self._retire()
                self.assertFalse(result["changed"])
                after = self._read_runtime()
                self.assertIsInstance(after["pending_supervisor_event"], dict)
                self.assertNotIn("retired_supervisor_events", after)

        # Project-state live sources and control flags.
        state_cases = {}
        waiting = _base_state()
        waiting["status"] = "WAITING_EXECUTOR"
        waiting["current_task"] = _live_current_task()
        state_cases["waiting_executor_with_task"] = waiting
        fv_live = _base_state()
        fv_live["final_verification"] = dict(
            _base_state()["final_verification"], status="IN_PROGRESS",
            verification_message_id=610)
        state_cases["fv_in_progress"] = fv_live
        human = _base_state()
        human["human_review_resume"] = {
            "status": "PENDING", "receipt_id": "r-1"}
        state_cases["human_decision_pending"] = human
        for name, state in state_cases.items():
            with self.subTest(case=name):
                self._write_state(state)
                report = self._classify()
                self.assertEqual(report["classification"], "UNCERTAIN",
                                 msg=json.dumps(report["checks"]))
        for flag in ("STOP", "HUMAN_REVIEW"):
            with self.subTest(case=f"flag_{flag.lower()}"):
                flag_path = self.m.CONTROL / flag
                flag_path.write_text("", encoding="utf-8")
                try:
                    report = self._classify()
                    self.assertEqual(report["classification"], "UNCERTAIN",
                                     msg=json.dumps(report["checks"]))
                finally:
                    flag_path.unlink()
        done = self.m.ROOT / "ZCODE_DONE.flag"
        done.write_text("", encoding="utf-8")
        try:
            report = self._classify()
            self.assertEqual(report["classification"], "UNCERTAIN",
                             msg=json.dumps(report["checks"]))
        finally:
            done.unlink()

    # -- idempotence / restart --------------------------------------------

    def test_retirement_is_idempotent(self):
        self.assertTrue(self._retire()["changed"])
        first = self._read_runtime()
        again = self._retire()
        self.assertEqual(again["classification"], "NONE")
        self.assertFalse(again["changed"])
        second = self._read_runtime()
        self.assertEqual(
            second.get("retired_supervisor_events"),
            first.get("retired_supervisor_events"))
        self.assertIsNone(second["pending_supervisor_event"])

    def test_stale_snapshot_cannot_resurrect_retired_event(self):
        stale = self._read_runtime()  # still holds the pending event
        self.assertTrue(self._retire()["changed"])
        # A writer holding the pre-retirement snapshot saves through the
        # fenced merge: the retired event must stay retired, the history
        # must survive, and any *new* event must still take the slot.
        self.m.save_runtime(stale)
        after = self._read_runtime()
        self.assertIsNone(after["pending_supervisor_event"])
        self.assertEqual(len(after["retired_supervisor_events"]), 1)
        fresh_event = {
            "reason": "HUMAN_DECISION_RESUME",
            "event": {"type": "HUMAN_DECISION_RESUME", "receipt_id": "r-9"},
            "recorded_at": "2026-09-18T02:00:00+00:00",
            "decision_attempts": 0, "retry_exhausted": False}
        with_new = self._read_runtime()
        with_new["pending_supervisor_event"] = fresh_event
        self.m.save_runtime(with_new)
        after_new = self._read_runtime()
        self.assertEqual(after_new["pending_supervisor_event"], fresh_event)
        self.assertEqual(len(after_new["retired_supervisor_events"]), 1)

    def test_malformed_history_refused_by_merge(self):
        self.assertTrue(self._retire()["changed"])
        runtime = self._read_runtime()
        runtime["retired_supervisor_events"] = "not-a-list"
        with self.assertRaises(RuntimeError):
            self.m.save_runtime(runtime)

    # -- startup ordering ---------------------------------------------------

    def _simulate_first_invocation(self):
        """Run a real orchestrator startup with the model call cut short.

        invoke_codex is replaced by a recorder that aborts the run after the
        first Supervisor invocation is issued (main() wraps the abort into
        ORCHESTRATOR_ERROR), so the recorded reason/event pair is exactly
        what startup would service first.
        """
        recorded = []

        def recorder(runtime, reason, event=None):
            recorded.append((reason, event))
            raise _AbortTurn()

        original = self.m.invoke_codex
        self.m.invoke_codex = recorder
        try:
            self.m.main()
        finally:
            self.m.invoke_codex = original
        self.assertEqual(len(recorded), 1)
        return recorded[0]

    def test_startup_services_stale_event_before_retirement(self):
        reason, event = self._simulate_first_invocation()
        self.assertEqual(reason, "EXECUTOR_RESULT_READY")
        self.assertEqual(event, {"type": "EXECUTOR_RESULT_READY"})

    def test_startup_reaches_orchestrator_start_after_retirement(self):
        self.assertTrue(self._retire()["changed"])
        reason, event = self._simulate_first_invocation()
        self.assertEqual(reason, "ORCHESTRATOR_START")
        self.assertEqual(event, {"runtime": "v2", "shared_file_executor": True})

    def test_no_pending_event_classifies_none(self):
        runtime = self._read_runtime()
        runtime["pending_supervisor_event"] = None
        self._write_runtime(runtime)
        report = self._classify()
        self.assertEqual(report["classification"], "NONE")
        self.assertEqual(self._retire()["classification"], "NONE")

    def test_scheduler_owner_alive_refuses_retirement(self):
        runtime = self._read_runtime()
        runtime["scheduler_owner"] = {
            "pid": os.getpid(), "started_at": "2026-09-18T00:00:00+00:00",
            "process_identity": "fixture"}
        self._write_runtime(runtime)
        report = self._retire()
        self.assertFalse(report["changed"])
        self.assertIn("refusal", report)
        self.assertIsInstance(
            self._read_runtime()["pending_supervisor_event"], dict)


class SupervisorEventReconciliationCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="supervisor-event-cli-")
        self.root = Path(self.temp.name)
        for path in ("control", "logs", "handoff/archive",
                     "handoff/executor_claims", "handoff/quarantine",
                     "handoff/completion_ledger/staged"):
            (self.root / path).mkdir(parents=True, exist_ok=True)
        project_root = self.root / "projects" / PROJECT_ID
        project_root.mkdir(parents=True)
        (project_root / "PROJECT_GOAL.md").write_text(
            "# Fixture goal\n", encoding="utf-8")
        (project_root / "RESEARCH_STATE.md").write_text(
            "# Fixture memory\n", encoding="utf-8")
        runtime = _base_runtime(dict(REAL_STALE_PENDING))
        (self.root / "control" / "orchestrator_runtime.json").write_text(
            json.dumps(runtime, indent=2), encoding="utf-8")
        (self.root / "control" / "ACTIVE_PROJECT.json").write_text(
            json.dumps({"schema_version": 1, "project_id": PROJECT_ID,
                        "project_root": f"projects/{PROJECT_ID}"}),
            encoding="utf-8")
        (project_root / "project_state.json").write_text(
            json.dumps(_base_state(), indent=2), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(RECON_PATH), "--root", str(self.root), *args],
            capture_output=True, text=True, timeout=120)

    def test_cli_prove_orphan_then_retire_then_noop(self):
        proved = self._run("prove")
        self.assertEqual(proved.returncode, 0, proved.stderr)
        self.assertIn("SUPERVISOR_EVENT_CLASSIFICATION: ORPHAN", proved.stdout)
        retired = self._run("retire")
        self.assertEqual(retired.returncode, 0, retired.stderr)
        self.assertIn("SUPERVISOR_EVENT_RETIRED: RETIRED", retired.stdout)
        runtime = json.loads(
            (self.root / "control" / "orchestrator_runtime.json").read_text(
                encoding="utf-8"))
        self.assertIsNone(runtime["pending_supervisor_event"])
        self.assertEqual(len(runtime["retired_supervisor_events"]), 1)
        again = self._run("retire")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("SUPERVISOR_EVENT_RETIRED: NO_PENDING_EVENT", again.stdout)
        runtime = json.loads(
            (self.root / "control" / "orchestrator_runtime.json").read_text(
                encoding="utf-8"))
        self.assertEqual(len(runtime["retired_supervisor_events"]), 1)

    def test_cli_refuses_live_event(self):
        runtime = json.loads(
            (self.root / "control" / "orchestrator_runtime.json").read_text(
                encoding="utf-8"))
        runtime["authorized_dispatch"] = {"MESSAGE_ID": 5}
        (self.root / "control" / "orchestrator_runtime.json").write_text(
            json.dumps(runtime, indent=2), encoding="utf-8")
        proved = self._run("prove")
        self.assertEqual(proved.returncode, 2)
        self.assertIn("SUPERVISOR_EVENT_CLASSIFICATION: UNCERTAIN", proved.stdout)
        refused = self._run("retire")
        self.assertEqual(refused.returncode, 2)
        self.assertIn("SUPERVISOR_EVENT_RETIRED: PRESERVED_UNCERTAIN",
                      refused.stdout)
        runtime = json.loads(
            (self.root / "control" / "orchestrator_runtime.json").read_text(
                encoding="utf-8"))
        self.assertIsInstance(runtime["pending_supervisor_event"], dict)


if __name__ == "__main__":
    unittest.main()
