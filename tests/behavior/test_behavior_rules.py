"""One test (or a few) per named rule of the behaviour state machine."""
from __future__ import annotations

import pytest

from maplebot.behavior import DEAD_ZONE_PX, LINGER_S, TURN_HOLD_S, Behavior
from maplebot.mapmodel import MapModel
from maplebot.types import Action, Flag

from behavior_helpers import PLAYER_X, make_behavior, make_settings, mob, perc

A = Action
X = PLAYER_X


def run(b, frames, start=0.0, dt=1 / 15):
    """Feed perceptions (or (perception, t) pairs) and return the Decisions."""
    out = []
    t = start
    for f in frames:
        out.append(b.decide(f, t))
        t += dt
    return out


def walk_right(b, t=0.0, map_x=0.5):
    d = b.decide(perc(map_x=map_x), t)
    assert d.action is A.WALK_RIGHT
    return d


# ---------------------------------------------------------------- WAIT
@pytest.mark.parametrize("kw", [dict(confidence=0.49), dict(flags={Flag.LOW_CONFIDENCE})])
def test_wait_on_low_confidence_idle_when_standing(kw):
    b = make_behavior()
    d = b.decide(perc(mobs=[X + 100], **kw), 0.0)
    assert (d.action, d.state) == (A.IDLE, "WAIT")
    assert "confidence" in d.reason


def test_wait_on_low_confidence_stops_walking_then_idles():
    b = make_behavior()
    walk_right(b)
    d1 = b.decide(perc(confidence=0.2), 0.1)
    d2 = b.decide(perc(confidence=0.2), 0.2)
    assert (d1.action, d1.state) == (A.STOP_WALK, "WAIT")
    assert (d2.action, d2.state) == (A.IDLE, "WAIT")
    assert b.walking is None


def test_wait_beats_recover_and_engage():
    b = make_behavior(hp_key="1")
    d = b.decide(perc(confidence=0.1, hp=0.1, mobs=[X + 50]), 0.0)
    assert d.state == "WAIT"


def test_confidence_exactly_half_is_trusted():
    b = make_behavior()
    assert b.decide(perc(confidence=0.5), 0.0).state == "PATROL"


@pytest.mark.parametrize("kw", [dict(map_x=None), dict(flags={Flag.MINIMAP_MISSING})])
def test_wait_on_missing_minimap(kw):
    b = make_behavior()
    walk_right(b)
    d1 = b.decide(perc(**kw), 0.1)
    d2 = b.decide(perc(**kw), 0.2)
    assert (d1.action, d1.state) == (A.STOP_WALK, "WAIT")
    assert (d2.action, d2.state) == (A.IDLE, "WAIT")
    assert "minimap" in d2.reason


def test_engage_still_works_without_minimap():
    # Shooting a mob in range on screen does not need the minimap position.
    b = make_behavior()
    d = b.decide(perc(map_x=None, mobs=[X + 100]), 0.0)
    assert (d.action, d.state) == (A.ATTACK, "ENGAGE")


# ---------------------------------------------------------------- RECOVER
def test_hp_potion_below_threshold():
    b = make_behavior(hp_key="1")
    d = b.decide(perc(hp=0.42), 0.0)
    assert (d.action, d.state) == (A.USE_HP_POT, "RECOVER")
    assert d.reason == "HP 42% < 50%"


def test_hp_at_threshold_no_potion():
    b = make_behavior(hp_key="1")
    assert b.decide(perc(hp=0.50), 0.0).action is not A.USE_HP_POT


def test_mp_potion_below_threshold():
    b = make_behavior(mp_key="2")
    d = b.decide(perc(mp=0.1), 0.0)
    assert (d.action, d.state) == (A.USE_MP_POT, "RECOVER")
    assert d.reason == "MP 10% < 30%"


def test_hp_before_mp_then_mp_next_tick():
    b = make_behavior(hp_key="1", mp_key="2")
    d1 = b.decide(perc(hp=0.2, mp=0.1), 0.0)
    d2 = b.decide(perc(hp=0.2, mp=0.1), 0.07)
    assert d1.action is A.USE_HP_POT
    assert d2.action is A.USE_MP_POT       # HP on cooldown, MP has its own cooldown


def test_potion_cooldown_gates_and_releases():
    b = make_behavior(hp_key="1", potions__cooldown_s=1.5)
    assert b.decide(perc(hp=0.2), 0.0).action is A.USE_HP_POT
    assert b.decide(perc(hp=0.2), 1.0).action is not A.USE_HP_POT
    assert b.decide(perc(hp=0.2), 1.49).action is not A.USE_HP_POT
    assert b.decide(perc(hp=0.2), 1.5).action is A.USE_HP_POT


def test_potion_cooldown_falls_through_to_engage():
    b = make_behavior(hp_key="1")
    b.decide(perc(hp=0.2), 0.0)
    d = b.decide(perc(hp=0.2, mobs=[X + 100]), 0.1)
    assert (d.action, d.state) == (A.ATTACK, "ENGAGE")


def test_unset_potion_keys_never_pot():
    b = make_behavior()   # both keys ""
    d = b.decide(perc(hp=0.05, mp=0.0), 0.0)
    assert d.action not in (A.USE_HP_POT, A.USE_MP_POT)


def test_unreadable_bars_never_pot():
    b = make_behavior(hp_key="1", mp_key="2")
    d = b.decide(perc(hp=None, mp=None), 0.0)
    assert d.state == "PATROL"


def test_recover_beats_engage():
    b = make_behavior(hp_key="1")
    d = b.decide(perc(hp=0.1, mobs=[X + 100]), 0.0)
    assert d.state == "RECOVER"


def test_potion_does_not_change_walking_belief():
    b = make_behavior(hp_key="1")
    walk_right(b)
    assert b.decide(perc(hp=0.1), 0.1).action is A.USE_HP_POT
    assert b.walking == "right"
    assert b.decide(perc(), 0.2).action is A.IDLE   # key still held, no repeat WALK


# ---------------------------------------------------------------- ENGAGE
def test_mob_right_in_range_attack():
    b = make_behavior()
    d = b.decide(perc(mobs=[X + 180]), 0.0)
    assert (d.action, d.state) == (A.ATTACK, "ENGAGE")
    assert d.target.x == X + 180
    assert "mob 180px right" in d.reason


def test_mob_left_turn_then_attack():
    b = make_behavior()
    d1 = b.decide(perc(mobs=[X - 200]), 0.0)
    d2 = b.decide(perc(mobs=[X - 200]), 0.07)
    assert (d1.action, d2.action) == (A.FACE_LEFT, A.ATTACK)
    assert b.facing == "left"


def test_walking_stops_first_then_faces_then_attacks():
    b = make_behavior()
    walk_right(b)
    ds = run(b, [perc(mobs=[X - 150])] * 3, start=0.1)
    assert [d.action for d in ds] == [A.STOP_WALK, A.FACE_LEFT, A.ATTACK]
    assert all(d.state == "ENGAGE" for d in ds)


def test_range_edges():
    b = make_behavior(skill__range_px=350, skill__vertical_tolerance_px=60)
    assert b.decide(perc(mobs=[X + 350]), 0.0).state == "ENGAGE"
    b = make_behavior(skill__range_px=350)
    assert b.decide(perc(mobs=[X + 351]), 0.0).state == "PATROL"


def test_vertical_tolerance():
    b = make_behavior(skill__vertical_tolerance_px=60)
    assert b.decide(perc(mobs=[mob(X + 100, y=400 + 60)]), 0.0).state == "ENGAGE"
    b = make_behavior(skill__vertical_tolerance_px=60)
    d = b.decide(perc(mobs=[mob(X + 100, y=400 - 61)]), 0.0)
    assert d.state == "PATROL" and d.target is None   # also not approached (other row)


def test_no_player_screen_no_engage():
    b = make_behavior()
    d = b.decide(perc(player=None, mobs=[X + 100]), 0.0)
    assert d.state == "PATROL"


def test_target_is_nearest_by_dx():
    b = make_behavior()
    d = b.decide(perc(mobs=[X + 300, X + 90, X + 200]), 0.0)
    assert d.target.x == X + 90


def test_nearest_behind_wins_only_by_margin():
    b = make_behavior()          # facing right
    d = b.decide(perc(mobs=[X + 120, X - 100]), 0.0)
    assert d.action is A.ATTACK and d.target.x == X + 120     # 20 px closer: keep facing
    b = make_behavior()
    d = b.decide(perc(mobs=[X + 200, X - 100]), 0.0)
    assert d.action is A.FACE_LEFT and d.target.x == X - 100   # 100 px closer: turn


def test_flicker_of_mob_ahead_does_not_turn_us():
    b = make_behavior()                                  # facing right
    assert b.decide(perc(mobs=[X + 100, X - 150]), 0.0).action is A.ATTACK
    d = b.decide(perc(mobs=[X - 150]), 0.07)              # right mob missed one frame
    assert d.action is A.IDLE and b.facing == "right"
    assert "last seen" in d.reason
    d = b.decide(perc(mobs=[X - 150]), 0.07 + TURN_HOLD_S)   # gone for good -> turn
    assert d.action is A.FACE_LEFT


def test_turned_to_target_is_held_through_flicker():
    b = make_behavior()
    assert b.decide(perc(mobs=[X - 100]), 0.0).action is A.FACE_LEFT
    d = b.decide(perc(mobs=[X + 150]), 0.07)              # left mob missed, right one seen
    assert d.action is A.ATTACK and b.facing == "left"


def test_approach_flicker_keeps_walking():
    b = make_behavior()
    walk_right(b)
    b.decide(perc(mobs=[X + 500]), 0.07)                  # approaching right
    d = b.decide(perc(mobs=[X - 450]), 0.14)              # right mob flickers out
    assert d.action is A.IDLE and b.walking == "right"
    d = b.decide(perc(mobs=[X - 450]), 0.14 + TURN_HOLD_S)
    assert d.action is A.WALK_LEFT


@pytest.mark.parametrize("dx", [DEAD_ZONE_PX, -DEAD_ZONE_PX, 0, 5, -5])
def test_dead_zone_keeps_facing(dx):
    b = make_behavior()
    d = b.decide(perc(mobs=[X + dx]), 0.0)
    assert d.action is A.ATTACK and b.facing == "right"
    b.facing = "left"
    d = b.decide(perc(mobs=[X + dx]), 1.0)
    assert d.action is A.ATTACK and b.facing == "left"


def test_just_outside_dead_zone_turns():
    b = make_behavior()
    d = b.decide(perc(mobs=[X - DEAD_ZONE_PX - 1]), 0.0)
    assert d.action is A.FACE_LEFT


def test_attack_cooldown_gating():
    b = make_behavior(skill__cooldown_s=0.65)
    f = perc(mobs=[X + 100])
    assert b.decide(f, 0.0).action is A.ATTACK
    d = b.decide(f, 0.3)
    assert (d.action, d.state) == (A.IDLE, "ENGAGE")
    assert "cooldown" in d.reason
    assert b.decide(f, 0.64).action is A.IDLE
    assert b.decide(f, 0.65).action is A.ATTACK


def test_linger_after_mob_disappears():
    b = make_behavior()
    b.decide(perc(mobs=[X + 100]), 0.0)
    d = b.decide(perc(), 0.3)
    assert (d.action, d.state) == (A.IDLE, "ENGAGE")
    assert "linger" in d.reason
    d = b.decide(perc(), LINGER_S - 0.01)
    assert d.state == "ENGAGE"
    d = b.decide(perc(), LINGER_S)
    assert (d.action, d.state) == (A.WALK_RIGHT, "PATROL")


def test_linger_flicker_does_not_walk():
    b = make_behavior()
    frames = [perc(mobs=[X + 100]) if i % 3 == 0 else perc() for i in range(30)]
    ds = run(b, frames)
    assert all(d.state == "ENGAGE" for d in ds)
    assert not any(d.action in (A.WALK_LEFT, A.WALK_RIGHT) for d in ds)


def test_linger_stops_walking_if_walking():
    # e.g. potion use, then still lingering: we never walk during linger
    b = make_behavior()
    b.decide(perc(mobs=[X + 100]), 0.0)
    b.walking = "right"            # pretend something left the key down
    d = b.decide(perc(), 0.2)
    assert (d.action, d.state) == (A.STOP_WALK, "ENGAGE")


# ---------------------------------------------------------------- PATROL
def test_start_patrol_right_then_idle_while_walking():
    b = make_behavior()
    ds = run(b, [perc(map_x=0.5 + i * 0.01) for i in range(5)])
    assert ds[0].action is A.WALK_RIGHT and ds[0].state == "PATROL"
    assert [d.action for d in ds[1:]] == [A.IDLE] * 4     # no repeated WALK
    assert all(d.state == "PATROL" for d in ds)


def test_turn_around_at_right_edge():
    b = make_behavior(calibration__patrol_left=0.3, calibration__patrol_right=0.7)
    walk_right(b, map_x=0.6)
    d = b.decide(perc(map_x=0.70), 0.1)
    assert (d.action, d.reason) == (A.WALK_LEFT, "reached right edge")
    d = b.decide(perc(map_x=0.69), 0.2)
    assert d.action is A.IDLE and b.walking == "left"


def test_turn_around_at_left_edge():
    b = make_behavior()
    b.walking = b.facing = "left"
    d = b.decide(perc(map_x=0.29), 0.0)
    assert (d.action, d.reason) == (A.WALK_RIGHT, "reached left edge")


def test_outside_bounds_walks_back_in():
    b = make_behavior()
    assert b.decide(perc(map_x=0.1), 0.0).action is A.WALK_RIGHT
    b = make_behavior()
    assert b.decide(perc(map_x=0.9), 0.0).action is A.WALK_LEFT


def test_approach_visible_mob_out_of_range():
    b = make_behavior()
    walk_right(b)
    d = b.decide(perc(map_x=0.5, mobs=[X - 500]), 0.1)
    assert (d.action, d.state) == (A.WALK_LEFT, "PATROL")
    assert d.target.x == X - 500 and "approach mob 500px left" in d.reason


def test_approach_ignores_other_rows():
    b = make_behavior()
    walk_right(b)
    d = b.decide(perc(mobs=[mob(X - 500, y=100)]), 0.1)
    assert d.action is A.IDLE and b.walking == "right"


def test_approach_never_beyond_edge():
    b = make_behavior(calibration__patrol_right=0.7)
    walk_right(b, map_x=0.69)
    # at the edge with a mob further right: turn around anyway
    d = b.decide(perc(map_x=0.70, mobs=[X + 500]), 0.1)
    assert d.action is A.WALK_LEFT
    # just inside again, mob still beyond the edge: don't bounce back toward it
    for i in range(10):
        d = b.decide(perc(map_x=0.695 - 0.005 * i, mobs=[X + 500]), 0.2 + i * 0.07)
        assert d.action is A.IDLE and b.walking == "left"


def test_edge_block_cleared_after_opposite_edge():
    b = make_behavior()
    b.decide(perc(map_x=0.70), 0.0)          # right edge -> walk left, block right
    b.decide(perc(map_x=0.30), 0.1)          # left edge -> walk right, block left
    d = b.decide(perc(map_x=0.4, mobs=[X + 500]), 0.2)
    assert d.action is A.IDLE and b.walking == "right"   # toward mob right: allowed
    d = b.decide(perc(map_x=0.45, mobs=[X - 500]), 0.3)
    assert d.action is A.IDLE and b.walking == "right"   # toward left edge: blocked


def test_edge_block_cleared_after_engage():
    b = make_behavior()
    b.decide(perc(map_x=0.70), 0.0)                       # block right
    b.decide(perc(map_x=0.69, mobs=[X + 100]), 0.1)       # fight
    d = b.decide(perc(map_x=0.69, mobs=[X + 500]), 1.0)
    assert d.action is A.WALK_RIGHT                        # free to approach again


def test_approach_prefers_walking_direction_unless_much_nearer():
    b = make_behavior()
    walk_right(b)
    d = b.decide(perc(mobs=[X + 450, X - 420]), 0.1)
    assert d.action is A.IDLE and b.walking == "right"
    d = b.decide(perc(mobs=[X + 600, X - 420]), 0.2)
    assert d.action is A.WALK_LEFT


def test_patrol_resumes_last_direction_after_fight():
    b = make_behavior()
    b.walking = b.facing = "left"
    b._patrol_dir = "left"
    run(b, [perc(mobs=[X + 100])] * 3)            # stop, face right, attack
    d = b.decide(perc(), 5.0)
    assert d.action is A.WALK_LEFT                 # resumes patrol direction, not facing


# ---------------------------------------------------------------- STUCK
def test_stuck_jump_after_stuck_s():
    b = make_behavior(behavior__stuck_s=2.5)
    walk_right(b, t=0.0, map_x=0.5)
    t, d = 0.0, None
    jumps = []
    while t < 2.6:
        t += 1 / 15
        d = b.decide(perc(map_x=0.502), t)       # moves only 0.002 < 0.004
        if d.action is A.JUMP:
            jumps.append(t)
    assert len(jumps) == 1
    assert 2.5 <= jumps[0] < 2.5 + 1 / 15 + 1e-9
    assert b.walking == "right"                  # jump keeps the walk key held


def test_stuck_once_then_timer_reset():
    b = make_behavior(behavior__stuck_s=1.0)
    walk_right(b)
    acts = [b.decide(perc(map_x=0.5), i * 0.1).action for i in range(1, 32)]
    jump_idx = [i for i, a in enumerate(acts) if a is A.JUMP]
    assert len(jump_idx) == 3                         # t=1.0, 2.0, 3.0
    d = [x for x in acts if x is not A.JUMP]
    assert set(d) == {A.IDLE}


def test_stuck_decision_shape():
    b = make_behavior(behavior__stuck_s=0.5)
    walk_right(b)
    b.decide(perc(), 0.3)
    d = b.decide(perc(), 0.5)
    assert (d.action, d.state) == (A.JUMP, "STUCK")
    assert "not moving" in d.reason


def test_moving_resets_stuck_timer():
    b = make_behavior(behavior__stuck_s=1.0)
    walk_right(b, t=0.0, map_x=0.5)
    assert b.decide(perc(map_x=0.502), 0.9).action is A.IDLE   # flat 0.9 s
    assert b.decide(perc(map_x=0.510), 1.0).action is A.IDLE   # moved > eps: reset
    assert b.decide(perc(map_x=0.512), 1.9).action is A.IDLE   # flat 0.9 s again
    d = b.decide(perc(map_x=0.512), 2.0)                       # 1.0 s since last move
    assert d.action is A.JUMP


def test_steady_walk_never_jumps():
    b = make_behavior(behavior__stuck_s=1.0)
    walk_right(b, map_x=0.4)
    for i in range(1, 40):
        d = b.decide(perc(map_x=0.4 + 0.005 * i), i * 0.1)
        assert d.action is not A.JUMP


def test_stopping_resets_stuck_timer():
    b = make_behavior(behavior__stuck_s=1.0)
    walk_right(b)
    b.decide(perc(), 0.8)
    b.decide(perc(confidence=0.0), 0.9)                 # STOP_WALK
    assert b.decide(perc(), 1.0).action is A.WALK_RIGHT  # restart, fresh timer
    assert b.decide(perc(), 1.5).action is A.IDLE
    assert b.decide(perc(), 1.99).action is A.IDLE
    assert b.decide(perc(), 2.0).action is A.JUMP


def test_no_stuck_while_standing():
    b = make_behavior(behavior__stuck_s=0.5)
    f = perc(mobs=[X + 100])
    acts = [b.decide(f, i * 0.1).action for i in range(50)]
    assert A.JUMP not in acts


def test_edge_turn_beats_stuck():
    b = make_behavior(behavior__stuck_s=0.5)
    walk_right(b, map_x=0.7 - 0.001)
    b.decide(perc(map_x=0.699), 0.4)
    d = b.decide(perc(map_x=0.70), 0.6)
    assert d.action is A.WALK_LEFT


# ---------------------------------------------------------------- belief / API
def test_belief_tracking_matches_controller_semantics():
    b = make_behavior()
    b.decide(perc(), 0.0)
    assert (b.facing, b.walking) == ("right", "right")
    b.decide(perc(mobs=[X - 100]), 0.1)                  # STOP_WALK
    assert (b.facing, b.walking) == ("right", None)
    b.decide(perc(mobs=[X - 100]), 0.2)                  # FACE_LEFT
    assert (b.facing, b.walking) == ("left", None)
    b.decide(perc(mobs=[X - 100]), 0.3)                  # ATTACK
    assert (b.facing, b.walking) == ("left", None)


def test_exactly_one_action_per_tick():
    b = make_behavior(hp_key="1")
    for i, f in enumerate([perc(), perc(hp=0.1), perc(mobs=[X + 30]), perc(map_x=None)]):
        d = b.decide(f, i * 0.1)
        assert isinstance(d.action, A) and d.state and d.reason


def test_reset_forgets_everything():
    b = make_behavior(hp_key="1")
    b.decide(perc(hp=0.1), 0.0)              # pot
    b.decide(perc(mobs=[X - 100]), 0.1)      # face left
    b.decide(perc(mobs=[X - 100]), 0.2)      # attack
    b.reset()
    assert (b.facing, b.walking) == ("right", None)
    assert b.decide(perc(hp=0.1), 0.3).action is A.USE_HP_POT     # pot cooldown gone
    assert b.decide(perc(mobs=[X + 100]), 0.4).action is A.ATTACK  # attack cd gone
    b.reset()
    assert b.decide(perc(), 0.45).action is A.WALK_RIGHT          # linger gone


def test_deterministic():
    frames = [perc(map_x=0.5 + 0.003 * i, mobs=[X + 300 - 10 * i] if i % 4 else [])
              for i in range(60)]
    a = [d for d in run(make_behavior(), frames)]
    c = [d for d in run(make_behavior(), frames)]
    assert a == c


def test_update_settings_changes_rules_and_bounds():
    b = make_behavior()
    s = make_settings(skill__range_px=100, calibration__patrol_right=0.55)
    b.update_settings(s)
    assert b.decide(perc(mobs=[X + 200]), 0.0).state == "PATROL"
    assert b.map.patrol_right == 0.55
    assert b.decide(perc(map_x=0.56), 0.1).action is A.WALK_LEFT


def test_update_settings_rejects_bad_bounds_and_keeps_old():
    b = make_behavior()
    bad = make_settings(calibration__patrol_left=0.8, calibration__patrol_right=0.2)
    with pytest.raises(ValueError):
        b.update_settings(bad)
    assert (b.map.patrol_left, b.map.patrol_right) == (0.3, 0.7)
    assert b.settings.calibration.patrol_left == 0.3


def test_constructor_uses_given_map_model():
    s = make_settings()
    m = MapModel.from_settings(make_settings(calibration__patrol_right=0.6))
    b = Behavior(s, m)
    assert b.decide(perc(map_x=0.61), 0.0).action is A.WALK_LEFT
