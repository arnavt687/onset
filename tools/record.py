"""Opt-in recording of one call's caller video and audio, step by step, for offline replay.

Recordings hold a person's face and voice, so they are:
  * off unless ONSET_RECORD=1 is set in the environment;
  * written only under `recordings/` (gitignored; ONSET_RECORD_DIR moves the root);
  * owner-only on disk: directories 0700, files 0600.

Layout of one call:

    recordings/<call-id>/<step>/
        video_000.mp4   caller frames (ONSET_RECORD_FORMAT=jpg writes frames_000/000000.jpg ... instead)
        frames.json     sidecar: one timestamp per written frame, and which segment holds it
        audio.wav       caller audio, 16-bit mono PCM
        meta.json       step name, start time, counts, dropped frames, marks, step metadata

A new video segment starts whenever the frame size changes (for example the phone rotates).

Use from the call (the main session wires this into agent.py):

    rec = Recorder.for_call(call_id, "arms")      # no-op unless ONSET_RECORD=1
    rec.add_frame(rgb, t)                         # never blocks: drops frames when behind
    rec.add_audio(pcm, sample_rate, t)
    rec.mark("hold_start", t)                     # optional phase marks for replay
    rec.close()                                   # flushes; call via asyncio.to_thread in async code

All disk work happens on one background thread per step, fed by bounded queues, so a slow
disk can only cost recorded frames, never call latency.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import secrets
import threading
import time
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("onset.record")

REPO_ROOT = Path(__file__).resolve().parent.parent
FORMAT_VERSION = 1
VIDEO_FPS_HINT = 15.0  # container frame rate only; frames.json holds the real timestamps
DIR_MODE = 0o700
FILE_MODE = 0o600

# Step names the replay CLI recognises (see tools/replay.py). Other names are recorded fine,
# replay just won't know which meter to run on them.
STEP_NEUTRAL = "neutral"  # relaxed face before the smile (optional; a "smile" mark also works)
STEP_SMILE = "smile"
STEP_ARMS = "arms"
STEP_SENTENCE = "sentence"  # set meta={"expected": "<the sentence>"} so replay can score it


def recording_enabled() -> bool:
    return os.environ.get("ONSET_RECORD", "").strip() == "1"


def recordings_root() -> Path:
    return Path(os.environ.get("ONSET_RECORD_DIR") or REPO_ROOT / "recordings")


def new_call_id() -> str:
    """A sortable, unguessable id: 20261113-221530-3f9a1c."""
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(3)


def _mkdir_private(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    try:
        os.chmod(path, DIR_MODE)  # mkdir's mode is filtered by the umask
    except OSError:
        pass


def _touch_private(path: Path) -> None:
    """Create (or truncate) path as an owner-only file before anything else opens it."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    os.close(fd)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


def _write_private(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    try:
        os.chmod(path, FILE_MODE)
    except OSError:
        pass


_STOP = object()


class Recorder:
    """Records one step of one call. Does nothing at all unless recording is enabled."""

    def __init__(
        self,
        dir: str | os.PathLike,
        step_name: str,
        *,
        enabled: bool | None = None,
        fmt: str | None = None,
        max_frames_queued: int = 32,
        max_audio_queued: int = 2000,
        meta: dict | None = None,
        copy: bool = True,
    ) -> None:
        """
        dir: the call's directory, normally recordings/<call-id> (see Recorder.for_call).
        step_name: subdirectory for this step, e.g. "smile", "arms", "sentence".
        enabled: None follows ONSET_RECORD=1; tests pass True/False.
        fmt: "mp4" (default) or "jpg"; None follows ONSET_RECORD_FORMAT.
        max_frames_queued: frames waiting for the writer before new ones are dropped
            (32 frames of 1280x720 RGB is about 90 MB, about 2 s of video).
        meta: extra step metadata saved in meta.json (e.g. {"expected": sentence}).
        copy: copy each frame before queueing, so the caller may reuse its buffer.
        """
        self.enabled = recording_enabled() if enabled is None else bool(enabled)
        self.step = step_name
        self.dir = Path(dir) / step_name
        self.fmt = (fmt or os.environ.get("ONSET_RECORD_FORMAT") or "mp4").lower()
        if self.fmt not in ("mp4", "jpg"):
            raise ValueError(f"unknown recording format {self.fmt!r} (mp4 or jpg)")
        self.copy = copy
        self.meta: dict = dict(meta or {})
        self.marks: list[dict] = []
        self.frames_dropped = 0  # writer was behind
        self.frames_invalid = 0  # not an H x W x 3/4 array
        self.audio_dropped = 0
        self.frames_written = 0
        self.error: str | None = None
        self._closed = False
        self._lock = threading.Lock()
        if not self.enabled:
            return

        self.started_at = datetime.now(timezone.utc).isoformat()
        self._frames: queue.Queue = queue.Queue(maxsize=max_frames_queued)
        self._audio: queue.Queue = queue.Queue(maxsize=max_audio_queued)
        root = recordings_root().resolve()
        if self.dir.resolve().is_relative_to(root):
            _mkdir_private(root)
        _mkdir_private(self.dir.parent)  # the call's directory
        _mkdir_private(self.dir)
        log.warning(
            "[onset] RECORDING step %r to %s (ONSET_RECORD=1; contains the caller's face and voice)",
            step_name, self.dir,
        )
        # Writer state, touched only by the writer thread.
        self._segments: list[dict] = []
        self._frame_index: list[list] = []  # [t, segment]
        self._writer = None  # cv2.VideoWriter of the current mp4 segment
        self._seg_size: tuple[int, int] | None = None
        self._wav: wave.Wave_write | None = None
        self._audio_info: dict | None = None
        self._audio_warned = False
        self._thread = threading.Thread(target=self._run, name=f"onset-record-{step_name}", daemon=True)
        self._thread.start()

    @classmethod
    def for_call(cls, call_id: str, step_name: str, **kw) -> "Recorder":
        """Recorder for recordings/<call-id>/<step> under the recordings root."""
        return cls(recordings_root() / call_id, step_name, **kw)

    # ------------------------------------------------------------------ called from the call

    def add_frame(self, rgb: np.ndarray, t: float) -> bool:
        """Queue one upright frame (H x W x 3 RGB or x 4 RGBA, uint8). Never blocks.

        Returns False when the frame was not queued (disabled, closed or writer behind)."""
        if not self.enabled or self._closed:
            return False
        if getattr(rgb, "ndim", 0) != 3 or rgb.shape[2] not in (3, 4):
            self.frames_invalid += 1
            return False
        try:
            self._frames.put_nowait((np.array(rgb, copy=True) if self.copy else rgb, float(t)))
            return True
        except queue.Full:
            self.frames_dropped += 1
            return False

    def add_audio(self, pcm: bytes | memoryview | np.ndarray, sample_rate: int, t: float) -> bool:
        """Queue a chunk of 16-bit mono PCM that started at time t. Never blocks."""
        if not self.enabled or self._closed:
            return False
        data = pcm.astype("<i2").tobytes() if isinstance(pcm, np.ndarray) else bytes(pcm)
        try:
            self._audio.put_nowait((data, int(sample_rate), float(t)))
            return True
        except queue.Full:
            self.audio_dropped += 1
            return False

    def mark(self, name: str, t: float) -> None:
        """A named moment in the step (e.g. "smile" when the smile prompt ends)."""
        if self.enabled and not self._closed:
            with self._lock:
                self.marks.append({"name": name, "t": float(t)})

    def set_meta(self, **kw) -> None:
        if self.enabled and not self._closed:
            with self._lock:
                self.meta.update(kw)

    def close(self, timeout: float = 30.0) -> None:
        """Flush everything queued, close the files and write meta.json.

        Blocks while the writer catches up: from async code use `await asyncio.to_thread(rec.close)`."""
        if not self.enabled or self._closed:
            self._closed = True
            return
        self._closed = True
        deadline = time.monotonic() + timeout
        for q in (self._frames, self._audio):
            while True:  # the stop marker must get in even if the queue is full
                try:
                    q.put(_STOP, timeout=0.1)
                    break
                except queue.Full:
                    if time.monotonic() > deadline or not self._thread.is_alive():
                        break
        self._thread.join(max(0.0, deadline - time.monotonic()))
        if self._thread.is_alive():
            log.error("[onset] recorder %s did not finish within %.0f s", self.dir, timeout)
        if self.frames_dropped or self.audio_dropped:
            log.warning("[onset] recorder %s dropped %d frames and %d audio chunks (writer was behind)",
                        self.dir, self.frames_dropped, self.audio_dropped)

    def __enter__(self) -> "Recorder":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ writer thread

    def _run(self) -> None:
        frames_done = audio_done = False
        while not (frames_done and audio_done):
            worked = False
            if not frames_done:
                try:
                    item = self._frames.get(timeout=0.02)
                    worked = True
                    if item is _STOP:
                        frames_done = True
                    else:
                        self._safe(self._write_frame, *item)
                except queue.Empty:
                    pass
            if not audio_done:
                while True:  # audio chunks are small: drain them all
                    try:
                        item = self._audio.get_nowait()
                    except queue.Empty:
                        break
                    worked = True
                    if item is _STOP:
                        audio_done = True
                        break
                    self._safe(self._write_audio, *item)
            if not worked and frames_done:
                time.sleep(0.01)
        self._safe(self._finish, always=True)

    def _safe(self, fn, *args, always: bool = False) -> None:
        if self.error is not None and not always:
            return  # after a disk error keep draining the queues, write nothing more
        try:
            fn(*args)
        except Exception as e:  # never let recording take the call down
            self.error = f"{type(e).__name__}: {e}"
            log.exception("[onset] recorder %s failed; recording stops, the call continues", self.dir)

    def _write_frame(self, img: np.ndarray, t: float) -> None:
        import cv2

        if img.ndim != 3 or img.shape[2] not in (3, 4):
            raise ValueError(f"expected H x W x 3 or 4 frame, got {img.shape}")
        if img.dtype != np.uint8:
            img = np.clip(img, 0, 255).astype(np.uint8)
        bgr = cv2.cvtColor(img, cv2.COLOR_RGBA2BGR if img.shape[2] == 4 else cv2.COLOR_RGB2BGR)
        h, w = bgr.shape[:2]
        if self._seg_size != (w, h):
            self._new_segment(w, h)
        seg = self._segments[-1]
        if seg["kind"] == "mp4":
            self._writer.write(bgr)
        else:
            ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 95])
            if not ok:
                raise RuntimeError("JPEG encoding failed")
            _write_private(self.dir / seg["file"] / f"{seg['frames']:06d}.jpg", buf.tobytes())
        seg["frames"] += 1
        self._frame_index.append([t, len(self._segments) - 1])
        self.frames_written += 1

    def _new_segment(self, w: int, h: int) -> None:
        import cv2

        self._close_video()
        n = len(self._segments)
        seg = None
        if self.fmt == "mp4":
            path = self.dir / f"video_{n:03d}.mp4"
            _touch_private(path)
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), VIDEO_FPS_HINT, (w, h))
            if writer.isOpened():
                self._writer = writer
                seg = {"kind": "mp4", "file": path.name}
            else:
                path.unlink(missing_ok=True)
                log.warning("[onset] recorder: no MP4 encoder available, writing JPEG frames instead")
        if seg is None:
            sub = self.dir / f"frames_{n:03d}"
            _mkdir_private(sub)
            seg = {"kind": "jpg", "file": sub.name}
        seg.update(width=w, height=h, frames=0)
        self._segments.append(seg)
        self._seg_size = (w, h)

    def _close_video(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
            try:
                os.chmod(self.dir / self._segments[-1]["file"], FILE_MODE)
            except OSError:
                pass

    def _write_audio(self, data: bytes, sample_rate: int, t: float) -> None:
        if self._wav is None:
            path = self.dir / "audio.wav"
            _touch_private(path)
            self._wav = wave.open(str(path), "wb")
            self._wav.setnchannels(1)
            self._wav.setsampwidth(2)
            self._wav.setframerate(sample_rate)
            self._audio_info = {"file": path.name, "sample_rate": sample_rate, "t_start": t,
                                "samples": 0, "rate_mismatch_chunks": 0}
        info = self._audio_info
        if sample_rate != info["sample_rate"]:
            info["rate_mismatch_chunks"] += 1
            if not self._audio_warned:
                self._audio_warned = True
                log.warning("[onset] recorder: audio rate changed %d -> %d Hz; skipping those chunks",
                            info["sample_rate"], sample_rate)
            return
        if len(data) % 2:
            data = data[:-1]
        self._wav.writeframes(data)
        info["samples"] += len(data) // 2

    def _finish(self) -> None:
        self._close_video()
        if self._wav is not None:
            self._wav.close()
            self._wav = None
        _write_private(self.dir / "frames.json", json.dumps({
            "version": FORMAT_VERSION,
            "segments": self._segments,
            "frames": self._frame_index,  # [t, segment index], in write order
        }).encode())
        with self._lock:
            marks, meta = list(self.marks), dict(self.meta)
        ts = [f[0] for f in self._frame_index]
        _write_private(self.dir / "meta.json", json.dumps({
            "version": FORMAT_VERSION,
            "step": self.step,
            "started_at": self.started_at,
            "format": self.fmt,
            "frames": {
                "written": len(ts),
                "dropped": self.frames_dropped,
                "invalid": self.frames_invalid,
                "t_first": ts[0] if ts else None,
                "t_last": ts[-1] if ts else None,
                "segments": len(self._segments),
            },
            "audio": None if self._audio_info is None else {
                **self._audio_info, "dropped_chunks": self.audio_dropped,
                "seconds": self._audio_info["samples"] / self._audio_info["sample_rate"],
            },
            "marks": sorted(marks, key=lambda m: m["t"]),
            "meta": meta,
            "error": self.error,
        }, indent=2).encode())
