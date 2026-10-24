import math

import pytest

from maplebot.mapmodel import MapModel, Platform
from maplebot.settings import Settings


def settings_with(left, right):
    s = Settings()
    s.calibration.patrol_left = left
    s.calibration.patrol_right = right
    return s


def test_from_settings_defaults():
    m = MapModel.from_settings(Settings())
    assert (m.patrol_left, m.patrol_right) == (0.30, 0.70)
    assert len(m.platforms) == 1
    assert m.patrol_y is None and m.patrol_y_band is None
    c = Settings().calibration
    assert m.patrol_y_tolerance == c.patrol_y_tolerance                       # below
    assert m.patrol_y_tolerance_above == getattr(c, "patrol_y_tolerance_above", 0.03)
    # v1: platform x extent unknown -> full width; patrol bounds sit inside it
    assert (m.platforms[0].left, m.platforms[0].right) == (0.0, 1.0)
    assert m.training is m.platforms[0]


@pytest.mark.parametrize("left,right", [(0.0, 1.0), (0.0, 0.01), (0.99, 1.0)])
def test_from_settings_accepts_valid_extremes(left, right):
    m = MapModel.from_settings(settings_with(left, right))
    assert (m.patrol_left, m.patrol_right) == (left, right)


@pytest.mark.parametrize("left,right", [
    (0.7, 0.3),          # swapped
    (0.5, 0.5),          # empty
    (-0.1, 0.5),         # below 0
    (0.2, 1.2),          # above 1
    (math.nan, 0.5),
    (None, 0.5),
    ("abc", 0.5),
])
def test_from_settings_rejects_invalid(left, right):
    with pytest.raises(ValueError) as e:
        MapModel.from_settings(settings_with(left, right))
    msg = str(e.value)
    assert "Patrol" in msg and "setup" in msg


def test_side_of_patrol():
    m = MapModel.from_settings(settings_with(0.3, 0.7))
    assert m.side_of_patrol(0.0) == "left_out"
    assert m.side_of_patrol(0.299) == "left_out"
    assert m.side_of_patrol(0.3) == "inside"
    assert m.side_of_patrol(0.5) == "inside"
    assert m.side_of_patrol(0.7) == "inside"
    assert m.side_of_patrol(0.701) == "right_out"
    assert m.side_of_patrol(1.0) == "right_out"


def test_platform_lookup_and_multi_platform_constructor():
    m = MapModel([Platform(0.1, 0.4, name="low"), Platform(0.5, 0.9, name="high")], 0.2, 0.3)
    assert m.platform_at(0.2).name == "low"
    assert m.platform_at(0.6).name == "high"
    assert m.platform_at(0.45) is None
    with pytest.raises(ValueError):
        MapModel([], 0.2, 0.3)
    with pytest.raises(ValueError):
        MapModel([Platform(0.6, 0.4)], 0.2, 0.3)


def settings_y(y, below=0.10, above=0.03):
    s = settings_with(0.3, 0.7)
    s.calibration.patrol_y = y
    s.calibration.patrol_y_tolerance = below
    s.calibration.patrol_y_tolerance_above = above
    return s


def test_patrol_y_exposed():
    m = MapModel.from_settings(settings_y(0.446, 0.09, 0.02))
    assert m.patrol_y == 0.446
    assert (m.patrol_y_tolerance, m.patrol_y_tolerance_above) == (0.09, 0.02)
    assert m.patrol_y_band == pytest.approx((0.426, 0.536))
    assert (m.training.y, m.training.y_below, m.training.y_above) == (0.446, 0.09, 0.02)


def test_on_platform_band_is_asymmetric():
    m = MapModel.from_settings(settings_y(0.45, below=0.10, above=0.03))
    assert m.on_platform(0.5, 0.45)
    assert m.on_platform(0.5, 0.5499)             # sagging middle, 0.0999 below
    assert not m.on_platform(0.5, 0.5501)         # 0.1001 below
    assert m.on_platform(0.5, 0.4201)             # 0.0299 above (jitter)
    assert not m.on_platform(0.5, 0.4199)         # 0.0301 above
    assert not m.on_platform(0.5, 0.62)           # fell to the bridge below
    assert not m.on_platform(0.5, None)           # unknown height counts as off
    assert m.on_platform(0.1, 0.45)               # x beyond patrol bounds is still the bridge


def test_above_zero_allowed():
    m = MapModel.from_settings(settings_y(0.45, below=0.1, above=0.0))
    assert m.on_platform(0.5, 0.45) and not m.on_platform(0.5, 0.449)


def test_tolerance_above_missing_from_settings_uses_default():
    s = settings_y(0.45)
    del s.calibration.patrol_y_tolerance_above      # instance attr -> falls back to class/default
    m = MapModel.from_settings(s)
    assert m.patrol_y_tolerance_above == getattr(s.calibration, "patrol_y_tolerance_above", 0.03)


def test_on_platform_without_patrol_y_accepts_any_height():
    m = MapModel.from_settings(settings_y(None))
    assert m.on_platform(0.5, 0.0) and m.on_platform(0.5, 1.0) and m.on_platform(0.5, None)


@pytest.mark.parametrize("y,below,above", [
    (-0.1, 0.1, 0.03), (1.5, 0.1, 0.03), ("x", 0.1, 0.03), (math.nan, 0.1, 0.03),
    (0.5, 0.0, 0.03), (0.5, -0.1, 0.03), (0.5, 0.9, 0.03), (0.5, None, 0.03),
    (0.5, 0.1, -0.01), (0.5, 0.1, 0.9), (0.5, 0.1, None), (0.5, 0.1, math.nan)])
def test_bad_patrol_y_rejected(y, below, above):
    with pytest.raises(ValueError):
        MapModel.from_settings(settings_y(y, below, above))


# Real-frame geometry, measured from data/labeled/tree-that-grew-2/000001.png, minimap box
# [15, 81, 166, 112] (112 px tall): the top bridge's anchored ends are at y ~0.062 and its
# sagging middle at 0.125-0.134; the next bridge down is ~0.117 below the ends.
BRIDGE_END_Y = 0.062
BRIDGE_MID_Y = (0.125, 0.134)
NEXT_BRIDGE_GAP = 0.117


def test_real_bridge_geometry_with_patrol_y_from_ends():
    """The wizard sets patrol_y from the two patrol-edge readings = the bridge ends."""
    m = MapModel.from_settings(settings_y(BRIDGE_END_Y))     # band defaults 0.10 / 0.03
    for y in (BRIDGE_END_Y, *BRIDGE_MID_Y, BRIDGE_END_Y - 1 / 112):     # incl. 1 px jitter up
        assert m.on_platform(0.5, y), y
    for y in (BRIDGE_MID_Y[1] + 1 / 112, BRIDGE_MID_Y[1] + 2 / 112):     # 1-2 px jitter down
        assert m.on_platform(0.5, y), y
    next_end = BRIDGE_END_Y + NEXT_BRIDGE_GAP                           # 0.179
    for y in (next_end, 0.18, 0.19, next_end + (BRIDGE_MID_Y[1] - BRIDGE_END_Y)):
        assert not m.on_platform(0.5, y), y
    assert not m.on_platform(0.5, BRIDGE_END_Y - NEXT_BRIDGE_GAP + 0.05)  # bridge above


def test_real_bridge_geometry_with_current_settings_defaults():
    s = settings_with(0.3, 0.7)
    s.calibration.patrol_y = BRIDGE_END_Y                     # tolerances left at defaults
    m = MapModel.from_settings(s)
    assert all(m.on_platform(0.5, y) for y in (BRIDGE_END_Y, *BRIDGE_MID_Y))
    assert not m.on_platform(0.5, 0.18) and not m.on_platform(0.5, 0.19)


def test_real_frame_tree_that_grew_2():
    """Label data/labeled/tree-that-grew-2/000001.json: player dot at minimap
    (0.506, 0.446) (on the central rope at that moment). Treat that as a bridge-end height:
    the dot is on-platform, the next bridge down (+0.117) and the one above are not."""
    import json
    from maplebot.settings import REPO_ROOT
    label = json.loads((REPO_ROOT / "data/labeled/tree-that-grew-2/000001.json").read_text())
    x, y = label["player_map"]
    m = MapModel.from_settings(settings_y(0.446))
    assert m.side_of_patrol(x) == "inside"
    assert m.on_platform(x, y)
    assert m.on_platform(x, y + 0.07)                         # its own sagging middle
    assert not m.on_platform(x, y + NEXT_BRIDGE_GAP)
    assert not m.on_platform(x, y - NEXT_BRIDGE_GAP)
