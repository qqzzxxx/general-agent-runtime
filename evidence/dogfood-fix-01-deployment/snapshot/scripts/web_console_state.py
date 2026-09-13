"""Deterministic Runtime-state interpretation for the v1.3 Web Console (P3).

A pure, offline interpreter that maps the bounded read-only
`supervisor_control.py status --json` document of one Runtime to a
human-readable Cockpit presentation: Current Execution (who is working, what
they are doing, since when, what happens next, whether the user must act),
protocol milestones as facts, and bounded Runtime Health facts.

Hard contracts:

- Deterministic and side-effect free: no clock, no randomness, no model
  calls, no filesystem or subprocess access. The same input document always
  produces the same interpretation.
- Fail closed: unknown enumeration values, malformed field types,
  contradictory fact combinations, and unusable documents produce an explicit
  honest ``STATE_UNAVAILABLE`` presentation with machine-readable reasons —
  never a guess.
- Facts only: no invented percentages, token usage, activity, or timestamps.
  ``since`` is reported only where the status document itself carries an
  authoritative timestamp (the pause record). The PUBLISHED milestone is
  reported as ``reached: null`` because the v1.2 status surface does not
  expose publication facts.

The status value spaces below mirror the v1.2 control plane
(``scripts/supervisor_control.py current_status``) and the v1.2 Orchestrator
(``control/orchestrator_runtime.json`` status, the control ``pause`` record,
and the completion ledger STATUS values).
"""
from __future__ import annotations

COCKPIT_INTERPRETATION_SCHEMA_VERSION = 1

FAM_CODEX_THINKING = "CODEX_THINKING"
FAM_WAITING_FOR_ZCODE_CLAIM = "WAITING_FOR_ZCODE_CLAIM"
FAM_ZCODE_EXECUTING = "ZCODE_EXECUTING"
FAM_COMPLETION_COMMITTED = "COMPLETION_COMMITTED"
FAM_RESULT_EVALUATION = "RESULT_EVALUATION"
FAM_FINAL_VERIFICATION_WAITING_CLAIM = "FINAL_VERIFICATION_WAITING_CLAIM"
FAM_FINAL_VERIFICATION_EXECUTING = "FINAL_VERIFICATION_EXECUTING"
FAM_PAUSE_REQUESTED = "PAUSE_REQUESTED"
FAM_PAUSED = "PAUSED"
FAM_HUMAN_REVIEW = "HUMAN_REVIEW"
FAM_COMPLETE = "COMPLETE"
FAM_STOPPED = "STOPPED"
FAM_ERROR = "ERROR"
FAM_IDLE = "IDLE"
FAM_NO_PROJECT = "NO_PROJECT"
FAM_STATE_UNAVAILABLE = "STATE_UNAVAILABLE"

KNOWN_RUNTIME_STATUSES = frozenset({
    "RUNNING", "PAUSED", "STOPPED", "STOPPED_BY_USER", "HUMAN_REVIEW",
    "DEADLINE_REACHED", "ORCHESTRATOR_ERROR",
})
KNOWN_PROJECT_STATUSES = frozenset({
    "WAITING_EXECUTOR", "SUPERVISOR_TURN", "COMPLETE", "BLOCKED", "STOPPED",
    "HUMAN_REVIEW", "PAUSED",
})
KNOWN_PAUSE_STATUSES = frozenset({
    "RUNNING", "PENDING_AFTER_CURRENT_STAGE", "PAUSED",
})
KNOWN_PAUSE_MODES = frozenset({"SAFE", "INTERRUPT_CURRENT"})
KNOWN_COMPLETION_STATUSES = frozenset({
    "COMPLETION_COMMITTED", "COMPLETION_CONSUMED", "COMPLETION_SEALED",
})

_IDENTITY_TYPES = (("MESSAGE_ID", int), ("TASK_ID", str), ("STAGE_ID", str),
                   ("ATTEMPT", int), ("NONCE", str))
_FV_KEY = "IS_FINAL_VERIFICATION"

_STATE_LABELS = {
    FAM_CODEX_THINKING: "Codex (Supervisor) is thinking",
    FAM_WAITING_FOR_ZCODE_CLAIM: "Waiting for ZCode to claim the authorized task",
    FAM_ZCODE_EXECUTING: "ZCode (Executor) is executing the authorized task",
    FAM_COMPLETION_COMMITTED: "ZCode committed its completion; Codex has not consumed it yet",
    FAM_RESULT_EVALUATION: "Codex (Supervisor) is evaluating the Executor result",
    FAM_FINAL_VERIFICATION_WAITING_CLAIM:
        "Waiting for ZCode to claim the Final Verification task",
    FAM_FINAL_VERIFICATION_EXECUTING:
        "Final Verification is executing (read-only or sandboxed)",
    FAM_PAUSE_REQUESTED: "Safe pause requested — it takes effect after the current stage",
    FAM_PAUSED: "Paused — the Runtime is holding at a safe boundary",
    FAM_HUMAN_REVIEW: "Human review — automation stopped for a human decision",
    FAM_COMPLETE: "Complete — the project reached its goal",
    FAM_STOPPED: "Stopped — the Runtime is no longer working",
    FAM_ERROR: "Error — the Runtime reported a failure condition",
    FAM_IDLE: "Idle — waiting for the Supervisor to plan the next step",
    FAM_NO_PROJECT: "No active project in this Runtime",
    FAM_STATE_UNAVAILABLE: "State unavailable — the reported facts do not determine a state",
}

_PROJECT_CODEX_LABELS = {
    "WAITING_EXECUTOR": "idle — waiting for the Executor",
    "SUPERVISOR_TURN": "planning or evaluating",
    "COMPLETE": "finished — project complete",
    "BLOCKED": "reported a blocked stage",
    "STOPPED": "stopped",
    "HUMAN_REVIEW": "waiting for a human decision",
    "PAUSED": "paused",
}

_RUNTIME_ORCHESTRATOR_LABELS = {
    "RUNNING": "the Orchestrator is running",
    "PAUSED": "the Orchestrator recorded PAUSED",
    "STOPPED": "the Orchestrator stopped normally",
    "STOPPED_BY_USER": "stopped by the user",
    "HUMAN_REVIEW": "held for human review",
    "DEADLINE_REACHED": "stopped — scheduled deadline reached",
    "ORCHESTRATOR_ERROR": "the Orchestrator reported an error",
}


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_str(value) -> bool:
    return isinstance(value, str)


def _identity_problems(field: str, value) -> list:
    problems = []
    if not isinstance(value, dict):
        return [f"{field}"]
    for key, kind in _IDENTITY_TYPES:
        checker = _is_int if kind is int else _is_str
        if not checker(value.get(key)):
            problems.append(f"{field}.{key}")
    fv = value.get(_FV_KEY)
    if fv is not None and not isinstance(fv, bool):
        problems.append(f"{field}.{_FV_KEY}")
    return problems


def _identity_of(field: str, value) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {key: value.get(key) for key, _ in _IDENTITY_TYPES}


def _document_problems(status) -> tuple[list, list, list]:
    """Validate one status document; return (unknown, malformed, contradiction)
    reason lists. Structure mirrors supervisor_control.current_status."""
    unknown: list = []
    malformed: list = []
    contradiction: list = []
    if not isinstance(status, dict):
        return unknown, ["document"], contradiction
    if not _is_int(status.get("schema_version")) \
            or status["schema_version"] != 1:
        malformed.append("schema_version")
    project_id = status.get("PROJECT_ID")
    if project_id is not None and not _is_str(project_id):
        malformed.append("PROJECT_ID")

    runtime_status = status.get("runtime_status")
    if runtime_status is not None:
        if not _is_str(runtime_status):
            malformed.append("runtime_status")
        elif runtime_status not in KNOWN_RUNTIME_STATUSES:
            unknown.append("runtime_status")

    project_status = status.get("project_status")
    if project_status is not None:
        if not _is_str(project_status):
            malformed.append("project_status")
        elif project_status not in KNOWN_PROJECT_STATUSES:
            unknown.append("project_status")

    pause = status.get("pause")
    if pause is not None:
        if not isinstance(pause, dict):
            malformed.append("pause")
        else:
            pause_status = pause.get("status")
            # v1.2 pause_status() treats a missing/empty status as RUNNING.
            if pause_status:  # non-empty string expected
                if not _is_str(pause_status):
                    malformed.append("pause.status")
                elif pause_status not in KNOWN_PAUSE_STATUSES:
                    unknown.append("pause.status")
            mode = pause.get("mode")
            if mode is not None and mode not in KNOWN_PAUSE_MODES:
                unknown.append("pause.mode")
            for key in ("requested_at", "paused_at", "resumed_at"):
                stamp = pause.get(key)
                if stamp is not None and not _is_str(stamp):
                    malformed.append(f"pause.{key}")

    active = status.get("active_task")
    if active is not None and not isinstance(active, dict):
        malformed.append("active_task")
    authorized = status.get("last_authorized_dispatch")
    if authorized is not None and not isinstance(authorized, dict):
        malformed.append("last_authorized_dispatch")
    malformed.extend(_identity_problems("active_task", active)
                     if isinstance(active, dict) else [])
    malformed.extend(_identity_problems("last_authorized_dispatch", authorized)
                     if isinstance(authorized, dict) else [])

    for field in ("active_task_claimed", "active_task_claim_recorded",
                  "active_task_retired", "human_review", "stop"):
        if not isinstance(status.get(field), bool):
            malformed.append(field)

    completion = status.get("active_task_completion_status")
    if completion is not None:
        if not _is_str(completion):
            malformed.append("active_task_completion_status")
        elif completion not in KNOWN_COMPLETION_STATUSES:
            unknown.append("active_task_completion_status")

    pending = status.get("pending_interventions")
    if not _is_int(pending) or pending < 0:
        malformed.append("pending_interventions")

    consumed = status.get("last_consumed_message_id")
    if consumed is not None and not _is_int(consumed):
        malformed.append("last_consumed_message_id")

    # -- contradiction checks (only meaningful on typed values) -------------
    if isinstance(active, dict) and project_status != "WAITING_EXECUTOR":
        contradiction.append("ACTIVE_TASK_OUTSIDE_WAITING_EXECUTOR")
    claimed = status.get("active_task_claimed")
    claim_recorded = status.get("active_task_claim_recorded")
    retired = status.get("active_task_retired")
    completion_status = completion if isinstance(completion, str) else None
    if claimed is True and completion_status is not None:
        contradiction.append("CLAIMED_WITH_COMPLETION")
    if claimed is True and claim_recorded is False:
        contradiction.append("CLAIMED_WITHOUT_CLAIM_RECORD")
    if (claim_recorded is True and claimed is False
            and completion_status is None and retired is not True):
        contradiction.append("CLAIM_RECORD_WITHOUT_CLAIM_OR_COMPLETION")
    if retired is True and claimed is True:
        contradiction.append("RETIRED_AND_CLAIMED")
    if isinstance(active, dict) and not isinstance(authorized, dict):
        contradiction.append("ACTIVE_TASK_WITHOUT_AUTHORIZATION")
    elif isinstance(active, dict) and isinstance(authorized, dict):
        if _identity_of("active_task", active) != \
                _identity_of("last_authorized_dispatch", authorized):
            contradiction.append("AUTHORIZATION_IDENTITY_MISMATCH")
    return unknown, malformed, contradiction


def _pause_view(status: dict) -> dict:
    pause = status.get("pause")
    view = {"status": "RUNNING", "requested_at": None, "paused_at": None}
    if isinstance(pause, dict):
        raw_status = pause.get("status")
        if raw_status:
            view["status"] = raw_status
        view["requested_at"] = pause.get("requested_at") \
            if _is_str(pause.get("requested_at")) else None
        view["paused_at"] = pause.get("paused_at") \
            if _is_str(pause.get("paused_at")) else None
    return view


def _worker_for(family: str, status: dict) -> dict:
    if family == FAM_ZCODE_EXECUTING \
            or family == FAM_FINAL_VERIFICATION_EXECUTING \
            or family == FAM_WAITING_FOR_ZCODE_CLAIM \
            or family == FAM_FINAL_VERIFICATION_WAITING_CLAIM:
        who = "ZCode (Executor)"
    elif family in (FAM_CODEX_THINKING, FAM_COMPLETION_COMMITTED,
                    FAM_RESULT_EVALUATION):
        who = "Codex (Supervisor)"
    elif family == FAM_PAUSE_REQUESTED:
        if status.get("active_task_claimed") is True:
            who = "ZCode (Executor)"
        elif status.get("project_status") == "SUPERVISOR_TURN":
            who = "Codex (Supervisor)"
        else:
            who = None
    else:
        who = None
    return {"available": who is not None, "who": who}


def _since_for(family: str, pause: dict) -> dict:
    if family == FAM_PAUSE_REQUESTED and pause.get("requested_at"):
        return {"available": True, "at": pause["requested_at"],
                "source": "pause.requested_at"}
    if family == FAM_PAUSED:
        if pause.get("paused_at"):
            return {"available": True, "at": pause["paused_at"],
                    "source": "pause.paused_at"}
        if pause.get("requested_at"):
            return {"available": True, "at": pause["requested_at"],
                    "source": "pause.requested_at"}
    return {"available": False, "at": None, "source": None}


def _user_action_for(family: str, status: dict) -> tuple[bool, str | None]:
    if status.get("stop") is True:
        return True, ("A formal STOP flag is present. Restarting requires a "
                      "deliberate human start.")
    if status.get("human_review") is True or family == FAM_HUMAN_REVIEW:
        return True, ("Human review has been requested; a human decision is "
                      "required (View Full Reason / Submit Human Decision).")
    if family == FAM_ERROR:
        return True, "The Runtime reported an error condition; inspect Runtime Health."
    if family == FAM_PAUSED:
        return True, "The Runtime is paused; resume it when you are ready."
    if family == FAM_STATE_UNAVAILABLE:
        return False, ("The state cannot be determined from the reported "
                       "facts; inspect the bounded details before acting.")
    return False, None


def _next_expected_for(family: str, status: dict) -> dict:
    active = status.get("active_task") \
        if isinstance(status.get("active_task"), dict) else None
    message_id = active.get("MESSAGE_ID") if active else None
    consumed = status.get("last_consumed_message_id")
    unavailable = {"available": False,
                   "label": ("Unavailable — what happens next cannot be "
                             "determined from the reported facts"),
                   "detail": "", "message_id": None}

    if family == FAM_STATE_UNAVAILABLE:
        return unavailable
    if family == FAM_WAITING_FOR_ZCODE_CLAIM:
        return {"available": True,
                "label": f"Waiting for ZCode to claim MESSAGE {message_id}",
                "detail": ("The task is authorized; the Executor has not "
                           "claimed it yet."), "message_id": message_id}
    if family == FAM_FINAL_VERIFICATION_WAITING_CLAIM:
        return {"available": True,
                "label": (f"Waiting for ZCode to claim Final Verification "
                          f"MESSAGE {message_id}"),
                "detail": ("The Final Verification task is authorized and "
                           "unclaimed; its result may lead to COMPLETE."),
                "message_id": message_id}
    if family == FAM_ZCODE_EXECUTING:
        return {"available": True,
                "label": (f"ZCode will publish its completion of MESSAGE "
                          f"{message_id}; Codex will evaluate it next"),
                "detail": "The Executor does not report progress estimates.",
                "message_id": message_id}
    if family == FAM_FINAL_VERIFICATION_EXECUTING:
        return {"available": True,
                "label": (f"Final Verification of MESSAGE {message_id} is "
                          f"running; its result may lead to COMPLETE"),
                "detail": "The verification gate decision belongs to the "
                          "Supervisor and the mechanical gate.",
                "message_id": message_id}
    if family == FAM_COMPLETION_COMMITTED:
        return {"available": True,
                "label": (f"Codex will evaluate the committed result of "
                          f"MESSAGE {message_id}"),
                "detail": "The completion is committed and awaiting "
                          "consumption by the Supervisor turn.",
                "message_id": message_id}
    if family == FAM_RESULT_EVALUATION:
        shown = message_id if message_id is not None else consumed
        return {"available": True,
                "label": (f"Codex will decide the next step for MESSAGE "
                          f"{shown}"),
                "detail": "The result was consumed; the Supervisor turn "
                          "decides the next move.",
                "message_id": shown}
    if family == FAM_CODEX_THINKING:
        return {"available": True,
                "label": "A new task dispatch for ZCode will be authorized",
                "detail": "The Supervisor turn is planning the next stage.",
                "message_id": None}
    if family == FAM_PAUSE_REQUESTED:
        return {"available": True,
                "label": ("The system will pause safely after the current "
                          "stage completes"),
                "detail": "A currently running valid task may finish; its "
                          "completion stays valid.",
                "message_id": message_id}
    if family == FAM_PAUSED:
        return {"available": True,
                "label": "Work resumes only after a human resume",
                "detail": "No new task will be authorized while paused.",
                "message_id": None}
    if family == FAM_HUMAN_REVIEW:
        return {"available": True,
                "label": "A human decision is required before automation "
                         "can continue",
                "detail": "Automation stopped deliberately; submit a human "
                          "decision to proceed.",
                "message_id": None}
    if family == FAM_COMPLETE:
        return {"available": True,
                "label": "No further work is expected — the project is "
                         "COMPLETE",
                "detail": "History remains inspectable.",
                "message_id": None}
    if family == FAM_STOPPED:
        return {"available": True,
                "label": "No further work is expected — the Runtime is "
                         "stopped",
                "detail": "A stopped Runtime does not resurrect by ordinary "
                          "resume; a deliberate start is required.",
                "message_id": None}
    if family == FAM_ERROR:
        return {"available": True,
                "label": "Automation is halted; follow the incident "
                         "runbook or submit a human intervention",
                "detail": "The Runtime recorded an error condition.",
                "message_id": None}
    if family == FAM_IDLE:
        return {"available": True,
                "label": "The Supervisor will plan and authorize the next "
                         "task",
                "detail": "No task is currently authorized.",
                "message_id": None}
    if family == FAM_NO_PROJECT:
        return {"available": True,
                "label": "Start a project in this Runtime to begin",
                "detail": "The Runtime has no active project yet.",
                "message_id": None}
    return unavailable


def _milestones_for(active: dict | None, claimed, claim_recorded,
                    completion: str | None) -> list:
    if not isinstance(active, dict):
        return []
    return [
        {"key": "AUTHORIZED", "label": "Authorized", "reached": True,
         "evidence": "active_task"},
        {"key": "CLAIMED", "label": "Claimed", "reached": claim_recorded,
         "evidence": "active_task_claim_recorded"},
        {"key": "WORKING", "label": "Working", "reached": claimed,
         "evidence": "active_task_claimed"},
        # The v1.2 status surface exposes no publication fact, so this
        # milestone is honestly unreported instead of inferred.
        {"key": "PUBLISHED", "label": "Published", "reached": None,
         "evidence": None},
        {"key": "COMPLETION_COMMITTED", "label": "Completion committed",
         "reached": completion == "COMPLETION_COMMITTED",
         "evidence": "active_task_completion_status"},
        {"key": "CONSUMED_SEALED", "label": "Consumed/sealed",
         "reached": completion in ("COMPLETION_CONSUMED",
                                   "COMPLETION_SEALED"),
         "evidence": "active_task_completion_status"},
    ]


def _health_for(status: dict) -> dict:
    project_id = status.get("PROJECT_ID")
    runtime_status = status.get("runtime_status")
    project_status = status.get("project_status")
    active = status.get("active_task") \
        if isinstance(status.get("active_task"), dict) else None
    authorized = status.get("last_authorized_dispatch") \
        if isinstance(status.get("last_authorized_dispatch"), dict) else None
    claimed = status.get("active_task_claimed")
    claim_recorded = status.get("active_task_claim_recorded")
    retired = status.get("active_task_retired")
    pending = status.get("pending_interventions")
    consumed = status.get("last_consumed_message_id")

    auth_source, auth = None, active or authorized
    if isinstance(auth, dict):
        auth_source = "active_task" if auth is active \
            else "last_authorized_dispatch"

    activity = None
    if claimed is True and isinstance(active, dict):
        activity = f"claim observed; executing MESSAGE {active['MESSAGE_ID']}"
    elif claim_recorded is True and isinstance(active, dict):
        activity = f"claim recorded for MESSAGE {active['MESSAGE_ID']}"
    elif _is_int(consumed):
        activity = f"last completion consumed: MESSAGE {consumed}"

    warnings = []
    if status.get("stop") is True:
        warnings.append({"severity": "error", "code": "STOP_FLAG",
                         "message": "A formal STOP flag is present in the "
                                    "Runtime."})
    if status.get("human_review") is True:
        warnings.append({"severity": "warning", "code": "HUMAN_REVIEW",
                         "message": "Human review has been requested."})
    if runtime_status == "ORCHESTRATOR_ERROR":
        warnings.append({"severity": "error", "code": "ORCHESTRATOR_ERROR",
                         "message": "The Orchestrator reported an error "
                                    "condition."})
    if project_status == "BLOCKED":
        warnings.append({"severity": "error", "code": "PROJECT_BLOCKED",
                         "message": "The project reported a blocked stage."})
    if runtime_status == "DEADLINE_REACHED":
        warnings.append({"severity": "warning", "code": "DEADLINE_REACHED",
                         "message": "The scheduled deadline was reached."})
    if retired is True:
        warnings.append({"severity": "warning", "code": "ACTIVE_TASK_RETIRED",
                         "message": "The last authorized task was retired."})
    if _is_int(pending) and pending > 0:
        warnings.append({"severity": "information",
                         "code": "PENDING_INTERVENTIONS",
                         "message": f"{pending} human intervention(s) are "
                                    f"pending."})

    return {
        "project": {"available": _is_str(project_id), "id": project_id},
        "orchestrator": {
            "available": runtime_status is not None,
            "status": runtime_status,
            "label": _RUNTIME_ORCHESTRATOR_LABELS.get(runtime_status)
            if _is_str(runtime_status) else None,
        },
        "codex": {
            "available": project_status is not None,
            "label": _PROJECT_CODEX_LABELS.get(project_status)
            if _is_str(project_status) else None,
        },
        "zcode": {"available": activity is not None,
                  "last_observed_activity": activity,
                  "at": None},
        "current_authorization": {
            "available": isinstance(auth, dict),
            "source": auth_source,
            "message_id": auth.get("MESSAGE_ID") if isinstance(auth, dict) else None,
            "task_id": auth.get("TASK_ID") if isinstance(auth, dict) else None,
            "stage_id": auth.get("STAGE_ID") if isinstance(auth, dict) else None,
            "attempt": auth.get("ATTEMPT") if isinstance(auth, dict) else None,
        },
        "current_claim": {"available": True, "recorded": claim_recorded,
                          "running": claimed, "retired": retired},
        "pending_interventions": {"available": _is_int(pending),
                                  "count": pending},
        "errors_and_warnings": warnings,
    }


def interpret_status(status) -> dict:
    """Interpret one bounded status document into a Cockpit presentation."""
    unknown, malformed, contradiction = _document_problems(status)
    result = {
        "schema_version": COCKPIT_INTERPRETATION_SCHEMA_VERSION,
        "deterministic": True,
        "state": {},
        "next_expected": {},
        "milestones": [],
        "protocol": {"available": False, "message_id": None, "task_id": None,
                     "stage_id": None, "attempt": None},
        "final_verification": {"available": False,
                               "is_final_verification": None},
        "health": {},
        "honesty": {"unknown_fields": unknown, "malformed_fields": malformed,
                    "contradictions": contradiction, "notes": []},
    }
    valid_doc = isinstance(status, dict)
    notes = result["honesty"]["notes"]
    if not valid_doc:
        notes.append("the control-plane status document is not a JSON object")
    elif unknown or malformed or contradiction:
        notes.append("no state is guessed from unknown, malformed, or "
                     "contradictory facts")
    elif status.get("active_task_retired") is True \
            and status.get("project_status") == "WAITING_EXECUTOR":
        notes.append("the authorized task is retired; the Runtime must "
                     "replan before new work can be claimed")

    determine = valid_doc and not (unknown or malformed or contradiction)
    family = _determine_family(status, _pause_view(status)) if determine \
        else FAM_STATE_UNAVAILABLE
    _assemble(result, status if valid_doc else {}, family, determine)
    return result


def _assemble(result: dict, status: dict, family: str,
              determine: bool) -> None:
    pause = _pause_view(status)
    active = status.get("active_task") \
        if isinstance(status.get("active_task"), dict) else None
    completion = status.get("active_task_completion_status") \
        if isinstance(status.get("active_task_completion_status"), str) \
        else None

    result["state"] = {
        "family": family,
        "label": _STATE_LABELS[family],
        "detail": _detail_for(status, family, determine),
        "worker": _worker_for(family, status),
        "since": _since_for(family, pause),
        "user_action_required": False,
        "user_action_reason": None,
    }
    required, reason = _user_action_for(family, status)
    result["state"]["user_action_required"] = required
    result["state"]["user_action_reason"] = reason
    if family == FAM_STATE_UNAVAILABLE and result["honesty"]["notes"]:
        result["state"]["detail"] += " " + " ".join(
            note.capitalize().rstrip(".") + "."
            for note in result["honesty"]["notes"])

    result["next_expected"] = _next_expected_for(family, status)
    result["milestones"] = _milestones_for(
        active, status.get("active_task_claimed"),
        status.get("active_task_claim_recorded"), completion)

    if isinstance(active, dict):
        result["protocol"] = {
            "available": True,
            "message_id": active.get("MESSAGE_ID"),
            "task_id": active.get("TASK_ID"),
            "stage_id": active.get("STAGE_ID"),
            "attempt": active.get("ATTEMPT"),
        }
        fv = active.get(_FV_KEY)
        result["final_verification"] = {"available": fv is not None,
                                        "is_final_verification": bool(fv)}

    health = _health_for(status)
    if not determine:
        reasons = result["honesty"]["unknown_fields"] \
            + result["honesty"]["malformed_fields"] \
            + result["honesty"]["contradictions"]
        message = ("The state presentation is unavailable"
                   + (" because of: " + ", ".join(reasons) if reasons else "")
                   + ".")
        if result["honesty"]["notes"]:
            message += " " + " ".join(result["honesty"]["notes"])
        health["errors_and_warnings"].append({
            "severity": "error", "code": "STATE_UNAVAILABLE",
            "message": message,
        })
    result["health"] = health


def _detail_for(status: dict, family: str, determine: bool) -> str:
    if determine:
        if family == FAM_STOPPED \
                and status.get("runtime_status") == "DEADLINE_REACHED":
            return ("The scheduled deadline was reached, so the Runtime "
                    "stopped instead of continuing.")
        if family == FAM_COMPLETION_COMMITTED:
            return ("The Executor claim produced a committed completion; "
                    "the Supervisor has not consumed it yet.")
        return ""
    detail = ("The reported facts were unknown, malformed, or "
              "contradictory, so no state is presented rather than a guess.")
    return detail


def _determine_family(status: dict, pause: dict) -> str:
    runtime_status = status.get("runtime_status")
    project_status = status.get("project_status")
    pause_status = pause["status"]
    completion = status.get("active_task_completion_status")
    completion = completion if isinstance(completion, str) else None
    active = status.get("active_task") \
        if isinstance(status.get("active_task"), dict) else None

    if status.get("stop") is True or project_status == "STOPPED" \
            or runtime_status in ("STOPPED", "STOPPED_BY_USER"):
        return FAM_STOPPED
    if runtime_status == "DEADLINE_REACHED":
        return FAM_STOPPED
    if status.get("human_review") is True or project_status == "HUMAN_REVIEW" \
            or runtime_status == "HUMAN_REVIEW":
        return FAM_HUMAN_REVIEW
    if runtime_status == "ORCHESTRATOR_ERROR" or project_status == "BLOCKED":
        return FAM_ERROR
    if pause_status == "PAUSED" or project_status == "PAUSED" \
            or runtime_status == "PAUSED":
        return FAM_PAUSED
    if project_status == "COMPLETE":
        return FAM_COMPLETE
    if pause_status == "PENDING_AFTER_CURRENT_STAGE":
        return FAM_PAUSE_REQUESTED
    if active is not None and completion == "COMPLETION_COMMITTED":
        return FAM_COMPLETION_COMMITTED
    if active is not None and completion in ("COMPLETION_CONSUMED",
                                             "COMPLETION_SEALED"):
        return FAM_RESULT_EVALUATION
    if project_status == "SUPERVISOR_TURN":
        if status.get("last_consumed_message_id") is not None:
            return FAM_RESULT_EVALUATION
        return FAM_CODEX_THINKING
    if project_status == "WAITING_EXECUTOR":
        if active is None:
            return FAM_IDLE
        if status.get("active_task_retired") is True:
            return FAM_STATE_UNAVAILABLE
        fv = active.get(_FV_KEY) is True
        if status.get("active_task_claimed") is True:
            return FAM_FINAL_VERIFICATION_EXECUTING if fv \
                else FAM_ZCODE_EXECUTING
        return FAM_FINAL_VERIFICATION_WAITING_CLAIM if fv \
            else FAM_WAITING_FOR_ZCODE_CLAIM
    if project_status is None:
        if status.get("PROJECT_ID") is None:
            return FAM_NO_PROJECT
        return FAM_IDLE
    return FAM_STATE_UNAVAILABLE
