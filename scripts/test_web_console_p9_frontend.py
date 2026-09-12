"""P9 static frontend tests: settings, alerts, notifications, Recent
Artifacts, and the Runtime-create surface in `web_console/index.html`.

Same pinning discipline as the P4–P8 frontend suites: structural markers,
exact bounded endpoints, honest copy, explicit-only browser-notification
permission (one request site, from a user gesture), text-only rendering,
per-Runtime reset, and no external resources or navigation.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
INDEX = SCRIPTS.parent / "web_console" / "index.html"


def index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


class RecentArtifactsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_recent_artifacts_markers_exist(self):
        for marker in ("recent-artifacts-section", "ra-list", "ra-status",
                       "ra-view-all"):
            self.assertIn(marker, self.text, marker)

    def test_view_all_artifacts_copy_is_pinned(self):
        self.assertIn("View all artifacts", self.text)

    def test_recent_artifacts_use_the_p6_catalog_endpoint(self):
        self.assertIn("/artifacts?", self.text)

    def test_message_filtered_navigation_is_wired(self):
        self.assertIn("openArtifactCenter(", self.text)


class AlertsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_alerts_markers_exist(self):
        for marker in ("alerts-section", "alerts-list", "alerts-status"):
            self.assertIn(marker, self.text, marker)

    def test_alerts_endpoint_is_bounded(self):
        self.assertIn("/alerts", self.text)
        self.assertNotIn("/alerts/exec", self.text)
        self.assertNotIn("/alerts/shell", self.text)

    def test_severity_vocabulary_is_presented(self):
        for severity in ("needs-attention", "warning", "informational"):
            self.assertIn(severity, self.text, severity)

    def test_offline_and_unavailable_copy_is_honest(self):
        self.assertIn("unavailable", self.text.lower())


class SettingsPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_settings_markers_exist(self):
        for marker in ("settings-section", "settings-global-save",
                       "settings-runtime-save", "settings-note",
                       "settings-note-save", "settings-note-clear",
                       "settings-note-to-intervention",
                       "settings-global-status", "settings-runtime-status"):
            self.assertIn(marker, self.text, marker)

    def test_settings_endpoints_are_bounded(self):
        self.assertIn("/settings", self.text)
        self.assertIn("/notes", self.text)
        self.assertNotIn("/settings/shell", self.text)

    def test_settings_keys_appear_in_the_client(self):
        for key in ("timeline_page_size", "decision_summary_mode",
                    "supervisor_model", "supervisor_reasoning_effort",
                    "alert_thresholds", "notifications"):
            self.assertIn(key, self.text, key)

    def test_source_labels_are_pinned(self):
        self.assertIn("global-default", self.text)
        self.assertIn("override", self.text)

    def test_queued_config_semantics_copy_is_pinned(self):
        self.assertIn("applies next Supervisor turn", self.text)
        self.assertIn("never interrupts an active call", self.text)

    def test_operator_note_is_labelled_ui_only(self):
        self.assertIn("UI-only", self.text)
        self.assertIn("never enters Codex context automatically", self.text)

    def test_note_conversion_requires_the_formal_intervention_path(self):
        self.assertIn("hc-int-comment", self.text)
        self.assertIn("Convert to intervention", self.text)
        # The note panel must not carry its own submit-to-Codex action.
        self.assertNotIn("settings-note-submit-codex", self.text)


class NotificationSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_permission_is_requested_exactly_once_from_a_gesture(self):
        self.assertEqual(self.text.count("requestPermission"), 1)
        enable_id = "settings-notify-enable"
        self.assertIn(enable_id, self.text)
        click_index = self.text.rfind(enable_id)
        request_index = self.text.find("requestPermission")
        self.assertGreater(request_index, click_index)
        self.assertLess(request_index - click_index, 6000,
                        "permission request must sit in the enable-button "
                        "click handler")

    def test_notification_construction_is_bounded(self):
        self.assertIn("new Notification(", self.text)
        # exactly one request site total (the one pinned above)
        self.assertEqual(len(re.findall(r"\.requestPermission\b", self.text)),
                         1)

    def test_no_forced_navigation_or_external_open(self):
        for forbidden in ("window.open", "location.href", "location.assign",
                          'target="_blank"', "location.replace"):
            self.assertNotIn(forbidden, self.text, forbidden)

    def test_degraded_in_console_attention_state_is_pinned(self):
        self.assertIn("in-Console", self.text)

    def test_notifications_are_deduplicated_by_alert_identity(self):
        self.assertIn("notifiedAlertIds", self.text)

    def test_no_desktop_automation_or_shell_from_the_client(self):
        for forbidden in ("child_process", "powershell", "PowerShell",
                          "WScript", "require("):
            self.assertNotIn(forbidden, self.text, forbidden)


class P9RenderingSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_text_only_rendering_everywhere(self):
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML",
                          "document.write", "eval(", "new Function"):
            self.assertNotIn(forbidden, self.text, forbidden)

    def test_no_external_urls_or_resources(self):
        self.assertNotIn("http://", self.text)
        self.assertNotIn("https://", self.text)


class P9JavaScriptIntegrityTests(unittest.TestCase):
    """The P4-P8 frontend suites pin text only; a latent JavaScript parse
    error (two adjacent string literals with no operator) shipped through
    P8 undetected and was found and fixed during P9. This test pins the
    pattern statically so the Cockpit script can never stop parsing
    again."""

    def test_no_adjacent_string_literals_without_operator(self):
        text = index_text()
        match = re.search(r"<script>(.*)</script>", text, re.S)
        self.assertIsNotNone(match)
        lines = match.group(1).splitlines()
        for i in range(len(lines) - 1):
            stripped = lines[i].rstrip()
            following = lines[i + 1].lstrip()
            if stripped.endswith('"') and not stripped.endswith('+"') \
                    and following.startswith('"'):
                self.fail(
                    "adjacent string literals without a '+' operator at "
                    f"script line {i + 1}: {stripped.strip()[:60]!r}")


class P9RuntimeSwitchResetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_select_runtime_resets_the_new_panels(self):
        for call in ("resetAlertsPanel()", "resetRecentArtifacts()",
                     "resetSettingsPanel()"):
            self.assertIn(call, self.text, call)

    def test_new_panels_define_reset_helpers(self):
        for definition in ("function resetAlertsPanel(",
                           "function resetRecentArtifacts(",
                           "function resetSettingsPanel("):
            self.assertIn(definition, self.text, definition)


class RuntimeCreateFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_create_markers_exist(self):
        for marker in ("runtime-create-section", "rc-template",
                       "rc-destination", "rc-label", "rc-submit",
                       "rc-status"):
            self.assertIn(marker, self.text, marker)

    def test_create_endpoints_are_bounded(self):
        self.assertIn("/api/runtime-templates", self.text)
        self.assertIn("/api/runtimes/create", self.text)

    def test_create_copy_is_explicit_and_warns_against_overwrite(self):
        self.assertIn("supported release", self.text.lower())
        self.assertIn("must not exist", self.text)

    def test_no_source_path_input_exists(self):
        # The browser can never name a template source path.
        self.assertNotIn("rc-source", self.text)


if __name__ == "__main__":
    unittest.main()
