"""Tests for the local ZCode Scheduled Automation setup helper.

All execution happens in isolated temporary Runtime fixtures. Tests use
``-NoClipboard`` and never launch Runtime components or modify live Runtime state.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


CANDIDATE = Path(__file__).resolve().parents[1]
HELPER = CANDIDATE / "PREPARE_ZCODE_AUTOMATION.ps1"
POWERSHELL = shutil.which("powershell.exe")
PLACEHOLDER = "<RUNTIME_ROOT>"
LIVE_ARTIFACTS = (
    "TO_ZCODE.md",
    "SUPERVISOR_BRIEF.md",
    "ZCODE_DONE.flag",
    "control/ACTIVE_PROJECT.json",
)


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ps_quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def normalized(value: str) -> str:
    """Collapse host-inserted line wrapping in Windows PowerShell diagnostics."""
    return " ".join(value.split())


def compact(value: str) -> str:
    return "".join(value.split())


@unittest.skipUnless(POWERSHELL, "Windows PowerShell is required")
class PrepareZCodeAutomationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="prepare-zcode-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Runtime Root"
        (self.root / "control").mkdir(parents=True)
        (self.root / "START_AGENT_SYSTEM.ps1").write_text(
            "Set-Content -LiteralPath launcher-ran.marker -Value ran\n",
            encoding="utf-8",
        )
        (self.root / "orchestrator.py").write_text(
            "from pathlib import Path\nPath('orchestrator-ran.marker').write_text('ran')\n",
            encoding="utf-8",
        )
        self.prompt = self.root / "control" / "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md"
        self.prompt.write_text(
            "Executor workspace: <RUNTIME_ROOT>\nUnicode: 漢字 café\n",
            encoding="utf-8",
        )

    def run_helper(
        self,
        *,
        helper: Path = HELPER,
        root: Path | None = None,
        output_prompt: bool = False,
        shadow_clipboard: bool = False,
        no_clipboard: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        args = [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
        ]
        switches = " -NoClipboard" if no_clipboard else ""
        if root is not None:
            switches += f" -RuntimeRoot {ps_quote(root)}"
        if output_prompt:
            switches += " -OutputPrompt"
        if shadow_clipboard:
            command = (
                "function Set-Clipboard { throw 'Set-Clipboard must not run' }; "
                f"& {ps_quote(helper)}{switches}"
            )
            args.extend(["-Command", command])
        else:
            args.extend(["-File", str(helper)])
            if root is not None:
                args.extend(["-RuntimeRoot", str(root)])
            if no_clipboard:
                args.append("-NoClipboard")
            if output_prompt:
                args.append("-OutputPrompt")
        return subprocess.run(
            args,
            cwd=self.root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

    def assert_runtime_was_not_started(self) -> None:
        self.assertFalse((self.root / "launcher-ran.marker").exists())
        self.assertFalse((self.root / "orchestrator-ran.marker").exists())
        for relative in LIVE_ARTIFACTS:
            self.assertFalse((self.root / relative).exists(), relative)

    def test_default_runtime_root_reads_and_renders_prompt_without_mutation(self) -> None:
        local_helper = self.root / HELPER.name
        shutil.copy2(HELPER, local_helper)
        before = file_sha256(self.prompt)

        result = self.run_helper(helper=local_helper, output_prompt=True)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Workspace:\n{self.root}", result.stdout)
        self.assertIn(f"Executor workspace: {self.root}", result.stdout)
        self.assertNotIn(PLACEHOLDER, result.stdout)
        self.assertEqual(file_sha256(self.prompt), before)
        self.assert_runtime_was_not_started()

    def test_explicit_runtime_root_is_selected(self) -> None:
        result = self.run_helper(root=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(f"Workspace:\n{self.root}", result.stdout)
        self.assertIn("Clipboard copy skipped (-NoClipboard).", result.stdout)

    def test_all_placeholders_are_replaced(self) -> None:
        self.prompt.write_text(
            "first=<RUNTIME_ROOT>\nsecond=<RUNTIME_ROOT>\nthird=<RUNTIME_ROOT>\n",
            encoding="utf-8",
        )
        result = self.run_helper(root=self.root, output_prompt=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn(PLACEHOLDER, result.stdout)
        self.assertEqual(result.stdout.count(str(self.root)), 4)  # workspace + three prompt values

    def test_missing_prompt_fails_clearly(self) -> None:
        self.prompt.unlink()
        result = self.run_helper(root=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Canonical Executor prompt template is missing", normalized(result.stderr))
        self.assert_runtime_was_not_started()

    def test_prompt_without_placeholder_fails_clearly_and_is_unchanged(self) -> None:
        self.prompt.write_text("No binding token here.\n", encoding="utf-8")
        before = file_sha256(self.prompt)
        result = self.run_helper(root=self.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("contains no literal", result.stderr)
        self.assertEqual(file_sha256(self.prompt), before)

    def test_invalid_runtime_root_fails_clearly(self) -> None:
        invalid = Path(self.temp.name) / "not-a-runtime"
        (invalid / "control").mkdir(parents=True)
        shutil.copy2(self.prompt, invalid / "control" / self.prompt.name)
        result = self.run_helper(root=invalid)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("required file is missing", normalized(result.stderr))

    def test_no_clipboard_does_not_invoke_set_clipboard(self) -> None:
        result = self.run_helper(root=self.root, shadow_clipboard=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("Clipboard copy skipped (-NoClipboard).", result.stdout)
        self.assertNotIn("must not run", result.stderr)

    def test_clipboard_failure_fails_without_claiming_success(self) -> None:
        result = self.run_helper(
            root=self.root,
            shadow_clipboard=True,
            no_clipboard=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Clipboardwritefailed", compact(result.stderr))
        self.assertNotIn("Copied to clipboard.", result.stdout)
        self.assert_runtime_was_not_started()

    def test_helper_creates_no_live_state_and_starts_no_runtime_component(self) -> None:
        result = self.run_helper(root=self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_runtime_was_not_started()


if __name__ == "__main__":
    unittest.main()
