"""P4 Web Console frontend static tests (offline).

Pins the Timeline panel and Task Detail drawer of the single-file Cockpit
frontend without a browser: structural markers, the six Task Detail tabs,
timeline controls (order/page-size/kind/search/pagination/live-refresh),
safe text-only rendering (no innerHTML/eval/document.write), no external
resources, well-formed HTML, and retained P1/P3 anchors. The deferred-region
pin (updated once at P5 and once at P6) now covers only the New Project
placeholder; the Human Control, Pending Controls, and Artifact Center panels
are live and pinned by their own suites.
"""
from __future__ import annotations

import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "web_console" / "index.html"


class WellFormedChecker(HTMLParser):
    VOID = {"meta", "br", "hr", "img", "input", "link", "wbr", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if not self.stack or self.stack[-1] != tag:
            self.errors.append(f"mismatched close for {tag!r} "
                               f"(stack top {self.stack[-1] if self.stack else None!r})")
            return
        self.stack.pop()


class FrontendSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INDEX.read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue(INDEX.is_file())

    def test_html_is_well_formed(self):
        checker = WellFormedChecker()
        checker.feed(self.text)
        checker.close()
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])

    def test_document_anchors_preserved(self):
        self.assertTrue(self.text.lstrip().startswith("<!DOCTYPE html>"))
        self.assertIn('<meta charset="utf-8">', self.text)
        self.assertIn('name="viewport"', self.text)
        self.assertIn("<title>General Agent Runtime v1.4 Dev", self.text)
        self.assertIn("/api/health", self.text)
        self.assertIn("/api/status", self.text)

    def test_no_external_resources(self):
        for pattern in (r'src\s*=\s*"', r'href\s*=\s*"', r"@import",
                        r"url\(\s*[\"']?https?:", r"fetch\(\s*[\"']https?:"):
            self.assertIsNone(re.search(pattern, self.text),
                              f"external resource pattern {pattern!r} found")

    def test_dynamic_rendering_is_text_only(self):
        for banned in ("innerHTML", "outerHTML", "document.write",
                       "eval(", "new Function", "insertAdjacentHTML"):
            self.assertNotIn(banned, self.text)

    def test_no_inline_event_handler_attributes(self):
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", self.text),
                          "inline event handler attribute found")


class TimelinePanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INDEX.read_text(encoding="utf-8")

    def test_timeline_section_exists_and_is_live(self):
        self.assertIn('id="timeline-section"', self.text)
        self.assertIn('id="tl-rounds"', self.text)

    def test_timeline_controls_exist(self):
        for marker in ('id="tl-order"', 'id="tl-page-size"', 'id="tl-kind"',
                       'id="tl-search"', 'id="tl-search-apply"',
                       'id="tl-refresh"', 'id="tl-prev"', 'id="tl-next"',
                       'id="tl-page-indicator"'):
            self.assertIn(marker, self.text)

    def test_order_and_kind_value_spaces_match_the_api(self):
        self.assertIn('value="newest"', self.text)
        self.assertIn('value="oldest"', self.text)
        self.assertIn('value="all"', self.text)
        self.assertIn('value="dispatch"', self.text)
        self.assertIn('value="completion"', self.text)
        self.assertIn('value="intervention"', self.text)

    def test_search_input_is_bounded_like_the_api(self):
        self.assertRegex(self.text, r'id="tl-search"[^>]*maxlength="120"')

    def test_pagination_and_page_size_bounds(self):
        for size in ('value="10"', 'value="20"', 'value="50"'):
            self.assertIn(size, self.text)
        self.assertIn("tlPageSize", self.text)

    def test_new_events_banner_exists_and_starts_hidden(self):
        self.assertIn('id="tl-new-events"', self.text)
        self.assertIn("New events available", self.text)
        self.assertIn('id="tl-jump-latest"', self.text)

    def test_timeline_polling_and_endpoint_use(self):
        self.assertIn("/timeline", self.text)
        self.assertRegex(self.text, r"TIMELINE_POLL_MS\s*=\s*\d+")

    def test_timeline_states_are_honest(self):
        for phrase in ("No rounds are recorded", "unavailable", "Loading"):
            self.assertIn(phrase, self.text)

    def test_old_timeline_placeholder_is_gone(self):
        self.assertNotIn("timeline-placeholder-section", self.text)
        self.assertNotIn("round history arrives in a later stage", self.text)

    def test_task_detail_drawer_exists(self):
        for marker in ('id="task-drawer"', 'id="td-title"', 'id="td-close"',
                       'id="td-tabs"', 'id="td-body"'):
            self.assertIn(marker, self.text)

    def test_task_detail_has_the_six_spec_tabs(self):
        for tab in ("Overview", "Codex Full Dispatch", "ZCode Feedback",
                    "Artifacts", "Context", "Protocol Details"):
            self.assertIn(tab, self.text)

    def test_dispatch_tab_mentions_exact_archive(self):
        self.assertIn("exact archived", self.text)

    def test_artifact_content_preview_is_deferred_honestly(self):
        self.assertIn("Artifact Center", self.text)
        self.assertIn("metadata", self.text)

    def test_task_detail_fetches_rounds_endpoint(self):
        self.assertIn("/rounds/", self.text)

    def test_deferred_regions_remain_non_functional(self):
        # P5 replaced the Human Control / Pending Controls placeholders,
        # P6 replaced the Artifact Center placeholder, and P7 replaced the
        # New Project placeholder with live panels (each pinned by its own
        # frontend suite); no deferred-region text remains.
        self.assertNotIn("not available in this stage", self.text)
        self.assertNotIn("artifacts-placeholder-section", self.text)

    def test_p3_panels_are_retained(self):
        for marker in ("ce-state-label", "ce-milestones", "ce-protocol",
                       "rh-orchestrator", "rh-zcode", "runtime-list",
                       "cockpit-grid"):
            self.assertIn(marker, self.text)

    def test_drawer_closes_on_escape(self):
        self.assertIn("Escape", self.text)

    def test_drawer_preserves_selection_across_refresh(self):
        self.assertIn("selectionPreserved", self.text)


if __name__ == "__main__":
    unittest.main()
