"""Bridge: runs on the driver's PC. Source -> normalize -> relay.

Rates (defaults): state 15 Hz, cars 8 Hz, session on change, map once per
track (built live from the first clean lap if there is no cached outline).
Reconnects to the relay forever; the sim being closed just pauses output.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

now_m = time.monotonic
import traceback
import urllib.parse

import websockets

from . import trackmap
from .delta import DeltaEncoder, cars_to_wire
from .commands import CommandGate
from .carrender import CarRenderer, render_key
from .director import Director
from .liveries import LiveryCapture, LiveryStore, livery_key
from .normalize import Deriver, build_cars, build_session, build_state
from .sources import TelemetrySource


class Bridge:
    def __init__(self, src: TelemetrySource, relay_url: str, token: str,
                 state_hz: float = 15.0, cars_hz: float = 8.0, allow_commands: bool = False,
                 allow_camera: bool = False, log=print, livery_dir: str | None = None,
                 render_cars: bool = True):
        self.src = src
        self.relay_url = relay_url
        self.token = token
        self.state_dt = 1.0 / state_hz
        self.cars_dt = 1.0 / cars_hz
        self.gate = CommandGate(allow_commands, camera=allow_camera)
        self.log = log
        self.deriver = Deriver()
        self._src_restarts = 0
        self._session_num = None
        self.session_msg: dict | None = None
        self.map_msg: dict | None = None
        self._last_siu = None
        self._collector = trackmap.LapCollector()
        self._map_key = None
        self.delta = DeltaEncoder()
        self.stats = {"sent": 0, "frames": 0, "reconnects": 0, "frame_errors": 0}
        # Broadcast helpers. Both only ever move the camera through self.gate,
        # so --allow-camera governs them exactly as it governs a producer.
        self._extra: list[dict] = []              # messages made off the event loop
        self._loop = None
        self.director = Director(self._cut, log=log)
        self.livery_store = LiveryStore(livery_dir)
        self.capture = LiveryCapture(self.livery_store, self._cut, self._emit, log=log)
        # iRacing's own picture of every car with its paint (Trading Paints included)
        self.renderer = CarRenderer(self.livery_store.base, self._emit, log=log) if render_cars else None
        self._render_check = 0.0
        self._last_cars = None
        self._last_state = None
        self._cam_hold = None                     # (carIdx, group, since)
        self._liveries_sent: set[str] = set()
        self._auto_tried: set[str] = set()
        self._last_err = None
        self._next_state = self._next_cars = 0.0
        # The feed (lap history + events) is append-only. Viewers get it whole
        # on connect, on a resend and after a reset, and only the new entries
        # otherwise; feed_seq says how far each viewer has been sent.
        self._feed_sent = 0
        self._feed_full_due = True

    def _ws_url(self) -> str:
        sep = "&" if "?" in self.relay_url else "?"
        return f"{self.relay_url.rstrip('/')}/bridge{sep}token={urllib.parse.quote(self.token)}"

    # ------------------------------------------------------------------ #
    def _maybe_session(self, outbox: list[dict]):
        siu = self.src.session_info_update
        if siu != self._last_siu:
            self.session_msg = build_session(self.src)
            self._last_siu = siu   # after the build, so a failed parse is retried next frame
            self._render_check = 0.0
            outbox.append(self.session_msg)
            tr = self.session_msg["track"]
            key = (tr.get("TrackID"), tr.get("TrackConfigName"))
            if key != self._map_key:
                self._map_key = key
                cached = trackmap.load(key[0], key[1] or "default")
                self.map_msg = cached.to_message() if cached else None
                if self.map_msg:
                    outbox.append(self.map_msg)
                    self.log(f"track map loaded from cache for {key}")
                else:
                    self.log(f"no cached map for {key}; will build from first clean lap")
                    self._collector = trackmap.LapCollector()

    def _maybe_build_map(self, state: dict, outbox: list[dict]):
        if self.map_msg is not None or self._map_key is None:
            return
        p = state["player"]
        g = self.src.get
        lap = self._collector.push(state["t"], p["lapPct"], g("VelocityX"), g("VelocityY"), g("YawNorth"),
                                   on_track=bool(p["isOnTrack"]), on_pit=bool(p["onPitRoad"]),
                                   car=(state.get("camera") or {}).get("carIdx"))
        if lap:
            tm = trackmap.build_from_lap(lap, self._map_key[0], self._map_key[1] or "default")
            trackmap.save(tm)
            self.map_msg = tm.to_message()
            outbox.append(self.map_msg)
            self.log(f"track map built: {len(lap)} samples, closure error {tm.closure_error_m} m, cached")

    async def _producer(self, ws):
        loop = asyncio.get_running_loop()
        self._next_state = self._next_cars = 0.0
        while True:
            ok = await loop.run_in_executor(None, self.src.tick)
            if not ok:
                await asyncio.sleep(0.5)
                continue
            self.stats["frames"] += 1
            try:
                outbox = self._build(time.perf_counter())
            except Exception:  # noqa: BLE001
                # One frame the normalizer cannot handle must not end the stream
                # mid-race -- uncaught, it escaped run() and the process exited.
                # Log each distinct error once (a persistent fault would
                # otherwise print at 60 Hz) and carry on with the next frame.
                err = traceback.format_exc()
                if err != self._last_err:
                    self._last_err = err
                    self.log("frame skipped; the normalizer raised:\n" + err)
                self.stats["frame_errors"] += 1
                outbox = []
            if self._extra:
                outbox.extend(self._extra)
                self._extra = []
            for m in outbox:
                await ws.send(json.dumps(m, separators=(",", ":")))
                self.stats["sent"] += 1
            # yield so the consumer can run; the source paces us at ~60 Hz
            await asyncio.sleep(0)

    def _build(self, now: float) -> list[dict]:
        outbox: list[dict] = []
        self._maybe_session(outbox)
        if now >= self._next_state:
            self._next_state = now + self.state_dt
            # Derived history belongs to one run of one session. Two things
            # end it: the session itself changing (qualifying into a race --
            # a stop taken in qualifying is not a race stop, and a stint does
            # not span the two), and a looping replay starting the recording
            # over. Both would otherwise carry stop counts, stint length,
            # gap trends, sector bests and fuel samples across the boundary.
            restarts = getattr(self.src, "restarts", 0)
            session_num = self.src.get("SessionNum")
            if restarts != self._src_restarts:
                self._src_restarts = restarts
                self._session_num = session_num
                self.deriver = Deriver()
                self._feed_full_due = True
                self.log("source restarted; derived history reset")
            elif session_num is not None and session_num != self._session_num:
                prev, self._session_num = self._session_num, session_num
                if prev is not None:
                    self.deriver = Deriver()
                    self._feed_full_due = True
                    self.log(f"session {prev} -> {session_num}; derived history reset")
            st = build_state(self.src, self.deriver)
            self._last_state = st
            self._maybe_build_map(st, outbox)
            enc = self.delta.encode(st, now)
            if enc:
                outbox.append(enc)
        if now >= self._next_cars:
            self._next_cars = now + self.cars_dt
            cars_msg = build_cars(self.src, self.session_msg, self.deriver)
            self._last_cars = cars_msg
            self._broadcast_tick(cars_msg, outbox)
            enc = self.delta.encode(cars_to_wire(cars_msg), now)
            if enc:
                outbox.append(enc)
            feed = self._feed_message()
            if feed:
                outbox.append(feed)
        return outbox

    # ------------------------------------------------------------------ #
    # broadcast: director, livery photos
    def _cut(self, car_number: str, group: int) -> dict:
        return self.gate.execute(self.src, {"cmd": "camera", "action": "car", "car": str(car_number),
                                            "group": int(group), "camera": 0})

    def _emit(self, msg: dict):
        """Thread-safe: queue a message for the next frame."""
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._extra.append, msg)
        else:
            self._extra.append(msg)

    def _cars_by_idx(self) -> dict:
        return {c["idx"]: c for c in (self._last_cars or {}).get("cars") or []}

    def _livery_messages(self) -> list[dict]:
        """Every kept picture for a car in this session (sent on connect/resend)."""
        out = []
        if self.renderer is not None:
            for d in (self.session_msg or {}).get("drivers") or []:
                k = render_key(d)
                m = self.renderer.cached_message(k) if k else None
                if m:
                    out.append(m)
        for c in (self._last_cars or {}).get("cars") or []:
            key = livery_key(c)
            if key and key not in self._liveries_sent:
                m = self.livery_store.message(key, c)
                if m:
                    out.append(m)
                    self._liveries_sent.add(key)
        return out

    def _broadcast_tick(self, cars_msg: dict, outbox: list):
        recent = [e for e in self.deriver.feed_events[-40:] if e.get("kind") == "race_incident"]
        if self.gate.camera:
            try:
                self.director.tick(self.session_msg, cars_msg, self._last_state, recent)
            except Exception as e:  # noqa: BLE001
                self.log(f"director: {type(e).__name__}: {e}")
                self.director.stop()
        if self.director.changed:
            self.director.changed = False
            outbox.append(self.director.status())
        # Car pictures from iRacing: on every session change, and every 20 s in
        # case Trading Paints has just downloaded a paint (it does, after you join).
        if self.renderer is not None and self.session_msg is not None and now_m() - self._render_check > 20.0:
            self._render_check = now_m()
            self.renderer.request(self.session_msg.get("drivers") or [])
        # photos already on disk for cars that have just appeared
        for c in cars_msg.get("cars") or []:
            key = livery_key(c)
            if key and key not in self._liveries_sent and self.livery_store.has(key):
                m = self.livery_store.message(key, c)
                if m:
                    outbox.append(m)
                self._liveries_sent.add(key)
        # Auto: the camera has been on a car in a chase view for a moment and
        # that car has no photo yet -- take one, without touching the camera.
        cam = (self._last_state or {}).get("camera") or {}
        edge = ((self._last_state or {}).get("derived") or {}).get("liveEdge") or {}
        car_idx, group = cam.get("carIdx"), cam.get("group")
        now = time.monotonic()
        if not self._cam_hold or self._cam_hold[:2] != (car_idx, group):
            self._cam_hold = (car_idx, group, now)
        cfg = self.livery_store.cfg
        if cfg.get("auto") and cfg.get("source") and not self.capture.status["sweeping"] \
                and now - self._cam_hold[2] > 2.5 and edge.get("atLive", True):
            gname = next((g.get("name") for g in (self.session_msg or {}).get("cameras") or [] if g.get("num") == group), "")
            car = self._cars_by_idx().get(car_idx)
            key = livery_key(car) if car else None
            if key and "chase" in (gname or "").lower() and not self.livery_store.has(key) \
                    and key not in self._auto_tried:
                self._auto_tried.add(key)
                threading.Thread(target=self.capture.snap, args=(dict(car),), daemon=True).start()

    def _broadcast_cmd(self, msg: dict) -> dict:
        res = {"type": "cmd_result", "ok": False, "echo": msg, "error": None}
        if not self.gate.camera:
            res["error"] = f"{msg.get('cmd')} needs camera control (start the bridge with --allow-camera)"
            return res
        kind, action = msg.get("cmd"), msg.get("action")
        cars = (self._last_cars or {}).get("cars") or []
        if kind == "director":
            if action == "set":
                self.director.configure(on=msg.get("on"), style=msg.get("style"), hold_s=msg.get("holdS"))
            elif action == "grid_walk":
                rows = ((self.session_msg or {}).get("grid") or {}).get("rows") or []
                by = {c["idx"]: c for c in cars}
                order = [by[r["idx"]] for r in rows if r.get("idx") in by] or \
                    sorted((c for c in cars if c.get("pos")), key=lambda c: c["pos"])
                snap = bool(msg.get("capture")) and bool(self.livery_store.cfg.get("source"))

                def on_car(car):
                    if snap:
                        threading.Timer(LiveryCapture.SETTLE_S, self.capture.snap, args=(dict(car),)).start()
                self.director.start_grid_walk(order, hold_s=msg.get("holdS", 5.0), on_car=on_car)
            elif action == "stop":
                self.director.stop()
            else:
                res["error"] = f"unknown director action {action!r}"
                return res
            res.update(ok=True, data=self.director.status())
            return res
        if kind == "livery":
            if action == "setup":
                cfg = self.livery_store.cfg
                for k in ("url", "source", "group"):
                    if isinstance(msg.get(k), str):
                        cfg[k] = msg[k].strip()
                if isinstance(msg.get("password"), str) and msg["password"] != "":
                    cfg["password"] = msg["password"]
                if isinstance(msg.get("auto"), bool):
                    cfg["auto"] = msg["auto"]
                self.livery_store.save_cfg()
                self.capture.reset()
                t = self.capture.test()
                self._emit(self.capture.status_message())
                res.update(ok=t["ok"], error=t.get("error"), data={"sources": t.get("sources") or []})
                return res
            if action == "sweep":
                group = self._group_by_name(self.livery_store.cfg.get("group") or "Chase")
                if group is None:
                    res["error"] = "no chase camera group in this session"
                    return res
                order = sorted((c for c in cars if c.get("num") is not None),
                               key=lambda c: (c.get("pos") or 999, c["idx"]))
                started = self.capture.sweep(order, group, skip_existing=bool(msg.get("missingOnly")))
                res.update(ok=started, error=None if started else "a capture is already running")
                return res
            if action == "stop":
                self.capture.stop()
                res["ok"] = True
                return res
            if action == "snap":
                car = self._cars_by_idx().get(msg.get("idx"))
                if not car:
                    res["error"] = "no such car"
                    return res
                threading.Thread(target=self.capture.snap, args=(dict(car),), daemon=True).start()
                res["ok"] = True
                return res
            res["error"] = f"unknown livery action {action!r}"
            return res
        res["error"] = f"unsupported command {kind!r}"
        return res

    def _group_by_name(self, name: str):
        for g in (self.session_msg or {}).get("cameras") or []:
            if (g.get("name") or "").lower() == name.lower():
                return g.get("num")
        return None

    def _feed_message(self) -> dict | None:
        d = self.deriver
        if self._feed_full_due:
            self._feed_full_due = False
            self._feed_sent = d.feed_seq
            return {"type": "feed", "full": True, "seq": d.feed_seq,
                    "laps": list(d.feed_laps), "events": list(d.feed_events)}
        if d.feed_seq > self._feed_sent:
            out = {"type": "feed", "full": False, "seq": d.feed_seq, **d.feed_since(self._feed_sent)}
            self._feed_sent = d.feed_seq
            return out
        return None

    async def _consumer(self, ws):
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type") == "cmd" and msg.get("cmd") in ("director", "livery"):
                res = await asyncio.get_running_loop().run_in_executor(None, self._broadcast_cmd, msg)
                self.log(f"cmd {msg.get('cmd')}/{msg.get('action')} -> {'ok' if res['ok'] else res['error']}")
                await ws.send(json.dumps(res))
            elif msg.get("type") == "cmd":
                # In a worker thread: a replay jump pauses between its seek and
                # its play, and the stream must not stall while it does.
                res = await asyncio.get_running_loop().run_in_executor(None, self.gate.execute, self.src, msg)
                self.log(f"cmd {msg} -> {'ok' if res['ok'] else res['error']}")
                await ws.send(json.dumps(res))
            elif msg.get("type") == "resend":
                # a new viewer wants the slow-changing messages again + fresh keyframes
                for m in (self.session_msg, self.map_msg, self.director.status(),
                          self.capture.status_message()):
                    if m:
                        await ws.send(json.dumps(m, separators=(",", ":")))
                self._liveries_sent.clear()
                for m in self._livery_messages():
                    await ws.send(json.dumps(m, separators=(",", ":")))
                self.delta.reset()
                self._feed_full_due = True

    async def run(self):
        self._loop = asyncio.get_running_loop()
        if not self.src.connect():
            self.log("source not ready; will keep trying")
        while True:
            try:
                async with websockets.connect(self._ws_url(), max_size=8 * 1024 * 1024, ping_interval=20) as ws:
                    self.log(f"connected to relay {self.relay_url}")
                    await ws.send(json.dumps({"type": "hello", "role": "bridge", "schema": 1,
                                              "source": self.src.name, "commands": self.gate.enabled,
                                              "camera": self.gate.camera}))
                    # force session/map resend and fresh keyframes on every (re)connect
                    self._last_siu = None
                    self._liveries_sent.clear()
                    self.delta.reset()
                    self._feed_full_due = True
                    await asyncio.gather(self._producer(ws), self._consumer(ws))
            except (OSError, websockets.exceptions.WebSocketException) as e:
                # The relay accepts one bridge and closes the previous one with
                # 4000 when a new one arrives. Reconnecting after that just takes
                # the slot back, and if both sides do it the two bridges replace
                # each other forever -- observed as a viewer receiving a fresh
                # session/map burst every few seconds while neither bridge kept
                # the stream. Whoever connected last is the one the operator
                # meant, so the displaced bridge stands down instead.
                code = getattr(e, "code", None) or getattr(e, "rcvd", None) and getattr(e.rcvd, "code", None)
                if code == 4000:
                    self.log("another bridge took over this relay; stopping. "
                             "Only one bridge can stream to a relay at a time.")
                    return
                self.stats["reconnects"] += 1
                self.log(f"relay connection lost ({type(e).__name__}: {e}); retrying in 3 s")
                await asyncio.sleep(3)
