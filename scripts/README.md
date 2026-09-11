# v2 automation scripts

The active system does **not** use Codex Computer Use, GUI automation, browser control, or the ZCode CLI.

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

## Regression tests

From the project directory:

```powershell
python -m unittest scripts.test_orchestrator -v
python -m unittest scripts.test_resume_human_review -v
```

These tests are mechanical fixtures only. They do not claim a real ZCode handshake and do not mark infrastructure READY.
