"""Build a track outline from the player's own lap.

iRacing does not expose track geometry. What it does expose every frame:
    VelocityX  forward speed in the car's frame (m/s)
    VelocityY  leftward speed in the car's frame (m/s)
    YawNorth   heading clockwise from north (rad): forward = (sin, cos) in (east, north)
    LapDistPct 0..1 distance around the lap

Integrating (VelocityX, VelocityY) rotated by YawNorth gives a world-frame
path. It drifts a little over a lap, so the loop is force-closed by spreading
the start/finish gap linearly along the lap (standard dead-reckoning fix).
The result is resampled onto a fixed grid of LapDistPct so any car can be
placed by its own CarIdxLapDistPct with one lookup.

Everything here is DERIVED. Validation lives in tests/test_trackmap.py, which
rebuilds the FakeSource outline from a simulated lap and measures the error.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field

DEFAULT_BINS = 720


@dataclass
class LapSample:
    t: float
    pct: float
    vx: float
    vy: float
    yaw: float


@dataclass
class TrackMap:
    track_id: int | str
    config: str
    bins: int
    points: list[list[float]]           # normalized to a unit box, index = bin
    start_finish_index: int = 0
    lap_length_m: float | None = None
    closure_error_m: float | None = None  # how far the raw integration missed
    source: str = "velocity_integration"
    meta: dict = field(default_factory=dict)

    def to_message(self) -> dict:
        return {"type": "map", "trackId": self.track_id, "config": self.config, "bins": self.bins,
                "points": self.points, "startFinishIndex": self.start_finish_index,
                "lapLengthM": self.lap_length_m, "closureErrorM": self.closure_error_m,
                "source": self.source, "meta": self.meta}

    @staticmethod
    def from_message(m: dict) -> "TrackMap":
        return TrackMap(track_id=m["trackId"], config=m["config"], bins=m["bins"], points=m["points"],
                        start_finish_index=m.get("startFinishIndex", 0), lap_length_m=m.get("lapLengthM"),
                        closure_error_m=m.get("closureErrorM"), source=m.get("source", "?"),
                        meta=m.get("meta", {}))


class LapCollector:
    """Feed it every frame; it hands back one complete lap of samples when the
    car crosses start/finish with a full clean lap behind it."""

    def __init__(self, min_samples: int = 200):
        self.min_samples = min_samples
        self._buf: list[LapSample] = []
        self._last_pct: float | None = None
        self._armed = False  # becomes True once we have seen a S/F crossing
        self._car = None

    def push(self, t, pct, vx, vy, yaw, on_track=True, on_pit=False, car=None) -> list[LapSample] | None:
        # While spectating, the kinematics follow the CAMERA car. A camera cut
        # mid-lap would splice two cars into one "lap" and cache a garbage
        # outline for the track, permanently -- and a broadcast cuts constantly.
        # So a change of camera car voids the lap in progress, exactly like a
        # pit visit does.
        if car != self._car:
            self._car = car
            self._buf.clear()
            self._armed = False
            self._last_pct = None
        if pct is None or vx is None or yaw is None or not on_track or on_pit:
            # a pit visit or leaving the track invalidates the lap in progress
            self._buf.clear()
            self._armed = False
            self._last_pct = pct
            return None
        crossed = self._last_pct is not None and self._last_pct > 0.9 and pct < 0.1
        self._last_pct = pct
        if crossed:
            if self._armed and len(self._buf) >= self.min_samples:
                lap = self._buf
                self._buf = []
                return lap
            self._buf = []
            self._armed = True
        if self._armed:
            self._buf.append(LapSample(t, pct, vx, vy or 0.0, yaw))
        return None


def integrate(samples: list[LapSample]) -> tuple[list[tuple[float, float, float]], float]:
    """Dead-reckon world positions. Returns [(pct, x, y)] and closure error (m)."""
    x = y = 0.0
    out = [(samples[0].pct, 0.0, 0.0)]
    for a, b in zip(samples, samples[1:]):
        dt = b.t - a.t
        if dt <= 0 or dt > 1.0:
            continue
        # trapezoidal on the rotated velocity
        def world(s):
            # YawNorth is the heading clockwise from north: forward = (sin, cos) in
            # (east, north), and VelocityY (leftward) along (-cos, sin). Proven against
            # GPS: an Indianapolis .ibt carries Lat/Lon and these channels for the same
            # lap, and the two outlines agree to ~2 m (tests/test_pipeline.py).
            #
            # History: it was first taken as a counterclockwise angle from +X (mirrored
            # maps), then "fixed" by eye at VIR to (-cos, sin), which was no longer
            # mirrored but turned 90 degrees, so north was not up. Each time
            # FakeSource.heading_at() carried the same assumption and the test passed.
            c, s_ = math.cos(s.yaw), math.sin(s.yaw)
            return (s.vx * s_ - s.vy * c, s.vx * c + s.vy * s_)
        wa, wb = world(a), world(b)
        x += 0.5 * (wa[0] + wb[0]) * dt
        y += 0.5 * (wa[1] + wb[1]) * dt
        out.append((b.pct, x, y))
    closure = math.hypot(out[-1][1] - out[0][1], out[-1][2] - out[0][2])
    return out, closure


def close_loop(path: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Distribute the end-gap linearly so the path is a closed loop."""
    if len(path) < 2:
        return path
    dx = path[-1][1] - path[0][1]
    dy = path[-1][2] - path[0][2]
    n = len(path) - 1
    return [(p, x - dx * i / n, y - dy * i / n) for i, (p, x, y) in enumerate(path)]


def resample(path: list[tuple[float, float, float]], bins: int = DEFAULT_BINS) -> list[list[float]]:
    """One point per LapDistPct bin, linear interpolation; wraps around."""
    pts = sorted(path, key=lambda p: p[0])
    # make monotonic and wrap the ends
    ext = [(p - 1.0, x, y) for p, x, y in pts] + pts + [(p + 1.0, x, y) for p, x, y in pts]
    out = []
    j = 0
    for b in range(bins):
        target = b / bins
        while j + 1 < len(ext) and ext[j + 1][0] < target:
            j += 1
        p0, x0, y0 = ext[j]
        p1, x1, y1 = ext[min(j + 1, len(ext) - 1)]
        f = 0.0 if p1 == p0 else (target - p0) / (p1 - p0)
        out.append([x0 + (x1 - x0) * f, y0 + (y1 - y0) * f])
    return out


def normalize_points(pts: list[list[float]]) -> tuple[list[list[float]], float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny) or 1.0
    cx, cy = (minx + maxx) / 2, (miny + maxy) / 2
    # y flipped so north is up on screen (screen y grows downward)
    return [[(x - cx) / span + 0.5, 0.5 - (y - cy) / span] for x, y in pts], span


def build_from_lap(samples: list[LapSample], track_id, config, bins: int = DEFAULT_BINS) -> TrackMap:
    raw, closure = integrate(samples)
    closed = close_loop(raw)
    lap_len = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(closed, closed[1:]))
    grid = resample(closed, bins)
    norm, span = normalize_points(grid)
    return TrackMap(track_id=track_id, config=config, bins=bins, points=norm,
                    start_finish_index=0, lap_length_m=round(lap_len, 1),
                    closure_error_m=round(closure, 1),
                    meta={"samples": len(samples), "spanM": round(span, 1)})


# --------------------------------------------------------------------------- #
# From an .ibt file (the suite's library): one clean lap, by GPS when it has it
# --------------------------------------------------------------------------- #

_IBT_FMT = {1: "<?", 2: "<i", 3: "<I", 4: "<f", 5: "<d"}


def _ibt_open(path):
    import mmap
    import struct
    f = open(path, "rb")
    mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    i = lambda o: struct.unpack_from("<i", mm, o)[0]
    sl, so, nv, vo, bl, bo = i(16), i(20), i(24), i(28), i(36), i(52)
    if nv <= 0 or bl <= 0 or so + sl > len(mm):
        raise ValueError("not an .ibt")
    yaml = bytes(mm[so:so + sl]).split(b"\0", 1)[0].decode("latin-1")
    var = {}
    for k in range(nv):
        q = vo + k * 144
        t, off, cnt = struct.unpack_from("<iii", mm, q)
        name = bytes(mm[q + 16:q + 48]).split(b"\0")[0].decode("latin-1")
        if cnt == 1 and t in _IBT_FMT:
            var[name] = (_IBT_FMT[t], off)
    return f, mm, yaml, var, bl, bo, i(8) or 60


def _yaml_track(yaml):
    import re

    def val(key):
        m = re.search(r"^\s*" + key + r":\s*([^\n]*)$", yaml, re.M)
        return m.group(1).strip() if m else None
    tid = val("TrackID")
    try:
        tid = int(tid)
    except (TypeError, ValueError):
        pass
    return tid, val("TrackConfigName") or None, val("TrackDisplayName")


def ibt_lap(path, max_laps: int = 40):
    """The first clean, complete lap in an .ibt: [(pct, lat, lon, t, vx, vy, yaw)], with the track's
    (TrackID, TrackConfigName, name). Clean: on track the whole way, no pit road, LapDistPct
    rising steadily from the line to the line (a reset jumps it). None when there is none."""
    import struct
    f, mm, yaml, var, bl, bo, tick = _ibt_open(path)
    try:
        need = ("LapDistPct", "Lap")
        if any(n not in var for n in need):
            return None, _yaml_track(yaml)
        rows = (len(mm) - bo) // bl
        get = {n: var[n] for n in ("LapDistPct", "Lap", "Lat", "Lon", "OnPitRoad", "IsOnTrack",
                                   "VelocityX", "VelocityY", "YawNorth") if n in var}

        def rd(r, n):
            v = get.get(n)
            return None if v is None else struct.unpack_from(v[0], mm, bo + r * bl + v[1])[0]
        cur, last_pct, laps_seen = None, None, 0
        for r in range(rows):
            pct = rd(r, "LapDistPct")
            ok = pct is not None and pct >= 0 and not rd(r, "OnPitRoad") and rd(r, "IsOnTrack") is not False
            crossed = last_pct is not None and last_pct > 0.9 and pct is not None and pct < 0.1
            if crossed:
                if cur is not None and len(cur) > 200 and cur[-1][0] > 0.98:
                    return cur, _yaml_track(yaml)
                laps_seen += 1
                if laps_seen > max_laps:
                    break
                cur = []
            if cur is not None:
                if not ok or (last_pct is not None and not crossed and not (-0.002 <= pct - last_pct < 0.02)):
                    cur = None                     # a pit visit, an off, a reset: wait for the next line
                else:
                    cur.append((pct, rd(r, "Lat"), rd(r, "Lon"), r / tick, rd(r, "VelocityX"), rd(r, "VelocityY"),
                                rd(r, "YawNorth")))
            last_pct = pct
        return None, _yaml_track(yaml)
    finally:
        mm.close()
        f.close()


def build_from_gps(lap, track_id, config, bins: int = DEFAULT_BINS) -> TrackMap | None:
    """An outline from GPS (Lat/Lon in degrees), north up, the same frame as build_from_lap."""
    pts = [(p, la, lo) for p, la, lo, *_ in lap if la and lo]
    if len(pts) < 200:
        return None
    lat0 = sum(q[1] for q in pts) / len(pts)
    k = math.cos(math.radians(lat0)) * 111320.0
    path = [(p, lo * k, la * 111320.0) for p, la, lo in pts]      # x east, y north (metres)
    x0, y0 = path[0][1], path[0][2]
    path = [(p, x - x0, y - y0) for p, x, y in path]
    closure = math.hypot(path[-1][1] - path[0][1], path[-1][2] - path[0][2])
    closed = close_loop(path)
    lap_len = sum(math.hypot(b[1] - a[1], b[2] - a[2]) for a, b in zip(closed, closed[1:]))
    if lap_len < 500:
        return None
    norm, span = normalize_points(resample(closed, bins))
    return TrackMap(track_id=track_id, config=config, bins=bins, points=norm, start_finish_index=0,
                    lap_length_m=round(lap_len, 1), closure_error_m=round(closure, 1), source="gps",
                    meta={"samples": len(pts), "spanM": round(span, 1)})


def build_from_ibt(path, bins: int = DEFAULT_BINS) -> TrackMap | None:
    """The outline of an .ibt's track: GPS when the file has it, else its own speed and heading."""
    lap, (tid, cfg, name) = ibt_lap(path)
    if not lap or tid is None:
        return None
    tm = build_from_gps(lap, tid, cfg or "default", bins)
    if tm is None and all(q[4] is not None and q[6] is not None for q in lap):
        tm = build_from_lap([LapSample(t, p, vx, vy or 0.0, yaw) for p, _la, _lo, t, vx, vy, yaw in lap],
                            tid, cfg or "default", bins)
    if tm is not None:
        tm.meta["from"] = "ibt"
        tm.meta["track"] = name
    return tm


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #


def cache_dir() -> str:
    d = os.environ.get("PITWALL_CACHE") or os.path.join(os.path.expanduser("~"), ".pitwall", "maps")
    os.makedirs(d, exist_ok=True)
    return d


def _key(track_id, config) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(config))
    return f"{track_id}_{safe}.json"


FRAME = 2      # north up; maps saved before it (turned 90 degrees) are built again


def save(tm: TrackMap) -> str:
    tm.meta["frame"] = FRAME
    path = os.path.join(cache_dir(), _key(tm.track_id, tm.config))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tm.to_message(), f)
    os.replace(tmp, path)
    return path


def load(track_id, config) -> TrackMap | None:
    path = os.path.join(cache_dir(), _key(track_id, config))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            tm = TrackMap.from_message(json.load(f))
    except (OSError, ValueError, KeyError):
        return None
    if (tm.meta or {}).get("frame") != FRAME:
        return None
    return tm


def ibt_track(path):
    """(TrackID, TrackConfigName, TrackDisplayName) of an .ibt, from its session info only."""
    f, mm, yaml, *_ = _ibt_open(path)
    mm.close()
    f.close()
    return _yaml_track(yaml)
