"""P6 Artifact Center static frontend tests (offline).

Pins the dedicated Artifact Center workspace of the single-file Cockpit
frontend without a browser: structural markers, search/filter/pagination
controls, preview and provenance presentation, the artifact-feedback form
that reuses the P5 intervention surface, per-Runtime state reset, safe
text-only rendering (no innerHTML/eval/document.write), no external
resources, and well-formed HTML. The P1–P5 anchors stay pinned by their own
suites; this file only adds the P6 surface and updates nothing else.
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
            self.errors.append(f"mismatched close for {tag!r}")
            return
        self.stack.pop()


class ArtifactCenterStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INDEX.read_text(encoding="utf-8")

    def test_html_is_well_formed(self):
        checker = WellFormedChecker()
        checker.feed(self.text)
        checker.close()
        self.assertEqual(checker.errors, [])
        self.assertEqual(checker.stack, [])

    def test_artifact_center_section_is_live(self):
        self.assertIn('id="artifact-center-section"', self.text)
        self.assertNotIn("artifacts-placeholder-section", self.text)
        self.assertNotIn(
            "Artifact browsing and previews arrive with the Artifact Center",
            self.text)

    def test_artifact_list_and_status_markers(self):
        for marker in ('id="ac-list"', 'id="ac-status"',
                       'id="ac-page-indicator"'):
            self.assertIn(marker, self.text)

    def test_search_and_required_filters_exist(self):
        for marker in ('id="ac-root"', 'id="ac-type"', 'id="ac-status-filter"',
                       'id="ac-message"', 'id="ac-task"', 'id="ac-search"',
                       'id="ac-apply"', 'id="ac-refresh"'):
            self.assertIn(marker, self.text)

    def test_filter_value_spaces_match_the_api(self):
        for value in ("workspace", "evidence", "reports"):
            self.assertIn(f'value="{value}"', self.text)
        for value in ("markdown", "text", "log", "code", "png", "jpeg",
                      "webp", "csv", "json", "pdf", "unsupported"):
            self.assertIn(f'value="{value}"', self.text)
        for value in ("ledger", "unbound", "missing"):
            self.assertIn(f'value="{value}"', self.text)

    def test_pagination_and_bounded_inputs(self):
        for marker in ('id="ac-prev"', 'id="ac-next"', 'id="ac-page-size"'):
            self.assertIn(marker, self.text)
        self.assertRegex(self.text, r'id="ac-search"[^>]*maxlength="120"')
        self.assertRegex(self.text, r'id="ac-task"[^>]*maxlength="120"')
        self.assertRegex(self.text, r'id="ac-message"[^>]*max="999999999"')

    def test_preview_states_are_presented_honestly(self):
        for phrase in ("Preview unavailable", "Unsupported format",
                       "TRUNCATED", "NOT_UTF8", "FORMAT_MISMATCH",
                       "read-only"):
            self.assertIn(phrase, self.text)

    def test_provenance_presentation_markers(self):
        for phrase in ("Producing MESSAGE", "Provenance", "completion status",
                       "unbound", "Publication SHA-256", "Current SHA-256",
                       "Completion ID", "Historical bytes are not retained"):
            self.assertIn(phrase, self.text)

    def test_navigation_links_to_timeline_detail(self):
        self.assertIn("Open Task Detail", self.text)
        self.assertIn("/rounds/", self.text)

    def test_artifact_feedback_form_reuses_intervention_machinery(self):
        for marker in ('id="ac-fb-mode"', 'id="ac-fb-comment"',
                       'id="ac-fb-submit"', 'id="ac-fb-confirm"',
                       'id="ac-fb-confirm-button"',
                       'id="ac-fb-cancel-button"'):
            self.assertIn(marker, self.text)
        self.assertIn('value="STEER"', self.text)
        self.assertIn('value="AUDIT"', self.text)
        self.assertIn("artifacts/feedback", self.text)
        self.assertRegex(self.text, r'id="ac-fb-comment"[^>]*maxlength="4000"')

    def test_feedback_explains_binding_and_no_dumping(self):
        self.assertIn("Give feedback to Codex", self.text)
        self.assertIn("Deep Review", self.text)
        self.assertIn("references", self.text.lower())

    def test_artifact_detail_images_use_same_origin_raw_route(self):
        self.assertIn("/raw", self.text)
        self.assertNotIn('src="http', self.text)


class ArtifactCenterSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INDEX.read_text(encoding="utf-8")

    def test_dynamic_rendering_is_text_only(self):
        for banned in ("innerHTML", "outerHTML", "document.write",
                       "eval(", "new Function", "insertAdjacentHTML"):
            self.assertNotIn(banned, self.text)

    def test_no_inline_event_handler_attributes(self):
        self.assertIsNone(re.search(r"\son[a-z]+\s*=", self.text),
                          "inline event handler attribute found")

    def test_no_external_resources(self):
        for pattern in (r'src\s*=\s*"', r'href\s*=\s*"', r"@import",
                        r"url\(\s*[\"']?https?:", r"fetch\(\s*[\"']https?:"):
            self.assertIsNone(re.search(pattern, self.text),
                              f"external resource pattern {pattern!r} found")

    def test_requests_are_same_origin(self):
        self.assertIn('cache: "no-store"', self.text)

    def test_runtime_switch_resets_artifact_state(self):
        self.assertIn("resetArtifactCenter", self.text)

    def test_artifact_polling_is_user_driven_not_forced(self):
        self.assertNotIn("setInterval(loadArtifacts", self.text)


class RetainedPanelsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = INDEX.read_text(encoding="utf-8")

    def test_p1_to_p5_anchors_are_retained(self):
        for marker in ("ce-state-label", "tl-rounds", "rh-orchestrator",
                       "runtime-list", "human-control-section",
                       "pending-controls-section", "task-drawer",
                       "cockpit-grid"):
            self.assertIn(marker, self.text)

    def test_task_drawer_artifacts_tab_links_to_artifact_center(self):
        self.assertIn("Open in Artifact Center", self.text)


if __name__ == "__main__":
    unittest.main()
