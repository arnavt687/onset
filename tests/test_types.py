"""The shared contracts import and hold the safety rule."""

from onset import types


def test_outcomes_have_no_all_clear():
    names = {o.value for o in types.Outcome}
    assert names == {"escalate", "unclear", "no_change_detected"}
    assert not any(word in " ".join(names) for word in ("fine", "normal", "healthy"))


def test_modules_import():
    from onset import chert_api, decide, speech  # noqa: F401
    from onset.measure import arms, face  # noqa: F401
