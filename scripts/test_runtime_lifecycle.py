"""Fix 06: real process/HTTP resume plus deterministic failure boundaries."""
import concurrent.futures
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_lifecycle as lifecycle
import supervisor_control as sc
import provider_usage
import executor_claim
import executor_completion
import executor_fence
import test_supervisor_control as core
from resume_lifecycle_fixture import install_driver


class ResumeLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.f = core.SupervisorControlTests()
        self.f.setUp()
        self.root = self.f.root
        self.children = []
        self.addCleanup(self.cleanup_runtime)

    def cleanup_runtime(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
            child.wait(timeout=5)
        try:
            owner = lifecycle.live_owner(self.root)
            if owner and owner['pid'] != os.getpid():
                os.kill(owner['pid'], signal.SIGTERM)
                self.wait_for(lambda: lifecycle.process_alive(owner['pid']) is False)
        finally:
            self.f.tearDown()

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        self.fail('fixture boundary not reached: ' + (self.root / 'logs' / 'resume-startup.log').read_text(errors='replace') if (self.root / 'logs' / 'resume-startup.log').exists() else 'fixture boundary not reached')

    def paused(self):
        install_driver(self.f)
        sc.set_pause(self.root)

    def tracked_resume(self):
        real_launch = lifecycle.launch
        def launch(root, token):
            child = real_launch(root, token)
            self.children.append(child)
            return child
        with mock.patch.object(lifecycle, 'launch', side_effect=launch):
            return sc.resume(self.root)

    def test_exited_pause_relaunches_schedulable_owner_and_retry_is_idempotent(self):
        self.paused()
        result = self.tracked_resume()
        self.assertTrue(result['startup']['verified'])
        self.assertEqual(result['startup']['status'], 'READY')
        owner = lifecycle.live_owner(self.root)
        self.assertEqual(owner, self.f.read_runtime()['scheduler_owner'])
        self.assertEqual(self.f.read_runtime()['status'], 'RUNNING')
        self.wait_for(lambda: (self.root / 'model-entered').exists())
        revision = sc.load_control(self.root)['revision']
        for _ in range(3):
            retry = sc.resume(self.root)
            self.assertEqual(retry['startup']['status'], 'EXISTING_OWNER')
            self.assertEqual(retry['startup']['owner'], owner)
        self.assertEqual(sc.load_control(self.root)['revision'], revision)
        self.assertEqual(len(self.children), 1)

    def test_two_http_resumes_share_one_real_scheduler(self):
        import test_web_console_control_http as http_fixture
        self.paused()
        server = http_fixture.ControlFixture(self.root / 'console-fixture')
        try:
            entry = server.add_runtime(self.root, 'Fix 06 isolated fixture')
            def request():
                return server.request('POST', f"/api/runtimes/{entry['id']}/controls/resume", body={})
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                results = list(pool.map(lambda _: request(), range(2)))
            self.assertEqual([item[0] for item in results], [200, 200], results)
            starts = [item[1]['control']['result']['startup'] for item in results]
            self.assertEqual(sorted(item['status'] for item in starts), ['EXISTING_OWNER', 'READY'])
            self.assertEqual(starts[0]['owner'], starts[1]['owner'])
        finally:
            server.close()

    def test_real_http_launcher_failure_retains_pause_and_returns_failure(self):
        import test_web_console_control_http as http_fixture
        self.paused()
        (self.root / 'orchestrator.py').unlink()
        server = http_fixture.ControlFixture(self.root / 'console-fixture')
        try:
            entry = server.add_runtime(self.root, 'Fix 06 failed launch')
            status, doc, _ = server.request('POST', f"/api/runtimes/{entry['id']}/controls/resume", body={})
            self.assertEqual(status, 409, doc)
            self.assertFalse(doc['ok'])
            self.assertEqual(sc.pause_status(self.root), 'PAUSED')
            self.assertIsNone(lifecycle.live_owner(self.root))
        finally:
            server.close()

    def test_launch_exception_and_real_early_exit_allow_retry(self):
        self.paused()
        for boot in (lambda *a: (_ for _ in ()).throw(OSError('launch failed')), lifecycle.launch):
            with self.subTest(launcher=boot):
                (self.root / 'orchestrator.py').write_text('raise SystemExit(7)\n')
                with mock.patch.object(lifecycle, 'launch', side_effect=boot):
                    with self.assertRaises(sc.ControlError):
                        sc.resume(self.root)
                self.assertEqual(sc.pause_status(self.root), 'PAUSED')
                self.assertEqual(self.f.read_runtime()['status'], 'PAUSED')
        install_driver(self.f)
        self.assertTrue(self.tracked_resume()['startup']['verified'])

    def test_timeout_cancels_ticket_so_delayed_start_cannot_clear_pause(self):
        self.paused()
        with mock.patch.object(lifecycle, 'STARTUP_TIMEOUT', 0.05), mock.patch.object(
                lifecycle, 'launch', return_value=mock.Mock(poll=lambda: None)):
            with self.assertRaisesRegex(sc.ControlError, 'timed out'):
                sc.resume(self.root)
        ticket = sc._read_json(self.root / 'control' / 'resume_lifecycle.json')
        o = self.f.configured_orchestrator()
        o.acquire_lock()
        try:
            with self.assertRaisesRegex(sc.ControlError, 'cancelled'):
                lifecycle.scheduler_startup(self.root, o.load_runtime(), ticket['id'])
        finally:
            o.release_lock()
        self.assertEqual(sc.pause_status(self.root), 'PAUSED')

    def test_new_pause_supersedes_startup_ticket(self):
        self.paused()
        o = self.f.configured_orchestrator()
        def changed(root, token):
            sc.set_pause(root)
            o.acquire_lock()
            try:
                lifecycle.scheduler_startup(root, o.load_runtime(), token)
            finally:
                o.release_lock()
        with mock.patch.object(lifecycle, 'launch', side_effect=changed):
            with self.assertRaisesRegex(sc.ControlError, 'superseded'):
                sc.resume(self.root)
        self.assertEqual(sc.pause_status(self.root), 'PAUSED')

    def test_paused_owner_is_allowed_to_exit_before_relaunch(self):
        self.paused()
        o = self.f.configured_orchestrator()
        o.acquire_lock()
        def exit_owner():
            time.sleep(0.1)
            o.release_lock()
        thread = threading.Thread(target=exit_owner)
        thread.start()
        try:
            result = self.tracked_resume()
            self.assertTrue(result['startup']['verified'])
            self.assertNotEqual(result['startup']['owner']['pid'], os.getpid())
        finally:
            thread.join(timeout=5)

    def test_runtime_write_then_control_write_failure_remains_retryable(self):
        self.paused()
        o = self.f.configured_orchestrator()
        save = sc.save_control
        def fail_running(root, value):
            if value['pause']['status'] == 'RUNNING':
                raise OSError('injected control write failure')
            return save(root, value)
        def boot(root, token):
            o.acquire_lock()
            try:
                lifecycle.scheduler_startup(root, o.load_runtime(), token)
            finally:
                o.release_lock()
        with mock.patch.object(lifecycle, 'launch', side_effect=boot), mock.patch.object(
                sc, 'save_control', side_effect=fail_running):
            with self.assertRaises(sc.ControlError):
                sc.resume(self.root)
        self.assertEqual(sc.pause_status(self.root), 'PAUSED')
        self.assertEqual(self.f.read_runtime()['status'], 'PAUSED')
        self.assertTrue(self.tracked_resume()['startup']['verified'])

    def test_known_interrupted_startup_preparation_recovers_but_unknown_owner_refuses(self):
        self.paused()
        control = sc.load_control(self.root)
        self.f._json(self.f.control / 'resume_lifecycle.json', {
            'schema_version': 1, 'project_id': self.f.PROJECT,
            'status': 'STARTING', 'id': 'interrupted-ticket', 'revision': control['revision']})
        runtime = self.f.read_runtime()
        runtime.update(status='RUNNING', scheduler_resume_id='interrupted-ticket')
        self.f._json(self.f.control / 'orchestrator_runtime.json', runtime)
        result = self.tracked_resume()
        self.assertTrue(result['startup']['verified'])
        with mock.patch.object(lifecycle, 'process_alive', return_value=None):
            with self.assertRaisesRegex(sc.ControlError, 'cannot be verified'):
                sc.resume(self.root)

    def test_pid_reuse_is_not_a_live_scheduler(self):
        self.paused()
        self.f._json(self.f.control / '.orchestrator.lock', {
            'pid': 12345, 'owner_id': 'old-owner', 'process_identity': 'old-birth'})
        with mock.patch.object(lifecycle, 'process_alive', return_value=True), mock.patch.object(
                lifecycle, 'process_identity', return_value='new-birth'):
            self.assertIsNone(lifecycle.live_owner(self.root))

    def test_failure_after_running_commit_rolls_back_even_if_steer_advanced_revision(self):
        self.paused()
        o = self.f.configured_orchestrator()
        def dies(root, token):
            o.acquire_lock()
            lifecycle.scheduler_startup(root, o.load_runtime(), token)
            sc.submit_intervention(root, b'preserve on failure')
            o.release_lock()
            raise OSError('child died after commit')
        with mock.patch.object(lifecycle, 'launch', side_effect=dies):
            with self.assertRaises(sc.ControlError):
                sc.resume(self.root)
        self.assertEqual(sc.pause_status(self.root), 'PAUSED')
        self.assertEqual(self.f.read_runtime()['status'], 'PAUSED')
        self.assertEqual(sc.list_interventions(self.root, self.f.PROJECT)[0]['status'], 'PENDING')

    def test_terminal_unknown_and_split_states_fail_closed(self):
        self.paused()
        for status in ('COMPLETE', 'STOPPED', 'HUMAN_REVIEW', 'BLOCKED', 'FUTURE'):
            with self.subTest(status=status):
                state = self.f.read_state()
                state['status'] = status
                self.f._json(self.f.project / 'project_state.json', state)
                with mock.patch.object(lifecycle, 'launch') as launch:
                    with self.assertRaises(sc.ControlError):
                        sc.resume(self.root)
                    launch.assert_not_called()
                self.assertEqual(sc.pause_status(self.root), 'PAUSED')
        self.f.state['status'] = 'SUPERVISOR_TURN'
        self.f.save_state()
        control = sc.load_control(self.root)
        control['pause']['status'] = 'RUNNING'
        sc.save_control(self.root, control)
        with self.assertRaisesRegex(sc.ControlError, 'no verified'):
            sc.resume(self.root)

    def test_claimed_executor_pending_pause_is_preserved(self):
        self.f.authorize_fixture(claimed=True)
        sc.set_pause(self.root)
        before = self.f.read_runtime()
        with self.assertRaisesRegex(sc.ControlError, 'pending'):
            sc.resume(self.root)
        self.assertEqual(self.f.read_runtime(), before)
        self.assertNotIn(700120, before['retired_message_ids'])
        self.assertEqual(sc.pause_status(self.root), 'PENDING_AFTER_CURRENT_STAGE')

    def test_paused_retired_or_unauthorized_current_task_fails_closed(self):
        self.f.authorize_fixture()
        current = self.f.read_state()
        sc.set_pause(self.root)
        self.f._json(self.f.project / 'project_state.json', current)
        for retired in ([700120], []):
            runtime = self.f.read_runtime()
            runtime['retired_message_ids'] = retired
            if not retired:
                runtime['authorized_dispatch'] = None
            self.f._json(self.f.control / 'orchestrator_runtime.json', runtime)
            with mock.patch.object(lifecycle, 'launch') as launch:
                with self.assertRaisesRegex(sc.ControlError, 'retired or lacks matching'):
                    sc.resume(self.root)
                launch.assert_not_called()
            self.assertEqual(sc.pause_status(self.root), 'PAUSED')

    def test_retired_task_and_old_completion_publication_usage_survive_new_steer_turn(self):
        # Seed an actual finished Supervisor turn with provider capture, followed
        # by its immutable completion and an unclaimed next dispatch.
        old_begin = sc.begin_supervisor_turn
        def captured(*args, **kwargs):
            turn = old_begin(*args, **kwargs)
            with provider_usage.stdout_capture(self.root, turn) as stream:
                stream.write(b'{"type":"thread.started","thread_id":"11111111-1111-4111-8111-111111111111"}\n{"type":"turn.started"}\n{"type":"turn.completed","usage":{"input_tokens":17,"output_tokens":5}}\n')
            return turn
        self.f.task = self.f.make_task(700100, 'old-completed')
        self.f.runtime['last_consumed_message_id'] = 700099
        with mock.patch.object(sc, 'begin_supervisor_turn', side_effect=captured):
            self.f.authorize_fixture()
        identity = {key: self.f.task[key] for key in sc.IDENTITY_KEYS}
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(executor_claim.acquire(self.root, *identity.values()), 0)
        claim_token = output.getvalue().split('claim_token=')[1].strip()
        workspace = executor_fence.prepare(self.root, identity, claim_token=claim_token)
        candidate = workspace / 'workspace' / 'existing.txt'
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_bytes(b'existing artifact identity and content')
        digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
        publication = executor_fence.publish(self.root, identity, 'workspace/existing.txt', digest,
                                             claim_token=claim_token)
        staging = self.f.project / 'completion_staging' / 'old-completed'
        self.f._json(staging / 'staging.json', {
            'COMPLETION_STAGING_SCHEMA_VERSION': 1, **identity, 'PROJECT_ID': self.f.PROJECT,
            'STATUS': 'STAGING_READY', 'CREATED_AT': sc.now_iso(),
            'RECEIPT': {**identity, 'STATUS': 'COMPLETED'},
            'DELIVERABLES': [{'path': 'workspace/existing.txt', 'sha256': digest}], 'EVIDENCE': []})
        executor_completion.commit(self.root, staging, claim_token=claim_token)
        o = self.f.configured_orchestrator()
        runtime = o.load_runtime()
        self.assertTrue(o.consume_executor_receipt(runtime)[0])
        self.f.runtime = o.load_runtime()
        self.f.task = self.f.make_task(700120, 'retire-before-claim')
        self.f.authorize_fixture()
        o.seal_completions(self.f.runtime, o.read_project_state())
        old_files = list((self.root / 'handoff' / 'completion_ledger').rglob('*'))
        old_files += list((self.root / 'handoff' / 'executor_publications').rglob('*'))
        old_files += list((self.root / 'control' / 'supervisor_turns').rglob('*'))
        old_files += list((self.root / 'control' / 'supervisor_usage').rglob('*'))
        old_files += [publication]
        before = {path: path.read_bytes() for path in old_files if path.is_file()}
        old_publications = list((self.root / 'handoff' / 'executor_publications').rglob('*.json'))
        self.assertEqual(len(old_publications), 1)
        old_turns = {p.stem for p in (self.root / 'control' / 'supervisor_turns').glob('*.json')}
        old_usage = [provider_usage.read_usage(self.root, turn, self.f.PROJECT) for turn in old_turns]
        self.assertEqual(sum(value.get('input_tokens') or 0 for value in old_usage), 17)
        self.paused()
        # QUOTA-PAUSE-PARK-V1: the pause parks the unclaimed dispatch; it is
        # retired only later, as SUPERSEDED, once the successor registers.
        self.assertNotIn(700120, self.f.read_runtime()['retired_message_ids'])
        self.assertIn(700120, self.f.read_runtime()['parked_message_ids'])
        steer = sc.submit_intervention(self.root, b'consume exactly once after resume')
        self.tracked_resume()
        (self.root / 'allow-model').touch()
        self.wait_for(lambda: any(r.get('intervention_id') == steer['intervention_id'] and r.get('status') == 'CONSUMED' for r in sc.list_interventions(self.root, self.f.PROJECT)))
        record = next(r for r in sc.list_interventions(self.root, self.f.PROJECT) if r['intervention_id'] == steer['intervention_id'])
        runtime = {}
        def next_dispatch_visible():
            nonlocal runtime
            # The real scheduler replaces this file under the fence mutex.
            # Read one coherent snapshot without racing Windows replacement IO.
            with executor_fence.runtime_lock(self.root):
                runtime = self.f.read_runtime()
            return (runtime.get('authorized_dispatch') or {}).get('MESSAGE_ID') == 700121
        self.wait_for(next_dispatch_visible)
        self.assertIn(700120, runtime['retired_message_ids'])
        self.assertEqual(runtime['authorized_dispatch']['MESSAGE_ID'], 700121)
        self.assertEqual(runtime['executor_receipts_consumed'], 1)
        self.assertEqual(list((self.root / 'handoff' / 'executor_publications').rglob('*.json')), old_publications)
        for path, data in before.items():
            self.assertEqual(path.read_bytes(), data, str(path))
        new_turns = {p.stem for p in (self.root / 'control' / 'supervisor_turns').glob('*.json')} - old_turns
        self.assertEqual(len(new_turns), 1)
        turn_id = new_turns.pop()
        usage = provider_usage.read_usage(self.root, turn_id, self.f.PROJECT)
        self.assertEqual(usage['input_tokens'], 11)
        self.assertEqual(usage['output_tokens'], 3)
        self.assertNotIn(usage['execution_id'], {value.get('execution_id') for value in old_usage})
        all_turns = old_turns | {turn_id}
        self.assertEqual(sum(provider_usage.read_usage(self.root, turn, self.f.PROJECT).get('input_tokens') or 0
                             for turn in all_turns), 28)
        sc.resume(self.root)
        self.assertEqual(record, next(r for r in sc.list_interventions(self.root, self.f.PROJECT) if r['intervention_id'] == steer['intervention_id']))
        self.assertEqual(len(list((self.root / 'control' / 'supervisor_turns').glob('*.json'))), len(old_turns) + 1)


if __name__ == '__main__':
    unittest.main()
