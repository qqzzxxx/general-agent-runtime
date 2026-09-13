"""P7 Setup Wizard static frontend tests.

Pins the four-step Project Setup Wizard in `web_console/index.html` the same
way the P4/P5/P6 frontend suites pin their panels: structural markers, exact
API endpoints and value spaces, honest capability/state copy, bounded-input
affordances (file selection, drag/drop, path input, copy buttons), the
fail-closed start flow, text-only rendering, and per-Runtime reset. The file
is parsed as text; nothing is executed and no browser is launched.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
INDEX = SCRIPTS.parent / "web_console" / "index.html"


def index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


class SetupWizardStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_setup_section_and_step_markers_exist(self):
        for marker in ("setup-wizard-section", "setup-steps",
                       "setup-step-1", "setup-step-2", "setup-step-3",
                       "setup-step-4", "setup-status"):
            self.assertIn(marker, self.text, marker)

    def test_four_steps_are_titled_per_spec(self):
        for title in ("Goal", "Inputs", "Codex Supervisor configuration",
                      "ZCode Automation setup", "Readiness"):
            self.assertIn(title, self.text, title)

    def test_back_next_navigation_exists(self):
        for marker in ("setup-back", "setup-next", "setupStep"):
            self.assertIn(marker, self.text, marker)

    def test_step1_goal_controls(self):
        for marker in ("setup-goal-file", "setup-goal-drop",
                       "setup-project-id", "setup-project-type",
                       "setup-goal-save", "setup-goal-validation"):
            self.assertIn(marker, self.text, marker)
        self.assertIn('accept=".md,.markdown,.txt"', self.text)

    def test_step1_project_type_value_space_matches_runtime(self):
        for value in ("GENERAL", "ACADEMIC_RESEARCH",
                      "SOFTWARE_ENGINEERING", "BUSINESS_RESEARCH"):
            self.assertIn(f'<option value="{value}"', self.text, value)

    def test_step1_input_path_controls(self):
        for marker in ("setup-input-paths", "setup-inputs-register",
                       "setup-inputs-none", "setup-input-inventory"):
            self.assertIn(marker, self.text, marker)
        self.assertIn("absolute path", self.text)

    def test_step2_supervisor_controls_and_honesty(self):
        for marker in ("setup-sup-model", "setup-sup-effort",
                       "setup-sup-mode", "setup-sup-save",
                       "setup-sup-note"):
            self.assertIn(marker, self.text, marker)
        for effort in ("LOW", "MEDIUM", "HIGH", "EXTRA_HIGH", "HIGHEST",
                       "ULTRA"):
            self.assertIn(f'value="{effort}"', self.text, effort)
        for mode in ("MINIMAL", "COMPACT", "DETAILED_ON_DEMAND"):
            self.assertIn(f'value="{mode}"', self.text, mode)
        self.assertIn("Compact", self.text)
        self.assertIn("draft only", self.text)
        self.assertIn("no active turn was changed", self.text)

    def test_step3_zcode_controls_and_honesty(self):
        for marker in ("setup-zcode-root", "setup-zcode-fetch",
                       "setup-zcode-prompt", "setup-zcode-copy",
                       "setup-zcode-steps", "setup-zcode-ack",
                       "setup-zcode-acknowledge"):
            self.assertIn(marker, self.text, marker)
        self.assertIn("cannot see", self.text)
        self.assertIn("Runtime Root", self.text)

    def test_step4_readiness_and_start(self):
        for marker in ("setup-readiness-run", "setup-readiness-list",
                       "setup-start", "setup-start-confirm",
                       "setup-start-result", "setup-open-cockpit"):
            self.assertIn(marker, self.text, marker)
        for key in ("goal_loaded", "goal_anchor", "inputs_registered",
                    "project_type_valid", "supervisor_config_valid",
                    "runtime_healthy", "python_requirements",
                    "control_plane_v12", "zcode_acknowledged", "preflight"):
            self.assertIn(key, self.text, key)
        self.assertIn("Start Agent Collaboration", self.text)

    def test_goal_workshop_affordances(self):
        for marker in ("setup-workshop-open", "setup-workshop-pack",
                       "setup-workshop-copy", "setup-startup-prompt",
                       "setup-startup-copy"):
            self.assertIn(marker, self.text, marker)
        self.assertIn("copy", self.text.lower())


class SetupWizardSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_start_is_fail_closed_and_confirmed(self):
        self.assertIn("SETUP_NOT_READY", self.text)
        self.assertIn("readiness", self.text)
        confirm_pattern = re.compile(
            r'setup-start-confirm[\s\S]{0,400}setup/start', re.MULTILINE)
        self.assertRegex(self.text, confirm_pattern)

    def test_uses_only_bounded_setup_endpoints(self):
        for endpoint in ("/setup/state", "/setup/goal", "/setup/inputs",
                         "/setup/supervisor", "/setup/zcode/acknowledge",
                         "/setup/zcode", "/setup/goal-workshop",
                         "/setup/readiness", "/setup/start"):
            self.assertIn(endpoint, self.text, endpoint)
        self.assertNotIn("/setup/exec", self.text)
        self.assertNotIn("/setup/shell", self.text)

    def test_no_arbitrary_path_or_command_surface(self):
        self.assertNotIn("eval(", self.text)
        self.assertNotIn("innerHTML", self.text)
        self.assertNotIn("document.write", self.text)
        self.assertNotIn("new Function", self.text)

    def test_no_external_urls_or_resources(self):
        self.assertNotIn("http://", self.text)
        self.assertNotIn("https://", self.text)
        self.assertNotIn('src="', self.text)

    def test_setup_is_user_driven_not_polled(self):
        self.assertNotRegex(
            self.text, r"setInterval\([^)]*setup",
            "the setup wizard must not poll in the background")

    def test_setup_resets_on_runtime_switch(self):
        select_runtime = re.search(
            r"function selectRuntime[\s\S]*?\n}", self.text)
        self.assertIsNotNone(select_runtime)
        self.assertIn("resetSetupWizard()", select_runtime.group(0))

    def test_goal_size_guidance_is_shown(self):
        self.assertIn("256 KiB", self.text)


if __name__ == "__main__":
    unittest.main()
