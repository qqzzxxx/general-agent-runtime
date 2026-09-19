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
        self.assertIn("Pause Scheduling", text)
        self.assertIn("Resume", text)

    def test_pause_explains_expected_effect(self):
        text = index_text()
        self.assertIn("current valid task may finish", text)
        self.assertIn("PAUSED", text)

    def test_pause_scheduling_explains_parked_is_not_failed(self):
        # QUOTA-PAUSE-PARK-V1: the default pause is scheduling-only. A
        # dispatched-but-unclaimed task is parked waiting work, not a
        # failure, and it needs no human decision after resume.
        text = index_text()
        self.assertIn('id="hc-parked-note"', text)
        self.assertIn("parked — not failed", text)
        self.assertIn("quota shortage", text)
        self.assertIn(
            "no extra human decision is needed after a quota pause", text)

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

    def test_human_review_states_apply_and_continue_with_safety_gate(self):
        # 2026-09-16 behavior update: a successful Apply now attempts one
        # gated automatic resume (the pre-2026 copy claimed the Runtime
        # "does not resume automatically", which is no longer true). The
        # static copy must express Prepare → Apply-and-continue, the
        # mechanical safety gate, and the one-click resume fallback.
        text = index_text()
        normalized = " ".join(text.split())
        self.assertNotIn("does not resume automatically", normalized)
        self.assertIn("apply and continue", normalized)
        self.assertIn("mechanical safety gate", normalized)
        self.assertIn("one-click Resume", normalized)
        self.assertIn("View", text)

    def test_applied_resume_card_and_retry_exist(self):
        text = index_text()
        for marker in ('id="hc-resume-state"',
                       'id="hc-resume-state-title"',
                       'id="hc-resume-state-text"',
                       'id="hc-resume-state-blockers"',
                       'id="hc-resume-retry"'):
            self.assertIn(marker, text, marker)
        normalized = " ".join(text.split())
        self.assertIn("Decision applied, Runtime not resumed", normalized)
        # The retry action calls the gated resume endpoint and the controls
        # projection feeds the card.
        self.assertIn('"/controls/human-review/resume"', text)
        self.assertIn("renderAppliedResumeState(lastControls.human_review",
                      text)
        self.assertIn("applied.event_pending !== true", text)

    def test_decision_inputs_are_bounded(self):
        text = index_text()
        self.assertIn('maxlength="24000"', text)
        self.assertIn("one per line", text)

    def test_apply_sends_the_exact_prepare_binding_not_dom_scrapes(self):
        # Regression pin (2026-09-15): the Apply button used to scrape the
        # receipt id out of rendered text and post {receipt_id} alone, which
        # the Runtime refused (CONTROL_INVALID_PAYLOAD, missing
        # receipt_sha256) after a successful prepare.
        text = index_text()
        self.assertIn("let preparedHumanDecisions", text)
        self.assertIn("let selectedHumanDecision", text)
        self.assertIn("adoptPreparedHumanDecisions(", text)
        self.assertIn("receipt_id: receipt.receipt_id", text)
        self.assertIn("receipt_sha256: receipt.receipt_sha256", text)
        # The old fragile path must be gone entirely.
        self.assertNotIn("match(/Receipt id:", text)

    def test_prepared_receipt_binding_is_displayed_before_apply(self):
        text = index_text()
        self.assertIn("Receipt SHA-256: ", text)
        self.assertIn("Bound to project-state hash: ", text)
        self.assertIn("hc-prepared-receipt", text)

    def test_controls_document_prepared_list_is_adopted(self):
        text = index_text()
        self.assertIn("Array.isArray(review.prepared)", text)

    def test_prepare_answer_without_receipt_binding_is_refused(self):
        text = index_text()
        self.assertIn(
            "typeof decision.receipt_sha256 !== \"string\"", text)

    def test_intervention_is_disabled_during_human_review(self):
        # The two flows must not be conflated: while HUMAN_REVIEW is active,
        # the ordinary intervention control stays disabled with a pointer to
        # the formal Prepare/Apply decision flow.
        text = index_text()
        self.assertIn(
            'intReason = "HUMAN_REVIEW needs the formal Human Decision flow "',
            text)
        self.assertIn("an intervention cannot resume the Runtime", text)
        normalized = " ".join(text.split())
        self.assertIn("Submit intervention\" is disabled while "
                      "HUMAN_REVIEW is active", normalized)


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
                "/controls/human-review/apply",
                "/controls/human-review/resume"):
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
function hideConfirm(id) { document.getElementById(id).hidden = true; }
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


class DecisionBriefPanelTests(unittest.TestCase):
    """Human Review Decision Brief (2026-09-16 follow-up).

    The review panel must project the authoritative Supervisor decision
    record as the primary surface, fold the ordinary steering/intervention
    area, keep the raw decision JSON viewable, and never generate a
    suggestion the Supervisor did not record.
    """

    def test_decision_brief_card_exists_and_is_rendered_from_controls(self):
        text = index_text()
        self.assertIn('id="hc-review-brief"', text)
        self.assertIn("function renderHumanReviewDecisionBrief(brief)",
                      text)
        self.assertIn("renderHumanReviewDecisionBrief(review.decision_brief)",
                      text)
        # The inactive path must hide the brief so no stale card survives.
        self.assertIn("briefCard.hidden = true;", text)

    def test_brief_sections_cover_the_required_questions(self):
        text = index_text()
        for marker in (
                "Supervisor 决定摘要",
                "发生了什么",
                "仍可信的结果",
                "未通过",
                "现在需要你决定什么",
                "Supervisor 建议",
                "若继续，允许改什么",
                "Supervisor 未提供明确建议",
                "goal_alignment.latest_result",
                "goal_alignment.unmet_criteria",
                "goal_alignment.next_action_alignment",
                "goal_alignment.scope_drift",
                "review.decision_brief"):
            self.assertIn(marker, text, marker)

    def test_raw_decision_json_stays_viewable_for_audit(self):
        text = index_text()
        self.assertIn("查看完整原始决定 JSON", text)
        self.assertIn("View the full raw decision JSON", text)
        self.assertIn("JSON.stringify(brief.raw_decision, null, 2)", text)
        self.assertIn("raw_decision_unavailable_reason", text)

    def test_intervention_area_folds_during_human_review(self):
        text = index_text()
        for marker in ('id="hc-int-details"', 'id="hc-int-summary"',
                       'id="hc-int-fold-note"', 'id="hc-int-form"'):
            self.assertIn(marker, text, marker)
        # The formal decision flow keeps its explicit pointer.
        self.assertIn("interventionFoldWasActive", text)
        self.assertIn("intDetails.open = false;", text)
        self.assertIn("foldNote.hidden = !reviewActive;", text)
        # The original intervention controls and their pins survive.
        for marker in ('id="hc-int-mode"', 'id="hc-int-comment"',
                       'id="hc-int-target"', 'id="hc-int-message-id"',
                       'id="hc-int-interrupt"', 'id="hc-int-submit"',
                       'id="hc-int-confirm"'):
            self.assertIn(marker, text, marker)

    def test_prepare_apply_flow_is_untouched_by_the_brief(self):
        text = index_text()
        self.assertIn('id="hc-review-prepare-button"', text)
        self.assertIn('id="hc-review-apply-button"', text)
        self.assertIn("adoptPreparedHumanDecisions([decision]);", text)
        self.assertIn("receipt_sha256: receipt.receipt_sha256", text)


class DecisionBriefRenderTests(unittest.TestCase):
    """Run the real render functions in Node with a minimal DOM shim."""

    def _harness(self) -> str:
        source = index_text()
        script = source.split("<script>", 1)[1].split("</script>", 1)[0]
        function = source.split("function renderHumanReview(review) {", 1)[
            1].split('document.getElementById("hc-review-prepare-button")'
                     ".addEventListener", 1)[0]
        return (
            "const assert = require('node:assert/strict');\n"
            "class Element {\n"
            "  constructor(tag) { this.tag = tag; this.children = []; "
            "this._text = ''; this.hidden = false; this.open = false; "
            "this._id = null; }\n"
            "  get id() { return this._id; }\n"
            "  set id(value) { this._id = value; if (value) "
            "nodes.set(value, this); }\n"
            "  set textContent(value) { this._text = String(value); "
            "this.children = []; }\n"
            "  get textContent() { return this._text + this.children.map("
            "c => c.textContent).join(''); }\n"
            "  append(...children) { this.children.push(...children); }\n"
            "}\n"
            "const nodes = new Map();\n"
            "const document = {getElementById(id) { if (!nodes.has(id)) "
            "nodes.set(id, new Element('div')); return nodes.get(id); }};\n"
            "let uiLanguage = 'zh-CN';\n"
            "function hideConfirm(id) { document.getElementById(id).hidden "
            "= true; }\n"
            "function el(tag, cls, text) { const n = new Element(tag); "
            "if (text !== undefined) n.textContent = text; return n; }\n"
            "function clear(n) { const purge = (node) => { for (const c of "
            "node.children) { if (c.id) nodes.delete(c.id); purge(c); } };\n"
            "  purge(n); n.textContent = ''; }\n"
            "function productCopy(zh, en) { return uiLanguage === 'en' ? en "
            ": zh; }\n"
            "function feedbackTone() {}\n"
            "\nfunction renderHumanReview(review) {" + function
            + "\nrenderHumanReview;")

    def _brief(self):
        return {
            "schema_version": 1,
            "state_available": True,
            "state_in_human_review": True,
            "inactive_reason": None,
            "available": True,
            "decision_record": {
                "found": True, "source": "decision_history",
                "history_index": 9, "is_latest_history_entry": True,
                "decision": "HUMAN_REVIEW",
                "committed_at": "2026-09-15T20:49:19.658Z",
                "scope": "策略研究对照回归、低频算子偏离及零重试预算下的人工审查",
                "message_id": None, "task_id": None, "stage_id": None},
            "reason": "对照方法错误；规则代码与声明不一致。",
            "trigger_task": {
                "available": True, "source": "previous_authorized_decision",
                "from_decision_index": 8, "message_id": 700106,
                "task_id": "task-79690b7d65b61b6788d25559",
                "stage_id": "stage-6d8a8ece27289efe033e608e",
                "authorized_by": "CONTINUE",
                "authorized_at": "2026-09-15T19:54:13.4351085+00:00"},
            "alignment_available": True, "alignment_malformed": False,
            "goal_alignment": {
                "original_objective": "可复现的多阶段分析研究流程。",
                "unmet_criteria": "关键策略比较暂不可信。",
                "latest_result": "核心结果资格仍有效。",
                "next_action_alignment": "建议一次 2700 秒零重试有限修订。",
                "scope_drift": "不扩大修补范围。",
                "method": "零重试预算已用，不自行追加。"},
            "suggestion": {"available": False, "text": None},
            "truncated_fields": [],
            "raw_decision_available": True,
            "raw_decision_unavailable_reason": None,
            "raw_decision": {"decision": "HUMAN_REVIEW",
                             "reason": "对照方法错误。"},
        }

    def _run(self, harness_body: str):
        harness = self._harness() + "\n" + harness_body + "\n"
        result = subprocess.run(["node"], input=harness, text=True,
                                encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_brief_renders_both_languages_verbatim(self):
        body = r'''
const review = {active: true, decision_brief: BRIEF,
  authorized_task: {present: false}, project_status: 'HUMAN_REVIEW',
  reason: {available: true, summary: 's', summary_en: 's',
           raw_error: 'r', truncated: false}};
for (const lang of ['zh-CN', 'en']) {
  uiLanguage = lang;
  renderHumanReview(review);
  const card = nodes.get('hc-review-brief');
  assert.equal(card.hidden, false);
  const text = card.textContent;
  assert(text.includes(lang === 'en' ? 'What happened' : '发生了什么'));
  assert(text.includes(lang === 'en'
    ? 'What still holds' : '仍可信的结果'));
  assert(text.includes(lang === 'en' ? 'What failed' : '未通过'));
  assert(text.includes(lang === 'en'
    ? 'What needs your decision' : '现在需要你决定什么'));
  assert(text.includes(lang === 'en'
    ? 'Suggested next action' : 'Supervisor 建议'));
  assert(text.includes(lang === 'en'
    ? 'what may change' : '若继续，允许改什么'));
  assert(text.includes(lang === 'en'
    ? 'The Supervisor did not record an explicit suggestion.'
    : 'Supervisor 未提供明确建议。'));
  assert(text.includes('MESSAGE_ID 700106'));
  assert(text.includes('task-79690b7d65b61b6788d25559'));
  // Verbatim projection, never paraphrased.
  assert(text.includes('关键策略比较暂不可信。'));
  assert(text.includes('建议一次 2700 秒零重试有限修订。'));
  // Raw decision JSON stays viewable.
  assert(text.includes('"decision": "HUMAN_REVIEW"'));
  assert(text.includes(lang === 'en'
    ? 'View the full raw decision JSON' : '查看完整原始决定 JSON'));
}
renderHumanReview({active: false});
assert.equal(nodes.get('hc-review-brief').hidden, true);
assert.equal(nodes.get('hc-review-block').hidden, true);
'''
        self._run("const BRIEF = " + json.dumps(self._brief(),
                                                ensure_ascii=False) + ";\n"
                  + body)

    def test_missing_or_stale_brief_hides_the_card(self):
        body = r'''
// Older controls documents without a decision_brief key: the card hides
// instead of rendering an empty or stale brief.
renderHumanReview({active: true, authorized_task: {present: false},
  project_status: 'HUMAN_REVIEW',
  reason: {available: false, truncated: false}});
assert.equal(nodes.get('hc-review-brief').hidden, true);
// A stale/foreign brief (not bound to a HUMAN_REVIEW state) never shows.
renderHumanReviewDecisionBrief(
  Object.assign({}, BRIEF, {state_in_human_review: false}));
assert.equal(nodes.get('hc-review-brief').hidden, true);
// Review active but no structured record: honest unavailability note.
uiLanguage = 'en';
renderHumanReviewDecisionBrief(
  Object.assign({}, BRIEF, {available: false, state_available: true}));
const card = nodes.get('hc-review-brief');
assert.equal(card.hidden, false);
assert(card.textContent.includes('No structured HUMAN_REVIEW decision'));
// Unviewable raw record is reported, not guessed.
renderHumanReviewDecisionBrief(Object.assign({}, BRIEF, {
  raw_decision_available: false,
  raw_decision_unavailable_reason: 'RAW_RECORD_EXCEEDS_DISPLAY_BOUND'}));
assert(card.textContent.includes('RAW_RECORD_EXCEEDS_DISPLAY_BOUND'));
'''
        self._run("const BRIEF = " + json.dumps(self._brief(),
                                                ensure_ascii=False) + ";\n"
                  + body)

    def test_truncation_is_labelled(self):
        brief = self._brief()
        brief["truncated_fields"] = ["decision_record.reason"]
        body = r'''
renderHumanReviewDecisionBrief(BRIEF);
const card = nodes.get('hc-review-brief');
assert(card.textContent.includes('decision_record.reason'));
'''
        self._run("const BRIEF = " + json.dumps(brief,
                                                ensure_ascii=False) + ";\n"
                  + body)

    def test_raw_json_disclosure_survives_poll_rebuild(self):
        # The card is rebuilt on every controls poll; an operator-expanded
        # raw-JSON disclosure must not be snapped shut by the rebuild, and a
        # manually closed one must stay closed.
        body = r'''
renderHumanReviewDecisionBrief(BRIEF);
const raw = document.getElementById('hc-review-brief-raw');
assert.equal(raw.open, false);
raw.open = true;
renderHumanReviewDecisionBrief(BRIEF);
assert.equal(document.getElementById('hc-review-brief-raw').open, true);
// The operator closes it again on the next rendered card.
document.getElementById('hc-review-brief-raw').open = false;
renderHumanReviewDecisionBrief(BRIEF);
assert.equal(document.getElementById('hc-review-brief-raw').open, false);
'''
        self._run("const BRIEF = " + json.dumps(self._brief(),
                                                ensure_ascii=False) + ";\n"
                  + body)

    def test_intervention_folds_on_transition_and_respects_the_operator(self):
        body = r'''
// Transition into HUMAN_REVIEW folds the steering area once.
updateControlButtons();
assert.equal(details.open, false);
assert.equal(submit.disabled, true);
assert.equal(nodes.get('hc-int-fold-note').hidden, false);
// A manual re-expansion is respected (no fight with the operator).
details.open = true;
updateControlButtons();
assert.equal(details.open, true);
// Leaving HUMAN_REVIEW reopens the section and restores the controls.
lastControls.human_review.active = false;
lastCockpitFamily = 'RUNNING';
updateControlButtons();
assert.equal(details.open, true);
assert.equal(nodes.get('hc-int-fold-note').hidden, true);
'''
        setup = r'''
function controlsUnavailableReason() { return null; }
let lastCockpitFamily = 'HUMAN_REVIEW';
let lastControls = {human_review: {active: true}, pause: {state: 'NONE',
  parked_dispatch: null}, stop: {applied: false}};
let interventionFoldWasActive = false;
const details = document.getElementById('hc-int-details');
const submit = document.getElementById('hc-int-submit');
'''
        source = index_text()
        function = source.split("function updateControlButtons() {", 1)[
            1].split("function renderPendingControlsUnavailable(message) {",
                     1)[0]
        harness = (self._harness() + "\n" + setup
                   + "\nfunction updateControlButtons() {" + function
                   + "\n" + body + "\n")
        result = subprocess.run(["node"], input=harness, text=True,
                                encoding="utf-8", capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class ApplyRefusalVisibilityTests(unittest.TestCase):
    """The Console must surface the helper's concrete Runtime refusal detail
    (stderr_head, e.g. "HUMAN_REVIEW_RESUME_FAILED: previous Executor task is
    not fully consumed: dispatched=603, consumed=602") instead of only a
    generic conflict/stale message. Backend decision semantics are untouched:
    the server envelope already carried stderr_head in error.detail."""

    def test_control_action_surfaces_helper_stderr_head(self):
        text = index_text()
        self.assertIn("error.detail.stderr_head", text)
        self.assertIn('" Helper output: "', text)

    def test_stderr_head_branch_is_guarded_on_non_empty_string(self):
        text = index_text()
        self.assertRegex(text, r'if \(error\.detail && typeof error\.detail\.stderr_head === "string"\s*&& error\.detail\.stderr_head\.trim\(\)\)')

    def test_backend_stderr_head_contract_unchanged(self):
        server = (REPO / "scripts" / "web_console_server.py").read_text(
            encoding="utf-8")
        self.assertIn(
            '"stderr_head": stderr_head[:STDOUT_ERROR_HEAD_BYTES]', server)


if __name__ == "__main__":
    unittest.main()
