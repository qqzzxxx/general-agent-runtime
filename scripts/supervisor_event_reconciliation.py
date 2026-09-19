"""Orphan pending Supervisor event retirement: ORPHAN-SUPERVISOR-EVENT-RECONCILIATION-V1.

One bounded, mechanically proven Runtime-owned reconciliation for one
confirmed pre-start hygiene gap: a Runtime-global pending Supervisor event
that predates the active project and binds no live Runtime work still owns
the next Supervisor invocation at startup, producing a factually wrong
first-turn trigger (e.g. a bare legacy EXECUTOR_RESULT_READY serviced
before a fresh project's ORCHESTRATOR_START).

The classification and retirement semantics are owned by the target tree's
orchestrator (classify_pending_supervisor_event /
reconcile_pending_supervisor_event); this tool is only the operator entry
point.  Fail-closed:

* LIVE — the event is backed by authoritative state (current task,
  authorization, claim, completion ledger, archived brief) — is preserved
  and serviced exactly as before;
* UNCERTAIN — orphan status cannot be proven mechanically (identity-bearing
  payload without a referent, unknown reason, already-serviced or
  terminal-audit event, or any live work source present) — stays visible
  and pending; nothing is discarded;
* ORPHAN — positive mechanical evidence that no live transaction or work
  item exists for the event — may be retired.  Retirement moves the event
  into the append-only Runtime history record
  `retired_supervisor_events` (full event snapshot, original recording
  time, retirement time, classification evidence) and clears the pending
  slot, so startup resumes from the project's real current state while the
  provenance stays auditable.

Phases:
* prove -- classification report only; writes nothing;
* retire -- re-classifies under the Runtime fence and commits the
  retirement only for a proven orphan; refuses otherwise without writing.

Exactly-once: the durable facts (cleared pending slot plus the retirement
history record) are the authority, so a re-run after retirement reports
NONE and writes nothing; save_runtime unions the history and refuses to
resurrect a retired event from a stale snapshot.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import uuid
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[1]
_TOOL_RUNTIME_ROOT = DEFAULT_ROOT
EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_CONFLICT = 3
EXIT_INTERNAL = 5

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))


class ReconciliationError(RuntimeError):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


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
    m.LOCK_FILE = m.CONTROL / ".orchestrator.lock"
    m.USER_ATTENTION = m.CONTROL / "USER_ATTENTION.json"
    m.USER_STATUS_REPORT = root / "reports" / "USER_STATUS.md"
    m.CODEX_LAST_OUTPUT = root / "CODEX_LAST_OUTPUT.txt"


def load_runtime_module(runtime_root: Path, target_root: Path):
    """Load the tool's own Runtime code and bind its paths to the target root."""
    orchestrator = Path(runtime_root) / "orchestrator.py"
    if not orchestrator.is_file():
        raise ReconciliationError(
            EXIT_INTERNAL, f"runtime orchestrator.py not found: {orchestrator}")
    spec = importlib.util.spec_from_file_location(
        f"supervisor_event_reconciliation_runtime_{uuid.uuid4().hex}", orchestrator)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    bind_runtime_paths(module, target_root)
    return module


def _print_report(report: dict) -> None:
    print(json.dumps(
        {key: value for key, value in report.items() if key != "retirement_record"},
        ensure_ascii=False, indent=2))
    classification = report.get("classification")
    print(f"SUPERVISOR_EVENT_CLASSIFICATION: {classification} "
          f"reason={report.get('reason')!r}")
    for item in report.get("checks") or []:
        mark = "PASS" if item.get("passed") else "FAIL"
        print(f"  [{mark}] {item.get('check')}: {item.get('detail')}")
    for referent in report.get("referents") or []:
        print(f"  [LIVE] authoritative referent: {referent}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify (prove) or retire (retire) an orphan Runtime-global "
                    "pending Supervisor event. Fail-closed: LIVE and UNCERTAIN "
                    "events are always preserved.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("prove", help="print the mechanical classification; write nothing")
    sub.add_parser("retire", help="retire the pending event iff it is a proven orphan")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    try:
        m = load_runtime_module(_TOOL_RUNTIME_ROOT, root)
        m.activate_project_scope()
        if args.command == "prove":
            report = m.reconcile_pending_supervisor_event(retire=False)
            _print_report(report)
            return EXIT_OK if report["classification"] in ("ORPHAN", "NONE") else EXIT_REFUSED
        report = m.reconcile_pending_supervisor_event(retire=True)
        _print_report(report)
        if report["classification"] == "NONE":
            print("SUPERVISOR_EVENT_RETIRED: NO_PENDING_EVENT nothing to do")
            return EXIT_OK
        if report.get("changed"):
            print(f"SUPERVISOR_EVENT_RETIRED: RETIRED reason={report['reason']!r}")
            return EXIT_OK
        print(f"SUPERVISOR_EVENT_RETIRED: PRESERVED_{report['classification']} "
              f"reason={report['reason']!r}"
              + (f" ({report['refusal']})" if report.get("refusal") else ""))
        return EXIT_REFUSED
    except ReconciliationError as exc:
        print(f"SUPERVISOR_EVENT_RECONCILIATION_FAILED: {exc}", file=sys.stderr)
        return exc.code
    except Exception as exc:  # noqa: BLE001
        print(f"SUPERVISOR_EVENT_RECONCILIATION_FAILED: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
