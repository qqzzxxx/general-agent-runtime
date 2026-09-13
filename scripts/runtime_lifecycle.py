"""Shared, verified Runtime resume. Control remains paused until owner startup.

The lifecycle mutex serializes callers; the Executor fence serializes the commit
with pause/STOP/claim and scheduler ownership. The fence is released while a child boots.
An expiring, revision-bound ticket prevents delayed children from undoing a newer
control request or a failed resume. Only the scheduler can acknowledge startup.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone

import executor_fence as fence
import supervisor_control as control

STARTUP_TIMEOUT = 5.0


def process_alive(pid: int) -> bool | None:
    """None means unprovable, never permission to reclaim an owner."""
    if type(pid) is not int or pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            code = wintypes.DWORD()
            return code.value == 259 if kernel.GetExitCodeProcess(handle, ctypes.byref(code)) else None
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return None


def process_identity(pid: int):
    """OS process birth identity, so PID reuse cannot prove scheduler health."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [wintypes.FILETIME() for _ in range(4)]
            if kernel.GetProcessTimes(handle, *[ctypes.byref(value) for value in times]):
                return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        finally:
            kernel.CloseHandle(handle)
        return None
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except OSError:
        return None


def live_owner(root: Path) -> dict | None:
    owner = control._read_json(root / "control" / ".orchestrator.lock")
    if owner is None:
        return None
    if not isinstance(owner, dict) or type(owner.get("pid")) is not int:
        raise control.ControlError("scheduler ownership is malformed")
    alive = process_alive(owner["pid"])
    if alive is None:
        raise control.ControlError("scheduler ownership cannot be verified")
    if alive and owner.get("process_identity") is not None:
        identity = process_identity(owner["pid"])
        if identity is None:
            raise control.ControlError("scheduler process identity cannot be verified")
        if identity != owner["process_identity"]:
            return None
    return owner if alive else None


def validate_resumable(root: Path):
    project_id, _, _, state, runtime = control._load_live(root)
    for flag in ("STOP", "HUMAN_REVIEW"):
        if (root / "control" / flag).exists():
            raise control.ControlError(f"{flag} requires its dedicated recovery path")
    if state.get("status") not in {"SUPERVISOR_TURN", "WAITING_EXECUTOR"}:
        raise control.ControlError(f"project {state.get('status')} is terminal or not resumable")
    if runtime.get("status") not in {"RUNNING", "PAUSED", "PAUSE_PENDING_AFTER_CURRENT_STAGE"}:
        raise control.ControlError("Runtime is terminal or not resumable")
    if project_id is not None and state.get("project_id") != project_id:
        raise control.ControlError("resume project identity mismatch")
    if state.get("status") == "SUPERVISOR_TURN" and state.get("current_task") is not None:
        raise control.ControlError("Supervisor stage has contradictory current task")
    if state.get("status") == "WAITING_EXECUTOR" and not isinstance(state.get("current_task"), dict):
        raise control.ControlError("Executor stage has no current task")
    if state.get("deadline_at"):
        try:
            deadline = datetime.fromisoformat(state["deadline_at"].replace("Z", "+00:00"))
            if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                raise ValueError("expired or unzoned")
        except (TypeError, ValueError) as exc:
            raise control.ControlError("project deadline is expired or invalid") from exc
    return project_id, state, runtime


def scheduler_startup(root: Path, runtime: dict, token: str | None = None) -> None:
    """Called by Orchestrator after initialization and before any scheduling."""
    with fence.runtime_lock(root):
        owner = live_owner(root)
        if not owner or owner["pid"] != os.getpid() or not owner.get("owner_id"):
            raise control.ControlError("startup requires the owned scheduler lock")
        fresh = control.load_control(root)
        if token:
            ticket = control._read_json(root / "control" / "resume_lifecycle.json")
            project_id, _, _ = validate_resumable(root)
            if (not isinstance(ticket, dict) or ticket.get("id") != token
                    or ticket.get("status") != "STARTING"
                    or ticket.get("expires_at", 0) <= time.time()
                    or ticket.get("revision") != fresh["revision"]
                    or ticket.get("project_id") != project_id
                    or (fresh.get("pause") or {}).get("status") != "PAUSED"):
                raise control.ControlError("resume ticket expired, cancelled, or superseded")
            previous = fresh["pause"]
            runtime["status"] = "RUNNING"
            runtime["scheduler_owner"] = owner
            runtime["scheduler_resume_id"] = token
            control._atomic_json(root / "control" / "orchestrator_runtime.json", runtime)
            # RUNNING is committed only by a live, initialized scheduler. If the
            # caller disappears, the child still owns and continues the Runtime.
            fresh["revision"] += 1
            fresh["pause"] = {**previous, "status": "RUNNING", "resumed_at": control.now_iso(),
                              "resume_id": token}
            control.save_control(root, fresh)
            control._atomic_json(root / "control" / "resume_lifecycle.json", {
                **ticket, "status": "READY", "owner": owner,
                "committed_revision": fresh["revision"], "ready_at": control.now_iso(),
            })
        else:
            runtime["scheduler_owner"] = owner


def launch(root: Path, token: str):
    script = root / "orchestrator.py"
    if not script.is_file():
        raise control.ControlError("Runtime orchestrator.py is missing")
    env = dict(os.environ, GAR_RESUME_TICKET=token)
    logs = root / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "resume-startup.log").open("ab") as log:
        return subprocess.Popen(
            [sys.executable, str(script)], cwd=root, env=env,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )


def resume(root: Path, *, start: bool = True) -> dict:
    root = Path(root).resolve()
    with fence.runtime_lock(root, name=".resume-lifecycle.lock"):
        # A PAUSED owner may still be unwinding its finally block. Never clear
        # pause while that process can exit without observing the change.
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while True:
            with fence.runtime_lock(root):
                control.reconcile_control_transactions_locked(root)
                project_id, state, runtime = validate_resumable(root)
                fresh = control.load_control(root)
                previous = fresh.get("pause")
                if not isinstance(previous, dict):
                    raise control.ControlError("pause facts are unavailable")
                owner = live_owner(root)
                # Recover only our own durable, unfinished startup preparation.
                # An arbitrary RUNNING/dead-owner contradiction remains closed.
                old_ticket = control._read_json(root / "control" / "resume_lifecycle.json", {})
                if (not owner and isinstance(old_ticket, dict)
                        and old_ticket.get("schema_version") == 1
                        and old_ticket.get("project_id") == project_id
                        and type(old_ticket.get("revision")) is int
                        and fresh["revision"] in {old_ticket["revision"], old_ticket["revision"] + 1}
                        and old_ticket.get("status") in {"STARTING", "FAILED"}
                        and old_ticket.get("id") is not None
                        and runtime.get("scheduler_resume_id") == old_ticket["id"]
                        and runtime.get("status") == "RUNNING"
                        and previous.get("status") in {"PAUSED", "RUNNING"}):
                    control.set_pause(root)
                    fresh = control.load_control(root)
                    previous = fresh["pause"]
                    _, state, runtime = validate_resumable(root)
                if previous.get("status") == "RUNNING":
                    if (owner and owner.get("owner_id") and owner.get("process_identity") is not None
                            and runtime.get("scheduler_owner") == owner
                            and runtime.get("status") == "RUNNING"):
                        return {"previous": previous, "current": previous,
                                "startup": {"status": "EXISTING_OWNER", "verified": True,
                                            "launched": False, "owner": owner}}
                    raise control.ControlError("RUNNING control has no verified schedulable owner")
                if previous.get("status") != "PAUSED" or runtime.get("status") != "PAUSED":
                    raise control.ControlError("pause is pending or Runtime/control facts disagree")
                if previous.get("PROJECT_ID", project_id) != project_id:
                    raise control.ControlError("pause belongs to a different project")
                retired = runtime.get("retired_message_ids", [])
                if not isinstance(retired, list) or any(type(value) is not int for value in retired):
                    raise control.ControlError("retirement facts are malformed")
                if state["status"] == "WAITING_EXECUTOR":
                    task = control.normalize_identity(state["current_task"])
                    if (task["MESSAGE_ID"] in retired
                            or not control._same_identity(task, runtime.get("authorized_dispatch"))):
                        raise control.ControlError("paused task is retired or lacks matching authorization")
                    if control.current_status(root).get("active_task_claimed") is True:
                        raise control.ControlError("claimed Executor requires pending safe pause")
                if not owner:
                    if not start:
                        raise control.ControlError("Resume requires scheduler launch; NoStart cannot clear pause")
                    ticket = {"schema_version": 1, "id": uuid.uuid4().hex,
                              "status": "STARTING", "project_id": project_id,
                              "revision": fresh["revision"],
                              "expires_at": time.time() + STARTUP_TIMEOUT}
                    control._atomic_json(root / "control" / "resume_lifecycle.json", ticket)
                    break
            if time.monotonic() >= deadline:
                raise control.ControlError("paused scheduler has not exited; pause retained, retry Resume")
            time.sleep(0.05)

        child = None
        try:
            child = launch(root, ticket["id"])
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while time.monotonic() < deadline:
                with fence.runtime_lock(root):
                    ready = control._read_json(root / "control" / "resume_lifecycle.json")
                    _, _, runtime = validate_resumable(root)
                    fresh = control.load_control(root)
                    owner = live_owner(root)
                    if (ready.get("id") == ticket["id"] and ready.get("status") == "READY"
                            and owner == ready.get("owner") and owner is not None
                            and runtime.get("scheduler_owner") == owner
                            and runtime.get("status") == "RUNNING"
                            and fresh["pause"]["status"] == "RUNNING"):
                        return {"previous": previous, "current": fresh["pause"],
                                "startup": {"status": "READY", "verified": True,
                                            "launched": True, "owner": owner}}
                if child.poll() is not None:
                    raise control.ControlError(f"scheduler exited during startup ({child.returncode})")
                time.sleep(0.05)
            raise control.ControlError("scheduler startup verification timed out")
        except Exception as exc:
            with fence.runtime_lock(root):
                current = control._read_json(root / "control" / "resume_lifecycle.json", {})
                control._atomic_json(root / "control" / "resume_lifecycle.json", {
                    **current, "status": "FAILED", "error": str(exc),
                })
                fresh = control.load_control(root)
                # Do not undo intervening operator decisions. If our child
                # committed RUNNING, request a real safe pause (including any
                # already claimed work); never fabricate a Runtime status.
                _, _, _, state, runtime = control._load_live(root)
                if ((fresh["pause"].get("resume_id") == ticket["id"]
                     or runtime.get("scheduler_resume_id") == ticket["id"])
                        and fresh["pause"]["status"] in {"PAUSED", "RUNNING"}
                        and state.get("status") in {"SUPERVISOR_TURN", "WAITING_EXECUTOR"}
                        and runtime.get("status") not in {"HUMAN_REVIEW", "STOPPED_BY_USER", "DEADLINE_REACHED"}):
                    control.set_pause(root)
            raise control.ControlError(f"Resume failed; inspect logs/resume-startup.log; {exc}") from exc
