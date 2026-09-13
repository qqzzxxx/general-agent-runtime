"""P9 pure-layer tests for deterministic Runtime-health soft alerts.

Every rule is evaluated from evidence (the control-plane status document,
the probe outcome, and the P8 Supervisor usage document) with settings-provided
thresholds. The tests pin stable alert identities, the severity vocabulary,
notification classification, unavailable-input honesty, and the reported-only
token-usage outlier rule.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_alerts as wal
from test_web_console_state import complete_doc


NOW = datetime(2026, 9, 12, 15, 0, 0, tzinfo=timezone.utc)
RUNTIME = {"id": "0123456789abcdef", "label": "Alpha", "root": r"C:\r\alpha"}


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def base_status(**overrides) -> dict:
    status = {
        "schema_version": 1,
        "PROJECT_ID": "demo-project",
        "runtime_status": "RUNNING",
        "project_status": "WAITING_EXECUTOR",
        "pause": {"status": "RUNNING", "requested_at": None, "mode": None,
                  "resumed_at": None, "paused_at": None},
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
    status.update(overrides)
    return status


def dispatch(message_id=700501, *, authorized_at=None, expires_at=None):
    return {"schema_version": 1, "MESSAGE_ID": message_id,
            "TASK_ID": "T", "STAGE_ID": "S", "ATTEMPT": 1, "NONCE": "n",
            "AUTHORIZED_AT": iso(authorized_at) if authorized_at else None,
            "EXPIRES_AT": iso(expires_at) if expires_at else None}


def project(**kw):
    return wal.project_alerts(
        runtime=RUNTIME, status=base_status(**kw), probe_ok=True,
        probe_error_code=None, usage_document=None,
        thresholds=wal.resolve_thresholds(None), now=NOW,
        generated_at=iso(NOW))


def rules(result):
    return {alert["rule"] for alert in result["alerts"]}


def by_rule(result, rule):
    found = [a for a in result["alerts"] if a["rule"] == rule]
    return found[0] if found else None


class VocabularyAndShapeTests(unittest.TestCase):
    def test_severity_vocabulary_is_pinned(self):
        self.assertEqual(wal.SEVERITY_VOCABULARY,
                         ("informational", "warning", "needs-attention"))

    def test_empty_inputs_produce_no_alerts_with_honesty_notes(self):
        result = project(runtime_status=None, project_status=None,
                         PROJECT_ID=None)
        self.assertEqual(result["alerts"], [])
        self.assertTrue(any("no live evidence" in note.lower()
                            for note in result["honesty"]))
        self.assertEqual(result["runtime"], RUNTIME)
        self.assertEqual(result["schema_version"], 1)

    def test_probe_failure_produces_one_offline_alert_and_suppression(self):
        result = wal.project_alerts(
            runtime=RUNTIME, status=None, probe_ok=False,
            probe_error_code="CONTROL_PLANE_TIMEOUT", usage_document=None,
            thresholds=wal.resolve_thresholds(None), now=NOW,
            generated_at=iso(NOW))
        self.assertEqual([a["rule"] for a in result["alerts"]],
                         ["runtime-offline"])
        alert = result["alerts"][0]
        self.assertEqual(alert["severity"], "needs-attention")
        self.assertEqual(alert["notification_class"], "must-attention")
        self.assertEqual(alert["notification_kind"], "HEALTH_SEVERE")
        self.assertEqual(alert["evidence"]["probe_error_code"],
                         "CONTROL_PLANE_TIMEOUT")
        self.assertTrue(any("suppressed" in note.lower()
                            for note in result["honesty"]))

    def test_alerts_are_sorted_and_ids_stable(self):
        soon = NOW + timedelta(minutes=10)
        status = base_status(
            human_review=True,
            last_authorized_dispatch=dispatch(expires_at=soon),
            active_task=dispatch(expires_at=soon))
        one = wal.project_alerts(
            runtime=RUNTIME, status=status, probe_ok=True,
            probe_error_code=None, usage_document=None,
            thresholds=wal.resolve_thresholds(None), now=NOW,
            generated_at=iso(NOW))
        two = wal.project_alerts(
            runtime=RUNTIME, status=status, probe_ok=True,
            probe_error_code=None, usage_document=None,
            thresholds=wal.resolve_thresholds(None), now=NOW,
            generated_at=iso(NOW))
        self.assertEqual(one, two)
        order = {name: i for i, name in enumerate(wal.SEVERITY_VOCABULARY)}
        severities = [a["severity"] for a in one["alerts"]]
        self.assertEqual(severities, sorted(severities,
                                            key=lambda s: order[s]))
        for alert in one["alerts"]:
            for key in ("id", "rule", "severity", "message",
                        "notification_class", "notification_kind",
                        "evidence"):
                self.assertIn(key, alert, key)


class ZcodePickupTests(unittest.TestCase):
    def authorized(self, minutes_ago, message_id=700501):
        disp = dispatch(message_id,
                        authorized_at=NOW - timedelta(minutes=minutes_ago))
        return base_status(active_task=disp, last_authorized_dispatch=disp)

    def test_old_unclaimed_dispatch_raises_warning(self):
        result = project(**{k: v for k, v in self.authorized(45).items()})
        alert = by_rule(result, "zcode-pickup")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["notification_class"],
                         "optional-informational")
        self.assertEqual(alert["notification_kind"], "ZCODE_PICKUP")
        self.assertEqual(alert["evidence"]["message_id"], 700501)
        self.assertEqual(alert["evidence"]["age_minutes"], 45.0)

    def test_extremely_old_dispatch_escalates_to_needs_attention(self):
        status = self.authorized(3 * 20 * 3)
        result = wal.project_alerts(
            runtime=RUNTIME, status=status, probe_ok=True,
            probe_error_code=None, usage_document=None,
            thresholds=wal.resolve_thresholds(None), now=NOW,
            generated_at=iso(NOW))
        alert = by_rule(result, "zcode-pickup")
        self.assertEqual(alert["severity"], "needs-attention")

    def test_fresh_dispatch_does_not_alert(self):
        result = project(**self.authorized(5))
        self.assertIsNone(by_rule(result, "zcode-pickup"))

    def test_claimed_dispatch_does_not_alert(self):
        status = self.authorized(90)
        status["active_task_claimed"] = True
        result = project(**status)
        self.assertIsNone(by_rule(result, "zcode-pickup"))

    def test_threshold_override_changes_the_boundary(self):
        status = self.authorized(45)
        thresholds = wal.resolve_thresholds(
            {"alert_thresholds": {"zcode_pickup_minutes": 60}})
        result = wal.project_alerts(
            runtime=RUNTIME, status=status, probe_ok=True,
            probe_error_code=None, usage_document=None, thresholds=thresholds,
            now=NOW, generated_at=iso(NOW))
        self.assertIsNone(by_rule(result, "zcode-pickup"))

    def test_unreadable_authorized_at_is_suppressed_not_fabricated(self):
        disp = dispatch()
        disp["AUTHORIZED_AT"] = "not-a-timestamp"
        status = base_status(active_task=disp, last_authorized_dispatch=disp)
        result = project(**status)
        self.assertIsNone(by_rule(result, "zcode-pickup"))
        self.assertTrue(any("zcode pickup" in note.lower()
                            for note in result["honesty"]))


class AuthorizationExpiryTests(unittest.TestCase):
    def test_only_current_unfinished_task_produces_timing_alerts(self):
        disp = dispatch(700987, authorized_at=NOW - timedelta(minutes=90),
                        expires_at=NOW + timedelta(minutes=5))
        current = base_status(active_task=disp, last_authorized_dispatch=disp)
        self.assertTrue({"zcode-pickup", "authorization-expiry"} <= rules(project(**current)))
        claimed = current | {"active_task_claimed": True, "active_task_claim_recorded": True}
        self.assertNotIn("zcode-pickup", rules(project(**claimed)))
        self.assertIn("authorization-expiry", rules(project(**claimed)))
        variants = [
            {"active_task": None},
            {"active_task_completion_status": "COMPLETION_COMMITTED"},
            {"active_task_completion_status": "CONSUMED"},
            {"active_task_completion_status": "SEALED"},
            {"last_consumed_message_id": disp["MESSAGE_ID"]},
            {"active_task_retired": True},
            {"human_review": True, "project_status": "HUMAN_REVIEW", "runtime_status": "HUMAN_REVIEW"},
            {"project_status": "COMPLETE", "runtime_status": "COMPLETE"},
            {"project_status": "SUPERVISOR_TURN"},
            {"stop": True},
            {"last_authorized_dispatch": disp | {"NONCE": "different"}},
        ]
        for variant in variants:
            with self.subTest(variant=variant):
                self.assertFalse({"zcode-pickup", "authorization-expiry"} & rules(project(**(current | variant))))

    def dispatch_with_expiry(self, minutes_ahead, message_id=700601):
        disp = dispatch(message_id,
                        expires_at=NOW + timedelta(minutes=minutes_ahead))
        return base_status(last_authorized_dispatch=disp,
                           active_task=disp)

    def test_warning_window_alerts_with_warning(self):
        result = project(**self.dispatch_with_expiry(45))
        alert = by_rule(result, "authorization-expiry")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["notification_class"], "none")
        self.assertEqual(alert["evidence"]["remaining_minutes"], 45.0)

    def test_critical_window_escalates_and_is_must_attention(self):
        result = project(**self.dispatch_with_expiry(10))
        alert = by_rule(result, "authorization-expiry")
        self.assertEqual(alert["severity"], "needs-attention")
        self.assertEqual(alert["notification_class"], "must-attention")
        self.assertEqual(alert["notification_kind"], "EXPIRY_RISK")

    def test_expired_authorization_is_needs_attention(self):
        result = project(**self.dispatch_with_expiry(-5))
        alert = by_rule(result, "authorization-expiry")
        self.assertEqual(alert["severity"], "needs-attention")

    def test_distant_expiry_does_not_alert(self):
        result = project(**self.dispatch_with_expiry(600))
        self.assertIsNone(by_rule(result, "authorization-expiry"))

    def test_completed_task_suppresses_expiry_alert(self):
        status = self.dispatch_with_expiry(10)
        status["active_task_completion_status"] = "COMPLETION_COMMITTED"
        status["active_task_completion"] = {
            "status": "COMPLETION_COMMITTED",
            "committed_at": iso(NOW - timedelta(minutes=1))}
        result = project(**status)
        self.assertIsNone(by_rule(result, "authorization-expiry"))

    def test_missing_dispatch_is_an_honest_note(self):
        result = project()
        self.assertIsNone(by_rule(result, "authorization-expiry"))


class CodexTurnLongTests(unittest.TestCase):
    def inflight(self, minutes_ago, turn_id="supervisor-turn-" + "a" * 24):
        return base_status(supervisor_turn_inflight={
            "turn_id": turn_id, "started_at": iso(
                NOW - timedelta(minutes=minutes_ago))})

    def test_long_running_turn_warns(self):
        result = project(**self.inflight(45))
        alert = by_rule(result, "codex-turn-long")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["evidence"]["age_minutes"], 45.0)

    def test_very_long_turn_escalates(self):
        result = project(**self.inflight(3 * 30 * 3))
        self.assertEqual(by_rule(result, "codex-turn-long")["severity"],
                         "needs-attention")

    def test_short_turn_does_not_alert(self):
        result = project(**self.inflight(2))
        self.assertIsNone(by_rule(result, "codex-turn-long"))

    def test_no_inflight_turn_does_not_alert(self):
        result = project(**base_status(supervisor_turn_inflight=None))
        self.assertIsNone(by_rule(result, "codex-turn-long"))


class CompletionUnconsumedTests(unittest.TestCase):
    def committed(self, minutes_ago, message_id=700701):
        return base_status(
            active_task_completion_status="COMPLETION_COMMITTED",
            active_task_completion={
                "status": "COMPLETION_COMMITTED",
                "committed_at": iso(NOW - timedelta(minutes=minutes_ago))},
            last_authorized_dispatch=dispatch(message_id),
            active_task=dispatch(message_id))

    def test_old_committed_completion_warns(self):
        result = project(**self.committed(60))
        alert = by_rule(result, "completion-unconsumed")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["evidence"]["message_id"], 700701)

    def test_very_old_committed_completion_escalates(self):
        result = project(**self.committed(3 * 30 * 3))
        self.assertEqual(by_rule(result, "completion-unconsumed")["severity"],
                         "needs-attention")

    def test_recent_commit_does_not_alert(self):
        result = project(**self.committed(5))
        self.assertIsNone(by_rule(result, "completion-unconsumed"))

    def test_consumed_completion_does_not_alert(self):
        status = self.committed(600)
        status["active_task_completion_status"] = "COMPLETION_CONSUMED"
        status["active_task_completion"]["status"] = "COMPLETION_CONSUMED"
        result = project(**status)
        self.assertIsNone(by_rule(result, "completion-unconsumed"))

    def test_missing_committed_at_is_suppressed(self):
        status = self.committed(600)
        status["active_task_completion"]["committed_at"] = None
        result = project(**status)
        self.assertIsNone(by_rule(result, "completion-unconsumed"))


class SevereStateTests(unittest.TestCase):
    def test_human_review_is_needs_attention_and_must_attention(self):
        result = project(human_review=True)
        alert = by_rule(result, "human-review")
        self.assertEqual(alert["severity"], "needs-attention")
        self.assertEqual(alert["notification_class"], "must-attention")
        self.assertEqual(alert["notification_kind"], "HUMAN_REVIEW")

    def test_stop_flag_warns_without_notification(self):
        result = project(stop=True)
        alert = by_rule(result, "stop-flag")
        self.assertEqual(alert["severity"], "warning")
        self.assertEqual(alert["notification_class"], "none")

    def test_orchestrator_error_is_needs_attention(self):
        result = project(runtime_status="ORCHESTRATOR_ERROR")
        alert = by_rule(result, "orchestrator-error")
        self.assertEqual(alert["severity"], "needs-attention")
        self.assertEqual(alert["notification_kind"], "ORCHESTRATOR_ERROR")

    def test_blocked_project_is_needs_attention(self):
        result = project(project_status="BLOCKED")
        alert = by_rule(result, "project-blocked")
        self.assertEqual(alert["severity"], "needs-attention")
        self.assertEqual(alert["notification_kind"], "HEALTH_SEVERE")

    def test_deadline_reached_warns(self):
        result = project(runtime_status="DEADLINE_REACHED")
        self.assertEqual(by_rule(result, "deadline-reached")["severity"],
                         "warning")

    def test_retired_task_warns_with_identity(self):
        disp = dispatch(700801)
        result = project(active_task_retired=True,
                         last_authorized_dispatch=disp)
        alert = by_rule(result, "task-retired")
        self.assertEqual(alert["evidence"]["message_id"], 700801)

    def test_complete_is_informational_and_optional(self):
        result = project(**complete_doc())
        alert = by_rule(result, "project-complete")
        self.assertEqual(alert["severity"], "informational")
        self.assertEqual(alert["notification_class"],
                         "optional-informational")
        self.assertEqual(alert["notification_kind"], "COMPLETE")

    def test_verified_complete_does_not_reactivate_historical_authorization(self):
        historical = dispatch(700104, authorized_at=NOW - timedelta(hours=2),
                              expires_at=NOW - timedelta(hours=1))
        result = project(**complete_doc(last_authorized_dispatch=historical))
        self.assertEqual(rules(result), {"project-complete"})

    def test_unvalidated_complete_never_announces_completion_or_revives_history(self):
        historical = dispatch(700104, authorized_at=NOW - timedelta(hours=2),
                              expires_at=NOW - timedelta(hours=1))
        result = project(**complete_doc(last_authorized_dispatch=historical,
                                         terminal_completion=None))
        self.assertNotIn("project-complete", rules(result))
        self.assertNotIn("authorization-expiry", rules(result))
        self.assertNotIn("zcode-pickup", rules(result))

    def test_safe_pause_is_informational_and_optional(self):
        result = project(pause={"status": "PAUSED", "mode": "SAFE",
                                "requested_at": None, "resumed_at": None,
                                "paused_at": None})
        alert = by_rule(result, "safe-pause")
        self.assertEqual(alert["severity"], "informational")
        self.assertEqual(alert["notification_kind"], "SAFE_PAUSE")

    def test_interrupt_pause_is_not_a_safe_pause_alert(self):
        result = project(pause={"status": "PAUSED",
                                "mode": "INTERRUPT_CURRENT",
                                "requested_at": None, "resumed_at": None,
                                "paused_at": None})
        self.assertIsNone(by_rule(result, "safe-pause"))

    def test_running_pause_state_does_not_alert(self):
        result = project()
        self.assertIsNone(by_rule(result, "safe-pause"))


class UsageOutlierTests(unittest.TestCase):
    def usage_doc(self, totals):
        turns = [{"turn_id": f"supervisor-turn-{i:024d}",
                  "total_tokens": total}
                 for i, total in enumerate(totals)]
        return {"schema_version": 1, "generated_at": iso(NOW),
                "usage": {"recent_reported_turns": turns},
                "honesty": []}

    def flags(self, totals, **threshold_overrides):
        overrides = {"alert_thresholds": threshold_overrides} \
            if threshold_overrides else None
        thresholds = wal.resolve_thresholds(overrides)
        return wal.usage_outlier_flags(self.usage_doc(totals),
                                       thresholds=thresholds)

    def test_clear_outlier_is_flagged_with_evidence(self):
        result = self.flags([1000, 100, 100, 100])
        self.assertEqual(len(result["outliers"]), 1)
        outlier = result["outliers"][0]
        self.assertEqual(outlier["total_tokens"], 1000)
        self.assertEqual(outlier["baseline_average_tokens"], 100.0)
        self.assertEqual(outlier["baseline_samples"], 3)

    def test_normal_turn_is_not_flagged(self):
        result = self.flags([150, 100, 100, 100])
        self.assertEqual(result["outliers"], [])

    def test_minimum_sample_is_enforced(self):
        result = self.flags([1000, 100])
        self.assertEqual(result["outliers"], [])
        self.assertTrue(result["not_comparable"])

    def test_threshold_override_changes_the_factor(self):
        strict = self.flags([250, 100, 100, 100],
                            usage_outlier_factor=2.0)
        self.assertEqual(len(strict["outliers"]), 1)
        relaxed = self.flags([250, 100, 100, 100],
                             usage_outlier_factor=3.0)
        self.assertEqual(relaxed["outliers"], [])

    def test_missing_or_shapeless_usage_document_is_not_comparable(self):
        for doc in (None, {}, {"usage": {}},
                    {"usage": {"recent_reported_turns": []}}):
            result = wal.usage_outlier_flags(
                doc, thresholds=wal.resolve_thresholds(None))
            self.assertEqual(result["outliers"], [])
            self.assertTrue(result["not_comparable"])

    def test_unreported_totals_are_excluded_from_the_comparison(self):
        doc = self.usage_doc([1000, 100, 100])
        doc["usage"]["recent_reported_turns"][0]["total_tokens"] = None
        result = wal.usage_outlier_flags(
            doc, thresholds=wal.resolve_thresholds(None))
        self.assertEqual(result["outliers"], [])

    def test_outlier_appears_as_an_informational_alert(self):
        usage = self.usage_doc([1000, 100, 100, 100])
        result = wal.project_alerts(
            runtime=RUNTIME, status=base_status(), probe_ok=True,
            probe_error_code=None, usage_document=usage,
            thresholds=wal.resolve_thresholds(None), now=NOW,
            generated_at=iso(NOW))
        alert = by_rule(result, "usage-outlier")
        self.assertIsNotNone(alert)
        self.assertEqual(alert["severity"], "informational")
        self.assertEqual(alert["notification_class"],
                         "optional-informational")
        self.assertEqual(alert["notification_kind"], "HIGH_TOKEN_TURN")

    def test_outlier_rule_ignores_zcode_usage_entirely(self):
        # The usage document carries only Supervisor turn data; the zcode
        # block is never a comparable sample. This pins that no zcode turn
        # id can appear in an outlier.
        usage = self.usage_doc([1000, 100, 100, 100])
        result = wal.usage_outlier_flags(
            usage, thresholds=wal.resolve_thresholds(None))
        for outlier in result["outliers"]:
            self.assertTrue(outlier["turn_id"].startswith("supervisor-turn-"))


class TimestampParsingTests(unittest.TestCase):
    def test_aware_iso_timestamps_parse(self):
        parsed = wal.parse_timestamp("2026-09-12T15:00:00+00:00")
        self.assertEqual(parsed, NOW)

    def test_z_suffix_parses(self):
        parsed = wal.parse_timestamp("2026-09-12T15:00:00Z")
        self.assertEqual(parsed, NOW)

    def test_naive_and_garbage_timestamps_are_unavailable(self):
        for bad in ("2026-09-12T15:00:00", "not-a-time", "", None, 5,
                    "2026-09-12T15:00:00+00:00extra"):
            self.assertIsNone(wal.parse_timestamp(bad))


if __name__ == "__main__":
    unittest.main()
