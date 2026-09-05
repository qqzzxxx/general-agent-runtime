"""G2 targeted regression: Project Profile contract (G2-01..12).

Legacy states must produce prompts byte-identical to the G1 build (pre_g2 snapshot);
explicit profiles load their own guidance exclusively; every invalid-profile condition
fails closed. V1.5 commercial fixtures and M1-M8 markers verified separately/by marker.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
LAB = CANDIDATE.parent
PRE_G2 = LAB / "audit" / "implementation_g2" / "pre_g2_orchestrator.py"  # the G1 build
PROFILES_SRC = CANDIDATE / "profiles"


def load(name, source_path, root, profiles_dir):
    spec = importlib.util.spec_from_file_location(name, source_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for attr, val in dict(
        ROOT=root, CONTROL=root / "control", LOGS=root / "logs",
        HANDOFF_ARCHIVE=root / "handoff" / "archive", REPORTS=root / "reports",
        PROJECT_STATE=root / "control" / "project_state.json",
        RUNTIME_STATE=root / "control" / "orchestrator_runtime.json",
        SUPERVISOR_RULES=root / "control" / "CODEX_SUPERVISOR_RUNTIME.md",
        RESEARCH_STATE=root / "RESEARCH_STATE.md",
        COMMERCIAL_GOAL=root / "control" / "CROSS_BORDER_GOAL.md",
        TO_ZCODE=root / "TO_ZCODE.md", SUPERVISOR_BRIEF=root / "SUPERVISOR_BRIEF.md",
        ZCODE_DONE=root / "ZCODE_DONE.flag",
        ZCODE_LAST_PROCESSED=root / "ZCODE_LAST_PROCESSED.txt",
        STOP_FLAG=root / "control" / "STOP", HUMAN_REVIEW_FLAG=root / "control" / "HUMAN_REVIEW",
        LOCK_FILE=root / "control" / ".orchestrator.lock",
        CODEX_LAST_OUTPUT=root / "CODEX_LAST_OUTPUT.txt",
        USER_ATTENTION=root / "control" / "USER_ATTENTION.json",
        USER_STATUS_REPORT=root / "reports" / "USER_STATUS.md",
        PROFILES_DIR=profiles_dir,
    ).items():
        setattr(m, attr, val)
    m.DESKTOP_NOTIFICATIONS_ENABLED = False
    m.USER_NOTIFICATION_CONSOLE_ENABLED = False
    return m


def norm(text):
    # G5A.5: the runtime-root line is location-relative by contract
    return [ln for ln in text.splitlines()
            if not ln.startswith(("DISPATCH_NONCE_SEED:", "TURN_TIME_UTC:",
                                  "Operate only inside"))]


class G2ProfileContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g2-")
        self.root = Path(self.temp.name)
        (self.root / "control").mkdir(parents=True)
        (self.root / "reports").mkdir()
        shutil.copytree(PROFILES_SRC, self.root / "profiles")
        self.cand = load("g2_candidate", CANDIDATE / "orchestrator.py", self.root,
                         self.root / "profiles")
        self.pre = (load("g2_pre_g1", PRE_G2, self.root, self.root / "profiles")
                    if PRE_G2.exists() else None)  # pre-G1 snapshot exists only in the lab
        self.cand.atomic_write(self.cand.COMMERCIAL_GOAL, "LEGACY_GOAL_BODY_MARKER")
        self.base_state = {"schema_version": 3, "status": "SUPERVISOR_TURN",
                           "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V1"}

    def tearDown(self):
        self.temp.cleanup()

    def state_with(self, **extra):
        state = dict(self.base_state)
        state.update(extra)
        self.cand.atomic_json(self.cand.PROJECT_STATE, state)
        if self.pre is not None:
            self.pre.atomic_json(self.pre.PROJECT_STATE, state)
        return state

    def prompt(self, m, state):
        try:
            return m.build_codex_prompt("TEST", {}, state)
        except TypeError:  # pre-G2 (G1) signature
            return m.build_codex_prompt("TEST", {})

    # G2-01: legacy state -> no profile anywhere; historical note: proved byte-equality
    # against the G1 build through G5A.5. G5A.5.2's charter-authorized contract/prompt
    # rewording means equivalence is now asserted as invariants (no profile block,
    # legacy goal fallback intact).
    def test_g2_01_legacy_prompt_invariants(self):
        state = self.state_with()  # no profile key
        self.assertIsNone(self.cand.resolve_profile(state))
        g2_text = self.prompt(self.cand, state)
        self.assertNotIn("PROJECT PROFILE", g2_text)
        self.assertNotIn("Final Verification (policy", g2_text)
        self.assertIn("LEGACY_GOAL_BODY_MARKER", g2_text)
        state_null = self.state_with(profile=None)
        self.assertIsNone(self.cand.resolve_profile(state_null))
        self.assertNotIn("PROJECT PROFILE", self.prompt(self.cand, state_null))

    # G2-02..05: each supported profile loads its own guidance
    def _check_profile(self, profile_id, sup_marker, exe_marker):
        state = self.state_with(profile=profile_id)
        prof = self.cand.resolve_profile(state)
        self.assertEqual(prof["profile_id"], profile_id)
        self.assertIn(sup_marker, prof["supervisor_guidance"])
        self.assertIn(exe_marker, prof["executor_guidance"])
        text = self.prompt(self.cand, state)
        self.assertIn("=== PROJECT PROFILE ===", text)
        self.assertIn(f"Profile: {profile_id}", text)
        self.assertIn(sup_marker, text)
        self.assertIn(exe_marker, text)
        self.assertIn("=== END PROJECT PROFILE ===", text)
        return prof

    def test_g2_02_general(self):
        self._check_profile("GENERAL", "Goal-driven", "Complete exactly one assigned stage")

    def test_g2_03_academic(self):
        self._check_profile("ACADEMIC_RESEARCH", "Anchor every stage to the research question",
                            "record exact environment, versions, configuration, and seeds")

    def test_g2_04_software(self):
        self._check_profile("SOFTWARE_ENGINEERING", "Reproduction before fix",
                            "Reproduce the defect first; capture the trace")

    def test_g2_05_business(self):
        self._check_profile("BUSINESS_RESEARCH", "Evaluate demand, competition, customer pain",
                            "Every figure needs a traceable source pointer")

    # G2-06: unknown profile -> fail closed
    def test_g2_06_unknown_profile_fails_closed(self):
        state = self.state_with(profile="PET_FOOD_EXPERT")
        with self.assertRaisesRegex(RuntimeError, "unsupported project profile"):
            self.cand.resolve_profile(state)
        with self.assertRaises(RuntimeError):
            self.prompt(self.cand, state)

    # G2-07: invalid PROFILE.json schema -> fail closed (multiple shapes)
    def test_g2_07_invalid_schema_fails_closed(self):
        d = self.root / "profiles" / "GENERAL"
        cases = [
            b'{"not json"',                                                                                       # broken json
            json.dumps({k: v for k, v in json.loads((d / "PROFILE.json").read_text(encoding="utf-8")).items()
                        if k != "display_name"}).encode(),                                                        # missing key
            json.dumps(dict(json.loads((d / "PROFILE.json").read_text(encoding="utf-8")),
                            extra_key=1)).encode(),                                                               # unknown key
            json.dumps(dict(json.loads((d / "PROFILE.json").read_text(encoding="utf-8")),
                            profile_schema_version=99)).encode(),                                                 # version mismatch
            json.dumps(dict(json.loads((d / "PROFILE.json").read_text(encoding="utf-8")),
                            profile_id="OTHER")).encode(),                                                        # id/dir mismatch
        ]
        for i, blob in enumerate(cases):
            (d / "PROFILE.json").write_bytes(blob)
            with self.assertRaises(RuntimeError, msg=f"case {i}"):
                self.cand.load_profile("GENERAL")
        # restore
        shutil.copy(PROFILES_SRC / "GENERAL" / "PROFILE.json", d / "PROFILE.json")
        self.assertIsNotNone(self.cand.load_profile("GENERAL"))

    # G2-08: missing guidance file -> fail closed
    def test_g2_08_missing_guidance_fails_closed(self):
        d = self.root / "profiles" / "GENERAL"
        (d / "SUPERVISOR_GUIDANCE.md").unlink()
        with self.assertRaisesRegex(RuntimeError, "supervisor guidance"):
            self.cand.load_profile("GENERAL")
        state = self.state_with(profile="GENERAL")
        with self.assertRaises(RuntimeError):
            self.prompt(self.cand, state)
        shutil.copy(PROFILES_SRC / "GENERAL" / "SUPERVISOR_GUIDANCE.md",
                    d / "SUPERVISOR_GUIDANCE.md")

    # G2-09: guidance path escape -> fail closed
    def test_g2_09_guidance_escape_fails_closed(self):
        d = self.root / "profiles" / "GENERAL"
        doc = json.loads((d / "PROFILE.json").read_text(encoding="utf-8"))
        for evil in ("..\\..\\evil.md", "subdir/../../../evil.md",
                     str(self.root / "evil.md")):
            doc["supervisor_guidance_file"] = evil
            (d / "PROFILE.json").write_text(json.dumps(doc), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "escapes the profile directory",
                                        msg=f"evil={evil!r}"):
                self.cand.load_profile("GENERAL")
        shutil.copy(PROFILES_SRC / "GENERAL" / "PROFILE.json", d / "PROFILE.json")

    # G2-10: profiles are mutually isolated (each contains only its own markers)
    def test_g2_10_profile_guidance_isolation(self):
        markers = {
            "GENERAL": ("Goal-driven", "Complete exactly one assigned stage"),
            "ACADEMIC_RESEARCH": ("Anchor every stage to the research question",
                                  "record exact environment, versions, configuration, and seeds"),
            "SOFTWARE_ENGINEERING": ("Reproduction before fix",
                                     "Reproduce the defect first; capture the trace"),
            "BUSINESS_RESEARCH": ("Evaluate demand, competition, customer pain",
                                  "Every figure needs a traceable source pointer"),
        }
        for pid, (sup, exe) in markers.items():
            prof = self.cand.load_profile(pid)
            self.assertIn(sup, prof["supervisor_guidance"])
            self.assertIn(exe, prof["executor_guidance"])
            for other_pid, (o_sup, o_exe) in markers.items():
                if other_pid == pid:
                    continue
                self.assertNotIn(o_sup, prof["supervisor_guidance"],
                                 f"{pid} leaks {other_pid} supervisor guidance")
                self.assertNotIn(o_exe, prof["executor_guidance"],
                                 f"{pid} leaks {other_pid} executor guidance")

    # G2-11 is executed as the unmodified V1.5 suite (see regression report); here we
    # additionally assert the FV evaluator source is untouched by G2 at the marker level.
    def test_g2_11_fv_evaluator_markers_unchanged(self):
        src = (CANDIDATE / "orchestrator.py").read_text(encoding="utf-8")
        for marker in ("FINAL_VERIFICATION_POLICY_VERSION = 1",
                       "FINAL_VERIFICATION_CLAIM_MIN = 3", "FINAL_VERIFICATION_CLAIM_MAX = 8",
                       "def evaluate_final_verification_receipt",
                       "def final_verification_gate_enforced",
                       "def enforce_terminal_verification_gate",
                       "commercial_authorized",
                       # G4: the V1.5 commercial predicate moved into the isolated legacy adapter
                       "def _legacy_gate_subject", 'startswith("CROSS_BORDER_ECOMMERCE")'):
            self.assertIn(marker, src)

    # G2-12: M1-M8 reliability markers all present
    def test_g2_12_reliability_markers_present(self):
        src = (CANDIDATE / "orchestrator.py").read_text(encoding="utf-8")
        for marker in ("FIX-F17", "FIX-F01", "FIX-F03", "FIX-F04", "FIX-F05",
                       "FIX-F06", "FIX-F18", "consecutive_noop_codex_turns",
                       "replay_consumed_receipt_event", "_lock_owner_dead",
                       "dispatch_registered_at", 'SUPERVISOR_MODEL = "gpt-5.6-sol"',
                       'SUPERVISOR_REASONING_EFFORT = "high"'):
            self.assertIn(marker, src)
        claim_src = (CANDIDATE / "scripts" / "executor_claim.py").read_text(encoding="utf-8")
        self.assertIn("FIX-F16", claim_src)


if __name__ == "__main__":
    unittest.main()
