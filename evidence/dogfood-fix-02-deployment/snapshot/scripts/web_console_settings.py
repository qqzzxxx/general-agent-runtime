"""P9 Console-owned settings storage and the UI-only operator note.

The Console keeps its own versioned settings: global defaults in
`web_console_data/settings.json` and per-Runtime overrides (plus the
operator note) in `web_console_data/settings/<runtime id>.json`. Both are
Console metadata — never authoritative Runtime state. Supervisor
model/effort values recorded here are only *defaults*: applying them to a
Runtime always goes through the formal P8 queue
(`queue-supervisor-config`), never through a direct write.

Storage follows the Registry pattern: strict schema validation on read, a
corrupted store fails every operation closed (`SETTINGS_CORRUPT`) and is
never silently reset, and writes are atomic (unique temp file, fsync,
`os.replace`).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from pathlib import Path

SCHEMA_VERSION = 1
SETTINGS_FILE_NAME = "settings.json"
RUNTIME_SETTINGS_SUBDIR = "settings"

RUNTIME_ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")
SUPERVISOR_MODEL_MAX_CHARS = 80
OPERATOR_NOTE_MAX_CHARS = 4000
TIMELINE_PAGE_SIZE_MIN = 5
TIMELINE_PAGE_SIZE_MAX = 100
DECISION_SUMMARY_MODES = ("compact", "full")
# Mirrors the Runtime's queue contract (`SUPERVISOR_CONFIG_EFFORTS`); the
# empty string means "inherit the Runtime's fixed policy".
SUPERVISOR_REASONING_EFFORTS = ("LOW", "MEDIUM", "HIGH")

ALERT_THRESHOLD_BOUNDS = {
    "zcode_pickup_minutes": (1, 10080),
    "expiry_warning_minutes": (1, 10080),
    "expiry_critical_minutes": (1, 10080),
    "turn_minutes": (1, 10080),
    "unconsumed_minutes": (1, 10080),
    "usage_outlier_factor": (1.5, 100.0),
    "usage_outlier_min_sample": (2, 20),
}
ALERT_THRESHOLD_DEFAULTS = {
    "zcode_pickup_minutes": 20,
    "expiry_warning_minutes": 60,
    "expiry_critical_minutes": 15,
    "turn_minutes": 30,
    "unconsumed_minutes": 30,
    "usage_outlier_factor": 3.0,
    "usage_outlier_min_sample": 3,
}
NOTIFICATION_DEFAULTS = {
    # Optional informational events stay off until the operator opts in;
    # must-attention events stay on until explicitly silenced.
    "complete": False,
    "safe_pause": False,
    "zcode_pickup": False,
    "high_token_turn": False,
    "human_review": True,
    "orchestrator_error": True,
    "health_severe": True,
    "expiry_risk": True,
}
DEFAULT_SETTINGS = {
    "supervisor_model": "",
    "supervisor_reasoning_effort": "",
    "decision_summary_mode": "compact",
    "timeline_page_size": 20,
    "alert_thresholds": dict(ALERT_THRESHOLD_DEFAULTS),
    "notifications": dict(NOTIFICATION_DEFAULTS),
}


class SettingsError(ValueError):
    """A settings payload or store that fails closed."""

    def __init__(self, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.code = code
        self.field = field


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and value == value)


def validate_runtime_id(runtime_id) -> str:
    if not isinstance(runtime_id, str) \
            or not RUNTIME_ID_PATTERN.fullmatch(runtime_id):
        raise SettingsError("RUNTIME_ID_INVALID",
                            "the runtime id is not a valid registry id")
    return runtime_id


def _reject_control_characters(text: str, *, allowed="\n\t") -> None:
    for char in text:
        if char in allowed:
            continue
        if unicodedata.category(char) == "Cc":
            raise SettingsError(
                "SETTINGS_VALUE_INVALID",
                "control characters are not allowed in this value")


def validate_model(value) -> str:
    if not isinstance(value, str):
        raise SettingsError("SETTINGS_VALUE_INVALID",
                            "the Supervisor model must be a string",
                            field="supervisor_model")
    stripped = value.strip()
    if len(stripped) > SUPERVISOR_MODEL_MAX_CHARS:
        raise SettingsError(
            "SETTINGS_VALUE_OUT_OF_RANGE",
            f"the Supervisor model must be at most "
            f"{SUPERVISOR_MODEL_MAX_CHARS} characters",
            field="supervisor_model")
    _reject_control_characters(stripped)
    return stripped


def validate_reasoning_effort(value) -> str:
    if value in (None, ""):
        return ""
    if value not in SUPERVISOR_REASONING_EFFORTS:
        raise SettingsError(
            "SETTINGS_VALUE_INVALID",
            "the reasoning effort must be empty or one of "
            + "/".join(SUPERVISOR_REASONING_EFFORTS),
            field="supervisor_reasoning_effort")
    return value


def validate_decision_summary_mode(value) -> str:
    if value not in DECISION_SUMMARY_MODES:
        raise SettingsError(
            "SETTINGS_VALUE_INVALID",
            "the decision summary mode must be one of "
            + "/".join(DECISION_SUMMARY_MODES),
            field="decision_summary_mode")
    return value


def validate_timeline_page_size(value) -> int:
    if not _is_int(value) \
            or not (TIMELINE_PAGE_SIZE_MIN <= value
                    <= TIMELINE_PAGE_SIZE_MAX):
        raise SettingsError(
            "SETTINGS_VALUE_OUT_OF_RANGE",
            f"the Timeline page size must be an integer between "
            f"{TIMELINE_PAGE_SIZE_MIN} and {TIMELINE_PAGE_SIZE_MAX}",
            field="timeline_page_size")
    return value


def _validate_threshold_value(name: str, value):
    low, high = ALERT_THRESHOLD_BOUNDS[name]
    if name == "usage_outlier_factor":
        if not _is_number(value) or not (low <= value <= high):
            raise SettingsError(
                "SETTINGS_VALUE_OUT_OF_RANGE",
                f"the alert threshold {name} must be between {low} and "
                f"{high}", field=f"alert_thresholds.{name}")
        return value
    if not _is_int(value) or not (low <= value <= high):
        raise SettingsError(
            "SETTINGS_VALUE_OUT_OF_RANGE",
            f"the alert threshold {name} must be an integer between {low} "
            f"and {high}", field=f"alert_thresholds.{name}")
    return value


def _check_threshold_consistency(thresholds: dict) -> None:
    if thresholds["expiry_critical_minutes"] \
            > thresholds["expiry_warning_minutes"]:
        raise SettingsError(
            "SETTINGS_VALUE_INVALID",
            "expiry_critical_minutes must not exceed "
            "expiry_warning_minutes",
            field="alert_thresholds.expiry_critical_minutes")


def validate_alert_thresholds(value, *, partial: bool = False) -> dict:
    if not isinstance(value, dict):
        raise SettingsError("SETTINGS_VALUE_INVALID",
                            "alert thresholds must be an object",
                            field="alert_thresholds")
    validated = {}
    for name, threshold in value.items():
        if name not in ALERT_THRESHOLD_BOUNDS:
            raise SettingsError("SETTINGS_UNKNOWN_FIELD",
                                f"unknown alert threshold {name!r}",
                                field=f"alert_thresholds.{name}")
        validated[name] = _validate_threshold_value(name, threshold)
    if partial:
        if "expiry_warning_minutes" in validated \
                and "expiry_critical_minutes" in validated:
            _check_threshold_consistency({
                **ALERT_THRESHOLD_DEFAULTS, **validated})
        return validated
    merged = {**ALERT_THRESHOLD_DEFAULTS, **validated}
    for name in ALERT_THRESHOLD_BOUNDS:
        if name not in validated:
            raise SettingsError(
                "SETTINGS_VALUE_INVALID",
                f"the alert threshold {name} is required",
                field=f"alert_thresholds.{name}")
    _check_threshold_consistency(merged)
    return validated


def validate_notifications(value, *, partial: bool = False) -> dict:
    if not isinstance(value, dict):
        raise SettingsError("SETTINGS_VALUE_INVALID",
                            "notification preferences must be an object",
                            field="notifications")
    validated = {}
    for name, flag in value.items():
        if name not in NOTIFICATION_DEFAULTS:
            raise SettingsError("SETTINGS_UNKNOWN_FIELD",
                                f"unknown notification preference {name!r}",
                                field=f"notifications.{name}")
        if not isinstance(flag, bool):
            raise SettingsError(
                "SETTINGS_VALUE_INVALID",
                f"the notification preference {name} must be a boolean",
                field=f"notifications.{name}")
        validated[name] = flag
    if not partial:
        for name in NOTIFICATION_DEFAULTS:
            if name not in validated:
                raise SettingsError(
                    "SETTINGS_VALUE_INVALID",
                    f"the notification preference {name} is required",
                    field=f"notifications.{name}")
    return validated


_SCALAR_VALIDATORS = {
    "supervisor_model": validate_model,
    "supervisor_reasoning_effort": validate_reasoning_effort,
    "decision_summary_mode": validate_decision_summary_mode,
    "timeline_page_size": validate_timeline_page_size,
}


def validate_settings_payload(payload, *, partial: bool) -> dict:
    """Validate one settings mapping; return only the provided keys
    (partial) or the full normalized mapping."""
    if not isinstance(payload, dict):
        raise SettingsError("SETTINGS_INVALID_PAYLOAD",
                            "the settings payload must be a JSON object")
    validated = {}
    for key, value in payload.items():
        if key not in DEFAULT_SETTINGS:
            raise SettingsError("SETTINGS_UNKNOWN_FIELD",
                                f"unknown setting {key!r}", field=key)
        if key == "alert_thresholds":
            validated[key] = validate_alert_thresholds(value, partial=partial)
        elif key == "notifications":
            validated[key] = validate_notifications(value, partial=partial)
        else:
            validated[key] = _SCALAR_VALIDATORS[key](value)
    if not partial:
        for key in DEFAULT_SETTINGS:
            if key not in validated:
                raise SettingsError("SETTINGS_VALUE_INVALID",
                                    f"the setting {key} is required",
                                    field=key)
    return validated


def validate_override_payload(payload) -> dict:
    """Validate a per-Runtime override request: exactly
    `{"overrides": {setting: value | null}}`; `null` clears an override."""
    if not isinstance(payload, dict) or set(payload) != {"overrides"} \
            or not isinstance(payload["overrides"], dict):
        raise SettingsError(
            "SETTINGS_INVALID_PAYLOAD",
            'the override payload must be exactly {"overrides": {...}} '
            "with an object value")
    overrides = {}
    for key, value in payload["overrides"].items():
        if key not in DEFAULT_SETTINGS:
            raise SettingsError("SETTINGS_UNKNOWN_FIELD",
                                f"unknown setting {key!r}", field=key)
        if value is None:
            overrides[key] = None
        elif key == "alert_thresholds":
            if not isinstance(value, dict):
                raise SettingsError(
                    "SETTINGS_VALUE_INVALID",
                    "alert threshold overrides must be an object",
                    field=key)
            nested = {}
            for name, threshold in value.items():
                if name not in ALERT_THRESHOLD_BOUNDS:
                    raise SettingsError(
                        "SETTINGS_UNKNOWN_FIELD",
                        f"unknown alert threshold {name!r}",
                        field=f"alert_thresholds.{name}")
                nested[name] = None if threshold is None \
                    else _validate_threshold_value(name, threshold)
            overrides[key] = nested
        elif key == "notifications":
            if not isinstance(value, dict):
                raise SettingsError(
                    "SETTINGS_VALUE_INVALID",
                    "notification overrides must be an object", field=key)
            nested = {}
            for name, flag in value.items():
                if name not in NOTIFICATION_DEFAULTS:
                    raise SettingsError(
                        "SETTINGS_UNKNOWN_FIELD",
                        f"unknown notification preference {name!r}",
                        field=f"notifications.{name}")
                if flag is not None and not isinstance(flag, bool):
                    raise SettingsError(
                        "SETTINGS_VALUE_INVALID",
                        f"the notification preference {name} must be a "
                        "boolean", field=f"notifications.{name}")
                nested[name] = flag
            overrides[key] = nested
        else:
            overrides[key] = _SCALAR_VALIDATORS[key](value)
    return overrides


def resolve_settings(partial: dict | None) -> dict:
    """Merge a validated partial settings mapping over the defaults."""
    resolved = json.loads(json.dumps(DEFAULT_SETTINGS))  # deep copy
    if partial:
        for key, value in partial.items():
            if key in ("alert_thresholds", "notifications"):
                resolved[key].update(value)
            else:
                resolved[key] = value
    return resolved


def effective_settings(global_settings: dict, overrides: dict | None) -> dict:
    """Deterministic precedence: a present override wins; `null` (or a null
    nested entry) means "inherit the global value". Every effective value
    carries its source so the UI/API can never blur inherited and
    overridden values."""
    effective = {}
    for key, default in global_settings.items():
        override = (overrides or {}).get(key)
        if isinstance(default, dict):
            nested = {}
            for name, nested_default in default.items():
                nested_value = nested_default
                source = "global-default"
                if isinstance(override, dict) and name in override:
                    if override[name] is not None:
                        nested_value = override[name]
                        source = "override"
                nested[name] = {"value": nested_value, "source": source}
            effective[key] = nested
        else:
            value, source = default, "global-default"
            if override is not None:
                value, source = override, "override"
            effective[key] = {"value": value, "source": source}
    return effective


def validate_operator_note(text) -> str:
    """The UI-only operator note: bounded text, stored verbatim (modulo
    newline normalization), never sent anywhere by the Console itself."""
    if not isinstance(text, str):
        raise SettingsError("SETTINGS_NOTE_INVALID",
                            "the operator note must be a string",
                            field="operator_note")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(normalized) > OPERATOR_NOTE_MAX_CHARS:
        raise SettingsError(
            "SETTINGS_NOTE_INVALID",
            f"the operator note must be at most {OPERATOR_NOTE_MAX_CHARS} "
            "characters", field="operator_note")
    _reject_control_characters(normalized, allowed="\n\t")
    return normalized


def _atomic_write_json(path: Path, value) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write((json.dumps(value, ensure_ascii=False, indent=2)
                          + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _read_json_file(path: Path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError("SETTINGS_CORRUPT",
                            f"the settings store could not be read: {exc}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise SettingsError("SETTINGS_CORRUPT",
                            "the settings store is not valid UTF-8 "
                            f"JSON: {exc}") from exc


class SettingsStore:
    """Console-owned settings storage (global + per-Runtime)."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)

    # -- global ----------------------------------------------------------

    def global_path(self) -> Path:
        return self.data_dir / SETTINGS_FILE_NAME

    def read_global(self) -> dict:
        document = _read_json_file(self.global_path())
        if document is None:
            return {"schema_version": SCHEMA_VERSION, "updated_at": None,
                    "settings": resolve_settings({})}
        try:
            if not isinstance(document, dict) \
                    or set(document) != {"schema_version", "updated_at",
                                         "settings"} \
                    or document["schema_version"] != SCHEMA_VERSION:
                raise ValueError("unexpected document shape")
            validate_settings_payload(document["settings"], partial=False)
        except (ValueError, SettingsError) as exc:
            raise SettingsError(
                "SETTINGS_CORRUPT",
                f"the global settings store is unusable: {exc}") from exc
        return document

    def write_global(self, *, settings: dict, updated_at: str) -> dict:
        validate_settings_payload(settings, partial=False)
        document = {"schema_version": SCHEMA_VERSION,
                    "updated_at": updated_at, "settings": settings}
        _atomic_write_json(self.global_path(), document)
        return document

    def merge_global(self, *, partial, updated_at: str) -> dict:
        stored = self.read_global()
        merged = dict(stored["settings"])
        for key, value in partial.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = {**merged[key], **value}
            else:
                merged[key] = value
        validate_settings_payload(merged, partial=False)
        return self.write_global(settings=merged, updated_at=updated_at)

    # -- per-Runtime -------------------------------------------------------

    def runtime_path(self, runtime_id: str) -> Path:
        validate_runtime_id(runtime_id)
        return self.data_dir / RUNTIME_SETTINGS_SUBDIR / f"{runtime_id}.json"

    def read_runtime(self, runtime_id: str) -> dict:
        path = self.runtime_path(runtime_id)
        document = _read_json_file(path)
        if document is None:
            return {"schema_version": SCHEMA_VERSION, "runtime_id":
                    runtime_id, "updated_at": None, "overrides": {},
                    "operator_note": None}
        try:
            if not isinstance(document, dict) \
                    or set(document) != {"schema_version", "runtime_id",
                                         "updated_at", "overrides",
                                         "operator_note"} \
                    or document["schema_version"] != SCHEMA_VERSION \
                    or document["runtime_id"] != runtime_id:
                raise ValueError("unexpected document shape")
            validate_override_payload({"overrides": document["overrides"]})
            note = document["operator_note"]
            if note is not None:
                if not isinstance(note, dict) \
                        or set(note) != {"text", "updated_at"}:
                    raise ValueError("unexpected operator note shape")
                validate_operator_note(note["text"])
        except (ValueError, SettingsError) as exc:
            raise SettingsError(
                "SETTINGS_CORRUPT",
                f"the stored settings for {runtime_id} are unusable: {exc}"
                ) from exc
        return document

    def write_overrides(self, runtime_id: str, *, overrides: dict,
                        updated_at: str) -> dict:
        document = self.read_runtime(runtime_id)
        merged = dict(document["overrides"])
        for key, value in overrides.items():
            if value is None:
                merged.pop(key, None)
            elif isinstance(value, dict) and isinstance(merged.get(key),
                                                        dict):
                nested = dict(merged[key])
                for name, sub in value.items():
                    if sub is None:
                        nested.pop(name, None)
                    else:
                        nested[name] = sub
                if nested:
                    merged[key] = nested
                else:
                    merged.pop(key, None)
            else:
                merged[key] = value
        # Re-validate the merged override map shape via the same validator
        # used on read (each present key must be a valid override value).
        validate_override_payload({"overrides": merged})
        document = {"schema_version": SCHEMA_VERSION, "runtime_id":
                    runtime_id, "updated_at": updated_at,
                    "overrides": merged,
                    "operator_note": document["operator_note"]}
        _atomic_write_json(self.runtime_path(runtime_id), document)
        return document

    def write_note(self, runtime_id: str, *, note: dict | None,
                   updated_at: str) -> dict:
        document = self.read_runtime(runtime_id)
        if note is not None:
            note = {"text": validate_operator_note(note.get("text")),
                    "updated_at": updated_at}
        document = {"schema_version": SCHEMA_VERSION, "runtime_id":
                    runtime_id, "updated_at": updated_at,
                    "overrides": document["overrides"],
                    "operator_note": note}
        _atomic_write_json(self.runtime_path(runtime_id), document)
        return document
