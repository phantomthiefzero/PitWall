"""Cars near each other: closing, meetings and traffic, class traffic, attacks, battles."""
from __future__ import annotations

import math


class Traffic:
    # Closing rate. A static gap says nothing about whether a pass is coming; the
    # RATE is what an engineer acts on ("gaining 0.4s a lap, catch him in seven").
    # Slope comes from least squares over the window rather than first-vs-last, so
    # one noisy sample cannot swing it.
    GAP_WINDOW_S = 90.0
    GAP_MIN_SPAN_S = 20.0
    GAP_MIN_SAMPLES = 4

    # An implausible rate is a wrong number wearing a confident face. Nothing in
    # road racing closes at tens of seconds per lap; anything that large means the
    # series was not continuous, so say nothing instead.
    GAP_MAX_RATE_S_PER_LAP = 15.0

    # Samples closer together than this add nothing to a 90 s trend but cost a
    # full least-squares pass each, on the driver's PC, beside the sim. Keeping
    # one every half second bounds the window at ~180 samples, and the slope is
    # only recomputed when a sample is added.
    GAP_SAMPLE_S = 0.5

    # _slope (least squares, None under 3 points) is defined once, further down: a second
    # definition here was silently replaced by that one.

    def closing(self, idx, t, interval_s, lap_time_s, ahead_idx=None) -> dict:
        out = {"closingSPerLap": None, "catchLaps": None}
        if idx is None or t is None:
            return out
        prev_ahead = self._gap_ahead.get(idx)
        if prev_ahead != ahead_idx:
            # The car ahead changed, so the interval now measures a different pair.
            # Carrying the old samples across would read the discontinuity as a
            # closing rate -- that is where +48 s/lap came from.
            self._gap_hist.pop(idx, None)
            self._gap_ahead[idx] = ahead_idx
        hist = self._gap_hist.setdefault(idx, [])
        if interval_s is None:
            hist.clear()          # gap not trustworthy: drop the thread, do not bridge it
            return out
        if hist and t - hist[-1][0] < self.GAP_SAMPLE_S:
            slope = self._gap_slope.get(idx)
        else:
            hist.append((t, interval_s))
            cutoff = t - self.GAP_WINDOW_S
            while hist and hist[0][0] < cutoff:
                hist.pop(0)
            slope = None
            if len(hist) >= self.GAP_MIN_SAMPLES and (hist[-1][0] - hist[0][0]) >= self.GAP_MIN_SPAN_S:
                slope = self._slope(hist)                      # gap-seconds per second
            self._gap_slope[idx] = slope
        if slope is None:
            return out
        if not lap_time_s or lap_time_s <= 0:
            return out
        closing = -slope * lap_time_s                                  # +ve = catching
        if abs(closing) < 0.02:                                        # below noise: call it level
            out["closingSPerLap"] = 0.0
            return out
        if abs(closing) > self.GAP_MAX_RATE_S_PER_LAP:
            hist.clear()                                               # series was not continuous
            self._gap_slope[idx] = None
            return out
        out["closingSPerLap"] = round(closing, 3)
        if closing > 0 and interval_s > 0:
            laps = interval_s / closing
            if laps < 999:
                out["catchLaps"] = round(laps, 1)
        return out

    # ---- spotter: who is around you on track --------------------------------
    NEAR_M = 150.0
    NEAR_HIST_S = 1.5

    def nearby(self, cars: list, player_idx, track_len_m, t) -> list:
        """Cars within NEAR_M metres of you along the track, ahead (+) or behind
        (-), from the lap-distance gap and the session's track length. Closing is
        how fast that distance is shrinking, from the last ~1.5 s; + = closing."""
        me = next((c for c in cars if c.get("idx") == player_idx), None)
        if me is None or not track_len_m or me.get("pct") is None or me["pct"] < 0 or t is None:
            self._near_hist.clear()
            return []
        out, seen = [], set()
        for c in cars:
            if c is me or c.get("pct") is None or c["pct"] < 0 or c.get("surface") == "not_in_world":
                continue
            dp = ((c["pct"] - me["pct"] + 0.5) % 1.0) - 0.5
            m = dp * track_len_m
            if abs(m) > self.NEAR_M:
                continue
            seen.add(c["idx"])
            h = self._near_hist.setdefault(c["idx"], [])
            if h and abs(m - h[-1][1]) > 50:          # a reset or a tow: start again
                h.clear()
            h.append((t, m))
            while h and t - h[0][0] > self.NEAR_HIST_S:
                h.pop(0)
            closing = None
            if len(h) >= 3 and h[-1][0] - h[0][0] >= 0.4:
                rate = self._slope(h)                  # metres per second, + = separation growing ahead
                if rate is not None:
                    closing = round(-rate if m > 0 else rate, 1)
            out.append({"idx": c["idx"], "meters": round(m, 1), "closingMs": closing,
                        "inPit": bool(c.get("onPitRoad"))})
        for i in list(self._near_hist):
            if i not in seen:
                del self._near_hist[i]
        return sorted(out, key=lambda x: abs(x["meters"]))[:8]

    def meetings(self, cars: list, player_idx, t=None, horizon=None) -> list:
        """Every car the followed car will meet on track within `horizon` seconds at
        both cars' current pace: slower cars it catches (you_catch) and faster ones
        that catch it (catches_you), with when and where. Smoothed over ~2 s so the
        marker settles instead of jittering with each update."""
        horizon = self.MEET_HORIZON_S if horizon is None else horizon
        me = next((c for c in cars if c.get("idx") == player_idx), None)
        if me is None or me.get("surface") != "on_track":
            return []
        um, pp = self._u(me), self.pace(player_idx)
        if um is None or not pp:
            return []
        out = []
        seen = set()
        for c in cars:
            if c.get("idx") == player_idx or c.get("surface") != "on_track" or c.get("onPitRoad"):
                continue
            uc, pc = self._u(c), self.pace(c["idx"])
            if uc is None or not pc:
                continue
            if pc > pp + self.TRAFFIC_MIN_DIFF_S:            # slower: the followed car catches it
                kind, d, rate = "you_catch", (uc - um) % 1.0, 1.0 / pp - 1.0 / pc
            elif pc < pp - self.TRAFFIC_MIN_DIFF_S:          # faster: it catches the followed car
                kind, d, rate = "catches_you", (um - uc) % 1.0, 1.0 / pc - 1.0 / pp
            else:
                continue
            tm = d / rate
            if tm > horizon:
                continue
            at_u = um + tm / pp                               # where the followed car will be then
            key = c["idx"]
            seen.add(key)
            prev = self._meet.get(key)
            if prev is not None and t is not None and prev[0] is not None and prev[3] == kind:
                k = 1.0 - math.exp(-max(0.0, t - prev[0]) / self.MEET_SMOOTH_S)
                pred = prev[1] - (t - prev[0])                # the last estimate, counted down
                tm_s = pred + k * (tm - pred) if abs(tm - pred) < 30 else tm
                du = ((at_u - prev[2] + 0.5) % 1.0) - 0.5
                at_u = prev[2] + k * du if abs(du) < 0.1 else at_u
                tm = max(0.0, tm_s)
            self._meet[key] = (t, tm, at_u % 1.0, kind)
            out.append({"idx": key, "kind": kind, "inS": round(tm, 1),
                        "inLaps": round(tm / pp, 1),
                        "atPct": round(self._pct_of_u(me.get("classId"), at_u), 3)})
        for k in list(self._meet):
            if k not in seen:
                del self._meet[k]
        return sorted(out, key=lambda x: x["inS"])

    def traffic(self, cars: list, player_idx, t=None) -> list:
        """The next few meetings (within TRAFFIC_HORIZON_S): the traffic list."""
        return [m for m in self.meetings(cars, player_idx, t) if m["inS"] <= self.TRAFFIC_HORIZON_S][:6]

    # ---- multiclass, for the broadcast ----------------------------------------
    # The traffic warning: when each class leader reaches the slower classes. The
    # same idea as meetings() (both cars' current pace, the lap-time fraction from
    # CarIdxEstTime), for every class leader instead of the followed car.
    CLASS_TRAFFIC_HORIZON_S = 60.0
    CLASS_TRAFFIC_PACK_S = 6.0

    def class_traffic(self, cars: list, t=None) -> list:
        """Each class leader's next meeting with a slower class: the first slower-class car it
        will reach at both cars' pace (aheadIdx, inS), and every slower-class car it reaches
        within CLASS_TRAFFIC_PACK_S after that (the pack: packN, packIdx). Only cars on track
        and out of the pit lane; within CLASS_TRAFFIC_HORIZON_S. Smoothed like meetings(), so
        the count runs down second by second instead of jumping with each update."""
        st = self.__dict__.setdefault("_ctraf", {})
        out, seen = [], set()
        for lead in cars:
            if lead.get("classPos") != 1 or lead.get("surface") != "on_track" or lead.get("onPitRoad"):
                continue
            ul, pl = self._u(lead), self.pace(lead["idx"])
            if ul is None or not pl:
                continue
            meets = []
            for c in cars:
                if c is lead or c.get("classId") == lead.get("classId") or c.get("surface") != "on_track" or c.get("onPitRoad"):
                    continue
                uc, pc = self._u(c), self.pace(c["idx"])
                if uc is None or not pc or pc <= pl + self.TRAFFIC_MIN_DIFF_S:
                    continue                              # not a slower car: never caught
                tm = ((uc - ul) % 1.0) / (1.0 / pl - 1.0 / pc)
                if tm <= self.CLASS_TRAFFIC_HORIZON_S:
                    meets.append((tm, c))
            if not meets:
                continue
            meets.sort(key=lambda x: x[0])
            tm, first = meets[0]
            pack = [c["idx"] for tt, c in meets if tt <= tm + self.CLASS_TRAFFIC_PACK_S]
            key = lead["idx"]
            seen.add(key)
            prev = st.get(key)
            if prev is not None and t is not None and prev[0] is not None and prev[2] == first["idx"]:
                k = 1.0 - math.exp(-max(0.0, t - prev[0]) / self.MEET_SMOOTH_S)
                pred = prev[1] - (t - prev[0])
                tm = max(0.0, pred + k * (tm - pred) if abs(tm - pred) < 30 else tm)
            st[key] = (t, tm, first["idx"])
            out.append({"idx": key, "classId": lead.get("classId"), "aheadIdx": first["idx"],
                        "aheadClassId": first.get("classId"), "inS": round(tm, 1),
                        "packN": len(pack), "packIdx": pack})
        for k in list(st):
            if k not in seen:
                del st[k]
        return sorted(out, key=lambda x: x["inS"])

    THROUGH_S = 3.0            # a faster class's car this close behind a slower class's car (seconds of track, at its pace)
    THROUGH_MIN_DIFF_S = 2.0   # and at least this much faster a lap: going past, not racing it

    def through_traffic(self, cars: list) -> list:
        """Faster-class cars about to go past slower-class cars right now (multiclass): for each, the slower cars up to
        THROUGH_S of track ahead of it at its pace (throughIdx, nearest first; behindS, the nearest). Not only
        the class leaders (class_traffic): a fight for P4 in a class carving through a pack is a story too. Cars on
        track and out of the pit lane only; the class's own fights are battles()."""
        if len({c.get("classId") for c in cars if c.get("classPos")}) < 2:
            return []
        out = []
        live = [c for c in cars if c.get("surface") == "on_track" and not c.get("onPitRoad") and c.get("classPos")]
        for f in live:
            uf, pf = self._u(f), self.pace(f["idx"])
            if uf is None or not pf:
                continue
            meets = []
            for c in live:
                if c is f or c.get("classId") == f.get("classId"):
                    continue
                uc, pc = self._u(c), self.pace(c["idx"])
                if uc is None or not pc or pc < pf + self.THROUGH_MIN_DIFF_S:
                    continue
                gap_u = (uc - uf) % 1.0
                if gap_u > 0.5:
                    continue                              # behind it: already through
                tm = gap_u * pf                           # seconds of track between them
                if tm <= self.THROUGH_S:
                    meets.append((tm, c["idx"]))
            if meets:
                meets.sort()
                out.append({"idx": f["idx"], "classId": f.get("classId"), "classPos": f.get("classPos"),
                            "throughIdx": [i for _, i in meets], "behindS": round(meets[0][0], 1)})
        return sorted(out, key=lambda x: (x["classPos"] or 99, x["behindS"]))

    ATTACK_WINDOW_S = 6.0      # how far back an attack is read: a run out of a corner, a tow down a straight
    ATTACK_GAP_S = 1.5         # (in the class's seconds) further back than this, no pass is coming
    ATTACK_GAIN_S = 0.15       # (in the class's seconds) gained on the car ahead in the window: going for it
    ATTACK_MIN_GAP_S = 0.3     # closer than this they are already fighting (battles: side by side, the move); in a
                               # superspeedway pack the gaps swing by this much all the time (Talladega)

    def attacks(self, cars: list, now, is_race: bool) -> list:
        """Who is going for the car ahead in its class right now: the live class gap (liveClassIntervalS, the tower's)
        over the last ATTACK_WINDOW_S against the same car ahead, in the class's seconds (gap_scale). The battles'
        closing rate is per lap, so a car that gains 0.4 s out of the last corner was not seen until the next lap;
        this sees it as it happens. Each chaser gets derived.attackGainS; the ones attacking are listed (cars ahead
        first, then the chaser), the best placed first. A race only."""
        hist = self.__dict__.setdefault("_atk", {})
        if not is_race or now is None:
            hist.clear()
            return []
        by_class: dict = {}
        for c in cars:
            if c.get("classPos") and c["classPos"] > 0:
                by_class.setdefault(c.get("classId"), []).append(c)
        out, seen = [], set()
        for cid, cs in by_class.items():
            cs.sort(key=lambda c: c["classPos"])
            k = self.gap_scale(cid)
            for a, b in zip(cs, cs[1:]):
                d = b.get("derived") or {}
                gap, laps = d.get("liveClassIntervalS"), d.get("classIntervalLaps")
                if a.get("onPitRoad") or b.get("onPitRoad") or gap is None or laps or gap < 0 \
                        or (a.get("derived") or {}).get("finished") or (b.get("derived") or {}).get("finished"):
                    continue
                seen.add(b["idx"])
                h = hist.setdefault(b["idx"], [])
                if h and h[-1][2] != a["idx"]:
                    h.clear()                             # another car ahead now (a pass, a stop): start again
                h.append((now, gap, a["idx"]))
                while len(h) > 1 and now - h[1][0] >= self.ATTACK_WINDOW_S:
                    h.pop(0)
                if now - h[0][0] < self.ATTACK_WINDOW_S * 0.6:
                    continue                              # not watched long enough to say
                gained = h[0][1] - gap
                d["attackGainS"] = round(gained, 3)
                if self.ATTACK_MIN_GAP_S * k <= gap <= self.ATTACK_GAP_S * k and gained >= self.ATTACK_GAIN_S * k:
                    out.append({"cars": [a["idx"], b["idx"]], "classId": cid, "gapS": round(gap, 3),
                                "gainedS": round(gained, 3), "windowS": round(now - h[0][0], 1),
                                "forClassPos": a["classPos"], "forPos": a.get("pos"), "scale": k})
        for i in list(hist):
            if i not in seen:
                del hist[i]
        return sorted(out, key=lambda x: (x["forClassPos"], x["forPos"] or 999))

    # Battles, for the broadcast. A pair of cars is in a battle when they are
    # next to each other IN THEIR CLASS and the gap between their F2Times is under
    # BATTLE_ENTER_S; it stays one until the gap opens past BATTLE_LEAVE_S, so a
    # gap hovering at 1.0 s does not make the graphic flicker on and off. Pairs
    # that touch become one group (a train). Overall-adjacent is the wrong test
    # in a multiclass race: a GT4 0.5 s behind a GT3 is traffic, not a battle.
    BATTLE_ENTER_S = 1.0
    BATTLE_LEAVE_S = 1.5
    # Those seconds are for a class going round at REF_SPEED_MS on average (a 90 s lap of a 4.5 km track: a GT3 at
    # a road course). A gap is a distance in time, so a slower class (an MX-5, a Formula Vee) is as close at more
    # seconds and a faster one (a GTP, an IndyCar, anything on an oval) at fewer: every class's time thresholds
    # scale with REF_SPEED_MS over its own average speed (class_scale), from SCALE_MIN to SCALE_MAX.
    REF_SPEED_MS = 50.0
    BATTLE_MAX_CARS = 4
    SCALE_MIN, SCALE_MAX = 0.6, 1.6

    def class_scale(self, cars: list, track_len) -> dict:
        """classId -> how many seconds of gap this class's second is (1.0 when unknown), and classId -> its lap.
        The class lap is iRacing's estimate (CarClassEstLapTime) where it has one, else its cars' best laps."""
        laps: dict = {}
        for c in cars:
            v = c.get("classEstLapS") or ((c.get("bestLapS") or 0) > 0 and c.get("bestLapS")) or None
            if v and v >= 5:
                laps.setdefault(c.get("classId"), []).append(v)
        lap = {k: sorted(v)[len(v) // 2] for k, v in laps.items()}
        scale = {}
        for k, v in lap.items():
            if track_len and track_len > 100:
                scale[k] = round(min(self.SCALE_MAX, max(self.SCALE_MIN, self.REF_SPEED_MS / (track_len / v))), 3)
        self._class_scale, self._class_lap = scale, lap
        return scale

    def gap_scale(self, class_id) -> float:
        return (getattr(self, "_class_scale", None) or {}).get(class_id, 1.0)

    def battles(self, cars: list, ahead_of: dict, is_race: bool) -> list:
        if not is_race:
            self._battle_pairs = set()
            return []
        live = getattr(self, "_battle_pairs", set())
        by_class: dict = {}
        for c in cars:
            if c.get("classPos") and c["classPos"] > 0:
                by_class.setdefault(c.get("classId"), []).append(c)
        links = []                         # (ahead, behind, gap)
        keep = set()
        for cls_cars in by_class.values():
            cls_cars.sort(key=lambda c: c["classPos"])
            for a, b in zip(cls_cars, cls_cars[1:]):
                # Both gaps must be ones we trust, and neither car may be in the
                # pit lane: a car in the lane is not racing the one beside it.
                if a.get("onPitRoad") or b.get("onPitRoad"):
                    continue
                # a car that has taken the checkered flag is on its cool-down lap, not racing the one behind
                if (a.get("derived") or {}).get("finished") or (b.get("derived") or {}).get("finished"):
                    continue
                # The live gap (CarIdxEstTime, live_gaps) where there is one, the same number the tower and the
                # battle box show: F2Time only refreshes when iRacing's timing catches up with a car, and a battle
                # read from it held two cars 8 s apart as "within a second" (Summit Point, Nov 2026).
                lv = (b.get("derived") or {}).get("liveClassIntervalS")
                laps = (b.get("derived") or {}).get("classIntervalLaps")
                if lv is not None and not laps:
                    gap = lv
                elif a.get("gapValid") and b.get("gapValid"):
                    gap = b["f2TimeS"] - a["f2TimeS"]
                else:
                    continue
                if gap < 0:
                    continue
                pair = (a["idx"], b["idx"])
                limit = (self.BATTLE_LEAVE_S if pair in live else self.BATTLE_ENTER_S) * self.gap_scale(a.get("classId"))
                if gap <= limit:
                    links.append((a, b, round(gap, 3)))
                    keep.add(pair)
        self._battle_pairs = keep
        groups: list[dict] = []
        tail = {}                          # idx of the last car in a group -> that group
        for a, b, gap in sorted(links, key=lambda x: (x[0].get("classId") or 0, x[0]["classPos"])):
            grp = tail.pop(a["idx"], None)
            if grp is None:
                grp = {"classId": a.get("classId"), "cars": [a["idx"]], "gapsS": [],
                       "scale": self.gap_scale(a.get("classId")),         # what "close" is in this class (director)
                       "closingSPerLap": [], "catchLaps": [],
                       "forClassPos": a["classPos"], "forPos": a.get("pos")}
                groups.append(grp)
            # The closing rate is the behind car's trend on the car directly
            # ahead OVERALL. It only describes this pair when the two are also
            # adjacent overall; otherwise it is about some other car, so null.
            same_pair = ahead_of.get(b["idx"]) == a["idx"]
            d = b.get("derived") or {}
            grp["cars"].append(b["idx"])
            grp["gapsS"].append(gap)
            grp["closingSPerLap"].append(d.get("closingSPerLap") if same_pair else None)
            grp["catchLaps"].append(d.get("catchLaps") if same_pair else None)
            tail[b["idx"]] = grp
        # A superspeedway pack (Talladega: 17 cars within a second, the box read "battle for P1, 17 cars" over the
        # last pair) is split at its biggest gaps into fights of BATTLE_MAX_CARS at most; a lone car is no fight.
        by = {c["idx"]: c for c in cars}

        def split(g):
            if len(g["cars"]) <= self.BATTLE_MAX_CARS:
                return [g] if len(g["cars"]) >= 2 else []
            gaps = g["gapsS"]
            k = max(range(1, len(gaps) - 1), key=lambda i: gaps[i]) if len(gaps) > 2 else 0
            def part(lo, hi):          # cars lo..hi-1, gaps lo..hi-2
                first = by.get(g["cars"][lo]) or {}
                return {**g, "cars": g["cars"][lo:hi], "gapsS": gaps[lo:hi - 1],
                        "closingSPerLap": g["closingSPerLap"][lo:hi - 1], "catchLaps": g["catchLaps"][lo:hi - 1],
                        "forClassPos": first.get("classPos", g["forClassPos"]), "forPos": first.get("pos", g["forPos"])}
            return split(part(0, k + 1)) + split(part(k + 1, len(g["cars"])))
        groups = [x for g in groups for x in split(g)]
        for grp in groups:
            grp["id"] = f"{grp['classId']}:{grp['cars'][0]}"
        groups.sort(key=lambda g: (g["forClassPos"], g["forPos"] or 999))
        return groups

    def battle_history(self, groups: list, t) -> None:
        """Adds swaps (places changed within the battle) and sinceS (how long these cars have been in it) to each
        battle, from the pairs in it (a pair's history survives the battle id changing as the cars swap)."""
        hist = getattr(self, "_bhist", {})
        if t is None:
            return
        for g in groups:
            swaps, since = 0, t
            for a, b in zip(g["cars"], g["cars"][1:]):
                k = frozenset((a, b))
                h = hist.get(k)
                if h is None or t - h["seen"] > self.BATTLE_FORGET_S:
                    h = hist[k] = {"since": t, "front": a, "swaps": 0, "seen": t}
                if h["front"] != a:
                    h["swaps"] += 1
                    h["front"] = a
                h["seen"] = t
                swaps += h["swaps"]
                since = min(since, h["since"])
            g["swaps"], g["sinceS"] = swaps, round(t - since, 1)
        for k in [k for k, h in hist.items() if t - h["seen"] > self.BATTLE_FORGET_S * 2]:
            del hist[k]
        self._bhist = hist
