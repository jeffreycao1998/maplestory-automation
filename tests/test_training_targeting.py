"""Recorded-style observations verify conservative Magic Claw training decisions."""
from dataclasses import replace

from maplebot.settings import Profile,Settings
from maplebot.mapmodel import MapModel
from maplebot.behavior import Behavior
from maplebot.behavior.targeting import SkillTargeting
from maplebot.types import Action,Perception,Mob,Item


def observation(*mobs):
    return Perception(0,(800,600),(.5,.7),(400,400),False,tuple(mobs),1.,1.)


def monster(x=500,y=415,h=40):
    return Mob(x,y,40,h,.99,'slime')


def test_magic_claw_uses_300_cap_even_with_old_saved_350_range():
    s=Settings();s.skill.range_px=350
    targeting=SkillTargeting(s,MapModel.from_settings(s))
    assert targeting.range_px==300
    b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(observation(monster(x=701)),0).action!=Action.ATTACK
    assert b.decide(observation(monster(x=701)),.8).action!=Action.ATTACK
    b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(observation(monster(x=700)),0).action==Action.FACE_RIGHT
    assert b.decide(observation(monster(x=700)),.8).action==Action.ATTACK


def test_directly_below_and_tall_lower_platform_sprite_are_never_cast_at():
    s=Settings()
    for mob in (monster(x=400,y=455),monster(x=400,y=415,h=120),monster(x=500,y=365)):
        b=Behavior(s,MapModel.from_settings(s))
        decisions=[b.decide(observation(mob),t) for t in (0,.8,1.6)]
        assert all(d.action!=Action.ATTACK for d in decisions)
        assert any('compatible platform/height' in d.reason for d in decisions)


def test_cast_requires_current_name_position_facing_and_animation_timing():
    s=Settings();p=observation(monster(x=300))
    b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(p,0).action==Action.FACE_LEFT
    assert b.decide(p,.1).action==Action.ATTACK
    assert b.decide(p,.2).action!=Action.ATTACK
    assert b.decide(replace(p,player_screen_estimated=True),1).action!=Action.ATTACK


def test_registered_ramp_uses_surface_height_and_rejects_other_surface():
    s=Settings();s.skill.vertical_tolerance_px=80
    s.calibration.map_reference={'minimap':{'width':1000,'height':1000}}
    s.calibration.platforms=[{'name':'ramp','points':[[.2,.5],[.8,.8]],'tolerance':.015}]
    target=SkillTargeting(s,MapModel.from_settings(s))
    p=replace(observation(),player_map=(.5,.65))
    # +100 screen/world x follows the ramp +50 world y: feet at 435+50.
    assert target.compatible(p,monster(x=500,y=465))
    assert not target.compatible(p,monster(x=500,y=415))
    assert not target.compatible(p,monster(x=750,y=415))


def test_training_only_overrides_old_loot_setting_and_never_pursues_drop(tmp_path):
    profile=Profile(tmp_path/'profile');s=Settings();profile.save(s)
    # Simulate an installed profile created before training_only existed.
    shared=profile._read(profile.shared_settings_path);shared.pop('training_only')
    shared['loot']['enabled']=True
    profile._write(profile.shared_settings_path,shared)
    s=profile.load();assert s.training_only and s.loot.enabled
    b=Behavior(s,MapModel.from_settings(s))
    p=replace(observation(monster()),items=(Item(410,425,20,20,.99,'mesos'),))
    decisions=[b.decide(p,t) for t in (0,.5,1.,1.5)]
    assert any(d.action==Action.ATTACK for d in decisions)
    assert all(d.action!=Action.PICKUP and not d.state.startswith('LOOT') for d in decisions)
    b=Behavior(s,MapModel.from_settings(s))
    decisions=[b.decide(replace(p,mobs=()),t) for t in (0,1,2)]
    assert decisions[0].action==Action.WALK_RIGHT
    assert all(not d.state.startswith('LOOT') for d in decisions)


def test_nearby_lower_source_platform_is_not_mistaken_for_tolerance_jitter():
    s=Settings();s.calibration.map_reference={'minimap':{'width':1000,'height':1000}}
    s.calibration.platforms=[{'name':'here','points':[[.1,.7],[.9,.7]],'tolerance':.005},
                            {'name':'below','points':[[.1,.72],[.9,.72]],'tolerance':.005}]
    targeting=SkillTargeting(s,MapModel.from_settings(s))
    p=observation()
    assert targeting.compatible(p,monster(x=400,y=415))
    # 20 px lower is inside the generic 24 px feet tolerance, but belongs below.
    assert not targeting.compatible(p,monster(x=400,y=435))
