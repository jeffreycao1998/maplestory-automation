"""60-second simulated training runs driving Behavior at 15 Hz."""
from __future__ import annotations

import pytest

from behavior_sim import simulate
from behavior_helpers import make_settings

OVERSHOOT_MAX = 0.005      # minimap fraction beyond the patrol bounds (one walk tick ~0.002)


@pytest.mark.parametrize("seed", range(1, 9))
def test_sixty_second_run(seed):
    r = simulate(seed=seed, duration_s=60.0, hz=15.0)
    assert r.kills >= 5, r
    assert r.max_overshoot <= OVERSHOOT_MAX, r
    assert r.max_facing_changes_per_s <= 3, r.facing_changes
    assert r.jumps == 0                                  # nothing to get stuck on
    assert set(r.states) <= {"PATROL", "ENGAGE"}      # sagging bridge: no OFF_PLATFORM
    # it patrols most of the 0.30..0.70 zone, not just one spot
    assert r.max_map_x - r.min_map_x >= 0.3


@pytest.mark.parametrize("seed", range(1, 6))
def test_crowded_fast_flickering_mobs_no_oscillation(seed):
    # 10 mobs moving 60 px/s, 25% of detections missed per frame
    r = simulate(seed=seed, n_mobs=10, mob_speed=60.0, miss_rate=0.25)
    assert r.kills >= 10
    assert r.max_overshoot <= OVERSHOOT_MAX
    assert r.max_facing_changes_per_s <= 3, r.facing_changes
    assert r.wrong_way_casts == 0


def test_obstacle_triggers_stuck_jump_and_patrol_continues():
    r = simulate(seed=1, obstacle=(2300, 2340))          # inside the 1200..2800 px zone
    assert r.jumps >= 1
    assert r.states.get("STUCK", 0) == r.jumps
    assert r.max_map_x > 0.68                             # got past the obstacle
    assert r.kills >= 5


@pytest.mark.parametrize("seed", range(1, 5))
def test_narrow_patrol_zone_stays_inside(seed):
    s = make_settings(calibration__patrol_left=0.45, calibration__patrol_right=0.55)
    r = simulate(seed=seed, settings=s, n_mobs=8)
    assert r.max_overshoot <= OVERSHOOT_MAX
    assert r.kills >= 3


# ---------------------------------------------------------------- facing unknown at start
@pytest.mark.parametrize("start_facing", ["left", "right"])
@pytest.mark.parametrize("seed", range(1, 6))
def test_true_start_facing_unknown_never_casts_wrong_way(seed, start_facing):
    r = simulate(seed=seed, start_facing=start_facing)
    assert r.wrong_way_casts == 0
    assert r.kills >= 5


# ---------------------------------------------------------------- sagging bridges
def band(below=0.10, above=0.03):
    """Settings with the asymmetric height band (the intended defaults unless given)."""
    s = make_settings()
    s.calibration.patrol_y_tolerance = below
    s.calibration.patrol_y_tolerance_above = above
    return s


@pytest.mark.parametrize("sag", [0.0, 0.04, 0.063, 0.071, 0.08])
def test_sagging_bridge_stays_on_platform(sag):
    # patrol_y from the patrol-edge readings (top of the sag), dot quantised to 1/112 and
    # read 1 px off on 20% of frames: the sagging middle must never count as off.
    for seed in range(1, 6):
        r = simulate(seed=seed, sag=sag, y_jitter=0.2, settings=band())
        assert r.states.get("OFF_PLATFORM", 0) == 0, (sag, seed)
        assert r.kills >= 5


@pytest.mark.parametrize("sag", [0.063, 0.071])
def test_sagging_bridge_with_current_settings_defaults(sag):
    # whatever settings.py's defaults are right now must also survive the real sag
    for seed in range(1, 4):
        r = simulate(seed=seed, sag=sag, y_jitter=0.2)
        assert r.states.get("OFF_PLATFORM", 0) == 0, (sag, seed)


def test_symmetric_tight_band_would_trip_on_sag():
    # documents why "below" must cover the sag: +-0.04 around the ends trips constantly
    r = simulate(seed=1, sag=0.07, settings=band(below=0.04, above=0.04))
    assert r.states.get("OFF_PLATFORM", 0) > 100


@pytest.mark.parametrize("seed", range(1, 5))
@pytest.mark.parametrize("sag", [0.063, 0.071])
def test_fall_to_bridge_below_stops_walking(seed, sag):
    r = simulate(seed=seed, fall_at=20.0, y_jitter=0.2, sag=sag, settings=band())
    assert r.fall_detect_delay_s is not None and r.fall_detect_delay_s <= 3.0
    assert r.walk_ticks_after_fall == 0          # never walks on the wrong bridge
    # only stands (OFF_PLATFORM) or shoots mobs in range (ENGAGE) until the supervisor pauses
    assert set(r.states_after_fall) <= {"OFF_PLATFORM", "ENGAGE"}
    assert r.states_after_fall.get("OFF_PLATFORM", 0) > 0


def test_no_patrol_y_never_off_platform():
    r = simulate(seed=1, patrol_y=None, fall_at=20.0)
    assert "OFF_PLATFORM" not in r.states
