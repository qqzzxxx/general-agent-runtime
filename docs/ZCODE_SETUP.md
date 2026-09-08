# ZCode Scheduled Automation Setup

This document explains how to connect ZCode / GLM as the Executor for General Agent Runtime V1.

## 1. One Automation per Runtime

Each Runtime installation should have its own ZCode Scheduled Automation.

Do not share one Automation across multiple Runtime Roots.

A single Runtime may contain many projects, but V1 activates one project at a time. The
Automation is Runtime-level: creating or switching projects does **not** require a new
Automation.

## 2. Workspace = Runtime Root

In ZCode, create a Scheduled Automation and set its Workspace to the absolute Runtime
Root.

Example:

```text
D:\general-agent-runtime\runtime
```

Do **not** point the Workspace at:

```text
D:\general-agent-runtime\runtime\projects\<project-id>
```

A fresh Runtime Root should contain tracked directories/files such as:

```text
control\
handoff\
profiles\
projects\
scripts\
orchestrator.py
START_PROJECT.ps1
START_AGENT_SYSTEM.ps1
```

`TO_ZCODE.md` is **not** expected in a fresh clone. It is a live, gitignored inbox that
the Runtime creates when the Supervisor publishes an Executor task.

## 3. Install the Canonical Executor Prompt

The canonical permanent Executor prompt is stored at:

```text
control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md
```

The preferred first-time setup method is to run this from the Runtime Root after
`START_PROJECT.ps1`:

```powershell
.\PREPARE_ZCODE_AUTOMATION.ps1
```

The helper reads the canonical template without modifying it, validates and replaces
all literal `<RUNTIME_ROOT>` placeholders with the absolute Runtime Root, and copies the
fully rendered prompt to the clipboard. Set the ZCode Workspace to the Runtime Root it
displays and paste the clipboard content into the Scheduled Automation prompt.

The helper only generates and copies the bound prompt. It does not configure ZCode,
choose a model or cadence, enable the Automation, start the Runtime, create or activate a
Project, or edit Runtime state. Keep the Automation paused through preflight.

For environments without clipboard support, `-NoClipboard` performs validation and
rendering but skips the copy. The manual fallback is:

1. Open `control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md`.
2. Copy its **full contents**.
3. Replace every literal `<RUNTIME_ROOT>` with the absolute path of this Runtime Root.
4. Paste the fully rendered prompt into ZCode.

For example, this literal placeholder:

```text
<RUNTIME_ROOT>
```

with the absolute path of this Runtime installation.

Example:

```text
<RUNTIME_ROOT>
```

becomes:

```text
D:\general-agent-runtime\runtime
```

Do not rewrite the protocol rules unless you are intentionally developing a new Runtime
protocol, and never edit the canonical template merely to bind one installation path.

The permanent Automation prompt normally stays unchanged across projects. For later
sequential Projects in the same Runtime, reuse the existing Automation and do not rerun
the helper unless you intentionally want to refresh or reinstall the prompt after a
Runtime update.

## 4. Executor Model

Current V1.1 dispatches identify the Executor protocol family as `GLM-5.3`. In ZCode,
select a compatible concrete model variant in that family. The maintainer's current
concrete selection, `GLM-5.3-Flash`, is explicitly acceptable under the current contract.

The concrete UI variant is an operator choice, not a permanent Runtime or Project
setting, and it may be changed in ZCode. Do not treat an arbitrary unrelated model family
as a validated drop-in replacement merely because it is capable; another family or
backend may be adaptable in principle, but requires its own compatibility validation.

Whatever compatible variant is selected must continue to obey the canonical Executor
protocol: retain the exact task identity and winning claim token; use the atomic claim,
`executor_fence.py prepare/check`, attempt-local workspace, and fenced canonical
publication; commit completion with the claim token; stay within the exact stage scope;
exit immediately after `COMPLETION_COMMITTED`; and never choose the next stage. The
canonical prompt installed in section 3 contains the complete rules.

The Executor performs mechanically authorized stages. It is not the Supervisor: it must
not choose the next stage, Final Acceptance, or project completion.

## 5. Choose the Recurrence / Cadence

General Agent Runtime does **not** mandate one fixed numeric recurrence. Recurrence only
controls how quickly a newly published `WAITING_EXECUTOR` task is noticed; it is never
execution authorization. Every wake must still pass the exact claim and fencing flow,
and overlapping or duplicate wakes safely stop through claim semantics when they do not
own the attempt.

Choose the shortest recurrence that provides the pickup latency you want and that ZCode
makes available. A shorter recurrence reduces dispatch pickup latency; a longer one can
leave the Runtime in `WAITING_EXECUTOR` until the next wake. Current Runtime deadline
calculation reserves at least one hour of scheduler grace because ZCode may offer only an
hourly wake. When hourly is ZCode's shortest available recurrence, use hourly. Do not
choose a recurrence longer than the effective scheduler-grace window unless the task
policy has explicitly allowed enough `SCHEDULER_GRACE_SECONDS` for that cadence.

The one-hour minimum grace is a Runtime watchdog allowance for a possible ZCode
application constraint, not a requirement that every Executor implementation use an
hourly schedule.

## 6. Keep the Automation Paused During Setup

Keep the Scheduled Automation paused while:

- installing or updating the Runtime;
- configuring the Workspace/prompt;
- creating or activating a project;
- checking preflight;
- debugging protocol problems.

Keep ZCode paused until Runtime setup, project creation, and any explicit preflight
checks are complete. Immediately before normal execution, enable the Automation and then
start `START_AGENT_SYSTEM.ps1`.

Once correctly configured and enabled, the Automation handles Executor wakes
automatically for the active project. The human does not wait for
`EXECUTOR_TASK_PUBLISHED` and does not relay tasks manually. After a terminal project
state, the Automation can be paused again.

## 7. Authorization Rule

Seeing `TO_ZCODE.md` does **not** authorize execution.

The Executor must read the exact task identity and run the claim helper command specified
by the task.

Only:

```text
CLAIM_ACQUIRED
exit code 0
```

allows the winning wake to begin the fenced attempt protocol. For a current fenced
dispatch, acquisition returns a claim token that the owner must retain. The permanent
claim is acquisition history, not lasting write authority.

Exit code 10 (`CLAIM_EXISTS`) or 11 (`ALREADY_PROCESSED`) means this wake exits quietly.
Any other claim-helper failure follows the fail-closed rules in the canonical prompt.

Never delete a claim directory to "retry" a task.

After acquisition, the owner must run `executor_fence.py prepare`, do all candidate work
in the returned attempt-local workspace, run `executor_fence.py check` at the required
checkpoints, and publish supported canonical outputs only through
`executor_fence.py publish`. A successful earlier check never authorizes a later direct
canonical write. See [Stale worker fencing](STALE_WORKER_FENCING.md) for exact commands,
supported paths, and failure semantics; the installed canonical prompt remains the
Executor's complete instruction set.

## 8. Execution Scope

The Automation must never operate outside its configured Runtime Root.

Within that Runtime:

- normal project work is scoped to the currently active project;
- Runtime-core files are out of scope by default;
- Runtime-core maintenance is allowed only when the current mechanically authorized task
  explicitly grants a narrow Runtime-root scope;
- another Runtime installation, production environment, test copy, backup, historical
  copy, or sibling Runtime tree is always out of scope unless it is itself the configured
  Runtime for a separate Automation.

A visible path or file is not permission. The current authorized task defines the
Executor's stage scope.

## 9. Completion Rule

The Executor must not directly publish authoritative completion.

Legal path:

```text
claim winner retains claim token
-> executor_fence.py prepare
-> attempt-local candidate work + executor_fence.py check checkpoints
-> executor_fence.py publish canonical outputs
-> completion staging
-> scripts/executor_completion.py commit --staging-dir "<staging dir>" --claim-token "<claim token>"
-> COMPLETION_COMMITTED
-> immediate Executor exit
```

The Runtime owns the authoritative completion state and the
`COMPLETION_COMMITTED -> COMPLETION_CONSUMED -> COMPLETION_SEALED` lifecycle.

On a successful completion commit, the Executor stops that wake immediately. It does not
wait for the Supervisor, poll for consumption, or choose the next stage.

## 10. Root Compatibility Files

The Executor must never directly create, repair, overwrite, or republish:

```text
SUPERVISOR_BRIEF.md
ZCODE_LAST_PROCESSED.txt
ZCODE_DONE.flag
```

These are Runtime-generated compatibility artifacts, not Executor-owned truth.

On a fresh Runtime, `ZCODE_LAST_PROCESSED.txt` may be absent. That is valid and means no
Executor message has been processed yet.

## 11. Fresh-Clone First Run

Recommended first-run sequence:

```text
0. obtain/create a clean Runtime Root
1. keep this Automation absent or paused during setup
2. use AI_BOOTSTRAP.md + PROJECT_GOAL_WORKSHOP.md to design the project
3. approve an external Goal file
4. run START_PROJECT.ps1 with that Goal file
5. if this Runtime has no Automation yet, run PREPARE_ZCODE_AUTOMATION.ps1
6. set ZCode Workspace, paste the prepared prompt, and keep the Automation paused
7. run python scripts\preflight.py
8. enable this Automation
9. run START_AGENT_SYSTEM.ps1
10. stop relaying tasks and let the Runtime loop autonomously
```

The Goal-design and end-to-end context lives in
[Start a New Project from an Idea](NEW_PROJECT_WORKFLOW.md). Automation setup is not
repeated for each Project in the same Runtime.

For a later sequential Project in an already-configured Runtime, keep the existing
Automation paused while preparing and activating the new Project. Reuse its Workspace,
prompt, model choice, and cadence; run preflight, enable it, and start the Runtime. Do not
create a second Automation for that Project.

Do not manually create legacy root bootstrap files.

## 12. Normal Lifecycle

```text
User creates/activates project
-> Orchestrator runs Supervisor
-> Supervisor atomically publishes a candidate TO_ZCODE.md task
-> Runtime validates it and registers exact identity/hash authorization
-> ZCode Scheduled Automation wakes
-> claim; winner receives claim token
-> executor_fence.py prepare
-> execute exactly one authorized stage in the attempt workspace
-> executor_fence.py check at required checkpoints
-> executor_fence.py publish canonical outputs
-> completion staging
-> executor_completion.py commit with --claim-token
-> COMPLETION_COMMITTED
-> Executor exits
-> Orchestrator consumes + seals completion
-> Supervisor reviews
-> next task or Final Verification
-> COMPLETE / HUMAN_REVIEW / STOP / BLOCKED
```

The human is not the Supervisor/Executor message bus.

## 13. Terminal and Human-Attention States

When the project reaches a terminal/attention state, follow the Runtime console and
`control\USER_ATTENTION.json`.

- `COMPLETE`: project accepted; Automation can be paused.
- `HUMAN_REVIEW`: human input/authorization is required; do not let the Executor invent
  the decision.
- `STOPPED` / real STOP: no silent automatic continuation.
- `BLOCKED`: inspect the reported mechanical/protocol reason before taking action.

## Important

The canonical source of truth for Executor behavior is:

```text
control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md
```

This setup document explains how to install and operate it; it does not replace the
protocol defined there.
