# Shared-file Executor protocol v2 (COMPLETION-SEAL-V1)

The active Scheduled Automation inbox is the **root** `TO_ZCODE.md`, not this directory. The active stage-summary outbox is the **root** `SUPERVISOR_BRIEF.md`. `ZCODE_DONE.flag` is the wake hint.

This file is descriptive only. The complete protocol is controlled by:
- `control/CODEX_SUPERVISOR_RUNTIME.md`
- `control/EXECUTOR_TASK_TEMPLATE.md`

Sequence:
1. ZCode Desktop Scheduled Automation checks root `TO_ZCODE.md`.
2. It reads `ZCODE_LAST_PROCESSED.txt`; a non-new MESSAGE_ID is skipped without a new DONE signal.
3. GLM acquires the exact permanent claim and retains its claim token. Only the winner proceeds. It runs `executor_fence.py prepare` with that identity/token, then `check` on resume and before every work batch or mutation-capable tool/command.
4. GLM writes deliverable/evidence candidates only in the returned attempt workspace. `executor_fence.py publish` rechecks live authorization under the Runtime mutex and copies each candidate to its canonical project path. Direct canonical writes are outside the supported protocol.
5. GLM builds a project-local completion staging directory (`completion_staging\`) with one `staging.json` carrying the exact stable identity, `PROJECT_ID`, `STATUS=STAGING_READY`, `CREATED_AT`, and the receipt payload.
6. GLM runs `python scripts/executor_completion.py commit --staging-dir "<staging dir>" --claim-token "<claim token>"`. Fenced output manifests must match Runtime publication records. On `COMPLETION_COMMITTED` / exit 0 the Runtime has durably written the authoritative ledger entry (`handoff/completion_ledger/`) and itself generated root `SUPERVISOR_BRIEF.md`, `ZCODE_LAST_PROCESSED.txt`, and `ZCODE_DONE.flag` (in that order, DONE last). GLM stops immediately.
7. On any completion-helper rejection (10/11/12/13/14) GLM fails closed and publishes nothing.
8. Python repairs a missing DONE hint from a matching committed ledger during live polling as well as startup. It consumes a completion only when the hint is backed by a matching `COMPLETION_COMMITTED` ledger entry (identity, project, and receipt hash all mechanically bound), marks it `COMPLETION_CONSUMED`, and later `COMPLETION_SEALED`. Repair and consume serialize with completion commit. Raw root artifacts without a committed record, rewritten briefs, and replays of consumed identities are quarantined and never drive the lifecycle.

No Computer Use, mouse/keyboard automation, browser control, or ZCode headless CLI is part of this protocol.

Timeout and supersession permanently retire the old identity without deleting its
claim. Check success is not a reusable write token. Publication and retirement are
serialized; completion rejection alone is insufficient. See
[`EXECUTOR-FENCE-V1`](../docs/STALE_WORKER_FENCING.md) for commands, rollout, crash
semantics, and the current lack of an OS sandbox against direct-write bypass.
