"""Synthetic scene generator: determinism, truth matches pixels, settings, sprites."""
from __future__ import annotations

import cv2
import numpy as np
import pytest

from maplebot.replay import synthetic as S


def test_same_seed_same_image():
    a, ta = S.make_scene(seed=3)
    b, tb = S.make_scene(seed=3)
    assert np.array_equal(a, b)
    assert ta == tb


def test_different_seed_different_image():
    a, _ = S.make_scene(seed=1)
    b, _ = S.make_scene(seed=2)
    assert not np.array_equal(a, b)


def test_random_scene_deterministic():
    a, ta = S.random_scene(11)
    b, tb = S.random_scene(11)
    assert np.array_equal(a, b) and ta == tb


def test_image_shape_and_dtype():
    img, truth = S.make_scene(seed=0, size=(800, 600))
    assert img.shape == (600, 800, 3) and img.dtype == np.uint8
    assert truth["size"] == [800, 600]


def test_truth_has_label_format_keys(synthetic_scene):
    t = synthetic_scene.truth
    for k in ("map", "minimap_box", "player_map", "player_screen", "mobs", "hp", "mp", "flags"):
        assert k in t
    for m in t["mobs"]:
        assert len(m["box"]) == 4 and isinstance(m["kind"], str)


def test_dot_colour_at_player_map(synthetic_scene):
    img, t = synthetic_scene.image, synthetic_scene.truth
    x, y, w, h = t["minimap_box"]
    fx, fy = t["player_map"]
    assert fx == pytest.approx(0.42, abs=1.0 / w)
    px, py = int(x + fx * w), int(y + fy * h)
    assert tuple(int(v) for v in img[py, px]) == S.DOT_BGR


def test_dot_centre_matches_mask_centroid(synthetic_scene):
    img, t = synthetic_scene.image, synthetic_scene.truth
    x, y, w, h = t["minimap_box"]
    sub = img[y:y + h, x:x + w]
    mask = np.all(sub == np.array(S.DOT_BGR, np.uint8), axis=2)
    ys, xs = np.nonzero(mask)
    assert (xs.mean() + 0.5) / w == pytest.approx(t["player_map"][0], abs=1e-5)
    assert (ys.mean() + 0.5) / h == pytest.approx(t["player_map"][1], abs=1e-5)


def test_no_dot_when_player_map_none():
    img, t = S.make_scene(seed=0, player_map=None)
    x, y, w, h = t["minimap_box"]
    assert t["player_map"] is None
    assert not np.all(img[y:y + h, x:x + w] == np.array(S.DOT_BGR, np.uint8), axis=2).any()


@pytest.mark.parametrize("frac", [0.0, 0.25, 0.8, 1.0])
def test_bars_filled_at_hp_mp(frac):
    img, t = S.make_scene(seed=0, hp=frac, mp=1.0 - frac)
    for key, colour, f in (("hp", S.HP_BGR, frac), ("mp", S.MP_BGR, 1.0 - frac)):
        x, y, w, h = t[f"{key}_bar_box"]
        row = img[y + h // 2, x:x + w]
        filled = np.all(row == np.array(colour, np.uint8), axis=1)
        empty = np.all(row == np.array(S.EMPTY_BGR, np.uint8), axis=1)
        assert t[key] == pytest.approx(f, abs=1.0 / w)
        n = int(round(t[key] * w))
        assert filled[:n].all() and empty[n:].all()


def test_mob_pixels_equal_sprite(synthetic_scene):
    img, t = synthetic_scene.image, synthetic_scene.truth
    for m in t["mobs"]:
        x, y, w, h = m["box"]
        spr = S.mob_sprite(0, m["facing"])
        mask = S.mob_mask(0, m["facing"]) > 0
        crop = img[y:y + h, x:x + w]
        if m["visible"] == 1.0:
            assert np.array_equal(crop[mask], spr[mask])


def test_mob_sprite_mirrored_and_not_a_rectangle():
    left, right = S.mob_sprite(0, "left"), S.mob_sprite(0, "right")
    assert left.shape == (S.MOB_SIZE[1], S.MOB_SIZE[0], 3)
    assert np.array_equal(left[:, ::-1], right)
    assert not np.array_equal(left, right)
    m = S.mob_mask(0)
    assert 0.4 < (m > 0).mean() < 0.9          # a blob, not a full box
    assert np.array_equal(S.mob_sprite(1), S.mob_sprite(1))
    assert not np.array_equal(S.mob_sprite(0), S.mob_sprite(1))


def test_mob_template_matches_scene(synthetic_scene):
    gray = cv2.cvtColor(synthetic_scene.image, cv2.COLOR_BGR2GRAY)
    tpl = cv2.cvtColor(synthetic_scene.mob_sprite, cv2.COLOR_BGR2GRAY)
    res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
    left_facing = [m for m in synthetic_scene.truth["mobs"] if m["facing"] == "left"]
    x, y, _, _ = left_facing[0]["box"]
    assert res[y, x] > 0.9


def test_name_tag_under_feet(synthetic_scene):
    img, t = synthetic_scene.image, synthetic_scene.truth
    tag = synthetic_scene.name_tag
    x, y, w, h = t["name_tag_box"]
    assert (h, w) == tag.shape[:2]
    assert np.array_equal(img[y:y + h, x:x + w], tag)
    px, py = t["player_screen"]
    assert x + w / 2 == pytest.approx(px, abs=1)
    assert y + h / 2 == pytest.approx(py + S.TAG_BELOW_BODY_PX, abs=1)
    assert y > py + S.CHAR_HALF_H - 2          # below the feet
    assert (tag > 200).any() and np.median(tag) < 60   # white text on dark


def test_hidden_player():
    _, t = S.make_scene(seed=0, player_y=-1)
    assert t["player_screen"] is None and t["name_tag_box"] is None


def test_mobs_off_screen_not_in_truth():
    _, t = S.make_scene(seed=0, mobs=[(-100, 496, "left"), (500, 496, "left")])
    assert len(t["mobs"]) == 1


def test_visible_fraction_drops_when_covered():
    _, t = S.make_scene(seed=0, player_x=512, mobs=[(512, 496, "left"), (800, 496, "left")])
    under_player, alone = t["mobs"]
    assert alone["visible"] == 1.0
    assert under_player["visible"] < 0.9


def test_settings_for_scene(synthetic_scene):
    t = synthetic_scene.truth
    s = S.settings_for_scene(t)
    assert s.calibration.minimap_box == list(t["minimap_box"])
    assert s.calibration.hp_bar_box == list(t["hp_bar_box"])
    assert s.calibration.mp_bar_box == list(t["mp_bar_box"])
    assert 0 <= s.calibration.patrol_left < s.calibration.patrol_right <= 1
    assert s.problems() == []


def test_dot_inside_default_hsv_range(synthetic_scene):
    from maplebot.settings import Calibration
    c = Calibration()
    hsv = cv2.cvtColor(np.uint8([[S.DOT_BGR]]), cv2.COLOR_BGR2HSV)[0, 0]
    assert all(lo <= v <= hi for v, lo, hi in zip(hsv, c.player_dot_hsv_low, c.player_dot_hsv_high))


def test_write_templates(tmp_path):
    paths = S.write_templates(tmp_path, mob_kinds=(0, 1))
    names = sorted(p.name for p in paths)
    assert names == ["mob_synthetic-0.png", "mob_synthetic-1.png", "player.png"]
    back = cv2.imread(str(tmp_path / "mob_synthetic-0.png"))
    assert np.array_equal(back, S.mob_sprite(0))


def test_tmp_profile_fixture(tmp_profile):
    assert [p.name for p in tmp_profile.mob_templates()] == ["mob_synthetic-0.png"]
    assert tmp_profile.player_template() is not None
    assert tmp_profile.load().calibration.minimap_box is not None
