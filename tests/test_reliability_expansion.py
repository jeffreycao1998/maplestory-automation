from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import threading
import time

import cv2
import numpy as np
import pytest

from maplebot.behavior import Behavior
from maplebot.controls import Controller, FakeBackend, InputBlocked
from maplebot.mapmodel import MapModel
from maplebot.perception import Perceiver
from maplebot.perception.hud import HUDFilter, HUDReader, LocalOCR, TextLine, parse_count
from maplebot.replay.folder_capture import FolderCapture, SequenceCapture
from maplebot.settings import Profile, Settings
from maplebot.supervisor import Supervisor
from maplebot.supervisor.economy import EconomyTracker
from maplebot.types import Action as A, Flag, Frame, Perception, VerdictKind
from tools.calibrate_motion import measure
from tools.learn_references import learn


def p(x=.5,y=.7,**kw):
    return replace(Perception(0,(800,600),(x,y),(400,400),False,(),1,1),**kw)


@pytest.mark.parametrize('text,value',[('15',15),('0',0),('123,456',123456),(' 19 ',19),('2,000,000,000',2000000000),
    ('19-',None),('O15',None),('1.0',None),('-1',None),('',None),('1 234',None),('12,34',None),('9999999999',None)])
def test_numeric_ocr_is_strict_and_blank_is_not_zero(text,value):assert parse_count(text)==value


def test_filter_needs_distinct_confirmed_samples_and_invalidates_changed_stale_text():
    s=Settings();f=HUDFilter(s)
    result={'hp_stock':[TextLine('15',.99)]}
    f.accept(result,0);f.accept(result,0);assert 'hp_stock' not in f.snapshot(.1)
    f.accept(result,.5);assert f.snapshot(.6)['hp_stock']==15
    assert 'hp_stock' not in f.snapshot(3)
    f.accept({'hp_stock':[TextLine('14',.99)]},3.1)
    assert 'hp_stock' not in f.snapshot(3.1)
    f.accept({'hp_stock':[TextLine('14',.99)]},3.6);assert f.snapshot(3.7)['hp_stock']==14
    f.accept({'hp_stock':[TextLine('14-',.99)]},4);assert 'hp_stock' not in f.snapshot(4)
    f.accept({'notification':[TextLine('Your inventory is full',.99)]},4.5)
    assert f.snapshot(4.5)['inventory_full']
    assert 'inventory_full' not in f.snapshot(7)


def test_reader_queues_one_background_job_without_blocking_control_ticks():
    entered,gate=threading.Event(),threading.Event();calls=[]
    class Backend:
        def read(self,crop,multiline=False):
            calls.append(1);entered.set();gate.wait(3);return [TextLine('12',.99)]
    s=Settings();s.calibration.hp_stock_box=[0,0,20,10]
    reader=HUDReader(s,Backend());image=np.zeros((50,50,3),np.uint8)
    try:
        reader.poll(image,0);assert entered.wait(1)
        start=time.monotonic()
        for i in range(100):reader.poll(image,i/10)
        assert time.monotonic()-start<.2 and len(calls)==1
    finally:gate.set();reader.close()


def test_perception_stock_is_confirmed_and_missing_stock_gets_a_guard_flag():
    class Backend:
        def read(self,*_,**__):return [TextLine('19',.99)]
    s=Settings();s.ocr.enabled=True;s.calibration.hp_stock_box=[0,0,20,10]
    per=Perceiver(s,[],None,hud_sync=True)
    per._hud.close();per._hud=HUDReader(s,Backend(),synchronous=True)
    try:
        image=np.zeros((50,50,3),np.uint8)
        first=per.perceive(Frame(image,0,(0,0,50,50)))
        second=per.perceive(Frame(image,.6,(0,0,50,50)))
        assert first.hp_stock is None and Flag.STOCK_UNREADABLE in first.flags
        assert second.hp_stock==19 and Flag.STOCK_UNREADABLE not in second.flags
    finally:per.close()


@pytest.mark.skipif(importlib.util.find_spec('rapidocr_onnxruntime') is None,reason='optional local OCR extra')
def test_actual_local_ocr_reads_visible_shortcut_counts_in_existing_real_screenshot():
    image=cv2.imread(str(Path(__file__).resolve().parents[1]/'data/labeled/tree-that-grew-2/000001.png'))
    engine=LocalOCR()
    for box,value in [((940,649,13,12),15),((940,683,13,12),19)]:
        x,y,w,h=box;lines=engine.read(image[y:y+h,x:x+w])
        assert len(lines)==1 and parse_count(lines[0].text)==value and lines[0].confidence>=.9


def test_fresh_visible_stock_wins_without_reusing_old_sample_after_potion_attempt():
    s=Settings();s.keys.hp_potion='a';b=Behavior(s,MapModel.from_settings(s))
    assert b.decide(p(hp=.4,hp_stock=20,hud_t=0),0).action==A.USE_HP_POT
    assert b.potion_estimates[0]==19
    b.decide(p(hp=1,hp_stock=20,hud_t=0),.1)
    assert b.potion_estimates[0]==19
    b.decide(p(hp=1,hp_stock=18,hud_t=.6),.6)
    assert b.potion_estimates[0]==18


def test_unreadable_stock_stops_regular_actions_then_pauses_and_inventory_full_stops_looting():
    s=Settings();b=Behavior(s,MapModel.from_settings(s));sup=Supervisor(s)
    obs=p(flags=frozenset({Flag.STOCK_UNREADABLE}))
    assert b.decide(obs,0).action==A.STOP_WALK
    assert sup.check(obs,0).kind==VerdictKind.OK
    assert sup.check(obs,6).kind==VerdictKind.PAUSE
    sup.reset(7)
    d=b.decide(p(inventory_full=True),7)
    assert d.state=='INVENTORY_FULL' and d.action==A.STOP_WALK
    sup.note_decision(d,7)
    assert sup.check(p(),7.1).kind==VerdictKind.PAUSE


def test_economy_distinguishes_wallet_delta_stock_loss_and_unknown_cost():
    s=Settings();s.keys.hp_potion='a';s.economy.hp_replacement_cost=50
    e=EconomyTracker(s)
    e.observe(p(mesos=1000,hp_stock=10),0)
    e.observe(p(mesos=1200,hp_stock=9),10)
    e.observe(p(mesos=1200,hp_stock=9),20)
    result=e.summary(20)
    assert result['wallet_delta']==200 and result['stock_decrease']['hp']==1
    assert result['replacement_cost_estimate']==50 and result['net_estimate']==150
    assert result['net_per_hour']==27000
    e.observe(p(mesos=1200),21);assert e.summary(21)['net_estimate'] is None
    e.observe(p(),22);assert e.summary(22)['wallet_delta'] is None
    e.observe(p(mesos=1200,hp_stock=12),23)
    assert e.summary(23)['stock_decrease']['hp']==1  # replenishment is not negative spending
    s.economy.hp_replacement_cost=None
    assert e.summary(24)['net_estimate'] is None


def ladder_settings(down=False,grab=False):
    s=Settings();s.loot.enabled=False
    s.calibration.platforms=[{'name':'low','points':[[.1,.7],[.9,.7]]},
                             {'name':'high','points':[[.1,.4],[.9,.4]]}]
    s.calibration.ladders=[{'name':'rope','x':.5,'top':.4,'bottom':.7}]
    s.calibration.connections=[{'name':'climb','source':'high' if down else 'low',
        'target':'low' if down else 'high','kind':'climb','launch_x':.5,'land_x':.5,
        'timeout_s':6,'ladder':'rope','grab_jump':grab}]
    s.calibration.route=['low' if down else 'high']
    return s


@pytest.mark.parametrize('down,grab',[(False,False),(False,True),(True,False)])
def test_climb_traversal_progress_stable_exit_and_key_release(down,grab):
    s=ladder_settings(down,grab);b=Behavior(s,MapModel.from_settings(s));sup=Supervisor(s);backend=FakeBackend()
    heights=[.7,.6,.5,.4,.4,.4] if not down else [.4,.5,.6,.7,.7,.7]
    with Controller(s,backend,sleep=lambda _:None) as c:
        for now,y in zip([0,.2,.5,.8,.9,1.1],heights):
            obs=p(y=y);assert sup.check(obs,now).kind==VerdictKind.OK
            d=b.decide(obs,now);c.execute(d.action,now);sup.note_decision(d,now)
        assert d.state=='NAV_LANDED' and not backend.held
    assert ('up' if not down else 'down',True) in backend.sequence()
    if grab:assert ('alt',True) in backend.sequence()


def test_climb_stall_or_lost_player_releases_vertical_keys():
    s=ladder_settings();b=Behavior(s,MapModel.from_settings(s));backend=FakeBackend()
    with Controller(s,backend,sleep=lambda _:None) as c:
        d=b.decide(p(),0);c.execute(d.action,0);assert backend.held=={'up'}
        d=b.decide(replace(p(),player_map=None,confidence=.4),.1);c.execute(d.action,.1)
        assert not backend.held
    b=Behavior(s,MapModel.from_settings(s));b.decide(p(),0)
    assert b.decide(p(),3).state=='NAV_FAILED'


def test_climb_hold_is_idempotent_and_direction_changes_release_previous_axis():
    backend=FakeBackend()
    with Controller(Settings(),backend,sleep=lambda _:None) as c:
        c.execute(A.CLIMB_UP,0);n=len(backend.events);c.execute(A.CLIMB_UP,.1)
        assert len(backend.events)==n
        c.execute(A.WALK_RIGHT,.2);assert backend.held=={'right'}
        c.execute(A.CLIMB_DOWN,.3);assert backend.held=={'down'}
        c.execute(A.STOP_WALK,.4);assert not backend.held


@pytest.mark.parametrize('bad',['missing','endpoint','direction','nan'])
def test_invalid_ladder_connections_fail_before_input(bad):
    s=ladder_settings()
    if bad=='missing':s.calibration.ladders=[]
    if bad=='endpoint':s.calibration.connections[0]['launch_x']=.6
    if bad=='direction':s=ladder_settings(down=True,grab=True)
    if bad=='nan':s.calibration.ladders[0]['top']=float('nan')
    with pytest.raises(ValueError):MapModel.from_settings(s)


def test_observe_mode_cannot_call_a_supplied_physical_backend(tmp_path,monkeypatch):
    from maplebot.runner import BotRunner
    from maplebot.replay import synthetic as S
    class PhysicalBackend:
        def key_down(self,key):raise AssertionError('physical input in observe mode')
        def key_up(self,key):raise AssertionError('physical input in observe mode')
    seq=S.SceneSequence(seed=1);s=seq.settings();s.safety.start_delay_s=0
    profile=Profile(tmp_path/'profile');profile.ensure();S.write_templates(profile.templates_dir)
    monkeypatch.setenv('MAPLEBOT_LOGS',str(tmp_path/'logs'))
    statuses=[];decisions=[]
    runner=BotRunner(profile,s,capture=SequenceCapture(seq,max_frames=8),backend=PhysicalBackend(),
                     use_hotkeys=False,observe_only=True,on_status=statuses.append,on_decision=decisions.append)
    runner.run()
    assert decisions and any(status.observe_only for status in statuses)
    report=json.loads((Path(runner.run_dir)/'summary.json').read_text())
    assert report['observe_only'] and report['economy']['pickup_attempts']==0


def test_manual_motion_measurement_and_verified_limits_are_only_explicit_candidates():
    s=Settings();samples=[(i*.1,(.2+i*.01,.7)) for i in range(11)]
    events=[{'t':0,'key':'right','down':True},{'t':1,'key':'right','down':False}]
    for start in (2,6,10):
        samples += [(start+dt,(.5+dx,y)) for dt,dx,y in [(0,0,.7),(.1,.02,.5),(.2,.04,.4),(.3,.06,.5),(.4,.1,.7),(.5,.1,.7),(.6,.1,.7)]]
        events += [{'t':start+.01,'key':'alt','down':True},{'t':start+.1,'key':'alt','down':False}]
    result=measure(samples,events,s.keys)
    assert not result['candidate']['verified']
    assert result['candidate']['walk_speed_map_s']==pytest.approx(.1)
    assert result['candidate']['jump_samples']==3
    assert result['candidate']['jump_rise']==pytest.approx(.27)


def test_recorded_capture_uses_real_timestamps_and_rejects_non_monotonic_times(tmp_path):
    for i in range(3):cv2.imwrite(str(tmp_path/f'{i:06d}.png'),np.zeros((20,20,3),np.uint8))
    (tmp_path/'meta.json').write_text(json.dumps({'fps':15,'frame_t':[.01,.13,.32]}))
    with FolderCapture(tmp_path,fps=None) as capture:assert [f.t for f in capture]==[.01,.13,.32]
    with FolderCapture(tmp_path,fps=10) as capture:assert [f.t for f in capture]==[0,.1,.2]
    (tmp_path/'meta.json').write_text(json.dumps({'fps':15,'frame_t':[.1,.1,.2]}))
    with pytest.raises(ValueError,match='increase'):FolderCapture(tmp_path,fps=None)


def dataset(tmp_path,groups=2,n=10,duplicate=False):
    sprite=np.random.default_rng(3).integers(30,240,(32,32,3),dtype=np.uint8)
    for group in range(groups):
        for i in range(n):
            image=np.random.default_rng(i if duplicate else group*100+i).integers(0,25,(128,192,3),dtype=np.uint8)
            image[40:72,40:72]=sprite
            stem=tmp_path/f'{group}-{i}';cv2.imwrite(str(stem.with_suffix('.png')),image)
            stem.with_suffix('.json').write_text(json.dumps({'recording_id':str(group),'reviewed':True,
                                                           'mobs':[{'box':[40,40,32,32],'kind':'slime'}],'items':[]}))


def test_reference_learning_uses_recording_holdout_and_never_activates(tmp_path):
    data=tmp_path/'data';data.mkdir();dataset(data)
    report=learn(data,tmp_path/'bank',Settings())
    assert set(report['train_recordings']).isdisjoint(report['test_recordings'])
    assert report['metrics']['mob']['recall']==1 and not report['activated']
    assert report['meets_reference_gate'] and report['test_frames']==10


def test_reference_learning_refuses_one_recording_and_cross_split_duplicate_pixels(tmp_path):
    data=tmp_path/'data';data.mkdir();dataset(data,groups=1,n=1)
    with pytest.raises(ValueError,match='two independent'):learn(data,tmp_path/'bank',Settings())
    report=learn(data,tmp_path/'smoke',Settings(),in_sample=True)
    assert report['in_sample'] and not report['meets_reference_gate']
    dup=tmp_path/'dup';dup.mkdir();dataset(dup,duplicate=True,n=1)
    with pytest.raises(ValueError,match='Identical frame'):learn(dup,tmp_path/'dup-bank',Settings())


def test_changed_view_pauses_instead_of_reusing_old_hud_coordinates():
    s=Settings();s.calibration.reference_size=[1366,768]
    per=Perceiver(s,[],None)
    try:obs=per.perceive(Frame(np.zeros((600,800,3),np.uint8),0,(0,0,800,600)))
    finally:per.close()
    assert Flag.VIEW_CHANGED in obs.flags
    assert Behavior(s,MapModel.from_settings(s)).decide(obs,0).action==A.STOP_WALK
    assert Supervisor(s).check(obs,0).kind==VerdictKind.PAUSE


def test_route_preview_carries_measured_connections_and_climb_corridor_failure():
    s=ladder_settings();b=Behavior(s,MapModel.from_settings(s))
    d=b.decide(p(x=.3),0)
    assert d.route==('climb',) and d.state=='NAV_APPROACH'
    b.decide(p(x=.5),1)
    assert b.decide(p(x=.5,y=.8),1.2).state=='NAV_FAILED'


def test_verified_movement_limits_reject_long_jumps_but_candidates_do_not():
    s=ladder_settings()
    s.calibration.connections=[{'name':'jump','kind':'jump','source':'low','target':'high',
                               'launch_x':.5,'land_x':.6}]
    s.calibration.movement_profile={'verified':False,'jump_rise':.1,'jump_horizontal':.05}
    MapModel.from_settings(s)
    s.calibration.movement_profile['verified']=True
    with pytest.raises(ValueError,match='calibration'):MapModel.from_settings(s)


def test_key_recording_limits_new_presses_to_configured_controls_and_game_focus(tmp_path):
    import io
    from tools.record import KeyLogger
    active=[True]
    logger=KeyLogger(tmp_path/'keys.jsonl',0,allowed={'left'},is_active=lambda:active[0])
    logger._fh=io.StringIO()
    logger._event('secret',True);logger._event('left',True)
    active[0]=False;logger._event('left',False);logger._event('left',True)
    rows=[json.loads(row) for row in logger._fh.getvalue().splitlines()]
    assert [(row['key'],row['down']) for row in rows]==[('left',True),('left',False)]


def test_hud_unknown_price_and_ocr_parameters_fail_cleanly_in_settings():
    s=Settings();s.economy.hp_replacement_cost=-1;s.ocr.confirm_reads=1
    errors=s.mechanics_problems()
    assert any('replacement_cost' in error for error in errors)
    assert any('confirm_reads' in error for error in errors)
    s=Settings();s.ocr.enabled=True
    assert any('HUD region' in error for error in s.mechanics_problems())


def test_movement_deadman_releases_keys_if_main_loop_stalls():
    backend=FakeBackend()
    with Controller(Settings(),backend,sleep=lambda _:None,movement_timeout_s=.04) as c:
        c.execute(A.WALK_RIGHT,0)
        deadline=time.monotonic()+1
        while time.monotonic()<deadline and not c.inhibited:time.sleep(.01)
        assert c.inhibited and not backend.held and 'lease expired' in c.inhibit_reason
        before=len(backend.events);c.execute(A.CLIMB_UP,1)
        assert len(backend.events)==before
    assert c._watchdog_stop.is_set() and not c._watchdog.is_alive()
    before=len(backend.events);c.allow();c.execute(A.WALK_LEFT,2)
    assert len(backend.events)==before and c.inhibited


def test_stock_guard_and_view_guard_configuration_roundtrip():
    s=Settings();s.calibration.reference_size=[1366,768]
    s.calibration.hp_stock_box=[940,683,13,12];s.calibration.mesos_box=[10,10,100,20]
    s.calibration.movement_profile={'verified':False,'jump_samples':3}
    from maplebot.app.logic import build_settings,iter_fields,format_value,get_value
    raw={sp.key:format_value(sp.kind,get_value(s,sp)) for sp in iter_fields()}
    actual,errors=build_settings(s,raw)
    assert not errors and actual.to_dict()==s.to_dict()


def test_latched_retreat_continues_if_stock_ocr_disappears_but_navigation_remains_known():
    s=Settings();s.loot.enabled=False;s.retreat.mp_start=10
    s.calibration.patrol_y=.7;s.calibration.safe_spot=[.4,.7]
    b=Behavior(s,MapModel.from_settings(s));sup=Supervisor(s)
    obs=p(flags=frozenset({Flag.STOCK_UNREADABLE}))
    assert sup.check(obs,0).kind==VerdictKind.OK
    d=b.decide(obs,0);assert d.state=='RETREAT_WALK'
    sup.note_decision(d,0)
    assert sup.check(obs,6).kind==VerdictKind.OK
    assert b.decide(obs,6).state=='RETREAT_WALK'
    assert b.decide(obs,46).state=='RETREAT_FAILED'
