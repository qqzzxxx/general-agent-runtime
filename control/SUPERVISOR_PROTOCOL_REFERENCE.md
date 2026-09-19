# Supervisor protocol reference — retained FV and Human Decision contracts

Context V2 and outcome ownership are defined by CODEX_SUPERVISOR_RUNTIME.md.
This reference supplies only the existing special output/lifecycle contracts.
Read the applicable section when choosing Final Verification or returning a
full-wire task in the read-only Human Decision transaction. Ordinary tasks use
ordinary_task_proposal; Runtime constructs their physical dispatch.

For a full-wire task, copy the canonical EXECUTOR_PROTOCOL array from
EXECUTOR_TASK_TEMPLATE.md unchanged. The helper instructions below describe the
retained wire compatibility boundary. Executor V2 receives its outcome view and
uses the existing entry/work/finish contract; do not ask it to reconstruct receipt,
hash or staging machinery in the task's semantic fields. Runtime constructs new FV
gate metadata from FINAL_VERIFICATION_REQUEST and binds the execution mode.

## Task dispatch

Root `TO_ZCODE.md` is the only Executor inbox. Publish atomically (`TO_ZCODE.md.tmp` -> `TO_ZCODE.md`). Header first:

Publication is only a candidate dispatch. The Python Orchestrator must mechanically
validate and register the exact inbox identity/hash before `executor_claim.py` will
allow execution. A fresh visible file is not authorization.

Runtime Core must also archive the exact validated candidate bytes under
`handoff/supervisor_dispatch_archive/` before authorization. Never create, edit, or
repair that Runtime-owned archive yourself. A candidate is eligible only for the
control revision and decision receipt that produced its exact identity and hash;
restart cannot upgrade an older candidate into authority.

`MESSAGE_ID: <n>`
`TASK_ID: <logical task>`
`STAGE_ID: <attempt/stage>`

Then exactly one fenced JSON object with at least:

- `PROTOCOL_VERSION: 2`
- `CLAIM_PROTOCOL_VERSION: 1` (mandatory for tasks dispatched after the claim-hotfix activation point)
- fresh monotonic `MESSAGE_ID` from `project_state.next_message_id`
- `TASK_ID`, `STAGE_ID`, `ATTEMPT`, fresh `NONCE`, timezone-aware `ISSUED_AT`
- `EXECUTOR_MODEL_FAMILY: GLM-5.3`
- `OBJECTIVE`, `INPUTS`, `OUTPUTS`, `ACCEPTANCE_CRITERIA`
- `MAX_TIME` as integer seconds, `MAX_RETRIES`, `SCHEDULER_GRACE_SECONDS` as integer seconds (use at least 3600 for Scheduled Automation)
- `SUPERVISOR_REVIEW_EFFORT`: always `high`. The Orchestrator independently pins every Codex Supervisor invocation to GPT-5.6 Sol + High; a task field can never downgrade it.
- `FORBIDDEN_ACTIONS`, `STOP_CONDITIONS`, `EXECUTOR_PROTOCOL`
  - `EXECUTOR_PROTOCOL` TYPE = array<string> (a flat JSON array of instruction strings).
    Objects, nested structures (e.g. CLAIM/EXECUTION/COMPLETION groupings), and
    non-string entries are REJECTED by the Orchestrator. At least one entry must
    contain the claim command `executor_claim.py acquire`.

`current_task` must use the same uppercase identity keys and persist `ISSUED_AT`, `MAX_TIME`, `SCHEDULER_GRACE_SECONDS`, and `SUPERVISOR_REVIEW_EFFORT` when set.

Executor protocol must require GLM to use an **atomic at-most-once claim before doing stage work**:

1. After reading the task identity, run:
   `python scripts/executor_claim.py acquire --message-id <MESSAGE_ID> --task-id "<TASK_ID>" --stage-id "<STAGE_ID>" --attempt <ATTEMPT> --nonce "<NONCE>"`
2. Exit code `0` / `CLAIM_ACQUIRED`: this instance owns the attempt and may proceed.
3. Exit code `10` / `CLAIM_EXISTS` or `11` / `ALREADY_PROCESSED`: stop this Scheduled Automation run immediately and silently. Do not browse/research, touch evidence/deliverables, build completion staging, call the completion helper, or create any root completion artifact.
4. Any other claim-helper failure: fail closed and stop without touching stage outputs; let the Python watchdog/Supervisor decide recovery.
5. Never delete a claim directory. If a claimed attempt crashes, it is not rerun under the same MESSAGE_ID/NONCE; a Supervisor retry must use a fresh MESSAGE_ID and NONCE.

After claim acquisition, GLM must retain the returned claim token and run
`executor_fence.py prepare` with the exact identity and token. All stage mutations
and subprocess outputs belong in that attempt workspace; canonical inputs are
read-only. Require `executor_fence.py check` on resume and before each work batch,
mutation-capable command, publication and completion. A successful checkpoint is
not a reusable write grant. Require `executor_fence.py publish` for canonical
workspace/evidence/reports files, using the same identity/token, relative path and
candidate SHA-256. No direct canonical output or project memory writes are allowed;
incorporate proposed memory changes from the receipt during Supervisor review.

Timeout/supersession retires old identities before recovery. Never continue a
retired identity or transfer a claim/token to a retry. New attempts need fresh IDs
and nonces. Follow [EXECUTOR-FENCE-V1](../docs/STALE_WORKER_FENCING.md), including
supported paths, file-size limits and the lack of an OS sandbox against bypass.
Then finish through Runtime-owned completion commit:

1. Build a completion staging directory under the active project's `completion_staging\` containing one `staging.json` (schema `COMPLETION_STAGING_SCHEMA_VERSION: 1`) with the exact stable identity, `PROJECT_ID`, `STATUS: STAGING_READY`, a timezone-aware `CREATED_AT`, and the full receipt payload (`RECEIPT`, whose identity must match the staging identity exactly).
2. Run: `python scripts/executor_completion.py commit --staging-dir "<staging dir>" --claim-token "<claim token>"`.
3. Exit code `0` / `COMPLETION_COMMITTED`: the Runtime has durably committed the authoritative completion and generated the root `SUPERVISOR_BRIEF.md`, `ZCODE_LAST_PROCESSED.txt`, and `ZCODE_DONE.flag` itself. Stop immediately — never edit, repair, or republish those root files.
4. Exit code `10` / `ALREADY_COMMITTED`, `11` / `COMPLETION_SEALED`, `12` / `COMPLETION_NOT_AUTHORIZED`, `13` / `COMPLETION_CLAIM_MISMATCH`, or `14` / `INVALID_COMPLETION_STAGING`: fail closed, stop, and publish nothing. A consumed/sealed identity can never commit again; a Supervisor retry requires a fresh MESSAGE_ID and NONCE.

Never choose the next stage; never bypass auth/CAPTCHA/access controls.

## HUMAN_REVIEW resume

`HUMAN_REVIEW` never auto-resumes. The only supported recovery path is the
Runtime-owned `RESUME_HUMAN_REVIEW.ps1` / `scripts/resume_human_review.py`
two-step receipt flow:

1. `prepare` validates the active isolated project and creates a structured Human
   Decision receipt bound to the exact SHA-256 of the current `project_state.json`.
   Preparation does not change project state.
2. The human reviews that receipt and explicitly submits it through `apply`.
3. `apply` holds the ordinary Orchestrator lock; validates active-project identity,
   exact `HUMAN_REVIEW` state, `current_task=null`, stale-state/flag hashes, the
   consumed Executor/archive/claim ledger, absence of STOP/DONE/incomplete attempts,
   and duplicate state; then archives the receipt immutably under the active project.
4. The only lifecycle commit is `HUMAN_REVIEW -> SUPERVISOR_TURN`. The tool preserves
   `current_task`, `next_message_id`, `last_supervisor_decision`, Final Verification,
   claim authorization, and existing wire files. It does not invoke Codex or ZCode.
5. On the next normal Orchestrator start, the archived receipt is revalidated and
   injected verbatim as `VERIFIED HUMAN DECISION RECEIPT`. This special Supervisor
   turn runs read-only and returns one structured result; reading/prompt construction/
   invocation never consumes the receipt. The Runtime validates the decision, lifecycle,
   Final Verification policy, and any selected task before committing anything.
6. The single atomic `project_state.json` replacement is the decision/consumption commit
   point. It stores the resulting Supervisor decision and lifecycle state together with
   `human_review_resume.status=CONSUMED`, a canonical decision hash/history identity, and
   an append-only `human_decision_consumption_ledger` entry bound to receipt ID/hash and
   project ID. Before that replace the receipt remains pending and replayable; after it,
   it is consumed and can never be injected as new authorization.
7. For `CONTINUE`/`REDIRECT`/`CHANGE_METHOD`/`REVISE`, the Runtime mechanically publishes
   the exact Supervisor-returned task before the state commit, but it remains unclaimable
   until the existing post-commit authorization record is saved. A crash after the state
   commit is recovered by the existing `WAITING_EXECUTOR` startup path without replaying
   the Human Decision. A later HUMAN_REVIEW may accept a distinct new receipt; prior
   consumed ledger entries remain historical and non-authorizing.

A receipt is not an approval token and is not an Executor dispatch. Receipt/archive
hash mismatch, a stale project-state snapshot, duplicate application, project-scope
mismatch, a busy lock, an invalid/malformed Supervisor result, a duplicate consumption,
or any unfinished Executor evidence fails closed. Pending and consumed lifecycle records
are revalidated mechanically by preflight.

## Final Verification Gate (generic)

If `final_verification.required=true`, `COMPLETE` is mechanically forbidden until the
active Profile's bound Final Verification policy has passed and FINAL_ACCEPTANCE is
complete. Use the active Profile policy (its bound `policy_id`/`policy_version`):
select 3–8 decision-critical claims within the Profile taxonomy, dispatch ONE bounded
`TASK_KIND=FINAL_VERIFICATION` task using the one-pass request below, and on FAIL/INCONCLUSIVE
apply only a narrow REVISE before reverifying. Only after a mechanically passing
verification receipt and one FINAL_ACCEPTANCE turn may you set
`final_verification.status=PASS` and `COMPLETE`.

For new decisions, record `decision=FINAL_VERIFICATION` in both decision records
and publish `FINAL_VERIFICATION_REQUEST` with `CRITICAL_CLAIMS`.
Runtime binds execution permissions from the FV policy; omit `EXECUTION_MODE`
and `FINAL_VERIFICATION_GATE`. Initial FV state may be
`NOT_STARTED`; after substantive revision use `REVERIFY`. Runtime prepares PENDING,
the canonical hash/count and immutable policy snapshot before the decision commit.
Any state claims/hash/policy already supplied must agree exactly with the request.
The Executor supplies `FINAL_VERIFICATION_RESULTS`; Runtime owns the receipt's
protocol metadata, never its judgments. See `control/FINAL_VERIFICATION_POLICY.md`.

In the resulting prepared task (or the retained legacy full-gate contract), these
fields belong inside `FINAL_VERIFICATION_GATE` (never at task top level):

```json
{
  "TASK_KIND": "FINAL_VERIFICATION",
  "FINAL_VERIFICATION_GATE": {
    "POLICY_ID": "<active Profile final_verification_policy_id>",
    "POLICY_VERSION": 1,
    "CLAIMS_HASH": "<canonical SHA256 of CRITICAL_CLAIMS>",
    "CLAIM_COUNT": 3,
    "CRITICAL_CLAIMS": []
  }
}
```

`CRITICAL_CLAIMS` element schema is mechanically enforced (reject + quarantine + one
bounded repair turn). Every element must be a JSON object with EXACTLY these six
lowercase keys — uppercase variants (`CLAIM_ID`, `TYPE`, `FALSIFIED_IF`, ...) are
aliases, NOT accepted, and are rejected as missing fields:

```json
{
  "claim_id": "C1",
  "claim": "Decision-critical factual claim.",
  "claim_type": "IP",
  "decision_impact": "HIGH",
  "evidence_pointers": ["evidence/example.txt"],
  "verification_standard": "Adversarially verify against the cited evidence."
}
```

`CLAIMS_HASH` is the canonical SHA-256 of the exact claim list: hash the JSON produced
by `json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(',', ':'))`.
Runtime stores the same claim list and hash in `project_state.final_verification`
for new requests. Only legacy full-gate dispatches require manual duplication.

`POLICY_ID` is mandatory for profile-routed projects. Legacy V1.5 commercial tasks
may omit only `POLICY_ID`; their remaining nested gate shape is unchanged.

Legacy note (V1.5 commercial compatibility): older commercial runs follow the same
lifecycle under the BUSINESS_RESEARCH policy; their historical predicate and wording
are retained for compatibility only.

When the commercial goal is otherwise satisfied:

1. Do not set `COMPLETE` yet.
2. Identify exactly 3–8 decision-critical claims whose being wrong could materially change the
   recommendation, spending decision, risk posture, or next action.
3. Prefer the highest-consequence IP, economics, price, demand/pain, regulatory/safety, and
   winner-vs-rejected claims.
4. Record a `FINAL_VERIFICATION` decision and choose the exact claims.
5. Publish one bounded `TASK_KIND=FINAL_VERIFICATION` task with
   `FINAL_VERIFICATION_REQUEST.CRITICAL_CLAIMS` (Runtime binds execution mode), using
   a fresh MESSAGE_ID/NONCE and the ordinary claim protocol. Runtime prepares and
   validates PENDING, claims hash/count and policy snapshot before authorization.
6. The verifier must be adversarial and should try to falsify first. Do not reopen broad research.
7. On FAIL/INCONCLUSIVE, use the smallest narrow REVISE necessary, then reverify.
8. On PASS, perform one `FINAL_ACCEPTANCE` turn. You may inspect a few precise raw evidence
   pointers for consequential claims, but do not redo GLM's bulk work.
9. Only after final acceptance may you set `final_verification.status=PASS`, record the accepted
   verification MESSAGE_ID and `brief_sha256`, update the final report, clear `current_task`, and
   set `COMPLETE`.

FV identity binding (mechanical): the authoritative Final Verification identity is the
MESSAGE_ID of the dispatch the Runtime itself validated and authorized — the Runtime records
it and the exact `FINAL_VERIFICATION_GATE` in `control/orchestrator_runtime.json`
(`authorized_dispatch`) at authorization time. Consumption and crash replay verify
the immutable dispatch archive and use its gate; a current_task mirror cannot override
it. Runtime binds `last_final_verification_message_id`
(+ receipt sha256, claims hash, overall status) when it consumes that receipt. Your
`final_verification` acceptance must reference exactly that consumed MESSAGE_ID and
`verification_receipt_sha256`; the COMPLETE gate compares those against the Runtime's own
record and fails closed on any mismatch. `current_task` is not required to mirror
`TASK_KIND`/`FINAL_VERIFICATION_GATE`.

Anti-repeat guard: if the Runtime's receipt ledger already holds a mechanically PASS receipt
for a claims hash and that receipt is still the freshest consumed Executor receipt,
re-dispatching an identical `FINAL_VERIFICATION` task (same claims hash) is mechanically
rejected at dispatch validation. In that situation repair `final_verification` to reference
the authoritative MESSAGE_ID + `verification_receipt_sha256` named in the gate event and
re-attempt FINAL_ACCEPTANCE, or set `HUMAN_REVIEW`. Do not re-execute the same verification.

The final report should include a compact `Decision-Critical Claims` section showing what passed,
what remains conditional/unknown, and what real-world gate comes next.

Python independently rejects premature COMPLETE and invalidates an old PASS if any newer Executor
receipt exists. See `control/FINAL_VERIFICATION_POLICY.md`.
