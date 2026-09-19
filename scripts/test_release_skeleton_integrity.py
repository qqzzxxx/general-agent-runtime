"""Release-skeleton integrity: every allowlisted skeleton entry must be part
of the tracked product source.

The Web Console Runtime-create flow copies exactly the named skeleton
allowlist (web_console_runtime_create), and the fresh-install readiness
proof (web_console_fresh_install) requires every control/handoff seed to be
present with identical content. An allowlisted entry that is missing from a
clean checkout (git clone / release archive) silently produces
created-but-never-ready Runtimes: copy_skeleton skips absent entries, and
the defect only surfaces as an obscure readiness preflight FAIL on a clean
install while all tests pass in the developer checkout that happens to hold
the untracked file.

F-001: control/HUMAN_DECISION_TEMPLATE.json was excluded by the live-receipt
gitignore pattern (control/HUMAN_DECISION_*.json), so it was never committed;
clean clones lacked it and every first-project bootstrap flow failed closed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SOURCE = SCRIPTS.parent
sys.path.insert(0, str(SCRIPTS))

import web_console_runtime_create as creation


def skeleton_required_paths() -> set[str]:
    required = set(creation.RELEASE_FILES) | set(creation.REQUIRED_RELEASE_PATHS)
    required.update("control/" + name for name in creation.CONTROL_SEED_FILES)
    required.update("handoff/" + name for name in creation.HANDOFF_SEED_FILES)
    return required


class SkeletonIntegrityTests(unittest.TestCase):
    def test_every_allowlisted_entry_exists_in_product_source(self):
        for relative in sorted(skeleton_required_paths()):
            self.assertTrue((SOURCE / relative).is_file(), relative)
        for name in creation.RELEASE_DIRECTORIES:
            self.assertTrue((SOURCE / name).is_dir(), name)

    def test_every_allowlisted_entry_is_git_tracked(self):
        """A clean clone contains only tracked files: any allowlisted entry
        absent from the index ships in no release built from main."""
        if not (SOURCE / ".git").exists() or shutil.which("git") is None:
            self.skipTest("product source is not a git checkout; "
                          "tracked-tree coverage cannot be checked here")
        listing = subprocess.run(
            ["git", "-C", str(SOURCE), "ls-files", "-z"],
            capture_output=True, check=True, timeout=60)
        tracked = set(listing.stdout.decode("utf-8", errors="replace")
                      .split("\0"))
        untracked = sorted(skeleton_required_paths() - tracked)
        self.assertEqual(untracked, [],
                         "skeleton-required entries missing from the tracked "
                         "tree (they ship in no clean clone or release): "
                         + ", ".join(untracked))


if __name__ == "__main__":
    unittest.main()
