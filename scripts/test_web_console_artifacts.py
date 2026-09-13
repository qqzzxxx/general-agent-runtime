"""P6 Artifact Center pure-layer tests (offline).

Pins the deterministic, fail-closed half of the Artifact Center:

- artifact path normalization (traversal, absolute/UNC/drive/device names,
  backslashes, invalid encodings, authorized publication roots only);
- format classification for exactly the v1.3.0 preview formats;
- query parsing with fail-closed bounds;
- the artifact index/catalog projection over authoritative completion-ledger
  receipts plus bounded walk results: deterministic ordering, pagination,
  filters, provenance honesty classes (verified ledger binding, unbound,
  untrusted), republish linkage, and truncation surfacing;
- bounded preview documents (text/CSV/JSON/image/PDF states, truncation,
  magic verification, refusal to guess);
- the artifact-feedback typed schema and binding checks that reuse the P5
  formal intervention surface.

The module never touches the filesystem, the clock, randomness, or
subprocesses; these tests exercise it as pure functions only.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import web_console_artifacts as wca


def ledger_entry(message_id=700107, *, task_id="TASK-P6", stage_id="p6-a1",
                 attempt=1, integrity="OK", status="COMPLETION_SEALED",
                 committed_at="2026-09-12T01:00:00+00:00",
                 created_at="2026-09-12T00:59:00+00:00",
                 producer="GLM-5.3", published_paths=None):
    receipt = {
        "schema_version": 1, "MESSAGE_ID": message_id, "TASK_ID": task_id,
        "STAGE_ID": stage_id, "ATTEMPT": attempt, "NONCE": "n" * 24,
        "PROJECT_ID": "proj-x", "STATUS": "COMPLETE",
        "OUTCOME": "stage delivered", "CREATED_AT": created_at,
        "EXECUTOR_MODEL_FAMILY": producer,
    }
    return {"COMPLETION_PROTOCOL_VERSION": 1, "STATUS": status,
            "MESSAGE_ID": message_id, "TASK_ID": task_id,
            "STAGE_ID": stage_id, "ATTEMPT": attempt, "NONCE": "n" * 24,
            "PROJECT_ID": "proj-x",
            "COMMIT_ID": f"completion-{message_id}-abc",
            "COMMITTED_AT": committed_at, "CONSUMED_AT": None,
            "SEALED_AT": None, "CONSUMED_ARCHIVE": None,
            "ledger_file": f"handoff/completion_ledger/"
                           f"completion-{message_id}-abc.json",
            "integrity": integrity, "RECEIPT": receipt,
            "artifact_provenance": {"integrity": "OK", "source": "sealed_manifest",
                "publications": [{**{k: receipt[k] for k in
                    ("MESSAGE_ID", "TASK_ID", "STAGE_ID", "ATTEMPT", "NONCE", "PROJECT_ID")},
                    **item, "PUBLISHED_AT": created_at}
                    for item in (published_paths if published_paths is not None else
                                 [{"path": "reports/r.md", "sha256": "a" * 64}])]}}


def walk_file(path, size_bytes=100):
    return {"path": path, "size_bytes": size_bytes}


class NormalizePathTests(unittest.TestCase):
    def test_accepts_authorized_root_paths(self):
        for path in ("workspace/out.md", "evidence/x/tests.txt",
                     "reports/v1.3-p6-artifact-center.md"):
            self.assertEqual(wca.normalize_artifact_path(path), path)

    def test_rejects_non_string_and_empty(self):
        for value in (None, 12, b"workspace/x", "", "   "):
            with self.assertRaises(wca.ArtifactPathError):
                wca.normalize_artifact_path(value)

    def test_rejects_traversal(self):
        for path in ("workspace/../secret.txt", "../workspace/x",
                     "reports/a/../../b", "workspace/a/../b/../../../c"):
            with self.assertRaises(wca.ArtifactPathError):
                wca.normalize_artifact_path(path)

    def test_rejects_dot_segments_and_empty_segments(self):
        for path in ("workspace/./x", "workspace//x", "/workspace/x",
                     "workspace/x/", ".", ".."):
            with self.assertRaises(wca.ArtifactPathError):
                wca.normalize_artifact_path(path)

    def test_rejects_absolute_unc_and_drive_paths(self):
        for path in ("C:/workspace/x", "C:\\workspace\\x", "\\\\server\\share",
                     "//server/share", "/reports/x"):
            with self.assertRaises(wca.ArtifactPathError):
                wca.normalize_artifact_path(path)

    def test_rejects_backslashes_entirely(self):
        with self.assertRaises(wca.ArtifactPathError):
            wca.normalize_artifact_path("workspace\\x.md")

    def test_rejects_unauthorized_roots(self):
        for path in ("handoff/x.json", "control/STOP", "projects/p/x",
                     "artifacts/x.bin", "workspace2/x", "web_console/x"):
            with self.assertRaises(wca.ArtifactPathError):
                wca.normalize_artifact_path(path)

    def test_rejects_windows_device_names(self):
        for path in ("workspace/CON", "reports/NUL.txt", "workspace/AUX",
                     "evidence/COM1", "reports/LPT9.log",
                     "workspace/COM0.dat"):
            with self.assertRaises(wca.ArtifactPathError):
                wca.normalize_artifact_path(path)

    def test_rejects_too_long_and_control_characters(self):
        with self.assertRaises(wca.ArtifactPathError):
            wca.normalize_artifact_path("workspace/" + "x" * 600)
        with self.assertRaises(wca.ArtifactPathError):
            wca.normalize_artifact_path("workspace/a\x00b")
        with self.assertRaises(wca.ArtifactPathError):
            wca.normalize_artifact_path("workspace/a\nb")

    def test_rejects_invalid_encoding_surrogates(self):
        with self.assertRaises(wca.ArtifactPathError):
            wca.normalize_artifact_path("workspace/\udcffbad")

    def test_accepts_unicode_names(self):
        path = "reports/报告-café-✅.md"
        self.assertEqual(wca.normalize_artifact_path(path), path)


class ClassifyFormatTests(unittest.TestCase):
    def test_spec_formats_are_classified(self):
        cases = {
            "workspace/a.md": "markdown",
            "workspace/b.markdown": "markdown",
            "evidence/log.txt": "text",
            "evidence/run.log": "log",
            "scripts/x.py": "code",
            "workspace/x.JS": "code",
            "workspace/img.PNG": "png",
            "workspace/img.jpg": "jpeg",
            "workspace/img.JPEG": "jpeg",
            "workspace/pic.webp": "webp",
            "workspace/data.csv": "csv",
            "workspace/doc.json": "json",
            "workspace/doc.pdf": "pdf",
        }
        for path, expected in cases.items():
            self.assertEqual(wca.classify_format(path), expected, path)

    def test_more_common_code_extensions(self):
        for ext in (".py", ".js", ".ts", ".c", ".h", ".cpp", ".java", ".cs",
                    ".go", ".rs", ".rb", ".sh", ".ps1", ".sql", ".toml",
                    ".yaml", ".yml", ".ini"):
            self.assertEqual(wca.classify_format("workspace/x" + ext),
                             "code", ext)

    def test_active_content_and_unknown_formats_are_unsupported(self):
        for path in ("workspace/a.html", "workspace/a.htm", "workspace/a.svg",
                     "workspace/a.xml", "workspace/a.exe", "workspace/a.bin",
                     "workspace/a", "workspace/a.docx", "workspace/a.xlsx"):
            self.assertEqual(wca.classify_format(path), "unsupported", path)


class QueryParsingTests(unittest.TestCase):
    def test_defaults(self):
        params = wca.parse_artifacts_query("")
        self.assertEqual(params["page"], 1)
        self.assertEqual(params["page_size"], 20)
        self.assertIsNone(params["root"])
        self.assertIsNone(params["type"])
        self.assertIsNone(params["message_id"])
        self.assertIsNone(params["task"])
        self.assertEqual(params["q"], "")
        self.assertIsNone(params["status"])

    def test_valid_parameters(self):
        params = wca.parse_artifacts_query(
            "page=2&page_size=50&root=reports&type=markdown"
            "&message_id=700107&task=p6&q=caf%C3%A9&status=unbound")
        self.assertEqual(params["page"], 2)
        self.assertEqual(params["page_size"], 50)
        self.assertEqual(params["root"], "reports")
        self.assertEqual(params["type"], "markdown")
        self.assertEqual(params["message_id"], 700107)
        self.assertEqual(params["task"], "p6")
        self.assertEqual(params["q"], "café")
        self.assertEqual(params["status"], "unbound")

    def test_unknown_parameter_is_refused(self):
        with self.assertRaises(wca.ArtifactsQueryError):
            wca.parse_artifacts_query("root=C:%5Celsewhere")
        with self.assertRaises(wca.ArtifactsQueryError):
            wca.parse_artifacts_query("runtime_root=/etc")

    def test_duplicate_parameter_is_refused(self):
        with self.assertRaises(wca.ArtifactsQueryError):
            wca.parse_artifacts_query("page=1&page=2")

    def test_bounds_are_fail_closed(self):
        for query in ("page=0", "page=10000001", "page=abc",
                      "page_size=0", "page_size=101",
                      "root=everywhere", "type=exe",
                      "message_id=700107x", "message_id=" + "9" * 10,
                      "task=" + "x" * 121, "q=" + "x" * 121,
                      "status=ok,status"):
            with self.assertRaises(wca.ArtifactsQueryError, msg=query):
                wca.parse_artifacts_query(query)


class BuildIndexTests(unittest.TestCase):
    def test_bound_artifact_has_verified_ledger_provenance(self):
        result = wca.build_artifact_index(
            [ledger_entry(700107)], [walk_file("reports/r.md")])
        record = result["index"][wca.artifact_id_for_publication("reports/r.md", "completion-700107-abc")]
        self.assertEqual(record["path"], "reports/r.md")
        self.assertEqual(record["root"], "reports")
        self.assertEqual(record["format"], "markdown")
        self.assertEqual(record["name"], "r.md")
        self.assertEqual(record["availability"], "present")
        self.assertEqual(record["provenance"]["class"], "ledger")
        self.assertEqual(record["provenance"]["message_id"], 700107)
        self.assertEqual(record["provenance"]["task_id"], "TASK-P6")
        self.assertEqual(record["provenance"]["stage_id"], "p6-a1")
        self.assertEqual(record["provenance"]["attempt"], 1)
        self.assertEqual(record["provenance"]["producer"], "GLM-5.3")
        self.assertEqual(record["provenance"]["committed_at"],
                         "2026-09-12T01:00:00+00:00")
        self.assertEqual(record["provenance"]["completion_status"],
                         "COMPLETION_SEALED")
        self.assertEqual(record["expected_sha256"], "a" * 64)

    def test_unbound_file_is_surfaced_without_invented_provenance(self):
        result = wca.build_artifact_index(
            [ledger_entry(700107)], [walk_file("workspace/orphan.bin")])
        record = result["index"][
            wca.artifact_id_for_path("workspace/orphan.bin")]
        self.assertEqual(record["provenance"]["class"], "unbound")
        self.assertIsNone(record["provenance"]["message_id"])
        self.assertIsNone(record["expected_sha256"])

    def test_bound_but_missing_file_is_reported_missing(self):
        result = wca.build_artifact_index(
            [ledger_entry(700107)], [])
        record = result["index"][wca.artifact_id_for_publication("reports/r.md", "completion-700107-abc")]
        self.assertEqual(record["availability"], "missing")
        self.assertEqual(record["provenance"]["class"], "ledger")

    def test_untrusted_ledger_entry_never_binds_provenance(self):
        result = wca.build_artifact_index(
            [ledger_entry(700108, integrity="HASH_MISMATCH",
                          published_paths=[
                              {"path": "reports/evil.md", "sha256": "b" * 64}])],
            [walk_file("reports/evil.md")])
        record = result["index"][
            wca.artifact_id_for_path("reports/evil.md")]
        self.assertEqual(record["provenance"]["class"], "unbound")
        self.assertEqual(result["honesty"]["untrusted_ledger_entries"]
                         ["count"], 1)

    def test_invalid_ledger_record_is_surfaced(self):
        result = wca.build_artifact_index(
            [{"ledger_file": "x.json",
              "integrity": "INVALID_LEDGER_ENTRY"}], [])
        self.assertEqual(result["honesty"]["untrusted_ledger_entries"]
                         ["count"], 1)
        self.assertEqual(result["index"], {})

    def test_receipt_path_that_cannot_normalize_is_counted(self):
        result = wca.build_artifact_index(
            [ledger_entry(700107, published_paths=[
                {"path": "../escape.md", "sha256": "a" * 64}])], [])
        self.assertEqual(result["index"], {})
        self.assertEqual(result["honesty"]["unusable_publication_paths"]
                         ["count"], 1)

    def test_republished_path_links_later_round(self):
        entries = [ledger_entry(700107, published_paths=[
                       {"path": "reports/live.md", "sha256": "a" * 64}]),
                   ledger_entry(700109, committed_at="2026-09-12T02:00:00+00:00",
                                published_paths=[
                                    {"path": "reports/live.md",
                                     "sha256": "c" * 64}])]
        result = wca.build_artifact_index(entries, [])
        record = result["index"][wca.artifact_id_for_publication("reports/live.md", "completion-700109-abc")]
        self.assertEqual(record["provenance"]["message_id"], 700109)
        self.assertEqual(len(result["index"]), 2)
        older = result["index"][wca.artifact_id_for_publication("reports/live.md", "completion-700107-abc")]
        self.assertEqual(older["expected_sha256"], "a" * 64)
        self.assertEqual(older["provenance"]["message_id"], 700107)
        self.assertEqual(record["expected_sha256"], "c" * 64)

    def test_index_is_deterministic(self):
        entries = [ledger_entry(700107), ledger_entry(700108, published_paths=[
            {"path": "workspace/w.json", "sha256": "d" * 64}])]
        files = [walk_file("reports/r.md"), walk_file("workspace/w.json"),
                 walk_file("workspace/z.txt")]
        first = wca.build_artifact_index(entries, files)
        second = wca.build_artifact_index(list(reversed(entries)),
                                          list(reversed(files)))
        self.assertEqual(first, second)


class CatalogTests(unittest.TestCase):
    def setUp(self):
        entries = [
            ledger_entry(700107, published_paths=[
                {"path": "reports/r.md", "sha256": "a" * 64},
                {"path": "evidence/t.txt", "sha256": "b" * 64}]),
            ledger_entry(700108, committed_at="2026-09-12T02:00:00+00:00",
                         published_paths=[
                             {"path": "workspace/w.json",
                              "sha256": "d" * 64}]),
        ]
        files = [walk_file("reports/r.md"), walk_file("evidence/t.txt"),
                 walk_file("workspace/w.json"),
                 walk_file("workspace/orphan.bin"),
                 walk_file("evidence/run.log")]
        built = wca.build_artifact_index(entries, files)
        self.index = built["index"]
        self.honesty = built["honesty"]

    def catalog(self, query=""):
        return wca.project_catalog(
            self.index, self.honesty, wca.parse_artifacts_query(query),
            generated_at="2026-09-12T10:00:00+00:00")

    def test_catalog_is_deterministic_and_sorted_by_path(self):
        first = self.catalog()
        second = self.catalog()
        self.assertEqual(first, second)
        paths = [item["path"] for item in first["artifacts"]]
        self.assertEqual(paths, sorted(paths))

    def test_pagination_is_deterministic(self):
        page_one = self.catalog("page_size=2&page=1")
        page_two = self.catalog("page_size=2&page=2")
        self.assertEqual(page_one["totals"]["artifacts"], 5)
        self.assertEqual(page_one["totals"]["pages"], 3)
        self.assertEqual(len(page_one["artifacts"]), 2)
        self.assertEqual(len(page_two["artifacts"]), 2)
        all_paths = [item["path"] for item in page_one["artifacts"]] + \
                    [item["path"] for item in page_two["artifacts"]]
        self.assertEqual(all_paths, sorted(
            item["path"] for item in self.catalog()["artifacts"])[:4])

    def test_filters_select_expected_subsets(self):
        self.assertEqual(
            [item["path"] for item in self.catalog("root=reports")["artifacts"]],
            ["reports/r.md"])
        self.assertEqual(
            [item["path"] for item in self.catalog("type=json")["artifacts"]],
            ["workspace/w.json"])
        self.assertEqual(
            [item["path"] for item in
             self.catalog("message_id=700107")["artifacts"]],
            ["evidence/t.txt", "reports/r.md"])
        self.assertEqual(
            [item["path"] for item in self.catalog("q=orphan")["artifacts"]],
            ["workspace/orphan.bin"])
        self.assertEqual(
            [item["path"] for item in self.catalog("status=unbound")["artifacts"]],
            ["evidence/run.log", "workspace/orphan.bin"])
        self.assertEqual(
            [item["path"] for item in self.catalog("status=ledger")["artifacts"]],
            ["evidence/t.txt", "reports/r.md", "workspace/w.json"])

    def test_task_filter_matches_substring(self):
        entries = [ledger_entry(700107, task_id="V13_P6_ARTIFACT_CENTER")]
        built = wca.build_artifact_index(
            entries, [walk_file("reports/r.md")])
        catalog = wca.project_catalog(
            built["index"], built["honesty"],
            wca.parse_artifacts_query("task=p6_artifact"),
            generated_at="2026-09-12T10:00:00+00:00")
        self.assertEqual(len(catalog["artifacts"]), 1)

    def test_honesty_block_carries_counts(self):
        catalog = self.catalog()
        self.assertIn("honesty", catalog)
        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["query"]["page"], 1)


class PreviewTests(unittest.TestCase):
    def test_text_preview_ok(self):
        doc = wca.project_preview("text", "hello café\n".encode("utf-8"),
                                  total_bytes=11)
        self.assertEqual(doc["kind"], "text")
        self.assertEqual(doc["state"], "OK")
        self.assertIn("café", doc["content"])
        self.assertFalse(doc["truncated"])

    def test_text_preview_truncates_by_bytes_lines_and_chars(self):
        big = ("line\n" * 9000).encode("utf-8")
        doc = wca.project_preview("text", big, total_bytes=len(big))
        self.assertEqual(doc["state"], "TRUNCATED")
        self.assertTrue(doc["truncated"])
        self.assertLessEqual(doc["returned_bytes"], wca.MAX_PREVIEW_BYTES)
        self.assertLessEqual(doc["returned_lines"], wca.MAX_PREVIEW_LINES)
        self.assertLessEqual(len(doc["content"]), wca.MAX_PREVIEW_CHARS)

    def test_text_preview_refuses_invalid_utf8(self):
        doc = wca.project_preview("log", b"\xff\xfe\x00bad", total_bytes=6)
        self.assertEqual(doc["state"], "NOT_UTF8")
        self.assertIsNone(doc["content"])

    def test_markdown_preview_is_plain_text(self):
        doc = wca.project_preview("markdown",
                                  "# Title\n<script>x</script>\n"
                                  .encode("utf-8"), total_bytes=27)
        self.assertEqual(doc["kind"], "text")
        self.assertIn("<script>", doc["content"])

    def test_json_preview_valid_and_malformed(self):
        doc = wca.project_preview("json", b'{"a": 1}', total_bytes=8)
        self.assertEqual(doc["state"], "OK")
        self.assertIn('"a"', doc["content"])
        bad = wca.project_preview("json", b'{"a": ', total_bytes=6)
        self.assertEqual(bad["state"], "MALFORMED_JSON")
        self.assertIsNotNone(bad["error"])

    def test_csv_preview_rows_and_truncation(self):
        rows = "\n".join(",".join(str(i) for i in range(10))
                         for _ in range(300)) + "\n"
        doc = wca.project_preview("csv", rows.encode("utf-8"),
                                  total_bytes=len(rows))
        self.assertEqual(doc["kind"], "table")
        self.assertEqual(doc["state"], "TRUNCATED")
        self.assertEqual(len(doc["rows"]), wca.MAX_PREVIEW_ROWS)

    def test_csv_preview_ok(self):
        doc = wca.project_preview("csv", b"a,b\n1,2\n", total_bytes=8)
        self.assertEqual(doc["state"], "OK")
        self.assertEqual(doc["rows"], [["a", "b"], ["1", "2"]])

    def test_image_magic_verification(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        doc = wca.project_preview("png", png, total_bytes=len(png))
        self.assertEqual(doc["kind"], "image")
        self.assertEqual(doc["state"], "OK")
        self.assertEqual(doc["media_type"], "image/png")
        polyglot = b"<html><script>alert(1)</script></html>"
        bad = wca.project_preview("png", polyglot, total_bytes=len(polyglot))
        self.assertEqual(bad["state"], "FORMAT_MISMATCH")
        self.assertIsNone(bad["content"])

    def test_jpeg_and_webp_magic(self):
        jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 16
        self.assertEqual(wca.project_preview(
            "jpeg", jpeg, total_bytes=len(jpeg))["state"], "OK")
        self.assertEqual(wca.project_preview(
            "jpeg", b"GIF89a" + b"\x00" * 16, total_bytes=22)["state"],
            "FORMAT_MISMATCH")
        webp = b"RIFF\x24\x00\x00\x00WEBVP" .replace(b"VP", b"P ") + b"\x00" * 8
        self.assertEqual(wca.project_preview(
            "webp", webp, total_bytes=len(webp))["state"], "OK")

    def test_pdf_magic_and_attachment_disposition(self):
        pdf = b"%PDF-1.7\n%" + b"\xe2\xe3\xcf\xd3\n" + b"\x00" * 16
        doc = wca.project_preview("pdf", pdf, total_bytes=len(pdf))
        self.assertEqual(doc["kind"], "pdf")
        self.assertEqual(doc["state"], "OK")
        self.assertEqual(doc["media_type"], "application/pdf")
        self.assertEqual(doc["disposition"], "attachment")
        bad = wca.project_preview("pdf", b"MZ\x90\x00" + b"\x00" * 16,
                                  total_bytes=20)
        self.assertEqual(bad["state"], "FORMAT_MISMATCH")

    def test_unsupported_format_is_honest(self):
        doc = wca.project_preview("unsupported", b"MZ\x90\x00", total_bytes=4)
        self.assertEqual(doc["kind"], "unavailable")
        self.assertEqual(doc["state"], "UNSUPPORTED")
        self.assertIsNone(doc.get("content"))

    def test_image_size_cap_refuses_before_guessing(self):
        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        doc = wca.project_preview("png", png,
                                  total_bytes=wca.MAX_IMAGE_BYTES + 1)
        self.assertEqual(doc["state"], "TOO_LARGE")

    def test_pdf_size_cap(self):
        pdf = b"%PDF-1.7\n" + b"\x00" * 8
        doc = wca.project_preview("pdf", pdf,
                                  total_bytes=wca.MAX_PDF_BYTES + 1)
        self.assertEqual(doc["state"], "TOO_LARGE")


class FeedbackSchemaTests(unittest.TestCase):
    def base(self, **overrides):
        payload = {"mode": "STEER", "comment": "Please re-check table 2.",
                   "message_id": 700107,
                   "artifact_paths": ["reports/r.md"],
                   "interrupt_current": False}
        payload.update(overrides)
        return payload

    def test_valid_request_normalizes_paths(self):
        request = wca.validate_artifact_feedback_request(self.base())
        self.assertEqual(request["mode"], "STEER")
        self.assertEqual(request["message_id"], 700107)
        self.assertEqual(request["artifact_paths"], ["reports/r.md"])
        self.assertFalse(request["interrupt_current"])

    def test_exact_key_set_is_enforced(self):
        payload = self.base()
        payload["root"] = "C:/elsewhere"
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(payload)
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request({"mode": "STEER"})

    def test_mode_and_comment_rules(self):
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(self.base(mode="FIX"))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(self.base(comment=""))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(self.base(comment="x" * 4001))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(self.base(comment="a\x01b"))

    def test_message_id_bounds(self):
        for value in (None, True, "700107", -1, 1000000000):
            with self.assertRaises(wca.ControlRequestError, msg=repr(value)):
                wca.validate_artifact_feedback_request(
                    self.base(message_id=value))

    def test_artifact_paths_are_normalized_and_bounded(self):
        with self.assertRaises(wca.ArtifactPathError):
            wca.validate_artifact_feedback_request(
                self.base(artifact_paths=["../escape.md"]))
        with self.assertRaises(wca.ArtifactPathError):
            wca.validate_artifact_feedback_request(
                self.base(artifact_paths=["C:/workspace/x"]))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(
                self.base(artifact_paths=[]))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(
                self.base(artifact_paths=[f"reports/f{i}.md"
                                          for i in range(9)]))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(
                self.base(artifact_paths=["reports/r.md", "reports/r.md"]))
        with self.assertRaises(wca.ControlRequestError):
            wca.validate_artifact_feedback_request(
                self.base(artifact_paths="reports/r.md"))

    def test_binding_requires_ledger_class_and_same_message(self):
        built = wca.build_artifact_index(
            [ledger_entry(700107, published_paths=[
                {"path": "reports/r.md", "sha256": "a" * 64}])],
            [walk_file("reports/r.md"), walk_file("workspace/orphan.bin")])
        request = wca.validate_artifact_feedback_request(
            self.base(artifact_paths=["reports/r.md"]))
        self.assertIsNone(wca.check_feedback_binding(request, built["index"]))
        wrong_round = wca.validate_artifact_feedback_request(
            self.base(message_id=700999,
                      artifact_paths=["reports/r.md"]))
        self.assertEqual(
            wca.check_feedback_binding(wrong_round, built["index"]),
            "ARTIFACT_NOT_BOUND_TO_MESSAGE")
        unbound = wca.validate_artifact_feedback_request(
            self.base(artifact_paths=["workspace/orphan.bin"]))
        self.assertEqual(
            wca.check_feedback_binding(unbound, built["index"]),
            "ARTIFACT_NOT_BOUND_TO_MESSAGE")
        unknown = wca.validate_artifact_feedback_request(
            self.base(artifact_paths=["reports/missing.md"]))
        self.assertEqual(
            wca.check_feedback_binding(unknown, built["index"]),
            "ARTIFACT_UNAVAILABLE")

    def test_composed_text_is_concise_and_bounded(self):
        text = wca.compose_artifact_feedback_text(
            "STEER", 700107, ["reports/r.md", "evidence/t.txt"],
            "Table 2 disagrees with the receipt. café ✅")
        self.assertIn("STEER", text)
        self.assertIn("700107", text)
        self.assertIn("reports/r.md", text)
        self.assertIn("evidence/t.txt", text)
        self.assertIn("Table 2", text)
        self.assertLess(len(text), 20000)
        # References only — never artifact content or full project history.
        self.assertNotIn("```json", text)


if __name__ == "__main__":
    unittest.main()
