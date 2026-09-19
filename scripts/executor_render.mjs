/** Fixed static-render worker. No arbitrary evaluation, URL, profile or command API.
 * Chromium is a trusted host dependency, not an OS sandbox supplied by Runtime.
 */
import {spawn} from 'node:child_process';
import {mkdtemp, rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join, resolve, dirname, basename} from 'node:path';
import {once} from 'node:events';

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
const chunks = [];
let inputBytes = 0;
for await (const chunk of process.stdin) {
  inputBytes += chunk.length;
  if (inputBytes > 2 * 1024 * 1024) throw new Error('oversized worker input');
  chunks.push(chunk);
}
let child, ws, profile, result, nextId = 0;
const pending = new Map();
const url = 'https://runtime.invalid/snapshot';
let served = false, blockedRequests = 0;
const csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; script-src 'none'; connect-src 'none'; frame-src 'none'; object-src 'none'; worker-src 'none'; base-uri 'none'; form-action 'none'; sandbox";
function command(method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = ++nextId;
    const timer = setTimeout(() => { pending.delete(id); reject(new Error('CDP timeout')); }, 4000);
    pending.set(id, {resolve, reject, timer});
    ws.send(JSON.stringify({id, method, params}));
  });
}
// Only Runtime-authored observation code is evaluated, in an isolated world.
const inspect = `(() => {
  if (document.querySelectorAll('*').length > 5000) throw Error('DOM exceeds 5000 elements');
  const rect = e => { const r = e.getBoundingClientRect(); return {x:r.x,y:r.y,width:r.width,height:r.height}; };
  const visible = e => !!e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden' && getComputedStyle(e).display !== 'none';
  const controls = [...document.querySelectorAll('input,select,textarea,button,a[href]')];
  if (controls.length > 200) throw Error('too many controls');
  const text = document.body?.innerText || '';
  return {title:document.title.slice(0,200), text:text.slice(0,16000), text_truncated:text.length>16000,
    viewport:{width:innerWidth,height:innerHeight}, content:{width:document.documentElement.scrollWidth,height:document.documentElement.scrollHeight},
    horizontal_overflow:document.documentElement.scrollWidth > innerWidth,
    script_elements:document.scripts.length,
    external_resource_elements:document.querySelectorAll('script[src],link[href],iframe,object,embed,img[src]:not([src^="data:"])').length,
    controls:controls.map(e=>({tag:e.tagName.toLowerCase(),type:e.getAttribute('type'),id:e.id.slice(0,200),
      text:(e.innerText||'').slice(0,200),labels:[...(e.labels||[])].map(l=>l.innerText.slice(0,200)),
      aria_label:(e.getAttribute('aria-label')||'').slice(0,200),visible:visible(e),disabled:!!e.disabled,rect:rect(e)}))};
})()`;

try {
  const request = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  if (typeof WebSocket !== 'function' || typeof request.browser !== 'string' || typeof request.html !== 'string' ||
      Buffer.byteLength(request.html) > 262144 || !Number.isInteger(request.width) || request.width < 320 || request.width > 1920 ||
      !Number.isInteger(request.height) || request.height < 320 || request.height > 1080) throw new Error('invalid provider inputs or Node version');
  profile = await mkdtemp(join(tmpdir(), 'runtime-render-'));
  child = spawn(request.browser, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-background-networking', '--disable-component-update', '--disable-sync', '--disable-extensions',
    '--disable-default-apps', '--no-proxy-server', '--host-resolver-rules=MAP * ~NOTFOUND',
    '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0', '--user-data-dir=' + profile, 'about:blank'],
    {windowsHide:true, stdio:['ignore','ignore','pipe']});
  const endpoint = await new Promise((resolve,reject) => {
    let log = '';
    const timer = setTimeout(()=>reject(new Error('browser startup timeout')), 7000);
    child.on('error', e=>{clearTimeout(timer);reject(e);});
    child.on('exit', ()=>{clearTimeout(timer);reject(new Error('browser exited'));});
    child.stderr.on('data', data=>{
      log = (log + data).slice(-16000);
      const match = log.match(/DevTools listening on (ws:\/\/127\.0\.0\.1:\d+\/[^\s]+)/);
      if (match) {clearTimeout(timer);resolve(match[1]);}
    });
  });
  const tabs = await (await fetch('http://' + new URL(endpoint).host + '/json/list', {signal:AbortSignal.timeout(3000)})).json();
  const tab = tabs.find(t=>t.type==='page');
  if (!tab || new URL(tab.webSocketDebuggerUrl).host !== new URL(endpoint).host) throw new Error('invalid local browser endpoint');
  ws = new WebSocket(tab.webSocketDebuggerUrl);
  await Promise.race([once(ws, 'open'), delay(3000).then(()=>{throw Error('CDP open timeout');})]);
  ws.addEventListener('message', event => {
    const packet = JSON.parse(event.data);
    if (packet.id && pending.has(packet.id)) {
      const p = pending.get(packet.id); pending.delete(packet.id); clearTimeout(p.timer);
      packet.error ? p.reject(new Error('CDP command failed')) : p.resolve(packet.result);
    }
    if (packet.method === 'Fetch.requestPaused') {
      const p = packet.params;
      if (!served && p.request.url === url && p.resourceType === 'Document') {
        served = true;
        command('Fetch.fulfillRequest', {requestId:p.requestId, responseCode:200,
          responseHeaders:[{name:'Content-Type',value:'text/html; charset=utf-8'},
            {name:'Content-Security-Policy',value:csp},{name:'Cache-Control',value:'no-store'}],
          body:Buffer.from(request.html).toString('base64')}).catch(()=>{});
      } else {blockedRequests++;command('Fetch.failRequest',{requestId:p.requestId,errorReason:'BlockedByClient'}).catch(()=>{});}
    }
  });
  await command('Page.enable');
  await command('Network.enable');
  await command('Network.setBypassServiceWorker', {bypass:true});
  await command('Page.setDownloadBehavior', {behavior:'deny'});
  await command('Emulation.setScriptExecutionDisabled', {value:true});
  await command('Emulation.setDeviceMetricsOverride', {width:request.width,height:request.height,deviceScaleFactor:1,mobile:false});
  await command('Fetch.enable', {patterns:[{urlPattern:'*',requestStage:'Request'}]});
  await command('Page.navigate', {url});
  let frame;
  for (let i=0;i<25;i++) {
    frame = (await command('Page.getFrameTree')).frameTree.frame;
    if (served && frame.url === url) break;
    await delay(40);
  }
  if (!served || frame.url !== url) throw Error('snapshot navigation failed');
  await delay(150);
  const world = await command('Page.createIsolatedWorld', {frameId:frame.id,worldName:'runtime-inspection'});
  const evaluated = await command('Runtime.evaluate', {expression:inspect, contextId:world.executionContextId,returnByValue:true});
  if (evaluated.exceptionDetails || !evaluated.result?.value) throw Error('static DOM inspection failed');
  const png = (await command('Page.captureScreenshot', {format:'png',captureBeyondViewport:false,
    clip:{x:0,y:0,width:request.width,height:request.height,scale:1}})).data;
  if ((await command('Page.getFrameTree')).frameTree.frame.url !== url) throw Error('snapshot navigated during inspection');
  result = {ok:true, ...evaluated.result.value, blocked_requests:blockedRequests,
    rendering:'Static HTML/CSS; page scripts and external resources disabled; viewport screenshot only', png};
} catch (error) {
  result = {ok:false,reason:'Static renderer failed: ' + (error.message || 'provider error').slice(0,120)};
} finally {
  if (ws?.readyState === WebSocket.OPEN) {try {await command('Browser.close');} catch {} ws.close();}
  for (const p of pending.values()) {clearTimeout(p.timer);p.reject(new Error('renderer closed'));}
  pending.clear();
  if (child && child.exitCode === null) {
    await Promise.race([once(child,'exit'),delay(1500)]);
    if (child.exitCode === null) {
      if (process.platform === 'win32') {
        const killer = spawn(join(process.env.SYSTEMROOT,'System32','taskkill.exe'), ['/PID',String(child.pid),'/T','/F'],
          {windowsHide:true,stdio:'ignore'});
        await Promise.race([once(killer,'exit'),delay(2000)]);
      } else child.kill('SIGKILL');
    }
  }
  if (profile) {try {
    const target = resolve(profile);
    if (dirname(target) !== resolve(tmpdir()) || !basename(target).startsWith('runtime-render-')) {
      throw Error('invalid owned profile cleanup path');
    }
    await rm(target,{recursive:true,force:true,maxRetries:3,retryDelay:200});}
    catch {result={ok:false,reason:'renderer profile cleanup failed'};}}
}
process.stdout.write(JSON.stringify(result));
