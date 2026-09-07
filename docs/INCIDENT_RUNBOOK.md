# General Agent Runtime — Incident Runbook

> Safe recovery procedures for common abnormal states.
>
> Rule zero: **do not "repair" the Runtime by deleting claims, rewriting ledgers, or editing lifecycle JSON until you have identified the exact state and a documented procedure says to do so.**

## 0. Triage before any repair

Record:

```text
Runtime Root
Active Project ID
project_state.status
current MESSAGE_ID / TASK_ID / STAGE_ID / ATTEMPT / NONCE
whether an Executor claim exists
whether completion is COMMITTED / CONSUMED / SEALED
whether Orchestrator is running
whether ZCode Automation is enabled / paused
whether current ZCode run is active
latest user feedback timestamp
```

Useful first checks:

```powershell
python .\scripts\preflight.py
Get-Content .\control\ACTIVE_PROJECT.json -Raw
Get-Content .\TO_ZCODE.md -Raw
```

Do not change anything until the lifecycle is understood.

---

# INC-001 — Goal Anchor hash mismatch

## Symptoms

Console reports a Goal Anchor failure / hash mismatch and enters `HUMAN_REVIEW` or refuses dispatch.

## Meaning

The canonical `PROJECT_GOAL.md` no longer matches the SHA-256 bound when the project was created.

## Common cause

The user or a tool edited the canonical Goal after project bootstrap.

## DO NOT

- do not update the stored hash manually;
- do not edit `project_state.goal_anchor`;
- do not silently rebind the Goal;
- do not force an Executor task through.

## Safe procedure

If the edit was accidental and the exact original Goal bytes are available:

1. restore the canonical Goal exactly;
2. run preflight;
3. follow the normal Human Review recovery path if the Runtime is already in Human Review.

If the edit was intentional and materially changes the project objective:

> create a new project with a new Goal rather than silently mutating the existing project's constitution.

---

# INC-002 — Executor task already claimed, user intentionally interrupts it

## Symptoms

- current project status: `WAITING_EXECUTOR`;
- exact claim exists for current MESSAGE_ID/NONCE;
- user stops the live ZCode Executor run before it commits completion;
- no completion exists yet.

## Meaning

The task has permanently consumed its execution identity, but it has no authoritative completion.

The same identity must not be rerun.

## DO NOT

- never delete the claim;
- never edit the claim metadata;
- never rerun the same MESSAGE_ID/NONCE;
- never hand-edit `project_state.json` to skip the task.

## Safe V1.0.x steering workaround

This is a **maintainer-level workaround**, not yet a first-class one-click Runtime feature.

Use only when:

- the exact current task is still the live `WAITING_EXECUTOR` task;
- the Executor run has truly been stopped;
- no authoritative completion for the current MESSAGE_ID already exists;
- the latest feedback has already been recorded in `RESEARCH_STATE.md`.

Create a project-local completion staging candidate whose exact task identity matches the current dispatch and whose receipt status clearly records a user abort, for example:

```text
STATUS = FAILED
ERROR_CODE = ABORTED_BY_USER
REASON = User intentionally interrupted this attempt; latest user feedback requires Supervisor replan.
```

Then commit it only through:

```powershell
python .\scripts\executor_completion.py commit --staging-dir "<staging-dir>"
```

Proceed only if the helper returns:

```text
COMPLETION_COMMITTED
```

The helper must mechanically validate:

- current authorization;
- exact stable identity;
- active project;
- claim;
- live `WAITING_EXECUTOR` state;
- completion ledger uniqueness.

If the helper rejects the staging, **stop**. Do not force or hand-edit state.

After commit:

```text
Orchestrator consume
-> Supervisor sees latest RESEARCH_STATE.md
-> Supervisor REVISE / REDIRECT
-> fresh MESSAGE_ID + fresh NONCE
```

### Operator-tested PowerShell pattern

The following pattern is intended for the current v1.0.x schema. Inspect the current helper/schema before using it on a later version.

```powershell
$root = (Get-Location).Path
$wire = Get-Content ".\TO_ZCODE.md" -Raw
$m = [regex]::Match(
  $wire,
  '```json\s*(.*?)\s*```',
  [System.Text.RegularExpressions.RegexOptions]::Singleline
)
if (-not $m.Success) { throw "Cannot parse TO_ZCODE.md" }

$task = $m.Groups[1].Value | ConvertFrom-Json
$active = Get-Content ".\control\ACTIVE_PROJECT.json" -Raw | ConvertFrom-Json
$project = Join-Path $root $active.project_root

$staging = Join-Path $project (
  "completion_staging\user-abort-" +
  $task.MESSAGE_ID + "-" + [guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Force -Path $staging | Out-Null

$receipt = [ordered]@{
  MESSAGE_ID = [int]$task.MESSAGE_ID
  TASK_ID    = [string]$task.TASK_ID
  STAGE_ID   = [string]$task.STAGE_ID
  ATTEMPT    = [int]$task.ATTEMPT
  NONCE      = [string]$task.NONCE
  STATUS     = "FAILED"
  ERROR_CODE = "ABORTED_BY_USER"
  REASON     = "User intentionally interrupted this Executor attempt. Re-read latest RESEARCH_STATE.md and replan."
}

$payload = [ordered]@{
  COMPLETION_STAGING_SCHEMA_VERSION = 1
  MESSAGE_ID = [int]$task.MESSAGE_ID
  TASK_ID    = [string]$task.TASK_ID
  STAGE_ID   = [string]$task.STAGE_ID
  ATTEMPT    = [int]$task.ATTEMPT
  NONCE      = [string]$task.NONCE
  PROJECT_ID = [string]$active.project_id
  STATUS     = "STAGING_READY"
  CREATED_AT = [DateTimeOffset]::UtcNow.ToString("o")
  RECEIPT    = $receipt
}

$payload | ConvertTo-Json -Depth 8 |
  Set-Content (Join-Path $staging "staging.json") -Encoding UTF8

python .\scripts\executor_completion.py commit --staging-dir "$staging"
```

Expected outcome:

```text
COMPLETION_COMMITTED
```

Then let the Orchestrator consume it and invoke the Supervisor.

---

# INC-003 — User adds new feedback while an old Executor task is already running

## Symptoms

The user updates `RESEARCH_STATE.md`, but the currently running Executor task was dispatched before that update.

## Meaning

The current Executor task does not automatically inherit the new feedback.

The **next Supervisor turn** will see the latest project memory.

## Safe procedure

### If the current task is almost done

Let it complete.

The next Supervisor turn should see:

```text
Executor completion = what just happened
latest RESEARCH_STATE = what the user now requires
```

The Supervisor should use the newer user feedback as the current quality/method baseline while treating the Executor completion as historical fact.

### If continuing would cause major waste/systemic defect

Use INC-002.

## DO NOT

Do not edit `TO_ZCODE.md` underneath the running task.

---

# INC-004 — A new Supervisor task is published before the user can inspect the new plan

## Symptoms

Executor completes, Orchestrator immediately invokes Supervisor, and a fresh task is published.

## Meaning

This is normal autonomous behavior.

## Safe procedure

If you want a review checkpoint:

1. pause the ZCode Automation so the fresh task is not executed yet;
2. inspect the Supervisor decision / task payload;
3. if correct, re-enable Automation;
4. if wrong, use safe steering/recovery rather than editing the task wire.

---

# INC-005 — Scheduled Automation appears not to fire

## Symptoms

- Orchestrator is `WAITING_EXECUTOR`;
- no visible new Scheduled Automation run appears.

## Diagnose in this order

1. Is the Automation enabled?
2. Is its Workspace exactly the Runtime Root?
3. Is a prior run still `in progress`?
4. Does ZCode serialize/skip overlapping runs for the same automation?
5. Is there actually a fresh task to claim?
6. Was the task already claimed by an earlier run?
7. Does the history show a very short successful wake that may have exited on `CLAIM_EXISTS`?

## Important distinction

A prompt cannot prevent a Scheduled Automation run from being created **before the prompt starts executing**.

If no Run record exists at the scheduled time, the likely cause is scheduler/client behavior, not `executor_claim.py`.

If a short Run exists, it may have started and exited because claim authorization was unavailable/already owned.

## Safe response

Do not weaken claim semantics to make the UI "look active".

---

# INC-006 — `CLAIM_EXISTS`

## Symptoms

Claim helper returns exit code 10.

## Meaning

That exact MESSAGE_ID/NONCE has already been claimed.

This is often expected during overlapping Scheduled Automation wakes.

## Safe procedure

Exit this wake quietly.

## DO NOT

Never delete the claim.

---

# INC-007 — `ALREADY_PROCESSED`

## Symptoms

Claim helper returns exit code 11.

## Meaning

The Runtime already considers the task identity processed/retired.

## Safe procedure

Exit this wake quietly.

Investigate only if the user expected a genuinely new task.

---

# INC-008 — Executor timeout

## Symptoms

Project remains `WAITING_EXECUTOR` beyond:

```text
MAX_TIME + scheduler grace
```

and Runtime watchdog invokes the timeout path.

## Meaning

The current attempt did not produce a valid completion within its allowed execution window.

## Safe procedure

Let the Orchestrator/Supervisor own recovery.

A Supervisor retry must use a fresh MESSAGE_ID and NONCE.

## DO NOT

- do not delete the claim;
- do not rerun the same identity;
- do not forge a completion to avoid timeout unless using a deliberate, truthful operator abort flow.

---

# INC-009 — Completion was committed but Supervisor did not progress

## Symptoms

`COMPLETION_COMMITTED` exists, but no new Supervisor turn appears.

## Diagnose

Check:

- is Orchestrator still running?
- ledger entry status;
- whether it is COMMITTED / CONSUMED / SEALED;
- Runtime logs;
- whether a compatibility wake artifact was lost after a crash.

## Safe procedure

Restart the Orchestrator normally if it is not running.

The Runtime is designed to reconcile authoritative ledger state across restarts.

## DO NOT

Do not manually recreate or rewrite:

```text
SUPERVISOR_BRIEF.md
ZCODE_LAST_PROCESSED.txt
ZCODE_DONE.flag
```

Those are Runtime-owned derived artifacts.

---

# INC-010 — `HUMAN_REVIEW`

## Symptoms

Project enters `HUMAN_REVIEW`, `current_task` is cleared as required by the lifecycle, and normal dispatch stops.

## Meaning

The Runtime requires a human decision and refuses to guess.

## Safe procedure

Use:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare ...
```

review the hash-bound receipt, then:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply ...
```

Restart normal orchestration as required by the documented flow.

## DO NOT

Do not delete the Human Review flag or set status manually.

---

# INC-011 — Orchestrator was interrupted with Ctrl+C

## Symptoms

Console shows an interruption and PowerShell returns to prompt.

## Meaning

This is a process interruption, not the formal STOP signal.

Persisted project/Runtime state remains authoritative.

## Safe procedure

1. leave live state unchanged;
2. ensure no conflicting Orchestrator is still running;
3. restart with:

```powershell
.\START_AGENT_SYSTEM.ps1
```

The Runtime should resume according to persisted lifecycle state.

If an Executor was still running separately, reason about that task's claim/completion state before intervening.

---

# INC-012 — User wants a temporary pause

## Safe procedure

Pause ZCode Scheduled Automation.

Optionally interrupt the Orchestrator process if you need a frozen maintenance window.

## DO NOT

Do not use `STOP_AGENT_SYSTEM.ps1` merely as a temporary pause.

---

# INC-013 — User wants a real stop

Use:

```powershell
.\STOP_AGENT_SYSTEM.ps1
```

This creates the real STOP signal and should be treated as a terminal stop request for normal automatic continuation.

---

# INC-014 — Previously accepted deliverables must be revised under a new quality standard

## Symptoms

A batch was marked PASS under an older baseline, then the user discovers a systematic defect.

Examples:

- output density too low;
- font too small;
- missing images;
- evidence presentation style wrong;
- internal meta-comments leaked into user-visible deliverables.

## Safe procedure

Append a newer feedback block to `RESEARCH_STATE.md`.

State explicitly:

```text
- this feedback is newer than the previous production baseline;
- historical PASS records remain history;
- already-generated deliverables must be re-evaluated retroactively;
- unfinished deliverables must use the new baseline immediately.
```

Do not rewrite old history merely to hide the previous PASS.

If the current Executor task was issued under the old standard, use INC-003.

---

# INC-015 — User wants to change the project Goal

## Distinguish two cases

### Same objective, new execution/quality requirement

Use `RESEARCH_STATE.md`.

Examples:

- stronger QA;
- larger fonts;
- new evidence threshold;
- rework already-generated outputs;
- different visual style;
- method redirection.

### Materially different objective

Create a new project/Goal.

Do not mutate the anchored Goal in place.

---

# INC-016 — Automation Workspace points at a project directory

## Symptoms

Executor cannot see Runtime-root wire/claim helpers or behaves inconsistently across projects.

## Fix

Set ZCode Automation Workspace to:

```text
<Runtime Root>
```

not:

```text
<Runtime Root>\projects\<project-id>
```

The Automation is Runtime-level.

---

# INC-017 — Two Runtime Roots exist on one machine

This is allowed.

Requirements:

- each Runtime Root is isolated;
- each has its own Automation;
- each has its own Orchestrator lock/state;
- an Executor operating in one Runtime must never inspect or mutate the sibling Runtime.

---

# INC-018 — User asks "can I just delete/modify this control file?"

Default answer:

> No, not until the specific lifecycle ownership is identified.

Files that should not be casually edited include:

```text
control\ACTIVE_PROJECT.json
control\orchestrator_runtime.json
projects\<id>\project_state.json
project Goal Anchor fields
handoff\executor_claims\
handoff\completion_ledger\
```

Prefer Runtime helpers and documented recovery paths.

---

## Final escalation rule

If the observed state does not match a documented case:

1. freeze future Executor wakes;
2. preserve all live artifacts;
3. inspect code/contracts for the exact Runtime version;
4. create a read-only incident snapshot;
5. reproduce in an isolated lab if Runtime Core may be defective;
6. do not "fix" production by weakening fail-closed checks.
