"""G5A targeted regression: unified project bootstrap / START_PROJECT (G5A-01..22).

Exercises scripts/start_project.py (the START_PROJECT.ps1 engine) plus one real
START_PROJECT.ps1 invocation, and the runtime-readiness path (activate -> load -> prompt
-> FV policy resolution) without ever calling Codex/GLM.
"""
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

CANDIDATE = Path(__file__).resolve().parents[1]
LAB = CANDIDATE.parent
PY = sys.executable


def load(name, source_path, root):
    spec = importlib.util.spec_from_file_location(name, source_path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    for attr, val in dict(
        ROOT=root, CONTROL=root / "control", LOGS=root / "logs",
        HANDOFF_ARCHIVE=root / "handoff" / "archive", REPORTS=root / "reports",
        PROJECT_STATE=root / "control" / "project_state.json",
        RUNTIME_STATE=root / "control" / "orchestrator_runtime.json",
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
        ACTIVE_PROJECT_FILE=root / "control" / "ACTIVE_PROJECT.json",
    ).items():
        setattr(m, attr, val)
    return m


def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def norm(text):
    return [ln for ln in text.splitlines() if not ln.startswith(("DISPATCH_NONCE_SEED:", "TURN_TIME_UTC:"))]


class G5ABase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g5a-")
        self.root = Path(self.temp.name)
        (self.root / "control").mkdir(parents=True)
        (self.root / "scripts").mkdir(parents=True)
        shutil.copy(CANDIDATE / "orchestrator.py", self.root / "orchestrator.py")
        shutil.copytree(CANDIDATE / "profiles", self.root / "profiles")
        shutil.copy(CANDIDATE / "scripts" / "start_project.py",
                    self.root / "scripts" / "start_project.py")
        self.m = load("g5a_runtime", self.root / "orchestrator.py", self.root)

    def tearDown(self):
        self.temp.cleanup()

    def start(self, pid, ptype, goal="Test goal for G5A.", goal_file=None, extra=()):
        args = [PY, str(CANDIDATE / "scripts" / "start_project.py"), "--root", str(self.root),
                "--project-id", pid, "--project-type", ptype]
        if goal_file:
            args += ["--goal-file", goal_file]
        else:
            args += ["--goal", goal]
        return subprocess.run(args + list(extra), capture_output=True, text=True)

    def seed_history(self, *, zlp=None, consumed=None, dispatched=None, legacy_next=None,
                     status="COMPLETE", pid="legacy-root-project"):
        if zlp is not None:
            (self.root / "ZCODE_LAST_PROCESSED.txt").write_text(f"{zlp}\n", encoding="utf-8")
        rt = {}
        if consumed is not None:
            rt["last_consumed_message_id"] = consumed
        if dispatched is not None:
            rt["last_dispatched_message_id"] = dispatched
        if rt:
            self.m.atomic_json(self.root / "control" / "orchestrator_runtime.json", rt)
        if legacy_next is not None or status:
            state = {"status": status, "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V2",
                     "commercial_authorized": True}
            if legacy_next is not None:
                state["next_message_id"] = legacy_next
            self.m.atomic_json(self.root / "control" / "project_state.json", state)


class G5ACreationTests(G5ABase):
    def assert_created(self, pid, ptype, r, expect_seed_min=None):
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["event"], "PROJECT_CREATED")
        proot = self.root / "projects" / pid
        state = read_json(proot / "project_state.json")
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(state["profile"], ptype)
        self.assertEqual(state["project_type"], ptype)
        self.assertEqual(state["goal_file"], "PROJECT_GOAL.md")
        self.assertIsNone(state["current_task"])
        fv = state["final_verification"]
        self.assertTrue(fv["required"])
        self.assertEqual(fv["status"], "NOT_STARTED")
        expected_policy = {"GENERAL": "GENERAL_FV_V1",
                           "ACADEMIC_RESEARCH": "ACADEMIC_FV_V1",
                           "SOFTWARE_ENGINEERING": "SOFTWARE_ENGINEERING_FV_V1",
                           "BUSINESS_RESEARCH": "BUSINESS_RESEARCH_FV_V1"}[ptype]
        self.assertEqual(fv["policy_id"], expected_policy)
        for key in ("critical_claims", "claims_hash", "verification_message_id",
                    "verification_receipt_sha256", "verified_at"):
            self.assertIn(key, fv)
        pointer = read_json(self.root / "control" / "ACTIVE_PROJECT.json")
        self.assertEqual(pointer, {"schema_version": 1, "project_id": pid,
                                   "project_root": f"projects/{pid}"})
        if expect_seed_min is not None:
            self.assertGreater(state["next_message_id"], expect_seed_min)
        return state, out

    def test_g5a_01_create_general(self):
        r = self.start("gen-001", "GENERAL", goal="Summarize a book.")
        self.assert_created("gen-001", "GENERAL", r)

    def test_g5a_02_create_academic(self):
        r = self.start("acad-001", "ACADEMIC_RESEARCH", goal="Reproduce and diagnose an anomaly.")
        self.assert_created("acad-001", "ACADEMIC_RESEARCH", r)

    def test_g5a_03_create_software(self):
        r = self.start("swe-001", "SOFTWARE_ENGINEERING", goal="Fix the parser bug.")
        self.assert_created("swe-001", "SOFTWARE_ENGINEERING", r)

    def test_g5a_04_create_business(self):
        r = self.start("biz-001", "BUSINESS_RESEARCH", goal="Evaluate a product opportunity.")
        self.assert_created("biz-001", "BUSINESS_RESEARCH", r)

    def test_g5a_05_unknown_project_type_fails_closed(self):
        r = self.start("x-001", "PET_FOOD_EXPERT", goal="g")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unsupported project type", r.stderr)
        self.assertFalse((self.root / "projects" / "x-001").exists())
        self.assertFalse((self.root / "control" / "ACTIVE_PROJECT.json").exists())

    def test_g5a_06_invalid_project_id_fails_closed(self):
        for bad in ("../evil", "a/b", "a\\b", "..", ".", "C:evil", "-lead", "a" * 65, ""):
            r = self.start(bad, "GENERAL", goal="g")
            self.assertNotEqual(r.returncode, 0, f"bad id {bad!r} accepted")
        self.assertFalse((self.root / "control" / "ACTIVE_PROJECT.json").exists())
        self.assertFalse((self.root / "projects").exists() and
                         any((self.root / "projects").iterdir()))

    def test_g5a_07_existing_project_not_overwritten(self):
        r = self.start("dup-001", "GENERAL", goal="first")
        self.assertEqual(r.returncode, 0)
        goal_before = (self.root / "projects" / "dup-001" / "PROJECT_GOAL.md").read_text(encoding="utf-8")
        (self.root / "control" / "ACTIVE_PROJECT.json").unlink()  # operator cleared pointer
        r2 = self.start("dup-001", "GENERAL", goal="SECOND ATTEMPT")
        self.assertNotEqual(r2.returncode, 0)
        self.assertIn("never overwritten", r2.stderr)
        self.assertEqual((self.root / "projects" / "dup-001" / "PROJECT_GOAL.md").read_text(
            encoding="utf-8"), goal_before)

    # G5A-08: nonterminal ACTIVE_PROJECT refuses replacement
    def test_g5a_08_nonterminal_active_refused(self):
        for status in ("SUPERVISOR_TURN", "WAITING_EXECUTOR"):
            with self.subTest(status=status):
                pid = f"busy-{status.lower()}"
                proot = self.root / "projects" / pid
                proot.mkdir(parents=True)
                self.m.atomic_json(proot / "project_state.json",
                                   {"status": status, "project": pid})
                self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE,
                                   {"schema_version": 1, "project_id": pid,
                                    "project_root": f"projects/{pid}"})
                r = self.start(f"new-{status.lower()}", "GENERAL", goal="g")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("ACTIVE_PROJECT_ALREADY_RUNNING", r.stderr)
                self.assertFalse((self.root / "projects" / f"new-{status.lower()}").exists())
                pointer = read_json(self.m.ACTIVE_PROJECT_FILE)
                self.assertEqual(pointer["project_id"], pid)

    # G5A-09: terminal active project allows a new project
    def test_g5a_09_terminal_active_allows_new(self):
        for status in ("COMPLETE", "BLOCKED", "STOPPED"):
            with self.subTest(status=status):
                proot = self.root / "projects" / f"done-{status}"
                proot.mkdir(parents=True)
                self.m.atomic_json(proot / "project_state.json", {"status": status})
                self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE,
                                   {"schema_version": 1, "project_id": f"done-{status}",
                                    "project_root": f"projects/done-{status}"})
                r = self.start(f"new-{status}", "GENERAL", goal="g")
                self.assertEqual(r.returncode, 0, r.stderr)
                pointer = read_json(self.m.ACTIVE_PROJECT_FILE)
                self.assertEqual(pointer["project_id"], f"new-{status}")
                # restore the replaced pointer for the next subTest round
                self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE,
                                   {"schema_version": 1, "project_id": f"done-{status}",
                                    "project_root": f"projects/done-{status}"})

    # G5A-10: HUMAN_REVIEW is still active
    def test_g5a_10_human_review_refused(self):
        proot = self.root / "projects" / "hr"
        proot.mkdir(parents=True)
        self.m.atomic_json(proot / "project_state.json", {"status": "HUMAN_REVIEW"})
        self.m.atomic_json(self.m.ACTIVE_PROJECT_FILE,
                           {"schema_version": 1, "project_id": "hr",
                            "project_root": "projects/hr"})
        r = self.start("after-hr", "GENERAL", goal="g")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ACTIVE_PROJECT_ALREADY_RUNNING", r.stderr)
        self.assertFalse((self.root / "projects" / "after-hr").exists())

    # G5A-11: invalid pointer -> fail closed, pointer untouched
    def test_g5a_11_invalid_pointer_fails_closed(self):
        (self.root / "control" / "ACTIVE_PROJECT.json").write_text("{broken json", encoding="utf-8")
        r = self.start("new-001", "GENERAL", goal="g")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("pointer is invalid", r.stderr)
        self.assertEqual((self.root / "control" / "ACTIVE_PROJECT.json").read_text(encoding="utf-8"),
                         "{broken json")

    # G5A-12/13/14: goal/profile/policy binding correctness
    def test_g5a_12_goal_written_project_local(self):
        goal_file = self.root / "external-goal.md"
        goal_file.write_text("GOAL_FROM_FILE_MARKER", encoding="utf-8")
        r = self.start("goal-file-001", "GENERAL", goal_file=str(goal_file))
        self.assertEqual(r.returncode, 0, r.stderr)
        text = (self.root / "projects" / "goal-file-001" / "PROJECT_GOAL.md").read_text(encoding="utf-8")
        self.assertIn("GOAL_FROM_FILE_MARKER", text)
        self.assertIn("PROJECT_ID: goal-file-001", text)

    def test_g5a_13_profile_binding(self):
        r = self.start("bind-academic", "ACADEMIC_RESEARCH", goal="g")
        self.assertEqual(r.returncode, 0, r.stderr)
        state = read_json(self.root / "projects" / "bind-academic" / "project_state.json")
        self.assertEqual(state["profile"], "ACADEMIC_RESEARCH")
        self.assertEqual(state["project_type"], "ACADEMIC_RESEARCH")
        self.assertEqual(state["phase"], "ACADEMIC_RESEARCH")

    def test_g5a_14_fv_policy_binding(self):
        r = self.start("bind-fv", "SOFTWARE_ENGINEERING", goal="g")
        self.assertEqual(r.returncode, 0, r.stderr)
        fv = read_json(self.root / "projects" / "bind-fv" / "project_state.json")["final_verification"]
        self.assertEqual(fv["policy_id"], "SOFTWARE_ENGINEERING_FV_V1")
        self.assertEqual(fv["policy_version"], 1)
        self.assertTrue(fv["required"])

    # G5A-15/16: pointer is the commit point; failures leave no pointer
    def test_g5a_15_pointer_is_last_commit(self):
        r = self.start("commit-001", "GENERAL", goal="g")
        self.assertEqual(r.returncode, 0, r.stderr)
        proj = self.root / "projects" / "commit-001"
        state_m = proj.joinpath("project_state.json").stat().st_mtime_ns
        pointer_m = (self.root / "control" / "ACTIVE_PROJECT.json").stat().st_mtime_ns
        self.assertGreaterEqual(pointer_m, state_m)
        self.assertFalse(any(p.name.startswith(".creating-") for p in
                             (self.root / "projects").iterdir()))

    def test_g5a_16_failed_init_leaves_no_pointer(self):
        d = self.root / "profiles" / "GENERAL"
        (d / "FINAL_VERIFICATION_POLICY.json").unlink()  # breaks late-stage validation
        r = self.start("doomed-001", "GENERAL", goal="g")
        self.assertNotEqual(r.returncode, 0)
        self.assertFalse((self.root / "control" / "ACTIVE_PROJECT.json").exists())
        self.assertFalse((self.root / "projects" / "doomed-001").exists())
        projects_dir = self.root / "projects"
        leftovers = [p for p in projects_dir.iterdir() if p.name.startswith(".creating-")]             if projects_dir.is_dir() else []
        self.assertEqual(leftovers, [])

    # G5A-17: MESSAGE_ID seed strictly above all runtime-known identities
    def test_g5a_17_message_id_seed(self):
        self.seed_history(zlp=800500, consumed=800400, dispatched=800600, legacy_next=800700)
        r = self.start("seed-001", "GENERAL", goal="g")
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["next_message_id"], 800701)
        self.assertEqual(sorted(out["message_id_seed_basis"]), [800400, 800500, 800600, 800700])
        # isolated projects with reserved ids are also respected
        p2 = self.root / "projects" / "reserved"
        p2.mkdir(parents=True)
        self.m.atomic_json(p2 / "project_state.json", {"status": "COMPLETE",
                                                       "next_message_id": 900000})
        (self.root / "control" / "ACTIVE_PROJECT.json").unlink()  # operator cleared pointer
        r2 = self.start("seed-002", "GENERAL", goal="g")
        out2 = json.loads(r2.stdout.strip().splitlines()[-1])
        self.assertEqual(out2["next_message_id"], 900001)
        # fresh runtime (no history) uses the documented floor
        root2 = self.root.parent / (self.root.name + "-fresh")
        shutil.copytree(self.root, root2)
        (root2 / "ZCODE_LAST_PROCESSED.txt").unlink()
        (root2 / "control" / "orchestrator_runtime.json").unlink()
        (root2 / "control" / "project_state.json").unlink()
        (root2 / "control" / "ACTIVE_PROJECT.json").unlink(missing_ok=True)
        shutil.rmtree(root2 / "projects" / "seed-001")
        shutil.rmtree(root2 / "projects" / "seed-002")
        shutil.rmtree(root2 / "projects" / "reserved")
        r3 = subprocess.run([PY, str(CANDIDATE / "scripts" / "start_project.py"),
                             "--root", str(root2), "--project-id", "fresh-001",
                             "--project-type", "GENERAL", "--goal", "g"],
                            capture_output=True, text=True)
        out3 = json.loads(r3.stdout.strip().splitlines()[-1])
        self.assertEqual(out3["next_message_id"], 700100)

    # G5A-18: A/B isolation after sequential creation
    def test_g5a_18_ab_isolation(self):
        self.start("iso-a", "GENERAL", goal="A GOAL MARKER")
        # operator completes A and clears the pointer before B is bootstrapped
        a_state = read_json(self.root / "projects" / "iso-a" / "project_state.json")
        a_state["status"] = "COMPLETE"
        self.m.atomic_json(self.root / "projects" / "iso-a" / "project_state.json", a_state)
        (self.root / "control" / "ACTIVE_PROJECT.json").unlink()
        self.start("iso-b", "GENERAL", goal="B GOAL MARKER")
        a = self.root / "projects" / "iso-a"
        b = self.root / "projects" / "iso-b"
        self.assertIn("A GOAL MARKER", (a / "PROJECT_GOAL.md").read_text(encoding="utf-8"))
        self.assertIn("B GOAL MARKER", (b / "PROJECT_GOAL.md").read_text(encoding="utf-8"))
        self.assertNotIn("B GOAL MARKER", (a / "PROJECT_GOAL.md").read_text(encoding="utf-8"))
        self.assertNotIn("A GOAL MARKER", (b / "PROJECT_GOAL.md").read_text(encoding="utf-8"))
        # runtime-owned artifacts did not leak into either project
        for p in (a, b):
            for owned in ("handoff", "orchestrator_runtime.json", "TO_ZCODE.md",
                          "ZCODE_DONE.flag"):
                self.assertFalse((p / owned).exists(), f"{p.name} must not own {owned}")

    # G5A-19: the runtime loads a freshly bootstrapped project as ready
    def test_g5a_19_runtime_loads_bootstrapped_project(self):
        r = self.start("ready-001", "ACADEMIC_RESEARCH", goal="ACADEMIC_GOAL_MARKER")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = load("g5a_ready", self.root / "orchestrator.py", self.root)
        active = m.activate_project_scope()
        self.assertEqual(active["project_id"], "ready-001")
        state = m.read_project_state()
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        self.assertEqual(m.resolve_goal_path(state).name, "PROJECT_GOAL.md")
        self.assertIn("ACADEMIC_GOAL_MARKER", m.safe_read_text(m.resolve_goal_path(state)))
        profile = m.resolve_profile(state)
        self.assertEqual(profile["profile_id"], "ACADEMIC_RESEARCH")
        policy, route = m._resolve_fv_policy_for_state(state)
        self.assertEqual((route, policy["policy_id"]), ("profile", "ACADEMIC_FV_V1"))
        prompt = m.build_codex_prompt("ORCHESTRATOR_START", {}, state)
        self.assertIn("ACADEMIC_GOAL_MARKER", prompt)
        self.assertIn("=== PROJECT PROFILE ===", prompt)
        self.assertIn("=== PROJECT RUNTIME SCOPE ===", prompt)
        self.assertIn("PROJECT_ID: ready-001", prompt)

    # G5A-20: creating B does not modify a COMPLETE project A
    def test_g5a_20_complete_project_untouched(self):
        r = self.start("keep-a", "GENERAL", goal="A")
        self.assertEqual(r.returncode, 0)
        a_state = self.root / "projects" / "keep-a" / "project_state.json"
        a_goal = self.root / "projects" / "keep-a" / "PROJECT_GOAL.md"
        before = (hashlib.sha256(a_state.read_bytes()).hexdigest(),
                  hashlib.sha256(a_goal.read_bytes()).hexdigest())
        self.start("keep-b", "GENERAL", goal="B")
        after = (hashlib.sha256(a_state.read_bytes()).hexdigest(),
                 hashlib.sha256(a_goal.read_bytes()).hexdigest())
        self.assertEqual(before, after)
        state = read_json(a_state)
        self.assertEqual(state["status"], "SUPERVISOR_TURN")  # untouched, still initial

    # G5A-21: legacy mode still works beside the bootstrap machinery
    def test_g5a_21_legacy_mode_preserved(self):
        self.seed_history(status="COMPLETE")
        state = {"schema_version": 3, "status": "WAITING_EXECUTOR",
                 "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V2", "commercial_authorized": True}
        self.m.atomic_json(self.m.PROJECT_STATE, state)
        self.m.atomic_write(self.m.COMMERCIAL_GOAL, "LEGACY_GOAL_BODY_MARKER")
        # no ACTIVE_PROJECT file -> legacy paths
        self.assertFalse((self.root / "control" / "ACTIVE_PROJECT.json").exists())
        self.assertEqual(self.m.resolve_goal_path(state).name, "CROSS_BORDER_GOAL.md")
        self.assertTrue(self.m._legacy_gate_subject(state))
        policy, route = self.m._resolve_fv_policy_for_state(state)
        self.assertEqual((route, policy["policy_id"]), ("legacy_v1_5", "BUSINESS_RESEARCH_FV_V1"))

    # G5A-22: markers
    def test_g5a_22_markers_preserved(self):
        src = (CANDIDATE / "orchestrator.py").read_text(encoding="utf-8")
        for marker in ("FIX-F17", "FIX-F01", "FIX-F03", "FIX-F04", "FIX-F05", "FIX-F06",
                       "FIX-F18", "consecutive_noop_codex_turns", "replay_consumed_receipt_event",
                       "_lock_owner_dead", "dispatch_registered_at",
                       "def evaluate_final_verification_receipt",
                       "def final_verification_terminal_check",
                       "def enforce_terminal_verification_gate",
                       'SUPERVISOR_MODEL = "gpt-5.6-sol"',
                       'SUPERVISOR_REASONING_EFFORT = "high"',
                       "def activate_project_scope", "def load_active_project"):
            self.assertIn(marker, src)
        self.assertIn("FIX-F16", (CANDIDATE / "scripts" / "executor_claim.py").read_text(encoding="utf-8"))


class G5ALauncherTests(G5ABase):
    # START_PROJECT.ps1 real invocation (thin wrapper over the same engine)
    def test_g5a_ps1_launcher(self):
        ps1 = CANDIDATE / "START_PROJECT.ps1"
        r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(ps1), "-ProjectId", "ps1-001",
                            "-ProjectType", "GENERAL", "-Goal", "PS1 GOAL MARKER",
                            "-RuntimeRoot", str(self.root)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr or r.stdout)
        state = read_json(self.root / "projects" / "ps1-001" / "project_state.json")
        self.assertEqual(state["status"], "SUPERVISOR_TURN")
        pointer = read_json(self.root / "control" / "ACTIVE_PROJECT.json")
        self.assertEqual(pointer["project_id"], "ps1-001")

    # §18 startup dry-run: all four profiles, create -> activate -> load -> prompt -> FV
    def test_g5a_dry_run_four_profiles(self):
        for ptype in ("GENERAL", "ACADEMIC_RESEARCH", "SOFTWARE_ENGINEERING", "BUSINESS_RESEARCH"):
            with self.subTest(profile=ptype):
                pid = f"dry-{ptype.lower()}"
                r = self.start(pid, ptype, goal=f"Dry run goal for {ptype}.")
                self.assertEqual(r.returncode, 0, r.stderr)
                m = load(f"g5a_dry_{ptype}", self.root / "orchestrator.py", self.root)
                active = m.activate_project_scope()
                self.assertEqual(active["project_id"], pid)
                state = m.read_project_state()
                self.assertEqual(state["status"], "SUPERVISOR_TURN")
                profile = m.resolve_profile(state)
                self.assertEqual(profile["profile_id"], ptype)
                policy, route = m._resolve_fv_policy_for_state(state)
                self.assertEqual(route, "profile")
                prompt = m.build_codex_prompt("ORCHESTRATOR_START", {}, state)
                self.assertIn(f"PROJECT_ID: {pid}", prompt)
                self.assertIn(f"Profile: {ptype}", prompt)
                self.assertIn(policy["policy_id"], json.dumps(profile["final_verification_policy"]))
                # operator clears the pointer before the next profile's project
                (self.root / "control" / "ACTIVE_PROJECT.json").unlink()


if __name__ == "__main__":
    unittest.main()
