"""Real-format Runtime-history fixture builder for v1.3 P4 verification.

Test and verification infrastructure only — never imported by product code.

Constructs synthetic Runtime Roots whose on-disk history uses the exact v1.2
formats, written through the real v1.2 code paths wherever one exists:

- dispatch archives + authorization seals via
  ``supervisor_control.archive_dispatch`` / ``seal_dispatch_authorization``;
- decision receipts under ``control/supervisor_decisions/<turn_id>.json``
  hash-bound with ``supervisor_control.canonical_json_bytes``;
- completion-ledger entries hash-bound exactly like
  ``executor_completion.build_entry`` (RECEIPT_SHA256 / BRIEF_SHA256 /
  CLAIM_IDENTITY_SHA256), in ``handoff/completion_ledger/``;
- intervention records under ``handoff/supervisor_interventions/``.

Every timestamp is an explicit argument, so fixtures are deterministic and
nothing here reads a clock. Callers never point these builders at a real
Runtime; fixtures live only under temporary directories.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent

# The read-only history commands import these siblings at call time
# (list_feedback imports executor_completion), so fixture Runtime Roots
# carry real copies of the v1.2 scripts.
RUNTIME_SCRIPTS = (
    "supervisor_control.py",
    "executor_completion.py",
    "executor_claim.py",
    "executor_fence.py",
)

MANIFEST_DUMMY_SHA256 = "0" * 64


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_control_bytes(value) -> bytes:
    """Byte-exact mirror of supervisor_control.canonical_json_bytes."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def new_runtime_root(base: Path, project_id: str,
                     *, source_scripts: Path = SCRIPTS) -> Path:
    """Create a minimal valid Runtime Root with the real v1.2 scripts."""
    root = Path(base)
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for name in RUNTIME_SCRIPTS:
        shutil.copyfile(source_scripts / name, root / "scripts" / name)
    control = root / "control"
    control.mkdir(parents=True, exist_ok=True)
    (control / "ACTIVE_PROJECT.json").write_text(
        json.dumps({"schema_version": 1, "project_id": project_id,
                    "project_root": f"projects/{project_id}"},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    project = root / "projects" / project_id
    project.mkdir(parents=True, exist_ok=True)
    (project / "project_state.json").write_text(
        json.dumps({"project_id": project_id, "status": "WAITING_EXECUTOR"},
                   ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return root


def dispatch_document(identity: dict, title: str, body: str) -> str:
    """A TO_ZCODE-shaped dispatch: prose plus exactly one ```json fence."""
    fence = json.dumps(dict(identity), ensure_ascii=False, indent=2)
    return (f"MESSAGE_ID: {identity['MESSAGE_ID']}\n"
            f"{title}\n\n"
            f"{body}\n\n"
            f"```json\n{fence}\n```\n")


def add_decision_receipt(root: Path, turn_id: str, decision: dict, *,
                         project_id: str,
                         originating_control_revision: int = 7,
                         resulting_status: str = "WAITING_EXECUTOR",
                         committed_at: str,
                         intervention_ids=None) -> str:
    """Write one decision receipt; return its canonical SHA-256 binding."""
    decision_part = {
        "decision": decision.get("decision", "CONTINUE"),
    }
    for key in ("reason", "decision_summary"):
        if key in decision:
            decision_part[key] = decision[key]
    receipt = {
        "schema_version": 1,
        "turn_id": turn_id,
        "PROJECT_ID": project_id,
        "originating_control_revision": originating_control_revision,
        "intervention_ids": list(intervention_ids or []),
        "invocation": None,
        "state_sha256_before": None,
        "state_sha256_after": None,
        "decision_history_index": None,
        "decision": decision_part,
        "decision_sha256": sha256_bytes(canonical_control_bytes(decision_part)),
        "resulting_status": resulting_status,
        "candidate": None,
        "committed_at": committed_at,
    }
    path = root / "control" / "supervisor_decisions" / f"{turn_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return sha256_bytes(canonical_control_bytes(receipt))


def add_archived_dispatch(root: Path, project_id: str, identity: dict,
                          dispatch_text: str, *,
                          archived_at: str,
                          turn_id: str | None = None,
                          decision_receipt_sha256: str | None = None,
                          authorized_at: str | None = None) -> dict:
    """Archive + seal one dispatch; returns the archive metadata record."""
    import supervisor_control

    data = dispatch_text.encode("utf-8")
    origin = None
    if turn_id is not None:
        if decision_receipt_sha256 is None:
            raise ValueError("a bound dispatch needs its decision receipt hash")
        origin = {
            "originating_control_revision": 7,
            "supervisor_turn_id": turn_id,
            "decision_receipt_sha256": decision_receipt_sha256,
        }
    metadata = supervisor_control.archive_dispatch(
        root, project_id, identity, data,
        archived_at=archived_at, origin=origin)
    authorization = {
        "schema_version": 1,
        **{key: identity[key] for key in
           ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")},
        "PROJECT_ID": project_id,
        "TO_ZCODE_SHA256": metadata["dispatch_sha256"],
        "AUTHORIZED_AT": authorized_at or archived_at,
        "SUPERVISOR_CONTROL_ORIGIN": {
            "originating_control_revision":
                origin["originating_control_revision"] if origin else None,
            "supervisor_turn_id": turn_id,
            "decision_receipt_sha256": decision_receipt_sha256,
        },
        "SUPERVISOR_DISPATCH_ARCHIVE": {
            "schema_version": 1,
            "metadata_file": metadata["metadata_file"],
            "archive_file": metadata["archive_file"],
            "authorization_file": metadata["authorization_file"],
            "dispatch_sha256": metadata["dispatch_sha256"],
        },
    }
    supervisor_control.seal_dispatch_authorization(root, authorization)
    return metadata


def add_completion(root: Path, project_id: str, identity: dict, receipt: dict,
                   *, status: str = "COMPLETION_COMMITTED",
                   committed_at: str, consumed_at: str | None = None,
                   sealed_at: str | None = None) -> dict:
    """Write one hash-bound completion-ledger entry (build_entry shape)."""
    import executor_completion as completion

    validated = completion._validated_identity(receipt, "RECEIPT")
    normalized = completion._validated_identity(identity, "identity")
    if validated != normalized:
        raise ValueError("receipt identity does not match the entry identity")
    commit_id = completion.commit_id_for(normalized["MESSAGE_ID"],
                                         normalized["NONCE"])
    nonce_digest = completion.nonce_digest(normalized["NONCE"])
    entry = {
        "COMPLETION_PROTOCOL_VERSION": completion.COMPLETION_PROTOCOL_VERSION,
        "COMMIT_ID": commit_id,
        "STATUS": status,
        **normalized,
        "PROJECT_ID": project_id,
        "CLAIM_DIR": (f"handoff/executor_claims/"
                      f"{normalized['MESSAGE_ID']}-{nonce_digest}.claim"),
        "CLAIM_IDENTITY_SHA256": completion.canonical_json_sha256(normalized),
        "RECEIPT_SHA256": completion.canonical_json_sha256(receipt),
        "BRIEF_SHA256": completion.sha256_bytes(
            completion.render_brief_bytes(receipt)),
        "STAGING_MANIFEST_SHA256": MANIFEST_DUMMY_SHA256,
        "COMMITTED_AT": committed_at,
        "CONSUMED_AT": consumed_at,
        "SEALED_AT": sealed_at,
        "CONSUMED_ARCHIVE": None,
        "RECEIPT": receipt,
    }
    path = root / "handoff" / "completion_ledger" / f"{commit_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(entry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return entry


def add_intervention(root: Path, project_id: str, intervention_id: str, *,
                     mode: str = "STEER", target_message_id: int | None = None,
                     interrupt_current: bool = False,
                     submitted_at: str, instruction_text: str,
                     status: str = "PENDING",
                     decision_receipt_file: str | None = None,
                     decision_receipt_sha256: str | None = None) -> dict:
    """Write one intervention record (intervention.json + instruction.txt)."""
    request_dir = (root / "handoff" / "supervisor_interventions"
                   / project_id / intervention_id)
    request_dir.mkdir(parents=True, exist_ok=True)
    data = instruction_text.encode("utf-8")
    (request_dir / "instruction.txt").write_bytes(data)
    meta = {
        "schema_version": 1,
        "intervention_id": intervention_id,
        "PROJECT_ID": project_id,
        "mode": mode,
        "target_message_id": target_message_id,
        "interrupt_current": interrupt_current,
        "submitted_at": submitted_at,
        "instruction_sha256": sha256_bytes(data),
        "instruction_file":
            (request_dir / "instruction.txt").relative_to(root).as_posix(),
    }
    (request_dir / "intervention.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    status_doc = {"status": status}
    if decision_receipt_file is not None:
        status_doc["decision_receipt_file"] = decision_receipt_file
        status_doc["decision_receipt_sha256"] = decision_receipt_sha256
    (request_dir / "status.json").write_text(
        json.dumps(status_doc, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    return meta


def make_receipt(identity: dict, *, status: str, summary: str,
                 created_at: str, deliverables=None, evidence=None) -> dict:
    """An authoritative-completion-shaped receipt bound to the identity."""
    receipt = {
        **{key: identity[key] for key in
           ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE")},
        "STATUS": status,
        "SUMMARY": summary,
        "CREATED_AT": created_at,
    }
    if deliverables is not None:
        receipt["DELIVERABLES"] = deliverables
    if evidence is not None:
        receipt["EVIDENCE"] = evidence
    return receipt
