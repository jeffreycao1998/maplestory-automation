"""SceneSequence world sim: walking, camera, attacks, respawn, determinism."""
from __future__ import annotations

import numpy as np
import pytest

from maplebot.replay import synthetic as S
from maplebot.types import Action, Frame


def _dot_x(seq) -> float:
    return seq.truth()["player_map"][0]


def test_frame_type_and_time(scene_sequence):
    f = scene_sequence.frame(0.5)
    assert isinstance(f, Frame)
    assert f.t == pytest.approx(0.5)
    assert f.image.shape == (768, 1024, 3)


def test_walking_moves_dot_and_stop_halts(scene_sequence):
    seq = scene_sequence
    seq.frame(0.0)
    x0 = _dot_x(seq)
    seq.apply(Action.WALK_RIGHT)
    seq.frame(1.0)
    x1 = _dot_x(seq)
    assert x1 > x0 + 0.02
    seq.apply(Action.STOP_WALK)
    seq.frame(2.0)
    assert _dot_x(seq) == pytest.approx(x1, abs=1e-3)
    seq.apply(Action.WALK_LEFT)
    seq.frame(3.0)
    assert _dot_x(seq) < x1 - 0.02
    assert seq.facing == "left"


def test_face_turns_without_walking(scene_sequence):
    seq = scene_sequence
    seq.apply(Action.WALK_RIGHT)
    seq.advance(0.5)
    seq.apply(Action.FACE_LEFT)
    x = seq.player_wx
    seq.advance(1.5)
    assert seq.player_wx == x and seq.facing == "left" and seq.walking is None


def test_camera_keeps_player_centred_and_mobs_shift():
    seq = S.SceneSequence(seed=1, n_mobs=0, mob_xs=[1500.0])
    t0 = seq.truth()
    seq.apply(Action.WALK_RIGHT)
    seq.frame(1.0)
    t1 = seq.truth()
    assert t0["player_screen"][0] == t1["player_screen"][0] == 512
    # the mob is static in the world, so it moves left on screen
    assert t1["mobs"][0]["box"][0] < t0["mobs"][0]["box"][0] - 50


def test_camera_clamps_at_world_edge():
    seq = S.SceneSequence(seed=0, player_x=100, n_mobs=0)
    assert seq.truth()["player_screen"][0] == 100


def test_wall_stops_player():
    seq = S.SceneSequence(seed=0, player_x=200, n_mobs=0)
    seq.apply(Action.WALK_LEFT)
    seq.advance(5.0)
    assert seq.player_wx == pytest.approx(seq.margin)


def test_blocker_stops_until_jump():
    seq = S.SceneSequence(seed=0, player_x=1000, n_mobs=0, blockers=[1100])
    seq.apply(Action.WALK_RIGHT)
    seq.advance(2.0)
    assert seq.player_wx < 1100
    seq.apply(Action.JUMP)
    seq.advance(3.0)
    assert seq.player_wx > 1100


def test_attack_two_hits_kill_then_respawn():
    seq = S.SceneSequence(seed=0, player_x=1000, n_mobs=0, mob_xs=[1200.0, 1450.0],
                          respawn_s=3.0, attack_cooldown_s=0.4)
    seq.apply(Action.FACE_RIGHT)
    seq.apply(Action.ATTACK, t=0.1)
    assert seq.mobs[0].hits == 1 and seq.mobs[0].alive
    seq.apply(Action.ATTACK, t=0.2)          # inside the cooldown: ignored
    assert seq.casts == 1
    seq.apply(Action.ATTACK, t=0.6)
    assert not seq.mobs[0].alive and seq.kills == 1
    assert len(seq.truth()["mobs"]) == 1     # only the far one (out of range) left on screen
    assert seq.mobs[1].hits == 0
    seq.advance(3.7)
    assert seq.mobs[0].alive and seq.mobs[0].hits == 0
    assert abs(seq.mobs[0].wx - seq.player_wx) > 100


def test_attack_misses_behind_and_out_of_range():
    seq = S.SceneSequence(seed=0, player_x=1000, n_mobs=0, mob_xs=[800.0, 1500.0])
    seq.apply(Action.FACE_RIGHT)
    seq.apply(Action.ATTACK, t=0.1)
    assert seq.hits == 0                     # 800 is behind, 1500 beyond 350 px
    seq.apply(Action.FACE_LEFT)
    seq.apply(Action.ATTACK, t=1.0)
    assert seq.mobs[0].hits == 1


def test_attack_stops_walking():
    seq = S.SceneSequence(seed=0, n_mobs=0)
    seq.apply(Action.WALK_RIGHT)
    seq.apply(Action.ATTACK)
    assert seq.walking is None


def test_potions_and_mp_cost():
    seq = S.SceneSequence(seed=0, n_mobs=0, hp=0.3, mp=0.5, mp_per_cast=0.1, potion_amount=0.4)
    seq.apply(Action.ATTACK)
    assert seq.mp == pytest.approx(0.4)
    seq.apply(Action.USE_HP_POT)
    seq.apply(Action.USE_MP_POT)
    assert seq.hp == pytest.approx(0.7) and seq.mp == pytest.approx(0.8)
    assert seq.truth()["hp"] == pytest.approx(0.7, abs=0.01)


def test_sequence_deterministic():
    def run():
        seq = S.SceneSequence(seed=5, mob_speed=40)
        seq.apply(Action.WALK_RIGHT)
        seq.frame(1.0)
        seq.apply(Action.ATTACK)
        return seq.frame(2.0).image
    assert np.array_equal(run(), run())


def test_settings_match_world(scene_sequence):
    s = scene_sequence.settings()
    assert s.calibration.minimap_box == list(scene_sequence.minimap_box)
    assert s.problems() == []
