"""The presentation's voice commands (web/present/voice.js), run under Node if present.

voice.js is the part with no page and no microphone in it: text in, command out, and the
listener that turns the recogniser's growing, revised results into commands that fire once.
These tests pin three things: the phrases that must work (alone and inside a sentence), the
lines of the pitch that must not move the deck, and single firing over interim and final results.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VOICE = ROOT / "web" / "present" / "voice.js"
DECK = (ROOT / "web" / "present" / "deck.js").read_text(encoding="utf-8")
INDEX = (ROOT / "web" / "present" / "index.html").read_text(encoding="utf-8")
SCENES = INDEX.count('<section class="scene')

# voice.js is an ES module with a .js name; a data: URL imports it on any Node, with no package.json.
RUNNER = r"""
import { readFileSync } from 'node:fs';
const src = readFileSync(process.argv[1], 'utf8');
const v = await import('data:text/javascript;base64,' + Buffer.from(src).toString('base64'));
const job = JSON.parse(readFileSync(0, 'utf8'));
const out = {
  match: job.texts.map(t => v.matchCommand(t, { scenes: job.scenes })),
  all: job.texts.map(t => v.matchCommands(t, { scenes: job.scenes }).map(m => m.cmd + (m.n || ''))),
  streams: job.streams.map(steps => {
    const l = v.makeListener({ scenes: job.scenes });
    return steps.map(s => (s.reset ? (l.reset(), []) : s.poll ? l.poll(s.t) : l.feed(s.results, s.t)).map(c => c.cmd + (c.n || '')));
  }),
  hold: v.HOLD_MS, cooldown: v.COOLDOWN_MS,
};
console.log(JSON.stringify(out));
"""

NEXT = [
    "next", "next slide", "move on", "let's move on", "go on", "continue", "go forward", "forward", "advance",
    "next one", "next please", "next page", "go next", "keep going", "moving on", "carry on", "onward", "proceed",
    "skip ahead", "advance the slide", "move forward", "let's continue", "okay next", "alright, next slide please",
    "Next.", "NEXT SLIDE", "necks slide", "next slight",
    # inside a sentence
    "so that's the hook, next slide, and here is the problem",
    "okay let's move on to the problem",
    "and that is why going home is the risky part, next",
    "that's the idea so next okay here is the demo",
    "okay, let's go on",
    "alright let's continue",
    "we can go to the next slide now",
    "let's move on to the next slide",
    "and with that, moving on",
    "cool, go forward please",
    "that is the hook | forward",
    "nobody is checking | continue",
]
BACK = [
    "back", "go back", "previous", "previous slide", "back one", "go back one", "go back a slide", "back one slide",
    "one back", "back please", "go backwards", "previous one", "prior slide", "go back to the previous slide",
    "back to the last slide", "sorry, go back", "wait, back one", "oops go back please", "okay back",
    "can we go back for a second",
    "actually let's go back to the previous slide",
    "let me back up",
    "they join the same call | back",
    "that was too fast | previous",
]
JUMPS = [
    ("go to the second slide", 2), ("go to slide two", 2), ("slide 3", 3), ("third slide", 3), ("jump to four", 4),
    ("go to the fourth one", 4), ("first slide", 1), ("go to the start", 1), ("last slide", SCENES), ("go to the end", SCENES),
    ("go to slide 2", 2), ("slide number five", 5), ("jump to slide number 6", 6), ("skip to slide seven", 7), ("go to 4", 4),
    ("go to number three", 3), ("take us to slide five", 5), ("back to slide two", 2), ("go back to slide 2", 2),
    ("the 3rd slide", 3), ("go to the 2nd slide", 2), ("jump to the fifth slide", 5), ("switch to the sixth one", 6),
    ("go to the forth slide", 4), ("scene four", 4), ("page 2", 2),
    ("back to the start", 1), ("go back to the beginning", 1), ("start over", 1), ("let's start over", 1), ("title slide", 1),
    ("take it from the top", 1), ("go to the very first slide", 1),
    ("final slide", SCENES), ("skip to the end", SCENES), ("jump to the last slide", SCENES), ("go to the last one", SCENES),
    ("closing slide", SCENES), ("back to the beginning of the talk", 1), ("skip to the end of the deck", SCENES),
    ("jump to for", 4), ("go to slide too", 2),
    # inside a sentence
    ("okay now let's go to slide three and look at the numbers", 3),
    ("we will jump to four now", 4),
    ("let me take us back to the second slide for a moment", 2),
    ("so if we go to the fourth one you can see the call", 4),
    ("and then slide 5 please", 5),
]
OTHER = [
    ("open the live view", "live"), ("show the live call", "live"), ("go to the dashboard", "live"), ("switch to the live view", "live"),
    ("bring up the dashboard", "live"), ("let's see the live dashboard", "live"), ("show us the live view", "live"),
    ("okay let's pull up the live call now", "live"), ("let me show you the live call", "live"),
    ("open the browser demo", "try"), ("try the browser demo", "try"), ("switch to the browser demo", "try"),
    ("use the backup demo", "try"), ("let's go to the in-browser demo", "try"), ("show the web version", "try"),
    ("start the demo", "demo"), ("go to the demo", "demo"), ("let's call it", "demo"), ("time for the live demo", "demo"),
    ("jump to the demo", "demo"), ("let's do the demo", "demo"), ("demo time", "demo"), ("on to the demo", "demo"),
    ("alright, let's start the live demo", "demo"),
    ("show the smile", "smile"), ("check the smile", "smile"), ("go to the smile", "smile"), ("show me the face check", "smile"),
    ("show the arms", "arms"), ("check the arms", "arms"), ("go to the arm check", "arms"), ("let's try the arms", "arms"),
    ("show the speech", "speech"), ("check the speech", "speech"), ("go to the sentence", "speech"), ("let's do the speech check", "speech"),
    ("show a change", "change"), ("act a change", "change"), ("make a change", "change"), ("show me a change", "change"),
    ("simulate a change", "change"), ("let's act out a change", "change"),
    ("back to normal", "normal"), ("back to usual", "normal"), ("back to baseline", "normal"), ("undo that", "normal"),
    ("play it again", "replay"), ("replay", "replay"), ("show that again", "replay"), ("run it again", "replay"), ("one more time", "replay"),
    ("stop listening", "voiceoff"), ("voice off", "voiceoff"), ("turn the voice off", "voiceoff"), ("turn off the microphone", "voiceoff"),
    ("mute the mic", "voiceoff"),
]

# The six lines named in the brief, then the recorded narration's and the run of show's wording,
# what the agent says out loud during the call, and ordinary sentences with the command words in them.
MUST_NOT = [
    "the next step is a clinician-led study",
    "the weeks after hospital discharge",
    "we're going to run you through our demo",
    "he can make that final decision",
    "both arms up for ten seconds",
    "Onset is a FaceTime agent for stroke survivors",
    # run of show
    "After a stroke or a mini-stroke, people go home, and their family becomes the early warning system.",
    "Nearly one in four strokes in the US happen to someone who has already had one.",
    "Onset is a one-minute check-in you FaceTime. It runs the checks of FAST, the public stroke-recognition checklist, and compares you with yourself.",
    "Picture someone a few weeks home. A usual morning. They call Onset.",
    "Face landmarks, on the laptop's CPU. It measures how far each corner of the mouth lifts.",
    "Ten seconds. It tracks the wrists against the shoulders, so a wobbly phone doesn't count as drift.",
    "Listen to what it says. Not 'you're fine'. 'No change detected compared with your usual check-in.'",
    "Same person, a different morning. We'll act out three changes: a one-sided smile, one arm sinking, and a pause in the middle of the sentence.",
    "Any one of those would have been enough. A change, or anything it couldn't measure clearly, gets the same answer: call 911 now.",
    "And the caregiver gets this text. It doesn't name a condition. It says what it noticed and what it told them to do.",
    "Three choices. The decision is fixed rules you can read, with no LLM in the decision path.",
    "Anything unclear counts as a change, so it leans toward false alarms. And it never tells anyone they're fine, and never diagnoses.",
    "The thresholds are provisional, tuned on synthetic data and one person. Our next step is a clinician-led validation study.",
    "Next is a clinician-led validation study. Thank you.",
    "It flagged this one. That's the design: when it isn't sure, it says call 911. A false alarm costs a phone call.",
    "Same pipeline, from a call we recorded this morning.",
    # the agent, out loud from the laptop during the call
    "Hi, this is Onset, your check-in. It takes about a minute.",
    "Look at the camera with a relaxed face. Now give me a big smile.",
    "Make sure I can see you down to your waist. Hold both arms straight out in front of you.",
    "You can lower your arms.",
    "Please repeat after me: You can't teach an old dog new tricks.",
    "No change detected compared with your usual check-in. If you feel unwell or worried, call 911.",
    "I noticed a change. Please call 911 now.",
    "I've sent a message to your caregiver.",
    # the command words in ordinary sentences
    "what's next for Onset is a clinical study",
    "next week we start the study",
    "it sits next to the phone",
    "next, we measure the arms",
    "next up is the live call",
    "the next one hundred days matter",
    "one in four go on to have another stroke",
    "going forward we want to validate the thresholds",
    "it is a step forward for stroke care",
    "we look forward to your questions",
    "this lets families move on with their lives",
    "patients are sent back home with a leaflet",
    "they come back for a follow-up in three months",
    "back then nobody was checking",
    "they can get back to their normal life",
    "a previous stroke raises the risk",
    "the previous check-in is the comparison",
    "we continue to monitor for ninety days",
    "the risk continues for three months",
    "you would have to go to one of these clinics",
    "they go to two different hospitals",
    "nearly one in four, so about twenty five percent",
    "the first check-in records the baseline",
    "First, a smile. Then both arms. Then a sentence to repeat.",
    "on slide three we showed the numbers",
    "as you saw on the first slide, the risk is highest early",
    "at the end of the call Onset decides",
    "from the start of the call to the end of it",
    "it adds the caregiver to the live call",
    "this is the monitoring dashboard",
    "the care team reads the dashboard every morning",
    "in our demo you will see two calls",
    "that was the demo",
    "FAST means face, arms, speech, time",
    "it noticed a change in the smile",
    "the caregiver gets a text and joins the same call",
    "there is no going back to the hospital every day",
    "who do they go to for help",
    "the last check-in was three days ago",
    "it took one second",
    "it goes back to one",
    "it takes us back to square one",
    "we went from zero to one in a weekend",
    "the first one is the smile, the second one is the arms and the third one is the speech",
    "if they have a second stroke the first one to know is the caregiver",
    "this is page one of the discharge plan",
    "what comes next is up to the clinicians",
    "the patient moves on to rehab",
    "a slow slide over weeks is flagged even when no single day crosses the line",
    "the browser demo runs on any phone or laptop, nothing is sent anywhere",
    "you can see the live dashboard here",
    "it is hard to start over after a stroke",
    "advance care planning is a different problem",
    "we want to slide this into a discharge plan",
]


@pytest.fixture(scope="module")
def voice():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    narration = json.loads((ROOT / "tools" / "video" / "narration.json").read_text(encoding="utf-8"))
    spoken = [s["text"] for s in narration["segments"]]
    texts = sorted({*NEXT, *BACK, *(t for t, _ in JUMPS), *(t for t, _ in OTHER), *MUST_NOT, *spoken,
                    "slide eight", "go to slide 12", "go to 911", "twelfth slide"})
    streams = {name: steps for name, steps in STREAMS.items()}
    job = {"scenes": SCENES, "texts": texts, "streams": list(streams.values())}
    res = subprocess.run([node, "--input-type=module", "-e", RUNNER, str(VOICE)], input=json.dumps(job),
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    return {
        "match": dict(zip(texts, out["match"])), "all": dict(zip(texts, out["all"])),
        "streams": dict(zip(streams, out["streams"])), "spoken": spoken, "hold": out["hold"], "cooldown": out["cooldown"],
    }


def _key(m):
    return None if m is None else m["cmd"] + (str(m["n"]) if m.get("n") else "")


def test_the_deck_has_seven_scenes():
    assert SCENES == 7  # the jumps below name scenes 1 to 7


@pytest.mark.parametrize("text", NEXT)
def test_next(voice, text):
    assert voice["all"][text] == ["next"], text


@pytest.mark.parametrize("text", BACK)
def test_back(voice, text):
    assert voice["all"][text] == ["back"], text


@pytest.mark.parametrize("text,n", JUMPS)
def test_jump_to_a_slide(voice, text, n):
    assert voice["all"][text] == [f"goto{n}"], text


def test_every_scene_by_cardinal_and_ordinal_word_and_digit(voice):
    # a second Node run, so the table above stays the readable list
    node = shutil.which("node")
    cards = ["one", "two", "three", "four", "five", "six", "seven"]
    ords = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh"]
    sfx = ["1st", "2nd", "3rd", "4th", "5th", "6th", "7th"]
    cases = []
    for i in range(SCENES):
        n = i + 1
        cases += [(f"go to slide {cards[i]}", n), (f"slide {n}", n), (f"go to slide {n}", n), (f"jump to {cards[i]}", n),
                  (f"jump to {n}", n), (f"{ords[i]} slide", n), (f"go to the {ords[i]} slide", n), (f"{sfx[i]} slide", n),
                  (f"go to the {ords[i]} one", n), (f"okay so go to slide {cards[i]} please", n)]
    job = {"scenes": SCENES, "texts": [t for t, _ in cases], "streams": []}
    res = subprocess.run([node, "--input-type=module", "-e", RUNNER, str(VOICE)], input=json.dumps(job),
                         capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr
    got = json.loads(res.stdout)["all"]
    for (text, n), keys in zip(cases, got):
        assert keys == [f"goto{n}"], text


def test_a_slide_the_deck_does_not_have_is_not_a_command(voice):
    for text in ("slide eight", "go to slide 12", "go to 911", "twelfth slide"):
        assert voice["match"][text] is None, text


@pytest.mark.parametrize("text,cmd", OTHER)
def test_the_other_commands(voice, text, cmd):
    assert voice["all"][text] == [cmd], text


@pytest.mark.parametrize("text", MUST_NOT)
def test_ordinary_sentences_do_not_move_the_deck(voice, text):
    assert voice["match"][text] is None, f"{text!r} fired {_key(voice['match'][text])}"


def test_the_recorded_narration_does_not_move_the_deck(voice):
    assert len(voice["spoken"]) >= 10
    for text in voice["spoken"]:
        assert voice["match"][text] is None, f"{text!r} fired {_key(voice['match'][text])}"


def test_match_returns_the_phrase_and_a_label(voice):
    m = voice["match"]["okay let's move on to the problem"]
    assert m == {"cmd": "next", "label": "Next", "phrase": "move on"}
    m = voice["match"]["okay now let's go to slide three and look at the numbers"]
    assert m["label"] == "Slide 3" and m["phrase"] == "go to slide three" and m["n"] == 3
    assert voice["match"]["first slide"]["label"] == "First slide"
    assert voice["match"]["last slide"]["label"] == "Last slide"


# --------------------------------------------------------------------------- the listener
# Each step is what the recogniser reports at that moment: the whole list of results so far
# (final ones, then the utterance being said), or a poll with nothing new.


def R(*parts):
    """('text', final) pairs, or (['best', 'second guess'], final)."""
    return [{"final": f, "alts": t if isinstance(t, list) else [t]} for t, f in parts]


def said(t, *parts):
    return {"t": t, "results": R(*parts)}


def poll(t):
    return {"t": t, "poll": True}


def words(sentence, t0=0, every=180, before=()):
    """A sentence arriving a word at a time, then complete."""
    ws, steps = sentence.split(), []
    for i in range(1, len(ws) + 1):
        steps.append(said(t0 + i * every, *before, (" ".join(ws[:i]), False)))
        steps.append(poll(t0 + i * every + every // 2))
    steps.append(said(t0 + (len(ws) + 4) * every, *before, (sentence, True)))
    return steps


STREAMS = {
    # mid-sentence: fires as soon as the phrase is there, not at the pause, and only once
    "mid_sentence": [
        said(0, ("so that's", False)),
        said(200, ("so that's the hook next", False)),
        said(400, ("so that's the hook next slide", False)),
        said(600, ("so that's the hook next slide and here", False)),
        said(900, ("so that's the hook next slide and here is the problem", False)),
        said(1500, ("So that's the hook. Next slide. And here is the problem.", True)),
        poll(2600),
    ],
    # a bare "next": waits a moment for the following word, then fires; the final result adds nothing
    "bare_next": [said(0, ("next", False)), poll(300), poll(520), said(900, ("Next.", True)), poll(2000)],
    # ... and the following word rules it out
    "next_step": [said(0, ("next", False)), said(200, ("next step is a", False)), poll(800),
                  said(1400, ("Next step is a clinician-led study.", True)), poll(2500)],
    # the browser reports the utterance being said in two pieces, and moves words between them
    "split_interims": [
        said(0, ("let's go to the", False), (" next", False)),
        said(150, ("let's go to the", False), (" next slide", False)),
        said(300, ("let's go to the next slide", False), (" please", False)),
        said(450, ("let's go to the next slide please", False)),
        said(1200, ("Let's go to the next slide, please.", True)),
    ],
    # two different commands in one breath both run, in order ("go back" waits a moment: "go back to ...")
    "two_commands": [
        said(0, ("next slide", False)),
        said(1500, ("next slide oh sorry go back", False)),
        poll(2050),
        said(2400, ("Next slide. Oh sorry, go back.", True)),
    ],
    "two_commands_at_once": [said(0, ("next slide no wait previous slide", False)), said(900, ("Next slide. No wait, previous slide.", True))],
    # the same command on purpose, two utterances; and again within the cooldown, dropped
    "said_twice": [
        said(0, ("next slide", False)), said(600, ("Next slide.", True)),
        said(2600, ("Next slide.", True), ("next slide", False)), said(3200, ("Next slide.", True), ("Next slide.", True)),
        said(3300, ("Next slide.", True), ("Next slide.", True), ("next slide", False)),
    ],
    # the recogniser changes its mind about earlier words; the phrase it acted on moves along
    "revised": [
        said(0, ("um next slide", False)),
        said(250, ("um so yeah next slide", False)),
        said(500, ("I'm so yeah, next slide", False)),
        said(1300, ("Um, so yeah, next slide.", True)),
    ],
    "revised_one_word": [said(0, ("and next", False)), poll(520), said(700, ("and then next", False)), poll(1300), said(1500, ("And then, next.", True))],
    "revised_while_waiting": [said(0, ("and go back", False)), said(200, ("and then we go back", False)), said(900, ("And then we go back.", True))],
    # a number jump waits for the number, and fires before the sentence ends
    "jump": [
        said(0, ("and now go to", False)), said(200, ("and now go to slide", False)), said(400, ("and now go to slide 3", False)),
        said(600, ("and now go to slide 3 where the", False)), said(1500, ("And now go to slide 3 where the checks are.", True)),
    ],
    "jump_bare_number": [said(0, ("jump to", False)), said(200, ("jump to four", False)), poll(450), poll(720), said(1300, ("Jump to four.", True))],
    "go_to_one_of": [said(0, ("you would have to go to one", False)), said(200, ("you would have to go to one of these clinics", False)),
                     poll(900), said(1500, ("You would have to go to one of these clinics.", True))],
    # heard wrong: the recogniser's second guess is taken once the utterance is complete, whole phrases only
    "second_guess": [said(0, ("necks light", False)), said(700, (["necks light", "next slide", "next light"], True)), poll(1500)],
    "second_guess_weak": [said(0, ("the text", False)), said(700, (["the text", "next", "back"], True)), poll(1500)],
    # a pause, then one word by itself
    "after_a_pause": [said(0, ("they join the same call", True)), said(1500, ("they join the same call", True), ("back", False)),
                      poll(1800), poll(2100), said(2600, ("they join the same call", True), ("Back.", True))],
    "not_after_a_pause": [said(0, ("patients are sent back", False)), poll(900), said(1500, ("patients are sent back home", True))],
    # the recogniser stops and starts again (it does after every silence): its results start from nothing
    "restart": [said(0, ("next slide", False)), said(500, ("Next slide.", True)), {"reset": True},
                said(6000, ("next slide", False)), said(6500, ("Next slide.", True))],
    "restart_inside_cooldown": [said(0, ("previous slide", False)), {"reset": True}, said(300, ("previous slide", False))],
    # the pitch, a word at a time, with a poll between words: nothing
    "pitch_1": words("the next step is a clinician-led study"),
    "pitch_2": words("the weeks after hospital discharge"),
    "pitch_3": words("we're going to run you through our demo"),
    "pitch_4": words("he can make that final decision"),
    "pitch_5": words("both arms up for ten seconds"),
    "pitch_6": words("Onset is a FaceTime agent for stroke survivors"),
    "pitch_7": words("one in four go on to have another stroke", before=(("Nearly one in four US strokes are recurrent.", True),)),
    # a command a word at a time: once
    "words_next": words("okay let's move on to the next slide"),
    "words_jump": words("so let's go to the second slide now"),
    "words_demo": words("alright let's start the live demo"),
}


def _fired(voice, name):
    return [(i, k) for i, step in enumerate(voice["streams"][name]) for k in step]


def test_mid_sentence_fires_once_and_before_the_sentence_ends(voice):
    assert _fired(voice, "mid_sentence") == [(2, "next")]


def test_bare_next_waits_for_the_next_word(voice):
    assert 300 < voice["hold"] <= 520
    assert _fired(voice, "bare_next") == [(2, "next")]
    assert _fired(voice, "next_step") == []


def test_split_interim_results_fire_once(voice):
    assert _fired(voice, "split_interims") == [(1, "next")]


def test_two_commands_in_one_utterance(voice):
    assert _fired(voice, "two_commands") == [(0, "next"), (2, "back")]
    assert _fired(voice, "two_commands_at_once") == [(0, "next"), (0, "back")]


def test_same_command_twice_and_the_cooldown(voice):
    assert voice["cooldown"] >= 600
    assert _fired(voice, "said_twice") == [(0, "next"), (2, "next")]


def test_revised_words_do_not_fire_again(voice):
    assert _fired(voice, "revised") == [(0, "next")]
    assert _fired(voice, "revised_one_word") == [(1, "next")]
    assert _fired(voice, "revised_while_waiting") == [(2, "back")]


def test_number_jumps_while_speaking(voice):
    assert _fired(voice, "jump") == [(2, "goto3")]
    assert _fired(voice, "jump_bare_number") == [(3, "goto4")]
    assert _fired(voice, "go_to_one_of") == []


def test_second_guesses_only_for_whole_phrases(voice):
    assert _fired(voice, "second_guess") == [(1, "next")]
    assert _fired(voice, "second_guess_weak") == []


def test_one_word_after_a_pause(voice):
    assert _fired(voice, "after_a_pause") == [(3, "back")]
    assert _fired(voice, "not_after_a_pause") == []


def test_recogniser_restart(voice):
    assert _fired(voice, "restart") == [(0, "next"), (3, "next")]
    assert _fired(voice, "restart_inside_cooldown") == [(0, "back")]


@pytest.mark.parametrize("name", [f"pitch_{i}" for i in range(1, 8)])
def test_pitch_lines_word_by_word_never_fire(voice, name):
    assert _fired(voice, name) == []


def test_commands_word_by_word_fire_once(voice):
    assert [k for _, k in _fired(voice, "words_next")] == ["next"]
    assert [k for _, k in _fired(voice, "words_jump")] == ["goto2"]
    assert [k for _, k in _fired(voice, "words_demo")] == ["demo"]


# --------------------------------------------------------------------------- the page


def test_the_deck_uses_the_matcher_and_keeps_the_audio_plumbing():
    assert "from './voice.js'" in DECK
    assert "makeListener({ scenes: SCENES.length })" in DECK
    for cmd in ("next", "back", "goto", "live", "try", "demo", "smile", "arms", "speech", "change", "normal", "replay", "voiceoff"):
        assert f"\n  {cmd}:" in DECK, f"deck.js has no action for {cmd}"
    # interim results, the chosen microphone's track, the restart after the microphone opens, the
    # watchdog and the clicker are as they were
    assert "rec.continuous = true; rec.interimResults = true;" in DECK
    assert "voice.rec.start(voice.track)" in DECK
    assert "if (voice.on && voice.track) setTimeout(startRec, 200)" in DECK
    assert "voice.endedAt = now; startRec(); } // never left stopped" in DECK
    assert "navigator.mediaSession.setActionHandler" in DECK and "a.loop = true; a.volume = .02" in DECK
    assert 'id="voice-heard"' in INDEX and 'id="voice-did"' in INDEX
