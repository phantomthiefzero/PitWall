"""Laps left in a timed race, the fuel plan, theoretical best, against you."""
from __future__ import annotations

import math


TIMED_PACE_MARGIN = 0.015


def timed_laps_left(remain_s, lap_pct, lap_s, leader=None) -> int | None:
    """Laps still to run in a timed race, counting the one in progress (SessionLapsRemainEx's sense).

    A timed race ends at the leader's first crossing of the line after the clock runs out, and each car at its
    own next crossing after that (Indianapolis, Nov 2026, the driver's own file: over the line with 2.4 s on the
    clock, so one more lap, and the flag at the end of it). So the count depends on WHERE the car is on its lap:
    it reaches the line in (1 - lap_pct) laps' time, then once a lap. Dividing the clock by the lap time alone
    (what this used to do) counts as if the car were on the line now, and was a lap short for most of every lap:
    half way round lap 30 of that race, 129 s left at 85 s a lap, it said 2 laps (1.5 laps of fuel) with 2.5 laps
    still to drive.

    leader: {"pct", "lapS", "me", "white"} when known: the flag falls at the leader's first crossing after the
    clock (now, on its lap in progress, when the white flag is out); a car that reaches the line before the
    leader does has another lap to run. Without it the car's own crossings stand in (right for the leader and
    for cars near it, a lap short for a car just ahead of the leader on the road).
    An estimate: it assumes the pace holds."""
    if remain_s is None or not (lap_s and lap_s > 0):
        return None
    r = max(0.0, float(remain_s))
    p = min(1.0, max(0.0, lap_pct)) if lap_pct is not None and lap_pct >= 0 else 0.0
    own = (1.0 - p) * lap_s                                  # the car's next crossing, seconds from now

    def after_clock(first, lap):                             # first crossing after the clock runs out
        return first if first > r else first + (math.floor((r - first) / lap) + 1) * lap

    lp, ll = (leader or {}).get("pct"), (leader or {}).get("lapS")
    if leader and not leader.get("me") and lp is not None and lp >= 0 and ll and ll > 0:
        first = (1.0 - min(1.0, max(0.0, lp))) * ll
        flag = first if leader.get("white") else after_clock(first, ll)
        # the car's first crossing at or after the flag
        return 1 if own >= flag - 1e-6 else 1 + int(math.floor((flag - own) / lap_s - 1e-9)) + 1
    if leader and leader.get("white"):
        return 1                                             # the leader (you) is on the last lap
    return 1 + int(round((after_clock(own, lap_s) - own) / lap_s))


def fuel_plan(fuel_l, fpl, laps_left, laps_left_est, lap_pct, lap_no, tank_l,
              menu_fuel_l=None, menu_fuel_on=False) -> dict:
    """Stops, pit window and fuel to add, from the measured burn.

    laps_left counts the lap in progress (SessionLapsRemainEx's sense: 25 on lap
    1 of 25). The flag falls at the end of lap lap_no + laps_left - 1. Fuel still
    needed is the burn times the distance left: the rest of this lap plus the
    full laps after it. Assumes the burn stays where it has been.

    Window: the LATEST lap you can box is the last line you can reach on the fuel
    aboard; the EARLIEST is the first from which the remaining distance fits in
    the fuel you can carry over the planned stops (a tank per stop). Fuel to
    add is for boxing at the end of this lap, exact and with one lap spare,
    and for boxing at the latest lap.
    """
    out = {"tankL": None if tank_l is None else round(tank_l, 1), "lapsLeft": laps_left,
           "lapsLeftIsEstimate": bool(laps_left_est), "finishLap": None, "needL": None,
           "stops": None, "windowOpenLap": None, "windowCloseLap": None, "boxNow": False,
           "fillToFull": False, "addThisLapL": None, "addThisLapSpareL": None, "addAtCloseL": None,
           "addAtOpenL": None, "addAtOpenSpareL": None,
           "spareAtFlagL": None, "spareAtFlagLaps": None,
           "menuFuelL": menu_fuel_l if menu_fuel_on else None, "menuVsPlanL": None}
    if not (fpl and fpl > 0 and fuel_l is not None and laps_left is not None
            and lap_no is not None and lap_pct is not None and lap_pct >= 0):
        return out
    p = min(1.0, max(0.0, lap_pct))
    end_lap = lap_no + laps_left - 1
    out["finishLap"] = end_lap
    need = fpl * max(0.0, laps_left - p)
    out["needL"] = round(need, 2)
    short = need - fuel_l
    add_this = 0.0
    if short <= 0:
        out["stops"] = 0
        out["spareAtFlagL"] = round(-short, 2)
        out["spareAtFlagLaps"] = round(-short / fpl, 2)
    elif tank_l and tank_l > 0:
        stops = math.ceil(short / tank_l)
        j_max = math.floor(fuel_l / fpl - (1 - p) + 1e-9)       # full laps you can still finish
        close = lap_no + j_max if j_max >= 0 else None
        out["boxNow"] = close is None or close <= lap_no
        while True:
            open_ = end_lap - math.floor(stops * tank_l / fpl + 1e-9)
            if close is None or open_ <= close or stops > 20:
                break
            stops += 1
        fill = stops > 1
        out.update(stops=stops, windowOpenLap=max(open_, lap_no), windowCloseLap=close, fillToFull=fill)

        def fuel_at(x):                                          # aboard at the end of lap x
            return fuel_l - fpl * ((1 - p) + (x - lap_no))

        def add_at(x, spare):
            fa = max(0.0, fuel_at(x))
            room = tank_l - fa
            if fill:
                return max(0.0, room)
            return min(room, max(0.0, fpl * (end_lap - x + spare) - fa))

        first = max(open_, lap_no)
        out["addAtOpenL"] = round(add_at(first, 0), 1)
        out["addAtOpenSpareL"] = round(add_at(first, 1), 1)
        # Boxing this lap only gets you to the flag once the window is open;
        # before that the tank cannot hold what is left, so no amount is given.
        if open_ <= lap_no:
            add_this = add_at(lap_no, 0)
            out["addThisLapL"] = round(add_this, 1)
            out["addThisLapSpareL"] = round(add_at(lap_no, 1), 1)
        else:
            add_this = add_at(first, 0)
        if close is not None:
            out["addAtCloseL"] = round(add_at(close, 0), 1)
    if out["menuFuelL"] is not None and out["stops"] is not None:
        # Against the stop you would make next: this lap if the window is open,
        # else the lap it opens.
        out["menuVsPlanL"] = round(menu_fuel_l - add_this, 1)
    return out


def fuel_save(plan, fpl, fuel_l, tank_l, lap_pct, lane_loss_s=None, box_s=None) -> dict | None:
    """What-if: run to the flag with one stop fewer by saving fuel.

    Distance left is the plan's (laps left minus the part of this lap done). With
    one stop fewer, the most fuel you can have for it is what is aboard plus a
    full tank at each remaining stop (boxing nearly dry each time), so the burn
    per lap must come down to that over the distance: targetLpl, and savePct
    against the measured burn. The stop you skip is worth its lane loss plus the
    median stationary time, both measured from stops watched this session; spread
    over the laps left that is the lap time you can give up and still come out
    ahead (breakEvenPerLapS). Nothing here says what lifting costs you per lap:
    that is the driver's to find. None without stops to skip or a known tank."""
    if not plan or not plan.get("stops") or not (fpl and fpl > 0) or fuel_l is None \
            or not (tank_l and tank_l > 0) or plan.get("lapsLeft") is None or lap_pct is None:
        return None
    dist = max(0.0, plan["lapsLeft"] - min(1.0, max(0.0, lap_pct)))
    if dist <= 0:
        return None
    fewer = plan["stops"] - 1
    target = (fuel_l + fewer * tank_l) / dist
    save = 1.0 - target / fpl
    cost = None
    if lane_loss_s is not None:
        cost = lane_loss_s + (box_s or 0.0)
    return {"stopsNow": plan["stops"], "stopsIfSaved": fewer, "targetLpl": round(target, 3),
            "savePct": round(save * 100, 1), "saveLPerLap": round(fpl - target, 3),
            "stopCostS": None if cost is None else round(cost, 1),
            "breakEvenPerLapS": None if cost is None else round(cost / dist, 2)}


# Sector boundaries are interpolated between samples, so a sum of best sectors
# carries a few hundredths of error. Within this, it is still a theoretical best.
THEO_SLACK_S = 0.05


def theoretical_best(best_sectors, best_lap_s):
    """(theoreticalBestS, bestLapLeftS), either may be None.

    A theoretical best can never be slower than the real best lap: that lap is
    made of sectors too, so the best of each is at most its own. When the sum of
    the best sectors we have TIMED comes out slower, we have not seen enough to
    know it -- the best lap was set before we connected (practice recording: the
    sum was 5.86 s slower than iRacing's best), or one of its sectors went
    untimed after an off (race recording: 0.655 s slower). Then there is nothing
    honest to show, so nothing is. Within THEO_SLACK_S it is timing error, and
    the real best lap is the tighter bound on the truth.
    """
    if not best_sectors or any(v is None for v in best_sectors):
        return None, None
    theo = round(sum(best_sectors), 3)
    if not best_lap_s or best_lap_s <= 0:
        return theo, None                       # no best lap reported: nothing to check against
    if theo > best_lap_s + THEO_SLACK_S:
        return None, None
    theo = min(theo, round(best_lap_s, 3))
    return theo, round(best_lap_s - theo, 3)


def vs_you(c: dict, me: dict, their_pace, my_pace) -> dict:
    """Head-to-head: this car minus you. Negative = they are quicker."""
    def diff(a, b):
        return round(a - b, 3) if a is not None and b is not None and a > 0 and b > 0 else None
    tb, mb = (c.get("derived") or {}).get("bestSectorS") or [], (me.get("derived") or {}).get("bestSectorS") or []
    return {"lastLapS": diff(c.get("lastLapS"), me.get("lastLapS")),
            "bestLapS": diff(c.get("bestLapS"), me.get("bestLapS")),
            "paceS": diff(their_pace, my_pace),
            "bestSectorS": [diff(a, b) for a, b in zip(tb, mb)] if tb and mb and len(tb) == len(mb) else None}
