"""End-to-end Human Decision regression over the Console HTTP surface.

Unlike `test_web_console_control_http.py`, which stubs the supported helper,
this suite drives the REAL `scripts/resume_human_review.py` against a fixture
Runtime built around the REAL `orchestrator.py` module code (exec'd rooted at
the fixture, the same pattern as `test_resume_human_review.py`). The whole
audited chain is exercised over the wire:

    prepare -> receipt_id + receipt_sha256 -> apply -> HUMAN_REVIEW_RESUMED

including the canonical receipt self-hash, the project-state hash binding,
the quiescence proof, the receipt archive, and the authoritative
HUMAN_REVIEW -> SUPERVISOR_TURN commit with the re-armed durable event.

It also pins the 2026-09-15 Web Console incident: an Apply request that does
not carry the prepare-returned receipt_sha256 must fail closed with
CONTROL_INVALID_PAYLOAD before any Runtime mutation.

No GUI, no external network, and the fixture Runtime is disposable.
"""
from __future__ import annotations

import hashlib
import json
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

import web_console_server as wcs

PROJECT_ID = "e2e-project"

STUB_CONTROL = """\
import json, sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    argv = [item for item in sys.argv[1:]]
    if "--root" in argv:
        argv = argv[argv.index("--root") + 2:]
    subcommand = argv[0] if argv else None
    if subcommand not in ("status", "interventions"):
        return 2
    if subcommand == "interventions":
        print("[]")
        return 0
    status = json.loads(
        (root / "stub_status.json").read_text(encoding="utf-8"))
    status["stop"] = (root / "control" / "STOP").exists()
    # Reflect the REAL authoritative state files, so a committed
    # HUMAN_REVIEW -> SUPERVISOR_TURN transition (and a started Runtime)
    # is observable through the read-only status probe, exactly like the
    # production supervisor_control status command reports them.
    active_path = root / "control" / "ACTIVE_PROJECT.json"
    if active_path.is_file():
        try:
            active = json.loads(
                active_path.read_text(encoding="utf-8-sig"))
            status["PROJECT_ID"] = active.get("project_id")
            state_path = (root / str(active.get("project_root") or "")
                          / "project_state.json")
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            status["project_status"] = state.get("status")
        except (OSError, ValueError):
            pass
    runtime_path = root / "control" / "orchestrator_runtime.json"
    if runtime_path.is_file():
        try:
            runtime = json.loads(
                runtime_path.read_text(encoding="utf-8-sig"))
            status["runtime_status"] = runtime.get("status")
        except (OSError, ValueError):
            pass
    status["human_review"] = (
        (root / "control" / "HUMAN_REVIEW").exists()
        or status.get("project_status") == "HUMAN_REVIEW")
    print(json.dumps(status, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""

# The fixture orchestrator module executes the REAL orchestrator source in its
# own namespace, so `__file__`-derived roots (ROOT, RUNTIME_SCRIPTS, helpers
# resolved next to the module) all resolve inside the fixture Runtime while
# the code under test stays the real product code.
FIXTURE_ORCHESTRATOR = """\
import sys
from pathlib import Path

_REPO = Path({repo!r})
for _entry in (str(_REPO), str(_REPO / "scripts")):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)
exec((_REPO / "orchestrator.py").read_text(encoding="utf-8"), globals())
"""


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def build_e2e_runtime(root: Path) -> None:
    """A disposable never-dispatched Runtime frozen in HUMAN_REVIEW."""
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    for name in ("resume_human_review.py", "executor_claim.py",
                 "executor_completion.py"):
        shutil.copyfile(REPO / "scripts" / name, scripts / name)
    _write(scripts / "supervisor_control.py", STUB_CONTROL)
    _write(root / "orchestrator.py",
           FIXTURE_ORCHESTRATOR.format(repo=str(REPO)))

    _write(root / "control" / "ACTIVE_PROJECT.json", json.dumps(
        {"schema_version": 1, "project_id": PROJECT_ID,
         "project_root": f"projects/{PROJECT_ID}"}, indent=2) + "\n")
    _write(root / "control" / "CODEX_SUPERVISOR_RUNTIME.md",
           "# Fixture supervisor rules\n")

    project = root / "projects" / PROJECT_ID
    goal = "# Fixture goal\n"
    goal_sha256 = hashlib.sha256(goal.encode("utf-8")).hexdigest()
    _write(project / "PROJECT_GOAL.md", goal)
    _write(project / "RESEARCH_STATE.md", "# Fixture memory\n")
    stamp = "2026-09-15T12:00:00+00:00"
    _write(project / "project_state.json", json.dumps({
        "schema_version": 4,
        "project_id": PROJECT_ID,
        "project_type": "GENERAL",
        "profile": "GENERAL",
        "status": "HUMAN_REVIEW",
        "phase": "GENERAL",
        "updated_at": stamp,
        "goal_file": "PROJECT_GOAL.md",
        "goal_anchor": {
            "schema_version": 1,
            "goal_path": "PROJECT_GOAL.md",
            "goal_sha256": goal_sha256,
            "bound_at": stamp,
            "provenance": "bootstrap",
        },
        "current_task": None,
        "next_message_id": 700100,
        "final_verification": {
            "policy_version": 1,
            "required": True,
            "status": "NOT_STARTED",
            "policy_id": "GENERAL_FV_V1",
            "critical_claims": [],
            "claims_hash": None,
            "verification_message_id": None,
            "verification_receipt_sha256": None,
            "verified_at": None,
        },
        "last_supervisor_decision": "HUMAN_REVIEW — fixture",
        "decision_history": [],
        "infrastructure_status": "READY",
        "deadline_at": None,
        "blocked_reason": "fixture human decision required",
        "notes": [],
    }, ensure_ascii=False, indent=2) + "\n")

    # Pre-first-dispatch shape: no Executor task was ever published, and the
    # bootstrap compatibility consumed pointer is the only Executor history.
    _write(root / "control" / "orchestrator_runtime.json", json.dumps({
        "schema_version": 2,
        "status": "HUMAN_REVIEW",
        "last_consumed_message_id": 602,
        "last_consumed_nonce": None,
        "last_consumed_brief_sha256": None,
        "last_dispatched_message_id": None,
        "last_dispatched_nonce": None,
        "authorized_dispatch": None,
        "retired_message_ids": [],
        "claim_protocol_required_from_message_id": 700001,
        "consecutive_codex_without_executor": 0,
    }, indent=2) + "\n")
    _write(root / "ZCODE_LAST_PROCESSED.txt", "602\n")

    (root / "logs").mkdir(exist_ok=True)
    (root / "handoff").mkdir(exist_ok=True)
    (root / "reports").mkdir(exist_ok=True)
    shutil.copyfile(REPO / "STOP_AGENT_SYSTEM.ps1",
                    root / "STOP_AGENT_SYSTEM.ps1")
    _write(root / "stub_status.json", json.dumps({
        "schema_version": 1,
        "PROJECT_ID": PROJECT_ID,
        "runtime_status": "HUMAN_REVIEW",
        "project_status": "HUMAN_REVIEW",
        "pause": {"status": "RUNNING", "requested_at": None, "mode": None,
                  "resumed_at": None},
        "active_task": None, "last_authorized_dispatch": None,
        "active_task_claimed": False, "active_task_claim_recorded": False,
        "active_task_completion_status": None, "active_task_retired": False,
        "pending_interventions": 0, "last_consumed_message_id": None,
        "human_review": True, "stop": False,
    }, indent=2) + "\n")


class HumanReviewEndToEndTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="hr-e2e-")
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
                  "label": "E2E human review — 端到端"})
        if status != 201:
            raise AssertionError(f"fixture registration failed: {status} "
                                 f"{payload!r}")
        self.runtime = payload["runtime"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

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

    def test_prepare_then_apply_resumes_the_runtime_end_to_end(self):
        status, controls = self.request(
            "GET", self.runtime_url("/controls"))
        self.assertEqual(status, 200)
        self.assertEqual(controls["controls"]["human_review"]["prepared"], [])

        decision = {
            "decision_content": (
                "额度已恢复，请继续当前科研项目。检查上一任务的状态和已有产物；"
                "不要重复已完成工作。保持 PROJECT_GOAL 和研究方向不变。"),
            "constraints_verbatim": [
                "保持 PROJECT_GOAL 不变。",
                "不要重复已完成工作。",
            ],
        }
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/prepare"),
            body=decision)
        self.assertEqual(status, 200)
        prepared = payload["human_decision"]
        self.assertRegex(prepared["receipt_id"],
                         r"^human-decision-[0-9a-f]{32}$")
        self.assertRegex(prepared["receipt_sha256"], r"^[0-9a-f]{64}$")

        # The stored receipt is exactly what prepare returned, and the
        # controls document mirrors it so Apply never has to re-derive it.
        stored = json.loads(
            (self.data_dir / "human_review" / self.runtime["id"]
             / f"{prepared['receipt_id']}.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["receipt_sha256"],
                         prepared["receipt_sha256"])
        self.assertEqual(stored["human_decision"], decision)
        status, controls = self.request(
            "GET", self.runtime_url("/controls"))
        self.assertEqual(status, 200)
        self.assertEqual(controls["controls"]["human_review"]["prepared"],
                         [{"receipt_id": prepared["receipt_id"],
                           "receipt_sha256": prepared["receipt_sha256"],
                           "submitted_at": stored["submitted_at"]}])

        # The project-state hash binding is the real current hash of the
        # fixture project_state.json.
        state_bytes = (self.runtime_root / "projects" / PROJECT_ID
                       / "project_state.json").read_bytes()
        self.assertEqual(prepared["previous_project_state_sha256"],
                         hashlib.sha256(state_bytes).hexdigest())

        # Incident regression: applying without the receipt hash fails closed
        # before the helper runs and mutates nothing.
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/apply"),
            body={"receipt_id": prepared["receipt_id"]})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "CONTROL_INVALID_PAYLOAD")
        self.assertIn("receipt_sha256", payload["error"]["detail"]["reason"])
        self.assertEqual(self.project_state()["status"], "HUMAN_REVIEW")
        self.assertTrue(
            (self.data_dir / "human_review" / self.runtime["id"]
             / f"{prepared['receipt_id']}.json").is_file())

        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/apply"),
            body={"receipt_id": prepared["receipt_id"],
                  "receipt_sha256": prepared["receipt_sha256"]})
        self.assertEqual(status, 200)
        result = payload["human_decision_result"]
        self.assertEqual(result["event"], "HUMAN_REVIEW_RESUMED_TO_SUPERVISOR")
        self.assertEqual(result["previous_status"], "HUMAN_REVIEW")
        self.assertEqual(result["status"], "SUPERVISOR_TURN")
        self.assertEqual(result["receipt_id"], prepared["receipt_id"])
        self.assertEqual(result["receipt_sha256"], prepared["receipt_sha256"])
        self.assertIs(result["executor_dispatched"], False)

        # Authoritative Runtime state really committed the transition.
        state = self.project_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        resume = state["human_review_resume"]
        self.assertEqual(resume["status"], "PENDING_SUPERVISOR_REVIEW")
        self.assertEqual(resume["receipt_id"], prepared["receipt_id"])
        self.assertEqual(resume["receipt_sha256"],
                         prepared["receipt_sha256"])
        self.assertEqual(resume["previous_status"], "HUMAN_REVIEW")
        self.assertEqual(state["current_task"], None)
        self.assertEqual(state["next_message_id"], 700100)
        archive = (self.runtime_root / "projects" / PROJECT_ID
                   / "human_decisions" / "archive"
                   / f"{prepared['receipt_id']}-"
                     f"{prepared['receipt_sha256']}.json")
        self.assertTrue(archive.is_file())

        runtime = self.runtime_state()
        self.assertEqual(runtime["status"], "SUPERVISOR_TURN")
        self.assertEqual(runtime["last_human_decision_receipt_id"],
                         prepared["receipt_id"])
        event = runtime["pending_supervisor_event"]
        self.assertEqual(event["reason"], "HUMAN_DECISION_RESUME")
        self.assertEqual(event["rearmed_by_receipt"], prepared["receipt_id"])
        self.assertFalse(event["retry_exhausted"])

        # The consumed receipt is gone from the Console listing and a replayed
        # confirmation cannot reapply.
        status, controls = self.request(
            "GET", self.runtime_url("/controls"))
        self.assertEqual(status, 200)
        self.assertEqual(controls["controls"]["human_review"]["prepared"], [])
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/apply"),
            body={"receipt_id": prepared["receipt_id"],
                  "receipt_sha256": prepared["receipt_sha256"]})
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"],
                         "HUMAN_DECISION_RECEIPT_UNKNOWN")


if __name__ == "__main__":
    unittest.main()
