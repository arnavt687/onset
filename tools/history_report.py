"""Care-team summary of one person's check-ins over time (HTML or JSON).

    ~/onset/.venv/bin/python tools/history_report.py --patient <id> [--out report.html]
    ~/onset/.venv/bin/python tools/history_report.py --patient <id> --json [--out report.json]

Reads the archived runs (runs/<run_id>/events.jsonl) through onset/history.py and writes one
self-contained file: no scripts, fonts or images from anywhere else, so it can be attached
to an email or printed. Without --out the report goes to stdout. A file written with --out
is owner-only (0600): it holds health measurements.

The HTML has a plain-language summary, the trend flags, a sparkline per measurement with
its baseline and change threshold, and a table of every check-in in the window. It
describes measurements only and never names a condition.
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from onset.history import (  # noqa: E402
    DEFAULT_BASELINES,
    DEFAULT_RUNS,
    DEFAULT_SETTINGS,
    METRICS,
    OUTCOME_LABELS,
    History,
    TrendSettings,
    fmt_value,
    load_history,
    valid_patient_id,
)

OUTCOME_CLASS = {"escalate": "esc", "unclear": "unc", "no_change_detected": "ncd", "baseline_recorded": "base"}


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _parse(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except ValueError:
        return None


def _when(iso: str | None) -> str:
    t = _parse(iso)
    return f"{t:%a %d %b %Y, %H:%M} UTC" if t else "–"


def _short(iso: str | None) -> str:
    t = _parse(iso)
    return f"{t:%a %d %b, %H:%M}" if t else "–"


def _day(iso: str | None) -> str:
    t = _parse(iso)
    return f"{t:%d %b}" if t else "–"


# --------------------------------------------------------------------------- sparkline


def sparkline(tr: dict, start: str | None, end: str | None, width: int = 280, height: int = 70) -> str:
    """Inline SVG: the values over time, the baseline (dotted) and the change threshold (dashed)."""
    pts = [(p["t"], p["v"], p["outcome"]) for p in tr.get("points", []) if p.get("v") is not None]
    label = f"{tr['label']}: {len(pts)} measured check-ins"
    if not pts:
        return (f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}">'
                f'<text x="{width / 2}" y="{height / 2 + 4}" text-anchor="middle" class="sp-empty">no measurements</text></svg>')
    t0, t1 = _parse(start), _parse(end)
    times = [_parse(t) for t, _, _ in pts]
    if t0 is None or t1 is None:
        t0, t1 = times[0], times[-1]
    span = max(1.0, (t1 - t0).total_seconds())
    ref, limit = tr.get("baseline"), tr.get("limit")
    vals = [v for _, v, _ in pts] + [x for x in (ref, limit) if x is not None]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1e-9:
        pad = abs(hi) * 0.1 or 0.05
        lo, hi = lo - pad, hi + pad
    pad = (hi - lo) * 0.1
    lo, hi = lo - pad, hi + pad
    left, right, top, bottom = 6, width - 6, 6, height - 6

    def x(t: datetime | None) -> float:
        frac = 0.5 if t is None else min(1.0, max(0.0, (t - t0).total_seconds() / span))
        return left + frac * (right - left)

    def y(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * (bottom - top)

    parts = [f'<svg class="spark" viewBox="0 0 {width} {height}" role="img" aria-label="{_e(label)}">']
    if limit is not None:
        parts.append(f'<line class="sp-limit" x1="{left}" x2="{right}" y1="{y(limit):.1f}" y2="{y(limit):.1f}">'
                     f'<title>change threshold {_e(fmt_value(tr["key"], limit))} ({_e(tr["limit_rule"])})</title></line>')
    if ref is not None:
        parts.append(f'<line class="sp-base" x1="{left}" x2="{right}" y1="{y(ref):.1f}" y2="{y(ref):.1f}">'
                     f'<title>baseline {_e(fmt_value(tr["key"], ref))}</title></line>')
    if tr.get("direction") in ("toward threshold", "away from threshold") and tr.get("fitted_start") is not None:
        parts.append(f'<line class="sp-fit" x1="{x(times[0]):.1f}" x2="{x(times[-1]):.1f}" '
                     f'y1="{y(tr["fitted_start"]):.1f}" y2="{y(tr["fitted_latest"]):.1f}"/>')
    if len(pts) > 1:
        line = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, (_, v, _) in zip(times, pts))
        parts.append(f'<polyline class="sp-line" points="{line}"/>')
    for t, (iso, v, outcome) in zip(times, pts):
        cls = OUTCOME_CLASS.get(outcome, "ncd")
        parts.append(f'<circle class="sp-pt {cls}" cx="{x(t):.1f}" cy="{y(v):.1f}" r="2.6">'
                     f'<title>{_e(_day(iso))}: {_e(fmt_value(tr["key"], v))} ({_e(OUTCOME_LABELS.get(outcome, outcome))})</title></circle>')
    parts.append("</svg>")
    return "".join(parts)


# --------------------------------------------------------------------------- HTML

CSS = """
:root { --ink:#13202d; --muted:#5b6b7a; --line:#d9e0e6; --soft:#f4f7f9; --red:#c8321f; --amber:#b86200;
  --teal:#0f766e; --bg:#ffffff; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 "Segoe UI", system-ui, -apple-system, sans-serif; }
main { max-width: 1080px; margin: 0 auto; padding: 28px 20px 40px; }
h1 { font-size: 24px; margin: 0 0 2px; letter-spacing: -0.01em; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--teal); margin: 28px 0 10px; }
.meta { color: var(--muted); }
.note { border: 1px solid var(--line); border-left: 4px solid var(--amber); background: #fffaf3; padding: 10px 14px; border-radius: 6px; margin: 16px 0; }
.summary li { margin: 4px 0; }
.counts { display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0 0; }
.count { border: 1px solid var(--line); border-radius: 8px; padding: 8px 12px; min-width: 120px; }
.count b { display: block; font-size: 22px; }
.count.esc b { color: var(--red); } .count.unc b { color: var(--amber); }
.flags { display: grid; gap: 8px; }
.flag { border: 1px solid var(--line); border-left: 4px solid var(--red); border-radius: 6px; padding: 10px 14px; background: #fff6f4; }
.flag.reached { border-left-color: var(--amber); background: #fffaf3; }
.flag b { display: block; }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 12px; }
.card { border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; }
.card.decline { border-color: var(--red); box-shadow: inset 4px 0 0 var(--red); }
.card h3 { font-size: 15px; margin: 0; display: flex; justify-content: space-between; gap: 8px; }
.card h3 small { color: var(--muted); font-weight: 400; font-size: 12px; }
.tag { font-size: 11px; font-weight: 600; color: #fff; background: var(--red); border-radius: 10px; padding: 1px 8px; white-space: nowrap; }
.card dl { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 6px 0 0; font-size: 13px; }
.card dt { color: var(--muted); } .card dd { margin: 0; font-variant-numeric: tabular-nums; }
.spark { width: 100%; height: 70px; display: block; margin-top: 6px; background: var(--soft); border-radius: 6px; }
.sp-line { fill: none; stroke: var(--ink); stroke-width: 1.4; }
.sp-fit { stroke: var(--teal); stroke-width: 1; opacity: .7; }
.sp-limit { stroke: var(--red); stroke-width: 1.2; stroke-dasharray: 5 3; }
.sp-base { stroke: var(--muted); stroke-width: 1; stroke-dasharray: 1 3; }
.sp-pt { fill: var(--ink); } .sp-pt.esc { fill: var(--red); } .sp-pt.unc { fill: var(--amber); } .sp-pt.base { fill: var(--teal); }
.sp-empty { fill: var(--muted); font-size: 12px; }
.legend { color: var(--muted); font-size: 12px; margin: 0 0 8px; }
.legend span { margin-right: 14px; white-space: nowrap; }
.legend i { display: inline-block; width: 18px; height: 0; vertical-align: middle; margin-right: 4px; border-top: 2px dashed var(--red); }
.legend i.base { border-top: 2px dotted var(--muted); } .legend i.fit { border-top: 1px solid var(--teal); }
.table-wrap { overflow-x: auto; border: 1px solid var(--line); border-radius: 8px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { padding: 6px 8px; border-bottom: 1px solid var(--line); text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; vertical-align: top; }
th { background: var(--soft); font-weight: 600; position: sticky; top: 0; }
th:first-child, td:first-child, th.l, td.l { text-align: left; }
td.notes { white-space: normal; min-width: 220px; color: var(--muted); }
tr:last-child td { border-bottom: 0; }
.out { font-weight: 600; } .out.esc { color: var(--red); } .out.unc { color: var(--amber); } .out.base { color: var(--teal); }
td.hit { color: var(--red); font-weight: 600; }
footer { color: var(--muted); font-size: 12px; margin-top: 28px; border-top: 1px solid var(--line); padding-top: 12px; }
@media print { main { padding: 0; } .card, .flag { break-inside: avoid; } th { position: static; } }
"""


def _metric_card(tr: dict, start: str | None, end: str | None) -> str:
    key = tr["key"]
    f = lambda v: fmt_value(key, v)  # noqa: E731

    def signed(v: float | None, extra: int = 0) -> str:
        if v is None:
            return "–"
        text = fmt_value(key, abs(v), extra)
        if float(text) == 0:
            return text
        return ("+" if v > 0 else "−") + text

    slope = tr.get("slope_per_week")
    rows = [
        ("Latest", f(tr.get("latest"))),
        ("Baseline", f(tr.get("baseline"))),
        ("Change threshold", f"{f(tr.get('limit'))} <small>({_e(tr.get('limit_rule') or 'none')})</small>"
         if tr.get("limit") is not None else "none without a baseline"),
        ("Change vs baseline", signed(tr.get("change_vs_baseline"))),
        ("Trend", f"{_e(tr.get('direction'))}" + (f", {signed(slope, 1)} per week (r = {abs(tr['r']):.2f})"
                                                   if slope is not None and tr.get("r") is not None else "")),
        ("Last 7 d vs prior 7 d",f"{f(tr['last7']['mean'])} vs {f(tr['prior7']['mean'])}"
         f" <small>(n = {tr['last7']['n']} / {tr['prior7']['n']})</small>"),
        ("Reached threshold", f"{tr.get('reached_limit', 0)} of {tr.get('n', 0)} check-ins"),
    ]
    if tr.get("days_to_limit") is not None and tr.get("direction") == "toward threshold":
        rows.append(("At this rate", f"threshold in about {tr['days_to_limit']:.0f} days"))
    dl = "".join(f"<dt>{_e(k)}</dt><dd>{v}</dd>" for k, v in rows)
    tag = '<span class="tag">gradual decline</span>' if tr.get("gradual_decline") else ""
    cls = "card decline" if tr.get("gradual_decline") else "card"
    return (f'<section class="{cls}"><h3><span>{_e(tr["label"])} <small>{_e(tr["unit"])}</small></span>{tag}</h3>'
            f'{sparkline(tr, start, end)}<dl>{dl}</dl></section>')


def _table(d: dict) -> str:
    limits = {k: (tr.get("limit"), tr.get("worse")) for k, tr in d["trends"].items()}
    head = ("<tr><th>Date (UTC)</th><th class=l>Outcome</th><th>Smile asym.</th><th>Arm drift L</th><th>Arm drift R</th>"
            "<th>Arm diff.</th><th>WER</th><th>Words/min</th><th>Longest pause</th><th class=l>Notes</th></tr>")
    body = []
    for c in reversed(d["checkins"]):  # newest first
        cells = []
        for key in ("face_asymmetry", "arm_drift_left", "arm_drift_right", "arm_drift_difference", "speech_wer",
                    "words_per_minute", "longest_pause_s"):
            v = c["metrics"].get(key)
            lim, worse = limits.get(key, (None, None))
            hit = v is not None and lim is not None and (v >= lim - 1e-9 if worse == "up" else v <= lim + 1e-9)
            cells.append(f'<td class="{"hit" if hit else ""}">{_e(fmt_value(key, v))}</td>')
        notes = list(c["flags"])
        if c["outcome"] in ("escalate", "unclear"):
            notes = [r for r in c["reasons"]] + notes
        if c.get("time_source") == "file":
            notes.append("time from the archive file")
        cls = OUTCOME_CLASS.get(c["outcome"], "")
        body.append(f'<tr><td>{_e(_short(c["started_at"]))}</td><td class="l out {cls}">{_e(c["outcome_label"])}</td>'
                    f'{"".join(cells)}<td class="notes">{_e("; ".join(notes)) or "–"}</td></tr>')
    if not body:
        body.append('<tr><td colspan="10" class="l">No check-ins in this window.</td></tr>')
    return f'<div class="table-wrap"><table>{head}{"".join(body)}</table></div>'


def render_html(h: History) -> str:
    d = h.to_dict()
    w = d["window"]
    c = d["counts"]
    counts = "".join(
        f'<div class="count {cls}"><b>{c.get(key, 0)}</b>{_e(label)}</div>'
        for key, cls, label in (("checkins", "", "check-ins in window"), ("escalate", "esc", "change detected"),
                                ("unclear", "unc", "unclear"), ("no_change_detected", "", "no change detected"),
                                ("baseline_recorded", "", "baseline recorded"))
    ) if d["checkins"] else ""
    flags = "".join(
        f'<div class="flag {"reached" if f["kind"] == "threshold_reached" else ""}">'
        f'<b>{_e("Gradual decline" if f["kind"] == "gradual_decline" else "Threshold reached")}: {_e(f["label"])}</b>'
        f'{_e(f["text"])}</div>' for f in d["flags"]
    ) or "<p>No trend flags in this window.</p>"
    cards = "".join(_metric_card(d["trends"][m.key], w["start"], w["end"]) for m in METRICS if m.key in d["trends"])
    b = d["baseline"]
    base_txt = (f"Baseline from {_e(d['baseline_source'])}" + (f", recorded {_e(_when(b.get('recorded_at')))}"
                                                                if b.get("recorded_at") else "")
                + (f", mean of {int(b['samples'])} check-ins" if b.get("samples") and b["samples"] > 1 else "")
                ) if b else "No baseline available"
    skipped = ""
    if d["skipped"]:
        items = "".join(f"<li><code>{_e(s['run'])}</code>: {_e(s['reason'])}</li>" for s in d["skipped"][:50])
        more = f"<li>and {len(d['skipped']) - 50} more</li>" if len(d["skipped"]) > 50 else ""
        skipped = f"<h2>Skipped archives</h2><ul>{items}{more}</ul>"
    s = d["settings"]
    window = (f"{_e(_when(w['start']))} to {_e(_when(w['end']))}" if w["start"] else "no check-ins yet")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Onset Check-in History</title>
<style>{CSS}</style>
</head>
<body>
<main>
<h1>Onset check-in history</h1>
<div class="meta">Patient ID <b>{_e(d["patient_id"])}</b> · {window} · {_e(base_txt)} · generated {_e(_when(d["generated_at"]))}</div>
<div class="note">{_e(d["disclaimer"])}</div>

<h2>Summary</h2>
<ul class="summary">{"".join(f"<li>{_e(x)}</li>" for x in d["summary"] if x != d["disclaimer"])}</ul>
<div class="counts">{counts}</div>

<h2>Trend flags</h2>
<div class="flags">{flags}</div>

<h2>Measurements over time</h2>
<p class="legend"><span><i></i>change threshold (onset/decide.py)</span><span><i class="base"></i>baseline</span>
<span><i class="fit"></i>least-squares trend</span><span>points: dark = no change detected, red = change detected,
amber = unclear, teal = baseline</span></p>
<div class="grid">{cards or "<p>No measurements yet.</p>"}</div>

<h2>Check-ins (newest first)</h2>
{_table(d)}
{skipped}
<footer>
Numbers come from each check-in's archived events (runs/&lt;run&gt;/events.jsonl). A number from a step that was
not measured cleanly is left out, exactly as the decision rules never use it. Change thresholds follow the main
per-measurement rule in onset/decide.py (docs/thresholds.md); the full rules also check side switching and word
counts. Gradual decline: at least {s["min_points"]} measured check-ins, a least-squares trend toward the
threshold with |r| ≥ {s["min_correlation"]:g}, the fitted latest value at least {s["min_progress"]:.0%} of the way from the
baseline to the threshold, and no check-in at or past it. Window: up to {s["window_days"]:g} days from the first
check-in; the comparison is the last {s["compare_days"]:g} days against the {s["compare_days"]:g} days before.
</footer>
</main>
</body>
</html>
"""


# --------------------------------------------------------------------------- CLI


def _write(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(text)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Care-team summary of one person's Onset check-ins over time.")
    ap.add_argument("--patient", required=True, help="patient id, as in baselines/<id>.json")
    ap.add_argument("--out", type=Path, help="write here (.json implies --json); default: stdout")
    ap.add_argument("--json", action="store_true", help="JSON instead of HTML")
    ap.add_argument("--runs", type=Path, default=DEFAULT_RUNS, help="runs directory (default: %(default)s)")
    ap.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES, help="baselines directory (default: %(default)s)")
    ap.add_argument("--window-days", type=float, default=DEFAULT_SETTINGS.window_days,
                    help="rolling window from the first check-in (default: %(default)s)")
    ap.add_argument("--as-of", help="end of the window, ISO 8601 (default: the latest check-in)")
    args = ap.parse_args(argv)
    if not valid_patient_id(args.patient):
        ap.error("patient id must be 1-128 letters, digits, '_', '+', '-' or '.'")
    as_of = None
    if args.as_of:
        try:
            as_of = datetime.fromisoformat(args.as_of)
        except ValueError:
            ap.error("--as-of must be an ISO 8601 date or time")
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=timezone.utc)
    if args.window_days <= 0:
        ap.error("--window-days must be above 0")
    settings = TrendSettings(**{**DEFAULT_SETTINGS.__dict__, "window_days": args.window_days})
    h = load_history(args.patient, args.runs, baselines_dir=args.baselines, as_of=as_of, settings=settings)
    as_json = args.json or (args.out is not None and args.out.suffix.lower() == ".json")
    text = json.dumps(h.to_dict(), indent=2) + "\n" if as_json else render_html(h)
    if args.out is None:
        sys.stdout.write(text)
    else:
        _write(args.out, text)
        print(f"Wrote {'JSON' if as_json else 'HTML'} history for {args.patient} "
              f"({len(h.checkins)} check-ins, {len(h.flags)} flags) to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
