"""P9 HTTP tests: settings, operator notes, alerts, and Runtime creation.

Exercises the new bounded localhost routes over a real in-process console
server: the versioned settings store (global defaults and per-Runtime
overrides with precedence), the UI-only operator note, the deterministic
alert projection (including probe failure and usage outliers over real P8
turn records), and Registry-driven creation from the supported-release
template — including rollback, concurrency, source immutability, and the
copied/excluded skeleton contract.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_server as wcs
import web_console_runtime_create as wcr


PARAM_STUB = """\
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
mode_file = root / "stub_mode.txt"
mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.exists() else "ok"
if mode == "exit2":
    print(json.dumps({"ok": False, "error": "stub failure",
                      "error_type": "ControlError"}))
    sys.exit(2)
status_file = root / "stub_status.json"
if status_file.exists():
    document = json.loads(status_file.read_text(encoding="utf-8"))
    print(json.dumps(document, ensure_ascii=True))
    sys.exit(0)
print(json.dumps({"schema_version": 1, "PROJECT_ID": None,
                  "runtime_status": None, "project_status": None,
                  "pause": {"status": "RUNNING", "requested_at": None,
                            "mode": None, "resumed_at": None},
                  "active_task": None, "last_authorized_dispatch": None,
                  "active_task_claimed": False,
                  "active_task_claim_recorded": False,
                  "active_task_completion_status": None,
                  "active_task_retired": False,
                  "pending_interventions": 0,
                  "last_consumed_message_id": None,
                  "human_review": False, "stop": False},
                 ensure_ascii=True))
"""

CREATED_ROOT_STUB = """\
import json, sys
print(json.dumps({"schema_version": 1, "PROJECT_ID": None,
                  "runtime_status": None, "project_status": None,
                  "pause": {"status": "RUNNING", "requested_at": None,
                            "mode": None, "resumed_at": None},
                  "active_task": None, "last_authorized_dispatch": None,
                  "active_task_claimed": False,
                  "active_task_claim_recorded": False,
                  "active_task_completion_status": None,
                  "active_task_retired": False,
                  "pending_interventions": 0,
                  "last_consumed_message_id": None,
                  "human_review": False, "stop": False},
                 ensure_ascii=True))
"""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root: Path) -> dict:
    manifest = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for name in files:
            path = Path(base) / name
            manifest[path.relative_to(root).as_posix()] = sha256_file(path)
    return manifest


def pickup_status(message_id=700501, minutes_ago=45, expires_minutes=600):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    dispatch = {
        "schema_version": 1, "MESSAGE_ID": message_id, "TASK_ID": "T",
        "STAGE_ID": "S", "ATTEMPT": 1, "NONCE": "n",
        "AUTHORIZED_AT": (now - timedelta(minutes=minutes_ago))
        .isoformat(timespec="seconds"),
        "EXPIRES_AT": (now + timedelta(minutes=expires_minutes))
        .isoformat(timespec="seconds"),
    }
    return {
        "schema_version": 1, "PROJECT_ID": "demo",
        "runtime_status": "RUNNING", "project_status": "WAITING_EXECUTOR",
        "pause": {"status": "RUNNING", "requested_at": None, "mode": None,
                  "resumed_at": None},
        "active_task": dict(dispatch),
        "last_authorized_dispatch": dict(dispatch),
        "active_task_claimed": False, "active_task_claim_recorded": False,
        "active_task_completion_status": None, "active_task_retired": False,
        "pending_interventions": 0, "last_consumed_message_id": None,
        "human_review": False, "stop": False,
        "supervisor_turn_inflight": None, "active_task_completion": None,
    }


def clean_status():
    return pickup_status(minutes_ago=0, expires_minutes=100000)


def turn_record(turn_id: str, total_tokens, reported=True, index=0) -> dict:
    minute = 10 + index
    return {
        "schema": "SUPERVISOR-TURN-OBSERVABILITY-V1", "schema_version": 1,
        "turn_id": turn_id, "PROJECT_ID": "demo",
        "invocation": {"reason": "EXECUTOR_RESULT_READY", "event": None},
        "started_at": f"2026-09-12T10:{minute:02d}:00+00:00",
        "finished_at": f"2026-09-12T10:{minute:02d}:30+00:00",
        "duration_seconds": 30.0,
        "supervisor_config": {"source": "fixed_policy",
                              "config_revision": None, "model": "m",
                              "reasoning_effort": "high", "queued_at": None},
        "usage": {"reported": reported,
                  "input_tokens": total_tokens,
                  "output_tokens": 0, "total_tokens": total_tokens,
                  "source": "test" if reported else None,
                  "note": None if reported else "not reported"},
        "context_manifest": None,
        "decision": {"committed": True,
                     "receipt_file": "control/supervisor_decisions/x.json",
                     "receipt_sha256": None, "decision": {},
                     "decision_sha256": None, "decision_summary": "did it",
                     "decision_history_index": 0,
                     "resulting_status": "WAITING_EXECUTOR"},
        "dispatch_linkage": {"MESSAGE_ID": 700501, "TASK_ID": "T",
                             "STAGE_ID": "S", "ATTEMPT": 1, "NONCE": "n",
                             "dispatch_sha256": "a" * 64},
        "intervention_ids": [],
        "outcome": {"committed": True, "stale": False,
                    "recovered_after_crash": False,
                    "candidate_validation_failed": False, "error": None},
    }


class P9Fixture:
    """One console server (with a minimal release-skeleton console root),
    two registered Runtime fixtures with parameterized status stubs, and
    an optional work area for created Runtimes."""

    def __init__(self, *, console_complete: bool = True):
        self.tmp = tempfile.TemporaryDirectory(prefix="wc-p9-")
        base = Path(self.tmp.name)
        self.base = base
        self.console_root = base / "console"
        self._build_console_skeleton(complete=console_complete)
        self.data_dir = base / "console-data"
        self.alpha = self._runtime(base / "alpha")
        self.beta = self._runtime(base / "beta")
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.alpha,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=8.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()
        self.alpha_id = self._register(self.alpha, "Alpha 运行时")
        self.beta_id = self._register(self.beta, "Beta")

    def _build_console_skeleton(self, *, complete: bool) -> None:
        console = self.console_root
        (console / "web_console").mkdir(parents=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    console / "web_console" / "index.html")
        for name in ("AI_BOOTSTRAP.md", "LICENSE", "README.md",
                     "V1.3_PRODUCT_SPEC.md", "orchestrator.py"):
            (console / name).write_text(f"content of {name}\n",
                                        encoding="utf-8")
        for name in wcr.RELEASE_FILES:
            if not (console / name).exists():
                (console / name).write_text(f"content of {name}\n",
                                            encoding="utf-8")
        for name in wcr.RELEASE_DIRECTORIES:
            (console / name).mkdir(exist_ok=True)
        (console / "control").mkdir(exist_ok=True)
        for name in wcr.CONTROL_SEED_FILES:
            (console / "control" / name).write_text(f"seed {name}\n",
                                                    encoding="utf-8")
        (console / "handoff").mkdir(exist_ok=True)
        (console / "handoff" / "PROTOCOL.md").write_text("protocol\n",
                                                         encoding="utf-8")
        (console / "profiles" / "GENERAL").mkdir(parents=True, exist_ok=True)
        (console / "profiles" / "GENERAL" / "profile.json").write_text(
            "{}\n", encoding="utf-8")
        (console / "docs" / "a.md").write_text("docs\n", encoding="utf-8")
        scripts = console / "scripts"
        for name in ("supervisor_control.py", "executor_claim.py",
                     "executor_fence.py", "executor_completion.py",
                     "preflight.py", "start_project.py", "other.py"):
            if name == "supervisor_control.py" and complete:
                (scripts / name).write_text(CREATED_ROOT_STUB,
                                            encoding="utf-8")
            else:
                (scripts / name).write_text(f"# {name}\n", encoding="utf-8")
        if not complete:
            (scripts / "executor_fence.py").unlink()

    def _runtime(self, path: Path) -> Path:
        (path / "scripts").mkdir(parents=True)
        (path / "scripts" / "supervisor_control.py").write_text(
            PARAM_STUB, encoding="utf-8")
        return path

    def _register(self, root: Path, label: str) -> str:
        import urllib.request
        import urllib.error
        body = json.dumps({"root": str(root), "label": label}) \
            .encode("utf-8")
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/runtimes", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=30) as response:
            document = json.loads(response.read().decode("utf-8"))
        return document["runtime"]["id"]

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "",
                payload=None, timeout: float = 60.0):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=timeout)
        try:
            headers = {}
            body = None
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False) \
                    .encode("utf-8")
                headers = {"Content-Type": "application/json"}
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), raw
        finally:
            conn.close()

    def json(self, method: str = "GET", path: str = "", payload=None):
        status, headers, raw = self.request(method, path, payload)
        return status, headers, json.loads(raw.decode("utf-8"))

    def set_status(self, root: Path, document) -> None:
        path = root / "stub_status.json"
        if document is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(json.dumps(document), encoding="utf-8")

    def set_mode(self, root: Path, mode: str) -> None:
        (root / "stub_mode.txt").write_text(mode, encoding="utf-8")

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)

    def restart(self) -> None:
        self.stop()
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.alpha,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=8.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    def close(self) -> None:
        self.stop()
        self.tmp.cleanup()


class P9HttpTestCase(unittest.TestCase):
    complete = True

    def setUp(self):
        self.fixture = P9Fixture(console_complete=self.complete)
        self.addCleanup(self.fixture.close)

    def write_turn_records(self, root: Path, records: list[dict]) -> None:
        directory = root / "control" / "supervisor_turns"
        directory.mkdir(parents=True, exist_ok=True)
        for record in records:
            path = directory / f"{record['turn_id']}.json"
            path.write_text(json.dumps(record), encoding="utf-8")


class SettingsHttpTests(P9HttpTestCase):
    def test_global_settings_default_document(self):
        status, _, document = self.fixture.json(path="/api/settings")
        self.assertEqual(status, 200)
        self.assertTrue(document["ok"])
        settings = document["global_settings"]["settings"]
        self.assertEqual(settings["timeline_page_size"], 20)
        self.assertEqual(settings["decision_summary_mode"], "compact")
        self.assertIsNone(document["global_settings"]["updated_at"])

    def test_global_update_round_trip_and_note(self):
        status, _, document = self.fixture.json(
            "POST", "/api/settings",
            {"settings": {"timeline_page_size": 33}})
        self.assertEqual(status, 200)
        self.assertEqual(
            document["global_settings"]["settings"]["timeline_page_size"],
            33)
        self.assertTrue(any("formal" in note.lower()
                            for note in document["honesty"]))
        status, _, document = self.fixture.json(path="/api/settings")
        self.assertEqual(
            document["global_settings"]["settings"]["timeline_page_size"],
            33)

    def test_global_unknown_field_and_range_refusals_leave_store_intact(self):
        before = (self.fixture.data_dir / "settings.json")
        baseline = before.read_bytes() if before.exists() else None
        for payload in ({"settings": {"nope": 1}},
                        {"settings": {"timeline_page_size": 100000}},
                        {"settings": {"supervisor_model": "a\x01b"}},
                        {"settings": "not-an-object"},
                        {"unexpected": True}):
            status, _, document = self.fixture.json("POST", "/api/settings",
                                                    payload)
            self.assertEqual(status, 400, payload)
            self.assertFalse(document["ok"], payload)
        after = before.read_bytes() if before.exists() else None
        self.assertEqual(after, baseline)

    def test_oversized_global_body_is_refused(self):
        payload = {"settings": {"supervisor_model": "m" * 70000}}
        status, _, document = self.fixture.json("POST", "/api/settings",
                                                payload)
        self.assertEqual(status, 413)

    def test_corrupt_global_store_fails_closed_without_reset(self):
        path = self.fixture.data_dir / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{corrupt")
        raw = path.read_bytes()
        status, _, document = self.fixture.json(path="/api/settings")
        self.assertEqual(status, 500)
        self.assertEqual(document["error"]["code"], "SETTINGS_CORRUPT")
        status, _, document = self.fixture.json(
            "POST", "/api/settings", {"settings": {"timeline_page_size": 5}})
        self.assertEqual(status, 500)
        self.assertEqual(path.read_bytes(), raw)

    def test_runtime_effective_settings_show_sources(self):
        self.fixture.json("POST", "/api/settings",
                          {"settings": {"timeline_page_size": 33}})
        status, _, document = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/settings")
        self.assertEqual(status, 200)
        view = document["runtime_settings"]
        self.assertEqual(view["effective"]["timeline_page_size"],
                         {"value": 33, "source": "global-default"})
        self.assertIsNone(view["operator_note"])
        status, _, document = self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            {"overrides": {"timeline_page_size": 50}})
        self.assertEqual(status, 200)
        self.assertEqual(document["runtime_settings"]["effective"]
                         ["timeline_page_size"],
                         {"value": 50, "source": "override"})

    def test_override_isolation_between_runtimes(self):
        self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            {"overrides": {"decision_summary_mode": "full"}})
        _, _, alpha = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/settings")
        _, _, beta = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.beta_id}/settings")
        self.assertEqual(alpha["runtime_settings"]["effective"]
                         ["decision_summary_mode"]["source"], "override")
        self.assertEqual(beta["runtime_settings"]["effective"]
                         ["decision_summary_mode"]["source"],
                         "global-default")

    def test_null_override_clears_back_to_inherited(self):
        self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            {"overrides": {"timeline_page_size": 50}})
        status, _, document = self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            {"overrides": {"timeline_page_size": None}})
        self.assertEqual(status, 200)
        self.assertEqual(document["runtime_settings"]["effective"]
                         ["timeline_page_size"]["source"],
                         "global-default")

    def test_override_validation_refusals(self):
        for payload in ({"overrides": {"nope": 1}},
                        {"overrides": {"timeline_page_size": 2}},
                        {"overrides": {"notifications":
                                       {"human_review": "yes"}}},
                        {"unexpected": True},
                        {"overrides": [1, 2]}):
            status, _, document = self.fixture.json(
                "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
                payload)
            self.assertEqual(status, 400, payload)

    def test_operator_note_set_read_and_clear(self):
        base = f"/api/runtimes/{self.fixture.alpha_id}"
        status, _, document = self.fixture.json(
            "POST", f"{base}/notes", {"text": "watch the 运行时 deadline ✅"})
        self.assertEqual(status, 200)
        self.assertEqual(document["operator_note"]["text"],
                         "watch the 运行时 deadline ✅")
        status, _, document = self.fixture.json(path=f"{base}/notes")
        self.assertEqual(document["operator_note"]["text"],
                         "watch the 运行时 deadline ✅")
        _, _, settings = self.fixture.json(path=f"{base}/settings")
        self.assertEqual(settings["runtime_settings"]["operator_note"]
                         ["text"], "watch the 运行时 deadline ✅")
        status, _, document = self.fixture.json("POST", f"{base}/notes",
                                                {"text": ""})
        self.assertEqual(status, 200)
        self.assertIsNone(document["operator_note"])

    def test_operator_note_bounds_and_hostile_text(self):
        base = f"/api/runtimes/{self.fixture.alpha_id}"
        status, _, document = self.fixture.json(
            "POST", f"{base}/notes", {"text": "<script>alert(1)</script>"})
        self.assertEqual(status, 200)  # stored verbatim; rendering is inert
        status, _, document = self.fixture.json(
            "POST", f"{base}/notes", {"text": "x" * 4001})
        self.assertEqual(status, 400)
        self.assertEqual(document["error"]["code"], "SETTINGS_NOTE_INVALID")
        status, _, document = self.fixture.json("POST", f"{base}/notes",
                                                {"text": None})
        self.assertEqual(status, 400)

    def test_settings_survive_a_server_restart(self):
        self.fixture.json("POST", "/api/settings",
                          {"settings": {"timeline_page_size": 44}})
        self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            {"overrides": {"supervisor_model": "m-9"}})
        self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/notes",
            {"text": "persist me"})
        self.fixture.restart()
        _, _, document = self.fixture.json(path="/api/settings")
        self.assertEqual(
            document["global_settings"]["settings"]["timeline_page_size"],
            44)
        _, _, document = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/settings")
        self.assertEqual(document["runtime_settings"]["effective"]
                         ["supervisor_model"],
                         {"value": "m-9", "source": "override"})
        self.assertEqual(document["runtime_settings"]["operator_note"]
                         ["text"], "persist me")

    def test_settings_method_allowlists(self):
        for method in ("DELETE", "PUT", "PATCH"):
            status, _, _ = self.fixture.request(method, "/api/settings")
            self.assertEqual(status, 405, method)
        status, _, _ = self.fixture.request("GET",
                                            f"/api/runtimes/"
                                            f"{self.fixture.alpha_id}/alerts")
        self.assertEqual(status, 200)
        status, _, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            payload={"overrides": {}})
        self.assertEqual(status, 200)


class AlertsHttpTests(P9HttpTestCase):
    def test_pickup_alert_fires_with_stable_identity_and_attribution(self):
        self.fixture.set_status(self.fixture.alpha, pickup_status())
        status, _, document = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")
        self.assertEqual(status, 200)
        alerts = document["alerts_document"]["alerts"]
        rules = {alert["rule"] for alert in alerts}
        self.assertIn("zcode-pickup", rules)
        pickup = [a for a in alerts if a["rule"] == "zcode-pickup"][0]
        self.assertEqual(pickup["severity"], "warning")
        self.assertEqual(pickup["notification_kind"], "ZCODE_PICKUP")
        self.assertEqual(document["runtime"]["id"], self.fixture.alpha_id)
        _, _, again = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")

        def identities(document):
            return [(a["id"], a["rule"], a["severity"])
                    for a in document["alerts_document"]["alerts"]]

        self.assertEqual(identities(document), identities(again))

    def test_clean_runtime_has_no_alerts_and_no_cross_attribution(self):
        self.fixture.set_status(self.fixture.alpha, pickup_status())
        _, _, beta = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.beta_id}/alerts")
        self.assertEqual(beta["alerts_document"]["alerts"], [])
        self.assertEqual(beta["runtime"]["id"], self.fixture.beta_id)

    def test_probe_failure_becomes_an_offline_alert(self):
        self.fixture.set_mode(self.fixture.alpha, "exit2")
        status, _, document = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")
        self.assertEqual(status, 200)
        alerts = document["alerts_document"]["alerts"]
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["rule"], "runtime-offline")
        self.assertEqual(alerts[0]["severity"], "needs-attention")
        self.assertEqual(alerts[0]["evidence"]["probe_error_code"],
                         "CONTROL_PLANE_ERROR")
        self.assertTrue(any("suppressed" in note.lower()
                            for note in document["alerts_document"]
                            ["honesty"]))

    def test_threshold_override_changes_the_projection(self):
        self.fixture.set_status(self.fixture.alpha, pickup_status())
        _, _, before = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")
        self.assertIn("zcode-pickup",
                      {a["rule"] for a in before["alerts_document"]["alerts"]})
        self.fixture.json(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/settings",
            {"overrides": {"alert_thresholds":
                           {"zcode_pickup_minutes": 10080}}})
        _, _, after = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")
        self.assertNotIn(
            "zcode-pickup",
            {a["rule"] for a in after["alerts_document"]["alerts"]})

    def test_usage_outlier_over_real_turn_records(self):
        # Distinct timestamps pin the newest-first order: the LAST record
        # (1000) is the newest turn, the three 100-total turns are its
        # comparable baseline.
        totals = [100, 100, 100, 1000]
        records = [turn_record(f"supervisor-turn-{i:024d}", total,
                               index=i)
                   for i, total in enumerate(totals)]
        self.write_turn_records(self.fixture.alpha, records)
        status, _, document = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")
        alerts = document["alerts_document"]["alerts"]
        outlier = [a for a in alerts if a["rule"] == "usage-outlier"]
        self.assertEqual(len(outlier), 1)
        self.assertEqual(outlier[0]["severity"], "informational")
        self.assertEqual(outlier[0]["notification_kind"], "HIGH_TOKEN_TURN")
        self.assertEqual(outlier[0]["evidence"]["total_tokens"], 1000)
        # Beta shares nothing.
        _, _, beta = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.beta_id}/alerts")
        self.assertNotIn("usage-outlier",
                         {a["rule"] for a in beta["alerts_document"]["alerts"]})

    def test_insufficient_usage_sample_is_honest(self):
        records = [turn_record(f"supervisor-turn-{i:024d}", total,
                               index=i)
                   for i, total in enumerate([100, 1000])]
        self.write_turn_records(self.fixture.alpha, records)
        _, _, document = self.fixture.json(
            path=f"/api/runtimes/{self.fixture.alpha_id}/alerts")
        doc = document["alerts_document"]
        self.assertNotIn("usage-outlier", {a["rule"] for a in doc["alerts"]})
        self.assertTrue(any("not comparable" in note.lower()
                            for note in doc["honesty"]))

    def test_alerts_reject_post_and_unknown_runtime(self):
        status, _, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.fixture.alpha_id}/alerts",
            payload={})
        self.assertEqual(status, 405)
        status, _, document = self.fixture.json(
            path="/api/runtimes/0000000000000000/alerts")
        self.assertEqual(status, 404)
        status, _, document = self.fixture.json(
            path="/api/runtimes/..%2Fescape/alerts")
        self.assertIn(status, (400, 404))


class RuntimeCreateHttpTests(P9HttpTestCase):
    def setUp(self):
        super().setUp()
        self.work = self.fixture.base / "created"
        self.work.mkdir()
        self.manifest_before = tree_manifest(self.fixture.console_root)

    def create(self, destination, label="Created Runtime", template=None):
        payload = {"template_id": template
                   if template is not None else wcr.TEMPLATE_ID,
                   "destination": str(destination), "label": label}
        return self.fixture.json("POST", "/api/runtimes/create", payload)

    def test_template_catalog_is_bounded_and_available(self):
        status, _, document = self.fixture.json(
            path="/api/runtime-templates")
        self.assertEqual(status, 200)
        templates = document["templates"]
        self.assertEqual(len(templates), 1)
        self.assertEqual(templates[0]["template_id"], wcr.TEMPLATE_ID)
        self.assertTrue(templates[0]["available"])
        self.assertEqual(templates[0]["missing_paths"], [])

    def test_create_registers_a_validated_runtime(self):
        destination = self.work / "new-runtime"
        status, _, document = self.create(destination)
        self.assertEqual(status, 201, document)
        entry = document["runtime"]
        self.assertRegex(entry["id"], r"^[0-9a-f]{16}$")
        self.assertEqual(entry["label"], "Created Runtime")
        created = destination
        copied = {p.relative_to(created).as_posix()
                  for p in created.rglob("*") if p.is_file()}
        for rel in ("orchestrator.py", "web_console/index.html",
                    "scripts/supervisor_control.py",
                    "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md",
                    "handoff/PROTOCOL.md", "START_WEB_CONSOLE.ps1"):
            self.assertIn(rel, copied, rel)
        for rel in ("control/ACTIVE_PROJECT.json", "control/STOP",
                    "control/orchestrator_runtime.json",
                    "web_console_data/instance.json",
                    "projects", "logs", "TO_ZCODE.md",
                    "SUPERVISOR_BRIEF.md", "PROJECT_GOAL.md"):
            self.assertNotIn(rel, copied, rel)
        self.assertFalse(any(p.name == "__pycache__"
                             for p in created.rglob("*")))
        # The created root answers the compatibility probe.
        status, _, probe = self.fixture.json(
            path=f"/api/runtimes/{entry['id']}/status")
        self.assertEqual(status, 200)
        _, _, listing = self.fixture.json(path="/api/runtimes")
        self.assertEqual(len(listing["runtimes"]), 3)

    def test_create_source_template_is_immutable(self):
        destination = self.work / "new-runtime"
        status, _, _ = self.create(destination)
        self.assertEqual(status, 201)
        self.assertEqual(tree_manifest(self.fixture.console_root),
                         self.manifest_before)

    def test_create_refusals(self):
        status, _, document = self.create(self.work / "x3", template="nope")
        self.assertEqual(status, 400)
        status, _, document = self.create("relative/dest")
        self.assertEqual(status, 400)
        self.assertEqual(document["error"]["code"],
                         "CREATE_INVALID_DESTINATION")
        status, _, document = self.create(str(self.work / "a" / ".." / "b"))
        self.assertEqual(status, 400)
        status, _, document = self.create(self.fixture.alpha / "nested")
        self.assertEqual(status, 400)
        self.assertEqual(document["error"]["code"],
                         "CREATE_DESTINATION_CONFLICTS")
        existing = self.work / "taken"
        existing.mkdir()
        status, _, document = self.create(existing)
        self.assertEqual(status, 409)
        self.assertEqual(document["error"]["code"],
                         "CREATE_DESTINATION_EXISTS")
        status, _, document = self.create(self.work / "badlabel",
                                          label="")
        self.assertEqual(status, 400)

    def test_unicode_destination_and_label_round_trip(self):
        destination = self.work / "运行时-二號"
        status, _, document = self.create(destination, label="二號 运行时 ✅")
        self.assertEqual(status, 201, document)
        self.assertEqual(document["runtime"]["label"], "二號 运行时 ✅")
        self.assertTrue((destination / "orchestrator.py").is_file())

    def test_concurrent_conflicting_creates_fail_closed(self):
        destination = self.work / "race"
        results = []

        def worker():
            results.append(self.create(destination))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        codes = sorted(status for status, _, _ in results)
        self.assertEqual(codes[0], 201)
        self.assertEqual(codes[1], 409)
        _, _, listing = self.fixture.json(path="/api/runtimes")
        self.assertEqual(len(listing["runtimes"]), 3)
        leftovers = [p.name for p in self.work.iterdir()
                     if p.name.startswith(".create-tmp-")]
        self.assertEqual(leftovers, [])

    def test_failed_copy_rolls_back_without_a_registered_root(self):
        self.fixture.stop()
        broken = P9Fixture(console_complete=False)
        try:
            work = broken.base / "created"
            work.mkdir()
            status, _, document = broken.json(
                "POST", "/api/runtimes/create",
                {"template_id": wcr.TEMPLATE_ID,
                 "destination": str(work / "broken-dest"),
                 "label": "Broken"})
            self.assertEqual(status, 502)
            self.assertIn(document["error"]["code"],
                          ("CREATE_SOURCE_INCOMPLETE",
                           "CREATE_VALIDATION_FAILED"))
            self.assertFalse((work / "broken-dest").exists())
            self.assertEqual([p.name for p in work.iterdir()], [])
            _, _, listing = broken.json(path="/api/runtimes")
            self.assertEqual(len(listing["runtimes"]), 2)
        finally:
            broken.close()

    def test_create_survives_restart_and_stays_registered(self):
        destination = self.work / "persistent"
        status, _, document = self.create(destination)
        self.assertEqual(status, 201)
        created_id = document["runtime"]["id"]
        self.fixture.restart()
        status, _, probe = self.fixture.json(
            path=f"/api/runtimes/{created_id}/status")
        self.assertEqual(status, 200)
        _, _, settings = self.fixture.json(
            path=f"/api/runtimes/{created_id}/settings")
        self.assertEqual(settings["runtime_settings"]["effective"]
                         ["timeline_page_size"]["source"],
                         "global-default")

    def test_method_allowlist_on_create_and_templates(self):
        status, _, _ = self.fixture.request("GET", "/api/runtimes/create")
        self.assertEqual(status, 405)
        status, _, _ = self.fixture.request("DELETE",
                                           "/api/runtime-templates")
        self.assertEqual(status, 405)
        status, _, _ = self.fixture.request("POST",
                                           "/api/runtime-templates",
                                           payload={})
        self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main()
