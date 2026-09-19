"""P4 Web Console history projection tests (pure, offline).

Covers the read-only Timeline/round projection layer that turns the v1.2
control plane's `timeline --json` documents into bounded, deterministic,
round-grouped presentations: fail-closed query parsing, round grouping by
stable MESSAGE_ID, honest lifecycle status derivation, deterministic
ordering/pagination/search/filter, unusable-record surfacing (never silent
skipping), determinism, and the no-fabrication contract (no percentages,
no token usage, no invented timestamps).
No HTTP, no subprocess, no filesystem access, no clock.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_history as wch


def dispatch_event(mid, task="TASK-T", stage="stage-t", attempt=1,
                   integrity="AUTHORIZED_VALID", archived_at=None,
                   nonce="n" * 24, turn="turn-1", **extra):
    record = {"MESSAGE_ID": mid, "TASK_ID": task, "STAGE_ID": stage,
              "ATTEMPT": attempt, "NONCE": nonce, "integrity": integrity,
              "trust_status": integrity, "archived_at": archived_at,
              "supervisor_turn_id": turn,
              "decision_receipt_sha256": "a" * 64,
              "originating_control_revision": 3}
    record.update(extra)
    return {"type": "SUPERVISOR_DISPATCH", "at": archived_at,
            "MESSAGE_ID": mid, "record": record}


def completion_event(mid, status="COMPLETION_SEALED", committed_at=None,
                     integrity="OK", task="TASK-T", stage="stage-t",
                     attempt=1, receipt_status="COMPLETE", **extra):
    receipt = {"MESSAGE_ID": mid, "TASK_ID": task, "STAGE_ID": stage,
               "ATTEMPT": attempt, "NONCE": "n" * 24,
               "STATUS": receipt_status, "OUTCOME": "all good",
               }
    record = {"MESSAGE_ID": mid, "TASK_ID": task, "STAGE_ID": stage,
              "ATTEMPT": attempt, "NONCE": "n" * 24, "STATUS": status,
              "integrity": integrity, "COMMITTED_AT": committed_at,
              "CONSUMED_AT": None, "SEALED_AT": None,
              "COMMIT_ID": f"completion-{mid}-abc", "RECEIPT": receipt,
              "artifact_provenance": {"integrity": "OK", "publications": [
                  {**{k: receipt[k] for k in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")},
                   "path": "reports/x.md", "sha256": "b" * 64}]}}
    record.update(extra)
    return {"type": "EXECUTOR_COMPLETION", "at": committed_at,
            "MESSAGE_ID": mid, "record": record}


def intervention_event(target, iid="H-001", mode="STEER",
                       submitted_at=None, status="CONSUMED", **extra):
    record = {"intervention_id": iid, "mode": mode,
              "target_message_id": target, "submitted_at": submitted_at,
              "status": status, "integrity": "OK",
              "instruction_text": "please adjust"}
    record.update(extra)
    return {"type": "HUMAN_INTERVENTION", "at": submitted_at,
            "MESSAGE_ID": target, "record": record}


class QueryParsingTests(unittest.TestCase):
    def test_defaults(self):
        params = wch.parse_timeline_query("")
        self.assertEqual(params["page"], 1)
        self.assertEqual(params["page_size"], 20)
        self.assertEqual(params["order"], "newest")
        self.assertEqual(params["kind"], "all")
        self.assertEqual(params["q"], "")

    def test_all_params_accepted(self):
        params = wch.parse_timeline_query(
            "page=3&page_size=50&order=oldest&kind=dispatch&q=caf%C3%A9")
        self.assertEqual(params, {"page": 3, "page_size": 50,
                                  "order": "oldest", "kind": "dispatch",
                                  "q": "café"})

    def test_page_bounds(self):
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page=0")
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page=-1")
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page=abc")
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page=1.5")
        self.assertEqual(wch.parse_timeline_query("page=1")["page"], 1)

    def test_page_size_bounds(self):
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page_size=0")
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page_size=101")
        self.assertEqual(wch.parse_timeline_query("page_size=100")
                         ["page_size"], 100)

    def test_order_and_kind_value_spaces(self):
        for bad in ("order=sideWAYS", "order=", "kind=decision",
                    "kind=ALL"):
            with self.assertRaises(wch.TimelineQueryError):
                wch.parse_timeline_query(bad)

    def test_unknown_parameter_rejected(self):
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("root=C:/elsewhere")
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("message_id=700501")

    def test_duplicate_parameter_rejected(self):
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("page=1&page=2")

    def test_search_length_bounded(self):
        with self.assertRaises(wch.TimelineQueryError):
            wch.parse_timeline_query("q=" + "x" * 121)
        self.assertEqual(wch.parse_timeline_query("q=" + "x" * 120)["q"],
                         "x" * 120)

    def test_search_is_stripped_and_blank_is_empty(self):
        self.assertEqual(wch.parse_timeline_query("q=%20%20")["q"], "")
        self.assertEqual(wch.parse_timeline_query("q=+700501+")["q"],
                         "700501")


class GroupingTests(unittest.TestCase):
    def test_empty_history_groups_nothing(self):
        result = wch.project_timeline([], wch.parse_timeline_query(""),
                                       generated_at="2026-09-12T00:00:00+00:00")
        self.assertEqual(result["rounds"], [])
        self.assertEqual(result["totals"]["rounds"], 0)

    def test_dispatch_and_completion_merge_into_one_round(self):
        events = [dispatch_event(700501, archived_at="2026-09-12T01:00:00+00:00"),
                  completion_event(700501, committed_at="2026-09-12T01:02:00+00:00")]
        result = wch.project_timeline(events, wch.parse_timeline_query(""),
                                      generated_at="x")
        self.assertEqual(result["totals"]["rounds"], 1)
        round_ = result["rounds"][0]
        self.assertEqual(round_["message_id"], 700501)
        self.assertTrue(round_["has_dispatch"])
        self.assertTrue(round_["has_completion"])

    def test_intervention_joins_target_round(self):
        events = [dispatch_event(700501),
                  intervention_event(700501, iid="H-009")]
        result = wch.project_timeline(events, wch.parse_timeline_query(""),
                                      generated_at="x")
        self.assertEqual(result["totals"]["rounds"], 1)
        self.assertEqual(result["rounds"][0]["intervention_count"], 1)

    def test_unbound_intervention_is_surfaced_not_dropped(self):
        events = [intervention_event(None, iid="H-010")]
        result = wch.project_timeline(events, wch.parse_timeline_query(""),
                                      generated_at="x")
        self.assertEqual(result["totals"]["rounds"], 0)
        self.assertEqual(len(result["honesty"]["unbound_events"]), 1)
        self.assertEqual(result["honesty"]["unbound_events"][0]["intervention_id"],
                         "H-010")

    def test_unusable_events_are_surfaced_with_reasons(self):
        events = [
            "not-a-dict",
            {"type": "MYSTERY", "at": None, "MESSAGE_ID": 1, "record": {}},
            {"type": "SUPERVISOR_DISPATCH", "at": None, "MESSAGE_ID": None,
             "record": {}},
            dispatch_event(700501),
        ]
        result = wch.project_timeline(events, wch.parse_timeline_query(""),
                                      generated_at="x")
        self.assertEqual(result["totals"]["rounds"], 1)
        self.assertEqual(result["totals"]["unusable_events"], 3)
        reasons = {entry["reason_code"]
                   for entry in result["honesty"]["unusable_events"]}
        self.assertIn("EVENT_NOT_AN_OBJECT", reasons)
        self.assertIn("EVENT_TYPE_UNKNOWN", reasons)
        self.assertIn("EVENT_MESSAGE_ID_INVALID", reasons)

    def test_unusable_event_list_is_bounded_but_counted(self):
        events = ["bad"] * 30 + [dispatch_event(700501)]
        result = wch.project_timeline(events, wch.parse_timeline_query(""),
                                      generated_at="x")
        self.assertLessEqual(len(result["honesty"]["unusable_events"]), 20)
        self.assertEqual(result["totals"]["unusable_events"], 30)


class StatusDerivationTests(unittest.TestCase):
    def project(self, events):
        return wch.project_timeline(events, wch.parse_timeline_query(""),
                                    generated_at="x")["rounds"]

    def test_completed_round(self):
        round_ = self.project([dispatch_event(1),
                               completion_event(1)])[0]
        self.assertEqual(round_["status"], "COMPLETED")
        self.assertEqual(round_["completion_status"], "COMPLETION_SEALED")

    def test_dispatched_without_completion(self):
        round_ = self.project([dispatch_event(1)])[0]
        self.assertEqual(round_["status"], "DISPATCHED_NO_COMPLETION")

    def test_untrusted_dispatch_integrity_is_reflected(self):
        for integrity in ("UNAUTHORIZED", "INCOMPLETE", "CORRUPT"):
            round_ = self.project(
                [dispatch_event(1, integrity=integrity,
                                error="seal missing")])[0]
            self.assertEqual(round_["status"], "DISPATCH_UNTRUSTED")
            self.assertEqual(round_["dispatch_integrity"], integrity)

    def test_completion_without_dispatch_is_an_anomaly(self):
        round_ = self.project([completion_event(1)])[0]
        self.assertEqual(round_["status"], "COMPLETION_WITHOUT_DISPATCH")

    def test_broken_completion_integrity_is_surfaced(self):
        round_ = self.project([dispatch_event(1),
                               completion_event(1, integrity="HASH_MISMATCH")])[0]
        self.assertEqual(round_["status"], "COMPLETION_UNTRUSTED")

    def test_intervention_only_round(self):
        round_ = self.project([intervention_event(5)])[0]
        self.assertEqual(round_["status"], "INTERVENTION_ONLY")

    def test_contradictory_history_fails_closed(self):
        round_ = self.project([dispatch_event(1), dispatch_event(1),
                               completion_event(1)])[0]
        self.assertEqual(round_["status"], "CONTRADICTORY_HISTORY")
        self.assertTrue(round_["honesty"]["contradictions"])

    def test_identity_conflict_between_records_is_flagged(self):
        round_ = self.project([dispatch_event(1, task="TASK-A"),
                               completion_event(1, task="TASK-B")])[0]
        self.assertEqual(round_["identity"]["task_id"], "TASK-A")
        self.assertTrue(round_["honesty"]["contradictions"])


class FactHonestyTests(unittest.TestCase):
    def project_round(self, events):
        return wch.project_timeline(events, wch.parse_timeline_query(""),
                                    generated_at="x")["rounds"][0]

    def test_duration_only_from_authoritative_timestamps(self):
        round_ = self.project_round(
            [dispatch_event(1, archived_at="2026-09-12T01:00:00+00:00"),
             completion_event(1, committed_at="2026-09-12T01:02:30+00:00")])
        self.assertEqual(round_["duration_seconds"], 150)
        round_ = self.project_round([dispatch_event(1)])
        self.assertIsNone(round_["duration_seconds"])

    def test_negative_or_unparseable_duration_is_not_reported(self):
        round_ = self.project_round(
            [dispatch_event(1, archived_at="2026-09-12T02:00:00+00:00"),
             completion_event(1, committed_at="2026-09-12T01:00:00+00:00")])
        self.assertIsNone(round_["duration_seconds"])
        round_ = self.project_round(
            [dispatch_event(1, archived_at="not-a-time"),
             completion_event(1, committed_at="also-not-a-time")])
        self.assertIsNone(round_["duration_seconds"])

    def test_completion_summary_only_from_receipt_status(self):
        round_ = self.project_round([dispatch_event(1), completion_event(1)])
        self.assertEqual(round_["completion_receipt_status"], "COMPLETE")
        round_ = self.project_round([dispatch_event(1)])
        self.assertIsNone(round_["completion_receipt_status"])

    def test_no_percent_or_token_words_anywhere(self):
        events = [dispatch_event(1), completion_event(1),
                  intervention_event(1)]
        result = wch.project_timeline(events, wch.parse_timeline_query("q=1"),
                                      generated_at="x")
        blob = json.dumps(result, ensure_ascii=False).lower()
        self.assertNotIn("percent", blob)
        self.assertNotIn("% complete", blob)
        self.assertNotIn("token", blob)


class OrderingPaginationSearchTests(unittest.TestCase):
    def events(self):
        return [
            dispatch_event(7001, archived_at="2026-09-12T01:00:00+00:00"),
            dispatch_event(7002, archived_at="2026-09-12T03:00:00+00:00"),
            dispatch_event(7003, archived_at="2026-09-12T02:00:00+00:00"),
            dispatch_event(7004, archived_at=None),
        ]

    def test_newest_first_with_timestampless_rounds_last(self):
        result = wch.project_timeline(self.events(),
                                      wch.parse_timeline_query(""),
                                      generated_at="x")
        self.assertEqual([r["message_id"] for r in result["rounds"]],
                         [7002, 7003, 7001, 7004])

    def test_oldest_first_with_timestampless_rounds_last(self):
        result = wch.project_timeline(
            self.events(), wch.parse_timeline_query("order=oldest"),
            generated_at="x")
        self.assertEqual([r["message_id"] for r in result["rounds"]],
                         [7001, 7003, 7002, 7004])

    def test_pagination_is_deterministic(self):
        result = wch.project_timeline(self.events(),
                                      wch.parse_timeline_query("page_size=2"),
                                      generated_at="x")
        self.assertEqual(result["totals"]["rounds"], 4)
        self.assertEqual(result["totals"]["pages"], 2)
        self.assertEqual([r["message_id"] for r in result["rounds"]],
                         [7002, 7003])
        result = wch.project_timeline(self.events(),
                                      wch.parse_timeline_query("page_size=2&page=2"),
                                      generated_at="x")
        self.assertEqual([r["message_id"] for r in result["rounds"]],
                         [7001, 7004])

    def test_page_beyond_end_is_empty_not_an_error(self):
        result = wch.project_timeline(self.events(),
                                      wch.parse_timeline_query("page_size=2&page=9"),
                                      generated_at="x")
        self.assertEqual(result["rounds"], [])
        self.assertEqual(result["totals"]["pages"], 2)
        self.assertEqual(result["query"]["page"], 9)

    def test_search_matches_identity_fields_case_insensitively(self):
        events = [dispatch_event(7001, task="Compile-Report"),
                  dispatch_event(7002, stage="Ship-Stage")]
        by_task = wch.project_timeline(events, wch.parse_timeline_query("q=compile"),
                                       generated_at="x")
        self.assertEqual([r["message_id"] for r in by_task["rounds"]], [7001])
        by_stage = wch.project_timeline(events, wch.parse_timeline_query("q=SHIP"),
                                        generated_at="x")
        self.assertEqual([r["message_id"] for r in by_stage["rounds"]], [7002])
        by_id = wch.project_timeline(events, wch.parse_timeline_query("q=7002"),
                                     generated_at="x")
        self.assertEqual([r["message_id"] for r in by_id["rounds"]], [7002])
        miss = wch.project_timeline(events, wch.parse_timeline_query("q=zzz"),
                                    generated_at="x")
        self.assertEqual(miss["rounds"], [])

    def test_kind_filter_selects_rounds_containing_the_kind(self):
        events = [dispatch_event(7001), completion_event(7001),
                  dispatch_event(7002), intervention_event(7003)]
        only_dispatch = wch.project_timeline(
            events, wch.parse_timeline_query("kind=dispatch"),
            generated_at="x")
        self.assertEqual(sorted(r["message_id"] for r in only_dispatch["rounds"]),
                         [7001, 7002])
        only_completion = wch.project_timeline(
            events, wch.parse_timeline_query("kind=completion"),
            generated_at="x")
        self.assertEqual([r["message_id"] for r in only_completion["rounds"]],
                         [7001])
        only_intervention = wch.project_timeline(
            events, wch.parse_timeline_query("kind=intervention"),
            generated_at="x")
        self.assertEqual([r["message_id"] for r in only_intervention["rounds"]],
                         [7003])

    def test_search_and_filter_and_pagination_compose(self):
        events = [dispatch_event(7001, task="Alpha"),
                  completion_event(7001),
                  dispatch_event(7002, task="Alpha"),
                  dispatch_event(7003, task="Beta")]
        result = wch.project_timeline(
            events, wch.parse_timeline_query("q=alpha&kind=dispatch&page_size=1"),
            generated_at="x")
        self.assertEqual(result["totals"]["rounds"], 2)
        self.assertEqual([r["message_id"] for r in result["rounds"]], [7002])
        self.assertEqual(result["totals"]["pages"], 2)

    def test_unicode_and_hostile_search_values_round_trip(self):
        events = [dispatch_event(7001, task="任务-<script>&")]
        result = wch.project_timeline(events,
                                      wch.parse_timeline_query("q=%E4%BB%BB%E5%8A%A1"),
                                      generated_at="x")
        self.assertEqual([r["message_id"] for r in result["rounds"]], [7001])


class RoundDetailTests(unittest.TestCase):
    """interpret_round_detail composes one Task Detail document."""

    @staticmethod
    def results(mid=700501, with_dispatch=True, with_completion=True,
                interventions=0, dispatch_integrity="AUTHORIZED_VALID",
                completion_integrity="OK", error=None):
        dispatch_result = {"ran": True, "exit_code": 0, "failure": None,
                           "document": None}
        if with_dispatch:
            record = dispatch_event(mid, integrity=dispatch_integrity,
                                    error=error)["record"]
            if dispatch_integrity == "AUTHORIZED_VALID":
                record["exact_dispatch"] = "# TO_ZCODE — exact archived bytes\n"
            dispatch_result["document"] = record
        completion_result = {"ran": True, "exit_code": 0, "failure": None,
                             "document": None}
        if with_completion:
            completion_result["document"] = completion_event(
                mid, integrity=completion_integrity)["record"]
        records = [intervention_event(mid, iid=f"H-{i}")["record"]
                   for i in range(interventions)]
        interventions_result = {"ran": True, "exit_code": 0,
                                "failure": None, "document": records}
        return dispatch_result, completion_result, interventions_result

    def test_found_round_composes_all_blocks(self):
        dispatch, completion, interventions = self.results()
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions,
            decision={"available": True, "verified": True,
                      "decision_decision": "CONTINUE",
                      "decision_reason": "results accepted"})
        self.assertTrue(detail["found"])
        self.assertEqual(detail["identity"]["message_id"], 700501)
        self.assertTrue(detail["dispatch"]["available"])
        self.assertEqual(detail["dispatch"]["exact_dispatch"],
                         "# TO_ZCODE — exact archived bytes\n")
        self.assertTrue(detail["completion"]["available"])
        self.assertEqual(len(detail["interventions"]), 0)
        self.assertTrue(detail["decision"]["available"])

    def test_not_found_when_no_source_knows_the_message(self):
        dispatch, completion, interventions = self.results(
            with_dispatch=False, with_completion=False)
        dispatch["document"] = {"ok": False, "error": "no archived Supervisor "
                                "dispatch for MESSAGE_ID=700501",
                                "error_type": "ControlError"}
        completion["document"] = {"ok": False, "error": "no authoritative "
                                  "Executor completion for MESSAGE_ID=700501",
                                  "error_type": "ControlError"}
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False})
        self.assertFalse(detail["found"])

    def test_partial_history_is_honest_about_missing_sources(self):
        dispatch, completion, interventions = self.results(
            with_completion=False)
        completion["document"] = {"ok": False, "error": "no authoritative "
                                  "Executor completion for MESSAGE_ID=700501",
                                  "error_type": "ControlError"}
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False})
        self.assertTrue(detail["found"])
        self.assertFalse(detail["completion"]["available"])
        self.assertTrue(detail["honesty"]["notes"])

    def test_transport_failure_is_surfaced_not_hidden(self):
        dispatch, completion, interventions = self.results()
        completion["ran"] = False
        completion["failure"] = "TIMEOUT"
        completion["document"] = None
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False})
        self.assertTrue(detail["found"])
        self.assertFalse(detail["completion"]["available"])
        codes = [entry["code"] for entry in detail["honesty"]["control"]]
        self.assertIn("COMPLETION_TIMEOUT", codes)

    def test_non_dict_completion_document_degrades_to_unavailable(self):
        # A control plane that answers `feedback` with valid JSON of the
        # wrong top-level type must degrade to an unavailable completion
        # block plus an honesty note — never crash the composition and
        # never invent completion data.
        for malformed in ([{"unexpected": "shape"}], "ok", 7, True):
            with self.subTest(document=malformed):
                dispatch, completion, interventions = self.results()
                completion["document"] = malformed
                detail = wch.interpret_round_detail(
                    700501, dispatch_result=dispatch,
                    completion_result=completion,
                    interventions_result=interventions,
                    decision={"available": False})
                self.assertTrue(detail["found"])
                self.assertFalse(detail["completion"]["available"])
                self.assertIsNone(detail["completion"]["receipt"])
                self.assertIsNone(detail["completion"]["status"])
                self.assertIn("the completion query returned unusable output",
                              detail["honesty"]["notes"])
                self.assertTrue(detail["dispatch"]["available"])

    def test_artifact_metadata_comes_from_verified_runtime_publications(self):
        dispatch, completion, interventions = self.results()
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications={"state": "ok", "records": [], "reason": None})
        self.assertTrue(detail["artifacts"]["available"])
        self.assertEqual(detail["artifacts"]["state"], "final")
        self.assertEqual(detail["artifacts"]["paths"],
                         [{"path": "reports/x.md", "sha256": "b" * 64,
                           "status": "final", "message_id": 700501,
                           "task_id": "TASK-T", "stage_id": "stage-t",
                           "attempt": 1, "published_at": None,
                           "commit_id": "completion-700501-abc"}])
        self.assertIn("historical bytes are not retained",
                      detail["artifacts"]["note"].lower())

    def test_final_publication_ids_come_from_the_verified_entry(self):
        completion = self.results()[1]
        completion["document"]["COMMIT_ID"] = "completion-700501-xyz"
        detail = wch.interpret_round_detail(
            700501, dispatch_result=self.results()[0],
            completion_result=completion,
            interventions_result=self.results()[2],
            decision={"available": False})
        path = detail["artifacts"]["paths"][0]
        self.assertEqual(path["commit_id"], "completion-700501-xyz")
        self.assertEqual(path["message_id"], 700501)
        self.assertEqual(path["status"], "final")

    def test_dispatched_no_completion_with_publications_is_provisional(self):
        dispatch, completion, interventions = self.results(
            with_completion=False)
        completion["document"] = {"ok": False, "error": "no authoritative "
                                  "Executor completion for MESSAGE_ID=700501",
                                  "error_type": "ControlError"}
        records = [{"path": "evidence/partial.txt", "sha256": "c" * 64,
                    "published_at": "2026-09-12T01:01:00+00:00",
                    "task_id": "TASK-T", "stage_id": "stage-t", "attempt": 1,
                    "commit_id": "completion-700501-" + "0" * 24,
                    "message_id": 700501}]
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications={"state": "ok", "records": records, "reason": None})
        block = detail["artifacts"]
        self.assertFalse(block["available"])
        self.assertEqual(block["state"], "provisional")
        self.assertFalse(block["complete"])
        self.assertEqual(block["paths"][0]["path"], "evidence/partial.txt")
        self.assertEqual(block["paths"][0]["status"], "provisional")
        self.assertIn("provisional", block["note"].lower())

    def test_dispatched_no_completion_without_publications_says_none(self):
        dispatch, completion, interventions = self.results(
            with_completion=False)
        completion["document"] = {"ok": False, "error": "no authoritative "
                                  "Executor completion for MESSAGE_ID=700501",
                                  "error_type": "ControlError"}
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications={"state": "ok", "records": [], "reason": None})
        block = detail["artifacts"]
        self.assertFalse(block["available"])
        self.assertEqual(block["state"], "none")
        self.assertEqual(block["paths"], [])
        self.assertIn("has not published", block["note"])

    def test_untrusted_completion_never_presents_final_metadata(self):
        dispatch, completion, interventions = self.results(
            completion_integrity="HASH_MISMATCH")
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications={"state": "ok", "records": [], "reason": None})
        block = detail["artifacts"]
        self.assertFalse(block["available"])
        self.assertEqual(block["state"], "unavailable")
        self.assertEqual(block["paths"], [])
        self.assertIn("HASH_MISMATCH", block["reason"])
        self.assertIn("not presented as final", block["reason"])

    def test_untrusted_completion_still_shows_provisional_records(self):
        dispatch, completion, interventions = self.results(
            completion_integrity="HASH_MISMATCH")
        records = [{"path": "reports/r.md", "sha256": "b" * 64,
                    "published_at": "2026-09-12T01:01:00+00:00",
                    "task_id": "TASK-T", "stage_id": "stage-t", "attempt": 1,
                    "commit_id": "completion-700501-" + "0" * 24,
                    "message_id": 700501}]
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications={"state": "ok", "records": records, "reason": None})
        block = detail["artifacts"]
        self.assertFalse(block["available"])
        self.assertEqual(block["state"], "provisional")
        self.assertTrue(all(path["status"] == "provisional"
                            for path in block["paths"]))
        self.assertIn("HASH_MISMATCH", block["reason"])

    def test_unverified_dispatch_identity_hides_precompletion_lookup(self):
        dispatch, completion, interventions = self.results(
            with_completion=False, dispatch_integrity="CORRUPT",
            error="metadata is corrupt")
        completion["document"] = {"ok": False, "error": "no authoritative "
                                  "Executor completion for MESSAGE_ID=700501",
                                  "error_type": "ControlError"}
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications={"state": "invalid", "records": [],
                          "reason": "DISPATCH_IDENTITY_UNAVAILABLE"})
        block = detail["artifacts"]
        self.assertEqual(block["state"], "unavailable")
        self.assertIn("DISPATCH_IDENTITY_UNAVAILABLE", block["reason"])

    def test_unusable_publication_projection_degrades_honestly(self):
        dispatch, completion, interventions = self.results(
            with_completion=False)
        completion["document"] = {"ok": False, "error": "no authoritative "
                                  "Executor completion for MESSAGE_ID=700501",
                                  "error_type": "ControlError"}
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False},
            publications="not a dict")
        block = detail["artifacts"]
        self.assertEqual(block["state"], "unavailable")
        self.assertIn("invalid publication projection", block["reason"])

    def test_artifacts_without_publications_argument_still_composes(self):
        dispatch, completion, interventions = self.results()
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False})
        self.assertTrue(detail["artifacts"]["available"])
        self.assertEqual(detail["artifacts"]["state"], "final")

    def test_exact_dispatch_only_from_authorized_valid(self):
        dispatch, completion, interventions = self.results(
            dispatch_integrity="UNAUTHORIZED", error="authorization seal is "
            "missing")
        self.assertNotIn("exact_dispatch",
                         dispatch["document"])
        detail = wch.interpret_round_detail(
            700501, dispatch_result=dispatch, completion_result=completion,
            interventions_result=interventions, decision={"available": False})
        self.assertTrue(detail["dispatch"]["available"])
        self.assertIsNone(detail["dispatch"]["exact_dispatch"])
        self.assertEqual(detail["dispatch"]["integrity"], "UNAUTHORIZED")


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_output(self):
        events = [dispatch_event(700501, archived_at="2026-09-12T01:00:00+00:00"),
                  completion_event(700501,
                                   committed_at="2026-09-12T01:02:00+00:00"),
                  intervention_event(700501, submitted_at="2026-09-12T01:05:00+00:00")]
        first = wch.project_timeline(events, wch.parse_timeline_query("q=a"),
                                     generated_at="gen")
        second = wch.project_timeline(events, wch.parse_timeline_query("q=a"),
                                      generated_at="gen")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
