"""Commands INTO iRacing. Off by default, and gated per kind.

Officially supported via the SDK broadcast channel (irsdk_BroadcastMsg):
    pit service   fuel amount, each tire (with pressure), tire compound,
                  fast repair, windshield tear-off, and clearing any of those
    camera        cut to a car by number or by position, in a camera group;
                  or hand the choice to iRacing (most exciting / leader / incident)
    replay        play speed (incl. slow motion and pause), jump to live, step
                  between incidents and laps, seek to a session time (replay
                  one incident)
    telemetry     start/stop/restart disk telemetry       (not used here)
    chat / macros iRacing chat macros 1-15                  (not used here)

NOT available via the SDK: in-car adjustments (brake bias, ARBs, TC, ABS,
fuel mix). Those would need keystroke injection mapped to iRacing's control
bindings, which is deliberately not implemented in this module.

Two independent gates, because they are different people's jobs:
    pit     the bridge must run with --allow-commands (engineer, driving)
    camera  the bridge must run with --allow-camera   (broadcast producer)
and the relay only forwards each kind from the token that owns it.

Every command is validated against a small allowlist and echoed back to the
sender with the result so the operator sees exactly what was sent.

This module is the only place that calls pyirsdk broadcast functions.

VERIFICATION: the camera and replay calls follow pyirsdk's documented API
(cam_switch_num, cam_switch_pos, replay_set_play_speed, replay_search) but have
NOT yet been run against the sim. See docs/VARIABLES_TO_VERIFY.md, "Broadcast".
"""
from __future__ import annotations

from typing import Any

PIT_ACTIONS = {
    # name: (PitCommandMode, takes_value, value_range, unit)
    "clear": ("clear", False, None, None),
    "windshield": ("ws", False, None, None),
    "fuel": ("fuel", True, (0, 500), "liters (0 = keep current amount)"),
    "lf": ("lf", True, (0, 400), "kPa (0 = keep current pressure)"),
    "rf": ("rf", True, (0, 400), "kPa"),
    "lr": ("lr", True, (0, 400), "kPa"),
    "rr": ("rr", True, (0, 400), "kPa"),
    "clear_tires": ("clear_tires", False, None, None),
    "fast_repair": ("fr", False, None, None),
    "clear_windshield": ("clear_ws", False, None, None),
    "clear_fast_repair": ("clear_fr", False, None, None),
    "clear_fuel": ("clear_fuel", False, None, None),
    "tire_compound": ("tc", True, (0, 16), "compound index"),
}

# iRacing's own shot choice: irsdk_csMode, sent in place of a car number.
CAMERA_FOCUS = {"exciting": -1, "leader": -2, "incident": -3}

# irsdk_RpySrchMode names, as pyirsdk spells them.
REPLAY_SEARCH = ("to_start", "to_end", "prev_session", "next_session", "prev_lap", "next_lap",
                 "prev_frame", "next_frame", "prev_incident", "next_incident")

# Which command kinds exist, and which gate each one needs.
KIND_GATE = {"pit": "pit", "camera": "camera", "replay": "camera"}


def _int(v, lo, hi, what):
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be an integer")
    if not (lo <= v <= hi):
        raise ValueError(f"{what} {v} outside ({lo}, {hi})")
    return v


def _car_number(v) -> str:
    """iRacing car numbers are strings, and leading zeros are significant ("07" is
    not "7"), so only digits are accepted and they are passed through as given."""
    s = str(v if v is not None else "").strip()
    if not s.isdigit() or len(s) > 3:
        raise ValueError("car number must be 1-3 digits, as shown on the car")
    return s


def plan_camera(cmd: dict) -> tuple[str, tuple]:
    """Validate a camera command and say which broadcast call it becomes.

    Returns (method, args) where method is a pyirsdk IRSDK method name. Kept
    separate from the call so it can be tested on any OS.
        {"cmd":"camera","action":"car","car":"34","group":11,"camera":0}
        {"cmd":"camera","action":"position","position":1,"group":11}
        {"cmd":"camera","action":"focus","focus":"exciting","group":11}
    camera 0 lets iRacing pick the best camera within the group.
    """
    action = cmd.get("action")
    group = _int(cmd.get("group", 0), 0, 255, "group")
    camera = _int(cmd.get("camera", 0), 0, 255, "camera")
    if action == "car":
        return "cam_switch_num", (_car_number(cmd.get("car")), group, camera)
    if action == "position":
        return "cam_switch_pos", (_int(cmd.get("position"), 1, 128, "position"), group, camera)
    if action == "focus":
        focus = cmd.get("focus")
        if focus not in CAMERA_FOCUS:
            raise ValueError(f"focus must be one of {sorted(CAMERA_FOCUS)}")
        return "cam_switch_num", (CAMERA_FOCUS[focus], group, camera)
    raise ValueError(f"unknown camera action {action!r}")


# Seconds to wait after a seek before setting the speed: a speed change sent
# while iRacing is still seeking can be lost (RobertsmaniaReplay waits for the
# frame number to settle for the same reason).
SEEK_SETTLE_S = 0.4


def plan_jump(cmd: dict) -> list[tuple[str, tuple]]:
    """Replay one moment, e.g. an incident:
        {"cmd":"replay","action":"jump","session":2,"timeS":812.4,"preroll":5,
         "car":"34","group":11}
    Cuts to the car first (when given), seeks to timeS - preroll in that session
    (irsdk_BroadcastReplaySearchSessionTime, milliseconds), waits for the seek to
    settle, then plays at normal speed. Returns the calls in order; ("sleep",
    (s,)) is a pause, not a call into iRacing.
    """
    session = _int(cmd.get("session"), 0, 64, "session")
    try:
        t = float(cmd.get("timeS"))
    except (TypeError, ValueError):
        raise ValueError("timeS must be a number of seconds")
    if not (0 <= t < 7 * 86400):
        raise ValueError("timeS out of range")
    try:
        pre = float(cmd.get("preroll", 5))
    except (TypeError, ValueError):
        raise ValueError("preroll must be a number of seconds")
    if not (0 <= pre <= 60):
        raise ValueError("preroll must be 0-60 s")
    steps = []
    if cmd.get("car") not in (None, ""):
        group = _int(cmd.get("group", 0), 0, 255, "group")
        steps.append(("cam_switch_num", (_car_number(cmd.get("car")), group, 0)))
    steps.append(("replay_search_session_time", (session, int(max(0.0, t - pre) * 1000))))
    steps.append(("sleep", (SEEK_SETTLE_S,)))
    steps.append(("replay_set_play_speed", (1, False)))
    return steps


def plan_replay(cmd: dict) -> tuple[str, tuple]:
    """{"cmd":"replay","action":"speed","speed":1,"slow":false}   speed 0 pauses
       {"cmd":"replay","action":"search","mode":"to_end"}          to_end = back to live"""
    action = cmd.get("action")
    if action == "speed":
        speed = _int(cmd.get("speed", 1), -16, 16, "speed")
        return "replay_set_play_speed", (speed, bool(cmd.get("slow", False)))
    if action == "search":
        mode = cmd.get("mode")
        if mode not in REPLAY_SEARCH:
            raise ValueError(f"search mode must be one of {list(REPLAY_SEARCH)}")
        return "replay_search", (mode,)
    raise ValueError(f"unknown replay action {action!r}")


class CommandGate:
    def __init__(self, enabled: bool = False, camera: bool = False):
        self.enabled = enabled          # pit service (kept as `enabled` for older callers)
        self.camera = camera            # camera + replay control

    def allowed(self, gate: str) -> bool:
        return self.enabled if gate == "pit" else self.camera if gate == "camera" else False

    def execute(self, src, cmd: dict) -> dict[str, Any]:
        """Returns {"ok": bool, "echo": cmd, "error": str|None}."""
        result = {"type": "cmd_result", "ok": False, "echo": cmd, "error": None}
        kind = cmd.get("cmd")
        if kind not in KIND_GATE:
            result["error"] = f"unsupported command {kind!r}"
            return result
        if not self.allowed(KIND_GATE[kind]):
            flag = "--allow-commands" if KIND_GATE[kind] == "pit" else "--allow-camera"
            result["error"] = f"{kind} commands are disabled on this bridge (start it with {flag})"
            return result
        if kind == "pit":
            return self._pit(src, cmd, result)
        try:
            if kind == "camera":
                steps = [plan_camera(cmd)]
            elif cmd.get("action") == "jump":
                steps = plan_jump(cmd)
            else:
                steps = [plan_replay(cmd)]
        except ValueError as e:
            result["error"] = str(e)
            return result
        # The fake field has a stand-in camera so the producer panel can be
        # exercised without the sim. It says so in the result; it is never
        # reported as having reached iRacing.
        sim = getattr(src, "simulate_broadcast", None)
        if sim is not None:
            try:
                for method, args in steps:
                    if method != "sleep":
                        sim(method, args)
                result.update(ok=True, simulated=True)
            except Exception as e:  # noqa: BLE001
                result["error"] = f"{type(e).__name__}: {e}"
            return result
        try:
            import time
            import irsdk
            ir = src.irsdk()  # raises for non-live sources
            for method, args in steps:
                if method == "sleep":
                    time.sleep(args[0])
                    continue
                if method == "replay_search":
                    args = (getattr(irsdk.RpySrchMode, args[0]),)
                getattr(ir, method)(*args)
            result["ok"] = True
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        return result

    def _pit(self, src, cmd, result):
        action = cmd.get("action")
        if action not in PIT_ACTIONS:
            result["error"] = f"unknown pit action {action!r}"
            return result
        mode_name, takes_value, rng, _unit = PIT_ACTIONS[action]
        value = cmd.get("value", 0)
        if takes_value:
            try:
                value = int(value)
            except (TypeError, ValueError):
                result["error"] = "value must be an integer"
                return result
            if not (rng[0] <= value <= rng[1]):
                result["error"] = f"value {value} outside {rng}"
                return result
        else:
            value = 0
        try:
            import irsdk
            ir = src.irsdk()  # raises for non-live sources
            mode = getattr(irsdk.PitCommandMode, mode_name)
            ir.pit_command(mode, value)
            result["ok"] = True
        except Exception as e:  # noqa: BLE001
            result["error"] = f"{type(e).__name__}: {e}"
        return result
