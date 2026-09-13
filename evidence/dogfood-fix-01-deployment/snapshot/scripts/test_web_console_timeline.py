"""P4 Web Console Timeline + Task Detail endpoint tests (offline, localhost).

Covers the two new read-only routes on top of the accepted P2 opaque-ID root
binding and P3 Cockpit surface:

- `GET /api/runtimes/<id>/timeline` — versioned, bounded, paginated round
  history with validated ordering/search/filter query parameters.
- `GET /api/runtimes/<id>/rounds/<message-id>` — Task Detail composition
  including the exact archived dispatch relay, the authoritative completion,
  interventions, the hash-verified Supervisor decision receipt, and
  artifact metadata provenance.

Fail-closed behavior is pinned over the wire: unknown/removed/traversal/
near-miss Runtime IDs, query rebinding, malformed control-plane output,
non-list timeline documents, and unknown rounds. Two unmistakably different
fixture Runtimes prove switching/isolation; Unicode and hostile strings
round-trip as UTF-8; the P1/P2/P3 routes remain intact. No GUI, no external
network, no mutation of any managed Runtime tree.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_server as wcs

ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

MARKER_A = "TIMELINE-ROOT-A-café"
MARKER_B = "TIMELINE-ROOT-B-中文"

STATUS_OK = {
    "schema_version": 1, "PROJECT_ID": "proj-x", "runtime_status": "RUNNING",
    "project_status": "WAITING_EXECUTOR",
    "pause": {"status": "RUNNING", "requested_at": None, "mode": "SAFE",
              "resumed_at": None},
    "active_task": None, "last_authorized_dispatch": None,
    "active_task_claimed": False, "active_task_claim_recorded": False,
    "active_task_completion_status": None, "active_task_retired": False,
    "pending_interventions": 0, "last_consumed_message_id": None,
    "human_review": False, "stop": False,
}

IDENTITY_A1 = {"MESSAGE_ID": 700501, "TASK_ID": "TASK-A-α",
               "STAGE_ID": "stage-a1", "ATTEMPT": 1, "NONCE": "a" * 24}
IDENTITY_A2 = {"MESSAGE_ID": 700502, "TASK_ID": "TASK-A-β",
               "STAGE_ID": "stage-a2", "ATTEMPT": 2, "NONCE": "c" * 24}
IDENTITY_B1 = {"MESSAGE_ID": 700601, "TASK_ID": "TASK-B-中",
               "STAGE_ID": "stage-b1", "ATTEMPT": 1, "NONCE": "b" * 24}

EXACT_DISPATCH_A1 = (
    "# TO_ZCODE — MESSAGE 700501\n\n```json\n"
    + json.dumps(IDENTITY_A1, ensure_ascii=False, sort_keys=True)
    + "\n```\n\nImplement the P4 slice. <script>alert('hostile')</script>\n"
)
EXACT_DISPATCH_B1 = (
    "# TO_ZCODE — MESSAGE 700601\n\n```json\n"
    + json.dumps(IDENTITY_B1, ensure_ascii=False, sort_keys=True)
    + "\n```\n\nRuntime B completely different dispatch 中文 ✅\n"
)

def canonical_decision_hash(value) -> str:
    """Mirror v1.2 supervisor_control.canonical_json_bytes (trailing LF)."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


DECISION_RECEIPT_A1 = {
    "schema_version": 1,
    "turn_id": "turn-700501",
    "PROJECT_ID": "proj-x",
    "originating_control_revision": 3,
    "intervention_ids": [],
    "invocation": {"source": "project_state"},
    "state_sha256_before": "0" * 64,
    "state_sha256_after": "1" * 64,
    "decision_history_index": 4,
    "decision": {"decision": "DISPATCH_EXECUTOR",
                 "reason": "P4 needs a bounded Timeline slice"},
    "decision_sha256": "2" * 64,
    "resulting_status": "WAITING_EXECUTOR",
    "candidate": {**IDENTITY_A1, "dispatch_sha256": "d" * 64},
    "committed_at": "2026-09-12T00:55:00+00:00",
}

DECISION_A1_SHA = canonical_decision_hash(DECISION_RECEIPT_A1)


def dispatch_record(identity, *, integrity="AUTHORIZED_VALID",
                    archived_at="2026-09-12T01:00:00+00:00",
                    exact=None, turn="turn-700501", error=None,
                    decision_sha=None):
    record = {"schema_version": 1, "PROJECT_ID": "proj-x",
              **identity, "archived_at": archived_at,
              "dispatch_sha256": "d" * 64,
              "archive_file": f"handoff/supervisor_dispatch_archive/proj-x/"
                              f"dispatch-{identity['MESSAGE_ID']}-"
                              f"{'0' * 24}.md",
              "originating_control_revision": 3,
              "supervisor_turn_id": turn,
              "decision_receipt_sha256": decision_sha or "d" * 64,
              "metadata_file": "meta.json",
              "authorization_file": "seal.json",
              "integrity": integrity, "trust_status": integrity}
    if exact is not None:
        record["exact_dispatch"] = exact
    if error is not None:
        record["error"] = error
    return record


def completion_record(identity, *, status="COMPLETION_SEALED",
                      committed_at="2026-09-12T01:02:00+00:00",
                      integrity="OK", receipt_status="COMPLETE"):
    return {"COMPLETION_PROTOCOL_VERSION": 1, "STATUS": status,
            **identity, "PROJECT_ID": "proj-x",
            "CLAIM_DIR": "claim-dir", "CLAIM_IDENTITY_SHA256": "e" * 64,
            "COMMIT_ID": f"completion-{identity['MESSAGE_ID']}-abc123",
            "RECEIPT_SHA256": "f" * 64, "BRIEF_SHA256": "0" * 64,
            "STAGING_MANIFEST_SHA256": "1" * 64,
            "COMMITTED_AT": committed_at, "CONSUMED_AT": None,
            "SEALED_AT": None, "CONSUMED_ARCHIVE": None,
            "ledger_file": f"handoff/completion_ledger/completion-"
                           f"{identity['MESSAGE_ID']}-abc123.json",
            "integrity": integrity,
            "RECEIPT": {**identity, "PROJECT_ID": "proj-x",
                        "STATUS": receipt_status,
                        "OUTCOME": "stage delivered with evidence",
                        "PUBLISHED_PATHS": [
                            {"path": "reports/p4-x.md", "sha256": "b" * 64},
                            {"path": "evidence/x/tests.txt",
                             "sha256": "c" * 64}]}}


def timeline_document_a():
    events = [
        {"type": "SUPERVISOR_DISPATCH", "at": "2026-09-12T01:00:00+00:00",
         "MESSAGE_ID": 700501,
         "record": dispatch_record(IDENTITY_A1, exact=EXACT_DISPATCH_A1)},
        {"type": "EXECUTOR_COMPLETION", "at": "2026-09-12T01:02:00+00:00",
         "MESSAGE_ID": 700501,
         "record": completion_record(IDENTITY_A1)},
        {"type": "SUPERVISOR_DISPATCH", "at": "2026-09-12T02:00:00+00:00",
         "MESSAGE_ID": 700502,
         "record": dispatch_record(
             IDENTITY_A2, turn="turn-700502", archived_at="2026-09-12T02:00:00+00:00",
             integrity="UNAUTHORIZED", error="authorization seal is missing")},
        {"type": "HUMAN_INTERVENTION",
         "at": "2026-09-12T01:05:00+00:00", "MESSAGE_ID": 700501,
         "record": {"intervention_id": "H-1001", "mode": "STEER",
                    "target_message_id": 700501,
                    "submitted_at": "2026-09-12T01:05:00+00:00",
                    "status": "CONSUMED", "integrity": "OK",
                    "instruction_text": "keep the patch bounded"}},
    ]
    return events


TIMELINE_B = [
    {"type": "SUPERVISOR_DISPATCH", "at": "2026-09-12T05:00:00+00:00",
     "MESSAGE_ID": 700601,
     "record": dispatch_record(IDENTITY_B1, exact=EXACT_DISPATCH_B1,
                               turn="turn-700601",
                               archived_at="2026-09-12T05:00:00+00:00")},
]


STUB_CONTROL = """\
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
mode = "ok"
mode_file = root / "stub_mode.txt"
if mode_file.exists():
    mode = mode_file.read_text(encoding="utf-8").strip()
history = root / "stub_history"


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


def load(name):
    return json.loads((history / name).read_text(encoding="utf-8"))


if mode == "sleep":
    import time
    time.sleep(30)
    sys.exit(0)
if mode == "garbage_ok":
    sys.stdout.write("definitely not json\\n")
    sys.exit(0)
if mode == "big_ok":
    sys.stdout.write("x" * (64 * 1024 + 32) + "\\n")
    sys.exit(0)

command_argv = [a for a in sys.argv[1:] if a != "--json"]
subcommand = None
message_id = None
skip = False
for index, item in enumerate(command_argv):
    if skip:
        skip = False
        continue
    if item in ("timeline", "tasks", "feedback", "interventions", "status"):
        subcommand = item
    if item == "--message-id":
        skip = True
        message_id = command_argv[index + 1]

if mode == "exit2_json":
    emit({"ok": False, "error": "boom: café", "error_type": "ControlError"})
    sys.exit(2)
if subcommand == "status":
    status_file = root / "stub_status.json"
    if status_file.exists():
        emit(json.loads(status_file.read_text(encoding="utf-8")))
    else:
        emit({"schema_version": 1, "PROJECT_ID": None})
    sys.exit(0)
if subcommand == "timeline":
    emit(load("timeline.json"))
    sys.exit(0)
if subcommand == "interventions":
    emit(load("interventions.json"))
    sys.exit(0)
if subcommand in ("tasks", "feedback"):
    item = history / f"{subcommand}-{message_id}.json"
    if not item.is_file():
        emit({"ok": False,
              "error": f"no record for MESSAGE_ID={message_id}",
              "error_type": "ControlError"})
        sys.exit(2)
    emit(json.loads(item.read_text(encoding="utf-8")))
    sys.exit(0)
emit({"ok": False, "error": "unknown subcommand", "error_type": "ControlError"})
sys.exit(2)
"""


def write_stub_runtime(runtime_root: Path) -> None:
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    (runtime_root / "scripts" / "supervisor_control.py").write_text(
        STUB_CONTROL, encoding="utf-8")


def write_history_files(runtime_root: Path, *, timeline, interventions,
                        tasks, feedback):
    directory = runtime_root / "stub_history"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "timeline.json").write_text(
        json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    (directory / "interventions.json").write_text(
        json.dumps(interventions, ensure_ascii=False), encoding="utf-8")
    for message_id, record in tasks.items():
        (directory / f"tasks-{message_id}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")
    for message_id, record in feedback.items():
        (directory / f"feedback-{message_id}.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")


class TimelineFixture:
    """One console server + two unmistakably different fixture Runtimes."""

    def __init__(self, base: Path):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.runtime_a = base / "runtime-a"
        self.runtime_b = base / "runtime-b"
        write_stub_runtime(self.runtime_a)
        write_stub_runtime(self.runtime_b)
        (self.runtime_a / "stub_status.json").write_text(
            json.dumps(dict(STATUS_OK, PROJECT_ID=MARKER_A),
                       ensure_ascii=False), encoding="utf-8")
        (self.runtime_b / "stub_status.json").write_text(
            json.dumps(dict(STATUS_OK, PROJECT_ID=MARKER_B),
                       ensure_ascii=False), encoding="utf-8")
        write_history_files(
            self.runtime_a,
            timeline=timeline_document_a(),
            interventions=[
                {"intervention_id": "H-1001", "mode": "STEER",
                 "target_message_id": 700501,
                 "submitted_at": "2026-09-12T01:05:00+00:00",
                 "status": "CONSUMED", "integrity": "OK",
                 "instruction_text": "keep the patch bounded"}],
            tasks={700501: dispatch_record(IDENTITY_A1, exact=EXACT_DISPATCH_A1,
                                           decision_sha=DECISION_A1_SHA),
                   700502: dispatch_record(
                       IDENTITY_A2, turn="turn-700502",
                       archived_at="2026-09-12T02:00:00+00:00",
                       integrity="UNAUTHORIZED",
                       error="authorization seal is missing"),
                   700503: dispatch_record(IDENTITY_A2, turn="turn-x",
                                           integrity="CORRUPT",
                                           error="metadata is corrupt")},
            feedback={700501: completion_record(IDENTITY_A1)},
        )
        write_history_files(
            self.runtime_b,
            timeline=TIMELINE_B,
            interventions=[],
            tasks={700601: dispatch_record(IDENTITY_B1, exact=EXACT_DISPATCH_B1,
                                           turn="turn-700601",
                                           archived_at="2026-09-12T05:00:00+00:00")},
            feedback={},
        )
        # Runtime A carries the valid Supervisor decision receipt for 700501.
        decisions = self.runtime_a / "control" / "supervisor_decisions"
        decisions.mkdir(parents=True, exist_ok=True)
        (decisions / "turn-700501.json").write_text(
            json.dumps(DECISION_RECEIPT_A1, ensure_ascii=False, indent=2),
            encoding="utf-8")
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_a,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=8.0)
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "/", body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            headers = {}
            payload = None
            if body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                parsed = None
            return response.status, parsed, raw
        finally:
            conn.close()

    def add_runtime(self, root: Path, label: str) -> dict:
        status, payload, _ = self.request(
            "POST", "/api/runtimes", body={"root": str(root), "label": label})
        if status != 201:
            raise AssertionError(f"fixture add failed: {status} {payload!r}")
        return payload["runtime"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TimelineEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = TimelineFixture(Path(self._tmp.name))
        self.runtime_a = self.fixture.add_runtime(
            self.fixture.runtime_a, "Runtime A — café")
        self.runtime_b = self.fixture.add_runtime(
            self.fixture.runtime_b, "Runtime B — 中文")

    def tearDown(self):
        self.fixture.close()
        self._tmp.cleanup()

    def timeline_of(self, runtime_id, query=""):
        return self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/timeline{query}")

    def test_timeline_document_is_versioned_and_bounded(self):
        status, payload, _ = self.timeline_of(self.runtime_a["id"])
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["runtime"]["id"], self.runtime_a["id"])
        timeline = payload["timeline"]
        self.assertEqual(timeline["schema_version"], 1)
        self.assertEqual(timeline["query"]["page"], 1)
        self.assertEqual(timeline["query"]["page_size"], 20)
        self.assertEqual(timeline["query"]["order"], "newest")
        self.assertEqual(timeline["totals"]["rounds"], 2)
        ids = [r["message_id"] for r in timeline["rounds"]]
        self.assertEqual(ids, [700502, 700501])

    def test_pagination_order_and_search_over_wire(self):
        runtime_id = self.runtime_a["id"]
        status, payload, _ = self.timeline_of(runtime_id, "?page_size=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["timeline"]["totals"]["pages"], 2)
        self.assertEqual([r["message_id"]
                          for r in payload["timeline"]["rounds"]], [700502])
        status, payload, _ = self.timeline_of(runtime_id, "?page_size=1&page=2")
        self.assertEqual([r["message_id"]
                          for r in payload["timeline"]["rounds"]], [700501])
        status, payload, _ = self.timeline_of(runtime_id, "?order=oldest")
        self.assertEqual([r["message_id"]
                          for r in payload["timeline"]["rounds"]],
                         [700501, 700502])
        status, payload, _ = self.timeline_of(runtime_id, "?q=700502")
        self.assertEqual([r["message_id"]
                          for r in payload["timeline"]["rounds"]], [700502])
        status, payload, _ = self.timeline_of(runtime_id, "?kind=intervention")
        self.assertEqual([r["message_id"]
                          for r in payload["timeline"]["rounds"]], [700501])

    def test_invalid_query_fails_closed_with_structured_error(self):
        runtime_id = self.runtime_a["id"]
        for query in ("?page=0", "?page_size=1000", "?order=sideways",
                      "?root=C:/elsewhere", "?page=1&page=2"):
            status, payload, _ = self.timeline_of(runtime_id, query)
            self.assertEqual(status, 400, query)
            self.assertEqual(payload["error"]["code"], "INVALID_QUERY_PARAM")

    def test_round_detail_composes_exact_dispatch_completion_and_decision(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_a['id']}/rounds/700501")
        self.assertEqual(status, 200)
        round_ = payload["round"]
        self.assertTrue(round_["found"])
        self.assertEqual(round_["identity"]["message_id"], 700501)
        self.assertEqual(round_["identity"]["task_id"], "TASK-A-α")
        # The exact archived dispatch is relayed byte-for-byte.
        self.assertEqual(round_["dispatch"]["exact_dispatch"],
                         EXACT_DISPATCH_A1)
        self.assertEqual(round_["dispatch"]["integrity"], "AUTHORIZED_VALID")
        self.assertTrue(round_["completion"]["available"])
        self.assertEqual(round_["completion"]["status"], "COMPLETION_SEALED")
        # The decision receipt verified against its recorded canonical hash.
        self.assertTrue(round_["decision"]["available"])
        self.assertTrue(round_["decision"]["verified"])
        self.assertEqual(round_["decision"]["decision_decision"],
                         "DISPATCH_EXECUTOR")
        # Artifact metadata comes from the authoritative receipt paths.
        self.assertTrue(round_["artifacts"]["available"])
        self.assertEqual(len(round_["artifacts"]["paths"]), 2)
        self.assertEqual(round_["interventions"][0]["intervention_id"],
                         "H-1001")

    def test_archived_dispatch_is_byte_exact_against_the_stub_archive(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_a['id']}/rounds/700501")
        exact = payload["round"]["dispatch"]["exact_dispatch"]
        source = EXACT_DISPATCH_A1
        self.assertEqual(exact, source)
        self.assertIn("<script>alert('hostile')</script>", exact)

    def test_unknown_round_fails_closed(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_a['id']}/rounds/799999")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "ROUND_NOT_FOUND")

    def test_round_route_rejects_non_numeric_and_oversized_ids(self):
        base = f"/api/runtimes/{self.runtime_a['id']}/rounds"
        for path in (f"{base}/abc", f"{base}/12345678901", f"{base}/-1"):
            status, payload, _ = self.fixture.request("GET", path)
            self.assertEqual(status, 404, path)

    def test_untrusted_dispatch_history_is_surfaced_not_hidden(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_a['id']}/rounds/700502")
        self.assertEqual(status, 200)
        round_ = payload["round"]
        self.assertEqual(round_["dispatch"]["integrity"], "UNAUTHORIZED")
        self.assertIsNone(round_["dispatch"]["exact_dispatch"])
        self.assertTrue(round_["honesty"]["notes"])

    def test_corrupt_dispatch_history_is_surfaced_not_hidden(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_a['id']}/rounds/700503")
        self.assertEqual(status, 200)
        round_ = payload["round"]
        self.assertTrue(round_["found"])
        self.assertEqual(round_["dispatch"]["integrity"], "CORRUPT")
        self.assertIsNone(round_["dispatch"]["exact_dispatch"])
        self.assertIn("metadata is corrupt",
                      round_["dispatch"]["error"] or "")

    def test_timeline_and_round_routes_are_get_only(self):
        base = f"/api/runtimes/{self.runtime_a['id']}"
        for path in (f"{base}/timeline", f"{base}/rounds/700501"):
            status, payload, _ = self.fixture.request("POST", path, body={})
            self.assertEqual(status, 405, path)
            self.assertEqual(payload["error"]["code"], "METHOD_NOT_ALLOWED")

    def set_mode(self, mode):
        mode_file = self.fixture.runtime_a / "stub_mode.txt"
        mode_file.write_text(mode, encoding="utf-8")
        self.addCleanup(lambda: mode_file.unlink(missing_ok=True))

    def test_malformed_control_plane_output_fails_closed(self):
        self.set_mode("garbage_ok")
        status, payload, _ = self.timeline_of(self.runtime_a["id"])
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"],
                         "CONTROL_PLANE_MALFORMED_OUTPUT")

    def test_control_plane_error_fails_closed(self):
        self.set_mode("exit2_json")
        status, payload, _ = self.timeline_of(self.runtime_a["id"])
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")

    def test_oversized_control_plane_output_is_refused(self):
        self.set_mode("big_ok")
        original = wcs.MAX_CONTROL_OUTPUT_BYTES
        wcs.MAX_CONTROL_OUTPUT_BYTES = 32 * 1024
        try:
            status, payload, _ = self.timeline_of(self.runtime_a["id"])
        finally:
            wcs.MAX_CONTROL_OUTPUT_BYTES = original
        self.assertEqual(status, 502)
        self.assertEqual(payload["error"]["code"],
                         "CONTROL_PLANE_OUTPUT_TOO_LARGE")

    def test_switching_runtimes_shows_isolated_histories(self):
        ids_a = self.timeline_of(self.runtime_a["id"])[1]["timeline"]["rounds"]
        ids_b = self.timeline_of(self.runtime_b["id"])[1]["timeline"]["rounds"]
        self.assertEqual([r["message_id"] for r in ids_a], [700502, 700501])
        self.assertEqual([r["message_id"] for r in ids_b], [700601])
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_b['id']}/rounds/700601")
        self.assertEqual(status, 200)
        self.assertEqual(payload["round"]["dispatch"]["exact_dispatch"],
                         EXACT_DISPATCH_B1)
        # Runtime B has no completion for its round: honest partial history.
        self.assertFalse(payload["round"]["completion"]["available"])
        # Round 700501 exists only in Runtime A.
        status, _, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_b['id']}/rounds/700501")
        self.assertEqual(status, 404)

    def test_unicode_and_hostile_strings_round_trip_as_utf8(self):
        status, payload, raw = self.timeline_of(self.runtime_a["id"])
        blob = raw.decode("utf-8")
        self.assertIn("TASK-A-α", blob)
        self.assertIn("TASK-A-β", blob)
        self.assertEqual(status, 200)
        # Hostile content inside the exact archived dispatch round-trips as
        # inert text through the round detail document; each Runtime carries
        # its own unmistakable Unicode strings.
        status, payload, raw = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_a['id']}/rounds/700501")
        blob = raw.decode("utf-8")
        self.assertIn("<script>alert('hostile')</script>", blob)
        self.assertIn("α", blob)
        status, payload, raw = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_b['id']}/rounds/700601")
        self.assertIn("中文", raw.decode("utf-8"))
        self.assertIn("✅", raw.decode("utf-8"))

    def test_unknown_removed_traversal_and_nearmiss_ids_fail_closed(self):
        real_id = self.runtime_a["id"]
        for bad in ("0" * 16, "z" * 16, real_id[:-1] + ("g" if real_id[-1]
                    != "g" else "h"), "../.." + "/" * 0, "", "x" * 17,
                    "%2e%2e%2f"):
            status, _, _ = self.timeline_of(bad)
            self.assertEqual(status, 404, repr(bad))
        status, _, _ = self.fixture.request(
            "GET", f"/api/runtimes/{real_id}../../timeline")
        self.assertEqual(status, 404)

    def test_query_parameters_cannot_rebind_the_runtime(self):
        # A path-shaped parameter is not a known timeline parameter and is
        # rejected outright instead of being tolerated or interpreted.
        status, payload, _ = self.timeline_of(
            self.runtime_a["id"],
            "?root=" + str(self.fixture.runtime_b).replace("\\", "%5C"))
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "INVALID_QUERY_PARAM")
        # Valid parameters only ever address the Registry-resolved root.
        status, payload, _ = self.timeline_of(self.runtime_a["id"], "?q=700502")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runtime"]["root"],
                         str(self.fixture.runtime_a))
        ids = [r["message_id"] for r in payload["timeline"]["rounds"]]
        self.assertEqual(ids, [700502])

    def test_p1_p2_p3_routes_remain_intact(self):
        runtime_id = self.runtime_a["id"]
        self.assertEqual(self.fixture.request("GET", "/api/health")[0], 200)
        self.assertEqual(self.fixture.request("GET", "/api/runtimes")[0], 200)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/cockpit")
        self.assertEqual(status, 200)
        self.assertTrue(payload["cockpit"]["interpretation"]["deterministic"])
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runtime"]["label"], "Runtime A — café")


class DecisionReceiptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = TimelineFixture(Path(self._tmp.name))
        self.runtime_a = self.fixture.add_runtime(
            self.fixture.runtime_a, "Runtime A")
        self.runtime_b = self.fixture.add_runtime(
            self.fixture.runtime_b, "Runtime B")

    def tearDown(self):
        self.fixture.close()
        self._tmp.cleanup()

    def round_of(self, runtime_id, mid):
        return self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/rounds/{mid}")

    def test_tampered_decision_receipt_is_not_trusted(self):
        path = (self.fixture.runtime_a / "control" / "supervisor_decisions"
                / "turn-700501.json")
        receipt = dict(DECISION_RECEIPT_A1)
        receipt["decision"] = {"decision": "DISPATCH_EXECUTOR",
                               "reason": "TAMPERED REASON"}
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        status, payload, _ = self.round_of(self.runtime_a["id"], 700501)
        self.assertEqual(status, 200)
        decision = payload["round"]["decision"]
        self.assertFalse(decision["available"])
        self.assertFalse(decision["verified"])
        self.assertTrue(decision["reason"])

    def test_missing_decision_receipt_is_honestly_unavailable(self):
        path = (self.fixture.runtime_a / "control" / "supervisor_decisions"
                / "turn-700501.json")
        path.unlink()
        status, payload, _ = self.round_of(self.runtime_a["id"], 700501)
        decision = payload["round"]["decision"]
        self.assertFalse(decision["available"])
        self.assertEqual(decision["reason"], "DECISION_RECEIPT_MISSING")

    def test_decision_receipt_with_wrong_candidate_binding_is_refused(self):
        path = (self.fixture.runtime_a / "control" / "supervisor_decisions"
                / "turn-700501.json")
        receipt = dict(DECISION_RECEIPT_A1)
        receipt["candidate"] = {**IDENTITY_A1, "dispatch_sha256": "e" * 64}
        path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        status, payload, _ = self.round_of(self.runtime_a["id"], 700501)
        decision = payload["round"]["decision"]
        self.assertFalse(decision["available"])

    def test_turn_id_with_path_characters_is_never_resolved(self):
        record = dispatch_record(IDENTITY_A1, turn="../escape")
        (self.fixture.runtime_b / "stub_history" / "tasks-700601.json").write_text(
            json.dumps(record, ensure_ascii=False), encoding="utf-8")
        status, payload, _ = self.round_of(self.runtime_b["id"], 700601)
        self.assertEqual(status, 200)
        decision = payload["round"]["decision"]
        self.assertFalse(decision["available"])
        self.assertEqual(decision["reason"], "SUPERVISOR_TURN_ID_INVALID")


if __name__ == "__main__":
    unittest.main()
