# Piece 3: sentence repetition (`speech`)

Status: implementation and unit tests done; integration run pending.

## What works
- `word_error_rate(expected, heard)`: word-level Levenshtein over normalized words
  (lowercase, punctuation stripped, contractions expanded on both sides, digits spelled out,
  hyphens split, audio-event tags removed). Can exceed 1.0 with many extra words.
- `check_sentence(clip, expected)`: local level checks, then ElevenLabs Scribe (REST
  `POST /v1/speech-to-text`, `timestamps_granularity=word`, `tag_audio_events=true`,
  `temperature=0`, `language_code=en`). Never raises; see quality rules below.

## Left to do
- Run the integration test once and record results here.
