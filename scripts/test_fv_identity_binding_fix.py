"""FV-IDENTITY-BINDING-V1 regression: authoritative Final Verification identity binding.

Covers the cold-start-smoke-001 incident class: an FV receipt consumed by the Runtime
must durably bind last_final_verification_* from the Runtime's own authorization
record (not transient Supervisor-authored current_task), the COMPLETE gate must accept
exactly that identity and reject stale/future/wrong references, ordinary bookkeeping
must not clobber the binding, an identical FV re-dispatch must be mechanically
rejected while its PASS receipt is the freshest consumed receipt, and a ledger-proven
consumption must reconcile a lost binding (bounded recovery) without re-executing the
verification.

All fixtures run in temporary directories with synthetic identities.
"""

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import executor_claim as claim_helper
import executor_completion as completion_helper


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


o = load("fv_identity_binding_orchestrator", CANDIDATE / "orchestrator.py")


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


CLAIMS = [
    claim("C1", "DELIVERABLE_INTEGRITY"),
    claim("C2", "NUMERICAL"),
    claim("C3", "SOURCE_SUPPORT"),
    claim("C4", "CONSTRAINT_COMPLIANCE"),
]
CLAIMS_HASH = o.canonical_claims_hash(CLAIMS)
CLAIM_CHECKS = {
    "C1": {"deliverable_check": "PASS", "deliverable_path": "workspace/artifact.txt"},
    "C2": {"calculation_check": "PASS", "calculation_artifact": "evidence/digest.txt"},
    "C3": {"source_pointers": ["evidence/digest.txt"]},
    "C4": {"constraint_check": "PASS"},
}


class FVIdentityBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fv-identity-binding-")
        self.root = Path(self.temp.name)
        for relative in ("control", "logs", "handoff/archive", "reports"):
            (self.root / relative).mkdir(parents=True)
        shutil.copytree(CANDIDATE / "profiles", self.root / "profiles")
        self.patch_orchestrator_root()
        self.runtime = {
            "schema_version": 2,
            "last_consumed_message_id": 700013,
            "last_consumed_nonce": "prior-nonce",
            "last_consumed_brief_sha256": "prior-brief",
            "last_dispatched_message_id": 700013,
            "last_dispatched_nonce": "prior-nonce",
            "authorized_dispatch": None,
            "retired_message_ids": [],
            "final_verification_receipt_ledger": [],
            "last_final_verification_message_id": None,
            "last_final_verification_receipt_sha256": None,
            "last_final_verification_claims_hash": None,
            "last_final_verification_overall_status": None,
            "last_final_verification_mechanical_pass": None,
        }
        o.atomic_json(o.RUNTIME_STATE, self.runtime)
        self.fv_message_id = 700014

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

    def task(self, message_id):
        return {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": message_id,
            "TASK_ID": "COLD_START_FINAL_VERIFICATION",
            "STAGE_ID": "FINAL_VERIFICATION_ATTEMPT_1",
            "ATTEMPT": 1,
            "NONCE": f"nonce-{message_id}",
            "OBJECTIVE": "bounded final verification",
            "OUTPUTS": ["evidence/fv.txt"],
            "EXECUTOR_PROTOCOL": [
                "Run python scripts/executor_claim.py acquire before stage work."
            ],
            "TASK_KIND": "FINAL_VERIFICATION",
            "FINAL_VERIFICATION_GATE": {
                "POLICY_ID": "GENERAL_FV_V1",
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": CLAIMS_HASH,
                "CLAIM_COUNT": len(CLAIMS),
                "CRITICAL_CLAIMS": CLAIMS,
            },
        }

    def ordinary_task(self, message_id):
        return {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": message_id,
            "TASK_ID": "task-ordinary",
            "STAGE_ID": "stage-ordinary",
            "ATTEMPT": 1,
            "NONCE": f"nonce-{message_id}",
            "OBJECTIVE": "bounded ordinary task",
            "OUTPUTS": ["deliverable.txt"],
            "EXECUTOR_PROTOCOL": [
                "Run python scripts/executor_claim.py acquire before stage work."
            ],
        }

    def state_for(self, task, fv_status="PENDING"):
        # Incident shape: current_task mirrors identity keys only (the wire contract),
        # with no TASK_KIND / FINAL_VERIFICATION_GATE mirror.
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
                "status": fv_status,
                "critical_claims": CLAIMS,
                "claims_hash": CLAIMS_HASH,
            },
        }

    def authorize(self, task, state):
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(task))
        o.register_dispatched_task(self.runtime, state)

    def receipt(self, task, overall="PASS"):
        return {
            "MESSAGE_ID": task["MESSAGE_ID"],
            "TASK_ID": task["TASK_ID"],
            "STAGE_ID": task["STAGE_ID"],
            "ATTEMPT": task["ATTEMPT"],
            "NONCE": task["NONCE"],
            "STATUS": "COMPLETED",
            "FINAL_VERIFICATION": {
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": CLAIMS_HASH,
                "OVERALL_STATUS": overall,
                "CLAIM_RESULTS": [
                    {
                        "claim_id": c["claim_id"],
                        "status": "SUPPORTED",
                        "checks": dict(CLAIM_CHECKS[c["claim_id"]]),
                    }
                    for c in CLAIMS
                ],
            },
        }

    def publish_receipt_and_consume(self, task, receipt):
        """COMPLETION-SEAL-V1: commit through the Runtime helper, then consume.

        The returned brief hash is the ledger entry's BRIEF_SHA256 (the canonical
        rendering of the committed receipt), which is exactly the value the
        Orchestrator records as last_final_verification_receipt_sha256.
        """
        identity = {key: task[key] for key in o.IDENTITY_KEYS}
        self.assertEqual(
            claim_helper.acquire(
                o.ROOT,
                identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
                identity["ATTEMPT"], identity["NONCE"],
            ),
            claim_helper.EXIT_ACQUIRED,
        )
        staging_dir = o.ROOT / "completion_staging" / f"stage-{task['MESSAGE_ID']}"
        staging_dir.mkdir(parents=True)
        staging = {
            "COMPLETION_STAGING_SCHEMA_VERSION": completion_helper.COMPLETION_STAGING_SCHEMA_VERSION,
            **identity,
            "PROJECT_ID": None,
            "STATUS": "STAGING_READY",
            "CREATED_AT": o.stamp(),
            "RECEIPT": receipt,
        }
        (staging_dir / "staging.json").write_text(
            json.dumps(staging, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(
            completion_helper.commit(o.ROOT, staging_dir), completion_helper.EXIT_COMMITTED)
        entry = completion_helper.load_entry_file(
            completion_helper.entry_path(o.ROOT, completion_helper.commit_id_for(
                identity["MESSAGE_ID"], identity["NONCE"]))
        )
        self.assertIsNotNone(entry)
        consumed, event = o.consume_executor_receipt(self.runtime)
        return consumed, event, entry["BRIEF_SHA256"]

    def consume_passing_fv(self, message_id=None, overall="PASS"):
        task = self.task(message_id or self.fv_message_id)
        self.authorize(task, self.state_for(task))
        consumed, event, brief_hash = self.publish_receipt_and_consume(
            task, self.receipt(task, overall=overall)
        )
        return task, consumed, event, brief_hash

    def accepted_state(self, task, brief_hash, reference_id=None, claims_hash=None):
        return {
            "schema_version": 4,
            "profile": "GENERAL",
            "status": "COMPLETE",
            "phase": "GENERAL",
            "infrastructure_status": "READY",
            "current_task": None,
            "final_verification": {
                "policy_id": "GENERAL_FV_V1",
                "policy_version": 1,
                "required": True,
                "status": "PASS",
                "critical_claims": CLAIMS,
                "claims_hash": claims_hash or CLAIMS_HASH,
                "verification_message_id": (
                    reference_id if reference_id is not None else task["MESSAGE_ID"]
                ),
                "verification_receipt_sha256": brief_hash,
                "verified_at": "2026-09-05T12:00:00+00:00",
            },
        }

    # 1. A legally dispatched / claimed / consumed PASS FV binds the runtime and the
    #    COMPLETE gate accepts exactly that identity.
    def test_consumed_pass_fv_is_bound_and_gate_accepts_same_identity(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        self.assertTrue(consumed)
        self.assertIn("final_verification", event)
        self.assertEqual(self.runtime["last_final_verification_message_id"], self.fv_message_id)
        self.assertEqual(self.runtime["last_final_verification_receipt_sha256"], brief_hash)
        self.assertEqual(self.runtime["last_final_verification_claims_hash"], CLAIMS_HASH)
        self.assertTrue(self.runtime["last_final_verification_mechanical_pass"])
        self.assertEqual(self.runtime["last_final_verification_overall_status"], "PASS")
        ledger = self.runtime["final_verification_receipt_ledger"]
        self.assertEqual(len(ledger), 1)
        self.assertEqual(ledger[0]["message_id"], self.fv_message_id)
        self.assertEqual(ledger[0]["receipt_sha256"], brief_hash)
        self.assertEqual(ledger[0]["claims_hash"], CLAIMS_HASH)
        self.assertTrue(ledger[0]["mechanical_pass"])

        state = self.accepted_state(task, brief_hash)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertTrue(allowed, reason)
        self.assertEqual(reason, "PASS")

    # 2. A verification_message_id that differs from the consumed FV fails closed.
    def test_mismatched_reference_is_rejected(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        state = self.accepted_state(task, brief_hash, reference_id=self.fv_message_id + 1)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)
        self.assertEqual(reason, "verification_message_id does not match runtime")

    # 3. A stale previous FV identity is rejected.
    def test_stale_previous_fv_reference_is_rejected(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        state = self.accepted_state(task, brief_hash, reference_id=self.fv_message_id - 1)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)

    # 4. A future / never-consumed FV identity is rejected.
    def test_future_unconsumed_reference_is_rejected(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        state = self.accepted_state(task, brief_hash, reference_id=self.fv_message_id + 100)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)
        self.assertEqual(reason, "verification_message_id does not match runtime")

    # 5. Ordinary subsequent bookkeeping must not overwrite the accepted FV identity.
    def test_ordinary_bookkeeping_does_not_clobber_binding(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        before = {
            key: self.runtime[key]
            for key in (
                "last_final_verification_message_id",
                "last_final_verification_receipt_sha256",
                "last_final_verification_claims_hash",
                "last_final_verification_mechanical_pass",
                "last_final_verification_overall_status",
            )
        }

        ordinary = self.ordinary_task(self.fv_message_id + 1)
        self.authorize(ordinary, {
            "schema_version": 4,
            "profile": "GENERAL",
            "status": "WAITING_EXECUTOR",
            "phase": "GENERAL",
            "infrastructure_status": "READY",
            "current_task": {key: ordinary[key] for key in o.IDENTITY_KEYS},
        })
        auth = self.runtime["authorized_dispatch"]
        self.assertIs(auth["IS_FINAL_VERIFICATION"], False)
        self.assertNotIn("FINAL_VERIFICATION_GATE", auth)
        for key, value in before.items():
            self.assertEqual(self.runtime[key], value)

        consumed, event, _ = self.publish_receipt_and_consume(
            ordinary, {key: ordinary[key] for key in o.IDENTITY_KEYS} | {"STATUS": "COMPLETED"}
        )
        self.assertTrue(consumed)
        self.assertNotIn("final_verification", event)
        for key, value in before.items():
            self.assertEqual(self.runtime[key], value)
        self.assertEqual(len(self.runtime["final_verification_receipt_ledger"]), 1)

        # By design a newer consumed receipt invalidates the stale PASS for COMPLETE,
        # but for the staleness reason — never by clobbering the identity binding.
        state = self.accepted_state(task, brief_hash)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)
        self.assertIn("newer Executor receipt", reason)

    # 6. Referencing some other consumed (ordinary) message id is rejected.
    def test_reference_to_ordinary_receipt_is_rejected(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        ordinary_id = self.fv_message_id + 1
        state = self.accepted_state(task, brief_hash, reference_id=ordinary_id)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)
        self.assertEqual(reason, "verification_message_id does not match runtime")

    # 7a. Identical FV re-dispatch with a freshest PASS ledger entry is rejected.
    def test_identical_fv_redispatch_rejected_while_pass_is_freshest(self):
        self.consume_passing_fv()
        repeat = self.task(self.fv_message_id + 2)
        repeat["STAGE_ID"] = "FINAL_VERIFICATION_ATTEMPT_2"
        repeat["ATTEMPT"] = 2
        state = self.state_for(repeat, fv_status="REVERIFY")
        state["current_task"] = {key: repeat[key] for key in o.IDENTITY_KEYS}
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(repeat))
        with self.assertRaisesRegex(RuntimeError, "Identical Final Verification re-dispatch rejected"):
            o.validate_dispatch_payload(self.runtime, state, repeat)

    # 7b. A FAIL/INCONCLUSIVE history re-enables ordinary re-verification.
    def test_identical_fv_redispatch_allowed_after_fail_history(self):
        self.runtime["final_verification_receipt_ledger"] = [{
            "message_id": 700013,
            "nonce": "prior-nonce",
            "receipt_sha256": "prior-brief",
            "claims_hash": CLAIMS_HASH,
            "overall_status": "FAIL",
            "mechanical_pass": False,
            "consumed_at": "2026-09-05T00:00:00+00:00",
        }]
        self.runtime["last_consumed_message_id"] = 700013
        repeat = self.task(700014)
        state = self.state_for(repeat, fv_status="REVERIFY")
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(repeat))
        o.validate_dispatch_payload(self.runtime, state, repeat)

    # 7c. A newer consumed receipt (changed artifact) re-enables re-verification.
    def test_identical_fv_redispatch_allowed_after_newer_receipt(self):
        self.consume_passing_fv()
        self.runtime["last_consumed_message_id"] = self.fv_message_id + 1
        self.runtime["last_consumed_nonce"] = "newer-nonce"
        self.runtime["last_consumed_brief_sha256"] = "newer-brief"
        repeat = self.task(self.fv_message_id + 2)
        state = self.state_for(repeat, fv_status="REVERIFY")
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(repeat))
        o.validate_dispatch_payload(self.runtime, state, repeat)

    # 7d. Ledger-proven consumption repairs a lost runtime binding (bounded recovery).
    def test_reconcile_repairs_lost_binding_from_ledger(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        for key in (
            "last_final_verification_message_id",
            "last_final_verification_receipt_sha256",
            "last_final_verification_claims_hash",
            "last_final_verification_overall_status",
            "last_final_verification_mechanical_pass",
        ):
            self.runtime[key] = None
        probe = {"final_verification": {
            "required": True,
            "verification_message_id": self.fv_message_id,
            "verification_receipt_sha256": brief_hash,
            "claims_hash": CLAIMS_HASH,
        }}
        self.assertTrue(o.reconcile_final_verification_binding(self.runtime, probe))
        self.assertEqual(self.runtime["last_final_verification_message_id"], self.fv_message_id)
        state = self.accepted_state(task, brief_hash)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertTrue(allowed, reason)

    # 7e. Without a ledger entry there is no self-heal: fail closed.
    def test_reconcile_refuses_without_ledger_entry(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        ledger_backup = self.runtime["final_verification_receipt_ledger"]
        self.runtime["final_verification_receipt_ledger"] = []
        for key in ("last_final_verification_message_id", "last_final_verification_receipt_sha256"):
            self.runtime[key] = None
        probe = {"final_verification": {
            "required": True,
            "verification_message_id": self.fv_message_id,
            "verification_receipt_sha256": brief_hash,
            "claims_hash": CLAIMS_HASH,
        }}
        self.assertFalse(o.reconcile_final_verification_binding(self.runtime, probe))
        self.assertIsNone(self.runtime["last_final_verification_message_id"])
        state = self.accepted_state(task, brief_hash)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)
        self.assertEqual(reason, "verification_message_id does not match runtime")
        self.runtime["final_verification_receipt_ledger"] = ledger_backup

    # 7f. A newer consumed receipt disables reconciliation (the staleness rule wins).
    def test_reconcile_refuses_when_newer_receipt_was_consumed(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        for key in ("last_final_verification_message_id", "last_final_verification_receipt_sha256"):
            self.runtime[key] = None
        self.runtime["last_consumed_message_id"] = self.fv_message_id + 1
        self.runtime["last_consumed_brief_sha256"] = "newer-brief"
        probe = {"final_verification": {
            "required": True,
            "verification_message_id": self.fv_message_id,
            "verification_receipt_sha256": brief_hash,
            "claims_hash": CLAIMS_HASH,
        }}
        self.assertFalse(o.reconcile_final_verification_binding(self.runtime, probe))
        self.assertIsNone(self.runtime["last_final_verification_message_id"])

    # 8. The authoritative identity and ledger survive a restart (persisted runtime).
    def test_authoritative_identity_survives_restart(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()
        o.save_runtime(self.runtime)
        reloaded = o.load_runtime()
        self.assertEqual(reloaded["last_final_verification_message_id"], self.fv_message_id)
        self.assertEqual(reloaded["last_final_verification_receipt_sha256"], brief_hash)
        self.assertEqual(len(reloaded["final_verification_receipt_ledger"]), 1)
        state = self.accepted_state(task, brief_hash)
        allowed, reason = o.final_verification_terminal_check(reloaded, state)
        self.assertTrue(allowed, reason)

        # Reconciliation also works from the reloaded persisted ledger.
        reloaded["last_final_verification_message_id"] = None
        reloaded["last_final_verification_receipt_sha256"] = None
        probe = {"final_verification": {
            "required": True,
            "verification_message_id": self.fv_message_id,
            "verification_receipt_sha256": brief_hash,
            "claims_hash": CLAIMS_HASH,
        }}
        self.assertTrue(o.reconcile_final_verification_binding(reloaded, probe))
        self.assertEqual(reloaded["last_final_verification_message_id"], self.fv_message_id)

    # 9. The gate's verification standard is not lowered by the binding fix.
    def test_gate_standards_are_not_lowered(self):
        task, consumed, event, brief_hash = self.consume_passing_fv()

        # claims/hash tampering in the acceptance is still rejected.
        state = self.accepted_state(task, brief_hash, claims_hash="0" * 64)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state)
        self.assertFalse(allowed)
        self.assertEqual(reason, "final_verification.claims_hash mismatch")

        # A non-mechanically-passing receipt can never approve COMPLETE. Advance the
        # freshest receipt past the PASS entry first, so the anti-repeat guard (which
        # is tested separately) does not reject this second FV dispatch.
        self.runtime["last_consumed_message_id"] = self.fv_message_id + 1
        self.runtime["last_consumed_nonce"] = "newer-nonce"
        self.runtime["last_consumed_brief_sha256"] = "newer-brief"
        task2, consumed2, event2, brief_hash2 = self.consume_passing_fv(
            message_id=self.fv_message_id + 10, overall="INCONCLUSIVE"
        )
        self.assertIs(self.runtime["last_final_verification_mechanical_pass"], False)
        state2 = self.accepted_state(task2, brief_hash2, claims_hash=CLAIMS_HASH)
        state2["final_verification"]["critical_claims"] = CLAIMS
        allowed, reason = o.final_verification_terminal_check(self.runtime, state2)
        self.assertFalse(allowed)
        self.assertIn("mechanical", reason)

        # Receipt hash mismatch is still rejected (referencing the latest consumed FV
        # identity but the wrong receipt bytes).
        state3 = self.accepted_state(task2, "f" * 64)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state3)
        self.assertFalse(allowed)
        self.assertEqual(reason, "verification_receipt_sha256 does not match runtime")

    # Extra: the consume-time fallback must fail closed on a non-FV authorization.
    def test_fv_shaped_receipt_without_fv_authorization_is_ordinary(self):
        ordinary = self.ordinary_task(self.fv_message_id)
        state = self.state_for(ordinary)
        state["current_task"] = {key: ordinary[key] for key in o.IDENTITY_KEYS}
        self.authorize(ordinary, state)
        self.assertIs(self.runtime["authorized_dispatch"]["IS_FINAL_VERIFICATION"], False)
        consumed, event, _ = self.publish_receipt_and_consume(
            ordinary, self.receipt(ordinary)
        )
        self.assertTrue(consumed)
        self.assertNotIn("final_verification", event)
        self.assertIsNone(self.runtime["last_final_verification_message_id"])
        self.assertEqual(self.runtime["final_verification_receipt_ledger"], [])

    # Extra: the legacy primary path (gate mirrored on current_task) still works.
    def test_current_task_gate_mirror_remains_supported(self):
        task = self.task(self.fv_message_id)
        state = self.state_for(task)
        state["current_task"] = dict(task)  # full mirror, pre-fix supervisor style
        # A pre-binding-era authorization record carries the identity but no
        # IS_FINAL_VERIFICATION flag, so consume must fall back to the primary
        # current_task mirror. The completion commit chain still applies.
        o.atomic_json(o.PROJECT_STATE, state)
        o.atomic_write(o.TO_ZCODE, wire(task))
        self.runtime["authorized_dispatch"] = {
            "schema_version": 1,
            **{key: task[key] for key in o.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": hashlib.sha256(o.TO_ZCODE.read_bytes()).hexdigest(),
            "AUTHORIZED_AT": o.stamp(),
        }
        o.atomic_json(o.RUNTIME_STATE, self.runtime)
        consumed, event, brief_hash = self.publish_receipt_and_consume(
            task, self.receipt(task)
        )
        self.assertTrue(consumed)
        self.assertIn("final_verification", event)
        self.assertEqual(self.runtime["last_final_verification_message_id"], self.fv_message_id)
        state_complete = self.accepted_state(task, brief_hash)
        allowed, reason = o.final_verification_terminal_check(self.runtime, state_complete)
        self.assertTrue(allowed, reason)

    # Extra: the authorization record must match the consumed identity exactly.
    def test_fv_fallback_requires_matching_authorization_identity(self):
        stale = self.task(700013)
        self.runtime["authorized_dispatch"] = {
            "schema_version": 1,
            **{key: stale[key] for key in o.IDENTITY_KEYS},
            "TO_ZCODE_SHA256": "0" * 64,
            "AUTHORIZED_AT": "2026-09-05T00:00:00+00:00",
            "IS_FINAL_VERIFICATION": True,
            "FINAL_VERIFICATION_GATE": {
                "POLICY_ID": "GENERAL_FV_V1",
                "POLICY_VERSION": 1,
                "CLAIMS_HASH": CLAIMS_HASH,
                "CLAIM_COUNT": len(CLAIMS),
                "CRITICAL_CLAIMS": CLAIMS,
            },
        }
        self.assertIsNone(
            o.authorized_final_verification_task(self.runtime, {}, self.fv_message_id)
        )
        stale_brief = self.receipt(stale)
        view = o.authorized_final_verification_task(self.runtime, stale_brief, 700013)
        self.assertIsNotNone(view)
        self.assertEqual(view["FINAL_VERIFICATION_GATE"]["CLAIMS_HASH"], CLAIMS_HASH)


if __name__ == "__main__":
    unittest.main()
