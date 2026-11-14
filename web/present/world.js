/* The 3D world behind the Onset presentation: one renderer, one canvas, one loop.
 *
 * The face and the figure made of landmark points, the point shader and the palette are the
 * designs from the pitch deck (web/pitch/index.html), ported here so the two decks look alike.
 * The camera flies along x from one scene's station to the next.
 *
 * The 3D shows a face and a body as measured points, so the audience sees that Onset measures
 * a person against themselves. Every word on a slide is HTML, not WebGL.
 */

const THREE_LOCAL = '../pitch/vendor/three/three.module.js';
const THREE_CDN = 'https://cdn.jsdelivr.net/npm/three@0.186.1/build/three.module.js';

export const COL = { glow: 0x67E0D2, left: 0x26A898, right: 0x7E80F2, alert: 0xFF5B45, text: 0xE9EEF2, dim: 0x4B6478, bg: 0x070C14 };
export const SCENE_X = { cover: 0, problem: 44, measure: 88, demo: 132, escalate: 176, how: 220, close: 264 };

const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
const lerp = (a, b, t) => a + (b - a) * t;
const easeIO = t => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const easeOut = t => 1 - Math.pow(1 - clamp(t), 3);
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

const POINT_VS = `
attribute float aSize; attribute vec3 aColor; attribute float aAlpha;
uniform float uPx; uniform float uScale; uniform float uOpacity; uniform float uFog; uniform float uScan; uniform float uScanOn;
uniform vec3 uTint; uniform float uTintK;
varying vec3 vColor; varying float vAlpha;
void main(){
  vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_Position = projectionMatrix * mv;
  float depth = -mv.z;
  float scan = uScanOn * exp(-pow(position.y - uScan, 2.0) / 0.0025);
  gl_PointSize = max(1.0, aSize * uScale * uPx * (1.0 + scan * 1.2) / depth);
  float fog = exp(-pow(uFog * depth, 2.0));
  vColor = mix(mix(aColor, uTint, uTintK), vec3(1.0), clamp(scan * 0.7, 0.0, 1.0));
  vAlpha = aAlpha * uOpacity * fog * (1.0 + scan * 1.4);
}`;
const POINT_FS = `
varying vec3 vColor; varying float vAlpha;
void main(){
  vec2 c = gl_PointCoord - 0.5; float d = length(c) * 2.0;
  if (d > 1.0) discard;
  float core = smoothstep(1.0, 0.0, d); float a = core * core * 0.85 + smoothstep(0.35, 0.0, d) * 0.6;
  gl_FragColor = vec4(vColor, a * vAlpha);
}`;

export async function loadThree() {
  try { return await import(THREE_LOCAL); } catch { return await import(THREE_CDN); }
}

export function makeWorld(THREE, canvas, { reduced = false } = {}) {
  const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' });
  renderer.setClearColor(COL.bg, 1);
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  const scene = new THREE.Scene();
  scene.fog = new THREE.FogExp2(COL.bg, 0.018);
  const camera = new THREE.PerspectiveCamera(35, 16 / 9, 0.1, 2000);
  const shared = { uPx: { value: 600 }, uFog: { value: 0.018 } };
  const V = (x, y, z) => new THREE.Vector3(x, y, z);
  const rnd = rng(20261114);

  function pointsMat() {
    return new THREE.ShaderMaterial({
      uniforms: { uPx: shared.uPx, uFog: shared.uFog, uScale: { value: 1 }, uOpacity: { value: 1 }, uScan: { value: -9 }, uScanOn: { value: 0 },
        uTint: { value: new THREE.Color(COL.alert) }, uTintK: { value: 0 } },
      vertexShader: POINT_VS, fragmentShader: POINT_FS, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    });
  }
  function cloud(n) {
    const g = new THREE.BufferGeometry();
    const pos = new Float32Array(n * 3), col = new Float32Array(n * 3), size = new Float32Array(n), alpha = new Float32Array(n);
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('aColor', new THREE.BufferAttribute(col, 3));
    g.setAttribute('aSize', new THREE.BufferAttribute(size, 1));
    g.setAttribute('aAlpha', new THREE.BufferAttribute(alpha, 1));
    const p = new THREE.Points(g, pointsMat()); p.frustumCulled = false;
    const c = new THREE.Color();
    p.set = (i, x, y, z, hex, s, a) => { pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z; c.set(hex); col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; size[i] = s; alpha[i] = a; };
    p.paint = (i, hex, s, a) => { c.set(hex); col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; size[i] = s; alpha[i] = a; };
    p.arr = { pos, col, size, alpha };
    p.dirty = (...names) => names.forEach(nm => { g.attributes[nm].needsUpdate = true; });
    return p;
  }
  const lineMat = (hex, opacity = .5) => new THREE.LineBasicMaterial({ color: hex, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false });

  /* A thing in the world that fades in and out as its scene comes and goes. */
  const actors = [];
  function actor(group, mats, lineMats = []) {
    const a = { group, vis: 0, want: 0, base: lineMats.map(m => m.opacity),
      apply() { mats.forEach(m => { m.uniforms.uOpacity.value = a.vis; }); lineMats.forEach((m, i) => { m.opacity = a.base[i] * a.vis; }); group.visible = a.vis > .004; } };
    actors.push(a); scene.add(group); a.apply(); return a;
  }

  /* ---------- dust: motes along the whole path, for parallax in flight */
  const dust = cloud(6000);
  for (let i = 0; i < 6000; i++) dust.set(i, -40 + rnd() * 350, (rnd() - .5) * 60, -70 + rnd() * 84, rnd() < .08 ? COL.glow : 0x7f9bb3, .04 + rnd() * .09, .12 + rnd() * .35);
  scene.add(dust);

  /* ---------- face made of landmark points (pitch deck design) */
  function faceData() {
    const P = []; const rows = []; const contours = [];
    const Wd = y => { const t = (y - 0.08) / 1.12; let w = 0.8 * Math.sqrt(Math.max(0, 1 - t * t)); if (y < -0.1) w *= 1 - 0.3 * Math.pow((-y - 0.1) / 0.95, 1.5); return w; };
    const g2 = (x, y, cx, cy, sx, sy) => Math.exp(-((x - cx) ** 2) / sx - ((y - cy) ** 2) / sy);
    const depth = (x, y) => {
      const w = Math.max(Wd(y), 0.02), u = clamp(x / w, -1, 1);
      let z = 0.62 * Math.pow(Math.max(0, 1 - u * u), 0.55) * (0.78 + 0.22 * Math.cos(y * 1.2));
      z += 0.28 * g2(x, y, 0, -0.02, 0.01, 0.07) * (y < 0.32 ? 1 : 0.3) + 0.1 * g2(x, y, 0, -0.2, 0.012, 0.01);
      z -= 0.07 * (g2(x, y, 0.33, 0.27, 0.012, 0.012) + g2(x, y, -0.33, 0.27, 0.012, 0.012));
      z += 0.05 * g2(x, y, 0, 0.45, 0.25, 0.006) + 0.06 * g2(x, y, 0, -0.48, 0.06, 0.008) + 0.05 * g2(x, y, 0, -0.86, 0.05, 0.01);
      z += 0.05 * (g2(x, y, 0.45, -0.05, 0.03, 0.03) + g2(x, y, -0.45, -0.05, 0.03, 0.03));
      return z;
    };
    const add = (x, y, kind, lip = 0, dz = 0) => { P.push({ x, y, z: depth(x, y) + dz, kind, lip }); return P.length - 1; };
    for (let y = -1.0; y <= 1.15; y += 0.064) {
      const w = Wd(y), n = Math.max(2, Math.round(2 * w / 0.064)); const row = [];
      for (let j = 0; j <= n; j++) { const x = -w + 2 * w * j / n; const edge = j === 0 || j === n; row.push(add(x + (edge ? 0 : (rnd() - .5) * .012), y + (rnd() - .5) * .01, edge ? 1 : 0)); }
      rows.push(row);
    }
    const ring = (cx, cy, rx, ry, n, kind = 1) => { const r = []; for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2; r.push(add(cx + Math.cos(a) * rx, cy + Math.sin(a) * ry, kind, 0, .015)); } r.push(r[0]); contours.push(r); };
    for (const s of [-1, 1]) {
      ring(s * 0.33, 0.27, 0.13, 0.05, 20); ring(s * 0.33, 0.27, 0.035, 0.035, 8);
      const b = []; for (let i = 0; i <= 10; i++) { const a = i / 10; b.push(add(s * (0.15 + 0.36 * a), 0.43 + 0.06 * Math.sin(Math.PI * a) - 0.02 * a, 1, 0, .02)); } contours.push(b);
    }
    { const nb = []; for (let i = 0; i <= 7; i++) nb.push(add(0, 0.24 - i * 0.06, 1, 0, .02)); contours.push(nb);
      const nn = []; for (let i = 0; i <= 8; i++) { const a = i / 8; nn.push(add(-0.11 + 0.22 * a, -0.235 - 0.03 * Math.sin(Math.PI * a), 1, 0, .02)); } contours.push(nn); }
    const lipLine = f => { const r = []; for (let i = 0; i <= 22; i++) { const a = i / 22; const x = -0.26 + 0.52 * a; r.push(add(x, f(a), 2, 1, .03)); } contours.push(r); return r; };
    lipLine(a => -0.465 + 0.06 * Math.sin(Math.PI * a) - 0.022 * Math.exp(-((a - 0.5) ** 2) / 0.003));
    lipLine(a => -0.465 - 0.09 * Math.sin(Math.PI * a));
    lipLine(a => -0.47 + 0.008 * Math.sin(Math.PI * a));
    const cornerL = add(0.265, -0.465, 3, 1, .04), cornerR = add(-0.265, -0.465, 3, 1, .04);
    return { P, rows, contours, cornerL, cornerR };
  }

  function FaceCloud({ scale = 3, vectors = false, ghost = false }) {
    const D = faceData(); const n = D.P.length;
    const group = new THREE.Group();
    const pts = cloud(n); pts.material.uniforms.uScale.value = scale;
    const base = new Float32Array(n * 3), scatter = new Float32Array(n * 3), delay = new Float32Array(n);
    D.P.forEach((p, i) => {
      base[i * 3] = p.x; base[i * 3 + 1] = p.y; base[i * 3 + 2] = p.z;
      const r = 2 + rnd() * 3, a = rnd() * 6.28, b = Math.acos(2 * rnd() - 1);
      scatter[i * 3] = r * Math.sin(b) * Math.cos(a); scatter[i * 3 + 1] = r * Math.cos(b); scatter[i * 3 + 2] = r * Math.sin(b) * Math.sin(a) - 1;
      delay[i] = rnd() * 0.45 + (1 - (p.y + 1) / 2.2) * 0.2;
      const k = p.kind;
      const hex = ghost ? 0x4F8F95 : k === 0 ? 0x5E93B5 : k === 1 ? 0xB9EFE6 : k === 2 ? 0xE8FFFA : (i === D.cornerL ? COL.left : COL.right);
      const s = k === 0 ? .03 : k === 1 ? .045 : k === 2 ? .05 : (vectors ? .12 : .06);
      const a2 = ghost ? .3 : k === 0 ? .55 : k === 3 ? 1 : .95;
      pts.set(i, p.x, p.y, p.z, k === 3 && !vectors ? 0xE8FFFA : hex, s, a2);
    });
    group.add(pts);
    const pairs = [];
    D.rows.forEach(r => { for (let j = 0; j < r.length - 1; j++) pairs.push(r[j], r[j + 1]); });
    for (let ri = 0; ri < D.rows.length - 1; ri++) { const a = D.rows[ri], b = D.rows[ri + 1]; for (let j = 0; j < a.length; j += 2) { const k = Math.round(j / (a.length - 1 || 1) * (b.length - 1)); pairs.push(a[j], b[k]); } }
    D.contours.forEach(r => { for (let j = 0; j < r.length - 1; j++) pairs.push(r[j], r[j + 1]); });
    const lg = new THREE.BufferGeometry(); const lpos = new Float32Array(pairs.length * 3);
    lg.setAttribute('position', new THREE.BufferAttribute(lpos, 3));
    const lmat = lineMat(ghost ? 0x2f6f74 : 0x3E8FA8, ghost ? .12 : .24);
    const lines = new THREE.LineSegments(lg, lmat); lines.frustumCulled = false;
    group.add(lines);
    group.scale.setScalar(scale);
    let arrows = null;
    if (vectors) {
      arrows = {};
      for (const side of ['L', 'R']) {
        const mat = new THREE.MeshBasicMaterial({ color: side === 'L' ? COL.left : COL.right, depthTest: false, transparent: true });
        const ln = new THREE.Mesh(new THREE.CylinderGeometry(.011, .011, 1, 10), mat);
        const head = new THREE.Mesh(new THREE.ConeGeometry(.03, .075, 14), mat);
        ln.renderOrder = head.renderOrder = 5; group.add(ln, head); arrows[side] = { ln, head, mat };
      }
    }
    const cur = new Float32Array(n * 3);
    let lineK = 1;
    function pose(L, R, asm = 1) {
      const { pos } = pts.arr;
      for (let i = 0; i < n; i++) {
        const p = D.P[i]; let x = base[i * 3], y = base[i * 3 + 1], z = base[i * 3 + 2];
        let dx = 0, dy = 0, dz = 0;
        for (const s of [1, -1]) {
          const lift = s > 0 ? L : R; if (lift <= 0) continue;
          const cx = s * 0.265, cy = -0.465; const d2 = (x - cx) ** 2 + (y - cy) ** 2;
          let w = Math.exp(-d2 / 0.03) + 0.5 * Math.exp(-((x - s * 0.42) ** 2 + (y + 0.15) ** 2) / 0.04) * 0.6;
          if (p.lip) { const t = Math.max(0, s * x) / 0.265; w = Math.max(w, t * t); }
          w = Math.min(w, 1.1);
          dy += lift * 0.12 * w; dx += s * lift * 0.05 * w; dz -= lift * 0.03 * w;
        }
        x += dx; y += dy; z += dz;
        if (asm < 1) { const k = easeIO(clamp((asm - delay[i]) / 0.55)); x = lerp(scatter[i * 3], x, k); y = lerp(scatter[i * 3 + 1], y, k); z = lerp(scatter[i * 3 + 2], z, k); }
        pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z; cur[i * 3] = x; cur[i * 3 + 1] = y; cur[i * 3 + 2] = z;
      }
      pts.dirty('position');
      for (let j = 0; j < pairs.length; j++) { const i = pairs[j]; lpos[j * 3] = cur[i * 3]; lpos[j * 3 + 1] = cur[i * 3 + 1]; lpos[j * 3 + 2] = cur[i * 3 + 2]; }
      lg.attributes.position.needsUpdate = true;
      lineK = clamp((asm - .7) / .3);
      if (arrows) for (const side of ['L', 'R']) {
        const i = side === 'L' ? D.cornerL : D.cornerR;
        const s = side === 'L' ? 1 : -1; const lift = side === 'L' ? L : R;
        const a = V(s * .265, -.465, base[i * 3 + 2] + .06), b = V(s * (.265 + .05 * lift), -.465 + .12 * lift, base[i * 3 + 2] + .06);
        const { ln, head } = arrows[side]; const dir = b.clone().sub(a); const len = dir.length();
        head.visible = ln.visible = len > .012; if (!ln.visible) continue;
        dir.normalize(); ln.quaternion.setFromUnitVectors(V(0, 1, 0), dir); head.quaternion.copy(ln.quaternion);
        ln.scale.set(1, Math.max(.001, len - .05), 1); ln.position.copy(a).addScaledVector(dir, (len - .05) / 2);
        head.position.copy(b).addScaledVector(dir, -.035);
      }
    }
    pose(0, 0, 1);
    const a = actor(group, [pts.material], [lmat]);
    const applyBase = a.apply;
    a.apply = () => { applyBase(); lmat.opacity *= lineK; if (arrows) for (const k of ['L', 'R']) arrows[k].mat.opacity = a.vis; };
    return Object.assign(a, { pts, pose });
  }

  /* ---------- a standing figure from points: bones sampled as tubes (pitch deck design) */
  function Figure({ scale = 1, color = 0x8fb3cc, alpha = .95 }) {
    const group = new THREE.Group();
    const J = { head: [0, 1.55, 0], neck: [0, 1.15, 0], shL: [.5, 1.0, 0], shR: [-.5, 1.0, 0], hipL: [.28, 0, 0], hipR: [-.28, 0, 0], knL: [.3, -.9, .03], knR: [-.3, -.9, .03], anL: [.31, -1.8, 0], anR: [-.31, -1.8, 0], pelvis: [0, .05, 0], elL: [0, 0, 0], wrL: [0, 0, 0], elR: [0, 0, 0], wrR: [0, 0, 0] };
    const BONES = [['neck', 'pelvis', .2], ['shL', 'shR', .09], ['hipL', 'hipR', .1], ['hipL', 'knL', .11], ['knL', 'anL', .09], ['hipR', 'knR', .11], ['knR', 'anR', .09], ['shL', 'elL', .08], ['elL', 'wrL', .07], ['shR', 'elR', .08], ['elR', 'wrR', .07]];
    const per = 150, nb = BONES.length * per, nh = 420;
    const jointNames = ['head', 'neck', 'shL', 'shR', 'elL', 'elR', 'wrL', 'wrR', 'hipL', 'hipR', 'knL', 'knR'];
    const pts = cloud(nb + nh + jointNames.length); pts.material.uniforms.uScale.value = scale * 2.3;
    const r3 = new Float32Array((nb + nh) * 3); for (let i = 0; i < r3.length; i++) r3[i] = rnd();
    const SEGS = [['head', 'neck'], ['neck', 'pelvis'], ['shL', 'shR'], ['shL', 'elL'], ['elL', 'wrL'], ['shR', 'elR'], ['elR', 'wrR'], ['hipL', 'hipR'], ['hipL', 'knL'], ['knL', 'anL'], ['hipR', 'knR'], ['knR', 'anR']];
    const sg = new THREE.BufferGeometry(); const spos = new Float32Array(SEGS.length * 6);
    sg.setAttribute('position', new THREE.BufferAttribute(spos, 3));
    const smat = lineMat(color, .6);
    const skel = new THREE.LineSegments(sg, smat); skel.frustumCulled = false;
    group.add(pts, skel); group.scale.setScalar(scale);
    { let i = 0;
      for (let b = 0; b < BONES.length; b++) for (let k = 0; k < per; k++, i++) pts.paint(i, color, .045, alpha * .85);
      for (let k = 0; k < nh; k++, i++) pts.paint(i, color, .045, alpha * .85);
      jointNames.forEach(nm => { const hi = nm === 'wrL' || nm === 'wrR'; pts.paint(i++, hi ? (nm === 'wrL' ? COL.left : COL.right) : 0xE9EEF2, hi ? .14 : .08, 1); });
      pts.dirty('aColor', 'aSize', 'aAlpha'); }
    /* thL, thR: arm angle below straight-out, in radians (0 = straight out in front, pi/2 = down) */
    function pose(thL = Math.PI / 2, thR = Math.PI / 2) {
      const arm = (sh, el, wr, th) => { const dy = -Math.sin(th), dz = Math.cos(th); for (const [j, d] of [[el, .8], [wr, 1.6]]) { J[j][0] = J[sh][0]; J[j][1] = J[sh][1] + dy * d; J[j][2] = J[sh][2] + dz * d; } };
      arm('shL', 'elL', 'wrL', thL); arm('shR', 'elR', 'wrR', thR);
      const { pos } = pts.arr; let i = 0;
      for (const [a, b, r] of BONES) {
        const A = J[a], B = J[b];
        for (let k = 0; k < per; k++, i++) {
          const t = r3[i * 3], ang = r3[i * 3 + 1] * 6.283, rr = r * Math.sqrt(r3[i * 3 + 2]);
          pos[i * 3] = A[0] + (B[0] - A[0]) * t + Math.cos(ang) * rr; pos[i * 3 + 1] = A[1] + (B[1] - A[1]) * t + Math.sin(ang) * rr * .6; pos[i * 3 + 2] = A[2] + (B[2] - A[2]) * t + Math.sin(ang) * rr;
        }
      }
      for (let k = 0; k < nh; k++, i++) {
        const u = r3[i * 3] * 6.283, v = Math.acos(2 * r3[i * 3 + 1] - 1), r = .3 * (0.9 + .1 * r3[i * 3 + 2]);
        pos[i * 3] = J.head[0] + r * Math.sin(v) * Math.cos(u); pos[i * 3 + 1] = J.head[1] + r * 1.15 * Math.cos(v); pos[i * 3 + 2] = J.head[2] + r * Math.sin(v) * Math.sin(u);
      }
      for (const nm of jointNames) { const p = J[nm]; pos[i * 3] = p[0]; pos[i * 3 + 1] = p[1]; pos[i * 3 + 2] = p[2]; i++; }
      pts.dirty('position');
      SEGS.forEach(([a, b], s) => { spos.set(J[a], s * 6); spos.set(J[b], s * 6 + 3); });
      sg.attributes.position.needsUpdate = true;
    }
    pose();
    const a = actor(group, [pts.material], [smat]);
    return Object.assign(a, { pts, pose, tint(k) { pts.material.uniforms.uTintK.value = k; } });
  }

  /* ---------- scene pieces */
  const X = SCENE_X;

  // cover: the face assembles out of dust and turns to follow the pointer
  const coverFace = FaceCloud({ scale: 3 });
  coverFace.group.position.set(X.cover + 3.7, .1, 0);
  coverFace.pts.material.uniforms.uScanOn.value = 1;

  // problem: 90 days as a calendar of lights (13 weeks x 7 days)
  const days = cloud(90);
  const daysGroup = new THREE.Group(); daysGroup.add(days);
  const CELL = .5;
  daysGroup.position.set(X.problem + 3.75, 0, 0); daysGroup.rotation.set(-.3, -.36, .03);
  { const g = new THREE.BufferGeometry(); const p = [];
    for (let c = 0; c <= 13; c++) p.push((c - 6.5) * CELL, 3.5 * CELL, 0, (c - 6.5) * CELL, -3.5 * CELL, 0);
    for (let r = 0; r <= 7; r++) p.push(-6.5 * CELL, (3.5 - r) * CELL, 0, 6.5 * CELL, (3.5 - r) * CELL, 0);
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(p), 3));
    var daysLines = lineMat(0x3E8FA8, .22); daysGroup.add(new THREE.LineSegments(g, daysLines)); }
  const daysActor = actor(daysGroup, [days.material], [daysLines]);
  days.material.uniforms.uScale.value = 3;

  // measure: the live face over its baseline ghost, the figure, the sentence as a waveform
  const ghostFace = FaceCloud({ scale: 2.9, ghost: true });
  ghostFace.group.position.set(X.measure + 3.7, .1, -.25); ghostFace.pose(1, 1);
  const liveFace = FaceCloud({ scale: 2.9, vectors: true });
  liveFace.group.position.set(X.measure + 3.7, .1, 0);
  const armFig = Figure({ scale: 1.7 });
  armFig.group.position.set(X.measure + 3.9, -.05, 0); armFig.group.rotation.y = -.82;
  const NW = 1500;
  const wave = cloud(NW); const waveGroup = new THREE.Group(); waveGroup.add(wave);
  waveGroup.position.set(X.measure + 3.9, .15, 0); waveGroup.rotation.set(.05, -.2, 0); waveGroup.scale.set(.74, 1.6, 1);
  wave.material.uniforms.uScale.value = 2.6;
  const waveActor = actor(waveGroup, [wave.material]);
  const waveSeed = new Float32Array(NW * 3); for (let i = 0; i < waveSeed.length; i++) waveSeed[i] = rnd();
  const WORDS = [.5, .34, .62, .3, .42, .5, .46, .7]; // relative length of each of the eight words

  // demo: a slow ring, a doorway for the live call
  const ring = cloud(900); const ringGroup = new THREE.Group(); ringGroup.add(ring);
  for (let i = 0; i < 900; i++) { const a = rnd() * 6.283, r = 3.3 + (rnd() - .5) * .34 + (rnd() < .12 ? (rnd() - .5) * 1.4 : 0); ring.set(i, Math.cos(a) * r, Math.sin(a) * r, (rnd() - .5) * .6, rnd() < .25 ? COL.glow : 0x6f9fba, .05 + rnd() * .08, .3 + rnd() * .6); }
  ring.material.uniforms.uScale.value = 2.4;
  const NR = 600; const ripple = cloud(NR); ripple.material.uniforms.uScale.value = 2.4; ringGroup.add(ripple);
  const rippleSeed = new Float32Array(NR); for (let i = 0; i < NR; i++) rippleSeed[i] = rnd();
  ringGroup.position.set(X.demo + 4.4, .15, -1);
  const ringActor = actor(ringGroup, [ring.material, ripple.material]);

  // escalate: the patient, the caregiver, and what travels between them
  const patient = Figure({ scale: 1.22 });
  patient.group.position.set(X.escalate + 1.9, -.25, 0); patient.group.rotation.y = .5; patient.pose(1.25, 0);
  const carer = Figure({ scale: 1.22, color: 0x9a9cf5 });
  carer.group.position.set(X.escalate + 6.1, -.25, 0); carer.group.rotation.y = -.6; carer.pose(Math.PI / 2, Math.PI / 2 - .5);
  const NB = 260; const beam = cloud(NB); beam.material.uniforms.uScale.value = 2.4; const beamSeed = new Float32Array(NB * 2); for (let i = 0; i < NB * 2; i++) beamSeed[i] = rnd();
  const beamActor = actor(beam, [beam.material]);

  // how: three trend lines over 90 days, one of them sliding toward its threshold
  const trend = cloud(270); const trendGroup = new THREE.Group(); trendGroup.add(trend);
  trendGroup.position.set(X.how + 4.4, -.75, 0); trendGroup.rotation.set(.05, -.28, 0); trend.material.uniforms.uScale.value = 2.6;
  const trendVal = new Float32Array(270);
  { const g = new THREE.BufferGeometry(); const p = [];
    for (let r = 0; r < 3; r++) { const y0 = (1 - r) * 1.2;
      for (let d = 0; d < 90; d++) { const drift = r === 2 ? Math.max(0, d - 52) / 38 : 0; trendVal[r * 90 + d] = y0 + (rnd() - .5) * .22 + Math.sin(d * .31 + r) * .07 - drift * .62; }
      for (let s = 0; s < 24; s++) p.push(-3.3 + s * .28, y0 - .46, 0, -3.3 + s * .28 + .14, y0 - .46, 0); }
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(p), 3));
    var trendLines = lineMat(0xFF5B45, .32); trendGroup.add(new THREE.LineSegments(g, trendLines)); }
  const trendActor = actor(trendGroup, [trend.material], [trendLines]);

  // close: the face again, smiling
  const closeFace = FaceCloud({ scale: 2.5 });
  closeFace.group.position.set(X.close + 4.3, .15, 0); closeFace.pose(1, 1);

  const CAST = {
    cover: [coverFace], problem: [daysActor], 'measure.0': [ghostFace, liveFace], 'measure.1': [armFig], 'measure.2': [waveActor],
    demo: [ringActor], escalate: [patient, carer, beamActor], how: [trendActor], close: [closeFace],
  };
  const CAM = {
    cover: { p: [0, .1, 13.5], l: [.3, .05, 0] },
    problem: { p: [X.problem, .7, 13.5], l: [X.problem + .3, -.1, 0] },
    'measure.0': { p: [X.measure, .15, 12.8], l: [X.measure + .5, 0, 0] },
    'measure.1': { p: [X.measure - .2, .5, 13.6], l: [X.measure + .4, .15, 0] },
    'measure.2': { p: [X.measure, .3, 13.2], l: [X.measure + .4, .1, 0] },
    demo: { p: [X.demo, 0, 15], l: [X.demo, 0, 0] },
    escalate: { p: [X.escalate, .5, 13.8], l: [X.escalate + .4, .1, 0] },
    how: { p: [X.how, .3, 13.5], l: [X.how + .4, 0, 0] },
    close: { p: [X.close, .1, 13.5], l: [X.close + .3, .05, 0] },
  };

  /* ---------- state the deck drives */
  const S = { scene: 'cover', step: 0, v: 0, esc: 0, demoOpen: false, pointer: [0, 0], motion: !reduced, dayLit: 0, t0: performance.now() };
  const cam = { p: V(...CAM.cover.p), l: V(...CAM.cover.l), fromP: V(...CAM.cover.p), fromL: V(...CAM.cover.l), toP: V(...CAM.cover.p), toL: V(...CAM.cover.l), t: 1, dur: 1, far: false };
  const par = V(0, 0, 0);
  const keyOf = (sc, st) => (CAM[`${sc}.${st}`] ? `${sc}.${st}` : sc);

  function go(sc, st = 0, instant = false) {
    const key = keyOf(sc, st); const c = CAM[key];
    const far = sc !== S.scene;
    if (far || st !== S.step) S.t0 = performance.now();
    S.scene = sc; S.step = st;
    cam.fromP.copy(cam.p); cam.fromL.copy(cam.l); cam.toP.set(...c.p); cam.toL.set(...c.l);
    cam.far = far; cam.dur = instant || !S.motion ? 0 : far ? 1.9 : 1.1; cam.t = cam.dur ? 0 : 1;
    const on = new Set(CAST[key] || CAST[sc] || []);
    actors.forEach(a => { a.want = on.has(a) ? 1 : 0; if (!cam.dur) { a.vis = a.want; a.apply(); } });
  }

  function resize(cssScale = 1) {
    const pr = clamp((window.devicePixelRatio || 1) * cssScale, .6, 2);
    renderer.setPixelRatio(pr); renderer.setSize(1920, 1080, false);
    shared.uPx.value = 600 * pr;
  }

  let last = performance.now(), raf = 0, running = true;
  const bez = (a, b, c, t) => (1 - t) * (1 - t) * a + 2 * (1 - t) * t * b + t * t * c;

  function frame(now) {
    raf = running ? requestAnimationFrame(frame) : 0;
    const dt = Math.min(.05, (now - last) / 1000); last = now;
    const t = now / 1000, ts = (now - S.t0) / 1000; // ts: seconds since this scene or step began
    const m = S.motion ? 1 : 0;

    // camera: fly, then drift a little with the pointer
    if (cam.t < 1) cam.t = Math.min(1, cam.t + dt / cam.dur);
    const k = easeIO(cam.t), arc = cam.far ? Math.sin(Math.PI * cam.t) : 0;
    cam.p.lerpVectors(cam.fromP, cam.toP, k); cam.l.lerpVectors(cam.fromL, cam.toL, k);
    par.x += (S.pointer[0] * .55 * m - par.x) * Math.min(1, dt * 3); par.y += (-S.pointer[1] * .32 * m - par.y) * Math.min(1, dt * 3);
    camera.position.set(cam.p.x + par.x, cam.p.y + par.y + arc * 1.2, cam.p.z + arc * 5.5);
    camera.fov = 35 + arc * 10; camera.updateProjectionMatrix();
    camera.lookAt(cam.l.x + par.x * .25, cam.l.y + par.y * .25, cam.l.z);
    dust.rotation.x = Math.sin(t * .05) * .01 * m;

    actors.forEach(a => { if (a.vis !== a.want) { a.vis += Math.sign(a.want - a.vis) * Math.min(Math.abs(a.want - a.vis), dt * (a.want ? 1.6 : 2.6)); a.apply(); } });

    if (coverFace.vis > 0) {
      const asm = m ? clamp(ts / 2.4) : 1, smile = .5 + .5 * Math.sin(t * .7 - 1.2);
      coverFace.pose(smile * asm, smile * asm, asm);
      coverFace.group.rotation.y = lerp(coverFace.group.rotation.y, -.28 + S.pointer[0] * .42 * m, Math.min(1, dt * 4));
      coverFace.group.rotation.x = lerp(coverFace.group.rotation.x, S.pointer[1] * .22 * m, Math.min(1, dt * 4));
      coverFace.pts.material.uniforms.uScan.value = m ? lerp(coverFace.pts.material.uniforms.uScan.value, -S.pointer[1] * 1.05 + .08, Math.min(1, dt * 6)) : -9;
      coverFace.apply();
    }

    if (daysActor.vis > 0) {
      const sweep = S.step >= 1 ? (m ? easeOut(ts / 5.2) * 90 : 90) : 0; S.dayLit = Math.round(sweep);
      const { pos } = days.arr;
      for (let d = 0; d < 90; d++) {
        const c = d / 7 | 0, r = d % 7;
        pos[d * 3] = (c - 6) * CELL; pos[d * 3 + 1] = (3 - r) * CELL; pos[d * 3 + 2] = Math.sin(t * 1.1 + c * .55 + r * .35) * .1 * m;
        const early = Math.exp(-d / 16), pulse = .75 + .25 * Math.sin(t * 2.2 - d * .22);
        if (d < sweep - 1) days.paint(d, COL.glow, .15, 1);
        else if (d < sweep) days.paint(d, 0xffffff, .3, 1);
        else if (early > .12) days.paint(d, COL.alert, .09 + .12 * early, (.35 + .65 * early) * pulse);
        else days.paint(d, 0x7f9bb3, .07, .6);
      }
      days.dirty('position', 'aColor', 'aSize', 'aAlpha');
    }

    const v = S.v;
    if (liveFace.vis > 0 || ghostFace.vis > 0) {
      const breathe = Math.sin(t * 1.3) * .015 * m;
      liveFace.pose(1 - .88 * v + breathe, 1 + breathe); liveFace.apply();
      const ry = -.3 + S.pointer[0] * .25 * m, rx = S.pointer[1] * .12 * m;
      for (const f of [liveFace, ghostFace]) { f.group.rotation.y = lerp(f.group.rotation.y, ry, Math.min(1, dt * 4)); f.group.rotation.x = lerp(f.group.rotation.x, rx, Math.min(1, dt * 4)); }
    }
    if (armFig.vis > 0) {
      const tremor = Math.sin(t * 5.1) * .012 * m;
      armFig.pose(v * .62 + tremor * (.4 + v), tremor * .4);
      armFig.group.rotation.y = lerp(armFig.group.rotation.y, -.82 + S.pointer[0] * .25 * m, Math.min(1, dt * 4));
    }
    if (waveActor.vis > 0) {
      // eight words; acting a change opens a pause after the fourth word and slows the rest
      const gap = .12 + v * 1.55, rate = 1 + v * .34; const total = WORDS.reduce((a, b) => a + b, 0) * rate + .1 * 7 + gap;
      const { pos } = wave.arr; let i = 0; let x = -total / 2;
      const per = NW / 8 | 0;
      for (let w = 0; w < 8; w++) {
        const len = WORDS[w] * rate;
        for (let q = 0; q < per; q++, i++) {
          const u = waveSeed[i * 3], env = Math.pow(Math.sin(Math.PI * u), .7) * (.5 + .5 * Math.abs(Math.sin(u * 9 + w * 2.1 + t * 2.4 * m)));
          pos[i * 3] = x + u * len; pos[i * 3 + 1] = (waveSeed[i * 3 + 1] * 2 - 1) * env * (.75 + .25 * Math.sin(w * 1.7)); pos[i * 3 + 2] = (waveSeed[i * 3 + 2] - .5) * .3;
          wave.paint(i, w % 2 ? 0xB9EFE6 : COL.glow, .05, .8);
        }
        x += len + (w === 3 ? gap : .1);
      }
      const gx0 = -total / 2 + WORDS.slice(0, 4).reduce((a, b) => a + b, 0) * rate + .3, hot = gap > 1.0;
      for (; i < NW; i++) { const u = waveSeed[i * 3]; pos[i * 3] = gx0 + u * gap; pos[i * 3 + 1] = (waveSeed[i * 3 + 1] - .5) * .03; pos[i * 3 + 2] = 0; wave.paint(i, hot ? COL.alert : COL.dim, hot ? .07 : .04, v > .05 ? .9 : 0); }
      wave.dirty('position', 'aColor', 'aSize', 'aAlpha');
    }

    if (ringActor.vis > 0) {
      ring.rotation.z = t * .05 * m; ringGroup.rotation.y = -.25 + Math.sin(t * .2) * .08 * m + S.pointer[0] * .2 * m; ringGroup.rotation.x = S.pointer[1] * .12 * m;
      const dim = ringActor.vis * (S.demoOpen ? .12 : 1);
      ring.material.uniforms.uOpacity.value = dim; ripple.material.uniforms.uOpacity.value = dim;
      for (let i = 0; i < NR; i++) {
        const ph = ((m ? t * .28 : .5) + (i % 3) / 3) % 1, a = rippleSeed[i] * 6.283 + (i % 3), r = .25 + ph * 2.9;
        ripple.set(i, Math.cos(a) * r, Math.sin(a) * r, 0, i % 3 ? COL.glow : 0xE9EEF2, .05 + .05 * (1 - ph), (1 - ph) * (1 - ph) * .95);
      }
      ripple.dirty('position', 'aColor', 'aSize', 'aAlpha');
    }

    if (patient.vis > 0) {
      const e = S.esc; // 0 change noticed, 1 text goes out, 2 caregiver is on the call, 3 call 911
      patient.tint(clamp(e + .35) * (.45 + .25 * Math.sin(t * 4 * m)) * (e >= 2 ? .45 : 1));
      patient.pose(1.25 + Math.sin(t * 1.1) * .02 * m, Math.sin(t * 4.7) * .01 * m);
      carer.pts.material.uniforms.uOpacity.value = carer.vis * clamp(e - .9, 0, 1) * .95; carer.group.visible = e > .9;
      const a = [X.escalate + 2.3, 1.0, .5], c = [X.escalate + 5.7, 1.0, .5], b = [X.escalate + 4, 3.3, 0];
      const joined = e >= 2;
      for (let i = 0; i < NB; i++) {
        const flow = (beamSeed[i * 2] + t * .22 * (i % 2 ? 1 : -1)) % 1; // both ways along the link
        const u = joined ? (flow + 1) % 1 : clamp(e - 1) * (.72 + .28 * beamSeed[i * 2]);
        const spread = joined ? .09 : .16 * (1 - clamp(e - 1) * .4);
        const ox = (beamSeed[i * 2 + 1] - .5) * spread, oy = (beamSeed[(i * 2 + 7) % (NB * 2)] - .5) * spread;
        beam.set(i, bez(a[0], b[0], c[0], u) + ox, bez(a[1], joined ? 1.9 : b[1], c[1], u) + oy, bez(a[2], b[2], c[2], u) + ox,
          joined ? COL.glow : 0xE9EEF2, joined ? .06 : .09, e < 1 ? 0 : joined ? .8 : .95);
      }
      beam.dirty('position', 'aColor', 'aSize', 'aAlpha');
    }

    if (trendActor.vis > 0) {
      const reveal = m ? easeOut((ts - .3) / 3.2) * 90 : 90;
      for (let r = 0; r < 3; r++) for (let d = 0; d < 90; d++) {
        const i = r * 90 + d, flagged = r === 2 && d > 74, on = d < reveal;
        trend.set(i, -3.3 + d * (6.6 / 89), trendVal[i] + Math.sin(t * 1.4 + d * .2 + r) * .012 * m, 0,
          flagged ? COL.alert : r === 0 ? COL.glow : r === 1 ? COL.right : 0xB9EFE6, flagged ? .15 : .1, on ? (d > reveal - 2 ? 1 : .9) : 0);
      }
      trend.dirty('position', 'aColor', 'aSize', 'aAlpha');
    }

    if (closeFace.vis > 0) {
      closeFace.group.rotation.y = lerp(closeFace.group.rotation.y, -.3 + S.pointer[0] * .4 * m, Math.min(1, dt * 4));
      closeFace.group.rotation.x = lerp(closeFace.group.rotation.x, S.pointer[1] * .2 * m, Math.min(1, dt * 4));
    }
    renderer.render(scene, camera);
  }

  go('cover', 0, true);
  raf = requestAnimationFrame(frame);
  document.addEventListener('visibilitychange', () => { running = !document.hidden; if (running && !raf) { last = performance.now(); raf = requestAnimationFrame(frame); } });

  return { state: S, go, resize, setMotion(on) { S.motion = on; }, renderer };
}
