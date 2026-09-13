# Dogfood Fix 04 — authoritative per-round token telemetry

The supported Codex Supervisor path now captures usage from its existing CLI
invocation. The supported ZCode integration exposes no host/provider usage
channel, so Executor usage remains unavailable. No estimated counts, costs,
billing calls, new credentials, provider migration, or history backfill were added.

## Evidence and production trace

**Supervisor.** `orchestrator.invoke_codex` starts a fresh `codex exec`, using the
existing model, effort, approval/sandbox policy, prompt, timeout, and final-message
file. The installed executable is **codex-cli 0.152.1**. Its help exposes `--json`
as stdout JSONL events and `-o` as the agent's final message. The official
[non-interactive-mode documentation](https://learn.chatgpt.com/docs/non-interactive-mode)
describes `thread.started`, `turn.started`, and `turn.completed.usage` and shows
the four fields below. This was verified against an actual fresh invocation,
not just a documentation example.

| Exact CLI field | Captured meaning | Live disposable probe |
| --- | --- | ---: |
| `input_tokens` | CLI-reported input token count | 15,897 |
| `cached_input_tokens` | CLI-reported cached input token count; kept separate | 3,712 |
| `output_tokens` | CLI-reported output token count | 5 |
| `reasoning_output_tokens` | CLI-reported reasoning output token count; exact field retained | 0 |
| `total_tokens` | Not emitted by this supported CLI event; unavailable | — |

The probe used `gpt-5.6-sol`, low effort, `--json`, `--sandbox read-only`,
`--ephemeral`, and an automatically removed temporary working directory. Its
instruction requested an OK response without tools or file access. Exit code was
0. Existing authentication was reused; no credentials or raw output stream were
saved. The machine-captured projection, IDs and capture digest are in
[`live-cli-probe.json`](../evidence/dogfood-fix-04/live-cli-probe.json).

The counters are the CLI's completed exec-turn usage, which may include multiple
internal model requests/tool iterations. They are **not** separate per-request
API receipts, account totals, or context-window measurements. Runtime does not
add cached input to input, add reasoning output to output, or calculate a total.

**Where data was lost.** The old command did not enable JSONL and did not capture
stdout. `supervisor_turn_usage` instead parsed `token_usage` out of `-o`'s final
agent message. That file is model-authored content, not provider telemetry.
Ordinary Supervisor answers did not contain it; even a correctly typed invented
block would previously have been accepted. Also, the optional observation existed
only in memory until `finish_supervisor_turn`, so crash recovery lost it. The
legacy parser now always returns unavailable, and the turn writer obtains usage
only from the independently captured transport receipt.

The repaired path is:

```text
begin_supervisor_turn (fresh Runtime turn UUID)
  -> create invocation identity
  -> existing codex exec --json subprocess
  -> stdout pipe reader: allowlisted turn.completed.usage only
  -> durable usage capture before decision accounting
  -> live finish OR existing transaction recovery
  -> SUPERVISOR-TURN-OBSERVABILITY-V1 usage projection
  -> Console capture/identity/hash verification
  -> Timeline / Task Detail / project reported sums and field coverage
```

**Executor, investigated independently.** `PREPARE_ZCODE_AUTOMATION.ps1` renders
the scheduled-automation prompt for the external ZCode UI and selects a compatible
GLM-5.3-family variant. Runtime does not invoke that provider or receive its
execution-result object. The automation reads `TO_ZCODE.md`, acquires the
`MESSAGE_ID / TASK_ID / STAGE_ID / ATTEMPT / NONCE` claim, performs work, and calls
`scripts/executor_completion.py commit`. The host-owned completion ledger binds
completion and artifact hashes to that authorized attempt. Neither the setup
adapter, claim helper, scheduled-automation handoff, nor completion API receives
provider token counters, a ZCode host execution ID, or a trusted usage callback.

There are therefore **no supported ZCode token fields** to capture. Runtime never
reads arbitrary receipt extensions, model prose, claim-token strings, or account
usage as token telemetry. A future host/provider callback with an immutable
execution identity would be a new upstream integration, outside this fix. This
finding is scoped to the integration implemented here, not a claim that every
ZCode product/interface lacks usage data.

## Authority, storage and execution identity

`scripts/provider_usage.py` captures stdout through an OS pipe while the existing
`subprocess.run` retains its stdin, timeout, exit-code and exception behavior.
Only the top-level CLI event envelope is parsed. Tool output and agent messages
inside `item.*` cannot become usage, even if their text contains plausible JSON.
Lines are bounded; large item payloads are drained without storing their contents.
Duplicate JSON keys, invalid known counters, missing completion events and
invalid lifecycle ordering produce unavailable usage. Unknown fields are ignored
without persisting their names or values or assigning them a meaning. Known
fields that are absent remain absent/null; actual integer zero remains zero.

Each turn has two create-only files under `control/supervisor_usage`:

- `<turn_id>.invocation.json`: `schema: CODEX-EXEC-USAGE-V1`, Runtime `turn_id`,
  `PROJECT_ID`, and a fresh Runtime-generated `execution_id` UUID. Created before
  starting the subprocess.
- `<turn_id>.json`: the same schema, `invocation_sha256`, the CLI's `thread_id`,
  and only the supplied allowlisted `usage` fields. Captured when the completed
  event is received, independently of the decision commit.

Publication writes/fsyncs a temporary file and atomically creates a hard link at
the final name without replacing an existing file. Storage errors disable
telemetry while the pipe continues draining and normal execution proceeds.
Filesystems that cannot support this create-only publication leave usage
unavailable. No raw provider stream, prompts, tools, headers, API keys, or secrets
are written into these records.

The existing turn-record top-level schema is preserved. Its usage block includes
`reported: true`, `status: provider_reported`, source
`codex_exec_json_turn_completed`, supplied counters, `execution_id`, `thread_id`,
and canonical `evidence_sha256`. `total_tokens` remains null. Unavailable records
retain the historical false/null block; Console explicitly projects
`status: unavailable`.

Readers derive capture paths exclusively from the turn identity, validate the
invocation's project/turn/UUID binding and hash, and compare the captured
projection with the immutable turn record. A mismatched capture is unavailable.
A turn file copied under another filename is unusable rather than counted again.
These hashes detect corruption and mismatched bindings; they are not signatures
against an attacker able to rewrite all Runtime authority files. The existing
Runtime-owned filesystem trust boundary is unchanged. Normal UI consumers have
read-only usage endpoints and no telemetry write API.

## Recovery and accounting

The accounting unit is **one fresh Codex exec process for one Runtime Supervisor
turn UUID**, with its own execution UUID. `invoke_codex` begins a new turn before
each subprocess call. It never resumes/reuses another turn's invocation capture.
The CLI's completed event describes the whole exec turn, not each tool command.

| Event | Accounting behavior |
| --- | --- |
| Completed usage, crash before decision commit | Recovery reads the existing capture for the same turn. One count. |
| Crash after decision receipt commit | Existing transaction recovery reuses the decision receipt and captured usage. One count. |
| Repeated recovery/replay after the turn record exists | Create-only turn record is unchanged; aggregation reads it once. |
| No committed decision, followed by a real retry | A new turn and execution UUID are created; both completed usage captures count, even if the first decision failed. |
| Timeout/nonzero exit after a completed usage event | Captured usage survives independently of the failed process/task outcome; recovery can project it. |
| Timeout/crash before a valid event is captured | Usage remains unavailable. No reconstruction from prompts, elapsed time or logs. |
| Usage storage or parsing failure | Existing task execution and recovery semantics continue; usage is unavailable. |

The receipt becomes durable when Runtime captures/fsyncs the terminal event, not
at the unknowable instant a provider sends bytes. A crash before that capture
can lose telemetry; it is never compensated with an estimate. An interrupted
turn appears in finished-turn aggregation when existing Runtime reconciliation
writes its turn record. No additional invocation is launched for observability.

## Console and historical compatibility

Supervisor list/detail, Timeline and Task Detail retain the same endpoints and
decision linkage. They show the actual supplied fields, source, and invocation
identity; missing categories are not rendered as zero. Cached input and reasoning
output retain their names. Executor usage is explicitly Not reported in Timeline,
Task Detail and project summary.

Project Supervisor aggregation filters to the active project's turn records when
an active-project identity exists. It reports reporting turns versus readable
finished turns, plus separate coverage for every counter. Every field sums only
turns reporting that field. Incomplete turn or field coverage, unusable records
or bounded-read limitations result in the **Partial reported sums** label and
honesty notes. Unsupported totals are null, so total-based outlier alerts have
no comparable sample; invented legacy totals cannot trigger them.

Executor coverage is `0 of Y authorized rounds`, where Y is the number of distinct
mechanically verified authorized `MESSAGE_ID` dispatches in the active project's
archive. This includes authorized rounds that were never claimed or failed, and
does not pretend to count opaque ZCode automation wakeups. Unverifiable records
are separately reported; an unavailable project identity gives an unavailable
denominator.

`C:\general-agent-runtime-v13-dogfood` was used only for read-only validation.
It still reports **Supervisor 0 of 7; Executor 0 of 5**. All counters remain null.
SHA-256 checks of 48 historical authority files before/after the reader validation
were identical; the manifest is in
[`historical-readonly-validation.json`](../evidence/dogfood-fix-04/historical-readonly-validation.json).
No historical turn was rewritten or backfilled. Even old records claiming usage
from model-authored final text are displayed as unavailable without changing
their stored bytes. There was no dogfood deployment or project rerun.

## Validation and changed files

The focused command was:

```powershell
python -m unittest -v scripts.test_provider_usage scripts.test_supervisor_observability scripts.test_web_console_supervisor scripts.test_web_console_supervisor_http scripts.test_web_console_supervisor_frontend scripts.test_web_console_runtime_create scripts.test_web_console_p9_http
```

Result: **159 tests in 44.257s — OK**, with no skips. Full individual test output
is in [`focused-tests.txt`](../evidence/dogfood-fix-04/focused-tests.txt).
The standalone entry-point check `python scripts/test_dispatch_validation_recovery.py`
ran **14 tests in 1.785s — OK**. The additional compatibility subset ran
**72 tests in 27.931s — OK**. The Node frontend smoke check passed all 11 assertions
against the actual JavaScript functions and parsed every inline script.

The complete regression command was:

```powershell
python -m unittest discover -v -s scripts -p 'test_*.py'
```

Result: **1,315 tests in 390.192s — OK (skipped=3)**: 1,312 passed,
0 failures, 0 test errors. Complete output is in
[`full-regression.txt`](../evidence/dogfood-fix-04/full-regression.txt).
The three existing skips are `test_h1_harness_rebinds_active_project_file`,
`test_h2_original_suite_green_with_real_pointer`, and
`test_h5_h8_real_candidate_preflight` from `test_g5a5_1_hotfix`, all because this
clean development Runtime has no active project pointer. The existing Windows
HTTP/lifecycle test harness emitted resource/shutdown diagnostics preserved in
the log; unittest completed successfully. `git diff --check` also passed.

The lifecycle regression suite removes the development Console's tracked
`web_console_data/instance.json`; that file was restored byte-for-byte from the
initial clean checkout after testing and is not part of this fix.

Changed production files:

- `orchestrator.py`: enable JSONL and capture stdout; reject final-message usage.
- `scripts/provider_usage.py`: new bounded transport capture, immutable storage and verification.
- `scripts/supervisor_control.py`: turn writer reads invocation-bound captures for live and recovered turns.
- `scripts/web_console_supervisor.py`: evidence validation, historical compatibility, project/field coverage and Executor denominator.
- `scripts/web_console_history.py`: explicit unavailable Executor usage in round/detail projections.
- `scripts/web_console_runtime_create.py`: require the capture module in new Runtime copies.
- `scripts/web_console_fresh_install.py`: include the module in the operational release fingerprint.
- `web_console/index.html`: actual categories, source/identity, partial sums and coverage.

Changed/new regression files:

- `scripts/test_provider_usage.py`
- `scripts/test_supervisor_observability.py`
- `scripts/test_web_console_supervisor.py`
- `scripts/test_web_console_supervisor_http.py`
- `scripts/test_web_console_supervisor_frontend.py`
- `scripts/test_web_console_p9_http.py`
- `scripts/test_web_console_runtime_create.py`

Documentation:

- `docs/DOGFOOD_FIX_04_TOKEN_TELEMETRY.md`
- `docs/WEB_CONSOLE_DEVELOPMENT.md`

Evidence (all under `evidence/dogfood-fix-04/`):

- `live-cli-probe.json`
- `historical-readonly-validation.json`
- `frontend-smoke.cjs`
- `frontend-smoke-result.json`
- `focused-tests.txt`
- `compatibility-tests.txt`
- `standalone-dispatch-tests.txt`
- `full-regression.txt`

 Tests exercise a real subprocess stdout pipe,
real control-plane begin/finish/recovery and HTTP readers; only the provider
responses in regression fixtures are synthetic. ZCode has only unavailable-path
fixtures. The live CLI probe above is separately identified as real telemetry.

The fix does not change authorization, claim/fence, publication, completion
commit/seal, Supervisor transaction recovery decisions, STOP, HUMAN_REVIEW,
Final Verification, prompts, routing or provider choice. No push, merge, tag,
release or parent Builder Runtime modification was performed.
