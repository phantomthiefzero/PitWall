"""Car pictures rendered by iRacing itself, with each driver's paint.

While the sim runs, iRacing serves a small local web server on 127.0.0.1:32034
(the same one its UI uses). /pk_car.png renders a picture of a car from a
description: the car (CarPath), iRacing's paint scheme and number design, and,
when given a path to a .tga, the custom paint on disk -- which is exactly where
Trading Paints puts the paints it downloads:

    Documents\\iRacing\\paint\\<CarPath>\\car_<UserID>.tga          a driver's paint
    Documents\\iRacing\\paint\\<CarPath>\\car_team_<TeamID>.tga     a team's paint (team events)

So every car gets a real picture: the Trading Paints livery where the driver has
one, iRacing's own paint scheme where not. No OBS, no camera moves.

The endpoint is not documented by iRacing. The parameter mapping follows
SIMRacingApps (iRacingCar.getImageUrl, Apache 2.0) as used by the
halvar20000/iracing-overlays livery overlay, including two traps found there:
spaces must be %20 (a '+' is read as a literal plus and the car is not found),
and an unknown CarPath silently returns a DEFAULT car rather than an error, so
the nested MX-5 folder ("mx5 mx52016") is tried in its folder form first.
"""
from __future__ import annotations

import os
import threading
import time
import urllib.parse
import urllib.request

RENDER_URL = "http://127.0.0.1:32034/pk_car.png"
RENDER_VIEW = 1
RENDER_SIZE = 2          # 0 small, 1 medium, 2 large


def documents_dir() -> str:
    """The user's Documents folder, following a OneDrive redirection on Windows."""
    try:
        import ctypes
        from ctypes import wintypes
        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0 and buf.value:
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return os.path.join(os.path.expanduser("~"), "Documents")


def paint_root() -> str:
    return os.path.join(documents_dir(), "iRacing", "paint")


def car_path_variants(car_path: str) -> list[str]:
    """iRacing's CarPath for the MX-5 is "mx5 mx52016"; on disk that space is a
    folder separator. Folder forms first, the raw form last."""
    if " " in car_path:
        return [car_path.replace(" ", "\\"), car_path.replace(" ", "/"), car_path]
    return [car_path]


def find_paint(car_path: str, user_id, team_id=None, root: str | None = None) -> str | None:
    """The custom paint iRacing would put on this car, if there is one. In a team
    event the car wears the TEAM's paint; looking only for the driver's own file
    would show his paint from some other series."""
    if not car_path:
        return None
    root = root or paint_root()
    names = []
    if team_id and team_id not in (0, -1):
        names.append(f"car_team_{team_id}.tga")
    if user_id and user_id not in (0, -1):
        names.append(f"car_{user_id}.tga")
    for folder in car_path_variants(car_path):
        for n in names:
            p = os.path.join(root, *folder.replace("\\", "/").split("/"), n)
            if os.path.isfile(p):
                return p
    return None


def render_params(d: dict, paint_path: str | None) -> dict:
    """A DriverInfo/Drivers entry -> /pk_car.png query parameters."""
    p = {"view": RENDER_VIEW, "size": RENDER_SIZE}
    if d.get("CarPath"):
        p["carPath"] = str(d["CarPath"]).strip()
    if d.get("CarID"):
        p["carId"] = str(d["CarID"])
    design = [x.strip() for x in str(d.get("CarDesignStr") or "").split(",")] if d.get("CarDesignStr") else []
    if design and design[0]:
        p["carPat"] = design[0]
    if len(design) >= 4:
        p["carCol"] = ",".join(design[1:4])
    num = [x.strip() for x in str(d.get("CarNumberDesignStr") or "").split(",")] if d.get("CarNumberDesignStr") else []
    if num and num[0]:
        p["numPat"] = num[0]
        p["numfont"] = num[0]
    if len(num) >= 2 and num[1]:
        p["numSlnt"] = num[1]
    if len(num) >= 5:
        p["numcol"] = ",".join(num[2:5])
    if d.get("CarNumber"):
        p["number"] = str(d["CarNumber"]).strip()
    lic = d.get("LicColor")
    if lic not in (None, ""):
        p["licCol"] = f"{lic & 0xFFFFFF:06x}" if isinstance(lic, int) else str(lic).lstrip("#").removeprefix("0x")
    sp1, sp2 = d.get("CarSponsor_1") or 0, d.get("CarSponsor_2") or 0
    if sp1 or sp2:
        p["sponsors"] = f"{sp1},{sp2}"
    if d.get("ClubID"):
        p["club"] = str(d["ClubID"])
    name = d.get("TeamName") or d.get("UserName")
    if name:
        p["name"] = str(name)
    if paint_path:
        p["carCustPaint"] = paint_path
    return p


def render_urls(d: dict, paint_path: str | None) -> list[str]:
    """One URL per CarPath form to try, in order."""
    p = render_params(d, paint_path)
    out = []
    for cp in car_path_variants(p.get("carPath", "")) if p.get("carPath") else [None]:
        q = dict(p)
        if cp:
            q["carPath"] = cp
        out.append(RENDER_URL + "?" + urllib.parse.urlencode(q, quote_via=urllib.parse.quote))
    return out


def _http_get(url: str, timeout: float = 6.0) -> tuple[int, str, bytes]:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


class CarRenderer:
    """Renders every car in the session once, on a worker thread, and again if
    its paint changes (Trading Paints downloads paints after you join)."""

    RETRY_DOWN_S = 30.0       # the render server was not answering: wait before trying again

    def __init__(self, store_dir: str, emit, log=print, fetch=_http_get, root: str | None = None):
        self.dir = os.path.join(store_dir, "renders")
        self.emit = emit
        self.log = log
        self.fetch = fetch
        self.root = root
        self._sig: dict[str, tuple] = {}      # key -> signature of what was rendered
        self._queue: dict[str, tuple] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._down_until = 0.0
        self.status = {"available": None, "rendered": 0, "error": None}
        threading.Thread(target=self._run, daemon=True, name="car-render").start()

    def path(self, key: str) -> str:
        return os.path.join(self.dir, "".join(ch for ch in key if ch.isalnum() or ch in "_-") + ".png")

    def signature(self, d: dict, paint: str | None) -> tuple:
        mt = os.path.getmtime(paint) if paint and os.path.isfile(paint) else None
        return (d.get("CarPath"), d.get("CarDesignStr"), d.get("CarNumberDesignStr"), str(d.get("CarNumber")),
                paint, mt)

    def request(self, drivers: list[dict]):
        """Queue every driver whose picture is missing or out of date."""
        for d in drivers:
            if d.get("IsSpectator") or d.get("CarIsPaceCar") or not d.get("CarPath"):
                continue
            key = render_key(d)
            if not key:
                continue
            paint = find_paint(d.get("CarPath"), d.get("UserID"), d.get("TeamID"), self.root)
            sig = self.signature(d, paint)
            if self._sig.get(key) == sig:
                continue
            with self._lock:
                self._queue[key] = (d, paint, sig)
        self._wake.set()

    def cached_message(self, key: str) -> dict | None:
        import base64
        p = self.path(key)
        if not os.path.isfile(p):
            return None
        with open(p, "rb") as f:
            return livery_message(key, "render", "data:image/png;base64," + base64.b64encode(f.read()).decode())

    def _run(self):
        while True:
            self._wake.wait(5.0)
            self._wake.clear()
            while True:
                if time.monotonic() < self._down_until:
                    break
                with self._lock:
                    if not self._queue:
                        break
                    key = next(iter(self._queue))
                    d, paint, sig = self._queue.pop(key)
                self._render_one(key, d, paint, sig)

    def _render_one(self, key, d, paint, sig):
        import base64
        for url in render_urls(d, paint):
            try:
                status, ctype, body = self.fetch(url)
            except Exception as e:  # noqa: BLE001
                # Not running, or an iRacing without the render server: stop and
                # retry later; the queue is kept.
                self.status.update(available=False, error=f"{type(e).__name__}: {e}")
                self._down_until = time.monotonic() + self.RETRY_DOWN_S
                with self._lock:
                    self._queue.setdefault(key, (d, paint, sig))
                return
            if status == 200 and ctype.lower().startswith("image/") and body:
                os.makedirs(self.dir, exist_ok=True)
                tmp = self.path(key) + ".part"
                with open(tmp, "wb") as f:
                    f.write(body)
                os.replace(tmp, self.path(key))
                self._sig[key] = sig
                self.status.update(available=True, error=None, rendered=self.status["rendered"] + 1)
                self.emit(livery_message(key, "render", f"data:{ctype.split(';')[0]};base64," + base64.b64encode(body).decode(),
                                         custom=bool(paint)))
                return
        self._sig[key] = sig      # every form refused: do not hammer it
        self.status["error"] = f"no picture for {d.get('CarPath')}"


def render_key(d: dict) -> str | None:
    """Same key as liveries.livery_key, from a raw DriverInfo entry."""
    who = d.get("TeamID") or d.get("UserID")
    if d.get("CarID") is None or who in (None, 0, -1):
        return None
    return f"{d['CarID']}_{who}"


def livery_message(car_key: str, kind: str, image: str, **extra) -> dict:
    """kind: "render" (iRacing's picture of the car with its paint) or "photo"
    (an OBS still from the broadcast). The relay keeps one of each per car."""
    return {"type": "livery", "key": f"{car_key}:{kind}", "car": car_key, "kind": kind, "image": image,
            "at": time.time(), **extra}
