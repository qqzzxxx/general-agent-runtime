# FV-SANDBOX-ISOLATION-V1 — developer documentation

Mechanical isolation boundary for Final Verification (FV) work in this
Runtime. Implemented by `scripts/fv_sandbox.py`; consumed by the FV receipt
contract in `orchestrator.py`.

## 1. The rule

> Untrusted or destructive Final Verification probe code must never be able to
> write to live authoritative Runtime state.

Two execution modes exist and are machine-auditable end-to-end:

| Mode | May do | Must run |
|---|---|---|
| `LIVE_READ_ONLY` | inspect live files/artifacts | anywhere, read-only |
| `SANDBOX_DESTRUCTIVE` | write/delete/tamper/corrupt **fixtures** | only inside an external sandbox, behind the guard |

An Executor may never downgrade `SANDBOX_DESTRUCTIVE` to live execution, and
may never claim `SANDBOX_DESTRUCTIVE` without the auditable sandbox evidence.

## 2. Sandbox lifecycle (`scripts/fv_sandbox.py`)

```python
import sys
from pathlib import Path
sys.path.insert(0, str(RUNTIME_ROOT / "scripts"))
import fv_sandbox as fvs

sandbox = fvs.FVSandbox.create(runtime_root,            # live root (parameter, never hardcoded)
                               project_id=project_id, label="fv-700xxx")
sandbox.populate_runtime_copy()                          # structural isolation: copy source into sandbox
probe = sandbox.write_probe_module("my_probe", CODE)     # probe material lives in-sandbox
outcome = sandbox.destructive_window(
    [sys.executable, str(probe)],
    bindings={"FIXTURE_STATE": sandbox.root / "runtime" / "control" / "state.json"},
    required_probe_modules=["..."],                      # optional identity verification
    probe_stdout_identity=True)
# outcome["outcome"]     FV_SANDBOX_CLEAN | FV_SANDBOX_GUARD_BLOCKED | FV_SANDBOX_ISOLATION_INCIDENT
# outcome["isolation_incident"]  -> attempt permanently invalid; never package PASS
sandbox.clean()                                          # only incident-free sandboxes are removable
```

Key mechanics:

- **External root by construction**: sandboxes are created under
  `<system temp>\general-agent-runtime-fv\...` and are mechanically rejected
  if nested inside the live Runtime root (either direction), before any
  directory is created.
- **Structural isolation**: the sandbox holds its own copy of the Runtime
  source, so `__file__`-derived module-level path globals inside the copy
  anchor to the sandbox. Probes import the copy — there is nothing left to
  rebind, which eliminates the historical "incomplete module-global rebinding"
  failure class instead of policing it.
- **Path guard before destructive code**: `validate_binding` /
  `validate_bindings` canonicalize Windows-aware (`realpath` resolves
  junctions/symlinks/reparse points; case, drive and separator normalization;
  drive-relative anchoring; `..` traversal) and raise `SandboxEscapeError`
  (`FV_SANDBOX_ESCAPE_DETECTED`) for anything resolving into the live Runtime
  root or outside the sandbox — before the probe starts.
- **Process isolation**: `run_probe` / `destructive_window` pin the working
  directory to a validated sandbox path and build the child environment by
  stripping `PYTHONPATH` / `PYTHONHOME` / `PYTHONSTARTUP`, re-pinning
  `PYTHONPATH` to the sandbox import roots only. `run_probe` refuses to
  execute any live Runtime file as probe material.
- **Import identity**: probes embed `PROBE_IDENTITY` (from
  `probe_identity_report`); drivers verify it with `verify_probe_identity`,
  which fail-closes when a module demonstrably resolved outside the sandbox.
- **Integrity window**: `destructive_window` snapshots a narrow protected
  manifest (active-project pointer, control state, canonical project goal,
  isolated project state, STOP/HUMAN_REVIEW) before the window, compares it
  after, and records one machine-readable `sandbox_outcome.json` inside the
  sandbox. Trusted protocol writes are exempt only when explicitly declared
  via `allowed_writes` (canonical paths).
- **Incident semantics**: guard bypass with mutation ⇒
  `FV_LIVE_STATE_MUTATION_DETECTED`, `isolation_incident=true`, forensic
  evidence (`incident_evidence/`) retained, `clean()` refused. Any incident
  permanently invalidates the verification attempt: the receipt contract
  mechanically rejects such receipts, so a same-attempt incident can never be
  packaged as PASS. Recovery requires a new lifecycle/identity.

## 3. FV contract extension (orchestrator.py)

- Dispatch gate may pin `"EXECUTION_MODE": "LIVE_READ_ONLY" |
  "SANDBOX_DESTRUCTIVE"` (optional; absent = `LIVE_READ_ONLY`; unknown values
  fail closed at dispatch validation).
- A `SANDBOX_DESTRUCTIVE` receipt must carry:
  ```json
  "EXECUTION_MODE": "SANDBOX_DESTRUCTIVE",
  "ISOLATION_INCIDENT": false,
  "SANDBOX": {
    "SANDBOX_ROOT": "<sandbox root>",
    "SANDBOX_IDENTITY_SHA256": "<identity hash>",
    "OUTSIDE_LIVE_RUNTIME": true,
    "GUARD_STATUS": "FV_GUARD_PASSED | FV_GUARD_REJECTED_UNSAFE_CANDIDATE",
    "LIVE_MANIFEST_STATUS": "UNCHANGED",
    "OUTCOME": "FV_SANDBOX_CLEAN",
    "EVIDENCE": ["<project-relative outcome/evidence pointers>"]
  }
  ```
- Mechanical receipt rules (evaluator): exact mode binding (no downgrade or
  upgrade), incident ⇒ fail regardless of `OVERALL_STATUS`, and the full
  auditable sandbox block required for destructive gates. Legacy receipts
  without the new fields evaluate exactly as before.

## 4. Operational rules for verification drivers

1. Read-only checks stay `LIVE_READ_ONLY` and never write live state.
2. Anything destructive goes through the sandbox API above — never by
   hand-rolled temp dirs, never by rebinding live module globals.
3. Keep the destructive window narrow; do not run trusted control-plane flows
   concurrently with a window; declare any unavoidable trusted write via
   `allowed_writes`.
4. A guard rejection of a deliberately-unsafe negative-path candidate is the
   expected result (`FV_GUARD_REJECTED_UNSAFE_CANDIDATE`) and is not an
   incident; an escape past the guard or any unexpected protected-state
   mutation is an incident and invalidates the attempt.
5. Never clean an incident sandbox; reference its outcome JSON in the FV
   receipt evidence.

Regression coverage: `scripts/test_fv_sandbox.py` (Windows-aware path/
security matrix, process/import isolation, historical-class structural
elimination, integrity window, receipt gate, backward compatibility).
