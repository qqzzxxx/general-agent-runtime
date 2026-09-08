"""Synthetic stale-worker reproduction; never contacts ZCode or production data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import executor_claim as claim
import executor_completion as completion
import executor_fence as fence
from test_completion_seal import load_orchestrator, wire


class ExecutorFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="stale-fence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.o = load_orchestrator(self.root)
        self.project = self.root / "projects" / "synthetic"
        self.project.mkdir(parents=True)
        self.o.atomic_json(self.o.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": "synthetic", "project_root": "projects/synthetic"})
        self.o.ACTIVE_PROJECT = {"project_id": "synthetic", "project_root": "projects/synthetic"}
        self.o.PROJECT_STATE = self.project / "project_state.json"
        self.tokens = {}
        self.runtime = {"status": "RUNNING", "last_consumed_message_id": 700109,
                        "retired_message_ids": []}

    def dispatch(self, message=700110, attempt=1, *, issued_at="current"):
        identity = dict(MESSAGE_ID=message, TASK_ID="U1-U3", STAGE_ID=f"BUILD-{attempt}",
                        ATTEMPT=attempt, NONCE=f"synthetic-nonce-{message}")
        task = {**identity, "OBJECTIVE": "synthetic U1-U3", "OUTPUTS": [],
                "MAX_TIME": 900, "ISSUED_AT": self.o.stamp(), "SCHEDULER_GRACE_SECONDS": 3600}
        if issued_at is None:
            task.pop("ISSUED_AT")
        elif issued_at != "current":
            task["ISSUED_AT"] = issued_at
        self.state = {"project_id": "synthetic", "status": "WAITING_EXECUTOR", "current_task": task}
        self.o.atomic_json(self.o.PROJECT_STATE, self.state)
        self.o.atomic_write(self.o.TO_ZCODE, wire(task))
        self.o.register_dispatched_task(self.runtime, self.state)
        return identity

    def acquire(self, identity):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = claim.acquire(self.root, *[identity[k] for k in fence.IDENTITY_KEYS])
        if code == 0:
            self.assertEqual(output.getvalue().count('claim_token='), 1)
            self.tokens[identity['MESSAGE_ID']] = output.getvalue().split('claim_token=')[1].strip()
        return code

    def check(self, identity):
        return fence.check(self.root, identity, claim_token=self.tokens[identity['MESSAGE_ID']])

    def prepare(self, identity):
        return fence.prepare(self.root, identity, claim_token=self.tokens[identity['MESSAGE_ID']])

    def publish(self, identity, relative, digest):
        return fence.publish(self.root, identity, relative, digest, claim_token=self.tokens[identity['MESSAGE_ID']])

    def commit(self, identity, staging):
        return completion.commit(self.root, staging, claim_token=self.tokens[identity['MESSAGE_ID']])

    def candidate(self, identity, text, relative="workspace/U1.txt"):
        work = self.prepare(identity)
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def timeout(self):
        expiry = datetime.fromisoformat(self.runtime["authorized_dispatch"]["EXPIRES_AT"])
        with patch.object(self.o, "utc_now", return_value=expiry + timedelta(seconds=1)):
            return self.o.executor_timeout_event(self.runtime, self.state)

    def staging(self, identity, manifests=None):
        path = self.project / "completion_staging" / str(identity["MESSAGE_ID"])
        path.mkdir(parents=True, exist_ok=True)
        self.o.atomic_json(path / "staging.json", {
            "COMPLETION_STAGING_SCHEMA_VERSION": 1, **identity, "PROJECT_ID": "synthetic",
            "STATUS": "STAGING_READY", "CREATED_AT": self.o.stamp(),
            "RECEIPT": {**identity, "STATUS": "COMPLETED"}, "DELIVERABLES": manifests or [],
        })
        return path

    def test_700110_timeout_700111_stale_worker_resumes(self):
        old = self.dispatch()
        self.assertEqual(self.acquire(old), 0)
        old_files = [self.candidate(old, "attempt 1", f"workspace/U{i}.txt") for i in range(1, 4)]
        old_claim = claim.claim_dir(self.root, 700110, old["NONCE"]) / "claim.json"
        claim_bytes = old_claim.read_bytes()
        event = self.timeout()
        self.assertEqual(event["message_id"], 700110)
        persisted = completion.read_runtime_state(self.root)
        self.assertIn(700110, persisted["retired_message_ids"])
        self.assertEqual(persisted["executor_retirements"][0]["REASON"], "EXECUTOR_TIMEOUT")
        # Refuse even in the interval before the Supervisor dispatches a retry.
        with self.assertRaises(fence.FenceError):
            self.check(old)
        new = self.dispatch(700111, 2)
        self.assertEqual(self.acquire(new), 0)
        self.assertEqual(self.acquire(new), claim.EXIT_CLAIM_EXISTS)
        manifests = []
        for i, (old_path, old_digest) in enumerate(old_files, 1):
            relative = f"workspace/U{i}.txt"
            _, digest = self.candidate(new, "attempt 2", relative)
            canonical = self.publish(new, relative, digest)
            old_path.write_text("stale resumed damage", encoding="utf-8")
            with self.assertRaises(fence.FenceError):
                self.publish(old, relative, old_digest)
            self.assertEqual(canonical.read_text(), "attempt 2")
            manifests.append({"path": relative, "sha256": digest})
        with self.assertRaises(completion.CompletionError) as rejected:
            self.commit(old, self.staging(old))
        self.assertEqual(rejected.exception.code, completion.EXIT_NOT_AUTHORIZED)
        self.assertEqual(self.commit(new, self.staging(new, manifests)), 0)
        with self.assertRaises(fence.FenceError):
            self.publish(new, "workspace/U1.txt", manifests[0]["sha256"])
        self.assertEqual(old_claim.read_bytes(), claim_bytes)
        self.assertEqual(len(list((self.root / "handoff/executor_claims").glob("*.claim"))), 2)
        self.assertFalse(completion.lookup_entries(self.root, 700110))

    def test_checkpoint_is_not_a_reusable_write_token(self):
        old = self.dispatch()
        self.acquire(old)
        _, digest = self.candidate(old, "stale")
        self.check(old)
        self.dispatch(700111, 2)  # supersession without a timeout
        with self.assertRaises(fence.FenceError):
            self.publish(old, "workspace/U1.txt", digest)
        self.assertFalse((self.project / "workspace/U1.txt").exists())
        self.assertEqual(self.runtime["executor_retirements"][0]["REASON"], "SUPERSEDED")

    def test_old_owner_cannot_adopt_new_claim_or_regenerate_token(self):
        old = self.dispatch()
        self.acquire(old)
        new = self.dispatch(700111, 2)
        self.acquire(new)
        for token in (None, self.tokens[700110]):
            with self.assertRaisesRegex(fence.FenceError, "token_mismatch"):
                fence.check(self.root, new, claim_token=token)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = claim.acquire(self.root, *[new[k] for k in fence.IDENTITY_KEYS])
        self.assertEqual(code, claim.EXIT_CLAIM_EXISTS)
        self.assertNotIn("claim_token=", output.getvalue())
        metadata, _ = completion.load_claim(self.root, new)
        self.assertNotIn(self.tokens[700111], json.dumps(metadata))
        self.assertEqual(metadata["CLAIM_TOKEN_SHA256"],
                         hashlib.sha256(self.tokens[700111].encode()).hexdigest())

    def test_expiry_during_snapshot_io_prevents_replace(self):
        identity = self.dispatch()
        self.acquire(identity)
        _, digest = self.candidate(identity, "candidate")
        check = fence.check_locked
        calls = []
        def expiring_check(*args, **kwargs):
            calls.append(True)
            if len(calls) == 2:
                self.runtime["authorized_dispatch"]["EXPIRES_AT"] = "2000-01-01T00:00:00+00:00"
                self.o.save_runtime(self.runtime)
            return check(*args, **kwargs)
        with patch.object(fence, "check_locked", side_effect=expiring_check):
            with self.assertRaisesRegex(fence.FenceError, "expired"):
                self.publish(identity, "workspace/U1.txt", digest)
        self.assertFalse((self.project / "workspace/U1.txt").exists())
        self.assertFalse(list((self.project / "workspace").glob("*.tmp")))

    def test_expiry_is_checked_without_watchdog_or_retry(self):
        identity = self.dispatch()
        self.acquire(identity)
        _, digest = self.candidate(identity, "candidate")
        self.runtime["authorized_dispatch"]["EXPIRES_AT"] = "2000-01-01T00:00:00+00:00"
        self.o.save_runtime(self.runtime)
        for action in (lambda: self.check(identity),
                       lambda: self.publish(identity, "workspace/U1.txt", digest)):
            with self.assertRaisesRegex(fence.FenceError, "expired"):
                action()
        with self.assertRaises(completion.CompletionError):
            self.commit(identity, self.staging(identity))

    def test_legacy_claim_cannot_be_upgraded_but_committed_result_can_recover(self):
        identity = self.dispatch()
        # Synthetic pre-upgrade Runtime authorization.
        for key in ("FENCE_VERSION", "EXPIRES_AT", "PROJECT_ID"):
            self.runtime["authorized_dispatch"].pop(key)
        self.o.save_runtime(self.runtime)
        self.assertEqual(claim.acquire(self.root, *[identity[k] for k in fence.IDENTITY_KEYS]), 0)
        with self.assertRaisesRegex(RuntimeError, "Legacy claimed"):
            self.o.register_dispatched_task(self.runtime, self.state, allow_same_identity=True)
        self.assertEqual(completion.commit(self.root, self.staging(identity)), 0)
        self.o.register_dispatched_task(self.runtime, self.state, allow_same_identity=True)
        self.assertNotIn("FENCE_VERSION", self.runtime["authorized_dispatch"])
        self.o.reconcile_completion_ledger(self.runtime, self.state)
        self.assertTrue(self.o.ZCODE_DONE.exists())

    def test_restart_preserves_expiry_and_never_reauthorizes_retirement(self):
        identity = self.dispatch()
        self.acquire(identity)
        original = dict(self.runtime["authorized_dispatch"])
        registered = self.runtime["dispatch_registered_at"]
        self.o.register_dispatched_task(self.runtime, self.state, allow_same_identity=True)
        self.assertEqual(self.runtime["authorized_dispatch"]["EXPIRES_AT"], original["EXPIRES_AT"])
        self.assertEqual(self.runtime["dispatch_registered_at"], registered)
        event = self.timeout()
        restarted = completion.read_runtime_state(self.root)
        self.assertEqual(self.o.pending_timeout_event(restarted, self.state), event)
        with self.assertRaisesRegex(RuntimeError, "retired"):
            self.o.register_dispatched_task(restarted, self.state, allow_same_identity=True)
        with self.assertRaises(fence.FenceError):
            self.check(identity)

    def test_restart_replays_timeout_through_main(self):
        self.dispatch()
        self.timeout()
        events = []
        def supervisor(runtime, reason, event):
            events.append((reason, event))
            raise KeyboardInterrupt
        with patch.object(self.o, "goal_anchor_gate", return_value=True), \
             patch.object(self.o, "invoke_codex", side_effect=supervisor), \
             patch.object(self.o, "register_dispatched_task") as register:
            self.o.main()
        self.assertEqual(events[0][0], "EXECUTOR_TIMEOUT")
        self.assertEqual(events[0][1]["message_id"], 700110)
        register.assert_not_called()

    def delayed_starter_preserves_retirement(self, issued_at):
        # B is launched against pre-dispatch R. At its ownership boundary, A
        # dispatches/claims/retires A1 then dies, leaving the old inbox in place.
        self.o.save_runtime(self.runtime)
        old_snapshot = self.o.load_runtime()
        acquire_lock, load_runtime = self.o.acquire_lock, self.o.load_runtime
        observations, retired, events = [], {}, []

        def owner_transition():
            acquire_lock()  # A owns the synthetic Runtime
            try:
                identity = self.dispatch(issued_at=issued_at)
                self.assertEqual(self.acquire(identity), 0)
                retired["identity"] = identity
                retired["expiry"] = self.runtime["authorized_dispatch"]["EXPIRES_AT"]
                retired["event"] = self.timeout()
            finally:
                self.o.release_lock()  # A dies/releases; B can now own it
            acquire_lock()

        def read_owned():
            observations.append(self.o.LOCK_FILE.exists())
            return load_runtime()

        def supervisor(runtime, reason, event):
            events.append((reason, event))
            raise KeyboardInterrupt

        with patch.object(self.o, "acquire_lock", side_effect=owner_transition), \
             patch.object(self.o, "load_runtime", side_effect=read_owned), \
             patch.object(self.o, "goal_anchor_gate", return_value=True), \
             patch.object(self.o, "invoke_codex", side_effect=supervisor), \
             patch.object(self.o.time, "sleep", side_effect=KeyboardInterrupt):
            self.assertEqual(self.o.main(), 130)
        self.assertEqual(old_snapshot["retired_message_ids"], [])
        self.assertEqual(observations, [True], "B must read R only after ownership")
        persisted = load_runtime()
        self.assertIn(700110, persisted["retired_message_ids"])
        self.assertEqual(persisted["pending_executor_timeout"], retired["event"])
        self.assertEqual(persisted["authorized_dispatch"]["EXPIRES_AT"], retired["expiry"])
        self.assertEqual(events, [("EXECUTOR_TIMEOUT", retired["event"])])
        with self.assertRaisesRegex(RuntimeError, "retired"):
            self.o.register_dispatched_task(persisted, self.state, allow_same_identity=True)
        with self.assertRaises(fence.FenceError):
            self.check(retired["identity"])
        self.assertFalse(self.o.LOCK_FILE.exists())

    def test_delayed_starter_preserves_retirement_missing_issued_at(self):
        self.delayed_starter_preserves_retirement(None)

    def test_delayed_starter_preserves_retirement_invalid_issued_at(self):
        self.delayed_starter_preserves_retirement("not-a-timestamp")

    def test_registration_races_first_claim_authorization_read(self):
        identity = self.dispatch()
        self.runtime.pop("authorized_dispatch")
        self.runtime.pop("last_dispatched_message_id")
        self.runtime.pop("last_dispatched_nonce")
        self.o.save_runtime(self.runtime)  # pre-registration R, as in the exploit
        entered, release = threading.Event(), threading.Event()
        claim_ready, registered = threading.Event(), threading.Event()
        save, read_text, runtime_lock = self.o.save_runtime, Path.read_text, fence.runtime_lock
        claiming_thread = []

        def delayed_save(runtime):
            entered.set()  # registration already holds the fence
            self.assertTrue(release.wait(5))
            save(runtime)
            registered.set()

        def possible_unlocked_read(path, *args, **kwargs):
            result = read_text(path, *args, **kwargs)
            if (claiming_thread and threading.get_ident() == claiming_thread[0]
                    and path == self.o.RUNTIME_STATE and not registered.is_set()):
                # Old acquire saved this unversioned snapshot, then verified the
                # newly registered dispatch without binding a token to its claim.
                claim_ready.set()
                self.assertTrue(registered.wait(5))
            return result

        @contextlib.contextmanager
        def observed_lock(root):
            if claiming_thread and threading.get_ident() == claiming_thread[0]:
                claim_ready.set()
            with runtime_lock(root):
                yield

        def acquire():
            claiming_thread.append(threading.get_ident())
            return self.acquire(identity)

        with ThreadPoolExecutor(2) as pool, \
             patch.object(self.o, "save_runtime", side_effect=delayed_save), \
             patch.object(Path, "read_text", possible_unlocked_read), \
             patch.object(fence, "runtime_lock", observed_lock):
            registration = pool.submit(self.o.register_dispatched_task, self.runtime, self.state)
            self.assertTrue(entered.wait(5))
            acquisition = pool.submit(acquire)
            try:
                self.assertTrue(claim_ready.wait(5))
            finally:
                release.set()
            registration.result(5)
            self.assertEqual(acquisition.result(5), 0)
        metadata, _ = completion.load_claim(self.root, identity)
        self.assertEqual(metadata["CLAIM_TOKEN_SHA256"],
                         hashlib.sha256(self.tokens[700110].encode()).hexdigest())
        self.check(identity)
        self.assertEqual(self.acquire(identity), claim.EXIT_CLAIM_EXISTS)

    def test_live_poll_recovers_commit_without_done_exactly_once(self):
        identity = self.dispatch()
        self.acquire(identity)
        staging = self.staging(identity)
        polls, events = [], []

        def polling_tick(_):
            polls.append(True)
            self.assertEqual(len(polls), 1, "live Runtime stalled on a committed ledger")
            with patch.object(completion, "publish_compatibility_artifacts", side_effect=OSError("helper died")):
                with self.assertRaises(OSError):
                    self.commit(identity, staging)
            self.assertFalse(self.o.ZCODE_DONE.exists())
            self.assertEqual(completion.lookup_entries(self.root, 700110)[0]["STATUS"], completion.STATUS_COMMITTED)

        def supervisor(runtime, reason, event):
            events.append((reason, event["message_id"]))
            self.assertEqual(completion.lookup_entries(self.root, 700110)[0]["STATUS"], completion.STATUS_CONSUMED)
            self.assertEqual(self.o.consume_executor_receipt(runtime), (False, None))
            self.o.atomic_json(self.o.PROJECT_STATE, {**self.state, "status": "STOPPED", "current_task": None})

        with patch.object(self.o, "goal_anchor_gate", return_value=True), \
             patch.object(self.o.time, "sleep", side_effect=polling_tick), \
             patch.object(self.o, "invoke_codex", side_effect=supervisor):
            self.assertEqual(self.o.main(), 5)
        self.assertEqual(events, [("EXECUTOR_RESULT_READY", 700110)])
        self.assertEqual(len(polls), 1)
        self.assertEqual(self.o.load_runtime()["last_consumed_message_id"], 700110)
        self.assertEqual(completion.lookup_entries(self.root, 700110)[0]["STATUS"], completion.STATUS_SEALED)

    def test_live_consume_waits_for_commit_compatibility_tail(self):
        identity = self.dispatch()
        self.acquire(identity)
        staging = self.staging(identity)
        entered, release, polling = threading.Event(), threading.Event(), threading.Event()
        publish = completion.publish_compatibility_artifacts

        def delayed_publish(*args, **kwargs):
            entered.set()  # ledger committed, but writer still holds the fence
            self.assertTrue(release.wait(5))
            return publish(*args, **kwargs)

        def consume():
            polling.set()
            return self.o.consume_executor_receipt(self.runtime)

        with ThreadPoolExecutor(2) as pool, \
             patch.object(completion, "publish_compatibility_artifacts", side_effect=delayed_publish):
            writer = pool.submit(self.commit, identity, staging)
            self.assertTrue(entered.wait(5))
            reader = pool.submit(consume)
            try:
                self.assertTrue(polling.wait(5))
                self.assertFalse(reader.done())
            finally:
                release.set()
            self.assertEqual(writer.result(5), 0)
            self.assertEqual(reader.result(5)[1]["message_id"], 700110)
        self.assertFalse(self.o.ZCODE_DONE.exists(), "writer must not republish after consume")
        self.assertEqual(self.o.consume_executor_receipt(self.runtime), (False, None))

    def test_staging_replacement_cannot_split_hash_and_committed_payload(self):
        identity = self.dispatch()
        self.acquire(identity)
        staging = self.staging(identity)
        path = staging / "staging.json"
        snapshot = path.read_bytes()
        replacement = json.loads(snapshot)
        replacement["RECEIPT"]["STATUS"] = "REPLACEMENT_MUST_NOT_BE_COMMITTED"
        read_bytes, reads = Path.read_bytes, []

        def replace_after_read(target):
            raw = read_bytes(target)
            if target == path:
                reads.append(raw)
                self.o.atomic_json(path, replacement)
            return raw

        with patch.object(Path, "read_bytes", replace_after_read):
            self.assertEqual(self.commit(identity, staging), 0)
        entry = completion.lookup_entries(self.root, 700110)[0]
        self.assertEqual(reads, [snapshot])
        self.assertEqual(entry["STAGING_MANIFEST_SHA256"], hashlib.sha256(snapshot).hexdigest())
        self.assertEqual(entry["RECEIPT"], json.loads(snapshot)["RECEIPT"])

    def test_staging_snapshot_invalid_encoding_or_json_fails_closed(self):
        identity = self.dispatch()
        self.acquire(identity)
        staging = self.staging(identity)
        for raw in (b"\xff", b"{broken json"):
            with self.subTest(raw=raw):
                (staging / "staging.json").write_bytes(raw)
                with self.assertRaises(completion.CompletionError) as rejected:
                    self.commit(identity, staging)
                self.assertEqual(rejected.exception.code, completion.EXIT_INVALID_STAGING)
                self.assertFalse(completion.lookup_entries(self.root, 700110))

    def test_project_lexical_ancestry_checked_before_resolution_portably(self):
        identity = self.dispatch()
        self.acquire(identity)
        lstat, resolve = Path.lstat, Path.resolve
        for link in (self.root / "projects", self.project):
            with self.subTest(link=link):
                def reparse(path, *args, **kwargs):
                    info = lstat(path, *args, **kwargs)
                    if path == link:
                        return SimpleNamespace(st_mode=info.st_mode, st_file_attributes=0x400)
                    return info

                def must_not_resolve_project(path, *args, **kwargs):
                    self.assertNotIn(path, (self.root / "projects", self.project),
                                     "resolution must not hide the original reparse ancestry")
                    return resolve(path, *args, **kwargs)

                with patch.object(Path, "lstat", reparse), \
                     patch.object(Path, "resolve", must_not_resolve_project):
                    with self.assertRaisesRegex(completion.CompletionError, "ancestry reparse"):
                        completion.resolve_active_project(self.root)
                    with self.assertRaisesRegex(RuntimeError, "ancestry reparse"):
                        self.o.load_active_project()
                    with self.assertRaisesRegex(fence.FenceError, "ancestry reparse"):
                        self.check(identity)

    @unittest.skipUnless(os.name == "nt", "native Windows junction verification")
    def test_native_windows_projects_and_project_junctions_rejected(self):
        for component in ("projects", "project"):
            with self.subTest(component=component):
                root = self.root / f"junction-{component}"
                target = root / "target"
                target.mkdir(parents=True)
                link = root / "projects" if component == "projects" else root / "projects" / "synthetic"
                link.parent.mkdir(parents=True, exist_ok=True)
                result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                                        capture_output=True, text=True)
                if result.returncode:
                    self.skipTest(f"junction creation unavailable: {result.stderr.strip()}")
                try:
                    self.o.atomic_json(root / "control/ACTIVE_PROJECT.json", {
                        "schema_version": 1, "project_id": "synthetic", "project_root": "projects/synthetic"})
                    with self.assertRaisesRegex(completion.CompletionError, "ancestry reparse"):
                        completion.resolve_active_project(root)
                    other = load_orchestrator(root)
                    with self.assertRaisesRegex(RuntimeError, "ancestry reparse"):
                        other.load_active_project()
                finally:
                    # Remove only this known junction, never recurse into its target.
                    self.assertTrue(link.absolute().is_relative_to(self.root.absolute()))
                    os.rmdir(link)
                self.assertTrue(target.is_dir())

    def test_timeout_waits_for_publication_then_fences_future_writes(self):
        identity = self.dispatch()
        self.acquire(identity)
        _, digest = self.candidate(identity, "authorized before timeout")
        entered, release, retiring = threading.Event(), threading.Event(), threading.Event()
        original_replace = os.replace
        def replace(source, target):
            if Path(target) == self.project / "workspace/U1.txt":
                entered.set()
                self.assertTrue(release.wait(5))
            return original_replace(source, target)
        def retire():
            retiring.set()
            return self.timeout()
        with ThreadPoolExecutor(2) as pool, patch.object(fence.os, "replace", side_effect=replace):
            publication = pool.submit(self.publish, identity, "workspace/U1.txt", digest)
            self.assertTrue(entered.wait(5))
            retirement = pool.submit(retire)
            self.assertTrue(retiring.wait(5))
            try:
                self.assertFalse(retirement.done())
            finally:
                release.set()
            publication.result(5)
            self.assertIsNotNone(retirement.result(5))
        with self.assertRaises(fence.FenceError):
            self.publish(identity, "workspace/U1.txt", digest)

    def test_retirement_wins_before_waiting_publication(self):
        identity = self.dispatch()
        self.acquire(identity)
        _, digest = self.candidate(identity, "must never publish")
        entered, release = threading.Event(), threading.Event()
        save = self.o.save_runtime
        def delayed_save(runtime):
            entered.set()
            self.assertTrue(release.wait(5))
            save(runtime)
        with ThreadPoolExecutor(2) as pool, patch.object(self.o, "save_runtime", side_effect=delayed_save):
            retirement = pool.submit(self.timeout)
            self.assertTrue(entered.wait(5))
            publication = pool.submit(self.publish, identity, "workspace/U1.txt", digest)
            release.set()
            retirement.result(5)
            with self.assertRaises(fence.FenceError):
                publication.result(5)
        self.assertFalse((self.project / "workspace/U1.txt").exists())

    def test_completion_winner_is_not_timed_out_even_without_wake_hint(self):
        identity = self.dispatch()
        self.acquire(identity)
        with patch.object(completion, "publish_compatibility_artifacts", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                self.commit(identity, self.staging(identity))
        self.assertIsNone(self.timeout())
        self.assertNotIn(700110, self.runtime["retired_message_ids"])
        self.o.reconcile_completion_ledger(self.runtime, self.state)
        self.assertTrue(self.o.ZCODE_DONE.exists())

    def test_wrong_identity_project_state_inbox_and_stop_fail_closed(self):
        identity = self.dispatch()
        self.acquire(identity)
        with self.assertRaises(fence.FenceError):
            self.check({**identity, "NONCE": "wrong"})
        for field, value in (("status", "HUMAN_REVIEW"), ("current_task", None)):
            changed = {**self.state, field: value}
            self.o.atomic_json(self.o.PROJECT_STATE, changed)
            with self.assertRaises(fence.FenceError):
                self.check(identity)
        self.o.atomic_json(self.o.PROJECT_STATE, self.state)
        self.o.STOP_FLAG.touch()
        with self.assertRaises(fence.FenceError):
            self.check(identity)
        self.o.STOP_FLAG.unlink()
        self.o.TO_ZCODE.write_text("corrupted inbox")
        with self.assertRaises(fence.FenceError):
            self.check(identity)
        self.o.atomic_json(self.o.ACTIVE_PROJECT_FILE, {
            "schema_version": 1, "project_id": "other", "project_root": "projects/other"})
        with self.assertRaisesRegex(fence.FenceError, "project_mismatch"):
            self.check(identity)

    def test_path_hash_and_hardlink_rejections(self):
        identity = self.dispatch()
        self.acquire(identity)
        candidate, digest = self.candidate(identity, "candidate")
        for relative in ("../U1", "workspace/../U1", "workspace\\U1", "C:/U1", "/U1",
                         "workspace/U1:stream", "workspace/CON", "workspace/name.",
                         "PROJECT_GOAL.md", "RESEARCH_STATE.md", "project_state.json",
                         "control/orchestrator_runtime.json", "reports/USER_STATUS.md"):
            with self.subTest(relative=relative), self.assertRaises(fence.FenceError):
                self.publish(identity, relative, digest)
        with self.assertRaisesRegex(fence.FenceError, "hash_mismatch"):
            self.publish(identity, "workspace/U1.txt", "0" * 64)
        os.link(candidate, candidate.with_name("alias.txt"))
        with self.assertRaisesRegex(fence.FenceError, "hardlink"):
            self.publish(identity, "workspace/U1.txt", digest)

    def test_completion_manifest_requires_runtime_publication(self):
        identity = self.dispatch()
        self.acquire(identity)
        canonical = self.project / "workspace/U1.txt"
        canonical.parent.mkdir()
        canonical.write_text("direct write is not published evidence")
        manifests = [{"path": "workspace/U1.txt", "sha256": hashlib.sha256(canonical.read_bytes()).hexdigest()}]
        with self.assertRaisesRegex(completion.CompletionError, "not_runtime_published"):
            self.commit(identity, self.staging(identity, manifests))

    def test_kernel_lock_released_on_process_death(self):
        identity = self.dispatch()
        self.acquire(identity)
        code = ("import sys; from pathlib import Path; import executor_fence as f; "
                "lock=f.runtime_lock(Path(sys.argv[1])); lock.__enter__(); "
                "print('locked', flush=True); sys.stdin.read()")
        proc = subprocess.Popen([sys.executable, "-c", code, str(self.root)],
                                cwd=Path(__file__).resolve().parent,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "locked")
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(self.check, identity)
                self.assertFalse(pending.done())
                proc.kill()
                proc.wait(timeout=5)
                self.assertEqual(pending.result(5), fence.attempt_root(self.project, identity))
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
