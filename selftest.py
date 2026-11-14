"""Offline check of Onset's video pipeline: no LiveKit room, no keys, no network."""

import numpy as np
from livekit import rtc

import agent

# A 480x640 (portrait) caller frame arriving sideways: 640x480 raw, rotated 90.
raw = np.zeros((480, 640, 4), dtype=np.uint8)
raw[..., 0] = 200
raw[..., 3] = 255
raw[0:20, 0:20, 1] = 255  # a marker in the raw top-left corner
frame = rtc.VideoFrame(640, 480, rtc.VideoBufferType.RGBA, raw.tobytes())
event = rtc.VideoFrameEvent(frame=frame, timestamp_us=0, rotation=rtc.VideoRotation.VIDEO_ROTATION_90)

upright = agent.to_rgba(event)

# The I420 path real calls take: convert the RGBA frame to I420 and back through to_rgba.
i420 = rtc.VideoFrameEvent(frame=frame.convert(rtc.VideoBufferType.I420), timestamp_us=0,
                           rotation=rtc.VideoRotation.VIDEO_ROTATION_0)
assert agent.to_rgba(i420).shape == (480, 640, 4)
assert upright.shape == (640, 480, 4), upright.shape
assert upright[0:20, -20:, 1].min() == 255, "marker should move to the top-right after a 90 degree turn"

out = agent.letterbox(upright, agent.OUT_W, agent.OUT_H)
assert out.shape == (agent.OUT_H, agent.OUT_W, 4)
agent.draw_test_overlay(out, 42, "480x640", 29.9)
assert out[..., 1].max() == 255  # green overlay drawn

source = rtc.VideoSource(agent.OUT_W, agent.OUT_H)
agent.send(source, out)
agent.send(source, agent.placeholder(1))
print("selftest ok:", "upright", upright.shape[1::-1], "->", out.shape[1::-1],
      "| agent name", agent.AGENT_NAME, "| bridge_ready parse",
      agent.bridge_ready('{"chert_bridge_ready":true}'), agent.bridge_ready("{}"))
