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
import shutil
import subprocess
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
        # Unified, non-misleading input copy (2026-09-16 wizard UX cleanup).
        self.assertRegex(self.text, r'Register\s+Project\s+Inputs')
        self.assertRegex(self.text, r'No\s+Additional\s+Inputs')
        # Honesty: register existing material only; empty is a valid answer.
        self.assertIn("never\n          invent inputs for completeness",
                      self.text)

    def test_goal_workshop_folds_and_is_marked_optional(self):
        # The Workshop is a collapsible optional helper: visible while no
        # Goal is validated, collapsed by default afterwards. The fold is
        # driven by updateWorkshopMode; the summary always carries the
        # Optional badge so the tool is never mistaken for a requirement.
        for marker in ("setup-workshop-details", "setup-workshop-mode-note",
                       "updateWorkshopMode", 'class="setup-badge optional"'):
            self.assertIn(marker, self.text, marker)
        self.assertIn("Optional", self.text)

    def test_step_status_rows_exist_for_all_steps(self):
        for marker in ("setup-status-step-1", "setup-status-step-2",
                       "setup-status-step-3", "setup-status-step-4",
                       "setup-badge", "renderSetupStepStatuses"):
            self.assertIn(marker, self.text, marker)

    def test_step2_distinguishes_draft_from_actual_baseline(self):
        for marker in ("setup-sup-baseline", "setup-sup-baseline-note",
                       "loadSetupSupervisorBaseline"):
            self.assertIn(marker, self.text, marker)
        self.assertIn("Current Runtime baseline (read-only)", self.text)
        # The baseline read goes through the existing read-only config view.
        self.assertRegex(
            self.text,
            r'supervisor/config"\);[\s\S]{0,900}fixed_policy')

    def test_step2_supervisor_controls_and_honesty(self):
        for marker in ("setup-sup-model", "setup-sup-effort",
                       "setup-sup-mode", "setup-sup-save",
                       "setup-sup-note"):
            self.assertIn(marker, self.text, marker)
        # The step-2 model and effort pickers are populated by the shared
        # fixed product menu helper; the old free-suggestion effort option
        # list must be gone.
        self.assertNotIn('value="EXTRA_HIGH"', self.text)
        self.assertNotIn('value="HIGHEST"', self.text)
        self.assertNotIn('value="ULTRA"', self.text)
        self.assertRegex(
            self.text,
            r'fillSupervisorModelSelect\(document\.getElementById'
            r'\("setup-sup-model"\)')
        self.assertRegex(
            self.text,
            r'fillSupervisorEffortSelect\(document\.getElementById'
            r'\("setup-sup-effort"\)')
        self.assertIn("Not chosen yet", self.text)
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


class WizardBehaviorTests(unittest.TestCase):
    """Behavior checks for the wizard fold/status logic, run in Node against
    the real functions extracted from index.html (same harness precedent as
    the human-control rendering tests)."""

    @classmethod
    def setUpClass(cls):
        cls.text = index_text()
        cls.node = shutil.which("node")

    def harness(self) -> str:
        state = self.text.split("let setupGoalValidated = false;", 1)[1]
        functions = state.split("function setSetupStatus", 1)[0]
        return r'''
const assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this._text = '';
    this.open = false; this.className = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
}
const nodes = new Map();
const document = {
  getElementById(id) { if (!nodes.has(id)) nodes.set(id, new Element('div')); return nodes.get(id); },
  createElement(tag) { return new Element(tag); },
  createTextNode(text) { const n = new Element('#text'); n._text = String(text); return n; },
};
function clear(n) { n.textContent = ''; }
let uiLanguage = 'zh-CN';
function productCopy(zh, en) { return uiLanguage === 'en' ? en : zh; }
let setupGoalValidated = false;
''' + functions

    @unittest.skipUnless(shutil.which("node"), "Node is required for behavior tests")
    def test_workshop_open_without_goal_collapsed_after_validation(self):
        harness = self.harness() + r'''
const details = document.getElementById('setup-workshop-details');
const note = document.getElementById('setup-workshop-mode-note');
// No validated Goal: the optional helper stays visible for drafting.
updateWorkshopMode(false);
assert.equal(details.open, true);
assert.ok(note.textContent.includes('可选辅助'), note.textContent);
// Validated Goal: collapsed by default and explicitly re-draft-only.
updateWorkshopMode(true);
assert.equal(details.open, false);
assert.ok(note.textContent.includes('默认收起'), note.textContent);
assert.ok(note.textContent.includes('重新起草'), note.textContent);
uiLanguage = 'en';
updateWorkshopMode(true);
assert.ok(note.textContent.includes('stays collapsed by default'), note.textContent);
'''
        result = subprocess.run(["node"], input=harness, text=True,
                                encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node is required for behavior tests")
    def test_step_status_labels_track_real_wizard_state(self):
        harness = self.harness() + r'''
renderSetupStepStatuses();
const s1 = document.getElementById('setup-status-step-1').textContent;
assert.ok(s1.includes('必需') && s1.includes('可选'), s1);
assert.ok(s1.includes('Goal') && s1.includes('Workshop'), s1);
assert.ok(document.getElementById('setup-status-step-2').textContent.includes('仅草稿'));
assert.ok(document.getElementById('setup-status-step-3').textContent.includes('需手工配置'));
assert.ok(document.getElementById('setup-status-step-4').textContent.includes('尚未运行'));
// Real recorded state flips the labels — nothing is fabricated.
setupGoalValidated = true;
setupInputsState = {decision: 'NONE_NEEDED'};
setupZcodeAcknowledged = true;
setupReadinessState = true;
renderSetupStepStatuses();
const done1 = document.getElementById('setup-status-step-1').textContent;
assert.ok(done1.includes('已验证'), done1);
assert.ok(done1.includes('已记录') && done1.includes('不需要额外输入'), done1);
assert.ok(document.getElementById('setup-status-step-3').textContent.includes('已确认（人工）'));
assert.ok(document.getElementById('setup-status-step-4').textContent.includes('已验证'));
// Registered inputs read differently from none-needed.
setupInputsState = {decision: 'REGISTERED'};
renderSetupStepStatuses();
assert.ok(document.getElementById('setup-status-step-1').textContent.includes('已登记'));
// A failing readiness run is shown as not ready, in English too.
setupReadinessState = false;
uiLanguage = 'en';
renderSetupStepStatuses();
const en4 = document.getElementById('setup-status-step-4').textContent;
assert.ok(en4.includes('Not ready'), en4);
'''
        result = subprocess.run(["node"], input=harness, text=True,
                                encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
