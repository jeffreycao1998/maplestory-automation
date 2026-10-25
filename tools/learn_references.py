"""Fit a diverse multi-pose reference bank from reviewed real frames; evaluate by recording.

python -m tools.learn_references --data data/labeled/my-recordings --output data/reference-banks/my-map --profile profile
No model is activated or live profile changed. At least two independent recordings are
required for held-out evaluation; --in-sample is only an explicitly optimistic smoke check.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import re

import cv2
import numpy as np
import tools
from maplebot.settings import Settings, Profile
from maplebot.perception import Perceiver
from maplebot.types import Frame
from tools.eval_perception import match_mobs


def load_dataset(folder, accept_legacy=False):
    frames=[]
    for path in sorted(Path(folder).rglob('*.json')):
        image_path=path.with_suffix('.png')
        if not image_path.exists():continue
        label=json.loads(path.read_text())
        if label.get('reviewed') is not True and not accept_legacy:continue
        image=cv2.imread(str(image_path))
        if image is None:raise ValueError(f'Unreadable frame {image_path}')
        group=str(label.get('recording_id') or path.parent.resolve())
        frames.append({'path':path,'image_path':image_path,'label':label,'image':image,'group':group,
                       'sha256':hashlib.sha256(image.tobytes()).hexdigest()})
    if not frames:raise ValueError('No reviewed PNG/JSON pairs; label recordings first')
    return frames


def boxes(label,role):
    for row in label.get('mobs' if role=='mob' else 'items',[]):
        yield row if isinstance(row,dict) else {'box':row,'kind':role}


def evaluate(frames, settings, template_dir, player_template=None):
    totals={role:{'tp':0,'fp':0,'fn':0} for role in ('mob','item')}
    settings=Settings.from_dict(settings.to_dict())
    settings.ocr.enabled=False
    settings.detection.sprite_templates_per_tick=max(128,len(list(template_dir.glob("mob_*.png"))),len(list(template_dir.glob("item_*.png"))))  # exhaustive evaluation, not a live budget
    per=Perceiver(settings,list(template_dir.glob('mob_*.png')),player_template,list(template_dir.glob('item_*.png')))
    try:
        for i,frame in enumerate(frames):
            lab,img=frame['label'],frame['image']
            current=Settings.from_dict(settings.to_dict())
            for key in ('minimap_box','hp_bar_box','mp_bar_box'):
                if key in lab:setattr(current.calibration,key,lab[key])
            per.update_settings(current)
            p=per.perceive(Frame(img,float(i),(0,0,img.shape[1],img.shape[0])))
            for role,predictions in (('mob',p.mobs),('item',p.items)):
                truth=[row['box'] for row in boxes(lab,role)]
                matched,missed,false=match_mobs(truth,[(m.x,m.y) for m in predictions])
                for key,value in [('tp',len(matched)),('fn',len(missed)),('fp',len(false))]:totals[role][key]+=value
    finally:per.close()
    for result in totals.values():
        result['recall']=result['tp']/(result['tp']+result['fn']) if result['tp']+result['fn'] else None
        result['precision']=result['tp']/(result['tp']+result['fp']) if result['tp']+result['fp'] else None
        result['fp_per_frame']=result['fp']/len(frames) if frames else None
    return totals


def learn(folder,output,settings,player_template=None,max_poses=12,in_sample=False,accept_legacy=False):
    output=Path(output)
    if output.exists() and any(output.iterdir()):raise ValueError('Output must be empty')
    frames=load_dataset(folder,accept_legacy)
    groups=sorted({f['group'] for f in frames})
    if len(groups)<2 and not in_sample:
        raise ValueError('Need at least two independent recordings; do not split adjacent frames into train/test')
    test_groups=set(groups[-max(1,len(groups)//5):]) if len(groups)>1 else set()
    train=[f for f in frames if f['group'] not in test_groups]
    test=[f for f in frames if f['group'] in test_groups]
    if not test:test=train
    if test_groups and {f['sha256'] for f in train}&{f['sha256'] for f in test}:
        raise ValueError('Identical frame pixels cross train/test recordings; remove duplicated captures')
    output.mkdir(parents=True,exist_ok=True)
    poses=defaultdict(list);manifest=[]
    for frame in train:
        image=frame['image'];height,width=image.shape[:2]
        for role in ('mob','item'):
            for row in boxes(frame['label'],role):
                if float(row.get('visible',1))<.7 or any(word in row.get('note','').lower() for word in ('covered','partly')):continue
                x,y,w,h=map(int,row['box'])
                if not (0<=x<x+w<=width and 0<=y<y+h<=height and min(w,h)>=8):
                    raise ValueError(f'Invalid annotation in {frame["path"]}')
                kind=re.sub('[^a-z0-9_-]+','-',str(row.get('kind',role)).lower())[:40] or role
                crop=image[y:y+h,x:x+w].copy()
                fingerprint=cv2.resize(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(32,32)).astype(np.float32)
                key=(role,kind)
                if len(poses[key])>=max_poses or any(np.mean(np.abs(fingerprint-pose))<3 for pose in poses[key]):continue
                poses[key].append(fingerprint)
                digest=hashlib.sha256(crop.tobytes()).hexdigest()[:12]
                name=f'{role}_pose_{kind}_{digest}.png'
                if not cv2.imwrite(str(output/name),crop):raise OSError(f'Could not save {name}')
                manifest.append({'file':name,'kind':kind,'recording_id':frame['group'],
                                 'frame_sha256':frame['sha256'],'box':[x,y,w,h]})
    if not manifest:raise ValueError('No visible usable reference crops')
    metrics=evaluate(test,settings,output,player_template)
    supported=[v for v in metrics.values() if v['recall'] is not None]
    unique_test_frames=len({frame["sha256"] for frame in test})
    approved=(bool(test_groups) and unique_test_frames>=10 and bool(supported) and
              all(v['recall']>=.9 and v['fp_per_frame']<=.1 for v in supported))
    report={'method':'reviewed multi-pose reference bank; not a universal neural detector',
            'train_recordings':sorted(set(f['group'] for f in train)),
            'test_recordings':sorted(test_groups),'test_frames':len(test),'test_unique_frames':unique_test_frames,
            'in_sample':not bool(test_groups),'metrics':metrics,'meets_reference_gate':approved,
            'activated':False,'entries':manifest,
            'limitations':'Held-out recording accuracy does not establish unseen-map/item or live gameplay accuracy. Item metrics require item annotations.'}
    (output/'manifest.json').write_text(json.dumps(report,indent=2))
    return report


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--profile',type=Path,default=Path('profile'))
    parser.add_argument('--max-poses',type=int,default=12);parser.add_argument('--in-sample',action='store_true')
    parser.add_argument('--accept-legacy-labels',action='store_true')
    args=parser.parse_args(argv);profile=Profile(args.profile)
    if not 1<=args.max_poses<=40:parser.error('--max-poses must be 1..40')
    try:report=learn(args.data,args.output,profile.load(),profile.player_template(),args.max_poses,args.in_sample,args.accept_legacy_labels)
    except (OSError,ValueError,KeyError) as exc:parser.exit(1,f'Reference learning failed: {exc}\n')
    print(json.dumps({'test_frames':report['test_frames'],'in_sample':report['in_sample'],
                      'metrics':report['metrics'],'meets_reference_gate':report['meets_reference_gate']},indent=2))
    print('References saved; no live profile was changed')
    return 0


if __name__=='__main__':raise SystemExit(main())
