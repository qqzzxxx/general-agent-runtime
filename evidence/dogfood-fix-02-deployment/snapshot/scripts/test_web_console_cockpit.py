"""P3 Web Console Cockpit endpoint tests (offline, localhost only).

Covers the read-only, Registry-ID-scoped Cockpit route on top of the accepted
P2 opaque-ID root binding: versioned bounded cockpit documents, deterministic
state interpretation over the wire, the explicit Runtime selector data,
switching between two unmistakably different fixture Runtimes (Current
Execution and Health update together, no cross-root attribution), fail-closed
behavior for unknown/removed/traversal-shaped/query-rebinding IDs and for
control-plane errors/malformed status, protocol-milestone honesty (no
percent/token fabrication), Unicode safety, and P1/P2 surface preservation.
No GUI, no external network, no mutation of any managed Runtime tree.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_server as wcs

ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

MARKER_A = "COCKPIT-ROOT-A-café"
MARKER_B = "COCKPIT-ROOT-B-中文"

STATUS_A = {
    "schema_version": 1,
    "PROJECT_ID": MARKER_A,
    "runtime_status": "RUNNING",
    "project_status": "WAITING_EXECUTOR",
    "pause": {"status": "RUNNING", "requested_at": None, "mode": "SAFE",
              "resumed_at": None},
    "active_task": {"MESSAGE_ID": 700401, "TASK_ID": "TASK-A-α",
                    "STAGE_ID": "stage-a", "ATTEMPT": 1, "NONCE": "a" * 24,
                    "IS_FINAL_VERIFICATION": False},
    "last_authorized_dispatch": {"MESSAGE_ID": 700401, "TASK_ID": "TASK-A-α",
                                 "STAGE_ID": "stage-a", "ATTEMPT": 1,
                                 "NONCE": "a" * 24},
    "active_task_claimed": True,
    "active_task_claim_recorded": True,
    "active_task_completion_status": None,
    "active_task_retired": False,
    "pending_interventions": 0,
    "last_consumed_message_id": 700391,
    "human_review": False,
    "stop": False,
}

STATUS_B = {
    "schema_version": 1,
    "PROJECT_ID": MARKER_B,
    "runtime_status": None,
    "project_status": "WAITING_EXECUTOR",
    "pause": {"status": "RUNNING", "requested_at": None, "mode": "SAFE",
              "resumed_at": None},
    "active_task": {"MESSAGE_ID": 700402, "TASK_ID": "TASK-B-中",
                    "STAGE_ID": "stage-b", "ATTEMPT": 2, "NONCE": "b" * 24,
                    "IS_FINAL_VERIFICATION": False},
    "last_authorized_dispatch": {"MESSAGE_ID": 700402, "TASK_ID": "TASK-B-中",
                                 "STAGE_ID": "stage-b", "ATTEMPT": 2,
                                 "NONCE": "b" * 24},
    "active_task_claimed": False,
    "active_task_claim_recorded": False,
    "active_task_completion_status": None,
    "active_task_retired": False,
    "pending_interventions": 0,
    "last_consumed_message_id": None,
    "human_review": False,
    "stop": False,
}

STATUS_HRV = {
    "schema_version": 1,
    "PROJECT_ID": "proj-hrv",
    "runtime_status": "HUMAN_REVIEW",
    "project_status": "HUMAN_REVIEW",
    "pause": {"status": "PAUSED", "requested_at": "2026-09-12T01:00:00+00:00",
              "paused_at": "2026-09-12T01:02:00+00:00", "mode": "SAFE",
              "resumed_at": None},
    "active_task": None,
    "last_authorized_dispatch": None,
    "active_task_claimed": False,
    "active_task_claim_recorded": False,
    "active_task_completion_status": None,
    "active_task_retired": False,
    "pending_interventions": 1,
    "last_consumed_message_id": 700400,
    "human_review": True,
    "stop": False,
}

STATUS_BAD_VALUE = dict(STATUS_A, PROJECT_ID="proj-weird",
                        runtime_status="WARP_DRIVE")
STATUS_BAD_TYPE = dict(STATUS_A, PROJECT_ID="proj-typed",
                       pending_interventions="many")

STUB_CONTROL = """\
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
mode = "ok"
mode_file = root / "stub_mode.txt"
if mode_file.exists():
    mode = mode_file.read_text(encoding="utf-8").strip()
status_file = root / "stub_status.json"


def emit(value):
    # Mirror the v1.2 machine mode: one ASCII-escaped JSON document.
    print(json.dumps(value, ensure_ascii=True, indent=2))


if mode == "ok":
    if status_file.exists():
        emit(json.loads(status_file.read_text(encoding="utf-8")))
    else:
        emit({"schema_version": 1, "PROJECT_ID": None,
              "runtime_status": None, "project_status": None,
              "pause": {"status": "RUNNING"}, "active_task": None,
              "last_authorized_dispatch": None, "active_task_claimed": False,
              "active_task_claim_recorded": False,
              "active_task_completion_status": None,
              "active_task_retired": False, "pending_interventions": 0,
              "last_consumed_message_id": None, "human_review": False,
              "stop": False})
    sys.exit(0)
if mode == "exit2_json":
    emit({"ok": False, "error": "boom: café", "error_type": "ControlError"})
    sys.exit(2)
if mode == "garbage_ok":
    sys.stdout.write("this is definitely not json\\n")
    sys.exit(0)
if mode == "schema99":
    emit({"schema_version": 99, "PROJECT_ID": "x"})
    sys.exit(99)
if mode == "sleep":
    import time
    time.sleep(30)
    sys.exit(0)
if mode == "crash":
    sys.exit(1)
sys.exit(3)
"""


def write_stub_runtime(runtime_root: Path) -> None:
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    (runtime_root / "scripts" / "supervisor_control.py").write_text(
        STUB_CONTROL, encoding="utf-8")


class CockpitFixture:
    """One console server + two unmistakably different fixture Runtimes."""

    def __init__(self, base: Path):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.runtime_a = base / "runtime-a"
        self.runtime_b = base / "runtime-b"
        write_stub_runtime(self.runtime_a)
        write_stub_runtime(self.runtime_b)
        (self.runtime_a / "stub_status.json").write_text(
            json.dumps(STATUS_A, ensure_ascii=False), encoding="utf-8")
        (self.runtime_b / "stub_status.json").write_text(
            json.dumps(STATUS_B, ensure_ascii=False), encoding="utf-8")
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_a,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=8.0)
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "/", body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {}
            payload = None
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

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class CockpitEndpointTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wc-cockpit-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.fixture = CockpitFixture(self.base)
        self.addCleanup(self.fixture.close)
        self.entry_a = self.fixture.add_runtime(self.fixture.runtime_a,
                                                "Alpha Café 科研")
        self.entry_b = self.fixture.add_runtime(self.fixture.runtime_b,
                                                "Beta 中文")

    def cockpit_of(self, runtime_id: str):
        return self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/cockpit")

    def test_cockpit_document_is_versioned_and_bounded(self):
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["cockpit"]["schema_version"], 1)
        self.assertEqual(payload["runtime"]["id"], self.entry_a["id"])
        self.assertEqual(payload["runtime"]["label"], "Alpha Café 科研")
        self.assertEqual(payload["cockpit"]["source"]["kind"],
                         "supervisor_control_status")
        self.assertEqual(payload["cockpit"]["source"]["exit_code"], 0)
        self.assertEqual(
            payload["cockpit"]["source"]["status_schema_version"], 1)
        self.assertTrue(payload["cockpit"]["backend"]["status"] == "ok")

    def test_cockpit_interprets_zcode_executing_for_runtime_a(self):
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        interp = payload["cockpit"]["interpretation"]
        self.assertEqual(interp["state"]["family"], "ZCODE_EXECUTING")
        self.assertEqual(interp["next_expected"]["message_id"], 700401)
        self.assertEqual(interp["protocol"]["message_id"], 700401)
        self.assertIn(MARKER_A, interp["health"]["project"]["id"])

    def test_cockpit_interprets_claim_wait_for_runtime_b(self):
        status, payload, _ = self.cockpit_of(self.entry_b["id"])
        interp = payload["cockpit"]["interpretation"]
        self.assertEqual(interp["state"]["family"],
                         "WAITING_FOR_ZCODE_CLAIM")
        self.assertIn("700402", interp["next_expected"]["label"])
        self.assertIn("700402", json.dumps(payload, ensure_ascii=True))

    def test_switching_runtimes_updates_current_execution_and_health(self):
        _, payload_a, _ = self.cockpit_of(self.entry_a["id"])
        _, payload_b, _ = self.cockpit_of(self.entry_b["id"])
        interp_a = payload_a["cockpit"]["interpretation"]
        interp_b = payload_b["cockpit"]["interpretation"]
        self.assertNotEqual(interp_a["state"]["family"],
                            interp_b["state"]["family"])
        self.assertNotEqual(interp_a["protocol"]["message_id"],
                            interp_b["protocol"]["message_id"])
        # ensure_ascii=False: leak checks must not be defeated by \u escapes.
        self.assertIn(MARKER_A, json.dumps(interp_a, ensure_ascii=False))
        self.assertIn(MARKER_B, json.dumps(interp_b, ensure_ascii=False))
        self.assertNotIn(MARKER_A, json.dumps(interp_b, ensure_ascii=False))
        self.assertNotIn(MARKER_B, json.dumps(interp_a, ensure_ascii=False))
        self.assertEqual(payload_a["runtime"]["root"],
                         str(self.fixture.runtime_a))
        self.assertEqual(payload_b["runtime"]["root"],
                         str(self.fixture.runtime_b))

    def test_human_review_cockpit_requires_user_action(self):
        (self.fixture.runtime_a / "stub_status.json").write_text(
            json.dumps(STATUS_HRV, ensure_ascii=False), encoding="utf-8")
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        interp = payload["cockpit"]["interpretation"]
        self.assertEqual(interp["state"]["family"], "HUMAN_REVIEW")
        self.assertTrue(interp["state"]["user_action_required"])
        self.assertIn("700400", interp["health"]["zcode"]
                      ["last_observed_activity"])

    def test_malformed_status_values_fail_closed_over_http(self):
        (self.fixture.runtime_a / "stub_status.json").write_text(
            json.dumps(STATUS_BAD_VALUE, ensure_ascii=False),
            encoding="utf-8")
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        self.assertEqual(status, 200)  # control plane answered coherently
        interp = payload["cockpit"]["interpretation"]
        self.assertEqual(interp["state"]["family"], "STATE_UNAVAILABLE")
        self.assertFalse(interp["next_expected"]["available"])
        self.assertNotIn(MARKER_B, json.dumps(payload, ensure_ascii=True))

    def test_malformed_status_types_fail_closed_over_http(self):
        (self.fixture.runtime_b / "stub_status.json").write_text(
            json.dumps(STATUS_BAD_TYPE, ensure_ascii=False),
            encoding="utf-8")
        status, payload, _ = self.cockpit_of(self.entry_b["id"])
        interp = payload["cockpit"]["interpretation"]
        self.assertEqual(interp["state"]["family"], "STATE_UNAVAILABLE")
        self.assertTrue(interp["honesty"]["malformed_fields"])

    def test_no_percent_or_token_fabrication_in_cockpit_payload(self):
        for entry in (self.entry_a, self.entry_b):
            _, payload, _ = self.cockpit_of(entry["id"])
            blob = json.dumps(payload, ensure_ascii=True).lower()
            self.assertNotIn("percent", blob)
            self.assertNotIn("% complete", blob)
            self.assertNotIn("output_tokens", blob)

    def test_control_plane_error_fails_closed_without_cockpit(self):
        (self.fixture.runtime_a / "stub_mode.txt").write_text(
            "exit2_json", encoding="utf-8")
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")
        self.assertNotIn("cockpit", payload)

    def test_malformed_control_plane_output_fails_closed(self):
        (self.fixture.runtime_a / "stub_mode.txt").write_text(
            "garbage_ok", encoding="utf-8")
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"],
                         "CONTROL_PLANE_MALFORMED_OUTPUT")

    def test_unsupported_status_schema_fails_closed(self):
        (self.fixture.runtime_a / "stub_mode.txt").write_text(
            "schema99", encoding="utf-8")
        status, payload, _ = self.cockpit_of(self.entry_a["id"])
        self.assertIn(status, (400, 502))
        self.assertFalse(payload["ok"])

    def test_unknown_removed_traversal_and_rebinding_ids_fail_closed(self):
        adversarial = [
            "0123456789abcdef",                       # well-formed, unknown
            self.entry_a["id"] + "0",                 # near-miss
            "..%2F..%2Fruntime-b",
            "C:%5CWindows",
            urllib.parse.quote("你的runtime"),
            "",
        ]
        for bad in adversarial:
            url = f"/api/runtimes/{bad}/cockpit"
            status, payload, _ = self.fixture.request("GET", url)
            self.assertEqual(status, 404, url)
            self.assertFalse(payload["ok"])
            self.assertIn(payload["error"]["code"],
                          ("ROUTE_NOT_FOUND", "RUNTIME_UNKNOWN"), url)
        # Removed IDs stop serving a cockpit immediately.
        _, removed, _ = self.fixture.request(
            "DELETE", f"/api/runtimes/{self.entry_b['id']}")
        self.assertTrue(removed["ok"])
        status, payload, _ = self.cockpit_of(self.entry_b["id"])
        self.assertEqual(status, 404)
        # Query parameters cannot rebind the selected Runtime.
        url = (f"/api/runtimes/{self.entry_a['id']}/cockpit"
               f"?root={urllib.parse.quote(str(self.fixture.runtime_b))}")
        status, payload, _ = self.fixture.request("GET", url)
        self.assertEqual(status, 200)
        self.assertEqual(payload["runtime"]["id"], self.entry_a["id"])
        self.assertIn(MARKER_A, json.dumps(payload, ensure_ascii=False))
        self.assertNotIn(MARKER_B, json.dumps(payload, ensure_ascii=False))

    def test_cockpit_route_is_get_only(self):
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            status, payload, _ = self.fixture.request(
                method, f"/api/runtimes/{self.entry_a['id']}/cockpit",
                body={"x": 1} if method in ("POST", "PUT", "PATCH") else None)
            self.assertEqual(status, 405, method)
            self.assertEqual(payload["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_unicode_labels_and_payloads_round_trip_as_utf8(self):
        status, _, raw = self.cockpit_of(self.entry_a["id"])
        self.assertEqual(status, 200)
        payload = json.loads(raw.decode("utf-8"))  # strict UTF-8
        self.assertEqual(payload["runtime"]["label"], "Alpha Café 科研")
        self.assertIn("科研".encode("utf-8"), raw)

    def test_runtimes_listing_backs_the_selector(self):
        status, payload, _ = self.fixture.request("GET", "/api/runtimes")
        self.assertEqual(status, 200)
        ids = {entry["id"] for entry in payload["runtimes"]}
        self.assertEqual(ids, {self.entry_a["id"], self.entry_b["id"]})
        for entry in payload["runtimes"]:
            self.assertRegex(entry["id"], ID_PATTERN)
            self.assertIn("label", entry)

    def test_p1_p2_routes_remain_intact_alongside_cockpit(self):
        status, payload, _ = self.fixture.request("GET", "/api/health")
        self.assertEqual(status, 200)
        status, payload, _ = self.fixture.request("GET", "/api/status")
        self.assertEqual(status, 200)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.entry_a['id']}/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control_plane"]["status"]["PROJECT_ID"],
                         MARKER_A)
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.entry_a['id']}/rename",
            body={"label": "renamed-α"})
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
