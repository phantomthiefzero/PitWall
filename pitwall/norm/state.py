"""The state message (your car, weather, clock) and the cars message; RefView for a followed car."""
from __future__ import annotations

import re
from ..sources import TelemetrySource
from .common import (
    CAMERA_STATE_BITS, CAR_LEFT_RIGHT, ENGINE_WARNING_BITS, FLAG_BITS, PIT_SV_BITS, SCHEMA_VERSION,
    SESSION_STATE, SKIES, TRACK_SURFACE, TRACK_WETNESS, _arr, _compound, _num, decode_bits, live_edge,
    surface_kind,
)
from .fuel import fuel_plan, fuel_save, theoretical_best, vs_you
from .gaps import (
    _parse_laps, _parse_secs, battle_pressure, f2_is_real, live_gaps, live_order, ref_gap, run_gaps,
)
from .race import _is_race, race_flags, race_length, results_positions, strength_of_field
from .deriver import Deriver


# --------------------------------------------------------------------------- #
# State message (player car + weather + session clock; ~15 Hz)
# --------------------------------------------------------------------------- #

_DC_VARS = {
    "brakeBias": "dcBrakeBias", "abs": "dcABS", "tractionControl": "dcTractionControl",
    "antiRollFront": "dcAntiRollFront", "antiRollRear": "dcAntiRollRear",
    "fuelMixture": "dcFuelMixture", "weightJackerRight": "dcWeightJackerRight",
    "weightJackerLeft": "dcWeightJackerLeft", "wingFront": "dcWingFront", "wingRear": "dcWingRear",
    "throttleShape": "dcThrottleShape", "diffEntry": "dcDiffEntry", "diffMiddle": "dcDiffMiddle",
    "diffExit": "dcDiffExit", "boostLevel": "dcBoostLevel", "mguk": "dcMGUKDeployMode",
    "dashPage": "dcDashPage", "launchRPM": "dcLaunchRPM",
}


class RefView:
    """A reference car chosen by one viewer, kept apart from the shared stream.

    Everything measured FROM a reference car keeps a little history of its own:
    gap trends, the undercut series, nearby closing rates. That is exactly the
    state Deriver.set_ref clears, and it is all a RefView owns. Everything else
    -- lap history, pace, pit stops, the session's derived knowledge -- is read
    from the shared Deriver. So a viewer following #23 runs the same tested
    Deriver methods as the shared stream (called with the RefView as self),
    without moving the shared reference or anyone else's.
    """
    OWN = frozenset(("_tp_hist", "_tp_ref", "_tp_slope", "_uc", "_uc_inpit", "_near_hist", "ref_idx",
                     "_gaps", "_gaps_seq", "_meet"))

    def __init__(self, base: "Deriver", ref_idx: int):
        object.__setattr__(self, "_base", base)
        self._tp_hist, self._tp_ref, self._tp_slope = {}, {}, {}
        self._uc, self._uc_inpit, self._near_hist = {}, {}, {}
        self._meet = {}                           # smoothed meetings, per car met
        self.ref_idx = ref_idx
        self._gaps, self._gaps_seq = {}, None     # gap series, rebuilt only when a lap is added

    def __getattr__(self, name):          # only reached for what is not owned here
        return getattr(self._base, name)

    def __setattr__(self, name, value):
        # A Deriver method that starts writing other state would silently fork
        # it per viewer; fail loudly instead, so it is added to OWN on purpose.
        if name not in self.OWN:
            raise AttributeError(f"RefView does not own {name!r}; add it to RefView.OWN if it is per-reference")
        object.__setattr__(self, name, value)


# The per-car fields that are measured from the reference car. A viewer's
# RefView replaces exactly these on top of the shared cars message.
REF_CAR_FIELDS = ("toPlayerS", "youGainSPerLap", "youCatchLaps", "catchesYouLaps", "vsYou")


def relative_to(view: RefView, cars_msg: dict, session_msg: dict | None, now) -> dict:
    """Everything in a cars message that is measured from the reference car,
    measured from view.ref_idx instead. Mirrors build_cars step for step (the
    test test_a_chosen_car_matches_the_shared_stream_for_the_same_car holds it
    to that), from the shared message's ref-independent fields."""
    cars = cars_msg.get("cars") or []
    shared = cars_msg.get("derived") or {}
    shared_ref = shared.get("ref") or {}
    is_race = bool(shared.get("isRace"))
    ref = view.ref_idx
    by_idx = {c["idx"]: c for c in cars}
    me = by_idx.get(ref)
    out = {"type": "refview", "ref": ref, "cars": {},
           "derived": {"ref": {"idx": ref if me is not None else None, "source": "chosen" if me is not None else None,
                               "own": shared_ref.get("own"), "camera": shared_ref.get("camera")},
                       "rejoin": None, "undercut": [], "traffic": [], "meetings": [], "nearby": [], "strategy": None},
           "gaps": {}}
    if me is None:
        return out
    # toPlayerS, as build_cars computes it, but from this car
    per = {}
    live = bool(shared.get("liveTiming"))
    for c in cars:
        tp = None
        if is_race and c is not me:
            tp = ref_gap(c, me, live)
        per[c["idx"]] = {"toPlayerS": tp}
    my_lap = Deriver.pace(view, ref)
    if my_lap is None and me.get("lastLapS") and me["lastLapS"] > 0:
        my_lap = me["lastLapS"]
    # the reference car has nothing relative to itself, as in the shared stream
    per[ref].update({"youGainSPerLap": None, "youCatchLaps": None, "catchesYouLaps": None, "vsYou": None})
    for c in cars:
        if c is me:
            continue
        per[c["idx"]].update(Deriver.to_player_trend(view, c["idx"], now, per[c["idx"]]["toPlayerS"],
                                                     my_lap, ref))
    # the cars as this viewer sees them: shared fields, relative ones replaced
    vcars = [{**c, "derived": {**(c.get("derived") or {}), **per[c["idx"]]}} for c in cars]
    vme = next(v for v in vcars if v["idx"] == ref)
    my_pace = Deriver.pace(view, ref)
    for v in vcars:
        if v is not vme:
            per[v["idx"]]["vsYou"] = v["derived"]["vsYou"] = vs_you(v, vme, Deriver.pace(view, v["idx"]), my_pace)
    track_len = track_length_m(session_msg)
    meet = Deriver.meetings(view, vcars, ref, now)
    out["cars"] = {str(i): {k: e.get(k) for k in REF_CAR_FIELDS} for i, e in per.items()}
    out["derived"].update({
        "rejoin": Deriver.rejoin(view, vcars, ref) if is_race else None,
        "undercut": Deriver.undercut(view, vcars, ref, now) if is_race else [],
        "traffic": [m for m in meet if m["inS"] <= Deriver.TRAFFIC_HORIZON_S][:6],
        "meetings": meet,
        "nearby": Deriver.nearby(view, vcars, ref, track_len, now),
        "strategy": Deriver.strategy(view, vcars, ref, shared.get("race"), shared.get("caution"), shared_ref.get("own"))
        if is_race else None,
    })
    # Rebuilt only when a lap has been added; it was rebuilt at every update.
    if view._gaps_seq != view._base._last_lap_seq:
        view._gaps, view._gaps_seq = Deriver.ref_gaps(view), view._base._last_lap_seq
    out["gaps"] = view._gaps
    return out


def track_length_m(session_msg) -> float | None:
    """TrackLength from the session info ("5.10 km", "3.16 mi")."""
    raw = str(((session_msg or {}).get("track") or {}).get("TrackLength") or "")
    m = re.match(r"\s*([\d.]+)\s*(km|mi)", raw)
    if not m:
        return None
    v = float(m.group(1))
    return v * 1000.0 if m.group(2) == "km" else v * 1609.344


def _delta(g, var: str, key: str) -> dict:
    """One LapDelta* channel, gated on its own validity flag.

    Returns {key+"S": seconds or None, key+"Rate": seconds per second or None}.
    The rate (_DD) is how fast the gap to the reference is changing right now,
    which is what tells a driver whether the current lap is still coming to them.
    """
    ok = g(var + "_OK")
    if not ok:
        return {key + "S": None, key + "Rate": None}
    return {key + "S": _num(g(var)), key + "Rate": _num(g(var + "_DD"))}


def build_state(src: TelemetrySource, deriver: Deriver, session_msg: dict | None = None) -> dict:
    g = src.get
    flags = g("SessionFlags")
    warnings = g("EngineWarnings")
    pit_sv = g("PitSvFlags")
    wet = g("TrackWetness")
    surf = g("PlayerTrackSurface")
    st = g("SessionState")
    tires = {}
    for c in ("LF", "RF", "LR", "RR"):
        tires[c] = {
            "carcassTempC": [_num(g(f"{c}temp{p}")) for p in ("CL", "CM", "CR")],
            "wearPct": [_num(g(f"{c}wear{p}")) for p in ("L", "M", "R")],
            "coldPressureKPa": _num(g(f"{c}coldPressure")),
            "pressureKPa": _num(g(f"{c}pressure")),
        }
    player = {
        "carIdx": _num(g("PlayerCarIdx")),
        # Which car the camera is on. While spectating, PlayerCarIdx is the
        # spectator slot (63 observed) and carries no car of its own, so the
        # camera car is the only thing that identifies what is being watched.
        "camCarIdx": _num(g("CamCarIdx")),
        "speedMs": _num(g("Speed")), "rpm": _num(g("RPM")), "gear": _num(g("Gear")),
        "throttle": _num(g("Throttle")), "brake": _num(g("Brake")), "clutch": _num(g("Clutch")),
        "steerRad": _num(g("SteeringWheelAngle")),
        "fuelL": _num(g("FuelLevel")), "fuelPct": _num(g("FuelLevelPct")),
        "fuelUsePerHourKg": _num(g("FuelUsePerHour")),
        "waterTempC": _num(g("WaterTemp")), "oilTempC": _num(g("OilTemp")),
        "oilPressBar": _num(g("OilPress")), "fuelPressBar": _num(g("FuelPress")),
        "voltage": _num(g("Voltage")),
        "engineWarnings": decode_bits(warnings, ENGINE_WARNING_BITS),
        "engineWarningsRaw": _num(warnings),
        "lap": _num(g("Lap")), "lapCompleted": _num(g("LapCompleted")),
        "lapPct": _num(g("LapDistPct")), "lapDistM": _num(g("LapDist")),
        "lastLapS": _num(g("LapLastLapTime")), "bestLapS": _num(g("LapBestLapTime")),
        "currentLapS": _num(g("LapCurrentLapTime")),
        # Every LapDelta* ships an _OK companion saying whether the value means
        # anything yet, and a _DD giving how fast the delta is itself moving.
        # Ignoring _OK meant an invalid delta rendered as a confident "+0.000"
        # -- iRacing reports 0.0 with _OK false before there is a reference lap.
        **_delta(g, "LapDeltaToBestLap", "deltaToBest"),
        **_delta(g, "LapDeltaToSessionBestLap", "deltaToSessionBest"),
        **_delta(g, "LapDeltaToOptimalLap", "deltaToOptimal"),
        **_delta(g, "LapDeltaToSessionOptimalLap", "deltaToSessionOptimal"),
        "onPitRoad": _num(g("OnPitRoad")), "inPitStall": _num(g("PlayerCarInPitStall")),
        "pitstopActive": _num(g("PitstopActive")),
        "trackSurface": TRACK_SURFACE.get(surf, str(surf)) if surf is not None else None,
        "position": _num(g("PlayerCarPosition")), "classPosition": _num(g("PlayerCarClassPosition")),
        "incidents": {"my": _num(g("PlayerCarMyIncidentCount")),
                      "team": _num(g("PlayerCarTeamIncidentCount")),
                      "driver": _num(g("PlayerCarDriverIncidentCount"))},
        "towTimeS": _num(g("PlayerCarTowTime")),
        "pitRepairLeftS": _num(g("PitRepairLeft")), "pitOptRepairLeftS": _num(g("PitOptRepairLeft")),
        "pitService": {
            "flags": decode_bits(pit_sv, PIT_SV_BITS), "flagsRaw": _num(pit_sv),
            "fuel": _num(g("PitSvFuel")),
            "pressuresKPa": {c: _num(g(f"PitSv{c}P")) for c in ("LF", "RF", "LR", "RR")},
            "tireCompound": _num(g("PitSvTireCompound")),
        },
        "dc": {k: _num(g(v)) for k, v in _DC_VARS.items()},
        "tires": tires,
        "isOnTrack": _num(g("IsOnTrack")), "isInGarage": _num(g("IsInGarage")),
        "isReplayPlaying": _num(g("IsReplayPlaying")),
        # The driver's machine and connection. A stint that fell apart at lap 12
        # is often the frame rate collapsing or the link degrading rather than
        # the driver, and none of this was previously surfaced.
        "rig": {
            "fps": _num(g("FrameRate")),
            "cpuPct": _num(g("CpuUsageFG")),
            "gpuPct": _num(g("GpuUsage")),
            "latencyS": _num(g("ChanAvgLatency")),
            "qualityPct": _num(g("ChanQuality")),
            "clockSkewS": _num(g("ChanClockSkew")),
        }, "replayFrame": _num(g("ReplayFrameNum")),
        "p2p": {"status": _num(g("PushToPass")), "count": _num(g("P2P_Count"))},
        # Tyre sets (your car): what has been used and what the rules leave.
        "tireSets": {"used": _num(g("TireSetsUsed")), "available": _num(g("TireSetsAvailable")),
                     "dryLimit": _num(g("PlayerCarDryTireSetLimit")),
                     "frontUsed": _num(g("FrontTireSetsUsed")), "rearUsed": _num(g("RearTireSetsUsed")),
                     "leftUsed": _num(g("LeftTireSetsUsed")), "rightUsed": _num(g("RightTireSetsUsed")),
                     "frontAvailable": _num(g("FrontTireSetsAvailable")),
                     "rearAvailable": _num(g("RearTireSetsAvailable")),
                     "perTireUsed": {c: _num(g(f"{c}TiresUsed")) for c in ("LF", "RF", "LR", "RR")},
                     "perTireAvailable": {c: _num(g(f"{c}TiresAvailable")) for c in ("LF", "RF", "LR", "RR")}},
        "fastRepairs": {"used": _num(g("PlayerFastRepairsUsed")), "available": _num(g("FastRepairAvailable"))},
        # Team events: iRacing's own driver-change bookkeeping for your team (never yet
        # seen in a real team race: see VARIABLES_TO_VERIFY.md).
        "driverChange": {"lapStatus": _num(g("DCLapStatus")), "driversSoFar": _num(g("DCDriversSoFar"))},
        # iRacing's spotter: whether a car is alongside, and on which side.
        "carLeftRight": CAR_LEFT_RIGHT.get(g("CarLeftRight")) if g("CarLeftRight") is not None else None,
        "carLeftRightRaw": _num(g("CarLeftRight")),
        # Accelerations at 360 Hz -- six samples per frame, not one. Sending the
        # whole burst rather than a single value is what makes a G-G trace show
        # the transitions between braking and cornering, instead of a scatter of
        # disconnected dots sampled at the frame rate.
        "accel": {
            "latG": [round(v / 9.80665, 3) for v in (g("LatAccel_ST") or [])],
            "longG": [round(v / 9.80665, 3) for v in (g("LongAccel_ST") or [])],
        },
        "tireCompound": _num(g("PlayerCarTireCompound")),
        "drySetsLimit": _num(g("PlayerCarDryTireSetLimit")),
        "weightPenaltyKg": _num(g("PlayerCarWeightPenalty")),
        "powerAdjustPct": _num(g("PlayerCarPowerAdjust")),
    }
    weather = {
        "airTempC": _num(g("AirTemp")), "trackTempC": _num(g("TrackTempCrew")),
        "trackTempDeprecatedC": _num(g("TrackTemp")),
        "airPressurePa": _num(g("AirPressure")), "airDensity": _num(g("AirDensity")),
        "windMs": _num(g("WindVel")), "windDirRad": _num(g("WindDir")),
        "humidity": _num(g("RelativeHumidity")),
        "skies": SKIES.get(g("Skies"), None), "skiesRaw": _num(g("Skies")),
        "fogPct": _num(g("FogLevel")), "precipitationPct": _num(g("Precipitation")),
        "wetness": TRACK_WETNESS.get(wet, None), "wetnessRaw": _num(wet),
        "declaredWet": _num(g("WeatherDeclaredWet")),
        "solarAltitudeRad": _num(g("SolarAltitude")), "solarAzimuthRad": _num(g("SolarAzimuth")),
    }
    session = {
        "num": _num(g("SessionNum")),
        "state": SESSION_STATE.get(st, None), "stateRaw": _num(st),
        "flags": decode_bits(flags, FLAG_BITS), "flagsRaw": _num(flags),
        "timeS": _num(g("SessionTime")), "timeRemainS": _num(g("SessionTimeRemain")),
        "lapsRemain": _num(g("SessionLapsRemainEx")), "timeOfDayS": _num(g("SessionTimeOfDay")),
        "paceMode": _num(g("PaceMode")),
    }
    # What the broadcast is looking at. CamCarIdx is confirmed live while
    # spectating (62 changes in the VIR race capture, following each camera cut);
    # group/camera numbers index session.cameras. Replay speed matters because
    # spectating IS a replay (speed 1 at the live edge): anything else means the
    # picture is behind the race and the overlay must say so.
    cam_state = g("CamCameraState")
    camera = {
        "carIdx": _num(g("CamCarIdx")),
        "group": _num(g("CamGroupNumber")),
        "num": _num(g("CamCameraNumber")),
        "state": decode_bits(cam_state, CAMERA_STATE_BITS), "stateRaw": _num(cam_state),
        "replaySpeed": _num(g("ReplayPlaySpeed")),
        "replaySlowMotion": _num(g("ReplayPlaySlowMotion")),
        "replaySessionNum": _num(g("ReplaySessionNum")),
        "replaySessionTimeS": _num(g("ReplaySessionTime")),
        # Frames from the end of the tape. NOT a delay to show: while spectating
        # at speed 1 it sat around 1200 (~20 s) in the VIR capture, so what
        # "at the live edge" reads as is not yet characterised.
        "replayFramesFromEnd": _num(g("ReplayFrameNumEnd")),
    }
    # A timed race's laps left: from the car's own pace (the median of its racing laps; the last lap alone may be
    # an in- or out-lap), where it is on the lap, and the leader's place and pace (the flag falls at the leader's
    # first crossing after the clock). Once the clock is out the lap in progress is the last.
    own_i = player["carIdx"]
    usual = deriver.own_lap_time(_num(g("LapLastLapTime")), player["lapCompleted"], player["onPitRoad"])
    own_lap_s = (deriver.pace(own_i) if own_i is not None else None) or usual or _num(g("LapLastLapTime"))
    leader = None
    try:
        pos, pcts, lasts = g("CarIdxPosition"), g("CarIdxLapDistPct"), g("CarIdxLastLapTime")
        li = next((i for i, v in enumerate(pos or []) if v == 1), None)
        if li is not None and pcts is not None and li < len(pcts):
            lt = deriver.pace(li) or (lasts[li] if lasts is not None and li < len(lasts) and lasts[li] and lasts[li] > 0 else None)
            leader = {"pct": pcts[li], "lapS": lt, "me": li == own_i,
                      "white": bool(flags is not None and int(flags) & 0x2)}
    except (TypeError, ValueError):
        leader = None
    derived = deriver.fuel(player["lapCompleted"], player["fuelL"],
                           laps_remain_ex=_num(g("SessionLapsRemainEx")),
                           time_remain_s=_num(g("SessionTimeRemain")),
                           lap_time_s=own_lap_s,
                           on_pit_road=player["onPitRoad"], lap_pct=player["lapPct"], leader=leader,
                           ended=st is not None and int(st) >= 5)
    deriver.refuel(session["timeS"], player["fuelL"], bool(player["inPitStall"]))
    derived["refuelLps"] = deriver.refuel_rate()
    live = bool(player["isOnTrack"]) or (player["speedMs"] or 0) > 0.5
    deriver.adjustments(session["timeS"], player["dc"], player["lap"], live)
    sv = player["pitService"]
    derived["plan"] = fuel_plan(player["fuelL"], derived["fuelPerLap"], derived["lapsRemaining"],
                                derived["lapsRemainingIsEstimate"], player["lapPct"], player["lap"],
                                deriver.tank_l, sv.get("fuel"), "fuel" in (sv.get("flags") or []))
    plan = derived["plan"]
    add = plan["addThisLapL"] if plan["addThisLapL"] is not None else plan["addAtOpenL"]
    rate = derived["refuelLps"]
    # Fuel time only: tyre changes and repairs add to it, and are not measured.
    plan["fuelTimeS"] = round(add / rate, 1) if add is not None and rate else None
    pl_loss = deriver.pit_loss()
    derived["fuelSave"] = fuel_save(plan, derived["fuelPerLap"], player["fuelL"], deriver.tank_l,
                                    player["lapPct"], pl_loss["laneLossS"], pl_loss["boxMedianS"])
    deriver._fuel_info = ({"plan": plan, "fpl": derived["fuelPerLap"], "fuelL": player["fuelL"],
                           "tankL": deriver.tank_l, "refuelLps": rate}
                          if derived["fuelPerLap"] and player["fuelL"] is not None else None)
    derived["liveEdge"] = live_edge(g)
    cur = next((x for x in (session_msg or {}).get("sessions") or [] if x.get("SessionNum") == session.get("num")), {})
    total, timed = _parse_laps(cur.get("SessionLaps")), bool(_parse_secs(cur.get("SessionTime")))
    seen = (getattr(deriver, "_race_laps_seen", {}) or {}).get(session.get("num"))
    to_go = total - seen if total and seen is not None and seen >= 0 else None
    is_race = "race" in str(cur.get("SessionType") or "").lower()
    fin = (getattr(deriver, "_race_finished", {}) or {}).get(session.get("num")) if is_race else None
    derived["raceFlags"] = race_flags(session.get("flags"), session.get("state"),
                                      session.get("timeRemainS"), timed and is_race, to_go if is_race else None, fin)
    # "live" as the dashboard judges the player block: spectating leaves it zero.
    player_live = bool(player["isOnTrack"]) or (player["speedMs"] or 0) > 0.5 or (player["rpm"] or 0) > 500
    derived.update(deriver.trends(session["timeS"], weather, player, player_live))
    derived["weatherOutlook"] = deriver.weather_outlook(session["timeS"], weather, session, session_msg)
    deriver.state_events(session["timeS"], dict(session, raceFlags=derived["raceFlags"]), player, weather)
    return {"type": "state", "schema": SCHEMA_VERSION, "t": session["timeS"],
            "session": session, "weather": weather, "player": player, "camera": camera, "derived": derived}


def build_cars(src: TelemetrySource, session_msg: dict | None, deriver: "Deriver | None" = None) -> dict:
    g = src.get
    drivers = (session_msg or {}).get("drivers", []) if session_msg else []
    by_idx = {d.get("CarIdx"): d for d in drivers if d.get("CarIdx") is not None}
    pct = g("CarIdxLapDistPct") or []
    pos = g("CarIdxPosition")
    cpos = g("CarIdxClassPosition")
    lap = g("CarIdxLap")
    lapc = g("CarIdxLapCompleted")
    f2 = g("CarIdxF2Time")
    est = g("CarIdxEstTime")
    onpit = g("CarIdxOnPitRoad")
    surf = g("CarIdxTrackSurface")
    last = g("CarIdxLastLapTime")
    best = g("CarIdxBestLapTime")
    bestn = g("CarIdxBestLapNum")
    comp = g("CarIdxTireCompound")
    cflags = g("CarIdxSessionFlags")
    frep = g("CarIdxFastRepairsUsed")
    p2ps = g("CarIdxP2P_Status")
    p2pc = g("CarIdxP2P_Count")
    gear = g("CarIdxGear")
    rpm = g("CarIdxRPM")
    steer = g("CarIdxSteer")
    pace = g("CarIdxPaceFlags")
    cls = g("CarIdxClass")

    cars = []
    for i in range(len(pct)):
        d = by_idx.get(i)
        s = _arr(surf, i)
        # skip empty slots: no driver entry AND not in world
        if d is None and (s is None or s == -1):
            continue
        if d and (d.get("IsSpectator") or d.get("CarIsPaceCar")):
            continue
        cars.append({
            "idx": i,
            "num": d.get("CarNumber") if d else None,
            "name": d.get("UserName") if d else None,
            "team": d.get("TeamName") if d else None,
            "classId": _arr(cls, i) if cls else (d.get("CarClassID") if d else None),
            "className": d.get("CarClassShortName") if d else None,
            # iRacing assigns each class its own colour (24-bit RGB) and a
            # relative-speed rank. Using the sim's own colours rather than
            # inventing a palette means the dashboard matches every other timing
            # screen the engineer is looking at.
            "classColor": d.get("CarClassColor") if d else None,
            "classRelSpeed": d.get("CarClassRelSpeed") if d else None,
            "car": d.get("CarScreenNameShort") if d else None,
            "iRating": d.get("IRating") if d else None,
            # Licence class and safety rating, with iRacing's own colour for the
            # class so it reads the way it does everywhere else in the sim.
            "licString": d.get("LicString") if d else None,
            "licColor": d.get("LicColor") if d else None,
            "pos": _arr(pos, i), "classPos": _arr(cpos, i),
            "lap": _arr(lap, i), "lapCompleted": _arr(lapc, i),
            "pct": _arr(pct, i),
            "f2TimeS": _arr(f2, i), "estTimeS": _arr(est, i),
            "onPitRoad": _arr(onpit, i),
            "surface": TRACK_SURFACE.get(s, str(s)) if s is not None else None,
            "lastLapS": _arr(last, i), "bestLapS": _arr(best, i), "bestLapNum": _arr(bestn, i),
            # -1 is iRacing's "not known": a car not loaded into the world yet (every
            # car at the start of a session) or gone from it. Not a compound.
            "compound": _compound(_arr(comp, i)),
            # The class's reference lap from the session info: what CarIdxEstTime
            # is measured against (its value at the line).
            # A replay's session info gave 1.0 for every car (Talladega): no lap is that short, so
            # under 5 s it is taken as not known, and gaps fall back to the car's best lap.
            "classEstLapS": d.get("CarClassEstLapTime") if d and (d.get("CarClassEstLapTime") or 0) >= 5 else None,
            # iRacing's paint scheme for the car and helmet ("pattern,col1,col2,col3").
            # Not the Trading Paints livery, which only exists as a texture.
            "carDesign": d.get("CarDesignStr") if d else None,
            "helmetDesign": d.get("HelmetDesignStr") if d else None,
            "suitDesign": d.get("SuitDesignStr") if d else None,
            "carId": d.get("CarID") if d else None,
            "userId": d.get("UserID") if d else None,
            "teamId": d.get("TeamID") if d else None,
            "flags": decode_bits(_arr(cflags, i), FLAG_BITS),
            "fastRepairsUsed": _arr(frep, i),
            "p2pActive": _arr(p2ps, i), "p2pCount": _arr(p2pc, i),
            "gear": _arr(gear, i), "rpm": _arr(rpm, i), "steerRad": _arr(steer, i),
            "paceFlagsRaw": _arr(pace, i),
            # From the session info, not telemetry: iRacing's own counted
            # incident points for the car (team total) and its current driver.
            # Refreshed only when the session info is. Negative or absent means
            # not published for this car in this session.
            "incidents": d.get("TeamIncidentCount") if d else None,
            "driverIncidents": d.get("CurDriverIncidentCount") if d else None,
        })

    # derived: interval to the car one position ahead, from CarIdxF2Time.
    # Only meaningful when F2Time is "time behind leader" (race sessions).
    #
    # Before a car completes its first lap iRacing has no gap to report, and
    # CarIdxF2Time carries a placeholder of exactly 0.001 * (position - 1)
    # instead of a time. Observed at Daytona: the leader crossing the line
    # flipped P2/P3 to real gaps (2.3722 / 2.4418) while P10, still on lap 1,
    # stayed on 0.0090. So the placeholder clears per car, not per session, and
    # a car's F2Time is only trustworthy once that car has a completed lap.
    # A completed lap is necessary but NOT sufficient. Observed mid-race at VIR:
    # P4 carried a real 2.8702 while P5 and P6 still carried placeholders of
    # 0.0020 and 0.0050 despite both having genuine last-lap times (104.496,
    # 104.750). Differencing those produced -2.868. Across one 15-minute race
    # that pattern yielded 1459 negative intervals.
    #
    # The property that actually holds is monotonicity: time behind the leader
    # cannot DECREASE as you go down the running order. So we track the last
    # F2Time we trusted and reject any car whose value falls below it -- that
    # car's F2Time disagrees with its scored position, and when the sim's two
    # signals disagree we show nothing rather than invent a gap.
    # The same walk also decides whether each car's RAW f2TimeS is fit to show as a
    # gap to the leader. It is the same question -- a placeholder is not a gap
    # whether you difference it or print it directly -- and the client was showing
    # "+0.001, +0.002, +0.003" down the field on lap 1 as though they were times.
    # gapValid is a flag, not a number: f2TimeS stays exactly as iRacing sent it.
    ordered = sorted([c for c in cars if c["pos"] and c["pos"] > 0], key=lambda c: c["pos"])
    prev_real = None
    for c in ordered:
        c["derived"] = {"intervalS": None}
        c["gapValid"] = False
        if not f2_is_real(c):
            continue
        if prev_real is None:                      # leader: nobody ahead to gap to
            prev_real = c["f2TimeS"]
            c["gapValid"] = True
            continue
        if c["f2TimeS"] < prev_real:               # inconsistent with the order
            continue                               # leave prev_real where it was
        c["gapValid"] = True
        c["derived"]["intervalS"] = round(c["f2TimeS"] - prev_real, 3)
        prev_real = c["f2TimeS"]

    # Defaults FIRST: cars that were never scored (pos 0, spectators, the pace car)
    # still get a derived block, because the per-car derivations below run over
    # every car, not just the ordered ones.
    for c in cars:
        c.setdefault("derived", {"intervalS": None})
        c.setdefault("gapValid", False)

    # Gaps that only mean something in a race, where F2Time is time behind the
    # leader: to your car (positive = that car is behind you) and to the leader
    # of the car's own class. Both are differences of two F2Times that each
    # passed the checks above; anything else stays null.
    session_num = _num(g("SessionNum"))
    is_race = _is_race(session_msg, session_num)
    if deriver is not None:
        deriver._race_now = is_race             # a race's opening lap is not a racing lap (consistency, pace)
    by_idx = {c["idx"]: c for c in cars}
    # Practice and qualifying: the best laps that count are iRacing's scored results (ResultsPositions FastestTime),
    # not CarIdxBestLapTime, which is -1 for a car that ran before we joined and can hold a lap that did not count
    # (Summit Point Lone Qualify, Nov 2026: pole 1:23.500 and P2 read as no time; the telemetry "pole", 1:25.618,
    # was P4's, whose counted lap was 1:27.421). iRacing's positions already follow the results.
    if is_race:
        # a car that finished and left (not in world) reads -1 for its best and last laps: the results keep them
        # (Summit Point: the winner's card and the results read "–" for his best lap)
        try:
            for r in results_positions(src.session_info("SessionInfo"), session_num):
                c = by_idx.get(r.get("CarIdx"))
                if c is None:
                    continue
                for k, rk in (("bestLapS", "FastestTime"), ("lastLapS", "LastTime")):
                    v = r.get(rk)
                    if not (c.get(k) or 0) > 0 and isinstance(v, (int, float)) and v > 0:
                        c[k] = v
                        c.setdefault("fromResults", []).append(k)       # not this car's telemetry
        except Exception:  # noqa: BLE001
            pass
    if not is_race:
        try:
            for r in results_positions(src.session_info("SessionInfo"), session_num):
                c = by_idx.get(r.get("CarIdx"))
                ft = r.get("FastestTime")
                if c is not None and isinstance(ft, (int, float)) and ft > 0:
                    c["bestLapTelemetryS"] = c.get("bestLapS")
                    c["bestLapS"] = ft
        except Exception:  # noqa: BLE001
            pass
    own_idx = _num(g("PlayerCarIdx"))
    own = by_idx.get(own_idx)                  # your car, when you are driving
    cam_idx = _num(g("CamCarIdx"))
    # Spectating is the normal case for this booth: PlayerCarIdx is then a
    # spectator slot with no car, and the camera car is what is being watched.
    # No viewer can move this reference: it is shared by everyone on the relay.
    # A viewer following another car gets a RefView (relative_to, below).
    if own is not None:
        player_idx, ref_source = own_idx, "your_car"
    elif cam_idx in by_idx:
        player_idx, ref_source = cam_idx, "camera"
    elif deriver is not None and deriver.ref_idx in by_idx and getattr(deriver, "ref_source", None) in ("camera", "camera_last"):
        # The camera on the pace car (or on no car): keep following the last car it showed, rather
        # than every relative panel going blank. Under a caution that was 40% of a Talladega replay.
        player_idx, ref_source = deriver.ref_idx, "camera_last"
    else:
        player_idx, ref_source = None, None
    me = by_idx.get(player_idx)
    if deriver is not None and player_idx != deriver.ref_idx:
        deriver.set_ref(player_idx)
    if deriver is not None:
        deriver.ref_source = ref_source
    class_leader = {}
    for c in ordered:
        if c["gapValid"] and c.get("classPos") == 1:
            class_leader[c.get("classId")] = c
    # Live timing: while the race is running, the order and every gap come from
    # where the cars are now, not from CarIdxPosition / CarIdxF2Time, which only
    # change when a car crosses a timing line (the battle and the tower sat still
    # for most of a lap). Before the start and after the flag, iRacing's own.
    live = bool(is_race) and SESSION_STATE.get(g("SessionState")) == "racing"
    running = live_order(ordered, live, deriver._dist_prev if deriver is not None else None)
    live_gaps(ordered)                     # the broadcast tower: iRacing's order, live gaps
    if deriver is not None:
        fin = (getattr(deriver, "_race_finished", {}) or {}).get(session_num) if is_race else None
        deriver.finish_gaps(ordered, "checkered" if fin else None, _num(g("SessionTime")), cars)
    run_gaps(running)                      # PitWall: the running order, live gaps
    for c in cars:
        d = c["derived"]
        d["toPlayerS"] = None
        d["classGapS"] = None
        if not is_race:
            continue
        if me is not None and c is not me:
            d["toPlayerS"] = ref_gap(c, me, live)
        if not c["gapValid"]:
            continue
        lead = class_leader.get(c.get("classId"))
        if lead is not None:
            d["classGapS"] = round(c["f2TimeS"] - lead["f2TimeS"], 3)

    if deriver is not None:
        now = _num(g("SessionTime"))
        deriver._lap_finished = []
        deriver._stops_tick = {}
        limit = ((session_msg or {}).get("options") or {}).get("IncidentLimit")
        try:
            deriver.incident_limit = int(str(limit).split()[0]) if limit is not None else None
        except ValueError:
            deriver.incident_limit = None        # the YAML says unlimited
        dc = (session_msg or {}).get("driverCar") or {}
        try:
            cap = float(dc.get("DriverCarFuelMaxLtr")) if dc.get("DriverCarFuelMaxLtr") is not None else None
            pct = float(dc.get("DriverCarMaxFuelPct")) if dc.get("DriverCarMaxFuelPct") is not None else 1.0
            # A series can restrict the tank (MaxFuelPct < 1); the usable tank is the product.
            deriver.tank_l = cap * pct if cap else None
        except (TypeError, ValueError):
            deriver.tank_l = None
        deriver._player_idx = own_idx if own is not None else None
        # Your own car's incidents are iRacing's count, not the guess made for the field: any rise marks the lap
        # (the driver's Indianapolis file: a 4x and two 1x went unseen by the telemetry signs, and the clean-lap
        # streak ran through them).
        my_inc = _num(g("PlayerCarMyIncidentCount")) if own is not None else None
        if my_inc is not None:
            was = deriver.__dict__.get("_own_inc")
            if was is not None and my_inc > was:
                deriver.__dict__.setdefault("_dirty", set()).add(own_idx)
            deriver.__dict__["_own_inc"] = my_inc
        track_len = track_length_m(session_msg)
        opts = (session_msg or {}).get("options") or {}
        try:
            fast_limit = int(str(opts.get("FastRepairsLimit")).split()[0]) if opts.get("FastRepairsLimit") is not None else None
        except ValueError:
            fast_limit = None                    # "unlimited"
        grid_by_idx = {r["idx"]: r for r in (((session_msg or {}).get("grid") or {}).get("rows") or [])}
        team_race = bool(((session_msg or {}).get("event") or {}).get("TeamRacing"))
        sector_starts = [s.get("SectorStartPct") for s in ((session_msg or {}).get("sectors") or [])
                         if s.get("SectorStartPct") is not None]
        ahead_of = {}                      # carIdx -> idx of the car it is measured against
        # The Δ/lap trend stays on iRacing's timing-line intervals: it is a per-lap
        # figure, and a live interval to a car of another class swings within the lap
        # with the two cars' different speed profiles (it read +-10 s/lap).
        prev_c = None
        for c in ordered:
            if prev_c is not None:
                ahead_of[c["idx"]] = prev_c["idx"]
            prev_c = c
        for c in cars:
            lap_t = c.get("lastLapS")
            c["derived"].update(deriver.closing(
                c.get("idx"), now, c["derived"].get("intervalS"),
                lap_t if lap_t and lap_t > 0 else None,
                ahead_idx=ahead_of.get(c.get("idx"))))
            c["derived"].update(deriver.pit(
                c.get("idx"), now, c.get("onPitRoad"),
                c.get("surface") == "in_pit_stall", c.get("lap"), pct=c.get("pct"), class_id=c.get("classId")))
            v = deriver.car_speed(c, now, track_len)
            c["derived"]["speedMs"] = None if v is None else round(v, 1)
            c["derived"].update(deriver.stint(
                c.get("idx"), c.get("lapCompleted"), c.get("lastLapS"),
                c["derived"].get("stopsSeen")))
            c["derived"].update(deriver.sectors(
                c.get("idx"), now, c.get("pct"), sector_starts,
                c.get("surface") == "on_track"))
            # Theoretical best: this car's own best sectors added together. The
            # gap between it and the actual best lap is time the driver has
            # already shown they can find, just not on the same lap.
            theo, left = theoretical_best(c["derived"].get("bestSectorS"), c.get("bestLapS"))
            if theo is not None:
                c["derived"]["theoreticalBestS"] = theo
            if left is not None:
                c["derived"]["bestLapLeftS"] = left

    out = {"type": "cars", "schema": SCHEMA_VERSION, "t": _num(g("SessionTime")), "cars": cars}
    if deriver is not None:
        for c in cars:
            deriver.record_lap(c, now)
        deriver.events_live = live_edge(g)["atLive"]
        for c in cars:
            c["derived"].update(deriver.car_extras(c, now, grid_by_idx, fast_limit, is_race, team_race))
        my_lap = deriver.pace(player_idx) if me is not None else None
        if my_lap is None and me is not None and me.get("lastLapS") and me["lastLapS"] > 0:
            my_lap = me["lastLapS"]
        for c in cars:
            p = deriver.pace(c["idx"])
            c["derived"]["paceS"] = None if p is None else round(p, 3)
            if c is me:
                continue
            c["derived"].update(deriver.to_player_trend(
                c["idx"], now, c["derived"].get("toPlayerS"), my_lap, player_idx))
            if me is not None:
                c["derived"]["vsYou"] = vs_you(c, me, p, deriver.pace(player_idx))
        try:
            results = results_positions(src.session_info("SessionInfo"), session_num)
        except Exception:  # noqa: BLE001
            results = []
        race = race_length(session_msg, session_num, ordered, _num(g("SessionTimeRemain")),
                           deriver, race_flags(decode_bits(g("SessionFlags"), FLAG_BITS),
                                               SESSION_STATE.get(g("SessionState"))), results=results,
                           state=SESSION_STATE.get(g("SessionState")))
        # the winner's flag is not the end for everyone: the race is over when the field has taken it (_finish_all)
        fs = deriver.finish_state()
        race["allFinished"] = bool(race.get("finished")) and fs["allFinished"] is True
        race["finishLeft"] = fs["finishLeft"] if race.get("finished") else None
        deriver._lead_lap = race.get("leaderLap")
        fi = deriver._fuel_info or {}
        deriver.stop_forecast(cars, race if is_race else None, own_idx if own is not None else None,
                              fi.get("fpl"), deriver.tank_l, fi.get("fuelL"))
        caution = deriver.caution(cars, decode_bits(g("SessionFlags"), FLAG_BITS), g("PitsOpen"),
                                  track_length_m(session_msg), now, state=SESSION_STATE.get(g("SessionState")))
        deriver.learn_lap_profile(cars)
        if live_edge(g)["atLive"]:
            deriver.lap_splits(cars, now)
        deriver.quali_laps(cars)
        if not is_race:
            Deriver.best_intervals(cars)
        meet = deriver.meetings(cars, player_idx, now)
        class_best_sectors = Deriver.sector_marks(cars)
        deriver.class_scale(cars, track_length_m(session_msg))
        for c in cars:
            c["derived"]["gapScale"] = deriver.gap_scale(c.get("classId"))   # what "close" is in its class (class_scale)
        battles = deriver.battles(cars, ahead_of, is_race)
        deriver.battle_history(battles, now)
        attacks = deriver.attacks(cars, now, is_race)
        battle_pressure(battles, attacks)
        tv = deriver.race_tv(cars, now, race if is_race else None, ahead_of, is_race)
        out["derived"] = {
            "caution": caution,
            "strategy": deriver.strategy(cars, player_idx, race, caution, own_idx if own is not None else None)
            if is_race and me is not None else None,
            "ref": {"idx": player_idx, "source": ref_source, "own": own_idx if own is not None else None,
                    "camera": cam_idx if cam_idx in by_idx else None},
            "sessionBestSectorS": deriver.session_best_sectors(),
            "isRace": is_race,
            "liveTiming": live,
            "pitLoss": deriver.pit_loss(),
            "rejoin": deriver.rejoin(cars, player_idx) if is_race and me is not None else None,
            "undercut": deriver.undercut(cars, player_idx, now) if is_race and me is not None else [],
            "traffic": [m for m in meet if m["inS"] <= deriver.TRAFFIC_HORIZON_S][:6],
            "meetings": meet,
            "compoundPace": deriver.compound_pace(cars),
            "nearby": deriver.nearby(cars, player_idx, track_length_m(session_msg), now),
            "battles": battles,
            "attacks": attacks,
            "throughTraffic": deriver.through_traffic(cars) if is_race else [],
            **tv,
            "lapChart": deriver.lap_chart(cars) if is_race else None,
            "classTraffic": deriver.class_traffic(cars, now),
            "classBestSectorS": class_best_sectors,
            "faster": deriver.faster_pairs(cars),
            "predictedIncidents": deriver.predicted_incidents(),
            "race": race,
            "sof": strength_of_field(cars),
        }
        edge = live_edge(g)
        out["derived"]["liveEdge"] = edge
        # Race events describe the race as it happens. While a replay is being
        # watched the field data is from the past, and pit stops or lead changes
        # read from it would be old news reported as new.
        if edge["atLive"]:
            deriver.field_events(cars, now, player_idx, deriver._stops_tick,
                                 state=SESSION_STATE.get(g("SessionState"), "invalid"), session_num=session_num)
        deriver.incidents(cars, edge, session_num,
                          surface_kind(((session_msg or {}).get("track") or {}).get("TrackType")),
                          state=SESSION_STATE.get(g("SessionState"), "invalid"),
                          flags=decode_bits(g("SessionFlags"), FLAG_BITS))
    return out
