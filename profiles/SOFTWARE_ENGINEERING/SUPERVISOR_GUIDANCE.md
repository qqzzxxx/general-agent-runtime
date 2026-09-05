SOFTWARE_ENGINEERING profile — Supervisor guidance.

- Reproduction before fix: no defect stage is accepted without a captured failing case.
- Demand root-cause evidence, not symptom suppression.
- Smallest justified patch: reject unrelated refactoring and drive-by improvements.
- A fix is complete only with a regression test that fails before and passes after.
- Check compatibility (versions, platforms, callers) stated by the task.
- Protect state/data integrity: migrations and schema changes need explicit before/after evidence.
- Judge the diff, not the description: every behavioral claim needs a run behind it.

## Final Verification (policy SOFTWARE_ENGINEERING_FV_V1)

Consequential-claim taxonomy: BUG_REPRODUCTION, ROOT_CAUSE, PATCH_CORRECTNESS,
REGRESSION, COMPATIBILITY, STATE_DATA_INTEGRITY. A fix is not done until the defect
reproduces, the root cause is evidenced, the patch carries a targeted test, and the
regression suite passes with preserved run artifacts. Claim types outside the taxonomy
are rejected.
