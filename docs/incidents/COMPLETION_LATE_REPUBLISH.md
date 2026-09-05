# Incident model: Consumed Executor Completion Late-Republish (missing completion seal)

**Status:** fixed by COMPLETION-SEAL-V1 (`scripts/executor_completion.py` + Orchestrator
consume/seal redesign). This document is a sanitized, synthetic-identity description of
the defect class; it contains no production identifiers, paths, or data.

## Symptom

During unattended operation of the pre-fix protocol, a Runtime observed this sequence:

1. The Executor claimed dispatch identity `X` (at-most-once claim held).
2. The Executor published completion `A` through the then-current publication contract
   (Executor-written root brief + processed pointer + DONE flag).
3. The Orchestrator consumed `A`, and the Supervisor committed a lifecycle decision
   based on it.
4. The same still-running Executor attempt `X` published completion `B`.
5. The root `SUPERVISOR_BRIEF.md` / `ZCODE_LAST_PROCESSED.txt` / `ZCODE_DONE.flag`
   were re-created, with `B` differing from `A` in both brief hash and timestamp, and
   appearing strictly after the Supervisor's consumption had completed.

There was no second claim; an archive of the consumed `A` existed; the runtime's
duplicate guards were derived, mutable pointers rather than an irreversible record.

## Root cause

The completion publication path was fully Executor-owned. The claim primitive guaranteed
at-most-once **execution**, but nothing guaranteed at-most-once **completion**: after a
completion was consumed, no irreversible authoritative seal existed, so the same attempt
could republish, rewind the processed pointer, and re-create the wake signal. The
Orchestrator consumed "whatever the root brief contained at wake time", so a republished
or forged completion could re-enter (or impersonate) the lifecycle.

## Fix (summary)

- Executor may only build a **candidate** (`completion_staging/`) — never an
  authoritative completion.
- A Runtime-owned helper (`scripts/executor_completion.py`) validates the candidate and
  durably commits exactly one ledger entry per `MESSAGE_ID`
  (`COMPLETION_COMMITTED -> COMPLETION_CONSUMED -> COMPLETION_SEALED`, monotonic,
  restart-proof, compare-and-set).
- Root completion artifacts are Runtime-generated derived views; the Orchestrator
  consumes only ledger-backed completions and quarantines/audits unbound raw signals.
- HUMAN_REVIEW resume classifies a raw DONE hint (genuine unconsumed commit -> fail
  closed; recognizable sealed replay -> quarantine and continue; unknown -> fail
  closed; hash/identity mismatch -> fail closed).

Regression: `scripts/test_completion_seal.py`
(`test_late_executor_republish_after_supervisor_consumption` and the numbered
fault/race cases).
