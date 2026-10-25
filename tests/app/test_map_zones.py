import copy
import hashlib
import json

import cv2
import numpy as np
import pytest

from maplebot.app.map_zones import make_plan,set_target,reference_descriptor
from maplebot.app.logic import WizardContext,select_patrol_segment,pick_safe_spot
from maplebot.settings import Settings,Profile
from maplebot.mapmodel import MapModel,Connection
from maplebot.mapmodel.source import source_geometry,candidate_connections


def test_source_chains_collision_surfaces_and_maps_spawns_by_foothold():
    raw={'0':{'1':{'1':{'x1':0,'x2':40,'y1':50,'y2':55,'next':2},
                        '2':{'x1':40,'x2':90,'y1':55,'y2':50,'prev':1},
                        '3':{'x1':40,'x2':40,'y1':0,'y2':50},
                        '4':{'x1':90,'x2':0,'y1':70,'y2':70}}}}
    g=source_geometry(raw,{'1':{'x':50,'y1':10,'y2':50}},
                      {'width':100,'height':100,'center_x':0,'center_y':0},
                      [{'id':'7','fh':2,'x':60,'y':30,'cy':54}])
    assert len(g['platforms'])==1 and len(g['platforms'][0]['points'])==3
    assert g['foothold_count']==4
    assert g['spawn_zones'][0]['spawns']==[[.6,.54]]
    assert g['spawn_zones'][0]['mobs']=={'7':1}
    assert g['platforms'][0]['drop_through'] is False
    assert not candidate_connections(g,{})


def test_measured_jump_candidates_do_not_invent_class_capabilities():
    g={'platforms':[{'name':'low','points':[[.1,.7],[.8,.7]],'tolerance':.01},
                    {'name':'high','points':[[.2,.6],[.7,.6]],'tolerance':.01}], 'ladders':[]}
    assert not candidate_connections(g,{'verified':False,'jump_rise':.3,'jump_horizontal':.3})
    assert not candidate_connections(g,{'verified':True,'jump_rise':.05,'jump_horizontal':.3})
    edges=candidate_connections(g,{'verified':True,'jump_rise':.15,'jump_horizontal':.3})
    assert len(edges)==1 and edges[0]['source']=='low' and edges[0]['kind']=='jump'


def plan_fixture(tmp_path):
    gray=np.random.default_rng(42).integers(0,255,(180,210),dtype=np.uint8)
    source=cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)
    path=tmp_path/'map.png';cv2.imwrite(str(path),source)
    desc={'path':str(path),'bank':'a'*32,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'map_id':'10002061','source_client_build':'1.0.0','minimap':{'width':3360,'height':2880,'world_scale':16}}
    image=np.zeros((180,220,3),np.uint8);image[10:140,10:170]=source[20:150,30:190]
    cv2.circle(image,(70,70),2,(0,255,255),-1)
    s=Settings();s.calibration.minimap_box=[10,10,160,130]
    s.calibration.movement_profile={'verified':True,'jump_rise':.15,'jump_horizontal':.3}
    s.calibration.safe_spot=[.5,.5]
    g={'platforms':[{'name':'low','points':[[.1,80.5/180],[.9,80.5/180]],'tolerance':.02},
                    {'name':'high','points':[[.2,.2],[.8,.2]],'tolerance':.01}],
       'ladders':[],'spawn_zones':[]}
    return s,desc,g,image


def test_map_switch_resets_old_viewport_calibration_and_does_not_change_live_settings(tmp_path):
    s,desc,g,image=plan_fixture(tmp_path);before=s.to_dict()
    plan,tracker,pos,view=make_plan(s,desc,g,image)
    assert s.to_dict()==before
    assert plan.calibration.movement_profile=={}
    assert plan.calibration.safe_spot is None
    assert 'path' not in plan.calibration.map_reference
    assert plan.calibration.route==['low']
    assert pos==pytest.approx((90.5/210,80.5/180),abs=.003)
    assert view==pytest.approx((-30,-20,210,180))
    with pytest.raises(ValueError,match='No measured route'):set_target(plan,'high',pos)
    assert plan.calibration.route==['low']


def test_missing_alignment_cannot_change_route_or_use_viewport_clicks(tmp_path):
    s,desc,g,image=plan_fixture(tmp_path)
    with pytest.raises(ValueError,match='Cannot align'):make_plan(s,desc,g,np.zeros_like(image))
    s.calibration.map_reference={'map_id':'example'}
    with pytest.raises(ValueError,match='alignment'):pick_safe_spot(s,(70,70))
    before=s.to_dict()
    with pytest.raises(ValueError,match='alignment'):
        select_patrol_segment(WizardContext(s),(30,70),(140,70),(.5,.5))
    assert s.to_dict()==before


def test_full_map_limits_use_visible_crop_transform(tmp_path):
    s,desc,g,image=plan_fixture(tmp_path)
    plan,_,pos,view=make_plan(s,desc,g,image)
    plan.calibration.route=[]
    ctx=WizardContext(plan,minimap_view=view)
    select_patrol_segment(ctx,(25,70),(145,70),pos)
    assert plan.calibration.patrol_left==pytest.approx(45.5/210)
    assert plan.calibration.patrol_right==pytest.approx(165.5/210)
    assert plan.calibration.route==['low']
    assert pick_safe_spot(plan,(70,70),view)==pytest.approx(pos,abs=.003)


def test_reference_bank_checksum_and_build_are_verified(tmp_path):
    profile=Profile(tmp_path/'profile');profile.ensure()
    bank='a'*32;folder=profile.templates_dir/'.sprite-banks'/bank;folder.mkdir(parents=True)
    path=folder/'reference_minimap.png';cv2.imwrite(str(path),np.ones((40,40,3),np.uint8))
    digest=hashlib.sha256(path.read_bytes()).hexdigest()
    manifest={'bank':bank,'map_id':'10002061','source_game':'MapleStory Classic World',
              'source_snapshot':'Release','source_client_build':'1.0.0',
              'reference_files':[{'file':'reference_minimap.png','sha256':digest}],
              'map_reference':{'minimap':{'width':640,'height':640,'world_scale':16}}}
    (profile.templates_dir/'sprite-library.json').write_text(json.dumps(manifest))
    descriptor=reference_descriptor(profile,manifest)
    s=Settings();s.calibration.map_reference={k:v for k,v in descriptor.items() if k!='path'}
    assert profile.map_reference_descriptor(s)['path']==str(path)
    path.write_bytes(b'changed')
    with pytest.raises(ValueError,match='missing or changed'):profile.map_reference_descriptor(s)
    s.calibration.map_reference['source_client_build']='wrong'
    with pytest.raises(ValueError,match='map/build'):profile.map_reference_descriptor(s)


def test_weighted_path_prefers_smaller_travel_budget_and_handles_cycles():
    model=MapModel.from_settings(Settings())
    model.connections=(Connection('slow','a','c','climb',.5,.5,20),
                       Connection('first','a','b','walk',.5,.6,2),
                       Connection('second','b','c','jump',.5,.5,3),
                       Connection('cycle','b','a','walk',.6,.5,2))
    assert tuple(e.name for e in model.path('a','c'))==('first','second')
    assert not model.path('c','a')


def test_source_safe_spot_requires_a_route_from_training_destination(tmp_path):
    s,desc,g,image=plan_fixture(tmp_path)
    plan,_,_,view=make_plan(s,desc,g,image)
    # Upper platform is visible but has no measured connection.
    with pytest.raises(ValueError,match='No measured route'):
        pick_safe_spot(plan,(85,26),view)


def test_source_limits_can_be_redrawn_after_selecting_one_target(tmp_path):
    s,desc,g,image=plan_fixture(tmp_path)
    plan,_,pos,view=make_plan(s,desc,g,image)
    ctx=WizardContext(plan,minimap_view=view)
    select_patrol_segment(ctx,(25,70),(145,70),pos)
    assert plan.calibration.route==['low']
