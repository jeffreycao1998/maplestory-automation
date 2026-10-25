"""Current-client footholds/spawns -> candidate map geometry. No live input or I/O."""
from __future__ import annotations

import math


def unpack_node(node):
    if isinstance(node, dict) and '$type' in node:
        return {key:unpack_node(value) for key,value in node.get('children',{}).items()}
    return node


def source_geometry(foothold_node, ladder_node, minimap, spawns=(), names=None):
    """Coordinates use the full minimap canvas, never the currently visible crop."""
    width,height=float(minimap['width']),float(minimap['height'])
    cx,cy=float(minimap['center_x']),float(minimap['center_y'])
    if not all(math.isfinite(v) for v in (width,height,cx,cy)) or min(width,height)<=0:
        raise ValueError('Invalid source minimap dimensions')
    def point(x,y):return ((float(x)+cx)/width,(float(y)+cy)/height)
    raw=unpack_node(foothold_node)
    segments=[];scanned=0
    for layer,groups in raw.items():
        for group,rows in groups.items():
            for rid,row in rows.items():
                scanned+=1
                if scanned>20000:raise ValueError('Too many source footholds')
                if not all(k in row for k in ('x1','y1','x2','y2')):continue
                values={k:int(row[k]) for k in ('x1','y1','x2','y2')}
                if values['x1']>=values['x2']:continue       # walls/reversed collision edges
                a,b=point(values['x1'],values['y1']),point(values['x2'],values['y2'])
                if not (0<=a[0]<b[0]<=1 and 0<=a[1]<=1 and 0<=b[1]<=1):continue
                segments.append(dict(values,id=str(rid),layer=str(layer),group=str(group),
                                     next=str(row.get('next','0')),prev=str(row.get('prev','0'))))
    by_id={(s['layer'],s['group'],s['id']):s for s in segments}
    used=set();platforms=[];foothold_platform={}
    for row in segments:
        key=(row['layer'],row['group'],row['id'])
        if key in used:continue
        start=row;seen=set()
        while start['id'] not in seen:
            seen.add(start['id']);previous=by_id.get((start['layer'],start['group'],start['prev']))
            if not previous or (previous['x2'],previous['y2'])!=(start['x1'],start['y1']):break
            start=previous
        chain=[];cursor=start
        while cursor is not None:
            key=(cursor['layer'],cursor['group'],cursor['id'])
            if key in used:break
            used.add(key);chain.append(cursor)
            nxt=by_id.get((cursor['layer'],cursor['group'],cursor['next']))
            if not nxt or (cursor['x2'],cursor['y2'])!=(nxt['x1'],nxt['y1']):break
            cursor=nxt
        if not chain:continue
        name=f"fh-{start['layer']}-{start['group']}-{start['id']}"
        points=[point(chain[0]['x1'],chain[0]['y1'])]+[point(s['x2'],s['y2']) for s in chain]
        platforms.append({'name':name,'points':[list(p) for p in points],
                          'tolerance':max(1.5*float(minimap.get('world_scale',16))/height,.006),
                          'drop_through':False})
        for s in chain:foothold_platform[s['id']]=name
    ladders=[]
    for lid,row in unpack_node(ladder_node).items():
        x,top=point(row['x'],min(int(row['y1']),int(row['y2'])))
        _,bottom=point(row['x'],max(int(row['y1']),int(row['y2'])))
        if 0<=x<=1 and 0<=top<bottom<=1:
            ladders.append({'name':f'ladder-{lid}','x':x,'top':top,'bottom':bottom})
    zones={}
    for spawn in spawns:
        pid=foothold_platform.get(str(spawn.get('fh','')))
        if pid is None:continue
        px,py=point(spawn['x'],spawn.get('cy',spawn['y']))
        if not (0<=px<=1 and 0<=py<=1):continue
        zone=zones.setdefault(pid,{'platform':pid,'spawns':[],'mobs':{}})
        mid=str(int(spawn['id']))
        zone['spawns'].append([px,py]);zone['mobs'][mid]=zone['mobs'].get(mid,0)+1
    return {'platforms':platforms,'ladders':ladders,'spawn_zones':list(zones.values()),
            'foothold_count':sum(len(rows) for groups in raw.values() for rows in groups.values()),
            'connections':[],'route':[],'requires_alignment':True,'requires_movement_calibration':True}


def candidate_connections(geometry, movement, margin=.012):
    """Conservative proposals only: jumps/grabs require a verified movement profile.

    This is not a proof of collision-free flight. Candidates must be reviewed in
    Observe mode; failed landings remain bounded by the navigation watchdog.
    """
    from . import Platform
    platforms=[Platform(p['points'][0][0],p['points'][-1][0],name=p['name'],
                        points=tuple(tuple(v) for v in p['points']),
                        y_below=p['tolerance'],y_above=p['tolerance']) for p in geometry['platforms']]
    result=[]
    verified=movement.get('verified') is True
    rise=movement.get('jump_rise',0) if verified else 0
    reach=movement.get('jump_horizontal',0) if verified else 0
    climb=movement.get('climb_speed_map_s',0) if verified else 0
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and v>=0 for v in (rise,reach,climb)):
        raise ValueError('Invalid measured movement capabilities')
    for ladder in geometry['ladders']:
        x=ladder['x']
        tops=[p for p in platforms if p.left+margin<=x<=p.right-margin and
              abs(p.height_at(x)-ladder['top'])<=p.y_above+.002]
        bottoms=[p for p in platforms if p.left+margin<=x<=p.right-margin and
                 0<=p.height_at(x)-ladder['bottom']<=max(rise,.002)+p.y_above]
        if not climb or len(tops)!=1 or len(bottoms)!=1 or tops[0].name==bottoms[0].name:continue
        a,b=bottoms[0],tops[0]
        gap=max(0,a.height_at(x)-ladder['bottom'])
        if gap>rise:continue
        timeout=min(30.,max(2.,(a.height_at(x)-b.height_at(x))/climb*1.8+1.))
        for src,dst,grab in ((a,b,gap>.002),(b,a,False)):
            result.append({'name':f"{ladder['name']}-{src.name}-{dst.name}",'source':src.name,
                           'target':dst.name,'kind':'climb','launch_x':x,'land_x':x,
                           'ladder':ladder['name'],'grab_jump':grab,'timeout_s':timeout})
    for a in platforms:
        for b in platforms:
            if a.name==b.name:continue
            # Touching surfaces need no guessed jump capability.
            boundary=a.right if abs(a.right-b.left)<1e-8 else b.right if abs(b.right-a.left)<1e-8 else None
            if boundary is not None and abs(a.height_at(boundary)-b.height_at(boundary))<.001:
                delta=min(.004,(a.right-a.left)/4,(b.right-b.left)/4)
                ax=boundary-delta if a.right==boundary else boundary+delta
                bx=boundary+delta if b.left==boundary else boundary-delta
                if abs(ax-bx)>=.001:
                    result.append({'name':f'walk-{a.name}-{b.name}','source':a.name,'target':b.name,
                                   'kind':'walk','launch_x':ax,'land_x':bx,'timeout_s':2.})
                continue
            lo=max(a.left,b.left)+margin;hi=min(a.right,b.right)-margin
            if lo<=hi:
                ax=bx=(lo+hi)/2
            elif a.right<b.left:
                ax,bx=a.right-margin,b.left+margin
            elif b.right<a.left:
                ax,bx=a.left+margin,b.right-margin
            else:continue
            if not (a.left+margin<=ax<=a.right-margin and b.left+margin<=bx<=b.right-margin):continue
            dy=a.height_at(ax)-b.height_at(bx)
            if verified and rise and reach and -.004<=dy<=rise*.9 and (.004<dy or abs(bx-ax)>.001) and abs(bx-ax)<=reach*.9:
                result.append({'name':f'jump-{a.name}-{b.name}','source':a.name,'target':b.name,
                               'kind':'jump','launch_x':ax,'land_x':bx,'timeout_s':3.})
    return result
