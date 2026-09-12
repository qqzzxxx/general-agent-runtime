"""P8 Supervisor-turn observability static frontend tests.

Pins the Supervisor observability panel in `web_console/index.html` the same
way the P4–P7 frontend suites pin their panels: structural markers, exact API
endpoints, honest unavailable/queued copy, bounded inputs, text-only
rendering, and per-Runtime reset. The file is parsed as text; nothing is
executed and no browser is launched.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
INDEX = SCRIPTS.parent / "web_console" / "index.html"


def index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


class SupervisorPanelStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_supervisor_panel_markers_exist(self):
        for marker in ("supervisor-section", "sv-latest-turn",
                       "sv-usage-summary", "sv-config-state",
                       "sv-config-model", "sv-config-effort",
                       "sv-config-submit", "sv-config-status",
                       "sv-config-draft"):
            self.assertIn(marker, self.text, marker)

    def test_supervisor_uses_only_bounded_endpoints(self):
        for endpoint in ("/supervisor/turns", "/supervisor/usage",
                         "/supervisor/config"):
            self.assertIn(endpoint, self.text, endpoint)
        self.assertNotIn("/supervisor/exec", self.text)
        self.assertNotIn("/supervisor/shell", self.text)

    def test_usage_honesty_copy_is_pinned(self):
        self.assertIn("Not reported", self.text)
        self.assertIn("usage not reported", self.text)

    def test_config_queue_copy_is_pinned(self):
        self.assertIn("applies next Supervisor turn", self.text)
        self.assertIn("never interrupts an active call", self.text)
        self.assertIn("queued", self.text.lower())

    def test_config_effort_choices_are_the_supported_runtime_set(self):
        section = re.search(
            r'id="sv-config-effort"[\s\S]{0,1200}?</select>', self.text)
        self.assertIsNotNone(section,
                             "the effort selector must exist with its options")
        for effort in ("LOW", "MEDIUM", "HIGH"):
            self.assertIn(f'value="{effort}"', section.group(0), effort)
        self.assertNotIn('value="ULTRA"', section.group(0))
        self.assertNotIn('value="HIGHEST"', section.group(0))

    def test_draft_prefill_is_labelled_non_authoritative(self):
        self.assertIn("setup draft", self.text)
        self.assertIn("non-authoritative", self.text)


class SupervisorPanelSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = index_text()

    def test_text_only_rendering_near_new_panel(self):
        # The whole file keeps the no-innerHTML/eval invariant; the panel
        # adds no new violation surface.
        self.assertNotIn("innerHTML", self.text)
        self.assertNotIn("eval(", self.text)
        self.assertNotIn("document.write", self.text)
        self.assertNotIn("new Function", self.text)

    def test_no_external_urls_or_resources(self):
        self.assertNotIn("http://", self.text)
        self.assertNotIn("https://", self.text)
        self.assertNotIn('src="', self.text)

    def test_config_submit_is_explicit_user_action(self):
        self.assertNotRegex(
            self.text, r"setInterval\([^)]*supervisor/config",
            "configuration submission must be user-driven, never polled")

    def test_supervisor_panel_resets_on_runtime_switch(self):
        select_runtime = re.search(
            r"function selectRuntime[\s\S]*?\n}", self.text)
        self.assertIsNotNone(select_runtime)
        self.assertIn("resetSupervisorPanel()", select_runtime.group(0))

    def test_timeline_rounds_show_turn_observability_when_available(self):
        for marker in ("svTurnForRound", "usage not reported",
                       "Supervisor turn"):
            self.assertIn(marker, self.text, marker)

    def test_drawer_context_tab_includes_turn_observability(self):
        context_tab = re.search(
            r'drawerTab === "context"[\s\S]{0,4000}(?=\n  if \(drawerTab === "protocol"\))',
            self.text)
        self.assertIsNotNone(context_tab)
        body = context_tab.group(0)
        for marker in ("supervisor/turns/", "context_manifest",
                       "usage not reported"):
            self.assertIn(marker, body, marker)


if __name__ == "__main__":
    unittest.main()
