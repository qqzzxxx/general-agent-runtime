"""P5 Human Control static frontend tests.

Pins the Human Control panel, the Pending Controls panel, the high-risk
STOP/interrupt affordances, the HUMAN_REVIEW presentation, and the text-only
rendering invariants of `web_console/index.html`. These are the same static
checks the P3/P4 suites apply, extended to the P5 surface.
"""
from __future__ import annotations

import re
import json
import shutil
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INDEX = REPO / "web_console" / "index.html"


def index_text() -> str:
    return INDEX.read_text(encoding="utf-8")


class RetainedSurfaceTests(unittest.TestCase):
    def test_p1_through_p4_anchors_are_retained(self):
        text = index_text()
        for anchor in (
                'id="runtime-list"', 'id="refresh"', 'id="health-state"',
                'id="ce-state-label"', 'id="ce-detail"', 'id="ce-worker"',
                'id="ce-next-label"', 'id="ce-action"', 'id="ce-milestones"',
                'id="tl-order"', 'id="tl-page-size"', 'id="tl-kind"',
                'id="tl-search"', 'id="tl-rounds"', 'id="tl-pages"',
                'id="task-drawer"', 'id="td-tabs"', 'id="td-close"',
                'id="rh-errors"', 'id="column-runtimes"',
                'id="column-current"', 'id="column-control"',
                'data-tab="overview"', 'data-tab="dispatch"',
                'data-tab="feedback"', 'data-tab="artifacts"',
                'data-tab="context"', 'data-tab="protocol"'):
            self.assertIn(anchor, text, anchor)

    def test_new_project_is_now_the_p7_setup_wizard(self):
        # P7 turned the New Project placeholder into the live four-step
        # Project Setup wizard (documented pin update, same precedent as
        # the P5/P6 pin updates).
        text = index_text()
        self.assertIn("New Project", text)
        self.assertIn("setup-wizard-section", text)

    def test_p5_placeholders_are_gone(self):
        text = index_text()
        self.assertNotIn(
            "Safe Pause/Resume, interventions, and formal STOP remain with "
            "the", text)
        self.assertNotIn(
            "Outstanding pause/intervention requests will be listed here in "
            "a later stage", text)


class HumanControlPanelTests(unittest.TestCase):
    def test_human_control_section_exists_with_safe_controls(self):
        text = index_text()
        for marker in (
                'id="human-control-section"', 'id="hc-pause"',
                'id="hc-pause-interrupt"', 'id="hc-pause-confirm"',
                'id="hc-pause-effect"', 'id="hc-resume"',
                'id="hc-resume-confirm"', 'id="hc-feedback"'):
            self.assertIn(marker, text, marker)
        self.assertIn("Safe Pause", text)
        self.assertIn("Resume", text)

    def test_pause_explains_expected_effect(self):
        text = index_text()
        self.assertIn("current valid task may finish", text)
        self.assertIn("PAUSED", text)

    def test_intervention_form_is_bounded_and_typed(self):
        text = index_text()
        for marker in ('id="hc-int-form"', 'id="hc-int-mode"',
                       'id="hc-int-comment"', 'id="hc-int-target"',
                       'id="hc-int-message-id"', 'id="hc-int-interrupt"',
                       'id="hc-int-submit"', 'id="hc-int-confirm"'):
            self.assertIn(marker, text, marker)
        self.assertIn('maxlength="4000"', text)
        self.assertIn("STEER", text)
        self.assertIn("AUDIT", text)
        self.assertIn("Historical MESSAGE_ID", text)

    def test_high_risk_area_is_separate_and_visually_distinct(self):
        text = index_text()
        for marker in ('id="hc-highrisk"', 'id="hc-interrupt-task"',
                       'id="hc-stop-prepare"', 'id="hc-stop-challenge"',
                       'id="hc-stop-token"', 'id="hc-stop-project"',
                       'id="hc-stop-confirm"', "hc-danger"):
            self.assertIn(marker, text, marker)
        self.assertIn("Formal STOP", text)
        self.assertIn("terminal", text)

    def test_stop_explains_terminal_impact(self):
        text = index_text()
        self.assertIn("Resume will not", text)
        self.assertIn("fresh authorization", text)

    def test_stop_confirmation_is_bound_to_token_and_project(self):
        text = index_text()
        self.assertIn("Confirmation token", text)
        self.assertIn("Project id", text)

    def test_interrupt_explains_cooperative_semantics(self):
        text = index_text()
        self.assertIn("does not kill", text)
        self.assertIn("paused", text)


class HumanReviewPanelTests(unittest.TestCase):
    def test_human_review_presentation_exists(self):
        text = index_text()
        for marker in ('id="hc-review-block"', 'id="hc-review-reason"',
                       'id="hc-review-facts"', 'id="hc-review-prepare-button"',
                       'id="hc-review-form"', 'id="hc-review-content"',
                       'id="hc-review-constraints"',
                       'id="hc-review-prepared"',
                       'id="hc-review-apply-button"'):
            self.assertIn(marker, text, marker)

    def test_human_review_states_non_automatic_resume(self):
        text = index_text()
        normalized = " ".join(text.split())
        self.assertIn("does not resume automatically", normalized)
        self.assertIn("View", text)

    def test_decision_inputs_are_bounded(self):
        text = index_text()
        self.assertIn('maxlength="24000"', text)
        self.assertIn("one per line", text)


class PendingControlsPanelTests(unittest.TestCase):
    def test_pending_controls_section_exists(self):
        text = index_text()
        for marker in ('id="pending-controls-section"', 'id="pc-list"',
                       'id="pc-status"', 'id="pc-refresh"'):
            self.assertIn(marker, text, marker)

    def test_control_state_vocabulary_is_present(self):
        text = index_text()
        for label in ("pending", "consumed", "superseded", "failed",
                      "needs recovery", "unavailable"):
            self.assertIn(label, text, label)


class ApiContractTests(unittest.TestCase):
    def test_frontend_calls_the_p5_endpoints(self):
        text = index_text()
        for endpoint in (
                "/controls", "/controls/pause", "/controls/resume",
                "/controls/intervention", "/controls/stop/prepare",
                "/controls/stop/confirm",
                "/controls/human-review/prepare",
                "/controls/human-review/apply"):
            self.assertIn(f'"{endpoint}"', text, endpoint)

    def test_all_requests_are_same_origin_relative_paths(self):
        text = index_text()
        for match in re.finditer(r'"([^"]*)"', text):
            url = match.group(1)
            self.assertFalse(url.startswith("http://"), url)
            self.assertFalse(url.startswith("https://"), url)
        # The request helper is the only fetch call site.
        self.assertEqual(text.count("fetch("), 1)


class RenderingSafetyTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is required for DOM rendering smoke test")
    def test_review_reason_renders_summary_and_literal_diagnostics_in_both_languages(self):
        source = index_text()
        script = source.split("<script>", 1)[1].split("</script>", 1)[0]
        checked = subprocess.run(["node", "--check"], input=script, text=True,
                                 encoding="utf-8", capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        function = source.split("function renderHumanReview(review) {", 1)[1].split(
            'document.getElementById("hc-review-prepare-button").addEventListener', 1)[0]
        harness = r'''
const assert = require('node:assert/strict');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }
  append(...children) { this.children.push(...children); }
}
const nodes = new Map();
const document = {getElementById(id) { if (!nodes.has(id)) nodes.set(id,new Element('div')); return nodes.get(id); }};
let uiLanguage = 'zh-CN';
function el(tag, cls, text) { const n = new Element(tag); if(text !== undefined)n.textContent=text; return n; }
function clear(n) { n.textContent = ''; }
function productCopy(zh,en) {return uiLanguage === 'en' ? en : zh;}
'''
        harness += "\nfunction renderHumanReview(review) {" + function
        harness += r'''
const review = {active:true, authorized_task:{present:false}, project_status:'HUMAN_REVIEW',
  reason:{available:true, summary:'最终验证在授权前失败，需要人工决策。',
          summary_en:'Verification failed before authorization; human review required.',
          raw_error:'<img src=x onerror=alert(1)> EXECUTION_MODE REVERIFY', truncated:false}};
for (const lang of ['zh-CN','en']) {
  uiLanguage = lang;
  renderHumanReview(review);
  renderHumanReview(review);
  const reason = nodes.get('hc-review-reason');
  assert(reason._text.includes(lang === 'en' ? 'before authorization' : '授权前'));
  assert.equal(reason.children.length,1);
  assert.equal(reason.children[0].tag,'details');
  assert.equal(reason.children[0].children[1].tag,'pre');
  assert.equal(reason.children[0].children[1].textContent,review.reason.raw_error);
}
renderHumanReview({active:false});
assert.equal(nodes.get('hc-review-block').hidden,true);
'''
        result = subprocess.run(["node"], input=harness, text=True, encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_html_injection_primitives(self):
        text = index_text()
        for forbidden in ("innerHTML", "document.write", "outerHTML",
                          "insertAdjacentHTML"):
            self.assertNotIn(forbidden, text, forbidden)
        self.assertNotIn("eval(", text)
        self.assertNotIn("new Function", text)

    def test_no_inline_event_handlers(self):
        text = index_text()
        for pattern in ("onclick=", "onchange=", "onsubmit=", "oninput=",
                        "onload=", "onerror="):
            self.assertNotIn(pattern, text, pattern)

    def test_no_external_resources(self):
        text = index_text()
        self.assertNotIn("<script src=", text)
        self.assertNotIn("<link ", text)
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)

    def test_dynamic_values_render_as_text(self):
        text = index_text()
        self.assertIn("textContent", text)
        self.assertIn("replaceChildren", text)


if __name__ == "__main__":
    unittest.main()
