"""The editor: turns a race's moments into a story for the full video, the way a TV highlights package is cut.

A race's moments come in clusters: two cars battle, go side by side, one passes, the other passes back. Picked one at
a time they gave the same fight four times in a row with a stinger between each (Watkins Glen, Nov 2026: "a bunch
of random clips", "basically all just one battle"). So:

1. Storylines (storylines): moments that share a car (not counting one car's video's own car) within STORY_GAP_S of
   each other are one story. A story is played as one continuous clip (pick.clips joins its pieces across gaps up to
   STORY_JOIN_S), captioned once, by what the story was ("#18 and #22 trade the BMW M2 Racing (G87) lead").
   Everything in the opening OPENING_S of racing is the start's story.
2. Acts (acts): the start, the early laps, mid-race, the closing laps, the finish. The time left after the start
   and the finish is shared out over the three middle acts by ACT_SHARE (the end of a race matters most), so the
   video tells the whole race instead of its loudest few minutes. Time an act cannot use goes on to the next.
3. Variety (_value): each story's score falls for every story already picked with the same car (CAR_REPEAT), of the
   same kind (KIND_REPEAT, twice in the same act) or with the same words; a class not yet in the video counts more
   (NEW_CLASS), as does the lead (the director's own order: the lead, then the podium, then the rest). A story worth
   under VALUE_FLOOR of its own score is left out even when there is time: a shorter video beats a repeat.

Chapters (story.chapters) name the acts on screen.
"""
from __future__ import annotations

import re

STORY_GAP_S = 75.0          # moments sharing a car this close in time are one story
STORY_JOIN_S = 25.0         # a story's pieces this close are played as one continuous clip
STORY_MAX_S = 50.0          # a story longer than this keeps the part around its best moment
STORY_SPAN_S = 240.0        # a story never runs longer than this (a long fight is told again later, as a new one)
OPENING_S = 90.0            # racing after the green that belongs to the start
ACT_SHARE = (("early", 0.25), ("middle", 0.30), ("late", 0.45))
ACT_NAMES = {"start": "The start", "early": "Early laps", "middle": "Mid-race", "late": "The closing laps",
             "finish": "The finish"}
CAR_REPEAT = 0.55
KIND_REPEAT = 0.75
NEW_CLASS = 1.25
VALUE_FLOOR = 0.2           # a story worth less than this share of its own score after the repeats is left out: a
                            # shorter video beats the same fight a fourth time
STORY_EXTRA = 0.25          # each further moment in a story adds this share of its score
MIN_SCORE = 8.0
FIGHT = {"pass", "lost", "battle", "sidebyside", "drama"}


def _parts(m, target):
    p = set(m.get("cars") or [])
    if m.get("focus") is not None:
        p.add(m["focus"])
    if target is not None and target >= 0:
        p.discard(target)
    return p


def clean(label) -> str:
    """A moment's label as a caption: no battle length ("(2 min)": the clip does not show two minutes), no
    "back and forth" (the story says it)."""
    label = re.sub(r"\s*\((\d+ s|\d+ min)\)", "", label or "")
    label = re.sub(r" for \d+ s$", "", label)        # "side by side for P6 for 4 s"
    # a crash of nine cars: the first three and how many more
    m = re.search(r"((?:#\w+, ){3,}#\w+ and #\w+)", label)
    if m:
        nums = re.findall(r"#\w+", m.group(1))
        if len(nums) > 4:
            label = label.replace(m.group(1), "%s and %d more" % (", ".join(nums[:3]), len(nums) - 3))
    return label.replace(" - back and forth", "").strip()


def _num_of(label, n):
    found = re.findall(r"#\w+", label or "")
    return found[n] if len(found) > n else ""


def storylines(moments, result) -> list[dict]:
    """Groups the moments into stories; writes each moment's "story" (an id) and returns the stories:
    {"id", "ids", "t0", "t1", "best", "score", "label", "parts", "kind", "lead"}."""
    target = (result or {}).get("target")
    green = (result or {}).get("green")
    stories: list[dict] = []
    order = sorted(moments, key=lambda m: m["t"])
    for m in order:
        if m["kind"] == "start":
            # the start and everything on the opening lap is one story, played on from the start
            st = _new(m, "opening", set())
            st["opening"] = True
            st["mandatory"] = True
            stories.append(st)
            m["story"] = "opening"
            continue
        if m.get("mandatory"):
            m["story"] = "m%s" % m["id"]
            stories.append(_new(m, "m%s" % m["id"], set()))
            continue
        if green is not None and m["t"] - green < OPENING_S and m["kind"] != "fastest":
            st = next((s for s in stories if s.get("opening")), None)
            if st is not None and st.get("mandatory"):
                st["ids"].append(m["id"])
                st["t1"] = max(st["t1"], m["t"])
                m["story"] = st["id"]
                continue
            if st is None:
                st = _new(m, "opening", set())
                st["opening"] = True
                stories.append(st)
            else:
                _join(st, m)
            m["story"] = st["id"]
            continue
        parts = _parts(m, target)
        st = None
        if parts:
            for s in reversed(stories):
                if s.get("mandatory") or s.get("opening"):
                    continue
                if m["t"] - s["t1"] > STORY_GAP_S or m["t"] - s["t0"] > STORY_SPAN_S:
                    continue
                # the story's own cars (its first moment's): sharing one car with whatever else it touched
                # would chain half the field into one "story"
                if s["parts"] & parts and (m["kind"] in FIGHT or s["kind"] == m["kind"]):
                    st = s
                    break
        if st is None:
            st = _new(m, "s%d" % len(stories), parts)
            stories.append(st)
        else:
            _join(st, m)
        m["story"] = st["id"]
    for s in stories:
        s["label"] = _story_label(s, moments)
        s["score"] = _story_score(s, moments)
    by = {s["id"]: s for s in stories}
    for m in moments:
        m["storyLabel"] = by[m["story"]]["label"]
    return stories


def _new(m, sid, parts):
    return {"id": sid, "ids": [m["id"]], "t0": m["t"], "t1": m["t"], "best": m, "parts": set(parts),
            "kind": m["kind"], "mandatory": bool(m.get("mandatory")), "cls": m.get("classPos")}


def _join(s, m):
    s["ids"].append(m["id"])
    s["t1"] = max(s["t1"], m["t"])
    s["t0"] = min(s["t0"], m["t"])
    if m["score"] > s["best"]["score"]:
        s["best"] = m
        s["kind"] = m["kind"]


def _story_score(s, moments):
    ms = [m for m in moments if m["id"] in set(s["ids"])]
    best = s["best"]["score"]
    rest = sorted((m["score"] for m in ms if m is not s["best"]), reverse=True)
    return min(best * 1.5, best + STORY_EXTRA * sum(rest))


def _story_label(s, moments):
    """What the story was, in one line: a pass that was passed back is a fight, not two passes."""
    if s.get("opening"):
        return "The start"
    ms = sorted((m for m in moments if m["id"] in set(s["ids"])), key=lambda m: m["t"])
    passes = [m for m in ms if m["kind"] in ("pass", "lost")]
    label = clean(s["best"]["label"])
    if len(passes) >= 2 and len({frozenset(_parts(m, None)) for m in passes}) == 1:
        a, b = _num_of(passes[0]["label"], 0), _num_of(passes[0]["label"], 1)
        for_what = ""
        lead = re.search(r"takes the (.*?)lead", passes[-1]["label"])
        if lead:
            for_what = " the %slead" % lead.group(1)
        else:
            pm = re.search(r" for (P\d+(?: in .*)?)$", clean(passes[-1]["label"]))
            for_what = " places for %s" % pm.group(1) if pm else " places"
        if a and b:
            return ("Last lap: " if any(m.get("lastLap") for m in passes) else "") + \
                "%s and %s trade%s" % (a, b, for_what)
    return label


def acts(result, moments):
    """The act each time falls in: (name, t0, t1) from the green to the first class's flag."""
    green = (result or {}).get("green")
    fin = [m["t"] for m in moments if m["kind"] == "finish"]
    end = min(fin) if fin else (result or {}).get("finish")
    if green is None or end is None or end <= green + OPENING_S:
        return []
    a0 = green + OPENING_S
    third = (end - a0) / 3.0
    return [("start", green - 60, a0), ("early", a0, a0 + third), ("middle", a0 + third, a0 + 2 * third),
            ("late", a0 + 2 * third, end - 1), ("finish", end - 1, 1e12)]


def act_of(spans, t):
    for name, a, b in spans:
        if a <= t < b:
            return name
    return None


def _value(s, picked, moments_by_id, act, cls_of):
    v = s["score"]
    best = s["best"]
    for p in picked:
        if p is s:
            continue
        shared = s["parts"] & p["parts"]
        if shared:
            v *= CAR_REPEAT ** len(shared)
        if p["kind"] == s["kind"]:
            v *= KIND_REPEAT * (KIND_REPEAT if p.get("act") == act else 1.0)
        if clean(p["best"]["label"]) == clean(best["label"]):
            v *= 0.5                    # "Battle for the class lead" twice reads as the same clip twice
    cls = cls_of(best)
    if cls is not None and all(cls_of(p["best"]) != cls for p in picked):
        v *= NEW_CLASS
    if best.get("classPos") == 1:
        v *= 1.15
    return v


def plan(moments, result, target_s, stinger_s=0.0, kinds=None, length=None) -> list[int]:
    """The moment ids for the full video: every story whole (or its best STORY_MAX_S), shared over the acts."""
    from .pick import length as clip_length
    length = length or clip_length
    stories = storylines(moments, result)
    by_id = {m["id"]: m for m in moments}
    spans = acts(result, moments)

    def cls_of(m):
        return m.get("cls") or None

    def allowed(m):
        return kinds is None or m["kind"] in kinds or m.get("mandatory")

    def ids_of(s):
        ms = [by_id[i] for i in s["ids"] if allowed(by_id[i])]
        if not ms:
            return []
        if s.get("opening"):
            # the start's story: the start (always in) and its two best moments after it, no more
            rest = [m for m in ms if m["kind"] != "start"]
            return [m["id"] for m in sorted(rest, key=lambda m: m["score"], reverse=True)[:2]]
        key = float(s["best"].get("key", s["best"]["t"]))
        keep = [m for m in ms if key - (STORY_MAX_S - 15) <= m["t"] <= key + 15] or [s["best"]]
        return [m["id"] for m in keep]

    chosen = [m["id"] for m in moments if m.get("mandatory") and allowed(m)]
    picked: list[dict] = []
    for s in stories:
        if s.get("mandatory"):
            s["act"] = act_of(spans, s["t0"])
            picked.append(s)
    opening = next((s for s in stories if s.get("opening")), None)
    base = length(moments, chosen, stinger_s)
    free = max(0.0, target_s - base)
    pool = [s for s in stories if (not s.get("mandatory") or s is opening) and s["score"] >= MIN_SCORE
            and ids_of(s)]
    for s in pool:
        s["act"] = "early" if s.get("opening") else act_of(spans, s["t0"]) if spans else None
    carry = 0.0
    for act, share in (ACT_SHARE if spans else (("", 1.0),)):
        budget = free * share + carry
        start_len = length(moments, chosen, stinger_s)
        cand = [s for s in pool if (not spans or s["act"] == act) and not s.get("_done")]
        while cand:
            cand.sort(key=lambda s: _value(s, picked, by_id, s.get("act"), cls_of), reverse=True)
            took = False
            for s in cand:
                if _value(s, picked, by_id, s.get("act"), cls_of) < VALUE_FLOOR * s["score"]:
                    continue
                new = length(moments, chosen + ids_of(s), stinger_s)
                if new - start_len <= budget and new <= target_s:
                    chosen += ids_of(s)
                    picked.append(s)
                    s["_done"] = True
                    cand.remove(s)
                    took = True
                    break
            if not took:
                break
        carry = max(0.0, budget - (length(moments, chosen, stinger_s) - start_len))
    # anything left: the best of what is left, anywhere
    rest = [s for s in pool if not s.get("_done")]
    while rest:
        rest.sort(key=lambda s: _value(s, picked, by_id, s.get("act"), cls_of), reverse=True)
        took = False
        for s in rest:
            if _value(s, picked, by_id, s.get("act"), cls_of) < VALUE_FLOOR * s["score"]:
                continue
            if length(moments, chosen + ids_of(s), stinger_s) <= target_s:
                chosen += ids_of(s)
                picked.append(s)
                rest.remove(s)
                took = True
                break
        if not took:
            break
    for s in pool:
        s.pop("_done", None)
    return sorted(set(chosen))
