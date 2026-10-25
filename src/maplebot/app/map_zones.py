"""Preview current-client map references and choose a reachable spawn platform.

No input is sent here. Changes remain in the wizard until Save. Geometry is a
reference; movement capabilities come only from reviewed manual recordings.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk

import cv2
import numpy as np

from maplebot.settings import Settings
from maplebot.mapmodel import MapModel
from maplebot.mapmodel.source import candidate_connections
from maplebot.perception.map_reference import MapReference
from .setup_detection import registered_player
from .widgets import ImageCanvas, CYAN, GREEN, ORANGE


def reference_descriptor(profile, manifest):
    rows=manifest.get('reference_files',[])
    if len(rows)!=1 or rows[0].get('file')!='reference_minimap.png':
        raise ValueError('Download this map’s sprites again to include its map image and platforms.')
    # Resolve only a published bank; do not accept paths from the downloaded JSON.
    import re
    bank=manifest.get('bank','')
    if not re.fullmatch(r'[0-9a-f]{32}',bank):raise ValueError('Invalid map reference bank.')
    path=profile.templates_dir/'.sprite-banks'/bank/'reference_minimap.png'
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=rows[0].get('sha256'):
        raise ValueError('Map reference is missing or changed. Download the map again.')
    return {'map_id':manifest['map_id'],'source_client_build':manifest['source_client_build'],
            'source_snapshot':manifest.get('source_snapshot'),'bank':bank,'sha256':rows[0]['sha256'],
            'minimap':manifest['map_reference']['minimap'],'path':str(path)}


def make_plan(settings, descriptor, geometry, image):
    from .logic import WizardContext
    trial=Settings.from_dict(settings.to_dict());c=trial.calibration
    portable={k:v for k,v in descriptor.items() if k!='path'}
    same_basis=(c.map_reference and all(c.map_reference.get(k)==portable.get(k)
                 for k in ('map_id','source_client_build','sha256','minimap')))
    # Viewport measurements are not full-map measurements. Never rescale by guessing.
    motion=copy.deepcopy(c.movement_profile) if same_basis else {}
    c.map_reference=portable
    c.platforms=copy.deepcopy(geometry['platforms']);c.ladders=copy.deepcopy(geometry['ladders'])
    c.connections=[];c.route=[];c.safe_spot=None;c.movement_profile=motion
    c.patrol_left,c.patrol_right,c.patrol_y=0.,1.,None
    ctx=WizardContext(trial)
    tracker=MapReference.from_descriptor(descriptor)
    pos=registered_player(ctx,image,tracker)
    if pos is None:raise ValueError('Cannot align your own dot to this map. Refresh the screenshot and check the selected map.')
    model=MapModel.from_settings(trial)
    here=model.safe_platform(list(pos),trial.behavior.edge_margin)
    set_target(trial,here.name)
    rebuild_connections(trial,geometry)
    return trial,tracker,pos,ctx.minimap_view


def rebuild_connections(settings, geometry):
    c=settings.calibration
    old=c.connections
    accepted=[]
    try:
        for edge in candidate_connections(geometry,c.movement_profile,settings.behavior.edge_margin):
            c.connections=accepted+[edge]
            try:MapModel.from_settings(settings)
            except ValueError:continue  # source endpoints outside the controller's supported limits
            accepted.append(edge)
    except Exception:
        c.connections=old
        raise
    c.connections=accepted


def set_target(settings, name, position=None):
    model=MapModel.from_settings(settings)
    dest=model.by_name(name)
    if dest.right-dest.left<.05:
        raise ValueError('This platform is too narrow for the current training controller.')
    if position is not None:
        here=model.safe_platform(list(position),settings.behavior.edge_margin)
        if here.name!=name and not model.path(here.name,name):
            raise ValueError('No measured route reaches this platform. Import reviewed movement measurements or add verified links first.')
    c=settings.calibration
    c.route=[name];c.patrol_left=dest.left;c.patrol_right=dest.right
    c.patrol_y=dest.height_at((dest.left+dest.right)/2)


class MapZones(tk.Toplevel):
    def __init__(self, master, profile, ctx, screenshot, on_applied):
        manifest=profile.sprite_library_manifest()
        if not manifest:raise ValueError('Download sprites for the current map first.')
        geometry=manifest.get('map_reference',{}).get('geometry')
        if not geometry:raise ValueError('Download this map’s sprites again to include platforms and spawn zones.')
        descriptor=reference_descriptor(profile,manifest)
        plan,tracker,pos,view=make_plan(ctx.settings,descriptor,geometry,screenshot)
        image=cv2.imdecode(np.fromfile(descriptor['path'],dtype=np.uint8),cv2.IMREAD_COLOR)
        super().__init__(master)
        self.title('Map and spawn zones — '+manifest.get('map_name',''))
        self.plan,self.geometry,self.tracker,self.pos,self.view=plan,geometry,tracker,pos,view
        self.ctx,self.on_applied=ctx,on_applied
        self.descriptor=descriptor
        self.image=image
        self.mob_names=manifest.get("map_reference",{}).get("mob_stats",{})
        self.bx=float(descriptor['minimap']['width'])/descriptor['minimap']['world_scale']
        self.by=float(descriptor['minimap']['height'])/descriptor['minimap']['world_scale']
        body=ttk.Frame(self,padding=12);body.pack(fill='both',expand=True)
        ttk.Label(body,text='Click a spawn marker or platform to choose a training destination.\n'
                  'Orange = spawn references; green = your measured position.\n'
                  'Only reachable destinations can be applied. These are client references, not live monster sightings.',
                  wraplength=650,justify='left').pack(anchor='w')
        self.canvas=ImageCanvas(body,650,420);self.canvas.pack(pady=8)
        self.canvas.mode='click';self.canvas.on_click=self.choose
        self.status=ttk.Label(body,wraplength=650,justify='left');self.status.pack(anchor='w')
        self.reviewed=tk.BooleanVar(value=False)
        ttk.Checkbutton(body,text='I reviewed the movement report against my manual recording.',variable=self.reviewed).pack(anchor='w')
        ttk.Button(body,text='Import movement report…',command=self.import_motion).pack(anchor='w',pady=5)
        ttk.Label(body,text='Applying replaces old viewport routes, training limits and safe spot.\n'
                  'It keeps movement measurements only when their map coordinate basis matches. Save the wizard to keep changes.',
                  wraplength=650,foreground='#805000').pack(anchor='w',pady=8)
        row=ttk.Frame(body);row.pack(fill='x')
        ttk.Button(row,text='Use selected platform',command=self.apply).pack(side='right')
        ttk.Button(row,text='Cancel',command=self.destroy).pack(side='right',padx=8)
        self.draw()

    def draw(self):
        c=self.plan.calibration
        def px(p):return (p[0]*self.bx,p[1]*self.by)
        overlays=[]
        for pl in c.platforms:
            overlays.append(('polyline',[px(v) for v in pl['points']],GREEN if pl['name'] in c.route else CYAN))
        for zone in self.geometry['spawn_zones']:
            overlays.extend(('dot',px(p),ORANGE) for p in zone['spawns'])
        overlays.append(('dot',px(self.pos),GREEN))
        self.canvas.overlays=overlays;self.canvas.show(self.image,zoom=True)
        mobs={}
        for zone in self.geometry['spawn_zones']:
            if zone['platform']==c.route[0]:mobs.update(zone['mobs'])
        names=', '.join(f"{self.mob_names.get(mid,{}).get('name',mid)} ×{count}" for mid,count in mobs.items())
        self.status.config(text=f"Selected {c.route[0]}; {len(c.connections)} supported links. {names} "
                           'Test alignment and navigation in Observe before live use.',foreground='#205020')

    def choose(self,x,y):
        model=MapModel.from_settings(self.plan)
        point=(x/self.bx,y/self.by)
        spawns=[(np.hypot((p[0]-point[0])*self.bx,(p[1]-point[1])*self.by),zone['platform'])
                for zone in self.geometry['spawn_zones'] for p in zone['spawns']]
        nearby=min(spawns,default=(float('inf'),None))
        candidates=[model.by_name(nearby[1])] if nearby[0]<=5 else [pl for pl in model.platforms if pl.contains_x(point[0])]
        if not candidates:return
        dest=min(candidates,key=lambda pl:abs(pl.height_at(point[0])-point[1]))
        if abs(dest.height_at(point[0])-point[1])*self.by>5:return
        try:set_target(self.plan,dest.name,self.pos)
        except ValueError as exc:self.status.config(text=str(exc),foreground='#b00000');return
        self.draw()

    def import_motion(self):
        if not self.reviewed.get():
            self.status.config(text='Review the manual recording and report, then check the box before importing.',foreground='#b00000');return
        path=filedialog.askopenfilename(parent=self,title='Movement calibration report',filetypes=[('JSON report','*.json')])
        if not path:return
        old=copy.deepcopy(self.plan.calibration.movement_profile)
        try:
            report=json.loads(Path(path).read_text(encoding='utf-8'))
            ref=report.get('map_reference') or {}
            if any(ref.get(k)!=self.descriptor.get(k) for k in ('map_id','source_client_build','sha256','minimap')):
                raise ValueError('Movement report uses another map/build or viewport coordinates. Record it after aligning this map.')
            candidate=dict(report['candidate'])
            if report.get('warnings'):raise ValueError('Movement report has unresolved warnings. Record complete walks, directional jumps and climbs first.')
            candidate['verified']=True
            self.plan.calibration.movement_profile=candidate
            rebuild_connections(self.plan,self.geometry)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            self.plan.calibration.movement_profile=old
            self.status.config(text=str(exc),foreground='#b00000');return
        self.draw()

    def apply(self):
        try:set_target(self.plan,self.plan.calibration.route[0],self.pos)
        except ValueError as exc:
            self.status.config(text=str(exc),foreground='#b00000');return
        self.ctx.settings.calibration=copy.deepcopy(self.plan.calibration)
        c=self.ctx.settings.calibration
        self.ctx.patrol_left_set,self.ctx.patrol_right_set=c.patrol_left,c.patrol_right
        self.ctx.patrol_left_y=self.ctx.patrol_right_y=c.patrol_y
        self.ctx.patrol_preset=True;self.ctx.dot_pos=self.pos;self.ctx.minimap_view=self.view
        self.on_applied(self.tracker)
        self.destroy()
