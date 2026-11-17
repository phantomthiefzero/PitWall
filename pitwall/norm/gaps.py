"""Gaps between cars: live (CarIdxEstTime), F2Time when it is real, battle pressure."""
from __future__ import annotations



# --------------------------------------------------------------------------- #
# Cars message (every car in the field; ~8 Hz)
# --------------------------------------------------------------------------- #


def f2_is_real(car: dict) -> bool:
    """True when this car's CarIdxF2Time is an actual gap rather than a placeholder.

    iRacing publishes 0.001 * (position - 1) until the car has a completed lap
    time; differencing that yields a fabricated ~1 ms interval. Requires both a
    present F2Time and a positive last lap time for the same car.
    """
    f2 = car.get("f2TimeS")
    if f2 is None or f2 < 0:
        return False
    last = car.get("lastLapS")
    if last is None or last <= 0:
        return False
    # The placeholder is 0.001 * (position - 1). Check it for P2 and back only:
    # the LEADER legitimately reports 0.0 (it is zero behind itself), which the
    # same formula also produces, and rejecting the leader would leave nothing to
    # measure the rest of the field against. A genuine sub-millisecond gap would
    # be suppressed by this, which is the right way to be wrong.
    pos = car.get("pos")
    if pos and pos >= 2 and abs(f2 - 0.001 * (pos - 1)) < 1e-6:
        return False
    return True


# --------------------------------------------------------------------------- #
# Live gaps, from CarIdxEstTime
# --------------------------------------------------------------------------- #
#
# CarIdxF2Time is iRacing's official gap, but it only moves when the timing
# catches up with a car: on a broadcast the tower sat on the same interval for
# most of a lap. CarIdxEstTime is live: the time this car's class takes to reach
# where the car is now, from the line. Two cars of one class at lap distances
# dA > dB are separated by the time B needs to reach A's spot, which is
# EstTime(A) - EstTime(B) on the same lap, plus a class lap for every whole lap.
# Across classes there is no shared time curve, so the fraction of a lap is
# scaled by the BEHIND car's class lap instead. Whole laps are kept separately,
# because "+1 lap" is what a broadcast says, not "+94.2".

def _dist(c):
    lap, pct = c.get("lap"), c.get("pct")
    if lap is None or pct is None or pct < 0 or lap < 0:
        return None
    return lap + pct


def gap_between(a: dict, b: dict):
    """Live time from car `b` back to car `a` (a ahead). Returns (seconds incl.
    whole laps, whole laps) or (None, None) when it cannot be worked out."""
    da, db = _dist(a), _dist(b)
    lap_b = b.get("classEstLapS") or ((b.get("bestLapS") or 0) > 0 and b.get("bestLapS")) or None
    if da is None or db is None or not lap_b or lap_b <= 0:
        return None, None
    d = da - db
    # b not behind a on track: they swapped since the order was scored (the order is iRacing's, from the line),
    # or nonsense. Reading it round the lap gave +1:17.0 for a car 0.6 s ahead (Summit Point): nothing instead.
    if d <= 0 or d > 200:
        return None, None
    laps = int(d + 1e-6)
    ea, eb = a.get("estTimeS"), b.get("estTimeS")
    same = a.get("classId") is not None and a.get("classId") == b.get("classId")
    # iRacing's CarIdxEstTime is 0.0 for every car while a replay plays (seen in a Talladega replay):
    # an estimate of exactly 0 is taken as missing, and the gap comes from the distance instead.
    if same and ea is not None and eb is not None and ea > 0 and eb > 0:
        frac_t = ea - eb if a["pct"] >= b["pct"] else (lap_b - eb) + ea
        if frac_t < 0 or frac_t > lap_b * 1.5:
            frac_t = (d - laps) * lap_b
    else:
        frac_t = (d - laps) * lap_b
    # The fractional part can cross a whole lap when the two cars straddle the
    # line; normalise so laps and seconds agree.
    if frac_t >= lap_b:
        frac_t -= lap_b
        laps += 1
    return round(laps * lap_b + frac_t, 3), laps


def live_order(ordered: list, live: bool, prev: dict | None = None) -> list:
    """The running order. Live (a race under way): by distance covered, lap plus
    lap fraction, so a pass shows when it happens. Cars without a position on
    track (not in the world) keep their scored slot. Not live: iRacing's order.
    Adds derived.livePos / liveClassPos either way.

    CarIdxLap and CarIdxLapDistPct can step at the line one update apart, which
    reads as a whole lap gained or lost for an instant; `prev` (per car, the last
    distance) takes out a jump of about a lap that the lap fraction did not make."""
    def dist(c):
        d = _dist(c)
        if d is None or c.get("surface") == "not_in_world":
            return None
        if prev is not None:
            last = prev.get(c["idx"])
            if last is not None:
                step = d - last
                if abs(step) > 0.5 and abs(step - round(step)) < 0.05:   # a whole lap, not a move
                    d -= round(step)
            prev[c["idx"]] = d
        return d

    if live:
        slots = [dist(c) for c in ordered]
        keyed = {c["idx"]: d for c, d in zip(ordered, slots)}
        movers = sorted((c for c in ordered if keyed[c["idx"]] is not None), key=lambda c: -keyed[c["idx"]])
        it = iter(movers)
        running = [next(it) if keyed[c["idx"]] is not None else c for c in ordered]
    else:
        running = list(ordered)
    cls = {}
    for k, c in enumerate(running, start=1):
        d = c.setdefault("derived", {})
        n = cls[c.get("classId")] = cls.get(c.get("classId"), 0) + 1
        d["livePos"], d["liveClassPos"] = k, n
    return running


def ref_gap(c: dict, me: dict, live: bool):
    """Seconds from the reference car `me` to car `c`, + when c is behind. Live: from
    where both are now (gap_between, whole laps included). Otherwise, or when either
    has no position on track: the difference of their time behind the leader."""
    if live:
        dc, dm = _dist(c), _dist(me)
        if dc is not None and dm is not None:
            if dc <= dm:
                g, _laps = gap_between(me, c)
                if g is not None:
                    return g
            else:
                g, _laps = gap_between(c, me)
                if g is not None:
                    return -g
    if c.get("gapValid") and me.get("gapValid"):
        return round(c["f2TimeS"] - me["f2TimeS"], 3)
    return None


def run_gaps(running: list):
    """The same live gaps along the running order (live_order), for PitWall's
    timing screen: derived.runIntervalS / runIntervalLaps (car ahead), runGapS /
    runGapLaps (leader), runClassIntervalS (car ahead in the class)."""
    leader = running[0] if running else None
    class_prev = {}
    prev = None
    for c in running:
        d = c.setdefault("derived", {})
        cp = class_prev.get(c.get("classId"))
        class_prev[c.get("classId")] = c
        d["runClassIntervalS"] = gap_between(cp, c)[0] if cp is not None else None
        if prev is None:
            d.update(runIntervalS=None, runIntervalLaps=None, runGapS=None, runGapLaps=None)
        else:
            d["runIntervalS"], d["runIntervalLaps"] = gap_between(prev, c)
            d["runGapS"], d["runGapLaps"] = gap_between(leader, c)
        prev = c


def live_gaps(ordered: list):
    """Adds derived.liveIntervalS / intervalLaps (to the car ahead in the order),
    liveGapS / gapLaps (to the overall leader), liveClassGapS / classGapLaps (to
    the class leader) and liveClassIntervalS / classIntervalLaps (to the car ahead
    in its class)."""
    leader = ordered[0] if ordered else None
    class_leader = {}
    class_prev = {}
    prev = None
    for c in ordered:
        d = c.setdefault("derived", {})
        cl = class_leader.setdefault(c.get("classId"), c)
        cp = class_prev.get(c.get("classId"))
        class_prev[c.get("classId")] = c
        # to the car ahead IN THIS CLASS, for a tower split by class
        d["liveClassIntervalS"], d["classIntervalLaps"] = gap_between(cp, c) if cp is not None else (None, None)
        if prev is None:
            d.update(liveIntervalS=None, intervalLaps=None, liveGapS=None, gapLaps=None)
        else:
            d["liveIntervalS"], d["intervalLaps"] = gap_between(prev, c)
            d["liveGapS"], d["gapLaps"] = gap_between(leader, c)
        if cl is c:
            d.update(liveClassGapS=None, classGapLaps=None)
        else:
            d["liveClassGapS"], d["classGapLaps"] = gap_between(cl, c)
        prev = c


# --------------------------------------------------------------------------- #
# Race length: laps or time, whichever ends it
# --------------------------------------------------------------------------- #

def _parse_laps(v):
    try:
        n = int(str(v).split()[0])
        return n if 0 < n < 10000 else None
    except (TypeError, ValueError):
        return None                       # "unlimited"


def _parse_secs(v):
    try:
        x = float(str(v).split()[0])
        return x if 0 < x < 7 * 86400 else None
    except (TypeError, ValueError):
        return None


PRESSURE_GAP_S = 1.0        # a gap of this much (class seconds) or more is no pressure from the gap alone
PRESSURE_RATE_S = 0.3       # closing this much a lap or more is full pressure from the closing rate
PRESSURE_ATTACK = 0.85      # going for it right now (attacks) is at least this


def battle_pressure(battles: list, attacks: list) -> None:
    """How hard the car behind is leaning on the car ahead, 0..1 per pair of a battle (`pressure`, beside gapsS): 60%
    from how small the gap is (in the class's seconds), 40% from how fast it is closing per lap; a chaser going for it
    right now (normalize attacks) is at least PRESSURE_ATTACK. A measure for a bar on the battle box, NOT a chance:
    nothing here knows whether the pass will come."""
    going = {tuple(a.get("cars") or [])[-2:] for a in attacks or []}
    for b in battles or []:
        k, out = b.get("scale") or 1.0, []
        for i, gap in enumerate(b.get("gapsS") or []):
            if gap is None:
                out.append(None)
                continue
            rate = (b.get("closingSPerLap") or [None] * (i + 1))[i]
            p = 0.6 * max(0.0, min(1.0, 1.0 - (gap / k) / PRESSURE_GAP_S)) + 0.4 * max(0.0, min(1.0, (rate or 0.0) / PRESSURE_RATE_S))
            if tuple(b["cars"][i:i + 2]) in going:
                p = max(p, PRESSURE_ATTACK)
            out.append(round(p, 2))
        b["pressure"] = out
