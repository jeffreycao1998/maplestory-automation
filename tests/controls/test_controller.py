import threading

import pytest

from maplebot.controls import Controller, FakeBackend
from maplebot.controls.backends import BackendError
from maplebot.settings import Settings
from maplebot.types import Action

DOWN, UP = True, False


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def settings():
    s = Settings()
    s.keys.hp_potion = "pgup"     # alias on purpose
    s.keys.mp_potion = "Delete"
    return s


def make(settings, clock, **fb):
    backend = FakeBackend(clock=clock, **fb)
    ctrl = Controller(settings, backend, sleep=clock.sleep)
    return ctrl, backend


def ex(ctrl, *actions):
    for a in actions:
        ctrl.execute(a, 0.0)


# ---- per-action key sequences ------------------------------------------------

def test_idle_does_nothing(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.IDLE)
    assert b.events == []


def test_walk_left_holds_left(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    assert b.sequence() == [("left", DOWN)]
    assert b.held == {"left"}
    assert c.walking == "left" and c.facing == "left"


def test_walk_right_holds_right(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_RIGHT)
    assert b.sequence() == [("right", DOWN)]
    assert c.walking == "right" and c.facing == "right"


def test_repeated_walk_is_noop(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT, Action.WALK_LEFT, Action.WALK_LEFT)
    assert b.sequence() == [("left", DOWN)]


def test_walk_direction_switch_releases_opposite_first(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT, Action.WALK_RIGHT)
    assert b.sequence() == [("left", DOWN), ("left", UP), ("right", DOWN)]
    assert b.held == {"right"}
    assert c.walking == "right" and c.facing == "right"


def test_idle_keeps_walking(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_RIGHT, Action.IDLE, Action.IDLE)
    assert b.held == {"right"} and c.walking == "right"


def test_stop_walk(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT, Action.STOP_WALK)
    assert b.sequence() == [("left", DOWN), ("left", UP)]
    assert b.held == set() and c.walking is None
    ex(c, Action.STOP_WALK)                     # nothing held -> no events
    assert len(b.events) == 2


def test_face_taps_direction_for_turn_tap_s(settings, clock):
    settings.behavior.turn_tap_s = 0.07
    c, b = make(settings, clock)
    ex(c, Action.FACE_LEFT)
    assert b.sequence() == [("left", DOWN), ("left", UP)]
    assert b.events[1][0] - b.events[0][0] == pytest.approx(0.07)
    assert c.facing == "left" and c.walking is None and b.held == set()


def test_face_when_already_facing_is_noop(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.FACE_RIGHT)
    n = len(b.events)
    ex(c, Action.FACE_RIGHT)
    assert len(b.events) == n
    ex(c, Action.WALK_LEFT, Action.STOP_WALK)
    n = len(b.events)
    ex(c, Action.FACE_LEFT)                     # walking left set facing already
    assert len(b.events) == n


def test_face_while_walking_stops_walk(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT, Action.FACE_RIGHT)
    assert b.sequence() == [("left", DOWN), ("left", UP), ("right", DOWN), ("right", UP)]
    assert c.walking is None and c.facing == "right" and b.held == set()


def test_face_same_way_while_walking_only_stops(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT, Action.FACE_LEFT)
    assert b.sequence() == [("left", DOWN), ("left", UP)]
    assert c.walking is None and c.facing == "left"


def test_attack_holds_for_skill_hold_s(settings, clock):
    settings.skill.hold_s = 0.12
    c, b = make(settings, clock)
    ex(c, Action.ATTACK)
    assert b.sequence() == [("ctrl", DOWN), ("ctrl", UP)]
    assert b.events[1][0] - b.events[0][0] == pytest.approx(0.12)
    assert b.held == set()


def test_attack_has_no_cooldown_logic(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.ATTACK, Action.ATTACK)
    assert b.sequence() == [("ctrl", DOWN), ("ctrl", UP)] * 2


def test_attack_while_walking_stops_walk_first(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_RIGHT, Action.ATTACK)
    assert b.sequence() == [("right", DOWN), ("right", UP), ("ctrl", DOWN), ("ctrl", UP)]
    assert c.walking is None and c.facing == "right" and b.held == set()


def test_jump_keeps_walk_held(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT, Action.JUMP)
    assert b.sequence() == [("left", DOWN), ("alt", DOWN), ("alt", UP)]
    assert b.events[2][0] - b.events[1][0] == pytest.approx(0.05)
    assert b.held == {"left"} and c.walking == "left"


def test_jump_standing(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.JUMP)
    assert b.sequence() == [("alt", DOWN), ("alt", UP)]


def test_potions_tap_configured_keys(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.USE_HP_POT, Action.USE_MP_POT)
    assert b.sequence() == [("pageup", DOWN), ("pageup", UP), ("delete", DOWN), ("delete", UP)]
    assert b.held == set()


def test_potions_unset_no_events(clock):
    s = Settings()                      # hp_potion / mp_potion = ""
    c, b = make(s, clock)
    ex(c, Action.USE_HP_POT, Action.USE_MP_POT)
    assert b.events == []


def test_potion_while_walking_keeps_walking(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_RIGHT, Action.USE_HP_POT)
    assert b.held == {"right"} and c.walking == "right"


def test_custom_keybinds_are_normalized(clock):
    s = Settings()
    s.keys.attack = "Z"
    s.keys.jump = "Space"
    s.keys.left = "a"
    s.keys.right = "d"
    c, b = make(s, clock)
    ex(c, Action.ATTACK, Action.JUMP, Action.WALK_LEFT)
    assert b.sequence() == [("z", DOWN), ("z", UP), ("space", DOWN), ("space", UP), ("a", DOWN)]


def test_execute_accepts_action_string_values(settings, clock):
    c, b = make(settings, clock)
    c.execute("walk_left", 0.0)
    assert b.held == {"left"}


def test_invalid_key_in_settings_raises_friendly_error(clock):
    s = Settings()
    s.keys.attack = "ctlr"
    with pytest.raises(ValueError, match="Attack key"):
        Controller(s, FakeBackend())


def test_same_left_right_rejected():
    s = Settings()
    s.keys.right = "left"
    with pytest.raises(ValueError):
        Controller(s, FakeBackend())


# ---- update_settings -----------------------------------------------------------

def test_update_settings_new_keys_releases_and_rebinds(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    new = Settings()
    new.keys.left, new.keys.right, new.keys.attack = "a", "d", "x"
    c.update_settings(new)
    assert b.held == set() and c.walking is None
    ex(c, Action.WALK_RIGHT, Action.ATTACK)
    assert b.sequence()[-4:] == [("d", DOWN), ("d", UP), ("x", DOWN), ("x", UP)]


def test_update_settings_same_keys_keeps_walking(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    new = Settings.from_dict(settings.to_dict())
    new.skill.hold_s = 0.2
    c.update_settings(new)
    assert b.held == {"left"} and c.walking == "left"
    ex(c, Action.ATTACK)
    assert b.events[-1][0] - b.events[-2][0] == pytest.approx(0.2)


def test_update_settings_invalid_keeps_old(settings, clock):
    c, b = make(settings, clock)
    bad = Settings()
    bad.keys.jump = "nope"
    with pytest.raises(ValueError):
        c.update_settings(bad)
    ex(c, Action.JUMP)
    assert b.sequence() == [("alt", DOWN), ("alt", UP)]


# ---- safety ----------------------------------------------------------------------

def test_exception_on_key_down_mid_tap_leaves_nothing_held(settings, clock):
    c, b = make(settings, clock, fail_on={"ctrl"})
    ex(c, Action.WALK_LEFT)
    with pytest.raises(BackendError):
        ex(c, Action.ATTACK)
    c.release_all()
    assert b.held == set() and c.held_keys == frozenset()
    assert c.walking is None


def test_exception_during_hold_releases_tapped_key(settings, clock):
    def boom(_s):
        raise KeyboardInterrupt

    b = FakeBackend(clock=clock)
    c = Controller(settings, b, sleep=boom)
    ex(c, Action.WALK_RIGHT)
    with pytest.raises(KeyboardInterrupt):
        ex(c, Action.JUMP)
    # the tapped key is released by the failing call itself; the walk stays held
    assert b.held == {"right"}
    c.release_all()
    assert b.held == set()


def test_exception_on_walk_press_leaves_nothing_new_held(settings, clock):
    c, b = make(settings, clock, fail_on={"right"})
    ex(c, Action.WALK_LEFT)
    with pytest.raises(BackendError):
        ex(c, Action.WALK_RIGHT)
    assert b.held == set()
    assert c.walking is None
    c.release_all()
    assert b.held == set()


def test_failed_release_is_retried_and_release_all_never_raises(settings, clock):
    c, b = make(settings, clock, fail_up_on={"left"})
    ex(c, Action.WALK_LEFT)
    c.release_all()                    # backend throws: logged, not raised
    assert "left" in c.held_keys       # still tracked so it gets retried
    b.fail_up_on.clear()
    c.release_all()
    assert b.held == set() and c.held_keys == frozenset()


def test_failed_tap_release_retried_by_release_all(settings, clock):
    c, b = make(settings, clock, fail_up_on={"ctrl"})
    with pytest.raises(BackendError):
        ex(c, Action.ATTACK)
    assert b.held == {"ctrl"}
    b.fail_up_on.clear()
    c.release_all()
    assert b.held == set()


def test_release_all_twice(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_RIGHT)
    c.release_all()
    n = len(b.events)
    c.release_all()
    assert len(b.events) == n and b.held == set() and c.walking is None


def test_release_all_on_fresh_controller(settings, clock):
    c, b = make(settings, clock)
    c.release_all()
    assert b.events == []


def test_context_manager_exit_while_walking(settings, clock):
    b = FakeBackend(clock=clock)
    with Controller(settings, b, sleep=clock.sleep) as c:
        ex(c, Action.WALK_LEFT)
        assert b.held == {"left"}
    assert b.held == set() and c.walking is None


def test_context_manager_exit_on_exception(settings, clock):
    b = FakeBackend(clock=clock)
    with pytest.raises(RuntimeError):
        with Controller(settings, b, sleep=clock.sleep) as c:
            ex(c, Action.WALK_RIGHT)
            raise RuntimeError("crash in runner")
    assert b.held == set()


def test_pause_while_walking_then_resume(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    c.release_all()                    # runner pauses
    assert b.held == set()
    ex(c, Action.WALK_LEFT)            # after resume WALK_LEFT must press again
    assert b.held == {"left"}


def test_atexit_hook_releases_and_is_weakref_safe(settings, clock, monkeypatch):
    import gc
    import weakref
    from maplebot.controls import controller as mod

    registered = []
    monkeypatch.setattr(mod.atexit, "register", lambda f: registered.append(f))
    monkeypatch.setattr(mod.atexit, "unregister", lambda f: None)
    b = FakeBackend(clock=clock)
    c = Controller(settings, b, sleep=clock.sleep)
    ex(c, Action.WALK_RIGHT)
    registered[-1]()                   # simulate interpreter exit
    assert b.held == set()

    ref = weakref.ref(c)
    del c
    gc.collect()
    assert ref() is None               # the atexit hook did not keep it alive
    registered[-1]()                   # and calling it afterwards is harmless


def test_close_unregisters_atexit(settings, clock, monkeypatch):
    from maplebot.controls import controller as mod
    unreg = []
    monkeypatch.setattr(mod.atexit, "register", lambda f: None)
    monkeypatch.setattr(mod.atexit, "unregister", lambda f: unreg.append(f))
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    c.close()
    c.close()
    assert b.held == set() and len(unreg) == 1


def test_release_all_from_other_thread_waits_for_tap(settings):
    """GUI thread release_all during a cast: never interleaves inside the tap."""
    started = threading.Event()
    go = threading.Event()

    def slow_sleep(_s):
        started.set()
        go.wait(2)

    b = FakeBackend()
    c = Controller(settings, b, sleep=slow_sleep)
    c.execute(Action.WALK_LEFT, 0.0)
    t = threading.Thread(target=c.execute, args=(Action.JUMP, 0.0))
    t.start()
    started.wait(2)
    r = threading.Thread(target=c.release_all)
    r.start()
    r.join(0.1)
    assert r.is_alive()                # blocked on the lock while jump is held
    go.set()
    t.join(2)
    r.join(2)
    assert b.held == set()
    assert b.sequence() == [("left", DOWN), ("alt", DOWN), ("alt", UP), ("left", UP)]


@pytest.mark.parametrize("seq", [
    [Action.WALK_LEFT, Action.JUMP, Action.ATTACK, Action.WALK_RIGHT, Action.FACE_LEFT],
    [Action.WALK_RIGHT, Action.USE_HP_POT, Action.WALK_LEFT, Action.IDLE],
    list(Action),
])
def test_every_sequence_ends_with_zero_held_after_release_all(settings, clock, seq):
    c, b = make(settings, clock)
    ex(c, *seq)
    c.release_all()
    assert b.held == set()
    # every down has a matching up, and no up without a prior down
    depth = {}
    for _, k, d in b.events:
        depth[k] = depth.get(k, 0) + (1 if d else -1)
        assert depth[k] in (0, 1)
    assert all(v == 0 for v in depth.values())


# ---- inhibit / allow (pause & stop from another thread) -------------------------

def test_inhibit_then_execute_produces_no_events(settings, clock):
    c, b = make(settings, clock)
    c.inhibit("paused by user")
    assert c.inhibited
    for a in Action:
        ex(c, a)
    assert b.events == [] and b.held == set()


def test_inhibit_releases_held_walk(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    c.inhibit()
    assert b.held == set() and c.walking is None
    ex(c, Action.WALK_LEFT)                 # the in-flight tick from the bug report
    assert b.held == set()
    assert b.sequence() == [("left", DOWN), ("left", UP)]


def test_allow_reenables(settings, clock):
    c, b = make(settings, clock)
    c.inhibit()
    c.allow()
    assert not c.inhibited
    ex(c, Action.WALK_RIGHT)
    assert b.held == {"right"}


def test_inhibit_and_release_all_idempotent(settings, clock):
    c, b = make(settings, clock)
    ex(c, Action.WALK_RIGHT)
    c.inhibit("a")
    c.inhibit("b")
    c.release_all()
    c.release_all()
    c.inhibit()
    assert b.sequence() == [("right", DOWN), ("right", UP)]
    assert c.inhibited and b.held == set()
    c.allow()
    c.allow()
    assert not c.inhibited


def test_inhibit_never_raises_when_backend_fails(settings, clock):
    c, b = make(settings, clock, fail_up_on={"left"})
    ex(c, Action.WALK_LEFT)
    c.inhibit()                             # release fails: logged, not raised
    assert c.inhibited
    ex(c, Action.WALK_RIGHT)                # still blocked
    assert ("right", DOWN) not in b.sequence()
    b.fail_up_on.clear()
    c.release_all()
    assert b.held == set()


def test_inhibit_from_other_thread_during_long_tap(settings):
    """inhibit() during a mid-hold attack while walking: waits for the tap, then
    nothing is held and no further key events happen."""
    started = threading.Event()
    go = threading.Event()

    def slow_sleep(_s):
        started.set()
        go.wait(2)

    b = FakeBackend()
    c = Controller(settings, b, sleep=slow_sleep)
    c.execute(Action.WALK_LEFT, 0.0)
    c.execute(Action.STOP_WALK, 0.0)
    c.execute(Action.WALK_LEFT, 0.0)
    runner = threading.Thread(target=c.execute, args=(Action.JUMP, 0.0))
    runner.start()
    assert started.wait(2)                  # jump key is mid-hold
    gui = threading.Thread(target=c.inhibit, args=("F11",))
    gui.start()
    gui.join(0.1)
    assert gui.is_alive()                   # blocked until the tap finishes
    go.set()
    runner.join(2)
    gui.join(2)
    assert not runner.is_alive() and not gui.is_alive()
    assert b.held == set() and c.inhibited
    n = len(b.events)
    for a in (Action.WALK_LEFT, Action.ATTACK, Action.JUMP, Action.FACE_RIGHT):
        c.execute(a, 0.0)                   # ticks already in flight after F11
    assert len(b.events) == n and b.held == set()
    assert b.sequence()[-3:] == [("alt", DOWN), ("alt", UP), ("left", UP)]


def test_inhibit_race_stress(settings):
    """A runner hammering WALK while another thread inhibits: once inhibit()
    returns, nothing is held and nothing is ever pressed again."""
    b = FakeBackend()
    c = Controller(settings, b, sleep=lambda s: None)
    stop = threading.Event()
    seq = [Action.WALK_LEFT, Action.WALK_RIGHT, Action.JUMP, Action.ATTACK]

    def runner():
        i = 0
        while not stop.is_set():
            c.execute(seq[i % len(seq)], 0.0)
            i += 1

    t = threading.Thread(target=runner)
    t.start()
    for _ in range(200):
        c.inhibit()
        n = len(b.events)
        assert b.held == set()
        threading.Event().wait(0.0005)
        assert len(b.events) == n           # no presses while inhibited
        c.allow()
    c.inhibit()
    stop.set()
    t.join(2)
    assert b.held == set()


def test_close_inhibits(settings, clock, monkeypatch):
    from maplebot.controls import controller as mod
    monkeypatch.setattr(mod.atexit, "unregister", lambda f: None)
    c, b = make(settings, clock)
    ex(c, Action.WALK_LEFT)
    c.close()
    ex(c, Action.WALK_LEFT)
    assert b.held == set() and c.inhibited
