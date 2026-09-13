# QUICKSTART

This is the command-focused path for a clean General Agent Runtime V1.1 Runtime Root.
If you have only a rough idea, begin with
[Start a New Project from an Idea](NEW_PROJECT_WORKFLOW.md) and the
[Project Goal Workshop](PROJECT_GOAL_WORKSHOP.md), then return here with an approved
external Goal file.

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

## 1. Obtain and Open a Clean Runtime Root

A public user may use a clean clone or download as the Runtime Root. Do not use a
maintainer/source checkout for ordinary project work. For substantial independent work,
especially as a maintainer, a fresh Runtime Root derived from a stable release is the
recommended isolation and archival pattern. See the
[new-project workflow](NEW_PROJECT_WORKFLOW.md) for the maintainer worktree example and
the distinction between source checkout, Runtime, Project, and Goal.

Run commands from the repository root — the directory containing files such as:

```text
START_PROJECT.ps1
PREPARE_ZCODE_AUTOMATION.ps1
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

Keep the Runtime's ZCode Scheduled Automation absent or paused throughout setup. If this
is a new Runtime Root, configure its Automation after project creation and before
preflight. If it is already configured, keep it paused and reuse it; do not create
another Automation for the next Project.

## 2. Prepare and Approve the External Goal

Before starting the Runtime, make the project objective explicit. A capable web AI can
follow [Project Goal Workshop](PROJECT_GOAL_WORKSHOP.md) to turn a natural idea into a
deliberate Goal document.

A good Goal usually states:

- the real objective;
- relevant context and available inputs/data/files;
- required deliverables;
- constraints and non-goals;
- success / acceptance criteria;
- evidence or reproducibility expectations;
- important forbidden actions or risk boundaries.

Do not pre-script every stage. The Supervisor is responsible for decomposing the project
and revising the method as evidence arrives. The external Goal filename and Markdown
headings are not a mechanically required schema.

Revise the external file freely during design. Only continue after the human approves
it. `START_PROJECT.ps1` will import it as the canonical `PROJECT_GOAL.md` and bind its
SHA-256; treat that imported copy as immutable.

## 3. Create and Activate the Project

`START_PROJECT.ps1` is mechanical only: it validates the input, creates the project in a
staging directory, verifies it, atomically publishes the project directory, then
activates `control\ACTIVE_PROJECT.json`. It does **not** call a model or start the Runtime.

Using the approved external Goal file:

```powershell
.\START_PROJECT.ps1 `
  -ProjectId "demo-001" `
  -ProjectType "SOFTWARE_ENGINEERING" `
  -GoalFile "C:\goals\demo.md"
```

Valid `-ProjectType` values are exactly:

```text
GENERAL
ACADEMIC_RESEARCH
SOFTWARE_ENGINEERING
BUSINESS_RESEARCH
```

One Runtime can store many projects, but V1 has exactly **one active project at a time**.

## 4. Configure the Executor Automation Once per Runtime

If this Runtime Root has not been configured before, create one ZCode Scheduled
Automation for it. Otherwise reuse the existing Automation.

For the first Automation setup in this Runtime, run:

```powershell
.\PREPARE_ZCODE_AUTOMATION.ps1
```

The helper validates and renders the canonical prompt with this absolute Runtime Root,
then copies it to the clipboard. It does not configure ZCode, enable the Automation,
start the Runtime, or edit Runtime state. In ZCode:

- Workspace: the **Runtime Root**, not `projects\<project-id>\`
- Prompt: paste the rendered prompt from the clipboard
- Keep the Automation **paused during setup**

The Automation is Runtime-level. You do not create a new Automation when you create a
new project in the same Runtime, and you do not change its Workspace between projects.
A different Runtime Root needs a different Automation.

For a later sequential Project, keep the existing Automation paused while preparing and
activating the Project, run preflight, then enable the Automation and start the Runtime
as below. Do not rerun the helper unless you intentionally want to refresh or reinstall
the Automation prompt after a Runtime update.

See [ZCODE_SETUP.md](ZCODE_SETUP.md) for the complete setup contract.

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
canonical claim flow returns `CLAIM_ACQUIRED` / exit code 0; continuing work and
canonical publication also require the returned token and current fence authorization.

## 7. Let the Runtime Loop

Normal lifecycle:

```text
Supervisor
-> authorized Executor task
-> ZCode wake
-> claim; winner retains claim token
-> executor_fence.py prepare
-> execute exactly one stage in the attempt-local workspace
-> executor_fence.py check at required checkpoints
-> executor_fence.py publish canonical outputs
-> completion staging
-> executor_completion.py commit with claim token
-> COMPLETION_COMMITTED
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

For current fenced dispatches, the claim is permanent acquisition history, not lasting
write authority. The winner must retain the returned claim token, prepare an attempt-local
workspace, pass `executor_fence.py check` checkpoints, and publish supported canonical
outputs only through `executor_fence.py publish`. See
[Stale worker fencing](STALE_WORKER_FENCING.md) for the exact commands and failure rules.

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
attempt-local candidate outputs
-> executor_fence.py publish
-> project completion_staging\
-> python scripts\executor_completion.py commit --staging-dir <dir> --claim-token "<claim token>"
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
PREPARE_ZCODE_AUTOMATION.ps1           copy a Runtime-bound Executor prompt
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
