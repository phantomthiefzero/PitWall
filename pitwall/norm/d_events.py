"""Race news: flags, car flags, passes, session state."""
from __future__ import annotations

from .gaps import _dist
from .race import race_flags


class Events:
    # ---- events from the field ---------------------------------------------
    CLOSING_EVENT_GAIN = 0.3
    CLOSING_EVENT_GAP = 3.0

    # Before the start (cars leaving the pits for the grid) and after the flag
    # (cars driving back in) pit lane traffic is not a pit stop.
    PIT_EVENT_STATES = {"parade_laps", "racing", None}

    def field_events(self, cars: list, t, player_idx, stops: dict, state=None, session_num=None):
        ev = self._ev
        by = {c["idx"]: c for c in cars}
        me = by.get(player_idx)
        # pit in / out. A car loading into the world in its pit box, or leaving the
        # world (a disconnect, a tow, the garage), changes the pit-road flag without
        # driving anywhere: those are not pit stops and reset the car's state.
        pit_states = state in self.PIT_EVENT_STATES
        for i, c in by.items():
            onr = bool(c.get("onPitRoad"))
            surf = c.get("surface")
            was, was_surf = ev["pit"].get(i), ev["surf"].get(i)
            ev["surf"][i] = surf
            if surf == "not_in_world":
                ev["pit"][i] = None
                continue
            ev["pit"][i] = onr
            if was is None or was_surf == "not_in_world" or not pit_states:
                continue
            if onr and not was:
                towed = was_surf in ("on_track", "off_track") and surf == "in_pit_stall"
                self.event(t, "pit_in", idx=i, carLap=c.get("lapCompleted"), towed=towed,
                           pos=c.get("pos") or None, classPos=c.get("classPos") or None)
            elif was and not onr:
                d = c.get("derived") or {}
                s = stops.get(i)
                self.event(t, "pit_out", idx=i, stationaryS=d.get("lastStopS") if d.get("stopsSeen") else None,
                           laneLossS=s["laneLossS"] if s else None, stop=d.get("stopsSeen") or None,
                           pos=c.get("pos") or None, classPos=c.get("classPos") or None)
        # fastest laps, overall and per class. Seeded silently from what is
        # already on the board: only an improvement we watched is news.
        best_all = ev["best"].get("all")
        for c in cars:
            b = c.get("bestLapS")
            if not b or b <= 0:
                continue
            key = ("cls", c.get("classId"))
            cb = ev["best"].get(key)
            seeded = ev["bestSeeded"]
            if cb is None or b < cb - 1e-4:
                n = c.get("bestLapNum")
                # a first lap includes the start (or the out-lap): never a headline
                if seeded and cb is not None and not (isinstance(n, (int, float)) and n < 2):
                    overall = best_all is None or b < best_all - 1e-4
                    self.event(t, "fastest_lap", idx=c["idx"], lapS=round(b, 3),
                               classId=c.get("classId"), overall=overall,
                               lapNum=n if isinstance(n, (int, float)) and n > 0 else None,
                               prevS=round(cb, 3))
                ev["best"][key] = b
            if best_all is None or b < best_all - 1e-4:
                ev["best"]["all"] = best_all = b
        ev["bestSeeded"] = True
        # Leader of EVERY class, for the broadcast. lead_change below only covers
        # the player's class and needs a player car, which a spectator does not
        # have. Held for 5 s, the same as lead_change, so a pass and re-pass
        # inside one corner is not two headlines.
        for cid in {c.get("classId") for c in cars if c.get("classPos")}:
            leader_c = next((c["idx"] for c in cars if c.get("classPos") == 1 and c.get("classId") == cid), None)
            self._stable(ev, ("class_lead", cid), leader_c, t, 5.0,
                         lambda old, new, cid=cid: self.event(
                             t, "class_lead", idx=new, prevIdx=old, classId=cid, sessionNum=session_num,
                             replayT=None if t is None else round(t - 5.0, 2)))
        if t is not None:
            self._car_flag_events(cars, t, session_num)
            if state == "racing":
                self._pass_events(cars, t, session_num)
            else:
                ev["cpos"] = {}                  # passes count only while racing: start again after a caution
        if me is None:
            return
        # someone closing on you, or you on them (class rivals, once per approach)
        for i, c in by.items():
            if i == player_idx or c.get("classId") != me.get("classId"):
                continue
            d = c.get("derived") or {}
            tp, gain = d.get("toPlayerS"), d.get("youGainSPerLap")
            key = None
            if c.get("onPitRoad") or me.get("onPitRoad"):
                tp = None                    # a pit stop is not someone closing in
            if tp is not None and gain is not None:
                if 0 < tp < self.CLOSING_EVENT_GAP and gain <= -self.CLOSING_EVENT_GAIN:
                    key = "behind_closing"
                elif -self.CLOSING_EVENT_GAP < tp < 0 and gain >= self.CLOSING_EVENT_GAIN:
                    key = "you_closing"
            prevk = ev["closing"].get(i)
            if key and key != prevk:
                self.event(t, key, idx=i, ref=player_idx, gapS=round(abs(tp), 2), rateSPerLap=round(abs(gain), 2))
            if key or (gain is not None and abs(gain) < 0.1) or tp is None:
                ev["closing"][i] = key
        # your class position, once it has held for 3 s
        cp = me.get("classPos") or None
        self._stable(ev, "pos", cp, t, 3.0, lambda old, new: self.event(t, "position", idx=player_idx, fromPos=old, toPos=new))
        # class leader changes, held for 5 s
        leader = next((c["idx"] for c in cars if c.get("classPos") == 1 and c.get("classId") == me.get("classId")), None)
        self._stable(ev, "lead", leader, t, 5.0, lambda old, new: self.event(t, "lead_change", idx=new, prevIdx=old))

    # Per-car flags (CarIdxSessionFlags). A spectator sees these too (the user, Nov 2026):
    # a black flag (a penalty), the furled black (a warning), disqualified, the meatball.
    CAR_FLAG_EVENTS = ("black", "furled", "disqualify", "repair")

    def _car_flag_events(self, cars, t, session_num):
        """car_flag when a car gains one of CAR_FLAG_EVENTS (seeded silently on first
        sight, not repeated within FLAG_REPEAT_S), and penalty_served when a black flag
        goes while the car is still in the world and not disqualified."""
        ev = self._ev
        seen, at = ev.setdefault("cflag", {}), ev.setdefault("cflagAt", {})
        for c in cars:
            i = c["idx"]
            if c.get("surface") == "not_in_world":
                seen.pop(i, None)                # left the world: its flags go with it, not news
                continue
            fl = set(c.get("flags") or []) & set(self.CAR_FLAG_EVENTS)
            had = seen.get(i)
            seen[i] = fl
            if had is None:
                continue
            where = dict(carLap=c.get("lap"), pos=c.get("pos") or None, classPos=c.get("classPos") or None,
                         classId=c.get("classId"), sessionNum=session_num, replayT=round(t, 2))
            for f in self.CAR_FLAG_EVENTS:
                if f in fl and f not in had:
                    last = at.get((i, f))
                    if last is None or t - last >= self.FLAG_REPEAT_S:
                        self.event(t, "car_flag", idx=i, flag=f, **where)
                    at[(i, f)] = t
            if "black" in had and "black" not in fl and "disqualify" not in fl:
                self.event(t, "penalty_served", idx=i, **where)

    # Passes for position in class, for the replay list. A car's place in its class
    # (the running order, derived.liveClassPos) must hold PASS_HOLD_S before it counts,
    # and the car it went by must be on track, off pit road and within PASS_NEAR_LAP of
    # it: a rival pitting, towing or leaving is a place gained, not a pass.
    PASS_HOLD_S = 2.0
    PASS_NEAR_LAP = 0.03

    def _pass_events(self, cars, t, session_num):
        st = self._ev.setdefault("cpos", {})
        racing = {c["idx"]: c for c in cars
                  if c.get("surface") in ("on_track", "off_track") and not c.get("onPitRoad")}
        gained = []
        for c in cars:
            i = c["idx"]
            p = (c.get("derived") or {}).get("liveClassPos")
            if not p or i not in racing:
                st.pop(i, None)
                continue
            s = st.get(i)
            if s is None or s["cid"] != c.get("classId"):
                st[i] = {"cid": c.get("classId"), "val": p, "cand": p, "since": t}
                continue
            if p != s["cand"]:
                s["cand"], s["since"] = p, t
            if s["cand"] != s["val"] and t - s["since"] >= self.PASS_HOLD_S:
                old, s["val"] = s["val"], s["cand"]
                if s["val"] < old:
                    gained.append((i, old, s["val"], s["since"]))
        for i, old, new, since in gained:
            a = racing[i]
            da = _dist(a)
            if da is None:
                continue
            passed = []
            for j, b in racing.items():
                if j == i or b.get("classId") != a.get("classId"):
                    continue
                bp = (b.get("derived") or {}).get("liveClassPos")
                db = _dist(b)
                if bp and new < bp <= old and db is not None and 0 < da - db <= self.PASS_NEAR_LAP:
                    passed.append((bp, j))
            if passed:
                self.event(t, "pass", idx=i, passedIdx=[j for _, j in sorted(passed)], classId=a.get("classId"),
                           classPos=new, fromClassPos=old, pos=(a.get("derived") or {}).get("livePos"),
                           carLap=a.get("lap"), sessionNum=session_num, replayT=round(since, 2))

    @staticmethod
    def _stable(ev, key, value, t, hold, emit):
        st = ev["stable"].setdefault(key, {"val": value, "cand": value, "since": t})
        if value is None:
            return
        if value != st["cand"]:
            st["cand"], st["since"] = value, t
        if st["cand"] != st["val"] and t - st["since"] >= hold:
            old, st["val"] = st["val"], st["cand"]
            if old is not None:
                emit(old, st["val"])

    # ---- events from your car, the session and the weather ------------------
    FLAG_EVENTS = ("green", "yellow", "caution", "red", "white", "checkered", "black",
                   "repair", "disqualify", "blue", "furled")
    FLAG_REPEAT_S = 20.0
    QUIET_WARNINGS = {"pit_speed_limiter", "rev_limiter"}
    ENGINE_RUNNING_RPM = 500

    def state_events(self, t, session: dict, player: dict, weather: dict):
        ev = self._ev
        self._player_lap = player.get("lap")
        lc, inc_now = player.get("lapCompleted"), (player.get("incidents") or {}).get("my")
        if lc is not None and inc_now is not None:
            if self._inc_lap is None or lc < self._inc_lap[0]:
                self._inc_lap = (lc, inc_now)
            elif lc > self._inc_lap[0]:
                if lc - self._inc_lap[0] == 1:
                    self._inc_by_lap[lc] = inc_now - self._inc_lap[1]
                self._inc_lap = (lc, inc_now)
        if t is None:
            return
        # the race's flags as build_state read them (no checkered before the leader finishes; white and checkered
        # from the race itself when a spectator's SessionFlags carry neither)
        flags = set(session.get("raceFlags") if session.get("raceFlags") is not None
                    else race_flags(session.get("flags"), session.get("state")))
        if ev["flags"] is not None:
            for f in self.FLAG_EVENTS:
                if f in flags and f not in ev["flags"]:
                    last = ev["flagAt"].get(f)
                    if last is None or t - last >= self.FLAG_REPEAT_S:
                        self.event(t, "flag", flag=f)
                    ev["flagAt"][f] = t
        ev["flags"] = flags
        inc = (player.get("incidents") or {}).get("my")
        if inc is not None:
            if ev["inc"] is not None and inc > ev["inc"]:
                self.event(t, "incident", delta=inc - ev["inc"], total=inc, limit=self.incident_limit,
                           idx=self._player_idx, pct=None if player.get("lapPct") is None
                           else round(player["lapPct"], 4))
            ev["inc"] = inc
        warn = set(player.get("engineWarnings") or []) - self.QUIET_WARNINGS
        # With the engine off (in the box before the start, after a stall) the oil
        # and fuel pressure lights are on by design: not news. Out of the car, none is.
        if not player.get("isOnTrack"):
            warn = set()
        elif (player.get("rpm") or 0) < self.ENGINE_RUNNING_RPM:
            warn -= {"oil_pressure", "fuel_pressure"}
        if ev["warn"] is not None:
            new = sorted(warn - ev["warn"])
            if new:
                self.event(t, "engine", warnings=new)
        ev["warn"] = warn
        tow = player.get("towTimeS")
        if tow is not None:
            if ev["tow"] is not None and tow > 0 and not ev["tow"] > 0:
                self.event(t, "tow", towS=round(tow, 1))
            ev["tow"] = tow
        wet = weather.get("wetness")
        if wet == "unknown":                      # not reported yet (or no longer): not a change
            wet = None
        if wet is not None:
            if ev["wet"] is not None and wet != ev["wet"]:
                self.event(t, "track_wetness", value=wet, prev=ev["wet"])
            ev["wet"] = wet
        rain = weather.get("precipitationPct")
        if rain is not None:
            raining = rain > 0.005
            if ev["rain"] is not None and raining != ev["rain"]:
                self.event(t, "rain_start" if raining else "rain_stop", value=round(rain, 3))
            ev["rain"] = raining
        dw = weather.get("declaredWet")
        if dw is not None:
            if ev["declaredWet"] is not None and bool(dw) != ev["declaredWet"]:
                self.event(t, "declared_wet", value=bool(dw))
            ev["declaredWet"] = bool(dw)
