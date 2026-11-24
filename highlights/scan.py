"""Scanning a race's replay in iRacing: jump to the race's start, play it fast, read every frame.

The replay file itself cannot be read (iRacing's own format), but the sim sends the same telemetry while a replay
plays, for every car. The scan plays the race session at SPEED (16x: a 6-hour race in about 23 minutes), feeds
each new frame to the Analyzer, and writes the frames it read to a scan file (the Recording format, only the
variables the Analyzer reads), so the moments can be found again later without scanning again.

Every replay command goes through pitwall.commands.CommandGate (the only module that talks to iRacing).
"""
from __future__ import annotations

import gzip
import json
import math
import os
import time

from .moments import field, make_analyzer, race_sessions, tape_time

SPEED = 16
SCAN_VARS = ("SessionNum", "SessionTime", "SessionState", "IsReplayPlaying", "ReplaySessionNum", "ReplaySessionTime",
             "ReplayPlaySpeed", "ReplayFrameNum", "CamCarIdx", "PlayerCarIdx",
             "CarIdxLapCompleted", "CarIdxLapDistPct", "CarIdxTrackSurface", "CarIdxOnPitRoad",
             "CarIdxLastLapTime", "CarIdxClass", "CarIdxClassPosition", "CarIdxPosition")
INFO_KEYS = ("WeekendInfo", "DriverInfo", "SessionInfo", "CameraInfo")
STALL_S = 6.0               # the tape not moving this long (wall time) at speed: the end of the tape
AFTER_FINISH_S = 30.0       # tape seconds read after the car takes the flag
LAND_WAIT_S = 10.0
KICK_S = 1.5               # the tape not moving this long: ask for the speed again (iRacing can drop a speed sent just
KICKS = 4                  # after a jump: Watkins Glen, Nov 2026, the scan sat paused at the start and ended empty)
SETTLE_S = 0.5             # after a jump, before asking for the speed
MIN_FRAMES = 300           # a scan that read fewer frames than this did not really play: an error, not a result
STEP_WAIT_S = 3.0           # a jump (to the tape's start, to the next session) shows in the telemetry within this


class ScanError(RuntimeError):
    pass


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def session_snapshot(src) -> dict:
    return {k: src.session_info(k) for k in INFO_KEYS}


def look(src) -> dict:
    """What is open in iRacing right now: is a replay playing, the field, the race sessions, the camera's car."""
    if not src.tick():
        return {"ok": False, "why": "iRacing is not running (or no session is loaded)."}
    info = session_snapshot(src)
    races = race_sessions(info.get("SessionInfo"))
    out = {"ok": True, "replay": bool(src.get("IsReplayPlaying")), "field": field(info.get("DriverInfo")),
           "races": races, "camCar": src.get("CamCarIdx"), "player": src.get("PlayerCarIdx"),
           "cameras": camera_groups(info.get("CameraInfo")), "track": "", "simMode": None}
    wk = info.get("WeekendInfo") or {}
    if isinstance(wk, dict):
        out["track"] = wk.get("TrackDisplayName") or ""
        out["simMode"] = wk.get("SimMode")
    if not out["replay"]:
        out["why"] = "Open the race's replay in iRacing first (it must be playing, not the live session)."
    elif not races:
        out["why"] = "This replay has no race session."
    return out


def camera_groups(camera_info) -> list[dict]:
    out = []
    for g in ((camera_info or {}).get("Groups") or []):
        if isinstance(g, dict) and isinstance(g.get("GroupNum"), int):
            out.append({"num": g["GroupNum"], "name": g.get("GroupName") or str(g["GroupNum"])})
    return out


class ScanWriter:
    """The Recording format (pitwall.recorder), only the scan's variables."""

    def __init__(self, path, headers):
        self.f = gzip.open(path, "wt", encoding="utf-8")
        keep = [h for h in headers if h.get("name") in SCAN_VARS]
        self.f.write(json.dumps({"type": "header", "recordedAt": time.time(), "source": "highlights-scan",
                                 "hz": 0, "vars": keep}) + "\n")
        self.n = 0

    def session(self, update, info):
        self.f.write(json.dumps({"type": "session", "update": update, "wall": time.time(), "info": info},
                                default=str) + "\n")

    def tick(self, get):
        self.f.write(json.dumps({"type": "tick", "wall": time.time(), "n": self.n,
                                 "vars": {k: _clean(get(k)) for k in SCAN_VARS}}) + "\n")
        self.n += 1

    def close(self):
        self.f.close()


def _wait_moved(src, before, clock, sleep, limit=STEP_WAIT_S):
    """Ticks until the tape's (session, time) differs from `before`; returns where it is (or None)."""
    end, now = clock() + limit, None
    while clock() < end:
        if src.tick():
            now = tape_time(src.get)
            if now != before:
                return now
        sleep(0.05)
    return now


def to_race_start(src, send, race_session, clock=time.monotonic, sleep=time.sleep, log=print) -> bool:
    """Puts the paused tape at the start of the race session the quick way: to the tape's start, then on a session at
    a time (both are jumps in iRacing, however long the replay). A search by session time scrubs through the tape and,
    from the end of a long replay, stutters for a long time; it is only the fallback. Returns True when there."""
    where = tape_time(src.get) if src.tick() else None
    send({"cmd": "replay", "action": "search", "mode": "to_start"})
    moved = _wait_moved(src, where, clock, sleep)
    if (moved is None or moved == where) and where is not None and (where[1] or 0) > 5:
        where = None                           # it did not move and it is not at a start: the quick way is not working
    else:
        where = moved or where
    for _ in range(12):
        if where is None or where[0] is None:
            break
        if where[0] == race_session:
            log("scan: at the start of the race session (t %.1f)" % (where[1] or 0))
            return True
        if where[0] > race_session:
            break
        send({"cmd": "replay", "action": "search", "mode": "next_session"})
        moved = _wait_moved(src, where, clock, sleep)
        if moved is None or moved == where:
            break
        where = moved
    log("scan: the quick way did not reach session %s: searching by time" % race_session)
    send({"cmd": "replay", "action": "jump", "session": race_session, "timeS": 0, "preroll": 0})
    send({"cmd": "replay", "action": "speed", "speed": 0})
    end = clock() + LAND_WAIT_S
    while clock() < end:
        if src.tick() and tape_time(src.get)[0] == race_session:
            return True
        sleep(0.05)
    return False


def scan(src, gate, target, out_path, race_session=None, speed=SPEED, stop=None, progress=None, log=print,
         clock=time.monotonic, sleep=time.sleep) -> dict:
    """Plays the race session of the open replay at `speed` and finds `target`'s moments. Returns the Analyzer's
    result with "scanFile". The replay is left paused."""
    if not src.tick():
        raise ScanError("iRacing is not running.")
    if not src.get("IsReplayPlaying"):
        raise ScanError("Open the race's replay in iRacing first.")
    info = session_snapshot(src)
    races = race_sessions(info.get("SessionInfo"))
    if race_session is None:
        if not races:
            raise ScanError("This replay has no race session.")
        race_session = races[-1]
    a = make_analyzer(target, race_session)
    a.session(info)

    def send(cmd):
        r = gate.execute(src, cmd)
        if not r.get("ok"):
            raise ScanError("iRacing did not take the replay command: %s" % r.get("error"))
        return r

    log("scan: going to the start of session %s" % race_session)
    send({"cmd": "replay", "action": "speed", "speed": 0})
    to_race_start(src, send, race_session, clock=clock, sleep=sleep, log=log)
    sleep(SETTLE_S)
    send({"cmd": "replay", "action": "speed", "speed": int(speed)})
    kicks = 0
    w = ScanWriter(out_path, src.var_headers())
    w.session(src.session_info_update, info)
    last_siu = src.session_info_update
    last_t, last_move = None, clock()
    in_race = False
    why = "end of the tape"
    try:
        while True:
            if stop is not None and stop():
                why = "stopped"
                break
            if not src.tick():
                if not src.is_connected:
                    raise ScanError("iRacing closed during the scan.")
                sleep(0.01)
                continue
            s, t = tape_time(src.get)
            if s == race_session:
                in_race = True
            elif in_race:
                why = "end of the race session"
                break
            if src.session_info_update != last_siu:
                last_siu = src.session_info_update
                info = session_snapshot(src)
                a.session(info)
                w.session(last_siu, info)
            if not isinstance(t, (int, float)) or (last_t is not None and t <= last_t):
                playing = src.get("ReplayPlaySpeed")
                if clock() - last_move > KICK_S and kicks < KICKS and playing != int(speed):
                    kicks += 1
                    log("scan: the tape is not playing (speed %s): asking again (%d)" % (playing, kicks))
                    send({"cmd": "replay", "action": "speed", "speed": int(speed)})
                    last_move = clock()
                    continue
                if clock() - last_move > STALL_S:
                    break
                sleep(0.005)
                continue
            last_t, last_move = t, clock()
            w.tick(src.get)
            a.feed(s, t, src.get)
            if progress is not None and w.n % 60 == 0:
                progress(t)
            if a.scan_done(t):
                why = "the finish is in"
                break
    finally:
        w.close()
        try:
            gate.execute(src, {"cmd": "replay", "action": "speed", "speed": 0})
            if a.frame_map and src.is_connected:
                # back to the start of the race, paused, by its frame (instant; no scrubbing back through the tape)
                gate.execute(src, {"cmd": "replay", "action": "frame", "frame": a.frame_map[0][1]})
        except Exception:  # noqa: BLE001
            pass
    log("scan: done (%s), %d frames" % (why, w.n))
    if w.n < MIN_FRAMES and why != "stopped":
        try:
            os.remove(out_path)                # not a scan anyone can use: not left among the saved ones
        except OSError:
            pass
        raise ScanError("The replay did not play in iRacing (the scan read %d frames). Press play in the replay "
                        "once, then Scan the race again." % w.n)
    out = a.finish()
    out["scanFile"] = out_path
    out["frames"] = w.n
    out["ended"] = why
    return out
