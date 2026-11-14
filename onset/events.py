"""The live event stream: what the call flow is doing, for the demo dashboard and replays.

The flow (live call or replay) writes to a run directory:

    runs/live/                      reset when a call or replay starts; also copied to runs/<run-id>/
      events.jsonl                  one JSON object per line, appended as things happen
      frame.jpg                     the latest annotated agent video frame (~5 fps), replaced atomically
      audio/<n>.wav                 each spoken line's audio, so the dashboard can play it in the room

Every event has "t" (seconds since the run started), "type", and the fields below.

    run_started   run_id, source ("call"|"replay"), patient_id, has_baseline (bool), sentence
    step          name ("greet"|"face_neutral"|"face_smile"|"face_reframe"|"arms_wait"|"arms_hold"|
                        "sentence"|"deciding"|"closing"|"baseline_saved"), caption (str, what the
                  caller sees), seconds (float|None, countdown length)
    framing       check ("face"|"arms"), phase ("neutral"|"smile"|"wait"|"hold"), problem (str, e.g.
                  "left_hand", "no_face"; see onset/script.py ARMS_FRAMING / FACE_FRAMING),
                  action ("retry": the fix-it caption is shown and the step runs again |
                  "give_up": no retries left, the step carries on and may end UNCLEAR),
                  retry (int, 1-based), max_retries (int), caption (str)
    say           text, audio (path relative to the run dir, or None)
    live          step, metrics (dict of small numbers for the meters, e.g. {"lift_left": 0.12})
    vad           how the sentence recording ended: enabled (bool), reason ("speech_end"|"timer"),
                  started_at, ended_at (seconds from "Say it now", negative = in the pre-roll, or None),
                  speech_s, record_s, floor_db
    result        check ("face"|"arms"|"speech"), quality, data (the FaceResult/ArmResult/SpeechResult as a dict)
    decision      outcome ("escalate"|"unclear"|"no_change_detected"), reasons (list[str]),
                  baseline_used (bool)
    caregiver     text, status ("dry_run"|"sent"|"failed"|"unknown"|"skipped"), detail (str)
    action        kind ("imessage"|"invite"|"call"), live (bool: false = dry run), status, target
                  (masked phone, e.g. "+1…02"), detail, plus kind-specific fields (chars, link)
    caregiver_join status ("invited"|"joined"|"talking"|"left"|"failed"), detail
    run_ended     reason

`caregiver_link.txt` (0600, only on escalation): the caregiver's join link, which holds tokens.
It is never put in an event; the local dashboard offers it as a demo button.

`runs/` is gitignored: it can hold a person's face and voice.
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

RUNS = Path(os.environ.get("ONSET_RUNS_DIR", Path(__file__).resolve().parent.parent / "runs"))


def _plain(value: Any) -> Any:
    """Dataclasses, enums and numpy values -> JSON-safe Python values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return None  # frames and arrays never go into events
    return value


LIVE_LOCK_STALE_S = 1800.0  # a crashed call's live.lock is ignored after this long


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def take_live(root: Path, run_id: str) -> bool:
    """Claim runs/live/ for this run. False when another process's call holds it (a second
    call while one is running): that call keeps the dashboard; this one writes to runs/<id>/."""
    lock = root / "live.lock"
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        return True
    for _ in range(3):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"pid": os.getpid(), "run_id": run_id, "at": time.time()}, f)
            return True
        except FileExistsError:
            try:
                info = json.loads(lock.read_text())
                pid, at = int(info["pid"]), float(info["at"])
            except (OSError, ValueError, KeyError, TypeError):
                pid, at = -1, 0.0
            if pid != os.getpid() and pid > 0 and _alive(pid) and time.time() - at < LIVE_LOCK_STALE_S:
                return False
            try:  # stale, ours (an unclosed earlier run in this process), or unreadable
                lock.unlink()
            except OSError:
                pass
        except OSError:
            return True
    return True


def release_live(root: Path, run_id: str) -> None:
    lock = root / "live.lock"
    try:
        info = json.loads(lock.read_text())
        if info.get("run_id") == run_id and int(info.get("pid", -1)) == os.getpid():
            lock.unlink()
    except (OSError, ValueError, TypeError):
        pass


class EventSink:
    """Append-only event log plus latest frame for one run. Thread-safe; never raises into the call."""

    FRAME_INTERVAL = 0.2  # seconds between frame.jpg writes

    def __init__(self, run_id: str, source: str, root: Path = RUNS) -> None:
        self.run_id, self.source, self.root = run_id, source, root
        self.is_live = take_live(root, run_id)
        self.live = root / "live" if self.is_live else root / run_id
        self.archive = root / run_id if self.is_live else None
        if not self.is_live:
            import logging

            logging.getLogger("onset.events").warning(
                "[onset] another call is running and owns runs/live; this run (%s) writes to %s", run_id, self.live)
        self._lock = threading.Lock()
        self._t0 = time.monotonic()
        self._last_frame = 0.0
        self._n_audio = 0
        try:
            if self.live.exists():
                shutil.rmtree(self.live)
            (self.live / "audio").mkdir(parents=True, mode=0o700)
            self._events = open(self.live / "events.jsonl", "a", encoding="utf-8")
        except OSError:
            self._events = None

    def emit(self, type: str, **fields: Any) -> None:
        record = {"t": round(time.monotonic() - self._t0, 3), "type": type, **_plain(fields)}
        with self._lock:
            if self._events is None:
                return
            try:
                self._events.write(json.dumps(record) + "\n")
                self._events.flush()
            except (OSError, TypeError, ValueError):
                pass

    def frame(self, rgba_or_rgb: np.ndarray) -> None:
        """Offer the latest annotated frame; written at most every FRAME_INTERVAL seconds."""
        now = time.monotonic()
        if now - self._last_frame < self.FRAME_INTERVAL:
            return
        self._last_frame = now
        try:
            import cv2

            img = rgba_or_rgb[..., :3]
            ok, buf = cv2.imencode(".jpg", cv2.cvtColor(img, cv2.COLOR_RGB2BGR), [cv2.IMWRITE_JPEG_QUALITY, 75])
            if ok:
                tmp = self.live / "frame.jpg.tmp"
                tmp.write_bytes(buf.tobytes())
                os.replace(tmp, self.live / "frame.jpg")
        except Exception:  # noqa: BLE001 - the dashboard is optional; the call is not
            pass

    def audio_path(self) -> tuple[Path, str]:
        """A new path for one spoken line's WAV, and its path relative to the run dir."""
        with self._lock:
            self._n_audio += 1
            rel = f"audio/{self._n_audio:03d}.wav"
        return self.live / rel, rel

    def private_file(self, name: str, text: str) -> Path | None:
        """A small owner-only file in the live run dir (e.g. the caregiver link for the local
        dashboard's demo button). Never copied into events. Returns its path, or None."""
        if "/" in name or "\\" in name or name.startswith("."):
            return None
        path = self.live / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            return path
        except OSError:
            return None

    def close(self, reason: str = "") -> None:
        self.emit("run_ended", reason=reason)
        with self._lock:
            if self._events is not None:
                self._events.close()
                self._events = None
        if self.archive is None:  # not the live run: it already wrote to runs/<id>/
            try:
                (self.live / "caregiver_link.txt").unlink(missing_ok=True)
            except OSError:
                pass
            return
        try:
            if self.archive.exists():
                shutil.rmtree(self.archive)
            shutil.copytree(self.live, self.archive, ignore=shutil.ignore_patterns("caregiver_link.txt"))
        except OSError:
            pass
        finally:
            release_live(self.root, self.run_id)
