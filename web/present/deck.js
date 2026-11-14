/* Onset presentation: scenes, steps, keys, the three interactive checks and the live demo.
 *
 * Served by the dashboard (tools/dashboard.py) at /present, the live call is same-origin:
 * the middle scene embeds the dashboard and flips to it by itself when a call starts.
 * Opened any other way (a static server, GitHub Pages) it falls back to the browser demo.
 */
import { loadThree, makeWorld } from './world.js';

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const params = new URLSearchParams(location.search);

const SCENES = [
  { id: 'cover', steps: 1 }, { id: 'problem', steps: 2 }, { id: 'measure', steps: 3 }, { id: 'demo', steps: 2, star: true },
  { id: 'escalate', steps: 1 }, { id: 'how', steps: 1 }, { id: 'close', steps: 1 },
];
SCENES.forEach(s => { s.el = $(`#s-${s.id}`); s.label = s.el.dataset.label; });
const DEMO = SCENES.findIndex(s => s.id === 'demo');

const reduceQuery = matchMedia('(prefers-reduced-motion: reduce)');
let motion = !reduceQuery.matches && !params.has('still');
let world = null;
let si = 0, st = 0;

/* ───────── fit the 1920x1080 stage to the window */
const stage = $('#stage');
let scale = 1;
function fit() {
  const app = $('#app'), w = app.clientWidth || innerWidth, h = app.clientHeight || innerHeight;
  const s = Math.min(w / 1920, h / 1080);
  if (!s || s === scale) return;
  scale = s;
  stage.style.transform = `translate(-50%,-50%) scale(${scale})`;
  world?.resize(scale);
}
scale = 0; fit();
new ResizeObserver(fit).observe($('#app')); addEventListener('resize', fit);

/* ───────── the rail */
const rail = $('#rail');
SCENES.forEach((s, i) => {
  const b = document.createElement('button');
  b.innerHTML = `<i></i><span>${s.star ? 'Live demo' : s.label}</span>`;
  if (s.star) b.classList.add('star');
  b.title = s.label; b.addEventListener('click', () => show(i, 0));
  rail.append(b); s.dot = b;
});
$$('.node').forEach((n, i) => n.style.setProperty('--n', i));

/* ───────── navigation */
let flightTimer = 0;
function show(i, s = 0, { instant = false } = {}) {
  i = clamp(i, 0, SCENES.length - 1); s = clamp(s, 0, SCENES[i].steps - 1);
  const far = i !== si, dir = i >= si ? 1 : -1, prev = SCENES[si], cur = SCENES[i];
  if (!far && s === st && !instant) return;
  if (far) {
    prev.el.style.setProperty('--dir', dir);
    prev.el.classList.remove('on'); prev.el.classList.add('out');
    setTimeout(() => prev.el.classList.remove('out'), 420);
    cur.el.style.setProperty('--enter', instant || !motion ? '0s' : '.8s');
    if (motion && !instant) {
      const f = $('#flight'); $('#flight-n').textContent = `0${i + 1} / 0${SCENES.length}`; $('#flight-t').textContent = cur.label;
      f.classList.remove('go'); void f.offsetWidth; f.classList.add('go');
      clearTimeout(flightTimer); flightTimer = setTimeout(() => f.classList.remove('go'), 1600);
    }
  }
  leave(prev.id, far);
  si = i; st = s;
  cur.el.classList.add('on');
  $$('[data-only]', cur.el).forEach(e => e.classList.toggle('vis', +e.dataset.only === st));
  $$('[data-from]', cur.el).forEach(e => e.classList.toggle('vis', st >= +e.dataset.from));
  document.body.dataset.scene = cur.id; document.body.dataset.step = st;
  $('#brand-sec').textContent = cur.label;
  SCENES.forEach((sc, k) => { sc.dot.classList.toggle('cur', k === si); sc.dot.classList.toggle('done', k < si); });
  $('#prev').disabled = si === 0 && st === 0; $('#next').disabled = si === SCENES.length - 1 && st === cur.steps - 1;
  $('#notes-text').textContent = cur.el.dataset.notes || '';
  history.replaceState(null, '', `#${si + 1}${st ? '.' + (st + 1) : ''}`);
  world?.go(cur.id, st, instant);
  enter(cur.id, st, far);
}
const next = () => (st < SCENES[si].steps - 1 ? show(si, st + 1) : show(si + 1, 0));
const back = () => (st > 0 ? show(si, st - 1) : si > 0 ? show(si - 1, SCENES[si - 1].steps - 1) : null);
$('#next').addEventListener('click', next); $('#prev').addEventListener('click', back);

/* ───────── the idea: three checks you can act out */
const CHECKS = [
  { how: 'Big smile. Onset tracks how far each corner of the mouth lifts, left against right.', name: 'Smile asymmetry',
    base: .03, limit: .13, max: .30, val: v => .03 + v * .24, fmt: x => x.toFixed(2) },
  { how: 'Both arms straight out for ten seconds. Does one drift down more than the other?', name: 'Arm drift difference',
    base: .02, limit: .20, max: .50, val: v => .02 + v * .44, fmt: x => x.toFixed(2) },
  { how: 'Repeat one sentence: “You can’t teach an old dog new tricks.” Words, pace and pauses.', name: 'Longest pause',
    base: .3, limit: 1.0, max: 2.0, val: v => .3 + v * 1.5, fmt: x => x.toFixed(1) + ' s' },
];
const act = $('#act');
let v = 0, vTo = 0, acting = 0;
function meter() {
  const c = CHECKS[st] || CHECKS[0], x = c.val(v), over = x > c.limit;
  $('#meter-val').textContent = c.fmt(x);
  $('#meter-fill').style.width = `${clamp(x / c.max) * 100}%`;
  $('#verdict').textContent = over ? 'Change from your usual' : 'No change from your usual';
  $('.check').classList.toggle('over', over);
  document.body.classList.toggle('alerting', SCENES[si].id === 'measure' && over);
  act.style.setProperty('--p', `${v * 100}%`);
  if (world) world.state.v = v;
}
function setCheck() {
  const c = CHECKS[st];
  $('#check-how').textContent = c.how; $('#meter-name').textContent = c.name;
  $('#tick-base').style.left = `${c.base / c.max * 100}%`; $('#tick-limit').style.left = `${c.limit / c.max * 100}%`;
  $$('.tab').forEach((t, k) => t.classList.toggle('sel', k === st));
  v = vTo = 0; acting = 0; act.value = 0; meter();
}
act.addEventListener('input', () => { act.classList.add('touched'); acting = 0; v = vTo = act.value / 100; meter(); });
$$('.tab').forEach(t => t.addEventListener('click', () => show(si, +t.dataset.goStep)));
function actChange() { act.classList.add('touched'); acting = 1; vTo = vTo > .5 ? 0 : .92; }

/* ───────── when it changes: a sequence that plays itself */
let escT0 = 0, escHold = -1;
const flow = $$('#flow li');
flow.forEach(li => li.addEventListener('click', () => { escHold = +li.dataset.e; }));
function escalateTick(now) {
  const e = escHold >= 0 ? escHold + .999 : motion ? clamp((now - escT0) / 1000 - .9, 0, 20) / 1.7 : 4;
  flow.forEach(li => li.classList.toggle('lit', e >= +li.dataset.e));
  document.body.classList.toggle('alerting', true);
  if (world) world.state.esc = Math.min(e, 3.5);
}

/* ───────── the live demo */
const frame = $('#frame'), note = $('#frame-note');
const live = { base: null, ok: false, view: 'live', frames: {}, es: null, caughtUp: false };
async function findDashboard() {
  // same origin when the dashboard serves this page; else a dashboard on its usual port
  try { const r = await fetch('/healthz', { cache: 'no-store' }); if (r.ok && (await r.text()).trim() === 'ok') { live.base = ''; live.ok = true; } } catch { /* a static server */ }
  if (!live.ok) { try { await fetch('http://localhost:8765/healthz', { mode: 'no-cors', cache: 'no-store' }); live.base = 'http://localhost:8765'; live.ok = true; } catch { /* not running */ } }
  if (live.ok && live.base === '') { listen(); lineNumber(); }
  if (!live.ok) { status('Live dashboard not running: showing the browser demo', ''); live.view = 'try'; }
  $$('.seg button').forEach(b => b.classList.toggle('sel', b.dataset.view === live.view));
}
function status(text, cls = '') { for (const id of ['#call-status', '#live-status']) { const el = $(id); el.textContent = text; el.className = `status ${cls}`; } }
const OUTCOME = { no_change_detected: ['No change from the usual check-in', 'hot'], escalate: ['Change noticed: caregiver added, call 911', 'alert'], unclear: ['Unclear: caregiver added, call 911', 'alert'] };
function listen() {
  const es = new EventSource('/events'); live.es = es;
  es.addEventListener('caught_up', () => { live.caughtUp = true; });
  es.addEventListener('reset', e => { try { if (JSON.parse(e.data).live) live.caughtUp = true; } catch { /* ignore */ } });
  es.onmessage = e => {
    if (!live.caughtUp) return; // history from before this page opened
    let ev; try { ev = JSON.parse(e.data); } catch { return; }
    if (ev.type === 'run_started') { status(ev.has_baseline ? 'Call in progress' : 'Call in progress: recording the baseline', 'hot'); if (SCENES[si].id === 'demo' && st === 0) show(si, 1); }
    else if (ev.type === 'step' && ev.caption) status(`Onset: ${ev.caption}`, 'hot');
    else if (ev.type === 'decision') status(...(OUTCOME[ev.outcome] || [ev.outcome, '']));
    else if (ev.type === 'caregiver_join' && ev.status === 'joined') status('The caregiver joined the call', 'alert');
    else if (ev.type === 'run_ended') setTimeout(() => status('Call ended. Ready for the next one.', ''), 6000);
  };
}
async function lineNumber() {
  let n = params.get('line');
  if (!n) { try { n = (await (await fetch('/config', { cache: 'no-store' })).json()).line_number; } catch { /* none */ } }
  if (!n) return;
  const el = $('#line-number'); el.textContent = n; el.hidden = false;
  el.addEventListener('click', () => el.classList.toggle('masked'));
}
/* The dashboard asks for one click before it may play the agent's voice. The presenter has
   already clicked or pressed a key here, and a same-origin frame shares that, so pass it on. */
function unlockSound() {
  try {
    const gate = live.frames.live?.contentDocument?.getElementById('unlock');
    if (gate?.classList.contains('show') && navigator.userActivation?.hasBeenActive) gate.click();
  } catch { /* another origin: the presenter clicks it */ }
}
function mount(view) {
  live.view = view;
  $$('.seg button').forEach(b => b.classList.toggle('sel', b.dataset.view === view));
  if (view === 'live' && !live.ok) {
    note.hidden = false; note.innerHTML = 'The live dashboard is not running on this computer.<br>Start everything with <code>tools/demo.sh --present</code>, or press <code>B</code> for the browser demo.';
    Object.values(live.frames).forEach(f => { f.style.display = 'none'; }); return;
  }
  note.hidden = true;
  if (!live.frames[view]) {
    const f = document.createElement('iframe');
    f.title = view === 'live' ? 'Onset live dashboard' : 'Onset browser demo';
    if (view === 'try') f.allow = 'camera; microphone';
    f.src = view === 'live' ? `${live.base}/` : '../try/index.html';
    f.addEventListener('load', () => { if (view === 'live') setTimeout(unlockSound, 400); try { f.contentWindow.addEventListener('keydown', e => { if (e.key === 'PageDown' || e.key === 'Escape') { e.preventDefault(); show(si + 1, 0); } else if (e.key === 'PageUp') { e.preventDefault(); show(si, 0); } }); } catch { /* another origin */ } });
    frame.append(f); live.frames[view] = f;
  }
  Object.entries(live.frames).forEach(([k, f]) => { f.style.display = k === view ? 'block' : 'none'; });
  if (view === 'live') unlockSound();
}
$$('.seg button').forEach(b => b.addEventListener('click', () => mount(b.dataset.view)));
$('#open-live').addEventListener('click', () => show(DEMO, 1));
$('#leave-live').addEventListener('click', () => show(DEMO + 1, 0));

/* ───────── scene hooks */
function enter(id, step, far) {
  if (id === 'measure') setCheck();
  if (id === 'escalate') { escT0 = performance.now(); escHold = -1; }
  if (id === 'demo') { if (world) world.state.demoOpen = step === 1; if (step === 1) mount(live.view); }
  if (id !== 'measure' && id !== 'escalate') document.body.classList.remove('alerting');
}
function leave(id, far) {
  if (id === 'demo' && far && world) world.state.demoOpen = false;
}

/* ───────── per-frame: counters and tweens that follow the world */
const dayN = $('#day-n');
let t0 = performance.now();
function tick(now) {
  requestAnimationFrame(tick);
  const id = SCENES[si].id;
  if (id === 'problem') dayN.textContent = world ? world.state.dayLit : st ? 90 : 0;
  if (id === 'measure' && acting) { v += (vTo - v) * .06; if (Math.abs(vTo - v) < .004) { v = vTo; acting = 0; } act.value = v * 100; meter(); }
  if (id === 'escalate') escalateTick(now);
  if (!$('#notes').hidden) { const s = (now - t0) / 1000 | 0; $('#clock').textContent = `${s / 60 | 0}:${String(s % 60).padStart(2, '0')}`; }
}
requestAnimationFrame(tick);

/* ───────── pointer: the world and the cards lean toward it */
stage.addEventListener('pointermove', e => {
  const r = stage.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
  stage.style.setProperty('--mx', x); stage.style.setProperty('--my', y);
  if (world) world.state.pointer = [x * 2 - 1, y * 2 - 1];
});
$$('[data-tilt]').forEach(el => {
  el.addEventListener('pointermove', e => { if (!motion) return; const r = el.getBoundingClientRect(); el.style.setProperty('--tx', ((e.clientX - r.left) / r.width - .5) * 14); el.style.setProperty('--ty', -((e.clientY - r.top) / r.height - .5) * 12); });
  el.addEventListener('pointerleave', () => { el.style.setProperty('--tx', 0); el.style.setProperty('--ty', 0); });
});
stage.addEventListener('pointerdown', e => {
  if (!motion || e.target.closest('iframe')) return;
  const r = stage.getBoundingClientRect(), d = document.createElement('div');
  d.className = 'ripple'; d.style.left = `${(e.clientX - r.left) / scale}px`; d.style.top = `${(e.clientY - r.top) / scale}px`;
  stage.append(d); setTimeout(() => d.remove(), 950);
});
let touchX = null;
stage.addEventListener('touchstart', e => { touchX = e.touches[0].clientX; }, { passive: true });
stage.addEventListener('touchend', e => { if (touchX == null) return; const dx = e.changedTouches[0].clientX - touchX; touchX = null; if (Math.abs(dx) > 70 && !e.target.closest('input,iframe,button,a')) (dx < 0 ? next : back)(); }, { passive: true });

/* ───────── keys */
function setMotion(on) { motion = on; document.body.classList.toggle('still', !on); world?.setMotion(on); }
addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const id = SCENES[si].id, k = e.key, inRange = e.target === act;
  if (k === 'ArrowRight' || k === 'PageDown' || k === ' ' || k === 'Enter') { if (inRange && k === 'ArrowRight') return; e.preventDefault(); next(); }
  else if (k === 'ArrowLeft' || k === 'PageUp' || k === 'Backspace') { if (inRange && k === 'ArrowLeft') return; e.preventDefault(); back(); }
  else if (k === 'Home') show(0, 0);
  else if (k === 'End') show(SCENES.length - 1, 0);
  else if (k === 'd' || k === 'D') show(DEMO, 0);
  else if (k === 'f' || k === 'F') { try { document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen(); } catch { /* not allowed */ } }
  else if (k === 'n' || k === 'N') { $('#notes').hidden = !$('#notes').hidden; }
  else if (k === 't' || k === 'T') t0 = performance.now();
  else if (k === 'm' || k === 'M') setMotion(!motion);
  else if (k === '?' || k === 'h' || k === 'H') $('#help').hidden = !$('#help').hidden;
  else if (k === 'Escape') $('#help').hidden = true;
  else if (id === 'measure' && '123'.includes(k)) show(si, +k - 1);
  else if (id === 'measure' && (k === 'a' || k === 'A')) actChange();
  else if (id === 'escalate' && (k === 'r' || k === 'R')) { escT0 = performance.now(); escHold = -1; }
  else if (id === 'demo' && (k === 'l' || k === 'L')) { if (st === 0) show(si, 1); mount('live'); }
  else if (id === 'demo' && (k === 'b' || k === 'B')) { if (st === 0) show(si, 1); mount('try'); }
});

/* ───────── start */
(async () => {
  setMotion(motion);
  reduceQuery.addEventListener('change', () => setMotion(!reduceQuery.matches));
  try {
    const THREE = await loadThree();
    world = makeWorld(THREE, $('#gl'), { reduced: !motion });
    world.resize(scale);
    $('#gl').addEventListener('webglcontextlost', () => document.body.classList.add('no-gl'));
  } catch (err) {
    console.warn('3D unavailable, the slides still work:', err);
    document.body.classList.add('no-gl');
  }
  await findDashboard();
  const m = /^#(\d+)(?:\.(\d+))?$/.exec(location.hash);
  show(m ? +m[1] - 1 : 0, m && m[2] ? +m[2] - 1 : 0, { instant: true });
  addEventListener('hashchange', () => { const h = /^#(\d+)(?:\.(\d+))?$/.exec(location.hash); if (h) show(+h[1] - 1, h[2] ? +h[2] - 1 : 0); });
  window.onsetDeck = { show, next, back, world, live };
})();
