"""Enum decoders, iRacing's incident table and small readers shared by every part."""
from __future__ import annotations

import math


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
