"""General Agent Runtime v1.3 Web Console backend (P1 skeleton + P2 registry
+ P3 read-only Cockpit core + P4 read-only Timeline and Task Detail).

A dependency-light, localhost-only HTTP surface over the existing v1.2
control plane.

P1 scope:

- `GET /api/health`  versioned backend liveness plus a structural availability
  signal for the v1.2 control plane (script presence).
- `GET /api/status`  versioned read-only Runtime status obtained by invoking
  `scripts/supervisor_control.py status --json` for the server-configured
  Runtime root, with an argument-vector subprocess, a bounded timeout, and an
  explicit Runtime-root binding. Terminal screen text is never parsed; only
  the JSON document on stdout is interpreted.
- `GET /` and `GET /index.html` serve the minimal same-origin landing page.

P2 scope (Runtime Registry):

- The Console owns a persistent Registry of explicitly added Runtime Roots
  (`web_console_data/runtime_registry.json`). It never scans disks and never
  creates Runtimes; removing an entry never touches the registered tree.
- `GET    /api/runtimes`                    list entries
- `POST   /api/runtimes`                    add an existing Runtime Root
- `GET    /api/runtimes/<id>`                show one entry
- `POST   /api/runtimes/<id>/rename`        rename the display label
- `POST   /api/runtimes/<id>/revalidate`    re-run compatibility validation
- `DELETE /api/runtimes/<id>`               remove only the Registry entry
- `GET    /api/runtimes/<id>/status`        status of that registered Runtime
- `GET    /api/runtimes/<id>/cockpit`       read-only Project Cockpit document
                                            for that registered Runtime (P3)

P3 scope (read-only Cockpit core):

`GET /api/runtimes/<id>/cockpit` reuses the P2 opaque-ID root binding and the
same bounded status probe, then enriches the status document with the pure,
offline interpreter in `web_console_state.py`: a deterministic Current
Execution presentation (state family and label, worker, since-when only where
the Runtime reported an authoritative timestamp, Next Expected, user-action
requirement), protocol milestones as facts, and bounded Runtime Health facts
(backend, Orchestrator, Codex, last observed ZCode activity, current
authorization, current claim, pending interventions, errors/warnings).
No model calls are made and no progress percentages or token usage are
invented; unknown, contradictory, or malformed status facts fail closed to an
explicit honest unavailable presentation (`STATE_UNAVAILABLE`), and
control-plane failures surface as the same deterministic error envelopes as
`/api/runtimes/<id>/status`. Mutation controls, Runtime creation, and P9
alert heuristics are intentionally absent.

P4 scope (read-only Timeline + Task Detail):

- `GET /api/runtimes/<id>/timeline`       bounded, paginated round history
- `GET /api/runtimes/<id>/rounds/<mid>`   read-only Task Detail document

Both routes reuse the P2 opaque-ID root binding exclusively. History is read
through the v1.2 control plane's own read-only commands (`timeline --json`,
`tasks --message-id <id> --json`, `feedback --message-id <id> --json`,
`interventions --json`) as bounded argument-vector subprocesses; the browser
can never submit, infer, or override a Runtime Root path or a file path, and
every query parameter is validated fail-closed before use. The pure
projection layer in `web_console_history.py` groups rounds by stable
MESSAGE_ID, derives honest lifecycle/integrity facts (corrupt, unauthorized,
incomplete, and contradictory records are surfaced, never silently skipped),
and enforces deterministic pagination, ordering, search, and filters. The
Task Detail composition relays the exact archived dispatch text (only for
`AUTHORIZED_VALID` archives, byte-for-byte), the authoritative completion
ledger entry with its executor receipt, interventions bound to the round,
and artifact metadata provenance from the receipt (content previews remain
deferred to the Artifact Center stage). The Supervisor decision receipt is
read server-side — one bounded file read whose path is derived from the
archive record's own `supervisor_turn_id`, never from browser input — and is
presented only when its canonical SHA-256 matches the hash recorded in the
dispatch archive metadata and its candidate binding matches the dispatch.
No model calls are made, no percentages or token usage are invented, and
control-plane failures surface as deterministic error envelopes (including
`CONTROL_PLANE_OUTPUT_TOO_LARGE` for oversized subprocess output and
`ROUND_NOT_FOUND` when no authoritative record exists for a MESSAGE_ID).

P5 scope (mutation-capable Human Control):

- `GET  /api/runtimes/<id>/controls`                    Pending Controls document
- `POST /api/runtimes/<id>/controls/pause`              Safe Pause (optional cooperative interrupt)
- `POST /api/runtimes/<id>/controls/resume`             Resume from PAUSED
- `POST /api/runtimes/<id>/controls/intervention`       STEER / Deep Review (AUDIT) submission
- `POST /api/runtimes/<id>/controls/stop/prepare`       server-issued STOP confirmation challenge
- `POST /api/runtimes/<id>/controls/stop/confirm`       confirmed Formal STOP
- `POST /api/runtimes/<id>/controls/human-review/prepare`  bind a Human Decision to the exact state hash
- `POST /api/runtimes/<id>/controls/human-review/apply`    apply the prepared receipt

Every mutation resolves the opaque Runtime ID exactly once through the
Registry and stays bound to that canonical root for the whole operation. Each
request body is a narrow typed schema validated fail-closed before any
Runtime call. Mutations are delegated exclusively to the existing formal v1.2
entry points — `supervisor_control.py pause | resume | intervene`, the
Runtime's own `STOP_AGENT_SYSTEM.ps1` (fixed argument-vector invocation; the
Console never writes control/STOP itself), and `resume_human_review.py
prepare | apply` — so project_state.json, intervention files, STOP artifacts,
claim/fence state, completion ledgers, and Human Decision receipts are never
written from generic Web handler code. The Formal STOP confirmation is a
server-issued, short-lived, single-use challenge bound to the Runtime ID and
the current project identity/state hash; stale, replayed, cross-Runtime, and
mismatched confirmations fail closed. Prepared Human Decision receipts are
stored only in the Console's non-authoritative data directory, are scoped to
one Runtime ID, and are deleted after a successful apply so a replayed
confirmation cannot reapply. HUMAN_REVIEW is presented with the bounded
reason from the Runtime's own control/HUMAN_REVIEW flag and never claims an
automatic resume.

P6 scope (read-only Artifact Center + artifact-triggered feedback):

- `GET  /api/runtimes/<id>/artifacts`                 filtered, paginated catalog
- `GET  /api/runtimes/<id>/artifacts/<aid>`           detail + provenance
- `GET  /api/runtimes/<id>/artifacts/<aid>/preview`   bounded read-only preview
- `GET  /api/runtimes/<id>/artifacts/<aid>/raw`       verified image/PDF bytes
- `POST /api/runtimes/<id>/artifacts/feedback`        artifact-bound STEER/AUDIT

Artifact discovery joins two authoritative sources: the completion-ledger
receipts (`feedback --json`) and a bounded, read-only walk of the active
project's three authorized publication roots (`workspace/`, `evidence/`,
`reports/`) resolved from the Runtime's own status document. The browser can
never name a path, a root, or a file: the artifact identifier is a SHA-256
of the normalized project-relative path, and every query parameter is
validated fail-closed. Provenance binds only to ledger entries the control
plane verified (`integrity == "OK"`); unbound files are shown without
invented provenance and untrusted ledger entries never bind. Previews are
bounded per format (byte/line/row/character caps), verified against magic
bytes and, for receipt-bound files, against the authoritative SHA-256 — a
mismatch refuses the preview instead of guessing. Images are served with a
strict content type and `nosniff`; PDFs are served only as downloads
(`attachment`) because in-browser PDF rendering could execute embedded PDF
scripts. Markdown and every other text format render as inert text. The one
mutation is artifact-triggered feedback, which reuses the P5 formal
intervention surface verbatim: the request binds a verified historical
MESSAGE_ID, normalized artifact paths, a bounded Unicode comment, and an
explicit STEER or AUDIT mode, then delegates to `supervisor_control.py
intervene` with references only — artifact contents and project history are
never auto-injected.

The runtime ID in a route is an opaque, server-generated 16-hex-character
value. A request's Runtime Root is resolved exclusively by looking up that
exact ID in the Registry on the server; no route accepts a filesystem path as
a selector or derives a path from an ID fragment, and the status subprocess
always receives the entry's own root via a top-level `--root` argument placed
before the subcommand.

Security posture: binds only to IPv4 loopback 127.0.0.1 (any other bind
request fails closed before listening), validates the Host header against the
served port, enforces a per-route method allowlist, bounds request bodies
(64 KiB) and error bodies, runs every control-plane call as an argument-vector
subprocess with a bounded timeout, and writes only its own non-authoritative
runtime data under the Web Console data directory.

CLI:
    python web_console_server.py serve --port N [--host 127.0.0.1]
        [--runtime-root DIR] [--console-root DIR] [--data-dir DIR]
        [--control-timeout SECONDS]
    python web_console_server.py verify-instance --data-dir DIR
        --runtime-root DIR [--health-timeout SECONDS]

`verify-instance` prints one JSON verdict (MISSING / INVALID / FOREIGN /
STALE / VERIFIED / ALIVE_UNVERIFIED) and is the shared freshness/ownership
check used by START_WEB_CONSOLE.ps1 and STOP_WEB_CONSOLE.ps1.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import logging
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import web_console_registry
import web_console_control
import web_console_history
import web_console_artifacts
import web_console_registry
import web_console_setup
import web_console_supervisor
import web_console_state
import web_console_settings
import web_console_alerts
import web_console_runtime_create
import resume_human_review

SCHEMA_VERSION = 1
SERVER_VERSION = "GARWebConsole/1.3.0"
DEFAULT_HOST = "127.0.0.1"
CONSOLE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ROOT = CONSOLE_ROOT
DEFAULT_DATA_DIR = CONSOLE_ROOT / "web_console_data"
CONTROL_SCRIPT_RELATIVE = Path("scripts") / "supervisor_control.py"
STATIC_INDEX_RELATIVE = Path("web_console") / "index.html"
METADATA_FILE_NAME = "instance.json"
STDOUT_ERROR_HEAD_BYTES = 2000
MAX_BODY_BYTES = 64 * 1024
# Upper bound on one control-plane subprocess stdout; larger outputs fail
# closed instead of being truncated silently.
MAX_CONTROL_OUTPUT_BYTES = 16 * 1024 * 1024
# Upper bound for one Supervisor decision receipt relayed into a round
# document; the receipt itself is a bounded control record.
MAX_DECISION_RECEIPT_BYTES = 1024 * 1024
# A supervisor_turn_id is used to build exactly one filename inside the
# Runtime's own control/supervisor_decisions directory; anything outside this
# safe charset is refused before any path is touched.
TURN_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
# A v1.2 PROJECT_ID is validated by the Runtime itself; the Console re-checks
# this exact shape before using one to build a project_state.json path or a
# helper argument.
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
# P5 Human Control bounds. The STOP challenge is a server-issued, short-lived,
# single-use secret bound to the Runtime ID and the current project
# identity/state; nothing about it is durable or authoritative.
STOP_CHALLENGE_TTL_SECONDS = 120.0
STOP_CHALLENGE_MAX_PENDING = 8
# Upper bound for one stored Human Decision receipt read back before apply;
# resume_human_review applies the same 128 KiB cap to receipt files.
MAX_HR_RECEIPT_BYTES = 128 * 1024
MAX_PROJECT_STATE_BYTES = 16 * 1024 * 1024
# Console-owned, non-authoritative storage for the two-step Human Decision
# flow. Decision input files and prepared receipts live here, never inside a
# managed Runtime tree.
HR_DATA_SUBDIR = "human_review"
HR_MAX_PREPARED_RECEIPTS = 32
STOP_SCRIPT_RELATIVE = "STOP_AGENT_SYSTEM.ps1"
HR_SCRIPT_RELATIVE = Path("scripts") / "resume_human_review.py"

# P7 Setup Wizard bounds. Setup drafts are Console-owned, non-authoritative
# data (one per registered Runtime); Goal upload needs more room than the
# 64 KiB control-request cap, so setup routes carry their own larger body
# bound while every pre-existing route keeps the original cap.
SETUP_MAX_BODY_BYTES = 512 * 1024
SETUP_GOAL_FILE_MAX_BYTES = web_console_setup.GOAL_MAX_BYTES
SETUP_PREFLIGHT_TIMEOUT_SECONDS = 90.0
SETUP_BOOTSTRAP_TIMEOUT_SECONDS = 120.0
SETUP_START_VERIFY_SECONDS = 20.0
ZCODE_TEMPLATE_RELATIVE = (Path("control")
                           / "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md")
START_SCRIPT_RELATIVE = "START_AGENT_SYSTEM.ps1"
BOOTSTRAP_SCRIPT_RELATIVE = Path("scripts") / "start_project.py"
MIN_PYTHON_VERSION = (3, 9)
# FILE_ATTRIBUTE_REPARSE_POINT on Windows; lstat exposes it on
# st_file_attributes so junction/symlink selections are refused, never
# followed.
REPARSE_POINT_FLAG = 0x400

# P6 Artifact Center bounds. Discovery walks only the three authorized
# publication roots of the active project (workspace/, evidence/, reports/),
# never the wider tree; hashing and preview reads are bounded before any
# content is interpreted.
ARTIFACTS_WALK_MAX_FILES = 5000
ARTIFACTS_WALK_MAX_DEPTH = 24
ARTIFACTS_MAX_HASH_BYTES = 64 * 1024 * 1024

# Route shapes for the Runtime Registry. Anything that is not exactly a
# generated ID (traversal, absolute paths, encoded or mixed separators) is
# not an ID and can never select a Runtime. The Task Detail message id is
# equally strict: 1..9 ASCII digits only (a v1.2 MESSAGE_ID is an integer).
ROUTE_REGISTRY_COLLECTION = re.compile(r"^/api/runtimes$")
ROUTE_REGISTRY_ITEM = re.compile(r"^/api/runtimes/([0-9a-f]{16})$")
ROUTE_REGISTRY_STATUS = re.compile(r"^/api/runtimes/([0-9a-f]{16})/status$")
ROUTE_REGISTRY_COCKPIT = re.compile(r"^/api/runtimes/([0-9a-f]{16})/cockpit$")
ROUTE_REGISTRY_CONTROLS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls$")
ROUTE_REGISTRY_PAUSE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/pause$")
ROUTE_REGISTRY_RESUME = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/resume$")
ROUTE_REGISTRY_INTERVENTION = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/intervention$")
ROUTE_REGISTRY_STOP_PREPARE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/stop/prepare$")
ROUTE_REGISTRY_STOP_CONFIRM = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/stop/confirm$")
ROUTE_REGISTRY_HR_PREPARE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/human-review/prepare$")
ROUTE_REGISTRY_HR_APPLY = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/controls/human-review/apply$")
ROUTE_REGISTRY_TIMELINE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/timeline$")
ROUTE_REGISTRY_ROUND = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/rounds/([0-9]{1,9})$")
ROUTE_REGISTRY_RENAME = re.compile(r"^/api/runtimes/([0-9a-f]{16})/rename$")
ROUTE_REGISTRY_REVALIDATE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/revalidate$")
# P6 Artifact Center. The artifact identifier is a SHA-256 of the normalized
# project-relative path computed by the server; a request can never carry a
# filesystem path (anything else than 64 hex characters is not an artifact).
ROUTE_REGISTRY_ARTIFACTS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/artifacts$")
ROUTE_REGISTRY_ARTIFACT_ITEM = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/artifacts/([0-9a-f]{64})$")
ROUTE_REGISTRY_ARTIFACT_PREVIEW = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/artifacts/([0-9a-f]{64})/preview$")
ROUTE_REGISTRY_ARTIFACT_RAW = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/artifacts/([0-9a-f]{64})/raw$")
ROUTE_REGISTRY_ARTIFACT_FEEDBACK = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/artifacts/feedback$")
# P7 Setup Wizard. The Runtime selection is the same opaque Registry ID; the
# wizard never accepts a filesystem path, a command, or a selector beyond the
# typed JSON schemas below.
ROUTE_REGISTRY_SETUP_STATE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/state$")
ROUTE_REGISTRY_SETUP_GOAL = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/goal$")
ROUTE_REGISTRY_SETUP_INPUTS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/inputs$")
ROUTE_REGISTRY_SETUP_SUPERVISOR = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/supervisor$")
ROUTE_REGISTRY_SETUP_ZCODE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/zcode$")
ROUTE_REGISTRY_SETUP_ZCODE_ACK = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/zcode/acknowledge$")
ROUTE_REGISTRY_SETUP_WORKSHOP = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/goal-workshop$")
ROUTE_REGISTRY_SETUP_READINESS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/readiness$")
ROUTE_REGISTRY_SETUP_START = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/setup/start$")
# P8 Supervisor-turn observability. Reads are bounded direct reads of the
# Runtime's own turn records and configuration document; the one mutation is
# delegated to the formal queue-supervisor-config subcommand.
ROUTE_REGISTRY_SUPERVISOR_TURNS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/supervisor/turns$")
ROUTE_REGISTRY_SUPERVISOR_TURN = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/supervisor/turns/([A-Za-z0-9._-]{1,120})$")
ROUTE_REGISTRY_SUPERVISOR_USAGE = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/supervisor/usage$")
ROUTE_REGISTRY_SUPERVISOR_CONFIG = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/supervisor/config$")

# P9 settings, notes, alerts, and Runtime creation. Runtime creation
# is Registry-driven and template-based only: the browser names a
# destination and a label, never a source path or a command.
ROUTE_SETTINGS = re.compile(r"^/api/settings$")
ROUTE_RUNTIME_SETTINGS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/settings$")
ROUTE_RUNTIME_NOTES = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/notes$")
ROUTE_RUNTIME_ALERTS = re.compile(
    r"^/api/runtimes/([0-9a-f]{16})/alerts$")
ROUTE_RUNTIME_TEMPLATES = re.compile(r"^/api/runtime-templates$")
ROUTE_RUNTIME_CREATE = re.compile(r"^/api/runtimes/create$")
# Console-owned settings never change authoritative Runtime state;
# Supervisor model/effort values reach a Runtime only through the
# formal P8 queue and apply at the next Supervisor turn boundary.
SETTINGS_APPLICATION_NOTE = (
    "settings are Console-owned defaults; Supervisor model/effort "
    "values reach a Runtime only through the formal queue (POST "
    "/api/runtimes/<id>/supervisor/config) and apply at the next "
    "Supervisor turn boundary")


def _parse_helper_document(stdout: str) -> dict | None:
    """Extract the one JSON object a Runtime helper printed on stdout.

    The supported helpers may emit Orchestrator log lines (e.g. a timestamped
    "Active project scope activated") before the JSON document, so the parser
    scans for the first parseable top-level JSON object and refuses anything
    it cannot bind to a single object.
    """
    decoder = json.JSONDecoder()
    for index, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(stdout[index:])
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
        return None
    return None


class ConfigError(ValueError):
    """Fail-closed startup validation error (never binds, never writes)."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def atomic_write_json(path: Path, value) -> None:
    """Write JSON atomically: unique temp file, fsync, then os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            handle.write(json_bytes(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def error_envelope(code: str, message: str, detail=None) -> dict:
    return {"schema_version": SCHEMA_VERSION, "ok": False,
            "error": {"code": code, "message": message, "detail": detail}}


def validate_bind_host(host: str) -> str:
    """P1 serves IPv4 loopback only; a non-loopback bind fails closed."""
    if host != DEFAULT_HOST:
        raise ConfigError(
            f"refusing non-loopback bind {host!r}: the Web Console listens on "
            f"{DEFAULT_HOST} only")
    return host


def validate_port(port: int) -> int:
    # Port 0 means "OS-assigned": an in-process/test convenience that is
    # rebound to the real port right after listen(); the CLI requires an
    # explicit port and never accepts 0.
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ConfigError(f"port must be an integer in 0..65535, got {port!r}")
    return port


def pid_alive(pid: int) -> bool:
    """Best-effort liveness of a process id, without touching the process."""
    pid = int(pid)
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def probe_health(port: int, timeout: float):
    """Fetch the health document from the loopback instance, or None."""
    try:
        conn = http.client.HTTPConnection(DEFAULT_HOST, int(port), timeout=timeout)
        try:
            conn.request("GET", "/api/health",
                         headers={"Host": f"{DEFAULT_HOST}:{int(port)}"})
            response = conn.getresponse()
            body = response.read()
            status = response.status
        finally:
            conn.close()
    except (OSError, ValueError):
        return None
    if status != 200:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None


def _paths_equal(left, right) -> bool:
    try:
        return Path(str(left)).resolve() == Path(str(right)).resolve()
    except OSError:
        return str(left) == str(right)


def verify_instance(data_dir: Path, runtime_root: Path,
                    health_timeout: float = 2.0) -> dict:
    """Decide whether recorded instance metadata is fresh and owned by us.

    Verdicts:
      MISSING          no metadata file; nothing is running per our records.
      INVALID          metadata exists but is unusable (corrupt/foreign schema).
      FOREIGN          metadata is bound to a different Runtime root.
      STALE            recorded pid is no longer alive (metadata may be cleaned).
      VERIFIED         recorded pid is alive and answers /api/health with the
                       exact token/pid/port/runtime-root binding recorded here.
      ALIVE_UNVERIFIED pid is alive but did not prove ownership (foreign or
                       hung process); never kill and never overwrite blindly.
    """
    data_dir = Path(data_dir)
    meta_path = data_dir / METADATA_FILE_NAME
    runtime_root = Path(runtime_root).resolve()
    if not meta_path.is_file():
        return {"verdict": "MISSING", "metadata_file": str(meta_path)}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return {"verdict": "INVALID", "metadata_file": str(meta_path),
                "reason": "instance metadata is missing or not valid JSON"}
    if (not isinstance(meta, dict)
            or meta.get("schema_version") != SCHEMA_VERSION
            or not isinstance(meta.get("pid"), int)
            or not isinstance(meta.get("port"), int)
            or not isinstance(meta.get("token"), str)
            or not isinstance(meta.get("runtime_root"), str)):
        return {"verdict": "INVALID", "metadata_file": str(meta_path),
                "reason": "instance metadata schema is not usable"}
    if not _paths_equal(meta["runtime_root"], runtime_root):
        return {"verdict": "FOREIGN", "metadata_file": str(meta_path),
                "instance": meta,
                "reason": ("instance metadata is bound to a different Runtime "
                           "root than requested")}
    if not pid_alive(meta["pid"]):
        return {"verdict": "STALE", "metadata_file": str(meta_path),
                "instance": meta,
                "reason": "recorded pid is no longer running"}
    health = probe_health(meta["port"], health_timeout)
    instance = (health or {}).get("instance") or {}
    if (health is not None
            and instance.get("token") == meta["token"]
            and instance.get("pid") == meta["pid"]
            and instance.get("port") == meta["port"]
            and _paths_equal(instance.get("runtime_root"), meta["runtime_root"])):
        return {"verdict": "VERIFIED", "metadata_file": str(meta_path),
                "instance": meta, "health": health}
    return {"verdict": "ALIVE_UNVERIFIED", "metadata_file": str(meta_path),
            "instance": meta, "health": health,
            "reason": ("recorded pid is alive but did not prove ownership of "
                       "this instance metadata")}


class ServerConfig:
    """Immutable per-instance server settings and identity."""

    def __init__(self, *, host: str, port: int, runtime_root: Path,
                 console_root: Path, data_dir: Path, control_timeout: float,
                 registry: web_console_registry.RuntimeRegistry):
        self.host = host
        self.port = port
        self.runtime_root = runtime_root
        self.console_root = console_root
        self.data_dir = data_dir
        self.control_timeout = control_timeout
        self.registry = registry
        self.pid = os.getpid()
        self.started_at = now_iso()
        self.token = secrets.token_hex(16)

    @property
    def control_script(self) -> Path:
        return self.runtime_root / CONTROL_SCRIPT_RELATIVE

    @property
    def static_index(self) -> Path:
        return self.console_root / STATIC_INDEX_RELATIVE

    @property
    def metadata_file(self) -> Path:
        return self.data_dir / METADATA_FILE_NAME

    def metadata_document(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "pid": self.pid,
                "port": self.port, "token": self.token,
                "runtime_root": str(self.runtime_root),
                "console_root": str(self.console_root),
                "started_at": self.started_at}

    def health_document(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "backend": {"status": "ok", "pid": self.pid,
                        "started_at": self.started_at,
                        "console_root": str(self.console_root),
                        "python_version": sys.version.split()[0]},
            "instance": {"token": self.token, "pid": self.pid,
                         "port": self.port,
                         "runtime_root": str(self.runtime_root),
                         "started_at": self.started_at},
            "control_plane": {
                # Health stays cheap: the structural signal distinguishes
                # backend liveness from control-plane availability without
                # spawning a subprocess. /api/status performs the real probe.
                "availability_check": "script_presence_structural",
                "script_present": self.control_script.is_file(),
                "script": CONTROL_SCRIPT_RELATIVE.as_posix(),
            },
        }


class WebConsoleServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, config: ServerConfig):
        self.config = config
        # Server-issued STOP challenges: in-memory, short-lived, single-use,
        # and never authoritative. They bind a confirmation to this Runtime's
        # opaque ID and the current project identity/state.
        self._stop_challenges = {}
        self._stop_challenge_lock = threading.RLock()
        # P7 Setup: one serialized start flow per Runtime (bootstrap and the
        # start entry point must never race), plus serialized draft writes.
        self._setup_start_locks = {}
        self._setup_start_locks_guard = threading.Lock()
        self._setup_draft_lock = threading.RLock()
        # P9: Runtime creation is fully serialized per Console so two
        # conflicting creates can never both observe an empty target.
        self._runtime_create_lock = threading.Lock()
        super().__init__(address, ConsoleRequestHandler)

    @classmethod
    def create(cls, *, host: str, port: int, runtime_root: Path,
               console_root: Path, data_dir: Path,
               control_timeout: float = 10.0) -> "WebConsoleServer":
        """Validate everything fail-closed, then bind. No writes before bind."""
        host = validate_bind_host(host)
        port = validate_port(port)
        if isinstance(control_timeout, bool) or not isinstance(control_timeout,
                                                               (int, float)) \
                or control_timeout <= 0:
            raise ConfigError("control timeout must be a positive number")
        runtime_root = Path(runtime_root).resolve()
        if not runtime_root.is_dir():
            raise ConfigError(f"Runtime root does not exist: {runtime_root}")
        console_root = Path(console_root).resolve()
        if not console_root.is_dir():
            raise ConfigError(f"console root does not exist: {console_root}")
        if not (console_root / STATIC_INDEX_RELATIVE).is_file():
            raise ConfigError(
                f"landing page is missing: {console_root / STATIC_INDEX_RELATIVE}")
        data_dir = Path(data_dir)
        if not data_dir.is_absolute():
            raise ConfigError("data directory must be an absolute path")
        registry = web_console_registry.RuntimeRegistry(
            data_dir, control_timeout=control_timeout)
        config = ServerConfig(host=host, port=port, runtime_root=runtime_root,
                              console_root=console_root, data_dir=data_dir,
                              control_timeout=control_timeout,
                              registry=registry)
        server = cls((host, port), config)
        if port == 0:
            # Adopt the OS-assigned port so Host validation, health, and
            # instance metadata all reflect the actually bound endpoint.
            server.config.port = server.server_address[1]
        return server

    def write_instance_metadata(self) -> Path:
        atomic_write_json(self.config.metadata_file,
                          self.config.metadata_document())
        return self.config.metadata_file

    def remove_own_instance_metadata(self) -> None:
        """Drop the metadata only if it still records exactly this instance."""
        path = self.config.metadata_file
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(meta, dict) and meta.get("token") == self.config.token:
            path.unlink(missing_ok=True)

    def status_result(self, runtime_root: Path | None = None,
                      runtime_meta: dict | None = None) -> tuple[int, dict]:
        """Invoke the v1.2 control plane read-only and bound its outcome.

        The root comes from the server configuration (P1 `/api/status`) or,
        for Registry-scoped status, from the Registry entry that the opaque
        route ID resolved to — never from request input. `runtime_meta`, when
        given, is echoed under `"runtime"` so the response names exactly which
        registered Runtime produced it.
        """
        config = self.config
        root = Path(runtime_root) if runtime_root is not None \
            else config.runtime_root
        # --root belongs to the top-level parser and precedes the subcommand;
        # the explicit binding removes any dependency on the server's cwd.
        command = [sys.executable, str(root / CONTROL_SCRIPT_RELATIVE),
                   "--root", str(root), "status", "--json"]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, capture_output=True, cwd=str(config.console_root),
                timeout=config.control_timeout)
        except subprocess.TimeoutExpired:
            return 504, error_envelope(
                "CONTROL_PLANE_TIMEOUT",
                f"supervisor_control status did not finish within "
                f"{config.control_timeout} seconds",
                {"timeout_seconds": config.control_timeout,
                 "runtime_root": str(root)})
        except OSError as exc:
            return 502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                f"failed to launch the v1.2 control plane: {exc}",
                {"runtime_root": str(root)})
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        detail = {"exit_code": completed.returncode, "elapsed_ms": elapsed_ms,
                  "runtime_root": str(root),
                  "stdout_head": stdout[:STDOUT_ERROR_HEAD_BYTES],
                  "stderr_head": stderr[:STDOUT_ERROR_HEAD_BYTES]}
        if completed.returncode == 0:
            try:
                document = json.loads(stdout)
            except ValueError:
                return 502, error_envelope(
                    "CONTROL_PLANE_MALFORMED_OUTPUT",
                    "supervisor_control status produced output that is not "
                    "one JSON document", detail)
            if not isinstance(document, dict):
                return 502, error_envelope(
                    "CONTROL_PLANE_MALFORMED_OUTPUT",
                    "supervisor_control status output is not a JSON object",
                    detail)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "ok": True,
                "control_plane": {"ok": True, "exit_code": 0,
                                  "elapsed_ms": elapsed_ms,
                                  "runtime_root": str(root),
                                  "status": document},
            }
            if runtime_meta is not None:
                payload["runtime"] = runtime_meta
            return 200, payload
        control_error = None
        try:
            parsed = json.loads(stdout)
            if isinstance(parsed, dict) and parsed.get("ok") is False:
                control_error = {"error": parsed.get("error"),
                                 "error_type": parsed.get("error_type")}
        except ValueError:
            pass
        detail["control_error"] = control_error
        return 502, error_envelope(
            "CONTROL_PLANE_ERROR",
            "the v1.2 control plane reported an error for the read-only "
            "status query", detail)

    def run_control(self, runtime_root: Path,
                    control_args: list) -> dict:
        """Run one bounded read-only v1.2 control query (P4 history).

        The root always comes from the Registry entry resolved by the opaque
        route ID; `control_args` is the subcommand plus its read-only flags.
        Returns a structured result; transport-level failures are coded, never
        raised, so the composition layer can surface honest partial history
        where appropriate.
        """
        config = self.config
        root = Path(runtime_root)
        command = [sys.executable, str(root / CONTROL_SCRIPT_RELATIVE),
                   "--root", str(root), *control_args]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, capture_output=True, cwd=str(config.console_root),
                timeout=config.control_timeout)
        except subprocess.TimeoutExpired:
            return {"ran": False, "exit_code": None, "document": None,
                    "failure": "TIMEOUT", "stdout_head": "", "stderr_head": "",
                    "elapsed_ms": int((time.monotonic() - started) * 1000)}
        except OSError as exc:
            return {"ran": False, "exit_code": None, "document": None,
                    "failure": "LAUNCH_FAILED",
                    "stdout_head": "", "stderr_head": str(exc)[:2000],
                    "elapsed_ms": int((time.monotonic() - started) * 1000)}
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout_raw = completed.stdout or b""
        stderr = (completed.stderr or b"").decode("utf-8", errors="replace")
        heads = {"stdout_head":
                 stdout_raw[:STDOUT_ERROR_HEAD_BYTES].decode(
                     "utf-8", errors="replace"),
                 "stderr_head": stderr[:STDOUT_ERROR_HEAD_BYTES],
                 "elapsed_ms": elapsed_ms}
        if len(stdout_raw) > MAX_CONTROL_OUTPUT_BYTES:
            return {"ran": False, "exit_code": completed.returncode,
                    "document": None, "failure": "OUTPUT_TOO_LARGE",
                    **heads}
        stdout = stdout_raw.decode("utf-8", errors="replace")
        heads["stdout_head"] = stdout[:STDOUT_ERROR_HEAD_BYTES]
        document = None
        parse_problem = None
        if stdout.strip():
            try:
                document = json.loads(stdout)
            except ValueError:
                parse_problem = "UNPARSEABLE"
        else:
            parse_problem = "UNPARSEABLE"
        if parse_problem:
            return {"ran": True, "exit_code": completed.returncode,
                    "document": None, "failure": parse_problem, **heads}
        return {"ran": True, "exit_code": completed.returncode,
                "document": document, "failure": None, **heads}

    def control_failure_envelope(self, result: dict, label: str) -> tuple:
        """Map one failed run_control result to a deterministic envelope."""
        failure = result.get("failure")
        detail = {"control_command": label,
                  "exit_code": result.get("exit_code"),
                  "elapsed_ms": result.get("elapsed_ms"),
                  "stdout_head": result.get("stdout_head"),
                  "stderr_head": result.get("stderr_head")}
        if failure == "TIMEOUT":
            return 504, error_envelope(
                "CONTROL_PLANE_TIMEOUT",
                f"the v1.2 control plane did not finish the {label} query "
                f"within {self.config.control_timeout} seconds", detail)
        if failure == "LAUNCH_FAILED":
            return 502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                f"failed to launch the v1.2 control plane for the {label} "
                f"query: {result.get('stderr_head', '')}", detail)
        if failure == "OUTPUT_TOO_LARGE":
            return 502, error_envelope(
                "CONTROL_PLANE_OUTPUT_TOO_LARGE",
                f"the {label} query produced more than "
                f"{MAX_CONTROL_OUTPUT_BYTES} bytes; refusing to relay it",
                detail)
        document = result.get("document")
        if isinstance(document, dict) and document.get("ok") is False:
            detail["control_error"] = {"error": document.get("error"),
                                       "error_type":
                                           document.get("error_type")}
        return 502, error_envelope(
            "CONTROL_PLANE_MALFORMED_OUTPUT" if failure else
            "CONTROL_PLANE_ERROR",
            f"the v1.2 control plane reported an error for the {label} "
            "query", detail)


class BodyError(Exception):
    """A request body that fails closed before any Registry operation runs."""

    def __init__(self, status: int, code: str, message: str,
                 close: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.close = close


class ConsoleRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = SERVER_VERSION

    def version_string(self) -> str:
        return SERVER_VERSION

    @property
    def config(self) -> ServerConfig:
        return self.server.config

    def log_message(self, fmt, *args):
        logging.info("%s %s", self.address_string(), fmt % args)

    def finish(self):
        # RFC 7230 6.6 "drain then close": when this connection ends after we
        # refused a request whose body we did not read (e.g. 413), half-close
        # the write side so the client reliably receives the response, then
        # discard any remaining request bytes briefly so the final close does
        # not RST the peer's pending receive. Keep-alive paths are unchanged.
        if getattr(self, "close_connection", False):
            try:
                self.connection.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            deadline = time.monotonic() + 1.0
            try:
                self.connection.settimeout(
                    max(0.01, deadline - time.monotonic()))
                while time.monotonic() < deadline:
                    if not self.connection.recv(65536):
                        break
            except OSError:
                pass
        super().finish()

    # -- method allowlist ---------------------------------------------------

    def do_GET(self):
        self._route(with_body=True)

    def do_HEAD(self):
        self._route(with_body=False)

    def do_POST(self):
        self._route(with_body=True, method="POST")

    def do_DELETE(self):
        self._route(with_body=True, method="DELETE")

    def do_PUT(self):
        self._method_not_allowed()

    def do_PATCH(self):
        self._method_not_allowed()

    def do_OPTIONS(self):
        self._method_not_allowed()

    # -- request handling ---------------------------------------------------

    def _host_problem(self) -> tuple[int, dict] | None:
        host = self.headers.get("Host")
        if host is None or not host.strip():
            return 400, error_envelope(
                "HOST_HEADER_REQUIRED",
                "an HTTP/1.1 Host header is required")
        allowed = {DEFAULT_HOST, f"{DEFAULT_HOST}:{self.config.port}"}
        if host.strip().lower() not in allowed:
            return 403, error_envelope(
                "HOST_HEADER_REJECTED",
                f"Host header {host.strip()!r} is not approved for this "
                "localhost-only console")
        return None

    def _route(self, *, with_body: bool, method: str = "GET"):
        problem = self._host_problem()
        if problem is not None:
            self._send_json(*problem, with_body=with_body)
            return
        path = urllib.parse.urlsplit(self.path).path
        if method == "GET":
            self._route_get(path, with_body=with_body)
        elif method == "POST":
            self._route_post(path)
        elif method == "DELETE":
            self._route_delete(path)
        else:
            self._method_not_allowed()

    def _route_get(self, path: str, *, with_body: bool):
        if path in ("/", "/index.html"):
            self._serve_index(with_body=with_body)
        elif path == "/api/health":
            self._send_json(200, self.config.health_document(),
                            with_body=with_body)
        elif path == "/api/status":
            status, payload = self.server.status_result()
            self._send_json(status, payload, with_body=with_body)
        else:
            match = ROUTE_REGISTRY_TIMELINE.match(path)
            if match:
                query = urllib.parse.urlsplit(self.path).query
                self._registry_timeline(match.group(1), query,
                                        with_body=with_body)
                return
            match = ROUTE_REGISTRY_ARTIFACTS.match(path)
            if match:
                query = urllib.parse.urlsplit(self.path).query
                self._registry_artifacts(match.group(1), query,
                                         with_body=with_body)
                return
            match = ROUTE_REGISTRY_ARTIFACT_PREVIEW.match(path)
            if match:
                self._registry_artifact_preview(match.group(1), match.group(2),
                                                with_body=with_body)
                return
            match = ROUTE_REGISTRY_ARTIFACT_RAW.match(path)
            if match:
                self._registry_artifact_raw(match.group(1), match.group(2),
                                            with_body=with_body)
                return
            match = ROUTE_REGISTRY_ARTIFACT_ITEM.match(path)
            if match:
                self._registry_artifact_item(match.group(1), match.group(2),
                                             with_body=with_body)
                return
            if ROUTE_REGISTRY_ARTIFACT_FEEDBACK.match(path):
                self._drain_request_body()
                self._send_json(
                    405, error_envelope(
                        "METHOD_NOT_ALLOWED",
                        "artifact feedback accepts POST only"),
                    with_body=with_body, extra_headers=[("Allow", "POST")])
                return
            match = ROUTE_REGISTRY_ROUND.match(path)
            if match:
                self._registry_round(match.group(1), match.group(2),
                                     with_body=with_body)
                return
            match = ROUTE_REGISTRY_STATUS.match(path)
            if match:
                self._registry_status(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_COCKPIT.match(path)
            if match:
                self._registry_cockpit(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_CONTROLS.match(path)
            if match:
                self._registry_controls(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_ITEM.match(path)
            if match:
                self._registry_get(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_SETUP_STATE.match(path)
            if match:
                self._setup_state(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_SETUP_ZCODE.match(path)
            if match:
                self._setup_zcode(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_SETUP_WORKSHOP.match(path)
            if match:
                self._setup_workshop(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_SETUP_READINESS.match(path)
            if match:
                self._setup_readiness(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_SUPERVISOR_TURN.match(path)
            if match:
                self._supervisor_turn_detail(match.group(1), match.group(2),
                                             with_body=with_body)
                return
            match = ROUTE_REGISTRY_SUPERVISOR_TURNS.match(path)
            if match:
                query = urllib.parse.urlsplit(self.path).query
                self._supervisor_turns(match.group(1), query,
                                       with_body=with_body)
                return
            match = ROUTE_REGISTRY_SUPERVISOR_USAGE.match(path)
            if match:
                self._supervisor_usage(match.group(1), with_body=with_body)
                return
            match = ROUTE_REGISTRY_SUPERVISOR_CONFIG.match(path)
            if match:
                self._supervisor_config_get(match.group(1),
                                            with_body=with_body)
                return
            match = ROUTE_RUNTIME_SETTINGS.match(path)
            if match:
                self._runtime_settings_get(match.group(1),
                                           with_body=with_body)
                return
            match = ROUTE_RUNTIME_NOTES.match(path)
            if match:
                self._runtime_notes_get(match.group(1),
                                        with_body=with_body)
                return
            match = ROUTE_RUNTIME_ALERTS.match(path)
            if match:
                self._runtime_alerts_get(match.group(1),
                                         with_body=with_body)
                return
            if ROUTE_RUNTIME_TEMPLATES.match(path):
                self._runtime_templates_get(with_body=with_body)
                return
            if ROUTE_SETTINGS.match(path):
                self._settings_get(with_body=with_body)
                return
            if ROUTE_REGISTRY_COLLECTION.match(path):
                self._registry_list(with_body=with_body)
                return
            if self._is_setup_mutation_path(path):
                self._drain_request_body()
                self._send_json(
                    405, error_envelope(
                        "METHOD_NOT_ALLOWED",
                        "this Setup Wizard operation accepts POST only"),
                    with_body=with_body, extra_headers=[("Allow", "POST")])
                return
            if self._is_control_mutation_path(path):
                self._drain_request_body()
                self._send_json(
                    405, error_envelope(
                        "METHOD_NOT_ALLOWED",
                        "this Human Control operation accepts POST only"),
                    with_body=with_body, extra_headers=[("Allow", "POST")])
                return
            if ROUTE_RUNTIME_CREATE.match(path):
                self._drain_request_body()
                self._send_json(
                    405, error_envelope(
                        "METHOD_NOT_ALLOWED",
                        "Runtime creation accepts POST only"),
                    with_body=with_body, extra_headers=[("Allow", "POST")])
                return
            if ROUTE_RUNTIME_TEMPLATES.match(path):
                self._drain_request_body()
                self._send_json(
                    405, error_envelope(
                        "METHOD_NOT_ALLOWED",
                        "the template catalog accepts GET/HEAD only"),
                    with_body=with_body,
                    extra_headers=[("Allow", "GET, HEAD")])
                return
            if (ROUTE_REGISTRY_RENAME.match(path)
                    or ROUTE_REGISTRY_REVALIDATE.match(path)):
                self._drain_request_body()
                self._send_json(
                    405, error_envelope(
                        "METHOD_NOT_ALLOWED",
                        "this Registry operation accepts POST only"),
                    with_body=with_body, extra_headers=[("Allow", "POST")])
                return
            self._send_json(404, error_envelope(
                "ROUTE_NOT_FOUND",
                f"no route exists at {path!r}"), with_body=with_body)

    @staticmethod
    def _is_control_mutation_path(path: str) -> bool:
        return any(pattern.match(path) for pattern in (
            ROUTE_REGISTRY_PAUSE, ROUTE_REGISTRY_RESUME,
            ROUTE_REGISTRY_INTERVENTION, ROUTE_REGISTRY_STOP_PREPARE,
            ROUTE_REGISTRY_STOP_CONFIRM, ROUTE_REGISTRY_HR_PREPARE,
            ROUTE_REGISTRY_HR_APPLY))

    @staticmethod
    def _is_setup_mutation_path(path: str) -> bool:
        return any(pattern.match(path) for pattern in (
            ROUTE_REGISTRY_SETUP_GOAL, ROUTE_REGISTRY_SETUP_INPUTS,
            ROUTE_REGISTRY_SETUP_SUPERVISOR, ROUTE_REGISTRY_SETUP_ZCODE_ACK,
            ROUTE_REGISTRY_SETUP_START))

    @staticmethod
    def _is_setup_read_path(path: str) -> bool:
        return any(pattern.match(path) for pattern in (
            ROUTE_REGISTRY_SETUP_STATE, ROUTE_REGISTRY_SETUP_ZCODE,
            ROUTE_REGISTRY_SETUP_WORKSHOP, ROUTE_REGISTRY_SETUP_READINESS))

    @staticmethod
    def _is_supervisor_read_path(path: str) -> bool:
        return any(pattern.match(path) for pattern in (
            ROUTE_REGISTRY_SUPERVISOR_TURNS, ROUTE_REGISTRY_SUPERVISOR_TURN,
            ROUTE_REGISTRY_SUPERVISOR_USAGE, ROUTE_REGISTRY_SUPERVISOR_CONFIG))

    def _route_post(self, path: str):
        match = ROUTE_REGISTRY_COLLECTION.match(path)
        if match:
            self._registry_add()
            return
        match = ROUTE_RUNTIME_CREATE.match(path)
        if match:
            self._runtime_create_post()
            return
        match = ROUTE_SETTINGS.match(path)
        if match:
            self._settings_post()
            return
        match = ROUTE_RUNTIME_SETTINGS.match(path)
        if match:
            self._runtime_settings_post(match.group(1))
            return
        match = ROUTE_RUNTIME_NOTES.match(path)
        if match:
            self._runtime_notes_post(match.group(1))
            return
        if ROUTE_RUNTIME_TEMPLATES.match(path):
            self._drain_request_body()
            self._send_json(
                405, error_envelope(
                    "METHOD_NOT_ALLOWED",
                    "the template catalog accepts GET/HEAD only"),
                extra_headers=[("Allow", "GET, HEAD")])
            return
        if ROUTE_RUNTIME_ALERTS.match(path):
            self._drain_request_body()
            self._send_json(
                405, error_envelope(
                    "METHOD_NOT_ALLOWED",
                    "the alerts document accepts GET/HEAD only"),
                extra_headers=[("Allow", "GET, HEAD")])
            return
        match = ROUTE_REGISTRY_SUPERVISOR_CONFIG.match(path)
        if match:
            self._supervisor_config_post(match.group(1))
            return
        if self._is_supervisor_read_path(path):
            self._drain_request_body()
            self._send_json(
                405, error_envelope(
                    "METHOD_NOT_ALLOWED",
                    "Supervisor observability documents accept GET/HEAD "
                    "only"),
                extra_headers=[("Allow", "GET, HEAD")])
            return
        match = ROUTE_REGISTRY_SETUP_GOAL.match(path)
        if match:
            self._setup_goal(match.group(1))
            return
        match = ROUTE_REGISTRY_SETUP_INPUTS.match(path)
        if match:
            self._setup_inputs(match.group(1))
            return
        match = ROUTE_REGISTRY_SETUP_SUPERVISOR.match(path)
        if match:
            self._setup_supervisor(match.group(1))
            return
        match = ROUTE_REGISTRY_SETUP_ZCODE_ACK.match(path)
        if match:
            self._setup_zcode_acknowledge(match.group(1))
            return
        match = ROUTE_REGISTRY_SETUP_START.match(path)
        if match:
            self._setup_start(match.group(1))
            return
        if self._is_setup_read_path(path):
            self._drain_request_body()
            self._send_json(
                405, error_envelope(
                    "METHOD_NOT_ALLOWED",
                    "this Setup Wizard document accepts GET/HEAD only"),
                extra_headers=[("Allow", "GET, HEAD")])
            return
        match = ROUTE_REGISTRY_RENAME.match(path)
        if match:
            self._registry_rename(match.group(1))
            return
        match = ROUTE_REGISTRY_REVALIDATE.match(path)
        if match:
            self._registry_revalidate(match.group(1))
            return
        match = ROUTE_REGISTRY_PAUSE.match(path)
        if match:
            self._control_pause(match.group(1))
            return
        match = ROUTE_REGISTRY_RESUME.match(path)
        if match:
            self._control_resume(match.group(1))
            return
        match = ROUTE_REGISTRY_INTERVENTION.match(path)
        if match:
            self._control_intervention(match.group(1))
            return
        match = ROUTE_REGISTRY_STOP_PREPARE.match(path)
        if match:
            self._control_stop_prepare(match.group(1))
            return
        match = ROUTE_REGISTRY_STOP_CONFIRM.match(path)
        if match:
            self._control_stop_confirm(match.group(1))
            return
        match = ROUTE_REGISTRY_HR_PREPARE.match(path)
        if match:
            self._control_hr_prepare(match.group(1))
            return
        match = ROUTE_REGISTRY_HR_APPLY.match(path)
        if match:
            self._control_hr_apply(match.group(1))
            return
        match = ROUTE_REGISTRY_ARTIFACT_FEEDBACK.match(path)
        if match:
            self._registry_artifact_feedback(match.group(1))
            return
        match = ROUTE_REGISTRY_CONTROLS.match(path)
        if match:
            self._drain_request_body()
            self._send_json(
                405, error_envelope(
                    "METHOD_NOT_ALLOWED",
                    "the Pending Controls document accepts GET/HEAD only"),
                extra_headers=[("Allow", "GET, HEAD")])
            return
        if (ROUTE_REGISTRY_ARTIFACTS.match(path)
                or ROUTE_REGISTRY_ARTIFACT_ITEM.match(path)
                or ROUTE_REGISTRY_ARTIFACT_PREVIEW.match(path)
                or ROUTE_REGISTRY_ARTIFACT_RAW.match(path)):
            self._drain_request_body()
            self._send_json(
                405, error_envelope(
                    "METHOD_NOT_ALLOWED",
                    "Artifact Center read routes accept GET/HEAD only"),
                extra_headers=[("Allow", "GET, HEAD")])
            return
        if "/controls" in path:
            # A control-shaped path that matched no valid route is not a
            # valid Runtime ID or a valid action; it fails closed as 404
            # rather than a method error.
            self._drain_request_body()
            self._send_json(404, error_envelope(
                "ROUTE_NOT_FOUND",
                f"no Human Control route exists at {path!r}"))
            return
        self._method_not_allowed()

    def _route_delete(self, path: str):
        match = ROUTE_REGISTRY_ITEM.match(path)
        if match:
            self._registry_remove(match.group(1))
            return
        if (ROUTE_SETTINGS.match(path)
                or ROUTE_RUNTIME_SETTINGS.match(path)
                or ROUTE_RUNTIME_NOTES.match(path)
                or ROUTE_RUNTIME_ALERTS.match(path)
                or ROUTE_RUNTIME_TEMPLATES.match(path)
                or ROUTE_RUNTIME_CREATE.match(path)):
            self._drain_request_body()
            self._send_json(405, error_envelope(
                "METHOD_NOT_ALLOWED",
                "settings, alerts, and Runtime creation routes do not accept DELETE"),
                extra_headers=[("Allow", "GET, HEAD, POST")])
            return
        if (ROUTE_REGISTRY_SUPERVISOR_CONFIG.match(path)
                or self._is_supervisor_read_path(path)):
            self._drain_request_body()
            self._send_json(405, error_envelope(
                "METHOD_NOT_ALLOWED",
                "Supervisor observability routes do not accept DELETE"),
                extra_headers=[("Allow", "GET, HEAD, POST")])
            return
        self._method_not_allowed()

    def _method_not_allowed(self):
        self._drain_request_body()
        self._send_json(405, error_envelope(
            "METHOD_NOT_ALLOWED",
            "this route does not accept the requested method"),
            with_body=True, extra_headers=[("Allow", "GET, HEAD")])

    def _drain_request_body(self):
        """Keep the connection consistent when refusing a carried body."""
        header = self.headers.get("Content-Length")
        try:
            length = int(header) if header else 0
        except ValueError:
            length = 0
        if length <= 0:
            return
        if length <= MAX_BODY_BYTES:
            self.rfile.read(length)
        else:
            self.close_connection = True

    def _read_json_body(self, *, allow_empty: bool = False,
                        max_bytes: int = MAX_BODY_BYTES) -> dict:
        if self.headers.get("Transfer-Encoding"):
            raise BodyError(
                400, "TRANSFER_ENCODING_UNSUPPORTED",
                "request bodies must be sent with Content-Length, not "
                "chunked transfer encoding")
        header = self.headers.get("Content-Length")
        if header is None:
            if allow_empty:
                return {}
            raise BodyError(
                411, "LENGTH_REQUIRED",
                "a JSON request body with Content-Length is required")
        try:
            length = int(header)
        except (TypeError, ValueError):
            raise BodyError(
                400, "REGISTRY_INVALID_JSON",
                "Content-Length is not a valid integer") from None
        if length < 0:
            raise BodyError(
                400, "REGISTRY_INVALID_JSON",
                "Content-Length must not be negative")
        if length > max_bytes:
            raise BodyError(
                413, "PAYLOAD_TOO_LARGE",
                f"request bodies are limited to {max_bytes} bytes",
                close=True)
        body = self.rfile.read(length) if length else b""
        if len(body) != length:
            raise BodyError(
                400, "REGISTRY_INVALID_JSON",
                "the request body was shorter than its Content-Length",
                close=True)
        if not body:
            if allow_empty:
                return {}
            raise BodyError(
                400, "REGISTRY_INVALID_JSON", "a JSON request body is required")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise BodyError(
                400, "REGISTRY_INVALID_JSON",
                "the request body is not one UTF-8 JSON document") from None
        if not isinstance(payload, dict):
            raise BodyError(
                400, "REGISTRY_INVALID_PAYLOAD",
                "the request body must be a JSON object")
        return payload

    def _send_body_error(self, exc: BodyError):
        if exc.close:
            self.close_connection = True
        self._send_json(exc.status, error_envelope(exc.code, exc.message))

    # -- Runtime Registry operations ----------------------------------------

    def _registry_error(self, exc: web_console_registry.RegistryError):
        self._send_json(exc.http_status,
                        error_envelope(exc.code, exc.message, exc.detail))

    def _registry_add(self):
        try:
            payload = self._read_json_body()
        except BodyError as exc:
            self._send_body_error(exc)
            return
        if (set(payload) != {"root", "label"}
                or not isinstance(payload["root"], str)
                or not isinstance(payload["label"], str)):
            self._send_json(400, error_envelope(
                "REGISTRY_INVALID_PAYLOAD",
                'add requires exactly {"root": "<absolute path>", '
                '"label": "<string>"}'))
            return
        try:
            entry = self.config.registry.add(payload["root"], payload["label"])
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        self._send_json(201, {"schema_version": SCHEMA_VERSION, "ok": True,
                              "runtime": entry})

    def _registry_list(self, *, with_body: bool):
        try:
            entries = self.config.registry.list_entries()
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        self._send_json(200, {"schema_version": SCHEMA_VERSION, "ok": True,
                              "count": len(entries), "runtimes": entries},
                        with_body=with_body)

    def _registry_get(self, runtime_id: str, *, with_body: bool):
        try:
            entry = self.config.registry.get(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        self._send_json(200, {"schema_version": SCHEMA_VERSION, "ok": True,
                              "runtime": entry}, with_body=with_body)

    def _registry_rename(self, runtime_id: str):
        try:
            payload = self._read_json_body()
        except BodyError as exc:
            self._send_body_error(exc)
            return
        if set(payload) != {"label"} or not isinstance(payload["label"], str):
            self._send_json(400, error_envelope(
                "REGISTRY_INVALID_PAYLOAD",
                'rename requires exactly {"label": "<string>"}'))
            return
        try:
            entry = self.config.registry.rename(runtime_id, payload["label"])
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        self._send_json(200, {"schema_version": SCHEMA_VERSION, "ok": True,
                              "runtime": entry})

    def _registry_revalidate(self, runtime_id: str):
        try:
            payload = self._read_json_body(allow_empty=True)
        except BodyError as exc:
            self._send_body_error(exc)
            return
        if payload:
            self._send_json(400, error_envelope(
                "REGISTRY_INVALID_PAYLOAD",
                "revalidate takes no parameters; send {} or no body"))
            return
        try:
            entry = self.config.registry.revalidate(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        self._send_json(200, {"schema_version": SCHEMA_VERSION, "ok": True,
                              "runtime": entry})

    def _registry_remove(self, runtime_id: str):
        self._drain_request_body()
        try:
            entry = self.config.registry.remove(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        self._send_json(200, {"schema_version": SCHEMA_VERSION, "ok": True,
                              "removed": entry})

    def _registry_status(self, runtime_id: str, *, with_body: bool):
        try:
            entry = self.config.registry.get(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        # The subprocess root comes from the Registry entry only; the request
        # cannot name, influence, or combine a filesystem path here.
        meta = {"id": entry["id"], "label": entry["label"],
                "root": entry["root"]}
        status, payload = self.server.status_result(
            runtime_root=Path(entry["root"]), runtime_meta=meta)
        self._send_json(status, payload, with_body=with_body)

    def _registry_timeline(self, runtime_id: str, query: str, *,
                           with_body: bool):
        """Bounded, paginated round history for one registered Runtime (P4).

        The query string is validated fail-closed before any control-plane
        call; it can never name a Runtime root, a file path, or a command.
        """
        try:
            entry = self.config.registry.get(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        try:
            params = web_console_history.parse_timeline_query(query)
        except web_console_history.TimelineQueryError as exc:
            self._send_json(400, error_envelope(
                "INVALID_QUERY_PARAM",
                "the timeline query was rejected; every parameter must be "
                "within its documented bounds",
                {"parameter_error": str(exc)}))
            return
        root = Path(entry["root"])
        result = self.server.run_control(root, ["timeline", "--json"])
        if result["failure"] or result["exit_code"] != 0:
            status, envelope = self.server.control_failure_envelope(
                result, "timeline")
            self._send_json(status, envelope, with_body=with_body)
            return
        document = result["document"]
        if not isinstance(document, list):
            # The v1.2 timeline command answers with one JSON list; anything
            # else is malformed output and fails closed.
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_MALFORMED_OUTPUT",
                "the timeline query produced output that is not one JSON "
                "list", {"control_command": "timeline",
                         "exit_code": result["exit_code"]}),
                with_body=with_body)
            return
        meta = {"id": entry["id"], "label": entry["label"],
                "root": entry["root"]}
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "runtime": meta,
            "timeline": web_console_history.project_timeline(
                document, params, generated_at=now_iso()),
        }, with_body=with_body)

    def _decision_block(self, root: Path, dispatch_record: dict) -> dict:
        """Compose the Supervisor decision block for one round (P4).

        One bounded server-side read. The receipt path is derived only from
        the dispatch archive record's own verified fields; the browser can
        never influence it. The receipt is presented only when its canonical
        SHA-256 matches the hash recorded in the archive metadata and its
        candidate binding matches the dispatch identity.
        """
        unavailable = {"available": False, "verified": False, "reason": None,
                       "turn_id": None, "committed_at": None,
                       "resulting_status": None, "decision_decision": None,
                       "decision_reason": None, "decision_history_index": None,
                       "intervention_ids": None,
                       "originating_control_revision": None}
        if not isinstance(dispatch_record, dict) \
                or dispatch_record.get("integrity") != "AUTHORIZED_VALID":
            unavailable["reason"] = "SUPERVISOR_ORIGIN_UNAVAILABLE"
            return unavailable
        turn_id = dispatch_record.get("supervisor_turn_id")
        receipt_hash = dispatch_record.get("decision_receipt_sha256")
        if not isinstance(turn_id, str) or not TURN_ID_PATTERN.fullmatch(turn_id):
            unavailable["reason"] = "SUPERVISOR_TURN_ID_INVALID"
            return unavailable
        if not isinstance(receipt_hash, str) or not HEX64.fullmatch(receipt_hash):
            unavailable["reason"] = "SUPERVISOR_ORIGIN_UNAVAILABLE"
            return unavailable
        receipt_path = root / "control" / "supervisor_decisions" / \
            f"{turn_id}.json"
        try:
            if not receipt_path.is_file():
                unavailable["reason"] = "DECISION_RECEIPT_MISSING"
                return unavailable
            raw = receipt_path.read_bytes()
            if len(raw) > MAX_DECISION_RECEIPT_BYTES:
                unavailable["reason"] = "DECISION_RECEIPT_TOO_LARGE"
                return unavailable
            receipt = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            unavailable["reason"] = "DECISION_RECEIPT_UNAVAILABLE"
            return unavailable
        if not isinstance(receipt, dict):
            unavailable["reason"] = "DECISION_RECEIPT_INVALID"
            return unavailable
        # Canonical form must match v1.2 supervisor_control.canonical_json_bytes.
        canonical = (json.dumps(receipt, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")) + "\n").encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != receipt_hash:
            unavailable["reason"] = "DECISION_RECEIPT_HASH_MISMATCH"
            return unavailable
        candidate = receipt.get("candidate")
        identity_ok = isinstance(candidate, dict) and all(
            candidate.get(key) == dispatch_record.get(key)
            for key in ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT",
                        "NONCE")) \
            and candidate.get("dispatch_sha256") == \
            dispatch_record.get("dispatch_sha256")
        if receipt.get("turn_id") != turn_id or not identity_ok:
            unavailable["reason"] = "DECISION_RECEIPT_BINDING_INVALID"
            return unavailable
        decision = receipt.get("decision") \
            if isinstance(receipt.get("decision"), dict) else {}
        revision = receipt.get("originating_control_revision")
        return {
            "available": True,
            "verified": True,
            "reason": None,
            "turn_id": turn_id,
            "committed_at": receipt.get("committed_at")
            if isinstance(receipt.get("committed_at"), str) else None,
            "resulting_status": receipt.get("resulting_status")
            if isinstance(receipt.get("resulting_status"), str) else None,
            "decision_decision": decision.get("decision")
            if isinstance(decision.get("decision"), str) else None,
            "decision_reason": decision.get("reason")
            if isinstance(decision.get("reason"), str) else None,
            "decision_history_index": receipt.get("decision_history_index")
            if (isinstance(receipt.get("decision_history_index"), int)
                and not isinstance(receipt.get("decision_history_index"),
                                   bool)) else None,
            "intervention_ids": receipt.get("intervention_ids")
            if isinstance(receipt.get("intervention_ids"), list) else None,
            "originating_control_revision": revision
            if (isinstance(revision, int)
                and not isinstance(revision, bool)) else None,
        }

    def _registry_round(self, runtime_id: str, message_id_text: str, *,
                        with_body: bool):
        """Read-only Task Detail document for one round (P4).

        The message id arrives pre-validated by the route pattern (1..9 ASCII
        digits) and the Runtime root comes from the Registry entry only.
        """
        try:
            entry = self.config.registry.get(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        message_id = int(message_id_text)
        root = Path(entry["root"])
        dispatch_result = self.server.run_control(
            root, ["tasks", "--message-id", str(message_id), "--json"])
        completion_result = self.server.run_control(
            root, ["feedback", "--message-id", str(message_id), "--json"])
        interventions_result = self.server.run_control(
            root, ["interventions", "--json"])
        # If every history source failed at the transport level there is no
        # honest round to compose; surface the deterministic failure instead.
        if all(result["failure"] for result in
               (dispatch_result, completion_result, interventions_result)):
            status, envelope = self.server.control_failure_envelope(
                dispatch_result, "round history")
            self._send_json(status, envelope, with_body=with_body)
            return
        dispatch_document = dispatch_result.get("document")
        decision = {"available": False, "verified": False, "reason": None,
                    "turn_id": None, "committed_at": None,
                    "resulting_status": None, "decision_decision": None,
                    "decision_reason": None, "decision_history_index": None,
                    "intervention_ids": None,
                    "originating_control_revision": None}
        if isinstance(dispatch_document, dict) \
                and dispatch_document.get("ok") is not False:
            decision = self._decision_block(root, dispatch_document)
        detail = web_console_history.interpret_round_detail(
            message_id,
            dispatch_result=dispatch_result,
            completion_result=completion_result,
            interventions_result=interventions_result,
            decision=decision)
        meta = {"id": entry["id"], "label": entry["label"],
                "root": entry["root"]}
        if not detail["found"]:
            self._send_json(404, error_envelope(
                "ROUND_NOT_FOUND",
                "no authoritative history exists for this MESSAGE_ID in this "
                "Runtime",
                {"honesty": detail["honesty"]}), with_body=with_body)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "runtime": meta,
            "round": detail,
        }, with_body=with_body)

    def _registry_cockpit(self, runtime_id: str, *, with_body: bool):
        try:
            entry = self.config.registry.get(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        meta = {"id": entry["id"], "label": entry["label"],
                "root": entry["root"]}
        status, payload = self.server.status_result(
            runtime_root=Path(entry["root"]), runtime_meta=meta)
        if status != 200:
            # A control plane that did not answer coherently never produces a
            # cockpit interpretation; the deterministic error envelope is the
            # fail-closed presentation.
            self._send_json(status, payload, with_body=with_body)
            return
        control = payload["control_plane"]
        interpretation = web_console_state.interpret_status(control["status"])
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION,
            "ok": True,
            "runtime": meta,
            "cockpit": {
                "schema_version": SCHEMA_VERSION,
                "source": {
                    "kind": "supervisor_control_status",
                    "status_schema_version": control["status"].get(
                        "schema_version")
                    if isinstance(control.get("status"), dict) else None,
                    "exit_code": control.get("exit_code"),
                    "elapsed_ms": control.get("elapsed_ms"),
                    "runtime_root": control.get("runtime_root"),
                },
                "runtime": meta,
                "backend": {"status": "ok", "pid": self.config.pid,
                            "started_at": self.config.started_at},
                "generated_at": now_iso(),
                "interpretation": interpretation,
            },
        }, with_body=with_body)

    # -- P5 Human Control -----------------------------------------------------

    def _resolved_entry(self, runtime_id: str):
        """Resolve the opaque ID once; every subsequent operation stays bound
        to this exact Registry entry's root."""
        try:
            entry = self.config.registry.get(runtime_id)
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return None
        return entry, {"id": entry["id"], "label": entry["label"],
                       "root": entry["root"]}

    def _control_status(self, root: Path, meta: dict):
        """Run the bounded read-only status probe for a control route."""
        status, payload = self.server.status_result(
            runtime_root=root, runtime_meta=meta)
        if status != 200:
            self._send_json(status, payload)
            return None
        return payload["control_plane"]["status"]

    def _schema_error(self, exc: web_console_control.ControlRequestError):
        self._send_json(400, error_envelope(
            "CONTROL_INVALID_PAYLOAD",
            "the request body was rejected; it must match the documented "
            "typed schema for this control",
            {"field": exc.field, "reason": str(exc)}))

    def _mutation_response(self, result: dict, label: str, meta: dict):
        failure = result.get("failure")
        document = result.get("document")
        detail = {"control_command": label,
                  "exit_code": result.get("exit_code"),
                  "elapsed_ms": result.get("elapsed_ms")}
        if failure == "TIMEOUT":
            return 504, error_envelope(
                "CONTROL_PLANE_TIMEOUT",
                f"the v1.2 control plane did not finish the {label} action "
                f"within {self.config.control_timeout} seconds", detail)
        if failure == "LAUNCH_FAILED":
            return 502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                f"failed to launch the v1.2 control plane for the {label} "
                f"action: {result.get('stderr_head', '')}", detail)
        if failure == "OUTPUT_TOO_LARGE":
            return 502, error_envelope(
                "CONTROL_PLANE_OUTPUT_TOO_LARGE",
                f"the {label} action produced more than "
                f"{MAX_CONTROL_OUTPUT_BYTES} bytes; refusing to relay it",
                detail)
        if failure == "UNPARSEABLE":
            return 502, error_envelope(
                "CONTROL_PLANE_MALFORMED_OUTPUT",
                f"the {label} action produced output that is not one JSON "
                "document", detail)
        if isinstance(document, dict) and document.get("ok") is False:
            detail["control_error"] = {"error": document.get("error"),
                                       "error_type":
                                           document.get("error_type")}
            return 409, error_envelope(
                "CONTROL_ACTION_REFUSED",
                f"the v1.2 control plane refused the {label} action", detail)
        if failure is not None or result.get("exit_code") != 0 \
                or not isinstance(document, dict):
            detail["stdout_head"] = result.get("stdout_head")
            detail["stderr_head"] = result.get("stderr_head")
            return 502, error_envelope(
                "CONTROL_PLANE_ERROR",
                f"the v1.2 control plane reported an error for the {label} "
                "action", detail)
        if label == "resume" and (
                not isinstance(document.get("startup"), dict)
                or document["startup"].get("verified") is not True
                or document["startup"].get("status") not in {"READY", "EXISTING_OWNER"}):
            return 502, error_envelope(
                "RESUME_UNVERIFIED", "Runtime did not prove scheduler startup", detail)
        return 200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "control": {"command": label, "exit_code": result["exit_code"],
                        "elapsed_ms": result["elapsed_ms"],
                        "result": document}}

    def _read_request_body(self):
        try:
            return self._read_json_body(), None
        except BodyError as exc:
            self._send_body_error(exc)
            return None, exc

    def _control_pause(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            request = web_console_control.validate_pause_request(payload)
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        control_args = ["pause"]
        if request["mode"] == "INTERRUPT_CURRENT":
            control_args.append("--interrupt-current-task")
        control_args.append("--json")
        result = self.server.run_control(Path(entry["root"]), control_args)
        status, response = self._mutation_response(result, "pause", meta)
        self._send_json(status, response)

    def _control_resume(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            web_console_control.validate_resume_request(payload)
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        result = self.server.run_control(Path(entry["root"]),
                                         ["resume", "--json"])
        status, response = self._mutation_response(result, "resume", meta)
        self._send_json(status, response)

    def _submit_intervention(self, entry, meta, request) -> tuple:
        """Delegate one validated intervention to the formal v1.2 entry point.

        Shared by the P5 intervention route and the P6 artifact-feedback
        route; the request schema is identical ({mode, comment,
        target_message_id, interrupt_current}) and the Runtime decides.
        """
        # The comment rides in one --text=<value> token, so a leading dash can
        # never be parsed as another option; there is no shell involved.
        control_args = ["intervene", f"--text={request['comment']}",
                        f"--mode={request['mode']}"]
        if request["target_message_id"] is not None:
            control_args.append(
                f"--target-message-id={request['target_message_id']}")
        if request["interrupt_current"]:
            control_args.append("--interrupt-current-task")
        control_args.append("--json")
        result = self.server.run_control(Path(entry["root"]), control_args)
        return self._mutation_response(result, "intervention", meta)

    def _control_intervention(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            request = web_console_control.validate_intervention_request(
                payload)
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        status, response = self._submit_intervention(entry, meta, request)
        self._send_json(status, response)

    def _project_state_hash(self, root: Path, project_id) -> str | None:
        if (not isinstance(project_id, str)
                or not PROJECT_ID_PATTERN.fullmatch(project_id)):
            return None
        path = root / "projects" / project_id / "project_state.json"
        try:
            if not path.is_file():
                return None
            raw = path.read_bytes()
        except OSError:
            return None
        if len(raw) > MAX_PROJECT_STATE_BYTES:
            return None
        return hashlib.sha256(raw).hexdigest()

    def _control_stop_prepare(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            payload = self._read_json_body(allow_empty=True)
        except BodyError as exc:
            self._send_body_error(exc)
            return
        if payload:
            self._send_json(400, error_envelope(
                "CONTROL_INVALID_PAYLOAD",
                "stop/prepare takes no parameters; send {} or no body"))
            return
        root = Path(entry["root"])
        status_doc = self._control_status(root, meta)
        if status_doc is None:
            return
        if status_doc.get("stop") is True:
            self._send_json(409, error_envelope(
                "STOP_ALREADY_APPLIED",
                "this Runtime already reports an applied formal STOP; STOP "
                "is terminal and cannot be issued again"))
            return
        project_id = status_doc.get("PROJECT_ID")
        state_hash = self._project_state_hash(root, project_id)
        if state_hash is None:
            self._send_json(409, error_envelope(
                "STOP_PROJECT_UNAVAILABLE",
                "the active project identity or state needed to bind a STOP "
                "confirmation is unavailable; nothing was issued"))
            return
        now = time.time()
        with self.server._stop_challenge_lock:
            self._prune_stop_challenges(now)
            if len(self.server._stop_challenges) >= \
                    STOP_CHALLENGE_MAX_PENDING:
                self._send_json(429, error_envelope(
                    "STOP_CHALLENGE_LIMIT",
                    f"at most {STOP_CHALLENGE_MAX_PENDING} unconfirmed STOP "
                    "challenges may exist; wait for one to expire"))
                return
            challenge = {
                "challenge_id": secrets.token_hex(16),
                "token": secrets.token_hex(16),
                "runtime_id": entry["id"],
                "project_id": project_id,
                "project_state_sha256": state_hash,
                "issued_at": now_iso(),
                "expires_at": now + STOP_CHALLENGE_TTL_SECONDS,
                "used": False,
            }
            self.server._stop_challenges[challenge["challenge_id"]] = challenge
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "stop_challenge": {
                "schema_version": SCHEMA_VERSION,
                "challenge_id": challenge["challenge_id"],
                "confirmation_token": challenge["token"],
                "project_id": project_id,
                "issued_at": challenge["issued_at"],
                "expires_in_seconds": STOP_CHALLENGE_TTL_SECONDS,
                "instructions": (
                    "Single-use, short-lived confirmation challenge bound to "
                    "this Runtime and the current project state. POST "
                    "challenge_id, confirmation_token, and project_id to "
                    "/controls/stop/confirm before it expires; a stale, "
                    "replayed, or mismatched confirmation fails closed."),
            }})

    def _prune_stop_challenges(self, now: float) -> None:
        expired = [challenge_id for challenge_id, challenge
                   in self.server._stop_challenges.items()
                   if not isinstance(challenge.get("expires_at"),
                                     (int, float))
                   or challenge["expires_at"] < now]
        for challenge_id in expired:
            self.server._stop_challenges.pop(challenge_id, None)

    def _control_stop_confirm(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            request = web_console_control.validate_stop_confirm_request(
                payload)
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        root = Path(entry["root"])
        with self.server._stop_challenge_lock:
            challenge = self.server._stop_challenges.get(
                request["challenge_id"])
            status_doc = self._control_status(root, meta)
            if status_doc is None:
                return
            project_id = status_doc.get("PROJECT_ID")
            state_hash = self._project_state_hash(root, project_id) or ""
            ok, reason = web_console_control.evaluate_stop_challenge(
                challenge, runtime_id=entry["id"], request=request,
                now=time.time(),
                project_id=project_id
                if isinstance(project_id, str) else "",
                project_state_sha256=state_hash,
                stop_applied=status_doc.get("stop") is True)
            if not ok:
                self._send_json(409, error_envelope(
                    "STOP_CONFIRM_REJECTED",
                    "the STOP confirmation was rejected; nothing was applied",
                    {"reason": reason}))
                return
            # Exactly-once challenge semantics: consume before running so a
            # replay can never double-fire the terminal action. A failed
            # Runtime STOP still consumes the challenge; a fresh confirmation
            # requires a fresh challenge.
            challenge["used"] = True
            stop_status, stop_payload = self._run_formal_stop(root)
        if stop_status != 200:
            self._send_json(stop_status, stop_payload)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "stop": {"schema_version": SCHEMA_VERSION, "applied": True,
                     "stop_flag_present": True,
                     "challenge_id": request["challenge_id"],
                     "exit_code": stop_payload["exit_code"],
                     "elapsed_ms": stop_payload["elapsed_ms"]}})

    def _run_formal_stop(self, root: Path) -> tuple[int, dict]:
        """Apply Formal STOP through the Runtime's own authoritative script.

        The Web layer never writes control/STOP itself; this runs the fixed,
        argument-vector invocation of the v1.2 STOP_AGENT_SYSTEM.ps1 entry
        point shipped inside the managed Runtime Root and verifies the
        authoritative result.
        """
        script = root / STOP_SCRIPT_RELATIVE
        if not script.is_file():
            return 502, error_envelope(
                "STOP_SCRIPT_UNAVAILABLE",
                "the Runtime Root does not contain the formal "
                "STOP_AGENT_SYSTEM.ps1 entry point; nothing was applied")
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            return 502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                "PowerShell is required to run the formal Runtime STOP "
                "entry point and was not found")
        command = [powershell, "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(script)]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, capture_output=True,
                cwd=str(self.config.console_root),
                timeout=self.config.control_timeout)
        except subprocess.TimeoutExpired:
            return 504, error_envelope(
                "CONTROL_PLANE_TIMEOUT",
                f"the formal Runtime STOP script did not finish within "
                f"{self.config.control_timeout} seconds")
        except OSError as exc:
            return 502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                f"failed to launch the formal Runtime STOP entry point: "
                f"{exc}")
        elapsed_ms = int((time.monotonic() - started) * 1000)
        detail = {"exit_code": completed.returncode, "elapsed_ms": elapsed_ms,
                  "stdout_head": completed.stdout.decode(
                      "utf-8", errors="replace")[:STDOUT_ERROR_HEAD_BYTES],
                  "stderr_head": completed.stderr.decode(
                      "utf-8", errors="replace")[:STDOUT_ERROR_HEAD_BYTES]}
        if completed.returncode != 0:
            return 502, error_envelope(
                "CONTROL_PLANE_ERROR",
                "the formal Runtime STOP entry point failed; the STOP flag "
                "state must be verified against the Runtime", detail)
        if not (root / "control" / "STOP").is_file():
            return 502, error_envelope(
                "CONTROL_PLANE_ERROR",
                "the formal Runtime STOP entry point did not produce the "
                "authoritative control/STOP artifact; refusing to report "
                "success", detail)
        return 200, {"exit_code": 0, "elapsed_ms": elapsed_ms}

    def _hr_directory(self, runtime_id: str) -> Path:
        return self.config.data_dir / HR_DATA_SUBDIR / runtime_id

    @staticmethod
    def _helper_exit_response(label: str, exit_code: int,
                              stderr_head: str):
        detail = {"helper_command": label, "exit_code": exit_code,
                  "stderr_head": stderr_head[:STDOUT_ERROR_HEAD_BYTES]}
        if exit_code == 2:
            return 400, error_envelope(
                "HUMAN_DECISION_REFUSED",
                f"the {label} input was refused by the supported helper",
                detail)
        if exit_code in (3, 4):
            return 409, error_envelope(
                "HUMAN_DECISION_REFUSED",
                f"the {label} was refused by the supported helper (conflict "
                "or stale/duplicate); the authoritative Runtime state is "
                "unchanged", detail)
        if exit_code == 5:
            return 500, error_envelope(
                "HUMAN_DECISION_INTERNAL",
                f"the supported helper reported an internal failure for the "
                f"{label}", detail)
        return 502, error_envelope(
            "CONTROL_PLANE_ERROR",
            f"the supported helper reported an error for the {label}",
            detail)

    def _run_hr_helper(self, root: Path, args: list) -> dict:
        command = [sys.executable, str(root / HR_SCRIPT_RELATIVE),
                   "--root", str(root), *args]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, capture_output=True,
                cwd=str(self.config.console_root),
                timeout=self.config.control_timeout)
        except subprocess.TimeoutExpired:
            return {"failure": "TIMEOUT"}
        except OSError as exc:
            return {"failure": "LAUNCH_FAILED", "stderr_head": str(exc)}
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout = (completed.stdout or b"").decode("utf-8", errors="replace")
        stderr_head = (completed.stderr or b"").decode(
            "utf-8", errors="replace")
        if completed.returncode != 0:
            return {"failure": None, "exit_code": completed.returncode,
                    "stderr_head": stderr_head, "elapsed_ms": elapsed_ms}
        document = _parse_helper_document(stdout)
        if document is None:
            return {"failure": "UNPARSEABLE", "exit_code": 0,
                    "stderr_head": stderr_head, "elapsed_ms": elapsed_ms}
        return {"failure": None, "exit_code": 0, "document": document,
                "stderr_head": stderr_head, "elapsed_ms": elapsed_ms}

    def _control_hr_prepare(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        root = Path(entry["root"])
        status_doc = self._control_status(root, meta)
        if status_doc is None:
            return
        if status_doc.get("human_review") is not True \
                or status_doc.get("project_status") != "HUMAN_REVIEW":
            self._send_json(409, error_envelope(
                "HUMAN_REVIEW_NOT_ACTIVE",
                "this Runtime is not in HUMAN_REVIEW; there is no human "
                "decision to prepare"))
            return
        project_id = status_doc.get("PROJECT_ID")
        if (not isinstance(project_id, str)
                or not PROJECT_ID_PATTERN.fullmatch(project_id)):
            self._send_json(409, error_envelope(
                "HUMAN_REVIEW_NOT_ACTIVE",
                "the active project identity is unavailable; refusing to "
                "prepare a Human Decision"))
            return
        try:
            decision = resume_human_review.validate_decision_payload(payload)
        except resume_human_review.ResumeError as exc:
            self._send_json(400, error_envelope(
                "HUMAN_DECISION_INVALID",
                "the Human Decision input was rejected by the supported "
                "helper's typed schema", {"reason": str(exc)}))
            return
        hr_dir = self._hr_directory(entry["id"])
        hr_dir.mkdir(parents=True, exist_ok=True)
        if len(list(hr_dir.glob("*.json"))) >= HR_MAX_PREPARED_RECEIPTS:
            self._send_json(429, error_envelope(
                "HUMAN_DECISION_LIMIT",
                f"at most {HR_MAX_PREPARED_RECEIPTS} prepared Human Decision "
                "receipts may exist per Runtime; apply or let a human clean "
                "the Console data directory"))
            return
        decision_file = hr_dir / f"decision-{uuid.uuid4().hex}.json"
        receipt_out = hr_dir / f"receipt-{uuid.uuid4().hex}.json"
        decision_file.write_bytes(json_bytes(decision))
        result = self._run_hr_helper(root, [
            "prepare", "--project-id", project_id,
            "--decision-file", str(decision_file),
            "--receipt-out", str(receipt_out)])
        decision_file.unlink(missing_ok=True)
        if result.get("failure") == "TIMEOUT":
            self._send_json(504, error_envelope(
                "CONTROL_PLANE_TIMEOUT",
                "the supported Human Decision helper did not finish the "
                "prepare step in time"))
            return
        if result.get("failure") == "LAUNCH_FAILED":
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                "failed to launch the supported Human Decision helper: "
                f"{result.get('stderr_head', '')}"))
            return
        if result.get("failure") == "UNPARSEABLE":
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_MALFORMED_OUTPUT",
                "the supported Human Decision helper produced output that "
                "is not one JSON document"))
            return
        if result.get("exit_code") != 0:
            status, response = self._helper_exit_response(
                "Human Decision prepare", result["exit_code"],
                result.get("stderr_head", ""))
            self._send_json(status, response)
            return
        event = result["document"]
        receipt_id = event.get("receipt_id")
        receipt_sha256 = event.get("receipt_sha256")
        previous_hash = event.get("previous_project_state_sha256")
        if (not isinstance(receipt_id, str)
                or not web_console_control.RECEIPT_ID_PATTERN.fullmatch(
                    receipt_id)
                or not isinstance(receipt_sha256, str)
                or not HEX64.fullmatch(receipt_sha256)
                or not isinstance(previous_hash, str)
                or not HEX64.fullmatch(previous_hash)):
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_MALFORMED_OUTPUT",
                "the supported Human Decision helper answered without a "
                "usable receipt binding"))
            return
        receipt_file = hr_dir / f"{receipt_id}.json"
        try:
            if receipt_out.is_file():
                os.replace(receipt_out, receipt_file)
            else:
                raise OSError("prepared receipt file is missing")
        except OSError:
            self._send_json(500, error_envelope(
                "HUMAN_DECISION_INTERNAL",
                "the prepared receipt could not be stored in the Console "
                "data directory"))
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "human_decision": {
                "schema_version": SCHEMA_VERSION,
                "receipt_id": receipt_id,
                "receipt_sha256": receipt_sha256,
                "previous_project_state_sha256": previous_hash,
                "project_id": event.get("project_id")
                if isinstance(event.get("project_id"), str) else project_id,
            }})

    def _control_hr_apply(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            request = web_console_control.validate_human_decision_apply_request(
                payload)
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        root = Path(entry["root"])
        receipt_file = self._hr_directory(entry["id"]) / \
            f"{request['receipt_id']}.json"
        try:
            raw = receipt_file.read_bytes()
            stored = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            self._send_json(404, error_envelope(
                "HUMAN_DECISION_RECEIPT_UNKNOWN",
                "no prepared Human Decision receipt with this ID exists for "
                "this Runtime"))
            return
        if len(raw) > MAX_HR_RECEIPT_BYTES or not isinstance(stored, dict):
            self._send_json(500, error_envelope(
                "HUMAN_DECISION_INTERNAL",
                "the stored Human Decision receipt is unusable"))
            return
        if stored.get("receipt_id") != request["receipt_id"] \
                or stored.get("receipt_sha256") != request["receipt_sha256"]:
            self._send_json(409, error_envelope(
                "HUMAN_DECISION_RECEIPT_MISMATCH",
                "the confirmation does not match the prepared receipt; "
                "stale or mismatched applications fail closed"))
            return
        result = self._run_hr_helper(root, [
            "apply", "--receipt", str(receipt_file)])
        if result.get("failure") == "TIMEOUT":
            self._send_json(504, error_envelope(
                "CONTROL_PLANE_TIMEOUT",
                "the supported Human Decision helper did not finish the "
                "apply step in time; verify the Runtime state before "
                "retrying"))
            return
        if result.get("failure") == "LAUNCH_FAILED":
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_UNAVAILABLE",
                "failed to launch the supported Human Decision helper: "
                f"{result.get('stderr_head', '')}"))
            return
        if result.get("failure") == "UNPARSEABLE":
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_MALFORMED_OUTPUT",
                "the supported Human Decision helper produced output that "
                "is not one JSON document"))
            return
        if result.get("exit_code") != 0:
            status, response = self._helper_exit_response(
                "Human Decision apply", result["exit_code"],
                result.get("stderr_head", ""))
            self._send_json(status, response)
            return
        event = result["document"]
        receipt_file.unlink(missing_ok=True)
        relayed = {}
        for key in ("event", "project_id", "previous_status", "status",
                    "receipt_id", "receipt_sha256"):
            if isinstance(event.get(key), str):
                relayed[key] = event[key]
        next_message_id = event.get("next_message_id_unchanged")
        if isinstance(next_message_id, int) \
                and not isinstance(next_message_id, bool):
            relayed["next_message_id_unchanged"] = next_message_id
        if isinstance(event.get("executor_dispatched"), bool):
            relayed["executor_dispatched"] = event["executor_dispatched"]
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "human_decision_result": relayed})

    def _registry_controls(self, runtime_id: str, *, with_body: bool):
        """Pending Controls document for one registered Runtime (P5).

        The document is derived only from the Runtime's own status document
        and its interventions query, plus a bounded server-side read of the
        HUMAN_REVIEW reason flag. Success is never inferred from root
        compatibility flags or UI-local state.
        """
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        status_doc = self._control_status(root, meta)
        if status_doc is None:
            return
        interventions_result = self.server.run_control(
            root, ["interventions", "--json"])
        interventions_document = None
        if interventions_result["failure"] is None \
                and interventions_result["exit_code"] == 0:
            interventions_document = interventions_result["document"]
        controls_doc = web_console_control.project_pending_controls(
            status_doc, interventions_document)
        reason_text, truncated = self._read_review_reason(root)
        controls_doc["human_review"] = \
            web_console_control.human_review_presentation(
                status_doc, reason_text, truncated=truncated)
        controls_doc["source"] = {
            "kind": "supervisor_control_status_plus_interventions",
            "status_exit_code": 0,
            "interventions_exit_code":
                interventions_result.get("exit_code"),
            "interventions_failure": interventions_result.get("failure"),
            "elapsed_ms": interventions_result.get("elapsed_ms")}
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "runtime": meta, "controls": controls_doc}, with_body=with_body)

    @staticmethod
    def _read_review_reason(root: Path) -> tuple[str | None, bool]:
        """Bounded server-side read of the Runtime's HUMAN_REVIEW reason.

        The path is fixed (the Registry-resolved root's own control flag);
        the browser can never influence it.
        """
        path = root / "control" / "HUMAN_REVIEW"
        limit = web_console_control.HUMAN_REVIEW_REASON_MAX_BYTES
        try:
            if not path.is_file():
                return None, False
            raw = path.read_bytes()
        except OSError:
            return None, False
        truncated = len(raw) > limit
        return raw[:limit].decode("utf-8", errors="replace"), truncated

    # -- P6 Artifact Center ----------------------------------------------------

    def _artifact_context(self, runtime_id: str):
        """Resolve the Registry entry once and build the fresh artifact view.

        Combines the bounded status probe (which names the active project),
        the authoritative completion-ledger query, and a bounded read-only
        walk of the three authorized publication roots. Returns None after
        sending the deterministic error envelope when any source fails
        closed; the caller sends nothing in that case.
        """
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return None
        entry, meta = resolved
        root = Path(entry["root"])
        status_doc = self._control_status(root, meta)
        if status_doc is None:
            return None
        project_id = status_doc.get("PROJECT_ID")
        if not isinstance(project_id, str) \
                or not PROJECT_ID_PATTERN.fullmatch(project_id):
            self._send_json(409, error_envelope(
                "ARTIFACTS_NO_ACTIVE_PROJECT",
                "the Runtime reports no usable active project; artifact "
                "discovery is refused rather than guessed"))
            return None
        ledger_result = self.server.run_control(root, ["feedback", "--json"])
        if ledger_result["failure"] or ledger_result["exit_code"] != 0:
            status, envelope = self.server.control_failure_envelope(
                ledger_result, "completion ledger")
            self._send_json(status, envelope)
            return None
        ledger_document = ledger_result.get("document")
        if not isinstance(ledger_document, list):
            self._send_json(502, error_envelope(
                "CONTROL_PLANE_MALFORMED_OUTPUT",
                "the completion ledger query produced output that is not "
                "one JSON list",
                {"control_command": "feedback",
                 "exit_code": ledger_result["exit_code"]}))
            return None
        files, walked_complete = self._walk_publication_roots(root, project_id)
        built = web_console_artifacts.build_artifact_index(ledger_document,
                                                           files)
        if not walked_complete:
            built["honesty"]["notes"].append(
                f"the authorized publication roots contain more files than "
                f"the bounded walk limit ({ARTIFACTS_WALK_MAX_FILES}); the "
                "catalog may be incomplete")
        return {"entry": entry, "meta": meta, "root": root,
                "project_id": project_id, "built": built}

    def _walk_publication_roots(self, root: Path, project_id: str):
        """Bounded read-only walk of the authorized publication roots.

        Symlinks and reparse points are never followed; any entry whose
        resolved path escapes the active project directory is refused
        silently and simply never becomes an artifact. Only path names and
        stat metadata are collected — no file content is read here.
        """
        base = root / "projects" / project_id
        try:
            base_resolved = base.resolve()
        except OSError:
            return [], True
        files = []
        complete = True
        for root_name in web_console_artifacts.AUTHORIZED_ROOTS:
            directory = base / root_name
            if not directory.is_dir():
                continue
            pending = [(directory, 0)]
            index = 0
            while index < len(pending):
                current, depth = pending[index]
                index += 1
                try:
                    children = sorted(current.iterdir(), key=lambda p: p.name)
                except OSError:
                    continue
                for child in children:
                    if len(files) >= ARTIFACTS_WALK_MAX_FILES:
                        complete = False
                        index = len(pending)
                        break
                    try:
                        if child.is_symlink():
                            continue
                        resolved = child.resolve()
                        if not resolved.is_relative_to(base_resolved):
                            continue
                        if child.is_dir():
                            if depth + 1 <= ARTIFACTS_WALK_MAX_DEPTH:
                                pending.append((child, depth + 1))
                            continue
                        if not child.is_file():
                            continue
                        try:
                            size = child.stat().st_size
                        except OSError:
                            size = None
                    except OSError:
                        continue
                    files.append({
                        "path": child.relative_to(base).as_posix(),
                        "size_bytes": size})
        return files, complete

    def _artifact_file_path(self, root: Path, project_id: str,
                            path: str) -> Path | None:
        """Resolve one cataloged artifact path to a real file inside the
        active project. The path comes only from the server's own
        walk/ledger join — never from request input."""
        candidate = root / "projects" / project_id
        for segment in path.split("/"):
            candidate = candidate / segment
        try:
            resolved = candidate.resolve()
            base = (root / "projects" / project_id).resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(base) or not resolved.is_file():
            return None
        return resolved

    def _hash_file_bounded(self, path: Path) -> tuple:
        """Return (size, sha256) with the hash refused above the bounded
        cap (None instead of a partial digest that could never match)."""
        try:
            size = path.stat().st_size
        except OSError:
            return None, None
        if size > ARTIFACTS_MAX_HASH_BYTES:
            return size, None
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError:
            return size, None
        return size, digest.hexdigest()

    def _artifact_snapshot(self, path: Path, preview_cap: int):
        """Hash the same snapshot whose bounded bytes will be returned."""
        digest, chunks, size, kept = hashlib.sha256(), [], 0, 0
        try:
            with path.open("rb") as handle:
                while True:
                    block = handle.read(min(1024 * 1024, ARTIFACTS_MAX_HASH_BYTES + 1 - size))
                    if not block:
                        return size, digest.hexdigest(), b"".join(chunks)
                    size += len(block)
                    digest.update(block)
                    piece = block[:max(0, preview_cap + 1 - kept)]
                    chunks.append(piece)
                    kept += len(piece)
                    if size > ARTIFACTS_MAX_HASH_BYTES:
                        return size, None, b"".join(chunks)
        except OSError:
            return None, None, b""

    def _artifact_from_context(self, context: dict, artifact_id: str):
        record = context["built"]["index"].get(artifact_id)
        if record is None:
            self._send_json(404, error_envelope(
                "ARTIFACT_UNKNOWN",
                "no artifact with this identifier exists in this Runtime's "
                "authorized publication roots or completion ledger"))
            return None
        return record

    def _registry_artifacts(self, runtime_id: str, query: str, *,
                            with_body: bool):
        """Filtered, paginated artifact catalog for one Runtime (P6)."""
        context = self._artifact_context(runtime_id)
        if context is None:
            return
        try:
            params = web_console_artifacts.parse_artifacts_query(query)
        except web_console_artifacts.ArtifactsQueryError as exc:
            self._send_json(400, error_envelope(
                "INVALID_QUERY_PARAM",
                "the artifact catalog query was rejected; every parameter "
                "must be within its documented bounds",
                {"parameter_error": str(exc)}), with_body=with_body)
            return
        built = context["built"]
        catalog = web_console_artifacts.project_catalog(
            built["index"], built["honesty"], params, generated_at=now_iso())
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "runtime": context["meta"],
            "artifacts": catalog,
        }, with_body=with_body)

    def _registry_artifact_item(self, runtime_id: str, artifact_id: str, *,
                                with_body: bool):
        context = self._artifact_context(runtime_id)
        if context is None:
            return
        record = self._artifact_from_context(context, artifact_id)
        if record is None:
            return
        size, actual = None, None
        file_path = self._artifact_file_path(
            context["root"], context["project_id"], record["path"])
        if file_path is not None:
            size, actual = self._hash_file_bounded(file_path)
        detail = web_console_artifacts.project_artifact_detail(
            record, size_bytes=size, actual_sha256=actual)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "runtime": context["meta"],
            "artifact": detail,
        }, with_body=with_body)

    def _registry_artifact_preview(self, runtime_id: str, artifact_id: str,
                                   *, with_body: bool):
        """Bounded read-only preview document (P6).

        Receipt-bound files must re-verify against the authoritative SHA-256
        before any content is interpreted; a mismatch (or an unavailable
        hash) refuses the preview instead of showing unverified bytes.
        """
        context = self._artifact_context(runtime_id)
        if context is None:
            return
        record = self._artifact_from_context(context, artifact_id)
        if record is None:
            return
        formats = web_console_artifacts
        format_name = record["format"]
        if record["availability"] == "missing":
            self._send_json(404, error_envelope(
                "ARTIFACT_UNAVAILABLE",
                "this artifact is bound in the completion ledger but is not "
                "present under the authorized publication roots"),
                with_body=with_body)
            return
        file_path = self._artifact_file_path(
            context["root"], context["project_id"], record["path"])
        if file_path is None:
            self._send_json(404, error_envelope(
                "ARTIFACT_UNAVAILABLE",
                "the artifact file could not be resolved inside the active "
                "project"), with_body=with_body)
            return
        if format_name == formats.FORMAT_PDF:
            read_cap = formats.MAX_PDF_BYTES
        elif format_name in (formats.FORMAT_PNG, formats.FORMAT_JPEG,
                             formats.FORMAT_WEBP):
            read_cap = formats.MAX_IMAGE_BYTES
        elif format_name == formats.FORMAT_UNSUPPORTED:
            read_cap = 0
        else:
            read_cap = formats.MAX_PREVIEW_BYTES
        size, actual, raw = self._artifact_snapshot(file_path, read_cap)
        if size is None:
            self._send_json(502, error_envelope(
                "ARTIFACT_UNREADABLE", "the artifact file could not be read"),
                with_body=with_body)
            return
        if record.get("expected_sha256") is not None:
            if actual is None:
                self._send_json(409, error_envelope(
                    "ARTIFACT_HASH_UNAVAILABLE",
                    "the artifact could not be hashed within the bounded "
                    "cap; the preview is refused rather than guessed"),
                    with_body=with_body)
                return
            if actual != record["expected_sha256"]:
                self._send_json(409, error_envelope(
                    "ARTIFACT_HASH_MISMATCH",
                    "the file bytes no longer match the authoritative "
                    "receipt hash; the preview is refused instead of "
                    "showing unverified content"), with_body=with_body)
                return
        preview = formats.project_preview(format_name, raw, total_bytes=size)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "runtime": context["meta"],
            "artifact": {"artifact_id": artifact_id,
                         "path": record["path"],
                         "format": format_name,
                         "verification": "verified"
                         if record.get("expected_sha256") is not None
                         else "unverified"},
            "preview": preview,
        }, with_body=with_body)

    def _registry_artifact_raw(self, runtime_id: str, artifact_id: str, *,
                               with_body: bool):
        """Serve verified image bytes (inline) or PDF bytes (attachment).

        Only these formats are served at all; every claim is re-verified
        (authoritative hash, then magic bytes) before one bounded byte
        reaches the socket.
        """
        context = self._artifact_context(runtime_id)
        if context is None:
            return
        record = self._artifact_from_context(context, artifact_id)
        if record is None:
            return
        formats = web_console_artifacts
        format_name = record["format"]
        if format_name not in (formats.FORMAT_PNG, formats.FORMAT_JPEG,
                               formats.FORMAT_WEBP, formats.FORMAT_PDF):
            self._send_json(404, error_envelope(
                "ARTIFACT_RAW_UNSUPPORTED",
                "raw bytes are served only for verified image and PDF "
                "formats; all other formats render as text or metadata"))
            return
        if record["availability"] == "missing":
            self._send_json(404, error_envelope(
                "ARTIFACT_UNAVAILABLE",
                "this artifact is bound in the completion ledger but is not "
                "present under the authorized publication roots"))
            return
        file_path = self._artifact_file_path(
            context["root"], context["project_id"], record["path"])
        if file_path is None:
            self._send_json(404, error_envelope(
                "ARTIFACT_UNAVAILABLE",
                "the artifact file could not be resolved inside the active "
                "project"))
            return
        cap = formats.MAX_IMAGE_BYTES if format_name != formats.FORMAT_PDF else formats.MAX_PDF_BYTES
        size, actual, raw = self._artifact_snapshot(file_path, cap)
        if size is None:
            self._send_json(502, error_envelope(
                "ARTIFACT_UNREADABLE", "the artifact file could not be read"),
                with_body=with_body)
            return
        if record.get("expected_sha256") is not None:
            if actual is None:
                self._send_json(409, error_envelope(
                    "ARTIFACT_HASH_UNAVAILABLE",
                    "the artifact could not be hashed within the bounded "
                    "cap; the bytes are refused rather than guessed"))
                return
            if actual != record["expected_sha256"]:
                self._send_json(409, error_envelope(
                    "ARTIFACT_HASH_MISMATCH",
                    "the file bytes no longer match the authoritative "
                    "receipt hash; the bytes are refused instead of served "
                    "unverified"))
                return
        cap = formats.MAX_IMAGE_BYTES if format_name != formats.FORMAT_PDF \
            else formats.MAX_PDF_BYTES
        if size is not None and size > cap:
            self._send_json(413, error_envelope(
                "ARTIFACT_TOO_LARGE",
                f"the artifact exceeds the {cap}-byte bound for raw "
                "serving; nothing was read"))
            return
        if len(raw) > cap or not formats.magic_matches(format_name, raw):
            self._send_json(409, error_envelope(
                "ARTIFACT_FORMAT_MISMATCH",
                "the file bytes do not match the claimed format's magic; "
                "refusing to serve active content as an image or document"))
            return
        media_type = formats.MEDIA_TYPES[format_name]
        disposition = "inline" if format_name != formats.FORMAT_PDF \
            else "attachment"
        headers = [("Content-Type", media_type),
                   ("X-Content-Type-Options", "nosniff"),
                   ("Content-Security-Policy", "default-src 'none'; sandbox"),
                   ("Cache-Control", "no-store"),
                   ("Content-Disposition", disposition)]
        self._send_raw_bytes(200, raw, extra_headers=headers,
                             with_body=with_body)

    def _registry_artifact_feedback(self, runtime_id: str):
        """Artifact-triggered STEER/AUDIT through the P5 formal surface.

        The request binds a verified historical MESSAGE_ID and normalized
        artifact paths (references only); the intervention text is composed
        server-side and delegated verbatim to `supervisor_control.py
        intervene`. Nothing about the artifact contents or project history
        is auto-injected.
        """
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            request = web_console_artifacts.validate_artifact_feedback_request(
                payload)
        except web_console_artifacts.ArtifactPathError:
            self._send_json(409, error_envelope(
                "ARTIFACT_FEEDBACK_REFUSED",
                "the referenced artifact path is not a normalized "
                "project-relative publication path; nothing was submitted",
                {"reason": "ARTIFACT_PATH_INVALID"}))
            return
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        context = self._artifact_context(runtime_id)
        if context is None:
            return
        binding = web_console_artifacts.check_feedback_binding(
            request, context["built"]["index"])
        if binding is not None:
            self._send_json(409, error_envelope(
                "ARTIFACT_FEEDBACK_REFUSED",
                "the artifact feedback was refused; nothing was submitted",
                {"reason": binding}))
            return
        composed = web_console_artifacts.compose_artifact_feedback_text(
            request["mode"], request["message_id"],
            request["artifact_paths"], request["comment"])
        status, response = self._submit_intervention(entry, meta, {
            "mode": request["mode"],
            "comment": composed,
            "target_message_id": request["message_id"],
            "interrupt_current": request["interrupt_current"],
        })
        if status != 200:
            self._send_json(status, response)
            return
        response["artifact_feedback"] = {
            "schema_version": SCHEMA_VERSION,
            "mode": request["mode"],
            "message_id": request["message_id"],
            "artifact_paths": request["artifact_paths"],
            "surface": "P5 formal intervention (supervisor_control intervene)",
        }
        self._send_json(status, response)

    # -- P8 Supervisor-turn observability --------------------------------------

    def _supervisor_turns(self, runtime_id: str, query: str, *,
                          with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            params = web_console_supervisor.parse_turns_query(query)
        except web_console_supervisor.TurnRecordError as exc:
            self._send_json(400, error_envelope(exc.code, str(exc)),
                            with_body=with_body)
            return
        document = web_console_supervisor.build_turns_document(
            Path(entry["root"]), limit=params["limit"],
            offset=params["offset"], generated_at=now_iso())
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "supervisor_turns": document}, with_body=with_body)

    def _supervisor_turn_detail(self, runtime_id: str, turn_id: str, *,
                                with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            document = web_console_supervisor.build_turn_detail_document(
                Path(entry["root"]), turn_id)
        except web_console_supervisor.TurnRecordError as exc:
            status = {"TURN_ID_INVALID": 400,
                      "TURN_NOT_FOUND": 404}.get(exc.code, 502)
            self._send_json(status, error_envelope(exc.code, str(exc)),
                            with_body=with_body)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "supervisor_turn": document}, with_body=with_body)

    def _supervisor_usage(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        document = web_console_supervisor.build_usage_document(
            Path(entry["root"]), generated_at=now_iso())
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "supervisor_usage": document}, with_body=with_body)

    def _supervisor_config_get(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        config_result = web_console_supervisor.read_config_document(root)
        capability = web_console_setup.capability_report(None)
        status, payload = self.server.status_result(runtime_root=root,
                                                    runtime_meta=meta)
        if status == 200 and isinstance(payload, dict):
            status_doc = (payload.get("control_plane") or {}).get("status")
            if isinstance(status_doc, dict):
                capability = web_console_setup.capability_report(status_doc)
        draft, draft_note = self._load_draft(runtime_id)
        view = web_console_supervisor.config_view(
            config_result, capability=capability,
            draft_supervisor=draft.get("supervisor"))
        if draft_note:
            view["honesty"]["notes"].append(draft_note)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "supervisor_config": view}, with_body=with_body)

    def _supervisor_config_post(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            request = web_console_supervisor.validate_config_change_request(
                payload)
        except web_console_control.ControlRequestError as exc:
            self._schema_error(exc)
            return
        # The only mutation: the formal v1.2 control-plane subcommand. The
        # Runtime validates again, queues the change under its own fence
        # lock, and never interrupts an active Supervisor turn.
        result = self.server.run_control(Path(entry["root"]), [
            "queue-supervisor-config",
            f"--model={request['model']}",
            f"--effort={request['reasoning_effort']}",
            "--json"])
        status, response = self._mutation_response(
            result, "supervisor config queue", meta)
        if status == 200:
            document = response["control"]["result"]
            response["supervisor_config"] = document.get("supervisor_config")
        self._send_json(status, response)

    # -- P9 Settings, operator notes, alerts, Runtime creation -------------

    def _settings_store(self):
        return web_console_settings.SettingsStore(self.config.data_dir)

    @staticmethod
    def _settings_error(exc: "web_console_settings.SettingsError",
                        *, default_status: int = 400) -> tuple[int, dict]:
        status = 500 if exc.code == "SETTINGS_CORRUPT" else default_status
        detail = {"field": exc.field} if exc.field else None
        return status, error_envelope(exc.code, str(exc), detail)

    def _settings_get(self, *, with_body: bool):
        try:
            document = self._settings_store().read_global()
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope, with_body=with_body)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "global_settings": document, "honesty":
            [SETTINGS_APPLICATION_NOTE]}, with_body=with_body)

    def _settings_post(self):
        payload, error = self._read_request_body()
        if error is not None:
            return
        if not isinstance(payload, dict) or set(payload) != {"settings"}:
            self._send_json(400, error_envelope(
                "SETTINGS_INVALID_PAYLOAD",
                'the body must be exactly {"settings": {...}} with an '
                "object value"))
            return
        try:
            partial = web_console_settings.validate_settings_payload(
                payload["settings"], partial=True)
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope)
            return
        try:
            document = self._settings_store().merge_global(
                partial=partial, updated_at=now_iso())
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "global_settings": document, "honesty":
            [SETTINGS_APPLICATION_NOTE]})

    def _runtime_settings_document(self, runtime_id: str, meta: dict):
        store = self._settings_store()
        global_document = store.read_global()
        runtime_document = store.read_runtime(runtime_id)
        effective = web_console_settings.effective_settings(
            global_document["settings"], runtime_document["overrides"])
        return {
            "schema_version": web_console_settings.SCHEMA_VERSION,
            "effective": effective,
            "overrides": runtime_document["overrides"],
            "operator_note": runtime_document["operator_note"],
            "updated_at": runtime_document["updated_at"],
            "global_updated_at": global_document["updated_at"],
            "supervisor_application": {"note": SETTINGS_APPLICATION_NOTE},
        }

    def _runtime_settings_get(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            document = self._runtime_settings_document(entry["id"], meta)
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope, with_body=with_body)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "runtime_settings": document}, with_body=with_body)

    def _runtime_settings_post(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        try:
            overrides = web_console_settings.validate_override_payload(
                payload)
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope)
            return
        store = self._settings_store()
        try:
            # Fail closed on threshold combinations that would only be
            # invalid after this override is applied (e.g. a critical
            # expiry window above the effective warning window).
            prospective = web_console_alerts.resolve_thresholds(
                store.read_runtime(entry["id"])["overrides"])
            provided = overrides.get("alert_thresholds")
            if isinstance(provided, dict):
                for name, value in provided.items():
                    if value is not None:
                        prospective[name] = value
            web_console_settings.validate_alert_thresholds(prospective)
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope)
            return
        try:
            store.write_overrides(entry["id"], overrides=overrides,
                                  updated_at=now_iso())
            document = self._runtime_settings_document(entry["id"], meta)
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "runtime_settings": document})

    def _runtime_notes_get(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            document = self._settings_store().read_runtime(entry["id"])
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope, with_body=with_body)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "operator_note": document["operator_note"]},
            with_body=with_body)

    def _runtime_notes_post(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        payload, error = self._read_request_body()
        if error is not None:
            return
        if not isinstance(payload, dict) or set(payload) != {"text"}:
            self._send_json(400, error_envelope(
                "SETTINGS_INVALID_PAYLOAD",
                'the body must be exactly {"text": "..."} — an empty '
                "string clears the note"))
            return
        note = None
        if payload["text"] != "":
            try:
                note = {"text": web_console_settings.validate_operator_note(
                    payload["text"])}
            except web_console_settings.SettingsError as exc:
                status, envelope = self._settings_error(exc)
                self._send_json(status, envelope)
                return
        try:
            stored = self._settings_store().write_note(
                entry["id"], note=note, updated_at=now_iso())
        except web_console_settings.SettingsError as exc:
            status, envelope = self._settings_error(exc)
            self._send_json(status, envelope)
            return
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "operator_note": stored["operator_note"]})

    def _runtime_alerts_get(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        overrides = None
        settings_note = None
        try:
            overrides = self._settings_store().read_runtime(
                entry["id"])["overrides"]
        except web_console_settings.SettingsError:
            overrides = None
            settings_note = ("settings are unreadable "
                             "(SETTINGS_CORRUPT); default alert thresholds "
                             "were applied")
        thresholds = web_console_alerts.resolve_thresholds(overrides)
        status_code, payload = self.server.status_result(
            runtime_root=root, runtime_meta=meta)
        probe_ok = status_code == 200
        probe_error_code = None
        status_document = None
        if probe_ok:
            status_document = (payload.get("control_plane") or {}) \
                .get("status")
        else:
            probe_error_code = (payload.get("error") or {}).get("code")
        usage_document = None
        try:
            usage_document = web_console_supervisor.build_usage_document(
                root, generated_at=now_iso())
        except OSError:
            usage_document = None
        document = web_console_alerts.project_alerts(
            runtime=meta, status=status_document, probe_ok=probe_ok,
            probe_error_code=probe_error_code,
            usage_document=usage_document, thresholds=thresholds,
            now=datetime.now(timezone.utc), generated_at=now_iso())
        if settings_note:
            document["honesty"].append(settings_note)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "alerts_document": document}, with_body=with_body)

    def _runtime_templates_get(self, *, with_body: bool):
        catalog = web_console_runtime_create.release_template_catalog()
        for template in catalog:
            missing = [
                rel for rel in
                web_console_runtime_create.REQUIRED_RELEASE_PATHS
                if not (self.config.console_root / rel).is_file()]
            template["available"] = not missing
            template["missing_paths"] = missing[:8]
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "templates": catalog}, with_body=with_body)

    @staticmethod
    def _create_error_status(code: str) -> int:
        return {"CREATE_UNKNOWN_TEMPLATE": 400,
                "CREATE_INVALID_DESTINATION": 400,
                "CREATE_DESTINATION_CONFLICTS": 400,
                "CREATE_DESTINATION_PARENT_MISSING": 400,
                "CREATE_DESTINATION_PARENT_INVALID": 400,
                "CREATE_DESTINATION_UNSAFE": 400,
                "CREATE_LABEL_INVALID": 400,
                "CREATE_DESTINATION_EXISTS": 409,
                "CREATE_SOURCE_INCOMPLETE": 502,
                "CREATE_SOURCE_UNSAFE": 502,
                "CREATE_COPY_FAILED": 502,
                "CREATE_VALIDATION_FAILED": 502}.get(code, 500)

    def _runtime_create_post(self):
        payload, error = self._read_request_body()
        if error is not None:
            return
        if not isinstance(payload, dict) \
                or set(payload) != {"template_id", "destination", "label"}:
            self._send_json(400, error_envelope(
                "CREATE_INVALID_PAYLOAD",
                'the body must be exactly {"template_id", "destination", '
                '"label"}; the template source is server-controlled'))
            return
        try:
            template_id = web_console_runtime_create.validate_template_id(
                payload["template_id"])
            label = web_console_runtime_create.validate_label(
                payload["label"])
            destination = web_console_runtime_create.normalize_destination(
                payload["destination"])
        except web_console_runtime_create.CreateError as exc:
            self._send_json(self._create_error_status(exc.code),
                            error_envelope(exc.code, str(exc)))
            return
        # The whole create-plus-register flow is serialized per Console:
        # two conflicting creates can never both observe an empty target,
        # and no partially registered root can ever appear.
        with self.server._runtime_create_lock:
            registered_roots = [entry.get("root") for entry
                                in self.config.registry.list_entries()]
            temp = None
            try:
                if destination.exists():
                    raise web_console_runtime_create.CreateError(
                        "CREATE_DESTINATION_EXISTS",
                        "the destination already exists; existing "
                        "directories are never overwritten")
                web_console_runtime_create.check_destination_conflicts(
                    destination, console_root=self.config.console_root,
                    data_dir=self.config.data_dir,
                    registered_roots=registered_roots)
                web_console_runtime_create.check_destination_parent(
                    destination)
                web_console_runtime_create.plan_copy(
                    self.config.console_root)
                temp = destination.parent / (
                    ".create-tmp-" + uuid.uuid4().hex)
                copied = web_console_runtime_create.copy_skeleton(
                    self.config.console_root, temp)
                verified = web_console_runtime_create.verify_copy(temp)
                import os as os_module
                os_module.rename(str(temp), str(destination))
                temp = None
            except web_console_runtime_create.CreateError as exc:
                self._send_json(self._create_error_status(exc.code),
                                error_envelope(exc.code, str(exc)))
                return
            except OSError as exc:
                if temp is not None:
                    import shutil
                    shutil.rmtree(temp, ignore_errors=True)
                self._send_json(502, error_envelope(
                    "CREATE_COPY_FAILED",
                    f"the skeleton copy could not be moved into place: "
                    f"{exc}"))
                return
            try:
                entry = self.config.registry.add(str(destination), label)
            except web_console_registry.RegistryError as exc:
                rollback = web_console_runtime_create.rollback_remove(
                    destination, verified["marker_rel"],
                    verified["marker_sha256"])
                self._send_json(exc.http_status, error_envelope(
                    exc.code, str(exc), {"rollback": rollback}))
                return
        self._send_json(201, {
            "schema_version": SCHEMA_VERSION, "ok": True,
            "runtime": {"id": entry["id"], "label": entry["label"],
                        "root": entry["root"]},
            "created": {"template_id": template_id,
                        "destination": str(destination),
                        "files_copied": copied["files"],
                        "copied_bytes": copied["bytes"],
                        "validated_paths": verified["files"]}})

    # -- P7 Setup Wizard --------------------------------------------------------

    def _setup_dir(self, runtime_id: str) -> Path:
        return self.config.data_dir / "setup" / runtime_id

    def _load_draft(self, runtime_id: str) -> tuple[dict, str | None]:
        """Load the Console-owned, non-authoritative setup draft.

        A missing draft is an empty draft; a corrupt one is never reset
        silently — it is treated as empty with an honesty note (the draft is
        a convenience, never authority, so it must not brick the wizard).
        """
        path = self._setup_dir(runtime_id) / "draft.json"
        with self.server._setup_draft_lock:
            try:
                raw = json.loads(path.read_bytes().decode("utf-8"))
            except FileNotFoundError:
                return web_console_setup.empty_draft(), None
            except (OSError, UnicodeDecodeError, ValueError):
                return web_console_setup.empty_draft(), \
                    "SETUP_DRAFT_UNUSABLE: the stored setup draft could " \
                    "not be read and was treated as empty"
        draft, note = web_console_setup.normalize_draft(raw)
        if draft is None:
            return web_console_setup.empty_draft(), \
                f"SETUP_DRAFT_UNUSABLE: {note}"
        return draft, None

    def _save_draft(self, runtime_id: str, draft: dict) -> None:
        draft["updated_at"] = now_iso()
        with self.server._setup_draft_lock:
            atomic_write_json(self._setup_dir(runtime_id) / "draft.json",
                              draft)

    @staticmethod
    def _project_id_exists(root: Path, project_id) -> bool:
        if not isinstance(project_id, str) \
                or not PROJECT_ID_PATTERN.fullmatch(project_id):
            return False
        return (root / "projects" / project_id).is_dir()

    def _setup_goal_context(self, root: Path, draft: dict) -> tuple:
        """Recompute the deterministic Goal validation for a draft."""
        if not isinstance(draft.get("goal_markdown"), str):
            return None, False
        exists = self._project_id_exists(root, draft.get("project_id"))
        validation = web_console_setup.validate_goal(
            project_id=draft.get("project_id"),
            project_type=draft.get("project_type"),
            goal_markdown=draft["goal_markdown"],
            project_id_exists=exists)
        return validation, exists

    def _setup_state(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        draft, draft_note = self._load_draft(runtime_id)
        goal_validation, exists_for_draft = self._setup_goal_context(
            root, draft)
        template = root / ZCODE_TEMPLATE_RELATIVE
        template_available = False
        try:
            template_available = template.is_file() \
                and template.stat().st_size <= \
                web_console_setup.ZCODE_TEMPLATE_MAX_BYTES
        except OSError:
            template_available = False
        notes = [draft_note] if draft_note else []
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "setup": {
                "schema_version": web_console_setup.SETUP_SCHEMA_VERSION,
                "draft": draft,
                "goal_validation": goal_validation,
                "project_id_exists_for_draft": exists_for_draft,
                "capabilities": web_console_setup.capability_report(None),
                "zcode_template_available": template_available,
                "goal_bounds": {
                    "max_bytes": web_console_setup.GOAL_MAX_BYTES,
                    "max_chars": web_console_setup.GOAL_MAX_CHARS,
                    "max_lines": web_console_setup.GOAL_MAX_LINES},
                "honesty": {"notes": notes},
            }}, with_body=with_body)

    def _setup_goal(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        try:
            payload = self._read_json_body(
                max_bytes=SETUP_MAX_BODY_BYTES)
        except BodyError as exc:
            self._send_body_error(exc)
            return
        allowed = {"project_id", "project_type", "goal_markdown",
                   "source_name"}
        if (not set(payload) <= allowed
                or not {"project_id", "project_type", "goal_markdown"}
                <= set(payload)
                or not all(isinstance(payload[key], str)
                           for key in ("project_id", "project_type",
                                       "goal_markdown"))
                or not isinstance(payload.get("source_name", ""),
                                  str) or len(payload.get("source_name",
                                                          "")) > 200):
            self._send_json(400, error_envelope(
                "SETUP_INVALID_PAYLOAD",
                'the Goal request must be {"project_id", "project_type", '
                '"goal_markdown"} with an optional bounded "source_name"'))
            return
        goal_markdown = payload["goal_markdown"]
        if len(goal_markdown.encode("utf-8")) > SETUP_GOAL_FILE_MAX_BYTES:
            self._send_json(413, error_envelope(
                "SETUP_GOAL_TOO_LARGE",
                f"the Goal exceeds {SETUP_GOAL_FILE_MAX_BYTES} UTF-8 "
                "bytes"))
            return
        exists = self._project_id_exists(root, payload["project_id"])
        validation = web_console_setup.validate_goal(
            project_id=payload["project_id"],
            project_type=payload["project_type"],
            goal_markdown=goal_markdown,
            project_id_exists=exists)
        if not validation["valid"]:
            self._send_json(200, {
                "schema_version": SCHEMA_VERSION, "ok": True,
                "runtime": meta, "stored": False,
                "goal_validation": validation})
            return
        draft, _ = self._load_draft(runtime_id)
        draft = web_console_setup.merge_goal_into_draft(
            draft, project_id=payload["project_id"],
            project_type=payload["project_type"],
            goal_markdown=goal_markdown,
            source_name=payload.get("source_name") or None)
        self._save_draft(runtime_id, draft)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "stored": True, "goal_validation": validation,
            "project_id": payload["project_id"],
            "project_type": payload["project_type"]})

    @staticmethod
    def _input_rejection_kind(reasons: list) -> str:
        codes = {reason["code"] for reason in reasons}
        if "CROSS_ROOT" in codes:
            return "SETUP_INPUT_CROSS_ROOT_REFUSED"
        if "REPARSE" in codes:
            return "SETUP_INPUT_REPARSE_REFUSED"
        return "SETUP_INPUT_PATH_INVALID"

    def _normalize_input_path(self, raw: str, own_root: Path,
                              other_roots: list) -> tuple:
        """Validate one user-selected absolute path; refuse everything that
        is not a plain, existing, non-reparse file or directory on this
        machine. No content is ever read."""
        if (not isinstance(raw, str) or not raw.strip()
                or len(raw) > web_console_setup.INPUT_PATH_MAX_CHARS
                or any(unicodedata.category(char) == "Cc"
                       for char in raw)):
            return None, {"code": "PATH", "reason":
                          "must be a non-empty absolute path string within "
                          "the documented length bound"}
        if raw.startswith("\\\\"):
            return None, {"code": "PATH", "reason":
                          "UNC and device paths are not supported"}
        candidate = Path(raw)
        if not candidate.is_absolute():
            return None, {"code": "PATH", "reason":
                          "the path is not absolute"}
        # PurePath collapses "." segments, so the raw string is scanned:
        # dot and dot-dot segments are refused before any resolution.
        if any(segment in (".", "..")
               for segment in re.split(r"[\\/]+", raw)):
            return None, {"code": "PATH", "reason":
                          "dot and dot-dot segments are refused"}
        try:
            resolved = candidate.resolve()
            # Reparse/symlink refusal must inspect the SELECTION itself
            # (lstat, no follow) before resolve() follows it anywhere.
            lstat = candidate.lstat()
        except (OSError, ValueError):
            return None, {"code": "PATH", "reason":
                          "the path could not be resolved on this "
                          "filesystem"}
        if sys.platform == "win32" and getattr(lstat, "st_file_attributes",
                                               0) & REPARSE_POINT_FLAG:
            return None, {"code": "REPARSE", "reason":
                          "symlink and reparse-point selections are "
                          "refused; select the real path"}
        if not resolved.exists():
            return None, {"code": "PATH", "reason":
                          "the path does not exist"}
        for other in other_roots:
            try:
                if resolved == other or other in resolved.parents:
                    return None, {"code": "CROSS_ROOT", "reason":
                                  "the path lies inside another registered "
                                  "Runtime Root; cross-Runtime binding is "
                                  "refused"}
            except (OSError, ValueError):
                continue
        if resolved.is_dir():
            kind = "directory"
        elif resolved.is_file():
            kind = "file"
        else:
            return None, {"code": "PATH", "reason":
                          "the path is neither a file nor a directory"}
        return {"resolved": resolved, "display": raw, "kind": kind}, None

    def _walk_input_selection(self, selection: dict) -> dict:
        """Bounded metadata-only walk of one validated selection.

        Symlinks/reparse points are never followed; any child whose
        resolved path escapes the selection is skipped and surfaced in the
        honesty notes. No file content is read.
        """
        resolved = selection["resolved"]
        entries = []
        skipped = 0
        complete = True
        if selection["kind"] == "file":
            try:
                size = resolved.stat().st_size
            except OSError:
                size = None
            return {"path": selection["display"], "kind": "file",
                    "entries": [{"path": resolved.name,
                                 "name": resolved.name, "kind": "file",
                                 "size_bytes": size}],
                    "complete": True, "error": None}
        pending = [(resolved, 0)]
        while pending:
            current, depth = pending.pop(0)
            try:
                children = sorted(current.iterdir(), key=lambda p: p.name)
            except OSError:
                complete = False
                continue
            for child in children:
                if len(entries) >= web_console_setup.INPUT_WALK_MAX_ENTRIES:
                    complete = False
                    pending = []
                    break
                try:
                    lstat = child.lstat()
                except OSError:
                    skipped += 1
                    continue
                if sys.platform == "win32" \
                        and getattr(lstat, "st_file_attributes", 0) \
                        & REPARSE_POINT_FLAG:
                    skipped += 1
                    continue
                try:
                    child_resolved = child.resolve()
                    if not child_resolved.is_relative_to(resolved):
                        skipped += 1
                        continue
                except (OSError, ValueError):
                    skipped += 1
                    continue
                if child.is_dir():
                    if depth + 1 <= web_console_setup.INPUT_WALK_MAX_DEPTH:
                        pending.append((child, depth + 1))
                    continue
                if not child.is_file():
                    continue
                try:
                    size = child.stat().st_size
                except OSError:
                    size = None
                entries.append({
                    "path": child.relative_to(resolved).as_posix(),
                    "name": child.name, "kind": "file", "size_bytes": size})
        if skipped:
            selection_notes = (f"{skipped} symlink/reparse/unreadable "
                               "entries were skipped (never followed)")
        else:
            selection_notes = ""
        return {"path": selection["display"], "kind": "directory",
                "entries": entries, "complete": complete, "error": None,
                "note": selection_notes or None}

    def _setup_inputs(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        try:
            payload = self._read_json_body()
        except BodyError as exc:
            self._send_body_error(exc)
            return
        try:
            request = web_console_setup.validate_inputs_request(payload)
        except web_console_control.ControlRequestError as exc:
            self._send_json(400, error_envelope(
                "SETUP_INVALID_PAYLOAD",
                "the input registration request was rejected",
                {"field": exc.field, "reason": str(exc)}))
            return
        if request.get("decision") == "NONE_NEEDED":
            draft, _ = self._load_draft(runtime_id)
            draft = web_console_setup.merge_none_needed_into_draft(draft)
            self._save_draft(runtime_id, draft)
            self._send_json(200, {
                "schema_version": SCHEMA_VERSION, "ok": True,
                "runtime": meta, "inputs": draft["inputs"]})
            return
        own_root = Path(entry["root"]).resolve()
        try:
            other_roots = [Path(item["root"]).resolve()
                           for item in self.config.registry.list_entries()
                           if item["id"] != runtime_id]
        except web_console_registry.RegistryError as exc:
            self._registry_error(exc)
            return
        selections = []
        rejections = []
        for raw in request["paths"]:
            normalized, rejection = self._normalize_input_path(
                raw, own_root, other_roots)
            if rejection is not None:
                rejections.append({"path": raw[:web_console_setup.
                                               INPUT_PATH_MAX_CHARS],
                                   "reason": rejection["reason"],
                                   "code": rejection["code"]})
                continue
            selections.append(normalized)
        if rejections:
            self._send_json(400, error_envelope(
                self._input_rejection_kind(rejections),
                "input registration was refused; nothing was stored",
                {"rejections": rejections}))
            return
        walked = [self._walk_input_selection(selection)
                  for selection in selections]
        inventory = web_console_setup.build_input_inventory(walked)
        draft, _ = self._load_draft(runtime_id)
        try:
            draft = web_console_setup.merge_inputs_into_draft(draft,
                                                              inventory)
        except web_console_control.ControlRequestError as exc:
            self._send_json(400, error_envelope(
                "SETUP_INPUT_PATH_INVALID",
                "the inventory exceeds the documented bound; nothing was "
                "stored", {"reason": str(exc)}))
            return
        self._save_draft(runtime_id, draft)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "inputs": draft["inputs"]})

    def _setup_supervisor(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            payload = self._read_json_body()
        except BodyError as exc:
            self._send_body_error(exc)
            return
        try:
            config = web_console_setup.validate_supervisor_config(payload)
        except web_console_control.ControlRequestError as exc:
            self._send_json(400, error_envelope(
                "SETUP_INVALID_PAYLOAD",
                "the Supervisor configuration request was rejected",
                {"field": exc.field, "reason": str(exc)}))
            return
        draft, _ = self._load_draft(runtime_id)
        draft = web_console_setup.merge_supervisor_into_draft(draft, config)
        self._save_draft(runtime_id, draft)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "supervisor": {
                "schema_version": web_console_setup.SETUP_SCHEMA_VERSION,
                **config,
                "draft_only": True,
                "note": ("v1.2 exposes no Supervisor model-config "
                         "interface, so this choice is a Console-side "
                         "setup draft; nothing was applied to the Runtime "
                         "and no active turn was changed")},
            "capabilities": web_console_setup.capability_report(None)})

    def _setup_zcode(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        template_path = root / ZCODE_TEMPLATE_RELATIVE
        try:
            if not template_path.is_file():
                raise FileNotFoundError("template is missing")
            raw = template_path.read_bytes()
        except OSError:
            self._send_json(502, error_envelope(
                "ZCODE_PROMPT_TEMPLATE_UNAVAILABLE",
                "the Runtime Root does not contain the canonical "
                "ZCODE_SCHEDULED_AUTOMATION_PROMPT.md template"),
                with_body=with_body)
            return
        if len(raw) > web_console_setup.ZCODE_TEMPLATE_MAX_BYTES:
            self._send_json(413, error_envelope(
                "ZCODE_PROMPT_TEMPLATE_TOO_LARGE",
                "the canonical prompt template exceeds the documented "
                "bound"), with_body=with_body)
            return
        try:
            prompt = web_console_setup.render_zcode_prompt(
                web_console_setup.decode_zcode_template(raw),
                entry["root"])
        except ValueError as exc:
            self._send_json(502, error_envelope(
                "ZCODE_PROMPT_TEMPLATE_INVALID", str(exc)),
                with_body=with_body)
            return
        draft, _ = self._load_draft(runtime_id)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "zcode": {
                "schema_version": web_console_setup.SETUP_SCHEMA_VERSION,
                "runtime_root": entry["root"],
                "prompt": prompt,
                "prompt_bytes": len(prompt.encode("utf-8")),
                "steps": list(web_console_setup.ZCODE_AUTOMATION_STEPS),
                "acknowledged": draft["zcode"]["acknowledged"],
                "acknowledged_at": draft["zcode"]["acknowledged_at"],
                "automation_state": {
                    "known": False, "unknown_by_design": True,
                    "note": ("the Console never inspects or claims ZCode "
                             "Automation state; acknowledge only after "
                             "you have configured it yourself")}},
        }, with_body=with_body)

    def _setup_zcode_acknowledge(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        try:
            payload = self._read_json_body(allow_empty=True)
        except BodyError as exc:
            self._send_body_error(exc)
            return
        if payload:
            self._send_json(400, error_envelope(
                "SETUP_INVALID_PAYLOAD",
                "zcode/acknowledge takes no parameters; send {} or no "
                "body"))
            return
        draft, _ = self._load_draft(runtime_id)
        draft = web_console_setup.merge_zcode_acknowledgement(draft,
                                                              now_iso())
        self._save_draft(runtime_id, draft)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "zcode": {"schema_version":
                      web_console_setup.SETUP_SCHEMA_VERSION,
                      "acknowledged": True,
                      "acknowledged_at": draft["zcode"]["acknowledged_at"]}})

    def _setup_workshop(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        doc_path = self.config.console_root / \
            web_console_setup.WORKSHOP_DOC_RELATIVE
        doc_text = None
        doc_truncated = False
        notes = []
        try:
            raw = doc_path.read_bytes()
        except OSError:
            notes.append("the canonical workshop document could not be "
                         "read")
        else:
            try:
                doc_text = raw.decode("utf-8")
            except UnicodeDecodeError:
                notes.append("the canonical workshop document is not "
                             "valid UTF-8")
            else:
                if len(raw) > web_console_setup.CONTEXT_PACK_MAX_DOC_BYTES:
                    doc_text = doc_text[:web_console_setup.
                                        CONTEXT_PACK_MAX_DOC_BYTES]
                    doc_truncated = True
        draft, draft_note = self._load_draft(runtime_id)
        if draft_note:
            notes.append(draft_note)
        inventory = draft["inputs"] \
            if isinstance(draft.get("inputs"), dict) \
            and draft["inputs"].get("decision") == "REGISTERED" else None
        pack = web_console_setup.compose_goal_workshop_pack(
            doc_text=doc_text,
            runtime_facts={"label": entry["label"],
                           "root": entry["root"],
                           "status_schema_version": None},
            project_type=draft.get("project_type"),
            input_inventory=inventory)
        startup = web_console_setup.compose_startup_prompt(
            project_type=draft.get("project_type"))
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "goal_workshop": {
                "schema_version": web_console_setup.SETUP_SCHEMA_VERSION,
                "context_pack": pack,
                "startup_prompt": startup,
                "doc_truncated": doc_truncated,
                "external_automation": "none_by_design",
                "honesty": {"notes": notes}},
        }, with_body=with_body)

    def _run_setup_preflight(self, root: Path) -> dict:
        """Bounded run of the Runtime's own read-only preflight script."""
        script = root / "scripts" / "preflight.py"
        if not script.is_file():
            return {"ok": False, "exit_code": None,
                    "head": "the Runtime preflight script is missing"}
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [sys.executable, str(script)], capture_output=True,
                cwd=str(root),
                timeout=SETUP_PREFLIGHT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return {"ok": False, "exit_code": None,
                    "head": f"preflight did not finish within "
                            f"{SETUP_PREFLIGHT_TIMEOUT_SECONDS} seconds"}
        except OSError as exc:
            return {"ok": False, "exit_code": None,
                    "head": f"preflight could not be launched: {exc}"}
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout = (completed.stdout or b"").decode("utf-8",
                                                  errors="replace")
        head = stdout[:STDOUT_ERROR_HEAD_BYTES]
        passed = completed.returncode == 0 and "PREFLIGHT: OK" in stdout
        return {"ok": passed, "exit_code": completed.returncode,
                "head": head or ("PREFLIGHT: OK" if passed else
                                 "preflight produced no output"),
                "elapsed_ms": elapsed_ms}

    def _compute_readiness(self, root: Path, meta: dict) -> tuple:
        """Shared readiness computation (GET readiness and the start gate)."""
        draft, draft_note = self._load_draft(meta["id"])
        goal_validation, exists_for_draft = self._setup_goal_context(
            root, draft)
        status, payload = self.server.status_result(
            runtime_root=root, runtime_meta=meta)
        if status != 200:
            status_failure = (payload or {}).get("error", {}).get("code",
                                                                  "UNKNOWN")
            status_doc = None
        else:
            status_failure = None
            status_doc = payload["control_plane"]["status"]
        preflight = self._run_setup_preflight(root)
        from web_console_fresh_install import interpret_fresh_install_preflight
        preflight = interpret_fresh_install_preflight(
            root=root, installation=self.config.console_root,
            preflight=preflight, status_doc=status_doc)
        python_check = {
            "ok": sys.version_info[:2] >= MIN_PYTHON_VERSION,
            "python_version": sys.version.split()[0]}
        profile_present = False
        project_type = draft.get("project_type")
        if isinstance(project_type, str):
            profile_present = (root / "profiles" / project_type
                               / "PROFILE.json").is_file()
        # The Goal Anchor check is about the wizard's own target project.
        project_id_exists = exists_for_draft
        readiness = web_console_setup.readiness_document(
            draft=draft, goal_validation=goal_validation,
            status_failure=status_failure, status_doc=status_doc,
            preflight=preflight, python_check=python_check,
            project_id_exists=project_id_exists,
            profile_present=profile_present)
        if draft_note:
            readiness.setdefault("honesty", {"notes": []})
            readiness["honesty"]["notes"].append(draft_note)
        return draft, readiness

    def _setup_readiness(self, runtime_id: str, *, with_body: bool):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        _, readiness = self._compute_readiness(root, meta)
        self._send_json(200, {
            "schema_version": SCHEMA_VERSION, "ok": True, "runtime": meta,
            "readiness": readiness}, with_body=with_body)

    def _setup_start(self, runtime_id: str):
        resolved = self._resolved_entry(runtime_id)
        if resolved is None:
            return
        entry, meta = resolved
        root = Path(entry["root"])
        try:
            payload = self._read_json_body(allow_empty=True)
        except BodyError as exc:
            self._send_body_error(exc)
            return
        if payload:
            self._send_json(400, error_envelope(
                "SETUP_INVALID_PAYLOAD",
                "setup/start takes no parameters; send {} or no body"))
            return
        with self._setup_start_lock(runtime_id):
            draft, readiness = self._compute_readiness(root, meta)
            if not readiness["ready"]:
                failing = [check["key"] for check in readiness["checks"]
                           if check["state"] != "PASS"]
                self._send_json(409, error_envelope(
                    "SETUP_NOT_READY",
                    "start is fail-closed: not every readiness check "
                    "passes; nothing was bootstrapped or started",
                    {"failing_checks": failing,
                     "checks": readiness["checks"]}))
                return
            bootstrap = self._run_bootstrap(root, draft)
            if bootstrap.get("failed"):
                self._send_json(bootstrap.pop("status"), error_envelope(
                    bootstrap.pop("code"), bootstrap.pop("message"),
                    bootstrap.get("detail")))
                return
            start_result = self._run_start_entry(root, meta)
            if start_result.get("failed"):
                self._send_json(start_result.pop("status"),
                                error_envelope(start_result.pop("code"),
                                               start_result.pop("message"),
                                               start_result.get("detail")))
                return
            self._send_json(200, {
                "schema_version": SCHEMA_VERSION, "ok": True,
                "runtime": meta,
                "start": {
                    "schema_version":
                        web_console_setup.SETUP_SCHEMA_VERSION,
                    "bootstrap": bootstrap,
                    "start_invoked": True,
                    "start_entry_point": START_SCRIPT_RELATIVE,
                    "verification": start_result["verification"],
                    "runtime_status": start_result.get("runtime_status")}})

    def _setup_start_lock(self, runtime_id: str) -> threading.Lock:
        with self.server._setup_start_locks_guard:
            if runtime_id not in self.server._setup_start_locks:
                self.server._setup_start_locks[runtime_id] = threading.Lock()
            return self.server._setup_start_locks[runtime_id]

    def _run_bootstrap(self, root: Path, draft: dict) -> dict:
        """Bootstrap the new project through the formal start_project.py
        entry point only (argument vector, no shell, bounded timeout). The
        Goal file is a temporary Console-side handoff file, never an
        authoritative write."""
        script = root / BOOTSTRAP_SCRIPT_RELATIVE
        if not script.is_file():
            return {"failed": True, "status": 502,
                    "code": "SETUP_BOOTSTRAP_UNAVAILABLE",
                    "message": "the Runtime Root does not contain the "
                               "formal start_project.py entry point",
                    "detail": None}
        handoff_dir = self._setup_dir("handoff-" + uuid.uuid4().hex[:8])
        handoff_dir.mkdir(parents=True, exist_ok=True)
        goal_file = handoff_dir / "bootstrap-goal.md"
        goal_file.write_bytes(
            draft["goal_markdown"].encode("utf-8"))
        command = [sys.executable, str(script), "--root", str(root),
                   "--project-id", draft["project_id"],
                   "--project-type", draft["project_type"],
                   "--goal-file", str(goal_file)]
        started = time.monotonic()
        try:
            completed = subprocess.run(
                command, capture_output=True,
                cwd=str(self.config.console_root),
                timeout=SETUP_BOOTSTRAP_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            return {"failed": True, "status": 504,
                    "code": "SETUP_BOOTSTRAP_TIMEOUT",
                    "message": "the bootstrap helper did not finish within "
                               f"{SETUP_BOOTSTRAP_TIMEOUT_SECONDS} seconds",
                    "detail": None}
        except OSError as exc:
            return {"failed": True, "status": 502,
                    "code": "SETUP_BOOTSTRAP_UNAVAILABLE",
                    "message": f"failed to launch the bootstrap helper: "
                               f"{exc}", "detail": None}
        finally:
            shutil.rmtree(handoff_dir, ignore_errors=True)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        stdout = (completed.stdout or b"").decode("utf-8",
                                                  errors="replace")
        stderr_head = (completed.stderr or b"").decode(
            "utf-8", errors="replace")[:STDOUT_ERROR_HEAD_BYTES]
        if completed.returncode != 0:
            mapping = {
                2: (400, "SETUP_BOOTSTRAP_REFUSED",
                    "the bootstrap helper refused the input"),
                3: (409, "SETUP_ACTIVE_PROJECT_OCCUPIED",
                    "the Runtime already has an active project in a "
                    "non-terminal state; finish or clear it first"),
                4: (409, "SETUP_PROJECT_EXISTS",
                    "a project with this id already exists; projects are "
                    "never overwritten"),
                5: (409, "SETUP_POINTER_INVALID",
                    "the existing ACTIVE_PROJECT pointer is invalid; the "
                    "bootstrap refused to proceed"),
            }
            status, code, message = mapping.get(
                completed.returncode,
                (502, "SETUP_BOOTSTRAP_ERROR",
                 "the bootstrap helper reported an error"))
            return {"failed": True, "status": status, "code": code,
                    "message": message,
                    "detail": {"exit_code": completed.returncode,
                               "stderr_head": stderr_head,
                               "elapsed_ms": elapsed_ms}}
        document = _parse_helper_document(stdout)
        if not isinstance(document, dict) \
                or document.get("event") != "PROJECT_CREATED":
            return {"failed": True, "status": 502,
                    "code": "SETUP_BOOTSTRAP_ERROR",
                    "message": "the bootstrap helper did not report a "
                               "verifiable PROJECT_CREATED event",
                    "detail": {"stdout_head":
                               stdout[:STDOUT_ERROR_HEAD_BYTES],
                               "elapsed_ms": elapsed_ms}}
        return {"exit_code": 0,
                "event": document.get("event"),
                "project_id": document.get("project_id"),
                "project_type": document.get("project_type"),
                "next_message_id": document.get("next_message_id"),
                "goal_sha256": document.get("goal_sha256"),
                "elapsed_ms": elapsed_ms}

    def _run_start_entry(self, root: Path, meta: dict) -> dict:
        """Start the Runtime through its own documented START_AGENT_SYSTEM
        entry point (fixed argument vector, detached, output to a
        Console-side log), then verify with the bounded status probe."""
        script = root / START_SCRIPT_RELATIVE
        if not script.is_file():
            return {"failed": True, "status": 502,
                    "code": "SETUP_START_SCRIPT_UNAVAILABLE",
                    "message": "the Runtime Root does not contain the "
                               "documented START_AGENT_SYSTEM.ps1 entry "
                               "point", "detail": None}
        powershell = shutil.which("powershell.exe") \
            or shutil.which("powershell")
        if not powershell:
            return {"failed": True, "status": 502,
                    "code": "SETUP_START_UNAVAILABLE",
                    "message": "PowerShell is required to invoke the "
                               "documented Runtime start entry point",
                    "detail": None}
        log_dir = self._setup_dir(meta["id"])
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        log_file = log_dir / f"start-{stamp}-{uuid.uuid4().hex[:8]}.log"
        creationflags = 0
        if sys.platform == "win32":
            # CREATE_NO_WINDOW (an invisible console: DETACHED_PROCESS
            # makes PowerShell exit silently without running the script)
            # plus a new process group so the Runtime loop outlives the
            # Console request thread.
            creationflags = 0x08000000 | 0x00000200
        try:
            with log_file.open("wb") as handle:
                subprocess.Popen(
                    [powershell, "-NoProfile", "-NonInteractive",
                     "-ExecutionPolicy", "Bypass", "-File", str(script)],
                    cwd=str(root), stdout=handle, stderr=handle,
                    stdin=subprocess.DEVNULL,
                    creationflags=creationflags, close_fds=True)
        except OSError as exc:
            return {"failed": True, "status": 502,
                    "code": "SETUP_START_UNAVAILABLE",
                    "message": f"failed to launch the Runtime start entry "
                               f"point: {exc}", "detail": None}
        deadline = time.monotonic() + SETUP_START_VERIFY_SECONDS
        runtime_status = None
        while time.monotonic() < deadline:
            status, payload = self.server.status_result(
                runtime_root=root, runtime_meta=meta)
            if status == 200:
                status_doc = payload["control_plane"]["status"]
                runtime_status = status_doc.get("runtime_status")
                if runtime_status == "RUNNING":
                    return {"failed": False,
                            "verification": "STARTED_VERIFIED",
                            "runtime_status": runtime_status}
            time.sleep(1.0)
        return {"failed": False, "verification": "STARTED_UNVERIFIED",
                "runtime_status": runtime_status}

    # -- static serving -------------------------------------------------------

    def _serve_index(self, *, with_body: bool):
        index = self.config.static_index
        try:
            body = index.read_bytes()
        except OSError:
            self._send_json(500, error_envelope(
                "STATIC_UNAVAILABLE", "the landing page file is unavailable"),
                with_body=with_body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _send_json(self, status: int, payload: dict, *,
                   with_body: bool = True, extra_headers=None):
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.end_headers()
        if with_body:
            self.wfile.write(body)

    def _send_raw_bytes(self, status: int, body: bytes, *,
                        with_body: bool = True, extra_headers=None):
        self.send_response(status)
        for name, value in extra_headers or []:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if with_body:
            self.wfile.write(body)


def cmd_serve(args) -> int:
    if args.port < 1:
        raise ConfigError(
            "serve requires an explicit port in 1..65535 (port 0 is reserved "
            "for in-process OS-assigned binding)")
    runtime_root = Path(args.runtime_root or DEFAULT_RUNTIME_ROOT)
    console_root = Path(args.console_root or CONSOLE_ROOT)
    data_dir = Path(args.data_dir or DEFAULT_DATA_DIR)
    server = WebConsoleServer.create(
        host=args.host, port=args.port, runtime_root=runtime_root,
        console_root=console_root, data_dir=data_dir,
        control_timeout=args.control_timeout)
    config = server.config
    # The server owns its log file. Launchers must NOT plumb the server's
    # stdout/stderr through inherited console pipes: a detached server that
    # holds an inherited pipe write-end keeps that pipe from ever reaching
    # EOF, which deadlocks any caller that reads the launcher's output.
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(
            log_path, encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
    logging.root.addHandler(handler)
    logging.root.setLevel(logging.INFO)
    server.write_instance_metadata()
    logging.info(
        "WEB_CONSOLE_SERVER_LISTENING host=%s port=%s pid=%s runtime_root=%s "
        "console_root=%s", config.host, config.port, config.pid,
        config.runtime_root, config.console_root)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("WEB_CONSOLE_SERVER_STOPPING keyboard interrupt")
    finally:
        server.server_close()
        server.remove_own_instance_metadata()
    return 0


def cmd_verify_instance(args) -> int:
    result = verify_instance(args.data_dir, args.runtime_root,
                             health_timeout=args.health_timeout)
    # ASCII escaping keeps redirected output machine-safe under any console
    # codepage; START/STOP parse this document.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="General Agent Runtime v1.3 Web Console backend (P1)")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="run the localhost console backend")
    serve.add_argument("--host", default=DEFAULT_HOST)
    serve.add_argument("--port", type=int, required=True)
    serve.add_argument("--runtime-root", type=Path, default=None)
    serve.add_argument("--console-root", type=Path, default=None)
    serve.add_argument("--data-dir", type=Path, default=None)
    serve.add_argument("--control-timeout", type=float, default=10.0)
    serve.add_argument("--log-file", type=Path, default=None)
    verify = sub.add_parser("verify-instance",
                            help="verify recorded instance metadata")
    verify.add_argument("--data-dir", type=Path, required=True)
    verify.add_argument("--runtime-root", type=Path, required=True)
    verify.add_argument("--health-timeout", type=float, default=2.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            return cmd_serve(args)
        return cmd_verify_instance(args)
    except ConfigError as exc:
        print(f"WEB_CONSOLE_CONFIG_ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"WEB_CONSOLE_IO_ERROR: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
