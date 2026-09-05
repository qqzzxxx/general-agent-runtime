"""Instance-isolation regression: one orchestrator per runtime root, parallel roots allowed.

Covers the multi-runtime safety contract:
  I1  same runtime root, second orchestrator  -> BLOCKED (fail closed)
  I2  different runtime roots                 -> each acquires its own lock, parallel OK
  I3  stale lock (dead recorded owner)        -> reclaimed by the verified FIX-F04 rule
  I4  one root's STOP / lock / state          -> never affects another root
  I5  fail-closed semantics preserved         -> corrupt/unreadable lock is never auto-reclaimed
  I6  launcher guard                          -> START_AGENT_SYSTEM.ps1 stays root-scoped
                                                  (no machine-wide process scanning)

These tests never launch the real orchestrator loop; they exercise the lock/state
primitives on temp runtime roots only.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
LAUNCHER = CANDIDATE / "START_AGENT_SYSTEM.ps1"


def load_orchestrator(root: Path):
    """Import a private orchestrator module instance and rebind its path globals to root."""
    spec = importlib.util.spec_from_file_location(f"iso_orchestrator_{abs(hash(root))}",
                                                  CANDIDATE / "orchestrator.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.ROOT = root
    m.CONTROL = root / "control"
    m.LOCK_FILE = m.CONTROL / ".orchestrator.lock"
    m.PROJECT_STATE = m.CONTROL / "project_state.json"
    m.ACTIVE_PROJECT_FILE = m.CONTROL / "ACTIVE_PROJECT.json"
    m.STOP_FLAG = m.CONTROL / "STOP"
    m.LOGS = root / "logs"  # keep test log output inside the temp root
    return m


def make_root(base: Path, name: str) -> Path:
    root = base / name
    (root / "control").mkdir(parents=True)
    return root


def lock_payload(pid: int) -> dict:
    return {"pid": pid, "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


class InstanceIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="instance-iso-")
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _live_process(self):
        # sleep instead of input(): child stdin may be closed, which would make the
        # process exit instantly and turn the "live owner" fixture into a stale lock
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        time.sleep(0.3)  # let the OS register the process
        return proc

    # I1: same root, second orchestrator -> BLOCKED
    def test_i1_same_root_second_start_blocked(self):
        root = make_root(self.base, "root-a")
        first = load_orchestrator(root)
        first.acquire_lock()  # live owner: this process
        second = load_orchestrator(root)
        with self.assertRaises(RuntimeError):
            second.acquire_lock()

        # same verdict when the recorded owner is a different live process
        root2 = make_root(self.base, "root-a2")
        proc = self._live_process()
        try:
            payload = lock_payload(proc.pid)
            (root2 / "control" / ".orchestrator.lock").write_text(
                json.dumps(payload), encoding="utf-8")
            m = load_orchestrator(root2)
            with self.assertRaises(RuntimeError):
                m.acquire_lock()
        finally:
            proc.kill()
            proc.wait()

    # I2: different roots -> parallel orchestrators allowed
    def test_i2_different_roots_run_in_parallel(self):
        root_a = make_root(self.base, "par-a")
        root_b = make_root(self.base, "par-b")
        mod_a = load_orchestrator(root_a)
        mod_b = load_orchestrator(root_b)
        mod_a.acquire_lock()
        mod_b.acquire_lock()  # must not be blocked by root-a's lock
        self.assertTrue(mod_a.LOCK_FILE.exists())
        self.assertTrue(mod_b.LOCK_FILE.exists())
        self.assertNotEqual(mod_a.LOCK_FILE, mod_b.LOCK_FILE)
        self.assertEqual(json.loads(mod_a.LOCK_FILE.read_text(encoding="utf-8"))["pid"],
                         json.loads(mod_b.LOCK_FILE.read_text(encoding="utf-8"))["pid"])

    # I3: stale lock from a dead owner -> reclaimed
    def test_i3_stale_lock_reclaimed(self):
        root = make_root(self.base, "stale")
        proc = self._live_process()
        pid = proc.pid
        proc.kill()
        proc.wait()
        m = load_orchestrator(root)
        if not m._lock_owner_dead(lock_payload(pid)):
            self.skipTest("pid was reused too quickly to simulate a dead owner")
        (root / "control" / ".orchestrator.lock").write_text(
            json.dumps(lock_payload(pid)), encoding="utf-8")
        m.acquire_lock()
        reclaimed = json.loads((root / "control" / ".orchestrator.lock")
                               .read_text(encoding="utf-8"))
        self.assertEqual(reclaimed["pid"], m.os.getpid())

    # I4: one root's STOP / lock / state does not leak into another root
    def test_i4_root_state_isolation(self):
        root_a = make_root(self.base, "iso-a")
        root_b = make_root(self.base, "iso-b")
        mod_a = load_orchestrator(root_a)
        mod_b = load_orchestrator(root_b)

        mod_a.acquire_lock()
        mod_b.acquire_lock()
        mod_a.STOP_FLAG.write_text("USER_STOP", encoding="utf-8")
        mod_a.atomic_json(mod_a.PROJECT_STATE, {"project": "proj-a", "status": "STOPPED"})
        mod_b.atomic_json(mod_b.PROJECT_STATE, {"project": "proj-b", "status": "SUPERVISOR_TURN"})

        self.assertTrue(mod_a.STOP_FLAG.exists())
        self.assertFalse(mod_b.STOP_FLAG.exists())
        self.assertEqual(mod_a.read_project_state()["project"], "proj-a")
        self.assertEqual(mod_b.read_project_state()["project"], "proj-b")

        mod_a.release_lock()  # releasing A must not touch B's lock
        self.assertFalse(mod_a.LOCK_FILE.exists())
        self.assertTrue(mod_b.LOCK_FILE.exists())

    # I5: corrupt or unparsable lock -> fail closed, never silently reclaimed
    def test_i5_fail_closed_on_unreadable_lock(self):
        root = make_root(self.base, "corrupt")
        (root / "control" / ".orchestrator.lock").write_text("{not json", encoding="utf-8")
        m = load_orchestrator(root)
        with self.assertRaises(RuntimeError):
            m.acquire_lock()

        root2 = make_root(self.base, "corrupt2")
        (root2 / "control" / ".orchestrator.lock").write_text(
            json.dumps({"started_at": "no-pid-field"}), encoding="utf-8")
        m2 = load_orchestrator(root2)
        with self.assertRaises(RuntimeError):
            m2.acquire_lock()

    # I6: the launcher must stay root-scoped (regression guard for the global-scan bug)
    def test_i6_launcher_is_root_scoped(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        self.assertNotIn("Get-CimInstance", text)
        self.assertNotIn("Win32_Process", text)
        self.assertIn(".orchestrator.lock", text)
        self.assertIn("acquire_lock", text)


if __name__ == "__main__":
    unittest.main()
