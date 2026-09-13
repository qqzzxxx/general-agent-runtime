"""Release-like startup regression for the public Runtime launcher.

The subprocesses in this test receive no developer PYTHONPATH and run from a
temporary, non-Git copy.  In particular, the Orchestrator process is not loaded
through this test runner, so the test suite's own sys.path cannot make Runtime
helper modules importable for it.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh.exe")


def clean_environment() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["ORCHESTRATOR_CONSOLE"] = "off"
    return env


def copy_release_layout(destination: Path) -> None:
    """Copy current product code/data, excluding repository and live state."""
    destination.mkdir(parents=True)
    for pattern in ("*.py", "*.ps1", "*.md"):
        for source in SOURCE.glob(pattern):
            shutil.copy2(source, destination / source.name)
    for name in (".gitignore", "LICENSE"):
        shutil.copy2(SOURCE / name, destination / name)
    for directory in ("control", "docs", "profiles", "scripts"):
        shutil.copytree(
            SOURCE / directory,
            destination / directory,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
        )
    for directory in (
        "handoff/archive",
        "handoff/completion_ledger",
        "handoff/executor_claims",
        "handoff/quarantine",
        "handoff/supervisor_dispatch_archive",
        "handoff/supervisor_interventions",
        "projects",
    ):
        (destination / directory).mkdir(parents=True, exist_ok=True)
    shutil.copy2(SOURCE / "handoff" / "PROTOCOL.md",
                 destination / "handoff" / "PROTOCOL.md")
    shutil.copy2(SOURCE / "projects" / "README.md",
                 destination / "projects" / "README.md")


@unittest.skipUnless(POWERSHELL, "PowerShell is required for the public launcher")
class CleanReleaseStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="runtime-clean-release-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.runtime = self.base / "Extracted Runtime With Spaces"
        copy_release_layout(self.runtime)
        self.env = clean_environment()

    def run_process(self, args: list[str], *, timeout: int = 180) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=self.base,  # never rely on launching from the Runtime directory
            env=self.env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )

    def test_public_launcher_bootstraps_production_imports_in_clean_copy(self) -> None:
        self.assertFalse((self.runtime / ".git").exists())
        self.assertNotIn("PYTHONPATH", self.env)

        bootstrap = self.run_process([
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.runtime / "START_PROJECT.ps1"),
            "-ProjectId",
            "clean-release-smoke",
            "-ProjectType",
            "GENERAL",
            "-Goal",
            "Prove clean release startup imports its bundled production helpers.",
        ])
        self.assertEqual(bootstrap.returncode, 0, bootstrap.stdout + bootstrap.stderr)

        preflight = self.run_process([
            sys.executable, str(self.runtime / "scripts" / "preflight.py")
        ])
        self.assertEqual(preflight.returncode, 0, preflight.stdout + preflight.stderr)
        self.assertIn("PREFLIGHT: OK", preflight.stdout)

        # Stop is checked only after the Orchestrator owns its lock, imports and
        # initializes the production control plane, and records successful start.
        # It prevents this regression from invoking an external Codex process.
        (self.runtime / "control" / "STOP").write_text("test stop\n", encoding="utf-8")
        startup = self.run_process([
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.runtime / "START_AGENT_SYSTEM.ps1"),
        ])
        combined = startup.stdout + startup.stderr
        self.assertEqual(startup.returncode, 2, combined)
        self.assertNotIn("ModuleNotFoundError", combined)
        self.assertNotIn(str(SOURCE), combined)

        log_path = self.runtime / "logs" / "orchestrator.jsonl"
        self.assertTrue(log_path.is_file(), combined)
        records = [json.loads(line) for line in log_path.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        self.assertIn("Orchestrator v2 started", [item.get("message") for item in records])
        self.assertIn(
            "control/STOP detected before startup; Codex was not invoked",
            [item.get("message") for item in records],
        )
        runtime_state = json.loads((
            self.runtime / "control" / "orchestrator_runtime.json"
        ).read_text(encoding="utf-8"))
        self.assertEqual(runtime_state["status"], "STOPPED_BY_USER")
        self.assertTrue((self.runtime / "control" / "supervisor_control.json").is_file())


if __name__ == "__main__":
    unittest.main()
