"""COMPLETION-SEAL-V1 regression: authoritative completion commit / consume / seal.

Primary regression (Late Executor Republish After Supervisor Consumption):
    claim X -> staging A -> commit A -> consume A -> Supervisor lifecycle decision
    -> seal X -> the SAME Executor attempt stages B and commits again.
The republish MUST be rejected, A MUST stay intact, no new authoritative DONE may
appear, and the Supervisor must never be invoked again for identity X. A raw
bypass that rewrites the root compatibility artifacts MUST be recognized from the
authoritative ledger, quarantined, audited, and never re-consumed.

Additional fault/race coverage (numbered to match the fix report):
    1  full lifecycle commit->consume->seal          11 crash after consume/seal
    2  duplicate commit rejected (10)                12 unknown raw DONE fail closed
    3  sealed identity commit rejected (11)          13 identity mismatch fail closed
    4  consumed A then same-attempt B rejected       14 brief hash mismatch fail closed
    5  sealed raw DONE replay: no lifecycle          15 sealed replay quarantined
    6  sealed brief rewrite: receipt intact          16 HUMAN_REVIEW + sealed replay resumes
    7  sealed pointer rewrite: no lifecycle effect   17 HUMAN_REVIEW + COMMITTED blocked
    8  second process cannot commit twice            18 executor_claim semantics unchanged
    9  crash before commit: no fake completion       19 FV identity binding regression intact
    10 crash after commit: exactly-once consume      20 isolation/repair regressions intact

All fixtures run in temporary directories with synthetic identities; no real
Orchestrator, Scheduled Automation, or production identity is involved.
"""

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import executor_claim as claim_helper
import executor_completion as completion_helper
import supervisor_control as supervisor_control
import resume_human_review as resume_module


def load_orchestrator(root: Path):
    spec = importlib.util.spec_from_file_location(
        f"completion_seal_orchestrator_{root.name}", RUNTIME_ROOT / "orchestrator.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for attr, val in dict(
        ROOT=root, CONTROL=root / "control", LOGS=root / "logs",
        HANDOFF_ARCHIVE=root / "handoff" / "archive", REPORTS=root / "reports",
        PROJECT_STATE=root / "control" / "project_state.json",
        RUNTIME_STATE=root / "control" / "orchestrator_runtime.json",
        SUPERVISOR_RULES=root / "control" / "CODEX_SUPERVISOR_RUNTIME.md",
        RESEARCH_STATE=root / "RESEARCH_STATE.md",
        COMMERCIAL_GOAL=root / "control" / "CROSS_BORDER_GOAL.md",
        TO_ZCODE=root / "TO_ZCODE.md", SUPERVISOR_BRIEF=root / "SUPERVISOR_BRIEF.md",
        ZCODE_DONE=root / "ZCODE_DONE.flag",
        ZCODE_LAST_PROCESSED=root / "ZCODE_LAST_PROCESSED.txt",
        STOP_FLAG=root / "control" / "STOP", HUMAN_REVIEW_FLAG=root / "control" / "HUMAN_REVIEW",
        LOCK_FILE=root / "control" / ".orchestrator.lock",
        CODEX_LAST_OUTPUT=root / "CODEX_LAST_OUTPUT.txt",
        USER_ATTENTION=root / "control" / "USER_ATTENTION.json",
        USER_STATUS_REPORT=root / "reports" / "USER_STATUS.md",
        ACTIVE_PROJECT_FILE=root / "control" / "ACTIVE_PROJECT.json",
    ).items():
        setattr(module, attr, val)
    module.DESKTOP_NOTIFICATIONS_ENABLED = False
    module.USER_NOTIFICATION_CONSOLE_ENABLED = False
    return module


def wire(value):
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


IDENTITY_KEYS = completion_helper.IDENTITY_KEYS


class CompletionSealTests(unittest.TestCase):
    PROJECT_ID = "seal-proj"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="completion-seal-")
        self.root = Path(self.temp.name)
        for relative in ("control", "logs", "handoff/archive", "handoff/executor_claims", "reports"):
            (self.root / relative).mkdir(parents=True)
        self.o = load_orchestrator(self.root)
        self.project_root = self.root / "projects" / self.PROJECT_ID
        self.project_root.mkdir(parents=True)
        (self.project_root / "PROJECT_GOAL.md").write_text("# goal\n", encoding="utf-8")
        (self.project_root / "RESEARCH_STATE.md").write_text("# memory\n", encoding="utf-8")
        (self.root / "profiles").mkdir()
        (self.root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps({
            "schema_version": 1, "project_id": self.PROJECT_ID,
            "project_root": f"projects/{self.PROJECT_ID}",
        }), encoding="utf-8")
        self.o.ACTIVE_PROJECT = {"project_id": self.PROJECT_ID,
                                 "project_root": f"projects/{self.PROJECT_ID}"}
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text("800099\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    # ------------------------------------------------------------------ fixtures

    def seed_dispatch(self, message_id=800100, task_id="T-SEAL", stage_id="S-SEAL",
                      attempt=1, nonce=None, status="WAITING_EXECUTOR", fv=False):
        """Publish the dispatch candidate, authorize it, and bind the live state."""
        nonce = nonce or f"nonce-{message_id}"
        identity = {
            "MESSAGE_ID": message_id, "TASK_ID": task_id, "STAGE_ID": stage_id,
            "ATTEMPT": attempt, "NONCE": nonce,
        }
        task = {"PROTOCOL_VERSION": 2, "CLAIM_PROTOCOL_VERSION": 1, **identity,
                "OBJECTIVE": "fixture", "OUTPUTS": []}
        if fv:
            task["TASK_KIND"] = "FINAL_VERIFICATION"
            task["FINAL_VERIFICATION_GATE"] = {
                "POLICY_ID": "GENERAL_FV_V1", "POLICY_VERSION": 1,
                "CLAIMS_HASH": "0" * 64, "CLAIM_COUNT": 3, "CRITICAL_CLAIMS": [],
            }
        (self.root / "TO_ZCODE.md").write_text(wire(task), encoding="utf-8")
        authorization = {
            "schema_version": 1, **identity,
            "TO_ZCODE_SHA256": hashlib.sha256((self.root / "TO_ZCODE.md").read_bytes()).hexdigest(),
            "AUTHORIZED_AT": "2030-01-01T00:00:00+00:00",
        }
        origin = {"originating_control_revision": 0,
                  "supervisor_turn_id": f"completion-fixture-{message_id}",
                  "decision_receipt_sha256": "a" * 64}
        binding = supervisor_control.archive_dispatch(
            self.root, self.PROJECT_ID, task,
            (self.root / "TO_ZCODE.md").read_bytes(), origin=origin)
        authorization.update({
            "PROJECT_ID": self.PROJECT_ID,
            "SUPERVISOR_CONTROL_ORIGIN": origin,
            "SUPERVISOR_DISPATCH_ARCHIVE": {
                key: binding[key] for key in ("schema_version", "metadata_file",
                                               "archive_file", "authorization_file",
                                               "dispatch_sha256")
            },
        })
        supervisor_control.seal_dispatch_authorization(self.root, authorization)
        if fv:
            authorization["IS_FINAL_VERIFICATION"] = True
            authorization["FINAL_VERIFICATION_GATE"] = task["FINAL_VERIFICATION_GATE"]
        self.o.atomic_json(self.o.RUNTIME_STATE, {
            "authorized_dispatch": authorization, "retired_message_ids": [],
        })
        state = {
            "schema_version": 4, "project_id": self.PROJECT_ID, "profile": "GENERAL",
            "status": status, "current_task": None,
            "final_verification": {"required": True, "status": "PENDING",
                                   "policy_id": "GENERAL_FV_V1", "policy_version": 1},
        }
        if status == "WAITING_EXECUTOR":
            state["current_task"] = dict(identity)
        (self.project_root / "project_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        # Isolated mode: the orchestrator rebinds PROJECT_STATE to the project root.
        self.assertIs(self.o.activate_project_scope() is not None, True)
        self.assertEqual(self.o.PROJECT_STATE, self.project_root / "project_state.json")
        self.runtime = {
            "schema_version": 2, "status": "RUNNING",
            "last_consumed_message_id": 800099, "last_consumed_nonce": "prior-nonce",
            "last_consumed_brief_sha256": "prior-brief",
            "last_dispatched_message_id": message_id, "last_dispatched_nonce": nonce,
            "authorized_dispatch": dict(authorization), "retired_message_ids": [],
            "executor_receipts_consumed": 0, "stale_receipts_ignored": 0,
            "protocol_errors": 0, "final_verification_receipt_ledger": [],
            "last_final_verification_message_id": None,
        }
        return identity, authorization

    def acquire(self, identity):
        code = claim_helper.acquire(
            self.root, identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
            identity["ATTEMPT"], identity["NONCE"],
        )
        self.assertEqual(code, claim_helper.EXIT_ACQUIRED)

    def make_receipt(self, identity, marker="A", status="COMPLETED"):
        return {
            "PROTOCOL_VERSION": 2, **identity, "STATUS": status,
            "Objective": f"stage objective {marker}",
            "Key findings": [f"finding {marker}"],
            "Recommended next action": "Supervisor review",
        }

    def make_staging(self, identity, receipt, name=None):
        staging_dir = (self.project_root / "completion_staging"
                       / (name or f"stage-{identity['MESSAGE_ID']}-{receipt['Objective'][-1]}"))
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging = {
            "COMPLETION_STAGING_SCHEMA_VERSION": completion_helper.COMPLETION_STAGING_SCHEMA_VERSION,
            **identity, "PROJECT_ID": self.PROJECT_ID, "STATUS": "STAGING_READY",
            "CREATED_AT": "2030-01-01T00:00:00+00:00", "RECEIPT": receipt,
        }
        (staging_dir / "staging.json").write_text(
            json.dumps(staging, ensure_ascii=False, indent=2), encoding="utf-8")
        return staging_dir

    def commit(self, identity, receipt, name=None):
        """Commit via the helper; returns the exit code (catching typed rejections)."""
        try:
            return completion_helper.commit(self.root, self.make_staging(identity, receipt, name))
        except completion_helper.CompletionError as exc:
            return exc.code

    def entry(self, message_id, nonce):
        entries = completion_helper.lookup_entries(self.root, message_id)
        return entries[0] if entries else None

    def audit_events(self, event_name):
        audit = self.root / "handoff" / "completion_ledger" / "audit.jsonl"
        if not audit.exists():
            return []
        return [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()
                if line.strip() and json.loads(line).get("event") == event_name]

    def quarantine_dirs(self):
        base = self.root / "handoff" / "quarantine"
        return sorted(path.name for path in base.glob("completion-*")) if base.exists() else []

    def consume(self):
        return self.o.consume_executor_receipt(self.runtime)

    def reseed_live_identity(self, message_id, nonce, status="WAITING_EXECUTOR"):
        """Advance the live lifecycle identity (e.g. the Supervisor's next dispatch)."""
        state = json.loads(self.o.PROJECT_STATE.read_text(encoding="utf-8-sig"))
        state["status"] = status
        state["current_task"] = (
            None if status != "WAITING_EXECUTOR"
            else {"MESSAGE_ID": message_id, "TASK_ID": "T-NEXT", "STAGE_ID": "S-NEXT",
                  "ATTEMPT": 1, "NONCE": nonce}
        )
        self.o.atomic_json(self.o.PROJECT_STATE, state)

    # ------------------------------------------------- primary regression (req 13)

    def test_late_executor_republish_after_supervisor_consumption(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        receipt_a = self.make_receipt(identity, marker="A")
        self.assertEqual(self.commit(identity, receipt_a), completion_helper.EXIT_COMMITTED)
        entry_a = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(entry_a["STATUS"], completion_helper.STATUS_COMMITTED)

        # Supervisor consumes A and makes its lifecycle decision (next identity).
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(event["commit_id"], entry_a["COMMIT_ID"])
        self.reseed_live_identity(identity["MESSAGE_ID"] + 1, "nonce-next")
        self.assertEqual(self.o.seal_completions(self.runtime, self.o.read_project_state()), 1)
        sealed = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(sealed["STATUS"], completion_helper.STATUS_SEALED)

        # The still-running Executor attempt stages B (different receipt) and commits.
        receipt_b = self.make_receipt(identity, marker="B")
        self.assertEqual(self.commit(identity, receipt_b), completion_helper.EXIT_COMPLETION_SEALED)
        after = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(after["RECEIPT_SHA256"], sealed["RECEIPT_SHA256"])  # A intact
        self.assertEqual(after["STATUS"], completion_helper.STATUS_SEALED)
        self.assertFalse(self.o.ZCODE_DONE.exists())  # no new authoritative DONE

        # The Executor bypasses the helper and rewrites the root artifacts directly.
        self.o.atomic_write(self.o.SUPERVISOR_BRIEF, wire(receipt_b))
        self.o.atomic_write(self.o.ZCODE_LAST_PROCESSED,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        with patch.object(self.o, "invoke_codex") as codex:
            seen2, event2 = self.consume()
            codex.assert_not_called()
        self.assertTrue(seen2)
        self.assertIsNone(event2)  # no new Supervisor lifecycle event
        self.assertEqual(self.runtime["stale_receipts_ignored"], 1)
        self.assertEqual(len(self.quarantine_dirs()), 1)
        self.assertEqual(len(self.audit_events("COMPLETION_ARTIFACTS_QUARANTINED")), 1)
        # The authoritative receipt survived the rewrite untouched.
        final = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(final["RECEIPT_SHA256"], completion_helper.canonical_json_sha256(receipt_a))
        self.assertTrue(completion_helper.entry_hashes_intact(final))

    # ------------------------------------------------------------------- 1..4

    def test_1_full_lifecycle_commit_consume_seal(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        receipt = self.make_receipt(identity)
        self.assertEqual(self.commit(identity, receipt), 0)
        entry = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(entry["STATUS"], completion_helper.STATUS_COMMITTED)
        self.assertEqual((self.root / "SUPERVISOR_BRIEF.md").read_text(encoding="utf-8"),
                         completion_helper.render_brief_text(receipt))
        pointer = (self.root / "ZCODE_LAST_PROCESSED.txt").read_text(encoding="utf-8")
        self.assertIn(f"MESSAGE_ID={identity['MESSAGE_ID']}", pointer)
        self.assertIn(f"NONCE={identity['NONCE']}", pointer)
        done = (self.root / "ZCODE_DONE.flag").read_text(encoding="utf-8")
        self.assertIn(f"COMPLETION_COMMIT_ID={entry['COMMIT_ID']}", done)
        self.assertEqual(entry["PROJECT_ID"], self.PROJECT_ID)

        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(self.runtime["last_consumed_message_id"], identity["MESSAGE_ID"])
        self.assertEqual(self.runtime["last_consumed_nonce"], identity["NONCE"])
        consumed = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(consumed["STATUS"], completion_helper.STATUS_CONSUMED)
        self.assertTrue(self.o.ZCODE_LAST_PROCESSED.exists())
        self.assertFalse(self.o.ZCODE_DONE.exists())
        archive = self.root / "handoff" / "archive" / Path(consumed["CONSUMED_ARCHIVE"]).name
        self.assertTrue(archive.exists())
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                         consumed["BRIEF_SHA256"])

        self.reseed_live_identity(identity["MESSAGE_ID"] + 1, "nonce-next")
        self.assertEqual(self.o.seal_completions(self.runtime, self.o.read_project_state()), 1)
        self.assertEqual(self.entry(identity["MESSAGE_ID"], identity["NONCE"])["STATUS"],
                         completion_helper.STATUS_SEALED)
        self.assertEqual(len(self.audit_events("COMPLETION_COMMITTED")), 1)
        self.assertEqual(len(self.audit_events("COMPLETION_CONSUMED")), 1)
        self.assertEqual(len(self.audit_events("COMPLETION_SEALED")), 1)

    def test_2_duplicate_commit_rejected(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        receipt = self.make_receipt(identity)
        self.assertEqual(self.commit(identity, receipt), 0)
        before = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        again = self.make_staging(identity, receipt, name="stage-duplicate")
        self.assertEqual(self.commit(identity, receipt, name="stage-duplicate"),
                         completion_helper.EXIT_ALREADY_COMMITTED)
        after = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(after["COMMITTED_AT"], before["COMMITTED_AT"])
        self.assertEqual(after["RECEIPT_SHA256"], before["RECEIPT_SHA256"])

    def test_3_sealed_identity_commit_rejected(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        self.consume()
        entry = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        entry["STATUS"] = completion_helper.STATUS_SEALED
        entry["SEALED_AT"] = "2030-01-01T01:00:00+00:00"
        completion_helper.save_entry(self.root, entry)
        self.assertEqual(self.commit(identity, self.make_receipt(identity, marker="B")),
                         completion_helper.EXIT_COMPLETION_SEALED)

    def test_4_consumed_identity_commit_rejected_and_a_intact(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        receipt_a = self.make_receipt(identity, marker="A")
        self.assertEqual(self.commit(identity, receipt_a), 0)
        self.consume()
        # Same attempt stages a different completion B after A was consumed.
        self.assertEqual(self.commit(identity, self.make_receipt(identity, marker="B")),
                         completion_helper.EXIT_COMPLETION_SEALED)
        entry = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(entry["RECEIPT_SHA256"], completion_helper.canonical_json_sha256(receipt_a))
        self.assertEqual(entry["STATUS"], completion_helper.STATUS_CONSUMED)

    # ------------------------------------------------------------------- 5..7

    def test_5_sealed_raw_done_replay_does_not_drive_lifecycle(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        self.consume()
        self.reseed_live_identity(identity["MESSAGE_ID"] + 1, "nonce-next")
        self.o.seal_completions(self.runtime, self.o.read_project_state())
        self.o.atomic_write(self.o.ZCODE_DONE, f"{identity['MESSAGE_ID']} late replay\n")
        with patch.object(self.o, "invoke_codex") as codex:
            seen, event = self.consume()
            codex.assert_not_called()
        self.assertTrue(seen)
        self.assertIsNone(event)
        self.assertEqual(self.runtime["last_consumed_message_id"], identity["MESSAGE_ID"])
        self.assertFalse(self.o.ZCODE_DONE.exists())

    def test_6_sealed_brief_rewrite_does_not_touch_authoritative_receipt(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        receipt = self.make_receipt(identity)
        self.assertEqual(self.commit(identity, receipt), 0)
        self.consume()
        self.reseed_live_identity(identity["MESSAGE_ID"] + 1, "nonce-next")
        self.o.seal_completions(self.runtime, self.o.read_project_state())
        self.o.atomic_write(self.o.SUPERVISOR_BRIEF, "TAMPERED BRIEF\n")
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertIsNone(event)
        entry = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        self.assertEqual(entry["RECEIPT_SHA256"], completion_helper.canonical_json_sha256(receipt))
        self.assertTrue(completion_helper.entry_hashes_intact(entry))

    def test_7_sealed_pointer_rewrite_has_no_lifecycle_effect(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        self.consume()
        # A replay rewinds the derived processed pointer.
        self.o.atomic_write(self.o.ZCODE_LAST_PROCESSED, "1\n")
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertIsNone(event)
        # The authoritative consume bookkeeping is untouched by the rewind.
        self.assertEqual(self.runtime["last_consumed_message_id"], identity["MESSAGE_ID"])
        self.assertEqual(self.runtime["last_consumed_nonce"], identity["NONCE"])
        self.assertFalse(self.o.ZCODE_DONE.exists())

    # ---------------------------------------------------------------------- 8

    def test_8_second_process_cannot_produce_second_completion(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        receipt = self.make_receipt(identity)
        staging_one = self.make_staging(identity, receipt, name="stage-proc-one")
        staging_two = self.make_staging(identity, receipt, name="stage-proc-two")
        helper = RUNTIME_ROOT / "scripts" / "executor_completion.py"
        commands = [
            [sys.executable, str(helper), "commit", "--root", str(self.root),
             "--staging-dir", str(staging_dir)]
            for staging_dir in (staging_one, staging_two)
        ]
        processes = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                      text=True) for cmd in commands]
        codes = sorted(process.wait() for process in processes)
        self.assertEqual(codes, [0, completion_helper.EXIT_ALREADY_COMMITTED])
        self.assertEqual(len(completion_helper.lookup_entries(self.root, identity["MESSAGE_ID"])), 1)

    # ------------------------------------------------------------------ 9..11

    def test_9_claim_crash_before_commit_fabricates_nothing(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.make_staging(identity, self.make_receipt(identity))  # staged, never committed
        # No commit => no ledger entry, no DONE, no consume, no Supervisor event.
        seen, event = self.consume()
        self.assertFalse(seen)
        self.assertIsNone(event)
        self.assertIsNone(self.entry(identity["MESSAGE_ID"], identity["NONCE"]))
        self.assertFalse(self.o.ZCODE_DONE.exists())
        self.assertEqual(self.runtime["last_consumed_message_id"], 800099)

    def test_10_commit_crash_before_consume_still_consumes_exactly_once(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        # Crash window A: the helper died before publishing the DONE wake hint.
        self.o.ZCODE_DONE.unlink()
        runtime = self.o.load_runtime()
        self.o.reconcile_completion_ledger(runtime, self.o.read_project_state())
        self.assertTrue(self.o.ZCODE_DONE.exists())  # artifacts repaired from the ledger
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        # Crash window B: the ledger transition survived but the pointer write did not.
        self.o.atomic_json(self.o.RUNTIME_STATE, {
            "authorized_dispatch": self.runtime["authorized_dispatch"],
            "retired_message_ids": [],
            "last_consumed_message_id": 800099, "last_consumed_nonce": "prior-nonce",
            "last_consumed_brief_sha256": "prior-brief",
            "last_dispatched_message_id": identity["MESSAGE_ID"],
            "last_dispatched_nonce": identity["NONCE"],
        })
        entry = self.entry(identity["MESSAGE_ID"], identity["NONCE"])
        entry["STATUS"] = completion_helper.STATUS_CONSUMED
        entry["CONSUMED_AT"] = "2030-01-01T01:00:00+00:00"
        completion_helper.save_entry(self.root, entry)
        runtime = self.o.load_runtime()
        self.o.reconcile_completion_ledger(runtime, self.o.read_project_state())
        self.assertEqual(runtime["last_consumed_message_id"], identity["MESSAGE_ID"])
        self.assertEqual(runtime["last_consumed_nonce"], identity["NONCE"])
        # The recovered pointers drive exactly one (and only one) F17 replay event.
        current = self.o.read_project_state()["current_task"]
        replay = self.o.replay_consumed_receipt_event(runtime, current)
        self.assertIsNotNone(replay)
        self.assertEqual(replay["commit_id"], entry["COMMIT_ID"])
        self.assertTrue(replay["replayed_after_crash"])
        # A raw DONE replay afterwards is still only a quarantine, never a re-consume.
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        seen2, event2 = self.o.consume_executor_receipt(runtime)
        self.assertTrue(seen2)
        self.assertIsNone(event2)

    def test_11_consumed_sealed_crash_cannot_second_decision(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        self.consume()
        self.reseed_live_identity(identity["MESSAGE_ID"] + 1, "nonce-next")
        runtime = self.o.load_runtime()
        self.o.reconcile_completion_ledger(runtime, self.o.read_project_state())
        self.assertEqual(self.entry(identity["MESSAGE_ID"], identity["NONCE"])["STATUS"],
                         completion_helper.STATUS_SEALED)
        # Restart after the decision: no replay event may exist for a SEALED entry.
        current = {"MESSAGE_ID": identity["MESSAGE_ID"] + 1, "TASK_ID": "T-NEXT",
                   "STAGE_ID": "S-NEXT", "ATTEMPT": 1, "NONCE": "nonce-next"}
        self.assertIsNone(self.o.replay_consumed_receipt_event(runtime, current))
        # Late raw DONE for the sealed identity: quarantined, no lifecycle change.
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        seen, event = self.o.consume_executor_receipt(runtime)
        self.assertTrue(seen)
        self.assertIsNone(event)
        self.assertEqual(runtime["stale_receipts_ignored"], 1)

    # ----------------------------------------------------------------- 12..15

    def test_12_unknown_raw_done_fails_closed(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.o.atomic_write(self.o.ZCODE_DONE, "800777 unknown raw completion\n")
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertIsNone(event)
        self.assertEqual(self.runtime["protocol_errors"], 1)
        self.assertEqual(self.runtime["last_consumed_message_id"], 800099)
        self.assertEqual(len(self.quarantine_dirs()), 1)

    def test_13_root_identity_mismatch_fails_closed(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        # A forged wake hint declares the right MESSAGE_ID with the wrong NONCE.
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE=forged\n")
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertIsNone(event)
        self.assertEqual(self.runtime["protocol_errors"], 1)
        self.assertEqual(self.runtime["last_consumed_message_id"], 800099)
        self.assertEqual(self.entry(identity["MESSAGE_ID"], identity["NONCE"])["STATUS"],
                         completion_helper.STATUS_COMMITTED)

    def test_15_sealed_replay_is_quarantined_and_audited(self):
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        self.assertEqual(self.commit(identity, self.make_receipt(identity)), 0)
        self.consume()
        self.reseed_live_identity(identity["MESSAGE_ID"] + 1, "nonce-next")
        self.o.seal_completions(self.runtime, self.o.read_project_state())
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={identity['MESSAGE_ID']}\nNONCE={identity['NONCE']}\n")
        seen, event = self.consume()
        self.assertTrue(seen)
        self.assertIsNone(event)
        quarantined = self.quarantine_dirs()
        self.assertEqual(len(quarantined), 1)
        self.assertTrue((self.root / "handoff" / "quarantine" / quarantined[0]
                         / "ZCODE_DONE.flag").exists())
        self.assertEqual(len(self.audit_events("COMPLETION_ARTIFACTS_QUARANTINED")), 1)

    # ----------------------------------------------------------------- 16..17

    def _resume_fixture(self, entry_status):
        """HUMAN_REVIEW project with a fully consumed last identity + ledger record."""
        message_id, nonce = 800099, "prior-nonce"
        identity = {
            "MESSAGE_ID": message_id, "TASK_ID": "T-LAST", "STAGE_ID": "S-LAST",
            "ATTEMPT": 1, "NONCE": nonce,
        }
        receipt = self.make_receipt(identity)
        entry = {
            "COMPLETION_PROTOCOL_VERSION": 1,
            "COMMIT_ID": completion_helper.commit_id_for(message_id, nonce),
            "STATUS": entry_status,
            **identity,
            "PROJECT_ID": self.PROJECT_ID,
            "CLAIM_DIR": f"handoff/executor_claims/{message_id}-x.claim",
            "CLAIM_IDENTITY_SHA256": completion_helper.canonical_json_sha256(identity),
            "RECEIPT_SHA256": completion_helper.canonical_json_sha256(receipt),
            "BRIEF_SHA256": completion_helper.sha256_bytes(
                completion_helper.render_brief_bytes(receipt)),
            "STAGING_MANIFEST_SHA256": "0" * 64,
            "COMMITTED_AT": "2030-01-01T00:00:00+00:00",
            "CONSUMED_AT": None if entry_status == completion_helper.STATUS_COMMITTED
            else "2030-01-01T00:30:00+00:00",
            "SEALED_AT": None if entry_status == completion_helper.STATUS_COMMITTED
            else "2030-01-01T01:00:00+00:00",
            "CONSUMED_ARCHIVE": None,
            "RECEIPT": receipt,
        }
        completion_helper._atomic_create(
            completion_helper.entry_path(self.root, entry["COMMIT_ID"]),
            json.dumps(entry, ensure_ascii=False, indent=2) + "\n")
        # consumed archive + claim backing the quiescence checks
        archive = (self.root / "handoff" / "archive"
                   / f"brief-{message_id}-{nonce[:12]}-consumed-{entry['BRIEF_SHA256'][:12]}.md")
        archive.write_bytes(completion_helper.render_brief_bytes(receipt))
        claim_dir = self.root / "handoff" / "executor_claims" / f"{message_id}-x.claim"
        claim_dir.mkdir(exist_ok=True)
        (claim_dir / "claim.json").write_text(
            json.dumps({"MESSAGE_ID": message_id, "NONCE": nonce}), encoding="utf-8")
        # raw replay wake hint bound to the committed identity
        self.o.atomic_write(self.o.ZCODE_DONE,
                            f"MESSAGE_ID={message_id}\nNONCE={nonce}\n")
        return identity, entry

    def _resume_state_setup(self, identity, entry):
        """Quiescent HUMAN_REVIEW state + runtime bound to the consumed identity."""
        state = {
            "schema_version": 4, "project_id": self.PROJECT_ID, "profile": "GENERAL",
            "status": "HUMAN_REVIEW", "current_task": None,
            "next_message_id": 800101, "final_verification": {"required": False},
            "infrastructure_status": "READY", "deadline_at": None,
            "human_decision_consumption_ledger": [], "human_review_resume": None,
            "last_supervisor_decision": "HUMAN_REVIEW — fixture",
        }
        (self.project_root / "project_state.json").write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        task = {"PROTOCOL_VERSION": 2, **identity, "OBJECTIVE": "fixture", "OUTPUTS": []}
        (self.root / "TO_ZCODE.md").write_text(wire(task), encoding="utf-8")
        runtime = {
            "schema_version": 2, "status": "HUMAN_REVIEW",
            "last_consumed_message_id": identity["MESSAGE_ID"],
            "last_consumed_nonce": identity["NONCE"],
            "last_consumed_brief_sha256": entry["BRIEF_SHA256"],
            "last_dispatched_message_id": identity["MESSAGE_ID"],
            "last_dispatched_nonce": identity["NONCE"],
            "authorized_dispatch": {
                "schema_version": 1, **identity,
                "TO_ZCODE_SHA256": hashlib.sha256(
                    (self.root / "TO_ZCODE.md").read_bytes()).hexdigest(),
                "AUTHORIZED_AT": "2030-01-01T00:00:00+00:00",
            },
            "retired_message_ids": [],
            "claim_protocol_required_from_message_id": 800001,
        }
        self.o.atomic_json(self.o.RUNTIME_STATE, runtime)

    def _resume_flow(self):
        """prepare + apply through the resume module; returns (prepare, apply, message)."""
        module = resume_module.load_runtime_module(RUNTIME_ROOT)
        resume_module.bind_runtime_paths(module, self.root)
        module.PROFILES_DIR = RUNTIME_ROOT / "profiles"
        decision_path = self.root / "decision.json"
        decision_path.write_text(json.dumps({
            "decision_content": "Continue with a bounded synthetic stage.",
            "constraints_verbatim": ["Synthetic fixture constraint."],
        }), encoding="utf-8")
        receipt_out = self.root / "prepared-decision.json"
        try:
            payload = resume_module.validate_decision_payload(
                resume_module.read_json_strict(decision_path, "decision input"))
            resume_module.prepare_receipt(
                module, project_id=self.PROJECT_ID, decision_payload=payload,
                receipt_out=receipt_out)
        except resume_module.ResumeError as exc:
            return exc.code, exc.code, str(exc)
        try:
            resume_module.apply_receipt(module, receipt_path=receipt_out)
        except resume_module.ResumeError as exc:
            return 0, exc.code, str(exc)
        return 0, resume_module.EXIT_OK, ""

    def test_14_brief_hash_mismatch_fail_closed_on_resume(self):
        identity, entry = self._resume_fixture(completion_helper.STATUS_SEALED)
        # A late republish rewrote the root brief before the resume attempt.
        self.o.atomic_write(self.o.SUPERVISOR_BRIEF, "REWRITTEN BRIEF\n")
        self._resume_state_setup(identity, entry)
        prepare, apply_code, apply_message = self._resume_flow()
        self.assertEqual(prepare, 0)
        self.assertEqual(apply_code, resume_module.EXIT_CONFLICT)
        self.assertIn("diverges", apply_message)

    def test_16_resume_with_known_sealed_replay_succeeds(self):
        identity, entry = self._resume_fixture(completion_helper.STATUS_SEALED)
        self._resume_state_setup(identity, entry)
        prepare, apply_code, apply_message = self._resume_flow()
        self.assertEqual(prepare, 0)
        self.assertEqual(apply_code, resume_module.EXIT_OK, apply_message)
        self.assertFalse(self.o.ZCODE_DONE.exists())  # replay artifacts quarantined
        self.assertEqual(len(self.quarantine_dirs()), 1)

    def test_17_resume_with_genuine_unconsumed_commit_blocked(self):
        identity, entry = self._resume_fixture(completion_helper.STATUS_COMMITTED)
        self._resume_state_setup(identity, entry)
        prepare, apply_code, apply_message = self._resume_flow()
        self.assertEqual(prepare, 0)
        self.assertEqual(apply_code, resume_module.EXIT_CONFLICT)
        self.assertIn("genuine unfinished completion", apply_message)

    # ----------------------------------------------------------------- 18..20

    def test_18_executor_claim_semantics_unchanged(self):
        self.assertEqual(claim_helper.EXIT_ACQUIRED, 0)
        self.assertEqual(claim_helper.EXIT_CLAIM_EXISTS, 10)
        self.assertEqual(claim_helper.EXIT_ALREADY_PROCESSED, 11)
        self.assertEqual(claim_helper.EXIT_ERROR, 12)
        identity, _ = self.seed_dispatch()
        self.acquire(identity)
        second = claim_helper.acquire(
            self.root, identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
            identity["ATTEMPT"], identity["NONCE"])
        self.assertEqual(second, claim_helper.EXIT_CLAIM_EXISTS)
        stale = claim_helper.acquire(self.root, 800099, "T0", "S0", 1, "nonce-old")
        self.assertEqual(stale, claim_helper.EXIT_ALREADY_PROCESSED)

    def test_19_fv_identity_binding_regression_intact(self):
        self.assertTrue((SCRIPTS / "test_fv_identity_binding_fix.py").is_file())
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "test_fv_identity_binding_fix.py")],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])

    def test_20_isolation_and_repair_regressions_intact(self):
        for name in ("test_g3_project_isolation.py", "test_dispatch_validation_recovery.py",
                     "test_instance_isolation.py"):
            result = subprocess.run([sys.executable, str(SCRIPTS / name)],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr[-2000:]}")


if __name__ == "__main__":
    unittest.main()
