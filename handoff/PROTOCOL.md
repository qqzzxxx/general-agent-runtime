# Shared-file Executor protocol v2 (COMPLETION-SEAL-V1)

The active Scheduled Automation inbox is the **root** `TO_ZCODE.md`, not this directory. The active stage-summary outbox is the **root** `SUPERVISOR_BRIEF.md`. `ZCODE_DONE.flag` is the wake hint.

This file is descriptive only. The complete protocol is controlled by:
- `control/CODEX_SUPERVISOR_RUNTIME.md`
- `control/EXECUTOR_TASK_TEMPLATE.md`

Sequence:
1. ZCode Desktop Scheduled Automation checks root `TO_ZCODE.md`.
2. It reads `ZCODE_LAST_PROCESSED.txt`; a non-new MESSAGE_ID is skipped without a new DONE signal.
3. GLM completes the whole stage internally.
4. GLM writes deliverables/evidence.
5. GLM builds a project-local completion staging directory (`completion_staging\`) with one `staging.json` carrying the exact stable identity, `PROJECT_ID`, `STATUS=STAGING_READY`, `CREATED_AT`, and the receipt payload.
6. GLM runs `python scripts/executor_completion.py commit --staging-dir "<staging dir>"`. On `COMPLETION_COMMITTED` / exit 0 the Runtime has durably written the authoritative ledger entry (`handoff/completion_ledger/`) and itself generated root `SUPERVISOR_BRIEF.md`, `ZCODE_LAST_PROCESSED.txt`, and `ZCODE_DONE.flag` (in that order, DONE last). GLM stops immediately.
7. On any completion-helper rejection (10/11/12/13/14) GLM fails closed and publishes nothing.
8. Python consumes a completion only when the root DONE hint is backed by a matching `COMPLETION_COMMITTED` ledger entry (identity, project, and receipt hash all mechanically bound), marks it `COMPLETION_CONSUMED`, and later `COMPLETION_SEALED`. Raw root artifacts without a committed record, rewritten briefs, and replays of consumed identities are quarantined and never drive the lifecycle.

No Computer Use, mouse/keyboard automation, browser control, or ZCode headless CLI is part of this protocol.
