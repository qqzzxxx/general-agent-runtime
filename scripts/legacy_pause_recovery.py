"""Canonical legacy pause recovery: LEGACY-PAUSE-RECOVERY-V2.

One bounded, mechanically proven operator recovery for one historical
corruption: a legacy Safe Pause retired a dispatched-but-never-claimed task
(PAUSE_BEFORE_CLAIM) and the ordinary attempt constructor counted that
never-executed dispatch as a consumed scientific retry attempt.

The repair converts the retirement into QUOTA-PAUSE-PARK-V1 semantics, the
same durable representation the live Runtime already uses for every
never-executed pause loss:

* one retry-budget fact: the MESSAGE_ID is appended to the append-only
  `parked_message_ids` record, which `ordinary_dispatch._construct` already
  excludes from attempt accounting (native quota parks and pause-caused
  timeouts park the same way);
* one durable Supervisor event: LEGACY_PARKED_STAGE_RESUME re-plans the same
  logical stage through the ordinary authorization path with an unchanged
  attempt number and an explicit not-a-failure context;
* one auditable proof record: projects/<project>/legacy_pause_recoveries/
  legacy-pause-recovery-<MESSAGE_ID>.json with the full mechanical proof and
  the Runtime state hashes before/after the commit.

Fail-closed: every proof must hold mechanically; any unknown or conflicting
fact aborts without writing. The proof is the union of the checks the two
pre-canonicalization tools each carried, in the stronger form of the two.
The retired identity is never revived: retirement history, the old
authorization, its NONCE/EXPIRES_AT and the quarantine stay immutable; the
next ordinary dispatch still allocates a fresh MESSAGE_ID and NONCE through
the ordinary authorization path. The tool never touches live research data
and never fabricates completions.

Phases:
* prove -- dry-run; prints the full mechanical proof report and writes nothing;
* apply -- re-proves under the Runtime fence, then commits the parked-id
  record, the durable pending Supervisor event and the proof record.

Exactly-once and convergence: the durable facts (parked membership plus the
owned pending event) are the authority, so a re-run on an already-recovered
identity is a no-op (ALREADY_APPLIED), including on trees converted by the
pre-canonicalization tool that parked the same way. The proof record is
created with create-only semantics; an existing record for the same identity
must match the current proof or apply refuses.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_TOOL_RUNTIME_ROOT = DEFAULT_ROOT
EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_CONFLICT = 3
EXIT_STALE_OR_DUPLICATE = 4
EXIT_INTERNAL = 5

SCHEMA_VERSION = 2
RECOVERY_NAME = "LEGACY-PAUSE-RECOVERY-V2"
EVENT_TYPE = "LEGACY_PARKED_STAGE_RESUME"
PARKED_KEY = "parked_message_ids"
PROOF_DIR = "legacy_pause_recoveries"
LEGACY_RECORD_DIR = Path("handoff") / "recovery_records"
# Only a legacy safe pause that closed an unclaimed dispatch may ever be
# exempted. EXECUTOR_TIMEOUT, SUPERSEDED and intervention retirements stay
# exactly where the existing accounting put them.
LEGACY_PAUSE_REASONS = {"PAUSE_BEFORE_CLAIM"}

EVENT_NOTE = (
    "A legacy safe pause retired this dispatch before any Executor claim, so "
    "it never executed. This is an infrastructure/user-quota pause, not a "
    "method failure, executor retry, or scientific failure; do not count it "
    "as a failed attempt. The retired MESSAGE_ID stays retired and is never "
    "revived; the Runtime allocates a fresh identity for the same logical "
    "stage without consuming the retry budget. Unpublished candidate "
    "evidence named in this event may be read-only verified and reused only "
    "after checking its hashes and outputs; it is not accepted evidence "
    "until re-submitted and independently reviewed, and publication and "
    "completion must use the fresh dispatch identity."
)


class RecoveryError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def _expired(moment) -> bool:
    """True only when the moment is a valid, zoned timestamp in the past."""
    try:
        parsed = datetime.fromisoformat(str(moment).replace("Z", "+00:00"))
        return bool(parsed.tzinfo) and datetime.now(timezone.utc) >= parsed
    except (TypeError, ValueError, AttributeError):
        return False


_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import executor_claim
import executor_completion as completion
import executor_fence
import ordinary_dispatch as od
import supervisor_control as sc


def bind_runtime_paths(m, root: Path) -> None:
    """Use the active Runtime's own helpers while preserving project isolation."""
    root = Path(root).resolve()
    m.ROOT = root
    m.CONTROL = root / "control"
    m.LOGS = root / "logs"
    m.HANDOFF_ARCHIVE = root / "handoff" / "archive"
    m.REPORTS = root / "reports"
    m.RUNTIME_STATE = m.CONTROL / "orchestrator_runtime.json"
    m.TO_ZCODE = root / "TO_ZCODE.md"
    m.SUPERVISOR_BRIEF = root / "SUPERVISOR_BRIEF.md"
    m.ZCODE_DONE = root / "ZCODE_DONE.flag"
    m.ZCODE_LAST_PROCESSED = root / "ZCODE_LAST_PROCESSED.txt"
    m.ACTIVE_PROJECT_FILE = m.CONTROL / "ACTIVE_PROJECT.json"
    m.ACTIVE_PROJECT = None


def load_runtime_module(runtime_root: Path, target_root: Path):
    """Load the tool's own Runtime code and bind its paths to the target root.

    The recovery semantics travel with the tool's own tree; only data paths
    point at the target Runtime being recovered.
    """
    orchestrator = Path(runtime_root) / "orchestrator.py"
    if not orchestrator.is_file():
        raise RecoveryError(EXIT_INTERNAL, f"runtime orchestrator.py not found: {orchestrator}")
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        f"legacy_pause_recovery_runtime_{uuid.uuid4().hex}", orchestrator)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind_runtime_paths(module, target_root)
    return module


def _identity_of(record: dict) -> dict:
    return {key: record.get(key) for key in sc.IDENTITY_KEYS}


def _identity_matches(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in sc.IDENTITY_KEYS)


class _Proof:
    def __init__(self):
        self.checks: list[dict] = []

    def add(self, check: str, passed: bool, detail: str) -> bool:
        self.checks.append({"check": check, "passed": bool(passed), "detail": str(detail)})
        return bool(passed)

    @property
    def ok(self) -> bool:
        return all(item["passed"] for item in self.checks)


def _read_runtime(m) -> dict:
    runtime = sc._read_json(m.RUNTIME_STATE, {}) or {}
    if not isinstance(runtime, dict):
        raise RecoveryError(EXIT_CONFLICT, "orchestrator_runtime.json is not an object")
    return runtime


def _project_layout(m) -> tuple[str, Path, Path, dict]:
    try:
        project_id, project_root, state_path = sc.resolve_active_project(Path(m.ROOT))
    except Exception as exc:
        raise RecoveryError(EXIT_CONFLICT, f"active project is unavailable: {exc}") from exc
    state = sc._read_json(state_path)
    if not isinstance(state, dict):
        raise RecoveryError(EXIT_CONFLICT, "active project_state.json is not an object")
    return project_id, project_root, state_path, state


def _scheduler_owner_alive(runtime: dict):
    owner = runtime.get("scheduler_owner") or {}
    if not isinstance(owner, dict) or not owner.get("pid"):
        return None
    import runtime_lifecycle
    return runtime_lifecycle.process_alive(int(owner["pid"]))


def _retirement_entry(runtime: dict, message_id: int) -> tuple[dict | None, bool]:
    """The unique retirement record for the target, plus its uniqueness."""
    entries = [
        entry for entry in (runtime.get("executor_retirements") or [])
        if isinstance(entry, dict) and entry.get("MESSAGE_ID") == message_id
    ]
    return (entries[0] if entries else None), len(entries) == 1


def _quarantine_scan(root: Path, message_id: int, expected_hash: str | None):
    """Scan handoff/quarantine for the dispatch's paused inbox copy.

    Returns (names, ambiguous): names lists exact-hash matches; ambiguous is
    true when any copy exists that does not match the authorized hash.
    """
    names: list[str] = []
    ambiguous = False
    quarantine_root = root / "handoff" / "quarantine"
    if not quarantine_root.is_dir():
        return names, ambiguous
    try:
        paths = list(quarantine_root.glob(f"to-zcode-{message_id}-*"))
    except OSError:
        return names, True
    for path in paths:
        try:
            digest = sc.sha256_bytes(path.read_bytes())
        except OSError:
            ambiguous = True
            continue
        if expected_hash is not None and digest == expected_hash:
            names.append(path.name)
        else:
            ambiguous = True
    return names, ambiguous


def _predicted_successor_attempt(root: Path, project_id: str, task_id: str,
                                 parked: set[int]) -> int:
    """Mirror ordinary_dispatch._construct attempt accounting, read-only."""
    attempt = 0
    for record in sc.list_dispatches(root, project_id):
        if record.get("integrity") != "AUTHORIZED_VALID":
            continue
        if record.get("PROJECT_ID") not in (None, project_id):
            continue
        task = sc._parse_dispatch_bytes((root / record["archive_file"]).read_bytes())
        if task.get("TASK_ID") != task_id:
            continue
        if record["MESSAGE_ID"] in parked:
            continue
        attempt = max(attempt, int(task.get("ATTEMPT") or 0))
    return attempt + 1


def _same_logical_archives(root: Path, project_id: str, task_id: str) -> list[dict]:
    same = []
    for record in sc.list_dispatches(root, project_id):
        if record.get("integrity") != "AUTHORIZED_VALID":
            continue
        task = sc._parse_dispatch_bytes((root / record["archive_file"]).read_bytes())
        if task.get("TASK_ID") == task_id:
            same.append({"MESSAGE_ID": record["MESSAGE_ID"],
                         "ATTEMPT": task.get("ATTEMPT"),
                         "LOGICAL_TASK": task.get("LOGICAL_TASK"),
                         "LOGICAL_STAGE": task.get("LOGICAL_STAGE")})
    return same


def _previous_logical_attempts(m, runtime: dict, project_id: str, message_id: int,
                               logical_task: str, logical_stage: str) -> list[dict]:
    """Archived same-logical-stage attempts below the target, oldest first."""
    root = Path(m.ROOT)
    workspaces = root / "projects" / project_id / "attempt_workspaces"
    previous = []
    for record in sc.list_dispatches(root, project_id):
        if record.get("integrity") != "AUTHORIZED_VALID":
            continue
        prior_id = record.get("MESSAGE_ID")
        if not isinstance(prior_id, int) or prior_id >= message_id:
            continue
        try:
            task = sc._parse_dispatch_bytes((root / record["archive_file"]).read_bytes())
        except Exception:
            continue
        if task.get("LOGICAL_TASK") != logical_task \
                or task.get("LOGICAL_STAGE") != logical_stage:
            continue
        entry, _ = _retirement_entry(runtime, prior_id)
        matches = list(workspaces.glob(f"{prior_id}-*")) if workspaces.is_dir() else []
        previous.append({
            "MESSAGE_ID": prior_id, "ATTEMPT": task.get("ATTEMPT"),
            "REASON": (entry or {}).get("REASON"),
            "attempt_workspace": matches[0].name if matches else None,
        })
    return sorted(previous, key=lambda item: item["MESSAGE_ID"])


def _completion_hits(root: Path, project_root: Path, message_id: int) -> list[str]:
    """Every completion-shaped artifact bound to the target, if any exists."""
    hits: list[str] = []
    staging_root = project_root / "completion_staging"
    if staging_root.is_dir():
        for path in staging_root.iterdir():
            if path.name.startswith(f"{message_id}-"):
                hits.append(f"completion_staging/{path.name}")
                continue
            document = sc._read_json(path / "staging.json", default=None) \
                if (path / "staging.json").is_file() else None
            if isinstance(document, dict) and document.get("MESSAGE_ID") == message_id:
                hits.append(f"completion_staging/{path.name}")
    ledger = completion.ledger_dir(Path(root))
    if ledger.is_dir():
        hits.extend(f"completion_ledger/{path.name}"
                    for path in ledger.glob(f"completion-{message_id}-*.json"))
        staged = ledger / "staged"
        if staged.is_dir():
            hits.extend(f"completion_ledger/staged/{path.name}"
                        for path in staged.glob(f"completion-{message_id}-*"))
    for name in ("executor_finishes", "executor_publications"):
        directory = root / "handoff" / name
        if directory.is_dir():
            hits.extend(f"{name}/{path.name}"
                        for path in directory.glob(f"completion-{message_id}-*"))
    return hits


def _ledger_quiescent(root: Path) -> tuple[bool, str | None]:
    """The authoritative completion ledger must hold only consumed/sealed
    history, so no other live Executor output is in flight."""
    ledger = completion.ledger_dir(Path(root))
    if not ledger.is_dir():
        return True, None
    staged = ledger / "staged"
    if staged.is_dir():
        for path in staged.iterdir():
            match = re.match(r"^completion-(\d+)-", path.name)
            if not match:
                return False, f"malformed staged entry: {path.name}"
            sealed = [
                item for item in completion.lookup_entries(Path(root), int(match.group(1)))
                if item.get("STATUS") in (completion.STATUS_CONSUMED,
                                          completion.STATUS_SEALED)
            ]
            if not sealed:
                return False, f"unsealed staged completion remains: {path.name}"
    for path in ledger.glob("completion-*.json"):
        item = completion.load_entry_file(path)
        if item is None:
            continue
        if item.get("STATUS") not in (completion.STATUS_CONSUMED,
                                      completion.STATUS_SEALED):
            return False, f"unconsumed authoritative completion remains: {path.name}"
    return True, None


def _consumed_chain_consistent(m, proof: _Proof, claims_root: Path) -> tuple[bool, int | None]:
    """The consumed chain below the retired dispatch must still verify."""
    runtime = _read_runtime(m)
    consumed = runtime.get("last_consumed_message_id")
    consumed_nonce = runtime.get("last_consumed_nonce")
    chain_ok = (type(consumed) is int and isinstance(consumed_nonce, str)
                and bool(consumed_nonce))
    chain_issue = None if chain_ok else \
        f"last consumed chain facts are missing (consumed={consumed!r})"
    pointer = None
    if chain_ok:
        if not m.ZCODE_LAST_PROCESSED.is_file():
            chain_ok, chain_issue = False, "ZCODE_LAST_PROCESSED.txt is missing"
        else:
            try:
                pointer = executor_claim.parse_last_processed_identity(
                    m.ZCODE_LAST_PROCESSED.read_text(encoding="utf-8-sig", errors="replace"))
            except ValueError as exc:
                chain_ok, chain_issue = False, f"ZCODE_LAST_PROCESSED.txt is malformed: {exc}"
    if chain_ok:
        if pointer.get("MESSAGE_ID") != consumed:
            chain_ok, chain_issue = False, (
                f"processed pointer MESSAGE_ID {pointer.get('MESSAGE_ID')!r} does not "
                f"match last_consumed_message_id {consumed}")
        elif "NONCE" in pointer and pointer.get("NONCE") != consumed_nonce:
            chain_ok, chain_issue = False, \
                "processed pointer NONCE does not match last_consumed_nonce"
    if chain_ok:
        brief_hash = runtime.get("last_consumed_brief_sha256")
        if isinstance(brief_hash, str) and re.fullmatch(r"[0-9a-f]{64}", brief_hash):
            pattern = f"brief-{consumed}-*-consumed-{brief_hash[:12]}.md"
            valid = [path for path in m.HANDOFF_ARCHIVE.glob(pattern)
                     if sc.sha256_bytes(path.read_bytes()) == brief_hash]
            if len(valid) != 1:
                chain_ok, chain_issue = False, (
                    "the consumed receipt is not backed by exactly one "
                    "hash-matching consumed archive")
    if chain_ok:
        required_from = runtime.get("claim_protocol_required_from_message_id")
        bound = (isinstance(required_from, int) and not isinstance(required_from, bool)
                 and consumed >= required_from)
        matches = list(claims_root.glob(f"{consumed}-*.claim")) \
            if claims_root.is_dir() else []
        if bound and len(matches) != 1:
            chain_ok, chain_issue = False, (
                "the consumed identity is not backed by exactly one matching "
                "at-most-once claim")
        for path in matches:
            claim = sc._read_json(path / "claim.json", default=None) \
                if (path / "claim.json").is_file() else None
            if isinstance(claim, dict) and (
                    claim.get("NONCE") != consumed_nonce
                    or (bound and claim.get("MESSAGE_ID") != consumed)):
                chain_ok, chain_issue = False, (
                    "the consumed identity claim diverges from the consumed "
                    f"authorization: {path.name}")
                break
    proof.add("consumed_chain_consistent", chain_ok,
              f"consumed chain verifies at MESSAGE_ID {consumed}" if chain_ok
              else chain_issue)
    return chain_ok, consumed


def build_proof(root: Path, message_id: int) -> dict:
    """Run every mechanical check; collect all evidence; write nothing."""
    root = Path(root).resolve()
    proof = _Proof()
    if type(message_id) is not int or message_id < 0:
        raise RecoveryError(EXIT_VALIDATION, "--message-id must be a non-negative integer")
    m = load_runtime_module(_TOOL_RUNTIME_ROOT, root)
    runtime = _read_runtime(m)
    project_id, project_root, _state_path, state = _project_layout(m)

    # 1. Tool/Runtime compatibility, control-plane quiescence and liveness:
    #    recovery runs only while the whole Runtime is paused with no live
    #    scheduler, so no scheduler turn or claim can interleave.
    proof.add("tool_supports_parked_exemption", hasattr(od, "_parked_message_ids"),
              "ordinary_dispatch carries the QUOTA-PAUSE-PARK-V1 parked-id exemption")
    for flag in ("STOP", "HUMAN_REVIEW"):
        proof.add(f"flag_{flag.lower()}_absent", not (root / "control" / flag).exists(),
                  f"control/{flag} is absent")
    alive = _scheduler_owner_alive(runtime)
    proof.add("runtime_not_running", alive is not True,
              "no live scheduler owner holds the Runtime" if alive is not True
              else f"scheduler owner pid {runtime.get('scheduler_owner', {}).get('pid')} "
                   f"is alive; stop the Runtime before recovery")
    control = sc.load_control(root)
    pause = control.get("pause") or {}
    paused = (runtime.get("status") == "PAUSED" and pause.get("status") == "PAUSED")
    proof.add("runtime_paused", paused,
              f"runtime.status={runtime.get('status')!r}, pause.status={pause.get('status')!r}")

    # 2. The target must be the retired dispatch tip: the last dispatched
    #    identity, with exactly one identity-bound legacy safe-pause retirement
    #    record, no successor, and the authorization still bound to it.
    dispatched = runtime.get("last_dispatched_message_id")
    authorization = runtime.get("authorized_dispatch") or {}
    retired_list = runtime.get("retired_message_ids")
    retired_ok = (isinstance(retired_list, list)
                  and all(type(value) is int for value in retired_list)
                  and message_id in retired_list)
    proof.add("target_retired", retired_ok,
              f"retired_message_ids contains {message_id}" if retired_ok
              else "retired_message_ids is malformed or does not contain the target")
    entry, unique = _retirement_entry(runtime, message_id)
    proof.add("retirement_record_unique", unique,
              "exactly one retirement record binds the target MESSAGE_ID" if unique
              else f"{len([e for e in runtime.get('executor_retirements') or [] if isinstance(e, dict) and e.get('MESSAGE_ID') == message_id])} "
                   f"retirement records mention MESSAGE_ID={message_id}")
    identity = _identity_of(entry or {})
    entry_ok = (
        entry is not None
        and entry.get("REASON") in LEGACY_PAUSE_REASONS
        and entry.get("SUPERSEDED_BY") is None
    )
    proof.add("legacy_retirement_record", entry_ok,
              "the retirement record is a legacy safe-pause retirement with no "
              f"successor ({sorted(LEGACY_PAUSE_REASONS)})" if entry_ok else
              "the retirement record is missing, not a legacy safe-pause "
              "retirement, or superseded")
    authorized_ok = (isinstance(authorization, dict) and bool(authorization)
                     and _identity_matches(authorization, identity))
    proof.add("authorization_matches_retired_identity", authorized_ok,
              "authorized_dispatch binds the retired identity" if authorized_ok else
              "authorized_dispatch does not bind the retired identity")
    nonce = runtime.get("last_dispatched_nonce")
    tip_ok = (dispatched == message_id and nonce == identity.get("NONCE"))
    proof.add("tip_is_target", tip_ok,
              f"last_dispatched_message_id={dispatched!r} with the retired nonce"
              if tip_ok else
              f"last_dispatched_message_id={dispatched!r} does not bind the "
              f"retired tip identity {message_id}")

    # 3. The pause transaction receipt must bind exactly this retirement, and
    #    its quarantined inbox copy must hash to the authorized dispatch bytes.
    receipt = pause.get("transaction_receipt")
    receipt_ok = (isinstance(receipt, dict)
                  and isinstance(receipt.get("subject_identity"), dict)
                  and _identity_matches(receipt.get("subject_identity"), identity)
                  and receipt.get("action") == "RETIRE"
                  and receipt.get("retirement_reason") in LEGACY_PAUSE_REASONS)
    proof.add("pause_receipt_binds_target", receipt_ok,
              "the pause transaction receipt binds this dispatch to the legacy "
              "safe-pause retirement" if receipt_ok else
              "the pause transaction receipt is missing or does not bind "
              f"MESSAGE_ID={message_id} to a legacy safe-pause RETIRE: "
              f"{json.dumps(receipt, ensure_ascii=False)[:400] if receipt is not None else 'missing'}")
    expected_hash = authorization.get("TO_ZCODE_SHA256") if authorized_ok else None
    hash_ok = isinstance(expected_hash, str) \
        and re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None
    quarantine_rel = receipt.get("quarantine") if isinstance(receipt, dict) else None
    quarantine_copy_ok = False
    if receipt_ok and hash_ok and isinstance(quarantine_rel, str) and quarantine_rel:
        quarantine_path = root / quarantine_rel
        if quarantine_path.is_file():
            try:
                quarantine_copy_ok = \
                    sc.sha256_bytes(quarantine_path.read_bytes()) == expected_hash
            except OSError:
                quarantine_copy_ok = False
    proof.add("quarantine_copy_hash_matches_inbox_authorization", quarantine_copy_ok,
              "the pause quarantine copy hashes to the authorized dispatch bytes"
              if quarantine_copy_ok else
              f"the receipt quarantine copy is missing or hash-divergent: {quarantine_rel!r}")

    # 4. The authorization archive must be intact under the canonical validator.
    archive_ok = False
    if authorized_ok and hash_ok:
        try:
            bound, detail = sc.verify_archive_binding(root, authorization)
        except Exception as exc:  # noqa: BLE001 - any validator crash is counter-evidence
            bound, detail = False, f"validator_error:{type(exc).__name__}"
        archive_ok = bool(bound)
        archive_detail = detail
    else:
        archive_detail = "no intact authorization to validate"
    proof.add("authorization_archive_intact", archive_ok,
              "the canonical archive validator binds the authorization, its "
              "metadata and its exact dispatch bytes" if archive_ok else
              f"the authorization archive failed validation: {archive_detail}")

    # 5. The claim-side fencing must refuse the old identity right now. This is
    #    both its own proof and one leg of the mechanical window closure below:
    #    PICKUP-EXECUTION-LIFECYCLE bounds a never-claimed dispatch with a
    #    bounded pickup authorization that dies on durable facts, not only on
    #    wall-clock.
    wake_reason = None
    wake_ok = False
    try:
        wake_ok, wake_reason = executor_claim.verify_authorized_dispatch(
            root, message_id, identity.get("TASK_ID"), identity.get("STAGE_ID"),
            identity.get("ATTEMPT"), identity.get("NONCE"))
    except Exception as exc:  # noqa: BLE001 - any validator crash is counter-evidence
        wake_reason = f"validator_error:{type(exc).__name__}"
    proof.add("wake_validation_refuses_old_identity", wake_ok is False,
              "claim-side validation refuses the retired identity "
              f"({wake_reason})" if wake_ok is False else
              "claim-side validation unexpectedly allows the retired identity")

    # 6. The authorization window must provably be closed: expired in the
    #    authoritative record, or mechanically dead as an acquisition — the
    #    identity durably retired, no live inbox, and claim-side validation
    #    refusing a wake. For a never-claimed dispatch a wall-clock wait past
    #    EXPIRES_AT adds no safety beyond those durable facts (a stale wake
    #    needs the live inbox hash and a non-retired identity), so recovery
    #    advances on them instead of waiting for natural expiry.
    window_evidence = []
    mechanically_dead = (retired_ok and not m.TO_ZCODE.exists()
                         and wake_ok is False)
    if authorized_ok and hash_ok:
        expires_at = authorization.get("EXPIRES_AT")
        if _expired(expires_at):
            window_evidence.append(f"authorization expired at {expires_at}")
        if mechanically_dead:
            window_evidence.append(
                "identity durably retired with no live inbox and claim-side "
                f"validation refusing a wake ({wake_reason})")
        quarantine_names, ambiguous = _quarantine_scan(root, message_id, expected_hash)
        if ambiguous:
            window_evidence = []
            proof.add("authorization_window_closed", False,
                      "quarantine evidence for the dispatch is ambiguous or "
                      "hash-divergent")
        elif window_evidence:
            proof.add("authorization_window_closed", True, "; ".join(window_evidence))
        elif quarantine_names:
            proof.add("authorization_window_closed", True,
                      "dispatch quarantined with the exact authorized hash: "
                      + ", ".join(quarantine_names))
        else:
            proof.add("authorization_window_closed", False,
                      "the authorization is neither expired, nor provably "
                      "quarantined, nor mechanically dead as an acquisition; "
                      "a stale worker wake cannot be ruled out")
    else:
        proof.add("authorization_window_closed", False,
                  "no intact authorization to close")

    # 6. No live inbox and no raw completion hint.
    inbox_absent = not m.TO_ZCODE.exists() and not m.ZCODE_DONE.exists()
    proof.add("no_live_inbox", inbox_absent,
              "no live TO_ZCODE.md and no raw ZCODE_DONE.flag" if inbox_absent
              else "a live TO_ZCODE.md or raw ZCODE_DONE.flag is present")

    # 7. No Executor claim at or beyond the target: the target itself was never
    #    claimed, and nothing newer exists that could own live work.
    claims_root = root / "handoff" / "executor_claims"
    claim_issue = None
    if claims_root.is_dir():
        for path in claims_root.iterdir():
            match = re.match(r"^(\d+)-", path.name)
            if not match:
                claim_issue = f"malformed Executor claim entry: {path.name}"
                break
            claim_id = int(match.group(1))
            if claim_id > message_id:
                claim_issue = (f"claim MESSAGE_ID {claim_id} exists beyond the "
                               f"recovery target {message_id}")
                break
            if claim_id == message_id:
                claim_issue = f"the target dispatch already has an Executor claim: {path.name}"
                break
    else:
        claim_issue = "handoff/executor_claims is missing"
    proof.add("no_claim_for_target", claim_issue is None,
              "no Executor claim exists at or beyond the target" if claim_issue is None
              else claim_issue)

    # 8. No attempt workspace for the target.
    workspaces = project_root / "attempt_workspaces"
    workspace_hits = sorted(path.name for path in workspaces.glob(f"{message_id}-*")) \
        if workspaces.is_dir() else []
    proof.add("no_attempt_workspace_for_target", not workspace_hits,
              "no attempt workspace exists for the target" if not workspace_hits
              else f"attempt workspace exists for the target: {workspace_hits}")

    # 9. No completion staging, authoritative ledger entry, finish hint or
    #    publication bound to the target; and the whole ledger is quiescent.
    completion_hits = _completion_hits(root, project_root, message_id)
    proof.add("no_completion_for_target", not completion_hits,
              "no completion staging, ledger entry, finish or publication exists "
              "for the target" if not completion_hits else
              f"completion artifacts exist for the target: {completion_hits}")
    quiet, ledger_issue = _ledger_quiescent(root)
    proof.add("ledger_quiescent", quiet,
              "every authoritative completion entry is consumed or sealed"
              if quiet else ledger_issue)

    # 10. The consumed chain below the retired dispatch must still verify.
    _consumed_chain_consistent(m, proof, claims_root)
    consumed = _read_runtime(m).get("last_consumed_message_id")
    proof.add("consumed_below_target", type(consumed) is int and consumed < message_id,
              f"last_consumed_message_id={consumed!r} is below the target"
              if type(consumed) is int and consumed < message_id else
              f"last_consumed_message_id={consumed!r} is not below the target")

    # 11. Project binding: the recovery target belongs to the active project and
    #     the project is exactly in the post-retirement Supervisor-turn shape.
    binding_ok = (
        state.get("status") == "SUPERVISOR_TURN"
        and state.get("current_task") is None
        and state.get("project_id") == project_id
        and authorization.get("PROJECT_ID") == project_id
        and state.get("next_message_id") == message_id + 1
    )
    proof.add("project_binding", binding_ok,
              "active project, SUPERVISOR_TURN state and next_message_id all bind "
              "the retired tip" if binding_ok else
              f"project binding mismatch: status={state.get('status')!r}, "
              f"current_task={'set' if state.get('current_task') is not None else 'null'}, "
              f"next_message_id={state.get('next_message_id')!r}, "
              f"project={project_id!r}")

    # 12. The dispatch origin must be in the past relative to the current
    #     control revision, so the current control plane never owned this
    #     dispatch's authorization.
    origin = (authorization.get("SUPERVISOR_CONTROL_ORIGIN") or {}).get(
        "originating_control_revision") if isinstance(authorization, dict) else None
    revision = control.get("revision")
    revision_ok = (type(origin) is int and type(revision) is int and revision > origin)
    proof.add("control_revision_moved_past_dispatch_origin", revision_ok,
              f"control revision {revision} is past the dispatch origin {origin}"
              if revision_ok else
              f"control revision {revision!r} has not moved past the dispatch "
              f"origin {origin!r}")

    # 13. The archived logical binding must reproduce the retired identity's
    #     TASK_ID/STAGE_ID labels, and the logical stage history is consistent.
    logical = {"LOGICAL_TASK": None, "LOGICAL_STAGE": None,
               "ATTEMPT": None, "MAX_RETRIES": None}
    if archive_ok:
        try:
            archive_meta = authorization.get("SUPERVISOR_DISPATCH_ARCHIVE") or {}
            task = sc._parse_dispatch_bytes(
                (root / archive_meta["archive_file"]).read_bytes())
            for key in logical:
                logical[key] = task.get(key)
        except Exception:
            pass
    reproduction_ok = False
    if archive_ok and logical["LOGICAL_TASK"] and logical["LOGICAL_STAGE"] \
            and isinstance(entry, dict):
        expected_task = "task-" + sc.sha256_bytes(sc.canonical_json_bytes(
            [project_id, logical["LOGICAL_TASK"]]))[:24]
        expected_stage = "stage-" + sc.sha256_bytes(sc.canonical_json_bytes(
            [project_id, [logical["LOGICAL_TASK"], logical["LOGICAL_STAGE"]]]))[:24]
        reproduction_ok = (expected_task == entry.get("TASK_ID")
                           and expected_stage == entry.get("STAGE_ID"))
    proof.add("logical_pair_reproduces_identity", reproduction_ok,
              "the archived logical task/stage reproduce the retired TASK_ID and "
              "STAGE_ID" if reproduction_ok else
              "the archived logical binding does not reproduce the retired "
              "identity labels")

    history = _same_logical_archives(root, project_id, identity.get("TASK_ID")) \
        if reproduction_ok else []
    history_ok = len({(item["LOGICAL_TASK"], item["LOGICAL_STAGE"]) for item in history}) <= 1
    proof.add("logical_stage_history_consistent", history_ok,
              f"all {len(history)} dispatches of TASK_ID bind one logical stage"
              if history_ok else f"logical stage history diverges: {history}")

    # 14. (The claim-side wake refusal is proven at step 5 and reused for the
    #      mechanical window closure.)

    # 15. The successor attempt prediction under the exemption must equal the
    #     retired attempt, mirroring ordinary_dispatch._construct.
    parked_values = runtime.get(PARKED_KEY)
    parked_valid = parked_values is None or (
        isinstance(parked_values, list)
        and all(type(value) is int and value >= 0 for value in parked_values))
    proof.add("parked_message_ids_well_formed", parked_valid,
              f"{PARKED_KEY}={parked_values!r}")
    predicted = None
    attempt_ok = False
    if parked_valid and reproduction_ok and type(entry.get("ATTEMPT")) is int:
        parked_set = set(parked_values or [])
        predicted = _predicted_successor_attempt(root, project_id,
                                                 identity.get("TASK_ID"),
                                                 parked_set | {message_id})
        attempt_ok = predicted == int(entry["ATTEMPT"])
    proof.add("successor_attempt_equals_retired_attempt_under_exemption", attempt_ok,
              f"the successor dispatch would keep ATTEMPT {predicted}"
              if attempt_ok else
              f"predicted successor attempt {predicted!r} does not equal the "
              f"retired attempt {identity.get('ATTEMPT')!r} under the exemption")

    # 16. The durable event slot must be free, or this recovery must already be
    #     applied consistently (exactly-once; also converges with trees the
    #     pre-canonicalization tool recovered into the same parked record).
    pending = runtime.get("pending_supervisor_event")
    foreign = (isinstance(pending, dict)
               and pending.get("reason") != EVENT_TYPE)
    already_parked = parked_valid and isinstance(parked_values, list) \
        and message_id in parked_values
    canonical_record = _proof_record_path(root, project_root, message_id)
    legacy_record = root / LEGACY_RECORD_DIR / f"pause-before-claim-{message_id}.json"
    record_present = canonical_record.is_file() or legacy_record.is_file()
    if already_parked:
        proof.add("recovery_already_applied_consistent", not foreign,
                  "the target is already parked and no foreign durable Supervisor "
                  "event is pending" if not foreign else
                  f"a different durable Supervisor event is pending: "
                  f"{pending.get('reason')!r}")
    else:
        proof.add("no_owned_supervisor_event", not isinstance(pending, dict),
                  "no durable Supervisor event is pending"
                  if not isinstance(pending, dict) else
                  f"a durable Supervisor event is already owned: "
                  f"{pending.get('reason')!r}")
        proof.add("recovery_record_absent", not record_present,
                  "no recovery proof record exists for the target yet"
                  if not record_present else
                  f"a recovery proof record already exists: {canonical_record.name}")

    # Facts for the audit record and the Supervisor event payload.
    expired_at = authorization.get("EXPIRES_AT") if isinstance(authorization, dict) else None
    facts = {
        "project_id": project_id,
        "identity": identity if entry is not None else None,
        "retirement": {
            "REASON": entry.get("REASON"),
            "RETIRED_AT": entry.get("RETIRED_AT"),
            "SUPERSEDED_BY": entry.get("SUPERSEDED_BY"),
        } if entry else None,
        "authorization_expired_at": expired_at,
        "quarantine": _quarantine_scan(root, message_id, expected_hash)[0]
            if hash_ok else [],
        "logical_binding": logical,
    }
    if logical["LOGICAL_TASK"] and logical["LOGICAL_STAGE"]:
        facts["previous_logical_attempts"] = _previous_logical_attempts(
            m, runtime, project_id, message_id,
            logical["LOGICAL_TASK"], logical["LOGICAL_STAGE"])
    else:
        facts["previous_logical_attempts"] = []

    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "legacy_pause_recovery.py",
        "recovery": RECOVERY_NAME,
        "message_id": message_id,
        "ok": proof.ok,
        "checks": proof.checks,
        "facts": facts,
    }


def _proof_record_path(root: Path, project_root: Path, message_id: int) -> Path:
    return project_root / PROOF_DIR / f"legacy-pause-recovery-{message_id}.json"


def _build_event(proof_report: dict, record_rel: str, record_sha: str,
                 expired: bool) -> dict:
    identity = proof_report["facts"]["identity"]
    previous = proof_report["facts"].get("previous_logical_attempts") or []
    prior = previous[-1] if previous else None
    event = {
        "type": EVENT_TYPE,
        **identity,
        "logical_task": proof_report["facts"]["logical_binding"]["LOGICAL_TASK"],
        "logical_stage": proof_report["facts"]["logical_binding"]["LOGICAL_STAGE"],
        "authorization_expired": bool(expired),
        "retirement": proof_report["facts"]["retirement"],
        "recovery_record": record_rel,
        "recovery_record_sha256": record_sha,
        "note": EVENT_NOTE,
    }
    if prior is not None:
        event["previous_attempt_evidence"] = {
            "MESSAGE_ID": prior["MESSAGE_ID"],
            "ATTEMPT": prior["ATTEMPT"],
            "retirement_reason": prior["REASON"],
            "attempt_workspace": prior["attempt_workspace"],
        }
    return event


def _failed_checks(report: dict) -> list[str]:
    return [f"{item['check']}: {item['detail']}"
            for item in report["checks"] if not item["passed"]]


def _record_consistent(existing: dict, proof_report: dict) -> bool:
    """An existing proof record must describe exactly this identity."""
    identity = proof_report["facts"]["identity"]
    record_identity = (existing.get("proof") or {}).get("facts", {}).get("identity")
    return (isinstance(existing, dict)
            and existing.get("message_id") == proof_report["message_id"]
            and existing.get("proof", {}).get("ok") is True
            and record_identity == identity)


def apply_recovery(root: Path, message_id: int) -> dict:
    """Prove under the fence, then commit parked id + durable event atomically."""
    root = Path(root).resolve()
    with executor_fence.runtime_lock(root):
        proof_report = build_proof(root, message_id)
        if not proof_report["ok"]:
            raise RecoveryError(
                EXIT_CONFLICT,
                "legacy pause recovery refused: " + "; ".join(_failed_checks(proof_report)))
        m = load_runtime_module(_TOOL_RUNTIME_ROOT, root)
        runtime = _read_runtime(m)
        _, project_root, _, _ = _project_layout(m)
        parked = list(runtime.get(PARKED_KEY) or [])
        record_path = _proof_record_path(root, project_root, message_id)
        if message_id in parked:
            # Exactly-once: the parked record is durable, so the event was
            # armed with it and is never re-armed after being serviced.
            return {
                "event": "LEGACY_PAUSE_RECOVERY_ALREADY_RECOVERED",
                "message_id": message_id,
                "status": "ALREADY_RECOVERED",
                "proof_record": str(record_path),
                "changed": False,
            }
        expired = _expired(proof_report["facts"].get("authorization_expired_at"))
        record_sha = None
        if record_path.exists():
            existing = sc._read_json(record_path, default=None)
            if not _record_consistent(existing, proof_report):
                raise RecoveryError(
                    EXIT_STALE_OR_DUPLICATE,
                    "a different recovery proof record already exists for this "
                    f"MESSAGE_ID: {record_path}")
            record_sha = sc.sha256_bytes(record_path.read_bytes())
        else:
            record = {
                "schema_version": SCHEMA_VERSION,
                "tool": "legacy_pause_recovery.py",
                "recovery": RECOVERY_NAME,
                "message_id": message_id,
                "created_at": sc.now_iso(),
                "proof": proof_report,
                "supervisor_context": {
                    "not_executed": True,
                    "not_scientific_failure": True,
                    "prior_candidates_reusable_as_inputs_only": (
                        "unpublished attempt-workspace candidates of this logical "
                        "stage may be verified and reused as inputs; they are not "
                        "accepted evidence until re-submitted and independently "
                        "reviewed"),
                },
            }
            record_bytes = json.dumps(record, ensure_ascii=False, indent=2,
                                      sort_keys=True).encode("utf-8")
            record_sha = sc.sha256_bytes(record_bytes)
            record_path.parent.mkdir(parents=True, exist_ok=True)
            sc._atomic_write_bytes(record_path, record_bytes, create_only=True)
        event = _build_event(proof_report, record_path.relative_to(root).as_posix(),
                             record_sha, expired)
        retirement_history = json.dumps(runtime.get("executor_retirements"), sort_keys=True)
        retired_list = json.dumps(runtime.get("retired_message_ids"), sort_keys=True)
        fresh = _read_runtime(m)
        if fresh.get("pending_supervisor_event") is not None:
            raise RecoveryError(EXIT_CONFLICT,
                                "a durable Supervisor event appeared during recovery")
        if message_id not in (fresh.get(PARKED_KEY) or []):
            fresh[PARKED_KEY] = [*(fresh.get(PARKED_KEY) or []), message_id]
        fresh["pending_supervisor_event"] = {
            "reason": EVENT_TYPE, "event": event,
            "recorded_at": sc.now_iso(),
            "decision_attempts": 0, "retry_exhausted": False,
            "pause_recovery": {
                "schema_version": SCHEMA_VERSION,
                "tool": "scripts/legacy_pause_recovery.py",
                "recovery": RECOVERY_NAME,
                "recovery_record": record_path.relative_to(root).as_posix(),
                "recovery_record_sha256": record_sha,
            },
        }
        sc._atomic_json(m.RUNTIME_STATE, fresh)
        verified = _read_runtime(m)
        if (message_id not in (verified.get(PARKED_KEY) or [])
                or not isinstance(verified.get("pending_supervisor_event"), dict)
                or json.dumps(verified.get("executor_retirements"), sort_keys=True)
                != retirement_history
                or json.dumps(verified.get("retired_message_ids"), sort_keys=True)
                != retired_list):
            raise RecoveryError(EXIT_INTERNAL, "recovery read-back did not commit")
        return {
            "event": "LEGACY_PAUSE_BUDGET_RECOVERED",
            "message_id": message_id,
            "status": "RECOVERED",
            "supervisor_event": EVENT_TYPE,
            "parked_message_ids": verified[PARKED_KEY],
            "proof_record": str(record_path),
            "recovery_record_sha256": record_sha,
            "changed": True,
        }


def _print_report(report: dict) -> None:
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"LEGACY_PAUSE_RECOVERY_PROOF: "
          f"{'PASS' if report['ok'] else 'FAIL'} message_id={report['message_id']}")
    for item in report["checks"]:
        mark = "PASS" if item["passed"] else "FAIL"
        print(f"  [{mark}] {item['check']}: {item['detail']}")
    if report["ok"]:
        binding = report["facts"]["logical_binding"]
        print(f"  logical work: {binding['LOGICAL_TASK']} / {binding['LOGICAL_STAGE']} "
              f"attempt {binding['ATTEMPT']} stays attempt "
              f"{binding['ATTEMPT']} after recovery; the next dispatch keeps this "
              f"logical attempt number with a fresh identity")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove or apply a legacy safe-pause retry-budget recovery "
                    "(converts the retirement to QUOTA-PAUSE-PARK-V1 semantics).")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("prove", "apply"):
        command = sub.add_parser(name)
        command.add_argument("--message-id", type=int, required=True,
                             help="retired MESSAGE_ID to recover (the dispatch tip)")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        if args.command == "prove":
            report = build_proof(root, args.message_id)
            _print_report(report)
            return EXIT_OK if report["ok"] else EXIT_VALIDATION
        result = apply_recovery(root, args.message_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"LEGACY_PAUSE_RECOVERY_APPLIED: {result['status']} "
              f"message_id={args.message_id}")
        return EXIT_OK
    except RecoveryError as exc:
        print(f"LEGACY_PAUSE_RECOVERY_FAILED: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001
        print(f"LEGACY_PAUSE_RECOVERY_FAILED: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
