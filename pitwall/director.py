"""Auto-director: picks the car and the camera, the way a TV director would.

iRacing's own "most exciting" mode picks a car but uses the same few TV cameras.
This chooses from every camera group the track offers -- chase, rear chase
(the car behind, from the car ahead), gyro, chopper, blimp, onboards, pit lane,
and any group added by a camera pack the user loaded -- and varies them, with
the subject chosen from what the race is doing:

    a fresh incident (contact, spin)   -> that car, from trackside
    a battle                           -> leader's rear chase / chaser's chase / onboard
    a pit stop                         -> pit lane
    the start and the leader now and then -> aerial and trackside
    otherwise                          -> the pack near the front

It never acts while the picture is behind live (a replay is being watched), and
every cut goes through the same command gate as a producer's.

Grid walk: before the start, down the starting grid one car at a time in a
chase view, optionally photographing each livery (pitwall/liveries.py).
"""
from __future__ import annotations

import random
import time

# camera group names (lower case) by kind; anything not listed is "trackside",
# which is where camera packs' custom groups land.
GROUP_KIND = {
    "chase": "chase", "far chase": "chase", "rear chase": "rear",
    "chopper": "aerial", "blimp": "aerial", "gyro": "gyro",
    "cockpit": "onboard", "roll bar": "onboard", "nose": "onboard", "gearbox": "onboard",
    "lf susp": "onboard", "rf susp": "onboard", "lr susp": "onboard", "rr susp": "onboard",
    "pit lane": "pit", "pit lane 2": "pit", "scenic": "never",
}

STYLES = {
    # weights per kind
    "cinematic": {"trackside": 4, "chase": 3, "rear": 3, "gyro": 2, "aerial": 2, "onboard": 2},
    "tv": {"trackside": 6, "chase": 2, "rear": 1, "aerial": 1},
    "onboard": {"onboard": 5, "chase": 2, "rear": 2, "trackside": 1},
}


def group_kind(name: str | None) -> str:
    n = (name or "").strip().lower()
    if n in GROUP_KIND:
        return GROUP_KIND[n]
    if n.startswith("tv"):
        return "trackside"
    return "trackside"


class Director:
    def __init__(self, cut, log=print, rng=None, clock=time.monotonic):
        self.cut = cut                    # cut(car_number: str, group: int) -> dict result
        self.log = log
        self.rng = rng or random.Random()
        self.clock = clock
        self.on = False
        self.style = "cinematic"
        self.hold_s = 8.0
        self.mode = "race"                # or "grid"
        self.grid = []                    # car dicts to walk
        self.grid_hold_s = 5.0
        self.on_grid_car = None           # callback(car) when the walk reaches a car
        self._next_at = 0.0
        self._last_group = None
        self._last_kind = None
        self._shown_incidents = set()
        self._battle_flip = False
        self._shots = 0
        self.current = None               # {"car", "group", "groupName", "reason"}
        self.changed = True

    # ------------------------------------------------------------------ #
    def configure(self, on=None, style=None, hold_s=None):
        if on is not None:
            self.on = bool(on)
            if self.on:
                self.mode = "race"
            self._next_at = 0.0
        if style in STYLES:
            self.style = style
        if hold_s is not None:
            self.hold_s = max(3.0, min(30.0, float(hold_s)))
        self.changed = True

    def start_grid_walk(self, cars: list[dict], hold_s: float = 5.0, on_car=None):
        self.grid = [c for c in cars if c.get("num") is not None]
        self.grid_hold_s = max(2.0, min(20.0, float(hold_s)))
        self.on_grid_car = on_car
        self.mode = "grid"
        self.on = True
        self._next_at = 0.0
        self.changed = True

    def stop(self):
        self.on = False
        self.mode = "race"
        self.grid = []
        self.changed = True

    def status(self) -> dict:
        return {"type": "director", "on": self.on, "mode": self.mode, "style": self.style,
                "holdS": self.hold_s, "gridLeft": len(self.grid) if self.mode == "grid" else None,
                "current": self.current, "styles": sorted(STYLES)}

    # ------------------------------------------------------------------ #
    def _groups(self, session) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for g in (session or {}).get("cameras") or []:
            if g.get("scenic"):
                continue
            k = group_kind(g.get("name"))
            if k == "never":
                continue
            out.setdefault(k, []).append(g)
        return out

    def _pick_group(self, groups, kinds_weights: dict) -> dict | None:
        options = []
        for kind, w in kinds_weights.items():
            for g in groups.get(kind, []):
                if g["num"] == self._last_group:
                    continue
                # do not repeat the same KIND twice in a row either, unless it is all there is
                ww = w * (0.35 if kind == self._last_kind else 1.0)
                options.append((ww, kind, g))
        if not options:
            return None
        total = sum(o[0] for o in options)
        r = self.rng.random() * total
        for w, kind, g in options:
            r -= w
            if r <= 0:
                return {**g, "kind": kind}
        return {**options[-1][2], "kind": options[-1][1]}

    def _do_cut(self, car: dict, group: dict, reason: str):
        res = self.cut(str(car["num"]), int(group["num"]))
        ok = res.get("ok", False) if isinstance(res, dict) else bool(res)
        if ok:
            self._last_group = group["num"]
            self._last_kind = group.get("kind")
            self._shots += 1
            self.current = {"car": car.get("idx"), "num": car.get("num"), "group": group["num"],
                            "groupName": group.get("name"), "reason": reason}
            self.changed = True
        return ok

    # ------------------------------------------------------------------ #
    def tick(self, session: dict | None, cars_msg: dict | None, state: dict | None, recent_incidents: list):
        if not self.on or not session or not cars_msg:
            return
        edge = ((state or {}).get("derived") or {}).get("liveEdge") or {}
        if edge.get("atLive") is False:
            return                                   # a replay is on: leave the camera alone
        now = self.clock()
        if now < self._next_at:
            return
        groups = self._groups(session)
        if self.mode == "grid":
            self._grid_step(groups, now)
            return
        cars = [c for c in cars_msg.get("cars") or [] if c.get("num") is not None and c.get("surface") != "not_in_world"]
        if not cars:
            return
        by = {c["idx"]: c for c in cars}
        weights = STYLES.get(self.style, STYLES["cinematic"])
        hold = self.hold_s * (0.75 + self.rng.random() * 0.5)
        race_state = ((state or {}).get("session") or {}).get("state")
        leader = min((c for c in cars if c.get("pos")), key=lambda c: c["pos"], default=None)

        # 1. a fresh incident worth showing
        for inc in reversed(recent_incidents):
            if inc.get("id") in self._shown_incidents or inc.get("what") not in ("contact", "spin", "lost_speed"):
                continue
            self._shown_incidents.add(inc.get("id"))
            c = by.get(inc.get("idx"))
            if c:
                g = self._pick_group(groups, {"trackside": 5, "chase": 2, "aerial": 1})
                if g and self._do_cut(c, g, f"incident: {inc.get('what')}"):
                    self._next_at = now + max(hold, 6.0)
                    return
        # 2. the start: wide
        if race_state in ("parade_laps", "warmup") or (leader and (leader.get("lap") or 0) <= 1 and self._shots < 3):
            if leader:
                g = self._pick_group(groups, {"aerial": 4, "trackside": 2, "rear": 1})
                if g and self._do_cut(leader, g, "the start"):
                    self._next_at = now + hold
                    return
        # 3. a car in its pit box
        pit = [c for c in cars if c.get("surface") == "in_pit_stall" and c.get("pos")]
        if pit and self.rng.random() < 0.25 and groups.get("pit"):
            c = min(pit, key=lambda c: c["pos"])
            g = self._pick_group(groups, {"pit": 1})
            if g and self._do_cut(c, g, "pit stop"):
                self._next_at = now + hold
                return
        # 4. a battle
        battles = ((cars_msg.get("derived") or {}).get("battles")) or []
        if battles and self.rng.random() < 0.75:
            b = battles[0] if self.rng.random() < 0.7 else self.rng.choice(battles)
            front, back = by.get(b["cars"][0]), by.get(b["cars"][-1])
            if front and back:
                self._battle_flip = not self._battle_flip
                if self._battle_flip and groups.get("rear"):
                    c, g, why = front, self._pick_group(groups, {"rear": 1}), "battle: the chaser, from the car ahead"
                else:
                    c = back
                    g = self._pick_group(groups, {k: w for k, w in weights.items() if k != "rear"})
                    why = "battle: the chaser"
                if g and self._do_cut(c, g, why):
                    self._next_at = now + hold
                    return
        # 5. the leader now and then, else someone near the front
        if leader and self._shots % 5 == 0:
            c, why = leader, "the leader"
        else:
            front = sorted((c for c in cars if c.get("pos")), key=lambda c: c["pos"])[:8] or cars
            c, why = self.rng.choice(front), "the field"
        g = self._pick_group(groups, weights)
        if g and self._do_cut(c, g, why):
            self._next_at = now + hold

    def _grid_step(self, groups, now):
        if not self.grid:
            self.mode = "race"
            self.on = False
            self.current = None
            self.changed = True
            return
        car = self.grid.pop(0)
        chase = [g for g in groups.get("chase", []) if (g.get("name") or "").lower() == "chase"] or groups.get("chase") \
            or groups.get("trackside") or []
        if not chase:
            return
        g = {**chase[0], "kind": "chase"}
        if self._do_cut(car, g, "starting grid"):
            if self.on_grid_car:
                self.on_grid_car(car)
        self._next_at = now + self.grid_hold_s
