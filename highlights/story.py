"""How Highlights tells the race: what gets recorded, in what order the pieces play, the words on them, and how long
each step will take. Plain data in, plain data out (no iRacing, no OBS, no ffmpeg), so it is tested on its own.

The full video tells the race as a story:

    cold open   the best moment's key seconds, no words (the hook: something happens before anything is explained)
    title card  the event and track, the car and team, its drivers, its class and where it started
    the race    the moments in race order; a new stint (a driver change in a team race) or, in a long race with one
                driver, a new hour starts a chapter (a small amber line over the clip's words)
    the best    moments carry replays from other cameras (the director's angles), the last slowed down
    result card where it finished, places gained from the start, laps, its best lap, the drivers

A short opens on its best moment a few seconds before it happens (HOOK_LEAD_S), not on the lead-up, then that
moment from other cameras, then the rest in race order, and ends without a fade so it plays round.
"""
from __future__ import annotations

from . import shots as shotlib
from .moments import driver_at, fmt_lap
from .pick import BEST_SPEEDS, SINGLE_SPEEDS, clips as make_clips, hook_first, short_window

HOOK_LEAD_S = 3.0           # a short starts this long before its best moment
TEASER_BEFORE_S = 2.5
TEASER_AFTER_S = 1.5
HOUR_CHAPTERS_FROM_S = 2 * 3600   # one driver, a race this long: a chapter each hour
SCAN_SPEED = 16
SCAN_OVERHEAD_S = 20.0
BUILD_RATE = {"wide": 0.6, "tall": 1.1}   # seconds of building per second of video (refined by each build)
BUILD_EACH_S = 6.0


def _ordinal_pos(p):
    return "P%d" % p if isinstance(p, int) else None


def camera_plan(result, outputs, settings, cams, number) -> dict:
    """Every recording the outputs need, each once: {"main": [clip with its shots], "angles": {moment id: [jobs]},
    "jobs": main and angle jobs in the order to record them (race order, the angles beside their moment)}."""
    ms = result["moments"]
    union = sorted({i for o in outputs for i in o["ids"]})
    main = make_clips(ms, union)
    by_id = {m["id"]: m for m in ms}
    pct_at = shotlib.path_lookup(result.get("path") or [])
    target = result.get("target")
    pref = settings.get("camera", "auto")
    for c in main:
        m = by_id[c["best"]]
        focus = m.get("focus", target)          # whole race: the car this moment is about
        c["shots"] = shotlib.plan(c, m, cams, focus, number, pct_at if focus == target else (lambda t: None),
                                  pref=pref)
        c["role"] = "main"
    n_top = int(settings.get("angleMoments", 3) or 0)
    each = int(settings.get("anglesEach", 2) or 0)
    angles = {}
    if n_top > 0 and each > 0 and pref in ("auto", None, ""):
        best = sorted((by_id[c["best"]] for c in main if by_id[c["best"]]["kind"] in shotlib.ANGLE_KINDS),
                      key=lambda m: 30.0 if m["kind"] == "finish" else m["score"], reverse=True)
        for m in best[:n_top]:
            c = next(x for x in main if x["best"] == m["id"])
            js = shotlib.angles(m, cams, m.get("focus", target), number, shotlib.key_groups(c["shots"], m), each)
            for k, j in enumerate(js):
                j.update(role="angle", moment=m["id"], session=c["session"], n="a%d_%d" % (m["id"], k),
                         kind=m["kind"], caption="%s (another angle)" % m["label"])
            if js:
                angles[m["id"]] = js
    jobs = []
    for c in main:
        jobs.append(c)
        jobs.extend(angles.get(c["best"], []))
    return {"main": main, "angles": angles, "jobs": jobs}


def chapters(result, items):
    """A chapter on the first clip of each new stint (team races); otherwise of each act of the race (edit.acts:
    early laps, mid-race, the closing laps, the finish), so the video reads as a race told in order; one driver in a
    long race, each new hour until the closing laps."""
    from .edit import ACT_NAMES, act_of, acts
    stints = result.get("stints") or []
    spans = acts(result, result.get("moments") or [])
    if len(stints) <= 1 and spans:
        green = result.get("green") or 0.0
        long_race = (result.get("finish") or 0) - green >= HOUR_CHAPTERS_FROM_S
        last = None
        for it in items:
            act = "finish" if it.get("kind") == "finish" else act_of(spans, it["a"] + 1)
            if act in ("early", "middle") and long_race:
                act = "Hour %d" % (int(max(0.0, it["a"] - green) // 3600) + 1)
            if act != last and act not in (None, "start"):
                it["chapter"] = ACT_NAMES.get(act, act)
            last = act
        return
    green = result.get("green") or 0.0
    long_race = (result.get("finish") or 0) - green >= HOUR_CHAPTERS_FROM_S
    last = None
    for it in items:
        if len(stints) > 1:
            n = 1 + max([k for k, st in enumerate(stints) if st["from"] <= it["a"]] or [0])
            if n != last:
                it["chapter"] = "Stint %d  ·  %s" % (n, stints[n - 1]["driver"])
            last = n
        elif long_race:
            hour = int(max(0.0, it["a"] - green) // 3600) + 1
            if last is not None and hour != last:
                it["chapter"] = "Hour %d" % hour
            last = hour


def inputs(out, result, recorded, settings, log=print) -> list[dict]:
    """An output's pieces, each cut from the recording that covers it, with its replays and chapter."""
    ms = {m["id"]: m for m in result["moments"]}
    cs = make_clips(result["moments"], out["ids"], own_labels=bool(out["vertical"]))
    if out["vertical"]:
        cs = hook_first(cs, bool(settings.get("shortReplay", True)))
    good = [x for x in recorded if x.get("ok")]
    mains = [x for x in good if x.get("role", "main") == "main"]
    res = []
    for k, c in enumerate(cs):
        r = next((x for x in mains if x.get("session", c["session"]) == c["session"]
                  and x["a"] <= c["a"] + 0.05 and x["b"] >= c["b"] - 0.05), None)
        if r is None:
            log("%s: no recording covers %s, left out" % (out["name"], c["caption"]))
            continue
        a, b = c["a"], c["b"]
        if out["vertical"]:
            a, b = short_window(c, k)       # the hook straight into the action, the rest cut to their moment
        item = {"path": r["path"], "recStart": r["recStart"], "a": a, "b": b, "caption": c["caption"],
                "kind": c["kind"], "best": c.get("best"), "ok": True}
        angle_recs = [x for x in good if x.get("role") == "angle" and x.get("moment") == c.get("best")]
        if angle_recs and not out["vertical"]:
            item["angles"] = [{"path": x["path"], "recStart": x["recStart"], "a": x["a"], "b": x["b"],
                               "speed": x.get("speed", 1.0)} for x in angle_recs]
        elif angle_recs and k == 0 and c.get("replay"):
            # a short: the key seconds only (pick's replay window), one camera for each speed it was measured with
            w = c["replay"]
            speeds = SINGLE_SPEEDS if out.get("key", "").startswith("s") else BEST_SPEEDS
            item["angles"] = [{"path": x["path"], "recStart": x["recStart"], "a": max(w["a"], x["a"]),
                               "b": min(w["b"], x["b"]), "speed": sp} for x, sp in zip(angle_recs, speeds)]
        elif out["vertical"] and k == 0 and c.get("replay"):
            item["replay"] = {"a": max(c["replay"]["a"], r["a"]), "b": min(c["replay"]["b"], r["b"])}
        if ms.get(c.get("best"), {}).get("kind") == "start" and out["vertical"]:
            continue
        res.append(item)
    if not out["vertical"]:
        chapters(result, res)
    return res


def teaser(result, recorded, ids):
    """The cold open: the best moment's key seconds (from another camera when one was recorded, so the race
    itself shows it fresh later), or None."""
    ms = [m for m in result["moments"] if m["id"] in set(ids) and not m.get("mandatory")
          and m["kind"] in shotlib.ANGLE_KINDS]
    if not ms:
        return None
    m = max(ms, key=lambda x: x["score"])
    key = shotlib.key_time(m)
    good = [x for x in recorded if x.get("ok")]
    pool = [x for x in good if x.get("role") == "angle" and x.get("moment") == m["id"]] + \
           [x for x in good if x.get("role", "main") == "main"]
    for r in pool:
        a, b = max(r["a"], key - TEASER_BEFORE_S), min(r["b"], key + TEASER_AFTER_S)
        if r["a"] <= key <= r["b"] and b - a >= 2.0:
            return {"path": r["path"], "recStart": r["recStart"], "a": a, "b": b}
    return None


def race_cards(result) -> tuple[dict, dict]:
    """The whole race's title card (event, track, the field) and result card (each class's winner)."""
    kicker = "  ·  ".join(x for x in (result.get("event"), result.get("track")) if x)
    classes = result.get("classes") or []
    field = "%d cars" % result["fieldSize"] if result.get("fieldSize") else ""
    lines = ["  ·  ".join(x for x in (field, " / ".join(classes) if len(classes) > 1 else "",
                                      "%s laps" % result["laps"] if result.get("laps") else "") if x)]
    intro = {"kicker": kicker or "Race highlights", "title": "Race in review", "lines": [x for x in lines if x]}
    wins = result.get("winners") or []
    out_lines = []
    for w in wins:
        out_lines.append(("%s  " % w["cls"] if w.get("cls") and len(wins) > 1 else "") +
                         ("#%s %s" % (w.get("number", ""), w.get("name") or "")).strip())
    title = "Winners" if len(wins) > 1 else ("#%s wins" % wins[0]["number"] if wins else "The finish")
    out_lines = out_lines[:4]
    cl = result.get("climber")
    if cl:
        out_lines.append("Biggest climber  #%s  %s to %s  (+%d places)" % (
            cl.get("number", ""), _ordinal_pos(cl["from"]), _ordinal_pos(cl["to"]), cl["gained"]) +
            (" in %s" % cl["cls"] if cl.get("cls") else ""))
    return intro, {"kicker": "Result", "title": title, "lines": out_lines}


def cards(result, target) -> tuple[dict, dict]:
    """The title card and the result card."""
    if result.get("mode") == "race":
        return race_cards(result)
    car = target or {}
    number = result.get("number") or car.get("number") or ""
    team = result.get("team") or car.get("team") or ""
    drivers = result.get("drivers") or ([car["driver"]] if car.get("driver") else [])
    cls = result.get("cls") or car.get("cls") or ""
    title = ("#%s %s" % (number, team or (drivers[0] if drivers else ""))).strip()
    kicker = "  ·  ".join(x for x in (result.get("event"), result.get("track")) if x)
    lines = []
    if drivers and (team or len(drivers) > 1):
        lines.append("  ·  ".join(drivers))
    start = _ordinal_pos(result.get("startPos"))
    lines.append("  ·  ".join(x for x in (cls, result.get("car"), "starts %s" % start if start else None) if x))
    intro = {"kicker": kicker or "Race highlights", "title": title or "Race highlights",
             "lines": [x for x in lines if x]}
    fin = result.get("classPos")
    out_lines = []
    if fin and result.get("startPos"):
        d = result["startPos"] - fin
        out_lines.append("Started %s  ·  %s" % (_ordinal_pos(result["startPos"]),
                                                  "+%d places" % d if d > 0 else "%d places" % d if d < 0 else
                                                  "held its place"))
    bits = []
    if result.get("laps"):
        bits.append("%s laps" % result["laps"])
    best = next((m for m in result.get("moments") or [] if m["kind"] == "fastest"), None)
    if best and best.get("lapTime"):
        bits.append("best lap %s" % fmt_lap(best["lapTime"]))
    if bits:
        out_lines.append("  ·  ".join(bits))
    if len(drivers) > 1:
        out_lines.append("  ·  ".join(drivers))
    outro = {"kicker": "Result" + (" · " + cls if cls else ""),
             "title": ("Finished P%d in class" % fin) if fin else "Took the flag", "lines": out_lines}
    return intro, outro


def scan_estimate(race_s) -> float | None:
    return race_s / SCAN_SPEED + SCAN_OVERHEAD_S if race_s else None


def build_estimate(outputs, rates=None) -> float:
    rates = dict(BUILD_RATE, **(rates or {}))
    return sum(o["length"] * rates["tall" if o["vertical"] else "wide"] + BUILD_EACH_S for o in outputs)


def minutes(s) -> str:
    """'about 4 min', 'under a minute'."""
    if s is None:
        return ""
    if s < 50:
        return "under a minute"
    m = s / 60.0
    return "about %d min" % round(m) if m < 90 else "about %.1f hours" % (m / 60.0)
