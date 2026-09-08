# Start a New Project from an Idea

This is the canonical onboarding path for a human who has a project idea and for the
web AI helping turn that idea into a running General Agent Runtime project. It connects
project design to the mechanical first-run steps without making the human a message bus
between agents.

For command-focused setup, use [QUICKSTART](QUICKSTART.md). For deeper operation and
steering, use the [Operator Playbook](OPERATOR_PLAYBOOK.md).

## 1. Know which thing you are working with

These names are deliberately distinct:

- **Source / maintainer checkout** — the repository used to maintain and release the
  Runtime itself. It is a source template, not the preferred place for ordinary project
  work.
- **Runtime Root** — one clean installation or checkout from which the Orchestrator
  runs. It contains `START_PROJECT.ps1`, `control/`, `profiles/`, `projects/`, and
  `scripts/`, and owns its live protocol state.
- **Project** — one unit of work under `projects/<project-id>/`. V1 can retain multiple
  projects in a Runtime, but exactly one is active at a time.
- **External Goal file** — the human-approved design document created outside the
  project before launch. It remains freely editable during the workshop.
- **Canonical `PROJECT_GOAL.md`** — the copy imported into the Project by
  `START_PROJECT.ps1`. Goal Anchor binds its exact bytes, so it is treated as immutable
  after import.
- **Web AI** — a project-design and setup assistant outside the autonomous Runtime loop.
- **Codex Supervisor** — the Runtime-invoked planner, reviewer, and decision-maker.
- **ZCode Executor** — the Scheduled Automation that performs exactly one mechanically
  authorized stage per wake.
- **Python Orchestrator** — the deterministic protocol layer that invokes the Supervisor,
  validates and authorizes dispatches, enforces lifecycle rules, and consumes/seals
  Executor completions.

## 2. Choose a clean Runtime Root

Do not run ordinary projects directly in the maintainer/source checkout. A public user
may make a clean clone or download and use that clean checkout as the Runtime Root. A
maintainer or anyone wanting stronger isolation should derive a fresh Runtime Root from
a stable release or tag for each substantial independent project.

For example, a Windows maintainer can create a detached worktree from the V1.1.1 tag:

```powershell
git worktree add --detach "C:\general-agent-runtime-research-001" v1.1.1
```

That worktree pattern is recommended for maintainers, not required for public users. A
clean clone, archive download, or another clean installation method is also valid.

One Runtime can contain multiple projects sequentially because V1 has one active project
at a time. Even so, a separate Runtime Root per substantial independent project is a
useful isolation and archival pattern, especially for the maintainer. The Automation
rule follows the Runtime boundary:

- projects in the **same Runtime Root** reuse that Runtime's one ZCode Automation;
- each **different Runtime Root** needs its own ZCode Automation, bound to that root.

Never point an Automation at one Runtime while allowing it to inspect a sibling Runtime.

## 3. Use the web AI for project design

The web AI helps the human clarify a rough idea, learn the relevant Runtime concepts,
produce a deliberate external Goal file, and guide setup if requested. It is outside the
autonomous Runtime loop: it is not the Supervisor, not the Executor, and not the message
bus between Codex and ZCode.

Start a capable web AI with this copyable prompt:

```text
You are helping me start a new project with General Agent Runtime.
First read AI_BOOTSTRAP.md and follow its routing rules.
Also read docs/PROJECT_GOAL_WORKSHOP.md.
Do not start the Runtime yet.
First help me turn my project idea into a deliberate external Goal file.
Do not pre-script every execution stage; the Runtime Supervisor will choose and revise
the route.
```

Then give the AI the idea naturally, with any files, constraints, resources, or concerns
already known. Follow the [Project Goal Workshop](PROJECT_GOAL_WORKSHOP.md) until the
human approves the external Goal file.

## 4. Why the three AIs do not need repeated explanations

You do **not** have to explain the dual-Agent system separately to every AI. Each role
has a canonical context route:

1. **Web AI / project-design assistant** reads `AI_BOOTSTRAP.md`, then
   `docs/PROJECT_GOAL_WORKSHOP.md` and this workflow as routed.
2. **Codex Supervisor** is invoked by the Runtime with
   `control/CODEX_SUPERVISOR_RUNTIME.md` plus current project and Runtime context. The
   human does not restate the system for every project.
3. **ZCode Executor** runs from one Runtime-level Scheduled Automation whose permanent
   prompt is copied from `control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md` with
   `<RUNTIME_ROOT>` bound to that Runtime. The human does not forward individual
   Supervisor tasks.

The Python Orchestrator remains the deterministic protocol, authorization, and lifecycle
layer between Supervisor and Executor.

## 5. Approve and import the Goal

The external Goal defines the destination and project constitution, not a fixed stage
plan. Revise it freely during the workshop. Only after the human approves it should it be
given to `START_PROJECT.ps1`.

Choose `-ProjectType` for the work because it selects the Runtime Profile, including
project guidance and the applicable Final Verification policy:

- `GENERAL` — work that does not fit one of the specialized Profiles;
- `ACADEMIC_RESEARCH` — research-oriented work where evidence, reproducibility, and
  research-specific verification matter;
- `SOFTWARE_ENGINEERING` — software implementation, engineering, debugging, validation,
  and testing;
- `BUSINESS_RESEARCH` — business, market, strategy, or decision-oriented research.

These four values are the complete supported set. The following is an academic-research
example:

```powershell
.\START_PROJECT.ps1 `
  -ProjectId "research-001" `
  -ProjectType "ACADEMIC_RESEARCH" `
  -GoalFile "C:\goals\research-001.md"
```

For a software project, use the same form with, for example,
`-ProjectId "software-001" -ProjectType "SOFTWARE_ENGINEERING"` and the approved software
Goal file. See [QUICKSTART](QUICKSTART.md) for the command-focused setup details.

The script validates the inputs, creates and verifies an isolated Project, imports the
Goal as `projects/<project-id>/PROJECT_GOAL.md`, binds Goal Anchor, and activates the
Project. It does not call a model or start the autonomous loop.

After import, do not rewrite the canonical Goal. Later feedback about method, quality,
presentation, or execution direction belongs in the Project's `RESEARCH_STATE.md` as
described in the [Operator Playbook](OPERATOR_PLAYBOOK.md). A materially different
objective normally belongs in a new Project.

## 6. Configure and start the Runtime

Use this normal sequence:

0. Obtain or create a clean Runtime Root.
1. Keep its ZCode Scheduled Automation absent or paused during setup.
2. Let a web AI read `AI_BOOTSTRAP.md` and the Goal Workshop.
3. Discuss the idea and approve an external Goal file.
4. Run `START_PROJECT.ps1` with that Goal file.
5. If this Runtime has not been configured before, create its one ZCode Automation using
   [ZCODE_SETUP](ZCODE_SETUP.md). Do not repeat this for every Project in the same Runtime.
6. Run preflight:

   ```powershell
   python .\scripts\preflight.py
   ```

7. Enable the ZCode Automation.
8. Start the Orchestrator:

   ```powershell
   .\START_AGENT_SYSTEM.ps1
   ```

9. Stop manually relaying tasks and let the Runtime loop.
10. Inspect milestones and add steering feedback only when useful.
11. If the Runtime enters `HUMAN_REVIEW`, respond through the audited prepare/apply flow.
12. At `COMPLETE`, inspect and archive the outputs as needed, then pause the Automation
    while the Runtime is idle.

`START_AGENT_SYSTEM.ps1` also performs its own launch checks. The explicit preflight is
valuable during initial setup because it catches configuration and project-state errors
before automation is enabled.

For a later sequential Project in an already-configured Runtime, reuse the existing
Automation. Keep it paused while preparing and activating the new Project, run preflight,
then enable it and run `START_AGENT_SYSTEM.ps1`. Do not create another Automation unless
you create another Runtime Root.

## 7. What happens after launch

For a current fenced dispatch, the normal stage path is:

```text
Supervisor dispatch
-> Runtime validation and authorization
-> Executor claim; winner receives a claim token
-> executor_fence.py prepare
-> work in the attempt-local candidate workspace
-> executor_fence.py check at required checkpoints
-> executor_fence.py publish for canonical outputs
-> completion staging
-> executor_completion.py commit --staging-dir "<staging dir>" --claim-token "<claim token>"
-> COMPLETION_COMMITTED
-> Executor exits immediately
-> Orchestrator consumes and seals
-> Supervisor reviews and chooses the next state
```

A permanent claim records acquisition history; it is not lasting write authority.
Canonical project outputs are published only through `executor_fence.py`, not written
directly by the Executor. See [Stale worker fencing](STALE_WORKER_FENCING.md) for the
complete EXECUTOR-FENCE-V1 contract and crash/recovery semantics.

The autonomous loop continues until the Supervisor and mechanical gates choose another
stage, `HUMAN_REVIEW`, `BLOCKED`, `STOPPED`, or `COMPLETE`. The human may inspect outputs
and steer, but should not edit task wires, claims, authorization state, completion
ledgers, Goal Anchor data, or lifecycle JSON by hand.
