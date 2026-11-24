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
        c["score"] = best["score"]
        c["key"] = best.get("key", best["t"])
        c["best"] = best["id"]
        c["driver"] = best.get("driver")
        c["replay"] = replay_window(best)
    return out


REPLAY_KINDS = {"pass": (-2.5, 1.0), "lost": (-2.5, 1.0), "spin": (-1.5, 3.0), "off": (-1.0, 2.5),
                "drama": (-1.5, 2.5)}
REPLAY_SPEED = 0.5


def replay_window(m):
    """The key seconds of a moment, for a slow-motion replay: around the swap of a pass, the start of a spin."""
    w = REPLAY_KINDS.get(m["kind"])
    if w is None:
        return None
    return {"a": round(m["t"] + w[0], 2), "b": round(m["t"] + w[1], 2)}


def replay_s(clip) -> float:
    r = clip.get("replay")
    return (r["b"] - r["a"]) / REPLAY_SPEED if r else 0.0


# ---------------------------------------------------------------------------- vertical shorts
SHORT_KINDS = {"pass", "lost", "battle", "spin", "off", "drama", "towed", "fastest", "finish"}


def hook_first(cs, replay=True) -> list[dict]:
    """A short opens on its best clip (the hook), with its slow-motion replay; the rest follow in race order, cut
    straight (a replay only on the hook)."""
    if not cs:
        return []
    best = max(cs, key=lambda c: 30.0 if c["kind"] == "finish" else c["score"])
    rest = [dict(c, replay=None) for c in cs if c is not best]
    return [dict(best, replay=best.get("replay") if replay else None)] + rest


def short_length(cs, stinger_s=0.0) -> float:
    return sum(c["b"] - c["a"] + replay_s(c) for c in cs) + max(0, len(cs) - 1) * stinger_s


def best_of(moments, max_s, stinger_s=0.0, kinds=None, replay=True) -> list[int]:
    """The moments for one best-of short: the highest scores that fit in max_s, hook first. The start is left out
    (a short starts in the action); the finish only if it scores among the best (its score is not mandatory here)."""
    allowed = [m for m in moments if m["kind"] in SHORT_KINDS and (kinds is None or m["kind"] in kinds
                                                                     or m["kind"] == "finish")]
    pool = sorted(allowed, key=lambda m: _short_score(m), reverse=True)
    chosen = []
    for m in pool:
        ids = chosen + [m["id"]]
        cs = hook_first(clips(moments, ids), replay)
        if short_length(cs, stinger_s) <= max_s:
            chosen = ids
    return sorted(chosen)


def _short_score(m):
    # the finish counts in a short only when it was a fight or a podium; the flag alone is not a highlight
    if m["kind"] == "finish":
        return 30.0
    return m["score"]


def singles(moments, n, max_s, kinds=None, replay=True) -> list[list[int]]:
    """One short per moment, for the n best moments (each with its replay), skipping ones that overlap one already
    taken."""
    pool = sorted([m for m in moments if m["kind"] in SHORT_KINDS - {"finish"} and not m.get("mandatory")
                   and (kinds is None or m["kind"] in kinds)], key=lambda m: m["score"], reverse=True)
    out, used = [], []
    for m in pool:
        if len(out) >= n:
            break
        a, b = m["segs"][0][0], m["segs"][-1][1]
        if any(a < ub and b > ua for ua, ub in used):
            continue
        if short_length(hook_first(clips(moments, [m["id"]]), replay)) > max_s:
            continue
        out.append([m["id"]])
        used.append((a, b))
    return out


def caption(m) -> str:
    lap = m.get("lap")
    if m["kind"] == "start":
        return "The start"
    if m["kind"] == "finish":
        return m["label"].split(" (")[0]
    return ("Lap %d  ·  %s" % (lap, m["label"])) if isinstance(lap, int) else m["label"]


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
