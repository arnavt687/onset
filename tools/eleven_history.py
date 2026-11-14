"""Recent ElevenLabs generations on this account (time, characters, voice, start of text). No key material printed."""

import datetime as dt
import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
key = os.environ.get("ELEVEN_API_KEY") or os.environ["ELEVENLABS_API_KEY"]
req = urllib.request.Request("https://api.elevenlabs.io/v1/history?page_size=10", headers={"xi-api-key": key})
with urllib.request.urlopen(req, timeout=20) as r:
    items = json.load(r).get("history", [])
for h in items:
    t = dt.datetime.fromtimestamp(h["date_unix"], dt.timezone.utc).strftime("%H:%M:%S UTC")
    print(t, h.get("voice_name"), h.get("model_id"), f'{h.get("character_count_change_to", 0) - h.get("character_count_change_from", 0)} chars',
          repr((h.get("text") or "")[:50]))
print(len(items), "items")
