"""G5A: unified project bootstrap (mechanical creation + activation).

Implements the START_PROJECT contract:
  validate -> build projects\\.creating-<id>-<nonce> -> verify -> atomic rename
  -> atomic ACTIVE_PROJECT.json publication (the commit point, always last).

The launcher performs MECHANICAL initialization only: it makes no research judgments,
creates no tasks, calls no model, and never touches Scheduled Automation.
Runtime history (orchestrator_runtime.json, wire files, claims, archive) is global and
is never reset; the new project's MESSAGE_ID space is seeded strictly above every id the
runtime has ever consumed/dispatched/recorded.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

EXIT_OK = 0
EXIT_VALIDATION = 2
EXIT_ALREADY_RUNNING = 3
EXIT_PROJECT_EXISTS = 4
EXIT_INVALID_POINTER = 5
EXIT_INTERNAL = 6

ALLOWED_TYPES = ("GENERAL", "ACADEMIC_RESEARCH", "SOFTWARE_ENGINEERING", "BUSINESS_RESEARCH")
TERMINAL_REPLACEABLE = {"COMPLETE", "BLOCKED", "STOPPED"}
DEFAULT_MESSAGE_ID_FLOOR = 700100  # historical runtime convention for a fresh install


def fail(code: int, message: str) -> "None":
    print(f"START_PROJECT_FAILED: {message}", file=sys.stderr)
    sys.exit(code)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_runtime_module(root: Path):
    """Import the runtime install's orchestrator and scope its path globals to root."""
    orchestrator = root / "orchestrator.py"
    if not orchestrator.is_file():
        fail(EXIT_INTERNAL, f"runtime orchestrator.py not found: {orchestrator}")
    spec = importlib.util.spec_from_file_location("g5a_runtime_orchestrator", orchestrator)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.ROOT = root
    m.CONTROL = root / "control"
    m.ACTIVE_PROJECT_FILE = m.CONTROL / "ACTIVE_PROJECT.json"
    return m


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:
        return default


def known_message_ids(root: Path) -> list[int]:
    """Every MESSAGE_ID the runtime has ever consumed, dispatched, marked, or reserved."""
    known: list[int] = []

    def add(value):
        if isinstance(value, bool):
            return
        if isinstance(value, int) and value > 0:
            known.append(value)

    runtime = read_json(root / "control" / "orchestrator_runtime.json", {}) or {}
    for key in ("last_consumed_message_id", "last_dispatched_message_id"):
        add(runtime.get(key))
    zlp = root / "ZCODE_LAST_PROCESSED.txt"
    if zlp.exists():
        match = re.search(r"-?\d+", zlp.read_text(encoding="utf-8-sig", errors="replace"))
        if match:
            add(int(match.group(0)))
    legacy_state = read_json(root / "control" / "project_state.json", {}) or {}
    add(legacy_state.get("next_message_id"))
    projects_dir = root / "projects"
    if projects_dir.is_dir():
        for state_path in projects_dir.glob("*/project_state.json"):
            add((read_json(state_path, {}) or {}).get("next_message_id"))
    return known


def main() -> int:
    ap = argparse.ArgumentParser(description="Create and activate a General Agent Runtime project.")
    ap.add_argument("--root", required=True)
    ap.add_argument("--project-id", required=True)
    ap.add_argument("--project-type", required=True)
    ap.add_argument("--goal", default=None)
    ap.add_argument("--goal-file", default=None)
    args = ap.parse_args()
    root = Path(args.root).resolve()

    # ---- input validation (fail closed) ------------------------------------------
    project_id = args.project_id or ""
    m = load_runtime_module(root)
    if not m.PROJECT_ID_PATTERN.fullmatch(project_id):
        fail(EXIT_VALIDATION, f"invalid project id {project_id!r}: must match "
             f"{m.PROJECT_ID_PATTERN.pattern}")
    project_type = (args.project_type or "").upper()
    if project_type not in ALLOWED_TYPES or project_type not in m.SUPPORTED_PROFILES:
        fail(EXIT_VALIDATION,
             f"unsupported project type {args.project_type!r}; allowed: {', '.join(ALLOWED_TYPES)}")
    if args.goal and args.goal_file:
        fail(EXIT_VALIDATION, "provide either --goal or --goal-file, not both")
    if args.goal_file:
        goal_source = Path(args.goal_file)
        if not goal_source.is_file():
            fail(EXIT_VALIDATION, f"goal file not found: {args.goal_file}")
        goal_text = goal_source.read_text(encoding="utf-8-sig")
    elif args.goal and args.goal.strip():
        goal_text = args.goal
    else:
        fail(EXIT_VALIDATION, "a non-empty project goal is required (--goal or --goal-file)")

    # ---- profile + policy must exist and validate BEFORE anything is created ------
    try:
        profile = m.load_profile(project_type)
    except Exception as exc:
        fail(EXIT_VALIDATION, f"profile validation failed: {exc!r}")
    policy = profile["final_verification_policy"]

    # ---- active pointer preflight -------------------------------------------------
    try:
        active = m.load_active_project()
    except RuntimeError as exc:
        fail(EXIT_INVALID_POINTER, f"existing ACTIVE_PROJECT pointer is invalid: {exc}")
    if active is not None:
        active_state = read_json(active["project_root"] / "project_state.json", {}) or {}
        status = str(active_state.get("status") or "").upper()
        if status == "HUMAN_REVIEW" or status not in TERMINAL_REPLACEABLE:
            fail(EXIT_ALREADY_RUNNING,
                 f"ACTIVE_PROJECT_ALREADY_RUNNING: project {active['project_id']!r} is "
                 f"{status or 'UNKNOWN'}; finish or clear it before starting another project")
        print(f"Replacing terminal active project {active['project_id']!r} ({status})")

    # ---- no overwrite of existing projects ----------------------------------------
    projects_dir = root / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    project_dir = projects_dir / project_id
    if project_dir.exists():
        fail(EXIT_PROJECT_EXISTS, f"project already exists: {project_dir} — never overwritten")

    # ---- MESSAGE_ID seed: strictly above every known runtime identity --------------
    known = known_message_ids(root)
    next_message_id = (max(known) + 1) if known else DEFAULT_MESSAGE_ID_FLOOR

    # ---- transactional build -------------------------------------------------------
    staging = projects_dir / f".creating-{project_id}-{uuid.uuid4().hex[:8]}"
    try:
        staging.mkdir(parents=True)
        for sub in ("workspace", "evidence", "reports"):
            (staging / sub).mkdir()
        goal_doc = (f"<!-- PROJECT_ID: {project_id} | PROJECT_TYPE: {project_type} | "
                    f"CREATED_AT: {now_iso()} -->\n\n{goal_text.strip()}\n")
        goal_file_path = staging / "PROJECT_GOAL.md"
        goal_file_path.write_text(goal_doc, encoding="utf-8")
        # GOAL-ANCHOR-V1: bind the canonical goal at bootstrap. The hash is computed
        # over the exact staged bytes (read back from disk) and persisted in
        # project_state.goal_anchor; the Runtime re-verifies it before every
        # Supervisor turn and rejects any silent rebind.
        goal_binding = m.build_goal_anchor_binding(
            staging, "PROJECT_GOAL.md", provenance="bootstrap")
        if hashlib.sha256(goal_file_path.read_bytes()).hexdigest() != goal_binding["goal_sha256"]:
            raise RuntimeError("goal anchor hash does not match the staged PROJECT_GOAL.md bytes")
        (staging / "RESEARCH_STATE.md").write_text(
            "# Project Memory\n\nNo Supervisor decisions yet.\n", encoding="utf-8")
        state = {
            "schema_version": 4,
            "project_id": project_id,
            "project_type": project_type,
            "profile": project_type,
            "status": "SUPERVISOR_TURN",
            "phase": project_type,
            "created_at": now_iso(),
            "started_at": now_iso(),
            "updated_at": now_iso(),
            "goal_file": "PROJECT_GOAL.md",
            "goal_anchor": goal_binding,
            "current_task": None,
            "next_message_id": next_message_id,
            "final_verification": {
                "policy_version": 1,
                "required": True,
                "status": "NOT_STARTED",
                "policy_id": policy["policy_id"],
                "critical_claims": [],
                "claims_hash": None,
                "verification_message_id": None,
                "verification_receipt_sha256": None,
                "verified_at": None,
            },
            "last_supervisor_decision": None,
            "decision_history": [],
            "infrastructure_status": "READY",
            "deadline_at": None,
            "blocked_reason": None,
            "notes": [f"bootstrapped by START_PROJECT ({project_type})"],
        }
        m.atomic_json(staging / "project_state.json", state)
        # verify the staged project the way the runtime will consume it
        reread = read_json(staging / "project_state.json")
        if not isinstance(reread, dict) or reread.get("status") != "SUPERVISOR_TURN":
            raise RuntimeError("staged project_state failed re-read validation")
        prof = m.load_profile(project_type)
        if prof["final_verification_policy"]["policy_id"] != policy["policy_id"]:
            raise RuntimeError("staged policy binding mismatch")
        if os.path.exists(project_dir):
            raise RuntimeError("target project directory appeared during initialization")
        os.rename(staging, project_dir)  # atomic commit of the project directory
    except Exception as exc:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        fail(EXIT_INTERNAL, f"initialization failed (no active pointer written): {exc!r}")

    # ---- commit point: atomically publish the active pointer (LAST step) -----------
    try:
        m.atomic_json(m.ACTIVE_PROJECT_FILE, {
            "schema_version": 1,
            "project_id": project_id,
            "project_root": f"projects/{project_id}",
        })
    except Exception as exc:
        fail(EXIT_INTERNAL, f"project created at {project_dir} but ACTIVE_PROJECT "
             f"publication failed: {exc!r}")

    print(json.dumps({
        "event": "PROJECT_CREATED",
        "project_id": project_id,
        "project_type": project_type,
        "project_root": str(project_dir),
        "profile_policy_id": policy["policy_id"],
        "next_message_id": next_message_id,
        "message_id_seed_basis": known,
        "goal_sha256": goal_binding["goal_sha256"],
        "status": "SUPERVISOR_TURN",
    }, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
