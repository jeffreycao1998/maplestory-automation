"""Bounded navigation over user-measured connections; never invent a jump."""
from __future__ import annotations

from maplebot.types import Action, Decision


class Navigation:
    def __init__(self, settings, model):
        self.settings, self.map = settings, model
        self.active = None
        self.started = None
        self.landed_since = None
        self.last_y = None
        self.seen_above = False
        self.goal_index = 0
        self.approach = None
        self.failed = ""
        self.climb_progress = None
        self.planned_route = ()

    def decide(self, p, now, *, active_only=False, goal=None):
        if not self.map.explicit:
            return None
        if self.failed:
            return Decision(Action.STOP_WALK, "NAV_FAILED", reason=self.failed)
        if p.player_map is None:
            return None
        x, y = p.player_map
        if self.active is not None:
            edge = self.active
            dest = self.map.by_name(edge.target)
            if now - self.started >= edge.timeout_s:
                self.failed = f"Landing on {edge.target} not confirmed after {edge.timeout_s:g}s"
                return Decision(Action.STOP_WALK, "NAV_FAILED", reason=self.failed)
            if edge.kind == "climb":
                return self._climb(p,now,edge,dest)
            if y < dest.height_at(x) - dest.y_above:
                self.seen_above = True
            on_dest = (dest.contains(x, y) and now - self.started >= 0.25 and
                       abs(x - edge.land_x) <= self.settings.behavior.waypoint_tolerance)
            stable = self.last_y is not None and -0.002 <= y - self.last_y <= 0.008
            if edge.kind == "jump" and not self.seen_above:
                stable = False
            self.last_y = y
            if on_dest and stable:
                if self.landed_since is None:
                    self.landed_since = now
                if now - self.landed_since >= self.settings.behavior.landing_settle_s:
                    self.active, self.approach = None, None
                    return Decision(Action.STOP_WALK, "NAV_LANDED", reason=f"Landed on {dest.name}")
            else:
                self.landed_since = None
            # Stop horizontal travel once the landing coordinate is reached; wait for gravity.
            dx = edge.land_x - x
            direction = 1 if edge.land_x > edge.launch_x else -1
            reached = abs(dx) <= self.settings.behavior.waypoint_tolerance or dx * direction <= 0
            if on_dest or edge.kind == "drop" or reached:
                action = Action.STOP_WALK
            else:
                action = Action.WALK_RIGHT if dx > 0 else Action.WALK_LEFT
            return Decision(action, "NAV_TRANSIT", reason=f"Landing on {dest.name}", transition=edge.name,route=self.planned_route)
        if active_only:
            return None
        here = self.map.platform_at(x, y)
        if here is None:
            return None
        if goal is not None and here.name == goal:
            return None
        goals = (goal,) if goal is not None else self.map.route or (self.map.training.name,)
        goal = goals[0] if goal is not None else goals[self.goal_index]
        if here.name == goal:
            if len(goals) == 1:
                return None
            self.goal_index = (self.goal_index + 1) % len(goals)
            goal = goals[self.goal_index]
        path = self.map.path(here.name, goal)
        if not path:
            if here.name == goal:
                return None
            self.failed = f"No measured path from {here.name} to {goal}"
            return Decision(Action.STOP_WALK, "NAV_FAILED", reason=self.failed)
        self.planned_route = tuple(e.name for e in path)
        edge = path[0]
        if self.approach is None or self.approach[0] != edge.name:
            self.approach = (edge.name, now)
        if now - self.approach[1] >= max(3.0, self.settings.behavior.stuck_s * 2):
            self.failed = f"Cannot reach launch point for {edge.name}"
            return Decision(Action.STOP_WALK, "NAV_FAILED", reason=self.failed)
        if abs(x - edge.launch_x) > self.settings.behavior.waypoint_tolerance:
            action = Action.WALK_RIGHT if edge.launch_x > x else Action.WALK_LEFT
            return Decision(action, "NAV_APPROACH", reason=f"Approaching {edge.name}",route=self.planned_route)
        self.active, self.started, self.last_y = edge, now, y
        self.landed_since = None
        self.seen_above = False
        if edge.kind == "climb":
            self.climb_progress = (y,now)
            ascending = dest_y(self.map,edge) < self.map.by_name(edge.source).height_at(edge.launch_x)
            action = Action.GRAB_UP if edge.grab_jump else Action.CLIMB_UP if ascending else Action.CLIMB_DOWN
        elif edge.kind == "drop":
            action = Action.DROP_DOWN
        elif edge.kind == "jump":
            action = (Action.JUMP_UP if abs(edge.land_x - edge.launch_x) <= 0.001 else
                      Action.JUMP_RIGHT if edge.land_x > edge.launch_x else Action.JUMP_LEFT)
        else:
            action = Action.WALK_RIGHT if edge.land_x > x else Action.WALK_LEFT
        return Decision(action, "NAV_LAUNCH", reason=f"{edge.kind} to {edge.target}", transition=edge.name,route=self.planned_route)

    def _climb(self,p,now,edge,dest):
        x,y = p.player_map
        start_y=self.map.by_name(edge.source).height_at(edge.launch_x)
        end_y=dest.height_at(edge.land_x)
        if not min(start_y,end_y)-.05 <= y <= max(start_y,end_y)+.05:
            self.failed = "Left the ladder vertical corridor"
            return Decision(Action.STOP_WALK,"NAV_FAILED",reason=self.failed)
        if abs(x-edge.land_x) > .03:
            self.failed = "Lost alignment with ladder/rope"
            return Decision(Action.STOP_WALK,"NAV_FAILED",reason=self.failed)
        at_dest = dest.contains(x,y) and now-self.started >= .25
        if at_dest:
            if self.last_y is not None and abs(y-self.last_y) <= .008:
                if self.landed_since is None:
                    self.landed_since = now
                if now-self.landed_since >= self.settings.behavior.landing_settle_s:
                    self.active,self.approach = None,None
                    return Decision(Action.STOP_WALK,"NAV_LANDED",reason=f"Left ladder at {dest.name}")
            else:
                self.landed_since = None
            action = Action.STOP_WALK
        else:
            self.landed_since = None
            if self.climb_progress is None or abs(y-self.climb_progress[0]) > .004:
                self.climb_progress = y,now
            elif now-self.climb_progress[1] >= self.settings.behavior.stuck_s:
                self.failed = "No progress while climbing ladder/rope"
                return Decision(Action.STOP_WALK,"NAV_FAILED",reason=self.failed)
            action = Action.CLIMB_UP if dest_y(self.map,edge) < y else Action.CLIMB_DOWN
        self.last_y = y
        return Decision(action,"NAV_TRANSIT",reason=f"Climbing to {dest.name}",transition=edge.name,route=self.planned_route)


def dest_y(model,edge):
    return model.by_name(edge.target).height_at(edge.land_x)
