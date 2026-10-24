"""Checks on the user's real screenshot (The Tree That Grew II, 1366x768).

Templates are cropped from the same frame (as the setup wizard would), so this is an
optimistic but real check of colour handling and animation-frame recall.
"""
import json
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from maplebot.perception import Perceiver, calib
from maplebot.settings import Settings
from maplebot.types import Flag, Frame

from perception_synth import write_png

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "labeled" / "tree-that-grew-2"
PNG, LABEL = DATA / "000001.png", DATA / "000001.json"

pytestmark = pytest.mark.skipif(not (PNG.exists() and LABEL.exists()),
                                reason="real labelled frame not present")

SLIME_CROP = (176, 458, 48, 42)
STUMP_CROP = (740, 368, 52, 50)
TAG_CROP = (676, 492, 60, 15)
DOT_CLICK = (98, 130)            # client px of the user's yellow dot
PARTY_DOT = (88, 121)            # orange dot of party member 'Tyul'
EASY = (0, 1, 2, 4)              # label indices of fully visible mobs
HARD = (3, 5)                    # partly off-screen slime, hit-effect stump


@pytest.fixture(scope="module")
def real():
    img = cv2.imread(str(PNG))
    lab = json.loads(LABEL.read_text())
    return img, lab


@pytest.fixture(scope="module")
def templates(real, tmp_path_factory):
    img, _ = real
    d = tmp_path_factory.mktemp("real_templates")
    def crop(b):
        x, y, w, h = b
        return img[y:y + h, x:x + w].copy()
    return (write_png(d / "mob_slime.png", crop(SLIME_CROP)),
            write_png(d / "mob_stump.png", crop(STUMP_CROP)),
            write_png(d / "player.png", crop(TAG_CROP)))


def settings_for(lab) -> Settings:
    s = Settings()
    s.calibration.minimap_box = list(lab["minimap_box"])
    s.calibration.hp_bar_box = list(lab["hp_bar_box"])
    s.calibration.mp_bar_box = list(lab["mp_bar_box"])
    return s


def _in_box(m, box, slack=6):
    x, y, w, h = box
    return x - slack <= m.x <= x + w + slack and y - slack <= m.y <= y + h + slack


@pytest.mark.parametrize("dx,dy", [(0, 0), (-1, -1), (1, 1), (2, 2), (-2, 0), (0, -2), (3, 1), (-3, 0)])
def test_sampled_dot_range_works_on_real_minimap(real, dx, dy):
    img, lab = real
    lo, hi = calib.hsv_range_from_sample(img, DOT_CLICK[0] + dx, DOT_CLICK[1] + dy)
    p = calib.minimap_player_position(img, lab["minimap_box"], lo, hi)
    assert p is not None
    assert abs(p[0] - lab["player_map"][0]) < 0.01 and abs(p[1] - lab["player_map"][1]) < 0.01


def test_sampled_dot_range_excludes_vines_and_party_dot(real):
    img, lab = real
    lo, hi = calib.hsv_range_from_sample(img, *DOT_CLICK)
    x, y, w, h = lab["minimap_box"]
    mask = calib.hsv_mask(cv2.cvtColor(img[y:y + h, x:x + w], cv2.COLOR_BGR2HSV), lo, hi)
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    assert n - 1 == 1, "only the player's dot should match"
    cx, cy = cents[1] + (x, y)
    assert abs(cx - DOT_CLICK[0]) <= 1 and abs(cy - DOT_CLICK[1]) <= 1
    # the orange party dot's core (H~14-21) is outside the hue range
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    px, py = PARTY_DOT
    assert not (lo[0] <= hsv[py, px, 0] <= hi[0])


def test_default_range_and_bars_on_real_frame(real):
    img, lab = real
    c = Settings().calibration
    p = calib.minimap_player_position(img, lab["minimap_box"], c.player_dot_hsv_low, c.player_dot_hsv_high)
    assert p is not None and abs(p[0] - 0.506) < 0.01 and abs(p[1] - 0.446) < 0.01
    assert abs(calib.bar_fill(img, lab["hp_bar_box"]) - lab["hp"]) <= 0.03
    assert abs(calib.bar_fill(img, lab["mp_bar_box"]) - lab["mp"]) <= 0.03


def test_mob_recall_on_real_frame(real, templates):
    img, lab = real
    slime, stump, tag = templates
    per = Perceiver(settings_for(lab), [slime, stump], tag)
    f = Frame(image=img, t=1.0, window=(0, 0, img.shape[1], img.shape[0]))
    p = per.perceive(f)

    hit, fps = set(), []
    for m in p.mobs:
        idx = [i for i, t in enumerate(lab["mobs"])
               if i not in hit and t["kind"] in m.template and _in_box(m, t["box"])]
        if idx:
            hit.add(idx[0])
        else:
            fps.append(m)
    easy = sum(i in hit for i in EASY)
    hard = sum(i in hit for i in HARD)
    print(f"\nreal frame: easy {easy}/4, hard {hard}/2, FP {len(fps)}, mobs {[(m.template, m.x, m.y, round(m.score, 2)) for m in p.mobs]}")
    assert easy == 4                  # incl. the two other slime animation frames
    assert fps == []
    assert not any(_in_box(m, lab["minimap_box"], 0) for m in p.mobs)

    # name tag, using settings.detection.player_tag_offset_px
    assert not p.player_screen_estimated
    tx, ty, tw, th = TAG_CROP
    assert p.player_screen == (tx + tw // 2, ty + th // 2 - 63)
    assert Flag.MINIMAP_MISSING not in p.flags and p.confidence == 1.0

    n = 10
    t0 = time.perf_counter()
    for _ in range(n):
        per.perceive(f)
    ms = (time.perf_counter() - t0) / n * 1000
    print(f"real frame perceive: {ms:.1f} ms/frame (2 mob templates + mirrors, name tag)")
    assert ms < 150


def test_player_tag_offset_setting(real, templates):
    img, lab = real
    slime, stump, tag = templates
    s = settings_for(lab)
    s.detection.player_tag_offset_px = 40
    p = Perceiver(s, [slime, stump], tag).perceive(Frame(img, 0.0, (0, 0, 1366, 768)))
    tx, ty, tw, th = TAG_CROP
    assert p.player_screen == (tx + tw // 2, ty + th // 2 - 40)
