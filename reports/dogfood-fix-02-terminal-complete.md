# Dogfood Fix 02 — Terminal COMPLETE state rendering

## Root cause and reproduction

The production Orchestrator copies the terminal project status to `orchestrator_runtime.json.status` after its existing Final Verification gate. On the real completed installation, both authority files report `COMPLETE`. `supervisor_control.current_status` correctly relayed this as `runtime_status: COMPLETE`.

The Console consumer's `KNOWN_RUNTIME_STATUSES` omitted `COMPLETE`. Its document validation rejected that legitimate producer value before reaching its existing project-COMPLETE family branch. The prior unit fixture tested `project_status: COMPLETE` with `runtime_status: RUNNING`, missing the actual terminal producer contract. The real CLI and the registered live Cockpit both reproduced `STATE_UNAVAILABLE`, with `honesty.unknown_fields == ["runtime_status"]`. The original responses are in `evidence/dogfood-fix-02/before-status.json` and `before-live.json`.

The old status projection also hid raw `current_task` outside `WAITING_EXECUTOR`, and did not provide bounded terminal acceptance evidence. Merely adding an enum would therefore have permitted some contradictory or incomplete completion facts. The live page additionally exposed historical-authorization confusion: the alert layer interpreted the retained final dispatch as a task still awaiting pickup and approaching expiry.

## Production changes

| File | Change |
| --- | --- |
| `scripts/supervisor_control.py` | Adds read-only `terminal_completion` evidence. Rejects malformed source objects and duplicate JSON keys in status authority. Checks raw current-task/project/inflight/intervention consistency and calls the existing installed `final_verification_terminal_check` predicate without enforcing, reconciling, saving or rerunning anything. Reports FV PASS only when the existing required gate passes. |
| `scripts/web_console_state.py` | Recognizes runtime COMPLETE and requires matching terminal statuses, valid versioned completion evidence, empty active execution, no pending intervention or safety hold, and a settled control state. Keeps machine-readable fail-closed reasons. Exposes terminal FV and last-consumed facts separately from active-task protocol facts. Historical authorization is no longer labeled current for verified COMPLETE. |
| `scripts/web_console_alerts.py` | Uses the same validated COMPLETE interpretation before treating retained authorization as historical. Prevents false pickup/expiry reminders and emits the completion notice only for validated COMPLETE. Invalid completion does not suppress risk reminders. |
| `web_console/index.html` | Displays verified terminal FV status and last processed MESSAGE separately from active task information; clears these facts on reload/error or Runtime switching. Existing COMPLETE wording and success presentation remain driven by the interpreter. |
| `scripts/test_terminal_complete_status.py` | Production source-file, installed CLI and localhost Cockpit regression; verifies read-only authority, historical dispatch, FV binding, missing/malformed/corrupt source data and terminal contradictions. |
| `scripts/test_web_console_state.py` | Replaces the impossible terminal fixture with the producer contract; unsettled pause plus terminal completion fails closed. |
| `scripts/test_web_console_alerts.py` | Covers historical authorization on verified COMPLETE, preservation of risk alerts on unvalidated completion, and validated completion notice. |
| `scripts/test_web_console_frontend.py` | Pins the terminal fact panel and clearing behavior. |

## Safety and compatibility

No authorization, claim/fence, completion commit/seal, STOP, HUMAN_REVIEW or Final Verification acceptance function was changed. The new producer calls only the existing read-only gate predicate and profile readers. The installed gate and all 16 profile files were independently SHA-256 compared with the tested source; all match.

Runtime COMPLETE alone, project COMPLETE alone, older status producers lacking terminal evidence, mismatched statuses, residual tasks or inflight turns, malformed/duplicate-key authority, missing required FV binding, failed mechanical FV or a newer consumed result cannot resolve to COMPLETE. Missing or invalid JSON uses the established control error envelope; unusable status facts use `STATE_UNAVAILABLE`. Known nonterminal families retain their existing interpretation and control behavior. Genuine ungated legacy completion may resolve to COMPLETE, but never invents FV PASS.

The raw historical `last_authorized_dispatch` remains in the status document. It does not become an active task, worker, protocol milestone or future dispatch. No progress, timestamp or usage is fabricated.

Completed-project validation uses `C:\general-agent-runtime-v13-dogfood` read-only. Any product deployment is separately allowlisted, backed up, atomically replaced and hash verified; authoritative history is never repaired or rewritten. No push, merge, tag or release is performed.

## Validation and live deployment

| Final validation | Run | Passed | Skipped | Failures / errors | Duration |
| --- | ---: | ---: | ---: | ---: | ---: |
| Focused state/producer/Cockpit/frontend/alerts/control/FV modules | 235 | 235 | 0 | 0 / 0 | 38.684 s |
| Complete regression: `python -B -m unittest discover -s scripts -p 'test_*.py' -v` | 1285 | 1282 | 3 | 0 / 0 | 384.064 s |

The three existing skips are `test_g5a5_1_hotfix` cases `test_h1_harness_rebinds_active_project_file`, `test_h2_original_suite_green_with_real_pointer`, and `test_h5_h8_real_candidate_preflight`, all because the clean Runtime has no active project pointer. Windows service-cleanup ResourceWarnings / WinError 10038 are retained in the raw log; the associated tests passed. `git diff --check` passed. All eight tested source/test hashes were rechecked after deployment and remain identical.

An earlier full run also passed 1283 tests (1280 passed, 3 skipped) before the final historical-authorization alert regressions. The table above is the later complete run of the final product code, not a combined claim from partial runs. Exact commands, skips, durations and hashes are in `evidence/dogfood-fix-02/test-results.json`; logs are `focused-final.txt` and `full-suite-final.txt`.

### Bounded deployment

Deployed on 2026-09-13 to the existing `C:\general-agent-runtime-v13-dogfood` installation. Before replacement, all **238 files** were copied byte-for-byte to `evidence/dogfood-fix-02-deployment/snapshot/` and individually SHA-256 verified. The live tree was verified unchanged during the snapshot and immediately before replacement. The explicit allowlist contains only `scripts/supervisor_control.py`, atomically replaced with the exact tested bytes:

- Before: `0a26f637b5745eb0ac52b536c575de2e6431dc52fc04e533b791a1343dbdd71d`
- After/tested source: `f3ba5a6c141c598717e42fa6f128d8c81b990a486b54f566f4cea2d35588dc7c`

The Console already serves product code from the development workspace, so its interpreter, alerts and HTML did not need copying into dogfood. The final regression's existing launcher tests stopped the original Console process and exercised temporary Console instances; after tests no Python worker remained. The ownership-aware `STOP_WEB_CONSOLE.ps1` confirmed `WEB_CONSOLE_NOT_RUNNING`. `START_WEB_CONSOLE.ps1 -Port 60649 -NoBrowser` then restored the real service as PID **27512**, preserving port **60649** and registered dogfood ID **51042462032cbc1f**. The prior real Console PID was **19740**. Only non-authoritative development Console instance metadata/logs changed as part of the service lifecycle; `web_console_data/instance.json` now truthfully identifies the new process.

After CLI, live HTTP and browser verification, all **226 protected non-cache files** in dogfood retain their original hashes; there are no deletions or new non-cache files, and no cache changes. This includes all **97 authority/history files** under `control/`, `handoff/`, `projects/`, root `logs/` and `reports/`, and the four root handoff/output files. They were checked against both the original pre-investigation hashes and the deployment snapshot. `before.json`, `after.json`, `history-hashes.json`, and `gate-code-hashes.json` enumerate the exact files and hashes. The only remaining Python process is the Console backend; no dogfood Orchestrator or Executor was started.

### Real live result

The installed Runtime's own CLI, the actual registered `/status`, `/cockpit`, and `/alerts` GET endpoints, and the browser at `http://127.0.0.1:60649/#cockpit` all verify the fix. Exact responses and assertions are retained in `evidence/dogfood-fix-02-deployment/live-http.json` and `verify_live.py`.

The real Cockpit visibly shows:

- Green **项目已完成**, with a completed header badge.
- **当前无人执行**; active task/worker are null, no active milestones or current authorization are invented.
- **最终验证: PASS** and **最后处理任务: MESSAGE 700104**.
- **项目已完成，无待执行任务** and **暂时不需要人工操作**.
- **未发现错误或警告**; no `STATE_UNAVAILABLE` warning.
- Exactly one informational project-complete reminder, with no false pickup/expiry reminders.

The Supervisor panel still reports decision COMPLETE, resulting status COMPLETE and resulting dispatch none this turn. Pending interventions remain zero. Project state remains COMPLETE, `current_task` remains null, and `next_message_id` remains 700105, because the complete project-state file is byte-for-byte unchanged. No project rerun, migration, activation, state repair, push, merge, tag or release occurred.
