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
    assert (m.platforms[0].left, m.platforms[0].right) == (0.30, 0.70)


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
