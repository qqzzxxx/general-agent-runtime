"""Disposable, localhost-only fixture for rendered UI acceptance.

Uses the existing artifact HTTP fixture and its recording control stub.
Never points the Console at a real Runtime or starts an Agent process.
Invoked by ui_productization_browser.mjs; a fixture-local signal closes it.
"""
from pathlib import Path
import json
import sys
import tempfile
import subprocess
import time

from test_web_console_artifacts_http import ArtifactsFixture
from test_web_console_cockpit import STATUS_A
from test_web_console_history import dispatch_event, completion_event


def main():
    repo = Path(__file__).resolve().parents[1]
    scratch = repo / '.test-tmp'
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ui-qa-', dir=scratch) as temp:
        fixture = ArtifactsFixture(Path(temp))
        try:
            active = dict(STATUS_A, PROJECT_ID='proj-x')
            active['active_task'] = dict(STATUS_A['active_task'], TASK_ID='检查中文输入与长路径-' + '运行边界验证' * 12)
            active['last_authorized_dispatch'] = dict(active['active_task'])
            (fixture.runtime_a / 'stub_status.json').write_text(json.dumps(active), encoding='utf-8')
            events = [dispatch_event(700107, task='交付检查', archived_at='2026-09-12T00:00:00+00:00'), completion_event(700107, task='交付检查', committed_at='2026-09-12T01:00:00+00:00')]
            (fixture.runtime_a / 'stub_history' / 'timeline.json').write_text(json.dumps(events), encoding='utf-8')
            stub = fixture.runtime_a / 'scripts' / 'supervisor_control.py'
            source = stub.read_text(encoding='utf-8').replace('if subcommand == "timeline":\n    emit([])', 'if subcommand == "timeline":\n    emit(json.loads((history / "timeline.json").read_text(encoding="utf-8")))')
            stub.write_text(source, encoding='utf-8')
            probe = subprocess.run([sys.executable, str(stub), '--root', str(fixture.runtime_a), 'status', '--json'], capture_output=True, timeout=15)
            if probe.returncode:
                raise RuntimeError(probe.stderr.decode('utf-8', errors='replace'))
            signal = Path(temp) / 'qa-signal.txt'
            signal.write_text('running', encoding='utf-8')
            print(json.dumps({'port': fixture.port, 'root': str(fixture.runtime_a), 'signal': str(signal)}), flush=True)
            # Do not inherit a blocked stdin pipe into the Windows control probe.
            while signal.read_text(encoding='utf-8') != 'close':
                if signal.read_text(encoding='utf-8') == 'paused':
                    active['runtime_status'] = 'PAUSED'
                    active['pause'] = {'status': 'PAUSED', 'mode': 'SAFE', 'requested_at': '2026-09-12T01:00:00+00:00', 'paused_at': '2026-09-12T01:02:00+00:00', 'resumed_at': None}
                    (fixture.runtime_a / 'stub_status.json').write_text(json.dumps(active), encoding='utf-8')
                    signal.write_text('running', encoding='utf-8')
                time.sleep(.1)
        finally:
            fixture.close()


if __name__ == '__main__':
    main()
