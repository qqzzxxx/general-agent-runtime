"""Production publication -> completion -> real control CLI -> HTTP regression.

Every mutation is confined to disposable Runtime fixtures. No receipt invents
PUBLISHED_PATHS. Legacy cases downgrade only their own synthetic ledger entry.
"""
import copy
import http.client
import io
import json
from pathlib import Path
import shutil
import threading
import unittest
from unittest.mock import patch

import executor_completion as completion
import executor_fence as fence
import supervisor_control as control
import web_console_artifacts as artifacts
import web_console_server as server
import test_executor_fence as fence_fixture

SCRIPTS = Path(__file__).resolve().parent


class PublicationProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.fx = fence_fixture.ExecutorFenceTests(methodName="runTest")
        self.fx.setUp()
        self.addCleanup(self.fx.doCleanups)
        self.root = self.fx.root

    def publish_round(self, mid=700110, text="first", extra=False):
        identity = self.fx.dispatch(mid)
        self.assertEqual(self.fx.acquire(identity), 0)
        _, digest = self.fx.candidate(identity, text, "evidence/rounds.csv")
        self.fx.publish(identity, "evidence/rounds.csv", digest)
        if extra:
            _, extra_digest = self.fx.candidate(identity, "unlisted publication", "workspace/note.md")
            self.fx.publish(identity, "workspace/note.md", extra_digest)
        staging = self.fx.staging(identity, [{"path": "evidence/rounds.csv", "sha256": digest}])
        self.assertEqual(self.fx.commit(identity, staging), 0)
        entry = completion.lookup_entries(self.root, mid)[0]
        self.assertNotIn("PUBLISHED_PATHS", entry["RECEIPT"])
        return entry

    def feedback(self, mid=700110):
        return control.find_feedback(self.root, mid, "synthetic")

    def legacy(self, entry):
        old = copy.deepcopy(entry)
        old.pop("PUBLICATION_MANIFEST")
        old.pop("PUBLICATION_MANIFEST_SHA256")
        completion.entry_path(self.root, old["COMMIT_ID"]).write_text(json.dumps(old), encoding="utf-8")
        return old

    def start_http(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        for path in SCRIPTS.glob("*.py"):
            if not path.name.startswith("test_"):
                shutil.copyfile(path, scripts / path.name)
        shutil.copyfile(SCRIPTS.parent / "orchestrator.py", self.root / "orchestrator.py")
        self.http = server.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.root,
            console_root=SCRIPTS.parent, data_dir=self.root / "console-data", control_timeout=10)
        thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        thread.start()
        def close():
            self.http.shutdown()
            self.http.server_close()
            thread.join(timeout=5)
        self.addCleanup(close)
        code, payload = self.request("POST", "/api/runtimes", {"root": str(self.root), "label": "Provenance fixture"})
        self.assertEqual(code, 201, payload)
        self.base = "/api/runtimes/" + payload["runtime"]["id"]

    def request(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.http.server_address[1], timeout=30)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None,
                         {"Content-Type": "application/json"} if body is not None else {})
            result = conn.getresponse()
            return result.status, json.loads(result.read())
        finally:
            conn.close()

    def test_real_publication_completion_http_and_repeated_path(self):
        old = self.publish_round(extra=True)
        self.assertEqual(len(old["PUBLICATION_MANIFEST"]["publications"]), 2)
        self.assertTrue(completion.entry_hashes_intact(old))
        self.assertTrue(self.fx.o.consume_executor_receipt(self.fx.runtime)[0])
        new = self.publish_round(700111, "second")
        self.fx.o.seal_completions(self.fx.runtime, self.fx.state)
        (self.fx.project / "workspace/orphan-700110.txt").write_text("unbound", encoding="utf-8")
        self.start_http()
        results = {}
        for mid, count in ((700110, 2), (700111, 1)):
            code, payload = self.request("GET", self.base + f"/rounds/{mid}")
            self.assertEqual(code, 200, payload)
            self.assertTrue(payload["round"]["artifacts"]["available"], payload)
            paths = payload["round"]["artifacts"]["paths"]
            self.assertEqual(len(paths), count)
            code, catalog = self.request("GET", self.base + f"/artifacts?message_id={mid}")
            self.assertEqual(code, 200, catalog)
            rows = catalog["artifacts"]["artifacts"]
            self.assertEqual({r["path"] for r in rows}, {r["path"] for r in paths})
            record = next(r for r in rows if r["path"] == "evidence/rounds.csv")
            self.assertEqual(record["provenance"]["message_id"], mid)
            self.assertEqual(record["provenance"]["task_id"], "U1-U3")
            self.assertEqual(record["provenance"]["stage_id"], "BUILD-1")
            results[mid] = record["artifact_id"]
        self.assertNotEqual(results[700110], results[700111])
        for mid, verdict, code_expected in ((700110, "hash_mismatch", 409), (700111, "verified", 200)):
            url = self.base + "/artifacts/" + results[mid]
            code, detail = self.request("GET", url)
            self.assertEqual(code, 200, detail)
            self.assertEqual(detail["artifact"]["verification"], verdict)
            code, preview = self.request("GET", url + "/preview")
            self.assertEqual(code, code_expected, preview)
        code, unbound = self.request("GET", self.base + "/artifacts?status=unbound")
        self.assertEqual(code, 200)
        self.assertEqual([r["path"] for r in unbound["artifacts"]["artifacts"]], ["workspace/orphan-700110.txt"])
        self.assertNotEqual(old["PUBLICATION_MANIFEST_SHA256"], new["PUBLICATION_MANIFEST_SHA256"])

    def test_sealed_provenance_survives_archived_evidence_removal(self):
        entry = self.publish_round()
        archive = completion.staged_archive_dir(self.root, entry["COMMIT_ID"]) / "staging.json"
        archive.unlink()
        fence.publication_record(self.root, entry, "evidence/rounds.csv").unlink()
        self.assertEqual(self.feedback()["artifact_provenance"]["source"], "sealed_manifest")

    def test_preview_returns_only_the_snapshot_it_hashed(self):
        entry = self.publish_round(text="first")
        self.start_http()
        path = self.fx.project / "evidence/rounds.csv"
        original = path.read_bytes()
        open_path = Path.open
        reads = []
        def replacing_open(target, *args, **kwargs):
            if target == path and args and args[0] == "rb":
                reads.append(True)
                # Reproduce replacement after the verifier has opened its file.
                with open_path(path, "wb") as handle:
                    handle.write(b"second")
                return io.BytesIO(original if len(reads) == 1 else b"second")
            return open_path(target, *args, **kwargs)
        aid = artifacts.artifact_id_for_publication("evidence/rounds.csv", entry["COMMIT_ID"])
        with patch.object(Path, "open", replacing_open):
            code, payload = self.request("GET", self.base + f"/artifacts/{aid}/preview")
        self.assertEqual(code, 200, payload)
        self.assertIn("first", json.dumps(payload["preview"]))
        self.assertNotIn("second", json.dumps(payload["preview"]))
        self.assertEqual(len(reads), 1)

    def test_legacy_recovery_is_hash_bound_and_read_only(self):
        entry = self.legacy(self.publish_round())
        path = completion.entry_path(self.root, entry["COMMIT_ID"])
        before = path.read_bytes()
        (self.fx.project / "evidence/rounds.csv").write_text("later bytes", encoding="utf-8")
        result = self.feedback()["artifact_provenance"]
        self.assertEqual(result["integrity"], "OK")
        self.assertEqual(result["source"], "legacy_staging_and_publication")
        self.assertEqual(len(result["publications"]), 1)
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_missing_corrupt_or_mismatched_records_fail_closed(self):
        entry = self.legacy(self.publish_round())
        record_path = fence.publication_record(self.root, entry, "evidence/rounds.csv")
        original = record_path.read_bytes()
        for value in (b"{", b"{}", b'{"path":"a","path":"b"}', None):
            with self.subTest(value=value):
                if value is None:
                    record_path.unlink(missing_ok=True)
                else:
                    record_path.write_bytes(value)
                self.assertEqual(self.feedback()["artifact_provenance"]["integrity"], "UNAVAILABLE")
        for key, value in (("sha256", "0" * 64), ("PROJECT_ID", "other"), ("MESSAGE_ID", 700111), ("STAGE_ID", "other")):
            record = json.loads(original)
            record[key] = value
            record_path.write_text(json.dumps(record), encoding="utf-8")
            self.assertEqual(self.feedback()["artifact_provenance"]["publications"], [])
        record_path.write_bytes(original)
        staging = completion.staged_archive_dir(self.root, entry["COMMIT_ID"]) / "staging.json"
        staging.write_bytes(staging.read_bytes() + b" ")
        self.assertEqual(self.feedback()["artifact_provenance"]["integrity"], "UNAVAILABLE")

    def test_sealed_corruption_never_falls_back_to_valid_legacy_archive(self):
        entry = self.publish_round()
        path = completion.entry_path(self.root, entry["COMMIT_ID"])
        for kind in ("hash", "missing", "identity", "duplicate", "project"):
            damaged = copy.deepcopy(entry)
            manifest = damaged["PUBLICATION_MANIFEST"]
            if kind == "hash":
                manifest["publications"][0]["sha256"] = "0" * 64
            elif kind == "missing":
                damaged.pop("PUBLICATION_MANIFEST")
            elif kind == "identity":
                manifest["MESSAGE_ID"] += 1
            elif kind == "project":
                manifest["PROJECT_ID"] = "other"
            else:
                manifest["publications"].append(copy.deepcopy(manifest["publications"][0]))
                damaged["PUBLICATION_MANIFEST_SHA256"] = completion.canonical_json_sha256(manifest)
            path.write_text(json.dumps(damaged), encoding="utf-8")
            self.assertFalse(completion.entry_hashes_intact(damaged), kind)
            self.assertEqual(self.feedback()["artifact_provenance"]["publications"], [], kind)

    def test_ambiguous_ledger_never_binds_catalog_or_task_detail(self):
        entry = self.publish_round()
        duplicate = self.root / "handoff/completion_ledger/completion-700110-duplicate.json"
        duplicate.write_text(json.dumps(entry), encoding="utf-8")
        records = control.list_feedback(self.root, "synthetic")
        self.assertTrue(all(r["artifact_provenance"]["integrity"] == "AMBIGUOUS" for r in records))
        self.assertEqual(artifacts.build_artifact_index(records, [])["index"], {})
        with self.assertRaisesRegex(control.ControlError, "ambiguous"):
            self.feedback()

    def test_receipt_claims_without_runtime_evidence_cannot_bind(self):
        entry = self.legacy(self.publish_round())
        staging = completion.staged_archive_dir(self.root, entry["COMMIT_ID"]) / "staging.json"
        staging.unlink()
        entry["RECEIPT"]["PUBLISHED_PATHS"] = [{"path": "reports/fake.md", "sha256": "a" * 64}]
        entry["RECEIPT_SHA256"] = completion.canonical_json_sha256(entry["RECEIPT"])
        entry["BRIEF_SHA256"] = completion.sha256_bytes(completion.render_brief_bytes(entry["RECEIPT"]))
        completion.entry_path(self.root, entry["COMMIT_ID"]).write_text(json.dumps(entry), encoding="utf-8")
        feedback = self.feedback()
        self.assertEqual(artifacts.build_artifact_index([feedback], [])["index"], {})

    def test_invalid_publication_blocks_commit(self):
        identity = self.fx.dispatch()
        self.fx.acquire(identity)
        _, digest = self.fx.candidate(identity, "bytes")
        self.fx.publish(identity, "workspace/U1.txt", digest)
        record_path = fence.publication_record(self.root, identity, "workspace/U1.txt")
        record_path.write_text("{}", encoding="utf-8")
        with self.assertRaises(completion.CompletionError):
            self.fx.commit(identity, self.fx.staging(identity))
        self.assertEqual(completion.lookup_entries(self.root, identity["MESSAGE_ID"]), [])

    def test_expiry_during_manifest_io_still_blocks_completion(self):
        identity = self.fx.dispatch()
        self.fx.acquire(identity)
        _, digest = self.fx.candidate(identity, "bytes")
        self.fx.publish(identity, "workspace/U1.txt", digest)
        collect = fence.collect_locked
        def expiring_collect(*args):
            manifest = collect(*args)
            # PICKUP-EXECUTION-LIFECYCLE: the post-IO recheck enforces the
            # execution budget (CLAIMED_AT + MAX_TIME); exhaust that clock.
            _, claim_path = completion.load_claim(self.root, identity)
            claim_data = json.loads(
                claim_path.joinpath("claim.json").read_text(encoding="utf-8-sig"))
            claim_data["CLAIMED_AT"] = "2000-01-01T00:00:00+00:00"
            claim_path.joinpath("claim.json").write_text(
                json.dumps(claim_data), encoding="utf-8")
            return manifest
        with patch.object(fence, "collect_locked", expiring_collect):
            with self.assertRaisesRegex(completion.CompletionError, "expired"):
                self.fx.commit(identity, self.fx.staging(identity))
        self.assertEqual(completion.lookup_entries(self.root, identity["MESSAGE_ID"]), [])


if __name__ == "__main__":
    unittest.main()
