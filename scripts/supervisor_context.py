"""Decision context plus demand-driven deep references. No new authority or cache.

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


MEMORY_MARKER = "<!-- supervisor-context-v2: current-memory -->"
PROJECT_MEMORY_H1 = "# project memory"
ARCHIVAL_H2 = ("## archive", "## historical decisions", "## decision history")
HISTORICAL_MEMORY_PREFIX = "## historical memory"
SUPERSEDED_LABEL = "# Project Memory (superseded snapshot)"


def _memory_segments(text: str) -> list[dict]:
    """Ordered fence-aware segments at H1/H2 headings; \r\n kept for offsets.

    Line and byte accounting stay against the stored bytes so history pointers
    address the exact file the read helper slices.
    """
    groups, lines, fence = [], [], None
    for line_text in text.splitlines(keepends=True):
        stripped = line_text.lstrip()
        if stripped.startswith(("```", "~~~")):
            char = stripped[0]
            length = len(stripped) - len(stripped.lstrip(char))
            if fence is None:
                fence = (char, length)
            elif char == fence[0] and length >= fence[1]:
                fence = None
        if fence is None and lines and (line_text.startswith("## ")
                                        or line_text.startswith("# ")):
            groups.append(lines)
            lines = []
        lines.append(line_text)
    groups.append(lines)
    segments, line_no, byte_offset = [], 1, 0
    for group in groups:
        raw = "".join(group)
        heading = group[0].rstrip("\r\n") if group else ""
        segments.append({"heading": heading, "title": heading.strip().casefold(),
                         "raw": raw, "line": line_no, "utf8_offset": byte_offset,
                         "text": raw.replace("\r\n", "\n").replace("\r", "\n")})
        line_no += raw.count("\n")
        byte_offset += len(raw.encode("utf-8"))
    return segments


def _is_h2(segment) -> bool:
    return segment["heading"].startswith("## ")


def _is_h1(segment) -> bool:
    return segment["heading"].startswith("# ")


def _is_archival_h2(segment) -> bool:
    return _is_h2(segment) and segment["title"] in ARCHIVAL_H2


def _is_wrapper_h2(segment) -> bool:
    """H2 that names a history unit: the archival titles or a wrapper title."""
    return _is_h2(segment) and (segment["title"] in ARCHIVAL_H2
                                or segment["title"].startswith(HISTORICAL_MEMORY_PREFIX))


def _is_snapshot_h1(segment) -> bool:
    return _is_h1(segment) and segment["title"] == PROJECT_MEMORY_H1


def _is_unclassified_h1(segment) -> bool:
    return _is_h1(segment) and not _is_snapshot_h1(segment)


def _marker_indexes(segments: list[dict]) -> set[int]:
    return {i for i, s in enumerate(segments)
            if any(line.strip() == MEMORY_MARKER for line in s["raw"].splitlines())}


def _single_section_extent(segments: list[dict], start: int) -> int:
    """Pre-V2 H2-tier extent: through following H1 content up to the next H2."""
    j = start + 1
    while j < len(segments) and not _is_h2(segments[j]):
        j += 1
    return j


def _fused_extent(segments: list[dict], start: int) -> int:
    """History-unit extent: to the next wrapper, unclassified H1, or second
    nested snapshot (a wrapper unit absorbs the one snapshot it demotes;
    a bare superseded snapshot unit never absorbs a following one)."""
    absorbed = _is_wrapper_h2(segments[start])
    j = start + 1
    while j < len(segments):
        seg = segments[j]
        if _is_wrapper_h2(seg) or _is_unclassified_h1(seg):
            break
        if _is_snapshot_h1(seg):
            if not absorbed:  # bare chain: each snapshot is its own unit
                break
            absorbed = False  # wrapper unit: absorb exactly one nested snapshot
        j += 1
    return j


def route_memory(data: bytes) -> tuple[str, list[dict], dict]:
    """Memory Routing V2: snapshot-chain recognition.

    The first `# Project Memory` H1 block is the current snapshot and stays
    inline; every later one is superseded history. Wrapper-titled H2s and
    superseded snapshots become deferred history units delivered as pointers.
    `utf8_offset` is the unit's first byte offset in the stored file (a leading
    BOM is counted), so `supervisor_inspect --offset <utf8_offset>` seeks
    byte-exactly onto the unit. Unclassified material always stays inline
    (fail-visible); the marker gates deferral and must sit inside the current
    snapshot block. Routing is a pure function of the file bytes.
    """
    bom = 3 if data.startswith(b"\xef\xbb\xbf") else 0
    segments = _memory_segments(data[bom:].decode("utf-8"))
    markers = _marker_indexes(segments)
    first = next((i for i, s in enumerate(segments) if _is_snapshot_h1(s)), None)
    mode, reason, chain = "full_inline", None, False
    boundary = None  # first segment after the current snapshot that is not current content
    if markers:
        if first is None:
            mode = "h2_sections"
        else:
            chain = True
            boundary = first + 1
            while boundary < len(segments):
                seg = segments[boundary]
                if _is_archival_h2(seg):
                    boundary += 1  # archival H2s live inside the current block
                    continue
                if _is_wrapper_h2(seg) or _is_snapshot_h1(seg) or _is_unclassified_h1(seg):
                    break
                boundary += 1
            if not any(first <= i < boundary for i in markers):
                mode, reason, chain = "unresolved", "marker_not_in_current_snapshot", False
            else:
                mode = "snapshot_chain"

    inline: list[str] = []
    deferred: list[dict] = []

    def unit(start: int, stop: int, heading: str) -> None:
        deferred.append({"heading": heading, "line": segments[start]["line"],
                         "characters": sum(len(segments[k]["text"]) for k in range(start, stop)),
                         "utf8_offset": bom + segments[start]["utf8_offset"]})

    i = 0
    state = "flat" if not chain else "preamble"
    while i < len(segments):
        seg = segments[i]
        if state == "flat":
            if mode == "h2_sections" and _is_archival_h2(seg):
                stop = _single_section_extent(segments, i)  # pre-V2 opt-in tier
                unit(i, stop, seg["heading"])
                i = stop
            else:
                inline.append(seg["text"])
                i += 1
        elif state == "preamble":
            inline.append(seg["text"])  # everything before the current snapshot
            if i == first:
                state = "current"
            i += 1
        elif state == "current":
            if _is_archival_h2(seg):  # defer this section as before, then resume
                stop = _single_section_extent(segments, i)
                unit(i, stop, seg["heading"])
                i = stop
            elif _is_wrapper_h2(seg) or (_is_snapshot_h1(seg) and i != first):
                state = "history"  # the history boundary itself opens a unit
            elif _is_unclassified_h1(seg):
                state = "tail"
            else:
                inline.append(seg["text"])
                i += 1
        elif state == "history":
            stop = _fused_extent(segments, i)
            unit(i, stop, seg["heading"] if _is_wrapper_h2(seg) else SUPERSEDED_LABEL)
            i = stop
            if i < len(segments) and _is_unclassified_h1(segments[i]):
                state = "tail"
        else:  # tail: unclassified blocks stay visible; recognized openers resume
            if _is_wrapper_h2(seg) or (_is_snapshot_h1(seg) and i != first):
                state = "history"
            else:
                inline.append(seg["text"])
                i += 1

    current_snapshot = {"line": 0, "characters": 0, "marker": bool(markers)}
    if chain:
        current_snapshot = {"line": segments[first]["line"],
                            "characters": sum(len(segments[k]["text"])
                                              for k in range(first, boundary)),
                            "marker": any(first <= m < boundary for m in markers)}
    routing = {"mode": mode, "current_snapshot": current_snapshot,
               "deferred_units": len(deferred), "reason": reason}
    return "".join(inline), deferred, routing


def memory_view(text: str) -> tuple[str, list[dict]]:
    """Deliver inline memory plus deferred history pointers (Routing V2)."""
    inline, deferred, _ = route_memory(text.encode("utf-8"))
    return inline, deferred


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
    "PARKED_STAGE_RESUME": "The previous dispatch was parked by a user scheduling/quota pause; that is not a method failure, executor retry, or scientific failure. Continue the same logical stage with an unchanged outcome and acceptance standard; the Runtime allocates a fresh dispatch identity without consuming the retry budget.",
    "LEGACY_PARKED_STAGE_RESUME": "The previous dispatch was retired by a legacy safe pause before any Executor claim, so it never executed; that is an infrastructure/user-quota pause, not a method failure, executor retry, or scientific failure. Continue the same logical stage with an unchanged outcome and acceptance standard; the Runtime allocates a fresh dispatch identity without consuming the retry budget. Candidate evidence named in the event may be read-only verified and reused only after checking its hashes and outputs; publication and completion must use the fresh dispatch identity.",
    "EXECUTOR_TIMEOUT_DURING_PAUSE": "The claimed stage expired while the Runtime was paused for scheduling/quota reasons; the expired identity is fenced and cannot resume. Re-plan the same pending work; the Runtime does not charge this pause-caused expiry to the logical retry budget.",
    "PICKUP_TIMEOUT_STAGE_RESUME": "The bounded pickup authorization expired before any Executor claimed the dispatch, so it never executed; that is scheduling/worker-availability recovery, not a method failure, executor retry, or scientific failure. Continue the same logical stage with an unchanged outcome and acceptance standard; the Runtime allocates a fresh dispatch identity without consuming the retry budget.",
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
        # A reference is a pointer, not a reason to open/hash its contents.
        # Provenance of actually delivered content is retained in prompt_sha256;
        # Goal/receipt authority is still established by the existing Runtime gates.
        sources[key] = {"path": rel, "delivery": delivery}

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

    memory_data, _ = load(memory_path)
    if memory_data is None:
        memory, deferred, routing = full_text(memory_path), [], {
            "mode": "full_inline",
            "current_snapshot": {"line": 0, "characters": 0, "marker": False},
            "deferred_units": 0, "reason": "unreadable_memory_file"}
    else:
        try:
            memory, deferred, routing = route_memory(memory_data)
        except UnicodeError:
            memory, deferred = full_text(memory_path), []
            routing = {"mode": "full_inline",
                       "current_snapshot": {"line": 0, "characters": 0, "marker": False},
                       "deferred_units": 0, "reason": "not_utf8"}
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
    reference("research_state", memory_path, "current snapshot; superseded history by reference")
    reference("executor_brief", receipt_path if receipt is not None else fallback_brief,
              "committed receipt semantic fields" if receipt is not None else "unverified compatibility text")
    reference("fv_protocol", root / "control/SUPERVISOR_PROTOCOL_REFERENCE.md", "FV/Human Decision only")
    reference("task_template", root / "control/EXECUTOR_TASK_TEMPLATE.md", "FV/Human Decision only")
    add("REFERENCED DEEP CONTEXT", "Decision context is above; inspect these pointers only when needed.\n"
        + "\n".join(f"{key}: {value['path']} ({value['delivery']})" for key, value in sources.items())
        + "\nImplementation/evidence paths in the Goal, state and receipts are also deep references.\n"
        + "Targeted text read: python scripts/supervisor_inspect.py --path <runtime-relative-file> --reason <ambiguity|conflict|regression|scope_drift|insufficient_evidence|fv_disagreement|interface_constraint|history|audit|contract> --offset 0 --limit 8192\n"
        + "Returns a UTF-8 byte slice. Read further only as needed; use native inspection for unsupported evidence.")
    add("TURN OUTPUT", f"Turn time: {now}\nRuntime Root: {root}.\n"
        "You are the Supervisor, not the Executor. Do not use browser/GUI/Computer Use "
        "or polling to route work; delegate implementation.\n"
        "Read the current project_state.json when applying your decision; preserve every unshown field and all history. "
        "The view is not a replacement state document; absence here does not remove a requirement.\n"
        "For FINAL_VERIFICATION or a Human Decision task, read control/SUPERVISOR_PROTOCOL_REFERENCE.md and "
        "control/EXECUTOR_TASK_TEMPLATE.md before authoring the retained output contract. "
        f"Compatibility-only nonce seed: {nonce}")
    prompt = "\n\n".join(f"=== {label} ===\n{body}\n=== END {label} ==="
                           for label, body in sections) + "\n"
    # Bounded observability contains only facts, never prompt/evidence contents.
    manifest = {"context_version": 4,
                "prompt_characters": len(prompt),
                "prompt_utf8_bytes": len(prompt.encode("utf-8")),
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "section_characters": {k: len(v) for k, v in sections},
                "supervisor_rules": {"delivery": "full", "truncated": False},
                "project_state": {"delivery": "projection", "truncated": False},
                "research_state": {"delivery": "current snapshot; units by reference",
                                   "truncated": False},
                "project_goal": {"delivery": "full", "truncated": False},
                "executor_brief": {"source": "committed" if receipt is not None else
                                   "unverified_compatibility" if fallback_brief else "none"},
                "deferred_memory_sections": len(deferred),
                "memory_routing": routing,
                "builder_reads": {"files": len(loaded),
                                  "bytes": sum(len(data) for data, _ in loaded.values() if data is not None)},
                "deep_reference_count": len(sources)}
    return SupervisorPrompt(prompt, manifest)
