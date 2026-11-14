"""Piece 4: baselines and the decision rules.

Owner: the `decide` session. See ROADMAP.md, piece 4.

The decision is rules, not a model: any measured change beyond its threshold, and any
step whose quality is not OK, gives ESCALATE or UNCLEAR. Both mean "call 911 now".
"""

from __future__ import annotations

from pathlib import Path

from onset.types import Baseline, CheckResults, Decision

BASELINE_DIR = Path("baselines")  # gitignored; files are owner-only (0600)


def load_baseline(patient_id: str) -> Baseline | None:
    raise NotImplementedError


def save_baseline(baseline: Baseline) -> None:
    raise NotImplementedError


def baseline_from(results: list[CheckResults], patient_id: str) -> Baseline:
    """Build a baseline from one or more check-ins recorded while the person was well."""
    raise NotImplementedError


def decide(results: CheckResults, baseline: Baseline | None) -> Decision:
    """No baseline, or any unclear step, is never NO_CHANGE_DETECTED."""
    raise NotImplementedError


def caregiver_summary(decision: Decision, patient_name: str) -> str:
    """The iMessage text for the caregiver: what was checked, what changed, the time. No diagnosis."""
    raise NotImplementedError
