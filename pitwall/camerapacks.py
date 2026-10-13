"""Which camera packs are installed for this session's track and cars.

A camera pack is a set of .cam files in Documents\\iRacing\\cameras\\tracks\\<track>
and ...\\cars\\<car> (iRacing's own savetrack / savecar write there too). The
suite's installer copies a downloaded pack there (suite/cameras.py).

Installed is not the same as loaded. Track Cams for Gourmets, for one, says its
files "still have to be loaded in sim": Ctrl+F12, then Load track / Load car.
Only then do the pack's groups appear in the session's CameraInfo, which is the
list the producer and the director choose from. So this reports what is on
disk for the current track, and the producer panel compares it with the groups
iRacing is actually offering.
"""
from __future__ import annotations

import os
import re

from .carrender import documents_dir


def cameras_dir() -> str:
    return os.path.join(documents_dir(), "iRacing", "cameras")


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _folders(path: str) -> list[str]:
    try:
        return sorted(d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d)))
    except OSError:
        return []


def _cam_files(path: str) -> list[str]:
    out = []
    for root, _dirs, files in os.walk(path):
        for f in files:
            if f.lower().endswith(".cam"):
                out.append(os.path.relpath(os.path.join(root, f), path).replace("\\", "/"))
    return sorted(out)


def match_folder(folders: list[str], name: str | None) -> str | None:
    """The folder for a track or car name. iRacing's names carry the layout
    ("virginia 2022 full") where a folder may not ("virginia 2022"), so the
    longest folder the name starts with wins; an exact match beats all."""
    n = _norm(name)
    if not n:
        return None
    best = None
    for f in folders:
        fn = _norm(f)
        if not fn:
            continue
        if fn == n:
            return f
        if n.startswith(fn + " ") or fn.startswith(n + " "):
            if best is None or len(_norm(best)) < len(fn):
                best = f
    return best


def packs_message(session: dict | None, base: str | None = None) -> dict:
    base = base or cameras_dir()
    tracks_dir, cars_dir = os.path.join(base, "tracks"), os.path.join(base, "cars")
    tracks, cars = _folders(tracks_dir), _folders(cars_dir)
    tr = (session or {}).get("track") or {}
    t = match_folder(tracks, tr.get("TrackName")) or match_folder(tracks, tr.get("TrackDisplayName"))
    car_paths = sorted({d.get("CarPath") for d in (session or {}).get("drivers") or [] if d.get("CarPath")})
    car_hits = []
    for cp in car_paths:
        f = match_folder(cars, cp)
        if f:
            car_hits.append({"carPath": cp, "folder": f, "files": _cam_files(os.path.join(cars_dir, f))[:20]})
    return {"type": "camera_packs", "dir": base, "exists": os.path.isdir(base),
            "trackCount": len(tracks), "carCount": len(cars),
            "track": {"name": tr.get("TrackDisplayName") or tr.get("TrackName"), "folder": t,
                      "files": _cam_files(os.path.join(tracks_dir, t))[:20] if t else []},
            "cars": car_hits}
