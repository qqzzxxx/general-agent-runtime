"""P9 Runtime Core tests for the additive status observability keys.

P9 extends `current_status` with two additive, bounded keys that the
Console's deterministic alert layer consumes:

- `supervisor_turn_inflight`: {turn_id, started_at} when a Supervisor turn
  is between begin and finish;
- `active_task_completion`: {status, committed_at} for the active identity's
  completion ledger entry.

The test pins additive-only evolution: every legacy key keeps its shape, a
bare tree reports null, malformed inflight data is never surfaced, and the
CLI JSON output carries the keys.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import supervisor_control as sc


PROJECT = "status-p9-fixture"
IDENTITY = {"MESSAGE_ID": 700501, "TASK_ID": "T-1", "STAGE_ID": "s1",
            "ATTEMPT": 1, "NONCE": "nonce-700501"}
COMPLETION_PROTOCOL_VERSION = 1


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")


class StatusP9Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="status-p9-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.control = self.root / "control"
        self.project = self.root / "projects" / PROJECT
        self._write_active_project()
        self.state = {
            "schema_version": 1, "project_id": PROJECT,
            "status": "WAITING_EXECUTOR", "current_task": dict(IDENTITY),
            "next_message_id": 700502, "decision_history": [],
        }
        write_json(self.project / "project_state.json", self.state)
        self.runtime = {
            "schema_version": 2, "status": "RUNNING",
            "authorized_dispatch": dict(IDENTITY),
            "retired_message_ids": [],
            "last_consumed_message_id": 700119,
        }
        write_json(self.control / "orchestrator_runtime.json", self.runtime)

    def _write_active_project(self):
        write_json(self.control / "ACTIVE_PROJECT.json", {
            "schema_version": 1, "project_id": PROJECT,
            "project_root": f"projects/{PROJECT}",
        })

    def test_bare_tree_reports_null_observability_keys(self):
        root = self.root
        (self.control / "orchestrator_runtime.json").unlink()
        (self.control / "ACTIVE_PROJECT.json").unlink()
        (self.project / "project_state.json").unlink()
        status = sc.current_status(root)
        self.assertEqual(status["schema_version"], 1)
        self.assertIsNone(status["supervisor_turn_inflight"])
        self.assertIsNone(status["active_task_completion"])
        for legacy in ("PROJECT_ID", "runtime_status", "project_status",
                       "pause", "active_task", "last_authorized_dispatch",
                       "active_task_claimed", "active_task_claim_recorded",
                       "active_task_completion_status", "active_task_retired",
                       "pending_interventions", "last_consumed_message_id",
                       "human_review", "stop"):
            self.assertIn(legacy, status, legacy)

    def test_inflight_supervisor_turn_is_surfaced_bounded(self):
        write_json(self.control / "supervisor_control.json", {
            "schema_version": 1, "revision": 3, "intervention_generation": 0,
            "pause": {"status": "RUNNING", "requested_at": None,
                      "mode": None, "resumed_at": None},
            "inflight_supervisor_turn": {
                "turn_id": "supervisor-turn-abc123",
                "started_at": "2026-09-12T15:00:00+00:00",
                "revision": 2,
                "supervisor_config": {"source": "fixed_policy"},
                "intervention_ids": [],
            },
        })
        status = sc.current_status(self.root)
        self.assertEqual(status["supervisor_turn_inflight"], {
            "turn_id": "supervisor-turn-abc123",
            "started_at": "2026-09-12T15:00:00+00:00",
        })

    def test_malformed_inflight_turn_is_never_surfaced(self):
        for bad in ("a string", 7, [], {"turn_id": 5, "started_at": None},
                    {"turn_id": "x"}):
            write_json(self.control / "supervisor_control.json", {
                "schema_version": 1, "revision": 1,
                "intervention_generation": 0,
                "pause": {"status": "RUNNING", "requested_at": None,
                          "mode": None, "resumed_at": None},
                "inflight_supervisor_turn": bad,
            })
            status = sc.current_status(self.root)
            self.assertIsNone(status["supervisor_turn_inflight"], bad)

    def test_completion_ledger_entry_is_surfaced_with_commit_time(self):
        entry = {
            "COMPLETION_PROTOCOL_VERSION": COMPLETION_PROTOCOL_VERSION,
            "STATUS": "COMPLETION_COMMITTED",
            "MESSAGE_ID": IDENTITY["MESSAGE_ID"],
            "TASK_ID": IDENTITY["TASK_ID"],
            "STAGE_ID": IDENTITY["STAGE_ID"],
            "ATTEMPT": IDENTITY["ATTEMPT"],
            "NONCE": IDENTITY["NONCE"],
            "PROJECT_ID": PROJECT,
            "CLAIM_DIR": "handoff/executor_claims/x.claim",
            "CLAIM_IDENTITY_SHA256": "0" * 64,
            "RECEIPT_SHA256": "1" * 64,
            "BRIEF_SHA256": None,
            "STAGING_MANIFEST_SHA256": None,
            "COMMITTED_AT": "2026-09-12T14:59:00+00:00",
            "CONSUMED_AT": None,
            "SEALED_AT": None,
            "CONSUMED_ARCHIVE": None,
            "RECEIPT": {},
        }
        write_json(self.root / "handoff" / "completion_ledger"
                   / f"completion-{IDENTITY['MESSAGE_ID']}-abc.json", entry)
        status = sc.current_status(self.root)
        self.assertEqual(status["active_task_completion"], {
            "status": "COMPLETION_COMMITTED",
            "committed_at": "2026-09-12T14:59:00+00:00",
        })
        # The legacy flat key keeps its exact meaning.
        self.assertEqual(status["active_task_completion_status"],
                         "COMPLETION_COMMITTED")

    def test_unmatched_completion_is_not_surfaced(self):
        entry = {
            "COMPLETION_PROTOCOL_VERSION": COMPLETION_PROTOCOL_VERSION,
            "STATUS": "COMPLETION_COMMITTED",
            "MESSAGE_ID": 700999,
            "TASK_ID": "OTHER", "STAGE_ID": "other", "ATTEMPT": 1,
            "NONCE": "other-nonce", "PROJECT_ID": PROJECT,
            "CLAIM_DIR": None, "CLAIM_IDENTITY_SHA256": None,
            "RECEIPT_SHA256": None, "BRIEF_SHA256": None,
            "STAGING_MANIFEST_SHA256": None,
            "COMMITTED_AT": "2026-09-12T14:59:00+00:00",
            "CONSUMED_AT": None, "SEALED_AT": None,
            "CONSUMED_ARCHIVE": None, "RECEIPT": {},
        }
        write_json(self.root / "handoff" / "completion_ledger"
                   / "completion-700999-abc.json", entry)
        status = sc.current_status(self.root)
        self.assertIsNone(status["active_task_completion"])
        self.assertIsNone(status["active_task_completion_status"])

    def test_cli_status_json_carries_the_new_keys(self):
        completed = subprocess.run(
            [sys.executable, str(SCRIPTS / "supervisor_control.py"),
             "--root", str(self.root), "status", "--json"],
            capture_output=True, timeout=60)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(completed.stdout.decode("utf-8"))
        self.assertIn("supervisor_turn_inflight", document)
        self.assertIn("active_task_completion", document)
        self.assertIsNone(document["supervisor_turn_inflight"])
        self.assertIsNone(document["active_task_completion"])


if __name__ == "__main__":
    unittest.main()
