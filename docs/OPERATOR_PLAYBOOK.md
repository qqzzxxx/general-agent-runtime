# General Agent Runtime — Operator Playbook

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

## 8. What to watch in the console

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

## 9. Safe inspection during a long project

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

## 10. Adding user steering feedback

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

## 11. Timing of feedback

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

## 12. Pausing vs stopping

### Pause ZCode Automation

Use when you want to prevent the next Executor wake while inspecting/steering.

This does not terminally stop the project.

### `Ctrl+C` Orchestrator

Use only as a process-level temporary interruption when necessary.

It does not create the formal STOP signal.

### `STOP_AGENT_SYSTEM.ps1`

Use when you actually intend to stop the Runtime's automatic project progression.

It is not the normal way to inject temporary feedback.

---

## 13. HUMAN_REVIEW

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

## 14. Completion

When project status becomes `COMPLETE`:

- inspect the final report/deliverables;
- pause the ZCode Automation if no new project will run immediately;
- preserve the Runtime/project data as needed;
- create a new project for a new objective.

Do not expect the Runtime to silently start another project.

---

## 15. New project in the same Runtime

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

## 16. Starting a new AI support conversation

Use the Runtime itself as the context package.

Tell the new AI:

> Read `AI_BOOTSTRAP.md` first and follow its routing rules. Use this repository version and current active project as authority. Do not infer behavior from generic multi-agent experience.

Then ask the actual question.

This removes dependency on a historical ChatGPT conversation.

---

## 17. Routine read-only diagnostic commands

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

## 18. Operator decision table

| Situation | Preferred action |
|---|---|
| Normal stage running | Let it run |
| Need to inspect output | Inspect project artifacts; do not touch protocol |
| New quality feedback, current task nearly done | Append feedback; let task complete; inspect next Supervisor decision |
| New feedback, current task would cause large waste | Pause future wakes; use controlled abort/steering runbook |
| Duplicate Scheduled Automation wake | Let claim semantics handle it |
| Goal Anchor mismatch | Stop editing; use incident runbook |
| HUMAN_REVIEW | Prepare/apply audited decision receipt |
| Need a materially different project objective | Create a new project/Goal |
| Want temporary pause | Pause Automation / optionally Ctrl+C Orchestrator |
| Want true stop | `STOP_AGENT_SYSTEM.ps1` |
