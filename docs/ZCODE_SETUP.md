# ZCode Scheduled Automation Setup

This document explains how to connect ZCode / GLM as the Executor for General Agent Runtime V1.

## 1. One Automation per Runtime

Each Runtime installation should have its own ZCode Scheduled Automation.

Do not share one Automation across multiple Runtime roots.

The Automation workspace must be the root directory of the Runtime installation.

Example:

```text
D:\general-agent-runtime\runtime
```

## 2. Configure the Automation Workspace

In ZCode, create a Scheduled Automation.

Set its workspace to the absolute Runtime Root.

The workspace should contain files and directories such as:

```text
TO_ZCODE.md
control\
handoff\
projects\
scripts\
```

## 3. Configure the Executor Prompt

The canonical Executor prompt is stored at:

```text
control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md
```

Copy the full contents of that file into the ZCode Scheduled Automation prompt.

Before using it, replace every literal:

```text
<RUNTIME_ROOT>
```

with the absolute path of your Runtime installation.

Example:

```text
<RUNTIME_ROOT>
```

becomes:

```text
D:\general-agent-runtime\runtime
```

Do not change the protocol rules unless you are intentionally modifying the Runtime protocol itself.

## 4. Executor Model

Configure ZCode to use the intended Executor model available in your environment.

The Executor is responsible for executing mechanically authorized stages only.

It is not the Supervisor and must not decide the next stage or project completion.

## 5. Keep the Automation Paused During Setup

Keep the Scheduled Automation paused while:

- installing or updating the Runtime;
- creating a project;
- checking Runtime configuration;
- debugging protocol problems.

Enable it only after the Orchestrator has published a valid Executor task.

## 6. Authorization Rule

Seeing `TO_ZCODE.md` does not authorize execution.

The Executor must first run the claim helper specified by the task.

Only:

```text
CLAIM_ACQUIRED
exit code 0
```

authorizes stage execution.

Any other result must follow the fail-closed rules in the canonical Executor prompt.

## 7. Completion Rule

The Executor must not directly publish authoritative completion.

The legal completion path is:

```text
stage outputs
→ completion staging
→ scripts/executor_completion.py commit
→ COMPLETION_COMMITTED
→ immediate Executor exit
```

The Runtime owns authoritative completion state, consume/seal transitions, and the root compatibility artifacts.

## 8. Root Compatibility Files

The Executor must never directly create, repair, overwrite, or republish:

```text
SUPERVISOR_BRIEF.md
ZCODE_LAST_PROCESSED.txt
ZCODE_DONE.flag
```

These are Runtime-generated compatibility artifacts.

## 9. Isolation Rule

The Automation must operate only on its configured Runtime Root and the currently active project.

It must not inspect or modify another Runtime installation, production environment, test environment, backup, historical copy, or sibling Runtime tree.

## 10. Normal Use

The normal lifecycle is:

```text
User creates/activates project
→ Orchestrator runs Supervisor
→ Supervisor publishes Executor task
→ ZCode Scheduled Automation wakes
→ claim
→ execute one stage
→ completion staging
→ completion commit
→ Executor exits
→ Orchestrator consumes completion
→ Supervisor reviews
→ next task or Final Verification
→ COMPLETE
```

Once the project reaches a terminal state, the Scheduled Automation can be paused.

## Important

The canonical source of truth for Executor behavior is:

```text
control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md
```

This setup document explains how to install it; it does not replace the protocol defined there.
