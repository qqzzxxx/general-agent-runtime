# QUICKSTART

Requirements: Windows with PowerShell 5+, Python 3.12+ on `PATH`, a Supervisor backend
CLI (current default: Codex CLI) on `PATH`, and ZCode Desktop Scheduled Automation (or
an equivalent scheduled automation) available for the Executor role.

## 1. Bootstrap the empty runtime

A fresh checkout contains no runtime state (all live state is gitignored on purpose).
Create the minimal bootstrap files once, from the runtime root:

```powershell
Copy-Item control\project_state.example.json control\project_state.json
Set-Content -Path ZCODE_LAST_PROCESSED.txt -Value 0
Set-Content -Path RESEARCH_STATE.md -Value "# Project memory (empty)"
```

Verify the runtime passes its mechanical checks:

```powershell
python scripts\preflight.py          # -> PREFLIGHT: OK (Mode: legacy, empty state)
python -m unittest discover -s scripts -p "test_*.py" -v
```

## 2. Create a project

Projects are isolated directories under `projects\<project-id>\`. `START_PROJECT.ps1`
performs mechanical creation only (validate → build in a staging directory → verify →
atomic rename → activate `control/ACTIVE_PROJECT.json` last). It never calls a model.

```powershell
# either inline goal text ...
.\START_PROJECT.ps1 -ProjectId demo-001 -ProjectType GENERAL -Goal "Analyze X and produce a decision report"

# ... or a goal file
.\START_PROJECT.ps1 -ProjectId demo-001 -ProjectType SOFTWARE_ENGINEERING -GoalFile C:\goals\demo.md
```

`-ProjectType` selects the profile: `GENERAL`, `SOFTWARE_ENGINEERING`,
`ACADEMIC_RESEARCH`, or `BUSINESS_RESEARCH`. The profile binds executor guidance and a
declarative Final Verification policy to the project.

## 3. Configure the Executor automation

Create a **ZCode Desktop Scheduled Automation** (or equivalent) that:

1. wakes periodically (the runtime budgets for an hourly cadence by default),
2. uses the runtime root as its working directory,
3. instructs the Executor model to follow `control/EXECUTOR_TASK_TEMPLATE.md`: read the
   inbox `TO_ZCODE.md`, claim via `scripts/executor_claim.py` (exit 0 = proceed,
   10/11 = exit quietly), execute the whole stage inside the project, then build a
   completion staging directory under the project's `completion_staging\` folder and
   commit it via `python scripts\executor_completion.py commit --staging-dir <dir>`
   (exit 0 = the Runtime commits the authoritative completion and generates the root
   artifacts; 10/11/12/13/14 = fail closed, publish nothing).

The claim protocol guarantees at-most-once execution even if the automation double-wakes.
The completion commit guarantees at-most-once authoritative completion per MESSAGE_ID;
root `SUPERVISOR_BRIEF.md` / `ZCODE_LAST_PROCESSED.txt` / `ZCODE_DONE.flag` are generated
by the Runtime itself and must never be written by the Executor.

## 4. Start / stop

```powershell
.\START_AGENT_SYSTEM.ps1
```

The launcher: (a) checks this runtime root's own lock — a live second orchestrator in
the *same* root blocks startup while *other* runtime roots are unaffected; (b) runs
preflight; (c) runs the mechanical regression suite; (d) starts `orchestrator.py`,
which drives the Supervisor loop.

```powershell
.\STOP_AGENT_SYSTEM.ps1   # creates control/STOP: terminal for new Supervisor calls
```

A real `control/STOP` is checked before any new Supervisor invocation and is a hard
stop — the Orchestrator will not resume on its own afterwards.

## 5. HUMAN_REVIEW

When the Supervisor raises `HUMAN_REVIEW`, automation pauses by design. Decide offline,
then:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare -ProjectId demo-001 `
    -DecisionFile my-decision.json -ReceiptFile receipt.json
# inspect the hash-bound receipt ...
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply -ReceiptFile receipt.json
```

`Prepare` validates the decision against the pending lifecycle record and produces a
receipt; `Apply` commits it. Never edit runtime state files by hand.

## 6. Runtime layout

```
orchestrator.py            mechanical Orchestrator (no model calls)
START_AGENT_SYSTEM.ps1     launcher (root-scoped instance check + preflight + tests)
control/                   policy docs + live control state (gitignored)
profiles/<TYPE>/           project templates incl. Final Verification policies
projects/<id>/             isolated project data (gitignored)
handoff/                   claims + completion ledger + consumed archive (gitignored)
TO_ZCODE.md                Executor inbox (created at runtime, gitignored)
SUPERVISOR_BRIEF.md        Runtime-generated receipt view (gitignored)
```
