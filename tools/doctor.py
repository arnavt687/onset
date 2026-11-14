#!/usr/bin/env python3
"""Is this machine ready to answer a FaceTime call on your Chert line? Checks everything, starts nothing.

    .venv/bin/python tools/doctor.py              every check (talks to LiveKit, ElevenLabs, Chert, NTP)
    .venv/bin/python tools/doctor.py --offline    only what needs no network
    .venv/bin/python tools/doctor.py --warm       also synthesize every scripted line into the TTS cache
    .venv/bin/python tools/doctor.py --json       machine-readable result

`tools/demo.sh --check` runs this; `tools/demo.sh` runs it (with --warm) before starting anything
and stops on a FAIL. Exit status: 1 if any check FAILs, else 0.

Never prints a key, a secret, the Chert account's email or a full phone number: every detail
line is passed through `scrub()`, which removes the values of secret settings, emails and
+phone numbers. The Chert project key is sent only to https://app.trychert.com.

Each check is a small function returning `Check(status, name, detail)` (a tuple), so the
logic is unit-tested with fakes in tests/test_doctor.py.
"""

from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import contextlib
import importlib
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NamedTuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

CHERT_HOST = "app.trychert.com"
ELEVEN_API = "https://api.elevenlabs.io"
MODEL_FILES = ("face_landmarker.task", "pose_landmarker_full.task")
VOICE_KEYS = ("ELEVENLABS_API_KEY", "ELEVEN_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY")
SCRIBE_MODELS = {"scribe_v1", "scribe_v1_experimental", "scribe_v2"}  # ids ElevenLabs documents for /v1/speech-to-text
CAREGIVER_JOIN = ("link", "off")
CLOCK_FAIL_S = 0.25  # Chert declines calls when this computer is further than this from its clock
CLOCK_WARN_S = 0.10
NTP_SERVERS = ("time.cloudflare.com", "time.google.com", "pool.ntp.org")
NTP_DELTA = 2208988800  # seconds from 1900-01-01 (NTP) to 1970-01-01 (Unix)
CLOCK_FIX = ("WSL: `sudo hwclock -s`, or `wsl --shutdown` from Windows and reopen Ubuntu; "
             "Windows: Settings > Time & language > Date & time > Sync now; "
             "Linux: `sudo timedatectl set-ntp true`; macOS: `sudo sntp -sS time.apple.com`")
CHERT_DOCTOR_CHECKS = ("session", "clock", "line", "worker", "line_free", "minutes")

# (url, headers, timeout) -> (status, body bytes). Tests pass a fake.
HttpGet = Callable[[str, Mapping[str, str], float], "tuple[int, bytes]"]


class Check(NamedTuple):
    status: str
    name: str
    detail: str


# ------------------------------------------------------------------------------- helpers

_SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD)", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_PHONE = re.compile(r"\+\d[\d ()-]{5,}\d")
_PLACEHOLDER = re.compile(r"YOUR_|<[^>]*>|^changeme$|^xxx+$", re.I)
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def mask_phone(text: str) -> str:
    """'+1 (555) 123-0001' -> '+1…01'."""
    def one(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        return f"+{digits[:1]}…{digits[-2:]}"
    return _PHONE.sub(one, text)


def scrub(text: str, env: Mapping[str, str] | None = None) -> str:
    """Remove secret values, emails and phone numbers from a line of output."""
    out = str(text)
    for k, v in (env or {}).items():
        if v and len(v) >= 6 and _SECRET_NAME.search(k):
            out = out.replace(v, "***")
    return mask_phone(_EMAIL.sub("<account>", out))


def is_placeholder(value: str | None) -> bool:
    v = (value or "").strip().strip('"').strip("'")
    return not v or bool(_PLACEHOLDER.search(v))


def parse_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines (as python-dotenv reads them; a minimal parser if it is missing)."""
    try:
        from dotenv import dotenv_values

        return {k: (v or "") for k, v in dotenv_values(path).items()}
    except ImportError:
        pass
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.removeprefix("export ").partition("=")
        v = v.strip()
        if v[:1] in "\"'" and v.endswith(v[:1]) and len(v) >= 2:
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].strip()
        out[k.strip()] = v
    return out


def effective_env(file_values: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    """What the agent sees: load_dotenv() never overrides a variable already in the environment."""
    return {**file_values, **{k: v for k, v in environ.items() if v}}


def urllib_get(url: str, headers: Mapping[str, str], timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https hosts
            return r.status, r.read(1_000_000)
    except urllib.error.HTTPError as e:
        return e.code, e.read(100_000) if e.fp else b""


def _json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8", errors="replace"))
    except ValueError:
        return None


# ------------------------------------------------------------------------------ the checks

def check_env_file(path: Path) -> Check:
    if not path.is_file():
        return Check(FAIL, "env file", f"{path.name} is missing: run tools/setup.sh (or cp .env.example .env.local) "
                                       "and fill it in (docs/run-on-your-chert-line.md)")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        return Check(WARN, "env file", f"{path.name} exists but other users can read it (mode {mode:o}): chmod 600 {path.name}")
    return Check(PASS, "env file", f"{path.name} exists (mode {mode:o})")


def check_settings(env: Mapping[str, str]) -> list[Check]:
    """Required names set and not placeholders. Names only; never values."""
    out: list[Check] = []
    lk = [k for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET") if is_placeholder(env.get(k))]
    url = (env.get("LIVEKIT_URL") or "").strip()
    if lk:
        out.append(Check(FAIL, "livekit settings", "missing or placeholder: " + ", ".join(lk)
                         + " (LiveKit Cloud > Settings > API keys)"))
    elif not re.match(r"^wss?://[^/\s]+/?$", url):
        out.append(Check(FAIL, "livekit settings", "LIVEKIT_URL should look like wss://<project>.livekit.cloud"))
    else:
        out.append(Check(PASS, "livekit settings", "LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET set"))

    name = (env.get("AGENT_NAME") or "").strip()
    if is_placeholder(name):
        out.append(Check(FAIL, "agent name", "AGENT_NAME is missing or still onset-YOUR_SUFFIX: pick a name unique "
                                             "to you (e.g. onset-<initials>); chert dev dispatches calls to it"))
    elif not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name):
        out.append(Check(FAIL, "agent name", "AGENT_NAME: letters, digits, - _ . only"))
    else:
        out.append(Check(PASS, "agent name", f"AGENT_NAME is set ({name})"))

    voices = [k for k in VOICE_KEYS if not is_placeholder(env.get(k))]
    if not voices:
        out.append(Check(FAIL, "voice key", "no voice key: set ELEVENLABS_API_KEY (or GOOGLE_API_KEY / OPENAI_API_KEY)"))
    else:
        out.append(Check(PASS, "voice key", "voice from " + voices[0] + (f" (fallbacks: {', '.join(voices[1:])})" if voices[1:] else "")))

    if is_placeholder(env.get("ELEVENLABS_API_KEY")) and is_placeholder(env.get("ELEVEN_API_KEY")):
        out.append(Check(FAIL, "scribe key", "ELEVENLABS_API_KEY is not set: the sentence step (ElevenLabs Scribe) "
                                             "could never be checked, so every call would end UNCLEAR"))
    else:
        out.append(Check(PASS, "scribe key", "ELEVENLABS_API_KEY set (Scribe speech-to-text)"))

    key, project = (env.get("CHERT_API_KEY") or "").strip(), (env.get("CHERT_PROJECT_ID") or "").strip()
    line = (env.get("CHERT_LINE_ID") or "").strip()
    if is_placeholder(key) or is_placeholder(project):
        missing = [n for n, v in (("CHERT_API_KEY", key), ("CHERT_PROJECT_ID", project)) if is_placeholder(v)]
        out.append(Check(WARN, "chert api settings", "not set: " + ", ".join(missing)
                         + ". Needed to text the caregiver (create a key at https://app.trychert.com/developer); "
                           "the line check uses the Chert CLI sign-in without them"))
    elif not key.startswith("ftk_"):
        out.append(Check(FAIL, "chert api settings", "CHERT_API_KEY must be a project API key (starts with ftk_)"))
    elif not _UUID.match(project):
        out.append(Check(FAIL, "chert api settings", "CHERT_PROJECT_ID is not a UUID"))
    elif line and not _UUID.match(line):
        out.append(Check(FAIL, "chert api settings", "CHERT_LINE_ID is set but is not a UUID"))
    else:
        out.append(Check(PASS, "chert api settings", "CHERT_API_KEY and CHERT_PROJECT_ID set"
                         + (", CHERT_LINE_ID pinned" if line else "")))
    return out


PACKAGES = ("livekit.agents", "livekit.api", "livekit.rtc", "livekit.plugins.elevenlabs", "livekit.plugins.google",
            "livekit.plugins.openai", "mediapipe", "cv2", "numpy", "dotenv", "aiohttp")


def check_packages(importer: Callable[[str], Any] = importlib.import_module,
                   version: tuple[int, int] = sys.version_info[:2]) -> Check:
    bad = []
    for mod in PACKAGES:
        try:
            importer(mod)
        except Exception as e:  # noqa: BLE001
            bad.append(f"{mod} ({type(e).__name__})")
    py = f"Python {version[0]}.{version[1]}"
    if bad:
        return Check(FAIL, "python packages", f"{py}; cannot import: {', '.join(bad)}. Run tools/setup.sh")
    if tuple(version) != (3, 12):
        return Check(WARN, "python packages", f"all import, but on {py}; Onset is tested on 3.12 (tools/setup.sh)")
    return Check(PASS, "python packages", f"{py}; livekit-agents, plugins, mediapipe, opencv import")


def models_dir(env: Mapping[str, str], root: Path = ROOT) -> Path:
    return Path(env["ONSET_MODELS"]).expanduser() if env.get("ONSET_MODELS") else root / "models"


@contextlib.contextmanager
def _quiet_stderr():
    """MediaPipe's native code logs straight to fd 2; hide that noise while loading."""
    try:
        sys.stderr.flush()
        saved, devnull = os.dup(2), os.open(os.devnull, os.O_WRONLY)
    except OSError:
        yield
        return
    os.dup2(devnull, 2)
    try:
        yield
    finally:
        os.dup2(saved, 2)
        os.close(saved)
        os.close(devnull)


def _load_model(path: Path) -> None:
    with _quiet_stderr():
        _load_model_noisy(path)


def _load_model_noisy(path: Path) -> None:
    from mediapipe.tasks.python import BaseOptions, vision

    if "face" in path.name:
        vision.FaceLandmarker.create_from_options(
            vision.FaceLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(path)))).close()
    else:
        vision.PoseLandmarker.create_from_options(
            vision.PoseLandmarkerOptions(base_options=BaseOptions(model_asset_path=str(path)))).close()


def check_models(directory: Path, loader: Callable[[Path], None] = _load_model) -> Check:
    missing = [n for n in MODEL_FILES if not (directory / n).is_file() or (directory / n).stat().st_size == 0]
    if missing:
        return Check(FAIL, "mediapipe models", f"missing in {directory}: {', '.join(missing)} (tools/setup.sh downloads them)")
    for n in MODEL_FILES:
        try:
            loader(directory / n)
        except Exception as e:  # noqa: BLE001
            return Check(FAIL, "mediapipe models", f"{n} does not load ({type(e).__name__}: {str(e)[:120]}); "
                                                   "delete it and run tools/setup.sh again")
    return Check(PASS, "mediapipe models", f"both models load from {directory}")


# ---- voice

def candidate_sentences(profiles: Any = None, baseline_sentence: Callable[[str], str | None] | None = None) -> list[str]:
    """Every sentence a call could ask for: the defaults, each profile's, each saved baseline's."""
    from onset.speech import SENTENCES

    out = list(SENTENCES)
    for p in getattr(profiles, "patients", ()) or ():
        for s in (p.sentence, baseline_sentence(p.id) if baseline_sentence else None):
            if s and s not in out:
                out.append(s)
    return out


def warm_texts(sentences: Iterable[str]) -> list[str]:
    """Every spoken line for calls with these sentences, greeting first, no duplicates."""
    from onset import script

    out: list[str] = []
    for s in sentences:
        for t in script.all_spoken(s):
            if t not in out:
                out.append(t)
    return out


def _real_tts():
    import agent  # noqa: PLC0415 - loads .env.local and builds the same voice the agent uses

    return agent.make_tts()


async def _synth_all(tts, texts: list[str], timeout: float) -> tuple[int, int, float, list[str]]:
    """(newly synthesized, already cached, seconds, failures) for `texts` through onset.voice's cache."""
    from livekit.agents.utils import http_context
    from onset import voice

    ident = voice.tts_identity(tts)
    new = cached = 0
    failed: list[str] = []
    t0 = time.monotonic()
    async with http_context.open():
        for text in texts:
            if voice.cache_path(ident, text).is_file():
                cached += 1
                continue
            try:
                await asyncio.wait_for(voice.synthesize(tts, text), timeout)
                new += 1
            except Exception as e:  # noqa: BLE001
                failed.append(f"{text[:30]!r}: {type(e).__name__}")
        try:
            await tts.aclose()
        except Exception:  # noqa: BLE001
            pass
    return new, cached, time.monotonic() - t0, failed


def check_tts(texts: list[str], *, offline: bool, warm: bool, make_tts: Callable[[], Any] = _real_tts,
              synth: Callable[..., Any] = _synth_all, timeout: float = 30.0) -> Check:
    """The greeting must be in the TTS cache: Chert's bridge_ready deadline is 10 s after the call is answered."""
    from onset import voice

    name = "voice (tts)"
    try:
        tts = make_tts()
    except Exception as e:  # noqa: BLE001
        return Check(FAIL, name, f"cannot build the voice ({type(e).__name__}: {str(e)[:120]})")
    parts = type(tts).__module__.split(".")
    engine = parts[2] if parts[:2] == ["livekit", "plugins"] and len(parts) > 2 else type(tts).__name__
    ident = voice.tts_identity(tts)
    greeting = texts[0]
    if offline:
        if voice.cache_path(ident, greeting).is_file():
            return Check(PASS, name, f"{engine}: the greeting is cached (offline: engine not called)")
        return Check(WARN, name, f"{engine}: the greeting is not cached yet; run without --offline (or with --warm)")
    todo = texts if warm else [greeting]
    try:
        new, cached, secs, failed = asyncio.run(synth(tts, todo, timeout))
    except Exception as e:  # noqa: BLE001
        return Check(FAIL, name, f"{engine}: synthesis failed ({type(e).__name__}: {str(e)[:160]})")
    if failed:
        status = WARN if voice.cache_path(ident, greeting).is_file() else FAIL
        return Check(status, name, f"{engine}: {len(failed)} of {len(todo)} lines failed: {'; '.join(failed[:3])}")
    what = f"all {len(todo)} scripted lines" if warm else "the greeting"
    return Check(PASS, name, f"{engine}: {what} cached ({new} synthesized now in {secs:.1f} s, {cached} already cached)")


def check_elevenlabs(env: Mapping[str, str], http: HttpGet = urllib_get, stt_model: str | None = None) -> Check:
    """The key is valid and the Scribe model the sentence check uses exists. Free endpoints only."""
    name = "elevenlabs key"
    key = (env.get("ELEVENLABS_API_KEY") or env.get("ELEVEN_API_KEY") or "").strip()
    if is_placeholder(key):
        return Check(SKIP, name, "no ELEVENLABS_API_KEY")
    if stt_model is None:
        stt_model = env.get("ONSET_STT_MODEL") or _default_stt_model()
    headers = {"xi-api-key": key, "accept": "application/json"}
    try:
        status, body = http(f"{ELEVEN_API}/v1/models", headers, 10.0)
    except OSError as e:
        return Check(WARN, name, f"could not reach api.elevenlabs.io ({type(e).__name__})")
    data = _json(body)
    if status == 401:
        detail = data.get("detail") if isinstance(data, dict) else None
        code = detail.get("status") if isinstance(detail, dict) else ""
        if code == "missing_permissions":
            return Check(WARN, name, "the key works but may not list models (restricted key); "
                                     "give it Text to Speech, Speech to Text and Models read access")
        return Check(FAIL, name, f"ElevenLabs rejected the key (HTTP 401{', ' + code if code else ''})")
    if status != 200 or not isinstance(data, list):
        return Check(WARN, name, f"GET /v1/models answered HTTP {status}; could not verify the key")
    ids = {m.get("model_id") for m in data if isinstance(m, dict)}
    tts_model = env.get("ELEVEN_MODEL") or "eleven_turbo_v2_5"
    notes = []
    if tts_model not in ids:
        notes.append(f"voice model {tts_model} is not offered to this key (ELEVEN_MODEL)")
    if stt_model not in ids and stt_model not in SCRIBE_MODELS:
        notes.append(f"Scribe model {stt_model!r} is not a known id ({', '.join(sorted(SCRIBE_MODELS))}); check ONSET_STT_MODEL")
    if notes:
        return Check(WARN, name, "key valid; " + "; ".join(notes))
    return Check(PASS, name, f"key valid; voice model {tts_model} and Scribe model {stt_model} available")


def _default_stt_model() -> str:
    try:
        from onset.speech import DEFAULT_MODEL

        return DEFAULT_MODEL
    except Exception:  # noqa: BLE001
        return "scribe_v2"


async def _list_rooms(url: str, key: str, secret: str) -> int:
    from livekit import api

    lk = api.LiveKitAPI(url, key, secret)
    try:
        rooms = await asyncio.wait_for(lk.room.list_rooms(api.ListRoomsRequest()), 10)
        return len(rooms.rooms)
    finally:
        await lk.aclose()


def check_livekit(env: Mapping[str, str], lister: Callable[[str, str, str], Any] = _list_rooms) -> Check:
    name = "livekit reachable"
    url, key, secret = (env.get(k, "").strip() for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if any(is_placeholder(v) for v in (url, key, secret)):
        return Check(SKIP, name, "LiveKit settings are missing")
    http_url = re.sub(r"^ws", "http", url)
    try:
        n = asyncio.run(lister(http_url, key, secret))
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:160]
        hint = " (wrong key or secret?)" if re.search(r"401|unauth", msg, re.I) else ""
        return Check(FAIL, name, f"list_rooms failed: {type(e).__name__}: {msg}{hint}")
    return Check(PASS, name, f"the keys work ({n} room{'s' if n != 1 else ''} open now)")


# ---- Chert

def parse_chert_doctor(text: str) -> dict[str, dict]:
    """`chert doctor --json` prints JSON records with a `checks` list; name -> check."""
    checks: dict[str, dict] = {}
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            for c in rec.get("checks") or []:
                if isinstance(c, dict) and c.get("name"):
                    checks[c["name"]] = c
    return checks


def line_number(checks: Mapping[str, dict]) -> str:
    m = re.search(r"\+?\d[\d ()-]{7,}\d", str(checks.get("line", {}).get("detail", "")))
    return m.group(0) if m else ""


def chert_checks(checks: Mapping[str, dict]) -> list[Check]:
    """The Chert CLI's own checks that matter here, as doctor Checks (no account email, phones masked)."""
    if not checks:
        return [Check(FAIL, "chert cli", "`tools/chert.sh doctor --json` gave no checks (is Node >= 20 installed? "
                                         "signed in? run `tools/chert.sh doctor` to see why)")]
    status_map = {"pass": PASS, "warn": WARN, "fail": FAIL, "skip": SKIP}
    out = []
    for name in CHERT_DOCTOR_CHECKS:
        c = checks.get(name)
        if not c:
            continue
        st = status_map.get(str(c.get("status")).lower(), WARN)
        detail = str(c.get("detail") or "")
        if name == "session":
            detail = "signed in" if st == PASS else detail
            if st != PASS:
                detail += " -> run `tools/chert.sh login`"
        fix = c.get("fix")
        if fix and st != PASS:
            detail += f" -> {fix}"
        if name == "clock" and st != PASS:
            detail += f" ({CLOCK_FIX}). Chert's reading is coarse; the clock (ntp) line has the precise offset"
        out.append(Check(st, f"chert {name}", detail))
    return out


def run_chert_doctor(root: Path = ROOT, timeout: float = 90.0) -> str:
    r = subprocess.run(["bash", str(root / "tools" / "chert.sh"), "doctor", "--json"], cwd=root,
                       capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    return r.stdout


def video_framing(label: str, framing: Any, out_size: tuple[int, int] | None) -> Check | None:
    """A line set to "fill" crops Onset's picture to the caller's upright window (docs/video-framing.md)."""
    if not isinstance(framing, str) or not framing:
        return None
    size = f"{out_size[0]}x{out_size[1]}" if out_size else "?"
    if framing == "fit":
        return Check(PASS, "video framing", f"{label}: video_framing fit, the caller sees the whole picture")
    if framing != "fill":
        return Check(WARN, "video framing", f"{label}: video_framing {framing!r} is new to Onset: check the captions "
                                            "on a test call")
    if out_size and out_size[1] > out_size[0]:
        return Check(PASS, "video framing", f"{label}: video_framing fill, and Onset sends an upright {size} picture "
                                            "with the captions inside the safe area")
    return Check(FAIL, "video framing", f"{label}: video_framing fill crops a wide picture to its middle third, and "
                                        f"Onset is set to send {size}: the captions will be cut off. Remove "
                                        "ONSET_FRAME=landscape from .env.local (see docs/video-framing.md)")


def _out_size(env: Mapping[str, str]) -> tuple[int, int] | None:
    try:
        from onset import overlay

        return overlay.output_size(env)
    except Exception:  # noqa: BLE001 - the doctor still runs without the agent's packages
        return None


def line_readiness(body: Any, line_id: str | None = None, out_size: tuple[int, int] | None = None) -> list[Check]:
    """Checks from GET /api/v1/lines: audio_ready false means silent calls both ways (docs/chert-audio.md)."""
    data = body.get("data", body) if isinstance(body, dict) else body
    items = data.get("items") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return [Check(WARN, "line readiness", "unexpected answer from GET /api/v1/lines")]
    lines = [ln for ln in items if isinstance(ln, dict)]
    if line_id:
        lines = [ln for ln in lines if ln.get("id") == line_id]
        if not lines:
            return [Check(FAIL, "line readiness", "CHERT_LINE_ID is not one of this project's lines")]
    if not lines:
        return [Check(FAIL, "line readiness", "the project has no line yet (run `tools/chert.sh onboard`)")]
    out = []
    for ln in lines:
        health = ln.get("session_health") or {}
        rd = health.get("readiness") or {}
        label = mask_phone(str(ln.get("display_name") or "line")) + f" ({str(ln.get('id', ''))[:8]})"
        facts = ", ".join(f"{k} {json.dumps(rd.get(k))}" for k in ("audio_ready", "video_ready", "facetime_signed_in"))
        facts += f", worker {health.get('status', '?')}"
        if rd.get("audio_ready") is not True:
            out.append(Check(FAIL, "line readiness", f"{label}: {facts}. Calls will be silent both ways: only Chert "
                                                     "can fix the Mac's audio (see docs/chert-audio.md)"))
        elif rd.get("video_ready") is not True:
            out.append(Check(WARN, "line readiness", f"{label}: {facts}. The caller's camera may not reach Onset"))
        else:
            out.append(Check(PASS, "line readiness", f"{label}: {facts}"))
        framing = video_framing(label, ln.get("video_framing"), out_size)
        if framing:
            out.append(framing)
    if len(lines) > 1 and not line_id:
        out.append(Check(WARN, "line readiness", f"{len(lines)} lines in the project: set CHERT_LINE_ID to the one Onset uses"))
    return out


def cli_session(env: Mapping[str, str]) -> tuple[str, str] | None:
    """(access token, project id) from the Chert CLI's sign-in (~/.chert/credentials.json), if any."""
    home = Path(env.get("CHERT_HOME") or Path.home() / ".chert").expanduser()
    try:
        data = json.loads((home / "credentials.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    token, project = data.get("access_token"), data.get("project_id")
    if isinstance(token, str) and len(token) > 20 and isinstance(project, str) and _UUID.match(project):
        return token, project
    return None


def check_line_readiness(env: Mapping[str, str], http: HttpGet = urllib_get,
                         session: Callable[[Mapping[str, str]], tuple[str, str] | None] = cli_session) -> list[Check]:
    """Uses the project key; without one, the Chert CLI's own sign-in (as tools/chert_line_health.mjs does)."""
    key, project = (env.get("CHERT_API_KEY") or "").strip(), (env.get("CHERT_PROJECT_ID") or "").strip()
    via = ""
    if is_placeholder(key) or is_placeholder(project) or not key.startswith("ftk_") or not _UUID.match(project):
        s = session(env)
        if not s:
            return [Check(SKIP, "line readiness", "needs CHERT_API_KEY and CHERT_PROJECT_ID, or a Chert CLI "
                                                  "sign-in (`tools/chert.sh login`)")]
        key, project, via = s[0], (project if _UUID.match(project) else s[1]), " (via the CLI sign-in)"
    url = f"https://{CHERT_HOST}/api/v1/lines?limit=100"
    if urllib.parse.urlsplit(url).hostname != CHERT_HOST:  # the key goes nowhere else
        raise RuntimeError("refusing to send the Chert key off app.trychert.com")
    try:
        status, body = http(url, {"Authorization": f"Bearer {key}", "X-Chert-Project-Id": project,
                                  "Accept": "application/json"}, 10.0)
    except OSError as e:
        return [Check(WARN, "line readiness", f"could not reach {CHERT_HOST} ({type(e).__name__})")]
    if status in (401, 403):
        return [Check(FAIL, "line readiness", f"Chert rejected the project key (HTTP {status}): check CHERT_API_KEY "
                                              "and CHERT_PROJECT_ID belong together")]
    if status != 200:
        return [Check(WARN, "line readiness", f"GET /api/v1/lines answered HTTP {status}")]
    line = (env.get("CHERT_LINE_ID") or "").strip() or None
    return [Check(c.status, c.name, c.detail + via) for c in line_readiness(_json(body), line, _out_size(env))]


# ---- clock

def to_ntp(t: float) -> bytes:
    sec = int(t) + NTP_DELTA
    frac = int((t - int(t)) * 2**32)
    return struct.pack("!II", sec & 0xFFFFFFFF, frac & 0xFFFFFFFF)


def from_ntp(b: bytes) -> float:
    sec, frac = struct.unpack("!II", b)
    return sec - NTP_DELTA + frac / 2**32


def sntp_request(t1: float) -> bytes:
    """48-byte SNTP v4 client request with our transmit time in it (echoed back as 'originate')."""
    return b"\x23" + b"\x00" * 39 + to_ntp(t1)


def sntp_offset(reply: bytes, sent_ts: bytes, t1: float, t4: float) -> tuple[float, float]:
    """(offset, round-trip delay) in seconds; offset > 0 means this computer is behind the server."""
    if len(reply) < 48:
        raise ValueError("short NTP reply")
    if reply[0] & 0x7 != 4 or reply[1] == 0:
        raise ValueError("not a usable server reply (mode/stratum)")
    if reply[24:32] != sent_ts:
        raise ValueError("NTP reply does not answer our request")
    t2, t3 = from_ntp(reply[32:40]), from_ntp(reply[40:48])
    return ((t2 - t1) + (t3 - t4)) / 2, (t4 - t1) - (t3 - t2)


def query_sntp(host: str, *, samples: int = 3, timeout: float = 2.0,
               sock_factory: Callable[[], Any] | None = None, clock: Callable[[], float] = time.time) -> tuple[float, float]:
    """Best (lowest-delay) offset of a few SNTP samples. Raises OSError/ValueError when none worked."""
    best: tuple[float, float] | None = None
    err: Exception | None = None
    for _ in range(samples):
        sock = sock_factory() if sock_factory else socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            t1 = clock()
            req = sntp_request(t1)
            sock.sendto(req, (host, 123))
            reply, _ = sock.recvfrom(512)
            t4 = clock()
            off = sntp_offset(reply, req[40:48], t1, t4)
            if best is None or off[1] < best[1]:
                best = off
        except (OSError, ValueError) as e:
            err = e
        finally:
            sock.close()
    if best is None:
        raise err or OSError("no NTP reply")
    return best


def check_clock(query: Callable[[str], tuple[float, float]] = query_sntp, servers: Iterable[str] = NTP_SERVERS) -> Check:
    name = "clock (ntp)"
    errors = []
    for host in servers:
        try:
            offset, delay = query(host)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{host}: {type(e).__name__}")
            continue
        side = "behind" if offset > 0 else "ahead of"
        what = f"this computer is {abs(offset):.3f} s {side} {host} (round trip {delay * 1000:.0f} ms)"
        if abs(offset) > CLOCK_FAIL_S:
            return Check(FAIL, name, f"{what}; Chert declines calls beyond {CLOCK_FAIL_S} s. Fix: {CLOCK_FIX}")
        if abs(offset) > CLOCK_WARN_S:
            return Check(WARN, name, f"{what}; close to Chert's {CLOCK_FAIL_S} s limit. Fix: {CLOCK_FIX}")
        return Check(PASS, name, what)
    return Check(WARN, name, "no NTP server answered (UDP 123 blocked?): " + ", ".join(errors)
                 + "; the chert clock check is the one Chert enforces")


# ---- profiles, safety gate, escalation, port

def check_profiles(env: Mapping[str, str], root: Path = ROOT, loader: Callable[..., Any] | None = None) -> list[Check]:
    from onset import patients

    loader = loader or patients.load_profiles
    p = env.get("ONSET_PATIENTS_FILE")
    path = (Path(p).expanduser() if Path(p).expanduser().is_absolute() else root / p) if p else None
    try:
        prof = loader(path, env)
    except patients.ProfileError as e:
        return [Check(FAIL, "patient profiles", str(e))]
    src = "the environment (ONSET_PATIENT_*)" if prof.source == "env" else prof.source
    out = [Check(PASS, "patient profiles", f"{len(prof.patients)} profile{'s' if len(prof.patients) != 1 else ''} "
                                           f"from {src}; calls use {prof.default or prof.patients[0].id!r}")]
    if len(prof.patients) > 1 and not prof.default:
        out[0] = Check(WARN, "patient profiles", f"{len(prof.patients)} profiles but no \"default\": Chert does not "
                       "pass the caller's phone yet, so calls would be anonymous and never saved as a baseline. "
                       "Set \"default\" (one patient per line for now)")
    elif len(prof.patients) > 1:
        out.append(Check(WARN, "patient profiles", f"one patient per line for now: Chert does not pass the caller's "
                                                   f"phone, so every call uses {prof.default!r}"))
    no_cg = [x.id for x in prof.patients if not x.caregiver.phone]
    if no_cg:
        out.append(Check(WARN, "caregiver phone", f"no caregiver phone for {', '.join(no_cg)}: nobody is texted or "
                                                  "invited on a change (ONSET_CAREGIVER_PHONE or the profile's caregiver.phone)"))
    return out


def check_gate(env: Mapping[str, str]) -> Check:
    from onset import actions

    deny = (env.get("ONSET_LIVE_ACTIONS_DENY") or "").strip()
    if not actions.live_actions_enabled(env):
        return Check(PASS, "safety gate", "DRY RUN: caregiver texts are logged and shown on the dashboard, nothing is "
                                          "sent (ONSET_LIVE_ACTIONS=1 to go live)")
    from onset import chert_api

    try:
        chert_api.load_config({k: v for k, v in env.items() if k.startswith("CHERT_")})
    except chert_api.ConfigError as e:
        return Check(FAIL, "safety gate", f"ONSET_LIVE_ACTIONS=1 but the Chert API is not configured: {e.code}")
    return Check(WARN, "safety gate", "LIVE: real iMessages go to caregivers on ESCALATE/UNCLEAR"
                 + (f" (kept dry: {deny})" if deny else "") + ". Each caregiver must have texted `join <code>` to the line "
                 "(docs/chert-messaging.md)")


def check_escalation(env: Mapping[str, str]) -> list[Check]:
    out: list[Check] = []
    join = (env.get("ONSET_CAREGIVER_JOIN") or "link").strip().lower()
    if join not in CAREGIVER_JOIN:
        extra = (" Chert cannot add anyone to a live FaceTime call, so facetime/both are not supported."
                 if join in ("facetime", "both") else "")
        return [Check(FAIL, "escalation", f"ONSET_CAREGIVER_JOIN={join!r}: use link or off.{extra}")]
    ttl = (env.get("ONSET_CAREGIVER_LINK_TTL_S") or "900").strip()
    if not ttl.isdigit() or not 60 <= int(ttl) <= 86400:
        out.append(Check(FAIL, "escalation", "ONSET_CAREGIVER_LINK_TTL_S must be whole seconds between 60 and 86400"))
    page = (env.get("ONSET_CAREGIVER_PAGE") or "").strip()
    talk = (env.get("ONSET_CAREGIVER_TALK") or "1").strip() == "1"
    if join == "off":
        out.append(Check(PASS, "escalation", "off: the caregiver gets the summary iMessage only"))
        return out
    if page and not page.startswith("https://"):
        out.append(Check(FAIL, "escalation", "ONSET_CAREGIVER_PAGE must be an https:// URL (the page reads tokens "
                                             "from the #fragment)"))
    elif talk and not page:
        out.append(Check(WARN, "escalation", "link: caregiver gets a watch-only LiveKit Meet link. Talking needs the "
                                             "caregiver page: host web/caregiver/ and set ONSET_CAREGIVER_PAGE"))
    else:
        out.append(Check(PASS, "escalation", "link: the caregiver gets the summary and a link to watch"
                         + (" and talk (Onset relays them)" if talk and page else "") + f"; link valid {ttl} s"))
    return out


def check_caregiver_page(env: Mapping[str, str], http: HttpGet = urllib_get) -> Check:
    page = (env.get("ONSET_CAREGIVER_PAGE") or "").strip()
    if (env.get("ONSET_CAREGIVER_JOIN") or "link").strip().lower() != "link" or not page.startswith("https://"):
        return Check(SKIP, "caregiver page", "no ONSET_CAREGIVER_PAGE to fetch")
    base = page.split("#", 1)[0]
    try:
        status, _ = http(base, {"Accept": "text/html"}, 10.0)
    except Exception as e:  # noqa: BLE001
        return Check(FAIL, "caregiver page", f"cannot fetch ONSET_CAREGIVER_PAGE ({type(e).__name__})")
    if status != 200:
        return Check(FAIL, "caregiver page", f"ONSET_CAREGIVER_PAGE answered HTTP {status} (is it published?)")
    return Check(PASS, "caregiver page", "ONSET_CAREGIVER_PAGE answers 200")


def check_port(port: int, host: str = "127.0.0.1") -> Check:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind((host, port))
    except OSError:
        return Check(WARN, "dashboard port", f"port {port} is in use (a dashboard from an earlier run? "
                                             "pkill -f tools/dashboard.py, or tools/demo.sh --port N)")
    finally:
        s.close()
    return Check(PASS, "dashboard port", f"port {port} is free")


# ------------------------------------------------------------------------------ running

def _guard(fn: Callable[[], Check | list[Check]], name: str) -> list[Check]:
    try:
        r = fn()
    except Exception as e:  # noqa: BLE001 - one broken check never hides the others
        return [Check(FAIL, name, f"the check itself failed: {type(e).__name__}: {str(e)[:160]}")]
    return list(r) if isinstance(r, list) else [r]


def run_checks(args: argparse.Namespace, env: dict[str, str], root: Path = ROOT) -> tuple[list[Check], dict]:
    facts: dict[str, Any] = {}
    out: list[Check] = []
    env_path = Path(args.env_file)
    out += _guard(lambda: check_env_file(env_path), "env file")
    out += _guard(lambda: check_settings(env), "settings")
    out += _guard(check_packages, "python packages")
    out += _guard(lambda: check_profiles(env, root), "patient profiles")
    out += _guard(lambda: check_gate(env), "safety gate")
    out += _guard(lambda: check_escalation(env), "escalation")
    out += _guard(lambda: check_port(args.port), "dashboard port")

    net: dict[str, concurrent.futures.Future] = {}
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
    if not args.offline:
        def chert() -> list[Check]:
            checks = parse_chert_doctor(run_chert_doctor(root))
            facts["line_number"] = line_number(checks)
            return chert_checks(checks)
        net["chert cli"] = pool.submit(_guard, chert, "chert cli")
        net["line readiness"] = pool.submit(_guard, lambda: check_line_readiness(env), "line readiness")
        net["clock (ntp)"] = pool.submit(_guard, check_clock, "clock (ntp)")
        net["livekit reachable"] = pool.submit(_guard, lambda: check_livekit(env), "livekit reachable")
        net["elevenlabs key"] = pool.submit(_guard, lambda: check_elevenlabs(env), "elevenlabs key")
        net["caregiver page"] = pool.submit(_guard, lambda: check_caregiver_page(env), "caregiver page")

    out += _guard(lambda: check_models(models_dir(env, root)), "mediapipe models")

    def tts() -> Check:
        from onset import decide, patients

        try:
            prof = patients.load_profiles(None, env)
        except patients.ProfileError:
            prof = None

        def base_sentence(pid: str) -> str | None:
            try:
                b = decide.load_baseline(pid)
                return b.sentence if b else None
            except Exception:  # noqa: BLE001
                return None
        return check_tts(warm_texts(candidate_sentences(prof, base_sentence)), offline=args.offline, warm=args.warm)
    out += _guard(tts, "voice (tts)")

    if args.offline:
        out += [Check(SKIP, n, "offline") for n in ("livekit reachable", "elevenlabs key", "chert cli",
                                                     "line readiness", "clock (ntp)", "caregiver page")]
    for f in net.values():
        out += f.result()
    pool.shutdown(wait=True)

    if args.skip_clock:
        out = [Check(WARN, c.name, c.detail + " (--skip-clock: not stopping)")
               if c.status == FAIL and c.name in ("clock (ntp)", "chert clock") else c for c in out]
    return [Check(c.status, c.name, scrub(c.detail, env)) for c in out], facts


def render(checks: list[Check], color: bool) -> str:
    colors = {PASS: "32", WARN: "33", FAIL: "31", SKIP: "90"}
    width = max((len(c.name) for c in checks), default=10)
    lines = []
    for c in checks:
        tag = f"\033[{colors[c.status]}m{c.status}\033[0m" if color else c.status
        lines.append(f"{tag}  {c.name.ljust(width)}  {c.detail}")
    counts = summary(checks)
    lines.append(f"{counts[PASS]} pass, {counts[WARN]} warn, {counts[FAIL]} fail, {counts[SKIP]} skip"
                 + ("  -> fix the FAIL lines, then run again" if counts[FAIL] else "  -> ready"))
    return "\n".join(lines)


def summary(checks: Iterable[Check]) -> dict[str, int]:
    counts = {PASS: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for c in checks:
        counts[c.status] += 1
    return counts


def to_json(checks: list[Check]) -> str:
    counts = summary(checks)
    return json.dumps({"ok": counts[FAIL] == 0, "summary": {k.lower(): v for k, v in counts.items()},
                       "checks": [c._asdict() for c in checks]}, indent=2)


def exit_code(checks: Iterable[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--offline", action="store_true", help="skip every check that needs the network")
    ap.add_argument("--warm", action="store_true", help="synthesize every scripted line into the TTS cache")
    ap.add_argument("--json", action="store_true", help="print JSON instead of text")
    ap.add_argument("--port", type=int, default=8765, help="dashboard port to check (default 8765)")
    ap.add_argument("--skip-clock", action="store_true", help="report clock problems as WARN, not FAIL")
    ap.add_argument("--env-file", default=str(ROOT / ".env.local"), help="settings file (default: .env.local)")
    ap.add_argument("--line-file", help="write the line's full phone number here (owner-only), for tools/demo.sh")
    args = ap.parse_args(argv)

    os.chdir(ROOT)  # baselines/, runs/ and config/ are relative to the repo, as for the agent
    env_path = Path(args.env_file)
    file_values = parse_env_file(env_path) if env_path.is_file() else {}
    for k, v in file_values.items():  # the agent's view (load_dotenv never overrides)
        if v:
            os.environ.setdefault(k, v)
    env = effective_env(file_values, os.environ)

    checks, facts = run_checks(args, env)
    if args.line_file and facts.get("line_number"):
        fd = os.open(args.line_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(facts["line_number"] + "\n")
    print(to_json(checks) if args.json else render(checks, sys.stdout.isatty()))
    return exit_code(checks)


if __name__ == "__main__":
    sys.exit(main())
