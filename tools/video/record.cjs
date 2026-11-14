// Record a web page to an MP4 (1920x1080, 30 fps) with Chrome's screencast, for the demo video.
//
//   node tools/video/record.cjs --url URL --out DIR [--seconds 20] [--dsf 1]
//        [--run "wsl.exe -d Ubuntu -- bash /home/.../demo_run.sh escalate"] [--run-at 1.5] [--tail 3]
//        [--keys "2:ArrowRight,6:ArrowRight"] [--pre 1.2] [--headed]
//
// Needs `playwright` (uses the installed Chrome, channel "chrome") and `ffmpeg-static`, found
// through NODE_PATH. Writes DIR/frames/*.jpg, DIR/frames.ffconcat and DIR/clip.mp4.
// With --run, the command starts --run-at seconds after recording begins and recording stops
// --tail seconds after it exits (or at --seconds, whichever is later).

const fs = require('fs');
const path = require('path');
const { spawn, execFileSync } = require('child_process');
const { chromium } = require('playwright');
const ffmpeg = require('ffmpeg-static');

function args() {
  const a = { seconds: 20, dsf: 1, runAt: 1.5, tail: 3, keys: '', w: 1920, h: 1080, quality: 92, pre: 1.2 };
  const v = process.argv.slice(2);
  for (let i = 0; i < v.length; i++) {
    const k = v[i].replace(/^--/, '');
    if (k === 'headed') { a.headed = true; continue; }
    const val = v[++i];
    const map = { url: 'url', out: 'out', seconds: 'seconds', dsf: 'dsf', run: 'run', 'run-at': 'runAt', tail: 'tail', keys: 'keys', w: 'w', h: 'h', quality: 'quality', wait: 'wait', pre: 'pre' };
    if (!map[k]) throw new Error(`unknown flag --${k}`);
    a[map[k]] = ['url', 'out', 'run', 'keys', 'wait'].includes(map[k]) ? val : Number(val);
  }
  if (!a.url || !a.out) throw new Error('--url and --out are required');
  return a;
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

(async () => {
  const a = args();
  const frames = path.join(a.out, 'frames');
  fs.rmSync(frames, { recursive: true, force: true });
  fs.mkdirSync(frames, { recursive: true });

  const browser = await chromium.launch({
    channel: 'chrome', headless: !a.headed,
    args: ['--enable-gpu', '--ignore-gpu-blocklist', '--enable-webgl', '--autoplay-policy=no-user-gesture-required', '--hide-scrollbars'],
  });
  const ctx = await browser.newContext({ viewport: { width: a.w, height: a.h }, deviceScaleFactor: a.dsf, colorScheme: 'dark' });
  const page = await ctx.newPage();
  page.on('pageerror', e => console.error('pageerror:', e.message));
  await page.goto(a.url, { waitUntil: 'load' });
  if (a.wait) await page.waitForSelector(a.wait, { timeout: 15000 }).catch(() => {});
  await sleep(a.pre * 1000);

  const cdp = await ctx.newCDPSession(page);
  const shots = [];
  let n = 0;
  cdp.on('Page.screencastFrame', async f => {
    const name = `${String(n++).padStart(6, '0')}.jpg`;
    fs.writeFileSync(path.join(frames, name), Buffer.from(f.data, 'base64'));
    shots.push({ name, t: f.metadata.timestamp });
    try { await cdp.send('Page.screencastFrameAck', { sessionId: f.sessionId }); } catch {}
  });
  await cdp.send('Page.startScreencast', { format: 'jpeg', quality: a.quality, maxWidth: a.w * a.dsf, maxHeight: a.h * a.dsf, everyNthFrame: 1 });
  const t0 = Date.now();

  // Keys at fixed times ("2:ArrowRight,6.5:Digit3").
  const keyTimers = (a.keys ? a.keys.split(',') : []).map(s => {
    const [t, key] = s.split(':');
    return setTimeout(() => page.keyboard.press(key).catch(() => {}), Number(t) * 1000);
  });

  let child = null, childDone = Promise.resolve();
  if (a.run) {
    await sleep(a.runAt * 1000);
    child = spawn(a.run, { shell: true, stdio: 'inherit', env: { ...process.env, MSYS_NO_PATHCONV: '1' } });
    childDone = new Promise(r => child.on('exit', r));
  }
  const minEnd = sleep(a.seconds * 1000);
  await Promise.all([minEnd, childDone.then(() => sleep(a.tail * 1000))]);
  keyTimers.forEach(clearTimeout);
  // Force one last frame so the tail is covered.
  const end = Date.now();
  await cdp.send('Page.stopScreencast');
  await sleep(300);
  await browser.close();

  if (!shots.length) throw new Error('no frames captured');
  const lines = ['ffconcat version 1.0'];
  for (let i = 0; i < shots.length; i++) {
    const next = i + 1 < shots.length ? shots[i + 1].t : shots[0].t + (end - t0) / 1000;
    const d = Math.max(0.001, next - shots[i].t);
    lines.push(`file 'frames/${shots[i].name}'`, `duration ${d.toFixed(4)}`);
  }
  lines.push(`file 'frames/${shots[shots.length - 1].name}'`);
  fs.writeFileSync(path.join(a.out, 'frames.ffconcat'), lines.join('\n') + '\n');
  const span = (shots[shots.length - 1].t - shots[0].t).toFixed(1);
  console.log(`${shots.length} frames over ${span}s (${(shots.length / Math.max(1, span)).toFixed(1)} fps captured)`);

  execFileSync(ffmpeg, ['-y', '-hide_banner', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', 'frames.ffconcat',
    '-vf', `fps=30,scale=${a.w * a.dsf}:${a.h * a.dsf}:flags=lanczos,format=yuv420p`, '-c:v', 'libx264', '-preset', 'medium', '-crf', '14',
    '-r', '30', 'clip.mp4'], { cwd: a.out, stdio: 'inherit' });
  console.log('wrote', path.join(a.out, 'clip.mp4'));
})().catch(e => { console.error(e); process.exit(1); });
