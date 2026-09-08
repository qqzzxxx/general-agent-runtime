# QUICKSTART

This walkthrough is the normal path for a **fresh clone** of General Agent Runtime V1.

A fresh checkout intentionally contains **no live Runtime state**. Do not manually create
legacy root state files before your first project.

New dispatches use EXECUTOR-FENCE-V1: the claim winner retains its returned token,
works in an attempt-local candidate workspace, and publishes canonical files via
`executor_fence.py`. Use the current Scheduled Automation prompt with the current
code. For an existing Runtime upgrade, stop legacy Executor sessions and their
child writers first; helpers cannot intercept their direct writes. Read
[Stale worker fencing](STALE_WORKER_FENCING.md) for commands and recovery limits.

## Requirements

- Windows with PowerShell 5+
- Python 3.12+ on `PATH`
- Codex CLI on `PATH` (required by the current Supervisor implementation)
- validated V1 Supervisor configuration: **GPT-5.6 Sol + high reasoning**
- ZCode Desktop Scheduled Automation (or an equivalent Executor implementation that conforms to the Runtime protocol)

Optional: install the Python package `rich` for enhanced interactive console output.

## 1. Open the Runtime Root

Run commands from the repository root — the directory containing files such as:

```text
START_PROJECT.ps1
START_AGENT_SYSTEM.ps1
orchestrator.py
control\
profiles\
projects\
scripts\
```

A fresh checkout does **not** need these live/legacy files:

```text
control\project_state.json
RESEARCH_STATE.md
ZCODE_LAST_PROCESSED.txt
TO_ZCODE.md
SUPERVISOR_BRIEF.md
```

Do not create them to "bootstrap" the Runtime. The isolated-project flow creates or
generates the state it actually needs.

## 2. Configure the Executor Automation Once

Create one ZCode Scheduled Automation for this Runtime installation.

- Workspace: the **Runtime Root**, not `projects\<project-id>\`
- Prompt: copy the full contents of
  `control\ZCODE_SCHEDULED_AUTOMATION_PROMPT.md`
- Replace every literal `<RUNTIME_ROOT>` in that prompt with this installation's absolute
  Runtime Root
- Keep the Automation **paused during setup**

The Automation is Runtime-level. You do not create a new Automation when you create a
new project in the same Runtime, and you do not change its Workspace between projects.

See [ZCODE_SETUP.md](ZCODE_SETUP.md) for the complete setup contract.

## 3. Prepare a Good Goal

Before starting the Runtime, make the project objective explicit.

For substantial research, engineering, or business work, it is often useful to discuss
the idea with a strong reasoning model first and turn that discussion into a deliberate
Goal document.

A good Goal usually states:

- the real objective;
- relevant context and available inputs/data/files;
- required deliverables;
- constraints and non-goals;
- success / acceptance criteria;
- evidence or reproducibility expectations;
- important forbidden actions or risk boundaries.

Do not pre-script every stage. The Supervisor is responsible for decomposing the project
and revising the method as evidence arrives.

The external Goal filename is arbitrary. `START_PROJECT.ps1` imports it into the project
as the canonical `PROJECT_GOAL.md` and binds its SHA-256. Treat that canonical Goal as
immutable after project creation.

## 4. Create and Activate the Project

`START_PROJECT.ps1` is mechanical only: it validates the input, creates the project in a
staging directory, verifies it, atomically publishes the project directory, then
activates `control\ACTIVE_PROJECT.json`. It does **not** call a model.

Using a Goal file:

```powershell
.\START_PROJECT.ps1 `
  -ProjectId "demo-001" `
  -ProjectType "SOFTWARE_ENGINEERING" `
  -GoalFile "C:\goals\demo.md"
```

Or using inline Goal text:

```powershell
.\START_PROJECT.ps1 `
  -ProjectId "demo-001" `
  -ProjectType "GENERAL" `
  -Goal "Analyze X and produce a decision report"
```

Valid `-ProjectType` values are exactly:

```text
GENERAL
ACADEMIC_RESEARCH
SOFTWARE_ENGINEERING
BUSINESS_RESEARCH
```

One Runtime can store many projects, but V1 has exactly **one active project at a time**.

## 5. Verify the Fresh Project

An explicit preflight is useful during setup:

```powershell
python .\scripts\preflight.py
```

A normal fresh isolated project should report:

```text
PREFLIGHT: OK
Mode: isolated
Project ID: demo-001
```

A missing `ZCODE_LAST_PROCESSED.txt` is normal on a fresh Runtime: it means that no
Executor message has been processed yet. A malformed existing pointer still fails
closed.

For release/debug validation you can also run the full regression suite:

```powershell
python -m unittest discover -s scripts -p "test_*.py"
```

`START_AGENT_SYSTEM.ps1` runs its own mechanical checks before starting the Orchestrator,
so manually running the suite is not required for every normal project.

## 6. Enable the Executor and Start the Agent System

After project creation and preflight are complete, enable the already-configured ZCode
Scheduled Automation, then run:

```powershell
.\START_AGENT_SYSTEM.ps1
```

The launcher checks the Runtime-root lock, runs preflight and mechanical regressions, then
starts `orchestrator.py`.

The Supervisor performs the first turn and publishes Executor tasks when needed. The
already-enabled Automation picks up a task on a later scheduled wake.

Do not manually copy a task into ZCode and do not treat `TO_ZCODE.md` as permission to
work. The Automation reads the Runtime-root inbox and may execute a stage only after the
canonical claim flow returns `CLAIM_ACQUIRED` / exit code 0.

## 7. Let the Runtime Loop

Normal lifecycle:

```text
Supervisor
-> authorized Executor task
-> ZCode wake
-> claim
-> execute exactly one stage
-> completion staging
-> completion commit
-> Executor exits
-> Orchestrator consumes + seals
-> Supervisor reviews
-> next stage / Final Verification / HUMAN_REVIEW / COMPLETE
```

You are not the message bus. Do not relay Supervisor/Executor messages by hand.

Once the project reaches `COMPLETE`, `STOPPED`, `BLOCKED`, or `HUMAN_REVIEW`, the
Orchestrator surfaces the terminal/attention state and stops normal progression as
defined by the protocol. After `COMPLETE`, the ZCode Automation can be paused until the
next project.

## 8. Authorization and Execution Scope

Seeing `TO_ZCODE.md` is **not** authorization.

The Executor must run the exact claim helper command specified by the task. Only:

```text
CLAIM_ACQUIRED
exit code 0
```

authorizes stage execution.

Exit code 10 (`CLAIM_EXISTS`) or 11 (`ALREADY_PROCESSED`) means the wake should exit
quietly. Other claim errors fail closed according to the canonical Executor prompt.

Execution scope:

- never leave the configured Runtime Root;
- normal project work stays inside the active project;
- Runtime-core maintenance is allowed only when the current authorized task explicitly
  grants a narrow Runtime-root scope;
- never inspect or modify another Runtime installation.

## 9. Completion Rule

The Executor does not directly publish authoritative completion.

Legal path:

```text
durable stage outputs
-> project completion_staging\
-> python scripts\executor_completion.py commit --staging-dir <dir>
-> COMPLETION_COMMITTED
-> immediate Executor exit
```

The Runtime owns the authoritative completion ledger, consume/seal transitions, and the
root compatibility artifacts:

```text
SUPERVISOR_BRIEF.md
ZCODE_LAST_PROCESSED.txt
ZCODE_DONE.flag
```

The Executor must not create, repair, overwrite, or republish those files.

## 10. HUMAN_REVIEW

When the Supervisor raises `HUMAN_REVIEW`, automation pauses by design.

Decide offline, then:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare -ProjectId demo-001 `
  -DecisionFile my-decision.json -ReceiptFile receipt.json

# inspect the hash-bound receipt, then:
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply -ReceiptFile receipt.json
```

`Prepare` validates the decision against the pending lifecycle record and produces a
receipt; `Apply` commits it. Never edit live Runtime state by hand.

## 11. Stop

To request a hard stop:

```powershell
.\STOP_AGENT_SYSTEM.ps1
```

This creates `control\STOP`. A real STOP is checked before any new Supervisor invocation
and is terminal for normal automatic continuation; the Orchestrator does not silently
resume itself.

## 12. Runtime Layout

```text
orchestrator.py                         mechanical Orchestrator
START_PROJECT.ps1                      create/activate isolated project
START_AGENT_SYSTEM.ps1                 preflight + tests + start Orchestrator
STOP_AGENT_SYSTEM.ps1                  hard stop
RESUME_HUMAN_REVIEW.ps1                audited Human Review resume wrapper

control/
  CODEX_SUPERVISOR_RUNTIME.md           Supervisor policy
  ZCODE_SCHEDULED_AUTOMATION_PROMPT.md canonical permanent Executor prompt
  ACTIVE_PROJECT.json                  live pointer, generated at runtime (gitignored)

profiles/<TYPE>/                       project templates + FV policies
projects/<id>/                         isolated project data (gitignored)
handoff/                               claims + completion ledger (live data gitignored)

TO_ZCODE.md                            Runtime-generated Executor inbox (gitignored)
SUPERVISOR_BRIEF.md                    Runtime-generated completion view (gitignored)
ZCODE_LAST_PROCESSED.txt               Runtime-generated compatibility pointer (gitignored)
```
