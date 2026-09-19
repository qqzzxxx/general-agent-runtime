"""Phase 5 real dispatch/fencing and renderer checks; no model or live ZCode run."""
import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import subprocess
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

import executor_capabilities as cap
import executor_claim as claim
import executor_completion as completion
import executor_contract as contract
import executor_entry as entry
import executor_fetch as fetch
import executor_inspect as inspect
import executor_work as work
import supervisor_control as sc
import test_executor_contract as fixtures

URL = "https://example.com/"
HTML = '<!doctype html><title>Sign up</title><style>form{width:720px}input{height:44px}</style><form><input id="email" type="email"><button>Go</button></form>'


def fields(browser=False, network=False):
    execution = {"autonomy": "HIGH", "capabilities": {"browser": "render" if browser else "none",
                 "network": "https_get" if network else "none"}}
    if network:
        execution["network_urls"] = [URL]
    return {**fixtures.task_fields(), "EXECUTION": execution}


def fake_fetch():
    return {"ok": True, "status_code": 200, "content_type": "text/plain", "body": base64.b64encode(b"source").decode()}


class CapabilityTests(unittest.TestCase):
    setUp = fixtures.ExecutorContractTests.setUp
    dispatch = fixtures.ExecutorContractTests.dispatch
    start = fixtures.ExecutorContractTests.start
    do = fixtures.ExecutorContractTests.do
    timeout = fixtures.ExecutorContractTests.timeout

    def test_policy_distinctions_and_autonomy_are_independent(self):
        views = []
        for level in ("LOW", "NORMAL", "HIGH"):
            task = fields(True, True)
            task["EXECUTION"]["autonomy"] = level
            views.append(contract.project(task)["capabilities"])
        self.assertEqual(views[0], views[1])
        self.assertEqual(views[1], views[2])
        self.assertEqual(views[0]["filesystem"]["assurance"], "runtime_enforced")
        self.assertEqual(views[0]["network"]["assurance"], "runtime_enforced")
        self.assertEqual(views[0]["browser"]["assurance"], "trusted_host")
        self.assertEqual(views[0]["gui"]["assurance"], "cooperative_unverified")
        inventory = cap.host_inventory()["zcode"]
        self.assertEqual(inventory["assurance"], "cooperative_unverified")
        self.assertEqual(inventory["task_work_authorized"], "requires_READY_task")

    def test_unknown_guarantees_modes_and_urls_fail_closed(self):
        for policy in ({"capabilities": {"shell": "sandboxed"}}, {"capabilities": {"gui": "trusted"}},
                       {"capabilities": {"browser": "sandboxed"}}, {"capabilities": {"network": "https_get"}},
                       {"capabilities": {"network": "none"}, "network_urls": [URL]}, {"host_attestation": "trusted"},
                       {"capabilities": {"browser": "render", "filesystem": "read"}}):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                contract.validate_execution(policy)
        for url in ("http://example.com/", "file:///etc/passwd", "https://a@b/", "https://a:444/",
                    "https://a/#x", "https://a/\r\nx", "https://a\\b/", "https://a./", "https://é.com/"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                cap.validate_url(url)

    def test_missing_optional_browser_does_not_prevent_entry(self):
        identity = self.dispatch(fields=fields(True))
        with patch.object(cap, "browser_dependencies", return_value={"node": None, "browser": None}):
            ready = entry.enter(self.root, contract_version=2)
        self.assertEqual(ready["status"], "READY")
        self.assertTrue(claim.claim_dir(self.root, identity["MESSAGE_ID"], identity["NONCE"]).exists())

    def test_not_granted_never_launches_worker(self):
        self.start(fields())
        with patch.object(inspect, "run_worker") as worker:
            self.assertEqual(self.do("fetch", url=URL)["action"], "STOP")
            self.assertEqual(self.do("render", area="work", path="workspace/x.html", width=375,
                                     height=812, screenshot="evidence/x.png")["action"], "STOP")
        worker.assert_not_called()

    def test_fetch_exact_scope_and_content_hash(self):
        self.start(fields(network=True))
        with patch.object(inspect, "run_worker", return_value=fake_fetch()) as worker:
            for url in (URL + "different", "https://other.example/", URL + "?data=x"):
                self.assertEqual(self.do("fetch", url=url)["action"], "STOP")
            worker.assert_not_called()
            result = self.do("fetch", url=URL)
            self.assertEqual(result["observation"]["sha256"], hashlib.sha256(b"source").hexdigest())
            self.assertNotIn(self.session, json.dumps(worker.call_args.args))
        self.assertEqual(result["status"], "OK")
        self.assertEqual(self.do("finish", result=fixtures.semantic("PARTIAL"))["status"], "FINISHED")

    def test_inflight_retirement_discards_success_and_failure(self):
        self.start(fields(network=True))
        entered = threading.Event()
        release = threading.Event()
        def slow(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return fake_fetch()
        results = []
        with patch.object(inspect, "run_worker", side_effect=slow):
            worker = threading.Thread(target=lambda: results.append(self.do("fetch", url=URL)))
            worker.start()
            self.assertTrue(entered.wait(5))
            # This completes before worker release: external IO does not hold the mutex.
            sc.submit_intervention(self.root, b"stop", interrupt_current=True)
            release.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results[0]["status"], "NOT_AUTHORIZED")
        self.assertNotIn("observation", results[0])

    def test_safe_pause_allows_bounded_read_and_failed_worker_is_honest(self):
        self.start(fields(network=True))
        sc.set_pause(self.root)
        with patch.object(inspect, "run_worker", side_effect=work.Unavailable("DNS failed")):
            self.assertEqual(self.do("fetch", url=URL)["status"], "UNAVAILABLE")
        def revoked_failure(*args):
            sc.submit_intervention(self.root, b"stop", interrupt_current=True)
            raise work.Unavailable("DNS failed")
        with patch.object(inspect, "run_worker", side_effect=revoked_failure):
            self.assertEqual(self.do("fetch", url=URL)["status"], "NOT_AUTHORIZED")

    def test_finish_started_closes_inspections(self):
        self.start(fields(network=True))
        original = completion._atomic_create
        def create(path, content):
            original(path, content)
            if path.parent.name == "executor_finishes":
                raise SystemExit("validated plan is durable")
        with patch.object(completion, "_atomic_create", side_effect=create):
            with self.assertRaises(SystemExit):
                self.do("finish", result=fixtures.semantic("PARTIAL"))
        with patch.object(inspect, "run_worker") as worker:
            self.assertEqual(self.do("fetch", url=URL)["action"], "STOP")
            worker.assert_not_called()

    def test_fetch_redacts_encoded_session_and_rejects_oversized_provider_body(self):
        self.start(fields(network=True))
        for body in (self.session.encode(), b"x" * (cap.MAX_FETCH_BYTES + 1)):
            observed = {**fake_fetch(), "body": base64.b64encode(body).decode()}
            with patch.object(inspect, "run_worker", return_value=observed):
                result = self.do("fetch", url=URL)
            self.assertNotEqual(result["status"], "OK")
            self.assertNotIn("observation", result)
            self.assertNotIn(self.session, json.dumps(result))

    def test_render_revocation_never_retains_screenshot(self):
        with patch.object(cap, "browser_dependencies", return_value={"node": "node", "browser": "browser"}):
            self.start(fields(True))
            self.do("write", path="workspace/form.html", text=HTML)
            def revoked(*args):
                sc.submit_intervention(self.root, b"stop", interrupt_current=True)
                return {"ok": True, "png": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode()}
            with patch.object(inspect, "run_worker", side_effect=revoked):
                result = self.do("render", area="work", path="workspace/form.html", width=375, height=812,
                                 screenshot="evidence/revoked.png")
            self.assertEqual(result["status"], "NOT_AUTHORIZED")
            self.assertFalse((self.work / "evidence/revoked.png").exists())

    def test_render_paths_viewport_and_changes_discard_evidence(self):
        with patch.object(cap, "browser_dependencies", return_value={"node": "node", "browser": "browser"}):
            self.start(fields(True))
            self.do("write", path="workspace/form.html", text=HTML)
            args = dict(area="work", path="workspace/form.html", width=375, height=812, screenshot="evidence/check.png")
            with patch.object(inspect, "run_worker") as worker:
                for extra in ({"path": "../x"}, {"screenshot": "workspace/x.png"}, {"width": True},
                              {"width": 10000}, {"area": "project", "path": "control/x"}):
                    self.assertEqual(self.do("render", **{**args, **extra})["action"], "STOP")
                worker.assert_not_called()
            def changed(*argv):
                self.do("write", path="workspace/form.html", text="Changed")
                return {"ok": True}
            with patch.object(inspect, "run_worker", side_effect=changed):
                self.assertEqual(self.do("render", **args)["status"], "UNAVAILABLE")
            self.assertFalse((self.work / "evidence/check.png").exists())

    def test_lost_optional_provider_does_not_revoke_other_tools_but_expiry_does(self):
        with patch.object(cap, "browser_dependencies", return_value={"node": "node", "browser": "browser"}):
            self.start(fields(True, True))
        with patch.object(cap, "browser_dependencies", return_value={"node": None, "browser": None}), \
                patch.object(inspect, "run_worker", return_value=fake_fetch()) as worker:
            self.assertEqual(self.do("fetch", url=URL)["status"], "OK")
            worker.assert_called_once()
        self.timeout()
        with patch.object(inspect, "run_worker") as worker:
            self.assertEqual(self.do("fetch", url=URL)["status"], "NOT_AUTHORIZED")
            worker.assert_not_called()

    @unittest.skipUnless(all(cap.browser_dependencies().values()), "installed Node/Chromium required")
    def test_real_render_detects_mobile_defect_then_publication(self):
        self.start(fields(True))
        self.do("write", path="workspace/form.html", text=HTML)
        args = dict(area="work", path="workspace/form.html", width=375, height=812)
        first = self.do("render", **args, screenshot="evidence/before.png")
        self.assertEqual(first["status"], "OK", first)
        self.assertTrue(first["observation"]["horizontal_overflow"])
        revised = HTML.replace("width:720px", "width:100%;max-width:460px").replace('<input id="email"',
                   '<label for="email">Email address</label><input id="email"').replace('>Go</button>', '>Create account</button>')
        self.do("write", path="workspace/form.html", text=revised)
        last = self.do("render", **args, screenshot="evidence/after.png")
        self.assertEqual(last["status"], "OK", last)
        self.assertFalse(last["observation"]["horizontal_overflow"])
        self.assertEqual(last["observation"]["controls"][0]["labels"], ["Email address"])
        self.assertEqual(last["observation"]["source_sha256"], hashlib.sha256(revised.encode()).hexdigest())
        self.assertEqual(self.do("render", **args, screenshot="evidence/after.png")["action"], "STOP")
        result = fixtures.semantic("COMPLETED", [{"path": p, "role": role} for p, role in
                    (("workspace/form.html", "deliverable"), ("evidence/before.png", "evidence"), ("evidence/after.png", "evidence"))])
        result["limitations"] = ["Static render; no functional submission or automatic visual judgment."]
        self.assertEqual(self.do("finish", result=result)["status"], "FINISHED")
        record = completion.lookup_entries(self.root, self.identity["MESSAGE_ID"])[0]
        self.assertTrue(completion.entry_hashes_intact(record))

    @unittest.skipUnless(all(cap.browser_dependencies().values()), "installed Node/Chromium required")
    def test_real_renderer_blocks_page_scripts_and_external_resources(self):
        self.start(fields(True))
        requests = []
        class Canary(BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append(self.path)
                self.send_response(200)
                self.end_headers()
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Canary)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(lambda: thread.join(2))
        self.addCleanup(server.shutdown)
        canary_url = f"http://127.0.0.1:{server.server_port}/"
        html = '<title>Safe</title><script>document.title="EXECUTED";fetch("http://127.0.0.1:1/")</script>' \
               '<iframe src="file:///C:/Windows/win.ini"></iframe><img src="http://127.0.0.1:1/x">' \
               '<style>@import "http://127.0.0.1:1/style.css";</style><p>Local</p>'
        html = html.replace("http://127.0.0.1:1/", canary_url)
        self.do("write", path="workspace/form.html", text=html)
        result = self.do("render", area="work", path="workspace/form.html", width=375, height=812,
                         screenshot="evidence/blocked.png")
        self.assertEqual(result["status"], "OK", result)
        self.assertEqual(result["observation"]["title"], "Safe")
        self.assertEqual(result["observation"]["script_elements"], 1)
        self.assertNotIn("[fonts]", result["observation"]["text"])
        self.assertEqual(requests, [], "static render reached the local canary")


class FetchWorkerTests(unittest.TestCase):
    def test_public_dns_rejects_private_mixed_and_mapped_addresses(self):
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:8.8.8.8", "224.0.0.1", "0.0.0.0",
                        "64:ff9b::a00:1", "2002:7f00:1::1", "2001::1"):
            rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), patch.object(socket, "getaddrinfo", return_value=rows), self.assertRaises(ValueError):
                fetch.public_addresses("example.com")

    def test_connection_pins_ip_tls_hostname_and_never_redirects(self):
        address = ("93.184.215.14", 443)
        conn, raw, ctx = MagicMock(), MagicMock(), MagicMock()
        reply = conn.getresponse.return_value
        reply.status = 302
        with patch.object(fetch, "public_addresses", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", address)]), \
                patch.object(fetch.socket, "socket", return_value=raw), \
                patch.object(fetch.ssl, "create_default_context", return_value=ctx), \
                patch.object(fetch.http.client, "HTTPSConnection", return_value=conn):
            with self.assertRaisesRegex(ValueError, "redirects"):
                fetch.fetch(URL)
        raw.connect.assert_called_once_with(address)
        ctx.wrap_socket.assert_called_once_with(raw, server_hostname="example.com")
        conn.request.assert_called_once()
        self.assertEqual(conn.request.call_args.args, ("GET", "/"))
        conn.close.assert_called_once()

    def test_fetch_body_size_compression_and_non_utf8_fail_closed(self):
        for encoding, body in (("gzip", b"body"), ("identity", b"x" * (fetch.MAX_BYTES + 1)), ("identity", b"\xff")):
            conn = MagicMock()
            reply = conn.getresponse.return_value
            reply.status = 200
            reply.getheader.side_effect = lambda name, default="": encoding if name == "Content-Encoding" else "text/plain"
            reply.read.return_value = body
            with patch.object(fetch, "public_addresses", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]), \
                    patch.object(fetch.socket, "socket"), patch.object(fetch.ssl, "create_default_context"), \
                    patch.object(fetch.http.client, "HTTPSConnection", return_value=conn), self.assertRaises(ValueError):
                fetch.fetch(URL)
            conn.close.assert_called_once()

    def test_source_controls_alone_do_not_establish_rendered_quality(self):
        import measure_capability_realization as measure
        self.assertTrue(all(measure.FormChecks.inspect(measure.DRAFT).values()))
        self.assertTrue(all(measure.FormChecks.inspect(measure.REVISED).values()))
        self.assertNotEqual(measure.DRAFT, measure.REVISED)

    def test_worker_timeout_and_malformed_output_are_unavailable(self):
        with self.assertRaises(work.Unavailable):
            inspect.run_worker([sys.executable, "-I", "-c", "import time;time.sleep(20)"], {}, .1, 100)
        with self.assertRaises(work.Unavailable):
            inspect.run_worker([sys.executable, "-I", "-c", "print('not json')"], {}, 5, 100)


if __name__ == "__main__":
    unittest.main()
