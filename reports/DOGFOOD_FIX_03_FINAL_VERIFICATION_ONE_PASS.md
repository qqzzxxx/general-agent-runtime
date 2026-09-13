# Dogfood Fix 03 — Final Verification One-Pass Contract

## Outcome

New profile-routed Final Verification decisions use a Runtime-prepared task and
Runtime-constructed receipt envelope. One substantive Executor execution can now
proceed through authorization, claim/fence, completion commit, consumption,
mechanical verification, and a Supervisor FINAL_ACCEPTANCE decision under the
same MESSAGE_ID. No protocol-repair Executor stage is needed for valid inputs.

Implementation is local and uncommitted. No push, merge, tag, release, dogfood
deployment, or rerun of the completed dogfood project was performed.

## Unified root cause and historical evidence

The lifecycle duplicated protocol facts across independently model-authored
project state, dispatch, receipt, and current-task bookkeeping. The preparation,
completion, and consumption boundaries did not share a single immutable contract:

1. Dispatch validation required PENDING/REVERIFY but no Runtime preparation step
   established it from the Supervisor's FV decision.
2. Completion commit validated ordinary staging and identity, but did not require
   or construct FV-specific structured metadata. A successful report could become
   an immutable committed receipt without the required FV envelope.
3. Consumption selected an FV-looking `current_task` before consulting the validated
   authorization. A gate-less FV mirror therefore shadowed the authoritative gate.
   Crash replay also depended on the transient current-task view.

The read-only evidence is in `C:\general-agent-runtime-v13-dogfood`.
`evidence/dogfood-fix-03/historical-evidence.json` records the paths and SHA-256 of
17 key files, the actual FV ledger, and the three receipt envelopes.

| Historical boundary | Recorded outcome | Why another turn occurred |
|---|---|---|
| Rejected 700102 candidate, 2026-09-12 20:42:10 UTC | `final_verification.status must be PENDING or REVERIFY at dispatch` | Supervisor had to restore a mechanically required state before authorization. |
| 700102 / `GENERAL_FV_V1_CHECK` | Four substantive claims passed in `reports/final-verification-700102.json`; committed receipt omitted `FINAL_VERIFICATION`. Runtime ledger: empty claims hash, null overall status, mechanical false. | The ordinary commit path sealed an incomplete FV receipt; the missing envelope was requested in 700103. |
| 700103 / `GENERAL_FV_V1_RECEIPT_REPAIR` | Receipt had the four-claim PASS envelope; Runtime ledger still recorded an empty claims hash and mechanical false. | Consumption evaluated the gate-less current-task view, expecting an empty claim/hash set. |
| 700104 / `GENERAL_FV_V1_BINDING_REPAIR` | Runtime ledger recorded the correct `a2e20dfe…61af74` claims hash, overall PASS, mechanical true. | Matching task/gate bookkeeping finally allowed acceptance; this was protocol repair, not a new substantive verification need. |

None of these historical receipts, dispatches, or ledger entries is repaired by
this change. Their sequence remains exactly as recorded.

## Ownership and production path

| Owner | Responsibility |
|---|---|
| Supervisor model | Decide FV is required; choose decision-critical claims, evidence pointers and verification standards; record `decision=FINAL_VERIFICATION`; perform final acceptance after the consumed mechanical result. |
| Runtime preparation | Expand `FINAL_VERIFICATION_REQUEST` before the Supervisor decision receipt commits; establish PENDING; compute CLAIMS_HASH and count; resolve policy ID/version; snapshot and hash policy; preserve the fresh task identity and ordinary control-origin/archive authorization. |
| Executor model | Author `FINAL_VERIFICATION_RESULTS.OVERALL_STATUS`, exact per-claim statuses/checks/evidence/notes, and applicable sandbox/isolation facts. Perform the actual adversarial verification. |
| Runtime completion helper | Validate the claim, fence, lifecycle and exact archived authorization; reject malformed/missing results; construct `FINAL_VERIFICATION` protocol metadata from immutable task inputs; preserve the authored judgments; hash and commit the resulting receipt. |
| Runtime consumer/replay | Verify the dispatch archive and identity, use its gate and pinned policy, evaluate coverage and policy checks, and bind the consumed result. Never let a current-task mirror override the archive. |
| Supervisor + terminal gate | Supervisor selects COMPLETE; Runtime independently checks the exact consumed MESSAGE_ID, receipt hash, claims hash, mechanical PASS and freshness. Runtime does not choose final acceptance. |

The ordinary task carries `FINAL_VERIFICATION_REQUEST` with `CRITICAL_CLAIMS` and
optional `EXECUTION_MODE`. It does not require the model to compute a gate hash or
copy policy metadata. Preparation stores the generated gate and state before the
decision receipt hashes them. State is written first so an interruption before
dispatch publication can replay the same explicit request idempotently. Neither
candidate file is claimable before the existing archive authorization seal.

Gate `CONTRACT_VERSION=1` is explicit. Legacy full-gate tasks and historical
receipts retain their original receipt format; arbitrary old receipts are never
upgraded. Valid new results preserve both the model-authored result object and
the constructed envelope in the committed receipt, under existing integrity seals.

If staging is rejected before commit, the owning worker can correct it under its
still-valid claim. This is not permission to repeat a committed, sealed, expired
or retired identity. Prose-only results, absent claims, or unreported judgments
remain insufficient; Runtime never derives a substantive PASS from a report.

## Safety properties

- Existing at-most-once claim, claim-token, expiry, retirement, publication/fence,
  completion integrity and sealing paths remain in force.
- STOP, HUMAN_REVIEW, malformed input, retired identity and conflicting supplied
  state metadata block preparation/authorization.
- Archive byte hashes, authorization seal, project/control origin and exact task
  identity are verified; the bytes actually parsed are rehashed as well.
- Policy ID/version and immutable policy/claims hashes are checked. A later edit
  to the profile file does not silently change the policy used for this execution.
- Every expected claim appears exactly once. Result statuses and check values are
  preserved. Overall FAIL/INCONCLUSIVE and contradictory per-claim evidence cannot
  be promoted to mechanical PASS, even when the model declares overall PASS.
- Sandbox mode and isolation-incident semantics are unchanged.
- Consumed receipt replay checks ledger integrity before reconstructing an event.
- Ordinary completion receipt content remains unchanged.
- Final acceptance remains Supervisor-only and newer completions still invalidate
  an older FV acceptance.

## Files changed

| File | Change |
|---|---|
| `scripts/final_verification_contract.py` | Shared request preparation, immutable archive resolution, policy binding and receipt construction. |
| `scripts/supervisor_control.py` | Prepare the explicit FV request before hashing and committing the Supervisor decision. |
| `scripts/executor_completion.py` | Construct/validate the fresh FV envelope before ledger commit. |
| `orchestrator.py` | Archive-first consumption/replay, pinned policy evaluation, strict receipt binding, replay integrity and updated Supervisor task instructions. |
| `control/CODEX_SUPERVISOR_RUNTIME.md` | New default Supervisor preparation workflow and legacy distinction. |
| `control/EXECUTOR_TASK_TEMPLATE.md` | Request and model-result contracts. |
| `control/FINAL_VERIFICATION_POLICY.md` | Complete ownership and compatibility contract. |
| `control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md` | Exact structured result instructions for Executor. |
| `scripts/test_fv_one_pass.py` | Fourteen production-path, failure, tamper and compatibility regressions. |
| `evidence/dogfood-fix-03/` | Historical evidence hashes and test logs. |
| This report | Root cause, implementation, ownership and validation record. |

## Regression design and validation

All new lifecycle tests use disposable Runtime roots with an isolated active
project and copied profile policies. They call the production Supervisor decision
transaction, registration/archive authorization, Executor claim/fence helper,
completion staging/commit, ledger consumption/replay and final acceptance gate.
Model outputs are deterministic fixtures; no external model execution is claimed.

The old-chain regression substitutes only the former receipt and gate-selection
boundaries while retaining the real production claim/fence/ledger path. It records
three successive rounds with mechanical outcomes `[false, false, true]`: missing
envelope, gate-less current task, then repaired binding. A separate negative check
reproduces the old PENDING-state dispatch rejection.

The new-path regression begins from NOT_STARTED, submits a correct explicit FV
request, acquires one claim, commits one all-PASS result, consumes it with a
gate-less FV-marked current task, replays it after a simulated decision interruption,
and commits Supervisor FINAL_ACCEPTANCE/COMPLETE. It asserts exactly one archived
Executor dispatch, one completion identity, the same MESSAGE_ID, and intact seals.

Additional regressions cover FAIL/INCONCLUSIVE preservation, negative per-claim
checks despite overall PASS, missing/malformed/duplicate claims, forged metadata,
stale nonce, altered archive/gate/policy/hash data, missing receipt bindings,
corrupt consumed receipt replay, profile changes, preparation retry, STOP,
HUMAN_REVIEW, retirement, and unchanged ordinary completion receipts.

Validation results:

| Command | Result |
|---|---|
| `python -m unittest discover -s scripts -p 'test_*.py'` | 1299 tests, 0 failures/errors, 3 skipped; 382.116 seconds. |
| `python -m unittest discover -s scripts -p 'test_fv*.py'` | 59 tests passed; rerun after the final malformed-state rejection check. |
| `python -m unittest discover -s scripts -p 'test_fv_one_pass.py'` | 14 new regressions passed (also included in the FV/full suites). |
| `python -m unittest discover -s scripts -p 'test_completion_seal.py'` | 21 tests passed. |
| `git diff --check` | Passed. |
| Historical SHA-256 recheck | All 17 recorded dogfood files unchanged. |

Logs are retained in `evidence/dogfood-fix-03/`. The three skipped checks are the
existing real-active-project-pointer tests in `test_g5a5_1_hotfix.py`; this clean
development checkout has no active project pointer. The full run also emits
Windows socket/subprocess resource-cleanup warnings; they are visible in the log
and did not fail assertions. This fix does not modify those unrelated tests.

The existing Windows launcher tests clean up the development console process and
instance metadata in this checkout. The development console is restarted on its
original loopback port 60649 after testing; its generated `web_console_data/instance.json`
therefore changes. No dogfood Runtime process or project is restarted.
