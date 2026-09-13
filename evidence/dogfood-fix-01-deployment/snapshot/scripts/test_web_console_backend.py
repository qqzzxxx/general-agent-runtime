"""P1 Web Console backend tests (offline, localhost only).

Covers the versioned health and read-only Runtime-status endpoints, the
supervisor_control --json subprocess contract (timeout, malformed output,
Unicode, explicit Runtime-root binding), loopback-only binding, Host-header
validation, the GET-only endpoint allowlist (mutation-shaped routes fail
closed), and static landing-page serving. No GUI, no external network.

The tests never touch authoritative Runtime state: fixture Runtime Roots are
synthetic directories (or a full isolated copy of this tree) and the control
plane is exercised read-only via `supervisor_control.py status --json`.
"""
from __future__ import annotations

import http.client
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_server as wcs


STUB_CONTROL = """\
import json, sys
from pathlib import Path
mode_file = Path(__file__).resolve().parents[1] / "stub_mode.txt"
mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.exists() else "ok"


def emit(value):
    # Mirror the v1.2 machine mode: one ASCII-escaped JSON document.
    print(json.dumps(value, ensure_ascii=True, indent=2))


if mode == "ok":
    emit({"schema_version": 1, "PROJECT_ID": None, "runtime_status": "IDLE-FIXTURE",
          "project_status": "SUPERVISOR_TURN", "pause": {"status": "RUNNING"},
          "active_task": None, "active_task_claimed": False,
          "pending_interventions": 0, "stop": False, "human_review": False})
    sys.exit(0)
if mode == "unicode":
    emit({"schema_version": 1, "PROJECT_ID": None, "note": "caf\\u00e9 \\u4e2d\\u6587",
          "runtime_status": None})
    sys.exit(0)
if mode == "exit2_json":
    emit({"ok": False, "error": "boom: caf\\u00e9", "error_type": "ControlError"})
    sys.exit(2)
if mode == "exit2_garbage":
    sys.stdout.write("SUPERVISOR_CONTROL_ERROR: boom\\n")
    sys.exit(2)
if mode == "garbage_ok":
    sys.stdout.write("this is definitely not json\\n")
    sys.exit(0)
if mode == "sleep":
    import time
    time.sleep(30)
    sys.exit(0)
if mode == "crash":
    sys.exit(1)
sys.exit(3)
"""


def make_fixture_runtime(console_parent: Path, control: str) -> Path:
    runtime = console_parent / "runtime"
    (runtime / "scripts").mkdir(parents=True, exist_ok=True)
    (runtime / "scripts" / "supervisor_control.py").write_text(
        control, encoding="utf-8")
    return runtime


class ServerFixture:
    """One in-process console server bound to an ephemeral loopback port."""

    def __init__(self, control: str | None = STUB_CONTROL, control_timeout: float = 8.0,
                 runtime_root: Path | None = None):
        self.tmp = tempfile.TemporaryDirectory(prefix="wc-backend-")
        base = Path(self.tmp.name)
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        if runtime_root is None:
            self.runtime_root = base / "runtime"
            self.runtime_root.mkdir()
            if control is not None:
                (self.runtime_root / "scripts").mkdir()
                (self.runtime_root / "scripts" / "supervisor_control.py").write_text(
                    control, encoding="utf-8")
        else:
            self.runtime_root = runtime_root
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_root,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=control_timeout)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "/",
                host: str | None = None, timeout: float = 20.0,
                skip_host: bool = False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            if skip_host:
                conn.putrequest(method, path, skip_host=True,
                                skip_accept_encoding=True)
                conn.endheaders()
            else:
                headers = {} if host is None else {"Host": host}
                conn.request(method, path, headers=headers)
            response = conn.getresponse()
            body = response.read()
            return response.status, dict(response.getheaders()), body
        finally:
            conn.close()

    def json(self, **kwargs):
        status, headers, body = self.request(**kwargs)
        return status, headers, json.loads(body.decode("utf-8"))

    def set_stub_mode(self, mode: str) -> None:
        (self.runtime_root / "stub_mode.txt").write_text(mode, encoding="utf-8")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.tmp.cleanup()


class BackendTestCase(unittest.TestCase):
    def setUp(self):
        self._fixtures = []

    def fixture(self, **kwargs) -> ServerFixture:
        fixture = ServerFixture(**kwargs)
        self._fixtures.append(fixture)
        return fixture

    def tearDown(self):
        for fixture in self._fixtures:
            fixture.close()


class BindingAndStartupTests(BackendTestCase):
    def test_non_loopback_bind_fails_closed_before_listening(self):
        for host in ("0.0.0.0", "::", "192.168.1.10", "localhost"):
            with self.assertRaises(ValueError):
                wcs.WebConsoleServer.create(
                    host=host, port=0, runtime_root=Path(self.tmp_root()),
                    console_root=self._console_root(),
                    data_dir=Path(self.tmp_root()) / "data",
                    control_timeout=5.0)

    def test_invalid_port_fails_closed(self):
        for port in (-1, 65536, 123456):
            with self.assertRaises(ValueError):
                wcs.WebConsoleServer.create(
                    host="127.0.0.1", port=port, runtime_root=Path(self.tmp_root()),
                    console_root=self._console_root(),
                    data_dir=Path(self.tmp_root()) / "data",
                    control_timeout=5.0)

    def test_os_assigned_port_binds_and_syncs_config(self):
        base = Path(self.tmp_root())
        runtime = make_fixture_runtime(base, STUB_CONTROL)
        server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=runtime,
            console_root=self._console_root(), data_dir=base / "data",
            control_timeout=5.0)
        self.addCleanup(server.server_close)
        actual = server.server_address[1]
        self.assertNotEqual(actual, 0)
        self.assertEqual(server.config.port, actual)

    def test_missing_runtime_root_fails_closed(self):
        with self.assertRaises(ValueError):
            wcs.WebConsoleServer.create(
                host="127.0.0.1", port=0, runtime_root=Path(self.tmp_root()) / "nope",
                console_root=self._console_root(),
                data_dir=Path(self.tmp_root()) / "data", control_timeout=5.0)

    def test_missing_landing_page_fails_closed(self):
        base = Path(self.tmp_root())
        runtime = make_fixture_runtime(base, STUB_CONTROL)
        console = base / "bare-console"
        console.mkdir()
        with self.assertRaises(ValueError):
            wcs.WebConsoleServer.create(
                host="127.0.0.1", port=0, runtime_root=runtime,
                console_root=console, data_dir=base / "data", control_timeout=5.0)

    def test_cli_refuses_non_loopback_host_without_creating_metadata(self):
        base = Path(self.tmp_root())
        runtime = make_fixture_runtime(base, STUB_CONTROL)
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS / "web_console_server.py"), "serve",
             "--host", "0.0.0.0", "--port", "8765", "--runtime-root", str(runtime),
             "--data-dir", str(base / "data"), "--console-root", str(self._console_root())],
            capture_output=True, text=True, timeout=30, cwd=str(REPO))
        self.assertNotEqual(proc.returncode, 0)
        combined = (proc.stdout + proc.stderr).lower()
        self.assertIn("loopback", combined)
        self.assertFalse((base / "data" / "instance.json").exists())

    def test_cli_refuses_out_of_range_port(self):
        base = Path(self.tmp_root())
        runtime = make_fixture_runtime(base, STUB_CONTROL)
        cases = {"0": "explicit port", "70000": "port must be an integer"}
        for port, expected in cases.items():
            proc = subprocess.run(
                [sys.executable, str(SCRIPTS / "web_console_server.py"), "serve",
                 "--host", "127.0.0.1", "--port", port, "--runtime-root", str(runtime),
                 "--data-dir", str(base / "data"), "--console-root", str(self._console_root())],
                capture_output=True, text=True, timeout=30, cwd=str(REPO))
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn(expected, proc.stderr + proc.stdout)

    def tmp_root(self) -> str:
        if not hasattr(self, "_tmp"):
            self._tmp = tempfile.TemporaryDirectory(prefix="wc-bind-")
            self.addCleanup(self._tmp.cleanup)
        return self._tmp.name

    def _console_root(self) -> Path:
        base = Path(self.tmp_root())
        console = base / "console"
        if not console.is_dir():
            (console / "web_console").mkdir(parents=True)
            shutil.copy(REPO / "web_console" / "index.html",
                        console / "web_console" / "index.html")
        return console


class HealthEndpointTests(BackendTestCase):
    def test_health_is_liveness_with_structural_control_plane_signal(self):
        fixture = self.fixture()
        status, headers, payload = fixture.json(path="/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["backend"]["status"], "ok")
        self.assertIn("application/json", headers["Content-Type"])
        self.assertIn("charset=utf-8", headers["Content-Type"])
        instance = payload["instance"]
        self.assertEqual(instance["port"], fixture.port)
        self.assertEqual(instance["pid"], fixture.server.config.pid)
        self.assertTrue(instance["token"])
        self.assertEqual(instance["runtime_root"], str(fixture.runtime_root))
        self.assertTrue(payload["control_plane"]["script_present"])

    def test_health_reports_missing_control_plane_without_dying(self):
        fixture = self.fixture(control=None)  # runtime root with no control script
        status, _, payload = fixture.json(path="/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["control_plane"]["script_present"])

    def test_instance_metadata_written_atomically_and_matches_health(self):
        fixture = self.fixture()
        meta_path = fixture.data_dir / "instance.json"
        self.assertTrue(meta_path.is_file())
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["schema_version"], 1)
        _, _, payload = fixture.json(path="/api/health")
        self.assertEqual(meta["token"], payload["instance"]["token"])
        self.assertEqual(meta["port"], payload["instance"]["port"])

    def test_instance_metadata_binds_runtime_root(self):
        fixture = self.fixture()
        meta = json.loads((fixture.data_dir / "instance.json").read_text(
            encoding="utf-8"))
        self.assertEqual(Path(meta["runtime_root"]), fixture.runtime_root.resolve())


class HostHeaderTests(BackendTestCase):
    def test_default_and_explicit_loopback_hosts_accepted(self):
        fixture = self.fixture()
        for host in (None, "127.0.0.1", f"127.0.0.1:{fixture.port}"):
            status, _, payload = fixture.json(path="/api/health", host=host)
            self.assertEqual(status, 200)

    def test_unapproved_host_headers_rejected(self):
        fixture = self.fixture()
        for host in ("evil.example", "localhost", "0.0.0.0", "127.0.0.1.evil.com",
                     "[::1]", "127.0.0.1:1", f"127.0.0.1:{fixture.port + 1}"):
            status, _, payload = fixture.json(path="/api/health", host=host)
            self.assertEqual(status, 403, host)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "HOST_HEADER_REJECTED")

    def test_missing_host_header_rejected(self):
        fixture = self.fixture()
        status, _, payload = fixture.json(path="/api/health", skip_host=True)
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])

    def test_host_validation_precedes_routing(self):
        fixture = self.fixture()
        for path in ("/", "/api/health", "/api/status", "/api/pause"):
            status, _, payload = fixture.json(path=path, host="evil.example")
            self.assertEqual(status, 403, path)


class StatusEndpointTests(BackendTestCase):
    def test_status_wraps_control_plane_success(self):
        fixture = self.fixture()
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["schema_version"], 1)
        control = payload["control_plane"]
        self.assertTrue(control["ok"])
        self.assertEqual(control["exit_code"], 0)
        self.assertEqual(control["runtime_root"], str(fixture.runtime_root))
        self.assertEqual(control["status"]["schema_version"], 1)
        self.assertEqual(control["status"]["runtime_status"], "IDLE-FIXTURE")

    def test_status_binds_the_requested_runtime_root_argument_vector(self):
        # The stub records nothing, but the response must echo the exact root;
        # a root switch is observable through the control_plane.runtime_root.
        fixture = self.fixture()
        _, _, payload = fixture.json(path="/api/status")
        self.assertEqual(payload["control_plane"]["runtime_root"],
                         str(fixture.runtime_root))
        self.assertNotEqual(payload["control_plane"]["runtime_root"],
                            str(fixture.console_root))

    def test_status_against_real_control_plane_in_tree_copy(self):
        fixture = self.fixture(control=None, runtime_root=REPO)
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        real = payload["control_plane"]["status"]
        self.assertEqual(real["schema_version"], 1)
        self.assertIn("runtime_status", real)
        self.assertIn("project_status", real)
        self.assertIn("pause", real)
        self.assertIn("stop", real)
        self.assertIn("human_review", real)

    def test_control_plane_error_exit_becomes_deterministic_envelope(self):
        fixture = self.fixture()
        fixture.set_stub_mode("exit2_json")
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        error = payload["error"]
        self.assertEqual(error["code"], "CONTROL_PLANE_ERROR")
        self.assertEqual(error["detail"]["exit_code"], 2)
        self.assertEqual(error["detail"]["control_error"]["error_type"],
                         "ControlError")
        self.assertIn("café", error["detail"]["control_error"]["error"])

    def test_control_plane_error_with_garbage_output_still_deterministic(self):
        fixture = self.fixture()
        fixture.set_stub_mode("exit2_garbage")
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")
        self.assertIsNone(payload["error"]["detail"]["control_error"])
        self.assertIn("SUPERVISOR_CONTROL_ERROR",
                      payload["error"]["detail"]["stdout_head"])

    def test_missing_control_plane_script_is_reported_not_hidden(self):
        fixture = self.fixture()
        (fixture.runtime_root / "scripts" / "supervisor_control.py").unlink()
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 502)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")

    def test_zero_exit_non_json_output_is_malformed_envelope(self):
        fixture = self.fixture()
        fixture.set_stub_mode("garbage_ok")
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_MALFORMED_OUTPUT")

    def test_crashing_control_plane_maps_to_control_plane_error(self):
        fixture = self.fixture()
        fixture.set_stub_mode("crash")
        status, _, payload = fixture.json(path="/api/status")
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")
        self.assertEqual(payload["error"]["detail"]["exit_code"], 1)

    def test_subprocess_timeout_is_bounded_and_kills_child(self):
        fixture = self.fixture(control_timeout=0.5)
        fixture.set_stub_mode("sleep")
        started = time.monotonic()
        status, _, payload = fixture.json(path="/api/status", timeout=20.0)
        elapsed = time.monotonic() - started
        self.assertEqual(status, 504)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_TIMEOUT")
        self.assertLess(elapsed, 15.0)

    def test_unicode_status_payload_round_trips_as_utf8(self):
        fixture = self.fixture()
        fixture.set_stub_mode("unicode")
        status, headers, body = fixture.request(path="/api/status")
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", headers["Content-Type"])
        payload = json.loads(body.decode("utf-8"))  # strict UTF-8 decode
        self.assertEqual(payload["control_plane"]["status"]["note"],
                         "café 中文")

    def test_concurrent_status_requests_are_handled(self):
        fixture = self.fixture(control_timeout=10.0)
        results = []

        def worker():
            results.append(fixture.json(path="/api/status"))

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(len(results), 3)
        for status, _, payload in results:
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])


class AllowlistAndMethodTests(BackendTestCase):
    MUTATION_ROUTES = ("/api/pause", "/api/resume", "/api/intervene", "/api/stop",
                       "/api/shutdown", "/api/restart", "/api/control")

    def test_get_only_unknown_api_routes_fail_closed(self):
        fixture = self.fixture()
        for path in self.MUTATION_ROUTES + ("/api", "/api/", "/api/tasks",
                                            "/api/unknown"):
            status, _, payload = fixture.json(path=path)
            self.assertEqual(status, 404, path)
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["error"]["code"], "ROUTE_NOT_FOUND")

    def test_mutation_methods_rejected_everywhere(self):
        fixture = self.fixture()
        paths = ("/", "/api/health", "/api/status") + self.MUTATION_ROUTES
        for method in ("POST", "PUT", "DELETE", "PATCH", "OPTIONS"):
            for path in paths:
                status, _, payload = fixture.json(method=method, path=path)
                self.assertEqual(status, 405, f"{method} {path}")
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "METHOD_NOT_ALLOWED")

    def test_unknown_verbs_fail_closed(self):
        fixture = self.fixture()
        status, _, _ = fixture.request(method="BREW", path="/api/health")
        self.assertEqual(status, 501)

    def test_head_requests_serve_headers_without_body(self):
        fixture = self.fixture()
        status, headers, body = fixture.request(method="HEAD", path="/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")
        self.assertIn("application/json", headers["Content-Type"])

    def test_query_strings_do_not_bypass_the_allowlist(self):
        fixture = self.fixture()
        status, _, payload = fixture.json(path="/api/status?command=pause")
        self.assertEqual(status, 200)  # same read-only route, query ignored
        status, _, payload = fixture.json(path="/api/pause?dry=1")
        self.assertEqual(status, 404)


class StaticServingTests(BackendTestCase):
    def test_landing_page_served_at_root_and_index(self):
        fixture = self.fixture()
        for path in ("/", "/index.html"):
            status, headers, body = fixture.request(path=path)
            self.assertEqual(status, 200, path)
            self.assertIn("text/html", headers["Content-Type"])
            self.assertIn("charset=utf-8", headers["Content-Type"])
            text = body.decode("utf-8")
            self.assertIn("<!DOCTYPE html>", text)
            self.assertIn("General Agent Runtime v1.3", text)
        root_text = fixture.request(path="/")[2].decode("utf-8")
        index_text = fixture.request(path="/index.html")[2].decode("utf-8")
        self.assertEqual(root_text, index_text)

    def test_traversal_and_unknown_paths_rejected(self):
        fixture = self.fixture()
        for path in ("/../orchestrator.py", "/../../project_state.json",
                      "/scripts/supervisor_control.py",
                      "/web_console/../scripts/supervisor_control.py",
                      "/%2e%2e/orchestrator.py", "/static/../../secrets.txt",
                      "/nope.html", "/api/health/extra"):
            status, _, _ = fixture.request(path=path)
            self.assertEqual(status, 404, path)

    def test_landing_page_has_no_external_dependencies(self):
        text = (REPO / "web_console" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("src=", text)
        self.assertNotIn("@import", text)
        self.assertIn('<meta charset="utf-8">', text)
        self.assertIn("/api/health", text)
        self.assertIn("/api/status", text)


if __name__ == "__main__":
    unittest.main()
