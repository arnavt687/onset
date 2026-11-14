"""Piece 3: sentence repetition (ElevenLabs Scribe speech-to-text, then plain comparison).

Owner: the `speech` session. See ROADMAP.md, piece 3.
"""

from __future__ import annotations

from onset.types import AudioClip, SpeechResult

# Short, plain, standard-style sentences. The flow picks one; the baseline stores which.
SENTENCES = [
    "You can't teach an old dog new tricks.",
    "The sky is blue in Cincinnati.",
    "It is a sunny day today.",
]


async def check_sentence(clip: AudioClip, expected: str) -> SpeechResult:
    """Transcribe the caller's attempt and compare it with the expected sentence."""
    raise NotImplementedError


def word_error_rate(expected: str, heard: str) -> float:
    """Word-level edit distance divided by the number of expected words, ignoring case and punctuation."""
    raise NotImplementedError
