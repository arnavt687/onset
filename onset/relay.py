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
import logging
from collections.abc import Callable

import numpy as np

log = logging.getLogger("onset.relay")

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
                self.on_status("talking", "")
                self.on_audio_track(track)
            elif track.kind == rtc.TrackKind.KIND_VIDEO:
                self._spawn(self._frames(track))

        @room.on("participant_disconnected")
        def _left(p) -> None:  # noqa: ANN001
            if not room.remote_participants:
                self.on_status("left", "the caregiver stopped talking")

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
