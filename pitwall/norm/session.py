"""The session message (from the YAML session string) and the starting grid."""
from __future__ import annotations

from ..sources import TelemetrySource
from .common import SCHEMA_VERSION


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
                    for k in ("IncidentLimit", "FastRepairsLimit", "StandingStart", "NumStarters", "StartingGrid",
                              # the weather settings and the sim's calendar and clock (the sun's path)
                              "WeatherType", "Skies", "WindDirection", "WindSpeed", "WeatherTemp",
                              "RelativeHumidity", "FogLevel", "TimeOfDay", "Date",
                              "EarthRotationSpeedupFactor")},
        "drivers": drivers,
        "sessions": sessions,
        "sectors": sp.get("Sectors", []) or [],
        "cameras": _camera_groups(src.session_info("CameraInfo")),
        "grid": _grid(src.session_info("QualifyResultsInfo"), si.get("Sessions") or [],
                      {d.get("CarIdx"): d.get("CarClassID") for d in drivers},
                      grid_per_row((wk.get("WeekendOptions") or {}).get("StartingGrid"))),
    }


def grid_per_row(starting_grid) -> int:
    """Cars a row from WeekendOptions/StartingGrid ("2x2 inline pole on left", "1x1 single file", "3x3 ..."):
    its first number. Two when it says nothing readable, as most grids are."""
    import re
    m = re.match(r"\s*(\d)\s*x", str(starting_grid or ""))
    n = int(m.group(1)) if m else 2
    return n if 1 <= n <= 4 else 2


def _qual_rows(rows, class_of=None, per_row=2) -> list[dict]:
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
    out.sort(key=lambda x: x["pos"])
    _grid_derived(out, class_of or {}, per_row)
    return out


def _grid_derived(rows, class_of, per_row=2) -> None:
    """For the grid walk (each row's "derived"): the row of the grid (two cars a row), the gap to pole and to the
    class's pole on the qualifying lap, and whether the car is on pole overall or in its class. The class pole is
    the class's first car (ClassPosition 1), not its quickest time: a grid set by a heat has positions without
    times, and those stay without gaps."""
    pole = next((r for r in rows if r["pos"] == 1), None)
    class_pole = {}
    for r in rows:
        k = class_of.get(r["idx"])
        if r.get("classPos") == 1 and k not in class_pole:
            class_pole[k] = r
    for r in rows:
        k = class_of.get(r["idx"])
        cp = class_pole.get(k)
        r["derived"] = {
            "row": (r["pos"] + per_row - 1) // per_row if r["pos"] > 0 else None,
            "pole": r["pos"] == 1,
            "classPole": r.get("classPos") == 1,
            "poleGapS": round(r["timeS"] - pole["timeS"], 3) if r["timeS"] and pole and pole["timeS"] and r is not pole else None,
            "classPoleGapS": round(r["timeS"] - cp["timeS"], 3) if r["timeS"] and cp and cp["timeS"] and r is not cp else None,
        }


def _grid(qri, sessions, class_of=None, per_row=2) -> dict:
    """The starting order for the race. A heat or a race with its own
    QualifyPositions uses those; otherwise the event's qualifying results."""
    for s in sessions:
        if "race" in str(s.get("SessionType") or "").lower() and s.get("QualifyPositions"):
            return {"source": "race_qualify_positions", "sessionNum": s.get("SessionNum"),
                    "rows": _qual_rows(s.get("QualifyPositions"), class_of, per_row), "perRow": per_row}
    rows = _qual_rows((qri or {}).get("Results"), class_of, per_row)
    return {"source": "qualify_results", "sessionNum": None, "rows": rows, "perRow": per_row} if rows else {"source": None, "rows": [], "perRow": per_row}


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
