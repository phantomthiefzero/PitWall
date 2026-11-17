"""Pit stops, stints, the pit-lane loss, rejoin and undercut, refuelling and in-car adjustments."""
from __future__ import annotations



class Pits:
    STILL_PCT_S = 5e-5          # part of a lap a second: under this a car in its box is standing (0.2 m/s on 4 km)

    def pit(self, idx, t, on_pit_road, in_box, lap, pct=None, class_id=None) -> dict:
        """Competitor stop tracking, from CarIdxOnPitRoad and CarIdxTrackSurface.

        Knowing who still has to stop changes the effective order more than any
        lap time -- the car 8 s up the road who has not stopped is not really
        ahead. Both signals are populated for every car, verified against a real
        race (11 pit-road entries, box times from 2 s to 176 s).

        Two things this must NOT claim:
          * A car released from the pits at the session start looks exactly like
            a car leaving a stop, except there is no entry before it. Those are
            ignored -- an exit is only a stop if we watched the entry.
          * We only see what happens while connected. So the count is stops SEEN,
            never stops made, and the client must word it that way.
        """
        out = {"stopsSeen": 0, "lastStopS": None, "lastStopLap": None,
               "lapsSincePit": None, "inPit": bool(on_pit_road)}
        if idx is None or t is None:
            return out
        on_pit_road, in_box = bool(on_pit_road), bool(in_box)
        st = self._pit.get(idx)
        if st is None:
            # First sight of this car. Seed the state from what it is doing RIGHT
            # NOW rather than from False -- a car already sitting in its box when
            # we connect would otherwise look like it just drove in, and its
            # release at the green would be counted as a completed stop. That is
            # what put a bogus 204 s "stop" on 13 of 16 cars in the VIR race.
            self._pit[idx] = {"stops": 0, "lastStopS": None, "lastStopLap": None,
                              "enterT": None, "enterLap": None, "boxT": None, "boxS": None,
                              "onRoad": on_pit_road, "inBox": in_box, "away": pct is not None and pct < 0,
                              # a stint's length is only known if we saw it start:
                              # at the start of the race, or at a stop we watched
                              "stints": [], "stintStart": lap if lap is not None and 0 <= lap <= 1 else None}
            out["inPit"] = on_pit_road
            return out
        was_away = st.get("away", False)
        st["away"] = pct is not None and pct < 0     # out of the world (LapDistPct -1)
        st["cls"] = class_id
        self._pit_approach(idx, st, t, on_pit_road, pct)
        # first seen before it was in the world (lap -1: the grid filling at a standing start, Summit Point): its
        # first stint starts when it shows up on lap 0 or 1 (it read "this stint: unknown" all race)
        if st.get("stintStart") is None and not st["stops"] and lap is not None and 0 <= lap <= 1:
            st["stintStart"] = lap
        if on_pit_road and not st["onRoad"] and was_away:
            pass        # it appeared in its box (joined, rejoined or spawned for the start): not a lane entry
        elif on_pit_road and not st["onRoad"]:               # entered the lane
            st["enterT"], st["enterLap"], st["boxS"] = t, lap, None
            st["enterPct"], st["bad"] = pct, False
        elif on_pit_road and pct is not None and pct >= 0 and st.get("lastPct") is not None:
            # A reset or tow puts the car in its box without driving the lane.
            # That looks like a stop to the counters, but timing it as lane loss
            # would be nonsense, so a jump in position voids the loss sample.
            step = (pct - st["lastPct"]) % 1.0
            if 0.05 < step < 0.995:
                st["bad"] = True
        # Stationary time is the time the car stands STILL in its box. The stall's surface is wider than the
        # stop: the car rolls in and pulls away on it, which read 14.6 s for a stop of 13.2 s (the driver's own
        # Indianapolis file: Speed 0 for 13.2 s) and 0 to 2.5 s long over 55 stops at Suzuka. A standing car's
        # CarIdxLapDistPct does not move at all (seen: exactly 0 from tick to tick).
        if in_box and st["inBox"] and st.get("boxT") is not None and pct is not None and pct >= 0 \
                and st.get("lastPct") is not None and st.get("lastT") is not None and 0 < t - st["lastT"] < 2:
            st["boxPct"] = True
            if abs(pct - st["lastPct"]) / (t - st["lastT"]) < self.STILL_PCT_S:
                st["still"] = st.get("still", 0.0) + (t - st["lastT"])
        if pct is not None and pct >= 0:
            st["lastPct"] = pct
        st["lastT"] = t
        if in_box and not st["inBox"]:                       # in the box
            st["boxT"], st["still"], st["boxPct"] = t, 0.0, False
        if st["inBox"] and not in_box and st["boxT"] is not None:
            st["boxS"] = st.get("still", 0.0) if st.get("boxPct") else t - st["boxT"]   # stationary time
            st["lastBoxS"] = round(st["boxS"], 1)
            st["boxT"] = None
        if st["onRoad"] and not on_pit_road:                 # left the lane
            if st["enterT"] is not None:                     # ...and we saw it arrive
                st["stops"] += 1
                st["lastStopS"] = round(st["boxS"] if st["boxS"] is not None else t - st["enterT"], 1)
                st["lastStopLap"] = st["enterLap"]
                rec = self._pit_lane_sample(idx, st, t, pct, st["boxS"])
                if rec is not None:
                    self._stops_tick[idx] = rec
                if st.get("stintStart") is not None and st["enterLap"] is not None:
                    st["stints"].append(st["enterLap"] - st["stintStart"])
                    del st["stints"][:-20]
                st["stintStart"] = lap
            st["enterT"] = st["enterLap"] = st["boxS"] = None
        st["onRoad"], st["inBox"] = on_pit_road, in_box

        out["stopsSeen"] = st["stops"]
        out["lastStopS"] = st["lastStopS"]
        out["lastStopLap"] = st["lastStopLap"]
        out["inPit"] = on_pit_road
        if st["lastStopLap"] is not None and lap is not None and lap >= 0:
            out["lapsSincePit"] = lap - st["lastStopLap"]
        return out

    # Stint pace. iRacing will not give us tyre wear (confirmed unavailable in
    # two cars, telemetry and YAML both), so the usable proxy is what the lap
    # times are doing across a stint. A car losing 0.8 s over eight laps is
    # telling you about its tyres whether or not the sim will.
    # STINT_MIN_LAPS is 3, set once in d_tv.py. It was also set to 4 here, but the class's later
    # line (3) always won, so the stint fall-off has used 3 all along.
    STINT_SLOW_LAP = 1.07       # slower than the stint's quickest by more than this: not a racing lap

    def stint(self, idx, lap_completed, last_lap_s, stops_seen) -> dict:
        out = {"stintLaps": 0, "stintAvgS": None, "degSPerLap": None}
        if idx is None or lap_completed is None:
            return out
        # iRacing posts a lap's time a moment AFTER its lap counter moves (a median of 1.6 s later for the field
        # at Suzuka, a tick or more for your own car), and LastLapTime holds the lap before's until then. Taking
        # the time as the counter moved gave every lap the time of the lap before, the first one twice, and the
        # in-lap to the stint after the stop (Indianapolis: "avg 1:29.2" for a stint of 1:24.8 laps). So a lap
        # waits for a NEW time; a lap that never gets one is left out.
        st = self._stint.get(idx)
        if st is None:      # first sight: the time showing is of a lap that was not watched
            st = self._stint[idx] = {"laps": [], "lastLap": lap_completed, "stops": stops_seen,
                                     "stale": last_lap_s, "wait": None}
        if stops_seen != st["stops"]:          # a stop ended the stint
            st["laps"], st["stops"] = [], stops_seen
            st["wait"] = None if st["wait"] is None else lap_completed   # the in-lap's time is not this stint's
            st["skip"] = st["wait"] is not None
        if lap_completed != st["lastLap"]:
            st["wait"] = lap_completed if lap_completed > (st["lastLap"] or 0) else None   # (the counter can go back in the pits)
            st["lastLap"], st["skip"] = lap_completed, False
        if last_lap_s and last_lap_s > 0 and last_lap_s != st["stale"]:
            st["stale"] = last_lap_s
            if st["wait"] is not None:
                if not st.get("skip"):
                    st["laps"].append((st["wait"], last_lap_s))
                    st["laps"] = st["laps"][-40:]
                st["wait"], st["skip"] = None, False
        laps = st["laps"]
        out["stintLaps"] = len(laps)
        if not laps:
            return out
        # the average is of racing laps, as the trend below: an out-lap (2:02 against 1:25) is not the stint's pace
        quick = min(l[1] for l in laps)
        racing = [l[1] for l in laps if l[1] <= quick * self.STINT_SLOW_LAP]
        out["stintAvgS"] = round(sum(racing) / len(racing), 3)
        # The trend is read from racing laps only: an out-lap, a lap behind the pace
        # car or one with a spin says nothing about the tyres (a caution lap ran
        # 2.5x slower in the fake race and made fresh tyres look worth minutes).
        laps = [l for l in laps if l[1] <= quick * self.STINT_SLOW_LAP]
        if len(laps) >= self.STINT_MIN_LAPS:
            n = len(laps)
            mx = sum(l[0] for l in laps) / n
            my = sum(l[1] for l in laps) / n
            denom = sum((l[0] - mx) ** 2 for l in laps)
            if denom > 0:
                # seconds of lap time gained (-) or lost (+) per lap of running
                out["degSPerLap"] = round(sum((l[0] - mx) * (l[1] - my) for l in laps) / denom, 3)
        return out

    def pit_loss(self) -> dict:
        """Lane loss measured from stops we watched from entry to exit:
        time between the pit-road lines, minus the stationary time, minus the
        time that car takes to cover the same stretch of track at its own pace.
        The stationary part depends on the service, so it is kept separate."""
        lane = [s["laneLossS"] for s in self._loss]
        boxes = [s["boxS"] for s in self._loss if s["boxS"] is not None and s["boxS"] > 1.0]
        med = self._median(lane)
        bmed = self._median(boxes)
        return {"laneLossS": None if med is None else round(med, 2), "samples": len(lane),
                "boxMedianS": None if bmed is None else round(bmed, 1), "boxSamples": len(boxes)}

    def _u_of_pct(self, class_id, pct):
        """Lap-time fraction at a track position, from the class's lap profile (the other way round from
        _pct_of_u); the position itself when too little of the lap has been seen."""
        bins = self._prof.get(class_id) or {}
        pts = sorted((sp / n, su / n) for sp, su, n in bins.values() if n)
        if len(pts) < self.PROFILE_BINS // 4:
            return pct % 1.0
        pct = pct % 1.0
        pts = [(p - 1.0, uu - 1.0) for p, uu in pts[-3:]] + pts + [(p + 1.0, uu + 1.0) for p, uu in pts[:3]]
        for (p0, u0), (p1, u1) in zip(pts, pts[1:]):
            if p0 <= pct <= p1 and p1 > p0:
                return (u0 + (u1 - u0) * (pct - p0) / (p1 - p0)) % 1.0
        return pct

    # What a trip through the pit lane costs is more than the time between its two lines: the car brakes for the
    # entry and gets back up to speed after the exit. So the loss is timed from a little before the entry to a
    # little after the exit, in lap-time fractions (the class's profile), against the car's own pace over the
    # same part of the lap. The driver's own Indianapolis file: 58.6 s between the lines, 13.2 s standing, and
    # the same stretch takes 12.0 s at speed (the lane runs beside the main straight): 33.4 s lost between the
    # lines and 35.6 s in all. Taking the stretch as its share of the lap's LENGTH (a fifth: 17.0 s) said 28.3.
    PIT_BEFORE_U = 0.04
    PIT_AFTER_U = 0.08

    def _pit_approach(self, idx, st, t, on_pit_road, pct):
        """The last seconds on track before a lane entry, and the wait after an exit for the car to be back up
        to speed; then the stop's loss sample is made whole."""
        if pct is None or pct < 0:
            return
        if not on_pit_road:
            h = st.setdefault("hist", [])
            h.append((t, pct))
            while h and t - h[0][0] > 25.0:
                h.pop(0)
            post = st.get("post")
            if post is not None:
                u = self._u_of_pct(st.get("cls"), pct)
                gone = (u - post["uExit"]) % 1.0
                if t - post["tExit"] > 60.0 or gone > 0.5:
                    st["post"] = None
                elif gone >= self.PIT_AFTER_U:
                    pt, pg = post.get("prev", (post["tExit"], 0.0))
                    f = (self.PIT_AFTER_U - pg) / (gone - pg) if gone > pg else 1.0
                    t_after = pt + f * (t - pt)
                    span = (post["uExit"] + self.PIT_AFTER_U - post["uPre"]) % 1.0
                    full = (t_after - post["tPre"]) - (post["box"] or 0.0) - span * post["pace"]
                    rec = post["rec"]
                    if rec["laneLossS"] - 1.0 <= full < 150.0:
                        rec["linesS"], rec["laneLossS"], rec["whole"] = rec["laneLossS"], round(full, 2), True
                    st["post"] = None
                else:
                    post["prev"] = (t, gone)
        else:
            st["post"] = None
            if not st.get("onRoad"):       # the entry line, this tick: where was the car PIT_BEFORE_U of a lap ago
                cls, pre = st.get("cls"), None
                u_in = self._u_of_pct(cls, pct)
                h = [(ht, (u_in - self._u_of_pct(cls, hp)) % 1.0) for ht, hp in st.get("hist", [])]
                for (t0, b0), (t1, b1) in zip(h, h[1:]):      # b: lap-time fraction still to go to the entry line
                    if b0 >= self.PIT_BEFORE_U >= b1 and b0 > b1 and b0 < 0.5:
                        pre = t0 + (b0 - self.PIT_BEFORE_U) / (b0 - b1) * (t1 - t0)
                st["preT"], st["hist"] = pre, []

    def _pit_lane_sample(self, idx, st, t, pct, box_s):
        """Called when a car leaves the lane after a stop we watched."""
        if st.get("bad") or st.get("enterPct") is None or pct is None or pct < 0:
            return None
        pace = self.pace(idx)
        if not pace:
            return None
        cls = st.get("cls")
        frac = (pct - st["enterPct"]) % 1.0
        u_in, u_out = self._u_of_pct(cls, st["enterPct"]), self._u_of_pct(cls, pct)
        track_s = ((u_out - u_in) % 1.0) * pace          # this stretch at the car's pace
        lane_t = t - st["enterT"]
        loss = lane_t - (box_s or 0.0) - track_s
        if not (frac < 0.3 and 0.0 < loss < 120.0):
            return None
        rec = {"idx": idx, "laneLossS": round(loss, 2), "boxS": box_s, "laneS": round(lane_t, 1),
               "trackS": round(track_s, 2)}
        self._loss.append(rec)
        del self._loss[:-30]
        pre = st.get("preT")              # the approach (set at the entry line)
        if pre is not None:
            st["post"] = {"rec": rec, "tPre": pre, "uPre": (u_in - self.PIT_BEFORE_U) % 1.0, "uExit": u_out,
                          "tExit": t, "box": box_s, "pace": pace}
        return rec

    # ---- rejoin --------------------------------------------------------------
    def predict_rejoin(self, cars: list, idx: int, loss_s: float) -> dict | None:
        """Where car idx would be if it lost loss_s right now. Race order only,
        from CarIdxF2Time (time behind the leader): the car's virtual gap is its
        gap plus the loss, and every car with a smaller gap is ahead of it.
        Only cars whose F2Time passed the placeholder/order checks take part."""
        me = next((c for c in cars if c.get("idx") == idx), None)
        if me is None or not me.get("gapValid"):
            return None
        v = me["f2TimeS"] + loss_s
        others = [c for c in cars if c.get("gapValid") and c.get("idx") != idx]
        ahead = [c for c in others if c["f2TimeS"] < v]
        same = [c for c in others if c.get("classId") == me.get("classId")]
        c_ahead = [c for c in same if c["f2TimeS"] < v]
        c_behind = [c for c in same if c["f2TimeS"] >= v]
        a = max(c_ahead, key=lambda c: c["f2TimeS"]) if c_ahead else None
        b = min(c_behind, key=lambda c: c["f2TimeS"]) if c_behind else None
        out = {"lossS": round(loss_s, 1), "pos": len(ahead) + 1, "classPos": len(c_ahead) + 1,
               "ahead": {"idx": a["idx"], "gapS": round(v - a["f2TimeS"], 1)} if a else None,
               "behind": {"idx": b["idx"], "gapS": round(b["f2TimeS"] - v, 1)} if b else None,
               "traffic": []}
        # Who is physically near on track as the car comes out, lapped cars
        # included: race-time separation folded into one lap of this car's pace.
        pace = self.pace(idx)
        if pace:
            near = []
            for c in others:
                if c.get("onPitRoad"):
                    continue
                sep = ((c["f2TimeS"] - v + pace / 2) % pace) - pace / 2
                if abs(sep) <= 2.0:
                    near.append({"idx": c["idx"], "onTrackS": round(sep, 1)})
            out["traffic"] = sorted(near, key=lambda x: abs(x["onTrackS"]))[:3]
        return out

    def rejoin(self, cars: list, player_idx) -> dict | None:
        pl = self.pit_loss()
        out = {**pl, "options": []}
        if pl["laneLossS"] is None or player_idx is None:
            return out
        opts = [("zero_stationary", 0.0)]
        if pl["boxMedianS"] is not None:
            opts.append(("typical_stop", pl["boxMedianS"]))
        own = self._pit.get(player_idx, {}).get("lastBoxS")
        if own:
            opts.append(("your_last_stop", own))
        for kind, s in opts:
            r = self.predict_rejoin(cars, player_idx, pl["laneLossS"] + s)
            if r is None:
                return out
            r["kind"], r["stationaryS"] = kind, round(s, 1)
            out["options"].append(r)
        return out

    def typical_loss(self) -> float | None:
        pl = self.pit_loss()
        if pl["laneLossS"] is None:
            return None
        return pl["laneLossS"] + (pl["boxMedianS"] or 0.0)

    # ---- undercut / overcut ------------------------------------------------
    UC_RANGE_S = 40.0
    UC_LAPS = 6

    def undercut(self, cars: list, player_idx, t) -> list:
        """Pit battles with the cars around you in your class.

        they_stopped: a rival stopped and you have not. afterYourStopS is where
          you would be against them if you stopped now (toPlayerS minus the
          typical loss); positive means you would still come out ahead.
        you_stopped: you stopped and the rival has not. afterTheirStopS is where
          they would be against you if they stopped now; positive = behind you.
        series holds that number at each of the stopping car's lap crossings.
        """
        by = {c["idx"]: c for c in cars}
        me = by.get(player_idx)
        loss = self.typical_loss()
        if me is None:
            return []
        my_cls = me.get("classId")
        in_pit = {i: bool(c.get("onPitRoad")) for i, c in by.items()}
        prev = self._uc_inpit
        for i, c in by.items():
            entered = in_pit[i] and not prev.get(i, in_pit[i])
            if not entered:
                continue
            if i == player_idx:
                for j, o in by.items():
                    if j == player_idx:
                        continue
                    rec = self._uc.get(j)
                    if rec and rec["kind"] == "they_stopped" and rec["settled"] is None:
                        rec["secondStopT"] = t      # you answered their stop
                        continue
                    tp = (o.get("derived") or {}).get("toPlayerS")
                    if (o.get("classId") == my_cls and tp is not None
                            and abs(tp) < self.UC_RANGE_S and not in_pit.get(j)):
                        self._uc[j] = {"kind": "you_stopped", "pre": tp, "t0": t,
                                       "stopLap": me.get("lapCompleted"), "series": [], "settled": None}
            else:
                tp = (c.get("derived") or {}).get("toPlayerS")
                rec = self._uc.get(i)
                if rec and rec["kind"] == "you_stopped" and rec["settled"] is None:
                    rec["secondStopT"] = t          # they answered: settle when out
                elif (c.get("classId") == my_cls and tp is not None and abs(tp) < self.UC_RANGE_S
                        and not in_pit.get(player_idx) and not self._recently_stopped(player_idx, me)):
                    self._uc[i] = {"kind": "they_stopped", "pre": tp, "t0": t,
                                   "stopLap": c.get("lapCompleted"), "series": [], "settled": None}
        self._uc_inpit = in_pit

        # lap crossings feed the series
        for e in self._lap_finished:
            for j, rec in self._uc.items():
                stopper = j if rec["kind"] == "they_stopped" else player_idx
                # Only laps after the stopper is back on track: a crossing made in
                # the lane measures the stop in progress, not the battle.
                if e["idx"] == stopper and rec["settled"] is None and not in_pit.get(stopper):
                    v = self._uc_value(rec, by.get(j), loss)
                    if v is not None:
                        rec["series"].append(round(v, 2))
                        del rec["series"][:-self.UC_LAPS]

        out = []
        for j, rec in list(self._uc.items()):
            c = by.get(j)
            if c is None:
                continue
            tp = (c.get("derived") or {}).get("toPlayerS")
            # settle once both cars have stopped and are both out of the lane
            if rec.get("secondStopT") is not None and rec["settled"] is None \
                    and not in_pit.get(j) and not in_pit.get(player_idx) and tp is not None:
                rec["settled"] = round(tp, 2)
                rec["settledT"] = t
                self.event(t, "pit_battle", idx=j, ref=player_idx, preS=round(rec["pre"], 2), nowS=rec["settled"],
                           first="them" if rec["kind"] == "they_stopped" else "you")
            stopper = c if rec["kind"] == "they_stopped" else me
            laps_since = None
            if stopper.get("lapCompleted") is not None and rec["stopLap"] is not None:
                laps_since = stopper["lapCompleted"] - rec["stopLap"]
            if (laps_since is not None and laps_since > self.UC_LAPS) or \
                    (rec.get("settledT") is not None and t - rec["settledT"] > 240):
                del self._uc[j]
                continue
            item = {"idx": j, "kind": rec["kind"], "stopLap": rec["stopLap"], "lapsSince": laps_since,
                    "preStopToPlayerS": round(rec["pre"], 2),
                    "toPlayerS": None if tp is None else round(tp, 2),
                    "inPit": in_pit.get(j) if rec["kind"] == "they_stopped" else in_pit.get(player_idx),
                    "series": list(rec["series"]), "settledToPlayerS": rec["settled"],
                    # change between the last two crossings. Both series use the
                    # toPlayerS sign (+ = they are behind you), so + is in your
                    # favour for either kind of battle.
                    "trendSPerLap": None}
            if len(rec["series"]) >= 2:
                step = rec["series"][-1] - rec["series"][-2]
                item["trendSPerLap"] = round(step, 2)
            v = self._uc_value(rec, c, loss)
            item["afterYourStopS" if rec["kind"] == "they_stopped" else "afterTheirStopS"] = \
                None if v is None or rec["settled"] is not None else round(v, 2)
            out.append(item)
        return out

    def _recently_stopped(self, idx, car) -> bool:
        """True within UC_LAPS laps of this car's last watched stop: a rival
        stopping then is answering that stop, not opening a new battle."""
        last = self._pit.get(idx, {}).get("lastStopLap")
        lap = (car or {}).get("lap")
        return last is not None and lap is not None and lap - last <= self.UC_LAPS

    @staticmethod
    def _uc_value(rec, c, loss):
        tp = (c.get("derived") or {}).get("toPlayerS") if c else None
        if tp is None or loss is None:
            return None
        return tp - loss if rec["kind"] == "they_stopped" else tp + loss

    # ---- your car: refuel rate, adjustments --------------------------------
    def refuel(self, t, fuel_level, in_stall):
        """Refuel rate measured at your own stops: the rising part of the fuel
        level while in the box (2.60 L/s in the Indianapolis capture)."""
        if in_stall and fuel_level is not None and t is not None:
            self._rf_samples.append((t, fuel_level))
            return
        s, self._rf_samples = self._rf_samples, []
        rise = [i for i in range(1, len(s)) if s[i][1] > s[i - 1][1] + 1e-5]
        if len(rise) >= 2:
            a, b = s[rise[0] - 1], s[rise[-1]]
            if b[1] - a[1] > 1.0 and b[0] - a[0] > 1.0:
                self._rf_rates.append((b[1] - a[1]) / (b[0] - a[0]))
                del self._rf_rates[:-10]

    def refuel_rate(self):
        return round(self._median(self._rf_rates), 3) if self._rf_rates else None

    ADJUST_QUIET_S = 3.0
    ADJUST_SKIP = {"dashPage"}

    def adjustments(self, t, dc: dict, lap, live: bool):
        """In-car adjustments (brake bias, TC, ABS...), your car only. Clicking
        through several steps is one change: it is reported once the control
        has been left alone for 3 s."""
        if not live or t is None:
            return
        cur = {k: v for k, v in (dc or {}).items() if v is not None and k not in self.ADJUST_SKIP}
        base, pend = self._adj_base, self._adj_pend
        if base is None:
            self._adj_base = dict(cur)
            return
        for k, v in cur.items():
            if k not in base:
                base[k] = v
            elif v != (pend[k]["to"] if k in pend else base[k]):
                if k in pend:
                    pend[k].update(to=v, t=t)
                else:
                    pend[k] = {"from": base[k], "to": v, "t": t, "lap": lap}
        for k in list(pend):
            if t - pend[k]["t"] >= self.ADJUST_QUIET_S:
                p = pend.pop(k)
                if p["to"] != p["from"]:
                    self.event(t, "adjust", control=k, fromValue=p["from"], toValue=p["to"], onLap=p["lap"])
                base[k] = p["to"]
