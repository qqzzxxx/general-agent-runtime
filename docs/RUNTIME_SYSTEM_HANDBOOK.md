# General Agent Runtime — System Handbook

> Maintainer-facing system model for General Agent Runtime.
>
> This handbook explains the design and intended invariants. If it conflicts with current code or canonical Runtime contracts, the current implementation wins.

## 1. Why this Runtime exists

General Agent Runtime is designed for **long, multi-stage research and engineering work** where one monolithic agent is a poor fit.

A single agent that plans, executes, verifies, loops, and decides when to stop tends to create several problems:

- expensive reasoning is wasted on repetitive work;
- execution drifts because planning and implementation are mixed;
- weak intermediate evidence can be accepted too easily;
- duplicate wakes or retries can repeat work;
- crash recovery becomes ambiguous;
- users become manual message relays between agents;
- the final result becomes difficult to audit.

The Runtime separates responsibilities into three roles:

```text
Supervisor = think, decide, review, redirect
Executor   = perform one authorized stage
Orchestrator = mechanically enforce the protocol
```

The human defines the goal, may inspect results, may steer quality or method, and may resolve explicit Human Review. The human should not relay every handoff manually.

---

## 2. Validated V1 implementation

Current validated Supervisor implementation:

```text
Codex CLI
GPT-5.6 Sol
high reasoning
```

Current Executor integration:

```text
ZCode Desktop Scheduled Automation
GLM-family Executor
```

The architecture is protocol-separated, but the current Supervisor backend is not a one-line interchangeable provider. Replacing the Supervisor backend requires adapting the invocation layer and validating behavior.

The Executor boundary is conceptually more portable, but any replacement must obey the same:

- Runtime-root isolation
- inbox semantics
- exact task identity
- authorization/claim semantics
- completion staging/commit semantics
- one-stage-only rule

---

## 3. Core architecture

Normal stage flow:

```text
User prepares Goal
        |
        v
START_PROJECT.ps1
        |
        v
canonical PROJECT_GOAL.md + Goal Anchor
        |
        v
Python Orchestrator
        |
        v
Codex Supervisor turn
        |
        v
candidate dispatch -> Runtime validation/authorization
        |
        v
TO_ZCODE.md
        |
        v
ZCode Scheduled Automation wake
        |
        v
executor_claim.py acquire
        |
        +-- not acquired -> quiet/fail-closed exit
        |
        v
Executor performs exactly one stage
        |
        v
durable project outputs + evidence
        |
        v
project-local completion staging
        |
        v
executor_completion.py commit
        |
        v
COMPLETION_COMMITTED
        |
        v
Runtime-generated compatibility artifacts
        |
        v
Orchestrator consumes completion
        |
        v
COMPLETION_CONSUMED
        |
        v
Codex Supervisor review / lifecycle decision
        |
        v
COMPLETION_SEALED
        |
        +--> next Executor stage
        +--> Final Verification
        +--> HUMAN_REVIEW
        +--> COMPLETE / BLOCKED / STOPPED
```

---

## 4. Role boundaries

### 4.1 Supervisor

The Supervisor owns high-value reasoning:

- project decomposition;
- choosing the next stage;
- accepting/rejecting Executor results;
- `REVISE`;
- `REDIRECT` / `CHANGE_METHOD`;
- deciding when evidence is sufficient;
- deciding when user input is required;
- selecting Final Verification claims;
- terminal recommendations and final acceptance logic.

The Supervisor should **not** do the Executor's bulk work:

- broad scraping;
- repetitive extraction;
- large batch transformations;
- long implementation loops;
- repetitive QA across many files;
- bulk evidence collection.

One nonterminal Supervisor turn should make one semantic decision, publish at most one fresh stage, then exit.

### 4.2 Executor

The Executor:

- wakes from a Runtime-level Scheduled Automation;
- reads the current Runtime-root inbox;
- acquires the exact permanent claim;
- works only after successful claim;
- completes exactly the authorized stage;
- writes durable outputs and evidence;
- creates project-local completion staging;
- commits completion through the Runtime helper;
- exits immediately after successful commit.

The Executor must not:

- choose the next stage;
- change project goals;
- decide project completion;
- bypass Final Verification;
- continue into the next task after completion;
- inspect sibling Runtime Roots;
- directly write authoritative lifecycle state;
- directly republish root completion truth.

### 4.3 Orchestrator

The Orchestrator is the deterministic control plane.

It owns or enforces:

- one-Orchestrator-per-Runtime lock;
- active project scope;
- Supervisor invocation;
- dispatch validation;
- dispatch authorization binding;
- exact task identity;
- inbox hash binding;
- timeout/watchdog behavior;
- Runtime-owned completion lifecycle;
- compatibility artifact generation;
- completion consume/seal;
- Final Verification gate;
- Goal Anchor checks;
- terminal/user-attention surfacing;
- Human Review lifecycle integration.

It is intentionally mechanical. It should not make domain/research judgments.

---

## 5. Runtime, project, profile, automation

### Runtime

One checkout/installation of the repository.

It owns:

- `control/`
- `handoff/`
- Runtime wire files
- claims
- completion ledger
- Orchestrator lock/state
- many project directories

### Project

One isolated unit of work under:

```text
projects/<project-id>/
```

Typical project-local truth includes:

```text
PROJECT_GOAL.md
RESEARCH_STATE.md
project_state.json
workspace/
evidence/
reports/
completion_staging/
```

### Active Project

V1 may store many projects, but exactly one project is active through:

```text
control/ACTIVE_PROJECT.json
```

### Profile

A project type binding such as:

- `GENERAL`
- `ACADEMIC_RESEARCH`
- `SOFTWARE_ENGINEERING`
- `BUSINESS_RESEARCH`

Profiles provide role guidance and Final Verification policy.

### ZCode Automation

The Automation is **Runtime-level**, not project-level.

Its Workspace is always the Runtime Root.

Creating a new project in the same Runtime does not require a new Automation or a different Workspace.

Different Runtime Roots should use different Automations.

---

## 6. Stable task identity

Every dispatch is bound by:

```text
MESSAGE_ID
TASK_ID
STAGE_ID
ATTEMPT
NONCE
```

Key meaning:

- `MESSAGE_ID`: monotonic Runtime conversation identity; retired ids are never reused.
- `TASK_ID`: logical task.
- `STAGE_ID`: concrete stage/attempt label.
- `ATTEMPT`: explicit revision/retry number.
- `NONCE`: fresh random dispatch identity.

The Runtime also binds authorization to the exact inbox snapshot/hash.

Therefore:

> a visible task file is only a candidate dispatch; it is not permission to execute.

---

## 7. Why permanent claim exists

Scheduled Automations can overlap, repeat, or wake while an earlier instance is still active.

The claim helper is the at-most-once execution gate.

Only:

```text
CLAIM_ACQUIRED
exit code 0
```

permits project-specific work.

Expected non-owner outcomes include:

```text
CLAIM_EXISTS
ALREADY_PROCESSED
```

Those wakes stop without stage work.

A claim is permanent for that exact identity.

Never delete a claim merely to retry.

If an already-claimed attempt fails or is intentionally aborted, a future Executor attempt requires a **fresh Supervisor-authorized identity**.

---

## 8. Why completion seal exists

At-most-once claim solves duplicate **execution ownership**. It does not prove at-most-once **completion publication**.

The Runtime therefore owns completion truth.

Legal flow:

```text
Executor stage work
-> candidate completion staging
-> executor_completion.py commit
-> one Runtime-owned ledger entry
-> COMPLETION_COMMITTED
-> Orchestrator consume
-> COMPLETION_CONSUMED
-> one Supervisor lifecycle decision
-> COMPLETION_SEALED
```

The lifecycle is monotonic and never reverses.

Root files such as:

```text
SUPERVISOR_BRIEF.md
ZCODE_LAST_PROCESSED.txt
ZCODE_DONE.flag
```

are derived compatibility artifacts / wake hints. They are not authoritative completion truth.

This prevents a still-running or stale Executor instance from driving the lifecycle twice.

---

## 9. Goal Anchor: project constitution

When an isolated project is created, the supplied Goal is imported as:

```text
projects/<project-id>/PROJECT_GOAL.md
```

The Runtime binds its exact bytes/SHA-256.

Before Supervisor turns and dispatch authorization, the Runtime verifies that binding.

Therefore:

- casual edits to the canonical Goal are not supported;
- accidental mutation fails closed;
- silent rebinding is forbidden;
- an intentional materially different goal should normally become a new project, not a hidden in-place rewrite.

Conceptually:

```text
PROJECT_GOAL.md = project constitution
```

---

## 10. Project memory: RESEARCH_STATE.md

`RESEARCH_STATE.md` is intentionally mutable.

Its purpose is:

- compressed project history;
- accepted findings;
- rejected methods;
- open questions;
- latest production baseline;
- user steering;
- quality corrections that do not change the Goal.

It is **not** a transcript.

A healthy memory file keeps only decision-relevant state.

### Goal vs memory

```text
Goal:
"Produce 27 classroom-ready PPTs."

Memory / user steering:
"Increase projection font size."
"Previously generated PPTs must be rechecked."
"Do not expose internal verification wording."
"Pages that claim to show images must contain real images."
```

The second category changes execution/quality criteria without changing the project's underlying objective.

---

## 11. Human steering during autonomous execution

The Runtime should be autonomous, but autonomy does not mean the human must accept an early bad production baseline.

A useful operating model is:

```text
Goal is fixed
Execution is autonomous
Human may inspect representative outputs
Human may append steering feedback
Supervisor re-evaluates the plan
Executor continues under the updated baseline
```

### Timing matters

If a user writes new feedback after a task was already dispatched:

- the already-issued Executor task still has its old objective/acceptance criteria;
- that running task does not magically update;
- the next Supervisor turn receives the latest project memory and can revise the route.

Two safe patterns:

1. **Task is nearly complete**
   Let it complete. The next Supervisor turn sees both:
   - the completion result (what just happened)
   - the latest `RESEARCH_STATE.md` (what the user now requires)

   The Supervisor can then reinterpret the completion under the new standard.

2. **Continuing would waste significant work or propagate a systemic defect**
   Pause future automation wakes, stop the current Executor instance, preserve the permanent claim, and use the runbook's safe abort/steering procedure so the Supervisor can immediately replan.

Current V1 does not yet expose a dedicated one-click "Pause + Feedback + Replan" command. This is a natural future feature candidate.

---

## 12. HUMAN_REVIEW is different from ordinary steering

`HUMAN_REVIEW` is a formal lifecycle state used when the system requires a human decision that it must not invent.

Examples:

- ambiguous objective;
- physical-world action;
- credentials/access only the user can provide;
- irreversible risk acceptance;
- a protocol condition that requires explicit human recovery.

Resume is audited through the prepare/apply receipt flow.

Do not use manual lifecycle edits to escape Human Review.

Ordinary quality steering through `RESEARCH_STATE.md` does **not** require entering `HUMAN_REVIEW` unless the Supervisor/Runtime raises it.

---

## 13. Final Verification

`COMPLETE` is not merely a Supervisor judgment.

Each project profile binds a Final Verification policy.

The Supervisor identifies decision-critical claims; the Executor performs a bounded independent verification stage; the Runtime mechanically validates the receipt and acceptance binding.

Only after required verification and final acceptance may the project enter `COMPLETE`.

This separates:

```text
"the last Executor stage succeeded"
```

from:

```text
"the project's original completion criteria are actually satisfied"
```

---

## 14. Failure philosophy

The Runtime is designed to **fail closed** rather than silently invent state.

Examples:

- modified Goal -> Human Review / no dispatch;
- unauthorized inbox -> no claim;
- duplicate claim -> no work;
- malformed completion staging -> no completion;
- raw completion wake without ledger backing -> quarantine/no lifecycle drive;
- live Orchestrator lock -> second Orchestrator blocked;
- invalid active-project pointer -> no unsafe fallback;
- invalid Human Review resume -> no state mutation.

Availability problems should not be "fixed" by weakening identity, claim, ledger, Goal Anchor, or Final Verification invariants.

---

## 15. Temporary process interruption vs real STOP

These are not the same.

### `Ctrl+C` on the Orchestrator

Process interruption only.

It does not by itself mean the user wants the project terminally stopped.

A later normal start may resume from persisted state.

### `STOP_AGENT_SYSTEM.ps1`

Creates the Runtime STOP signal.

This is a real stop request and participates in lifecycle semantics.

Do not use it as a temporary pause just to add feedback.

---

## 16. Isolation

The strongest simple rule:

> One Executor Automation belongs to one Runtime Root.

The Executor may not inspect:

- another Runtime installation;
- a production Runtime from a lab Runtime;
- a backup Runtime;
- a historical copy;
- a sibling Runtime.

Project work stays inside the active project unless the current task explicitly authorizes narrow Runtime-core maintenance.

This prevents cross-environment contamination.

---

## 17. Maintainer knowledge pack vs canonical contracts

This handbook is intentionally broader and more explanatory than the canonical protocol files.

Use:

- `README.md` / `QUICKSTART.md`: onboarding
- `ARCHITECTURE.md`: compact architecture
- canonical control/handoff files: formal protocol
- this handbook: deep maintainer mental model
- `OPERATOR_PLAYBOOK.md`: normal operation
- `INCIDENT_RUNBOOK.md`: abnormal operation
- `CHANGE_POLICY.md`: changing the Runtime itself

---

## 18. Version discipline

Documentation must evolve with protocol behavior.

Recommended release meaning:

- patch: documentation, operator clarity, non-semantic fixes
- minor: backward-compatible new Runtime capability (for example first-class Interactive Steering)
- major: incompatible wire/state/protocol change

Never move a release tag after publication to "make it include the new docs".

A historical tag should remain a historical snapshot.
