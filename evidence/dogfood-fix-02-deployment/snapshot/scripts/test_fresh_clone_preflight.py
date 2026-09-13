"""Fresh-clone preflight regression (PROJECT_GOAL.md section 6, items 1-17).

A true fresh clone contains only git-tracked files: control\\project_state.json,
root RESEARCH_STATE.md and ZCODE_LAST_PROCESSED.txt are live-state files that never
ship (.gitignore), and START_PROJECT creates the isolated project instead of them.
These tests build exactly that fixture (no legacy/bootstrap root files) and prove:

  * a fresh isolated clone passes read-only preflight (items 1-9);
  * legacy-form and v2 last-processed pointers stay accepted while a malformed
    existing pointer fails closed, via the canonical
    executor_claim.read_last_processed semantics (items 10-12);
  * the isolated FV status contract, including IN_PROGRESS, is intact
    (item 13 supplement; the full status matrix lives in test_preflight_fv_status.py);
  * malformed ACTIVE_PROJECT.json and missing/invalid project-local
    project_state.json still fail closed (items 14-15);
  * legacy single-project requirements - including its bootstrap artifacts - are
    preserved verbatim (item 16);
  * every run is proven read-only by a full-tree snapshot comparison, ignoring
    only interpreter bytecode caches (item 17).

Deterministic and sandboxed: everything runs in tempfile fixtures; the live
Runtime is never a test target.
"""
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
PY = sys.executable
PROJECT_ID = "fresh-clone-smoke-001"
# The legacy/bootstrap root files a fresh clone must NOT need (all .gitignore'd).
LEGACY_ROOT_FILES = ("control/project_state.json", "RESEARCH_STATE.md",
                     "ZCODE_LAST_PROCESSED.txt")
V2_POINTER = ("MESSAGE_ID=41\nTASK_ID=some-task\nSTAGE_ID=some-stage\n"
              "ATTEMPT=1\nNONCE=nonce-41\n")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path) -> dict:
    """Full-tree snapshot for the read-only proof. __pycache__/*.pyc are
    interpreter bytecode caches, not writes by preflight, and are ignored."""
    out = {}
    for p in sorted(root.rglob("*")):
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        out[p.relative_to(root).as_posix()] = "DIR" if p.is_dir() else sha256_file(p)
    return out


def build_fresh_clone_fixture(root: Path, *, last_processed=None,
                              project_state="valid", pointer="valid",
                              fv_status="NOT_STARTED") -> None:
    """Model `git clone` + `START_PROJECT.ps1` output for PROJECT_ID.

    last_processed: None (file absent) | any literal file content
    project_state:  "valid" | "missing" | "invalid"
    pointer:        "valid" | "malformed" | "absent" (legacy mode)
    """
    control = root / "control"
    control.mkdir(parents=True)
    (root / "scripts").mkdir()
    shutil.copy(CANDIDATE / "orchestrator.py", root / "orchestrator.py")
    shutil.copy(CANDIDATE / "control" / "CODEX_SUPERVISOR_RUNTIME.md",
                control / "CODEX_SUPERVISOR_RUNTIME.md")
    shutil.copy(CANDIDATE / "scripts" / "preflight.py",
                root / "scripts" / "preflight.py")
    shutil.copy(CANDIDATE / "scripts" / "executor_claim.py",
                root / "scripts" / "executor_claim.py")
    shutil.copytree(CANDIDATE / "profiles", root / "profiles")

    proot = root / "projects" / PROJECT_ID
    for sub in ("workspace", "evidence", "reports"):
        (proot / sub).mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    goal_doc = (f"<!-- PROJECT_ID: {PROJECT_ID} | PROJECT_TYPE: SOFTWARE_ENGINEERING | "
                f"CREATED_AT: {now} -->\n\n# Smoke goal\n\n"
                f"Fresh-clone preflight must pass read-only.\n")
    goal_path = proot / "PROJECT_GOAL.md"
    goal_path.write_text(goal_doc, encoding="utf-8")
    (proot / "RESEARCH_STATE.md").write_text(
        "# Project Memory\n\nNo Supervisor decisions yet.\n", encoding="utf-8")

    if project_state != "missing":
        state = {
            "schema_version": 4,
            "project_id": PROJECT_ID,
            "project_type": "SOFTWARE_ENGINEERING",
            "profile": "SOFTWARE_ENGINEERING",
            "status": "SUPERVISOR_TURN",
            "phase": "SOFTWARE_ENGINEERING",
            "created_at": now,
            "started_at": now,
            "updated_at": now,
            "goal_file": "PROJECT_GOAL.md",
            "goal_anchor": {
                "schema_version": 1,
                "goal_path": "PROJECT_GOAL.md",
                "goal_sha256": sha256_file(goal_path),
                "bound_at": now,
                "provenance": "bootstrap",
            },
            "current_task": None,
            "next_message_id": 700001,
            "final_verification": {
                "policy_version": 1,
                "required": True,
                "status": fv_status,
                "policy_id": "SOFTWARE_ENGINEERING_FV_V1",
                "critical_claims": [],
                "claims_hash": None,
                "verification_message_id": None,
                "verification_receipt_sha256": None,
                "verified_at": None,
            },
            "last_supervisor_decision": None,
            "decision_history": [],
            "infrastructure_status": "READY",
            "deadline_at": None,
            "blocked_reason": None,
            "notes": ["bootstrapped by START_PROJECT (SOFTWARE_ENGINEERING)"],
        }
        text = (json.dumps(state, ensure_ascii=False, indent=2) + "\n"
                if project_state == "valid" else "{not json")
        (proot / "project_state.json").write_text(text, encoding="utf-8")

    if pointer == "valid":
        (control / "ACTIVE_PROJECT.json").write_text(json.dumps(
            {"schema_version": 1, "project_id": PROJECT_ID,
             "project_root": f"projects/{PROJECT_ID}"}), encoding="utf-8")
    elif pointer == "malformed":
        (control / "ACTIVE_PROJECT.json").write_text('{"schema_version": 1',
                                                     encoding="utf-8")

    if last_processed is not None:
        (root / "ZCODE_LAST_PROCESSED.txt").write_text(last_processed,
                                                       encoding="utf-8")

    # The defect's premise: a fresh clone demonstrably lacks the legacy artifacts.
    for rel in LEGACY_ROOT_FILES:
        if rel == "ZCODE_LAST_PROCESSED.txt" and last_processed is not None:
            continue
        assert not (root / rel).exists(), f"fixture must omit {rel}"


def build_legacy_fixture(root: Path, *, with_last_processed=True) -> None:
    """Legacy single-project layout (no ACTIVE_PROJECT pointer)."""
    control = root / "control"
    control.mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "reports").mkdir()
    (root / "orchestrator.py").write_text("# stub\n", encoding="utf-8")
    (root / "RESEARCH_STATE.md").write_text("legacy memory\n", encoding="utf-8")
    (control / "CODEX_SUPERVISOR_RUNTIME.md").write_text("contract\n", encoding="utf-8")
    (control / "CROSS_BORDER_GOAL.md").write_text("goal\n", encoding="utf-8")
    (control / "project_state.json").write_text(json.dumps({
        "schema_version": 3, "status": "COMPLETE",
        "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V1",
        "infrastructure_status": "READY", "amazon_authorized": True}),
        encoding="utf-8")
    (root / "reports" / "INFRASTRUCTURE_STATUS.md").write_text(
        "INFRASTRUCTURE_STATUS: READY\n", encoding="utf-8")
    shutil.copy(CANDIDATE / "scripts" / "preflight.py",
                root / "scripts" / "preflight.py")
    shutil.copy(CANDIDATE / "scripts" / "executor_claim.py",
                root / "scripts" / "executor_claim.py")
    if with_last_processed:
        (root / "ZCODE_LAST_PROCESSED.txt").write_text("700008\n", encoding="utf-8")


def error_lines(result) -> list:
    return [line[2:] for line in result.stdout.splitlines() if line.startswith("- ")]


class FreshClonePreflightTests(unittest.TestCase):
    def run_in_fixture(self, builder=build_fresh_clone_fixture, **kwargs):
        temp = tempfile.TemporaryDirectory(prefix="fresh-clone-preflight-test-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        builder(root, **kwargs)
        before = snapshot(root)
        r = subprocess.run([PY, str(root / "scripts" / "preflight.py")],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=root)
        self.assertEqual(snapshot(root), before,
                         "preflight must stay read-only (full fixture tree)")
        return r, root

    # Items 1-8: a fresh isolated Runtime/project without any of the three
    # legacy/bootstrap root files passes preflight.
    def test_fresh_clone_isolated_preflight_ok(self):
        r, root = self.run_in_fixture()
        for rel in LEGACY_ROOT_FILES:
            self.assertFalse((root / rel).exists(), rel)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT: OK", r.stdout)
        self.assertIn("Mode: isolated", r.stdout)
        self.assertIn(f"Project ID: {PROJECT_ID}", r.stdout)
        self.assertIn("Project type/profile: SOFTWARE_ENGINEERING", r.stdout)
        self.assertIn("Final verification: required=True status=NOT_STARTED "
                      "policy=SOFTWARE_ENGINEERING_FV_V1", r.stdout)
        self.assertEqual(error_lines(r), [], r.stdout)

    # Item 9: missing ZCODE_LAST_PROCESSED.txt means "nothing processed yet".
    def test_missing_last_processed_is_nothing_processed(self):
        r, root = self.run_in_fixture()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((root / "ZCODE_LAST_PROCESSED.txt").exists())
        self.assertNotIn("ZCODE_LAST_PROCESSED", r.stdout)

    # Item 10: a valid legacy-form pointer stays accepted.
    def test_legacy_last_processed_form_accepted(self):
        r, _ = self.run_in_fixture(last_processed="42\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT: OK", r.stdout)

    # Item 11: a valid v2 last-processed identity stays accepted.
    def test_v2_last_processed_identity_accepted(self):
        r, _ = self.run_in_fixture(last_processed=V2_POINTER)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT: OK", r.stdout)

    # Item 12: a malformed existing pointer still fails closed.
    def test_malformed_last_processed_fails_closed(self):
        r, _ = self.run_in_fixture(last_processed="LAST=PROCESSED\n")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("ZCODE_LAST_PROCESSED.txt is malformed", r.stdout)

    # Item 13 (supplement): the Runtime-owned in-flight FV marker IN_PROGRESS
    # stays accepted on a fresh-clone-shaped fixture.
    def test_in_progress_fv_status_accepted(self):
        r, _ = self.run_in_fixture(fv_status="IN_PROGRESS")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Final verification: required=True status=IN_PROGRESS "
                      "policy=SOFTWARE_ENGINEERING_FV_V1", r.stdout)

    # Item 14: a malformed ACTIVE_PROJECT.json still fails closed - with the
    # pointer error as the only problem, proving no legacy fallback/noise.
    def test_malformed_active_project_pointer_fails_closed(self):
        r, root = self.run_in_fixture(pointer="malformed")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("ACTIVE_PROJECT pointer is missing or invalid", r.stdout)
        self.assertEqual(error_lines(r),
                         [line for line in error_lines(r) if "ACTIVE_PROJECT" in line])
        self.assertEqual(len(error_lines(r)), 1, r.stdout)
        # fail closed, no auto-repair of the pointer file
        self.assertEqual((root / "control" / "ACTIVE_PROJECT.json").read_text(
            encoding="utf-8"), '{"schema_version": 1')

    # Item 15a: missing project-local project_state.json still fails closed.
    def test_missing_project_local_state_fails_closed(self):
        r, _ = self.run_in_fixture(project_state="missing")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("active project has no project_state.json", r.stdout)

    # Item 15b: invalid project-local project_state.json still fails closed.
    def test_invalid_project_local_state_fails_closed(self):
        r, _ = self.run_in_fixture(project_state="invalid")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("project_state is missing or invalid", r.stdout)

    # Item 16a: legacy single-project behavior stays covered and unchanged.
    def test_legacy_single_project_mode_still_ok(self):
        r, _ = self.run_in_fixture(builder=build_legacy_fixture)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("PREFLIGHT: OK", r.stdout)
        self.assertIn("Mode: legacy", r.stdout)
        self.assertIn("Phase: CROSS_BORDER_ECOMMERCE_AGENT_V1", r.stdout)
        self.assertIn("Project status: COMPLETE", r.stdout)

    # Item 16b: legacy mode still requires its bootstrap artifacts - the
    # legacy contract was narrowed to isolated mode only, not removed.
    def test_legacy_mode_still_requires_bootstrap_pointer(self):
        r, _ = self.run_in_fixture(builder=build_legacy_fixture,
                                   with_last_processed=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
        self.assertIn("Mode: legacy", r.stdout)
        self.assertIn("missing:", r.stdout)
        self.assertGreaterEqual(
            sum("ZCODE_LAST_PROCESSED" in line for line in error_lines(r)), 1)


if __name__ == "__main__":
    unittest.main()
