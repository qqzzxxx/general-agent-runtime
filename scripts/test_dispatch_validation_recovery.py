"""FIX-700102 regression: mechanically rejected FINAL_VERIFICATION dispatch recovery.

Incident: Codex published a FINAL_VERIFICATION dispatch whose CRITICAL_CLAIMS used
uppercase keys (CLAIM_ID/TYPE/CLAIM/FALSIFIED_IF). Post-turn validation raised
RuntimeError("Invalid FINAL_VERIFICATION critical claims: ..."), the Runtime went
terminal ORCHESTRATOR_ERROR, but TO_ZCODE.md stayed visible while authorized_dispatch
never covered it — every Scheduled Automation wakeup then hit
CLAIM_NOT_AUTHORIZED / authorization_message_id_mismatch (exit 12) forever.

The fix keeps every validation rule strict and adds: quarantine of the rejected
candidate out of the Executor inbox, exactly one bounded Supervisor repair turn
(with the precise validation error injected into the prompt), and terminal
fail-closed on a second consecutive rejection. The Executor claim helper semantics
(0/10/11/12) are unchanged and re-asserted here.
"""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

CANDIDATE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


o = load("fvr_orchestrator", CANDIDATE / "orchestrator.py")
c = load("fvr_executor_claim", CANDIDATE / "scripts" / "executor_claim.py")


def wire(value):
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


def valid_claim(cid, ctype="DELIVERABLE_INTEGRITY"):
    return {
        "claim_id": cid,
        "claim": f"Decision-critical claim {cid}.",
        "claim_type": ctype,
        "decision_impact": "HIGH",
        "evidence_pointers": [f"evidence/{cid}.txt"],
        "verification_standard": f"Adversarially verify {cid} against cited evidence.",
    }


class DispatchValidationRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fvr-700102-")
        self.root = Path(self.temp.name)
        for relative in ("control", "logs", "handoff/archive", "reports", "workspace"):
            (self.root / relative).mkdir(parents=True)
        shutil.copytree(CANDIDATE / "profiles", self.root / "profiles")
        self.patch_orchestrator_root()
        o.DESKTOP_NOTIFICATIONS_ENABLED = False
        o.USER_NOTIFICATION_CONSOLE_ENABLED = False
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text("700013\n", encoding="utf-8")
        self.runtime = {
            "schema_version": 2,
            "last_consumed_message_id": 700013,
            "last_consumed_nonce": "prior-nonce",
            "last_consumed_brief_sha256": "prior-brief",
            "last_dispatched_message_id": 700013,
            "last_dispatched_nonce": "prior-nonce",
            "claim_protocol_version": 1,
            "claim_protocol_required_from_message_id": 700008,
            "authorized_dispatch": None,
            "retired_message_ids": [],
            "dispatch_validation_repair_used": 0,
            "last_dispatch_validation_error": None,
            "last_quarantined_dispatch": None,
        }
        o.atomic_json(o.RUNTIME_STATE, self.runtime)
        (self.root / "workspace" / "sentinel.txt").write_text("sentinel\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def patch_orchestrator_root(self):
        o.ROOT = self.root
        o.CONTROL = self.root / "control"
        o.LOGS = self.root / "logs"
        o.HANDOFF_ARCHIVE = self.root / "handoff" / "archive"
        o.REPORTS = self.root / "reports"
        o.PROJECT_STATE = self.root / "control" / "project_state.json"
        o.RUNTIME_STATE = self.root / "control" / "orchestrator_runtime.json"
        o.SUPERVISOR_RULES = self.root / "control" / "CODEX_SUPERVISOR_RUNTIME.md"
        o.RESEARCH_STATE = self.root / "RESEARCH_STATE.md"
        o.COMMERCIAL_GOAL = self.root / "control" / "CROSS_BORDER_GOAL.md"
        o.TO_ZCODE = self.root / "TO_ZCODE.md"
        o.SUPERVISOR_BRIEF = self.root / "SUPERVISOR_BRIEF.md"
        o.ZCODE_DONE = self.root / "ZCODE_DONE.flag"
        o.ZCODE_LAST_PROCESSED = self.root / "ZCODE_LAST_PROCESSED.txt"
        o.STOP_FLAG = self.root / "control" / "STOP"
        o.HUMAN_REVIEW_FLAG = self.root / "control" / "HUMAN_REVIEW"
        o.LOCK_FILE = self.root / "control" / ".orchestrator.lock"
        o.CODEX_LAST_OUTPUT = self.root / "CODEX_LAST_OUTPUT.txt"
        o.USER_ATTENTION = self.root / "control" / "USER_ATTENTION.json"
        o.USER_STATUS_REPORT = self.root / "reports" / "USER_STATUS.md"
        o.ACTIVE_PROJECT_FILE = self.root / "control" / "ACTIVE_PROJECT.json"
        o.ACTIVE_PROJECT = None
        o.PROFILES_DIR = self.root / "profiles"

    # --- fixtures -----------------------------------------------------------

    def publish(self, task, state=None):
        if state is None:
            state = self.state_for(task)
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(task))
        return state

    def state_for(self, task, claims=None):
        gate_claims = task["FINAL_VERIFICATION_GATE"]["CRITICAL_CLAIMS"] if claims is None else claims
        return {
            "schema_version": 4,
            "profile": "GENERAL",
            "status": "WAITING_EXECUTOR",
            "phase": "GENERAL",
            "infrastructure_status": "READY",
            "current_task": {key: task[key] for key in o.IDENTITY_KEYS},
            "final_verification": {
                "policy_id": "GENERAL_FV_V1",
                "policy_version": 1,
                "required": True,
                "status": "PENDING",
                "critical_claims": gate_claims,
                "claims_hash": o.canonical_claims_hash(gate_claims),
            },
        }

    def fv_task(self, message_id=700015, claims=None):
        claims = claims if claims is not None else [
            valid_claim("C1"),
            valid_claim("C2", "NUMERICAL"),
            valid_claim("C3", "SOURCE_SUPPORT"),
        ]
        task = {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": message_id,
            "TASK_ID": "task-final-verification",
            "STAGE_ID": "final-verification-attempt-1",
            "ATTEMPT": 1,
            "NONCE": f"nonce-{message_id}",
            "OBJECTIVE": "bounded adversarial verification",
            "OUTPUTS": ["evidence/final_verification.json"],
            "TASK_KIND": "FINAL_VERIFICATION",
            "FINAL_VERIFICATION_GATE": {
                "POLICY_ID": "GENERAL_FV_V1",
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": o.canonical_claims_hash(claims),
                "CLAIM_COUNT": len(claims),
                "CRITICAL_CLAIMS": claims,
            },
            "EXECUTOR_PROTOCOL": [
                "Run python scripts/executor_claim.py acquire before stage work."
            ],
        }
        return task

    def uppercase_claim(self, cid):
        """The exact 700102 incident shape: uppercase keys, only four fields."""
        return {
            "CLAIM_ID": cid,
            "TYPE": "DELIVERABLE_INTEGRITY",
            "CLAIM": f"Decision-critical claim {cid}.",
            "FALSIFIED_IF": f"Claim {cid} is contradicted.",
        }

    def reject(self, task, state):
        """Run register + recovery exactly as the orchestrator failure path does."""
        try:
            o.register_dispatched_task(self.runtime, state)
        except RuntimeError as exc:
            if "same task identity" in str(exc):
                raise
            return o.handle_dispatch_registration_failure(self.runtime, state, exc)
        return None

    def acquire(self, task):
        return c.acquire(
            self.root,
            task["MESSAGE_ID"],
            task["TASK_ID"],
            task["STAGE_ID"],
            task["ATTEMPT"],
            task["NONCE"],
        )

    def quarantine_files(self):
        parent = self.root / "handoff" / "quarantine"
        return sorted(p.name for p in parent.glob("dispatch-*-rejected.md")) if parent.exists() else []

    # --- 1. valid critical_claims pass normally ------------------------------

    def test_valid_fv_dispatch_registers_and_authorizes(self):
        task = self.fv_task()
        state = self.publish(task)
        registered = o.register_dispatched_task(self.runtime, state)
        self.assertEqual(registered["MESSAGE_ID"], task["MESSAGE_ID"])
        auth = self.runtime["authorized_dispatch"]
        self.assertEqual(auth["MESSAGE_ID"], task["MESSAGE_ID"])
        self.assertEqual(auth["NONCE"], task["NONCE"])
        self.assertEqual(len(auth["TO_ZCODE_SHA256"]), 64)
        self.assertEqual(self.acquire(task), c.EXIT_ACQUIRED)

    # --- 2. missing claim_id -> no authorization ------------------------------

    def test_missing_claim_id_is_rejected_quarantined_and_unclaimable(self):
        claims = [valid_claim("C1"), valid_claim("C2"), valid_claim("C3")]
        del claims[0]["claim_id"]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)

        returned = self.reject(task, state)
        self.assertIsNotNone(returned)
        self.assertFalse(o.TO_ZCODE.exists())
        self.assertEqual(len(self.quarantine_files()), 1)
        self.assertIn("dispatch-700015-", self.quarantine_files()[0])
        self.assertEqual(returned["status"], "SUPERVISOR_TURN")
        marker = returned["dispatch_repair"]
        self.assertIn("missing ['claim_id']", marker["error"])
        self.assertEqual(marker["rejected_identity"]["MESSAGE_ID"], 700015)
        self.assertEqual(self.runtime["dispatch_validation_repair_used"], 1)
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims_directories(), [])

    # --- 3. missing verification_standard -> no authorization -----------------

    def test_missing_verification_standard_is_rejected_and_quarantined(self):
        claims = [valid_claim("C1"), valid_claim("C2"), valid_claim("C3")]
        del claims[1]["verification_standard"]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)

        returned = self.reject(task, state)
        self.assertIsNotNone(returned)
        self.assertFalse(o.TO_ZCODE.exists())
        self.assertEqual(len(self.quarantine_files()), 1)
        self.assertIn("missing ['verification_standard']", returned["dispatch_repair"]["error"])
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims_directories(), [])

    # --- 4. malformed claim -> exactly one structural repair opportunity ------

    def test_first_rejection_arms_exactly_one_bounded_repair_turn(self):
        claims = [self.uppercase_claim(cid) for cid in ("C1", "C2", "C3")]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)

        returned = self.reject(task, state)
        self.assertIsNotNone(returned)
        self.assertEqual(returned["dispatch_repair"]["repair_attempt"], 1)
        self.assertEqual(returned["dispatch_repair"]["max_repair_attempts"], 1)

        prompt = o.build_codex_prompt(
            "SUPERVISOR_TURN", {"source": "project_state"}, o.read_project_state()
        )
        self.assertIn("MECHANICAL DISPATCH REJECTION", prompt)
        self.assertIn("critical_claims[1] missing", prompt)
        self.assertIn("'claim_id'", prompt)
        self.assertIn("Repair attempt: 1 of 1", prompt)
        self.assertIn("six lowercase keys", prompt)

    # --- 5. repaired dispatch registers and resets the repair chain ----------

    def test_repaired_dispatch_registers_resets_counter_and_clears_marker(self):
        claims = [valid_claim("C1"), valid_claim("C2"), valid_claim("C3")]
        del claims[0]["verification_standard"]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)
        self.assertIsNotNone(self.reject(task, state))

        # Supervisor repair turn: fix the claim schema, republish the same identity.
        fixed = [valid_claim(cid) for cid in ("C1", "C2", "C3")]
        fixed_task = self.fv_task(claims=fixed)
        fixed_state = self.state_for(fixed_task, claims=fixed)
        fixed_state["status"] = "WAITING_EXECUTOR"
        o.atomic_json(o.PROJECT_STATE, fixed_state)
        o.atomic_write(o.TO_ZCODE, wire(fixed_task))

        registered = o.register_dispatched_task(self.runtime, fixed_state)
        self.assertEqual(registered["MESSAGE_ID"], 700015)
        self.assertEqual(self.runtime["dispatch_validation_repair_used"], 0)
        self.assertIsNone(o.read_project_state().get("dispatch_repair"))
        self.assertEqual(self.runtime["authorized_dispatch"]["MESSAGE_ID"], 700015)
        self.assertEqual(self.acquire(fixed_task), c.EXIT_ACQUIRED)

    # --- 6. second rejection is terminal fail-closed --------------------------

    def test_second_rejection_is_terminal_fail_closed(self):
        claims = [valid_claim("C1"), valid_claim("C2"), valid_claim("C3")]
        del claims[0]["decision_impact"]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)
        self.assertIsNotNone(self.reject(task, state))

        # The Supervisor repair turn republishes another invalid claim set.
        bad2 = [valid_claim(cid) for cid in ("C1", "C2", "C3")]
        del bad2[1]["claim_id"]
        task2 = self.fv_task(claims=bad2)
        task2["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(bad2)
        task2["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(bad2)
        state2 = self.state_for(task2, claims=bad2)
        state2["status"] = "WAITING_EXECUTOR"
        o.atomic_json(o.PROJECT_STATE, state2)
        o.atomic_write(o.TO_ZCODE, wire(task2))

        with self.assertRaisesRegex(RuntimeError, "missing \\['claim_id'\\]"):
            o.handle_dispatch_registration_failure(
                self.runtime, state2, RuntimeError(
                    "Invalid FINAL_VERIFICATION critical claims: critical_claims[2] missing ['claim_id']"
                )
            )
        self.assertEqual(self.runtime["dispatch_validation_repair_used"], 1)
        # Nothing was ever authorized for the rejected candidates...
        self.assertIsNone(self.runtime["authorized_dispatch"])
        # ...and the second invalid candidate was still quarantined out of the inbox,
        # so a terminal failure cannot leave a repeated-gate-crash trap behind.
        self.assertFalse(o.TO_ZCODE.exists())
        self.assertEqual(len(self.quarantine_files()), 2)

    # --- 7. invalid candidate never reaches execution -------------------------

    def test_invalid_candidate_causes_no_executor_execution(self):
        claims = [self.uppercase_claim(cid) for cid in ("C1", "C2", "C3")]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)
        sentinel = self.root / "workspace" / "sentinel.txt"

        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.reject(task, state)
        self.assertFalse(o.TO_ZCODE.exists())
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims_directories(), [])
        self.assertFalse(o.SUPERVISOR_BRIEF.exists())
        self.assertFalse(o.ZCODE_DONE.exists())
        self.assertEqual(
            (self.root / "ZCODE_LAST_PROCESSED.txt").read_text(encoding="utf-8"), "700013\n"
        )
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "sentinel\n")

    # --- 8. no unmanaged repeat-crash state after quarantine ------------------

    def test_quarantine_removes_repeated_gate_crash_state(self):
        claims = [self.uppercase_claim(cid) for cid in ("C1", "C2", "C3")]
        task = self.fv_task(claims=claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(claims)
        task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(claims)
        state = self.publish(task)
        self.reject(task, state)

        # Repeated Scheduled-Automation-style wakeups cannot claim anything: the inbox
        # is gone and the authorization record still binds the previous dispatch.
        for _ in range(3):
            self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims_directories(), [])
        self.assertEqual(
            self.runtime["last_quarantined_dispatch"],
            f"handoff{chr(92)}quarantine{chr(92)}{self.quarantine_files()[0]}",
        )
        # A second quarantine attempt is a no-op (nothing matching left in the inbox).
        self.assertIsNone(o.quarantine_dispatch_candidate(state, "repeat"))
        self.assertEqual(len(self.quarantine_files()), 1)

    # --- 9. claim helper exit-code semantics unchanged -------------------------

    def test_claim_helper_exit_code_semantics_unchanged(self):
        self.assertEqual(c.EXIT_ACQUIRED, 0)
        self.assertEqual(c.EXIT_CLAIM_EXISTS, 10)
        self.assertEqual(c.EXIT_ALREADY_PROCESSED, 11)
        self.assertEqual(c.EXIT_ERROR, 12)

        task = self.fv_task()
        state = self.publish(task)
        o.register_dispatched_task(self.runtime, state)
        self.assertEqual(self.acquire(task), 0)          # CLAIM_ACQUIRED
        self.assertEqual(self.acquire(task), 10)         # CLAIM_EXISTS
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text("700015\n", encoding="utf-8")
        task_processed = self.fv_task(700014)
        self.publish(task_processed, self.state_for(task_processed))
        self.assertEqual(self.acquire(task_processed), 11)  # ALREADY_PROCESSED (<= last)
        task_unauthorized = self.fv_task(700017)
        self.publish(task_unauthorized, self.state_for(task_unauthorized))
        self.assertEqual(self.acquire(task_unauthorized), 12)  # NOT_AUTHORIZED / fail closed

    # --- 10. supervisor contract documents the lowercase claim schema ----------

    def test_supervisor_contract_documents_lowercase_claim_schema(self):
        for relative in (
            "control/CODEX_SUPERVISOR_RUNTIME.md",
            "control/EXECUTOR_TASK_TEMPLATE.md",
            "control/FINAL_VERIFICATION_POLICY.md",
        ):
            text = (CANDIDATE / relative).read_text(encoding="utf-8")
            normalized = " ".join(text.split())
            self.assertIn("claim_id", text, relative)
            self.assertIn("verification_standard", text, relative)
            self.assertIn("lowercase", normalized, relative)
            self.assertIn("quarantin", normalized, relative)

        prompt = o.build_codex_prompt(
            "EXECUTOR_RESULT_READY", {"type": "EXECUTOR_RESULT_READY"}, {"status": "WAITING_EXECUTOR"}
        )
        self.assertIn('"claim_id"', prompt)
        self.assertIn('"verification_standard"', prompt)
        self.assertIn("six lowercase keys", prompt)
        self.assertIn("sort_keys=True", prompt)

    # --- integration: invoke_codex failure path arms the repair turn -----------

    def fake_codex(self, actions):
        calls = {"n": 0}

        def run(cmd, **_kwargs):
            index = calls["n"]
            calls["n"] += 1
            output_path = Path(cmd[cmd.index("-o") + 1])
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text("", encoding="utf-8")
            if index < len(actions):
                actions[index]()
            return subprocess.CompletedProcess(cmd, 0)

        return run

    def test_invoke_codex_rejected_dispatch_then_repair_turn_succeeds(self):
        bad_claims = [self.uppercase_claim(cid) for cid in ("C1", "C2", "C3")]
        bad_task = self.fv_task(claims=bad_claims)
        bad_task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(bad_claims)
        bad_task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(bad_claims)
        state = self.publish(bad_task)

        def repair_action():
            fixed = [valid_claim(cid) for cid in ("C1", "C2", "C3")]
            fixed_task = self.fv_task(claims=fixed)
            fixed_task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(fixed)
            fixed_task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(fixed)
            o.atomic_json(o.PROJECT_STATE, self.state_for(fixed_task, claims=fixed))
            o.atomic_write(o.TO_ZCODE, wire(fixed_task))

        o.acquire_lock()
        try:
            with patch.object(o, "find_codex", return_value="codex-fixture"), patch.object(
                o.subprocess, "run", side_effect=self.fake_codex([])
            ):
                # Turn 1: the supervisor turn leaves the malformed dispatch untouched;
                # registration must fail and be recovered, not crash the runtime.
                o.invoke_codex(self.runtime, "EXECUTOR_RESULT_READY", {"type": "EXECUTOR_RESULT_READY"})
            self.assertEqual(o.read_project_state()["status"], "SUPERVISOR_TURN")
            self.assertFalse(o.TO_ZCODE.exists())
            self.assertEqual(self.runtime["dispatch_validation_repair_used"], 1)

            with patch.object(o, "find_codex", return_value="codex-fixture"), patch.object(
                o.subprocess, "run", side_effect=self.fake_codex([repair_action])
            ):
                # Turn 2: the repair turn republishes a valid dispatch; registration
                # resets the chain.
                o.invoke_codex(self.runtime, "SUPERVISOR_TURN", {"source": "project_state"})
        finally:
            o.release_lock()

        self.assertEqual(self.runtime["authorized_dispatch"]["MESSAGE_ID"], 700015)
        self.assertEqual(self.runtime["dispatch_validation_repair_used"], 0)
        self.assertIsNone(o.read_project_state().get("dispatch_repair"))

    def test_invoke_codex_second_rejected_dispatch_is_terminal(self):
        bad_claims = [self.uppercase_claim(cid) for cid in ("C1", "C2", "C3")]
        bad_task = self.fv_task(claims=bad_claims)
        bad_task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(bad_claims)
        bad_task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(bad_claims)
        self.publish(bad_task)

        def bad_repair_action():
            worse = [self.uppercase_claim(cid) for cid in ("C1", "C2", "C3")]
            worse_task = self.fv_task(claims=worse)
            worse_task["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"] = o.canonical_claims_hash(worse)
            worse_task["FINAL_VERIFICATION_GATE"]["CLAIM_COUNT"] = len(worse)
            o.atomic_json(o.PROJECT_STATE, self.state_for(worse_task, claims=worse))
            o.atomic_write(o.TO_ZCODE, wire(worse_task))

        o.acquire_lock()
        try:
            with patch.object(o, "find_codex", return_value="codex-fixture"), patch.object(
                o.subprocess, "run", side_effect=self.fake_codex([])
            ):
                o.invoke_codex(self.runtime, "EXECUTOR_RESULT_READY", {"type": "EXECUTOR_RESULT_READY"})
            with patch.object(o, "find_codex", return_value="codex-fixture"), patch.object(
                o.subprocess, "run", side_effect=self.fake_codex([bad_repair_action])
            ):
                with self.assertRaisesRegex(RuntimeError, "Invalid FINAL_VERIFICATION critical claims"):
                    o.invoke_codex(self.runtime, "SUPERVISOR_TURN", {"source": "project_state"})
        finally:
            o.release_lock()

        self.assertEqual(self.runtime["dispatch_validation_repair_used"], 1)
        # The terminal path does not rewrite the Supervisor's last state; it matters
        # that the inbox is clean (already asserted via quarantine above) so no
        # Executor can ever claim the rejected dispatch afterwards.
        self.assertEqual(o.read_project_state()["status"], "WAITING_EXECUTOR")
        self.assertIsNone(self.runtime["authorized_dispatch"])

    def claims_directories(self):
        parent = self.root / "handoff" / "executor_claims"
        return [p for p in parent.iterdir() if p.is_dir()] if parent.exists() else []


class PreflightRecoveryMiddleStateTests(unittest.TestCase):
    """FIX-700102: preflight must not hard-block the recoverable dispatch-rejection
    middle state, or the START script would prevent the Orchestrator startup repair
    path from ever running. A missing inbox WITH a covering authorization remains a
    real inbox-loss error."""

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="fvr-preflight-")
        cls.root = Path(cls.temp.name)
        for relative in ("control", "logs", "handoff/archive", "reports", "scripts"):
            (cls.root / relative).mkdir(parents=True)
        shutil.copytree(CANDIDATE / "profiles", cls.root / "profiles")
        shutil.copy(CANDIDATE / "scripts" / "preflight.py", cls.root / "scripts" / "preflight.py")
        shutil.copy(CANDIDATE / "orchestrator.py", cls.root / "orchestrator.py")
        cls.project_id = "preflight-smoke-001"
        (cls.root / "projects" / cls.project_id).mkdir(parents=True)
        (cls.root / "control" / "project_state.json").write_text("{}\n", encoding="utf-8")
        (cls.root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps({
            "schema_version": 1,
            "project_id": cls.project_id,
            "project_root": f"projects/{cls.project_id}",
        }), encoding="utf-8")
        (cls.root / "control" / "CODEX_SUPERVISOR_RUNTIME.md").write_text("contract\n", encoding="utf-8")
        (cls.root / "RESEARCH_STATE.md").write_text("memory\n", encoding="utf-8")
        (cls.root / "ZCODE_LAST_PROCESSED.txt").write_text("700013\n", encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def write_scene(self, authorized_message_id):
        task_identity = {
            "MESSAGE_ID": 700015,
            "TASK_ID": "task-final-verification",
            "STAGE_ID": "final-verification-attempt-1",
            "ATTEMPT": 1,
            "NONCE": "nonce-700015",
        }
        state = {
            "schema_version": 4,
            "project_id": self.project_id,
            "profile": "GENERAL",
            "status": "WAITING_EXECUTOR",
            "phase": "GENERAL",
            "infrastructure_status": "READY",
            "started_at": "2026-09-05T07:10:19+00:00",
            "current_task": dict(task_identity),
            "final_verification": {
                "policy_id": "GENERAL_FV_V1",
                "policy_version": 1,
                "required": True,
                "status": "PENDING",
                "critical_claims": [],
                "claims_hash": None,
            },
        }
        (self.root / "projects" / self.project_id / "project_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        authorization = None
        if authorized_message_id is not None:
            authorization = {
                "schema_version": 1,
                **{key: (authorized_message_id if key == "MESSAGE_ID" else value)
                   for key, value in task_identity.items()},
                "TO_ZCODE_SHA256": "0" * 64,
                "AUTHORIZED_AT": "2026-09-05T07:22:55+00:00",
            }
        runtime = {"schema_version": 2, "authorized_dispatch": authorization,
                   "retired_message_ids": []}
        (self.root / "control" / "orchestrator_runtime.json").write_text(
            json.dumps(runtime, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def run_preflight(self):
        import subprocess
        return subprocess.run(
            [subprocess.sys.executable if hasattr(subprocess, "sys") else "python",
             str(self.root / "scripts" / "preflight.py")],
            cwd=self.root, capture_output=True, text=True,
        )

    def test_uncovered_authorization_with_missing_inbox_is_recoverable(self):
        self.write_scene(authorized_message_id=700013)
        result = self.run_preflight()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PREFLIGHT: OK", result.stdout)
        self.assertIn("recoverable dispatch-rejection middle state", result.stdout)

    def test_covered_authorization_with_missing_inbox_fails_closed(self):
        self.write_scene(authorized_message_id=700015)
        result = self.run_preflight()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", result.stdout)
        self.assertIn("WAITING_EXECUTOR but TO_ZCODE.md is missing", result.stdout)


if __name__ == "__main__":
    unittest.main()
