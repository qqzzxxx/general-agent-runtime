# General Agent Runtime — Operator Playbook

[Phase 8 Supervisor V2](v1.4-supervisor-v2.md) needs no project-state migration.
When separately deploying, ship `supervisor_context.py`, the canonical Supervisor
contract and `SUPERVISOR_PROTOCOL_REFERENCE.md` together; the Runtime creation copier
includes the new static reference. Existing Executor V2 configuration is unchanged.
Steer the desired experience and true constraints explicitly; an existing layout is
not a requirement unless the user says it is. Full decision history remains available.

For [Phase 6](v1.4-host-native-executor.md), deploy code and the regenerated permanent
prompt together when deployment is authorized. Native tools are allowed for the
bound outcome when available in the scheduled session. Existing explicit sealed
restrictions remain in force; do not edit a live dispatch to enable tools. A
checkpoint/publication rejection ends authority, but cannot kill independent host
processes. Confirm old Executor activity has stopped during interruption/rollout.

For v1.4 development deployments, install the updated Executor prompt alongside
Runtime code. Normal Executor work uses entry, later fence checkpoints, and one
semantic finish operation. Do not ask Executors to hash outputs or author staging.
Existing claims, publication records and completion ledger remain authoritative.
See [Phase 3](v1.4-runtime-owned-completion.md) for recovery and compatibility limits.

> Normal human operating procedures for General Agent Runtime.
>
> This document covers ordinary use and controlled steering. For failures and repair, use `INCIDENT_RUNBOOK.md`.
> If you are starting with only an idea, begin with
> [Start a New Project from an Idea](NEW_PROJECT_WORKFLOW.md) and the
> [Project Goal Workshop](PROJECT_GOAL_WORKSHOP.md).

## 1. Operator philosophy

The human should:

- define a good project Goal;
- configure one Executor Automation per Runtime;
- start the Runtime;
- inspect meaningful milestones when desired;
- append steering feedback when necessary;
- respond to explicit `HUMAN_REVIEW`;
- stop or archive completed work.

The human should **not**:

- manually relay Supervisor tasks to the Executor;
- manually fabricate claim state;
- edit lifecycle JSON to "unstick" the system;
- delete claims;
- rewrite the canonical Goal after project creation;
- hand-publish root completion artifacts.

---

## 2. Fresh Runtime setup

### 2.1 Clone/download the Runtime

Use a clean Runtime Root. A clean clone/download may serve as the Runtime for a public
user. Do not run ordinary projects in the maintainer/source checkout. A separate Runtime
Root per substantial independent project is recommended for stronger isolation and
archival, especially for maintainers, although one Runtime can technically retain
multiple projects sequentially because V1 activates only one at a time.

Each separate Runtime Root needs its own Automation. Multiple projects in the same
Runtime reuse that Runtime's existing Automation.

### 2.2 Keep Automation absent or paused

During Goal preparation and Project creation, keep this Runtime Root's ZCode Automation
absent or paused. Configuring it earlier is mechanically safe while it remains paused,
but the canonical first-run sequence below configures it after `START_PROJECT.ps1`.

---

## 3. Prepare the project Goal

Use the [Project Goal Workshop](PROJECT_GOAL_WORKSHOP.md) when turning a rough idea into
an approved external Goal file.

A useful Goal states:

- objective;
- inputs/data/files;
- required deliverables;
- constraints;
- non-goals;
- acceptance criteria;
- evidence/reproducibility expectations;
- safety/risk boundaries.

Do not over-script the exact stage sequence. The Supervisor should be free to revise the route.

For a large project, it is reasonable to discuss the project with a strong reasoning model before creating the Goal.

---

## 4. Create and activate the project

Example:

```powershell
.\START_PROJECT.ps1 `
  -ProjectId "demo-001" `
  -ProjectType "GENERAL" `
  -GoalFile "C:\goals\demo.md"
```

After creation, treat:

```text
projects\demo-001\PROJECT_GOAL.md
```

as immutable.

---

## 5. Configure ZCode Scheduled Automation once

Workspace:

```text
<Runtime Root>
```

Not:

```text
<Runtime Root>\projects\<project-id>
```

Copy the permanent prompt from:

```text
control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md
```

Bind `<RUNTIME_ROOT>` to the absolute path and follow
[ZCode Scheduled Automation Setup](ZCODE_SETUP.md) for the compatible model choice and
cadence. Keep the Automation paused.

For EXECUTOR-FENCE-V1 upgrades, stop existing Executor sessions and child writers before
replacing the Runtime code and installed Automation prompt together. A lost token,
expired attempt, or `ATTEMPT_FENCED` result means stop, preserve artifacts, and recover
through a fresh Supervisor attempt. Never delete/reacquire the claim or resume a legacy
direct-write session. Read [Stale worker fencing](STALE_WORKER_FENCING.md) before
upgrading an existing Runtime.

For later Projects in this same Runtime, reuse this Automation rather than creating
another one.

---

## 6. Run preflight

```powershell
python .\scripts\preflight.py
```

Expected:

```text
PREFLIGHT: OK
Mode: isolated
Project ID: demo-001
```

---

## 7. Start normal autonomous execution

Enable the Runtime's ZCode Automation, then:

```powershell
.\START_AGENT_SYSTEM.ps1
```

Normal sequence:

```text
Supervisor
-> task published
-> Executor wake
-> claim; winner retains claim token
-> fenced attempt workspace + checkpoints
-> Runtime-fenced canonical publication
-> completion commit with claim token
-> Executor exits on COMPLETION_COMMITTED
-> Orchestrator consume/seal
-> Supervisor
-> ...
```

The user does not relay messages.

---

## 8. Configure the Supervisor model (optional baseline)

By default the Orchestrator runs Codex Supervisor turns with its built-in
default policy. To pin a different baseline, set the two optional fields in
the Runtime's control document `control/supervisor_control.json`:

```json
{
  "schema_version": 1,
  "revision": 0,
  "intervention_generation": 0,
  "pause": {"status": "RUNNING", "requested_at": null, "mode": null, "resumed_at": null},
  "supervisor_model": "gpt-6-astra",
  "supervisor_reasoning_effort": "high"
}
```

Rules:

- `supervisor_model` and `supervisor_reasoning_effort` must be present and
  valid together; `supervisor_reasoning_effort` accepts
  `LOW` / `MEDIUM` / `HIGH` / `XHIGH` case-insensitively. Queued changes
  through the Console or `queue-supervisor-config` accept only the
  canonical models `gpt-5.6-sol` and `gpt-6-astra` (confirmed against the
  installed Codex CLI; see `evidence/v1.4-supervisor-config-ui/`).
- A missing pair keeps the built-in default policy (backward compatible
  with older control documents). A partial or invalid pair is never
  half-applied: the built-in default policy applies and the Orchestrator
  logs the values it actually used each turn.
- An explicit queued configuration (Web Console "Queue a model / reasoning
  change", or `supervisor_control.py queue-supervisor-config`) still takes
  precedence at the next eligible Supervisor turn boundary.
- The Web Console Supervisor panel reads this same configuration and shows
  it as the fixed policy baseline; no second configuration exists.
- ZCode (Executor) and the Scheduled Automation are not affected by this
  setting.

---

## 9. What to watch in the console

Useful lifecycle events:

```text
EXECUTOR_TASK_PUBLISHED
CLAIM_ACQUIRED
COMPLETION_COMMITTED
COMPLETION_CONSUMED
COMPLETION_SEALED
Supervisor decision
HUMAN_REVIEW
BLOCKED
COMPLETE
STOPPED
```

`WAITING_EXECUTOR` is normal while waiting for the Scheduled Automation.

A Scheduled Automation wake that exits because of `CLAIM_EXISTS` can also be normal.

---

## 10. Safe inspection during a long project

You may inspect:

- project deliverables;
- rendered artifacts;
- project reports;
- project evidence;
- current `RESEARCH_STATE.md`;
- console output;
- Scheduled Automation history.

Avoid editing live protocol files merely because you are inspecting.

If you find a quality problem, use steering feedback rather than rewriting Goal or live state.

---

## 11. Adding user steering feedback

Use the active project's:

```text
projects\<project-id>\RESEARCH_STATE.md
```

Append concise, decision-relevant feedback.

Recommended pattern:

```markdown
# USER_FEEDBACK_003 — Short title

Priority:
- this is the newest quality baseline
- it does not modify PROJECT_GOAL.md
- it applies to future work
- it applies retroactively to previously generated deliverables if stated

Requirements:
- ...
```

Keep prior history. Do not rewrite old PASS records simply to make history look cleaner.

Explicitly say when a newer baseline supersedes old acceptance.

Example:

```text
Previously generated deliverables must be re-evaluated under this rule.
Unfinished deliverables should use this rule immediately.
```

---

## 12. Timing of feedback

### Case A — Executor has not claimed the task yet

Best option:

- keep Automation paused;
- allow the Supervisor to receive feedback before the next task is executed, or use documented recovery if a stale task must be replaced.

Do not manually edit the task wire.

### Case B — Executor is already running and the task is almost done

Often best:

1. append feedback to `RESEARCH_STATE.md`;
2. let the current task complete;
3. allow the next Supervisor turn to see:
   - current completion result;
   - latest project memory;
4. verify that the Supervisor's new decision incorporates the feedback.

### Case C — Executor is running but continuing would waste major work or propagate a systemic defect

Use the Incident Runbook's controlled steering/abort procedure.

Do not:

- delete the claim;
- reuse the same identity;
- edit `project_state.json` by hand.

---

## 13. Pausing vs stopping

### `PAUSE_AGENT_SYSTEM.ps1`

Use the Runtime pause command when you want to prevent the next stage while
inspecting or steering:

```powershell
.\PAUSE_AGENT_SYSTEM.ps1
```

Idle work pauses immediately. An authorized-but-unclaimed dispatch is parked
(QUOTA-PAUSE-PARK-V1): the inbox is quarantined so nothing can be claimed or
executed while paused, the identity stays valid, no retry budget is consumed,
and Resume re-plans the same logical stage automatically with a fresh
authorized task — no Human Decision is required after a plain quota pause.
A long pause may outlive the parked dispatch's authorization expiry; Resume
still recovers through a new Supervisor-authorized identity, never by
extending the expired nonce. An already-claimed attempt normally finishes;
Runtime consumes its authoritative completion but does not start the next
Supervisor turn. If the claimed attempt's authorization expires while paused,
it is retired per fencing and converted into one bounded Supervisor re-plan,
so Resume stays available. For explicit
cooperative revocation at the next fence checkpoint:

```powershell
.\PAUSE_AGENT_SYSTEM.ps1 -InterruptCurrentTask
```

This is Runtime authority revocation, not an instantaneous operating-system kill.
If an authoritative completion committed before the interrupt obtained the fence, the
completion wins and is delivered; if retirement committed first, later publication or
completion is rejected. Safe-pause completion is persisted as a deferred event and is
replayed to one Supervisor turn after resume, including across restart.
Resume with `RESUME_AGENT_SYSTEM.ps1`; it does not replay completed work or bypass
HUMAN_REVIEW.

This does not terminally stop the project.

### `Ctrl+C` Orchestrator

Use only as a process-level temporary interruption when necessary.

It does not create the formal STOP signal.

### `STOP_AGENT_SYSTEM.ps1`

Use when you actually intend to stop the Runtime's automatic project progression.

It is not the normal way to inject temporary feedback.

### Steering, AUDIT, and history

Use `REQUEST_SUPERVISOR_INTERVENTION.ps1`, not a manual `RESEARCH_STATE.md` edit:

```powershell
.\REQUEST_SUPERVISOR_INTERVENTION.ps1 -Text "Change direction after this stage."
.\REQUEST_SUPERVISOR_INTERVENTION.ps1 -Mode AUDIT -TargetMessageId 700120 `
  -Text "Audit this dispatch and materially dependent later work."
```

The original intervention bytes and delivery status remain auditable. Submission is
recoverable across interruption; delivery becomes consumed only with a validated
durable Supervisor decision receipt. A no-op, invalid, or stale turn leaves it pending
for redelivery. A historical
target never rewrites dispatches, completions, or decisions; corrections use new
MESSAGE_IDs. Inspect the exact verified histories with:

```powershell
.\SHOW_SUPERVISOR_TASKS.ps1 -MessageId 700120
.\SHOW_EXECUTOR_FEEDBACK.ps1 -MessageId 700120
.\SHOW_AGENT_TIMELINE.ps1 -Json
.\SHOW_SUPERVISOR_INTERVENTIONS.ps1 -Json
```

Task-history integrity is explicit: `AUTHORIZED_VALID`, `UNAUTHORIZED`, `INCOMPLETE`,
or `CORRUPT`. The query refuses escaped paths and does not repair a broken record.
All v1.2 `-Json` wrappers write exactly one ASCII-safe JSON document to stdout; ordinary
status/start text is separate, so redirected output can be parsed under Windows
PowerShell 5 and PowerShell 7.

After upgrade, an archive-less authorization is not a new claim capability. Leave an
already-running claimed attempt to the documented completion-only recovery path. An
unclaimed legacy dispatch must be explicitly migrated/re-registered (or replaced by a
fresh Supervisor decision) so the v1.2 archive and seal exist before execution.

---

## 14. HUMAN_REVIEW

When the Runtime enters `HUMAN_REVIEW`, automation stops by design.

Use:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare `
  -ProjectId "demo-001" `
  -DecisionFile "decision.json" `
  -ReceiptFile "receipt.json"
```

Inspect the receipt.

Then:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply `
  -ReceiptFile "receipt.json"
```

Do not hand-edit the Human Review flag or lifecycle state.

---

## 15. Completion

When project status becomes `COMPLETE`:

- inspect the final report/deliverables;
- pause the ZCode Automation if no new project will run immediately;
- preserve the Runtime/project data as needed;
- create a new project for a new objective.

Do not expect the Runtime to silently start another project.

---

## 16. New project in the same Runtime

The same Runtime can store multiple projects.

You do not need a new ZCode Automation for every project.

Keep:

```text
Workspace = Runtime Root
```

A different Runtime Root should get a different Automation.

For a later sequential Project, pause the existing Automation while preparing the Goal
and running `START_PROJECT.ps1`. Then run preflight, enable that same Automation, and
start the Runtime. Do not create a second Automation for the Project.

For a substantial independent objective, consider a separate Runtime Root even though a
same-Runtime Project is mechanically supported. This creates a clearer isolation and
archival boundary. Follow [Start a New Project from an Idea](NEW_PROJECT_WORKFLOW.md) to
choose the pattern; Automation setup is reused only within the same Runtime Root.

---

## 17. Starting a new AI support conversation

Use the Runtime itself as the context package.

Tell the new AI:

> Read `AI_BOOTSTRAP.md` first and follow its routing rules. Use this repository version and current active project as authority. Do not infer behavior from generic multi-agent experience.

Then ask the actual question.

This removes dependency on a historical ChatGPT conversation.

---

## 18. Routine read-only diagnostic commands

### Current project

```powershell
Get-Content .\control\ACTIVE_PROJECT.json -Raw
```

### Project state

```powershell
$active = Get-Content .\control\ACTIVE_PROJECT.json -Raw | ConvertFrom-Json
$projectRoot = Join-Path $PWD $active.project_root
Get-Content (Join-Path $projectRoot "project_state.json") -Raw
```

### Current Executor task

```powershell
Get-Content .\TO_ZCODE.md -Raw
```

### Preflight

```powershell
python .\scripts\preflight.py
```

### Git working tree for a development Runtime

```powershell
git status --short
```

These are diagnostics, not permission to mutate state.

---

## 19. Operator decision table

| Situation | Preferred action |
|---|---|
| Normal stage running | Let it run |
| Need to inspect output | Inspect project artifacts; do not touch protocol |
| New quality feedback, current task nearly done | Submit `REQUEST_SUPERVISOR_INTERVENTION.ps1`; safe delivery follows the current stage |
| New feedback, current task would cause large waste | Submit with `-InterruptCurrentTask`, or pause with that explicit option |
| Duplicate Scheduled Automation wake | Let claim semantics handle it |
| Goal Anchor mismatch | Stop editing; use incident runbook |
| HUMAN_REVIEW | Prepare/apply audited decision receipt |
| Need a materially different project objective | Create a new project/Goal |
| Want temporary pause | `PAUSE_AGENT_SYSTEM.ps1` |
| Want true stop | `STOP_AGENT_SYSTEM.ps1` |
