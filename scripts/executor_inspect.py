"""Two-phase bounded observations: snapshot under authority, observe, recheck and retain.

No Runtime lock is held while waiting for DNS/browser/subprocess work. An in-flight
read can drain until its timeout after revocation, but its result cannot be retained.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

import executor_capabilities as capabilities
import executor_completion as completion
import executor_entry as entry
import executor_fence as fence

SCRIPTS = Path(__file__).resolve().parent


def run_worker(argv, payload, timeout, max_output):
    """Only fixed Runtime workers reach here, never Executor-selected commands."""
    import executor_work as work
    env = {k: v for k, v in os.environ.items()
           if k.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "HOME", "LANG"}}
    with tempfile.TemporaryDirectory(prefix="runtime-inspect-") as scratch:
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                cwd=scratch, env=env, start_new_session=os.name != "nt",
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            raw, _ = proc.communicate(json.dumps(payload).encode("utf-8"), timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                if os.name == "nt":
                    subprocess.run([str(Path(os.environ["SYSTEMROOT"]) / "System32/taskkill.exe"),
                                    "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            finally:
                if proc.poll() is None:
                    proc.kill()
                proc.communicate(timeout=5)
            raise work.Unavailable("inspection timed out; no observation retained") from exc
        finally:
            for stream in (proc.stdin, proc.stdout):
                stream.close()
        if proc.returncode or len(raw) > max_output:
            raise work.Unavailable("inspection provider failed or exceeded output limit")
        try:
            result = json.loads(raw)
            json.dumps(result, allow_nan=False)
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise ValueError()
        except (ValueError, UnicodeError) as exc:
            raise work.Unavailable("malformed inspection provider response") from exc
        if not result["ok"]:
            raise work.Unavailable("inspection unavailable: " + str(result.get("reason", "provider failure"))[:200])
        return result


def context_locked(root, session):
    import executor_work as work
    ready = entry.enter(root, resume_token=session, contract_version=2)
    if ready["status"] != "READY":
        raise fence.FenceError(ready["reason"])
    identity = completion._validated_identity(completion.read_runtime_state(root)["authorized_dispatch"], "inspection")
    _, project = fence.check_locked(root, identity, claim_token=session)
    candidate = fence.attempt_root(project, identity)
    if work.finish.boundary_started(root, project, identity):
        raise ValueError("finish has started; inspection is closed")
    return work.utility_contract_locked(root), identity, project, candidate


def source_path(project, candidate, fs, request):
    import executor_work as work
    if request["area"] == "project":
        return work.input_path(project, fs["project_read_paths"], request["path"])
    if request["area"] == "work":
        return fence.output_path(candidate, request["path"])
    raise ValueError("unknown filesystem area")


def perform(root, session, request):
    import executor_work as work
    op = request["op"]
    with fence.runtime_lock(root):
        caps, identity, project, candidate = context_locked(root, session)
        name = "browser" if op == "render" else "network"
        if op not in caps[name]["operations"]:
            raise ValueError("operation denied by capability")
        if op == "fetch":
            capabilities.validate_url(request["url"])
            if request["url"] not in caps["network"]["urls"]:
                raise ValueError("URL outside exact network_urls grant")
            argv = [sys.executable, "-I", str(SCRIPTS / "executor_fetch.py")]
            payload, timeout, max_output = {"url": request["url"]}, 15, 400000
        else:
            if (type(request["width"]) is not int or not 320 <= request["width"] <= 1920
                    or type(request["height"]) is not int or not 320 <= request["height"] <= 1080):
                raise ValueError("invalid render viewport")
            screenshot = request["screenshot"]
            if not isinstance(screenshot, str) or not screenshot.startswith("evidence/") or not screenshot.endswith(".png"):
                raise ValueError("screenshot must be an evidence/*.png candidate")
            destination = fence.output_path(candidate, screenshot)
            if destination.exists():
                raise ValueError("screenshot path must be unused; preserve prior inspection evidence")
            source = source_path(project, candidate, caps["filesystem"], request)
            snapshot = work.read_bytes(source, capabilities.MAX_RENDER_BYTES, observation=True)
            if session.encode() in snapshot:
                raise ValueError("input contains session secret")
            try:
                html = snapshot.decode("utf-8-sig")
            except UnicodeError as exc:
                raise work.Unavailable("render requires UTF-8 HTML") from exc
            deps = capabilities.browser_dependencies()
            if not all(deps.values()):
                raise work.Unavailable("browser provider unavailable")
            argv = [deps["node"], str(SCRIPTS / "executor_render.mjs")]
            payload = {"browser": deps["browser"], "html": html, "width": request["width"], "height": request["height"]}
            timeout, max_output = 30, 12 * 1024 * 1024
        fence.check_locked(root, identity, claim_token=session)
    # Errors are also re-fenced, so an in-flight failure cannot tell a revoked
    # Executor to CONTINUE. No worker receives the session or any project path.
    failure = None
    try:
        observed = run_worker(argv, payload, timeout, max_output)
    except (work.Unavailable, OSError) as exc:
        failure = exc
    with fence.runtime_lock(root):
        current_caps, current_identity, project, candidate = context_locked(root, session)
        if current_identity != identity or current_caps != caps:
            raise fence.FenceError("inspection authority changed")
        if failure:
            raise work.Unavailable(str(failure))
        if session in json.dumps(observed):
            raise ValueError("observation contains session secret")
        if op == "fetch":
            body = base64.b64decode(observed["body"], validate=True)
            if len(body) > capabilities.MAX_FETCH_BYTES:
                raise work.Unavailable("response exceeds fetch limit")
            if session.encode() in body:
                raise ValueError("observation contains session secret")
            return work.response("OK", observation={"url": request["url"],
                "status_code": observed["status_code"], "content_type": observed["content_type"],
                "text": body.decode("utf-8-sig"), "sha256": hashlib.sha256(body).hexdigest(),
                "source": "Runtime HTTPS GET; remote contents unverified"})
        if work.read_bytes(source_path(project, candidate, caps["filesystem"], request), capabilities.MAX_RENDER_BYTES,
                           observation=True) != snapshot:
            raise work.Unavailable("source changed during rendering; inspect current candidate again")
        png = base64.b64decode(observed.pop("png"), validate=True)
        if not png.startswith(b"\x89PNG\r\n\x1a\n") or len(png) > 8 * 1024 * 1024:
            raise work.Unavailable("invalid or oversized screenshot")
        if fence.output_path(candidate, screenshot).exists():
            raise ValueError("screenshot path was occupied during rendering")
        observed.pop("ok")
        work.replace_candidate(root, identity, session, candidate, screenshot, png)
        return work.response("OK", observation={**observed, "source_area": request["area"],
            "source_path": request["path"], "source_sha256": hashlib.sha256(snapshot).hexdigest(),
            "screenshot": screenshot, "screenshot_sha256": hashlib.sha256(png).hexdigest(),
            "assurance": "trusted_host", "judgment": "Rendered measurements, not acceptance or visual-quality judgment"})
