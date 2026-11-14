#!/usr/bin/env bash
set -euo pipefail
cd ~/onset
gh label create piece --color 0E8A16 --description "A roadmap piece" 2>/dev/null || true
gh label create blocked --color B60205 --description "Waiting on something outside the repo" 2>/dev/null || true
mk() { # title, body, [extra label]
  if gh issue list --state all --search "in:title \"$1\"" --json title -q '.[].title' | grep -qxF "$1"; then echo "exists: $1"; return; fi
  gh issue create --title "$1" --label piece ${3:+--label "$3"} --body "$2" | tail -1
}
mk "0 · Agent audio reaches the caller (Chert)" "Blocked on Chert: their Mac logs agent_connected but no agent audio reaches the phone, including Chert's own demo agent. Evidence: CHERT_SUPPORT_REPORT.md." blocked
mk "1 · face: smile asymmetry" "See ROADMAP.md row 1. Branch piece/face. Files: onset/measure/face.py, tests/test_face.py."
mk "2 · arms: arm drift over a 10 s hold" "See ROADMAP.md row 2. Branch piece/arms. Files: onset/measure/arms.py, tests/test_arms.py."
mk "3 · speech: sentence repetition" "See ROADMAP.md row 3. Branch piece/speech. Files: onset/speech.py, tests/test_speech.py."
mk "4 · decide: baseline and decision rules" "See ROADMAP.md row 4. Branch piece/decide. Files: onset/decide.py, tests/test_decide.py, docs/thresholds.md."
mk "5 · flow: scripted call flow and overlay" "See ROADMAP.md row 5. Main session, after 1-4 land. Files: agent.py, onset/flow.py, onset/script.py."
mk "6 · chert-api: caregiver iMessage" "See ROADMAP.md row 6. Branch piece/chert-api. Files: onset/chert_api.py, tests/test_chert_api.py, docs/chert-messaging.md."
mk "7 · harness: recorder, replay and CI" "See ROADMAP.md row 7. Branch piece/harness. Files: tools/record.py, tools/replay.py, tests/conftest.py, .github/workflows/ci.yml."
mk "8 · outbound check-in calls (research)" "See ROADMAP.md row 8. chert-api session, after 6. Files: onset/chert_api.py, docs/outbound.md."
gh issue list --label piece --json number,title -q '.[] | "#\(.number) \(.title)"' | sort -t'#' -k2 -n
