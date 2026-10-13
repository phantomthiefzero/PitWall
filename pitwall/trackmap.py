"""Build a track outline from the player's own lap.

iRacing does not expose track geometry. What it does expose every frame:
    VelocityX  forward speed in the car's frame (m/s)
    VelocityY  leftward speed in the car's frame (m/s)
    YawNorth   heading relative to north (rad); heading = (-cos, sin), NOT (cos, sin)
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
            # YawNorth is NOT a counterclockwise angle from +X. Treating it as one
            # produced a world frame of the wrong handedness, so every generated
            # outline came out MIRRORED against the real track -- confirmed by eye
            # at VIR North, where the turn-1 hairpin landed top-right instead of
            # top-left. The X component is negated to restore correct handedness:
            # heading = (-cos(yaw), sin(yaw)).
            #
            # This went unnoticed because FakeSource.heading_at() emitted the same
            # wrong convention, so the ground-truth test inverted the same error
            # and passed at 1.5 m. Both were changed together; the test now checks
            # the convention iRacing actually uses.
            c, s_ = math.cos(s.yaw), math.sin(s.yaw)
            return (-(s.vx * c - s.vy * s_), s.vx * s_ + s.vy * c)
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
# Cache
# --------------------------------------------------------------------------- #


def cache_dir() -> str:
    d = os.environ.get("PITWALL_CACHE") or os.path.join(os.path.expanduser("~"), ".pitwall", "maps")
    os.makedirs(d, exist_ok=True)
    return d


def _key(track_id, config) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in str(config))
    return f"{track_id}_{safe}.json"


def save(tm: TrackMap) -> str:
    path = os.path.join(cache_dir(), _key(tm.track_id, tm.config))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tm.to_message(), f)
    return path


def load(track_id, config) -> TrackMap | None:
    path = os.path.join(cache_dir(), _key(track_id, config))
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return TrackMap.from_message(json.load(f))
