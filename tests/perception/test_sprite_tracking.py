import cv2
import numpy as np
from maplebot.perception.sprites import SpriteMatcher


def write(path,seed):
    rng=np.random.default_rng(seed)
    image=rng.integers(0,255,(24,24,4),dtype=np.uint8)
    image[:,:,3]=0;image[2:22,2:22,3]=255
    cv2.imwrite(str(path),image)
    return image[:,:,:3],image[:,:,3]


def paste(frame,sprite,mask,x,y):
    roi=frame[y:y+24,x:x+24];roi[mask>0]=sprite[mask>0]


def test_animated_species_is_freshly_verified_outside_global_pose_budget(tmp_path):
    matcher=SpriteMatcher()
    a,ma=write(tmp_path/'mob_library_7_move_0.png',21)
    b,mb=write(tmp_path/'mob_library_7_move_1.png',39)
    write(tmp_path/'mob_library_6_move_0.png',50)
    for path in sorted(tmp_path.glob('*.png'),reverse=True):matcher.add(path)
    # Explicit order: first pose searched, next tick a different species searched.
    matcher.templates.sort(key=lambda t:t[0]);matcher.templates=matcher.templates[1:]+matcher.templates[:1]
    image=np.zeros((120,160,3),np.uint8);paste(image,a,ma,55,45)
    assert matcher.find(image,.9,mirror=False,budget=1)
    matcher._cursor=2
    animated=np.zeros_like(image);paste(animated,b,mb,59,46)
    found=matcher.find(animated,.9,mirror=False,budget=1)
    assert found and found[0].template=='mob_library_7_move_1.png'
    assert abs(found[0].x-71)<=1
    assert not matcher.find(np.zeros_like(image),.9,mirror=False,budget=1)
    assert matcher._tracks==[]


def test_tracked_pixels_in_ui_exclusion_are_not_reported(tmp_path):
    matcher=SpriteMatcher();p=tmp_path/'mob_library_7_move_0.png';a,mask=write(p,21);matcher.add(p)
    image=np.zeros((120,160,3),np.uint8);paste(image,a,mask,55,45)
    assert matcher.find(image,.9,mirror=False,budget=1)
    assert not matcher.find(image,.9,exclude=[(45,35,50,50)],mirror=False,budget=1)
