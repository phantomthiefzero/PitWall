"""What does iRacing expose in the role you are currently in?

Driving, spectating and officially spotting for a driver are three different
states, and they expose different things. This prints which car the SDK thinks
is "yours", and then which groups of player-car channels actually carry data --
so a missing readout can be pinned on the sim withholding it rather than guessed
at from an empty panel.

  python scripts/role_probe.py            one snapshot
  python scripts/role_probe.py --watch 30 sample for 30 s and report what moved
"""
from __future__ import annotations

import argparse
import sys
import time

GROUPS = {
    "identity": ["PlayerCarIdx", "CamCarIdx", "IsOnTrack", "IsOnTrackCar",
                 "IsInGarage", "IsReplayPlaying", "IsSpectator"],
    "fuel": ["FuelLevel", "FuelLevelPct", "FuelUsePerHour", "FuelPress"],
    "tyres": ["LFwearL", "LFwearM", "LFwearR", "LFtempCL", "LFtempCM", "LFtempCR",
              "LFcoldPressure", "RFwearM", "LRwearM", "RRwearM"],
    "engine": ["RPM", "Speed", "Gear", "WaterTemp", "OilTemp", "OilPress", "Voltage"],
    "inputs": ["Throttle", "Brake", "Clutch", "SteeringWheelAngle"],
    "dc controls": ["dcBrakeBias", "dcABS", "dcTractionControl", "dcFuelMixture"],
    "pit service": ["PitSvFuel", "PitSvFlags", "PitRepairLeft", "PitOptRepairLeft",
                    "PlayerCarInPitStall", "PitstopActive"],
    "damage/penalty": ["PlayerCarWeightPenalty", "PlayerCarPowerAdjust", "PlayerCarTowTime",
                       "PlayerCarMyIncidentCount", "PlayerCarTeamIncidentCount"],
}


def val(ir, name):
    try:
        return ir[name]
    except Exception:
        return None


def nonzero(v):
    if v is None:
        return False
    if isinstance(v, (list, tuple)):
        return any(nonzero(x) for x in v)
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return abs(v) > 1e-9
    return bool(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", type=float, default=0.0, help="sample for N seconds and report what changed")
    a = ap.parse_args()

    import irsdk
    ir = irsdk.IRSDK()
    if not ir.startup() or not ir.is_connected:
        print("iRacing is not running, or you are not in a session.", file=sys.stderr)
        return 2
    ir.freeze_var_buffer_latest()

    di = ir["DriverInfo"] or {}
    wk = ir["WeekendInfo"] or {}
    pidx = val(ir, "PlayerCarIdx")
    drivers = {d.get("CarIdx"): d for d in (di.get("Drivers") or [])}
    me = drivers.get(pidx, {})
    cam = drivers.get(val(ir, "CamCarIdx"), {})

    print(f"track   : {wk.get('TrackDisplayName')} ({wk.get('TrackConfigName')})")
    print(f"session : {wk.get('EventType')}  SimMode={wk.get('SimMode')}")
    print()
    print(f"PlayerCarIdx {pidx} -> {me.get('UserName')!r}  team={me.get('TeamName')!r} "
          f"spectator={me.get('IsSpectator')} car={me.get('CarScreenNameShort')!r}")
    print(f"CamCarIdx    {val(ir, 'CamCarIdx')} -> {cam.get('UserName')!r}")
    print(f"DriverInfo.DriverCarIdx = {di.get('DriverCarIdx')}   DriverUserID = {di.get('DriverUserID')}")
    # The role is not a telemetry variable -- there is no IsSpotter. It shows up
    # in the radio frequency list instead: spectating gets @SPECTATORS and no
    # @TEAM; being attached to a car gets a @TEAM frequency whose CarIdx names
    # that car. That is the most likely way to identify the driver you are
    # officially spotting for while PlayerCarIdx stays a spectator slot.
    ri = ir["RadioInfo"] or {}
    print()
    print("Radio frequencies (this is where the role shows up):")
    team_idx = None
    for radio in ri.get("Radios") or []:
        for fr in radio.get("Frequencies") or []:
            ci = fr.get("CarIdx")
            who = drivers.get(ci, {}).get("UserName") if ci is not None and ci >= 0 else None
            print(f"  {fr.get('FrequencyName'):16} CarIdx={str(ci):>4}"
                  + (f"  -> {who}" if who else ""))
            if fr.get("FrequencyName") == "@TEAM" and ci is not None and ci >= 0:
                team_idx = ci
    print()
    if team_idx is not None and team_idx != pidx:
        print(f"  >>> @TEAM points at car {team_idx} ({drivers.get(team_idx, {}).get('UserName')!r}) "
              f"while PlayerCarIdx is {pidx}. That is the car being spotted for.")
    elif team_idx is not None:
        print(f"  >>> @TEAM matches PlayerCarIdx ({pidx}) -- attached to your own car.")
    else:
        print("  >>> no @TEAM frequency: not attached to any car (plain spectator).")

    print()
    print("Which player-car channels actually carry data:")
    for group, names in GROUPS.items():
        live = [n for n in names if nonzero(val(ir, n))]
        state = "POPULATED" if live else "all zero/empty"
        print(f"  {group:16} {state:15} {', '.join(live[:6]) if live else ''}")

    if a.watch > 0:
        print()
        print(f"watching for {a.watch:.0f} s to see which of those actually MOVE ...")
        first = {n: repr(val(ir, n)) for names in GROUPS.values() for n in names}
        changed = set()
        t0 = time.time()
        while time.time() - t0 < a.watch:
            ir.freeze_var_buffer_latest()
            for n in list(first):
                if repr(val(ir, n)) != first[n]:
                    changed.add(n)
            time.sleep(0.25)
        for group, names in GROUPS.items():
            moved = [n for n in names if n in changed]
            print(f"  {group:16} {'CHANGED' if moved else 'static':10} {', '.join(moved[:6])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
