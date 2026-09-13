"""P7 Setup Wizard HTTP endpoint tests (offline, localhost).

Covers the new bounded setup routes over the wire on top of the P2 opaque-ID
root binding:

- `GET  /api/runtimes/<id>/setup/state`            wizard state + draft
- `POST /api/runtimes/<id>/setup/goal`             Goal validation + draft save
- `POST /api/runtimes/<id>/setup/inputs`           input inventory registration
- `POST /api/runtimes/<id>/setup/supervisor`       Supervisor config draft
- `GET  /api/runtimes/<id>/setup/zcode`            canonical prompt + steps
- `POST /api/runtimes/<id>/setup/zcode/acknowledge` explicit acknowledgement
- `GET  /api/runtimes/<id>/setup/goal-workshop`    bounded context pack
- `GET  /api/runtimes/<id>/setup/readiness`        the ten readiness checks
- `POST /api/runtimes/<id>/setup/start`            fail-closed bootstrap/start

Fail-closed behavior is pinned over the wire: typed request schemas, the real
bootstrap helper (`start_project.py`) and the real preflight inside disposable
v1.2 Runtime fixtures, refusal matrices (traversal, reparse points, cross-root
bindings, existing projects, occupied active projects), racing start requests,
restart persistence of Console-owned drafts, and method allowlists. The start
entry point in the fixture records its fixed argument-vector invocation
instead of launching the real orchestrator loop; no GUI and no network.
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_server as wcs
import web_console_setup as setup

REPO_SCRIPTS = REPO / "scripts"
POWERSHELL = shutil.which("powershell.exe")

STUB_CONTROL = """\
import json, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
mode_file = root / "stub_mode.txt"
mode = mode_file.read_text(encoding="utf-8").strip() \\
    if mode_file.exists() else "ok"


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


argv = [item for item in sys.argv[1:]]
if "--root" in argv:
    argv = argv[argv.index("--root") + 2:]
subcommand = argv[0] if argv else None
with (root / "stub_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:]}, ensure_ascii=False)
                 + "\\n")

if mode == "sleep":
    time.sleep(12)
    sys.exit(0)
if mode == "garbage":
    sys.stdout.write("definitely not json\\n")
    sys.exit(0)
if mode == "refuse":
    emit({"ok": False, "error": "control refused: caf\\u00e9",
          "error_type": "ControlError"})
    sys.exit(2)

if subcommand == "status":
    status = json.loads(
        (root / "stub_status.json").read_text(encoding="utf-8"))
    status["stop"] = (root / "control" / "STOP").exists()
    emit(status)
    sys.exit(0)
if subcommand == "timeline":
    emit([])
    sys.exit(0)
if subcommand == "interventions":
    emit([])
    sys.exit(0)
emit({"ok": False, "error": "unsupported subcommand", "error_type": "X"})
sys.exit(2)
"""

# Bounded start entry point used instead of the real orchestrator loop: it
# records its invocation (proving the fixed argument-vector contract) and
# flips the stub status document to RUNNING so the post-start verification
# poll can observe the same effect the real START_AGENT_SYSTEM.ps1 produces
# (the real orchestrator writes its RUNNING status at startup).
STUB_START_PS1 = """\
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
Set-Content -LiteralPath (Join-Path $Root "start-marker.txt") -Value "invoked"
$active = Get-Content -LiteralPath (Join-Path $Root "control\\ACTIVE_PROJECT.json") -Raw | ConvertFrom-Json
$statePath = Join-Path $Root "stub_status.json"
$status = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$status.runtime_status = "RUNNING"
$status.project_status = "SUPERVISOR_TURN"
$status.PROJECT_ID = $active.project_id
$status | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $statePath
exit 0
"""

ZCODE_TEMPLATE = (
    "# Executor instructions\n\nYour workspace is <RUNTIME_ROOT>.\n"
    "Line two repeats <RUNTIME_ROOT> once more.\n")

GOAL_TEXT = """# Project Goal

## Objective

Prove that the v1.3 Setup Wizard bootstraps a complete project from the
Console without touching any authoritative state directly.

## Context

This goal is exercised by the P7 HTTP test suite against disposable
fixtures.

## Required Deliverables

- a bootstrapped project with the bound Goal Anchor
- readiness and start evidence under reports/ and evidence/

## Acceptance Criteria

- The bootstrap runs through the real start_project.py helper only.
- The start entry point is invoked with a fixed argument vector.
"""


def write_runtime_files(root: Path) -> None:
    """Assemble one disposable v1.2 Runtime fixture with the real helpers."""
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "supervisor_control.py").write_text(STUB_CONTROL,
                                                   encoding="utf-8")
    for name in ("start_project.py", "preflight.py", "executor_claim.py",
                 "resume_human_review.py", "executor_completion.py",
                 "executor_fence.py"):
        shutil.copyfile(REPO_SCRIPTS / name, scripts / name)
    # The real bootstrap helper loads the Runtime's own orchestrator module
    # from the Runtime Root, exactly as START_PROJECT.ps1 does.
    shutil.copyfile(REPO / "orchestrator.py", root / "orchestrator.py")
    shutil.copytree(REPO / "profiles", root / "profiles",
                    dirs_exist_ok=True)
    (root / "START_AGENT_SYSTEM.ps1").write_text(STUB_START_PS1,
                                                 encoding="utf-8")
    (root / "control").mkdir(exist_ok=True)
    (root / "control" / "CODEX_SUPERVISOR_RUNTIME.md").write_text(
        "# Fixture supervisor rules\n", encoding="utf-8")
    (root / "control" / "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md").write_bytes(
        ZCODE_TEMPLATE.encode("utf-8"))
    (root / "control" / "INFRA_TEST_PLAN.md").write_text(
        "# fixture\n", encoding="utf-8")
    (root / "logs").mkdir(exist_ok=True)
    (root / "handoff").mkdir(exist_ok=True)


def arm_terminal_project(root: Path, project_id: str, message_floor: int) -> None:
    """Arm a terminal (COMPLETE) active project so preflight passes and the
    real bootstrap may replace it with the wizard-created project."""
    (root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps(
        {"schema_version": 1, "project_id": project_id,
         "project_root": f"projects/{project_id}"}), encoding="utf-8")
    project = root / "projects" / project_id
    project.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": 4, "project_id": project_id,
        "project_type": "GENERAL", "profile": "GENERAL",
        "status": "COMPLETE", "phase": "GENERAL",
        "created_at": "2026-09-12T00:00:00+00:00",
        "updated_at": "2026-09-12T00:00:00+00:00",
        "infrastructure_status": "READY",
        "next_message_id": message_floor,
        "final_verification": {"policy_version": 1, "required": True,
                               "status": "PASS"},
        "decision_history": [], "current_task": None,
    }
    (project / "project_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    (project / "PROJECT_GOAL.md").write_text("# Old goal\n",
                                             encoding="utf-8")
    (project / "RESEARCH_STATE.md").write_text("# Memory\n",
                                               encoding="utf-8")
    (root / "control" / "orchestrator_runtime.json").write_text(json.dumps(
        {"schema_version": 2, "status": "RUNNING",
         "retired_message_ids": []}), encoding="utf-8")
    (root / "stub_status.json").write_text(json.dumps({
        "schema_version": 1, "PROJECT_ID": project_id,
        # Deliberately not RUNNING: the start route's verification must
        # observe the TRANSITION to RUNNING (the stub entry point's
        # effect), never the pre-existing state.
        "runtime_status": "PAUSED", "project_status": "COMPLETE",
        "pause": {"status": "RUNNING", "requested_at": None, "mode": None,
                  "resumed_at": None},
        "active_task": None, "last_authorized_dispatch": None,
        "active_task_claimed": False, "active_task_claim_recorded": False,
        "active_task_completion_status": None, "active_task_retired": False,
        "pending_interventions": 0, "last_consumed_message_id": None,
        "human_review": False, "stop": False,
    }, ensure_ascii=False), encoding="utf-8")


def ps_quote(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


class SetupFixture:
    """One console server + one real-helper disposable Runtime fixture."""

    def __init__(self, base: Path, *, project_id: str = "proj-old",
                 message_floor: int = 700400):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True,
                                                  exist_ok=True)
        (self.console_root / "docs").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        (self.console_root / "docs" / "WEB_AI_GOAL_WORKSHOP.md").write_text(
            "# Contract\n\nThe Goal defines the destination.\n",
            encoding="utf-8")
        self.data_dir = base / "console-data"
        self.runtime = base / "runtime-fixture-café"
        write_runtime_files(self.runtime)
        arm_terminal_project(self.runtime, project_id, message_floor)
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=6.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str, path: str, body=None, raw_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=120)
        try:
            headers = {}
            payload = raw_body
            if body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode(
                    "utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                parsed = None
            return response.status, parsed, raw
        finally:
            conn.close()

    def add_runtime(self, label: str = "Fixture café") -> dict:
        status, payload, _ = self.request(
            "POST", "/api/runtimes",
            body={"root": str(self.runtime), "label": label})
        if status != 201:
            raise AssertionError(f"fixture add failed: {status} {payload!r}")
        return payload["runtime"]

    def register_goal(self, runtime_id: str, project_id: str = "proj-new"):
        status, payload, _ = self.request(
            "POST", f"/api/runtimes/{runtime_id}/setup/goal",
            body={"project_id": project_id, "project_type": "GENERAL",
                  "goal_markdown": GOAL_TEXT, "source_name": "goal.md"})
        if status != 200:
            raise AssertionError(f"goal save failed: {status} {payload!r}")
        return payload


class SetupRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="setup-http-",
                                          ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.fixture = SetupFixture(Path(self.temp.name))
        self.addCleanup(self.fixture.server.server_close)
        self.runtime = self.fixture.add_runtime()
        self.rid = self.runtime["id"]

    def setup_path(self, suffix: str) -> str:
        return f"/api/runtimes/{self.rid}/setup/{suffix}"

    # -- state ----------------------------------------------------------------

    def test_state_on_fresh_draft_is_honest(self):
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("state"))
        self.assertEqual(status, 200)
        state = payload["setup"]
        self.assertEqual(state["schema_version"], 1)
        self.assertFalse(state["draft"]["zcode"]["acknowledged"])
        self.assertIsNone(state["draft"]["goal_markdown"])
        self.assertFalse(state["capabilities"]["reported"])
        self.assertTrue(state["zcode_template_available"])
        self.assertFalse(state["project_id_exists_for_draft"])

    def test_state_reflects_saved_goal(self):
        self.fixture.register_goal(self.rid)
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("state"))
        state = payload["setup"]
        self.assertEqual(state["draft"]["project_id"], "proj-new")
        self.assertTrue(state["goal_validation"]["valid"])
        self.assertEqual(state["draft"]["goal_source_name"], "goal.md")

    def test_adversarial_runtime_ids_fail_closed(self):
        for bad in ("zzzzzzzzzzzzzzzz", "0" * 16, "../../etc", ""):
            status, _, _ = self.fixture.request(
                "GET", f"/api/runtimes/{bad}/setup/state")
            self.assertEqual(status, 404, repr(bad))

    def test_draft_survives_server_restart(self):
        self.fixture.register_goal(self.rid)
        second = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0,
            runtime_root=self.fixture.runtime,
            console_root=self.fixture.console_root,
            data_dir=self.fixture.data_dir, control_timeout=6.0)
        thread = threading.Thread(target=second.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(second.server_close)
        port = second.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
        try:
            conn.request("GET", f"/api/runtimes/{self.rid}/setup/state",
                         headers={"Host": f"127.0.0.1:{port}"})
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            conn.close()
        self.assertEqual(payload["setup"]["draft"]["project_id"], "proj-new")

    # -- goal -----------------------------------------------------------------

    def test_goal_save_roundtrips_unicode(self):
        unicode_goal = GOAL_TEXT.replace("Setup Wizard", "設置嚮導 café ✅")
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "proj-cafe-2", "project_type": "GENERAL",
                  "goal_markdown": unicode_goal})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["stored"])
        self.assertTrue(payload["goal_validation"]["valid"])
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("state"))
        self.assertIn("設置嚮導 café ✅", payload["setup"]["draft"]
                      ["goal_markdown"])

    def test_goal_validation_failures_are_not_stored(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "bad path", "project_type": "GENERAL",
                  "goal_markdown": GOAL_TEXT})
        self.assertEqual(status, 200)
        self.assertFalse(payload["stored"])
        self.assertIn("PROJECT_ID_INVALID",
                      [e["code"] for e in
                       payload["goal_validation"]["errors"]])
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "proj-new", "project_type": "RESEARCH",
                  "goal_markdown": GOAL_TEXT})
        self.assertIn("PROJECT_TYPE_INVALID",
                      [e["code"] for e in payload["goal_validation"]["errors"]])
        hostile = GOAL_TEXT.replace(
            "readiness and start evidence under reports/ and evidence/",
            "a dashboard bundle under artifacts/")
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "proj-new", "project_type": "GENERAL",
                  "goal_markdown": hostile})
        self.assertFalse(payload["stored"])
        self.assertIn("PUBLICATION_ROOT_UNSUPPORTED",
                      [e["code"] for e in
                       payload["goal_validation"]["errors"]])
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("state"))
        self.assertIsNone(payload["setup"]["draft"]["project_id"])

    def test_goal_refuses_existing_project(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "proj-old", "project_type": "GENERAL",
                  "goal_markdown": GOAL_TEXT})
        self.assertEqual(status, 200)
        self.assertFalse(payload["stored"])
        self.assertIn("PROJECT_EXISTS",
                      [e["code"] for e in
                       payload["goal_validation"]["errors"]])

    def test_goal_schema_and_size_failures(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "p", "project_type": "GENERAL",
                  "goal_markdown": GOAL_TEXT, "root": "C:\\evil"})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "SETUP_INVALID_PAYLOAD")
        status, _, _ = self.fixture.request("GET", self.setup_path("goal"))
        self.assertEqual(status, 405)
        big = "x" * (setup.GOAL_MAX_BYTES + 100)
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"),
            body={"project_id": "p", "project_type": "GENERAL",
                  "goal_markdown": big})
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "SETUP_GOAL_TOO_LARGE")
        huge_raw = json.dumps({"project_id": "p", "project_type": "GENERAL",
                               "goal_markdown": "y" * 600000}
                              ).encode("utf-8")
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("goal"), raw_body=huge_raw)
        self.assertEqual(status, 413)

    # -- inputs ---------------------------------------------------------------

    def test_input_registration_builds_metadata_inventory(self):
        folder = self.fixture.base / "inputs" / "dataset-café"
        (folder / "sub").mkdir(parents=True)
        (folder / "readme.txt").write_text("input bytes\n", encoding="utf-8")
        (folder / "sub" / "data.csv").write_text("a,b\n1,2\n",
                                                 encoding="utf-8")
        data_file = self.fixture.base / "inputs" / "extra-中文.csv"
        data_file.write_text("x\n", encoding="utf-8")
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"),
            body={"paths": [str(folder), str(data_file)]})
        self.assertEqual(status, 200, payload)
        inventory = payload["inputs"]
        self.assertEqual(inventory["decision"], "REGISTERED")
        self.assertTrue(inventory["complete"])
        paths = [entry["path"] for entry in inventory["entries"]]
        self.assertIn("readme.txt", paths)
        self.assertIn("sub/data.csv", paths)
        self.assertIn("extra-中文.csv", paths)
        all_text = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("input bytes", all_text)
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("state"))
        self.assertEqual(payload["setup"]["draft"]["inputs"]["decision"],
                         "REGISTERED")

    def test_none_needed_decision(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"),
            body={"decision": "NONE_NEEDED"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["inputs"]["decision"], "NONE_NEEDED")

    def test_input_path_refusals(self):
        inputs_dir = self.fixture.base / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)
        relative = inputs_dir / "ok.txt"
        relative.write_text("x\n", encoding="utf-8")
        for label, path in (
                ("relative", "inputs/ok.txt"),
                ("traversal", str(self.fixture.base / "x" / ".." / "y")),
                ("dot-segments", str(self.fixture.base) + "\\inputs\\.\\ok.txt"),
                ("missing", str(self.fixture.base / "gone.txt")),
                ("empty", "   ")):
            status, payload, _ = self.fixture.request(
                "POST", self.setup_path("inputs"),
                body={"paths": [path]})
            self.assertEqual(status, 400, (label, payload))
            self.assertEqual(payload["error"]["code"],
                             "SETUP_INPUT_PATH_INVALID", label)
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"), body={"paths": []})
        self.assertEqual(status, 400)
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"),
            body={"paths": ["C:\\x"] * (setup.INPUT_MAX_SELECTIONS + 1)})
        self.assertEqual(status, 400)

    @unittest.skipUnless(sys.platform == "win32",
                         "junction reparse points are a Windows behavior")
    def test_reparse_points_are_refused_or_skipped(self):
        target_dir = self.fixture.base / "inputs" / "real"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "keep.txt").write_text("keep\n", encoding="utf-8")
        outside = self.fixture.base / "outside-secret.txt"
        outside.write_text("secret\n", encoding="utf-8")
        link = self.fixture.base / "inputs" / "junction-link"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside.parent)],
            capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest("junction creation unavailable")
        # A reparse point as the selection itself is refused.
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"), body={"paths": [str(link)]})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"],
                         "SETUP_INPUT_REPARSE_REFUSED")
        # A reparse point inside a registered folder is skipped, never
        # followed, and the outside file never appears in the inventory.
        containing = self.fixture.base / "inputs" / "container"
        containing.mkdir(exist_ok=True)
        inner_link = containing / "jump"
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(inner_link), str(target_dir)],
            capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest("junction creation unavailable")
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"),
            body={"paths": [str(containing)]})
        self.assertEqual(status, 200)
        entries = [entry["path"] for entry in payload["inputs"]["entries"]]
        self.assertNotIn("jump/keep.txt", entries)
        self.assertNotIn("jump", entries)
        self.assertNotIn("secret", json.dumps(payload))

    def test_cross_root_input_binding_is_refused(self):
        second = self.fixture.base / "second-runtime-café"
        write_runtime_files(second)
        arm_terminal_project(second, "proj-second", 700700)
        status, payload, _ = self.fixture.request(
            "POST", "/api/runtimes",
            body={"root": str(second), "label": "Second"})
        self.assertEqual(status, 201, payload)
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("inputs"),
            body={"paths": [str(second / "orchestrator.py")]})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"],
                         "SETUP_INPUT_CROSS_ROOT_REFUSED")

    # -- supervisor -----------------------------------------------------------

    def test_supervisor_draft_roundtrip(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("supervisor"),
            body={"model": "GPT-5.6 Sol", "reasoning_effort": "HIGH",
                  "explanation_mode": "COMPACT"})
        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["supervisor"]["draft_only"])
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("state"))
        supervisor = payload["setup"]["draft"]["supervisor"]
        self.assertEqual(supervisor["model"], "GPT-5.6 Sol")
        self.assertEqual(supervisor["explanation_mode"], "COMPACT")
        self.assertFalse(payload["setup"]["capabilities"]["reported"])

    def test_supervisor_schema_refusals(self):
        for body in ({"model": "m"}, {"model": None, "reasoning_effort": None,
                                      "explanation_mode": "ULTRA"},
                     {"model": None, "reasoning_effort": None,
                      "explanation_mode": "COMPACT", "project_state": "x"}):
            status, payload, _ = self.fixture.request(
                "POST", self.setup_path("supervisor"), body=body)
            self.assertEqual(status, 400, body)
            self.assertEqual(payload["error"]["code"],
                             "SETUP_INVALID_PAYLOAD")
        status, _, _ = self.fixture.request(
            "GET", self.setup_path("supervisor"))
        self.assertEqual(status, 405)

    # -- zcode ----------------------------------------------------------------

    def test_zcode_prompt_matches_canonical_render(self):
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("zcode"))
        self.assertEqual(status, 200, payload)
        zcode = payload["zcode"]
        template_raw = (self.fixture.runtime / "control"
                        / "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md").read_bytes()
        expected = setup.render_zcode_prompt(
            setup.decode_zcode_template(template_raw), str(self.fixture.runtime))
        self.assertEqual(zcode["prompt"], expected)
        self.assertNotIn("<RUNTIME_ROOT>", zcode["prompt"])
        self.assertTrue(zcode["automation_state"]["unknown_by_design"])
        self.assertEqual(len(zcode["steps"]), 6)
        self.assertFalse(zcode["acknowledged"])

    @unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
    def test_zcode_prompt_is_byte_equivalent_to_the_official_helper(self):
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("zcode"))
        helper = REPO / "PREPARE_ZCODE_AUTOMATION.ps1"
        command = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8"
                   + "; & " + ps_quote(helper)
                   + " -RuntimeRoot " + ps_quote(self.fixture.runtime)
                   + " -NoClipboard -OutputPrompt")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", command],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", cwd=str(self.fixture.runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = result.stdout.split("Rendered prompt:\n", 1)[1]
        self.assertEqual(payload["zcode"]["prompt"].rstrip("\n"),
                         rendered.rstrip("\n"))

    def test_zcode_acknowledge_flow(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("zcode/acknowledge"), body={})
        self.assertEqual(status, 200)
        self.assertTrue(payload["zcode"]["acknowledged"])
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("zcode"))
        self.assertTrue(payload["zcode"]["acknowledged"])
        status, _, _ = self.fixture.request(
            "GET", self.setup_path("zcode/acknowledge"))
        self.assertEqual(status, 405)

    def test_zcode_template_missing_fails_closed(self):
        template = (self.fixture.runtime / "control"
                    / "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md")
        saved = template.read_bytes()
        template.unlink()
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("zcode"))
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"],
                         "ZCODE_PROMPT_TEMPLATE_UNAVAILABLE")
        template.write_bytes(saved)

    # -- workshop -------------------------------------------------------------

    def test_workshop_pack_is_bounded_and_canonical(self):
        self.fixture.register_goal(self.rid)
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("goal-workshop"))
        self.assertEqual(status, 200, payload)
        pack = payload["goal_workshop"]
        self.assertIn("The Goal defines the destination.", pack
                      ["context_pack"])
        self.assertIn("GENERAL", pack["context_pack"])
        self.assertIn("context pack", pack["startup_prompt"])
        self.assertLessEqual(len(pack["context_pack"].encode("utf-8")),
                             setup.CONTEXT_PACK_MAX_BYTES)
        self.assertEqual(pack["external_automation"], "none_by_design")
        status, _, _ = self.fixture.request(
            "POST", self.setup_path("goal-workshop"), body={})
        self.assertEqual(status, 405)

    # -- readiness ------------------------------------------------------------

    def test_readiness_all_pass_after_full_setup(self):
        self.fixture.register_goal(self.rid)
        self.fixture.request("POST", self.setup_path("inputs"),
                             body={"decision": "NONE_NEEDED"})
        self.fixture.request("POST", self.setup_path("supervisor"),
                             body={"model": None, "reasoning_effort": None,
                                   "explanation_mode": "COMPACT"})
        self.fixture.request("POST", self.setup_path("zcode/acknowledge"),
                             body={})
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("readiness"))
        self.assertEqual(status, 200, payload)
        readiness = payload["readiness"]
        self.assertTrue(readiness["ready"],
                        json.dumps(readiness["checks"], ensure_ascii=False))
        keys = {check["key"]: check["state"] for check in readiness["checks"]}
        self.assertEqual(len(keys), 10)
        self.assertTrue(all(state == "PASS" for state in keys.values()))

    def test_readiness_reflects_each_missing_input(self):
        self.fixture.register_goal(self.rid)
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("readiness"))
        keys = {check["key"]: check["state"]
                for check in payload["readiness"]["checks"]}
        self.assertFalse(payload["readiness"]["ready"])
        self.assertEqual(keys["inputs_registered"], "FAIL")
        self.assertEqual(keys["zcode_acknowledged"], "FAIL")
        self.assertEqual(keys["goal_loaded"], "PASS")
        self.assertEqual(keys["runtime_healthy"], "PASS")
        self.assertEqual(keys["preflight"], "PASS")

    def test_readiness_with_control_plane_refused(self):
        (self.fixture.runtime / "stub_mode.txt").write_text(
            "refuse", encoding="utf-8")
        self.addCleanup((self.fixture.runtime / "stub_mode.txt").unlink,
                        missing_ok=True)
        status, payload, _ = self.fixture.request(
            "GET", self.setup_path("readiness"))
        self.assertEqual(status, 200)
        keys = {check["key"]: check["state"]
                for check in payload["readiness"]["checks"]}
        self.assertEqual(keys["runtime_healthy"], "FAIL")
        self.assertFalse(payload["readiness"]["ready"])

    # -- start ----------------------------------------------------------------

    def start_full_setup(self):
        self.fixture.register_goal(self.rid)
        self.fixture.request("POST", self.setup_path("inputs"),
                             body={"decision": "NONE_NEEDED"})
        self.fixture.request("POST", self.setup_path("supervisor"),
                             body={"model": None, "reasoning_effort": None,
                                   "explanation_mode": "COMPACT"})
        self.fixture.request("POST", self.setup_path("zcode/acknowledge"),
                             body={})

    def test_start_happy_path_through_real_bootstrap(self):
        self.start_full_setup()
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("start"), body={})
        self.assertEqual(status, 200, json.dumps(payload, ensure_ascii=False))
        start = payload["start"]
        self.assertEqual(start["bootstrap"]["event"], "PROJECT_CREATED")
        self.assertEqual(start["bootstrap"]["project_id"], "proj-new")
        self.assertEqual(start["verification"], "STARTED_VERIFIED")
        project_dir = self.fixture.runtime / "projects" / "proj-new"
        self.assertTrue((project_dir / "project_state.json").is_file())
        self.assertTrue((project_dir / "PROJECT_GOAL.md").is_file())
        self.assertTrue((self.fixture.runtime
                         / "control" / "ACTIVE_PROJECT.json").is_file())
        self.assertTrue((self.fixture.runtime
                         / "start-marker.txt").is_file())
        active = json.loads((self.fixture.runtime / "control"
                             / "ACTIVE_PROJECT.json").read_text(
                                 encoding="utf-8"))
        self.assertEqual(active["project_id"], "proj-new")
        # Transition into the selected Runtime's Cockpit.
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.rid}/cockpit")
        self.assertEqual(status, 200)
        interpretation = payload["cockpit"]["interpretation"]
        self.assertEqual(interpretation["state"]["family"], "CODEX_THINKING")
        # The Console wrote the goal nowhere authoritative: the only new
        # project files are the bootstrap-created ones.
        self.assertTrue((project_dir / "PROJECT_GOAL.md").read_text(
            encoding="utf-8").strip().endswith(
                GOAL_TEXT.strip().splitlines()[-1]))

    def test_start_refuses_when_not_ready(self):
        self.fixture.register_goal(self.rid)
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("start"), body={})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "SETUP_NOT_READY")
        failing = payload["error"]["detail"]["failing_checks"]
        self.assertIn("zcode_acknowledged", failing)
        self.assertIn("inputs_registered", failing)
        self.assertFalse((self.fixture.runtime
                          / "projects" / "proj-new").exists())

    def test_start_refuses_duplicate_start_after_success(self):
        self.start_full_setup()
        status, _, _ = self.fixture.request(
            "POST", self.setup_path("start"), body={})
        self.assertEqual(status, 200)
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("start"), body={})
        self.assertEqual(status, 409)
        self.assertIn(payload["error"]["code"],
                      ("SETUP_NOT_READY", "SETUP_ACTIVE_PROJECT_OCCUPIED",
                       "SETUP_PROJECT_EXISTS"))

    def test_racing_start_requests_produce_exactly_one_project(self):
        self.start_full_setup()
        results = []
        lock = threading.Lock()

        def runner():
            status, payload, _ = self.fixture.request(
                "POST", self.setup_path("start"), body={})
            with lock:
                results.append((status, payload))

        threads = [threading.Thread(target=runner) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=180)
        statuses = sorted(status for status, _ in results)
        self.assertEqual(statuses[0], 200, results)
        self.assertEqual(statuses[1], 409, results)
        projects = sorted(
            path.name for path in
            (self.fixture.runtime / "projects").iterdir())
        self.assertEqual(projects.count("proj-new"), 1)

    def test_start_without_goal_is_refused_before_bootstrap(self):
        status, payload, _ = self.fixture.request(
            "POST", self.setup_path("start"), body={})
        self.assertEqual(status, 409)
        self.assertFalse((self.fixture.runtime / "start-marker.txt").exists())

    def test_method_allowlists(self):
        for suffix in ("state", "zcode", "goal-workshop", "readiness"):
            status, _, _ = self.fixture.request(
                "POST", self.setup_path(suffix), body={})
            self.assertEqual(status, 405, suffix)
        for suffix in ("goal", "inputs", "supervisor", "start"):
            status, _, _ = self.fixture.request(
                "GET", self.setup_path(suffix))
            self.assertEqual(status, 405, suffix)


class SetupIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="setup-iso-",
                                          ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # One console server managing two unmistakable disposable Runtimes.
        self.fixture = SetupFixture(self.base / "a", project_id="proj-a",
                                    message_floor=700500)
        self.addCleanup(self.fixture.server.server_close)
        self.runtime_b_root = self.base / "b" / "runtime-beta-中文"
        write_runtime_files(self.runtime_b_root)
        arm_terminal_project(self.runtime_b_root, "proj-b", 700600)
        self.runtime_a = self.fixture.add_runtime(label="A")
        status, payload, _ = self.fixture.request(
            "POST", "/api/runtimes",
            body={"root": str(self.runtime_b_root), "label": "B"})
        self.assertEqual(status, 201, payload)
        self.runtime_b = payload["runtime"]
        self.rid_a = self.runtime_a["id"]
        self.rid_b = self.runtime_b["id"]

    def test_drafts_never_leak_between_runtimes(self):
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.rid_a}/setup/goal",
            body={"project_id": "proj-new-a", "project_type": "GENERAL",
                  "goal_markdown": GOAL_TEXT})
        self.assertEqual(status, 200)
        self.assertTrue(payload["stored"])
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.rid_b}/setup/state")
        self.assertEqual(status, 200, payload)
        draft_b = payload["setup"]["draft"]
        self.assertIsNone(draft_b["project_id"])
        self.assertIsNone(draft_b["goal_markdown"])
        # Start under B cannot use A's draft: B is not ready.
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.rid_b}/setup/start", body={})
        self.assertEqual(status, 409)

    def test_start_under_b_does_not_touch_a(self):
        self.fixture.request(
            "POST", f"/api/runtimes/{self.rid_b}/setup/goal",
            body={"project_id": "proj-new-b", "project_type": "GENERAL",
                  "goal_markdown": GOAL_TEXT})
        for suffix, body in (("inputs", {"decision": "NONE_NEEDED"}),
                             ("supervisor", {"model": None,
                                             "reasoning_effort": None,
                                             "explanation_mode": "COMPACT"}),
                             ("zcode/acknowledge", {})):
            status, _, _ = self.fixture.request(
                "POST", f"/api/runtimes/{self.rid_b}/setup/{suffix}",
                body=body)
            self.assertEqual(status, 200, suffix)
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.rid_b}/setup/start", body={})
        self.assertEqual(status, 200, payload)
        self.assertEqual(
            json.loads((self.runtime_b_root / "control"
                        / "ACTIVE_PROJECT.json").read_text(
                            encoding="utf-8"))["project_id"],
            "proj-new-b")
        self.assertEqual(
            json.loads((self.fixture.runtime / "control"
                        / "ACTIVE_PROJECT.json").read_text(
                            encoding="utf-8"))["project_id"],
            "proj-a")


if __name__ == "__main__":
    unittest.main()
