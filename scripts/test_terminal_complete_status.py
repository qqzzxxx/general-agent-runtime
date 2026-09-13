"""Dogfood Fix 02: actual status producer/CLI/HTTP, plus adversarial interpretation.

All mutated authority is synthetic and disposable; no live project is rerun.
"""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.request import urlopen

import supervisor_control as control
import web_console_state as interpreter
import web_console_server as server
from test_web_console_state import complete_doc, status_doc, with_active_task

REPO = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class TerminalStatusTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="terminal-status-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.state_path = self.root / "projects/demo/project_state.json"
        self.runtime_path = self.root / "control/orchestrator_runtime.json"
        write(self.root / "control/ACTIVE_PROJECT.json", {
            "schema_version": 1, "project_id": "demo", "project_root": "projects/demo"})
        claims = [{"claim_id": "C1", "claim": "deliverable is readable",
                   "claim_type": "DELIVERABLE_INTEGRITY", "decision_impact": "HIGH",
                   "evidence_pointers": ["workspace/result.md"],
                   "verification_standard": "read exact deliverable bytes"}]
        claims = [dict(claims[0], claim_id=f"C{i}", claim=f"deliverable {i} is readable")
                  for i in range(1, 4)]
        import importlib.util
        spec = importlib.util.spec_from_file_location("terminal_test_gate", REPO / "orchestrator.py")
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        claim_hash = gate.canonical_claims_hash(claims)
        self.state = {
            "schema_version": 4, "project_id": "demo", "profile": "GENERAL",
            "status": "COMPLETE", "current_task": None,
            "final_verification": {"required": True, "policy_version": 1,
                "policy_id": "GENERAL_FV_V1", "status": "PASS", "critical_claims": claims,
                "claims_hash": claim_hash, "verification_message_id": 700104,
                "verification_receipt_sha256": "a" * 64},
        }
        self.runtime = {
            "schema_version": 2, "status": "COMPLETE", "last_consumed_message_id": 700104,
            "last_consumed_brief_sha256": "a" * 64,
            "last_final_verification_message_id": 700104,
            "last_final_verification_receipt_sha256": "a" * 64,
            "last_final_verification_claims_hash": claim_hash,
            "last_final_verification_mechanical_pass": True,
            "last_final_verification_overall_status": "PASS",
            "authorized_dispatch": {"MESSAGE_ID": 700104, "TASK_ID": "FV",
                "STAGE_ID": "verify", "ATTEMPT": 1, "NONCE": "b" * 24,
                "IS_FINAL_VERIFICATION": True},
        }
        self.assertEqual(gate.final_verification_terminal_check(self.runtime, self.state), (True, "PASS"))
        self.save()

    def save(self):
        write(self.state_path, self.state)
        write(self.runtime_path, self.runtime)

    def interpret(self):
        return interpreter.interpret_status(control.current_status(self.root))

    def assert_complete(self, result):
        self.assertEqual(result["state"]["family"], "COMPLETE", result["honesty"])
        self.assertIsNone(result["state"]["worker"]["who"])
        self.assertFalse(result["state"]["user_action_required"])
        self.assertFalse(result["protocol"]["available"])
        self.assertFalse(result["health"]["current_authorization"]["available"])
        self.assertEqual(result["milestones"], [])
        self.assertIsNone(result["next_expected"]["message_id"])
        self.assertEqual(result["terminal_completion"], {"available": True,
            "last_consumed_message_id": 700104, "final_verification_status": "PASS"})
        self.assertNotIn("STATE_UNAVAILABLE", json.dumps(result))

    def test_producer_preserves_historical_dispatch_without_inventing_activity(self):
        before = {p: p.read_bytes() for p in self.root.rglob("*.json")}
        status = control.current_status(self.root)
        self.assertEqual(status["runtime_status"], "COMPLETE")
        self.assertEqual(status["last_authorized_dispatch"]["MESSAGE_ID"], 700104)
        self.assertIsNone(status["active_task"])
        self.assert_complete(interpreter.interpret_status(status))
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob("*.json")})

    def test_real_installed_cli_and_cockpit_http(self):
        (self.root / "scripts").mkdir()
        for p in (REPO / "scripts").glob("*.py"):
            if not p.name.startswith("test_"):
                shutil.copy2(p, self.root / "scripts" / p.name)
        shutil.copy2(REPO / "orchestrator.py", self.root / "orchestrator.py")
        shutil.copytree(REPO / "profiles", self.root / "profiles")
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.root.rglob("*") if p.is_file()}
        proc = subprocess.run([sys.executable, "-B", str(self.root / "scripts/supervisor_control.py"),
            "--root", str(self.root), "status", "--json"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assert_complete(interpreter.interpret_status(json.loads(proc.stdout)))
        http = server.WebConsoleServer.create(host="127.0.0.1", port=0,
            runtime_root=self.root, console_root=REPO, data_dir=self.root / "console-data")
        http.config.registry.add(str(self.root), "Terminal fixture")
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        try:
            base = "http://127.0.0.1:" + str(http.server_address[1])
            with urlopen(base + "/api/runtimes") as response:
                entries = json.load(response)
            runtime_id = entries["runtimes"][0]["id"]
            with urlopen(base + f"/api/runtimes/{runtime_id}/cockpit") as response:
                self.assert_complete(json.load(response)["cockpit"]["interpretation"])
        finally:
            http.shutdown()
            http.server_close()
            thread.join(5)
        self.assertTrue(all(hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in before.items()))

    def test_source_contradictions_hidden_by_old_projection_fail_closed(self):
        cases = [("status", "WAITING_EXECUTOR"), ("current_task", {}),
                 ("current_task", "corrupt"), ("project_id", "other"),
                 ("final_verification", []), ("final_verification", {"required": "true"})]
        original = copy.deepcopy(self.state)
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.state = dict(original, **{field: value})
                self.save()
                self.assertEqual(self.interpret()["state"]["family"], "STATE_UNAVAILABLE")
        self.state = dict(original)
        del self.state["current_task"]
        self.save()
        self.assertEqual(self.interpret()["state"]["family"], "STATE_UNAVAILABLE")

    def test_corrupt_final_verification_binding_fails_closed(self):
        for field, value in [("last_final_verification_mechanical_pass", False),
                             ("last_final_verification_overall_status", "FAIL"),
                             ("last_final_verification_claims_hash", "wrong"),
                             ("last_consumed_message_id", 700105),
                             ("last_consumed_brief_sha256", "wrong")]:
            with self.subTest(field=field):
                old = self.runtime[field]
                self.runtime[field] = value
                self.save()
                self.assertEqual(self.interpret()["state"]["family"], "STATE_UNAVAILABLE")
                self.runtime[field] = old

    def test_malformed_source_json_is_rejected(self):
        for path in (self.state_path, self.runtime_path, self.root / "control/supervisor_control.json"):
            for raw in ('[]', 'null', '{', '{"status":"RUNNING","status":"COMPLETE"}'):
                with self.subTest(path=path, raw=raw):
                    before = path.read_bytes() if path.exists() else None
                    path.write_text(raw)
                    with self.assertRaises(control.ControlError):
                        control.current_status(self.root)
                    if before is None:
                        path.unlink()
                    else:
                        path.write_bytes(before)

    def test_raw_malformed_inflight_cannot_be_hidden(self):
        for value in ({}, "corrupt", {"turn_id": "unfinished", "started_at": "now"}):
            write(self.root / "control/supervisor_control.json", {
                "schema_version": 1, "revision": 0, "intervention_generation": 0,
                "pause": {"status": "RUNNING"}, "inflight_supervisor_turn": value})
            self.assertEqual(self.interpret()["state"]["family"], "STATE_UNAVAILABLE")

    def test_legacy_ungated_completion_never_invents_fv_pass(self):
        self.state.pop("final_verification")
        self.runtime = {key: value for key, value in self.runtime.items()
                        if not key.startswith("last_final_verification_")}
        self.save()
        result = self.interpret()
        self.assertEqual(result["state"]["family"], "COMPLETE")
        self.assertIsNone(result["terminal_completion"]["final_verification_status"])

    def test_missing_or_malformed_project_verification_cannot_downgrade_gate(self):
        original = copy.deepcopy(self.state)
        for value in (None, {"required": False},
                      dict(original["final_verification"], verification_message_id="700104"),
                      dict(original["final_verification"], policy_version=True)):
            with self.subTest(value=value):
                self.state["final_verification"] = value
                self.save()
                self.assertEqual(self.interpret()["state"]["family"], "STATE_UNAVAILABLE")


class TerminalInterpreterTests(unittest.TestCase):
    def test_valid_complete_is_deterministic(self):
        doc = complete_doc(last_consumed_message_id=700104)
        first = interpreter.interpret_status(doc)
        self.assertEqual(first, interpreter.interpret_status(copy.deepcopy(doc)))
        self.assertEqual(first["state"]["family"], "COMPLETE")

    def test_unknown_malformed_missing_contradictory_terminal_facts(self):
        cases = [("runtime_status", "unknown"), ("runtime_status", None),
                 ("runtime_status", []), ("project_status", "WAITING_EXECUTOR"),
                 ("project_status", None), ("terminal_completion", None),
                 ("terminal_completion", {}), ("active_task", {}),
                 ("pending_interventions", 1), ("stop", True), ("human_review", True),
                 ("active_task_claimed", True), ("supervisor_turn_inflight", {}),
                 ("pause", {"status": "PAUSED"}),
                 ("pause", {"status": "RUNNING", "mode": []})]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                result = interpreter.interpret_status(complete_doc(**{field: value}))
                self.assertEqual(result["state"]["family"], "STATE_UNAVAILABLE")
                self.assertFalse(result["terminal_completion"]["available"])
        for field in ("active_task", "supervisor_turn_inflight", "terminal_completion", "runtime_status"):
            doc = complete_doc()
            del doc[field]
            self.assertEqual(interpreter.interpret_status(doc)["state"]["family"], "STATE_UNAVAILABLE")

    def test_nonterminal_running_paused_human_review_stop_preserved(self):
        cases = [(with_active_task(status_doc(), claimed=True, claim_recorded=True), "ZCODE_EXECUTING"),
                 (status_doc(runtime_status="PAUSED"), "PAUSED"),
                 (status_doc(human_review=True), "HUMAN_REVIEW"),
                 (status_doc(stop=True), "STOPPED")]
        for doc, family in cases:
            self.assertEqual(interpreter.interpret_status(doc)["state"]["family"], family)


if __name__ == "__main__":
    unittest.main()
