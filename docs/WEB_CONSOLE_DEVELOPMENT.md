# Web Console Development (v1.3 P1 skeleton + P2 Registry + P3 Cockpit core + P4 Timeline/Task Detail + P5 Human Control + P6 Artifact Center + P7 Setup Wizard)

Status: **P7 (four-step Project Setup Wizard)** on top of P6 (read-only Artifact Center + artifact-triggered feedback)
top of P5 (mutation-capable Human Control), P4 (read-only Timeline + Task
Detail), P3 (read-only Project Cockpit foundation), P2 (Runtime Registry +
opaque-ID status routing), and the P1 backend skeleton, built on the stable
v1.2 control plane. P6 adds the dedicated, root-scoped Artifact Center:
artifact discovery over the active project's three authorized publication
roots (`workspace/`, `evidence/`, `reports/`) joined with authoritative
completion-ledger receipts, bounded read-only previews for exactly the
v1.3.0 formats (Markdown, TXT, LOG, common code files, PNG, JPG/JPEG, WebP,
CSV, JSON, PDF), hash-and-magic verification before any content is served,
honest provenance (verified ledger binding, unbound, missing), search and
filters with deterministic pagination, MESSAGE_ID linkage into the accepted
Timeline/Task Detail surfaces, and artifact-triggered STEER/Deep Review that
reuses the P5 formal intervention surface with references only. Every
mutation still delegates to the existing formal v1.2 entry points
(`supervisor_control.py pause | resume | intervene`,
`STOP_AGENT_SYSTEM.ps1`, `resume_human_review.py prepare | apply`); the Web
layer never writes project_state.json, intervention files, STOP artifacts,
claim/fence state, completion ledgers, or Human Decision receipts itself,
and the browser remains read-only for artifacts (no edit/rename/delete/
upload surface exists at all). P7 adds the four-step Project Setup
wizard for an **already registered** Runtime: deterministic Goal validation
with draft storage, a metadata-only input inventory, an honest Supervisor
configuration draft, the canonical ZCode Automation prompt with explicit
human acknowledgement, the ten fail-closed readiness checks, and
bootstrap/start delegated to the Runtime's own formal entry points
(`start_project.py`, `START_AGENT_SYSTEM.ps1`) with no shell, no direct
authoritative-state writes, and no overwrite of existing projects or Goal
Anchors. Notifications, P9 alert heuristics, and Runtime creation/template
copy (deferred to P9) still do not exist in this tree.

## 1. What exists after P9

| Path | Purpose |
|---|---|
| `scripts/web_console_server.py` | Localhost-only, dependency-light HTTP backend (Python stdlib only); P1 routes, the Registry API, opaque-ID status routing, the P3 Cockpit route, and the P4 Timeline/round routes. |
| `scripts/web_console_registry.py` | Console-owned persistent Runtime Registry: storage, fail-closed validation, bounded compatibility probe, CRUD semantics. |
| `scripts/web_console_state.py` | P3 pure deterministic interpreter: status document → Current Execution / Next Expected / milestones / Runtime Health. No clock, no AI, no filesystem, no subprocess. |
| `scripts/web_console_history.py` | P4 pure projection layer: control-plane history documents → round-grouped Timeline/Task Detail presentations with deterministic pagination/order/search/filter and structured honesty blocks. No clock, no AI, no filesystem, no subprocess. |
| `scripts/web_console_control.py` | P5 pure Human Control layer: typed fail-closed request schemas, the Pending Controls projection over authoritative status/intervention facts, the STOP-challenge evaluation, and the HUMAN_REVIEW presentation block. No clock, no AI, no filesystem, no subprocess. |
| `scripts/web_console_artifacts.py` | P6 pure Artifact Center layer: artifact path normalization (authorized roots only), format classification, fail-closed query parsing, the index/catalog projection over ledger receipts plus walk results, bounded preview documents, and the artifact-feedback schema/binding checks. No clock, no AI, no filesystem, no subprocess. |
| `web_console/index.html` | Three-column desktop-first Cockpit with the live Timeline panel, the Task Detail drawer, the P5 Human Control / Pending Controls panels, the P6 Artifact Center workspace, the P7 Setup Wizard, the P8 Supervisor turns & usage panel, and the P9 soft-alerts / settings & operator-note / notifications / Recent Artifacts / Runtime-create panels (single same-origin file, no frameworks, no external resources). |
| `START_WEB_CONSOLE.ps1` | Single-instance launcher: start → wait for health → open browser. |
| `STOP_WEB_CONSOLE.ps1` | Verified, idempotent stopper for this Runtime's console instance. |
| `scripts/test_web_console_backend.py` | Backend unittest suite (P1 surface, unmodified). |
| `scripts/test_web_console_scripts.py` | Lifecycle script unittest suite (Windows, `-NoBrowser` only, unmodified). |
| `scripts/test_web_console_registry.py` | Registry unittest suite (P2 surface, unmodified). |
| `scripts/test_web_console_state.py` | P3 interpreter decision tables, fail-closed matrix, honesty/determinism tests. |
| `scripts/test_web_console_cockpit.py` | P3 Cockpit route tests (unmodified). |
| `scripts/test_web_console_frontend.py` | P3 static frontend tests (unmodified). |
| `scripts/test_web_console_history.py` | P4 projection-layer tests: query validation, grouping, status derivation, ordering/pagination/search/filter, honesty, determinism. |
| `scripts/test_web_console_timeline.py` | P4 HTTP tests: timeline/round routes, byte-exact dispatch relay, decision-receipt verification, isolation, adversarial IDs, fail-closed control-plane output. |
| `scripts/test_web_console_timeline_frontend.py` | P4 static frontend tests: Timeline panel + drawer markers, safe text-only rendering, no external resources, retained P1/P3 anchors. |
| `scripts/test_web_console_control.py` | P5 pure-layer tests: request schema matrix, Pending Controls projection, STOP-challenge evaluation, HUMAN_REVIEW presentation. |
| `scripts/test_web_console_control_http.py` | P5 HTTP tests: pause/resume/intervention argv contracts, refusal/timeout mapping, STOP challenge flow (real PS1), Human Decision prepare/apply, isolation, adversarial IDs. |
| `scripts/test_web_console_control_frontend.py` | P5 static frontend tests: Human Control panel, Pending Controls panel, high-risk STOP affordances, text-only rendering. |
| `scripts/test_web_console_artifacts.py` | P6 pure-layer tests: path normalization matrix, format classification, query bounds, index/catalog determinism and honesty, preview states, feedback schema and binding. |
| `scripts/test_web_console_artifacts_http.py` | P6 HTTP tests: catalog/detail/preview/raw routes over stub fixtures with real artifact files, hash/magic/polyglot/oversized/non-UTF-8 refusals, isolation, feedback argv contract and refusals, method allowlists. |
| `scripts/test_web_console_artifacts_frontend.py` | P6 static frontend tests: Artifact Center markers, filters, preview states, provenance labels, feedback form, text-only rendering, per-Runtime reset. |
| `scripts/web_console_setup.py` | P7 pure Setup Wizard layer: Goal validator decision table, Supervisor config schema + capability honesty, input-request schema + inventory projection, canonical ZCode prompt rendering, readiness decision table, bounded context pack/startup prompt composition, draft schema. No clock, no AI, no filesystem, no subprocess. |
| `scripts/test_web_console_setup.py` | P7 pure-layer tests: Goal validator decision table (Unicode, malformed Markdown, hostile publication roots, routing-internals warnings), config schema, capability honesty, inventory projection, prompt rendering (BOM/placeholder fail-closed), readiness matrix, workshop pack bounds, draft normalization. |
| `scripts/test_web_console_setup_http.py` | P7 HTTP tests: all setup routes over the wire, real `start_project.py` bootstrap and real preflight in disposable Runtime fixtures, refusal matrices (traversal, dot segments, reparse points, cross-root bindings, existing projects, occupied active projects), racing start requests, restart persistence, method allowlists, canonical prompt byte-equivalence with the official PS1 helper. |
| `scripts/test_web_console_setup_frontend.py` | P7 static frontend tests: four-step wizard markers, exact endpoints and value spaces, honest capability/state copy, bounded inputs, fail-closed start confirm, text-only rendering, per-Runtime reset, no setup polling. |
| `scripts/web_console_supervisor.py` | P8 pure Supervisor-observability layer: strict SUPERVISOR-TURN-OBSERVABILITY-V1 record validation, bounded turns/detail/usage projections, receipt-binding verification, and the configuration document/view. No clock, no AI, no filesystem, no subprocess. |
| `scripts/web_console_settings.py` | P9 Console-owned settings store and operator note: global defaults + per-Runtime overrides with deterministic precedence and source labels, range/unknown-field/threshold-consistency validation, fail-closed atomic storage (corrupt stores are never reset), and the bounded UI-only operator note. |
| `scripts/web_console_alerts.py` | P9 pure deterministic soft-alert layer: stable-identity alerts with severity vocabulary, notification classification, evidence-derived facts, honest unavailable/suppression handling, and the reported-only token-usage outlier rule. No clock, no AI, no filesystem, no subprocess. |
| `scripts/web_console_runtime_create.py` | P9 Registry-driven Runtime creation: the server-controlled supported-release template catalog, destination/label validation, the copied/excluded skeleton contract, reparse-point defenses, copy verification, and marker-checked rollback. |
| `scripts/test_web_console_supervisor.py` / `test_web_console_supervisor_http.py` / `test_web_console_supervisor_frontend.py` | P8 pure/HTTP/static-frontend test suites (unmodified in P9). |
| `scripts/test_web_console_settings.py` | P9 pure settings tests: defaults, validation matrix, precedence/override semantics, operator-note bounds, corruption and atomicity. |
| `scripts/test_web_console_alerts.py` | P9 pure alerts tests: every rule and severity, stable identities, notification classes, unavailable/suppressed honesty, outlier rule, timestamp parsing. |
| `scripts/test_web_console_runtime_create.py` | P9 create-contract tests: template catalog, allowlist/exclusion contract, destination/conflict/parent validation, junction refusal, copy verification, rollback honesty. |
| `scripts/test_web_console_p9_http.py` | P9 HTTP tests: settings/notes/alerts/create over the wire, validation matrices, corruption, restart persistence, isolation, concurrency, rollback, source immutability, method allowlists. |
| `scripts/test_web_console_p9_frontend.py` | P9 static frontend tests: panel markers, bounded endpoints, honest copy, notification permission single-gesture pin, dedupe, reset-on-switch, text-only rendering, and the script-parse-integrity regression pin. |

## 2. Architecture and threat boundary

```
Browser (same-origin) ──127.0.0.1 only──> web_console_server.py
                                             │  argument-vector subprocess,
                                             │  bounded timeout, --root bound
                                             ▼
                              scripts/supervisor_control.py status --json
                                             │ read-only query
                                             ▼
                              v1.2 Runtime state (control/, projects/)
                                             │
                              web_console_state.interpret_status()
                                             │ pure, offline, deterministic
                                             ▼
                              Cockpit document (current execution, next
                              expected, milestones, health facts)
```

- **Loopback only.** The server refuses any bind address other than the exact
  string `127.0.0.1` before opening a socket (`0.0.0.0`, `::`, LAN IPs, and
  `localhost` are all rejected).
- **Host validation.** HTTP/1.1 `Host` must be exactly `127.0.0.1` or
  `127.0.0.1:<port>`; anything else gets `403 HOST_HEADER_REJECTED`; a missing
  Host gets `400`.
- **Read-only against managed Runtimes.** The P3 surface adds no mutation
  endpoint. `POST` on the cockpit route is `405`. The only Console writes
  remain its own non-authoritative data under `web_console_data/`.
- **Bounded control-plane calls.** All status/cockpit data flows through the
  same argument-vector `supervisor_control.py --root <root> status --json`
  subprocess with a bounded timeout; only the JSON document on stdout is
  interpreted; terminal screen text is never parsed.
- **Opaque-ID root binding (P2, reused by P3).** A request's Runtime Root is
  resolved exclusively by looking up the route's 16-hex-character Registry ID
  on the server. No route accepts a filesystem path as a selector; query
  parameters and bodies cannot rebind the root; malformed or unknown IDs fail
  closed with `404`.
- **Registry mutations are bounded.** Only `add` accepts a root path and it
  must validate as an existing compatible Runtime Root. No endpoint lists
  directories, reads files, runs commands, or touches authoritative Runtime
  state.

## 3. Runtime Registry (P2)

### Storage

`web_console_data/runtime_registry.json` — owned by the Web Console
installation, never by a managed Runtime. Updates are atomic (unique temp
file + fsync + `os.replace`), Unicode-safe (UTF-8, `ensure_ascii=False`), and
serialized in-process by a lock. A corrupt store makes every Registry
operation fail closed with `500 REGISTRY_CORRUPT`; it is never silently
reset or overwritten.

Document schema (v1):

```json
{"schema_version": 1, "updated_at": "2026-09-12T00:00:00+00:00",
 "runtimes": {"<16 hex chars>": {
   "id": "<16 hex chars>", "root": "C:\\...\\runtime-root",
   "label": "…", "added_at": "…", "updated_at": "…",
   "validation": {"ok": true, "checked_at": "…",
                  "status_schema_version": 1, "probe_elapsed_ms": 71}}}}
```

### Validation (add and revalidate)

Fail-closed checks, in order: label syntax (1–120 chars, no control
characters, Unicode allowed), absolute non-empty root path, root exists, root
is a directory, duplicate canonical root (path resolved and case-normalized),
control script present (`scripts/supervisor_control.py`), and a bounded
read-only status probe that must answer one JSON document with a supported
`schema_version` (currently 1). A failed revalidate records the failure in
the entry's `validation` block and returns the structured error.

### Runtime creation is out of scope

The Registry only registers **existing** Runtime Roots. There is no
create-from-release operation, no disk or directory scanning, and no
template copy; that capability is deferred to P9 by the product plan.

## 4. P3 — deterministic state interpretation and the read-only Cockpit

### `web_console_state.interpret_status(status) -> dict`

A pure function from one `supervisor_control status --json` document to the
interpretation document (`schema_version` 1). Guarantees:

- **Deterministic:** no clock, randomness, model calls, filesystem, or
  subprocess access; the same input always yields the same output.
- **Fail closed:** unknown enumeration values, wrong field types, missing
  required fields, and contradictory fact combinations all produce the
  explicit `STATE_UNAVAILABLE` family with machine-readable reasons in
  `honesty` (`unknown_fields`, `malformed_fields`, `contradictions`,
  `notes`) — never a guessed state. Human-safety flags (`stop`,
  `human_review`) remain surfaced through `user_action_required` even when
  the rest of the document is unusable.
- **Facts only:** no percentages or token usage are ever emitted (the words
  cannot appear in the output). `since` is reported only when the status
  document itself carries an authoritative timestamp — the v1.2 control
  `pause` record (`pause.requested_at` for a requested pause,
  `pause.paused_at`/`pause.requested_at` for paused). Every other family
  reports `since.available: false`.
- **Backward-compatible semantics:** a missing or empty `pause.status`
  defaults to `RUNNING` exactly like v1.2's `pause_status()`; a missing
  `IS_FINAL_VERIFICATION` key on an authorized dispatch means "not a Final
  Verification task".

Interpretation shape (stable keys):

```json
{
  "schema_version": 1,
  "deterministic": true,
  "state": {"family": "…", "label": "…", "detail": "…",
            "worker": {"available": true, "who": "ZCode (Executor)"},
            "since": {"available": false, "at": null, "source": null},
            "user_action_required": false, "user_action_reason": null},
  "next_expected": {"available": true, "label": "Waiting for ZCode to claim
                    MESSAGE 700126", "detail": "…", "message_id": 700126},
  "milestones": [{"key": "AUTHORIZED", "label": "Authorized", "reached": true,
                  "evidence": "active_task"}, …],
  "protocol": {"available": true, "message_id": 700126, "task_id": "…",
               "stage_id": "…", "attempt": 1},
  "final_verification": {"available": true, "is_final_verification": false},
  "health": {"project": {…}, "orchestrator": {…}, "codex": {…},
             "zcode": {…}, "current_authorization": {…},
             "current_claim": {…}, "pending_interventions": {…},
             "errors_and_warnings": […]},
  "honesty": {"unknown_fields": [], "malformed_fields": [],
              "contradictions": [], "notes": []}
}
```

### Current Execution families (decision-table precedence, top wins)

| # | Family | Determined by (bounded facts) |
|---|---|---|
| 1 | `STATE_UNAVAILABLE` | unknown/malformed/contradictory inputs; retired authorized task; catch-all |
| 2 | `STOPPED` | `stop` flag; `project_status=STOPPED`; `runtime_status=STOPPED/STOPPED_BY_USER/DEADLINE_REACHED` |
| 3 | `HUMAN_REVIEW` | `human_review` flag; `project_status=HUMAN_REVIEW`; `runtime_status=HUMAN_REVIEW` |
| 4 | `ERROR` | `runtime_status=ORCHESTRATOR_ERROR`; `project_status=BLOCKED` |
| 5 | `PAUSED` | `pause.status=PAUSED`; `project_status=PAUSED`; `runtime_status=PAUSED` |
| 6 | `COMPLETE` | `project_status=COMPLETE` (outranks a still-pending pause request) |
| 7 | `PAUSE_REQUESTED` | `pause.status=PENDING_AFTER_CURRENT_STAGE` |
| 8 | `COMPLETION_COMMITTED` | active task with `active_task_completion_status=COMPLETION_COMMITTED` |
| 9 | `RESULT_EVALUATION` | active task with `COMPLETION_CONSUMED/COMPLETION_SEALED`; or `SUPERVISOR_TURN` with a consumed MESSAGE_ID |
| 10 | `CODEX_THINKING` | `project_status=SUPERVISOR_TURN` without a consumed result |
| 11 | `ZCODE_EXECUTING` / `FINAL_VERIFICATION_EXECUTING` | `WAITING_EXECUTOR` + active task claimed (Final Verification split out by `IS_FINAL_VERIFICATION`) |
| 12 | `WAITING_FOR_ZCODE_CLAIM` / `FINAL_VERIFICATION_WAITING_CLAIM` | `WAITING_EXECUTOR` + active task unclaimed |
| 13 | `IDLE` | `WAITING_EXECUTOR` without an active task, or no recorded activity on a registered project |
| 14 | `NO_PROJECT` | no `PROJECT_ID` and no recorded activity |

Mechanically impossible combinations (for example an active task outside
`WAITING_EXECUTOR`, claimed with a completion, claimed without a claim
record, retired and claimed, or an active task without its authorization
identity) fail closed to `STATE_UNAVAILABLE` rather than being resolved by
guessing which fact "wins".

### Next Expected (no AI call)

Derived solely from the family and the task identity, e.g. claim wait →
"Waiting for ZCode to claim MESSAGE `<id>`"; executing → "ZCode will publish
its completion of MESSAGE `<id>`; Codex will evaluate it next"; Final
Verification → "…its result may lead to COMPLETE"; committed → "Codex will
evaluate the committed result of MESSAGE `<id>`"; safe pause → "The system
will pause safely after the current stage completes"; HUMAN_REVIEW → "A
human decision is required before automation can continue". Terminal and
unavailable families state their honestly bounded expectation.

### Protocol milestones as facts

When a task identity is active, the Cockpit shows
`Authorized → Claimed → Working → Published → Completion committed →
Consumed/sealed`, each mapped from an exact status field
(`active_task`, `active_task_claim_recorded`, `active_task_claimed`,
`active_task_completion_status`). **Published is always `reached: null`
("not reported")** because the v1.2 status surface exposes no publication
fact; it is never inferred.

### Runtime Health (bounded facts)

`web_console_backend` (server self-report: pid, started_at), `orchestrator`
(status + human label, or unavailable), `codex` (label from the project
status, or unavailable), `zcode` (last observed claim/consumption fact; no
timestamp exists in the status document so `at` is always null),
`current_authorization` (identity fields + source), `current_claim`
(recorded/running/retired booleans), `pending_interventions` (count), and
`errors_and_warnings` (STOP flag, HUMAN_REVIEW, ORCHESTRATOR_ERROR, BLOCKED,
DEADLINE_REACHED, retired task, pending interventions, interpreter
unavailability). Unreported facts display as unavailable; no P9 soft-alert
heuristics or notifications are computed.

### Cockpit route

`GET /api/runtimes/<id>/cockpit` (GET/HEAD only) resolves the opaque ID
through the Registry exactly like the P2 status route, runs the same bounded
status probe, and on success returns:

```json
{"schema_version": 1, "ok": true,
 "runtime": {"id": "…", "label": "…", "root": "C:\\…\\runtime"},
 "cockpit": {"schema_version": 1,
   "source": {"kind": "supervisor_control_status", "status_schema_version": 1,
              "exit_code": 0, "elapsed_ms": 63,
              "runtime_root": "C:\\…\\runtime"},
   "runtime": {"id": "…", "label": "…", "root": "C:\\…\\runtime"},
   "backend": {"status": "ok", "pid": 1234, "started_at": "…"},
   "generated_at": "…",
   "interpretation": {…as above…}}}
```

Fail-closed behavior: any control-plane failure (`502
CONTROL_PLANE_ERROR`, `502 CONTROL_PLANE_MALFORMED_OUTPUT`, `504
CONTROL_PLANE_TIMEOUT`, `502 CONTROL_PLANE_UNAVAILABLE`) is returned
verbatim as the error envelope and **no** cockpit document is produced. A
coherent status document containing unknown/contradictory values still
answers `200` with a `STATE_UNAVAILABLE` interpretation — the honest
presentation — because the control plane itself answered correctly. Unknown,
removed, traversal-shaped, and query-rebinding IDs behave exactly like the
P2 status route (404 / no cross-root attribution).

## 4b. P4 — Timeline and Task Detail

### Data sources (all bounded, read-only, argument-vector subprocesses)

- `supervisor_control.py --root <root> timeline --json` — the merged event
  list (`SUPERVISOR_DISPATCH`, `EXECUTOR_COMPLETION`, `HUMAN_INTERVENTION`);
- `tasks --message-id <id> --json` — one archived dispatch record, carrying
  `exact_dispatch` (the exact archived TO_ZCODE text) **only** when the
  archive verifies `AUTHORIZED_VALID`;
- `feedback --message-id <id> --json` — one authoritative completion-ledger
  entry including the executor receipt;
- `interventions --json` — intervention records (filtered per round).

The browser can never submit, infer, or override a Runtime Root path or a
file path: the subprocess root comes exclusively from the Registry entry
resolved by the opaque route ID.

### `GET /api/runtimes/<id>/timeline` → 200 / 400 / 502 / 504

Query parameters (all optional; unknown, duplicated, or out-of-bounds values
→ `400 INVALID_QUERY_PARAM`):

| Parameter | Default | Bounds |
|---|---|---|
| `page` | 1 | integer 1..10,000,000 |
| `page_size` | 20 | integer 1..100 |
| `order` | `newest` | `newest` \| `oldest` |
| `kind` | `all` | `all` \| `dispatch` \| `completion` \| `intervention` |
| `q` | empty | ≤ 120 chars after trimming; case-insensitive substring over MESSAGE_ID / TASK_ID / STAGE_ID |

Response: `{"schema_version": 1, "ok": true, "runtime": {…},
"timeline": {…}}` where `timeline` carries the query echo, `totals`
(rounds after filtering, pages, ingested/unusable/unbound event counts), the
`rounds` page, and a structured `honesty` block.

Round grouping: one round per stable MESSAGE_ID, merging its dispatch,
completion, and interventions. Round `status` vocabulary (facts only):
`COMPLETED`, `DISPATCHED_NO_COMPLETION`, `DISPATCH_UNTRUSTED`,
`COMPLETION_UNTRUSTED`, `COMPLETION_WITHOUT_DISPATCH`, `INTERVENTION_ONLY`,
`CONTRADICTORY_HISTORY`. Integrity verdicts (`AUTHORIZED_VALID`,
`UNAUTHORIZED`, `INCOMPLETE`, `CORRUPT`, `OK`, `HASH_MISMATCH`) are relayed
verbatim; corrupt/contradictory records are surfaced in `honesty` (bounded
lists plus exact counts), never silently skipped. A duration is derived only
from two authoritative ISO timestamps (`archived_at`, `COMMITTED_AT`);
rounds without any authoritative timestamp sort last in both orders.

### `GET /api/runtimes/<id>/rounds/<message-id>` → 200 / 404 / 502 / 504

The message id must be 1..9 ASCII digits (route pattern; anything else is
`404 ROUTE_NOT_FOUND`). The composition relays:

- **dispatch** — integrity verdict plus, for `AUTHORIZED_VALID` archives
  only, `exact_dispatch` verbatim; archive hashes and file references;
- **completion** — the authoritative ledger entry summary and the full
  executor receipt (status, outcome, committed/consumed/sealed timestamps,
  commit id, ledger file);
- **interventions** — the records targeting this MESSAGE_ID (bounded);
- **artifacts** — read-only metadata/provenance from the receipt's
  `PUBLISHED_PATHS` (path + sha256; content previews are deferred to the
  Artifact Center stage);
- **decision** — the Supervisor decision receipt, presented only when its
  canonical SHA-256 matches the `decision_receipt_sha256` recorded in the
  dispatch archive metadata and its candidate binding matches the dispatch
  identity. The receipt file is resolved server-side from the archive
  record's own `supervisor_turn_id` (strict charset) inside the Runtime's
  `control/supervisor_decisions/`; the browser can never influence the path.
  Refusal reasons are structured codes: `SUPERVISOR_ORIGIN_UNAVAILABLE`,
  `SUPERVISOR_TURN_ID_INVALID`, `DECISION_RECEIPT_MISSING`,
  `DECISION_RECEIPT_TOO_LARGE`, `DECISION_RECEIPT_INVALID`,
  `DECISION_RECEIPT_HASH_MISMATCH`, `DECISION_RECEIPT_BINDING_INVALID`;
- **honesty** — per-source control failures (`*_TIMEOUT`,
  `*_LAUNCH_FAILED`, `*_OUTPUT_TOO_LARGE`, `*_UNPARSEABLE`), notes, and
  identity contradictions.

No authoritative record for the MESSAGE_ID → `404 ROUND_NOT_FOUND` with the
honesty block as detail. All three history sources failing at the transport
level → the deterministic control-plane error envelope (including
`CONTROL_PLANE_OUTPUT_TOO_LARGE` when one subprocess exceeds 16 MiB of
output).

## 4c. P5 — Human Control (mutation-capable)

### Design boundary

```
Browser ──127.0.0.1 only──> index.html (Human Control + Pending Controls panels)
                              │  POST /api/runtimes/<id>/controls/…  (typed JSON)
                              ▼
               web_console_server.py  (P2 opaque-ID root binding, 64 KiB body cap)
                              │  argument-vector subprocesses, bounded timeout,
                              │  root always from the Registry entry
                              ▼
               existing formal v1.2 entry points only:
                 supervisor_control.py pause | resume | intervene --json
                 STOP_AGENT_SYSTEM.ps1            (fixed -File invocation)
                 resume_human_review.py prepare | apply
                              ▼
               v1.2 Runtime state (fence-serialized, crash-safe transactions)
```

- **Pure layer.** `web_console_control.py` validates every request body
  against a narrow typed schema (exact key sets, bounded strings, no control
  characters, booleans not truthy integers), projects the Pending Controls
  document from authoritative facts only, evaluates STOP confirmations, and
  composes the HUMAN_REVIEW presentation. It never touches clock, disk,
  randomness, or subprocesses.
- **Mutations are delegation, never imitation.** The Console builds one
  argument vector per action (`--root <registry root>` always first,
  `--text=<comment>` joined-token form so a leading dash cannot become an
  option) and maps the helper's structured outcome: exit 0 with a document →
  200; a structured `ok:false` refusal → `409 CONTROL_ACTION_REFUSED`;
  timeout → `504 CONTROL_PLANE_TIMEOUT`; launch failure → `502
  CONTROL_PLANE_UNAVAILABLE`; oversized/unparseable output → `502
  CONTROL_PLANE_OUTPUT_TOO_LARGE` / `CONTROL_PLANE_MALFORMED_OUTPUT`.
- **Cooperative interruption.** Interrupt is an option of Pause
  (`--interrupt-current-task`) and of interventions. Outcomes are surfaced
  verbatim from the Runtime's disposition (`PRESERVE_RUNNING`,
  `COMPLETION_COMMITTED_WINS`, `PAUSED_UNCLAIMED_RETIRED`,
  `PAUSED_CURRENT_REVOKED`, `UNCLAIMED_TASK_RETIRED`,
  `PENDING_NEXT_SUPERVISOR_TURN`), never invented. The Console never kills
  processes; interruption revokes authority through the Runtime's own
  fencing.
- **Formal STOP.** `stop/prepare` issues a server-issued, in-memory,
  single-use challenge (32-hex id + 32-hex token, 120 s TTL, ≤ 8 pending)
  bound to the Runtime ID, the active PROJECT_ID, and the SHA-256 of the
  project's `project_state.json` at issuance. `stop/confirm` re-probes
  status, re-hashes the state file, and evaluates in a fixed order
  (unknown → replayed → expired → token → project → already-stopped →
  state-changed); every mismatch is `409 STOP_CONFIRM_REJECTED` with a
  reason and nothing is applied. A confirmed challenge is consumed before
  the formal script runs; the fixed `powershell -NoProfile -NonInteractive
  -ExecutionPolicy Bypass -File <root>/STOP_AGENT_SYSTEM.ps1` invocation is
  the only STOP path, and success additionally requires the authoritative
  `control/STOP` artifact to exist afterwards.
- **Human Decision.** `human-review/prepare` requires an active
  HUMAN_REVIEW, validates the typed decision payload with the supported
  helper's own `validate_decision_payload`, stores the decision file and
  the prepared receipt only in the Console's non-authoritative data
  directory (`web_console_data/human_review/<runtime-id>/`), and relays
  only the receipt binding (id, SHA-256, state hash) — never paths.
  `human-review/apply` resolves the receipt server-side by pattern-checked
  ID, refuses a confirmation that does not match the stored
  `receipt_id`/`receipt_sha256` pair (`409
  HUMAN_DECISION_RECEIPT_MISMATCH`), and deletes the receipt after a
  successful apply so a replayed confirmation cannot reapply (`404`).
  Helper refusals (exit 3/4) map to `409 HUMAN_DECISION_REFUSED`.
- **Pending Controls.** `GET /controls` composes `status --json` +
  `interventions --json` + a bounded (64 KiB) server-side read of the
  Runtime's own `control/HUMAN_REVIEW` reason flag. Intervention records are
  classified as pending / consumed / needs_recovery / failed (integrity) /
  other (unknown statuses surface verbatim), newest first, capped at the 50
  most recent with a truncation note; malformed records are counted in the
  honesty block. Success is never inferred from root compatibility flags or
  UI-local state.
- **Frontend gating.** All controls stay disabled when no Runtime is
  selected, the state is `STATE_UNAVAILABLE`/`NO_PROJECT`, the control plane
  did not answer, STOP is applied, the project is COMPLETE, or (for
  Pause/Resume) the facts are ambiguous. Resume is disabled during
  HUMAN_REVIEW with an explanation that a Human Decision is required. Every
  mutation is a two-step inline confirmation; STOP additionally requires the
  issued challenge with an expiring countdown. All dynamic values render as
  text (`textContent` only).

## 4d. P6 — Artifact Center (read-only + one formal mutation)

### Data sources (all bounded; the browser can never name a path)

- `supervisor_control.py --root <root> status --json` — names the active
  project (without it, discovery refuses with `409
  ARTIFACTS_NO_ACTIVE_PROJECT` rather than guessing);
- `supervisor_control.py --root <root> feedback --json` — the authoritative
  completion ledger; only entries the control plane verified
  (`integrity == "OK"`) bind provenance, via their receipt's
  `PUBLISHED_PATHS` (path + SHA-256);
- a bounded, read-only walk of the active project's three authorized
  publication roots (`workspace/`, `evidence/`, `reports/`, ≤ 5000 files,
  depth ≤ 24): symlinks and reparse points are never followed and any entry
  whose resolved path escapes the active project is refused.

Discovery is a pure join (`web_console_artifacts.build_artifact_index`):
receipt-bound files carry verified provenance, walked files without a
receipt are honestly `unbound` (the filename alone is never proof), ledger
paths whose file is absent are cataloged with availability `missing`, paths
that cannot normalize and untrusted ledger entries are surfaced in the
honesty block, and a path republished by a later round binds to the highest
producing MESSAGE_ID with earlier rounds listed in `republished_by`. The
artifact identifier is the SHA-256 of the normalized project-relative path —
stable across restarts, revealing nothing, and the only artifact selector a
request can express (anything else than 64 hex characters is not one).

### Preview policy (bounded, verified, inert)

| Format family | Extensions | Behavior |
|---|---|---|
| Markdown / Text / Log / code | `.md .markdown .txt .log` + common code extensions | bounded UTF-8 text window (256 KiB, 5000 lines, 100k chars), rendered as inert text; not valid UTF-8 → `NOT_UTF8` |
| CSV | `.csv` | bounded table (200 rows × 64 columns) built text-only |
| JSON | `.json` | parsed then pretty-printed; malformed → `MALFORMED_JSON`, never guessed |
| PNG / JPEG / WebP | `.png .jpg .jpeg .webp` | served raw only after the receipt hash and the format magic verify; ≤ 8 MiB; exact media type + `X-Content-Type-Options: nosniff` + CSP sandbox |
| PDF | `.pdf` | metadata + download only (`Content-Disposition: attachment`, ≤ 32 MiB); in-browser PDF rendering is deliberately not enabled because embedded PDF scripts would be active content |
| everything else (incl. HTML/SVG/XML) | — | metadata and provenance only; never rendered or served |

Receipt-bound files must re-verify against the authoritative SHA-256 before
any content is interpreted; a mismatch (`ARTIFACT_HASH_MISMATCH`) or an
unavailable hash refuses the preview/raw serving instead of showing
unverified bytes. Markdown renders as plain text — no HTML conversion, so no
injection surface.

### Routes

- `GET  /api/runtimes/<id>/artifacts` — filtered, paginated catalog
  (`page`, `page_size`, `root`, `type`, `message_id`, `task`, `q`,
  `status`; unknown/duplicate/out-of-bounds → `400 INVALID_QUERY_PARAM`).
  Deterministic path-ascending order; `status` facets: `ledger`, `unbound`,
  `missing`.
- `GET  /api/runtimes/<id>/artifacts/<aid>` — detail with live stat, bounded
  hash recompute, and the honest verification verdict
  (`verified` / `hash_mismatch` / `unverified` / `unavailable`).
- `GET  /api/runtimes/<id>/artifacts/<aid>/preview` — the bounded preview
  document (per-format states `OK`/`TRUNCATED`/`NOT_UTF8`/
  `MALFORMED_JSON`/`FORMAT_MISMATCH`/`TOO_LARGE`/`UNSUPPORTED`).
- `GET  /api/runtimes/<id>/artifacts/<aid>/raw` — verified image bytes
  (inline) or PDF bytes (attachment) only; every other format is `404`.
- `POST /api/runtimes/<id>/artifacts/feedback` — artifact-triggered
  STEER/Deep Review (see below). GET → `405`; POST on the read routes →
  `405`.

New error codes: `ARTIFACTS_NO_ACTIVE_PROJECT` (409),
`ARTIFACT_UNKNOWN` (404), `ARTIFACT_UNAVAILABLE` (404),
`ARTIFACT_HASH_MISMATCH` (409), `ARTIFACT_HASH_UNAVAILABLE` (409),
`ARTIFACT_FORMAT_MISMATCH` (409), `ARTIFACT_TOO_LARGE` (413),
`ARTIFACT_UNREADABLE` (502), `ARTIFACT_RAW_UNSUPPORTED` (404),
`ARTIFACT_FEEDBACK_REFUSED` (409, with `detail.reason`).

### Artifact-triggered feedback (P5 surface reuse)

`POST /artifacts/feedback` accepts exactly `{mode: "STEER" | "AUDIT",
comment: str(1..4000), message_id: int, artifact_paths: [1..8 normalized
paths], interrupt_current: bool}`. The Console then refuses (409
`ARTIFACT_FEEDBACK_REFUSED`, reason in `detail`) unless every referenced
artifact exists and is bound to that exact MESSAGE_ID by a verified
receipt. The instruction text is composed server-side — the round, the
artifact paths as references, and the user comment — and delegated verbatim
through the P5 intervention machinery (`supervisor_control.py intervene
--text=… --mode=… --target-message-id=… [--interrupt-current-task] --json`);
the Runtime's own target validation and exactly-once semantics are
unchanged. Artifact contents and project history are never auto-injected.

## 4e. P7 — Project Setup Wizard (Goal + Inputs → Codex configuration → ZCode Automation → Readiness/Start)

### Design boundary

```
Browser ──127.0.0.1 only──> index.html (four-step Project Setup wizard)
                              │  GET  /api/runtimes/<id>/setup/…   (typed)
                              │  POST /api/runtimes/<id>/setup/…   (typed JSON)
                              ▼
               web_console_server.py  (P2 opaque-ID root binding,
                                       setup body cap 512 KiB)
                              │  status probe + real preflight + formal
                              │  entry points (argument vectors only)
                              ▼
               start_project.py  (bootstrap)   START_AGENT_SYSTEM.ps1  (start)
                              ▼
               v1.2 Runtime state (the Console never writes it directly)
```

- **Pure layer first.** `web_console_setup.py` owns the Goal validator
  decision table (errors: `PROJECT_ID_INVALID`, `PROJECT_TYPE_INVALID`,
  `PROJECT_EXISTS`, `GOAL_EMPTY`, `GOAL_NOT_TEXT`, `GOAL_TOO_LARGE`,
  `GOAL_OBJECTIVE_MISSING`, `GOAL_ACCEPTANCE_MISSING`,
  `PUBLICATION_ROOT_UNSUPPORTED`; deterministic warnings for
  MESSAGE_ID/NONCE/claim-fence/completion-ledger routing internals and
  exact turn/task counts), the Supervisor config schema, the input
  request schema and inventory projection, the canonical ZCode prompt
  rendering, the ten-check readiness decision table, the bounded context
  pack / startup prompt composition, and the draft schema.
- **Console-owned draft.** All wizard choices live in
  `web_console_data/setup/<runtime-id>/draft.json` (atomic, per-Runtime,
  non-authoritative). A corrupt draft is never reset silently: it is
  treated as empty with a `SETUP_DRAFT_UNUSABLE` honesty note. Drafts
  never leak between Runtimes because storage is keyed by the opaque ID
  and bootstrap consumes only the selected entry's root.
- **Goal intake.** One bounded Markdown document (≤ 256 KiB UTF-8,
  ≤ 100k chars, ≤ 5000 lines) selected via file picker or drag/drop and
  read locally by the browser. Invalid Goals are answered with the
  validation decision and **never stored**; a Goal naming an existing
  project (`PROJECT_EXISTS`) is refused — projects and Goal Anchors are
  never overwritten.
- **Input registration.** The user types/pastes up to 8 absolute paths per
  request. Each path must be absolute, free of dot/dot-dot segments, not
  UNC/device, not a symlink/reparse point (junctions included — the
  selection is lstat'ed before any resolve), and not inside another
  registered Runtime Root (`SETUP_INPUT_CROSS_ROOT_REFUSED`). Directories
  are walked metadata-only (≤ 512 entries, depth ≤ 8; reparse children
  skipped, never followed); contents are never read, embedded, copied,
  moved, or deleted. Missing/unreadable selections surface honestly.
- **Supervisor configuration is a draft.** v1.2 exposes no model-config
  interface and reports no provider capabilities, so model/effort/mode
  choices are stored as an explicit draft (`draft_only: true`), default
  Compact explanation mode, and the response says nothing was applied to
  the Runtime and no active turn was changed. If a future status document
  carries a well-formed `supervisor_capabilities` block, the capability
  report surfaces it verbatim instead of the honest "not reported".
- **ZCode setup.** `GET /setup/zcode` renders the Runtime's own
  `control/ZCODE_SCHEDULED_AUTOMATION_PROMPT.md` (BOM tolerated, strict
  UTF-8, all `<RUNTIME_ROOT>` placeholders replaced) — byte-equivalent to
  `PREPARE_ZCODE_AUTOMATION.ps1` for the same root (pinned by a test that
  runs the real helper). The response shows the exact Runtime Root, the
  six documented setup steps, and `automation_state.unknown_by_design`
  with a note that the Console never inspects or claims ZCode Automation
  state; acknowledgement is an explicit human POST recorded with a
  timestamp in the draft. The Console does not choose the ZCode model.
- **Goal Workshop.** `GET /setup/goal-workshop` composes a bounded
  (≤ 64 KiB), deterministic context pack from the canonical
  `docs/WEB_AI_GOAL_WORKSHOP.md` plus the selected ProjectType, the
  supported publication roots, the goal-writing rules, and the current
  input inventory (≤ 200 entries, metadata only). The Runtime Root path
  is deliberately excluded; `external_automation` is `none_by_design`.
  A startup prompt instructs the human's external Web AI; the Console
  never opens, embeds, calls, or automates any Web AI.
- **Readiness.** `GET /setup/readiness` exposes the ten specification
  checks separately, all required: `goal_loaded`, `goal_anchor`
  (creatable by the formal bootstrap — never an existing-project
  overwrite), `inputs_registered` (inventory or explicit
  none-needed), `project_type_valid` (profile present in the Runtime),
  `supervisor_config_valid` (draft), `runtime_healthy` (status probe),
  `python_requirements`, `control_plane_v12` (status schema_version 1),
  `zcode_acknowledged`, and `preflight` (the Runtime's own
  `scripts/preflight.py`, bounded, `"PREFLIGHT: OK"` required).
- **Start is fail-closed.** `POST /setup/start` takes no parameters,
  serializes per-Runtime, recomputes readiness, and answers
  `409 SETUP_NOT_READY` with the failing checks otherwise. On success it
  writes the Goal to a temporary Console-side handoff file and runs the
  formal `scripts/start_project.py` (`--root … --project-id …
  --project-type … --goal-file …`, argument vector, 120 s bound), maps
  the documented exit codes (`3` → `409 SETUP_ACTIVE_PROJECT_OCCUPIED`,
  `4` → `409 SETUP_PROJECT_EXISTS`, `5` → `409 SETUP_POINTER_INVALID`,
  `2` → `400 SETUP_BOOTSTRAP_REFUSED`), deletes the handoff file, then
  invokes the Runtime's own `START_AGENT_SYSTEM.ps1` (fixed argument
  vector, `CREATE_NO_WINDOW` detached, output to a Console-side log) and
  polls the status probe for at most 20 s, reporting
  `STARTED_VERIFIED` or `STARTED_UNVERIFIED` honestly. No shell-string
  interpolation, no direct authoritative-state writes, and no claim that
  a queued start succeeded before the Runtime reports it.

### Routes

- `GET  /api/runtimes/<id>/setup/state` — draft + recomputed Goal
  validation + capability honesty + template availability.
- `POST /api/runtimes/<id>/setup/goal` — validate + store the Goal draft
  (invalid Goals are never stored).
- `POST /api/runtimes/<id>/setup/inputs` — `{"paths": [1..8]}` or
  `{"decision": "NONE_NEEDED"}`.
- `POST /api/runtimes/<id>/setup/supervisor` — the config draft.
- `GET  /api/runtimes/<id>/setup/zcode` — canonical prompt + steps + ack
  state; `POST /api/runtimes/<id>/setup/zcode/acknowledge` — `{}`.
- `GET  /api/runtimes/<id>/setup/goal-workshop` — context pack + startup
  prompt.
- `GET  /api/runtimes/<id>/setup/readiness` — the ten checks.
- `POST /api/runtimes/<id>/setup/start` — fail-closed bootstrap/start.

New error codes: `SETUP_INVALID_PAYLOAD` (400), `SETUP_GOAL_TOO_LARGE`
(413), `SETUP_INPUT_PATH_INVALID` (400, with per-path rejections),
`SETUP_INPUT_REPARSE_REFUSED` (400), `SETUP_INPUT_CROSS_ROOT_REFUSED`
(400), `ZCODE_PROMPT_TEMPLATE_UNAVAILABLE` (502),
`ZCODE_PROMPT_TEMPLATE_INVALID` (502), `ZCODE_PROMPT_TEMPLATE_TOO_LARGE`
(413), `SETUP_NOT_READY` (409, with `failing_checks`),
`SETUP_BOOTSTRAP_REFUSED` (400), `SETUP_ACTIVE_PROJECT_OCCUPIED` (409),
`SETUP_PROJECT_EXISTS` (409), `SETUP_POINTER_INVALID` (409),
`SETUP_BOOTSTRAP_ERROR` (502), `SETUP_BOOTSTRAP_TIMEOUT` (504),
`SETUP_BOOTSTRAP_UNAVAILABLE` (502), `SETUP_START_SCRIPT_UNAVAILABLE`
(502), `SETUP_START_UNAVAILABLE` (502). Setup POST routes accept bodies
up to 512 KiB (413 `PAYLOAD_TOO_LARGE` above); every pre-existing route
keeps the original 64 KiB cap.

## 4f. P8 — Supervisor-turn observability and queued Supervisor configuration

### Design boundary

- **Runtime-owned turn records.** The v1.2 control plane now durably writes
  one `SUPERVISOR-TURN-OBSERVABILITY-V1` record per finished Supervisor
  turn to `control/supervisor_turns/<turn_id>.json` (create-only, under the
  fence lock, inside `_finish_supervisor_turn_locked`). It carries: turn
  id, project, invocation, started/finished, duration, the applied
  Supervisor configuration, exact reported token usage (or an explicit
  not-reported block), a bounded context manifest, the decision receipt
  binding (file + canonical SHA-256 + decision + decision summary +
  resulting status), the resulting dispatch candidate, intervention ids,
  and the validation/error outcome. The recovery path (crash between the
  decision commit and the accounting write) writes the same record with
  `recovered_after_crash: true`; an existing record is never overwritten.
  Dogfood Fix 04 recovers usage from the separately captured invocation receipt,
  even when the transient observation is lost. Decision receipts are unchanged.
- **Orchestrator observation.** `invoke_codex` records the effective
  model/effort, elapsed time, and the bounded context manifest
  (input classes only: supervisor rules, project state, research state,
  goal, profile, human decision receipt, interventions, executor brief,
  mechanical event — never file contents or model reasoning).
- **Authoritative usage (Dogfood Fix 04).** `codex exec --json` stdout supplies
  `turn.completed.usage`; `-o` contains model-authored text and is never a usage
  source. Runtime captures only `input_tokens`, `cached_input_tokens`,
  `output_tokens`, and `reasoning_output_tokens` when supplied. No total is
  calculated. Create-only invocation/capture files under
  `control/supervisor_usage` bind the Runtime turn UUID, execution UUID, project,
  Codex thread UUID, and canonical hashes. Live completion and recovery read the
  same capture; Console validates its binding before displaying or summing it.
  Old reported blocks without this evidence are read as unavailable, without
  rewriting history. See [the complete trace and validation report](DOGFOOD_FIX_04_TOKEN_TELEMETRY.md).
- **Explicit queued configuration (Runtime contract).**
  `supervisor_control.py queue-supervisor-config --model M --effort E`
  validates `{model, reasoning_effort}` (bounded model string;
  `SUPERVISOR_CONFIG_EFFORTS = LOW/MEDIUM/HIGH` — the domain the Runtime's
  Codex integration applies today) under the fence lock and writes
  `control/supervisor_config.json` as *pending*. `begin_supervisor_turn`
  is the only consumer: at that turn boundary pending becomes *active*
  (and stays active until replaced), the turn snapshot records the source,
  and the orchestrator applies it to the `-m`/`model_reasoning_effort`
  arguments. No configuration change can ever interrupt, replace, or
  mutate an active model call; STOP forbids queueing; a refused
  observation fails the finish call closed before any accounting mutation.
- **Console reads.** The pure layer `web_console_supervisor.py` strictly
  validates turn records (exact key sets, typed blocks, bounded shapes),
  verifies each record's decision-receipt binding against the real receipt
  file (`RECEIPT_VERIFIED` / `RECEIPT_MISMATCH` / `RECEIPT_UNAVAILABLE` —
  hostile paths are refused, never read), and projects bounded list/detail
  documents plus the usage summary. Project totals sum only reliably
  reported values and expose coverage counts; ZCode usage is always
  explicitly not reported.
- **Frontend.** A Cockpit "Supervisor turns & usage" panel (latest turn,
  honest usage summary, active-vs-pending configuration with a
  draft-prefilled queue form); Timeline rounds show the linked turn's
  profile/duration/usage when a record exists; the Task Detail Context tab
  extends the verified decision with the turn's usage and context
  manifest. Rendering stays text-only; nothing polls or auto-submits the
  configuration.

### Routes

- `GET  /api/runtimes/<id>/supervisor/turns?limit&offset` → 200 / 400
  (`TURNS_QUERY_INVALID`) — bounded, deterministic newest-first list with
  totals and surfaced unusable records.
- `GET  /api/runtimes/<id>/supervisor/turns/<turn-id>` → 200 / 400
  (`TURN_ID_INVALID`) / 404 (`TURN_NOT_FOUND`) / 502
  (`TURN_RECORD_UNUSABLE`) — bounded detail with the context manifest.
- `GET  /api/runtimes/<id>/supervisor/usage` → 200 — project usage summary
  (reported values only, coverage counts, ZCode `not reported`).
- `GET  /api/runtimes/<id>/supervisor/config` → 200 — active/pending
  configuration truth, capability report, supported effort set, and the
  non-authoritative setup-draft prefill.
- `POST /api/runtimes/<id>/supervisor/config` `{model, reasoning_effort}`
  → 200 (queued; includes `turn_in_flight` truth) / 400
  (`CONTROL_INVALID_PAYLOAD`) / 409 (`CONTROL_ACTION_REFUSED`, e.g. STOP)
  / 502/504 — the only mutation, delegated to the formal subcommand.

## 4g. P9 — Settings, operator notes, soft alerts, notifications, Recent Artifacts, and Runtime creation

### Design boundary

P9 adds the last pre-release feature slice on the same rules as every
earlier stage: the browser speaks only to bounded localhost APIs; every
mutation is either a Console-owned metadata write (settings, operator
note) or a delegation to a formal Runtime interface (the P8
`queue-supervisor-config` queue, the Registry); and nothing here edits
authoritative Runtime state directly.

- **Settings are Console-owned and versioned.** Global defaults live in
  `web_console_data/settings.json`; per-Runtime overrides and the operator
  note live in `web_console_data/settings/<runtime id>.json`. Keys:
  `supervisor_model`, `supervisor_reasoning_effort` (LOW/MEDIUM/HIGH or
  empty = the Runtime's fixed policy), `decision_summary_mode`
  (compact/full), `timeline_page_size` (5–100), `alert_thresholds`
  (seven bounded, range-checked values), and `notifications` (four
  opt-in informational events, four must-attention events on by
  default). Unknown fields, wrong types, out-of-range values, and
  threshold combinations that only become invalid after a merge (e.g. a
  critical expiry window above the effective warning window) are refused.
  **Precedence is deterministic:** an override wins; `null` clears back
  to the global default; every effective value carries its source
  (`global-default` / `override`), so UI and API can never blur the two.
- **A corrupted settings store fails closed** (`SETTINGS_CORRUPT`, HTTP
  500) and is never silently reset — same rule as the Registry.
- **Supervisor model/effort settings never bypass the P8 queue.** The
  settings POST only records Console defaults/overrides. Applying a value
  to a Runtime is an explicit separate action that delegates to the
  formal `POST /api/runtimes/<id>/supervisor/config` queue — pending
  survives restart, applies only at the next eligible turn boundary,
  never interrupts an active call, and is refused on STOP. The settings
  response carries this honesty note, and the Cockpit shows the active
  (P8 view) and the effective setting (source-labelled) side by side.
- **The operator note is UI-only metadata.** It is stored per Runtime
  (bounded 4000 characters, newline-normalized) and is never added to any
  Codex context, prompt, or manifest. "Convert to intervention" copies
  the note text into the existing formal Human Control form; submitting
  it there remains an explicit user action through the unchanged v1.2
  intervention path, which preserves project/Runtime attribution.
- **Soft alerts are deterministic and non-AI.** The pure layer
  `web_console_alerts.py` projects evidence — the control-plane status
  document (including two new additive keys, `supervisor_turn_inflight`
  and `active_task_completion`), the probe outcome, and the P8 usage
  document — into alerts with stable identities, Runtime attribution, a
  fixed severity vocabulary (`informational` / `warning` /
  `needs-attention`), and evidence-derived facts. Missing or unreadable
  evidence is listed in an honesty block and never fabricated. Rules:
  prolonged ZCode pickup, authorization expiry risk (warning and
  must-attention tiers), unusually long Codex turn, committed-but-
  unconsumed completion, Runtime offline (probe failure), existing
  severe/error states (HUMAN_REVIEW, STOP, ORCHESTRATOR_ERROR, BLOCKED,
  DEADLINE_REACHED, retired task), project COMPLETE, safe Pause, and the
  reported-usage outlier (below). If the status probe fails, exactly one
  offline alert is produced and all status-derived rules are suppressed
  with a note — guessing is never an option.
- **Usage outliers come only from reliably reported totals.** The rule
  flags the newest reported turn when its total exceeds
  `usage_outlier_factor` × the average of the prior
  `usage_outlier_min_sample`+ reported turns (documented deterministic
  rule, window 10). Turns without a reported total never enter the
  comparison, "not comparable yet" is surfaced while the sample is too
  small, and ZCode usage is always Not reported (the Runtime has no
  authoritative source for it).
- **Notifications degrade safely.** The alerts document classifies every
  alert as `must-attention`, `optional-informational`, or `none`.
  Browser notification permission is requested exactly once in the code,
  only from the explicit "Enable browser notifications" click. A refusal
  or an unsupported browser degrades to the visible in-Console alerts
  panel. Notifications are deduplicated per Runtime + stable alert id,
  fire only for must-attention or opted-in classes, never navigate the
  user, never open external URLs, and never automate a desktop.
- **Runtime creation is Registry-driven and allowlist-copied.**
  `web_console_runtime_create.py` owns the catalog (exactly one
  server-controlled template, `gar-local-stable`, whose source is the
  Console installation itself — the browser can never name a source
  path), the destination/label validation, the copy contract, and the
  rollback helper. The executor serializes the whole create+register
  flow under one Console lock, copies into a temporary sibling directory
  (never a partial tree at the destination), renames only after a
  complete verified copy, registers only after the Runtime's own status
  probe validates the result, and rolls back on failure — a rollback
  refuses to delete any tree whose recorded marker hash no longer
  matches. Concurrent conflicting creates fail closed (one 201, the
  loser `409 CREATE_DESTINATION_EXISTS`); no partially registered root
  can ever appear.
- **Copied/excluded template contract.** Copied: the release root files
  (`README.md`, `LICENSE`, `orchestrator.py`, the PowerShell entry
  points, `V1.3_PRODUCT_SPEC.md`, `AI_BOOTSTRAP.md`, `.gitignore`), the
  product directories (`docs/`, `profiles/`, `scripts/`, `web_console/`),
  the static control seed documents (policy/workshop/template JSON/MD
  files including `ZCODE_SCHEDULED_AUTOMATION_PROMPT.md`), and
  `handoff/PROTOCOL.md`. Never copied: `projects/`, `workspace/`,
  `evidence/`, `reports/`, `logs/`, `web_console_data/`, `.git/`,
  `TO_ZCODE.md`, `SUPERVISOR_BRIEF.md`, `ZCODE_DONE.flag`,
  `ZCODE_LAST_PROCESSED.txt`, `PROJECT_GOAL.md`, every mutable
  `control/` state file (ACTIVE_PROJECT.json, STOP, HUMAN_REVIEW,
  USER_ATTENTION.json, orchestrator_runtime.json, supervisor_* stores
  and decisions/turns directories, lock files), and every ledger/claim/
  dispatch archive under `handoff/`. `__pycache__`/`*.pyc` are excluded
  everywhere; symlinks and Windows junctions anywhere in the source walk
  or in the destination parent chain refuse the operation
  (`CREATE_SOURCE_UNSAFE` / `CREATE_DESTINATION_UNSAFE`).
- **Cockpit Recent Artifacts block.** A compact block over the accepted
  P6 catalog (bounded page, newest-first by provenance commit time, five
  items) with preview/open actions and "View all artifacts"; every open
  lands in the Artifact Center already filtered to the artifact's
  MESSAGE_ID. Rendering stays text-only; hostile strings and Unicode
  round-trip inertly.
- **Pre-existing defect found and fixed during P9.** The Cockpit script
  had contained a latent JavaScript parse error since P4 (two adjacent
  string literals with no `+` operator in the Task Detail dispatch
  drawer); because every frontend test is static-text-only, it shipped
  undetected through P8. Fixed, and the P9 frontend suite now pins the
  pattern (`test_no_adjacent_string_literals_without_operator`) plus a
  full `node --check` was run on the script during the stage.

### Routes (new in P9)

- `GET /api/settings` → 200 global settings document (defaults when
  never written) / 500 `SETTINGS_CORRUPT`.
- `POST /api/settings` `{"settings": {...partial...}}` → 200 updated
  document / 400 (`SETTINGS_INVALID_PAYLOAD`, `SETTINGS_UNKNOWN_FIELD`,
  `SETTINGS_VALUE_INVALID`, `SETTINGS_VALUE_OUT_OF_RANGE`) / 413 /
  500 `SETTINGS_CORRUPT`.
- `GET /api/runtimes/<id>/settings` → 200 effective values with sources,
  stored overrides, operator note, and the supervisor-application
  honesty note / 500 `SETTINGS_CORRUPT`.
- `POST /api/runtimes/<id>/settings` `{"overrides": {key: value|null}}`
  → 200 updated effective view / 400 (same codes) / 500. `null` clears
  an override (nested `null` clears one threshold/preference).
- `GET|POST /api/runtimes/<id>/notes` → 200 operator note (`null` when
  unset; `{"text": ""}` clears) / 400 (`SETTINGS_INVALID_PAYLOAD`,
  `SETTINGS_NOTE_INVALID`) / 500.
- `GET /api/runtimes/<id>/alerts` → 200 alert projection document
  (probe failure still 200 with the single offline alert) / 404 unknown
  id / 502/504 surfaced codes inside the document's offline evidence.
- `GET /api/runtime-templates` → 200 the server-controlled catalog with
  availability (required skeleton paths) and missing paths.
- `POST /api/runtimes/create`
  `{"template_id", "destination", "label"}` → 201 created + registered /
  400 (`CREATE_INVALID_PAYLOAD`, `CREATE_UNKNOWN_TEMPLATE`,
  `CREATE_INVALID_DESTINATION`, `CREATE_DESTINATION_CONFLICTS`,
  `CREATE_DESTINATION_PARENT_MISSING`, `CREATE_DESTINATION_PARENT_INVALID`,
  `CREATE_DESTINATION_UNSAFE`, `CREATE_LABEL_INVALID`) / 409
  `CREATE_DESTINATION_EXISTS` / 502 (`CREATE_SOURCE_INCOMPLETE`,
  `CREATE_SOURCE_UNSAFE`, `CREATE_COPY_FAILED`,
  `CREATE_VALIDATION_FAILED`) with rollback facts where a rollback ran.

Method allowlists are complete: DELETE/PUT/PATCH/OPTIONS fail closed on
all of the above, and wrong-method requests answer 405 with `Allow`.

### Storage and compatibility

New Console-owned files: `web_console_data/settings.json`,
`web_console_data/settings/<runtime id>.json`. Nothing inside any
managed Runtime tree is written except by the pre-existing formal
interfaces. The two additive status keys are purely additive; every
legacy status consumer (P3 interpreter included) is unaffected. Rollback:
delete the two new pure layers (`web_console_settings.py`,
`web_console_alerts.py`, `web_console_runtime_create.py`) and the new
tests, revert `web_console_server.py`, `supervisor_control.py`,
`web_console/index.html`, and this document to the P8 revisions, and
delete the settings store files. The created-Runtime feature leaves no
residue when unused.

## 5. API contract (v1, all responses UTF-8 JSON unless HTML)

All JSON bodies carry `"schema_version": 1`. Success bodies have
`"ok": true`; error bodies `{"ok": false, "error": {"code", "message",
"detail"}}`.

### `GET /api/health` → 200

Liveness of the backend plus a structural (subprocess-free) availability
signal for the v1.2 control plane. A missing control-plane script does not
make health fail; it is reported as `script_present: false`.

### `GET /api/status` → 200 / 502 / 504

Invokes the real control plane for the server-configured Runtime root and
wraps the exact `supervisor_control status --json` document under
`control_plane.status`. Failures surface deterministically:
`CONTROL_PLANE_ERROR`, `CONTROL_PLANE_MALFORMED_OUTPUT`,
`CONTROL_PLANE_TIMEOUT`, `CONTROL_PLANE_UNAVAILABLE`.

### `GET /api/runtimes` → 200

`{"schema_version": 1, "ok": true, "count": N, "runtimes": [entries]}`,
ordered by `added_at`. Corrupt storage → `500 REGISTRY_CORRUPT`.

### `POST /api/runtimes` → 201

Body: exactly `{"root": "<absolute path>", "label": "<string>"}`. Validates
fail-closed; refusals use the bounded Registry error codes
(`REGISTRY_INVALID_PAYLOAD`, `REGISTRY_INVALID_JSON`, `REGISTRY_INVALID_ROOT`,
`REGISTRY_ROOT_NOT_FOUND`, `REGISTRY_ROOT_NOT_A_DIRECTORY`,
`REGISTRY_NOT_A_RUNTIME_ROOT`, `REGISTRY_UNSUPPORTED_SCHEMA`,
`REGISTRY_INVALID_LABEL`, `409 REGISTRY_DUPLICATE_ROOT`).

### `GET /api/runtimes/<id>` → 200
One entry document; unknown or malformed ID → `404 RUNTIME_UNKNOWN`.

### `POST /api/runtimes/<id>/rename` → 200
Body: exactly `{"label": "<string>"}`. Changes only the label and
`updated_at`; the registered Runtime tree is untouched.

### `POST /api/runtimes/<id>/revalidate` → 200
Body: `{}` or empty. Re-runs the full validation pipeline.

### `DELETE /api/runtimes/<id>` → 200
Removes only the Registry record (`{"ok": true, "removed": {…}}`). The
registered Runtime tree is never deleted, moved, or modified.

### `GET /api/runtimes/<id>/status` → 200 / 502 / 504
Same control-plane wrapping as `/api/status`, resolved through the Registry
entry, plus `"runtime": {"id", "label", "root"}` attribution.

### `GET /api/runtimes/<id>/cockpit` → 200 / 502 / 504

See §4. GET/HEAD only; other methods → `405 METHOD_NOT_ALLOWED`.

### P5 Human Control routes

All mutation routes are POST-only (GET → `405` with `Allow: POST`), accept
exactly one JSON body within the 64 KiB cap, resolve the opaque Runtime ID
once through the Registry, and stay bound to that entry's root for the whole
operation. New error codes: `CONTROL_INVALID_PAYLOAD` (400),
`CONTROL_ACTION_REFUSED` (409), `STOP_CONFIRM_REJECTED` (409, with
`detail.reason`), `STOP_ALREADY_APPLIED` (409),
`STOP_PROJECT_UNAVAILABLE` (409), `STOP_CHALLENGE_LIMIT` (429),
`STOP_SCRIPT_UNAVAILABLE` (502), `HUMAN_REVIEW_NOT_ACTIVE` (409),
`HUMAN_DECISION_INVALID` (400), `HUMAN_DECISION_REFUSED` (400/409),
`HUMAN_DECISION_RECEIPT_UNKNOWN` (404), `HUMAN_DECISION_RECEIPT_MISMATCH`
(409), `HUMAN_DECISION_INTERNAL` (500), `HUMAN_DECISION_LIMIT` (429).

- `GET /api/runtimes/<id>/controls` → 200 / 502 / 504 — the Pending Controls
  document: `pause` facts (state `NONE|PENDING|APPLIED|UNAVAILABLE`, mode,
  timestamps, disposition, `superseded_by_completion`), `stop.applied`,
  bounded `interventions` with per-record categories and `counts`
  (pending/consumed/superseded/failed/needs_recovery/other), the
  `human_review` presentation block (active flag, reason excerpt, authorized
  task facts, project status), and a `honesty` block. POST on this route →
  405.
- `POST …/controls/pause` — body exactly `{"mode": "SAFE" |
  "INTERRUPT_CURRENT"}`; runs `supervisor_control.py --root <root> pause
  [--interrupt-current-task] --json`.
- `POST …/controls/resume` — body exactly `{}`; runs `resume --json`.
- `POST …/controls/intervention` — body exactly `{"mode": "STEER" | "AUDIT",
  "comment": str(1..4000, no control chars), "target_message_id": int |
  null, "interrupt_current": bool}`; runs `intervene --text=<comment>
  --mode=<mode> [--target-message-id=<id>] [--interrupt-current-task]
  --json`. Nonexistent/future/cross-project/untrusted targets are refused by
  the Runtime before any intervention is created and surface as
  `409 CONTROL_ACTION_REFUSED`.
- `POST …/controls/stop/prepare` — body `{}` (or none). Refuses when STOP is
  already applied. Returns the challenge (id, token, project id, TTL).
- `POST …/controls/stop/confirm` — body exactly `{"challenge_id",
  "confirmation_token", "project_id"}`. Applies the formal Runtime STOP on a
  fully verified challenge.
- `POST …/controls/human-review/prepare` — body exactly
  `{"decision_content": str(≤24000), "constraints_verbatim": [str, …]}` per
  the helper's schema; requires active HUMAN_REVIEW.
- `POST …/controls/human-review/apply` — body exactly
  `{"receipt_id": "human-decision-<32hex>", "receipt_sha256": <64hex>}`.

### Request body bounds (Registry POST routes)

Bodies must use `Content-Length` (chunked → `400
TRANSFER_ENCODING_UNSUPPORTED`; missing length → `411 LENGTH_REQUIRED`) and
are capped at 64 KiB (`413 PAYLOAD_TOO_LARGE`). The connection is
half-closed and drained (RFC 7230 §6.6) so a refused large body is always
answered deterministically.

### Error envelope example

```json
{"schema_version": 1, "ok": false,
 "error": {"code": "ROUTE_NOT_FOUND", "message": "no read-only route exists at '/api/pause'",
           "detail": null}}
```

Other codes: `HOST_HEADER_REQUIRED` (400), `HOST_HEADER_REJECTED` (403),
`METHOD_NOT_ALLOWED` (405), `STATIC_UNAVAILABLE` (500), the Registry codes
above plus `RUNTIME_UNKNOWN` (404), `REGISTRY_CORRUPT` (500),
`REGISTRY_IO_ERROR` (500), and the control-plane codes in §4.

## 6. The Cockpit frontend (P3 + P4)

`web_console/index.html` is one same-origin file (no frameworks, no external
resources, no build step) implementing the desktop-first three-column
foundation from the product specification:

- **Left (`#column-runtimes`):** the explicit Runtime selector backed only by
  Registry opaque IDs (label + id buttons), a status hint, the P1 backend
  health panel, and a clearly non-functional "New Project" placeholder.
- **Center (`#column-current`):** the Current Execution panel answering who
  is working, what they are doing, since when (only when the Runtime
  reported an authoritative timestamp), what happens next (Next Expected),
  and whether the user must act; protocol milestones; the task identity;
  a `<details>` block with bounded technical details; the **P4 Timeline
  panel** (order / page size / event-kind filter / bounded search /
  pagination / refresh controls, round cards with collapse-expand, honest
  status labels and integrity badges, and a "New events available — Jump to
  latest" banner); and the **P6 Artifact Center** workspace (replacing the
  former Recent Artifacts placeholder).
- **Right (`#column-control`):** the Runtime Health panel, the **P5 Human
  Control panel** (Safe Pause / Resume with expected-effect explanations and
  two-step inline confirmations, the bounded intervention form with optional
  historical MESSAGE_ID targeting and cooperative interrupt, the
  HUMAN_REVIEW presentation with the Runtime's own reason and the two-step
  Human Decision prepare/apply flow, and a visually distinct high-risk area
  holding Interrupt Current Task and the challenge-confirmed Formal STOP),
  and the **P5 Pending Controls panel** (authoritative pause/stop/
  intervention facts with honest categories, counts, and notes, polled every
  10 s). Controls stay disabled whenever preconditions are unavailable or
  ambiguous; Runtime switching closes every confirmation and drops any
  unconfirmed STOP challenge so state cannot leak between Runtimes.
- **Project Setup wizard (`#setup-wizard-section`, P7):** a four-step
  guided flow for the selected Runtime — Goal + Inputs (file picker and
  drag/drop bounded at 256 KiB, the deterministic validator's decision
  rendered inline, the bounded Goal Workshop context pack and startup
  prompt with copy buttons, metadata-only input registration), Codex
  Supervisor configuration (explicit draft copy with capability honesty),
  ZCode Automation setup (exact Runtime Root, canonical prompt with
  one-click copy, the six documented steps, and an explicit
  acknowledgement the Console cannot infer), and Readiness/Preflight/
  Start (the ten checks rendered separately, a two-step confirmed start,
  and an honest `STARTED_VERIFIED`/`STARTED_UNVERIFIED` result with a
  Cockpit handoff). Back/next navigation, per-Runtime reset, and no
  background polling of setup endpoints.
- **Task Detail drawer (`#task-drawer`):** opened from any round card
  (instead of navigating away), with six tabs — Overview, Codex Full
  Dispatch (the exact archived bytes in a `<pre>`), ZCode Feedback (the
  authoritative receipt, full JSON inspectable), Artifacts (metadata and
  provenance only), Context (the hash-verified Supervisor decision receipt
  or its honest refusal reason), and Protocol Details (identity, hashes,
  archive/ledger/seal file references). Closed via the Close button or
  Escape; Runtime switching closes it so no data can leak between Runtimes.

Rendering is text-only: every dynamic value is inserted with
`textContent`/`createElement` (no `innerHTML`, no `eval`, no
`document.write`), so hostile strings and Unicode render as inert text.
Periodic polling refreshes `GET /api/runtimes` (30 s), the selected Runtime's
cockpit (5 s), backend health (15 s), and the Timeline (15 s). Refreshes
preserve the user's browsing position: page, filters, open rounds, and an
open drawer are never reset by a background refresh; when a refresh of a
non-default page observes that rounds shifted underneath it, the jump-to-
latest banner appears instead of moving the user. The **P6 Artifact Center**
section offers a searchable, filterable, paginated artifact list
(root/type/status/message/task/keyword controls with the exact API value
spaces), expandable per-artifact detail with the provenance panel (Producing
MESSAGE, task/stage/producer, publication and completion facts, binding
class, verification verdict, republish linkage), the bounded preview pane
(text/table/JSON/image/download-PDF with explicit `TRUNCATED` /
`NOT_UTF8` / `FORMAT_MISMATCH` / `TOO_LARGE` / unsupported states), "Open
Task Detail" navigation into the accepted drawer, and the artifact-feedback
form (STEER / Deep Review AUDIT with a two-step confirmation, one producing
MESSAGE_ID per item, references only) posting to `/artifacts/feedback`; the
Task Detail drawer's Artifacts tab links back into the Artifact Center
pre-filtered to the round. The Artifact Center is user-driven (no periodic
polling) and resets completely on Runtime switching. All requests are
same-origin GETs with `cache: "no-store"`. A media query collapses the grid
to one column under 980 px.

## 7. Instance lifecycle

### Instance metadata (non-authoritative)

`web_console_data/instance.json`, written atomically by the server after a
successful bind. It is a hint, never authority: `web_console_server.py
verify-instance` classifies it as `MISSING` / `INVALID` / `FOREIGN` /
`STALE` / `VERIFIED` / `ALIVE_UNVERIFIED`.

### START_WEB_CONSOLE.ps1

`START_WEB_CONSOLE.ps1 [-Port n] [-RuntimeRoot dir] [-Foreground] [-NoBrowser]
[-HealthTimeoutSeconds 30]` — resolves the Runtime Root, refuses
`FOREIGN`/`ALIVE_UNVERIFIED`, cleans `STALE`/`INVALID`, starts at most one
hidden backend on a free loopback port, polls until `VERIFIED`, prints
`WEB_CONSOLE_READY pid=… port=… url=…`, and opens the browser (unless
`-NoBrowser`). The launcher never redirects the server's stdio (a detached
server inheriting a pipe write-end would keep the pipe from reaching EOF).
`-Foreground` runs in the calling console for diagnostics.

### STOP_WEB_CONSOLE.ps1

`STOP_WEB_CONSOLE.ps1 [-RuntimeRoot dir] [-WaitExitSeconds 10]` — idempotent;
only a `VERIFIED` pid is ever stopped; `INVALID`/`FOREIGN`/`ALIVE_UNVERIFIED`
are refused (exit 3). The Orchestrator and unrelated processes are never
stopped by this script.

### Cleanup contract

`web_console_data/` holds only non-authoritative Console data: instance
metadata, the Runtime Registry file, and per-start logs. Metadata is removed
on verified stop / stale-start / clean Ctrl+C; a hard-killed backend leaves
STALE metadata that the next START/STOP cleans. The Registry file is durable
by design and survives restarts; deleting it intentionally forgets all
registrations.

## 8. Running the tests

From the target tree root:

```powershell
# Focused web console set (P1-P7)
python -m unittest discover -v -s scripts -p "test_web_console_*.py"
# Individual suites
python -m unittest discover -v -s scripts -p "test_web_console_backend.py"
python -m unittest discover -v -s scripts -p "test_web_console_scripts.py"
python -m unittest discover -v -s scripts -p "test_web_console_registry.py"
python -m unittest discover -v -s scripts -p "test_web_console_state.py"
python -m unittest discover -v -s scripts -p "test_web_console_cockpit.py"
python -m unittest discover -v -s scripts -p "test_web_console_frontend.py"
python -m unittest discover -v -s scripts -p "test_web_console_history.py"
python -m unittest discover -v -s scripts -p "test_web_console_timeline.py"
python -m unittest discover -v -s scripts -p "test_web_console_timeline_frontend.py"
python -m unittest discover -v -s scripts -p "test_web_console_control.py"
python -m unittest discover -v -s scripts -p "test_web_console_control_http.py"
python -m unittest discover -v -s scripts -p "test_web_console_control_frontend.py"
python -m unittest discover -v -s scripts -p "test_web_console_artifacts.py"
python -m unittest discover -v -s scripts -p "test_web_console_artifacts_http.py"
python -m unittest discover -v -s scripts -p "test_web_console_artifacts_frontend.py"
python -m unittest discover -v -s scripts -p "test_web_console_setup.py"
python -m unittest discover -v -s scripts -p "test_web_console_setup_http.py"
python -m unittest discover -v -s scripts -p "test_web_console_setup_frontend.py"
# Complete regression (v1.2 suite plus the console suites)
python -m unittest discover -v -s scripts -p "test_*.py"
```

Script tests exercise the real PowerShell scripts with `-NoBrowser` only;
they never open a browser and never touch authoritative Runtime state.

## 9. Compatibility and rollback

- P1 added files only. P2 modified `scripts/web_console_server.py` and added
  the Registry. P3 modified `scripts/web_console_server.py` (cockpit route)
  and `web_console/index.html`, and added `scripts/web_console_state.py`
  plus three P3 test files. P4 modified `scripts/web_console_server.py`
  (two new route patterns, the timeline/round handlers, the bounded
  `run_control` helper and its deterministic failure envelopes, the
  decision-receipt verifier, the `web_console_history` import, and the
  server version string), `web_console/index.html` (Timeline panel and
  Task Detail drawer replacing the Timeline placeholder; all P1/P3 anchors
  and panels retained), and added `scripts/web_console_history.py` plus
  three P4 test files. P5 modified `scripts/web_console_server.py` (the
  eight control route patterns, the GET controls handler, the four mutation
  handlers plus STOP/Human Decision flows, `_parse_helper_document` for
  helpers that log to stdout, the in-memory STOP-challenge store, the
  `web_console_control`/`resume_human_review` imports, and the server
  version string), `web_console/index.html` (live Human Control and Pending
  Controls panels replacing their placeholders; all P1–P4 anchors, panels,
  and the drawer retained), and added `scripts/web_console_control.py`
  plus three P5 test files. One P4-era static test
  (`test_web_console_timeline_frontend.py`) was updated in its
  `test_deferred_regions_remain_non_functional` case only, because the
  Human Control / Pending Controls regions it pinned as deferred
  placeholders are now live P5 panels. No v1.2 file is modified, no v1.2
  semantics re-implemented or weakened, and every P1–P4 suite runs green.
  P6 modified `scripts/web_console_server.py` (the five artifact route
  patterns, the four read handlers plus the feedback handler, the shared
  `_submit_intervention` delegation extracted from the P5 intervention
  handler, the bounded publication-root walk, the bounded hash helper,
  `_send_raw_bytes`, the `web_console_artifacts` import, and the server
  version string), `web_console/index.html` (the live Artifact Center
  workspace replacing the placeholder, the drawer-to-Artifact-Center link,
  and the per-Runtime reset), `scripts/test_web_console_timeline_frontend.py`
  (only its `test_deferred_regions_remain_non_functional` case, because the
  Artifact Center placeholder it pinned is now a live P6 panel — the same
  documented single-pin precedent as P5), and added
  `scripts/web_console_artifacts.py` plus three P6 test files.
- P7 modified `scripts/web_console_server.py` (the nine setup route
  patterns, the six setup handlers plus the readiness/start flows, the
  per-route `max_bytes` parameter on `_read_json_body` (existing routes
  unchanged at 64 KiB), the per-Runtime start lock and draft store, the
  `web_console_setup` import, and the server version string),
  `web_console/index.html` (the live four-step Project Setup wizard
  section, per-Runtime wizard reset, and the footer endpoint list),
  added `scripts/web_console_setup.py`,
  `docs/WEB_AI_GOAL_WORKSHOP.md`, and three P7 test files, and updated
  three pre-existing static frontend pins (the "New Project" placeholder
  cases in `test_web_console_frontend.py`,
  `test_web_console_control_frontend.py`, and
  `test_web_console_timeline_frontend.py`) because that region is now a
  live panel — the same documented single-pin precedent as P5 and P6.
- Rollback of P5: revert `scripts/web_console_server.py` and
  `web_console/index.html` to their P4 revisions, restore the one
  `test_web_console_timeline_frontend.py` test case, and delete
  `scripts/web_console_control.py`,
  `scripts/test_web_console_control.py`,
  `scripts/test_web_console_control_http.py`, and
  `scripts/test_web_console_control_frontend.py`. Prepared Human Decision
  receipts live only in the Console's `web_console_data/human_review/`
  directory and can be archived or deleted by a human without touching any
  Runtime; the Console never wrote into a managed Runtime tree outside the
  formal entry points.
- Rollback of P6: revert `scripts/web_console_server.py` and
  `web_console/index.html` to their P5 revisions, restore the one
  `test_web_console_timeline_frontend.py` test case, and delete
  `scripts/web_console_artifacts.py`,
  `scripts/test_web_console_artifacts.py`,
  `scripts/test_web_console_artifacts_http.py`, and
  `scripts/test_web_console_artifacts_frontend.py`. P6 introduces no
  persistent Console-side state at all (the artifact view is rebuilt per
  request from authoritative sources), so nothing needs cleaning; artifact
  feedback interventions are ordinary Runtime-owned intervention records.
- The full v1.2 unittest discovery suite remains the standing per-stage gate
  (880 tests at P6, only the 3 pre-existing clean-copy environment skips).

## 10. Known limitations (P1–P6 scope)

- One console instance per installation directory.
- The interpreter sees only what `supervisor_control status --json` exposes.
  It derives nothing from disk scanning, terminal screens, or model calls,
  so facts outside that document (e.g. task-level publication state) are
  reported as unavailable rather than inferred.
- "Since when" is available only for pause-driven families
  (`pause.requested_at` / `pause.paused_at`); the v1.2 status document
  carries no start timestamp for dispatches, claims, or Supervisor turns.
- The PUBLISHED milestone is honestly unreported (`reached: null`): the
  status surface has no publication fact.
- `RESULT_EVALUATION` can also appear while the project is still
  `WAITING_EXECUTOR` with a consumed completion (a v1.2 crash-recovery
  window); the presentation stays factual about what is known.
- Timeline search covers MESSAGE_ID / TASK_ID / STAGE_ID only; keyword
  search inside dispatch text remains deferred. Artifact name/path search,
  filters, and previews live in the P6 Artifact Center. Filter facets that
  the v1.2 control plane does not expose as events (Codex Decision as a
  timeline event, Deep Review, Warning, Error, Complete) are honest
  deferrals, not hidden gaps.
- The exact archived dispatch is relayed only for `AUTHORIZED_VALID`
  archives; untrusted archives show their verdict and never their bytes.
- The Supervisor decision receipt is shown only when its recorded canonical
  hash and candidate binding verify; v1.2 exposes no decision-listing
  command, so decisions appear per round (through the archive binding), not
  as a standalone timeline facet.
- The Task Detail drawer's Artifacts tab remains metadata/provenance-only
  and links into the P6 Artifact Center, where bounded previews and
  artifact-triggered feedback live. The browser is read-only for artifacts:
  no edit, rename, delete, or upload surface exists anywhere in P6.
- Human Control (P5) delegates exclusively to the v1.2 entry points; the
  Console has no model-config control (no formal v1.2 interface exists),
  so P7 records Supervisor model/effort/mode as an explicit setup draft
  and never claims an active turn was changed; "model change queued —
  applies next turn" needs a future Runtime-side interface. The Console
  has no STOP recovery/un-stop flow (recovery stays with the
  documented v1.2 STOP path and a human).
- The STOP challenge store is in-memory: a backend restart invalidates
  unconfirmed challenges (a fresh challenge must be requested); challenges
  are never persisted or authoritative.
- Prepared Human Decision receipts persist in the Console data directory
  until applied or cleaned by a human; the Console never transmits them to
  a Runtime until the user confirms apply.
- The smoke and HTTP suites exercise cooperative interruption against an
  authorized-but-unclaimed task (the v1.2 RETIRE path). The claimed/running
  disposition (`PAUSED_CURRENT_REVOKED`) is covered by the v1.2 control
  suite and the stub HTTP tests; building a genuine in-flight Executor
  claim in a fixture would require the full Supervisor candidate-origin
  machinery, which no P5 test fabricates.
- Registry serialization is in-process only (single console instance per
  installation, enforced by the START/STOP lifecycle).
- P6 Artifact Center bounds, all documented and enforced: the publication-
  root walk stops at 5000 files / depth 24 (a truncated walk is surfaced in
  the honesty block), hash re-verification is capped at 64 MiB (above it the
  hash is `unavailable` and previews are refused for receipt-bound files),
  text previews cap at 256 KiB / 5000 lines / 100k characters, tables at
  200 rows × 64 columns, images at 8 MiB, PDFs at 32 MiB.
- The per-artifact Final Verification relationship is not determined in the
  catalog (it would require one dispatch query per producing round); it is
  shown per round in the Task Detail drawer, and the provenance panel links
  there. Catalog provenance otherwise shows verified receipt facts, the
  `unbound` class, and republish linkage only.
- File-tree and image-gallery views, rich (HTML) Markdown rendering, the
  compact Recent Artifacts block in the Cockpit, and diff/version comparison
  (v1.3.1) are explicit deferrals; the P6 list view is deterministic and
  path-ordered.
- Artifact feedback binds one producing MESSAGE_ID per item (multi-artifact
  selection is allowed within one round); the Console refuses cross-round
  selections before anything is submitted.
- Health's control-plane signal is structural (script presence); the real
  availability probe is `/api/status`, `/api/runtimes/<id>/status`, or the
  cockpit route.
- P7 bounds, all documented and enforced: the Goal draft is capped at
  256 KiB UTF-8 / 100k characters / 5000 lines; setup request bodies at
  512 KiB; input registration at 8 selections per request, 512 walked
  entries and depth 8 per selection, 2000 inventory entries per draft
  (all metadata-only); the ZCode template at 256 KiB; the context pack at
  64 KiB (contract excerpt 48 KiB, inventory excerpt 200 entries). Setup
  drafts live only in the Console's `web_console_data/setup/` and are
  never authoritative; a corrupt draft is surfaced and treated as empty.
  The wizard does not implement P9 Runtime creation, notifications, or
  alert heuristics, and the ordinary external link to a Web AI is
  deliberately omitted: the Console never opens, embeds, or automates any
  external AI, and the frontend keeps its zero-external-URL invariant.
- The frontend is deliberately dependency-free; there is no build step, so
  browser support targets evergreen desktop browsers with `replaceChildren`
  and `fetch`.
- Auto-selected free ports have the usual TOCTOU window between the port
  probe and the bind; the backend fails loudly if the port is taken.
