# Project Goal Workshop

This workshop is for the human and a capable web AI **before** `START_PROJECT.ps1`.
Its output is a deliberate external Goal file that defines the project's destination and
constitution without pre-scripting the Supervisor's execution plan.

The web AI should first read `AI_BOOTSTRAP.md`. For the full path from a rough idea to a
running Runtime, also read [Start a New Project from an Idea](NEW_PROJECT_WORKFLOW.md).

## Workshop behavior

Let the human dump the full idea naturally first. Do not begin with a giant questionnaire
and do not ask for information already supplied. Synthesize what is known, identify
material contradictions or gaps, then ask only targeted questions whose answers would
change the project constitution, acceptance decision, or risk boundary.

The workshop should converge through these concerns, not mechanically interrogate the
human in this order:

1. **Raw intent** — what the human currently wants to make, learn, decide, or change.
2. **Real objective** — why the project exists and what outcome would be valuable.
3. **Existing context** — prior work, relevant history, stakeholders, environment, and
   current baseline.
4. **Available inputs and resources** — files, data, code, equipment, access, tools,
   expertise, budget, and time that actually exist.
5. **Required final deliverables** — the concrete artifacts or decisions the Runtime
   must produce.
6. **Constraints** — scope, format, compatibility, schedule, cost, policy, language, or
   technical restrictions.
7. **Non-goals** — tempting adjacent work that is explicitly outside this project.
8. **Acceptance criteria** — observable conditions for judging the project successful.
9. **Evidence and reproducibility** — what must be cited, measured, tested, recorded, or
   repeatable, and at what standard.
10. **Risk boundaries and forbidden actions** — actions the Runtime must never take and
    decisions that require human review.
11. **Material unresolved assumptions or decisions** — uncertainties that genuinely
    affect scope, safety, deliverables, or acceptance.
12. **Final consistency review** — verify that deliverables serve the objective,
    acceptance criteria cover the deliverables, resources can support the work,
    constraints do not contradict success, and unresolved material choices are either
    decided or explicitly bounded.

Prefer a short round of high-impact questions over exhaustive completeness. If an
unknown can safely be resolved by the Supervisor during execution without changing the
project constitution, it usually does not need a workshop question.

## Goal document contract

Use the following structure as a recommended writing aid, not as an invented mechanical
schema. `START_PROJECT.ps1` accepts Goal content; it does not require these exact Markdown
headings.

```markdown
# Project Goal

## Objective

## Context

## Available Inputs and Resources

## Required Deliverables

## Constraints

## Non-Goals

## Acceptance Criteria

## Evidence and Reproducibility

## Risk Boundaries and Forbidden Actions

## Resolved Assumptions / Human Decisions
```

Write each section at the level needed to constrain meaningful decisions:

- The **Objective** states the actual destination and why it matters.
- **Context** contains facts needed to interpret the request, not a full conversation
  transcript.
- **Available Inputs and Resources** distinguishes verified resources from hoped-for
  access.
- **Required Deliverables** names inspectable final artifacts and their intended users.
- **Constraints** and **Non-Goals** prevent scope drift without dictating every method.
- **Acceptance Criteria** are specific enough for the Supervisor and Final Verification
  gate to decide whether the original Goal is satisfied.
- **Evidence and Reproducibility** states the proof standard instead of assuming that a
  polished deliverable is sufficient evidence.
- **Risk Boundaries and Forbidden Actions** makes unsafe, irreversible, unauthorized,
  or out-of-scope actions explicit.
- **Resolved Assumptions / Human Decisions** records consequential choices made during
  the workshop so the Runtime does not have to rediscover them.

If a material decision remains unresolved and the Runtime must not guess, resolve it with
the human before launch or state a clear Human Review boundary. Do not hide uncertainty
by writing an arbitrary assumption as fact.

## Destination, not stage plan

The Goal says what must ultimately be true and what rules govern the project. The Codex
Supervisor is responsible for decomposition, stage selection, review, and adaptive
routing as evidence arrives. Avoid Goal language such as a long mandatory sequence of
implementation stages unless that sequence is itself a genuine constraint.

The external Goal file may be revised freely throughout this workshop. When the draft is
complete, perform one final consistency review and ask the human to approve the actual
text. Do not start the Runtime before that approval.

After approval, `START_PROJECT.ps1 -GoalFile <path>` imports the document into the
Project as the canonical `projects/<project-id>/PROJECT_GOAL.md`. Goal Anchor binds that
canonical copy's exact bytes. Treat it as immutable after import: later steering about
method, quality, presentation, or execution direction belongs in `RESEARCH_STATE.md`,
not in a Goal rewrite. A materially changed objective normally requires a new Project.

## Handoff checklist

Before handing the approved external Goal to Runtime setup, confirm:

- the objective and required deliverables agree;
- acceptance criteria make success inspectable;
- claimed inputs/resources are available or their absence is explicitly handled;
- non-goals and forbidden actions bound the most likely scope and safety failures;
- evidence requirements match the consequence of the decisions being made;
- material contradictions are resolved;
- remaining uncertainty can be handled adaptively or through explicit `HUMAN_REVIEW`;
- the document defines the destination without taking over the Supervisor's planning
  role;
- the human has approved this version for import.
