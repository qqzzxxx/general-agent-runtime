# Executor task wire template (Supervisor fills values)

Publish root `TO_ZCODE.md` with a plain compatibility header followed by exactly one fenced JSON object. Keep the task stage-sized, not micro-sized.

```text
MESSAGE_ID: 700001
TASK_ID: INFRA-A
STAGE_ID: INFRA-A-NORMAL
```

Then append this authoritative payload.

Wire contract for `EXECUTOR_PROTOCOL`: TYPE = array<string> — a FLAT JSON array of
instruction strings. Objects and nested structures (e.g. CLAIM/EXECUTION/COMPLETION
groupings) are REJECTED by the Orchestrator. At least one entry must contain the
claim command (`executor_claim.py acquire`).

The published file is a candidate only. Execution is authorized only after the Python
Orchestrator has validated and registered the exact task identity and inbox SHA-256.
`executor_claim.py` fails closed if that registration is missing or the inbox changed.

```json
{
  "PROTOCOL_VERSION": 2,
  "CLAIM_PROTOCOL_VERSION": 1,
  "MESSAGE_ID": 700001,
  "TASK_ID": "INFRA-A",
  "STAGE_ID": "INFRA-A-NORMAL",
  "ATTEMPT": 1,
  "NONCE": "fresh-random-nonce",
  "ISSUED_AT": "timezone-aware ISO-8601",
  "EXECUTOR_MODEL_FAMILY": "GLM-5.3",
  "OBJECTIVE": "One complete stage objective.",
  "INPUTS": [],
  "OUTPUTS": [],
  "ACCEPTANCE_CRITERIA": [],
  "MAX_TIME": 900,
  "MAX_RETRIES": 2,
  "SCHEDULER_GRACE_SECONDS": 3600,
  "SUPERVISOR_REVIEW_EFFORT": "high",
  "FORBIDDEN_ACTIONS": [
    "Do not bypass authentication, CAPTCHA, access control, or anti-automation restrictions.",
    "Do not invent missing data or credentials.",
    "Do not choose the next stage."
  ],
  "STOP_CONDITIONS": [],
  "EXECUTOR_PROTOCOL": [
    "Work only inside the active Runtime Root and the active Project Root supplied by the Supervisor.",
    "Before any stage work, run: python scripts/executor_claim.py acquire --message-id <MESSAGE_ID> --task-id <TASK_ID> --stage-id <STAGE_ID> --attempt <ATTEMPT> --nonce <NONCE>.",
    "Proceed only on CLAIM_ACQUIRED / exit code 0. On CLAIM_EXISTS / exit code 10 or ALREADY_PROCESSED / exit code 11, stop this run immediately and silently: do not touch deliverables, build completion staging, call the completion helper, or create any root completion artifact.",
    "On any other claim-helper failure, fail closed and stop without touching stage outputs; watchdog/Supervisor owns recovery.",
    "Never delete the claim. A retry must use a fresh MESSAGE_ID and NONCE.",
    "Complete the whole stage internally before returning to the Supervisor.",
    "Write deliverables/evidence first.",
    "Build a completion staging directory under the active Project Root's completion_staging\\ folder: one staging.json (COMPLETION_STAGING_SCHEMA_VERSION 1) containing the exact MESSAGE_ID, TASK_ID, STAGE_ID, ATTEMPT, NONCE, PROJECT_ID, STATUS=STAGING_READY, a timezone-aware CREATED_AT, and the full receipt payload as RECEIPT (identity fields must match the staging identity exactly).",
    "Then run: python scripts/executor_completion.py commit --staging-dir \"<staging dir>\".",
    "Proceed only on COMPLETION_COMMITTED / exit code 0: the Runtime then generates SUPERVISOR_BRIEF.md, ZCODE_LAST_PROCESSED.txt, and ZCODE_DONE.flag itself. Stop immediately; never create, edit, repair, or republish those root files.",
    "On completion-helper exit codes 10/11/12/13/14 (ALREADY_COMMITTED / COMPLETION_SEALED / COMPLETION_NOT_AUTHORIZED / COMPLETION_CLAIM_MISMATCH / INVALID_COMPLETION_STAGING), fail closed and stop without publishing anything.",
    "Stop after the stage; never create the next TO_ZCODE task."
  ]
}
```

Production note: `MAX_TIME` and `SCHEDULER_GRACE_SECONDS` are integer seconds. Do not write strings such as `90 minutes`.

Concurrency note: the permanent atomic claim provides at-most-once active execution per MESSAGE_ID/NONCE even when Scheduled Automation wakeups overlap. The Runtime completion commit provides at-most-once authoritative completion per MESSAGE_ID: a second commit is refused (exit 10) and a consumed/sealed identity can never commit again (exit 11). Root completion artifacts are Runtime-generated; the Orchestrator never consumes a completion that lacks a matching committed ledger record, so bypassing the helper cannot manufacture a completion.

Supervisor model note: all Codex turns are fixed to GPT-5.6 Sol + High by the Orchestrator; `SUPERVISOR_REVIEW_EFFORT` should remain `high` for wire/state clarity.


## Generic profile-bound final-verification specialization

Ordinary tasks remain unchanged. For a `TASK_KIND: FINAL_VERIFICATION` stage, include:

```json
{
  "TASK_KIND": "FINAL_VERIFICATION",
  "FINAL_VERIFICATION_GATE": {
    "POLICY_ID": "<active Profile final_verification_policy_id>",
    "POLICY_VERSION": 1,
    "CLAIMS_HASH": "<canonical SHA256>",
    "CLAIM_COUNT": 3,
    "CRITICAL_CLAIMS": [
      {
        "claim_id": "C1",
        "claim": "Decision-critical factual claim",
        "claim_type": "IP",
        "decision_impact": "HIGH",
        "evidence_pointers": ["..."],
        "verification_standard": "Verify exact identifier against authoritative metadata."
      }
    ]
  }
}
```

All five gate fields shown above are nested under `FINAL_VERIFICATION_GATE`; do not
place `POLICY_ID`, `POLICY_VERSION`, `CLAIMS_HASH`, or `CRITICAL_CLAIMS` at task top
level. `POLICY_ID` is mandatory for profile-routed projects. Only legacy V1.5
commercial compatibility tasks may omit `POLICY_ID`.

Claim element keys are mechanically enforced and must be written EXACTLY as shown:
`claim_id`, `claim`, `claim_type`, `decision_impact`, `evidence_pointers`,
`verification_standard` — six lowercase keys, no uppercase variants or aliases, no
missing keys. A claim written with uppercase keys (`CLAIM_ID`, `TYPE`,
`FALSIFIED_IF`, ...) is rejected as schema-invalid: the dispatch is quarantined, and
the Supervisor gets one bounded repair turn.

The Executor must add the `FINAL_VERIFICATION` receipt object defined in
`control/FINAL_VERIFICATION_POLICY.md`.

This task is bounded adversarial verification. Do not restart broad market research.
