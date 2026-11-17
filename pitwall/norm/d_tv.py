"""Race TV extras: pit stops on air, closing in, race pace, lap chart, per-car extras."""
from __future__ import annotations

import math
from .race import race_laps_left


class RaceTV:
    # ---- what a TV race broadcast shows, from what we measure ------------------
    PIT_SHOW_AFTER_S = 15.0     # a finished stop stays in the list this long (its time, where it came out)
    PIT_LONG_S = 180.0          # longer in the lane than this is not a pit stop to show (repairs, retired, garage)
    RECENT_PACE_LAPS = 5
    CLOSING_MIN_S_PER_LAP = 0.1
    CLOSING_MAX_GAP_S = 8.0
    CLOSING_MAX_S_PER_LAP = 3.0
    BATTLE_FORGET_S = 60.0      # a pair apart this long starts its history again

    def race_tv(self, cars: list, t, race: dict | None, ahead_of: dict, is_race: bool) -> dict:
        """pitStops (live in the lane, and just out), closing (who is catching whom in class, and whether before the
        end), and each car's recentPaceS / recentPaceVsLeaderS (its last racing laps against its class leader's).
        Everything from our own lap, gap and pit tracking: iRacing gives none of it."""
        by = {c["idx"]: c for c in cars}
        out = {"pitStops": [], "closing": []}
        # recent race pace
        lead_pace = {}
        for c in cars:
            laps = (self._lapsum.get(c["idx"]) or {}).get("racing", [])[-self.RECENT_PACE_LAPS:]
            p = round(sum(laps) / len(laps), 3) if len(laps) >= 3 else None
            c["derived"]["recentPaceS"], c["derived"]["recentPaceLaps"] = p, len(laps)
            if c.get("classPos") == 1:
                lead_pace[c.get("classId")] = p
        for c in cars:
            p, lp = c["derived"]["recentPaceS"], lead_pace.get(c.get("classId"))
            c["derived"]["recentPaceVsLeaderS"] = round(p - lp, 3) if p is not None and lp is not None else None
        if not is_race or t is None:
            return out
        # pit stops: the lane and box time running, where it should come out (from the typical loss, fixed at the
        # lane entry), and a stop just finished with its time and the place it came out in
        loss = self.typical_loss()
        recent = getattr(self, "_pit_recent", {})
        for c in cars:
            st = self._pit.get(c["idx"])
            if not st:
                continue
            if c.get("onPitRoad") and st.get("enterT") is not None:
                if st.get("rejoinAt") != st["enterT"]:
                    st["rejoinAt"] = st["enterT"]
                    st["rejoin"] = self.predict_rejoin(cars, c["idx"], loss) if loss is not None else None
                box = ((st.get("still", 0.0) if st.get("boxPct") else t - st["boxT"])
                       if st.get("boxT") is not None else st.get("boxS"))
                if t - st["enterT"] > self.PIT_LONG_S:
                    continue                  # in the garage or out of the race (Summit Point: 368 s and counting)
                prev = recent.get(c["idx"]) or {}
                if prev.get("enterT") != st["enterT"]:          # a new stop: where it came in from, kept for the stop
                    prev = recent[c["idx"]] = {"enterT": st["enterT"], "fromPos": c.get("pos") or None,
                                               "fromClassPos": c.get("classPos") or None}
                prev["rejoin"] = st.get("rejoin")
                out["pitStops"].append({"idx": c["idx"], "done": False, "laneS": round(t - st["enterT"], 1),
                                        "boxS": None if box is None else round(box, 1), "inBox": st.get("boxT") is not None,
                                        "stop": st["stops"] + 1, "rejoin": st.get("rejoin"),
                                        "fromPos": prev["fromPos"], "fromClassPos": prev["fromClassPos"]})
            elif c["idx"] in recent and not c.get("onPitRoad"):
                r = recent[c["idx"]]
                if "outT" not in r:
                    r.update(outT=t, stationaryS=st.get("lastStopS"), pos=c.get("pos") or None,
                             classPos=c.get("classPos") or None, stop=st["stops"])
                if t - r["outT"] <= self.PIT_SHOW_AFTER_S:
                    out["pitStops"].append({"idx": c["idx"], "done": True, "stationaryS": r["stationaryS"],
                                            "stop": r["stop"], "pos": r["pos"], "classPos": r["classPos"],
                                            "rejoin": r["rejoin"], "outS": round(t - r["outT"], 1),
                                            "fromPos": r.get("fromPos"), "fromClassPos": r.get("fromClassPos")})
                else:
                    recent.pop(c["idx"], None)
        self._pit_recent = recent
        # closing in: a car catching the car ahead in its class, how fast, when it gets there, before the end or not
        left = race_laps_left(race)
        for c in cars:
            if race and race.get("finished"):
                break                  # no chase after the flag (Summit Point: "closing in" ran over the results)
            d, a = c["derived"], by.get(ahead_of.get(c["idx"]))
            # the gap the tower, the card and the battle box show (live, in class), else iRacing's at the line:
            # the graphic read +2.080 while the tower beside it read +1.727 (Summit Point)
            cl = d.get("closingSPerLap")
            gap = d.get("liveClassIntervalS") if d.get("liveClassIntervalS") is not None else d.get("intervalS")
            if a is None or a.get("classId") != c.get("classId") or c.get("onPitRoad") or a.get("onPitRoad"):
                continue
            # within a second it is a battle already; over CLOSING_MAX_S_PER_LAP it is a car in trouble, not a chase
            # (seconds of this class: class_scale)
            k = self.gap_scale(c.get("classId"))
            if cl is None or not (self.CLOSING_MIN_S_PER_LAP * k <= cl <= self.CLOSING_MAX_S_PER_LAP * k) or gap is None \
                    or not (self.BATTLE_ENTER_S * k < gap <= self.CLOSING_MAX_GAP_S * k):
                continue
            catch = round(gap / cl, 1) if cl > 0 else d.get("catchLaps")      # at the gap shown
            out["closing"].append({"idx": c["idx"], "aheadIdx": a["idx"], "gapS": round(gap, 2),
                                   "closingSPerLap": cl, "catchLaps": catch,
                                   "beforeEnd": None if catch is None or left is None else catch <= left,
                                   "forClassPos": a.get("classPos") or None})
        out["closing"].sort(key=lambda x: (x["beforeEnd"] is False, x["forClassPos"] or 99))
        del out["closing"][6:]
        return out

    def lap_chart(self, cars: list) -> dict:
        """Every car's place at the end of each lap it completed (from the laps we watched): {idx: [[lap, pos,
        classPos], ...]}, for the lap chart."""
        out = {}
        for c in cars:
            laps = [[e["lap"], e.get("pos"), e.get("classPos")] for e in self._laps.get(c["idx"], [])
                    if e.get("lap") is not None and e.get("pos")]
            if laps:
                out[str(c["idx"])] = laps
        return out

    # ------------------------------------------------------------------ #
    # Everything about one car that can be had for ANY car: the booth is
    # mostly used spectating, where only the CarIdx* arrays and the session
    # info are available. Fuel, tyres and in-car settings are your car only.
    # ------------------------------------------------------------------ #

    # Measured on the fake against its true speed: 0.25 s smoothing lagged
    # braking by up to 4 m/s (95th percentile), 0.08 s by 1.3. The fake has no
    # sampling noise and the sim's network-updated positions may, so 0.1 s.
    SPEED_SMOOTH_S = 0.1

    def car_speed(self, c, t, track_len):
        """Speed from how far along the lap the car moved since the last sample.
        iRacing gives other cars' positions but not their speed. Smoothed over
        ~0.1 s; a jump in position (reset, tow, replay cut) starts it again."""
        idx, pct = c.get("idx"), c.get("pct")
        sp = self._spd.setdefault(idx, {"t": None, "pct": None, "v": None, "max": None})
        if (not track_len or pct is None or pct < 0 or t is None
                or c.get("surface") in (None, "not_in_world")):
            sp.update(t=None, pct=None, v=None)
            return None
        v = None
        if sp["t"] is not None and t > sp["t"]:
            dt = t - sp["t"]
            d = ((pct - sp["pct"] + 0.5) % 1.0) - 0.5        # signed lap fraction
            if -0.001 < d < 0:
                d = 0.0                                      # sampling jitter, not reversing
            if 0 <= d < 0.05 and dt < 1.0:
                raw = d * track_len / dt
                if raw < 150.0:                              # 540 km/h: anything above is a glitch
                    a = 1.0 - math.exp(-dt / self.SPEED_SMOOTH_S)
                    v = raw if sp["v"] is None else sp["v"] + a * (raw - sp["v"])
        sp["t"], sp["pct"], sp["v"] = t, pct, v
        if v is not None and not c.get("onPitRoad"):
            sp["max"] = v if sp["max"] is None else max(sp["max"], v)
        return v

    def _take_top_speed(self, idx):
        sp = self._spd.get(idx)
        if not sp or sp["max"] is None:
            return None
        top, sp["max"] = sp["max"], None
        return round(top, 1)

    # A tyre or driver change shows up while the car is in the pits or soon after
    # it leaves (the session info that names the driver can lag the stop).
    PIT_CHANGE_WINDOW_S = 120.0
    STINT_MIN_LAPS = 3
    # tyre fall-off a stop for fresh tyres is weighed on (s a lap per lap of age), for a 90 s lap: a car on a
    # longer lap loses more seconds for the same wear, so both scale with the class's lap (lap_scale)
    TYRE_DEG_MIN = 0.03
    TYRE_DEG_MAX = 0.5
    REF_LAP_S = 90.0

    def lap_scale(self, class_id) -> float:
        lap = (getattr(self, "_class_lap", None) or {}).get(class_id)
        return min(2.5, max(0.4, lap / self.REF_LAP_S)) if lap else 1.0

    def car_extras(self, c, t, grid_by_idx, fast_limit, is_race, team_race) -> dict:
        idx = c["idx"]
        out = {}
        s = self._lapsum.get(idx) or {"racing": [], "top": None, "led": 0, "classLed": 0}
        racing = s["racing"][-self.CONSISTENCY_LAPS:]
        if len(racing) >= 3:
            m = sum(racing) / len(racing)
            out["consistencyS"] = round(math.sqrt(sum((x - m) ** 2 for x in racing) / len(racing)), 3)
        else:
            out["consistencyS"] = None
        out["topSpeedBestMs"] = s["top"]
        g = grid_by_idx.get(idx) if is_race else None
        out["gridPos"] = g["pos"] if g else None
        out["gridClassPos"] = g["classPos"] if g else None
        out["posGained"] = g["pos"] - c["pos"] if g and c.get("pos") else None
        out["classPosGained"] = (g["classPos"] - c["classPos"]
                                 if g and g.get("classPos") and c.get("classPos") else None)
        out["lapsLedSeen"] = s["led"]
        out["cleanStreak"] = s.get("clean", 0)
        out["classLapsLedSeen"] = s["classLed"]
        # Stints, from stops watched entry to exit. A stint that started before
        # we connected (and not at the start of the race) has no known length.
        p = self._pit.get(idx) or {}
        stints = list(p.get("stints") or [])
        start = p.get("stintStart")
        out["stintsSeen"] = stints
        out["stintStartLap"] = start
        out["lapsThisStint"] = (c["lap"] - start) if start is not None and c.get("lap") is not None else None
        # only stints long enough to stand for a tank: a first stint cut short by damage said "in around lap 2" on
        # lap 6, and "overdue, 4 stops to make" in a 12-minute sprint (Summit Point); and only a lap still to come
        full = [x for x in stints if x >= self.STINT_MIN_LAPS]
        out["longestStintLaps"] = max(full) if full else None
        est = start + max(full) if full and start is not None else None
        out["nextStopLapEst"] = est if est is not None and c.get("lap") is not None and est > c["lap"] else None
        # Tyres: a compound change is the only tyre fact iRacing gives for
        # another car (same-compound changes are invisible).
        # Tyres can only be changed in the pits, so a compound that differs anywhere
        # else (the sim catching up as the field loads, a choice made in the garage)
        # is taken in quietly, not reported as a change.
        cs = self._cmp.get(idx)
        comp = c.get("compound")
        in_pits = bool(c.get("onPitRoad")) or c.get("surface") == "in_pit_stall"
        if cs is None:
            self._cmp[idx] = cs = {"c": comp, "changes": 0, "pitT": t if in_pits else None,
                                   "hist": [[comp, c.get("lap") if is_race and (c.get("lap") or 0) <= 1 else None]]}
        if in_pits:
            cs["pitT"] = t
        if comp is not None and comp != cs["c"]:
            recent = cs.get("pitT") is not None and t is not None and t - cs["pitT"] <= self.PIT_CHANGE_WINDOW_S
            if cs["c"] is not None and recent and c.get("surface") != "not_in_world":
                cs["changes"] += 1
                cs.setdefault("hist", []).append([comp, c.get("lap")])
                del cs["hist"][:-8]
                if self.events_live:
                    self.event(t, "compound_change", idx=idx, fromCompound=cs["c"], toCompound=comp,
                               classId=c.get("classId"))
            cs["c"] = comp
        out["compoundChanges"] = cs["changes"]
        # Tyre history: each compound run seen, from the lap it started (None: started before we
        # connected). Only compound CHANGES are visible, so two stints on one compound are one run.
        hist = cs.get("hist") or []
        if hist and hist[-1][0] is None and comp is not None:
            hist[-1][0] = comp                       # the compound was not known yet at first sight
        lap_now = c.get("lap")
        if lap_now is None or lap_now < 0 or c.get("surface") == "not_in_world":
            lap_now = cs.get("lastLap")          # gone from the session: the run lasted to the last lap seen
        else:
            cs["lastLap"] = lap_now
        out["tyreRuns"] = [{"compound": h[0], "fromLap": h[1],
                            "laps": ((hist[k + 1][1] if k + 1 < len(hist) else lap_now) - h[1])
                            if h[1] is not None and (hist[k + 1][1] if k + 1 < len(hist) else lap_now) is not None else None}
                           for k, h in enumerate(hist) if h[0] is not None]
        used = c.get("fastRepairsUsed")
        out["fastRepairsLeft"] = (max(0, fast_limit - used)
                                  if isinstance(fast_limit, int) and isinstance(used, (int, float)) and used >= 0
                                  else None)
        out["drivers"] = self._driver_stints(c, t, team_race)
        return out

    def _driver_stints(self, c, t, team_race):
        """Team races: who is driving, and how long each driver has been in the
        car since we connected. iRacing names the current driver per car in the
        session info; a change there is a driver swap."""
        idx = c["idx"]
        key = c.get("userId") if c.get("userId") is not None else c.get("name")
        if key is None or t is None:
            return None
        st = self._drv.get(idx)
        if st is None:
            st = self._drv[idx] = {"cur": key, "t": t, "lap": c.get("lapCompleted"), "tot": {}}
            st["tot"][key] = {"name": c.get("name"), "timeS": 0.0, "laps": 0}
        rec = st["tot"].setdefault(st["cur"], {"name": c.get("name"), "timeS": 0.0, "laps": 0})
        rec["timeS"] += max(0.0, t - st["t"])
        lc = c.get("lapCompleted")
        if lc is not None and st["lap"] is not None and lc > st["lap"]:
            rec["laps"] += lc - st["lap"]
        st["t"], st["lap"] = t, lc
        if key != st["cur"]:
            prev = st["tot"].get(st["cur"], {}).get("name")
            # drivers swap in the pit box; a name changing anywhere else is the
            # session info catching up, counted but not announced
            pit_t = (self._cmp.get(idx) or {}).get("pitT")
            if self.events_live and pit_t is not None and t - pit_t <= self.PIT_CHANGE_WINDOW_S:
                self.event(t, "driver_change", idx=idx, fromName=prev, toName=c.get("name"))
            st["cur"] = key
            st["tot"].setdefault(key, {"name": c.get("name"), "timeS": 0.0, "laps": 0})
        if not team_race and len(st["tot"]) < 2:
            return None
        total = sum(v["timeS"] for v in st["tot"].values())
        rows = [{"name": v["name"], "timeS": round(v["timeS"], 1), "laps": v["laps"], "current": k == st["cur"],
                 # share of the time driven since we connected; an even split is 100 / drivers
                 "sharePct": round(v["timeS"] / total * 100, 1) if total > 0 else None}
                for k, v in st["tot"].items()]
        # The driver with the least time is next in an even rotation (a suggestion,
        # not iRacing's rule: its minimums are per event and not in the telemetry).
        others = [r for r in rows if not r["current"]]
        nxt = min(others, key=lambda r: r["timeS"]) if others else None
        for r in rows:
            r["next"] = r is nxt
            r["evenPct"] = round(100 / len(rows), 1)       # an even split, to compare sharePct with
        return rows
