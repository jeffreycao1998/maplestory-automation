"""Conservative spell eligibility using current detections, not an engine emulator."""
from __future__ import annotations

import json

from maplebot.settings import REPO_ROOT


class SkillTargeting:
    def __init__(self,settings,model):
        self.settings,self.model=settings,model
        self.profile=None
        if settings.skill.targeting_profile=='magic_claw':
            self.profile=json.loads((REPO_ROOT/'configs/skills/magic-claw.yaml').read_text(encoding='utf-8'))

    @property
    def range_px(self):
        return min(self.settings.skill.range_px,self.profile['range_px']) if self.profile else self.settings.skill.range_px

    def compatible(self,p,m):
        if p.player_screen is None or p.player_screen_estimated:return False
        px,py=p.player_screen
        if abs(m.y-py)>self.settings.skill.vertical_tolerance_px:return False
        if not self.profile:return True
        if p.player_map is None or not self.model.on_platform(*p.player_map):return False
        foot_y=py+self.profile['player_feet_offset_px']
        expected=foot_y
        reference=self.settings.calibration.map_reference
        if self.model.explicit and reference:
            mini=reference.get('minimap',{})
            width,height=mini.get('width'),mini.get('height')
            if not width or not height:return False
            here=[pl for pl in self.model.platforms if pl.contains(*p.player_map)]
            if len(here)!=1:return False
            surface=here[0]
            units=self.profile['world_units_per_screen_px']
            mx=p.player_map[0]+(m.x-px)*units/width
            if not surface.contains_x(mx):return False
            origin_y=surface.height_at(p.player_map[0])
            expected+=(surface.height_at(mx)-origin_y)*height/units
            # A lower platform can be close enough to fit the tolerance band. If
            # another known surface better explains the mob's feet, do not cast.
            own_error=abs(m.y+m.h/2-expected)
            for other in self.model.platforms:
                if other.name==surface.name or not other.contains_x(mx):continue
                other_y=foot_y+(other.height_at(mx)-origin_y)*height/units
                if abs(m.y+m.h/2-other_y)<=own_error:return False
        # Bottom of the currently observed sprite is a conservative foot estimate.
        # No vertical raycast or head/sprite overlap makes a lower floor hittable.
        return abs(m.y+m.h/2-expected)<=self.profile['feet_tolerance_px']

    def rejection_note(self,p):
        if self.profile and p.mobs and not any(self.compatible(p,m) for m in p.mobs):
            return 'no Magic Claw targets on compatible platform/height'
        return ''
