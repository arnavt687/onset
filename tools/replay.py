"""Replay a recorded call offline: load its steps as Frames / AudioClips and run the meters.

    from tools.replay import load
    rec = load("recordings/20261113-221530-3f9a1c")
    for frame in rec["arms"].frames():  # onset.types.Frame, in recorded order
        meter.add(frame)
    clip = rec["sentence"].audio()       # onset.types.AudioClip or None

CLI (prints what each importable meter makes of the recording; stubs say "not implemented yet"):

    ~/onset/.venv/bin/python tools/replay.py recordings/<call-id> [--step arms] [--offline] [--json]

Which meter runs on which step, by step name (see tools/record.py):
    smile     FaceMeter: neutral frames from a "neutral" step, or frames before a "smile" mark;
              smile frames after the "smile" mark (or the whole step without one)
    arms      ArmMeter over every frame
    sentence  check_sentence(audio, meta["expected"]); needs the network, skipped with --offline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import wave
from collections.abc import Callable, Iterator
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:  # run as a script: make `onset` and `tools` importable
    sys.path.insert(0, str(REPO_ROOT))

from onset.types import AudioClip, Frame  # noqa: E402
from tools.record import STEP_ARMS, STEP_NEUTRAL, STEP_SENTENCE, STEP_SMILE  # noqa: E402


class Step:
    """One recorded step directory: recordings/<call-id>/<step>/."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        self.meta_file: dict = json.loads((self.path / "meta.json").read_text())
        index_file = self.path / "frames.json"
        self.index: dict = json.loads(index_file.read_text()) if index_file.exists() else {"segments": [], "frames": []}
        self.name: str = self.meta_file.get("step", self.path.name)
        self.meta: dict = self.meta_file.get("meta") or {}
        self.marks: list[dict] = self.meta_file.get("marks") or []

    def __repr__(self) -> str:
        return f"Step({self.name!r}, frames={self.frame_count}, audio={self.audio_seconds:.1f}s)"

    @property
    def frame_count(self) -> int:
        return len(self.index["frames"])

    @property
    def timestamps(self) -> list[float]:
        return [f[0] for f in self.index["frames"]]

    @property
    def audio_seconds(self) -> float:
        a = self.meta_file.get("audio")
        return float(a["seconds"]) if a else 0.0

    def mark(self, name: str) -> float | None:
        """Time of the first mark with this name, or None."""
        return next((m["t"] for m in self.marks if m["name"] == name), None)

    def frames(self, t_from: float | None = None, t_to: float | None = None) -> Iterator[Frame]:
        """The recorded frames in order, optionally only those with t_from <= t < t_to."""
        import cv2

        per_segment: dict[int, list[float]] = {}
        for t, seg in self.index["frames"]:
            per_segment.setdefault(seg, []).append(t)
        for seg_no, seg in enumerate(self.index["segments"]):
            times = per_segment.get(seg_no, [])
            if seg["kind"] == "mp4":
                cap = cv2.VideoCapture(str(self.path / seg["file"]))
                try:
                    for t in times:
                        ok, bgr = cap.read()
                        if not ok:
                            break  # truncated file (e.g. the call was killed): stop at what's there
                        if _wanted(t, t_from, t_to):
                            yield Frame(rgb=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), t=t)
                finally:
                    cap.release()
            else:
                for i, t in enumerate(times):
                    if not _wanted(t, t_from, t_to):
                        continue
                    bgr = cv2.imread(str(self.path / seg["file"] / f"{i:06d}.jpg"), cv2.IMREAD_COLOR)
                    if bgr is None:
                        break
                    yield Frame(rgb=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB), t=t)

    def audio(self) -> AudioClip | None:
        a = self.meta_file.get("audio")
        if not a or not (self.path / a["file"]).exists():
            return None
        with wave.open(str(self.path / a["file"]), "rb") as w:
            pcm = w.readframes(w.getnframes())
            rate = w.getframerate()
        return AudioClip(pcm=pcm, sample_rate=rate, t_start=float(a["t_start"]))


def _wanted(t: float, t_from: float | None, t_to: float | None) -> bool:
    return (t_from is None or t >= t_from) and (t_to is None or t < t_to)


class Recording:
    """A recorded call: recordings/<call-id>/, holding one directory per step."""

    def __init__(self, path: str | os.PathLike) -> None:
        self.path = Path(path)
        if (self.path / "meta.json").exists():  # pointed at a single step
            self.steps = {Step(self.path).name: Step(self.path)}
        else:
            found = sorted(p for p in self.path.iterdir() if (p / "meta.json").exists())
            self.steps = {s.name: s for s in map(Step, found)}
        if not self.steps:
            raise FileNotFoundError(f"no recorded steps (meta.json) under {self.path}")

    def __getitem__(self, step: str) -> Step:
        return self.steps[step]

    def __contains__(self, step: str) -> bool:
        return step in self.steps

    def __iter__(self) -> Iterator[Step]:
        return iter(self.steps.values())


def load(path: str | os.PathLike) -> Recording:
    return Recording(path)


# --------------------------------------------------------------------------- running the meters

NOT_IMPLEMENTED = "not implemented yet"


def default_models_dir() -> Path:
    for p in (os.environ.get("ONSET_MODELS"), REPO_ROOT / "models", Path.home() / "onset" / "models"):
        if p and (Path(p) / "face_landmarker.task").exists():
            return Path(p)
    return REPO_ROOT / "models"


def _jsonable(x):
    if is_dataclass(x):
        return _jsonable(asdict(x))
    if isinstance(x, Enum):
        return x.value
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    return x


def _attempt(fn: Callable[[], object]) -> dict:
    """Run one meter; turn stubs and failures into a status instead of an exception."""
    try:
        return {"status": "ok", "result": _jsonable(fn())}
    except NotImplementedError:
        return {"status": NOT_IMPLEMENTED}
    except ImportError as e:
        return {"status": "not importable", "error": str(e)}
    except Exception as e:  # noqa: BLE001 - report, keep replaying the other steps
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def _make(cls, model: Path):
    try:
        return cls(model_path=str(model))
    except TypeError:
        return cls()


def run_face(rec: Recording, smile: Step, models: Path) -> dict:
    def go():
        from onset.measure.face import FaceMeter

        meter = _make(FaceMeter, models / "face_landmarker.task")
        t_smile = smile.mark("smile")
        if STEP_NEUTRAL in rec:
            neutral = rec[STEP_NEUTRAL].frames()
        elif t_smile is not None:
            neutral = smile.frames(smile.mark("neutral"), t_smile)
        else:
            raise ValueError('no neutral frames: record a "neutral" step or mark "smile" in this one')
        for f in neutral:
            meter.add_neutral(f)
        for f in smile.frames(t_smile):
            meter.add_smile(f)
        return meter.result()

    return _attempt(go)


def run_arms(step: Step, models: Path) -> dict:
    def go():
        from onset.measure.arms import ArmMeter

        meter = _make(ArmMeter, models / "pose_landmarker_full.task")
        for f in step.frames():
            meter.add(f)
        return meter.result()

    return _attempt(go)


def run_speech(step: Step, offline: bool) -> dict:
    if offline:
        return {"status": "skipped (--offline)"}

    def go():
        from onset.speech import check_sentence

        clip = step.audio()
        if clip is None:
            raise ValueError("this step has no audio")
        expected = step.meta.get("expected") or step.meta.get("sentence")
        if not expected:
            raise ValueError('no expected sentence: record it as meta={"expected": ...}')
        try:
            from dotenv import load_dotenv

            load_dotenv(REPO_ROOT / ".env.local")
            load_dotenv(Path.home() / "onset" / ".env.local")
        except ImportError:
            pass
        return asyncio.run(check_sentence(clip, expected))

    return _attempt(go)


def run_meters(rec: Recording, models: Path | None = None, offline: bool = False,
               only: str | None = None) -> dict[str, dict]:
    """{step name: {"meter": ..., "status": "ok" | "not implemented yet" | ..., "result": ...}}."""
    models = models or default_models_dir()
    out: dict[str, dict] = {}
    for step in rec:
        if only and step.name != only:
            continue
        name = step.name.lower()
        if name == STEP_NEUTRAL:
            continue  # used by the smile step
        if STEP_SMILE in name or "face" in name:
            out[step.name] = {"meter": "FaceMeter", **run_face(rec, step, models)}
        elif "arm" in name or name == STEP_ARMS:
            out[step.name] = {"meter": "ArmMeter", **run_arms(step, models)}
        elif STEP_SENTENCE in name or "speech" in name:
            out[step.name] = {"meter": "check_sentence", **run_speech(step, offline)}
        else:
            out[step.name] = {"meter": None, "status": "no meter for this step name"}
    return out


def describe(step: Step) -> str:
    ts = step.timestamps
    span = (ts[-1] - ts[0]) if len(ts) > 1 else 0.0
    fps = (len(ts) - 1) / span if span > 0 else 0.0
    sizes = ", ".join(f"{s['width']}x{s['height']}" for s in step.index["segments"]) or "-"
    dropped = step.meta_file.get("frames", {}).get("dropped", 0)
    marks = ", ".join(f"{m['name']}@{m['t']:.2f}s" for m in step.marks) or "none"
    return (f"{step.frame_count} frames ({sizes}, {span:.1f} s, {fps:.1f} fps, {dropped} dropped), "
            f"audio {step.audio_seconds:.1f} s, marks: {marks}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording", help="recordings/<call-id> (or one step directory inside it)")
    ap.add_argument("--step", help="only this step")
    ap.add_argument("--models", type=Path, help="MediaPipe models directory (default: models/ or ~/onset/models)")
    ap.add_argument("--offline", action="store_true", help="skip meters that need the network (speech)")
    ap.add_argument("--json", action="store_true", help="print the results as JSON")
    args = ap.parse_args(argv)

    try:
        rec = load(args.recording)
    except (FileNotFoundError, NotADirectoryError, json.JSONDecodeError) as e:
        print(f"replay: cannot load {args.recording}: {e}", file=sys.stderr)
        return 2
    results = run_meters(rec, args.models, args.offline, args.step)
    if args.json:
        print(json.dumps(results, indent=2))
        return 0
    print(f"recording {rec.path}")
    for step in rec:
        if args.step and step.name != args.step:
            continue
        print(f"\n[{step.name}] {describe(step)}")
        r = results.get(step.name)
        if r is None:
            continue
        line = f"  {r['meter'] or '-'}: {r['status']}"
        if "error" in r:
            line += f" ({r['error']})"
        print(line)
        if r.get("result") is not None:
            for k, v in r["result"].items():
                print(f"    {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
