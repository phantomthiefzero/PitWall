"""How well the race director keeps the camera on the action, from a capture.

    python scripts/director_report.py recordings/race.jsonl.gz [--session N] [--seed 1] [--top N] [--list]

The work is pitwall.director_report (the suite's home page uses it too): every pass for position in a race, live /
in the picture / late / missed, and whether a pass that was not live was replayed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pitwall.director_report import run, judge, summary  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--session", type=int)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--top", type=int, help="only passes for this class place or better")
    ap.add_argument("--list", action="store_true", help="every pass, one a line")
    a = ap.parse_args()
    passes, on, replays, _ = run(a.path, a.session, a.seed)
    rows = judge(passes, on, replays, a.top)
    if a.list:
        for r in rows:
            print("%7.1f  P%-2s  %3s passes %-3s  %-6s %s%s  on air: %s car %s" % (
                r["t"], r["classPos"], r["idx"], r["passed"], r["verdict"], "replayed " if r["replayed"] else "",
                "(the start)" if r["start"] else "", r["onAir"][0], r["onAir"][1]))
    print(summary(rows))
    rest = [r for r in rows if not r["start"]]
    print("  after the start: %s" % summary(rest))
    for top in (3, 10):
        print("  top %d after the start: %s" % (top, summary([r for r in rest if (r["classPos"] or 99) <= top])))
    shots = sum(1 for x, y in zip(on, on[1:]) if x[1] != y[1])
    print("  %d replays, %d cuts" % (len(replays), shots))


if __name__ == "__main__":
    main()
