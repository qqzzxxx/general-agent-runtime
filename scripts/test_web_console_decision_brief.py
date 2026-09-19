"""Human Review Decision Brief tests (2026-09-16 follow-up).

Entering HUMAN_REVIEW used to surface only a generic "decision required"
line plus the mechanical control/HUMAN_REVIEW flag text, so the operator had
to read project_state.json by hand. The Decision Brief projects the
authoritative Supervisor decision record (decision_history /
last_supervisor_decision, including the GOAL-ANCHOR-V1 goal_alignment
contract) verbatim into the controls document.

These tests pin the projection contract:

* a complete goal_alignment record is projected field for field;
* a reason-only record (no alignment, no suggestion) degrades honestly —
  the suggestion line is explicitly "not provided", never inferred;
* missing or malformed decision fields fail closed to None/unavailable;
* the brief binds to the LATEST committed HUMAN_REVIEW decision, so a new
  review with a different MESSAGE_ID replaces the previous projection;
* a non-HUMAN_REVIEW state never projects a stale brief;
* the full raw decision JSON stays viewable for audit (bounded);
* the HTTP controls document carries the brief end to end, and Prepare
  keeps working unchanged alongside it.

The projection is read-only: nothing here writes Runtime state, and the
lifecycle (receipt, Apply, resume) is covered by the existing suites.
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_control as wcc
from test_web_console_human_review_e2e import PROJECT_ID, build_e2e_runtime

# A synthetic full-shape review record: the supervisor recorded the review
# with the complete six-field goal_alignment and no dedicated suggestion
# field, which is the projection contract's hardest case.
REVIEW_RECORD = {
    "decision": "HUMAN_REVIEW",
    "at": "2026-09-15T20:49:19.658Z",
    "scope": "对照方法回归、算子偏差复核及零重试预算下的人工审查",
    "reason": (
        "复核发现对照实现与声明不一致，导致关键对照锚点全部改变；"
        "核心比较与机制解释暂不可信，且单次 MAX_RETRIES=0 预算已用，"
        "按边界返回人工审查。"),
    "goal_alignment": {
        "original_objective": (
            "建立可复现的多阶段分析流程，以真实实验回答有效性、策略"
            "与参数成本问题，形成可追溯research package。"),
        "unmet_criteria": (
            "关键对照方法未按设计执行，核心比较与机制解释不可信；"
            "完整材料、关键结论独立检查及最终核验未完成。"),
        "latest_result": (
            "复核关键源码与结果面板。一致性检查发现对照结果全部变化；"
            "新缺陷不推翻已完成阶段的核心结果资格。"),
        "next_action_alignment": (
            "停止派发并保留历史；建议新人工决定审查同一策略任务一次2700秒零重试"
            "有限修订：修正对照、锚点核验、重跑受影响结果、更新下游分析；"
            "之后重审是否开展后续阶段。"),
        "scope_drift": (
            "保持既定研究方向。新缺陷不推翻已完成阶段资格，但阻断"
            "当前比较验收；不扩大修补范围，不把修补包装成参数任务。"),
        "method": (
            "历史方法与人工修订预算保持不变。当前任务单次"
            "MAX_RETRIES=0已用，不自行追加；修订仅为新Human Decision的审查建议。"),
    },
}

AUTHORIZING_RECORD = {
    "decision": "CONTINUE",
    "at": "2026-09-15T19:54:13.4351085+00:00",
    "message_id": 700106,
    "task_id": "task-79690b7d65b61b6788d25559",
    "stage_id": "stage-6d8a8ece27289efe033e608e",
    "scope": "已资格化流程的小规模多场景策略研究",
    "reason": "基于已资格化的同一流程，开展小规模多场景受控策略研究。",
    "goal_alignment": {
        "original_objective": "可复现的多阶段分析研究流程。",
        "unmet_criteria": "后续策略与成本阶段未完成。",
        "latest_result": "有限修订完成且核心资格可继续。",
        "next_action_alignment": "开展小规模多场景策略研究。",
        "scope_drift": "保持方向，不进入报告/PPT。",
        "method": "单次2700秒、零自动重试。",
    },
}


def review_state(history, **overrides):
    state = {
        "schema_version": 4,
        "project_id": "proj-review",
        "status": "HUMAN_REVIEW",
        "decision_history": history,
        "last_supervisor_decision": {"decision": "HUMAN_REVIEW",
                                     "scope": None, "reason": "mirror"},
    }
    state.update(overrides)
    return state


class DecisionBriefProjectionTests(unittest.TestCase):
    def test_complete_goal_alignment_is_projected_field_for_field(self):
        brief = wcc.human_review_decision_brief(
            review_state([AUTHORIZING_RECORD, REVIEW_RECORD]))
        self.assertTrue(brief["state_available"])
        self.assertTrue(brief["state_in_human_review"])
        self.assertTrue(brief["available"])
        self.assertFalse(brief["alignment_malformed"])
        self.assertTrue(brief["alignment_available"])
        self.assertEqual(brief["reason"], REVIEW_RECORD["reason"])
        record = brief["decision_record"]
        self.assertTrue(record["found"])
        self.assertEqual(record["source"], "decision_history")
        self.assertEqual(record["history_index"], 1)
        self.assertTrue(record["is_latest_history_entry"])
        self.assertEqual(record["committed_at"], REVIEW_RECORD["at"])
        self.assertEqual(record["scope"], REVIEW_RECORD["scope"])
        for field, value in REVIEW_RECORD["goal_alignment"].items():
            self.assertEqual(brief["goal_alignment"][field], value)

    def test_trigger_task_binds_the_reviewed_message_id(self):
        brief = wcc.human_review_decision_brief(
            review_state([AUTHORIZING_RECORD, REVIEW_RECORD]))
        trigger = brief["trigger_task"]
        self.assertTrue(trigger["available"])
        self.assertEqual(trigger["source"], "previous_authorized_decision")
        self.assertEqual(trigger["message_id"], 700106)
        self.assertEqual(trigger["task_id"],
                         "task-79690b7d65b61b6788d25559")
        self.assertEqual(trigger["stage_id"],
                         "stage-6d8a8ece27289efe033e608e")
        self.assertEqual(trigger["authorized_by"], "CONTINUE")
        self.assertEqual(trigger["from_decision_index"], 0)

    def test_record_bound_identity_wins_as_trigger(self):
        bound = dict(REVIEW_RECORD, message_id=700106,
                     task_id="task-79690b7d65b61b6788d25559",
                     stage_id="stage-6d8a8ece27289efe033e608e")
        brief = wcc.human_review_decision_brief(review_state([bound]))
        trigger = brief["trigger_task"]
        self.assertTrue(trigger["available"])
        self.assertEqual(trigger["source"], "decision_record")
        self.assertEqual(trigger["message_id"], 700106)

    def test_reason_only_record_degrades_without_inventing(self):
        record = {"decision": "HUMAN_REVIEW", "at": "2026-09-16T00:00:00Z",
                  "reason": "自动恢复次数已用尽，需要人工决策。"}
        brief = wcc.human_review_decision_brief(review_state([record]))
        self.assertTrue(brief["available"])
        self.assertEqual(brief["reason"], record["reason"])
        self.assertFalse(brief["alignment_available"])
        self.assertFalse(brief["alignment_malformed"])
        for field in wcc.DECISION_BRIEF_GOAL_ALIGNMENT_FIELDS:
            self.assertIsNone(brief["goal_alignment"][field])
        self.assertFalse(brief["suggestion"]["available"])
        self.assertIsNone(brief["suggestion"]["text"])
        self.assertFalse(brief["trigger_task"]["available"])

    def test_recorded_suggestion_is_shown_verbatim_only_when_present(self):
        record = dict(REVIEW_RECORD, suggestion="允许一次 2700 秒有限修订。")
        brief = wcc.human_review_decision_brief(review_state([record]))
        self.assertTrue(brief["suggestion"]["available"])
        self.assertEqual(brief["suggestion"]["text"],
                         "允许一次 2700 秒有限修订。")
        # The complete record WITHOUT the suggestion field must not inherit
        # or derive one (the review record has no suggestion field).
        plain = wcc.human_review_decision_brief(
            review_state([REVIEW_RECORD]))
        self.assertFalse(plain["suggestion"]["available"])

    def test_malformed_fields_fail_closed(self):
        record = {
            "decision": "HUMAN_REVIEW",
            "at": 12345,
            "reason": 42,
            "scope": ["not", "a", "string"],
            "message_id": True,
            "task_id": "",
            "goal_alignment": "not an object",
        }
        brief = wcc.human_review_decision_brief(review_state([record]))
        self.assertTrue(brief["state_in_human_review"])
        self.assertTrue(brief["available"])
        self.assertTrue(brief["alignment_malformed"])
        self.assertFalse(brief["alignment_available"])
        self.assertIsNone(brief["reason"])
        self.assertIsNone(brief["decision_record"]["committed_at"])
        self.assertIsNone(brief["decision_record"]["scope"])
        self.assertIsNone(brief["decision_record"]["message_id"])
        self.assertIsNone(brief["decision_record"]["task_id"])

    def test_partial_alignment_reports_unavailable_fields(self):
        record = dict(REVIEW_RECORD)
        record["goal_alignment"] = {
            "original_objective": "o", "unmet_criteria": "u",
            "latest_result": "", "next_action_alignment": None,
            "scope_drift": "s", "method": "m"}
        brief = wcc.human_review_decision_brief(review_state([record]))
        self.assertFalse(brief["alignment_available"])
        self.assertFalse(brief["alignment_malformed"])
        self.assertEqual(brief["goal_alignment"]["original_objective"], "o")
        self.assertIsNone(brief["goal_alignment"]["latest_result"])
        self.assertIsNone(brief["goal_alignment"]["next_action_alignment"])

    def test_no_structured_record_is_honest_in_review(self):
        # The exact shape of the e2e fixture / legacy states: a plain-string
        # last_supervisor_decision mirror and empty history.
        state = {"status": "HUMAN_REVIEW", "decision_history": [],
                 "last_supervisor_decision": "HUMAN_REVIEW — fixture"}
        brief = wcc.human_review_decision_brief(state)
        self.assertTrue(brief["state_in_human_review"])
        self.assertFalse(brief["available"])
        self.assertIsNone(brief["inactive_reason"])
        self.assertFalse(brief["decision_record"]["found"])

    def test_legacy_last_supervisor_decision_mirror_is_projected(self):
        state = {"status": "HUMAN_REVIEW", "decision_history": [],
                 "last_supervisor_decision": {
                     "decision": "HUMAN_REVIEW",
                     "at": "2026-09-16T00:00:00Z",
                     "scope": "legacy review",
                     "reason": "legacy mirror reason"}}
        brief = wcc.human_review_decision_brief(state)
        self.assertTrue(brief["available"])
        self.assertEqual(brief["decision_record"]["source"],
                         "last_supervisor_decision")
        self.assertIsNone(brief["decision_record"]["history_index"])
        self.assertIsNone(brief["decision_record"]
                          ["is_latest_history_entry"])
        self.assertEqual(brief["reason"], "legacy mirror reason")

    def test_latest_review_wins_when_message_ids_differ(self):
        older_review = {
            "decision": "HUMAN_REVIEW", "at": "2026-09-15T13:44:43.659Z",
            "scope": "已授权尝试耗尽后的执行恢复审查",
            "reason": "旧审查：700102 未领取退役。",
            "goal_alignment": dict(REVIEW_RECORD["goal_alignment"],
                                   unmet_criteria="旧审查未通过项。")}
        newer_authorizing = {
            "decision": "CONTINUE", "at": "2026-09-15T16:30:44Z",
            "message_id": 700103, "task_id": "reference_validation",
            "stage_id": "upstream_qualification"}
        newer_review = dict(REVIEW_RECORD)
        history = [older_review, newer_authorizing, newer_review]
        brief = wcc.human_review_decision_brief(review_state(history))
        self.assertEqual(brief["decision_record"]["history_index"], 2)
        self.assertEqual(brief["reason"], REVIEW_RECORD["reason"])
        self.assertEqual(brief["trigger_task"]["message_id"], 700103)
        self.assertNotIn("旧审查未通过项。",
                         (brief["goal_alignment"]["unmet_criteria"] or ""))

    def test_review_not_latest_history_entry_is_labelled(self):
        history = [REVIEW_RECORD,
                   dict(AUTHORIZING_RECORD, decision="CONTINUE")]
        brief = wcc.human_review_decision_brief(review_state(history))
        self.assertTrue(brief["available"])
        self.assertFalse(brief["decision_record"]["is_latest_history_entry"])

    def test_non_human_review_state_never_projects_a_stale_brief(self):
        # After the human decision is applied the review is over; the old
        # HUMAN_REVIEW record must not leak into the brief.
        state = review_state(
            [AUTHORIZING_RECORD, REVIEW_RECORD,
             {"decision": "REVISE", "at": "2026-09-15T20:58:32+00:00",
              "message_id": 700107, "reason": "有限修订已授权。"}],
            status="WAITING_EXECUTOR")
        brief = wcc.human_review_decision_brief(state)
        self.assertFalse(brief["state_in_human_review"])
        self.assertFalse(brief["available"])
        self.assertEqual(brief["inactive_reason"],
                         "PROJECT_NOT_IN_HUMAN_REVIEW")
        self.assertFalse(brief["decision_record"]["found"])
        self.assertIsNone(brief["reason"])
        self.assertFalse(brief["raw_decision_available"])

    def test_unreadable_state_degrades_to_unavailable(self):
        brief = wcc.human_review_decision_brief(None)
        self.assertFalse(brief["state_available"])
        self.assertFalse(brief["state_in_human_review"])
        self.assertFalse(brief["available"])
        self.assertEqual(brief["inactive_reason"],
                         "PROJECT_STATE_UNAVAILABLE")

    def test_raw_decision_json_stays_viewable_and_bounded(self):
        brief = wcc.human_review_decision_brief(
            review_state([AUTHORIZING_RECORD, REVIEW_RECORD]))
        self.assertTrue(brief["raw_decision_available"])
        self.assertEqual(brief["raw_decision"], REVIEW_RECORD)
        huge = dict(REVIEW_RECORD, reason="长" * 100000)
        bounded = wcc.human_review_decision_brief(review_state([huge]))
        self.assertFalse(bounded["raw_decision_available"])
        self.assertEqual(bounded["raw_decision_unavailable_reason"],
                         "RAW_RECORD_EXCEEDS_DISPLAY_BOUND")
        self.assertTrue(bounded["available"])
        self.assertEqual(len(bounded["reason"]),
                         wcc.DECISION_BRIEF_REASON_MAX)
        self.assertIn("decision_record.reason", bounded["truncated_fields"])


class DecisionBriefHttpWiringTests(unittest.TestCase):
    """End to end: the controls document carries the brief read from the
    fixture Runtime's real project_state.json, and Prepare keeps working."""

    def setUp(self):
        import web_console_server as wcs
        self._wcs = wcs
        self._tmp = tempfile.TemporaryDirectory(prefix="hr-brief-")
        base = Path(self._tmp.name)
        self.runtime_root = base / "runtime"
        build_e2e_runtime(self.runtime_root)
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True)
        shutil.copyfile(REPO / "web_console" / "index.html",
                        self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.write_project_state(review_state(
            [AUTHORIZING_RECORD, REVIEW_RECORD],
            project_id=PROJECT_ID))
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_root,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=30.0)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()
        status, payload = self.request(
            "POST", "/api/runtimes",
            body={"root": str(self.runtime_root),
                  "label": "Decision brief — 决策摘要"})
        if status != 201:
            raise AssertionError(f"fixture registration failed: {status} "
                                 f"{payload!r}")
        self.runtime = payload["runtime"]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self._tmp.cleanup()

    def write_project_state(self, state):
        path = (self.runtime_root / "projects" / PROJECT_ID
                / "project_state.json")
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    def request(self, method, path, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server_port,
                                          timeout=60)
        try:
            data = None
            headers = {}
            if body is not None:
                data = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            return response.status, json.loads(raw.decode("utf-8"))
        finally:
            conn.close()

    @property
    def server_port(self) -> int:
        return self.server.server_address[1]

    def runtime_url(self, suffix: str) -> str:
        return f"/api/runtimes/{self.runtime['id']}{suffix}"

    def controls(self) -> dict:
        status, payload = self.request("GET", self.runtime_url("/controls"))
        self.assertEqual(status, 200, payload)
        return payload["controls"]

    def test_controls_document_projects_the_brief_end_to_end(self):
        review = self.controls()["human_review"]
        self.assertTrue(review["active"])
        brief = review["decision_brief"]
        self.assertTrue(brief["available"])
        self.assertEqual(brief["reason"], REVIEW_RECORD["reason"])
        self.assertTrue(brief["alignment_available"])
        self.assertEqual(brief["goal_alignment"]["unmet_criteria"],
                         REVIEW_RECORD["goal_alignment"]
                         ["unmet_criteria"])
        self.assertEqual(brief["trigger_task"]["message_id"], 700106)
        self.assertFalse(brief["suggestion"]["available"])
        self.assertTrue(brief["raw_decision_available"])
        self.assertEqual(brief["raw_decision"], REVIEW_RECORD)

    def test_brief_disappears_when_the_review_is_over(self):
        state = review_state(
            [AUTHORIZING_RECORD, REVIEW_RECORD,
             {"decision": "REVISE", "at": "2026-09-15T20:58:32+00:00",
              "message_id": 700107, "reason": "有限修订已授权。"}],
            status="WAITING_EXECUTOR", project_id=PROJECT_ID)
        self.write_project_state(state)
        review = self.controls()["human_review"]
        brief = review["decision_brief"]
        self.assertFalse(brief["available"])
        self.assertEqual(brief["inactive_reason"],
                         "PROJECT_NOT_IN_HUMAN_REVIEW")

    def test_unreadable_project_state_reports_unavailable(self):
        (self.runtime_root / "control" / "ACTIVE_PROJECT.json").write_text(
            "{ not json", encoding="utf-8")
        brief = self.controls()["human_review"]["decision_brief"]
        self.assertFalse(brief["state_available"])
        self.assertFalse(brief["available"])
        self.assertEqual(brief["inactive_reason"],
                         "PROJECT_STATE_UNAVAILABLE")

    def test_prepare_keeps_working_alongside_the_brief(self):
        status, payload = self.request(
            "POST", self.runtime_url("/controls/human-review/prepare"),
            body={"decision_content": "允许一次 2700 秒有限修订。",
                  "constraints_verbatim": ["不重置历史预算。"]})
        self.assertEqual(status, 200, payload)
        decision = payload["human_decision"]
        self.assertTrue(decision["receipt_id"].startswith("human-decision-"))
        review = self.controls()["human_review"]
        self.assertEqual(len(review["prepared"]), 1)
        self.assertTrue(review["decision_brief"]["available"])


if __name__ == "__main__":
    unittest.main()
