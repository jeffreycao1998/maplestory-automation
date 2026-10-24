"""Shared pytest fixtures (owner: test-harness-engineer).

All fixtures are synthetic and fast (one 1024x768 render, cached per session).

    synthetic_scene  -> SyntheticScene(image, truth, mob_sprite, name_tag, frame)
    scene_settings   -> Settings calibrated to that scene (fresh copy per test)
    tmp_profile      -> Profile in tmp_path with settings.yaml + mob/player templates
    scene_sequence   -> a fresh SceneSequence world (seed 0)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT / "src", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


@dataclass(frozen=True)
class SyntheticScene:
    image: np.ndarray        # BGR uint8 -- read-only; copy before drawing on it
    truth: dict              # label format (data/README.md) + extras
    mob_sprite: np.ndarray   # exact template crop of the scene's mob kind
    name_tag: np.ndarray     # exact template crop of the player's name tag

    @property
    def frame(self):
        from maplebot.types import Frame
        h, w = self.image.shape[:2]
        return Frame(image=self.image.copy(), t=0.0, window=(0, 0, w, h))


@pytest.fixture(scope="session")
def _scene_cache() -> SyntheticScene:
    from maplebot.replay import synthetic as S
    img, truth = S.make_scene(
        seed=0, player_x=512, mobs=[(740, 496, "left"), (240, 496, "right"), (900, 496, "right")],
        player_map=(0.42, 0.62), hp=0.8, mp=0.5)
    img.setflags(write=False)
    return SyntheticScene(img, truth, S.mob_sprite(0), S.name_tag_sprite())


@pytest.fixture
def synthetic_scene(_scene_cache) -> SyntheticScene:
    return _scene_cache


@pytest.fixture
def scene_settings(_scene_cache):
    from maplebot.replay.synthetic import settings_for_scene
    return settings_for_scene(_scene_cache.truth)


@pytest.fixture
def tmp_profile(tmp_path, scene_settings):
    from maplebot.replay.synthetic import write_templates
    from maplebot.settings import Profile
    prof = Profile(tmp_path / "profile")
    prof.ensure()
    write_templates(prof.templates_dir, mob_kinds=(0,))
    prof.save(scene_settings)
    return prof


@pytest.fixture
def scene_sequence():
    from maplebot.replay.synthetic import SceneSequence
    return SceneSequence(seed=0)
