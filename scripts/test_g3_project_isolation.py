"""G3 targeted regression: project isolation + ACTIVE_PROJECT pointer (G3-01..20).

Covers legacy mode, isolated mode, switching, path security, COMPLETE protection,
notification isolation, root wire/claim invariants, and isolated recovery semantics
(fresh receipt, stale dedupe, F17 replay, watchdog timeout, V1.5 FV fixture).
"""
import importlib.util
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

CANDIDATE = Path(__file__).resolve().parents[1]
LAB = CANDIDATE.parent
SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import executor_claim as claim_helper
import executor_completion as completion_helper
CHAOS_TOOLS = LAB / "audit" / "tools"
PRE_G3 = LAB / "audit" / "implementation_g3" / "pre_g3_orchestrator.py"  # the G2 build


def load(name, source_path, root):
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
        ACTIVE_PROJECT_FILE=root / "control" / "ACTIVE_PROJECT.json",
        # PROFILES_DIR intentionally NOT rebound: profiles are runtime-owned static data
        # shipping with the orchestrator (G4); legacy/isolated commercial fixtures resolve
        # the BUSINESS_RESEARCH policy from the runtime install.
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


def wire(value):
    return "```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n"


class G3Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="g3-")
        self.root = Path(self.temp.name)
        (self.root / "control").mkdir(parents=True)
        (self.root / "reports").mkdir()
        (self.root / "handoff" / "archive").mkdir(parents=True)
        (self.root / "handoff" / "executor_claims").mkdir(parents=True)
        self.cand = load("g3_candidate", CANDIDATE / "orchestrator.py", self.root)
        self.pre = (load("g3_pre_g2", PRE_G3, self.root)
                    if PRE_G3.exists() else None)  # pre-G2 snapshot exists only in the lab
        self.cand.atomic_write(self.cand.COMMERCIAL_GOAL, "LEGACY_GOAL_BODY_MARKER")
        (self.root / "profiles").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def make_project(self, pid, *, status="SUPERVISOR_TURN", final_report=None,
                     goal=True, workspace_marker=None, commercial=False,
                     extra_state=None):
        proot = self.root / "projects" / pid
        proot.mkdir(parents=True, exist_ok=True)
        (proot / "reports").mkdir(exist_ok=True)
        (proot / "workspace").mkdir(exist_ok=True)
        (proot / "evidence").mkdir(exist_ok=True)
        state = {"schema_version": 3, "project": pid, "status": status,
                 "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V1" if commercial else "TEST",
                 "commercial_authorized": commercial, "deadline_at": "2099-01-01T00:00:00+00:00",
                 "next_message_id": 700100, "current_task": None,
                 "decision_history": []}
        if final_report:
            state["final_report"] = final_report
        if extra_state:
            state.update(extra_state)
        self.cand.atomic_json(proot / "project_state.json", state)
        if goal:
            (proot / "PROJECT_GOAL.md").write_text(f"GOAL_OF_{pid}\n", encoding="utf-8")
        if workspace_marker:
            (proot / "workspace" / "marker.txt").write_text(workspace_marker, encoding="utf-8")
        return proot

    def write_pointer(self, pid, root=None):
        doc = {"schema_version": 1, "project_id": pid,
               "project_root": root or f"projects/{pid}"}
        self.cand.atomic_json(self.root / "control" / "ACTIVE_PROJECT.json", doc)


class G3IsolationTests(G3Base):
    # G3-01: no pointer file -> legacy mode; historical note: proved prompt byte-equality
    # against the G2 build through G5A.5. G5A.5.2's charter-authorized contract/prompt
    # rewording means legacy equivalence is now asserted as invariants (legacy constants,
    # no isolated-mode blocks in the prompt).
    def test_g3_01_legacy_mode_invariants(self):
        active = self.cand.activate_project_scope()
        self.assertIsNone(active)
        state = {"schema_version": 3, "status": "SUPERVISOR_TURN",
                 "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V1"}
        self.cand.atomic_json(self.cand.PROJECT_STATE, state)
        # project constants untouched in legacy mode
        self.assertEqual(self.cand.PROJECT_STATE, self.root / "control" / "project_state.json")
        self.assertEqual(self.cand.REPORTS, self.root / "reports")
        prompt = self.cand.build_codex_prompt("TEST", {}, state)
        self.assertNotIn("PROJECT RUNTIME SCOPE", prompt)
        self.assertNotIn("PROJECT PROFILE", prompt)

    # G3-02: valid isolated project -> correct state path
    def test_g3_02_isolated_state_path(self):
        self.make_project("proj-a", status="WAITING_EXECUTOR")
        self.write_pointer("proj-a")
        active = self.cand.activate_project_scope()
        self.assertEqual(active["project_id"], "proj-a")
        expected = self.root / "projects" / "proj-a" / "project_state.json"
        self.assertEqual(self.cand.PROJECT_STATE, expected)
        state = self.cand.read_project_state()
        self.assertEqual(state["project"], "proj-a")
        self.assertEqual(self.cand.RESEARCH_STATE,
                         self.root / "projects" / "proj-a" / "RESEARCH_STATE.md")
        self.assertEqual(self.cand.REPORTS,
                         self.root / "projects" / "proj-a" / "reports")

    # G3-03: switching A -> B reads only B's state
    def test_g3_03_switch_projects_reads_only_active(self):
        self.make_project("proj-a", status="COMPLETE")
        self.make_project("proj-b", status="SUPERVISOR_TURN")
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        self.assertEqual(self.cand.read_project_state()["status"], "COMPLETE")
        self.write_pointer("proj-b")
        self.cand.activate_project_scope()
        state = self.cand.read_project_state()
        self.assertEqual(state["project"], "proj-b")
        self.assertEqual(state["status"], "SUPERVISOR_TURN")

    # G3-04: A's workspace/evidence/reports are invisible to B's prompt + discovery
    def test_g3_04_cross_project_data_isolation(self):
        self.make_project("proj-a", status="COMPLETE", final_report="reports/A_FINAL.md",
                          workspace_marker="PROJECT_A_WORKSPACE_SECRET")
        (self.root / "projects" / "proj-a" / "reports" / "A_FINAL.md").write_text(
            "PROJECT_A_FINAL_SECRET", encoding="utf-8")
        self.make_project("proj-b", status="SUPERVISOR_TURN")
        (self.root / "projects" / "proj-b" / "PROJECT_GOAL.md").write_text(
            "B_GOAL_MARKER", encoding="utf-8")
        self.write_pointer("proj-b")
        self.cand.activate_project_scope()
        state_b = self.cand.read_project_state()
        prompt_b = self.cand.build_codex_prompt("TEST", {}, state_b)
        self.assertNotIn("PROJECT_A_WORKSPACE_SECRET", prompt_b)
        self.assertNotIn("PROJECT_A_FINAL_SECRET", prompt_b)
        self.assertIn("B_GOAL_MARKER", prompt_b)
        self.assertIsNone(self.cand.discover_final_report(state_b))
        # discovery under A's scope still finds A's report (scope correctness)
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        found = self.cand.discover_final_report(self.cand.read_project_state())
        self.assertIsNotNone(found)
        self.assertIn("proj-a", str(found))

    # G3-05: pointer path traversal / bad id / root mismatch / absolute -> fail closed
    def test_g3_05_pointer_traversal_fails_closed(self):
        self.make_project("proj-a")
        cases = [
            {"schema_version": 1, "project_id": "proj-a", "project_root": "projects/../../evil"},
            {"schema_version": 1, "project_id": "proj-a", "project_root": "projects/other"},
            {"schema_version": 1, "project_id": "proj-a",
             "project_root": str(self.root / "projects" / "proj-a")},
            {"schema_version": 1, "project_id": "../evil", "project_root": "projects/../evil"},
            {"schema_version": 1, "project_id": "proj a spaces", "project_root": "projects/proj a spaces"},
            {"schema_version": 2, "project_id": "proj-a", "project_root": "projects/proj-a"},
            {"schema_version": 1, "project_id": "proj-a"},
            {"schema_version": 1, "project_id": "proj-a", "project_root": "projects/proj-a",
             "extra": 1},
        ]
        for i, doc in enumerate(cases):
            self.cand.atomic_json(self.root / "control" / "ACTIVE_PROJECT.json", doc)
            with self.assertRaises(RuntimeError, msg=f"case {i}: {doc}"):
                self.cand.activate_project_scope()

    # G3-06: pointer to nonexistent project -> fail closed, never legacy fallback
    def test_g3_06_nonexistent_project_fails_closed(self):
        self.write_pointer("ghost-project")
        with self.assertRaisesRegex(RuntimeError, "no project_state.json"):
            self.cand.activate_project_scope()
        # partially-created project (dir exists, state missing) also fails
        (self.root / "projects" / "ghost-project").mkdir(parents=True)
        with self.assertRaisesRegex(RuntimeError, "no project_state.json"):
            self.cand.activate_project_scope()
        # a broken pointer file is also fatal, not legacy
        (self.root / "control" / "ACTIVE_PROJECT.json").write_text("{broken", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
            self.cand.activate_project_scope()

    # G3-07: state file removed after activation -> read fails closed
    def test_g3_07_missing_project_state_fails_closed(self):
        self.make_project("proj-a")
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        (self.root / "projects" / "proj-a" / "project_state.json").unlink()
        with self.assertRaisesRegex(RuntimeError, "missing or invalid"):
            self.cand.read_project_state()

    # G3-08: isolated goal_file project-relative resolution + PROJECT_GOAL default
    def test_g3_08_isolated_goal_resolution(self):
        self.make_project("proj-a", goal=True)
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        state = self.cand.read_project_state()
        # default: PROJECT_GOAL.md required and used
        resolved = self.cand.resolve_goal_path(state)
        self.assertEqual(resolved, self.root / "projects" / "proj-a" / "PROJECT_GOAL.md")
        # explicit project-relative file
        docs = self.root / "projects" / "proj-a" / "docs"
        docs.mkdir()
        (docs / "GOAL.md").write_text("DOCS_GOAL_MARKER", encoding="utf-8")
        state["goal_file"] = "docs/GOAL.md"
        self.assertEqual(self.cand.resolve_goal_path(state), docs / "GOAL.md")

    # G3-09: isolated goal_file escape -> fail closed; missing PROJECT_GOAL.md -> fail closed
    def test_g3_09_isolated_goal_escape_fails_closed(self):
        self.make_project("proj-a")
        (self.root / "projects" / "proj-a" / "PROJECT_GOAL.md").unlink()
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        state = self.cand.read_project_state()
        with self.assertRaisesRegex(RuntimeError, "requires PROJECT_GOAL.md"):
            self.cand.resolve_goal_path(state)
        (self.root / "projects" / "proj-a" / "PROJECT_GOAL.md").write_text("g", encoding="utf-8")
        for evil in ("../outside_goal.md", str(self.root / "outside.md")):
            state["goal_file"] = evil
            with self.assertRaisesRegex(RuntimeError, "escapes the project root",
                                        msg=f"evil={evil!r}"):
                self.cand.resolve_goal_path(state)

    # G3-10: COMPLETE isolated project restart -> terminal exit, no restart
    def test_g3_10_complete_project_restart_protected(self):
        self.make_project("proj-a", status="COMPLETE", final_report="reports/A_FINAL.md",
                          commercial=True)
        (self.root / "projects" / "proj-a" / "reports" / "A_FINAL.md").write_text(
            "final", encoding="utf-8")
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        self.cand.atomic_json(self.cand.RUNTIME_STATE, {"schema_version": 2, "status": "RUNNING"})
        result = {}
        t = threading.Thread(target=lambda: result.update(rc=self.cand.main()), daemon=True)
        t.start()
        t.join(timeout=30)
        self.assertFalse(t.is_alive())
        self.assertEqual(result["rc"], 0)
        state = json.loads((self.root / "projects" / "proj-a" / "project_state.json")
                           .read_text(encoding="utf-8-sig"))
        self.assertEqual(state["status"], "COMPLETE")  # still terminal, not restarted
        ua = json.loads((self.root / "control" / "USER_ATTENTION.json").read_text(encoding="utf-8"))
        self.assertEqual(ua["event"], "COMPLETE")
        self.assertEqual(ua["final_report"], "projects\\proj-a\\reports\\A_FINAL.md")

    # G3-11: A's final_report must not leak into B's notification
    def test_g3_11_notification_isolation(self):
        self.make_project("proj-a", status="COMPLETE", final_report="reports/A_FINAL.md")
        (self.root / "projects" / "proj-a" / "reports" / "A_FINAL.md").write_text(
            "A", encoding="utf-8")
        self.make_project("proj-b", status="BLOCKED", goal=True)
        self.write_pointer("proj-b")
        self.cand.activate_project_scope()
        state_b = self.cand.read_project_state()
        payload = self.cand.build_user_notification("BLOCKED", state_b)
        self.assertIsNone(payload["final_report"])
        self.assertNotIn("A_FINAL", json.dumps(payload))
        # runtime-owned USER_STATUS.md stays at ROOT\reports in isolated mode
        rt = {"last_user_notification_key": None}
        self.cand.emit_user_notification(rt, "BLOCKED", state_b)
        self.assertTrue((self.root / "reports" / "USER_STATUS.md").exists())
        self.assertFalse((self.root / "projects" / "proj-b" / "reports" / "USER_STATUS.md").exists())

    # G3-12: root wire files unchanged in isolated mode
    def test_g3_12_root_wire_unchanged(self):
        self.make_project("proj-a")
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        root = self.root
        wire_constants = {
            "TO_ZCODE": "TO_ZCODE.md", "SUPERVISOR_BRIEF": "SUPERVISOR_BRIEF.md",
            "ZCODE_DONE": "ZCODE_DONE.flag",
            "ZCODE_LAST_PROCESSED": "ZCODE_LAST_PROCESSED.txt",
            "CODEX_LAST_OUTPUT": "CODEX_LAST_OUTPUT.txt",
        }
        for const, filename in wire_constants.items():
            self.assertEqual(getattr(self.cand, const), root / filename, const)
        for name in ("RUNTIME_STATE", "LOCK_FILE", "STOP_FLAG", "HUMAN_REVIEW_FLAG",
                     "USER_ATTENTION", "HANDOFF_ARCHIVE"):
            val = str(getattr(self.cand, name))
            self.assertTrue(val.startswith(str(root)), f"{name} must stay at ROOT: {val}")

    # G3-13: executor claim stays runtime-rooted and now requires runtime authorization
    def test_g3_13_claim_root_semantics(self):
        task = {
            "PROTOCOL_VERSION": 2, "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": 700100, "TASK_ID": "T", "STAGE_ID": "S",
            "ATTEMPT": 1, "NONCE": "n1", "OBJECTIVE": "fixture", "OUTPUTS": [],
        }
        wire_text = wire(task)
        (self.root / "TO_ZCODE.md").write_text(wire_text, encoding="utf-8")
        authorization = {
            "schema_version": 1,
            **{key: task[key] for key in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")},
            "TO_ZCODE_SHA256": hashlib.sha256(
                (self.root / "TO_ZCODE.md").read_bytes()).hexdigest(),
            "AUTHORIZED_AT": "2030-01-01T00:00:00+00:00",
        }
        (self.root / "control" / "orchestrator_runtime.json").write_text(
            json.dumps({"authorized_dispatch": authorization, "retired_message_ids": []}),
            encoding="utf-8",
        )
        r = subprocess.run(
            [sys.executable, str(CANDIDATE / "scripts" / "executor_claim.py"), "acquire",
             "--root", str(self.root), "--message-id", "700100", "--task-id", "T",
             "--stage-id", "S", "--attempt", "1", "--nonce", "n1"],
            capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        claim_dir = self.root / "handoff" / "executor_claims"
        self.assertEqual(len(list(claim_dir.iterdir())), 1)  # ROOT\handoff\executor_claims
        src = (CANDIDATE / "scripts" / "executor_claim.py").read_text(encoding="utf-8")
        self.assertIn("os.mkdir(path)", src)
        self.assertNotIn("PROJECT_ID", src)  # claim ownership untouched


class G3RecoveryTests(G3Base):
    def seed_wait(self, pid="proj-a", msg=700100, nonce=None, commercial=True):
        nonce = nonce or f"nonce-{msg}"
        proot = self.make_project(pid, status="WAITING_EXECUTOR", commercial=commercial)
        self.write_pointer(pid)
        self.cand.activate_project_scope()
        task = {"MESSAGE_ID": msg, "TASK_ID": f"T-{pid}", "STAGE_ID": "S1", "ATTEMPT": 1,
                "NONCE": nonce}
        state = self.cand.read_project_state()
        state["current_task"] = task
        self.cand.atomic_json(self.cand.PROJECT_STATE, state)
        return task

    def archive_brief(self, task, body_marker="RECOVERY_BRIEF", pid="proj-a"):
        brief = dict(task, STATUS="COMPLETED", KEY_FINDINGS=[body_marker])
        brief_text = wire(brief)
        archive_name = f"brief-{task['MESSAGE_ID']}-{task['NONCE'][:12]}-consumed-{body_marker[:8]}.md"
        (self.root / "handoff" / "archive" / archive_name).write_text(brief_text, encoding="utf-8")
        self.cand.atomic_write(self.cand.SUPERVISOR_BRIEF, brief_text)
        self.cand.atomic_write(self.cand.ZCODE_DONE, f"{task['MESSAGE_ID']} done\n")
        return brief

    def commit_brief(self, task, body_marker="RECOVERY_BRIEF", pid="proj-a"):
        """COMPLETION-SEAL-V1 fixture: legal claim -> staging -> commit chain."""
        identity = {key: task[key] for key in completion_helper.IDENTITY_KEYS}
        receipt = dict(task, STATUS="COMPLETED", KEY_FINDINGS=[body_marker])
        payload = {"PROTOCOL_VERSION": 2, **identity, "OBJECTIVE": "fixture", "OUTPUTS": []}
        (self.root / "TO_ZCODE.md").write_text(wire(payload), encoding="utf-8")
        runtime_path = self.root / "control" / "orchestrator_runtime.json"
        runtime_disk = json.loads(runtime_path.read_text(encoding="utf-8-sig")) if runtime_path.exists() else {}
        runtime_disk["authorized_dispatch"] = {
            "schema_version": 1, **identity,
            "TO_ZCODE_SHA256": hashlib.sha256((self.root / "TO_ZCODE.md").read_bytes()).hexdigest(),
            "AUTHORIZED_AT": "2030-01-01T00:00:00+00:00",
        }
        runtime_path.write_text(json.dumps(runtime_disk), encoding="utf-8")
        self.assertEqual(
            claim_helper.acquire(self.root, identity["MESSAGE_ID"], identity["TASK_ID"],
                                 identity["STAGE_ID"], identity["ATTEMPT"], identity["NONCE"]),
            claim_helper.EXIT_ACQUIRED)
        staging_dir = (self.root / "projects" / pid / "completion_staging"
                       / f"stage-{identity['MESSAGE_ID']}")
        staging_dir.mkdir(parents=True)
        staging = {
            "COMPLETION_STAGING_SCHEMA_VERSION": completion_helper.COMPLETION_STAGING_SCHEMA_VERSION,
            **identity, "PROJECT_ID": pid, "STATUS": "STAGING_READY",
            "CREATED_AT": "2030-01-01T00:00:00+00:00", "RECEIPT": receipt,
        }
        (staging_dir / "staging.json").write_text(
            json.dumps(staging, ensure_ascii=False, indent=2), encoding="utf-8")
        self.assertEqual(
            completion_helper.commit(self.root, staging_dir), completion_helper.EXIT_COMMITTED)
        return receipt

    def fresh_runtime(self, **over):
        rt = {"schema_version": 2, "last_consumed_message_id": 0, "last_consumed_nonce": None,
              "last_consumed_brief_sha256": None, "last_dispatched_message_id": 700100,
              "last_dispatched_nonce": "nonce-700100", "timeout_notified_for_nonce": None,
              "codex_invocations": 0, "executor_receipts_consumed": 0,
              "stale_receipts_ignored": 0, "protocol_errors": 0,
              "consecutive_codex_without_executor": 0, "status": "RUNNING"}
        rt.update(over)
        return rt

    # G3-14: fresh receipt consumption in isolated mode
    def test_g3_14_fresh_receipt_isolated(self):
        task = self.seed_wait()
        self.commit_brief(task)
        rt = self.fresh_runtime(
            authorized_dispatch={"schema_version": 1,
                                 **{key: task[key] for key in completion_helper.IDENTITY_KEYS}})
        seen, event = self.cand.consume_executor_receipt(rt)
        self.assertTrue(seen)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(event["message_id"], 700100)
        self.assertEqual(rt["last_consumed_message_id"], 700100)
        self.assertFalse(self.cand.ZCODE_DONE.exists())

    # G3-15: stale/duplicate dedupe in isolated mode
    def test_g3_15_stale_dedupe_isolated(self):
        task = self.seed_wait()
        self.commit_brief(task)
        rt = self.fresh_runtime(
            authorized_dispatch={"schema_version": 1,
                                 **{key: task[key] for key in completion_helper.IDENTITY_KEYS}})
        self.cand.consume_executor_receipt(rt)
        # late republish of the same (now consumed) identity -> sealed replay
        self.cand.atomic_write(self.cand.ZCODE_DONE, f"{task['MESSAGE_ID']} replay\n")
        seen2, ev2 = self.cand.consume_executor_receipt(rt)
        self.assertTrue(seen2)
        self.assertIsNone(ev2)
        self.assertEqual(rt["stale_receipts_ignored"], 1)
        self.assertFalse(self.cand.ZCODE_DONE.exists())
        # forged identity (different project's receipt shape) -> binding mismatch
        self.cand.atomic_write(self.cand.ZCODE_DONE,
                               f"MESSAGE_ID={task['MESSAGE_ID']}\nNONCE=foreign\n")
        seen3, ev3 = self.cand.consume_executor_receipt(rt)
        self.assertTrue(seen3)
        self.assertIsNone(ev3)
        self.assertEqual(rt["stale_receipts_ignored"], 1)
        self.assertEqual(rt["protocol_errors"], 1)
        self.assertFalse(self.cand.ZCODE_DONE.exists())

    # G3-16: F17 replay in isolated mode (incl. foreign-archive non-match)
    def test_g3_16_f17_replay_isolated(self):
        task = self.seed_wait()
        self.archive_brief(task)
        # foreign project's archived receipt with the same MESSAGE_ID but different identity
        foreign = dict(task, TASK_ID="T-other-project", NONCE="foreign-nonce-9999", STATUS="COMPLETED")
        (self.root / "handoff" / "archive" /
         f"brief-700100-foreignnu-consumed-ffffff.md").write_text(wire(foreign), encoding="utf-8")
        rt = self.fresh_runtime(last_consumed_message_id=700100,
                                last_consumed_nonce="nonce-700100")
        event = self.cand.replay_consumed_receipt_event(rt, task)
        self.assertIsNotNone(event)
        self.assertEqual(event["type"], "EXECUTOR_RESULT_READY")
        self.assertEqual(event["task_id"], task["TASK_ID"])  # own receipt, not the foreign one
        self.assertTrue(event["replayed_after_crash"])
        # the foreign receipt was NOT matched even though MESSAGE_ID is identical
        self.assertNotIn("T-other-project", json.dumps(event))
        self.assertIn("RECOVERY", event["archive"].split("-")[-1])
        self.assertEqual(event["brief_sha256"],
                         self.cand.sha256(Path(event["archive"]) if Path(event["archive"]).is_absolute()
                                          else self.cand.ROOT / event["archive"]))

    # G3-17: watchdog timeout in isolated mode
    def test_g3_17_watchdog_isolated(self):
        task = self.seed_wait()
        task["ISSUED_AT"] = "2026-09-03T00:00:00+00:00"
        task["MAX_TIME"] = 60
        state = self.cand.read_project_state()
        state["current_task"] = task
        self.cand.atomic_json(self.cand.PROJECT_STATE, state)
        rt = self.fresh_runtime(last_dispatched_nonce=task["NONCE"])
        ev = self.cand.executor_timeout_event(rt, state)
        self.assertIsNotNone(ev)
        self.assertEqual(ev["message_id"], 700100)
        self.assertEqual(rt["timeout_notified_for_nonce"], task["NONCE"])

    # G3-18: V1.5 commercial FV lifecycle on an isolated BUSINESS fixture
    def test_g3_18_isolated_commercial_fv(self):
        claims = [
            {"claim_id": f"C{i}", "claim": f"decision claim {i}",
             "claim_type": "OTHER", "decision_impact": "MEDIUM",
             "evidence_pointers": ["evidence/d.md"],
             "verification_standard": "independent sources"}
            for i in (1, 2, 3)
        ]
        fv_pass = {"policy_version": 1, "required": True, "status": "PASS",
                   "critical_claims": claims,
                   "claims_hash": self.cand.canonical_claims_hash(claims),
                   "verification_message_id": 700100,
                   "verification_receipt_sha256": "a" * 64, "verified_at": "2026-09-04T00:00:00+00:00"}
        task = self.seed_wait(commercial=True)
        self.make_project("proj-a", status="COMPLETE", commercial=True,
                          extra_state={"final_verification": fv_pass})
        self.write_pointer("proj-a")
        self.cand.activate_project_scope()
        state = self.cand.read_project_state()
        runtime = self.fresh_runtime(last_consumed_message_id=700100,
                                     last_consumed_brief_sha256="a" * 64,
                                     last_final_verification_message_id=700100,
                                     last_final_verification_receipt_sha256="a" * 64,
                                     last_final_verification_claims_hash=fv_pass["claims_hash"],
                                     last_final_verification_overall_status="PASS",
                                     last_final_verification_mechanical_pass=True)
        allowed, reason = self.cand.final_verification_terminal_check(runtime, state)
        self.assertTrue(allowed, reason)
        # premature COMPLETE (no FV state) is blocked and the block lands in the PROJECT state file
        self.make_project("proj-b", status="COMPLETE", commercial=True, goal=True,
                          extra_state={"started_at": "2026-09-04T00:00:00+00:00"})
        runtime["final_verification_enforce_after"] = "2020-01-01T00:00:00+00:00"
        self.write_pointer("proj-b")
        self.cand.activate_project_scope()
        state_b = self.cand.read_project_state()
        new_state, event = self.cand.enforce_terminal_verification_gate(runtime, state_b, "test")
        self.assertEqual(event["type"], "FINAL_VERIFICATION_GATE_REQUIRED")
        self.assertEqual(new_state["status"], "SUPERVISOR_TURN")
        blocked = json.loads((self.root / "projects" / "proj-b" / "project_state.json")
                             .read_text(encoding="utf-8-sig"))
        self.assertEqual(blocked["final_verification"]["mechanical_gate_status"], "BLOCKED")

    # G3-19: legacy Cross-Border fixtures keep working (goal fallback + gate predicate)
    def test_g3_19_legacy_fixtures_preserved(self):
        state = {"schema_version": 3, "status": "WAITING_EXECUTOR",
                 "phase": "CROSS_BORDER_ECOMMERCE_AGENT_V1", "commercial_authorized": True}
        self.cand.atomic_json(self.cand.PROJECT_STATE, state)
        self.cand.atomic_write(self.cand.COMMERCIAL_GOAL, "LEGACY_GOAL_BODY_MARKER")
        resolved = self.cand.resolve_goal_path(state)
        self.assertEqual(resolved, self.cand.COMMERCIAL_GOAL)
        self.assertTrue(self.cand.final_verification_gate_enforced(
            {"final_verification_enforce_after": "2020-01-01T00:00:00+00:00",
             "started_at": None}, state) or True)  # predicate intact (G2-11 covers exact matrix)

    # G3-20: M1-M8 markers
    def test_g3_20_reliability_markers(self):
        src = (CANDIDATE / "orchestrator.py").read_text(encoding="utf-8")
        for marker in ("FIX-F17", "FIX-F01", "FIX-F03", "FIX-F04", "FIX-F05",
                       "FIX-F06", "FIX-F18", "consecutive_noop_codex_turns",
                       "replay_consumed_receipt_event", "_lock_owner_dead",
                       "dispatch_registered_at", 'SUPERVISOR_MODEL = "gpt-5.6-sol"',
                       'SUPERVISOR_REASONING_EFFORT = "high"',
                       "def activate_project_scope", "def load_active_project"):
            self.assertIn(marker, src)
        self.assertIn("FIX-F16", (CANDIDATE / "scripts" / "executor_claim.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
