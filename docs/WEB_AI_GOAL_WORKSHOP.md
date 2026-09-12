# Web AI Goal Workshop Contract (General Agent Runtime)

This is the canonical contract for using an external Web AI (such as
ChatGPT) to help a human write the Project Goal for a General Agent Runtime
project. The Runtime Web Console can generate a **copyable context pack**
and a **startup prompt** from this document plus the current Runtime facts;
the human pastes them into the Web AI themselves. The Console never embeds,
calls, scrapes, or automates any Web AI, and the pack never contains the
local Runtime Root path.

For the deeper human-facing workshop guide, read
[Project Goal Workshop](PROJECT_GOAL_WORKSHOP.md); for the mechanical
path from an approved Goal to a running project, read
[Start a New Project from an Idea](NEW_PROJECT_WORKFLOW.md).

## 1. Who is who (roles)

- **Human** — the project owner. Approves the Goal, makes every
  HUMAN_REVIEW decision, and is never a message bus between agents.
- **`PROJECT_GOAL.md`** — the canonical, human-approved Goal document
  imported at bootstrap. It defines the **destination** and the project
  constitution. Goal Anchor binds its exact bytes, so it is treated as
  immutable after import.
- **Codex Supervisor** — the Runtime-invoked planner, reviewer, and
  decision-maker. It decomposes the Goal into stages, reviews results, and
  decides when Final Verification is warranted. It never rewrites the Goal.
- **ZCode Executor** — the Scheduled Automation that performs exactly one
  mechanically authorized stage per wake, inside a fenced attempt
  workspace. It never chooses the next stage.
- **Runtime Core (Python)** — the deterministic protocol layer:
  authorization, claims, fencing, completion commit/consume/seal, and
  recovery. It is mechanical; it makes no research judgments.

## 2. What belongs in a Goal

A Goal should normally contain equivalent content for:

- **Objective** — the destination and why it matters.
- **Background / Context** — facts needed to interpret the request.
- **Available Inputs** — verified resources, distinguished from hoped-for
  access.
- **Required Deliverables** — concrete, inspectable artifacts.
- **Constraints** — scope, format, compatibility, schedule, policy.
- **Non-Goals** — tempting adjacent work that is out of scope.
- **Quality Requirements / Evidence and Reproducibility** — the proof
  standard instead of assuming a polished artifact is proof.
- **Acceptance Criteria** — observable conditions the Supervisor and the
  Final Verification gate can judge.
- **Risk Boundaries and Forbidden Actions** — what must never be done and
  what requires human review.

The mechanical validator in the Console requires meaningful Objective and
Deliverables-or-Acceptance content, rejects unreadable or oversized input,
and warns when Runtime routing internals appear.

## 3. What does NOT belong in a Goal

A Goal must not prescribe Runtime routing or protocol internals:

- MESSAGE_ID values, NONCE values, TASK_ID/STAGE_ID sequences;
- claim, fence, completion-ledger, or authorization mechanics;
- exact Supervisor-turn counts, exact Executor-task counts, or an exact
  authorization sequence;
- retry or recovery internals.

Decomposition, stage selection, and adaptive routing are the Supervisor's
job. A Goal that prescribes them produces warnings and brittle work.

## 4. ProjectType semantics

One of four profiles is selected at setup and validated against the
Runtime's profiles:

- **GENERAL** — open-ended goal-directed work under the default profile.
- **ACADEMIC_RESEARCH** — research questions, literature handling,
  citations, and explicit evidence standards.
- **SOFTWARE_ENGINEERING** — codebases, tests, reproducible builds, and
  engineering deliverables.
- **BUSINESS_RESEARCH** — market/competitive analysis and decision
  memos with stated confidence and sources.

## 5. Supported publication roots

All project outputs are published only inside the active project's:

- `workspace/`
- `evidence/`
- `reports/`

A Goal that requires outputs under `artifacts/`, `control/`, `handoff/`,
or `projects/` is **rejected before bootstrap** — `artifacts/` conflicts
with the EXECUTOR-FENCE-V1 publication protocol, and the others are
authoritative Runtime areas no project may write through a Goal. State
deliverables using the supported roots (for example
`reports/final-report.md`).

## 6. HUMAN_REVIEW

When automation reaches a boundary it must not cross alone, the Runtime
stops in HUMAN_REVIEW: a human decision — prepared and applied through an
audited receipt — is required before the Supervisor can continue. The Goal
should state which kinds of decisions must go this way rather than leaving
them to the Supervisor's judgment.

## 7. Final Verification

Before a project can be judged COMPLETE, the Runtime's Final Verification
gate independently verifies the decision-critical claims against the
standards the Goal and profile define. Goals should make success
inspectable: concrete acceptance criteria, cited evidence, reproducible
commands. Final Acceptance belongs to the Supervisor plus the mechanical
gate — never to the Executor, and never to the Goal text itself.

## 8. Historical correction

Published history is not rewritten: dispatches, completions, claims, and
ledger entries are append-only and remain inspectable after the fact.
Corrections happen as **new, later work** (for example a Supervisor STEER
or a new stage that supersedes an earlier artifact) — not by editing past
records. A materially changed objective normally requires a new Project,
not a Goal rewrite; later steering about method or quality belongs in
project memory, never in a Goal Anchor edit.

## 9. Current input inventory

The context pack generated by the Console includes the **current input
inventory** registered in the Setup wizard: the metadata (paths, kinds,
sizes) of the files and folders the human selected for this project.
Contents are never included. Referencing these inputs in the Goal keeps
the Goal honest about what actually exists.

## 10. How the human uses this document

1. In the Console's Project Setup wizard (Step 1), generate the context
   pack and startup prompt and copy both into your external Web AI.
2. Let the Web AI interview you, refine the idea, and produce one complete
   Markdown Goal following the rules above.
3. Approve the final text yourself, save it as a `.md` file, and load it
   in the wizard's Goal file picker; the deterministic validator checks it
   before anything is stored.
4. Continue through the wizard: inputs, Supervisor configuration draft,
   ZCode Automation acknowledgement, then readiness, preflight, and start.
