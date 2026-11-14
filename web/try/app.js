// Try Onset: the one-minute check-in, in the browser. Everything runs on this device: MediaPipe
// Tasks Vision (WebAssembly + WebGL) reads the webcam; nothing is recorded or uploaded. The lines
// are the agent's own (onset/script.py), the maths is measure.js (a port of onset/measure/*.py and
// onset/speech.py) and the thresholds are onset/decide.py's.
import * as M from './measure.js';

// ------------------------------------------------------------------ configuration

const MP_VERSION = '0.10.14';
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MODELS = {
  face: 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task',
  pose: 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task',
};
const SENTENCE = 'The sky is blue in Cincinnati.';
const VIDEO_URL = 'https://youtu.be/L58lr_4DPik';
const SKIP_AFTER_MS = 15000;      // offer "Skip this step" after this long without a usable picture
const GIVE_UP_MS = 75000;         // and move on by itself after this long
const ARMS_WAIT_MS = 40000;       // arms never raised: "That's all right. Let's move on."
const SAY_IT_MS = 9000;           // the sentence window

// Every line comes from onset/script.py: say = spoken, caption = shown on the video.
const L = {
  GREETING: { say: "Hello, this is Onset, your check-in helper. This takes about one minute. Let's begin.", caption: 'Hello, this is Onset. A one-minute check-in.' },
  FACE_NEUTRAL: { say: 'Please look at the camera, and relax your face.', caption: 'Look here. Relax your face.' },
  FACE_SMILE: { say: 'Now give me a big smile, and hold it.', caption: 'Big smile! Hold it.' },
  ARMS_RAISE: { say: 'Now raise both arms straight out in front of you, and keep them up.', caption: 'Raise both arms straight out. Keep them up.' },
  ARMS_HOLD: { say: 'Good. Keep both arms up until the bar is full.', caption: 'Keep both arms up.' },
  ARMS_DONE: { say: 'Thank you. You can lower your arms.', caption: 'You can lower your arms.' },
  ARMS_TIMEOUT: { say: "That's all right. Let's move on.", caption: "That's all right. Let's move on." },
  ARMS_RESTART: { say: "Let's start the hold again.", caption: "Let's start the hold again." },
  FRAME_DARK: { say: "It's too dark for me to see you. Please turn on a light.", caption: "It's too dark to see you. Please turn on a light." },
  FRAME_NO_BODY: { say: "I can't see you. Please prop the phone up so I can see you from the waist up.", caption: "I can't see you. Prop the phone so I see you from the waist up." },
  FRAME_MOVE_BACK: { say: "I can't see both of your arms. Please move back so your hands are in the picture.", caption: "I can't see both arms. Move back so your hands are in the picture." },
  FRAME_TILT_DOWN: { say: "I can't see your shoulders. Please move back, or tilt the phone down a little.", caption: "I can't see your shoulders. Move back, or tilt the phone down." },
  FRAME_TILT_UP: { say: "I can't see your shoulders. Please tilt the phone up a little.", caption: "I can't see your shoulders. Tilt the phone up a little." },
  FRAME_CENTER: { say: 'Please face the phone, in the middle of the picture.', caption: 'Please face the phone, in the middle of the picture.' },
  FRAME_HANDS_HIGH: { say: 'Your hands are above the picture. Please hold your arms straight out, at shoulder height.', caption: 'Hands too high. Hold your arms straight out, at shoulder height.' },
  FRAME_LEFT_HAND: { say: "I can't see your left hand. Please move back so both hands are in the picture.", caption: "I can't see your left hand. Move back so both hands show." },
  FRAME_RIGHT_HAND: { say: "I can't see your right hand. Please move back so both hands are in the picture.", caption: "I can't see your right hand. Move back so both hands show." },
  FACE_NOT_VISIBLE: { say: "I can't see your face. Please hold the phone in front of you.", caption: "I can't see your face. Hold the phone in front of you." },
  FACE_OFF_CENTER: { say: 'Your face is at the edge of the picture. Please move it to the middle.', caption: 'Please move your face to the middle of the picture.' },
  FACE_TURNED: { say: 'Please look straight at the phone.', caption: 'Please look straight at the phone.' },
  SENTENCE_PROMPT: { say: `Last one. Please repeat this sentence after me: ${SENTENCE}`, caption: `Get ready to say: “${SENTENCE}”` },
  SENTENCE_GO: { say: '', caption: `Say it now: “${SENTENCE}”` },
  DECIDING: { say: 'Thank you. One moment.', caption: 'Thank you. One moment.' },
  CLOSE_ESCALATE: { say: 'Some of your results were different from your usual check-in. I am adding your caregiver to this call now. Please call 9 1 1.', caption: 'Change noticed. Adding your caregiver to this call. Please call 911 now.' },
  CLOSE_NO_CHANGE: { say: "I didn't notice any change from your usual check-in. If you feel unwell at any time, call 9 1 1. Goodbye.", caption: 'No change from your usual check-in. If you feel unwell, call 911.' },
};
const ARMS_FRAMING = { too_dark: L.FRAME_DARK, no_body: L.FRAME_NO_BODY, shoulders_low: L.FRAME_TILT_DOWN, shoulders_high: L.FRAME_TILT_UP, shoulders_side: L.FRAME_CENTER,
  both_hands: L.FRAME_MOVE_BACK, hands_high: L.FRAME_HANDS_HIGH, left_hand: L.FRAME_LEFT_HAND, right_hand: L.FRAME_RIGHT_HAND };
const FACE_FRAMING = { too_dark: L.FRAME_DARK, no_face: L.FACE_NOT_VISIBLE, edge: L.FACE_OFF_CENTER, turned: L.FACE_TURNED };
const STEP_NAME = { 1: 'Smile', 2: 'Arms', 3: 'Sentence' };

// ------------------------------------------------------------------ small helpers

const $ = id => document.getElementById(id);
const now = () => performance.now();
const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const has = v => typeof v === 'number' && Number.isFinite(v);
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const f2 = v => has(v) ? v.toFixed(2) : '–';
function store(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } }
function load(k) { try { return localStorage.getItem(k); } catch { return null; } }

const ABORT = Symbol('abort');
let run = null;                      // the current check; { alive }
function alive() { if (!run || !run.alive) throw ABORT; }
const sleep = ms => new Promise(r => setTimeout(r, ms)).then(alive);
const tick = () => sleep(80);

function show(id) {
  for (const s of document.querySelectorAll('.screen')) s.classList.toggle('on', s.id === id);
  window.scrollTo(0, 0);
}

// ------------------------------------------------------------------ settings

const settings = { voice: load('onset-try-voice') !== 'off', simulate: false };
$('voice').checked = settings.voice;
function setVoice(on) {
  settings.voice = on; store('onset-try-voice', on ? 'on' : 'off');
  $('voice').checked = on; $('mute').setAttribute('aria-pressed', String(!on));
  $('mute').title = on ? 'Mute the voice' : 'Turn the voice on';
  if (!on && 'speechSynthesis' in window) speechSynthesis.cancel();
}
setVoice(settings.voice);
$('voice').addEventListener('change', e => setVoice(e.target.checked));
$('mute').addEventListener('click', () => setVoice(!settings.voice));
$('simulate').addEventListener('change', e => { settings.simulate = e.target.checked; });

const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
if (!SR) $('srnote').textContent = 'This browser has no speech recognition, so the sentence is timed but its words are not checked.';
else $('srnote').textContent = "To check the sentence's words, your browser's built-in speech recognition transcribes it; some browsers use their maker's online service for this.";

// ------------------------------------------------------------------ the voice

let voicePick = null;
function pickVoice() {
  if (!('speechSynthesis' in window)) return null;
  const vs = speechSynthesis.getVoices().filter(v => /^en(-|_|$)/i.test(v.lang));
  const pref = [/Google US English/i, /Samantha/i, /Aria|Jenny|Ava/i, /Natural/i, /en-US/i];
  for (const re of pref) { const v = vs.find(x => re.test(x.name) || re.test(x.lang)); if (v) return v; }
  return vs[0] || null;
}
if ('speechSynthesis' in window) {
  voicePick = pickVoice();
  speechSynthesis.addEventListener?.('voiceschanged', () => { voicePick = pickVoice(); });
}

// Speak (if the voice is on) and resolve when done; the caption carries the words either way.
function speak(text) {
  const words = text ? text.split(/\s+/).length : 0;
  const readMs = Math.max(1400, words * 300 + 500);
  if (!text || !settings.voice || !('speechSynthesis' in window)) return new Promise(r => setTimeout(r, text ? readMs : 0));
  return new Promise(resolve => {
    let done = false;
    const finish = () => { if (!done) { done = true; clearTimeout(timer); resolve(); } };
    const timer = setTimeout(finish, words * 650 + 3500);
    try {
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      if (voicePick) u.voice = voicePick;
      u.lang = voicePick?.lang || 'en-US'; u.rate = 1; u.pitch = 1;
      u.onend = finish; u.onerror = finish;
      speechSynthesis.speak(u);
    } catch { finish(); }
  });
}

// ------------------------------------------------------------------ overlay chrome

const cap = $('cap'), pill = $('pill'), cdbar = $('cdbar'), ring = $('ring');
let capText = null;
function caption(text, pos = 'bottom', tone = '') {
  const cls = `cap ${pos}${tone ? ' ' + tone : ''}${text ? '' : ' empty'}`;
  if (text !== capText) {
    capText = text;
    cap.textContent = text || '';
    cap.className = cls;
    if (text) { void cap.offsetWidth; cap.classList.add('enter'); }
  } else cap.className = cls + (text ? ' enter' : '');
  const other = pos === 'top' ? 'bottom' : 'top';
  pill.classList.toggle('at-top', other === 'top'); pill.classList.toggle('at-bottom', other === 'bottom');
  cdbar.classList.toggle('at-top', other === 'top'); cdbar.classList.toggle('at-bottom', other === 'bottom');
}
async function say(line, pos = 'bottom', tone = '') {
  alive();
  caption(line.caption, pos, tone);
  await speak(line.say);
  alive();
}
function progress(n) {
  if (!n) { pill.hidden = true; return; }
  pill.hidden = false;
  pill.innerHTML = `Step ${n} of 3 · ${STEP_NAME[n]} <i>${[1, 2, 3].map(k => `<b class="${k <= n ? 'on' : ''}"></b>`).join('')}</i>`;
}
let barAnim = null;
function countdown(ms) {
  cdbar.hidden = false;
  const i = cdbar.querySelector('i');
  barAnim?.cancel();
  barAnim = i.animate([{ transform: 'scaleX(1)' }, { transform: 'scaleX(0)' }], { duration: ms, easing: 'linear', fill: 'forwards' });
}
function hideBar() { barAnim?.cancel(); barAnim = null; cdbar.hidden = true; }

let skipWanted = false, doneWanted = false;
$('skip').addEventListener('click', () => { skipWanted = true; $('skip').hidden = true; });
$('done').addEventListener('click', () => { doneWanted = true; $('done').hidden = true; });

// ------------------------------------------------------------------ MediaPipe

let visionPromise = null, vision = null;
const loadState = { got: 0, total: 0 };

// MediaPipe's WebAssembly runtime writes its start-up notes ("INFO: Created TensorFlow Lite
// XNNPACK delegate for CPU.") to stderr, which the browser shows as console errors. They are not
// errors; drop exactly those lines and pass everything else through.
{
  const consoleError = console.error.bind(console);
  console.error = (...a) => { if (typeof a[0] === 'string' && /^INFO: /.test(a[0])) return; consoleError(...a); };
}

async function fetchModel(url) {
  const res = await fetch(url, { mode: 'cors' });
  if (!res.ok) throw new Error(`model HTTP ${res.status}`);
  const size = +res.headers.get('content-length') || 0;
  loadState.total += size || 5e6;
  if (!res.body || !res.body.getReader) { const b = new Uint8Array(await res.arrayBuffer()); loadState.got += b.length; return b; }
  const reader = res.body.getReader(), parts = [];
  let n = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parts.push(value); n += value.length; loadState.got += value.length;
  }
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

function loadVision() {
  if (visionPromise) return visionPromise;
  visionPromise = (async () => {
    const [mod, faceBuf, poseBuf] = await Promise.all([
      import(`${MP_BASE}/vision_bundle.mjs`),
      fetchModel(MODELS.face),
      fetchModel(MODELS.pose),
    ]);
    const files = await mod.FilesetResolver.forVisionTasks(`${MP_BASE}/wasm`);
    const make = async (Cls, buf, extra) => {
      let last;
      for (const delegate of ['GPU', 'CPU']) {
        try { return await Cls.createFromOptions(files, { baseOptions: { modelAssetBuffer: buf, delegate }, runningMode: 'VIDEO', ...extra }); }
        catch (e) { last = e; }
      }
      throw last || new Error('model failed to start');
    };
    const face = await make(mod.FaceLandmarker, faceBuf, { numFaces: 1, minFaceDetectionConfidence: 0.5, minFacePresenceConfidence: 0.5, minTrackingConfidence: 0.5 });
    const pose = await make(mod.PoseLandmarker, poseBuf, { numPoses: 1 });
    vision = { face, pose };
    return vision;
  })();
  visionPromise.catch(() => { visionPromise = null; });
  return visionPromise;
}

// Start downloading the models while the visitor reads the landing page.
addEventListener('load', () => {
  const go = () => loadVision().catch(() => { /* reported when the check starts */ });
  if ('requestIdleCallback' in window) requestIdleCallback(go, { timeout: 2500 }); else setTimeout(go, 800);
});

// ------------------------------------------------------------------ camera and microphone

const video = $('video'), canvas = $('overlay'), ctx = canvas.getContext('2d');
let stream = null, audio = null;

class CameraError extends Error { constructor(kind, cause) { super(kind); this.kind = kind; this.cause = cause; } }

async function openCamera() {
  if (!navigator.mediaDevices?.getUserMedia) throw new CameraError(window.isSecureContext ? 'unsupported' : 'insecure');
  const vid = { facingMode: 'user', width: { ideal: 1280 }, height: { ideal: 720 } };
  const kind = e => e?.name === 'NotAllowedError' || e?.name === 'SecurityError' ? 'denied'
    : e?.name === 'NotFoundError' || e?.name === 'OverconstrainedError' ? 'nocamera'
    : e?.name === 'NotReadableError' || e?.name === 'AbortError' ? 'busy' : 'unknown';
  try {
    return await navigator.mediaDevices.getUserMedia({ video: vid, audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
  } catch (e) {
    // The camera alone is enough for two of the three checks: retry without the microphone.
    try { return await navigator.mediaDevices.getUserMedia({ video: vid }); }
    catch (e2) { throw new CameraError(kind(e2) === 'unknown' ? kind(e) : kind(e2), e2); }
  }
}

function makeAudio(ac, s) {
  try {
    if (!ac || !s.getAudioTracks().length) return null;
    const src = ac.createMediaStreamSource(s), an = ac.createAnalyser();
    an.fftSize = 1024; src.connect(an);
    const buf = new Float32Array(an.fftSize);
    return { ac, rms() { an.getFloatTimeDomainData(buf); let x = 0; for (const v of buf) x += v * v; return Math.sqrt(x / buf.length); } };
  } catch { return null; }
}

function stopCamera() {
  stream?.getTracks().forEach(t => t.stop());
  stream = null;
  video.srcObject = null; video.classList.remove('streaming');
  if (audio?.ac && audio.ac.state !== 'closed') audio.ac.close().catch(() => {});
  audio = null;
}

function fitStage() {
  const vw = video.videoWidth, vh = video.videoHeight;
  if (!vw || !vh) return;
  const stage = $('stage');
  stage.style.aspectRatio = `${vw} / ${vh}`;
  stage.style.setProperty('--arn', String(vw / vh));
}
video.addEventListener('loadedmetadata', fitStage);
addEventListener('resize', () => { fitStage(); sizeCanvas(); });

// ------------------------------------------------------------------ the per-frame loop

const want = { face: false, pose: false };
let drawMode = 'none';
let lastFace = null, lastPose = null;   // the newest observations
let faceSink = null;                     // array collecting usable smile-step corners
let poseFrames = [];                     // body frames, the last ~15 s
let liveRest = null;                     // neutral corner rest, for live lift numbers
let holdInfo = null;                     // { t0, start: [y, y] } during the arm hold
let lumaVal = 128, lumaAt = 0, lastVideoTime = -1, raf = 0;
const tsLast = { face: 0, pose: 0 };
const lumaCanvas = document.createElement('canvas'); lumaCanvas.width = 32; lumaCanvas.height = 18;
const lumaCtx = lumaCanvas.getContext('2d', { willReadFrequently: true });

function sizeCanvas() {
  const r = canvas.getBoundingClientRect(), dpr = Math.min(devicePixelRatio || 1, 2);
  const w = Math.max(2, Math.round(r.width * dpr)), h = Math.max(2, Math.round(r.height * dpr));
  if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}

function measureLuma() {
  try {
    lumaCtx.drawImage(video, 0, 0, 32, 18);
    const d = lumaCtx.getImageData(0, 0, 32, 18).data;
    let s = 0;
    for (let i = 0; i < d.length; i += 4) s += 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2];
    lumaVal = s / (d.length / 4);
  } catch { /* keep the last value */ }
}

function stamp(kind, t) { const ts = Math.max(Math.round(t), tsLast[kind] + 1); tsLast[kind] = ts; return ts; }

function loop() {
  raf = requestAnimationFrame(loop);
  if (!vision || video.readyState < 2 || !video.videoWidth) { draw(); return; }
  const t = now();
  if (video.currentTime !== lastVideoTime) {
    lastVideoTime = video.currentTime;
    const w = video.videoWidth, h = video.videoHeight;
    if (t - lumaAt > 400) { lumaAt = t; measureLuma(); }
    if (want.face) {
      try {
        const r = vision.face.detectForVideo(video, stamp('face', t));
        const lm = r.faceLandmarks?.[0] || null;
        const obs = { t: t / 1000, lm, geom: lm ? M.faceGeometry(lm, w, h) : null, luma: lumaVal, w, h };
        lastFace = obs;
        if (faceSink && !M.faceProblem(obs)) faceSink.push(obs.geom.corners);
      } catch { lastFace = { t: t / 1000, lm: null, geom: null, luma: lumaVal, w, h }; }
    }
    if (want.pose) {
      try {
        const r = vision.pose.detectForVideo(video, stamp('pose', t));
        const s = M.poseSample(t / 1000, r.landmarks?.[0] || null, w, h, lumaVal);
        lastPose = s;
        const b = M.bodyFrame(s);
        poseFrames.push(b);
        const cut = t / 1000 - 15;
        while (poseFrames.length && poseFrames[0].t < cut) poseFrames.shift();
      } catch { lastPose = { t: t / 1000, pts: null, w, h, luma: lumaVal }; }
    }
    updateLive();
  }
  draw();
}

// ------------------------------------------------------------------ drawing what the AI sees

function mapper() {
  const W = canvas.clientWidth, H = canvas.clientHeight, vw = video.videoWidth, vh = video.videoHeight;
  const s = Math.max(W / vw, H / vh), dw = vw * s, dh = vh * s, ox = (W - dw) / 2, oy = (H - dh) / 2;
  return (x, y) => [W - (ox + x * dw), oy + y * dh];   // normalized, unmirrored -> mirrored screen
}

function glowLine(pts, color, width, blur = 10) {
  if (pts.length < 2) return;
  ctx.save();
  ctx.strokeStyle = color; ctx.lineWidth = width; ctx.lineCap = 'round'; ctx.lineJoin = 'round';
  ctx.shadowColor = color; ctx.shadowBlur = blur;
  ctx.beginPath(); ctx.moveTo(...pts[0]); for (const p of pts.slice(1)) ctx.lineTo(...p); ctx.stroke();
  ctx.restore();
}
function dot(p, r, fill, ringColor = 'rgba(255,255,255,.9)') {
  ctx.save();
  ctx.shadowColor = fill; ctx.shadowBlur = 14;
  ctx.fillStyle = fill; ctx.beginPath(); ctx.arc(p[0], p[1], r, 0, 7); ctx.fill();
  ctx.shadowBlur = 0; ctx.lineWidth = 1.5; ctx.strokeStyle = ringColor; ctx.stroke();
  ctx.restore();
}

const COL = { glow: '#67E0D2', left: '#26A898', right: '#7E80F2', white: 'rgba(255,255,255,.75)' };

function draw() {
  sizeCanvas();
  const W = canvas.clientWidth, H = canvas.clientHeight;
  ctx.clearRect(0, 0, W, H);
  if (!video.videoWidth) return;
  const map = mapper(), tnow = now() / 1000;
  if (drawMode === 'face' && lastFace?.lm && tnow - lastFace.t < 0.5) {
    const lm = lastFace.lm, P = i => map(lm[i].x, lm[i].y);
    // corner brackets around the face
    let x0 = 1, y0 = 1, x1 = 0, y1 = 0;
    for (const p of lm) { if (p.x < x0) x0 = p.x; if (p.x > x1) x1 = p.x; if (p.y < y0) y0 = p.y; if (p.y > y1) y1 = p.y; }
    const a = map(x0, y0), b = map(x1, y1);
    const L0 = Math.min(a[0], b[0]) - 10, R0 = Math.max(a[0], b[0]) + 10, T0 = Math.min(a[1], b[1]) - 10, B0 = Math.max(a[1], b[1]) + 10;
    const k = Math.min(28, (R0 - L0) * 0.16);
    ctx.save(); ctx.strokeStyle = 'rgba(103,224,210,.55)'; ctx.lineWidth = 2; ctx.lineCap = 'round';
    for (const [cx, cy, dx, dy] of [[L0, T0, 1, 1], [R0, T0, -1, 1], [L0, B0, 1, -1], [R0, B0, -1, -1]]) {
      ctx.beginPath(); ctx.moveTo(cx + dx * k, cy); ctx.lineTo(cx, cy); ctx.lineTo(cx, cy + dy * k); ctx.stroke();
    }
    ctx.restore();
    // eye line and lips
    const g = lastFace.geom;
    if (g) {
      const e0 = M.faceToImage(g, [-0.62, 0]), e1 = M.faceToImage(g, [0.62, 0]);
      ctx.save(); ctx.setLineDash([3, 5]); glowLine([map(e0[0] / lastFace.w, e0[1] / lastFace.h), map(e1[0] / lastFace.w, e1[1] / lastFace.h)], 'rgba(255,255,255,.35)', 1.2, 0); ctx.restore();
    }
    glowLine(M.FACE.LIPS_OUTER.map(P), 'rgba(103,224,210,.85)', 1.8, 10);
    // neutral rest and the lift of each corner
    for (const [side, idx, col] of [[0, M.FACE.L_MOUTH, COL.left], [1, M.FACE.R_MOUTH, COL.right]]) {
      const cur = P(idx);
      if (liveRest && g) {
        const r = M.cornerToImage(g, side, liveRest[side]), rp = map(r[0] / lastFace.w, r[1] / lastFace.h);
        glowLine([rp, cur], col, 2, 6);
        ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,.7)'; ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(rp[0], rp[1], 3.5, 0, 7); ctx.stroke(); ctx.restore();
      }
      dot(cur, 5.5, col);
    }
  }
  if (drawMode === 'arms' && lastPose?.pts && tnow - lastPose.t < 0.5) {
    const s = lastPose, P = r => map(s.pts[r][0] / s.w, s.pts[r][1] / s.h), vis = r => s.pts[r][2] >= M.ARMS.VIS_MIN;
    if (vis(M.LS) && vis(M.RS)) glowLine([P(M.LS), P(M.RS)], COL.white, 2.5, 8);
    for (const [name, sh, el, wr, col] of [['left', M.LS, M.LE, M.LW, COL.left], ['right', M.RS, M.RE, M.RW, COL.right]]) {
      if (!vis(sh)) continue;
      const chain = [P(sh)];
      if (vis(el)) chain.push(P(el));
      if (vis(wr)) chain.push(P(wr));
      glowLine(chain, col, 4, 12);
      dot(P(sh), 4.5, col);
      if (vis(wr)) {
        dot(P(wr), 8, col);
        const side = name === 'left' ? 0 : 1, lvl = holdInfo?.start?.[side];
        if (has(lvl) && vis(M.LS) && vis(M.RS)) {   // where this wrist started the hold
          const ls = s.pts[M.LS], rs = s.pts[M.RS], sw = Math.hypot(ls[0] - rs[0], ls[1] - rs[1]);
          const u = [(ls[0] - rs[0]) / sw, (ls[1] - rs[1]) / sw], d = [-u[1], u[0]], m = [(ls[0] + rs[0]) / 2, (ls[1] + rs[1]) / 2];
          const px = s.pts[wr], rx = ((px[0] - m[0]) * u[0] + (px[1] - m[1]) * u[1]) / sw;
          const q = [m[0] + sw * (rx * u[0] + lvl * d[0]), m[1] + sw * (rx * u[1] + lvl * d[1])];
          const c = map(q[0] / s.w, q[1] / s.h), half = Math.max(16, sw / s.w * canvas.clientWidth * 0.18);
          ctx.save(); ctx.setLineDash([5, 4]); glowLine([[c[0] - half, c[1]], [c[0] + half, c[1]]], col, 2, 4); ctx.restore();
        }
      }
    }
  }
}

// ------------------------------------------------------------------ live panel

function buildLive() {
  $('livebody').innerHTML = `
  <div class="lv-step" id="lv-1"><div class="lv-head"><b>1 · Smile</b><span id="lv1s">next</span></div>
    <div class="mrow"><span class="k">your left</span><div class="meter l"><div class="fill" id="lvfl"></div></div><span class="v" id="lvfl-v">–</span></div>
    <div class="mrow"><span class="k">your right</span><div class="meter r"><div class="fill" id="lvfr"></div></div><span class="v" id="lvfr-v">–</span></div>
    <div class="mrow"><span class="k">asymmetry</span><div class="meter" id="lvfa-m"><div class="fill" id="lvfa"></div><i class="tick" style="left:${M.LIMITS.face_asymmetry / 0.5 * 100}%"></i></div><span class="v" id="lvfa-v">–</span></div>
    <p class="lv-note">How far each mouth corner lifts from your relaxed face, in eye-distance units. 1 − weaker ÷ stronger; a change is ${M.LIMITS.face_asymmetry.toFixed(2)} or more.</p></div>
  <div class="lv-step" id="lv-2"><div class="lv-head"><b>2 · Arms</b><span id="lv2s">next</span></div>
    <div class="mrow"><span class="k">left drift</span><div class="meter l"><div class="fill" id="lval"></div></div><span class="v" id="lval-v">–</span></div>
    <div class="mrow"><span class="k">right drift</span><div class="meter r"><div class="fill" id="lvar"></div></div><span class="v" id="lvar-v">–</span></div>
    <div class="mrow"><span class="k">difference</span><div class="meter" id="lvad-m"><div class="fill" id="lvad"></div><i class="tick" style="left:${M.LIMITS.arm_difference / 0.4 * 100}%"></i></div><span class="v" id="lvad-v">–</span></div>
    <p class="lv-note">How far each wrist sinks below where it started, in shoulder widths, measured against your shoulder line so camera shake cancels out. Arms straight out in front of you works best.</p></div>
  <div class="lv-step" id="lv-3"><div class="lv-head"><b>3 · Sentence</b><span id="lv3s">next</span></div>
    <div class="lv-heard" id="lvheard">&nbsp;<span class="exp">expected: ${esc(SENTENCE)}</span></div>
    <div class="lv-wave" id="lvwave">${'<i></i>'.repeat(28)}</div>
    <p class="lv-note" id="lv3n">${SR ? 'Word errors, speaking rate and the longest pause.' : 'This browser has no speech recognition: Onset times your speech and pauses from the microphone level, but cannot check the words.'}</p></div>`;
}
function liveStep(n, state) {
  for (const k of [1, 2, 3]) {
    const el = $(`lv-${k}`); if (!el) continue;
    el.classList.toggle('cur', k === n);
    if (k < n) el.classList.add('done');
  }
  const chip = $('livechip');
  chip.className = `chip ${state === 'measuring' ? 'measuring' : state === 'done' ? 'ok' : 'waiting'}`;
  chip.textContent = state;
}
function setStepNote(n, text) { const el = $(`lv${n}s`); if (el) el.textContent = text; }
function meter(id, v, max, bad = false) {
  const el = $(id); if (!el) return;
  el.style.width = has(v) ? `${clamp(v / max) * 100}%` : '0%';
  el.parentElement.classList.toggle('bad', bad);
  const t = $(`${id}-v`); if (t) t.textContent = has(v) ? v.toFixed(2) : '–';
}

let liveSmile = null;
function updateLive() {
  if (drawMode === 'face' && liveRest && lastFace?.geom) {
    const l = M.liftOf(lastFace.geom.corners, liveRest), a = M.asymmetryIndex(l[0], l[1]);
    const strong = Math.max(l[0], l[1]) >= M.FACE_LIMITS.MIN_SMILE_LIFT;
    liveSmile = { l, a: strong ? a : null };
    meter('lvfl', Math.max(0, l[0]), 0.25); meter('lvfr', Math.max(0, l[1]), 0.25);
    meter('lvfa', strong ? a : null, 0.5, strong && a >= M.LIMITS.face_asymmetry);
    if (!strong) $('lvfa-v').textContent = '–';
  }
  if (drawMode === 'arms' && holdInfo) {
    const t = now() / 1000;
    const recent = poseFrames.filter(f => f.t >= holdInfo.t0 - 0.1);
    const ld = M.liveDrift(recent, holdInfo.t0, Math.min(t, holdInfo.t0 + M.ARMS.HOLD_SECONDS));
    holdInfo.start = ld.start;
    const [dl, dr] = ld.drift;
    meter('lval', has(dl) ? Math.max(0, dl) : null, 0.6, has(dl) && dl >= M.LIMITS.arm_single);
    meter('lvar', has(dr) ? Math.max(0, dr) : null, 0.6, has(dr) && dr >= M.LIMITS.arm_single);
    const diff = has(dl) && has(dr) ? Math.abs(dl - dr) : null;
    meter('lvad', diff, 0.4, has(diff) && diff >= M.LIMITS.arm_difference);
  }
}

// ------------------------------------------------------------------ framing

function faceNow() {
  const t = now() / 1000;
  return lastFace && t - lastFace.t < 0.6 ? M.faceProblem(lastFace) : (lumaVal < M.FACE_LIMITS.MIN_LUMA ? 'too_dark' : 'no_face');
}
function armsNow(wrists) {
  const t = now() / 1000;
  return lastPose && t - lastPose.t < 0.6 ? M.armsProblem(lastPose, { wrists }) : (lumaVal < 45 ? 'too_dark' : 'no_body');
}

// Wait until the picture shows what the step needs. Says the agent's fix-it line when it does
// not, offers "Skip this step" after SKIP_AFTER_MS and gives up by itself after GIVE_UP_MS.
async function framingGate(kind, pos) {
  const t0 = now();
  let okSince = null, badSince = null, shown = null, shownAt = 0;
  skipWanted = false;
  for (;;) {
    await tick();
    const p = kind === 'face' ? faceNow() : armsNow(false);
    if (!p) {
      okSince ??= now(); badSince = null;
      if (now() - okSince > 700) { $('skip').hidden = true; return 'ok'; }
    } else {
      okSince = null; badSince ??= now();
      if (now() - badSince > 900 && (p !== shown || now() - shownAt > 12000)) {
        shown = p; shownAt = now();
        const line = (kind === 'face' ? FACE_FRAMING : ARMS_FRAMING)[p] || (kind === 'face' ? L.FACE_NOT_VISIBLE : L.FRAME_NO_BODY);
        caption(line.caption, pos); speak(line.say);
      }
    }
    if (now() - t0 > SKIP_AFTER_MS && $('skip').hidden && !skipWanted) $('skip').hidden = false;
    if (skipWanted || now() - t0 > GIVE_UP_MS) { $('skip').hidden = true; return 'skipped'; }
  }
}

// ------------------------------------------------------------------ step 1: smile

async function stepFace() {
  progress(1); liveStep(1, 'measuring'); setStepNote(1, 'framing');
  want.face = true; want.pose = false; drawMode = 'face'; liveRest = null;
  for (let attempt = 0; ; attempt++) {
    if (await framingGate('face', 'top') === 'skipped') { setStepNote(1, 'skipped'); return { status: 'skipped' }; }
    setStepNote(1, 'relaxed face');
    await say(L.FACE_NEUTRAL, 'top');
    faceSink = [];
    countdown(2200); await sleep(2200); hideBar();
    const neutral = faceSink;
    if (neutral.length >= 3) liveRest = M.restCorners(neutral);
    setStepNote(1, 'smile');
    faceSink = [];
    const said = say(L.FACE_SMILE, 'top');
    await said;
    countdown(3000); await sleep(3000); hideBar();
    const smile = faceSink; faceSink = null;
    drawMode = 'none';   // freeze the live meters; the final numbers come from the peak frames
    const res = M.smileResult(neutral, smile);
    if (res.status === 'partial' && attempt < 1) drawMode = 'face';
    if (res.status === 'partial' && attempt < 1) { liveRest = null; continue; }
    setStepNote(1, res.status === 'ok' ? `asymmetry ${f2(res.asym)}` : res.status === 'no_smile' ? 'no smile seen' : 'not clear');
    if (res.status === 'ok') {
      meter('lvfl', Math.max(0, res.liftLeft), 0.25); meter('lvfr', Math.max(0, res.liftRight), 0.25);
      meter('lvfa', res.asym, 0.5, res.asym >= M.LIMITS.face_asymmetry);
    }
    return res;
  }
}

// ------------------------------------------------------------------ step 2: arms

async function stepArms() {
  progress(2); liveStep(2, 'measuring'); setStepNote(2, 'framing');
  want.face = false; want.pose = true; drawMode = 'arms'; holdInfo = null; poseFrames = [];
  for (let attempt = 0; ; attempt++) {
    if (await framingGate('arms', 'bottom') === 'skipped') { setStepNote(2, 'skipped'); return { status: 'skipped' }; }
    setStepNote(2, 'raise both arms');
    ring.hidden = false; ring.classList.add('waiting'); $('ringn').textContent = '10'; $('ringarc').style.strokeDashoffset = '326.7';
    caption(L.ARMS_RAISE.caption, 'bottom');
    const said = speak(L.ARMS_RAISE.say);
    // Wait for both wrists up and steady (arms.py find_hold_start).
    const tw = now();
    let t0 = null, shown = null, shownAt = 0, badSince = null;
    skipWanted = false;
    while (t0 === null) {
      await tick();
      const recent = poseFrames.filter(f => f.t >= now() / 1000 - 1.2);
      t0 = M.holdStart(recent);
      if (t0 !== null) break;
      const p = armsNow(true);
      if (p && now() - tw > 5000) {
        badSince ??= now();
        if (now() - badSince > 2000 && (p !== shown || now() - shownAt > 12000)) {
          shown = p; shownAt = now();
          const line = ARMS_FRAMING[p] || L.FRAME_MOVE_BACK;
          caption(line.caption, 'bottom'); speak(line.say);
        }
      } else badSince = null;
      if (now() - tw > SKIP_AFTER_MS && $('skip').hidden && !skipWanted) $('skip').hidden = false;
      if (skipWanted) { ring.hidden = true; setStepNote(2, 'skipped'); return { status: 'skipped' }; }
      if (now() - tw > ARMS_WAIT_MS) {
        $('skip').hidden = true; ring.hidden = true;
        await say(L.ARMS_TIMEOUT, 'bottom');
        setStepNote(2, 'arms not raised');
        return { status: 'not_raised' };
      }
    }
    $('skip').hidden = true;
    await Promise.race([said, sleep(400)]);
    // The 10 s hold.
    setStepNote(2, 'holding');
    holdInfo = { t0, start: [null, null] };
    ring.classList.remove('waiting');
    caption(L.ARMS_HOLD.caption, 'bottom'); speak(L.ARMS_HOLD.say);
    countdown(Math.max(0, (t0 + M.ARMS.HOLD_SECONDS) * 1000 - now()));
    let lostSince = null, restart = null, t1 = null, lowered = false;
    for (;;) {
      await sleep(50);
      const t = now() / 1000, el = t - t0;
      $('ringn').textContent = String(Math.max(0, Math.ceil(M.ARMS.HOLD_SECONDS - el - 1e-6)));
      $('ringarc').style.strokeDashoffset = (326.7 * (1 - clamp(el / M.ARMS.HOLD_SECONDS))).toFixed(1);
      if (el >= M.ARMS.HOLD_SECONDS) { t1 = t0 + M.ARMS.HOLD_SECONDS; break; }
      const recent = poseFrames.filter(f => f.t >= t0 - 0.1);
      const down = M.bothDownSince(recent.slice(-30));
      if (down !== null && el > 1.5) { t1 = down; lowered = true; break; }
      // Picture lost (not an arm coming down): ask to fix it and restart the hold once.
      const p = armsNow(true), last = recent[recent.length - 1];
      const dropping = last && last.ok && [0, 1].some(sd => M.wristDown(last, sd));
      if (p && !dropping) { lostSince ??= now(); if (now() - lostSince > 2500) { restart = p; break; } } else lostSince = null;
    }
    hideBar();
    if (restart && attempt < 1) {
      holdInfo = null; ring.hidden = true;
      await say(ARMS_FRAMING[restart] || L.FRAME_MOVE_BACK, 'bottom');
      await say(L.ARMS_RESTART, 'bottom');
      continue;
    }
    if (restart) t1 = now() / 1000;
    const frames = poseFrames.filter(f => f.t >= t0 - 0.1 && f.t <= t1 + 0.05);
    frames.lowered = lowered;
    const res = M.armsResult(frames, t0, t1);
    holdInfo = null;
    ring.hidden = true;
    if (res.status === 'ok') {
      meter('lval', Math.max(0, res.left), 0.6, res.left >= M.LIMITS.arm_single);
      meter('lvar', Math.max(0, res.right), 0.6, res.right >= M.LIMITS.arm_single);
      meter('lvad', res.diff, 0.4, res.diff >= M.LIMITS.arm_difference);
      setStepNote(2, `difference ${f2(res.diff)}`);
    } else setStepNote(2, 'not clear');
    drawMode = 'none';
    await say(L.ARMS_DONE, 'bottom');
    return res;
  }
}

// ------------------------------------------------------------------ step 3: sentence

function startRecognition() {
  if (!SR) return null;
  const st = { finals: [], interim: '', error: null, ended: false, rec: null };
  try {
    const r = new SR();
    r.lang = 'en-US'; r.continuous = true; r.interimResults = true; r.maxAlternatives = 3;
    r.onresult = e => {
      const fin = []; let inter = '';
      for (let i = 0; i < e.results.length; i++) {
        const res = e.results[i];
        if (res.isFinal) fin.push(Array.from(res, a => a.transcript));
        else inter += res[0].transcript;
      }
      st.finals = fin; st.interim = inter;
      const shown = (fin.map(a => a[0]).join(' ') + ' ' + inter).trim();
      const h = $('lvheard');
      if (h && shown) h.innerHTML = `“${esc(shown)}”<span class="exp">expected: ${esc(SENTENCE)}</span>`;
    };
    r.onerror = e => { st.error = e.error || 'error'; };
    r.onend = () => { st.ended = true; };
    r.start();
    st.rec = r;
  } catch (e) { st.error = 'start-failed'; st.ended = true; }
  return st;
}

// The heard sentence: the alternative closest to the expected one (Scribe's language model
// leans towards real words in the same way; see onset/speech.py AUTOCORRECT_NOTE).
function bestTranscript(st) {
  if (!st) return null;
  if (!st.finals.length) return st.interim.trim() || null;
  let text = '';
  for (const alts of st.finals) {
    let best = alts[0], bw = Infinity;
    for (const a of alts) { const w = M.wordErrorRate(SENTENCE, (text + ' ' + a).trim()); if (w < bw) { bw = w; best = a; } }
    text = (text + ' ' + best).trim();
  }
  return (text + ' ' + st.interim).trim() || null;
}

async function stepSentence() {
  progress(3); liveStep(3, 'measuring'); setStepNote(3, 'get ready');
  want.face = false; want.pose = false; drawMode = 'none';
  await say(L.SENTENCE_PROMPT, 'bottom');
  await sleep(250);
  caption(L.SENTENCE_GO.caption, 'bottom');
  setStepNote(3, 'listening');
  countdown(SAY_IT_MS);
  doneWanted = false;
  const levels = [], t0 = now();
  const rec = startRecognition();
  const bars = Array.from(document.querySelectorAll('#lvwave i'));
  const timer = audio ? setInterval(() => {
    const r = audio.rms();
    levels.push({ t: (now() - t0) / 1000, rms: r });
    if (levels.length % 3) return;   // a scrolling level trace, ~60 ms per bar
    const recent = levels.slice(-bars.length * 3);
    bars.forEach((b, i) => {
      const chunk = recent.slice(i * 3, i * 3 + 3);
      const v = chunk.length ? Math.max(...chunk.map(x => x.rms)) : 0;
      b.style.height = `${Math.max(2, Math.min(26, Math.sqrt(v) * 55))}px`;
    });
  }, 20) : 0;
  setTimeout(() => { if (run?.alive && !doneWanted) $('done').hidden = false; }, 1200);
  let vt = null;
  try {
    for (;;) {
      await sleep(150);
      const el = now() - t0;
      if (doneWanted || el > SAY_IT_MS) break;
      vt = audio ? M.voiceTiming(levels) : null;
      const finalHeard = rec && rec.finals.length && !rec.interim;
      const quiet = vt?.offset != null ? el / 1000 - vt.offset : 0;
      if (vt?.onset != null && el > 2000 && ((quiet > 1.3 && (finalHeard || !rec || rec.ended)) || quiet > 2.6)) break;
      if (!audio && rec && finalHeard && el > 2500) break;
    }
  } finally {
    clearInterval(timer);
    $('done').hidden = true; hideBar();
  }
  // Leave out the click on "Done speaking".
  const cutAt = doneWanted ? (now() - t0) / 1000 - 0.15 : Infinity;
  vt = audio ? M.voiceTiming(levels.filter(l => l.t < cutAt)) : null;
  if (rec?.rec) {
    try { rec.rec.stop(); } catch { /* already stopped */ }
    const tw = now();
    while (!rec.ended && now() - tw < 1800) await sleep(100);
  }
  const transcript = bestTranscript(rec);
  const out = { status: 'ok', transcript, wer: null, wpm: null, pause: null };
  if (transcript) out.wer = M.wordErrorRate(SENTENCE, transcript);
  else out.werNote = !SR ? 'not measured in this browser' : rec?.error === 'not-allowed' ? 'speech recognition was blocked' : 'speech recognition heard nothing';
  if (vt?.onset != null) {
    const n = transcript ? M.normalizeWords(transcript).filter(w => !M.FILLERS.has(w)).length : M.normalizeWords(SENTENCE).length;
    const span = vt.offset - vt.onset;
    if (span > 0.4 && n > 0) out.wpm = n / span * 60;
    out.pause = vt.longestPause;
  }
  if (!audio) out.timingNote = 'no microphone';
  if (!transcript && vt?.onset == null) { out.status = 'not_heard'; setStepNote(3, 'not heard'); }
  else setStepNote(3, out.wer != null ? `${Math.round(out.wer * 100)}% words wrong` : 'timed');
  return out;
}

// ------------------------------------------------------------------ the whole check

let lastResults = null;

async function startCheck() {
  if (run) run.alive = false;
  const me = { alive: true };
  run = me;
  // Unlock speech and audio inside the click (iOS and Chrome autoplay rules).
  let ac = null;
  try { ac = new (window.AudioContext || window.webkitAudioContext)(); ac.resume?.(); } catch { ac = null; }
  if (settings.voice && 'speechSynthesis' in window) {
    try { speechSynthesis.cancel(); const u = new SpeechSynthesisUtterance(' '); u.volume = 0; speechSynthesis.speak(u); } catch { /* no voice */ }
  }
  buildLive(); liveStep(0, 'ready');
  progress(0); hideBar(); ring.hidden = true; $('skip').hidden = true; $('done').hidden = true;
  caption('', 'bottom');
  show('check');
  $('loading').hidden = false; $('loadtitle').textContent = 'Opening your camera'; $('loadsub').textContent = 'Your browser will ask for the camera and microphone. They stay on this device.';
  try {
    stream = await openCamera();
  } catch (e) {
    ac?.close().catch(() => {});
    if (run === me) problem(e.kind || 'unknown');
    return;
  }
  if (!me.alive) { stream.getTracks().forEach(t => t.stop()); return; }
  video.srcObject = stream;
  try { await video.play(); } catch { /* autoplay with muted video still starts */ }
  video.classList.add('streaming');
  audio = makeAudio(ac, stream);
  if (!audio) ac?.close().catch(() => {});
  cancelAnimationFrame(raf); raf = requestAnimationFrame(loop);
  try {
    if (!vision) {
      $('loadtitle').textContent = 'Loading the on-device AI';
      const shownAt = now();
      const progressTimer = setInterval(() => {
        const pct = loadState.total ? Math.round(100 * loadState.got / loadState.total) : 0;
        $('loadsub').textContent = `Face and body models from Google's MediaPipe, about 12 MB, once${pct ? ` · ${Math.min(pct, 100)}%` : ''}.` + (now() - shownAt > 20000 ? ' This is taking a while on this connection.' : '');
      }, 250);
      try {
        await Promise.race([loadVision(), new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 90000))]);
      } finally { clearInterval(progressTimer); }
    }
  } catch (e) {
    if (run === me) problem('models');
    return;
  }
  if (!me.alive) return;
  $('loading').hidden = true;
  try {
    await say(L.GREETING, 'bottom');
    const face = await stepFace();
    const arms = await stepArms();
    const speech = await stepSentence();
    want.face = want.pose = false; drawMode = 'none'; progress(0); liveStep(4, 'done');
    await say(L.DECIDING, 'bottom');
    lastResults = { face, arms, speech, at: new Date() };
    window.onsetDemoResult = lastResults;
    const view = settings.simulate ? M.simulateChange(lastResults) : lastResults;
    const d = M.decide(view);
    if (d.outcome === 'change') await say(L.CLOSE_ESCALATE, 'bottom', 'alert');
    else await say(L.CLOSE_NO_CHANGE, 'bottom', 'calm');
    await sleep(600);
    finish();
    $('simulate2').checked = settings.simulate;
    renderResults();
    show('results');
  } catch (e) {
    if (e !== ABORT) { console.warn('check stopped', e); if (run === me) problem('unknown'); }
  }
}

function finish() {
  if (run) run.alive = false;
  run = null;
  cancelAnimationFrame(raf);
  want.face = want.pose = false; drawMode = 'none'; faceSink = null; holdInfo = null; liveRest = null;
  if ('speechSynthesis' in window) speechSynthesis.cancel();
  stopCamera();
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

$('start').addEventListener('click', startCheck);
$('again').addEventListener('click', startCheck);
$('pretry').addEventListener('click', startCheck);
$('exit').addEventListener('click', () => { finish(); show('landing'); });
$('psample').addEventListener('click', () => {
  lastResults = {
    face: { status: 'ok', asym: 0.06, liftLeft: 0.094, liftRight: 0.1, weaker: 'left' },
    arms: { status: 'ok', left: 0.03, right: 0.015, diff: 0.015, hold: 10 },
    speech: { status: 'ok', transcript: 'the sky is blue in Cincinnati', wer: 0, wpm: 156, pause: 0.32 },
    at: new Date(), sample: true,
  };
  $('simulate2').checked = settings.simulate;
  renderResults(); show('results');
});
$('simulate2').addEventListener('change', e => { settings.simulate = e.target.checked; $('simulate').checked = settings.simulate; renderResults(); });

// ------------------------------------------------------------------ something went wrong

const PROBLEMS = {
  denied: ['Camera access is blocked', 'Onset needs the camera to see the smile and the arm hold. Allow the camera for this page (the camera icon in the address bar), then try again. The picture never leaves your browser.'],
  nocamera: ['No camera found', 'This device has no camera Onset can use. Try on a laptop or phone with a front camera, or watch the real call instead.'],
  busy: ['The camera is busy', 'Another app (a video call, perhaps) is using the camera. Close it and try again.'],
  insecure: ['The camera needs a secure page', 'Browsers only open the camera on https pages. Open this demo from its https link.'],
  unsupported: ["This browser can't open the camera", 'Try a current version of Chrome, Edge, Safari or Firefox.'],
  models: ["The AI models didn't load", "The face and body models come from Google's MediaPipe servers (about 12 MB). Check the connection, or try another browser. You can also see a sample result or watch the real call."],
  unknown: ['Something went wrong', 'The check stopped unexpectedly. Try again, or watch the real call instead.'],
};
function problem(kind) {
  finish();
  const [t, b] = PROBLEMS[kind] || PROBLEMS.unknown;
  $('ptitle').textContent = t; $('pbody').textContent = b;
  show('problem');
}

// ------------------------------------------------------------------ results

// Plausible earlier check-ins for the trend lines (seeded, so they look the same every time).
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function gauss(r) { return Math.sqrt(-2 * Math.log(r() || 1e-9)) * Math.cos(2 * Math.PI * r()); }
function history(seed, mean, sd, lo = 0, round = 3) {
  const r = rng(seed);
  return Array.from({ length: 13 }, () => +Math.max(lo, mean + sd * gauss(r)).toFixed(round));
}
const HIST = {
  face: history(11, M.BASELINE.face_asymmetry, 0.035),
  arms: history(23, 0.025, 0.014),
  wer: history(37, 0, 0).map((v, i) => (i === 5 ? 0 : v)),
  wpm: history(41, M.BASELINE.speech_wpm, 7, 90, 0),
  pause: history(53, M.BASELINE.speech_pause_s, 0.08, 0.1),
};

function spark(hist, today, base, limit, dir) {
  const W = 220, H = 46, pad = 5;
  const vals = [...hist, ...(has(today) ? [today] : [])];
  let lo = Math.min(...vals, base, limit), hi = Math.max(...vals, base, limit);
  if (hi - lo < 1e-6) { hi += 1; lo -= 1; }
  const span = hi - lo; lo -= span * 0.12; hi += span * 0.12;
  const n = 14, x = i => pad + i * (W - 2 * pad) / (n - 1), y = v => H - pad - (v - lo) / (hi - lo) * (H - 2 * pad);
  const pts = vals.map((v, i) => [x(i), y(v)]);
  const line = pts.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('');
  const area = `${line}L${pts[pts.length - 1][0].toFixed(1)},${H}L${pts[0][0].toFixed(1)},${H}Z`;
  const last = pts[pts.length - 1];
  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="14-day trend, demo history">
    <defs><linearGradient id="spg" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#67E0D2" stop-opacity=".35"/><stop offset="1" stop-color="#67E0D2" stop-opacity="0"/></linearGradient></defs>
    <line class="sp-lim" x1="0" x2="${W}" y1="${y(limit).toFixed(1)}" y2="${y(limit).toFixed(1)}"/>
    <line class="sp-base" x1="0" x2="${W}" y1="${y(base).toFixed(1)}" y2="${y(base).toFixed(1)}"/>
    <path class="sp-area" d="${area}"/>
    <path class="sp-line sp-draw" d="${line}"/>
    ${pts.slice(0, hist.length).map(p => `<circle class="sp-pt" cx="${p[0].toFixed(1)}" cy="${p[1].toFixed(1)}" r="1.8"/>`).join('')}
    ${has(today) ? `<circle class="sp-today" cx="${last[0].toFixed(1)}" cy="${last[1].toFixed(1)}" r="4.2"/>` : ''}
  </svg>`;
}

const STEP_LABEL = { face: 'the smile', arms: 'the arm hold', speech: 'the sentence', words: "the sentence's words", timing: 'the speech timing' };
function fine(c, v) {
  if (c.unit === 'wpm') return v.toFixed(1);
  if (c.name === 'Words wrong') return `${(v * 100).toFixed(1)}%`;
  return v.toFixed(3);
}
function notMeasuredWhy(step, r) {
  const s = r[step] || {};
  if (s.status === 'skipped') return 'Skipped.';
  if (step === 'face') return s.status === 'no_smile' ? 'No smile was seen.' : 'The face was not seen clearly enough.';
  if (step === 'arms') return s.status === 'not_raised' ? 'The arms were not seen raised.' : 'The wrists were not seen for the whole hold.';
  return 'The sentence was not heard.';
}

function renderResults() {
  const real = lastResults;
  if (!real) return;
  const r = settings.simulate ? M.simulateChange(real) : real;
  const d = M.decide(r);
  const sim = !!r.simulated;
  const tag = sim ? '<span class="simbadge">simulated</span>' : real.sample ? '<span class="simbadge">sample</span>' : '';

  // headline
  const v = $('verdict');
  if (d.outcome === 'change') {
    v.className = 'verdict change';
    v.innerHTML = `<div class="vicon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 6.5v7M12 17.5v.01" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg></div>
      <div><h1>Change noticed ${tag}</h1><p>On the real call Onset would close with: <q>${esc(L.CLOSE_ESCALATE.caption)}</q></p></div>`;
  } else if (d.outcome === 'no_change') {
    v.className = 'verdict';
    v.innerHTML = `<div class="vicon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5.5 12.5l4.2 4.2 8.8-9.2" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <div><h1>Looking good: no change from your usual check-in ${tag}</h1><p>Every number is within its threshold of the demo baseline. On the real call Onset closes with: <q>${esc(L.CLOSE_NO_CHANGE.caption)}</q></p></div>`;
  } else if (['face', 'arms', 'speech'].every(s => d.unmeasured.includes(s))) {
    v.className = 'verdict partial';
    v.innerHTML = `<div class="vicon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 7v6M12 16.5v.01" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/></svg></div>
      <div><h1>Onset couldn't see or hear enough this time ${tag}</h1><p>None of the three checks could be measured. On the real call that is <q>${esc('I could not check clearly. Adding your caregiver. Please call 911 now.')}</q> Try again in good light, with your face and both hands in the picture, or turn on <b>Simulate a change</b> to see the escalation.</p></div>`;
  } else {
    const miss = d.unmeasured.map(s => STEP_LABEL[s]);
    v.className = 'verdict partial';
    v.innerHTML = `<div class="vicon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M5.5 12.5l4.2 4.2 8.8-9.2" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/></svg></div>
      <div><h1>Looking good on what Onset could measure ${tag}</h1><p>No change in what was measured. Not measured this time: ${esc(miss.join(' and '))}. On the real call, anything Onset cannot check counts like a change: it adds the caregiver and recommends calling 911.</p></div>`;
  }

  // escalation card with the caregiver's iMessage
  const e = $('escalate');
  if (d.outcome === 'change') {
    const when = r.at || new Date();
    const day = when.toLocaleDateString('en-US', { weekday: 'short', day: '2-digit', month: 'short' }).replace(',', '');
    const time = when.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
    const msg = `Onset check-in for Alex on ${day} at ${time}. Checked: smile, arm hold, repeating a sentence.\nWhat Onset noticed:\n${d.changes.map(c => `- ${c.text}.`).join('\n')}\nAlex was told to call 911 now. Please check on them right away. Onset is a research prototype and cannot diagnose.`;
    e.hidden = false;
    e.innerHTML = `<div>
        <div class="e-k">What happens next</div>
        <h2>Change noticed. In the real call, Onset adds your caregiver to this call and strongly recommends calling 911.</h2>
        <ul>${d.changes.map(c => `<li>${esc(c.text)}</li>`).join('')}</ul>
        <div class="steps911"><span class="now">Caller told to call 911 now</span><span>Caregiver texted</span><span>Caregiver joins the call</span></div>
        ${sim || real.sample ? '' : '<p class="e-note">This demo compares you with a fixed demo baseline. On a real call the baseline is the person\'s own well-day check-in, so a smile or a posture that is simply theirs is not a change.</p>'}
      </div>
      <div class="imsg" aria-label="Preview of the caregiver's iMessage">
        <div class="top"><b>Onset</b>iMessage · to the caregiver</div>
        <div class="msgs"><div class="bubble">${esc(msg)}</div><span class="when">Delivered · demo preview, nothing was sent</span></div>
      </div>`;
  } else { e.hidden = true; e.innerHTML = ''; }

  // the metric cards
  const B = M.BASELINE, LIM = M.LIMITS;
  const f = r.face || {}, a = r.arms || {}, s = r.speech || {};
  const cards = [
    { step: 'face', name: 'Smile asymmetry', sub: '1 − weaker corner lift ÷ stronger', val: f.status === 'ok' ? f.asym : null, fmt: v => v.toFixed(2), base: B.face_asymmetry, lim: LIM.face_asymmetry, dir: 'up', max: 0.5, hist: HIST.face,
      extra: f.status === 'ok' && has(f.liftLeft) ? `<span class="l">L ${f2(f.liftLeft)}</span> · <span class="r">R ${f2(f.liftRight)}</span><br>lift, eye widths` : '' },
    { step: 'arms', name: 'Arm drift difference', sub: '|left − right| over the 10 s hold, shoulder widths', val: a.status === 'ok' ? a.diff : null, fmt: v => v.toFixed(2), base: Math.abs(B.arm_drift_left - B.arm_drift_right), lim: LIM.arm_difference, dir: 'up', max: 0.4, hist: HIST.arms,
      extra: a.status === 'ok' ? `<span class="l">L ${f2(Math.max(0, a.left))}</span> · <span class="r">R ${f2(Math.max(0, a.right))}</span><br>usual ${f2(B.arm_drift_left)} · ${f2(B.arm_drift_right)}` : '',
      alsoChanged: a.status === 'ok' && Math.max(a.left, a.right) >= LIM.arm_single, note: `An arm falling ${LIM.arm_single.toFixed(2)} or more on its own also counts.` },
    { step: 'speech', name: 'Words wrong', sub: `word error rate on “${SENTENCE}”`, val: s.status === 'ok' ? s.wer : null, fmt: v => `${Math.round(v * 100)}%`, base: B.speech_wer, lim: LIM.speech_wer, dir: 'up', max: 0.6, hist: HIST.wer,
      extra: s.transcript ? `heard: “${esc(s.transcript)}”` : '', why: s.status === 'ok' && !has(s.wer) ? `Not measured: ${s.werNote || 'no transcript'}.` : null },
    { step: 'speech', name: 'Speaking rate', sub: 'words per minute', val: s.status === 'ok' ? s.wpm : null, fmt: v => `${Math.round(v)}`, unit: 'wpm', base: B.speech_wpm, lim: LIM.speech_wpm, dir: 'down', max: 220, hist: HIST.wpm,
      why: s.status === 'ok' && !has(s.wpm) ? 'Not measured: no speech timing from the microphone.' : null, limFmt: v => `${Math.round(v)} wpm or less` },
    { step: 'speech', name: 'Longest pause', sub: 'gap between words while saying the sentence', val: s.status === 'ok' ? s.pause : null, fmt: v => v.toFixed(1), unit: 's', base: B.speech_pause_s, lim: LIM.speech_pause, dir: 'up', max: 2.5, hist: HIST.pause,
      why: s.status === 'ok' && !has(s.pause) ? 'Not measured: no speech timing from the microphone.' : null },
  ];
  $('metrics').innerHTML = cards.map((c, i) => {
    const measured = has(c.val);
    const changed = measured && ((c.dir === 'up' ? c.val >= c.lim - 1e-9 : c.val <= c.lim + 1e-9) || c.alsoChanged);
    const chip = !measured ? '<span class="chip unclear">not measured</span>' : changed ? '<span class="chip changed">change</span>' : '<span class="chip ok">no change</span>';
    const fmtLim = c.limFmt ? c.limFmt(c.lim) : `${c.fmt(c.lim)}${c.unit ? ' ' + c.unit : ''} or more`;
    const pos = v => `${clamp(v / c.max) * 100}%`;
    const why = !measured ? (c.why || notMeasuredWhy(c.step, r)) : c.why;
    // Near a limit, show one more digit so 0.196 does not read as "0.20, no change".
    const shown = !measured ? '–' : c.fmt(c.val) === c.fmt(c.lim) ? fine(c, c.val) : c.fmt(c.val);
    return `<article class="metric ${changed ? 'changed' : ''} ${measured ? '' : 'unmeasured'}" style="animation-delay:${0.05 * i}s">
      <div class="mh"><div><b>${c.name}</b><small>${esc(c.sub)}</small></div>${chip}</div>
      <div class="big"><span class="val">${shown}</span>${c.unit ? `<span class="unit">${c.unit}</span>` : ''}<span class="extra">${c.extra || ''}</span></div>
      <div class="gauge"><div class="fill" data-w="${measured ? pos(c.val) : '0%'}"></div><i class="tick base" style="left:${pos(c.base)}"></i><i class="tick lim" style="left:${pos(c.lim)}"></i></div>
      <div class="legend3"><span><i></i>usual <em>${c.fmt(c.base)}${c.unit ? ' ' + c.unit : ''}</em></span><span><i class="lim"></i>change at <em>${fmtLim}</em></span></div>
      <div class="spark">${spark(c.hist, measured ? c.val : null, c.base, c.lim, c.dir)}<div class="cap14">14 days<br>demo history</div></div>
      ${why ? `<div class="mnote">${esc(why)}</div>` : c.note ? `<div class="mnote">${esc(c.note)}</div>` : ''}
    </article>`;
  }).join('');
  requestAnimationFrame(() => requestAnimationFrame(() => {
    for (const el of document.querySelectorAll('.gauge .fill')) el.style.width = el.dataset.w;
  }));

  $('rnote').innerHTML = `${sim ? 'Simulated: your measured numbers were pushed past the thresholds to show the escalation path. ' : ''}`
    + 'Thresholds are the agent\'s own (<code>onset/decide.py</code>); the baseline is a fixed demo baseline, where the real call uses the person\'s own well-day check-in.';
}

// Read-only hook for automated checks.
Object.defineProperty(window, 'onsetDemo', { value: Object.freeze({ get results() { return lastResults; }, get vision() { return !!vision; },
  get face() { return lastFace?.geom ? { yaw: +lastFace.geom.yaw.toFixed(1), roll: +lastFace.geom.roll.toFixed(1), problem: M.faceProblem(lastFace) } : null; } }) });
