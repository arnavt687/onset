"""Longitudinal history (onset/history.py), the care-team report and the dashboard endpoint.

Every test builds synthetic run archives in a tmp dir, in the format the call flow writes
(onset/events.py): runs/<run_id>/events.jsonl.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from onset import history as H
from onset.decide import DEFAULT_THRESHOLDS as T

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
BASE = {"patient_id": "p1", "recorded_at": "2026-08-31T09:00:00+00:00", "face_asymmetry": 0.05,
        "arm_drift_difference": 0.02, "speech_word_error_rate": 0.0, "speech_words_per_minute": 150.0,
        "sentence": "The sky is blue.", "samples": 3, "arm_drift_left": 0.04, "arm_drift_right": 0.02,
        "speech_longest_pause_s": 0.2}


def face(asym=0.05, quality="ok"):
    return {"quality": quality, "asymmetry": asym if quality == "ok" else None, "weaker_side": "right",
            "frames_used": 40, "detail": {}}


def arms(left=0.04, right=0.02, quality="ok", hold=10.0, **detail):
    ok = quality == "ok"
    return {"quality": quality, "left_drift": left if ok else None, "right_drift": right if ok else None,
            "drift_difference": abs(left - right) if ok else None, "weaker_side": "left", "hold_seconds": hold,
            "frames_used": 100, "detail": detail}


def speech(wer=0.0, wpm=150.0, pause=0.2, quality="ok", **detail):
    ok = quality == "ok"
    return {"quality": quality, "expected": "The sky is blue.", "transcript": "The sky is blue",
            "word_error_rate": wer if ok else None, "words_per_minute": wpm if ok else None,
            "longest_pause_s": pause if ok else None, "detail": detail}


def write_run(runs: Path, run_id: str, when: datetime | None, *, patient="p1", outcome="no_change_detected",
              f=None, a=None, s=None, baseline=BASE, reasons=None, extra_lines=(), mtime: datetime | None = None,
              started_extra=None):
    d = runs / run_id
    d.mkdir(parents=True)
    started = {"t": 0.0, "type": "run_started", "run_id": run_id, "source": "call", "patient_id": patient,
               "has_baseline": baseline is not None, "sentence": "The sky is blue.", "baseline": baseline}
    if when is not None:
        started["started_at"] = when.isoformat()
    started.update(started_extra or {})
    events = [started,
              {"t": 1.0, "type": "live", "step": "face_smile", "metrics": {"asym": 0.1}},
              {"t": 14.0, "type": "result", "check": "face", "quality": (f or face())["quality"], "data": f or face()},
              {"t": 30.0, "type": "result", "check": "arms", "quality": (a or arms())["quality"], "data": a or arms()},
              {"t": 45.0, "type": "result", "check": "speech", "quality": (s or speech())["quality"],
               "data": s or speech()}]
    if outcome == "baseline_recorded":
        events.append({"t": 46.0, "type": "step", "name": "baseline_saved", "caption": "", "seconds": None})
    elif outcome is not None:
        events.append({"t": 46.0, "type": "decision", "outcome": outcome, "reasons": reasons or [],
                       "baseline_used": True})
    events.append({"t": 48.0, "type": "run_ended", "reason": "complete"})
    with open(d / "events.jsonl", "w", encoding="utf-8") as fh:
        for e in events:
            fh.write(json.dumps(e) + "\n")
        for line in extra_lines:
            fh.write(line + "\n")
    if mtime is not None:
        os.utime(d / "events.jsonl", (mtime.timestamp(), mtime.timestamp()))
    return d


def series(runs: Path, values: list[float], key: str = "diff", start: datetime = T0, step_days: float = 1.0,
           patient="p1"):
    """One run per day. key "diff": arm drift difference = baseline 0.02 + value (the left arm
    drifts more); key "wpm": words per minute = value."""
    for i, v in enumerate(values):
        when = start + timedelta(days=i * step_days)
        kw = {}
        if key == "diff":
            kw["a"] = arms(left=0.04 + v, right=0.02)
        elif key == "wpm":
            kw["s"] = speech(wpm=v)
        write_run(runs, f"r{i:03d}", when, patient=patient, **kw)


# --------------------------------------------------------------------------- reading runs


def test_empty_history_never_raises(tmp_path):
    h = H.load_history("p1", tmp_path / "does-not-exist")
    assert h.checkins == [] and h.trends == {} and h.flags == []
    assert h.summary[0].startswith("No finished check-ins")
    d = h.to_dict()
    json.dumps(d)
    assert d["counts"]["checkins"] == 0 and d["counts"]["escalate"] == 0 and d["window"]["start"] is None
    # An existing but empty runs dir, and an invalid patient id, behave the same way.
    (tmp_path / "runs").mkdir()
    assert H.load_history("p1", tmp_path / "runs").checkins == []
    bad = H.load_history("../etc", tmp_path / "runs")
    assert bad.checkins == [] and bad.skipped[0]["reason"] == "invalid patient id"


def test_records_are_sorted_and_only_this_patient(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "b", T0 + timedelta(days=2), outcome="escalate", reasons=["Speech was slower than usual"],
              s=speech(wpm=110.0))
    write_run(runs, "a", T0, outcome="baseline_recorded", baseline=None)
    write_run(runs, "c", T0 + timedelta(days=1), outcome="unclear", a=arms(quality="partial"))
    write_run(runs, "other", T0, patient="p2")
    write_run(runs, "live", T0 + timedelta(days=2))  # the working copy of the latest run: never read
    h = H.load_history("p1", runs)
    assert [c.run_id for c in h.checkins] == ["a", "c", "b"]
    assert [c.outcome for c in h.checkins] == ["baseline_recorded", "unclear", "escalate"]
    c = h.checkins[1]
    assert c.metrics["arm_drift_difference"] is None  # never a number from a step that was not OK
    assert c.metrics["face_asymmetry"] == pytest.approx(0.05)
    assert "arms: only partly in view" in c.flags
    b = h.checkins[2]
    assert b.metrics["words_per_minute"] == 110.0 and b.reasons == ["Speech was slower than usual"]
    assert h.counts["escalate"] == 1 and h.counts["unclear"] == 1 and h.counts["baseline_recorded"] == 1
    assert h.counts["checkins"] == 3
    d = h.to_dict()["checkins"][0]
    assert d["date"] == "2026-09-01" and d["time_source"] == "event"


def test_key_metrics_and_quality_flags(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "r1", T0, outcome="unclear",
              a=arms(left=0.10, right=0.03, hold=6.5, early_drop="left"),
              s=speech(quality="partial", low_confidence=True))
    c = H.load_history("p1", runs).checkins[0]
    assert c.metrics["arm_drift_left"] == 0.10 and c.metrics["arm_drift_right"] == 0.03
    assert c.metrics["arm_drift_difference"] == pytest.approx(0.07)
    assert c.metrics["speech_wer"] is None and c.metrics["words_per_minute"] is None
    assert "arms: not held up for the full time" in c.flags
    assert "arms: one arm came down early" in c.flags
    assert "sentence: not heard clearly enough" in c.flags
    assert "sentence: some words hard to make out" in c.flags


def test_time_falls_back_to_the_archive_file(tmp_path):
    runs = tmp_path / "runs"
    end = datetime(2026, 9, 3, 10, 0, 48, tzinfo=timezone.utc)
    write_run(runs, "r1", None, mtime=end)
    c = H.load_history("p1", runs).checkins[0]
    assert c.time_source == "file"
    assert c.started_at == datetime(2026, 9, 3, 10, 0, 0, tzinfo=timezone.utc)  # mtime less the run's length


# --------------------------------------------------------------------------- trends


def test_slope_change_vs_baseline_and_week_comparison(tmp_path):
    runs = tmp_path / "runs"
    values = [0.0 + 0.002 * i for i in range(14)]  # arm drift difference 0.02 .. 0.046
    series(runs, values)
    h = H.load_history("p1", runs)
    tr = h.trends["arm_drift_difference"]
    assert tr["n"] == 14
    assert tr["baseline"] == pytest.approx(0.02) and tr["limit"] == pytest.approx(0.08)
    assert tr["slope_per_day"] == pytest.approx(0.002, abs=1e-6)
    assert tr["slope_per_week"] == pytest.approx(0.014, abs=1e-5)
    assert tr["r"] == pytest.approx(1.0)
    assert tr["latest"] == pytest.approx(0.046)
    assert tr["change_vs_baseline"] == pytest.approx(0.026)
    # The last 7 days (days 7-13) against the 7 days before (days 0-6).
    assert tr["last7"]["n"] == 7 and tr["prior7"]["n"] == 7
    assert tr["last7_minus_prior7"] == pytest.approx(0.014)
    assert tr["direction"] == "toward threshold"
    # Flat metrics show no trend.
    assert h.trends["face_asymmetry"]["direction"] == "no steady trend"
    assert h.trends["face_asymmetry"]["slope_per_day"] == pytest.approx(0.0)


def test_gradual_decline_flag_when_no_check_in_crossed(tmp_path):
    runs = tmp_path / "runs"
    # Toward the 0.08 limit (baseline 0.02 + 0.06), never reaching it, with a little noise.
    values = [0.0, 0.006, 0.008, 0.015, 0.02, 0.022, 0.03, 0.033, 0.037, 0.045]
    series(runs, values)
    h = H.load_history("p1", runs)
    tr = h.trends["arm_drift_difference"]
    assert tr["reached_limit"] == 0
    assert tr["progress"] >= 0.5
    assert tr["gradual_decline"] is True
    assert tr["days_to_limit"] is not None and tr["days_to_limit"] > 0
    flags = [f for f in h.flags if f["kind"] == "gradual_decline"]
    assert [f["metric"] for f in flags] == ["arm_drift_difference"]
    text = " ".join(h.summary)
    assert "Arm drift difference has risen steadily" in text
    assert "No single check-in reached the change threshold of 0.08" in text


def test_speaking_rate_decline_is_downward(tmp_path):
    runs = tmp_path / "runs"
    series(runs, [150, 148, 145, 143, 140, 137, 135, 132], key="wpm")  # limit 120 (80 % of 150)
    tr = H.load_history("p1", runs).trends["words_per_minute"]
    assert tr["limit"] == pytest.approx(120.0)
    assert tr["slope_per_day"] < 0 and tr["direction"] == "toward threshold"
    assert tr["gradual_decline"] is True


def test_no_gradual_decline_when_a_check_in_reached_the_threshold(tmp_path):
    runs = tmp_path / "runs"
    series(runs, [0.0, 0.01, 0.02, 0.03, 0.04, 0.06, 0.05])  # day 5: 0.08 = the limit (counts, as in decide)
    h = H.load_history("p1", runs)
    tr = h.trends["arm_drift_difference"]
    assert tr["reached_limit"] == 1 and tr["gradual_decline"] is False
    assert [f["kind"] for f in h.flags] == ["threshold_reached"]


def test_no_gradual_decline_for_noise_or_too_few_points(tmp_path):
    runs = tmp_path / "noisy"
    series(runs, [0.0, 0.045, 0.0, 0.05, 0.002, 0.048, 0.001, 0.05])  # big swings, no steady line
    tr = H.load_history("p1", runs).trends["arm_drift_difference"]
    assert tr["gradual_decline"] is False
    runs = tmp_path / "few"
    series(runs, [0.0, 0.02, 0.04, 0.05])  # steady, but only 4 check-ins
    tr = H.load_history("p1", runs).trends["arm_drift_difference"]
    assert tr["direction"] == "toward threshold" and tr["gradual_decline"] is False
    runs = tmp_path / "small"
    series(runs, [0.0, 0.002, 0.004, 0.006, 0.008, 0.01])  # steady but only a sixth of the way
    tr = H.load_history("p1", runs).trends["arm_drift_difference"]
    assert tr["progress"] < 0.5 and tr["gradual_decline"] is False


def test_rolling_window_is_90_days(tmp_path):
    runs = tmp_path / "runs"
    series(runs, [0.0] * 13, step_days=10)  # 120 days of check-ins, every 10 days
    h = H.load_history("p1", runs)
    assert h.total_checkins == 13
    assert len(h.checkins) == 10  # days 30 .. 120
    assert h.window_end - h.window_start == timedelta(days=90)
    short = H.load_history("p1", runs, settings=H.TrendSettings(window_days=30))
    assert len(short.checkins) == 4
    early = H.load_history("p1", runs, as_of=T0 + timedelta(days=25))
    assert len(early.checkins) == 3 and early.window_start == T0


def test_baseline_falls_back_to_the_saved_file(tmp_path):
    from onset.decide import save_baseline
    from onset.types import Baseline

    runs, bdir = tmp_path / "runs", tmp_path / "baselines"
    write_run(runs, "r1", T0, baseline=None, outcome="baseline_recorded")
    h = H.load_history("p1", runs)
    assert h.baseline is None and h.trends["face_asymmetry"]["limit"] is None
    assert h.trends["arm_drift_difference"]["limit"] == pytest.approx(T.arm_drift_difference_absolute)
    assert any("No baseline" in line for line in h.summary)
    save_baseline(Baseline("p1", "2026-08-30T10:00:00+00:00", 0.03, 0.01, 0.0, 140.0, "The sky is blue."),
                  base_dir=bdir)
    h = H.load_history("p1", runs, baselines_dir=bdir)
    assert h.baseline_source == "baseline file"
    assert h.trends["face_asymmetry"]["limit"] == pytest.approx(0.13)
    assert h.trends["words_per_minute"]["limit"] == pytest.approx(112.0)


def test_limits_follow_decide_thresholds():
    ref, lim, _ = H.reference_and_limit("arm_drift_difference", {"arm_drift_difference": 0.18})
    assert lim == pytest.approx(T.arm_drift_difference_absolute)  # capped by the absolute rule
    _, lim, _ = H.reference_and_limit("longest_pause_s", {"speech_longest_pause_s": 0.1})
    assert lim == pytest.approx(T.speech_pause_floor_s)
    _, lim, _ = H.reference_and_limit("longest_pause_s", {"speech_longest_pause_s": 0.6})
    assert lim == pytest.approx(0.6 + T.speech_pause_increase_s)
    _, lim, _ = H.reference_and_limit("longest_pause_s", None)
    assert lim == pytest.approx(T.speech_longest_pause_s)
    _, lim, _ = H.reference_and_limit("arm_drift_left", None)
    assert lim == pytest.approx(T.arm_single_drift_absolute)


# --------------------------------------------------------------------------- malformed archives


def test_malformed_and_partial_runs_are_skipped_and_counted(tmp_path):
    runs = tmp_path / "runs"
    write_run(runs, "good", T0)
    write_run(runs, "good2", T0 + timedelta(days=1), extra_lines=["{not json", "[1, 2]", ""])
    (runs / "garbage").mkdir()
    (runs / "garbage" / "events.jsonl").write_bytes(b"\x00\xff garbage\n{{{\n")
    (runs / "empty").mkdir()
    (runs / "empty" / "events.jsonl").write_text("")
    (runs / "dir-not-file").mkdir()
    (runs / "dir-not-file" / "events.jsonl").mkdir()
    write_run(runs, "unfinished", T0, outcome=None)  # the call dropped before a decision
    write_run(runs, "weird-outcome", T0, outcome="fine")
    write_run(runs, "nan", T0 + timedelta(days=2), f=face(asym=float("nan")),
              a={"quality": "ok", "left_drift": "x", "right_drift": True, "drift_difference": None},
              s={"quality": "ok", "data": 3})
    write_run(runs, "unfinished-other", T0, patient="p2", outcome=None)  # someone else's: not counted
    (runs / "no-events").mkdir()  # not a run at all (e.g. runs/video): ignored
    (runs / "stray.txt").write_text("x")
    odd_dir = runs / "results-not-dict"
    odd_dir.mkdir()
    (odd_dir / "events.jsonl").write_text(
        json.dumps({"t": 0, "type": "run_started", "patient_id": "p1", "run_id": "results-not-dict",
                    "started_at": "not a date"}) + "\n"
        + json.dumps({"t": 1, "type": "result", "check": "face", "quality": None, "data": "nope"}) + "\n"
        + json.dumps({"t": 2, "type": "decision", "outcome": "unclear", "reasons": "not a list"}) + "\n")
    stamp = (T0 + timedelta(days=3)).timestamp()
    os.utime(odd_dir / "events.jsonl", (stamp, stamp))

    h = H.load_history("p1", runs)
    reasons = {s["run"]: s["reason"] for s in h.skipped}
    assert reasons["garbage"] == "no run_started event"
    assert reasons["empty"] == "no run_started event"
    assert reasons["dir-not-file"] == "no events.jsonl"
    assert reasons["unfinished"].startswith("no outcome")
    assert reasons["weird-outcome"] == "unrecognised decision outcome"
    assert "unfinished-other" not in reasons and "no-events" not in reasons
    assert h.counts["skipped"] == len(h.skipped) == 5
    assert [c.run_id for c in h.checkins] == ["good", "good2", "nan", "results-not-dict"]
    nan = next(c for c in h.checkins if c.run_id == "nan")
    assert nan.metrics["face_asymmetry"] is None and nan.metrics["arm_drift_left"] is None
    assert nan.metrics["speech_wer"] is None
    good2 = next(c for c in h.checkins if c.run_id == "good2")
    assert "archive: 2 unreadable line(s) ignored" in good2.flags
    odd = next(c for c in h.checkins if c.run_id == "results-not-dict")
    assert odd.time_source == "file" and odd.reasons == [] and odd.quality["face"] == "error"
    assert any("could not be used" in line for line in h.summary)
    json.dumps(h.to_dict())


def test_load_history_never_raises_even_if_reading_blows_up(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    write_run(runs, "r1", T0)

    def boom(*a, **k):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(H, "_metric_trend", boom)
    h = H.load_history("p1", runs)
    assert h.checkins == [] and "history could not be built" in h.skipped[-1]["reason"]
    monkeypatch.setattr(H, "read_run", boom)
    h = H.load_history("p1", runs)
    assert h.skipped[0] == {"run": "r1", "reason": "unreadable (RuntimeError)"}


# --------------------------------------------------------------------------- report, demo, dashboard


def test_report_html_and_json(tmp_path, capsys):
    from tools import history_report

    runs = tmp_path / "runs"
    series(runs, [0.0, 0.006, 0.008, 0.015, 0.02, 0.022, 0.03, 0.033, 0.037, 0.045])
    write_run(runs, "esc", T0 + timedelta(days=10, hours=1), outcome="escalate",
              reasons=["The <left> arm drifted down more than usual"], a=arms(left=0.09, right=0.02))
    out = tmp_path / "r.html"
    assert history_report.main(["--patient", "p1", "--runs", str(runs), "--out", str(out)]) == 0
    page = out.read_text(encoding="utf-8")
    assert (out.stat().st_mode & 0o777) == 0o600
    assert page.startswith("<!doctype html>") and "<title>Onset Check-in History</title>" in page
    assert page.count("<svg") == len(H.METRICS) and 'class="sp-limit"' in page
    assert "Gradual decline: Arm drift difference" in page
    assert "&lt;left&gt;" in page and "<left>" not in page  # reasons are escaped
    assert "http://" not in page and "https://" not in page and "<script" not in page  # self-contained
    assert "not a diagnosis" in page

    assert history_report.main(["--patient", "p1", "--runs", str(runs), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert d["patient_id"] == "p1" and len(d["checkins"]) == 11 and d["counts"]["escalate"] == 1
    js = tmp_path / "r.json"
    history_report.main(["--patient", "p1", "--runs", str(runs), "--out", str(js)])
    assert json.loads(js.read_text())["flags"]

    with pytest.raises(SystemExit):
        history_report.main(["--patient", "../x", "--runs", str(runs)])


def test_report_for_an_empty_history(tmp_path, capsys):
    from tools import history_report

    assert history_report.main(["--patient", "nobody", "--runs", str(tmp_path)]) == 0
    page = capsys.readouterr().out
    assert "No finished check-ins are archived" in page and "No check-ins in this window" in page


def test_demo_history_writes_a_two_week_story(tmp_path):
    from tools import demo_history

    dirs = demo_history.write_demo(tmp_path, days=14, end_day=datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert len(dirs) == 14
    h = H.load_history(demo_history.PATIENT, tmp_path)
    assert len(h.checkins) == 14 and not h.skipped
    assert h.counts["baseline_recorded"] == 1 and h.counts["escalate"] == 1 and h.counts["unclear"] == 1
    declines = {f["metric"] for f in h.flags if f["kind"] == "gradual_decline"}
    assert declines == {"arm_drift_difference", "words_per_minute"}
    # Re-running replaces the same days instead of adding more.
    demo_history.write_demo(tmp_path, days=14, end_day=datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert len(H.load_history(demo_history.PATIENT, tmp_path).checkins) == 14


def test_dashboard_history_endpoint(tmp_path):
    from aiohttp.test_utils import TestClient, TestServer

    from tools.dashboard import make_app

    series(tmp_path, [0.0, 0.006, 0.008, 0.015, 0.02, 0.022, 0.03, 0.033, 0.037, 0.045])

    async def go():
        client = TestClient(TestServer(make_app(tmp_path, tmp_path / "baselines")))
        await client.start_server()
        try:
            r = await client.get("/api/history", params={"patient": "p1"})
            assert r.status == 200 and "no-store" in r.headers["Cache-Control"]
            d = await r.json()
            assert len(d["checkins"]) == 10 and "artifacts" not in d["checkins"][0]
            assert d["trends"]["arm_drift_difference"]["gradual_decline"] is True
            r = await client.get("/api/history", params={"patient": "nobody"})
            assert r.status == 200 and (await r.json())["checkins"] == []
            assert (await client.get("/api/history", params={"patient": "../p1"})).status == 400
            assert (await client.get("/api/history")).status == 400
            page = await (await client.get("/")).text()
            assert 'id="trend"' in page and "/api/history?patient=" in page and "Click to enable sound" in page
        finally:
            await client.close()

    asyncio.run(go())
