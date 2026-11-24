"""Choosing moments to fill a target length, and turning them into clips to record.

auto_pick: the start and the finish always (mandatory), then the best-scoring moments that still fit. A moment
close in time to one of the same kind already chosen counts less (SPREAD_S, SPREAD_FACTOR), so a long race is not
one stint's battle five times. Overlapping moments cost only the extra seconds they add.

clips: the chosen moments' pieces in race order; pieces closer than MERGE_GAP_S become one clip (no stinger in
the middle of a fight). Each clip is captioned by its best moment.
"""
from __future__ import annotations

SPREAD_S = 180.0
SPREAD_FACTOR = 0.75
MERGE_GAP_S = 4.0
MIN_SCORE = 8.0


def clips(moments, chosen_ids) -> list[dict]:
    chosen = [m for m in moments if m["id"] in set(chosen_ids)]
    pieces = []
    for m in chosen:
        for a, b in m["segs"]:
            if b > a:
                pieces.append((m["session"], a, b, m))
    pieces.sort(key=lambda p: (p[0] if p[0] is not None else -1, p[1]))
    out = []
    for s, a, b, m in pieces:
        last = out[-1] if out else None
        if last is not None and last["session"] == s and a - last["b"] <= MERGE_GAP_S:
            last["b"] = max(last["b"], b)
            if m["id"] not in last["moments"]:
                last["moments"].append(m["id"])
            if m["score"] > last["_best"]["score"] and not last["_best"].get("mandatory"):
                last["_best"] = m
            if m.get("mandatory") and not last["_best"].get("mandatory"):
                last["_best"] = m
            continue
        out.append({"session": s, "a": a, "b": b, "moments": [m["id"]], "_best": m})
    for n, c in enumerate(out):
        best = c.pop("_best")
        c["n"] = n
        c["a"], c["b"] = round(c["a"], 2), round(c["b"], 2)
        c["kind"] = best["kind"]
        c["label"] = best["label"]
        c["lap"] = best.get("lap")
        c["caption"] = caption(best)
        c["cars"] = best.get("cars") or []
    return out


def caption(m) -> str:
    lap = m.get("lap")
    if m["kind"] == "start":
        return "The start"
    if m["kind"] == "finish":
        return m["label"].split(" (")[0]
    return ("Lap %d  -  %s" % (lap, m["label"])) if isinstance(lap, int) else m["label"]


def length(moments, chosen_ids, stinger_s=0.0) -> float:
    cs = clips(moments, chosen_ids)
    return sum(c["b"] - c["a"] for c in cs) + max(0, len(cs) - 1) * stinger_s


def auto_pick(moments, target_s, stinger_s=0.0, kinds=None) -> list[int]:
    """Ids of the moments to use. kinds: the kinds allowed (None = all)."""
    allowed = [m for m in moments if kinds is None or m["kind"] in kinds or m.get("mandatory")]
    chosen = [m["id"] for m in allowed if m.get("mandatory")]
    pool = [m for m in allowed if not m.get("mandatory") and m["score"] >= MIN_SCORE]
    used = length(moments, chosen, stinger_s)
    picked = [m for m in moments if m["id"] in chosen]
    while pool:
        def eff(m):
            near = sum(1 for p in picked if p["kind"] == m["kind"] and abs(p["t"] - m["t"]) < SPREAD_S)
            return m["score"] * (SPREAD_FACTOR ** near)
        pool.sort(key=eff, reverse=True)
        took = False
        for m in pool:
            new = length(moments, chosen + [m["id"]], stinger_s)
            if new <= target_s:
                chosen.append(m["id"])
                picked.append(m)
                pool.remove(m)
                used = new
                took = True
                break
        if not took:
            break
    return sorted(chosen)
