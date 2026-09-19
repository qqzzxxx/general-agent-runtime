"""P5 Human Control pure-logic tests.

Covers the fail-closed request schemas, the pending-controls projection over
authoritative Runtime facts, the stop-challenge evaluation, and the
HUMAN_REVIEW presentation block. These tests pin `web_console_control`, the
pure layer that the HTTP surface composes; they never touch the filesystem,
the clock, or subprocesses.
"""
from __future__ import annotations

import unittest

import web_console_control as wcc


class ValidatePauseRequestTests(unittest.TestCase):
    def test_safe_pause_is_accepted(self):
        self.assertEqual(wcc.validate_pause_request({"mode": "SAFE"}),
                         {"mode": "SAFE"})

    def test_interrupt_pause_is_accepted(self):
        self.assertEqual(
            wcc.validate_pause_request({"mode": "INTERRUPT_CURRENT"}),
            {"mode": "INTERRUPT_CURRENT"})

    def test_exact_keys_are_required(self):
        for payload in ({}, {"mode": "SAFE", "reason": "x"}, {"pause": True}):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_pause_request(payload)

    def test_mode_must_be_an_exact_known_value(self):
        for mode in ("safe", "PAUSE", "RESUME", "SAFE ", " STOP", 5, None, True):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_pause_request({"mode": mode})

    def test_payload_must_be_an_object(self):
        for payload in (None, [], "SAFE", 7):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_pause_request(payload)


class ValidateResumeRequestTests(unittest.TestCase):
    def test_empty_object_is_accepted(self):
        self.assertEqual(wcc.validate_resume_request({}), {})

    def test_any_key_is_refused(self):
        for payload in ({"force": True}, {"mode": "SAFE"}, None, []):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_resume_request(payload)


class ValidateInterventionRequestTests(unittest.TestCase):
    def valid_payload(self, **overrides):
        payload = {"mode": "STEER", "comment": "keep the patch bounded",
                   "target_message_id": None, "interrupt_current": False}
        payload.update(overrides)
        return payload

    def test_full_valid_request_is_normalized(self):
        self.assertEqual(
            wcc.validate_intervention_request(self.valid_payload()),
            {"mode": "STEER", "comment": "keep the patch bounded",
             "target_message_id": None, "interrupt_current": False})

    def test_audit_mode_is_accepted(self):
        result = wcc.validate_intervention_request(
            self.valid_payload(mode="AUDIT"))
        self.assertEqual(result["mode"], "AUDIT")

    def test_mode_is_restricted_to_the_runtime_vocabulary(self):
        for mode in ("steer", "audit", "STOP", "DEEP_REVIEW", "", 3, None):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_intervention_request(
                    self.valid_payload(mode=mode))

    def test_exact_keys_are_required(self):
        for overrides in ({"extra": 1},):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_intervention_request(
                    self.valid_payload(**overrides))
        incomplete = {"mode": "STEER", "comment": "x",
                      "target_message_id": None}
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_intervention_request(incomplete)

    def test_comment_bounds_and_control_characters(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_intervention_request(self.valid_payload(comment="   "))
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_intervention_request(self.valid_payload(comment=""))
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_intervention_request(
                self.valid_payload(comment="x" * 4001))
        ok = wcc.validate_intervention_request(
            self.valid_payload(comment="x" * 4000))
        self.assertEqual(len(ok["comment"]), 4000)
        multiline = wcc.validate_intervention_request(
            self.valid_payload(comment="line one\nline two\ttabbed"))
        self.assertIn("\n", multiline["comment"])

    def test_comment_may_start_with_a_dash(self):
        # The HTTP layer passes the comment with a --text=<value> token, so a
        # leading dash must not be a schema-level refusal.
        result = wcc.validate_intervention_request(
            self.valid_payload(comment="--force something"))
        self.assertEqual(result["comment"], "--force something")

    def test_null_byte_and_control_characters_are_refused(self):
        for comment in ("bad\x00null", "bad\x07bell", "bad\x1besc"):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_intervention_request(
                    self.valid_payload(comment=comment))

    def test_unicode_comment_is_accepted(self):
        comment = "请保持补丁最小 ✅ café"
        result = wcc.validate_intervention_request(
            self.valid_payload(comment=comment))
        self.assertEqual(result["comment"], comment)

    def test_target_message_id_bounds(self):
        for target in (None, 0, 1, 700501, 999999999):
            result = wcc.validate_intervention_request(
                self.valid_payload(target_message_id=target))
            self.assertEqual(result["target_message_id"], target)
        for target in (-1, 10 ** 10, "700501", 7.0, True, False):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_intervention_request(
                    self.valid_payload(target_message_id=target))

    def test_interrupt_current_must_be_boolean(self):
        for value in (True, False):
            result = wcc.validate_intervention_request(
                self.valid_payload(interrupt_current=value))
            self.assertIs(result["interrupt_current"], value)
        for value in ("true", 1, 0, None):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_intervention_request(
                    self.valid_payload(interrupt_current=value))

    def test_payload_must_be_an_object(self):
        for payload in (None, [], "x", 5):
            with self.assertRaises(wcc.ControlRequestError):
                wcc.validate_intervention_request(payload)


class ValidateStopConfirmRequestTests(unittest.TestCase):
    def valid_payload(self, **overrides):
        payload = {"challenge_id": "a" * 32,
                   "confirmation_token": "b" * 32,
                   "project_id": "proj-x"}
        payload.update(overrides)
        return payload

    def test_valid_request_is_accepted(self):
        self.assertEqual(
            wcc.validate_stop_confirm_request(self.valid_payload()),
            self.valid_payload())

    def test_exact_keys_are_required(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(
                {"challenge_id": "a" * 32, "confirmation_token": "b" * 32})
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(
                {**self.valid_payload(), "root": "C:/elsewhere"})

    def test_identifiers_must_match_the_issued_shape(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(
                self.valid_payload(challenge_id="A" * 32))
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(
                self.valid_payload(confirmation_token="b" * 31))
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(
                self.valid_payload(project_id="proj\nx"))
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(
                self.valid_payload(project_id="x" * 121))

    def test_payload_must_be_an_object(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_stop_confirm_request(None)


class ValidateHumanDecisionApplyRequestTests(unittest.TestCase):
    def valid_payload(self, **overrides):
        payload = {"receipt_id": "human-decision-" + "a" * 32,
                   "receipt_sha256": "c" * 64}
        payload.update(overrides)
        return payload

    def test_valid_request_is_accepted(self):
        self.assertEqual(
            wcc.validate_human_decision_apply_request(self.valid_payload()),
            self.valid_payload())

    def test_receipt_id_shape_is_enforced(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_human_decision_apply_request(
                self.valid_payload(receipt_id="human-decision-uppercase"))
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_human_decision_apply_request(
                self.valid_payload(receipt_id="../../escape"))

    def test_receipt_hash_must_be_hex64(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_human_decision_apply_request(
                self.valid_payload(receipt_sha256="short"))

    def test_exact_keys_are_required(self):
        with self.assertRaises(wcc.ControlRequestError):
            wcc.validate_human_decision_apply_request({"receipt_id": "x"})


class PreparedDecisionSummaryTests(unittest.TestCase):
    def stored_receipt(self, **overrides):
        receipt = {
            "schema_version": 1,
            "receipt_id": "human-decision-" + "a" * 32,
            "project_id": "proj-x",
            "previous_status": "HUMAN_REVIEW",
            "human_decision": {"decision_content": "continue",
                               "constraints_verbatim": ["keep the baseline"]},
            "submitted_at": "2026-09-15T14:03:26+00:00",
            "previous_project_state_sha256": "b" * 64,
            "receipt_sha256": "c" * 64,
        }
        receipt.update(overrides)
        return receipt

    def test_valid_receipt_is_summarized_with_its_own_fields(self):
        self.assertEqual(
            wcc.prepared_decision_summary(self.stored_receipt()),
            {"receipt_id": "human-decision-" + "a" * 32,
             "receipt_sha256": "c" * 64,
             "submitted_at": "2026-09-15T14:03:26+00:00"})

    def test_summary_never_includes_decision_content(self):
        summary = wcc.prepared_decision_summary(self.stored_receipt())
        self.assertNotIn("human_decision", summary)
        self.assertNotIn("previous_project_state_sha256", summary)

    def test_missing_submitted_at_is_tolerated_as_none(self):
        summary = wcc.prepared_decision_summary(
            self.stored_receipt(submitted_at=None))
        self.assertIsNone(summary["submitted_at"])

    def test_malformed_records_are_rejected(self):
        bad_id = self.stored_receipt(
            receipt_id="human-decision-UPPER" + "a" * 23)
        bad_hash = self.stored_receipt(receipt_sha256="c" * 63)
        for stored in (None, [], "receipt", {}, {"receipt_id": "x"},
                       bad_id, bad_hash):
            self.assertIsNone(wcc.prepared_decision_summary(stored),
                              repr(stored))


def status_doc(**overrides):
    doc = {
        "schema_version": 1, "PROJECT_ID": "proj-x",
        "runtime_status": "RUNNING", "project_status": "WAITING_EXECUTOR",
        "pause": {"status": "RUNNING", "requested_at": None, "mode": None,
                  "resumed_at": None},
        "active_task": None, "last_authorized_dispatch": None,
        "active_task_claimed": False, "active_task_claim_recorded": False,
        "active_task_completion_status": None, "active_task_retired": False,
        "pending_interventions": 0, "last_consumed_message_id": None,
        "human_review": False, "stop": False,
    }
    doc.update(overrides)
    return doc


def intervention_record(**overrides):
    record = {
        "schema_version": 1, "intervention_id": "intervention-1",
        "PROJECT_ID": "proj-x", "mode": "STEER", "target_message_id": None,
        "interrupt_current": False,
        "submitted_at": "2026-09-12T01:00:00+00:00",
        "instruction_sha256": "d" * 64,
        "instruction_file": "handoff/supervisor_interventions/x/instruction.txt",
        "status": "PENDING", "integrity": "OK",
        "instruction_text": "keep going",
    }
    record.update(overrides)
    return record


class ProjectPendingControlsTests(unittest.TestCase):
    def test_running_runtime_has_no_outstanding_pause(self):
        doc = wcc.project_pending_controls(status_doc(), [])
        self.assertEqual(doc["schema_version"], 1)
        self.assertEqual(doc["pause"]["present"], False)
        self.assertEqual(doc["pause"]["state"], "NONE")
        self.assertEqual(doc["stop"]["applied"], False)
        self.assertEqual(doc["interventions"]["available"], True)
        self.assertEqual(doc["interventions"]["items"], [])
        self.assertEqual(doc["counts"],
                         {"pending": 0, "consumed": 0, "superseded": 0,
                          "failed": 0, "needs_recovery": 0, "other": 0})
        self.assertEqual(doc["honesty"]["notes"], [])

    def test_pause_request_is_pending(self):
        pause = {"status": "PENDING_AFTER_CURRENT_STAGE",
                 "requested_at": "2026-09-12T01:00:00+00:00",
                 "mode": "SAFE", "resumed_at": None,
                 "disposition": "PAUSE_PENDING_AFTER_CURRENT_STAGE"}
        doc = wcc.project_pending_controls(status_doc(pause=pause), [])
        self.assertEqual(doc["pause"]["present"], True)
        self.assertEqual(doc["pause"]["state"], "PENDING")
        self.assertEqual(doc["pause"]["mode"], "SAFE")
        self.assertEqual(doc["pause"]["disposition"],
                         "PAUSE_PENDING_AFTER_CURRENT_STAGE")
        self.assertFalse(doc["pause"]["superseded_by_completion"])

    def test_paused_is_applied(self):
        pause = {"status": "PAUSED",
                 "requested_at": "2026-09-12T01:00:00+00:00",
                 "mode": "SAFE", "resumed_at": None,
                 "paused_at": "2026-09-12T01:05:00+00:00",
                 "disposition": "PAUSED_UNCLAIMED_RETIRED"}
        doc = wcc.project_pending_controls(status_doc(pause=pause), [])
        self.assertEqual(doc["pause"]["state"], "APPLIED")
        self.assertEqual(doc["pause"]["paused_at"],
                         "2026-09-12T01:05:00+00:00")

    def test_parked_dispatch_is_surfaced_as_waiting_work(self):
        # QUOTA-PAUSE-PARK-V1: parked work is paused logical work, so the
        # controls document must expose it for the "waiting, not failed" note.
        pause = {"status": "PAUSED",
                 "requested_at": "2026-09-16T01:00:00+00:00",
                 "mode": "SAFE", "resumed_at": None,
                 "paused_at": "2026-09-16T01:05:00+00:00",
                 "disposition": "PAUSED_UNCLAIMED_PARKED",
                 "parked_dispatch": {
                     "MESSAGE_ID": 700120, "TASK_ID": "T-700120",
                     "STAGE_ID": "S-700120", "ATTEMPT": 1, "NONCE": "n-1",
                     "PARKED_AT": "2026-09-16T01:05:00+00:00",
                     "EXPIRES_AT": "2026-09-16T03:00:00+00:00",
                     "QUARANTINE": "handoff/quarantine/to-zcode-700120.md",
                     "CONVERTED_AT": "2026-09-16T02:00:00+00:00",
                     "CONVERTED_REASON": "PARKED_STAGE_RESUME"}}
        doc = wcc.project_pending_controls(status_doc(pause=pause), [])
        parked = doc["pause"]["parked_dispatch"]
        self.assertEqual(parked["message_id"], 700120)
        self.assertEqual(parked["task_id"], "T-700120")
        self.assertEqual(parked["parked_at"], "2026-09-16T01:05:00+00:00")
        self.assertEqual(parked["expires_at"], "2026-09-16T03:00:00+00:00")
        self.assertEqual(parked["converted_at"], "2026-09-16T02:00:00+00:00")

    def test_malformed_parked_dispatch_is_not_surfaced(self):
        for parked in ("garbage", {"TASK_ID": "no-message-id"}, 7):
            with self.subTest(parked=parked):
                pause = {"status": "PAUSED", "mode": "SAFE",
                         "requested_at": None, "resumed_at": None,
                         "disposition": "PAUSED_UNCLAIMED_PARKED",
                         "parked_dispatch": parked}
                doc = wcc.project_pending_controls(status_doc(pause=pause), [])
                self.assertIsNone(doc["pause"]["parked_dispatch"])

    def test_completion_wins_disposition_is_surfaced_as_superseded(self):
        pause = {"status": "PENDING_AFTER_CURRENT_STAGE",
                 "requested_at": "2026-09-12T01:00:00+00:00",
                 "mode": "SAFE", "resumed_at": None,
                 "disposition": "COMPLETION_COMMITTED_WINS"}
        doc = wcc.project_pending_controls(status_doc(pause=pause), [])
        self.assertTrue(doc["pause"]["superseded_by_completion"])
        self.assertEqual(doc["counts"]["superseded"], 1)

    def test_interrupt_mode_is_relayed(self):
        pause = {"status": "PAUSED", "requested_at": None,
                 "mode": "INTERRUPT_CURRENT", "resumed_at": None,
                 "disposition": "PAUSED_CURRENT_REVOKED"}
        doc = wcc.project_pending_controls(status_doc(pause=pause), [])
        self.assertEqual(doc["pause"]["mode"], "INTERRUPT_CURRENT")

    def test_stop_fact_is_surfaced(self):
        doc = wcc.project_pending_controls(status_doc(stop=True), [])
        self.assertEqual(doc["stop"]["applied"], True)

    def test_unknown_pause_status_is_honest_unavailability(self):
        doc = wcc.project_pending_controls(
            status_doc(pause={"status": "WEIRD"}), [])
        self.assertEqual(doc["pause"]["state"], "UNAVAILABLE")
        self.assertTrue(doc["honesty"]["notes"])

    def test_malformed_pause_block_is_surfaced(self):
        doc = wcc.project_pending_controls(status_doc(pause="garbage"), [])
        self.assertEqual(doc["pause"]["present"], False)
        self.assertTrue(doc["honesty"]["notes"])

    def test_intervention_categories_are_separated(self):
        records = [
            intervention_record(intervention_id="H-1", status="PENDING"),
            intervention_record(intervention_id="H-2", status="INJECTED"),
            intervention_record(intervention_id="H-3", status="CONSUMED",
                                consumed_at="2026-09-12T02:00:00+00:00"),
            intervention_record(intervention_id="H-4",
                                status="RECOVERY_REQUIRED"),
            intervention_record(intervention_id="H-5", integrity="ERROR"),
            intervention_record(intervention_id="H-6",
                                integrity="HASH_MISMATCH"),
            intervention_record(intervention_id="H-7", status="WEIRD"),
        ]
        doc = wcc.project_pending_controls(status_doc(), records)
        categories = {item["intervention_id"]: item["category"]
                      for item in doc["interventions"]["items"]}
        self.assertEqual(categories, {
            "H-1": "pending", "H-2": "pending", "H-3": "consumed",
            "H-4": "needs_recovery", "H-5": "failed", "H-6": "failed",
            "H-7": "other"})
        self.assertEqual(doc["counts"],
                         {"pending": 2, "consumed": 1, "superseded": 0,
                          "failed": 2, "needs_recovery": 1, "other": 1})

    def test_intervention_items_are_newest_first_and_bounded(self):
        records = [intervention_record(
            intervention_id=f"H-{index:03d}",
            submitted_at=f"2026-09-12T0{index % 10}:00:00+00:00")
            for index in range(60)]
        doc = wcc.project_pending_controls(status_doc(), records)
        self.assertEqual(doc["interventions"]["total"], 60)
        self.assertEqual(doc["interventions"]["relayed"],
                         wcc.PENDING_INTERVENTION_LIMIT)
        self.assertTrue(doc["interventions"]["truncated"])
        self.assertTrue(any("truncated" in note.lower()
                            for note in doc["honesty"]["notes"]))
        submitted = [item["submitted_at"]
                     for item in doc["interventions"]["items"]]
        self.assertEqual(submitted, sorted(submitted, reverse=True))

    def test_intervention_fields_are_relayed_verbatim(self):
        record = intervention_record(target_message_id=700501,
                                     interrupt_current=True, mode="AUDIT")
        doc = wcc.project_pending_controls(status_doc(), [record])
        item = doc["interventions"]["items"][0]
        self.assertEqual(item["target_message_id"], 700501)
        self.assertIs(item["interrupt_current"], True)
        self.assertEqual(item["mode"], "AUDIT")

    def test_malformed_intervention_records_are_surfaced_not_crashing(self):
        doc = wcc.project_pending_controls(
            status_doc(), ["not-a-dict", 42, intervention_record()])
        self.assertEqual(doc["interventions"]["relayed"], 1)
        self.assertEqual(doc["honesty"]["malformed_records"], 2)
        self.assertTrue(doc["honesty"]["notes"])

    def test_unavailable_intervention_source_is_honest(self):
        doc = wcc.project_pending_controls(status_doc(), None)
        self.assertEqual(doc["interventions"]["available"], False)
        self.assertTrue(doc["honesty"]["notes"])

    def test_projection_is_deterministic(self):
        records = [intervention_record(), intervention_record(
            intervention_id="H-2", status="CONSUMED")]
        one = wcc.project_pending_controls(status_doc(), records)
        two = wcc.project_pending_controls(status_doc(), records)
        self.assertEqual(one, two)


class EvaluateStopChallengeTests(unittest.TestCase):
    def challenge(self, **overrides):
        record = {
            "challenge_id": "a" * 32,
            "token": "b" * 32,
            "runtime_id": "0123456789abcdef",
            "project_id": "proj-x",
            "project_state_sha256": "c" * 64,
            "issued_at": "2026-09-12T01:00:00+00:00",
            "expires_at": 1000.0,
            "used": False,
        }
        record.update(overrides)
        return record

    def base_kwargs(self, **overrides):
        kwargs = {
            "runtime_id": "0123456789abcdef",
            "request": {"challenge_id": "a" * 32,
                        "confirmation_token": "b" * 32,
                        "project_id": "proj-x"},
            "now": 900.0,
            "project_id": "proj-x",
            "project_state_sha256": "c" * 64,
            "stop_applied": False,
        }
        kwargs.update(overrides)
        return kwargs

    def test_valid_confirmation_passes(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs())
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_unknown_challenge_fails_closed(self):
        ok, reason = wcc.evaluate_stop_challenge(None, **self.base_kwargs())
        self.assertFalse(ok)
        self.assertEqual(reason, "CHALLENGE_UNKNOWN")

    def test_cross_runtime_challenge_fails_closed(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(),
            **self.base_kwargs(runtime_id="ffffffffffffffff"))
        self.assertFalse(ok)
        self.assertEqual(reason, "CHALLENGE_UNKNOWN")

    def test_used_challenge_is_replayed_and_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(used=True), **self.base_kwargs(now=2000.0))
        self.assertFalse(ok)
        self.assertEqual(reason, "CHALLENGE_ALREADY_USED")

    def test_expired_challenge_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(now=1000.1))
        self.assertFalse(ok)
        self.assertEqual(reason, "CHALLENGE_EXPIRED")

    def test_token_mismatch_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(
                request={"challenge_id": "a" * 32,
                         "confirmation_token": "f" * 32,
                         "project_id": "proj-x"}))
        self.assertFalse(ok)
        self.assertEqual(reason, "TOKEN_MISMATCH")

    def test_challenge_id_mismatch_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(
                request={"challenge_id": "e" * 32,
                         "confirmation_token": "b" * 32,
                         "project_id": "proj-x"}))
        self.assertFalse(ok)

    def test_project_mismatch_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(project_id="proj-other"))
        self.assertFalse(ok)
        self.assertEqual(reason, "PROJECT_MISMATCH")

    def test_request_project_mismatch_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(
                request={"challenge_id": "a" * 32,
                         "confirmation_token": "b" * 32,
                         "project_id": "proj-other"}))
        self.assertFalse(ok)
        self.assertEqual(reason, "PROJECT_MISMATCH")

    def test_already_stopped_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(stop_applied=True))
        self.assertFalse(ok)
        self.assertEqual(reason, "STOP_ALREADY_APPLIED")

    def test_changed_project_state_is_refused(self):
        ok, reason = wcc.evaluate_stop_challenge(
            self.challenge(), **self.base_kwargs(
                project_state_sha256="d" * 64))
        self.assertFalse(ok)
        self.assertEqual(reason, "STATE_CHANGED")


class HumanReviewPresentationTests(unittest.TestCase):
    def test_active_review_with_reason(self):
        status = status_doc(human_review=True, project_status="HUMAN_REVIEW",
                            active_task=None)
        block = wcc.human_review_presentation(
            status, "Reason text from the flag", truncated=False)
        self.assertTrue(block["active"])
        self.assertEqual(block["reason"]["available"], True)
        self.assertEqual(block["reason"]["text"],
                         "Reason text from the flag")
        self.assertFalse(block["reason"]["truncated"])
        self.assertEqual(block["authorized_task"]["present"], False)

    def test_reason_unavailable_is_honest(self):
        block = wcc.human_review_presentation(
            status_doc(human_review=True), None, truncated=False)
        self.assertTrue(block["active"])
        self.assertEqual(block["reason"]["available"], False)
        self.assertIsNone(block["reason"]["text"])

    def test_truncated_reason_is_labelled(self):
        block = wcc.human_review_presentation(
            status_doc(human_review=True), "partial", truncated=True)
        self.assertTrue(block["reason"]["truncated"])

    def test_authorized_task_facts_are_surfaced(self):
        status = status_doc(
            human_review=True,
            active_task={"MESSAGE_ID": 700501, "TASK_ID": "T",
                         "STAGE_ID": "S", "ATTEMPT": 1, "NONCE": "n"},
            active_task_claimed=True)
        block = wcc.human_review_presentation(status, None, truncated=False)
        self.assertEqual(block["authorized_task"]["present"], True)
        self.assertIs(block["authorized_task"]["claimed"], True)
        self.assertEqual(block["authorized_task"]["message_id"], 700501)

    def test_inactive_review(self):
        block = wcc.human_review_presentation(status_doc(), None,
                                              truncated=False)
        self.assertFalse(block["active"])
        self.assertEqual(block["reason"]["available"], False)


def applied_runtime_doc(**event_overrides):
    event = {
        "reason": wcc.HUMAN_DECISION_RESUME_REASON,
        "event": {
            "type": "HUMAN_DECISION_RESUME",
            "project_id": "proj-x",
            "receipt_id": "human-decision-" + "a" * 32,
            "receipt_sha256": "c" * 64,
            "previous_status": "HUMAN_REVIEW",
        },
        "recorded_at": "2026-09-16T00:00:00+00:00",
        "decision_attempts": 0,
        "retry_exhausted": False,
        "rearmed_by_receipt": "human-decision-" + "a" * 32,
    }
    event.update(event_overrides)
    return {"schema_version": 2, "status": "SUPERVISOR_TURN",
            "pending_supervisor_event": event}


class AutoResumeGateTests(unittest.TestCase):
    """The mechanical safety gate for the automatic resume after a Human
    Decision Apply: every input that cannot be proven must refuse."""

    def gate(self, status="default", runtime="ok", owner=None,
             owner_error=None, expected_receipt_id=None):
        if status == "default":
            status = status_doc(project_status="SUPERVISOR_TURN",
                                runtime_status="SUPERVISOR_TURN",
                                human_review=False)
        runtime_doc = None if runtime == "missing" else (
            applied_runtime_doc() if runtime == "ok" else runtime)
        return wcc.evaluate_auto_resume_gate(
            status, runtime_doc, live_owner=owner, owner_error=owner_error,
            expected_receipt_id=expected_receipt_id)

    def allowed(self, **kwargs):
        gate = self.gate(**kwargs)
        self.assertTrue(gate["allowed"], gate)
        self.assertEqual(gate["blocked"], [])
        return gate

    def test_proven_state_allows_the_start(self):
        gate = self.allowed()
        self.assertEqual(gate["receipt_id"], "human-decision-" + "a" * 32)
        self.assertEqual(gate["project_id"], "proj-x")

    def test_receipt_binding_is_enforced_on_the_apply_path(self):
        self.allowed(expected_receipt_id="human-decision-" + "a" * 32)
        gate = self.gate(
            expected_receipt_id="human-decision-" + "b" * 32)
        self.assertFalse(gate["allowed"])
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["RECEIPT_MISMATCH"])

    def test_stop_refuses(self):
        gate = self.gate(status=status_doc(project_status="SUPERVISOR_TURN",
                                           stop=True))
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["STOP_PRESENT"])

    def test_active_human_review_refuses(self):
        gate = self.gate(status=status_doc(
            project_status="SUPERVISOR_TURN", human_review=True))
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["HUMAN_REVIEW_ACTIVE"])

    def test_uncommitted_transition_refuses(self):
        gate = self.gate(status=status_doc(project_status="HUMAN_REVIEW",
                                           human_review=True))
        self.assertEqual(
            sorted(item["code"] for item in gate["blocked"]),
            ["HUMAN_REVIEW_ACTIVE", "PROJECT_NOT_SUPERVISOR_TURN"])

    def test_missing_project_identity_refuses(self):
        gate = self.gate(status=status_doc(project_status="SUPERVISOR_TURN",
                                           PROJECT_ID=None))
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["PROJECT_ID_UNAVAILABLE"])

    def test_unavailable_status_refuses_fail_closed(self):
        for broken in (None, {}, "status"):
            gate = self.gate(status=broken)
            self.assertFalse(gate["allowed"], repr(broken))
            self.assertEqual([item["code"] for item in gate["blocked"]],
                             ["STATUS_UNAVAILABLE"], repr(broken))

    def test_missing_runtime_document_refuses(self):
        gate = self.gate(runtime="missing")
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["RESUME_EVENT_UNAVAILABLE"])

    def test_missing_or_consumed_event_refuses(self):
        gate = self.gate(runtime={"schema_version": 2,
                                  "status": "SUPERVISOR_TURN"})
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["RESUME_EVENT_MISSING"])

    def test_malformed_event_refuses(self):
        cases = (
            applied_runtime_doc(reason="OTHER"),
            applied_runtime_doc(event={"type": "HUMAN_DECISION_RESUME",
                                       "receipt_id": "r"}),
            applied_runtime_doc(event={"type": "HUMAN_DECISION_RESUME",
                                       "project_id": "proj-x"}),
        )
        for broken in cases:
            gate = self.gate(runtime=broken)
            self.assertEqual([item["code"] for item in gate["blocked"]],
                             ["RESUME_EVENT_MISSING"], repr(broken))

    def test_project_mismatch_refuses(self):
        status = status_doc(project_status="SUPERVISOR_TURN",
                            PROJECT_ID="proj-other")
        gate = self.gate(status=status)
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["PROJECT_MISMATCH"])

    def test_live_owner_refuses_a_second_start(self):
        gate = self.gate(owner={"pid": 4242, "owner_id": "x",
                                "process_identity": 7})
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["LIVE_OWNER"])

    def test_unverifiable_owner_refuses_fail_closed(self):
        gate = self.gate(owner_error="scheduler ownership cannot be verified")
        self.assertEqual([item["code"] for item in gate["blocked"]],
                         ["OWNER_UNVERIFIABLE"])

    def test_all_reasons_are_reported_together(self):
        status = status_doc(project_status="WAITING_EXECUTOR", stop=True)
        gate = self.gate(status=status, runtime="missing", owner_error="x")
        self.assertEqual(
            sorted(item["code"] for item in gate["blocked"]),
            ["OWNER_UNVERIFIABLE", "PROJECT_NOT_SUPERVISOR_TURN",
             "RESUME_EVENT_UNAVAILABLE", "STOP_PRESENT"])

    def test_blocked_gate_constructor_shape(self):
        gate = wcc.auto_resume_gate_blocked(
            "START_IN_PROGRESS", "another start holds the lock")
        self.assertFalse(gate["allowed"])
        self.assertIsNone(gate["receipt_id"])
        self.assertEqual(gate["blocked"],
                         [{"code": "START_IN_PROGRESS",
                           "message": "another start holds the lock"}])

    def test_applied_resume_facts_projection(self):
        facts = wcc.applied_resume_facts(self.allowed())
        self.assertEqual(facts, {
            "schema_version": wcc.AUTO_RESUME_SCHEMA_VERSION,
            "event_pending": True,
            "receipt_id": "human-decision-" + "a" * 32,
            "project_id": "proj-x",
            "auto_resume_allowed": True,
            "owner_live": False,
            "blocked": []})
        consumed = wcc.applied_resume_facts(
            self.gate(runtime={"schema_version": 2}))
        self.assertFalse(consumed["event_pending"])
        running = wcc.applied_resume_facts(
            self.gate(owner={"pid": 1, "owner_id": "o"}))
        self.assertTrue(running["event_pending"])
        self.assertTrue(running["owner_live"])
        self.assertFalse(running["auto_resume_allowed"])
        unavailable = wcc.applied_resume_facts(None)
        self.assertFalse(unavailable["event_pending"])
        self.assertFalse(unavailable["auto_resume_allowed"])


if __name__ == "__main__":
    unittest.main()
