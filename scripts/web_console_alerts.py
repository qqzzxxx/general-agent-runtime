"""P9 deterministic Runtime-health soft alerts and usage outlier flags.

Pure projection layer (no clock, disk, randomness, or subprocess). Every
alert is derived only from evidence the caller passes in:

- `status`: the v1.2 control-plane `status --json` document (including the
  P9 additive observability keys `supervisor_turn_inflight` and
  `active_task_completion`);
- `probe_ok` / `probe_error_code`: the outcome of the read-only status
  probe itself;
- `usage_document`: the P8 Supervisor usage document (reported token
  totals only);
- `thresholds`: operator settings (defaults resolved when absent).

Every alert carries a stable identity (stable across polls for the same
underlying fact), Runtime attribution via the response envelope, a severity
from the fixed vocabulary ("informational" / "warning" /
"needs-attention"), a notification classification, and evidence with
timestamps/facts. Missing or unreadable evidence is listed honestly and
never fabricated into an alert.
"""
from __future__ import annotations

from datetime import datetime

from web_console_settings import ALERT_THRESHOLD_DEFAULTS

SCHEMA_VERSION = 1
SEVERITY_VOCABULARY = ("informational", "warning", "needs-attention")
_SEVERITY_RANK = {"needs-attention": 0, "warning": 1, "informational": 2}

NOTIFICATION_MUST_ATTENTION = "must-attention"
NOTIFICATION_OPTIONAL = "optional-informational"
NOTIFICATION_NONE = "none"

# Notification kinds mirror the spec's event classes (§24). They are stable
# identifiers the frontend maps onto operator preferences.
KIND_HUMAN_REVIEW = "HUMAN_REVIEW"
KIND_ORCHESTRATOR_ERROR = "ORCHESTRATOR_ERROR"
KIND_HEALTH_SEVERE = "HEALTH_SEVERE"
KIND_EXPIRY_RISK = "EXPIRY_RISK"
KIND_COMPLETE = "COMPLETE"
KIND_SAFE_PAUSE = "SAFE_PAUSE"
KIND_ZCODE_PICKUP = "ZCODE_PICKUP"
KIND_HIGH_TOKEN_TURN = "HIGH_TOKEN_TURN"


def parse_timestamp(value):
    """Parse one ISO timestamp; naive or unreadable values are unavailable
    (never guessed)."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed


def resolve_thresholds(overrides) -> dict:
    thresholds = dict(ALERT_THRESHOLD_DEFAULTS)
    if isinstance(overrides, dict):
        provided = overrides.get("alert_thresholds")
        if isinstance(provided, dict):
            for key in thresholds:
                value = provided.get(key)
                if value is not None:
                    thresholds[key] = value
    return thresholds


def _alert(*, rule, severity, message, evidence, notification_class,
           notification_kind, identity):
    return {"id": identity, "rule": rule, "severity": severity,
            "message": message,
            "notification_class": notification_class,
            "notification_kind": notification_kind,
            "evidence": evidence}


def _age_minutes(now: datetime, when: datetime) -> float:
    return round((now - when).total_seconds() / 60.0, 1)


def _escalation(age_or_remaining: float, threshold: float) -> str:
    if age_or_remaining >= threshold * 3:
        return "needs-attention"
    return "warning"


def usage_outlier_flags(usage_document, *, thresholds: dict) -> dict:
    """Flag the newest reported turn when its total is unusually expensive
    relative to the recent reported average. Only reliably reported totals
    participate; anything missing or partial stays out of the comparison,
    and ZCode usage is never a comparable sample."""
    not_comparable = None
    outliers = []
    usage = usage_document.get("usage") \
        if isinstance(usage_document, dict) else None
    turns = usage.get("recent_reported_turns") \
        if isinstance(usage, dict) else None
    samples = []
    if isinstance(turns, list):
        for turn in turns:
            if not isinstance(turn, dict):
                continue
            turn_id = turn.get("turn_id")
            total = turn.get("total_tokens")
            if isinstance(turn_id, str) and isinstance(total, int) \
                    and not isinstance(total, bool) and total >= 0:
                samples.append((turn_id, total))
    if not samples:
        not_comparable = ("usage outlier rule is not comparable yet: no "
                          "Supervisor turn has reported token usage")
        return {"outliers": outliers, "not_comparable": not_comparable,
                "evaluated_turns": 0, "baseline_samples": 0}
    candidate_id, candidate_total = samples[0]
    baseline = samples[1:1 + 10]
    minimum = thresholds["usage_outlier_min_sample"]
    factor = thresholds["usage_outlier_factor"]
    if len(baseline) < minimum:
        not_comparable = (
            f"usage outlier rule is not comparable yet: only "
            f"{len(baseline)} comparable prior turn(s) reported usage; the "
            f"minimum sample is {minimum}")
        return {"outliers": outliers, "not_comparable": not_comparable,
                "evaluated_turns": len(samples),
                "baseline_samples": len(baseline)}
    average = sum(total for _, total in baseline) / len(baseline)
    if candidate_total > factor * average:
        outliers.append({
            "turn_id": candidate_id, "total_tokens": candidate_total,
            "baseline_average_tokens": round(average, 1),
            "baseline_samples": len(baseline),
            "baseline_turn_ids": [turn_id for turn_id, _ in baseline],
            "factor": factor})
    return {"outliers": outliers, "not_comparable": not_comparable,
            "evaluated_turns": len(samples), "baseline_samples":
            len(baseline)}


def project_alerts(*, runtime, status, probe_ok, probe_error_code,
                   usage_document, thresholds, now, generated_at) -> dict:
    """Project one bounded, deterministic alert document for one Runtime."""
    alerts = []
    honesty = []

    def note(text: str) -> None:
        honesty.append(text)

    if not probe_ok:
        alerts.append(_alert(
            rule="runtime-offline", severity="needs-attention",
            message="the Web Console could not reach this Runtime's "
                    "control plane",
            evidence={"probe_error_code": probe_error_code},
            notification_class=NOTIFICATION_MUST_ATTENTION,
            notification_kind=KIND_HEALTH_SEVERE,
            identity="runtime-offline"))
        note("status-derived alerts are suppressed for this cycle because "
             "the read-only status probe failed; the offline alert is the "
             "only honest signal available")
    elif not isinstance(status, dict):
        note("no live evidence: the control-plane status document is "
             "unavailable")
    else:
        active = status.get("active_task") \
            if isinstance(status.get("active_task"), dict) else None
        authorized = status.get("last_authorized_dispatch") \
            if isinstance(status.get("last_authorized_dispatch"), dict) \
            else None
        # Only the producer's current identity is eligible. Historical dispatch
        # timestamps cannot be joined to lifecycle facts for a different task.
        auth = active
        from web_console_state import interpret_status
        complete = (status.get("runtime_status") == "COMPLETE"
                    and status.get("project_status") == "COMPLETE"
                    and interpret_status(status)["state"]["family"] == "COMPLETE")
        inflight = status.get("supervisor_turn_inflight") \
            if isinstance(status.get("supervisor_turn_inflight"), dict) \
            else None
        completion = status.get("active_task_completion") \
            if isinstance(status.get("active_task_completion"), dict) \
            else None
        completion_status = (completion or {}).get("status") \
            or status.get("active_task_completion_status")
        claimed = status.get("active_task_claimed") is True
        consumed = status.get("last_consumed_message_id")
        identity_keys = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")
        if (not isinstance(auth, dict) or not isinstance(authorized, dict)
                or any(auth.get(k) != authorized.get(k) for k in identity_keys)
                or status.get("project_status") != "WAITING_EXECUTOR"
                or status.get("runtime_status") not in {"RUNNING", "WAITING_EXECUTOR"}
                or status.get("human_review") is True or status.get("stop") is True
                or (status.get("pause") or {}).get("status") == "PAUSED"
                or status.get("active_task_retired") is True
                or completion_status is not None
                or (type(consumed) is int and type(auth.get("MESSAGE_ID")) is int
                    and auth["MESSAGE_ID"] <= consumed)):
            auth = None

        # Non-timing diagnostics can still identify retained historical facts.
        subject = active or authorized
        message_id = subject.get("MESSAGE_ID") if subject else None
        message_label = str(message_id) if isinstance(message_id, int) \
            and not isinstance(message_id, bool) else "active"

        # -- ZCode pickup -------------------------------------------------
        if isinstance(auth, dict):
            authorized_at = parse_timestamp(auth.get("AUTHORIZED_AT"))
            pickup_threshold = thresholds["zcode_pickup_minutes"]
            if authorized_at is None:
                note("zcode pickup rule not evaluated: AUTHORIZED_AT is "
                     "present but unreadable")
            elif claimed or completion_status is not None:
                pass
            else:
                age = _age_minutes(now, authorized_at)
                if age > pickup_threshold:
                    severity = _escalation(age, pickup_threshold)
                    alerts.append(_alert(
                        rule="zcode-pickup", severity=severity,
                        message="the Executor has not picked up the "
                                "authorized task",
                        evidence={"message_id": message_id,
                                  "authorized_at":
                                  auth.get("AUTHORIZED_AT"),
                                  "age_minutes": age,
                                  "claimed": claimed},
                        notification_class=NOTIFICATION_OPTIONAL,
                        notification_kind=KIND_ZCODE_PICKUP,
                        identity=f"zcode-pickup:{message_label}"))

        # -- Authorization expiry -----------------------------------------
        if isinstance(auth, dict):
            expires_at = parse_timestamp(auth.get("EXPIRES_AT"))
            warning_window = thresholds["expiry_warning_minutes"]
            critical_window = thresholds["expiry_critical_minutes"]
            if expires_at is None:
                note("authorization-expiry not evaluated: EXPIRES_AT is "
                     "present but unreadable")
            elif completion_status is not None:
                pass
            else:
                remaining = round((expires_at - now).total_seconds() / 60.0,
                                  1)
                if remaining <= 0:
                    severity, kind_class, kind = "needs-attention", \
                        NOTIFICATION_MUST_ATTENTION, KIND_EXPIRY_RISK
                elif remaining <= critical_window:
                    severity, kind_class, kind = "needs-attention", \
                        NOTIFICATION_MUST_ATTENTION, KIND_EXPIRY_RISK
                elif remaining <= warning_window:
                    severity, kind_class, kind = "warning", \
                        NOTIFICATION_NONE, None
                else:
                    severity, kind_class, kind = None, None, None
                if severity is not None:
                    alerts.append(_alert(
                        rule="authorization-expiry", severity=severity,
                        message="the current dispatch authorization is "
                                "nearing (or past) its expiry",
                        evidence={"message_id": message_id,
                                  "expires_at": auth.get("EXPIRES_AT"),
                                  "remaining_minutes": remaining},
                        notification_class=kind_class,
                        notification_kind=kind,
                        identity=f"authorization-expiry:{message_label}"))

        # -- Long-running Supervisor turn ---------------------------------
        if inflight is not None:
            started_at = parse_timestamp(inflight.get("started_at"))
            turn_id = inflight.get("turn_id")
            turn_threshold = thresholds["turn_minutes"]
            if started_at is None or not isinstance(turn_id, str):
                note("codex-turn-long not evaluated: the in-flight turn "
                     "record is incomplete or unreadable")
            else:
                age = _age_minutes(now, started_at)
                if age > turn_threshold:
                    severity = _escalation(age, turn_threshold)
                    alerts.append(_alert(
                        rule="codex-turn-long", severity=severity,
                        message="the Supervisor turn has been running "
                                "unusually long",
                        evidence={"turn_id": turn_id,
                                  "started_at": inflight.get("started_at"),
                                  "age_minutes": age},
                        notification_class=NOTIFICATION_NONE,
                        notification_kind=None,
                        identity=f"codex-turn-long:{turn_id}"))

        # -- Committed but unconsumed completion ---------------------------
        if completion is not None:
            committed_at = parse_timestamp(completion.get("committed_at"))
            unconsumed_threshold = thresholds["unconsumed_minutes"]
            if completion.get("status") == "COMPLETION_COMMITTED":
                if committed_at is None:
                    note("completion-unconsumed not evaluated: the "
                         "completion commit time is unreadable")
                else:
                    age = _age_minutes(now, committed_at)
                    if age > unconsumed_threshold:
                        severity = _escalation(age, unconsumed_threshold)
                        alerts.append(_alert(
                            rule="completion-unconsumed", severity=severity,
                            message="a completion was committed but not "
                                    "consumed within the expected time",
                            evidence={"status": completion.get("status"),
                                      "committed_at":
                                      completion.get("committed_at"),
                                      "age_minutes": age,
                                      "message_id": message_id},
                            notification_class=NOTIFICATION_NONE,
                            notification_kind=None,
                            identity=f"completion-unconsumed:"
                                     f"{message_label}"))

        # -- Existing severe / error states --------------------------------
        project_id = status.get("PROJECT_ID")
        project_label = project_id if isinstance(project_id, str) \
            and project_id else "project"
        if status.get("human_review") is True:
            alerts.append(_alert(
                rule="human-review", severity="needs-attention",
                message="human review has been requested for this Runtime",
                evidence={"project_id": project_id},
                notification_class=NOTIFICATION_MUST_ATTENTION,
                notification_kind=KIND_HUMAN_REVIEW,
                identity=f"human-review:{project_label}"))
        if status.get("stop") is True:
            alerts.append(_alert(
                rule="stop-flag", severity="warning",
                message="a formal STOP flag is present in the Runtime",
                evidence={"project_id": project_id},
                notification_class=NOTIFICATION_NONE, notification_kind=None,
                identity=f"stop-flag:{project_label}"))
        runtime_status = status.get("runtime_status")
        if runtime_status == "ORCHESTRATOR_ERROR":
            alerts.append(_alert(
                rule="orchestrator-error", severity="needs-attention",
                message="the Orchestrator reported an error condition",
                evidence={"runtime_status": runtime_status},
                notification_class=NOTIFICATION_MUST_ATTENTION,
                notification_kind=KIND_ORCHESTRATOR_ERROR,
                identity=f"orchestrator-error:{project_label}"))
        if runtime_status == "DEADLINE_REACHED":
            alerts.append(_alert(
                rule="deadline-reached", severity="warning",
                message="the scheduled deadline was reached",
                evidence={"runtime_status": runtime_status},
                notification_class=NOTIFICATION_NONE, notification_kind=None,
                identity=f"deadline-reached:{project_label}"))
        if status.get("project_status") == "BLOCKED":
            alerts.append(_alert(
                rule="project-blocked", severity="needs-attention",
                message="the project reported a blocked stage",
                evidence={"project_status": status.get("project_status")},
                notification_class=NOTIFICATION_MUST_ATTENTION,
                notification_kind=KIND_HEALTH_SEVERE,
                identity=f"project-blocked:{project_label}"))
        if status.get("active_task_retired") is True:
            alerts.append(_alert(
                rule="task-retired", severity="warning",
                message="the last authorized task was retired",
                evidence={"message_id": message_id},
                notification_class=NOTIFICATION_NONE, notification_kind=None,
                identity=f"task-retired:{message_label}"))

        # -- Lifecycle informational events ---------------------------------
        if complete:
            alerts.append(_alert(
                rule="project-complete", severity="informational",
                message="the project reached COMPLETE",
                evidence={"project_status": "COMPLETE"},
                notification_class=NOTIFICATION_OPTIONAL,
                notification_kind=KIND_COMPLETE,
                identity=f"project-complete:{project_label}"))
        pause = status.get("pause") if isinstance(status.get("pause"), dict) \
            else None
        if pause is not None and pause.get("status") == "PAUSED" \
                and pause.get("mode") == "SAFE":
            alerts.append(_alert(
                rule="safe-pause", severity="informational",
                message="the Runtime reached a safe Pause",
                evidence={"pause_status": "PAUSED", "pause_mode": "SAFE"},
                notification_class=NOTIFICATION_OPTIONAL,
                notification_kind=KIND_SAFE_PAUSE,
                identity=f"safe-pause:{project_label}"))

        if not any((active, authorized, inflight, completion)) \
                and not (status.get("human_review") or status.get("stop")) \
                and runtime_status not in ("ORCHESTRATOR_ERROR",
                                           "DEADLINE_REACHED") \
                and status.get("project_status") not in ("BLOCKED",
                                                         "COMPLETE"):
            note("no live evidence for pickup, expiry, turn, completion, "
                 "or usage rules")

    # -- Reported-usage outliers --------------------------------------------
    if usage_document is not None:
        flags = usage_outlier_flags(usage_document, thresholds=thresholds)
        for outlier in flags["outliers"]:
            alerts.append(_alert(
                rule="usage-outlier", severity="informational",
                message="a Supervisor turn reported unusually expensive "
                        "token usage relative to the recent average",
                evidence={"turn_id": outlier["turn_id"],
                          "total_tokens": outlier["total_tokens"],
                          "baseline_average_tokens":
                          outlier["baseline_average_tokens"],
                          "baseline_samples": outlier["baseline_samples"],
                          "factor": outlier["factor"]},
                notification_class=NOTIFICATION_OPTIONAL,
                notification_kind=KIND_HIGH_TOKEN_TURN,
                identity=f"usage-outlier:{outlier['turn_id']}"))
        if flags["not_comparable"]:
            note(flags["not_comparable"])
    else:
        note("no Supervisor usage document was provided; the usage-outlier "
             "rule is not comparable this cycle")

    alerts.sort(key=lambda alert: (_SEVERITY_RANK[alert["severity"]],
                                   alert["rule"], alert["id"]))
    return {"schema_version": SCHEMA_VERSION, "generated_at": generated_at,
            "runtime": runtime,
            "severity_vocabulary": list(SEVERITY_VOCABULARY),
            "thresholds": dict(thresholds), "alerts": alerts,
            "honesty": honesty}
