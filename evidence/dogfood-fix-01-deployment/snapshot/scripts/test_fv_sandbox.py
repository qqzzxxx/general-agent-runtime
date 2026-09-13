"""FV-SANDBOX-ISOLATION-V1 regression tests.

Deterministic, Windows-aware tests for the Final Verification isolation
boundary. Every test operates on synthetic throwaway roots under the system
temp directory. The real development Runtime appears exactly once, as a
NON-WRITING rejection candidate for the path guard; no test ever writes to it.
"""

import _winapi  # noqa: F401  (junction creation on Windows)
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fv_sandbox as fvs  # noqa: E402

MODULE_PATH = SCRIPTS.parent / "orchestrator.py"
_spec = importlib.util.spec_from_file_location("orchestrator_fviso", MODULE_PATH)
o = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(o)

REAL_RUNTIME_ROOT = MODULE_PATH.resolve().parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_sentinel_live_runtime(root: Path) -> Path:
    """Disposable synthetic 'live' Runtime whose protected state must never be
    mutated by probes. Mirrors the real module-level path-global pattern."""
    (root / "scripts").mkdir(parents=True)
    (root / "control").mkdir(parents=True)
    (root / "scripts" / "runtime_module.py").write_text(
        "import json\n"
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parent.parent\n"
        "CONTROL = ROOT / 'control'\n"
        "PROJECT_STATE = CONTROL / 'project_state.json'\n"
        "def write_fixture_state(fixture):\n"
        "    PROJECT_STATE.parent.mkdir(parents=True, exist_ok=True)\n"
        "    PROJECT_STATE.write_text(json.dumps(fixture), encoding='utf-8')\n"
        "    return str(PROJECT_STATE)\n",
        encoding="utf-8",
    )
    protected = root / "control" / "project_state.json"
    protected.write_text(
        json.dumps({"role": "PROTECTED_SENTINEL_LIVE_STATE"}), encoding="utf-8")
    (root / "control" / "ACTIVE_PROJECT.json").write_text(
        json.dumps({"schema_version": 1, "project_id": "sentinel-p",
                    "project_root": "projects/sentinel-p"}), encoding="utf-8")
    (root / "orchestrator.py").write_text("# synthetic sentinel\n", encoding="utf-8")
    return protected


def probe_script(body: str) -> str:
    return body


class _TempRootCase(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory(prefix="fviso-tests-")
        self.base = Path(self._temp.name)
        self.live = self.base / "sentinel_live_runtime"
        self.protected = make_sentinel_live_runtime(self.live)
        self.addCleanup(self._temp.cleanup)

    def new_sandbox(self, **kwargs) -> fvs.FVSandbox:
        sandbox = fvs.FVSandbox.create(
            self.live, project_id="sentinel-p", label="t",
            parent=self.base / "sandbox_parent", **kwargs)
        self.addCleanup(shutil.rmtree, sandbox.root, ignore_errors=True)
        return sandbox


# ---------------------------------------------------------------------------
# 1. Sandbox creation + mechanical path guard (Windows-aware matrix)
# ---------------------------------------------------------------------------

class SandboxCreationTests(_TempRootCase):
    def test_01_sandbox_created_physically_outside_live_root(self):
        s = self.new_sandbox()
        self.assertFalse(fvs.resolves_within(s.canonical_root, s.canonical_runtime_root))
        self.assertFalse(fvs.resolves_within(s.canonical_runtime_root, s.canonical_root))
        identity = json.loads((s.root / "sandbox_identity.json").read_text(encoding="utf-8"))
        self.assertEqual(identity["kind"], "FV_SANDBOX_IDENTITY")
        self.assertTrue(identity["identity_sha256"])
        self.assertEqual(s.identity_sha256, identity["identity_sha256"])

    def test_02_nested_sandbox_rejected_at_creation(self):
        with self.assertRaises(RuntimeError):
            fvs.FVSandbox.create(self.live, label="nested",
                                 parent=self.live / "inside_live")
        self.assertFalse((self.live / "inside_live").exists())

    def test_03_sandbox_bound_path_accepted_and_canonicalized(self):
        s = self.new_sandbox()
        mixed_case = Path(str(s.root).upper()) / "control" / "state.json"
        resolved = s.validate_binding("OUT", mixed_case)
        self.assertTrue(fvs.resolves_within(resolved, s.canonical_root))
        self.assertEqual(resolved, fvs.canonical_path(s.root / "control" / "state.json"))

    def test_04_direct_live_path_rejected_before_any_write(self):
        s = self.new_sandbox()
        before = sha(self.protected)
        listing_before = sorted(p.name for p in (self.live / "control").iterdir())
        with self.assertRaises(fvs.SandboxEscapeError) as ctx:
            s.validate_binding("PROJECT_STATE", self.protected)
        self.assertEqual(ctx.exception.reason, "RESOLVES_INTO_LIVE_RUNTIME_AUTHORITY")
        self.assertEqual(sha(self.protected), before)
        self.assertEqual(sorted(p.name for p in (self.live / "control").iterdir()),
                         listing_before)

    def test_05_real_development_runtime_rejected_as_non_writing_candidate(self):
        s = self.new_sandbox()
        sentinel_candidate = REAL_RUNTIME_ROOT / "control" / "fviso_must_not_exist.sentinel"
        self.assertFalse(sentinel_candidate.exists())
        with self.assertRaises(fvs.SandboxEscapeError):
            s.validate_binding("REAL_LIVE", sentinel_candidate)
        self.assertFalse(sentinel_candidate.exists(), "guard must never write")

    def test_06_relative_traversal_escape_rejected(self):
        s = self.new_sandbox()
        for candidate in ("..\\..\\escape.txt", str(s.root / "a" / ".." / ".." / ".." / "e.txt")):
            with self.assertRaises(fvs.SandboxEscapeError) as ctx:
                s.validate_binding("TRAV", candidate)
            self.assertEqual(ctx.exception.reason, "RESOLVES_OUTSIDE_SANDBOX")

    def test_07_normalization_case_and_drive_relative_aliases(self):
        s = self.new_sandbox()
        drive = os.path.splitdrive(s.canonical_root)[0]
        self.assertEqual(s.validate_binding("CASE", drive.upper() + s.canonical_root[2:]),
                         s.canonical_root)
        self.assertEqual(s.validate_binding("CASE2", str(s.root) + os.sep),
                         s.canonical_root)
        # A drive-relative reference anchors outside the sandbox (current cwd)
        # and must be rejected, never silently accepted.
        drive_relative = drive + "probe_relative_escape.txt"
        with self.assertRaises(fvs.SandboxEscapeError) as ctx:
            s.validate_binding("DRIVEREL", drive_relative)
        self.assertEqual(ctx.exception.reason, "RESOLVES_OUTSIDE_SANDBOX")

    def test_08_empty_binding_rejected(self):
        s = self.new_sandbox()
        for bad in ("", "   "):
            with self.assertRaises(fvs.SandboxEscapeError):
                s.validate_binding("EMPTY", bad)

    def _reparse_escape_common(self, link: Path):
        s = self.new_sandbox()
        before = sha(self.protected)
        with self.assertRaises(fvs.SandboxEscapeError) as ctx:
            s.validate_binding("REPARSE", link / "state.json")
        self.assertEqual(ctx.exception.reason, "RESOLVES_INTO_LIVE_RUNTIME_AUTHORITY")
        self.assertEqual(sha(self.protected), before)

    def test_09_junction_escape_rejected(self):
        link = self.base / "junction_link"
        try:
            _winapi.CreateJunction(str(self.live), str(link))
        except OSError as exc:
            self.skipTest(f"junction primitive unavailable on this platform: {exc}")
        self._reparse_escape_common(link)

    def test_10_directory_symlink_escape_rejected(self):
        link = self.base / "symlink_link"
        try:
            os.symlink(str(self.live), str(link), target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"symlink primitive unavailable without privilege: {exc}")
        self._reparse_escape_common(link)


# ---------------------------------------------------------------------------
# 2. Process / import isolation
# ---------------------------------------------------------------------------

class ProcessIsolationTests(_TempRootCase):
    def test_11_wrong_cwd_refused_before_probe_starts(self):
        s = self.new_sandbox()
        probe = s.write_probe_module("p", "print('hi')\n")
        with self.assertRaises(fvs.SandboxEscapeError) as ctx:
            s.run_probe([sys.executable, str(probe)], cwd=self.live)
        self.assertEqual(ctx.exception.reason, "RESOLVES_INTO_LIVE_RUNTIME_AUTHORITY")

    def test_12_wrong_cwd_cannot_redirect_relative_writes(self):
        s = self.new_sandbox()
        probe = s.write_probe_module(
            "cwd_probe",
            "from pathlib import Path\n"
            "Path('probe_out').mkdir(exist_ok=True)\n"
            "(Path('probe_out') / 'w.txt').write_text('sandboxed', encoding='utf-8')\n"
            "print('WROTE', (Path.cwd() / 'probe_out' / 'w.txt').resolve())\n")
        result = s.run_probe([sys.executable, str(probe)], cwd=s.root / "probe")
        self.assertEqual(result["returncode"], 0, result)
        self.assertTrue((s.root / "probe" / "probe_out" / "w.txt").is_file())
        self.assertFalse((self.live / "probe_out").exists())
        self.assertEqual(sha(self.protected),
                         sha(self.protected))  # sentinel untouched (hash stable)

    def test_13_child_environment_strips_contamination_vectors(self):
        s = self.new_sandbox()
        s.populate_runtime_copy(
            {str(self.live / "scripts" / "runtime_module.py"):
             "runtime/scripts/runtime_module.py"})
        old = dict(os.environ)
        try:
            os.environ["PYTHONPATH"] = str(self.live / "scripts")
            os.environ["PYTHONHOME"] = str(self.live)
            env = s.child_environment()
        finally:
            os.environ.clear()
            os.environ.update(old)
        self.assertNotIn(str(self.live / "scripts"), env["PYTHONPATH"])
        self.assertNotIn("PYTHONHOME", env)
        self.assertNotIn("PYTHONSTARTUP", env)
        self.assertIn(str(s.runtime_copy_root), env["PYTHONPATH"])
        self.assertIn(str(s.runtime_copy_root / "scripts"), env["PYTHONPATH"])

    IDENTITY_PROBE = (
        "import importlib, json, os, sys\n"
        "mods = {}\n"
        "for name in ('runtime_module',):\n"
        "    try:\n"
        "        mods[name] = importlib.import_module(name).__file__\n"
        "    except Exception as exc:\n"
        "        mods[name] = 'IMPORT_ERROR: %r' % (exc,)\n"
        "print(json.dumps({'PROBE_IDENTITY': {'cwd': os.getcwd(), "
        "'executable': sys.executable, 'modules': mods}}))\n"
    )

    def test_14_poisoned_pythonpath_cannot_import_live_code(self):
        s = self.new_sandbox()
        s.populate_runtime_copy(
            {str(self.live / "scripts" / "runtime_module.py"):
             "runtime/scripts/runtime_module.py"})
        probe = s.write_probe_module("identity_probe", self.IDENTITY_PROBE)
        old = os.environ.get("PYTHONPATH")
        try:
            os.environ["PYTHONPATH"] = str(self.live / "scripts")  # contamination
            result = s.run_probe([sys.executable, str(probe)])
        finally:
            if old is None:
                os.environ.pop("PYTHONPATH", None)
            else:
                os.environ["PYTHONPATH"] = old
        self.assertEqual(result["returncode"], 0, result)
        report = json.loads(result["stdout"])["PROBE_IDENTITY"]
        # Resolves to the SANDBOX copy despite the poisoned parent environment.
        fvs.verify_probe_identity(report, s.canonical_root, ["runtime_module"])
        self.assertTrue(report["modules"]["runtime_module"].replace("/", "\\")
                        .lower().startswith(s.canonical_root.lower()))

    def test_15_probe_identity_verification_fails_closed_on_live_import(self):
        s = self.new_sandbox()
        report = {"cwd": str(s.probe_dir),
                  "executable": sys.executable,
                  "modules": {"runtime_module": str(self.live / "scripts" / "runtime_module.py")}}
        with self.assertRaises(fvs.SandboxEscapeError):
            fvs.verify_probe_identity(report, s.canonical_root, ["runtime_module"])
        report_cwd = {"cwd": str(self.live), "executable": sys.executable, "modules": {}}
        with self.assertRaises(fvs.SandboxEscapeError):
            fvs.verify_probe_identity(report_cwd, s.canonical_root, [])


# ---------------------------------------------------------------------------
# 3. Historical failure class: incomplete module-global rebinding
# ---------------------------------------------------------------------------

class HistoricalClassTests(_TempRootCase):
    def test_16_incomplete_rebinding_class_structurally_eliminated(self):
        s = self.new_sandbox()
        s.populate_runtime_copy(
            {str(self.live / "scripts" / "runtime_module.py"):
             "runtime/scripts/runtime_module.py"})
        protected_before = sha(self.protected)
        # The probe imports the module and writes fixture state WITHOUT any
        # global rebinding — the sandbox copy's __file__-derived globals anchor
        # inside the sandbox by construction.
        probe = s.write_probe_module(
            "fixture_probe",
            "import json, runtime_module\n"
            "print(json.dumps({'written': runtime_module.write_fixture_state("
            "{'fixture': 'sandboxed'}), 'module': runtime_module.__file__}))\n")
        outcome = s.destructive_window(
            [sys.executable, str(probe)],
            bindings={"FIXTURE_STATE": s.root / "runtime" / "control" / "project_state.json"},
            required_probe_modules=["runtime_module"],
            probe_stdout_identity=False)
        self.assertEqual(outcome["outcome"], fvs.OUTCOME_CLEAN, outcome)
        self.assertEqual(outcome["guard_status"], fvs.FV_GUARD_PASSED)
        self.assertEqual(outcome["live_manifest_status"], fvs.LIVE_MANIFEST_UNCHANGED)
        self.assertFalse(outcome["isolation_incident"])
        sandbox_copy = s.root / "runtime" / "control" / "project_state.json"
        self.assertTrue(sandbox_copy.is_file(), "write must land in the sandbox copy")
        self.assertEqual(sha(self.protected), protected_before)
        identity = json.loads(outcome["probe"]["stdout"])
        self.assertTrue(
            identity["module"].replace("/", "\\").lower().startswith(s.canonical_root.lower()))

    def test_17_live_argv_material_refused(self):
        s = self.new_sandbox()
        with self.assertRaises(fvs.SandboxEscapeError):
            s.run_probe([sys.executable, str(self.live / "scripts" / "runtime_module.py")])


# ---------------------------------------------------------------------------
# 4. Protected live-state integrity manifest / destructive window
# ---------------------------------------------------------------------------

class ManifestWindowTests(_TempRootCase):
    def test_18_manifest_unchanged_across_safe_destructive_probe(self):
        s = self.new_sandbox()
        probe = s.write_probe_module("writer", "print('destructive-ish')\n")
        outcome = s.destructive_window([sys.executable, str(probe)],
                                       bindings={"OUT": s.root / "tmp.bin"})
        self.assertEqual(outcome["outcome"], fvs.OUTCOME_CLEAN)
        self.assertEqual(outcome["status"], "FV_PROBE_COMPLETED")
        self.assertEqual(outcome["live_manifest_status"], fvs.LIVE_MANIFEST_UNCHANGED)
        self.assertEqual(outcome["manifest_sha256_before"], outcome["manifest_sha256_after"])
        self.assertFalse(outcome["isolation_incident"])
        self.assertTrue((s.root / "sandbox_outcome.json").is_file())

    def test_19_unexpected_protected_mutation_fails_closed_as_incident(self):
        s = self.new_sandbox()
        # Rogue probe bypasses the guard entirely and writes the protected
        # sentinel state (synthetic disposable root — never the real Runtime).
        probe = s.write_probe_module(
            "rogue",
            "from pathlib import Path\n"
            "Path(r'" + str(self.protected) + "').write_text('{\"tampered\": true}', "
            "encoding='utf-8')\n")
        outcome = s.destructive_window([sys.executable, str(probe)],
                                       bindings={"OUT": s.root / "tmp.bin"})
        self.assertEqual(outcome["outcome"], fvs.OUTCOME_INCIDENT)
        self.assertEqual(outcome["status"], fvs.FV_LIVE_STATE_MUTATION_DETECTED)
        self.assertTrue(outcome["isolation_incident"])
        self.assertEqual(outcome["live_manifest_status"], fvs.LIVE_MANIFEST_MUTATION)
        self.assertEqual(len(outcome["live_manifest_mismatches"]), 1)
        for name in ("live_manifest_before.json", "live_manifest_after.json"):
            self.assertTrue((s.root / "incident_evidence" / name).is_file())
        self.assertFalse(s.clean(), "incident sandbox must be retained")

    def test_20_trusted_protocol_writes_not_classified_as_escape(self):
        s = self.new_sandbox()
        probe = s.write_probe_module(
            "trusted",
            "from pathlib import Path\n"
            "Path(r'" + str(self.protected) + "').write_text('{\"trusted\": true}', "
            "encoding='utf-8')\n")
        outcome = s.destructive_window(
            [sys.executable, str(probe)],
            bindings={"OUT": s.root / "tmp.bin"},
            allowed_writes=[self.protected])
        self.assertEqual(outcome["live_manifest_status"], fvs.LIVE_MANIFEST_UNCHANGED)
        self.assertFalse(outcome["isolation_incident"])
        self.assertEqual(len(outcome["allowed_trusted_writes"]), 1)

    def test_21_guard_blocked_window_executes_nothing(self):
        s = self.new_sandbox()
        probe = s.write_probe_module("never", "print('must not run')\n")
        outcome = s.destructive_window(
            [sys.executable, str(probe)],
            bindings={"OUT": self.protected})
        self.assertEqual(outcome["outcome"], fvs.OUTCOME_GUARD_BLOCKED)
        self.assertEqual(outcome["status"], fvs.FV_SANDBOX_ESCAPE_DETECTED)
        self.assertIs(outcome.get("probe_executed"), False)
        self.assertIsNone(outcome["probe"])
        self.assertEqual(outcome["live_manifest_status"], fvs.LIVE_MANIFEST_UNCHANGED)
        self.assertFalse(outcome["isolation_incident"])

    def test_22_clean_sandbox_cleanup_and_incident_retention(self):
        s = self.new_sandbox()
        root_str = str(s.root)
        self.assertTrue(s.clean())
        self.assertFalse(Path(root_str).exists())
        # An incident sandbox (recorded outcome) is never deletable.
        s2 = self.new_sandbox()
        s2.write_outcome({"isolation_incident": True, "status": "X"})
        self.assertFalse(s2.clean())
        self.assertTrue(s2.root.exists())


# ---------------------------------------------------------------------------
# 5. FV contract extension in the orchestrator (dispatch + receipt gate)
# ---------------------------------------------------------------------------

def _policy_claims():
    def claim(cid, ctype):
        checks = {
            "BUG_REPRODUCTION": {"reproduction_check": "PASS",
                                 "reproduction_artifact": "evidence/repro.txt"},
            "REGRESSION": {"regression_suite_check": "PASS",
                           "regression_artifact": "evidence/regression.txt"},
            "ROOT_CAUSE": {"causal_chain_check": "PASS",
                           "root_cause_pointers": ["evidence/rca.md"]},
        }[ctype]
        return {"claim_id": cid, "claim": f"synthetic {ctype} claim",
                "claim_type": ctype, "decision_impact": "HIGH",
                "evidence_pointers": [f"evidence/{cid}.txt"],
                "verification_standard": "synthetic standard"}

    return [claim("C1", "BUG_REPRODUCTION"), claim("C2", "REGRESSION"),
            claim("C3", "ROOT_CAUSE")]


class FVContractTests(_TempRootCase):
    def setUp(self):
        super().setUp()
        # Sandbox copy of the profiles tree (loader mutates nothing here; the
        # copy keeps the suite independent of live profile state).
        shutil.copytree(SCRIPTS.parent / "profiles", self.base / "profiles")
        o.PROFILES_DIR = self.base / "profiles"
        self.addCleanup(setattr, o, "PROFILES_DIR", SCRIPTS.parent / "profiles")
        self.claims = _policy_claims()
        self.claims_hash = o.canonical_claims_hash(self.claims)
        self.policy, _ = o._resolve_fv_policy_for_state({"profile": "SOFTWARE_ENGINEERING"})

    def _gated_state(self, status="PENDING"):
        return {"profile": "SOFTWARE_ENGINEERING",
                "final_verification": {"required": True, "status": status,
                                       "critical_claims": self.claims,
                                       "claims_hash": self.claims_hash}}

    def _gate(self, **extra):
        gate = {"POLICY_VERSION": 1, "POLICY_ID": "SOFTWARE_ENGINEERING_FV_V1",
                "CLAIMS_HASH": self.claims_hash,
                "CLAIM_COUNT": 3, "CRITICAL_CLAIMS": self.claims}
        gate.update(extra)
        return gate

    def _dispatch_task(self, gate):
        return {"TASK_KIND": "FINAL_VERIFICATION", "CLAIM_PROTOCOL_VERSION": 1,
                "FINAL_VERIFICATION_GATE": gate}

    def test_23_dispatch_execution_mode_validation(self):
        # Absent mode keeps legacy semantics.
        o.validate_final_verification_dispatch(self._gated_state(), self._dispatch_task(self._gate()))
        o.validate_final_verification_dispatch(
            self._gated_state(),
            self._dispatch_task(self._gate(EXECUTION_MODE="SANDBOX_DESTRUCTIVE")))
        o.validate_final_verification_dispatch(
            self._gated_state(),
            self._dispatch_task(self._gate(EXECUTION_MODE="LIVE_READ_ONLY")))
        with self.assertRaises(RuntimeError) as ctx:
            o.validate_final_verification_dispatch(
                self._gated_state(),
                self._dispatch_task(self._gate(EXECUTION_MODE="LIVE_DESTRUCTIVE")))
        self.assertIn("EXECUTION_MODE", str(ctx.exception))

    def _receipt_brief(self, *, execution_mode=None, sandbox=None,
                       incident=None, overall="PASS"):
        payload = {"POLICY_VERSION": 1, "CLAIMS_HASH": self.claims_hash,
                   "OVERALL_STATUS": overall,
                   "CLAIM_RESULTS": [
                       {"claim_id": c["claim_id"], "status": "SUPPORTED",
                        "claim_type": c["claim_type"],
                        "decision_impact": "HIGH",
                        "checks": {
                            "BUG_REPRODUCTION": {"reproduction_check": "PASS",
                                                 "reproduction_artifact": "a"},
                            "REGRESSION": {"regression_suite_check": "PASS",
                                           "regression_artifact": "a"},
                            "ROOT_CAUSE": {"causal_chain_check": "PASS",
                                           "root_cause_pointers": ["a"]},
                        }[c["claim_type"]],
                        "evidence_pointers": ["evidence/x.txt"]}
                       for c in self.claims]}
        if execution_mode is not None:
            payload["EXECUTION_MODE"] = execution_mode
        if sandbox is not None:
            payload["SANDBOX"] = sandbox
        if incident is not None:
            payload["ISOLATION_INCIDENT"] = incident
        return {"FINAL_VERIFICATION": payload}

    def _fv_task(self, **gate_extra):
        return self._dispatch_task(self._gate(**gate_extra))

    VALID_SANDBOX = {"SANDBOX_ROOT": "C:\\temp\\general-agent-runtime-fv\\run-1",
                     "SANDBOX_IDENTITY_SHA256": "a" * 64,
                     "OUTSIDE_LIVE_RUNTIME": True,
                     "GUARD_STATUS": "FV_GUARD_PASSED",
                     "LIVE_MANIFEST_STATUS": "UNCHANGED",
                     "OUTCOME": "FV_SANDBOX_CLEAN"}

    def test_24_legacy_receipt_without_new_fields_unchanged(self):
        result = o.evaluate_final_verification_receipt(self._fv_task(), self._receipt_brief())
        self.assertEqual(result["issues"], [])
        self.assertTrue(result["mechanical_pass"])

    def test_25_execution_mode_binding_rejects_downgrade_and_upgrade(self):
        # Downgrade: destructive gate, receipt silent -> mechanical fail.
        result = o.evaluate_final_verification_receipt(
            self._fv_task(EXECUTION_MODE="SANDBOX_DESTRUCTIVE"), self._receipt_brief())
        self.assertFalse(result["mechanical_pass"])
        self.assertTrue(any("EXECUTION_MODE" in i for i in result["issues"]))
        # Upgrade: read-only gate, destructive receipt claim -> mechanical fail.
        result = o.evaluate_final_verification_receipt(
            self._fv_task(), self._receipt_brief(execution_mode="SANDBOX_DESTRUCTIVE"))
        self.assertFalse(result["mechanical_pass"])
        # Matching modes pass (with the auditable sandbox block).
        result = o.evaluate_final_verification_receipt(
            self._fv_task(EXECUTION_MODE="SANDBOX_DESTRUCTIVE"),
            self._receipt_brief(execution_mode="SANDBOX_DESTRUCTIVE",
                                sandbox=dict(self.VALID_SANDBOX)))
        self.assertEqual(result["issues"], [], result["issues"])
        self.assertTrue(result["mechanical_pass"])

    def test_26_isolation_incident_can_never_be_pass(self):
        result = o.evaluate_final_verification_receipt(
            self._fv_task(EXECUTION_MODE="SANDBOX_DESTRUCTIVE"),
            self._receipt_brief(execution_mode="SANDBOX_DESTRUCTIVE",
                                sandbox=dict(self.VALID_SANDBOX),
                                incident=True))
        self.assertFalse(result["mechanical_pass"])
        self.assertTrue(any("ISOLATION_INCIDENT" in i for i in result["issues"]))
        # Incident forces failure even on the CLAIM_RESULTS early-return path.
        brief = self._receipt_brief(incident=True)
        brief["FINAL_VERIFICATION"]["CLAIM_RESULTS"] = "not-a-list"
        result = o.evaluate_final_verification_receipt(self._fv_task(), brief)
        self.assertFalse(result["mechanical_pass"])

    def test_27_destructive_receipt_requires_auditable_sandbox_block(self):
        mode = {"EXECUTION_MODE": "SANDBOX_DESTRUCTIVE"}
        # Missing SANDBOX object.
        result = o.evaluate_final_verification_receipt(
            self._fv_task(**mode), self._receipt_brief(execution_mode="SANDBOX_DESTRUCTIVE"))
        self.assertFalse(result["mechanical_pass"])
        self.assertTrue(any("SANDBOX" in i for i in result["issues"]))
        # Sandbox that claims to be inside the live runtime.
        bad = dict(self.VALID_SANDBOX, OUTSIDE_LIVE_RUNTIME=False)
        result = o.evaluate_final_verification_receipt(
            self._fv_task(**mode), self._receipt_brief(sandbox=bad, execution_mode="SANDBOX_DESTRUCTIVE"))
        self.assertFalse(result["mechanical_pass"])
        # Guard status not auditable.
        bad = dict(self.VALID_SANDBOX, GUARD_STATUS="TRUST_ME")
        result = o.evaluate_final_verification_receipt(
            self._fv_task(**mode), self._receipt_brief(sandbox=bad, execution_mode="SANDBOX_DESTRUCTIVE"))
        self.assertFalse(result["mechanical_pass"])
        # Manifest mutation disclosed.
        bad = dict(self.VALID_SANDBOX, LIVE_MANIFEST_STATUS="MUTATION_DETECTED")
        result = o.evaluate_final_verification_receipt(
            self._fv_task(**mode), self._receipt_brief(sandbox=bad, execution_mode="SANDBOX_DESTRUCTIVE"))
        self.assertFalse(result["mechanical_pass"])

    def test_28_contract_summary_matches_modes(self):
        summary = fvs.render_contract_summary()
        self.assertEqual(summary["execution_modes"], list(o.FINAL_VERIFICATION_EXECUTION_MODES))
        self.assertIn(summary["escape_status"], (fvs.FV_SANDBOX_ESCAPE_DETECTED,))


if __name__ == "__main__":
    unittest.main()
