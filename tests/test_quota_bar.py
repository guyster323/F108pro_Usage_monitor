from __future__ import annotations

from quotadeck.renderer.canvas import (
    BAR_BG,
    BAR_TEXT_ON_EMPTY,
    BAR_TEXT_ON_FILL,
    draw_split_quota_bar,
    new_canvas,
    quota_bar_label,
)


def test_quota_bar_label_is_compact() -> None:
    assert quota_bar_label("5H", 72) == "5H 72%"
    assert quota_bar_label("WEEKLY", 54) == "WEEKLY 54%"
    assert quota_bar_label("AUTO", None) == "AUTO --%"


def test_text_switches_color_at_fill_boundary() -> None:
    image = new_canvas()
    box = (0, 0, 99, 49)
    fill = (8, 93, 74)
    boundary = draw_split_quota_bar(image, box, 50, "5H", fill)
    assert boundary == 50

    filled_text = 0
    empty_text = 0
    for y in range(2, 48):
        for x in range(2, 98):
            pixel = image.getpixel((x, y))
            if x < boundary and pixel == BAR_TEXT_ON_FILL:
                filled_text += 1
            if x >= boundary and pixel == BAR_TEXT_ON_EMPTY:
                empty_text += 1
    assert filled_text > 0
    assert empty_text > 0
    assert image.getpixel((3, 18)) == fill
    assert image.getpixel((96, 18)) == BAR_BG


def test_zero_and_full_quota_use_the_whole_track() -> None:
    empty = new_canvas()
    full = new_canvas()
    box = (0, 0, 99, 49)
    fill = (8, 93, 74)
    assert draw_split_quota_bar(empty, box, 0, "5H", fill) == 2
    assert draw_split_quota_bar(full, box, 100, "5H", fill) == 98
    assert empty.getpixel((3, 18)) == BAR_BG
    assert full.getpixel((96, 18)) == fill


def test_non_finite_quota_degrades_to_no_data() -> None:
    for value in (float("nan"), float("inf"), float("-inf"), True, False, 10**10000):
        image = new_canvas()
        assert draw_split_quota_bar(image, (0, 0, 99, 49), value, "5H", (8, 93, 74)) == 2
        assert quota_bar_label("5H", value) == "5H --%"
