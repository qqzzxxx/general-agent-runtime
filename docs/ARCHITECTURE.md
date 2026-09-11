# ARCHITECTURE

## Components

```
+--------------------------+        shared files        +---------------------------+
| Python Orchestrator      | <------------------------> | Supervisor backend        |
| (deterministic, no model)|   TO_ZCODE.md (dispatch)   | (high reasoning, planned  |
|  - validates dispatches  |   SUPERVISOR_BRIEF.md *    |  per-stage reviews)       |
|  - binds authorization   |   ZCODE_DONE.flag / _LAST_*|                           |
|  - enforces FV gate      +----------------------------+---------------------------+
|  - owns budget/state     |  claims + completion ledger+---------------------------+
+------------+-------------+ <-------------------------> | Executor automation       |
             |              handoff/executor_claims     | (high throughput, whole-  |
             v              handoff/completion_ledger   |  stage execution)         |
   control/ (runtime state)                               +---------------------------+
   projects/<id>/ (isolated project data)

  * derived compatibility artifacts / wake hints, Runtime-generated from the ledger
```

- **Orchestrator** (`orchestrator.py`) never calls a model itself and never does stage
  work. It validates every handoff mechanically, owns the global runtime state
  (`control/orchestrator_runtime.json`), the dispatch authorization records, the Final
  Verification gate, budget ceilings, and human notifications.
- **Supervisor** receives a compressed context prompt (runtime contract, project memory,
  current state) and returns exactly one machine-readable decision: dispatch a new
  Executor stage, revise, redirect, request human review, or terminate.
- **Executor** receives one stage at a time as a JSON task embedded in `TO_ZCODE.md`,
  executes it fully inside the active project, and publishes a single-fenced-JSON
  receipt.

## Identity model

Every dispatch is identified by the tuple
`MESSAGE_ID / TASK_ID / STAGE_ID / ATTEMPT / NONCE`:

- `MESSAGE_ID` — a monotonically increasing conversation turn number; permanently
  retired ids can never be re-issued.
- `NONCE` — random per dispatch; makes claim and authorization binding unique even if
  other fields repeat.
- `ATTEMPT` — revision attempts of the same stage are first-class records, not edits.

The Orchestrator binds the authorization record to the **SHA-256 of the exact inbox
snapshot** at dispatch time. The Executor's claim helper rechecks that hash and every
identity field before work starts. Consequences:

- a task file without a matching authorization record is unclaimable (crash between
  publish and authorize fails closed);
- a modified inbox never matches (no executor-side task injection);
- a consumed message id or reused identity is rejected permanently.

## Supervisor control plane (v1.2)

`SUPERVISOR-DISPATCH-ARCHIVE-V1` runs inside the same fence-serialized registration
as authorization. After mechanical validation, Runtime Core writes the exact
`TO_ZCODE.md` bytes and self-describing metadata under
`handoff/supervisor_dispatch_archive/<project>/`; only then does it save the
authorization record. The claim helper verifies the archive identity and the same
SHA-256 bound to the inbox, plus the authorization seal and current Supervisor origin
revision. Same identity/same bytes is idempotent; same project and
`MESSAGE_ID` with a different identity or hash fails closed. Rejected candidates
remain quarantine records and never enter the authorized archive.

There is no archive-less acquisition compatibility mode. An unclaimed legacy
authorization is rejected. An explicitly re-registered dispatch receives a durable
origin receipt, exact archive, and seal before it can be claimed. Only a matching
permanent claim that already existed before upgrade can use the fenced legacy
completion-only recovery path; the historical claim alone is not treated as active
after its completion is committed, consumed, or sealed.

Human Supervisor interventions are immutable inputs under
`handoff/supervisor_interventions/<project>/`. Mutable delivery status is separate
from the original bytes. Submission uses a recoverable transaction journal containing
the exact instruction bytes and intended revision; every fence authority path
reconciles an accepted but partially applied transaction before proceeding. A
monotonically increasing control revision binds every
Supervisor invocation: a pause or intervention arriving after prompt construction
invalidates and quarantines the unseen candidate before it can become claimable.
Interventions that arrive while an already-claimed attempt is running wait for the
next Supervisor decision unless the operator explicitly requests Runtime-level
revocation. Historical targets are correction anchors; dispatch/completion history
is never rewritten and repair work always uses a fresh `MESSAGE_ID`.

Supervisor output is also a transaction boundary. A turn is accepted only when it
appends exactly one decision-history entry, commits a valid lifecycle state, and—when
dispatching—binds the exact candidate identity/hash. Runtime stores a decision receipt
before marking its interventions consumed. Recovery either finishes that validated
decision or requeues the inputs; process success alone has no authority.

Pause is separate from STOP and HUMAN_REVIEW. Idle or unclaimed work reaches
`PAUSED` immediately (an unclaimed dispatch is retired); an already-claimed stage
may finish under safe pause, after which its authoritative completion is consumed
and the next Supervisor decision is deferred until resume. Interrupt mode retires
the current identity so fence checkpoints revoke its Runtime authority; it is not
an OS process kill. Ordering is fence-deterministic: a previously committed completion
wins and remains deliverable, while a retirement committed first prevents a later
completion commit. The pause record carries a recoverable transaction until required
retirement and lifecycle writes are durable, so a crash at the pause commit boundary
does not reverse that ordering.

The read-only task, feedback, intervention, status, and combined-timeline queries
and all mutations have JSON forms in `scripts/supervisor_control.py`. This is the
stable backend boundary intended for a later v1.3 browser console. JSON commands emit
one ASCII-escaped JSON document on stdout so redirected output remains parseable under
Windows PowerShell 5 code pages; wrapper lifecycle text is emitted separately.

## Concurrency and instance isolation

- **Executor claims** — `scripts/executor_claim.py` acquires a permanent claim under
  `handoff/executor_claims/<message-id>-<nonce>.claim/`. Filesystem creation is the
  serialization point: exit 0 = acquired, exit 10 = another live claim, exit 11 =
  already processed, exit 12 = error. Losers exit quietly without doing work.
- **Orchestrator instances** — one orchestrator per runtime root. `acquire_lock()`
  creates `control/.orchestrator.lock` exclusively; an existing lock with a live owner
  pid fails closed, while a lock whose recorded owner is provably dead is reclaimed
  (crash recovery). The launcher pre-check in `START_AGENT_SYSTEM.ps1` is advisory and
  root-scoped; the authoritative guarantee lives in `acquire_lock()`. Independent
  runtime roots (different directories) never share locks, state, stop flags, or claims,
  and can run in parallel on the same machine.

## Post-claim publication fencing (EXECUTOR-FENCE-V1)

Permanent claim acquisition is not lasting write authority. New dispatches bind
project and expiry. The claim winner receives an opaque token (only its hash is
stored), uses `executor_fence.py prepare/check`, and writes into a separate attempt
workspace. Only `executor_fence.py publish` replaces canonical project outputs.
Timeout/supersession persist retirement before recovery; restart replays the timeout
without reauthorizing the attempt. Publication, retirement, dispatch and completion
share an OS-backed mutex, including file IO and hashing. A successful earlier
checkpoint cannot authorize a later stale publication. Published files are copies,
not shared candidate inodes.

See [Stale worker fencing](STALE_WORKER_FENCING.md) for the contract and boundaries:
publication is per file; arbitrary direct writes by the independently running
same-user ZCode process are not intercepted. Hard isolation requires an OS sandbox
or tool write broker. Update the installed prompt and stop legacy workers at rollout.

## Completion lifecycle (COMPLETION-SEAL-V1)

A claim proves at-most-once *execution*; it does not by itself prove at-most-once
*completion*. The authoritative completion fact is therefore manufactured solely by
the Runtime completion helper (`scripts/executor_completion.py`):

1. The Executor finishes stage outputs and builds a **candidate**: a project-local
   staging directory (`<project_root>/completion_staging/.../staging.json`, strict
   schema) carrying the exact stable identity, `PROJECT_ID`, `STATUS=STAGING_READY`,
   and the receipt payload. Staging is never authoritative.
2. The helper mechanically validates the staging against the authorized dispatch
   (identity + inbox hash), the live `WAITING_EXECUTOR` lifecycle, the at-most-once
   claim, and the ledger; then it atomically creates ONE ledger entry under
   `handoff/completion_ledger/` via hard-link compare-and-set. Exit codes:
   `0` COMMITTED, `10` ALREADY_COMMITTED, `11` COMPLETION_SEALED (consumed/sealed
   identities can never commit again), `12` NOT_AUTHORIZED, `13` CLAIM_MISMATCH,
   `14` INVALID_STAGING.
3. The entry binds the stable identity, project, claim identity hash, canonical
   receipt hash (`RECEIPT_SHA256`), the derived brief hash (`BRIEF_SHA256`), the
   staging manifest hash, `committed_at`, and a deterministic `commit_id`.
4. Only after the commit is durable does the Runtime generate the root compatibility
   artifacts (`SUPERVISOR_BRIEF.md` -> `ZCODE_LAST_PROCESSED.txt` -> `ZCODE_DONE.flag`
   last) from the committed entry.

The ledger entry status advances monotonically
`COMPLETION_COMMITTED -> COMPLETION_CONSUMED -> COMPLETION_SEALED` and is never
reversible; restarts never unseal it. The Orchestrator consumes a completion only
when the root DONE wake hint is backed by a matching COMMITTED entry whose identity,
project, and receipt hashes all bind; consumption archives the authoritative
receipt, then marks `CONSUMED`, and the identity is sealed once its single Supervisor
lifecycle decision is past. Everything else is classified and fails closed:

- raw DONE without a ledger record -> `UNKNOWN_RAW_COMPLETION` (quarantine + audit,
  no consumption, no Supervisor turn);
- identity/hash binding mismatch -> `COMPLETION_BINDING_MISMATCH` (fail closed);
- replay of a CONSUMED/SEALED identity -> known stale republish (quarantine + audit,
  lifecycle unchanged).

`ZCODE_LAST_PROCESSED.txt` is likewise Runtime-generated from the committed identity;
executors no longer write it. Crash recovery derives missing runtime pointers,
missing compatibility artifacts, and replay events from the ledger, so a completion
is consumed exactly once and triggers at most one Supervisor lifecycle decision even
across restarts.
Live polling also repairs a missing DONE hint for the current committed completion;
helper death after ledger commit does not require an Orchestrator restart.

## Profiles and Final Verification

Each profile (`profiles/<TYPE>/`) binds:

- `PROFILE.json` — goal parameterization and policy binding;
- `SUPERVISOR_GUIDANCE.md` / `EXECUTOR_GUIDANCE.md` — role-specific operating guidance;
- `FINAL_VERIFICATION_POLICY.json` — a declarative, schema-validated policy listing the
  evidence claims (e.g. fresh run receipts, claim counts, reproduced outputs) required
  before the Orchestrator's mechanical gate allows a terminal `COMPLETE`.

The gate is enforced in the Orchestrator: a Supervisor `COMPLETE` without a passing,
policy-satisfying verification state is blocked and recorded (`gate_block_reason`),
never silently accepted.

## Project isolation

`activate_project_scope()` rebinds every mutable path global (project state, memory,
reports, workspace, evidence) under `projects/<active-id>/` when an active project
pointer exists. All scope roots are validated to stay inside `ROOT\projects` — path
traversal and pointer escapes fail closed. Without a pointer the runtime runs in
legacy single-project mode, preserved for compatibility.

## Human decisions

Terminal/attention states (`HUMAN_REVIEW`, `BLOCKED`, deadline, unrecoverable errors)
are surfaced mechanically by the Orchestrator (console banner + `USER_ATTENTION.json`).
Human decisions re-enter the runtime only through hash-bound receipts prepared and
applied by `scripts/resume_human_review.py`; consumption is ledgered so a receipt can
never be replayed into pending state.
