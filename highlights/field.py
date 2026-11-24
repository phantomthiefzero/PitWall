"""The whole race's moments (Highlights' "whole race" mode): the same scan, every car at once.

Built on moments.Analyzer (its car tracking: race distance unwrapped at the line, each class's speed), with field-wide
detectors in place of the one-car ones. Each moment names its "focus", the car the cameras follow (the passer, the car
that began a crash, the winner), and its other cars, so the camera work (shots.py) tells it as it does for one car.

    pass        two cars of one class on the same lap swap places on track and it stands HOLD_S. Found between
                neighbours on track only (cars sorted by lap fraction each frame), so a 60-car field costs ~60 pairs.
                Scored by the place it was for: the class lead most, then the podium, the top ten.
    battle      two cars of one class on the same lap within BATTLE_GAP_S for BATTLE_MIN_S.
    crash       one car far slower than its class or off track (a spin, an off, a stop); others in trouble within
                CRASH_JOIN_S and CRASH_NEAR of it join it (a multi-car crash). From LEAD_UP_S before to the last car
                on its way again.
    start       the green flag (always in).
    finish      each class's winner taking the flag, from the replay's own final results (always in); a second place
                within CLOSE_FINISH_S makes it a close finish.
    fastest     each class's fastest lap of the race.

Traffic (another class, a lap apart) is not a story of the whole race and is left out.
"""
from __future__ import annotations

from .moments import (Analyzer, class_name, HOLD_S, NEAR_LAP, BATTLE_GAP_S, BATTLE_END_S, BATTLE_MIN_S, SLOW_RATIO,
                      OFF_MIN_S, SLOW_MIN_S, LEAD_UP_S, LATE_SHARE, LATE_BONUS, AFTER_FINISH_S, START_S,
                      NOT_IN_WORLD, OFF_TRACK, IN_STALL, APPROACHING_PITS, _num, fmt_lap, race_length_guess)

CRASH_JOIN_S = 4.0
CRASH_NEAR = 0.03
CRASH_MAX_S = 36.0
CLOSE_FINISH_S = 1.0


class FieldAnalyzer(Analyzer):
    mode = "race"

    def __init__(self, race_session=None):
        super().__init__(-1, race_session)       # no car is "the" car: -1 is no car index
        self.cls_names: dict = {}
        self.cross_all: dict[int, list] = {}
        self.best_lap: dict = {}                  # class id -> (t, lap time, car, lap)
        self.results_all: list[dict] = []
        self.field_n = 0
        self._pair_rel: dict = {}
        self._pair_pending: dict = {}
        self._pair_battle: dict = {}
        self._pair_passes: list[dict] = []
        self._trouble: dict[int, dict] = {}
        self._crashes: list[dict] = []
        self._crash_seen: dict[int, float] = {}
        self.start_focus = None

    # ------------------------------------------------------------------ session info
    def session(self, info):
        super().session(info)
        drivers = ((info or {}).get("DriverInfo") or {}).get("Drivers") or []
        real = [d for d in drivers if isinstance(d, dict) and d.get("CarIsPaceCar") not in (1, True)
                and d.get("IsSpectator") not in (1, True) and isinstance(d.get("CarIdx"), int)]
        if real:
            self.field_n = len(real)
            self.names = getattr(self, "names", {})
            for d in real:
                if d.get("CarClassID") is not None:
                    self.cls_names[d["CarClassID"]] = class_name(d)
                self.names[d["CarIdx"]] = d.get("TeamName") or d.get("UserName") or ""
        for s in (((info or {}).get("SessionInfo") or {}).get("Sessions") or []):
            if isinstance(s, dict) and s.get("SessionNum") == self.race_session and s.get("ResultsPositions"):
                self.results_all = [r for r in s["ResultsPositions"] if isinstance(r, dict)]

    def _crossed(self, i, t, lc):
        self.cross_all.setdefault(i, []).append((t, lc))

    def _lapped(self, i, t, lt, lc):
        c = self.cars.get(i)
        if c is None or c.cls is None:
            return
        b = self.best_lap.get(c.cls)
        if b is None or lt < b[1]:
            self.best_lap[c.cls] = (t, lt, i, lc)

    def scan_done(self, t) -> bool:
        """Every class winner (by the replay's own results) has taken the flag, and AFTER_FINISH_S went by."""
        wins = self._winners()
        if not wins:
            return False
        last = []
        for w in wins:
            got = next((tt for tt, lc in self.cross_all.get(w["idx"], []) if lc >= w["laps"]), None)
            if got is None:
                return False
            last.append(got)
        return t > max(last) + AFTER_FINISH_S

    # ------------------------------------------------------------------ helpers
    def lap_s(self, cls) -> float | None:
        r = self.cls_typ.get(cls) or self.cls_rate.get(cls)
        return 1.0 / r if r else None

    def num(self, i) -> str:
        return "#%s" % self.numbers.get(i, i)

    def class_label(self, i) -> str:
        """The car's class name, when the race has more than one class (else nothing: "the lead", "P3")."""
        c = self.cars.get(i)
        if c is None or len({v for v in self.cls_names.values()}) < 2:
            return ""
        return str(self.cls_names.get(c.cls, ""))

    def _lap_of(self, i):
        c = self.cars.get(i)
        return int(c.lc) + 1 if c is not None and c.lc is not None else None

    def _winners(self) -> list[dict]:
        """Each class's winner: [{"idx", "laps", "cls"}], from the replay's own final results; without them (a
        recording, a replay saved mid-race), each class's first car across the line after the checkered flag."""
        out = []
        for r in self.results_all:
            if _num(r.get("ClassPosition")) == 0 and isinstance(r.get("CarIdx"), int) and _num(r.get("LapsComplete")):
                out.append({"idx": r["CarIdx"], "laps": int(r["LapsComplete"]), "cls": r.get("CarClassID")})
        if out or self.checkered is None:
            return out
        first: dict = {}
        for i, cr in self.cross_all.items():
            c = self.cars.get(i)
            got = next(((t, lc) for t, lc in cr if t >= self.checkered), None)
            if c is None or got is None:
                continue
            if c.cls not in first or got[0] < first[c.cls][0]:
                first[c.cls] = (got[0], got[1], i)
        return [{"idx": i, "laps": lc, "cls": k} for k, (t, lc, i) in first.items()]

    # ------------------------------------------------------------------ detectors
    def _detect(self, t, dt):
        if self.start_focus is None:
            lead = [(c.dist, i) for i, c in self.cars.items() if c.dist is not None and c.surf != NOT_IN_WORLD]
            if lead:
                self.start_focus = max(lead)[1]
        on = [(c.pct, i, c) for i, c in self.cars.items()
              if c.dist is not None and c.pct is not None and not c.pit
              and c.surf not in (NOT_IN_WORLD, IN_STALL, APPROACHING_PITS)]
        on.sort(key=lambda x: x[0])
        close = set()
        n = len(on)
        for k in range(n if n > 2 else n - 1):
            pa, ia, ca = on[k]
            pb, ib, cb = on[(k + 1) % n]
            if ia == ib or (pb - pa) % 1.0 > NEAR_LAP:
                continue
            key = (min(ia, ib), max(ia, ib))
            close.add(key)
            self._pair(t, key)
        for key in list(self._pair_rel):
            if key not in close:
                self._pair_rel.pop(key, None)
        self._confirm_pairs(t)
        self._battles_all(t, close)
        self._troubles(t)
        self._spectacle(t)

    def _pair(self, t, key):
        i, j = key
        ci, cj = self.cars[i], self.cars[j]
        d = cj.dist - ci.dist
        rel = d - round(d)                    # + : j is ahead of i on track
        if rel == 0:
            return                            # level this frame: who leads is decided by the frames either side
        prev = self._pair_rel.get(key)
        self._pair_rel[key] = rel
        if prev is None or (prev > 0) == (rel > 0):
            return
        if key in self._pair_pending:         # flipped back inside HOLD_S: side by side
            self._pair_pending.pop(key)
            return
        passer, passed = (i, j) if rel < 0 else (j, i)
        if ci.cls != cj.cls or round(d) != 0:
            self._note_traffic(passer, passed, t)
            return                            # another class or a lap apart: traffic (only slicing through counts)
        r = self._ratio(self.cars[passed])
        if r is not None and r < 0.6:
            return                            # going by a car that has stopped is not a pass
        self._pair_pending[key] = {"t": t, "passer": passer, "passed": passed,
                                   "off": self.cars[passed].surf == OFF_TRACK}

    def _confirm_pairs(self, t):
        for key, p in list(self._pair_pending.items()):
            if t - p["t"] < HOLD_S:
                continue
            self._pair_pending.pop(key)
            pos = self.class_pos(p["passer"])
            if pos is None:
                continue
            cls = self.class_label(p["passer"])
            what = (" the %s lead" % cls if cls else " the lead") if pos == 1 else \
                (" P%d%s" % (pos, (" in " + cls) if cls else ""))
            score = 30 + (60 if pos == 1 else 30 if pos <= 3 else 10 if pos <= 10 else 0)
            label = "%s %s %s for%s" % (self.num(p["passer"]), "takes" if pos == 1 else "passes",
                                       ("" if pos == 1 else self.num(p["passed"])), what)
            if pos == 1:
                label = "%s takes%s from %s" % (self.num(p["passer"]), what, self.num(p["passed"]))
            if p["off"]:
                label += " as %s goes off" % self.num(p["passed"])
            b = self._pair_battle.get(key)
            if b is not None:
                score += min(20.0, (t - b["start"]) / 12.0)
            m = self._add("pass", p["t"], [(p["t"] - 10, p["t"] + 5)], score, label, [p["passed"]],
                          lap=self._lap_of(p["passer"]), focus=p["passer"], otherAheadBefore=True, classPos=pos,
                          at=self.cars[p["passer"]].pct)
            for q in self._pair_passes:
                if q["pair"] == key and p["t"] - q["t"] <= 45:
                    q["score"] = round(q["score"] + 15, 1)
                    m["score"] = round(m["score"] + 15, 1)
                    if not m["label"].endswith(" - back and forth"):
                        m["label"] += " - back and forth"
            m["pair"] = key
            self._pair_passes.append(m)
            self._note_pass(p["passer"], pos, p["t"])
            if b is not None:
                b["passes"].append(m)

    def _battles_all(self, t, close):
        for key in close:
            i, j = key
            ci, cj = self.cars[i], self.cars[j]
            if ci.cls != cj.cls:
                continue
            d = cj.dist - ci.dist
            lap_t = self.lap_s(ci.cls)
            if not lap_t or round(d) != 0:
                continue
            gap = abs(d) * lap_t
            b = self._pair_battle.get(key)
            if gap <= BATTLE_GAP_S:
                if b is None:
                    front, back = (j, i) if d > 0 else (i, j)
                    b = self._pair_battle[key] = {"start": t, "last": t, "min": gap, "minT": t, "passes": [],
                                                  "front": front, "back": back, "lap": self._lap_of(front),
                                                  "pos": self.class_pos(front)}
                b["last"] = t
                if gap < b["min"]:
                    b["min"], b["minT"] = gap, t
        for key, b in list(self._pair_battle.items()):
            if key not in close and t - b["last"] > BATTLE_END_S:
                self._end_pair_battle(key, t)
            elif key in close and t - b["last"] > BATTLE_END_S:
                self._end_pair_battle(key, t)

    def _end_pair_battle(self, key, t):
        b = self._pair_battle.pop(key, None)
        if b is None:
            return
        dur = b["last"] - b["start"]
        if dur < BATTLE_MIN_S:
            return
        # the place fought for: the better of the two cars' places as it began and now (a pass swaps them)
        pos = min([p for p in (b["pos"], self.class_pos(b["front"]), self.class_pos(b["back"])) if p] or [None]) \
            if any(p for p in (b["pos"], self.class_pos(b["front"]), self.class_pos(b["back"]))) else None
        cls = self.class_label(b["front"])
        score = 15 + min(25.0, dur / 12.0) + (10 if b["min"] < 0.3 else 0) + \
            (45 if pos == 1 else 25 if pos and pos <= 3 else 5 if pos and pos <= 10 else 0)
        if b["passes"]:
            key_t = max(b["passes"], key=lambda m: m["score"])["t"]
            seg = (key_t - 25, key_t + 5)
        else:
            mid = min(max(b["minT"], b["start"] + 12.5), b["last"] - 12.5)
            seg, key_t = (mid - 12.5, mid + 12.5), b["minT"]
        what = ("%.0f min" % (dur / 60)) if dur >= 90 else ("%.0f s" % dur)
        for_what = (" for the %s lead" % cls if cls else " for the lead") if pos == 1 else \
            (" for P%d%s" % (pos, (" in " + cls) if cls else "") if pos else "")
        m = self._add("battle", seg[0], [seg], score, "%s and %s battle%s (%s)" % (
            self.num(b["back"]), self.num(b["front"]), for_what, what), [b["front"]], lap=b["lap"],
            focus=b["back"], otherAheadBefore=True, duration=round(dur, 1), closest=round(b["min"], 2), classPos=pos)
        m["key"] = round(min(max(key_t, seg[0]), seg[1]), 2)

    def _troubles(self, t):
        for i, c in self.cars.items():
            tr = self._trouble.get(i)
            on = c.dist is not None and not c.pit and c.surf not in (NOT_IN_WORLD, IN_STALL, APPROACHING_PITS)
            r = self._ratio(c) if on else None
            bad = on and (c.surf == OFF_TRACK or (r is not None and r < SLOW_RATIO))
            if bad:
                if tr is None:
                    tr = self._trouble[i] = {"t": t, "end": t, "off": False, "slow": False, "at": c.pct}
                    self._join_crash(i, tr, t)
                tr["end"] = t
                tr["off"] = tr["off"] or c.surf == OFF_TRACK
                tr["slow"] = tr["slow"] or (r is not None and r < SLOW_RATIO)
            elif tr is not None and t - tr["end"] > 1.0:
                self._trouble.pop(i)
                tr["done"] = True
        for cr in list(self._crashes):
            if all(m.get("done") for m in cr["cars"].values()) and t - max(m["end"] for m in cr["cars"].values()) > 2:
                self._crashes.remove(cr)
                self._emit_crash(cr)

    def _join_crash(self, i, tr, t):
        if t - self._crash_seen.get(i, -1e9) < 20:
            tr["skip"] = True
        for cr in self._crashes:
            if t - cr["t"] <= CRASH_JOIN_S and abs(((tr["at"] or 0) - (cr["at"] or 0) + 0.5) % 1.0 - 0.5) <= CRASH_NEAR:
                cr["cars"][i] = tr
                return
        self._crashes.append({"t": t, "at": tr["at"], "first": i, "cars": {i: tr}, "lap": self._lap_of(i)})

    def _emit_crash(self, cr):
        cars = {i: m for i, m in cr["cars"].items() if not m.get("skip")}
        if not cars:
            return
        real = {i: m for i, m in cars.items()
                if (m["slow"] and m["end"] - m["t"] >= SLOW_MIN_S) or (m["off"] and m["end"] - m["t"] >= OFF_MIN_S)}
        if not real:
            return
        for i in real:
            self._crash_seen[i] = cr["t"]
        first = cr["first"] if cr["first"] in real else next(iter(real))
        others = [i for i in real if i != first]
        end = max(m["end"] for m in real.values())
        start = cr["t"] - LEAD_UP_S
        end = min(end + 5.0, start + CRASH_MAX_S)
        top = min([p for p in (self.class_pos(i) for i in real) if p] or [99])
        names = [self.num(i) for i in [first] + others]
        if len(real) >= 2:
            label = "Crash - %s" % (", ".join(names[:-1]) + " and " + names[-1])
            score = 50 + 15 * (len(real) - 1)
        elif real[first]["slow"]:
            label, score = "%s spins" % names[0], 35
        else:
            label, score = "%s goes off" % names[0], 18
        if top == 1:
            label += " (the %s leader)" % self.class_label(first) if self.class_label(first) else " (the leader)"
        score += 25 if top == 1 else 12 if top <= 3 else 0
        kind = "crash" if len(real) >= 2 else "spin" if real[first]["slow"] else "off"
        m = self._add(kind, cr["t"], [(start, end)], score, label, others, lap=cr["lap"], focus=first,
                      at=cr["at"], carsInvolved=len(real))
        m["key"] = round(cr["t"], 2)

    # ------------------------------------------------------------------ the end
    def finish(self) -> dict:
        t_end = self.t or 0.0
        for key in list(self._pair_battle):
            self._end_pair_battle(key, t_end)
        self._confirm_pairs(t_end + HOLD_S)
        for i, tr in list(self._trouble.items()):
            tr["done"] = True
        for cr in list(self._crashes):
            self._emit_crash(cr)
        self._crashes = []
        self._spec_finish(t_end)
        moments = list(self.moments)
        winners = []
        if self.green is not None:
            moments.insert(0, {"kind": "start", "session": self.race_session, "t": round(self.green, 2),
                               "segs": [[round(max(0.0, self.green - 6), 2), round(self.green + START_S, 2)]],
                               "score": 100.0, "label": "The start", "lap": 1, "cars": [], "mandatory": True,
                               "focus": self.start_focus})
        last_finish = None
        for w in self._winners():
            got = next((tt for tt, lc in self.cross_all.get(w["idx"], []) if lc >= w["laps"]), None)
            cls = str(self.cls_names.get(w["cls"], "") or "")
            winners.append({"idx": w["idx"], "number": self.numbers.get(w["idx"], ""), "cls": cls,
                            "name": getattr(self, "names", {}).get(w["idx"], "")})
            if got is None:
                continue
            last_finish = max(last_finish or got, got)
            second = next((r for r in self.results_all if _num(r.get("ClassPosition")) == 1
                           and r.get("CarClassID") == w["cls"]), None)
            gap_s, close_idx = None, []
            if second is not None:
                t2 = next((tt for tt, lc in self.cross_all.get(second["CarIdx"], []) if lc >= w["laps"]), None)
                if t2 is not None and 0 <= t2 - got <= CLOSE_FINISH_S:
                    gap_s, close_idx = t2 - got, [second["CarIdx"]]
            label = "%s wins%s" % (self.num(w["idx"]), (" " + cls) if cls and len(self._winners()) > 1 else "")
            if gap_s is not None:
                label += " by %.1f s" % gap_s
            moments.append({"kind": "finish", "session": self.race_session, "t": round(got, 2),
                            "segs": [[round(got - 20, 2), round(got + 8, 2)]], "score": 100.0 if gap_s is None else 120.0,
                            "label": label, "lap": w["laps"], "cars": close_idx, "mandatory": True,
                            "focus": w["idx"], "key": round(got, 2)})
        if last_finish is None and self.checkered is not None:
            last_finish = self.checkered
        for cls, (tb, lt, i, lap) in self.best_lap.items():
            if last_finish is not None and tb > last_finish + 5:
                continue
            lead = min(25.0, 0.3 * lt)
            name = str(self.cls_names.get(cls, "") or "")
            moments.append({"kind": "fastest", "session": self.race_session, "t": round(tb, 2),
                            "segs": [[round(tb - lead, 2), round(tb + 3, 2)]], "score": 22.0,
                            "label": "%s sets the fastest lap%s - %s" % (self.num(i), (" in " + name) if name and
                                                                         len(self.best_lap) > 1 else "",
                                                                         fmt_lap(lt)),
                            "lap": lap, "cars": [], "focus": i, "lapTime": lt})
        moments = [m for m in moments if last_finish is None or m["t"] <= last_finish + 5]
        # each class's last lap: from its winner's line crossing before the flag to the flag
        windows = []
        for w in self._winners():
            cr = self.cross_all.get(w["idx"], [])
            got = next((tt for tt, lc in cr if lc >= w["laps"]), None)
            before = next((tt for tt, lc in cr if lc >= w["laps"] - 1), None)
            if got is not None and before is not None and before < got:
                c = self.cars.get(w["idx"])
                windows.append((before, got + 1, c.cls if c is not None else None))
        self.last_lap(moments, windows)
        if self.green is not None and last_finish is not None and last_finish > self.green:
            cut = last_finish - LATE_SHARE * (last_finish - self.green)
            for m in moments:
                if not m.get("mandatory") and m["t"] >= cut:
                    m["score"] = round(m["score"] * LATE_BONUS, 1)
                    m["late"] = True
        moments.sort(key=lambda m: m["t"])
        for n, m in enumerate(moments):
            m["id"] = n
            m.setdefault("mandatory", False)
            m.pop("pair", None)
        classes = sorted({str(v) for v in self.cls_names.values() if v})
        climb = self.climber()
        if climb is not None:
            climb.update(number=self.numbers.get(climb["idx"], ""), name=getattr(self, "names", {}).get(climb["idx"], ""),
                         cls=self.class_label(climb["idx"]))
        return {"mode": "race", "target": None, "session": self.race_session, "green": self.green,
                "finish": last_finish, "laps": max([w_["laps"] for w_ in self._winners()] or [0]) or None,
                "track": self.track, "event": self.event, "lapTime": None, "moments": moments, "path": [],
                "trackKey": self.track_key, "numbers": self.numbers, "cameraInfo": self.camera_info,
                "raceS": race_length_guess(self.session_info, self.race_session), "winners": winners, "classes": classes, "fieldSize": self.field_n,
                "stints": [], "drivers": [], "team": "", "number": "", "startPos": None, "classPos": None,
                "frameMap": self.frame_map,
                "climber": climb}
