"""QUOTA-PAUSE-PARK-V1: a scheduling/quota pause parks work, never fails it.

Dogfood regression suite. A user pausing for a few hours because of a quota
shortage must not lose a dispatched-but-unclaimed task, must not consume the
logical retry budget, must never be forced through HUMAN_REVIEW, and must be
able to resume even when a long pause outlived a claimed attempt's
authorization expiry. Explicit interrupts keep the fail-closed retirement
semantics.
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor_claim
import executor_completion
import ordinary_dispatch as od
import supervisor_control as sc
import test_supervisor_control as core
from resume_lifecycle_fixture import simulated_resume


def proposal():
    return {"logical_task": "summary", "logical_stage": "check evidence",
            "objective": "Produce a checked summary of the supplied evidence.",
            "inputs": ["evidence/source.txt"], "outputs": ["reports/summary.md"],
            "acceptance_criteria": ["Every finding cites supporting evidence."],
            "max_time": 900, "max_retries": 2}


class QuotaPauseLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = core.SupervisorControlTests()
        self.f.setUp()
        self.root = self.f.root
        self.o = self.f.configured_orchestrator()
        self.addCleanup(self.f.tearDown)

    # -- ordinary dispatch flow helpers (same boundaries as production) ----

    def begin(self):
        turn = sc.begin_supervisor_turn(self.root, self.f.PROJECT,
                                        ordinary_dispatch_required=True)
        state = self.f.read_state()
        decision = {"decision": "CONTINUE", "reason": "Check the supplied evidence"}
        state["last_supervisor_decision"] = dict(decision)
        state["decision_history"].append(decision)
        state["status"] = "WAITING_EXECUTOR"
        state["ordinary_task_proposal"] = proposal()
        self.f._json(self.o.PROJECT_STATE, state)
        return turn

    def finish(self, turn):
        return sc.finish_supervisor_turn(
            self.root, turn, processed=True,
            candidate_validator=self.o.validate_supervisor_candidate_snapshot)

    def authorize(self, turn):
        return self.o.register_dispatched_task(
            self.o.load_runtime(), self.f.read_state(),
            expected_control_revision=turn["revision"])

    def claim(self, task):
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return executor_claim.acquire(
                self.root, *[task[k] for k in sc.IDENTITY_KEYS])

    def dispatch_one(self):
        """One full Supervisor decision -> constructed -> authorized dispatch."""
        turn = self.begin()
        self.assertFalse(self.finish(turn))
        task = sc._parse_dispatch_bytes(self.o.TO_ZCODE.read_bytes())
        self.authorize(turn)
        return task

    def expired_authorization(self, task):
        runtime = self.f.read_runtime()
        runtime["authorized_dispatch"]["EXPIRES_AT"] = "2026-09-11T02:00:00+00:00"
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)

    # -- scenarios ----------------------------------------------------------

    def test_parked_resume_converts_without_losing_work(self):
        """Dispatched, unclaimed, quota pause -> parked; Resume re-arms it."""
        self.dispatch_one()
        result = sc.set_pause(self.root)
        self.assertEqual(result["disposition"], "PAUSED_UNCLAIMED_PARKED")
        self.assertNotIn(700120, self.f.read_runtime()["retired_message_ids"])
        self.assertFalse(self.o.TO_ZCODE.exists())

        resumed = simulated_resume(self.f)
        self.assertEqual(resumed["current"]["status"], "RUNNING")
        runtime = self.f.read_runtime()
        state = self.f.read_state()
        # The wait became one bounded Supervisor re-plan, mechanically.
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertIsNone(state["current_task"])
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(pending["reason"], "PARKED_STAGE_RESUME")
        self.assertEqual(pending["event"]["type"], "PARKED_STAGE_RESUME")
        self.assertEqual(pending["event"]["MESSAGE_ID"], 700120)
        self.assertFalse(pending["event"]["authorization_expired"])
        self.assertEqual(pending["decision_attempts"], 0)
        # Not a failure: nothing retired, attempt budget untouched.
        self.assertNotIn(700120, runtime["retired_message_ids"])
        self.assertIn(700120, runtime["parked_message_ids"])
        pause = sc.load_control(self.root)["pause"]
        self.assertEqual(pause["parked_dispatch"]["CONVERTED_REASON"],
                         "PARKED_STAGE_RESUME")
        # A stale worker wake for the parked identity is still refused.
        parked_task = sc._parse_dispatch_bytes(
            (self.root / pause["parked_dispatch"]["QUARANTINE"]).read_bytes())
        self.assertNotEqual(self.claim(parked_task), executor_claim.EXIT_ACQUIRED)

    def test_long_pause_across_authorization_expiry_resumes_safely(self):
        """Expiry during the pause never blocks Resume and never revives a nonce."""
        task = self.dispatch_one()
        self.expired_authorization(task)
        self.assertEqual(sc.set_pause(self.root)["disposition"],
                         "PAUSED_UNCLAIMED_PARKED")
        resumed = simulated_resume(self.f)
        self.assertEqual(resumed["current"]["status"], "RUNNING")
        runtime = self.f.read_runtime()
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(pending["reason"], "PARKED_STAGE_RESUME")
        self.assertTrue(pending["event"]["authorization_expired"])
        # The expired parked dispatch cannot be claimed or revived.
        self.assertNotEqual(self.claim(task), executor_claim.EXIT_ACQUIRED)
        self.assertNotIn(700120, runtime["retired_message_ids"])

    def test_parked_redispatch_keeps_attempt_and_supersedes_parked_id(self):
        """Resume issues a fresh identity for the same logical stage at the
        same attempt number; the parked id is retired only as SUPERSEDED."""
        parked = self.dispatch_one()
        self.assertEqual(parked["ATTEMPT"], 1)
        self.assertEqual(sc.set_pause(self.root)["disposition"],
                         "PAUSED_UNCLAIMED_PARKED")
        simulated_resume(self.f)
        successor = self.dispatch_one()
        self.assertEqual(successor["MESSAGE_ID"], 700121)
        self.assertNotEqual(successor["NONCE"], parked["NONCE"])
        # Quota park did not charge the logical retry budget.
        self.assertEqual(successor["ATTEMPT"], 1)
        runtime = self.f.read_runtime()
        self.assertIn(700120, runtime["retired_message_ids"])
        superseded = next(entry for entry in runtime["executor_retirements"]
                          if entry["MESSAGE_ID"] == 700120)
        self.assertEqual(superseded["REASON"], "SUPERSEDED")
        self.assertEqual(superseded["SUPERSEDED_BY"], 700121)
        self.assertEqual(self.claim(successor), executor_claim.EXIT_ACQUIRED)
        self.assertEqual(self.claim(successor), executor_claim.EXIT_CLAIM_EXISTS)
        self.assertNotEqual(self.claim(parked), executor_claim.EXIT_ACQUIRED)

    def test_repeated_pause_resume_cycles_never_charge_retry_budget(self):
        """Pause/resume flapping across quota windows stays attempt-neutral."""
        for expected_message_id in (700120, 700121, 700122):
            task = self.dispatch_one()
            self.assertEqual(task["MESSAGE_ID"], expected_message_id)
            self.assertEqual(task["ATTEMPT"], 1)
            self.assertEqual(sc.set_pause(self.root)["disposition"],
                             "PAUSED_UNCLAIMED_PARKED")
            simulated_resume(self.f)
        runtime = self.f.read_runtime()
        # Three park cycles: the two superseded ids and the currently parked
        # one; every re-issue kept ATTEMPT 1.
        self.assertEqual(runtime["parked_message_ids"],
                         [700120, 700121, 700122])
        self.assertEqual(sorted(runtime["retired_message_ids"]),
                         [700120, 700121])

    def test_interrupt_pause_on_unclaimed_keeps_retire_semantics(self):
        self.dispatch_one()
        result = sc.set_pause(self.root, interrupt_current=True)
        self.assertEqual(result["disposition"], "PAUSED_UNCLAIMED_RETIRED")
        self.assertIn(700120, self.f.read_runtime()["retired_message_ids"])
        self.assertEqual(self.f.read_state()["status"], "SUPERVISOR_TURN")
        self.assertNotIn("parked_message_ids", self.f.read_runtime())

    def test_pause_claimed_stage_expiring_while_paused_stays_resumable(self):
        """The dogfood hard block: expiry during pause must not wedge Resume."""
        self.f.authorize_fixture(claimed=True)
        runtime = self.f.read_runtime()
        runtime["authorized_dispatch"]["EXPIRES_AT"] = "2026-09-11T02:00:00+00:00"
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)
        self.assertEqual(sc.set_pause(self.root)["status"],
                         "PENDING_AFTER_CURRENT_STAGE")
        # A scheduler watching the pending pause observes the expiry.
        runtime = self.o.load_runtime()
        state = self.f.read_state()
        timeout_event = self.o.executor_timeout_event(runtime, state)
        self.assertIsNotNone(timeout_event)
        self.assertIn(700120, runtime["retired_message_ids"])
        self.o.park_paused_timeout_stage(runtime, timeout_event)
        sc.settle_pause(self.root, "CURRENT_STAGE_TIMED_OUT")
        runtime["status"] = "PAUSED"
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)

        resumed = simulated_resume(self.f)
        self.assertEqual(resumed["current"]["status"], "RUNNING")
        runtime = self.f.read_runtime()
        state = self.f.read_state()
        # Resumable: the wait became a Supervisor stage with the durable
        # pause-timeout event; fencing retirement and budget-neutrality hold.
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(pending["reason"], "EXECUTOR_TIMEOUT_DURING_PAUSE")
        self.assertTrue(pending["event"]["authorization_expired"])
        self.assertIn(700120, runtime["retired_message_ids"])
        self.assertIn(700120, runtime["parked_message_ids"])
        self.assertNotEqual(
            self.claim(self.f.task), executor_claim.EXIT_ACQUIRED)

    def test_completion_during_pause_still_wins_over_park(self):
        """A committed completion visible at pause time is authoritative."""
        self.f.authorize_fixture(claimed=True)
        claim = executor_claim.claim_dir(self.root, 700120, "nonce-700120")
        self.assertTrue(claim.is_dir())
        self.f.completion_entry(status=executor_completion.STATUS_COMMITTED)
        result = sc.set_pause(self.root)
        self.assertEqual(result["status"], "PENDING_AFTER_CURRENT_STAGE")
        self.assertEqual(result["disposition"], "COMPLETION_COMMITTED_WINS")
        self.assertNotIn(700120, self.f.read_runtime()["retired_message_ids"])
        self.assertNotIn("parked_message_ids", self.f.read_runtime())

    def test_parked_conversion_fails_closed_without_park_evidence(self):
        """No SAFE-pause parking fact: retired waits keep failing resume."""
        self.f.authorize_fixture()
        current = self.f.read_state()
        sc.set_pause(self.root)
        # Fabricate a retired wait with no pending timeout evidence.
        self.f._json(self.f.project / "project_state.json", current)
        runtime = self.f.read_runtime()
        runtime["retired_message_ids"] = [700120]
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)
        with mock.patch("runtime_lifecycle.launch") as launch:
            with self.assertRaisesRegex(sc.ControlError,
                                        "retired or lacks matching"):
                simulated_resume(self.f)
            launch.assert_not_called()
        self.assertEqual(sc.pause_status(self.root), "PAUSED")


if __name__ == "__main__":
    unittest.main()
