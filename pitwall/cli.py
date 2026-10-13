"""pitwall command line.

  python -m pitwall check   [--source live|fake|dump --path X]   print one normalized frame
  python -m pitwall record  --out race.jsonl.gz [--hz 10] [--minutes 30]
  python -m pitwall analyze race.jsonl.gz [--md docs/VARIABLES.md] [--json report.json]
  python -m pitwall map     race.jsonl.gz  [--out map.json]       build a track outline from a recording
  python -m pitwall stream  --relay ws://host:8765 --token T [--source fake] [--allow-commands] [--allow-camera]

Replaying a capture through the whole stack (no iRacing needed):
  python -m pitwall stream --source replay --path recordings/race.jsonl.gz --relay ws://127.0.0.1:8765 --token B
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

from . import __version__
from .sources import make_source


def _src(args):
    kw = {}
    if args.source == "fake":
        kw = {"realtime": not getattr(args, "fast", False),
              "spectator": getattr(args, "spectator", False),
              "parade_s": getattr(args, "parade", 0.0),
              "grid_s": getattr(args, "grid", 0.0)}
    elif args.source == "replay":
        kw = {"realtime": not getattr(args, "fast", False),
              "speed": getattr(args, "speed", 1.0),
              "loop": getattr(args, "loop", False)}
    return make_source(args.source, getattr(args, "path", None), **kw)


def cmd_check(args):
    from .normalize import Deriver, build_cars, build_session, build_state
    src = _src(args)
    if not src.connect():
        print("could not connect to source", file=sys.stderr)
        return 2
    src.tick()
    sess = build_session(src)
    st = build_state(src, Deriver())
    cars = build_cars(src, sess)
    print(json.dumps({"session": sess, "state": st, "cars": cars}, indent=1)[: args.limit])
    print(f"\n... {len(src.var_headers())} variables available from {src.name}")
    return 0


def cmd_record(args):
    from .recorder import record
    src = _src(args)
    dur = args.minutes * 60 if args.minutes else None
    clock = "sim" if (args.source == "fake" and args.fast) else "wall"
    record(src, args.out, hz=args.hz, duration_s=dur, max_ticks=args.ticks, clock=clock)
    return 0


def cmd_analyze(args):
    from .analyze import analyze, to_markdown
    rep = analyze(args.recording)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rep, f, indent=1)
    md = to_markdown(rep)
    if args.md:
        with open(args.md, "w", encoding="utf-8") as f:
            f.write(md)
        print(f"wrote {args.md}")
    else:
        print(md)
    labels = {}
    for v in rep["vars"].values():
        labels[v["label"]] = labels.get(v["label"], 0) + 1
    print(f"{rep['ticks']} ticks / {rep['seconds']} s; labels: {labels}")
    return 0


def cmd_map(args):
    from . import trackmap
    from .recorder import iter_recording
    coll = trackmap.LapCollector()
    track_id, config = "unknown", "default"
    lap = None
    for rec in iter_recording(args.recording):
        if rec["type"] == "session":
            wk = (rec.get("info") or {}).get("WeekendInfo") or {}
            track_id = wk.get("TrackID", track_id)
            config = wk.get("TrackConfigName", config) or "default"
        if rec["type"] != "tick":
            continue
        v = rec["vars"]
        lap = coll.push(v.get("SessionTime"), v.get("LapDistPct"), v.get("VelocityX"), v.get("VelocityY"),
                        v.get("YawNorth"), on_track=bool(v.get("IsOnTrack")), on_pit=bool(v.get("OnPitRoad")))
        if lap:
            break
    if not lap:
        print("no complete clean lap in this recording (need a full lap on track without a pit visit)")
        return 1
    tm = trackmap.build_from_lap(lap, track_id, config)
    out = args.out or trackmap.save(tm)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(tm.to_message(), f)
    print(f"map for track {track_id} / {config}: {len(lap)} samples, lap length {tm.lap_length_m} m, "
          f"closure error {tm.closure_error_m} m -> {out}")
    return 0


def cmd_stream(args):
    from .bridge import Bridge
    src = _src(args)
    b = Bridge(src, args.relay, args.token, state_hz=args.state_hz, cars_hz=args.cars_hz,
               allow_commands=args.allow_commands, allow_camera=args.allow_camera)
    try:
        asyncio.run(b.run())
    except KeyboardInterrupt:
        print("bye")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pitwall", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"pitwall {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_source(sp):
        sp.add_argument("--source", choices=["live", "fake", "dump", "replay"], default="live")
        sp.add_argument("--path", help="dump file for --source dump, or a .jsonl.gz for --source replay")
        sp.add_argument("--fast", action="store_true",
                        help="fake/replay source: run as fast as possible instead of in real time")
        sp.add_argument("--speed", type=float, default=1.0, help="replay source: playback rate (2 = double)")
        sp.add_argument("--loop", action="store_true", help="replay source: restart at the end")
        sp.add_argument("--parade", type=float, default=0.0,
                        help="fake source: seconds of parade laps before the green (to see the starting grid)")
        sp.add_argument("--grid", type=float, default=0.0,
                        help="fake source: seconds parked on the grid before the parade laps (needs --parade)")
        sp.add_argument("--spectator", action="store_true",
                        help="fake source: behave like a spectator slot (PlayerCarIdx 63, no private car data)")

    s = sub.add_parser("check", help="print one normalized frame")
    add_source(s)
    s.add_argument("--limit", type=int, default=6000)
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("record", help="record every raw variable to a .jsonl.gz")
    add_source(s)
    s.add_argument("--out", required=True)
    s.add_argument("--hz", type=float, default=10.0)
    s.add_argument("--minutes", type=float)
    s.add_argument("--ticks", type=int)
    s.set_defaults(fn=cmd_record)

    s = sub.add_parser("analyze", help="classify variable behavior from a recording")
    s.add_argument("recording")
    s.add_argument("--md")
    s.add_argument("--json")
    s.set_defaults(fn=cmd_analyze)

    s = sub.add_parser("map", help="build a track outline from a recording")
    s.add_argument("recording")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_map)

    s = sub.add_parser("stream", help="stream to a relay")
    add_source(s)
    s.add_argument("--relay", required=True, help="ws://host:port of the relay")
    s.add_argument("--token", required=True, help="bridge token configured on the relay")
    s.add_argument("--state-hz", type=float, default=15.0)
    s.add_argument("--cars-hz", type=float, default=8.0)
    s.add_argument("--allow-commands", action="store_true", help="accept pit commands from engineers (phase 2)")
    s.add_argument("--allow-camera", action="store_true",
                   help="accept camera and replay commands from a broadcast producer")
    s.set_defaults(fn=cmd_stream)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
