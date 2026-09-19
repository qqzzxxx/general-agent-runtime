"""HUMAN-DECISION-FV-BRIDGE-V1 regression: Human Decision -> fresh Final Verification.

Pins the corrected Runtime semantics from
docs/v1.4-human-decision-fv-serial-reproduction.md section 9:

* the resume-turn decision vocabulary admits FINAL_VERIFICATION and
  FINAL_ACCEPTANCE with their decision/status pairs (COMPLETE now requires
  FINAL_ACCEPTANCE; STOP no longer produces COMPLETE);
* a resume-turn FINAL_VERIFICATION must carry FINAL_VERIFICATION_REQUEST and is
  bridged through final_verification_contract.prepare() — the Runtime authors
  the gate, records its provenance, and only then validates/publishes;
* a hand-authored gate is rejected on the resume path (REQUEST-only rule and
  decision binding) and at registration (gate provenance), so internally
  self-consistent gates never gain authority;
* FINAL_ACCEPTANCE runs the ordinary terminal predicate unchanged — human
  authorization alone cannot satisfy the FV gate;
* exhausting a HUMAN_DECISION_RESUME retry budget voids the pending receipt
  into the terminal EXHAUSTED shape, keeping a later Human Review cycle
  preparable (no stranded pending receipt).
"""

import copy
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
RESUME_PATH = Path(__file__).resolve().with_name("resume_human_review.py")
spec = importlib.util.spec_from_file_location("resume_human_review_bridge_under_test", RESUME_PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)

import final_verification_contract as contract  # noqa: E402


CLAIMS = [
    {
        "claim_id": "C1",
        "claim": "The baseline deliverable exists and records findings F1-F3.",
        "claim_type": "DELIVERABLE_INTEGRITY",
        "decision_impact": "HIGH",
        "evidence_pointers": ["workspace/baseline-analysis.md"],
        "verification_standard": "Confirm the deliverable exists and contains F1-F3.",
    },
    {
        "claim_id": "C2",
        "claim": "The counts digest matches the deliverable.",
        "claim_type": "NUMERICAL",
        "decision_impact": "HIGH",
        "evidence_pointers": ["evidence/metrics-digest.txt"],
        "verification_standard": "Recompute the counts and compare.",
    },
    {
        "claim_id": "C3",
        "claim": "Every finding cites its source section.",
        "claim_type": "SOURCE_SUPPORT",
        "decision_impact": "MEDIUM",
        "evidence_pointers": ["workspace/baseline-analysis.md"],
        "verification_standard": "Check each finding for an explicit citation.",
    },
]


class HumanDecisionFVBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="human-decision-fv-bridge-")
        self.root = Path(self.temp.name)
        self.m = r.load_runtime_module(RUNTIME_ROOT)
        r.bind_runtime_paths(self.m, self.root)
        self.m.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        # final_verification_contract.prepare() resolves policy through an engine
        # module bound to <root>/profiles, so the fixture root needs real profiles.
        shutil.copytree(RUNTIME_ROOT / "profiles", self.root / "profiles")
        for path in (
            self.m.CONTROL,
            self.m.LOGS,
            self.m.HANDOFF_ARCHIVE,
            self.m.REPORTS,
            self.root / "handoff" / "executor_claims",
        ):
            path.mkdir(parents=True, exist_ok=True)
        self.project_id = "bridge-test"
        self.project_root = self.root / "projects" / self.project_id
        self.project_root.mkdir(parents=True)
        (self.project_root / "workspace").mkdir(parents=True, exist_ok=True)
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
        self.goal_sha256 = hashlib.sha256(
            (self.project_root / "PROJECT_GOAL.md").read_bytes()).hexdigest()

        policy, _ = self.m._resolve_fv_policy_for_state({"profile": "GENERAL"})
        self.claims_hash = self.m.canonical_claims_hash(CLAIMS)
        # The reproduction fork state: an FV PASS (message 700098) superseded by a
        # newer ordinary receipt (700099), project parked in HUMAN_REVIEW.
        self.fv_message_id = 700098
        self.fv_receipt_sha = "a" * 64
        self.next_message_id = 700100
        self.base_state = {
            "schema_version": 4,
            "project_id": self.project_id,
            "project_type": "GENERAL",
            "profile": "GENERAL",
            "status": "HUMAN_REVIEW",
            "phase": "GENERAL",
            "created_at": self.m.stamp(),
            "started_at": self.m.stamp(),
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
            "next_message_id": self.next_message_id,
            "final_verification": {
                "policy_version": policy["policy_version"],
                "required": True,
                "status": "PENDING",
                "policy_id": policy["policy_id"],
                "critical_claims": copy.deepcopy(CLAIMS),
                "claims_hash": self.claims_hash,
                "verification_message_id": None,
                "verification_receipt_sha256": None,
                "verified_at": None,
            },
            "last_supervisor_decision": {
                "decision": "HUMAN_REVIEW",
                "scope": "verification-authorization",
                "reason": "fixture park: fresh FV authorization belongs to the human",
            },
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
            "final_verification_receipt_ledger": [{
                "message_id": self.fv_message_id,
                "nonce": "fv-fixture-nonce",
                "receipt_sha256": self.fv_receipt_sha,
                "claims_hash": self.claims_hash,
                "overall_status": "PASS",
                "mechanical_pass": True,
                "consumed_at": "2026-09-18T00:00:00+00:00",
            }],
            "last_final_verification_message_id": self.fv_message_id,
            "last_final_verification_receipt_sha256": self.fv_receipt_sha,
            "last_final_verification_claims_hash": self.claims_hash,
            "last_final_verification_overall_status": "PASS",
            "last_final_verification_mechanical_pass": True,
        }
        self._write_runtime(self.runtime)
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
            self.root / "handoff" / "executor_claims" / f"{self.last_message_id}-fixture.claim"
        )
        claim_dir.mkdir()
        self.m.atomic_json(
            claim_dir / "claim.json",
            {"MESSAGE_ID": self.last_message_id, "NONCE": self.last_nonce},
        )
        self.payload = {
            "decision_content": (
                "Authorization for one limited Final Verification re-run of the identical "
                "claims against the current latest evidence, through the Runtime's formal "
                "gate, followed by the normal FINAL_ACCEPTANCE path if it passes."
            ),
            "constraints_verbatim": [
                "Exactly one limited FINAL_VERIFICATION re-run is authorized.",
                "Do not hand-author a FINAL_VERIFICATION_GATE; only the Runtime constructs gates.",
                "A Human Decision is not itself an FV result.",
            ],
        }
        self.receipt_path = self.root / "prepared-human-decision.json"

    def tearDown(self):
        self.temp.cleanup()

    # --- fixture helpers -----------------------------------------------------

    def _write_state(self, value):
        self.m.atomic_json(self.project_root / "project_state.json", value)

    def _read_state(self):
        return json.loads(
            (self.project_root / "project_state.json").read_text(encoding="utf-8-sig"))

    def _write_runtime(self, value):
        self.m.atomic_json(self.m.RUNTIME_STATE, value)

    def _read_runtime(self):
        return json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8-sig"))

    def _reset_park(self):
        """Return the fixture to the pre-receipt HUMAN_REVIEW park state."""
        self._write_state(copy.deepcopy(self.base_state))
        runtime = self._read_runtime()
        runtime["status"] = "HUMAN_REVIEW"
        runtime["pending_supervisor_event"] = None
        self._write_runtime(runtime)
        self.receipt_path.unlink(missing_ok=True)

    def _pending_state(self):
        receipt = r.prepare_receipt(
            self.m, project_id=self.project_id,
            decision_payload=self.payload, receipt_out=self.receipt_path)
        r.apply_receipt(self.m, receipt_path=self.receipt_path)
        self.m.activate_project_scope()
        return self._read_state(), receipt

    def _goal_alignment(self, method_text):
        return {
            "original_objective": "fixture goal",
            "unmet_criteria": "Runtime FV PASS over the latest evidence",
            "latest_result": "FV PASS superseded by newer ordinary evidence",
            "next_action_alignment": f"{method_text} advances the fixture goal",
            "scope_drift": "none",
            "method": method_text,
        }

    def _fv_request_task(self, message_id=None):
        return {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": message_id or self.next_message_id,
            "TASK_ID": "fixture-fv-reverification",
            "STAGE_ID": "fixture-fv-stage-1",
            "ATTEMPT": 1,
            "NONCE": "fixture-fv-fresh-nonce",
            "ISSUED_AT": self.m.stamp(),
            "MAX_TIME": 2700,
            "MAX_RETRIES": 0,
            "TASK_KIND": "FINAL_VERIFICATION",
            "OBJECTIVE": "Re-verify the identical claims over the combined latest evidence.",
            "OUTPUTS": ["reports/fv-results.md"],
            "EXECUTOR_PROTOCOL": [
                "Run python scripts/executor_claim.py acquire --message-id <MESSAGE_ID> "
                "--task-id <TASK_ID> --stage-id <STAGE_ID> --attempt <ATTEMPT> "
                "--nonce <NONCE> before any verification work.",
            ],
            "FINAL_VERIFICATION_REQUEST": {"CRITICAL_CLAIMS": copy.deepcopy(CLAIMS)},
        }

    def _hand_authored_gate(self, task):
        policy, _ = self.m._resolve_fv_policy_for_state({"profile": "GENERAL"})
        task = copy.deepcopy(task)
        task.pop("FINAL_VERIFICATION_REQUEST", None)
        task["FINAL_VERIFICATION_GATE"] = {
            "CONTRACT_VERSION": 1,
            "POLICY_ID": policy["policy_id"],
            "POLICY_VERSION": policy["policy_version"],
            "CLAIMS_HASH": self.claims_hash,
            "CLAIM_COUNT": len(CLAIMS),
            "CRITICAL_CLAIMS": copy.deepcopy(CLAIMS),
            "EXECUTION_MODE": self.m.final_verification_policy_execution_mode(policy),
            "POLICY_SNAPSHOT": policy,
            "POLICY_SHA256": contract.digest(policy),
        }
        return task

    def _result(self, state, *, decision, resulting, executor_task=None,
                patch_extra=None):
        meta = state["human_review_resume"]
        identity = {
            "message_id": None, "task_id": None, "stage_id": None,
        }
        patch = {
            "status": resulting,
            "current_task": None,
            "next_message_id": state["next_message_id"],
        }
        if executor_task is not None:
            identity = {
                "message_id": executor_task["MESSAGE_ID"],
                "task_id": executor_task["TASK_ID"],
                "stage_id": executor_task["STAGE_ID"],
            }
            patch["current_task"] = {
                key: executor_task[key] for key in self.m.IDENTITY_KEYS}
            patch["next_message_id"] = executor_task["MESSAGE_ID"] + 1
        if patch_extra:
            patch.update(patch_extra)
        return {
            "schema_version": self.m.HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION,
            "project_id": self.project_id,
            "receipt_id": meta["receipt_id"],
            "receipt_sha256": meta["receipt_sha256"],
            "previous_project_state_sha256": self.m.sha256(self.m.PROJECT_STATE),
            "supervisor_decision": {
                "decision": decision,
                "at": self.m.stamp(),
                "reason": "fixture bridge decision",
                "scope": "fixture-bridge",
                **identity,
                "goal_alignment": self._goal_alignment(decision),
            },
            "resulting_lifecycle_state": resulting,
            "project_state_patch": patch,
            "executor_task": executor_task,
        }

    def _commit_direct(self, result):
        state = self.m.read_project_state()
        state_hash = self.m.sha256(self.m.PROJECT_STATE)
        self.m.acquire_lock()
        try:
            return self.m.commit_human_decision_supervisor_result(
                self.runtime, state, state_hash, result)
        finally:
            self.m.release_lock()

    def _published_task(self):
        text = self.m.TO_ZCODE.read_text(encoding="utf-8-sig")
        return json.loads(text.split("```json")[1].split("```")[0])

    # --- 1. decision vocabulary and status pairs ------------------------------

    def test_vocabulary_and_status_pairs_pin_the_bridge(self):
        for decision in ("FINAL_VERIFICATION", "FINAL_ACCEPTANCE"):
            self.assertIn(decision, self.m.HUMAN_DECISION_ALLOWED_DECISIONS)
        self.m._validate_human_decision_status_pair("FINAL_VERIFICATION", "WAITING_EXECUTOR")
        self.m._validate_human_decision_status_pair("FINAL_ACCEPTANCE", "COMPLETE")
        for decision, status in (
            ("STOP", "COMPLETE"),
            ("FINAL_VERIFICATION", "COMPLETE"),
            ("FINAL_ACCEPTANCE", "WAITING_EXECUTOR"),
            ("REVISE", "COMPLETE"),
        ):
            with self.subTest(decision=decision, status=status):
                with self.assertRaisesRegex(
                        RuntimeError, "does not match resulting lifecycle state"):
                    self.m._validate_human_decision_status_pair(decision, status)

    def test_resume_prompt_contract_states_the_new_decisions(self):
        state, _ = self._pending_state()
        prompt = self.m.build_codex_prompt("HUMAN_DECISION_RESUME", {}, state)
        self.assertIn(
            "decision must be CONTINUE, REDIRECT, CHANGE_METHOD, REVISE, "
            "FINAL_VERIFICATION,", prompt)
        self.assertIn(
            "executor_task must carry FINAL_VERIFICATION_REQUEST", prompt)

    # --- 2. the authorized resume FINAL_VERIFICATION bridge -------------------

    def test_authorized_final_verification_resume_commits_runtime_prepared_gate(self):
        state, _ = self._pending_state()
        task = self._fv_request_task()
        committed = self._commit_direct(
            self._result(state, decision="FINAL_VERIFICATION",
                         resulting="WAITING_EXECUTOR", executor_task=task))
        self.assertEqual(committed["status"], "WAITING_EXECUTOR")
        self.assertEqual(committed["last_supervisor_decision"]["decision"],
                         "FINAL_VERIFICATION")
        # prepare() authored the gate and pinned the state mirror.
        self.assertEqual(committed["final_verification"]["status"], "PENDING")
        self.assertEqual(committed["final_verification"]["claims_hash"], self.claims_hash)
        published = self._published_task()
        self.assertNotIn("FINAL_VERIFICATION_REQUEST", published)
        self.assertIn("FINAL_VERIFICATION_GATE", published)
        self.assertEqual(published["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"],
                         self.claims_hash)
        # Gate provenance was durably recorded by prepare().
        runtime = self._read_runtime()
        self.assertTrue(any(
            entry.get("message_id") == self.next_message_id
            and entry.get("claims_hash") == self.claims_hash
            for entry in runtime.get("final_verification_prepared_identities") or []))
        # The prepared dispatch authorizes through the ordinary registration path.
        registered = self.m.register_dispatched_task(runtime, committed)
        self.assertEqual(registered["MESSAGE_ID"], self.next_message_id)
        authorized = self._read_runtime()["authorized_dispatch"]
        self.assertEqual(authorized["MESSAGE_ID"], self.next_message_id)
        self.assertIs(authorized["IS_FINAL_VERIFICATION"], True)

    def test_resume_final_verification_with_hand_authored_gate_rejected(self):
        state, _ = self._pending_state()
        task = self._hand_authored_gate(self._fv_request_task())
        with self.assertRaisesRegex(
                RuntimeError, "requires FINAL_VERIFICATION_REQUEST"):
            self._commit_direct(self._result(
                state, decision="FINAL_VERIFICATION",
                resulting="WAITING_EXECUTOR", executor_task=task))
        after = self._read_state()
        self.assertEqual(after["status"], "SUPERVISOR_TURN")
        self.assertEqual(after["human_review_resume"]["status"],
                         "PENDING_SUPERVISOR_REVIEW")
        self.assertEqual(after["next_message_id"], self.next_message_id)

    def test_fv_task_under_non_fv_decision_rejected(self):
        for task in (self._fv_request_task(), self._hand_authored_gate(
                self._fv_request_task())):
            with self.subTest(request="REQUEST" if "FINAL_VERIFICATION_REQUEST"
                              in task else "GATE"):
                self._reset_park()
                state, _ = self._pending_state()
                with self.assertRaisesRegex(
                        RuntimeError,
                        "FINAL_VERIFICATION dispatch requires decision=FINAL_VERIFICATION"):
                    self._commit_direct(self._result(
                        state, decision="REVISE",
                        resulting="WAITING_EXECUTOR", executor_task=task))
                after = self._read_state()
                self.assertEqual(after["human_review_resume"]["status"],
                                 "PENDING_SUPERVISOR_REVIEW")

    def test_resume_final_verification_prepare_failure_keeps_receipt_pending(self):
        state, _ = self._pending_state()
        task = self._fv_request_task()
        # A conflicting mirror makes prepare() fail closed before any commit.
        conflicting = copy.deepcopy(state)
        conflicting["final_verification"]["critical_claims"] = copy.deepcopy(CLAIMS[:2])
        conflicting["final_verification"]["claims_hash"] = \
            self.m.canonical_claims_hash(CLAIMS[:2])
        self._write_state(conflicting)
        with self.assertRaisesRegex(RuntimeError, "FV dispatch preparation"):
            self._commit_direct(self._result(
                conflicting, decision="FINAL_VERIFICATION",
                resulting="WAITING_EXECUTOR", executor_task=task))
        after = self._read_state()
        self.assertEqual(after["human_review_resume"]["status"],
                         "PENDING_SUPERVISOR_REVIEW")

    # --- 3. FINAL_ACCEPTANCE on the resume turn -------------------------------

    def test_final_acceptance_rejects_superseded_pass_fail_closed(self):
        # F-007: the fv record is Runtime-owned; the acceptance runs the
        # ordinary terminal predicate on the Runtime-stamped record. The
        # fixture's default fork has a newer ordinary receipt after the FV
        # PASS, so the stamp still fails the freshness check fail-closed.
        state, _ = self._pending_state()
        with self.assertRaisesRegex(
                RuntimeError, "COMPLETE rejected by Final Verification Gate"):
            self._commit_direct(self._result(
                state, decision="FINAL_ACCEPTANCE", resulting="COMPLETE"))
        after = self._read_state()
        self.assertEqual(after["status"], "SUPERVISOR_TURN")
        self.assertEqual(after["human_review_resume"]["status"],
                         "PENDING_SUPERVISOR_REVIEW")
        # The model can no longer supply or damage the fv record at all.
        damaging = self._result(
            state, decision="FINAL_ACCEPTANCE", resulting="COMPLETE",
            patch_extra={"final_verification": {
                **copy.deepcopy(state["final_verification"]),
                "status": "PASS"}})
        with self.assertRaisesRegex(RuntimeError, "Runtime-owned"):
            self._commit_direct(damaging)

    def test_final_acceptance_commits_on_fresh_pass(self):
        state, _ = self._pending_state()
        # Rewire the runtime to the fresh-PASS shape: the FV receipt is the
        # freshest consumed receipt, so the ordinary terminal predicate passes.
        runtime = self._read_runtime()
        runtime.update(
            last_consumed_message_id=self.fv_message_id,
            last_consumed_nonce="fv-fixture-nonce",
            last_consumed_brief_sha256=self.fv_receipt_sha,
            last_dispatched_message_id=self.fv_message_id,
            last_dispatched_nonce="fv-fixture-nonce",
        )
        self._write_runtime(runtime)
        self.runtime.update(runtime)
        # F-007: no fv in the patch — the Runtime stamps the acceptance itself
        # from its own verified binding, producing the complete fv shape.
        committed = self._commit_direct(self._result(
            state, decision="FINAL_ACCEPTANCE", resulting="COMPLETE"))
        self.assertEqual(committed["status"], "COMPLETE")
        fv = committed["final_verification"]
        self.assertEqual(fv["status"], "PASS")
        self.assertIs(fv["required"], True)
        self.assertEqual(fv["claims_hash"], self.claims_hash)
        self.assertEqual(fv["verification_message_id"], self.fv_message_id)
        self.assertTrue(fv["verified_at"])
        meta = committed["human_review_resume"]
        self.assertEqual(meta["status"], "CONSUMED")
        self.assertEqual(meta["resulting_supervisor_decision"], "FINAL_ACCEPTANCE")
        self.assertEqual(len(committed["human_decision_consumption_ledger"]), 1)

    # --- 4. gate provenance at the authorization boundary ---------------------

    def test_registration_rejects_unprepared_gate_and_accepts_prepared(self):
        state, _ = self._pending_state()
        candidate = copy.deepcopy(state)
        candidate["status"] = "WAITING_EXECUTOR"
        candidate["last_supervisor_decision"] = {
            "decision": "FINAL_VERIFICATION", "scope": "fixture",
            "reason": "fixture FV decision",
        }
        task = self._hand_authored_gate(self._fv_request_task())
        candidate["current_task"] = {
            key: task[key] for key in self.m.IDENTITY_KEYS}
        candidate["next_message_id"] = task["MESSAGE_ID"] + 1
        self._write_state(candidate)
        self.m.atomic_write(
            self.m.TO_ZCODE,
            "```json\n" + json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n")
        runtime = self._read_runtime()
        with self.assertRaisesRegex(RuntimeError, "not Runtime-prepared"):
            self.m.validate_dispatch_payload(runtime, candidate, task)
        with self.assertRaisesRegex(RuntimeError, "not Runtime-prepared"):
            self.m.register_dispatched_task(runtime, candidate)
        contract.record_prepared_identity(
            self.m.ROOT, runtime,
            {key: task[key] for key in self.m.IDENTITY_KEYS},
            self.claims_hash, self.m.stamp())
        self.m.validate_dispatch_payload(runtime, candidate, task)

    def test_save_runtime_union_keeps_prepared_identities(self):
        state, _ = self._pending_state()
        task = self._fv_request_task()
        self._commit_direct(self._result(
            state, decision="FINAL_VERIFICATION",
            resulting="WAITING_EXECUTOR", executor_task=task))
        recorded = self._read_runtime()["final_verification_prepared_identities"]
        self.assertTrue(recorded)
        # A stale orchestrator snapshot (no provenance in memory) must not erase
        # the durable record.
        stale = {key: value for key, value in self._read_runtime().items()
                 if key != "final_verification_prepared_identities"}
        self.m.save_runtime(stale)
        after = self._read_runtime().get("final_verification_prepared_identities")
        self.assertEqual(after, recorded)

    # --- F-006: an invalid resume result is bounded, never fatal -------------

    def _resume_turn_codex(self, results):
        calls = {"n": 0}

        def run(cmd, **_kwargs):
            index = calls["n"]
            calls["n"] += 1
            output_path = Path(cmd[cmd.index("-o") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            payload = results[min(index, len(results) - 1)]
            output_path.write_text(json.dumps(payload, ensure_ascii=False),
                                   encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0)

        return run

    def test_invalid_resume_result_is_bounded_not_fatal(self):
        # F-006: a model-produced Human Decision result claiming
        # WAITING_EXECUTOR without an executor_task used to raise out of
        # invoke_codex and kill the orchestrator. It must instead be accounted
        # under the durable decision budget: first attempt retries, exhausting
        # the budget finalizes HUMAN_REVIEW and voids the pending receipt.
        state, receipt = self._pending_state()
        invalid = self._result(state, decision="FINAL_VERIFICATION",
                               resulting="WAITING_EXECUTOR", executor_task=None)
        event = {"type": "HUMAN_DECISION_RESUME", "project_id": self.project_id}
        self.m.acquire_lock()
        try:
            for attempt in (1, 2):
                with patch.object(self.m, "find_codex", return_value="codex-fixture"),                         patch.object(self.m.subprocess, "run",
                                     side_effect=self._resume_turn_codex([invalid])):
                    self.m.invoke_codex(self.runtime, "HUMAN_DECISION_RESUME", event)
                if attempt == 1:
                    self.assertEqual(self._read_state()["status"], "SUPERVISOR_TURN")
                    pending = self._read_runtime()["pending_supervisor_event"]
                    self.assertEqual(pending["decision_attempts"], 1)
                    self.assertFalse(pending.get("retry_exhausted", False))
        finally:
            self.m.release_lock()
        after = self._read_state()
        self.assertEqual(after["status"], "HUMAN_REVIEW")
        meta = after["human_review_resume"]
        self.assertEqual(meta["status"], "EXHAUSTED")
        self.assertEqual(meta["receipt_id"], receipt["receipt_id"])
        self.assertIn("WAITING_EXECUTOR requires one Executor task",
                      meta["exhaustion_reason"])
        self.assertEqual(after.get("human_decision_consumption_ledger") or [], [])
        pending = self._read_runtime()["pending_supervisor_event"]
        self.assertEqual(pending["decision_attempts"], 2)
        self.assertTrue(pending["retry_exhausted"])

    # --- F-007: the acceptance fv record is Runtime-owned ---------------------

    def test_final_acceptance_stamp_is_runtime_owned(self):
        # F-007: the model's project_state_patch used to replace the whole
        # final_verification dict, dropping required/policy_version/claims_hash.
        # That silently disabled the terminal gate (gate_enforced reads
        # required) and damaged terminal presentation. The patch is now
        # refused, and the Runtime stamps the acceptance itself from its own
        # verified binding.
        state, receipt = self._pending_state()
        runtime = self._read_runtime()
        runtime["last_consumed_message_id"] = self.fv_message_id
        runtime["last_consumed_brief_sha256"] = self.fv_receipt_sha
        self._write_runtime(runtime)
        self.runtime.update(runtime)

        damaging = self._result(
            state, decision="FINAL_ACCEPTANCE", resulting="COMPLETE",
            patch_extra={"final_verification": {
                "status": "PASS",
                "verification_message_id": self.fv_message_id,
                "verification_receipt_sha256": self.fv_receipt_sha}})
        with self.assertRaisesRegex(RuntimeError, "Runtime-owned"):
            self._commit_direct(damaging)
        self.assertEqual(self._read_state()["final_verification"]["status"], "PENDING")

        good = self._result(state, decision="FINAL_ACCEPTANCE", resulting="COMPLETE")
        committed = self._commit_direct(good)
        fv = committed["final_verification"]
        self.assertEqual(fv["status"], "PASS")
        self.assertIs(fv["required"], True)
        self.assertEqual(fv["policy_version"], self.base_state["final_verification"]["policy_version"])
        self.assertEqual(fv["claims_hash"], self.claims_hash)
        self.assertEqual(fv["verification_message_id"], self.fv_message_id)
        self.assertEqual(fv["verification_receipt_sha256"], self.fv_receipt_sha)
        self.assertTrue(fv["verified_at"])
        fresh_runtime = self.m.load_runtime()
        self.assertEqual(
            self.m.final_verification_terminal_check(fresh_runtime, committed),
            (True, "PASS"))

    # --- 5. exhaustion recovery ------------------------------------------------

    def _exhaust_resume_event(self, error="fixture invalid resume result"):
        runtime = self._read_runtime()
        runtime["pending_supervisor_event"] = {
            "reason": "HUMAN_DECISION_RESUME",
            "event": {"type": "HUMAN_DECISION_RESUME", "project_id": self.project_id},
            "recorded_at": self.m.stamp(),
            "decision_attempts": self.m.MAX_SUPERVISOR_DECISION_ATTEMPTS,
            "retry_exhausted": False,
        }
        self._write_runtime(runtime)
        self.m.acquire_lock()
        try:
            self.m.exhaust_supervisor_retry_budget(
                runtime, "HUMAN_DECISION_RESUME",
                runtime["pending_supervisor_event"]["event"], error)
        finally:
            self.m.release_lock()

    def test_exhaustion_voids_pending_receipt_into_recoverable_state(self):
        state, receipt = self._pending_state()
        self._exhaust_resume_event()
        after = self._read_state()
        self.assertEqual(after["status"], "HUMAN_REVIEW")
        meta = after["human_review_resume"]
        self.assertEqual(meta["status"], "EXHAUSTED")
        self.assertEqual(meta["receipt_id"], receipt["receipt_id"])
        self.assertIn("fixture invalid resume result", meta["exhaustion_reason"])
        # The voided receipt never entered the consumption ledger.
        self.assertEqual(after.get("human_decision_consumption_ledger") or [], [])
        # It is not injectable and does not raise on lifecycle validation.
        self.assertIsNone(
            self.m.load_verified_human_decision_for_supervisor(after))
        self.runtime.update(self._read_runtime())
        # A genuinely new Human Review cycle is preparable (no stranded receipt).
        second_receipt = r.prepare_receipt(
            self.m, project_id=self.project_id,
            decision_payload=self.payload,
            receipt_out=self.root / "second-human-decision.json")
        self.assertNotEqual(second_receipt["receipt_id"], receipt["receipt_id"])
        r.apply_receipt(
            self.m, receipt_path=self.root / "second-human-decision.json")
        resumed = self._read_state()
        self.assertEqual(resumed["status"], "SUPERVISOR_TURN")
        self.assertEqual(resumed["human_review_resume"]["status"],
                         "PENDING_SUPERVISOR_REVIEW")
        # The authorized transition is now expressible on the fresh cycle.
        task = self._fv_request_task()
        committed = self._commit_direct(self._result(
            resumed, decision="FINAL_VERIFICATION",
            resulting="WAITING_EXECUTOR", executor_task=task))
        self.assertEqual(committed["status"], "WAITING_EXECUTOR")

    def test_exhausted_receipt_cannot_be_reapplied(self):
        state, receipt = self._pending_state()
        self._exhaust_resume_event()
        with self.assertRaises(r.ResumeError) as caught:
            r.apply_receipt(self.m, receipt_path=self.receipt_path)
        self.assertTrue(any(
            word in str(caught.exception).lower()
            for word in ("stale", "duplicate")))

    def test_non_resume_exhaustion_leaves_receipt_pending(self):
        state, _ = self._pending_state()
        runtime = self._read_runtime()
        runtime["pending_supervisor_event"] = {
            "reason": "SUPERVISOR_TURN",
            "event": None,
            "recorded_at": self.m.stamp(),
            "decision_attempts": self.m.MAX_SUPERVISOR_DECISION_ATTEMPTS,
            "retry_exhausted": False,
        }
        self._write_runtime(runtime)
        self.m.acquire_lock()
        try:
            self.m.exhaust_supervisor_retry_budget(
                runtime, "SUPERVISOR_TURN", None, "fixture ordinary exhaustion")
        finally:
            self.m.release_lock()
        after = self._read_state()
        self.assertEqual(after["status"], "HUMAN_REVIEW")
        self.assertEqual(after["human_review_resume"]["status"],
                         "PENDING_SUPERVISOR_REVIEW")

    def test_exhausted_meta_shape_is_validated(self):
        state, _ = self._pending_state()
        self._exhaust_resume_event()
        good = self._read_state()
        self.assertIsNone(
            self.m.load_verified_human_decision_for_supervisor(good))
        for mutate in (
            lambda meta: meta.pop("exhausted_at"),
            lambda meta: meta.update(exhausted_at="not-a-timestamp"),
            lambda meta: meta.update(exhaustion_reason="  "),
            lambda meta: meta.update(unknown_field=1),
        ):
            with self.subTest(mutate=mutate.__name__ if hasattr(mutate, "__name__") else ""):
                bad = copy.deepcopy(good)
                mutate(bad["human_review_resume"])
                self._write_state(bad)
                with self.assertRaises(RuntimeError):
                    self.m.load_verified_human_decision_for_supervisor(bad)
        self._write_state(good)


if __name__ == "__main__":
    unittest.main(verbosity=2)
