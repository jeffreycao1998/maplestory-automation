import numpy as np
import pytest

from maplebot.perception import calib
from maplebot.settings import Calibration

from perception_synth import HP_BOX, MINIMAP_BOX, MP_BOX, background, draw_bar, draw_minimap

DEF = Calibration()
LOW, HIGH = DEF.player_dot_hsv_low, DEF.player_dot_hsv_high


@pytest.mark.parametrize("fx,fy", [(0.1, 0.2), (0.42, 0.61), (0.5, 0.5), (0.93, 0.85), (0.27, 0.07)])
def test_dot_position_accuracy(fx, fy):
    img = background()
    draw_minimap(img, dot=(fx, fy))
    p = calib.minimap_player_position(img, MINIMAP_BOX, LOW, HIGH)
    assert p is not None
    # within half a minimap pixel
    assert abs(p[0] - fx) <= 0.5 / MINIMAP_BOX[2] + 1e-6
    assert abs(p[1] - fy) <= 0.5 / MINIMAP_BOX[3] + 1e-6


def test_missing_dot_returns_none():
    img = background()
    draw_minimap(img, dot=None)
    assert calib.minimap_player_position(img, MINIMAP_BOX, LOW, HIGH) is None


def test_single_pixel_noise_and_huge_blob_ignored():
    img = background()
    draw_minimap(img, dot=None)
    x, y, w, h = MINIMAP_BOX
    img[y + 5, x + 5] = (0, 230, 255)                      # 1-px noise
    assert calib.minimap_player_position(img, MINIMAP_BOX, LOW, HIGH) is None
    img[y + 40:y + 100, x + 20:x + 120] = (0, 230, 255)    # 25% of the minimap: not a dot
    assert calib.minimap_player_position(img, MINIMAP_BOX, LOW, HIGH) is None
    draw_minimap(img, dot=(0.7, 0.3))
    img[y + 60:y + 100, x + 20:x + 120] = (0, 230, 255)
    p = calib.minimap_player_position(img, MINIMAP_BOX, LOW, HIGH)
    assert p is not None and abs(p[0] - 0.7) < 0.01 and abs(p[1] - 0.3) < 0.01


@pytest.mark.parametrize("box", [None, [0, 0, 0, 10], [1300, 700, 200, 200], [-5, 0, 10, 10], "junk"])
def test_minimap_invalid_box(box):
    img = background()
    assert calib.minimap_player_position(img, box, LOW, HIGH) is None


def test_hsv_range_from_sample_catches_dot():
    img = background()
    draw_minimap(img, dot=(0.6, 0.4), dot_r=2)
    x, y, w, h = MINIMAP_BOX
    cx, cy = int(x + 0.6 * w), int(y + 0.4 * h)
    for dx, dy in [(0, 0), (1, 0), (0, -1)]:     # slightly off-centre clicks too
        lo, hi = calib.hsv_range_from_sample(img, cx + dx, cy + dy)
        assert all(0 <= v <= 179 for v in (lo[0], hi[0]))
        assert all(0 <= v <= 255 for v in lo[1:] + hi[1:])
        assert lo[0] <= hi[0]
        p = calib.minimap_player_position(img, MINIMAP_BOX, lo, hi)
        assert p is not None and abs(p[0] - 0.6) < 0.01 and abs(p[1] - 0.4) < 0.01


def test_hsv_range_from_sample_custom_colour_and_tolerance():
    img = background()
    x, y, w, h = MINIMAP_BOX
    img[y:y + h, x:x + w] = (40, 30, 30)
    img[y + 50:y + 54, x + 100:x + 104] = (60, 200, 60)    # green dot (another server's skin)
    lo, hi = calib.hsv_range_from_sample(img, x + 101, y + 51)
    p = calib.minimap_player_position(img, MINIMAP_BOX, lo, hi)
    assert p is not None and abs(p[0] - 102 / w) < 0.01
    # tolerant: a slightly darker/less saturated version of the dot is still caught
    img[y + 50:y + 54, x + 100:x + 104] = (70, 170, 70)
    assert calib.minimap_player_position(img, MINIMAP_BOX, lo, hi) is not None
    # ...but the dark minimap background is not
    assert calib.minimap_player_position(img, MINIMAP_BOX, lo, hi) is not None
    img[y + 50:y + 54, x + 100:x + 104] = (40, 30, 30)
    assert calib.minimap_player_position(img, MINIMAP_BOX, lo, hi) is None


def test_hsv_range_from_sample_bad_input():
    lo, hi = calib.hsv_range_from_sample(None, 3, 3)
    assert len(lo) == 3 and len(hi) == 3
    lo, hi = calib.hsv_range_from_sample(background(), 99999, -4)
    assert len(lo) == 3


@pytest.mark.parametrize("fill", [0.0, 0.03, 0.25, 0.5, 0.731, 0.9, 1.0])
def test_bar_fill_accuracy(fill):
    img = background()
    draw_bar(img, HP_BOX, fill, (30, 30, 220))     # red HP
    draw_bar(img, MP_BOX, 1 - fill, (220, 90, 30))  # blue MP
    hp = calib.bar_fill(img, HP_BOX)
    mp = calib.bar_fill(img, MP_BOX)
    assert hp is not None and abs(hp - fill) <= 0.03
    assert mp is not None and abs(mp - (1 - fill)) <= 0.03


def test_bar_fill_invalid_box():
    img = background()
    assert calib.bar_fill(img, None) is None
    assert calib.bar_fill(img, [0, 0, 0, 5]) is None
    assert calib.bar_fill(img, [1360, 760, 50, 50]) is None
    assert calib.bar_fill(None, HP_BOX) is None
