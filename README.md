# Onset

A post-discharge stroke check-in agent you can FaceTime. Onset runs a 60-second scripted check
(smile, arms up, repeat a sentence), compares the results with the person's saved baseline, and
if anything changed or is unclear tells them to call 911 now and texts a caregiver.

> **Not a medical device.** Onset never says "you're fine" and never diagnoses. It is a research
> prototype and must not be relied on by patients until it has been clinically validated.

## How it runs

- **Chert** gives the agent a real Apple line: FaceTime calls reach it through `chert dev`.
- **LiveKit** carries audio and video between Chert and the agent (your own LiveKit Cloud project).
- **agent.py** is a Python LiveKit agent (`livekit-agents` 1.8). It receives the caller's camera,
  sends it back with an overlay, and speaks with ElevenLabs (falls back to Gemini, then OpenAI).

See [ROADMAP.md](ROADMAP.md) for the development map and [CHERT_SUPPORT_REPORT.md](CHERT_SUPPORT_REPORT.md)
for the open audio issue on Chert's side.

## Setup (WSL Ubuntu)

```bash
cd ~/onset
~/.venvs/uv/bin/uv venv --python 3.12 .venv
~/.venvs/uv/bin/uv pip install --python .venv/bin/python -r requirements.txt
cp .env.example .env.local   # then fill in your keys privately
```

## Run

```bash
.venv/bin/python agent.py start     # terminal 1: the agent
tools/chert.sh dev                  # terminal 2: connects it to the Chert line
tools/stop.sh                       # stops both (Ctrl+C releases the line)
```

## Test without FaceTime

```bash
.venv/bin/python selftest.py          # offline video pipeline check
.venv/bin/python tools/fake_call.py   # plays Chert's part in a private LiveKit room
```
