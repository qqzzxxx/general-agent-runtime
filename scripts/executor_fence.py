"""Runtime-owned post-claim authorization and candidate-file publication.

This is a cooperative filesystem protocol, not an OS sandbox. Executors write
only attempt-local candidates; canonical files are replaced here while holding
the same kernel lock used by dispatch, retirement and completion commit.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
FENCE_VERSION = 1
IDENTITY_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")


class FenceError(RuntimeError):
    pass


@contextmanager
def runtime_lock(root: Path):
    """Cross-process mutex; kernel releases it on crash. Never unlink it."""
    path = Path(root) / "control" / ".executor-fence.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        deadline = time.monotonic() + 10
        while True:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise FenceError("runtime_fence_busy")
                time.sleep(0.02)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def completion_module():
    scripts = str(Path(__file__).resolve().parent)
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import executor_completion
    return executor_completion


def check_locked(root: Path, identity: dict, *, require_claim=True, claim_token=None):
    """Fresh check, never a reusable authorization token. Caller holds mutex."""
    c = completion_module()
    try:
        identity = c._validated_identity(identity, "fence")
        runtime = c.read_runtime_state(root)
        if runtime is None:
            raise FenceError("runtime_state_unavailable")
        c._check_authorization(runtime, identity)
        auth = runtime["authorized_dispatch"]
        if auth.get("FENCE_VERSION") != FENCE_VERSION:
            raise FenceError("fencing_not_registered: legacy attempts require operator recovery")
        pid, project = c.resolve_active_project(root)
        if "PROJECT_ID" not in auth or auth["PROJECT_ID"] != pid:
            raise FenceError("authorized_project_mismatch")
        if (root / "control" / "STOP").exists() or (root / "control" / "HUMAN_REVIEW").exists():
            raise FenceError("runtime_stop_or_human_review")
        if runtime.get("status") in {"STOPPED_BY_USER", "HUMAN_REVIEW", "DEADLINE_REACHED", "ORCHESTRATOR_ERROR"}:
            raise FenceError("runtime_not_running")
        expires = datetime.fromisoformat(auth["EXPIRES_AT"])
        if expires.tzinfo is None or datetime.now(timezone.utc) >= expires:
            raise FenceError("attempt_expired")
        state = c._check_live_lifecycle(root, pid, identity)
        if state.get("deadline_at"):
            deadline = datetime.fromisoformat(state["deadline_at"].replace("Z", "+00:00"))
            if deadline.tzinfo is None or datetime.now(timezone.utc) >= deadline:
                raise FenceError("project_deadline_reached")
        c._check_ledger_absent(root, identity)
        if require_claim:
            claim = c._check_claim(root, identity)
            if (not isinstance(claim_token, str) or not claim_token
                    or not hmac.compare_digest(hashlib.sha256(claim_token.encode()).hexdigest(),
                                               str(claim.get("CLAIM_TOKEN_SHA256") or ""))):
                raise FenceError("claim_owner_token_mismatch")
        import executor_claim
        ok, reason = executor_claim.verify_authorized_dispatch(
            root, *[identity[key] for key in IDENTITY_KEYS])
        if not ok:
            raise FenceError(reason)
        return pid, project
    except (c.CompletionError, KeyError, TypeError, ValueError) as exc:
        raise FenceError(str(exc)) from exc


def attempt_root(project: Path, identity: dict) -> Path:
    digest = hashlib.sha256(identity["NONCE"].encode()).hexdigest()[:24]
    return project / "attempt_workspaces" / f"{identity['MESSAGE_ID']}-{digest}"


def safe_path(base: Path, relative: str) -> Path:
    """Reject traversal, Windows aliases/ADS and symlink/reparse components."""
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise FenceError("use a nonempty forward-slash relative path")
    parts = relative.split("/")
    if any(p in {"", ".", ".."} or p[-1:] in {" ", "."}
           or re.search(r'[<>:"|?*\x00-\x1f]', p)
           or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", p)
           for p in parts):
        raise FenceError("unsafe_path")
    target = base
    # Check the base and its ancestors as well (including junctions on Windows).
    for component in [base, *base.parents]:
        reject_link(component)
    for part in parts:
        target = target / part
        reject_link(target)
    if not target.resolve().is_relative_to(base.resolve()):
        raise FenceError("path_escape")
    return target


def reject_link(path: Path):
    if path.is_symlink():
        raise FenceError("symlink_not_allowed")
    if path.exists():
        st = path.lstat()
        if getattr(st, "st_file_attributes", 0) & 0x400:
            raise FenceError("reparse_point_not_allowed")
        if path.is_file() and st.st_nlink != 1:
            raise FenceError("hardlink_not_allowed")


def output_path(base: Path, relative: str) -> Path:
    target = safe_path(base, relative)
    parts = relative.split("/")
    if not (len(parts) > 1 and parts[0] in {"workspace", "evidence", "reports"}):
        raise FenceError("publication limited to workspace/, evidence/, reports/")
    if relative.casefold() == "reports/user_status.md":
        raise FenceError("runtime_owned_output")
    return target


def check(root: Path, identity: dict, *, claim_token=None) -> Path:
    root = Path(root).resolve()
    with runtime_lock(root):
        _, project = check_locked(root, identity, claim_token=claim_token)
        return attempt_root(project, identity)


def prepare(root: Path, identity: dict, *, claim_token=None) -> Path:
    root = Path(root).resolve()
    with runtime_lock(root):
        _, project = check_locked(root, identity, claim_token=claim_token)
        work = attempt_root(project, identity)
        safe_path(project, work.relative_to(project).as_posix())
        work.mkdir(parents=True, exist_ok=True)
        for name in ("workspace", "evidence", "reports"):
            safe_path(work, name).mkdir(exist_ok=True)
        return work


def publication_record(root: Path, identity: dict, relative: str) -> Path:
    c = completion_module()
    digest = hashlib.sha256(relative.encode()).hexdigest()
    return (root / "handoff" / "executor_publications" /
            c.commit_id_for(identity["MESSAGE_ID"], identity["NONCE"]) / f"{digest}.json")


def publish(root: Path, identity: dict, relative: str, expected_sha256: str, *, claim_token=None) -> Path:
    """Publish one copied snapshot, serialized with retirement; no shared inode."""
    root = Path(root).resolve()
    c = completion_module()
    with runtime_lock(root):
        pid, project = check_locked(root, identity, claim_token=claim_token)
        source = output_path(attempt_root(project, identity), relative)
        target = output_path(project, relative)
        if not source.is_file() or source.stat().st_size > c.EVIDENCE_MAX_FILE_BYTES:
            raise FenceError("candidate_missing_or_too_large")
        data = source.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256) or digest != expected_sha256:
            raise FenceError("candidate_hash_mismatch")
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(target.name + f".{uuid.uuid4().hex}.tmp")
        try:
            with temp.open("xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            # Recheck after snapshot IO: in particular, the deadline may have passed.
            check_locked(root, identity, claim_token=claim_token)
            output_path(project, relative)
            os.replace(temp, target)
            c._atomic_write(publication_record(root, identity, relative), json.dumps({
                **identity, "PROJECT_ID": pid, "path": relative, "sha256": digest,
                "PUBLISHED_AT": c.now_iso(),
            }) + "\n")
        finally:
            temp.unlink(missing_ok=True)
        return target


def validate_publications(root: Path, identity: dict, staging: dict):
    """Fenced completions may reference only canonical outputs published by us."""
    c = completion_module()
    for item in (staging.get("EVIDENCE") or []) + (staging.get("DELIVERABLES") or []):
        record = c._read_json_file(publication_record(root, identity, item["path"]))
        if not isinstance(record, dict) or not c.identity_values_match(record, identity):
            raise FenceError("output_not_runtime_published")
        if (record.get("path") != item["path"] or record.get("sha256") != item["sha256"]
                or record.get("PROJECT_ID") != staging["PROJECT_ID"]):
            raise FenceError("publication_binding_mismatch")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "prepare", "publish"))
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--claim-token", required=True)
    for key in IDENTITY_KEYS:
        parser.add_argument("--" + key.lower().replace("_", "-"), required=True,
                            type=int if key in {"MESSAGE_ID", "ATTEMPT"} else str)
    parser.add_argument("--path")
    parser.add_argument("--sha256")
    args = parser.parse_args()
    identity = {key: getattr(args, key.lower()) for key in IDENTITY_KEYS}
    try:
        if args.command == "publish":
            if not args.path or not args.sha256:
                raise FenceError("publish requires --path and --sha256")
            path = publish(args.root, identity, args.path, args.sha256, claim_token=args.claim_token)
            print(f"PUBLICATION_COMMITTED path={path}")
        else:
            path = globals()[args.command](args.root, identity, claim_token=args.claim_token)
            print(f"ATTEMPT_AUTHORIZED attempt_workspace={path}")
        return 0
    except (FenceError, OSError) as exc:
        print(f"ATTEMPT_FENCED reason={exc}", file=sys.stderr)
        return 12


if __name__ == "__main__":
    raise SystemExit(main())
