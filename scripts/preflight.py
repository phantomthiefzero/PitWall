"""Wait until iRacing is in a state worth recording, then describe it.

Exists because a phase-1 recording is only useful if the player is actually in
the car: a replay or a spectator slot leaves IsOnTrack false, which produces a
recording with no "track" segment and no player-car data (fuel, tires, dc*).
That mistake costs a whole session, so check before spending fifteen minutes.

  python scripts/preflight.py            wait up to 5 min for a usable state
  python scripts/preflight.py --now      check once and exit
  python scripts/preflight.py --allow-replay

Exit codes: 0 ready, 1 timed out, 2 iRacing not running.
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, __file__.rsplit("scripts", 1)[0])

SESSION_STATE = {0: "invalid", 1: "get_in_car", 2: "warmup", 3: "parade_laps",
                 4: "racing", 5: "checkered", 6: "cool_down"}


def snapshot(ir):
    ir.freeze_var_buffer_latest()
    g = lambda k: ir[k]  # noqa: E731
    wk = ir["WeekendInfo"] or {}
    di = ir["DriverInfo"] or {}
    idx = g("PlayerCarIdx")
    drivers = {d.get("CarIdx"): d for d in (di.get("Drivers") or [])}
    me = drivers.get(idx, {})
    return {
        "track": f"{wk.get('TrackDisplayName')} ({wk.get('TrackConfigName')})",
        "trackId": wk.get("TrackID"),
        "car": me.get("CarScreenNameShort"),
        "driver": me.get("UserName"),
        "simMode": wk.get("SimMode"),
        "sessionState": SESSION_STATE.get(g("SessionState"), g("SessionState")),
        "onTrack": bool(g("IsOnTrack")),
        "replay": bool(g("IsReplayPlaying")),
        "inGarage": bool(g("IsInGarage")),
        "onPitRoad": bool(g("OnPitRoad")),
        "fuelL": g("FuelLevel"),
        "speedMs": g("Speed"),
    }


def verdict(s, allow_replay):
    """Return (ready, reason). Ready means the recording will contain player data."""
    if s["replay"] and not allow_replay:
        return False, "a replay is playing - player car data (fuel, tires, dc*) will be blank"
    if s["inGarage"]:
        return False, "you are in the garage, not on track"
    if not s["onTrack"] and not allow_replay:
        return False, "IsOnTrack is false - spectating, or not in the car yet"
    if not s["replay"] and s["fuelL"] in (0, 0.0):
        return False, "FuelLevel reads 0 - not really in the car yet"
    return True, "in the car, on track"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=300.0, help="seconds to wait (default 300)")
    ap.add_argument("--now", action="store_true", help="check once, do not wait")
    ap.add_argument("--allow-replay", action="store_true", help="accept a replay/spectator state")
    a = ap.parse_args()

    import irsdk
    ir = irsdk.IRSDK()
    deadline = time.time() + (0 if a.now else a.timeout)
    last = None
    while True:
        if not ir.is_initialized or not ir.is_connected:
            ir.shutdown()
            ir.startup()
        if ir.is_initialized and ir.is_connected:
            s = snapshot(ir)
            ok, why = verdict(s, a.allow_replay)
            line = (f"{s['track']} | {s['car']} | {s['driver']} | {s['sessionState']} | "
                    f"onTrack={s['onTrack']} replay={s['replay']} fuel={s['fuelL']:.1f}L")
            if line != last:
                print(line)
                last = line
            if ok:
                print(f"READY: {why}")
                return 0
            msg = f"waiting: {why}"
        else:
            msg = "waiting: iRacing not running / not in a session"
        if a.now or time.time() >= deadline:
            print(msg)
            return 1 if (ir.is_initialized and ir.is_connected) else 2
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
