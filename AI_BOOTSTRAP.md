# General Agent Runtime 鈥?AI Bootstrap

> Status: maintainer knowledge pack for General Agent Runtime V1.x.
> Purpose: give any capable new AI enough context to assist with this Runtime without relying on a previous chat.

## 1. What this file is

This file is the **entry point for an AI that is helping operate, debug, maintain, or extend General Agent Runtime**.

Do not infer this system from generic "multi-agent" experience.
Do not guess protocol behavior from memory.
Read the repository's actual contracts and current project state as routed below.

This file is a **router**, not the full system manual.

---

## 2. Source-of-truth precedence

When sources disagree, use this order.

### Runtime mechanics

1. Current Runtime code and regression tests
2. `control/CODEX_SUPERVISOR_RUNTIME.md`
3. `control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md`
4. `control/EXECUTOR_TASK_TEMPLATE.md`
5. `handoff/PROTOCOL.md`
6. `docs/ARCHITECTURE.md`
7. This maintainer knowledge pack

The knowledge pack explains the system. It does not override the implementation.

### Project intent

1. Current project's canonical `PROJECT_GOAL.md`
2. Latest explicit human steering / quality feedback recorded in the project's `RESEARCH_STATE.md`
3. Earlier project memory and historical Supervisor decisions
4. Executor recommendations

A later user feedback entry may revise **method, quality bar, presentation style, or execution direction** without rewriting the Goal. It may not silently contradict the Goal.

### Live lifecycle truth

Use Runtime-owned state and ledgers, not a chat transcript:

- `projects/<active-id>/project_state.json`
- `control/ACTIVE_PROJECT.json`
- `control/orchestrator_runtime.json`
- `handoff/executor_claims/`
- `handoff/completion_ledger/`
- current `TO_ZCODE.md`
- Runtime-generated compatibility artifacts only as supporting views

Never treat a visible `TO_ZCODE.md`, `SUPERVISOR_BRIEF.md`, or `ZCODE_DONE.flag` by itself as authority.

---

## 3. First read

For any Runtime question, first read:

1. `docs/RUNTIME_SYSTEM_HANDBOOK.md`

Then route by question type.

### A. Normal operation / starting / monitoring a project

Read:

- `docs/OPERATOR_PLAYBOOK.md`
- current project's `PROJECT_GOAL.md`
- current project's `RESEARCH_STATE.md`
- current project's `project_state.json` when live-state detail matters

### B. Failure / interruption / stuck task / bad output / recovery

Read:

- `docs/INCIDENT_RUNBOOK.md`
- current project's `PROJECT_GOAL.md`
- current project's `RESEARCH_STATE.md`
- current project's `project_state.json`
- relevant current Runtime state and ledgers only as needed

Typical triggers:

- Goal Anchor mismatch
- `CLAIM_EXISTS`
- task already claimed but interrupted
- Executor timeout
- completion committed but no Supervisor progress
- `HUMAN_REVIEW`
- Scheduled Automation appears not to fire
- current task was issued before new user feedback
- user wants to revise already-generated deliverables

### C. Runtime Core or protocol change

Read:

- `docs/CHANGE_POLICY.md`
- `docs/RUNTIME_SYSTEM_HANDBOOK.md`
- the exact implementation files and tests affected by the proposed change

Never modify Runtime Core from assumptions alone.

---

## 4. Current validated V1 architecture

The validated V1 route is:

```text
Python Orchestrator
-> Codex CLI / GPT-5.6 Sol / high reasoning
-> TO_ZCODE.md
-> ZCode Desktop Scheduled Automation / GLM Executor
-> permanent claim
-> exactly one authorized stage
-> completion staging
-> Runtime-owned completion commit
-> completion ledger
-> Orchestrator consume + seal
-> Codex Supervisor review
-> next stage / Final Verification / HUMAN_REVIEW / terminal state
```

Roles:

- **Supervisor**: planning, acceptance, redirection, revision, stop, human-review decisions
- **Executor**: high-throughput implementation/research for exactly one authorized stage
- **Orchestrator**: deterministic protocol, authorization, state, scheduling, consume/seal, recovery, Final Verification gate

The human is **not** the message bus.

---

## 5. Non-negotiable invariants

Before advising any action, preserve these invariants:

1. `PROJECT_GOAL.md` is Goal-Anchor bound after project creation; do not casually edit it.
2. Seeing `TO_ZCODE.md` is not authorization.
3. Only `CLAIM_ACQUIRED` / exit code 0 authorizes Executor stage work.
4. Never delete a permanent claim to "retry".
5. A retry of a claimed attempt requires a fresh `MESSAGE_ID` and fresh `NONCE`.
6. The Executor must not directly manufacture authoritative completion.
7. Authoritative completion is Runtime-owned:
   `COMPLETION_COMMITTED -> COMPLETION_CONSUMED -> COMPLETION_SEALED`.
8. Do not hand-edit `ACTIVE_PROJECT.json`, lifecycle state, Goal Anchor bindings, completion ledger, or authorization state.
9. One Runtime Root has at most one active Orchestrator.
10. ZCode Scheduled Automation Workspace is the **Runtime Root**, not a project directory.
11. One Runtime may contain many projects, but V1 has one active project at a time.
12. `HUMAN_REVIEW` is resumed only through the audited prepare/apply flow.
13. A Supervisor opinion alone cannot bypass required Final Verification.
14. Separate Runtime Roots are isolated. Never inspect or modify sibling Runtime trees from an Executor run.
15. `STOP_AGENT_SYSTEM.ps1` is a real stop signal, not a temporary pause button.

---

## 6. Goal vs project memory vs live state

Keep these concepts separate:

```text
PROJECT_GOAL.md
= WHAT the project must ultimately achieve
= immutable after bootstrap under Goal Anchor

RESEARCH_STATE.md
= compressed mutable project memory
= what happened, what was learned, latest user steering/quality corrections

project_state.json
= authoritative current lifecycle state
= current task, status, message sequence, FV state, Supervisor decision metadata
```

Do not use `RESEARCH_STATE.md` as a transcript. Keep it compressed.

A useful user-feedback pattern is:

```markdown
# USER_FEEDBACK_00N 鈥?concise title

Priority:
- newer than previous quality baseline
- does not modify PROJECT_GOAL.md
- applies prospectively and, if explicitly stated, retroactively

Rules:
- ...
```

If newer feedback supersedes an old PASS/production baseline, keep the old history but clearly state that the new feedback has priority for future acceptance.

---

## 7. Runtime steering principle

The Runtime is autonomous by default, but the human may inspect outputs and steer quality or method during execution.

Current V1 principle:

```text
immutable Goal
+ mutable project memory / user feedback
+ safe task lifecycle
= human steering without turning the human into the message bus
```

If feedback is added while an Executor task is already running:

- that already-issued task does **not** automatically inherit the new feedback;
- the next Supervisor turn does read the latest `RESEARCH_STATE.md`;
- if the current task is nearly done, it may be cleaner to let it complete and let the next Supervisor turn re-evaluate under the new feedback;
- if continuing the current task would waste substantial work or propagate a serious defect, use the runbook's safe interruption/abort procedure.

Never solve steering by silently editing the canonical Goal.

---

## 8. How to answer the user

When acting as a Runtime maintainer AI:

1. Identify the exact lifecycle state before prescribing repair.
2. Distinguish:
   - current task identity
   - claim state
   - completion state
   - Supervisor state
   - user feedback timing
3. Prefer documented helper flows over manual file edits.
4. Give one safe step at a time for risky recovery.
5. Explicitly state what **not** to touch.
6. If the repository version differs from the documentation, inspect the current implementation before giving commands.
7. If a procedure is a workaround rather than a first-class V1 feature, say so.

---

## 9. Recommended opening prompt for a new AI conversation

The human may use:

> You are helping me operate and maintain General Agent Runtime. First read `AI_BOOTSTRAP.md` from the Runtime Root and follow its routing rules. Do not guess this Runtime from generic agent experience. Use the repository's current protocol and current active project as authority. After loading the necessary context, help me with the issue I describe.

That should be enough to replace reliance on a specific historical chat.
