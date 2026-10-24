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
    assert m.patrol_y is None and m.patrol_y_tolerance == 0.08
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


def settings_y(y, tol=0.04):
    s = settings_with(0.3, 0.7)
    s.calibration.patrol_y = y
    s.calibration.patrol_y_tolerance = tol
    return s


def test_patrol_y_exposed():
    m = MapModel.from_settings(settings_y(0.446, 0.05))
    assert (m.patrol_y, m.patrol_y_tolerance) == (0.446, 0.05)
    assert m.training.y == 0.446


def test_on_platform_with_patrol_y():
    m = MapModel.from_settings(settings_y(0.45, 0.04))
    assert m.on_platform(0.5, 0.45)
    assert m.on_platform(0.5, 0.4899) and m.on_platform(0.5, 0.4101)
    assert not m.on_platform(0.5, 0.4901) and not m.on_platform(0.5, 0.4099)
    assert not m.on_platform(0.5, 0.62)           # fell to the bridge below
    assert not m.on_platform(0.5, None)           # unknown height counts as off
    assert m.on_platform(0.1, 0.45)               # x beyond patrol bounds is still the bridge


def test_on_platform_without_patrol_y_accepts_any_height():
    m = MapModel.from_settings(settings_y(None))
    assert m.on_platform(0.5, 0.0) and m.on_platform(0.5, 1.0) and m.on_platform(0.5, None)


@pytest.mark.parametrize("y,tol", [(-0.1, 0.04), (1.5, 0.04), ("x", 0.04), (math.nan, 0.04),
                                   (0.5, 0.0), (0.5, -0.1), (0.5, 0.9), (0.5, None)])
def test_bad_patrol_y_rejected(y, tol):
    with pytest.raises(ValueError):
        MapModel.from_settings(settings_y(y, tol))


def test_real_frame_tree_that_grew_2():
    """Label data/labeled/tree-that-grew-2/000001.json: player dot at minimap
    (0.506, 0.446) (the character is on the central rope at that moment). Bridges on that
    minimap are ~12-13 px = ~0.11 apart (measured from the frame's minimap crop), so a
    band of +-0.04 around one height never includes the bridge above or below."""
    import json
    from maplebot.settings import REPO_ROOT
    label = json.loads((REPO_ROOT / "data/labeled/tree-that-grew-2/000001.json").read_text())
    x, y = label["player_map"]
    m = MapModel.from_settings(settings_y(0.446))
    assert m.side_of_patrol(x) == "inside"
    assert m.on_platform(x, y)
    assert not m.on_platform(x, y + 0.11) and not m.on_platform(x, y - 0.11)
