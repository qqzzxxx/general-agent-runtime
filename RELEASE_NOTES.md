# General Agent Runtime v1.4 Release Notes

v1.4 keeps the v1.3 product foundation — permanent Supervisor dispatch archive,
authoritative Executor feedback history, combined timeline, STEER/AUDIT interventions,
reversible pause/resume, cooperative current-task interruption, claim / fence /
completion-seal semantics, and Runtime-owned Final Verification and Final Acceptance —
and adds the following user-visible capabilities.

## Configurable Supervisor baseline

The Supervisor model and reasoning effort are operator-configurable through
`control/supervisor_control.json` (`supervisor_model` / `supervisor_reasoning_effort`),
including queued per-change configurations that take effect at a turn boundary. The
built-in defaults apply only when the configuration is absent or invalid, and the
effective source is recorded per Supervisor turn.

## Agent Intelligence baseline

Supervisor context now uses Memory Routing V2 (snapshot-chain routing with the current
memory block inlined and older units deferred behind bounded views), together with
refined contract wording for exploration judgment, ambiguity / Human Review escalation,
and task restrictions. The result is materially smaller Supervisor prompts with no loss
of contract coverage.

## Runtime-owned dispatch and completion

The Orchestrator prepares, authorizes, and seals task dispatches and completion records
itself. The Executor contract moves to `scripts/executor_entry.py --contract-version 2`
with an opaque session and JSON operations (`scripts/executor_work.py`), host-native
tools permitted by default, and publication/completion still owned exclusively by the
Runtime fence and completion seal.

## Pickup / execution lifecycle

Dispatches gain an explicit pickup phase with pickup timeouts and claimed-time budgets.
A claimed Executor timeout is reconciled through a dedicated, receipts-once
reconciliation path instead of stalling the lifecycle.

## Human Decision lifecycle and the Final Verification bridge

Human decisions enter the lifecycle as first-class records with a verbatim Decision
Brief projected into the Web Console. A human decision can carry a project through the
Runtime-owned Final Verification and Final Acceptance path — including resume
vocabulary and gate provenance — without manual lifecycle surgery.

## Reconciliation and recovery

Orphan Supervisor events can be retired with provenance, legacy paused stages recover
through one canonical park/resume mechanism, and quota pauses park unclaimed dispatches
so resume re-plans without retry charges.

## Web Console v1.4

The redesigned light-theme Web Console provides Runtime creation (an isolated release
skeleton), a Setup wizard for Goal / inputs / Supervisor / Executor configuration, the
Cockpit with Pause / Resume, Artifacts with per-round provenance, Human Review and
Human Decision surfaces, Final Verification state, and fresh-install readiness checks.
Console instance data, registries, and setup drafts remain local, gitignored data.
