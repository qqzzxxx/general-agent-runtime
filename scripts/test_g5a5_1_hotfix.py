"""G5A.5.1 targeted regression: ACTIVE_PROJECT test-harness isolation + preflight (H1–H10).

Runs against the REAL candidate (which currently contains a live
ACTIVE_PROJECT.json pointer exists) plus temp-fixture sandboxes.
"""
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
PY = sys.executable
REAL_POINTER = CANDIDATE / "control" / "ACTIVE_PROJECT.json"


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def run(cmd, cwd=None):
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=cwd)


class G5A5_1HotfixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # §0 precondition: the real candidate pointer exists (this is the H1/H2 subject)
        cls.real_pointer_precondition = REAL_POINTER.exists()
        pointer = read_json(REAL_POINTER) or {}
        cls.real_state = read_json(CANDIDATE / pointer.get("project_root", "") /
                                   "project_state.json")
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g5a5_1-")
        self.root = Path(self.temp.name)
        (self.root / "control").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    # H1: the patched harness rebinds ACTIVE_PROJECT_FILE into the temp fixture root
    @unittest.skipUnless(REAL_POINTER.exists(), "clean runtime has no active project pointer")
    def test_h1_harness_rebinds_active_project_file(self):
        self.assertTrue(self.real_pointer_precondition,
                        "precondition: real candidate pointer exists")
        spec = importlib.util.spec_from_file_location(
            "orig_test_module", CANDIDATE / "scripts" / "test_orchestrator.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        harness = mod.OrchestratorMechanicalTests("__init__")  # no setUp; dummy instance
        harness._patch_root(self.root)  # body uses the module-global orchestrator `o`
        self.assertEqual(mod.o.ACTIVE_PROJECT_FILE,
                         self.root / "control" / "ACTIVE_PROJECT.json")
        self.assertNotEqual(mod.o.ACTIVE_PROJECT_FILE, REAL_POINTER)
        # and the module constant now differs from the harness view (the leak is closed)
        self.assertNotEqual(mod_o_unpatched_path(), mod.o.ACTIVE_PROJECT_FILE)

    # H2: the full original suite is green while the real pointer exists
    @unittest.skipUnless(REAL_POINTER.exists(), "clean runtime has no active project pointer")
    def test_h2_original_suite_green_with_real_pointer(self):
        self.assertTrue(self.real_pointer_precondition)
        r = run([PY, str(CANDIDATE / "scripts" / "test_orchestrator.py")])
        self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-2000:])
        self.assertIn("Ran 22 tests", r.stderr)
        self.assertIn("OK", r.stderr)

    # H3: temp root without pointer -> legacy fixture mode
    def test_h3_legacy_fixture_mode(self):
        spec = importlib.util.spec_from_file_location(
            "g51_h3", CANDIDATE / "orchestrator.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for attr, val in dict(ROOT=self.root, CONTROL=self.root / "control",
                              ACTIVE_PROJECT_FILE=self.root / "control" / "ACTIVE_PROJECT.json",
                              PROJECT_STATE=self.root / "control" / "project_state.json").items():
            setattr(m, attr, val)
        self.assertIsNone(m.activate_project_scope())  # legacy fixture mode
        self.assertEqual(m.PROJECT_STATE, self.root / "control" / "project_state.json")

    # H4: temp root WITH an isolated pointer -> reads the temp project, not the candidate
    def test_h4_isolated_fixture_pointer_reads_temp_project(self):
        pid = "temp-fixture-project"
        proot = self.root / "projects" / pid
        proot.mkdir(parents=True)
        (proot / "project_state.json").write_text(json.dumps(
            {"schema_version": 3, "status": "SUPERVISOR_TURN", "project": pid,
             "phase": "GENERAL_TEST"}), encoding="utf-8")
        self.m = None
        spec = importlib.util.spec_from_file_location(
            "g51_h4", CANDIDATE / "orchestrator.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        for attr, val in dict(ROOT=self.root, CONTROL=self.root / "control",
                              ACTIVE_PROJECT_FILE=self.root / "control" / "ACTIVE_PROJECT.json",
                              PROJECT_STATE=self.root / "control" / "project_state.json",
                              HANDOFF_ARCHIVE=self.root / "handoff" / "archive").items():
            setattr(m, attr, val)
        m.atomic_json(m.ACTIVE_PROJECT_FILE, {"schema_version": 1, "project_id": pid,
                                              "project_root": f"projects/{pid}"})
        active = m.activate_project_scope()
        self.assertEqual(active["project_id"], pid)
        state = m.read_project_state()
        self.assertEqual(state["project"], pid)  # temp project, never the candidate one

    # H5/H6/H7/H8: real candidate preflight
    @unittest.skipUnless(REAL_POINTER.exists(), "clean runtime has no active project pointer")
    def test_h5_h8_real_candidate_preflight(self):
        pointer = read_json(CANDIDATE / "control" / "ACTIVE_PROJECT.json")
        project_id = pointer["project_id"]
        state_path = CANDIDATE / pointer["project_root"] / "project_state.json"
        state = read_json(state_path)
        sha_before = sha(state_path)
        r = run([PY, str(CANDIDATE / "scripts" / "preflight.py")])
        sha_after = sha(state_path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Mode: isolated", r.stdout)
        self.assertIn(f"Project ID: {project_id}", r.stdout)
        self.assertIn(f"Project type/profile: {state['profile']}", r.stdout)
        self.assertIn(f"Phase: {state['phase']}", r.stdout)
        self.assertIn("Infrastructure status: READY", r.stdout)
        self.assertIn(f"Project status: {state['status']}", r.stdout)
        self.assertIn(f"policy={state['final_verification']['policy_id']}", r.stdout)
        self.assertIn(str(CANDIDATE), r.stdout)  # Runtime Root line
        self.assertEqual(sha_before, sha_after)  # H8: read-only

    # H9: invalid ACTIVE_PROJECT -> preflight fail closed
    def test_h9_invalid_pointer_fail_closed(self):
        fixture = Path(tempfile.mkdtemp(prefix="g5a5_1-invalid-"))
        try:
            control = fixture / "control"
            control.mkdir(parents=True)
            (fixture / "RESEARCH_STATE.md").write_text("m", encoding="utf-8")
            (fixture / "ZCODE_LAST_PROCESSED.txt").write_text("0\n", encoding="utf-8")
            (fixture / "orchestrator.py").write_text("# stub\n", encoding="utf-8")
            (fixture / "control" / "project_state.json").write_text("{}", encoding="utf-8")
            (fixture / "control" / "CODEX_SUPERVISOR_RUNTIME.md").write_text("x", encoding="utf-8")
            (control / "ACTIVE_PROJECT.json").write_text('{"schema_version": 1}', encoding="utf-8")
            r = run([PY, str(CANDIDATE / "scripts" / "preflight.py"), "--x"], cwd=fixture)
            # run the candidate preflight with the FIXTURE as its root by copying the script
            (fixture / "scripts").mkdir(exist_ok=True)
            shutil.copy(CANDIDATE / "scripts" / "preflight.py",
                        fixture / "scripts" / "preflight.py")
            r = run([PY, str(fixture / "scripts" / "preflight.py")], cwd=fixture)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("PREFLIGHT: BLOCKED", r.stdout)
            self.assertIn("ACTIVE_PROJECT", r.stdout)
            # pointer file untouched (fail closed, no auto-repair, no legacy fallback)
            self.assertEqual((control / "ACTIVE_PROJECT.json").read_text(encoding="utf-8"),
                             '{"schema_version": 1}')
        finally:
            shutil.rmtree(fixture, ignore_errors=True)

    # H10: legacy runtime without pointer -> legacy preflight behavior preserved
    def test_h10_legacy_preflight_preserved(self):
        fixture = Path(tempfile.mkdtemp(prefix="g5a5_1-legacy-"))
        try:
            control = fixture / "control"
            control.mkdir(parents=True)
            (fixture / "RESEARCH_STATE.md").write_text("m", encoding="utf-8")
            (fixture / "ZCODE_LAST_PROCESSED.txt").write_text("700008\n", encoding="utf-8")
            (fixture / "orchestrator.py").write_text("# stub\n", encoding="utf-8")
            (fixture / "control" / "CODEX_SUPERVISOR_RUNTIME.md").write_text("x", encoding="utf-8")
            (fixture / "control" / "project_state.json").write_text(json.dumps({
                "schema_version": 3, "status": "COMPLETE",
                "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V1",
                "infrastructure_status": "READY", "amazon_authorized": True}), encoding="utf-8")
            (fixture / "control" / "CROSS_BORDER_GOAL.md").write_text("g", encoding="utf-8")
            reports = fixture / "reports"
            reports.mkdir()
            (reports / "INFRASTRUCTURE_STATUS.md").write_text(
                "INFRASTRUCTURE_STATUS: READY\n", encoding="utf-8")
            (fixture / "scripts").mkdir(exist_ok=True)
            shutil.copy(CANDIDATE / "scripts" / "preflight.py",
                        fixture / "scripts" / "preflight_under_test.py")
            r = run([PY, str(fixture / "scripts" / "preflight_under_test.py")], cwd=fixture)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("Mode: legacy", r.stdout)
            self.assertIn("Phase: CROSS_BORDER_ECOMMERCE_AGENT_V1", r.stdout)
            self.assertIn("Project status: COMPLETE", r.stdout)
            self.assertNotIn("isolated", r.stdout.split("Mode:")[1].splitlines()[0])
        finally:
            shutil.rmtree(fixture, ignore_errors=True)


# helper used by H1 (path of the UNPATCHED module constant)
def mod_o_unpatched_path():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "orig_probe", CANDIDATE / "orchestrator.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.CONTROL / "ACTIVE_PROJECT.json"


if __name__ == "__main__":
    unittest.main()
