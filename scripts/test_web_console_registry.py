"""P2 Web Console Runtime Registry tests (offline, localhost only).

Covers the Console-owned persistent Runtime Registry: explicit add/list/
rename/revalidate/remove-without-delete semantics, server-generated opaque
stable IDs, atomic restart-resilient storage, fail-closed validation
(nonexistent/file/non-Runtime/incompatible roots, duplicate canonical roots,
invalid labels/payloads, corrupted storage), Unicode-safe labels, and
adversarial two-root isolation for the opaque-ID status route (no path
selectors, no traversal/absolute/encoded/mixed-separator IDs, no cross-root
attribution). No GUI, no external network, no disk scanning, no Runtime
creation, and no mutation of any registered Runtime tree.

Fixture Runtime Roots are synthetic directories whose control-plane stubs
emit unmistakably different status payloads, so any cross-root data leakage
is observable as a test failure.
"""
from __future__ import annotations

import hashlib
import http.client
import importlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
REPO = SCRIPTS.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import web_console_server as wcs

ID_PATTERN = re.compile(r"^[0-9a-f]{16}$")

MARKER_A = "ROOT-A-MARKER-café"
MARKER_B = "ROOT-B-MARKER-中文"

STUB_TEMPLATE = '''\
import json, sys, time
from pathlib import Path
root = Path(__file__).resolve().parents[1]
mode_file = root / "stub_mode.txt"
mode = mode_file.read_text(encoding="utf-8").strip() if mode_file.exists() else "ok"
MARKER = {marker!r}


def emit(value):
    # Mirror the v1.2 machine mode: one ASCII-escaped JSON document.
    print(json.dumps(value, ensure_ascii=True, indent=2))


if mode == "ok":
    emit({{"schema_version": 1, "PROJECT_ID": MARKER,
          "runtime_status": MARKER + "-STATUS", "project_status": "SUPERVISOR_TURN",
          "pause": {{"status": "RUNNING"}}, "active_task": None,
          "active_task_claimed": False, "pending_interventions": 0,
          "stop": False, "human_review": False}})
    sys.exit(0)
if mode == "unicode":
    emit({{"schema_version": 1, "PROJECT_ID": MARKER, "note": "café 中文"}})
    sys.exit(0)
if mode == "schema99":
    emit({{"schema_version": 99, "PROJECT_ID": MARKER}})
    sys.exit(99)
if mode == "exit2_json":
    emit({{"ok": False, "error": "boom: café", "error_type": "ControlError"}})
    sys.exit(2)
if mode == "garbage_ok":
    sys.stdout.write("this is definitely not json\\n")
    sys.exit(0)
if mode == "sleep":
    time.sleep(30)
    sys.exit(0)
if mode == "crash":
    sys.exit(1)
sys.exit(3)
'''


def write_stub_runtime(runtime_root: Path, marker: str) -> None:
    (runtime_root / "scripts").mkdir(parents=True, exist_ok=True)
    (runtime_root / "scripts" / "supervisor_control.py").write_text(
        STUB_TEMPLATE.format(marker=marker), encoding="utf-8")


def tree_hash(root: Path) -> str:
    """Order-independent-per-path recursive content hash of a directory tree."""
    digest = hashlib.sha256()
    for path in sorted(Path(root).rglob("*")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        if path.is_file():
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        digest.update(b"\x01")
    return digest.hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="wc-registry-")
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)


class RegistryStoreTests(Base):
    """Direct RuntimeRegistry semantics (no HTTP)."""

    def setUp(self):
        super().setUp()
        self.wcreg = importlib.import_module("web_console_registry")
        self.data_dir = self.base / "data"
        self.runtime_a = self.base / "runtime-a"
        self.runtime_b = self.base / "runtime-b"
        write_stub_runtime(self.runtime_a, MARKER_A)
        write_stub_runtime(self.runtime_b, MARKER_B)

    def registry(self, **kwargs):
        return self.wcreg.RuntimeRegistry(self.data_dir, **kwargs)

    def add_a(self, label="Runtime A", registry=None, root=None):
        return (registry or self.registry()).add(
            str(root or self.runtime_a), label)

    # -- add ----------------------------------------------------------------

    def test_add_creates_entry_with_opaque_id_and_validation(self):
        entry = self.add_a()
        self.assertRegex(entry["id"], ID_PATTERN)
        self.assertEqual(Path(entry["root"]).resolve(), self.runtime_a.resolve())
        self.assertEqual(entry["label"], "Runtime A")
        self.assertTrue(entry["validation"]["ok"])
        self.assertEqual(entry["validation"]["status_schema_version"], 1)
        document = json.loads(
            (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_text(
                encoding="utf-8"))
        self.assertEqual(document["runtimes"][entry["id"]]["id"], entry["id"])

    def test_add_survives_restart_with_same_id(self):
        first = self.add_a()
        reopened = self.registry()
        entries = reopened.list_entries()
        self.assertEqual([e["id"] for e in entries], [first["id"]])
        self.assertEqual(entries[0]["label"], "Runtime A")

    def test_add_rejects_nonexistent_root(self):
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            self.registry().add(str(self.base / "missing"), "x")
        self.assertEqual(caught.exception.code, "REGISTRY_ROOT_NOT_FOUND")

    def test_add_rejects_file_root(self):
        target = self.base / "plain-file.txt"
        target.write_text("data", encoding="utf-8")
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            self.registry().add(str(target), "x")
        self.assertEqual(caught.exception.code, "REGISTRY_ROOT_NOT_A_DIRECTORY")

    def test_add_rejects_non_runtime_directory(self):
        bare = self.base / "bare-dir"
        bare.mkdir()
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            self.registry().add(str(bare), "x")
        self.assertEqual(caught.exception.code, "REGISTRY_NOT_A_RUNTIME_ROOT")

    def test_add_rejects_relative_and_empty_roots(self):
        for bad in ("", "relative/path", ".", "runtime-a"):
            with self.assertRaises(self.wcreg.RegistryError) as caught:
                self.registry().add(bad, "x")
            self.assertEqual(caught.exception.code, "REGISTRY_INVALID_ROOT", bad)

    def test_add_rejects_unverifiable_control_plane(self):
        registry = self.registry()
        (self.runtime_a / "stub_mode.txt").write_text("exit2_json",
                                                      encoding="utf-8")
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.add(str(self.runtime_a), "x")
        self.assertEqual(caught.exception.code, "REGISTRY_NOT_A_RUNTIME_ROOT")

    def test_add_rejects_incompatible_status_schema(self):
        registry = self.registry()
        (self.runtime_a / "stub_mode.txt").write_text("schema99",
                                                      encoding="utf-8")
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.add(str(self.runtime_a), "x")
        self.assertEqual(caught.exception.code, "REGISTRY_UNSUPPORTED_SCHEMA")

    def test_add_rejects_duplicate_canonical_roots(self):
        registry = self.registry()
        self.add_a(registry=registry)
        variants = [
            str(self.runtime_a),                              # exact again
            str(self.runtime_a) + "\\",                       # trailing sep
            str(self.runtime_a) + "/",                        # trailing slash
            str(self.base / "runtime-a" / "." ),              # dot segment
            str(self.runtime_a).replace("\\", "/"),           # forward slashes
            str(self.runtime_a).upper(),                      # case variant
            str(self.runtime_a).lower(),                      # case variant
        ]
        for variant in variants:
            with self.assertRaises(self.wcreg.RegistryError) as caught:
                registry.add(variant, "alias")
            self.assertEqual(caught.exception.code, "REGISTRY_DUPLICATE_ROOT",
                             variant)
        self.assertEqual(len(registry.list_entries()), 1)

    def test_add_uses_bounded_probe_timeout(self):
        registry = self.registry(control_timeout=0.5)
        (self.runtime_a / "stub_mode.txt").write_text("sleep", encoding="utf-8")
        started = time.monotonic()
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.add(str(self.runtime_a), "x")
        self.assertLess(time.monotonic() - started, 15.0)
        self.assertEqual(caught.exception.code, "REGISTRY_NOT_A_RUNTIME_ROOT")

    # -- labels -------------------------------------------------------------

    def test_label_validation_boundaries(self):
        registry = self.registry()
        for bad in ("", "   ", "a" * 121, "line1\nline2", "tab\there",
                    "null\x00byte"):
            with self.assertRaises(self.wcreg.RegistryError) as caught:
                registry.add(str(self.runtime_a), bad)
            self.assertEqual(caught.exception.code, "REGISTRY_INVALID_LABEL",
                             repr(bad))
        self.assertEqual(registry.list_entries(), [])

    def test_unicode_label_round_trips(self):
        entry = self.add_a(label="科研 Runtime — café 中文 🚀")
        self.assertEqual(entry["label"], "科研 Runtime — café 中文 🚀")
        reopened = self.registry().list_entries()[0]
        self.assertEqual(reopened["label"], "科研 Runtime — café 中文 🚀")
        raw = (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_bytes()
        self.assertIn("科研".encode("utf-8"), raw)  # stored as real UTF-8

    def test_label_length_boundary_accepted(self):
        entry = self.add_a(label="L" * 120)
        self.assertEqual(len(entry["label"]), 120)

    # -- rename / revalidate / remove --------------------------------------

    def test_rename_changes_only_the_label(self):
        registry = self.registry()
        entry = self.add_a(registry=registry)
        before = json.loads(
            (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_text(
                encoding="utf-8"))
        renamed = registry.rename(entry["id"], "重新命名 — renamed")
        self.assertEqual(renamed["label"], "重新命名 — renamed")
        after = json.loads(
            (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_text(
                encoding="utf-8"))
        before_entry = dict(before["runtimes"][entry["id"]])
        after_entry = dict(after["runtimes"][entry["id"]])
        before_entry["label"] = after_entry["label"] = None
        before_entry["updated_at"] = after_entry["updated_at"] = None
        self.assertEqual(before_entry, after_entry)
        self.assertEqual(
            tree_hash(self.runtime_a),
            tree_hash(self.runtime_a))  # sanity: helper deterministic
        reopened = self.registry().get(entry["id"])
        self.assertEqual(reopened["label"], "重新命名 — renamed")

    def test_rename_rejects_invalid_labels_and_unknown_ids(self):
        registry = self.registry()
        entry = self.add_a(registry=registry)
        for bad in ("", "  ", "x" * 121, "bad\nlabel"):
            with self.assertRaises(self.wcreg.RegistryError) as caught:
                registry.rename(entry["id"], bad)
            self.assertEqual(caught.exception.code, "REGISTRY_INVALID_LABEL")
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.rename("0123456789abcdef", "new")
        self.assertEqual(caught.exception.code, "RUNTIME_UNKNOWN")

    def test_revalidate_updates_validation_metadata(self):
        registry = self.registry()
        entry = self.add_a(registry=registry)
        (self.runtime_a / "stub_mode.txt").write_text("unicode",
                                                      encoding="utf-8")
        time.sleep(1.1)  # ensure a visibly later checked_at
        updated = registry.revalidate(entry["id"])
        self.assertTrue(updated["validation"]["ok"])
        self.assertEqual(updated["validation"]["status_schema_version"], 1)
        self.assertGreaterEqual(updated["validation"]["checked_at"],
                                entry["validation"]["checked_at"])
        self.assertEqual(updated["id"], entry["id"])

    def test_revalidate_reports_broken_root_fail_closed(self):
        registry = self.registry()
        entry = self.add_a(registry=registry)
        (self.runtime_a / "scripts" / "supervisor_control.py").unlink()
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.revalidate(entry["id"])
        self.assertEqual(caught.exception.code, "REGISTRY_NOT_A_RUNTIME_ROOT")
        stored = self.registry().get(entry["id"])
        self.assertFalse(stored["validation"]["ok"])
        self.assertEqual(stored["validation"]["failure_code"],
                         "REGISTRY_NOT_A_RUNTIME_ROOT")

    def test_remove_drops_only_registry_entry_and_never_touches_root(self):
        registry = self.registry()
        entry_a = self.add_a(registry=registry)
        entry_b = registry.add(str(self.runtime_b), "Runtime B")
        hash_a = tree_hash(self.runtime_a)
        hash_b = tree_hash(self.runtime_b)
        removed = registry.remove(entry_a["id"])
        self.assertEqual(removed["id"], entry_a["id"])
        self.assertEqual(tree_hash(self.runtime_a), hash_a)
        self.assertEqual(tree_hash(self.runtime_b), hash_b)
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.get(entry_a["id"])
        self.assertEqual(caught.exception.code, "RUNTIME_UNKNOWN")
        remaining = self.registry().list_entries()
        self.assertEqual([e["id"] for e in remaining], [entry_b["id"]])
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.remove(entry_a["id"])
        self.assertEqual(caught.exception.code, "RUNTIME_UNKNOWN")
        self.assertEqual(tree_hash(self.runtime_a), hash_a)

    # -- corrupted storage --------------------------------------------------

    def prepare_corrupt(self, raw: bytes) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / self.wcreg.REGISTRY_FILE_NAME
        path.write_bytes(raw)

    def test_corrupt_storage_fails_closed_and_is_never_reset(self):
        for raw in (b"{not json", b"[]", b'{"schema_version": 2, "runtimes": {}}',
                    b'{"schema_version": 1}', b'{"schema_version": 1, "runtimes": []}'):
            self.prepare_corrupt(raw)
            registry = self.registry()
            for action in (registry.list_entries,
                           lambda: registry.get("0123456789abcdef")):
                with self.assertRaises(self.wcreg.RegistryError) as caught:
                    action()
                self.assertEqual(caught.exception.code, "REGISTRY_CORRUPT")
            with self.assertRaises(self.wcreg.RegistryError) as caught:
                registry.add(str(self.runtime_b), "B")
            self.assertEqual(caught.exception.code, "REGISTRY_CORRUPT")
            self.assertEqual(
                (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_bytes(),
                raw)  # no silent reset/overwrite

    def test_storage_with_mangled_entry_fails_closed(self):
        self.prepare_corrupt(json.dumps(
            {"schema_version": 1, "updated_at": "2026-09-12T00:00:00+00:00",
             "runtimes": {"0123456789abcdef": {"id": "0123456789abcdef"}}},
            ensure_ascii=False).encode("utf-8"))
        registry = self.registry()
        with self.assertRaises(self.wcreg.RegistryError) as caught:
            registry.list_entries()
        self.assertEqual(caught.exception.code, "REGISTRY_CORRUPT")

    def test_missing_storage_starts_empty(self):
        self.assertEqual(self.registry().list_entries(), [])

    # -- concurrency --------------------------------------------------------

    def test_concurrent_conflicting_updates_serialize(self):
        registry = self.registry()
        results = {"ok": [], "duplicate": 0, "unexpected": []}

        def worker(n):
            try:
                entry = registry.add(str(self.runtime_a), f"w{n}")
                results["ok"].append(entry["id"])
            except self.wcreg.RegistryError as exc:
                if exc.code == "REGISTRY_DUPLICATE_ROOT":
                    results["duplicate"] += 1
                else:
                    results["unexpected"].append(exc.code)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        self.assertEqual(results["unexpected"], [])
        self.assertEqual(len(results["ok"]) + results["duplicate"], 6)
        self.assertLessEqual(len(results["ok"]), 1)
        reopened = self.registry()
        document = json.loads(
            (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_text(
                encoding="utf-8"))
        self.assertEqual(len(document["runtimes"]), len(results["ok"]))
        self.assertEqual(len(reopened.list_entries()), len(results["ok"]))

    def test_registry_file_is_valid_utf8_json_without_ascii_escaping(self):
        self.add_a(label="中文 label")
        raw = (self.data_dir / self.wcreg.REGISTRY_FILE_NAME).read_bytes()
        document = json.loads(raw.decode("utf-8"))
        self.assertEqual(document["schema_version"], 1)
        self.assertNotIn(b"\\u", raw)


class RegistryHttpFixture:
    """One console server + two unmistakably different fixture Runtime Roots."""

    def __init__(self, base: Path, data_dir: Path | None = None,
                 control_timeout: float = 8.0):
        self.base = base
        self.console_root = base / "console"
        (self.console_root / "web_console").mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / "web_console" / "index.html",
                    self.console_root / "web_console" / "index.html")
        self.data_dir = data_dir if data_dir is not None else base / "console-data"
        self.runtime_a = base / "runtime-a"
        self.runtime_b = base / "runtime-b"
        write_stub_runtime(self.runtime_a, MARKER_A)
        write_stub_runtime(self.runtime_b, MARKER_B)
        self.server = wcs.WebConsoleServer.create(
            host="127.0.0.1", port=0, runtime_root=self.runtime_a,
            console_root=self.console_root, data_dir=self.data_dir,
            control_timeout=control_timeout)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        self.server.write_instance_metadata()

    @property
    def port(self) -> int:
        return self.server.server_address[1]

    def request(self, method: str = "GET", path: str = "/", body=None,
                raw_body: bytes | None = None, host: str | None = None,
                timeout: float = 30.0):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=timeout)
        try:
            headers = {} if host is None else {"Host": host}
            payload = None
            if raw_body is not None:
                payload = raw_body
                headers["Content-Type"] = "application/json; charset=utf-8"
            elif body is not None:
                payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
                headers["Content-Type"] = "application/json; charset=utf-8"
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type") or ""
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                parsed = None
            return response.status, content_type, parsed, raw
        finally:
            conn.close()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class RegistryHttpTests(Base):
    """HTTP-level Registry routes and opaque-ID status routing."""

    def setUp(self):
        super().setUp()
        self.wcreg = importlib.import_module("web_console_registry")
        self.fixture = RegistryHttpFixture(self.base)
        self.addCleanup(self.fixture.close)

    def add_runtime(self, root: Path, label: str):
        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", body={"root": str(root), "label": label})
        self.assertEqual(status, 201, payload)
        self.assertTrue(payload["ok"])
        return payload["runtime"]

    def test_full_crud_cycle_over_http(self):
        entry = self.add_runtime(self.fixture.runtime_a, "Alpha Café")
        self.assertRegex(entry["id"], ID_PATTERN)
        self.assertTrue(entry["validation"]["ok"])

        status, _, payload, _ = self.fixture.request("GET", "/api/runtimes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["runtimes"][0]["id"], entry["id"])

        status, _, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{entry['id']}/rename",
            body={"label": "renamed-α"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["runtime"]["label"], "renamed-α")

        status, _, payload, _ = self.fixture.request(
            "POST", f"/api/runtimes/{entry['id']}/revalidate", body={})
        self.assertEqual(status, 200)
        self.assertTrue(payload["runtime"]["validation"]["ok"])

        status, _, payload, _ = self.fixture.request(
            "GET", f"/api/runtimes/{entry['id']}")
        self.assertEqual(status, 200)
        self.assertEqual(payload["runtime"]["label"], "renamed-α")

        status, _, payload, _ = self.fixture.request(
            "DELETE", f"/api/runtimes/{entry['id']}")
        self.assertEqual(status, 200)
        status, _, payload, _ = self.fixture.request("GET", "/api/runtimes")
        self.assertEqual(payload["count"], 0)

    def test_add_validation_failures_are_machine_readable(self):
        cases = [
            ({"root": str(self.base / "missing"), "label": "x"},
             400, "REGISTRY_ROOT_NOT_FOUND"),
            ({"root": str(self.base), "label": ""}, 400, "REGISTRY_INVALID_LABEL"),
            ({"root": "relative/path", "label": "x"}, 400, "REGISTRY_INVALID_ROOT"),
            ({"label": "no root"}, 400, "REGISTRY_INVALID_PAYLOAD"),
            ({"root": str(self.fixture.runtime_a)}, 400, "REGISTRY_INVALID_PAYLOAD"),
            ({"root": str(self.fixture.runtime_a), "label": "x", "extra": 1},
             400, "REGISTRY_INVALID_PAYLOAD"),
            ({"root": 123, "label": "x"}, 400, "REGISTRY_INVALID_PAYLOAD"),
        ]
        for body, expected_status, expected_code in cases:
            status, _, payload, _ = self.fixture.request(
                "POST", "/api/runtimes", body=body)
            self.assertEqual(status, expected_status, body)
            self.assertEqual(payload["error"]["code"], expected_code, body)
        status, _, payload, _ = self.fixture.request("GET", "/api/runtimes")
        self.assertEqual(payload["count"], 0)

    def test_duplicate_root_over_http_conflicts(self):
        self.add_runtime(self.fixture.runtime_a, "first")
        for variant in (str(self.fixture.runtime_a) + "/",
                        str(self.fixture.runtime_a).upper()):
            status, _, payload, _ = self.fixture.request(
                "POST", "/api/runtimes",
                body={"root": variant, "label": "alias"})
            self.assertEqual(status, 409, variant)
            self.assertEqual(payload["error"]["code"], "REGISTRY_DUPLICATE_ROOT")
        status, _, payload, _ = self.fixture.request("GET", "/api/runtimes")
        self.assertEqual(payload["count"], 1)

    def test_payload_bounds_and_encoding_fail_closed(self):
        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", raw_body=b"{not json")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "REGISTRY_INVALID_JSON")

        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", raw_body=b'["array"]')
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "REGISTRY_INVALID_PAYLOAD")

        oversized = json.dumps(
            {"root": str(self.fixture.runtime_a), "label": "x" * 70000},
            ensure_ascii=False).encode("utf-8")
        self.assertGreater(len(oversized), 65536)
        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", raw_body=oversized)
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "PAYLOAD_TOO_LARGE")

        # A valid-JSON body larger than the cap is still rejected.
        big_ok = json.dumps(
            {"root": str(self.fixture.runtime_a),
             "label": "x" * 120, "pad": "y" * 70000}).encode("utf-8")
        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", raw_body=big_ok)
        self.assertEqual(status, 413)

        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", body={})
        self.assertEqual(status, 400)
        self.assertIn(payload["error"]["code"],
                      ("REGISTRY_INVALID_PAYLOAD", "REGISTRY_INVALID_ROOT"))

    def test_unicode_label_over_http_is_utf8_safe(self):
        label = "中文 — café 🚀"
        entry = self.add_runtime(self.fixture.runtime_a, label)
        status, content_type, payload, raw = self.fixture.request(
            "GET", f"/api/runtimes/{entry['id']}")
        self.assertEqual(status, 200)
        self.assertIn("charset=utf-8", content_type)
        self.assertEqual(payload["runtime"]["label"], label)
        self.assertIn(label.encode("utf-8"), raw)

    def test_corrupt_registry_storage_over_http(self):
        self.add_runtime(self.fixture.runtime_a, "A")
        path = self.fixture.data_dir / self.wcreg.REGISTRY_FILE_NAME
        good = path.read_bytes()
        path.write_bytes(b"{corrupt")
        for method, path_url, body in (
                ("GET", "/api/runtimes", None),
                ("POST", "/api/runtimes",
                 {"root": str(self.fixture.runtime_b), "label": "B"}),
                ("POST", "/api/runtimes/0123456789abcdef/rename",
                 {"label": "x"})):
            status, _, payload, _ = self.fixture.request(method, path_url,
                                                         body=body)
            self.assertEqual(status, 500, (method, path_url))
            self.assertEqual(payload["error"]["code"], "REGISTRY_CORRUPT")
        self.assertEqual(path.read_bytes(), b"{corrupt")  # never reset
        path.write_bytes(good)
        status, _, payload, _ = self.fixture.request("GET", "/api/runtimes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)

    def test_registry_survives_server_restart(self):
        entry = self.add_runtime(self.fixture.runtime_a, "persists")
        self.fixture.close()
        reopened = RegistryHttpFixture(self.base, data_dir=self.fixture.data_dir)
        self.addCleanup(reopened.close)
        status, _, payload, _ = reopened.request("GET", "/api/runtimes")
        self.assertEqual(status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["runtimes"][0]["id"], entry["id"])
        self.assertEqual(payload["runtimes"][0]["label"], "persists")
        status, _, payload, _ = reopened.request(
            "GET", f"/api/runtimes/{entry['id']}/status")
        self.assertEqual(status, 200)
        self.assertEqual(payload["control_plane"]["status"]["PROJECT_ID"],
                         MARKER_A)


class TwoRootIsolationHttpTests(Base):
    """Adversarial two-root proof for the opaque-ID status route."""

    def setUp(self):
        super().setUp()
        self.wcreg = importlib.import_module("web_console_registry")
        self.fixture = RegistryHttpFixture(self.base)
        self.addCleanup(self.fixture.close)
        self.entry_a = self.add_runtime(self.fixture.runtime_a, "Root A")
        self.entry_b = self.add_runtime(self.fixture.runtime_b, "Root B")
        self.hash_a = tree_hash(self.fixture.runtime_a)
        self.hash_b = tree_hash(self.fixture.runtime_b)

    def add_runtime(self, root: Path, label: str):
        status, _, payload, _ = self.fixture.request(
            "POST", "/api/runtimes", body={"root": str(root), "label": label})
        self.assertEqual(status, 201, payload)
        return payload["runtime"]

    def status_of(self, runtime_id: str, suffix: str = "/status"):
        return self.fixture.request("GET", f"/api/runtimes/{runtime_id}{suffix}")

    def test_each_id_returns_exactly_its_own_root_payload(self):
        for entry, marker in ((self.entry_a, MARKER_A),
                              (self.entry_b, MARKER_B)):
            status, _, payload, raw = self.status_of(entry["id"])
            self.assertEqual(status, 200)
            self.assertEqual(payload["runtime"]["id"], entry["id"])
            self.assertEqual(payload["control_plane"]["status"]["PROJECT_ID"],
                             marker)
            self.assertEqual(
                payload["control_plane"]["status"]["runtime_status"],
                marker + "-STATUS")
            self.assertNotIn(MARKER_A.encode("utf-8"), raw) if marker is MARKER_B \
                else self.assertNotIn(MARKER_B.encode("utf-8"), raw)
            self.assertEqual(
                Path(payload["control_plane"]["runtime_root"]).resolve(),
                (self.fixture.runtime_a if marker is MARKER_A
                 else self.fixture.runtime_b).resolve())

    def test_unknown_or_malformed_ids_fail_closed(self):
        adversarial_ids = [
            "0123456789abcdeff",            # wrong length
            "0123456789ABCDEF",             # uppercase hex
            "zzzzzzzzzzzzzzzz",             # non-hex
            self.entry_a["id"] + "0",       # near-miss
            "..%2F..%2Fruntime-b",          # encoded traversal
            "..\\..\\runtime-b",            # backstone traversal
            "../runtime-b",                 # traversal
            "C:\\Windows",                  # absolute path
            "C:/Windows",                   # absolute path
            "\\\\server\\share",            # UNC path
            self.entry_a["id"] + "/../../runtime-b",
            self.entry_a["id"] + "\\..\\runtime-b",
            "%2e%2e%2fruntime-b",
            urllib.parse.quote("你的runtime"),   # non-ASCII id, percent-encoded
            "",
            "%20" + self.entry_a["id"],          # encoded whitespace prefix
        ]
        for bad in adversarial_ids:
            for suffix in ("", "/status", "/rename", "/revalidate"):
                url = f"/api/runtimes/{bad}{suffix}"
                status, _, payload, _ = self.fixture.request("GET", url)
                self.assertEqual(status, 404, url)
                self.assertFalse(payload["ok"])
                self.assertIn(payload["error"]["code"],
                              ("ROUTE_NOT_FOUND", "RUNTIME_UNKNOWN"), url)
                self.assertNotIn(b"ROOT-B-MARKER", (json.dumps(payload)
                                                    if payload else b"").encode())

    def test_rename_and_revalidate_ignore_path_like_payload_fields(self):
        # An ID plus a foreign root in the body must fail closed, not rebind.
        for action, body in (("rename", {"label": "x", "root": "C:\\Windows"}),
                             ("rename", {"label": "x", "id": self.entry_b["id"]}),
                             ("revalidate", {"root": str(self.fixture.runtime_b)}),
                             ("revalidate", {"id": self.entry_b["id"]})):
            status, _, payload, _ = self.fixture.request(
                "POST",
                f"/api/runtimes/{self.entry_a['id']}/{action}", body=body)
            self.assertEqual(status, 400, (action, body))
            self.assertEqual(payload["error"]["code"],
                             "REGISTRY_INVALID_PAYLOAD")
        status, _, payload, _ = self.status_of(self.entry_a["id"])
        self.assertEqual(payload["control_plane"]["status"]["PROJECT_ID"],
                         MARKER_A)

    def test_query_parameters_cannot_rebind_the_root(self):
        probes = [
            f"/api/runtimes/{self.entry_a['id']}/status"
            f"?root={self.fixture.runtime_b.as_posix()}",
            f"/api/runtimes/{self.entry_a['id']}/status?id={self.entry_b['id']}",
            f"/api/runtimes/{self.entry_a['id']}?root={self.fixture.runtime_b.as_posix()}",
            f"/api/status?root={self.fixture.runtime_b.as_posix()}",
        ]
        for url in probes:
            status, _, payload, _ = self.fixture.request("GET", url)
            self.assertEqual(status, 200, url)
            self.assertNotIn("ROOT-B-MARKER",
                             json.dumps(payload, ensure_ascii=True), url)
        # The legacy P1 status route stays bound to the server-configured root.
        status, _, payload, _ = self.fixture.request(
            "GET", f"/api/status?root={self.fixture.runtime_b.as_posix()}")
        self.assertEqual(payload["control_plane"]["runtime_root"],
                         str(self.fixture.runtime_a))

    def test_remove_is_scoped_and_roots_stay_byte_identical(self):
        status, _, payload, _ = self.fixture.request(
            "DELETE", f"/api/runtimes/{self.entry_a['id']}")
        self.assertEqual(status, 200)
        status, _, payload, _ = self.status_of(self.entry_a["id"])
        self.assertEqual(status, 404)
        status, _, payload, raw = self.status_of(self.entry_b["id"])
        self.assertEqual(status, 200)
        self.assertEqual(payload["control_plane"]["status"]["PROJECT_ID"],
                         MARKER_B)
        self.assertEqual(tree_hash(self.fixture.runtime_a), self.hash_a)
        self.assertEqual(tree_hash(self.fixture.runtime_b), self.hash_b)

    def test_p1_surface_unchanged_alongside_registry(self):
        for method, path in (("POST", "/api/runtimes/0123456789abcdef"),
                             ("PUT", "/api/runtimes"),
                             ("PATCH", "/api/runtimes"),
                             ("DELETE", "/api/health"),
                             ("DELETE", "/api/status"),
                             ("DELETE", "/"),
                             ("POST", "/api/health"),
                             ("GET", "/api/runtimes/0123456789abcdef/rename")):
            status, _, payload, _ = self.fixture.request(method, path)
            self.assertEqual(status, 405, (method, path))
        status, _, payload, _ = self.fixture.request("GET", "/api/health")
        self.assertEqual(status, 200)
        status, _, payload, _ = self.fixture.request("GET", "/api/status")
        self.assertEqual(status, 200)


if __name__ == "__main__":
    unittest.main()
