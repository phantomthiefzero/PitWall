"""Classify every recorded variable by how it actually behaves.

For each variable, across the whole recording:
    present      fraction of ticks where the value was not None
    changes      how many ticks the value differed from the previous tick
    rate_track   changes/sec while the player was on track and not on pit road
    rate_pit     changes/sec while on pit road or in the pit stall
    rate_replay  changes/sec while IsReplayPlaying
    replay_avail fraction present while IsReplayPlaying

Then a label:
    live         changes regularly while driving
    pit-only     changes only during pit activity (tire wear/tread temps expect this)
    static       never changed during the recording
    absent       never present
    replay-blank present live but blank in replay
    slow         changes, but rarely (flags, counters, lap numbers)

One recording is not the truth. Run practice, quali, race+pit stop, rain,
replay; each produces its own VARIABLES.md and the union is the spec.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .recorder import iter_recording


def _changed(a, b):
    return a != b


def analyze(path: str) -> dict:
    header = None
    stats = {}
    prev = {}
    n_ticks = 0
    span = 0.0
    first_wall = None
    seg_time = defaultdict(float)  # track / pit / replay seconds
    sessions = 0
    last_wall = None

    for rec in iter_recording(path):
        if rec["type"] == "header":
            header = rec
            for h in rec["vars"]:
                stats[h["name"]] = dict(meta=h, present=0, changes=0, changes_track=0, changes_pit=0,
                                        changes_replay=0, present_replay=0, ticks_replay=0,
                                        distinct=set(), sample=None)
            continue
        if rec["type"] == "session":
            sessions += 1
            continue
        if rec["type"] != "tick":
            continue
        n_ticks += 1
        wall = rec["wall"]
        if first_wall is None:
            first_wall = wall
        dt = 0.0 if last_wall is None else max(0.0, wall - last_wall)
        last_wall = wall
        v = rec["vars"]
        on_track = bool(v.get("IsOnTrack"))
        pit = bool(v.get("OnPitRoad")) or bool(v.get("PlayerCarInPitStall")) or v.get("PlayerTrackSurface") in (1, 2)
        replay = bool(v.get("IsReplayPlaying"))
        seg = "replay" if replay else ("pit" if pit else ("track" if on_track else "other"))
        seg_time[seg] += dt

        for name, s in stats.items():
            val = v.get(name)
            if val is not None:
                s["present"] += 1
                if s["sample"] is None:
                    s["sample"] = val
                if len(s["distinct"]) < 50:
                    s["distinct"].add(json.dumps(val) if isinstance(val, list) else val)
            if replay:
                s["ticks_replay"] += 1
                if val is not None:
                    s["present_replay"] += 1
            if name in prev and _changed(prev[name], val):
                s["changes"] += 1
                if seg == "track":
                    s["changes_track"] += 1
                elif seg == "pit":
                    s["changes_pit"] += 1
                elif seg == "replay":
                    s["changes_replay"] += 1
            prev[name] = val

    span = (last_wall - first_wall) if (first_wall is not None and last_wall) else 0.0
    out = {"path": path, "ticks": n_ticks, "seconds": round(span, 1), "sessions_updates": sessions,
           "segments_s": {k: round(t, 1) for k, t in seg_time.items()}, "vars": {}}

    def rate(ch, secs):
        return round(ch / secs, 3) if secs > 0 else None

    for name, s in stats.items():
        present = s["present"] / n_ticks if n_ticks else 0.0
        rt = rate(s["changes_track"], seg_time["track"])
        rp = rate(s["changes_pit"], seg_time["pit"])
        rr = rate(s["changes_replay"], seg_time["replay"])
        ra = (s["present_replay"] / s["ticks_replay"]) if s["ticks_replay"] else None
        if present == 0:
            label = "absent"
        elif s["changes"] == 0:
            label = "static"
        elif (rt or 0) < 0.02 and (rp or 0) > 0:
            label = "pit-only"
        elif (rt or 0) >= 0.5:
            label = "live"
        elif rt is None and (rr or 0) >= 0.5:
            # Replay-only recording: IsOnTrack is false for every tick, so there
            # is no "track" segment and rate_track is None. The variable still
            # demonstrably changes, but a replay only proves iRacing RECORDS it
            # -- not that it is populated live in a session. Label it distinctly
            # so the two claims are never conflated in VARIABLES_TO_VERIFY.md.
            label = "live(replay)"
        else:
            label = "slow"
        if ra is not None and ra < 0.5 and present > 0.5:
            label += ",replay-blank"
        out["vars"][name] = {
            "type": s["meta"]["type"], "count": s["meta"]["count"], "unit": s["meta"]["unit"],
            "desc": s["meta"]["desc"], "present": round(present, 3), "changes": s["changes"],
            "rate_track": rt, "rate_pit": rp, "rate_replay": rr, "replay_present": ra,
            "distinct": len(s["distinct"]), "label": label,
            "sample": s["sample"] if not isinstance(s["sample"], list) else f"[{len(s['sample'])} values]",
        }
    return out


TYPE_NAMES = {0: "char", 1: "bool", 2: "int", 3: "bitfield", 4: "float", 5: "double"}


def to_markdown(report: dict) -> str:
    lines = [f"# Variable behavior report", "",
             f"Recording: `{report['path']}`  ", f"Ticks: {report['ticks']} over {report['seconds']} s  ",
             f"Session-info updates seen: {report['sessions_updates']}  ",
             f"Time by segment (s): {report['segments_s']}", "",
             "Labels: **live** changes while driving; **slow** changes rarely (counters, flags); "
             "**pit-only** changes only in pit activity; **static** never changed; **absent** never present; "
             "**live(replay)** changes at live rates but was only ever seen in a replay, which proves "
             "iRacing records it, not that it is populated in a live session; "
             "**replay-blank** present live but not in replay.", "",
             "| Variable | Type | N | Unit | Label | Present | Δ/s track | Δ/s pit | Δ/s replay | Sample | Description |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in sorted(report["vars"]):
        v = report["vars"][name]
        lines.append("| `{}` | {} | {} | {} | **{}** | {:.0%} | {} | {} | {} | {} | {} |".format(
            name, TYPE_NAMES.get(v["type"], v["type"]), v["count"], v["unit"] or "", v["label"],
            v["present"], v["rate_track"] if v["rate_track"] is not None else "–",
            v["rate_pit"] if v["rate_pit"] is not None else "–",
            v["rate_replay"] if v["rate_replay"] is not None else "–",
            str(v["sample"])[:18].replace("|", "/"), (v["desc"] or "").replace("|", "/")))
    return "\n".join(lines) + "\n"
