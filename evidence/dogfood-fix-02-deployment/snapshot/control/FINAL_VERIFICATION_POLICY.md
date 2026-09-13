# Generic Final Verification Gate (with Production V1.5 compatibility)

## Purpose

Before a new commercial run may enter `COMPLETE`, the Supervisor must identify and adversarially
verify the 3–8 facts that carry the final decision. This is a bounded verification stage, not a
second broad research pass.

Typical decision-critical claims include:

- exact patent / IP identifiers and legal-risk facts;
- unit-economics arithmetic or price anchors;
- the core demand or customer-pain evidence;
- regulatory / safety claims;
- the fact that separates the selected candidate from rejected alternatives.

## State contract

`control/project_state.json`:

```json
{
  "final_verification": {
    "policy_version": 1,
    "required": true,
    "status": "NOT_STARTED | PENDING | REVERIFY | PASS | FAIL",
    "critical_claims": [],
    "claims_hash": null,
    "verification_message_id": null,
    "verification_receipt_sha256": null,
    "verified_at": null
  }
}
```

Each critical claim requires:

- `claim_id`
- `claim`
- `claim_type`
- `decision_impact`: `HIGH` or `MEDIUM`
- `evidence_pointers`
- `verification_standard`

These keys are mechanically enforced and lowercase exactly as shown. Uppercase
variants or aliases (`CLAIM_ID`, `TYPE`, `FALSIFIED_IF`, ...) do not satisfy the
schema: the dispatch is rejected and quarantined, and the Supervisor receives one
bounded repair turn with the precise validation error.

Recommended types: `IP`, `ECONOMICS`, `CUSTOMER_PAIN`, `DEMAND`, `COMPETITION`,
`REGULATORY`, `SAFETY`, `PRODUCT_SPEC`, `OTHER`.

## Verification task

Use the ordinary atomic Executor claim and a fresh MESSAGE_ID/NONCE.

```json
{
  "TASK_KIND": "FINAL_VERIFICATION",
  "FINAL_VERIFICATION_GATE": {
    "POLICY_ID": "<active Profile final_verification_policy_id>",
    "POLICY_VERSION": 1,
    "CLAIMS_HASH": "<canonical SHA256>",
    "CLAIM_COUNT": 3,
    "CRITICAL_CLAIMS": []
  }
}
```

For a profile-routed project, `POLICY_ID` is required and must match the active
Profile's bound policy. Every field shown belongs inside `FINAL_VERIFICATION_GATE`;
top-level policy/claims fields do not satisfy the dispatch contract. Legacy V1.5
commercial compatibility tasks may omit only `POLICY_ID`.

The task claim list must exactly match `project_state.final_verification.critical_claims`.

The Executor should try to falsify each claim first and avoid broad scope expansion.

## Receipt contract

The normal v2 receipt adds:

```json
{
  "FINAL_VERIFICATION": {
    "POLICY_VERSION": 1,
    "CLAIMS_HASH": "...",
    "OVERALL_STATUS": "PASS | FAIL | INCONCLUSIVE",
    "CLAIM_RESULTS": [
      {
        "claim_id": "C1",
        "status": "SUPPORTED | PARTIALLY_SUPPORTED | WEAK | UNSUPPORTED | CONTRADICTED | NOT_VERIFIABLE",
        "checks": {},
        "evidence_pointers": [],
        "auditor_note": "..."
      }
    ]
  }
}
```

Mechanical gate requirements:

- Every dispatched claim appears exactly once.
- HIGH-impact claim => `SUPPORTED`.
- MEDIUM-impact claim => `SUPPORTED` or `PARTIALLY_SUPPORTED`.
- IP => `authoritative_identifier_check=PASS` plus authoritative source pointer(s).
- ECONOMICS => `mechanical_recalculation=PASS` plus a calculation artifact pointer.
- CUSTOMER_PAIN / DEMAND => `source_independence_check=PASS` and at least 2 independent
  underlying sources.
- REGULATORY / SAFETY => `authoritative_source_check=PASS` plus authoritative source pointer(s).
- Overall verifier status must be `PASS`.

Python validates the envelope, identity, exact claim-set hash, coverage, required check fields,
and accepted receipt identity. It does not make the underlying commercial judgment.

## Final acceptance

After a mechanically passing verification receipt, Codex performs one `FINAL_ACCEPTANCE` turn.

To enter `COMPLETE`, Codex must set:

- `final_verification.status = PASS`
- unchanged `critical_claims` / `claims_hash`
- `verification_message_id` = accepted verification MESSAGE_ID
- `verification_receipt_sha256` = injected `brief_sha256`
- `verified_at` timestamp

Any newer Executor receipt after verification invalidates the old gate approval.

## Failure behavior

If verification fails/inconclusive, do not rerun the whole project. REVISE the smallest affected
claim/evidence/economics/IP segment, then dispatch a fresh final-verification task with a fresh
MESSAGE_ID/NONCE.

If Codex attempts `COMPLETE` before the gate passes, Python mechanically returns the lifecycle to
`SUPERVISOR_TURN` and emits `FINAL_VERIFICATION_GATE_REQUIRED`.

Commercial runs started before V1.5 activation are grandfathered unless their state explicitly
sets `final_verification.required=true`.
