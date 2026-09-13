# Fix 01 deployment to the existing dogfood Runtime

Deployed on 2026-09-13 to `C:\general-agent-runtime-v13-dogfood`. The registered live Web Console now resolves the historical Runtime publications. The completed project was not run again, and its history was not rewritten.

## Deployment choice and exact product changes

The Console's P9 creation mechanism (`scripts/web_console_runtime_create.py`) requires a previously non-existing destination and cannot upgrade this existing Runtime. Its template copying mechanism was therefore not used. The existing-installation procedure is code replacement with workers stopped, as described in `docs/QUICKSTART.md`; there were no running dogfood Orchestrator or Executor Python processes, and project state was already `COMPLETE` with no current task.

Used a one-time explicit three-file allowlist, backed up before replacement, with an atomic replacement for each file while the Console was stopped. No migration, creation, activation, preflight, project start/resume, claim, publication, completion, or Orchestrator command was invoked.

Only these product files were updated, relative to the dogfood root:

| File | Purpose | Deployed SHA-256 |
| --- | --- | --- |
| `scripts/executor_fence.py` | Validated, read-only historical staging/publication recovery and shared publication validation | `0bcaa6eff4768798785d88c108d1a2b55f48c4ae94bb9c7117c1f16fdf6ffcdb` |
| `scripts/executor_completion.py` | Completion integrity validation, including the additive sealed manifest contract used by the reader | `8d41eb241ad6edc2e3ef668135d14355d212ebd9c32e199c0197749513169dcb` |
| `scripts/supervisor_control.py` | Expose validated `artifact_provenance` in feedback; reject ambiguous MESSAGE_ID associations | `0a26f637b5745eb0ac52b536c575de2e6431dc52fc04e533b791a1343dbdd71d` |

These are the exact tested Fix 01 modules, not newly edited variants. All source hashes match the earlier Fix 01 test-results manifest. The backed-up installed modules match the pre-Fix-01 Git HEAD content after line-ending normalization, confirming that copying these modules introduces only their existing Fix 01 changes. The completion module includes the previously tested future-commit manifest support, but no commit path was executed during deployment or verification.

Dogfood's Console files were not updated because the actual serving Console already runs the repaired UI and consumers from the development workspace. Its `run_control` launches the registered Runtime's own `scripts/supervisor_control.py` for each read-only query. No permanent dogfood read service exists to restart.

## Preserved history and snapshot

Before mutation, all **238 files** in the dogfood installation were copied byte-for-byte to `evidence/dogfood-fix-01-deployment/snapshot/` outside the dogfood root and individually SHA-256 verified. The live tree was rehashed to confirm it had not changed during the snapshot, and again immediately before deployment.

After deployment and live verification, **224 protected non-cache files** have identical before/after SHA-256 hashes. No protected file was deleted and no non-cache file was added to dogfood. This includes all 97 files in the conservatively protected history/data scope:

| Scope | Files unchanged |
| --- | ---: |
| Entire `control/`, including active project pointer, Goal Anchor cross-checks, control state, candidates, Supervisor turns/decisions, attention and prompt files | 27 |
| Entire `handoff/`, including all completion ledgers, audit, staging archives, publication records, permanent claims, dispatch archives, brief archives and quarantine | 42 |
| Entire `projects/`, including project state, Goal, research state, claims/FV results, all artifacts and attempt workspaces | 22 |
| Entire root `logs/` and `reports/` | 2 |
| `CODEX_LAST_OUTPUT.txt`, `SUPERVISOR_BRIEF.md`, `TO_ZCODE.md`, `ZCODE_LAST_PROCESSED.txt` | 4 |

`evidence/dogfood-fix-01-deployment/history-hashes.md` enumerates every one of these 97 paths and its unchanged SHA-256. `before.json` records every original file; `after.json` lists all 224 protected files with individual before/after hashes and the exact old/new product hashes. The original 15 provenance-evidence files also still match the hashes recorded by the earlier Fix 01 inspection.

The only incidental dogfood changes are two generated Python bytecode caches: `scripts/__pycache__/executor_completion.cpython-312.pyc` and `scripts/__pycache__/executor_fence.cpython-312.pyc`. These are not historical authority. Their original bytes are included in the snapshot. No authoritative data was repaired, fabricated, restored, or edited.

## Service lifecycle

Used the existing ownership-verifying `STOP_WEB_CONSOLE.ps1` and `START_WEB_CONSOLE.ps1 -Port 60649 -NoBrowser` from the development Console installation. The verified Console PID changed from **36908** to **19740**, preserving its endpoint `http://127.0.0.1:60649/` and the registered dogfood ID **51042462032cbc1f**. The only restarted process was the Web Console backend. The normal launcher changed its non-authoritative Console instance metadata and server log outside dogfood.

The final process check found only that Console Python process; no dogfood project worker was started. Project state remains `COMPLETE`, `current_task` remains null, next MESSAGE_ID remains 700105, and Final Verification remains `PASS`, because the entire project-state file is byte-for-byte unchanged.

## Real live verification

Used the real browser UI at the existing Console endpoint, selecting registered `v1.3 Dogfood`. Before deployment, the Artifact Center showed all four current files as unbound and five unavailable/untrusted provenance notices. After deployment and refresh, it showed five catalog entries: four bound publication entries and the still-unbound `reports/final-report.md`.

| MESSAGE | Live result |
| --- | --- |
| 700100 | Task Detail's Artifacts tab lists `evidence/dogfood-rounds.csv` and `workspace/dogfood-note.md` with their historical hashes. Artifact Center shows both bound to 700100; the note has a verified, readable preview. |
| 700101 | Artifact Center shows a separate later `evidence/dogfood-rounds.csv` publication bound to 700101, matching current bytes, with its header and 700100 data row visible in the preview. |
| 700102 | Artifact Center shows `reports/final-verification-700102.json` bound to 700102, matching current bytes, with its JSON preview visible. |
| 700103 | Filtering Artifact Center by 700103 gives zero artifacts. Task Detail's Artifacts tab explicitly displays `No published paths` and `no verified publications are available for this round`. |
| 700104 | Filtering Artifact Center by 700104 gives zero artifacts. Task Detail's Artifacts tab explicitly displays the same empty-publication result. |

The old 700100 CSV association is **metadata-only** in this Console. Its recorded SHA-256 is `11e76e182c77c81e8b4b1b6626f34df685f899d33d1991617181de3f52727242`; current bytes hash to `b076c865409dbcfd0c67c01e5714d021011037a9602207990074f711601ba003`, matching 700101. The UI displays `HASH MISMATCH` and refuses its preview with `ARTIFACT_HASH_MISMATCH`. The old attempt-workspace files were neither restored nor exposed as historical previews.

A GET-only check against the same live server also verified Task Detail, MESSAGE_ID-filtered catalogs, artifact details and preview responses for all five rounds. Note/later CSV/JSON previews returned HTTP 200; old CSV preview returned HTTP 409 `ARTIFACT_HASH_MISMATCH`. All assertions passed. Exact responses, artifact IDs and timestamps are saved in `evidence/dogfood-fix-01-deployment/live-http.json`; the verification code is `verify_live.py`. Its initial unbound-file assertion was corrected to inspect the documented `provenance.class` field; that correction changed only the external verification script, not product or historical data.

The pre-existing Console cockpit/header `STATE_UNAVAILABLE` / `runtime_status` issue remains visible even though Runtime project state is COMPLETE. It predates this deployment and is outside Fix 01 provenance reading; no state was altered to conceal it.

No push, merge, tag, or release was performed. Deployment evidence and backups remain local in the development workspace.
