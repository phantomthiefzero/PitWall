"""Ready to go live? The home page's checklist: everything a broadcast or a session leans on, in one place.

Each check says what it found in a few words and, when something is off, what to do. Nothing here changes anything:
it only looks (the sim connection, PitWall's relay and bridge, the pages connected to the relay, the last camera check,
the share link, recording and the disk, updates). States: ok, warn (works, but look), bad (will not work), info.
"""
from __future__ import annotations

import json
import os
import shutil
import time


def _check(key, name, state, detail, fix=""):
    return {"key": key, "name": name, "state": state, "detail": detail, "fix": fix}


def _ago(t):
    s = max(0, time.time() - t)
    if s < 3600:
        return "%d min ago" % (s // 60)
    if s < 86400 * 2:
        return "%d h ago" % (s // 3600)
    return "%d days ago" % (s // 86400)


def camera_check(path=None):
    """The last camera and replay check (pitwall.simcheck writes sim_check.json beside sim_log.txt)."""
    if path is None:
        try:
            from pitwall import simlog
            path = os.path.join(os.path.dirname(simlog.path()), "sim_check.json")
        except Exception:
            return _check("camera", "Camera and replay commands", "warn", "could not look for the last check")
    try:
        with open(path, encoding="utf-8") as f:
            r = json.load(f)
        when = os.path.getmtime(path)
    except (OSError, ValueError):
        return _check("camera", "Camera and replay commands", "warn", "never checked on this PC",
                      "In the producer: Setup > Check, with the broadcast on and iRacing running (about a minute)")
    steps = r.get("steps") or []
    bad = [s.get("name") or "?" for s in steps if s.get("state") != "passed"]
    if not steps:
        return _check("camera", "Camera and replay commands", "warn", "the last check did not finish (%s)" % _ago(when),
                      "Run it again: producer Setup > Check")
    if bad:
        return _check("camera", "Camera and replay commands", "bad",
                      "%d of %d did not work (%s): %s" % (len(bad), len(steps), _ago(when), ", ".join(map(str, bad[:4]))),
                      "Look at sim_check.json beside the car pictures, and send it in")
    return _check("camera", "Camera and replay commands", "ok", "all %d worked (%s)" % (len(steps), _ago(when)))


def disk(folder, mode=None):
    try:
        free = shutil.disk_usage(folder).free / 1024 ** 3
    except OSError:
        return _check("disk", "Room for recordings", "warn", "could not read the disk")
    words = "%.0f GB free where recordings go" % free
    if free < 2:
        return _check("disk", "Room for recordings", "bad", words, "Free some space: a race takes 10 to 60 MB")
    if free < 10:
        return _check("disk", "Room for recordings", "warn", words)
    return _check("disk", "Room for recordings", "ok", words)


def checks(engine) -> list:
    out = []
    hub = getattr(engine, "hub", None)
    on = bool(hub and hub.connected)
    out.append(_check("sim", "iRacing", "ok" if on else "warn", "running" if on else "not running",
                      "" if on else "Start iRacing and join or watch a session"))
    pw = getattr(engine, "pitwall", None)
    info = pw.info() if pw else {}
    if not info.get("ok"):
        out.append(_check("pitwall", "PitWall and the broadcast", "bad", info.get("error") or "not started",
                          "Close the app and start it again; if it stays, send in the log (Settings)"))
        return out
    out.append(_check("pitwall", "PitWall and the broadcast", "ok", "running on this PC"))
    relay = getattr(pw, "relay", None)
    pages = relay.page_counts() if relay is not None and hasattr(relay, "page_counts") else {}
    n = pages.get("overlay", 0)
    out.append(_check("obs", "OBS overlay", "ok" if n else "warn",
                      ("%d overlay open" % n + ("s" if n > 1 else "")) if n else "no overlay connected",
                      "" if n else "In OBS, add a Browser source with the overlay link (producer: Setup > OBS)"))
    p = pages.get("producer", 0)
    out.append(_check("producer", "Producer", "ok" if p else "info", "open" if p else "not open",
                      "" if p else "Open it from the PitWall tile: Broadcast > Producer"))
    bridge = getattr(pw, "bridge", None)
    d = getattr(bridge, "director", None)
    if d is not None:
        out.append(_check("live", "Broadcast switch", "ok" if d.live else "info", "on" if d.live else "off",
                          "" if d.live else "The producer's Broadcast switch at the top"))
        if on:
            has = getattr(bridge, "map_msg", None) is not None
            out.append(_check("map", "Track map", "ok" if has else "warn", "ready" if has else "none for this track yet",
                              "" if has else "Drive a few laps here (or keep a telemetry file of this track: the suite "
                                             "builds the outline from it)"))
    out.append(camera_check())
    sh = getattr(pw, "share", None)
    st = sh.info().get("state") if sh is not None else "off"
    out.append(_check("share", "Share link", "ok" if st == "on" else "info",
                      "on" if st == "on" else "off: only this PC sees PitWall" if st in ("off", None) else str(st)))
    rec = getattr(engine, "recorder", None)
    auto = getattr(engine, "autorec", None)
    if rec is not None:
        mode = (auto.settings.get("mode") if auto is not None else "off")
        state = rec.state
        words = {"recording": "recording now", "waiting": "waiting for iRacing"}.get(state, "not recording")
        words += {"races": " · races are recorded automatically", "all": " · every session is recorded automatically",
                  "off": " · recording by hand only"}.get(mode, "")
        out.append(_check("rec", "Recording", "ok" if state == "recording" or mode != "off" else "info", words,
                          "" if mode != "off" or state == "recording" else "Turn on Record automatically, or press Record"))
        try:
            from suite.recording import recordings_dir
            out.append(disk(recordings_dir(), mode))
        except Exception:
            pass
    up = getattr(engine, "updater", None)
    if up is not None:
        u = up.info()
        if u.get("available"):
            out.append(_check("update", "Updates", "warn", "build %s is ready" % (u.get("latest") or {}).get("build"),
                              "Update after the show, not before it"))
        else:
            out.append(_check("update", "Updates", "ok", "up to date"))
    return out
