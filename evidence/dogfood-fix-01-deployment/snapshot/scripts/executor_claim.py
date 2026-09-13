from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import socket
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# G5A.5: default root is the Runtime install that owns this claim helper
# (parents[1] = the directory containing scripts\). --root still overrides.
DEFAULT_ROOT = Path(__file__).resolve().parents[1]
EXIT_ACQUIRED = 0
EXIT_CLAIM_EXISTS = 10
EXIT_ALREADY_PROCESSED = 11
EXIT_ERROR = 12
AUTHORIZED_DISPATCH_SCHEMA_VERSION = 1
IDENTITY_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")
JSON_FENCE = re.compile(r"```json\s*\n(.*?)\n```", re.S | re.I)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class LastProcessedFormatError(ValueError):
    """ZCODE_LAST_PROCESSED.txt is not a valid wire-format pointer."""


LAST_PROCESSED_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")


def parse_last_processed_identity(text: str) -> dict:
    """Canonical parser for ZCODE_LAST_PROCESSED.txt (shared by the whole Runtime).

    v2 wire format: exactly the five key=value identity lines (MESSAGE_ID,
    TASK_ID, STAGE_ID, ATTEMPT, NONCE), each exactly once, no unknown keys.
    Legacy format: one bare non-negative integer (MESSAGE_ID only); it remains
    accepted because bootstrap (QUICKSTART) seeds "0" and existing claim
    fixtures use it.

    Returns {"MESSAGE_ID": int} for legacy text and the full identity dict for
    v2 text. Raises LastProcessedFormatError on anything malformed or
    ambiguous; it never returns a partially validated identity.
    """
    if not isinstance(text, str):
        raise LastProcessedFormatError("last-processed payload must be text")
    stripped = text.strip()
    if not stripped:
        raise LastProcessedFormatError("last-processed pointer is empty")
    lines = stripped.splitlines()
    if len(lines) == 1 and re.fullmatch(r"\d+", lines[0].strip()):
        return {"MESSAGE_ID": int(lines[0].strip())}
    identity: dict = {}
    for number, line in enumerate(lines, start=1):
        match = re.fullmatch(r"([A-Z_]+)=(.*)", line)
        if not match:
            raise LastProcessedFormatError(f"line {number} is not KEY=VALUE: {line!r}")
        key, value = match.group(1), match.group(2)
        if key in identity:
            raise LastProcessedFormatError(f"duplicate identity field: {key}")
        identity[key] = value
    missing = [key for key in LAST_PROCESSED_KEYS if key not in identity]
    if missing:
        raise LastProcessedFormatError(f"missing identity fields: {missing}")
    unknown = sorted(set(identity) - set(LAST_PROCESSED_KEYS))
    if unknown:
        raise LastProcessedFormatError(f"unknown identity fields: {unknown}")
    if not re.fullmatch(r"\d+", identity["MESSAGE_ID"]):
        raise LastProcessedFormatError("MESSAGE_ID must be a non-negative integer")
    if not re.fullmatch(r"\d+", identity["ATTEMPT"]) or int(identity["ATTEMPT"]) < 1:
        raise LastProcessedFormatError("ATTEMPT must be an integer >= 1")
    for key in ("TASK_ID", "STAGE_ID", "NONCE"):
        if not identity[key] or identity[key] != identity[key].strip():
            raise LastProcessedFormatError(
                f"{key} must be non-empty without surrounding whitespace"
            )
    identity["MESSAGE_ID"] = int(identity["MESSAGE_ID"])
    identity["ATTEMPT"] = int(identity["ATTEMPT"])
    return identity


def read_last_processed(root: Path) -> dict:
    """Read the last-processed identity via the canonical parser.

    A missing file keeps the bootstrap semantics of "nothing processed yet"
    ({-1}); a malformed file raises LastProcessedFormatError — a corrupted
    pointer must fail closed, never silently read as "nothing processed".
    """
    path = root / "ZCODE_LAST_PROCESSED.txt"
    if not path.exists():
        return {"MESSAGE_ID": -1}
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    return parse_last_processed_identity(text)


def claim_dir(root: Path, message_id: int, nonce: str) -> Path:
    nonce_digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]
    return root / "handoff" / "executor_claims" / f"{message_id}-{nonce_digest}.claim"


def authorization_error(message_id: int, reason: str) -> int:
    print(
        f"CLAIM_NOT_AUTHORIZED message_id={message_id} reason={reason}",
        file=sys.stderr,
    )
    return EXIT_ERROR


def verify_authorized_dispatch(
    root: Path, message_id: int, task_id: str, stage_id: str, attempt: int, nonce: str,
    *, allow_paused_running: bool = False,
    allow_legacy_claimed_recovery: bool = False,
) -> tuple[bool, str]:
    """Bind a claim to Orchestrator state and the exact currently published inbox.

    The runtime authorization is necessary but not sufficient: the public inbox must
    still exist, parse, match the complete identity, and retain the authorized SHA-256.
    This makes both publish-before-authorize and post-authorization inbox damage fail
    closed before the claim directory compare-and-set.
    """
    runtime_path = root / "control" / "orchestrator_runtime.json"
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return False, f"runtime_state_unavailable:{type(exc).__name__}"
    if not isinstance(runtime, dict):
        return False, "runtime_state_not_object"

    try:
        retired = {int(value) for value in (runtime.get("retired_message_ids") or [])}
    except Exception:
        return False, "retired_message_ids_malformed"
    if message_id in retired:
        return False, "message_id_retired"
    if runtime.get("timeout_notified_for_nonce") == nonce:
        return False, "attempt_timed_out"

    authorization = runtime.get("authorized_dispatch")
    if not isinstance(authorization, dict):
        return False, "authorization_missing"
    if authorization.get("schema_version") != AUTHORIZED_DISPATCH_SCHEMA_VERSION:
        return False, "authorization_schema_mismatch"

    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import supervisor_control
    if supervisor_control.uncommitted_intervention_transactions(root):
        return False, "intervention_transaction_recovery_required"
    try:
        control = supervisor_control.load_control(root)
    except Exception as exc:
        return False, f"supervisor_control_unavailable:{type(exc).__name__}"
    pause = str((control.get("pause") or {}).get("status") or "RUNNING")
    if pause != "RUNNING" and not allow_paused_running:
        return False, "runtime_paused"

    requested = {
        "MESSAGE_ID": message_id,
        "TASK_ID": task_id,
        "STAGE_ID": stage_id,
        "ATTEMPT": attempt,
        "NONCE": nonce,
    }

    # v1.2 acquisition invariant: no exact archive + authorization seal means no
    # new claim. A pre-upgrade claim may finish through the separately fenced
    # recovery path, but this verifier never turns it into a new acquisition.
    has_archive = isinstance(authorization.get("SUPERVISOR_DISPATCH_ARCHIVE"), dict)
    if not has_archive:
        # Compatibility is completion-only: an already claimed pre-upgrade task
        # may finish through the separately fenced recovery path, but an
        # archive-less authorization is never a valid new acquisition.
        if not allow_legacy_claimed_recovery:
            return False, "dispatch_archive_binding_missing"
        try:
            legacy_claim = json.loads(
                (claim_dir(root, message_id, nonce) / "claim.json").read_text(
                    encoding="utf-8-sig"))
            if (supervisor_control.normalize_identity(
                    legacy_claim, "legacy claim identity")
                    != supervisor_control.normalize_identity(
                        requested, "requested legacy identity")):
                return False, "legacy_claim_missing_or_invalid"
        except Exception:
            return False, "legacy_claim_missing_or_invalid"
    else:
        archive_ok, archive_reason = supervisor_control.verify_archive_binding(root, authorization)
        if not archive_ok:
            return False, archive_reason
        origin = authorization.get("SUPERVISOR_CONTROL_ORIGIN") or {}
        if (not allow_paused_running
                and origin.get("originating_control_revision") != control.get("revision")):
            return False, "supervisor_control_origin_stale"

    for key in IDENTITY_KEYS:
        if authorization.get(key) != requested[key]:
            return False, f"authorization_{key.lower()}_mismatch"

    inbox = root / "TO_ZCODE.md"
    try:
        raw = inbox.read_bytes()
        expected_hash = str(authorization.get("TO_ZCODE_SHA256") or "")
        if not expected_hash or hashlib.sha256(raw).hexdigest() != expected_hash:
            return False, "inbox_hash_mismatch"
        text = raw.decode("utf-8-sig")
        blocks = JSON_FENCE.findall(text)
        if len(blocks) != 1:
            return False, "inbox_json_fence_count"
        task = json.loads(blocks[0])
    except Exception as exc:
        return False, f"inbox_unavailable_or_malformed:{type(exc).__name__}"
    if not isinstance(task, dict):
        return False, "inbox_payload_not_object"
    for key in IDENTITY_KEYS:
        if task.get(key) != requested[key]:
            return False, f"inbox_{key.lower()}_mismatch"
    return True, "OK"


def acquire(root: Path, message_id: int, task_id: str, stage_id: str, attempt: int, nonce: str) -> int:
    # Even legacy acquisition must serialize with registration: inspecting the
    # version before taking the lock can create a tokenless claim for a new fence.
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import executor_fence as fence
    try:
        with fence.runtime_lock(root):
            return _acquire(root, message_id, task_id, stage_id, attempt, nonce, fence=fence)
    except (fence.FenceError, OSError) as exc:
        return authorization_error(message_id, str(exc))


def _acquire(root: Path, message_id: int, task_id: str, stage_id: str, attempt: int, nonce: str, *, fence) -> int:
    """Caller holds the fence mutex through authorization, metadata and success."""
    root = root.resolve()
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    import supervisor_control
    supervisor_control.reconcile_control_transactions_locked(root)
    try:
        last = read_last_processed(root)["MESSAGE_ID"]
    except LastProcessedFormatError as exc:
        print(
            f"CLAIM_ERROR message_id={message_id} error=malformed ZCODE_LAST_PROCESSED.txt: {exc}",
            file=sys.stderr,
        )
        return EXIT_ERROR
    if message_id <= last:
        print(f"ALREADY_PROCESSED message_id={message_id} last_processed={last}")
        return EXIT_ALREADY_PROCESSED

    authorized, reason = verify_authorized_dispatch(
        root, message_id, task_id, stage_id, attempt, nonce
    )
    if not authorized:
        return authorization_error(message_id, reason)

    token = None
    runtime = fence.completion_module().read_runtime_state(root)
    if runtime is None:
        return authorization_error(message_id, "runtime_state_unavailable")
    auth = runtime.get("authorized_dispatch")
    if isinstance(auth, dict) and "FENCE_VERSION" in auth:
        identity = dict(zip(IDENTITY_KEYS, (message_id, task_id, stage_id, attempt, nonce)))
        fence.check_locked(root, identity, require_claim=False)
        token = secrets.token_urlsafe(32)

    parent = root / "handoff" / "executor_claims"
    path = claim_dir(root, message_id, nonce)

    try:
        parent.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        # FIX-F16: unusable claims directory must yield the documented EXIT_ERROR,
        # not an unhandled traceback (executor still fails closed either way).
        print(f"CLAIM_ERROR message_id={message_id} error={exc!r}", file=sys.stderr)
        return EXIT_ERROR

    try:
        # os.mkdir is the atomic compare-and-set: only one overlapping instance can win.
        os.mkdir(path)
    except FileExistsError:
        print(f"CLAIM_EXISTS message_id={message_id} claim={path}")
        return EXIT_CLAIM_EXISTS
    except Exception as exc:
        print(f"CLAIM_ERROR message_id={message_id} error={exc!r}", file=sys.stderr)
        return EXIT_ERROR

    claim = {
        "CLAIM_PROTOCOL_VERSION": 1,
        "MESSAGE_ID": message_id,
        "TASK_ID": task_id,
        "STAGE_ID": stage_id,
        "ATTEMPT": attempt,
        "NONCE": nonce,
        "CLAIMED_AT": now_iso(),
        "HOSTNAME": socket.gethostname(),
        "SEMANTICS": "permanent at-most-once claim for this MESSAGE_ID/NONCE; do not delete",
    }
    if token is not None:
        claim["CLAIM_TOKEN_SHA256"] = hashlib.sha256(token.encode()).hexdigest()
    try:
        tmp = path / "claim.json.tmp"
        tmp.write_text(json.dumps(claim, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path / "claim.json")
    except Exception as exc:
        # Fail closed: leave the claim directory in place so no duplicate owner can appear.
        print(f"CLAIM_METADATA_ERROR message_id={message_id} claim={path} error={exc!r}", file=sys.stderr)
        return EXIT_ERROR

    suffix = f" claim_token={token}" if token is not None else ""
    print(f"CLAIM_ACQUIRED message_id={message_id} claim={path}{suffix}")
    return EXIT_ACQUIRED


def selftest() -> int:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "ZCODE_LAST_PROCESSED.txt").write_text("700005\n", encoding="utf-8")
        args = dict(root=root, message_id=700006, task_id="T", stage_id="S", attempt=1, nonce="nonce-700006")
        task = {
            "PROTOCOL_VERSION": 2,
            "CLAIM_PROTOCOL_VERSION": 1,
            "MESSAGE_ID": args["message_id"],
            "TASK_ID": args["task_id"],
            "STAGE_ID": args["stage_id"],
            "ATTEMPT": args["attempt"],
            "NONCE": args["nonce"],
            "OBJECTIVE": "selftest",
            "OUTPUTS": [],
        }
        wire = "```json\n" + json.dumps(task, ensure_ascii=False, indent=2) + "\n```\n"
        (root / "TO_ZCODE.md").write_text(wire, encoding="utf-8")
        (root / "control").mkdir()
        raw = (root / "TO_ZCODE.md").read_bytes()
        import supervisor_control
        origin = {"originating_control_revision": 0,
                  "supervisor_turn_id": "selftest-turn-700006",
                  "decision_receipt_sha256": "a" * 64}
        binding = supervisor_control.archive_dispatch(root, None, task, raw, origin=origin)
        authorization = {
            "schema_version": AUTHORIZED_DISPATCH_SCHEMA_VERSION,
            **{key: task[key] for key in IDENTITY_KEYS},
            "TO_ZCODE_SHA256": hashlib.sha256(raw).hexdigest(),
            "AUTHORIZED_AT": now_iso(),
            "PROJECT_ID": None,
            "FENCE_VERSION": 1,
            "EXPIRES_AT": "2099-01-01T00:00:00+00:00",
            "SUPERVISOR_CONTROL_ORIGIN": origin,
            "SUPERVISOR_DISPATCH_ARCHIVE": {
                key: binding[key] for key in ("schema_version", "metadata_file",
                                               "archive_file", "authorization_file",
                                               "dispatch_sha256")
            },
        }
        supervisor_control.seal_dispatch_authorization(root, authorization)
        (root / "control" / "project_state.json").write_text(json.dumps({
            "status": "WAITING_EXECUTOR",
            "current_task": {key: task[key] for key in IDENTITY_KEYS},
        }), encoding="utf-8")
        (root / "control" / "orchestrator_runtime.json").write_text(
            json.dumps({"status": "RUNNING", "authorized_dispatch": authorization,
                        "retired_message_ids": []}),
            encoding="utf-8",
        )
        first = acquire(**args)
        second = acquire(**args)
        stale = acquire(root, 700005, "T0", "S0", 1, "nonce-old")
        if first != EXIT_ACQUIRED or second != EXIT_CLAIM_EXISTS or stale != EXIT_ALREADY_PROCESSED:
            print(f"SELFTEST_FAILED first={first} second={second} stale={stale}", file=sys.stderr)
            return 1
        print("EXECUTOR_CLAIM_SELFTEST: OK")
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Atomic at-most-once Executor claim helper.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    acq = sub.add_parser("acquire")
    acq.add_argument("--root", default=str(DEFAULT_ROOT))
    acq.add_argument("--message-id", type=int, required=True)
    acq.add_argument("--task-id", required=True)
    acq.add_argument("--stage-id", required=True)
    acq.add_argument("--attempt", type=int, required=True)
    acq.add_argument("--nonce", required=True)

    sub.add_parser("selftest")
    ns = parser.parse_args()

    if ns.cmd == "selftest":
        return selftest()
    return acquire(Path(ns.root), ns.message_id, ns.task_id, ns.stage_id, ns.attempt, ns.nonce)


if __name__ == "__main__":
    raise SystemExit(main())
