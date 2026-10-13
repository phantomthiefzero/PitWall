"""Who moves the camera, and the auto-director.

One owner at a time. Before this, the auto-director, the grid walk and the
producer's own cuts all moved iRacing's camera independently: the director cut
away seconds after the producer chose a car, and a battle the producer picked
was abandoned on the next shot. Now there is exactly one owner, and it is shown
in the producer panel:

    "you"     the producer. Every cut made by hand -- a car, a camera group,
              iRacing's own "most exciting" -- takes the camera back from
              whatever had it, and nothing moves it again until asked.
    "race"    the auto-director picks the car and the shot from the race.
    "grid"    the grid walk: down the starting grid one car at a time, in
              order, chase camera, holding each one. Starts on its own when the
              pace laps begin (can be turned off); ends at the green flag.
    "battle"  follows the cars of one battle, varying the shot between them,
              until the battle is over.

"grid" and "battle" are temporary: when they end, the camera goes back to
whoever had it before ("race" or "you"). Nothing acts while a replay is on
screen, nor while livery photos are being taken; the owner carries on at live.

Shot choice in "race" mode, from what the race is doing:

    a fresh incident (contact, spin)   -> that car, from trackside
    a battle                           -> leader's rear chase / chaser's chase / onboard
    a pit stop                         -> pit lane
    the start and the leader now and then -> aerial and trackside
    otherwise                          -> the pack near the front

Every cut goes through the same command gate as a producer's.
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

# A battle, shot by shot: (kind, which car). "rear" on the car in FRONT looks
# back at it and shows the chaser behind; "chase" on the LAST car shows the
# whole train ahead of it.
BATTLE_SHOTS = [("chase", "back"), ("rear", "front"), ("trackside", "back"),
                ("aerial", "front"), ("onboard", "back")]

FRESH_INCIDENT_S = 20.0     # an incident older than this is not news
BATTLE_LOST_S = 8.0         # a followed battle missing this long is over


def group_kind(name: str | None) -> str:
    n = (name or "").strip().lower()
    if n in GROUP_KIND:
        return GROUP_KIND[n]
    return "trackside"


class Director:
    def __init__(self, cut, log=print, rng=None, clock=time.monotonic):
        self.cut = cut                    # cut(car_number: str, group: int) -> dict result
        self.log = log
        self.rng = rng or random.Random()
        self.clock = clock
        # owner: on=False is "you"; on=True with mode race / grid / battle
        self.on = False
        self.mode = "race"
        self.resume = None                # owner to return to after grid / battle: "race" or None
        self.style = "cinematic"
        self.hold_s = 8.0
        self.auto_grid = True             # walk the grid by itself when the pace laps start
        self.grid = []                    # car dicts still to visit
        self.grid_total = 0
        self.grid_hold_s = 5.0
        self.on_grid_car = None           # callback(car) when the walk reaches a car
        self.battle = None                # {"cars": [idx...], "lostAt": t|None, "shot": n}
        self.battle_hold_s = 6.0
        self.note = None                  # why the owner last changed, for the panel
        self._next_at = 0.0
        self._last_group = None
        self._last_kind = None
        self._shown_incidents = set()
        self._battle_flip = False
        self._shots = 0
        self._grid_walked = set()         # session numbers already walked automatically
        self._last_state = None
        self.current = None               # {"car", "num", "group", "groupName", "reason"}
        self.changed = True

    # ------------------------------------------------------------------ owner
    @property
    def owner(self) -> str:
        return self.mode if self.on else "you"

    def _set_owner(self, owner, note=None):
        if owner in (None, "you"):
            self.on, self.mode = False, "race"
        else:
            self.on, self.mode = True, owner
        if owner != "grid":
            self.grid = []
        if owner != "battle":
            self.battle = None
        self.note = note
        self._next_at = 0.0
        self.changed = True

    def _end_temporary(self, note):
        back, self.resume = self.resume, None
        self._set_owner(back, note)

    def configure(self, on=None, style=None, hold_s=None, auto_grid=None):
        if on is not None:
            self.resume = None
            self._set_owner("race" if on else None, "director on" if on else "director off")
        if style in STYLES:
            self.style = style
        if hold_s is not None:
            self.hold_s = max(3.0, min(30.0, float(hold_s)))
        if auto_grid is not None:
            self.auto_grid = bool(auto_grid)
        self.changed = True

    def manual(self, why="you cut the camera"):
        """The producer cut by hand: they have the camera now, from anything."""
        if not self.on:
            return False
        self.resume = None
        self._set_owner(None, why)
        return True

    def start_grid_walk(self, cars: list[dict], hold_s: float | None = None, on_car=None):
        self.grid = [c for c in cars if c.get("num") is not None]
        self.grid_total = len(self.grid)
        if hold_s is not None:
            self.grid_hold_s = max(2.0, min(20.0, float(hold_s)))
        self.on_grid_car = on_car
        if self.owner not in ("grid", "battle"):
            self.resume = "race" if self.owner == "race" else None
        self.on, self.mode = True, "grid"
        self.battle = None
        self.note = "walking the grid"
        self._next_at = 0.0
        self.changed = True

    def follow_battle(self, car_idxs: list[int]):
        cars = [int(i) for i in car_idxs if i is not None]
        if len(cars) < 2:
            return False
        if self.owner not in ("grid", "battle"):
            self.resume = "race" if self.owner == "race" else None
        self.on, self.mode = True, "battle"
        self.grid = []
        self.battle = {"cars": cars, "lostAt": None, "shot": 0}
        self.note = "following a battle"
        self._next_at = 0.0
        self.changed = True
        return True

    def stop(self):
        """Stop whatever is running; the producer has the camera."""
        self.resume = None
        self._set_owner(None, "stopped")

    def status(self) -> dict:
        return {"type": "director", "on": self.on, "mode": self.mode, "owner": self.owner,
                "resume": self.resume, "style": self.style, "holdS": self.hold_s,
                "autoGrid": self.auto_grid, "gridHoldS": self.grid_hold_s,
                "gridLeft": len(self.grid) if self.owner == "grid" else None,
                "gridTotal": self.grid_total if self.owner == "grid" else None,
                "battle": list(self.battle["cars"]) if self.battle else None,
                "note": self.note, "current": self.current, "styles": sorted(STYLES)}

    # ------------------------------------------------------------------ shots
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
                if g["num"] == self._last_group and sum(len(groups.get(k, [])) for k in kinds_weights) > 1:
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

    # ------------------------------------------------------------------ tick
    def tick(self, session: dict | None, cars_msg: dict | None, state: dict | None,
             recent_incidents: list, busy: bool = False):
        if not session or not cars_msg:
            return
        race_state = ((state or {}).get("session") or {}).get("state")
        self._watch_session(session, cars_msg, state, race_state)
        if not self.on:
            return
        edge = ((state or {}).get("derived") or {}).get("liveEdge") or {}
        if edge.get("atLive") is False or busy:
            return                                   # a replay / a photo sweep: leave the camera alone
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
        if self.mode == "battle":
            self._battle_step(groups, by, cars_msg, now)
            return
        self._race_step(groups, cars, by, cars_msg, state, race_state, recent_incidents, edge, now)

    def _watch_session(self, session, cars_msg, state, race_state):
        """The pace laps starting begin the grid walk; the green flag ends it."""
        was, self._last_state = self._last_state, race_state
        is_race = bool((cars_msg.get("derived") or {}).get("isRace"))
        snum = ((state or {}).get("session") or {}).get("num")
        if self.mode == "grid" and self.on and race_state == "racing" and was in ("parade_laps", "warmup", "get_in_car"):
            self._end_temporary("the green flag: grid walk over")
            return
        if (self.auto_grid and is_race and race_state == "parade_laps" and was != "parade_laps"
                and snum not in self._grid_walked and self.owner != "battle"):
            rows = (session.get("grid") or {}).get("rows") or []
            by = {c["idx"]: c for c in cars_msg.get("cars") or []}
            order = [by[r["idx"]] for r in rows if r.get("idx") in by]
            if order:
                self._grid_walked.add(snum)
                self.start_grid_walk(order, on_car=self.on_grid_car)
                self.note = "pace laps: walking the grid"

    def _race_step(self, groups, cars, by, cars_msg, state, race_state, recent_incidents, edge, now):
        weights = STYLES.get(self.style, STYLES["cinematic"])
        hold = self.hold_s * (0.75 + self.rng.random() * 0.5)
        leader = min((c for c in cars if c.get("pos")), key=lambda c: c["pos"], default=None)
        t_live = edge.get("tLive")
        # 1. a fresh incident worth showing (one from minutes ago is not news:
        #    turning the director on must not replay the whole feed)
        for inc in reversed(recent_incidents):
            if inc.get("id") in self._shown_incidents or inc.get("what") not in ("contact", "spin", "lost_speed"):
                continue
            self._shown_incidents.add(inc.get("id"))
            if inc.get("what") == "lost_speed" and inc.get("confidence") == "low":
                continue
            if t_live is not None and inc.get("t") is not None and t_live - inc["t"] > FRESH_INCIDENT_S:
                continue
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
                    c, g, why = front, self._pick_group(groups, {"rear": 1}), "battle: from the car ahead"
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

    def _battle_step(self, groups, by, cars_msg, now):
        b = self.battle
        want = set(b["cars"])
        battles = ((cars_msg.get("derived") or {}).get("battles")) or []
        # the same battle: shares at least two cars (it may have grown or shrunk)
        match = max(battles, key=lambda x: len(want & set(x["cars"])), default=None)
        if match and len(want & set(match["cars"])) >= 2:
            b["cars"], b["lostAt"] = list(match["cars"]), None
        else:
            b["lostAt"] = b["lostAt"] if b["lostAt"] is not None else now
            if now - b["lostAt"] >= BATTLE_LOST_S:
                self._end_temporary("the battle is over")
                return
        front, back = by.get(b["cars"][0]), by.get(b["cars"][-1])
        if not front or not back:
            return
        for k in range(len(BATTLE_SHOTS)):
            kind, who = BATTLE_SHOTS[(b["shot"] + k) % len(BATTLE_SHOTS)]
            if not groups.get(kind):
                continue
            g = self._pick_group(groups, {kind: 1})
            car = front if who == "front" else back
            if g and self._do_cut(car, g, "battle"):
                b["shot"] = (b["shot"] + k + 1) % len(BATTLE_SHOTS)
                self._next_at = now + self.battle_hold_s
                return
        self._next_at = now + 1.0

    def _grid_step(self, groups, now):
        if not self.grid:
            self.current = None
            self._end_temporary("grid walk done")
            return
        car = self.grid.pop(0)
        chase = [g for g in groups.get("chase", []) if (g.get("name") or "").lower() == "chase"] or groups.get("chase") \
            or groups.get("trackside") or []
        if not chase:
            return
        g = {**chase[0], "kind": "chase"}
        if self._do_cut(car, g, "starting grid"):
            self.current["gridPos"] = self.grid_total - len(self.grid)
            if self.on_grid_car:
                self.on_grid_car(car)
        self.changed = True
        self._next_at = now + self.grid_hold_s
