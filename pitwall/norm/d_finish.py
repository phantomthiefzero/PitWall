"""The end of a race: the checkered flag, the field coming in, the gaps held at the line."""
from __future__ import annotations



class Finish:
    def race_end(self, session_num, state, leader_laps, total, timed, remain, lcs=None) -> bool | None:
        """Has the leader taken the checkered flag? A lap race: the leader has the laps. Otherwise the first line
        crossing after the end came (a timed race's clock out, or the session state turning checkered, which iRacing
        does before the leader gets there). None while the end has not come (or for a session we cannot read).
        The leader is whoever gets there first: lcs (every car's laps completed) is read as well as the car iRacing
        has first, whose place only changes at a timing line. Le Mans, Nov 2026: car 10 passed car 4 for the lead
        on the last lap and crossed first, iRacing still had car 4 first, so the race ended at car 4's crossing and
        the real winner was never marked as having finished. The laps of the tick before it ended are kept
        (_end_snap): the cars that crossed in the tick it ended took the flag in it."""
        ends = getattr(self, "_race_ends", {})
        st = ends.setdefault(session_num, {})
        self._race_ends = ends
        lcs = {i: lc for i, lc in (lcs or {}).items() if lc is not None and lc >= 0}
        if lcs:
            most = max(lcs.values())
            leader_laps = most if leader_laps is None else max(leader_laps, most)
        prev = st.get("prev")
        st["prev"] = lcs
        done = False
        if total and leader_laps is not None and leader_laps >= total:
            done = True
        else:
            came = state in ("checkered", "cool_down") or (
                state == "racing" and timed and remain is not None and 0 <= remain <= 0.5)
            if came and st.get("lc") is None and leader_laps is not None:
                st["lc"] = leader_laps
            if st.get("lc") is not None and leader_laps is not None and leader_laps > st["lc"]:
                st["done"] = True
            done = bool(st.get("done")) or state == "cool_down"
            if not done and st.get("lc") is None:
                self._race_finished = {**getattr(self, "_race_finished", {}), session_num: None}
                return None
        if done and not st.get("snap_set"):
            st["snap_set"] = True
            self._end_snap = {"session": session_num, "laps": dict(prev) if prev else None}
        self._race_finished = {**getattr(self, "_race_finished", {}), session_num: done}
        return done

    FINISH_SETTLE_S = 2.0       # F2Time is read for this long after a car takes the flag (the timing catching up)
    # The race is over for everyone once every car still running has taken the flag. A car does not hold it up when it
    # has left (not in the world), gone into the pit lane, or stood still on track for FINISH_STUCK_S (stopped or
    # parked: iRacing ends its race there). However it goes, no longer than FINISH_WAIT_LAPS of the slowest running
    # car's lap after the winner (every car still running reaches the line within one of its laps), FINISH_WAIT_MIN_S
    # at least. Le Mans, Nov 2026, two classes: the GR86s' winner crossed 122 s after the overall winner and the last
    # car 176 s after; the podium was on air at 60 s, before the second class had a winner.
    FINISH_STUCK_S = 20.0
    FINISH_WAIT_LAPS = 1.25
    FINISH_WAIT_MIN_S = 45.0

    def _finish_all(self, ordered, fin, t) -> None:
        if not fin or t is None:
            return
        if getattr(self, "_fin_all", None) is not None:
            return                                   # once over, over
        seen = getattr(self, "_fin_seen", None)
        if seen is None:
            seen = self._fin_seen = {}
        win_t = min(f["t"] for f in fin.values())
        pending, paces = [], []
        for c in ordered:
            i = c["idx"]
            pct = c.get("pct")
            mv = seen.get(i)
            if pct is not None and pct >= 0 and (mv is None or abs(pct - mv[0]) > 1e-4):
                seen[i] = mv = (pct, t)
            if i in fin or c.get("surface") == "not_in_world" or c.get("onPitRoad") or (c.get("lapCompleted") or -1) < 0:
                continue
            if mv is not None and t - mv[1] >= self.FINISH_STUCK_S:
                continue                             # standing still: it is not coming to the line
            pending.append(i)
            p = self.pace(i)
            if p:
                paces.append(p)
        if not paces:
            paces = [p for p in (self.pace(c["idx"]) for c in ordered) if p]
        wait = max(self.FINISH_WAIT_MIN_S, self.FINISH_WAIT_LAPS * max(paces)) if paces else 300.0
        self._fin_left = len(pending)
        if not pending or t - win_t >= wait:
            self._fin_all = t

    def finish_state(self) -> dict:
        """After the winner's flag: has everyone finished, and how many are still to come to the line."""
        if not getattr(self, "_fin", None):
            return {"allFinished": None, "finishLeft": None}
        return {"allFinished": getattr(self, "_fin_all", None) is not None,
                "finishLeft": 0 if getattr(self, "_fin_all", None) is not None else getattr(self, "_fin_left", None)}

    def finish_gaps(self, ordered: list, state, t, cars=None) -> None:
        """After the checkered flag the gaps from where the cars are now mean nothing (the winner is on a cool-down
        lap while the rest still race to the line: the tower read +1:17.0 for a car on the lead lap). Each car's
        gap is taken as it takes the flag, from F2Time (iRacing's time behind the leader at the line) and the laps
        it finished down, and held; cars still racing to the line keep their live gaps.
        cars: every car, placed or not. A little after the winner's flag iRacing gives every car position -1 (Le Mans:
        the ordered list went empty 47 s after the winner, with 16 cars still on their last lap), so who has crossed
        the line is read from all of them."""
        allc = cars if cars is not None else ordered
        if state not in ("checkered", "cool_down") or t is None:
            self._fin, self._fin_lc = {}, {c["idx"]: c.get("lapCompleted") for c in allc}
            self._fin_seen, self._fin_all, self._fin_lead_laps = {}, None, None
            return
        fin, last = getattr(self, "_fin", {}), getattr(self, "_fin_lc", {})
        if not fin and allc:
            snap = (getattr(self, "_end_snap", None) or {}).get("laps")
            crossed = [c for c in allc if snap and c.get("lapCompleted") is not None and snap.get(c["idx"]) is not None
                       and c["lapCompleted"] > snap[c["idx"]]]
            if crossed:
                # the laps of the tick before the race ended: whoever crossed since took the flag (the winner first)
                last = dict(snap)
                self._fin_lead_laps = max(c["lapCompleted"] for c in crossed)
            elif ordered:
                # nothing to go by (the app started after the finish): iRacing's first car is the winner
                lead = ordered[0]
                fin[lead["idx"]] = {"t": t, "laps": 0, "gap": 0.0}
                self._fin_lead_laps = lead.get("lapCompleted")
        lead_laps = getattr(self, "_fin_lead_laps", None)
        for c in allc:
            lc, i = c.get("lapCompleted"), c["idx"]
            if i not in fin and lc is not None and last.get(i) is not None and lc > last[i]:
                first = not fin
                fin[i] = {"t": t, "laps": max(0, lead_laps - lc) if lead_laps is not None else 0,
                          "gap": 0.0 if first else None}
            f = fin.get(i)
            # at the line F2Time is fresh even where the checks for a running race (gapValid) do not pass
            if f is not None and f["laps"] == 0 and t - f["t"] <= self.FINISH_SETTLE_S and f["gap"] != 0.0 \
                    and (c.get("f2TimeS") or 0) > 0:
                f["gap"] = round(c["f2TimeS"], 3)
            if lc is not None:
                last[i] = lc
        self._fin, self._fin_lc = fin, last
        self._finish_all(allc, fin, t)
        prev, cls_lead, cls_prev, seen = None, {}, {}, {}
        for c in ordered:
            f, d = fin.get(c["idx"]), c.setdefault("derived", {})
            if f is None:
                # still on its last lap: measured against a winner already past the line, the live gap grows by the
                # winner's cool-down (P4 read 24.8 s for 13.9): iRacing's at its last crossing instead
                lc = c.get("lapCompleted")
                laps = max(0, lead_laps - 1 - lc) if lead_laps is not None and lc is not None and lc >= 0 else 0
                f = {"laps": laps, "gap": round(c["f2TimeS"], 3) if not laps and (c.get("f2TimeS") or 0) > 0 else None}
            else:
                d["finished"] = True
            seen[c["idx"]] = f
            d["liveGapS"], d["gapLaps"] = (None, f["laps"]) if f["laps"] else (f["gap"], None)
            p = prev and seen.get(prev["idx"])
            d["liveIntervalS"], d["intervalLaps"] = (
                (None, None) if p is None else
                (None, f["laps"] - p["laps"]) if f["laps"] != p["laps"] else
                (round(f["gap"] - p["gap"], 3) if f["gap"] is not None and p["gap"] is not None else None, None))
            k = c.get("classId")
            cl, cp = cls_lead.setdefault(k, f), cls_prev.get(k)
            d["liveClassGapS"], d["classGapLaps"] = (None, None) if cl is f else (
                (None, f["laps"] - cl["laps"]) if f["laps"] != cl["laps"] else
                (round(f["gap"] - cl["gap"], 3) if f["gap"] is not None and cl["gap"] is not None else None, None))
            d["liveClassIntervalS"], d["classIntervalLaps"] = (None, None) if cp is None else (
                (None, f["laps"] - cp["laps"]) if f["laps"] != cp["laps"] else
                (round(f["gap"] - cp["gap"], 3) if f["gap"] is not None and cp["gap"] is not None else None, None))
            # a car that left before the flag keeps a stale gap: one behind it can come out ahead of it
            # (the producer's field read "+-108.9"). No interval rather than a negative one.
            for k2 in ("liveIntervalS", "intervalLaps", "liveClassGapS", "classGapLaps", "liveClassIntervalS", "classIntervalLaps"):
                if d.get(k2) is not None and d[k2] < 0:
                    d[k2] = None
            cls_prev[k] = f
            prev = c
