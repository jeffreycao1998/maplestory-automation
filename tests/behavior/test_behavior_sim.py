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
    assert set(r.states) <= {"PATROL", "ENGAGE"}
    # it patrols the whole zone, not just one spot
    assert r.min_map_x < 0.32 and r.max_map_x > 0.68


def test_crowded_fast_mobs_no_oscillation():
    r = simulate(seed=3, n_mobs=10, mob_speed=60.0, miss_rate=0.25)
    assert r.kills >= 10
    assert r.max_overshoot <= OVERSHOOT_MAX
    assert r.max_facing_changes_per_s <= 4, r.facing_changes


def test_obstacle_triggers_stuck_jump_and_patrol_continues():
    r = simulate(seed=1, obstacle=(2300, 2340))          # inside the 1200..2800 px zone
    assert r.jumps >= 1
    assert r.states.get("STUCK", 0) == r.jumps
    assert r.max_map_x > 0.68                             # got past the obstacle
    assert r.kills >= 5


def test_narrow_patrol_zone_stays_inside():
    s = make_settings(calibration__patrol_left=0.45, calibration__patrol_right=0.55)
    r = simulate(seed=2, settings=s)
    assert r.max_overshoot <= OVERSHOOT_MAX
    assert r.kills >= 3
