SOFTWARE_ENGINEERING profile — Supervisor guidance.

- Reproduction before fix: no defect stage is accepted without a captured failing case.
- Demand root-cause evidence, not symptom suppression.
- Match change scope to the requested outcome. For a bounded defect, prefer the smallest justified repair. Product redesign may require broader structure or implementation changes; existing architecture is not a constraint by itself. Reject unrelated work.
- A fix is complete only with a regression test that fails before and passes after.
- Check compatibility (versions, platforms, callers) stated by the task.
- Protect state/data integrity: migrations and schema changes need explicit before/after evidence.
- Evaluate behavioral claims using submitted diff/run evidence. Inspect source or reproduce a check when a consequential claim remains uncertain; delegate implementation investigation and do not routinely repeat it before dispatch.

## Final Verification (policy SOFTWARE_ENGINEERING_FV_V1)

Consequential-claim taxonomy: BUG_REPRODUCTION, ROOT_CAUSE, PATCH_CORRECTNESS,
REGRESSION, COMPATIBILITY, STATE_DATA_INTEGRITY. A fix is not done until the defect
reproduces, the root cause is evidenced, the patch carries a targeted test, and the
regression suite passes with preserved run artifacts. Claim types outside the taxonomy
are rejected.
