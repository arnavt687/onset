"""Piece 3: sentence repetition (ElevenLabs Scribe speech-to-text, then plain comparison).

Owner: the `speech` session. See ROADMAP.md, piece 3.

`check_sentence` never raises into the call: every failure becomes a `SpeechResult` whose
quality is not OK, which the flow treats as "could not tell" (UNCLEAR).

What goes into `detail` (all plain JSON values, safe to log; never the API key):
- `audio`: duration, levels (dBFS), clipping fraction, `too_quiet` / `clipped` flags
- `model`, `latency_s`: which Scribe model answered and how long the request took
- `words`: the heard words with start/end seconds and logprob
- `alignment`: per expected word, what was heard (ok / sub / del / ins)
- `fillers`, `audio_events`: "um"/"uh" and "(mumbling)"-style tags that were dropped before WER
- `mean_logprob`, `min_logprob`: Scribe's confidence; low values hint that it guessed
- `onset_delay_s`: seconds from the start of the clip to the first word
- `autocorrect_note`: what we know about Scribe pulling slurred speech toward real words
"""

from __future__ import annotations

import asyncio
import io
import math
import os
import re
import time
import wave
from pathlib import Path

import aiohttp
import numpy as np

from onset.types import AudioClip, Quality, SpeechResult

# Short, plain, standard-style sentences. The flow picks one; the baseline stores which.
SENTENCES = [
    "You can't teach an old dog new tricks.",
    "The sky is blue in Cincinnati.",
    "It is a sunny day today.",
]

STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
DEFAULT_MODEL = os.environ.get("ONSET_STT_MODEL", "scribe_v2")
REQUEST_TIMEOUT_S = 15.0

# Audio gates. Levels are dBFS of 16-bit PCM (0 dBFS = full scale).
MIN_SPEECH_S = 0.3  # shorter than this cannot hold a sentence
SILENCE_DBFS = -55.0  # a 20 ms window quieter than this is silence
QUIET_DBFS = -40.0  # speech (loud windows) quieter than this is "too quiet"
CLIP_LEVEL = 32000  # |sample| at or above this counts as clipped
CLIP_FRACTION = 0.005  # more than 0.5% clipped samples makes the audio "clipped"

# Recorded from the integration run; see notes/speech.md.
AUTOCORRECT_NOTE = (
    "Scribe is a language-model ASR: on garbled or slurred input it tends to output the "
    "nearest real words, so a low WER does not prove clear speech. Use min_logprob and "
    "words_per_minute/longest_pause_s alongside WER. No API option disables this; "
    "temperature=0 and tag_audio_events=true are set, and no_verbatim is left off."
)

FILLERS = {"um", "uh", "er", "erm", "ah", "hmm", "mm", "uhm", "umm"}


# ---------------------------------------------------------------------------- normalization

_CONTRACTIONS = {
    "can't": "cannot",
    "won't": "will not",
    "shan't": "shall not",
    "ain't": "is not",
    "let's": "let us",
    "it's": "it is",
    "that's": "that is",
    "there's": "there is",
    "here's": "here is",
    "what's": "what is",
    "who's": "who is",
    "where's": "where is",
    "he's": "he is",
    "she's": "she is",
    "y'all": "you all",
    "o'clock": "oclock",
}
_SUFFIXES = [  # applied after the whole-word table
    ("n't", " not"),
    ("'re", " are"),
    ("'ll", " will"),
    ("'ve", " have"),
    ("'m", " am"),
    ("'d", " would"),
]

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()


def number_to_words(n: int) -> str:
    """Cardinal number in plain words, e.g. 42 -> 'forty two' (no hyphens, no 'and')."""
    if n < 0:
        return "minus " + number_to_words(-n)
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + ("" if ones == 0 else " " + _ONES[ones])
    for size, name in ((10**9, "billion"), (10**6, "million"), (1000, "thousand"), (100, "hundred")):
        if n >= size:
            head, rest = divmod(n, size)
            return number_to_words(head) + " " + name + ("" if rest == 0 else " " + number_to_words(rest))
    raise AssertionError("unreachable")


def _digits_to_words(match: re.Match) -> str:
    text = match.group(0).replace(",", "")
    if "." in text:
        whole, frac = text.split(".", 1)
        return number_to_words(int(whole)) + " point " + " ".join(_ONES[int(d)] for d in frac)
    return number_to_words(int(text))


def normalize_words(text: str) -> list[str]:
    """Lowercase words with contractions expanded, numbers spelled out and punctuation removed."""
    text = text.lower().replace("’", "'").replace("‘", "'")
    text = re.sub(r"\([^)]*\)|\[[^\]]*\]", " ", text)  # audio-event tags like (laughter)
    text = re.sub(r"\d[\d,]*(?:\.\d+)?", lambda m: " " + _digits_to_words(m) + " ", text)
    text = re.sub(r"[-–—/]", " ", text)  # forty-two -> forty two
    out: list[str] = []
    for token in text.split():
        token = re.sub(r"^[^\w']+|[^\w']+$", "", token).strip("'")
        if not token:
            continue
        if token in _CONTRACTIONS:
            token = _CONTRACTIONS[token]
        else:
            for suffix, repl in _SUFFIXES:
                if token.endswith(suffix) and len(token) > len(suffix):
                    token = token[: -len(suffix)] + repl
                    break
        token = re.sub(r"[^\w ]", "", token)  # possessive 's and anything left
        out.extend(token.split())
    return out


# ---------------------------------------------------------------------------- word error rate


def align_words(expected: list[str], heard: list[str]) -> list[tuple[str, str | None, str | None]]:
    """Levenshtein alignment: list of (op, expected_word, heard_word), op in ok/sub/del/ins."""
    n, m = len(expected), len(heard)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = 0 if expected[i - 1] == heard[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
    ops: list[tuple[str, str | None, str | None]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (expected[i - 1] != heard[j - 1]):
            ops.append(("ok" if expected[i - 1] == heard[j - 1] else "sub", expected[i - 1], heard[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            ops.append(("del", expected[i - 1], None))
            i -= 1
        else:
            ops.append(("ins", None, heard[j - 1]))
            j -= 1
    ops.reverse()
    return ops


def word_error_rate(expected: str, heard: str) -> float:
    """Word-level edit distance divided by the number of expected words, ignoring case and punctuation.

    Both sides go through `normalize_words`, so "can't" == "cannot" and "2" == "two".
    Can exceed 1.0 when the person says many extra words.
    """
    exp, got = normalize_words(expected), normalize_words(heard)
    if not exp:
        return 0.0 if not got else 1.0
    errors = sum(op != "ok" for op, _, _ in align_words(exp, got))
    return errors / len(exp)


# ---------------------------------------------------------------------------- audio levels


def _dbfs(rms: float) -> float:
    return 20 * math.log10(rms / 32768) if rms > 0 else -120.0


def audio_stats(clip: AudioClip) -> dict:
    """Levels of a 16-bit mono clip, computed locally (no network)."""
    samples = np.frombuffer(clip.pcm[: len(clip.pcm) // 2 * 2], dtype="<i2").astype(np.float64)
    sr = clip.sample_rate or 16000
    duration = len(samples) / sr
    if not len(samples):
        return {"duration_s": 0.0, "rms_dbfs": -120.0, "speech_dbfs": -120.0, "peak_dbfs": -120.0,
                "clipped_fraction": 0.0, "active_fraction": 0.0, "too_quiet": False, "clipped": False,
                "silent": True}
    win = max(1, sr // 50)  # 20 ms
    n_win = max(1, len(samples) // win)
    frames = samples[: n_win * win].reshape(n_win, -1) if len(samples) >= win else samples.reshape(1, -1)
    window_db = [_dbfs(r) for r in np.sqrt((frames**2).mean(axis=1))]
    active = [db for db in window_db if db > SILENCE_DBFS]
    loud = sorted(active, reverse=True)[: max(1, len(active) // 2)]  # loudest half of the speech
    speech_db = 10 * math.log10(sum(10 ** (db / 10) for db in loud) / len(loud)) if active else -120.0
    peak = float(np.abs(samples).max())
    clipped_fraction = float((np.abs(samples) >= CLIP_LEVEL).mean())
    total_rms = float(np.sqrt((samples**2).mean()))
    silent = len(active) * frames.shape[1] / sr < MIN_SPEECH_S
    return {
        "duration_s": round(duration, 3),
        "rms_dbfs": round(_dbfs(total_rms), 1),
        "speech_dbfs": round(speech_db, 1),
        "peak_dbfs": round(_dbfs(peak), 1),
        "clipped_fraction": round(clipped_fraction, 4),
        "active_fraction": round(len(active) / len(window_db), 3),
        "too_quiet": (not silent) and speech_db < QUIET_DBFS,
        "clipped": clipped_fraction > CLIP_FRACTION,
        "silent": silent,
    }


def _wav_bytes(clip: AudioClip) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(clip.sample_rate)
        w.writeframes(clip.pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------- Scribe


def load_api_key() -> str | None:
    """ELEVENLABS_API_KEY from the environment or ~/onset/.env.local. Never logged."""
    key = os.environ.get("ELEVENLABS_API_KEY") or os.environ.get("ELEVEN_API_KEY")
    if key:
        return key
    try:
        from dotenv import dotenv_values
    except ImportError:
        return None
    for path in (Path(__file__).resolve().parent.parent / ".env.local", Path.home() / "onset" / ".env.local"):
        if path.is_file():
            values = dotenv_values(path)
            key = values.get("ELEVENLABS_API_KEY") or values.get("ELEVEN_API_KEY")
            if key:
                return key
    return None


async def transcribe(
    clip: AudioClip,
    *,
    api_key: str,
    model_id: str = DEFAULT_MODEL,
    session: aiohttp.ClientSession | None = None,
    timeout_s: float = REQUEST_TIMEOUT_S,
) -> dict:
    """POST the clip to Scribe and return its JSON (text, words with start/end/type/logprob)."""
    form = aiohttp.FormData()
    if clip.sample_rate == 16000:
        form.add_field("file", clip.pcm, filename="speech.pcm", content_type="application/octet-stream")
        form.add_field("file_format", "pcm_s16le_16")
    else:
        form.add_field("file", _wav_bytes(clip), filename="speech.wav", content_type="audio/wav")
    form.add_field("model_id", model_id)
    form.add_field("language_code", "en")
    form.add_field("timestamps_granularity", "word")
    form.add_field("tag_audio_events", "true")  # "(mumbling)" etc. instead of invented words
    form.add_field("temperature", "0")
    form.add_field("diarize", "false")

    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.post(
            STT_URL,
            data=form,
            headers={"xi-api-key": api_key},
            timeout=aiohttp.ClientTimeout(total=timeout_s),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:300]
                raise RuntimeError(f"Scribe HTTP {resp.status}: {body}")
            return await resp.json()
    finally:
        if own:
            await session.close()


def _speech_timing(words: list[dict]) -> tuple[float | None, float | None, float | None]:
    """(words_per_minute, longest_pause_s, onset_delay_s) from timed word tokens."""
    timed = [w for w in words if w.get("start") is not None and w.get("end") is not None]
    if not timed:
        return None, None, None
    span = timed[-1]["end"] - timed[0]["start"]
    wpm = len(timed) / span * 60 if span > 0 else None
    gaps = [b["start"] - a["end"] for a, b in zip(timed, timed[1:])]
    return wpm, max([0.0, *gaps]), timed[0]["start"]


# ---------------------------------------------------------------------------- the check


async def check_sentence(
    clip: AudioClip,
    expected: str,
    *,
    api_key: str | None = None,
    model_id: str = DEFAULT_MODEL,
    session: aiohttp.ClientSession | None = None,
) -> SpeechResult:
    """Transcribe the caller's attempt and compare it with the expected sentence."""
    detail: dict = {"model": model_id, "autocorrect_note": AUTOCORRECT_NOTE}

    def result(quality: Quality, transcript=None, wer=None, wpm=None, pause=None) -> SpeechResult:
        return SpeechResult(quality, expected, transcript, wer, wpm, pause, detail)

    try:
        stats = audio_stats(clip)
    except Exception as exc:  # malformed buffer
        detail["error"] = f"audio: {type(exc).__name__}: {exc}"
        return result(Quality.ERROR)
    detail["audio"] = stats
    if stats["duration_s"] < MIN_SPEECH_S or stats["silent"]:
        detail["reason"] = "silence"
        return result(Quality.NO_RESPONSE)

    key = api_key or load_api_key()
    if not key:
        detail["error"] = "no ELEVENLABS_API_KEY"
        return result(Quality.ERROR)

    t0 = time.monotonic()
    try:
        data = await transcribe(clip, api_key=key, model_id=model_id, session=session)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        detail["latency_s"] = round(time.monotonic() - t0, 3)
        detail["error"] = f"{type(exc).__name__}: {str(exc)[:300]}".replace(key, "***")
        return result(Quality.ERROR)
    detail["latency_s"] = round(time.monotonic() - t0, 3)

    tokens = data.get("words") or []
    events = [w.get("text", "") for w in tokens if w.get("type") == "audio_event"]
    spoken = [w for w in tokens if w.get("type") == "word" and normalize_words(w.get("text", ""))]
    fillers = [w for w in spoken if normalize_words(w["text"]) and normalize_words(w["text"])[0] in FILLERS]
    content = [w for w in spoken if w not in fillers]
    detail["audio_events"] = events
    detail["fillers"] = [w["text"] for w in fillers]
    detail["words"] = [
        {"text": w["text"], "start": w.get("start"), "end": w.get("end"), "logprob": w.get("logprob")}
        for w in spoken
    ]
    logprobs = [w["logprob"] for w in content if isinstance(w.get("logprob"), (int, float))]
    if logprobs:
        detail["mean_logprob"] = round(sum(logprobs) / len(logprobs), 3)
        detail["min_logprob"] = round(min(logprobs), 3)
    if data.get("language_code"):
        detail["language"] = data.get("language_code")

    raw_text = (data.get("text") or "").strip()
    detail["raw_text"] = raw_text
    if not content:
        detail["reason"] = "no words heard"
        return result(Quality.NO_RESPONSE, transcript=raw_text or None)

    transcript = " ".join(w["text"].strip() for w in content)
    wer = word_error_rate(expected, transcript)
    detail["word_error_rate_with_fillers"] = round(word_error_rate(expected, " ".join(w["text"] for w in spoken)), 3)
    detail["alignment"] = [
        {"op": op, "expected": e, "heard": h}
        for op, e, h in align_words(normalize_words(expected), normalize_words(transcript))
    ]
    wpm, pause, onset = _speech_timing(content)
    detail["onset_delay_s"] = None if onset is None else round(onset, 3)

    quality = Quality.OK
    level_problem = [name for name in ("too_quiet", "clipped") if stats[name]]
    if level_problem and wer > 0:
        # Errors in a bad recording might be the recording, not the person.
        detail["reason"] = "transcript untrustworthy: " + ", ".join(level_problem)
        quality = Quality.PARTIAL
    if wpm is None:
        detail["reason"] = "no word timestamps"
        quality = Quality.PARTIAL

    return result(
        quality,
        transcript=transcript,
        wer=round(wer, 3),
        wpm=None if wpm is None else round(wpm, 1),
        pause=None if pause is None else round(pause, 3),
    )
