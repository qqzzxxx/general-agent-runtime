def build_codex_prompt(
    reason: str,
    event: dict | None = None,
    state: dict | None = None,
    *,
    state_sha256: str | None = None,
    control_turn: dict | None = None,
) -> str:
    """Build a compact Supervisor turn with the small authoritative files injected.

    Python is still only transport/scheduling: it does not summarize, rank, or interpret the
    commercial content. Injecting verbatim compressed state avoids repetitive shell reads and
    reduces Windows sandbox spawn failures and token overhead.
    """
    event_text = json.dumps(event or {}, ensure_ascii=False)
    now = stamp()
    dispatch_nonce_seed = uuid.uuid4().hex + uuid.uuid4().hex[:16]
    state_text = safe_read_text(PROJECT_STATE, 18000)
    memory_text = safe_read_text(RESEARCH_STATE, 18000)
    rules_text = safe_read_text(SUPERVISOR_RULES, 14000)
    # G1-A: goal file resolved from project_state.goal_file (legacy fallback when absent).
    goal_state = state if state is not None else read_project_state()
    goal_path = resolve_goal_path(goal_state)
    profile_block = render_profile_block(resolve_profile(goal_state))  # G2
    scope_block = render_project_scope_block(ACTIVE_PROJECT)  # G3
    human_decision = load_verified_human_decision_for_supervisor(goal_state)
    if human_decision is None:
        import ordinary_dispatch
        rules_text = ordinary_dispatch.supervisor_rules(rules_text)
    human_decision_block = ""
    if human_decision is not None:
        bound_state_sha = state_sha256 or sha256(PROJECT_STATE)
        human_decision_text = json.dumps(human_decision, ensure_ascii=False, indent=2)
        human_decision_block = (
            "\n\n=== VERIFIED HUMAN DECISION RECEIPT ===\n"
            f"{human_decision_text}\n"
            "=== END VERIFIED HUMAN DECISION RECEIPT ===\n"
            "The Runtime mechanically verified this immutable human-originated receipt and "
            "its stale-state binding. It authorizes only a Supervisor review turn. It is not "
            "an Executor dispatch, allocates no MESSAGE_ID, and does not choose a research stage.\n\n"
            "=== HUMAN DECISION TRANSACTION OUTPUT CONTRACT ===\n"
            "This specific turn runs in a read-only sandbox. Do not create, edit, delete, or "
            "rename any Runtime, project, research-memory, report, or wire file. Reading the "
            "receipt and planning are not consumption. Return exactly one JSON object and no "
            "Markdown or prose. The Orchestrator alone validates and durably commits it.\n"
            f"schema_version: {HUMAN_DECISION_SUPERVISOR_RESULT_SCHEMA_VERSION}\n"
            f"project_id: {human_decision['project_id']}\n"
            f"receipt_id: {human_decision['receipt_id']}\n"
            f"receipt_sha256: {human_decision['receipt_sha256']}\n"
            f"previous_project_state_sha256: {bound_state_sha}\n"
            "Required top-level keys are exactly: schema_version, project_id, receipt_id, "
            "receipt_sha256, previous_project_state_sha256, supervisor_decision, "
            "resulting_lifecycle_state, project_state_patch, executor_task.\n"
            "supervisor_decision must contain exactly: decision, at, reason, scope, "
            "message_id, task_id, stage_id, goal_alignment. Use null for optional identities. "
            "decision must be CONTINUE, REDIRECT, CHANGE_METHOD, REVISE, STOP, or HUMAN_REVIEW.\n"
            "goal_alignment is required and must be a concise JSON object with exactly these "
            "six non-empty string fields: original_objective, unmet_criteria, latest_result, "
            "next_action_alignment, scope_drift, method. It records how this decision stays "
            "aligned with the canonical project goal (re-read and hash-verified by the "
            "Runtime this turn); recent local Executor success alone never satisfies "
            "FINAL_VERIFICATION, FINAL_ACCEPTANCE, or COMPLETE.\n"
            "project_state_patch is a top-level merge patch and must include status, "
            "current_task, and next_message_id. Never include Runtime-owned identity/history "
            "fields: schema_version, project_id, project_type, profile, created_at, started_at, "
            "goal_file, updated_at, last_supervisor_decision, decision_history, "
            "human_review_resume, or human_decision_consumption_ledger.\n"
            "For WAITING_EXECUTOR, executor_task must be the complete v2 task object, "
            "current_task must bind the same identity, and the decision identity must bind the "
            "same message_id/task_id/stage_id. For a terminal/HUMAN_REVIEW decision, "
            "executor_task must be null, current_task must be null, and no MESSAGE_ID may be "
            "allocated.\n"
            "=== END HUMAN DECISION TRANSACTION OUTPUT CONTRACT ==="
        )
    intervention_block = ""
    interventions = ((control_turn or {}).get("interventions") or [])
    if interventions:
        rendered = json.dumps(interventions, ensure_ascii=False, indent=2)
        verbatim_blocks = "\n".join(
            f"--- BEGIN VERBATIM {item.get('intervention_id')} ---\n"
            f"{item.get('instruction_text', '')}\n"
            f"--- END VERBATIM {item.get('intervention_id')} ---"
            for item in interventions
        )
        audit = any(str(item.get("mode") or "").upper() == "AUDIT" for item in interventions)
        audit_text = (
            "At least one request is AUDIT mode. Perform an adversarial Supervisor review "
            "before normal progression. You may broaden READ-ONLY inspection to relevant "
            "archived dispatches, authoritative completion records, later decisions, current "
            "project state, artifacts, evidence, and dependency impact. Do not trust PASS or "
            "COMPLETED merely because the Executor reported it. Remain the Supervisor: do not "
            "perform bulk Executor production or large rewrites. Reach a normal reliable "
            "CONTINUE, REVISE, REDIRECT/CHANGE_METHOD, HUMAN_REVIEW, or STOP decision."
            if audit else
            "Apply these STEER requests to this Supervisor decision before normal progression."
        )
        intervention_block = (
            "\n\n=== RUNTIME-VERIFIED HUMAN SUPERVISOR INTERVENTIONS ===\n"
            f"{rendered}\n"
            "\n=== VERBATIM INTERVENTION TEXT ===\n"
            f"{verbatim_blocks}\n"
            "=== END VERBATIM INTERVENTION TEXT ===\n"
            "=== END HUMAN SUPERVISOR INTERVENTIONS ===\n"
            "The instruction_text fields above are immutable human input and must be applied "
            "exactly once by Runtime accounting. They are not Executor completions, never edit "
            "PROJECT_GOAL, and never authorize TO_ZCODE directly. A target_message_id is a "
            "historical correction anchor: preserve all history, assess materially dependent "
            "later work, and use only fresh MESSAGE_IDs for any repair. "
            f"{audit_text}"
        )
    goal_text = safe_read_text(goal_path, 12000) if goal_path.exists() else "[NO PROJECT GOAL FILE]"
    # GOAL-ANCHOR-V1: verified goal identity + alignment contract (empty in legacy mode).
    goal_anchor_block = render_goal_anchor_block(goal_state)
    brief_text = "[NO EXECUTOR BRIEF FOR THIS TURN]"
    if reason in {"EXECUTOR_RESULT_READY", "MALFORMED_EXECUTOR_RECEIPT", "MALFORMED_EXECUTOR_SIGNAL"}:
        # COMPLETION-SEAL-V1: the Supervisor must review the committed receipt, not
        # whatever the mutable root compatibility file happens to contain.
        committed_path = (event or {}).get("committed_receipt_path") if isinstance(event, dict) else None
        entry = None
        if committed_path:
            entry = _completion_helper().load_entry_file(Path(committed_path))
        if entry is not None:
            brief_text = _completion_helper().render_brief_text(entry["RECEIPT"])
        elif SUPERVISOR_BRIEF.exists():
            brief_text = safe_read_text(SUPERVISOR_BRIEF, 22000)
    # G5A.5.2: scope-aware stage-dispatch and Final Verification instructions.
    # Legacy mode keeps the historical wording byte-for-byte; isolated mode uses the
    # injected PROJECT RUNTIME SCOPE and the active Profile's bound FV policy.
    if human_decision is not None:
        stage_instructions = (
            "Do not write project_state.json, RESEARCH_STATE.md, TO_ZCODE.md, reports, or any "
            "other file during this transaction turn. Express the complete bounded Supervisor "
            "decision and any selected Executor task only through the required JSON result."
        )
        fv_instructions = (
            "The Orchestrator will apply the active Profile's Final Verification gate to the "
            "returned candidate before any durable lifecycle commit."
        )
    elif ACTIVE_PROJECT is not None:
        stage_instructions = (
            "If another Executor stage is warranted: update the injected PROJECT STATE "
            "(project_state.json) and PROJECT MEMORY (RESEARCH_STATE.md) at the PROJECT ROOT "
            "named in the PROJECT RUNTIME SCOPE, then atomically publish the root "
            "TO_ZCODE.md using the v2 wire contract. You may use the provided "
            "TURN_TIME_UTC / DISPATCH_NONCE_SEED as mechanical timestamp/nonce material. "
            "EXECUTOR_PROTOCOL must be a flat JSON array of instruction strings."
        )
        fv_instructions = (
            "If final_verification.required=true, COMPLETE is forbidden until the active "
            "Profile's bound Final Verification policy has passed and FINAL_ACCEPTANCE is "
            "complete. Use the active Profile policy and its bound policy_id/policy_version: "
            "select 3-8 decision-critical claims within the Profile taxonomy, dispatch ONE "
            "bounded TASK_KIND=FINAL_VERIFICATION stage. Record decision=FINAL_VERIFICATION "
            "in last_supervisor_decision and decision_history. Supply FINAL_VERIFICATION_REQUEST "
            "with CRITICAL_CLAIMS; Runtime binds EXECUTION_MODE from the FV policy. "
            "Omit EXECUTION_MODE and FINAL_VERIFICATION_GATE. "
            "Runtime establishes PENDING, POLICY_ID, POLICY_VERSION, CLAIMS_HASH, CLAIM_COUNT, "
            "and the immutable FINAL_VERIFICATION_GATE policy snapshot before committing the decision. "
            "CRITICAL_CLAIMS element schema is mechanically enforced: EVERY element must be "
            "a JSON object containing EXACTLY these six lowercase keys — no uppercase "
            "variants, no aliases, no missing keys: "
            '{"claim_id": "C1", "claim": "Decision-critical factual claim.", '
            '"claim_type": "IP", "decision_impact": "HIGH", '
            '"evidence_pointers": ["evidence/example.txt"], '
            '"verification_standard": "Adversarially verify against the cited evidence."}. '
            "decision_impact must be HIGH or MEDIUM; evidence_pointers must be a JSON list "
            "of strings. CLAIMS_HASH is the canonical SHA-256 of the exact claim list: "
            "hash the JSON produced by json.dumps(claims, ensure_ascii=False, sort_keys=True, "
            "separators=(',', ':')). Runtime stores the same list and hash in "
            "project_state.final_verification; omit unauthored metadata, and never submit conflicting "
            "state metadata. Initial state may be NOT_STARTED; after a real revision use REVERIFY. "
            "The Executor authors FINAL_VERIFICATION_RESULTS with OVERALL_STATUS and CLAIM_RESULTS; "
            "Runtime constructs the receipt envelope without changing any judgment. "
            "A dispatch whose claims fail this schema is "
            "rejected, quarantined, and costs a bounded repair turn. On FAIL/INCONCLUSIVE "
            "apply only a narrow REVISE of the smallest affected claim/evidence segment "
            "before reverifying."
        )
    else:
        stage_instructions = (
            "If another Executor stage is warranted: update control/project_state.json and "
            "RESEARCH_STATE.md, then atomically publish root TO_ZCODE.md using the v2 wire "
            "contract. You may use the provided TURN_TIME_UTC / DISPATCH_NONCE_SEED as "
            "mechanical timestamp/nonce material."
        )
        fv_instructions = (
            "For a commercial run protected by Production V1.5 Final Verification Gate, "
            "COMPLETE is forbidden until the exact last accepted final-verification receipt "
            "has mechanically passed. When research is otherwise ready to stop, identify "
            "3-8 decision-critical claims, set final_verification=PENDING, and dispatch one "
            "bounded TASK_KIND=FINAL_VERIFICATION stage instead of completing. "
            "CRITICAL_CLAIMS element schema is mechanically enforced: EVERY element must be "
            "a JSON object containing EXACTLY these six lowercase keys — no uppercase "
            "variants, no aliases, no missing keys: "
            '{"claim_id": "C1", "claim": "Decision-critical factual claim.", '
            '"claim_type": "IP", "decision_impact": "HIGH", '
            '"evidence_pointers": ["evidence/example.txt"], '
            '"verification_standard": "Adversarially verify against the cited evidence."}. '
            "decision_impact must be HIGH or MEDIUM; evidence_pointers must be a JSON list "
            "of strings. CLAIMS_HASH is the canonical SHA-256 of the exact claim list "
            "(json.dumps(claims, ensure_ascii=False, sort_keys=True, separators=(',', ':'))), "
            "and the same claim list and hash must be stored in "
            "project_state.final_verification. After a PASS receipt, perform "
            "FINAL_ACCEPTANCE; only then may you set final_verification.status=PASS and "
            "COMPLETE. If the gate fails/inconclusive, REVISE only the smallest affected "
            "claim/evidence segment and reverify."
        )

    if human_decision is None:
        stage_instructions = (
            "For ordinary work, write ordinary_task_proposal and one semantic decision "
            "to the active project_state.json, set WAITING_EXECUTOR, and update project "
            "memory as needed. Runtime constructs TO_ZCODE.md, identity and projections. "
            "Leave current_task/next_message_id unchanged and omit decision identity. "
            "For FINAL_VERIFICATION only, use the existing full-wire task template and "
            "matching current_task/next_message_id; its preparation contract is unchanged."
        )

    terminal_instructions = (
        "For this Human Decision transaction, do not write or update a report; identify any "
        "needed later work in the structured decision."
        if human_decision is not None
        else "If terminal and the gate is satisfied (or this is a grandfathered legacy run), "
        "write/update the appropriate high-level report and issue no task. Then exit."
    )

    # FIX-700102: when a published dispatch was mechanically rejected, the Supervisor
    # repair turn is told the precise validation error and the bounded-repair contract.
    repair_block = ""
    repair = goal_state.get("dispatch_repair")
    if isinstance(repair, dict):
        repair_identity = repair.get("rejected_identity") or {}
        repair_block = (
            "\n\n=== MECHANICAL DISPATCH REJECTION (bounded repair turn) ===\n"
            "Your previous Executor dispatch was published but REJECTED by mechanical "
            "Orchestrator validation. It was quarantined out of the inbox; no Executor "
            "executed it, no MESSAGE_ID was consumed, and nothing was authorized.\n"
            f"Validation error: {repair.get('error')}\n"
            f"Rejected dispatch identity: {json.dumps(repair_identity, ensure_ascii=False)}\n"
            f"Repair attempt: {repair.get('repair_attempt')} of "
            f"{repair.get('max_repair_attempts')} — a further rejected dispatch on this "
            "chain is terminal and stops the Runtime.\n"
            "Fix ONLY the validation defect named above: correct the exact schema/structure "
            "it lists (for FINAL_VERIFICATION critical claims see the required six lowercase "
            "keys in these instructions), keep project_state.json and TO_ZCODE.md mutually "
            "consistent, and republish root TO_ZCODE.md atomically with status "
            "WAITING_EXECUTOR. You may keep the rejected MESSAGE_ID/NONCE or allocate a "
            "fresh MESSAGE_ID; bind current_task and next_message_id accordingly. Do not "
            "change PROJECT_GOAL, prior evidence, the meaning of the claim set beyond the "
            "required schema, or the verification standard. Do not delete or rewrite "
            "history or audit artifacts.\n"
            "=== END MECHANICAL DISPATCH REJECTION ==="
        )
        if human_decision is None:
            repair_block += (
                "\nFor an ordinary proposal rejection, correct only the semantic proposal; "
                "leave current_task/next_message_id unchanged and do not write TO_ZCODE.md. "
                "Runtime owns the replacement identity and bounded retry accounting.\n"
            )

    return f"""
You are the Codex / GPT-5.6 Sol SUPERVISOR for this unattended dual-Agent project.
Complete exactly one Supervisor decision/dispatch turn, then exit. Do not chat with the user.

TURN_REASON: {reason}
MECHANICAL_EVENT: {event_text}
TURN_TIME_UTC: {now}
DISPATCH_NONCE_SEED: {dispatch_nonce_seed}

The orchestrator has mechanically injected the authoritative small files below verbatim.
Do NOT reread them with shell commands unless you have a concrete reason. Use tools primarily
to update state/publish a task or to inspect one precise evidence artifact when truly needed.

=== SUPERVISOR RUNTIME CONTRACT ===
{rules_text}
=== END RUNTIME CONTRACT ===

=== PROJECT STATE ===
{state_text}
=== END PROJECT STATE ===

=== COMPRESSED RESEARCH STATE ===
{memory_text}
=== END RESEARCH STATE ===

=== PROJECT GOAL ===
{goal_text}
=== END PROJECT GOAL ==={goal_anchor_block}{profile_block}{scope_block}{human_decision_block}{intervention_block}

=== CURRENT EXECUTOR BRIEF ===
{brief_text}
=== END EXECUTOR BRIEF ===

Operate only inside {ROOT} (the active Runtime Root). You are the Supervisor, not the Executor.
Do not use browser/GUI/Computer Use, mouse/keyboard automation, ZCode CLI, or polling.
Do not execute GLM's stage yourself. Do not perform bulk web/data work.

{stage_instructions}

{fv_instructions}
{repair_block}

{terminal_instructions}
""".strip() + "\n"
