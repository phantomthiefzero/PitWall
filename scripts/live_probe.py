"""Is there ANY live tyre data in this car? Scans every channel the SDK has.

  python scripts/live_probe.py             30 s scan of the current car
  python scripts/live_probe.py --seconds 60

Checks all telemetry variables AND every leaf field of every session-YAML
section, reports which changed, and calls out any tyre/temp/pressure channel
among them. Written because in-car dashes show live tyre temps and pressures
that the SDK does not appear to publish -- this makes the claim cheap to
re-test in any car rather than argued from two data points.

Findings so far (Toyota GR86, Ferrari 296 GT3): no live tyre channel in either.
If this ever prints a live tyre variable, that is a real discovery -- the
dashboard could then show something it currently cannot.
"""
from __future__ import annotations

import argparse
import sys
import time

SECTIONS = ["WeekendInfo", "SessionInfo", "CameraInfo", "RadioInfo",
            "DriverInfo", "SplitTimeInfo", "CarSetup", "QualifyResultsInfo"]
TYRE_WORDS = ("tire", "tyre", "temp", "press", "wear", "tread")


def snapshot(ir, names):
    ir.freeze_var_buffer_latest()
    out = {}
    for n in names:
        try:
            v = ir[n]
            out["var:" + n] = repr(tuple(v) if isinstance(v, list) else v)
        except Exception:
            out["var:" + n] = None

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, path + "/" + str(k))
        elif isinstance(o, list):
            for i, v in enumerate(o):
                walk(v, path + "[" + str(i) + "]")
        else:
            out["yaml:" + path] = repr(o)

    for sec in SECTIONS:
        try:
            d = ir[sec]
        except Exception:
            d = None
        if d is not None:
            walk(d, sec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()

    import irsdk
    ir = irsdk.IRSDK()
    if not ir.startup() or not ir.is_connected:
        print("iRacing is not running, or you are not in a session.", file=sys.stderr)
        return 2

    names = sorted(ir._var_headers_dict.keys())
    wk = ir["WeekendInfo"] or {}
    di = ir["DriverInfo"] or {}
    me = next((d for d in (di.get("Drivers") or []) if d.get("CarIdx") == ir["PlayerCarIdx"]), {})
    print(f"car   : {me.get('CarScreenName')}  ({me.get('CarPath')})")
    print(f"track : {wk.get('TrackDisplayName')} / {wk.get('TrackConfigName')}")

    first = snapshot(ir, names)
    print(f"channels: {sum(1 for k in first if k.startswith('var:'))} telemetry variables, "
          f"{sum(1 for k in first if k.startswith('yaml:'))} YAML fields")
    print(f"scanning for {a.seconds:.0f} s -- drive the car, do not sit still ...")

    seen = {k: {v} for k, v in first.items()}
    moving = False
    t0 = time.time()
    while time.time() - t0 < a.seconds:
        time.sleep(a.interval)
        s = snapshot(ir, names)
        try:
            if (ir["Speed"] or 0) > 5:
                moving = True
        except Exception:
            pass
        for k, v in s.items():
            seen.setdefault(k, set()).add(v)

    changed = sorted(k for k, vs in seen.items() if len(vs) > 1)
    tyre = [k for k in changed if any(w in k.lower() for w in TYRE_WORDS)
            and not any(x in k for x in ("AirTemp", "AirPress", "OilTemp", "OilPress",
                                         "WaterTemp", "ManifoldPress", "TrackTemp", "TrackSurfaceTemp",
                                         "TrackAirTemp", "WeatherTemp"))]
    print()
    if not moving:
        print("WARNING: the car never exceeded 5 m/s. A stationary car proves nothing -- re-run while driving.")
    print(f"channels that changed: {len(changed)}")
    print()
    if tyre:
        print("*** LIVE TYRE CHANNELS FOUND -- this contradicts the current finding: ***")
        for k in tyre:
            print("   ", k)
    else:
        print("No live tyre temperature or pressure channel in this car.")
        print("(Engine/ambient temps and pressures are excluded; they are always live.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
