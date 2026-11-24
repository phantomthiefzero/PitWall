"""Camera work for Highlights, the Broadcast director's way (pitwall/director.py).

What each camera group is comes from the director itself: camera_role() and the producer's word on each group
("Your cameras" in the producer, kept per track in PitWall's obs.json), camera packs included, and the cameras that
stand at one place (spots: a pack's "Turn 5", the start / finish line) used in place of a trackside shot as the car
comes to them. Groups the director never uses (Scenic) are never used here either.

A clip is a story told in shots, in an order, never one group held from start to end:

    lead-up     wide first, then closer (the director's sequences), SHOT_S a shot
    the key     the shot that shows the moment itself starts KEY_BEFORE_S before it and holds KEY_AFTER_S past it:
                Far Chase on the car behind for a pass or a battle (the whole fight ahead of it), a camera by the
                track (or the corner's own camera) on the car in trouble for a spin, an off or a wreck nearby
    after       Rear Chase on the car in front (it looks back: the car just passed, the wreck just missed), then wider

and, for the best moments, ANGLES: the key seconds again from other cameras (onboard with the chaser, from above, by
the track), recorded on their own and played after the clip as replays, the last at half speed.

Battles use the director's BATTLE_SHOTS, with "back" and "front" for the car behind and the car in front: before a
pass the passer is behind, after it in front. A shot is never the group just used, and a kind is not used twice in a
row unless the track has nothing else.
"""
from __future__ import annotations

import bisect
import random

from pitwall.director import BATTLE_SHOTS, SEQUENCES, STORY_SEQ, Director

SHOT_S = 5.0                # a lead-up or after shot
SHOT_MIN_S = 2.5            # no shot shorter than this (a shorter remainder joins its neighbour)
KEY_BEFORE_S = 2.5
KEY_AFTER_S = 2.5
ANGLE_AFTER_S = 3.0
ANGLE_BEFORE_S = {"spin": 5.0, "off": 4.0, "drama": 5.0, "towed": 6.0, "finish": 5.0}
ANGLE_BEFORE_DEFAULT = 4.0
ANGLE_SPEEDS = (1.0, 0.5, 0.5)          # replay 1 at full speed, the next ones slowed down
ANGLE_KINDS = ("pass", "lost", "battle", "spin", "off", "towed", "drama", "crash", "finish",
               "pack", "multiclass", "sidebyside", "train", "restart")
OVERHEAD_S = 7.0            # per recording: the seek, the camera settling, OBS starting and stopping

# a kind the track has no camera for: the nearest kind it has
FALLBACK = {"aerial": ["far", "trackside"], "onboard": ["chase", "far"], "rear": ["chase", "far"],
            "gyro": ["far", "chase"], "pit": ["trackside", "far"], "trackside": ["far", "chase"],
            "chase": ["far", "trackside"], "far": ["chase", "trackside"], "detail": ["chase", "far"]}

# (kind, who) stories. who: "T" (the car the video is about), "other" (the other car in the story), "back" / "front"
# (the car behind / in front at that moment of a fight).
LEAD = {
    "battle": BATTLE_SHOTS,
    "pass": BATTLE_SHOTS, "lost": BATTLE_SHOTS, "traffic": BATTLE_SHOTS,
    "spin": [("far", "T"), ("chase", "T"), ("trackside", "T")],
    "off": [("far", "T"), ("chase", "T"), ("trackside", "T")],
    "towed": [("far", "T"), ("trackside", "T")],
    "drama": [("far", "T"), ("aerial", "T"), ("chase", "T")],
    "crash": [("far", "T"), ("aerial", "T"), ("chase", "T")],
    "pit": [("far", "T"), ("trackside", "T")],
    "start": [(k, "T") for k in STORY_SEQ["start"]],
    "finish": [(k, "T") for k in STORY_SEQ["finish"]],
    "fastest": [(k, "T") for k in STORY_SEQ["fastlap"] + ["onboard"]],
    # the sights (spectacle.py). T: the moment's own car (the front of a pack, the car slicing through)
    "pack": [("aerial", "T"), ("far", "other")],         # from above, then from behind the pack looking up it
    "multiclass": [("aerial", "T"), ("far", "other")],
    "sidebyside": [("far", "other"), ("chase", "T")],
    "train": [("far", "T"), ("chase", "T")],
    "charge": BATTLE_SHOTS,
    "restart": [(k, "T") for k in STORY_SEQ["start"]],
    "pitrush": [("aerial", "T"), ("far", "T")],
    "pitlead": [("far", "other"), ("chase", "T")],       # the leader turning in, the car that inherits it
    "out": [("far", "T"), ("aerial", "T")],
}
KEY = {
    "battle": ("far", "back"), "pass": ("far", "back"), "lost": ("far", "back"), "traffic": ("far", "back"),
    "spin": ("trackside", "T"), "off": ("trackside", "T"), "towed": ("trackside", "T"),
    "drama": ("trackside", "other"),            # the wreck itself, on the car it happened to
    "crash": ("trackside", "T"),                # whole race: the car that began it, by the track
    "pit": ("pit", "T"), "start": ("aerial", "T"), "finish": ("trackside", "T"), "fastest": ("trackside", "T"),
    "pack": ("rear", "T"),                      # Rear Chase on the front car: the whole pack behind it
    "multiclass": ("trackside", "T"), "sidebyside": ("aerial", "T"), "train": ("far", "T"), "charge": ("far", "T"),
    "restart": ("aerial", "T"), "pitrush": ("pit", "T"), "pitlead": ("pit", "other"), "out": ("trackside", "T"),
}
AFTER = {
    "battle": [("rear", "front"), ("chase", "back"), ("aerial", "front")],
    "pass": [("rear", "front"), ("far", "front"), ("aerial", "front")],
    "lost": [("rear", "front"), ("far", "back")],
    "traffic": [("rear", "front"), ("far", "T")],
    "spin": [("aerial", "T"), ("far", "T")],
    "off": [("aerial", "T"), ("far", "T")],
    "towed": [("aerial", "T")],
    "drama": [("rear", "T"), ("far", "T")],      # looking back from in front of T: the wreck just missed
    "crash": [("aerial", "T"), ("trackside", "other"), ("far", "T")],
    "pit": [("pit", "T"), ("far", "T")],
    "start": [(k, "T") for k in ("far", "rear", "trackside", "aerial")],
    "finish": [(k, "T") for k in STORY_SEQ["after"]],
    "fastest": [("far", "T"), ("aerial", "T")],
    "pack": [("aerial", "T"), ("trackside", "T")],
    "multiclass": [("rear", "T"), ("aerial", "T")],
    "sidebyside": [("rear", "T"), ("far", "T")],
    "train": [("rear", "T"), ("aerial", "T")],          # looking back at the cars just gone by
    "charge": [("rear", "T"), ("aerial", "T")],
    "restart": [("rear", "T"), ("trackside", "T"), ("far", "T")],
    "pitrush": [("pit", "other"), ("far", "T")],
    "pitlead": [("far", "T"), ("aerial", "T")],
    "out": [("aerial", "T")],
}
ANGLES = {
    "pass": [("onboard", "back"), ("rear", "front"), ("aerial", "back"), ("trackside", "back")],
    "lost": [("onboard", "back"), ("rear", "front"), ("aerial", "front"), ("trackside", "front")],
    "battle": [("onboard", "back"), ("rear", "front"), ("aerial", "back")],
    "spin": [("onboard", "T"), ("aerial", "T"), ("trackside", "T"), ("chase", "T")],
    "off": [("onboard", "T"), ("aerial", "T"), ("trackside", "T")],
    "towed": [("aerial", "T"), ("trackside", "T")],
    "drama": [("onboard", "T"), ("trackside", "other"), ("aerial", "other")],   # T's view of the near miss first
    "crash": [("onboard", "other"), ("aerial", "T"), ("trackside", "T")],       # from inside a car caught up in it
    "finish": [("onboard", "T"), ("aerial", "T")],
    "pack": [("onboard", "other"), ("aerial", "T"), ("rear", "T")],     # from inside the pack
    "multiclass": [("onboard", "T"), ("aerial", "T"), ("trackside", "T")],
    "sidebyside": [("onboard", "T"), ("onboard", "other"), ("aerial", "T")],
    "train": [("onboard", "T"), ("rear", "T"), ("aerial", "T")],
    "restart": [("onboard", "T"), ("aerial", "T")],
}


def groups_from_info(camera_info) -> list[dict]:
    """CameraInfo/Groups in the shape the director reads (pitwall/norm/session.py _camera_groups)."""
    out = []
    for g in ((camera_info or {}).get("Groups") or []):
        if isinstance(g, dict) and g.get("GroupNum") is not None:
            out.append({"num": g["GroupNum"], "name": g.get("GroupName"), "scenic": bool(g.get("IsScenic")),
                        "cameras": [{"num": c.get("CameraNum"), "name": c.get("CameraName")}
                                    for c in (g.get("Cameras") or []) if isinstance(c, dict)]})
    return out


class Cameras:
    """The track's camera groups as the Broadcast director reads them."""

    def __init__(self, camera_info, setup=None, lap_s=None):
        self.groups = groups_from_info(camera_info)
        self._d = Director(lambda *a: {"ok": True}, log=lambda *a: None)
        self._d.set_cameras(setup or {})
        self.kinds = self._d._groups({"cameras": self.groups})
        self.spots = list(self._d._spots)
        self.lap_s = lap_s if lap_s and 20 < lap_s < 900 else 90.0

    def names(self) -> list[dict]:
        """Every group the director would use, for the page's camera menu."""
        return [{"num": g["num"], "name": g["name"]} for g in self.groups
                if not g.get("scenic") and any(g["num"] == x["num"] for k in self.kinds.values() for x in k)
                or any(g["num"] == sp["num"] for sp in self.spots)]

    def pick(self, kind, rng, avoid=None, avoid_kind=None, pct=None, near=False) -> dict | None:
        """A group of this kind (or the nearest kind the track has): never `avoid` (the group on air). A trackside
        shot of a car coming to a corner's own camera is that camera (the director's _do_cut)."""
        for k in [kind] + FALLBACK.get(kind, []):
            if k == "trackside" and pct is not None and self.spots:
                self._d._last_group = avoid
                sp = self._d._spot_for({"pct": pct, "bestLapS": self.lap_s}, near=near)
                if sp is not None:
                    return {**sp, "kind": "spot"}
            opts = [g for g in self.kinds.get(k, []) if g["num"] != avoid]
            if k == avoid_kind and k != kind:
                continue
            if opts:
                return {**rng.choice(opts), "kind": k}
        g = next((x for x in self.kinds.get(kind, [])), None)        # the one on air is all there is
        return {**g, "kind": kind} if g else None


def _who(who, m, at_t, target, number):
    """The car number a shot is on. Before the moment's key the other car is in front when otherAheadBefore; a pass
    or a lost place swaps them at the key."""
    other = (m.get("cars") or [None])[0]
    if who == "T" or other is None:
        return number(target)
    if who == "other":
        return number(other)
    ahead = bool(m.get("otherAheadBefore"))
    if m["kind"] in ("pass", "lost", "traffic") and at_t >= m["t"]:
        ahead = not ahead
    front, back = (other, target) if ahead else (target, other)
    return number(front if who == "front" else back)


def _lay(a, b, step=SHOT_S):
    """Shot start times filling [a, b) in shots of about `step`, none under SHOT_MIN_S."""
    if b - a < SHOT_MIN_S:
        return [a] if b > a else []
    n = max(1, int(round((b - a) / step)))
    return [a + (b - a) * i / n for i in range(n)]


def key_time(m) -> float:
    return float(m.get("key", m["t"]))


def plan(clip, m, cams, target, number, pct_at=lambda t: None, rng=None, pref="auto") -> list[dict]:
    """The shots of one clip: [{"t": tape seconds to cut at, "group", "groupName", "kind", "car"}], the first at the
    clip's start. m: the clip's best moment (its story). number(idx) -> the car number to send iRacing."""
    rng = rng or random.Random(m.get("id", 0))
    a, b = float(clip["a"]), float(clip["b"])
    if pref and pref != "auto":
        g = next((x for x in cams.groups if str(x["name"]).lower() == str(pref).lower()), None)
        if g is not None:
            return [{"t": a, "group": g["num"], "groupName": g["name"], "kind": "set", "car": number(target)}]
    kind = m["kind"] if m["kind"] in KEY else "battle" if m.get("cars") else "fastest"
    key = key_time(m)
    if m.get("keys"):                           # a montage (a charge): each clip builds to its own pass
        key = min(m["keys"], key=lambda k: abs(float(k) - (a + b) / 2))
    key = min(max(key, a), b)
    k0, k1 = max(a, key - KEY_BEFORE_S), min(b, key + KEY_AFTER_S)
    if k0 - a < SHOT_MIN_S:
        k0 = a
    if b - k1 < SHOT_MIN_S:
        k1 = b
    beats = []                                  # (start, (kind, who), near)
    lead = LEAD.get(kind) or [(k, "T") for k in SEQUENCES["cinematic"]]
    for i, t in enumerate(_lay(a, k0)):
        beats.append((t, lead[i % len(lead)], False))
    # the shot before the key never takes the key's kind (the key would have to fall back to a lesser camera)
    if beats and beats[-1][1][0] == KEY[kind][0]:
        prev = beats[-2][1][0] if len(beats) > 1 else None
        alt = next((x for x in lead if x[0] not in (KEY[kind][0], prev)), None)
        if alt:
            beats[-1] = (beats[-1][0], alt, False)
    beats.append((k0, KEY[kind], kind in ("spin", "off", "towed", "drama")))
    after = AFTER.get(kind) or [("far", "T")]
    for i, t in enumerate(_lay(k1, b)):
        beats.append((t, after[i % len(after)], False))
    shots, last_g, last_k = [], None, None
    for t, (want, who), near in beats:
        car = _who(who, m, t, target, number)
        pct = m.get("at") if near or who != "T" else pct_at(t)
        g = cams.pick(want, rng, avoid=last_g, avoid_kind=last_k if want != last_k else None, pct=pct, near=near)
        if g is None:
            continue
        if shots and g["num"] == last_g and car == shots[-1]["car"]:
            continue                            # the same picture: hold it
        shots.append({"t": round(t, 2), "group": g["num"], "groupName": g.get("name"), "kind": g["kind"], "car": car})
        last_g, last_k = g["num"], g["kind"]
    if shots:
        shots[0]["t"] = round(a, 2)
    return shots


def angles(m, cams, target, number, key_groups=(), n=2, rng=None) -> list[dict]:
    """The key seconds of moment m again from up to n other cameras: [{"a", "b", "speed", "shots": [one shot]}]."""
    if m["kind"] not in ANGLE_KINDS or n <= 0:
        return []
    rng = rng or random.Random(1000 + m.get("id", 0))
    key = key_time(m)
    if m["kind"] == "drama" and m.get("passT"):
        key = (key + float(m["passT"])) / 2.0     # the wreck and T going by it in one window
    a = key - ANGLE_BEFORE_S.get(m["kind"], ANGLE_BEFORE_DEFAULT)
    b = (float(m["passT"]) + 2.0) if m["kind"] == "drama" and m.get("passT") else key + ANGLE_AFTER_S
    b = max(b, key + ANGLE_AFTER_S)
    used, out = set(key_groups), []
    for want, who in ANGLES.get(m["kind"], []):
        if len(out) >= n:
            break
        car = _who(who, dict(m, t=1e18), a, target, number)   # the roles as they were going into the moment
        g = cams.pick(want, rng, pct=m.get("at"), near=want == "trackside")
        if g is None or g["num"] in used:
            continue
        used.add(g["num"])
        out.append({"a": round(a, 2), "b": round(b, 2), "speed": ANGLE_SPEEDS[min(len(out), len(ANGLE_SPEEDS) - 1)],
                    "shots": [{"t": round(a, 2), "group": g["num"], "groupName": g.get("name"), "kind": g["kind"],
                               "car": car}]})
    return out


def key_groups(shots, m) -> set:
    """The groups on air over the moment itself (an angle replay should show something else)."""
    k = key_time(m)
    on = [s for s in shots if s["t"] <= k] or shots[:1]
    return {on[-1]["group"]} if on else set()


def path_lookup(path):
    """pct_at(t) from T's (t, lap fraction) samples."""
    ts = [p[0] for p in path or []]

    def pct_at(t):
        if not ts:
            return None
        i = bisect.bisect_left(ts, t)
        i = min(max(i, 0), len(ts) - 1)
        return path[i][1]
    return pct_at


def record_s(jobs) -> float:
    """Wall seconds to record these jobs (each plays at 1x, plus the seek and OBS starting and stopping)."""
    return sum(float(j["b"]) - float(j["a"]) + OVERHEAD_S for j in jobs)
