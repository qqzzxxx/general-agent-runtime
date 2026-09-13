"""P1 Web Console lifecycle script tests (START/STOP_WEB_CONSOLE.ps1).

These tests exercise the real PowerShell lifecycle scripts end to end on
Windows: single-instance start, already-running reuse, verified/idempotent/
refusing stop behavior, stale and foreign instance metadata, health-timeout
failure, foreground diagnostic mode, and the shared instance verifier.
Every invocation passes -NoBrowser so automation never opens a GUI browser.

Fixture Runtime Roots are synthetic directories holding a real copy of
scripts/supervisor_control.py; nothing authoritative is touched.
"""
from __future__ import annotations

import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

START_SCRIPT = REPO / "START_WEB_CONSOLE.ps1"
STOP_SCRIPT = REPO / "STOP_WEB_CONSOLE.ps1"
SERVER_SCRIPT = SCRIPTS / "web_console_server.py"
DATA_DIR = REPO / "web_console_data"


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    except Exception:
        return False


def _recently_dead_pid() -> int:
    """A pid that existed and has exited (best-effort uniqueness)."""
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    time.sleep(0.2)
    proc.kill()
    proc.wait(timeout=30)
    time.sleep(0.2)
    return proc.pid


class PowerShellLifecycle:
    def __init__(self, method_name: str):
        self.tag = f"{method_name}-{uuid.uuid4().hex[:8]}"
        self.started_pids: list[int] = []

    def run_ps(self, script: Path, *args: str, timeout: float = 180.0):
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive",
               "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
        return subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def start(self, runtime_root: Path, port: int | None = None,
              extra: tuple[str, ...] = (), timeout: float = 180.0):
        args = ["-RuntimeRoot", str(runtime_root), "-NoBrowser"]
        if port is not None:
            args += ["-Port", str(port)]
        args.extend(extra)
        return self.run_ps(START_SCRIPT, *args, timeout=timeout)

    def stop(self, runtime_root: Path, timeout: float = 120.0):
        return self.run_ps(STOP_SCRIPT, "-RuntimeRoot", str(runtime_root),
                           timeout=timeout)

    def health(self, port: int, timeout: float = 5.0):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
        try:
            conn.request("GET", "/api/health",
                         headers={"Host": f"127.0.0.1:{port}"})
            response = conn.getresponse()
            body = response.read()
            return response.status, json.loads(body.decode("utf-8"))
        except OSError:
            return None, None
        finally:
            conn.close()

    @staticmethod
    def field(text: str, name: str):
        match = re.search(rf"{name}=(\S+)", text)
        return int(match.group(1)) if match else None

    def write_metadata(self, data_dir: Path, *, pid: int, port: int,
                       runtime_root: Path, token: str = "0" * 32) -> Path:
        data_dir.mkdir(parents=True, exist_ok=True)
        meta = {"schema_version": 1, "pid": pid, "port": port, "token": token,
                "runtime_root": str(runtime_root), "console_root": str(REPO),
                "started_at": datetime.now(timezone.utc).isoformat()}
        path = data_dir / "instance.json"
        path.write_text(json.dumps(meta), encoding="utf-8")
        return path

    def verify(self, data_dir: Path, runtime_root: Path):
        proc = subprocess.run(
            [sys.executable, str(SERVER_SCRIPT), "verify-instance",
             "--data-dir", str(data_dir), "--runtime-root", str(runtime_root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60)
        if proc.returncode != 0:
            raise AssertionError(
                f"verify-instance failed: {proc.returncode} {proc.stderr}")
        return json.loads(proc.stdout)

    def kill_pid_tree(self, pid: int) -> None:
        # PowerShell/taskkill output follows the console codepage; decode
        # permissively so a UTF-8 test environment cannot crash the reader.
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=30)

    def wait_pid_gone(self, pid: int, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                return True
            time.sleep(0.25)
        return not pid_alive(pid)

    def cleanup(self, runtime_root: Path) -> None:
        for pid in list(self.started_pids):
            self.kill_pid_tree(pid)
            self.wait_pid_gone(pid, timeout=10)
        kill_stray_console_servers(REPO)
        if (DATA_DIR / "instance.json").exists():
            try:
                self.stop(runtime_root)
            except Exception:
                pass
        if (DATA_DIR / "instance.json").exists():
            try:
                (DATA_DIR / "instance.json").unlink()
            except OSError:
                pass


def console_server_pids_for(repo_path: Path) -> list[int]:
    """Pids of live web_console_server processes bound to this tree only."""
    query = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*web_console_server.py serve*' "
             f"-and $_.CommandLine -like '*{repo_path}*' }} | "
             "Select-Object -ExpandProperty ProcessId")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", query],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids = []
    for line in proc.stdout.decode("utf-8", errors="replace").split():
        if line.strip().isdigit():
            pids.append(int(line))
    return pids


def kill_stray_console_servers(repo_path: Path) -> None:
    """Safety net: no test-started backend may outlive the test session."""
    for pid in console_server_pids_for(repo_path):
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            pass


def tearDownModule():
    kill_stray_console_servers(REPO)


@unittest.skipUnless(sys.platform == "win32",
                     "PowerShell lifecycle scripts are Windows-only")
class WebConsoleScriptTests(unittest.TestCase):
    def setUp(self):
        self.lifecycle = PowerShellLifecycle(self.id())
        self.tmp = tempfile.TemporaryDirectory(prefix="wc-scripts-")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.runtime_root = self.base / "runtime"
        (self.runtime_root / "scripts").mkdir(parents=True)
        shutil.copy(SCRIPTS / "supervisor_control.py",
                    self.runtime_root / "scripts" / "supervisor_control.py")
        if (DATA_DIR / "instance.json").exists():
            (DATA_DIR / "instance.json").unlink()
        self.addCleanup(self.lifecycle.cleanup, self.runtime_root)

    def track_server_pid(self, output: str) -> int:
        pid = self.lifecycle.field(output, "pid")
        self.assertIsNotNone(pid)
        self.lifecycle.started_pids.append(pid)
        return pid

    # -- start / health -----------------------------------------------------

    def test_start_waits_for_health_and_reports_ready_without_browser(self):
        port = free_port()
        proc = self.lifecycle.start(self.runtime_root, port=port)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WEB_CONSOLE_READY", proc.stdout)
        pid = self.track_server_pid(proc.stdout)
        self.assertTrue(pid_alive(pid))
        meta = json.loads((DATA_DIR / "instance.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["pid"], pid)
        self.assertEqual(meta["port"], port)
        self.assertEqual(Path(meta["runtime_root"]), self.runtime_root.resolve())
        status, payload = self.lifecycle.health(port)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["instance"]["runtime_root"],
                         str(self.runtime_root.resolve()))

    def test_start_twice_reuses_the_single_healthy_instance(self):
        port = free_port()
        first = self.lifecycle.start(self.runtime_root, port=port)
        self.assertEqual(first.returncode, 0, first.stdout)
        pid = self.track_server_pid(first.stdout)
        second = self.lifecycle.start(self.runtime_root, port=port)
        self.assertEqual(second.returncode, 0, second.stdout)
        self.assertIn("WEB_CONSOLE_ALREADY_RUNNING", second.stdout)
        self.assertEqual(self.lifecycle.field(second.stdout, "pid"), pid)
        self.assertEqual(json.loads(
            (DATA_DIR / "instance.json").read_text(encoding="utf-8"))["pid"], pid)
        status, payload = self.lifecycle.health(port)
        self.assertEqual(status, 200)
        self.assertEqual(payload["instance"]["pid"], pid)

    def test_start_default_runtime_root_is_the_installation_root(self):
        proc = self.lifecycle.start(REPO, port=free_port())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        pid = self.track_server_pid(proc.stdout)
        status, payload = self.lifecycle.health(
            json.loads((DATA_DIR / "instance.json").read_text(
                encoding="utf-8"))["port"])
        self.assertEqual(status, 200)
        self.assertEqual(payload["instance"]["runtime_root"], str(REPO.resolve()))

    def test_start_recovers_from_stale_metadata(self):
        dead = _recently_dead_pid()
        self.lifecycle.write_metadata(DATA_DIR, pid=dead, port=free_port(),
                                      runtime_root=self.runtime_root)
        proc = self.lifecycle.start(self.runtime_root, port=free_port())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WEB_CONSOLE_READY", proc.stdout)
        self.track_server_pid(proc.stdout)
        meta = json.loads((DATA_DIR / "instance.json").read_text(encoding="utf-8"))
        self.assertNotEqual(meta["pid"], dead)

    def test_start_fails_closed_when_port_is_unavailable(self):
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.bind(("127.0.0.1", 0))
        blocker.listen(1)
        self.addCleanup(blocker.close)
        port = blocker.getsockname()[1]
        started = time.monotonic()
        proc = self.lifecycle.start(self.runtime_root, port=port,
                                    extra=("-HealthTimeoutSeconds", "20"))
        elapsed = time.monotonic() - started
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("WEB_CONSOLE_START_FAILED", proc.stdout)
        self.assertLess(elapsed, 60.0)

    def test_start_refuses_runtime_root_without_control_plane(self):
        bare = self.base / "bare-runtime"
        bare.mkdir()
        proc = self.lifecycle.start(bare, port=free_port())
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("WEB_CONSOLE_START_FAILED", proc.stdout)
        self.assertFalse((DATA_DIR / "instance.json").exists())

    def test_start_refuses_missing_runtime_root(self):
        proc = self.lifecycle.start(self.base / "does-not-exist", port=free_port())
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse((DATA_DIR / "instance.json").exists())

    def test_foreground_mode_serves_without_browser_until_killed(self):
        port = free_port()
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-File", str(START_SCRIPT),
             "-RuntimeRoot", str(self.runtime_root), "-NoBrowser",
             "-Port", str(port), "-Foreground"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace")
        self.addCleanup(self._kill_tree, proc)
        deadline = time.monotonic() + 60
        status = payload = None
        while time.monotonic() < deadline:
            status, payload = self.lifecycle.health(port)
            if status == 200:
                break
            time.sleep(0.5)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIsNone(proc.poll(),
                          "foreground console exited on its own")
        server_pid = payload["instance"]["pid"]
        self.assertTrue(pid_alive(server_pid))

    def _kill_tree(self, proc: subprocess.Popen) -> None:
        if proc.poll() is None:
            self.lifecycle.kill_pid_tree(proc.pid)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                pass

    # -- stop ---------------------------------------------------------------

    def test_stop_verified_instance_then_idempotent_again(self):
        port = free_port()
        started = self.lifecycle.start(self.runtime_root, port=port)
        self.assertEqual(started.returncode, 0, started.stdout)
        pid = self.track_server_pid(started.stdout)
        stopped = self.lifecycle.stop(self.runtime_root)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn("WEB_CONSOLE_STOPPED", stopped.stdout)
        self.assertTrue(self.lifecycle.wait_pid_gone(pid))
        self.assertFalse((DATA_DIR / "instance.json").exists())
        again = self.lifecycle.stop(self.runtime_root)
        self.assertEqual(again.returncode, 0, again.stdout)
        self.assertIn("WEB_CONSOLE_NOT_RUNNING", again.stdout)
        status, _ = self.lifecycle.health(port)
        self.assertIsNone(status)

    def test_stop_removes_stale_metadata(self):
        dead = _recently_dead_pid()
        self.lifecycle.write_metadata(DATA_DIR, pid=dead, port=free_port(),
                                      runtime_root=self.runtime_root)
        stopped = self.lifecycle.stop(self.runtime_root)
        self.assertEqual(stopped.returncode, 0, stopped.stdout + stopped.stderr)
        self.assertIn("WEB_CONSOLE_STALE_METADATA_REMOVED", stopped.stdout)
        self.assertFalse((DATA_DIR / "instance.json").exists())

    def test_stop_refuses_foreign_live_pid(self):
        victim = subprocess.Popen([sys.executable, "-c",
                                   "import time; time.sleep(120)"])
        self.addCleanup(self.lifecycle.kill_pid_tree, victim.pid)
        self.addCleanup(self.lifecycle.wait_pid_gone, victim.pid, 10)
        self.lifecycle.write_metadata(DATA_DIR, pid=victim.pid,
                                      port=free_port(),
                                      runtime_root=self.runtime_root)
        stopped = self.lifecycle.stop(self.runtime_root)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("WEB_CONSOLE_STOP_REFUSED", stopped.stdout)
        self.assertTrue(pid_alive(victim.pid),
                        "STOP killed an unrelated process")

    def test_stop_refuses_corrupt_metadata(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "instance.json").write_text("not json at all",
                                                encoding="utf-8")
        stopped = self.lifecycle.stop(self.runtime_root)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("WEB_CONSOLE_STOP_REFUSED", stopped.stdout)

    def test_stop_refuses_metadata_bound_to_another_runtime(self):
        victim = subprocess.Popen([sys.executable, "-c",
                                   "import time; time.sleep(120)"])
        self.addCleanup(self.lifecycle.kill_pid_tree, victim.pid)
        self.addCleanup(self.lifecycle.wait_pid_gone, victim.pid, 10)
        other = self.base / "other-runtime"
        other.mkdir()
        self.lifecycle.write_metadata(DATA_DIR, pid=victim.pid,
                                      port=free_port(), runtime_root=other)
        stopped = self.lifecycle.stop(self.runtime_root)
        self.assertNotEqual(stopped.returncode, 0)
        self.assertIn("WEB_CONSOLE_STOP_REFUSED", stopped.stdout)
        self.assertTrue(pid_alive(victim.pid))

    # -- shared verifier ----------------------------------------------------

    def test_verify_instance_reports_missing_stale_foreign_and_unverified(self):
        # MISSING
        verdict = self.lifecycle.verify(DATA_DIR, self.runtime_root)
        self.assertEqual(verdict["verdict"], "MISSING")
        # STALE
        dead = _recently_dead_pid()
        self.lifecycle.write_metadata(DATA_DIR, pid=dead, port=free_port(),
                                      runtime_root=self.runtime_root)
        self.assertEqual(
            self.lifecycle.verify(DATA_DIR, self.runtime_root)["verdict"], "STALE")
        # FOREIGN (bound to another runtime root)
        other = self.base / "other-runtime"
        other.mkdir()
        self.lifecycle.write_metadata(DATA_DIR, pid=dead, port=free_port(),
                                      runtime_root=other)
        self.assertEqual(
            self.lifecycle.verify(DATA_DIR, self.runtime_root)["verdict"], "FOREIGN")
        # ALIVE_UNVERIFIED (live pid, closed port)
        victim = subprocess.Popen([sys.executable, "-c",
                                   "import time; time.sleep(120)"])
        self.addCleanup(self.lifecycle.kill_pid_tree, victim.pid)
        self.addCleanup(self.lifecycle.wait_pid_gone, victim.pid, 10)
        self.lifecycle.write_metadata(DATA_DIR, pid=victim.pid,
                                      port=free_port(),
                                      runtime_root=self.runtime_root)
        self.assertEqual(self.lifecycle.verify(DATA_DIR, self.runtime_root)[
            "verdict"], "ALIVE_UNVERIFIED")

    def test_verify_instance_confirms_live_owned_instance(self):
        import web_console_server as wcs
        console = self.base / "console"
        (console / "web_console").mkdir(parents=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    console / "web_console" / "index.html")
        server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_root,
            console_root=console, data_dir=console / "data", control_timeout=5.0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            server.write_instance_metadata()
            verdict = self.lifecycle.verify(console / "data", self.runtime_root)
            self.assertEqual(verdict["verdict"], "VERIFIED")
            self.assertEqual(verdict["health"]["instance"]["token"],
                             server.config.token)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
