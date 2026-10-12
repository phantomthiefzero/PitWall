"""Delta encoding for the two high-rate message types (state, cars).

The bridge sends a full "keyframe" every KEYFRAME_S seconds and on every
reconnect; in between it sends only the keys that changed. Floats are rounded
first so jitter in the 6th decimal does not count as a change.

Wire format:
    full:   {"type":"state", "full":true,  ...entire message}
    delta:  {"type":"state", "full":false, ...only changed keys, nested}
For "cars", the list is keyed by car index so a delta can address one car:
    {"type":"cars","full":false,"t":..,"cars":{"12":{"pct":0.4413}}}
A full cars message also uses the dict form. Client-side merge is a deep
merge where lists replace and dicts recurse (see client/index.html: merge()).
"""
from __future__ import annotations

ROUND = 5
KEYFRAME_S = 5.0


def rounded(v):
    if isinstance(v, float):
        return round(v, ROUND)
    if isinstance(v, dict):
        return {k: rounded(x) for k, x in v.items()}
    if isinstance(v, list):
        return [rounded(x) for x in v]
    return v


def diff(old, new):
    """Return the subset of `new` that differs from `old` (dict-recursive).
    Lists are compared whole. Returns None when nothing changed."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return new if old != new else None
    out = {}
    for k, nv in new.items():
        if k not in old:
            out[k] = nv
            continue
        ov = old[k]
        if isinstance(ov, dict) and isinstance(nv, dict):
            d = diff(ov, nv)
            if d is not None and d != {}:
                out[k] = d
        elif ov != nv:
            out[k] = nv
    for k in old:
        if k not in new:
            out[k] = None
    return out or None


class DeltaEncoder:
    def __init__(self, keyframe_s: float = KEYFRAME_S):
        self.keyframe_s = keyframe_s
        self._last: dict[str, dict] = {}
        self._last_key_t: dict[str, float] = {}

    def reset(self):
        self._last.clear()
        self._last_key_t.clear()

    def encode(self, msg: dict, now: float) -> dict | None:
        """msg must already be in wire shape (cars as dict). Returns the message
        to send, or None if it is a delta with no changes."""
        t = msg["type"]
        msg = rounded(msg)
        last = self._last.get(t)
        due = now - self._last_key_t.get(t, -1e9) >= self.keyframe_s
        if last is None or due:
            self._last[t] = msg
            self._last_key_t[t] = now
            return {"type": t, "full": True, **{k: v for k, v in msg.items() if k != "type"}}
        d = diff(last, msg)
        self._last[t] = msg
        if not d:
            return None
        d.pop("type", None)
        return {"type": t, "full": False, **d}


def cars_to_wire(cars_msg: dict) -> dict:
    return {**cars_msg, "cars": {str(c["idx"]): c for c in cars_msg["cars"]}}
