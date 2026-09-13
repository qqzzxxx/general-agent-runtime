"""P3 Web Console state interpreter tests (pure, offline).

Covers the deterministic Current Execution state families and the Next
Expected decision table derived ONLY from the bounded
`supervisor_control.py status --json` document, plus fail-closed behavior for
unknown, contradictory, missing, and malformed inputs, the protocol milestone
facts, honest "since when" handling, and determinism (same input, same
output; no clock, no AI, no fabricated progress or token values).
"""
from __future__ import annotations

import copy
import importlib
import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def unavailable(result) -> bool:
    """True when the interpreter failed closed to the honest unavailable
    presentation instead of guessing a state."""
    return result["state"]["family"] == "STATE_UNAVAILABLE"


def identity(message_id=700126, task_id="V13_TASK", stage_id="stage-a1",
             attempt=1, nonce="b" * 24, **extra):
    doc = {"MESSAGE_ID": message_id, "TASK_ID": task_id, "STAGE_ID": stage_id,
           "ATTEMPT": attempt, "NONCE": nonce}
    doc.update(extra)
    return doc


def status_doc(**over):
    doc = {
        "schema_version": 1,
        "PROJECT_ID": "proj-demo-001",
        "runtime_status": "RUNNING",
        "project_status": "WAITING_EXECUTOR",
        "pause": {"status": "RUNNING", "requested_at": None, "mode": "SAFE",
                  "resumed_at": None},
        "active_task": None,
        "last_authorized_dispatch": None,
        "active_task_claimed": False,
        "active_task_claim_recorded": False,
        "active_task_completion_status": None,
        "active_task_retired": False,
        "pending_interventions": 0,
        "last_consumed_message_id": None,
        "human_review": False,
        "stop": False,
    }
    doc.update(over)
    return doc


def complete_doc(**over):
    return status_doc(**dict({
        "runtime_status": "COMPLETE", "project_status": "COMPLETE",
        "supervisor_turn_inflight": None,
        "terminal_completion": {"schema_version": 1, "valid": True,
                                "problems": [], "final_verification_status": "PASS"},
    }, **over))


_UNSET = object()


def with_active_task(doc, task=None, authorized=_UNSET, claimed=False,
                     claim_recorded=False, completion=None, retired=False,
                     fv=False):
    task = identity(**({"IS_FINAL_VERIFICATION": True} if fv else {})) \
        if task is None else task
    if authorized is _UNSET:
        authorized = copy.deepcopy(task)
    doc["active_task"] = task
    doc["last_authorized_dispatch"] = authorized
    doc["active_task_claimed"] = claimed
    doc["active_task_claim_recorded"] = claim_recorded
    doc["active_task_completion_status"] = completion
    doc["active_task_retired"] = retired
    return doc


class StateInterpreterCase(unittest.TestCase):
    """Base: the interpreter module is imported lazily per test so the red
    phase shows one failure per targeted case, mirroring the P2 convention."""

    def setUp(self):
        self.state = importlib.import_module("web_console_state")


class StateFamilyTests(StateInterpreterCase):
    """Every required Current Execution family maps from bounded facts."""

    def test_codex_thinking(self):
        result = self.state.interpret_status(
            status_doc(project_status="SUPERVISOR_TURN"))
        self.assertEqual(result["state"]["family"], self.state.FAM_CODEX_THINKING)
        self.assertIn("Codex", result["state"]["label"])
        self.assertEqual(result["state"]["worker"]["who"], "Codex (Supervisor)")
        self.assertFalse(result["state"]["user_action_required"])

    def test_waiting_for_zcode_claim_with_exact_message_id(self):
        result = self.state.interpret_status(
            with_active_task(status_doc()))
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_WAITING_FOR_ZCODE_CLAIM)
        self.assertEqual(result["next_expected"]["message_id"], 700126)
        self.assertIn("700126", result["next_expected"]["label"])
        self.assertIn("claim", result["next_expected"]["label"].lower())

    def test_zcode_executing(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True, claim_recorded=True))
        self.assertEqual(result["state"]["family"], self.state.FAM_ZCODE_EXECUTING)
        self.assertEqual(result["state"]["worker"]["who"], "ZCode (Executor)")

    def test_completion_committed(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claim_recorded=True,
                             completion="COMPLETION_COMMITTED"))
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_COMPLETION_COMMITTED)
        self.assertIn("evaluate", result["next_expected"]["label"].lower())
        self.assertEqual(result["next_expected"]["message_id"], 700126)

    def test_completion_consumed_maps_to_result_evaluation(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claim_recorded=True,
                             completion="COMPLETION_CONSUMED"))
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_RESULT_EVALUATION)

    def test_completion_sealed_maps_to_result_evaluation(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claim_recorded=True,
                             completion="COMPLETION_SEALED"))
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_RESULT_EVALUATION)

    def test_supervisor_processing_consumed_result(self):
        # Mechanically, active_task is only reported while the project waits
        # for the Executor; a running Supervisor turn surfaces the consumed
        # MESSAGE_ID via last_consumed_message_id instead.
        doc = status_doc(project_status="SUPERVISOR_TURN")
        doc["last_consumed_message_id"] = 700126
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_RESULT_EVALUATION)
        self.assertIn("Codex", result["state"]["label"])
        self.assertIn("700126", result["next_expected"]["label"])

    def test_final_verification_waiting_claim_is_its_own_family(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), fv=True))
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_FINAL_VERIFICATION_WAITING_CLAIM)
        self.assertTrue(result["final_verification"]["is_final_verification"])
        self.assertIn("Final Verification", result["next_expected"]["label"])

    def test_final_verification_executing(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True, claim_recorded=True,
                             fv=True))
        self.assertEqual(result["state"]["family"],
                         self.state.FAM_FINAL_VERIFICATION_EXECUTING)
        self.assertIn("COMPLETE", result["next_expected"]["label"])

    def test_pause_requested(self):
        doc = status_doc(pause={"status": "PENDING_AFTER_CURRENT_STAGE",
                                "requested_at": "2026-09-12T01:00:00+00:00",
                                "mode": "SAFE", "resumed_at": None})
        result = self.state.interpret_status(
            with_active_task(doc, claimed=True, claim_recorded=True))
        self.assertEqual(result["state"]["family"], self.state.FAM_PAUSE_REQUESTED)
        self.assertFalse(result["state"]["user_action_required"])
        self.assertIn("pause", result["next_expected"]["label"].lower())

    def test_paused_via_pause_record(self):
        doc = status_doc(pause={"status": "PAUSED",
                                "requested_at": "2026-09-12T01:00:00+00:00",
                                "paused_at": "2026-09-12T01:05:00+00:00",
                                "mode": "SAFE", "resumed_at": None})
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"], self.state.FAM_PAUSED)
        self.assertTrue(result["state"]["user_action_required"])
        self.assertTrue(result["state"]["since"]["available"])
        self.assertEqual(result["state"]["since"]["at"],
                         "2026-09-12T01:05:00+00:00")

    def test_paused_via_project_status(self):
        result = self.state.interpret_status(status_doc(project_status="PAUSED"))
        self.assertEqual(result["state"]["family"], self.state.FAM_PAUSED)

    def test_paused_via_runtime_status(self):
        result = self.state.interpret_status(status_doc(runtime_status="PAUSED"))
        self.assertEqual(result["state"]["family"], self.state.FAM_PAUSED)

    def test_human_review_wins(self):
        result = self.state.interpret_status(status_doc(human_review=True))
        self.assertEqual(result["state"]["family"], self.state.FAM_HUMAN_REVIEW)
        self.assertTrue(result["state"]["user_action_required"])
        self.assertIn("decision", result["next_expected"]["label"].lower())

    def test_project_status_human_review(self):
        result = self.state.interpret_status(
            status_doc(project_status="HUMAN_REVIEW", human_review=True))
        self.assertEqual(result["state"]["family"], self.state.FAM_HUMAN_REVIEW)

    def test_complete(self):
        result = self.state.interpret_status(complete_doc())
        self.assertEqual(result["state"]["family"], self.state.FAM_COMPLETE)
        self.assertFalse(result["state"]["user_action_required"])
        self.assertIn("COMPLETE", result["next_expected"]["label"])

    def test_stopped_via_stop_flag(self):
        result = self.state.interpret_status(status_doc(stop=True))
        self.assertEqual(result["state"]["family"], self.state.FAM_STOPPED)

    def test_stopped_via_project_status(self):
        result = self.state.interpret_status(status_doc(project_status="STOPPED"))
        self.assertEqual(result["state"]["family"], self.state.FAM_STOPPED)

    def test_stopped_by_user_runtime_status(self):
        result = self.state.interpret_status(
            status_doc(runtime_status="STOPPED_BY_USER"))
        self.assertEqual(result["state"]["family"], self.state.FAM_STOPPED)

    def test_deadline_reached_maps_to_stopped(self):
        result = self.state.interpret_status(
            status_doc(runtime_status="DEADLINE_REACHED"))
        self.assertEqual(result["state"]["family"], self.state.FAM_STOPPED)
        self.assertIn("deadline", result["state"]["detail"].lower())

    def test_error_via_orchestrator_error(self):
        result = self.state.interpret_status(
            status_doc(runtime_status="ORCHESTRATOR_ERROR"))
        self.assertEqual(result["state"]["family"], self.state.FAM_ERROR)
        self.assertTrue(result["state"]["user_action_required"])

    def test_error_via_blocked_project(self):
        result = self.state.interpret_status(status_doc(project_status="BLOCKED"))
        self.assertEqual(result["state"]["family"], self.state.FAM_ERROR)

    def test_idle_waiting_for_next_dispatch(self):
        result = self.state.interpret_status(status_doc())
        self.assertEqual(result["state"]["family"], self.state.FAM_IDLE)
        self.assertIn("Supervisor", result["next_expected"]["label"])

    def test_no_project(self):
        doc = status_doc(PROJECT_ID=None, runtime_status=None,
                         project_status=None, pause=None)
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"], self.state.FAM_NO_PROJECT)


class PrecedenceTests(StateInterpreterCase):
    """Deterministic precedence between simultaneous facts."""

    def test_stop_beats_everything_else(self):
        doc = with_active_task(status_doc(stop=True), claimed=True,
                               claim_recorded=True)
        doc["human_review"] = True
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"], self.state.FAM_STOPPED)

    def test_human_review_beats_terminal_progress(self):
        doc = with_active_task(status_doc(human_review=True), claimed=True,
                               claim_recorded=True)
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"], self.state.FAM_HUMAN_REVIEW)

    def test_error_beats_pause_and_execution(self):
        doc = with_active_task(status_doc(runtime_status="ORCHESTRATOR_ERROR"),
                               claimed=True, claim_recorded=True)
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"], self.state.FAM_ERROR)

    def test_paused_beats_active_execution(self):
        doc = with_active_task(
            status_doc(pause={"status": "PAUSED", "requested_at": None,
                              "mode": "SAFE", "resumed_at": None}),
            claimed=True, claim_recorded=True)
        result = self.state.interpret_status(doc)
        self.assertEqual(result["state"]["family"], self.state.FAM_PAUSED)

    def test_complete_with_unsettled_pause_fails_closed(self):
        # Conflicting terminal/control facts require inspection, never a guess.
        doc = complete_doc(
                         pause={"status": "PENDING_AFTER_CURRENT_STAGE",
                                "requested_at": "2026-09-12T01:00:00+00:00",
                                "mode": "SAFE", "resumed_at": None})
        result = self.state.interpret_status(doc)
        self.assertTrue(unavailable(result))


class FailClosedTests(StateInterpreterCase):
    """Unknown, contradictory, and malformed inputs never guess."""

    def test_unknown_runtime_status_fails_closed(self):
        result = self.state.interpret_status(status_doc(runtime_status="WARP_DRIVE"))
        self.assertTrue(unavailable(result))
        self.assertIn("runtime_status", json.dumps(result["honesty"]))
        self.assertFalse(result["next_expected"]["available"])

    def test_unknown_project_status_fails_closed(self):
        result = self.state.interpret_status(
            status_doc(project_status="SOMETHING_NEW"))
        self.assertTrue(unavailable(result))

    def test_unknown_pause_status_fails_closed(self):
        doc = status_doc(pause={"status": "WHENEVER", "requested_at": None,
                                "mode": "SAFE", "resumed_at": None})
        result = self.state.interpret_status(doc)
        self.assertTrue(unavailable(result))

    def test_unknown_pause_mode_fails_closed(self):
        doc = status_doc(pause={"status": "RUNNING", "requested_at": None,
                                "mode": "SURPRISE", "resumed_at": None})
        result = self.state.interpret_status(doc)
        self.assertTrue(unavailable(result))

    def test_wrong_type_runtime_status_fails_closed(self):
        result = self.state.interpret_status(status_doc(runtime_status=7))
        self.assertTrue(unavailable(result))

    def test_wrong_type_pending_interventions_fails_closed(self):
        result = self.state.interpret_status(status_doc(pending_interventions="3"))
        self.assertTrue(unavailable(result))

    def test_boolean_pending_interventions_is_malformed(self):
        result = self.state.interpret_status(status_doc(pending_interventions=True))
        self.assertTrue(unavailable(result))

    def test_non_bool_human_review_fails_closed(self):
        result = self.state.interpret_status(status_doc(human_review="yes"))
        self.assertTrue(unavailable(result))

    def test_wrong_schema_version_fails_closed(self):
        result = self.state.interpret_status(status_doc(schema_version=2))
        self.assertTrue(unavailable(result))

    def test_pause_wrong_type_fails_closed(self):
        result = self.state.interpret_status(status_doc(pause="RUNNING"))
        self.assertTrue(unavailable(result))

    def test_active_task_wrong_type_fails_closed(self):
        result = self.state.interpret_status(status_doc(active_task="700126"))
        self.assertTrue(unavailable(result))

    def test_malformed_identity_fails_closed(self):
        bad = identity(MESSAGE_ID="700126")
        result = self.state.interpret_status(
            with_active_task(status_doc(), task=bad,
                             authorized=copy.deepcopy(bad)))
        self.assertTrue(unavailable(result))

    def test_claimed_without_claim_record_is_contradiction(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True))
        self.assertTrue(unavailable(result))
        self.assertTrue(result["honesty"]["contradictions"])

    def test_claim_record_without_claim_or_completion_is_contradiction(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claim_recorded=True))
        self.assertTrue(unavailable(result))

    def test_claimed_with_completion_is_contradiction(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True, claim_recorded=True,
                             completion="COMPLETION_COMMITTED"))
        self.assertTrue(unavailable(result))

    def test_retired_and_claimed_is_contradiction(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True, claim_recorded=True,
                             retired=True))
        self.assertTrue(unavailable(result))

    def test_active_task_without_waiting_executor_is_contradiction(self):
        doc = with_active_task(status_doc(project_status="SUPERVISOR_TURN"),
                               claim_recorded=True,
                               completion="COMPLETION_CONSUMED")
        result = self.state.interpret_status(doc)
        self.assertTrue(unavailable(result))

    def test_active_task_without_authorization_is_contradiction(self):
        doc = with_active_task(status_doc(), authorized=None)
        result = self.state.interpret_status(doc)
        self.assertTrue(unavailable(result))

    def test_retired_unclaimed_task_is_not_a_claim_wait(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), retired=True))
        self.assertTrue(unavailable(result))
        self.assertIn("retired", json.dumps(result["state"]).lower())

    def test_unavailable_state_keeps_human_safety_flags_actionable(self):
        doc = status_doc(human_review=True, runtime_status="WARP_DRIVE")
        result = self.state.interpret_status(doc)
        self.assertTrue(unavailable(result))
        self.assertTrue(result["state"]["user_action_required"])
        self.assertIn("human review", result["state"]["user_action_reason"]
                      .lower())

    def test_unavailable_state_reports_stop_flag(self):
        doc = status_doc(stop=True, project_status="WHY")
        result = self.state.interpret_status(doc)
        self.assertTrue(result["state"]["user_action_required"])

    def test_status_must_be_a_dict(self):
        for bad in (None, [], "ok", 7):
            result = self.state.interpret_status(bad)
            self.assertTrue(unavailable(result), bad)


class HonestPresentationTests(StateInterpreterCase):
    """Facts only: no invented timestamps, percentages, or token counts."""

    def test_executing_has_no_authoritative_since_timestamp(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True, claim_recorded=True))
        self.assertFalse(result["state"]["since"]["available"])
        self.assertIsNone(result["state"]["since"]["at"])

    def test_pause_requested_since_uses_requested_at(self):
        doc = status_doc(pause={"status": "PENDING_AFTER_CURRENT_STAGE",
                                "requested_at": "2026-09-12T01:00:00+00:00",
                                "mode": "SAFE", "resumed_at": None})
        result = self.state.interpret_status(
            with_active_task(doc, claimed=True, claim_recorded=True))
        self.assertTrue(result["state"]["since"]["available"])
        self.assertEqual(result["state"]["since"]["at"],
                         "2026-09-12T01:00:00+00:00")
        self.assertEqual(result["state"]["since"]["source"],
                         "pause.requested_at")

    def test_no_percent_or_token_fabrication_anywhere(self):
        docs = [
            with_active_task(status_doc(), claimed=True, claim_recorded=True),
            with_active_task(status_doc()),
            status_doc(project_status="COMPLETE"),
            status_doc(human_review=True),
        ]
        for doc in docs:
            blob = json.dumps(self.state.interpret_status(doc)).lower()
            self.assertNotIn("percent", blob)
            self.assertNotIn("% complete", blob)
            self.assertNotIn("token", blob)

    def test_published_milestone_is_unreported_not_faked(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claimed=True, claim_recorded=True))
        published = [m for m in result["milestones"] if m["key"] == "PUBLISHED"]
        self.assertEqual(len(published), 1)
        self.assertIsNone(published[0]["reached"])

    def test_milestones_track_claim_facts(self):
        result = self.state.interpret_status(
            with_active_task(status_doc(), claim_recorded=True,
                             completion="COMPLETION_COMMITTED"))
        reached = {m["key"]: m["reached"] for m in result["milestones"]}
        self.assertTrue(reached["AUTHORIZED"])
        self.assertTrue(reached["CLAIMED"])
        self.assertFalse(reached["WORKING"])
        self.assertTrue(reached["COMPLETION_COMMITTED"])
        self.assertFalse(reached["CONSUMED_SEALED"])

    def test_milestones_absent_without_active_task(self):
        result = self.state.interpret_status(status_doc(project_status="COMPLETE"))
        self.assertEqual(result["milestones"], [])

    def test_protocol_facts_echo_the_identity(self):
        result = self.state.interpret_status(with_active_task(status_doc()))
        self.assertEqual(result["protocol"]["message_id"], 700126)
        self.assertEqual(result["protocol"]["task_id"], "V13_TASK")
        self.assertEqual(result["protocol"]["stage_id"], "stage-a1")
        self.assertEqual(result["protocol"]["attempt"], 1)

    def test_zcode_activity_without_claim_reports_last_consumed_fact(self):
        doc = status_doc(last_consumed_message_id=700100)
        result = self.state.interpret_status(doc)
        health = result["health"]
        self.assertIn("700100", health["zcode"]["last_observed_activity"])
        self.assertIsNone(health["zcode"]["at"])  # no timestamp is reported

    def test_unreported_health_facts_display_as_unavailable(self):
        doc = status_doc(PROJECT_ID=None, runtime_status=None,
                         project_status=None, pause=None)
        result = self.state.interpret_status(doc)
        self.assertFalse(result["health"]["orchestrator"]["available"])
        self.assertFalse(result["health"]["current_authorization"]["available"])

    def test_stop_flag_appears_in_errors_and_warnings(self):
        result = self.state.interpret_status(status_doc(stop=True))
        codes = [entry["code"] for entry in
                 result["health"]["errors_and_warnings"]]
        self.assertIn("STOP_FLAG", codes)

    def test_pending_interventions_count_is_factual(self):
        result = self.state.interpret_status(status_doc(pending_interventions=2))
        self.assertEqual(result["health"]["pending_interventions"]["count"], 2)

    def test_interpretation_is_versioned_and_deterministic(self):
        doc = with_active_task(status_doc(), claimed=True, claim_recorded=True)
        first = self.state.interpret_status(doc)
        second = self.state.interpret_status(copy.deepcopy(doc))
        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertTrue(first["deterministic"])

    def test_interpreter_output_is_json_serializable_ascii_safe(self):
        doc = with_active_task(status_doc(), claimed=True, claim_recorded=True)
        doc["PROJECT_ID"] = "科研-中文"
        blob = json.dumps(self.state.interpret_status(doc), ensure_ascii=True)
        self.assertTrue(blob)


if __name__ == "__main__":
    unittest.main()
