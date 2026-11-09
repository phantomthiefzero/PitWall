"""How well the race director keeps the camera on the action, from a capture.

    python scripts/director_report.py recordings/race.jsonl.gz [--session N] [--seed 1]

Runs the capture through normalize and the director as the bridge does (no sim, no camera: the director's cuts are
recorded instead of sent) and reports, for every pass for position in a race, whether the camera was already on one
of the two cars when it happened (live), only arrived after it (late), or never showed it (missed), and whether a
missed pass was replayed. A pass counts as live when the camera was on the passer or the passed car at any moment in
the LIVE_BEFORE_S before normalize reported it (its event comes a moment after the cars change places).
"""
import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pitwall.director import Director                                       # noqa: E402
from pitwall.normalize import Deriver, build_session, build_state, build_cars  # noqa: E402
from pitwall.sources import ReplaySource                                    # noqa: E402

LIVE_BEFORE_S = 6.0
LATE_AFTER_S = 12.0
NEAR_LAP = 0.02              # 2% of a lap from the camera's car: in the same shot (about a second and a half)


def run(path, session=None, seed=1, top=None, log=False):
    src = ReplaySource(path, realtime=False)
    src.connect()
    deriver, sess, k = Deriver(), None, 0
    t = [0.0]
    on = []                      # (t, car idx on camera, story cars, replaying)
    replays = []                 # (t, moment) the director replayed

    num_to_idx = {}

    def cut(num, group):
        return {"ok": True}

    def replay(cmd):
        return {"ok": True}

    d = Director(cut, log=(print if log else (lambda *a: None)), rng=random.Random(seed), clock=lambda: t[0], replay=replay)
    d.set_live(True)
    d.configure(on=True)
    cars = state = None
    passes = []
    seen_ev = set()
    while src.tick():
        k += 1
        if sess is None or k % 600 == 0:
            sess = build_session(src)
            num_to_idx = {str(dr.get("carNumber")): dr.get("idx") for dr in sess.get("drivers") or []}
        state = build_state(src, deriver, sess)
        cars = build_cars(src, sess, deriver)
        snum = (state.get("session") or {}).get("num")
        if session is not None and snum != session:
            continue
        t[0] = float(src.get("SessionTime") or 0.0)
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
    return passes, on, replays


def judge(passes, on, replays, top=None):
    out = []
    for e in passes:
        if top and (e.get("classPos") or 99) > top:
            continue
        pair = {e.get("idx")} | set((e.get("passedIdx") or [])[:1])
        te = e.get("t") or 0.0
        live = any(te - LIVE_BEFORE_S <= ts <= te and c in pair and not rp for ts, c, _, rp, _, _ in on)
        late = not live and any(te < ts <= te + LATE_AFTER_S and c in pair and not rp for ts, c, _, rp, _, _ in on)
        # in the picture: the camera on a car right by them (a chase or a wide shot of a pack shows both)
        near = live or any(te - LIVE_BEFORE_S <= ts <= te and not rp and c in pos and any(
            p in pos and abs(pos[c] - pos[p]) < NEAR_LAP for p in pair) for ts, c, _, rp, _, pos in on)
        rep = any((m or {}).get("kind") == "pass" and (m or {}).get("idx") == e.get("idx")
                  and abs(((m or {}).get("t") or -1e9) - te) < 1.0 for _, m in replays)
        story = next((key for ts, _, _, _, key, _ in reversed(on) if ts <= te), None)
        what = next(((key, c) for ts, c, _, _, key, _ in reversed(on) if ts <= te), (None, None))
        opening = story == "start" or (e.get("carLap") is not None and e["carLap"] <= 1)   # the opening lap
        out.append({"t": te, "start": opening, "classPos": e.get("classPos"), "idx": e.get("idx"), "passed": (e.get("passedIdx") or [None])[0],
                    "verdict": "live" if live else "late" if late else "missed", "replayed": rep, "near": near, "onAir": what})
    return out


def summary(rows):
    n = len(rows)
    if not n:
        return "no passes for position"
    c = {v: sum(1 for r in rows if r["verdict"] == v) for v in ("live", "late", "missed")}
    rep = sum(1 for r in rows if r["verdict"] != "live" and r["replayed"])
    near = sum(1 for r in rows if r["near"])
    return "%d passes: %d live (%d%%), %d in the picture (%d%%), %d late, %d missed; %d of the %d not live replayed" % (
        n, c["live"], round(100 * c["live"] / n), near, round(100 * near / n), c["late"], c["missed"], rep, c["late"] + c["missed"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--session", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--top", type=int, help="only passes for this class place or better")
    ap.add_argument("--list", action="store_true", help="every pass, one a line")
    a = ap.parse_args()
    passes, on, replays = run(a.path, a.session, a.seed)
    rows = judge(passes, on, replays, a.top)
    if a.list:
        for r in rows:
            print("%7.1f  P%-2s  %3s passes %-3s  %-6s %s%s" % (r["t"], r["classPos"], r["idx"], r["passed"], r["verdict"], "replayed " if r["replayed"] else "", "(the start)" if r["start"] else "") + "  on air: %s car %s" % r["onAir"])
    print(summary(rows))
    rest = [r for r in rows if not r["start"]]
    print("  after the start: %s" % summary(rest))
    for top in (3, 10):
        print("  top %d after the start: %s" % (top, summary([r for r in rest if (r["classPos"] or 99) <= top])))
    shots = sum(1 for x, y in zip(on, on[1:]) if x[1] != y[1])
    print("  %d replays, %d cuts" % (len(replays), shots))


if __name__ == "__main__":
    main()
