"""Release-blocking UI structure and presentation regression checks.

Real browser layout checks live in ui_productization_browser.mjs; these
offline tests pin routing, accessibility, localization and JS parsing.
"""
from html.parser import HTMLParser
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import unittest

INDEX = Path(__file__).resolve().parents[1] / 'web_console' / 'index.html'


class Structure(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.stack, self.ids, self.routes = [], {}, []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            if attrs['id'] in self.ids:
                raise AssertionError('duplicate ID: ' + attrs['id'])
            self.ids[attrs['id']] = {'parents': list(self.stack), 'attrs': attrs}
        if 'data-view' in attrs:
            self.routes.append(attrs['data-view'])
        if tag not in {'meta', 'input', 'br', 'hr', 'img', 'link', 'wbr'}:
            self.stack.append(attrs.get('id', tag))

    def handle_endtag(self, tag):
        self.stack.pop()


class ApplicationShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = INDEX.read_text(encoding='utf-8')
        cls.structure = Structure(cls.source)

    def test_each_major_surface_is_routed_and_separate(self):
        for route in ('cockpit', 'timeline', 'artifacts', 'setup', 'settings', 'create', 'register'):
            self.assertIn(route, self.structure.routes)
            view = self.structure.ids['view-' + route]
            if route != 'cockpit':
                self.assertIn('hidden', view['attrs'])
        for panel, route in {'settings-section':'settings', 'runtime-create-section':'create', 'timeline-section':'timeline', 'artifact-center-section':'artifacts', 'setup-wizard-section':'setup'}.items():
            parents = self.structure.ids[panel]['parents']
            self.assertIn('view-' + route, parents)
            self.assertNotIn('view-cockpit', parents)

    def test_navigation_and_both_runtime_actions_are_in_sidebar(self):
        self.assertIn('column-runtimes', self.structure.ids['primary-nav']['parents'])
        self.assertIn('id="empty-state"', self.source)
        self.assertIn('Create your first Runtime', self.source)
        self.assertIn('Register Existing Runtime', self.source)
        self.assertIn('position: fixed', self.source)

    def test_registration_uses_existing_contract(self):
        self.assertIn('postJson("/api/runtimes", {root:', self.source)
        self.assertIn('label: document.getElementById("rr-label").value.trim()', self.source)
        self.assertIn('if (button.disabled) { return; }', self.source)
        self.assertIn('finally { button.disabled = false; }', self.source)

    def test_default_chinese_and_explicit_english_switch(self):
        self.assertIn('<html lang="zh-CN">', self.source)
        self.assertIn('"gar-language") === "en" ? "en" : "zh-CN"', self.source)
        self.assertIn('"Current execution": "当前执行"', self.source)
        self.assertIn('"Register Existing Runtime": "注册已有 Runtime"', self.source)
        self.assertIn('value="en">English', self.source)

    def test_layout_protects_health_values_and_hidden_surfaces(self):
        self.assertIn('[hidden] { display: none !important; }', self.source)
        self.assertIn('grid-template-columns: 115px minmax(0, 1fr)', self.source)
        self.assertIn('@media (max-width: 1190px)', self.source)
        self.assertIn('document.getElementById("ce-since").title = state.since.at', self.source)
        self.assertIn('new Intl.RelativeTimeFormat', self.source)
        self.assertIn('advanced.id = "settings-advanced"', self.source)

    def test_no_development_stage_labels_in_static_visible_copy(self):
        body = self.source.split('<body>', 1)[1].split('<script>', 1)[0]
        body = re.sub(r'<!--[\s\S]*?-->', '', body)
        self.assertIsNone(re.search(r'\bP[3-7]\b', body))

    @unittest.skipUnless(shutil.which('node'), 'Node not available')
    def test_javascript_parses_and_localization_keeps_unknown_data(self):
        script = self.source.split('<script>', 1)[1].split('</script>', 1)[0]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / 'frontend.js'
            target.write_text(script, encoding='utf-8')
            result = subprocess.run(['node', '--check', str(target)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            # Only evaluate the pure presentation definitions; no fake browser DOM.
            presentation = script.split('const COCKPIT_POLL_MS', 1)[0]
            target.write_text('const localStorage = {getItem:()=>null};\n' + presentation + '\nconsole.log(JSON.stringify([uiText("Current execution"), uiText("中文原始内容<script>"), relativeTime("not a timestamp")]));', encoding='utf-8')
            result = subprocess.run(['node', str(target)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), ['当前执行', '中文原始内容<script>', 'not a timestamp'])


if __name__ == '__main__':
    unittest.main()
