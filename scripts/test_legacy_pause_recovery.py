"""LEGACY-PAUSE-RECOVERY-V2: the one canonical legacy pause recovery.

The fixture mirrors the real research-dogfood scene: attempt 1 was consumed,
attempt 2 was retired EXECUTOR_TIMEOUT with a stale claim and workspace, and
attempt 3 was retired PAUSE_BEFORE_CLAIM by the legacy Safe Pause before any
Executor claim. The canonical tool must prove that unexecuted shape
condition by condition, convert it to QUOTA-PAUSE-PARK-V1 semantics (the
append-only parked_message_ids budget record plus one durable
LEGACY_PARKED_STAGE_RESUME event), keep the successor attempt number and a
fresh identity, never revive the retired identity, keep real executor
timeouts budget-charging, and be exactly-once/idempotent.

The second class drives the recovery through the real Supervisor
registration path (begin_supervisor_turn / finish_supervisor_turn /
register_dispatched_task) instead of the hand-built state fixture, so the
successor dispatch is the production-constructed identity.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = RUNTIME_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import executor_claim
import executor_completion as completion
import ordinary_dispatch as od
import supervisor_control as sc
import test_supervisor_control as core

RECOVERY_PATH = SCRIPTS / "legacy_pause_recovery.py"
_spec = importlib.util.spec_from_file_location("legacy_pause_recovery_under_test", RECOVERY_PATH)
lpr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lpr)

PROJECT_ID = "legacy-test"
LOGICAL_TASK = "fixture_parameter_research"
LOGICAL_STAGE = "controlled_cost_study"


def _label(kind: str, value) -> str:
    return kind + "-" + sc.sha256_bytes(
        sc.canonical_json_bytes([PROJECT_ID, value]))[:24]


TASK_ID = _label("task", LOGICAL_TASK)
STAGE_ID = _label("stage", [LOGICAL_TASK, LOGICAL_STAGE])
CONSUMED_ID, TIMEOUT_ID, PAUSED_ID = 700108, 700109, 700110
NONCE = {
    CONSUMED_ID: "nonce-consumed-108",
    TIMEOUT_ID: "nonce-timeout-109",
    PAUSED_ID: "nonce-paused-110",
}


def _identity(message_id: int, attempt: int) -> dict:
    return {"MESSAGE_ID": message_id, "TASK_ID": TASK_ID, "STAGE_ID": STAGE_ID,
            "ATTEMPT": attempt, "NONCE": NONCE[message_id]}


def _digest(nonce: str) -> str:
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]


def _task(message_id: int, attempt: int) -> dict:
    return {
        "PROTOCOL_VERSION": 2, "CLAIM_PROTOCOL_VERSION": 1,
        "MESSAGE_ID": message_id, "TASK_ID": TASK_ID, "STAGE_ID": STAGE_ID,
        "ATTEMPT": attempt, "NONCE": NONCE[message_id],
        "LOGICAL_TASK": LOGICAL_TASK, "LOGICAL_STAGE": LOGICAL_STAGE,
        "OBJECTIVE": "fixture objective", "INPUTS": ["fixture input"],
        "OUTPUTS": ["fixture output"], "ACCEPTANCE_CRITERIA": ["fixture criterion"],
        "MAX_TIME": 2700, "MAX_RETRIES": 2,
    }


def _dispatch_bytes(task: dict) -> bytes:
    return (f"MESSAGE_ID: {task['MESSAGE_ID']}\nTASK_ID: {task['TASK_ID']}\n"
            f"STAGE_ID: {task['STAGE_ID']}\n\n```json\n"
            + json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n").encode("utf-8")


_ORIGIN = {"originating_control_revision": 2,
           "supervisor_turn_id": "supervisor-turn-fixture",
           "decision_receipt_sha256": "a" * 64}


class LegacyPauseRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="legacy-pause-recovery-")
        self.root = Path(self.temp.name)
        self.m = lpr.load_runtime_module(RUNTIME_ROOT, self.root)
        self.m.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        for path in (self.m.CONTROL, self.m.LOGS, self.m.HANDOFF_ARCHIVE,
                     self.root / "handoff" / "executor_claims",
                     self.root / "handoff" / "quarantine",
                     self.root / "handoff" / "executor_finishes",
                     self.root / "handoff" / "executor_publications",
                     self.root / "handoff" / "completion_ledger" / "staged",
                     self.root / "control" / "ordinary_dispatch_preparations"):
            path.mkdir(parents=True, exist_ok=True)
        self.project_root = self.root / "projects" / PROJECT_ID
        for name in ("attempt_workspaces", "completion_staging", "evidence",
                     "reports", "workspace"):
            (self.project_root / name).mkdir(parents=True)
        self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": PROJECT_ID,
            "project_root": f"projects/{PROJECT_ID}"})
        (self.project_root / "PROJECT_GOAL.md").write_text("# Fixture goal\n", encoding="utf-8")
        (self.project_root / "RESEARCH_STATE.md").write_text("# Fixture memory\n", encoding="utf-8")

        # Dispatch history: 700108 consumed, 700109 timeout-retired with stale
        # claim/workspace, 700110 pause-retired unclaimed (the recovery target).
        self.authorization = {}
        for message_id, attempt in ((CONSUMED_ID, 1), (TIMEOUT_ID, 2), (PAUSED_ID, 3)):
            task = _task(message_id, attempt)
            raw = _dispatch_bytes(task)
            binding = sc.archive_dispatch(self.root, PROJECT_ID, task, raw, origin=_ORIGIN)
            authorization = {
                "schema_version": 1, **{key: task[key] for key in sc.IDENTITY_KEYS},
                "TO_ZCODE_SHA256": hashlib.sha256(raw).hexdigest(),
                "AUTHORIZED_AT": sc.now_iso(), "PROJECT_ID": PROJECT_ID,
                "FENCE_VERSION": 1,
                "EXPIRES_AT": "2020-01-01T00:00:00+00:00",
                "SUPERVISOR_CONTROL_ORIGIN": dict(_ORIGIN),
                "SUPERVISOR_DISPATCH_ARCHIVE": {
                    key: binding[key] for key in ("schema_version", "metadata_file",
                                                  "archive_file", "authorization_file",
                                                  "dispatch_sha256")},
            }
            sc.seal_dispatch_authorization(self.root, authorization)
            self.authorization[message_id] = authorization
        self.paused_bytes = _dispatch_bytes(_task(PAUSED_ID, 3))

        consumed = self.authorization[CONSUMED_ID]
        brief = f"fixture consumed Executor receipt for {CONSUMED_ID}\n".encode("utf-8")
        brief_hash = hashlib.sha256(brief).hexdigest()
        (self.m.HANDOFF_ARCHIVE
         / f"brief-{CONSUMED_ID}-{NONCE[CONSUMED_ID][:12]}-consumed-{brief_hash[:12]}.md"
         ).write_bytes(brief)
        for message_id in (CONSUMED_ID, TIMEOUT_ID):
            claim_dir = (self.root / "handoff" / "executor_claims"
                         / f"{message_id}-{_digest(NONCE[message_id])}.claim")
            claim_dir.mkdir()
            self.m.atomic_json(claim_dir / "claim.json", {
                "CLAIM_PROTOCOL_VERSION": 1,
                **{key: self.authorization[message_id][key] for key in sc.IDENTITY_KEYS},
                "CLAIMED_AT": sc.now_iso()})
        self.quarantine_rel = (
            f"handoff/quarantine/to-zcode-{PAUSED_ID}-pause-"
            f"{hashlib.sha256(self.paused_bytes).hexdigest()[:12]}.md")
        (self.root / self.quarantine_rel).write_bytes(self.paused_bytes)
        (self.project_root / "attempt_workspaces"
         / f"{TIMEOUT_ID}-{_digest(NONCE[TIMEOUT_ID])}").mkdir()

        self.m.atomic_write(
            self.m.ZCODE_LAST_PROCESSED,
            "".join(f"{key}={value}\n" for key, value in _identity(CONSUMED_ID, 1).items()))

        self.state = {
            "schema_version": 4, "project_id": PROJECT_ID, "status": "SUPERVISOR_TURN",
            "current_task": None, "next_message_id": PAUSED_ID + 1,
            "updated_at": sc.now_iso(),
            "last_supervisor_decision": {"decision": "CONTINUE"},
            "decision_history": [{"decision": "CONTINUE"}],
        }
        self._write_state(self.state)
        self.runtime = {
            "schema_version": 2, "status": "PAUSED",
            "last_consumed_message_id": CONSUMED_ID,
            "last_consumed_nonce": NONCE[CONSUMED_ID],
            "last_consumed_brief_sha256": brief_hash,
            "last_dispatched_message_id": PAUSED_ID,
            "last_dispatched_nonce": NONCE[PAUSED_ID],
            "authorized_dispatch": self.authorization[PAUSED_ID],
            "retired_message_ids": [TIMEOUT_ID, PAUSED_ID],
            "executor_retirements": [
                {**_identity(TIMEOUT_ID, 2), "RETIRED_AT": sc.now_iso(),
                 "REASON": "EXECUTOR_TIMEOUT", "SUPERSEDED_BY": None},
                {**_identity(PAUSED_ID, 3), "RETIRED_AT": sc.now_iso(),
                 "REASON": "PAUSE_BEFORE_CLAIM", "SUPERSEDED_BY": None},
            ],
            "claim_protocol_required_from_message_id": 700001,
            "consecutive_codex_without_executor": 0,
        }
        self._write_runtime(self.runtime)
        self.control = {
            "schema_version": 1, "revision": 3, "intervention_generation": 0,
            "pause": {"status": "PAUSED", "mode": "SAFE", "requested_at": sc.now_iso(),
                      "transaction_receipt": {
                          "action": "RETIRE",
                          "subject_identity": _identity(PAUSED_ID, 3),
                          "retirement_reason": "PAUSE_BEFORE_CLAIM",
                          "disposition": "PAUSED_UNCLAIMED_RETIRED",
                          "quarantine": self.quarantine_rel}},
        }
        sc.save_control(self.root, self.control)

    def tearDown(self):
        self.temp.cleanup()

    # --- fixture helpers -----------------------------------------------------

    def _identity_of(self, message_id: int) -> dict:
        return {key: self.authorization[message_id][key] for key in sc.IDENTITY_KEYS}

    def _write_state(self, value):
        sc._atomic_json(self.project_root / "project_state.json", value)

    def _read_state(self):
        return sc._read_json(self.project_root / "project_state.json")

    def _write_runtime(self, value):
        sc._atomic_json(self.m.RUNTIME_STATE, value)

    def _read_runtime(self):
        return sc._read_json(self.m.RUNTIME_STATE)

    def _construct_successor(self, max_retries=2):
        proposal = od.validate_proposal({
            "logical_task": LOGICAL_TASK, "logical_stage": LOGICAL_STAGE,
            "objective": "fixture objective", "inputs": ["fixture input"],
            "outputs": ["fixture output"], "acceptance_criteria": ["fixture criterion"],
            "max_retries": max_retries,
        })
        turn = {"PROJECT_ID": PROJECT_ID,
                "dispatch_projection_before": {"next_message_id": PAUSED_ID + 1}}
        return od._construct(self.root, turn, proposal)

    def _proof_failures(self):
        try:
            return lpr.build_proof(self.root, PAUSED_ID)["checks"]
        except lpr.RecoveryError as exc:
            return str(exc)

    def _assert_refused(self, pattern="refused"):
        with self.assertRaisesRegex(lpr.RecoveryError, pattern):
            lpr.apply_recovery(self.root, PAUSED_ID)
        self.assertNotIn(lpr.PARKED_KEY, self._read_runtime())

    # --- the recovery itself --------------------------------------------------

    def test_prove_passes_mechanically_and_writes_nothing(self):
        sha = lambda: hashlib.sha256(
            b"".join(p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file())
        ).hexdigest()
        before = sha()
        report = lpr.build_proof(self.root, PAUSED_ID)
        self.assertTrue(report["ok"], json.dumps(report["checks"], indent=2))
        self.assertEqual(report["recovery"], "LEGACY-PAUSE-RECOVERY-V2")
        self.assertEqual(sha(), before)
        binding = report["facts"]["logical_binding"]
        self.assertEqual(binding["LOGICAL_TASK"], LOGICAL_TASK)
        self.assertEqual(binding["ATTEMPT"], 3)
        previous = report["facts"]["previous_logical_attempts"]
        self.assertEqual([item["MESSAGE_ID"] for item in previous],
                         [CONSUMED_ID, TIMEOUT_ID])
        self.assertEqual(previous[-1]["REASON"], "EXECUTOR_TIMEOUT")
        self.assertTrue(previous[-1]["attempt_workspace"])

    def test_apply_records_parked_id_and_durable_event(self):
        result = lpr.apply_recovery(self.root, PAUSED_ID)
        self.assertEqual(result["status"], "RECOVERED")
        runtime = self._read_runtime()
        self.assertEqual(runtime[lpr.PARKED_KEY], [PAUSED_ID])
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(pending["reason"], "LEGACY_PARKED_STAGE_RESUME")
        event = pending["event"]
        self.assertEqual(event["type"], "LEGACY_PARKED_STAGE_RESUME")
        self.assertEqual(event["MESSAGE_ID"], PAUSED_ID)
        self.assertEqual(event["retirement"]["REASON"], "PAUSE_BEFORE_CLAIM")
        self.assertTrue(event["authorization_expired"])
        self.assertEqual(event["previous_attempt_evidence"]["MESSAGE_ID"], TIMEOUT_ID)
        self.assertIn("never executed", event["note"])
        self.assertTrue(event["recovery_record"].startswith("projects/"))
        record_bytes = (self.root / event["recovery_record"]).read_bytes()
        self.assertEqual(hashlib.sha256(record_bytes).hexdigest(),
                         event["recovery_record_sha256"])
        self.assertEqual(pending["pause_recovery"]["recovery_record_sha256"],
                         event["recovery_record_sha256"])
        # History is immutable: retirement, authorization and state stay exact.
        self.assertEqual(runtime["retired_message_ids"], [TIMEOUT_ID, PAUSED_ID])
        self.assertEqual(runtime["authorized_dispatch"], self.authorization[PAUSED_ID])
        self.assertEqual(runtime["last_dispatched_nonce"], NONCE[PAUSED_ID])
        state = self._read_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertIsNone(state["current_task"])
        self.assertEqual(state["next_message_id"], PAUSED_ID + 1)
        record = sc._read_json(self.project_root / "legacy_pause_recoveries"
                               / f"legacy-pause-recovery-{PAUSED_ID}.json")
        self.assertTrue(record["proof"]["ok"])
        self.assertTrue(record["supervisor_context"]["not_executed"])
        self.assertTrue(record["supervisor_context"]["not_scientific_failure"])

    def test_apply_is_idempotent(self):
        first = lpr.apply_recovery(self.root, PAUSED_ID)
        digest = self.m.RUNTIME_STATE.read_bytes()
        second = lpr.apply_recovery(self.root, PAUSED_ID)
        self.assertEqual(first["status"], "RECOVERED")
        self.assertEqual(second["status"], "ALREADY_RECOVERED")
        self.assertFalse(second["changed"])
        self.assertEqual(self.m.RUNTIME_STATE.read_bytes(), digest)
        runtime = self._read_runtime()
        self.assertEqual(runtime[lpr.PARKED_KEY], [PAUSED_ID])

    def test_apply_refuses_foreign_pending_event(self):
        self._write_runtime({**self.runtime, "pending_supervisor_event": {
            "reason": "EXECUTOR_RESULT_READY", "event": {"type": "EXECUTOR_RESULT_READY"},
            "recorded_at": sc.now_iso(), "decision_attempts": 0,
            "retry_exhausted": False}})
        with self.assertRaisesRegex(lpr.RecoveryError, "refused"):
            lpr.apply_recovery(self.root, PAUSED_ID)
        self.assertNotIn(lpr.PARKED_KEY, self._read_runtime())

    def test_apply_refuses_when_not_paused(self):
        self._write_runtime({**self.runtime, "status": "RUNNING"})
        self._assert_refused()

    def test_apply_refuses_when_control_pause_running(self):
        control = copy.deepcopy(self.control)
        control["pause"]["status"] = "RUNNING"
        sc.save_control(self.root, control)
        self._assert_refused()

    def test_apply_refuses_live_scheduler_owner(self):
        self._write_runtime({**self.runtime, "scheduler_owner": {
            "pid": os.getpid(), "owner_id": "fixture"}})
        self._assert_refused()

    def test_apply_refuses_under_control_flags(self):
        for flag in ("STOP", "HUMAN_REVIEW"):
            (self.root / "control" / flag).write_text("", encoding="utf-8")
            try:
                self._assert_refused()
            finally:
                (self.root / "control" / flag).unlink()

    # --- the accounting fix ----------------------------------------------------

    def test_next_dispatch_keeps_logical_attempt_and_fresh_identity(self):
        lpr.apply_recovery(self.root, PAUSED_ID)
        task = self._construct_successor()
        self.assertEqual(task["MESSAGE_ID"], PAUSED_ID + 1)
        self.assertEqual(task["ATTEMPT"], 3)
        self.assertNotEqual(task["NONCE"], NONCE[PAUSED_ID])

    def test_without_parking_the_corruption_reproduces(self):
        # Regression guard: the parked record is what keeps the attempt at 3.
        # Without it the constructor counts the paused tip and exceeds the
        # bounded retry budget (attempt 4 > max_retries 2 + 1).
        lpr.apply_recovery(self.root, PAUSED_ID)
        runtime = self._read_runtime()
        runtime.pop(lpr.PARKED_KEY)
        self._write_runtime(runtime)
        with self.assertRaisesRegex(sc.CandidateValidationError, "budget exhausted"):
            self._construct_successor()

    def test_executor_timeout_still_consumes_retry(self):
        lpr.apply_recovery(self.root, PAUSED_ID)
        # attempt 3 is re-issued with max_retries 1: the timeout-retired attempt
        # 2 still consumed the budget, so only attempt 2 would be re-issuable.
        with self.assertRaisesRegex(sc.CandidateValidationError, "budget exhausted"):
            self._construct_successor(max_retries=1)

    def test_park_family_exclusions_still_apply(self):
        lpr.apply_recovery(self.root, PAUSED_ID)
        runtime = self._read_runtime()
        # A pause-caused timeout park (EXECUTOR_TIMEOUT_DURING_PAUSE) uses the
        # same record; both facts must stay excluded from attempt accounting.
        runtime[lpr.PARKED_KEY] = [TIMEOUT_ID, PAUSED_ID]
        self._write_runtime(runtime)
        task = self._construct_successor()
        self.assertEqual(task["ATTEMPT"], 2)

    def test_malformed_parked_list_fails_closed(self):
        lpr.apply_recovery(self.root, PAUSED_ID)
        runtime = self._read_runtime()
        runtime[lpr.PARKED_KEY] = str(PAUSED_ID)
        self._write_runtime(runtime)
        with self.assertRaises(sc.CandidateValidationError):
            self._construct_successor()

    # --- fail-closed scenes ----------------------------------------------------

    def test_claimed_target_is_refused(self):
        claim_dir = (self.root / "handoff" / "executor_claims"
                     / f"{PAUSED_ID}-{_digest(NONCE[PAUSED_ID])}.claim")
        claim_dir.mkdir()
        self.m.atomic_json(claim_dir / "claim.json", {
            "CLAIM_PROTOCOL_VERSION": 1, **self._identity_of(PAUSED_ID),
            "CLAIMED_AT": sc.now_iso()})
        self._assert_refused("claim")

    def test_workspace_target_is_refused(self):
        (self.project_root / "attempt_workspaces"
         / f"{PAUSED_ID}-{_digest(NONCE[PAUSED_ID])}").mkdir()
        self._assert_refused("workspace")

    def test_completion_staging_is_refused(self):
        staged = self.project_root / "completion_staging" / f"{PAUSED_ID}-staged"
        staged.mkdir()
        self.m.atomic_json(staged / "staging.json", {
            "MESSAGE_ID": PAUSED_ID, "STATUS": "STAGING_READY"})
        self._assert_refused("completion")

    def test_ledger_completion_is_refused(self):
        entry = {"COMPLETION_PROTOCOL_VERSION": completion.COMPLETION_PROTOCOL_VERSION,
                 "STATUS": completion.STATUS_COMMITTED, "MESSAGE_ID": PAUSED_ID}
        (self.root / "handoff" / "completion_ledger"
         / f"completion-{PAUSED_ID}-fixture.json").write_text(
            json.dumps(entry), encoding="utf-8")
        self._assert_refused("completion")

    def test_finish_hint_is_refused(self):
        hint = self.root / "handoff" / "executor_finishes" / f"completion-{PAUSED_ID}-x.json"
        hint.write_text(json.dumps({"MESSAGE_ID": PAUSED_ID}), encoding="utf-8")
        self._assert_refused("completion")

    def test_hash_archive_mismatch_is_refused(self):
        binding = self.authorization[PAUSED_ID]["SUPERVISOR_DISPATCH_ARCHIVE"]
        (self.root / binding["archive_file"]).write_bytes(b"tampered dispatch bytes")
        self._assert_refused("archive")

    def test_intervention_timeout_reason_is_refused(self):
        runtime = self._read_runtime()
        runtime["executor_retirements"][-1]["REASON"] = "EXECUTOR_TIMEOUT"
        self._write_runtime(runtime)
        self._assert_refused("retirement record")

    def test_successor_retirement_is_refused(self):
        runtime = self._read_runtime()
        runtime["executor_retirements"][-1]["SUPERSEDED_BY"] = PAUSED_ID + 1
        self._write_runtime(runtime)
        self._assert_refused("retirement record")

    def test_missing_pause_receipt_is_refused(self):
        control = copy.deepcopy(self.control)
        control["pause"].pop("transaction_receipt")
        sc.save_control(self.root, control)
        self._assert_refused("receipt")

    def test_pause_receipt_for_other_dispatch_is_refused(self):
        control = copy.deepcopy(self.control)
        control["pause"]["transaction_receipt"]["subject_identity"] = \
            self._identity_of(TIMEOUT_ID)
        sc.save_control(self.root, control)
        self._assert_refused("receipt")

    def test_quarantine_copy_hash_mismatch_is_refused(self):
        quarantine = self.root / self.quarantine_rel
        quarantine.write_bytes(quarantine.read_bytes() + b"\n")
        self._assert_refused("quarantine")

    def test_waiting_executor_state_is_refused(self):
        self._write_state({**self.state, "status": "WAITING_EXECUTOR",
                           "current_task": self._identity_of(TIMEOUT_ID)})
        self._assert_refused("project binding")

    def test_consumed_pointer_mismatch_is_refused(self):
        self.m.atomic_write(self.m.ZCODE_LAST_PROCESSED, f"{TIMEOUT_ID}\n")
        self._assert_refused("consumed_chain")

    def test_live_inbox_is_refused(self):
        self.m.TO_ZCODE.write_bytes(self.paused_bytes)
        self._assert_refused("live TO_ZCODE")

    def test_control_revision_not_past_origin_is_refused(self):
        control = copy.deepcopy(self.control)
        control["revision"] = 2
        sc.save_control(self.root, control)
        self._assert_refused("origin")

    def test_identity_reproduction_mismatch_is_refused(self):
        # Rebind the archive to dispatch bytes whose LOGICAL_TASK no longer
        # reproduces the retired TASK_ID/STAGE_ID labels: hashes stay
        # consistent, so only the reproduction proof can catch this.
        authorization = copy.deepcopy(self.authorization[PAUSED_ID])
        binding = authorization["SUPERVISOR_DISPATCH_ARCHIVE"]
        task = _task(PAUSED_ID, 3)
        task["LOGICAL_TASK"] = "renamed_research"
        raw = _dispatch_bytes(task)
        digest = hashlib.sha256(raw).hexdigest()
        (self.root / binding["archive_file"]).write_bytes(raw)
        metadata_path = self.root / binding["metadata_file"]
        metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
        metadata["dispatch_sha256"] = digest
        metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
        seal_path = self.root / binding["authorization_file"]
        seal = json.loads(seal_path.read_text(encoding="utf-8-sig"))
        seal["dispatch_sha256"] = digest
        seal_path.write_text(json.dumps(seal), encoding="utf-8")
        authorization["TO_ZCODE_SHA256"] = digest
        authorization["SUPERVISOR_DISPATCH_ARCHIVE"]["dispatch_sha256"] = digest
        runtime = self._read_runtime()
        runtime["authorized_dispatch"] = authorization
        self._write_runtime(runtime)
        self._assert_refused("reproduce")

    def test_predicted_successor_attempt_mismatch_is_refused(self):
        # A same-stage dispatch at ATTEMPT 3 that the exemption does not cover
        # would push the successor to attempt 4: the prediction proof refuses.
        task = dict(_task(PAUSED_ID, 3), MESSAGE_ID=700105,
                    NONCE="nonce-synthetic-105")
        raw = _dispatch_bytes(task)
        binding = sc.archive_dispatch(self.root, PROJECT_ID, task, raw, origin=_ORIGIN)
        authorization = {
            "schema_version": 1, **{key: task[key] for key in sc.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": hashlib.sha256(raw).hexdigest(),
            "AUTHORIZED_AT": sc.now_iso(), "PROJECT_ID": PROJECT_ID,
            "FENCE_VERSION": 1, "EXPIRES_AT": "2020-01-01T00:00:00+00:00",
            "SUPERVISOR_CONTROL_ORIGIN": dict(_ORIGIN),
            "SUPERVISOR_DISPATCH_ARCHIVE": {
                key: binding[key] for key in ("schema_version", "metadata_file",
                                              "archive_file", "authorization_file",
                                              "dispatch_sha256")},
        }
        sc.seal_dispatch_authorization(self.root, authorization)
        self._assert_refused("attempt")

    # --- stale wake fencing -----------------------------------------------------

    def test_stale_wake_of_recovered_identity_is_refused(self):
        lpr.apply_recovery(self.root, PAUSED_ID)
        runtime = self._read_runtime()
        self.assertIn(PAUSED_ID, runtime["retired_message_ids"])
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "executor_claim.py"), "acquire",
             "--root", str(self.root), "--message-id", str(PAUSED_ID),
             "--task-id", TASK_ID, "--stage-id", STAGE_ID, "--attempt", "3",
             "--nonce", NONCE[PAUSED_ID]],
            capture_output=True, text=True, cwd=RUNTIME_ROOT)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("message_id_retired", proc.stderr)


class LegacyPauseRecoveryEndToEndTests(unittest.TestCase):
    """The canonical recovery against the real Supervisor dispatch path."""

    def setUp(self):
        self.f = core.SupervisorControlTests()
        self.f.setUp()
        self.root = self.f.root
        self.o = self.f.configured_orchestrator()
        self.addCleanup(self.f.tearDown)
        self.message_id = None

    # -- fixture: one real dispatch retired by a legacy SAFE pause ----------

    def proposal(self):
        return {"logical_task": "summary", "logical_stage": "check evidence",
                "objective": "Produce a checked summary of the supplied evidence.",
                "inputs": ["evidence/source.txt"], "outputs": ["reports/summary.md"],
                "acceptance_criteria": ["Every finding cites supporting evidence."],
                "forbidden_actions": [], "stop_conditions": [],
                "max_time": 900, "max_retries": 2}

    def begin(self):
        turn = sc.begin_supervisor_turn(self.root, self.f.PROJECT,
                                        ordinary_dispatch_required=True)
        state = self.f.read_state()
        decision = {"decision": "CONTINUE", "reason": "Check the supplied evidence"}
        state["last_supervisor_decision"] = dict(decision)
        state["decision_history"].append(decision)
        state["status"] = "WAITING_EXECUTOR"
        state["ordinary_task_proposal"] = self.proposal()
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

    def legacy_pause_retirement(self, reason="PAUSE_BEFORE_CLAIM"):
        """Dispatch one task, then retire it exactly like the old pause code."""
        turn = self.begin()
        self.assertFalse(self.finish(turn))
        self.authorize(turn)
        state = self.f.read_state()
        identity = sc.normalize_identity(state["current_task"], "fixture task")
        runtime = self.f.read_runtime()
        quarantine = sc._quarantine_current(self.root, identity, "pause")
        sc._retire(runtime, identity, reason)
        runtime["status"] = "PAUSED"
        runtime["last_consumed_nonce"] = "nonce-consumed-700119"
        if isinstance(runtime.get("authorized_dispatch"), dict):
            runtime["authorized_dispatch"]["EXPIRES_AT"] = "2020-01-01T00:00:00+00:00"
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)
        pointer = {"MESSAGE_ID": 700119, "TASK_ID": "T-700119",
                   "STAGE_ID": "S-700119", "ATTEMPT": 1,
                   "NONCE": "nonce-consumed-700119"}
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text(
            "".join(f"{key}={value}\n" for key, value in pointer.items()),
            encoding="utf-8")
        state["status"] = "SUPERVISOR_TURN"
        state["current_task"] = None
        state["next_message_id"] = identity["MESSAGE_ID"] + 1
        self.f._json(self.o.PROJECT_STATE, state)
        control = sc.load_control(self.root)
        control["revision"] = int(control.get("revision", 1)) + 1
        control["pause"] = {
            "status": "PAUSED", "mode": "SAFE", "requested_at": sc.now_iso(),
            "disposition": "PAUSED_UNCLAIMED_RETIRED",
            "transaction_receipt": {
                "schema_version": 1, "action": "RETIRE",
                "subject_identity": identity, "retirement_reason": reason,
                "disposition": "PAUSED_UNCLAIMED_RETIRED",
                "applied_at": sc.now_iso(), "quarantine": quarantine,
            },
        }
        sc.save_control(self.root, control)
        self.message_id = identity["MESSAGE_ID"]
        return identity

    def construct_successor(self):
        return od._construct(self.root, {
            "PROJECT_ID": self.f.PROJECT,
            "dispatch_projection_before": {"current_task": None,
                                           "next_message_id": self.message_id + 1},
        }, self.proposal())

    # -- scenarios ----------------------------------------------------------

    def test_apply_converts_preserves_history_and_keeps_attempt(self):
        identity = self.legacy_pause_retirement()
        runtime_before = self.f.read_runtime()
        history = json.dumps(runtime_before["executor_retirements"], sort_keys=True)
        retired = json.dumps(runtime_before["retired_message_ids"], sort_keys=True)
        state_path = self.o.PROJECT_STATE
        state_bytes = state_path.read_bytes()

        result = lpr.apply_recovery(self.root, self.message_id)
        self.assertEqual(result["status"], "RECOVERED")

        runtime = self.f.read_runtime()
        self.assertEqual(runtime[lpr.PARKED_KEY], [self.message_id])
        pending = runtime["pending_supervisor_event"]
        self.assertEqual(pending["reason"], lpr.EVENT_TYPE)
        self.assertFalse(pending["retry_exhausted"])
        self.assertEqual(pending["decision_attempts"], 0)
        event = pending["event"]
        self.assertEqual(event["message_id"] if "message_id" in event
                         else event["MESSAGE_ID"], self.message_id)
        self.assertIn("not a method failure", event["note"])
        self.assertIn("not accepted evidence until re-submitted", event["note"])
        record_bytes = (self.root / event["recovery_record"]).read_bytes()
        record = json.loads(record_bytes.decode("utf-8"))
        self.assertEqual(record["recovery"], "LEGACY-PAUSE-RECOVERY-V2")
        self.assertTrue(record["proof"]["ok"])
        self.assertTrue(record["supervisor_context"]["not_executed"])
        self.assertEqual(hashlib.sha256(record_bytes).hexdigest(),
                         event["recovery_record_sha256"])

        # Retirement history and project state are untouched.
        runtime_after = self.f.read_runtime()
        self.assertEqual(json.dumps(runtime_after["executor_retirements"], sort_keys=True),
                         history)
        self.assertEqual(json.dumps(runtime_after["retired_message_ids"], sort_keys=True),
                         retired)
        self.assertEqual(state_path.read_bytes(), state_bytes)

        # The successor dispatch of the same logical stage keeps its attempt.
        task = self.construct_successor()
        self.assertEqual(task["MESSAGE_ID"], self.message_id + 1)
        self.assertEqual(task["ATTEMPT"], identity["ATTEMPT"])
        self.assertEqual(task["TASK_ID"], identity["TASK_ID"])
        self.assertEqual(task["STAGE_ID"], identity["STAGE_ID"])
        self.assertNotEqual(task["NONCE"], identity["NONCE"])

        again = lpr.apply_recovery(self.root, self.message_id)
        self.assertEqual(again["status"], "ALREADY_RECOVERED")

    def test_lift_pause_and_register_production_successor(self):
        identity = self.legacy_pause_retirement()
        result = lpr.apply_recovery(self.root, self.message_id)
        self.assertEqual(result["status"], "RECOVERED")
        old = sc.normalize_identity(identity, "old")
        allowed, reason = executor_claim.verify_authorized_dispatch(
            self.root, *[old[key] for key in sc.IDENTITY_KEYS])
        self.assertFalse(allowed)
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = executor_claim.acquire(self.root,
                                          *[old[key] for key in sc.IDENTITY_KEYS])
        self.assertNotEqual(code, executor_claim.EXIT_ACQUIRED)

        # Lift the pause and let the successor dispatch register; the old
        # identity stays dead and the successor is a fresh identity.
        control = sc.load_control(self.root)
        control["pause"]["status"] = "RUNNING"
        sc.save_control(self.root, control)
        runtime = self.f.read_runtime()
        runtime["status"] = "RUNNING"
        self.f._json(self.f.control / "orchestrator_runtime.json", runtime)
        turn = self.begin()
        self.assertFalse(self.finish(turn))
        self.authorize(turn)
        state = self.f.read_state()
        self.assertEqual(state["status"], "WAITING_EXECUTOR")
        successor = sc.normalize_identity(state["current_task"], "successor")
        self.assertEqual(successor["MESSAGE_ID"], self.message_id + 1)
        self.assertEqual(successor["ATTEMPT"], old["ATTEMPT"])
        self.assertEqual(successor["TASK_ID"], old["TASK_ID"])
        self.assertNotEqual(successor["NONCE"], old["NONCE"])
        self.assertTrue(self.o.TO_ZCODE.exists())
        allowed, _ = executor_claim.verify_authorized_dispatch(
            self.root, *[old[key] for key in sc.IDENTITY_KEYS])
        self.assertFalse(allowed)

    def test_save_runtime_merge_preserves_offline_parked_record(self):
        # The recovery commits while no scheduler is live. A stale scheduler
        # snapshot carrying an older parked list must not erase the durable
        # budget fact on its next fenced save (same protection as retirement
        # history).
        self.legacy_pause_retirement()
        result = lpr.apply_recovery(self.root, self.message_id)
        self.assertEqual(result["status"], "RECOVERED")
        stale = self.f.read_runtime()
        stale[lpr.PARKED_KEY] = []
        self.o.save_runtime(stale)
        runtime = self.f.read_runtime()
        self.assertEqual(runtime[lpr.PARKED_KEY], [self.message_id])

    def test_real_executor_timeout_refusal_is_not_recoverable(self):
        self.legacy_pause_retirement(reason="EXECUTOR_TIMEOUT")
        failures = lpr.build_proof(self.root, self.message_id)["checks"]
        failed = {item["check"] for item in failures if not item["passed"]}
        self.assertIn("legacy_retirement_record", failed)
        # No parking: the ordinary timeout still advances the attempt.
        task = self.construct_successor()
        self.assertEqual(task["ATTEMPT"], 2)
        self.assertEqual(task["MESSAGE_ID"], self.message_id + 1)


if __name__ == "__main__":
    unittest.main()
