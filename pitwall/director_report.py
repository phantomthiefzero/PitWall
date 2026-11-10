"""How well the race director kept the camera on the action, from a capture.

Runs a capture (.jsonl.gz, `pitwall record` or the suite's Record button) through normalize and the director as
the bridge does -- no sim, no camera: the director's cuts are recorded instead of sent -- and judges every pass for
position in a race: live (the camera was on the passer or the passed car at some moment in the LIVE_BEFORE_S before
normalize reported it; its event comes a moment after the cars change places), in the picture (on a car right by
them: a chase or a wide shot shows both), late (it got there within LATE_AFTER_S after), or missed; and whether a
pass that was not live was replayed. Used by scripts/director_report.py and the suite's home page.
"""
from __future__ import annotations

import random

from .director import Director
from .normalize import Deriver, build_session, build_state, build_cars
from .sources import ReplaySource

LIVE_BEFORE_S = 6.0
LATE_AFTER_S = 12.0
NEAR_LAP = 0.02              # 2% of a lap from the camera's car: in the same shot (about a second and a half)


def run(path, session=None, seed=1, progress=None, stop=None, log=False):
    """Plays the capture through the director. progress(t) is called now and then with the race's clock; stop() true
    ends early. Returns (passes, on air samples, replays, driver names)."""
    src = ReplaySource(path, realtime=False)
    src.connect()
    deriver, sess, k = Deriver(), None, 0
    t = [0.0]
    on = []                      # (t, car idx on camera, story cars, replaying, story, {idx: lap + pct})
    replays = []                 # (t, moment) the director replayed
    names = {}

    d = Director(lambda num, group: {"ok": True}, log=(print if log else (lambda *a: None)), rng=random.Random(seed),
                 clock=lambda: t[0], replay=lambda cmd: {"ok": True})
    d.set_live(True)
    d.configure(on=True)
    passes, seen_ev = [], set()
    while src.tick():
        k += 1
        if sess is None or k % 600 == 0:
            sess = build_session(src)
            for dr in sess.get("drivers") or []:
                if dr.get("CarIdx") is not None and dr.get("UserName"):
                    names[dr["CarIdx"]] = dr["UserName"]
        state = build_state(src, deriver, sess)
        cars = build_cars(src, sess, deriver)
        snum = (state.get("session") or {}).get("num")
        if session is not None and snum != session:
            continue
        t[0] = float(src.get("SessionTime") or 0.0)
        if progress is not None and k % 300 == 0:
            progress(t[0])
        if stop is not None and stop():
            break
        recent = [e for e in deriver.feed_events[-60:] if e.get("kind") in ("race_incident", "pass", "class_lead", "fastest_lap")]
        d.tick(sess, cars, state, recent)
        cur = (d.current or {}).get("car")
        seg = d.segment or {}
        on.append((t[0], cur, tuple(seg.get("cars") or ()), d.rp is not None, seg.get("key") or (d.current or {}).get("reason"),
                   {c["idx"]: (c.get("lap") or 0) + (c.get("pct") or 0.0) for c in cars.get("cars") or [] if c.get("pct") is not None}))
        if d.rp is not None and (not replays or replays[-1][1] is not d.rp.get("moment")):
            replays.append((t[0], d.rp.get("moment")))
        for e in deriver.feed_events[-30:]:
            key = (e.get("kind"), e.get("idx"), e.get("t"), e.get("classPos"))
            if e.get("kind") == "pass" and key not in seen_ev and (cars.get("derived") or {}).get("isRace"):
                seen_ev.add(key)
                passes.append(e)
    return passes, on, replays, names


def judge(passes, on, replays, top=None):
    out = []
    for e in passes:
        if top and (e.get("classPos") or 99) > top:
            continue
        pair = {e.get("idx")} | set((e.get("passedIdx") or [])[:1])
        te = e.get("t") or 0.0
        live = any(te - LIVE_BEFORE_S <= ts <= te and c in pair and not rp for ts, c, _, rp, _, _ in on)
        late = not live and any(te < ts <= te + LATE_AFTER_S and c in pair and not rp for ts, c, _, rp, _, _ in on)
        near = live or any(te - LIVE_BEFORE_S <= ts <= te and not rp and c in pos and any(
            p in pos and abs(pos[c] - pos[p]) < NEAR_LAP for p in pair) for ts, c, _, rp, _, pos in on)
        rep = any((m or {}).get("kind") == "pass" and (m or {}).get("idx") == e.get("idx")
                  and abs(((m or {}).get("t") or -1e9) - te) < 1.0 for _, m in replays)
        story = next((key for ts, _, _, _, key, _ in reversed(on) if ts <= te), None)
        what = next(((key, c) for ts, c, _, _, key, _ in reversed(on) if ts <= te), (None, None))
        opening = story == "start" or (e.get("carLap") is not None and e["carLap"] <= 1)   # the opening lap
        out.append({"t": te, "start": opening, "classPos": e.get("classPos"), "idx": e.get("idx"),
                    "passed": (e.get("passedIdx") or [None])[0], "replayT": e.get("replayT"),
                    "verdict": "live" if live else "late" if late else "missed", "replayed": rep, "near": near,
                    "onAir": list(what)})
    return out


def counts(rows) -> dict:
    n = len(rows)
    c = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("live", "late", "missed")}
    return {"passes": n, **c, "near": sum(1 for r in rows if r["near"]),
            "notLive": c["late"] + c["missed"], "replayed": sum(1 for r in rows if r["verdict"] != "live" and r["replayed"])}


def summary(rows) -> str:
    c = counts(rows)
    if not c["passes"]:
        return "no passes for position"
    n = c["passes"]
    return "%d passes: %d live (%d%%), %d in the picture (%d%%), %d late, %d missed; %d of the %d not live replayed" % (
        n, c["live"], round(100 * c["live"] / n), c["near"], round(100 * c["near"] / n), c["late"], c["missed"],
        c["replayed"], c["notLive"])


def report(path, session=None, seed=1, progress=None, stop=None) -> dict:
    """Everything the home page shows: counts for every pass, after the opening lap, its top 3 and top 10, the
    cuts and replays, and each pass with the drivers' names."""
    passes, on, replays, names = run(path, session, seed, progress, stop)
    rows = judge(passes, on, replays)
    rest = [r for r in rows if not r["start"]]
    for r in rows:
        r["name"] = names.get(r["idx"], "#%s" % r["idx"])
        r["passedName"] = names.get(r["passed"], "#%s" % r["passed"]) if r["passed"] is not None else ""
    return {"all": counts(rows), "afterStart": counts(rest),
            "top3": counts([r for r in rest if (r["classPos"] or 99) <= 3]),
            "top10": counts([r for r in rest if (r["classPos"] or 99) <= 10]),
            "cuts": sum(1 for x, y in zip(on, on[1:]) if x[1] != y[1]), "replays": len(replays),
            "raceS": round(on[-1][0] - on[0][0], 1) if on else 0.0, "rows": rows}
