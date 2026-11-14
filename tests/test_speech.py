"""Piece 3 tests: WER and normalization offline; Scribe end to end only with ONSET_NETWORK_TESTS=1.

The integration test makes a handful of short clips with ElevenLabs TTS (cached as .wav in
tests/_speech_cache/, which git ignores) and sends each to Scribe once. Run it with:

    ONSET_NETWORK_TESTS=1 ~/onset/.venv/bin/python -m pytest -q -s tests/test_speech.py
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import wave
from pathlib import Path

import numpy as np
import pytest

from onset import speech
from onset.speech import (
    SENTENCES,
    align_words,
    audio_stats,
    check_sentence,
    normalize_words,
    number_to_words,
    word_error_rate,
)
from onset.types import AudioClip, Quality

SR = 16000


def tone(seconds: float, amplitude: float, sr: int = SR) -> bytes:
    t = np.arange(int(seconds * sr)) / sr
    return (amplitude * 32767 * np.sin(2 * math.pi * 220 * t)).astype("<i2").tobytes()


def clip(pcm: bytes, sr: int = SR) -> AudioClip:
    return AudioClip(pcm=pcm, sample_rate=sr, t_start=0.0)


# ---------------------------------------------------------------------------- normalization


def test_normalize_case_punctuation():
    assert normalize_words("The sky is BLUE, in Cincinnati!") == ["the", "sky", "is", "blue", "in", "cincinnati"]


def test_normalize_contractions_both_ways():
    assert normalize_words("You can't teach") == ["you", "cannot", "teach"]
    assert normalize_words("won't") == ["will", "not"]
    assert normalize_words("It’s") == ["it", "is"]
    assert normalize_words("we're I'm they'll you've she'd isn't") == (
        "we are i am they will you have she would is not".split()
    )


def test_normalize_numbers_and_hyphens():
    assert normalize_words("2 dogs") == ["two", "dogs"]
    assert normalize_words("42") == ["forty", "two"]
    assert normalize_words("forty-two") == ["forty", "two"]
    assert normalize_words("1,250") == "one thousand two hundred fifty".split()
    assert normalize_words("3.5") == ["three", "point", "five"]


def test_normalize_drops_audio_event_tags():
    assert normalize_words("(laughter) it is [noise] a day") == ["it", "is", "a", "day"]


def test_number_to_words():
    assert number_to_words(0) == "zero"
    assert number_to_words(19) == "nineteen"
    assert number_to_words(90) == "ninety"
    assert number_to_words(101) == "one hundred one"
    assert number_to_words(2026) == "two thousand twenty six"
    assert number_to_words(1_000_000) == "one million"


# ---------------------------------------------------------------------------- WER


def test_wer_exact_and_equivalent_forms():
    s = "You can't teach an old dog new tricks."
    assert word_error_rate(s, s) == 0.0
    assert word_error_rate(s, "you cannot teach an old dog new tricks") == 0.0
    assert word_error_rate("It is a sunny day today.", "It's a sunny day today") == 0.0


def test_wer_substitution_deletion_insertion():
    s = "It is a sunny day today."  # 6 words
    assert word_error_rate(s, "It is a rainy day today") == pytest.approx(1 / 6)
    assert word_error_rate(s, "It is sunny day today") == pytest.approx(1 / 6)
    assert word_error_rate(s, "It is a very sunny day today") == pytest.approx(1 / 6)


def test_wer_swapped_words_cost_two():
    assert word_error_rate("The sky is blue in Cincinnati.", "The blue is sky in Cincinnati") == pytest.approx(2 / 6)


def test_wer_nothing_right_and_empty():
    assert word_error_rate("It is a sunny day today.", "") == 1.0
    assert word_error_rate("", "") == 0.0
    assert word_error_rate("", "hello") == 1.0
    assert word_error_rate("a day", "one two three four five") > 1.0  # many extra words


def test_alignment_ops():
    ops = align_words("a b c".split(), "a x c d".split())
    assert [o for o, _, _ in ops] == ["ok", "sub", "ok", "ins"]


# ---------------------------------------------------------------------------- audio gates (offline)


def test_silence_is_no_response_without_network(monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("silence must not be sent to Scribe")

    monkeypatch.setattr(speech, "transcribe", boom)
    r = asyncio.run(check_sentence(clip(bytes(SR * 2 * 3)), SENTENCES[0], api_key="x"))
    assert r.quality is Quality.NO_RESPONSE
    assert r.word_error_rate is None and r.transcript is None


def test_too_short_is_no_response():
    r = asyncio.run(check_sentence(clip(tone(0.1, 0.3)), SENTENCES[0], api_key="x"))
    assert r.quality is Quality.NO_RESPONSE


def test_level_flags():
    assert audio_stats(clip(tone(1.0, 0.3)))["too_quiet"] is False
    assert audio_stats(clip(tone(1.0, 0.003)))["too_quiet"] is True  # about -53 dBFS
    hot = (np.sign(np.sin(np.arange(SR) / SR * 2 * math.pi * 220)) * 32767).astype("<i2").tobytes()
    assert audio_stats(clip(hot))["clipped"] is True
    assert audio_stats(clip(tone(1.0, 0.5)))["clipped"] is False


def _fake_scribe(words: list[tuple[str, float, float]]):
    async def fake(c, **kwargs):
        out = []
        for i, (text, start, end) in enumerate(words):
            if i:
                out.append({"text": " ", "type": "spacing", "start": words[i - 1][2], "end": start, "logprob": 0})
            kind = "audio_event" if text.startswith("(") else "word"
            out.append({"text": text, "type": kind, "start": start, "end": end, "logprob": -0.1})
        return {"text": " ".join(w[0] for w in words), "words": out, "language_code": "eng"}

    return fake


def test_timing_from_word_timestamps(monkeypatch):
    words = [("It", 0.5, 0.7), ("is", 0.8, 0.9), ("a", 1.0, 1.1), ("sunny", 1.2, 1.5), ("um", 1.6, 1.7),
             ("day", 3.7, 4.0), ("today.", 4.1, 4.5)]
    monkeypatch.setattr(speech, "transcribe", _fake_scribe(words))
    r = asyncio.run(check_sentence(clip(tone(5.0, 0.3)), "It is a sunny day today.", api_key="x"))
    assert r.quality is Quality.OK
    assert r.word_error_rate == 0.0  # the filler is dropped
    assert r.detail["fillers"] == ["um"]
    assert r.detail["word_error_rate_with_fillers"] > 0
    assert r.longest_pause_s == pytest.approx(2.2)  # "sunny" end 1.5 -> "day" start 3.7, filler ignored
    assert r.words_per_minute == pytest.approx(6 / 4.0 * 60)
    assert r.detail["onset_delay_s"] == 0.5


def test_audio_events_only_is_no_response(monkeypatch):
    monkeypatch.setattr(speech, "transcribe", _fake_scribe([("(mumbling)", 0.2, 2.0)]))
    r = asyncio.run(check_sentence(clip(tone(3.0, 0.3)), SENTENCES[0], api_key="x"))
    assert r.quality is Quality.NO_RESPONSE
    assert r.detail["audio_events"] == ["(mumbling)"]


def test_quiet_audio_with_errors_is_partial(monkeypatch):
    monkeypatch.setattr(speech, "transcribe", _fake_scribe([("It", 0.1, 0.3), ("is", 0.4, 0.5), ("day", 0.6, 0.9)]))
    r = asyncio.run(check_sentence(clip(tone(2.0, 0.003)), "It is a sunny day today.", api_key="x"))
    assert r.quality is Quality.PARTIAL
    assert "too_quiet" in r.detail["reason"]
    assert r.word_error_rate == pytest.approx(0.5)


def test_api_failure_is_error_and_hides_key(monkeypatch):
    async def fail(c, **kwargs):
        raise RuntimeError("bad key secret-123")

    monkeypatch.setattr(speech, "transcribe", fail)
    r = asyncio.run(check_sentence(clip(tone(2.0, 0.3)), SENTENCES[0], api_key="secret-123"))
    assert r.quality is Quality.ERROR
    assert "secret-123" not in json.dumps(r.detail)


def test_missing_key_is_error(monkeypatch):
    monkeypatch.setattr(speech, "load_api_key", lambda: None)
    r = asyncio.run(check_sentence(clip(tone(2.0, 0.3)), SENTENCES[0]))
    assert r.quality is Quality.ERROR


def test_result_detail_is_json():
    r = asyncio.run(check_sentence(clip(bytes(100)), SENTENCES[0], api_key="x"))
    json.dumps(r.detail)


# ---------------------------------------------------------------------------- integration (opt-in, paid)

network = pytest.mark.skipif(os.environ.get("ONSET_NETWORK_TESTS") != "1", reason="set ONSET_NETWORK_TESTS=1")

CACHE = Path(__file__).parent / "_speech_cache"
VOICE = os.environ.get("ONSET_TEST_VOICE", "EXAVITQu4vr4xnSDxMaL")  # Sarah, as in agent.py
SENT = SENTENCES[0]  # "You can't teach an old dog new tricks."

# name -> (text sent to TTS, voice speed, what we expect)
CLIPS = {
    "clear": (SENT, 1.0),
    "missing_words": ("You can't teach a dog new tricks.", 1.0),
    "swapped_word": ("You can't teach an old frog new tricks.", 1.0),
    "slow_pauses": ('You can\'t teach <break time="1.8s" /> an old dog <break time="1.5s" /> new tricks.', 0.7),
    "garbled": ("Yoo kaan teesh un ohl dahg noo triks.", 0.8),
}


async def _tts(text: str, speed: float, key: str) -> bytes:
    import aiohttp

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}?output_format=pcm_16000"
    body = {"text": text, "model_id": "eleven_multilingual_v2",
            "voice_settings": {"stability": 0.6, "similarity_boost": 0.8, "speed": speed}}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, json=body, headers={"xi-api-key": key},
                          timeout=aiohttp.ClientTimeout(total=30)) as r:
            assert r.status == 200, (r.status, (await r.text())[:200])
            return await r.read()


def _cached_clip(name: str, key: str) -> AudioClip:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{name}.wav"
    if not path.exists():
        text, speed = CLIPS[name]
        pcm = asyncio.run(_tts(text, speed, key))
        pad = bytes(int(0.5 * SR) * 2)  # half a second of silence either side, like a real turn
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1), w.setsampwidth(2), w.setframerate(SR)
            w.writeframes(pad + pcm + pad)
    with wave.open(str(path), "rb") as w:
        return AudioClip(pcm=w.readframes(w.getnframes()), sample_rate=w.getframerate(), t_start=0.0)


@pytest.fixture(scope="module")
def results():
    key = speech.load_api_key()
    assert key, "ELEVENLABS_API_KEY missing from ~/onset/.env.local"
    clips = {name: _cached_clip(name, key) for name in CLIPS}
    pcm = np.frombuffer(clips["clear"].pcm, "<i2")
    gain = 10 ** ((-47 - audio_stats(clips["clear"])["speech_dbfs"]) / 20)  # speech at about -47 dBFS
    clips["quiet"] = AudioClip((pcm * gain).astype("<i2").tobytes(), SR, 0.0)
    clips["clipped"] = AudioClip(np.clip(pcm.astype(int) * 30, -32768, 32767).astype("<i2").tobytes(), SR, 0.0)

    async def run_all():
        return {n: await check_sentence(c, SENT, api_key=key) for n, c in clips.items()}

    out = asyncio.run(run_all())
    report = {
        n: {"quality": r.quality.value, "transcript": r.transcript, "wer": r.word_error_rate,
            "wpm": r.words_per_minute, "longest_pause_s": r.longest_pause_s,
            "latency_s": r.detail.get("latency_s"), "min_logprob": r.detail.get("min_logprob"),
            "mean_logprob": r.detail.get("mean_logprob"), "events": r.detail.get("audio_events"),
            "audio": {k: r.detail.get("audio", {}).get(k) for k in ("duration_s", "speech_dbfs", "clipped_fraction",
                                                                   "too_quiet", "clipped")},
            "reason": r.detail.get("reason"), "error": r.detail.get("error")}
        for n, r in out.items()
    }
    (CACHE / "last_run.json").write_text(json.dumps(report, indent=2))
    print("\n" + json.dumps(report, indent=2))
    return out


@network
def test_clear_sentence(results):
    r = results["clear"]
    assert r.quality is Quality.OK, r.detail
    assert r.word_error_rate <= 0.15
    assert 80 <= r.words_per_minute <= 260
    assert r.longest_pause_s < 0.8


@network
def test_missing_and_swapped_words_raise_wer(results):
    for name in ("missing_words", "swapped_word"):
        assert results[name].quality is Quality.OK, results[name].detail
        assert results[name].word_error_rate >= 0.1, (name, results[name].transcript)


@network
def test_slow_speech_with_pauses(results):
    r, clear = results["slow_pauses"], results["clear"]
    assert r.quality is Quality.OK, r.detail
    assert r.longest_pause_s >= 1.0
    assert r.words_per_minute < clear.words_per_minute


@network
def test_garbled_is_recorded(results):
    # Not asserting a value: this clip measures whether Scribe "auto-corrects" (see notes/speech.md).
    r = results["garbled"]
    assert r.quality in (Quality.OK, Quality.NO_RESPONSE, Quality.PARTIAL)


@network
def test_level_problems_flagged(results):
    assert results["quiet"].detail["audio"]["too_quiet"] is True
    assert results["clipped"].detail["audio"]["clipped"] is True
    for name in ("quiet", "clipped"):
        r = results[name]
        assert r.quality is (Quality.OK if r.word_error_rate == 0 else Quality.PARTIAL), r.detail
