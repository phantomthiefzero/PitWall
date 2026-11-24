"""Choosing moments to fill a target length, and turning them into clips to record.

auto_pick: the start and the finish always (mandatory), then the best-scoring moments that still fit. A moment
close in time to one of the same kind already chosen counts less (SPREAD_S, SPREAD_FACTOR), so a long race is not
one stint's battle five times. Overlapping moments cost only the extra seconds they add.

clips: the chosen moments' pieces in race order; pieces closer than MERGE_GAP_S become one clip (no stinger in
the middle of a fight). Each clip is captioned by its best moment.
"""
from __future__ import annotations

import re

SPREAD_S = 180.0
SPREAD_FACTOR = 0.75
MERGE_GAP_S = 4.0
MIN_SCORE = 8.0
STORY_JOIN_S = 25.0         # pieces of one story (edit.storylines) this close play as one clip, no stinger between


def clips(moments, chosen_ids, own_labels=False) -> list[dict]:
    """own_labels: caption each clip by its moment, not its story (a short shows the moment, not the whole fight)."""
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
        same_story = last is not None and m.get("story") is not None and \
            m.get("story") in {x.get("story") for x in last["_ms"]}
        if last is not None and last["session"] == s and (a - last["b"] <= MERGE_GAP_S or
                                                          (same_story and a - last["b"] <= STORY_JOIN_S)):
            last["_ms"].append(m)
            last["b"] = max(last["b"], b)
            if m["id"] not in last["moments"]:
                last["moments"].append(m["id"])
            if m["score"] > last["_best"]["score"] and not last["_best"].get("mandatory"):
                last["_best"] = m
            if m.get("mandatory") and not last["_best"].get("mandatory"):
                last["_best"] = m
            continue
        out.append({"session": s, "a": a, "b": b, "moments": [m["id"]], "_best": m, "_ms": [m]})
    for n, c in enumerate(out):
        best = c.pop("_best")
        c.pop("_ms", None)
        c["n"] = n
        c["a"], c["b"] = round(c["a"], 2), round(c["b"], 2)
        c["kind"] = best["kind"]
        c["label"] = best["label"]
        c["lap"] = best.get("lap")
        c["caption"] = caption(best, own_labels)
        c["cars"] = best.get("cars") or []
        c["score"] = best["score"]
        c["key"] = best.get("key", best["t"])
        c["best"] = best["id"]
        c["driver"] = best.get("driver")
        c["replay"] = replay_window(best)
    return out


REPLAY_KINDS = {"pass": (-2.5, 1.0), "lost": (-2.5, 1.0), "spin": (-1.5, 3.0), "off": (-1.0, 2.5),
                "drama": (-1.5, 2.5), "crash": (-1.5, 3.5), "sidebyside": (-1.0, 2.0), "train": (-1.5, 2.0)}
REPLAY_SPEED = 0.5


def replay_window(m):
    """The key seconds of a moment, for a slow-motion replay: around the swap of a pass, the start of a spin."""
    w = REPLAY_KINDS.get(m["kind"])
    if w is None:
        return None
    base = float(m["key"]) if m.get("spectacle") and m.get("key") is not None else m["t"]
    return {"a": round(base + w[0], 2), "b": round(base + w[1], 2)}


def replay_s(clip, speeds=(REPLAY_SPEED,)) -> float:
    """Seconds a clip's replays add: its replay window once at each speed (a single moment's short shows it from
    two cameras, at full speed and slowed down; a best-of only slowed down, to keep room for more moments)."""
    r = clip.get("replay")
    return sum((r["b"] - r["a"]) / s for s in speeds) if r else 0.0


SINGLE_SPEEDS = (1.0, REPLAY_SPEED)
BEST_SPEEDS = (REPLAY_SPEED,)
HOOK_LEAD_S = 3.0           # a short starts this long before its best moment (story.HOOK_LEAD_S)
SHORT_BEFORE_S = 5.0        # after the hook, a short's clips are cut to the seconds around their moment
SHORT_AFTER_S = 3.0


def short_window(c, k):
    """The part of clip c a short plays (k: its place in the short, 0 = the hook)."""
    key = float(c.get("key", c["a"]))
    if k == 0:
        return max(c["a"], key - HOOK_LEAD_S), c["b"]
    return max(c["a"], key - SHORT_BEFORE_S), min(c["b"], key + SHORT_AFTER_S)


# ---------------------------------------------------------------------------- vertical shorts
SHORT_KINDS = {"pass", "lost", "battle", "spin", "off", "drama", "crash", "towed", "fastest", "finish",
               "pack", "multiclass", "sidebyside", "train", "restart", "pitlead", "out"}


def hook_first(cs, replay=True) -> list[dict]:
    """A short opens on its best clip (the hook), with its slow-motion replay; the rest follow in race order, cut
    straight (a replay only on the hook)."""
    if not cs:
        return []
    best = max(cs, key=lambda c: 30.0 if c["kind"] == "finish" else c["score"])
    rest = [dict(c, replay=None) for c in cs if c is not best]
    return [dict(best, replay=best.get("replay") if replay else None)] + rest


def short_length(cs, stinger_s=0.0, speeds=BEST_SPEEDS) -> float:
    """How long a short of these clips (hook first) runs: the hook from HOOK_LEAD_S before its moment, its replays."""
    total = 0.0
    for k, c in enumerate(cs):
        a, b = short_window(c, k)
        total += b - a + (replay_s(c, speeds) if k == 0 else 0.0)
    return total + max(0, len(cs) - 1) * stinger_s


def best_of(moments, max_s, stinger_s=0.0, kinds=None, replay=True) -> list[int]:
    """The moments for one best-of short: the highest scores that fit in max_s, hook first. The start is left out
    (a short starts in the action); the finish only if it scores among the best (its score is not mandatory here)."""
    allowed = [m for m in moments if m["kind"] in SHORT_KINDS and (kinds is None or m["kind"] in kinds
                                                                     or m["kind"] == "finish")]
    pool = sorted(allowed, key=lambda m: _short_score(m), reverse=True)
    chosen, stories = [], set()
    for m in pool:
        if m.get("story") is not None and m["story"] in stories:
            continue                    # one moment a story: a best-of is many stories, not one fight
        ids = chosen + [m["id"]]
        cs = hook_first(clips(moments, ids), replay)
        if short_length(cs, stinger_s) <= max_s:
            chosen = ids
            stories.add(m.get("story"))
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
    out, used, stories = [], [], set()
    for m in pool:
        if len(out) >= n:
            break
        a, b = m["segs"][0][0], m["segs"][-1][1]
        if any(a < ub and b > ua for ua, ub in used):
            continue
        if m.get("story") is not None and m["story"] in stories:
            continue                    # one short a story: not the same fight's pass and its pass back
        stories.add(m.get("story"))
        if short_length(hook_first(clips(moments, [m["id"]]), replay), speeds=SINGLE_SPEEDS) > max_s:
            continue
        out.append([m["id"]])
        used.append((a, b))
    return out


def caption(m, own=False) -> str:
    """What a viewer reads: the story when the editor has named one (a fight, not its single pass), with the lap;
    never a battle's length ("(2 min)": the clip does not show two minutes)."""
    lap = m.get("lap")
    if m["kind"] == "start" or (not own and m.get("storyLabel") == "The start"):
        return "The start"
    if m["kind"] == "finish":
        return m["label"]
    from .edit import clean
    label = clean(m["label"] if own else (m.get("storyLabel") or m["label"]))
    return ("Lap %d  ·  %s" % (lap, label)) if isinstance(lap, int) else label


def length(moments, chosen_ids, stinger_s=0.0) -> float:
    cs = clips(moments, chosen_ids)
    return sum(c["b"] - c["a"] for c in cs) + max(0, len(cs) - 1) * stinger_s


def auto_pick(moments, target_s, stinger_s=0.0, kinds=None, result=None) -> list[int]:
    """Ids of the moments to use. kinds: the kinds allowed (None = all). With the race's result, the editor
    (edit.plan) picks whole stories shared over the race's acts; without, the best moments that fit."""
    if result is not None:
        from .edit import plan
        return plan(moments, result, target_s, stinger_s, kinds)
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
