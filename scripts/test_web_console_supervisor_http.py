"""P8 HTTP tests for the Console Supervisor-turn observability surface.

Exercises the bounded read routes (turn list / detail / usage), the honest
configuration document and its queue mutation through the real
supervisor_control.py subcommand, adversarial isolation cases, and restart
persistence — all over real HTTP against a disposable Runtime fixture.
"""
from __future__ import annotations

import http.client
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
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_server as wcs
import web_console_supervisor as wsup
import supervisor_control as sc

REPO_SCRIPTS = REPO / "scripts"


def real_runtime_files(root: Path) -> None:
    """One disposable Runtime fixture with the real control plane helper."""
    scripts = root / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    for name in ("supervisor_control.py", "executor_claim.py",
                 "executor_fence.py", "executor_completion.py",
                 "resume_human_review.py"):
        shutil.copyfile(REPO_SCRIPTS / name, scripts / name)
    (root / "control").mkdir(exist_ok=True)
    (root / "control" / "CODEX_SUPERVISOR_RUNTIME.md").write_text(
        "# Fixture supervisor rules\n", encoding="utf-8")
    (root / "logs").mkdir(exist_ok=True)
    (root / "handoff" / "completion_ledger").mkdir(parents=True, exist_ok=True)


def arm_project(root: Path, project_id: str = "proj-alpha") -> None:
    (root / "control" / "ACTIVE_PROJECT.json").write_text(json.dumps(
        {"schema_version": 1, "project_id": project_id,
         "project_root": f"projects/{project_id}"}), encoding="utf-8")
    project = root / "projects" / project_id
    project.mkdir(parents=True, exist_ok=True)
    (project / "project_state.json").write_text(json.dumps({
        "schema_version": 1, "project_id": project_id,
        "status": "SUPERVISOR_TURN", "current_task": None,
        "next_message_id": 700500, "decision_history": [],
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (project / "RESEARCH_STATE.md").write_text("# Memory\n",
                                               encoding="utf-8")


def commit_record_turn(root: Path, *, decision_reason: str,
                       usage=None, model="gpt-5.6-sol",
                       effort="high", status="COMPLETE") -> str:
    """Drive one real Supervisor turn (no model call) to mint a record."""
    turn = sc.begin_supervisor_turn(root, "proj-alpha")
    state_path = (root / "projects" / "proj-alpha" / "project_state.json")
    state = json.loads(state_path.read_text(encoding="utf-8"))
    entry = {"decision": "CONTINUE", "reason": decision_reason}
    state["decision_history"].append(entry)
    state["last_supervisor_decision"] = dict(entry)
    state["status"] = status
    state["current_task"] = None
    state_path.write_text(json.dumps(state, ensure_ascii=False,
                                     indent=2) + "\n", encoding="utf-8")
    if usage is None:
        usage = {"reported": False, "input_tokens": None,
                 "output_tokens": None, "total_tokens": None, "source": None,
                 "note": "the Codex environment did not report token usage "
                         "for this turn"}
    sc.finish_supervisor_turn(root, turn, processed=True, observation={
        "model": model, "reasoning_effort": effort, "elapsed_seconds": 12.5,
        "usage": usage,
        "context_manifest": {
            "project_state": {"path": "projects/proj-alpha/project_state.json",
                              "truncated": False},
            "research_state": {"path": "RESEARCH_STATE.md",
                               "truncated": False},
        }})
    # Restore the pre-decision status so further fixture turns can commit;
    # last_supervisor_decision stays as the durable history it is.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["status"] = "SUPERVISOR_TURN"
    state_path.write_text(json.dumps(state, ensure_ascii=False,
                                     indent=2) + "\n", encoding="utf-8")
    return turn["turn_id"]


REPORTED_USAGE = {"reported": True, "input_tokens": 900, "output_tokens": 150,
                  "total_tokens": 1050, "source": "codex_output_token_usage",
                  "note": None}


class SupervisorHttpFixture:
    """One console server + one real-control-plane Runtime fixture."""

    def __init__(self, base: Path):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.runtime = base / "runtime-alpha"
        real_runtime_files(self.runtime)
        arm_project(self.runtime)
        self._start_server()

    def _start_server(self):
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=15.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    def restart(self):
        self.server.shutdown()
        self.thread.join(timeout=10)
        self._start_server()

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=10)

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str, path: str, body=None, raw_body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        try:
            headers = {}
            payload = raw_body
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

    def add_runtime(self, root=None, label="Alpha contrôlé") -> dict:
        status, payload, _ = self.request(
            "POST", "/api/runtimes",
            body={"root": str(root or self.runtime), "label": label})
        if status != 201:
            raise AssertionError(f"fixture add failed: {status} {payload!r}")
        return payload["runtime"]


class SupervisorRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsup-http-")
        self.fixture = SupervisorHttpFixture(Path(self.temp.name))
        self.entry = self.fixture.add_runtime()
        self.runtime_id = self.entry["id"]

    def tearDown(self):
        self.fixture.stop()
        self.temp.cleanup()

    # -- turn list / detail / usage -------------------------------------------

    def test_turns_empty_state_is_honest(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["supervisor_turns"]["turns"], [])
        self.assertTrue(payload["supervisor_turns"]["honesty"])

    def test_turn_list_pagination_and_projection(self):
        for index in range(5):
            commit_record_turn(
                self.fixture.runtime,
                decision_reason=f"turn {index} · 阶段",
                usage=REPORTED_USAGE if index % 2 else None)
        pages = []
        for offset in (0, 2, 4):
            status, payload, _ = self.fixture.request(
                "GET",
                f"/api/runtimes/{self.runtime_id}/supervisor/turns"
                f"?limit=2&offset={offset}")
            self.assertEqual(status, 200)
            pages.append(payload["supervisor_turns"])
        view = pages[0]
        self.assertEqual(view["totals"]["total_records"], 5)
        self.assertEqual(view["totals"]["offset"], 0)
        all_turns = [turn for page in pages for turn in page["turns"]]
        # Several fixture turns commit within the same second, so their
        # relative order is deterministic but not meaningful; the pages must
        # instead cover every record exactly once.
        self.assertEqual(
            sorted(turn["decision"]["summary"] for turn in all_turns),
            sorted(f"turn {index} · 阶段" for index in range(5)))
        self.assertEqual(
            sum(1 for turn in all_turns if turn["usage"]["reported"]), 2)
        for turn in all_turns:
            self.assertEqual(turn["supervisor_config"]["model"],
                             "gpt-5.6-sol")
            self.assertEqual(turn["receipt"]["integrity"],
                             "RECEIPT_VERIFIED")
            self.assertNotIn("context_manifest", turn)

    def test_turn_detail_carries_manifest_and_linkage_absent_here(self):
        turn_id = commit_record_turn(self.fixture.runtime,
                                     decision_reason="detail probe · café")
        status, payload, _ = self.fixture.request(
            "GET",
            f"/api/runtimes/{self.runtime_id}/supervisor/turns/{turn_id}")
        self.assertEqual(status, 200)
        detail = payload["supervisor_turn"]["turn"]
        self.assertEqual(detail["turn_id"], turn_id)
        self.assertIn("project_state", detail["context_manifest"])
        self.assertEqual(detail["decision"]["summary"], "detail probe · café")
        self.assertIsNone(detail["dispatch_linkage"])
        self.assertEqual(detail["outcome"]["committed"], True)

    def test_usage_summary_sums_only_reported(self):
        commit_record_turn(self.fixture.runtime, decision_reason="a",
                           usage=REPORTED_USAGE)
        commit_record_turn(self.fixture.runtime, decision_reason="b")
        commit_record_turn(self.fixture.runtime, decision_reason="c",
                           usage=REPORTED_USAGE)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/usage")
        self.assertEqual(status, 200)
        summary = payload["supervisor_usage"]["usage"]
        self.assertEqual(summary["turns_total"], 3)
        self.assertEqual(summary["turns_with_reported_usage"], 2)
        self.assertEqual(summary["totals"]["total_tokens"], 2100)
        self.assertFalse(summary["zcode_usage"]["reported"])
        self.assertIn("not reported", json.dumps(summary).lower())

    def test_turn_detail_not_found_and_hostile_ids(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns/"
                   "supervisor-turn-does-not-exist")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "TURN_NOT_FOUND")
        for hostile in ("..%2F..%2FTO_ZCODE.md", "%2e%2e"):
            status, payload, _ = self.fixture.request(
                "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns/"
                       f"{hostile}")
            self.assertEqual(status, 404, hostile)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns/"
                   "bad%2Ejson")
        self.assertIn(status, (400, 404))

    def test_query_validation_fails_closed(self):
        for query in ("limit=0", "limit=101", "limit=abc", "offset=-1"):
            status, payload, _ = self.fixture.request(
                "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns?"
                       f"{query}")
            self.assertEqual(status, 400, query)
            self.assertEqual(payload["error"]["code"], "TURNS_QUERY_INVALID")

    def test_malformed_record_is_surfaced_not_silent(self):
        commit_record_turn(self.fixture.runtime, decision_reason="good")
        broken = (self.fixture.runtime / "control" / "supervisor_turns"
                  / "broken.json")
        broken.write_text("{definitely not json", encoding="utf-8")
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns")
        self.assertEqual(status, 200)
        view = payload["supervisor_turns"]
        self.assertEqual(view["totals"]["total_records"], 1)
        self.assertEqual(view["totals"]["total_unusable"], 1)

    # -- configuration ----------------------------------------------------------

    def test_config_get_absent_then_queued_then_active(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/config")
        self.assertEqual(status, 200)
        config = payload["supervisor_config"]
        self.assertFalse(config["active"]["configured"])
        self.assertIsNone(config["pending"])

        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_id}/supervisor/config",
            body={"model": "gpt-6", "reasoning_effort": "HIGH"})
        self.assertEqual(status, 200, payload)
        result = payload["supervisor_config"]
        self.assertTrue(result["queued"])
        self.assertFalse(result["turn_in_flight"])

        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/config")
        config = payload["supervisor_config"]
        self.assertFalse(config["active"]["configured"])
        self.assertEqual(config["pending"]["model"], "gpt-6")
        self.assertEqual(config["pending"]["applies"],
                         "next eligible Supervisor turn")

        # The next eligible turn boundary consumes the queue (real helper).
        turn = sc.begin_supervisor_turn(self.fixture.runtime, "proj-alpha")
        state_path = (self.fixture.runtime / "projects" / "proj-alpha"
                      / "project_state.json")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        entry = {"decision": "CONTINUE", "reason": "consume queued config"}
        state["decision_history"].append(entry)
        state["last_supervisor_decision"] = dict(entry)
        state["status"] = "COMPLETE"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sc.finish_supervisor_turn(self.fixture.runtime, turn, processed=True)

        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/config")
        config = payload["supervisor_config"]
        self.assertTrue(config["active"]["configured"])
        self.assertEqual(config["active"]["model"], "gpt-6")
        self.assertIsNone(config["pending"])

    def test_config_post_validation_matrix(self):
        bad_bodies = [
            {"model": "gpt-6"},
            {"reasoning_effort": "HIGH"},
            {"model": "gpt-6", "reasoning_effort": "ULTRA"},
            {"model": "", "reasoning_effort": "HIGH"},
            {"model": "gpt-6", "reasoning_effort": "HIGH", "extra": None},
            {"model": 6, "reasoning_effort": "HIGH"},
        ]
        for body in bad_bodies:
            status, payload, _ = self.fixture.request(
                "POST", f"/api/runtimes/{self.runtime_id}/supervisor/config",
                body=body)
            self.assertEqual(status, 400, body)
            self.assertEqual(payload["error"]["code"],
                             "CONTROL_INVALID_PAYLOAD", body)

    def test_config_post_refused_on_stop(self):
        (self.fixture.runtime / "control" / "STOP").write_bytes(b"STOP")
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_id}/supervisor/config",
            body={"model": "gpt-6", "reasoning_effort": "HIGH"})
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "CONTROL_ACTION_REFUSED")

    def test_config_post_oversized_body(self):
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_id}/supervisor/config",
            raw_body=b'{"model":"' + b"x" * (64 * 1024 + 1) + b'"}')
        self.assertEqual(status, 413)

    # -- isolation and surface hygiene -------------------------------------------

    def test_second_runtime_is_isolated(self):
        beta = self.fixture.base / "runtime-beta"
        real_runtime_files(beta)
        arm_project(beta, "proj-beta")
        beta_entry = self.fixture.add_runtime(root=beta, label="Beta")
        commit_record_turn(self.fixture.runtime, decision_reason="alpha only")
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{beta_entry['id']}/supervisor/turns")
        self.assertEqual(status, 200)
        self.assertEqual(payload["supervisor_turns"]["turns"], [])
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{beta_entry['id']}/supervisor/usage")
        self.assertEqual(payload["supervisor_usage"]["usage"]
                         ["turns_total"], 0)
        status, alpha_usage, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/usage")
        self.assertEqual(alpha_usage["supervisor_usage"]["usage"]
                         ["turns_total"], 1)

    def test_unknown_runtime_id_is_404(self):
        status, payload, _ = self.fixture.request(
            "GET", "/api/runtimes/0000000000000000/supervisor/turns")
        self.assertEqual(status, 404)

    def test_method_allowlist(self):
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_id}/supervisor/turns",
            body={})
        self.assertEqual(status, 405)
        status, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_id}/supervisor/usage",
            body={})
        self.assertEqual(status, 405)
        status, payload, _ = self.fixture.request(
            "DELETE", f"/api/runtimes/{self.runtime_id}/supervisor/config")
        self.assertEqual(status, 405)

    def test_restart_persistence(self):
        commit_record_turn(self.fixture.runtime, decision_reason="before restart",
                           usage=REPORTED_USAGE)
        self.fixture.request(
            "POST", f"/api/runtimes/{self.runtime_id}/supervisor/config",
            body={"model": "gpt-6", "reasoning_effort": "LOW"})
        self.fixture.restart()
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/turns")
        self.assertEqual(status, 200)
        self.assertEqual(payload["supervisor_turns"]["totals"]
                         ["total_records"], 1)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/supervisor/config")
        config = payload["supervisor_config"]
        self.assertEqual(config["pending"]["reasoning_effort"], "LOW")


if __name__ == "__main__":
    unittest.main()
