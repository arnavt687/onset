"""Piece 4: baselines, decision rules and the caregiver text.

Safety properties under test:
- no baseline, a corrupt baseline, or any step that is not OK never gives NO_CHANGE_DETECTED;
- a value exactly at a threshold counts as a change;
- no output string says "fine", "normal", "healthy" or "stroke".
"""

from __future__ import annotations

import ast
import json
import logging
import math
import os
import stat
from dataclasses import asdict, replace
from datetime import timedelta, timezone
from pathlib import Path

import pytest

from onset import decide as D
from onset.types import ArmResult, Baseline, CheckResults, FaceResult, Outcome, Quality, SpeechResult

SENTENCE = "The sky is blue in Cincinnati today"
T = D.DEFAULT_THRESHOLDS
NOT_OK = [q for q in Quality if q is not Quality.OK]
BANNED = ("fine", "normal", "healthy", "no stroke", "stroke")


# ------------------------------------------------------------------------------ helpers


def results(
    *,
    face_q=Quality.OK, asym=0.02, face_side="right",
    arm_q=Quality.OK, diff=0.02, arm_side="left", hold=10.0,
    speech_q=Quality.OK, wer=0.0, wpm=150.0, pause=0.4, sentence=SENTENCE,
    started_at="2026-11-13T14:05:00+00:00",
) -> CheckResults:
    return CheckResults(
        face=FaceResult(quality=face_q, asymmetry=asym, weaker_side=face_side, frames_used=30),
        arms=ArmResult(
            quality=arm_q, left_drift=diff, right_drift=0.0, drift_difference=diff,
            weaker_side=arm_side, hold_seconds=hold, frames_used=300,
        ),
        speech=SpeechResult(
            quality=speech_q, expected=sentence, transcript=sentence.lower(),
            word_error_rate=wer, words_per_minute=wpm, longest_pause_s=pause,
        ),
        started_at=started_at,
    )


def baseline(**kw) -> Baseline:
    base = dict(
        patient_id="p001", recorded_at="2026-11-01T10:00:00+00:00",
        face_asymmetry=0.02, arm_drift_difference=0.02,
        speech_word_error_rate=0.0, speech_words_per_minute=150.0,
        sentence=SENTENCE, samples=3,
    )
    base.update(kw)
    return Baseline(**base)


B = baseline()


# ------------------------------------------------------------------- decision: outcomes

CASES = [
    # id, results kwargs, baseline, outcome, substrings expected in the reasons
    ("all within thresholds", {}, B, Outcome.NO_CHANGE_DETECTED, []),
    ("improvement is not a change", dict(asym=0.0, diff=0.0, wpm=180.0), B, Outcome.NO_CHANGE_DETECTED, []),
    # smile
    ("smile at threshold", dict(asym=0.02 + T.face_asymmetry_increase), B, Outcome.ESCALATE, ["right side of the smile"]),
    ("smile just below", dict(asym=0.02 + T.face_asymmetry_increase - 0.001), B, Outcome.NO_CHANGE_DETECTED, []),
    ("smile far above", dict(asym=0.4), B, Outcome.ESCALATE, ["lifted less than usual"]),
    ("smile no side", dict(asym=0.4, face_side=None), B, Outcome.ESCALATE, ["One side of the smile"]),
    ("smile odd side value", dict(asym=0.4, face_side="middle"), B, Outcome.ESCALATE, ["One side of the smile"]),
    # arms
    ("arms at threshold", dict(diff=0.02 + T.arm_drift_difference_increase), B, Outcome.ESCALATE, ["left arm drifted"]),
    ("arms just below", dict(diff=0.02 + T.arm_drift_difference_increase - 0.001), B, Outcome.NO_CHANGE_DETECTED, []),
    ("arms no side", dict(diff=0.5, arm_side=None), B, Outcome.ESCALATE, ["One arm drifted"]),
    ("hold at minimum", dict(hold=T.arm_min_hold_s), B, Outcome.NO_CHANGE_DETECTED, []),
    ("hold too short", dict(hold=T.arm_min_hold_s - 0.1), B, Outcome.UNCLEAR, ["full time"]),
    ("hold missing", dict(hold=float("nan")), B, Outcome.UNCLEAR, ["full time"]),
    # sentence
    ("wer at threshold", dict(wer=T.speech_wer_increase), B, Outcome.ESCALATE, ["less clearly"]),
    ("wer just below", dict(wer=T.speech_wer_increase - 0.001), B, Outcome.NO_CHANGE_DETECTED, []),
    ("rate drop at threshold", dict(wpm=150.0 * (1 - T.speech_rate_drop_fraction)), B, Outcome.ESCALATE, ["slower than usual"]),
    ("rate drop just under", dict(wpm=150.0 * (1 - T.speech_rate_drop_fraction) + 0.5), B, Outcome.NO_CHANGE_DETECTED, []),
    ("pause at threshold", dict(pause=T.speech_longest_pause_s), B, Outcome.ESCALATE, ["long pause"]),
    ("pause just below", dict(pause=T.speech_longest_pause_s - 0.01), B, Outcome.NO_CHANGE_DETECTED, []),
    ("pause missing", dict(pause=None), B, Outcome.UNCLEAR, ["pauses"]),
    ("wer above 1 (extra words)", dict(wer=1.4), B, Outcome.ESCALATE, ["less clearly"]),
    ("other sentence still compared", dict(sentence="A different sentence", wer=0.5), B, Outcome.ESCALATE, ["less clearly"]),
    # missing or broken numbers with quality OK
    ("asymmetry missing", dict(asym=None), B, Outcome.UNCLEAR, ["smile check did not give"]),
    ("asymmetry nan", dict(asym=float("nan")), B, Outcome.UNCLEAR, ["smile check did not give"]),
    ("asymmetry negative", dict(asym=-0.1), B, Outcome.UNCLEAR, ["smile check did not give"]),
    ("drift missing", dict(diff=None), B, Outcome.UNCLEAR, ["arm check did not give"]),
    ("drift inf", dict(diff=float("inf")), B, Outcome.UNCLEAR, ["arm check did not give"]),
    ("wer missing", dict(wer=None), B, Outcome.UNCLEAR, ["sentence check did not give"]),
    ("wpm missing", dict(wpm=None), B, Outcome.UNCLEAR, ["sentence check did not give"]),
    # baseline problems
    ("no baseline", {}, None, Outcome.UNCLEAR, ["no saved baseline"]),
    ("no baseline, big smile asymmetry", dict(asym=0.9), None, Outcome.UNCLEAR, ["no saved baseline"]),
    ("no baseline, long pause still counts", dict(pause=5.0), None, Outcome.ESCALATE, ["long pause", "no saved baseline"]),
    ("invalid baseline wpm 0", {}, baseline(speech_words_per_minute=0.0), Outcome.UNCLEAR, ["no saved baseline"]),
    ("invalid baseline nan", {}, baseline(face_asymmetry=float("nan")), Outcome.UNCLEAR, ["no saved baseline"]),
    ("invalid baseline id", {}, baseline(patient_id="../etc"), Outcome.UNCLEAR, ["no saved baseline"]),
    # mixed
    ("change plus unclear", dict(asym=0.5, speech_q=Quality.TOO_DARK), B, Outcome.ESCALATE, ["smile", "too dark"]),
    ("everything changed", dict(asym=0.5, diff=0.5, wer=0.6, wpm=60.0, pause=3.0), B, Outcome.ESCALATE,
     ["smile", "arm", "less clearly", "slower", "long pause"]),
]


@pytest.mark.parametrize("kw,base,outcome,needles", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_decide_table(kw, base, outcome, needles):
    d = D.decide(results(**kw), base)
    assert d.outcome is outcome, d.reasons
    joined = " | ".join(d.reasons).lower()
    for needle in needles:
        assert needle.lower() in joined
    if outcome is Outcome.NO_CHANGE_DETECTED:
        assert d.reasons == []
    else:
        assert d.reasons, "every escalation or unclear result must say why"
    assert len(d.reasons) == len(set(d.reasons))


STEP_KW = {"face": "face_q", "arms": "arm_q", "speech": "speech_q"}


@pytest.mark.parametrize("step", list(STEP_KW))
@pytest.mark.parametrize("quality", NOT_OK, ids=[q.value for q in NOT_OK])
def test_every_quality_failure_is_unclear(step, quality):
    d = D.decide(results(**{STEP_KW[step]: quality}), B)
    assert d.outcome is Outcome.UNCLEAR
    assert len(d.reasons) == 1
    # Numbers from a step that was not OK are never trusted, even if they look like a change.
    bad = dict(asym=0.9, diff=0.9, wer=0.9, wpm=10.0)
    step_values = {"face": ["asym"], "arms": ["diff"], "speech": ["wer", "wpm"]}[step]
    d2 = D.decide(results(**{STEP_KW[step]: quality}, **{k: bad[k] for k in step_values}), B)
    assert d2.outcome is Outcome.UNCLEAR


@pytest.mark.parametrize("quality", NOT_OK, ids=[q.value for q in NOT_OK])
def test_never_no_change_without_baseline_or_with_unclear_step(quality):
    for kw in ({}, {"face_q": quality}, {"arm_q": quality}, {"speech_q": quality}):
        assert D.decide(results(**kw), None).outcome is not Outcome.NO_CHANGE_DETECTED
        if kw:
            assert D.decide(results(**kw), B).outcome is not Outcome.NO_CHANGE_DETECTED


def test_decision_carries_inputs():
    r = results()
    d = D.decide(r, B)
    assert d.results is r and d.baseline is B
    assert D.decide(r, baseline(speech_words_per_minute=0.0)).baseline is None


def test_custom_thresholds():
    strict = replace(T, face_asymmetry_increase=0.001)
    assert D.decide(results(asym=0.025), B).outcome is Outcome.NO_CHANGE_DETECTED
    assert D.decide(results(asym=0.025), B, strict).outcome is Outcome.ESCALATE


def test_thresholds_live_in_one_dataclass():
    names = {f for f in asdict(T)}
    assert names == {
        "face_asymmetry_increase", "arm_drift_difference_increase", "arm_min_hold_s",
        "speech_wer_increase", "speech_rate_drop_fraction", "speech_longest_pause_s",
    }
    assert all(v > 0 for v in asdict(T).values())
    assert 0 < T.speech_rate_drop_fraction < 1
    doc = (Path(__file__).resolve().parents[1] / "docs" / "thresholds.md").read_text(encoding="utf-8")
    for name in names:
        assert f"`{name}`" in doc, f"{name} is not documented in docs/thresholds.md"


# ------------------------------------------------------------------------ baseline_from


def test_baseline_from_averages_and_counts():
    rs = [results(asym=0.01, diff=0.03, wer=0.0, wpm=140.0), results(asym=0.03, diff=0.01, wer=0.2, wpm=160.0)]
    b = D.baseline_from(rs, "p001")
    assert b.patient_id == "p001"
    assert b.samples == 2
    assert b.face_asymmetry == pytest.approx(0.02)
    assert b.arm_drift_difference == pytest.approx(0.02)
    assert b.speech_word_error_rate == pytest.approx(0.1)
    assert b.speech_words_per_minute == pytest.approx(150.0)
    assert b.sentence == SENTENCE
    assert b.recorded_at.endswith("+00:00")


def test_baseline_from_single_sample():
    assert D.baseline_from([results()], "p001").samples == 1


@pytest.mark.parametrize("step", list(STEP_KW))
@pytest.mark.parametrize("quality", NOT_OK, ids=[q.value for q in NOT_OK])
def test_baseline_from_refuses_unclear_check_ins(step, quality):
    with pytest.raises(D.BaselineError, match="quality"):
        D.baseline_from([results(), results(**{STEP_KW[step]: quality})], "p001")


@pytest.mark.parametrize("kw", [dict(asym=None), dict(diff=None), dict(wer=None), dict(wpm=None),
                                dict(asym=float("nan")), dict(wpm=-1.0)])
def test_baseline_from_refuses_missing_values(kw):
    with pytest.raises(D.BaselineError):
        D.baseline_from([results(**kw)], "p001")


def test_baseline_from_refuses_empty_mixed_sentences_and_bad_ids():
    with pytest.raises(D.BaselineError):
        D.baseline_from([], "p001")
    with pytest.raises(D.BaselineError, match="same sentence"):
        D.baseline_from([results(), results(sentence="Another one")], "p001")
    for bad in ("", "../x", "a/b", ".hidden", "x" * 200, "a b"):
        with pytest.raises(D.BaselineError):
            D.baseline_from([results()], bad)


def test_baseline_from_refuses_zero_rate():
    with pytest.raises(D.BaselineError):
        D.baseline_from([results(wpm=0.0)], "p001")


# --------------------------------------------------------------------- save and load


def mode(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_save_load_roundtrip_and_permissions(tmp_path):
    d = tmp_path / "baselines"
    path = D.save_baseline(B, base_dir=d)
    assert path == d / "p001.json"
    assert mode(d) == 0o700
    assert mode(path) == 0o600
    assert D.load_baseline("p001", base_dir=d) == B
    assert json.loads(path.read_text())["version"] == D.BASELINE_FORMAT_VERSION
    assert sorted(p.name for p in d.iterdir()) == ["p001.json"]  # no temp files left


def test_save_tightens_existing_directory_and_overwrites(tmp_path):
    d = tmp_path / "baselines"
    d.mkdir(mode=0o755)
    os.chmod(d, 0o755)
    D.save_baseline(B, base_dir=d)
    newer = replace(B, face_asymmetry=0.05, samples=5)
    path = D.save_baseline(newer, base_dir=d)
    assert mode(d) == 0o700 and mode(path) == 0o600
    assert D.load_baseline("p001", base_dir=d) == newer


def test_save_uses_module_dir_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "BASELINE_DIR", tmp_path / "bl")
    D.save_baseline(B)
    assert D.load_baseline("p001") == B


def test_save_is_atomic_on_failure(tmp_path, monkeypatch):
    d = tmp_path / "baselines"
    D.save_baseline(B, base_dir=d)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(D.os, "replace", boom)
    with pytest.raises(OSError):
        D.save_baseline(replace(B, face_asymmetry=0.3), base_dir=d)
    monkeypatch.undo()
    assert D.load_baseline("p001", base_dir=d) == B
    assert sorted(p.name for p in d.iterdir()) == ["p001.json"]


@pytest.mark.parametrize("bad", [
    baseline(face_asymmetry=float("nan")), baseline(speech_words_per_minute=0.0),
    baseline(speech_word_error_rate=1.5), baseline(samples=0), baseline(patient_id="../x"),
    baseline(recorded_at="yesterday"), baseline(sentence=" "),
])
def test_save_refuses_invalid_baseline(tmp_path, bad):
    with pytest.raises(D.BaselineError):
        D.save_baseline(bad, base_dir=tmp_path)
    assert not any(tmp_path.rglob("*.json"))


def test_load_missing_is_none(tmp_path):
    assert D.load_baseline("p001", base_dir=tmp_path / "nothing") is None


def good_payload() -> dict:
    return {"version": D.BASELINE_FORMAT_VERSION, **asdict(B)}


def with_(key, value):
    p = good_payload()
    p[key] = value
    return json.dumps(p)


def without(key):
    p = good_payload()
    del p[key]
    return json.dumps(p)


CORRUPT = {
    "empty": "",
    "not json": "{not json",
    "truncated": json.dumps(good_payload())[:-10],
    "list": "[]",
    "null": "null",
    "missing field": without("face_asymmetry"),
    "missing version": without("version"),
    "wrong version": with_("version", 99),
    "extra field": with_("diagnosis", "x"),
    "nan": json.dumps(good_payload()).replace('"face_asymmetry": 0.02', '"face_asymmetry": NaN'),
    "infinity": json.dumps(good_payload()).replace('"arm_drift_difference": 0.02', '"arm_drift_difference": Infinity'),
    "negative": with_("arm_drift_difference", -0.1),
    "string number": with_("face_asymmetry", "0.02"),
    "bool number": with_("face_asymmetry", True),
    "zero rate": with_("speech_words_per_minute", 0),
    "wer above 1": with_("speech_word_error_rate", 2.0),
    "zero samples": with_("samples", 0),
    "float samples": with_("samples", 2.5),
    "other patient": with_("patient_id", "p002"),
    "bad timestamp": with_("recorded_at", "last tuesday"),
    "empty sentence": with_("sentence", ""),
    "huge": json.dumps({**good_payload(), "sentence": "x" * 70000}),
}


@pytest.mark.parametrize("content", list(CORRUPT.values()), ids=list(CORRUPT))
def test_corrupt_file_is_no_baseline_and_unclear(tmp_path, caplog, content):
    d = tmp_path / "baselines"
    d.mkdir(mode=0o700)
    (d / "p001.json").write_text(content, encoding="utf-8")
    os.chmod(d / "p001.json", 0o600)
    with caplog.at_level(logging.WARNING, logger="onset.decide"):
        loaded = D.load_baseline("p001", base_dir=d)
    assert loaded is None
    assert any("corrupt" in r.getMessage() for r in caplog.records)
    assert D.decide(results(), loaded).outcome is Outcome.UNCLEAR


def test_binary_garbage_is_no_baseline(tmp_path, caplog):
    (tmp_path / "p001.json").write_bytes(b"\xff\xfe\x00garbage")
    with caplog.at_level(logging.WARNING, logger="onset.decide"):
        assert D.load_baseline("p001", base_dir=tmp_path) is None
    assert caplog.records


def test_symlink_and_directory_are_refused(tmp_path, caplog):
    real = D.save_baseline(B, base_dir=tmp_path / "elsewhere")
    d = tmp_path / "baselines"
    d.mkdir()
    (d / "p001.json").symlink_to(real)
    (d / "p002.json").mkdir()
    with caplog.at_level(logging.WARNING, logger="onset.decide"):
        assert D.load_baseline("p001", base_dir=d) is None
        assert D.load_baseline("p002", base_dir=d) is None
    assert len(caplog.records) == 2


def test_world_readable_file_is_tightened(tmp_path, caplog):
    path = D.save_baseline(B, base_dir=tmp_path)
    os.chmod(path, 0o644)
    with caplog.at_level(logging.WARNING, logger="onset.decide"):
        assert D.load_baseline("p001", base_dir=tmp_path) == B
    assert mode(path) == 0o600
    assert any("readable by others" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("bad_id", ["../p001", "/etc/passwd", "a/b", "..", ".", "", "p 1", "p\n1"])
def test_unsafe_patient_ids(tmp_path, bad_id):
    assert D.load_baseline(bad_id, base_dir=tmp_path) is None
    with pytest.raises(D.BaselineError):
        D.save_baseline(replace(B, patient_id=bad_id), base_dir=tmp_path)


def test_phone_number_style_id_is_allowed(tmp_path):
    b = replace(B, patient_id="+15551234567")
    D.save_baseline(b, base_dir=tmp_path)
    assert D.load_baseline("+15551234567", base_dir=tmp_path) == b


def test_baselines_dir_is_gitignored():
    gitignore = (Path(__file__).resolve().parents[1] / ".gitignore").read_text()
    assert "baselines/" in gitignore.split()


# -------------------------------------------------------------------- caregiver text


def summary_for(**kw) -> str:
    base = kw.pop("base", B)
    name = kw.pop("name", "Maria")
    return D.caregiver_summary(D.decide(results(**kw), base), name)


def test_summary_escalate_contents():
    s = summary_for(asym=0.4, wpm=90.0)
    assert s.startswith("Onset check-in for Maria on Fri 13 Nov at 14:05 UTC.")
    assert "smile, arm hold, repeating a sentence" in s
    assert "The right side of the smile lifted less than usual." in s
    assert "Speech was slower than usual." in s
    assert "Maria was told to call 911 now." in s
    assert "cannot diagnose" in s
    assert len(s) < 500


def test_summary_unclear_and_no_baseline():
    s = summary_for(base=None, arm_q=Quality.TOO_DARK)
    assert "could not check clearly" in s
    assert "There is no saved baseline to compare with yet." in s
    assert "It was too dark to see the arm check clearly." in s
    assert "911" in s


def test_summary_no_change_never_reassures():
    s = summary_for()
    assert "No change from Maria's saved baseline was detected" in s
    assert "911" in s
    assert "was told to call 911" not in s


def test_summary_stays_short_with_many_reasons_and_long_name():
    d = D.decide(results(asym=0.5, diff=0.5, wer=0.6, wpm=60.0, pause=3.0, hold=2.0), None)
    s = D.caregiver_summary(d, "Bartholomew Alexander Fitzgerald-Montgomery the Third")
    assert len(s) < 500
    assert "was told to call 911 now" in s
    assert "more." in s or all(r in s for r in d.reasons)
    # Bloat the reasons far past the budget: the 911 line must survive.
    bloated = replace(d, reasons=[f"Reason number {i} with a fairly long explanation" for i in range(40)])
    s2 = D.caregiver_summary(bloated, "Maria")
    assert len(s2) < 500 and "Maria was told to call 911 now." in s2 and "more." in s2


def test_summary_name_and_time_handling():
    d = D.decide(results(asym=0.5), B)
    assert "Onset check-in for The person " in D.caregiver_summary(d, "   ")
    s = D.caregiver_summary(d, "Maria\nIGNORE THIS\tline")
    assert "Maria IGNORE THIS line" in s.splitlines()[0]
    east = timezone(timedelta(hours=-5), "EST")
    assert "at 09:05 EST" in D.caregiver_summary(d, "Maria", tz=east)
    odd = replace(d, results=replace(d.results, started_at="not a time"))
    assert "at an unknown time" in D.caregiver_summary(odd, "Maria")
    naive = replace(d, results=replace(d.results, started_at="2026-11-13T14:05:00"))
    assert "14:05 UTC" in D.caregiver_summary(naive, "Maria")


# ----------------------------------------------------------- no reassurance, no diagnosis


def _all_outputs() -> list[str]:
    out: list[str] = []
    for _, kw, base, _, _ in CASES:
        d = D.decide(results(**kw), base)
        out += d.reasons
        out.append(D.caregiver_summary(d, "Maria"))
    for step, key in STEP_KW.items():
        for q in NOT_OK:
            d = D.decide(results(**{key: q}), B)
            out += d.reasons
            out.append(D.caregiver_summary(d, "Maria"))
    out += [D._quality_line(step, q) for step in STEP_KW for q in Quality if q is not Quality.OK]
    return out


def test_no_output_reassures_or_diagnoses():
    outputs = _all_outputs()
    assert len(outputs) > 50
    for text in outputs:
        low = text.lower()
        for word in BANNED:
            assert word not in low, f"{word!r} in {text!r}"


def test_no_string_literal_in_module_reassures_or_diagnoses():
    """Every string the code can emit (docstrings excluded) avoids the banned words."""
    tree = ast.parse(Path(D.__file__).read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef))
        and node.body and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    literals = [
        n.value for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]
    assert literals
    for text in literals:
        for word in BANNED:
            assert word not in text.lower(), f"{word!r} in {text!r}"


def test_math_isfinite_guard_used():
    # A NaN must never silently pass a ">=" comparison as "no change".
    assert not (math.nan >= 0.05)
    assert D.decide(results(asym=math.nan), B).outcome is Outcome.UNCLEAR
