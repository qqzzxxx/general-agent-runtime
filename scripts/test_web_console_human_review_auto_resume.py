"""Gated automatic Runtime resume after a Human Decision Apply (over HTTP).

2026-09-16 dogfood incident: Apply correctly committed
HUMAN_REVIEW -> SUPERVISOR_TURN with the durable HUMAN_DECISION_RESUME
event, but nothing ever started the Orchestrator, so the operator had to
leave the Console and run START_AGENT_SYSTEM.ps1 by hand.

This suite drives the REAL `scripts/resume_human_review.py` and the REAL
gated auto-resume chain over the Console HTTP surface against a disposable
fixture Runtime (the same real-orchestrator-module pattern as
`test_web_console_human_review_e2e.py`, whose fixture builder it reuses):

    prepare -> apply (receipt exactly-once) -> gate proof -> the Runtime's
    own START_AGENT_SYSTEM.ps1 entry point -> STARTED_VERIFIED

It pins the safety semantics: no auto-start without proving the committed
SUPERVISOR_TURN + persisted HUMAN_DECISION_RESUME event, no start under
control/STOP or a live scheduler owner, exactly one start under concurrent
double-Apply, dead/stale locks tolerated without the Console ever editing
authoritative state, and "applied but not resumed" surviving as an honest
projection when the start fails.

No GUI, no external network; the fixture START script stands in for the
real orchestrator loop the same way `test_web_console_setup_http.py` does.
"""
from __future__ import annotations

import json
import os
import shutil
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

import runtime_lifecycle
import web_console_server as wcs
from test_web_console_human_review_e2e import PROJECT_ID, build_e2e_runtime

DECISION = {
    "decision_content": (
        "额度已恢复，请继续当前科研项目。检查上一任务的状态和已有产物；"
        "不要重复已完成工作。保持 PROJECT_GOAL 和研究方向不变。"),
    "constraints_verbatim": [
        "保持 PROJECT_GOAL 不变。",
        "不要重复已完成工作。",
    ],
}

# Bounded start entry point: records its invocation (one line per start) and
# flips the runtime document to RUNNING so the verification poll observes the
# same effect the real START_AGENT_SYSTEM.ps1 (via orchestrator.py) produces.
STUB_START_PS1 = """\
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root
Add-Content -LiteralPath (Join-Path $Root "start-marker.txt") -Value "invoked"
$runtimePath = Join-Path $Root "control\\orchestrator_runtime.json"
$runtime = Get-Content -LiteralPath $runtimePath -Raw | ConvertFrom-Json
$runtime.status = "RUNNING"
$runtime | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $runtimePath
exit 0
"""


class AutoResumeEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hr-auto-")
        base = Path(self._tmp.name)
        self.runtime_root = base / "runtime"
        build_e2e_runtime(self.runtime_root)
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True)
        shutil.copyfile(REPO / "web_console" / "index.html",
                        self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_root,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=30.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()
        status, payload = self.request(
            "POST", "/api/runtimes",
            body={"root": str(self.runtime_root),
                  "label": "Auto resume — 自动恢复"})
        if status != 201:
            raise AssertionError(f"fixture registration failed: {status} "
                                 f"{payload!r}")
        self.runtime = payload["runtime"]
        self.write_start_script()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    # -- fixture helpers -----------------------------------------------------

    def write_start_script(self):
        (self.runtime_root / "START_AGENT_SYSTEM.ps1").write_text(
            STUB_START_PS1, encoding="utf-8")

    def start_marker_count(self) -> int:
        marker = self.runtime_root / "start-marker.txt"
        if not marker.is_file():
            return 0
        return len([line for line in
                    marker.read_text(encoding="utf-8").splitlines()
                    if line.strip()])

    def prepare(self):
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/prepare"),
            body=DECISION)
        self.assertEqual(status, 200, payload)
        return payload["human_decision"]

    def apply(self, decision):
        return self.request(
            "POST", self.runtime_url("/controls/human-review/apply"),
            body={"receipt_id": decision["receipt_id"],
                  "receipt_sha256": decision["receipt_sha256"]})

    def request(self, method, path, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server_port,
                                          timeout=60)
        try:
            data = None
            headers = {}
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
        finally:
            conn.close()

    @property
    def server_port(self) -> int:
        return self.server.server_address[1]

    def runtime_url(self, suffix: str) -> str:
        return f"/api/runtimes/{self.runtime['id']}{suffix}"

    def project_state(self) -> dict:
        return json.loads(
            (self.runtime_root / "projects" / PROJECT_ID
             / "project_state.json").read_text(encoding="utf-8-sig"))

    def runtime_state(self) -> dict:
        return json.loads(
            (self.runtime_root / "control" / "orchestrator_runtime.json")
            .read_text(encoding="utf-8-sig"))

    def controls(self) -> dict:
        status, payload = self.request(
            "GET", self.runtime_url("/controls"))
        self.assertEqual(status, 200, payload)
        return payload["controls"]

    def write_live_owner_lock(self):
        """A lock naming THIS live process, exactly as orchestrator.py
        acquire_lock writes it (pid + process birth identity)."""
        identity = runtime_lifecycle.process_identity(os.getpid())
        lock = self.runtime_root / "control" / ".orchestrator.lock"
        lock.write_text(json.dumps({
            "pid": os.getpid(),
            "started_at": "2026-09-16T00:00:00+00:00",
            "owner_id": "fixture-owner",
            "process_identity": identity,
        }), encoding="utf-8")
        return lock

    # -- tests ---------------------------------------------------------------

    def test_apply_auto_resumes_the_runtime_end_to_end(self):
        decision = self.prepare()
        status, payload = self.apply(decision)
        self.assertEqual(status, 200, payload)
        result = payload["human_decision_result"]
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        self.assertEqual(result["status"], "SUPERVISOR_TURN")
        auto = payload["auto_resume"]
        self.assertTrue(auto["attempted"])
        self.assertTrue(auto["start_invoked"])
        self.assertEqual(auto["verification"], "STARTED_VERIFIED")
        self.assertEqual(auto["runtime_status"], "RUNNING")
        self.assertEqual(auto["start_entry_point"], "START_AGENT_SYSTEM.ps1")
        self.assertIsNone(auto["error"])
        self.assertTrue(auto["gate"]["allowed"])
        self.assertEqual(auto["gate"]["receipt_id"], decision["receipt_id"])
        self.assertEqual(auto["gate"]["project_id"], PROJECT_ID)

        # Exactly one start invocation happened, through the documented
        # START entry point.
        self.assertEqual(self.start_marker_count(), 1)

        # The authoritative project state committed the transition before
        # any start, and the runtime document keeps the re-armed durable
        # event for the (simulated) scheduler.
        state = self.project_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(state["human_review_resume"]["status"],
                         "PENDING_SUPERVISOR_REVIEW")
        runtime = self.runtime_state()
        self.assertEqual(runtime["status"], "RUNNING")
        self.assertEqual(
            runtime["pending_supervisor_event"]["reason"],
            "HUMAN_DECISION_RESUME")
        self.assertEqual(
            runtime["pending_supervisor_event"]["rearmed_by_receipt"],
            decision["receipt_id"])

        # The controls projection reports the applied decision honestly.
        applied = self.controls()["human_review"]["applied_resume"]
        self.assertTrue(applied["event_pending"])
        self.assertEqual(applied["receipt_id"], decision["receipt_id"])
        self.assertEqual(applied["project_id"], PROJECT_ID)

    def test_apply_refuses_to_start_when_start_entry_point_fails(self):
        # No START_AGENT_SYSTEM.ps1: the start fails, but the decision stays
        # applied and the failure is surfaced as "applied, not resumed"
        # instead of masquerading as an Apply failure.
        (self.runtime_root / "START_AGENT_SYSTEM.ps1").unlink(missing_ok=True)
        decision = self.prepare()
        status, payload = self.apply(decision)
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["human_decision_result"]["status"],
                         "SUPERVISOR_TURN")
        auto = payload["auto_resume"]
        self.assertTrue(auto["attempted"])
        self.assertFalse(auto["start_invoked"])
        self.assertIsNone(auto["verification"])
        self.assertEqual(auto["error"]["code"],
                         "SETUP_START_SCRIPT_UNAVAILABLE")
        self.assertEqual(self.project_state()["status"], "SUPERVISOR_TURN")
        self.assertEqual(self.start_marker_count(), 0)

        applied = self.controls()["human_review"]["applied_resume"]
        self.assertTrue(applied["event_pending"])
        self.assertEqual(applied["receipt_id"], decision["receipt_id"])

        # The gated one-click resume finishes the job once the entry point
        # exists again; the decision was never re-prepared or re-applied.
        self.write_start_script()
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/resume"), body={})
        self.assertEqual(status, 200, payload)
        resumed = payload["resume_runtime"]
        self.assertTrue(resumed["start_invoked"])
        self.assertEqual(resumed["verification"], "STARTED_VERIFIED")
        self.assertEqual(self.start_marker_count(), 1)

    def test_resume_endpoint_requires_the_persisted_event(self):
        # Still in HUMAN_REVIEW: nothing applied, so nothing may start.
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/resume"), body={})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["error"]["code"],
                         "HUMAN_REVIEW_RESUME_BLOCKED")
        codes = {item["code"]
                 for item in payload["error"]["detail"]["blocked"]}
        self.assertIn("PROJECT_NOT_SUPERVISOR_TURN", codes)
        self.assertIn("HUMAN_REVIEW_ACTIVE", codes)
        self.assertIn("RESUME_EVENT_MISSING", codes)
        self.assertEqual(self.start_marker_count(), 0)

    def test_stop_blocks_the_resume_endpoint(self):
        decision = self.prepare()
        status, payload = self.apply(decision)
        self.assertEqual(status, 200)
        self.assertTrue(payload["auto_resume"]["start_invoked"])
        marker_count = self.start_marker_count()

        # STOP appears after the apply (the decision is still applied);
        # the gate must refuse any further automatic start.
        (self.runtime_root / "control" / "STOP").write_text("stop\n",
                                                            encoding="utf-8")
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/resume"), body={})
        self.assertEqual(status, 409, payload)
        codes = [item["code"]
                 for item in payload["error"]["detail"]["blocked"]]
        self.assertIn("STOP_PRESENT", codes)
        self.assertEqual(self.start_marker_count(), marker_count)

    def test_live_owner_blocks_a_second_start(self):
        decision = self.prepare()
        status, payload = self.apply(decision)
        self.assertEqual(status, 200)
        # The fixture START script does not claim scheduler ownership, so
        # write the lock a live orchestrator would own (this process).
        lock = self.write_live_owner_lock()
        try:
            status, payload = self.request(
                "POST", self.runtime_url("/controls/human-review/resume"),
                body={})
            self.assertEqual(status, 409, payload)
            codes = [item["code"]
                     for item in payload["error"]["detail"]["blocked"]]
            self.assertIn("LIVE_OWNER", codes)
            self.assertEqual(self.start_marker_count(), 1)
        finally:
            lock.unlink(missing_ok=True)
        # Without a live owner the same gated endpoint starts the Runtime.
        # The verification poll can observe RUNNING (left by the first
        # simulated start) before the stub script records its invocation,
        # so wait briefly for the marker instead of racing it.
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/resume"),
            body={})
        self.assertEqual(status, 200, payload)
        self.assertEqual(payload["resume_runtime"]["verification"],
                         "STARTED_VERIFIED")
        deadline = time.monotonic() + 30
        while self.start_marker_count() < 2 and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(self.start_marker_count(), 2)

    def test_stale_dead_lock_is_tolerated_and_never_edited(self):
        # A dead owner's lock is reclaimed by orchestrator.py's own
        # acquire_lock (FIX-F04 dead-owner reclaim) inside the apply helper
        # and released when the helper exits — the existing Runtime recovery
        # semantics. The Console tolerates the stale lock at gate time,
        # starts through the documented entry point, and never edits
        # authoritative state itself.
        lock = self.runtime_root / "control" / ".orchestrator.lock"
        lock.write_text(json.dumps({
            "pid": 4000000,
            "started_at": "2026-09-16T00:00:00+00:00",
            "owner_id": "dead-owner",
            "process_identity": 12345,
        }), encoding="utf-8")
        decision = self.prepare()
        status, payload = self.apply(decision)
        self.assertEqual(status, 200, payload)
        auto = payload["auto_resume"]
        self.assertTrue(auto["attempted"])
        self.assertTrue(auto["start_invoked"])
        self.assertEqual(auto["verification"], "STARTED_VERIFIED")
        self.assertEqual(self.start_marker_count(), 1)
        # Reclaimed and released by the Runtime helper itself (FIX-F04),
        # not by Console code: no lock survives the apply.
        self.assertFalse(lock.is_file())

    def test_concurrent_double_apply_starts_exactly_once(self):
        decision = self.prepare()
        outcomes = []

        def worker():
            outcomes.append(self.apply(decision))

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        statuses = sorted(status for status, _ in outcomes)
        self.assertEqual(statuses, [200, 409], outcomes)
        # The Human Decision receipt stays exactly-once: one archived
        # receipt, one committed transition, and exactly one start.
        winner = next(payload for status, payload in outcomes
                      if status == 200)
        self.assertEqual(
            winner["human_decision_result"]["event"],
            "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        self.assertTrue(winner["auto_resume"]["start_invoked"])
        archive_dir = (self.runtime_root / "projects" / PROJECT_ID
                       / "human_decisions" / "archive")
        self.assertEqual(len(list(archive_dir.glob("*.json"))), 1)
        self.assertEqual(self.project_state()["status"], "SUPERVISOR_TURN")
        deadline = time.monotonic() + 30
        while self.start_marker_count() < 1 and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertEqual(self.start_marker_count(), 1)

    def test_manual_setup_start_path_stays_independent(self):
        # The pre-existing manual start route keeps its fail-closed
        # readiness contract (no wizard draft -> not ready) and is not
        # unlocked by the auto-resume work.
        status, payload = self.request(
            "POST", self.runtime_url("/setup/start"), body={})
        self.assertEqual(status, 409, payload)
        self.assertEqual(payload["error"]["code"], "SETUP_NOT_READY")
        self.assertEqual(self.start_marker_count(), 0)


if __name__ == "__main__":
    unittest.main()
