"""P9 pure-layer tests for Registry-driven Runtime creation.

Pins the supported-release template catalog, the copied/excluded
Runtime-template contract (product and control skeleton in, operational
state out), destination normalization and conflict checks, the reparse /
symlink defenses, copy verification, and rollback honesty. The HTTP
executor behavior (including concurrency and registration) is covered by
the P9 HTTP suite.
"""
from __future__ import annotations

import hashlib
import os
import shutil
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

import web_console_runtime_create as wcr


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_manifest(root: Path) -> dict:
    manifest = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            path = Path(base) / name
            rel = path.relative_to(root).as_posix()
            manifest[rel] = sha256_file(path)
    return manifest


class TemplateCatalogTests(unittest.TestCase):
    def test_catalog_has_exactly_the_local_stable_template(self):
        catalog = wcr.release_template_catalog()
        self.assertEqual(len(catalog), 1)
        template = catalog[0]
        self.assertEqual(template["template_id"], wcr.TEMPLATE_ID)
        self.assertEqual(template["source"]["kind"], "console_installation")
        self.assertEqual(template["compatibility"]["status_schema_versions"],
                         [1])
        for key in ("template_id", "title", "description", "source",
                    "compatibility"):
            self.assertIn(key, template, key)

    def test_known_template_ids_accept_and_refuse(self):
        self.assertEqual(wcr.validate_template_id(wcr.TEMPLATE_ID),
                         wcr.TEMPLATE_ID)
        for bad in ("", "other-template", None, 5, wcr.TEMPLATE_ID + "x"):
            with self.assertRaises(wcr.CreateError) as caught:
                wcr.validate_template_id(bad)
            self.assertEqual(caught.exception.code, "CREATE_UNKNOWN_TEMPLATE")


class SkeletonContractTests(unittest.TestCase):
    def test_release_directories_are_the_product_skeleton(self):
        for name in ("docs", "profiles", "scripts", "web_console"):
            self.assertIn(name, wcr.RELEASE_DIRECTORIES, name)

    def test_operational_top_level_entries_are_never_in_the_allowlist(self):
        forbidden = ("projects", "workspace", "evidence", "reports", "logs",
                     "web_console_data", ".git", "handoff", "control",
                     "completion_staging", "attempt_workspaces",
                     "TO_ZCODE.md", "SUPERVISOR_BRIEF.md", "ZCODE_DONE.flag",
                     "ZCODE_LAST_PROCESSED.txt", "PROJECT_GOAL.md",
                     "RESEARCH_STATE.md", "ACTIVE_PROJECT.json")
        for name in forbidden:
            self.assertNotIn(name, wcr.RELEASE_FILES, name)
            self.assertNotIn(name, wcr.RELEASE_DIRECTORIES, name)
            self.assertNotIn(name, wcr.CONTROL_SEED_FILES, name)
            self.assertNotIn(name, wcr.HANDOFF_SEED_FILES, name)

    def test_control_seed_is_an_explicit_static_file_allowlist(self):
        for name in wcr.CONTROL_SEED_FILES:
            self.assertFalse(name.endswith("/") or name.endswith("\\"), name)
        for excluded in ("ACTIVE_PROJECT.json", "STOP", "HUMAN_REVIEW",
                         "USER_ATTENTION.json", "orchestrator_runtime.json",
                         "supervisor_control.json", "supervisor_config.json",
                         "supervisor_candidate.json"):
            self.assertNotIn(excluded, wcr.CONTROL_SEED_FILES, excluded)

    def test_handoff_seed_is_only_the_static_protocol_document(self):
        self.assertEqual(set(wcr.HANDOFF_SEED_FILES), {"PROTOCOL.md"})

    def test_required_release_paths_are_verified_after_copy(self):
        for rel in ("orchestrator.py", "scripts/supervisor_control.py",
                    "scripts/executor_claim.py", "scripts/executor_fence.py",
                    "scripts/executor_completion.py", "web_console/index.html",
                    "START_WEB_CONSOLE.ps1", "START_AGENT_SYSTEM.ps1",
                    "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md"):
            self.assertIn(rel, wcr.REQUIRED_RELEASE_PATHS, rel)

    def test_no_pyc_or_pycache_is_ever_copied(self):
        self.assertIn("__pycache__", wcr.COPY_EXCLUDED_NAMES)
        self.assertIn(".pyc", wcr.COPY_EXCLUDED_SUFFIXES)


class DestinationValidationTests(unittest.TestCase):
    def test_absolute_normalized_destinations_are_accepted(self):
        base = Path(tempfile.gettempdir()).resolve()
        raw = str(base / "new-runtime")
        for variant in (raw, raw.replace("\\", "/"),
                        raw + os.sep, raw + "/"):
            normalized = wcr.normalize_destination(variant)
            self.assertTrue(normalized.is_absolute())
            self.assertEqual(os.path.normcase(str(normalized)),
                             os.path.normcase(raw))
        unicode_dest = wcr.normalize_destination(
            str(base / "运行时-一號"))
        self.assertTrue(str(unicode_dest).endswith("运行时-一號"))

    def test_relative_and_non_string_destinations_are_refused(self):
        for bad in ("", "relative/path", "new-runtime", None, 5, [],
                    "C:relative/path"):
            with self.assertRaises(wcr.CreateError) as caught:
                wcr.normalize_destination(bad)
            self.assertEqual(caught.exception.code,
                             "CREATE_INVALID_DESTINATION")

    def test_dot_segments_unc_devices_and_control_chars_are_refused(self):
        base = Path(tempfile.gettempdir()).resolve()
        for bad in (str(base / "a" / ".." / "dest"),
                    "\\\\server\\share\\dest", "//server/share/dest",
                    str(base / "con"), str(base / "aux"), str(base / "nul"),
                    str(base / "dest."),
                    str(base / "dest "),
                    str(base / "de\x01st"),
                    "a" * (wcr.DESTINATION_MAX_CHARS + 1),
                    str(base / ".create-tmp-abc"),
                    str(base / "x" / ".create-tmp-abc")):
            with self.assertRaises(wcr.CreateError) as caught:
                wcr.normalize_destination(bad)
            self.assertEqual(caught.exception.code,
                             "CREATE_INVALID_DESTINATION", bad)

    def test_drive_root_itself_is_refused(self):
        with self.assertRaises(wcr.CreateError):
            wcr.normalize_destination("C:\\")


class DestinationConflictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.console = self.base / "console"
        self.data = self.base / "console" / "web_console_data"
        self.console.mkdir()
        self.data.mkdir()
        self.registered = self.base / "existing-runtime"
        self.registered.mkdir()

    def test_destination_equal_or_nested_with_registered_root_conflicts(self):
        for dest in (self.registered,
                     self.registered / "child",
                     self.registered / "child" / "deep"):
            with self.assertRaises(wcr.CreateError) as caught:
                wcr.check_destination_conflicts(
                    dest, console_root=self.console, data_dir=self.data,
                    registered_roots=[str(self.registered)])
            self.assertEqual(caught.exception.code,
                             "CREATE_DESTINATION_CONFLICTS")

    def test_destination_containing_a_registered_root_conflicts(self):
        container = self.base / "containers"
        nested = container / "nested-runtime"
        nested.mkdir(parents=True)
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.check_destination_conflicts(
                container, console_root=self.console, data_dir=self.data,
                registered_roots=[str(nested)])
        self.assertEqual(caught.exception.code,
                         "CREATE_DESTINATION_CONFLICTS")

    def test_destination_touching_console_or_data_dir_conflicts(self):
        conflicting = (self.console, self.console / "inner", self.data,
                       self.data / "inner")
        for dest in conflicting:
            with self.assertRaises(wcr.CreateError):
                wcr.check_destination_conflicts(
                    dest, console_root=self.console, data_dir=self.data,
                    registered_roots=[str(self.registered)])
        wcr.check_destination_conflicts(
            self.base / "outside", console_root=self.console,
            data_dir=self.data, registered_roots=[str(self.registered)])

    def test_sibling_destination_does_not_conflict(self):
        wcr.check_destination_conflicts(
            self.base / "brand-new", console_root=self.console,
            data_dir=self.data, registered_roots=[str(self.registered)])


class ParentChecksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_existing_parent_passes(self):
        parent = self.base / "parents"
        parent.mkdir()
        wcr.check_destination_parent(parent / "dest")

    def test_missing_parent_fails_closed(self):
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.check_destination_parent(self.base / "no-such" / "dest")
        self.assertEqual(caught.exception.code,
                         "CREATE_DESTINATION_PARENT_MISSING")

    def test_parent_that_is_a_file_fails_closed(self):
        parent = self.base / "afile"
        parent.write_text("x", encoding="utf-8")
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.check_destination_parent(parent / "dest")
        self.assertEqual(caught.exception.code,
                         "CREATE_DESTINATION_PARENT_INVALID")

    def test_junction_in_the_parent_chain_is_refused(self):
        real = self.base / "real"
        real.mkdir()
        link = self.base / "link"
        try:
            import _winapi
            _winapi.CreateJunction(str(real), str(link))
        except (ImportError, OSError):
            self.skipTest("junction creation unavailable in this environment")
        self.assertTrue(wcr.is_reparse(link))
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.check_destination_parent(link / "dest")
        self.assertEqual(caught.exception.code, "CREATE_DESTINATION_UNSAFE")


class CopyContractTests(unittest.TestCase):
    PS1 = ("PAUSE_AGENT_SYSTEM.ps1", "PREPARE_ZCODE_AUTOMATION.ps1",
           "REQUEST_SUPERVISOR_INTERVENTION.ps1", "RESUME_AGENT_SYSTEM.ps1",
           "RESUME_HUMAN_REVIEW.ps1", "SHOW_AGENT_STATUS.ps1",
           "SHOW_AGENT_TIMELINE.ps1", "SHOW_EXECUTOR_FEEDBACK.ps1",
           "SHOW_SUPERVISOR_INTERVENTIONS.ps1", "SHOW_SUPERVISOR_TASKS.ps1",
           "START_AGENT_SYSTEM.ps1", "START_PROJECT.ps1",
           "START_WEB_CONSOLE.ps1", "STOP_AGENT_SYSTEM.ps1",
           "STOP_WEB_CONSOLE.ps1")

    def build_source(self, root: Path, *, complete: bool = True) -> Path:
        (root / "control").mkdir(parents=True)
        (root / "handoff").mkdir()
        (root / "web_console").mkdir()
        (root / "scripts").mkdir()
        (root / "docs").mkdir()
        (root / "profiles" / "GENERAL").mkdir(parents=True)
        for name in ("AI_BOOTSTRAP.md", "LICENSE", "README.md",
                     "V1.3_PRODUCT_SPEC.md", ".gitignore", "orchestrator.py"):
            (root / name).write_text(f"content of {name}\n",
                                     encoding="utf-8")
        for name in self.PS1:
            (root / name).write_text(f"content of {name}\n",
                                     encoding="utf-8")
        for name in wcr.CONTROL_SEED_FILES:
            (root / "control" / name).write_text(f"control {name}\n",
                                                 encoding="utf-8")
        # Operational control state that must NEVER be copied:
        for name in ("STOP", "HUMAN_REVIEW", "ACTIVE_PROJECT.json",
                     "orchestrator_runtime.json", "supervisor_control.json",
                     "supervisor_config.json"):
            (root / "control" / name).write_text("operational\n",
                                                 encoding="utf-8")
        (root / "control" / "supervisor_decisions").mkdir()
        (root / "control" / "supervisor_decisions" / "t.json").write_text(
            "{}", encoding="utf-8")
        (root / "handoff" / "PROTOCOL.md").write_text("protocol\n",
                                                      encoding="utf-8")
        (root / "handoff" / "completion_ledger").mkdir()
        (root / "handoff" / "completion_ledger" / "c.json").write_text(
            "{}", encoding="utf-8")
        (root / "handoff" / "executor_claims").mkdir()
        (root / "handoff" / "executor_claims" / "x.claim").mkdir()
        (root / "handoff" / "executor_claims" / "x.claim" / "claim.json") \
            .write_text("{}", encoding="utf-8")
        (root / "web_console" / "index.html").write_text("<html></html>",
                                                         encoding="utf-8")
        for name in ("supervisor_control.py", "executor_claim.py",
                     "executor_fence.py", "executor_completion.py",
                     "preflight.py", "start_project.py"):
            (root / "scripts" / name).write_text(f"# {name}\n",
                                                 encoding="utf-8")
        (root / "scripts" / "junk.pyc").write_text("bytecode",
                                                   encoding="utf-8")
        (root / "scripts" / "__pycache__").mkdir()
        (root / "scripts" / "__pycache__" / "x.pyc").write_text(
            "bytecode", encoding="utf-8")
        (root / "docs" / "a.md").write_text("docs\n", encoding="utf-8")
        (root / "profiles" / "GENERAL" / "profile.json").write_text(
            "{}\n", encoding="utf-8")
        # Project output and runtime residue that must NEVER be copied:
        for rel in ("projects/p1/project_state.json",
                    "workspace/w.md", "evidence/e.txt", "reports/r.md",
                    "web_console_data/instance.json",
                    "web_console_data/runtime_registry.json",
                    "logs/server.log", "TO_ZCODE.md",
                    "SUPERVISOR_BRIEF.md", "PROJECT_GOAL.md"):
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("residue\n", encoding="utf-8")
        if not complete:
            (root / "scripts" / "supervisor_control.py").unlink()
        return root

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_incomplete_source_is_refused_before_any_copy(self):
        source = self.build_source(self.base / "src", complete=False)
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.plan_copy(source)
        self.assertEqual(caught.exception.code, "CREATE_SOURCE_INCOMPLETE")

    def test_copy_transfers_exactly_the_skeleton_and_leaves_source_intact(self):
        source = self.build_source(self.base / "src")
        before = tree_manifest(source)
        temp = self.base / "dest-parent" / ".create-tmp-1"
        temp.parent.mkdir()
        result = wcr.copy_skeleton(source, temp)
        self.assertGreater(result["files"], 0)
        copied = {p.relative_to(temp).as_posix()
                  for p in temp.rglob("*") if p.is_file()}
        # The skeleton is present:
        for rel in ("orchestrator.py", "README.md", "LICENSE",
                    "START_WEB_CONSOLE.ps1", "web_console/index.html",
                    "scripts/supervisor_control.py", "docs/a.md",
                    "profiles/GENERAL/profile.json",
                    "control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md",
                    "control/SUPERVISOR_POLICY.md", "handoff/PROTOCOL.md"):
            self.assertIn(rel, copied, rel)
        # Operational state is absent:
        for rel in ("control/STOP", "control/HUMAN_REVIEW",
                    "control/ACTIVE_PROJECT.json",
                    "control/orchestrator_runtime.json",
                    "control/supervisor_config.json",
                    "control/supervisor_decisions/t.json",
                    "handoff/completion_ledger/c.json",
                    "handoff/executor_claims/x.claim/claim.json",
                    "projects/p1/project_state.json", "workspace/w.md",
                    "evidence/e.txt", "reports/r.md",
                    "web_console_data/instance.json", "logs/server.log",
                    "TO_ZCODE.md", "SUPERVISOR_BRIEF.md", "PROJECT_GOAL.md",
                    "scripts/junk.pyc",
                    "scripts/__pycache__/x.pyc"):
            self.assertNotIn(rel, copied, rel)
        self.assertNotIn("control", [p.name for p in temp.rglob("__pycache__")])
        # The source is byte-identical:
        self.assertEqual(tree_manifest(source), before)

    def test_verify_copy_reports_the_marker_hash(self):
        source = self.build_source(self.base / "src")
        temp = self.base / "v"
        wcr.copy_skeleton(source, temp)
        result = wcr.verify_copy(temp)
        self.assertEqual(result["marker_rel"],
                         "scripts/supervisor_control.py")
        self.assertEqual(result["marker_sha256"],
                         sha256_file(temp / "scripts" /
                                     "supervisor_control.py"))
        (temp / "scripts" / "executor_fence.py").unlink()
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.verify_copy(temp)
        self.assertEqual(caught.exception.code, "CREATE_VALIDATION_FAILED")

    def test_copy_refuses_a_reparse_point_in_the_source(self):
        source = self.build_source(self.base / "src")
        real = self.base / "elsewhere"
        real.mkdir()
        try:
            import _winapi
            _winapi.CreateJunction(str(real), str(source / "docs" / "link"))
        except (ImportError, OSError):
            self.skipTest("junction creation unavailable in this environment")
        temp = self.base / "v2"
        with self.assertRaises(wcr.CreateError) as caught:
            wcr.copy_skeleton(source, temp)
        self.assertEqual(caught.exception.code, "CREATE_SOURCE_UNSAFE")
        self.assertFalse(temp.exists())


class RollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()

    def test_rollback_removes_a_tree_that_still_matches_its_marker(self):
        tree = self.base / "created"
        (tree / "scripts").mkdir(parents=True)
        marker = tree / "scripts" / "supervisor_control.py"
        marker.write_text("# release\n", encoding="utf-8")
        result = wcr.rollback_remove(
            tree, "scripts/supervisor_control.py", sha256_file(marker))
        self.assertTrue(result["removed"])
        self.assertIsNone(result["skipped_reason"])
        self.assertFalse(tree.exists())

    def test_rollback_refuses_to_delete_a_changed_tree(self):
        tree = self.base / "created"
        (tree / "scripts").mkdir(parents=True)
        marker = tree / "scripts" / "supervisor_control.py"
        marker.write_text("# release\n", encoding="utf-8")
        marker.write_text("# tampered\n", encoding="utf-8")
        result = wcr.rollback_remove(
            tree, "scripts/supervisor_control.py",
            hashlib.sha256(b"# release\n").hexdigest())
        self.assertFalse(result["removed"])
        self.assertTrue(result["skipped_reason"])
        self.assertTrue(tree.exists())

    def test_rollback_reports_a_missing_marker_without_deleting(self):
        tree = self.base / "created"
        tree.mkdir()
        result = wcr.rollback_remove(tree, "scripts/supervisor_control.py",
                                     "0" * 64)
        self.assertFalse(result["removed"])
        self.assertTrue(result["skipped_reason"])


class LabelValidationTests(unittest.TestCase):
    def test_labels_follow_the_registry_rules(self):
        self.assertEqual(wcr.validate_label("Smoke 运行时"), "Smoke 运行时")
        for bad in ("", " ", " x", "x ", "x" * 121, "a\x01b", None, 5):
            with self.assertRaises(wcr.CreateError) as caught:
                wcr.validate_label(bad)
            self.assertEqual(caught.exception.code, "CREATE_LABEL_INVALID")


if __name__ == "__main__":
    unittest.main()
