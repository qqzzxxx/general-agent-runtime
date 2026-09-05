# General Agent Runtime

An unattended dual-Agent runtime in which a **high-reasoning Supervisor**, a
**high-throughput Executor**, and a **deterministic Python Orchestrator** cooperate on
multi-stage research and engineering projects through plain shared files — with no human
acting as the message bus.

The architecture is model-agnostic. The current implementation uses:

| Role | Current backend | Replaced by configuring |
|---|---|---|
| Supervisor (planning, acceptance, redirection, stop) | Codex CLI / GPT (`codex exec`) | any CLI agent with comparable batch invocation |
| Executor (bulk implementation, evidence production) | ZCode Desktop Scheduled Automation / GLM | any scheduled automation that can read/write the wire files |
| Orchestrator (mechanical scheduling, authorization, gating) | Python, no model calls | fixed by design |

Nothing in the protocol depends on a particular vendor: the Orchestrator only requires
that the Supervisor backend can be invoked from a shell with a text prompt, and that the
Executor backend wakes periodically, reads the inbox file, executes, and publishes a
receipt.

## What problem it solves

Handing a long, multi-stage project to "an AI agent" fails in predictable ways when a
single model does everything: it drifts, invents results, burns tokens in loops, and
cannot be audited. General Agent Runtime separates the concerns instead:

- the **Supervisor** decides *what should happen next and whether it was done right*
  (high reasoning effort, expensive, invoked rarely);
- the **Executor** does *the work of one stage at a time* (cheap, fast, invoked once per
  stage);
- the **Orchestrator** enforces *mechanically verifiable rules* between them so neither
  agent can skip, replay, forge, or partially consume a handoff.

All coordination happens through files in the runtime root — every handoff is an
auditable artifact with a cryptographic identity binding.

## How one stage flows

```
Python Orchestrator
  -> invokes Supervisor CLI with a compressed context prompt
  -> Supervisor writes the next dispatch to the inbox TO_ZCODE.md
  -> Orchestrator mechanically validates the dispatch and binds an authorization record
     (MESSAGE_ID / TASK_ID / STAGE_ID / ATTEMPT / NONCE + inbox SHA-256) in
     control/orchestrator_runtime.json
  -> Executor automation wakes, claims the task via scripts/executor_claim.py
     (CLAIM_ACQUIRED is the only permission to start work)
  -> Executor performs the whole stage, writes deliverables + evidence into the project
  -> Executor stages a candidate completion and commits it via
     scripts/executor_completion.py (COMPLETION_COMMITTED is the only permission to finish)
  -> the Runtime durably records the authoritative completion in handoff/completion_ledger/
     and itself generates SUPERVISOR_BRIEF.md, ZCODE_LAST_PROCESSED.txt, ZCODE_DONE.flag
  -> Orchestrator consumes the ledger-backed completion exactly once, seals the identity,
     clears the wake hint, and invokes the Supervisor again for review / next dispatch
```

The complete operating contract lives in
[control/CODEX_SUPERVISOR_RUNTIME.md](control/CODEX_SUPERVISOR_RUNTIME.md) and
[control/EXECUTOR_TASK_TEMPLATE.md](control/EXECUTOR_TASK_TEMPLATE.md); the wire-level
sequence is described in [handoff/PROTOCOL.md](handoff/PROTOCOL.md).

## Why claim / authorization / completion-seal exist

Three failure classes motivated the design, and all are enforced mechanically:

1. **At-most-once execution.** A scheduled automation may wake twice for the same inbox,
   or two Executor instances may race. `executor_claim.py` takes a permanent,
   filesystem-guaranteed claim on the exact `MESSAGE_ID + NONCE` under
   `handoff/executor_claims/`; the second contender exits quietly (exit 10 = claim
   exists, exit 11 = already processed). Replays and stale re-issues are rejected.
2. **No unauthorized dispatch.** A file appearing in the inbox is not authorization.
   The Orchestrator publishes a dispatch *before* writing the authorization record, and
   binds it to the exact inbox snapshot hash. A crash between the two leaves a visible
   but unclaimable task — claim fails closed. Executor-side modifications of the inbox
   never match the recorded hash.
3. **At-most-once completion.** A claim does not by itself prove how many times a
   finished attempt may republish its result. `executor_completion.py` therefore owns
   the authoritative completion commit: one ledger entry per `MESSAGE_ID` under
   `handoff/completion_ledger/`, monotonic `COMPLETION_COMMITTED -> COMPLETION_CONSUMED
   -> COMPLETION_SEALED`, never reversible. A second commit is refused (exit 10), a
   consumed/sealed identity can never drive the lifecycle again (exit 11), and the
   Orchestrator consumes only ledger-backed completions — raw root artifacts, rewritten
   briefs, and late replays are quarantined and audited instead.

Additional reliability mechanisms (all preserved by the regression suite): atomic
publication via temp-file rename, stale-lock reclaim only for provably dead owners,
Final Verification gating before any `COMPLETE`, terminal-state supervision, budget and
retry ceilings, and hard stop conditions.

## Project / Profile / Runtime

- **Runtime** — this repository. Owns global state (control plane, wire files, claims,
  archive) and exactly one Orchestrator process per runtime root.
- **Project** — one directory under `projects/<project-id>/` with its own goal, state,
  code, evidence, and reports. Projects are isolated: the Orchestrator scopes all
  read/write paths to the active project. The active project is named by
  `control/ACTIVE_PROJECT.json` (the activation commit point of project creation).
- **Profile** — a project template in `profiles/` (`GENERAL`, `SOFTWARE_ENGINEERING`,
  `ACADEMIC_RESEARCH`, `BUSINESS_RESEARCH`) that binds a goal parameterization, executor
  guidance, and a declarative Final Verification policy to the project type.

## Quick start

See [docs/QUICKSTART.md](docs/QUICKSTART.md) for the full walkthrough. In short:

```powershell
# one-time bootstrap of the empty runtime state (see QUICKSTART for details)
Copy-Item control\project_state.example.json control\project_state.json
Set-Content ZCODE_LAST_PROCESSED.txt -Value 0
Set-Content RESEARCH_STATE.md -Value "# Project memory (empty)"

# create an isolated project from a profile
.\START_PROJECT.ps1 -ProjectId demo-001 -ProjectType GENERAL -GoalFile <your-goal.md>

# enable a ZCode Desktop Scheduled Automation pointing at this runtime root, then
.\START_AGENT_SYSTEM.ps1    # preflight + regression tests + orchestrator
.\STOP_AGENT_SYSTEM.ps1     # write control/STOP (terminal for new Supervisor calls)
```

Instance isolation is per runtime root: one runtime root runs at most one Orchestrator
(enforced by `control/.orchestrator.lock` + pid verification, fail closed), while
several independent runtime roots on the same machine can run in parallel.

## Human review (HUMAN_REVIEW)

When a stage requires input only a human can provide — ambiguous objectives, physical
actions, credentials, or acceptance of irreversible risk — the Supervisor sets the
project to `HUMAN_REVIEW` instead of guessing. The Orchestrator surfaces a terminal
notification and stops dispatching. The human decides offline, then applies the decision
with the audited wrapper:

```powershell
.\RESUME_HUMAN_REVIEW.ps1 -Mode Prepare -ProjectId demo-001 -DecisionFile decision.json -ReceiptFile receipt.json
# inspect the hash-bound receipt, then:
.\RESUME_HUMAN_REVIEW.ps1 -Mode Apply -ReceiptFile receipt.json
```

A consumed receipt can never silently return to pending or regain authorization
semantics; the ledger is validated on every later startup.

## Final Verification

`COMPLETE` is never a Supervisor opinion alone. Each profile ships a declarative Final
Verification policy (`profiles/<type>/FINAL_VERIFICATION_POLICY.json`) that the
Orchestrator enforces mechanically: before a terminal `COMPLETE` is accepted, the
project must produce independently re-runnable evidence (fresh execution runs, receipts,
claim counts) that satisfies the policy's claims — otherwise the gate blocks the
completion attempt and records the block reason.

## Safety boundaries

- The Executor works only inside the active project root; the Orchestrator validates
  every path against the runtime's own `projects` directory.
- No GUI automation, no browser control, no Computer Use, no headless agent CLI is part
  of the protocol — only shared files and shell invocation.
- Terminal and error states are surfaced to the human via the console and
  `control/USER_ATTENTION.json`; unattended operation never auto-starts a new project.
- Multiple runtime roots on one machine are isolated from each other (locks, state,
  stop flags, and claims are all root-scoped).
- Runtime state, project data, logs, and claims are gitignored: a repository checkout
  never contains execution history (see [docs/SECURITY.md](docs/SECURITY.md)).

## Requirements

- Windows (PowerShell 5+), Python 3.12+
- A Supervisor backend CLI on `PATH` (current default: Codex CLI)
- ZCode Desktop Scheduled Automation (or equivalent) configured to wake on this runtime
  root and execute the inbox task with an Executor-capable model

## Documentation

- [docs/QUICKSTART.md](docs/QUICKSTART.md) — bootstrap, first project, automation setup
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, wire format, identity model
- [docs/SECURITY.md](docs/SECURITY.md) — threat model, isolation, what never gets committed

## Status

`v0.1` open-source release of a runtime that has been operating unattended on real
projects. The protocol and reliability mechanisms are frozen; see the regression suite
under `scripts/test_*.py` (run with `python -m unittest discover -s scripts -p "test_*.py"`,
from the runtime root).

## License

[MIT](LICENSE)
