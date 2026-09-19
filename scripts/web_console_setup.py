"""P7 Setup Wizard pure layer for the v1.3 Web Console.

This module holds the deterministic, offline half of the four-step Project
Setup surface (spec §4.3/§5): the Goal validator decision table, the
Supervisor configuration schema with capability honesty, the
input-registration schema and inventory projection, the canonical ZCode
Automation prompt rendering, the readiness decision table, the bounded Goal
Workshop context pack / startup prompt composition, and the Console-owned
setup draft schema.

It never touches the filesystem, the clock, randomness, or subprocesses —
the HTTP layer in `web_console_server.py` owns those. Two boundaries matter:

- The draft is Console-owned and non-authoritative. Nothing here writes
  project_state.json, PROJECT_GOAL.md, the Goal Anchor, or any other
  authoritative Runtime state; bootstrap/start delegate to the existing
  formal entry points (`start_project.py`, `START_AGENT_SYSTEM.ps1`).
- Publication honesty is structural: only `workspace/`, `evidence/`, and
  `reports/` are supported output roots; a Goal that requires `artifacts/`,
  `control/`, `handoff/`, or `projects/` outputs is rejected before
  bootstrap (the EXECUTOR-FENCE-V1 conflict the spec calls out).
"""
from __future__ import annotations

import re
import unicodedata

import web_console_control

SETUP_SCHEMA_VERSION = 1
PROJECT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
PROJECT_TYPES = ("GENERAL", "ACADEMIC_RESEARCH", "SOFTWARE_ENGINEERING",
                 "BUSINESS_RESEARCH")
SUPPORTED_PUBLICATION_ROOTS = ("workspace/", "evidence/", "reports/")

# Goal bounds. The HTTP layer additionally caps the raw request body; these
# bounds are the validator's own deterministic limits.
GOAL_MAX_BYTES = 256 * 1024
GOAL_MAX_CHARS = 100_000
GOAL_MAX_LINES = 5000
GOAL_MEANINGFUL_CHARS = 30

# A Goal must not prescribe Runtime routing internals. Each pattern is one
# deterministic warning kind; matches are surfaced, never rewritten.
_ROUTING_PATTERNS = (
    ("W_ROUTING_MESSAGE_ID", re.compile(r"MESSAGE_ID")),
    ("W_ROUTING_NONCE", re.compile(r"NONCE")),
    ("W_ROUTING_FENCE",
     re.compile(r"claim[ _-]?token|executor[ _-]?fence|fence[ _-]?check",
                re.IGNORECASE)),
    ("W_ROUTING_LEDGER", re.compile(r"completion[ _-]?ledger",
                                    re.IGNORECASE)),
    ("W_EXACT_TURN_COUNT",
     re.compile(r"\b(exactly|precisely)\s+\d+\s+(supervisor\s+)?turns?\b",
                re.IGNORECASE)),
    ("W_EXACT_TASK_COUNT",
     re.compile(r"\b(exactly|precisely)\s+\d+\s+(executor\s+)?tasks?\b",
                re.IGNORECASE)),
)

# Unsafe/unsupported publication requirements. `artifacts/` is the spec's
# named EXECUTOR-FENCE-V1 conflict; `control/`, `handoff/`, and `projects/`
# are authoritative Runtime areas no project output may target.
_UNSAFE_ROOT_PATTERN = re.compile(
    r"\b(artifacts?|control|handoff|projects)/", re.IGNORECASE)

# Supervisor configuration. The draft is Console-owned and
# non-authoritative: it only prefills the formal queued-configuration form,
# so its vocabulary mirrors the Runtime queue contract
# (`supervisor_control.SUPERVISOR_CONFIG_EFFORTS`, XHIGH confirmed from the
# installed Codex CLI, evidence/v1.4-supervisor-config-ui/). Values outside
# this set are refused on write; a stored draft with an older suggestion
# (e.g. ULTRA) still loads verbatim and is surfaced as a legacy value.
EXPLANATION_MODES = ("MINIMAL", "COMPACT", "DETAILED_ON_DEMAND")
DEFAULT_EXPLANATION_MODE = "COMPACT"
REASONING_EFFORTS = ("LOW", "MEDIUM", "HIGH", "XHIGH")
SUPERVISOR_MODEL_MAX_CHARS = 80

# Input registration bounds (metadata only; contents are never read).
INPUT_MAX_SELECTIONS = 8
INPUT_PATH_MAX_CHARS = 2048
INPUT_WALK_MAX_ENTRIES = 512
INPUT_WALK_MAX_DEPTH = 8
INPUT_INVENTORY_MAX_ENTRIES = 2000

# ZCode Automation setup. The Console renders the canonical template
# byte-equivalently to PREPARE_ZCODE_AUTOMATION.ps1 and never claims to know
# the Automation state.
ZCODE_PROMPT_PLACEHOLDER = "<RUNTIME_ROOT>"
ZCODE_TEMPLATE_MAX_BYTES = 256 * 1024
ZCODE_AUTOMATION_STEPS = (
    "1. Open ZCode.",
    "2. Create a Scheduled Automation.",
    "3. Set the Workspace to the displayed Runtime Root.",
    "4. Paste the canonical prompt (one-click copy above).",
    "5. Choose the executor model in ZCode — the Console does not choose it.",
    "6. Keep the Automation paused until preflight passes, then enable it "
    "immediately before starting the Runtime.",
)

# Goal Workshop pack bounds. The pack is composed from the canonical
# document plus Runtime facts; no external AI is ever embedded or called.
WORKSHOP_DOC_RELATIVE = "docs/WEB_AI_GOAL_WORKSHOP.md"
CONTEXT_PACK_MAX_BYTES = 64 * 1024
CONTEXT_PACK_MAX_DOC_BYTES = 48 * 1024
CONTEXT_PACK_INVENTORY_MAX_ENTRIES = 200
STARTUP_PROMPT_MAX_CHARS = 2000

DRAFT_SCHEMA_VERSION = 1


def _error(code: str, message: str, *, field: str | None = None,
           matched: str | None = None) -> dict:
    error = {"code": code, "message": message}
    if field is not None:
        error["field"] = field
    if matched is not None:
        error["matched"] = matched
    return error


def _section_meaningful(text: str) -> bool:
    stripped = re.sub(r"[#>*`\-+]", " ", text)
    return len("".join(stripped.split())) >= GOAL_MEANINGFUL_CHARS


def _split_sections(markdown: str) -> list[tuple[int, str, str]]:
    """Return (level, title, body) for every Markdown ATX heading section."""
    heading = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
    lines = markdown.splitlines()
    found = []
    for index, line in enumerate(lines):
        match = heading.match(line)
        if match:
            found.append((index, len(match.group(1)),
                          match.group(2).strip()))
    sections = []
    for position, (index, level, title) in enumerate(found):
        end = len(lines)
        for next_index, next_level, _ in found[position + 1:]:
            if next_level <= level:
                end = next_index
                break
        sections.append((level, title, "\n".join(lines[index + 1:end])))
    return sections


def _find_section(sections, keyword: str):
    for level, title, body in sections:
        if keyword in title.lower():
            return body
    return None


def validate_goal(*, project_id, project_type, goal_markdown,
                  project_id_exists: bool = False) -> dict:
    """Deterministic Goal decision table (spec §5 Step 1, Goal Validator).

    Errors block bootstrap; warnings surface Runtime-routing internals the
    Goal should not prescribe. The validator never rewrites content and
    never invents acceptance of an unsupported publication root.
    """
    errors: list[dict] = []
    warnings: list[dict] = []

    id_present = isinstance(project_id, str)
    id_valid = bool(id_present and PROJECT_ID_PATTERN.fullmatch(project_id))
    if not id_valid:
        errors.append(_error(
            "PROJECT_ID_INVALID",
            "project_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
            field="project_id"))
    type_present = isinstance(project_type, str)
    type_valid = bool(type_present and project_type in PROJECT_TYPES)
    if not type_valid:
        errors.append(_error(
            "PROJECT_TYPE_INVALID",
            "project_type must be one of " + ", ".join(PROJECT_TYPES),
            field="project_type"))

    readability = {"ok": False, "reason": None, "bytes": 0, "chars": 0,
                   "lines": 0}
    objective = {"present": False, "meaningful": False}
    deliverables = {"present": False, "meaningful": False}
    acceptance = {"present": False, "meaningful": False}
    unsafe_found: list[str] = []
    routing_found: list[str] = []

    if not isinstance(goal_markdown, str):
        readability["reason"] = "the Goal content is not text"
        errors.append(_error("GOAL_EMPTY",
                             "a non-empty Markdown Goal is required",
                             field="goal_markdown"))
    else:
        text = goal_markdown
        if text.startswith("\ufeff"):
            text = text[1:]
        readability["bytes"] = len(text.encode("utf-8"))
        readability["chars"] = len(text)
        readability["lines"] = text.count("\n") + 1
        if not text.strip():
            readability["reason"] = "the Goal is empty"
            errors.append(_error("GOAL_EMPTY",
                                 "a non-empty Markdown Goal is required",
                                 field="goal_markdown"))
        elif any(unicodedata.category(char) == "Cc" and char
                 not in ("\t", "\n", "\r") for char in text):
            readability["reason"] = "control characters are not readable"
            errors.append(_error(
                "GOAL_NOT_TEXT",
                "the Goal contains control characters and is not readable "
                "Markdown", field="goal_markdown"))
        elif readability["chars"] > GOAL_MAX_CHARS \
                or readability["lines"] > GOAL_MAX_LINES:
            readability["reason"] = "the Goal exceeds the documented bounds"
            errors.append(_error(
                "GOAL_TOO_LARGE",
                f"the Goal exceeds {GOAL_MAX_CHARS} characters or "
                f"{GOAL_MAX_LINES} lines", field="goal_markdown"))
        else:
            readability["ok"] = True
            sections = _split_sections(text)
            objective_body = _find_section(sections, "objective")
            objective["present"] = objective_body is not None
            objective["meaningful"] = bool(
                objective_body and _section_meaningful(objective_body))
            deliverables_body = _find_section(sections, "deliverable")
            deliverables["present"] = deliverables_body is not None
            deliverables["meaningful"] = bool(
                deliverables_body and _section_meaningful(deliverables_body))
            acceptance_body = _find_section(sections, "acceptance")
            acceptance["present"] = acceptance_body is not None
            acceptance["meaningful"] = bool(
                acceptance_body and _section_meaningful(acceptance_body))
            if not objective["meaningful"]:
                errors.append(_error(
                    "GOAL_OBJECTIVE_MISSING",
                    "a meaningful Objective section is required",
                    field="goal_markdown"))
            if not (deliverables["meaningful"] or acceptance["meaningful"]):
                errors.append(_error(
                    "GOAL_ACCEPTANCE_MISSING",
                    "meaningful Required Deliverables or Acceptance Criteria "
                    "content is required", field="goal_markdown"))
            for match in _UNSAFE_ROOT_PATTERN.finditer(text):
                token = match.group(0)
                if token not in unsafe_found:
                    unsafe_found.append(token)
            if unsafe_found:
                errors.append(_error(
                    "PUBLICATION_ROOT_UNSUPPORTED",
                    "the Goal requires outputs under an unsupported root; "
                    "supported publication roots are " +
                    ", ".join(SUPPORTED_PUBLICATION_ROOTS),
                    matched=", ".join(unsafe_found[:8])))
            for code, pattern in _ROUTING_PATTERNS:
                match = pattern.search(text)
                if match:
                    routing_found.append(code)
                    warnings.append(_error(
                        code,
                        "the Goal prescribes Runtime routing internals "
                        f"({match.group(0)!r}); planning belongs to the "
                        "Supervisor",
                        matched=match.group(0)[:60]))

    if project_id_exists:
        errors.append(_error(
            "PROJECT_EXISTS",
            "a project with this id already exists; projects and Goal "
            "Anchors are never overwritten", field="project_id"))

    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": {
            "project_id": {"present": id_present, "valid": id_valid},
            "project_type": {"present": type_present, "valid": type_valid},
            "goal_readability": readability,
            "objective": objective,
            "deliverables": deliverables,
            "acceptance_criteria": acceptance,
            "publication_roots": {
                "supported": list(SUPPORTED_PUBLICATION_ROOTS),
                "unsafe_found": unsafe_found},
            "routing_internals": {"found": routing_found},
            "overwrite_protection": {"project_id_exists":
                                     bool(project_id_exists)},
        },
    }


def validate_supervisor_config(payload) -> dict:
    """Exactly {model, reasoning_effort, explanation_mode} (draft only)."""
    web_console_control._require_object(payload)
    web_console_control._require_exact_keys(
        payload, ("model", "reasoning_effort", "explanation_mode"))
    model = payload["model"]
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            raise web_console_control.ControlRequestError(
                "model must be a non-empty string or null", field="model")
        model = model.strip()
        if len(model) > SUPERVISOR_MODEL_MAX_CHARS:
            raise web_console_control.ControlRequestError(
                f"model exceeds {SUPERVISOR_MODEL_MAX_CHARS} characters",
                field="model")
        if any(unicodedata.category(char) == "Cc" for char in model):
            raise web_console_control.ControlRequestError(
                "model must not contain control characters", field="model")
    effort = payload["reasoning_effort"]
    if effort is not None:
        if isinstance(effort, str):
            effort = effort.strip().upper()
        if effort not in REASONING_EFFORTS:
            raise web_console_control.ControlRequestError(
                "reasoning_effort must be one of "
                + ", ".join(REASONING_EFFORTS)
                + " or null", field="reasoning_effort")
    mode = payload["explanation_mode"]
    if not isinstance(mode, str) or mode not in EXPLANATION_MODES:
        raise web_console_control.ControlRequestError(
            "explanation_mode must be one of " +
            ", ".join(EXPLANATION_MODES), field="explanation_mode")
    return {"model": model, "reasoning_effort": effort,
            "explanation_mode": mode}


def capability_report(status_doc) -> dict:
    """Derive Supervisor choices from capabilities the Runtime reported.

    The v1.2 status document carries no capability fields, so the honest
    default is "not reported"; a future Runtime that publishes a
    well-formed `supervisor_capabilities` block is surfaced verbatim.
    """
    note = ("the Runtime and provider report no Supervisor capability "
            "list; shown choices are Console-side suggestions recorded as "
            "a setup draft")
    capabilities = status_doc.get("supervisor_capabilities") \
        if isinstance(status_doc, dict) else None
    if isinstance(capabilities, dict):
        models = capabilities.get("models")
        efforts = capabilities.get("reasoning_efforts")
        if (isinstance(models, list) and models
                and all(isinstance(item, str) for item in models)
                and isinstance(efforts, list) and efforts
                and all(isinstance(item, str) for item in efforts)):
            return {"reported": True, "models": list(models),
                    "reasoning_efforts": list(efforts),
                    "source": "supervisor_capabilities", "note": None}
        note = "the Runtime reported an unusable capability block"
    return {"reported": False, "models": [], "reasoning_efforts": [],
            "source": None, "note": note}


def validate_inputs_request(payload) -> dict:
    """Exactly {"paths": [1..N str]} or {"decision": "NONE_NEEDED"}."""
    web_console_control._require_object(payload)
    keys = set(payload)
    if keys == {"paths"}:
        paths = payload["paths"]
        if (not isinstance(paths, list) or not paths
                or len(paths) > INPUT_MAX_SELECTIONS
                or not all(isinstance(item, str) and item
                           for item in paths)):
            raise web_console_control.ControlRequestError(
                f"paths must be a list of 1..{INPUT_MAX_SELECTIONS} "
                "non-empty strings", field="paths")
        return {"paths": list(paths)}
    if keys == {"decision"}:
        if payload["decision"] != "NONE_NEEDED":
            raise web_console_control.ControlRequestError(
                'decision must be "NONE_NEEDED"', field="decision")
        return {"decision": "NONE_NEEDED"}
    raise web_console_control.ControlRequestError(
        'request schema mismatch; send exactly {"paths": [...]} or '
        '{"decision": "NONE_NEEDED"}')


def build_input_inventory(selections: list) -> dict:
    """Project walked selections into the normalized metadata inventory.

    Entries are metadata only (path/name/kind/size); contents are never
    read. Per-selection caps and selection errors surface honestly.
    """
    projected_selections = []
    entries = []
    notes: list[str] = []
    for selection in selections:
        selection_entries = selection.get("entries") or []
        ordered = sorted(selection_entries, key=lambda item: item["path"])
        complete = bool(selection.get("complete")) \
            and selection.get("error") is None
        if len(ordered) > INPUT_WALK_MAX_ENTRIES:
            ordered = ordered[:INPUT_WALK_MAX_ENTRIES]
            complete = False
            notes.append(
                f"selection {selection.get('path')!r} exceeded the "
                f"{INPUT_WALK_MAX_ENTRIES}-entry cap; its inventory is "
                "truncated")
        if selection.get("error"):
            notes.append(
                f"selection {selection.get('path')!r} could not be fully "
                f" inventoried: {selection['error']}")
        projected_selections.append({
            "path": selection.get("path"), "kind": selection.get("kind"),
            "entry_count": len(ordered), "complete": complete,
            "error": selection.get("error")})
        entries.extend(ordered)
    if len(entries) > INPUT_INVENTORY_MAX_ENTRIES:
        notes.append(
            f"the inventory was truncated to {INPUT_INVENTORY_MAX_ENTRIES} "
            "entries")
        entries = entries[:INPUT_INVENTORY_MAX_ENTRIES]
    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "decision": "REGISTERED",
        "selections": projected_selections,
        "entries": entries,
        "total_entries": len(entries),
        "complete": all(item["complete"] for item in projected_selections),
        "honesty": {"notes": notes},
    }


def decode_zcode_template(raw: bytes) -> str:
    """Strictly decode the canonical template (BOM tolerated, UTF-8 only)."""
    if not raw:
        raise ValueError("the canonical prompt template is empty")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(
            "the canonical prompt template is not valid UTF-8") from exc


def render_zcode_prompt(template_text: str, runtime_root: str) -> str:
    """Render the canonical prompt byte-equivalently to the PS1 helper."""
    if ZCODE_PROMPT_PLACEHOLDER not in template_text:
        raise ValueError(
            "the canonical prompt template contains no literal "
            f"{ZCODE_PROMPT_PLACEHOLDER} placeholder")
    rendered = template_text.replace(ZCODE_PROMPT_PLACEHOLDER, runtime_root)
    if ZCODE_PROMPT_PLACEHOLDER in rendered:
        raise ValueError(
            f"prompt rendering failed closed because a literal "
            f"{ZCODE_PROMPT_PLACEHOLDER} placeholder remains")
    return rendered


def compose_goal_workshop_pack(*, doc_text, runtime_facts,
                               project_type, input_inventory) -> str:
    """Bounded, deterministic copyable context pack for an external Web AI.

    Composed from the canonical workshop document plus Runtime facts and the
    current input inventory. The Runtime Root path is deliberately excluded,
    and no external AI is ever embedded, called, or automated.
    """
    label = runtime_facts.get("label") if isinstance(runtime_facts, dict) \
        else None
    schema_version = runtime_facts.get("status_schema_version") \
        if isinstance(runtime_facts, dict) else None
    lines = [
        "# General Agent Runtime — Goal Workshop context pack",
        "",
        "## Runtime facts",
        f"- Console label: {label if isinstance(label, str) and label else 'not reported'}",
        f"- Control-plane status schema version: {schema_version if isinstance(schema_version, int) else 'not reported'}",
        "- Supported publication roots: "
        + ", ".join(SUPPORTED_PUBLICATION_ROOTS),
        "- Outputs under artifacts/, control/, handoff/, or projects/ are "
        "unsupported and will be rejected before bootstrap.",
    ]
    lines.append("- Selected ProjectType: "
                 + (project_type if isinstance(project_type, str)
                    and project_type else "not selected yet"))
    lines += [
        "",
        "## ProjectType semantics",
        "- GENERAL: open-ended goal-directed work with the default profile.",
        "- ACADEMIC_RESEARCH: research questions, citations, and evidence "
        "standards.",
        "- SOFTWARE_ENGINEERING: codebases, tests, and engineering "
        "deliverables.",
        "- BUSINESS_RESEARCH: market/competitive analysis and decision "
        "memos.",
        "",
        "## Current input inventory",
    ]
    if isinstance(input_inventory, dict) and input_inventory.get("entries"):
        shown = input_inventory["entries"][
            :CONTEXT_PACK_INVENTORY_MAX_ENTRIES]
        for entry in shown:
            size = entry.get("size_bytes")
            lines.append(f"- {entry.get('path')} ({entry.get('kind')}"
                         + (f", {size} bytes" if isinstance(size, int)
                            else "") + ")")
        total = input_inventory.get("total_entries")
        if isinstance(total, int) and total > len(shown):
            lines.append(f"- … inventory truncated at {len(shown)} of "
                         f"{total} entries")
        if not input_inventory.get("complete"):
            lines.append("- Note: the inventory is incomplete; see the "
                         "Console honesty notes.")
    else:
        lines.append("- no input inventory has been registered yet")
    lines += [
        "",
        "## Goal-writing rules",
        "- The Goal defines the destination: objective, context, inputs, "
        "deliverables, constraints, non-goals, acceptance criteria, "
        "evidence standard, and risk boundaries.",
        "- A Goal must NOT prescribe MESSAGE_ID values, NONCE values, "
        "claim/fence/completion mechanics, exact Supervisor-turn or "
        "Executor-task counts, exact authorization sequences, or retry "
        "internals.",
        "- Meaningful Objective and Deliverables/Acceptance Criteria "
        "content is required; the mechanical validator enforces this.",
        "",
        "## Runtime Goal Workshop Contract",
    ]
    if isinstance(doc_text, str) and doc_text.strip():
        encoded = doc_text.encode("utf-8")
        if len(encoded) > CONTEXT_PACK_MAX_DOC_BYTES:
            doc_text = encoded[:CONTEXT_PACK_MAX_DOC_BYTES].decode(
                "utf-8", errors="replace")
            lines.append(doc_text)
            lines.append("\n[CONTRACT_TRUNCATED — the canonical document "
                         "continues; read "
                         f"{WORKSHOP_DOC_RELATIVE} in the Runtime tree]")
        else:
            lines.append(doc_text)
    else:
        lines.append("[CONTRACT_UNAVAILABLE — the canonical workshop "
                     f"document {WORKSHOP_DOC_RELATIVE} could not be read; "
                     "ask the human to provide it]")
    pack = "\n".join(lines)
    if len(pack.encode("utf-8")) > CONTEXT_PACK_MAX_BYTES:
        target = pack.encode("utf-8")[:CONTEXT_PACK_MAX_BYTES]
        pack = target.decode("utf-8", errors="ignore")
    return pack


def compose_startup_prompt(*, project_type) -> str:
    """Short instruction the user pastes into their external Web AI."""
    type_line = (f"The selected ProjectType is {project_type}."
                 if isinstance(project_type, str) and project_type
                 else "The ProjectType is not selected yet.")
    return (
        "You are acting as a General Agent Runtime Goal Workshop partner. "
        "Ask the human to paste the Goal Workshop context pack from the "
        "Runtime Web Console, then help refine the human's project idea "
        "into one complete Markdown Project Goal. " + type_line +
        " Follow the contract in the pack: cover Objective, Context, "
        "Available Inputs, Required Deliverables, Constraints, Non-Goals, "
        "Acceptance Criteria, Evidence and Reproducibility, and Risk "
        "Boundaries; never prescribe Runtime routing internals such as "
        "MESSAGE_ID, NONCE, claim/fence mechanics, or exact turn counts; "
        "keep outputs inside the supported publication roots. Finish by "
        "asking the human to approve the final text before it is imported."
    )


def readiness_document(*, draft, goal_validation, status_failure,
                       status_doc, preflight, python_check,
                       project_id_exists, profile_present) -> dict:
    """The ten spec readiness checks, each exposed separately (§5 Step 4).

    Start stays fail-closed: every check is required, and unknown or
    contradictory facts fail the affected check instead of being guessed.
    """
    checks: list[dict] = []

    def add(key, label, passed, detail):
        checks.append({"key": key, "label": label,
                       "state": "PASS" if passed else "FAIL",
                       "required": True, "detail": detail})

    goal_ok = bool(draft.get("goal_markdown")
                   and isinstance(goal_validation, dict)
                   and goal_validation.get("valid"))
    add("goal_loaded", "Goal loaded and valid", goal_ok,
        "the draft Goal passed the deterministic validator" if goal_ok
        else "no validated Goal draft exists")

    anchor_ok = goal_ok and not project_id_exists
    add("goal_anchor", "Goal Anchor ready or creatable", anchor_ok,
        "the formal bootstrap binds the Goal Anchor (GOAL-ANCHOR-V1) at "
        "project creation" if anchor_ok else
        ("a project with this id already exists; Goal Anchors are never "
         "overwritten" if project_id_exists else
         "load a valid Goal first"))

    inputs = draft.get("inputs") if isinstance(draft, dict) else None
    inputs_ok = bool(isinstance(inputs, dict) and (
        inputs.get("decision") == "NONE_NEEDED"
        or (inputs.get("decision") == "REGISTERED"
            and isinstance(inputs.get("total_entries"), int)
            and inputs["total_entries"] >= 1)))
    add("inputs_registered", "Inputs registered", inputs_ok,
        "an input inventory or an explicit none-needed decision exists"
        if inputs_ok else
        "register at least one input or record that none are needed")

    project_type = draft.get("project_type")
    type_ok = bool(project_type in PROJECT_TYPES and profile_present)
    add("project_type_valid", "ProjectType valid", type_ok,
        f"{project_type} profile is present in the Runtime" if type_ok
        else "select a supported ProjectType with a Runtime profile")

    supervisor = draft.get("supervisor") if isinstance(draft, dict) else None
    supervisor_ok = bool(isinstance(supervisor, dict)
                         and supervisor.get("explanation_mode")
                         in EXPLANATION_MODES)
    add("supervisor_config_valid", "Supervisor configuration valid (draft)",
        supervisor_ok,
        "the setup draft is recorded; it applies only when a supported "
        "Runtime interface consumes it — no active turn was changed"
        if supervisor_ok else "save a Supervisor configuration draft")

    runtime_ok = status_failure is None and isinstance(status_doc, dict)
    add("runtime_healthy", "Runtime healthy", runtime_ok,
        "the control plane answered the read-only status probe"
        if runtime_ok else f"status probe failed: {status_failure}")

    python_ok = bool(isinstance(python_check, dict)
                     and python_check.get("ok"))
    add("python_requirements", "Python/runtime requirements", python_ok,
        f"server Python {python_check.get('python_version', 'unknown')}"
        if python_ok else "the Python runtime does not meet the minimum")

    plane_ok = bool(runtime_ok and status_doc.get("schema_version") == 1)
    add("control_plane_v12", "v1.2 control plane available", plane_ok,
        "supervisor_control.py answers with status schema_version 1"
        if plane_ok else "the control plane did not answer with a "
        "supported schema")

    zcode = draft.get("zcode") if isinstance(draft, dict) else None
    ack_ok = bool(isinstance(zcode, dict) and zcode.get("acknowledged"))
    add("zcode_acknowledged", "ZCode setup acknowledged", ack_ok,
        "a human acknowledged the ZCode Automation steps in this Console"
        if ack_ok else "acknowledge the ZCode Automation setup steps")

    preflight_ok = bool(isinstance(preflight, dict) and preflight.get("ok"))
    add("preflight", "Preflight", preflight_ok,
        (preflight.get("head") or "PREFLIGHT: OK").splitlines()[0]
        if preflight_ok else
        (str((preflight or {}).get("head") or "preflight did not pass")))

    return {
        "schema_version": SETUP_SCHEMA_VERSION,
        "ready": all(check["state"] == "PASS" for check in checks),
        "checks": checks,
    }


# -- Console-owned setup draft (non-authoritative) ---------------------------

def empty_draft() -> dict:
    return {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "project_id": None,
        "project_type": None,
        "goal_markdown": None,
        "goal_source_name": None,
        "inputs": None,
        "supervisor": None,
        "zcode": {"acknowledged": False, "acknowledged_at": None},
        "updated_at": None,
    }


def normalize_draft(raw) -> tuple[dict | None, str | None]:
    """Type-check a stored draft; unusable documents are never guessed."""
    if not isinstance(raw, dict):
        return None, "the stored setup draft is missing or not an object"
    if raw.get("schema_version") != DRAFT_SCHEMA_VERSION:
        return None, "the stored setup draft has an unsupported schema"
    draft = empty_draft()
    project_id = raw.get("project_id")
    if project_id is not None and not isinstance(project_id, str):
        return None, "the stored setup draft has an invalid project_id"
    project_type = raw.get("project_type")
    if project_type is not None and not isinstance(project_type, str):
        return None, "the stored setup draft has an invalid project_type"
    goal = raw.get("goal_markdown")
    if goal is not None and not isinstance(goal, str):
        return None, "the stored setup draft has an invalid goal_markdown"
    source_name = raw.get("goal_source_name")
    if source_name is not None and not isinstance(source_name, str):
        return None, "the stored setup draft has an invalid goal_source_name"
    inputs = raw.get("inputs")
    if inputs is not None and not isinstance(inputs, dict):
        return None, "the stored setup draft has an invalid inputs block"
    supervisor = raw.get("supervisor")
    if supervisor is not None and not isinstance(supervisor, dict):
        return None, "the stored setup draft has an invalid supervisor block"
    zcode = raw.get("zcode")
    if zcode is not None:
        if not isinstance(zcode, dict) or not isinstance(
                zcode.get("acknowledged"), bool):
            return None, "the stored setup draft has an invalid zcode block"
        draft["zcode"] = {"acknowledged": zcode["acknowledged"],
                          "acknowledged_at": zcode.get("acknowledged_at")
                          if isinstance(zcode.get("acknowledged_at"), str)
                          else None}
    draft.update({
        "project_id": project_id, "project_type": project_type,
        "goal_markdown": goal, "goal_source_name": source_name,
        "inputs": inputs, "supervisor": supervisor})
    return draft, None


def merge_goal_into_draft(draft: dict, *, project_id: str,
                          project_type: str, goal_markdown: str,
                          source_name: str | None) -> dict:
    merged = dict(draft)
    merged["project_id"] = project_id
    merged["project_type"] = project_type
    merged["goal_markdown"] = goal_markdown
    merged["goal_source_name"] = source_name
    return merged


def merge_inputs_into_draft(draft: dict, inventory: dict) -> dict:
    entries = inventory.get("entries")
    if not isinstance(entries, list) \
            or len(entries) > INPUT_INVENTORY_MAX_ENTRIES:
        raise web_console_control.ControlRequestError(
            f"the inventory exceeds {INPUT_INVENTORY_MAX_ENTRIES} entries",
            field="inputs")
    merged = dict(draft)
    merged["inputs"] = {
        "decision": inventory.get("decision"),
        "entries": list(entries),
        "selections": list(inventory.get("selections") or []),
        "total_entries": inventory.get("total_entries"),
        "complete": inventory.get("complete"),
        "honesty": dict(inventory.get("honesty") or {"notes": []}),
    }
    return merged


def merge_none_needed_into_draft(draft: dict) -> dict:
    merged = dict(draft)
    merged["inputs"] = {
        "decision": "NONE_NEEDED", "entries": [], "selections": [],
        "total_entries": 0, "complete": True,
        "honesty": {"notes": ["the user recorded that no additional "
                              "inputs are needed"]},
    }
    return merged


def merge_supervisor_into_draft(draft: dict, config: dict) -> dict:
    merged = dict(draft)
    merged["supervisor"] = dict(config)
    return merged


def merge_zcode_acknowledgement(draft: dict, acknowledged_at: str) -> dict:
    merged = dict(draft)
    merged["zcode"] = {"acknowledged": True,
                       "acknowledged_at": acknowledged_at}
    return merged
