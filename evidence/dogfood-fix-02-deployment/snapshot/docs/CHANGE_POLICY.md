# General Agent Runtime — Change Policy

> Rules for changing Runtime Core, protocol behavior, or maintainer documentation.

## 1. Principle

General Agent Runtime is a reliability system, not just an agent prompt bundle.

A small-looking change can affect:

- execution uniqueness;
- authorization;
- restart recovery;
- completion truth;
- Final Verification;
- Human Review;
- project isolation;
- terminal semantics.

Therefore Runtime Core changes must be treated like protocol changes.

---

## 2. Change classes

### Class A — Documentation-only / non-semantic

Examples:

- clearer onboarding;
- Maintainer Knowledge Pack;
- typo fixes;
- examples that do not change behavior.

Typical version effect:

```text
patch release
```

### Class B — Backward-compatible Runtime capability

Examples:

- a first-class `PAUSE_AND_STEER` command;
- a dedicated user-feedback/steering channel;
- improved safe status introspection;
- new optional UI that does not change protocol truth.

Typical version effect:

```text
minor release
```

### Class C — Protocol/state/wire compatibility change

Examples:

- task identity fields change;
- claim semantics change;
- completion ledger schema changes incompatibly;
- Human Review receipt schema changes incompatibly;
- Final Verification binding changes incompatibly.

Typical version effect:

```text
major release
```

Do not move an existing public tag to include later work.

---

## 3. Never develop Runtime Core on a live production project

Before changing Runtime Core:

1. pause the Executor Automation for the development Runtime;
2. stop or freeze the live Orchestrator safely;
3. create an isolated development/lab copy;
4. make sure the lab cannot inspect/write another Runtime;
5. use synthetic projects/identities for destructive tests.

Production should be a read-only reference during audit unless a separately reviewed merge is being applied.

---

## 4. Invariants that must survive every change

### 4.1 Task authorization

A visible `TO_ZCODE.md` is not authorization.

The exact task identity and inbox snapshot must be mechanically authorized.

### 4.2 At-most-once execution

Only one claim owner may execute an identity.

Permanent claims must never become a casual mutable lease.

### 4.3 Fresh identity on retry

A claimed attempt is never rerun under the same MESSAGE_ID/NONCE.

### 4.3a Post-claim fencing

A permanent claim is not permanent write authority. Preserve durable retirement,
claim-owner token binding, attempt-local candidate writes, and Runtime-controlled
canonical publication serialized with retirement. Tests must include an acquired
worker that pauses, times out, and resumes after a fresh retry starts. A checkpoint
or completion rejection alone does not prove canonical write safety. State the
same-user direct-write bypass limit explicitly; see `STALE_WORKER_FENCING.md`.

### 4.4 At-most-once completion

Authoritative completion remains Runtime-owned.

No Executor-owned root file may become completion truth again.

### 4.5 Monotonic completion lifecycle

```text
COMPLETION_COMMITTED
-> COMPLETION_CONSUMED
-> COMPLETION_SEALED
```

No reverse transition.

### 4.6 Goal integrity

Canonical Goal remains hash-bound and fail-closed.

### 4.7 Active-project isolation

Project-local work stays under the active project.

### 4.8 Runtime isolation

A Runtime/Executor must not inspect sibling Runtime Roots.

### 4.9 Human Review integrity

Human decisions re-enter only through audited, state-bound receipts.

### 4.10 Final Verification integrity

`COMPLETE` cannot bypass required profile policy verification.

### 4.11 STOP semantics

A real STOP remains distinguishable from temporary process interruption.

### 4.12 Console/UI non-authority

Presentation failures must not change protocol outcome.

---

## 5. Required workflow for a Runtime Core change

### Phase 1 — Reproduce / define the problem

Write:

- symptom;
- expected invariant;
- actual behavior;
- smallest failing scenario;
- whether the problem is correctness, safety, availability, UX, or documentation.

For a bug, reproduce before fixing when feasible.

### Phase 2 — Root-cause audit

Identify:

- authoritative state;
- writer/owner of each relevant artifact;
- crash windows;
- identity binding;
- duplicate/replay behavior;
- restart behavior.

Do not patch before knowing which invariant is actually broken.

### Phase 3 — Minimal change

Prefer the smallest change that restores the invariant.

Avoid weakening validation merely to improve liveness.

### Phase 4 — Targeted regression

Add a test that would have failed before the fix.

### Phase 5 — Full regression suite

Run:

```powershell
python -m unittest discover -s scripts -p "test_*.py"
```

No release if the full suite regresses.

### Phase 6 — Fault/restart checks when relevant

If the change touches lifecycle/claims/completion/recovery:

test at least:

- crash before publication;
- crash after publication but before authorization;
- crash after claim;
- crash around completion commit;
- crash between consume and Supervisor decision;
- restart with stale lock;
- duplicate wake / competing claim;
- stale/replayed completion.

### Phase 7 — Documentation update

If behavior changes, update every affected layer:

- canonical Runtime contract;
- architecture;
- quickstart/setup if operator-visible;
- handbook;
- operator playbook;
- incident runbook;
- change policy if governance itself changes.

No code-only protocol change.

---

## 6. Special policy for Interactive Steering

A first-class human steering feature is a strong candidate for a future minor release.

Desired user experience:

```text
user pauses/steers
-> feedback recorded immutably or append-only
-> current attempt safely classified
-> if already claimed, attempt is truthfully terminated without claim deletion
-> Supervisor immediately receives latest feedback
-> Supervisor replans
-> fresh task identity
-> Executor resumes
```

A proper implementation should remove the need for users to manually build `ABORTED_BY_USER` completion staging.

It must preserve:

- permanent claim;
- completion ledger truth;
- Goal Anchor;
- exact fresh identity for replan;
- crash/restart safety.

Do not implement "steering" by making claims deletable.

---

## 7. Documentation consistency policy

The Maintainer Knowledge Pack exists specifically so a new AI can replace lost conversational context.

Therefore every protocol-relevant change must answer:

```text
Would a fresh capable AI reading the repository now give the correct repair advice?
```

If not, documentation is incomplete.

The repository, not a private chat, must be sufficient to reconstruct the maintainer mental model.

---

## 8. Release checklist

Before tagging:

- [ ] working tree contains only intended changes
- [ ] no live project state is tracked
- [ ] no local credentials/tokens/user paths leaked
- [ ] no sibling/production Runtime path leaked
- [ ] full regression suite passes
- [ ] targeted new tests pass
- [ ] fresh-clone preflight path still passes
- [ ] canonical Executor prompt remains consistent with code
- [ ] Supervisor contract remains consistent with code
- [ ] architecture docs updated
- [ ] Maintainer Knowledge Pack updated
- [ ] version/release notes match actual semantics
- [ ] tag points to the intended immutable commit

---

## 9. AI maintainer rule

When an AI is asked to modify Runtime Core:

1. read `AI_BOOTSTRAP.md`;
2. read this file and the System Handbook;
3. inspect the current implementation;
4. state the invariant being changed/preserved;
5. make the smallest auditable change;
6. prove it with tests;
7. update the knowledge pack.

Do not rely on what a previous conversation "probably meant".
