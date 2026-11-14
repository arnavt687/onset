#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# One-off probes from setup; not part of the project.
rm -f tools/gemini_probe.py tools/gemini_text_probe.py tools/inspect_plugin.sh tools/add-gemini.sh \
      tools/add-elevenlabs.sh tools/gitcheck.sh tools/tts_models.sh
rm -rf __pycache__
# Pinned dependencies, from what is installed and tested.
"${UV:-uv}" pip freeze --python .venv/bin/python \
  | grep -iE '^(livekit|livekit-agents|livekit-api|livekit-protocol|livekit-plugins-(openai|google|elevenlabs)|numpy|opencv-python-headless|python-dotenv|google-genai)==' > requirements.txt
cat requirements.txt
[ -d .git ] || git init -q -b main
git add -A
echo "--- files staged:"; git status --short
echo "--- secret scan of staged files:"
if git diff --cached -U0 | grep -nE '(AQ\.[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{20,}|sk_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9]{20,}|API[A-Z]{3,}[A-Za-z0-9]{8,}|"?secret"?\s*[:=]\s*"?[A-Za-z0-9]{24,})'; then
  echo "POSSIBLE SECRET FOUND - not committing"; exit 1
fi
git ls-files --cached | grep -E '^\.env\.local$' && { echo ".env.local is staged - not committing"; exit 1; }
echo "clean"
