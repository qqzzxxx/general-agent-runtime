"""Artifact provenance / task-scoping end-to-end tests (offline, localhost).

Covers the artifact ↔ round task-scoping contract across the real HTTP
surface of the Web Console:

- an Artifact Center artifact locates back to its producing round
  (MESSAGE_ID 700100: provenance message/task/stage/commit identity);
- Task Detail shows only the artifacts of that MESSAGE (per-round scoping);
- DISPATCHED_NO_COMPLETION with Runtime publication records → the round is
  honestly reported as "published, not yet complete" (provisional);
- DISPATCHED_NO_COMPLETION without publications → "no artifacts published";
- a completion whose integrity failed never has its publications presented
  as final metadata.

The Console reads the Runtime's own pre-completion publication records
(`handoff/executor_publications/<commit_id>/`, the same records a verified
completion seals); Runtime lifecycle semantics are stubbed exactly like the
other console suites. A static section pins the frontend copy and markers.
No GUI, no external network.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_artifacts as artifacts
import web_console_server as wcs


def nonce_digest(nonce: str) -> str:
    """Pinned Runtime commit-id formula (executor_completion.nonce_digest)."""
    return hashlib.sha256(nonce.encode("utf-8")).hexdigest()[:24]


def commit_id_for(message_id: int, nonce: str) -> str:
    return f"completion-{message_id}-{nonce_digest(nonce)}"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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

NONCE = {700100: "nonce-700100" + "a" * 8, 700101: "nonce-700101" + "b" * 8,
         700102: "nonce-700102" + "c" * 8, 700103: "nonce-700103" + "d" * 8,
         700104: "nonce-700104" + "e" * 8}
TASK = {700100: "TASK-UIX", 700101: "TASK-DATA", 700102: "TASK-WIP",
        700103: "TASK-QUIET", 700104: "TASK-BROKEN"}
STAGE = {700100: "stage-ui", 700101: "stage-data", 700102: "stage-wip",
         700103: "stage-quiet", 700104: "stage-broken"}

FILE_700100 = b"# final report for round 700100\n"
FILE_700101 = b"# final report for round 700101\n"
FILE_700102 = b"work in progress evidence for 700102\n"


def identity_of(message_id: int) -> dict:
    return {"MESSAGE_ID": message_id, "TASK_ID": TASK[message_id],
            "STAGE_ID": STAGE[message_id], "ATTEMPT": 1,
            "NONCE": NONCE[message_id]}


def dispatch_record(message_id: int, *,
                    integrity="AUTHORIZED_VALID") -> dict:
    return {"schema_version": 1, "PROJECT_ID": "proj-x", **identity_of(message_id),
            "archived_at": "2026-09-16T01:00:00+00:00",
            "dispatch_sha256": "d" * 64,
            "archive_file": "handoff/supervisor_dispatch_archive/proj-x/"
                            f"dispatch-{message_id}.md",
            "originating_control_revision": 3,
            "supervisor_turn_id": f"turn-{message_id}",
            "decision_receipt_sha256": "a" * 64,
            "metadata_file": "meta.json", "authorization_file": "seal.json",
            "integrity": integrity, "trust_status": integrity}


def completion_record(message_id: int, path: str, digest: str, *,
                      integrity="OK") -> dict:
    return {"COMPLETION_PROTOCOL_VERSION": 1,
            "STATUS": "COMPLETION_SEALED", **identity_of(message_id),
            "PROJECT_ID": "proj-x",
            "COMMIT_ID": commit_id_for(message_id, NONCE[message_id]),
            "COMMITTED_AT": "2026-09-16T01:05:00+00:00",
            "CONSUMED_AT": None, "SEALED_AT": None,
            "CONSUMED_ARCHIVE": None,
            "ledger_file": f"handoff/completion_ledger/"
                           f"completion-{message_id}-"
                           f"{nonce_digest(NONCE[message_id])}.json",
            "integrity": integrity,
            "RECEIPT": {**identity_of(message_id), "PROJECT_ID": "proj-x",
                        "STATUS": "COMPLETE", "OUTCOME": "stage delivered"},
            "artifact_provenance": {
                "integrity": "OK" if integrity == "OK" else "UNAVAILABLE",
                "source": "sealed_manifest" if integrity == "OK" else None,
                "publications": [{**identity_of(message_id),
                                  "PROJECT_ID": "proj-x", "path": path,
                                  "sha256": digest,
                                  "PUBLISHED_AT":
                                      "2026-09-16T01:05:00+00:00"}]},
            }


def publication_record(message_id: int, path: str, digest: str) -> dict:
    """The Runtime fence's per-artifact record (executor_fence.publish)."""
    return {**identity_of(message_id), "PROJECT_ID": "proj-x",
            "path": path, "sha256": digest,
            "PUBLISHED_AT": "2026-09-16T01:02:00+00:00"}


class RoundArtifactsFixture:
    """One console server + one fixture Runtime with five distinct rounds."""

    def __init__(self, base: Path):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True,
                                                  exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.runtime = base / "runtime"
        self._build_runtime(self.runtime)
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime,
            console_root=self.console_root, data_dir=base / "console-data",
            control_timeout=8.0)
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    def _build_runtime(self, root: Path) -> None:
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "supervisor_control.py").write_text(
            STUB_CONTROL, encoding="utf-8")
        (root / "stub_status.json").write_text(
            json.dumps(STATUS_OK), encoding="utf-8")
        project = root / "projects" / "proj-x"
        for name in ("workspace", "evidence", "reports"):
            (project / name).mkdir(parents=True, exist_ok=True)
        # Round 700100: completed; its report is bound to MESSAGE 700100.
        (project / "reports" / "final-700100.md").write_bytes(FILE_700100)
        # Round 700101: completed with a different artifact, same project.
        (project / "reports" / "final-700101.md").write_bytes(FILE_700101)
        # Round 700102: dispatched, not completed; the fence already
        # published one artifact (canonical file + Runtime record).
        (project / "evidence" / "wip-700102.txt").write_bytes(FILE_700102)
        tasks = {}
        feedback = {}
        for message_id in NONCE:
            tasks[message_id] = dispatch_record(message_id)
        feedback[700100] = completion_record(
            700100, "reports/final-700100.md", sha256_bytes(FILE_700100))
        feedback[700101] = completion_record(
            700101, "reports/final-700101.md", sha256_bytes(FILE_700101))
        # 700104: a completion whose ledger hashes no longer verify. Its
        # claimed publication must never resurface as bound metadata.
        feedback[700104] = completion_record(
            700104, "reports/final-700104.md", "e" * 64,
            integrity="HASH_MISMATCH")
        history = root / "stub_history"
        history.mkdir(parents=True, exist_ok=True)
        (history / "feedback_all.json").write_text(
            json.dumps([entry for entry in feedback.values()],
                       ensure_ascii=False), encoding="utf-8")
        for message_id, record in tasks.items():
            (history / f"tasks-{message_id}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8")
        for message_id, record in feedback.items():
            (history / f"feedback-{message_id}.json").write_text(
                json.dumps(record, ensure_ascii=False), encoding="utf-8")
        # Runtime publication records for 700102 only (pre-completion).
        record = publication_record(700102, "evidence/wip-700102.txt",
                                    sha256_bytes(FILE_700102))
        digest = hashlib.sha256(
            record["path"].encode("utf-8")).hexdigest()
        directory = (root / "handoff" / "executor_publications"
                     / commit_id_for(700102, NONCE[700102]))
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{digest}.json").write_text(
            json.dumps(record), encoding="utf-8")

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "/"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request(method, path)
            response = conn.getresponse()
            raw = response.read()
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                parsed = None
            return response.status, parsed
        finally:
            conn.close()

    def add_runtime(self) -> dict:
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            body = json.dumps({"root": str(self.runtime),
                               "label": "Round artifacts fixture"}).encode()
            conn.request("POST", "/api/runtimes", body=body,
                         headers={"Content-Type":
                                  "application/json; charset=utf-8"})
            response = conn.getresponse()
            payload = json.loads(response.read().decode("utf-8"))
            status = response.status
        finally:
            conn.close()
        if status != 201:
            raise AssertionError(f"fixture add failed: {status} {payload!r}")
        return payload["runtime"]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


STUB_CONTROL = """\
import json, sys
from pathlib import Path
root = Path(__file__).resolve().parents[1]
history = root / "stub_history"


def emit(value):
    print(json.dumps(value, ensure_ascii=True, indent=2))


argv = [a for a in sys.argv[1:] if a != "--json"]
subcommand = None
message_id = None
skip = False
for index, item in enumerate(argv):
    if skip:
        skip = False
        continue
    if item in ("timeline", "tasks", "feedback", "interventions", "status"):
        subcommand = item
    if item == "--message-id":
        skip = True
        message_id = argv[index + 1]

if subcommand == "status":
    emit(json.loads((root / "stub_status.json").read_text(encoding="utf-8")))
    sys.exit(0)
if subcommand == "feedback":
    if message_id is None:
        emit(json.loads((history / "feedback_all.json")
                        .read_text(encoding="utf-8")))
        sys.exit(0)
    item = history / f"feedback-{message_id}.json"
    if not item.is_file():
        emit({"ok": False,
              "error": f"no authoritative Executor completion for "
                       f"MESSAGE_ID={message_id}",
              "error_type": "ControlError"})
        sys.exit(2)
    emit(json.loads(item.read_text(encoding="utf-8")))
    sys.exit(0)
if subcommand == "tasks":
    item = history / f"tasks-{message_id}.json"
    if not item.is_file():
        emit({"ok": False,
              "error": f"no archived Supervisor dispatch for "
                       f"MESSAGE_ID={message_id}",
              "error_type": "ControlError"})
        sys.exit(2)
    emit(json.loads(item.read_text(encoding="utf-8")))
    sys.exit(0)
if subcommand in ("timeline", "interventions"):
    emit([])
    sys.exit(0)
emit({"ok": False, "error": "unknown subcommand", "error_type": "ControlError"})
sys.exit(2)
"""


class RoundArtifactScopingTests(unittest.TestCase):
    """End-to-end over HTTP: provenance identity and per-MESSAGE scoping."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fixture = RoundArtifactsFixture(Path(cls._tmp.name))
        cls.runtime_id = cls.fixture.add_runtime()["id"]

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()
        cls._tmp.cleanup()

    def round_of(self, message_id: int):
        status, payload = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/rounds/{message_id}")
        self.assertEqual(status, 200, payload)
        return payload["round"]

    def catalog_of(self, query: str = ""):
        status, payload = self.fixture.request(
            "GET", f"/api/runtimes/{self.runtime_id}/artifacts{query}")
        self.assertEqual(status, 200, payload)
        return payload["artifacts"]

    def test_artifact_locates_back_to_700100(self):
        catalog = self.catalog_of("?message_id=700100")
        self.assertEqual(catalog["totals"]["artifacts"], 1)
        artifact = catalog["artifacts"][0]
        provenance = artifact["provenance"]
        self.assertEqual(provenance["class"], "ledger")
        self.assertEqual(provenance["message_id"], 700100)
        self.assertEqual(provenance["task_id"], TASK[700100])
        self.assertEqual(provenance["stage_id"], STAGE[700100])
        self.assertEqual(provenance["attempt"], 1)
        self.assertEqual(provenance["commit_id"],
                         commit_id_for(700100, NONCE[700100]))
        self.assertEqual(provenance["completion_integrity"], "OK")
        self.assertEqual(artifact["path"], "reports/final-700100.md")

    def test_catalog_message_filter_excludes_other_rounds(self):
        catalog = self.catalog_of("?message_id=700100")
        paths = [item["path"] for item in catalog["artifacts"]]
        self.assertNotIn("reports/final-700101.md", paths)
        catalog = self.catalog_of("?message_id=700101")
        paths = [item["path"] for item in catalog["artifacts"]]
        self.assertEqual(paths, ["reports/final-700101.md"])
        self.assertEqual(catalog["artifacts"][0]["provenance"]["message_id"],
                         700101)

    def test_task_detail_shows_only_this_messages_artifacts(self):
        round_ = self.round_of(700100)
        block = round_["artifacts"]
        self.assertTrue(block["available"])
        self.assertEqual(block["state"], "final")
        self.assertEqual([item["message_id"]
                          for item in block["paths"]], [700100])
        self.assertEqual(block["paths"][0]["path"], "reports/final-700100.md")
        self.assertEqual(block["paths"][0]["commit_id"],
                         commit_id_for(700100, NONCE[700100]))
        other = self.round_of(700101)
        self.assertEqual([item["path"] for item in other["artifacts"]["paths"]],
                         ["reports/final-700101.md"])

    def test_dispatched_no_completion_with_publication_is_provisional(self):
        round_ = self.round_of(700102)
        self.assertTrue(round_["found"])
        self.assertFalse(round_["completion"]["available"])
        block = round_["artifacts"]
        self.assertFalse(block["available"])
        self.assertFalse(block["complete"])
        self.assertEqual(block["state"], "provisional")
        self.assertEqual([item["path"] for item in block["paths"]],
                         ["evidence/wip-700102.txt"])
        record = block["paths"][0]
        self.assertEqual(record["status"], "provisional")
        self.assertEqual(record["message_id"], 700102)
        self.assertEqual(record["commit_id"],
                         commit_id_for(700102, NONCE[700102]))
        self.assertEqual(record["sha256"], sha256_bytes(FILE_700102))
        self.assertIn("provisional", block["note"].lower())

    def test_dispatched_no_completion_without_publication_says_none(self):
        round_ = self.round_of(700103)
        self.assertTrue(round_["found"])
        block = round_["artifacts"]
        self.assertFalse(block["available"])
        self.assertEqual(block["state"], "none")
        self.assertEqual(block["paths"], [])
        self.assertIn("has not published", block["note"])

    def test_completion_integrity_failure_never_fakes_final_metadata(self):
        round_ = self.round_of(700104)
        block = round_["artifacts"]
        self.assertFalse(block["available"])
        self.assertNotEqual(block["state"], "final")
        self.assertNotEqual(block["state"], "provisional")
        self.assertEqual(block["paths"], [])
        self.assertIn("HASH_MISMATCH", block["reason"])
        self.assertIn("not presented as final", block["reason"])
        # The corrupted entry's claimed publication is not surfaced anywhere
        # in the block as if it were bound.
        self.assertNotIn("reports/final-700104.md",
                         json.dumps(block))

    def test_untrusted_completion_status_is_reflected_in_the_round(self):
        round_ = self.round_of(700104)
        self.assertTrue(round_["completion"]["available"])
        self.assertEqual(round_["completion"]["integrity"], "HASH_MISMATCH")

    def test_round_700102_is_dispatched_without_completion(self):
        round_ = self.round_of(700102)
        self.assertTrue(round_["dispatch"]["available"])
        self.assertFalse(round_["completion"]["available"])


class PublicationRecordValidatorTests(unittest.TestCase):
    """Pure fail-closed validation of raw Runtime publication records."""

    def valid_entry(self, path="evidence/a.txt", name=None, **overrides):
        record = {"MESSAGE_ID": 700102, "TASK_ID": "TASK-WIP",
                  "STAGE_ID": "stage-wip", "ATTEMPT": 1,
                  "NONCE": "n" * 24, "PROJECT_ID": "proj-x",
                  "path": path, "sha256": "a" * 64,
                  "PUBLISHED_AT": "2026-09-16T01:02:00+00:00"}
        record.update(overrides)
        filename = hashlib.sha256(path.encode()).hexdigest() + ".json"
        return {"name": name if name is not None else filename,
                "record": record}

    def identity(self, **overrides):
        identity = {"MESSAGE_ID": 700102, "TASK_ID": "TASK-WIP",
                    "STAGE_ID": "stage-wip", "ATTEMPT": 1, "NONCE": "n" * 24,
                    "PROJECT_ID": "proj-x"}
        identity.update(overrides)
        return identity

    def test_valid_records_project_with_identity(self):
        publications, reason = \
            artifacts.verified_runtime_publication_records(
                [self.valid_entry()], self.identity())
        self.assertIsNone(reason)
        self.assertEqual(publications, [{
            "path": "evidence/a.txt", "sha256": "a" * 64,
            "published_at": "2026-09-16T01:02:00+00:00",
            "task_id": "TASK-WIP", "stage_id": "stage-wip", "attempt": 1}])

    def test_any_tampering_refuses_the_whole_set(self):
        stripped = self.valid_entry()
        stripped["record"] = {key: value for key, value
                              in stripped["record"].items()
                              if key != "PUBLISHED_AT"}
        cases = {
            "filename_binding": [self.valid_entry(name="0" * 64 + ".json")],
            "schema_extra_key": [self.valid_entry(extra="x")],
            "schema_missing_key": [stripped],
            "identity_message": [self.valid_entry(MESSAGE_ID=999999)],
            "identity_task": [self.valid_entry(TASK_ID="OTHER")],
            "project": [self.valid_entry(PROJECT_ID="proj-y")],
            "path_escape": [self.valid_entry(path="../evidence/a.txt")],
            "unauthorized_root": [self.valid_entry(path="secrets/a.txt")],
            "hash_shape": [self.valid_entry(sha256="A" * 64)],
            "timestamp_naive": [self.valid_entry(
                PUBLISHED_AT="2026-09-16T01:02:00")],
            "duplicate_casefold": [
                self.valid_entry(path="evidence/a.txt"),
                self.valid_entry(path="evidence/A.txt",
                                 sha256="b" * 64)],
        }
        for label, entries in cases.items():
            with self.subTest(case=label):
                publications, reason = \
                    artifacts.verified_runtime_publication_records(
                        entries, self.identity())
                self.assertIsNotNone(reason, label)
                self.assertEqual(publications, [])

    def test_incomplete_dispatch_identity_refuses(self):
        publications, reason = \
            artifacts.verified_runtime_publication_records(
                [self.valid_entry()], self.identity(TASK_ID=None))
        self.assertEqual(reason, "incomplete dispatch identity")
        self.assertEqual(publications, [])

    def test_record_count_is_bounded(self):
        entries = [self.valid_entry(path=f"evidence/f{index}.txt")
                   for index in range(129)]
        publications, reason = \
            artifacts.verified_runtime_publication_records(
                entries, self.identity())
        self.assertIsNotNone(reason)
        self.assertEqual(publications, [])


class RoundArtifactsFrontendPinsTests(unittest.TestCase):
    """Static pins for the task-scoped artifact presentation."""

    @classmethod
    def setUpClass(cls):
        cls.text = (REPO / "web_console" / "index.html").read_text(
            encoding="utf-8")

    def test_published_but_incomplete_copy_is_pinned(self):
        self.assertIn("已发布，任务尚未完成。", self.text)
        self.assertIn("Published — the task has not completed yet.", self.text)

    def test_no_artifacts_copy_is_pinned(self):
        self.assertIn("当前任务尚未发布产物。", self.text)
        self.assertIn("The current task has not published any artifacts.",
                      self.text)

    def test_provisional_metadata_is_labeled_not_final(self):
        self.assertIn("provisional (not sealed as final metadata)", self.text)
        self.assertIn("final publication metadata", self.text)

    def test_artifact_center_cards_carry_provenance_identity(self):
        self.assertIn("ac-prov-line", self.text)
        self.assertIn("ac-prov-commit", self.text)
        self.assertIn('"ac-prov-open round-detail-open"', self.text)
        self.assertIn("Publication/completion id: ", self.text)

    def test_artifact_center_cards_link_to_task_detail(self):
        self.assertIn("openTaskDetail(prov.message_id)", self.text)


if __name__ == "__main__":
    unittest.main()
