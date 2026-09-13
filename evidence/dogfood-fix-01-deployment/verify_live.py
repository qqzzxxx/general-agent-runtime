"""GET-only verification against the existing real registered Web Console."""
import json
from pathlib import Path
from urllib.request import urlopen
from urllib.error import HTTPError
from datetime import datetime, timezone

OUT = Path(__file__).resolve().parent
BASE = 'http://127.0.0.1:60649/api/runtimes/51042462032cbc1f'
responses = {}

def get(suffix):
    try:
        response = urlopen(BASE + suffix, timeout=30)
    except HTTPError as error:
        response = error
    with response:
        code, document = response.status, json.loads(response.read())
    responses[suffix] = {'status': code, 'document': document}
    return code, document

expected = {700100: ['evidence/dogfood-rounds.csv', 'workspace/dogfood-note.md'],
            700101: ['evidence/dogfood-rounds.csv'],
            700102: ['reports/final-verification-700102.json'], 700103: [], 700104: []}
summary = []
for mid, paths in expected.items():
    code, document = get(f'/rounds/{mid}')
    assert code == 200
    artifacts = document['round']['artifacts']
    assert artifacts['available'], artifacts
    assert sorted(p['path'] for p in artifacts['paths']) == paths
    code, document = get(f'/artifacts?message_id={mid}')
    assert code == 200
    rows = document['artifacts']['artifacts']
    assert sorted(r['path'] for r in rows) == paths
    for row in rows:
        assert row['provenance']['message_id'] == mid
        url = '/artifacts/' + row['artifact_id']
        code, detail = get(url)
        assert code == 200
        old = mid == 700100 and row['path'].endswith('.csv')
        assert detail['artifact']['verification'] == ('hash_mismatch' if old else 'verified')
        code, preview = get(url + '/preview')
        assert code == (409 if old else 200), preview
        if old:
            assert preview['error']['code'] == 'ARTIFACT_HASH_MISMATCH'
        summary.append({'message_id': mid, 'path': row['path'], 'artifact_id': row['artifact_id'],
                        'verification': detail['artifact']['verification'], 'preview_http_status': code})
    if not rows:
        summary.append({'message_id': mid, 'publications': []})
code, document = get('/artifacts')
assert code == 200
assert len(document['artifacts']['artifacts']) == 5
unbound = [r['path'] for r in document['artifacts']['artifacts'] if r['provenance']['class'] == 'unbound']
assert unbound == ['reports/final-report.md'], unbound
(OUT / 'live-http.json').write_text(json.dumps({'at': datetime.now(timezone.utc).isoformat(),
    'base': BASE, 'method': 'GET only; existing real server and registry', 'passed': True,
    'summary': summary, 'responses': responses}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
print(json.dumps(summary, indent=2))
