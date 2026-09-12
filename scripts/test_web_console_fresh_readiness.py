"""First-project dogfood: real HTTP create, status, Setup and preflight.

Only the final agent-process launch is mocked; bootstrap is the release helper.
All Runtime state is disposable and created through the production interfaces.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import web_console_server as wcs
import web_console_runtime_create as creation
from test_web_console_setup_http import SetupFixture


class FreshRuntimeFixture(SetupFixture):
    def __init__(self, base):
        self.base = base
        self.console_root = base / "installation"
        creation.copy_skeleton(REPO, self.console_root)
        self.data_dir = base / "console-data"
        self.runtime = base / "fresh-runtime"
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.console_root,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=10.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def create_runtime(self):
        status, payload, _ = self.request(
            "POST", "/api/runtimes/create", body={
                "template_id": creation.TEMPLATE_ID,
                "destination": str(self.runtime), "label": "Fresh dogfood"})
        if status != 201:
            raise AssertionError((status, payload))
        return payload["runtime"]


class FreshReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fresh-readiness-")
        self.addCleanup(self.temp.cleanup)
        self.fixture = FreshRuntimeFixture(Path(self.temp.name))
        self.addCleanup(self.fixture.server.server_close)
        self.addCleanup(self.fixture.server.shutdown)
        self.rid = self.fixture.create_runtime()["id"]
        self.fixture.register_goal(self.rid)
        for suffix, body in (
                ("inputs", {"decision": "NONE_NEEDED"}),
                ("supervisor", {"model": None, "reasoning_effort": None,
                                "explanation_mode": "COMPACT"}),
                ("zcode/acknowledge", {})):
            status, payload, _ = self.fixture.request(
                "POST", self.path(suffix), body=body)
            self.assertEqual(status, 200, payload)

    def path(self, suffix):
        return f"/api/runtimes/{self.rid}/setup/{suffix}"

    def readiness(self):
        status, payload, _ = self.fixture.request("GET", self.path("readiness"))
        self.assertEqual(status, 200, payload)
        return payload["readiness"]

    def raw_preflight(self):
        return subprocess.run(
            [sys.executable, str(self.fixture.runtime / "scripts/preflight.py")],
            cwd=self.fixture.runtime, capture_output=True, text=True,
            encoding="utf-8", timeout=30)

    def test_created_runtime_first_project_is_ready(self):
        raw = self.raw_preflight()
        self.assertEqual(raw.returncode, 1)
        self.assertIn("Mode: legacy", raw.stdout)
        for relative in ("control/project_state.json", "RESEARCH_STATE.md",
                         "ZCODE_LAST_PROCESSED.txt"):
            path = self.fixture.runtime / relative
            self.assertFalse(path.exists())
            self.assertIn(f"- missing: {path}", raw.stdout)
        readiness = self.readiness()
        self.assertTrue(readiness["ready"], json.dumps(readiness, indent=2))
        self.assertFalse((self.fixture.runtime / "projects").exists())
        self.assertFalse((self.fixture.runtime / "control/ACTIVE_PROJECT.json").exists())

    def bootstrap(self):
        with mock.patch.object(wcs.ConsoleRequestHandler, "_run_start_entry",
                               return_value={"verification": "TEST_LAUNCH_BOUNDARY"}) as launch:
            status, payload, _ = self.fixture.request("POST", self.path("start"), body={})
        self.assertEqual(status, 200, payload)
        launch.assert_called_once()
        self.assertEqual(payload["start"]["bootstrap"]["event"], "PROJECT_CREATED")
        return self.fixture.runtime / "projects/proj-new"

    def test_start_gate_runs_real_bootstrap_and_binds_goal_anchor(self):
        self.assertTrue(self.readiness()["ready"])
        project = self.bootstrap()
        state = json.loads((project / "project_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(state["goal_anchor"]["goal_sha256"],
                         hashlib.sha256((project / "PROJECT_GOAL.md").read_bytes()).hexdigest())
        self.assertTrue((project / "RESEARCH_STATE.md").is_file())
        pointer = json.loads((self.fixture.runtime / "control/ACTIVE_PROJECT.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(pointer["project_root"], "projects/proj-new")
        raw = self.raw_preflight()
        self.assertEqual(raw.returncode, 0, raw.stdout + raw.stderr)
        self.assertIn("Mode: isolated", raw.stdout)
        # Formal isolated bootstrap creates project-local state, never legacy placeholders.
        for relative in ("control/project_state.json", "RESEARCH_STATE.md",
                         "ZCODE_LAST_PROCESSED.txt"):
            self.assertFalse((self.fixture.runtime / relative).exists())

    def assert_blocked(self):
        readiness = self.readiness()
        self.assertFalse(readiness["ready"], readiness)
        self.assertEqual(next(c["state"] for c in readiness["checks"]
                              if c["key"] == "preflight"), "FAIL")
        with mock.patch.object(wcs.ConsoleRequestHandler, "_run_bootstrap") as bootstrap:
            status, payload, _ = self.fixture.request("POST", self.path("start"), body={})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["error"]["code"], "SETUP_NOT_READY")
        bootstrap.assert_not_called()

    def test_lifecycle_footprints_and_ambiguous_state_fail_closed(self):
        for relative, content in (
                ("control/ACTIVE_PROJECT.json", "{broken"),
                ("control/project_state.json", "{}"),
                ("RESEARCH_STATE.md", "old memory"),
                ("ZCODE_LAST_PROCESSED.txt", "garbage"),
                ("TO_ZCODE.md", "NO_ACTIVE_TASK"),
                ("control/orchestrator_runtime.json", "{}"),
                ("control/STOP", ""), ("control/HUMAN_REVIEW", ""),
                ("control/executor_claims.json", "{}"),
                ("handoff/old-receipt.json", "{}"),
                ("unknown-state.json", "{}")):
            with self.subTest(path=relative):
                path = self.fixture.runtime / relative
                path.write_text(content, encoding="utf-8")
                try:
                    self.assert_blocked()
                finally:
                    path.unlink()

    def test_even_empty_project_or_history_directories_are_ambiguous(self):
        for relative in ("projects", "logs", "handoff/archive"):
            with self.subTest(path=relative):
                path = self.fixture.runtime / relative
                path.mkdir()
                try:
                    self.assert_blocked()
                finally:
                    path.rmdir()

    def test_existing_project_missing_state_stays_blocked_with_or_without_pointer(self):
        project = self.bootstrap()
        (project / "project_state.json").unlink()
        self.assert_blocked()
        (self.fixture.runtime / "control/ACTIVE_PROJECT.json").unlink()
        self.assert_blocked()

    def test_modified_or_incomplete_release_cannot_claim_freshness(self):
        for relative in ("scripts/start_project.py", "control/budget.json",
                         "profiles/GENERAL/FINAL_VERIFICATION_POLICY.json",
                         "control/CODEX_SUPERVISOR_RUNTIME.md"):
            with self.subTest(path=relative):
                path = self.fixture.runtime / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\n# changed\n")
                    self.assert_blocked()
                    path.unlink()
                    self.assert_blocked()
                finally:
                    path.write_bytes(original)

    def test_start_rechecks_freshness_after_readiness(self):
        self.assertTrue(self.readiness()["ready"])
        (self.fixture.runtime / "control/STOP").touch()
        self.assert_blocked()
        self.assertFalse((self.fixture.runtime / "projects").exists())

    def test_missing_setup_requirements_never_enable_bootstrap(self):
        draft_path = self.fixture.data_dir / "setup" / self.rid / "draft.json"
        original = json.loads(draft_path.read_text(encoding="utf-8"))
        for key, value in (("goal_markdown", None), ("project_type", "INVALID"),
                           ("inputs", None), ("supervisor", None),
                           ("zcode", {"acknowledged": False, "acknowledged_at": None})):
            with self.subTest(key=key):
                draft_path.write_text(json.dumps({**original, key: value}), encoding="utf-8")
                self.assertFalse(self.readiness()["ready"])
                with mock.patch.object(wcs.ConsoleRequestHandler, "_run_bootstrap") as bootstrap:
                    status, _, _ = self.fixture.request("POST", self.path("start"), body={})
                self.assertEqual(status, 409)
                bootstrap.assert_not_called()

    def test_unrecognized_truncated_or_failed_preflight_never_passes(self):
        raw = self.raw_preflight()
        for result in (
                {"ok": False, "exit_code": 1, "head": raw.stdout + "- other error\n"},
                {"ok": False, "exit_code": 1, "head": raw.stdout[:200]},
                {"ok": False, "exit_code": None, "head": "preflight timed out"},
                {"ok": False, "exit_code": 2, "head": raw.stdout}):
            with self.subTest(result=result), mock.patch.object(
                    wcs.ConsoleRequestHandler, "_run_setup_preflight", return_value=result):
                self.assert_blocked()

    def test_reparse_product_entry_fails_closed(self):
        original = creation.is_reparse
        target = self.fixture.runtime / "profiles/GENERAL/PROFILE.json"
        with mock.patch.object(creation, "is_reparse",
                               side_effect=lambda p: Path(p) == target or original(p)):
            self.assert_blocked()

    def test_incomplete_or_contradictory_status_cannot_prove_freshness(self):
        original = self.fixture.server.status_result
        for key, value in (("PROJECT_ID", "old-project"), ("active_task", {}),
                           ("stop", True), ("pause", None)):
            def changed_status(**kwargs):
                status, payload = original(**kwargs)
                payload["control_plane"]["status"] = {
                    **payload["control_plane"]["status"], key: value}
                return status, payload
            with self.subTest(key=key), mock.patch.object(
                    self.fixture.server, "status_result", side_effect=changed_status):
                self.assert_blocked()

    def test_pre_fix_created_runtime_does_not_need_new_console_files(self):
        # Models the already-created dogfood tree: it predates this Console fix.
        for name in ("web_console_fresh_install.py", "test_web_console_fresh_readiness.py"):
            (self.fixture.runtime / "scripts" / name).unlink()
        self.assertTrue(self.readiness()["ready"])


if __name__ == "__main__":
    unittest.main()
