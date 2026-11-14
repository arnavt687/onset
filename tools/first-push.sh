#!/usr/bin/env bash
set -euo pipefail
cd ~/onset
git add -A
git ls-files --cached | grep -qE '^\.env\.local$' && { echo ".env.local staged; aborting"; exit 1; }
git commit -q -F - <<'MSG'
Onset step 1: FaceTime video agent on Chert

Python LiveKit agent that answers Chert FaceTime Video calls, reads the
caller's camera (logs resolution and fps), sends it back with a test
overlay, and greets with ElevenLabs on Chert's bridge_ready signal.
Includes a fake-call harness, offline self-test and Chert helpers.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
MSG
if gh repo view arnavt687/onset >/dev/null 2>&1; then
  echo "repo arnavt687/onset already exists; not creating"; exit 2
fi
gh repo create onset --private --source . --remote origin --push \
  --description "Onset: a FaceTime stroke check-in agent prototype (Chert + LiveKit)"
gh repo view --json url,visibility,defaultBranchRef -q '"\(.url) \(.visibility) \(.defaultBranchRef.name)"'
git log --oneline -1
