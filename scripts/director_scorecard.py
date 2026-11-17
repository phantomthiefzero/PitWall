"""The director's scorecard: run every recording through the director and compare with the kept scores.

    python scripts/director_scorecard.py [folder or capture ...] [--seeds 3] [--save]

With no paths it reads Documents\\iRacingSuite\\Recordings (the Record button's folder). Exits 1 when any race scores
worse than its kept range (pitwall.director_scorecard), so it can stand in front of a change to the director.
--save keeps this run's scores as the new range. Captures are played one at a time (a big field takes 1.5 GB).
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pitwall import director_scorecard as sc  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--seeds", type=int, default=len(sc.SEEDS))
    ap.add_argument("--save", action="store_true", help="keep these scores as the new range")
    ap.add_argument("--baseline", default=sc.BASELINE)
    a = ap.parse_args()
    paths = a.paths or [os.path.join(os.path.expanduser("~"), "Documents", "iRacingSuite", "Recordings")]
    files = sc.recordings(paths)
    if not files:
        print("no recordings found in", ", ".join(paths))
        return 2
    kept, scores, worse = sc.load(a.baseline), {}, False
    for f in files:
        name = os.path.basename(f)
        now = sc.score(f, seeds=tuple(range(1, a.seeds + 1)))
        cmp = sc.compare(now, kept.get(name))
        worse = worse or cmp["verdict"] == "worse"
        scores[name] = now
        print(sc.line(name, now, cmp), flush=True)
    if a.save:
        sc.save(scores, a.baseline)
        print("kept as the new scores:", a.baseline)
    return 1 if worse else 0


if __name__ == "__main__":
    sys.exit(main())
