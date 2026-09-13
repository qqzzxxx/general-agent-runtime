# GOAL-ANCHOR-V1 — Canonical Project Goal Anchoring

GOAL-ANCHOR-V1 keeps long-running autonomous Supervisor ↔ Executor work faithful
to the user's original project intent. The canonical goal becomes a mechanically
bound source of truth: it is bound at bootstrap, re-verified from disk before
every Supervisor decision, and any unexpected change fails the project closed
for human attention instead of being silently adopted.

```
PROJECT_GOAL.md -> Supervisor re-grounding -> Executor stage -> authoritative
completion -> Supervisor re-grounding -> next decision -> ... -> Final
Verification -> COMPLETE
```

## 1. Bootstrap binding

`scripts/start_project.py` (and therefore `START_PROJECT.ps1`) writes the
canonical `PROJECT_GOAL.md` into the new project and persists a binding in
`project_state.json`:

```json
"goal_anchor": {
  "schema_version": 1,
  "goal_path": "PROJECT_GOAL.md",
  "goal_sha256": "<sha256 of the exact PROJECT_GOAL.md bytes>",
  "bound_at": "<timezone-aware UTC timestamp>",
  "provenance": "bootstrap"
}
```

The hash is computed over the real staged file bytes and re-checked before the
project directory is committed, so a freshly created project always verifies.
The `PROJECT_CREATED` output event carries the bound `goal_sha256`.

## 2. Per-turn verification and fail-closed enforcement

Before every Supervisor turn — whatever the reason (`ORCHESTRATOR_START`,
`SUPERVISOR_TURN`, `EXECUTOR_RESULT_READY`, `EXECUTOR_TIMEOUT`,
`FINAL_VERIFICATION_GATE_REQUIRED`, `HUMAN_DECISION_RESUME`) — `invoke_codex`
runs the goal-anchor gate. The gate re-reads the canonical goal from disk,
recomputes its SHA-256, and compares it with `project_state.goal_anchor`. The
gate runs again after a Supervisor turn and before `register_dispatched_task`
authorizes a dispatch (both in-turn and at startup re-authorization), so no new
Executor dispatch or authorization can ever originate from an unverified goal
state. Completions that were already executed and durably committed under a
previously authorized dispatch are still consumed as bookkeeping; the
Supervisor decision about them remains gated.

Failure modes, all failing closed into the bounded human-attention lifecycle
state (`status = HUMAN_REVIEW`, `current_task = null`, `goal_anchor_failure`
record, user notification, no prompt/decision/dispatch):

| Failure | Reason |
|---|---|
| `goal_anchor` absent (pre-binding project) | `GOAL_ANCHOR_MISSING` |
| binding schema/hash/timestamp invalid | `GOAL_ANCHOR_MALFORMED` |
| goal path absolute, non-posix, or escaping the project root | `GOAL_ANCHOR_PATH_ESCAPE` |
| canonical goal file missing | `GOAL_ANCHOR_FILE_MISSING` |
| canonical goal file unreadable | `GOAL_ANCHOR_UNREADABLE` |
| recomputed SHA-256 differs from the binding | `GOAL_ANCHOR_HASH_MISMATCH` |
| binding disagrees with the Runtime-owned copy | `GOAL_ANCHOR_REBIND_REJECTED` |

The Runtime additionally records the bound hash of the active project in its
own state (`control/orchestrator_runtime.json`, key `goal_anchor_binding`) on
first successful verification. A later, self-consistent rewrite of *both* the
goal file and `project_state.goal_anchor` therefore still fails closed — a
silent hash rebind or trust upgrade is mechanically impossible through project
state alone.

## 3. Goal alignment records

The Supervisor prompt injects a verified `GOAL ANCHOR` block (canonical path,
bound SHA-256, verification status) plus a goal-alignment contract: every
committed Supervisor decision — in `last_supervisor_decision` and every new
`decision_history` entry — must include a concise structured `goal_alignment`
object with exactly six non-empty string fields:

- `original_objective` — what the project goal actually requires;
- `unmet_criteria` — which success/completion criteria remain unmet;
- `latest_result` — what the latest Executor result accomplished relative to the goal;
- `next_action_alignment` — how the proposed next action advances the original goal;
- `scope_drift` — whether scope drift is occurring and what is being done about it;
- `method` — continue / revise / redirect / abandon judgment for the current method.

`CONTINUE`, `REVISE`, `REDIRECT`, `CHANGE_METHOD`, `FINAL_VERIFICATION`,
`FINAL_ACCEPTANCE`, `COMPLETE`, `HUMAN_REVIEW`, and `STOP` are all
goal-alignment-checked; recent local Executor success alone can never justify
`FINAL_VERIFICATION`, `FINAL_ACCEPTANCE`, or `COMPLETE`. Decisions committed by
the Runtime on the Human Decision resume path enforce the record mechanically
(`validate_goal_alignment`).

## 4. Legacy projects and migration

Legacy single-project runtimes (no `control/ACTIVE_PROJECT.json`) keep their
exact prior behavior; goal anchoring is defined per isolated project. An
isolated project created before GOAL-ANCHOR-V1 has no binding and therefore
fails closed (`GOAL_ANCHOR_MISSING`) — it is never silently bound. The only
sanctioned recovery is the bounded, exactly-once migration tool:

```
python scripts/migrate_goal_anchor.py
```

It holds the Orchestrator lock, refuses to run when any binding already exists
(exit 3), binds the current canonical goal bytes with `provenance: migration`,
records the Runtime-owned cross-check copy, verifies the result end-to-end, and
leaves the lifecycle state untouched; the project then continues through the
normal `RESUME_HUMAN_REVIEW.ps1` receipt flow. Exit codes: `0` bound, `2`
invalid state, `3` already bound, `4` no active project, `5` lock busy, `6`
internal error.

## 5. What is deliberately unchanged

Claim authorization, exact stable identity binding, completion
staging/commit/consume/seal, Final Verification identity and mechanical gating,
HUMAN_REVIEW/resume receipts, project and runtime-root isolation, recovery and
replay protection, publication order, exit codes, and Rich Console behavior are
unchanged. Console output and prompt text are presentation/contract surfaces
only; goal authority lives exclusively in `project_state.goal_anchor` and the
Runtime-owned cross-check record. The full invariant matrix is enforced by
`scripts/test_goal_anchor.py` plus the existing regression suite.
