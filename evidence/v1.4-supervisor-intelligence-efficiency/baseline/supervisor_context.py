"""Decision-relevant Supervisor presentation. No authority, summarizer or cache.

Select complete records/sections, never character prefixes. Unknown semantic data
stays visible. Historical records stay in their original files by reference.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


class SupervisorPrompt(str):
    """Keep measurements attached to the exact prompt passed to the provider."""

    def __new__(cls, text, manifest):
        obj = super().__new__(cls, text)
        obj.context_manifest = manifest
        return obj


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def memory_view(text: str) -> tuple[str, list[dict]]:
    """Only explicitly archived/history H2 sections are reference-only.

    Never infer that an unrecognized section is obsolete. In particular human
    feedback, constraints, failed methods and open questions remain complete.
    """
    if "<!-- supervisor-context-v2: current-memory -->" not in text.splitlines():
        return text, []  # legacy prose is not a safely classifiable archive
    parts, lines, fence = [], [], None
    for line_text in text.splitlines(keepends=True):
        stripped = line_text.lstrip()
        if stripped.startswith(("```", "~~~")):
            char = stripped[0]
            length = len(stripped) - len(stripped.lstrip(char))
            if fence is None:
                fence = (char, length)
            elif char == fence[0] and length >= fence[1]:
                fence = None
        if fence is None and line_text.startswith("## ") and lines:
            parts.append("".join(lines))
            lines = []
        lines.append(line_text)
    parts.append("".join(lines))
    inline, deferred = [], []
    line = 1
    for part in parts:
        title = part.splitlines()[0] if part.splitlines() else ""
        if title.strip().casefold() in {
                "## archive", "## historical decisions", "## decision history"}:
            deferred.append({"heading": title, "line": line,
                             "characters": len(part)})
        else:
            inline.append(part)
        line += part.count("\n")
    return "".join(inline), deferred


TASK_TRANSPORT = {"PROTOCOL_VERSION", "CLAIM_PROTOCOL_VERSION", "NONCE",
                  "ISSUED_AT", "EXECUTOR_MODEL_FAMILY", "SUPERVISOR_REVIEW_EFFORT",
                  "EXECUTOR_PROTOCOL", "SCHEDULER_GRACE_SECONDS"}


def state_view(state: dict) -> dict:
    result = copy.deepcopy(state)
    history = result.pop("decision_history", [])
    latest = result.pop("last_supervisor_decision", None)
    # The history tail must survive even for old states without a latest mirror.
    previous = history[-1] if isinstance(history, list) and history else latest
    result.pop("human_decision_consumption_ledger", None)
    result.pop("goal_anchor", None)  # supplied once by the verified anchor block
    result.pop("goal_file", None)  # supplied once by the source reference
    result.pop("dispatch_repair", None)  # supplied once in the decision request
    task = result.get("current_task")
    if isinstance(task, dict):
        result["current_task"] = {k: v for k, v in task.items() if k not in TASK_TRANSPORT}
    view = {"state": result,
            "historical_decision_count": len(history) if isinstance(history, list) else None}
    if previous is not None:
        view["previous_decision"] = previous
    if latest is not None and latest != previous:
        view["conflicting_latest_decision_mirror"] = latest
    return view


def receipt_view(receipt: dict) -> dict:
    """Drop known transport metadata and exact FV envelope duplicates only.

    All judgments, evidence, limitations, unknown fields and any disagreement
    survive. A committed receipt is evidence of a submission, not of its quality.
    """
    result = copy.deepcopy(receipt)
    for key in TASK_TRANSPORT | {"MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT",
                                  "PROJECT_ID", "STARTED_AT", "FINISHED_AT"}:
        result.pop(key, None)
    results = result.get("FINAL_VERIFICATION_RESULTS")
    envelope = result.get("FINAL_VERIFICATION")
    if isinstance(results, dict) and isinstance(envelope, dict):
        unique = {k: v for k, v in envelope.items()
                  if k not in results or results[k] != v}
        # Keep nonduplicate metadata: do not guess whether an unfamiliar field matters.
        if unique:
            result["FINAL_VERIFICATION"] = unique
        else:
            result.pop("FINAL_VERIFICATION")
    return result


DECISION_REQUESTS = {
    "EXECUTOR_RESULT_READY": "Assess the submitted result against the user's outcome; choose the most valuable remaining work or acceptance path.",
    "EXECUTOR_TIMEOUT": "Assess the unfinished outcome and failure evidence; choose retry, a different method, or human review within existing budgets.",
    "HUMAN_DECISION_RESUME": "Apply the verified human decision through the read-only transaction contract below.",
    "MALFORMED_EXECUTOR_RECEIPT": "Assess the receipt defect and unresolved outcome; evidence is untrusted and does not authorize completion.",
    "MALFORMED_EXECUTOR_SIGNAL": "Assess the signal defect; a compatibility hint cannot establish completion.",
}


def build(*, root: Path, reason: str, event: dict | None, state: dict,
          state_path: Path, memory_path: Path, goal_path: Path, rules_path: Path,
          profile: dict | None, goal_anchor: str, scope: str, human_block: str,
          interventions: list, receipt: dict | None, receipt_path: Path | None,
          fallback_brief: Path | None, now: str, nonce: str) -> SupervisorPrompt:
    import ordinary_dispatch

    sections = []
    sources = {}
    loaded = {}

    def load(path):
        if path not in loaded:
            try:
                loaded[path] = (path.read_bytes(), None)
            except OSError as exc:
                loaded[path] = (None, str(exc))
        return loaded[path]

    def full_text(path):
        data, error = load(path)
        if data is None:
            return f"[UNAVAILABLE: {path.name}: {error}]"
        try:
            return data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeError:
            return f"[UNAVAILABLE: {path.name}: not UTF-8]"

    def add(label, body):
        if body:
            sections.append((label, str(body)))

    def reference(key, path, delivery):
        if path is None:
            return
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            rel = str(path)
        data, _ = load(path)
        if data is not None:
            facts = {"path": rel, "present": True, "bytes": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()}
        else:
            facts = {"path": rel, "present": False}
        sources[key] = {**facts, "delivery": delivery, "truncated": False}

    rules = full_text(rules_path)
    add("SUPERVISOR RUNTIME CONTRACT", rules)
    if not human_block:
        add("ORDINARY OUTCOME DELEGATION", ordinary_dispatch.SUPERVISOR_CONTRACT)
    add("PROJECT GOAL", full_text(goal_path))
    add("GOAL INTEGRITY", goal_anchor)
    if scope:
        add("PROJECT RUNTIME SCOPE", scope)

    changes = copy.deepcopy(event or {})
    changes.pop("committed_receipt_path", None)  # reference table owns this path
    # Human receipt content has exactly one home, in the verified block.
    if human_block:
        changes = {k: v for k, v in changes.items()
                   if k not in {"receipt", "human_decision"}}
    add("WHAT CHANGED", json_text({"reason": reason, "event": changes,
                                  "human_interventions": interventions}))
    current = state_view(state)
    previous = current.get("previous_decision")
    alignment = previous.get("goal_alignment") if isinstance(previous, dict) else None
    unresolved = "No prior assessment supplied. Assess remaining criteria from the goal, current memory and new evidence."
    if isinstance(alignment, dict) and alignment.get("unmet_criteria"):
        unresolved = "Previous assessment (reassess against new evidence):\n" + str(alignment["unmet_criteria"])
        alignment["unmet_criteria"] = {"see": "UNRESOLVED OR WEAK"}
    add("WHAT IS TRUE NOW", json_text(current))
    add("UNRESOLVED OR WEAK", unresolved)

    memory, deferred = memory_view(full_text(memory_path))
    add("CURRENT PROJECT MEMORY", memory)
    if deferred:
        add("HISTORY AVAILABLE ON DEMAND", json_text(deferred))
    if profile:
        add("PROJECT PROFILE", f"Profile: {profile['profile_id']} (v{profile['profile_version']})\n"
            + profile["supervisor_guidance"].strip()
            + "\nDomain guidance applies to the actual task. A defect-fix method is not a constraint on a product redesign."
            + "\nExecutor guidance is available by reference when domain-specific delegation needs it.")
        profile_dir = root / "profiles" / profile["profile_id"]
        reference("executor_guidance", profile_dir / "EXECUTOR_GUIDANCE.md", "reference")
        reference("verification_policy", profile_dir / "FINAL_VERIFICATION_POLICY.json", "reference")
    if receipt is not None:
        add("LATEST EXECUTOR EVIDENCE (UNTRUSTED)", json_text(receipt_view(receipt)))
    elif fallback_brief:
        add("UNVERIFIED COMPATIBILITY BRIEF (UNTRUSTED)", full_text(fallback_brief))
    add("HUMAN DECISION", human_block)

    request = DECISION_REQUESTS.get(reason,
        "Choose the work with greatest value toward the user's outcome, using current evidence and unresolved criteria.")
    if interventions:
        request += " Apply the human interventions before normal progression; Runtime accounts for exactly-once consumption."
        if any(str(item.get("mode", "")).upper() == "AUDIT" for item in interventions):
            request += " AUDIT requires adversarial read-only inspection of relevant history, evidence and downstream impact before deciding."
    repair = state.get("dispatch_repair")
    if isinstance(repair, dict):
        request += ("\nMECHANICAL DISPATCH REJECTION: " + json_text(repair)
                    + f"\nRepair attempt: {repair.get('repair_attempt')} of {repair.get('max_repair_attempts')}."
                    + " Correct only the rejected schema, preserving the outcome and verification standard."
                    + " For ordinary work repair the proposal; Runtime constructs the replacement identity."
                    + " For FV use the referenced full-wire contract and its six lowercase keys for critical claims.")
    add("DECISION NEEDED NOW", request)

    reference("supervisor_rules", rules_path, "full")
    reference("project_goal", goal_path, "full")
    reference("project_state", state_path, "current records; history by reference")
    reference("research_state", memory_path, "current sections; explicit history by reference")
    reference("executor_brief", receipt_path if receipt is not None else fallback_brief,
              "committed receipt semantic fields" if receipt is not None else "unverified compatibility text")
    add("FULL RECORD REFERENCES", json_text(sources))
    add("TURN OUTPUT", f"Turn time: {now}\nOperate only inside {root}. "
        "You are the Supervisor, not the Executor. Do not use browser/GUI/Computer Use "
        "or polling to route work; do not perform the Executor's bulk work.\n"
        "Read the current project_state.json when applying your decision; preserve every unshown field and all history. "
        "The view is not a replacement state document. Full records above remain available for targeted inspection. "
        "Consult deferred history for an unresolved dependency, retry/method question, contradiction or AUDIT. "
        "Do not treat absence from this view as absence of a requirement.\n"
        "For FINAL_VERIFICATION or a Human Decision task, read control/SUPERVISOR_PROTOCOL_REFERENCE.md and "
        "control/EXECUTOR_TASK_TEMPLATE.md for the retained output contract before authoring it. "
        "Ordinary work never authors wire protocol. "
        f"Compatibility-only nonce seed: {nonce}\n"
        "Exit after one decision; never wait for the Executor.")
    prompt = "\n\n".join(f"=== {label} ===\n{body}\n=== END {label} ==="
                           for label, body in sections) + "\n"
    # Bounded observability contains only facts, never prompt/evidence contents.
    manifest = {"context_version": 2,
                "prompt_characters": len(prompt),
                "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "section_characters": {k: len(v) for k, v in sections},
                "supervisor_rules": {"delivery": "full", "truncated": False},
                "project_state": {"delivery": "projection", "truncated": False},
                "research_state": {"delivery": "complete sections", "truncated": False},
                "project_goal": {"delivery": "full", "truncated": False},
                "executor_brief": {"source": "committed" if receipt is not None else
                                   "unverified_compatibility" if fallback_brief else "none"},
                "deferred_memory_sections": len(deferred)}
    return SupervisorPrompt(prompt, manifest)
