"""P3 Web Console static frontend tests (offline, no browser).

The Cockpit is a single same-origin HTML file with no external resources.
These tests pin the P3 Cockpit foundation statically: three-column layout,
explicit Runtime selector, Current Execution and Runtime Health regions,
Next Expected and user-action presentation, clearly non-functional
placeholders for deferred regions, safe text-only rendering (no
innerHTML/eval/external sources), polling, and the P1 anchors that must
remain (DOCTYPE, charset, no external dependencies, /api/health, /api/status).
"""
from __future__ import annotations

import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

INDEX = REPO / "web_console" / "index.html"


class WellFormedness(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "input", "link", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack:
            self.errors.append(f"closing </{tag}> with empty stack")
        elif self.stack[-1] != tag:
            self.errors.append(
                f"closing </{tag}> does not match open <{self.stack[-1]}>")
            self.stack.pop()
        else:
            self.stack.pop()


class StaticFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INDEX.read_text(encoding="utf-8")

    def test_index_exists_with_p1_anchors(self):
        self.assertTrue(INDEX.is_file())
        self.assertIn("<!DOCTYPE html>", self.text)
        self.assertIn('<meta charset="utf-8">', self.text)
        self.assertIn("General Agent Runtime v1.3", self.text)

    def test_no_external_dependencies(self):
        self.assertNotIn("http://", self.text)
        self.assertNotIn("https://", self.text)
        self.assertNotIn("src=", self.text)
        self.assertNotIn("@import", self.text)

    def test_three_column_cockpit_layout_is_present(self):
        for marker in ("cockpit-grid", "column-runtimes", "column-current",
                       "column-control"):
            self.assertIn(marker, self.text, marker)

    def test_runtime_selector_present_and_opaque_id_backed(self):
        self.assertIn('id="runtime-list"', self.text)
        self.assertIn("/api/runtimes", self.text)
        self.assertIn("/cockpit", self.text)

    def test_current_execution_region_presents_required_answers(self):
        for marker in ("current-execution", "ce-state-label", "ce-worker",
                       "ce-since", "ce-next-label", "ce-action",
                       "ce-milestones", "ce-protocol"):
            self.assertIn(marker, self.text, marker)

    def test_runtime_health_region_lists_bounded_facts(self):
        for marker in ("runtime-health", "rh-orchestrator", "rh-codex",
                       "rh-zcode", "rh-authorization", "rh-claim",
                       "rh-interventions", "rh-errors"):
            self.assertIn(marker, self.text, marker)

    def test_deferred_regions_are_clearly_nonfunctional_placeholders(self):
        # P7 turned the New Project placeholder into the live Project Setup
        # wizard (documented pin update, same precedent as the P5/P6 pin
        # updates); the placeholder styling itself is still in use.
        self.assertIn("Project Setup", self.text)
        self.assertIn("Timeline", self.text)
        self.assertIn("placeholder", self.text.lower())

    def test_rendering_is_text_only_and_safe(self):
        self.assertNotIn("innerHTML", self.text)
        self.assertNotIn("outerHTML", self.text)
        self.assertNotIn("document.write", self.text)
        self.assertNotIn("eval(", self.text)
        self.assertIn("textContent", self.text)

    def test_cockpit_polls_readonly_endpoints(self):
        self.assertIn("setInterval", self.text)
        self.assertIn("cache: \"no-store\"", self.text)

    def test_terminal_facts_use_validated_interpretation_and_clear_on_reload(self):
        self.assertIn('<dl id="ce-terminal" hidden>', self.text)
        self.assertIn('terminalPanel.hidden = !terminal.available', self.text)
        self.assertIn('terminal.final_verification_status === "PASS"', self.text)
        self.assertIn('terminal.last_consumed_message_id', self.text)
        load = self.text.split('async function loadCockpit()', 1)[1]
        self.assertLess(load.index('document.getElementById("ce-terminal").hidden = true'),
                        load.index('await fetchJson('))

    def test_html_is_well_formed(self):
        parser = WellFormedness()
        parser.feed(self.text)
        self.assertEqual(parser.errors, [])
        self.assertEqual(parser.stack, [])

    def test_footer_keeps_p1_api_references(self):
        self.assertIn("/api/health", self.text)
        self.assertIn("/api/status", self.text)


if __name__ == "__main__":
    unittest.main()
