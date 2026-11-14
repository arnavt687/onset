#!/usr/bin/env bash
# One-time setup for a fresh clone (safe to run again; it skips what is already done).
#
#   tools/setup.sh              Python 3.12 venv + requirements, MediaPipe models, Node/Chert CLI check,
#                               .env.local from .env.example (owner-only)
#   tools/setup.sh --profiles   also copy config/patients.example.json to config/patients.json
#   tools/setup.sh --dry-run    print the steps, change nothing
#
# Linux, WSL Ubuntu or macOS. Needs: python3.12 (or uv, which can fetch it), curl, Node >= 20
# (nvm is fine). Next steps: docs/run-on-your-chert-line.md
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT=$PWD
DRY=0
PROFILES=0
FAILED=0

say()  { printf '\033[1m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[setup] %s\033[0m\n' "$*" >&2; }
fail() { printf '\033[31m[setup] %s\033[0m\n' "$*" >&2; FAILED=1; }
run()  { printf '  + %s\n' "$*"; [ "$DRY" = 1 ] || "$@"; }

for arg in "$@"; do
  case "$arg" in
    --dry-run|-n) DRY=1 ;;
    --profiles) PROFILES=1 ;;
    -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done
[ "$DRY" = 1 ] && say "dry run: nothing is changed"

# ---------------------------------------------------------------- Python 3.12 + requirements
find_uv() {
  local c
  for c in "$(command -v uv 2>/dev/null)" "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" "$HOME/.venvs/uv/bin/uv"; do
    [ -n "$c" ] && [ -x "$c" ] && { echo "$c"; return 0; }
  done
  return 1
}
VENV_PY=$ROOT/.venv/bin/python
UV=$(find_uv || true)
if [ -x "$VENV_PY" ]; then
  ver=$("$VENV_PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
  if [ "$ver" = 3.12 ]; then
    say "python: .venv exists (Python 3.12)"
  else
    fail "python: .venv uses Python ${ver:-?}, Onset needs 3.12: remove .venv and run tools/setup.sh again"
  fi
elif command -v python3.12 >/dev/null 2>&1; then
  say "python: creating .venv with $(command -v python3.12)"
  run python3.12 -m venv .venv
elif [ -n "$UV" ]; then
  say "python: no python3.12 on PATH; creating .venv with uv (downloads Python 3.12 if needed)"
  run "$UV" venv --python 3.12 .venv
else
  fail "python: Python 3.12 not found. Install it (Ubuntu: sudo apt install python3.12 python3.12-venv, or the deadsnakes PPA; macOS: brew install python@3.12), or install uv (https://docs.astral.sh/uv/) and run this again"
fi

if [ "$FAILED" = 0 ]; then
  STAMP=.venv/.onset-requirements
  want=$(cksum < requirements.txt)
  if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$want" ]; then
    say "requirements: already installed (requirements.txt unchanged)"
  else
    say "requirements: installing requirements.txt into .venv"
    if [ -n "$UV" ] && ! "$VENV_PY" -m pip --version >/dev/null 2>&1; then
      run "$UV" pip install --python "$VENV_PY" -r requirements.txt && { [ "$DRY" = 1 ] || echo "$want" > "$STAMP"; }
    else
      { run "$VENV_PY" -m pip install --upgrade --quiet pip && run "$VENV_PY" -m pip install -r requirements.txt; } \
        && { [ "$DRY" = 1 ] || echo "$want" > "$STAMP"; } || fail "requirements: pip install failed (see above)"
    fi
  fi
fi

# MediaPipe's native library loads EGL/GLES even for CPU inference (Linux only).
if [ "$(uname -s)" = Linux ] && command -v ldconfig >/dev/null 2>&1; then
  missing=()
  for lib in libEGL.so.1 libGLESv2.so.2 libGL.so.1; do
    ldconfig -p 2>/dev/null | grep -q "$lib" || missing+=("$lib")
  done
  [ ${#missing[@]} -eq 0 ] && say "system libraries: EGL/GLES present" \
    || warn "system libraries missing for MediaPipe (${missing[*]}): sudo apt-get install -y libegl1 libgles2 libgl1"
fi

# ---------------------------------------------------------------- MediaPipe models (same URLs as CI)
fetch_model() {
  if [ -s "models/$1" ]; then say "model: models/$1 present"; return 0; fi
  say "model: downloading $1"
  run curl -fsSL --retry 3 -o "models/$1.part" "$2" && run mv "models/$1.part" "models/$1" \
    || { rm -f "models/$1.part"; fail "model: could not download $1"; }
}
run mkdir -p models
fetch_model face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
fetch_model pose_landmarker_full.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task

# ---------------------------------------------------------------- Node + the Chert CLI
[ -s "$HOME/.nvm/nvm.sh" ] && source "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
if command -v node >/dev/null 2>&1; then
  major=$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null)
  if [ "${major:-0}" -ge 20 ]; then
    say "node: $(node --version)"
    say "chert cli: checking tools/chert.sh --version (npx fetches @trychert/facetime the first time)"
    if [ "$DRY" = 1 ]; then
      printf '  + bash tools/chert.sh --version\n'
    elif v=$(timeout 180 bash tools/chert.sh --version 2>/dev/null | tail -1) && [ -n "$v" ]; then
      say "chert cli: $v"
    else
      fail "chert cli: tools/chert.sh --version did not run (network? npm?). Try: bash tools/chert.sh --help"
    fi
  else
    fail "node: $(node --version) is too old; need >= 20 (nvm install 24 && nvm use 24)"
  fi
else
  fail "node: not found. Install Node 24 with nvm: https://github.com/nvm-sh/nvm, then nvm install 24"
fi

# ---------------------------------------------------------------- settings
if [ -f .env.local ]; then
  say ".env.local: exists (left as it is)"
  [ "$DRY" = 1 ] || chmod 600 .env.local
else
  say ".env.local: creating from .env.example (owner-only); fill it in next"
  run cp .env.example .env.local && run chmod 600 .env.local
fi
if [ "$PROFILES" = 1 ]; then
  if [ -f config/patients.json ]; then
    say "profiles: config/patients.json exists (left as it is)"
  else
    say "profiles: creating config/patients.json from the example (gitignored; edit the phones)"
    run cp config/patients.example.json config/patients.json && run chmod 600 config/patients.json
  fi
fi

echo
if [ "$FAILED" = 1 ]; then
  fail "setup is not complete: fix the lines above and run tools/setup.sh again"
  exit 1
fi
say "done. Next (docs/run-on-your-chert-line.md):"
echo "  1. fill in .env.local (names and where to get each value are in the doc)"
echo "  2. tools/chert.sh login"
echo "  3. tools/demo.sh --check   then   tools/demo.sh"
