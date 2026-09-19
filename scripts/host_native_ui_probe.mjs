/** Phase 6 test fixture: native Node/Chromium, independent of executor_work.
 * Executes the local prototype's JavaScript and takes screenshots in its work dir.
 * This is measurement code, not a Runtime operation or host isolation mechanism.
 */
import {spawn} from 'node:child_process';
import {mkdtemp, rm, writeFile} from 'node:fs/promises';
import {join, resolve, dirname, basename} from 'node:path';
import {pathToFileURL} from 'node:url';
import {once} from 'node:events';

const [browser, source, label] = process.argv.slice(2);
const work = process.cwd();
const delay = ms => new Promise(r => setTimeout(r, ms));
let child, ws, profile, nextId = 0;
const pending = new Map();
function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => {pending.delete(id); reject(Error('CDP timeout'));}, 5000);
    pending.set(id, {resolve, reject, timer});
    ws.send(JSON.stringify({id, method, params}));
  });
}
async function evaluate(expression) {
  const r = await command('Runtime.evaluate', {expression, returnByValue:true, awaitPromise:true});
  if (r.exceptionDetails) throw Error('Page evaluation failed');
  return r.result.value;
}
try {
  profile = await mkdtemp(join(work, 'browser-profile-'));
  child = spawn(browser, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-extensions',
    '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'],
    {windowsHide:true, stdio:['ignore', 'ignore', 'pipe']});
  const endpoint = await new Promise((resolve, reject) => {
    let log = '';
    const timer = setTimeout(() => reject(Error('Browser startup timeout')), 8000);
    child.on('error', e => {clearTimeout(timer); reject(e);});
    child.on('exit', () => {clearTimeout(timer); reject(Error('Browser exited'));});
    child.stderr.on('data', data => {
      log = (log + data).slice(-16000);
      const m = log.match(/DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/[^\s]+)/);
      if (m) {clearTimeout(timer); resolve(m[1]);}
    });
  });
  const tabs = await (await fetch('http://' + new URL(endpoint).host + '/json/list', {signal:AbortSignal.timeout(3000)})).json();
  ws = new WebSocket(tabs.find(t => t.type === 'page').webSocketDebuggerUrl);
  await once(ws, 'open');
  ws.addEventListener('message', event => {
    const packet = JSON.parse(event.data), p = pending.get(packet.id);
    if (!p) return;
    pending.delete(packet.id); clearTimeout(p.timer);
    packet.error ? p.reject(Error(packet.error.message)) : p.resolve(packet.result);
  });
  await command('Page.enable');
  const versions = await command('Browser.getVersion');
  const rounds = [];
  for (const [name, width, height] of [['mobile', 375, 812], ['desktop', 1280, 900]]) {
    await command('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor:1, mobile:false});
    await command('Page.navigate', {url:pathToFileURL(resolve(work, source)).href});
    for (let i = 0; i < 50; i++) {
      if (await evaluate("document.readyState === 'complete' && !!document.querySelector('form')")) break;
      await delay(50);
    }
    // Native CDP input dispatch exercises the page's real submit handler.
    await evaluate("document.querySelector('input').focus()");
    await command('Input.insertText', {text:'reader@example.test'});
    const button = await evaluate("(() => {const r=document.querySelector('button').getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2};})()");
    await command('Input.dispatchMouseEvent', {type:'mousePressed', button:'left', clickCount:1, ...button});
    await command('Input.dispatchMouseEvent', {type:'mouseReleased', button:'left', clickCount:1, ...button});
    const observation = await evaluate(`(() => {
      const input = document.querySelector('input'), button = document.querySelector('button');
      return {
        no_horizontal_overflow:document.documentElement.scrollWidth <= innerWidth,
        associated_label:!!input.labels.length,
        descriptive_action:button.innerText === 'Create account',
        controls_at_least_44px:input.getBoundingClientRect().height >= 44 && button.getBoundingClientRect().height >= 44,
        local_submit_handler:document.querySelector('[role=status]').innerText === 'Demo ready for reader@example.test',
        page_javascript_executed:window.demoReady === true,
        content_width:document.documentElement.scrollWidth, viewport_width:innerWidth
      };
    })()`);
    const screenshot = `evidence/native-${label}-${name}.png`;
    const png = (await command('Page.captureScreenshot', {format:'png', captureBeyondViewport:false})).data;
    await writeFile(join(work, screenshot), Buffer.from(png, 'base64'));
    rounds.push({viewport:{width, height}, observation, screenshot});
  }
  process.stdout.write(JSON.stringify({provider:'native Node/Chromium CDP', browser:versions.product,
    node:process.version, rounds, model_run:false, visual_judgment:false}));
} finally {
  if (ws?.readyState === WebSocket.OPEN) {try {await command('Browser.close');} catch {} ws.close();}
  for (const p of pending.values()) {clearTimeout(p.timer); p.reject(Error('Browser closed'));}
  if (child && child.exitCode === null) {
    await Promise.race([once(child, 'exit'), delay(1500)]);
    if (child.exitCode === null) {
      if (process.platform === 'win32') {
        const killer = spawn(join(process.env.SYSTEMROOT, 'System32/taskkill.exe'), ['/PID', String(child.pid), '/T', '/F'],
          {windowsHide:true, stdio:'ignore'});
        await once(killer, 'exit');
      } else child.kill('SIGKILL');
    }
  }
  if (profile) {
    const target = resolve(profile);
    if (dirname(target) !== resolve(work) || !basename(target).startsWith('browser-profile-')) throw Error('Invalid profile cleanup path');
    await rm(target, {recursive:true, force:true, maxRetries:3, retryDelay:200});
  }
}
