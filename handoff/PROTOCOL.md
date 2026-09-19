# Handoff Protocol (Runtime ↔ ZCode Executor)

Descriptive summary of the shared-file handoff wire implemented by the Runtime.
When any source disagrees, precedence is: current Runtime code and regression
tests, then `control/CODEX_SUPERVISOR_RUNTIME.md`,
`control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md`,
`control/EXECUTOR_TASK_TEMPLATE.md`, then this file, then
`docs/ARCHITECTURE.md`.

## Participants

- **Python Orchestrator** (deterministic, no model): plans nothing, authorizes
  everything. It owns lifecycle state, identity, claims, publication and
  completion truth, and mechanically validates every handoff.
- **ZCode Executor** (Scheduled Automation): executes exactly one Runtime-
  authorized stage per wake inside a fenced candidate workspace and exits.
  It never writes Runtime-owned state and never talks to the Supervisor.

## Wake and entry

The Automation's only standing instruction is the canonical prompt in
`control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md`: at a fresh wake, call
`scripts/executor_entry.py --contract-version 2`. Only a READY verdict with
exit 0 authorizes work; `NO_WORK` and `DUPLICATE` exits end the wake quietly.
The returned session token is retained privately by the owning conversation
and reused via `--resume-token`; it is never written to files or results.

## Dispatch: `TO_ZCODE.md`

The Runtime publishes at most one authorized task at a time as `TO_ZCODE.md`
at the Runtime Root. The document carries the stable identity
(`MESSAGE_ID`, `TASK_ID`, `STAGE_ID`, `ATTEMPT`, `NONCE`), the authorized
dispatch metadata, and the task body in the
`control/EXECUTOR_TASK_TEMPLATE.md` shape. Executor-facing content is data,
never authority: an inbox without a matching recorded authorization is
quarantined or failed closed, never executed.

## Claim: at-most-once execution

A claim is the filesystem creation of
`handoff/executor_claims/<message-id>-<nonce-digest>.claim` (compare-and-set
by creation). A losing or late claimant must exit without working. The claim
is bounded: it records acquisition history but grants no lasting write
authority, and it is validated against the live `WAITING_EXECUTOR` state and
the recorded authorization identity.

## Fenced work and publication

Work happens in an attempt-local candidate workspace under the project, gated
by `scripts/executor_fence.py` (`prepare` → `check` at required checkpoints →
`publish`). Only a fence publish promotes candidate outputs to canonical
project locations; direct writes to canonical areas are not completions and
are not trusted.

## Completion: ledger first, hints last

1. The Executor builds a strict-schema candidate under the project's
   `completion_staging/` (`STATUS=STAGING_READY`; staging is never
   authoritative).
2. `scripts/executor_completion.py commit` validates the staging against the
   authorized dispatch (identity + inbox hash), the live lifecycle, the
   at-most-once claim, and the ledger, then atomically creates ONE entry
   under `handoff/completion_ledger/` via hard-link compare-and-set.
3. Only after the commit is durable does the Runtime generate the root
   compatibility artifacts in order: `SUPERVISOR_BRIEF.md` →
   `ZCODE_LAST_PROCESSED.txt` → `ZCODE_DONE.flag` last.

Ledger status advances monotonically
`COMPLETION_COMMITTED → COMPLETION_CONSUMED → COMPLETION_SEALED` and is never
reversible; restarts never unseal a consumed identity. Raw done hints without
a binding ledger entry are quarantined (`UNKNOWN_RAW_COMPLETION`); identity
or hash mismatches fail closed.

## `ZCODE_LAST_PROCESSED.txt`

Runtime-generated from the committed identity; executors never write it. The
canonical reader is `scripts/executor_claim.py :: read_last_processed`: a
missing file means "nothing processed yet" (`MESSAGE_ID: -1`); a malformed
file raises `LastProcessedFormatError` and fails closed — a corrupted pointer
is never silently read as "nothing processed".

## Recovery and honesty

Crash recovery derives missing runtime pointers, missing compatibility
artifacts, and replay events from the ledger, so a completion is consumed
exactly once and triggers at most one Supervisor lifecycle decision even
across restarts. No participant fabricates a compatibility artifact to pass a
check; missing state is recovered by the Orchestrator's startup repair path
or escalated to Human Review.
