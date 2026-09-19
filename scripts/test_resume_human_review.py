import copy
import hashlib
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
RESUME_PATH = Path(__file__).resolve().with_name("resume_human_review.py")
spec = importlib.util.spec_from_file_location("resume_human_review_under_test", RESUME_PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class HumanReviewResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="human-review-resume-")
        self.root = Path(self.temp.name)
        self.m = r.load_runtime_module(RUNTIME_ROOT)
        r.bind_runtime_paths(self.m, self.root)
        # Profiles are immutable Runtime code/data; tests keep project/control state isolated.
        self.m.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        for path in (
            self.m.CONTROL,
            self.m.LOGS,
            self.m.HANDOFF_ARCHIVE,
            self.m.REPORTS,
            self.root / "handoff" / "executor_claims",
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.project_id = "resume-test"
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
        # GOAL-ANCHOR-V1: the fixture models a post-GOAL-ANCHOR isolated project, so
        # project_state carries the binding for the exact canonical goal bytes.
        self.goal_sha256 = hashlib.sha256(
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
                "goal_sha256": self.goal_sha256,
                "bound_at": self.m.stamp(),
                "provenance": "bootstrap",
            },
            "current_task": None,
            "next_message_id": 700100,
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
            "blocked_reason": "fixture human decision required",
            "notes": [],
        }
        self._write_state(self.base_state)

        self.last_message_id = 700099
        self.last_nonce = "fixture-consumed-nonce"
        brief_content = "fixture consumed Executor receipt\n"
        brief_hash = hashlib.sha256(brief_content.encode("utf-8")).hexdigest()
        brief_path = (
            self.m.HANDOFF_ARCHIVE
            / f"brief-{self.last_message_id}-{self.last_nonce[:12]}-consumed-{brief_hash[:12]}.md"
        )
        brief_path.write_text(brief_content, encoding="utf-8", newline="\n")
        last_task = {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": self.last_message_id,
            "TASK_ID": "fixture-last-task",
            "STAGE_ID": "fixture-last-stage",
            "ATTEMPT": 1,
            "NONCE": self.last_nonce,
        }
        self.original_inbox = (
            f"MESSAGE_ID: {self.last_message_id}\n"
            f"TASK_ID: {last_task['TASK_ID']}\n"
            f"STAGE_ID: {last_task['STAGE_ID']}\n\n"
            "```json\n"
            + json.dumps(last_task, ensure_ascii=False, indent=2)
            + "\n```\n"
        )
        self.m.atomic_write(self.m.TO_ZCODE, self.original_inbox)
        inbox_hash = self.m.sha256(self.m.TO_ZCODE)
        self.runtime = {
            "schema_version": 2,
            "status": "HUMAN_REVIEW",
            "last_consumed_message_id": self.last_message_id,
            "last_consumed_nonce": self.last_nonce,
            "last_consumed_brief_sha256": brief_hash,
            "last_dispatched_message_id": self.last_message_id,
            "last_dispatched_nonce": self.last_nonce,
            "authorized_dispatch": {
                "schema_version": 1,
                "MESSAGE_ID": self.last_message_id,
                "TASK_ID": "fixture-last-task",
                "STAGE_ID": "fixture-last-stage",
                "ATTEMPT": 1,
                "NONCE": self.last_nonce,
                "TO_ZCODE_SHA256": inbox_hash,
                "AUTHORIZED_AT": self.m.stamp(),
            },
            "retired_message_ids": [],
            "claim_protocol_required_from_message_id": 700001,
            "consecutive_codex_without_executor": 0,
        }
        self.m.atomic_json(self.m.RUNTIME_STATE, self.runtime)
        # The default fixture uses the current v2 key=value wire format, so the
        # whole suite exercises resume against the formal pointer format.
        self.last_task_identity = {
            "MESSAGE_ID": self.last_message_id,
            "TASK_ID": "fixture-last-task",
            "STAGE_ID": "fixture-last-stage",
            "ATTEMPT": 1,
            "NONCE": self.last_nonce,
        }
        self.m.atomic_write(
            self.m.ZCODE_LAST_PROCESSED,
            "".join(f"{key}={value}\n" for key, value in self.last_task_identity.items()),
        )
        claim_dir = (
            self.root
            / "handoff"
            / "executor_claims"
            / f"{self.last_message_id}-fixture.claim"
        )
        claim_dir.mkdir()
        self.m.atomic_json(
            claim_dir / "claim.json",
            {"MESSAGE_ID": self.last_message_id, "NONCE": self.last_nonce},
        )

        self.payload = {
            "decision_content": (
                "Authorize assumption-safe hardware-independent work; Python and MATLAB may be "
                "used by the Executor; Codex Supervisor chooses any later stage."
            ),
            "constraints_verbatim": [
                "NO REAL-DMD EXPERIMENT HAS OCCURRED.",
                "Do not fabricate hardware, experiments, measurements, calibration, or data.",
            ],
        }
        self.receipt_path = self.root / "prepared-human-decision.json"

    def tearDown(self):
        self.temp.cleanup()

    def _write_state(self, value):
        self.m.atomic_json(self.project_root / "project_state.json", value)

    def _read_state(self):
        return json.loads((self.project_root / "project_state.json").read_text(encoding="utf-8"))

    def _write_runtime(self, value):
        self.m.atomic_json(self.m.RUNTIME_STATE, value)

    def _prepare(self):
        return r.prepare_receipt(
            self.m,
            project_id=self.project_id,
            decision_payload=self.payload,
            receipt_out=self.receipt_path,
        )

    def _apply(self):
        return r.apply_receipt(self.m, receipt_path=self.receipt_path)

    def _pending_state(self):
        self._prepare()
        self._apply()
        self.m.activate_project_scope()
        return self.m.read_project_state()

    # --- pre-first-dispatch HUMAN_REVIEW resume ------------------------------

    def _strip_to_pre_dispatch(self, *, consumed=602, write_pointer=True):
        """Reshape the post-task fixture into the fresh-bootstrap HUMAN_REVIEW
        scene: no Executor task was ever published; only the bootstrap
        compatibility consumed pointer may exist."""
        runtime = copy.deepcopy(self.runtime)
        runtime["last_consumed_message_id"] = consumed
        runtime["last_consumed_nonce"] = None
        runtime["last_consumed_brief_sha256"] = None
        runtime["last_dispatched_message_id"] = None
        runtime["last_dispatched_nonce"] = None
        runtime["authorized_dispatch"] = None
        self.m.TO_ZCODE.unlink(missing_ok=True)
        claim_dir = (
            self.root / "handoff" / "executor_claims"
            / f"{self.last_message_id}-fixture.claim"
        )
        if claim_dir.exists():
            for child in claim_dir.iterdir():
                child.unlink()
            claim_dir.rmdir()
        self.m.ZCODE_LAST_PROCESSED.unlink(missing_ok=True)
        if write_pointer:
            self.m.atomic_write(self.m.ZCODE_LAST_PROCESSED, f"{consumed}\n")
        self._write_runtime(runtime)
        return runtime

    def test_sealed_post_task_review_resumes_with_removed_inbox(self):
        # F-005: exhausting the Supervisor decision budget finalizes
        # HUMAN_REVIEW and removes the stale inbox of the fully consumed and
        # sealed dispatch. Apply must still work there; only a present-but-
        # mismatched inbox is a conflict.
        self.m.TO_ZCODE.unlink()
        receipt = self._prepare()
        self._apply()
        self.m.activate_project_scope()
        state = self.m.read_project_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")

    def test_present_but_mismatched_inbox_still_rejected(self):
        self.m.TO_ZCODE.write_text(
            self.original_inbox + "tampered\n", encoding="utf-8")
        self._prepare()
        with self.assertRaisesRegex(r.ResumeError, "hash-mismatched"):
            self._apply()

    def test_pre_dispatch_human_review_prepares_and_applies(self):
        self._strip_to_pre_dispatch(consumed=602)
        receipt = self._prepare()
        self.assertEqual(receipt["project_id"], self.project_id)
        result = self._apply()
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        state = self._read_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(state["human_review_resume"]["status"], "PENDING_SUPERVISOR_REVIEW")
        self.assertIsNone(state["current_task"])
        self.assertEqual(state["next_message_id"], 700100)

    def test_pre_dispatch_resume_without_compat_pointer(self):
        self._strip_to_pre_dispatch(consumed=602, write_pointer=False)
        result = self._prepare_and_apply()
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")

    def test_pre_dispatch_pointer_mismatch_still_rejected(self):
        self._strip_to_pre_dispatch(consumed=602)
        self.m.atomic_write(self.m.ZCODE_LAST_PROCESSED, "700050\n")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_dispatched_but_unconsumed_still_rejected(self):
        runtime = self._strip_to_pre_dispatch(consumed=602, write_pointer=False)
        runtime["last_dispatched_message_id"] = 700100
        self._write_runtime(runtime)
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_mixed_authorized_dispatch_still_rejected(self):
        runtime = self._strip_to_pre_dispatch(consumed=602)
        runtime["authorized_dispatch"] = {
            "schema_version": 1, "MESSAGE_ID": 700100, "NONCE": "ghost",
        }
        self._write_runtime(runtime)
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_active_claim_blocks_pre_dispatch_resume(self):
        self._strip_to_pre_dispatch(consumed=602)
        ghost = self.root / "handoff" / "executor_claims" / "700100-ghost.claim"
        ghost.mkdir()
        self.m.atomic_json(ghost / "claim.json", {"MESSAGE_ID": 700100})
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_staged_completion_blocks_pre_dispatch_resume(self):
        self._strip_to_pre_dispatch(consumed=602)
        staged = self.root / "handoff" / "completion_ledger" / "staged" / "completion-700100-x"
        staged.mkdir(parents=True)
        (staged / "staging.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_unconsumed_ledger_entry_blocks_pre_dispatch_resume(self):
        self._strip_to_pre_dispatch(consumed=602)
        completion = r._load_completion_helper()
        entry = {
            "COMPLETION_PROTOCOL_VERSION": completion.COMPLETION_PROTOCOL_VERSION,
            "STATUS": completion.STATUS_COMMITTED,
            "MESSAGE_ID": 700100,
        }
        ledger = self.root / "handoff" / "completion_ledger"
        ledger.mkdir(parents=True)
        (ledger / "completion-700100-fixture.json").write_text(
            json.dumps(entry), encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    # --- retired-unclaimed HUMAN_REVIEW resume (pause/timeout retirement) ----

    def _reshape_retired_unclaimed(
        self, *, reason="PAUSE_BEFORE_CLAIM", quarantine=True, expire=True,
        live_inbox=False, retired_list=True, executor_retirements=True,
    ):
        """Reshape the post-task fixture into the retired-unclaimed scene: the
        last dispatch (last_message_id + 1) was authorized, never claimed, and
        closed by an authoritative control transaction (pause/intervention/
        timeout), so it can never be consumed."""
        runtime = copy.deepcopy(self.runtime)
        dispatched_id = self.last_message_id + 1
        dispatched_nonce = "fixture-retired-dispatch-nonce"
        retired_task = {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": dispatched_id,
            "TASK_ID": "fixture-retired-task",
            "STAGE_ID": "fixture-retired-stage",
            "ATTEMPT": 2,
            "NONCE": dispatched_nonce,
        }
        inbox = (
            f"MESSAGE_ID: {dispatched_id}\n"
            f"TASK_ID: {retired_task['TASK_ID']}\n"
            f"STAGE_ID: {retired_task['STAGE_ID']}\n\n"
            "```json\n" + json.dumps(retired_task, ensure_ascii=False, indent=2) + "\n```\n"
        )
        self.m.atomic_write(self.m.TO_ZCODE, inbox)
        inbox_hash = self.m.sha256(self.m.TO_ZCODE)
        digest = hashlib.sha256(dispatched_nonce.encode("utf-8")).hexdigest()[:24]
        archive_meta = {
            "metadata_file": (
                f"handoff/supervisor_dispatch_archive/{self.project_id}"
                f"/dispatch-{dispatched_id}-{digest}.json"),
            "archive_file": (
                f"handoff/supervisor_dispatch_archive/{self.project_id}"
                f"/dispatch-{dispatched_id}-{digest}.md"),
            "authorization_file": (
                f"handoff/supervisor_dispatch_archive/{self.project_id}"
                f"/dispatch-{dispatched_id}-{digest}.authorized.json"),
            "dispatch_sha256": inbox_hash,
        }
        for key, document in (
            ("metadata_file", {
                "schema_version": 1, "PROJECT_ID": self.project_id,
                "MESSAGE_ID": dispatched_id, "TASK_ID": retired_task["TASK_ID"],
                "STAGE_ID": retired_task["STAGE_ID"], "ATTEMPT": 2,
                "NONCE": dispatched_nonce, "dispatch_sha256": inbox_hash}),
            ("authorization_file", {
                "schema_version": 1, "PROJECT_ID": self.project_id,
                "MESSAGE_ID": dispatched_id,
                "TASK_ID": retired_task["TASK_ID"], "STAGE_ID": retired_task["STAGE_ID"],
                "ATTEMPT": 2, "NONCE": dispatched_nonce,
                "dispatch_sha256": inbox_hash}),
        ):
            path = self.root / archive_meta[key]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        (self.root / archive_meta["archive_file"]).write_text(
            inbox, encoding="utf-8", newline="\n")
        runtime["last_dispatched_message_id"] = dispatched_id
        runtime["last_dispatched_nonce"] = dispatched_nonce
        runtime["authorized_dispatch"] = {
            "schema_version": 1, "MESSAGE_ID": dispatched_id,
            "TASK_ID": retired_task["TASK_ID"], "STAGE_ID": retired_task["STAGE_ID"],
            "ATTEMPT": 2, "NONCE": dispatched_nonce, "TO_ZCODE_SHA256": inbox_hash,
            "AUTHORIZED_AT": self.m.stamp(),
            "EXPIRES_AT": "2020-01-01T00:00:00+00:00" if expire
            else "2999-01-01T00:00:00+00:00",
            "SUPERVISOR_DISPATCH_ARCHIVE": archive_meta,
        }
        runtime["retired_message_ids"] = [dispatched_id] if retired_list else []
        runtime["executor_retirements"] = [{
            "MESSAGE_ID": dispatched_id, "TASK_ID": retired_task["TASK_ID"],
            "STAGE_ID": retired_task["STAGE_ID"], "ATTEMPT": 2,
            "NONCE": dispatched_nonce, "RETIRED_AT": self.m.stamp(),
            "REASON": reason, "SUPERSEDED_BY": None,
        }] if executor_retirements else []
        if quarantine:
            quarantine_dir = self.root / "handoff" / "quarantine"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            (quarantine_dir / f"to-zcode-{dispatched_id}-pause-{inbox_hash[:12]}.md") \
                .write_text(inbox, encoding="utf-8", newline="\n")
            self.m.TO_ZCODE.unlink()
        elif not live_inbox:
            self.m.TO_ZCODE.unlink()
        self._write_runtime(runtime)
        return runtime

    def _retired_dispatch_id(self):
        return self.last_message_id + 1

    def test_pause_retired_unclaimed_human_review_resumes(self):
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        result = self._prepare_and_apply()
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        state = self._read_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(state["human_review_resume"]["status"], "PENDING_SUPERVISOR_REVIEW")
        self.assertIsNone(state["current_task"])
        runtime = json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))
        self.assertEqual(runtime["status"], "SUPERVISOR_TURN")
        self.assertEqual(runtime["pending_supervisor_event"]["reason"],
                         "HUMAN_DECISION_RESUME")

    def test_retired_unclaimed_resume_is_exactly_once(self):
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        self._prepare_and_apply()
        with self.assertRaisesRegex(r.ResumeError, "duplicate resume"):
            self._apply()

    def test_timeout_retired_unclaimed_with_quarantined_inbox_resumes(self):
        self._reshape_retired_unclaimed(reason="EXECUTOR_TIMEOUT")
        result = self._prepare_and_apply()
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")

    def test_expired_authorization_without_quarantine_resumes(self):
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM",
                                        quarantine=False, live_inbox=False)
        result = self._prepare_and_apply()
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")

    def test_timeout_retired_with_live_inbox_still_rejected(self):
        self._reshape_retired_unclaimed(reason="EXECUTOR_TIMEOUT",
                                        quarantine=False, live_inbox=True)
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_claimed_retired_dispatch_still_rejected(self):
        runtime = self._reshape_retired_unclaimed(reason="PAUSE_INTERRUPT")
        claim = self.root / "handoff" / "executor_claims" / \
            f"{self._retired_dispatch_id()}-ghost.claim"
        claim.mkdir()
        self.m.atomic_json(claim / "claim.json", {
            "MESSAGE_ID": runtime["last_dispatched_message_id"],
            "NONCE": runtime["last_dispatched_nonce"],
        })
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_live_attempt_workspace_for_retired_dispatch_still_rejected(self):
        runtime = self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        digest = hashlib.sha256(
            runtime["last_dispatched_nonce"].encode("utf-8")).hexdigest()[:24]
        workspace = self.project_root / "attempt_workspaces" / \
            f"{self._retired_dispatch_id()}-{digest}"
        workspace.mkdir(parents=True)
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_quarantine_hash_mismatch_still_rejected(self):
        dispatched_id = self._retired_dispatch_id()
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        quarantine = self.root / "handoff" / "quarantine" / \
            f"to-zcode-{dispatched_id}-pause-{'0' * 12}.md"
        quarantine.write_text("tampered quarantine copy\n", encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_archive_brief_hash_divergence_still_rejected(self):
        runtime = self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        archive_file = self.root / \
            runtime["authorized_dispatch"]["SUPERVISOR_DISPATCH_ARCHIVE"]["archive_file"]
        archive_file.write_text(
            archive_file.read_text(encoding="utf-8") + "late rewrite\n",
            encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_retired_list_without_authoritative_record_still_rejected(self):
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM",
                                        executor_retirements=False)
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_superseded_reason_still_rejected(self):
        self._reshape_retired_unclaimed(reason="SUPERSEDED")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_unexpired_authorization_without_quarantine_still_rejected(self):
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM",
                                        quarantine=False, expire=False)
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_ledger_entry_for_retired_dispatch_still_rejected(self):
        dispatched_id = self._retired_dispatch_id()
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        completion = r._load_completion_helper()
        entry = {
            "COMPLETION_PROTOCOL_VERSION": completion.COMPLETION_PROTOCOL_VERSION,
            "STATUS": completion.STATUS_COMMITTED,
            "MESSAGE_ID": dispatched_id,
        }
        ledger = self.root / "handoff" / "completion_ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        (ledger / f"completion-{dispatched_id}-fixture.json").write_text(
            json.dumps(entry), encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_completion_staging_for_retired_dispatch_still_rejected(self):
        runtime = self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        staging = self.project_root / "completion_staging" / \
            f"{self._retired_dispatch_id()}-fixture"
        staging.mkdir(parents=True)
        (staging / "staging.json").write_text(json.dumps({
            "MESSAGE_ID": runtime["last_dispatched_message_id"],
            "STATUS": "STAGING_READY",
        }), encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_sealed_staging_archive_of_consumed_task_does_not_block(self):
        # Production shape: the ledger keeps the staged copy of an already
        # sealed task as a historical archive. It must not read as live work.
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        completion = r._load_completion_helper()
        consumed_id = self.last_message_id
        entry = {
            "COMPLETION_PROTOCOL_VERSION": completion.COMPLETION_PROTOCOL_VERSION,
            "STATUS": completion.STATUS_SEALED,
            "MESSAGE_ID": consumed_id,
        }
        ledger = self.root / "handoff" / "completion_ledger"
        ledger.mkdir(parents=True, exist_ok=True)
        (ledger / f"completion-{consumed_id}-fixture.json").write_text(
            json.dumps(entry), encoding="utf-8")
        staged = ledger / "staged" / f"completion-{consumed_id}-fixture"
        staged.mkdir(parents=True)
        (staged / "staging.json").write_text(json.dumps({
            "MESSAGE_ID": consumed_id, "STATUS": "STAGING_READY",
        }), encoding="utf-8")
        result = self._prepare_and_apply()
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")

    def test_staged_copy_without_ledger_entry_still_rejected(self):
        self._reshape_retired_unclaimed(reason="PAUSE_BEFORE_CLAIM")
        staged = self.root / "handoff" / "completion_ledger" / "staged" / \
            f"completion-{self.last_message_id}-orphan"
        staged.mkdir(parents=True)
        (staged / "staging.json").write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(r.ResumeError, "not fully consumed"):
            self._prepare_and_apply()

    def test_retired_unclaimed_proof_never_matches_known_shapes(self):
        # Non-regression canaries: the new proof must stay silent on the two
        # established legal shapes and must never replace their own checks.
        self.assertFalse(r._assert_retired_unclaimed_quiescent(self.m, self.runtime))
        self._strip_to_pre_dispatch(consumed=602)
        runtime = json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))
        self.assertFalse(r._assert_retired_unclaimed_quiescent(self.m, runtime))

    def _prepare_and_apply(self):
        self._prepare()
        return self._apply()

    def _read_pending(self):
        return json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8")).get(
            "pending_supervisor_event")

    def test_apply_rearms_durable_event_for_human_resume(self):
        # The resume transaction owns the lifecycle change, so it must also
        # replace any stale/exhausted durable event with the verified resume
        # event under fresh budgets - otherwise the scheduler services the old
        # event and the lifecycle guard blocks every wake.
        self.runtime["pending_supervisor_event"] = {
            "reason": "ORCHESTRATOR_START",
            "event": {"runtime": "v2", "shared_file_executor": True},
            "recorded_at": self.m.stamp(),
            "decision_attempts": 2,
            "retry_exhausted": True,
        }
        self._write_runtime(self.runtime)
        self._prepare()
        self._apply()
        state = self._read_state()
        pending = self._read_pending()
        self.assertEqual(pending["reason"], "HUMAN_DECISION_RESUME")
        self.assertFalse(pending["retry_exhausted"])
        self.assertEqual(pending["decision_attempts"], 0)
        self.assertEqual(pending["event"], self.m.human_decision_resume_event(state))
        self.assertEqual(
            pending["rearmed_by_receipt"], state["human_review_resume"]["receipt_id"])

    def test_startup_reconcile_replaces_exhausted_event_after_resume(self):
        # Pre-fix live shape: Apply committed the lifecycle while the exhausted
        # durable event stayed owned; startup must re-arm it (audited), exactly
        # once, and never without a verified pending receipt.
        self._strip_to_pre_dispatch(consumed=602)
        self._prepare()
        self._apply()
        state = self._read_state()
        self.runtime["pending_supervisor_event"] = {
            "reason": "ORCHESTRATOR_START",
            "event": {"runtime": "v2", "shared_file_executor": True},
            "recorded_at": self.m.stamp(),
            "decision_attempts": 2,
            "retry_exhausted": True,
            "exhausted_at": self.m.stamp(),
            "last_error": "fixture exhausted",
        }
        self._write_runtime(self.runtime)
        merged = self.m.load_runtime()
        self.assertTrue(
            self.m.reconcile_exhausted_event_for_human_resume(merged, state))
        pending = merged["pending_supervisor_event"]
        self.assertEqual(pending["reason"], "HUMAN_DECISION_RESUME")
        self.assertFalse(pending["retry_exhausted"])
        self.assertEqual(pending["decision_attempts"], 0)
        self.assertEqual(pending["event"], self.m.human_decision_resume_event(state))
        self.assertEqual(pending["rearmed_from"]["last_error"], "fixture exhausted")
        # Idempotent: the re-armed event is not exhausted, so no second replace.
        self.assertFalse(
            self.m.reconcile_exhausted_event_for_human_resume(merged, state))

    def test_reconcile_ignores_non_exhausted_or_receiptless_states(self):
        state = self._read_state()
        self.runtime["pending_supervisor_event"] = {
            "reason": "EXECUTOR_RESULT_READY",
            "event": {"type": "EXECUTOR_RESULT_READY"},
            "recorded_at": self.m.stamp(),
            "decision_attempts": 1,
            "retry_exhausted": False,
        }
        self._write_runtime(self.runtime)
        merged = self.m.load_runtime()
        # Active (not exhausted) event is never replaced...
        self.assertFalse(
            self.m.reconcile_exhausted_event_for_human_resume(merged, state))
        # ...and without a pending verified receipt neither is an exhausted one.
        self.runtime["pending_supervisor_event"]["retry_exhausted"] = True
        self._write_runtime(self.runtime)
        merged = self.m.load_runtime()
        self.assertFalse(
            self.m.reconcile_exhausted_event_for_human_resume(merged, state))

    def _fixture_goal_alignment(self, method_text):
        # GOAL-ANCHOR-V1: committed decisions carry the six-field alignment record.
        return {
            "original_objective": "fixture goal",
            "unmet_criteria": "fixture criteria pending",
            "latest_result": "fixture human decision received",
            "next_action_alignment": f"{method_text} advances the fixture goal",
            "scope_drift": "none",
            "method": method_text,
        }

    def _supervisor_result(self, state=None, *, terminal_human_review=False):
        state = state or self.m.read_project_state()
        meta = state["human_review_resume"]
        if terminal_human_review:
            decision = {
                "decision": "HUMAN_REVIEW",
                "at": self.m.stamp(),
                "reason": "The bounded fixture decision requires a later human review.",
                "scope": "fixture-review",
                "message_id": None,
                "task_id": None,
                "stage_id": None,
                "goal_alignment": self._fixture_goal_alignment("HUMAN_REVIEW"),
            }
            return {
                "schema_version": self.m.HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION,
                "project_id": self.project_id,
                "receipt_id": meta["receipt_id"],
                "receipt_sha256": meta["receipt_sha256"],
                "previous_project_state_sha256": self.m.sha256(self.m.PROJECT_STATE),
                "supervisor_decision": decision,
                "resulting_lifecycle_state": "HUMAN_REVIEW",
                "project_state_patch": {
                    "status": "HUMAN_REVIEW",
                    "current_task": None,
                    "next_message_id": state["next_message_id"],
                    "blocked_reason": "fixture needs another human decision",
                },
                "executor_task": None,
            }

        message_id = state["next_message_id"]
        task = {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": message_id,
            "TASK_ID": "fixture-assumption-safe-stage",
            "STAGE_ID": "fixture-stage-attempt-1",
            "ATTEMPT": 1,
            "NONCE": "fixture-human-resume-fresh-nonce",
            "ISSUED_AT": self.m.stamp(),
            "OBJECTIVE": "Run one bounded fixture stage.",
            "OUTPUTS": ["fixture-output.json"],
            "EXECUTOR_PROTOCOL": [
                "Run python scripts/executor_claim.py acquire before stage work."
            ],
        }
        decision = {
            "decision": "CONTINUE",
            "at": self.m.stamp(),
            "reason": "The explicit Human Decision permits one bounded fixture stage.",
            "scope": "fixture-stage",
            "message_id": message_id,
            "task_id": task["TASK_ID"],
            "stage_id": task["STAGE_ID"],
            "goal_alignment": self._fixture_goal_alignment("CONTINUE"),
        }
        return {
            "schema_version": self.m.HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION,
            "project_id": self.project_id,
            "receipt_id": meta["receipt_id"],
            "receipt_sha256": meta["receipt_sha256"],
            "previous_project_state_sha256": self.m.sha256(self.m.PROJECT_STATE),
            "supervisor_decision": decision,
            "resulting_lifecycle_state": "WAITING_EXECUTOR",
            "project_state_patch": {
                "status": "WAITING_EXECUTOR",
                "current_task": copy.deepcopy(task),
                "next_message_id": message_id + 1,
                "blocked_reason": None,
            },
            "executor_task": task,
        }

    @staticmethod
    def _fake_codex_success(result, commands=None):
        def run(cmd, **_kwargs):
            if commands is not None:
                commands.append(list(cmd))
            output_path = Path(cmd[cmd.index("-o") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)

        return run

    def _invoke_with_owned_lock(self, result=None, *, run_side_effect=None):
        if run_side_effect is None:
            run_side_effect = self._fake_codex_success(result)
        self.m.acquire_lock()
        try:
            with patch.object(self.m, "find_codex", return_value="codex-fixture"), patch.object(
                self.m.subprocess, "run", side_effect=run_side_effect
            ):
                self.m.invoke_codex(
                    self.runtime,
                    "HUMAN_DECISION_RESUME",
                    self.m.human_decision_resume_event(self.m.read_project_state()),
                )
        finally:
            self.m.release_lock()

    def _commit_direct(self, result):
        state = self.m.read_project_state()
        state_hash = self.m.sha256(self.m.PROJECT_STATE)
        self.m.acquire_lock()
        try:
            return self.m.commit_human_decision_supervisor_result(
                self.runtime, state, state_hash, result
            )
        finally:
            self.m.release_lock()

    def test_normal_resume_archives_receipt_and_only_enters_supervisor_turn(self):
        state_hash_before = self.m.sha256(self.project_root / "project_state.json")
        runtime_before = copy.deepcopy(self.runtime)
        receipt = self._prepare()
        self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")
        self.assertEqual(receipt["previous_project_state_sha256"], state_hash_before)

        result = self._apply()
        state = self._read_state()
        runtime = json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "SUPERVISOR_TURN")
        self.assertFalse(result["executor_dispatched"])
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertIsNone(state["current_task"])
        self.assertEqual(state["next_message_id"], 700100)
        self.assertEqual(state["last_supervisor_decision"], "HUMAN_REVIEW — fixture")
        self.assertEqual(self.m.TO_ZCODE.read_text(encoding="utf-8"), self.original_inbox)
        self.assertEqual(runtime["last_consumed_message_id"], runtime_before["last_consumed_message_id"])
        self.assertEqual(runtime["last_dispatched_message_id"], runtime_before["last_dispatched_message_id"])
        archive = Path(result["receipt_archive"])
        self.assertTrue(archive.is_file())
        self.assertEqual(self.m.sha256(archive), state["human_review_resume"]["receipt_file_sha256"])

    def test_duplicate_resume_fails_closed(self):
        self._prepare()
        self._apply()
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_STALE_OR_DUPLICATE)

    def test_wrong_project_receipt_fails_closed(self):
        receipt = self._prepare()
        receipt["project_id"] = "wrong-project"
        receipt["receipt_sha256"] = self.m.human_decision_receipt_hash(receipt)
        self.m.atomic_json(self.receipt_path, receipt)
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
        self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_stale_project_state_hash_fails_closed(self):
        self._prepare()
        state = self._read_state()
        state["notes"].append("external state edit")
        self._write_state(state)
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_STALE_OR_DUPLICATE)
        self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_non_null_current_task_fails_closed(self):
        self._prepare()
        state = self._read_state()
        state["current_task"] = {"MESSAGE_ID": 700100}
        self._write_state(state)
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)

    def test_previous_task_not_consumed_fails_closed(self):
        self._prepare()
        runtime = copy.deepcopy(self.runtime)
        runtime["last_consumed_message_id"] = self.last_message_id - 1
        self._write_runtime(runtime)
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)

    def _write_last_processed(self, text):
        self.m.atomic_write(self.m.ZCODE_LAST_PROCESSED, text)

    def _v2_last_processed(self, **overrides):
        identity = dict(self.last_task_identity)
        identity.update(overrides)
        return "".join(f"{key}={value}\n" for key, value in identity.items())

    def test_v2_key_value_last_processed_resumes_normally(self):
        self.assertEqual(
            self.m.ZCODE_LAST_PROCESSED.read_text(encoding="utf-8"),
            self._v2_last_processed(),
        )
        self._prepare()
        result = self._apply()
        self.assertEqual(result["status"], "SUPERVISOR_TURN")
        self.assertEqual(self._read_state()["status"], "SUPERVISOR_TURN")

    def test_legacy_numeric_last_processed_still_accepted(self):
        self._write_last_processed(f"{self.last_message_id}\n")
        self._prepare()
        result = self._apply()
        self.assertEqual(result["status"], "SUPERVISOR_TURN")

    def test_malformed_last_processed_fails_closed(self):
        self._prepare()
        for variant in (
            "",
            "   \n",
            "not-a-pointer\n",
            f"{self.last_message_id}abc\n",
            self._v2_last_processed() + "GARBAGE LINE\n",
            self._v2_last_processed() + "EXTRA=1\n",
            self._v2_last_processed(MESSAGE_ID=self.last_message_id + 50000),
        ):
            with self.subTest(variant=variant[:40]):
                self._write_last_processed(variant)
                with self.assertRaises(r.ResumeError) as caught:
                    self._apply()
                self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
                self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_last_processed_missing_fields_fail_closed(self):
        self._prepare()
        for key in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE"):
            with self.subTest(missing=key):
                broken = self._v2_last_processed().replace(
                    f"{key}={self.last_task_identity[key]}\n", ""
                )
                self._write_last_processed(broken)
                with self.assertRaises(r.ResumeError) as caught:
                    self._apply()
                self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
                self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_last_processed_illegal_attempt_fails_closed(self):
        self._prepare()
        for bad in ("THREE", "0", "-1", "1.5"):
            with self.subTest(attempt=bad):
                self._write_last_processed(self._v2_last_processed(ATTEMPT=bad))
                with self.assertRaises(r.ResumeError) as caught:
                    self._apply()
                self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
                self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_last_processed_duplicate_identity_field_fails_closed(self):
        self._prepare()
        pointer = self._v2_last_processed()
        duplicated = pointer + f"MESSAGE_ID={self.last_message_id}\n"
        self._write_last_processed(duplicated)
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
        self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_v2_identity_mismatch_with_authoritative_state_fails_closed(self):
        self._prepare()
        for key in ("TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE"):
            forged = {key: f"forged-{str(self.last_task_identity[key]).lower()}"}
            if key == "ATTEMPT":
                forged = {key: self.last_task_identity[key] + 1}
            with self.subTest(field=key):
                self._write_last_processed(self._v2_last_processed(**forged))
                with self.assertRaises(r.ResumeError) as caught:
                    self._apply()
                self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
                self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")

    def test_stop_or_unconsumed_done_signal_fails_closed(self):
        for signal_path, content in (
            (self.m.STOP_FLAG, "USER_STOP\n"),
            (self.m.ZCODE_DONE, "done\n"),
        ):
            with self.subTest(signal=signal_path.name):
                signal_path.unlink(missing_ok=True)
                if self.receipt_path.exists():
                    self.receipt_path.unlink()
                self._prepare()
                self.m.atomic_write(signal_path, content)
                with self.assertRaises(r.ResumeError) as caught:
                    self._apply()
                self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
                self.assertEqual(self._read_state()["status"], "HUMAN_REVIEW")
                signal_path.unlink(missing_ok=True)

    def test_authorized_inbox_hash_mismatch_fails_closed(self):
        self._prepare()
        self.m.atomic_write(self.m.TO_ZCODE, self.original_inbox + "tampered\n")
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)

    def test_conflicting_terminal_states_fail_closed(self):
        self._prepare()
        for status in ("COMPLETE", "STOPPED", "BLOCKED"):
            with self.subTest(status=status):
                state = copy.deepcopy(self.base_state)
                state["status"] = status
                self._write_state(state)
                with self.assertRaises(r.ResumeError) as caught:
                    self._apply()
                self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)

    def test_active_project_and_project_state_identity_mismatch_fails_closed(self):
        self._prepare()
        state = self._read_state()
        state["project_id"] = "different-state-project"
        self._write_state(state)
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)

    def test_concurrent_lock_holder_fails_closed_without_changes(self):
        self._prepare()
        before = self.m.sha256(self.project_root / "project_state.json")
        self.m.atomic_json(self.m.LOCK_FILE, {"pid": os.getpid(), "started_at": self.m.stamp()})
        try:
            with self.assertRaises(r.ResumeError) as caught:
                self._apply()
            self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)
            self.assertEqual(self.m.sha256(self.project_root / "project_state.json"), before)
        finally:
            self.m.LOCK_FILE.unlink(missing_ok=True)

    def test_unfinished_newer_executor_claim_fails_closed(self):
        self._prepare()
        newer = self.root / "handoff" / "executor_claims" / "700100-newer.claim"
        newer.mkdir()
        self.m.atomic_json(newer / "claim.json", {"MESSAGE_ID": 700100})
        with self.assertRaises(r.ResumeError) as caught:
            self._apply()
        self.assertEqual(caught.exception.code, r.EXIT_CONFLICT)

    def test_orchestrator_routes_resumed_state_to_supervisor_not_executor(self):
        self._prepare()
        self._apply()
        calls = []

        def fake_invoke(runtime, reason, event=None):
            calls.append((reason, event))
            raise KeyboardInterrupt

        self.m.DESKTOP_NOTIFICATIONS_ENABLED = False
        self.m.USER_NOTIFICATION_CONSOLE_ENABLED = False
        with patch.object(self.m, "invoke_codex", side_effect=fake_invoke), patch.object(
            self.m,
            "register_dispatched_task",
            side_effect=AssertionError("resume must enter Supervisor before any Executor registration"),
        ):
            exit_code = self.m.main()
        self.assertEqual(exit_code, 130)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "HUMAN_DECISION_RESUME")
        self.assertEqual(calls[0][1]["receipt_id"], self._read_state()["human_review_resume"]["receipt_id"])

    def test_verified_human_decision_is_injected_verbatim_into_supervisor_prompt(self):
        self._prepare()
        self._apply()
        self.m.activate_project_scope()
        state = self.m.read_project_state()
        prompt = self.m.build_codex_prompt("HUMAN_DECISION_RESUME", {}, state)
        self.assertIn("=== VERIFIED HUMAN DECISION RECEIPT ===", prompt)
        self.assertIn(self.payload["decision_content"], prompt)
        for constraint in self.payload["constraints_verbatim"]:
            self.assertIn(constraint, prompt)
        self.assertIn("allocates no MESSAGE_ID", prompt)
        self.assertIn("read-only sandbox", prompt)
        self.assertIn("Return exactly one JSON object", prompt)

    def test_pending_valid_decision_is_atomically_consumed_and_never_reinjected(self):
        pending = self._pending_state()
        result = self._supervisor_result(pending)
        commands = []
        self._invoke_with_owned_lock(
            run_side_effect=self._fake_codex_success(result, commands)
        )

        state = self.m.read_project_state()
        meta = state["human_review_resume"]
        self.assertEqual(state["status"], "WAITING_EXECUTOR")
        self.assertEqual(meta["status"], "CONSUMED")
        self.assertEqual(meta["project_id"], self.project_id)
        self.assertEqual(meta["receipt_id"], pending["human_review_resume"]["receipt_id"])
        self.assertEqual(meta["resulting_supervisor_decision"], "CONTINUE")
        self.assertEqual(meta["resulting_lifecycle_state"], "WAITING_EXECUTOR")
        self.assertEqual(len(state["human_decision_consumption_ledger"]), 1)
        self.assertEqual(
            meta["decision_sha256"],
            self.m.canonical_json_sha256(state["decision_history"][-1]),
        )
        self.assertIsNone(self.m.load_verified_human_decision_for_supervisor(state))
        self.assertIn("--sandbox", commands[0])
        self.assertIn("read-only", commands[0])
        self.assertIn("--ephemeral", commands[0])
        self.assertNotIn("--approve-for-me", commands[0])

        # A later ordinary Supervisor lifecycle must retain audit history without
        # restoring authorization semantics to the consumed receipt.
        later = copy.deepcopy(state)
        later["status"] = "SUPERVISOR_TURN"
        later["current_task"] = None
        self._write_state(later)
        self.assertIsNone(self.m.human_decision_resume_event(self.m.read_project_state()))
        prompt = self.m.build_codex_prompt(
            "SUPERVISOR_TURN", {}, self.m.read_project_state()
        )
        self.assertNotIn("=== VERIFIED HUMAN DECISION RECEIPT ===", prompt)

    def test_codex_timeout_keeps_receipt_pending_and_replayable(self):
        pending = self._pending_state()

        def timeout(*_args, **_kwargs):
            raise subprocess.TimeoutExpired("codex-fixture", 1)

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            self._invoke_with_owned_lock(run_side_effect=timeout)
        state = self.m.read_project_state()
        self.assertEqual(state, pending)
        self.assertEqual(state["human_review_resume"]["status"], "PENDING_SUPERVISOR_REVIEW")
        self.assertIsNotNone(self.m.human_decision_resume_event(state))

    def test_codex_nonzero_keeps_receipt_pending(self):
        pending = self._pending_state()

        def nonzero(cmd, **_kwargs):
            return subprocess.CompletedProcess(cmd, 9)

        with self.assertRaisesRegex(RuntimeError, "exited with code 9"):
            self._invoke_with_owned_lock(run_side_effect=nonzero)
        self.assertEqual(self.m.read_project_state(), pending)
        self.assertIsNotNone(self.m.human_decision_resume_event(self.m.read_project_state()))

    def test_malformed_or_invalid_decision_keeps_receipt_pending(self):
        pending = self._pending_state()
        for failure in ("malformed", "invalid_lifecycle", "invalid_policy"):
            with self.subTest(failure=failure):
                if failure != "malformed" and self.m.read_project_state().get("status") != "SUPERVISOR_TURN":
                    self.fail("fixture unexpectedly left pending lifecycle")
                pending = self.m.read_project_state()

                if failure == "malformed":
                    def run(cmd, **_kwargs):
                        output_path = Path(cmd[cmd.index("-o") + 1])
                        output_path.parent.mkdir(parents=True, exist_ok=True)
                        output_path.write_text("{malformed", encoding="utf-8")
                        return subprocess.CompletedProcess(cmd, 0)
                else:
                    bad = self._supervisor_result(pending)
                    if failure == "invalid_lifecycle":
                        bad["supervisor_decision"]["decision"] = "STOP"
                    else:
                        bad["supervisor_decision"]["decision"] = "STOP"
                        bad["supervisor_decision"]["message_id"] = None
                        bad["supervisor_decision"]["task_id"] = None
                        bad["supervisor_decision"]["stage_id"] = None
                        bad["resulting_lifecycle_state"] = "COMPLETE"
                        bad["project_state_patch"]["status"] = "COMPLETE"
                        bad["project_state_patch"]["current_task"] = None
                        bad["project_state_patch"]["next_message_id"] = pending["next_message_id"]
                        bad["executor_task"] = None
                    run = self._fake_codex_success(bad)

                with self.assertRaises(RuntimeError):
                    self._invoke_with_owned_lock(run_side_effect=run)
                self.assertEqual(self.m.read_project_state(), pending)
                self.assertIsNotNone(self.m.human_decision_resume_event(pending))

    def test_fault_before_durable_commit_keeps_pending_and_replayable(self):
        pending = self._pending_state()
        result = self._supervisor_result(pending)
        original_atomic_json = self.m.atomic_json

        def fail_project_commit(path, value):
            if Path(path) == Path(self.m.PROJECT_STATE):
                raise OSError("injected before durable project-state commit")
            return original_atomic_json(path, value)

        with patch.object(self.m, "atomic_json", side_effect=fail_project_commit):
            with self.assertRaisesRegex(OSError, "before durable"):
                self._invoke_with_owned_lock(result)
        state = self.m.read_project_state()
        self.assertEqual(state, pending)
        self.assertEqual(state["human_review_resume"]["status"], "PENDING_SUPERVISOR_REVIEW")
        self.assertIsNotNone(self.m.human_decision_resume_event(state))
        runtime = json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))
        self.assertEqual(runtime["authorized_dispatch"]["MESSAGE_ID"], self.last_message_id)

    def test_fault_after_durable_commit_stays_consumed_and_restart_does_not_reinject(self):
        self._pending_state()
        result = self._supervisor_result()
        with patch.object(
            self.m,
            "register_dispatched_task",
            side_effect=RuntimeError("injected immediately after durable commit"),
        ):
            with self.assertRaisesRegex(RuntimeError, "immediately after durable"):
                self._invoke_with_owned_lock(result)

        committed = self.m.read_project_state()
        self.assertEqual(committed["human_review_resume"]["status"], "CONSUMED")
        self.assertIsNone(self.m.human_decision_resume_event(committed))

        # Startup recovers the valid visible-but-unregistered task through the
        # existing WAITING_EXECUTOR path; it must not call Supervisor again.
        self.m.DESKTOP_NOTIFICATIONS_ENABLED = False
        self.m.USER_NOTIFICATION_CONSOLE_ENABLED = False
        with patch.object(
            self.m, "invoke_codex", side_effect=AssertionError("consumed receipt was replayed")
        ), patch.object(self.m.time, "sleep", side_effect=KeyboardInterrupt):
            exit_code = self.m.main()
        self.assertEqual(exit_code, 130)
        runtime = json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))
        self.assertEqual(runtime["authorized_dispatch"]["MESSAGE_ID"], result["executor_task"]["MESSAGE_ID"])

    def test_consumed_a_allows_new_pending_and_consumed_b(self):
        self._pending_state()
        first_result = self._supervisor_result(terminal_human_review=True)
        first_committed = self._commit_direct(first_result)
        first_id = first_committed["human_review_resume"]["receipt_id"]
        self.assertEqual(first_committed["human_review_resume"]["status"], "CONSUMED")

        runtime = json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8"))
        runtime["status"] = "HUMAN_REVIEW"
        self._write_runtime(runtime)
        self.receipt_path = self.root / "second-human-decision.json"
        self.payload = {
            "decision_content": "A distinct later Human Decision B.",
            "constraints_verbatim": ["Decision A remains consumed and auditable."],
        }
        second_receipt = self._prepare()
        self._apply()
        pending_b = self.m.read_project_state()
        self.assertEqual(pending_b["human_review_resume"]["receipt_id"], second_receipt["receipt_id"])
        self.assertIsNotNone(self.m.load_verified_human_decision_for_supervisor(pending_b))
        second_committed = self._commit_direct(
            self._supervisor_result(pending_b, terminal_human_review=True)
        )
        ledger = second_committed["human_decision_consumption_ledger"]
        self.assertEqual([item["receipt_id"] for item in ledger], [first_id, second_receipt["receipt_id"]])
        self.assertTrue(all(item["status"] == "CONSUMED" for item in ledger))

    def test_duplicate_consumption_and_second_lock_holder_fail_closed(self):
        pending = self._pending_state()
        pending_hash = self.m.sha256(self.m.PROJECT_STATE)
        result = self._supervisor_result(pending)
        self._commit_direct(result)
        committed_hash = self.m.sha256(self.m.PROJECT_STATE)

        self.m.acquire_lock()
        try:
            with self.assertRaises(RuntimeError):
                self.m.commit_human_decision_supervisor_result(
                    self.runtime, pending, pending_hash, result
                )
            with self.assertRaises(RuntimeError):
                self.m.acquire_lock()
        finally:
            self.m.release_lock()
        self.assertEqual(self.m.sha256(self.m.PROJECT_STATE), committed_hash)

    def test_consumed_receipt_cannot_be_rewritten_to_pending(self):
        self._pending_state()
        committed = self._commit_direct(self._supervisor_result())
        forged_pending = {
            key: copy.deepcopy(committed["human_review_resume"][key])
            for key in self.m.HUMAN_DECISION_RESUME_BASE_KEYS
        }
        forged_pending["status"] = "PENDING_SUPERVISOR_REVIEW"
        committed["status"] = "SUPERVISOR_TURN"
        committed["current_task"] = None
        committed["human_review_resume"] = forged_pending
        self._write_state(committed)
        with self.assertRaisesRegex(RuntimeError, "cannot return to pending"):
            self.m.load_verified_human_decision_for_supervisor(self.m.read_project_state())

    def test_tampered_consumption_binding_fails_closed(self):
        self._pending_state()
        committed = self._commit_direct(self._supervisor_result())
        committed["human_decision_consumption_ledger"][0]["decision_sha256"] = "0" * 64
        self._write_state(committed)
        with self.assertRaisesRegex(RuntimeError, "decision_sha256"):
            self.m.load_verified_human_decision_for_supervisor(self.m.read_project_state())

    def test_tampered_archived_receipt_blocks_supervisor_context(self):
        self._prepare()
        result = self._apply()
        archive = Path(result["receipt_archive"])
        tampered = json.loads(archive.read_text(encoding="utf-8"))
        tampered["human_decision"]["decision_content"] = "tampered"
        self.m.atomic_json(archive, tampered)
        self.m.activate_project_scope()
        with self.assertRaises(RuntimeError):
            self.m.human_decision_resume_event(self.m.read_project_state())

if __name__ == "__main__":
    unittest.main()
