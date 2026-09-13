"""P6 Artifact Center HTTP endpoint tests (offline, localhost).

Covers the new read-only Artifact Center routes and the single mutation on
top of the accepted P2 opaque-ID root binding:

- `GET  /api/runtimes/<id>/artifacts`              catalog (filters, pagination)
- `GET  /api/runtimes/<id>/artifacts/<aid>`        detail + provenance
- `GET  /api/runtimes/<id>/artifacts/<aid>/preview` bounded read-only preview
- `GET  /api/runtimes/<id>/artifacts/<aid>/raw`    verified image/PDF bytes
- `POST /api/runtimes/<id>/artifacts/feedback`     artifact-bound STEER/AUDIT

Fail-closed behavior is pinned over the wire: unknown/missing/hashed-wrong
artifacts, malformed and rebinding queries, oversized and polyglot files,
non-UTF-8 content, unsupported formats, unknown MESSAGE_ID feedback targets,
unbound/cross-message artifact paths, adversarial Runtime IDs, and method
mismatches. Two unmistakably different fixture Runtimes prove isolation; the
P1–P5 routes remain intact. No GUI, no external network; the intervention is
delegated to a recording stub of the real v1.2 control-plane argv contract.
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

import web_console_server as wcs


def artifact_id(path: str) -> str:
    import web_console_artifacts as artifacts
    if path == "workspace/unbound.txt":
        return artifacts.artifact_id_for_path(path)
    mid = 800801 if path in ("reports/beta.md", "evidence/only-b.txt") else 700107
    return artifacts.artifact_id_for_publication(path, f"completion-{mid}-abc")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


MARKER_A = "ARTIFACT-ROOT-A-café"
MARKER_B = "ARTIFACT-ROOT-B-中文"

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

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PDF_BYTES = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n" + b"1 0 obj\n" + b"\x00" * 32
POLYGLOT_BYTES = b"<html><script>alert('polyglot')</script></html>"
NON_UTF8_BYTES = b"\xff\xfe\x00binary"


class ArtifactsFixture:
    """One console server + two fixture Runtimes with real artifact files."""

    def __init__(self, base: Path):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = base / "console-data"
        self.runtime_a = base / "runtime-a"
        self.runtime_b = base / "runtime-b"
        self._build_runtime(self.runtime_a, "proj-x", 700107, MARKER_A,
                            full=True)
        self._build_runtime(self.runtime_b, "proj-y", 800801, MARKER_B,
                            full=False)
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_a,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=8.0)
        import threading
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    # -- fixture construction ------------------------------------------------

    def _build_runtime(self, root: Path, project_id: str, message_id: int,
                       status_marker: str, *, full: bool) -> dict:
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        (root / "scripts" / "supervisor_control.py").write_text(
            STUB_CONTROL, encoding="utf-8")
        (root / "stub_status.json").write_text(
            json.dumps(dict(STATUS_OK, PROJECT_ID=project_id)),
            encoding="utf-8")
        project = root / "projects" / project_id
        for name in ("workspace", "evidence", "reports"):
            (project / name).mkdir(parents=True, exist_ok=True)
        if full:
            files = {
                "reports/r.md": "# Report café\n<body onload=alert(1)>\n",
                "evidence/t.txt": "plain evidence line 1\nline 2\n",
                "evidence/run.log": "2026-09-12 INFO started\n"
                                    "2026-09-12 INFO ok\n",
                "workspace/w.json": '{"alpha": 1, "beta": [2, 3]}',
                "workspace/data.csv": "a,b\n1,2\n3,4\n",
                "workspace/img.png": PNG_BYTES,
                "workspace/img.jpg": JPEG_BYTES,
                "workspace/doc.pdf": PDF_BYTES,
                "workspace/poly.png": POLYGLOT_BYTES,
                "workspace/bad.bin": b"MZ\x90\x00" + b"\x11" * 16,
                "workspace/nonutf8.txt": NON_UTF8_BYTES,
                "reports/中文-café.md": "# 中文报告 café ✅\n",
            }
            huge = ("line " + "x" * 60 + "\n") * 6000
            files["workspace/huge.txt"] = huge
        else:
            files = {
                "reports/beta.md": "# Beta runtime report 中文\n",
                "evidence/only-b.txt": "runtime B evidence\n",
            }
        written = {}
        for relative, content in files.items():
            payload = content if isinstance(content, bytes) \
                else content.encode("utf-8")
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            written[relative] = payload
        if full:
            # A receipt-bound file whose on-disk bytes no longer match the
            # authoritative hash: the preview must refuse, not guess.
            (project / "reports" / "mismatch.md").write_bytes(
                b"# original receipt text\n")
            # A receipt-bound image whose bytes no longer match: the raw
            # route must refuse even though the magic would re-verify.
            (project / "workspace" / "badhash.png").write_bytes(PNG_BYTES)
            # A file present under an authorized root with no receipt.
            (project / "workspace" / "unbound.txt").write_bytes(
                b"never published through the ledger\n")
            # A bound path that does not exist on disk at all.
            published = [
                {"path": path, "sha256": sha256_bytes(payload)}
                for path, payload in written.items()
            ]
            published.append({"path": "reports/mismatch.md",
                              "sha256": sha256_bytes(b"# different bytes\n")})
            published.append({"path": "workspace/badhash.png",
                              "sha256": sha256_bytes(PNG_BYTES[::-1])})
            published.append({"path": "reports/ghost.md",
                              "sha256": "e" * 64})
        else:
            published = [
                {"path": path, "sha256": sha256_bytes(payload)}
                for path, payload in written.items()
            ]
        feedback = [{
            "COMPLETION_PROTOCOL_VERSION": 1,
            "STATUS": "COMPLETION_SEALED",
            "MESSAGE_ID": message_id, "TASK_ID": "TASK-ART",
            "STAGE_ID": "stage-art", "ATTEMPT": 1, "NONCE": "n" * 24,
            "PROJECT_ID": project_id,
            "COMMIT_ID": f"completion-{message_id}-abc",
            "COMMITTED_AT": "2026-09-12T01:00:00+00:00",
            "CONSUMED_AT": None, "SEALED_AT": None,
            "CONSUMED_ARCHIVE": None,
            "ledger_file": f"handoff/completion_ledger/"
                           f"completion-{message_id}-abc.json",
            "integrity": "OK",
            "RECEIPT": {"schema_version": 1, "MESSAGE_ID": message_id,
                        "TASK_ID": "TASK-ART", "STAGE_ID": "stage-art",
                        "ATTEMPT": 1, "NONCE": "n" * 24,
                        "PROJECT_ID": project_id, "STATUS": "COMPLETE",
                        "OUTCOME": "stage delivered",
                        "CREATED_AT": "2026-09-12T00:59:00+00:00",
                        "EXECUTOR_MODEL_FAMILY": "GLM-5.3",
                        },
            "artifact_provenance": {"integrity": "OK", "source": "sealed_manifest",
                "publications": [{"MESSAGE_ID": message_id, "TASK_ID": "TASK-ART",
                    "STAGE_ID": "stage-art", "ATTEMPT": 1, "NONCE": "n" * 24,
                    "PROJECT_ID": project_id, "PUBLISHED_AT": "2026-09-12T01:00:00+00:00", **item}
                    for item in published]},
        }]
        history = root / "stub_history"
        history.mkdir(parents=True, exist_ok=True)
        (history / "feedback_all.json").write_text(
            json.dumps(feedback, ensure_ascii=False), encoding="utf-8")
        return {"message_id": message_id, "project_id": project_id,
                "files": written}

    # -- transport -----------------------------------------------------------

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


argv = [a for a in sys.argv[1:] if a != "--json"]
subcommand = None
for item in ("timeline", "tasks", "feedback", "interventions", "status",
             "intervene", "pause", "resume"):
    if item in argv:
        subcommand = item

if mode == "exit2_json":
    emit({"ok": False, "error": "boom: café", "error_type": "ControlError"})
    sys.exit(2)

if subcommand == "status":
    emit(json.loads((root / "stub_status.json").read_text(encoding="utf-8")))
    sys.exit(0)
if subcommand == "feedback":
    emit(json.loads((history / "feedback_all.json")
                    .read_text(encoding="utf-8")))
    sys.exit(0)
if subcommand == "intervene":
    (root / "stub_interventions.log").open("a", encoding="utf-8") \\
        .write(json.dumps(argv, ensure_ascii=False) + "\\n")
    emit({"schema_version": 1, "status": "PENDING",
          "intervention_id": "intervention-abc123",
          "mode": next(a for a in argv if a.startswith("--mode="))
          .split("=", 1)[1],
          "target_message_id": int(next(
              (a for a in argv if a.startswith("--target-message-id=")),
              "--target-message-id=0").split("=", 1)[1])})
    sys.exit(0)
if subcommand == "timeline":
    emit([])
    sys.exit(0)
if subcommand == "interventions":
    emit([])
    sys.exit(0)
emit({"ok": False, "error": "unknown subcommand", "error_type": "ControlError"})
sys.exit(2)
"""


class ArtifactsEndpointTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fixture = ArtifactsFixture(Path(self._tmp.name))
        self.runtime_a = self.fixture.add_runtime(
            self.fixture.runtime_a, "Runtime A — café")
        self.runtime_b = self.fixture.add_runtime(
            self.fixture.runtime_b, "Runtime B — 中文")
        self.id_a = self.runtime_a["id"]
        self.id_b = self.runtime_b["id"]

    def tearDown(self):
        self.fixture.close()
        self._tmp.cleanup()

    def artifacts_of(self, runtime_id, query=""):
        return self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/artifacts{query}")

    # -- catalog -------------------------------------------------------------

    def test_catalog_is_versioned_and_lists_fixture_artifacts(self):
        status, payload, _ = self.artifacts_of(self.id_a)
        self.assertEqual(status, 200)
        self.assertEqual(payload["schema_version"], 1)
        self.assertTrue(payload["ok"])
        catalog = payload["artifacts"]
        self.assertEqual(catalog["schema_version"], 1)
        paths = [item["path"] for item in catalog["artifacts"]]
        self.assertIn("reports/r.md", paths)
        self.assertIn("workspace/img.png", paths)
        self.assertIn("reports/中文-café.md", paths)
        self.assertIn("workspace/unbound.txt", paths)
        # A ledger-bound path whose file is absent is still cataloged —
        # with the honest "missing" availability, never hidden.
        ghost = next(item for item in catalog["artifacts"]
                     if item["path"] == "reports/ghost.md")
        self.assertEqual(ghost["availability"], "missing")
        r = next(item for item in catalog["artifacts"]
                 if item["path"] == "reports/r.md")
        self.assertEqual(r["format"], "markdown")
        self.assertEqual(r["provenance"]["class"], "ledger")
        self.assertEqual(r["provenance"]["message_id"], 700107)
        self.assertEqual(r["provenance"]["producer"], "GLM-5.3")
        self.assertEqual(r["artifact_id"], artifact_id("reports/r.md"))
        ghost = next(item for item in catalog["artifacts"]
                     if item["path"] == "reports/mismatch.md")
        self.assertEqual(ghost["availability"], "present")

    def test_catalog_filters_and_pagination(self):
        status, payload, _ = self.artifacts_of(self.id_a, "?type=image-root")
        self.assertEqual(status, 400)
        status, payload, _ = self.artifacts_of(self.id_a, "?root=reports")
        paths = [item["path"] for item in payload["artifacts"]["artifacts"]]
        self.assertTrue(all(path.startswith("reports/") for path in paths))
        status, payload, _ = self.artifacts_of(self.id_a, "?page_size=3")
        self.assertEqual(len(payload["artifacts"]["artifacts"]), 3)
        self.assertEqual(payload["artifacts"]["totals"]["pages"],
                         (payload["artifacts"]["totals"]["artifacts"] + 2) // 3)

    def test_catalog_refuses_rebinding_and_unknown_parameters(self):
        for query in ("?root=" + str(self.fixture.runtime_b).replace("\\", "%5C"),
                      "?runtime_root=/etc", "?path=reports/r.md",
                      "?page=abc"):
            status, payload, _ = self.artifacts_of(self.id_a, query)
            self.assertEqual(status, 400, query)
            self.assertEqual(payload["error"]["code"], "INVALID_QUERY_PARAM")

    # -- detail --------------------------------------------------------------

    def test_detail_verifies_bound_artifact_hash(self):
        aid = artifact_id("reports/r.md")
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/{aid}")
        self.assertEqual(status, 200)
        detail = payload["artifact"]
        self.assertEqual(detail["path"], "reports/r.md")
        self.assertEqual(detail["verification"], "verified")
        self.assertEqual(detail["provenance"]["message_id"], 700107)

    def test_detail_refuses_unknown_and_adversarial_ids(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/"
                   + "0" * 64)
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"]["code"], "ARTIFACT_UNKNOWN")
        for bad in ("..", "%2e%2e", "z" * 64, "short", ""):
            escaped = bad.replace("/", "%2F").replace("\\", "%5C")
            status, _, _ = self.fixture.request(
                "GET", f"/api/runtimes/{self.id_a}/artifacts/{escaped}")
            self.assertEqual(status, 404, bad)

    def test_missing_bound_artifact_reports_missing(self):
        aid = artifact_id("reports/ghost.md")
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/{aid}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["artifact"]["availability"], "missing")

    # -- previews ------------------------------------------------------------

    def preview_of(self, runtime_id, path):
        return self.fixture.request(
            "GET", f"/api/runtimes/{runtime_id}/artifacts/"
                   f"{artifact_id(path)}/preview")

    def test_text_and_markdown_previews(self):
        status, payload, _ = self.preview_of(self.id_a, "evidence/t.txt")
        self.assertEqual(status, 200)
        preview = payload["preview"]
        self.assertEqual(preview["kind"], "text")
        self.assertEqual(preview["state"], "OK")
        self.assertIn("line 2", preview["content"])
        status, payload, _ = self.preview_of(self.id_a, "reports/r.md")
        preview = payload["preview"]
        self.assertEqual(preview["kind"], "text")
        self.assertIn("<body onload=alert(1)>", preview["content"])

    def test_json_and_csv_previews(self):
        status, payload, _ = self.preview_of(self.id_a, "workspace/w.json")
        self.assertEqual(payload["preview"]["state"], "OK")
        status, payload, _ = self.preview_of(self.id_a, "workspace/data.csv")
        self.assertEqual(payload["preview"]["kind"], "table")
        self.assertEqual(payload["preview"]["rows"][0], ["a", "b"])

    def test_non_utf8_preview_is_honest(self):
        status, payload, _ = self.preview_of(self.id_a, "workspace/nonutf8.txt")
        self.assertEqual(status, 200)
        self.assertEqual(payload["preview"]["state"], "NOT_UTF8")
        self.assertIsNone(payload["preview"]["content"])

    def test_oversized_text_preview_truncates(self):
        status, payload, _ = self.preview_of(self.id_a, "workspace/huge.txt")
        self.assertEqual(status, 200)
        preview = payload["preview"]
        self.assertEqual(preview["state"], "TRUNCATED")
        self.assertTrue(preview["truncated"])

    def test_hash_mismatch_refuses_preview(self):
        status, payload, _ = self.preview_of(self.id_a, "reports/mismatch.md")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "ARTIFACT_HASH_MISMATCH")

    def test_unsupported_format_preview_is_honest(self):
        status, payload, _ = self.preview_of(self.id_a, "workspace/bad.bin")
        self.assertEqual(status, 200)
        self.assertEqual(payload["preview"]["kind"], "unavailable")
        self.assertEqual(payload["preview"]["state"], "UNSUPPORTED")

    def test_image_raw_serves_verified_bytes_with_hardened_headers(self):
        status, payload, raw = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/"
                   f"{artifact_id('workspace/img.png')}/raw")
        self.assertEqual(status, 200)
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.fixture.port,
                                          timeout=30)
        conn.request("GET", f"/api/runtimes/{self.id_a}/artifacts/"
                            f"{artifact_id('workspace/img.png')}/raw",
                     headers={"Host": f"127.0.0.1:{self.fixture.port}"})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        self.assertEqual(response.getheader("Content-Type"), "image/png")
        self.assertEqual(response.getheader("X-Content-Type-Options"),
                         "nosniff")
        self.assertEqual(body, PNG_BYTES)
        self.assertEqual(payload, None)  # body is bytes, not JSON

    def test_polyglot_image_refuses_raw_and_flags_preview(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/"
                   f"{artifact_id('workspace/poly.png')}/raw")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "ARTIFACT_FORMAT_MISMATCH")
        status, payload, _ = self.preview_of(self.id_a, "workspace/poly.png")
        self.assertEqual(payload["preview"]["state"], "FORMAT_MISMATCH")

    def test_pdf_raw_is_attachment_not_inline(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.fixture.port,
                                          timeout=30)
        conn.request("GET", f"/api/runtimes/{self.id_a}/artifacts/"
                            f"{artifact_id('workspace/doc.pdf')}/raw")
        response = conn.getresponse()
        body = response.read()
        conn.close()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"),
                         "application/pdf")
        disposition = response.getheader("Content-Disposition") or ""
        self.assertIn("attachment", disposition)

    def test_raw_refuses_text_formats(self):
        status, _, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/"
                   f"{artifact_id('evidence/t.txt')}/raw")
        self.assertEqual(status, 404)

    def test_raw_refuses_hash_mismatch(self):
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/"
                   f"{artifact_id('workspace/badhash.png')}/raw")
        self.assertEqual(status, 409)
        self.assertEqual(payload["error"]["code"], "ARTIFACT_HASH_MISMATCH")

    def test_unicode_artifact_round_trips(self):
        aid = artifact_id("reports/中文-café.md")
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/{aid}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["artifact"]["path"], "reports/中文-café.md")
        status, payload, _ = self.preview_of(self.id_a, "reports/中文-café.md")
        self.assertIn("中文报告", payload["preview"]["content"])

    # -- isolation -----------------------------------------------------------

    def test_runtimes_do_not_see_each_others_artifacts(self):
        status, payload_a, _ = self.artifacts_of(self.id_a)
        status, payload_b, _ = self.artifacts_of(self.id_b)
        paths_a = {item["path"]
                   for item in payload_a["artifacts"]["artifacts"]}
        paths_b = {item["path"]
                   for item in payload_b["artifacts"]["artifacts"]}
        self.assertIn("reports/r.md", paths_a)
        self.assertNotIn("reports/r.md", paths_b)
        self.assertEqual(payload_b["artifacts"]["artifacts"][0]
                         ["provenance"]["message_id"], 800801)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_b}/artifacts/"
                   f"{artifact_id('reports/r.md')}")
        self.assertEqual(status, 404)
        status, payload, _ = self.preview_of(self.id_b, "reports/r.md")
        self.assertEqual(status, 404)

    def test_adversarial_runtime_ids_fail_closed(self):
        for bad in ("zzzzzzzzzzzzzzzz", "0" * 16, "../../etc", "%2e%2e"):
            status, _, _ = self.fixture.request(
                "GET", f"/api/runtimes/{bad}/artifacts")
            self.assertEqual(status, 404, bad)

    # -- feedback ------------------------------------------------------------

    def feedback(self, runtime_id, body):
        return self.fixture.request(
            "POST", f"/api/runtimes/{runtime_id}/artifacts/feedback",
            body=body)

    def intervention_log(self, runtime_root):
        log = runtime_root / "stub_interventions.log"
        if not log.is_file():
            return []
        return [json.loads(line) for line in
                log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_feedback_submits_bound_intervention_through_p5_surface(self):
        status, payload, _ = self.feedback(self.id_a, {
            "mode": "STEER", "comment": "Figure 2 is wrong. café",
            "message_id": 700107,
            "artifact_paths": ["reports/r.md", "evidence/t.txt"],
            "interrupt_current": False})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["artifact_feedback"]["message_id"], 700107)
        self.assertEqual(payload["artifact_feedback"]["artifact_paths"],
                         ["reports/r.md", "evidence/t.txt"])
        argv = self.intervention_log(self.fixture.runtime_a)
        self.assertEqual(len(argv), 1)
        text = next(a for a in argv[0] if a.startswith("--text="))
        self.assertIn("--mode=STEER", argv[0])
        self.assertIn("--target-message-id=700107", argv[0])
        self.assertNotIn("--interrupt-current-task", argv[0])
        self.assertIn("reports/r.md", text)
        self.assertIn("Figure 2", text)
        # References only — the artifact bytes are never dumped.
        self.assertNotIn("# Report caf", text)

    def test_feedback_audit_mode_with_interrupt(self):
        status, payload, _ = self.feedback(self.id_a, {
            "mode": "AUDIT", "comment": "Deep review the CSV.",
            "message_id": 700107, "artifact_paths": ["workspace/data.csv"],
            "interrupt_current": True})
        self.assertEqual(status, 200)
        argv = self.intervention_log(self.fixture.runtime_a)
        self.assertIn("--mode=AUDIT", argv[0])
        self.assertIn("--interrupt-current-task", argv[0])

    def test_feedback_refuses_unbound_and_cross_message_targets(self):
        for message_id, paths, expected in (
                (700999, ["reports/r.md"], "ARTIFACT_NOT_BOUND_TO_MESSAGE"),
                (700107, ["workspace/unbound.txt"],
                 "ARTIFACT_NOT_BOUND_TO_MESSAGE"),
                (700107, ["reports/ghost.md"], "ARTIFACT_UNAVAILABLE"),
                (700107, ["../escape.md"], "ARTIFACT_PATH_INVALID"),
                (700107, ["control/STOP"], "ARTIFACT_PATH_INVALID")):
            status, payload, _ = self.feedback(self.id_a, {
                "mode": "STEER", "comment": "bad binding",
                "message_id": message_id, "artifact_paths": paths,
                "interrupt_current": False})
            self.assertEqual(status, 409, (message_id, paths))
            self.assertEqual(payload["error"]["code"],
                             "ARTIFACT_FEEDBACK_REFUSED")
            self.assertEqual(payload["error"]["detail"]["reason"], expected)
        self.assertEqual(self.intervention_log(self.fixture.runtime_a), [])

    def test_feedback_schema_violations_fail_closed(self):
        base = {"mode": "STEER", "comment": "x", "message_id": 700107,
                "artifact_paths": ["reports/r.md"],
                "interrupt_current": False}
        for mutation in ({"mode": "FIX"}, {"comment": ""},
                         {"message_id": "700107"},
                         {"artifact_paths": []},
                         {"extra": 1}, {"root": "C:/elsewhere"}):
            payload = dict(base)
            payload.update(mutation)
            status, response, _ = self.feedback(self.id_a, payload)
            self.assertEqual(status, 400, mutation)
            self.assertEqual(response["error"]["code"],
                             "CONTROL_INVALID_PAYLOAD")
        self.assertEqual(self.intervention_log(self.fixture.runtime_a), [])

    def test_feedback_is_post_only(self):
        status, _, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/artifacts/feedback")
        self.assertEqual(status, 405)

    def test_artifact_routes_reject_post(self):
        for path in ("/artifacts", f"/artifacts/{artifact_id('reports/r.md')}",
                     f"/artifacts/{artifact_id('reports/r.md')}/preview"):
            status, _, _ = self.fixture.request(
                "POST", f"/api/runtimes/{self.id_a}{path}", body={})
            self.assertEqual(status, 405, path)

    # -- control-plane failure handling ---------------------------------------

    def test_status_failure_yields_error_envelope(self):
        (self.fixture.runtime_a / "stub_mode.txt").write_text(
            "exit2_json", encoding="utf-8")
        try:
            status, payload, _ = self.artifacts_of(self.id_a)
            self.assertEqual(status, 502)
            self.assertEqual(payload["error"]["code"], "CONTROL_PLANE_ERROR")
        finally:
            (self.fixture.runtime_a / "stub_mode.txt").unlink()

    def test_malformed_feedback_document_fails_closed(self):
        history = self.fixture.runtime_a / "stub_history"
        original = (history / "feedback_all.json").read_text(
            encoding="utf-8")
        (history / "feedback_all.json").write_text(
            '{"not": "a list"}', encoding="utf-8")
        try:
            status, payload, _ = self.artifacts_of(self.id_a)
            self.assertEqual(status, 502)
            self.assertEqual(payload["error"]["code"],
                             "CONTROL_PLANE_MALFORMED_OUTPUT")
        finally:
            (history / "feedback_all.json").write_text(original,
                                                       encoding="utf-8")

    def test_no_active_project_refuses_artifacts(self):
        status_file = self.fixture.runtime_a / "stub_status.json"
        original = status_file.read_text(encoding="utf-8")
        status_file.write_text(
            json.dumps(dict(STATUS_OK, PROJECT_ID=None)), encoding="utf-8")
        try:
            status, payload, _ = self.artifacts_of(self.id_a)
            self.assertEqual(status, 409)
            self.assertEqual(payload["error"]["code"],
                             "ARTIFACTS_NO_ACTIVE_PROJECT")
        finally:
            status_file.write_text(original, encoding="utf-8")

    # -- earlier surfaces intact ----------------------------------------------

    def test_p1_to_p5_routes_remain_intact(self):
        self.assertEqual(self.fixture.request("GET", "/api/health")[0], 200)
        self.assertEqual(self.fixture.request("GET", "/api/runtimes")[0], 200)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/cockpit")
        self.assertEqual(status, 200)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/timeline")
        self.assertEqual(status, 200)
        status, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{self.id_a}/controls")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
