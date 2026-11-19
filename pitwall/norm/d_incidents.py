"""Incidents predicted from telemetry (other cars' counts are hidden from a spectator)."""
from __future__ import annotations

from .common import incident_causes
from .gaps import _dist


class Incidents:
    # ---- incidents ------------------------------------------------------------
    # iRacing does not publish other cars' incident counts while the race runs
    # (observed: every count arrived only after the chequered flag), so the live
    # list is built from what every car's telemetry shows, and labelled as a
    # prediction. Signals, all per car, all at the live edge only:
    #   off track     CarIdxTrackSurface on_track -> off_track (iRacing's own flag)
    #   slowed        under 35% of the field's speed at that 0.5% of the lap, 1 s+
    #   stopped       under 5% of it for 2 s+
    #   backwards     lap distance running backwards: a car facing the wrong way
    #   nearby        another car within ~0.4% of a lap when it began, and whether
    #                 that car lost speed too (contact)
    #   tow / reset   gone from the world, or in its pit box without driving the lane
    #   repair flag   CarIdxSessionFlags gains "repair" (damage)
    # Signals for one car within a few seconds are one incident; when it settles
    # it is classified with iRacing's table (Sporting Code 3.5.1), the most
    # serious part counting, as iRacing counts concurrent incidents (3.5.2).
    # The counted points (session info) are still read: they appear after the
    # race, and are then compared with the prediction, car by car.
    INC_ANCHOR_WINDOW_S = 30.0
    INC_FALLBACK_S = 10.0
    TOW_GONE_S = 8.0                # out of the world this long, and not back on track: a tow or a reset
    INC_SETTLE_S = 3.0              # quiet this long after the last signal -> incident is complete
    INC_PREROLL_S = 0.0
    SLOW_RATIO = 0.35
    STOP_RATIO = 0.05
    SLOW_MIN_S = 1.0
    STOP_MIN_S = 2.0
    BACKWARDS_RATE = -0.003         # laps per second; a car rolling back, not jitter
    NEAR_LAPS = 0.004               # "alongside": 0.4% of a lap (~20 m on 5 km)
    NEAR_DIP = 0.75                 # the other car fell under 75% of the field's speed too
    SLOW_BINS = 200
    SLOW_BIN_SAMPLES = 40
    SLOW_BIN_MIN = 15
    LIKELY_POINTS = {"paved": {"off_track": 1, "spin": 2, "lost_speed": 2, "contact": 4, "tow": 0, "damage": 0},
                     "dirt": {"off_track": 1, "spin": 2, "lost_speed": 2, "contact": 2, "tow": 0, "damage": 0}}

    def _incst(self):
        return self.__dict__.setdefault("_inc", {
            "count": {}, "surf": {}, "prev": {}, "since": {}, "bins": {}, "open": {}, "last_rate": {},
            "flags": {}, "predicted": {}, "obs": {}, "next_id": 1, "tally_done": False})

    # Only green-flag running is watched. Before the start the grid sits still
    # and the pace laps crawl; under a full-course caution the field slows as
    # one; after the chequered flag finishers cruise. None of that is an incident.
    INC_STATES = ("racing", "checkered")
    INC_QUIET_FLAGS = {"caution", "caution_waving", "red"}

    def _inc_watching(self, st, cars, state, flags) -> bool:
        if state is None:                       # no session state given: always watch
            return True
        was = st.get("state")
        st["state"] = state
        if state == "racing" and was != "racing" and was != "checkered":
            st["bins"].clear()                  # pace-lap speeds are not normal running
        if state == "checkered" and was != "checkered":
            laps = {c["idx"]: c.get("lap") for c in cars}
            st["fin_lap"] = laps
            # the leader's flag is what ended the race: it has finished already
            top = max((v for v in laps.values() if v is not None), default=None)
            for i, v in laps.items():
                if top is not None and v == top:
                    laps[i] = v - 1
        if state not in self.INC_STATES or self.INC_QUIET_FLAGS & set(flags or ()):
            return False
        if self._caut.get("inf"):              # a caution read from the field (a replay has no flags)
            return False
        return True

    def incidents(self, cars: list, edge: dict, session_num, surface: str = "paved",
                  state: str | None = None, flags=None):
        st = self._incst()
        t_live, t_play, at_live = edge["tLive"], edge["tPlay"], edge["atLive"]
        self._counted(st, cars, t_live, session_num, surface)
        watching = self._inc_watching(st, cars, state, flags)
        if not at_live or t_play is None or not watching:
            st["prev"].clear()
            st["since"].clear()
            if not watching:
                st["open"].clear()              # half-seen signals from the caution / grid
            return
        if state == "checkered":                # a car that has taken the flag is cruising
            fin = st.get("fin_lap") or {}
            cars = [c for c in cars if c.get("lap") is None or fin.get(c["idx"]) is None
                    or c["lap"] <= fin[c["idx"]]]
        by = {c["idx"]: c for c in cars}
        rates = {}
        for c in cars:
            i, pct, surf = c["idx"], c.get("pct"), c.get("surface")
            was = st["surf"].get(i)
            st["surf"][i] = surf
            # tow / reset: in the box without the lane, or out of the world and back in the pits (or
            # gone for good). A car that vanishes for a second or two and comes back where it was is a
            # connection blink, not a tow: one Talladega driver blinked a dozen times in 10 minutes.
            gone = st.setdefault("gone", {})
            if was in ("on_track", "off_track") and surf == "not_in_world":
                gone[i] = t_play
            elif was in ("on_track", "off_track") and surf == "in_pit_stall":
                self._signal(st, c, t_play, "tow", session_num)
            if i in gone and surf != "not_in_world":
                t_gone = gone.pop(i)
                if surf == "in_pit_stall" or c.get("onPitRoad"):
                    self._signal(st, c, t_gone, "tow", session_num)
            elif i in gone and t_play - gone[i] >= self.TOW_GONE_S:
                self._signal(st, c, gone.pop(i), "tow", session_num)
            if was == "on_track" and surf == "off_track":
                self._signal(st, c, t_play, "off_track", session_num)
            # race control flags on the car
            fl = set(c.get("flags") or [])
            had = st["flags"].get(i)
            st["flags"][i] = fl
            if had is not None and "repair" in fl and "repair" not in had:
                self._signal(st, c, t_play, "damage", session_num)
            # speed in laps per second
            lap = c.get("lap")
            prev = st["prev"].get(i)
            moving = surf in ("on_track", "off_track") and not c.get("onPitRoad") and pct is not None \
                and pct >= 0 and lap is not None
            st["prev"][i] = (t_play, lap + pct) if moving else None
            if not moving or prev is None:
                continue
            dt = t_play - prev[0]
            if dt <= 0 or dt > 1.0:
                continue
            rate = (lap + pct - prev[1]) / dt
            if rate > 0.2 or rate < -0.05:          # a lap counter step or a reset, not motion
                continue
            rates[i] = (rate, int(pct * self.SLOW_BINS) % self.SLOW_BINS)
        meds = [self._median(b) for b in st["bins"].values() if len(b) >= self.SLOW_BIN_MIN]
        field = self._median(meds) if meds else None
        ratio = {}
        for i, (rate, b) in rates.items():
            hist = st["bins"].setdefault(b, [])
            med = self._median(hist) if len(hist) >= self.SLOW_BIN_MIN else None
            if med is None or field is None or med <= 0 or med < 0.3 * field:
                hist.append(rate)                     # still learning this part of the lap
                if len(hist) > self.SLOW_BIN_SAMPLES:
                    del hist[0]
                continue
            ratio[i] = rate / med
            c = by[i]
            if rate < self.BACKWARDS_RATE:
                self._signal(st, c, t_play, "backwards", session_num)
            slow_since = st["since"].setdefault(i, {})
            for kind, lim, hold in (("slowed", self.SLOW_RATIO, self.SLOW_MIN_S), ("stopped", self.STOP_RATIO, self.STOP_MIN_S)):
                if rate < lim * med:
                    t0 = slow_since.setdefault(kind, t_play)
                    if t_play - t0 < hold:
                        continue
                    if not slow_since.get(kind + "_sent"):
                        # once per stretch: a car parked for a minute is one
                        # incident, not one every few seconds (it was re-opened
                        # after every settle, thousands of times on a stopped grid)
                        slow_since[kind + "_sent"] = True
                        self._signal(st, c, t0, kind, session_num)
                    inc = st["open"].get(i)
                    if inc is not None and t_play - inc["t"] < self.INC_FALLBACK_S:
                        inc["last"] = max(inc["last"], t_play)   # still happening
                else:
                    slow_since.pop(kind, None)
                    slow_since.pop(kind + "_sent", None)
            if rate >= self.SLOW_RATIO * med:
                hist.append(rate)                     # only normal running defines normal
                if len(hist) > self.SLOW_BIN_SAMPLES:
                    del hist[0]
        # who was alongside each car that has just started an incident
        for i, inc in st["open"].items():
            if inc.get("near_checked") or i not in by:
                continue
            inc["near_checked"] = True
            me = by[i]
            dm = _dist(me)
            if dm is None:
                continue
            for j, c in by.items():
                dj = _dist(c)
                if j == i or dj is None or c.get("onPitRoad"):
                    continue
                if abs(dj - dm) <= self.NEAR_LAPS:
                    inc["near"].add(j)
                    if ratio.get(j, 1.0) < self.NEAR_DIP:
                        inc["near_dip"].add(j)
        # close what has settled
        for i in [i for i, inc in st["open"].items() if t_play - inc["last"] >= self.INC_SETTLE_S]:
            self._close(st, st["open"].pop(i), surface)

    def _signal(self, st, c, t, kind, session_num):
        i = c["idx"]
        self.__dict__.setdefault("_dirty", set()).add(i)      # this lap is not a clean one (cleanStreak)
        inc = st["open"].get(i)
        if inc is None:
            inc = {"idx": i, "t": t, "last": t, "signals": set(), "near": set(), "near_dip": set(),
                   "lap": c.get("lap"), "pct": c.get("pct"), "sessionNum": session_num,
                   "classId": c.get("classId")}
            st["open"][i] = inc
        if kind in inc["signals"] and kind in ("slowed", "stopped", "backwards"):
            inc["last"] = max(inc["last"], t)
            return
        inc["signals"].add(kind)
        inc["t"] = min(inc["t"], t)
        inc["last"] = max(inc["last"], t)

    def _close(self, st, inc, surface):
        sig = inc["signals"]
        table = self.LIKELY_POINTS.get(surface, self.LIKELY_POINTS["paved"])
        # Another car that was alongside AND lost speed with it is contact.
        partners = sorted(j for j in inc["near_dip"] if j in st["open"] or self._recent_partner(st, j, inc))
        parts = []
        if partners or (inc["near_dip"] and ({"slowed", "stopped"} & sig)):
            parts.append("contact")
        if "backwards" in sig or ({"off_track"} <= sig and ({"slowed", "stopped"} & sig)):
            parts.append("spin")
        elif {"slowed", "stopped"} & sig:
            parts.append("lost_speed")
        if "off_track" in sig and not parts:
            parts.append("off_track")
        if "tow" in sig:
            parts.append("tow")
        if "damage" in sig:
            parts.append("damage")
        if not parts:
            return
        kind = max(parts, key=lambda k: (table.get(k, 0), k == "contact"))
        pts = max(table.get(k, 0) for k in parts)
        confidence = ("high" if kind == "off_track" or "backwards" in sig or len(inc["near_dip"]) else
                      "medium" if "stopped" in sig or "damage" in sig else "low")
        iid = st["next_id"]
        st["next_id"] += 1
        st["predicted"][inc["idx"]] = st["predicted"].get(inc["idx"], 0) + pts
        st["obs"].setdefault(inc["idx"], []).append({"t": inc["t"], "kind": kind})
        del st["obs"][inc["idx"]][:-20]
        self.event(inc["t"], "race_incident", id=iid, idx=inc["idx"], what=kind, parts=parts,
                   likelyPoints=pts, confidence=confidence, signals=sorted(sig),
                   near=sorted(inc["near"]), contactWith=sorted(inc["near_dip"]),
                   lap=inc["lap"], pct=round(inc["pct"] or 0.0, 4), classId=inc["classId"],
                   sessionNum=inc["sessionNum"], replayT=round(inc["t"], 2), endT=round(inc["last"], 2),
                   surface=surface)

    def _recent_partner(self, st, j, inc):
        return any(abs(o["t"] - inc["t"]) <= 3.0 for o in st["obs"].get(j, []))

    def predicted_incidents(self) -> dict:
        return dict(self._incst()["predicted"])

    def _counted(self, st, cars, t_live, session_num, surface):
        """iRacing's own counts. Hidden from a spectator during the race, they
        arrive in one step after the finish; when they do, compare each car's
        official total with what was predicted for it."""
        rises = []
        for c in cars:
            i, n = c["idx"], c.get("incidents")
            if not isinstance(n, (int, float)) or n < 0 or t_live is None:
                continue
            prev = st["count"].get(i)
            st["count"][i] = n
            if prev is None or n <= prev:
                continue
            rises.append((c, int(n - prev), int(n)))
        if not rises:
            return
        # A step for most of the field at once is the post-race publication, not
        # incidents happening now: report it as the official tally.
        if len(rises) >= max(3, len(cars) // 3) or st["tally_done"]:
            pred = st["predicted"]
            self.event(t_live, "official_incidents", sessionNum=session_num, cars=[
                {"idx": c["idx"], "official": int(c.get("incidents") or 0), "predicted": pred.get(c["idx"], 0)}
                for c in cars if isinstance(c.get("incidents"), (int, float)) and c["incidents"] >= 0])
            st["tally_done"] = True
            return
        for c, pts, total in rises:
            i = c["idx"]
            anchor = next((o for o in reversed(st["obs"].get(i, []))
                           if t_live - self.INC_ANCHOR_WINDOW_S <= o["t"] <= t_live + 1), None)
            self.event(t_live, "car_incident", idx=i, points=pts, total=total,
                       driverTotal=c.get("driverIncidents"), limit=self.incident_limit,
                       lap=c.get("lap"), sessionNum=session_num,
                       replayT=round(anchor["t"], 2) if anchor else round(t_live - self.INC_FALLBACK_S, 2),
                       anchor=anchor["kind"] if anchor else "reported", surface=surface,
                       **incident_causes(pts, surface))
