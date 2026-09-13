"""GOAL-ANCHOR-V1 bounded migration: bind the active project's canonical goal.

GOAL-ANCHOR-V1 makes an isolated active project without a goal_anchor binding
fail closed into HUMAN_REVIEW (legacy-unbound). The only sanctioned recovery for
such a project is this explicit, bounded, exactly-once migration: it binds the
CURRENT bytes of the active project's canonical PROJECT_GOAL.md with
provenance="migration", records the same binding in the Runtime-owned
cross-check state, and refuses to run when any binding already exists (never a
rebind, never a hash update). It performs no other state change, makes no
research judgment, and never touches the goal file itself.

Usage (from the Runtime Root):
    python scripts/migrate_goal_anchor.py [--root <runtime root>]

Exit codes: 0 bound | 2 invalid state | 3 already bound | 4 no active project |
5 lock busy | 6 internal error.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

EXIT_OK = 0
EXIT_INVALID = 2
EXIT_ALREADY_BOUND = 3
EXIT_NO_ACTIVE_PROJECT = 4
EXIT_LOCK_BUSY = 5
EXIT_INTERNAL = 6


def load_runtime_module(root: Path):
    orchestrator = root / "orchestrator.py"
    if not orchestrator.is_file():
        raise RuntimeError(f"runtime orchestrator.py not found: {orchestrator}")
    spec = importlib.util.spec_from_file_location("goal_anchor_migration_orchestrator", orchestrator)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = root
    module.CONTROL = root / "control"
    module.ACTIVE_PROJECT_FILE = module.CONTROL / "ACTIVE_PROJECT.json"
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    root = Path(args.root).resolve()

    try:
        m = load_runtime_module(root)
    except Exception as exc:
        print(f"GOAL_ANCHOR_MIGRATION_FAILED: {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL

    try:
        m.activate_project_scope()
        active = m.ACTIVE_PROJECT
    except Exception as exc:
        print(f"GOAL_ANCHOR_MIGRATION_FAILED: invalid ACTIVE_PROJECT pointer: {exc!r}", file=sys.stderr)
        return EXIT_INVALID
    if active is None:
        print(
            "GOAL_ANCHOR_MIGRATION_FAILED: no isolated active project is set; "
            "legacy single-project mode is not migrated",
            file=sys.stderr,
        )
        return EXIT_NO_ACTIVE_PROJECT
    project_id = active["project_id"]
    project_root = active["project_root"]

    try:
        m.acquire_lock()
    except Exception as exc:
        print(f"GOAL_ANCHOR_MIGRATION_FAILED: orchestrator lock is busy: {exc}", file=sys.stderr)
        return EXIT_LOCK_BUSY

    try:
        state = m.read_project_state()
        if state.get("project_id") != project_id:
            print(
                f"GOAL_ANCHOR_MIGRATION_FAILED: project_state.project_id "
                f"{state.get('project_id')!r} does not match the active project {project_id!r}",
                file=sys.stderr,
            )
            return EXIT_INVALID
        if state.get("goal_anchor") is not None:
            print(
                f"GOAL_ANCHOR_MIGRATION_FAILED: project {project_id} already has a "
                "goal_anchor binding; this tool never rebinds or updates a hash",
                file=sys.stderr,
            )
            return EXIT_ALREADY_BOUND

        binding = m.build_goal_anchor_binding(
            project_root, "PROJECT_GOAL.md", provenance="migration")
        state["goal_anchor"] = binding
        state["updated_at"] = m.stamp()
        m.atomic_json(m.PROJECT_STATE, state)

        runtime = m.load_runtime()
        runtime["goal_anchor_binding"] = {
            "schema_version": m.GOAL_ANCHOR_SCHEMA_VERSION,
            "project_id": project_id,
            "goal_path": binding["goal_path"],
            "goal_sha256": binding["goal_sha256"],
            "provenance": binding["provenance"],
            "recorded_at": m.stamp(),
        }
        m.save_runtime(runtime)

        # Read-back proof: the migrated project must verify cleanly end to end.
        reread_state = m.read_project_state()
        verified = m.verify_goal_anchor_with_runtime(m.load_runtime(), reread_state)
        if not verified or verified["goal_sha256"] != binding["goal_sha256"]:
            raise RuntimeError("post-migration verification failed")
        m.log(
            "GOAL-ANCHOR-V1 bounded migration bound the canonical project goal",
            project_id=project_id,
            goal_path=binding["goal_path"],
            goal_sha256=binding["goal_sha256"],
            bound_at=binding["bound_at"],
        )
    except Exception as exc:
        print(f"GOAL_ANCHOR_MIGRATION_FAILED: {exc!r}", file=sys.stderr)
        return EXIT_INTERNAL
    finally:
        m.release_lock()

    print(json.dumps({
        "event": "GOAL_ANCHOR_MIGRATED",
        "project_id": project_id,
        "goal_path": binding["goal_path"],
        "goal_sha256": binding["goal_sha256"],
        "provenance": binding["provenance"],
    }, ensure_ascii=False))
    print("Next step: resolve the HUMAN_REVIEW pause through the normal "
          "RESUME_HUMAN_REVIEW.ps1 receipt flow.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
