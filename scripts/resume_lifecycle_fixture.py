"""Isolated real scheduler fixture. Only model execution/goal input is synthetic."""
import json
import os
from pathlib import Path
import shutil
import sys
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent


def simulated_resume(fixture):
    """Existing control-unit tests: exercise the real ticket commit with a fake owner."""
    import runtime_lifecycle as lifecycle
    import supervisor_control as sc
    o = fixture.configured_orchestrator()
    def boot(root, token):
        o.acquire_lock()
        lifecycle.scheduler_startup(root, o.load_runtime(), token)
        return mock.Mock(poll=lambda: None)
    try:
        with mock.patch.object(lifecycle, 'launch', side_effect=boot):
            return sc.resume(fixture.root)
    finally:
        o.release_lock()


def install_driver(fixture):
    scripts = fixture.root / 'scripts'
    scripts.mkdir(exist_ok=True)
    for name in ('supervisor_control.py', 'runtime_lifecycle.py', 'executor_fence.py',
                 'executor_completion.py', 'executor_claim.py', 'ordinary_dispatch.py'):
        shutil.copy2(SCRIPTS / name, scripts / name)
    source = '''import json, os, sys, time, uuid
from pathlib import Path
from unittest import mock
sys.path.insert(0, SCRIPTS_PATH)
import test_supervisor_control as fixture_module
f = fixture_module.SupervisorControlTests()
f.root = Path(__file__).resolve().parent
f.control = f.root / 'control'
f.project = f.root / 'projects' / f.PROJECT
o = f.configured_orchestrator()
o.POLL_SECONDS = 0.05
o.goal_anchor_gate = lambda *a: True
o.build_codex_prompt = lambda *a, **k: 'synthetic fixture goal'
o.find_codex = lambda: 'fixture-model'
def model(*args, **kwargs):
    (f.root / 'model-entered').write_text('yes')
    while not (f.root / 'allow-model').exists():
        time.sleep(0.02)
    state = o.read_project_state()
    decision = {'decision': 'CONTINUE', 'reason': 'fresh next action after resume'}
    state['decision_history'].append(decision)
    state.update(status='WAITING_EXECUTOR', last_supervisor_decision=decision,
                 ordinary_task_proposal={'logical_task': 'resume', 'logical_stage': 'next-action',
                     'objective': 'Complete the next action after resume', 'inputs': [],
                     'outputs': ['reports/resume.md'],
                     'acceptance_criteria': ['Document the completed next action.']})
    o.atomic_json(o.PROJECT_STATE, state)
    events = [{'type': 'thread.started', 'thread_id': str(uuid.uuid4())}, {'type': 'turn.started'}, {'type': 'turn.completed', 'usage': {'input_tokens': 11, 'output_tokens': 3}}]
    kwargs['stdout'].write((''.join(json.dumps(e)+'\\n' for e in events)).encode())
    return mock.Mock(returncode=0)
o.subprocess.run = model
raise SystemExit(o.main())
'''.replace('SCRIPTS_PATH', repr(str(SCRIPTS)))
    (fixture.root / 'orchestrator.py').write_text(source, encoding='utf-8')
