"""The caption overlay's look: alert styling, caption entrance, progress pill, balanced lines."""

import numpy as np

from onset import overlay, script


def test_alert_only_for_call_911_now():
    assert overlay.is_alert(script.CLOSE_ESCALATE.caption)
    assert overlay.is_alert(script.CLOSE_UNCLEAR.caption)
    for line in (script.CLOSE_NO_CHANGE, script.CLOSE_BASELINE_SAVED, script.GREETING, script.FACE_SMILE):
        assert not overlay.is_alert(line.caption), line


def test_caption_settles_to_the_static_layer():
    text = script.ARMS_RAISE.caption
    layer, _ = overlay.caption_layer(text, 640, 360, "bottom")
    want = overlay.blank(640, 360)
    overlay.composite(want, layer)
    start = overlay.blank(640, 360)
    overlay.draw_caption(start, text, "bottom", 10.0)  # first frame of this caption: still entering
    assert not np.array_equal(start, want)
    done = overlay.blank(640, 360)
    overlay.draw_caption(done, text, "bottom", 10.0 + overlay.ENTER_S + 0.01)
    assert np.array_equal(done, want)


def test_pill_marks_progress():
    one = overlay.pill_layer("Step 1 of 3 · Smile", 640, 360, "top")
    three = overlay.pill_layer("Step 3 of 3 · Sentence", 640, 360, "top")
    teal = lambda img: int(np.all(img[..., :3] == overlay.GLOW, axis=-1).sum())  # noqa: E731
    assert teal(three) > teal(one) > 0


def test_lines_are_balanced():
    _, info = overlay.caption_layer(script.CLOSE_NO_CHANGE.caption, 640, 360, "bottom")
    lens = [len(x) for x in info["lines"]]
    assert len(lens) <= overlay.MAX_LINES and min(lens) >= 0.4 * max(lens), info["lines"]
