"""Register a cropped/scrolling minimap against a saved map image.

Independent implementation: multiple masked texture patches must agree on one
translation. A dot, a repeated tile, or the last position alone is insufficient.
"""
from __future__ import annotations

from dataclasses import dataclass
import json

import cv2
import numpy as np

from maplebot.settings import REPO_ROOT
from .calib import as_bgr, box_inside


@dataclass(frozen=True)
class MapView:
    offset_x: float
    offset_y: float
    basis_x: float
    basis_y: float
    inliers: int
    score: float

    def to_map(self, x, y):
        return ((x-self.offset_x)/self.basis_x, (y-self.offset_y)/self.basis_y)

    def to_pixel(self, point):
        return (self.offset_x+point[0]*self.basis_x, self.offset_y+point[1]*self.basis_y)

    def values(self):
        return (self.offset_x,self.offset_y,self.basis_x,self.basis_y)


def map_to_image(box, point, view=None):
    x,y,w,h=box
    dx,dy,bx,by=view if view is not None else (0,0,w,h)
    return (int(round(x+dx+point[0]*bx)),int(round(y+dy+point[1]*by)))


class MapReference:
    def __init__(self, image, metadata=None, options=None):
        raw=image
        image=as_bgr(image)
        if image is None:
            raise ValueError('Map reference image is unreadable')
        if options is None:
            options=json.loads((REPO_ROOT/'configs/map-tracking.yaml').read_text())
        self.options=options
        self.metadata=metadata or {}
        mini=self.metadata.get('minimap',{})
        if mini:
            import math
            values=[mini.get(k) for k in ('width','height','world_scale')]
            if any(not isinstance(v,(float,int)) or isinstance(v,bool) or not math.isfinite(v) or v<=0 for v in values):
                raise ValueError('Invalid map reference scale')
        self.image=image
        self.alpha=(raw[:,:,3] if raw.ndim==3 and raw.shape[2]==4 else np.full(image.shape[:2],255,np.uint8))
        self.previous=None
        self._sets={}

    @classmethod
    def from_descriptor(cls, descriptor):
        image=cv2.imdecode(np.fromfile(str(descriptor['path']),dtype=np.uint8),cv2.IMREAD_UNCHANGED)
        return cls(image,descriptor)

    def _patches(self, scale):
        if scale in self._sets:
            return self._sets[scale]
        h,w=self.image.shape[:2]
        resized=cv2.resize(self.image,(round(w*scale),round(h*scale)),interpolation=cv2.INTER_NEAREST)
        alpha=cv2.resize(self.alpha,(resized.shape[1],resized.shape[0]),interpolation=cv2.INTER_NEAREST)
        size=self.options['patch_size'];stride=self.options['patch_stride']
        patches=[]
        for y in range(0,resized.shape[0]-size+1,stride):
            for x in range(0,resized.shape[1]-size+1,stride):
                patch=resized[y:y+size,x:x+size]
                mask=np.uint8(alpha[y:y+size,x:x+size]>=180)*255
                if np.count_nonzero(mask)<size*size*.2:
                    continue
                spread=float(patch[mask>0].std())
                if spread<self.options['texture_std']:
                    continue
                patches.append((x,y,patch.copy(),mask.copy(),spread))
        # Evenly distributed patches preserve landmarks across the whole source.
        limit=self.options['max_patches']
        if len(patches)>limit:
            indices=np.linspace(0,len(patches)-1,limit,dtype=int)
            patches=[patches[i] for i in indices]
        result=(patches,resized.shape[1]/w,resized.shape[0]/h)
        self._sets[scale]=result
        return result

    def _votes(self, roi, scale, previous=None):
        patches,sx,sy=self._patches(scale)
        size=self.options['patch_size'];votes=[]
        for x,y,patch,mask,_ in patches:
            rx,ry,rw,rh=0,0,roi.shape[1],roi.shape[0]
            if previous is not None:
                slack=self.options['tracking_slack_px']
                px,py=x+previous.offset_x,y+previous.offset_y
                if not (0<=px and 0<=py and px+size<=rw and py+size<=rh):
                    continue
                rx,ry=max(0,int(px-slack)),max(0,int(py-slack))
                rw,rh=min(rw,int(px+size+slack))-rx,min(rh,int(py+size+slack))-ry
            if rw<size or rh<size:
                continue
            scores=cv2.matchTemplate(roi[ry:ry+rh,rx:rx+rw],patch,cv2.TM_CCOEFF_NORMED,mask=mask)
            np.nan_to_num(scores,copy=False,nan=-1,posinf=-1,neginf=-1)
            _,score,_,(px,py)=cv2.minMaxLoc(scores)
            if score>=self.options['min_patch_score']:
                votes.append((float(px+rx-x),float(py+ry-y),x,y,float(score)))
        if not votes:
            return None
        tolerance=self.options['translation_tolerance_px']
        clusters=[]
        for vote in votes:
            cluster=[v for v in votes if abs(v[0]-vote[0])<=tolerance and abs(v[1]-vote[1])<=tolerance]
            clusters.append(cluster)
        clusters.sort(key=len,reverse=True)
        best=clusters[0]
        if len(best)<self.options['min_inliers']:
            return None
        if (max(v[2] for v in best)-min(v[2] for v in best)<self.options['min_landmark_span_px'] or
            max(v[3] for v in best)-min(v[3] for v in best)<self.options['min_landmark_span_px']):
            return None
        dx,dy=float(np.median([v[0] for v in best])),float(np.median([v[1] for v in best]))
        alternative=max((len(c) for c in clusters if abs(c[0][0]-dx)>tolerance*2 or
                         abs(c[0][1]-dy)>tolerance*2),default=0)
        if alternative and len(best)<alternative*self.options['ambiguity_ratio']:
            return None
        mini=self.metadata.get('minimap',{})
        world_scale=mini.get('world_scale')
        if world_scale:
            bx=float(mini['width'])/world_scale*sx
            by=float(mini['height'])/world_scale*sy
        else:
            bx=self.image.shape[1]*sx;by=self.image.shape[0]*sy
        return MapView(dx,dy,bx,by,len(best),float(np.mean([v[4] for v in best])))

    def align(self, image, box=None):
        image=as_bgr(image)
        if image is None:
            return None
        if box is not None:
            checked=box_inside(image,box)
            if checked is None:
                return None
            x,y,w,h=checked;image=image[y:y+h,x:x+w]
        if self.previous is not None:
            view,scale=self.previous
            found=self._votes(image,scale,view)
            if found is not None:
                self.previous=(found,scale)
                return found
        for scale in self.options['scales']:
            found=self._votes(image,scale)
            if found is not None:
                self.previous=(found,scale)
                return found
        self.previous=None
        return None
