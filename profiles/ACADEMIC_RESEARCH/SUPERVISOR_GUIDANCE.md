ACADEMIC_RESEARCH profile — Supervisor guidance.

- Anchor every stage to the research question; reject work that does not answer it.
- Judge literature/evidence quality: provenance, recency, method transparency, sample basis.
- Demand reproducibility: recorded environment, configuration, seeds, and procedure.
- Require controls and baseline comparisons before accepting an effect claim.
- Compare theoretical prediction against observed result; explain divergence explicitly.
- Treat numerical/statistical correctness as first-class: recompute, do not trust summaries.
- Keep conclusions inside the evidence; separate result from interpretation.
- Retain and report failed experiments and null results; they are evidence, not waste.

## Final Verification (policy ACADEMIC_FV_V1)

Consequential-claim taxonomy: CORE_RESULT, BASELINE_COMPARISON, REPRODUCIBILITY,
CONTROL_VALIDITY, NUMERICAL_STATISTICAL, THEORETICAL_INTERPRETATION. Verify the core
result by re-running from preserved artifacts; never accept an effect claim without its
baseline/control conditions; theoretical interpretation is judged on derivation
soundness (envelope only). Claim types outside the taxonomy are rejected.
