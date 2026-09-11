import importlib.util
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import executor_claim as claim_helper
import executor_completion as completion_helper
import supervisor_control as supervisor_helper

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator_v2", MODULE_PATH)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


def wire(value):
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


def archived_authorization(root, identity, data, project_id=None):
    """Create a tokenless completion-test authorization with a real v1.2 archive."""
    origin = {
        "originating_control_revision": supervisor_helper.load_control(root)["revision"],
        "supervisor_turn_id": f"fixture-{identity['MESSAGE_ID']}-{identity['NONCE']}",
        "decision_receipt_sha256": hashlib.sha256(data + b"fixture-origin").hexdigest(),
    }
    binding = supervisor_helper.archive_dispatch(
        root, project_id, identity, data, origin=origin)
    authorization = {
        "schema_version": 1, **identity,
        "TO_ZCODE_SHA256": hashlib.sha256(data).hexdigest(),
        "PROJECT_ID": project_id,
        "AUTHORIZED_AT": o.stamp(),
        "SUPERVISOR_CONTROL_ORIGIN": origin,
        "SUPERVISOR_DISPATCH_ARCHIVE": {
            key: binding[key] for key in (
                "schema_version", "metadata_file", "archive_file",
                "authorization_file", "dispatch_sha256")
        },
    }
    supervisor_helper.seal_dispatch_authorization(root, authorization)
    return authorization


class OrchestratorMechanicalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="agent-orchestrator-v2-")
        self.root = Path(self.temp.name)
        self._patch_root(self.root)
        for p in [o.CONTROL, o.LOGS, o.HANDOFF_ARCHIVE, o.REPORTS, self.root / "workspace"]:
            p.mkdir(parents=True, exist_ok=True)
        o.DESKTOP_NOTIFICATIONS_ENABLED = False
        o.USER_NOTIFICATION_CONSOLE_ENABLED = False
        (self.root / "ZCODE_LAST_PROCESSED.txt").write_text("602\n", encoding="utf-8")
        self.base_state = {
            "schema_version": 2,
            "status": "WAITING_EXECUTOR",
            "phase": "INFRASTRUCTURE_TEST_SUITE_V2",
            "infrastructure_status": "TESTING",
            "started_at": o.stamp(),
            "deadline_at": "2099-01-01T00:00:00+00:00",
            "current_task": {
                "MESSAGE_ID": 700001,
                "TASK_ID": "T-A",
                "STAGE_ID": "INFRA-A-NORMAL",
                "ATTEMPT": 1,
                "NONCE": "nonce-a",
                "ISSUED_AT": o.stamp(),
                "MAX_TIME": 900,
            },
        }
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        self.runtime = {
            "schema_version": 2,
            "last_consumed_message_id": 602,
            "last_consumed_nonce": None,
            "last_consumed_brief_sha256": None,
            "last_dispatched_message_id": 700001,
            "last_dispatched_nonce": "nonce-a",
            "executor_receipts_consumed": 0,
            "stale_receipts_ignored": 0,
            "protocol_errors": 0,
        }

    def tearDown(self):
        self.temp.cleanup()

    def _patch_root(self, root):
        o.ROOT = root
        o.CONTROL = root / "control"
        o.LOGS = root / "logs"
        o.HANDOFF_ARCHIVE = root / "handoff" / "archive"
        o.REPORTS = root / "reports"
        o.PROJECT_STATE = o.CONTROL / "project_state.json"
        o.RUNTIME_STATE = o.CONTROL / "orchestrator_runtime.json"
        o.SUPERVISOR_RULES = o.CONTROL / "CODEX_SUPERVISOR_RUNTIME.md"
        o.INFRA_PLAN = o.CONTROL / "INFRA_TEST_PLAN.md"
        o.RESEARCH_STATE = root / "RESEARCH_STATE.md"
        o.COMMERCIAL_GOAL = o.CONTROL / "CROSS_BORDER_GOAL.md"
        o.TO_ZCODE = root / "TO_ZCODE.md"
        o.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
        o.ZCODE_DONE = root / "ZCODE_DONE.flag"
        o.ZCODE_LAST_PROCESSED = root / "ZCODE_LAST_PROCESSED.txt"
        o.STOP_FLAG = o.CONTROL / "STOP"
        o.HUMAN_REVIEW_FLAG = o.CONTROL / "HUMAN_REVIEW"
        o.LOCK_FILE = o.CONTROL / ".orchestrator.lock"
        o.CODEX_LAST_OUTPUT = root / "CODEX_LAST_OUTPUT.txt"
        o.USER_ATTENTION = o.CONTROL / "USER_ATTENTION.json"
        o.USER_STATUS_REPORT = o.REPORTS / "USER_STATUS.md"
        # G5A.5.1: main() reads the active-project pointer via this path; it MUST resolve
        # inside the temp fixture root so tests never see the real candidate pointer.
        o.ACTIVE_PROJECT_FILE = o.CONTROL / "ACTIVE_PROJECT.json"

    def commit_receipt(self, **changes):
        """COMPLETION-SEAL-V1 fixture: drive the legal claim -> staging -> commit chain.

        The Runtime completion helper manufactures the ledger entry and the root
        compatibility artifacts; consume_executor_receipt only accepts completions
        backed by that authoritative record.
        """
        task = self.base_state["current_task"]
        identity = {
            key: o.task_value(task, key)
            for key in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")
        }
        brief = {
            "PROTOCOL_VERSION": 2,
            "MESSAGE_ID": o.task_value(task, "MESSAGE_ID"),
            "TASK_ID": o.task_value(task, "TASK_ID"),
            "STAGE_ID": o.task_value(task, "STAGE_ID"),
            "ATTEMPT": o.task_value(task, "ATTEMPT"),
            "NONCE": o.task_value(task, "NONCE"),
            "EXECUTOR_MODEL_FAMILY": "GLM-5.3",
            "RUNTIME_MODEL": "GLM-5.3-Flash",
            "STATUS": "COMPLETED",
            "Objective": "fixture",
            "Key findings": ["fixture"],
            "Core metrics": {},
            "What we can conclude": ["fixture"],
            "What we cannot conclude": [],
            "Problems / uncertainty": [],
            "Work performed": ["fixture"],
            "Failed methods": [],
            "Recommended next action": "Supervisor review",
            "Evidence pointers": [],
            "Resource / efficiency note": "fixture",
            "Deliverables": [],
            "Acceptance self-check": {},
            "STARTED_AT": o.stamp(),
            "FINISHED_AT": o.stamp(),
        }
        brief.update(changes)
        payload = {"PROTOCOL_VERSION": 2, **identity, "OBJECTIVE": "fixture", "OUTPUTS": []}
        o.atomic_write(o.TO_ZCODE, wire(payload))
        authorization = archived_authorization(o.ROOT, identity, o.TO_ZCODE.read_bytes())
        o.atomic_json(o.RUNTIME_STATE, {
            "authorized_dispatch": authorization,
            "retired_message_ids": [],
        })
        self.runtime["authorized_dispatch"] = authorization
        self.assertEqual(
            claim_helper.acquire(
                o.ROOT,
                identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
                identity["ATTEMPT"], identity["NONCE"],
            ),
            claim_helper.EXIT_ACQUIRED,
        )
        staging_dir = self.root / "completion_staging" / f"stage-{identity['MESSAGE_ID']}"
        staging_dir.mkdir(parents=True)
        staging = {
            "COMPLETION_STAGING_SCHEMA_VERSION": completion_helper.COMPLETION_STAGING_SCHEMA_VERSION,
            **identity,
            "PROJECT_ID": None,
            "STATUS": "STAGING_READY",
            "CREATED_AT": o.stamp(),
            "RECEIPT": brief,
        }
        (staging_dir / "staging.json").write_text(
            json.dumps(staging, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(
            completion_helper.commit(o.ROOT, staging_dir), completion_helper.EXIT_COMMITTED)
        return brief

    def test_fresh_receipt_consumed_once(self):
        self.commit_receipt()
        seen, event = o.consume_executor_receipt(self.runtime)
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(self.runtime["last_consumed_message_id"], 700001)
        self.assertFalse(o.ZCODE_DONE.exists())

        # A recreated raw DONE for the same (now consumed) identity is a late
        # republish: quarantined, counted stale, never consumed a second time.
        o.atomic_write(o.ZCODE_DONE, "700001 replay\n")
        seen2, event2 = o.consume_executor_receipt(self.runtime)
        self.assertTrue(seen2)
        self.assertIsNone(event2)
        self.assertEqual(self.runtime["stale_receipts_ignored"], 1)
        self.assertFalse(o.ZCODE_DONE.exists())

    def test_done_identity_mismatch_is_quarantined_not_consumed(self):
        self.commit_receipt()
        forged = (
            "MESSAGE_ID=700001\nNONCE=forged-nonce\n"
        )
        o.atomic_write(o.ZCODE_DONE, forged)
        seen, event = o.consume_executor_receipt(self.runtime)
        self.assertTrue(seen)
        self.assertIsNone(event)
        self.assertEqual(self.runtime["last_consumed_message_id"], 602)
        self.assertEqual(self.runtime["protocol_errors"], 1)
        self.assertFalse(o.ZCODE_DONE.exists())

    def test_unknown_raw_done_is_fail_closed(self):
        o.atomic_write(o.ZCODE_DONE, "done\n")
        seen, event = o.consume_executor_receipt(self.runtime)
        self.assertTrue(seen)
        self.assertIsNone(event)
        self.assertEqual(self.runtime["protocol_errors"], 1)
        self.assertFalse(o.ZCODE_DONE.exists())

    def test_lowercase_current_task_identity_is_accepted(self):
        lower = {
            "message_id": 700001,
            "task_id": "T-A",
            "stage_id": "INFRA-A-NORMAL",
            "attempt": 1,
            "nonce": "nonce-a",
            "issued_at": o.stamp(),
        }
        self.base_state["current_task"] = lower
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        self.commit_receipt()
        seen, event = o.consume_executor_receipt(self.runtime)
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")

    def test_staged_to_zcode_is_promoted_when_root_is_stale(self):
        lower = {
            "message_id": 700001,
            "task_id": "T-A",
            "stage_id": "INFRA-A-NORMAL",
            "attempt": 1,
            "nonce": "nonce-a",
            "issued_at": o.stamp(),
        }
        self.base_state["current_task"] = lower
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        o.atomic_write(o.TO_ZCODE, "MESSAGE_ID: 602\nNO_ACTIVE_TASK\n")
        payload = {
            "PROTOCOL_VERSION": 2,
            "MESSAGE_ID": 700001,
            "TASK_ID": "T-A",
            "STAGE_ID": "INFRA-A-NORMAL",
            "ATTEMPT": 1,
            "NONCE": "nonce-a",
            "OBJECTIVE": "fixture",
            "OUTPUTS": [],
        }
        staged = self.root / "TO_ZCODE.md.tmp"
        o.atomic_write(staged, wire(payload))
        loaded = o.load_or_promote_dispatch(self.base_state)
        self.assertEqual(loaded["MESSAGE_ID"], 700001)
        self.assertFalse(staged.exists())
        self.assertEqual(o.parse_json_fence(o.TO_ZCODE)["NONCE"], "nonce-a")

    def test_executor_timeout_recovers_lowercase_state_metadata_from_task_file(self):
        lower = {
            "message_id": 700001,
            "task_id": "T-A",
            "stage_id": "INFRA-A-NORMAL",
            "attempt": 1,
            "nonce": "nonce-a",
            "issued_at": "2000-01-01T00:00:00+00:00",
        }
        self.base_state["current_task"] = lower
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        payload = {
            "PROTOCOL_VERSION": 2,
            "MESSAGE_ID": 700001,
            "TASK_ID": "T-A",
            "STAGE_ID": "INFRA-A-NORMAL",
            "ATTEMPT": 1,
            "NONCE": "nonce-a",
            "OBJECTIVE": "fixture",
            "OUTPUTS": [],
            "MAX_TIME": 900,
            "SCHEDULER_GRACE_SECONDS": 600,
        }
        o.atomic_write(o.TO_ZCODE, wire(payload))
        self.runtime["timeout_notified_for_nonce"] = None
        event = o.executor_timeout_event(self.runtime, self.base_state)
        self.assertIsNotNone(event)
        self.assertEqual(event["message_id"], 700001)

    def test_restart_while_waiting_executor_does_not_invoke_codex(self):
        payload = {
            "PROTOCOL_VERSION": 2,
            "MESSAGE_ID": 700001,
            "TASK_ID": "T-A",
            "STAGE_ID": "INFRA-A-NORMAL",
            "ATTEMPT": 1,
            "NONCE": "nonce-a",
            "OBJECTIVE": "fixture",
            "OUTPUTS": [],
            "MAX_TIME": 900,
            "SCHEDULER_GRACE_SECONDS": 600,
        }
        o.atomic_write(o.TO_ZCODE, wire(payload))
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        # Explicitly migrate/re-register this pre-control fixture before testing
        # restart. v1.2 startup itself must not authorize provenance-free work.
        o.register_dispatched_task(self.runtime, self.base_state, allow_same_identity=True)
        with patch.object(o, "invoke_codex", side_effect=AssertionError("Codex must not be called on WAITING_EXECUTOR restart")):
            with patch.object(o.time, "sleep", side_effect=KeyboardInterrupt):
                code = o.main()
        self.assertEqual(code, 130)

    def test_real_stop_prevents_codex_start(self):
        self.base_state["status"] = "SUPERVISOR_TURN"
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        o.atomic_write(o.STOP_FLAG, "STOP\n")
        with patch.object(o, "invoke_codex", side_effect=AssertionError("Codex must not be called")):
            code = o.main()
        self.assertEqual(code, 2)

    def test_real_human_review_prevents_codex_start(self):
        self.base_state["status"] = "HUMAN_REVIEW"
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        with patch.object(o, "invoke_codex", side_effect=AssertionError("Codex must not be called")):
            code = o.main()
        self.assertEqual(code, 3)

    def test_hard_deadline_prevents_codex_start(self):
        self.base_state["status"] = "SUPERVISOR_TURN"
        self.base_state["deadline_at"] = "2000-01-01T00:00:00+00:00"
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        with patch.object(o, "invoke_codex", side_effect=AssertionError("Codex must not be called")):
            code = o.main()
        self.assertEqual(code, 4)

    def test_codex_prompt_keeps_role_boundary_and_injects_state(self):
        o.atomic_write(o.SUPERVISOR_RULES, "RUNTIME_CONTRACT_FIXTURE")
        o.atomic_write(o.RESEARCH_STATE, "RESEARCH_MEMORY_FIXTURE")
        o.atomic_write(o.COMMERCIAL_GOAL, "COMMERCIAL_GOAL_FIXTURE")
        prompt = o.build_codex_prompt("TEST")
        self.assertIn("Supervisor, not the Executor", prompt)
        self.assertIn("Do not use browser/GUI/Computer Use", prompt)
        self.assertIn("RUNTIME_CONTRACT_FIXTURE", prompt)
        self.assertIn("RESEARCH_MEMORY_FIXTURE", prompt)
        self.assertIn("COMMERCIAL_GOAL_FIXTURE", prompt)
        self.assertIn('"status": "WAITING_EXECUTOR"', prompt)

    def test_reasoning_profile_is_always_high(self):
        cases = [
            ({"infrastructure_status": "TESTING"}, "ORCHESTRATOR_START"),
            ({"infrastructure_status": "READY"}, "SUPERVISOR_TURN"),
            ({"infrastructure_status": "READY", "current_task": {"SUPERVISOR_REVIEW_EFFORT": "medium"}}, "EXECUTOR_RESULT_READY"),
            ({"infrastructure_status": "READY"}, "EXECUTOR_TIMEOUT"),
        ]
        for state, reason in cases:
            self.assertEqual(o.supervisor_effort(state, reason), "high")

    def test_supervisor_model_and_effort_are_pinned(self):
        self.assertEqual(o.SUPERVISOR_MODEL, "gpt-5.6-sol")
        self.assertEqual(o.SUPERVISOR_REASONING_EFFORT, "high")


    def test_timeout_recovers_all_metadata_from_wire_and_parses_minutes(self):
        self.base_state["current_task"] = {
            "MESSAGE_ID": 700001,
            "TASK_ID": "T-A",
            "STAGE_ID": "INFRA-A-NORMAL",
            "ATTEMPT": 1,
            "NONCE": "nonce-a",
        }
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        payload = {
            "PROTOCOL_VERSION": 2,
            "MESSAGE_ID": 700001,
            "TASK_ID": "T-A",
            "STAGE_ID": "INFRA-A-NORMAL",
            "ATTEMPT": 1,
            "NONCE": "nonce-a",
            "ISSUED_AT": "2000-01-01T00:00:00+00:00",
            "OBJECTIVE": "fixture",
            "OUTPUTS": [],
            "MAX_TIME": "90 minutes",
            "SCHEDULER_GRACE_SECONDS": 180,
        }
        o.atomic_write(o.TO_ZCODE, wire(payload))
        self.runtime["timeout_notified_for_nonce"] = None
        event = o.executor_timeout_event(self.runtime, self.base_state)
        self.assertIsNotNone(event)
        self.assertEqual(event["max_time_seconds"], 5400)
        self.assertEqual(event["scheduler_grace_seconds"], 3600)

    def test_duration_parser_accepts_numeric_and_human_units(self):
        self.assertEqual(o.parse_duration_seconds(900, 1), 900)
        self.assertEqual(o.parse_duration_seconds("90 minutes", 1), 5400)
        self.assertEqual(o.parse_duration_seconds("2h", 1), 7200)

    def test_prompt_contains_literal_windows_path_without_control_escape(self):
        o.atomic_write(o.SUPERVISOR_RULES, "RUNTIME_CONTRACT_FIXTURE")
        o.atomic_write(o.RESEARCH_STATE, "RESEARCH_MEMORY_FIXTURE")
        o.atomic_write(o.COMMERCIAL_GOAL, "COMMERCIAL_GOAL_FIXTURE")
        prompt = o.build_codex_prompt("TEST")
        # G5A.5: the prompt is bound to the runtime install location, not a historical path
        self.assertIn(str(o.ROOT), prompt)
        self.assertNotIn("\x07", prompt)  # no control-escape corruption of embedded paths


    def test_future_dispatch_requires_executor_claim_protocol(self):
        self.runtime["claim_protocol_required_from_message_id"] = 700008
        self.base_state["current_task"] = {
            "MESSAGE_ID": 700008, "TASK_ID": "T-CLAIM", "STAGE_ID": "S-CLAIM",
            "ATTEMPT": 1, "NONCE": "nonce-claim",
        }
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        payload = {
            "PROTOCOL_VERSION": 2, "MESSAGE_ID": 700008, "TASK_ID": "T-CLAIM",
            "STAGE_ID": "S-CLAIM", "ATTEMPT": 1, "NONCE": "nonce-claim",
            "OBJECTIVE": "fixture", "OUTPUTS": [], "EXECUTOR_PROTOCOL": [],
        }
        o.atomic_write(o.TO_ZCODE, wire(payload))
        with self.assertRaisesRegex(RuntimeError, "claim protocol missing"):
            o.register_dispatched_task(self.runtime, self.base_state)

    def test_future_dispatch_accepts_executor_claim_protocol(self):
        self.runtime["claim_protocol_required_from_message_id"] = 700008
        self.base_state["current_task"] = {
            "MESSAGE_ID": 700008, "TASK_ID": "T-CLAIM", "STAGE_ID": "S-CLAIM",
            "ATTEMPT": 1, "NONCE": "nonce-claim",
        }
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        payload = {
            "PROTOCOL_VERSION": 2, "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": 700008, "TASK_ID": "T-CLAIM", "STAGE_ID": "S-CLAIM",
            "ATTEMPT": 1, "NONCE": "nonce-claim", "OBJECTIVE": "fixture", "OUTPUTS": [],
            "EXECUTOR_PROTOCOL": ["Run python scripts/executor_claim.py acquire before stage work."],
        }
        o.atomic_write(o.TO_ZCODE, wire(payload))
        task = o.register_dispatched_task(self.runtime, self.base_state)
        self.assertEqual(task["MESSAGE_ID"], 700008)


    def test_terminal_notification_writes_user_status_and_dedupes(self):
        state = dict(self.base_state)
        state["status"] = "COMPLETE"
        state["project"] = "runtime-root"
        state["last_supervisor_decision"] = {
            "decision": "STOP",
            "scope": "FINAL",
            "reason": "Evidence is sufficient; stop desk research.",
        }
        report = o.REPORTS / "CROSS_BORDER_FINAL.md"
        report.write_text("# final\n", encoding="utf-8")
        runtime = dict(self.runtime)

        with patch.object(o, "launch_windows_desktop_notification", return_value=True) as desktop:
            first = o.emit_user_notification(runtime, "COMPLETE", state)
            second = o.emit_user_notification(runtime, "COMPLETE", state)

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(desktop.call_count, 1)
        self.assertTrue(o.USER_ATTENTION.exists())
        self.assertTrue(o.USER_STATUS_REPORT.exists())
        text = o.USER_STATUS_REPORT.read_text(encoding="utf-8")
        self.assertIn("项目已完成", text)
        self.assertIn("CROSS_BORDER_FINAL.md", text)
        self.assertIn("不会再自动派发", text)

    def test_discover_final_report_prefers_explicit_state_path(self):
        old = o.REPORTS / "older.md"
        old.write_text("old\n", encoding="utf-8")
        explicit = o.REPORTS / "chosen.md"
        explicit.write_text("chosen\n", encoding="utf-8")
        state = {"final_report": "reports/chosen.md"}
        self.assertEqual(o.discover_final_report(state), explicit)

    def test_complete_main_emits_notification_and_exits_without_codex(self):
        self.base_state["status"] = "COMPLETE"
        self.base_state["project"] = "runtime-root"
        self.base_state["last_supervisor_decision"] = {
            "decision": "STOP",
            "scope": "FINAL",
            "reason": "done",
        }
        o.atomic_json(o.PROJECT_STATE, self.base_state)
        with patch.object(o, "invoke_codex", side_effect=AssertionError("Codex must not be called")):
            code = o.main()
        self.assertEqual(code, 0)
        self.assertTrue(o.USER_STATUS_REPORT.exists())
        payload = json.loads(o.USER_ATTENTION.read_text(encoding="utf-8"))
        self.assertEqual(payload["event"], "COMPLETE")
        self.assertTrue(payload["no_further_executor_tasks"])

    def test_error_notification_contains_exception_reason(self):
        state = {"project": "runtime-root", "phase": "X", "status": "ORCHESTRATOR_ERROR"}
        payload = o.build_user_notification("ORCHESTRATOR_ERROR", state, {"error": "boom"})
        self.assertEqual(payload["reason"], "boom")
        self.assertIn("不可恢复错误", payload["headline"])


if __name__ == "__main__":
    unittest.main()
