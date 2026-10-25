from dataclasses import replace
from pathlib import Path
import json
import sys
import threading
import time

import cv2
import numpy as np
import pytest

from maplebot.app.logic import pick_safe_spot, build_settings, iter_fields, format_value, get_value
from maplebot.behavior import Behavior
from maplebot.controls import Controller, FakeBackend, InputBlocked
from maplebot.mapmodel import MapModel
from maplebot.perception import Perceiver
from maplebot.perception.interruptions import central_panel
from maplebot.settings import Settings, Profile
from maplebot.supervisor import Supervisor
from maplebot.types import Action as A, Decision, Flag, Frame, Mob, Perception, VerdictKind


def settings():
    s=Settings();s.loot.enabled=False
    s.calibration.patrol_y=.5
    s.calibration.minimap_box=[10,10,200,100]
    s.calibration.safe_spot=[.4,.5]
    return s


def p(x=.6,y=.5,hp=1,mp=1,mobs=()):
    return Perception(0,(800,600),(x,y),(400,400),False,tuple(mobs),hp,mp)


def behavior(s):return Behavior(s,MapModel.from_settings(s))


def test_reserve_trigger_ignores_mobs_and_loot_and_stays_latched_when_bars_recover():
    s=settings();s.retreat.mp_start=10
    b=behavior(s)
    d=b.decide(p(mobs=[Mob(420,400,30,30,1,'slime')]),0)
    assert d.state=='RETREAT_WALK' and d.action==A.WALK_LEFT
    assert 'MP potions' in d.retreat_reason
    assert b.decide(p(x=.55,mp=1),.1).state=='RETREAT_WALK'
    b.reset()
    assert b.decide(p(x=.55),.2).retreat_reason


def test_estimates_decrement_and_survive_pause_resume_without_replenishing():
    s=settings();s.keys.mp_potion='a';s.retreat.mp_start=12
    b=behavior(s)
    assert b.decide(p(mp=.2),0).action==A.USE_MP_POT
    assert b.potion_estimates==(None,11)
    b.reset()
    assert b.potion_estimates==(None,11)
    assert b.decide(p(mp=.2),2).action==A.USE_MP_POT
    assert b.potion_estimates==(None,10)
    assert b.decide(p(mp=1),2.1).state=='RETREAT_WALK'


def test_known_empty_stock_does_not_spam_an_unusable_potion_key():
    s=settings();s.retreat.hp_start=0;s.keys.hp_potion='a'
    b=behavior(s)
    assert b.decide(p(hp=.4),0).state=='RETREAT_WALK'
    assert b.potion_estimates==(0,None)


def test_low_mp_triggers_even_without_stock_count_and_potion_wins_first():
    s=settings();s.keys.mp_potion='a'
    b=behavior(s)
    assert b.decide(p(mp=.01),0).action==A.USE_MP_POT
    assert b.decide(p(mp=.6),.1).state=='RETREAT_WALK'
    assert b.potion_estimates==(None,None)


def test_safe_spot_requires_stable_arrival_then_pauses_without_resuming_combat():
    s=settings();s.retreat.mp_start=10;b=behavior(s);supervisor=Supervisor(s)
    backend=FakeBackend()
    with Controller(s,backend,sleep=lambda _:None) as c:
        for now,x in [(0,.6),(.1,.5),(.3,.4),(.4,.4),(.9,.4),(1.4,.4)]:
            obs=p(x=x)
            assert supervisor.check(obs,now).kind==VerdictKind.OK
            d=b.decide(obs,now);c.execute(d.action,now);supervisor.note_decision(d,now)
        assert d.state=='RETREAT_ARRIVED'
        assert not backend.held
        assert supervisor.check(p(x=.4),1.5).kind==VerdictKind.PAUSE
    b.reset();supervisor.reset(1.7)
    assert b.decide(p(x=.4),1.8).state=='RETREAT_ARRIVED'


def test_retreat_deadline_wins_even_when_every_frame_requests_a_potion():
    s=settings();s.retreat.timeout_s=2;s.keys.hp_potion='a';s.retreat.mp_start=10
    b=behavior(s)
    assert b.decide(p(hp=.4),0).action==A.USE_HP_POT
    assert b.decide(p(hp=.4),1.5).action==A.USE_HP_POT
    assert b.decide(p(hp=.4),3).state=='RETREAT_FAILED'


def multi_platform_settings():
    s=settings();s.retreat.mp_start=10;s.calibration.safe_spot=[.5,.4]
    s.calibration.platforms=[{'name':'low','points':[[.1,.7],[.9,.7]]},
                             {'name':'high','points':[[.2,.4],[.8,.4]]}]
    s.calibration.route=['low']
    s.calibration.connections=[{'name':'up','source':'low','target':'high','kind':'jump','launch_x':.5,'land_x':.5}]
    return s


def test_retreat_overrides_patrol_destination_and_uses_existing_flight_watchdog():
    s=multi_platform_settings();b=behavior(s);supervisor=Supervisor(s)
    for now,y in [(0,.7),(.1,.6),(.3,.35),(.4,.4),(.5,.4),(.7,.4),(.8,.4),(1,.4),(1.5,.4)]:
        obs=p(x=.5,y=y)
        assert supervisor.check(obs,now).kind==VerdictKind.OK
        d=b.decide(obs,now);supervisor.note_decision(d,now)
        assert d.retreat_reason and d.action not in (A.ATTACK,A.PICKUP)
        if now==0:assert d.state=='NAV_LAUNCH' and d.action==A.JUMP_UP
    assert d.state=='RETREAT_ARRIVED'
    assert supervisor.check(p(x=.5,y=.4),1.6).kind==VerdictKind.PAUSE


def test_unreachable_safe_platform_stops_and_pauses_without_guessing_a_jump():
    s=multi_platform_settings();s.calibration.connections=[];b=behavior(s);supervisor=Supervisor(s)
    supervisor.check(p(y=.7),0)
    d=b.decide(p(y=.7),0)
    assert d.state=='NAV_FAILED' and d.action==A.STOP_WALK
    supervisor.note_decision(d,0)
    assert supervisor.check(p(y=.7),.1).kind==VerdictKind.PAUSE


def test_flight_already_in_progress_finishes_before_diverting_to_safe_spot():
    s=multi_platform_settings();s.calibration.safe_spot=[.4,.7];s.calibration.route=['high']
    s.retreat.mp_start=20;b=behavior(s)
    assert b.decide(p(x=.5,y=.7),0).state=='NAV_LAUNCH'
    d=b.decide(p(x=.5,y=.6,mp=.01),.1)
    assert d.state=='NAV_TRANSIT' and d.retreat_reason


def test_critical_hp_stop_and_unknown_position_are_not_bypassed_by_retreat():
    s=settings();s.retreat.mp_start=10;b=behavior(s);supervisor=Supervisor(s)
    obs=p(hp=.1);supervisor.check(obs,0)
    supervisor.note_decision(b.decide(obs,0),0)
    assert supervisor.check(obs,1.1).kind==VerdictKind.STOP
    assert b.decide(replace(p(),player_map=None),.1).state=='WAIT'


def test_spell_lock_is_not_skipped_when_a_retreat_triggers():
    s=settings();b=behavior(s);b.facing='right'
    obs=p(mobs=[Mob(450,400,20,20,1,'mob')])
    assert b.decide(obs,0).action==A.ATTACK
    assert b.decide(p(mp=.01),.1).state=='CASTING'
    assert b.decide(p(),.7).state=='RETREAT_WALK'


def test_retreat_not_enabled_without_a_marker_or_when_disabled():
    s=settings();s.calibration.safe_spot=None;s.retreat.mp_start=0;b=behavior(s)
    assert not b.decide(p(mp=.01),0).retreat_reason
    s=settings();s.retreat.enabled=False;s.retreat.mp_start=0
    assert not behavior(s).decide(p(mp=.01),0).retreat_reason


def test_click_coordinates_and_edge_airborne_ambiguous_points_rejected():
    s=settings()
    assert pick_safe_spot(s,(89,59))==pytest.approx([.3975,.495])
    for point in [(5,5),(10,60),(209,60),(90,10)]:
        with pytest.raises(ValueError):pick_safe_spot(s,point)
    s.calibration.patrol_y=None
    with pytest.raises(ValueError,match='height'):pick_safe_spot(s,(89,59))


def test_settings_roundtrip_and_bad_reserves_timing_marker_validation():
    s=settings();s.retreat.mp_start=42;s.play_style.enabled=True
    raw={sp.key:format_value(sp.kind,get_value(s,sp)) for sp in iter_fields()}
    actual,errors=build_settings(s,raw)
    assert not errors and actual.to_dict()==s.to_dict()
    s.retreat.hp_start=-1;s.play_style.reaction_min_s=.5;s.play_style.reaction_max_s=.1
    assert any('hp_start' in error for error in s.mechanics_problems())
    assert any('reaction' in error for error in s.mechanics_problems())


def test_focus_guard_releases_existing_walk_and_requires_explicit_resume():
    active=[True];backend=FakeBackend()
    with Controller(Settings(),backend,sleep=lambda _:None,can_press=lambda:active[0]) as c:
        c.execute(A.WALK_RIGHT,0);assert backend.held=={'right'}
        active[0]=False
        with pytest.raises(InputBlocked):c.execute(A.IDLE,.1)
        assert not backend.held and c.inhibited
        active[0]=True;before=len(backend.events);c.execute(A.WALK_LEFT,.2)
        assert len(backend.events)==before
        c.allow();c.execute(A.WALK_LEFT,.3);assert backend.held=={'left'}


def test_focus_loss_mid_drop_prevents_jump_and_releases_down():
    active=[True];backend=FakeBackend()
    with Controller(Settings(),backend,sleep=lambda _:active.__setitem__(0,False),can_press=lambda:active[0]) as c:
        with pytest.raises(InputBlocked):c.execute(A.DROP_DOWN,0)
        assert not backend.held
        assert not any(key=='alt' and down for key,down in backend.sequence())


def test_unverifiable_focus_fails_closed():
    def broken():raise RuntimeError('API failure')
    backend=FakeBackend()
    with Controller(Settings(),backend,can_press=broken) as c:
        with pytest.raises(InputBlocked):c.execute(A.ATTACK,0)
    assert not backend.events


def test_central_modal_pause_is_immediate_sticky_and_beats_retreat():
    image=np.full((600,800,3),(20,100,30),np.uint8)
    cv2.rectangle(image,(220,180),(580,400),(210,210,210),-1)
    cv2.putText(image,'Manual review',(250,250),cv2.FONT_HERSHEY_SIMPLEX,.7,(0,0,0),2)
    assert central_panel(image)
    per=Perceiver(Settings(),[],None).perceive(Frame(image,0,(0,0,800,600)))
    assert Flag.UNEXPECTED_DIALOG in per.flags
    s=settings();s.retreat.mp_start=10;b=behavior(s);supervisor=Supervisor(s)
    obs=replace(p(),flags=frozenset({Flag.UNEXPECTED_DIALOG}))
    assert b.decide(obs,0).action==A.STOP_WALK
    assert supervisor.check(obs,0).kind==VerdictKind.PAUSE
    assert supervisor.check(p(),.1).kind==VerdictKind.PAUSE
    supervisor.reset(.2);assert supervisor.check(p(),.2).kind==VerdictKind.OK


def test_alert_reference_matches_without_a_large_modal_and_is_always_a_stop(tmp_path):
    rng=np.random.default_rng(5);image=rng.integers(0,255,(180,240,3),dtype=np.uint8)
    alert=image[60:90,80:160].copy();path=tmp_path/'alert_001.png';cv2.imwrite(str(path),alert)
    s=Settings();s.safety.pause_on_dialog=False
    per=Perceiver(s,[],None,alert_templates=[path]).perceive(Frame(image,0,(0,0,240,180)))
    assert Flag.ALERT_MATCH in per.flags
    assert Supervisor(s).check(per,0).kind==VerdictKind.PAUSE


def test_real_frame_has_no_generic_dialog_candidate():
    path=Path(__file__).resolve().parents[1]/'data/labeled/tree-that-grew-2/000001.png'
    assert not central_panel(cv2.imread(str(path)))


def test_play_style_is_repeatable_and_never_delays_potions_or_retreat():
    s=settings();s.play_style.enabled=True;s.keys.hp_potion='a'
    first,second=behavior(s),behavior(s)
    mob=Mob(450,400,20,20,1,'mob')
    actions=[]
    for b in (first,second):
        b.facing='right'
        ds=[b.decide(p(mobs=[mob]),t) for t in (0,.05,.25,.7,.9,1.6)]
        actions.append([d.action for d in ds])
        assert ds[0].action==A.IDLE and ds[2].action==A.ATTACK
        assert b.decide(p(hp=.4),2).action==A.USE_HP_POT
        assert b.decide(p(mp=.01),2.6).state=='RETREAT_WALK'
    assert actions[0]==actions[1]


def test_runner_arrival_releases_keys_and_exposes_manual_pause(tmp_path, monkeypatch):
    from maplebot.runner import BotRunner
    from maplebot.replay.synthetic import write_templates
    monkeypatch.setenv('MAPLEBOT_LOGS',str(tmp_path/'logs'))
    s=settings();s.retreat.mp_start=10;s.safety.start_delay_s=0
    s.calibration.hp_bar_box=[0,580,100,10]
    s.behavior.turn_tap_s=.001
    profile=Profile(tmp_path/'profile');profile.ensure();write_templates(profile.templates_dir)
    positions=iter([(0,.6),(.1,.5),(.3,.4),(.4,.4),(.9,.4),(1.4,.4),(1.5,.4)])
    current=[None]
    class Capture:
        def grab(self):
            t,x=next(positions);current[0]=p(x=x)
            return Frame(np.zeros((600,800,3),np.uint8),t,(0,0,800,600))
    monkeypatch.setattr(Perceiver,'perceive',lambda self,frame:current[0])
    backend=FakeBackend();statuses=[]
    def status(st):
        statuses.append(st)
        if st.paused:runner.stop('Test finished after safe-spot pause')
    runner=BotRunner(profile,s,capture=Capture(),backend=backend,use_hotkeys=False,on_status=status)
    runner.run()
    assert any(st.paused and 'refill' in st.message.lower() for st in statuses)
    assert any(st.mp_pots_estimated==10 for st in statuses)
    assert not backend.held
    records=[json.loads(row) for row in (Path(runner.run_dir)/'ticks.jsonl').read_text().splitlines()]
    assert any(row['state']=='RETREAT_ARRIVED' and row['retreat_reason'] for row in records)
    assert records[-1]['verdict']=='pause'


def test_foreground_verification_requires_exact_window_identity_and_client_box(monkeypatch):
    from maplebot.perception import capture
    import ctypes
    from types import SimpleNamespace
    monkeypatch.setattr(capture,'find_client_area',lambda _: (10,20,800,600))
    cap=capture.ScreenCapture('MapleStory')
    class Foreground:
        value=12
        def __call__(self):return self.value
    fg=Foreground()
    monkeypatch.setattr(capture.sys,'platform','win32')
    monkeypatch.setattr(ctypes,'windll',SimpleNamespace(user32=SimpleNamespace(GetForegroundWindow=fg)),raising=False)
    monkeypatch.setattr(capture,'_enum_windows',lambda _: [(12,'MapleStory',(10,20,800,600))])
    assert cap.is_foreground()
    fg.value=13;assert not cap.is_foreground()
    fg.value=12
    monkeypatch.setattr(capture,'_enum_windows',lambda _: [(12,'MapleStory',(10,20,900,600))])
    assert not cap.is_foreground()


def test_retreat_settings_are_shared_but_marker_and_alerts_survive_map_switches(tmp_path):
    s=settings();s.retreat.mp_start=80;s.play_style.enabled=True
    profile=Profile(tmp_path/'profile');profile.save(s)
    original=profile.current_map
    profile.alerts_dir.mkdir(parents=True,exist_ok=True)
    (profile.alerts_dir/'alert_001.png').write_bytes(b'reference')
    profile.create_map('Second map')
    assert profile.load().calibration.safe_spot is None
    assert profile.load().retreat.mp_start==80
    assert len(profile.alert_templates())==1
    profile.switch_map(original)
    assert profile.load().calibration.safe_spot==[.4,.5]


def test_runner_checks_focus_on_an_injected_capture_too(tmp_path, monkeypatch):
    from maplebot.runner import BotRunner
    from maplebot.replay.synthetic import write_templates
    monkeypatch.setenv('MAPLEBOT_LOGS',str(tmp_path/'logs'))
    s=settings();s.calibration.safe_spot=None;s.safety.start_delay_s=0
    s.calibration.hp_bar_box=[0,580,100,10]
    profile=Profile(tmp_path/'profile');profile.ensure();write_templates(profile.templates_dir)
    class Capture:
        def grab(self):return Frame(np.zeros((600,800,3),np.uint8),0,(0,0,800,600))
        def is_foreground(self):return False
    monkeypatch.setattr(Perceiver,'perceive',lambda *_:p())
    backend=FakeBackend();statuses=[]
    def on_status(st):
        statuses.append(st)
        if st.paused:runner.stop('Finished focus test')
    runner=BotRunner(profile,s,capture=Capture(),backend=backend,use_hotkeys=False,on_status=on_status)
    runner.run()
    assert any(st.paused and 'focus' in st.message for st in statuses)
    assert not backend.events


def test_gui_resume_countdown_allows_returning_focus_before_live_input(tmp_path, monkeypatch):
    from maplebot.runner import BotRunner
    from maplebot.replay.synthetic import write_templates
    import maplebot.perception as perception_module
    monkeypatch.setenv('MAPLEBOT_LOGS',str(tmp_path/'logs'))
    s=settings();s.calibration.safe_spot=None;s.safety.start_delay_s=.05
    s.calibration.hp_bar_box=[0,580,100,10]
    profile=Profile(tmp_path/'profile');profile.ensure();write_templates(profile.templates_dir)
    active=[True];decisions=[];statuses=[]
    class Capture:
        def __init__(self,*_):pass
        def grab(self):return Frame(np.zeros((600,800,3),np.uint8),time.monotonic(),(0,0,800,600))
        def is_foreground(self):return active[0]
        def close(self):pass
    monkeypatch.setattr(perception_module,'ScreenCapture',Capture)
    monkeypatch.setattr(Perceiver,'perceive',lambda *_:p())
    def on_decision(d):
        decisions.append(d)
        if len(decisions)==1:
            runner.pause();active[0]=False;runner.resume()
        else:runner.stop('Finished resume test')
    def on_status(st):
        statuses.append(st)
        if st.countdown_s is not None and st.message.startswith('Resuming'):
            active[0]=True
    backend=FakeBackend()
    runner=BotRunner(profile,s,backend=backend,use_hotkeys=False,on_status=on_status,on_decision=on_decision)
    runner.run()
    assert len(decisions)==2
    assert any(st.message.startswith('Resuming') and st.countdown_s is not None for st in statuses)
    assert not any('focus' in st.message.lower() for st in statuses)
    assert not backend.held


@pytest.mark.parametrize('requested,actual',[(True,True),(True,False),(False,True)])
def test_window_activation_requires_actual_foreground_verification(monkeypatch,requested,actual):
    from maplebot.perception import capture
    import ctypes
    from types import SimpleNamespace
    monkeypatch.setattr(capture,'find_client_area',lambda _: (10,20,800,600))
    cap=capture.ScreenCapture('MapleStory')
    calls=[]
    def activate(hwnd):calls.append(hwnd);return requested
    class Foreground:
        def __call__(self):return 12 if actual else 13
    monkeypatch.setattr(capture.sys,'platform','win32')
    user32=SimpleNamespace(SetForegroundWindow=activate,GetForegroundWindow=Foreground())
    monkeypatch.setattr(ctypes,'windll',SimpleNamespace(user32=user32),raising=False)
    monkeypatch.setattr(capture,'_enum_windows',lambda _: [(12,'MapleStory',(10,20,800,600))])
    assert cap.activate() is actual
    assert calls==[12]
    assert 'foreground verified='+str(actual) in cap.focus_details()
