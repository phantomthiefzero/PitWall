"""Livery photos for the broadcast.

Trading Paints delivers each driver's paint as a texture that iRacing wraps
around the 3D car; the flat texture is unrecognisable on screen. The livery as
viewers know it only exists in iRacing's own picture, so that is where it is
taken from: with the camera on a car in a chase view, OBS is asked for a still
of the iRacing capture source (pitwall/obs.py). Each photo is kept on disk per
driver and car, so a regular in the same car is already there next week.

The main source of car pictures is iRacing's own renderer (pitwall/carrender.py),
which needs no OBS and draws every car with its Trading Paints paint. These
photos are the optional extra: the car as it looked on track.

Photos are only ever taken of what is on screen; nothing here invents a livery.
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time


def livery_key(car: dict) -> str | None:
    """Same paint, same key: the car model plus who is driving it (the team in a
    team event, since a team shares one paint)."""
    who = car.get("teamId") or car.get("userId")
    if car.get("carId") is None or who in (None, 0, -1):
        return None
    return f"{car['carId']}_{who}"


def default_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".pitwall")


class LiveryStore:
    def __init__(self, base: str | None = None):
        self.base = base or default_dir()
        self.dir = os.path.join(self.base, "liveries")
        self.cfg_path = os.path.join(self.base, "obs.json")
        self.cfg = {"url": "ws://127.0.0.1:4455", "password": "", "source": "", "group": "Chase",
                    "auto": True}
        try:
            with open(self.cfg_path, encoding="utf-8") as f:
                self.cfg.update({k: v for k, v in json.load(f).items() if k in self.cfg})
        except (OSError, ValueError):
            pass

    def save_cfg(self):
        os.makedirs(self.base, exist_ok=True)
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f)

    def path(self, key: str) -> str:
        safe = "".join(ch for ch in key if ch.isalnum() or ch in "_-")
        return os.path.join(self.dir, safe + ".jpg")

    def has(self, key: str) -> bool:
        return os.path.isfile(self.path(key))

    def put(self, key: str, data_uri: str):
        os.makedirs(self.dir, exist_ok=True)
        raw = base64.b64decode(data_uri.split(",", 1)[-1])
        tmp = self.path(key) + ".part"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, self.path(key))

    def get(self, key: str) -> str | None:
        try:
            with open(self.path(key), "rb") as f:
                return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
        except OSError:
            return None

    def message(self, key: str, car: dict | None = None, image: str | None = None) -> dict | None:
        from .carrender import livery_message
        image = image or self.get(key)
        if not image:
            return None
        return livery_message(key, "photo", image, idx=(car or {}).get("idx"))


class LiveryCapture:
    """Takes photos on a worker thread. `cut(car_number, group)` moves iRacing's
    camera (through the command gate); `emit(msg)` sends a message to viewers."""

    SETTLE_S = 1.3      # after a cut, let the camera arrive and the car fill the frame

    def __init__(self, store: LiveryStore, cut, emit, log=print, client_factory=None):
        self.store = store
        self.cut = cut
        self.emit = emit
        self.log = log
        self._client_factory = client_factory
        self._client = None
        self._busy = threading.Lock()
        self.status = {"connected": False, "error": None, "sweeping": False, "done": 0, "total": 0,
                       "lastKey": None}

    def _obs(self):
        if self._client is None:
            if self._client_factory:
                self._client = self._client_factory(self.store.cfg)
            else:
                from .obs import OBSClient
                self._client = OBSClient(self.store.cfg["url"], self.store.cfg["password"])
        return self._client

    def reset(self):
        if self._client is not None and hasattr(self._client, "close"):
            self._client.close()
        self._client = None

    def test(self) -> dict:
        try:
            srcs = self._obs().sources()
            self.status.update(connected=True, error=None)
            return {"ok": True, "sources": srcs}
        except Exception as e:  # noqa: BLE001
            self.reset()
            self.status.update(connected=False, error=str(e))
            return {"ok": False, "error": str(e)}

    def snap(self, car: dict) -> bool:
        """Photograph whatever is on screen now and file it under this car."""
        key = livery_key(car)
        src = self.store.cfg.get("source")
        if not key or not src:
            return False
        try:
            img = self._obs().screenshot(src)
        except Exception as e:  # noqa: BLE001
            self.reset()
            self.status.update(connected=False, error=str(e))
            self.emit(self.status_message())
            return False
        self.store.put(key, img)
        self.status.update(connected=True, error=None, lastKey=key)
        msg = self.store.message(key, car)   # re-read: one MIME type, whatever OBS called it
        if msg:
            self.emit(msg)
        return True

    def sweep(self, cars: list[dict], group: int, skip_existing: bool = False, hold_s: float | None = None):
        """Cut to each car in turn, wait, photograph. Runs on its own thread."""
        if not self._busy.acquire(blocking=False):
            return False

        def run():
            try:
                todo = [c for c in cars if livery_key(c) and not (skip_existing and self.store.has(livery_key(c)))]
                self.status.update(sweeping=True, done=0, total=len(todo))
                self.emit(self.status_message())
                for c in todo:
                    if not self.status["sweeping"]:
                        break
                    if c.get("num") is None:
                        continue
                    self.cut(str(c["num"]), group)
                    time.sleep(hold_s if hold_s is not None else self.SETTLE_S)
                    self.snap(c)
                    self.status["done"] += 1
                    self.emit(self.status_message())
            finally:
                self.status["sweeping"] = False
                self.emit(self.status_message())
                self._busy.release()
        threading.Thread(target=run, daemon=True, name="livery-sweep").start()
        return True

    def stop(self):
        self.status["sweeping"] = False

    def status_message(self) -> dict:
        cfg = {k: v for k, v in self.store.cfg.items() if k != "password"}
        cfg["hasPassword"] = bool(self.store.cfg.get("password"))
        return {"type": "livery_status", **self.status, "config": cfg}
