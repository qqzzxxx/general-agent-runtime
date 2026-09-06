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

Copy the **full contents** of that file into the ZCode Scheduled Automation prompt.

Replace every literal:

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
protocol.

The permanent Automation prompt normally stays unchanged across projects.

## 4. Executor Model

Configure ZCode to use the intended Executor-capable model available in your environment.

The Executor performs mechanically authorized stages. It is not the Supervisor: it must
not choose the next stage, Final Acceptance, or project completion.

## 5. Keep the Automation Paused During Setup

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

## 6. Authorization Rule

Seeing `TO_ZCODE.md` does **not** authorize execution.

The Executor must read the exact task identity and run the claim helper command specified
by the task.

Only:

```text
CLAIM_ACQUIRED
exit code 0
```

authorizes stage execution.

Exit code 10 (`CLAIM_EXISTS`) or 11 (`ALREADY_PROCESSED`) means this wake exits quietly.
Any other claim-helper failure follows the fail-closed rules in the canonical prompt.

Never delete a claim directory to "retry" a task.

## 7. Execution Scope

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

## 8. Completion Rule

The Executor must not directly publish authoritative completion.

Legal path:

```text
stage outputs
-> completion staging
-> scripts/executor_completion.py commit
-> COMPLETION_COMMITTED
-> immediate Executor exit
```

The Runtime owns the authoritative completion state and the
`COMPLETION_COMMITTED -> COMPLETION_CONSUMED -> COMPLETION_SEALED` lifecycle.

On a successful completion commit, the Executor stops that wake immediately. It does not
wait for the Supervisor, poll for consumption, or choose the next stage.

## 9. Root Compatibility Files

The Executor must never directly create, repair, overwrite, or republish:

```text
SUPERVISOR_BRIEF.md
ZCODE_LAST_PROCESSED.txt
ZCODE_DONE.flag
```

These are Runtime-generated compatibility artifacts, not Executor-owned truth.

On a fresh Runtime, `ZCODE_LAST_PROCESSED.txt` may be absent. That is valid and means no
Executor message has been processed yet.

## 10. Fresh-Clone First Run

Recommended first-run sequence:

```text
1. clone/download Runtime
2. configure this Automation (Workspace = Runtime Root, canonical prompt installed)
3. keep Automation paused during setup
4. prepare a project Goal
5. run START_PROJECT.ps1
6. optionally run python scripts\preflight.py
7. enable this Automation
8. run START_AGENT_SYSTEM.ps1
9. let the Runtime loop autonomously
```

Do not manually create legacy root bootstrap files.

## 11. Normal Lifecycle

```text
User creates/activates project
-> Orchestrator runs Supervisor
-> Supervisor proposes next stage
-> Runtime validates + authorizes + publishes Executor task
-> ZCode Scheduled Automation wakes
-> claim
-> execute exactly one authorized stage
-> completion staging
-> completion commit
-> Executor exits
-> Orchestrator consumes + seals completion
-> Supervisor reviews
-> next task or Final Verification
-> COMPLETE / HUMAN_REVIEW / STOP / BLOCKED
```

The human is not the Supervisor/Executor message bus.

## 12. Terminal and Human-Attention States

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
