"""Map registration must survive scrolling and abstain on wrong/stale images."""
import cv2
import numpy as np
import pytest

from maplebot.perception.map_reference import MapReference, map_to_image
from maplebot.perception import Perceiver
from maplebot.settings import Settings
from maplebot.types import Frame,Flag


def reference(seed=42):
    # Reproducible landmarks with enough space for independently agreeing patches.
    return np.random.default_rng(seed).integers(0,255,(180,210,3),dtype=np.uint8)


def test_cropped_scroll_keeps_same_world_point():
    image=reference();tracker=MapReference(image)
    a=tracker.align(image[20:150,30:190])
    assert a is not None and a.inliers>=4
    assert a.values()==pytest.approx((-30,-20,210,180))
    b=tracker.align(image[28:158,37:197])
    assert b is not None
    assert a.to_map(60,60)==pytest.approx(b.to_map(53,52))
    assert map_to_image((10,20,160,130),a.to_map(60,60),a.values())==(70,80)


def test_bad_frame_cannot_reuse_previous_registration():
    tracker=MapReference(reference())
    assert tracker.align(reference()[20:150,30:190]) is not None
    assert tracker.align(reference(91)[20:150,30:190]) is None
    assert tracker.previous is None
    assert tracker.align(np.zeros((130,160,3),np.uint8)) is None


def test_repeated_texture_is_not_a_unique_alignment():
    tile=reference()[:40,:40]
    image=np.tile(tile,(5,6,1))
    # Identical landmarks in separated repeated areas produce competing translations.
    assert MapReference(image).align(image[:130,:160]) is None


def test_overlay_uses_source_world_scale_not_rounded_png_size():
    image=reference()
    metadata={'minimap':{'width':3363,'height':2885,'world_scale':16}}
    view=MapReference(image,metadata).align(image[20:150,30:190])
    assert view.basis_x==pytest.approx(3363/16)
    assert view.basis_y==pytest.approx(2885/16)


def test_perceiver_without_configured_reference_cannot_return_viewport_position(scene_settings,synthetic_scene):
    scene_settings.calibration.map_reference={'map_id':'example'}
    per=Perceiver(scene_settings,[],None)
    p=per.perceive(synthetic_scene.frame)
    assert p.player_map is None and Flag.MINIMAP_MISSING in p.flags
    assert p.minimap_view is None
    per.close()


def test_perceiver_maps_dot_with_fresh_registration(tmp_path):
    source=reference();path=tmp_path/'map.png';cv2.imwrite(str(path),source)
    desc={'path':str(path),'map_id':'example'}
    s=Settings();s.calibration.map_reference={'map_id':'example'}
    s.calibration.minimap_box=[10,10,160,130]
    img=np.zeros((180,220,3),np.uint8);img[10:140,10:170]=source[20:150,30:190]
    cv2.circle(img,(70,70),2,(0,255,255),-1)
    per=Perceiver(s,[],None,map_reference=desc)
    p=per.perceive(Frame(img,0,(0,0,220,180)))
    assert p.player_map==pytest.approx((90.5/210,80.5/180),abs=.003)
    assert p.minimap_view is not None
    changed=Settings.from_dict(s.to_dict())
    changed.calibration.map_reference={'map_id':'another map'}
    per.update_settings(changed)
    assert per.perceive(Frame(img,.5,(0,0,220,180))).player_map is None
    per.update_settings(s)
    img[10:140,10:170]=0;cv2.circle(img,(70,70),2,(0,255,255),-1)
    p=per.perceive(Frame(img,1,(0,0,220,180)))
    assert p.player_map is None and p.minimap_view is None
    per.close()
