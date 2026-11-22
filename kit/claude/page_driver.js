// A page driver for the kit's offline page checks, with no npm dependency required:
//   1. Playwright, when it is installed (require('playwright'));
//   2. else any Chromium-family browser already on the computer (Chrome, Chromium, Edge, Brave; or $KIT_BROWSER),
//      driven over the DevTools protocol with Node's built-in WebSocket and fetch (Node >= 22);
//   3. else null: the caller falls back to a syntax-only check of the page's scripts.
// Interface: { kind, open(html), evaluate(fn | string, arg?), press(key), wait(ms), errors: [], close() }.
const fs = require('fs');
const os = require('os');
const path = require('path');
const http = require('http');
const { spawn, execFileSync } = require('child_process');

function servePage(html) {
  return new Promise((resolve) => {
    const srv = http.createServer((req, res) => {
      if (req.url === '/' || req.url.startsWith('/?')) {
        res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
        res.end(html);
      } else {
        res.writeHead(404);
        res.end();
      }
    });
    srv.listen(0, '127.0.0.1', () => resolve({ srv, url: 'http://127.0.0.1:' + srv.address().port + '/' }));
  });
}

function asExpression(fn, arg) {
  const body = typeof fn === 'function' ? fn.toString() : 'function () { return (' + fn + '); }';
  return '(' + body + ')(' + (arg === undefined ? '' : JSON.stringify(arg)) + ')';
}

async function viaPlaywright() {
  let chromium;
  try {
    ({ chromium } = require('playwright'));
  } catch (e) {
    return null;
  }
  let b;
  try {
    b = await chromium.launch();
  } catch (e) {
    return null;
  }
  const ctx = await b.newContext({ viewport: { width: 1300, height: 900 } });
  const p = await ctx.newPage();
  const d = { kind: 'playwright', errors: [] };
  p.on('pageerror', (e) => d.errors.push(String(e)));
  p.on('console', (m) => { if (m.type() === 'error') d.errors.push('console: ' + m.text()); });
  d.open = async (html) => {
    await p.route('**/*', (r) => (r.request().url().startsWith('http://kit.test') ? r.fulfill({ body: html, contentType: 'text/html; charset=utf-8' }) : r.abort()));
    await p.goto('http://kit.test/');
  };
  d.evaluate = (fn, arg) => p.evaluate(asExpression(fn, arg));
  d.press = (key) => p.keyboard.press(key);
  d.wait = (ms) => p.waitForTimeout(ms);
  d.close = () => b.close();
  return d;
}

function findBrowser() {
  const env = process.env.KIT_BROWSER;
  if (env && fs.existsSync(env)) return env;
  const names = ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser', 'microsoft-edge', 'microsoft-edge-stable', 'msedge', 'brave-browser', 'chrome'];
  const fixed = [
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    '/Applications/Chromium.app/Contents/MacOS/Chromium',
    '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
    '/Applications/Brave Browser.app/Contents/MacOS/Brave Browser',
    'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe',
    'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
    'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  ];
  for (const f of fixed) if (fs.existsSync(f)) return f;
  for (const n of names) {
    try {
      const out = execFileSync(process.platform === 'win32' ? 'where' : 'which', [n], { stdio: ['ignore', 'pipe', 'ignore'] }).toString().split(/\r?\n/)[0].trim();
      if (out) return out;
    } catch (e) { /* not on PATH */ }
  }
  return null;
}

async function viaDevTools() {
  if (typeof WebSocket !== 'function' || typeof fetch !== 'function') return null;
  const bin = findBrowser();
  if (!bin) return null;
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'kit-page-'));
  const proc = spawn(bin, ['--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--remote-debugging-port=0', '--user-data-dir=' + profile, '--window-size=1300,900', 'about:blank'],
  { stdio: ['ignore', 'ignore', 'pipe'] });
  const wsBrowser = await new Promise((resolve) => {
    let buf = '';
    const t = setTimeout(() => resolve(null), 15000);
    proc.stderr.on('data', (c) => {
      buf += c;
      const m = buf.match(/DevTools listening on (ws:\/\/\S+)/);
      if (m) { clearTimeout(t); resolve(m[1]); }
    });
    proc.on('exit', () => { clearTimeout(t); resolve(null); });
  });
  if (!wsBrowser) { try { proc.kill(); } catch (e) { /* gone */ } return null; }
  const base = 'http://' + new URL(wsBrowser).host;
  const targets = await (await fetch(base + '/json/list')).json();
  const page = targets.find((t) => t.type === 'page');
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
  let id = 0;
  const pending = new Map();
  const d = { kind: 'devtools (' + path.basename(bin) + ')', errors: [] };
  ws.onmessage = (ev) => {
    const msg = JSON.parse(typeof ev.data === 'string' ? ev.data : ev.data.toString());
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(msg.error.message)); else resolve(msg.result);
      return;
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      const x = msg.params.exceptionDetails;
      d.errors.push(String((x.exception && x.exception.description) || x.text));
    } else if (msg.method === 'Runtime.consoleAPICalled' && msg.params.type === 'error') {
      d.errors.push('console: ' + msg.params.args.map((a) => (a.value !== undefined ? a.value : a.description)).join(' '));
    } else if (msg.method === 'Log.entryAdded' && msg.params.entry.level === 'error') {
      d.errors.push('console: ' + msg.params.entry.text);
    }
  };
  const send = (method, params) => new Promise((resolve, reject) => {
    const n = ++id;
    pending.set(n, { resolve, reject });
    ws.send(JSON.stringify({ id: n, method, params: params || {} }));
  });
  await send('Runtime.enable');
  await send('Log.enable');
  await send('Page.enable');
  let server = null;
  d.open = async (html) => {
    server = await servePage(html);
    await send('Page.navigate', { url: server.url });
    await new Promise((r) => setTimeout(r, 300));
  };
  d.evaluate = async (fn, arg) => {
    const r = await send('Runtime.evaluate', { expression: asExpression(fn, arg), awaitPromise: true, returnByValue: true, userGesture: true });
    if (r.exceptionDetails) throw new Error((r.exceptionDetails.exception && r.exceptionDetails.exception.description) || r.exceptionDetails.text);
    return r.result.value;
  };
  d.press = async (key) => {
    // One printable key (the page tests press single letters), as a real keyboard event reaches the page.
    const code = key.length === 1 ? 'Key' + key.toUpperCase() : key;
    const vk = key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0;
    await send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, text: key.length === 1 ? key : undefined, windowsVirtualKeyCode: vk });
    await send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: vk });
  };
  d.wait = (ms) => new Promise((r) => setTimeout(r, ms));
  d.close = async () => {
    try { ws.close(); } catch (e) { /* closed */ }
    if (server) server.srv.close();
    try { proc.kill(); } catch (e) { /* gone */ }
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) { /* busy on Windows */ }
  };
  return d;
}

// The fallback when no browser can run: compile every inline <script> (syntax only, nothing executes).
function syntaxCheck(html) {
  const vm = require('vm');
  const errors = [];
  const re = /<script(\s[^>]*)?>([\s\S]*?)<\/script>/gi;
  let m;
  let n = 0;
  while ((m = re.exec(html))) {
    const attrs = m[1] || '';
    if (/\bsrc=/.test(attrs) || /type=["'](?!text\/javascript|module)[^"']+["']/.test(attrs)) continue;
    n += 1;
    try {
      new vm.Script(m[2], { filename: 'inline-script-' + n + '.js' });
    } catch (e) {
      if (/type=["']module["']/.test(attrs) && /import|export/.test(String(e))) continue;
      errors.push('script ' + n + ': ' + String(e));
    }
  }
  return { scripts: n, errors };
}

async function launch() {
  if (process.env.KIT_PAGE_DRIVER !== 'devtools') {
    const pw = await viaPlaywright();
    if (pw) return pw;
  }
  if (process.env.KIT_PAGE_DRIVER !== 'playwright') {
    return viaDevTools();
  }
  return null;
}

module.exports = { launch, syntaxCheck, findBrowser };
