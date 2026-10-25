"""Review recorded frames and save real mob/drop labels; never send game input.

python -m tools.label_recording data/recordings/manual --output data/labeled/manual --profile profile --every 15
Predictions are proposals only. Save reviewed frame explicitly after checking misses.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil

import cv2
import tools
from maplebot.settings import Profile
from maplebot.perception import Perceiver
from maplebot.types import Frame


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',type=Path,default=Path('profile'));parser.add_argument('--every',type=int,default=15)
    args=parser.parse_args(argv)
    if args.every<1:parser.error('--every must be positive')
    folder=args.recording/'frames' if (args.recording/'frames').exists() else args.recording
    files=sorted(folder.glob('*.png'))[::args.every]
    if not files:parser.error('No recorded PNG frames')
    group=hashlib.sha256((args.recording/'meta.json').read_bytes() if (args.recording/'meta.json').exists() else files[0].read_bytes()).hexdigest()[:20]
    profile=Profile(args.profile);settings=profile.load()
    # OCR is unrelated to label proposals and must not spawn background readers here.
    settings.ocr.enabled=False
    per=Perceiver(settings,profile.mob_templates(),profile.player_template(),profile.item_templates())
    import tkinter as tk
    from tkinter import ttk
    from maplebot.app.widgets import ImageCanvas
    root=tk.Tk();root.title('Review recording — no game input')
    kind=tk.StringVar(value='mob');name=tk.StringVar(value='unknown');visible=tk.BooleanVar(value=True)
    top=ttk.Frame(root,padding=8);top.pack(fill='x')
    ttk.Combobox(top,textvariable=kind,values=['mob','item'],state='readonly',width=8).pack(side='left')
    ttk.Entry(top,textvariable=name,width=24).pack(side='left',padx=5)
    ttk.Checkbutton(top,text='Mostly visible',variable=visible).pack(side='left')
    canvas=ImageCanvas(root,960,620);canvas.pack();canvas.mode='box'
    status=ttk.Label(root);status.pack(fill='x')
    rows=tk.Listbox(root,height=5);rows.pack(fill='x')
    state={'index':0,'boxes':[],'image':None,'proposal':None}
    args.output.mkdir(parents=True,exist_ok=True)
    def paths():
        stem=f'{group}-{files[state["index"]].stem}'
        return args.output/(stem+'.png'),args.output/(stem+'.json')
    def redraw():
        canvas.overlays=[('box',row['box'],'#ffbb33' if row['role']=='mob' else '#ffff55',row['kind']) for row in state['boxes']]
        canvas.show(state['image']);rows.delete(0,'end')
        for row in state['boxes']:rows.insert('end',f"{row['role']}: {row['kind']} {row['box']} visible={row['visible']}")
    def load(index):
        state['index']=max(0,min(len(files)-1,index));state['image']=cv2.imread(str(files[state['index']]))
        _,label_path=paths();state['boxes']=[]
        if label_path.exists():
            lab=json.loads(label_path.read_text())
            for role,field in [('mob','mobs'),('item','items')]:state['boxes'] += [dict(row,role=role) for row in lab.get(field,[])]
        img=state['image'];state['proposal']=per.perceive(Frame(img,0,(0,0,img.shape[1],img.shape[0])))
        redraw();status.config(text=f"Frame {state['index']+1}/{len(files)}. Draw missing objects; Save marks this frame reviewed.")
    def add(box):
        if box is None:return
        state['boxes'].append({'role':kind.get(),'kind':name.get().strip() or 'unknown','box':list(box),'visible':1 if visible.get() else .5})
        redraw()
    canvas.on_box=add
    def proposals():
        p=state['proposal']
        for role,detections in [('mob',p.mobs),('item',p.items)]:
            for d in detections:state['boxes'].append({'role':role,'kind':d.template,'box':[d.x-d.w//2,d.y-d.h//2,d.w,d.h],'visible':1})
        redraw();status.config(text='Proposals added. Check every box and missed object before saving.')
    def delete():
        chosen=rows.curselection()
        if chosen:state['boxes'].pop(chosen[0]);redraw()
    def save():
        png,label_path=paths();shutil.copyfile(files[state['index']],png)
        label={'recording_id':group,'reviewed':True,'source_frame':files[state['index']].name,
               'mobs':[{k:v for k,v in row.items() if k!='role'} for row in state['boxes'] if row['role']=='mob'],
               'items':[{k:v for k,v in row.items() if k!='role'} for row in state['boxes'] if row['role']=='item']}
        for field in ('minimap_box','hp_bar_box','mp_bar_box'):label[field]=getattr(settings.calibration,field)
        label_path.write_text(json.dumps(label,indent=2));status.config(text=f'Saved reviewed frame {label_path.name}')
    bar=ttk.Frame(root,padding=8);bar.pack(fill='x')
    for title,command in [('Previous',lambda:load(state['index']-1)),('Use proposals',proposals),('Delete selected',delete),
                          ('Save reviewed frame',save),('Next',lambda:load(state['index']+1))]:ttk.Button(bar,text=title,command=command).pack(side='left',padx=4)
    def close():per.close();root.destroy()
    root.protocol('WM_DELETE_WINDOW',close);load(0);root.mainloop()
    return 0


if __name__=='__main__':raise SystemExit(main())
