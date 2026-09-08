# General Agent Runtime

An unattended dual-Agent runtime in which a **high-reasoning Supervisor**, a
**high-throughput Executor**, and a **deterministic Python Orchestrator** cooperate on
multi-stage research and engineering projects through plain shared files — with no human
acting as the message bus.

The architecture separates Supervisor, Executor, and Orchestrator roles at the
protocol level, but this release is **not plug-and-play model-agnostic**. The current
Supervisor integration is implemented specifically for Codex CLI and pins GPT-5.6 Sol.
The validated V1 Supervisor configuration is **Codex CLI + GPT-5.6 Sol + high reasoning**.
Other Supervisor models/backends are not part of the validated V1 release.

| Role | Current implementation | Portability |
|---|---|---|
| Supervisor (planning, acceptance, redirection, stop) | Codex CLI / GPT-5.6 Sol (`codex exec`) | replacing it requires code-level backend/invocation adaptation |
| Executor (bulk implementation, evidence production) | ZCode Desktop Scheduled Automation / GLM | an equivalent scheduled automation can be adapted if it fully obeys the Runtime wire, claim, scope, and completion protocols |
| Orchestrator (mechanical scheduling, authorization, gating) | Python; mechanically invokes the Supervisor backend | fixed by design |

The shared-file protocol is vendor-neutral in concept, especially at the Executor
boundary, but this repository's Supervisor invocation is Codex-specific today. Supporting
another Supervisor backend requires adapting the Runtime invocation layer rather than
changing a single configuration value.

## What problem it solves

Handing a long, multi-stage project to "an AI agent" fails in predictable ways when a
single model does everything: it drifts, invents results, burns tokens in loops, and
cannot be audited. General Agent Runtime separates the concerns instead:

- the **Supervisor** decides *what should happen next and whether it was done right*
  (high reasoning effort, expensive, invoked rarely);
- the **Executor** does *the work of one authorized stage at a time* (cheap, fast,
  invoked once per stage);
- the **Orchestrator** enforces *mechanically verifiable rules* between them so neither
  agent can skip, replay, forge, or partially consume a handoff.

All coordination happens through files in the Runtime Root. Every handoff has an
auditable identity and authorization trail.

## Start a new project from an idea

The Runtime is autonomous **after** a project starts, but it cannot infer the project you
meant to ask for. The quality of the canonical `PROJECT_GOAL.md` is therefore a major
upper bound on the quality of the resulting work.

A capable web AI can guide the design phase without becoming part of the Runtime loop.
Start with [Start a New Project from an Idea](docs/NEW_PROJECT_WORKFLOW.md) and use the
[Project Goal Workshop](docs/PROJECT_GOAL_WORKSHOP.md) to turn the rough idea into an
approved external Goal file. A useful opening prompt is:

```text
Read AI_BOOTSTRAP.md. I want to start a new project.
```

The short path is:

1. discuss the project with a strong reasoning model until the objective and constraints
   are clear;
2. write a Goal file that states the real objective, available inputs, required
   deliverables, constraints, success criteria, evidence expectations, and important
   forbidden actions;
3. give that Goal file to `START_PROJECT.ps1`;
4. let the Supervisor decide the stage plan and the Executor carry it out.

Do **not** try to pre-script every stage in the Goal. Define the destination and the
rules of the project; let the Supervisor choose and revise the route.

`START_PROJECT.ps1` imports the supplied Goal as the project's canonical
`PROJECT_GOAL.md` and Goal Anchor binds its exact SHA-256. Treat it as immutable after
project creation: changing its bytes later fails closed instead of silently changing the
project constitution.

The web AI is only the pre-launch design/setup assistant. After launch, the Codex
Supervisor, ZCode Executor, and Python Orchestrator use their own canonical context; the
human does not explain the protocol to each role or relay their messages.

## How one stage flows

```text
Python Orchestrator
  -> invokes Supervisor CLI with a compressed context prompt
  -> Supervisor atomically publishes a candidate TO_ZCODE.md dispatch
  -> Orchestrator mechanically validates the candidate
  -> Orchestrator registers authorization bound to the exact task identity + inbox SHA-256
  -> Executor automation wakes
  -> Executor claims via scripts/executor_claim.py
     (the winner receives and retains its claim token)
  -> Executor runs executor_fence.py prepare
  -> Executor performs exactly the authorized stage in its attempt-local workspace
     with executor_fence.py check checkpoints
     - normal project work stays inside the active project
     - Runtime-core maintenance is allowed only when the current authorized task
       explicitly permits the exact Runtime-root scope
  -> Executor publishes canonical outputs only through executor_fence.py publish
  -> Executor stages a candidate completion and commits it via
     scripts/executor_completion.py commit --staging-dir "<staging dir>" --claim-token "<claim token>"
     (COMPLETION_COMMITTED / exit 0 is the only legal completion publication)
  -> Executor immediately exits
  -> Runtime records the authoritative completion in handoff/completion_ledger/
  -> Runtime generates SUPERVISOR_BRIEF.md, ZCODE_LAST_PROCESSED.txt, ZCODE_DONE.flag
  -> Orchestrator consumes the ledger-backed completion exactly once and seals it
  -> Supervisor reviews the result and chooses the next task, Final Verification,
     HUMAN_REVIEW, STOP, or COMPLETE
```

The operating contracts live in:

- [control/CODEX_SUPERVISOR_RUNTIME.md](control/CODEX_SUPERVISOR_RUNTIME.md) — Supervisor policy;
- [control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md](control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md) — canonical permanent Executor automation prompt;
- [control/EXECUTOR_TASK_TEMPLATE.md](control/EXECUTOR_TASK_TEMPLATE.md) — task wire/schema guidance;
- [handoff/PROTOCOL.md](handoff/PROTOCOL.md) — handoff protocol.

## Why claim / authorization / completion-seal exist

Three failure classes motivated the design, and all are enforced mechanically:

1. **Acquisition history and fenced authority.** A scheduled automation may wake twice
   for the same inbox,
   or two Executor instances may race. `executor_claim.py` takes a permanent,
   filesystem-guaranteed claim on the exact `MESSAGE_ID + NONCE` under
   `handoff/executor_claims/`; the second contender exits quietly (exit 10 = claim
   exists, exit 11 = already processed). The winning worker receives a claim token, but
   the permanent claim is acquisition history—not indefinite write authority. Live
   authority is rechecked by `executor_fence.py`, and canonical outputs are published
   only through that helper. Replays, expired attempts, and stale workers are rejected.
2. **No unauthorized dispatch.** Seeing `TO_ZCODE.md` is not authorization. The claim
   helper validates the exact authorized task identity and inbox snapshot. A visible but
   unauthorized, stale, or modified inbox fails closed.
3. **At-most-once completion.** A claim does not by itself prove how many times a
   finished attempt may republish its result. `executor_completion.py` owns the
   authoritative completion commit: one ledger entry per `MESSAGE_ID` under
   `handoff/completion_ledger/`, monotonic `COMPLETION_COMMITTED -> COMPLETION_CONSUMED
   -> COMPLETION_SEALED`, never reversible. A second commit is refused (exit 10), a
   consumed/sealed identity can never drive the lifecycle again (exit 11), and the
   Orchestrator consumes only ledger-backed completions.

Additional reliability mechanisms include atomic publication, stale-lock recovery only
for provably dead owners, Goal Anchor checks before every Supervisor turn and dispatch,
Final Verification gating before `COMPLETE`, bounded retries/budgets, fail-closed unknown
states, and hard stop conditions.

## Source checkout / Runtime / Project / Goal / Profile

- **Source / maintainer checkout** — the repository used to develop and release the
  Runtime. It is a source template, not the preferred location for ordinary projects.
- **Runtime Root** — one clean installation of this repository. It owns the control plane, wire
  files, claims, completion ledger, logs, and exactly one active Orchestrator process.
- **Project** — one isolated directory under `projects/<project-id>/` containing its
  canonical Goal, state, workspace, evidence, and reports.
- **External Goal file** — the human-approved project constitution supplied to
  `START_PROJECT.ps1`; it is imported as the canonical, Goal-Anchor-bound
  `PROJECT_GOAL.md`.
- **Active Project** — one Runtime may contain many projects, but V1 activates exactly
  one at a time through `control/ACTIVE_PROJECT.json`.
- **Profile** — a project template in `profiles/`: `GENERAL`,
  `SOFTWARE_ENGINEERING`, `ACADEMIC_RESEARCH`, or `BUSINESS_RESEARCH`. The profile binds
  Executor guidance and a declarative Final Verification policy to the project type.

The ZCode Automation is **Runtime-level**, not Project-level: its Workspace remains the
Runtime Root and its permanent prompt does not change when you switch projects.

A public user may use a clean clone/download as the Runtime Root. For stronger isolation,
especially in maintainer work, use a fresh Runtime Root per substantial independent
project. Projects inside one Runtime reuse its Automation; each separate Runtime Root
requires its own Automation. See the
[new-project workflow](docs/NEW_PROJECT_WORKFLOW.md) for the recommended release-worktree
pattern and alternatives.

## Quick start

If you have only an idea, start with
[docs/NEW_PROJECT_WORKFLOW.md](docs/NEW_PROJECT_WORKFLOW.md). See
[docs/QUICKSTART.md](docs/QUICKSTART.md) for the command-focused walkthrough and
[docs/ZCODE_SETUP.md](docs/ZCODE_SETUP.md) for the Executor setup.

A fresh clone needs **no manual bootstrap state files**.

```powershell
# 1) create an isolated project from a Goal file
.\START_PROJECT.ps1 `
  -ProjectId "demo-001" `
  -ProjectType "GENERAL" `
  -GoalFile "C:\goals\demo.md"

# 2) optional explicit check (START_AGENT_SYSTEM also runs preflight)
python .\scripts\preflight.py
# -> PREFLIGHT: OK
# -> Mode: isolated

# 3) enable the already-configured ZCode Scheduled Automation, then start the Orchestrator
.\START_AGENT_SYSTEM.ps1
```

After launch, no manual Supervisor/Executor handoff is required. The Supervisor publishes
Executor tasks when needed, and the already-enabled Scheduled Automation picks them up on
a later wake. A wake by itself never authorizes work: `CLAIM_ACQUIRED` / exit code 0 is
necessary to begin, and the returned token plus successful fence checks govern continuing
work and publication.

There is intentionally **no** first-run instruction to create
`control\project_state.json`, root `RESEARCH_STATE.md`, or
`ZCODE_LAST_PROCESSED.txt`. Those are legacy/runtime artifacts, not fresh-clone
prerequisites.

Instance isolation is per Runtime Root: one Runtime Root runs at most one Orchestrator,
while separate Runtime Roots can run independently.

## Human review (HUMAN_REVIEW)

When a stage requires input only a human can provide — ambiguous objectives, physical
actions, credentials, or acceptance of irreversible risk — the Supervisor sets the
project to `HUMAN_REVIEW` instead of guessing. The Orchestrator stops dispatching and
surfaces the reason.

The human decides offline, then uses the audited wrapper:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare -ProjectId demo-001 `
  -DecisionFile decision.json -ReceiptFile receipt.json

# inspect the hash-bound receipt, then:
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply -ReceiptFile receipt.json
```

Never edit `ACTIVE_PROJECT.json`, `project_state.json`, Goal Anchor bindings, completion
ledgers, or other live control state by hand.

## Final Verification

`COMPLETE` is never a Supervisor opinion alone. Each profile ships a declarative Final
Verification policy (`profiles/<type>/FINAL_VERIFICATION_POLICY.json`) that the
Orchestrator enforces mechanically. Decision-critical claims must be backed by the
required independent evidence; a malformed or insufficient verification receipt is
rejected rather than treated as "close enough."

Destructive verification belongs in an isolated external sandbox, not in the live
Runtime. See [docs/FV_SANDBOX_ISOLATION_V1.md](docs/FV_SANDBOX_ISOLATION_V1.md).

## Console output (optional Rich)

The Orchestrator renders console output through a presentation-only layer. With the
optional [`rich`](https://github.com/Textualize/rich) package installed and an
interactive terminal, you get concise Supervisor decision panels, lifecycle events, a
non-spamming `WAITING_EXECUTOR` spinner, and distinct terminal / `HUMAN_REVIEW` /
unrecoverable-error panels.

Without Rich — or on a pipe/non-interactive session — the Runtime falls back to plain
text. Durable event logging remains in `logs/orchestrator.jsonl`, and presentation
failures cannot change a protocol outcome.

Set `ORCHESTRATOR_CONSOLE` to `auto` (default), `rich`, `plain`, or `off`.

## Project goal anchoring (GOAL-ANCHOR-V1)

Every isolated project binds its canonical Goal at bootstrap:
`scripts/start_project.py` persists `project_state.goal_anchor` with the canonical Goal
path and byte-exact SHA-256 of `PROJECT_GOAL.md`. Before every Supervisor turn — and
again before any dispatch is authorized — the Orchestrator re-reads the Goal and checks
the binding. A second Runtime-owned binding in `control/orchestrator_runtime.json`
detects silent self-consistent rewrites.

A missing, unreadable, malformed, path-escaping, unbound, or hash-mismatched Goal fails
closed into `HUMAN_REVIEW` with no authorized Executor task. The Runtime never silently
rebinds the Goal. See [docs/GOAL_ANCHOR_V1.md](docs/GOAL_ANCHOR_V1.md).

## Safety boundaries

- The Executor must never operate outside its configured Runtime Root.
- Normal project work is scoped to the active project. A Runtime-core modification is
  allowed only when the current mechanically authorized task explicitly permits that
  narrow Runtime-root scope; otherwise Runtime Core is out of scope.
- Seeing `TO_ZCODE.md` never authorizes work. Successful claim acquisition is necessary
  to begin, but continuing work and canonical publication require the returned token and
  current `executor_fence.py` authorization.
- The Executor never directly writes authoritative root completion artifacts.
- Terminal and error states are surfaced to the human; unattended operation never
  auto-starts a new project.
- Runtime state, project data, logs, claims, Human Review receipts, and wire files are
  gitignored: a repository checkout does not contain prior execution history.
- Multiple Runtime Roots are isolated from one another. One Automation must not inspect
  or modify sibling Runtime installations.

See [docs/SECURITY.md](docs/SECURITY.md) for the threat model and isolation rules.

## Requirements

- Windows with PowerShell 5+
- Python 3.12+ on `PATH`
- Codex CLI on `PATH` (required by the current Supervisor implementation)
- ZCode Desktop Scheduled Automation (or an equivalent Executor implementation that conforms to the Runtime protocol)
- optional: Python package `rich` for enhanced interactive console rendering

## Documentation

### AI / Maintainer context

If you are an AI assistant or maintainer helping operate, debug, recover, or extend
this Runtime, start with:

- [AI_BOOTSTRAP.md](AI_BOOTSTRAP.md) — context router and source-of-truth rules
- [docs/NEW_PROJECT_WORKFLOW.md](docs/NEW_PROJECT_WORKFLOW.md) — idea-to-running-project onboarding
- [docs/PROJECT_GOAL_WORKSHOP.md](docs/PROJECT_GOAL_WORKSHOP.md) — human + web AI Goal design
- [docs/RUNTIME_SYSTEM_HANDBOOK.md](docs/RUNTIME_SYSTEM_HANDBOOK.md) — deep system mental model
- [docs/OPERATOR_PLAYBOOK.md](docs/OPERATOR_PLAYBOOK.md) — normal human operation and steering
- [docs/INCIDENT_RUNBOOK.md](docs/INCIDENT_RUNBOOK.md) — failure recovery and incident procedures
- [docs/CHANGE_POLICY.md](docs/CHANGE_POLICY.md) — Runtime Core change and release discipline

The goal of this Maintainer Knowledge Pack is to make repository context sufficient for
a capable new AI to assist with the Runtime without depending on a particular historical
chat conversation.

- [docs/QUICKSTART.md](docs/QUICKSTART.md) — first project, first run, normal lifecycle
- [docs/ZCODE_SETUP.md](docs/ZCODE_SETUP.md) — canonical ZCode Scheduled Automation setup
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, wire format, identity model
- [docs/SECURITY.md](docs/SECURITY.md) — threat model, isolation, what never gets committed
- [docs/GOAL_ANCHOR_V1.md](docs/GOAL_ANCHOR_V1.md) — immutable Goal binding
- [docs/FV_SANDBOX_ISOLATION_V1.md](docs/FV_SANDBOX_ISOLATION_V1.md) — safe destructive verification

## Status

General Agent Runtime **v1.1.0** is the current public release. The Runtime was
validated with the regression suite and with a clean-clone onboarding smoke path:

```text
clean clone -> START_PROJECT.ps1 -> preflight -> PREFLIGHT: OK (Mode: isolated)
```

Run the full regression suite from the Runtime Root with:

```powershell
python -m unittest discover -s scripts -p "test_*.py"
```

## License

[MIT](LICENSE)
