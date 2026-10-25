"""Measure manual recordings, never press keys or silently change live settings.

python -m tools.calibrate_motion data/recordings/manual --profile profile --output reports/motion.json
Record at 15+ FPS with --keys; include separate walks, jumps and casts on known surfaces.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import median

import tools
from maplebot.perception.calib import minimap_player_position
from maplebot.replay.folder_capture import FolderCapture
from maplebot.settings import Profile


def measure(samples, events, keys):
    samples = [s for s in samples if s[1] is not None]
    if any(not math.isfinite(t) or any(not math.isfinite(v) or not 0<=v<=1 for v in pos)
           for t,pos in samples) or any(b[0]<=a[0] for a,b in zip(samples,samples[1:])):
        raise ValueError("Samples need increasing times and finite minimap positions")
    if any(not isinstance(e.get('t'),(int,float)) or not math.isfinite(e['t']) or
           type(e.get('down')) is not bool or not isinstance(e.get('key'),str) for e in events):
        raise ValueError("Malformed manual key event")
    events = sorted(events,key=lambda e:e['t'])
    held=set();cursor=0;walk=[];climb=[];jumps=[];casts=[]
    jump_starts=[];cast_starts=[]
    for e in events:
        if not isinstance(e.get('t'),(int,float)) or not math.isfinite(e['t']) or type(e.get('down')) is not bool:
            raise ValueError("Malformed manual key event")
        if e['down'] and e['key']==keys.jump:
            state=set()
            for prior in events:
                if prior['t']>e['t']:break
                (state.add if prior['down'] else state.discard)(prior['key'])
            if keys.down not in state:jump_starts.append(e['t'])
        if e['down'] and e['key']==keys.attack:cast_starts.append(e['t'])
    for (ta,a),(tb,b) in zip(samples,samples[1:]):
        while cursor<len(events) and events[cursor]['t']<=ta:
            event=events[cursor];(held.add if event['down'] else held.discard)(event['key']);cursor+=1
        dt=tb-ta
        if dt>.25:continue
        dx,dy=b[0]-a[0],b[1]-a[1]
        # Exclude takeoff/cast periods and unlabelled vertical movement from walking speed.
        nearby=any(ta-1.5<t<=tb for t in jump_starts+cast_starts)
        if not nearby and abs(dy)<.008 and abs(dx)>.001 and ((keys.right in held and dx>0) or (keys.left in held and dx<0)):
            walk.append(abs(dx)/dt)
        if abs(dx)<.004 and abs(dy)>.001 and ((keys.up in held and dy<0) or (keys.down in held and dy>0)):
            climb.append(abs(dy)/dt)
    for start in jump_starts:
        before=[s for s in samples if 0<=start-s[0]<=.2]
        if not before:continue
        t0,origin=before[-1];flight=[];above=False
        for t,pos in samples:
            if not start<t<=start+3:continue
            flight.append((t,pos))
            above=above or pos[1]<origin[1]-.02
            if above and len(flight)>=3 and abs(pos[1]-origin[1])<.012:
                recent=flight[-3:]
                if max(p[1] for _,p in recent)-min(p[1] for _,p in recent)<.01:
                    jumps.append({'rise':origin[1]-min(p[1] for _,p in flight),
                                  'horizontal':abs(pos[0]-origin[0]),'airtime_s':t-start})
                    break
    for start in cast_starts:
        before=[s for s in samples if 0<=start-s[0]<=.2]
        if before:
            origin=before[-1][1]
            moving=[t-start for t,pos in samples if start<t<=start+2 and abs(pos[0]-origin[0])>.004]
            if moving:casts.append(min(moving))
    warnings=[]
    if len(walk)<5:warnings.append('Need more uninterrupted walking observations')
    if len(jumps)<3:warnings.append('Need at least three complete jumps returning to the starting height')
    if len(climb)<5:warnings.append('No reliable ladder/rope speed measurement')
    directional=[j['horizontal'] for j in jumps if j['horizontal']>.02]
    if len(directional)<3:warnings.append('Need three directional jumps before estimating horizontal reach')
    profile={'verified':False,'walk_speed_map_s':median(walk) if len(walk)>=5 else None,
             'jump_rise':min(j['rise'] for j in jumps)*.9 if len(jumps)>=3 else None,
             'jump_horizontal':min(directional)*.9 if len(directional)>=3 else None,
             'airtime_s':median(j['airtime_s'] for j in jumps) if jumps else None,
             'climb_speed_map_s':median(climb) if len(climb)>=5 else None,
             'jump_samples':len(jumps)}
    return {'candidate':profile,'walk_observations':len(walk),'jumps':jumps,
            'observed_movement_after_cast_s':casts,'warnings':warnings,
            'note':'Map fractions depend on minimap calibration. Cast-to-movement observations include human delay and do not prove an animation lock. Verify candidates before use.'}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('recording',type=Path);parser.add_argument('--profile',type=Path,default=Path('profile'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    profile=Profile(args.profile)
    settings=profile.load();cal=settings.calibration
    if cal.minimap_box is None:parser.error("Calibrate the minimap before measuring movement")
    try:
        events=[json.loads(row) for row in (args.recording/'keys.jsonl').read_text().splitlines() if row.strip()]
        from maplebot.perception.map_reference import MapReference
        descriptor=profile.map_reference_descriptor(settings)
        tracker=MapReference.from_descriptor(descriptor) if descriptor else None
        samples=[]
        with FolderCapture(args.recording,fps=None) as capture:
            for f in capture:
                pos=minimap_player_position(f.image,cal.minimap_box,cal.player_dot_hsv_low,cal.player_dot_hsv_high)
                if tracker:
                    view=tracker.align(f.image,cal.minimap_box)
                    pos=view.to_map(pos[0]*cal.minimap_box[2],pos[1]*cal.minimap_box[3]) if view and pos else None
                    if pos and not all(0<=v<=1 for v in pos):pos=None
                samples.append((f.t,pos))
        report=measure(samples,events,settings.keys)
        report['recording']=str(args.recording)
        report['map_reference']=cal.map_reference
        args.output.parent.mkdir(parents=True,exist_ok=True)
        if args.output.exists():raise ValueError('Output exists; choose a new report path')
        args.output.write_text(json.dumps(report,indent=2))
    except (ValueError,OSError,KeyError) as exc:parser.exit(1,f'Calibration failed: {exc}\n')
    print(f"Wrote candidate measurements to {args.output}; verified=False, no live settings changed")
    return 0


if __name__=='__main__':raise SystemExit(main())
