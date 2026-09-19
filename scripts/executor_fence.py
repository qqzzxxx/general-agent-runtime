"""Runtime-owned post-claim authorization and candidate-file publication.

This is a cooperative filesystem protocol, not an OS sandbox. Executors write
only attempt-local candidates; canonical files are replaced here while holding
the same kernel lock used by dispatch, retirement and completion commit.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import sys
import threading
import time
import uuid

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
FENCE_VERSION = 1
IDENTITY_KEYS = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")


class FenceError(RuntimeError):
    pass


_LOCK_STATE = threading.local()


@contextmanager
def runtime_lock(root: Path, *, name: str = ".executor-fence.lock"):
    """Cross-process mutex; kernel releases it on crash. Never unlink it."""
    root = Path(root).resolve()
    if name not in {".executor-fence.lock", ".resume-lifecycle.lock"}:
        raise FenceError("unknown Runtime mutex")
    key = (str(root).casefold() if os.name == "nt" else str(root)) + "/" + name
    held = getattr(_LOCK_STATE, "held", set())
    if key in held:
        yield
        return
    path = root / "control" / name
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
            held.add(key)
            _LOCK_STATE.held = held
            yield
        finally:
            held.discard(key)
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


def token_cli_args(argv, option):
    """Opaque URL-safe tokens may begin with '-'; keep them argparse values."""
    args = list(sys.argv[1:] if argv is None else argv)
    index = 0
    while index < len(args) - 1:
        if args[index] == option:
            args[index:index + 2] = [option + "=" + args[index + 1]]
        index += 1
    return args


def _authorization_deadline(auth: dict, claim, state) -> datetime:
    """PICKUP-EXECUTION-LIFECYCLE: the clock this attempt is bounded by.

    An unclaimed dispatch is bounded by its pickup authorization (EXPIRES_AT).
    A claimed attempt is bounded by its execution budget, CLAIMED_AT (durable
    in claim.json) plus MAX_TIME (recorded on the authorization; the live task
    mirror is the legacy fallback). An authorization without any execution-
    clock fact keeps the legacy combined window, where EXPIRES_AT bounded both
    waiting and execution.
    """
    expires = datetime.fromisoformat(str(auth["EXPIRES_AT"]))
    claimed_at = None
    if isinstance(claim, dict) and claim.get("CLAIMED_AT"):
        try:
            parsed = datetime.fromisoformat(
                str(claim["CLAIMED_AT"]).replace("Z", "+00:00"))
            claimed_at = parsed if parsed.tzinfo is not None else None
        except (TypeError, ValueError):
            claimed_at = None
    max_seconds = auth.get("MAX_TIME")
    if claimed_at is not None and type(max_seconds) is int and max_seconds > 0:
        return claimed_at + timedelta(seconds=max_seconds)
    if claimed_at is not None and isinstance(state, dict):
        mirror = state.get("current_task")
        if isinstance(mirror, dict) and type(mirror.get("MAX_TIME")) is int \
                and mirror["MAX_TIME"] > 0:
            return claimed_at + timedelta(seconds=mirror["MAX_TIME"])
    if expires.tzinfo is None:
        raise FenceError("attempt_expired")
    return expires


def check_locked(root: Path, identity: dict, *, require_claim=True, claim_token=None):
    """Fresh check, never a reusable authorization token. Caller holds mutex."""
    c = completion_module()
    try:
        scripts = str(Path(__file__).resolve().parent)
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import supervisor_control
        supervisor_control.reconcile_control_transactions_locked(Path(root).resolve())
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
        state = c._check_live_lifecycle(root, pid, identity)
        claim, _ = c.load_claim(root, identity)
        if datetime.now(timezone.utc) >= _authorization_deadline(auth, claim, state):
            raise FenceError("attempt_expired")
        if state.get("deadline_at"):
            deadline = datetime.fromisoformat(state["deadline_at"].replace("Z", "+00:00"))
            if deadline.tzinfo is None or datetime.now(timezone.utc) >= deadline:
                raise FenceError("project_deadline_reached")
        c._check_ledger_absent(root, identity)
        if require_claim:
            check_claim_owner(root, identity, claim_token)
        import executor_claim
        ok, reason = executor_claim.verify_authorized_dispatch(
            root, *[identity[key] for key in IDENTITY_KEYS],
            allow_paused_running=require_claim,
            allow_legacy_claimed_recovery=require_claim,
        )
        if not ok:
            raise FenceError(reason)
        return pid, project
    except (c.CompletionError, supervisor_control.ControlError,
            KeyError, TypeError, ValueError) as exc:
        raise FenceError(str(exc)) from exc


def check_claim_owner(root: Path, identity: dict, claim_token):
    """Prove retained ownership only; this never grants live publication authority."""
    claim = completion_module()._check_claim(root, identity)
    if (not isinstance(claim_token, str) or not 1 <= len(claim_token) <= 256
            or not hmac.compare_digest(hashlib.sha256(claim_token.encode()).hexdigest(),
                                       str(claim.get("CLAIM_TOKEN_SHA256") or ""))):
        raise FenceError("claim_owner_token_mismatch")


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


def publication_policy(root: Path, identity: dict, relative: str) -> bool:
    """Check sealed task restrictions under the Runtime lock; return FV read-only.

    Applies at the publication boundary, including low-level compatibility calls.
    Host tool access is never a grant to publish Runtime files or repair FV inputs.
    """
    c = completion_module()
    auth = c.read_runtime_state(root)["authorized_dispatch"]
    import final_verification_contract as fv
    if "SUPERVISOR_DISPATCH_ARCHIVE" not in auth:
        return False  # Existing fenced legacy recovery has no sealed execution policy.
    task = fv.archived_task(root, auth)
    if task.get("EXECUTION", {}).get("capabilities", {}).get("filesystem", "workspace") != "workspace":
        raise FenceError("publication requires workspace capability")
    is_read_only = (task.get("FINAL_VERIFICATION_GATE") or {}).get("EXECUTION_MODE") == "LIVE_READ_ONLY"
    if is_read_only:
        name = attempt_root(Path("."), identity).name
        if not any(relative.startswith(f"{area}/verification/{name}/") for area in ("evidence", "reports")):
            raise FenceError("FV publication is limited to new attempt-specific verification evidence/reports")
    return is_read_only


def publish(root: Path, identity: dict, relative: str, expected_sha256: str, *, claim_token=None) -> Path:
    """Publish one copied snapshot, serialized with retirement; no shared inode."""
    root = Path(root).resolve()
    c = completion_module()
    with runtime_lock(root):
        pid, project = check_locked(root, identity, claim_token=claim_token)
        source = output_path(attempt_root(project, identity), relative)
        target = output_path(project, relative)
        read_only = publication_policy(root, identity, relative)
        if read_only and target.exists():
            raise FenceError("FV cannot replace an existing canonical file")
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
            publication_policy(root, identity, relative)
            if read_only:
                # Atomic creation: a target appearing during IO cannot be replaced.
                os.link(temp, target)
                temp.unlink()
            else:
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



# Completion-bound publication provenance (additive v1 manifest).
MAX_PUBLICATIONS = 128
MANIFEST_KEYS = {"schema_version", "COMMIT_ID", "PROJECT_ID", "publications",
                 *IDENTITY_KEYS}
RECORD_KEYS = {"PROJECT_ID", "path", "sha256", "PUBLISHED_AT",
               *IDENTITY_KEYS}


def validate_record(record, entry):
    c = completion_module()
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ValueError("publication record schema mismatch")
    if (c._validated_identity(record, "publication") !=
            c._validated_identity(entry, "completion") or
            record["PROJECT_ID"] != entry.get("PROJECT_ID")):
        raise ValueError("publication identity/project mismatch")
    path = record["path"]
    if not isinstance(path, str) or "\\" in path:
        raise ValueError("invalid publication path")
    parts = path.split("/")
    if (len(parts) < 2 or parts[0] not in {"workspace", "evidence", "reports"}
            or path.casefold() == "reports/user_status.md"
            or any(p in {"", ".", ".."} or p[-1:] in {" ", "."}
                   or re.search(r'[<>:"|?*\x00-\x1f]', p)
                   or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\..*)?", p)
                   for p in parts)):
        raise ValueError("invalid publication path")
    if not isinstance(record["sha256"], str) or not c.HEX64.fullmatch(record["sha256"]):
        raise ValueError("invalid publication hash")
    c._check_timestamp(record["PUBLISHED_AT"], "PUBLISHED_AT")


def manifest_for(entry, records):
    c = completion_module()
    manifest = {"schema_version": 1, **c.entry_identity(entry),
                "PROJECT_ID": entry.get("PROJECT_ID"),
                "COMMIT_ID": c.commit_id_for(entry["MESSAGE_ID"], entry["NONCE"]),
                "publications": sorted(records, key=lambda r: r["path"])}
    validate_manifest(manifest, entry)
    return manifest


def validate_manifest(manifest, entry):
    c = completion_module()
    if (not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or c._validated_identity(manifest, "manifest") !=
               c._validated_identity(entry, "completion")
            or manifest["PROJECT_ID"] != entry.get("PROJECT_ID")
            or manifest["COMMIT_ID"] != c.commit_id_for(entry["MESSAGE_ID"], entry["NONCE"])):
        raise ValueError("publication manifest binding mismatch")
    records = manifest["publications"]
    if not isinstance(records, list) or len(records) > MAX_PUBLICATIONS:
        raise ValueError("invalid publication list")
    seen = set()
    for record in records:
        validate_record(record, entry)
        key = record["path"].casefold()
        if key in seen:
            raise ValueError("ambiguous duplicate publication path")
        seen.add(key)


def sealed_intact(entry):
    c = completion_module()
    present = {"PUBLICATION_MANIFEST", "PUBLICATION_MANIFEST_SHA256"} & set(entry)
    if not present:
        return True  # additive extension; old completions remain valid
    if len(present) != 2:
        return False
    try:
        manifest = entry["PUBLICATION_MANIFEST"]
        validate_manifest(manifest, entry)
        return c.canonical_json_sha256(manifest) == entry["PUBLICATION_MANIFEST_SHA256"]
    except (ValueError, TypeError, KeyError, c.CompletionError):
        return False


def read_publication_json(root, relative, cap=None):
    if cap is None:
        cap = completion_module().STAGING_MAX_BYTES
    path = safe_path(Path(root), relative)
    with path.open("rb") as handle:
        raw = handle.read(cap + 1)
    if len(raw) > cap:
        raise ValueError("publication evidence exceeds read bound")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("ambiguous duplicate JSON key in publication evidence")
            result[key] = value
        return result
    return raw, json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object)


def collect_locked(root, entry):
    """Snapshot every Runtime-published final path for this fenced attempt."""
    c = completion_module()
    commit_id = c.commit_id_for(entry["MESSAGE_ID"], entry["NONCE"])
    relative = f"handoff/executor_publications/{commit_id}"
    directory = safe_path(Path(root), relative)
    records = []
    if directory.exists():
        for path in directory.iterdir():
            if len(records) >= MAX_PUBLICATIONS:
                raise ValueError("too many publication records")
            _, record = read_publication_json(root, f"{relative}/{path.name}")
            validate_record(record, entry)
            if path.name != c.sha256_bytes(record["path"].encode()) + ".json":
                raise ValueError("publication record filename binding mismatch")
            records.append(record)
    return manifest_for(entry, records)


def project_publications(root, entry):
    """Read-only control-plane projection. Never fall back after corruption."""
    c = completion_module()
    try:
        if not c.entry_hashes_intact(entry):
            raise ValueError("completion integrity failure")
        if entry.get("COMMIT_ID") != c.commit_id_for(entry["MESSAGE_ID"], entry["NONCE"]):
            raise ValueError("completion id mismatch")
        if "PUBLICATION_MANIFEST" in entry:
            return {"integrity": "OK", "source": "sealed_manifest",
                    "publications": entry["PUBLICATION_MANIFEST"]["publications"]}
        raw, staging = read_publication_json(root, f"handoff/completion_ledger/staged/{entry['COMMIT_ID']}/staging.json")
        if (c.sha256_bytes(raw) != entry.get("STAGING_MANIFEST_SHA256")
                or not isinstance(staging, dict)
                or c._validated_identity(staging, "archived staging") != c.entry_identity(entry)
                or staging.get("PROJECT_ID") != entry.get("PROJECT_ID")
                or staging.get("RECEIPT") != entry["RECEIPT"]
                or staging.get("COMPLETION_STAGING_SCHEMA_VERSION") != 1
                or staging.get("STATUS") != "STAGING_READY"):
            raise ValueError("archived staging binding mismatch")
        paths = {}
        for label in ("EVIDENCE", "DELIVERABLES"):
            items = staging.get(label, [])
            if not isinstance(items, list) or len(items) > c.EVIDENCE_MAX_ENTRIES:
                raise ValueError("invalid archived output list")
            for item in items:
                if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
                    raise ValueError("invalid archived output")
                # Validate BEFORE deriving any filesystem path.
                candidate = {**c.entry_identity(entry), "PROJECT_ID": entry.get("PROJECT_ID"),
                             **item, "PUBLISHED_AT": entry.get("COMMITTED_AT")}
                validate_record(candidate, entry)
                if item["path"] in paths and paths[item["path"]] != item["sha256"]:
                    raise ValueError("conflicting archived output hashes")
                paths[item["path"]] = item["sha256"]
        records = []
        for path, digest in paths.items():
            record_path = publication_record(Path(root), entry, path).relative_to(root).as_posix()
            _, record = read_publication_json(root, record_path)
            validate_record(record, entry)
            if record["path"] != path or record["sha256"] != digest:
                raise ValueError("legacy publication/staging mismatch")
            records.append(record)
        manifest = manifest_for(entry, records)
        return {"integrity": "OK", "source": "legacy_staging_and_publication",
                "publications": manifest["publications"],
                "note": "Only publications corroborated by hash-bound archived staging are recovered; historical bytes are not retained."}
    except (OSError, ValueError, TypeError, KeyError, c.CompletionError, FenceError) as exc:
        return {"integrity": "UNAVAILABLE", "source": None,
                "publications": [], "note": str(exc)}


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
