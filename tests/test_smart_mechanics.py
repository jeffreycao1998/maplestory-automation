"""Recorded-observation sequences drive behavior, safety and fake controls together."""
from dataclasses import replace
import json
from pathlib import Path

import cv2
import numpy as np
import pytest

from maplebot.behavior import Behavior
from maplebot.controls import Controller, FakeBackend
from maplebot.mapmodel import MapModel
from maplebot.perception import Perceiver
from maplebot.perception.autosetup import suggest_regions
from maplebot.perception.sprites import SpriteMatcher
from maplebot.settings import Settings, Profile
from maplebot.supervisor import Supervisor
from maplebot.types import Action as A, Frame, Item, Mob, Perception, VerdictKind
from tools.import_assets import build_pack, extract_map, navigation_candidate


def perception(x=.5, y=.7, hp=1, items=(), mobs=()):
    return Perception(0, (800, 600), (x,y), (400,400), False, tuple(mobs), hp, 1, items=tuple(items))


def navigation_settings():
    s = Settings()
    s.loot.enabled = False
    s.calibration.platforms = [
        {"name":"ground", "points":[[.1,.7],[.9,.7]]},
        {"name":"upper", "points":[[.2,.4],[.8,.4]], "drop_through":True},
    ]
    s.calibration.connections = [
        {"name":"up", "source":"ground", "target":"upper", "kind":"jump", "launch_x":.5,"land_x":.5},
        {"name":"down", "source":"upper", "target":"ground", "kind":"drop", "launch_x":.5,"land_x":.5},
    ]
    s.calibration.route = ["upper","ground"]
    return s


def test_ramp_and_sag_use_local_height_not_one_wide_band():
    s = Settings()
    s.calibration.platforms = [{"name":"ramp", "points":[[.1,.7],[.5,.4],[.9,.7]]}]
    m = MapModel.from_settings(s)
    assert m.on_platform(.3,.55)
    assert m.on_platform(.5,.4)
    assert not m.on_platform(.3,.7)
    assert not m.on_platform(.95,.7)


@pytest.mark.parametrize('mutation', ['solid_drop','upward_drop','gap_walk','edge_landing','nan_timeout','unknown_route','bad_shape'])
def test_invalid_routes_fail_before_input(mutation):
    s = navigation_settings()
    if mutation == 'solid_drop': s.calibration.platforms[1]['drop_through'] = False
    if mutation == 'upward_drop': s.calibration.connections[0]['kind'] = 'drop'
    if mutation == 'gap_walk': s.calibration.connections[0]['kind'] = 'walk'
    if mutation == 'edge_landing': s.calibration.connections[0]['land_x'] = .2
    if mutation == 'nan_timeout': s.calibration.connections[0]['timeout_s'] = float('nan')
    if mutation == 'unknown_route': s.calibration.route = ['missing']
    if mutation == 'bad_shape': s.calibration.platforms[0]['points'] = 'invalid'
    with pytest.raises(ValueError): MapModel.from_settings(s)


def test_jump_drop_and_landing_sequence_keeps_watchdog_and_controls_in_sync():
    s = navigation_settings()
    b, supervisor, backend = Behavior(s, MapModel.from_settings(s)), Supervisor(s), FakeBackend()
    controller = Controller(s, backend, sleep=lambda _: None)
    decisions = []
    with controller:
        for t, y in [(0,.7),(.1,.6),(.3,.35),(.4,.4),(.5,.4),(.7,.4),(.8,.4),
                     (1,.5),(1.1,.7),(1.2,.7),(1.4,.7)]:
            p = perception(y=y)
            assert supervisor.check(p,t).kind == VerdictKind.OK
            d = b.decide(p,t)
            controller.execute(d.action,t)
            supervisor.note_decision(d,t)
            decisions.append(d)
    assert decisions[0].action == A.JUMP_UP
    assert sum(d.state=='NAV_LANDED' for d in decisions) == 2
    assert any(d.action == A.DROP_DOWN for d in decisions)
    assert backend.held == set()
    seq = backend.sequence()
    i = seq.index(('down',True))
    assert seq[i:i+4] == [('down',True),('alt',True),('alt',False),('down',False)]


def test_failed_landing_pauses_even_if_player_returns_to_source():
    s = navigation_settings()
    b, supervisor = Behavior(s,MapModel.from_settings(s)), Supervisor(s)
    p = perception()
    assert supervisor.check(p,0).kind == VerdictKind.OK
    d=b.decide(p,0);supervisor.note_decision(d,0)
    assert d.state=='NAV_LAUNCH'
    assert supervisor.check(p,1.6).kind == VerdictKind.PAUSE
    assert b.decide(p,1.6).state=='NAV_FAILED'


def test_no_navigation_exemption_for_missing_player_or_critical_hp():
    s = navigation_settings(); s.safety.player_lost_s=.2
    b, supervisor=Behavior(s,MapModel.from_settings(s)), Supervisor(s)
    p=perception();supervisor.check(p,0);supervisor.note_decision(b.decide(p,0),0)
    missing=replace(p,player_map=None)
    supervisor.check(missing,.1)
    assert supervisor.check(missing,.4).kind == VerdictKind.PAUSE


@pytest.mark.parametrize('action',[A.JUMP,A.JUMP_UP,A.JUMP_LEFT,A.JUMP_RIGHT,A.DROP_DOWN,A.WALK_LEFT,A.FACE_LEFT])
def test_controller_enforces_spell_lock_and_releases_afterwards(action):
    s=Settings(); backend=FakeBackend()
    with Controller(s,backend,sleep=lambda _:None) as c:
        c.execute(A.ATTACK,0)
        before=len(backend.events)
        c.execute(action,.1)
        assert len(backend.events)==before
        c.execute(action,.7)
        assert len(backend.events)>before
    assert not backend.held


@pytest.mark.parametrize('stage',[1,2])
def test_drop_failure_releases_down_jump_and_walk(stage):
    s=Settings(); backend=FakeBackend(); calls=[]
    def sleep(_):
        calls.append(1)
        if len(calls)==stage: raise RuntimeError('input interrupted')
    with Controller(s,backend,sleep=sleep) as c:
        c.execute(A.WALK_RIGHT,0)
        with pytest.raises(RuntimeError):c.execute(A.DROP_DOWN,1)
        assert not backend.held
        c.inhibit()
        c.execute(A.DROP_DOWN,2)
        assert not backend.held


def test_predictive_edge_braking_and_damage_recovery():
    s=Settings();s.loot.enabled=False
    b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(perception(x=.6),0).action==A.WALK_RIGHT
    # At current speed another reaction interval would cross the right boundary.
    assert b.decide(perception(x=.67),.1).state=='EDGE_GUARD'
    assert b.walking is None
    assert b.decide(perception(x=.66,hp=.8),.2).state=='KNOCKBACK'
    assert b.decide(perception(x=.66,hp=.8),.4).action==A.STOP_WALK
    assert b.decide(perception(x=.66,hp=.8),.8).state=='PATROL'


def test_loot_picks_up_between_fights_and_bounds_uncollectable_drops():
    s=Settings();s.training_only=False;s.loot.max_attempts=2
    b=Behavior(s,MapModel.from_settings(s))
    item=Item(410,425,20,20,.99,'mesos.png')
    mob=Mob(450,400,30,30,.99,'slime.png')
    p=perception(items=[item],mobs=[mob])
    assert b.decide(p,0).action==A.PICKUP
    assert b.decide(p,.5).action==A.PICKUP
    assert b.decide(p,1).state=='LOOT_SKIP'
    assert b.decide(p,1.1).state=='ENGAGE'


def test_loot_does_not_chase_another_floor_or_past_edge():
    s=Settings();s.training_only=False;b=Behavior(s,MapModel.from_settings(s))
    p=perception(x=.69,items=[Item(700,425,20,20,.99,'coin')])
    d=b.decide(p,0)
    assert d.action!=A.WALK_RIGHT
    p=perception(items=[Item(700,100,20,20,.99,'coin')])
    assert b.decide(p,1).state!='LOOT'
    s.keys.pickup=''
    b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(p,0).action==A.WALK_RIGHT


def test_low_confidence_beats_loot_navigation_and_potions():
    s=navigation_settings();s.loot.enabled=True;s.training_only=False;s.keys.hp_potion='a'
    b=Behavior(s,MapModel.from_settings(s))
    p=replace(perception(hp=.3,items=[Item(410,425,20,20,1,'coin')]),confidence=.4)
    assert b.decide(p,0).state=='WAIT'


def test_autosetup_recovers_exact_real_frame_regions_and_abstains_on_blank():
    root=Path(__file__).resolve().parents[1]/'data/labeled/tree-that-grew-2'
    lab=json.loads((root/'000001.json').read_text())
    result=suggest_regions(cv2.imread(str(root/'000001.png')))
    for key in ('minimap_box','hp_bar_box','mp_bar_box'):assert result[key]==lab[key]
    assert suggest_regions(np.zeros((600,800,3),np.uint8))=={}
    # A red/blue fill without complete capacity frames must not be treated as full bars.
    image=np.zeros((600,800,3),np.uint8)
    image[550:560,100:140]=(0,0,255);image[550:560,180:200]=(255,0,0)
    assert suggest_regions(image)=={}


def test_alpha_sprites_match_over_different_backgrounds_and_ignore_ui(tmp_path):
    rng=np.random.default_rng(44)
    sprite=rng.integers(40,255,(24,24,4),dtype=np.uint8)
    sprite[:,:,3]=0;sprite[3:21,3:21,3]=255
    path=tmp_path/'item_test.png';cv2.imwrite(str(path),sprite)
    matcher=SpriteMatcher();assert matcher.add(path)
    image=np.full((160,200,3),(20,120,10),np.uint8)
    mask=sprite[:,:,3]>0
    image[60:84,80:104][mask]=sprite[:,:,:3][mask]
    assert len(matcher.find(image,.95,mirror=False))==1
    assert matcher.find(image,.95,exclude=[(70,50,50,50)],mirror=False)==[]
    # Imported item sprites are wired into Perception, with UI exclusions and body-to-feet offset.
    tag=rng.integers(0,255,(10,20,3),dtype=np.uint8);tag_path=tmp_path/'player.png';cv2.imwrite(str(tag_path),tag)
    image[100:110,80:100]=tag
    s=Settings();s.training_only=False;s.detection.player_tag_offset_px=58
    p=Perceiver(s,[],tag_path,[path]).perceive(Frame(image,0,(0,0,200,160)))
    assert len(p.items)==1 and not p.player_screen_estimated


def test_new_settings_roundtrip_and_fail_fast_for_unsafe_values(tmp_path):
    s=navigation_settings();s.keys.pickup='x';s.loot.max_attempts=3
    profile=Profile(tmp_path/'profile');profile.save(s)
    assert profile.load().calibration.platforms==s.calibration.platforms
    assert profile.load().keys.pickup=='x'
    s.loot.tap_s=float('nan')
    assert any('loot.tap_s' in x for x in s.mechanics_problems())
    s.keys.down=s.keys.jump
    assert 'Down needs a separate key' in s.mechanics_problems()


def test_catalog_import_selects_map_mobs_and_their_drops_without_network(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import tools.import_assets as importer
    catalog=tmp_path/'catalog';output=tmp_path/'pack'
    def write(path, data):
        p=catalog/path;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data))
    write('WEB/Map/map-links.json',{'maps':{'101010101':{'mapName':'Test','mobs':[{'id':'0210100'}]}}})
    write('WEB/Mob/data.json',[{'id':'0210100','name':'Slime','preview':'Mob/images/slime.png'}])
    write('WEB/Reward/drop-index.json',{'modes':{'monsterBook':{'mobs':{'0210100':[
        {'type':'item','itemId':'04000004','itemName':'Squishy Liquid','itemTab':'Etc'}]}}}})
    write('WEB/Etc/data.json',[{'id':'04000004','name':'Squishy Liquid','preview':'Etc/images/liquid.png'}])
    for f in ['WEB/Mob/images/slime.png','WEB/Etc/images/liquid.png']:
        p=catalog/f;p.parent.mkdir(parents=True,exist_ok=True)
        cv2.imwrite(str(p),np.full((20,20,4),(50,200,40,255),np.uint8))
    monkeypatch.setattr(importer.subprocess,'run',lambda *a,**kw:SimpleNamespace(stdout='abc123\n'))
    manifest=build_pack(catalog,'101010101',output)
    assert len(manifest['entries'])==2
    assert (output/'mob_catalog_210100.png').is_file()
    assert (output/'item_catalog_4000004.png').is_file()
    assert not manifest['validated_on_classic']
    with pytest.raises(ValueError,match='empty'):build_pack(catalog,'101010101',output)


def test_offline_footholds_join_into_ramp_candidates_without_enabling_travel():
    raw={'minimap':{'width':1000,'height':1000,'centerX':0,'centerY':0},'footholds':[
        {'id':'1','layer':'1','group':'0','x1':100,'y1':500,'x2':400,'y2':400,'next':2},
        {'id':'2','layer':'1','group':'0','x1':400,'y1':400,'x2':900,'y2':300,'prev':1},
        {'id':'3','layer':'1','group':'0','x1':900,'y1':300,'x2':900,'y2':600}  # wall
    ]}
    result=navigation_candidate(raw)
    assert result['requires_alignment']
    cal=result['calibration']
    assert len(cal['platforms'])==1
    assert cal['platforms'][0]['points']==[[.1,.5],[.4,.4],[.9,.3]]
    assert not cal['connections'] and not cal['route']
    s=Settings();s.calibration.platforms=cal['platforms']
    assert MapModel.from_settings(s).on_platform(.25,.45)


def test_vertical_navigation_jump_releases_approach_walk():
    s=navigation_settings();backend=FakeBackend()
    b=Behavior(s,MapModel.from_settings(s))
    with Controller(s,backend,sleep=lambda _:None) as c:
        c.execute(b.decide(perception(x=.4),0).action,0)
        assert backend.held=={'right'}
        d=b.decide(perception(x=.5),.7)
        assert d.action==A.JUMP_UP
        c.execute(d.action,.7)
        assert not backend.held and b.walking is None


def test_jumping_past_platform_while_rising_is_not_a_confirmed_landing():
    s=navigation_settings();b=Behavior(s,MapModel.from_settings(s))
    b.decide(perception(),0)
    for t,y in [(.3,.40),(.4,.399),(.6,.398)]:
        assert b.decide(perception(y=y),t).state=='NAV_TRANSIT'
    assert b.decide(perception(y=.7),1.6).state=='NAV_FAILED'


def test_sprite_budget_cycles_through_all_templates(tmp_path):
    matcher=SpriteMatcher();rng=np.random.default_rng(2)
    for i in range(3):
        sprite=rng.integers(0,255,(20,20,4),dtype=np.uint8)
        sprite[:,:,3]=255;sprite[:2,:,3]=0
        path=tmp_path/f'{i}.png';cv2.imwrite(str(path),sprite);matcher.add(path)
    image=np.full((100,120,3),20,np.uint8)
    for i,(_,bgr,mask) in enumerate(matcher.templates):
        roi=image[10:10+bgr.shape[0],i*35:i*35+bgr.shape[1]];roi[mask>0]=bgr[mask>0]
    found=[m.template for _ in range(3) for m in matcher.find(image,.95,mirror=False,budget=1)]
    assert set(found)=={'0.png','1.png','2.png'}


def test_slow_observation_rate_does_not_reset_stuck_navigation_deadline():
    s=navigation_settings();b=Behavior(s,MapModel.from_settings(s))
    for t in (0,.7,1.4,2.1,2.8,3.5,4.2):
        assert b.decide(perception(x=.3),t).state=='NAV_APPROACH'
    assert b.decide(perception(x=.3),5.1).state=='NAV_FAILED'


@pytest.mark.parametrize('tick_s',[.1,.5,1.0])
def test_fallback_pickup_cannot_starve_patrol_at_slow_capture_rates(tick_s):
    s=Settings();s.loot.enabled=True;s.training_only=False
    b=Behavior(s,MapModel.from_settings(s))
    backend=FakeBackend()
    actions=[]
    with Controller(s,backend,sleep=lambda _:None) as controller:
        for i in range(8):
            # Actual forward progress, on the same platform, without visible drops.
            p=perception(x=.4+i*.01)
            d=b.decide(p,i*tick_s);actions.append(d)
            controller.execute(d.action,i*tick_s)
            assert s.keys.right in backend.held
    assert actions[0].action==A.WALK_RIGHT
    assert any(d.state=='PATROL' for d in actions[1:])
    assert not any(a.state==c.state=='LOOT_SWEEP' for a,c in zip(actions,actions[1:]))
    assert not backend.held


def test_fallback_sweep_does_not_hide_stuck_patrol_or_steal_a_route_launch():
    s=Settings();b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(perception(),0).action==A.WALK_RIGHT
    decisions=[b.decide(perception(),t) for t in (1,2,3)]
    assert any(d.state=='STUCK' for d in decisions)
    s=navigation_settings();s.loot.enabled=True;s.training_only=False
    b=Behavior(s,MapModel.from_settings(s))
    d=b.decide(perception(x=.5,y=.7),0)
    assert d.state=='NAV_LAUNCH' and d.action==A.JUMP_UP
