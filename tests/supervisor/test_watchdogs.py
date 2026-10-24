from __future__ import annotations

import dataclasses

import pytest

from maplebot.supervisor import Supervisor
from maplebot.types import Action, Decision, Flag, VerdictKind

from supervisor_helpers import make_p, make_settings, stuck_jump

DT = 0.1  # 10 Hz ticks


def run(sup, start, end, now_offset=0.0, **pkw):
    """Tick from start (inclusive) to end (exclusive) with the same perception.
    Returns list of (t, verdict)."""
    out = []
    n = int(round((end - start) / DT))
    for i in range(n):
        t = round(start + i * DT, 6)
        out.append((t, sup.check(make_p(t, **pkw), t)))
    return out


def first_non_ok(results):
    for t, v in results:
        if v.kind != VerdictKind.OK:
            return t, v
    return None


@pytest.fixture
def sup():
    s = Supervisor(make_settings())
    s.start(0.0)
    return s


# ---- healthy baseline ---------------------------------------------------------
def test_healthy_run_stays_ok(sup):
    assert first_non_ok(run(sup, 0, 60)) is None


# ---- player lost ------------------------------------------------------------------
def test_player_lost_triggers_pause_at_threshold(sup):
    run(sup, 0, 1)
    res = run(sup, 1.0, 5.0, player=None)
    t, v = first_non_ok(res)
    assert v.kind == VerdictKind.PAUSE
    assert t == pytest.approx(4.0)            # lost from t=1.0, threshold 3 s
    assert "Can't see your character on the minimap for 3 s" in v.reason
    assert "minimap is closed" in v.reason


def test_player_lost_just_under_threshold_ok(sup):
    res = run(sup, 0, 2.9, player=None)       # last tick 2.8 s
    assert first_non_ok(res) is None


def test_player_lost_interrupted_resets_timer(sup):
    run(sup, 0, 2.5, player=None)
    run(sup, 2.5, 2.6)                        # seen again for one tick
    res = run(sup, 2.6, 5.5, player=None)     # 2.9 s lost → still OK
    assert first_non_ok(res) is None


# ---- no mobs ----------------------------------------------------------------------
def test_no_mobs_triggers_pause(sup):
    res = run(sup, 0, 181, mobs=())
    t, v = first_non_ok(res)
    assert v.kind == VerdictKind.PAUSE
    assert t == pytest.approx(180.0)
    assert "No monsters seen for 3 min" in v.reason


def test_no_mobs_interrupted_ok(sup):
    run(sup, 0, 170, mobs=())
    run(sup, 170, 170.1)                      # one mob seen
    res = run(sup, 170.1, 340, mobs=())       # 169.9 s
    assert first_non_ok(res) is None


def test_no_mobs_just_under_threshold_ok(sup):
    assert first_non_ok(run(sup, 0, 179.9, mobs=())) is None


# ---- stuck --------------------------------------------------------------------------
def test_stuck_jumps_trigger_pause(sup):
    for i in range(5):
        t = i * 5.0
        sup.note_decision(stuck_jump(), t)
        assert sup.check(make_p(t), t).kind == VerdictKind.OK
    sup.note_decision(stuck_jump(), 25.0)
    v = sup.check(make_p(25.0), 25.0)
    assert v.kind == VerdictKind.PAUSE
    assert "stuck 6 times in the last 1 min" in v.reason


def test_stuck_jumps_spread_over_more_than_60s_ok(sup):
    for i in range(10):                       # one every 13 s → never 6 within 60 s
        t = i * 13.0
        sup.note_decision(stuck_jump(), t)
        assert sup.check(make_p(t), t).kind == VerdictKind.OK


def test_only_stuck_state_jumps_count(sup):
    for i in range(20):
        t = i * 1.0
        sup.note_decision(Decision(Action.JUMP, "PATROL"), t)      # normal jump
        sup.note_decision(Decision(Action.WALK_LEFT, "STUCK"), t)  # stuck, not a jump
        assert sup.check(make_p(t), t).kind == VerdictKind.OK


def test_stuck_window_slides(sup):
    for i in range(5):
        sup.note_decision(stuck_jump(), float(i))
    sup.check(make_p(4.0), 4.0)
    # 6th jump after the first 5 aged out of the 60 s window
    sup.note_decision(stuck_jump(), 64.5)
    assert sup.check(make_p(64.5), 64.5).kind == VerdictKind.OK


# ---- HP critical ----------------------------------------------------------------------
def test_hp_critical_triggers_stop_after_1s(sup):
    run(sup, 0, 1)
    res = run(sup, 1.0, 3.0, hp=0.10)
    t, v = first_non_ok(res)
    assert v.kind == VerdictKind.STOP
    assert t == pytest.approx(2.0)
    assert "HP has been below 15%" in v.reason and "now 10%" in v.reason


def test_hp_critical_blip_ok(sup):
    for k in range(10):                       # 0.9 s low, then a healthy reading
        base = k * 1.0
        run(sup, base, base + 0.9, hp=0.05)
        res = run(sup, base + 0.9, base + 1.0, hp=0.5)
        assert first_non_ok(res) is None


def test_hp_at_threshold_ok(sup):
    assert first_non_ok(run(sup, 0, 10, hp=0.15)) is None


def test_hp_none_never_triggers(sup):
    assert first_non_ok(run(sup, 0, 10, hp=None)) is None


def test_hp_none_ticks_do_not_reset_low_timer(sup):
    run(sup, 0, 0.5, hp=0.05)
    run(sup, 0.5, 0.8, hp=None)               # bar unreadable briefly
    res = run(sup, 0.8, 1.5, hp=0.05)
    t, v = first_non_ok(res)
    assert v.kind == VerdictKind.STOP and t == pytest.approx(1.0)


# ---- max runtime ------------------------------------------------------------------------
def test_max_runtime_stop():
    sup = Supervisor(make_settings(max_runtime_min=1.0))
    sup.start(100.0)
    assert sup.check(make_p(159.9), 159.9).kind == VerdictKind.OK
    v = sup.check(make_p(160.0), 160.0)
    assert v.kind == VerdictKind.STOP
    assert "maximum run time of 1 min" in v.reason
    assert sup.elapsed(160.0) == pytest.approx(60.0)


def test_max_runtime_zero_unlimited():
    sup = Supervisor(make_settings(max_runtime_min=0))
    sup.start(0.0)
    assert sup.check(make_p(1e6), 1e6).kind == VerdictKind.OK


def test_max_runtime_not_reset_by_resume():
    sup = Supervisor(make_settings(max_runtime_min=1.0))
    sup.start(0.0)
    sup.check(make_p(30), 30)
    sup.reset(30.0)
    assert sup.check(make_p(60.0), 60.0).kind == VerdictKind.STOP


# ---- low confidence ------------------------------------------------------------------------
def test_low_confidence_triggers_pause(sup):
    res = run(sup, 0, 4, flags={Flag.LOW_CONFIDENCE}, confidence=0.2)
    t, v = first_non_ok(res)
    assert v.kind == VerdictKind.PAUSE and t == pytest.approx(3.0)
    assert "Detection has been unsure for 3 s" in v.reason


def test_low_confidence_interrupted_ok(sup):
    run(sup, 0, 2.5, flags={Flag.LOW_CONFIDENCE})
    run(sup, 2.5, 2.6)
    assert first_non_ok(run(sup, 2.6, 5.5, flags={Flag.LOW_CONFIDENCE})) is None


def test_other_flags_do_not_count_as_low_confidence(sup):
    assert first_non_ok(run(sup, 0, 10, flags={Flag.PLAYER_OFFSCREEN})) is None


# ---- sticky / priority / reset ----------------------------------------------------------------
def test_pause_is_sticky_until_reset(sup):
    run(sup, 0, 3.1, player=None)
    # player is back, but verdict stays latched
    res = run(sup, 3.1, 10)
    assert all(v.kind == VerdictKind.PAUSE for _, v in res)
    sup.reset(10.0)
    assert sup.check(make_p(10.0), 10.0).kind == VerdictKind.OK


def test_stop_is_sticky_through_reset_only_by_start(sup):
    run(sup, 0, 1.1, hp=0.05)
    assert sup.verdict.kind == VerdictKind.STOP
    assert all(v.kind == VerdictKind.STOP for _, v in run(sup, 1.1, 5))
    sup.start(5.0)
    assert sup.check(make_p(5.0), 5.0).kind == VerdictKind.OK


def test_stop_outranks_pause_same_tick(sup):
    # player lost from t=0 (pause due at 3.0); HP critical from t=2.0 (stop due at 3.0)
    run(sup, 0, 2.0, player=None)
    res = run(sup, 2.0, 3.1, player=None, hp=0.05)
    t, v = first_non_ok(res)
    assert t == pytest.approx(3.0) and v.kind == VerdictKind.STOP and "HP" in v.reason


def test_stop_upgrades_latched_pause(sup):
    # pause condition already latched, then HP drops
    sup.start(10.0)
    run(sup, 10.0, 13.1, player=None)
    assert sup.verdict.kind == VerdictKind.PAUSE
    res = run(sup, 13.1, 14.5, player=None, hp=0.05)
    assert res[-1][1].kind == VerdictKind.STOP
    assert "HP" in res[-1][1].reason


def test_stop_never_downgraded_to_pause(sup):
    run(sup, 0, 1.1, hp=0.05)
    reason = sup.verdict.reason
    res = run(sup, 1.1, 10, player=None, mobs=())
    assert all(v.kind == VerdictKind.STOP and v.reason == reason for _, v in res)


def test_reset_clears_timers(sup):
    run(sup, 0, 2.9, player=None, mobs=())
    for i in range(5):
        sup.note_decision(stuck_jump(), 2.0 + i * 0.1)
    sup.reset(2.9)
    # Full thresholds needed again after reset
    assert first_non_ok(run(sup, 2.9, 5.8, player=None)) is None
    sup.note_decision(stuck_jump(), 6.0)
    assert sup.check(make_p(6.0), 6.0).kind == VerdictKind.OK


def test_update_settings_applies_new_thresholds(sup):
    run(sup, 0, 1.5, player=None)
    s = make_settings(player_lost_s=1.0)
    sup.update_settings(s)
    assert sup.check(make_p(1.5, player=None), 1.5).kind == VerdictKind.PAUSE
    assert "for 1 s" in sup.verdict.reason


def test_check_without_start_autostarts():
    sup = Supervisor(make_settings())
    assert sup.check(make_p(50.0), 50.0).kind == VerdictKind.OK
    assert sup.elapsed(51.0) == pytest.approx(1.0)


def test_internal_error_returns_stop(sup):
    bad = dataclasses.replace(make_p(1.0), mobs=None)  # mobs=None is tolerated...
    assert sup.check(bad, 1.0).kind == VerdictKind.OK
    assert sup.check(object(), 2.0).kind == VerdictKind.STOP   # ...garbage is not
    assert "internal error" in sup.verdict.reason


# ---- HP unreadable ------------------------------------------------------------------------
def test_hp_unreadable_triggers_pause(sup):
    run(sup, 0, 1)
    res = run(sup, 1.0, 11.5, hp=None)
    t, v = first_non_ok(res)
    assert v.kind == VerdictKind.PAUSE and t == pytest.approx(11.0)
    assert v.reason.startswith("Can't read your HP bar for 10 s — the bot can't tell")
    assert "redo that setup step" in v.reason


def test_hp_unreadable_just_under_threshold_ok(sup):
    assert first_non_ok(run(sup, 0, 9.95, hp=None)) is None


def test_hp_unreadable_interrupted_ok(sup):
    run(sup, 0, 9.0, hp=None)
    run(sup, 9.0, 9.1, hp=0.8)                     # one good reading resets it
    assert first_non_ok(run(sup, 9.1, 19.0, hp=None)) is None


def test_hp_unreadable_reset_clears(sup):
    run(sup, 0, 9.5, hp=None)
    sup.reset(9.5)
    assert first_non_ok(run(sup, 9.5, 19.4, hp=None)) is None


# ---- off platform --------------------------------------------------------------------------
def off_platform():
    return Decision(Action.IDLE, "OFF_PLATFORM", reason="minimap y below platform")


def drive(sup, start, end, decision):
    """Runner order: check() then note_decision() each tick."""
    out = []
    for i in range(int(round((end - start) / DT))):
        t = round(start + i * DT, 6)
        out.append((t, sup.check(make_p(t), t)))
        sup.note_decision(decision, t)
    return out


def test_off_platform_triggers_pause(sup):
    drive(sup, 0, 1, Decision(Action.WALK_LEFT, "PATROL"))
    res = drive(sup, 1.0, 3.5, off_platform())
    t, v = first_non_ok(res)
    # first OFF_PLATFORM noted at 1.0 → 2.0 s held at the check on t=3.0
    assert v.kind == VerdictKind.PAUSE and t == pytest.approx(3.0)
    assert v.reason == ("Your character isn't on the training platform any more — it may "
                        "have fallen to another bridge. Move it back and resume.")


def test_off_platform_just_under_threshold_ok(sup):
    res = drive(sup, 0, 2.0, off_platform())       # held 1.9 s at last check
    assert first_non_ok(res) is None


def test_off_platform_interrupted_ok(sup):
    drive(sup, 0, 1.8, off_platform())
    drive(sup, 1.8, 1.9, Decision(Action.WALK_RIGHT, "RECOVER"))  # back on platform
    assert first_non_ok(drive(sup, 1.9, 3.8, off_platform())) is None


def test_off_platform_without_decisions_never_triggers(sup):
    sup.note_decision(off_platform(), 0.0)
    sup.note_decision(Decision(Action.IDLE, "PATROL"), 0.1)
    assert first_non_ok(run(sup, 0.2, 20)) is None


def test_off_platform_reset_clears(sup):
    drive(sup, 0, 1.9, off_platform())
    sup.reset(1.9)
    assert sup.check(make_p(2.5), 2.5).kind == VerdictKind.OK
    sup.note_decision(off_platform(), 2.5)
    assert sup.check(make_p(4.4), 4.4).kind == VerdictKind.OK
    assert sup.check(make_p(4.5), 4.5).kind == VerdictKind.PAUSE


# ---- promotable constants via getattr ------------------------------------------------------
def test_defaults_when_settings_lack_fields(sup):
    assert not hasattr(sup.settings.safety, "stuck_window_s")
    assert sup.stuck_window_s == 60.0 and sup.hp_critical_hold_s == 1.0


def _with_extra(**extra):
    s = make_settings()
    for k, v in extra.items():
        object.__setattr__(s.safety, k, v)        # simulate a future settings field
    return s


def test_hp_critical_hold_read_from_settings():
    sup = Supervisor(_with_extra(hp_critical_hold_s=0.3))
    sup.start(0.0)
    t, v = first_non_ok(run(sup, 0, 1.0, hp=0.05))
    assert v.kind == VerdictKind.STOP and t == pytest.approx(0.3)


def test_stuck_window_read_from_settings():
    sup = Supervisor(_with_extra(stuck_window_s=10.0))
    sup.start(0.0)
    for i in range(6):                             # 6 jumps over 15 s: OK with a 10 s window
        t = i * 3.0
        sup.note_decision(stuck_jump(), t)
        assert sup.check(make_p(t), t).kind == VerdictKind.OK
    sup2 = Supervisor(make_settings())             # same sequence with default 60 s → PAUSE
    sup2.start(0.0)
    for i in range(6):
        sup2.note_decision(stuck_jump(), i * 3.0)
    assert sup2.check(make_p(15.0), 15.0).kind == VerdictKind.PAUSE
