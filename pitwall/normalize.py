"""Turn raw iRacing variables into the protocol messages in docs/PROTOCOL.md.

Provenance rule (the whole point of this project):
    * Anything under a plain key is copied straight from an iRacing variable.
    * Anything under a "derived" key was computed here. The UI must label it.
    * Tire wear / tread values are copied as-is but flagged "snapshot" in the
      protocol docs because the sim only refreshes them at pit stops.

No module above this one is allowed to compute racing math from raw variables;
if a number needs deriving, it happens here under "derived" so it is auditable
in one place.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .sources import TelemetrySource

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------- #
# Enum decoders (values from irsdk.py / the official SDK header)
# --------------------------------------------------------------------------- #

FLAG_BITS = {
    0x0001: "checkered", 0x0002: "white", 0x0004: "green", 0x0008: "yellow",
    0x0010: "red", 0x0020: "blue", 0x0040: "debris", 0x0080: "crossed",
    0x0100: "yellow_waving", 0x0200: "one_lap_to_green", 0x0400: "green_held",
    0x0800: "ten_to_go", 0x1000: "five_to_go", 0x2000: "random_waving",
    0x4000: "caution", 0x8000: "caution_waving",
    0x010000: "black", 0x020000: "disqualify", 0x040000: "servicible",
    0x080000: "furled", 0x100000: "repair", 0x200000: "dq_scoring_invalid",
    0x10000000: "start_hidden", 0x20000000: "start_ready",
    0x40000000: "start_set", 0x80000000: "start_go",
}
ENGINE_WARNING_BITS = {
    0x0001: "water_temp", 0x0002: "fuel_pressure", 0x0004: "oil_pressure",
    0x0008: "engine_stalled", 0x0010: "pit_speed_limiter", 0x0020: "rev_limiter",
    0x0040: "oil_temp", 0x0080: "mandatory_repair_needed", 0x0100: "optional_repair_needed",
}
PIT_SV_BITS = {
    0x01: "lf_tire", 0x02: "rf_tire", 0x04: "lr_tire", 0x08: "rr_tire",
    0x10: "fuel", 0x20: "windshield", 0x40: "fast_repair",
}
CAMERA_STATE_BITS = {
    0x0001: "session_screen", 0x0002: "scenic_active", 0x0004: "cam_tool_active",
    0x0008: "ui_hidden", 0x0010: "auto_shot_selection", 0x0020: "temporary_edits",
    0x0040: "key_acceleration", 0x0080: "key10x_acceleration", 0x0100: "mouse_aim_mode",
}
TRACK_SURFACE = {-1:"not_in_world", 0: "off_track", 1: "in_pit_stall", 2: "approaching_pits", 3: "on_track"}
SESSION_STATE = {0: "invalid", 1: "get_in_car", 2: "warmup", 3: "parade_laps", 4: "racing", 5: "checkered", 6: "cool_down"}
TRACK_WETNESS = {0: "unknown", 1: "dry", 2: "mostly_dry", 3: "very_lightly_wet", 4: "lightly_wet",
                 5: "moderately_wet", 6: "very_wet", 7: "extremely_wet"}
SKIES = {0: "clear", 1: "partly_cloudy", 2: "mostly_cloudy", 3: "overcast"}
# irsdk_CarLeftRight: iRacing's own spotter, the same signal its spotter calls from.
CAR_LEFT_RIGHT = {0: "off", 1: "clear", 2: "car_left", 3: "car_right", 4: "car_left_right",
                  5: "two_cars_left", 6: "two_cars_right"}

# iRacing's incident points, Official Sporting Code 3.5.1 (Sep 2025). The table is
# per surface: on dirt, heavy contact is 2x instead of 4x. Light contact is 0x, so
# it never moves a count and can never be seen in this data. When incidents are
# concurrent only the most serious counts (3.5.2: "2x that causes a 4x" is 4x).
INCIDENT_POINTS = {
    "paved": {0: ["light contact"], 1: ["wheels off the racing surface"],
              2: ["loss of control", "contact with an object"], 4: ["heavy contact with another car"]},
    "dirt": {0: ["light contact"], 1: ["wheels off the racing surface"],
             2: ["loss of control", "contact with an object", "heavy contact with another car"]},
}


def surface_kind(track_type) -> str:
    """WeekendInfo/TrackType ("road course", "dirt oval", "dirt road", ...)."""
    return "dirt" if "dirt" in str(track_type or "").lower() else "paved"


def incident_causes(points: int, surface: str = "paved") -> dict:
    """What a rise of `points` in a car's count can mean, by iRacing's table.

    The count only tells us the total went up. A rise matching one table entry
    is probably that incident; it can still be several smaller ones counted
    between two session updates (two 1x offs read as 2x). A rise matching no
    entry (3x, 5x) is certainly more than one incident. Both facts are stated;
    nothing here picks one cause over another.
    """
    table = INCIDENT_POINTS.get(surface, INCIDENT_POINTS["paved"])
    single = table.get(points, [])
    return {"causes": single, "single": bool(single), "maybeSeveral": points > 1}


def live_edge(g) -> dict:
    """Where the telemetry is in time. While spectating, iRacing is playing its
    replay, and every CarIdx* variable comes from the replay frame -- so after a
    producer jumps back to an incident, the field data is the field at that
    moment, not now. SessionTime keeps the live clock; ReplaySessionTime is the
    playhead. Within LIVE_TOLERANCE_S of each other counts as live (the same 4 s
    RobertsmaniaReplay uses; NOT yet measured here)."""
    t_live = _num(g("SessionTime"))
    replaying = bool(g("IsReplayPlaying"))
    t_play = _num(g("ReplaySessionTime")) if replaying else t_live
    if t_play is None:
        t_play = t_live
    behind = None
    if replaying and t_live is not None and t_play is not None:
        behind = round(t_live - t_play, 2)
    at_live = (behind is None or abs(behind) < LIVE_TOLERANCE_S) and \
        (not replaying or (g("ReplayPlaySpeed") in (None, 1) and not g("ReplayPlaySlowMotion")))
    return {"tLive": t_live, "tPlay": t_play, "behindLiveS": behind, "atLive": at_live}


LIVE_TOLERANCE_S = 4.0


def decode_bits(value: int | None, table: dict[int, str]) -> list[str]:
    if value is None:
        return []
    try:
        value = int(value)
    except (TypeError, ValueError):
        return []
    return [name for bit, name in table.items() if value & bit]


def _num(v):
    """JSON-safe number: NaN/inf -> None, bools stay bools."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    return v


def _arr(v, idx, default=None):
    if isinstance(v, list) and 0 <= idx < len(v):
        return _num(v[idx])
    return default


# --------------------------------------------------------------------------- #
# Session message (from the YAML session string; changes rarely)
# --------------------------------------------------------------------------- #

_DRIVER_KEYS = ("CarIdx", "UserName", "TeamName", "CarNumber", "CarNumberRaw", "CarClassID",
                "CarClassShortName", "CarScreenNameShort", "CarScreenName", "CarPath",
                "IRating", "LicString", "LicLevel", "LicColor", "IsSpectator", "CarIsPaceCar", "CarIsAI",
                "CarClassEstLapTime", "CarClassRelSpeed", "CarClassColor", "TeamID", "UserID",
                "CurDriverIncidentCount", "TeamIncidentCount", "CarDesignStr", "HelmetDesignStr", "CarID",
                "CarNumberDesignStr", "CarSponsor_1", "CarSponsor_2", "ClubID")


def build_session(src: TelemetrySource) -> dict:
    wk = src.session_info("WeekendInfo") or {}
    di = src.session_info("DriverInfo") or {}
    si = src.session_info("SessionInfo") or {}
    sp = src.session_info("SplitTimeInfo") or {}
    drivers = []
    for d in di.get("Drivers", []) or []:
        drivers.append({k: d.get(k) for k in _DRIVER_KEYS if k in d})
    sessions = []
    for s in si.get("Sessions", []) or []:
        # SessionTrackRubberState is a written description ("moderately high usage",
        # "carry over"). It is a per-session SETTING describing the track at the
        # START of that session: verified unchanged across 86 session-info updates
        # over 15 minutes of racing. With TrackDynamicTrack on, the real surface
        # evolves from there and the sim never reports the evolved state.
        sessions.append({k: s.get(k) for k in ("SessionNum", "SessionType", "SessionLaps",
                                                 "SessionTime", "SessionName", "SessionSubType",
                                                 "SessionSkipped", "SessionRunGroupsUsed",
                                                 "SessionTrackRubberState") if k in s})
    return {
        "type": "session",
        "schema": SCHEMA_VERSION,
        "update": src.session_info_update,
        "track": {k: wk.get(k) for k in ("TrackName", "TrackID", "TrackDisplayName", "TrackDisplayShortName",
                                          "TrackConfigName", "TrackLength", "TrackNumTurns", "TrackPitSpeedLimit",
                                          "TrackType", "TrackLatitude", "TrackLongitude", "TrackNorthOffset",
                                          "TrackDynamicTrack", "TrackCleanup")},
        "event": {k: wk.get(k) for k in ("EventType", "Category", "SeriesID", "SeasonID", "SessionID",
                                          "SubSessionID", "LeagueID", "Official", "TeamRacing", "NumCarClasses",
                                          "TrackWeatherType", "TrackSkies", "SimMode")},
        "driverTires": [{"index": t.get("TireIndex"), "type": t.get("TireCompoundType")}
                        for t in (di.get("DriverTires") or []) if isinstance(t, dict)],
        "driverCar": {k: di.get(k) for k in ("DriverCarIdx", "DriverUserID", "DriverCarFuelMaxLtr",
                                              "DriverCarMaxFuelPct", "DriverCarSLShiftRPM",
                                              "DriverCarSLBlinkRPM", "DriverCarRedLine", "DriverCarFuelKgPerLtr",
                                              "DriverCarEstLapTime", "DriverPitTrkPct", "DriverCarIdleRPM",
                                              "DriverCarGearNumForward", "DriverSetupName")},
        # Race rules the booth needs: the incident limit (a number, or
        # "unlimited") and the fast-repair allowance.
        "options": {k: (wk.get("WeekendOptions") or {}).get(k)
                    for k in ("IncidentLimit", "FastRepairsLimit", "StandingStart", "NumStarters")},
        "drivers": drivers,
        "sessions": sessions,
        "sectors": sp.get("Sectors", []) or [],
        "cameras": _camera_groups(src.session_info("CameraInfo")),
        "grid": _grid(src.session_info("QualifyResultsInfo"), si.get("Sessions") or []),
    }


def _qual_rows(rows) -> list[dict]:
    """QualifyResultsInfo/Results (or a session's QualifyPositions) as a grid.
    iRacing numbers these positions from 0 (pole is Position 0); shown from 1.
    The offset is applied only when a 0 is actually present, so a list that is
    already 1-based is not pushed down a place."""
    rows = [r for r in (rows or []) if isinstance(r, dict) and r.get("CarIdx") is not None]
    if not rows:
        return []
    off = 1 if min((r.get("Position") if r.get("Position") is not None else 1) for r in rows) == 0 else 0
    coff = 1 if min((r.get("ClassPosition") if r.get("ClassPosition") is not None else 1) for r in rows) == 0 else 0
    out = []
    for r in rows:
        ft = r.get("FastestTime")
        out.append({"pos": (r.get("Position") or 0) + off,
                    "classPos": (r.get("ClassPosition") or 0) + coff if r.get("ClassPosition") is not None else None,
                    "idx": r.get("CarIdx"),
                    "timeS": ft if isinstance(ft, (int, float)) and ft > 0 else None,
                    "lap": r.get("FastestLap")})
    return sorted(out, key=lambda x: x["pos"])


def _grid(qri, sessions) -> dict:
    """The starting order for the race. A heat or a race with its own
    QualifyPositions uses those; otherwise the event's qualifying results."""
    for s in sessions:
        if "race" in str(s.get("SessionType") or "").lower() and s.get("QualifyPositions"):
            return {"source": "race_qualify_positions", "sessionNum": s.get("SessionNum"),
                    "rows": _qual_rows(s.get("QualifyPositions"))}
    rows = _qual_rows((qri or {}).get("Results"))
    return {"source": "qualify_results", "sessionNum": None, "rows": rows} if rows else {"source": None, "rows": []}


def _camera_groups(ci) -> list[dict]:
    """CameraInfo/Groups as the broadcast panel needs it: the group numbers are
    what a camera command takes, and they differ by track, so they are read from
    the session rather than hard-coded. Copied, not interpreted."""
    out = []
    for gr in ((ci or {}).get("Groups") or []):
        if not isinstance(gr, dict) or gr.get("GroupNum") is None:
            continue
        out.append({
            "num": gr.get("GroupNum"),
            "name": gr.get("GroupName"),
            "scenic": bool(gr.get("IsScenic")),
            "cameras": [{"num": c.get("CameraNum"), "name": c.get("CameraName")}
                        for c in (gr.get("Cameras") or []) if isinstance(c, dict)],
        })
    return out


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


class Deriver:
    """Keeps the little bit of history needed for derived values."""

    def __init__(self):
        self._last_lap_completed = None
        self._fuel_at_lap_start = None
        self._fuel_per_lap_samples: list[float] = []
        self._gap_hist: dict[int, list[tuple[float, float]]] = {}
        self._gap_ahead: dict[int, int | None] = {}
        self._gap_slope: dict[int, float | None] = {}
        self._tp_slope: dict[int, float | None] = {}
        self._pit: dict[int, dict] = {}
        self._stint: dict[int, dict] = {}
        self._sect: dict[int, dict] = {}
        self._sect_best: list[float | None] = []
        # booth
        self._lapst: dict[int, dict] = {}
        self._laps: dict[int, list[dict]] = {}
        self._lap_finished: list[dict] = []
        self._loss: list[dict] = []
        self._stops_tick: dict[int, dict] = {}
        self._tp_hist: dict[int, list[tuple[float, float]]] = {}
        self._tp_ref: dict[int, int | None] = {}
        self._uc: dict[int, dict] = {}
        self._uc_inpit: dict[int, bool] = {}
        self._player_lap = None
        self._player_idx = None
        # The car everything relative is measured from: one a viewer chose,
        # else your own car when driving, else the camera car when spectating.
        self.focus_manual: int | None = None
        self.ref_idx: int | None = None
        self.ref_source: str | None = None
        self._last_lap_seq = 0
        self._spd: dict[int, dict] = {}
        self._cmp: dict[int, dict] = {}
        self._drv: dict[int, dict] = {}
        self._rf_samples: list[tuple[float, float]] = []
        self._rf_rates: list[float] = []
        self._adj_base: dict | None = None
        self._adj_pend: dict[str, dict] = {}
        self.tank_l: float | None = None
        self._fuel_by_lap: dict[int, dict] = {}
        self._fuel_lap_pit = False
        self._fuel_partial = False
        self._inc_by_lap: dict[int, int] = {}
        self._inc_lap = None                    # (lapCompleted, incidents at its start)
        self._near_hist: dict[int, list[tuple[float, float]]] = {}
        self.incident_limit: int | None = None
        self.feed_seq = 0
        self.feed_laps: list[dict] = []
        self.feed_events: list[dict] = []
        self._ev = {"pit": {}, "best": {}, "bestSeeded": False, "closing": {}, "stable": {},
                    "flags": None, "flagAt": {}, "inc": None, "warn": None, "tow": None,
                    "wet": None, "rain": None, "declaredWet": None}

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

    @staticmethod
    def _slope(hist):
        n = len(hist)
        mt = sum(h[0] for h in hist) / n
        mg = sum(h[1] for h in hist) / n
        denom = sum((h[0] - mt) ** 2 for h in hist)
        if denom <= 0:
            return None
        return sum((h[0] - mt) * (h[1] - mg) for h in hist) / denom

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

    def pit(self, idx, t, on_pit_road, in_box, lap, pct=None) -> dict:
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
                              "onRoad": on_pit_road, "inBox": in_box,
                              # a stint's length is only known if we saw it start:
                              # at the start of the race, or at a stop we watched
                              "stints": [], "stintStart": lap if lap is not None and 0 <= lap <= 1 else None}
            out["inPit"] = on_pit_road
            return out
        if on_pit_road and not st["onRoad"]:                 # entered the lane
            st["enterT"], st["enterLap"], st["boxS"] = t, lap, None
            st["enterPct"], st["bad"] = pct, False
        elif on_pit_road and pct is not None and pct >= 0 and st.get("lastPct") is not None:
            # A reset or tow puts the car in its box without driving the lane.
            # That looks like a stop to the counters, but timing it as lane loss
            # would be nonsense, so a jump in position voids the loss sample.
            step = (pct - st["lastPct"]) % 1.0
            if 0.05 < step < 0.995:
                st["bad"] = True
        if pct is not None and pct >= 0:
            st["lastPct"] = pct
        if in_box and not st["inBox"]:                       # stopped in the box
            st["boxT"] = t
        if st["inBox"] and not in_box and st["boxT"] is not None:
            st["boxS"] = t - st["boxT"]                      # stationary time
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
    STINT_MIN_LAPS = 4

    def stint(self, idx, lap_completed, last_lap_s, stops_seen) -> dict:
        out = {"stintLaps": 0, "stintAvgS": None, "degSPerLap": None}
        if idx is None or lap_completed is None:
            return out
        st = self._stint.setdefault(idx, {"laps": [], "lastLap": None, "stops": stops_seen})
        if stops_seen != st["stops"]:          # a stop ended the stint
            st["laps"], st["lastLap"], st["stops"] = [], None, stops_seen
        if (last_lap_s and last_lap_s > 0 and lap_completed != st["lastLap"]):
            st["laps"].append((lap_completed, last_lap_s))
            st["lastLap"] = lap_completed
            st["laps"] = st["laps"][-40:]
        laps = st["laps"]
        out["stintLaps"] = len(laps)
        if not laps:
            return out
        out["stintAvgS"] = round(sum(l[1] for l in laps) / len(laps), 3)
        if len(laps) >= self.STINT_MIN_LAPS:
            n = len(laps)
            mx = sum(l[0] for l in laps) / n
            my = sum(l[1] for l in laps) / n
            denom = sum((l[0] - mx) ** 2 for l in laps)
            if denom > 0:
                # seconds of lap time gained (-) or lost (+) per lap of running
                out["degSPerLap"] = round(sum((l[0] - mx) * (l[1] - my) for l in laps) / denom, 3)
        return out

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

    # ------------------------------------------------------------------ #
    # Race engineer booth: lap history, pace, gaps to you, pit loss,
    # rejoin, undercut watch, traffic, and the event feed.
    # ------------------------------------------------------------------ #

    # How long to wait for CarIdxLastLapTime to change after the lap counter
    # ticks. The player's LapLastLapTime is known to lag the crossing (the Lap
    # Analyzer had to correct for exactly that); whether the per-car array does
    # is not verified, so both orders are handled: a value that already changed
    # is taken at once, one that has not is waited for.
    LAP_TIME_WAIT_S = 10.0
    PACE_LAPS = 5
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
        item = {"t": None if t is None else round(t, 2), "kind": kind, "lap": self._player_lap}
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
        self._feed_add("lap", e)
        self._lap_finished.append(e)
        return e

    def pace(self, idx) -> float | None:
        """Median of this car's recent racing laps: no in- or out-laps, no
        unknown times. Two laps minimum, so one lap cannot stand for a pace."""
        laps = [e["lapS"] for e in self._laps.get(idx, []) if e["lapS"] and not e["pit"]]
        laps = laps[-self.PACE_LAPS:]
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

    def _pit_lane_sample(self, idx, st, t, pct, box_s):
        """Called when a car leaves the lane after a stop we watched."""
        if st.get("bad") or st.get("enterPct") is None or pct is None or pct < 0:
            return None
        pace = self.pace(idx)
        if not pace:
            return None
        frac = (pct - st["enterPct"]) % 1.0
        lane_t = t - st["enterT"]
        loss = lane_t - (box_s or 0.0) - frac * pace
        if not (frac < 0.3 and 0.0 < loss < 120.0):
            return None
        rec = {"idx": idx, "laneLossS": round(loss, 2), "boxS": box_s, "laneS": round(lane_t, 1)}
        self._loss.append(rec)
        del self._loss[:-30]
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

    # ---- traffic -----------------------------------------------------------
    TRAFFIC_HORIZON_S = 90.0
    TRAFFIC_MIN_DIFF_S = 0.5

    def traffic(self, cars: list, player_idx) -> list:
        """When you will meet slower cars ahead, and faster cars from behind,
        at everyone's current pace. Constant-pace arithmetic on track position:
        the gap in lap fraction closes at (1/your pace - 1/their pace) laps per
        second. Speeds vary around a lap, so the place is approximate."""
        me = next((c for c in cars if c.get("idx") == player_idx), None)
        if me is None or me.get("surface") != "on_track" or me.get("pct") is None or me["pct"] < 0:
            return []
        pp = self.pace(player_idx)
        if not pp:
            return []
        out = []
        for c in cars:
            if c.get("idx") == player_idx or c.get("surface") != "on_track":
                continue
            if c.get("pct") is None or c["pct"] < 0:
                continue
            pc = self.pace(c["idx"])
            if not pc:
                continue
            if pc > pp + self.TRAFFIC_MIN_DIFF_S:            # slower car: you catch it
                d = (c["pct"] - me["pct"]) % 1.0
                rate = 1.0 / pp - 1.0 / pc
                tm = d / rate
                if tm <= self.TRAFFIC_HORIZON_S:
                    out.append({"idx": c["idx"], "kind": "you_catch", "inS": round(tm, 1),
                                "atPct": round((me["pct"] + tm / pp) % 1.0, 4)})
            elif pc < pp - self.TRAFFIC_MIN_DIFF_S:          # faster car: it catches you
                d = (me["pct"] - c["pct"]) % 1.0
                rate = 1.0 / pc - 1.0 / pp
                tm = d / rate
                if tm <= self.TRAFFIC_HORIZON_S:
                    out.append({"idx": c["idx"], "kind": "catches_you", "inS": round(tm, 1),
                                "atPct": round((me["pct"] + tm / pp) % 1.0, 4)})
        return sorted(out, key=lambda x: x["inS"])[:6]

    # ---- events from the field ---------------------------------------------
    CLOSING_EVENT_GAIN = 0.3
    CLOSING_EVENT_GAP = 3.0

    def field_events(self, cars: list, t, player_idx, stops: dict):
        ev = self._ev
        by = {c["idx"]: c for c in cars}
        me = by.get(player_idx)
        # pit in / out
        for i, c in by.items():
            onr = bool(c.get("onPitRoad"))
            was = ev["pit"].get(i)
            ev["pit"][i] = onr
            if was is None:
                continue
            if onr and not was:
                self.event(t, "pit_in", idx=i, carLap=c.get("lapCompleted"))
            elif was and not onr:
                d = c.get("derived") or {}
                s = stops.get(i)
                self.event(t, "pit_out", idx=i, stationaryS=d.get("lastStopS") if d.get("stopsSeen") else None,
                           laneLossS=s["laneLossS"] if s else None)
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
                if seeded and cb is not None:
                    overall = best_all is None or b < best_all - 1e-4
                    self.event(t, "fastest_lap", idx=c["idx"], lapS=round(b, 3),
                               classId=c.get("classId"), overall=overall)
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
                         lambda old, new, cid=cid: self.event(t, "class_lead", idx=new, prevIdx=old, classId=cid))
        if me is None:
            return
        # someone closing on you, or you on them (class rivals, once per approach)
        for i, c in by.items():
            if i == player_idx or c.get("classId") != me.get("classId"):
                continue
            d = c.get("derived") or {}
            tp, gain = d.get("toPlayerS"), d.get("youGainSPerLap")
            key = None
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
        flags = set(session.get("flags") or [])
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

    # Battles, for the broadcast. A pair of cars is in a battle when they are
    # next to each other IN THEIR CLASS and the gap between their F2Times is under
    # BATTLE_ENTER_S; it stays one until the gap opens past BATTLE_LEAVE_S, so a
    # gap hovering at 1.0 s does not make the graphic flicker on and off. Pairs
    # that touch become one group (a train). Overall-adjacent is the wrong test
    # in a multiclass race: a GT4 0.5 s behind a GT3 is traffic, not a battle.
    BATTLE_ENTER_S = 1.0
    BATTLE_LEAVE_S = 1.5

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
                if not (a.get("gapValid") and b.get("gapValid")):
                    continue
                if a.get("onPitRoad") or b.get("onPitRoad"):
                    continue
                gap = b["f2TimeS"] - a["f2TimeS"]
                if gap < 0:
                    continue
                pair = (a["idx"], b["idx"])
                limit = self.BATTLE_LEAVE_S if pair in live else self.BATTLE_ENTER_S
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
        for grp in groups:
            grp["id"] = f"{grp['classId']}:{grp['cars'][0]}"
        groups.sort(key=lambda g: (g["forClassPos"], g["forPos"] or 999))
        return groups

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
            # tow / reset: out of the world mid-session, or in the box without the lane
            if was in ("on_track", "off_track") and surf == "not_in_world":
                self._signal(st, c, t_play, "tow", session_num)
            elif was in ("on_track", "off_track") and surf == "in_pit_stall":
                self._signal(st, c, t_play, "tow", session_num)
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

    @staticmethod
    def _close_lap_sectors(st, t, n):
        """A lap's own sector times, kept apart from the rolling latest-per-sector
        list: a void sector stays None for that lap instead of showing the time
        from the lap before."""
        st["lapDone"] = (t, list(st["lap"]))
        st["lap"] = [None] * n

    # ------------------------------------------------------------------ #
    # Everything about one car that can be had for ANY car: the booth is
    # mostly used spectating, where only the CarIdx* arrays and the session
    # info are available. Fuel, tyres and in-car settings are your car only.
    # ------------------------------------------------------------------ #

    # Measured on the fake against its true speed: 0.25 s smoothing lagged
    # braking by up to 4 m/s (95th percentile), 0.08 s by 1.3. The fake has no
    # sampling noise and the sim's network-updated positions may, so 0.1 s.
    SPEED_SMOOTH_S = 0.1

    def car_speed(self, c, t, track_len):
        """Speed from how far along the lap the car moved since the last sample.
        iRacing gives other cars' positions but not their speed. Smoothed over
        ~0.1 s; a jump in position (reset, tow, replay cut) starts it again."""
        idx, pct = c.get("idx"), c.get("pct")
        sp = self._spd.setdefault(idx, {"t": None, "pct": None, "v": None, "max": None})
        if (not track_len or pct is None or pct < 0 or t is None
                or c.get("surface") in (None, "not_in_world")):
            sp.update(t=None, pct=None, v=None)
            return None
        v = None
        if sp["t"] is not None and t > sp["t"]:
            dt = t - sp["t"]
            d = ((pct - sp["pct"] + 0.5) % 1.0) - 0.5        # signed lap fraction
            if -0.001 < d < 0:
                d = 0.0                                      # sampling jitter, not reversing
            if 0 <= d < 0.05 and dt < 1.0:
                raw = d * track_len / dt
                if raw < 150.0:                              # 540 km/h: anything above is a glitch
                    a = 1.0 - math.exp(-dt / self.SPEED_SMOOTH_S)
                    v = raw if sp["v"] is None else sp["v"] + a * (raw - sp["v"])
        sp["t"], sp["pct"], sp["v"] = t, pct, v
        if v is not None and not c.get("onPitRoad"):
            sp["max"] = v if sp["max"] is None else max(sp["max"], v)
        return v

    def _take_top_speed(self, idx):
        sp = self._spd.get(idx)
        if not sp or sp["max"] is None:
            return None
        top, sp["max"] = sp["max"], None
        return round(top, 1)

    def car_extras(self, c, t, grid_by_idx, fast_limit, is_race, team_race) -> dict:
        idx = c["idx"]
        out = {}
        racing = [e["lapS"] for e in self._laps.get(idx, []) if e["lapS"] and not e["pit"]][-5:]
        if len(racing) >= 3:
            m = sum(racing) / len(racing)
            out["consistencyS"] = round(math.sqrt(sum((x - m) ** 2 for x in racing) / len(racing)), 3)
        else:
            out["consistencyS"] = None
        tops = [e["topSpeedMs"] for e in self._laps.get(idx, []) if e.get("topSpeedMs")]
        out["topSpeedBestMs"] = max(tops) if tops else None
        g = grid_by_idx.get(idx) if is_race else None
        out["gridPos"] = g["pos"] if g else None
        out["gridClassPos"] = g["classPos"] if g else None
        out["posGained"] = g["pos"] - c["pos"] if g and c.get("pos") else None
        out["classPosGained"] = (g["classPos"] - c["classPos"]
                                 if g and g.get("classPos") and c.get("classPos") else None)
        laps = self._laps.get(idx, [])
        out["lapsLedSeen"] = sum(1 for e in laps if e.get("pos") == 1)
        out["classLapsLedSeen"] = sum(1 for e in laps if e.get("classPos") == 1)
        # Stints, from stops watched entry to exit. A stint that started before
        # we connected (and not at the start of the race) has no known length.
        p = self._pit.get(idx) or {}
        stints = list(p.get("stints") or [])
        start = p.get("stintStart")
        out["stintsSeen"] = stints
        out["stintStartLap"] = start
        out["lapsThisStint"] = (c["lap"] - start) if start is not None and c.get("lap") is not None else None
        out["longestStintLaps"] = max(stints) if stints else None
        out["nextStopLapEst"] = start + max(stints) if stints and start is not None else None
        # Tyres: a compound change is the only tyre fact iRacing gives for
        # another car (same-compound changes are invisible).
        cs = self._cmp.get(idx)
        comp = c.get("compound")
        if cs is None:
            self._cmp[idx] = cs = {"c": comp, "changes": 0}
        elif comp is not None and comp != cs["c"]:
            if cs["c"] is not None:
                cs["changes"] += 1
                self.event(t, "compound_change", idx=idx, fromCompound=cs["c"], toCompound=comp)
            cs["c"] = comp
        out["compoundChanges"] = cs["changes"]
        used = c.get("fastRepairsUsed")
        out["fastRepairsLeft"] = (max(0, fast_limit - used)
                                  if isinstance(fast_limit, int) and isinstance(used, (int, float)) and used >= 0
                                  else None)
        out["drivers"] = self._driver_stints(c, t, team_race)
        return out

    def _driver_stints(self, c, t, team_race):
        """Team races: who is driving, and how long each driver has been in the
        car since we connected. iRacing names the current driver per car in the
        session info; a change there is a driver swap."""
        idx = c["idx"]
        key = c.get("userId") if c.get("userId") is not None else c.get("name")
        if key is None or t is None:
            return None
        st = self._drv.get(idx)
        if st is None:
            st = self._drv[idx] = {"cur": key, "t": t, "lap": c.get("lapCompleted"), "tot": {}}
            st["tot"][key] = {"name": c.get("name"), "timeS": 0.0, "laps": 0}
        rec = st["tot"].setdefault(st["cur"], {"name": c.get("name"), "timeS": 0.0, "laps": 0})
        rec["timeS"] += max(0.0, t - st["t"])
        lc = c.get("lapCompleted")
        if lc is not None and st["lap"] is not None and lc > st["lap"]:
            rec["laps"] += lc - st["lap"]
        st["t"], st["lap"] = t, lc
        if key != st["cur"]:
            prev = st["tot"].get(st["cur"], {}).get("name")
            self.event(t, "driver_change", idx=idx, fromName=prev, toName=c.get("name"))
            st["cur"] = key
            st["tot"].setdefault(key, {"name": c.get("name"), "timeS": 0.0, "laps": 0})
        if not team_race and len(st["tot"]) < 2:
            return None
        return [{"name": v["name"], "timeS": round(v["timeS"], 1), "laps": v["laps"], "current": k == st["cur"]}
                for k, v in st["tot"].items()]

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

    @staticmethod
    def laps_remaining(laps_remain_ex, time_remain_s, lap_time_s):
        """(laps, is_estimate) or (None, False).

        A lap-limited session states it outright. A timed one does not, so it has
        to be projected from the clock and the last lap -- which is a guess, and
        is flagged as one. iRacing uses 32767 as "unlimited"; anything at or above
        that is not a count.
        """
        if laps_remain_ex is not None and 0 <= laps_remain_ex < 32767:
            return int(laps_remain_ex), False
        if time_remain_s and lap_time_s and lap_time_s > 0 and time_remain_s < 86400 * 3:
            return math.ceil(time_remain_s / lap_time_s), True
        return None, False

    def fuel(self, lap_completed, fuel_level, laps_remain_ex=None,
             time_remain_s=None, lap_time_s=None, on_pit_road=None, lap_pct=None) -> dict:
        out = {"fuelPerLap": None, "lapsOfFuel": None, "fuelPerLapSamples": len(self._fuel_per_lap_samples),
               "lapsRemaining": None, "lapsRemainingIsEstimate": False,
               "fuelToFinishL": None, "fuelMarginL": None, "fuelSavePerLapL": None}
        laps_left, est = self.laps_remaining(laps_remain_ex, time_remain_s, lap_time_s)
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


def vs_you(c: dict, me: dict, their_pace, my_pace) -> dict:
    """Head-to-head: this car minus you. Negative = they are quicker."""
    def diff(a, b):
        return round(a - b, 3) if a is not None and b is not None and a > 0 and b > 0 else None
    tb, mb = (c.get("derived") or {}).get("bestSectorS") or [], (me.get("derived") or {}).get("bestSectorS") or []
    return {"lastLapS": diff(c.get("lastLapS"), me.get("lastLapS")),
            "bestLapS": diff(c.get("bestLapS"), me.get("bestLapS")),
            "paceS": diff(their_pace, my_pace),
            "bestSectorS": [diff(a, b) for a, b in zip(tb, mb)] if tb and mb and len(tb) == len(mb) else None}


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


def build_state(src: TelemetrySource, deriver: Deriver) -> dict:
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
    derived = deriver.fuel(player["lapCompleted"], player["fuelL"],
                           laps_remain_ex=_num(g("SessionLapsRemainEx")),
                           time_remain_s=_num(g("SessionTimeRemain")),
                           lap_time_s=_num(g("LapLastLapTime")),
                           on_pit_road=player["onPitRoad"], lap_pct=player["lapPct"])
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
    derived["liveEdge"] = live_edge(g)
    deriver.state_events(session["timeS"], session, player, weather)
    return {"type": "state", "schema": SCHEMA_VERSION, "t": session["timeS"],
            "session": session, "weather": weather, "player": player, "camera": camera, "derived": derived}


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
            "compound": _arr(comp, i),
            # The class's reference lap from the session info: what CarIdxEstTime
            # is measured against (its value at the line).
            "classEstLapS": d.get("CarClassEstLapTime") if d else None,
            # iRacing's paint scheme for the car and helmet ("pattern,col1,col2,col3").
            # Not the Trading Paints livery, which only exists as a texture.
            "carDesign": d.get("CarDesignStr") if d else None,
            "helmetDesign": d.get("HelmetDesignStr") if d else None,
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
    by_idx = {c["idx"]: c for c in cars}
    own_idx = _num(g("PlayerCarIdx"))
    own = by_idx.get(own_idx)                  # your car, when you are driving
    cam_idx = _num(g("CamCarIdx"))
    manual = deriver.focus_manual if deriver is not None else None
    # Spectating is the normal case for this booth: PlayerCarIdx is then a
    # spectator slot with no car, and the camera car is what is being watched.
    if manual is not None and manual in by_idx:
        player_idx, ref_source = manual, "chosen"
    elif own is not None:
        player_idx, ref_source = own_idx, "your_car"
    elif cam_idx in by_idx:
        player_idx, ref_source = cam_idx, "camera"
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
    for c in cars:
        d = c["derived"]
        d["toPlayerS"] = None
        d["classGapS"] = None
        if not is_race or not c["gapValid"]:
            continue
        lead = class_leader.get(c.get("classId"))
        if lead is not None:
            d["classGapS"] = round(c["f2TimeS"] - lead["f2TimeS"], 3)
        if me is not None and me["gapValid"] and c is not me:
            d["toPlayerS"] = round(c["f2TimeS"] - me["f2TimeS"], 3)

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
                c.get("surface") == "in_pit_stall", c.get("lap"), pct=c.get("pct")))
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
            bs = c["derived"].get("bestSectorS")
            if bs and all(v is not None for v in bs):
                theo = round(sum(bs), 3)
                c["derived"]["theoreticalBestS"] = theo
                if c.get("bestLapS") and c["bestLapS"] > 0:
                    c["derived"]["bestLapLeftS"] = round(c["bestLapS"] - theo, 3)

    live_gaps(ordered)
    out = {"type": "cars", "schema": SCHEMA_VERSION, "t": _num(g("SessionTime")), "cars": cars}
    if deriver is not None:
        for c in cars:
            deriver.record_lap(c, now)
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
        out["derived"] = {
            "ref": {"idx": player_idx, "source": ref_source, "own": own_idx if own is not None else None,
                    "camera": cam_idx if cam_idx in by_idx else None},
            "sessionBestSectorS": deriver.session_best_sectors(),
            "isRace": is_race,
            "pitLoss": deriver.pit_loss(),
            "rejoin": deriver.rejoin(cars, player_idx) if is_race and me is not None else None,
            "undercut": deriver.undercut(cars, player_idx, now) if is_race and me is not None else [],
            "traffic": deriver.traffic(cars, player_idx),
            "compoundPace": deriver.compound_pace(cars),
            "nearby": deriver.nearby(cars, player_idx, track_length_m(session_msg), now),
            "battles": deriver.battles(cars, ahead_of, is_race),
            "predictedIncidents": deriver.predicted_incidents(),
            "race": race_length(session_msg, session_num, ordered, _num(g("SessionTimeRemain")),
                                deriver, decode_bits(g("SessionFlags"), FLAG_BITS)),
        }
        edge = live_edge(g)
        out["derived"]["liveEdge"] = edge
        # Race events describe the race as it happens. While a replay is being
        # watched the field data is from the past, and pit stops or lead changes
        # read from it would be old news reported as new.
        if edge["atLive"]:
            deriver.field_events(cars, now, player_idx, deriver._stops_tick)
        deriver.incidents(cars, edge, session_num,
                          surface_kind(((session_msg or {}).get("track") or {}).get("TrackType")),
                          state=SESSION_STATE.get(g("SessionState"), "invalid"),
                          flags=decode_bits(g("SessionFlags"), FLAG_BITS))
    return out


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
    lap_b = b.get("classEstLapS")
    if da is None or db is None or not lap_b or lap_b <= 0:
        return None, None
    d = da - db
    if d < -0.02 or d > 200:            # crossing the line out of step, or nonsense
        return None, None
    d = max(d, 0.0)
    laps = int(d + 1e-6)
    ea, eb = a.get("estTimeS"), b.get("estTimeS")
    same = a.get("classId") is not None and a.get("classId") == b.get("classId")
    if same and ea is not None and eb is not None and ea >= 0 and eb >= 0:
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


def race_length(session_msg, session_num, ordered: list, time_remain_s, deriver, flags) -> dict:
    """How much of the session is left, from the LEADER, not the player.

    SessionLapsRemainEx is the player's own count, and a spectator has no car:
    a lap-limited race then showed the session clock, which ran on past the
    chequered flag. The lap limit comes from the session info (SessionLaps) and
    the leader's completed laps; the time limit from SessionTimeRemain. When a
    race has both, the one that ends it first is estimated from the leader's
    pace (derived)."""
    sess = next((s for s in (session_msg or {}).get("sessions") or [] if s.get("SessionNum") == session_num), {})
    total = _parse_laps(sess.get("SessionLaps"))
    limit_s = _parse_secs(sess.get("SessionTime"))
    leader = ordered[0] if ordered else None
    done = leader.get("lapCompleted") if leader else None
    if done is not None and done < 0:
        done = 0
    remain = time_remain_s if time_remain_s is not None and 0 <= time_remain_s < 7 * 86400 else None
    to_go = max(0, total - done) if total and done is not None else None
    pace = deriver.pace(leader["idx"]) if (deriver is not None and leader) else None
    by_time = None
    if limit_s and remain is not None and pace:
        by_time = int(math.ceil(remain / pace)) if remain > 0 else 0
    if total and (not limit_s or by_time is None or to_go is None or to_go <= by_time):
        ends = "laps"
    elif limit_s:
        ends = "time"
    else:
        ends = "laps" if total else None
    return {"totalLaps": total, "leaderLapsDone": done,
            "leaderLap": leader.get("lap") if leader else None,
            "lapsToGo": to_go, "timeRemainS": remain if limit_s or not total else remain,
            "timeLimited": bool(limit_s), "lapsByTime": by_time, "endsBy": ends,
            "white": "white" in (flags or []), "checkered": "checkered" in (flags or [])}


def _is_race(session_msg: dict | None, session_num) -> bool:
    for s in (session_msg or {}).get("sessions") or []:
        if s.get("SessionNum") == session_num:
            return "race" in str(s.get("SessionType") or "").lower()
    return False
