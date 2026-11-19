"""Laps and sectors: lap records, pace, the lap profile and splits, qualifying laps, where they're faster."""
from __future__ import annotations



class Laps:
    # Sector times. iRacing publishes sector BOUNDARIES (SplitTimeInfo) but not
    # per-car sector times, so they are timed here from CarIdxLapDistPct crossings
    # against SessionTime. That shows WHERE time is going rather than only how
    # much, which is the difference between "he is quicker" and "he is quicker in
    # the infield".
    def sectors(self, idx, t, pct, starts, on_track) -> dict:
        out = {"sectorS": None, "bestSectorS": None, "sectorIdx": None}
        if idx is None or t is None or pct is None or not starts:
            return out
        n = len(starts)
        if len(self._sect_best) != n:
            self._sect_best = [None] * n
        st = self._sect.setdefault(idx, {"last_pct": None, "last_t": None, "cur": None,
                                         "enter_t": None, "times": [None] * n, "best": [None] * n,
                                         "lap": [None] * n, "lapDone": None})
        if len(st["times"]) != n:
            st["times"], st["best"], st["lap"] = [None] * n, [None] * n, [None] * n

        def sector_of(p):
            k = 0
            for i, sp in enumerate(starts):
                if p >= sp:
                    k = i
            return k

        cur = sector_of(pct)
        prev_pct, prev_t, prev_cur = st["last_pct"], st["last_t"], st["cur"]
        st["last_pct"], st["last_t"] = pct, t

        # Off track, in the pits, or teleported: the running sector is void. Time
        # it again from the next clean boundary rather than reporting a sector
        # that includes an off or a pit lane.
        jumped = prev_pct is not None and not (0 <= (pct - prev_pct) % 1.0 < 0.25)
        if not on_track or jumped:
            if prev_cur is not None and cur == 0 and prev_cur == n - 1:
                self._close_lap_sectors(st, t, n)      # crossed the line while void
            st["cur"], st["enter_t"] = cur, None
            st["last_t"] = t
            return out

        if prev_cur is None:
            # First sight is almost never on a boundary: the car is somewhere
            # inside a sector, so timing from here would bank a partial sector
            # (22.3 s against a real 38.9 s in the fake) as that car's best --
            # and its theoretical best lap with it. Start at the next line.
            st["cur"], st["enter_t"] = cur, None
            return out
        if cur != prev_cur:
            # Interpolate the moment of crossing rather than using the sample time.
            # Sampling is only ~8 Hz, so taking the tick as the boundary quantises
            # every sector to the sample interval -- which showed up as sector
            # times landing on neat 0.5 s steps. Linear interpolation across the
            # boundary recovers the sub-sample crossing.
            cross_t = t
            boundary = starts[cur]
            if prev_pct is not None and prev_t is not None and pct != prev_pct:
                span = (pct - prev_pct) % 1.0
                to_edge = (boundary - prev_pct) % 1.0
                if 0 < span and 0 <= to_edge <= span:
                    cross_t = prev_t + (t - prev_t) * (to_edge / span)
            if st["enter_t"] is not None:
                elapsed = cross_t - st["enter_t"]
                if 0.5 < elapsed < 600:
                    st["times"][prev_cur] = round(elapsed, 3)
                    st["lap"][prev_cur] = round(elapsed, 3)
                    if st["best"][prev_cur] is None or elapsed < st["best"][prev_cur]:
                        st["best"][prev_cur] = round(elapsed, 3)
                    if self._sect_best[prev_cur] is None or elapsed < self._sect_best[prev_cur]:
                        self._sect_best[prev_cur] = round(elapsed, 3)
            if cur == 0 and prev_cur == n - 1:
                self._close_lap_sectors(st, cross_t, n)
            st["cur"], st["enter_t"] = cur, cross_t
        out["sectorS"] = list(st["times"])
        out["bestSectorS"] = list(st["best"])
        out["sectorIdx"] = cur
        return out

    FEED_MAX_LAPS = 6000
    FEED_MAX_EVENTS = 1500   # incidents for a whole field add up over a long race

    def _feed_add(self, kind: str, item: dict):
        self.feed_seq += 1
        item["seq"] = self.feed_seq
        lst = self.feed_laps if kind == "lap" else self.feed_events
        lst.append(item)
        cap = self.FEED_MAX_LAPS if kind == "lap" else self.FEED_MAX_EVENTS
        if len(lst) > cap:
            del lst[: len(lst) - cap]

    def feed_since(self, seq: int) -> dict:
        return {"laps": [x for x in self.feed_laps if x["seq"] > seq],
                "events": [x for x in self.feed_events if x["seq"] > seq]}

    def event(self, t, kind: str, **fields):
        # the lap it happened on: yours while you drive; spectating (no car of your own) the leader's, which is
        # the race's lap (it read "lap 0" on everything at Summit Point)
        lap = self._player_lap if (self._player_lap or 0) > 0 else getattr(self, "_lead_lap", None)
        item = {"t": None if t is None else round(t, 2), "kind": kind, "lap": lap}
        item.update(fields)
        self._feed_add("event", item)

    def record_lap(self, c: dict, t) -> dict | None:
        """Per-car lap entries, taken at the line. Returns the entry when one is
        finalised this tick (its lap time known or given up on), else None."""
        idx, lc, last = c.get("idx"), c.get("lapCompleted"), c.get("lastLapS")
        if idx is None or lc is None or t is None or lc < 0:
            return None
        st = self._lapst.get(idx)
        if st is None:
            # First sight: the current LastLapTime belongs to a lap we did not
            # watch. Remember it so it is never credited to the next one.
            self._lapst[idx] = {"lc": lc, "final": last, "pend": None, "pendT": None,
                                "stale": None, "pit": bool(c.get("onPitRoad"))}
            return None
        if c.get("onPitRoad"):
            st["pit"] = True
        # iRacing's active reset (lone practice, your car only) puts the car back (or on) to a
        # saved point while the lap clock runs: the lap distance jumps. That lap's time means
        # nothing, so it is kept out of pace, consistency and best laps.
        pct = c.get("pct")
        if idx == self._player_idx and pct is not None and pct >= 0:
            pp, pt = st.get("pp"), st.get("pt")
            if pp is not None and lc == st["lc"] and not c.get("onPitRoad"):
                dd, dt = pct - pp, max(0.0, t - pt)
                if not (pp > 0.9 and pct < 0.1) and (dd < -0.003 or dd > 0.01 + dt * 0.05):
                    st["reset"] = True
            st["pp"], st["pt"] = pct, t
        done = None
        if lc > st["lc"]:
            if st["pend"] is not None:          # the previous lap never got a time
                done = self._finish_lap(idx, st)
            d = c.get("derived") or {}
            st["pend"] = {"idx": idx, "lap": lc, "lapS": None, "pos": c.get("pos"),
                          "classPos": c.get("classPos"),
                          "gapS": c.get("f2TimeS") if c.get("gapValid") else None,
                          "classGapS": d.get("classGapS"), "toPlayerS": d.get("toPlayerS"),
                          "pit": st["pit"] or bool(c.get("onPitRoad")),
                          "jump": lc - st["lc"] != 1, "t": round(t, 2), "sectors": None,
                          "reset": bool(st.pop("reset", False)),
                          "topSpeedMs": self._take_top_speed(idx)}
            if idx == self._player_idx:
                st["pend"]["fuelL"] = None
            st["pendT"], st["stale"], st["lc"] = t, st["final"], lc
            st["pit"] = bool(c.get("onPitRoad"))
        pend = st["pend"]
        if pend is not None:
            if not pend.get("_td"):
                if last is not None and last > 0 and last != st["stale"]:
                    if not pend["jump"]:
                        pend["lapS"] = round(last, 3)
                    st["final"] = last
                    pend["_td"] = True
                elif t - st["pendT"] >= self.LAP_TIME_WAIT_S:
                    pend["_td"] = True             # no new time: leave lapS None
            self._attach_lap_extras(idx, pend)
            waited = t - st["pendT"]
            # Only wait for what is actually being collected for this car: no
            # sector timer (no SplitTimeInfo) or no fuel tracking means nothing
            # is coming.
            sect_ok = pend["sectors"] is not None or idx not in self._sect
            fuel_ok = (idx != self._player_idx or self._last_lap_completed is None
                       or "refuel" in pend)
            extras = sect_ok and fuel_ok
            if pend.get("_td") and (extras or waited >= self.LAP_EXTRAS_WAIT_S):
                done = self._finish_lap(idx, st)
        return done

    # The line is crossed by the lap counter, the sector timer and the fuel
    # sampler on slightly different ticks; a lap entry waits this long for the
    # others to catch up before going out without them.
    LAP_EXTRAS_WAIT_S = 3.0

    def _attach_lap_extras(self, idx, pend):
        if pend["sectors"] is None:
            sd = (self._sect.get(idx) or {}).get("lapDone")
            if sd and abs(sd[0] - pend["t"]) <= self.LAP_EXTRAS_WAIT_S:
                pend["sectors"] = sd[1]
                pend["tx"] = round(sd[0], 3)          # interpolated line crossing
        if idx == self._player_idx:
            f = self._fuel_by_lap.get(pend["lap"])
            if f is not None:
                pend["fuelL"] = f["usedL"]
                pend["refuel"] = f["refuel"]
            if pend["lap"] in self._inc_by_lap:
                pend["incL"] = self._inc_by_lap[pend["lap"]]

    def _finish_lap(self, idx, st) -> dict:
        e = st["pend"]
        st["pend"] = None
        e.pop("jump", None)
        e.pop("_td", None)
        self._last_lap_seq += 1
        lst = self._laps.setdefault(idx, [])
        lst.append(e)
        del lst[:-200]
        s = self._lapsum.setdefault(idx, {"racing": [], "top": None, "led": 0, "classLed": 0})
        if e["lapS"] and not e["pit"] and not e.get("reset") and (s.get("best") is None or e["lapS"] < s["best"]):
            s["best"] = e["lapS"]
        # a lap behind the pace car (well over this car's best) is not a racing lap either
        slow = s.get("best") is not None and e["lapS"] and e["lapS"] > s["best"] * self.PACE_SLOW_LAP
        # nor is a race's opening lap (from the grid or the rolling start, through the first-lap traffic): with it,
        # the booth notes read "consistency ±5.86 s" for an MX-5 lapping within a second (Summit Point)
        opening = getattr(self, "_race_now", False) and (e.get("lap") or 0) <= 1
        if e["lapS"] and not e["pit"] and not e.get("reset") and not slow and not opening:
            s["racing"].append(e["lapS"])
            del s["racing"][:-max(self.PACE_LAPS, self.CONSISTENCY_LAPS)]
        # clean laps in a row (the Arcade look's streak on the driver card): a racing lap with no incident signal
        # (off track, a spin, contact, a tow, damage), no pit and no reset; a caution lap or the opening lap leaves it
        dirty = self.__dict__.setdefault("_dirty", set())
        if e["pit"] or e.get("reset") or idx in dirty:
            s["clean"] = 0
        elif e["lapS"] and not slow and not opening:
            s["clean"] = s.get("clean", 0) + 1
        dirty.discard(idx)
        if e.get("topSpeedMs") and (s["top"] is None or e["topSpeedMs"] > s["top"]):
            s["top"] = e["topSpeedMs"]
        s["led"] += e.get("pos") == 1
        s["classLed"] += e.get("classPos") == 1
        self._feed_add("lap", e)
        self._lap_finished.append(e)
        return e

    PACE_SLOW_LAP = 1.5         # a lap this much over the car's best: a caution lap, not racing pace

    def pace(self, idx) -> float | None:
        """Median of this car's recent racing laps: no in- or out-laps, no
        unknown times. Two laps minimum, so one lap cannot stand for a pace."""
        laps = (self._lapsum.get(idx) or {}).get("racing", [])[-self.PACE_LAPS:]
        if len(laps) < 2:
            return None
        s = sorted(laps)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    # Player-relative trend: d(toPlayerS)/lap. Positive is GOOD for the player
    # whichever side the other car is on: a car ahead (negative toPlayerS)
    # coming back towards zero, or a car behind (positive) dropping further back.
    def to_player_trend(self, idx, t, to_player_s, lap_time_s, player_idx) -> dict:
        out = {"youGainSPerLap": None, "youCatchLaps": None, "catchesYouLaps": None}
        if idx is None or t is None:
            return out
        if self._tp_ref.get(idx) != player_idx:
            self._tp_hist.pop(idx, None)
            self._tp_ref[idx] = player_idx
        hist = self._tp_hist.setdefault(idx, [])
        if to_player_s is None:
            hist.clear()
            return out
        if hist and t - hist[-1][0] < self.GAP_SAMPLE_S:
            slope = self._tp_slope.get(idx)
        else:
            hist.append((t, to_player_s))
            cutoff = t - self.GAP_WINDOW_S
            while hist and hist[0][0] < cutoff:
                hist.pop(0)
            slope = None
            if len(hist) >= self.GAP_MIN_SAMPLES and (hist[-1][0] - hist[0][0]) >= self.GAP_MIN_SPAN_S:
                slope = self._slope(hist)
            self._tp_slope[idx] = slope
        if slope is None or not lap_time_s or lap_time_s <= 0:
            return out
        gain = slope * lap_time_s
        if abs(gain) > self.GAP_MAX_RATE_S_PER_LAP:
            hist.clear()
            self._tp_slope[idx] = None
            return out
        if abs(gain) < 0.02:
            out["youGainSPerLap"] = 0.0
            return out
        out["youGainSPerLap"] = round(gain, 3)
        if to_player_s < 0 < gain:
            laps = -to_player_s / gain
            if laps < 999:
                out["youCatchLaps"] = round(laps, 1)
        elif to_player_s > 0 > gain:
            laps = to_player_s / -gain
            if laps < 999:
                out["catchesYouLaps"] = round(laps, 1)
        return out

    # ---- pit loss ------------------------------------------------------------
    @staticmethod
    def _median(vals):
        s = sorted(vals)
        if not s:
            return None
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2

    # ---- traffic: where and when the followed car meets another -----------
    # Worked in lap TIME, not lap distance. In distance, two cars a second apart
    # are 20 m apart in a hairpin and 80 m on a straight, so a constant-pace sum
    # on lap fraction swung the catch time and threw the marker round the map.
    # CarIdxEstTime / CarClassEstLapTime ("u") is how far round the lap a car is
    # in time; at a steady pace it grows evenly, so the meeting time is stable,
    # and the meeting place is turned back into a track position through each
    # class's lap profile (u against lap fraction), learned as the cars run.
    TRAFFIC_HORIZON_S = 90.0
    TRAFFIC_MIN_DIFF_S = 0.5
    MEET_HORIZON_S = 600.0
    PROFILE_BINS = 200
    MEET_SMOOTH_S = 2.0

    def learn_lap_profile(self, cars: list) -> None:
        """Per class: the average lap-time fraction at each part of the lap, from
        cars on track (shared stream only; followed views read it)."""
        for c in cars:
            pct, est, lap = c.get("pct"), c.get("estTimeS"), c.get("classEstLapS")
            if c.get("surface") != "on_track" or c.get("onPitRoad") or pct is None or not 0 <= pct < 1 \
                    or est is None or est < 0 or not lap or lap <= 0:
                continue
            u = est / lap
            if not 0 <= u <= 1.05:
                continue
            bins = self._prof.setdefault(c.get("classId"), {})
            b = int(pct * self.PROFILE_BINS) % self.PROFILE_BINS
            sp, su, n = bins.get(b, (0.0, 0.0, 0))
            if n >= 50:                              # a running average over the latest ~50
                sp, su, n = sp * 49 / 50, su * 49 / 50, 49
            bins[b] = (sp + pct, su + u, n + 1)

    # ---- where they are faster: each car's best lap timed over equal stretches of the track ----
    # Every car's position is known all the time (CarIdxLapDistPct), so the time it reaches each 1/24 of
    # the lap can be interpolated between two samples. A lap timed that way from line to line, with no pit
    # visit and no jump, at racing pace, is kept when it is the car's quickest. Two cars' best laps compared
    # stretch by stretch say where one is faster than the other: no inputs needed, any car.
    STRETCHES = 24

    def lap_splits(self, cars: list, t) -> None:
        if t is None:
            return
        K = self.STRETCHES
        for c in cars:
            i, pct = c.get("idx"), c.get("pct")
            st = self._split.setdefault(i, {"last": None, "cross": None, "best": None, "bestS": None, "bad": False})
            ok = pct is not None and pct >= 0 and c.get("surface") in ("on_track", "off_track")
            if c.get("onPitRoad") or not ok:
                st["bad"], st["last"] = True, None if not ok else (pct, t)
                continue
            last = st["last"]
            st["last"] = (pct, t)
            if last is None or t <= last[1]:
                continue
            p0, t0 = last
            step = pct - p0
            wrapped = p0 > 0.9 and pct < 0.1
            if wrapped:
                step += 1.0
            if step < 0 or step > 0.05:              # a jump: a reset, a tow, a replay cut
                st["bad"] = True
                continue
            at = lambda q: t0 + (q - p0) / step * (t - t0) if step > 0 else t
            if wrapped:                               # the line: finish the lap, start the next
                tl = at(1.0)
                cr = st["cross"]
                if cr is not None and not st["bad"] and len(cr) == K:
                    prof = [round(x - cr[0], 3) for x in cr] + [round(tl - cr[0], 3)]
                    lap = prof[-1]
                    best = st["bestS"]
                    # never quicker than iRacing's own best for the car (a timing slip), and no slower than ever
                    floor = (c.get("bestLapS") or 0) * 0.98
                    if lap > 5 and lap >= floor and (best is None or lap < best):
                        st["best"], st["bestS"] = prof, lap
                st["cross"], st["bad"] = [tl], False
                p0, t0, step = 0.0, tl, pct
                at = lambda q: t0 + (q - p0) / step * (t - t0) if step > 0 else t
            cr = st["cross"]
            if cr is None:
                continue
            k = len(cr)                               # the next boundary to cross is k/K
            while k < K and pct >= k / K:
                cr.append(at(k / K))
                k += 1

    # Qualifying: a car on a push lap against its class's pole lap, stretch by stretch (lap_splits).
    QUALI_PUSH_S = 3.0          # further behind the pole lap than this: not a push lap (an out- or cool-down lap)
    QUALI_MIN_STRETCH = 2       # a delta needs a couple of stretches behind it

    @staticmethod
    def best_intervals(cars: list) -> None:
        """Practice and qualifying: each car's best lap against the best of the car ahead of it in its class
        (derived.classBestIntervalS), the interval a timing screen shows outside a race."""
        by: dict = {}
        for c in cars:
            if c.get("classPos") and c["classPos"] > 0:
                by.setdefault(c.get("classId"), []).append(c)
        for L in by.values():
            L.sort(key=lambda c: c["classPos"])
            for x, y in zip(L, L[1:]):
                bx, by_ = x.get("bestLapS"), y.get("bestLapS")
                if bx and bx > 0 and by_ and by_ > 0:
                    y.setdefault("derived", {})["classBestIntervalS"] = round(by_ - bx, 3)

    def quali_laps(self, cars: list) -> None:
        """Sets each car's derived.classPoleGapS (its best lap against its class's best, iRacing's best
        laps) and, on a push lap, derived.qualiLap: {stretch, of, deltaPoleS, deltaOwnS, poleIdx,
        projectedS, projectedPos}. deltaPoleS is this lap's time to the last stretch boundary passed
        against the class pole lap's time to the same boundary (both timed here, by lap_splits);
        projectedS is the pole lap plus that delta, projectedPos where that would put the car in its
        class on iRacing's best laps."""
        K = self.STRETCHES
        best: dict = {}
        for c in cars:
            b = c.get("bestLapS")
            if b and b > 0 and (c.get("classId") not in best or b < best[c.get("classId")]):
                best[c.get("classId")] = b
        poles: dict = {}
        for c in cars:
            sp = self._split.get(c.get("idx")) or {}
            if sp.get("best") and (c.get("classId") not in poles or sp["bestS"] < poles[c.get("classId")][1]["bestS"]):
                poles[c.get("classId")] = (c["idx"], sp)
        for c in cars:
            d = c.setdefault("derived", {})
            b, cb = c.get("bestLapS"), best.get(c.get("classId"))
            d["classPoleGapS"] = round(b - cb, 3) if b and b > 0 and cb else None
            d["classBestIntervalS"] = None
            d["qualiLap"] = None
            sp, pole = self._split.get(c.get("idx")) or {}, poles.get(c.get("classId"))
            cr = sp.get("cross")
            if not pole or not cr or sp.get("bad") or c.get("onPitRoad"):
                continue
            k = len(cr) - 1
            if k < self.QUALI_MIN_STRETCH or k >= K:
                continue
            pidx, psp = pole
            dp = (cr[k] - cr[0]) - psp["best"][k]
            if dp > self.QUALI_PUSH_S:
                continue
            own = sp.get("best")
            proj = psp["bestS"] + dp
            others = [x.get("bestLapS") for x in cars if x is not c and x.get("classId") == c.get("classId")
                      and (x.get("bestLapS") or 0) > 0]
            d["qualiLap"] = {"stretch": k, "of": K, "deltaPoleS": round(dp, 3),
                             "deltaOwnS": round((cr[k] - cr[0]) - own[k], 3) if own else None,
                             "poleIdx": pidx, "projectedS": round(proj, 3),
                             "projectedPos": 1 + sum(1 for x in others if x < proj)}

    def faster(self, a, b) -> dict | None:
        """Car a against car b, best lap against best lap, over STRETCHES equal parts of the track:
        seconds a gains in each (negative: a loses), and each lap's time at every boundary."""
        pa, pb = (self._split.get(a) or {}).get("best"), (self._split.get(b) or {}).get("best")
        if not pa or not pb:
            return None
        seg = [round((pb[k + 1] - pb[k]) - (pa[k + 1] - pa[k]), 3) for k in range(self.STRETCHES)]
        # two best laps far apart (one a slow lap, the only one timed by stretches) say nothing about where either is
        # quicker (Summit Point: 1:36.0 against 1:24.9, "quicker on 0 of 24 stretches"): marked, and not shown
        comparable = abs(pb[-1] - pa[-1]) <= self.FASTER_COMPARABLE * min(pa[-1], pb[-1])
        return {"a": a, "b": b, "gainS": seg, "totalS": round(pb[-1] - pa[-1], 3), "lapA": pa, "lapB": pb,
                "comparable": comparable}

    FASTER_COMPARABLE = 0.025   # best laps within 2.5% of each other: a fair comparison

    def faster_pairs(self, cars: list) -> list:
        """Every car against the car ahead of it in its class (the battles and the battle box), when
        both have a best lap timed by stretches."""
        by: dict = {}
        for c in cars:
            if c.get("classPos") and c["classPos"] > 0:
                by.setdefault(c.get("classId"), []).append(c)
        out = []
        for L in by.values():
            L.sort(key=lambda c: c["classPos"])
            for x, y in zip(L, L[1:]):
                f = self.faster(x["idx"], y["idx"])
                if f:
                    out.append(f)
        return out

    def _u(self, c):
        est, lap = c.get("estTimeS"), c.get("classEstLapS")
        if est is not None and est >= 0 and lap and lap > 0 and 0 <= est / lap <= 1.05:
            return min(est / lap, 0.99999)
        pct = c.get("pct")
        return pct if pct is not None and pct >= 0 else None

    def _pct_of_u(self, class_id, u):
        """Track position at lap-time fraction u, from the class's lap profile; the
        lap-time fraction itself when too little of the lap has been seen."""
        bins = self._prof.get(class_id) or {}
        pts = sorted((sp / n, su / n) for sp, su, n in bins.values() if n)
        if len(pts) < self.PROFILE_BINS // 4:
            return u % 1.0
        u = u % 1.0
        pts = [(p - 1.0, uu - 1.0) for p, uu in pts[-3:]] + pts + [(p + 1.0, uu + 1.0) for p, uu in pts[:3]]
        for (p0, u0), (p1, u1) in zip(pts, pts[1:]):
            if u0 <= u <= u1 and u1 > u0:
                return (p0 + (p1 - p0) * (u - u0) / (u1 - u0)) % 1.0
        return u

    @staticmethod
    def sector_marks(cars: list) -> dict:
        """F1's sector colours, per class: for each car's latest time in each sector, "p" (purple)
        when it is its class's quickest that sector among the cars here, "g" (green) when it is the
        car's own best, "y" (yellow) when neither, None for a sector not timed. Each car gets
        derived.sectorMarks; returns each class's best sectors (classBestSectorS)."""
        best: dict = {}
        for c in cars:
            b = (c.get("derived") or {}).get("bestSectorS") or []
            if not b:
                continue
            cur = best.setdefault(c.get("classId"), [None] * len(b))
            if len(cur) != len(b):             # every car of a track has the same sectors; never mix two layouts
                continue
            for i, v in enumerate(b):
                if v and (cur[i] is None or v < cur[i]):
                    cur[i] = v
        for c in cars:
            d = c.get("derived")
            if d is None:
                continue
            s, pb, cb = d.get("sectorS") or [], d.get("bestSectorS") or [], best.get(c.get("classId")) or []
            marks = []
            for i, v in enumerate(s):
                if not v:
                    marks.append(None)
                elif i < len(cb) and cb[i] and v <= cb[i] + 5e-4:
                    marks.append("p")
                elif i < len(pb) and pb[i] and v <= pb[i] + 5e-4:
                    marks.append("g")
                else:
                    marks.append("y")
            d["sectorMarks"] = marks if any(marks) else None
        return {str(k): v for k, v in best.items()}

    @staticmethod
    def _close_lap_sectors(st, t, n):
        """A lap's own sector times, kept apart from the rolling latest-per-sector
        list: a void sector stays None for that lap instead of showing the time
        from the lap before."""
        st["lapDone"] = (t, list(st["lap"]))
        st["lap"] = [None] * n

    def set_ref(self, idx):
        """A different reference car: everything measured FROM the old one is
        void. Without this, switching cars reads as the reference jumping from
        P8 to P3, or a car 'closing' by the gap between two different cars."""
        self.ref_idx = idx
        self._tp_hist.clear()
        self._tp_ref.clear()
        self._tp_slope.clear()
        self._uc.clear()
        self._uc_inpit = {}
        self._near_hist.clear()
        self._meet.clear()
        self._ev["closing"] = {}
        self._ev["stable"].pop("pos", None)

    def ref_gaps(self) -> dict:
        """Gap to the reference car at each lap line, for every car: the time
        each crossed the line on lap N minus the time the reference crossed it.
        Recomputed whole, so it is right for whichever car is the reference."""
        ref = self.ref_idx
        rl = {e["lap"]: e.get("tx", e["t"]) for e in self._laps.get(ref, [])} if ref is not None else {}
        out = {}
        for idx, laps in self._laps.items():
            if idx == ref:
                continue
            s = [[e["lap"], round(e.get("tx", e["t"]) - rl[e["lap"]], 2)] for e in laps if e["lap"] in rl]
            if s:
                out[str(idx)] = s
        return out

    def session_best_sectors(self) -> list:
        return list(self._sect_best)

    def own_lap_time(self, last_lap_s, lap_completed, on_pit_road):
        """Your usual lap: the median of your last five laps that did not touch the pit lane, from LapLastLapTime
        as each lap is scored. The last lap alone may be an in- or out-lap (Indianapolis: 98 s and 122 s against
        85 s, which moved the projected finish by three laps for the two laps after the stop)."""
        st = getattr(self, "_own_laps", None)
        if st is None:
            st = self._own_laps = {"laps": [], "seen": None, "lc": lap_completed, "pit": bool(on_pit_road), "prev_pit": True}
        if on_pit_road:
            st["pit"] = True
        if lap_completed is not None and lap_completed != st["lc"]:
            st["prev_pit"], st["pit"], st["lc"] = st["pit"], bool(on_pit_road), lap_completed    # the lap just finished
        if last_lap_s and last_lap_s > 0 and last_lap_s != st["seen"]:
            first, st["seen"] = st["seen"] is None, last_lap_s
            if not first and not st["prev_pit"]:          # the first value seen is of a lap that was not watched
                st["laps"] = (st["laps"] + [float(last_lap_s)])[-5:]
        laps = sorted(st["laps"])
        if not laps:
            return None
        n = len(laps)
        return laps[n // 2] if n % 2 else (laps[n // 2 - 1] + laps[n // 2]) / 2
