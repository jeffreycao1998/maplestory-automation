"""Bounded human-readable status history. No screen access or input operations."""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import time

from .settings import REPO_ROOT


def perception_summary(p):
    details=[]
    if p.player_map is None:
        details.append('minimap player position missing')
    else:
        details.append(f'position {p.player_map[0]:.3f}, {p.player_map[1]:.3f}')
    details.append('name tag missing: attacks disabled' if p.player_screen_estimated or p.player_screen is None else 'name tag found')
    details.append(f'{len(p.mobs)} mobs detected; {len(p.items)} drops detected')
    if p.flags:details.append('flags: '+', '.join(sorted(f.value for f in p.flags)))
    return '; '.join(details)


def decision_message(perception, decision, settings, observe_only=False):
    """Explain a decision and its current visual evidence; never claim delivery."""
    p,d=perception,decision
    prefix='OBSERVE ONLY — no game input' if observe_only else 'Running'
    details=[d.reason or d.state,perception_summary(p)]
    if d.retreat_reason:details.append(d.retreat_reason)
    binds=settings.keys
    keys={
        'attack':(binds.attack,), 'walk_left':(binds.left,), 'walk_right':(binds.right,),
        'face_left':(binds.left,), 'face_right':(binds.right,), 'jump':(binds.jump,),
        'jump_up':(binds.jump,), 'jump_left':(binds.left,binds.jump),
        'jump_right':(binds.right,binds.jump), 'drop_down':(binds.down,binds.jump),
        'climb_up':(binds.up,), 'climb_down':(binds.down,), 'grab_up':(binds.up,binds.jump),
        'pickup':(binds.pickup,), 'use_hp_pot':(binds.hp_potion,), 'use_mp_pot':(binds.mp_potion,),
    }.get(d.action.value,())
    if keys:
        details.append(('would use keys: ' if observe_only else 'input requested: ')+', '.join(k for k in keys if k))
    return prefix+' — '+details[0]+'\n'+'; '.join(details[1:])


class ActivityJournal:
    def __init__(self, clock=time.monotonic):
        config=json.loads((REPO_ROOT/'configs/activity.yaml').read_text(encoding='utf-8'))
        self.lines=deque(maxlen=int(config['max_lines']))
        self.clock=clock
        self.detail_interval=float(config['detail_interval_s'])
        self.repeat_interval=float(config['repeat_interval_s'])
        self._key=None;self._message=None;self._last=-float('inf')

    def append(self,text):
        text=' '.join(str(text).splitlines())[:4000]
        line=f"{datetime.now():%H:%M:%S}  {text}"
        self.lines.append(line)
        return line

    def record(self,status):
        s=status
        category=('decision' if s.message.startswith(('Running —','OBSERVE ONLY —')) else
                  'countdown' if s.countdown_s is not None else s.message.split('\n',1)[0])
        key=(s.running,s.paused,s.observe_only,s.state,s.action,s.countdown_s is not None,category)
        now=self.clock()
        elapsed=now-self._last
        changed=s.message!=self._message
        if key==self._key and elapsed<(self.detail_interval if changed else self.repeat_interval):
            return None
        self._key,self._message,self._last=key,s.message,now
        state='Paused' if s.paused else 'Stopped' if not s.running else s.state or 'Running'
        action=('would ' if s.observe_only else '')+(s.action or '--')
        bars=' '.join(f'{name.upper()} {"?" if value is None else f"{value:.0%}"}' for name,value in (('hp',s.hp),('mp',s.mp)))
        return self.append(f'{state} | {action} | {bars} | {s.message}')

    def text(self):
        return '\n'.join(self.lines)
