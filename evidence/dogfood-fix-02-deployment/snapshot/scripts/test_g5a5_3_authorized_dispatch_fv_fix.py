"""G5A.5.3 regression: authorized dispatch claim gate + canonical Generic FV wire."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


o = load("g5a5_3_orchestrator", CANDIDATE / "orchestrator.py")
c = load("g5a5_3_executor_claim", CANDIDATE / "scripts" / "executor_claim.py")


def wire(value):
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


def claim(cid, ctype):
    return {
        "claim_id": cid,
        "claim": f"decision-critical claim {cid}",
        "claim_type": ctype,
        "decision_impact": "HIGH",
        "evidence_pointers": [f"evidence/{cid}.txt"],
        "verification_standard": f"Adversarially verify {cid}.",
    }


class AuthorizedDispatchAndFVTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g5a5-3-")
        self.root = Path(self.temp.name)
        for relative in ("control", "logs", "handoff/archive", "reports"):
            (self.root / relative).mkdir(parents=True)
        shutil.copytree(CANDIDATE / "profiles", self.root / "profiles")
        self.patch_orchestrator_root()
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text(
            "700013\n", encoding="utf-8"
        )
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
        }
        o.atomic_json(o.RUNTIME_STATE, self.runtime)

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

    def normal_task(self, message_id=700015):
        return {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": message_id,
            "TASK_ID": "task-normal",
            "STAGE_ID": "stage-normal",
            "ATTEMPT": 1,
            "NONCE": f"nonce-{message_id}",
            "OBJECTIVE": "bounded task",
            "OUTPUTS": ["deliverable.txt"],
            "EXECUTOR_PROTOCOL": [
                "Run python scripts/executor_claim.py acquire before stage work."
            ],
        }

    def fv_task_and_state(self, message_id=700015):
        claims = [
            claim("C1", "BUG_REPRODUCTION"),
            claim("C2", "ROOT_CAUSE"),
            claim("C3", "REGRESSION"),
        ]
        task = self.normal_task(message_id)
        task.update({
            "TASK_ID": "task-final-verification",
            "STAGE_ID": "final-verification-v3",
            "ATTEMPT": 3,
            "TASK_KIND": "FINAL_VERIFICATION",
            "FINAL_VERIFICATION_GATE": {
                "POLICY_ID": "SOFTWARE_ENGINEERING_FV_V1",
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": o.canonical_claims_hash(claims),
                "CLAIM_COUNT": len(claims),
                "CRITICAL_CLAIMS": claims,
            },
        })
        state = {
            "schema_version": 4,
            "profile": "SOFTWARE_ENGINEERING",
            "status": "WAITING_EXECUTOR",
            "phase": "SOFTWARE_ENGINEERING",
            "infrastructure_status": "READY",
            "current_task": {
                key: task[key] for key in o.IDENTITY_KEYS
            },
            "final_verification": {
                "policy_id": "SOFTWARE_ENGINEERING_FV_V1",
                "policy_version": 1,
                "required": True,
                "status": "PENDING",
                "critical_claims": claims,
                "claims_hash": o.canonical_claims_hash(claims),
            },
        }
        return task, state

    def publish(self, task, state=None):
        if state is None:
            state = {
                "schema_version": 4,
                "profile": "SOFTWARE_ENGINEERING",
                "status": "WAITING_EXECUTOR",
                "phase": "SOFTWARE_ENGINEERING",
                "infrastructure_status": "READY",
                "current_task": {key: task[key] for key in o.IDENTITY_KEYS},
                "final_verification": {
                    "policy_id": "SOFTWARE_ENGINEERING_FV_V1",
                    "policy_version": 1,
                    "required": True,
                    "status": "NOT_STARTED",
                    "critical_claims": [],
                    "claims_hash": None,
                },
            }
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(task))
        return state

    def authorize(self, task, state=None):
        state = self.publish(task, state)
        o.register_dispatched_task(self.runtime, state)
        return state

    def acquire(self, task, **changes):
        identity = {key: task[key] for key in o.IDENTITY_KEYS}
        identity.update(changes)
        return c.acquire(
            self.root,
            identity["MESSAGE_ID"],
            identity["TASK_ID"],
            identity["STAGE_ID"],
            identity["ATTEMPT"],
            identity["NONCE"],
        )

    def claims(self):
        parent = self.root / "handoff" / "executor_claims"
        return list(parent.iterdir()) if parent.exists() else []

    def test_unregistered_fresh_visible_task_is_rejected(self):
        task = self.normal_task()
        self.publish(task)
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])

    def test_valid_authorization_claims_exactly_once(self):
        task = self.normal_task()
        self.authorize(task)
        self.assertEqual(self.acquire(task), c.EXIT_ACQUIRED)
        self.assertEqual(self.acquire(task), c.EXIT_CLAIM_EXISTS)
        self.assertEqual(len(self.claims()), 1)

    def test_wrong_nonce_is_rejected(self):
        task = self.normal_task()
        self.authorize(task)
        self.assertEqual(self.acquire(task, NONCE="wrong-nonce"), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])

    def test_wrong_task_stage_and_attempt_are_rejected(self):
        task = self.normal_task()
        self.authorize(task)
        cases = (
            {"TASK_ID": "wrong-task"},
            {"STAGE_ID": "wrong-stage"},
            {"ATTEMPT": 99},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertEqual(self.acquire(task, **changes), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])

    def test_already_processed_preserves_existing_behavior(self):
        task = self.normal_task()
        self.publish(task)
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text(
            f"{task['MESSAGE_ID']}\n", encoding="utf-8"
        )
        self.assertEqual(self.acquire(task), c.EXIT_ALREADY_PROCESSED)
        self.assertEqual(self.claims(), [])

    def test_concurrent_authorized_claim_has_one_winner(self):
        task = self.normal_task()
        self.authorize(task)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.acquire(task), range(8)))
        self.assertEqual(results.count(c.EXIT_ACQUIRED), 1)
        self.assertEqual(results.count(c.EXIT_CLAIM_EXISTS), 7)
        self.assertEqual(len(self.claims()), 1)

    def test_final_verification_without_nested_gate_is_rejected(self):
        task, state = self.fv_task_and_state()
        del task["FINAL_VERIFICATION_GATE"]
        with self.assertRaisesRegex(
            RuntimeError, "missing FINAL_VERIFICATION_GATE"
        ):
            o.validate_final_verification_dispatch(state, task)

    def test_profile_bound_generic_fv_dispatch_is_authorized(self):
        task, state = self.fv_task_and_state()
        self.authorize(task, state)
        auth = self.runtime["authorized_dispatch"]
        self.assertEqual(auth["MESSAGE_ID"], task["MESSAGE_ID"])
        self.assertEqual(auth["NONCE"], task["NONCE"])
        self.assertEqual(auth["TASK_ID"], task["TASK_ID"])
        self.assertEqual(auth["STAGE_ID"], task["STAGE_ID"])
        self.assertEqual(auth["ATTEMPT"], task["ATTEMPT"])
        self.assertEqual(len(auth["TO_ZCODE_SHA256"]), 64)

    def test_existing_non_fv_dispatch_remains_compatible(self):
        task = self.normal_task()
        self.authorize(task)
        self.assertEqual(self.runtime["last_dispatched_message_id"], 700015)
        self.assertEqual(self.acquire(task), c.EXIT_ACQUIRED)

    def test_700014_class_ordering_has_no_executor_side_effects(self):
        task = self.normal_task(700014)
        task.update({
            "TASK_ID": "task_queue_final_verification",
            "STAGE_ID": "final_verification_v2",
            "ATTEMPT": 2,
            "NONCE": "unauthorized-700014-nonce",
        })
        self.publish(task)
        deliverable = self.root / "deliverable.txt"
        deliverable.write_text("sentinel\n", encoding="utf-8")

        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])
        self.assertEqual(deliverable.read_text(encoding="utf-8"), "sentinel\n")
        self.assertEqual(
            (self.root / "ZCODE_LAST_PROCESSED.txt").read_text(encoding="utf-8"),
            "700013\n",
        )
        self.assertFalse(o.SUPERVISOR_BRIEF.exists())
        self.assertFalse(o.ZCODE_DONE.exists())

    def test_authorized_but_missing_or_changed_inbox_is_rejected(self):
        task = self.normal_task()
        self.authorize(task)
        o.TO_ZCODE.unlink()
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])
        o.atomic_write(o.TO_ZCODE, wire({**task, "OBJECTIVE": "changed after auth"}))
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])

    def test_retired_message_cannot_be_registered_or_claimed(self):
        task = self.normal_task(700014)
        state = self.publish(task)
        self.runtime["retired_message_ids"] = [700014]
        o.atomic_json(o.RUNTIME_STATE, self.runtime)
        with self.assertRaisesRegex(RuntimeError, "permanently retired"):
            o.register_dispatched_task(self.runtime, state)
        self.assertEqual(self.acquire(task), c.EXIT_ERROR)
        self.assertEqual(self.claims(), [])

    def test_supervisor_contract_shows_exact_nested_profile_gate(self):
        runtime_contract = (CANDIDATE / "control" / "CODEX_SUPERVISOR_RUNTIME.md").read_text(
            encoding="utf-8"
        )
        template = (CANDIDATE / "control" / "EXECUTOR_TASK_TEMPLATE.md").read_text(
            encoding="utf-8"
        )
        for text in (runtime_contract, template):
            normalized = " ".join(text.split())
            self.assertIn('"FINAL_VERIFICATION_GATE": {', text)
            self.assertIn('"POLICY_ID": "<active Profile final_verification_policy_id>"', text)
            self.assertIn("task top level", normalized)


if __name__ == "__main__":
    unittest.main()
