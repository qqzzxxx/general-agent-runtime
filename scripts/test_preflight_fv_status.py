"""Targeted regression: isolated-project preflight must accept the
Runtime's legitimate in-flight Final Verification status IN_PROGRESS while
still failing closed on unknown or malformed statuses.

Deterministic and sandboxed: every case runs scripts/preflight.py inside a
fresh temp runtime fixture; no test reads or depends on the live project's
mutable state.
"""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
PY = sys.executable


def build_fixture(root: Path, fv_status) -> Path:
    control = root / "control"
    control.mkdir(parents=True)
    (root / "RESEARCH_STATE.md").write_text("sandbox\n", encoding="utf-8")
    (root / "orchestrator.py").write_text("# stub\n", encoding="utf-8")
    (control / "CODEX_SUPERVISOR_RUNTIME.md").write_text("sandbox\n", encoding="utf-8")
    (control / "project_state.json").write_text("{}", encoding="utf-8")
    (root / "ZCODE_LAST_PROCESSED.txt").write_text("42\n", encoding="utf-8")
    pid = "fv-sandbox-001"
    proot = root / "projects" / pid
    proot.mkdir(parents=True)
    (control / "ACTIVE_PROJECT.json").write_text(json.dumps(
        {"schema_version": 1, "project_id": pid, "project_root": f"projects/{pid}"}),
        encoding="utf-8")
    profiles = root / "profiles" / "SOFTWARE_ENGINEERING"
    profiles.mkdir(parents=True)
    (profiles / "PROFILE.json").write_text(json.dumps(
        {"final_verification_policy_id": "SANDBOX_FV_V1"}), encoding="utf-8")
    (profiles / "FINAL_VERIFICATION_POLICY.json").write_text(json.dumps(
        {"policy_id": "SANDBOX_FV_V1"}), encoding="utf-8")
    state = {
        "schema_version": 4,
        "project_id": pid,
        "profile": "SOFTWARE_ENGINEERING",
        "status": "WAITING_EXECUTOR",
        "phase": "FINAL_VERIFICATION",
        "infrastructure_status": "READY",
        "final_verification": {
            "required": True,
            "status": fv_status,
            "policy_id": "SANDBOX_FV_V1",
            "policy_version": 1,
        },
        "human_review_resume": None,
        "human_decision_consumption_ledger": None,
    }
    (proot / "project_state.json").write_text(
        json.dumps(state, indent=2), encoding="utf-8")
    scripts = root / "scripts"
    scripts.mkdir()
    shutil.copy(CANDIDATE / "scripts" / "preflight.py", scripts / "preflight.py")
    return proot / "project_state.json"


class PreflightFinalVerificationStatusTests(unittest.TestCase):
    def run_preflight(self, fv_status):
        with tempfile.TemporaryDirectory(prefix="fv-preflight-test-") as tmp:
            root = Path(tmp)
            state_path = build_fixture(root, fv_status)
            before = state_path.read_bytes()
            r = subprocess.run([PY, str(root / "scripts" / "preflight.py")],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", cwd=root)
            self.assertEqual(state_path.read_bytes(), before, "preflight must stay read-only")
            return r

    # IN_PROGRESS is the Runtime-owned marker for an authorized
    # Final Verification task in flight; read-only preflight must accept it.
    def test_in_progress_final_verification_status_is_accepted(self):
        r = self.run_preflight("IN_PROGRESS")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT: OK", r.stdout)
        self.assertIn("Mode: isolated", r.stdout)
        self.assertIn("Final verification: required=True status=IN_PROGRESS "
                      "policy=SANDBOX_FV_V1", r.stdout)

    # Control: the same fixture with an already-allowed status stays accepted,
    # proving the fixture is otherwise valid (not passing for a wrong reason).
    def test_pending_status_control_remains_accepted(self):
        r = self.run_preflight("PENDING")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT: OK", r.stdout)

    def test_unknown_final_verification_status_still_rejected(self):
        r = self.run_preflight("SORT_OF_DONE")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("unexpected final_verification.status: 'SORT_OF_DONE'", r.stdout)

    def test_missing_final_verification_status_still_rejected(self):
        r = self.run_preflight(None)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("unexpected final_verification.status: None", r.stdout)

    def test_malformed_final_verification_status_still_rejected(self):
        r = self.run_preflight({"state": "IN_PROGRESS"})
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("unexpected final_verification.status:", r.stdout)


if __name__ == "__main__":
    unittest.main()
