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


# ---------------------------------------------------------------- upright frame, "fill" lines (issue #27)

SENTENCE = "You can't teach an old dog new tricks."


def test_output_is_upright_by_default():
    assert overlay.output_size({}) == (540, 960)
    assert overlay.output_size({"OUT_WIDTH": "640", "OUT_HEIGHT": "360"}) == (540, 960)  # an old .env.local
    assert overlay.output_size({"OUT_WIDTH": "1280", "OUT_HEIGHT": "720"}) == (720, 1280)
    assert overlay.output_size({"OUT_WIDTH": "720", "OUT_HEIGHT": "1280"}) == (720, 1280)
    assert overlay.output_size({"OUT_WIDTH": "1280", "OUT_HEIGHT": "720", "ONSET_FRAME": "landscape"}) == (1280, 720)
    assert overlay.output_size({"OUT_WIDTH": "wide", "OUT_HEIGHT": ""}) == (540, 960)


def test_safe_box_is_the_centred_3_by_4_region():
    assert overlay.safe_box(640, 360) == (0, 360)  # wide frames keep the old layout
    assert overlay.safe_box(540, 720) == (0, 720)  # already 3:4
    top, bottom = overlay.safe_box(540, 960)
    assert (top, bottom) == (120, 840) and (bottom - top) * 3 == 540 * 4


def test_every_caption_survives_a_fill_crop():
    """In the upright frame every caption, pill and bar sits inside the safe box, whole and unshortened."""
    w, h = overlay.output_size({})
    top, bottom = overlay.safe_box(w, h)
    lines = script.ALL_LINES + [script.sentence_prompt(SENTENCE), script.sentence_go(SENTENCE)]
    for line in lines:
        for pos in ("top", "bottom"):
            layer, info = overlay.caption_layer(line.caption, w, h, pos)
            assert info["cap"] >= overlay.MIN_CAP_PX, (line.caption, info)
            assert not info["lines"][-1].endswith("…"), line.caption
            assert " ".join(info["lines"]) == " ".join(line.caption.split()), line.caption
            assert top <= info["box"][1] and info["box"][3] <= bottom, (line.caption, info)
            words = np.all(layer[..., :3] == overlay.TEXT, axis=-1) & (layer[..., 3] == 255)
            rows, cols = np.flatnonzero(words.any(axis=1)), np.flatnonzero(words.any(axis=0))
            assert top <= rows[0] and rows[-1] < bottom, line.caption
            assert 0 < cols[0] and cols[-1] < w - 1, line.caption  # not touching either side
    for pos in ("top", "bottom"):
        pill = overlay.pill_layer("Step 3 of 3 · Sentence", w, h, pos)
        rows = np.flatnonzero(pill[..., 3].any(axis=1))
        assert top <= rows[0] and rows[-1] < bottom
        img = overlay.blank(w, h)
        before = img.copy()
        overlay.countdown_bar(img, 0.5, pos)
        rows = np.flatnonzero((img != before).any(axis=(1, 2)))
        assert top <= rows[0] and rows[-1] < bottom


def test_composite_matches_a_whole_frame_blend():
    rng = np.random.default_rng(3)
    base = rng.integers(0, 256, (360, 640, 4), dtype=np.uint8)
    layer, _ = overlay.caption_layer(script.ARMS_RAISE.caption, 640, 360, "bottom")
    a = layer[..., 3:4].astype(np.uint16)
    want = base.copy()
    want[..., :3] = ((base[..., :3].astype(np.uint16) * (255 - a) + layer[..., :3].astype(np.uint16) * a) // 255).astype(np.uint8)
    want[..., 3] = 255
    got = base.copy()
    overlay.composite(got, layer)
    assert np.array_equal(got, want)
    untouched = base.copy()
    overlay.composite(untouched, np.zeros_like(layer))  # an empty layer changes nothing
    assert np.array_equal(untouched, base)
