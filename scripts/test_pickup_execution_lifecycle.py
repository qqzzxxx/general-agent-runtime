"""PICKUP-EXECUTION-LIFECYCLE: bounded pickup authorization vs execution budget.

Acceptance scenarios for the unified executor lifecycle state model:

* WAITING_FOR_CLAIM is a scheduling phase bounded by EXPIRES_AT (registration
  time + scheduler grace). Expiring unclaimed is a worker-availability
  recovery: the identity dies, the MESSAGE_ID parks, and ATTEMPT is unchanged.
* CLAIMED/EXECUTING owns the budget: CLAIMED_AT (durable in claim.json) plus
  MAX_TIME (recorded on the authorization). A late-but-legal claim gets the
  full MAX_TIME; restarting never resets it; exhausting it charges the retry
  exactly like any real execution.

All scenarios run through the production boundaries: Supervisor turn ->
ordinary construction -> authorization -> claim helper -> fence -> completion.
"""
import contextlib
import hashlib
import io
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor_claim
import executor_completion as completion
import executor_fence as fence
import supervisor_control as sc
import test_supervisor_control as core
from resume_lifecycle_fixture import simulated_resume


def proposal():
    return {"logical_task": "summary", "logical_stage": "check evidence",
            "objective": "Produce a checked summary of the supplied evidence.",
            "inputs": ["evidence/source.txt"], "outputs": ["reports/summary.md"],
            "acceptance_criteria": ["Every finding cites supporting evidence."],
            "max_time": 900, "max_retries": 2}


def frozen_datetime(moment):
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):  # noqa: N802 - datetime classmethod API
            return moment if tz is None else moment.astimezone(tz)
    return FrozenDatetime


class PickupExecutionLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = core.SupervisorControlTests()
        self.f.setUp()
        self.root = self.f.root
        self.o = self.f.configured_orchestrator()
        self.addCleanup(self.f.tearDown)
        self.tokens = {}

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
        self.f._json(self.f.project / "project_state.json", state)
        return turn

    def finish_turn(self, turn):
        return sc.finish_supervisor_turn(
            self.root, turn, processed=True,
            candidate_validator=self.o.validate_supervisor_candidate_snapshot)

    def authorize(self, turn):
        return self.o.register_dispatched_task(
            self.o.load_runtime(), self.f.read_state(),
            expected_control_revision=turn["revision"])

    def claim(self, task, *, record_token=False):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(errors):
            code = executor_claim.acquire(
                self.root, *[task[k] for k in sc.IDENTITY_KEYS])
        if record_token and code == 0:
            self.tokens[task["MESSAGE_ID"]] = \
                output.getvalue().split("claim_token=")[1].strip()
        return code, errors.getvalue()

    def dispatch_one(self):
        """One full Supervisor decision -> constructed -> authorized dispatch."""
        turn = self.begin()
        self.assertFalse(self.finish_turn(turn))
        task = sc._parse_dispatch_bytes(self.o.TO_ZCODE.read_bytes())
        self.authorize(turn)
        return task

    def mutate_runtime(self, mutate):
        runtime = self.f.read_runtime()
        mutate(runtime)
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)
        return runtime

    def expire_pickup(self, task):
        def mutate(runtime):
            runtime["authorized_dispatch"]["EXPIRES_AT"] = "2026-01-01T00:00:00+00:00"
        return self.mutate_runtime(mutate)

    def claim_record(self, task):
        claim, path = completion.load_claim(
            self.root, {key: task[key] for key in sc.IDENTITY_KEYS})
        self.assertIsNotNone(claim)
        return claim, path

    def travel(self, module, moment):
        return mock.patch.object(module, "datetime", frozen_datetime(moment))

    def read_expiry(self, task):
        return datetime.fromisoformat(
            self.f.read_runtime()["authorized_dispatch"]["EXPIRES_AT"])

    def retirement_reason(self, runtime, message_id):
        entry = next(item for item in runtime["executor_retirements"]
                     if item["MESSAGE_ID"] == message_id)
        return entry["REASON"]

    def parked_ids(self):
        return self.f.read_runtime().get("parked_message_ids") or []

    # -- scenarios ----------------------------------------------------------

    def test_unclaimed_pickup_timeout_parks_without_retry_charge(self):
        """Long unclaimed wait expires: identity dies, ATTEMPT is unchanged."""
        task = self.dispatch_one()
        self.assertEqual(task["ATTEMPT"], 1)
        self.expire_pickup(task)
        event = self.o.executor_timeout_event(
            self.o.load_runtime(), self.f.read_state())
        # The wait became one bounded scheduling recovery, mechanically.
        self.assertEqual(event["type"], "PICKUP_TIMEOUT_STAGE_RESUME")
        self.assertEqual(event["message_id"], task["MESSAGE_ID"])
        self.assertTrue(event["authorization_expired"])
        state = self.f.read_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertIsNone(state["current_task"])
        runtime = self.f.read_runtime()
        self.assertIn(task["MESSAGE_ID"], runtime["retired_message_ids"])
        self.assertEqual(self.retirement_reason(runtime, task["MESSAGE_ID"]),
                         "PICKUP_TIMEOUT")
        self.assertIn(task["MESSAGE_ID"], self.parked_ids())
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(pending["reason"], "PICKUP_TIMEOUT_STAGE_RESUME")
        self.assertEqual(pending["event"]["type"], event["type"])
        # The live inbox is gone; a stale wake has nothing to claim.
        self.assertFalse(self.o.TO_ZCODE.exists())
        code, _ = self.claim(task)
        self.assertNotEqual(code, executor_claim.EXIT_ACQUIRED)
        # Re-dispatch of the same logical stage keeps the attempt number.
        successor = self.dispatch_one()
        self.assertEqual(successor["MESSAGE_ID"], task["MESSAGE_ID"] + 1)
        self.assertNotEqual(successor["NONCE"], task["NONCE"])
        self.assertEqual(successor["ATTEMPT"], 1)

    def test_repeated_unclaimed_pickups_never_exhaust_retry_budget(self):
        """Every unclaimed expiry stays attempt-neutral for the logical stage."""
        for expected_message_id in (700120, 700121, 700122):
            task = self.dispatch_one()
            self.assertEqual(task["MESSAGE_ID"], expected_message_id)
            self.assertEqual(task["ATTEMPT"], 1)
            self.expire_pickup(task)
            event = self.o.executor_timeout_event(
                self.o.load_runtime(), self.f.read_state())
            self.assertEqual(event["type"], "PICKUP_TIMEOUT_STAGE_RESUME")
        runtime = self.f.read_runtime()
        self.assertEqual(runtime["parked_message_ids"],
                         [700120, 700121, 700122])
        self.assertEqual(sorted(runtime["retired_message_ids"]),
                         [700120, 700121, 700122])

    def test_claim_after_pickup_expiry_refused(self):
        """A worker waking after the pickup window can never acquire."""
        task = self.dispatch_one()
        self.expire_pickup(task)
        code, errors = self.claim(task)
        self.assertEqual(code, executor_claim.EXIT_ERROR)
        self.assertIn("pickup_window_expired", errors)
        self.assertFalse(self.claim_record_path_exists(task))
        # The scheduling recovery then re-plans without charging the budget.
        event = self.o.executor_timeout_event(
            self.o.load_runtime(), self.f.read_state())
        self.assertEqual(event["type"], "PICKUP_TIMEOUT_STAGE_RESUME")
        self.assertEqual(self.dispatch_one()["ATTEMPT"], 1)

    def claim_record_path_exists(self, task):
        _, path = completion.load_claim(
            self.root, {key: task[key] for key in sc.IDENTITY_KEYS})
        return path.exists()

    def test_late_legal_claim_gets_full_execution_budget(self):
        """A claim one second before expiry owns MAX_TIME from CLAIMED_AT."""
        task = self.dispatch_one()
        authorization = self.f.read_runtime()["authorized_dispatch"]
        self.assertEqual(authorization["MAX_TIME"], 900)
        expiry = self.read_expiry(task)
        late = expiry - timedelta(seconds=1)
        with self.travel(executor_claim, late):
            code, _ = self.claim(task, record_token=True)
        self.assertEqual(code, executor_claim.EXIT_ACQUIRED)
        claim, _ = self.claim_record(task)
        claimed_at = datetime.fromisoformat(claim["CLAIMED_AT"])
        self.assertEqual(claimed_at, late)
        identity = {key: task[key] for key in sc.IDENTITY_KEYS}
        # Well past the pickup window the wait must not have burned the
        # budget: the watchdog stays silent and the fence stays open.
        after_window = expiry + timedelta(minutes=10)
        runtime = self.o.load_runtime()
        state = self.f.read_state()
        with mock.patch.object(self.o, "utc_now", return_value=after_window):
            self.assertIsNone(self.o.executor_timeout_event(runtime, state))
        with self.travel(fence, after_window):
            work = fence.prepare(self.root, identity,
                                 claim_token=self.tokens[task["MESSAGE_ID"]])
        self.assertTrue(work.is_dir())
        # The budget ends exactly at CLAIMED_AT + MAX_TIME.
        exhausted = claimed_at + timedelta(seconds=task["MAX_TIME"] + 1)
        with self.travel(fence, exhausted):
            with self.assertRaisesRegex(fence.FenceError, "expired"):
                fence.check(self.root, identity,
                            claim_token=self.tokens[task["MESSAGE_ID"]])

    def test_completion_inside_execution_lease_after_long_wait(self):
        """Publication and completion survive far past the dispatch instant."""
        task = self.dispatch_one()
        expiry = self.read_expiry(task)
        late = expiry - timedelta(seconds=1)
        with self.travel(executor_claim, late):
            code, _ = self.claim(task, record_token=True)
        self.assertEqual(code, executor_claim.EXIT_ACQUIRED)
        identity = {key: task[key] for key in sc.IDENTITY_KEYS}
        after_window = expiry + timedelta(minutes=10)
        with self.travel(fence, after_window):
            work = fence.prepare(self.root, identity,
                                 claim_token=self.tokens[task["MESSAGE_ID"]])
            candidate = work / "workspace" / "summary.txt"
            candidate.write_text("attempt output", encoding="utf-8")
            fence.publish(self.root, identity, "workspace/summary.txt",
                          hashlib.sha256(candidate.read_bytes()).hexdigest(),
                          claim_token=self.tokens[task["MESSAGE_ID"]])
        staging = self.f.project / "completion_staging" / str(task["MESSAGE_ID"])
        staging.mkdir(parents=True, exist_ok=True)
        self.f._json(staging / "staging.json", {
            "COMPLETION_STAGING_SCHEMA_VERSION": 1, **identity,
            "PROJECT_ID": self.f.PROJECT, "STATUS": "STAGING_READY",
            "CREATED_AT": self.o.stamp(),
            "RECEIPT": {**identity, "STATUS": "COMPLETED"},
            "DELIVERABLES": [{"path": "workspace/summary.txt",
                              "sha256": hashlib.sha256(
                                  (self.f.project / "workspace/summary.txt")
                                  .read_bytes()).hexdigest()}],
        })
        self.assertEqual(
            completion.commit(self.root, staging,
                              claim_token=self.tokens[task["MESSAGE_ID"]]),
            completion.EXIT_COMMITTED)

    def test_claimed_execution_timeout_charges_retry_budget(self):
        """Only a real claimed execution timeout consumes an attempt."""
        task = self.dispatch_one()
        self.assertEqual(task["ATTEMPT"], 1)
        code, _ = self.claim(task, record_token=True)
        self.assertEqual(code, executor_claim.EXIT_ACQUIRED)
        claim, _ = self.claim_record(task)
        claimed_at = datetime.fromisoformat(claim["CLAIMED_AT"])
        exhausted = claimed_at + timedelta(seconds=task["MAX_TIME"] + 1)
        with mock.patch.object(self.o, "utc_now", return_value=exhausted):
            event = self.o.executor_timeout_event(
                self.o.load_runtime(), self.f.read_state())
        self.assertEqual(event["type"], "EXECUTOR_TIMEOUT")
        self.assertEqual(event["message_id"], task["MESSAGE_ID"])
        runtime = self.f.read_runtime()
        self.assertIn(task["MESSAGE_ID"], runtime["retired_message_ids"])
        self.assertEqual(self.retirement_reason(runtime, task["MESSAGE_ID"]),
                         "EXECUTOR_TIMEOUT")
        self.assertNotIn(task["MESSAGE_ID"], self.parked_ids())
        # A real execution is charged: the successor runs at ATTEMPT 2.
        successor = self.dispatch_one()
        self.assertEqual(successor["ATTEMPT"], 2)
        # The timed-out owner can never publish or complete again.
        identity = {key: task[key] for key in sc.IDENTITY_KEYS}
        with self.assertRaises(fence.FenceError):
            fence.check(self.root, identity,
                        claim_token=self.tokens[task["MESSAGE_ID"]])
        staging = self.f.project / "completion_staging" / f"stale-{task['MESSAGE_ID']}"
        staging.mkdir(parents=True, exist_ok=True)
        self.f._json(staging / "staging.json", {
            "COMPLETION_STAGING_SCHEMA_VERSION": 1, **identity,
            "PROJECT_ID": self.f.PROJECT, "STATUS": "STAGING_READY",
            "CREATED_AT": self.o.stamp(),
            "RECEIPT": {**identity, "STATUS": "COMPLETED"},
        })
        with self.assertRaises(completion.CompletionError):
            completion.commit(self.root, staging,
                              claim_token=self.tokens[task["MESSAGE_ID"]])

    def test_restart_preserves_execution_budget_after_pickup_window(self):
        """Restart neither resets the budget nor rejects a working owner."""
        task = self.dispatch_one()
        code, _ = self.claim(task, record_token=True)
        self.assertEqual(code, executor_claim.EXIT_ACQUIRED)
        claim, _ = self.claim_record(task)
        claimed_at = datetime.fromisoformat(claim["CLAIMED_AT"])
        # Wall clock moved far past the pickup window while the owner worked.
        self.expire_pickup(task)
        runtime = self.o.load_runtime()
        state = self.f.read_state()
        reauthorized = self.o.register_dispatched_task(
            runtime, state, allow_same_identity=True)
        self.assertEqual(reauthorized["MESSAGE_ID"], task["MESSAGE_ID"])
        authorization = self.f.read_runtime()["authorized_dispatch"]
        self.assertEqual(authorization["EXPIRES_AT"], "2026-01-01T00:00:00+00:00")
        self.assertEqual(authorization["MAX_TIME"], task["MAX_TIME"])
        self.assertEqual(
            self.o.execution_deadline_for(authorization, claim, task),
            claimed_at + timedelta(seconds=task["MAX_TIME"]))
        self.assertEqual(
            runtime.get("recovered_owned_executor_attempt", {}).get("policy"),
            "PRESERVE_ALREADY_CLAIMED_ATTEMPT")
        # The owner is still fully authorized on its unchanged timeline, and
        # the watchdog stays silent inside the preserved budget.
        inside = claimed_at + timedelta(seconds=task["MAX_TIME"] - 1)
        with self.travel(fence, inside):
            work = fence.prepare(self.root,
                                 {key: task[key] for key in sc.IDENTITY_KEYS},
                                 claim_token=self.tokens[task["MESSAGE_ID"]])
        self.assertTrue(work.is_dir())
        with mock.patch.object(self.o, "utc_now", return_value=inside):
            self.assertIsNone(self.o.executor_timeout_event(runtime, state))

    def test_pickup_conversion_keeps_pause_resume_lifecycle_consistent(self):
        """The pickup park composes with QUOTA-PAUSE-PARK-V1 pause/resume."""
        task = self.dispatch_one()
        self.expire_pickup(task)
        event = self.o.executor_timeout_event(
            self.o.load_runtime(), self.f.read_state())
        self.assertEqual(event["type"], "PICKUP_TIMEOUT_STAGE_RESUME")
        # A user pause after the conversion parks nothing new and resumes.
        pause = sc.set_pause(self.root)
        self.assertEqual(pause["disposition"], "PAUSED_IDLE")
        resumed = simulated_resume(self.f)
        self.assertEqual(resumed["current"]["status"], "RUNNING")
        runtime = self.f.read_runtime()
        self.assertEqual(runtime["pending_supervisor_event"]["reason"],
                         "PICKUP_TIMEOUT_STAGE_RESUME")
        self.assertIn(task["MESSAGE_ID"], runtime["parked_message_ids"])
        # The re-plan after resume still keeps the attempt number.
        successor = self.dispatch_one()
        self.assertEqual(successor["ATTEMPT"], 1)
        self.assertIn(task["MESSAGE_ID"], self.parked_ids())


if __name__ == "__main__":
    unittest.main()
