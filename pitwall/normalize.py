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
                "CurDriverIncidentCount", "TeamIncidentCount")


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
    }


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
                              "onRoad": on_pit_road, "inBox": in_box}
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
                                         "enter_t": None, "times": [None] * n, "best": [None] * n})
        if len(st["times"]) != n:
            st["times"], st["best"] = [None] * n, [None] * n

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
            st["cur"], st["enter_t"] = cur, None
            st["last_t"] = t
            return out

        if prev_cur is None:
            st["cur"], st["enter_t"] = cur, t
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
                    if st["best"][prev_cur] is None or elapsed < st["best"][prev_cur]:
                        st["best"][prev_cur] = round(elapsed, 3)
                    if self._sect_best[prev_cur] is None or elapsed < self._sect_best[prev_cur]:
                        self._sect_best[prev_cur] = round(elapsed, 3)
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
                          "jump": lc - st["lc"] != 1, "t": round(t, 2)}
            st["pendT"], st["stale"], st["lc"] = t, st["final"], lc
            st["pit"] = bool(c.get("onPitRoad"))
        pend = st["pend"]
        if pend is not None:
            if last is not None and last > 0 and last != st["stale"]:
                if not pend["jump"]:
                    pend["lapS"] = round(last, 3)
                st["final"] = last
                done = self._finish_lap(idx, st)
            elif t - st["pendT"] >= self.LAP_TIME_WAIT_S:
                done = self._finish_lap(idx, st)   # no new time: leave lapS None
        return done

    def _finish_lap(self, idx, st) -> dict:
        e = st["pend"]
        st["pend"] = None
        e.pop("jump", None)
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
                self.event(t, "pit_battle", idx=j, preS=round(rec["pre"], 2), nowS=rec["settled"],
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
                self.event(t, key, idx=i, gapS=round(abs(tp), 2), rateSPerLap=round(abs(gain), 2))
            if key or (gain is not None and abs(gain) < 0.1) or tp is None:
                ev["closing"][i] = key
        # your class position, once it has held for 3 s
        cp = me.get("classPos") or None
        self._stable(ev, "pos", cp, t, 3.0, lambda old, new: self.event(t, "position", fromPos=old, toPos=new))
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
                self.event(t, "incident", delta=inc - ev["inc"], total=inc, limit=self.incident_limit)
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
    # Two independent kinds, never mixed up:
    #   counted   a car's incident total in the session info went up. That is
    #             iRacing's own judgement and the only source of points. It
    #             arrives with the session-info update, some time after the event.
    #   observed  something seen in the telemetry of every car: leaving the track
    #             (CarIdxTrackSurface) or slowing far below the field's speed at
    #             that point of the lap (a spin, a crash, a stop). Not points.
    # A counted incident is anchored to the latest observed event of the same car
    # shortly before it, which is what "replay it" jumps to.
    INC_ANCHOR_WINDOW_S = 30.0      # how far back an observed event may explain a counted one
    INC_FALLBACK_S = 10.0           # no observed event: start this far before the count changed
    OFF_HOLD_S = 5.0                # one off-track per car per 5 s (kerb-hopping is one event)
    SLOW_HOLD_S = 15.0
    SLOW_RATIO = 0.35               # under 35% of the field's speed at this point of the lap
    SLOW_MIN_S = 1.0                # for at least this long
    SLOW_BINS = 200                 # 0.5% of a lap per bin
    SLOW_BIN_SAMPLES = 40
    SLOW_BIN_MIN = 15

    def incidents(self, cars: list, edge: dict, session_num, surface: str = "paved"):
        st = self.__dict__.setdefault("_inc", {"count": {}, "surf": {}, "off_at": {}, "slow_at": {},
                                               "prev": {}, "slow_since": {}, "bins": {}, "obs": {}})
        t_live, t_play, at_live = edge["tLive"], edge["tPlay"], edge["atLive"]
        # counted -- session info is live state, so this runs even mid-replay
        for c in cars:
            i, n = c["idx"], c.get("incidents")
            if not isinstance(n, (int, float)) or n < 0 or t_live is None:
                continue                         # not published for this car / session
            prev = st["count"].get(i)
            st["count"][i] = n
            if prev is None or n <= prev:
                continue                         # first sight is history; a drop is a reset
            pts = int(n - prev)
            anchor = None
            for ob in reversed(st["obs"].get(i, [])):
                if t_live - self.INC_ANCHOR_WINDOW_S <= ob["t"] <= t_live + 1:
                    anchor = ob
                    break
            self.event(t_live, "car_incident", idx=i, points=pts, total=int(n),
                       driverTotal=c.get("driverIncidents"), limit=self.incident_limit,
                       lap=c.get("lap"), sessionNum=session_num,
                       replayT=round(anchor["t"], 2) if anchor else round(t_live - self.INC_FALLBACK_S, 2),
                       anchor=anchor["kind"] if anchor else "reported", surface=surface,
                       **incident_causes(pts, surface))
        # observed -- only at the live edge. Watching a replay re-plays the
        # telemetry, and the same off-track must not be reported twice.
        if not at_live or t_play is None:
            st["prev"].clear()
            st["slow_since"].clear()
            return
        rates = []
        for c in cars:
            i, pct, surf = c["idx"], c.get("pct"), c.get("surface")
            was = st["surf"].get(i)
            st["surf"][i] = surf
            if was == "on_track" and surf == "off_track" and \
                    t_play - st["off_at"].get(i, -1e9) >= self.OFF_HOLD_S:
                st["off_at"][i] = t_play
                self._observed(st, i, t_play, "off_track", c, session_num)
            # speed in laps per second from successive lap distance
            lapc = c.get("lapCompleted")
            prev = st["prev"].get(i)
            racing = surf in ("on_track", "off_track") and not c.get("onPitRoad") and pct is not None \
                and pct >= 0 and lapc is not None
            st["prev"][i] = (t_play, lapc + pct) if racing else None
            if not racing or prev is None:
                st["slow_since"].pop(i, None)
                continue
            dt = t_play - prev[0]
            if dt <= 0 or dt > 1.0:
                continue
            rate = (lapc + pct - prev[1]) / dt
            if rate < -0.01 or rate > 0.2:       # a reset or a scored lap jump, not motion
                st["slow_since"].pop(i, None)
                continue
            rates.append((i, int(pct * self.SLOW_BINS) % self.SLOW_BINS, max(rate, 0.0), c))
        all_meds = [self._median(b) for b in st["bins"].values() if len(b) >= self.SLOW_BIN_MIN]
        field = self._median(all_meds) if all_meds else None
        for i, b, rate, c in rates:
            hist = st["bins"].setdefault(b, [])
            med = self._median(hist) if len(hist) >= self.SLOW_BIN_MIN else None
            slow = (med is not None and field is not None and med >= 0.3 * field and rate < self.SLOW_RATIO * med)
            if slow:
                since = st["slow_since"].setdefault(i, t_play)
                if t_play - since >= self.SLOW_MIN_S and t_play - st["slow_at"].get(i, -1e9) >= self.SLOW_HOLD_S:
                    st["slow_at"][i] = t_play
                    self._observed(st, i, since, "slowed", c, session_num)
            else:
                st["slow_since"].pop(i, None)
                hist.append(rate)              # only normal running defines "normal"
                if len(hist) > self.SLOW_BIN_SAMPLES:
                    del hist[0]

    def _observed(self, st, i, t, kind, c, session_num):
        ob = {"t": t, "kind": kind}
        lst = st["obs"].setdefault(i, [])
        lst.append(ob)
        del lst[:-20]
        self.event(t, kind, idx=i, lap=c.get("lap"), pct=round(c.get("pct") or 0.0, 4),
                   sessionNum=session_num, replayT=round(t, 2))

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
             time_remain_s=None, lap_time_s=None) -> dict:
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
            return out
        if lap_completed > self._last_lap_completed:
            used = (self._fuel_at_lap_start or 0) - fuel_level
            # ignore laps where we refueled or the counter jumped
            if 0 < used < 25 and lap_completed - self._last_lap_completed == 1:
                self._fuel_per_lap_samples.append(used)
                self._fuel_per_lap_samples = self._fuel_per_lap_samples[-5:]
            self._last_lap_completed = lap_completed
            self._fuel_at_lap_start = fuel_level
        if self._fuel_per_lap_samples:
            fpl = sum(self._fuel_per_lap_samples) / len(self._fuel_per_lap_samples)
            out["fuelPerLap"] = round(fpl, 3)
            out["lapsOfFuel"] = round(fuel_level / fpl, 2) if fpl > 0 else None
            # Fuel to finish: the number an engineer actually says. Needs BOTH a
            # measured burn rate and a known race length; if either is missing we
            # say nothing rather than guess at one of them.
            if fpl > 0 and laps_left is not None:
                need = fpl * laps_left
                out["fuelToFinishL"] = round(need, 2)
                out["fuelMarginL"] = round(fuel_level - need, 2)
                # "You are short" is a problem; "save 0.12 a lap" is an
                # instruction. Same arithmetic, and it is the next thing said.
                if fuel_level < need and laps_left > 0:
                    out["fuelSavePerLapL"] = round((need - fuel_level) / laps_left, 3)
        out["fuelPerLapSamples"] = len(self._fuel_per_lap_samples)
        return out


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
                           lap_time_s=_num(g("LapLastLapTime")))
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
            "flags": decode_bits(_arr(cflags, i), FLAG_BITS),
            "fastRepairsUsed": _arr(frep, i),
            "p2pActive": _arr(p2ps, i), "p2pCount": _arr(p2pc, i),
            "gear": _arr(gear, i), "rpm": _arr(rpm, i),
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
    player_idx = _num(g("PlayerCarIdx"))
    by_idx = {c["idx"]: c for c in cars}
    me = by_idx.get(player_idx)
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

    out = {"type": "cars", "schema": SCHEMA_VERSION, "t": _num(g("SessionTime")), "cars": cars}
    if deriver is not None:
        for c in cars:
            deriver.record_lap(c, now)
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
        out["derived"] = {
            "sessionBestSectorS": deriver.session_best_sectors(),
            "isRace": is_race,
            "pitLoss": deriver.pit_loss(),
            "rejoin": deriver.rejoin(cars, player_idx) if is_race and me is not None else None,
            "undercut": deriver.undercut(cars, player_idx, now) if is_race and me is not None else [],
            "traffic": deriver.traffic(cars, player_idx),
            "battles": deriver.battles(cars, ahead_of, is_race),
        }
        edge = live_edge(g)
        out["derived"]["liveEdge"] = edge
        # Race events describe the race as it happens. While a replay is being
        # watched the field data is from the past, and pit stops or lead changes
        # read from it would be old news reported as new.
        if edge["atLive"]:
            deriver.field_events(cars, now, player_idx, deriver._stops_tick)
        deriver.incidents(cars, edge, session_num,
                          surface_kind(((session_msg or {}).get("track") or {}).get("TrackType")))
    return out


def _is_race(session_msg: dict | None, session_num) -> bool:
    for s in (session_msg or {}).get("sessions") or []:
        if s.get("SessionNum") == session_num:
            return "race" in str(s.get("SessionType") or "").lower()
    return False
