import logging
import time

import cv2
import numpy as np
import pytest

from maplebot.perception import Perceiver
from maplebot.perception.perceiver import PLAYER_TAG_TO_BODY_PX
from maplebot.settings import Settings
from maplebot.types import Flag, Frame, Perception

from perception_synth import (H, HP_BOX, MINIMAP_BOX, MP_BOX, W, background, draw_bar,
                              draw_minimap, mob_sprite, name_tag, paste, write_png)


def make_settings(**det) -> Settings:
    s = Settings()
    s.calibration.minimap_box = list(MINIMAP_BOX)
    s.calibration.hp_bar_box = list(HP_BOX)
    s.calibration.mp_bar_box = list(MP_BOX)
    for k, v in det.items():
        setattr(s.detection, k, v)
    return s


@pytest.fixture
def templates(tmp_path):
    sprites = [mob_sprite(i) for i in range(3)]
    paths = [write_png(tmp_path / f"mob_{i}.png", s) for i, s in enumerate(sprites)]
    tag = name_tag()
    tag_path = write_png(tmp_path / "player.png", tag)
    return sprites, paths, tag, tag_path


def scene(sprites, placements, tag=None, tag_at=None, dot=(0.4, 0.6), hp=0.8, mp=0.5, seed=0):
    """placements: [(sprite_idx, cx, cy, mirrored)]."""
    img = background(seed)
    draw_minimap(img, dot=dot)
    draw_bar(img, HP_BOX, hp, (30, 30, 220))
    draw_bar(img, MP_BOX, mp, (220, 90, 30))
    for idx, cx, cy, mirrored in placements:
        spr = sprites[idx]
        paste(img, cv2.flip(spr, 1) if mirrored else spr, cx, cy)
    if tag is not None:
        paste(img, tag, *tag_at)
    return Frame(image=img, t=123.0, window=(0, 0, W, H))


PLACEMENTS = [
    (0, 300, 270, False), (0, 900, 270, True),
    (1, 520, 490, False), (1, 1100, 490, True),
    (2, 700, 660, False), (2, 200, 660, True),
]


def assert_mob_near(mobs, cx, cy, name, tol=4):
    hits = [m for m in mobs if abs(m.x - cx) <= tol and abs(m.y - cy) <= tol]
    assert len(hits) == 1, (cx, cy, mobs)
    assert hits[0].template == name


def test_full_perception(templates):
    sprites, paths, tag, tag_path = templates
    f = scene(sprites, PLACEMENTS, tag=tag, tag_at=(683, 500))
    p = Perceiver(make_settings(), paths, tag_path).perceive(f)
    assert isinstance(p, Perception)
    assert p.t == 123.0 and p.screen_size == (W, H)
    assert p.player_map is not None
    assert abs(p.player_map[0] - 0.4) < 0.01 and abs(p.player_map[1] - 0.6) < 0.01
    # recall + positions, incl. mirrored, and exactly one detection per mob
    assert len(p.mobs) == len(PLACEMENTS), p.mobs
    for idx, cx, cy, _ in PLACEMENTS:
        assert_mob_near(p.mobs, cx, cy, f"mob_{idx}.png")
    for m in p.mobs:
        assert 0.7 <= m.score <= 1.0
        assert (m.w, m.h) == (48, 40)
    # player
    assert not p.player_screen_estimated
    assert p.player_screen == (683, 500 - PLAYER_TAG_TO_BODY_PX)
    # bars
    assert abs(p.hp - 0.8) <= 0.03 and abs(p.mp - 0.5) <= 0.03
    assert p.flags == frozenset()
    assert p.confidence == 1.0


def test_mirrored_not_found_without_mirror_setting(templates):
    sprites, paths, _, _ = templates
    f = scene(sprites, [(0, 300, 270, False), (0, 900, 270, True)])
    p = Perceiver(make_settings(mirror_templates=False), paths, None).perceive(f)
    assert len(p.mobs) == 1 and abs(p.mobs[0].x - 300) <= 4


def test_full_resolution_matching(templates):
    sprites, paths, _, _ = templates
    f = scene(sprites, PLACEMENTS)
    p = Perceiver(make_settings(downscale=1.0), paths, None).perceive(f)
    assert len(p.mobs) == len(PLACEMENTS)
    for idx, cx, cy, _ in PLACEMENTS:
        assert_mob_near(p.mobs, cx, cy, f"mob_{idx}.png", tol=1)


def test_no_detections_inside_minimap_or_bars(templates):
    sprites, paths, _, _ = templates
    f = scene(sprites, [(0, 600, 270, False)])
    img = f.image
    # Mob-looking pixels inside the minimap and bars (e.g. a UI icon)
    x, y, w, h = MINIMAP_BOX
    paste(img, sprites[1], x + 60, y + 60)
    paste(img, cv2.resize(sprites[2], (48, 14)), HP_BOX[0] + 80, HP_BOX[1] + 7)
    s = make_settings()
    s.calibration.mp_bar_box = [MP_BOX[0], MP_BOX[1] - 40, MP_BOX[2], MP_BOX[3] + 40]
    paste(img, sprites[0], MP_BOX[0] + 80, MP_BOX[1] - 15)
    p = Perceiver(s, paths, None).perceive(f)
    assert len(p.mobs) == 1 and abs(p.mobs[0].x - 600) <= 4
    # and without the exclusion boxes those would have been detected
    s2 = make_settings()
    s2.calibration.mp_bar_box = None
    p2 = Perceiver(s2, paths, None).perceive(f)
    assert any(abs(m.x - (MP_BOX[0] + 80)) <= 4 for m in p2.mobs)


def test_overlapping_mobs_and_dedup(templates):
    sprites, paths, _, _ = templates
    # two different mobs side by side (touching) and a mob that matches several templates
    f = scene(sprites, [(0, 400, 270, False), (1, 448, 270, False)])
    p = Perceiver(make_settings(), paths + paths, None).perceive(f)   # duplicate templates
    assert len(p.mobs) == 2
    assert_mob_near(p.mobs, 400, 270, "mob_0.png")
    assert_mob_near(p.mobs, 448, 270, "mob_1.png")


def test_empty_screen_has_no_false_positives(templates):
    sprites, paths, _, _ = templates
    for seed in range(3):
        f = scene(sprites, [], seed=seed)
        p = Perceiver(make_settings(), paths, None).perceive(f)
        assert p.mobs == ()


def test_player_tag_not_found_falls_back(templates):
    sprites, paths, tag, tag_path = templates
    f = scene(sprites, PLACEMENTS)
    p = Perceiver(make_settings(), paths, tag_path).perceive(f)
    assert p.player_screen_estimated
    assert p.player_screen == (W // 2, int(H * 0.62))
    assert Flag.PLAYER_OFFSCREEN in p.flags
    assert p.confidence == pytest.approx(0.8)
    assert Flag.LOW_CONFIDENCE not in p.flags


def test_no_player_template_estimates_without_flag(templates):
    sprites, paths, tag, _ = templates
    f = scene(sprites, PLACEMENTS, tag=tag, tag_at=(683, 500))
    p = Perceiver(make_settings(), paths, None).perceive(f)
    assert p.player_screen_estimated and p.player_screen == (W // 2, int(H * 0.62))
    assert Flag.PLAYER_OFFSCREEN not in p.flags and p.confidence == 1.0


def test_minimap_missing_flags_and_confidence(templates):
    sprites, paths, _, tag_path = templates
    f = scene(sprites, PLACEMENTS, dot=None)
    p = Perceiver(make_settings(), paths, tag_path).perceive(f)
    assert p.player_map is None
    assert Flag.MINIMAP_MISSING in p.flags
    assert p.confidence == pytest.approx(0.3)
    assert Flag.LOW_CONFIDENCE in p.flags
    # not calibrated at all
    s = make_settings()
    s.calibration.minimap_box = None
    s.calibration.hp_bar_box = None
    p = Perceiver(s, paths, None).perceive(scene(sprites, PLACEMENTS))
    assert p.player_map is None and Flag.MINIMAP_MISSING in p.flags
    assert p.hp is None and p.mp is not None
    assert p.confidence == pytest.approx(0.5) and Flag.LOW_CONFIDENCE not in p.flags


def test_bad_templates_skipped(tmp_path, templates, caplog):
    sprites, paths, _, _ = templates
    bad = tmp_path / "mob_bad.png"
    bad.write_bytes(b"not a png")
    flat = write_png(tmp_path / "mob_flat.png", np.full((30, 30, 3), 128, np.uint8))
    tiny = write_png(tmp_path / "mob_tiny.png", np.random.default_rng(1).integers(0, 255, (6, 6, 3)).astype(np.uint8))
    with caplog.at_level(logging.WARNING):
        per = Perceiver(make_settings(), [bad, flat, tiny, tmp_path / "missing.png", paths[0]],
                        tmp_path / "nope.png")
    assert "mob_bad.png" in caplog.text and "missing.png" in caplog.text
    p = per.perceive(scene(sprites, [(0, 300, 270, False)]))
    assert len(p.mobs) == 1
    assert Flag.PLAYER_OFFSCREEN in p.flags   # configured but unusable -> treated as not found


def test_update_settings(templates):
    sprites, paths, _, _ = templates
    f = scene(sprites, [(0, 300, 270, False), (0, 900, 270, True)])
    per = Perceiver(make_settings(mirror_templates=False), paths, None)
    assert len(per.perceive(f).mobs) == 1
    per.update_settings(make_settings(mirror_templates=True))
    assert len(per.perceive(f).mobs) == 2
    per.update_settings(make_settings(mob_threshold=1.01))
    assert len(per.perceive(f).mobs) == 0


@pytest.mark.parametrize("image", [
    np.zeros((768, 1366, 3), np.uint8),               # black frame
    np.full((768, 1366, 3), 255, np.uint8),           # white frame
    np.zeros((20, 30, 3), np.uint8),                  # tiny frame (smaller than boxes/templates)
    np.zeros((0, 0, 3), np.uint8),                    # empty
    np.zeros((768, 1366), np.uint8),                  # grayscale
    np.zeros((768, 1366, 4), np.uint8),               # BGRA
    np.random.default_rng(0).random((480, 640, 3)),   # float image (noise)
    None,
], ids=["black", "white", "tiny", "empty", "gray", "bgra", "float_noise", "none"])
def test_garbage_frames_do_not_raise(templates, image):
    _, paths, _, tag_path = templates
    per = Perceiver(make_settings(), paths, tag_path)
    p = per.perceive(Frame(image=image, t=1.0, window=(0, 0, 0, 0)))
    assert isinstance(p, Perception)
    assert 0.0 <= p.confidence <= 1.0
    if image is not None and image.dtype == np.uint8:
        assert Flag.MINIMAP_MISSING in p.flags and p.player_map is None


def test_garbage_settings_do_not_raise(templates):
    sprites, paths, _, _ = templates
    s = make_settings(downscale=0)
    s.calibration.minimap_box = [1, 2]
    s.calibration.hp_bar_box = ["a", 1, 2, 3]
    p = Perceiver(s, paths, None).perceive(scene(sprites, PLACEMENTS))
    assert isinstance(p, Perception) and len(p.mobs) == len(PLACEMENTS)


def test_timing(templates):
    sprites, paths, tag, tag_path = templates
    f = scene(sprites, PLACEMENTS, tag=tag, tag_at=(683, 500))
    per = Perceiver(make_settings(), paths, tag_path)
    per.perceive(f)   # warm-up
    n = 20
    t0 = time.perf_counter()
    for _ in range(n):
        per.perceive(f)
    ms = (time.perf_counter() - t0) / n * 1000
    print(f"\nperceive: {ms:.1f} ms/frame at {W}x{H}, 3 mob templates (+mirrored), player tag")
    assert ms < 150   # generous bound for slow CI; target on a normal PC is <= 40 ms
