"""The director's scorecard: every recording, a few runs each, against the last scores kept.

One change to the director moves the camera at every cut after it, so one run of one race says little: Talladega's
pack gave 22 to 24 passes live out of 57 from runs that differed only in the random seed. So each recording is run with
several seeds (the director picks nothing at random, but the seed decides ties and the angle a sequence starts on),
and a score only counts as worse when it falls below the lowest the kept scores ever had, by more than one pass.

The scores are those of pitwall.director_report for passes for the top 3 of each class after the opening lap (one
camera cannot be live on 300 passes: judge it on the top 3): live, in the picture, missed, and of those not live, how
many were replayed. Kept in docs/director_baseline.json by the recording's file name, so a race recorded on another
PC compares with itself. Used by scripts/director_scorecard.py.
"""
from __future__ import annotations

import json
import os

from .director_report import report

SEEDS = (1, 2, 3)
METRICS = ("live", "near", "missed", "replayed")      # what the table shows; missed is the one where fewer is better
WORSE_BY = 1                                          # passes: less than this off the kept range is no change
BASELINE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "director_baseline.json")


def score(path, seeds=SEEDS, progress=None, stop=None) -> dict:
    """{passes, runs: [{live, near, missed, replayed}, ...]} for the top 3 after the opening lap, one run a seed."""
    runs, n = [], 0
    for s in seeds:
        r = report(path, seed=s, progress=progress, stop=stop)["top3"]
        n = r["passes"]
        runs.append({k: r[k] for k in METRICS})
        if stop is not None and stop():
            break
    return {"passes": n, "seeds": list(seeds)[:len(runs)], "runs": runs}


def _range(runs, k):
    vals = [r[k] for r in runs]
    return (min(vals), max(vals)) if vals else (None, None)


def compare(now: dict, kept: dict | None) -> dict:
    """Each metric's range now against the kept range: "worse" when the best run now is worse than the worst run kept
    by more than WORSE_BY passes, "better" the other way round, else "same". A race with no kept scores is "new"; one
    whose number of passes changed (normalize found the passes differently) is compared as shares of its passes."""
    if not kept or not kept.get("runs"):
        return {"verdict": "new", "metrics": {}}
    n0, n1 = kept.get("passes") or 0, now.get("passes") or 0
    scale = (n1 / n0) if n0 and n1 and n0 != n1 else 1.0
    out, verdict = {}, "same"
    for k in METRICS:
        lo0, hi0 = _range(kept["runs"], k)
        lo1, hi1 = _range(now["runs"], k)
        if lo0 is None or lo1 is None:
            continue
        lo0, hi0 = lo0 * scale, hi0 * scale
        fewer_better = k == "missed"
        if fewer_better:
            v = "worse" if lo1 > hi0 + WORSE_BY else "better" if hi1 < lo0 - WORSE_BY else "same"
        else:
            v = "worse" if hi1 < lo0 - WORSE_BY else "better" if lo1 > hi0 + WORSE_BY else "same"
        out[k] = {"now": [lo1, hi1], "kept": [round(lo0, 1), round(hi0, 1)], "verdict": v}
        if v == "worse":
            verdict = "worse"
        elif v == "better" and verdict == "same":
            verdict = "better"
    return {"verdict": verdict, "metrics": out}


def load(path=BASELINE) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(scores: dict, path=BASELINE) -> None:
    kept = load(path)
    kept.update(scores)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(kept, f, indent=1, sort_keys=True)
        f.write("\n")


def recordings(paths) -> list:
    """The captures in the paths given (files or folders), by name."""
    out = []
    for p in paths:
        if os.path.isdir(p):
            out += [os.path.join(p, f) for f in sorted(os.listdir(p)) if f.endswith(".jsonl.gz")]
        elif p.endswith(".jsonl.gz") and os.path.exists(p):
            out.append(p)
    return out


def line(name, now, cmp) -> str:
    def rng(a):
        return "%d" % a[0] if a[0] == a[1] else "%d-%d" % (a[0], a[1])
    m = cmp.get("metrics") or {}
    parts = []
    for k, word in (("live", "live"), ("near", "in the picture"), ("missed", "missed"), ("replayed", "replayed")):
        lo, hi = _range(now["runs"], k)
        mark = {"worse": " (worse)", "better": " (better)"}.get((m.get(k) or {}).get("verdict"), "")
        parts.append("%s %s%s" % (rng((lo, hi)), word, mark))
    return "%-52s %3d passes: %s  [%s]" % (name[:52], now["passes"], ", ".join(parts), cmp["verdict"])
