"""Runtime-owned ordinary candidate construction. Call only under the fence lock.

Preparation is a reservation, never authorization. The existing decision receipt,
dispatch archive/seal and register_dispatched_task remain the authority boundary.
"""
from __future__ import annotations

import copy
import json
import secrets
from pathlib import Path

import supervisor_control as sc

SCHEMA_VERSION = 1
MAX_TIME_SECONDS = 2700
MAX_RETRIES = 2
SCHEDULER_GRACE_SECONDS = 3600
REQUIRED = {"logical_task", "logical_stage", "objective", "inputs", "outputs",
            "acceptance_criteria"}
OPTIONAL = {"forbidden_actions", "stop_conditions", "max_time", "max_retries", "execution"}

SUPERVISOR_CONTRACT = """## Task dispatch

For an ordinary stage, author only `ordinary_task_proposal` in project_state.json:
required keys: logical_task, logical_stage, objective (non-empty strings), inputs,
outputs, acceptance_criteria (flat string arrays; outputs and criteria non-empty).
Optional: forbidden_actions and stop_conditions (flat string arrays), max_time
(integer seconds, 1..2700; default 2700), max_retries (integer 0..2; default 2).
For Executor V2, optional execution selects autonomy (LOW/NORMAL/HIGH; default
NORMAL), capabilities, and project-relative read_paths (files or subtrees; default
workspace, evidence, reports). HIGH encourages relevant exploration, inspection
and quality iteration within the same outcome; it never expands permissions.
Filesystem modes: none/read/workspace (default workspace). shell, network, browser
and gui currently support only none: there is no enforceable Runtime adapter for
them. Do not promise their use. Set read_paths explicitly for other task inputs;
Runtime authority files are excluded. Specify observable acceptance criteria and
relevant input context; keep process instructions in task semantics, not protocol.
Use stable logical_task/logical_stage names for the same work: repeating that pair
is a retry, bounded by the original retry limit. Change the names only for a
substantively different task/stage. Runtime assigns the attempt number.

Persist one semantic decision in last_supervisor_decision and append exactly one
decision_history entry, with reason and goal_alignment. Set WAITING_EXECUTOR and
update project memory as needed. Leave current_task and next_message_id unchanged;
omit message_id/task_id/stage_id from the new decision. Runtime fills those mirrors.
Never author ordinary physical IDs, nonce, issue time, wire versions, model fields,
EXECUTOR_PROTOCOL, or TO_ZCODE.md. Unknown proposal keys are rejected.

Runtime validates the proposal and reserves one candidate per control turn. It
constructs the canonical protocol, task projection and counter. A visible candidate
is unclaimable until the existing decision receipt, exact dispatch archive and
registration authorize it at the current control revision. Never edit reservations,
claims, archives or authorization. Restart reuses the reservation; a new Executor
retry receives a fresh MESSAGE_ID/NONCE. Models choose work; Runtime represents it.

Final Verification and the read-only Human Decision transaction retain their
existing full-wire contracts; see control/EXECUTOR_TASK_TEMPLATE.md when selecting
Final Verification. Do not put FV requests/gates in ordinary_task_proposal.
Remove any leftover ordinary_task_proposal when choosing a terminal or FV decision.

"""


def supervisor_rules(text: str) -> str:
    """Replace only the legacy dispatch section in the existing delivered prefix."""
    start = text.find("## Task dispatch\n")
    end = text.find("## Authoritative completion", start)
    if start < 0 or end < 0:
        return text + "\n" + SUPERVISOR_CONTRACT
    return text[:start] + SUPERVISOR_CONTRACT + text[end:]


def validate_proposal(value: dict) -> dict:
    if not isinstance(value, dict) or not REQUIRED <= value.keys() or value.keys() - REQUIRED - OPTIONAL:
        raise sc.CandidateValidationError("ordinary proposal has missing or unknown keys")
    result = copy.deepcopy(value)
    if "execution" in result:
        import executor_contract
        try:
            result["execution"] = executor_contract.validate_execution(result["execution"])
        except ValueError as exc:
            raise sc.CandidateValidationError(str(exc)) from exc
    for key in ("logical_task", "logical_stage", "objective"):
        item = result[key]
        limit = 120 if key.startswith("logical_") else 16000
        if (not isinstance(item, str) or not item.strip() or len(item) > limit
                or (key.startswith("logical_") and (item != item.strip() or any(ord(c) < 32 for c in item)))):
            raise sc.CandidateValidationError(f"ordinary proposal {key} is invalid")
    for key in ("inputs", "outputs", "acceptance_criteria", "forbidden_actions", "stop_conditions"):
        items = result.setdefault(key, [])
        if (not isinstance(items, list) or len(items) > 100
                or any(not isinstance(s, str) or not s.strip() or len(s) > 16000 for s in items)
                or (key in {"outputs", "acceptance_criteria"} and not items)):
            raise sc.CandidateValidationError(f"ordinary proposal {key} must be a flat string array")
    for key, default, minimum in (("max_time", MAX_TIME_SECONDS, 1), ("max_retries", MAX_RETRIES, 0)):
        item = result.setdefault(key, default)
        if type(item) is not int or not minimum <= item <= default:
            raise sc.CandidateValidationError(f"ordinary proposal {key} must be an integer {minimum}..{default}")
    return result


def preparation_path(root: Path, turn: dict) -> Path:
    return root / "control" / "ordinary_dispatch_preparations" / f"{turn['turn_id']}.json"


def _history(root: Path) -> tuple[list[int], list[dict]]:
    """Read Runtime records across projects; corruption never frees an identity."""
    ids, tasks = [], []
    archives = []
    for directory in sc.archive_root(root).glob("*"):
        if directory.is_dir():
            archives.extend(sc.list_dispatches(root, None if directory.name == "_legacy" else directory.name))
    for record in archives:
        if record.get("integrity") not in {"AUTHORIZED_VALID", "UNAUTHORIZED"}:
            raise sc.CandidateValidationError("ordinary allocation blocked by corrupt dispatch archive")
        ids.append(record["MESSAGE_ID"])
        if record.get("integrity") == "AUTHORIZED_VALID":
            tasks.append({"project_id": record.get("PROJECT_ID"),
                          "task": sc._parse_dispatch_bytes((root / record["archive_file"]).read_bytes())})
    for path in (root / "control" / "ordinary_dispatch_preparations").glob("*.json"):
        record = sc._read_json(path)
        ids.append(sc.normalize_identity(record["task"])["MESSAGE_ID"])
    for path in (root / "handoff" / "executor_claims").glob("*/claim.json"):
        ids.append(sc.normalize_identity(sc._read_json(path))["MESSAGE_ID"])
    runtime = sc._read_json(root / "control" / "orchestrator_runtime.json", {}) or {}
    ids.extend(runtime.get("retired_message_ids") or [])
    for key in ("last_consumed_message_id", "last_dispatched_message_id"):
        ids.append(runtime.get(key) or 0)
    if runtime.get("authorized_dispatch"):
        ids.append(sc.normalize_identity(runtime["authorized_dispatch"])["MESSAGE_ID"])
    pointer = root / "ZCODE_LAST_PROCESSED.txt"
    if pointer.exists():
        import executor_claim
        ids.append(executor_claim.parse_last_processed_identity(pointer.read_text(encoding="utf-8-sig"))["MESSAGE_ID"])
    if any(type(n) is not int or n < 0 for n in ids):
        raise sc.CandidateValidationError("ordinary allocation history contains invalid MESSAGE_ID")
    return ids, tasks


def _construct(root: Path, turn: dict, proposal: dict) -> dict:
    ids, history = _history(root)
    floor = turn["dispatch_projection_before"]["next_message_id"]
    if type(floor) is not int or floor < 0:
        raise sc.CandidateValidationError("ordinary next_message_id baseline is invalid")
    previous = [r["task"] for r in history if r["project_id"] == turn["PROJECT_ID"]
                and r["task"].get("LOGICAL_TASK") == proposal["logical_task"]
                and r["task"].get("LOGICAL_STAGE") == proposal["logical_stage"]]
    attempt = max((t["ATTEMPT"] for t in previous), default=0) + 1
    retries = min([proposal["max_retries"], *[t["MAX_RETRIES"] for t in previous]])
    if attempt > retries + 1:
        raise sc.CandidateValidationError("ordinary logical stage retry budget exhausted")
    def label(kind, value):
        return kind + "-" + sc.sha256_bytes(sc.canonical_json_bytes([turn["PROJECT_ID"], value]))[:24]
    task = {"PROTOCOL_VERSION": 2, "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": max([floor, *[n + 1 for n in ids]]),
            "TASK_ID": label("task", proposal["logical_task"]),
            "STAGE_ID": label("stage", [proposal["logical_task"], proposal["logical_stage"]]),
            "ATTEMPT": attempt, "NONCE": secrets.token_hex(24), "ISSUED_AT": sc.now_iso(),
            "EXECUTOR_MODEL_FAMILY": "GLM-5.3", "SUPERVISOR_REVIEW_EFFORT": "high",
            "SCHEDULER_GRACE_SECONDS": SCHEDULER_GRACE_SECONDS,
            "MAX_TIME": proposal["max_time"], "MAX_RETRIES": retries}
    for key in ("logical_task", "logical_stage", "objective", "inputs", "outputs",
                "acceptance_criteria", "forbidden_actions", "stop_conditions"):
        task[key.upper()] = proposal[key]
    task["FORBIDDEN_ACTIONS"] = list(dict.fromkeys([*BASE_RESTRICTIONS, *task["FORBIDDEN_ACTIONS"]]))
    task["EXECUTOR_PROTOCOL"] = list(EXECUTOR_PROTOCOL)
    if "execution" in proposal:
        task["EXECUTION"] = copy.deepcopy(proposal["execution"])
    return task


def prepare_locked(root: Path, turn: dict, state: dict, state_path: Path) -> dict:
    """Reserve exact output before either projection write; replay only exact states."""
    if sc.resolve_active_project(root)[0] != turn["PROJECT_ID"]:
        raise sc.CandidateValidationError("ordinary proposal active project changed")
    path = preparation_path(root, turn)
    record = sc._read_json(path)
    current_hash = sc.sha256_bytes(sc.canonical_json_bytes(state))
    if record is None:
        proposal = validate_proposal(state.get("ordinary_task_proposal"))
        if state.get("status") != "WAITING_EXECUTOR":
            raise sc.CandidateValidationError("ordinary proposal requires WAITING_EXECUTOR")
        if state["last_supervisor_decision"].get("decision") not in {"CONTINUE", "REVISE", "REDIRECT", "CHANGE_METHOD"}:
            raise sc.CandidateValidationError("ordinary proposal requires an ordinary semantic decision")
        before = turn.get("dispatch_projection_before")
        if not isinstance(before, dict) or any(state.get(k) != before[k] for k in before):
            raise sc.CandidateValidationError("ordinary current_task/next_message_id are Runtime-owned")
        for decision in (state["last_supervisor_decision"], state["decision_history"][-1]):
            if any(k in decision for k in ("message_id", "task_id", "stage_id", *sc.IDENTITY_KEYS)):
                raise sc.CandidateValidationError("ordinary decision must omit physical identity")
        inbox = root / "TO_ZCODE.md"
        inbox_hash = sc.sha256_bytes(inbox.read_bytes()) if inbox.exists() else None
        if inbox_hash != turn.get("dispatch_inbox_sha256_before"):
            raise sc.CandidateValidationError("ordinary inbox is Runtime-owned")
        task = _construct(root, turn, proposal)
        after = copy.deepcopy(state)
        after.pop("ordinary_task_proposal")
        after["current_task"] = {key: task[key] for key in (
            *sc.IDENTITY_KEYS, "ISSUED_AT", "MAX_TIME", "MAX_RETRIES", "SCHEDULER_GRACE_SECONDS",
            "SUPERVISOR_REVIEW_EFFORT", "OBJECTIVE", "LOGICAL_TASK", "LOGICAL_STAGE")}
        after["next_message_id"] = task["MESSAGE_ID"] + 1
        for decision in (after["last_supervisor_decision"], after["decision_history"][-1]):
            decision.update({key.lower(): task[key] for key in ("MESSAGE_ID", "TASK_ID", "STAGE_ID")})
        record = {"schema_version": SCHEMA_VERSION, "turn_id": turn["turn_id"],
                  "PROJECT_ID": turn["PROJECT_ID"], "revision": turn["revision"],
                  "state_sha256_before": current_hash, "state_after": after,
                  "state_sha256_after": sc.sha256_bytes(sc.canonical_json_bytes(after)), "task": task,
                  "task_sha256": sc.sha256_bytes(sc.canonical_json_bytes(task)),
                  "inbox_sha256_before": inbox_hash}
        sc._atomic_write_bytes(path, sc.canonical_json_bytes(record), create_only=True)
        sc._failure_point("ordinary_after_reservation")
        # Use the durable key order on first construction as well as recovery;
        # authority binds exact bytes, not merely equivalent JSON objects.
        record = sc._read_json(path)
    if (record.get("schema_version") != SCHEMA_VERSION or record.get("turn_id") != turn["turn_id"]
            or record.get("PROJECT_ID") != turn["PROJECT_ID"] or record.get("revision") != turn["revision"]
            or current_hash not in {record["state_sha256_before"], record["state_sha256_after"]}
            or sc.sha256_bytes(sc.canonical_json_bytes(record["state_after"])) != record["state_sha256_after"]
            or sc.sha256_bytes(sc.canonical_json_bytes(record["task"])) != record["task_sha256"]):
        raise sc.CandidateValidationError("ordinary reservation state/turn binding mismatch")
    after, task = record["state_after"], record["task"]
    data = (f"MESSAGE_ID: {task['MESSAGE_ID']}\nTASK_ID: {task['TASK_ID']}\nSTAGE_ID: {task['STAGE_ID']}\n\n"
            + "```json\n" + json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n").encode("utf-8")
    # Receipt reconstruction is read-only, especially after a newer revision.
    if sc.decision_receipt_path(root, turn["turn_id"]).exists():
        if state != after:
            raise sc.CandidateValidationError("ordinary committed projection changed")
        return state
    inbox = root / "TO_ZCODE.md"
    live_hash = sc.sha256_bytes(inbox.read_bytes()) if inbox.exists() else None
    if live_hash not in {record["inbox_sha256_before"], sc.sha256_bytes(data)}:
        raise sc.CandidateValidationError("ordinary reservation inbox changed")
    # State first; recovery recognizes both sides and restores the exact wire.
    sc._atomic_json(state_path, after)
    sc._failure_point("ordinary_after_state")
    sc._atomic_write_bytes(root / "TO_ZCODE.md", data)
    sc._failure_point("ordinary_after_inbox")
    return after


# Canonical compatibility wire, copied verbatim from the v1.3 task template.
# Phase 2 entry projects these known repetitions out of the Executor task view;
# the immutable wire and its validator remain compatible with older clients.
BASE_RESTRICTIONS = (
    'Do not bypass authentication, CAPTCHA, access control, or anti-automation restrictions.',
    'Do not invent missing data or credentials.',
    'Do not choose the next stage.',
)
EXECUTOR_PROTOCOL = (
    'Work only inside the active Runtime Root and the active Project Root supplied by the Supervisor.',
    'Before any stage work, run: python scripts/executor_claim.py acquire --message-id <MESSAGE_ID> --task-id <TASK_ID> --stage-id <STAGE_ID> --attempt <ATTEMPT> --nonce <NONCE>.',
    'Proceed only on CLAIM_ACQUIRED / exit code 0. On CLAIM_EXISTS / exit code 10 or ALREADY_PROCESSED / exit code 11, stop this run immediately and silently: do not touch deliverables, build completion staging, call the completion helper, or create any root completion artifact.',
    'On any other claim-helper failure, fail closed and stop without touching stage outputs; watchdog/Supervisor owns recovery.',
    'Never delete the claim. A retry must use a fresh MESSAGE_ID and NONCE.',
    'Complete the whole stage internally before returning to the Supervisor.',
    'Retain the claim_token returned only to the successful claim owner. It is required by fencing and completion helpers; never put it in deliverables or receipts.',
    'Run python scripts/executor_fence.py prepare with the original five identity arguments and --claim-token <token>. All stage writes and subprocess output belong in its returned attempt_workspace; canonical files are read-only inputs.',
    'Run python scripts/executor_fence.py check with the original identity and token after claim, on every resume, before each work batch or mutation-capable command, and before publication/completion. Only ATTEMPT_AUTHORIZED / exit 0 permits candidate work; otherwise stop. Never switch to a newer inbox identity.',
    'Publish candidates only with python scripts/executor_fence.py publish using the original identity/token, --path <project-relative path> and --sha256 <candidate hash>. Supported outputs are workspace/, evidence/, reports/ files (excluding reports/USER_STATUS.md), at most 64 MiB each. Only PUBLICATION_COMMITTED / exit 0 means canonical publication; otherwise stop.',
    'Never directly write canonical outputs, RESEARCH_STATE.md, or publication records. Suggest memory updates in the receipt. See docs/STALE_WORKER_FENCING.md.',
    "Build a completion staging directory under the active Project Root's completion_staging\\ folder: one staging.json (COMPLETION_STAGING_SCHEMA_VERSION 1) containing the exact MESSAGE_ID, TASK_ID, STAGE_ID, ATTEMPT, NONCE, PROJECT_ID, STATUS=STAGING_READY, a timezone-aware CREATED_AT, and the full receipt payload as RECEIPT (identity fields must match the staging identity exactly).",
    'Then run: python scripts/executor_completion.py commit --staging-dir "<staging dir>" --claim-token "<token>".',
    'Proceed only on COMPLETION_COMMITTED / exit code 0: the Runtime then generates SUPERVISOR_BRIEF.md, ZCODE_LAST_PROCESSED.txt, and ZCODE_DONE.flag itself. Stop immediately; never create, edit, repair, or republish those root files.',
    'On completion-helper exit codes 10/11/12/13/14 (ALREADY_COMMITTED / COMPLETION_SEALED / COMPLETION_NOT_AUTHORIZED / COMPLETION_CLAIM_MISMATCH / INVALID_COMPLETION_STAGING), fail closed and stop without publishing anything.',
    'Stop after the stage; never create the next TO_ZCODE task.',
)
