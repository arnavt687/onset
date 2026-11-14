"""Voice-over for the demo video: ElevenLabs TTS with character timestamps, one file per segment.

    ~/onset/.venv/bin/python tools/video/tts.py [--out runs/video/vo] [--only n01_hook,n05_arms] [--dry]

Reads tools/video/narration.json. For each segment writes <id>.mp3 and <id>.json (the text and
ElevenLabs' character alignment, used for subtitle timing). A segment whose text, voice and
settings are unchanged is not sent again (cached by a hash in its .json). Prints the characters
sent. The key comes from .env.local (ELEVENLABS_API_KEY) and is never printed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
API = "https://api.elevenlabs.io/v1"


def key() -> str:
    for env in (REPO / ".env.local", Path.home() / "onset" / ".env.local"):
        if env.is_file():
            load_dotenv(env)
    k = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
    if not k:
        sys.exit("ELEVENLABS_API_KEY not set in .env.local")
    return k


def subscription(k: str) -> dict | None:
    req = urllib.request.Request(f"{API}/user/subscription", headers={"xi-api-key": k})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.load(r)
    except (urllib.error.URLError, OSError):
        return None


def synth(k: str, cfg: dict, text: str, prev: str | None, nxt: str | None) -> dict:
    body = {"text": text, "model_id": cfg["model_id"], "voice_settings": cfg["voice_settings"]}
    if prev:
        body["previous_text"] = prev
    if nxt:
        body["next_text"] = nxt
    url = f"{API}/text-to-speech/{cfg['voice_id']}/with-timestamps?output_format=mp3_44100_128"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"xi-api-key": k, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"ElevenLabs HTTP {e.code}: {e.read()[:300]!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path.home() / "onset" / "runs" / "video" / "vo")
    ap.add_argument("--only", default="")
    ap.add_argument("--dry", action="store_true", help="only count characters")
    a = ap.parse_args()
    cfg = json.loads((HERE / "narration.json").read_text())
    segs = cfg["segments"]
    total = sum(len(s["text"]) for s in segs)
    print(f"{len(segs)} segments, {total} characters of narration")
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "narration.txt").write_text("\n\n".join(s["text"] for s in segs) + "\n")
    if a.dry:
        return
    k = key()
    before = subscription(k)
    only = set(filter(None, a.only.split(",")))
    sent = 0
    for i, s in enumerate(segs):
        if only and s["id"] not in only:
            continue
        settings = {k2: cfg[k2] for k2 in ("voice_id", "model_id", "voice_settings")}
        h = hashlib.sha256(json.dumps([s["text"], settings], sort_keys=True).encode()).hexdigest()[:16]
        meta_path = a.out / f"{s['id']}.json"
        if meta_path.is_file() and (a.out / f"{s['id']}.mp3").is_file():
            if json.loads(meta_path.read_text()).get("hash") == h:
                print(f"  {s['id']}: cached")
                continue
        prev = segs[i - 1]["text"] if i > 0 else None
        nxt = segs[i + 1]["text"] if i + 1 < len(segs) else None
        r = synth(k, cfg, s["text"], prev, nxt)
        (a.out / f"{s['id']}.mp3").write_bytes(base64.b64decode(r["audio_base64"]))
        al = r.get("alignment") or r.get("normalized_alignment")
        meta_path.write_text(json.dumps({"id": s["id"], "text": s["text"], "hash": h, "alignment": al}))
        end = al["character_end_times_seconds"][-1] if al else 0
        sent += len(s["text"])
        print(f"  {s['id']}: {len(s['text'])} chars, {end:.2f}s")
    after = subscription(k)
    print(f"characters sent this run: {sent}")
    if before and after:
        print(f"account character_count {before.get('character_count')} -> {after.get('character_count')}"
              f" (limit {after.get('character_limit')})")


if __name__ == "__main__":
    main()
