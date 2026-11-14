// Cut the demo video from the recordings, the voice-over and the timeline (timeline.cjs).
//
//   node tools/video/build.cjs [--work DIR] [--vo DIR] [--captions DIR] [--out FILE] [--copy FILE] [--only-shots]
//
// Inputs (defaults in brackets)
//   --work      recordings from record_dash.ps1 / record_deck.ps1    [%LOCALAPPDATA%\Temp\onset-video\work]
//   --vo        tts.py output: <id>.mp3 + <id>.json                   [\\wsl.localhost\Ubuntu\home\arnav\onset\runs\video\vo]
//   --captions  render_captions.py output                             [\\wsl.localhost\Ubuntu\home\arnav\onset\runs\video\captions]
// Writes WORK/build/ (cards, shots, subs.ass, vo_mix.wav), the MP4 at --out, a copy at --copy,
// and next to the MP4: <name>.srt and <name>-narration.txt.
// Needs playwright + ffmpeg-static (NODE_PATH) and the installed Chrome.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const ffmpeg = require('ffmpeg-static');

const FPS = 30, W = 1920, H = 1080;
const FIT = { w: 1632, h: 918, x: 144, y: 8 };   // content area; the band below is for subtitles
const BG = '0x04070d';
const HERE = __dirname;
const VIDEO_CARDS = process.env.ONSET_VIDEO_CARDS || 'http://127.0.0.1:8772/video/index.html'; // serve_deck.sh <repo>/web 8772

function opts() {
  const o = {
    work: path.join(process.env.LOCALAPPDATA, 'Temp', 'onset-video', 'work-v2'),
    vo: '\\\\wsl.localhost\\Ubuntu\\home\\arnav\\onset\\runs\\video\\vo-v2',
    captions: '\\\\wsl.localhost\\Ubuntu\\home\\arnav\\onset\\runs\\video\\captions-v2',
    out: '\\\\wsl.localhost\\Ubuntu\\home\\arnav\\onset\\runs\\video\\onset-demo-v2.mp4',
    copy: path.join(process.env.USERPROFILE, 'Videos', 'onset-demo-v2.mp4'),
  };
  const v = process.argv.slice(2);
  for (let i = 0; i < v.length; i++) {
    const k = v[i].replace(/^--/, '');
    if (k === 'only-shots') o.onlyShots = true;
    else if (k === 'no-copy') o.copy = null;
    else o[k] = v[++i];
  }
  return o;
}

const run = (args, cwd) => execFileSync(ffmpeg, ['-y', '-hide_banner', '-loglevel', 'error', ...args], { cwd, stdio: 'inherit' });
const fileUrl = p => 'file:' + (p.startsWith('\\\\') ? p.replace(/\\/g, '/') : '///' + p.replace(/\\/g, '/'));
const enc = s => encodeURIComponent(s);

// ------------------------------------------------------------------ voice-over and subtitles

function loadVO(dir, id) {
  const meta = JSON.parse(fs.readFileSync(path.join(dir, `${id}.json`), 'utf8'));
  const al = meta.alignment;
  return { id, text: meta.text, mp3: path.join(dir, `${id}.mp3`), chars: al.characters, st: al.character_start_times_seconds,
    en: al.character_end_times_seconds, dur: al.character_end_times_seconds[al.character_end_times_seconds.length - 1] };
}

// Split one segment into cues: sentences, and long sentences at the comma or colon nearest the middle.
function cues(vo, offset) {
  const text = vo.chars.join('');
  const parts = [];
  let s = 0;
  for (let i = 0; i < text.length; i++) {
    if (/[.?!]/.test(text[i]) && (i + 1 === text.length || text[i + 1] === ' ') && !/\bUS$|\bLLM$/.test(text.slice(s, i))) {
      parts.push([s, i]); s = i + 1;
    }
  }
  if (s < text.length && text.slice(s).trim()) parts.push([s, text.length - 1]);
  const out = [];
  const split = ([a, b]) => {
    const seg = text.slice(a, b + 1);
    if (seg.trim().length <= 88) return [[a, b]];
    const mid = (a + b) / 2;
    let best = -1;
    for (let i = a + 20; i < b - 20; i++) if (/[,:]/.test(text[i]) && text[i + 1] === ' ' && (best < 0 || Math.abs(i - mid) < Math.abs(best - mid))) best = i;
    if (best < 0) return [[a, b]];
    return [...split([a, best]), ...split([best + 1, b])];
  };
  for (const p of parts) out.push(...split(p));
  return out.map(([a, b]) => {
    while (a < b && text[a] === ' ') a++;
    return { text: text.slice(a, b + 1).trim(), start: offset + vo.st[a], end: offset + vo.en[b] };
  });
}

function fmtSrt(t) {
  const ms = Math.round(t * 1000), h = Math.floor(ms / 3600000), m = Math.floor(ms / 60000) % 60, s = Math.floor(ms / 1000) % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')},${String(ms % 1000).padStart(3, '0')}`;
}
function fmtAss(t) {
  const cs = Math.round(t * 100), h = Math.floor(cs / 360000), m = Math.floor(cs / 6000) % 60, s = Math.floor(cs / 100) % 60;
  return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(cs % 100).padStart(2, '0')}`;
}
function wrap2(t, max = 44) {
  if (t.length <= max) return [t];
  const words = t.split(' ');
  let best = null;
  for (let i = 1; i < words.length; i++) {
    const a = words.slice(0, i).join(' '), b = words.slice(i).join(' ');
    const score = Math.max(a.length, b.length);
    if (!best || score < best.score) best = { score, lines: [a, b] };
  }
  return best.lines;
}

function writeSubs(all, srtPath, assPath) {
  // Hold each cue a little longer, up to the next one; at least 1.2 s on screen.
  all.sort((a, b) => a.start - b.start);
  all.forEach((c, i) => {
    const next = all[i + 1] ? all[i + 1].start - 0.05 : Infinity;
    c.end = Math.min(next, Math.max(c.end + 0.35, c.start + 1.2));
  });
  fs.writeFileSync(srtPath, all.map((c, i) => `${i + 1}\n${fmtSrt(c.start)} --> ${fmtSrt(c.end)}\n${wrap2(c.text).join('\n')}\n`).join('\n'));
  const head = `[Script Info]
ScriptType: v4.00+
PlayResX: ${W}
PlayResY: ${H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: VO,Segoe UI Semibold,48,&H00FFFFFF,&H00FFFFFF,&H38000000,&H00000000,0,0,0,0,100,100,0,0,3,13,0,2,190,190,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
`;
  fs.writeFileSync(assPath, head + all.map(c => `Dialogue: 0,${fmtAss(c.start)},${fmtAss(c.end)},VO,,0,0,0,,${wrap2(c.text, 52).join('\\N')}`).join('\n') + '\n');
}

// ------------------------------------------------------------------ cards

async function renderCards(jobs) {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--allow-file-access-from-files'] });
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  const cardsUrl = fileUrl(path.join(HERE, 'cards.html'));
  for (const j of jobs) {
    const page = await ctx.newPage();
    if (j.lower) {
      // The lower-third design from web/video/index.html, with this shot's words, on a clear ground.
      await page.goto(`${VIDEO_CARDS}?rec&still#lower-1`, { waitUntil: 'load' });
      await page.evaluate(L => {
        document.documentElement.style.background = 'transparent';
        document.body.style.background = 'transparent';
        document.getElementById('stage').style.background = 'transparent';
        const lt = document.querySelector('.lt');
        lt.classList.toggle('alert', !!L.alert);
        lt.style.transform = 'scale(.8)';
        if (L.pos === 'br') { lt.style.left = 'auto'; lt.style.right = '190px'; lt.style.bottom = '190px'; lt.style.transformOrigin = 'bottom right'; }
        else if (L.pos === 'tl') { lt.style.bottom = 'auto'; lt.style.top = '110px'; lt.style.left = '190px'; lt.style.transformOrigin = 'top left'; }
        else { lt.style.left = '190px'; lt.style.bottom = '190px'; lt.style.transformOrigin = 'bottom left'; }
        lt.querySelector('.k').textContent = L.k;
        lt.querySelector('h2').textContent = L.h;
        const p = lt.querySelector('p');
        if (L.p) p.textContent = L.p; else p.remove();
      }, j.lower);
      await page.evaluate(() => document.fonts.ready);
    } else {
      await page.goto(`${cardsUrl}?${j.query}`, { waitUntil: 'load' });
    }
    await page.waitForTimeout(400);
    await page.screenshot({ path: j.out, omitBackground: !!j.transparent });
    await page.close();
  }
  await browser.close();
}

// ------------------------------------------------------------------ shots

function shotFilter(s, dur, frames, inputs) {
  // Returns the filter graph for one shot; input 0 is the picture, then overlays.
  const f = [];
  let base;
  if (s.card === 'title' || s.card === 'end') {
    f.push(`[0:v]setpts=PTS-STARTPTS,fps=${FPS},tpad=stop_mode=clone:stop_duration=${dur + 1},scale=${W}:${H},setsar=1[b0]`);
  } else if (s.card || s.phone) {
    // Slow push-in, 3 % over the shot.
    const full = s.card === 'title' || s.card === 'end';
    const z = `(1+0.03*t/${dur.toFixed(3)})`;
    base = `[0:v]scale=w='trunc(${W * 2}*${z}/2)*2':h=-2:eval=frame:flags=bicubic,crop=${W * 2}:${H * 2},scale=${W}:${H}:flags=area,setsar=1`;
    if (!full) base = `[0:v]scale=${W}:${H},setsar=1`; // phone and disclose cards: no motion (text stays crisp)
    if (full) base = `[0:v]scale=w='trunc(${W * 2}*${z}/2)*2':h=-2:eval=frame:flags=bicubic,crop=${W * 2}:${H * 2},scale=${W}:${H}:flags=area,setsar=1`;
    f.push(`${base}[b0]`);
  } else {
    const speed = s.speed || 1;
    let chain = `[0:v]setpts=(PTS-STARTPTS)/${speed},fps=${FPS},tpad=stop_mode=clone:stop_duration=${dur + 1}`;
    const srcScale = s.dash ? 1.5 : 1;
    if (s.crop) {
      const [x, y, w, h] = s.crop.map(v => Math.round(v * srcScale));
      chain += `,crop=${w}:${h}:${x}:${y}`;
    }
    chain += `,scale=${FIT.w}:${FIT.h}:flags=lanczos,pad=${W}:${H}:${FIT.x}:${FIT.y}:color=${BG},setsar=1`;
    f.push(`${chain}[b0]`);
  }
  let last = 'b0', n = 0;
  for (const inp of inputs) {
    n++;
    const a = inp.fadeIn ?? 0.25, b = inp.until ?? dur;
    f.push(`[${n}:v]format=rgba,fade=in:st=${a}:d=0.3:alpha=1${inp.until ? `,fade=out:st=${b - 0.35}:d=0.35:alpha=1` : ''}[o${n}]`);
    f.push(`[${last}][o${n}]overlay=0:0:shortest=1[b${n}]`);
    last = `b${n}`;
  }
  f.push(`[${last}]format=yuv420p,trim=end_frame=${frames}[v]`);
  return f.join(';');
}

// ------------------------------------------------------------------ main

(async () => {
  const o = opts();
  const timeline = require('./timeline.cjs');
  const B = path.join(o.work, 'build');
  fs.mkdirSync(path.join(B, 'shots'), { recursive: true });
  fs.mkdirSync(path.join(B, 'cards'), { recursive: true });
  process.env.FONTCONFIG_FILE = process.env.FONTCONFIG_FILE || path.join(o.work, '..', 'fonts.conf');

  // Lay out beats and shots in time.
  const vos = {}, placed = [], shots = [];
  let t = 0;
  for (const beat of timeline) {
    const vo = beat.vo ? (vos[beat.vo] = loadVO(o.vo, beat.vo)) : null;
    const fixed = beat.shots.reduce((a, s) => a + (s.dur || 0), 0);
    const len = vo ? beat.pre + vo.dur + beat.post : fixed;
    if (vo) placed.push({ vo, at: t + beat.pre });
    const rest = Math.max(0.5, len - fixed);
    const beatEnd = Math.round((t + Math.max(len, fixed)) * FPS) / FPS;
    beat.shots.forEach((s, k) => {
      // Shot boundaries on whole frames so audio and video stay aligned.
      const end = k === beat.shots.length - 1 ? beatEnd : Math.round((t + (s.dur || rest)) * FPS) / FPS;
      shots.push({ ...s, start: t, dur: end - t });
      t = end;
    });
  }
  const total = t;
  console.log(`timeline ${total.toFixed(2)} s, ${shots.length} shots, ${placed.length} voice-over segments`);

  // Cards (title, end, disclose, phone views, lower-thirds, tag).
  const cardJobs = [];
  const cap = n => fileUrl(path.join(o.captions, `${n}.png`));
  const C = n => path.join(B, 'cards', n);
  cardJobs.push({ out: C('gh.png'), query: 'kind=gh', transparent: true });
  shots.forEach((s, i) => {
    if (s.phone) cardJobs.push({ out: C(`phone-${i}.png`), query: `kind=phone&img=${enc(cap(s.phone))}&k=${enc(s.k || '')}&t=${enc(s.t || '')}&p=${enc(s.p || '')}` });
    if (s.lower) cardJobs.push({ out: C(`lower-${i}.png`), transparent: true, lower: { k: s.lower.k, h: s.lower.t, p: s.lower.p, alert: s.lower.alert, pos: s.lower.pos } });
  });
  await renderCards(cardJobs);

  // Shots.
  const list = [];
  shots.forEach((s, i) => {
    const frames = Math.round(s.dur * FPS);
    const args = [];
    if (s.card === 'title' || s.card === 'end') args.push('-ss', String(s.ss || 0), '-i', path.join(o.work, `card-${s.card}`, 'clip.mp4'));
    else if (s.card) args.push('-loop', '1', '-framerate', String(FPS), '-t', String(s.dur + 0.5), '-i', C(`${s.card}.png`));
    else if (s.phone) args.push('-loop', '1', '-framerate', String(FPS), '-t', String(s.dur + 0.5), '-i', C(`phone-${i}.png`));
    else {
      const src = s.video || (s.deck ? path.join(o.work, `deck-${s.deck}`, 'clip.mp4') : path.join(o.work, `dash-${s.dash}`, 'clip.mp4'));
      args.push('-ss', String(s.ss || 0), '-i', src);
    }
    const overlays = [];
    if (s.lower) { args.push('-loop', '1', '-framerate', String(FPS), '-i', C(`lower-${i}.png`)); overlays.push({ fadeIn: 0.3, until: Math.min(s.dur, 4.4) }); }
    if (s.card === 'end') { args.push('-loop', '1', '-framerate', String(FPS), '-i', C('gh.png')); overlays.push({ fadeIn: 1.4 }); }
    const fc = shotFilter(s, s.dur, frames, overlays);
    const out = path.join(B, 'shots', `shot-${String(i).padStart(2, '0')}.mp4`);
    run([...args, '-filter_complex', fc, '-map', '[v]', '-frames:v', String(frames), '-r', String(FPS),
      '-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-pix_fmt', 'yuv420p', '-an', out]);
    list.push(`file '${out.replace(/\\/g, '/')}'`);
    const label = s.card ? `card ${s.card}` : s.phone ? `phone ${s.phone}` : s.deck ? `deck slide ${s.deck}` : s.dash ? `dashboard ${s.dash}${s.crop ? ' (zoom)' : ''}` : `video ${s.video}`;
    s.label = label;
    console.log(`  shot ${i} ${s.start.toFixed(2)}-${(s.start + s.dur).toFixed(2)} ${label}`);
  });
  if (o.onlyShots) return;
  fs.writeFileSync(path.join(B, 'shots.txt'), list.join('\n') + '\n');
  run(['-f', 'concat', '-safe', '0', '-i', 'shots.txt', '-c', 'copy', 'picture.mp4'], B);

  // Voice-over mix.
  const aargs = [], af = [];
  placed.forEach((p, i) => {
    aargs.push('-i', p.vo.mp3);
    const ms = Math.round(p.at * 1000);
    af.push(`[${i}:a]aresample=48000,aformat=channel_layouts=stereo,adelay=${ms}|${ms}[a${i}]`);
  });
  af.push(`${placed.map((_, i) => `[a${i}]`).join('')}amix=inputs=${placed.length}:normalize=0:dropout_transition=0,apad=whole_dur=${total.toFixed(3)},atrim=end=${total.toFixed(3)},loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000[a]`);
  run([...aargs, '-filter_complex', af.join(';'), '-map', '[a]', '-c:a', 'pcm_s16le', 'vo_mix.wav'], B);

  // Subtitles.
  const all = placed.flatMap(p => cues(p.vo, p.at));
  const base = o.out.replace(/\.mp4$/i, '');
  writeSubs(all, path.join(B, 'subs.srt'), path.join(B, 'subs.ass'));

  // Final: burn subtitles, fade in/out, mux.
  const tmp = path.join(B, 'final.mp4');
  run(['-i', 'picture.mp4', '-i', 'vo_mix.wav', '-filter_complex',
    `[0:v]ass=subs.ass:fontsdir='C\\:/Windows/Fonts',fade=in:st=0:d=0.6,fade=out:st=${(total - 1.2).toFixed(2)}:d=1.2[v]`,
    '-map', '[v]', '-map', '1:a', '-c:v', 'libx264', '-preset', 'slow', '-crf', '17', '-profile:v', 'high', '-pix_fmt', 'yuv420p',
    '-r', String(FPS), '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', '-t', total.toFixed(3), 'final.mp4'], B);

  fs.mkdirSync(path.dirname(o.out), { recursive: true });
  fs.copyFileSync(tmp, o.out);
  fs.copyFileSync(path.join(B, 'subs.srt'), base + '.srt');
  fs.writeFileSync(base + '-narration.txt', timeline.filter(b => b.vo).map(b => vos[b.vo].text).join('\n\n') + '\n');
  if (o.copy) { fs.mkdirSync(path.dirname(o.copy), { recursive: true }); fs.copyFileSync(tmp, o.copy); }

  // Shot list for the report.
  const mmss = x => `${Math.floor(x / 60)}:${(x % 60).toFixed(1).padStart(4, '0')}`;
  const report = shots.map((s, i) => `${String(i).padStart(2)}  ${mmss(s.start)}-${mmss(s.start + s.dur)}  ${s.label}${s.lower ? `  [${s.lower.k}: ${s.lower.t}]` : ''}`);
  const vrep = placed.map(p => `${mmss(p.at)}-${mmss(p.at + p.vo.dur)}  ${p.vo.id}`);
  fs.writeFileSync(base + '-shots.txt', ['SHOTS', ...report, '', 'VOICE-OVER', ...vrep, '', `total ${mmss(total)}`].join('\n') + '\n');
  console.log(`wrote ${o.out} (${total.toFixed(1)} s)`);
})().catch(e => { console.error(e); process.exit(1); });
