from pathlib import Path
import json
import re
import sys

# G5A.5.1: preflight follows the SAME project-scope rules as the orchestrator.
#   control\ACTIVE_PROJECT.json missing -> legacy single-project mode
#   present + valid                     -> isolated project mode
#   present + invalid                   -> fail closed (never a silent legacy fallback)
# Read-only: this script never writes project state.

ROOT = Path(__file__).resolve().parents[1]  # G5A.5: runtime install root
CONTROL = ROOT / "control"
ACTIVE_PROJECT = CONTROL / "ACTIVE_PROJECT.json"
SUPPORTED_PROFILES = ("GENERAL", "ACADEMIC_RESEARCH", "SOFTWARE_ENGINEERING",
                      "BUSINESS_RESEARCH")
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
LIFECYCLE_STATUSES = {"SUPERVISOR_TURN", "WAITING_EXECUTOR", "COMPLETE", "BLOCKED",
                      "STOPPED", "HUMAN_REVIEW"}

required = [
    ROOT / "orchestrator.py",
    CONTROL / "project_state.json",
    CONTROL / "CODEX_SUPERVISOR_RUNTIME.md",
    ROOT / "RESEARCH_STATE.md",
    ROOT / "ZCODE_LAST_PROCESSED.txt",
]

errors = [f"missing: {p}" for p in required if not p.exists()]
state = {}
mode = "legacy"
pointer = None
state_path = CONTROL / "project_state.json"

# ---- active project pointer (same rules as the orchestrator) ----------------------
if ACTIVE_PROJECT.exists():
    pointer = None
    try:
        pointer = json.loads(ACTIVE_PROJECT.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"ACTIVE_PROJECT pointer is missing or invalid: {exc}")
    if isinstance(pointer, dict):
        unknown = sorted(set(pointer) - {"schema_version", "project_id", "project_root"})
        missing = sorted({"schema_version", "project_id", "project_root"} - set(pointer))
        if unknown or missing:
            errors.append(f"ACTIVE_PROJECT schema mismatch (missing={missing}, unknown={unknown})")
        elif pointer.get("schema_version") != 1:
            errors.append("ACTIVE_PROJECT schema_version must be 1")
        elif not isinstance(pointer.get("project_id"), str) or \
                not PROJECT_ID_PATTERN.fullmatch(pointer["project_id"]):
            errors.append(f"ACTIVE_PROJECT.project_id is not a safe project id: "
                          f"{pointer.get('project_id')!r}")
        else:
            pid = pointer["project_id"]
            proot_raw = pointer.get("project_root")
            if not isinstance(proot_raw, str) or \
                    Path(proot_raw).as_posix() != f"projects/{pid}":
                errors.append(f"ACTIVE_PROJECT.project_root must be exactly "
                              f"'projects/{pid}' (got {proot_raw!r})")
            else:
                project_root = (ROOT / proot_raw).resolve()
                try:
                    project_root.relative_to((ROOT / "projects").resolve())
                except Exception:
                    errors.append("ACTIVE_PROJECT.project_root escapes ROOT\\projects")
                else:
                    state_path = project_root / "project_state.json"
                    if not state_path.is_file():
                        errors.append(f"active project has no project_state.json: {project_root}")
                    else:
                        mode = "isolated"
    else:
        pointer = "INVALID"

# ---- legacy / isolated state load --------------------------------------------------
if pointer != "INVALID":
    try:
        state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        if not isinstance(state, dict):
            errors.append(f"project_state is missing or invalid: {state_path}")
            state = {}
    except Exception as exc:
        errors.append(f"project_state is missing or invalid: {state_path}: {exc}")
        state = {}

if state and not errors:
    try:
        infra = state.get("infrastructure_status")
        phase = state.get("phase")
        status = str(state.get("status") or "")
        profile = state.get("profile") or state.get("project_type")
        if infra not in {"TESTING", "READY", "BLOCKED"}:
            errors.append("unexpected infrastructure_status")

        if mode == "isolated":
            # isolated project mode: profile + lifecycle + FV binding checks
            if status not in LIFECYCLE_STATUSES:
                errors.append(f"unexpected project status: {status!r}")
            if profile not in SUPPORTED_PROFILES:
                errors.append(f"unsupported project profile: {profile!r}")
            else:
                profile_dir = ROOT / "profiles" / profile
                profile_doc = json.loads(
                    (profile_dir / "PROFILE.json").read_text(encoding="utf-8-sig"))
                expected_policy = profile_doc.get("final_verification_policy_id")
                policy_doc = json.loads(
                    (profile_dir / "FINAL_VERIFICATION_POLICY.json").read_text(encoding="utf-8-sig"))
                if policy_doc.get("policy_id") != expected_policy:
                    errors.append("profile/policy binding mismatch")
            fv = state.get("final_verification")
            if not isinstance(fv, dict) or fv.get("required") is not True:
                errors.append("isolated project requires final_verification.required=true")
            elif str(fv.get("status") or "").upper() not in \
                    {"NOT_STARTED", "PENDING", "REVERIFY", "PASS", "FAIL"}:
                errors.append(f"unexpected final_verification.status: {fv.get('status')!r}")
        else:
            # legacy single-project mode: preserve the historical V1.5 checks verbatim
            if phase == "CROSS_BORDER_ECOMMERCE_AGENT_V1":
                if infra != "READY":
                    errors.append("commercial phase requires infrastructure_status READY")
                if state.get("amazon_authorized") is not True:
                    errors.append("commercial phase requires amazon_authorized true")
                if not (CONTROL / "CROSS_BORDER_GOAL.md").exists():
                    errors.append("commercial phase missing control/CROSS_BORDER_GOAL.md")
                status_report = ROOT / "reports" / "INFRASTRUCTURE_STATUS.md"
                if not status_report.exists() or "INFRASTRUCTURE_STATUS: READY" not in \
                        status_report.read_text(encoding="utf-8-sig"):
                    errors.append("commercial phase requires verified READY infrastructure report")
            else:
                # During/after infrastructure testing, commercial authorization must remain off.
                if state.get("amazon_authorized") is not False:
                    errors.append("amazon_authorized must be false outside the commercial phase")
                if not (CONTROL / "INFRA_TEST_PLAN.md").exists():
                    errors.append("infrastructure phase missing control/INFRA_TEST_PLAN.md")

        # ZCODE_LAST_PROCESSED.txt: use the Runtime canonical parser
        # (scripts/executor_claim.py) — the same wire-format semantics as
        # resume_human_review.py — so no entry point keeps a divergent dialect.
        last = -1
        try:
            import executor_claim as _claim_helper
        except Exception:
            _claim_helper = None
        if _claim_helper is not None:
            try:
                last = _claim_helper.parse_last_processed_identity(
                    (ROOT / "ZCODE_LAST_PROCESSED.txt").read_text(
                        encoding="utf-8-sig", errors="replace"
                    )
                )["MESSAGE_ID"]
            except _claim_helper.LastProcessedFormatError as exc:
                errors.append(f"ZCODE_LAST_PROCESSED.txt is malformed: {exc}")
        else:
            # Canonical helper unavailable: keep the historical first-integer scan.
            _lp_text = (ROOT / "ZCODE_LAST_PROCESSED.txt").read_text(
                encoding="utf-8-sig", errors="replace"
            ).strip()
            _lp_match = re.search(r"-?\d+", _lp_text)
            last = int(_lp_match.group(0)) if _lp_match else -1
        inbox = ROOT / "TO_ZCODE.md"
        if state.get("status") == "WAITING_EXECUTOR" and not inbox.exists():
            # FIX-700102: a mechanically rejected dispatch candidate is quarantined out
            # of the inbox, leaving a recoverable middle state. It is identifiable
            # mechanically: the recorded authorization does not cover current_task, so
            # nothing claimable was lost and the Orchestrator startup repair path owns
            # recovery. An authorization that DOES cover current_task with a missing
            # inbox remains a real inbox-loss error.
            auth = None
            runtime_path = CONTROL / "orchestrator_runtime.json"
            if runtime_path.exists():
                try:
                    loaded_runtime = json.loads(runtime_path.read_text(encoding="utf-8-sig"))
                    if isinstance(loaded_runtime, dict):
                        auth = loaded_runtime.get("authorized_dispatch")
                except Exception:
                    auth = None
            current = state.get("current_task") or {}
            ident_keys = ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")

            def _ident(container, key):
                if isinstance(container, dict):
                    return container.get(key, container.get(key.lower()))
                return None

            covered = isinstance(auth, dict) and all(
                auth.get(key) == _ident(current, key) for key in ident_keys)
            if covered:
                errors.append("WAITING_EXECUTOR but TO_ZCODE.md is missing")
            else:
                print("NOTE: WAITING_EXECUTOR without TO_ZCODE.md and without a matching "
                      "authorization: recoverable dispatch-rejection middle state; the "
                      "Orchestrator startup repair path owns recovery.")
        if inbox.exists():
            text = inbox.read_text(encoding="utf-8-sig")
            m = re.search(r"MESSAGE_ID\s*[:：]\s*(\d+)", text)
            if m and "NO_ACTIVE_TASK" in text and int(m.group(1)) > last:
                errors.append("idle TO_ZCODE MESSAGE_ID is newer than ZCODE_LAST_PROCESSED "
                              "and could execute unexpectedly")

        # Pending and consumed Human Decision lifecycle records are authoritative in
        # every later status. Reuse the Runtime validator so a consumed receipt can
        # never silently return to pending or regain authorization semantics.
        if mode == "isolated" and (
                state.get("human_review_resume") is not None
                or state.get("human_decision_consumption_ledger") is not None):
            try:
                import resume_human_review as hr
                runtime_module = hr.load_runtime_module(ROOT)
                runtime_module.activate_project_scope()
                runtime_module.load_verified_human_decision_for_supervisor(
                    runtime_module.read_project_state())
            except Exception as exc:
                errors.append(f"invalid HUMAN_REVIEW lifecycle/receipt ledger: {exc}")
    except Exception as exc:
        errors.append(f"invalid project_state.json: {exc}")

if errors:
    print("PREFLIGHT: BLOCKED")
    print(f"Mode: {mode}")
    print(f"Runtime Root: {ROOT}")
    if mode == "isolated" and isinstance(pointer, dict):
        print(f"Project ID: {pointer['project_id']}")
    for item in errors:
        print("-", item)
    raise SystemExit(1)

print("PREFLIGHT: OK")
print(f"Mode: {mode}")
print(f"Runtime Root: {ROOT}")
print("Architecture: shared-file Scheduled Automation only")
if mode == "isolated" and isinstance(pointer, dict):
    print(f"Project ID: {pointer['project_id']}")
    print(f"Project type/profile: {state.get('profile')}")
print("Phase:", state.get("phase"))
print("Infrastructure status:", state.get("infrastructure_status"))
print("Project status:", state.get("status"))
if isinstance(state.get("final_verification"), dict):
    fv = state["final_verification"]
    print(f"Final verification: required={fv.get('required')} status={fv.get('status')} "
          f"policy={fv.get('policy_id')}")
