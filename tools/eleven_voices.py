"""List the ElevenLabs voices this key can use (name, id, a few labels). Prints no key material."""

import json
import os
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")
key = os.environ.get("ELEVEN_API_KEY") or os.environ["ELEVENLABS_API_KEY"]
req = urllib.request.Request("https://api.elevenlabs.io/v1/voices", headers={"xi-api-key": key})
with urllib.request.urlopen(req, timeout=20) as r:
    voices = json.load(r)["voices"]
for v in voices:
    labels = v.get("labels") or {}
    tags = ", ".join(str(labels.get(k)) for k in ("gender", "age", "accent", "descriptive", "description", "use_case") if labels.get(k))
    print(f"{v['voice_id']}  {v['name']:<28} {tags}")
print(len(voices), "voices")
