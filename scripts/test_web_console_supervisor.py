"""P8 pure-layer tests for the Console's Supervisor-turn observability.

Covers the strict SUPERVISOR-TURN-OBSERVABILITY-V1 record validation, the
bounded turn list/detail/usage projections, receipt-binding verification,
fail-closed handling of malformed/hostile data, and the honest active-versus-
pending Supervisor configuration document.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_supervisor as wsup
import web_console_control
import web_console_setup
import supervisor_control as sc


def canonical_receipt_bytes(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def sample_record(**overrides) -> dict:
    value = {
        "schema": "SUPERVISOR-TURN-OBSERVABILITY-V1",
        "schema_version": 1,
        "turn_id": "supervisor-turn-" + "a1" * 12,
        "PROJECT_ID": "demo-project",
        "invocation": {"reason": "EXECUTOR_RESULT_READY", "event": None},
        "started_at": "2026-09-12T10:00:00+00:00",
        "finished_at": "2026-09-12T10:01:30+00:00",
        "duration_seconds": 90.5,
        "supervisor_config": {"source": "fixed_policy",
                              "config_revision": None,
                              "model": "gpt-5.6-sol",
                              "reasoning_effort": "high",
                              "queued_at": None},
        "usage": {"reported": False, "input_tokens": None,
                  "output_tokens": None, "total_tokens": None,
                  "source": None,
                  "note": "the Codex environment did not report token usage "
                          "for this turn"},
        "context_manifest": {"project_state": {"path": "projects/demo-project/"
                                                       "project_state.json",
                                               "truncated": False}},
        "decision": {"committed": True,
                     "receipt_file": "control/supervisor_decisions/x.json",
                     "receipt_sha256": None,
                     "decision": {"decision": "CONTINUE",
                                  "reason": "stage complete; dispatch next"},
                     "decision_sha256": "b" * 64,
                     "decision_summary": "stage complete; dispatch next",
                     "decision_history_index": 3,
                     "resulting_status": "WAITING_EXECUTOR"},
        "dispatch_linkage": {"MESSAGE_ID": 700126, "TASK_ID": "T-700126",
                             "STAGE_ID": "S-700126", "ATTEMPT": 1,
                             "NONCE": "nonce-700126",
                             "dispatch_sha256": "c" * 64},
        "intervention_ids": ["H-004"],
        "outcome": {"committed": True, "stale": False,
                    "recovered_after_crash": False,
                    "candidate_validation_failed": False, "error": None},
    }
    value.update(overrides)
    return value


def write_record(root: Path, record: dict, name: str | None = None) -> Path:
    directory = root / "control" / "supervisor_turns"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name or f"{record['turn_id']}.json")
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def record_with_decision_fields(**fields) -> dict:
    """A valid sample record with decision-block fields overridden."""
    record = sample_record()
    record["decision"].update(fields)
    return record


class TurnRecordValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsup-pure-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_valid_record_loads(self):
        path = write_record(self.root, sample_record())
        record = wsup.load_turn_record(path)
        self.assertEqual(record["turn_id"], sample_record()["turn_id"])

    def test_missing_or_oversized_or_non_json_is_refused(self):
        path = self.root / "control" / "supervisor_turns" / "x.json"
        path.parent.mkdir(parents=True)
        for label, content in (
                ("oversized", json.dumps(sample_record()) + " " * (wsup.MAX_TURN_RECORD_BYTES + 1)),
                ("not json", "garbage"),
                ("array", "[]"),
                ("empty", "")):
            with self.subTest(case=label):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(wsup.TurnRecordError):
                    wsup.load_turn_record(path)

    def test_schema_and_key_set_are_strict(self):
        bad = sample_record(schema="SOMETHING-ELSE")
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, bad))
        extra = sample_record()
        extra["surprise"] = 1
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, extra))
        missing = sample_record()
        del missing["usage"]
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, missing))

    def test_nested_block_keys_are_strict(self):
        cases = {
            "usage extra key": ("usage", {"reported": False, "extra": 1}),
            "decision extra key": ("decision", {"committed": False}),
            "outcome extra key": ("outcome", {"committed": False}),
            "config extra key": ("supervisor_config", {"source": "queued"}),
        }
        for label, (key, block) in cases.items():
            with self.subTest(case=label):
                bad = sample_record(**{key: block})
                with self.assertRaises(wsup.TurnRecordError):
                    wsup.load_turn_record(write_record(self.root, bad))

    def test_usage_consistency_is_enforced(self):
        not_reported_but_counted = sample_record(usage={
            "reported": False, "input_tokens": 5, "output_tokens": None,
            "total_tokens": None, "source": None, "note": None})
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root,
                                               not_reported_but_counted))
        reported_without_values = sample_record(usage={
            "reported": True, "input_tokens": None, "output_tokens": None,
            "total_tokens": None, "source": "codex_output_token_usage",
            "note": None})
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root,
                                               reported_without_values))
        negative = sample_record(usage={
            "reported": True, "input_tokens": -2, "output_tokens": 1,
            "total_tokens": None, "source": "s", "note": None})
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, negative))

    def test_identity_and_linkage_shapes_are_enforced(self):
        bad_turn_id = sample_record(turn_id="../escape")
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, bad_turn_id))
        bad_linkage = sample_record(
            dispatch_linkage={"MESSAGE_ID": 1, "TASK_ID": "t",
                              "STAGE_ID": "s", "ATTEMPT": 1, "NONCE": "n",
                              "dispatch_sha256": "c" * 64, "extra": True})
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, bad_linkage))
        bad_linkage_hash = sample_record(
            dispatch_linkage={"MESSAGE_ID": 1, "TASK_ID": "t",
                              "STAGE_ID": "s", "ATTEMPT": 1, "NONCE": "n",
                              "dispatch_sha256": "zz"})
        with self.assertRaises(wsup.TurnRecordError):
            wsup.load_turn_record(write_record(self.root, bad_linkage_hash))

    def test_unicode_decision_summary_round_trips(self):
        summary = "阶段完成 · продолжить ▚ next"
        record = record_with_decision_fields(
            decision_summary=summary,
            decision={"decision": "CONTINUE", "reason": summary})
        loaded = wsup.load_turn_record(write_record(self.root, record))
        self.assertEqual(loaded["decision"]["decision_summary"], summary)


class ReceiptBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsup-receipt-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _record_with_receipt(self, receipt: dict) -> dict:
        receipt_file = "control/supervisor_decisions/turn.json"
        target = self.root / receipt_file
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_receipt_bytes(receipt))
        return record_with_decision_fields(
            receipt_file=receipt_file,
            receipt_sha256=hashlib.sha256(
                canonical_receipt_bytes(receipt)).hexdigest())

    def test_matching_receipt_verifies(self):
        record = self._record_with_receipt({"turn_id": "t1", "v": 1})
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertEqual(view["turns"][0]["receipt"]["integrity"],
                         "RECEIPT_VERIFIED")

    def test_hash_mismatch_is_flagged_not_hidden(self):
        record = self._record_with_receipt({"turn_id": "t1", "v": 1})
        record["decision"]["receipt_sha256"] = "d" * 64
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertEqual(view["turns"][0]["receipt"]["integrity"],
                         "RECEIPT_MISMATCH")

    def test_missing_receipt_is_unavailable(self):
        record = record_with_decision_fields(
            receipt_file="control/supervisor_decisions/gone.json",
            receipt_sha256="d" * 64)
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertEqual(view["turns"][0]["receipt"]["integrity"],
                         "RECEIPT_UNAVAILABLE")

    def test_hostile_receipt_path_is_never_read(self):
        outside = self.root / "TO_ZCODE.md"
        outside.write_bytes(b"secret dispatch bytes")
        record = record_with_decision_fields(
            receipt_file="../TO_ZCODE.md",
            receipt_sha256=hashlib.sha256(b"secret dispatch bytes").hexdigest())
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        turn = view["turns"][0]
        self.assertNotEqual(turn["receipt"]["integrity"], "RECEIPT_VERIFIED")
        self.assertTrue(any("refused" in note.lower() or "path" in note.lower()
                            for note in view["honesty"]))

    def test_receipt_outside_control_is_refused(self):
        target = self.root / "handoff" / "other.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(canonical_receipt_bytes({"v": 1}))
        record = record_with_decision_fields(
            receipt_file="handoff/other.json",
            receipt_sha256=hashlib.sha256(
                canonical_receipt_bytes({"v": 1})).hexdigest())
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertNotEqual(view["turns"][0]["receipt"]["integrity"],
                            "RECEIPT_VERIFIED")

    def test_no_binding_is_not_verified_nor_flagged(self):
        record = record_with_decision_fields(receipt_file=None,
                                             receipt_sha256=None)
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertIsNone(view["turns"][0]["receipt"]["integrity"])


class TurnListTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsup-list-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_records_directory_is_honest_empty(self):
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertEqual(view["turns"], [])
        self.assertEqual(view["totals"]["total_records"], 0)
        self.assertTrue(view["honesty"])

    def test_legacy_runtime_with_receipt_only_is_mentioned(self):
        receipt = self.root / "control" / "supervisor_decisions" / "t.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text("{}", encoding="utf-8")
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertEqual(view["turns"], [])
        self.assertTrue(any("receipt" in note.lower()
                            for note in view["honesty"]))

    def test_ordering_is_newest_first_and_deterministic(self):
        for index, (finished, started) in enumerate((
                ("2026-09-12T10:00:00+00:00", "2026-09-12T09:58:00+00:00"),
                ("2026-09-12T11:00:00+00:00", "2026-09-12T10:58:00+00:00"),
                ("2026-09-12T09:00:00+00:00", "2026-09-12T08:58:00+00:00"))):
            write_record(self.root, sample_record(
                turn_id=f"supervisor-turn-{index:024d}",
                finished_at=finished, started_at=started))
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        finished_order = [turn["finished_at"] for turn in view["turns"]]
        self.assertEqual(finished_order, sorted(finished_order, reverse=True))

    def test_pagination_bounds_and_totals(self):
        for index in range(7):
            write_record(self.root, sample_record(
                turn_id=f"supervisor-turn-{index:024d}",
                finished_at=f"2026-09-12T10:0{index}:00+00:00"))
        page_one = wsup.build_turns_document(
            self.root, limit=3, offset=0, generated_at="now")
        page_two = wsup.build_turns_document(
            self.root, limit=3, offset=3, generated_at="now")
        page_beyond = wsup.build_turns_document(
            self.root, limit=3, offset=9, generated_at="now")
        self.assertEqual([t["turn_id"] for t in page_one["turns"]],
                         [f"supervisor-turn-{i:024d}" for i in (6, 5, 4)])
        self.assertEqual(len(page_two["turns"]), 3)
        self.assertEqual(page_beyond["turns"], [])
        self.assertEqual(page_one["totals"]["total_records"], 7)
        self.assertEqual(page_one["totals"]["offset"], 0)

    def test_unusable_records_are_counted_and_listed_not_silently_skipped(self):
        write_record(self.root, sample_record(
            turn_id="supervisor-turn-" + "0" * 24,
            finished_at="2026-09-12T10:05:00+00:00"))
        bad = self.root / "control" / "supervisor_turns" / "broken.json"
        bad.write_text("{not json", encoding="utf-8")
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        self.assertEqual(view["totals"]["total_records"], 1)
        self.assertEqual(view["totals"]["total_unusable"], 1)
        self.assertTrue(view["unusable"])

    def test_query_parser_bounds(self):
        base = {"limit": wsup.parse_turns_query("")["limit"]}
        self.assertEqual(base["limit"], wsup.DEFAULT_PAGE_SIZE)
        self.assertEqual(
            wsup.parse_turns_query(urllib.parse.urlencode(
                {"limit": "5", "offset": "7"})),
            {"limit": 5, "offset": 7})
        for query in ("limit=0", "limit=101", "limit=-1", "limit=abc",
                      "offset=-2", "offset=xyz", "limit=5&extra=1"):
            with self.subTest(query=query):
                with self.assertRaises(wsup.TurnRecordError):
                    wsup.parse_turns_query(query)

    def test_list_projection_is_bounded_and_honest(self):
        record = sample_record()
        write_record(self.root, record)
        view = wsup.build_turns_document(
            self.root, limit=20, offset=0, generated_at="now")
        turn = view["turns"][0]
        for key in ("turn_id", "project_id", "started_at", "finished_at",
                    "duration_seconds", "supervisor_config", "usage",
                    "decision", "dispatch_linkage", "intervention_ids",
                    "outcome", "receipt"):
            self.assertIn(key, turn)
        self.assertNotIn("context_manifest", turn,
                         "the bounded list must not carry the full detail")
        self.assertEqual(turn["usage"]["reported"], False)
        self.assertEqual(turn["decision"]["summary"],
                         "stage complete; dispatch next")
        self.assertEqual(turn["dispatch_linkage"]["MESSAGE_ID"], 700126)

    def test_detail_includes_context_manifest(self):
        record = sample_record()
        write_record(self.root, record)
        document = wsup.build_turn_detail_document(
            self.root, record["turn_id"])
        self.assertEqual(document["turn"]["context_manifest"],
                         record["context_manifest"])
        self.assertEqual(document["turn"]["invocation"],
                         record["invocation"])

    def test_detail_refuses_missing_hostile_and_unusable(self):
        record = sample_record()
        write_record(self.root, record)
        with self.assertRaises(wsup.TurnRecordError) as missing:
            wsup.build_turn_detail_document(self.root,
                                            "supervisor-turn-" + "f" * 24)
        self.assertEqual(missing.exception.code, "TURN_NOT_FOUND")
        with self.assertRaises(wsup.TurnRecordError) as hostile:
            wsup.build_turn_detail_document(self.root, "../../TO_ZCODE")
        self.assertEqual(hostile.exception.code, "TURN_ID_INVALID")
        broken = self.root / "control" / "supervisor_turns" / "bad.json"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_text("[]", encoding="utf-8")
        with self.assertRaises(wsup.TurnRecordError) as unusable:
            wsup.build_turn_detail_document(self.root, "bad")
        self.assertEqual(unusable.exception.code, "TURN_RECORD_UNUSABLE")


class UsageSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsup-usage-")
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_only_reported_values_are_summed(self):
        write_record(self.root, sample_record(
            turn_id="supervisor-turn-" + "0" * 24,
            usage={"reported": True, "input_tokens": 1000,
                   "output_tokens": 200, "total_tokens": 1200,
                   "source": "codex_output_token_usage", "note": None},
            finished_at="2026-09-12T10:00:00+00:00"))
        write_record(self.root, sample_record(
            turn_id="supervisor-turn-" + "1" * 24,
            usage={"reported": False, "input_tokens": None,
                   "output_tokens": None, "total_tokens": None,
                   "source": None, "note": "not reported"},
            finished_at="2026-09-12T11:00:00+00:00"))
        write_record(self.root, sample_record(
            turn_id="supervisor-turn-" + "2" * 24,
            usage={"reported": True, "input_tokens": None,
                   "output_tokens": 50, "total_tokens": 300,
                   "source": "codex_output_token_usage", "note": None},
            finished_at="2026-09-12T12:00:00+00:00"))
        document = wsup.build_usage_document(self.root, generated_at="now")
        summary = document["usage"]
        self.assertEqual(summary["turns_total"], 3)
        self.assertEqual(summary["turns_with_reported_usage"], 2)
        self.assertEqual(summary["totals"]["total_tokens"], 1500)
        self.assertEqual(summary["totals"]["output_tokens"], 250)
        self.assertEqual(summary["totals"]["input_tokens"], 1000)
        self.assertEqual(summary["coverage"]["input_tokens_reported"], 1)
        self.assertEqual(summary["coverage"]["output_tokens_reported"], 2)
        self.assertEqual(summary["coverage"]["total_tokens_reported"], 2)
        self.assertEqual(summary["average_total_per_reported_turn"], 750.0)
        self.assertEqual(summary["highest_total_turn"]["turn_id"],
                         "supervisor-turn-" + "0" * 24)
        self.assertEqual(summary["highest_total_turn"]["total_tokens"], 1200)
        self.assertEqual(summary["zcode_usage"]["reported"], False)

    def test_no_reported_usage_is_stated_plainly(self):
        write_record(self.root, sample_record(
            turn_id="supervisor-turn-" + "0" * 24))
        document = wsup.build_usage_document(self.root, generated_at="now")
        summary = document["usage"]
        self.assertEqual(summary["turns_with_reported_usage"], 0)
        self.assertIsNone(summary["totals"]["total_tokens"])
        self.assertIsNone(summary["average_total_per_reported_turn"])
        self.assertTrue(any("not reported" in note.lower()
                            for note in document["honesty"]))

    def test_recent_usage_is_newest_first_and_bounded(self):
        for index in range(7):
            write_record(self.root, sample_record(
                turn_id=f"supervisor-turn-{index:024d}",
                usage={"reported": True, "input_tokens": index,
                       "output_tokens": index, "total_tokens": index * 10,
                       "source": "s", "note": None},
                finished_at=f"2026-09-12T1{index}:00:00+00:00"))
        document = wsup.build_usage_document(self.root, generated_at="now")
        recent = document["usage"]["recent_reported_turns"]
        self.assertEqual(len(recent), wsup.MAX_RECENT_REPORTED_TURNS)
        self.assertEqual(recent[0]["turn_id"], f"supervisor-turn-{6:024d}")


class ConfigDocumentTests(unittest.TestCase):
    PROJECT = "config-view-fixture"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wsup-config-")
        self.root = Path(self.temp.name)
        # The real Runtime configuration contract requires an active project.
        (self.root / "control").mkdir(parents=True, exist_ok=True)
        (self.root / "control" / "ACTIVE_PROJECT.json").write_text(
            json.dumps({"schema_version": 1, "project_id": self.PROJECT,
                        "project_root": f"projects/{self.PROJECT}"}),
            encoding="utf-8")
        (self.root / "projects" / self.PROJECT).mkdir(parents=True,
                                                      exist_ok=True)
        (self.root / "projects" / self.PROJECT / "project_state.json").write_text(
            json.dumps({"schema_version": 1, "project_id": self.PROJECT,
                        "status": "SUPERVISOR_TURN", "current_task": None,
                        "next_message_id": 1, "decision_history": []}),
            encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_absent_config_is_fixed_policy_with_honesty(self):
        result = wsup.read_config_document(self.root)
        self.assertEqual(result["state"], "absent")
        view = wsup.config_view(
            result, capability=web_console_setup.capability_report(None),
            draft_supervisor=None)
        self.assertFalse(view["active"]["configured"])
        self.assertIsNone(view["active"]["model"])
        self.assertTrue(any("fixed" in note.lower()
                            for note in view["active"]["notes"]))
        self.assertIsNone(view["pending"])

    def test_real_runtime_queue_then_view(self):
        # Build the configuration through the real Runtime contract.
        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        result = wsup.read_config_document(self.root)
        self.assertEqual(result["state"], "ok")
        view = wsup.config_view(
            result, capability=web_console_setup.capability_report(None),
            draft_supervisor={"model": "gpt-6", "reasoning_effort": "HIGHEST",
                              "explanation_mode": "COMPACT"})
        self.assertFalse(view["active"]["configured"])
        self.assertEqual(view["pending"]["model"], "gpt-6")
        self.assertEqual(view["pending"]["reasoning_effort"], "HIGH")
        self.assertEqual(view["pending"]["applies"],
                         "next eligible Supervisor turn")
        self.assertEqual(view["draft"]["model"], "gpt-6")
        self.assertEqual(view["draft"]["reasoning_effort"], "HIGHEST")
        self.assertFalse(view["capability"]["reported"])
        self.assertEqual(view["supported_reasoning_efforts"],
                         list(wsup.SUPPORTED_REASONING_EFFORTS))
        self.assertNotIn("HIGHEST", view["supported_reasoning_efforts"])

    def test_active_config_after_consumption(self):
        sc.queue_supervisor_config(
            self.root, {"model": "gpt-6", "reasoning_effort": "LOW"})
        turn = sc.begin_supervisor_turn(self.root, "config-view-fixture")
        result = wsup.read_config_document(self.root)
        view = wsup.config_view(
            result, capability=web_console_setup.capability_report(None),
            draft_supervisor=None)
        self.assertTrue(view["active"]["configured"])
        self.assertEqual(view["active"]["model"], "gpt-6")
        self.assertEqual(view["active"]["reasoning_effort"], "LOW")
        self.assertIsNone(view["pending"])
        sc.finish_supervisor_turn(self.root, turn, processed=False)

    def test_unusable_config_fails_closed_with_honesty(self):
        path = self.root / "control" / "supervisor_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{broken", encoding="utf-8")
        result = wsup.read_config_document(self.root)
        self.assertEqual(result["state"], "unusable")
        view = wsup.config_view(
            result, capability=web_console_setup.capability_report(None),
            draft_supervisor=None)
        self.assertFalse(view["active"]["configured"])
        self.assertTrue(view["honesty"]["notes"])

    def test_config_with_unknown_key_is_unusable(self):
        path = self.root / "control" / "supervisor_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "schema_version": 1, "PROJECT_ID": None, "active": None,
            "pending": None, "updated_at": None, "extra": True}),
            encoding="utf-8")
        result = wsup.read_config_document(self.root)
        self.assertEqual(result["state"], "unusable")

    def test_request_validation_matrix(self):
        good = wsup.validate_config_change_request(
            {"model": " gpt-6 ", "reasoning_effort": "HIGH"})
        self.assertEqual(good, {"model": "gpt-6", "reasoning_effort": "HIGH"})
        bad_payloads = [
            {"model": "gpt-6"},
            {"reasoning_effort": "HIGH"},
            {"model": "gpt-6", "reasoning_effort": "HIGH", "x": 1},
            {"model": "", "reasoning_effort": "HIGH"},
            {"model": "x" * 81, "reasoning_effort": "HIGH"},
            {"model": "gpt-6", "reasoning_effort": "ULTRA"},
            {"model": "gpt-6", "reasoning_effort": 5},
        ]
        for payload in bad_payloads:
            with self.subTest(payload=payload):
                with self.assertRaises(
                        web_console_control.ControlRequestError):
                    wsup.validate_config_change_request(payload)

    def test_supported_efforts_match_runtime_contract(self):
        self.assertEqual(
            set(wsup.SUPPORTED_REASONING_EFFORTS),
            set(sc.SUPERVISOR_CONFIG_EFFORTS),
            "the Console vocabulary must never diverge from the Runtime "
            "configuration contract")


if __name__ == "__main__":
    unittest.main()
