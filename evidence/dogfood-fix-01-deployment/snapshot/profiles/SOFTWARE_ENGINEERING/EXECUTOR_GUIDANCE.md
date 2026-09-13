SOFTWARE_ENGINEERING profile — Executor guidance.

- Reproduce the defect first; capture the trace/log/failing test as evidence.
- Implement the minimal change that fixes the captured root cause.
- Add or update tests covering the fix and its edge cases.
- Deliver the exact diff; keep unrelated code untouched.
- Record full test-run evidence: suite, commands, pass/fail output, before vs after.
- Note rollback considerations and any behavior change visible to callers.
