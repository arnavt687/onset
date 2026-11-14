"""Smart-speed render: variable speed (1.0-1.5x) per narration phrase, pauses and transitions compressed.

Video uses one continuous piecewise time map (setpts as a sum of clip() terms) so it never drifts;
audio is trimmed at the same boundaries, atempo'd per segment and concatenated.
"""
import re, subprocess, sys, json, pathlib

SRC = r"C:\Users\arnav\Videos\onset-demo-v2.mp4"
SRT = r"C:\Users\arnav\Videos\onset-demo-v2.srt"
OUT = sys.argv[1]
OUT_SRT = sys.argv[2]
SIL = sys.argv[3]
WORK = pathlib.Path(sys.argv[4])

# per-cue speed: 1.0 = slow emphasis, 1.5 = fast
CUE_SPEED = {
    1: 1.10, 2: 1.05, 3: 1.15, 4: 1.15, 5: 1.08, 6: 1.20, 7: 1.15, 8: 1.10,
    9: 1.25, 10: 1.15, 11: 1.35, 12: 1.20, 13: 1.35, 14: 1.35, 15: 1.20, 16: 1.35,
    17: 1.30, 18: 1.25, 19: 1.30, 20: 1.30, 21: 1.35, 22: 1.18, 23: 1.15, 24: 1.25,
    25: 1.15, 26: 1.05, 27: 1.00, 28: 1.25, 29: 1.20, 30: 1.25, 31: 1.30, 32: 1.35,
    33: 1.40, 34: 1.40, 35: 1.30, 36: 1.15, 37: 1.05, 38: 1.25, 39: 1.15, 40: 1.35,
    41: 1.35, 42: 1.15,
}
FAST = 1.5          # transitions / long pauses
EMPH_PAUSE = 1.15   # a beat held before an emphasised line
SHORT_PAUSE = 0.35  # pauses shorter than this inside a phrase keep the phrase speed

def ts(s):
    h, m, rest = s.split(':'); sec, ms = rest.split(',')
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms) / 1000

def fmt(t):
    ms = round(t * 1000)
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"

txt = open(SRT, encoding='utf-8').read()
cues = []
for block in txt.strip().split('\n\n'):
    lines = block.strip().split('\n')
    a, b = lines[1].split(' --> ')
    cues.append((int(lines[0]), ts(a), ts(b), '\n'.join(lines[2:])))

dur = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', SRC]).decode())

sil = []
st = None
for line in open(SIL, encoding='utf-8', errors='ignore'):
    m = re.search(r'silence_start: ([\d.]+)', line)
    if m: st = float(m.group(1))
    m = re.search(r'silence_end: ([\d.]+)', line)
    if m and st is not None: sil.append((st, float(m.group(1)))); st = None
if st is not None: sil.append((st, dur))

# intervals: alternating speech / silence
iv = []
t = 0.0
for a, b in sil:
    a = max(a, t)
    if a > t + 1e-3: iv.append([t, a, 'speech'])
    if b > a: iv.append([a, min(b, dur), 'silence'])
    t = min(b, dur)
if t < dur - 1e-3: iv.append([t, dur, 'speech'])

def cue_for(a, b):
    best, ov = None, 0
    for n, ca, cb, _ in cues:
        o = min(b, cb) - max(a, ca)
        if o > ov: best, ov = n, o
    return best

def next_cue_after(t):
    for n, ca, cb, _ in cues:
        if ca >= t - 0.05: return n
    return None

segs = []
for a, b, kind in iv:
    if kind == 'speech':
        n = cue_for(a, b)
        s = CUE_SPEED.get(n, 1.3)
    else:
        n_in = cue_for(a, b)
        # short pause inside a phrase: keep that phrase's speed
        inside = n_in is not None and any(ca <= a and b <= cb for _, ca, cb, _ in cues)
        if (b - a) < SHORT_PAUSE and inside:
            s = CUE_SPEED.get(n_in, 1.3)
        else:
            nx = next_cue_after(b)
            s = EMPH_PAUSE if (nx and CUE_SPEED.get(nx, 1.3) <= 1.06 and (b - a) < 1.2) else FAST
    segs.append([a, b, s])

# merge neighbours with the same speed
merged = []
for a, b, s in segs:
    if merged and abs(merged[-1][2] - s) < 1e-9 and abs(merged[-1][1] - a) < 1e-6:
        merged[-1][1] = b
    else:
        merged.append([a, b, s])
segs = [x for x in merged if x[1] - x[0] > 0.005]

def fmap(t):
    out = 0.0
    for a, b, s in segs:
        if t <= a: break
        out += (min(t, b) - a) / s
    return out

# video: piecewise time map
terms = '+'.join(f"clip(T-{a:.4f},0,{b - a:.4f})/{s}" for a, b, s in segs)
vf = f"[0:v]setpts='({terms})/TB',fps=30[v]"
# audio: trim + tempo per segment, concat
af = []
labels = []
for i, (a, b, s) in enumerate(segs):
    lab = f"a{i}"
    tempo = f",atempo={s}" if abs(s - 1) > 1e-6 else ""
    af.append(f"[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS{tempo}[{lab}]")
    labels.append(f"[{lab}]")
af.append(f"{''.join(labels)}concat=n={len(segs)}:v=0:a=1[a]")
script = WORK / 'smartspeed_filter.txt'
script.write_text(vf + ';\n' + ';\n'.join(af), encoding='utf-8')

subprocess.check_call(['ffmpeg', '-v', 'error', '-y', '-i', SRC, '-/filter_complex', str(script),
                       '-map', '[v]', '-map', '[a]', '-c:v', 'libx264', '-crf', '19', '-preset', 'medium',
                       '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '160k', '-movflags', '+faststart', OUT])

with open(OUT_SRT, 'w', encoding='utf-8') as f:
    for n, a, b, text in cues:
        f.write(f"{n}\n{fmt(fmap(a))} --> {fmt(fmap(b))}\n{text}\n\n")

speech = sum(b - a for a, b, k in iv if k == 'speech')
print(json.dumps({'segments': len(segs), 'in_dur': round(dur, 2), 'out_dur_map': round(fmap(dur), 2),
                  'speech_in': round(speech, 1), 'cue_times_out': [(n, round(fmap(a), 2)) for n, a, b, _ in cues[::6]]}))
