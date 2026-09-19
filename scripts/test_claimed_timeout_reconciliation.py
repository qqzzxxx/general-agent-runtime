"""CLAIMED-TIMEOUT-RECONCILIATION-V1 focused tests.

Behavioral classes from the v1.4 claimed-timeout -> HUMAN_REVIEW deadlock fix:

A. the live 603 shape: claimed -> timeout-retired -> zero committed completion
   -> HUMAN_REVIEW; Apply refused before reconciliation, the same prepared
   receipt applies after Runtime reconciliation;
B. genuinely active claimed work stays blocking;
C. ambiguous claimed state stays fail-closed (no reconciliation);
D. a committed completion is never reinterpreted as the zero-completion case;
E. idempotence and restart: no duplicated records, no double consumption, no
   resurrection, stale workers stay fenced.

Every test exercises the real Runtime helpers and persisted state shapes via
scripts/resume_human_review.py against an isolated temp root.
"""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
RESUME_PATH = Path(__file__).resolve().with_name("resume_human_review.py")
spec = importlib.util.spec_from_file_location("resume_human_reconcile_under_test", RESUME_PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class ClaimedTimeoutReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="claimed-timeout-reconcile-")
        self.root = Path(self.temp.name)
        self.m = r.load_runtime_module(RUNTIME_ROOT)
        r.bind_runtime_paths(self.m, self.root)
        self.m.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        for path in (
            self.m.CONTROL,
            self.m.LOGS,
            self.m.HANDOFF_ARCHIVE,
            self.m.REPORTS,
            self.root / "handoff" / "executor_claims",
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.project_id = "reconcile-test"
        self.project_root = self.root / "projects" / self.project_id
        self.project_root.mkdir(parents=True)
        self.m.atomic_json(
            self.m.ACTIVE_PROJECT_FILE,
            {
                "schema_version": 1,
                "project_id": self.project_id,
                "project_root": f"projects/{self.project_id}",
            },
        )
        (self.project_root / "PROJECT_GOAL.md").write_text("# Fixture goal\n", encoding="utf-8")
        (self.project_root / "RESEARCH_STATE.md").write_text("# Fixture memory\n", encoding="utf-8")
        self.m.atomic_write(self.m.SUPERVISOR_RULES, "# Fixture supervisor rules\n")
        goal_sha256 = hashlib.sha256(
            (self.project_root / "PROJECT_GOAL.md").read_bytes()).hexdigest()
        self.base_state = {
            "schema_version": 4,
            "project_id": self.project_id,
            "project_type": "GENERAL",
            "profile": "GENERAL",
            "status": "HUMAN_REVIEW",
            "phase": "GENERAL",
            "updated_at": self.m.stamp(),
            "goal_file": "PROJECT_GOAL.md",
            "goal_anchor": {
                "schema_version": 1,
                "goal_path": "PROJECT_GOAL.md",
                "goal_sha256": goal_sha256,
                "bound_at": self.m.stamp(),
                "provenance": "bootstrap",
            },
            "current_task": None,
            "next_message_id": 700101,
            "final_verification": {
                "policy_version": 1,
                "required": True,
                "status": "NOT_STARTED",
                "policy_id": "GENERAL_FV_V1",
                "critical_claims": [],
                "claims_hash": None,
                "verification_message_id": None,
                "verification_receipt_sha256": None,
                "verified_at": None,
            },
            "last_supervisor_decision": "HUMAN_REVIEW — fixture",
            "decision_history": [],
            "infrastructure_status": "READY",
            "deadline_at": None,
            "blocked_reason": "fixture claimed timeout needs reconciliation",
            "notes": [],
        }
        self._write_state(self.base_state)

        self.consumed_id = 700099
        self.dispatched_id = 700100
        self.dispatched_nonce = "fixture-claimed-timeout-nonce"
        self.dispatched_task_id = "fixture-claimed-fv"
        self.dispatched_stage_id = "fixture-claimed-fv-stage"
        self.payload = {
            "decision_content": "Reconcile the timed-out attempt and continue.",
            "constraints_verbatim": ["Preserve all historical evidence verbatim."],
        }
        self.receipt_path = self.root / "prepared-human-decision.json"

    def tearDown(self):
        self.temp.cleanup()

    # --- fixture shaping -------------------------------------------------

    def _write_state(self, value):
        self.m.atomic_json(self.project_root / "project_state.json", value)

    def _read_state(self):
        return json.loads((self.project_root / "project_state.json").read_text(encoding="utf-8"))

    def _read_runtime(self):
        return json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))

    def _write_runtime(self, value):
        self.m.atomic_json(self.m.RUNTIME_STATE, value)

    def _claim_dir_for(self, message_id, nonce):
        digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]
        return self.root / "handoff" / "executor_claims" / f"{message_id}-{digest}.claim"

    def _identity(self, message_id=None, nonce=None):
        return {
            "MESSAGE_ID": self.dispatched_id if message_id is None else message_id,
            "TASK_ID": self.dispatched_task_id,
            "STAGE_ID": self.dispatched_stage_id,
            "ATTEMPT": 1,
            "NONCE": self.dispatched_nonce if nonce is None else nonce,
        }

    def _reshape_claimed_timeout(
        self, *, reason="EXECUTOR_TIMEOUT", retired=True, timeout_nonce=True,
        expire=True, live_inbox=True, claim=True, workspace=True,
    ):
        """Reshape the fixture into the live-603 scene: the last dispatch was
        claimed, its claimed execution budget expired, the Runtime retired it
        EXECUTOR_TIMEOUT with zero committed completion, and the Supervisor
        committed HUMAN_REVIEW. The consumed chain is the legacy migrated
        shape (no nonce/brief binding, no processed pointer)."""
        runtime = {
            "schema_version": 2,
            "status": "HUMAN_REVIEW",
            "last_consumed_message_id": self.consumed_id,
            "last_consumed_nonce": None,
            "last_consumed_brief_sha256": None,
            "last_dispatched_message_id": self.dispatched_id,
            "last_dispatched_nonce": self.dispatched_nonce,
            "claim_protocol_required_from_message_id": None,
            "consecutive_codex_without_executor": 0,
            "retired_message_ids": [self.dispatched_id] if retired else [],
            "executor_retirements": [{
                **self._identity(),
                "RETIRED_AT": self.m.stamp(),
                "REASON": reason,
                "SUPERSEDED_BY": None,
            }] if retired else [],
            "timeout_notified_for_nonce": self.dispatched_nonce if timeout_nonce else None,
            "pending_executor_timeout": {
                "type": "EXECUTOR_TIMEOUT",
                "task_id": self.dispatched_task_id,
                "stage_id": self.dispatched_stage_id,
                "message_id": self.dispatched_id,
                "attempt": 1,
                "nonce": self.dispatched_nonce,
            },
        }
        inbox = (
            f"MESSAGE_ID: {self.dispatched_id}\n"
            f"TASK_ID: {self.dispatched_task_id}\n"
            f"STAGE_ID: {self.dispatched_stage_id}\n\n"
            "```json\n" + json.dumps(self._identity(), indent=2) + "\n```\n"
        )
        self.m.atomic_write(self.m.TO_ZCODE, inbox)
        inbox_hash = self.m.sha256(self.m.TO_ZCODE)
        if not live_inbox:
            self.m.TO_ZCODE.unlink()
        digest = hashlib.sha256(self.dispatched_nonce.encode("utf-8")).hexdigest()[:24]
        archive_meta = {
            "metadata_file": (
                f"handoff/supervisor_dispatch_archive/{self.project_id}"
                f"/dispatch-{self.dispatched_id}-{digest}.json"),
            "archive_file": (
                f"handoff/supervisor_dispatch_archive/{self.project_id}"
                f"/dispatch-{self.dispatched_id}-{digest}.md"),
            "authorization_file": (
                f"handoff/supervisor_dispatch_archive/{self.project_id}"
                f"/dispatch-{self.dispatched_id}-{digest}.authorized.json"),
            "dispatch_sha256": inbox_hash,
        }
        for key, document in (
            ("metadata_file", {
                "schema_version": 1, "PROJECT_ID": self.project_id,
                "MESSAGE_ID": self.dispatched_id, "TASK_ID": self.dispatched_task_id,
                "STAGE_ID": self.dispatched_stage_id, "ATTEMPT": 1,
                "NONCE": self.dispatched_nonce, "dispatch_sha256": inbox_hash}),
            ("authorization_file", {
                "schema_version": 1, "PROJECT_ID": self.project_id,
                "MESSAGE_ID": self.dispatched_id, "TASK_ID": self.dispatched_task_id,
                "STAGE_ID": self.dispatched_stage_id, "ATTEMPT": 1,
                "NONCE": self.dispatched_nonce, "dispatch_sha256": inbox_hash}),
        ):
            path = self.root / archive_meta[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        (self.root / archive_meta["archive_file"]).write_text(
            inbox, encoding="utf-8", newline="\n")
        runtime["authorized_dispatch"] = {
            "schema_version": 1,
            **self._identity(),
            "TO_ZCODE_SHA256": inbox_hash,
            "AUTHORIZED_AT": self.m.stamp(),
            "EXPIRES_AT": ("2020-01-01T00:00:00+00:00" if expire
                           else "2999-01-01T00:00:00+00:00"),
            "SUPERVISOR_DISPATCH_ARCHIVE": archive_meta,
        }
        if claim:
            claim_dir = self._claim_dir_for(self.dispatched_id, self.dispatched_nonce)
            claim_dir.mkdir(parents=True, exist_ok=True)
            self.m.atomic_json(claim_dir / "claim.json", {
                "CLAIM_PROTOCOL_VERSION": 1,
                **self._identity(),
                "CLAIMED_AT": self.m.stamp(),
                "HOSTNAME": "fixture",
                "SEMANTICS": "permanent at-most-once claim; do not delete",
                "CLAIM_TOKEN_SHA256": hashlib.sha256(b"fixture-token").hexdigest(),
            })
        if workspace:
            ws = (self.project_root / "attempt_workspaces"
                  / f"{self.dispatched_id}-{digest}")
            ws.mkdir(parents=True, exist_ok=True)
            (ws / "executor-result.json").write_text("{}\n", encoding="utf-8")
        self._write_runtime(runtime)
        return runtime

    def _reconcile(self):
        return r.reconcile_claimed_timeout(self.m, project_id=self.project_id)

    def _prepare(self):
        return r.prepare_receipt(
            self.m,
            project_id=self.project_id,
            decision_payload=self.payload,
            receipt_out=self.receipt_path,
        )

    def _apply(self):
        return r.apply_receipt(self.m, receipt_path=self.receipt_path)

    def _write_ledger_entry(self, message_id, status):
        completion = r._load_completion_helper()
        ledger = self.root / "handoff" / "completion_ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        entry = {
            "COMPLETION_PROTOCOL_VERSION": completion.COMPLETION_PROTOCOL_VERSION,
            "STATUS": status,
            **self._identity(message_id),
        }
        path = ledger / f"completion-{message_id}-fixture.json"
        path.write_text(json.dumps(entry), encoding="utf-8")
        return path

    # --- A. the live 603 shape ---------------------------------------------

    def test_apply_refused_before_reconciliation(self):
        self._reshape_claimed_timeout()
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()

    def test_reconcile_then_apply_resumes_with_same_receipt(self):
        self._reshape_claimed_timeout()
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()
        result = self._reconcile()
        self.assertEqual(result["event"], "CLAIMED_TIMEOUT_RECONCILED")
        self.assertFalse(result["duplicate"])
        applied = self._apply()
        self.assertEqual(applied["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        state = self._read_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(state["human_review_resume"]["status"], "PENDING_SUPERVISOR_REVIEW")
        self.assertIsNone(state["current_task"])
        runtime = self._read_runtime()
        self.assertEqual(runtime["status"], "SUPERVISOR_TURN")
        self.assertEqual(runtime["pending_supervisor_event"]["reason"],
                         "HUMAN_DECISION_RESUME")
        self.assertEqual(runtime["last_human_decision_receipt_id"],
                         applied["receipt_id"])
        records = runtime["claimed_timeout_reconciliations"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["MESSAGE_ID"], self.dispatched_id)
        self.assertEqual(records[0]["REASON"], "EXECUTOR_TIMEOUT")
        self.assertEqual(records[0]["COMMITTED_COMPLETIONS"], 0)

    def test_reconciliation_preserves_history_and_receipt_binding(self):
        self._reshape_claimed_timeout()
        state_before = (self.project_root / "project_state.json").read_bytes()
        inbox_before = self.m.TO_ZCODE.read_bytes()
        claim_before = (self._claim_dir_for(
            self.dispatched_id, self.dispatched_nonce) / "claim.json").read_bytes()
        self._prepare()
        receipt_before = self.receipt_path.read_bytes()
        self._reconcile()
        self.assertEqual(
            (self.project_root / "project_state.json").read_bytes(), state_before)
        self.assertEqual(self.m.TO_ZCODE.read_bytes(), inbox_before)
        self.assertEqual(
            (self._claim_dir_for(self.dispatched_id, self.dispatched_nonce)
             / "claim.json").read_bytes(), claim_before)
        self.assertTrue(self._claim_dir_for(
            self.dispatched_id, self.dispatched_nonce).is_dir())
        self.assertTrue(any((self.project_root / "attempt_workspaces").iterdir()))
        self.assertEqual(self.receipt_path.read_bytes(), receipt_before)
        # The prepared receipt stays valid: prepare -> reconcile -> apply.
        self._apply()
        self.assertEqual(self._read_state()["status"], "SUPERVISOR_TURN")

    def test_reconciliation_writes_runtime_owned_audit_event(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        audit = (self.root / "handoff" / "completion_ledger" / "audit.jsonl")
        self.assertTrue(audit.is_file())
        events = [json.loads(line) for line in
                  audit.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertTrue(any(e.get("event") == "CLAIMED_TIMEOUT_RECONCILED"
                            and e.get("MESSAGE_ID") == self.dispatched_id
                            for e in events))

    def test_reconcile_refused_outside_human_review(self):
        self._reshape_claimed_timeout()
        state = self._read_state()
        state["status"] = "WAITING_EXECUTOR"
        self._write_state(state)
        with self.assertRaisesRegex(r.ResumeError, "HUMAN_REVIEW"):
            self._reconcile()

    # --- B. active claimed work stays blocking -----------------------------

    def test_unretired_claim_stays_blocking(self):
        self._reshape_claimed_timeout(retired=False)
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()

    def test_extra_active_claim_beyond_dispatched_stays_blocking(self):
        self._reshape_claimed_timeout()
        ghost = self._claim_dir_for(700101, "fixture-ghost-active-nonce")
        ghost.mkdir(parents=True)
        self.m.atomic_json(ghost / "claim.json", {
            "MESSAGE_ID": 700101, "NONCE": "fixture-ghost-active-nonce"})
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()

    # --- C. ambiguous claimed state stays fail-closed -----------------------

    def test_non_authority_retirement_reason_refuses(self):
        self._reshape_claimed_timeout(reason="SUPERSEDED")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_missing_timeout_nonce_binding_refuses(self):
        self._reshape_claimed_timeout(timeout_nonce=False)
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_open_authorization_window_refuses(self):
        self._reshape_claimed_timeout(expire=False)
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_tampered_dispatch_archive_refuses(self):
        self._reshape_claimed_timeout()
        runtime = self._read_runtime()
        archive_file = self.root / runtime["authorized_dispatch"][
            "SUPERVISOR_DISPATCH_ARCHIVE"]["archive_file"]
        archive_file.write_text("tampered dispatch bytes\n", encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_claim_identity_mismatch_refuses(self):
        self._reshape_claimed_timeout()
        claim_path = (self._claim_dir_for(
            self.dispatched_id, self.dispatched_nonce) / "claim.json")
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["NONCE"] = "fixture-different-nonce"
        claim_path.write_text(json.dumps(claim), encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_malformed_claim_directory_refuses(self):
        self._reshape_claimed_timeout()
        malformed = self.root / "handoff" / "executor_claims" / "not-a-claim-dir"
        malformed.mkdir()
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_done_flag_refuses(self):
        self._reshape_claimed_timeout()
        self.m.atomic_write(self.m.ZCODE_DONE, "MESSAGE_ID: 700100\n")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_tampered_live_inbox_refuses(self):
        self._reshape_claimed_timeout()
        self.m.atomic_write(self.m.TO_ZCODE, "rewritten inbox bytes\n")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_staged_completion_for_dispatched_refuses(self):
        self._reshape_claimed_timeout()
        staged = (self.root / "handoff" / "completion_ledger" / "staged"
                  / f"completion-{self.dispatched_id}-fixture")
        staged.mkdir(parents=True)
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_unconsumed_ledger_entry_anywhere_refuses(self):
        self._reshape_claimed_timeout()
        self._write_ledger_entry(self.consumed_id - 50, "COMPLETION_COMMITTED")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_apply_refused_after_claim_tampering(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        claim_path = (self._claim_dir_for(
            self.dispatched_id, self.dispatched_nonce) / "claim.json")
        claim = json.loads(claim_path.read_text(encoding="utf-8"))
        claim["CLAIM_TOKEN_SHA256"] = hashlib.sha256(b"rewritten").hexdigest()
        claim_path.write_text(json.dumps(claim), encoding="utf-8")
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()

    def test_apply_refused_after_workspace_disappearance(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        digest = hashlib.sha256(self.dispatched_nonce.encode("utf-8")).hexdigest()[:24]
        ws = self.project_root / "attempt_workspaces" / f"{self.dispatched_id}-{digest}"
        for child in ws.iterdir():
            child.unlink()
        ws.rmdir()
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()

    # --- D. a committed completion is never the zero-completion case --------

    def test_committed_completion_for_dispatched_refuses(self):
        self._reshape_claimed_timeout()
        self._write_ledger_entry(self.dispatched_id, "COMPLETION_COMMITTED")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._apply()

    def test_sealed_completion_for_dispatched_refuses(self):
        self._reshape_claimed_timeout()
        self._write_ledger_entry(self.dispatched_id, "COMPLETION_SEALED")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    # --- E. idempotence, restart, exactly-once, stale-worker fencing --------

    def test_reconcile_is_idempotent(self):
        self._reshape_claimed_timeout()
        first = self._reconcile()
        self.assertFalse(first["duplicate"])
        second = self._reconcile()
        self.assertEqual(second["event"], "CLAIMED_TIMEOUT_ALREADY_RECONCILED")
        self.assertTrue(second["duplicate"])
        records = self._read_runtime()["claimed_timeout_reconciliations"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["RECONCILIATION_ID"],
                         first["reconciliation_id"])

    def test_reconcile_refused_after_apply(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        self._prepare()
        self._apply()
        with self.assertRaisesRegex(r.ResumeError, "HUMAN_REVIEW"):
            self._reconcile()

    def test_apply_is_exactly_once(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        self._prepare()
        self._apply()
        with self.assertRaisesRegex(r.ResumeError, "duplicate resume"):
            self._apply()
        state = self._read_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")

    def test_reconciled_state_survives_restart(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        # Simulate a Runtime restart: rebuild the module and re-read the
        # persisted runtime state from disk only.
        fresh_spec = importlib.util.spec_from_file_location(
            "resume_human_reconcile_restart", RESUME_PATH)
        fresh = importlib.util.module_from_spec(fresh_spec)
        fresh_spec.loader.exec_module(fresh)
        m2 = fresh.load_runtime_module(RUNTIME_ROOT)
        fresh.bind_runtime_paths(m2, self.root)
        m2.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        m2.activate_project_scope()
        runtime = json.loads(m2.RUNTIME_STATE.read_text(encoding="utf-8-sig"))
        self.assertTrue(fresh._assert_claimed_timeout_reconciled(
            m2, runtime, require_record=True))
        self._prepare()
        applied = self._apply()
        self.assertEqual(applied["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")

    def test_stale_worker_fenced_after_reconciliation(self):
        self._reshape_claimed_timeout()
        self._reconcile()
        runtime = self._read_runtime()
        identity = self._identity()
        completion = r._load_completion_helper()
        with self.assertRaisesRegex(completion.CompletionError, "retired"):
            completion._check_authorization(runtime, identity)
        with self.assertRaises(completion.CompletionError):
            completion._check_live_lifecycle(self.root, self.project_id, identity)
        claim_helper = r._load_claim_helper()
        allowed, reason = claim_helper.verify_authorized_dispatch(
            self.root, identity["MESSAGE_ID"], identity["TASK_ID"],
            identity["STAGE_ID"], identity["ATTEMPT"], identity["NONCE"])
        self.assertFalse(allowed)
        self.assertIn("message_id_retired", reason)

    def test_legacy_processed_pointer_mismatch_refuses(self):
        self._reshape_claimed_timeout()
        self.m.atomic_write(self.m.ZCODE_LAST_PROCESSED, f"{self.consumed_id - 1}\n")
        with self.assertRaisesRegex(r.ResumeError, "not.*reconcilable"):
            self._reconcile()

    def test_legacy_processed_pointer_match_resumes(self):
        self._reshape_claimed_timeout()
        self.m.atomic_write(self.m.ZCODE_LAST_PROCESSED, f"{self.consumed_id}\n")
        self._reconcile()
        self._prepare()
        self._apply()
        self.assertEqual(self._read_state()["status"], "SUPERVISOR_TURN")

    def test_full_identity_chain_resumes(self):
        """Non-legacy chain: consumed nonce/brief bound, claim protocol
        required, v2 processed pointer — every optional check exercises."""
        runtime = self._reshape_claimed_timeout()
        nonce = "fixture-consumed-chain-nonce"
        brief_content = "fixture consumed chain receipt\n"
        brief_hash = hashlib.sha256(brief_content.encode("utf-8")).hexdigest()
        (self.m.HANDOFF_ARCHIVE
         / f"brief-{self.consumed_id}-{nonce[:12]}-consumed-{brief_hash[:12]}.md") \
            .write_text(brief_content, encoding="utf-8", newline="\n")
        consumed_identity = {
            "MESSAGE_ID": self.consumed_id,
            "TASK_ID": "fixture-consumed-task",
            "STAGE_ID": "fixture-consumed-stage",
            "ATTEMPT": 1,
            "NONCE": nonce,
        }
        consumed_claim = self._claim_dir_for(self.consumed_id, nonce)
        consumed_claim.mkdir(parents=True, exist_ok=True)
        self.m.atomic_json(consumed_claim / "claim.json", consumed_identity)
        self.m.atomic_write(
            self.m.ZCODE_LAST_PROCESSED,
            "".join(f"{key}={value}\n" for key, value in consumed_identity.items()))
        runtime.update({
            "last_consumed_nonce": nonce,
            "last_consumed_brief_sha256": brief_hash,
            "claim_protocol_required_from_message_id": 700001,
        })
        self._write_runtime(runtime)
        self._reconcile()
        self._prepare()
        self._apply()
        self.assertEqual(self._read_state()["status"], "SUPERVISOR_TURN")

    def test_cli_subcommand_dispatch_round_trip(self):
        """The CLI layer routes reconcile-claimed-timeout through the same
        in-process transaction: exit 0 + JSON event on success, exit 3 +
        fail-closed stderr outside HUMAN_REVIEW, exit 0 duplicate on re-run."""
        def event_json(stdout_text):
            # main() prints the event JSON last; orchestrator log lines
            # (prefixed "[timestamp] ...") may precede it on stdout.
            lines = [line for line in stdout_text.splitlines()
                     if not line.startswith("[")]
            return json.loads("\n".join(lines))

        self._reshape_claimed_timeout()
        argv = ["resume_human_review.py", "--root", str(self.root),
                "reconcile-claimed-timeout", "--project-id", self.project_id]
        with patch.object(r, "load_runtime_module", return_value=self.m), \
                patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            code = r.main()
        self.assertEqual(code, r.EXIT_OK)
        self.assertEqual(event_json(out.getvalue())["event"],
                         "CLAIMED_TIMEOUT_RECONCILED")
        self.assertEqual(len(
            self._read_runtime()["claimed_timeout_reconciliations"]), 1)
        state = self._read_state()
        state["status"] = "SUPERVISOR_TURN"
        self._write_state(state)
        with patch.object(r, "load_runtime_module", return_value=self.m), \
                patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(io.StringIO()) as out, \
                contextlib.redirect_stderr(io.StringIO()) as err:
            code = r.main()
        self.assertEqual(code, r.EXIT_CONFLICT)
        self.assertIn("HUMAN_REVIEW_RESUME_FAILED", err.getvalue())


if __name__ == "__main__":
    unittest.main()
