"""P9 pure-layer tests for Console-owned settings and operator notes.

Covers the versioned settings schema (global defaults and per-Runtime
overrides), deterministic precedence, range/unknown-field/corruption
validation, the UI-only operator note contract, and the fail-closed storage
behavior. The storage layer is the same atomic JSON pattern as the Registry;
a corrupted store is never reset silently.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_settings as wset


RUNTIME_ID = "0123456789abcdef"


class DefaultsShapeTests(unittest.TestCase):
    def test_defaults_cover_the_specified_setting_families(self):
        for key in ("supervisor_model", "supervisor_reasoning_effort",
                    "decision_summary_mode", "timeline_page_size",
                    "alert_thresholds", "notifications"):
            self.assertIn(key, wset.DEFAULT_SETTINGS, key)

    def test_default_notification_policy_follows_the_spec(self):
        # Optional informational events start off; must-attention starts on.
        notifications = wset.DEFAULT_SETTINGS["notifications"]
        for key in ("complete", "safe_pause", "zcode_pickup",
                    "high_token_turn"):
            self.assertFalse(notifications[key], key)
        for key in ("human_review", "orchestrator_error", "health_severe",
                    "expiry_risk"):
            self.assertTrue(notifications[key], key)

    def test_default_thresholds_are_inside_their_bounds(self):
        for name, value in wset.DEFAULT_SETTINGS["alert_thresholds"].items():
            low, high = wset.ALERT_THRESHOLD_BOUNDS[name]
            self.assertGreaterEqual(value, low, name)
            self.assertLessEqual(value, high, name)

    def test_default_supervisor_values_inherit_the_runtime_policy(self):
        self.assertEqual(wset.DEFAULT_SETTINGS["supervisor_model"], "")
        self.assertEqual(
            wset.DEFAULT_SETTINGS["supervisor_reasoning_effort"], "")

    def test_threshold_bounds_are_documented_for_every_threshold(self):
        self.assertEqual(set(wset.ALERT_THRESHOLD_BOUNDS),
                         set(wset.DEFAULT_SETTINGS["alert_thresholds"]))


class SettingsValidationTests(unittest.TestCase):
    def test_full_payload_is_accepted(self):
        payload = {
            "supervisor_model": " gpt-5.6-sol ",
            "supervisor_reasoning_effort": "HIGH",
            "decision_summary_mode": "full",
            "timeline_page_size": 50,
            "alert_thresholds": {"zcode_pickup_minutes": 45},
            "notifications": {"complete": True},
        }
        values = wset.validate_settings_payload(payload, partial=True)
        self.assertEqual(values["supervisor_model"], "gpt-5.6-sol")
        self.assertEqual(values["supervisor_reasoning_effort"], "HIGH")
        self.assertEqual(values["timeline_page_size"], 50)
        self.assertEqual(values["alert_thresholds"],
                         {"zcode_pickup_minutes": 45})
        self.assertEqual(values["notifications"], {"complete": True})

    def test_unknown_setting_is_refused(self):
        with self.assertRaises(wset.SettingsError) as caught:
            wset.validate_settings_payload({"unknown_key": 1}, partial=True)
        self.assertEqual(caught.exception.code, "SETTINGS_UNKNOWN_FIELD")

    def test_non_string_model_is_refused_and_model_strips_whitespace(self):
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload({"supervisor_model": 5},
                                           partial=True)
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload({"supervisor_model": "a" * 81},
                                           partial=True)
        values = wset.validate_settings_payload(
            {"supervisor_model": "\tm-1\n"}, partial=True)
        self.assertEqual(values["supervisor_model"], "m-1")

    def test_model_with_control_character_is_refused(self):
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload({"supervisor_model": "m\x01x"},
                                           partial=True)

    def test_effort_must_be_empty_or_a_supported_value(self):
        for effort in ("", "LOW", "MEDIUM", "HIGH"):
            values = wset.validate_settings_payload(
                {"supervisor_reasoning_effort": effort}, partial=True)
            self.assertEqual(values["supervisor_reasoning_effort"], effort)
        for effort in ("low", "ULTRA", "HIGHEST", "EXTRA_HIGH", 7):
            with self.assertRaises(wset.SettingsError):
                wset.validate_settings_payload(
                    {"supervisor_reasoning_effort": effort}, partial=True)

    def test_decision_summary_mode_value_space(self):
        for mode in ("compact", "full"):
            wset.validate_settings_payload({"decision_summary_mode": mode},
                                           partial=True)
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload({"decision_summary_mode": "raw"},
                                           partial=True)

    def test_timeline_page_size_bounds(self):
        for size in (5, 100):
            wset.validate_settings_payload({"timeline_page_size": size},
                                           partial=True)
        for size in (4, 101, "20", 20.5, True, None):
            with self.assertRaises(wset.SettingsError):
                wset.validate_settings_payload({"timeline_page_size": size},
                                               partial=True)

    def test_threshold_unknown_name_and_range_are_refused(self):
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload(
                {"alert_thresholds": {"nope": 1}}, partial=True)
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload(
                {"alert_thresholds": {"zcode_pickup_minutes": 0}},
                partial=True)
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload(
                {"alert_thresholds": {"zcode_pickup_minutes": 10081}},
                partial=True)
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload(
                {"alert_thresholds": {"usage_outlier_factor": 1.0}},
                partial=True)
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload(
                {"alert_thresholds": {"usage_outlier_min_sample": 1}},
                partial=True)

    def test_expiry_critical_may_not_exceed_warning(self):
        with self.assertRaises(wset.SettingsError) as caught:
            wset.validate_settings_payload(
                {"alert_thresholds": {"expiry_warning_minutes": 10,
                                      "expiry_critical_minutes": 30}},
                partial=True)
        self.assertEqual(caught.exception.code, "SETTINGS_VALUE_INVALID")
        merged = wset.validate_settings_payload(
            {"alert_thresholds": {"expiry_critical_minutes": 90}},
            partial=True)
        resolved = wset.resolve_settings(merged)
        with self.assertRaises(wset.SettingsError):
            wset.validate_alert_thresholds(resolved["alert_thresholds"])

    def test_notification_values_must_be_booleans(self):
        with self.assertRaises(wset.SettingsError):
            wset.validate_settings_payload({"notifications": {"complete": 1}},
                                           partial=True)
        values = wset.validate_settings_payload(
            {"notifications": {"human_review": False}}, partial=True)
        self.assertEqual(values["notifications"], {"human_review": False})

    def test_non_object_payload_is_refused(self):
        for payload in (None, [], "x", 5):
            with self.assertRaises(wset.SettingsError):
                wset.validate_settings_payload(payload, partial=True)

    def test_resolve_settings_merges_partial_over_defaults(self):
        resolved = wset.resolve_settings({"timeline_page_size": 33})
        self.assertEqual(resolved["timeline_page_size"], 33)
        self.assertEqual(resolved["decision_summary_mode"], "compact")
        self.assertEqual(resolved["alert_thresholds"]["turn_minutes"], 30)
        self.assertEqual(resolved["notifications"]["human_review"], True)


class OverrideAndPrecedenceTests(unittest.TestCase):
    def test_override_payload_shape_is_enforced(self):
        with self.assertRaises(wset.SettingsError) as caught:
            wset.validate_override_payload({"supervisor_model": "m"})
        self.assertEqual(caught.exception.code, "SETTINGS_INVALID_PAYLOAD")
        values = wset.validate_override_payload(
            {"overrides": {"timeline_page_size": 44,
                           "supervisor_model": None}})
        self.assertEqual(values["timeline_page_size"], 44)
        self.assertIsNone(values["supervisor_model"])

    def test_null_clears_and_unknown_keys_refuse_inside_overrides(self):
        with self.assertRaises(wset.SettingsError):
            wset.validate_override_payload({"overrides": {"nope": None}})
        with self.assertRaises(wset.SettingsError):
            wset.validate_override_payload({"overrides": {"notifications":
                                                          {"nope": True}}})

    def test_precedence_override_wins_else_global(self):
        global_settings = wset.resolve_settings({})
        overrides = {"timeline_page_size": 60}
        effective = wset.effective_settings(global_settings, overrides)
        self.assertEqual(effective["timeline_page_size"],
                         {"value": 60, "source": "override"})
        self.assertEqual(effective["decision_summary_mode"],
                         {"value": "compact", "source": "global-default"})

    def test_nested_thresholds_and_notifications_precedence(self):
        global_settings = wset.resolve_settings(
            {"alert_thresholds": {"turn_minutes": 45},
             "notifications": {"complete": True}})
        overrides = {"alert_thresholds": {"turn_minutes": 90,
                                          "zcode_pickup_minutes": None},
                     "notifications": {"human_review": False}}
        effective = wset.effective_settings(global_settings, overrides)
        self.assertEqual(effective["alert_thresholds"]["turn_minutes"],
                         {"value": 90, "source": "override"})
        self.assertEqual(effective["alert_thresholds"]["zcode_pickup_minutes"],
                         {"value": 20, "source": "global-default"})
        self.assertEqual(effective["notifications"]["complete"],
                         {"value": True, "source": "global-default"})
        self.assertEqual(effective["notifications"]["human_review"],
                         {"value": False, "source": "override"})

    def test_null_override_clears_back_to_global(self):
        global_settings = wset.resolve_settings({})
        effective = wset.effective_settings(global_settings,
                                            {"timeline_page_size": None})
        self.assertEqual(effective["timeline_page_size"],
                         {"value": 20, "source": "global-default"})

    def test_effective_view_is_deterministic(self):
        global_settings = wset.resolve_settings({})
        overrides = {"supervisor_model": "m-1"}
        one = wset.effective_settings(global_settings, overrides)
        two = wset.effective_settings(global_settings, overrides)
        self.assertEqual(one, two)


class OperatorNoteTests(unittest.TestCase):
    def test_note_is_accepted_verbatim_with_newlines(self):
        text = "first line\nsecond\tline"
        self.assertEqual(wset.validate_operator_note(text), text)

    def test_note_must_be_a_string(self):
        for value in (None, 5, [], {}):
            with self.assertRaises(wset.SettingsError):
                wset.validate_operator_note(value)

    def test_note_length_bound(self):
        wset.validate_operator_note("a" * wset.OPERATOR_NOTE_MAX_CHARS)
        with self.assertRaises(wset.SettingsError):
            wset.validate_operator_note(
                "a" * (wset.OPERATOR_NOTE_MAX_CHARS + 1))

    def test_note_control_characters_other_than_newline_tab_are_refused(self):
        for bad in ("a\x00b", "a\x1bb", "a\x7fb"):
            with self.assertRaises(wset.SettingsError):
                wset.validate_operator_note(bad)

    def test_carriage_returns_are_normalized(self):
        self.assertEqual(wset.validate_operator_note("a\r\nb"), "a\nb")

    def test_unicode_note_round_trips(self):
        text = "备注：请关注 ← runtime → ✅"
        self.assertEqual(wset.validate_operator_note(text), text)


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name)
        self.store = wset.SettingsStore(self.data_dir)

    def test_missing_documents_read_as_defaults(self):
        doc = self.store.read_global()
        self.assertEqual(doc["settings"], wset.resolve_settings({}))
        self.assertIsNone(doc["updated_at"])
        runtime = self.store.read_runtime(RUNTIME_ID)
        self.assertEqual(runtime["overrides"], {})
        self.assertIsNone(runtime["operator_note"])

    def test_global_round_trip(self):
        self.store.write_global(
            settings=wset.resolve_settings({"timeline_page_size": 77}),
            updated_at="2026-09-12T15:00:00+00:00")
        doc = self.store.read_global()
        self.assertEqual(doc["settings"]["timeline_page_size"], 77)
        self.assertEqual(doc["updated_at"], "2026-09-12T15:00:00+00:00")

    def test_runtime_overrides_and_note_round_trip(self):
        self.store.write_overrides(
            RUNTIME_ID, overrides={"timeline_page_size": 44},
            updated_at="2026-09-12T15:00:01+00:00")
        self.store.write_note(
            RUNTIME_ID,
            note={"text": "watch the deadline", "updated_at":
                  "2026-09-12T15:00:02+00:00"},
            updated_at="2026-09-12T15:00:02+00:00")
        doc = self.store.read_runtime(RUNTIME_ID)
        self.assertEqual(doc["overrides"], {"timeline_page_size": 44})
        self.assertEqual(doc["operator_note"]["text"], "watch the deadline")

    def test_invalid_runtime_id_is_refused_before_any_path_is_built(self):
        for bad in ("", "XYZ", "../escape", "0123456789abcdeg",
                    "0123456789abcdef/../../x", None, 5,
                    "0123456789ABCDEF"):
            with self.assertRaises(wset.SettingsError):
                self.store.read_runtime(bad)

    def test_corrupt_global_store_fails_closed_and_is_never_reset(self):
        self.store.write_global(
            settings=wset.resolve_settings({}),
            updated_at="2026-09-12T15:00:00+00:00")
        path = self.store.global_path()
        path.write_bytes(b"{not json at all")
        raw = path.read_bytes()
        with self.assertRaises(wset.SettingsError) as caught:
            self.store.read_global()
        self.assertEqual(caught.exception.code, "SETTINGS_CORRUPT")
        self.assertEqual(path.read_bytes(), raw)

    def test_out_of_range_stored_value_is_corruption_not_a_crash(self):
        self.store.write_global(
            settings=wset.resolve_settings({}),
            updated_at="2026-09-12T15:00:00+00:00")
        path = self.store.global_path()
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["settings"]["timeline_page_size"] = 100000
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(wset.SettingsError) as caught:
            self.store.read_global()
        self.assertEqual(caught.exception.code, "SETTINGS_CORRUPT")

    def test_unknown_stored_key_is_corruption(self):
        self.store.write_global(
            settings=wset.resolve_settings({}),
            updated_at="2026-09-12T15:00:00+00:00")
        path = self.store.global_path()
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["settings"]["mystery"] = 1
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(wset.SettingsError):
            self.store.read_global()

    def test_corrupt_runtime_store_fails_closed_and_is_never_reset(self):
        self.store.write_overrides(RUNTIME_ID, overrides={},
                                   updated_at="2026-09-12T15:00:00+00:00")
        path = self.store.runtime_path(RUNTIME_ID)
        path.write_bytes(b"[[[")
        raw = path.read_bytes()
        with self.assertRaises(wset.SettingsError) as caught:
            self.store.read_runtime(RUNTIME_ID)
        self.assertEqual(caught.exception.code, "SETTINGS_CORRUPT")
        self.assertEqual(path.read_bytes(), raw)

    def test_runtime_store_with_wrong_runtime_id_is_corruption(self):
        self.store.write_overrides(RUNTIME_ID, overrides={},
                                   updated_at="2026-09-12T15:00:00+00:00")
        path = self.store.runtime_path(RUNTIME_ID)
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["runtime_id"] = "ffffffffffffffff"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(wset.SettingsError):
            self.store.read_runtime(RUNTIME_ID)

    def test_write_is_atomic_and_survives_as_valid_json(self):
        self.store.write_overrides(RUNTIME_ID,
                                   overrides={"decision_summary_mode": "full"},
                                   updated_at="2026-09-12T15:00:00+00:00")
        path = self.store.runtime_path(RUNTIME_ID)
        leftovers = [p.name for p in path.parent.iterdir()
                     if p.name != path.name]
        self.assertEqual(leftovers, [])
        doc = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(doc["overrides"]["decision_summary_mode"], "full")


if __name__ == "__main__":
    unittest.main()
