"""Bridge: runs on the driver's PC. Source -> normalize -> relay.

Rates (defaults): state 15 Hz, cars 8 Hz, session on change, map once per
track (built live from the first clean lap if there is no cached outline).
Reconnects to the relay forever; the sim being closed just pauses output.
"""
from __future__ import annotations

import asyncio
import json
import time
import traceback
import urllib.parse

import websockets

from . import trackmap
from .delta import DeltaEncoder, cars_to_wire
from .commands import CommandGate
from .normalize import Deriver, build_cars, build_session, build_state
from .sources import TelemetrySource


class Bridge:
    def __init__(self, src: TelemetrySource, relay_url: str, token: str,
                 state_hz: float = 15.0, cars_hz: float = 8.0, allow_commands: bool = False,
                 allow_camera: bool = False, log=print):
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
            self._maybe_build_map(st, outbox)
            enc = self.delta.encode(st, now)
            if enc:
                outbox.append(enc)
        if now >= self._next_cars:
            self._next_cars = now + self.cars_dt
            enc = self.delta.encode(cars_to_wire(build_cars(self.src, self.session_msg, self.deriver)), now)
            if enc:
                outbox.append(enc)
            feed = self._feed_message()
            if feed:
                outbox.append(feed)
        return outbox

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
            if msg.get("type") == "cmd":
                # In a worker thread: a replay jump pauses between its seek and
                # its play, and the stream must not stall while it does.
                res = await asyncio.get_running_loop().run_in_executor(None, self.gate.execute, self.src, msg)
                self.log(f"cmd {msg} -> {'ok' if res['ok'] else res['error']}")
                await ws.send(json.dumps(res))
            elif msg.get("type") == "resend":
                # a new viewer wants the slow-changing messages again + fresh keyframes
                for m in (self.session_msg, self.map_msg):
                    if m:
                        await ws.send(json.dumps(m, separators=(",", ":")))
                self.delta.reset()
                self._feed_full_due = True

    async def run(self):
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
