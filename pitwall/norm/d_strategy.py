"""Strategy: tyre pace, the next stop, the caution, the plan, fuel."""
from __future__ import annotations

import math
from .fuel import TIMED_PACE_MARGIN, timed_laps_left
from .race import race_laps_left


class Strategy:
    def compound_pace(self, cars) -> list:
        """Wet or dry: each class's cars grouped by tyre compound, with the
        middle of their last racing laps. Only cars whose last lap was a racing
        lap count. The call is when the other compound's group is quicker."""
        groups = {}
        for c in cars:
            last = next((e for e in reversed(self._laps.get(c["idx"], [])) if e["lapS"]), None)
            if last is None or last["pit"] or c.get("compound") is None:
                continue
            groups.setdefault((c.get("classId"), c["compound"]), []).append(last["lapS"])
        out = []
        for (cls, comp), laps in groups.items():
            out.append({"classId": cls, "compound": comp, "cars": len(laps),
                        "medianLastLapS": round(self._median(laps), 3)})
        for cls in {o["classId"] for o in out}:
            g = [o for o in out if o["classId"] == cls]
            best = min(o["medianLastLapS"] for o in g)
            for o in g:
                o["fastest"] = o["medianLastLapS"] == best and len(g) > 1
                o["vsFastestS"] = round(o["medianLastLapS"] - best, 3) if len(g) > 1 else None
        return sorted(out, key=lambda o: (str(o["classId"]), o["compound"]))

    # ---- strategy: rival stops, the caution call, options side by side ------
    # iRacing gives no fuel or tyre data for another car, so when a rival must
    # stop is read from how long its stints run: its own longest watched stint,
    # else the middle of the watched stints in its class, else -- for a car of
    # the same model as yours -- how far your tank goes at your burn. Each says
    # which it used, and all of it is derived.
    STINT_MIN_SAMPLES = 2

    def stop_forecast(self, cars: list, race: dict | None, own_idx, fpl, tank_l, fuel_l) -> None:
        """Adds derived.stopDue to every car: {lap, lapsLeft, rangeLaps, basis, stopsOwed, overdue}."""
        to_go = race_laps_left(race)
        by_class: dict = {}
        for c in cars:
            for s in (c["derived"].get("stintsSeen") or []):
                if s and s >= self.STINT_MIN_LAPS:
                    by_class.setdefault(c.get("classId"), []).append(s)
        own = next((c for c in cars if c["idx"] == own_idx), None)
        fuel_range = math.floor(tank_l / fpl) if fpl and tank_l and fpl > 0 else None
        done = bool(race and race.get("finished"))
        for c in cars:
            d = c["derived"]
            d["stopDue"] = None
            if done:
                continue               # the flag is out: no stop is due any more (strategy read "Due" after the finish)
            this, start = d.get("lapsThisStint"), d.get("stintStartLap")
            rng, basis = None, None
            if c is own and fpl and fuel_l is not None and this is not None:
                rng, basis = this + math.floor(fuel_l / fpl), "your_fuel"
            elif d.get("longestStintLaps"):
                rng, basis = d["longestStintLaps"], "own_stints"
            elif len(by_class.get(c.get("classId"), [])) >= self.STINT_MIN_SAMPLES:
                seen = by_class[c.get("classId")]
                rng, basis = int(round(self._median(seen))), "class_stints"
                # already further than the class usually runs: it can go at least as
                # far as the longest stint seen, so only call it overdue past that
                if this is not None and this > rng:
                    rng = max(seen)
            elif fuel_range and own is not None and c.get("carId") is not None and c.get("carId") == own.get("carId"):
                rng, basis = fuel_range, "your_tank"
            if not rng or rng < 1 or this is None or start is None:
                continue
            left = rng - this
            owed = None
            if to_go is not None:
                owed = 0 if left >= to_go else math.ceil((to_go - max(0, left)) / max(1, rng if basis != "your_fuel" else (fuel_range or rng)))
            d["stopDue"] = {"lap": start + rng, "lapsLeft": max(0, left), "rangeLaps": rng, "basis": basis,
                            "stopsOwed": owed, "overdue": left < 0}

    # A replay carries no flags (SessionFlags 0, no pace or per-car flags: a Talladega replay and a
    # Daytona one). Then a full-course caution is read from the field: most cars on track going at well
    # under their best lap's average speed, together, for a while. Green running in a superspeedway
    # pack sat at 0.9-1.3x; the caution laps at 2.3-2.8x.
    CAUTION_IN_RATIO, CAUTION_IN_S = 1.7, 15.0
    CAUTION_OUT_RATIO, CAUTION_OUT_S = 1.3, 8.0

    def caution(self, cars: list, flags, pits_open, track_len, t, state=None) -> dict:
        """Under a full-course caution the field crawls behind the pace car, so a
        stop costs far less than under green: the lane takes as long as ever, but
        the field covers much less track meanwhile. The loss is re-scaled from the
        stops watched this session by how much slower the field is going now."""
        flags = flags or []
        active = "caution" in flags or "caution_waving" in flags
        st = self._caut
        inferred = False
        # No racing flag at all: a replay (no bits), or a spectator, whose SessionFlags carry only the start-light
        # and service bits once the race is under way (Summit Point, Nov 2026): read the caution from the field.
        racing_bits = {"green", "yellow", "yellow_waving", "caution", "caution_waving", "white", "checkered", "red",
                       "green_held", "debris", "crossed", "one_lap_to_green"}
        if not racing_bits & set(flags):
            slow = []
            for c in cars:
                v = (c.get("derived") or {}).get("speedMs")
                b = c.get("bestLapS")
                if c.get("onPitRoad") or c.get("surface") != "on_track" or not v or v < 2 or not b or b <= 0 or not track_len:
                    continue
                slow.append((track_len / b) / v)
            r_best = self._median(slow) if len(slow) >= 5 else None
            was = st.get("inf", False)
            if state != "racing" or t is None:
                st["inf"], st["infT"] = False, None
            elif r_best is None:
                pass                          # too few speeds this tick (a gap in the data): no change
            else:
                cond = r_best >= self.CAUTION_IN_RATIO if not was else r_best < self.CAUTION_OUT_RATIO
                if cond:
                    st["infT"] = st.get("infT") if st.get("infT") is not None else t
                    if t - st["infT"] >= (self.CAUTION_IN_S if not was else self.CAUTION_OUT_S):
                        st["inf"], st["infT"] = not was, None
                        # race control has no flag to report here, so say what the field did
                        self.event(t, "flag", flag="green" if was else "caution", inferred=True)
                else:
                    st["infT"] = None
            inferred = active = bool(st.get("inf"))
        if active and st["t0"] is None:
            st["t0"] = t
        elif not active:
            st["t0"] = None
        out = {"active": active, "inferred": inferred, "since": st["t0"],
               "pitsOpen": None if pits_open is None or inferred else bool(pits_open),
               "fieldSlowdown": None, "lossGreenS": None, "lossNowS": None, "savingS": None}
        green = self.typical_loss()
        out["lossGreenS"] = None if green is None else round(green, 1)
        ratios = []
        for c in cars:
            v = (c.get("derived") or {}).get("speedMs")
            p = self.pace(c["idx"])
            if c.get("onPitRoad") or c.get("surface") != "on_track" or not v or v < 2 or not p or not track_len:
                continue
            ratios.append((track_len / p) / v)
        r = self._median(ratios) if len(ratios) >= 3 else None
        out["fieldSlowdown"] = None if r is None else round(r, 2)
        if not active or r is None or green is None or not self._loss:
            return out
        drive = self._median([s["laneS"] - (s["boxS"] or 0.0) for s in self._loss])
        track = self._median([s.get("trackS", s["laneS"] - (s["boxS"] or 0.0) - s["laneLossS"]) for s in self._loss])
        box = self.pit_loss()["boxMedianS"] or 0.0
        now = max(0.0, drive - max(1.0, r) * track) + box
        out["lossNowS"] = round(now, 1)
        out["savingS"] = round(green - now, 1)
        return out

    def strategy(self, cars: list, ref, race: dict | None, caution: dict | None, own_idx) -> dict | None:
        """Ways to run the rest of the race for the reference car, side by side: the
        time each costs in stops and where it is likely to finish in class if every
        car keeps its pace and stops when its stints run out. Fuel-exact for your
        own car (the fuel plan); from stint lengths for any other car."""
        by = {c["idx"]: c for c in cars}
        me = by.get(ref)
        to_go = race_laps_left(race)
        if me is None or to_go is None:
            return None
        pl = self.pit_loss()
        lane = pl["laneLossS"]
        box = pl["boxMedianS"]
        green = self.typical_loss()
        info = self._fuel_info or {}
        plan = info.get("plan") if ref == own_idx else None
        fpl, fuel_l, tank, rate = info.get("fpl"), info.get("fuelL"), info.get("tankL"), info.get("refuelLps")
        md = me.get("derived") or {}
        out = {"basis": "fuel" if plan and plan.get("stops") is not None else "stints", "lapsToGo": to_go,
               "lossGreenS": None if green is None else round(green, 1), "options": [], "best": None,
               "targets": self._targets(cars, me, to_go)}

        def stop_s(add_l):
            if rate and add_l is not None:
                return add_l / rate
            return box or 0.0

        opts = []
        if out["basis"] == "fuel":
            stops, short = plan["stops"], max(0.0, (plan.get("needL") or 0.0) - (fuel_l or 0.0))
            lap_no = plan["finishLap"] - plan["lapsLeft"] + 1 if plan.get("finishLap") is not None else None
            if stops == 0:
                opts.append({"kind": "no_stop", "stops": 0, "lossS": 0.0,
                             "spareL": plan.get("spareAtFlagL")})
            elif lane is not None:
                total = stops * lane + (short / rate if rate else stops * (box or 0.0))
                open_ = plan.get("windowOpenLap")
                if open_ is not None and lap_no is not None and open_ <= lap_no:
                    opts.append({"kind": "box_now", "stops": stops, "lossS": total, "lap": lap_no,
                                 "addL": plan.get("addThisLapL"),
                                 "rejoin": self.predict_rejoin(cars, ref, lane + stop_s(plan.get("addThisLapL")))})
                if plan.get("windowCloseLap") is not None:
                    opts.append({"kind": "box_latest", "stops": stops, "lossS": total, "lap": plan["windowCloseLap"],
                                 "addL": plan.get("addAtCloseL")})
                left = plan["lapsLeft"]
                if fpl and tank and left and left > 0:
                    over = short - (stops - 1) * tank            # what one stop fewer leaves you short
                    save = over / left
                    pct = save / fpl * 100
                    if 0 < pct <= 15:
                        opts.append({"kind": "stretch", "stops": stops - 1,
                                     "lossS": (stops - 1) * lane + (max(0.0, short - over) / rate if rate else (stops - 1) * (box or 0.0)),
                                     "saveLPerLap": round(save, 3), "savePct": round(pct, 1)})
        elif lane is not None and green is not None:
            sd = md.get("stopDue") or {}
            owed = sd.get("stopsOwed")
            if owed is not None:
                due, now_lap = sd.get("lap"), me.get("lap")
                if due is not None and now_lap is not None and due < now_lap:
                    due = None                  # a forecast lap already gone by says nothing
                opts.append({"kind": "as_forecast", "stops": owed, "lossS": owed * green, "lap": due if owed else None})
        # fresh tyres: worth a stop when the stint's fall-off over the laps left beats the stop
        deg, age = md.get("degSPerLap"), md.get("stintLaps")
        # not under a caution (the laps are slow for everyone, so they say nothing about the tyres)
        # and not from a fall-off no tyre has (over TYRE_DEG_MAX s a lap is traffic or a caution in the stint)
        ks = self.lap_scale(me.get("classId"))
        if (lane is not None and deg and self.TYRE_DEG_MIN * ks <= deg <= self.TYRE_DEG_MAX * ks and age and age >= 5
                and to_go >= 3 and opts and not (caution and caution.get("active"))):
            base = min(o["lossS"] for o in opts)
            gain = deg * age * to_go
            opts.append({"kind": "tyres", "stops": min(o["stops"] for o in opts) + 1,
                         "lossS": base + lane + (box or 0.0), "tyreGainS": round(gain, 1),
                         "rejoin": self.predict_rejoin(cars, ref, lane + (box or 0.0))})
        # under a caution, a stop now against the stop it replaces
        if caution and caution.get("active") and caution.get("lossNowS") is not None and caution.get("pitsOpen") is not False and opts:
            base = min((o for o in opts if o["kind"] != "tyres"), key=lambda o: o["lossS"], default=None)
            if base is not None and base["stops"] >= 1 and green is not None:
                extra = 0
                if out["basis"] == "fuel" and fpl and tank:
                    need_after = (plan.get("needL") or 0.0)            # filling to full now
                    extra = max(0, math.ceil((need_after - tank) / tank - 1e-9))
                else:
                    extra = base["stops"] - 1
                opts.append({"kind": "caution_now", "stops": 1 + extra,
                             "lossS": caution["lossNowS"] + extra * green,
                             "rejoin": self.predict_rejoin(cars, ref, caution["lossNowS"])})
        for o in opts:
            o["lossS"] = round(o["lossS"], 1)
            # the tyre gain comes off its time, and its finish is worked out with it
            o["netS"] = round(o["lossS"] - o["tyreGainS"], 1) if o["kind"] == "tyres" else o["lossS"]
            o.update(self._finish_estimate(cars, me, o["netS"], to_go, green))
        if opts:
            # the best is the best finish; time decides between equal finishes (and when none is known)
            out["best"] = min(opts, key=lambda o: (o.get("finishClassPos") or 999, o["netS"],
                                                   o.get("savePct") or 0))["kind"]
        out["options"] = opts
        return out

    def _finish_estimate(self, cars, me, loss_s, to_go, green) -> dict:
        """Class position at the flag if every car keeps its pace and makes the stops
        its stints say it still owes: time behind the leader now, plus laps left at
        its pace, plus its stops."""
        mine = me.get("classId")
        same = [c for c in cars if c.get("classId") == mine and c.get("gapValid")]
        if not me.get("gapValid") or green is None:
            return {"finishClassPos": None, "finishAhead": None, "finishBehind": None}
        paces = {c["idx"]: self.pace(c["idx"]) for c in same}
        use_pace = all(p for p in paces.values())

        def proj(c, loss):
            return c["f2TimeS"] + (paces[c["idx"]] * to_go if use_pace else 0.0) + loss

        mine_t = proj(me, loss_s)
        others = []
        for c in same:
            if c is me:
                continue
            owed = ((c.get("derived") or {}).get("stopDue") or {}).get("stopsOwed")
            others.append((proj(c, (owed or 0) * green), c["idx"], owed is None))
        ahead = sorted([o for o in others if o[0] < mine_t], key=lambda o: -o[0])
        behind = sorted([o for o in others if o[0] >= mine_t], key=lambda o: o[0])
        return {"finishClassPos": len(ahead) + 1,
                "finishAhead": {"idx": ahead[0][1], "s": round(mine_t - ahead[0][0], 1), "guessed": ahead[0][2]} if ahead else None,
                "finishBehind": {"idx": behind[0][1], "s": round(behind[0][0] - mine_t, 1), "guessed": behind[0][2]} if behind else None,
                "withPace": use_pace}

    def _targets(self, cars, me, to_go) -> dict:
        """What the driver has to do: the class car ahead to catch, the one behind to
        keep back, by the flag."""
        mine = me.get("classId")
        same = [c for c in cars if c is not me and c.get("classId") == mine
                and (c.get("derived") or {}).get("toPlayerS") is not None]
        ahead = max((c for c in same if c["derived"]["toPlayerS"] < 0), key=lambda c: c["derived"]["toPlayerS"], default=None)
        behind = min((c for c in same if c["derived"]["toPlayerS"] > 0), key=lambda c: c["derived"]["toPlayerS"], default=None)
        out = {"catch": None, "hold": None}
        if ahead is not None and to_go > 0:
            gap = -ahead["derived"]["toPlayerS"]
            gain = ahead["derived"].get("youGainSPerLap")
            my_pace = self.pace(me["idx"])
            need = gap / to_go
            out["catch"] = {"idx": ahead["idx"], "gapS": round(gap, 2), "needSPerLap": round(need, 3),
                            "gainingSPerLap": gain, "catchLaps": ahead["derived"].get("youCatchLaps"),
                            "targetLapS": round(my_pace - need, 3) if my_pace else None,
                            "onCourse": bool(gain and gain >= need)}
        if behind is not None and to_go > 0:
            gap = behind["derived"]["toPlayerS"]
            closing = behind["derived"].get("youGainSPerLap")
            closing = -closing if closing is not None else None   # + = they are catching you
            laps = behind["derived"].get("catchesYouLaps")
            out["hold"] = {"idx": behind["idx"], "gapS": round(gap, 2), "closingSPerLap": closing,
                           "catchesInLaps": laps, "beforeFlag": bool(laps is not None and laps <= to_go)}
        return out

    @staticmethod
    def laps_remaining(laps_remain_ex, time_remain_s, lap_time_s, lap_pct=None, leader=None, ended=False):
        """(laps, is_estimate) or (None, False), counting the lap in progress.

        A lap-limited session states it outright. A timed one does not, so it has
        to be projected from the clock, the pace and where the car is on the lap
        (timed_laps_left) -- which is a guess, and is flagged as one. iRacing uses
        32767 as "unlimited"; anything at or above that is not a count.
        """
        if laps_remain_ex is not None and 0 <= laps_remain_ex < 32767:
            return int(laps_remain_ex), False
        if ended and lap_time_s and lap_time_s > 0:
            return 1, True                    # the clock is out: this lap is the last
        if time_remain_s is not None and time_remain_s >= 0 and lap_time_s and lap_time_s > 0 \
                and time_remain_s < 86400 * 3:
            # for fuel, a lap too many is cheap and a lap too few is the end of the race: where a pace a little
            # quicker (TIMED_PACE_MARGIN) would mean one more lap, that lap is counted
            quick = dict(leader, lapS=leader["lapS"] * (1 - TIMED_PACE_MARGIN)) if leader and leader.get("lapS") else leader
            return max(timed_laps_left(time_remain_s, lap_pct, lap_time_s, leader),
                       timed_laps_left(time_remain_s, lap_pct, lap_time_s * (1 - TIMED_PACE_MARGIN), quick)), True
        return None, False

    def fuel(self, lap_completed, fuel_level, laps_remain_ex=None,
             time_remain_s=None, lap_time_s=None, on_pit_road=None, lap_pct=None, leader=None, ended=False) -> dict:
        out = {"fuelPerLap": None, "lapsOfFuel": None, "fuelPerLapSamples": len(self._fuel_per_lap_samples),
               "lapsRemaining": None, "lapsRemainingIsEstimate": False,
               "fuelToFinishL": None, "fuelMarginL": None, "fuelSavePerLapL": None}
        laps_left, est = self.laps_remaining(laps_remain_ex, time_remain_s, lap_time_s, lap_pct, leader, ended)
        out["lapsRemaining"], out["lapsRemainingIsEstimate"] = laps_left, est
        if lap_completed is None or fuel_level is None:
            return out
        if self._last_lap_completed is None:
            self._last_lap_completed = lap_completed
            self._fuel_at_lap_start = fuel_level
            # Connected part-way round: the lap now finishing was not watched
            # from its start, so what it used is only part of a lap.
            self._fuel_partial = True
            return out
        if on_pit_road:
            self._fuel_lap_pit = True
        if lap_completed > self._last_lap_completed:
            used = (self._fuel_at_lap_start or 0) - fuel_level
            one = lap_completed - self._last_lap_completed == 1
            # Ignore laps where we refueled or the counter jumped -- and laps
            # that touched pit road: an in-lap at the limiter burns less (2.21 L
            # against 2.44-2.51 in the Indianapolis capture), and counting it
            # would make the car look more economical than it is.
            partial = getattr(self, "_fuel_partial", False)
            self._fuel_partial = False
            if 0 < used < 25 and one and not self._fuel_lap_pit and not partial:
                self._fuel_per_lap_samples.append(used)
                self._fuel_per_lap_samples = self._fuel_per_lap_samples[-5:]
            if one:
                self._fuel_by_lap[lap_completed] = {"usedL": round(used, 2) if used > 0 and not partial else None,
                                                    "refuel": used < 0, "pit": self._fuel_lap_pit}
                if len(self._fuel_by_lap) > 300:
                    del self._fuel_by_lap[min(self._fuel_by_lap)]
            self._last_lap_completed = lap_completed
            self._fuel_at_lap_start = fuel_level
            self._fuel_lap_pit = bool(on_pit_road)
        elif lap_completed < self._last_lap_completed:
            # The counter went BACK (observed at Indianapolis: 33 -> 32 as the
            # session ended, and 0 -> -1 -> 0 on the grid). Start counting again
            # from here rather than crediting a lap that was not driven.
            self._last_lap_completed = lap_completed
            self._fuel_at_lap_start = fuel_level
            self._fuel_lap_pit = bool(on_pit_road)
            self._fuel_partial = True
        if self._fuel_per_lap_samples:
            fpl = sum(self._fuel_per_lap_samples) / len(self._fuel_per_lap_samples)
            out["fuelPerLap"] = round(fpl, 3)
            out["lapsOfFuel"] = round(fuel_level / fpl, 2) if fpl > 0 else None
            # Fuel to finish: the number an engineer actually says. Needs BOTH a
            # measured burn rate and a known race length; if either is missing we
            # say nothing rather than guess at one of them.
            if fpl > 0 and laps_left is not None:
                # With the position known, the distance left is the rest of this
                # lap plus the laps after it -- the same figure the fuel plan uses,
                # so the two panels never disagree about what is needed.
                left = laps_left - min(1.0, max(0.0, lap_pct)) if lap_pct is not None and lap_pct >= 0 else laps_left
                need = fpl * max(0.0, left)
                out["fuelToFinishL"] = round(need, 2)
                out["fuelMarginL"] = round(fuel_level - need, 2)
                # "You are short" is a problem; "save 0.12 a lap" is an
                # instruction. Same arithmetic, and it is the next thing said.
                if fuel_level < need and laps_left > 0:
                    out["fuelSavePerLapL"] = round((need - fuel_level) / laps_left, 3)
        out["fuelPerLapSamples"] = len(self._fuel_per_lap_samples)
        return out
