from pathlib import Path
import hashlib
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import executor_claim as c

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    (root / "ZCODE_LAST_PROCESSED.txt").write_text("700005\n", encoding="utf-8")
    task = {
        "PROTOCOL_VERSION": 2,
        "CLAIM_PROTOCOL_VERSION": 1,
        "MESSAGE_ID": 700006,
        "TASK_ID": "PET",
        "STAGE_ID": "DISCOVERY",
        "ATTEMPT": 1,
        "NONCE": "same-nonce",
        "OBJECTIVE": "fixture",
        "OUTPUTS": [],
    }
    wire = "```json\n" + json.dumps(task, indent=2) + "\n```\n"
    (root / "TO_ZCODE.md").write_text(wire, encoding="utf-8")
    (root / "control").mkdir()
    auth = {
        "schema_version": 1,
        **{key: task[key] for key in c.IDENTITY_KEYS},
        "TO_ZCODE_SHA256": hashlib.sha256((root / "TO_ZCODE.md").read_bytes()).hexdigest(),
        "AUTHORIZED_AT": c.now_iso(),
    }
    (root / "control" / "orchestrator_runtime.json").write_text(
        json.dumps({"authorized_dispatch": auth, "retired_message_ids": []}),
        encoding="utf-8",
    )
    first = c.acquire(root, 700006, "PET", "DISCOVERY", 1, "same-nonce")
    second = c.acquire(root, 700006, "PET", "DISCOVERY", 1, "same-nonce")
    stale = c.acquire(root, 700005, "OLD", "OLD", 1, "old-nonce")
    assert first == c.EXIT_ACQUIRED
    assert second == c.EXIT_CLAIM_EXISTS
    assert stale == c.EXIT_ALREADY_PROCESSED
print("EXECUTOR_CLAIM_CONCURRENCY_TEST: OK")

# ---- canonical ZCODE_LAST_PROCESSED.txt parser regression (v2 wire + legacy) ----
# The same parser backs executor_claim, resume_human_review, and preflight.

V2_POINTER = (
    "MESSAGE_ID=700104\n"
    "TASK_ID=COLD_START_FINAL_VERIFICATION\n"
    "STAGE_ID=FINAL_VERIFICATION_ATTEMPT_3\n"
    "ATTEMPT=3\n"
    "NONCE=307d49a803e949e7b7cbeeb6e89641eb9ebf79c6c01f4183\n"
)


def expect_malformed(text):
    try:
        c.parse_last_processed_identity(text)
    except c.LastProcessedFormatError:
        return
    raise AssertionError(f"expected LastProcessedFormatError for {text!r}")


parsed_v2 = c.parse_last_processed_identity(V2_POINTER)
assert parsed_v2 == {
    "MESSAGE_ID": 700104,
    "TASK_ID": "COLD_START_FINAL_VERIFICATION",
    "STAGE_ID": "FINAL_VERIFICATION_ATTEMPT_3",
    "ATTEMPT": 3,
    "NONCE": "307d49a803e949e7b7cbeeb6e89641eb9ebf79c6c01f4183",
}, parsed_v2

# Legacy numeric pointers stay accepted (bootstrap seeds "0").
assert c.parse_last_processed_identity("0\n") == {"MESSAGE_ID": 0}
assert c.parse_last_processed_identity("  700099  ") == {"MESSAGE_ID": 700099}

# A single key=value line is still a partial v2 pointer: missing fields fail closed.
expect_malformed("MESSAGE_ID=700104\nATTEMPT=1\n")

# Order-insensitive key=value set, CRLF-tolerant.
reordered = (
    "NONCE=nonce-x\n"
    "ATTEMPT=2\r\n"
    "STAGE_ID=S2\r\n"
    "TASK_ID=T2\r\n"
    "MESSAGE_ID=7\r\n"
)
assert c.parse_last_processed_identity(reordered) == {
    "MESSAGE_ID": 7, "TASK_ID": "T2", "STAGE_ID": "S2", "ATTEMPT": 2, "NONCE": "nonce-x",
}

# Fail closed: empty, legacy junk, partial key=value, duplicates, unknown keys,
# illegal MESSAGE_ID/ATTEMPT, whitespace-damaged values.
expect_malformed("")
expect_malformed("   \n")
expect_malformed("-5\n")
expect_malformed("700104abc\n")
expect_malformed("MESSAGE_ID=700104\n")
expect_malformed(V2_POINTER.replace("MESSAGE_ID=700104\n", ""))
expect_malformed(V2_POINTER.replace("MESSAGE_ID=700104\n", "message_id=700104\n"))
expect_malformed(V2_POINTER + "MESSAGE_ID=700104\n")
expect_malformed(V2_POINTER + "EXTRA=1\n")
expect_malformed(V2_POINTER.replace("MESSAGE_ID=700104", "MESSAGE_ID=-700104"))
expect_malformed(V2_POINTER.replace("MESSAGE_ID=700104", "MESSAGE_ID=0x700104"))
expect_malformed(V2_POINTER.replace("ATTEMPT=3", "ATTEMPT=THREE"))
expect_malformed(V2_POINTER.replace("ATTEMPT=3", "ATTEMPT=0"))
expect_malformed(V2_POINTER.replace("ATTEMPT=3", "ATTEMPT=-1"))
expect_malformed(V2_POINTER.replace("ATTEMPT=3", "ATTEMPT=1.5"))
expect_malformed(V2_POINTER.replace("TASK_ID=COLD_START_FINAL_VERIFICATION", "TASK_ID= "))
expect_malformed(V2_POINTER.replace("NONCE=307d49a803e949e7b7cbeeb6e89641eb9ebf79c6c01f4183", "NONCE=  "))
expect_malformed(V2_POINTER + "\nMESS=broken\n")

with tempfile.TemporaryDirectory() as td:
    bad_root = Path(td)
    (bad_root / "ZCODE_LAST_PROCESSED.txt").write_text("not-a-pointer\n", encoding="utf-8")
    err = c.acquire(bad_root, 700105, "T", "S", 1, "n")
    assert err == c.EXIT_ERROR, err
    assert not (bad_root / "handoff").exists(), "claim CAS must not run on a malformed pointer"

with tempfile.TemporaryDirectory() as td:
    empty_root = Path(td)
    args = (empty_root, 700001, "T", "S", 1, "n")
    assert c.acquire(*args) == c.EXIT_ERROR, "missing authorization must fail closed"

print("EXECUTOR_CLAIM_LAST_PROCESSED_PARSER_TEST: OK")
