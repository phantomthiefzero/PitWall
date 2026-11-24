"""Finds the moments worth watching for ONE car in a race, from replay ticks.

Fed one frame at a time (feed), so a scan of a 24-hour race never holds the race in memory, and so the same code
reads a 16x scan of the replay, a suite Recording, or the fake field. Only raw iRacing variables are read:

    CarIdxLapCompleted + CarIdxLapDistPct   each car's race distance (laps), unwrapped at the line
    CarIdxTrackSurface                      -1 not in world, 0 off track, 1 pit stall, 2 approaching pits, 3 on track
    CarIdxOnPitRoad                         between the pit cones
    CarIdxLastLapTime                       each car's lap times (a new value = a lap)
    CarIdxClass                             class id (multiclass)
    SessionState                            4 racing, 5 checkered (the green flag is 3 -> 4)
    ReplaySessionNum / ReplaySessionTime    where on the tape (SessionNum / SessionTime when no replay plays)

What a moment is, for the chosen car "T":
    pass / lost     T and another car swap places on track and stay swapped HOLD_S. A same-class car on the same lap
                    is a position; a car of another class, or a lap up or down, is traffic (scored low).
    battle          a same-class car on the same lap within BATTLE_GAP_S for at least BATTLE_MIN_S.
    off / spin      T off track, or T far slower than its own class is going right now (a spin, contact, a stop:
                    measured against the class, so a caution or the opening lap is not a spin).
    towed           T leaves the world mid-race outside the pits (a reset or a tow).
    drama           another car near T goes off or nearly stops.
    pit             T's stop: arriving in the box and leaving it.
    fastest         T's best lap (more if it is the class's fastest of the race).
    start / finish  the green flag, and T taking the flag (from the replay's own final results when it has them).

Times are the tape's: (session number, seconds), which is what the clip recorder seeks to.
"""
from __future__ import annotations

import math
import statistics

NOT_IN_WORLD, OFF_TRACK, IN_STALL, APPROACHING_PITS, ON_TRACK = -1, 0, 1, 2, 3

HOLD_S = 4.0              # a swap of places must stand this long to be a pass (side by side flips back and forth)
NEAR_LAP = 0.03           # two cars this close (fraction of a lap) can be passing each other
BATTLE_GAP_S = 1.0        # a battle: within this ...
BATTLE_END_S = 6.0        # ... and over 1.5 s for this long ends it
BATTLE_MIN_S = 20.0       # ... for at least this long
SLOW_RATIO = 0.45         # slower than this share of the class's speed is a spin / stop
SLOW_MIN_S = 1.0
OFF_MIN_S = 0.3
DRAMA_NEAR_S = 2.5
RATE_TAU_S = 1.5          # smoothing of each car's speed (laps per second)
LATE_SHARE = 0.10         # the last 10% of the race scores more
LATE_BONUS = 1.3

KIND_LABEL = {
    "start": "The start", "finish": "The finish", "pass": "Pass", "lost": "Lost a place", "traffic": "Traffic",
    "battle": "Battle", "off": "Off track", "spin": "Big moment", "towed": "Out on track", "drama": "Drama nearby",
    "pit": "Pit stop", "fastest": "Fastest lap",
}


def _num(v, default=None):
    return v if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)) else default


def race_sessions(session_info) -> list[int]:
    out = []
    for s in ((session_info or {}).get("Sessions") or []):
        if isinstance(s, dict) and str(s.get("SessionType", "")).lower() == "race":
            out.append(s.get("SessionNum"))
    return [s for s in out if isinstance(s, int)]


def field(driver_info) -> list[dict]:
    """The cars that race: no pace car, no spectators."""
    out = []
    for d in ((driver_info or {}).get("Drivers") or []):
        if not isinstance(d, dict) or d.get("CarIsPaceCar") in (1, True) or d.get("IsSpectator") in (1, True):
            continue
        if not isinstance(d.get("CarIdx"), int):
            continue
        out.append({"idx": d["CarIdx"], "number": str(d.get("CarNumber") or ""), "driver": d.get("UserName") or "",
                    "team": d.get("TeamName") or "", "cls": d.get("CarClassShortName") or "",
                    "classId": d.get("CarClassID"), "car": d.get("CarScreenNameShort") or d.get("CarScreenName") or ""})
    return out


def _ord(n) -> str:
    return "P%d" % n


class _Car:
    __slots__ = ("dist", "pct", "lc", "rate", "t", "surf", "pit", "last_lap", "laps", "cls", "seen")

    def __init__(self):
        self.dist = None
        self.pct = None
        self.lc = None
        self.rate = None
        self.t = None
        self.surf = None
        self.pit = False
        self.last_lap = None
        self.laps = []
        self.cls = None
        self.seen = False


class Analyzer:
    def __init__(self, target: int, race_session: int | None = None):
        self.target = target
        self.race_session = race_session
        self.cars: dict[int, _Car] = {}
        self.moments: list[dict] = []
        self.green = None              # tape seconds of the green flag
        self.green_guessed = False
        self.checkered = None
        self.t = None
        self.t_first = None
        self.state = None
        self.results = None            # the replay's own final results for T, when its session info has them
        self.track = ""
        self.event = ""
        self.cls_rate: dict = {}       # class id -> median speed now (laps/s)
        self.cls_typ: dict = {}        # class id -> usual race speed (slow EMA of the median)
        self.crossings: list[tuple[float, int]] = []    # (t, laps completed) for T
        self.t_laps: list[tuple[float, float, int]] = []   # (t, lap time, lap) for T
        self.cls_best: dict = {}       # class id -> best lap time seen (anyone in it), for "fastest of the race"
        # running detectors
        self._rel: dict[int, float] = {}
        self._pending: list[dict] = []
        self._battle: dict[int, dict] = {}
        self._off = None
        self._slow = None
        self._gone = None
        self._pit = None
        self._other_off: dict[int, float] = {}
        self._other_slow: dict[int, float] = {}
        self._drama_t: dict[int, float] = {}
        self._last_drama = -1e9
        self._pass_log: list[dict] = []

    # ------------------------------------------------------------------ session info
    def session(self, info: dict):
        si = (info or {}).get("SessionInfo") or {}
        if self.race_session is None:
            r = race_sessions(si)
            if r:
                self.race_session = r[-1]
        wk = (info or {}).get("WeekendInfo") or {}
        if isinstance(wk, dict):
            self.track = wk.get("TrackDisplayName") or self.track
            cfg = wk.get("TrackConfigName")
            if cfg and cfg not in self.track:
                self.track = "%s - %s" % (self.track, cfg)
            self.event = wk.get("SeriesName") or wk.get("EventType") or self.event
        for s in (si.get("Sessions") or []):
            if isinstance(s, dict) and s.get("SessionNum") == self.race_session:
                for r in (s.get("ResultsPositions") or []):
                    if isinstance(r, dict) and r.get("CarIdx") == self.target:
                        laps = _num(r.get("LapsComplete"))
                        cp = _num(r.get("ClassPosition"))
                        if laps is not None and laps > 0:
                            self.results = {"laps": int(laps),
                                            "classPos": int(cp) + 1 if cp is not None else None,
                                            "pos": _num(r.get("Position"))}

    # ------------------------------------------------------------------ frames
    def feed(self, snum, t, get):
        """One frame: snum, t = where on the tape; get(name) = the frame's raw variable."""
        if self.race_session is not None and snum != self.race_session:
            return
        if not isinstance(t, (int, float)):
            return
        if self.t is not None and t <= self.t:
            return                                   # paused, or the tape went back: not new
        dt = 0.0 if self.t is None else t - self.t
        if dt > 30:
            # the tape jumped ahead (a skip): start the per-car tracking again
            self._rel.clear()
            self._pending.clear()
            for c in self.cars.values():
                c.rate = None
            dt = 0.0
        self.t = t
        if self.t_first is None:
            self.t_first = t
        st = get("SessionState")
        if isinstance(st, int):
            if self.green is None and st == 4 and self.state in (1, 2, 3):
                self.green = t
            if self.checkered is None and st >= 5 and self.state == 4:
                self.checkered = t
            self.state = st
        self._read_cars(t, dt, get)
        if self.green is None:
            if self.state is None or self.state == 4:
                # no green seen (the scan began mid-race or the replay sends no state): from the first frame where
                # the field is moving at racing speed
                self._guess_green(t)
            return
        self._detect(t, dt)

    def _read_cars(self, t, dt, get):
        lc_a, pct_a = get("CarIdxLapCompleted") or [], get("CarIdxLapDistPct") or []
        surf_a, pit_a = get("CarIdxTrackSurface") or [], get("CarIdxOnPitRoad") or []
        last_a, cls_a = get("CarIdxLastLapTime") or [], get("CarIdxClass") or []
        alpha = 1.0 - math.exp(-dt / RATE_TAU_S) if dt > 0 else 1.0
        for i in range(min(len(lc_a), len(pct_a))):
            lc, pct = _num(lc_a[i]), _num(pct_a[i])
            surf = _num(surf_a[i], NOT_IN_WORLD) if i < len(surf_a) else NOT_IN_WORLD
            c = self.cars.get(i)
            if lc is None or pct is None or pct < 0 or surf == NOT_IN_WORLD:
                if c is not None:
                    c.surf, c.rate = NOT_IN_WORLD, None
                continue
            if c is None:
                c = self.cars[i] = _Car()
            c.cls = _num(cls_a[i]) if i < len(cls_a) else None
            c.surf = surf
            c.pit = bool(pit_a[i]) if i < len(pit_a) else False
            naive = lc + pct
            if c.dist is None or c.pct is None:
                c.dist = naive
            else:
                step = pct - c.pct
                step -= round(step)                  # across the line: 0.99 -> 0.01 is +0.02
                d = c.dist + step
                if abs(d - naive) > 0.5 and 0.1 < pct < 0.9:
                    d = naive                        # a reset or a tow: take the counter's word
                if dt > 0:
                    r = max(0.0, (d - c.dist) / dt)
                    c.rate = r if c.rate is None else c.rate + alpha * (r - c.rate)
                c.dist = d
            c.pct, c.t = pct, t
            if c.lc is not None and lc > c.lc and i == self.target:
                self.crossings.append((t, int(lc)))
            c.lc = lc
            lt = _num(last_a[i]) if i < len(last_a) else None
            if lt is not None and lt > 0 and lt != c.last_lap:
                if c.last_lap is not None or c.seen:
                    c.laps.append(lt)
                    if c.cls is not None and self.green is not None:
                        b = self.cls_best.get(c.cls)
                        self.cls_best[c.cls] = lt if b is None else min(b, lt)
                    if i == self.target and self.green is not None:
                        self.t_laps.append((t, lt, int(lc)))
                c.last_lap = lt
            c.seen = True
        # each class's speed right now, and its usual race speed
        by_cls: dict = {}
        for c in self.cars.values():
            if c.rate is not None and c.surf == ON_TRACK and not c.pit:
                by_cls.setdefault(c.cls, []).append(c.rate)
        a_slow = 1.0 - math.exp(-dt / 60.0) if dt > 0 else 0.0
        for k, rates in by_cls.items():
            if len(rates) >= 1:
                m = statistics.median(rates)
                self.cls_rate[k] = m
                if self.green is not None:
                    self.cls_typ[k] = m if k not in self.cls_typ else self.cls_typ[k] + a_slow * (m - self.cls_typ[k])

    def _guess_green(self, t):
        me = self.cars.get(self.target)
        if me is None or me.rate is None:
            return
        k = me.cls
        r = self.cls_rate.get(k)
        moving = [c for c in self.cars.values() if c.rate is not None and c.surf == ON_TRACK and not c.pit]
        if r and len(moving) >= max(2, len(self.cars) // 2):
            if not hasattr(self, "_peak"):
                self._peak = 0.0
            self._peak = max(self._peak, r)
            # racing pace is reached when the class median holds steady after rising (a lap at least)
            if me.lc is not None and me.lc >= 1 and r > 0.8 * self._peak:
                self.green = t
                self.green_guessed = True

    # ------------------------------------------------------------------ helpers
    def lap_time(self) -> float | None:
        """T's usual lap: the median of its recent laps, pit laps left out; else from its class's speed."""
        laps = [x[1] for x in (self.t_laps[1:] if len(self.t_laps) > 1 else self.t_laps)[-9:]]
        if len(laps) >= 1:
            m = statistics.median(laps)
            good = [x for x in laps if x < 1.15 * m]
            if good:
                return statistics.median(good)
        me = self.cars.get(self.target)
        r = self.cls_typ.get(me.cls) if me else None
        return 1.0 / r if r else None

    def class_pos(self, idx) -> int | None:
        me = self.cars.get(idx)
        if me is None or me.dist is None:
            return None
        return 1 + sum(1 for j, c in self.cars.items()
                       if j != idx and c.cls == me.cls and c.dist is not None and c.dist > me.dist + 1e-6)

    def lap_no(self) -> int | None:
        me = self.cars.get(self.target)
        return int(me.lc) + 1 if me and me.lc is not None else None

    def _ratio(self, c) -> float | None:
        r = self.cls_rate.get(c.cls)
        if c.rate is None or not r:
            return None
        return c.rate / r

    def _add(self, kind, t, segs, score, label, cars=(), **extra):
        m = {"kind": kind, "session": self.race_session, "t": round(t, 2),
             "segs": [[round(max(0.0, a), 2), round(b, 2)] for a, b in segs], "score": round(score, 1),
             "label": label, "lap": extra.pop("lap", self.lap_no()), "cars": list(cars)}
        m.update(extra)
        self.moments.append(m)
        return m

    # ------------------------------------------------------------------ detectors
    def _detect(self, t, dt):
        me = self.cars.get(self.target)
        if me is None:
            return
        lap_t = self.lap_time()
        self._passes(t, me)
        self._confirm(t)
        if lap_t:
            self._battles(t, me, lap_t)
        self._own_trouble(t, me)
        self._others_trouble(t, me, lap_t)
        self._pits(t, me, lap_t)

    def _passes(self, t, me):
        if me.surf in (NOT_IN_WORLD,) or me.pit or me.dist is None:
            self._rel.clear()
            return
        for j, c in self.cars.items():
            if j == self.target or c.dist is None:
                continue
            if c.surf == NOT_IN_WORLD or c.pit or c.surf in (IN_STALL, APPROACHING_PITS):
                self._rel.pop(j, None)
                continue
            diff = c.dist - me.dist
            rel = diff - round(diff)                 # where it is on track: + ahead of T, - behind
            prev = self._rel.get(j)
            self._rel[j] = rel
            if prev is None or abs(rel) > NEAR_LAP or abs(prev) > NEAR_LAP:
                continue
            if (prev > 0) == (rel > 0) or rel == 0 or prev == 0:
                continue
            gain = rel < 0                           # it was ahead and is now behind: T went by
            # a flip back within HOLD_S cancels the one waiting (side by side)
            back = next((p for p in self._pending if p["idx"] == j), None)
            if back is not None:
                self._pending.remove(back)
                continue
            laps_apart = round(diff)                 # 0: same lap (a position); -1 T laps it; +1 T unlaps itself
            ratio = self._ratio(c)
            self._pending.append({"idx": j, "t": t, "gain": gain, "lapsApart": laps_apart,
                                  "same": c.cls == me.cls, "posBefore": self._pos_before(gain),
                                  "slowCar": ratio is not None and ratio < 0.6, "offCar": c.surf == OFF_TRACK})

    def _pos_before(self, gain):
        p = self.class_pos(self.target)
        if p is None:
            return None
        return p + 1 if gain else p - 1               # the place T held before the swap

    def _confirm(self, t):
        for p in list(self._pending):
            if t - p["t"] < HOLD_S:
                continue
            self._pending.remove(p)
            self._score_pass(p)

    def _score_pass(self, p):
        j, t = p["idx"], p["t"]
        lap = self.lap_no()
        if p["slowCar"]:
            return                                    # going by a car that has stopped or spun is not a pass
        position = p["same"] and p["lapsApart"] == 0
        if not position:
            score = 10 if p["gain"] else 6
            label = ("Through traffic" if p["gain"] else "Let a faster car by") if not p["same"] else \
                    ("Lapped a car" if p["gain"] else "Lapped")
            if p["offCar"]:
                score += 6
            self._add("traffic", t, [(t - 8, t + 4)], score, label, [j], gain=p["gain"])
            return
        now = self.class_pos(self.target)
        if p["gain"]:
            score, pos = 50, now
            bonus = 45 if pos == 1 else 25 if pos and pos <= 3 else 10 if pos and pos <= 10 else 0
            label = "Takes the class lead" if pos == 1 else "Pass for %s" % _ord(pos) if pos else "Pass"
        else:
            score, pos = 35, p["posBefore"]
            bonus = (45 if pos == 1 else 25 if pos and pos <= 3 else 10 if pos and pos <= 10 else 0) * 0.6
            label = "Loses the class lead" if pos == 1 else "Loses %s" % _ord(pos) if pos else "Loses a place"
        if p["offCar"]:
            label += " (the other car went off)"
        score += bonus
        b = self._battle.get(j)
        if b is not None:
            score += min(20.0, (t - b["start"]) / 12.0)
        m = self._add("pass" if p["gain"] else "lost", t, [(t - 10, t + 5)], score, label, [j],
                      classPos=pos, lap=lap)
        # back and forth with the same car inside 45 s: both count more
        for q in self._pass_log:
            if q["cars"] == [j] and t - q["t"] <= 45:
                q["score"] = round(q["score"] + 15, 1)
                m["score"] = round(m["score"] + 15, 1)
                m["label"] += " - back and forth"
        self._pass_log.append(m)
        if b is not None:
            b["passes"].append(m)

    def _battles(self, t, me, lap_t):
        if me.pit or me.surf == NOT_IN_WORLD:
            for j in list(self._battle):
                self._end_battle(j, t)
            return
        for j, c in self.cars.items():
            if j == self.target or c.cls != me.cls or c.dist is None:
                continue
            diff = c.dist - me.dist
            close = (round(diff) == 0 and not c.pit and c.surf != NOT_IN_WORLD
                     and abs(diff) * lap_t <= BATTLE_GAP_S)
            b = self._battle.get(j)
            if close:
                gap = abs(diff) * lap_t
                if b is None:
                    b = self._battle[j] = {"start": t, "last": t, "min": gap, "minT": t, "passes": [],
                                           "pos": self.class_pos(self.target), "lap": self.lap_no()}
                b["last"] = t
                if gap < b["min"]:
                    b["min"], b["minT"] = gap, t
            elif b is not None:
                far = abs(diff) * lap_t > 1.5 or c.pit or c.surf == NOT_IN_WORLD or round(diff) != 0
                if far and t - b["last"] > BATTLE_END_S:
                    self._end_battle(j, t)

    def _end_battle(self, j, t):
        b = self._battle.pop(j, None)
        if b is None:
            return
        dur = b["last"] - b["start"]
        if dur < BATTLE_MIN_S:
            return
        pos = min([x for x in (b["pos"], self.class_pos(self.target)) if x], default=None)
        score = 22 + min(25.0, dur / 12.0) + (10 if b["min"] < 0.3 else 0)
        score += 25 if pos == 1 else 15 if pos and pos <= 3 else 0
        if b["passes"]:
            key = max(b["passes"], key=lambda m: m["score"])["t"]
            seg = (key - 25, key + 5)
        else:
            mid = min(max(b["minT"], b["start"] + 12.5), b["last"] - 12.5)
            seg = (mid - 12.5, mid + 12.5)
        mins = dur / 60.0
        what = ("%.0f min" % mins) if mins >= 1.5 else ("%.0f s" % dur)
        lead = " for the class lead" if pos == 1 else (" for %s" % _ord(pos) if pos else "")
        self._add("battle", seg[0], [seg], score, "Battle%s (%s)" % (lead, what), [j], lap=b["lap"],
                  duration=round(dur, 1), closest=round(b["min"], 2), classPos=pos)

    def _own_trouble(self, t, me):
        # off track
        if me.surf == OFF_TRACK and not me.pit:
            if self._off is None:
                self._off = {"t": t, "lap": self.lap_no()}
            self._off["end"] = t
        elif self._off is not None:
            o, self._off = self._off, None
            dur = o["end"] - o["t"] + 0.1
            if dur >= OFF_MIN_S:
                spun = self._slow is not None or (self.moments and self.moments[-1]["kind"] == "spin"
                                                  and abs(self.moments[-1]["t"] - o["t"]) < 8)
                if not spun:
                    self._add("off", o["t"], [(o["t"] - 7, o["end"] + 6)], 40 + min(25.0, dur * 5),
                              "Off track" if dur < 3 else "Big trip off track", lap=o["lap"], duration=round(dur, 1))
        # far slower than its class (a spin, contact, a stop on track)
        ratio = self._ratio(me)
        slow = ratio is not None and ratio < SLOW_RATIO and not me.pit and me.surf in (ON_TRACK, OFF_TRACK)
        if slow:
            if self._slow is None:
                self._slow = {"t": t, "off": me.surf == OFF_TRACK, "lap": self.lap_no()}
            self._slow["end"] = t
            self._slow["off"] = self._slow["off"] or me.surf == OFF_TRACK
        elif self._slow is not None:
            s, self._slow = self._slow, None
            dur = s["end"] - s["t"]
            if dur >= SLOW_MIN_S:
                # an off track just before it belongs to this moment
                for m in reversed(self.moments[-3:]):
                    if m["kind"] == "off" and s["t"] - 10 <= m["t"] <= s["end"]:
                        self.moments.remove(m)
                        s["off"] = True
                        s["t"] = min(s["t"], m["t"])
                start = s["t"] - 8
                self._add("spin", s["t"], [(start, min(s["end"] + 6, start + 30))], 60 + (10 if s["off"] else 0),
                          "Off and spinning" if s["off"] else "Big moment", lap=s["lap"], duration=round(dur, 1))
        # gone from the world mid-race outside the pits
        if me.surf == NOT_IN_WORLD and self._gone is None and self.checkered is None:
            self._gone = t
            if self.green is not None and t - self.green > 30:
                self._add("towed", t, [(t - 15, t + 2)], 45, "Out on track - towed or reset")
        elif me.surf != NOT_IN_WORLD:
            self._gone = None

    def _others_trouble(self, t, me, lap_t):
        if not lap_t or me.surf == NOT_IN_WORLD or me.pit:
            return
        for j, c in self.cars.items():
            if j == self.target or c.dist is None or c.pit or c.surf == NOT_IN_WORLD:
                self._other_off.pop(j, None)
                self._other_slow.pop(j, None)
                continue
            rel = (c.dist - me.dist) - round(c.dist - me.dist)
            near_s = abs(rel) * lap_t
            ratio = self._ratio(c)
            trouble = c.surf == OFF_TRACK or (ratio is not None and ratio < SLOW_RATIO)
            if not trouble:
                self._other_off.pop(j, None)
                continue
            since = self._other_off.setdefault(j, t)
            if t - since < OFF_MIN_S or near_s > DRAMA_NEAR_S:
                continue
            if t - self._drama_t.get(j, -1e9) < 30 or t - self._last_drama < 10:
                continue
            self._drama_t[j] = self._last_drama = t
            where = "ahead" if rel > 0 else "behind"
            self._add("drama", t, [(t - 8, t + 6)], 25 + (15 if near_s <= 1.0 and rel > 0 else 0),
                      "Trouble just %s" % where, [j])

    def _pits(self, t, me, lap_t):
        on = me.pit or me.surf in (IN_STALL,)
        if on and self._pit is None:
            self._pit = {"in": t, "pos": self.class_pos(self.target), "lap": self.lap_no()}
        if self._pit is not None:
            p = self._pit
            if me.surf == IN_STALL:
                p.setdefault("stall", t)
                p["stallEnd"] = t
            if not on and "out" not in p:
                p["out"] = t
                p["due"] = t + (lap_t or 90.0) * 0.5     # positions settle half a lap after the stop
            if "due" in p and t >= p["due"]:
                self._pit = None
                after = self.class_pos(self.target)
                if "stall" in p:
                    segs = [(p["stall"] - 6, p["stall"] + 5), (p["stallEnd"] - 3, p["stallEnd"] + 6)]
                    if segs[1][0] - segs[0][1] < 4:
                        segs = [(segs[0][0], segs[1][1])]
                else:
                    segs = [(p["in"] - 3, min(p["out"] + 5, p["in"] + 25))]
                if p["out"] - p["in"] < 4:
                    return                             # a blip of the pit flag, not a stop
                label = "Pit stop"
                if p["pos"] and after:
                    label += " - %s to %s" % (_ord(p["pos"]), _ord(after)) if p["pos"] != after else \
                        " - holds %s" % _ord(after)
                self._add("pit", p["in"], segs, 20 + (5 if p["pos"] and after and after < p["pos"] else 0), label,
                          lap=p["lap"], stopS=round(p["stallEnd"] - p["stall"], 1) if "stall" in p else None)

    # ------------------------------------------------------------------ the end
    def finish(self) -> dict:
        t_end = self.t
        for j in list(self._battle):
            self._end_battle(j, t_end or 0.0)
        for p in list(self._pending):
            self._pending.remove(p)
            self._score_pass(p)
        # T taking the flag
        fin_t = None
        if self.results and self.crossings:
            fin_t = next((t for t, lc in self.crossings if lc >= self.results["laps"]), None)
        if fin_t is None and self.checkered is not None:
            fin_t = next((t for t, lc in self.crossings if t >= self.checkered), None)
        guessed_finish = False
        if fin_t is None and self.crossings:
            fin_t, guessed_finish = self.crossings[-1][0], True
        cls_pos = (self.results or {}).get("classPos")
        if cls_pos is None and fin_t is not None:
            cls_pos = self.class_pos(self.target)
        moments = [m for m in self.moments if fin_t is None or m["t"] <= fin_t + 5]
        if self.green is not None:
            moments.insert(0, {"kind": "start", "session": self.race_session, "t": round(self.green, 2),
                               "segs": [[round(max(0.0, self.green - 6), 2), round(self.green + 40, 2)]],
                               "score": 100.0, "label": "The start" + (" (found by pace)" if self.green_guessed else ""),
                               "lap": 1, "cars": [], "mandatory": True})
        if fin_t is not None:
            lab = "The finish" + (" - %s in class" % _ord(cls_pos) if cls_pos else "")
            moments.append({"kind": "finish", "session": self.race_session, "t": round(fin_t, 2),
                            "segs": [[round(fin_t - 20, 2), round(fin_t + 8, 2)]], "score": 100.0,
                            "label": lab + (" (last line crossing seen)" if guessed_finish else ""),
                            "lap": (self.results or {}).get("laps"), "cars": [], "mandatory": True})
        # T's best lap, and whether it was the class's fastest of the race
        me = self.cars.get(self.target)
        if self.t_laps:
            best = min(self.t_laps, key=lambda x: x[1])
            t_b, lt, lap = best
            fastest_cls = me is not None and self.cls_best.get(me.cls) is not None and lt <= self.cls_best[me.cls]
            lead = min(25.0, 0.3 * lt)
            moments.append({"kind": "fastest", "session": self.race_session, "t": round(t_b, 2),
                            "segs": [[round(t_b - lead, 2), round(t_b + 3, 2)]],
                            "score": 40.0 if fastest_cls else 18.0,
                            "label": ("Fastest lap of the race in class" if fastest_cls else "Best lap") +
                                     " - %s" % fmt_lap(lt), "lap": lap, "cars": [], "lapTime": lt})
        # the last part of the race counts more
        if self.green is not None and fin_t is not None and fin_t > self.green:
            cut = fin_t - LATE_SHARE * (fin_t - self.green)
            for m in moments:
                if not m.get("mandatory") and m["t"] >= cut:
                    m["score"] = round(m["score"] * LATE_BONUS, 1)
                    m["late"] = True
        moments.sort(key=lambda m: m["t"])
        for n, m in enumerate(moments):
            m["id"] = n
            m.setdefault("mandatory", False)
        return {"target": self.target, "session": self.race_session, "green": self.green, "finish": fin_t,
                "classPos": cls_pos, "laps": (self.results or {}).get("laps") or (self.crossings[-1][1] if self.crossings else None),
                "track": self.track, "event": self.event, "lapTime": self.lap_time(), "moments": moments}


def fmt_lap(s) -> str:
    if not isinstance(s, (int, float)) or s <= 0:
        return ""
    m, sec = divmod(s, 60)
    return "%d:%06.3f" % (m, sec) if m else "%.3f" % sec


# ---------------------------------------------------------------------------- reading a capture
def tape_time(get):
    """Where on the tape this frame is: the replay's playhead while a replay plays, else the session clock."""
    if get("IsReplayPlaying"):
        s, t = get("ReplaySessionNum"), get("ReplaySessionTime")
        if isinstance(t, (int, float)):
            return (s if isinstance(s, int) else get("SessionNum")), float(t)
    return get("SessionNum"), get("SessionTime")


def analyze_capture(path, target, race_session=None, progress=None) -> dict:
    """A scan file or any suite Recording (.jsonl.gz) through the Analyzer."""
    from pitwall.recorder import iter_recording
    a = Analyzer(target, race_session)
    n = 0
    for rec in iter_recording(path):
        kind = rec.get("type")
        if kind == "session":
            a.session(rec.get("info") or {})
        elif kind == "tick":
            v = rec.get("vars") or {}
            s, t = tape_time(v.get)
            a.feed(s, t, v.get)
            n += 1
            if progress is not None and n % 2000 == 0:
                progress(t)
    return a.finish()


def capture_field(path) -> dict:
    """The field and the race sessions named in a capture's session info (the last one in the file)."""
    from pitwall.recorder import iter_recording
    info = {}
    for rec in iter_recording(path):
        if rec.get("type") == "session":
            info = rec.get("info") or info
    return {"field": field(info.get("DriverInfo")), "races": race_sessions(info.get("SessionInfo")), "info": info}
