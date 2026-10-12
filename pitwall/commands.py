"""Commands INTO iRacing (phase 2). Off by default.

Officially supported via the SDK broadcast channel (irsdk_BroadcastMsg):
    pit service   fuel amount, each tire (with pressure), tire compound,
                  fast repair, windshield tear-off, and clearing any of those
    telemetry     start/stop/restart disk telemetry
    chat / macros iRacing chat macros 1-15
    replay/camera not used here

NOT available via the SDK: in-car adjustments (brake bias, ARBs, TC, ABS,
fuel mix). Those would need keystroke injection mapped to iRacing's control
bindings, which is deliberately not implemented in this module.

Every command is validated against a small allowlist and echoed back to the
sender with the result so the engineer sees exactly what was sent.
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


class CommandGate:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    def execute(self, src, cmd: dict) -> dict[str, Any]:
        """Returns {"ok": bool, "echo": cmd, "error": str|None}."""
        result = {"type": "cmd_result", "ok": False, "echo": cmd, "error": None}
        if not self.enabled:
            result["error"] = "commands are disabled on this bridge (start it with --allow-commands)"
            return result
        kind = cmd.get("cmd")
        if kind != "pit":
            result["error"] = f"unsupported command {kind!r}; only 'pit' is implemented"
            return result
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
