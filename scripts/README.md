# v2 automation scripts

Phase 9 makes deep inspection demand-driven. `supervisor_inspect.py` supplies
bounded read-only text slices for a decision question; `supervisor_intelligence.py`
records per-invocation prompt size, duration and observed helper read cost beside
the existing authoritative provider usage. `measure_supervisor_efficiency.py`
reproduces the offline Phase 8/9 B3 comparison. See
[the Phase 9 report](../docs/v1.4-supervisor-intelligence-efficiency.md).

Phase 8 adds `supervisor_context.py` for the Supervisor's Outcome Owner decision
view and `measure_supervisor_context.py` for the offline Phase 7/V2 comparison and
recorded A/B dispatch analysis. Ordinary delegation may include typed
`outcome_context`; Runtime construction, authorization and Executor V2 stay intact.
See [the Phase 8 report](../docs/v1.4-supervisor-v2.md).

Supervisor routing does **not** use Codex Computer Use, GUI automation, browser
control, or the ZCode CLI for dispatch. The V2 Executor can use its available native
host tools within the authorized task.

Active runtime:

`orchestrator.py` -> `codex exec` -> root `TO_ZCODE.md` -> existing ZCode Desktop Scheduled Automation -> root `SUPERVISOR_BRIEF.md` + `ZCODE_DONE.flag` -> `orchestrator.py` -> `codex exec`.

The old Computer Use supervisor and PoC orchestrator are preserved under `legacy/` only and must not be run.

## Start

Use `START_AGENT_SYSTEM.ps1` from the project folder. It refuses to start if another `orchestrator.py` process for this project is already running.

## Stop

Create `control/STOP` (or use `STOP_AGENT_SYSTEM.ps1`). A real STOP is checked before any new Codex call and is terminal for the running orchestrator.

`supervisor_control.py` is the reusable v1.2 operator/control-plane API. Its
subcommands expose JSON-capable status, exact Supervisor dispatch history,
authoritative Executor feedback, a combined timeline, immutable interventions,
pause, and resume. Root PowerShell wrappers are the normal operator entry points;
future UI code should call the structured operations instead of scraping display text.
JSON mode is a strict machine interface: stdout is one ASCII-escaped JSON document,
while human lifecycle/start messages use a separate stream. Task queries validate the
full archive/seal trust boundary and report `AUTHORIZED_VALID`, `UNAUTHORIZED`,
`INCOMPLETE`, or `CORRUPT` without repairing records.

New claims always require a valid v1.2 dispatch archive and authorization seal.
Archive-less pre-upgrade metadata is not upgraded implicitly; only a task that already
owns its permanent claim can use the completion-only legacy recovery path.

`executor_entry.py --contract-version 2` is the current Executor entry. It returns
an outcome contract and opaque session; `executor_work.py --session <session>`
accepts one JSON operation on stdin. Scoped file read/list/write/copy and semantic
finish operations check live authority internally, including before replacement.
LOW/NORMAL/HIGH selects working style only. Phase 6 permits native host tools by
default and exposes work/input locations, preserving explicit task restrictions.
Use `checkpoint` before native batches. Filesystem/fetch/render operations remain
optional utilities; finish still owns all publication/completion. See
[the Phase 6 contract](../docs/v1.4-host-native-executor.md).

Phase 7 keeps invalid semantic finishes correctable by the same live owner until
the validated finish plan is durable. Only `INVALID_RESULT/CORRECT_AND_RESUBMIT`
permits correction after a rejected V2 finish; the plan, publication and completion
remain immutable. V2 FV judgments use `verification`; Runtime constructs the FV
completion containers. See [Phase 7 recovery](../docs/v1.4-semantic-finish-recovery.md).

The default `executor_entry.py` API remains V1 for compatibility. Fresh V1 wakes run
`python scripts/executor_entry.py`; only JSON `READY` and exit 0 allow work. The
result contains a task view, the original attempt identity/token and validated
project/workspace/staging paths. An owning session can reenter with
`--resume-token <retained-token>`. Resume never acquires or recovers a token.
Every non-ready response has `action: STOP` and no task/credential payload. Existing
fence checks remain required after startup. Runtime finish owns publication and completion. This is
cooperative same-user fencing, not an OS sandbox. See
[the Phase 2 report](../docs/v1.4-executor-entry-adapter.md).

`executor_finish.py --claim-token <retained-token> --result finish.json` is the
v1.4 Phase 3 finish API. The result path is relative to the owning attempt workspace.
The Executor supplies artifacts, outcome, findings, evidence, limitations and
task-specific completion data. Runtime hashes and publishes candidates, constructs
the receipt/staging, then uses the existing completion commit. Every structured
response has `action: STOP`. No Executor-authored hash, identity copy, staging file
or per-artifact publish call is needed. The low-level APIs remain compatible.
See [the Phase 3 contract and recovery report](../docs/v1.4-runtime-owned-completion.md).

## Resume HUMAN_REVIEW

Do not edit `project_state.json` and do not invent an approval token. Copy
`control/HUMAN_DECISION_TEMPLATE.json` to a working decision-input file and replace
the placeholders with the human's verbatim decision and constraints. Then prepare a
hash-bound receipt without changing lifecycle state:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare `
  -ProjectId <project-id> `
  -DecisionFile <decision-input.json> `
  -ReceiptFile <prepared-receipt.json>
```

Inspect the prepared receipt. After explicit human submission, apply it:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply -ReceiptFile <prepared-receipt.json>
```

Successful apply performs only `HUMAN_REVIEW -> SUPERVISOR_TURN`; it does not start
the Orchestrator or publish an Executor task. Start the normal Runtime separately
with `START_AGENT_SYSTEM.ps1` when the human intends the next Codex Supervisor turn.

The first resumed Supervisor turn is transactional and read-only. It returns a strict
structured decision to the Orchestrator instead of editing lifecycle or wire files.
Timeout, nonzero exit, malformed/invalid output, or any pre-commit failure leaves the
receipt `PENDING_SUPERVISOR_REVIEW` for safe replay. One atomic project-state commit stores
both the accepted decision and `CONSUMED` receipt binding; a consumed receipt is audit
history and is never injected again, even if the project later returns to
`SUPERVISOR_TURN`. A later HUMAN_REVIEW uses a new receipt and preserves the prior
consumption ledger.

## Retire an orphan pending Supervisor event

A Runtime-global pending Supervisor event that provably binds no live Runtime
work (for example a bare legacy `EXECUTOR_RESULT_READY` recorded before a
project migration) still owns the next Supervisor invocation at startup. The
classification and retirement semantics are owned by `orchestrator.py`
(ORPHAN-SUPERVISOR-EVENT-RECONCILIATION-V1); `supervisor_event_reconciliation.py`
is the operator entry point and refuses to write unless every mechanical check
proves orphan status. Run it only while the Agent system is stopped:

```powershell
python scripts/supervisor_event_reconciliation.py prove
python scripts/supervisor_event_reconciliation.py retire
```

`prove` writes nothing and exits 0 only for a proven orphan (or no pending
event). `retire` commits one fenced transaction: the event moves into the
append-only `retired_supervisor_events` history record with its original
recording time, retirement time, and classification evidence, and the pending
slot is cleared so startup resumes from the project's real current state.
LIVE and UNCERTAIN events are always preserved; retirement is idempotent and a
retired event cannot be resurrected by a stale snapshot.

## Phase 5 capability realization

`executor_work.py` now accepts optional `fetch` and `render` operations in addition
to the Phase 4 filesystem and finish operations. The sealed execution policy grants
exact HTTPS URLs and/or static HTML rendering; no direct ZCode tool is a grant.
`executor_capabilities.py` prints read-only dependency/installation evidence, clearly
distinguished from live session availability. `executor_fetch.py` and
`executor_render.mjs` are private fixed workers, not alternate Executor entry points.

```powershell
python scripts/executor_capabilities.py
python -m unittest discover -s scripts -p "test_executor_capabilities.py"
python scripts/measure_capability_realization.py --output evidence/v1.4-capability-realization
```

The measurement requires installed Node >=22 and Chrome/Chromium/Edge. Optional
`--network-smoke` adds one real HTTPS GET to example.com. It runs private synthetic
Runtime roots; it does not start a model or ZCode. See
[Phase 5 design and evidence](../docs/v1.4-capability-realization.md) for exact scope,
assurance and historical overhead. Phase 6 permits native shell/GUI tools and makes
renderer dependencies optional at entry; the routing to ZCode remains unchanged.

## Phase 6 host-native Executor

```powershell
python -m unittest discover -s scripts -p "test_host_native_executor.py"
python scripts/measure_host_native_executor.py
```

The measurement uses native Node/Chromium with page JavaScript, input/clicks and
screenshots, followed by Runtime finish, in a private synthetic Runtime. It compares
saved Phase 5 contract sources with a real Phase 6 entry. It does not run a live
ZCode model or establish a model-quality improvement.

## Regression tests

From the project directory:

```powershell
python -m unittest scripts.test_orchestrator -v
python -m unittest scripts.test_resume_human_review -v
```

These tests are mechanical fixtures only. They do not claim a real ZCode handshake and do not mark infrastructure READY.
