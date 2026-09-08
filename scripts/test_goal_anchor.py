"""GOAL-ANCHOR-V1 targeted deterministic regression tests.

Covers: bootstrap binding with byte-exact SHA-256; repeated per-Supervisor-turn
re-read/verification; unchanged-goal progression; changed / missing / unreadable /
malformed / path-escaping / legacy-unbound / rebind failure into the bounded
HUMAN_REVIEW state (current_task null, no dispatch authorization); Executor
receipt non-override; required concise goal_alignment records on committed
decision paths (including FINAL_VERIFICATION/COMPLETE guardrails in the prompt
contract); the bounded migration tool; and startup re-authorization gating.
Legacy single-project mode keeps its exact prior behavior (no goal anchoring).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
RUNTIME_ROOT = SCRIPTS.parent
if str(RUNTIME_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNTIME_ROOT))

MODULE_PATH = RUNTIME_ROOT / "orchestrator.py"
spec = importlib.util.spec_from_file_location("goal_anchor_orchestrator", MODULE_PATH)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)

GOAL_TEXT = "# Fixture Goal\n\nSatisfy the fixture success criteria.\n"


def alignment_record(method="CONTINUE"):
    return {
        "original_objective": "Satisfy the fixture goal.",
        "unmet_criteria": "One criterion still unmet.",
        "latest_result": "Latest stage advanced one criterion.",
        "next_action_alignment": "The next stage closes the last criterion.",
        "scope_drift": "None.",
        "method": method,
    }


class GoalAnchorFixture(unittest.TestCase):
    """Sandbox runtime root with one isolated active project (post-GOAL-ANCHOR)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="goal-anchor-")
        self.root = Path(self.temp.name)
        self.m = self._load_orchestrator(self.root)
        for relative in ("control", "logs", "handoff/archive", "reports"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.project_id = "goal-anchor-fixture-001"
        self.project_root = self.root / "projects" / self.project_id
        self.project_root.mkdir(parents=True)
        self.goal_file = self.project_root / "PROJECT_GOAL.md"
        self.goal_file.write_text(GOAL_TEXT, encoding="utf-8")
        (self.project_root / "RESEARCH_STATE.md").write_text("# Memory\n", encoding="utf-8")
        self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE, {
            "schema_version": 1,
            "project_id": self.project_id,
            "project_root": f"projects/{self.project_id}",
        })
        self.binding = {
            "schema_version": 1,
            "goal_path": "PROJECT_GOAL.md",
            "goal_sha256": hashlib.sha256(self.goal_file.read_bytes()).hexdigest(),
            "bound_at": "2026-09-05T00:00:00+00:00",
            "provenance": "bootstrap",
        }
        self.state = {
            "schema_version": 4,
            "project_id": self.project_id,
            "project_type": "GENERAL",
            "profile": "GENERAL",
            "status": "SUPERVISOR_TURN",
            "phase": "GENERAL",
            "created_at": "2026-09-05T00:00:00+00:00",
            "started_at": "2026-09-05T00:00:00+00:00",
            "updated_at": self.m.stamp(),
            "goal_file": "PROJECT_GOAL.md",
            "goal_anchor": dict(self.binding),
            "current_task": None,
            "next_message_id": 700100,
            "decision_history": [],
        }
        self.m.atomic_json(self.project_root / "project_state.json", self.state)
        self.runtime = {
            "schema_version": 2,
            "last_consumed_message_id": 602,
            "last_dispatched_message_id": None,
            "last_dispatched_nonce": None,
        }
        self.m.atomic_json(self.m.RUNTIME_STATE, self.runtime)
        self.active = self.m.activate_project_scope()
        self.m.DESKTOP_NOTIFICATIONS_ENABLED = False
        self.m.USER_NOTIFICATION_CONSOLE_ENABLED = False

    def tearDown(self):
        self.temp.cleanup()

    def _load_orchestrator(self, root: Path):
        module_spec = importlib.util.spec_from_file_location(
            f"goal_anchor_orchestrator_{abs(hash(str(root)))}", MODULE_PATH)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        module.ROOT = root
        module.CONTROL = root / "control"
        module.LOGS = root / "logs"
        module.HANDOFF_ARCHIVE = root / "handoff" / "archive"
        module.REPORTS = root / "reports"
        module.PROJECT_STATE = module.CONTROL / "project_state.json"
        module.RUNTIME_STATE = module.CONTROL / "orchestrator_runtime.json"
        module.SUPERVISOR_RULES = module.CONTROL / "CODEX_SUPERVISOR_RUNTIME.md"
        module.RESEARCH_STATE = root / "RESEARCH_STATE.md"
        module.COMMERCIAL_GOAL = module.CONTROL / "CROSS_BORDER_GOAL.md"
        module.TO_ZCODE = root / "TO_ZCODE.md"
        module.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
        module.ZCODE_DONE = root / "ZCODE_DONE.flag"
        module.ZCODE_LAST_PROCESSED = root / "ZCODE_LAST_PROCESSED.txt"
        module.STOP_FLAG = module.CONTROL / "STOP"
        module.HUMAN_REVIEW_FLAG = module.CONTROL / "HUMAN_REVIEW"
        module.LOCK_FILE = module.CONTROL / ".orchestrator.lock"
        module.CODEX_LAST_OUTPUT = root / "CODEX_LAST_OUTPUT.txt"
        module.USER_ATTENTION = module.CONTROL / "USER_ATTENTION.json"
        module.USER_STATUS_REPORT = module.REPORTS / "USER_STATUS.md"
        module.ACTIVE_PROJECT_FILE = module.CONTROL / "ACTIVE_PROJECT.json"
        return module

    def write_state(self, state):
        self.m.atomic_json(self.m.PROJECT_STATE, state)

    def read_state(self):
        return self.m.read_project_state()

    def read_runtime_file(self):
        return json.loads(self.m.RUNTIME_STATE.read_text(encoding="utf-8-sig"))

    def tamper_goal(self):
        self.goal_file.write_text("# Fixture Goal\n\nTAMpered success criteria.\n", encoding="utf-8")


class BootstrapBindingTests(GoalAnchorFixture):
    """Run the real start_project bootstrap in a sandbox runtime copy."""

    def setUp(self):
        super().setUp()
        self.temp.cleanup()  # bootstrap builds its own sandbox
        self.temp = tempfile.TemporaryDirectory(prefix="goal-anchor-bootstrap-")
        self.root = Path(self.temp.name)
        shutil.copy(RUNTIME_ROOT / "orchestrator.py", self.root / "orchestrator.py")
        (self.root / "scripts").mkdir()
        shutil.copy(RUNTIME_ROOT / "scripts" / "executor_completion.py",
                    self.root / "scripts" / "executor_completion.py")
        shutil.copy(RUNTIME_ROOT / "scripts" / "start_project.py", self.root / "scripts" / "start_project.py")
        shutil.copytree(RUNTIME_ROOT / "profiles", self.root / "profiles")

    def test_bootstrap_binds_canonical_goal_byte_exact(self):
        result = subprocess.run(
            [sys.executable, str(self.root / "scripts" / "start_project.py"),
             "--root", str(self.root), "--project-id", "boot-001",
             "--project-type", "GENERAL", "--goal", "Bootstrap goal body."],
            cwd=str(self.root), capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        created = json.loads(result.stdout.strip().splitlines()[-1])
        project_root = self.root / "projects" / "boot-001"
        goal_bytes = (project_root / "PROJECT_GOAL.md").read_bytes()
        expected_sha = hashlib.sha256(goal_bytes).hexdigest()
        self.assertEqual(created["goal_sha256"], expected_sha)

        state = json.loads((project_root / "project_state.json").read_text(encoding="utf-8-sig"))
        binding = state["goal_anchor"]
        self.assertEqual(set(binding), {"schema_version", "goal_path", "goal_sha256", "bound_at", "provenance"})
        self.assertEqual(binding["schema_version"], 1)
        self.assertEqual(binding["goal_path"], "PROJECT_GOAL.md")
        self.assertEqual(binding["goal_sha256"], expected_sha)
        self.assertEqual(binding["provenance"], "bootstrap")
        # timezone-aware bound_at
        from datetime import datetime
        parsed = datetime.fromisoformat(binding["bound_at"])
        self.assertIsNotNone(parsed.tzinfo)
        # the binding matches what the Runtime verifier accepts, byte-exact
        m = self._load_orchestrator(self.root)
        m.activate_project_scope()
        verified = m.verify_goal_anchor(json.loads(
            (project_root / "project_state.json").read_text(encoding="utf-8-sig")))
        self.assertEqual(verified["goal_sha256"], expected_sha)
        self.assertEqual(verified["goal_path"], project_root / "PROJECT_GOAL.md")


class PerTurnVerificationTests(GoalAnchorFixture):
    def test_unchanged_goal_progresses_and_runtime_records_binding_once(self):
        self.assertTrue(self.m.goal_anchor_gate(self.runtime, self.read_state()))
        record = self.read_runtime_file()["goal_anchor_binding"]
        self.assertEqual(record["goal_sha256"], self.binding["goal_sha256"])
        self.assertEqual(record["project_id"], self.project_id)
        self.assertEqual(record["provenance"], "bootstrap")
        # Second verified turn must not duplicate or mutate the Runtime-owned record.
        record_at = self.read_runtime_file()["goal_anchor_binding"]
        self.assertTrue(self.m.goal_anchor_gate(self.runtime, self.read_state()))
        self.assertEqual(self.read_runtime_file()["goal_anchor_binding"], record_at)
        self.assertEqual(self.read_state()["status"], "SUPERVISOR_TURN")

    def test_every_turn_rereads_goal_and_detects_changed_bytes(self):
        self.assertTrue(self.m.goal_anchor_gate(self.runtime, self.read_state()))
        self.tamper_goal()
        self.assertFalse(self.m.goal_anchor_gate(self.runtime, self.m.read_project_state()))
        state = self.read_state()
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertIsNone(state["current_task"])
        self.assertEqual(state["goal_anchor_failure"]["reason"], "GOAL_ANCHOR_HASH_MISMATCH")
        # No silent rebind: the stored binding is untouched.
        self.assertEqual(state["goal_anchor"], self.binding)
        self.assertEqual(self.read_runtime_file()["status"], "HUMAN_REVIEW")
        attention = json.loads(self.m.USER_ATTENTION.read_text(encoding="utf-8-sig"))
        self.assertIn("GOAL-ANCHOR-V1", attention["reason"])

    def test_missing_goal_fails_closed(self):
        self.goal_file.unlink()
        self.assertFalse(self.m.goal_anchor_gate(self.runtime, self.read_state()))
        state = self.read_state()
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertIsNone(state["current_task"])
        self.assertEqual(state["goal_anchor_failure"]["reason"], "GOAL_ANCHOR_FILE_MISSING")
        self.assertIn("goal_anchor", state)

    def test_unreadable_goal_fails_closed(self):
        original = Path.read_bytes

        def broken_read_bytes(path_self, *args, **kwargs):
            if path_self == self.goal_file:
                raise PermissionError(13, "fixture denied")
            return original(path_self, *args, **kwargs)

        with patch.object(Path, "read_bytes", broken_read_bytes):
            self.assertFalse(self.m.goal_anchor_gate(self.runtime, self.read_state()))
        state = self.read_state()
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertEqual(state["goal_anchor_failure"]["reason"], "GOAL_ANCHOR_UNREADABLE")

    def test_legacy_unbound_project_fails_closed_without_trust_upgrade(self):
        state = self.read_state()
        del state["goal_anchor"]
        self.write_state(state)
        self.assertFalse(self.m.goal_anchor_gate(self.runtime, self.m.read_project_state()))
        state = self.read_state()
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertEqual(state["goal_anchor_failure"]["reason"], "GOAL_ANCHOR_MISSING")
        self.assertIsNone(state.get("goal_anchor"))  # no binding was fabricated

    def test_malformed_and_path_escaping_bindings_fail_closed(self):
        cases = [
            {"schema_version": 2, **{k: v for k, v in self.binding.items() if k != "schema_version"}},
            {**self.binding, "goal_sha256": "nothex"},
            {**self.binding, "bound_at": "2026-09-05T00:00:00"},
            {**self.binding, "provenance": "supervisor"},
            {**self.binding, "extra": True},
            {**self.binding, "goal_path": "../outside.md"},
            {**self.binding, "goal_path": "C:\\evil.md"},
            {**self.binding, "goal_path": "OTHER.md"},
            {k: v for k, v in self.binding.items() if k != "bound_at"},
        ]
        for bad_binding in cases:
            with self.subTest(binding=bad_binding):
                state = self.read_state()
                state["goal_anchor"] = bad_binding
                state["status"] = "SUPERVISOR_TURN"
                self.write_state(state)
                self.assertFalse(self.m.goal_anchor_gate(self.runtime, self.m.read_project_state()))
                fresh = self.read_state()
                self.assertEqual(fresh["status"], "HUMAN_REVIEW")
                self.assertIsNone(fresh["current_task"])
                self.assertIn(fresh["goal_anchor_failure"]["reason"], {
                    "GOAL_ANCHOR_MALFORMED", "GOAL_ANCHOR_PATH_ESCAPE"})

    def test_silent_self_consistent_rebind_is_rejected(self):
        self.assertTrue(self.m.goal_anchor_gate(self.runtime, self.read_state()))
        # Supervisor-style tamper: rewrite the goal bytes AND the state binding so
        # they are self-consistent again. The Runtime-owned copy still disagrees.
        self.tamper_goal()
        state = self.read_state()
        state["goal_anchor"] = {**self.binding, "goal_sha256":
                                hashlib.sha256(self.goal_file.read_bytes()).hexdigest()}
        self.write_state(state)
        self.assertFalse(self.m.goal_anchor_gate(self.runtime, self.m.read_project_state()))
        fresh = self.read_state()
        self.assertEqual(fresh["status"], "HUMAN_REVIEW")
        self.assertEqual(fresh["goal_anchor_failure"]["reason"], "GOAL_ANCHOR_REBIND_REJECTED")


class DispatchAuthorizationTests(GoalAnchorFixture):
    def setUp(self):
        super().setUp()
        self.task = {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": 700100,
            "TASK_ID": "fixture-stage",
            "STAGE_ID": "fixture-stage-1",
            "ATTEMPT": 1,
            "NONCE": "nonce-goal-anchor-1",
            "ISSUED_AT": self.m.stamp(),
            "OBJECTIVE": "fixture",
            "OUTPUTS": ["out.txt"],
            "EXECUTOR_PROTOCOL": ["python scripts/executor_claim.py acquire ..."],
        }
        state = self.read_state()
        state["status"] = "WAITING_EXECUTOR"
        state["current_task"] = {k: self.task[k] for k in
                                 ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")}
        self.write_state(state)
        self.m.atomic_write(self.m.TO_ZCODE, "identity\n\n```json\n"
                            + json.dumps(self.task) + "\n```\n")

    def test_no_dispatch_authorization_from_tampered_goal(self):
        self.tamper_goal()

        def codex_must_not_run(*args, **kwargs):
            raise AssertionError("Codex must not be invoked from an unverified goal state")

        with patch.object(self.m, "find_codex", codex_must_not_run):
            self.m.invoke_codex(self.runtime, "SUPERVISOR_TURN", {"source": "fixture"})
        state = self.read_state()
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertIsNone(state["current_task"])
        # register_dispatched_task never ran: no authorization record exists.
        self.assertIsNone(self.read_runtime_file().get("authorized_dispatch"))

    def test_startup_reauthorization_blocked_by_tampered_goal(self):
        self.tamper_goal()

        def codex_must_not_run(*args, **kwargs):
            raise AssertionError("Codex must not be invoked from an unverified goal state")

        runtime = self.read_runtime_file()
        runtime["last_dispatched_message_id"] = 700100
        runtime["last_dispatched_nonce"] = "nonce-goal-anchor-1"
        self.m.atomic_json(self.m.RUNTIME_STATE, runtime)
        with patch.object(self.m, "find_codex", codex_must_not_run):
            exit_code = self.m.main()
        self.assertEqual(exit_code, 3)
        state = self.read_state()
        self.assertEqual(state["status"], "HUMAN_REVIEW")
        self.assertIsNone(state["current_task"])
        self.assertIsNone(self.read_runtime_file().get("authorized_dispatch"))

    def test_startup_reauthorization_proceeds_with_verified_goal(self):
        class WaitOnceView:
            def __getattr__(self, name):
                return lambda *a, **k: None

            def waiting_tick(self, *a, **k):
                raise KeyboardInterrupt  # exit main()'s poll loop right after re-auth

            def waiting_stop(self):
                return None

        runtime = self.read_runtime_file()
        runtime["last_dispatched_message_id"] = 700100
        runtime["last_dispatched_nonce"] = "nonce-goal-anchor-1"
        self.m.atomic_json(self.m.RUNTIME_STATE, runtime)
        with patch.object(self.m, "_view", lambda: WaitOnceView()):
            # waiting_tick raises KeyboardInterrupt; main() catches it and exits 130.
            self.assertEqual(self.m.main(), 130)
        authorized = self.read_runtime_file()["authorized_dispatch"]
        self.assertEqual(authorized["MESSAGE_ID"], 700100)
        self.assertEqual(authorized["TO_ZCODE_SHA256"],
                         hashlib.sha256(self.m.TO_ZCODE.read_bytes()).hexdigest())
        self.assertEqual(self.read_state()["status"], "WAITING_EXECUTOR")

    def test_receipt_fields_cannot_override_goal_fail_closed(self):
        self.tamper_goal()
        runtime = self.read_runtime_file()
        # A "mechanically PASS" Final Verification receipt is on record…
        runtime["last_final_verification_message_id"] = 700099
        runtime["last_final_verification_receipt_sha256"] = "f" * 64
        runtime["last_final_verification_mechanical_pass"] = True
        runtime["last_final_verification_overall_status"] = "PASS"
        self.m.atomic_json(self.m.RUNTIME_STATE, runtime)
        state = self.read_state()
        state["final_verification"] = {
            "policy_version": 1, "required": True, "status": "PASS",
            "critical_claims": [], "claims_hash": None,
        }
        self.write_state(state)
        # …but the unverified goal still fails the turn closed; no decision,
        # dispatch, or COMPLETE progression can occur, and the goal is not rebound.
        self.assertFalse(self.m.goal_anchor_gate(self.m.load_runtime(), self.m.read_project_state()))
        fresh = self.read_state()
        self.assertEqual(fresh["status"], "HUMAN_REVIEW")
        self.assertEqual(fresh["goal_anchor_failure"]["reason"], "GOAL_ANCHOR_HASH_MISMATCH")
        self.assertEqual(fresh["goal_anchor"], self.binding)


class AlignmentRecordTests(GoalAnchorFixture):
    def test_committed_decision_requires_concise_alignment_record(self):
        base = {
            "decision": "CONTINUE",
            "at": self.m.stamp(),
            "reason": "Next bounded stage.",
            "scope": "fixture",
            "message_id": None,
            "task_id": None,
            "stage_id": None,
            "goal_alignment": alignment_record(),
        }
        entry = self.m._normalized_human_supervisor_decision(base)
        self.assertEqual(entry["goal_alignment"], alignment_record())

        missing = {k: v for k, v in base.items() if k != "goal_alignment"}
        with self.assertRaises(RuntimeError):
            self.m._normalized_human_supervisor_decision(missing)

        bad_records = [
            {**alignment_record(), "extra": "x"},
            {k: v for k, v in alignment_record().items() if k != "scope_drift"},
            {**alignment_record(), "method": ""},
            {**alignment_record(), "method": None},
            {**alignment_record(), "original_objective": "x" * 2001},
            "not an object",
        ]
        for bad in bad_records:
            with self.subTest(bad=bad), self.assertRaises(RuntimeError):
                self.m._normalized_human_supervisor_decision({**base, "goal_alignment": bad})

    def test_prompt_contract_contains_goal_anchor_and_alignment_fields(self):
        prompt = self.m.build_codex_prompt(
            "SUPERVISOR_TURN", {"source": "fixture"}, self.read_state())
        self.assertIn("=== GOAL ANCHOR (GOAL-ANCHOR-V1) ===", prompt)
        self.assertIn(self.binding["goal_sha256"], prompt)
        for field in self.m.GOAL_ALIGNMENT_FIELDS:
            self.assertIn(field, prompt)
        self.assertIn("goal_alignment", prompt)
        # Goal checks on the Final Verification / COMPLETE paths.
        self.assertIn("FINAL_VERIFICATION", prompt)
        self.assertIn("FINAL_ACCEPTANCE", prompt)
        self.assertIn("COMPLETE", prompt)
        self.assertIn("Recent local Executor success alone", prompt)
        self.assertIn("STOP", prompt)
        self.assertIn("HUMAN_REVIEW", prompt)

    def test_prompt_fails_closed_when_goal_cannot_be_verified(self):
        self.tamper_goal()
        prompt = self.m.build_codex_prompt(
            "SUPERVISOR_TURN", {"source": "fixture"}, self.read_state())
        self.assertIn("GOAL ANCHOR VERIFICATION FAILED", prompt)
        self.assertIn("no decision or dispatch is permitted", prompt)

    def test_human_decision_output_contract_requires_goal_alignment(self):
        fake_receipt = {
            "project_id": self.project_id,
            "receipt_id": "human-decision-" + "a" * 32,
            "receipt_sha256": "e" * 64,
            "previous_status": "HUMAN_REVIEW",
        }
        with patch.object(self.m, "load_verified_human_decision_for_supervisor",
                          lambda state: fake_receipt):
            prompt = self.m.build_codex_prompt(
                "HUMAN_DECISION_RESUME",
                {"type": "HUMAN_DECISION_RESUME", "receipt_id": fake_receipt["receipt_id"]},
                self.read_state(),
            )
        self.assertIn("goal_alignment is required", prompt)
        self.assertIn("original_objective, unmet_criteria, latest_result", prompt)

    def test_legacy_mode_prompt_has_no_goal_anchor_block(self):
        state = self.read_state()
        legacy_state = dict(state)
        legacy_state["goal_file"] = None  # legacy COMMERCIAL_GOAL resolution path
        with patch.object(self.m, "ACTIVE_PROJECT", None):
            prompt = self.m.build_codex_prompt("SUPERVISOR_TURN", {}, legacy_state)
            self.assertNotIn("GOAL ANCHOR", prompt)
            self.assertIsNone(self.m.verify_goal_anchor(state))
            # Legacy gate is a no-op and never mutates lifecycle state.
            self.assertTrue(self.m.goal_anchor_gate(self.runtime, state))
        self.assertEqual(state["status"], "SUPERVISOR_TURN")


class MigrationToolTests(GoalAnchorFixture):
    """Run scripts/migrate_goal_anchor.py as a subprocess in a sandbox runtime copy."""

    def setUp(self):
        super().setUp()
        self.temp.cleanup()
        self.temp = tempfile.TemporaryDirectory(prefix="goal-anchor-migrate-")
        self.root = Path(self.temp.name)
        shutil.copy(RUNTIME_ROOT / "orchestrator.py", self.root / "orchestrator.py")
        (self.root / "scripts").mkdir()
        shutil.copy(RUNTIME_ROOT / "scripts" / "executor_completion.py",
                    self.root / "scripts" / "executor_completion.py")
        shutil.copytree(RUNTIME_ROOT / "profiles", self.root / "profiles")
        for relative in ("control", "logs", "handoff/archive", "reports"):
            (self.root / relative).mkdir(parents=True, exist_ok=True)
        self.project_id = "legacy-unbound-001"
        self.project_root = self.root / "projects" / self.project_id
        self.project_root.mkdir(parents=True)
        self.goal_file = self.project_root / "PROJECT_GOAL.md"
        self.goal_file.write_text(GOAL_TEXT, encoding="utf-8")
        (self.project_root / "RESEARCH_STATE.md").write_text("# Memory\n", encoding="utf-8")
        (self.root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps({
            "schema_version": 1,
            "project_id": self.project_id,
            "project_root": f"projects/{self.project_id}",
        }), encoding="utf-8")
        # legacy-unbound project paused by the GOAL-ANCHOR fail-closed gate
        state = {
            "schema_version": 4,
            "project_id": self.project_id,
            "status": "HUMAN_REVIEW",
            "goal_file": "PROJECT_GOAL.md",
            "current_task": None,
            "goal_anchor_failure": {"reason": "GOAL_ANCHOR_MISSING", "detail": "legacy-unbound"},
        }
        (self.project_root / "project_state.json").write_text(
            json.dumps(state, indent=2) + "\n", encoding="utf-8")

    def run_migration(self):
        import os
        env = dict(os.environ)
        env["ORCHESTRATOR_CONSOLE"] = "off"  # keep stdout to the JSON event only
        return subprocess.run(
            [sys.executable, str(RUNTIME_ROOT / "scripts" / "migrate_goal_anchor.py"),
             "--root", str(self.root)],
            cwd=str(self.root), capture_output=True, text=True, env=env,
        )

    def test_migration_binds_once_and_verifies(self):
        result = self.run_migration()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        event = json.loads(result.stdout.strip().splitlines()[0])
        expected_sha = hashlib.sha256(self.goal_file.read_bytes()).hexdigest()
        self.assertEqual(event["provenance"], "migration")
        self.assertEqual(event["goal_sha256"], expected_sha)
        state = json.loads((self.project_root / "project_state.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(state["goal_anchor"]["goal_sha256"], expected_sha)
        self.assertEqual(state["status"], "HUMAN_REVIEW")  # lifecycle untouched
        runtime = json.loads((self.root / "control" / "orchestrator_runtime.json").read_text(encoding="utf-8-sig"))
        self.assertEqual(runtime["goal_anchor_binding"]["goal_sha256"], expected_sha)

        # A post-migration Supervisor turn verifies cleanly.
        m = self._load_orchestrator(self.root)
        m.DESKTOP_NOTIFICATIONS_ENABLED = False
        m.USER_NOTIFICATION_CONSOLE_ENABLED = False
        m.activate_project_scope()
        self.assertTrue(m.goal_anchor_gate(m.load_runtime(), m.read_project_state()))

    def test_migration_refuses_rebind_and_hash_update(self):
        self.assertEqual(self.run_migration().returncode, 0)
        self.goal_file.write_text("# Rewritten goal\n", encoding="utf-8")
        second = self.run_migration()
        self.assertEqual(second.returncode, 3)  # EXIT_ALREADY_BOUND
        state = json.loads((self.project_root / "project_state.json").read_text(encoding="utf-8-sig"))
        # the binding was never updated to the new bytes
        self.assertNotEqual(state["goal_anchor"]["goal_sha256"],
                            hashlib.sha256(self.goal_file.read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
