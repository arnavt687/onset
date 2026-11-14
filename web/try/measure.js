// The measurement maths of the real Onset agent, ported to the browser.
//
//   smile     onset/measure/face.py   corner lift in a face frame, asymmetry = 1 - weaker / stronger
//   arms      onset/measure/arms.py   wrist drift below the shoulder line, in shoulder widths
//   sentence  onset/speech.py         word error rate, words per minute, longest pause
//   rules     onset/decide.py         the same thresholds; the baseline is a fixed demo baseline
//
// Everything here is a pure function on landmark arrays or numbers (no DOM, no MediaPipe), so
// the numbers mean the same thing as the agent's. Simplifications are noted where they happen.

// ------------------------------------------------------------------ thresholds and baseline

// onset/decide.py Thresholds (the subset this demo applies).
export const TH = {
  face_asymmetry_increase: 0.10,
  arm_drift_difference_absolute: 0.20,
  arm_single_drift_absolute: 0.50,
  speech_wer_increase: 0.10,
  speech_rate_drop_fraction: 0.20,
  speech_pause_floor_s: 1.0,
  speech_pause_increase_s: 0.7,
};

// A fixed "usual check-in" for the demo, built so a typical person passes. The real call compares
// with the person's own well-day check-in. Smile asymmetry from a webcam has about +-0.1 of
// run-to-run noise, and ordinary smiles of real people measured 0.10 to 0.26 in testing, so the
// demo's usual asymmetry is 0.18 (a change is still +0.10 on top of it, as in decide.py).
export const BASELINE = {
  face_asymmetry: 0.18,
  arm_drift_left: 0.02,
  arm_drift_right: 0.02,
  speech_wer: 0.0,
  speech_wpm: 140,
  speech_pause_s: 0.4,
};

// Where each rule fires, given the baseline (a value exactly at a limit counts as a change).
export const LIMITS = {
  face_asymmetry: BASELINE.face_asymmetry + TH.face_asymmetry_increase,
  arm_difference: TH.arm_drift_difference_absolute,
  arm_single: TH.arm_single_drift_absolute,
  speech_wer: BASELINE.speech_wer + TH.speech_wer_increase,
  speech_wpm: BASELINE.speech_wpm * (1 - TH.speech_rate_drop_fraction),
  speech_pause: Math.max(TH.speech_pause_floor_s, BASELINE.speech_pause_s + TH.speech_pause_increase_s),
};

const EPS = 1e-9;

// ------------------------------------------------------------------ small helpers

export function median(a) {
  const b = a.filter(Number.isFinite).sort((x, y) => x - y);
  if (!b.length) return NaN;
  const m = b.length >> 1;
  return b.length % 2 ? b[m] : (b[m - 1] + b[m]) / 2;
}

// numpy.percentile with linear interpolation.
export function percentile(a, q) {
  const b = a.filter(Number.isFinite).sort((x, y) => x - y);
  if (!b.length) return NaN;
  const pos = (b.length - 1) * q / 100, lo = Math.floor(pos), hi = Math.ceil(pos);
  return b[lo] + (b[hi] - b[lo]) * (pos - lo);
}

const deg = r => r * 180 / Math.PI;

// ------------------------------------------------------------------ smile (face.py)

// MediaPipe Face Mesh indices. "R_" is the person's right = image left (non-mirrored frame).
export const FACE = {
  R_EYE: [33, 133], L_EYE: [263, 362], R_MOUTH: 61, L_MOUTH: 291, NOSE: 1,
  LIPS_OUTER: [61, 185, 40, 39, 37, 0, 267, 269, 270, 409, 291, 375, 321, 405, 314, 17, 84, 181, 91, 146, 61],
  LIPS_INNER: [78, 191, 80, 81, 82, 13, 312, 311, 310, 415, 308, 324, 318, 402, 317, 14, 87, 178, 88, 95, 78],
};
const KEY_POINTS = [33, 133, 263, 362, 61, 291, 1];
export const FACE_LIMITS = {
  MAX_YAW_DEG: 20, EDGE_MARGIN: 0.02, MIN_LUMA: 40, MIN_SMILE_LIFT: 0.03, MIN_ASYM_FOR_SIDE: 0.05,
  PEAK_FRACTION: 0.5, MIN_PEAK_FRAMES: 5, NOSE_DEPTH: 0.64, MIN_NEUTRAL_FRAMES: 5, MIN_SMILE_FRAMES: 8,
};

// One frame's face reduced to what the smile check needs. lm: normalized landmarks; w, h: frame px.
export function faceGeometry(lm, w, h) {
  if (!lm || lm.length < 400) return null;
  const P = i => [lm[i].x * w, lm[i].y * h];
  const mid = (a, b) => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const eyeR = mid(P(33), P(133)), eyeL = mid(P(263), P(362));
  const vx = eyeL[0] - eyeR[0], vy = eyeL[1] - eyeR[1];
  const iod = Math.hypot(vx, vy);
  if (!(iod > 1e-6)) return null;
  const ax = [vx / iod, vy / iod];          // along the eye line, towards the person's left
  const ay = [-ax[1], ax[0]];                // down the face
  const c = mid(eyeL, eyeR);
  const local = p => { const dx = p[0] - c[0], dy = p[1] - c[1]; return [(dx * ax[0] + dy * ax[1]) / iod, (dx * ay[0] + dy * ay[1]) / iod]; };
  const ml = local(P(291)), mr = local(P(61)), nose = local(P(1));
  return {
    center: c, xAxis: ax, yAxis: ay, iod,
    roll: deg(Math.atan2(ax[1], ax[0])),
    yaw: deg(Math.atan(nose[0] / FACE_LIMITS.NOSE_DEPTH)),
    // rows [left, right] (person's own), cols [outward, up], inter-ocular units
    corners: [[ml[0], -ml[1]], [-mr[0], -mr[1]]],
  };
}

// Face frame (IOD units, x along the eye line, y down) -> frame pixels.
export function faceToImage(g, q) {
  return [g.center[0] + g.iod * (q[0] * g.xAxis[0] + q[1] * g.yAxis[0]), g.center[1] + g.iod * (q[0] * g.xAxis[1] + q[1] * g.yAxis[1])];
}
export function cornerToImage(g, side, outUp) {
  const sign = side === 0 ? 1 : -1;
  return faceToImage(g, [sign * outUp[0], -outUp[1]]);
}

// The framing problem on one observation (face.py face_problem), or null.
export function faceProblem(obs) {
  if (!obs || !obs.lm) return obs && obs.luma < FACE_LIMITS.MIN_LUMA ? 'too_dark' : 'no_face';
  const g = obs.geom;
  if (!g) return 'no_face';
  const mx = FACE_LIMITS.EDGE_MARGIN, my = FACE_LIMITS.EDGE_MARGIN;
  for (const i of KEY_POINTS) {
    const p = obs.lm[i];
    if (p.x < mx || p.x > 1 - mx || p.y < my || p.y > 1 - my) return 'edge';
  }
  if (Math.abs(g.yaw) > FACE_LIMITS.MAX_YAW_DEG) return 'turned';
  if (obs.luma < FACE_LIMITS.MIN_LUMA) return 'too_dark';
  return null;
}

export function asymmetryIndex(liftLeft, liftRight) {
  const strong = Math.max(liftLeft, liftRight);
  if (!(strong > 0)) return null;
  const weak = Math.max(Math.min(liftLeft, liftRight), 0);
  return Math.min(1, Math.max(0, 1 - weak / strong));
}

// Per-corner rest position: the median over the neutral frames.
export function restCorners(neutral) {
  return [0, 1].map(s => [0, 1].map(k => median(neutral.map(c => c[s][k]))));
}

// Lift of each corner relative to rest, projected on the up-and-outward diagonal.
export function liftOf(corners, rest) {
  return [0, 1].map(s => ((corners[s][0] - rest[s][0]) + (corners[s][1] - rest[s][1])) / Math.SQRT2);
}

// face.py corner_lifts + evaluate: neutral and smile are arrays of FaceGeom.corners from usable frames.
export function smileResult(neutral, smile) {
  const F = FACE_LIMITS;
  if (neutral.length < F.MIN_NEUTRAL_FRAMES || smile.length < F.MIN_SMILE_FRAMES) {
    return { status: 'partial', reason: 'face not seen clearly enough', frames: neutral.length + smile.length };
  }
  const rest = restCorners(neutral);
  const lifts = smile.map(c => liftOf(c, rest));
  const total = lifts.map(l => l[0] + l[1]);
  const top = percentile(total, 90);
  let peak = total.map(t => top > 0 ? t >= F.PEAK_FRACTION * top : true);
  const need = Math.min(F.MIN_PEAK_FRAMES, total.length);
  if (peak.filter(Boolean).length < need) {
    const order = total.map((t, i) => [t, i]).sort((a, b) => b[0] - a[0]).slice(0, need).map(x => x[1]);
    peak = total.map((_, i) => order.includes(i));
  }
  const sel = lifts.filter((_, i) => peak[i]);
  const liftLeft = median(sel.map(l => l[0])), liftRight = median(sel.map(l => l[1]));
  const strong = Math.max(liftLeft, liftRight);
  const out = { liftLeft, liftRight, frames: neutral.length + smile.length, peakFrames: sel.length, rest };
  if (strong < F.MIN_SMILE_LIFT) return { ...out, status: 'no_smile', reason: 'no smile seen' };
  const asym = asymmetryIndex(liftLeft, liftRight);
  const weaker = asym !== null && asym >= F.MIN_ASYM_FOR_SIDE ? (liftLeft < liftRight ? 'left' : 'right') : null;
  return { ...out, status: 'ok', asym, weaker };
}

// ------------------------------------------------------------------ arms (arms.py)

export const ARMS = {
  HOLD_SECONDS: 10, MIN_HOLD_S: 6, STABLE_S: 0.5, STABLE_RANGE: 0.20, UP_LEVEL: 0.25, DOWN_LEVEL: 0.75,
  DOWN_SUSTAIN_S: 0.3, WINDOW_S: 1.5, SMOOTH_S: 0.5, MIN_WINDOW_FILL: 0.4, VIS_MIN: 0.5, EDGE: 0.01, SIDE_NOISE: 0.03,
};
// Rows in a pose sample (person's own sides) and the MediaPipe Pose indices they come from.
export const LS = 0, RS = 1, LE = 2, RE = 3, LW = 4, RW = 5;
export const POSE_INDEX = [11, 12, 13, 14, 15, 16];
const OK_PT = 0, BELOW = 1, ABOVE = 2, MISSING = 3;

// MediaPipe pose landmarks -> 6 x [x_px, y_px, visibility], person's left shoulder on the image right.
export function poseSample(t, lm, w, h, luma) {
  if (!lm || lm.length < 17) return { t, pts: null, w, h, luma };
  let pts = POSE_INDEX.map(i => [lm[i].x * w, lm[i].y * h, lm[i].visibility ?? 0]);
  if (pts[LS][0] < pts[RS][0]) pts = [pts[RS], pts[LS], pts[RE], pts[LE], pts[RW], pts[LW]];
  return { t, pts, w, h, luma };
}

function wristState(x, y, vis, w, h) {
  if (!Number.isFinite(y)) return MISSING;
  if (y > h * (1 - ARMS.EDGE)) return BELOW;
  if (y < h * ARMS.EDGE) return ABOVE;
  if (vis >= ARMS.VIS_MIN && x >= -w * ARMS.EDGE && x <= w * (1 + ARMS.EDGE)) return OK_PT;
  return MISSING;
}

function shouldersOk(s) {
  if (!s.pts) return false;
  for (const r of [LS, RS]) {
    const [x, y, v] = s.pts[r];
    if (v < ARMS.VIS_MIN || x < 0 || x > s.w || y < 0 || y > s.h) return false;
  }
  return Math.hypot(s.pts[LS][0] - s.pts[RS][0], s.pts[LS][1] - s.pts[RS][1]) > 1;
}

// One sample in the shoulder-based body frame: wrists as (x, y) in shoulder widths, y down.
export function bodyFrame(s) {
  const out = { t: s.t, ok: false, sw: NaN, rel: [[NaN, NaN], [NaN, NaN]], state: [MISSING, MISSING] };
  if (!shouldersOk(s)) return out;
  const ls = s.pts[LS], rs = s.pts[RS];
  let ux = ls[0] - rs[0], uy = ls[1] - rs[1];
  const sw = Math.hypot(ux, uy); ux /= sw; uy /= sw;
  const dx = -uy, dy = ux;
  const mx = (ls[0] + rs[0]) / 2, my = (ls[1] + rs[1]) / 2;
  out.ok = true; out.sw = sw;
  [LW, RW].forEach((row, side) => {
    const [x, y, v] = s.pts[row];
    const st = wristState(x, y, v, s.w, s.h);
    out.state[side] = st;
    let p;
    if (st === OK_PT) p = [x, y];
    else if (st === BELOW) p = [x, Math.max(y, s.h)];
    else return;
    out.rel[side] = [((p[0] - mx) * ux + (p[1] - my) * uy) / sw, ((p[0] - mx) * dx + (p[1] - my) * dy) / sw];
  });
  return out;
}

export function wristUp(b, side) {
  if (!b.ok) return false;
  return (b.state[side] === OK_PT && b.rel[side][1] <= ARMS.UP_LEVEL) || b.state[side] === ABOVE;
}
export function wristDown(b, side) {
  if (!b.ok) return false;
  return (b.state[side] === OK_PT && b.rel[side][1] >= ARMS.DOWN_LEVEL) || b.state[side] === BELOW;
}

function fps(ts) {
  const d = [];
  for (let i = 1; i < ts.length; i++) if (ts[i] > ts[i - 1]) d.push(ts[i] - ts[i - 1]);
  return d.length ? 1 / median(d) : 15;
}

// arms.py find_hold_start, on the recent body frames: the time both wrists have been up and
// steady for STABLE_S, or null.
export function holdStart(frames) {
  if (frames.length < 3) return null;
  const last = frames[frames.length - 1];
  if (!(wristUp(last, 0) && wristUp(last, 1))) return null;
  if (last.t - frames[0].t < ARMS.STABLE_S * 0.9) return null;
  const win = frames.filter(f => f.t >= last.t - ARMS.STABLE_S && f.ok);
  const need = Math.max(3, Math.floor(0.6 * ARMS.STABLE_S * fps(frames.map(f => f.t))));
  if (win.length < need || !win.every(f => wristUp(f, 0) && wristUp(f, 1))) return null;
  for (const side of [0, 1]) {
    const ys = win.map(f => f.rel[side][1]).filter(Number.isFinite);
    if (ys.length && Math.max(...ys) - Math.min(...ys) > ARMS.STABLE_RANGE) return null;
  }
  return last.t;
}

function medianIn(frames, side, a, b, rate) {
  const ys = frames.filter(f => f.t >= a && f.t <= b).map(f => f.rel[side][1]).filter(Number.isFinite);
  if (ys.length < Math.max(2, ARMS.MIN_WINDOW_FILL * (b - a) * rate)) return null;
  return median(ys);
}

// arms.py side_drift: end = last-window median - first-window median; peak = largest rolling
// SMOOTH_S median - start (catches a drop that was corrected); drift = max(end, peak).
export function sideDrift(frames, side, t0, t1) {
  const rate = fps(frames.map(f => f.t));
  const start = medianIn(frames, side, t0, t0 + ARMS.WINDOW_S, rate);
  const endLevel = medianIn(frames, side, t1 - ARMS.WINDOW_S, t1, rate);
  if (start === null || endLevel === null) return { drift: null, start };
  const end = endLevel - start;
  let peak = end;
  for (const f of frames) {
    if (f.t < t0 || f.t > t1) continue;
    const lvl = medianIn(frames, side, f.t - ARMS.SMOOTH_S, f.t, rate);
    if (lvl !== null && lvl - start > peak) peak = lvl - start;
  }
  return { drift: Math.max(end, peak), end, peak, start };
}

// Drift so far, for the live meters (arms.py ArmMeter.live).
export function liveDrift(frames, t0, now) {
  const rate = fps(frames.map(f => f.t));
  const out = [null, null], start = [null, null];
  for (const side of [0, 1]) {
    start[side] = medianIn(frames, side, t0, t0 + ARMS.WINDOW_S, rate);
    const cur = medianIn(frames, side, now - ARMS.SMOOTH_S, now, rate);
    if (start[side] !== null && cur !== null && now - t0 >= ARMS.WINDOW_S) out[side] = cur - start[side];
  }
  return { drift: out, start };
}

// First time a wrist stayed down DOWN_SUSTAIN_S (both arms coming down ends the hold early).
export function bothDownSince(frames) {
  let since = null;
  for (const f of frames) {
    if (!f.ok) continue;
    if (wristDown(f, 0) && wristDown(f, 1)) { if (since === null) since = f.t; }
    else since = null;
  }
  const last = frames[frames.length - 1];
  return since !== null && last && last.t - since >= ARMS.DOWN_SUSTAIN_S ? since : null;
}

// The arm step's result from the body frames of the hold [t0, t1].
export function armsResult(frames, t0, t1) {
  const hold = t1 - t0;
  // Arms lowered together at the end: leave the lowering out of the last window (LOWER_TRIM_S).
  const tMeas = frames.lowered ? Math.max(t0 + 0.5, t1 - 0.5) : t1;
  const L = sideDrift(frames, 0, t0, tMeas), R = sideDrift(frames, 1, t0, tMeas);
  if (L.drift === null || R.drift === null) return { status: 'partial', reason: 'wrists lost during the hold', hold };
  if (hold < ARMS.MIN_HOLD_S && Math.max(L.drift, R.drift) < TH.arm_single_drift_absolute) {
    return { status: 'partial', reason: 'hold too short', hold, left: L.drift, right: R.drift };
  }
  const diff = Math.abs(L.drift - R.drift);
  const weaker = diff > ARMS.SIDE_NOISE ? (L.drift > R.drift ? 'left' : 'right') : null;
  return { status: 'ok', left: L.drift, right: R.drift, diff, weaker, hold };
}

// arms.py frame_problem (only the problems the demo reports).
export function armsProblem(s, { wrists = true } = {}) {
  if (!s || !s.pts) return s && s.luma < 45 ? 'too_dark' : 'no_body';
  if (!shouldersOk(s)) {
    const ys = [s.pts[LS][1], s.pts[RS][1]], xs = [s.pts[LS][0], s.pts[RS][0]], vs = [s.pts[LS][2], s.pts[RS][2]];
    if (ys.some(y => y > s.h)) return 'shoulders_low';
    if (ys.some(y => y < 0)) return 'shoulders_high';
    if (xs.some(x => x < 0 || x > s.w)) return 'shoulders_side';
    return vs.every(v => v < ARMS.VIS_MIN) ? 'no_body' : 'shoulders_side';
  }
  if (!wrists) return null;
  const st = [LW, RW].map(r => wristState(...s.pts[r], s.w, s.h));
  const lost = st.map(x => x === MISSING || x === ABOVE);
  if (lost[0] && lost[1]) return st.every(x => x === ABOVE) ? 'hands_high' : 'both_hands';
  if (lost[0]) return 'left_hand';
  if (lost[1]) return 'right_hand';
  return null;
}

// ------------------------------------------------------------------ sentence (speech.py)

const CONTRACTIONS = { "can't": 'cannot', "won't": 'will not', "it's": 'it is', "that's": 'that is', "let's": 'let us', "what's": 'what is' };
const SUFFIXES = [["n't", ' not'], ["'re", ' are'], ["'ll", ' will'], ["'ve", ' have'], ["'m", ' am'], ["'d", ' would']];
const ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen'.split(' ');
export const FILLERS = new Set(['um', 'uh', 'er', 'erm', 'ah', 'hmm', 'mm', 'uhm', 'umm']);

// Lowercase words, contractions expanded, punctuation removed (speech.py normalize_words; small
// numbers only).
export function normalizeWords(text) {
  let s = String(text || '').toLowerCase().replace(/[’‘]/g, "'");
  s = s.replace(/\([^)]*\)|\[[^\]]*\]/g, ' ').replace(/\b\d{1,2}\b/g, d => ` ${ONES[+d] ?? d} `).replace(/[-–—/]/g, ' ');
  const out = [];
  for (let tok of s.split(/\s+/)) {
    tok = tok.replace(/^[^\w']+|[^\w']+$/g, '').replace(/^'+|'+$/g, '');
    if (!tok) continue;
    if (CONTRACTIONS[tok]) tok = CONTRACTIONS[tok];
    else for (const [suf, rep] of SUFFIXES) if (tok.endsWith(suf) && tok.length > suf.length) { tok = tok.slice(0, -suf.length) + rep; break; }
    out.push(...tok.replace(/[^\w ]/g, '').split(' ').filter(Boolean));
  }
  return out;
}

// Levenshtein alignment: [op, expected, heard] with op in ok / sub / del / ins.
export function alignWords(exp, got) {
  const n = exp.length, m = got.length;
  const d = Array.from({ length: n + 1 }, (_, i) => Array.from({ length: m + 1 }, (_, j) => i === 0 ? j : j === 0 ? i : 0));
  for (let i = 1; i <= n; i++) for (let j = 1; j <= m; j++) {
    d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (exp[i - 1] === got[j - 1] ? 0 : 1));
  }
  const ops = [];
  let i = n, j = m;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && d[i][j] === d[i - 1][j - 1] + (exp[i - 1] !== got[j - 1] ? 1 : 0)) {
      ops.push([exp[i - 1] === got[j - 1] ? 'ok' : 'sub', exp[i - 1], got[j - 1]]); i--; j--;
    } else if (i > 0 && d[i][j] === d[i - 1][j] + 1) { ops.push(['del', exp[i - 1], null]); i--; }
    else { ops.push(['ins', null, got[j - 1]]); j--; }
  }
  return ops.reverse();
}

export function wordErrorRate(expected, heard) {
  const exp = normalizeWords(expected), got = normalizeWords(heard).filter(w => !FILLERS.has(w));
  if (!exp.length) return got.length ? 1 : 0;
  return alignWords(exp, got).filter(o => o[0] !== 'ok').length / exp.length;
}

// Voice activity from 20 ms mic levels: [{t, rms}] -> speech onset/offset and the longest pause.
// The agent gets word timestamps from Scribe; in the browser the mic level stands in for them.
export function voiceTiming(levels, { minGap = 0.18, minSeg = 0.08 } = {}) {
  if (levels.length < 5) return null;
  const rms = levels.map(l => l.rms);
  const floor = percentile(rms, 15);
  const thr = Math.max(floor * 3.2, percentile(rms, 98) * 0.16, 0.006);
  let segs = [];
  let cur = null;
  for (const l of levels) {
    if (l.rms >= thr) { if (!cur) cur = [l.t, l.t]; else cur[1] = l.t; }
    else if (cur && l.t - cur[1] > 0.06) { segs.push(cur); cur = null; }
  }
  if (cur) segs.push(cur);
  const merged = [];
  for (const s of segs) {
    const prev = merged[merged.length - 1];
    if (prev && s[0] - prev[1] < minGap) prev[1] = s[1]; else merged.push([s[0], s[1]]);
  }
  segs = merged.filter(s => s[1] - s[0] >= minSeg);
  // A short isolated blip before or after the sentence (a breath, a click) is not speech.
  while (segs.length > 1 && segs[0][1] - segs[0][0] < 0.25 && segs[1][0] - segs[0][1] > 0.7) segs.shift();
  while (segs.length > 1 && segs[segs.length - 1][1] - segs[segs.length - 1][0] < 0.25 && segs[segs.length - 1][0] - segs[segs.length - 2][1] > 0.7) segs.pop();
  if (!segs.length) return { onset: null, offset: null, longestPause: null, speaking: 0, threshold: thr };
  let longest = 0;
  for (let i = 1; i < segs.length; i++) longest = Math.max(longest, segs[i][0] - segs[i - 1][1]);
  return {
    onset: segs[0][0], offset: segs[segs.length - 1][1] + 0.02, longestPause: longest,
    speaking: segs.reduce((a, s) => a + s[1] - s[0], 0), segments: segs.length, threshold: thr,
  };
}

// ------------------------------------------------------------------ the decision (decide.py)

const has = v => typeof v === 'number' && Number.isFinite(v);

// The change lines decide.py would write, for the measured values. Steps that were not
// measured are listed separately; in the real call they count like a change (UNCLEAR).
export function decide(r) {
  const changes = [], unmeasured = [];
  const f = r.face;
  if (f && f.status === 'ok' && has(f.asym)) {
    if (f.asym - BASELINE.face_asymmetry >= TH.face_asymmetry_increase - EPS) {
      changes.push({ step: 'face', text: f.weaker ? `The ${f.weaker} side of the smile lifted less than usual` : 'One side of the smile lifted less than usual' });
    }
  } else unmeasured.push('face');
  const a = r.arms;
  if (a && a.status === 'ok' && has(a.diff)) {
    const side = a.left >= a.right ? 'left' : 'right';
    if (Math.max(a.left, a.right) >= TH.arm_single_drift_absolute - EPS) changes.push({ step: 'arms', text: `The ${side} arm dropped while it was held up` });
    else if (a.diff >= TH.arm_drift_difference_absolute - EPS) changes.push({ step: 'arms', text: `The ${a.weaker || side} arm drifted down much more than the other` });
  } else unmeasured.push('arms');
  const s = r.speech;
  if (s && s.status === 'ok') {
    if (has(s.wer) && s.wer - BASELINE.speech_wer >= TH.speech_wer_increase - EPS) changes.push({ step: 'speech', text: 'The sentence came out less clearly than usual' });
    if (has(s.wpm) && s.wpm <= LIMITS.speech_wpm + EPS) changes.push({ step: 'speech', text: 'Speech was slower than usual' });
    if (has(s.pause) && s.pause >= LIMITS.speech_pause - EPS) changes.push({ step: 'speech', text: 'There was a long pause while saying the sentence' });
    if (!has(s.wer)) unmeasured.push('words');
    if (!has(s.wpm) && !has(s.pause)) unmeasured.push('timing');
  } else unmeasured.push('speech');
  return { outcome: changes.length ? 'change' : unmeasured.length ? 'partial' : 'no_change', changes, unmeasured };
}

// "Simulate a change": push today's numbers past the thresholds the way an acted change would.
export function simulateChange(r) {
  const face = { ...(r.face || {}), status: 'ok', simulated: true };
  face.liftRight = Math.max(has(face.liftRight) ? face.liftRight : 0.10, 0.08);
  face.liftLeft = face.liftRight * 0.62;
  face.asym = asymmetryIndex(face.liftLeft, face.liftRight); face.weaker = 'left';
  const arms = { ...(r.arms || {}), status: 'ok', simulated: true };
  arms.right = has(arms.right) ? Math.min(Math.max(arms.right, 0), 0.08) : 0.02;
  arms.left = arms.right + 0.29; arms.diff = arms.left - arms.right; arms.weaker = 'left'; arms.hold = arms.hold || 10;
  const speech = { ...(r.speech || {}), status: 'ok', simulated: true };
  speech.wpm = Math.min(has(speech.wpm) ? speech.wpm : BASELINE.speech_wpm, BASELINE.speech_wpm) * 0.68;
  speech.pause = 1.7;
  if (!has(speech.wer)) speech.werNote = speech.werNote || 'not measured in this browser';
  return { ...r, face, arms, speech, simulated: true };
}
