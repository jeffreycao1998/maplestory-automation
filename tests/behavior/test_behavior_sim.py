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
@pytest.mark.parametrize("sag", [0.0, 0.04, 0.06, 0.07, 0.08])
def test_sagging_bridge_does_not_trip_default_tolerance(sag):
    # default patrol_y_tolerance 0.04, patrol_y = middle of the y range of the patrol zone,
    # minimap dot quantised to 1/112 and read 1 px off on 20% of frames
    for seed in range(1, 6):
        r = simulate(seed=seed, sag=sag, y_jitter=0.2)
        assert r.walk_ticks_after_fall == 0
        assert r.max_off_streak_s < 0.5, (sag, seed, r.max_off_streak_s)   # << platform_lost_s
        assert r.kills >= 5
        if sag <= 0.06:
            assert r.states.get("OFF_PLATFORM", 0) == 0


def test_patrol_y_taken_at_bridge_end_trips_on_deep_sag_with_tight_tolerance():
    # documents why a tight tolerance needs patrol_y from the middle of the walked range
    from maplebot.settings import Settings
    s = Settings()
    s.calibration.patrol_y_tolerance = 0.04
    r = simulate(seed=1, sag=0.07, patrol_y="end", settings=s)
    assert r.states.get("OFF_PLATFORM", 0) > 100


def test_default_tolerance_survives_patrol_y_from_bridge_ends():
    # the wizard sets patrol_y from the two edge readings (bridge ends); the default
    # tolerance (0.08) must not trip on a sagging middle
    r = simulate(seed=1, sag=0.07, patrol_y="end")
    assert r.states.get("OFF_PLATFORM", 0) == 0


@pytest.mark.parametrize("seed", range(1, 5))
def test_fall_to_bridge_below_stops_walking(seed):
    r = simulate(seed=seed, fall_at=20.0, y_jitter=0.2)
    assert r.fall_detect_delay_s is not None and r.fall_detect_delay_s <= 1.5
    assert r.walk_ticks_after_fall == 0          # never walks on the wrong bridge
    # only stands (OFF_PLATFORM) or shoots mobs in range (ENGAGE) until the supervisor pauses
    assert set(r.states_after_fall) <= {"OFF_PLATFORM", "ENGAGE"}
    assert r.states_after_fall.get("OFF_PLATFORM", 0) > 0


def test_no_patrol_y_never_off_platform():
    r = simulate(seed=1, patrol_y=None, fall_at=20.0)
    assert "OFF_PLATFORM" not in r.states
