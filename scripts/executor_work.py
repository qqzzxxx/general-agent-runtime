"""V2 task operations: bind and fence inside Runtime, then perform one bounded action.

JSON on stdin; --session is the retained opaque entry token. No arbitrary command
execution or host-tool forwarding. This is a cooperative same-user boundary.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

import executor_completion as completion
import executor_capabilities as capabilities
import executor_contract as contract
import executor_entry as entry
import executor_fence as fence
import executor_finish as finish

MAX_REQUEST_BYTES = 1024 * 1024
MAX_TEXT_BYTES = 262144
MAX_LIST_ENTRIES = 1000
RESULT_PATH = "executor-result.json"
OPERATIONS = {
    **capabilities.OPERATIONS,
    "read": {"op", "area", "path"}, "list": {"op", "area", "path"},
    "write": {"op", "path", "text"},
    "copy": {"op", "area", "path", "destination"}, "finish": {"op", "result"},
    "checkpoint": {"op"},
    "help": {"op", "topic"},
}


class Unavailable(ValueError):
    """A permitted observation failed; no candidate retained (a GET may have run)."""


def response(status, **data):
    if status == "UNAVAILABLE":
        data.setdefault("guidance", "This optional observation was unsuccessful. Choose another permitted method or report the gap; it does not revoke authority.")
    return {"schema_version": 2, "status": status,
            "action": "CONTINUE" if status in {"OK", "UNAVAILABLE"} else "STOP", **data}


def utility_contract_locked(root):
    """Deferred metadata only; caller must hold the lock and prove live ownership.

    Use the same sealed task and existing utility projection as before. Recheck
    bytes because entry's compact view intentionally omits optional helper detail.
    """
    import supervisor_control as control
    auth = completion.read_runtime_state(root)["authorized_dispatch"]
    raw = (root / "TO_ZCODE.md").read_bytes()
    if completion.sha256_bytes(raw) != auth.get("TO_ZCODE_SHA256"):
        raise fence.FenceError("inbox_hash_mismatch")
    return contract.project(control._parse_dispatch_bytes(raw))["capabilities"]["utilities"]


def utility_help(utilities):
    schemas = {
        "read": {"op": "read", "area": "project|work", "path": "<relative path>"},
        "list": {"op": "list", "area": "project|work", "path": "<relative directory>"},
        "write": {"op": "write", "path": "<relative work path>", "text": "<UTF-8 text>"},
        "copy": {"op": "copy", "area": "project|work", "path": "<relative path>", "destination": "<relative work path>"},
        "fetch": {"op": "fetch", "url": "<exact permitted HTTPS URL>"},
        "render": {"op": "render", "area": "project|work", "path": "<relative HTML path>",
                   "width": 1280, "height": 800, "screenshot": "evidence/<unused name>.png"},
    }
    allowed = {op for cap in utilities.values() for op in cap["operations"]}
    return {"utilities": utilities, "requests": {op: schema for op, schema in schemas.items() if op in allowed},
            "rules": "Optional utilities, not exclusive methods or host-tool permissions. Send each request separately using runtime.command; paths are forward-slash relative. Read/list area selects declared project inputs or work files. Copy/write target work only. Render is static and cannot establish interactions or automatic visual judgment."}


def parse_request(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request exceeds 1 MiB")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    result = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique)
    finish.encoded(result)  # Reject NaN/Infinity, including nested values.
    return result


def input_path(project, policy, relative):
    contract.validate_input_path(relative)
    # Deliberately case-sensitive even on Windows: a mismatch denies access.
    if not any(relative == p or relative.startswith(p + "/") for p in policy):
        raise ValueError("input outside declared read_paths")
    return fence.safe_path(project, relative)


def read_bytes(path, limit, *, observation=False):
    error = Unavailable if observation else ValueError
    if not path.is_file():
        raise error("input is missing or is not a regular file")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise error("file exceeds operation limit")
    return data


def replace_candidate(root, identity, session, work, relative, data, *, internal=False):
    path = fence.safe_path(work, relative) if internal else fence.output_path(work, relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        fence.check_locked(root, identity, claim_token=session)
        fence.safe_path(work, relative)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def perform(root=entry.DEFAULT_ROOT, *, session, request):
    root = Path(root).resolve()
    try:
        if not isinstance(request, dict) or len(finish.encoded(request).encode("utf-8")) > MAX_REQUEST_BYTES:
            raise ValueError("invalid or oversized request")
        if not isinstance(session, str) or not 1 <= len(session) <= 256:
            return response("NOT_AUTHORIZED", reason="invalid session")
        if session in finish.encoded(request):
            raise ValueError("request contains session secret")
        op = request.get("op")
        if not isinstance(op, str) or op not in OPERATIONS or set(request) != OPERATIONS[op]:
            raise ValueError("unsupported operation or request fields")
        if op in capabilities.OPERATIONS:
            import executor_inspect
            return executor_inspect.perform(root, session, request)
        with fence.runtime_lock(root):
            ready = entry.enter(root, resume_token=session, contract_version=2)
            if ready["status"] != "READY":
                return response("NOT_AUTHORIZED", reason=ready["reason"])
            runtime = completion.read_runtime_state(root)
            identity = completion._validated_identity(runtime["authorized_dispatch"], "work")
            _, project = fence.check_locked(root, identity, claim_token=session)
            work = fence.attempt_root(project, identity)
            fs = ready["contract"]["capabilities"]["filesystem"]
            frozen = finish.boundary_started(root, project, identity)
            if op != "finish" and frozen:
                raise ValueError("finish has started; candidate work is closed")
            if op == "checkpoint":
                return response("OK")
            if op == "help":
                if request["topic"] != "utilities":
                    raise ValueError("unsupported help topic")
                return response("OK", **utility_help(utility_contract_locked(root)))
            if op == "finish":
                result = request["result"]
                data = finish.encoded(result).encode("utf-8")
                if len(data) > completion.STAGING_MAX_BYTES or session in data.decode("utf-8"):
                    raise ValueError("invalid semantic result size or secret")
                # Read-only tasks can still report judgments, but cannot publish
                # files obtained by bypassing the advertised write capability.
                if fs["mode"] != "workspace" and isinstance(result, dict) and result.get("artifacts"):
                    raise ValueError("publication requires workspace capability")
                path = fence.safe_path(work, RESULT_PATH)
                if frozen and path.exists():
                    if read_bytes(path, completion.STAGING_MAX_BYTES) != data:
                        raise ValueError("finish result is already frozen")
                elif not frozen:
                    replace_candidate(root, identity, session, work, RESULT_PATH, data, internal=True)
                else:
                    raise ValueError("finish boundary exists without its retained result")
                result = finish.finish(root, claim_token=session, result_path=RESULT_PATH)
                if result["status"] == "INVALID_RESULT":
                    # Validation itself grants no retry authority. Recheck the
                    # owner/deadline/control and durable effects under this lock.
                    fence.check_locked(root, identity, claim_token=session)
                    if not finish.boundary_started(root, project, identity):
                        return response("INVALID_RESULT", action="CORRECT_AND_RESUBMIT",
                                        reason=result["reason"],
                                        recovery="Validation has not frozen this attempt. Correct the reported defect, inspect or repair candidates as permitted, stop candidate writers, and resubmit using the same retained session within the existing deadline and scope."
                                        + (" Keep FV claims/standards and honest negative judgments fixed; correction cannot promote a verdict without evidence."
                                           if "verification" in ready["contract"]["context"] else ""),
                                        result_contract=ready["contract"]["runtime"]["finish"])
                # Ledger IDs are Runtime transport details, not outcome evidence.
                return response(result["status"], **({"reason": result["reason"]} if "reason" in result else {}))
            if op not in fs["operations"]:
                raise ValueError("operation denied by filesystem capability")
            if op == "write":
                if not isinstance(request["text"], str):
                    raise ValueError("write requires text")
                data = request["text"].encode("utf-8")
                if len(data) > MAX_TEXT_BYTES or session in request["text"]:
                    raise ValueError("invalid write size or secret")
                replace_candidate(root, identity, session, work, request["path"], data)
                return response("OK", bytes_written=len(data))
            area, relative = request["area"], request["path"]
            if area == "project":
                path = input_path(project, fs["project_read_paths"], relative)
            elif area == "work":
                # Reads/listing of the three root directories are also supported.
                if relative in ("workspace", "evidence", "reports"):
                    path = fence.safe_path(work, relative)
                else:
                    path = fence.output_path(work, relative)
            else:
                raise ValueError("unknown filesystem area")
            if op == "read":
                data = read_bytes(path, MAX_TEXT_BYTES, observation=True)
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError as exc:
                    raise Unavailable("text read requires UTF-8; binary copy is available with workspace capability") from exc
                if session in text:
                    raise ValueError("input contains session secret")
                fence.check_locked(root, identity, claim_token=session)
                return response("OK", text=text)
            if op == "list":
                if not path.is_dir():
                    raise Unavailable("directory is missing or is not a directory")
                rows = []
                for child in path.iterdir():
                    if len(rows) >= MAX_LIST_ENTRIES:
                        raise Unavailable("directory exceeds 1000 entries; use a narrower path")
                    child_relative = relative + "/" + child.name
                    if child_relative.casefold() == "reports/user_status.md":
                        continue
                    if area == "project":
                        input_path(project, fs["project_read_paths"], child_relative)
                    else:
                        fence.output_path(work, child_relative)
                    rows.append({"name": child.name, "kind": "directory" if child.is_dir() else "file"})
                fence.check_locked(root, identity, claim_token=session)
                return response("OK", entries=sorted(rows, key=lambda row: row["name"]))
            data = read_bytes(path, completion.EVIDENCE_MAX_FILE_BYTES, observation=True)
            if session.encode("utf-8") in data:
                raise ValueError("input contains session secret")
            replace_candidate(root, identity, session, work, request["destination"], data)
            return response("OK", bytes_written=len(data))
    except Unavailable as exc:
        return response("UNAVAILABLE", reason=str(exc))
    except (fence.FenceError, completion.CompletionError) as exc:
        return response("NOT_AUTHORIZED", reason=str(exc))
    except (ValueError, TypeError, KeyError, RuntimeError, RecursionError) as exc:
        # Avoid echoing arbitrary request text or credentials in diagnostics.
        return response("INVALID_REQUEST", reason=str(exc).replace(str(session), "<redacted>"))
    except OSError as exc:
        return response("ERROR", reason=type(exc).__name__)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=entry.DEFAULT_ROOT)
    parser.add_argument("--session", required=True)
    args = parser.parse_args(fence.token_cli_args(argv, "--session"))
    try:
        request = parse_request(sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1))
        result = perform(args.root, session=args.session, request=request)
    except (ValueError, TypeError, RecursionError):
        result = response("INVALID_REQUEST", reason="invalid request JSON")
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["status"] in {"OK", "UNAVAILABLE", "FINISHED"} else 12


if __name__ == "__main__":
    raise SystemExit(main())
