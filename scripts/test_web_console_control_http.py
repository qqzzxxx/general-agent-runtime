"""P5 Human Control HTTP endpoint tests (offline, localhost).

Covers the mutation-capable routes on top of the P2 opaque-ID root binding:

- `GET  /api/runtimes/<id>/controls`                  pending-controls document
- `POST /api/runtimes/<id>/controls/pause`            Safe Pause / interrupt pause
- `POST /api/runtimes/<id>/controls/resume`           Resume
- `POST /api/runtimes/<id>/controls/intervention`     STEER / AUDIT submission
- `POST /api/runtimes/<id>/controls/stop/prepare`     STOP challenge issuance
- `POST /api/runtimes/<id>/controls/stop/confirm`     confirmed Formal STOP
- `POST /api/runtimes/<id>/controls/human-review/*`   Human Decision prepare/apply

Fail-closed behavior is pinned over the wire: typed request schemas, argument
vector contracts against a stub control plane, refusals, timeouts, adversarial
Runtime IDs, root rebinding, replayed/stale/cross-Runtime STOP confirmations,
and Runtime-scoped Human Decision receipts. No GUI and no external network.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_server as wcs

REPO_SCRIPTS = REPO / "scripts"

MARKER_A = "ALPHA-café"
MARKER_B = "BETA-中文"

STATUS_A = {
    "schema_version": 1, "PROJECT_ID": "proj-alpha",
    "runtime_status": "RUNNING", "project_status": "WAITING_EXECUTOR",
    "pause": {"status": "RUNNING", "requested_at": None, "mode": None,
              "resumed_at": None},
    "active_task": {"MESSAGE_ID": 700501, "TASK_ID": "TASK-A-α",
                    "STAGE_ID": "stage-a1", "ATTEMPT": 1, "NONCE": "a" * 24},
    "last_authorized_dispatch": None,
    "active_task_claimed": False, "active_task_claim_recorded": False,
    "active_task_completion_status": None, "active_task_retired": False,
    "pending_interventions": 0, "last_consumed_message_id": None,
    "human_review": False, "stop": False,
}
STATUS_B = dict(STATUS_A, PROJECT_ID="proj-beta", active_task=None)

STUB_CONTROL = """\
import json, os, sys, time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
mode = "ok"
mode_file = root / "stub_mode.txt"
if mode_file.exists():
    mode = mode_file.read_text(encoding="utf-8").strip()


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


def read_json(path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


argv = [item for item in sys.argv[1:]]
if "--root" in argv:
    argv = argv[argv.index("--root") + 2:]
subcommand = argv[0] if argv else None
flags = argv[1:]
with (root / "stub_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"argv": sys.argv[1:]}, ensure_ascii=False) + "\\n")

if mode == "sleep":
    time.sleep(12)
    sys.exit(0)
if mode == "garbage":
    sys.stdout.write("definitely not json\\n")
    sys.exit(0)
if mode == "exit1_plain":
    emit({"unexpected": True})
    sys.stderr.write("broken control plane\\n")
    sys.exit(1)
if mode == "refuse" or (mode == "refuse_interventions"
                        and subcommand == "interventions"):
    emit({"ok": False, "error": "control refused: café",
          "error_type": "ControlError"})
    sys.exit(2)

if subcommand == "status":
    status = read_json(root / "stub_status.json", {"schema_version": 1})
    pause_file = root / "stub_control_pause.json"
    if pause_file.exists():
        status = dict(status)
        status["pause"] = read_json(pause_file, status.get("pause"))
    status = dict(status)
    status["stop"] = (root / "control" / "STOP").exists()
    emit(status)
    sys.exit(0)
if subcommand == "interventions":
    emit(read_json(root / "stub_interventions.json", []))
    sys.exit(0)
if subcommand == "pause":
    interrupt = "--interrupt-current-task" in flags
    pause = {"status": "PAUSED", "requested_at": "2026-09-12T02:00:00+00:00",
             "mode": "INTERRUPT_CURRENT" if interrupt else "SAFE",
             "resumed_at": None,
             "disposition": ("PAUSED_CURRENT_REVOKED" if interrupt
                             else "PAUSED_UNCLAIMED_RETIRED")}
    (root / "stub_control_pause.json").write_text(
        json.dumps(pause, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(pause)
    sys.exit(0)
if subcommand == "resume":
    previous = read_json(root / "stub_control_pause.json", {})
    pause = {"status": "RUNNING", "requested_at": previous.get("requested_at"),
             "mode": previous.get("mode"), "resumed_at": "2026-09-12T03:00:00+00:00"}
    (root / "stub_control_pause.json").write_text(
        json.dumps(pause, ensure_ascii=False, indent=2), encoding="utf-8")
    emit({"previous": previous, "current": pause,
          "startup": {"status": "READY", "verified": True}})
    sys.exit(0)
if subcommand == "intervene":
    record = {"intervention_id": "intervention-stub-1", "mode": "STEER",
              "target_message_id": None, "interrupt_current": False,
              "submitted_at": "2026-09-12T02:30:00+00:00",
              "status": "PENDING", "integrity": "OK",
              "instruction_text": "", "disposition":
              "PENDING_NEXT_SUPERVISOR_TURN"}
    for flag in flags:
        if flag.startswith("--text="):
            record["instruction_text"] = flag[len("--text="):]
        if flag.startswith("--mode="):
            record["mode"] = flag[len("--mode="):]
        if flag.startswith("--target-message-id="):
            record["target_message_id"] = int(
                flag[len("--target-message-id="):])
        if flag == "--interrupt-current-task":
            record["interrupt_current"] = True
    records = read_json(root / "stub_interventions.json", [])
    records.append(record)
    (root / "stub_interventions.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    emit(record)
    sys.exit(0)
emit({"ok": False, "error": "unknown subcommand", "error_type": "ControlError"})
sys.exit(2)
"""

STUB_HUMAN_REVIEW = """\
import json, sys, uuid
from pathlib import Path

root = Path(__file__).resolve().parents[1]
mode = "ok"
mode_file = root / "stub_hr_mode.txt"
if mode_file.exists():
    mode = mode_file.read_text(encoding="utf-8").strip()

argv = [item for item in sys.argv[1:]]
if "--root" in argv:
    argv = argv[argv.index("--root") + 2:]
subcommand = argv[0] if argv else None
with (root / "stub_calls.jsonl").open("a", encoding="utf-8") as handle:
    handle.write(json.dumps({"helper": subcommand}, ensure_ascii=False) + "\\n")


def fail(code, message):
    print(f"HUMAN_REVIEW_RESUME_FAILED: {message}", file=sys.stderr)
    sys.exit(code)


if mode == "refuse":
    fail(4, "stale or duplicate (stub)")
if mode == "conflict":
    fail(3, "conflict (stub)")

def value_of(name):
    return argv[argv.index(name) + 1]

if subcommand == "prepare":
    payload = json.loads(Path(value_of("--decision-file")).read_text(
        encoding="utf-8"))
    receipt = {
        "schema_version": 1,
        "receipt_id": "human-decision-" + uuid.uuid4().hex,
        "project_id": value_of("--project-id"),
        "human_decision": payload,
        "submitted_at": "2026-09-12T04:00:00+00:00",
        "previous_project_state_sha256": "b" * 64,
        "receipt_sha256": "a" * 64,
    }
    out = Path(value_of("--receipt-out"))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\\n",
                   encoding="utf-8")
    print(json.dumps({
        "event": "HUMAN_DECISION_RECEIPT_PREPARED",
        "project_id": receipt["project_id"],
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "previous_project_state_sha256":
            receipt["previous_project_state_sha256"],
    }, ensure_ascii=False, indent=2))
    sys.exit(0)
if subcommand == "apply":
    receipt = json.loads(Path(value_of("--receipt")).read_text(
        encoding="utf-8"))
    print(json.dumps({
        "event": "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR",
        "project_id": receipt["project_id"],
        "previous_status": "HUMAN_REVIEW",
        "status": "SUPERVISOR_TURN",
        "receipt_id": receipt["receipt_id"],
        "receipt_sha256": receipt["receipt_sha256"],
        "next_message_id_unchanged": 700100,
        "executor_dispatched": False,
    }, ensure_ascii=False, indent=2))
    sys.exit(0)
fail(5, "unknown command (stub)")
"""

STOP_PS1_NAME = "STOP_AGENT_SYSTEM.ps1"


def write_stub_runtime(root: Path, status: dict) -> None:
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "supervisor_control.py").write_text(
        STUB_CONTROL, encoding="utf-8")
    (scripts / "resume_human_review.py").write_text(
        STUB_HUMAN_REVIEW, encoding="utf-8")
    (root / "control").mkdir(parents=True, exist_ok=True)
    (root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps(
        {"schema_version": 1, "project_id": status["PROJECT_ID"],
         "project_root": f"projects/{status['PROJECT_ID']}"}), encoding="utf-8")
    project = root / "projects" / status["PROJECT_ID"]
    project.mkdir(parents=True, exist_ok=True)
    (project / "project_state.json").write_text(
        json.dumps({"status": status["project_status"],
                    "project_id": status["PROJECT_ID"]}), encoding="utf-8")
    (root / "stub_status.json").write_text(
        json.dumps(status, ensure_ascii=False), encoding="utf-8")
    (root / "stub_interventions.json").write_text("[]", encoding="utf-8")
    shutil.copyfile(REPO / STOP_PS1_NAME, root / STOP_PS1_NAME)


class ControlFixture:
    """One console server + two unmistakably different stub Runtimes."""

    def __init__(self, base: Path):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.runtime_a = base / "runtime-alpha"
        self.runtime_b = base / "runtime-beta"
        write_stub_runtime(self.runtime_a, dict(STATUS_A))
        write_stub_runtime(self.runtime_b, dict(STATUS_B))
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_a,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=4.0)
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "/", body=None,
                raw_body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=60)
        try:
            headers = {}
            payload = None if raw_body is None else raw_body
            if body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
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

    def add_runtime(self, root: Path, label: str) -> dict:
        status, payload, _ = self.request(
            "POST", "/api/runtimes", body={"root": str(root), "label": label})
        if status != 201:
            raise AssertionError(f"fixture add failed: {status} {payload!r}")
        return payload["runtime"]

    def calls(self, root: Path) -> list:
        calls_file = root / "stub_calls.jsonl"
        if not calls_file.exists():
            return []
        return [json.loads(line) for line in
                calls_file.read_text(encoding="utf-8").splitlines()]

    def control_calls(self, root: Path, command: str) -> list:
        """Stub invocations of one control subcommand (registry status
        probes are recorded too, so callers must filter)."""
        return [call for call in self.calls(root)
                if "argv" in call and command in call["argv"][2:]]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class ControlEndpointTestsBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = ControlFixture(Path(self._tmp.name))
        self.runtime_a = self.fixture.add_runtime(
            self.fixture.runtime_a, "Runtime A — café")
        self.runtime_b = self.fixture.add_runtime(
            self.fixture.runtime_b, "Runtime B — 中文")

    def tearDown(self):
        self.fixture.close()
        self._tmp.cleanup()

    def post_control(self, runtime_id, action, body):
        return self.fixture.request(
            "POST", f"/api/runtimes/{runtime_id}/controls/{action}", body=body)

    def get_controls(self, runtime_id):
        return self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/controls")

    def set_mode(self, mode):
        (self.fixture.runtime_a / "stub_mode.txt").write_text(
            mode, encoding="utf-8")
        self.addCleanup(lambda: (self.fixture.runtime_a / "stub_mode.txt")
                        .unlink(missing_ok=True))


class PauseResumeInterventionTests(ControlEndpointTestsBase):
    def test_safe_pause_round_trips_the_exact_argv_contract(self):
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "pause", {"mode": "SAFE"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["control"]["command"], "pause")
        self.assertEqual(payload["runtime"]["id"], self.runtime_a["id"])
        calls = self.fixture.control_calls(self.fixture.runtime_a, "pause")
        self.assertEqual(len(calls), 1)
        argv = calls[0]["argv"]
        self.assertEqual(argv[0], "--root")
        self.assertEqual(argv[1], str(self.fixture.runtime_a))
        self.assertIn("pause", argv)
        self.assertLess(argv.index("--root"), argv.index("pause"))
        self.assertIn("--json", argv)
        self.assertNotIn("--interrupt-current-task", argv)

    def test_interrupt_pause_passes_the_authoritative_flag(self):
        status, payload, _ = self.post_control(
            self.runtime_a["id"], "pause", {"mode": "INTERRUPT_CURRENT"})
        self.assertEqual(status, 200)
        argv = self.fixture.control_calls(
            self.fixture.runtime_a, "pause")[0]["argv"]
        self.assertIn("--interrupt-current-task", argv)
        self.assertEqual(payload["control"]["result"]["mode"],
                         "INTERRUPT_CURRENT")

    def test_resume_round_trips(self):
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "resume", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        argv = self.fixture.control_calls(
            self.fixture.runtime_a, "resume")[0]["argv"]
        self.assertEqual(argv[0], "--root")
        self.assertIn("resume", argv)
        self.assertIn("--json", argv)

    def test_resume_without_startup_proof_is_not_reported_as_success(self):
        script = self.fixture.runtime_a / "scripts" / "supervisor_control.py"
        script.write_text(STUB_CONTROL.replace('"verified": True', '"verified": False'),
                          encoding="utf-8")
        status, payload, _ = self.post_control(self.runtime_a["id"], "resume", {})
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "RESUME_UNVERIFIED")

    def test_intervention_with_unicode_comment_target_and_interrupt(self):
        comment = "请优先修复回归 ✅ café <script>x</script>"
        status, payload, _ = self.post_control(
            self.runtime_a["id"], "intervention",
            {"mode": "STEER", "comment": comment,
             "target_message_id": 700501, "interrupt_current": True})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        argv = self.fixture.control_calls(
            self.fixture.runtime_a, "intervene")[0]["argv"]
        self.assertIn(f"--text={comment}", argv)
        self.assertIn("--mode=STEER", argv)
        self.assertIn("--target-message-id=700501", argv)
        self.assertIn("--interrupt-current-task", argv)
        self.assertEqual(payload["control"]["result"]["instruction_text"],
                         comment)

    def test_project_level_intervention_has_no_target_flag(self):
        status, payload, _ = self.post_control(
            self.runtime_a["id"], "intervention",
            {"mode": "AUDIT", "comment": "audit the latest evidence",
             "target_message_id": None, "interrupt_current": False})
        self.assertEqual(status, 200)
        argv = self.fixture.control_calls(
            self.fixture.runtime_a, "intervene")[0]["argv"]
        self.assertNotIn("--target-message-id", argv)
        self.assertFalse(any(item.startswith("--target-message-id=")
                             for item in argv))
        self.assertIn("--mode=AUDIT", argv)
        self.assertNotIn("--interrupt-current-task", argv)

    def test_interventions_can_target_a_different_runtime_independently(self):
        status, payload, _ = self.post_control(
            self.runtime_b["id"], "intervention",
            {"mode": "STEER", "comment": "beta only 中文",
             "target_message_id": None, "interrupt_current": False})
        self.assertEqual(status, 200)
        beta_calls = self.fixture.control_calls(self.fixture.runtime_b,
                                                "intervene")
        self.assertEqual(len(beta_calls), 1)
        self.assertEqual(beta_calls[0]["argv"][1], str(self.fixture.runtime_b))
        self.assertEqual(self.fixture.control_calls(self.fixture.runtime_a,
                                                    "intervene"), [])

    def test_control_refusal_is_a_structured_conflict(self):
        self.set_mode("refuse")
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "pause", {"mode": "SAFE"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "CONTROL_ACTION_REFUSED")
        self.assertEqual(payload["error"]["detail"]["control_command"],
                         "pause")
        self.assertIn("café", payload["error"]["detail"]["control_error"]
                      ["error"])

    def test_malformed_control_output_is_a_502(self):
        self.set_mode("garbage")
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "pause", {"mode": "SAFE"})
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"],
                         "CONTROL_PLANE_MALFORMED_OUTPUT")

    def test_plain_control_failure_is_a_502(self):
        self.set_mode("exit1_plain")
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "pause", {"mode": "SAFE"})
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")

    def test_control_timeout_is_a_504(self):
        self.set_mode("sleep")
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "pause", {"mode": "SAFE"})
        self.assertEqual(status, 504)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_TIMEOUT")

    def test_invalid_bodies_fail_closed_without_invoking_the_control_plane(
            self):
        bad_bodies = [
            {},
            {"mode": "SAFE", "reason": "extra"},
            {"mode": "safe"},
            {"mode": 5},
        ]
        for body in bad_bodies:
            status, payload, _ = self.post_control(self.runtime_a["id"],
                                                   "pause", body)
            self.assertEqual(status, 400, repr(body))
            self.assertEqual(payload["error"]["code"],
                             "CONTROL_INVALID_PAYLOAD", repr(body))
        # A JSON body that is not an object is refused by the shared P2 body
        # reader before the typed schema applies.
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "pause", "SAFE")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "REGISTRY_INVALID_PAYLOAD")
        bad_interventions = [
            {"mode": "STEER", "comment": "   ", "target_message_id": None,
             "interrupt_current": False},
            {"mode": "STEER", "comment": "x" * 4001,
             "target_message_id": None, "interrupt_current": False},
            {"mode": "STEER", "comment": "ok", "target_message_id": -1,
             "interrupt_current": False},
            {"mode": "STEER", "comment": "ok", "target_message_id": None,
             "interrupt_current": "yes"},
            {"mode": "STEER", "comment": "ok"},
            {"mode": "AUDIT", "comment": "ok", "target_message_id": None,
             "interrupt_current": False, "root": "C:/elsewhere"},
        ]
        for body in bad_interventions:
            status, payload, _ = self.post_control(self.runtime_a["id"],
                                                   "intervention", body)
            self.assertEqual(status, 400, repr(body))
            self.assertEqual(payload["error"]["code"],
                             "CONTROL_INVALID_PAYLOAD", repr(body))
        status, payload, _ = self.post_control(self.runtime_a["id"],
                                               "resume", {"force": True})
        self.assertEqual(status, 400)
        for command in ("pause", "resume", "intervene"):
            self.assertEqual(
                self.fixture.control_calls(self.fixture.runtime_a, command),
                [])

    def test_oversized_request_body_is_refused(self):
        body = json.dumps({"mode": "STEER", "comment": "x" * 70000,
                           "target_message_id": None,
                           "interrupt_current": False}).encode("utf-8")
        status, payload, _ = self.fixture.request(
            "POST",
            f"/api/runtimes/{self.runtime_a['id']}/controls/intervention",
            raw_body=body,
            )
        self.assertIn(status, (400, 413))

    def test_mutation_routes_are_post_only(self):
        base = f"/api/runtimes/{self.runtime_a['id']}/controls"
        for path in (f"{base}/pause", f"{base}/resume",
                     f"{base}/intervention", f"{base}/stop/prepare",
                     f"{base}/stop/confirm",
                     f"{base}/human-review/prepare",
                     f"{base}/human-review/apply"):
            status, payload, raw = self.fixture.request("GET", path)
            self.assertEqual(status, 405, path)
            self.assertEqual(payload["error"]["code"], "METHOD_NOT_ALLOWED")
        status, payload, _ = self.fixture.request("POST",
                                                  f"{base}", body={})
        self.assertEqual(status, 405)

    def test_unknown_and_adversarial_runtime_ids_fail_closed(self):
        real_id = self.runtime_a["id"]
        for bad in ("0" * 16, "z" * 16, "x" * 17, "", "%2e%2e%2f"):
            status, _, _ = self.post_control(bad, "pause", {"mode": "SAFE"})
            self.assertEqual(status, 404, repr(bad))
        status, _, _ = self.post_control(real_id + "0", "pause",
                                         {"mode": "SAFE"})
        self.assertEqual(status, 404)

    def test_body_cannot_rebind_the_runtime_root(self):
        status, payload, _ = self.post_control(
            self.runtime_a["id"], "pause",
            {"mode": "SAFE", "root": str(self.fixture.runtime_b)})
        self.assertEqual(status, 400)
        self.assertEqual(self.fixture.control_calls(self.fixture.runtime_b,
                                                    "pause"), [])
        status, payload, _ = self.post_control(
            self.runtime_a["id"], "stop/prepare",
            {"root": str(self.fixture.runtime_b)})
        self.assertEqual(status, 400)


class PendingControlsDocumentTests(ControlEndpointTestsBase):
    def test_controls_document_reflects_authoritative_facts(self):
        status, payload, _ = self.get_controls(self.runtime_a["id"])
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtime"]["id"], self.runtime_a["id"])
        controls = payload["controls"]
        self.assertEqual(controls["schema_version"], 1)
        self.assertEqual(controls["pause"]["state"], "NONE")
        self.assertFalse(controls["stop"]["applied"])
        self.assertTrue(controls["interventions"]["available"])
        self.assertFalse(controls["human_review"]["active"])

    def test_pause_then_controls_shows_the_applied_state(self):
        status, _, _ = self.post_control(self.runtime_a["id"], "pause",
                                         {"mode": "SAFE"})
        self.assertEqual(status, 200)
        status, payload, _ = self.get_controls(self.runtime_a["id"])
        self.assertEqual(payload["controls"]["pause"]["state"], "APPLIED")
        self.assertEqual(payload["controls"]["pause"]["mode"], "SAFE")

    def test_pending_and_consumed_interventions_are_separated(self):
        records = [
            {"intervention_id": "H-1", "mode": "STEER",
             "target_message_id": 700501, "interrupt_current": False,
             "submitted_at": "2026-09-12T01:00:00+00:00",
             "status": "PENDING", "integrity": "OK"},
            {"intervention_id": "H-2", "mode": "AUDIT",
             "target_message_id": None, "interrupt_current": False,
             "submitted_at": "2026-09-12T00:30:00+00:00",
             "status": "CONSUMED", "integrity": "OK",
             "consumed_at": "2026-09-12T01:30:00+00:00"},
            {"intervention_id": "H-3", "mode": "STEER",
             "target_message_id": None, "interrupt_current": False,
             "submitted_at": "2026-09-12T00:00:00+00:00",
             "status": "PENDING", "integrity": "HASH_MISMATCH"},
        ]
        (self.fixture.runtime_a / "stub_interventions.json").write_text(
            json.dumps(records, ensure_ascii=False), encoding="utf-8")
        status, payload, _ = self.get_controls(self.runtime_a["id"])
        controls = payload["controls"]
        categories = {item["intervention_id"]: item["category"]
                      for item in controls["interventions"]["items"]}
        self.assertEqual(categories,
                         {"H-1": "pending", "H-2": "consumed",
                          "H-3": "failed"})
        self.assertEqual(controls["counts"]["pending"], 1)
        self.assertEqual(controls["counts"]["consumed"], 1)
        self.assertEqual(controls["counts"]["failed"], 1)

    def test_intervention_source_failure_is_honest_not_fatal(self):
        self.set_mode("refuse_interventions")
        status, payload, _ = self.get_controls(self.runtime_a["id"])
        self.assertEqual(status, 200)
        controls = payload["controls"]
        self.assertFalse(controls["interventions"]["available"])
        self.assertTrue(controls["honesty"]["notes"])
        self.assertEqual(controls["pause"]["state"], "NONE")

    def test_status_failure_fails_the_controls_document(self):
        self.set_mode("garbage")
        status, payload, _ = self.get_controls(self.runtime_a["id"])
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"],
                         "CONTROL_PLANE_MALFORMED_OUTPUT")

    def test_controls_isolation_between_runtimes(self):
        self.post_control(self.runtime_a["id"], "pause", {"mode": "SAFE"})
        status, payload, _ = self.get_controls(self.runtime_b["id"])
        self.assertEqual(payload["controls"]["pause"]["state"], "NONE")
        self.assertEqual(payload["runtime"]["id"], self.runtime_b["id"])

    def test_controls_route_is_get_only(self):
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_a['id']}/controls",
            body={})
        self.assertEqual(status, 405)


class StopControlTests(ControlEndpointTestsBase):
    def prepare(self, runtime_id=None):
        return self.fixture.request(
            "POST",
            f"/api/runtimes/{runtime_id or self.runtime_a['id']}"
            f"/controls/stop/prepare",
            body={})

    def confirm(self, challenge, runtime_id=None, token=None,
                project_id=None):
        return self.fixture.request(
            "POST",
            f"/api/runtimes/{runtime_id or self.runtime_a['id']}"
            f"/controls/stop/confirm",
            body={"challenge_id": challenge["challenge_id"],
                  "confirmation_token": token or challenge["confirmation_token"],
                  "project_id": project_id or challenge["project_id"]})

    def test_stop_challenge_flow_applies_the_formal_stop_path(self):
        status, payload, _ = self.prepare()
        self.assertEqual(status, 200)
        challenge = payload["stop_challenge"]
        self.assertEqual(challenge["schema_version"], 1)
        self.assertRegex(challenge["challenge_id"], r"^[0-9a-f]{32}$")
        self.assertRegex(challenge["confirmation_token"], r"^[0-9a-f]{32}$")
        self.assertEqual(challenge["project_id"], "proj-alpha")
        self.assertEqual(challenge["expires_in_seconds"],
                         wcs.STOP_CHALLENGE_TTL_SECONDS)
        status, payload, _ = self.confirm(challenge)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["stop"]["applied"])
        self.assertTrue((self.fixture.runtime_a / "control" / "STOP")
                        .exists())
        self.assertFalse((self.fixture.runtime_b / "control" / "STOP")
                         .exists())
        status, payload, _ = self.get_controls(self.runtime_a["id"])
        self.assertTrue(payload["controls"]["stop"]["applied"])

    def test_stop_confirm_rejects_a_wrong_token_without_consuming(self):
        challenge = self.prepare()[1]["stop_challenge"]
        status, payload, _ = self.confirm(challenge, token="f" * 32)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "STOP_CONFIRM_REJECTED")
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "TOKEN_MISMATCH")
        self.assertFalse((self.fixture.runtime_a / "control" / "STOP")
                         .exists())
        status, payload, _ = self.confirm(challenge)
        self.assertEqual(status, 200)

    def test_stop_confirm_rejects_replay_after_success(self):
        challenge = self.prepare()[1]["stop_challenge"]
        self.assertEqual(self.confirm(challenge)[0], 200)
        status, payload, _ = self.confirm(challenge)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "CHALLENGE_ALREADY_USED")

    def test_stop_confirm_rejects_cross_runtime_challenges(self):
        challenge = self.prepare()[1]["stop_challenge"]
        status, payload, _ = self.confirm(
            challenge, runtime_id=self.runtime_b["id"])
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "CHALLENGE_UNKNOWN")
        self.assertFalse((self.fixture.runtime_b / "control" / "STOP")
                         .exists())

    def test_stop_confirm_rejects_expired_challenges(self):
        original = wcs.STOP_CHALLENGE_TTL_SECONDS
        wcs.STOP_CHALLENGE_TTL_SECONDS = 0.0
        try:
            challenge = self.prepare()[1]["stop_challenge"]
        finally:
            wcs.STOP_CHALLENGE_TTL_SECONDS = original
        status, payload, _ = self.confirm(challenge)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "CHALLENGE_EXPIRED")

    def test_stop_confirm_rejects_changed_project_state(self):
        challenge = self.prepare()[1]["stop_challenge"]
        state_file = (self.fixture.runtime_a / "projects" / "proj-alpha"
                      / "project_state.json")
        state_file.write_text('{"status": "SUPERVISOR_TURN", "changed": true}',
                              encoding="utf-8")
        status, payload, _ = self.confirm(challenge)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "STATE_CHANGED")

    def test_stop_confirm_rejects_project_mismatch(self):
        challenge = self.prepare()[1]["stop_challenge"]
        status, payload, _ = self.confirm(challenge, project_id="proj-beta")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "PROJECT_MISMATCH")

    def test_stop_prepare_refuses_when_stop_is_already_applied(self):
        (self.fixture.runtime_a / "control" / "STOP").write_text("USER_STOP",
                                                                 encoding="utf-8")
        status, payload, _ = self.prepare()
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "STOP_ALREADY_APPLIED")

    def test_stop_challenge_issuance_is_bounded(self):
        original = wcs.STOP_CHALLENGE_MAX_PENDING
        wcs.STOP_CHALLENGE_MAX_PENDING = 2
        try:
            self.assertEqual(self.prepare()[0], 200)
            self.assertEqual(self.prepare()[0], 200)
            status, payload, _ = self.prepare()
            self.assertEqual(status, 429)
            self.assertEqual(payload["error"]["code"], "STOP_CHALLENGE_LIMIT")
        finally:
            wcs.STOP_CHALLENGE_MAX_PENDING = original

    def test_stop_prepare_fails_closed_on_status_failure(self):
        self.set_mode("garbage")
        status, payload, _ = self.prepare()
        self.assertEqual(status, 502)

    def test_stop_confirm_on_unknown_challenge_fails_closed(self):
        status, payload, _ = self.confirm(
            {"challenge_id": "a" * 32, "confirmation_token": "b" * 32,
             "project_id": "proj-alpha"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["detail"]["reason"],
                         "CHALLENGE_UNKNOWN")

    def test_stop_confirm_rejects_malformed_bodies(self):
        for body in ({}, {"challenge_id": "a" * 32},
                     {"challenge_id": "short", "confirmation_token": "b" * 32,
                      "project_id": "proj-alpha"},
                     {"challenge_id": "a" * 32, "confirmation_token": "b" * 32,
                      "project_id": "proj-alpha", "root": "C:/x"}):
            status, payload, _ = self.fixture.request(
                "POST",
                f"/api/runtimes/{self.runtime_a['id']}/controls/stop/confirm",
                body=body)
            self.assertEqual(status, 400, repr(body))
            self.assertEqual(payload["error"]["code"],
                             "CONTROL_INVALID_PAYLOAD")


class HumanDecisionTests(ControlEndpointTestsBase):
    decision = {
        "decision_content": "Continue with the bounded stage; keep the "
                            "patch minimal.",
        "constraints_verbatim": [
            "No fabricated measurements.",
            "Preserve the v1.2 safety baseline.",
        ],
    }

    def review_status(self):
        return dict(STATUS_A, human_review=True,
                    project_status="HUMAN_REVIEW", active_task=None)

    def test_flagless_runtime_failure_reason_is_available_over_console_http(self):
        import supervisor_control as sc
        raw = "FV dispatch preparation: FV request EXECUTION_MODE 'REVERIFY' conflicts with Runtime policy binding"
        state = {"status": "HUMAN_REVIEW", "supervisor_retry_failure": {
            "decision_attempts": 2, "last_error": raw}}
        status_doc = self.review_status() | {"human_review_reason": sc._human_review_reason(state, {})}
        (self.fixture.runtime_a / "stub_status.json").write_text(json.dumps(status_doc), encoding="utf-8")
        code, payload, _ = self.fixture.request("GET", f"/api/runtimes/{self.runtime_a['id']}/controls")
        self.assertEqual(code, 200)
        reason = payload["controls"]["human_review"]["reason"]
        self.assertTrue(reason["available"])
        self.assertIn("授权前", reason["summary"])
        self.assertEqual(reason["raw_error"], raw)
        self.assertEqual(reason["stage"], "FINAL_VERIFICATION_DISPATCH")

    def activate_review(self, root):
        (root / "stub_status.json").write_text(
            json.dumps(self.review_status(), ensure_ascii=False),
            encoding="utf-8")

    def prepare(self, runtime_id=None, body=None):
        return self.fixture.request(
            "POST",
            f"/api/runtimes/{runtime_id or self.runtime_a['id']}"
            f"/controls/human-review/prepare",
            body=self.decision if body is None else body)

    def apply(self, receipt_id, receipt_sha256, runtime_id=None):
        return self.fixture.request(
            "POST",
            f"/api/runtimes/{runtime_id or self.runtime_a['id']}"
            f"/controls/human-review/apply",
            body={"receipt_id": receipt_id,
                  "receipt_sha256": receipt_sha256})

    def test_prepare_requires_active_human_review(self):
        status, payload, _ = self.prepare()
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "HUMAN_REVIEW_NOT_ACTIVE")
        self.assertEqual(
            [call for call in self.fixture.calls(self.fixture.runtime_a)
             if "helper" in call], [])

    def test_prepare_validates_typed_input_before_invoking_the_helper(self):
        self.activate_review(self.fixture.runtime_a)
        for body in ({}, {"decision_content": "x"},
                     {"decision_content": "   ",
                      "constraints_verbatim": ["c"]},
                     {"decision_content": "x", "constraints_verbatim": []},
                     {"decision_content": "x",
                      "constraints_verbatim": ["c"], "extra": 1},
                     {"decision_content": 5,
                      "constraints_verbatim": ["c"]}):
            status, payload, _ = self.prepare(body=body)
            self.assertEqual(status, 400, repr(body))
            self.assertEqual(payload["error"]["code"],
                             "HUMAN_DECISION_INVALID", repr(body))
        self.assertEqual(
            [call for call in self.fixture.calls(self.fixture.runtime_a)
             if "helper" in call], [])

    def test_prepare_and_apply_round_trip(self):
        self.activate_review(self.fixture.runtime_a)
        status, payload, _ = self.prepare()
        self.assertEqual(status, 200)
        decision = payload["human_decision"]
        self.assertEqual(decision["schema_version"], 1)
        self.assertRegex(decision["receipt_id"],
                         r"^human-decision-[0-9a-f]{32}$")
        self.assertEqual(decision["receipt_sha256"], "a" * 64)
        self.assertEqual(decision["project_id"], "proj-alpha")
        helpers = [call for call in self.fixture.calls(self.fixture.runtime_a)
                   if "helper" in call]
        self.assertEqual(helpers, [{"helper": "prepare"}])
        status, payload, _ = self.apply(decision["receipt_id"],
                                        decision["receipt_sha256"])
        self.assertEqual(status, 200)
        result = payload["human_decision_result"]
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        self.assertEqual(result["status"], "SUPERVISOR_TURN")
        self.assertEqual(result["receipt_id"], decision["receipt_id"])
        helpers = [call for call in self.fixture.calls(self.fixture.runtime_a)
                   if "helper" in call]
        self.assertEqual(helpers, [{"helper": "prepare"},
                                   {"helper": "apply"}])
        # The consumed receipt is gone: a replayed confirmation cannot reapply.
        status, payload, _ = self.apply(decision["receipt_id"],
                                        decision["receipt_sha256"])
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"],
                         "HUMAN_DECISION_RECEIPT_UNKNOWN")

    def test_apply_rejects_a_mismatched_receipt_hash(self):
        self.activate_review(self.fixture.runtime_a)
        _, payload, _ = self.prepare()
        decision = payload["human_decision"]
        status, payload, _ = self.apply(decision["receipt_id"], "f" * 64)
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"],
                         "HUMAN_DECISION_RECEIPT_MISMATCH")
        helpers = [call for call in self.fixture.calls(self.fixture.runtime_a)
                   if "helper" in call]
        self.assertEqual(helpers, [{"helper": "prepare"}])
        status, payload, _ = self.apply(decision["receipt_id"],
                                        decision["receipt_sha256"])
        self.assertEqual(status, 200)

    def test_apply_unknown_receipt_fails_closed(self):
        self.activate_review(self.fixture.runtime_a)
        status, payload, _ = self.apply("human-decision-" + "0" * 32,
                                        "a" * 64)
        self.assertEqual(status, 404)

    def test_receipts_are_runtime_scoped(self):
        self.activate_review(self.fixture.runtime_a)
        _, payload, _ = self.prepare()
        decision = payload["human_decision"]
        status, payload, _ = self.apply(decision["receipt_id"],
                                        decision["receipt_sha256"],
                                        runtime_id=self.runtime_b["id"])
        self.assertEqual(status, 404)
        self.assertEqual(
            [call for call in self.fixture.calls(self.fixture.runtime_b)
             if "helper" in call], [])

    def test_helper_refusal_maps_to_a_structured_conflict(self):
        self.activate_review(self.fixture.runtime_a)
        (self.fixture.runtime_a / "stub_hr_mode.txt").write_text(
            "refuse", encoding="utf-8")
        self.addCleanup(lambda: (self.fixture.runtime_a / "stub_hr_mode.txt")
                        .unlink(missing_ok=True))
        status, payload, _ = self.prepare()
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "HUMAN_DECISION_REFUSED")
        self.assertEqual(payload["error"]["detail"]["exit_code"], 4)

    def test_helper_conflict_maps_to_a_structured_conflict(self):
        self.activate_review(self.fixture.runtime_a)
        (self.fixture.runtime_a / "stub_hr_mode.txt").write_text(
            "conflict", encoding="utf-8")
        self.addCleanup(lambda: (self.fixture.runtime_a / "stub_hr_mode.txt")
                        .unlink(missing_ok=True))
        status, payload, _ = self.prepare()
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "HUMAN_DECISION_REFUSED")

    def test_no_server_side_paths_leak_into_decision_responses(self):
        self.activate_review(self.fixture.runtime_a)
        status, payload, raw = self.prepare()
        blob = raw.decode("utf-8")
        self.assertNotIn(str(self.fixture.data_dir), blob)
        self.assertNotIn("receipt_path", blob)
        self.assertNotIn("decision_file", blob)
        decision = payload["human_decision"]
        status, payload, raw = self.apply(decision["receipt_id"],
                                          decision["receipt_sha256"])
        blob = raw.decode("utf-8")
        self.assertNotIn(str(self.fixture.data_dir), blob)
        self.assertNotIn("receipt_archive", blob)


if __name__ == "__main__":
    unittest.main()
