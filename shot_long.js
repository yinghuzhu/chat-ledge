#!/usr/bin/env node
// 用 Chrome DevTools Protocol 把本地 HTML 渲染成完整长图 PNG（无第三方依赖）。
// 用法: node shot_long.js <input.html> <output.png> [width=1080] [scale=2] [clipY=0] [clipH=auto]
// 注意: 本机沙盒环境启动 Chrome 必须带 --no-sandbox，否则静默失败。
const { spawn } = require('child_process');
const fs = require('fs');

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333 + (process.pid % 200);
const htmlPath = process.argv[2];
const outPath = process.argv[3];
const WIDTH = parseInt(process.argv[4] || '1080', 10);
const SCALE = parseFloat(process.argv[5] || '2');
if (!htmlPath || !outPath) { console.error('usage: node shot_long.js <html> <png> [width] [scale]'); process.exit(2); }
const url = 'file://' + encodeURI(htmlPath).replace(/#/g, '%23');
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function waitPort() {
  for (let i = 0; i < 80; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return await r.json(); }
    catch (e) {}
    await sleep(250);
  }
  throw new Error('chrome devtools not ready');
}

(async () => {
  const profile = '/tmp/cdp-shot-' + Date.now() + '-' + process.pid;
  const chrome = spawn(CHROME, [
    '--headless=new', '--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage',
    '--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check',
    '--force-device-scale-factor=1', '--allow-file-access-from-files',
    `--user-data-dir=${profile}`, `--remote-debugging-port=${PORT}`, 'about:blank',
  ], { stdio: 'ignore' });
  try {
    const ver = await waitPort();
    const ws = new WebSocket(ver.webSocketDebuggerUrl);
    await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
    let id = 0; const pending = new Map(); const events = [];
    ws.onmessage = m => {
      const msg = JSON.parse(m.data);
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id); pending.delete(msg.id);
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result);
      } else if (msg.method) events.push(msg);
    };
    const send = (method, params = {}, sessionId) => new Promise((res, rej) => {
      const mid = ++id; pending.set(mid, { res, rej });
      ws.send(JSON.stringify({ id: mid, method, params, sessionId }));
    });
    const { targetId } = await send('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await send('Target.attachToTarget', { targetId, flatten: true });
    const S = (m, p) => send(m, p, sessionId);
    await S('Page.enable'); await S('Runtime.enable');
    await S('Emulation.setDeviceMetricsOverride', { width: WIDTH, height: 1200, deviceScaleFactor: 1, mobile: false });
    await S('Page.navigate', { url });
    for (let i = 0; i < 100; i++) {
      if (events.some(e => e.method === 'Page.loadEventFired')) break;
      await sleep(100);
    }
    await sleep(600);
    await S('Runtime.evaluate', { expression: 'document.fonts && document.fonts.ready', awaitPromise: true });
    const { result } = await S('Runtime.evaluate', {
      expression: 'Math.ceil(document.documentElement.scrollHeight)', returnByValue: true,
    });
    const H = result.value;
    const clipY = parseInt(process.argv[6] || '0', 10);
    const clipH = process.argv[7] ? parseInt(process.argv[7], 10) : (H - clipY);
    await S('Emulation.setDeviceMetricsOverride', {
      width: WIDTH, height: Math.min(clipH, H), deviceScaleFactor: SCALE, mobile: false,
    });
    await sleep(350);
    const shot = await S('Page.captureScreenshot', {
      format: 'png', captureBeyondViewport: true,
      clip: { x: 0, y: clipY, width: WIDTH, height: clipH, scale: 1 },
    });
    fs.writeFileSync(outPath, Buffer.from(shot.data, 'base64'));
    console.log(`${outPath}  ${WIDTH}x${clipH} @${SCALE}x  page=${H}  ${(fs.statSync(outPath).size / 1048576).toFixed(2)}MB`);
    ws.close();
  } finally {
    chrome.kill('SIGKILL');
    try { fs.rmSync(profile, { recursive: true, force: true }); } catch (e) {}
  }
})().catch(e => { console.error('ERR', e.message); process.exit(1); });
