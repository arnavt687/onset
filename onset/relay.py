"""The caregiver's side room: Onset listens there and relays them into the call.

Chert lets only one participant publish audio in the call's room, so the caregiver's
microphone and camera are published into `<room>-cg` instead (onset/caregiver.py mints the
tokens). `SideRoomRelay` joins that room hidden and subscribe-only and hands agent.py:

  * each caregiver audio track (agent.py plays it through its own single audio track, so the
    caller hears the caregiver);
  * the caregiver's camera frames (drawn picture-in-picture by CheckFlow);
  * presence changes ("talking" when they publish audio, "left" when they leave).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable

import numpy as np

log = logging.getLogger("onset.relay")

PAGE_TOPIC = "onset-cg"
# What the caregiver page may report about itself, and how the dashboard says it.
PAGE_STATES = {
    "asking": "the caregiver's phone is asking them to allow the microphone and camera",
    "mic_denied": "the caregiver's microphone is blocked in their browser: they can watch but not talk",
    "no_mic": "the caregiver's device has no microphone available: they can watch but not talk",
    "no_camera": "the caregiver is talking without a camera",
    "talk_failed": "the caregiver's page could not start talking (connection)",
    "muted": "the caregiver muted their microphone",
    "unmuted": "the caregiver unmuted their microphone",
    "stopped": "the caregiver pressed Leave: they are watching only",
}

AudioTrackFn = Callable[[object], None]  # rtc.RemoteAudioTrack
FrameFn = Callable[[np.ndarray, float], None]
StatusFn = Callable[[str, str], None]


class SideRoomRelay:
    def __init__(self, *, url: str, token: str, on_audio_track: AudioTrackFn, on_frame: FrameFn,
                 on_status: StatusFn, clock: Callable[[], float]) -> None:
        self.url, self._token = url, token
        self.on_audio_track, self.on_frame, self.on_status = on_audio_track, on_frame, on_status
        self.clock = clock
        self.room = None
        self._tasks: set[asyncio.Task] = set()
        self._talkers: set[str] = set()  # identities whose microphone reached us

    def _spawn(self, coro) -> None:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)

    async def start(self) -> bool:
        from livekit import rtc

        room = rtc.Room()
        self.room = room

        @room.on("track_subscribed")
        def _sub(track, pub, p) -> None:  # noqa: ANN001
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                log.info("[onset] caregiver audio arrived in the side room (%s)", p.identity)
                self._talkers.add(p.identity)
                self.on_status("talking", "")
                self.on_audio_track(track)
            elif track.kind == rtc.TrackKind.KIND_VIDEO:
                self._spawn(self._frames(track))

        @room.on("participant_disconnected")
        def _left(p) -> None:  # noqa: ANN001
            # Only someone who was talking can stop: the page also joins briefly, without a
            # microphone, just to report that the microphone is blocked.
            if p.identity in self._talkers:
                self._talkers.discard(p.identity)
                if not self._talkers:
                    self.on_status("left", "the caregiver stopped talking")

        @room.on("data_received")
        def _data(packet) -> None:  # noqa: ANN001
            # The caregiver page's own state (topic "onset-cg"), e.g. {"state": "mic_denied"}.
            try:
                if getattr(packet, "topic", "") != PAGE_TOPIC:
                    return
                msg = json.loads(bytes(packet.data).decode("utf-8"))
                state = str(msg.get("state") or "")[:40]
                if state in PAGE_STATES:
                    log.info("[onset] caregiver page: %s", state)
                    self.on_status("page", PAGE_STATES[state])
            except Exception:  # noqa: BLE001 - never let a stray packet touch the call
                log.debug("unreadable packet in the side room", exc_info=True)

        try:
            await room.connect(self.url, self._token, rtc.RoomOptions(auto_subscribe=True))
        except Exception as e:  # noqa: BLE001
            log.warning("[onset] could not join the caregiver side room: %s", type(e).__name__)
            self.room = None
            return False
        log.info("[onset] listening for the caregiver in the side room")
        return True

    async def _frames(self, track) -> None:  # noqa: ANN001
        from livekit import rtc

        stream = rtc.VideoStream(track)
        try:
            async for ev in stream:
                f = ev.frame
                if f.type != rtc.VideoBufferType.RGBA:
                    f = f.convert(rtc.VideoBufferType.RGBA)
                img = np.frombuffer(f.data, dtype=np.uint8).reshape(f.height, f.width, 4)
                rot = {rtc.VideoRotation.VIDEO_ROTATION_90: 0, rtc.VideoRotation.VIDEO_ROTATION_180: 1,
                       rtc.VideoRotation.VIDEO_ROTATION_270: 2}.get(ev.rotation)
                if rot is not None:
                    import cv2

                    img = cv2.rotate(img, (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180, cv2.ROTATE_90_COUNTERCLOCKWISE)[rot])
                self.on_frame(np.array(img, copy=True), self.clock())
        except Exception:  # noqa: BLE001
            log.debug("caregiver video ended", exc_info=True)
        finally:
            await stream.aclose()

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self.room is not None:
            try:
                await self.room.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self.room = None
