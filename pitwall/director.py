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

"race" mode tells stories, not shots. Each candidate gets an interest score from what the race is doing:

    a fresh incident (contact, spin)          -> cut at once, from trackside
    the start                                 -> wide, the leader
    a lead change, a pass for position        -> the cars involved
    a battle: how close, closing or not, what it is for, how many cars
    a class leader, more the longer since it was last shown
    a top car stopped in its pit box

The director stays with the best story for a few shots, changing angle within it (SEGMENT_MIN_S to SEGMENT_MAX_S),
and a story it has just shown scores lower for a while (FATIGUE_S), so it neither hops between random cars every
few seconds nor sits on one battle all race. Nothing is picked for its own sake: no random car "in the field".

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
FRESH_MOMENT_S = 12.0       # a pass or a lead change older than this is not worth cutting to
SEGMENT_MIN_S = 14.0        # a story gets at least this long (two or three shots) unless something bigger happens
SEGMENT_MAX_S = 40.0        # and at most this, then another story gets the air
FATIGUE_S = 150.0           # a story just shown scores lower for this long
INTERRUPT_BY = 35.0         # a new story must beat the one on air by this much to cut in before SEGMENT_MIN_S
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
        # The broadcast's master switch (the producer panel's "Broadcast on"). Off, which is how every
        # app start begins, nothing here moves the camera: no director, no automatic grid walk at the
        # pace laps, no followed battle, and the bridge refuses the producer's camera, replay and
        # photo commands too. Only the person running the show turns it on.
        self.live = False
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
        self.segment = None               # the story on air: {"key", "why", "cars", "start", "shot"}
        self._aired: dict = {}            # story key -> [(start, end), ...] in the last FATIGUE_S
        self._lead_shown: dict = {}       # classId -> when its leader was last on air
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

    def set_live(self, on: bool):
        """The master switch. Off stops whatever owns the camera and leaves it where it is."""
        self.live = bool(on)
        self.resume = None
        self._set_owner(None, "broadcast on" if self.live else "broadcast off")
        if not self.live:
            self.current = None
        self.changed = True

    def configure(self, on=None, style=None, hold_s=None, auto_grid=None):
        if on and not self.live:
            on = None                                 # the director runs only with the broadcast on
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
        if not self.live:
            return False
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
        return True

    def follow_battle(self, car_idxs: list[int]):
        cars = [int(i) for i in car_idxs if i is not None]
        if len(cars) < 2 or not self.live:
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

    def end_battle(self):
        """Stop following a battle: the camera goes back to whoever had it before (the director, or you)."""
        if self.on and self.mode == "battle":
            self._end_temporary("stopped following the battle")
            return True
        return False

    def stop(self):
        """Stop whatever is running; the producer has the camera."""
        self.resume = None
        self._set_owner(None, "stopped")

    def status(self) -> dict:
        return {"type": "director", "live": self.live, "on": self.on, "mode": self.mode, "owner": self.owner,
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
        if not self.live or not session or not cars_msg:
            return                                   # broadcast off: nothing runs, not even the grid walk
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
                    self._end_segment(now)
                    return
        # 2. the start: wide
        if race_state in ("parade_laps", "warmup") or (leader and (leader.get("lap") or 0) <= 1 and self._shots < 3):
            if leader:
                g = self._pick_group(groups, {"aerial": 4, "trackside": 2, "rear": 1})
                if g and self._do_cut(leader, g, "the start"):
                    self._next_at = now + hold
                    self._end_segment(now)
                    return
        # 3. a story
        self._story_step(groups, cars, by, cars_msg, recent_incidents, t_live, weights, hold, now)

    # ------------------------------------------------------------------ stories
    def _end_segment(self, now):
        if self.segment:
            self._aired.setdefault(self.segment["key"], []).append((self.segment["start"], now))
        self.segment = None

    def _aired_s(self, key, now):
        spans = [(a, b) for a, b in self._aired.get(key, []) if b > now - FATIGUE_S]
        self._aired[key] = spans
        return sum(b - max(a, now - FATIGUE_S) for a, b in spans)

    def _stories(self, cars, by, cars_msg, moments, t_live, now) -> list[dict]:
        """Every story worth a camera now, with its interest score (before fatigue)."""
        out = []
        multi = len({c.get("classId") for c in cars if c.get("classPos")}) > 1
        # lead changes and passes for position, just now
        for e in moments:
            if t_live is not None and e.get("t") is not None and t_live - e["t"] > FRESH_MOMENT_S:
                continue
            if e.get("kind") == "class_lead" and e.get("idx") in by:
                out.append({"key": "lead-change:%s" % e.get("classId"), "score": 85, "why": "new leader",
                            "cars": [e["idx"]] + ([e["prevIdx"]] if e.get("prevIdx") in by else [])})
            elif e.get("kind") == "pass" and e.get("idx") in by:
                cp = e.get("classPos") or 99
                out.append({"key": "pass:%s:%s" % (e["idx"], cp), "score": 50 + (25 if cp <= 3 else 10 if cp <= 6 else 0),
                            "why": "a pass for %sP%s" % ("class " if multi else "", cp),
                            "cars": [x for x in (e.get("passedIdx") or [])[:1] if x in by] + [e["idx"]]})
        # battles: how close, closing or not, what they are for, how many cars
        for b in (cars_msg.get("derived") or {}).get("battles") or []:
            cs = [i for i in b.get("cars") or [] if i in by]
            if len(cs) < 2 or any(by[i].get("onPitRoad") for i in cs):
                continue
            gap = min(b.get("gapsS") or [9.9])
            closing = max([x for x in b.get("closingSPerLap") or [] if x is not None] or [0.0])
            fp = b.get("forClassPos") or 99
            score = 35 + (30 if gap < 0.3 else 18 if gap < 0.6 else 6) + (12 if closing > 0.1 else 0) \
                + (30 if fp == 1 else 18 if fp <= 3 else 8 if fp <= 6 else 0) + 5 * (len(cs) - 2)
            out.append({"key": "battle:%s" % b.get("id"), "score": score, "cars": cs,
                        "why": "battle for %sP%s" % ("class " if multi else "", fp)})
        # practice and qualifying: a push lap, more the better the place it is on for and the nearer the line;
        # followed to the end of the lap (it drops out of the list when the lap ends or goes off)
        is_race = bool((cars_msg.get("derived") or {}).get("isRace"))
        for c in cars:
            q = (c.get("derived") or {}).get("qualiLap")
            if is_race or not q or c.get("onPitRoad"):
                continue
            pp = q.get("projectedPos") or 99
            score = 45 + (35 if pp == 1 else 22 if pp <= 3 else 10 if pp <= 10 else 0) + 12 * (q.get("stretch") or 0) / max(1, q.get("of") or 24)
            out.append({"key": "push:%s" % c["idx"], "score": score, "cars": [c["idx"]], "max": 150.0,
                        "why": "a flying lap, on for P%s" % pp})
        # the last lap: the leader to the line
        race = (cars_msg.get("derived") or {}).get("race") or {}
        if race.get("lapsToGo") == 1 or race.get("white"):
            lead = min((c for c in cars if c.get("pos")), key=lambda c: c["pos"], default=None)
            if lead is not None:
                out.append({"key": "finish", "score": 95, "cars": [lead["idx"]], "max": 240.0, "why": "the last lap"})
        # each class's leader, more interesting the longer it has been off air
        leaders = {}
        for c in cars:
            if c.get("classPos") == 1 and not c.get("onPitRoad"):
                leaders[c.get("classId")] = c
        for cid, c in leaders.items():
            since = now - self._lead_shown.get(str(cid), now - 120.0)
            out.append({"key": "leader:%s" % cid, "score": 18 + min(32.0, since / 6.0), "cars": [c["idx"]],
                        "why": "the %sleader" % ("class " if multi else "")})
        # a car near the front stopped in its box
        for c in cars:
            if c.get("surface") == "in_pit_stall" and (c.get("classPos") or 99) <= 5:
                out.append({"key": "pit:%s" % c["idx"], "score": 30, "cars": [c["idx"]], "why": "pit stop", "pit": True})
        return out

    def _story_step(self, groups, cars, by, cars_msg, moments, t_live, weights, hold, now):
        seg = self.segment
        best, best_s = None, -1e9
        for st in self._stories(cars, by, cars_msg, moments, t_live, now):
            sc = st["score"] - 0.4 * self._aired_s(st["key"], now)
            if seg and st["key"] == seg["key"]:
                age = now - seg["start"]
                sc += 25 if age < SEGMENT_MIN_S else -200 if age >= st.get("max", SEGMENT_MAX_S) else 0
            st["now"] = sc
            if sc > best_s:
                best, best_s = st, sc
        if best is None:
            return
        on_air = next((x for x in [best] if seg and x["key"] == seg["key"]), None)
        if seg and best["key"] != seg["key"] and now - seg["start"] < SEGMENT_MIN_S:
            # a story still running keeps the air unless the new one is much bigger
            cur = next((st for st in self._stories(cars, by, cars_msg, moments, t_live, now) if st["key"] == seg["key"]), None)
            if cur is not None and best_s < cur["score"] - 0.4 * self._aired_s(cur["key"], now) + 25 + INTERRUPT_BY:
                best, on_air = cur, cur
        if not on_air:
            if seg:
                self._aired.setdefault(seg["key"], []).append((seg["start"], now))
            seg = self.segment = {"key": best["key"], "why": best["why"], "cars": best["cars"], "start": now, "shot": 0}
        else:
            seg["cars"] = best["cars"]
        # the shot within the story: battles go round the battle's angles, the rest by style
        cs = [by[i] for i in seg["cars"] if i in by]
        if not cs:
            self.segment = None
            return
        if best.get("pit") and groups.get("pit"):
            car, g = cs[0], self._pick_group(groups, {"pit": 1})
        elif len(cs) >= 2:
            car, g = None, None
            for k in range(len(BATTLE_SHOTS)):
                kind, who = BATTLE_SHOTS[(seg["shot"] + k) % len(BATTLE_SHOTS)]
                if groups.get(kind):
                    car, g = (cs[0] if who == "front" else cs[-1]), self._pick_group(groups, {kind: 1})
                    seg["shot"] = (seg["shot"] + k + 1) % len(BATTLE_SHOTS)
                    break
        else:
            car, g = cs[0], self._pick_group(groups, weights)
        if car is not None and g and self._do_cut(car, g, seg["why"]):
            if seg["key"].startswith("leader:"):
                self._lead_shown[seg["key"].split(":", 1)[1]] = now
            for c in cs:
                if c.get("classPos") == 1:
                    self._lead_shown[str(c.get("classId"))] = now
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
