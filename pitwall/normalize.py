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


def _compound(v):
    return int(v) if isinstance(v, (int, float)) and v >= 0 else None


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
                                          "TrackAltitude", "TrackCity", "TrackCountry",
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
                    for k in ("IncidentLimit", "FastRepairsLimit", "StandingStart", "NumStarters",
                              # the weather settings and the sim's calendar and clock (the sun's path)
                              "WeatherType", "Skies", "WindDirection", "WindSpeed", "WeatherTemp",
                              "RelativeHumidity", "FogLevel", "TimeOfDay", "Date",
                              "EarthRotationSpeedupFactor")},
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
        # Per-car running figures, updated as each lap is finished: pace and
        # consistency read the last few racing laps, top speed and laps led the
        # whole race. They were recomputed from the lap list for every car at
        # every update -- which grew with the race -- and that list keeps only
        # 200 laps, so laps led undercounted past lap 200.
        self._lapsum: dict[int, dict] = {}
        # Observed trends (weather, your engine) and the session's peak g. They
        # were computed in the dashboard from what each screen had watched since
        # it connected; here they cover the session, for everyone alike.
        self._trend: dict[str, list] = {"weather": [], "engine": []}
        self._trend_at: dict[str, float | None] = {"weather": None, "engine": None}
        self._tod: list = []                    # (session time, time of day): the sim clock's speed
        self._outlook = None                    # (t, last outlook)
        self.gg_peak = 0.0
        self._lap_finished: list[dict] = []
        self._loss: list[dict] = []
        self._stops_tick: dict[int, dict] = {}
        self._tp_hist: dict[int, list[tuple[float, float]]] = {}
        self._tp_ref: dict[int, int | None] = {}
        self._uc: dict[int, dict] = {}
        self._uc_inpit: dict[int, bool] = {}
        self._player_lap = None
        self._player_idx = None
        # The car the SHARED stream measures everything relative from: your own
        # car when driving, else the camera car when spectating. A viewer who
        # wants another car gets it through a RefView, for them alone.
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
        # your car's fuel plan as of the last state update, for the strategy options
        self._fuel_info: dict | None = None
        self._caut = {"t0": None}
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
        # False while a replay is being watched: per-car changes read from the past
        # are not news (field_events is skipped then; this covers the per-car ones)
        self.events_live = True
        self._dist_prev: dict[int, float] = {}
        self._prof: dict = {}                      # class -> lap profile bins (learn_lap_profile)
        self._split: dict = {}                     # idx -> this lap's stretch crossings, and its best lap's (lap_splits)
        self._meet: dict = {}                      # smoothed meetings for the shared reference
        self._ev = {"pit": {}, "best": {}, "bestSeeded": False, "closing": {}, "stable": {}, "surf": {},
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
                              "onRoad": on_pit_road, "inBox": in_box, "away": pct is not None and pct < 0,
                              # a stint's length is only known if we saw it start:
                              # at the start of the race, or at a stop we watched
                              "stints": [], "stintStart": lap if lap is not None and 0 <= lap <= 1 else None}
            out["inPit"] = on_pit_road
            return out
        was_away = st.get("away", False)
        st["away"] = pct is not None and pct < 0     # out of the world (LapDistPct -1)
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
    STINT_SLOW_LAP = 1.07       # slower than the stint's quickest by more than this: not a racing lap

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
        # The trend is read from racing laps only: an out-lap, a lap behind the pace
        # car or one with a spin says nothing about the tyres (a caution lap ran
        # 2.5x slower in the fake race and made fresh tyres look worth minutes).
        quick = min(l[1] for l in laps)
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
    CONSISTENCY_LAPS = 5
    # name: (sample every s, samples kept, keys, minimum samples to show a trend)
    TRENDS = {"weather": (10.0, 90, ("airTempC", "trackTempC", "humidity", "precipitationPct", "windMs",
                                      "wetnessRaw", "solarAltitudeRad", "skiesRaw"), 2),
              "engine": (5.0, 360, ("waterTempC", "oilTempC", "oilPressBar"), 3)}
    TREND_DRAW_POINTS = 60      # a sparkline is ~60 px: more points draw nothing more

    def trends(self, t, weather: dict, player: dict, player_live: bool) -> dict:
        """What the weather and your engine have DONE: never a forecast (iRacing
        publishes none). Per trend: the span watched, each value's change from
        the first sample to the last, and a thinned series for a sparkline.
        Engine samples only while the player block is live -- spectating, it
        reads flat zeros. Also the session's peak combined g, from the same
        360 Hz accelerometer samples the grip trace draws."""
        out = {}
        for name, (every, keep, keys, need) in self.TRENDS.items():
            src = weather if name == "weather" else player
            hist = self._trend[name]
            last = self._trend_at[name]
            if t is not None and (name == "weather" or player_live) and (last is None or t - last >= every or t < last):
                if last is not None and t < last:
                    hist.clear()                     # the clock went back: a new session or a replay jump
                self._trend_at[name] = t
                hist.append((t, {k: src.get(k) for k in keys}))
                del hist[:-keep]
            if len(hist) < need:
                out[name + "Trend"] = None
                continue
            step = max(1, -(-len(hist) // self.TREND_DRAW_POINTS))
            tr = {"spanS": round(hist[-1][0] - hist[0][0], 1), "change": {}, "series": {}}
            for k in keys:
                vals = [s[k] for _t, s in hist if s.get(k) is not None]
                if len(vals) < 2:
                    continue
                tr["change"][k] = round(vals[-1] - vals[0], 4)
                tr["series"][k] = [round(v, 4) for v in vals[::step]] + ([round(vals[-1], 4)] if (len(vals) - 1) % step else [])
            out[name + "Trend"] = tr
        acc = player.get("accel") or {}
        if player_live:
            for lat, lon in zip(acc.get("latG") or [], acc.get("longG") or []):
                if lat is not None and lon is not None:
                    m = math.hypot(lat, lon)
                    if m > self.gg_peak:
                        self.gg_peak = m
        out["ggPeakG"] = round(self.gg_peak, 2) if self.gg_peak > 0.01 else None
        return out
    # ---- weather outlook ---------------------------------------------------
    # iRacing's own forecast and radar come from its servers to the sim's UI; the
    # SDK has none (docs/VARIABLES_TO_VERIFY.md). What can be worked out here, with
    # physics and what this session has done so far, is labelled as an estimate:
    #   * the sun's path, from the track's latitude, the date and where the sun is
    #     now (SolarAltitude), run at the sim's own clock speed;
    #   * the track temperature, from the air temperature plus a heating term in
    #     the sunlight reaching the track (clear-sky irradiance from the sun's
    #     height, cut by the cloud cover), fitted to this session's readings;
    #   * the air temperature, humidity and rain, carried on from their trend over
    #     the last 15 minutes (never more than an hour ahead);
    #   * the dew point (Magnus formula) and how fast the track is drying or wetting.
    OUTLOOK_STEPS_S = (900, 1800, 2700, 3600)
    TRACK_HEAT_PRIOR = 0.025          # degC of track above air per W/m2 of sunlight (asphalt, typical)
    SKY_COVER = {0: 0.0, 1: 0.375, 2: 0.75, 3: 1.0}      # okta fraction per Skies value
    AIR_TREND_MAX_C_PER_H = 4.0

    @staticmethod
    def irradiance(alt_rad, sky_cover):
        """Sunlight on level ground, W/m2: Haurwitz clear-sky model, then
        Kasten & Czeplak's cloud factor. Zero with the sun down."""
        if alt_rad is None or alt_rad <= 0:
            return 0.0
        sh = math.sin(alt_rad)
        clear = 1098.0 * sh * math.exp(-0.057 / sh)
        n = 0.0 if sky_cover is None else max(0.0, min(1.0, sky_cover))
        return clear * (1.0 - 0.75 * n ** 3.4)

    @staticmethod
    def dew_point(air_c, rh):
        """Magnus formula (Alduchov & Eskridge constants). rh as 0..1."""
        if air_c is None or rh is None or rh <= 0:
            return None
        g = math.log(min(rh, 1.0)) + 17.625 * air_c / (243.04 + air_c)
        return 243.04 * g / (17.625 - g)

    @staticmethod
    def _declination(day_of_year):
        return math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (284 + day_of_year)))

    @staticmethod
    def _slope(pts):
        n = len(pts)
        if n < 3:
            return None
        mx = sum(x for x, _ in pts) / n
        my = sum(y for _, y in pts) / n
        den = sum((x - mx) ** 2 for x, _ in pts)
        return sum((x - mx) * (y - my) for x, y in pts) / den if den > 0 else None

    OUTLOOK_EVERY_S = 5.0

    def weather_outlook(self, t, weather: dict, session: dict, session_msg: dict | None) -> dict | None:
        """Worked out every few seconds (the weather moves slowly; the sun's search
        is a few thousand steps), the last answer kept in between."""
        if t is None or weather.get("airTempC") is None:
            return None
        last = self._outlook
        if last is not None and 0 <= t - last[0] < self.OUTLOOK_EVERY_S:
            return last[1]
        out = self._weather_outlook(t, weather, session, session_msg)
        self._outlook = (t, out)
        return out

    def _weather_outlook(self, t, weather: dict, session: dict, session_msg: dict | None) -> dict | None:
        hist = [(ht, h) for ht, h in self._trend["weather"]]
        opts = (session_msg or {}).get("options") or {}
        track = (session_msg or {}).get("track") or {}
        out = {"dewPointC": None, "sun": None, "steps": [], "trackModel": None, "rain": None, "clockSpeed": None}
        dp = self.dew_point(weather.get("airTempC"), weather.get("humidity"))
        out["dewPointC"] = None if dp is None else round(dp, 1)
        # the sim clock: how many sim seconds pass per real second (time of day can be sped up)
        tod = session.get("timeOfDayS")
        self._tod.append((t, tod))
        del self._tod[:-60]
        speed = None
        pts = [(a, b) for a, b in self._tod if b is not None]
        if len(pts) >= 2 and pts[-1][0] - pts[0][0] >= 20:
            r = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
            if 0.5 < r < 200:
                speed = r
        if speed is None:
            try:
                f = float(str(opts.get("EarthRotationSpeedupFactor")).split()[0])
            except (TypeError, ValueError, IndexError):
                f = None
            speed = f if f and f > 0 else 1.0
        out["clockSpeed"] = round(speed, 2)
        # the sun: hour angle now from where the sun is, then forward at the sim's clock
        try:                                             # "37.161648 m": a number with a unit
            lat = float(str(track.get("TrackLatitude")).split()[0])
        except (TypeError, ValueError, IndexError):
            lat = None
        alt = weather.get("solarAltitudeRad")
        date = str(opts.get("Date") or "")
        sun = None
        alt_fn = None
        if lat is not None and alt is not None and re.match(r"^\d{4}-\d{2}-\d{2}", date):
            y, mo, d = (int(x) for x in date[:10].split("-"))
            doy = (mo - 1) * 30.4 + d
            phi, dec = math.radians(lat), self._declination(doy)
            c = math.cos(phi) * math.cos(dec)
            if abs(c) > 1e-6:
                cos_h = (math.sin(alt) - math.sin(phi) * math.sin(dec)) / c
                cos_h = max(-1.0, min(1.0, cos_h))
                h_abs = math.acos(cos_h)
                alts = [a for _t, a in ((ht, hh.get("solarAltitudeRad")) for ht, hh in hist) if a is not None]
                if len(alts) >= 2 and abs(alts[-1] - alts[0]) > 1e-4:
                    rising = alts[-1] > alts[0]
                else:
                    rising = tod is not None and tod < 12.5 * 3600
                h_now = -h_abs if rising else h_abs
                w = 2 * math.pi / 86400.0 * speed               # hour angle per real second

                def alt_at(dt):
                    hh = h_now + w * dt
                    return math.asin(max(-1.0, min(1.0, math.sin(phi) * math.sin(dec) + c * math.cos(hh))))

                def crossing(target_rad, going_down):
                    step = 120.0
                    prev = alt_at(0.0)
                    for k in range(1, int(86400 / speed / step) + 2):
                        a = alt_at(k * step)
                        if (going_down and prev >= target_rad > a) or (not going_down and prev < target_rad <= a):
                            return round(k * step)
                        prev = a
                    return None
                horizon = math.radians(-0.833)
                sun = {"altDeg": round(math.degrees(alt), 1), "rising": rising,
                       "setsInS": crossing(horizon, True) if alt > horizon else None,
                       "risesInS": crossing(horizon, False) if alt <= horizon else None}
                out["sun"] = sun
                alt_fn = alt_at
        # the track's heating in the sun, fitted to this session where the sun has moved enough
        cover = self.SKY_COVER.get(weather.get("skiesRaw"))
        samples = []
        for _ht, hh in hist:
            if None in (hh.get("trackTempC"), hh.get("airTempC")):
                continue
            samples.append((self.irradiance(hh.get("solarAltitudeRad"), self.SKY_COVER.get(hh.get("skiesRaw"))),
                            hh["trackTempC"] - hh["airTempC"]))
        b, basis = self.TRACK_HEAT_PRIOR, "typical"
        if len(samples) >= 6 and max(x for x, _ in samples) - min(x for x, _ in samples) >= 150:
            fit = self._slope(samples)
            if fit is not None and 0.0 < fit < 0.08:
                b, basis = fit, "fitted"
        a = (sum(y - b * x for x, y in samples) / len(samples)) if samples else \
            ((weather.get("trackTempC") or weather["airTempC"]) - weather["airTempC"]
             - b * self.irradiance(alt, cover))
        out["trackModel"] = {"basis": basis, "cPerWm2": round(b, 4), "offsetC": round(a, 2),
                             "sunWm2": round(self.irradiance(alt, cover))}
        # trends over the last 15 minutes
        def trend(key):
            p = [(ht, hh.get(key)) for ht, hh in hist if hh.get(key) is not None and t - ht <= 900]
            if len(p) < 6 or p[-1][0] - p[0][0] < 120:
                return None
            return self._slope(p)
        air_s = trend("airTempC")
        if air_s is not None:
            cap = self.AIR_TREND_MAX_C_PER_H / 3600
            air_s = max(-cap, min(cap, air_s))
        hum_s, rain_s, wet_s = trend("humidity"), trend("precipitationPct"), trend("wetnessRaw")
        sun_now = self.irradiance(alt, cover)
        for dt in self.OUTLOOK_STEPS_S:
            air = weather["airTempC"] + (air_s or 0.0) * dt
            sun_alt = alt_fn(dt) if alt_fn else alt
            # anchored on the reading now: only the changes in air and sunlight move it
            track_now = weather.get("trackTempC")
            track_c = (track_now + (air - weather["airTempC"]) + b * (self.irradiance(sun_alt, cover) - sun_now)
                       if track_now is not None else air + a + b * self.irradiance(sun_alt, cover))
            hum = weather.get("humidity")
            if hum is not None and hum_s is not None:
                hum = max(0.0, min(1.0, hum + hum_s * dt))
            rain = weather.get("precipitationPct")
            if rain is not None and rain_s is not None:
                rain = max(0.0, min(1.0, rain + rain_s * dt))
            out["steps"].append({"inS": dt, "airC": round(air, 1), "trackC": round(track_c, 1),
                                 "sunAltDeg": None if sun_alt is None else round(math.degrees(sun_alt), 1),
                                 "humidity": None if hum is None else round(hum, 3),
                                 "rainPct": None if rain is None else round(rain, 3)})
        wet = weather.get("wetnessRaw")
        if wet is not None or rain_s is not None:
            per10 = None if wet_s is None else round(wet_s * 600, 2)
            nxt = None
            if wet_s and abs(wet_s) > 1e-5 and wet is not None and wet >= 1:
                target = math.floor(wet) - 1 if wet_s < 0 else math.floor(wet) + 1
                if 1 <= target <= 7:
                    nxt = {"level": target, "inS": round(abs((target - wet) / wet_s))}
            out["rain"] = {"rainTrendPer10Min": None if rain_s is None else round(rain_s * 600, 3),
                           "wetnessTrendPer10Min": per10, "nextWetness": nxt,
                           "state": None if wet_s is None or abs(wet_s * 600) < 0.05
                           else ("drying" if wet_s < 0 else "getting wetter")}
        return out

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
        # iRacing's active reset (lone practice, your car only) puts the car back (or on) to a
        # saved point while the lap clock runs: the lap distance jumps. That lap's time means
        # nothing, so it is kept out of pace, consistency and best laps.
        pct = c.get("pct")
        if idx == self._player_idx and pct is not None and pct >= 0:
            pp, pt = st.get("pp"), st.get("pt")
            if pp is not None and lc == st["lc"] and not c.get("onPitRoad"):
                dd, dt = pct - pp, max(0.0, t - pt)
                if not (pp > 0.9 and pct < 0.1) and (dd < -0.003 or dd > 0.01 + dt * 0.05):
                    st["reset"] = True
            st["pp"], st["pt"] = pct, t
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
                          "reset": bool(st.pop("reset", False)),
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
        s = self._lapsum.setdefault(idx, {"racing": [], "top": None, "led": 0, "classLed": 0})
        if e["lapS"] and not e["pit"] and not e.get("reset") and (s.get("best") is None or e["lapS"] < s["best"]):
            s["best"] = e["lapS"]
        # a lap behind the pace car (well over this car's best) is not a racing lap either
        slow = s.get("best") is not None and e["lapS"] and e["lapS"] > s["best"] * self.PACE_SLOW_LAP
        if e["lapS"] and not e["pit"] and not e.get("reset") and not slow:
            s["racing"].append(e["lapS"])
            del s["racing"][:-max(self.PACE_LAPS, self.CONSISTENCY_LAPS)]
        if e.get("topSpeedMs") and (s["top"] is None or e["topSpeedMs"] > s["top"]):
            s["top"] = e["topSpeedMs"]
        s["led"] += e.get("pos") == 1
        s["classLed"] += e.get("classPos") == 1
        self._feed_add("lap", e)
        self._lap_finished.append(e)
        return e

    PACE_SLOW_LAP = 1.5         # a lap this much over the car's best: a caution lap, not racing pace

    def pace(self, idx) -> float | None:
        """Median of this car's recent racing laps: no in- or out-laps, no
        unknown times. Two laps minimum, so one lap cannot stand for a pace."""
        laps = (self._lapsum.get(idx) or {}).get("racing", [])[-self.PACE_LAPS:]
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

    # ---- traffic: where and when the followed car meets another -----------
    # Worked in lap TIME, not lap distance. In distance, two cars a second apart
    # are 20 m apart in a hairpin and 80 m on a straight, so a constant-pace sum
    # on lap fraction swung the catch time and threw the marker round the map.
    # CarIdxEstTime / CarClassEstLapTime ("u") is how far round the lap a car is
    # in time; at a steady pace it grows evenly, so the meeting time is stable,
    # and the meeting place is turned back into a track position through each
    # class's lap profile (u against lap fraction), learned as the cars run.
    TRAFFIC_HORIZON_S = 90.0
    TRAFFIC_MIN_DIFF_S = 0.5
    MEET_HORIZON_S = 600.0
    PROFILE_BINS = 200
    MEET_SMOOTH_S = 2.0

    def learn_lap_profile(self, cars: list) -> None:
        """Per class: the average lap-time fraction at each part of the lap, from
        cars on track (shared stream only; followed views read it)."""
        for c in cars:
            pct, est, lap = c.get("pct"), c.get("estTimeS"), c.get("classEstLapS")
            if c.get("surface") != "on_track" or c.get("onPitRoad") or pct is None or not 0 <= pct < 1 \
                    or est is None or est < 0 or not lap or lap <= 0:
                continue
            u = est / lap
            if not 0 <= u <= 1.05:
                continue
            bins = self._prof.setdefault(c.get("classId"), {})
            b = int(pct * self.PROFILE_BINS) % self.PROFILE_BINS
            sp, su, n = bins.get(b, (0.0, 0.0, 0))
            if n >= 50:                              # a running average over the latest ~50
                sp, su, n = sp * 49 / 50, su * 49 / 50, 49
            bins[b] = (sp + pct, su + u, n + 1)

    # ---- where they are faster: each car's best lap timed over equal stretches of the track ----
    # Every car's position is known all the time (CarIdxLapDistPct), so the time it reaches each 1/24 of
    # the lap can be interpolated between two samples. A lap timed that way from line to line, with no pit
    # visit and no jump, at racing pace, is kept when it is the car's quickest. Two cars' best laps compared
    # stretch by stretch say where one is faster than the other: no inputs needed, any car.
    STRETCHES = 24

    def lap_splits(self, cars: list, t) -> None:
        if t is None:
            return
        K = self.STRETCHES
        for c in cars:
            i, pct = c.get("idx"), c.get("pct")
            st = self._split.setdefault(i, {"last": None, "cross": None, "best": None, "bestS": None, "bad": False})
            ok = pct is not None and pct >= 0 and c.get("surface") in ("on_track", "off_track")
            if c.get("onPitRoad") or not ok:
                st["bad"], st["last"] = True, None if not ok else (pct, t)
                continue
            last = st["last"]
            st["last"] = (pct, t)
            if last is None or t <= last[1]:
                continue
            p0, t0 = last
            step = pct - p0
            wrapped = p0 > 0.9 and pct < 0.1
            if wrapped:
                step += 1.0
            if step < 0 or step > 0.05:              # a jump: a reset, a tow, a replay cut
                st["bad"] = True
                continue
            at = lambda q: t0 + (q - p0) / step * (t - t0) if step > 0 else t
            if wrapped:                               # the line: finish the lap, start the next
                tl = at(1.0)
                cr = st["cross"]
                if cr is not None and not st["bad"] and len(cr) == K:
                    prof = [round(x - cr[0], 3) for x in cr] + [round(tl - cr[0], 3)]
                    lap = prof[-1]
                    best = st["bestS"]
                    # never quicker than iRacing's own best for the car (a timing slip), and no slower than ever
                    floor = (c.get("bestLapS") or 0) * 0.98
                    if lap > 5 and lap >= floor and (best is None or lap < best):
                        st["best"], st["bestS"] = prof, lap
                st["cross"], st["bad"] = [tl], False
                p0, t0, step = 0.0, tl, pct
                at = lambda q: t0 + (q - p0) / step * (t - t0) if step > 0 else t
            cr = st["cross"]
            if cr is None:
                continue
            k = len(cr)                               # the next boundary to cross is k/K
            while k < K and pct >= k / K:
                cr.append(at(k / K))
                k += 1

    def faster(self, a, b) -> dict | None:
        """Car a against car b, best lap against best lap, over STRETCHES equal parts of the track:
        seconds a gains in each (negative: a loses), and each lap's time at every boundary."""
        pa, pb = (self._split.get(a) or {}).get("best"), (self._split.get(b) or {}).get("best")
        if not pa or not pb:
            return None
        seg = [round((pb[k + 1] - pb[k]) - (pa[k + 1] - pa[k]), 3) for k in range(self.STRETCHES)]
        return {"a": a, "b": b, "gainS": seg, "totalS": round(pb[-1] - pa[-1], 3), "lapA": pa, "lapB": pb}

    def faster_pairs(self, cars: list) -> list:
        """Every car against the car ahead of it in its class (the battles and the battle box), when
        both have a best lap timed by stretches."""
        by: dict = {}
        for c in cars:
            if c.get("classPos") and c["classPos"] > 0:
                by.setdefault(c.get("classId"), []).append(c)
        out = []
        for L in by.values():
            L.sort(key=lambda c: c["classPos"])
            for x, y in zip(L, L[1:]):
                f = self.faster(x["idx"], y["idx"])
                if f:
                    out.append(f)
        return out

    def _u(self, c):
        est, lap = c.get("estTimeS"), c.get("classEstLapS")
        if est is not None and est >= 0 and lap and lap > 0 and 0 <= est / lap <= 1.05:
            return min(est / lap, 0.99999)
        pct = c.get("pct")
        return pct if pct is not None and pct >= 0 else None

    def _pct_of_u(self, class_id, u):
        """Track position at lap-time fraction u, from the class's lap profile; the
        lap-time fraction itself when too little of the lap has been seen."""
        bins = self._prof.get(class_id) or {}
        pts = sorted((sp / n, su / n) for sp, su, n in bins.values() if n)
        if len(pts) < self.PROFILE_BINS // 4:
            return u % 1.0
        u = u % 1.0
        pts = [(p - 1.0, uu - 1.0) for p, uu in pts[-3:]] + pts + [(p + 1.0, uu + 1.0) for p, uu in pts[:3]]
        for (p0, u0), (p1, u1) in zip(pts, pts[1:]):
            if u0 <= u <= u1 and u1 > u0:
                return (p0 + (p1 - p0) * (u - u0) / (u1 - u0)) % 1.0
        return u

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

    # ---- events from the field ---------------------------------------------
    CLOSING_EVENT_GAIN = 0.3
    CLOSING_EVENT_GAP = 3.0

    # Before the start (cars leaving the pits for the grid) and after the flag
    # (cars driving back in) pit lane traffic is not a pit stop.
    PIT_EVENT_STATES = {"parade_laps", "racing", None}

    def field_events(self, cars: list, t, player_idx, stops: dict, state=None):
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

    # A tyre or driver change shows up while the car is in the pits or soon after
    # it leaves (the session info that names the driver can lag the stop).
    PIT_CHANGE_WINDOW_S = 120.0

    def car_extras(self, c, t, grid_by_idx, fast_limit, is_race, team_race) -> dict:
        idx = c["idx"]
        out = {}
        s = self._lapsum.get(idx) or {"racing": [], "top": None, "led": 0, "classLed": 0}
        racing = s["racing"][-self.CONSISTENCY_LAPS:]
        if len(racing) >= 3:
            m = sum(racing) / len(racing)
            out["consistencyS"] = round(math.sqrt(sum((x - m) ** 2 for x in racing) / len(racing)), 3)
        else:
            out["consistencyS"] = None
        out["topSpeedBestMs"] = s["top"]
        g = grid_by_idx.get(idx) if is_race else None
        out["gridPos"] = g["pos"] if g else None
        out["gridClassPos"] = g["classPos"] if g else None
        out["posGained"] = g["pos"] - c["pos"] if g and c.get("pos") else None
        out["classPosGained"] = (g["classPos"] - c["classPos"]
                                 if g and g.get("classPos") and c.get("classPos") else None)
        out["lapsLedSeen"] = s["led"]
        out["classLapsLedSeen"] = s["classLed"]
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
        # Tyres can only be changed in the pits, so a compound that differs anywhere
        # else (the sim catching up as the field loads, a choice made in the garage)
        # is taken in quietly, not reported as a change.
        cs = self._cmp.get(idx)
        comp = c.get("compound")
        in_pits = bool(c.get("onPitRoad")) or c.get("surface") == "in_pit_stall"
        if cs is None:
            self._cmp[idx] = cs = {"c": comp, "changes": 0, "pitT": t if in_pits else None}
        if in_pits:
            cs["pitT"] = t
        if comp is not None and comp != cs["c"]:
            recent = cs.get("pitT") is not None and t is not None and t - cs["pitT"] <= self.PIT_CHANGE_WINDOW_S
            if cs["c"] is not None and recent and c.get("surface") != "not_in_world":
                cs["changes"] += 1
                if self.events_live:
                    self.event(t, "compound_change", idx=idx, fromCompound=cs["c"], toCompound=comp,
                               classId=c.get("classId"))
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
            # drivers swap in the pit box; a name changing anywhere else is the
            # session info catching up, counted but not announced
            pit_t = (self._cmp.get(idx) or {}).get("pitT")
            if self.events_live and pit_t is not None and t - pit_t <= self.PIT_CHANGE_WINDOW_S:
                self.event(t, "driver_change", idx=idx, fromName=prev, toName=c.get("name"))
            st["cur"] = key
            st["tot"].setdefault(key, {"name": c.get("name"), "timeS": 0.0, "laps": 0})
        if not team_race and len(st["tot"]) < 2:
            return None
        total = sum(v["timeS"] for v in st["tot"].values())
        rows = [{"name": v["name"], "timeS": round(v["timeS"], 1), "laps": v["laps"], "current": k == st["cur"],
                 # share of the time driven since we connected; an even split is 100 / drivers
                 "sharePct": round(v["timeS"] / total * 100, 1) if total > 0 else None}
                for k, v in st["tot"].items()]
        # The driver with the least time is next in an even rotation (a suggestion,
        # not iRacing's rule: its minimums are per event and not in the telemetry).
        others = [r for r in rows if not r["current"]]
        nxt = min(others, key=lambda r: r["timeS"]) if others else None
        for r in rows:
            r["next"] = r is nxt
            r["evenPct"] = round(100 / len(rows), 1)       # an even split, to compare sharePct with
        return rows

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
                if s and s > 0:
                    by_class.setdefault(c.get("classId"), []).append(s)
        own = next((c for c in cars if c["idx"] == own_idx), None)
        fuel_range = math.floor(tank_l / fpl) if fpl and tank_l and fpl > 0 else None
        for c in cars:
            d = c["derived"]
            d["stopDue"] = None
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
        if not flags:                         # no flags at all: a replay (live always carries some bits)
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
        track = self._median([s["laneS"] - (s["boxS"] or 0.0) - s["laneLossS"] for s in self._loss])
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
                opts.append({"kind": "as_forecast", "stops": owed, "lossS": owed * green, "lap": sd.get("lap") if owed else None})
        # fresh tyres: worth a stop when the stint's fall-off over the laps left beats the stop
        deg, age = md.get("degSPerLap"), md.get("stintLaps")
        if lane is not None and deg and deg >= 0.03 and age and age >= 5 and to_go >= 3 and opts:
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
            o.update(self._finish_estimate(cars, me, o["lossS"], to_go, green))
        tyres = [o for o in opts if o["kind"] == "tyres"]
        for o in tyres:                 # the tyre gain comes off its time
            o["netS"] = round(o["lossS"] - o["tyreGainS"], 1)
        for o in opts:
            o.setdefault("netS", o["lossS"])
        if opts:
            out["best"] = min(opts, key=lambda o: (o["netS"], o.get("savePct") or 0))["kind"]
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
        self._meet.clear()
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
    pl_loss = deriver.pit_loss()
    derived["fuelSave"] = fuel_save(plan, derived["fuelPerLap"], player["fuelL"], deriver.tank_l,
                                    player["lapPct"], pl_loss["laneLossS"], pl_loss["boxMedianS"])
    deriver._fuel_info = ({"plan": plan, "fpl": derived["fuelPerLap"], "fuelL": player["fuelL"],
                           "tankL": deriver.tank_l, "refuelLps": rate}
                          if derived["fuelPerLap"] and player["fuelL"] is not None else None)
    derived["liveEdge"] = live_edge(g)
    # "live" as the dashboard judges the player block: spectating leaves it zero.
    player_live = bool(player["isOnTrack"]) or (player["speedMs"] or 0) > 0.5 or (player["rpm"] or 0) > 500
    derived.update(deriver.trends(session["timeS"], weather, player, player_live))
    derived["weatherOutlook"] = deriver.weather_outlook(session["timeS"], weather, session, session_msg)
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
    by_idx = {c["idx"]: c for c in cars}
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
        race = race_length(session_msg, session_num, ordered, _num(g("SessionTimeRemain")),
                           deriver, decode_bits(g("SessionFlags"), FLAG_BITS))
        fi = deriver._fuel_info or {}
        deriver.stop_forecast(cars, race if is_race else None, own_idx if own is not None else None,
                              fi.get("fpl"), deriver.tank_l, fi.get("fuelL"))
        caution = deriver.caution(cars, decode_bits(g("SessionFlags"), FLAG_BITS), g("PitsOpen"),
                                  track_length_m(session_msg), now, state=SESSION_STATE.get(g("SessionState")))
        deriver.learn_lap_profile(cars)
        if live_edge(g)["atLive"]:
            deriver.lap_splits(cars, now)
        meet = deriver.meetings(cars, player_idx, now)
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
            "battles": deriver.battles(cars, ahead_of, is_race),
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
                                 state=SESSION_STATE.get(g("SessionState"), "invalid"))
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
    lap_b = b.get("classEstLapS") or ((b.get("bestLapS") or 0) > 0 and b.get("bestLapS")) or None
    if da is None or db is None or not lap_b or lap_b <= 0:
        return None, None
    d = da - db
    if d < -0.02 or d > 200:            # crossing the line out of step, or nonsense
        return None, None
    d = max(d, 0.0)
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


SOF_K = 1600 / math.log(2)


def _sof(ratings):
    """iRacing's strength-of-field formula over the drivers' iRatings:
    (1600 / ln 2) * ln(n / sum(exp(-iR * ln 2 / 1600))). iRacing does not publish
    the number in the telemetry, so it is computed here from DriverInfo."""
    rs = [r for r in ratings if isinstance(r, (int, float)) and r > 0]
    if not rs:
        return None
    return int(round(SOF_K * math.log(len(rs) / sum(math.exp(-r / SOF_K) for r in rs))))


def strength_of_field(cars: list) -> dict | None:
    """{"overall", "cars", "classes": [{"classId","className","sof","cars"}]} from the
    entry list (spectators and the pace car are not in it). Drivers without an
    iRating (AI, rookies at 0) are left out."""
    rated = [c for c in cars if isinstance(c.get("iRating"), (int, float)) and c["iRating"] > 0]
    if not rated:
        return None
    classes = {}
    for c in rated:
        classes.setdefault(c.get("classId"), []).append(c)
    rows = [{"classId": k, "className": v[0].get("className"), "sof": _sof([c["iRating"] for c in v]), "cars": len(v)}
            for k, v in classes.items()]
    rows.sort(key=lambda r: -(r["sof"] or 0))
    return {"overall": _sof([c["iRating"] for c in rated]), "cars": len(rated), "classes": rows}


# --------------------------------------------------------------------------- #
# Watching a replay: hold the live picture
# --------------------------------------------------------------------------- #

def results_positions(session_info: dict | None, session_num) -> list:
    """SessionInfo/Sessions[session_num]/ResultsPositions: iRacing's scored results,
    which the server keeps updating while a replay is being watched (the CarIdx*
    telemetry then comes from the replay frame instead)."""
    for s in (session_info or {}).get("Sessions") or []:
        if s.get("SessionNum") == session_num:
            return [r for r in (s.get("ResultsPositions") or []) if isinstance(r, dict)]
    return []


def hold_cars(last_live: dict, results: list, edge: dict, t) -> dict:
    """The cars message while a replay is being watched behind live: the last live
    message, with what iRacing's results say now laid over it (position, class
    position, laps, last and best lap). Everything measured from track positions
    (gaps, trends, the map) stays as it was at the moment the replay began, and
    derived.held says so. The derived history is not fed from the replay at all."""
    import copy
    msg = copy.deepcopy(last_live)
    msg["t"] = t
    by = {c["idx"]: c for c in msg.get("cars") or []}
    updated = 0
    for r in results:
        c = by.get(r.get("CarIdx"))
        if c is None:
            continue
        pos, cpos = r.get("Position"), r.get("ClassPosition")
        d = c.setdefault("derived", {})
        if isinstance(pos, int) and pos > 0:
            c["pos"] = d["livePos"] = pos
            updated += 1
        if isinstance(cpos, int) and cpos >= 0:
            c["classPos"] = d["liveClassPos"] = cpos + 1         # 0-based in the results
        if isinstance(r.get("LapsComplete"), int) and r["LapsComplete"] >= 0:
            c["lapCompleted"] = r["LapsComplete"]
        if isinstance(r.get("LastTime"), (int, float)) and r["LastTime"] > 0:
            c["lastLapS"] = r["LastTime"]
        if isinstance(r.get("FastestTime"), (int, float)) and r["FastestTime"] > 0:
            c["bestLapS"] = r["FastestTime"]
    held = (last_live.get("derived") or {}).get("held") or {}
    msg.setdefault("derived", {})["held"] = {
        "sinceT": held.get("sinceT", last_live.get("t")),     # the live moment the picture is from
        "behindLiveS": edge.get("behindLiveS"),
        "fromResults": updated > 0,
    }
    return msg


def race_laps_left(race: dict | None):
    """Laps the leader still has to run: the lap count, or the clock at the leader's pace."""
    if not race:
        return None
    if race.get("endsBy") == "laps" and race.get("lapsToGo") is not None:
        return race["lapsToGo"]
    return race.get("lapsByTime")


def _is_race(session_msg: dict | None, session_num) -> bool:
    for s in (session_msg or {}).get("sessions") or []:
        if s.get("SessionNum") == session_num:
            return "race" in str(s.get("SessionType") or "").lower()
    return False
