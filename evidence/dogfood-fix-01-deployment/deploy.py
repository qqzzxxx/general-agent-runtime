"""One-time, allowlisted Fix 01 deployment; evidence stays outside dogfood."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime, timezone

EVIDENCE = Path(__file__).resolve().parent
SOURCE = EVIDENCE.parent.parent
TARGET = Path(r'C:\general-agent-runtime-v13-dogfood')
FILES = ['scripts/executor_fence.py', 'scripts/executor_completion.py', 'scripts/supervisor_control.py']

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise RuntimeError(f'Reparse point refused: {path}')
        if path.is_file():
            result[path.relative_to(root).as_posix()] = {'sha256': digest(path), 'bytes': path.stat().st_size}
    return result

def save(name, value):
    (EVIDENCE / name).write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

def verify():
    before = json.loads((EVIDENCE / 'before.json').read_text())['files']
    after = inventory(TARGET)
    protected = {p: v for p, v in before.items() if p not in FILES and '__pycache__' not in Path(p).parts}
    changed = [p for p, v in protected.items() if after.get(p) != v]
    additions = [p for p in after if p not in before and '__pycache__' not in Path(p).parts]
    deleted = [p for p in before if p not in after]
    result = {'at': datetime.now(timezone.utc).isoformat(), 'protected_file_count': len(protected),
              'protected_files': {p: {'before': v['sha256'], 'after': after.get(p, {}).get('sha256')} for p, v in protected.items()},
              'changed_protected_files': changed, 'new_noncache_files': additions, 'deleted_files': deleted,
              'product_files': {p: {'before': before[p]['sha256'], 'after': after[p]['sha256'], 'source': digest(SOURCE / p)} for p in FILES},
              'cache_changes': [p for p in after if '__pycache__' in Path(p).parts and after[p] != before.get(p)]}
    result['passed'] = not (changed or additions or deleted) and all(v['after'] == v['source'] for v in result['product_files'].values())
    save('after.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'protected_files'}, indent=2))
    assert result['passed']

mode = sys.argv[1]
if mode == 'snapshot':
    assert not (EVIDENCE / 'before.json').exists(), 'Never overwrite baseline'
    tested = json.loads((SOURCE / 'evidence/dogfood-fix-01/test-results.json').read_text())['final_source_sha256']
    for relative in FILES:
        assert digest(SOURCE / relative) == tested[relative], f'Untested source: {relative}'
        compile((SOURCE / relative).read_bytes(), relative, 'exec')
    before = inventory(TARGET)
    for relative in before:
        backup = EVIDENCE / 'snapshot' / relative
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(TARGET / relative, backup)
        assert digest(backup) == before[relative]['sha256']
    assert inventory(TARGET) == before, 'Target changed during snapshot'
    save('before.json', {'at': datetime.now(timezone.utc).isoformat(), 'root': str(TARGET), 'files': before})
    save('source.json', {p: digest(SOURCE / p) for p in FILES})
    print(f'Snapshotted and SHA-256 verified all {len(before)} files before deployment')
elif mode == 'deploy':
    before = json.loads((EVIDENCE / 'before.json').read_text())['files']
    assert inventory(TARGET) == before, 'Target changed since snapshot'
    expected = json.loads((EVIDENCE / 'source.json').read_text())
    for relative in FILES:
        assert digest(SOURCE / relative) == expected[relative]
    for relative in FILES:
        target = TARGET / relative
        assert target.resolve().is_relative_to(TARGET.resolve())
        temp = target.with_suffix('.fix01.tmp')
        with temp.open('xb') as stream:
            stream.write((SOURCE / relative).read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        assert digest(temp) == expected[relative]
        os.replace(temp, target)
    verify()
elif mode == 'verify':
    verify()
else:
    raise ValueError(mode)
