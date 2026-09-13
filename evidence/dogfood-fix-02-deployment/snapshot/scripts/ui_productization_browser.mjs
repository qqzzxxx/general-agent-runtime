/** Real Chrome/Edge rendering and browser assertions, using built-in Node CDP.
 * No npm/browser installation. All Runtime data belongs to a disposable fixture.
 * Run: node scripts/ui_productization_browser.mjs
 */
import {spawn} from 'node:child_process';
import {existsSync, mkdirSync, writeFileSync, rmSync} from 'node:fs';
import {resolve, dirname, basename} from 'node:path';
import {createInterface} from 'node:readline';
import assert from 'node:assert/strict';

const repo = resolve(import.meta.dirname, '..');
const out = resolve(repo, 'evidence/ui-productization');
mkdirSync(out, {recursive: true});
const browserPath = [process.env.UI_QA_BROWSER, 'C:/Program Files/Google/Chrome/Application/chrome.exe', 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'].find(path => path && existsSync(path));
assert(browserPath, 'VISUAL_QA_REQUIRES_HUMAN: no installed Chrome/Edge found');
const fixture = spawn('python', ['scripts/ui_productization_fixture.py'], {cwd: repo, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe']});
let browser, socket, fixtureSignal, profile;
const consoleErrors = [], checks = [];
fixture.stderr.on('data', data => writeFileSync(resolve(out, 'fixture.log'), data, {flag: 'a'}));
const delay = ms => new Promise(done => setTimeout(done, ms));
try {
  const info = await new Promise((done, reject) => {
    const lines = createInterface({input: fixture.stdout});
    lines.once('line', line => done(JSON.parse(line)));
    fixture.once('error', reject);
    fixture.once('exit', code => { if (code) reject(new Error('Fixture exited: ' + code)); });
  });
  fixtureSignal = info.signal;
  profile = resolve(repo, '.test-tmp/ui-browser-' + Date.now());
  const endpoint = await new Promise((done, reject) => {
    browser = spawn(browserPath, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--remote-debugging-port=0', '--remote-allow-origins=*', '--user-data-dir=' + profile, 'about:blank'], {windowsHide: true, stdio: ['ignore','ignore','pipe']});
    let output = '';
    browser.stderr.on('data', data => { output += data; const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/); if (match) done(match[1]); });
    browser.once('error', reject);
    browser.once('exit', code => reject(new Error('Browser exited: ' + code)));
  });
  const endpointUrl = new URL(endpoint);
  const tabs = await (await fetch('http://' + endpointUrl.host + '/json/list')).json();
  socket = new WebSocket(tabs.find(tab => tab.type === 'page').webSocketDebuggerUrl);
  await new Promise(done => socket.addEventListener('open', done, {once:true}));
  let nextId = 0;
  const pending = new Map();
  socket.addEventListener('message', event => {
    const data = JSON.parse(event.data);
    if (data.id && pending.has(data.id)) { const {done,reject,timer} = pending.get(data.id); clearTimeout(timer); pending.delete(data.id); data.error ? reject(new Error(JSON.stringify(data.error))) : done(data.result); }
    if (data.method === 'Runtime.exceptionThrown') consoleErrors.push(data.params.exceptionDetails);
  });
  function command(method, params = {}) {
    return new Promise((done,reject) => { const id = ++nextId; const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout: ' + method)); }, 20000); pending.set(id, {done,reject,timer}); socket.send(JSON.stringify({id,method,params})); });
  }
  async function evaluate(expression) {
    const result = await command('Runtime.evaluate', {expression, awaitPromise:true, returnByValue:true});
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  }
  async function until(expression) {
    for (let i=0; i<100; i++) { if (await evaluate(expression)) return; await delay(150); }
    const diagnostics = await evaluate(`({register:document.getElementById('rr-status')?.textContent, state:document.getElementById('ce-state-label')?.textContent, html:document.body.innerText.slice(-1500)})`);
    writeFileSync(resolve(out,'failure.json'),JSON.stringify({expression,diagnostics,consoleErrors},null,2));
    await screenshot('failure');
    throw new Error('UI condition timed out: ' + expression + ' ' + JSON.stringify(diagnostics));
  }
  async function click(selector) { await evaluate(`document.querySelector(${JSON.stringify(selector)}).click()`); await delay(150); }
  async function screenshot(name, width=1366, height=768) {
    await command('Emulation.setDeviceMetricsOverride', {width,height,deviceScaleFactor:1,mobile:false});
    await delay(250);
    const bounds = await evaluate(`({width:innerWidth, height:innerHeight, client:document.documentElement.clientWidth, scroll:document.documentElement.scrollWidth, views:[...document.querySelectorAll('.app-view')].filter(e=>!e.hidden).map(e=>e.id), narrow:[...document.querySelectorAll('#runtime-health dd')].filter(e=>e.getClientRects().length && e.getBoundingClientRect().width < 120).length})`);
    assert.equal(bounds.scroll, bounds.client, name + ': page overflow');
    assert.equal(bounds.views.length, 1, name + ': multiple surfaces visible');
    assert.equal(bounds.narrow, 0, name + ': squeezed health value');
    const shot = await command('Page.captureScreenshot', {format:'png', captureBeyondViewport:false});
    writeFileSync(resolve(out, name + '.png'), Buffer.from(shot.data,'base64'));
    checks.push({name, ...bounds});
  }
  await command('Page.enable'); await command('Runtime.enable');
  await command('Emulation.setDeviceMetricsOverride', {width:1366,height:768,deviceScaleFactor:1,mobile:false});
  await command('Page.navigate', {url:'http://127.0.0.1:' + info.port});
  await until(`document.getElementById('empty-state') && !document.getElementById('empty-state').hidden`);
  assert.equal(await evaluate('document.documentElement.lang'), 'zh-CN');
  for (const route of ['create','register']) {
    assert(await evaluate(`(()=>{const e=document.querySelector('.runtime-actions [data-view=${route}]');const r=e.getBoundingClientRect();return r.top>=0 && r.bottom<=innerHeight && e.textContent.includes('Runtime')})()`));
  }
  await screenshot('empty-1366');
  await click('.runtime-actions [data-view="create"]');
  await until(`!document.getElementById('view-create').hidden`);
  assert(await evaluate(`document.getElementById('rc-submit').getBoundingClientRect().bottom < innerHeight`));
  await screenshot('create-1366');
  await click('.runtime-actions [data-view="register"]');
  await evaluate(`document.getElementById('rr-root').value=${JSON.stringify(info.root + '-missing')};document.getElementById('rr-label').value='不存在的 Runtime'`);
  await click('#rr-submit');
  await until(`document.getElementById('rr-status').classList.contains('bad') && !document.getElementById('rr-submit').disabled`);
  assert.equal(await evaluate('registeredRuntimes.length'),0);
  await evaluate(`document.getElementById('rr-root').value=${JSON.stringify(info.root)};document.getElementById('rr-label').value='本地测试 Runtime · 中文路径与长名称验证'`);
  await click('#rr-submit');
  await until(`document.getElementById('rr-status').classList.contains('ok')`);
  await click('#primary-nav [data-view="cockpit"]');
  await until(`document.getElementById('ce-state-label').textContent.includes('ZCode 正在')`);
  assert.equal(await evaluate(`document.getElementById('settings-section').getClientRects().length`), 0);
  assert.equal(await evaluate(`document.getElementById('active-project').textContent`), 'proj-x');
  await screenshot('cockpit-1366');
  await screenshot('cockpit-1440',1440,900);
  await screenshot('cockpit-1920',1920,1080);
  await command('Emulation.setDeviceMetricsOverride', {width:1366,height:768,deviceScaleFactor:1,mobile:false});
  await click('#primary-nav [data-view="settings"]');
  assert.equal(await evaluate(`document.getElementById('settings-advanced').open`),false);
  await screenshot('settings-1366');
  await click('#primary-nav [data-view="artifacts"]');
  await until(`document.querySelectorAll('.ac-item').length > 0`);
  await screenshot('artifacts-1366');
  await click('.ac-name');
  await until(`document.querySelector('.ac-preview pre')`);
  assert.equal(await evaluate(`document.querySelector('.ac-preview pre').textContent`),'2026-09-12 INFO started\n2026-09-12 INFO ok\n');
  await evaluate('openArtifactCenter(700107)');
  await until(`document.getElementById('ac-message').value === '700107'`);
  await click('#primary-nav [data-view="timeline"]');
  await until(`document.querySelector('.timeline-round')`);
  await screenshot('timeline-1366');
  await click('#primary-nav [data-view="setup"]');
  assert.equal(await evaluate(`document.querySelectorAll('#setup-progress li').length`),4);
  for (let step=1;step<=4;step++) { await evaluate(`setupShowStep(${step})`); assert.equal(await evaluate(`document.querySelectorAll('#setup-steps > fieldset:not([hidden])').length`),1); }
  await evaluate('setupShowStep(1)');
  await screenshot('setup-1366');
  await click('#primary-nav [data-view="cockpit"]');
  await click('#hc-highrisk > summary');
  assert.equal(await evaluate(`document.getElementById('hc-highrisk').open`),true);
  writeFileSync(info.signal,'paused');
  await until(`document.getElementById('ce-state-label').textContent.includes('安全暂停')`);
  await screenshot('paused-1366');
  assert(!await evaluate(`document.getElementById('ce-since').textContent.includes('2026-')`));
  assert(await evaluate(`document.getElementById('ce-since').title.includes('2026-09-12T01:02')`));
  await evaluate(`document.getElementById('hc-highrisk').scrollIntoView({block:'center'})`);
  await screenshot('human-control-1366');
  assert(await evaluate(`document.querySelector('#primary-nav').getBoundingClientRect().top > 0`));
  await evaluate(`document.getElementById('ui-language').value='en';document.getElementById('ui-language').dispatchEvent(new Event('change'))`);
  await until(`document.documentElement.lang==='en' && document.getElementById('ce-state-label').textContent.includes('Paused')`);
  await screenshot('english-1366');
  assert.equal(consoleErrors.length,0,'Uncaught browser exceptions');
  writeFileSync(resolve(out,'browser-checks.json'),JSON.stringify({renderer:browserPath,checks,consoleErrors,result:'PASS'},null,2));
  console.log(JSON.stringify({result:'PASS',screenshots:checks.length,consoleErrors:consoleErrors.length}));
  await command('Browser.close');
} finally {
  if (socket) socket.close();
  if (browser && browser.exitCode === null) browser.kill();
  if (fixtureSignal && existsSync(fixtureSignal)) writeFileSync(fixtureSignal,'close');
  await new Promise(done => fixture.exitCode !== null ? done() : fixture.once('exit', done));
  // Only remove this run's verified disposable browser profile.
  if (profile && dirname(profile) === resolve(repo,'.test-tmp') && basename(profile).startsWith('ui-browser-')) {
    try { rmSync(profile,{recursive:true,force:true,maxRetries:10,retryDelay:200}); } catch (_) { /* A delayed browser exit may retain a disposable profile. */ }
  }
}
