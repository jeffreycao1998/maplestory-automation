"""Drawn patrols use screenshot coordinates and never invent cross-platform travel."""
from copy import deepcopy

import pytest

from maplebot.app import logic as L
from maplebot.mapmodel import MapModel
from maplebot.settings import Settings


def context():
    s=Settings();s.calibration.minimap_box=[10,20,100,100]
    return L.WizardContext(settings=s)


def pixel(x,y):
    return (10+x*100-.5,20+y*100-.5)


@pytest.mark.parametrize('reverse',[False,True])
def test_drag_limits_in_either_direction_includes_current_bridge(reverse):
    ctx=context();ends=[pixel(.2,.4),pixel(.8,.41)]
    if reverse:ends.reverse()
    L.select_patrol_segment(ctx,*ends,(.5,.45))
    c=ctx.settings.calibration
    assert (c.patrol_left,c.patrol_right,c.patrol_y)==pytest.approx((.2,.8,.405))
    assert (ctx.patrol_left_set,ctx.patrol_right_set)==pytest.approx((.2,.8))
    assert L.step_problem('patrol',ctx) is None
    assert MapModel.from_settings(ctx.settings).on_platform(.5,.45)


@pytest.mark.parametrize('start,end,pos',[
    ((0,0),pixel(.8,.4),(.5,.4)),
    (pixel(.2,.4),(110,50),(.5,.4)),
    (pixel(.2,.4),pixel(.2,.4),(.2,.4)),
    (pixel(.2,.4),pixel(.21,.4),(.205,.4)),
    (pixel(.2,.2),pixel(.8,.6),(.5,.4)),
    (pixel(.2,.4),pixel(.8,.4),None),
    (pixel(.2,.4),pixel(.8,.4),(.5,.7)),
    (pixel(.2,.4),pixel(.8,.4),(.9,.4)),
    ((float('nan'),30),pixel(.8,.4),(.5,.4)),
    (pixel(.2,.4),pixel(.8,.4),(float('nan'),.4)),
])
def test_invalid_draw_keeps_existing_limits(start,end,pos):
    ctx=context();before=deepcopy(ctx)
    with pytest.raises(ValueError):L.select_patrol_segment(ctx,start,end,pos)
    assert ctx==before


def test_known_ramp_allows_different_end_heights_without_creating_geometry():
    ctx=context()
    ctx.settings.calibration.platforms=[{'name':'ramp','points':[[.1,.7],[.9,.3]],'tolerance':.015}]
    geometry=deepcopy(ctx.settings.calibration.platforms)
    assert L.select_patrol_segment(ctx,pixel(.2,.65),pixel(.8,.35),None)=='ramp'
    assert ctx.settings.calibration.platforms==geometry
    assert ctx.settings.calibration.connections==[]


def test_known_platforms_cannot_be_joined_by_drawing_a_line():
    ctx=context()
    ctx.settings.calibration.platforms=[
        {'name':'top','points':[[.1,.3],[.9,.3]],'tolerance':.015},
        {'name':'bottom','points':[[.1,.8],[.9,.8]],'tolerance':.015}]
    before=deepcopy(ctx)
    with pytest.raises(ValueError,match='same known platform'):
        L.select_patrol_segment(ctx,pixel(.2,.3),pixel(.8,.8),None)
    assert ctx==before


def test_active_multi_platform_route_is_not_silently_changed():
    ctx=context();ctx.settings.calibration.route=['ground','top']
    before=deepcopy(ctx)
    with pytest.raises(ValueError,match='multi-platform route'):
        L.select_patrol_segment(ctx,pixel(.2,.4),pixel(.8,.4),(.5,.4))
    assert ctx==before


def test_existing_safe_spot_is_preserved_and_can_be_revalidated():
    ctx=context();ctx.settings.calibration.safe_spot=[.15,.4]
    L.select_patrol_segment(ctx,pixel(.2,.4),pixel(.8,.4),(.5,.4))
    assert ctx.settings.calibration.safe_spot==[.15,.4]
    with pytest.raises(ValueError,match='edge margin'):
        MapModel.from_settings(ctx.settings).safe_platform(ctx.settings.calibration.safe_spot)
