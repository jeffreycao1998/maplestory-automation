"""Offline setup flow and calibration checks; no GUI or game process."""
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from maplebot.app import logic
from maplebot.app.setup_detection import detect_screen, unique_player_dot, PIXEL_REGIONS
from maplebot.settings import Settings

DATA = Path(__file__).resolve().parents[2] / "data/labeled/tree-that-grew-2"


def context():
    return logic.WizardContext(settings=Settings(), window_box=(0, 0, 1366, 768))


def test_default_setup_has_six_pages_and_supplies_are_opt_in():
    steps = logic.setup_steps()
    assert [s.key for s in steps] == ["screen", "mobs", "player", "patrol", "keys", "summary"]
    assert len(logic.setup_steps(True)) == 7
    assert logic.setup_steps(True)[3].key == "supplies"
    assert "Step 1 of 6" in logic.step_label(0, steps=steps)


def test_real_screenshot_automatically_calibrates_entire_first_page_without_ocr():
    ctx = context()
    frame = cv2.imread(str(DATA / "000001.png"))
    expected = json.loads((DATA / "000001.json").read_text())
    report = detect_screen(ctx, frame)
    for field in ("minimap_box", "hp_bar_box", "mp_bar_box"):
        assert getattr(ctx.settings.calibration, field) == expected[field]
    assert ctx.dot_pos == pytest.approx(expected["player_map"], abs=.005)
    from maplebot.perception.calib import minimap_player_position
    c = ctx.settings.calibration
    assert minimap_player_position(frame, c.minimap_box, c.player_dot_hsv_low,
                                   c.player_dot_hsv_high) == pytest.approx(ctx.dot_pos)
    assert report.hp == pytest.approx(expected["hp"], abs=.03)
    assert report.mp == pytest.approx(expected["mp"], abs=.03)
    assert logic.step_problem("screen", ctx) is None
    assert ctx.settings.ocr.enabled is False


def test_blank_screenshot_does_not_create_fabricated_fixed_position_regions():
    ctx = context()
    report = detect_screen(ctx, np.zeros((768, 1366, 3), np.uint8))
    assert not report.applied
    assert ctx.dot_pos is None
    assert ctx.settings.calibration.hp_bar_box is None
    assert logic.step_problem("screen", ctx)


def test_same_size_manual_regions_survive_automatic_detection():
    ctx = context()
    ctx.settings.calibration.hp_bar_box = [10, 20, 100, 14]
    ctx.settings.calibration.reference_size = [1366, 768]
    report = detect_screen(ctx, cv2.imread(str(DATA / "000001.png")))
    assert ctx.settings.calibration.hp_bar_box == [10, 20, 100, 14]
    assert "hp_bar_box" not in report.applied


def test_lost_automatic_regions_are_cleared_on_retake():
    ctx = context()
    first = detect_screen(ctx, cv2.imread(str(DATA / "000001.png")))
    second = detect_screen(ctx, np.zeros((768, 1366, 3), np.uint8), first.owned)
    assert ctx.settings.calibration.hp_bar_box is None
    assert ctx.settings.calibration.mp_bar_box is None
    assert ctx.settings.calibration.minimap_box is None
    assert second.owned == {}


def test_retake_does_not_reclaim_manually_adjusted_automatic_region():
    ctx = context()
    frame = cv2.imread(str(DATA / "000001.png"))
    first = detect_screen(ctx, frame)
    ctx.settings.calibration.hp_bar_box = [505, 751, 102, 14]
    second = detect_screen(ctx, frame, first.owned)
    assert ctx.settings.calibration.hp_bar_box == [505, 751, 102, 14]
    assert "hp_bar_box" not in second.owned


def test_resize_invalidates_old_pixel_regions_even_before_first_capture():
    ctx = context()
    ctx.settings.calibration.reference_size = [1024, 768]
    ctx.patrol_preset = True
    ctx.patrol_left_set, ctx.patrol_right_set = .2, .8
    for name in PIXEL_REGIONS:
        setattr(ctx.settings.calibration, name, [1, 1, 50, 12])
    report = detect_screen(ctx, cv2.imread(str(DATA / "000001.png")))
    assert report.resized
    assert ctx.settings.calibration.reference_size == [1366, 768]
    assert ctx.settings.calibration.hp_stock_box is None
    assert ctx.settings.calibration.mesos_box is None
    assert ctx.settings.calibration.hp_bar_box == [504, 751, 103, 14]
    assert not ctx.patrol_preset and ctx.patrol_left_set is None


def test_ambiguous_player_dots_require_manual_confirmation():
    image = np.zeros((120, 200, 3), np.uint8)
    image[30:34, 40:44] = (0, 255, 255)
    low, high = [20, 150, 180], [35, 255, 255]
    assert unique_player_dot(image, [0, 0, 200, 120], low, high) is not None
    image[70:74, 120:124] = (0, 255, 255)
    assert unique_player_dot(image, [0, 0, 200, 120], low, high) is None


@pytest.mark.parametrize("missing", ["window", "screenshot", "minimap", "dot", "hp"])
def test_combined_page_cannot_skip_required_calibration(missing):
    ctx = context()
    detect_screen(ctx, cv2.imread(str(DATA / "000001.png")))
    if missing == "window": ctx.window_box = None
    if missing == "screenshot": ctx.screenshot_size = None
    if missing == "minimap": ctx.settings.calibration.minimap_box = None
    if missing == "dot": ctx.dot_pos = None
    if missing == "hp": ctx.settings.calibration.hp_bar_box = None
    assert logic.step_problem("screen", ctx)
