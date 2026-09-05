"""RICH-CONSOLE-UX regression coverage for the optional Rich console layer.

Narrowly scoped companion to test_orchestrator.py: proves that the console
presentation layer (a) provides the requested UX behaviors, and (b) can never
change protocol behavior — state transitions, claim authorization, completion
commit/consume/seal, Final Verification inputs, exit codes, durable logging,
publication artifacts, or the authority of the completion ledger over
ZCODE_DONE.flag / SUPERVISOR_BRIEF.md.
"""
import contextlib
import hashlib
import importlib.util
import io
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

MODULE_PATH = Path(__file__).resolve().parents[1] / "orchestrator.py"
spec = importlib.util.spec_from_file_location("orchestrator_console_view", MODULE_PATH)
o = importlib.util.module_from_spec(spec)
spec.loader.exec_module(o)


def wire(value):
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


class _RestrictedEncodingStream:
    """A stdout stand-in that rejects every non-ASCII character, like a legacy
    console with a restrictive codepage."""

    encoding = "ascii"

    def __init__(self):
        self.chunks = []

    def write(self, text):
        text.encode("ascii")  # raises UnicodeEncodeError on non-ASCII text
        self.chunks.append(text)
        return len(text)

    def flush(self):
        pass


class ConsolePresentationTests(unittest.TestCase):

    IDENTITY = {
        "MESSAGE_ID": 700960,
        "TASK_ID": "T-VIEW",
        "STAGE_ID": "S-VIEW",
        "ATTEMPT": 1,
        "NONCE": "nonce-view",
    }

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="console-view-tests-")
        self.root = Path(self.temp.name)
        self._saved = {
            name: getattr(o, name, None)
            for name in (
                "ROOT", "CONTROL", "LOGS", "HANDOFF_ARCHIVE", "REPORTS", "PROJECT_STATE",
                "RUNTIME_STATE", "SUPERVISOR_RULES", "RESEARCH_STATE", "COMMERCIAL_GOAL",
                "TO_ZCODE", "SUPERVISOR_BRIEF", "ZCODE_DONE", "ZCODE_LAST_PROCESSED",
                "STOP_FLAG", "HUMAN_REVIEW_FLAG", "LOCK_FILE", "CODEX_LAST_OUTPUT",
                "USER_ATTENTION", "USER_STATUS_REPORT", "ACTIVE_PROJECT_FILE",
                "ACTIVE_PROJECT", "DESKTOP_NOTIFICATIONS_ENABLED",
                "USER_NOTIFICATION_CONSOLE_ENABLED", "_VIEW",
            )
        }
        self._apply_root(self.root)
        for path in (o.CONTROL, o.LOGS, o.HANDOFF_ARCHIVE, o.REPORTS):
            path.mkdir(parents=True, exist_ok=True)
        o.DESKTOP_NOTIFICATIONS_ENABLED = False
        o.USER_NOTIFICATION_CONSOLE_ENABLED = True
        o._VIEW = None
        o.ZCODE_LAST_PROCESSED.write_text("602\n", encoding="utf-8")

    def tearDown(self):
        for name, value in self._saved.items():
            setattr(o, name, value)
        self.temp.cleanup()

    def _apply_root(self, root):
        o.ROOT = root
        o.CONTROL = root / "control"
        o.LOGS = root / "logs"
        o.HANDOFF_ARCHIVE = root / "handoff" / "archive"
        o.REPORTS = root / "reports"
        o.PROJECT_STATE = o.CONTROL / "project_state.json"
        o.RUNTIME_STATE = o.CONTROL / "orchestrator_runtime.json"
        o.SUPERVISOR_RULES = o.CONTROL / "CODEX_SUPERVISOR_RUNTIME.md"
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
        o.ACTIVE_PROJECT_FILE = o.CONTROL / "ACTIVE_PROJECT.json"
        o.ACTIVE_PROJECT = None

    # ---- fixtures -----------------------------------------------------------

    def _notification_state(self):
        return {
            "schema_version": 2,
            "status": "WAITING_EXECUTOR",
            "phase": "CONSOLE_TESTS",
            "project": "runtime-root",
            "last_supervisor_decision": {
                "decision": "DISPATCH", "scope": "S1", "reason": "fixture reason",
            },
        }

    def _write_waiting_state(self, identity=None, status="WAITING_EXECUTOR", **extra):
        identity = dict(identity or self.IDENTITY)
        state = {
            "schema_version": 2,
            "status": status,
            "phase": "CONSOLE_TESTS",
            "project": "runtime-root",
            "started_at": o.stamp(),
            "deadline_at": "2099-01-01T00:00:00+00:00",
            "current_task": {
                **identity,
                "ISSUED_AT": o.stamp(),
                "MAX_TIME": 900,
                "SCHEDULER_GRACE_SECONDS": 3600,
            },
        }
        state.update(extra)
        o.atomic_json(o.PROJECT_STATE, state)
        return state

    def _commit_completion(self, identity):
        """Drive the legal claim -> staging -> authoritative commit chain."""
        payload = {"PROTOCOL_VERSION": 2, **identity, "OBJECTIVE": "fixture", "OUTPUTS": []}
        o.atomic_write(o.TO_ZCODE, wire(payload))
        o.atomic_json(o.RUNTIME_STATE, {
            "authorized_dispatch": {
                "schema_version": 1,
                **identity,
                "TO_ZCODE_SHA256": hashlib.sha256(o.TO_ZCODE.read_bytes()).hexdigest(),
                "AUTHORIZED_AT": o.stamp(),
            },
            "retired_message_ids": [],
        })
        self._write_waiting_state(identity)
        self.assertEqual(
            claim_helper.acquire(
                o.ROOT, identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
                identity["ATTEMPT"], identity["NONCE"],
            ),
            claim_helper.EXIT_ACQUIRED,
        )
        brief = {
            "PROTOCOL_VERSION": 2, **identity,
            "EXECUTOR_MODEL_FAMILY": "GLM-5.3", "RUNTIME_MODEL": "GLM-5.3-Flash",
            "STATUS": "COMPLETED", "Objective": "fixture",
            "Key findings": ["one", "two", "three"],
            "Core metrics": {}, "What we can conclude": ["done"],
            "What we cannot conclude": [], "Problems / uncertainty": [],
            "Work performed": ["work"], "Failed methods": [],
            "Recommended next action": "Supervisor review",
            "Evidence pointers": [], "Resource / efficiency note": "n/a",
            "Deliverables": [], "Acceptance self-check": {},
            "STARTED_AT": o.stamp(), "FINISHED_AT": o.stamp(),
        }
        staging_dir = o.ROOT / "completion_staging" / f"stage-{identity['MESSAGE_ID']}"
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

    @staticmethod
    def _broken_view():
        """A ConsoleView whose whole rendering pipeline raises when touched."""
        view = o.ConsoleView(mode="rich", stream=io.StringIO())

        def boom(*_args, **_kwargs):
            raise RuntimeError("injected rendering failure")

        for internal in ("_write_line", "_ensure_rich_console", "_probe_rich",
                         "_render", "_banner_lines", "_banner_body_lines",
                         "_announce_wait", "_start_live", "_refresh_live", "_stop_live"):
            setattr(view, internal, boom)
        return view

    # ---- presentation behavior ---------------------------------------------

    def test_plain_mode_renders_understandable_text_without_ansi(self):
        buffer = io.StringIO()
        view = o.ConsoleView(mode="plain", stream=buffer)
        view.log_line("[2026-09-06T00:00:00] fixture log event")
        view.lifecycle_event("COMPLETION_CONSUMED", message_id=1, commit_id="c-1")
        view.supervisor_decision(turn="EXECUTOR_RESULT_READY", decision="DISPATCH",
                                 scope="S1", project_status="WAITING_EXECUTOR")
        text = buffer.getvalue()
        self.assertIn("fixture log event", text)
        self.assertIn("COMPLETION_CONSUMED", text)
        self.assertIn("Decision : DISPATCH", text)
        self.assertNotIn("\x1b", text)

    def test_auto_mode_non_tty_falls_back_to_plain(self):
        buffer = io.StringIO()  # not a TTY
        view = o.ConsoleView(mode="auto", stream=buffer)
        self.assertFalse(view._use_rich())
        view.log_line("plain event")
        view.terminal_panel(o.build_user_notification("COMPLETE", self._notification_state()))
        text = buffer.getvalue()
        self.assertIn("plain event", text)
        self.assertIn("Event: COMPLETE", text)
        self.assertNotIn("\x1b", text)

    def test_rich_mode_renders_distinct_panels(self):
        state = self._notification_state()
        outputs = {}
        for kind in ("COMPLETE", "HUMAN_REVIEW", "ORCHESTRATOR_ERROR"):
            buffer = io.StringIO()
            view = o.ConsoleView(mode="rich", stream=buffer)
            self.assertTrue(view._use_rich())
            if kind == "HUMAN_REVIEW":
                view.human_review_panel(o.build_user_notification(kind, state))
            elif kind == "ORCHESTRATOR_ERROR":
                view.error_panel(o.build_user_notification(kind, state), error="boom")
            else:
                view.terminal_panel(o.build_user_notification(kind, state))
            outputs[kind] = buffer.getvalue()
        self.assertIn("TERMINAL: COMPLETE", outputs["COMPLETE"])
        self.assertIn("HUMAN REVIEW REQUIRED", outputs["HUMAN_REVIEW"])
        self.assertIn("ORCHESTRATOR ERROR", outputs["ORCHESTRATOR_ERROR"])
        # Each marker is exclusive to its presentation, and each panel keeps
        # the substantive banner content.
        markers = {
            "COMPLETE": "TERMINAL:",
            "HUMAN_REVIEW": "HUMAN REVIEW REQUIRED",
            "ORCHESTRATOR_ERROR": "ORCHESTRATOR ERROR",
        }
        for kind, text in outputs.items():
            self.assertIn("User status:", text)
            for other_kind, marker in markers.items():
                if other_kind != kind:
                    self.assertNotIn(marker, text)
        for text in outputs.values():
            self.assertNotIn("\x1b", text)  # non-terminal stream: styled, never ANSI

    def test_forced_rich_import_failure_falls_back_to_plain(self):
        blocked = {"rich", "rich.console", "rich.live", "rich.spinner",
                   "rich.panel", "rich.text", "rich.markup"}
        buffer = io.StringIO()
        with patch.dict(sys.modules, {name: None for name in blocked}):
            view = o.ConsoleView(mode="rich", stream=buffer)
            self.assertFalse(view._use_rich(), "Rich import failure must disable the Rich path")
            view.log_line("fallback event")
            view.terminal_panel(o.build_user_notification("COMPLETE", self._notification_state()))
        text = buffer.getvalue()
        self.assertIn("fallback event", text)
        self.assertIn("Event: COMPLETE", text)
        self.assertNotIn("\x1b", text)

    def test_injected_render_failure_never_raises(self):
        view = self._broken_view()
        payload = o.build_user_notification("COMPLETE", self._notification_state())
        self.assertIsNone(view.log_line("line"))
        self.assertIsNone(view.supervisor_decision(turn="R", decision="D"))
        self.assertIsNone(view.lifecycle_event("COMPLETION_SEALED", message_id=1))
        self.assertIsNone(view.terminal_panel(payload))
        self.assertIsNone(view.human_review_panel(payload))
        self.assertIsNone(view.error_panel(payload, error="e"))
        self.assertIsNone(view.event_panel(payload))
        self.assertIsNone(view.waiting_tick(message_id=1, task_id="T", stage_id="S", attempt=1))
        self.assertIsNone(view.waiting_stop())

    def test_waiting_status_is_not_spammy(self):
        buffer = io.StringIO()
        view = o.ConsoleView(mode="plain", stream=buffer)
        for _ in range(30):
            view.waiting_tick(message_id=1, task_id="T", stage_id="S", attempt=1)
        self.assertEqual(buffer.getvalue().count("WAITING_EXECUTOR"), 1)
        view.waiting_stop()
        # A new dispatch (new identity) announces itself exactly once again.
        for _ in range(10):
            view.waiting_tick(message_id=2, task_id="T2", stage_id="S2", attempt=1)
        self.assertEqual(buffer.getvalue().count("WAITING_EXECUTOR"), 2)

    def test_waiting_rich_live_and_stop_are_safe_on_non_terminal(self):
        buffer = io.StringIO()
        view = o.ConsoleView(mode="rich", stream=buffer)
        for _ in range(3):
            view.waiting_tick(message_id=1, task_id="T", stage_id="S", attempt=1)
        view.waiting_stop()
        self.assertNotIn("\x1b", buffer.getvalue())

    def test_restricted_console_encoding_cannot_crash_rendering(self):
        stream = _RestrictedEncodingStream()
        view = o.ConsoleView(mode="plain", stream=stream)
        view.terminal_panel(o.build_user_notification("COMPLETE", self._notification_state()))
        text = "".join(stream.chunks)
        self.assertIn("Event: COMPLETE", text)
        self.assertTrue(text.isascii(), "fallback output must be encodable on the restricted stream")

    def test_plain_event_panel_keeps_banner_content(self):
        buffer = io.StringIO()
        view = o.ConsoleView(mode="plain", stream=buffer)
        payload = o.build_user_notification("BLOCKED", self._notification_state())
        view.event_panel(payload)
        text = buffer.getvalue()
        self.assertIn("AGENT HANDSHAKE —", text)
        self.assertIn("Event: BLOCKED", text)
        self.assertIn("User status:", text)
        self.assertIn("No further Executor tasks", text)

    # ---- protocol invariance under console failure ---------------------------

    def test_off_mode_keeps_durable_log_and_drops_console_only(self):
        buffer = io.StringIO()
        with patch.object(o, "_VIEW", o.ConsoleView(mode="off", stream=buffer)):
            o.log("durable event survives off mode", detail=7)
        record = json.loads(
            (o.LOGS / "orchestrator.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(record["message"], "durable event survives off mode")
        self.assertEqual(record["detail"], 7)
        self.assertEqual(buffer.getvalue(), "")

    def test_console_failure_cannot_change_consume_outcome(self):
        runtime = {
            "last_consumed_message_id": 602,
            "protocol_errors": 0,
            "authorized_dispatch": {"schema_version": 1, **self.IDENTITY},
        }
        self._commit_completion(self.IDENTITY)
        seen, event = o.consume_executor_receipt(runtime)
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(runtime["last_consumed_message_id"], self.IDENTITY["MESSAGE_ID"])
        self.assertFalse(o.ZCODE_DONE.exists())
        baseline_errors = runtime["protocol_errors"]

        # A fresh identity, consumed while the entire view pipeline raises.
        second = dict(self.IDENTITY, MESSAGE_ID=700961, NONCE="nonce-view-2")
        runtime["authorized_dispatch"] = {"schema_version": 1, **second}
        self._commit_completion(second)
        with patch.object(o, "_VIEW", self._broken_view()):
            o.log("logged while the view is broken")
            seen2, event2 = o.consume_executor_receipt(runtime)
        self.assertTrue(seen2)
        self.assertEqual(event2["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(runtime["last_consumed_message_id"], second["MESSAGE_ID"])
        self.assertEqual(runtime["protocol_errors"], baseline_errors)
        self.assertFalse(o.ZCODE_DONE.exists())
        # Durable logging still recorded the event with identical content.
        lines = (o.LOGS / "orchestrator.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("logged while the view is broken" in line for line in lines))

    def test_console_failure_cannot_change_terminal_exit_paths(self):
        # STOP -> exit 2, HUMAN_REVIEW -> exit 3, COMPLETE -> exit 0, all while
        # every console render call raises.
        with patch.object(o, "_VIEW", self._broken_view()):
            self._write_waiting_state(status="WAITING_EXECUTOR")
            o.atomic_write(o.STOP_FLAG, "STOP\n")
            with patch.object(o, "invoke_codex",
                              side_effect=AssertionError("Codex must not be called")):
                self.assertEqual(o.main(), 2)
            payload = json.loads(o.USER_ATTENTION.read_text(encoding="utf-8"))
            self.assertEqual(payload["event"], "STOPPED_BY_USER")
            o.STOP_FLAG.unlink()

            self._write_waiting_state(status="HUMAN_REVIEW")
            with patch.object(o, "invoke_codex",
                              side_effect=AssertionError("Codex must not be called")):
                self.assertEqual(o.main(), 3)
            payload = json.loads(o.USER_ATTENTION.read_text(encoding="utf-8"))
            self.assertEqual(payload["event"], "HUMAN_REVIEW")

            self._write_waiting_state(
                status="COMPLETE",
                last_supervisor_decision={"decision": "STOP", "scope": "FINAL", "reason": "done"},
            )
            with patch.object(o, "invoke_codex",
                              side_effect=AssertionError("Codex must not be called")):
                self.assertEqual(o.main(), 0)
            payload = json.loads(o.USER_ATTENTION.read_text(encoding="utf-8"))
            self.assertEqual(payload["event"], "COMPLETE")
            self.assertTrue(o.USER_STATUS_REPORT.exists())

    def test_console_failure_cannot_change_startup_repair_and_recovery(self):
        # A restart while WAITING_EXECUTOR must keep its recovery semantics with
        # a broken view: resume the in-flight dispatch, never spend a Supervisor
        # turn, and exit only via the interrupt path (130).
        identity = self.IDENTITY
        self._publish_dispatch_file(identity)
        self._write_waiting_state(identity)
        with patch.object(o, "_VIEW", self._broken_view()):
            with patch.object(o, "invoke_codex",
                              side_effect=AssertionError("Codex must not be called")):
                with patch.object(o.time, "sleep", side_effect=KeyboardInterrupt):
                    code = o.main()
        self.assertEqual(code, 130)
        runtime = o.read_json(o.RUNTIME_STATE)
        self.assertEqual(runtime["last_dispatched_message_id"], identity["MESSAGE_ID"])
        self.assertEqual(runtime["last_dispatched_nonce"], identity["NONCE"])

    def _publish_dispatch_file(self, identity):
        payload = {"PROTOCOL_VERSION": 2, **identity, "OBJECTIVE": "fixture", "OUTPUTS": []}
        o.atomic_write(o.TO_ZCODE, wire(payload))

    def _authorize_dispatch(self, identity):
        """Publish TO_ZCODE and bind the Runtime authorization record."""
        self._publish_dispatch_file(identity)
        o.atomic_json(o.RUNTIME_STATE, {
            "authorized_dispatch": {
                "schema_version": 1,
                **identity,
                "TO_ZCODE_SHA256": hashlib.sha256(o.TO_ZCODE.read_bytes()).hexdigest(),
                "AUTHORIZED_AT": o.stamp(),
            },
            "retired_message_ids": [],
        })

    def _write_staging(self, identity, receipt):
        staging_dir = o.ROOT / "completion_staging" / f"stage-{identity['MESSAGE_ID']}"
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
        return staging_dir

    def _fixture_receipt(self, identity):
        return {
            "PROTOCOL_VERSION": 2, **identity,
            "EXECUTOR_MODEL_FAMILY": "GLM-5.3", "RUNTIME_MODEL": "GLM-5.3-Flash",
            "STATUS": "COMPLETED", "Objective": "fixture",
            "Key findings": ["one", "two", "three"],
            "Core metrics": {}, "What we can conclude": ["done"],
            "What we cannot conclude": [], "Problems / uncertainty": [],
            "Work performed": ["work"], "Failed methods": [],
            "Recommended next action": "Supervisor review",
            "Evidence pointers": [], "Resource / efficiency note": "n/a",
            "Deliverables": [], "Acceptance self-check": {},
            "STARTED_AT": o.stamp(), "FINISHED_AT": o.stamp(),
        }

    def test_console_failure_cannot_change_claim_and_commit_at_most_once(self):
        identity = dict(self.IDENTITY, MESSAGE_ID=700962, NONCE="nonce-view-3")
        self._authorize_dispatch(identity)
        self._write_waiting_state(identity)
        with patch.object(o, "_VIEW", self._broken_view()):
            self.assertEqual(
                claim_helper.acquire(
                    o.ROOT, identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
                    identity["ATTEMPT"], identity["NONCE"],
                ),
                claim_helper.EXIT_ACQUIRED,
            )
            # The second contender for the same identity still loses quietly.
            self.assertEqual(
                claim_helper.acquire(
                    o.ROOT, identity["MESSAGE_ID"], identity["TASK_ID"], identity["STAGE_ID"],
                    identity["ATTEMPT"], identity["NONCE"],
                ),
                claim_helper.EXIT_CLAIM_EXISTS,
            )
            receipt = self._fixture_receipt(identity)
            staging_dir = self._write_staging(identity, receipt)
            self.assertEqual(
                completion_helper.commit(o.ROOT, staging_dir),
                completion_helper.EXIT_COMMITTED,
            )
            # A second completion commit for the committed identity is refused.
            duplicate_dir = self._write_staging(
                dict(identity, NONCE=identity["NONCE"]), receipt)
            with self.assertRaises(completion_helper.CompletionError) as ctx:
                completion_helper.commit(o.ROOT, duplicate_dir)
            self.assertEqual(ctx.exception.code, completion_helper.EXIT_ALREADY_COMMITTED)

        # Publication artifacts remain exact derived views of the ledger entry.
        commit_id = completion_helper.commit_id_for(identity["MESSAGE_ID"], identity["NONCE"])
        entry = completion_helper.load_entry_file(completion_helper.entry_path(o.ROOT, commit_id))
        self.assertEqual(entry["STATUS"], completion_helper.STATUS_COMMITTED)
        self.assertEqual(
            o.SUPERVISOR_BRIEF.read_text(encoding="utf-8"),
            completion_helper.render_brief_text(receipt))
        self.assertEqual(
            o.ZCODE_LAST_PROCESSED.read_text(encoding="utf-8"),
            completion_helper.last_processed_text(entry))
        self.assertEqual(
            o.ZCODE_DONE.read_text(encoding="utf-8"),
            completion_helper.done_flag_text(entry))

    def test_console_failure_cannot_change_final_verification_gate(self):
        def gate_block_reason():
            state = self._write_waiting_state(
                status="COMPLETE",
                final_verification={"required": True, "status": "REQUIRED"},
            )
            runtime = {"final_verification_gate_blocks": 0}
            new_state, event = o.enforce_terminal_verification_gate(runtime, state, "test")
            return new_state, event, dict(runtime)

        normal_state, normal_event, normal_runtime = gate_block_reason()
        self.assertIsNotNone(normal_event)  # COMPLETE with status REQUIRED must block
        with patch.object(o, "_VIEW", self._broken_view()):
            broken_state, broken_event, broken_runtime = gate_block_reason()
        self.assertIsNotNone(broken_event)
        self.assertEqual(broken_state["status"], normal_state["status"])
        self.assertEqual(broken_state["status"], "SUPERVISOR_TURN")
        self.assertEqual(
            broken_runtime["final_verification_gate_blocks"],
            normal_runtime["final_verification_gate_blocks"],
        )
        self.assertEqual(
            broken_state["final_verification"]["gate_block_reason"],
            normal_state["final_verification"]["gate_block_reason"],
        )

    def test_console_failure_cannot_change_project_isolation(self):
        # An isolated active project keeps its scope rebinding and its exit
        # semantics even when every console render raises.
        pid = "isolated-view-project"
        project_root = o.ROOT / "projects" / pid
        (project_root / "reports").mkdir(parents=True)
        o.atomic_json(o.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": pid, "project_root": f"projects/{pid}",
        })
        o.atomic_json(project_root / "project_state.json", {
            "schema_version": 2, "project_id": pid, "status": "COMPLETE",
            "phase": "CONSOLE_TESTS", "project": pid,
            "started_at": o.stamp(), "deadline_at": "2099-01-01T00:00:00+00:00",
            "last_supervisor_decision": {"decision": "STOP", "scope": "FINAL", "reason": "done"},
        })
        with patch.object(o, "_VIEW", self._broken_view()):
            with patch.object(o, "invoke_codex",
                              side_effect=AssertionError("Codex must not be called")):
                code = o.main()
        self.assertEqual(code, 0)
        self.assertEqual(o.ACTIVE_PROJECT["project_id"], pid)
        # USER_STATUS.md is Runtime-owned and stays at ROOT\reports in every mode.
        self.assertTrue(o.USER_STATUS_REPORT.exists())
        payload = json.loads(o.USER_ATTENTION.read_text(encoding="utf-8"))
        self.assertEqual(payload["event"], "COMPLETE")

    def test_console_output_is_never_completion_authority(self):
        # Rendering a "consumed" lifecycle line fabricates nothing: without a
        # ledger entry the receipt is not consumed, whatever the console shows.
        runtime = {
            "last_consumed_message_id": 602,
            "protocol_errors": 0,
            "authorized_dispatch": {"schema_version": 1, **self.IDENTITY},
        }
        view = o.ConsoleView(mode="plain", stream=io.StringIO())
        view.lifecycle_event("COMPLETION_CONSUMED", message_id=self.IDENTITY["MESSAGE_ID"])
        seen, event = o.consume_executor_receipt(runtime)
        self.assertFalse(seen)
        self.assertIsNone(event)
        self.assertEqual(runtime["last_consumed_message_id"], 602)

        # With the authoritative ledger present, the same consume path works.
        self._commit_completion(self.IDENTITY)
        seen, event = o.consume_executor_receipt(runtime)
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(runtime["last_consumed_message_id"], self.IDENTITY["MESSAGE_ID"])

    def test_main_waits_with_single_status_line(self):
        identity = self.IDENTITY
        self._write_waiting_state(identity)
        self._publish_dispatch_file(identity)
        polls = {"count": 0}

        def sleep_and_interrupt(_seconds):
            polls["count"] += 1
            if polls["count"] >= 6:
                raise KeyboardInterrupt
            return None

        captured = io.StringIO()
        with patch.object(o, "invoke_codex",
                          side_effect=AssertionError("Codex must not be called")):
            with patch.object(o.time, "sleep", side_effect=sleep_and_interrupt):
                with contextlib.redirect_stdout(captured):
                    code = o.main()
        self.assertEqual(code, 130)
        self.assertGreaterEqual(polls["count"], 5)
        # Live status exists, and one wait episode prints exactly one line.
        self.assertEqual(captured.getvalue().count("WAITING_EXECUTOR"), 1)
        self.assertIn(identity["TASK_ID"], captured.getvalue())


if __name__ == "__main__":
    unittest.main()
