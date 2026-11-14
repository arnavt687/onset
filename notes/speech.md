# Piece 3: sentence repetition (`speech`)

Status: **done** for the ROADMAP row. `check_sentence` returns a `SpeechResult` with transcript,
WER, words per minute and longest pause. Unit tests pass offline (22). The integration test
ran against the live ElevenLabs APIs on 2026-11-13 and passed (8 clips, all 25 tests).
No new packages: it uses `aiohttp`, `numpy` and `python-dotenv`, which are already installed.

## API

```python
from onset.speech import check_sentence, word_error_rate, SENTENCES
result = await check_sentence(AudioClip(pcm, sample_rate, t_start), SENTENCES[0])
```

- `word_error_rate(expected, heard)` computes word-level Levenshtein distance over normalized words,
  divided by the number of expected words. Normalization lowercases, strips punctuation,
  expands contractions on both sides ("can't" becomes "cannot", "it's" becomes "it is"), spells
  digits as words ("42" becomes "forty two"), splits hyphens and drops "(laughter)"-style tags.
  The result can go above 1.0 when the person adds many extra words.
- `check_sentence(clip, expected, *, api_key=None, model_id="scribe_v2", session=None)`
  checks levels locally, calls Scribe (`POST /v1/speech-to-text`) and compares the
  transcript with the expected sentence. It never raises into the call, apart from
  `CancelledError`. 32 or 48 kHz audio is downsampled to 16 kHz and sent as raw
  `pcm_s16le_16`, the lower-latency path. Other sample rates are sent as WAV.
- Scribe options sent: `timestamps_granularity=word`, `tag_audio_events=true`,
  `temperature=0`, `language_code=en`, `diarize=false`. No `keyterms` are sent, because they
  would bias the transcript toward the expected words. `no_verbatim` is left off, so
  fillers and false starts stay visible.
- The key is `ELEVENLABS_API_KEY` from the environment, or else from `.env.local` (the
  worktree's first, then `~/onset/.env.local`) via `dotenv_values`. The key is never logged, and it is
  scrubbed from error text.

## Quality rules

| Situation | Quality | Scribe called? |
|---|---|---|
| Shorter than 0.3 s, or less than 0.3 s of 20 ms windows above -55 dBFS | `NO_RESPONSE` | no (free) |
| Scribe returns no words (only audio events such as "(mumbling)", or nothing) | `NO_RESPONSE` | yes |
| Speech level below -40 dBFS (`too_quiet`) or more than 0.5 % of samples at or above 32000 (`clipped`), **and** WER > 0 | `PARTIAL` (an error could come from the recording, not the person) | yes |
| Same level problem but WER = 0 | `OK`, with the flag kept in `detail["audio"]` | yes |
| No word timestamps | `PARTIAL` | yes |
| HTTP error, timeout (15 s), no key, malformed audio | `ERROR` | n/a |

`words_per_minute` = timed content words / (last word end - first word start) x 60, so pauses
count. `longest_pause_s` = the largest gap between consecutive content words. Fillers ("um",
"uh") are excluded from WER and timing. They are listed in `detail["fillers"]`, and
`detail["word_error_rate_with_fillers"]` gives the WER with them included.

`detail` also contains: `audio` (levels), `latency_s`, `words` (text, start, end, logprob),
`alignment` (each expected word as ok/sub/del/ins), `mean_logprob`, `min_logprob`,
`low_confidence_words` (logprob < -0.15, provisional), `onset_delay_s`, `audio_events`,
`raw_text` and `autocorrect_note`.

## Integration run (2026-11-13, scribe_v2, TTS voice Sarah, `eleven_multilingual_v2`, 16 kHz)

Expected sentence: "You can't teach an old dog new tricks." (8 words)

| Clip (TTS text) | Quality | Transcript | WER | WPM | Longest pause | min logprob | Latency |
|---|---|---|---|---|---|---|---|
| clear | ok | You can't teach an old dog new tricks. | 0.00 | 253 | 0.08 s | -0.09 | 0.36 s |
| missing words ("teach a dog") | ok | You can't teach a dog new tricks? | 0.25 | 256 | 0.06 s | -0.00 | 0.50 s |
| swapped word ("old frog") | ok | You can't teach an old frog new tricks | 0.125 | 267 | 0.10 s | -0.00 | 0.32 s |
| slow (speed 0.7) + 1.8 s / 1.5 s breaks | ok | You can't teach an old dog new tricks. | 0.00 | 74 | 1.96 s | -0.03 | 0.58 s |
| garbled ("Yoo kaan teesh un ohl dahg noo triks") | ok | Yo kannte schon all dag no tricks. | 0.875 | 175 | 0.16 s | -0.85 | 0.38 s |
| mild slur ("can't teash an old dog new twicks") | ok | You can't teach an old dog new tricks | **0.00** | 245 | 0.08 s | -0.23 | 0.50 s |
| clear at -46 dBFS (quiet) | ok, `too_quiet` flagged | exact | 0.00 | 255 | 0.08 s | -0.01 | 0.36 s |
| clear x30, 44 % clipped | ok, `clipped` flagged | exact | 0.00 | 253 | 0.06 s | -0.00 | 0.28 s |

A separate probe compared scribe_v1 with scribe_v2 on two mild-slur clips and on a muffled
version of the clear clip (8-sample low-pass, 15 % slower):

| Clip | scribe_v1 | scribe_v2 |
|---|---|---|
| "teash ... twicks" | exact sentence, WER 0, min lp -0.13 | exact sentence, WER 0, min lp -0.17 |
| "Yuh can' teach an ol' dahg new trigs" | "You can teach an old dog new tricks", WER 0.125, min lp -0.36 | same, min lp -0.35 |
| muffled + slowed clear | exact, WER 0 | exact, WER 0 |

### Does Scribe "auto-correct" slurred speech? Yes, for mild slurring

- With heavy garbling, the transcript shows it: WER 0.875 and min logprob around -0.85.
- With mild slurring, the transcript hides it. Mispronounced words ("teash", "twicks", "trigs",
  "dahg") come back as the correct real words, and WER is 0. scribe_v1 and scribe_v2 behave
  the same way. A real word swap ("frog") was kept, so Scribe is not pulling the transcript
  toward the idiom itself. It pulls sounds toward the nearest plausible word.
- The only trace is lower confidence: min logprob -0.13 to -0.36 on mild slurring, against
  0.00 to -0.10 on clear speech. The clear clip's min logprob varied between runs (-0.00 to
  -0.10) even with `temperature=0`, so this signal is weak and noisy.
- Options checked (API reference, 2026-11-13): no parameter disables the language model or its
  biasing. The only controls are `temperature` (we use 0), `no_verbatim` (we leave it off, so
  disfluencies stay), `tag_audio_events` (on) and `keyterms` (we never send them, because
  they increase bias). `seed` gives best-effort determinism only.
- Consequence: WER catches wrong, missing or extra words, but it **will not catch mild
  dysarthria**. Rate, pauses and confidence have to carry that part, compared with the
  person's own baseline.

## Latency and cost per check

- Latency: Scribe answered in 0.28 to 0.58 s (scribe_v2) and 0.30 to 0.48 s (scribe_v1) for 3 to 8 s clips.
  Silence is rejected locally in about 1 ms.
- Cost per check (Scribe v2, $0.20 per audio hour; silence is billed): a 5 s clip costs
  about $0.0003, and a 15 s window about $0.0008. The pricing docs state no minimum billed duration for plain requests; minimums apply only to keyterms over 100 and to `transcript_edit`, neither of which we use.
- Test cost: TTS ran once for about 350 characters (7 clips, cached in
  `recordings/speech-tests/*.wav`, gitignored). Each integration run sends 8 clips (about 30 s of
  audio) to Scribe, about $0.002. Reruns reuse the cached clips.

## Suggested thresholds for piece 4 (`decide`)

These come from TTS speech, not from patients. Treat them as starting points to replace once
real baselines exist.

- Any quality other than OK means UNCLEAR. That covers silence, level problems combined with
  errors, and API errors.
- WER: escalate if `wer >= baseline_wer + 0.10`. With 6 to 8 word sentences, one extra wrong,
  missing or added word adds 0.125 to 0.17. Clear speech scored 0.00.
- Rate: escalate if `wpm < 0.7 x baseline_wpm`. The slow clip scored 0.29x the clear clip.
  TTS at normal speed ran about 250 wpm, and real older adults repeating a sentence will be
  closer to 120 to 180.
- Pauses: escalate if `longest_pause_s > max(1.0, baseline_pause + 0.7)`. Clear speech pauses
  were at most 0.1 s, and the injected breaks measured 1.96 s.
- Confidence (to help catch auto-corrected slurring): treat `min_logprob < -0.5`, or
  `mean_logprob` falling by more than 0.15 below baseline, as UNCLEAR rather than ESCALATE on its own. The
  `Baseline` type has no logprob field yet. Piece 4 or the main session would need a
  `types.py` change to store `speech_min_logprob`. I left `types.py` unchanged.
- `onset_delay_s` (time to the first word) is recorded but needs real-call data before it can
  get a threshold.

## How to run

```bash
cd ~/onset-wt/speech && ~/onset/.venv/bin/python -m pytest -q tests/test_speech.py           # offline
ONSET_NETWORK_TESTS=1 ~/onset/.venv/bin/python -m pytest -q -s tests/test_speech.py          # paid, prints a report
```

The paid run writes `recordings/speech-tests/last_run.json`.

## Next steps (not part of this row)

- Replace the TTS numbers with a few real recorded attempts from piece 7 (`tools/record.py`).
- If mild dysarthria matters more than Scribe can show, consider a local phoneme recognizer
  (for example a wav2vec2 phoneme CTC model). It would compare sounds, not words, and avoid the
  language-model correction. That needs a new package and model.
