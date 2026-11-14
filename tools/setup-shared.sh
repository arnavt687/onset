#!/usr/bin/env bash
# Shared dependencies for the parallel pieces: MediaPipe + its models, pytest.
set -euo pipefail
cd "$(dirname "$0")/.."
"${UV:-uv}" pip install --quiet --python .venv/bin/python mediapipe pytest
mkdir -p models notes tests docs
fetch() { [ -s "models/$1" ] || curl -fsSL -o "models/$1" "$2"; }
fetch face_landmarker.task https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
fetch pose_landmarker_full.task https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_full/float16/latest/pose_landmarker_full.task
ls -la models
.venv/bin/python - <<'PY'
import importlib.metadata as m
import mediapipe as mp
from mediapipe.tasks.python import vision, BaseOptions
for name in ("face_landmarker.task", "pose_landmarker_full.task"):
    opts = (vision.FaceLandmarkerOptions if "face" in name else vision.PoseLandmarkerOptions)(
        base_options=BaseOptions(model_asset_path=f"models/{name}"))
    (vision.FaceLandmarker if "face" in name else vision.PoseLandmarker).create_from_options(opts).close()
    print("loads ok:", name)
print("mediapipe", m.version("mediapipe"), "| pytest", m.version("pytest"))
PY
grep -qi '^mediapipe==' requirements.txt || "${UV:-uv}" pip freeze --python .venv/bin/python | grep -iE '^(mediapipe|pytest)==' >> requirements.txt
grep -q '^models/' .gitignore || printf '\n# MediaPipe models (downloaded by tools/setup-shared.sh)\nmodels/\n' >> .gitignore
touch tests/__init__.py
