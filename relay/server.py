"""Pitwall relay. Runs anywhere with a public address (cheap VPS, Fly, Railway).

    python relay/server.py --port 8765 --bridge-token SECRET1 --view-token SECRET2 [--engineer-token SECRET3]
                           [--producer-token SECRET4]

Paths:
    /bridge?token=BRIDGE_TOKEN     exactly one bridge (the driver's PC). A new one replaces the old.
    /view?token=VIEW_TOKEN         any number of dashboards. Get a snapshot on connect.

Viewer -> bridge messages are only forwarded from the token that owns them:
    engineer token   pit-service commands
    producer token   camera and replay commands, and broadcast graphics cues
The view token is read-only. Everything else is a dumb fan-out: the relay
never interprets telemetry.

Broadcast graphics state ("gfx") lives HERE rather than in the bridge, because
it belongs to the people producing the show, not to the sim: a producer sends a
patch, the relay merges it into one shared state and sends the whole state to
every viewer. Two producers (you and a co-host) therefore always see the same
thing, and an OBS browser source that reloads mid-race gets the current state
on connect. The broadcast pages are served at /broadcast/overlay.html and
/broadcast/producer.html so a co-host needs nothing but a link.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import http
import json
import time
import urllib.parse

import os
import sys

import websockets
from websockets.asyncio.server import serve
from websockets.datastructures import Headers
from websockets.http11 import Response

SNAPSHOT_TYPES = ("session", "map", "state", "cars", "hello", "feed")

# Messages a viewer may have queued before it is judged too slow and dropped.
# At ~20 msg/s this is ~10 s of stream. Each viewer drains its own queue, so a
# phone that backgrounds its tab or loses signal can no longer stall the
# fan-out -- measured before this existed: one viewer that stopped reading took
# every other viewer from 20 msg/s to 0 within 5 s. A dropped viewer
# reconnects and gets a fresh snapshot, which a delta stream needs anyway after
# a gap.
VIEWER_QUEUE = 200
CLOSE_TOO_SLOW = 4002

# Which viewer role may send which command kind on to the bridge.
CMD_ROLE = {"pit": "engineer", "camera": "producer", "replay": "producer"}

# A graphics patch is a few toggles and car indexes; anything bigger is a bug or
# abuse, and the merged state is copied to every viewer.
GFX_MAX_BYTES = 16 * 1024

# Next to relay/ in the repo; inside the suite's .exe, in the unpacked bundle.
BROADCAST_DIR = os.path.join(getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "broadcast")
BROADCAST_PAGES = {"/broadcast/overlay.html": "overlay.html", "/broadcast/producer.html": "producer.html",
                   "/broadcast/": "producer.html", "/broadcast": "producer.html"}


def token_ok(given: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def merge_gfx(into: dict, patch: dict) -> dict:
    """Deep merge; a None value deletes the key. Lists replace."""
    for k, v in patch.items():
        if not isinstance(k, str):
            continue
        if v is None:
            into.pop(k, None)
        elif isinstance(v, dict) and isinstance(into.get(k), dict):
            merge_gfx(into[k], v)
        elif isinstance(v, dict):
            into[k] = merge_gfx({}, v)
        else:
            into[k] = v
    return into


def msg_is_full(raw: str) -> bool:
    # cheap check without a second json.loads on every frame
    return '"full":true' in raw[:60] or '"full": true' in raw[:60]


class Relay:
    def __init__(self, bridge_token: str, view_token: str, engineer_token: str | None,
                 producer_token: str | None = None):
        self.bridge_token = bridge_token
        self.view_token = view_token
        self.engineer_token = engineer_token
        self.producer_token = producer_token
        self.gfx: dict = {}
        self.gfx_rev = 0
        self.bridge = None
        self.viewers: dict = {}      # ws -> asyncio.Queue of outgoing frames
        self.snapshot: dict[str, str] = {}
        self.stats = {"in": 0, "out": 0, "dropped_slow": 0, "started": time.time()}

    async def _fanout(self, raw: str):
        # Never awaits a viewer: each one has its own queue and writer task.
        for ws, q in list(self.viewers.items()):
            try:
                q.put_nowait(raw)
            except asyncio.QueueFull:
                self.viewers.pop(ws, None)
                self.stats["dropped_slow"] += 1
                print(f"[relay] viewer {ws.remote_address} fell behind; disconnecting it")
                asyncio.ensure_future(ws.close(code=CLOSE_TOO_SLOW, reason="viewer fell behind"))

    async def _writer(self, ws, q: asyncio.Queue):
        try:
            while True:
                raw = await q.get()
                await ws.send(raw)
                self.stats["out"] += 1
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _to_bridge(self, raw: str):
        # The bridge closing between the check and the send is the bridge's
        # problem, not this viewer's: do not let it end the viewer's session.
        try:
            await self.bridge.send(raw)
        except (AttributeError, websockets.exceptions.ConnectionClosed):
            pass

    async def handle_bridge(self, ws):
        if self.bridge is not None:
            try:
                await self.bridge.close(code=4000, reason="replaced by a new bridge")
            except Exception:
                pass
        self.bridge = ws
        print(f"[relay] bridge connected from {ws.remote_address}")
        await self._fanout(json.dumps({"type": "relay", "bridge": "connected"}))
        try:
            async for raw in ws:
                self.stats["in"] += 1
                try:
                    t = json.loads(raw).get("type")
                except Exception:
                    continue
                if t in SNAPSHOT_TYPES and (t not in ("state", "cars", "feed") or msg_is_full(raw)):
                    self.snapshot[t] = raw
                await self._fanout(raw)
                # Messages already buffered from the bridge are read without
                # suspending, so without this a burst never lets the viewer
                # writers run and every queue fills, healthy viewers included.
                await asyncio.sleep(0)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            if self.bridge is ws:
                self.bridge = None
                await self._fanout(json.dumps({"type": "relay", "bridge": "disconnected"}))
            print("[relay] bridge disconnected")

    def _gfx_message(self, by: str | None = None) -> str:
        return json.dumps({"type": "gfx", "rev": self.gfx_rev, "state": self.gfx, "by": by})

    async def handle_viewer(self, ws, engineer: bool = False, role: str | None = None):
        role = role or ("engineer" if engineer else "view")
        engineer = role == "engineer"
        q: asyncio.Queue = asyncio.Queue(maxsize=VIEWER_QUEUE)
        # Snapshot and status go through the same queue as the live stream, so
        # they arrive in order and the writer task is the only thing sending.
        q.put_nowait(json.dumps({"type": "relay", "bridge": "connected" if self.bridge else "disconnected",
                                 "engineer": engineer, "role": role, "viewers": len(self.viewers) + 1}))
        for t in ("hello", "session", "map", "state", "cars", "feed"):
            if t in self.snapshot:
                q.put_nowait(self.snapshot[t])
        q.put_nowait(self._gfx_message())
        self.viewers[ws] = q
        writer = asyncio.ensure_future(self._writer(ws, q))
        print(f"[relay] viewer connected ({role}), {len(self.viewers)} total")

        def reply(obj):
            try:
                q.put_nowait(json.dumps(obj))
            except asyncio.QueueFull:
                pass   # already being dropped for falling behind
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "gfx":
                    if role != "producer":
                        reply({"type": "gfx_result", "ok": False, "error": "only the producer token can change graphics"})
                    elif not isinstance(msg.get("set"), dict) or len(raw) > GFX_MAX_BYTES:
                        reply({"type": "gfx_result", "ok": False, "error": "a gfx message needs a small 'set' object"})
                    else:
                        merge_gfx(self.gfx, msg["set"])
                        if len(json.dumps(self.gfx)) > GFX_MAX_BYTES:
                            self.gfx = {}
                        self.gfx_rev += 1
                        await self._fanout(self._gfx_message(by=msg.get("by") if isinstance(msg.get("by"), str) else None))
                elif msg.get("type") == "cmd":
                    needs = CMD_ROLE.get(msg.get("cmd"))
                    if role != needs:
                        why = ("this connection is view-only" if role == "view" else
                               f"{msg.get('cmd')!r} commands need the {needs or 'right'} token")
                        reply({"type": "cmd_result", "ok": False, "echo": msg, "error": why})
                    elif self.bridge is None:
                        reply({"type": "cmd_result", "ok": False, "echo": msg, "error": "no bridge connected"})
                    else:
                        await self._to_bridge(raw)
                elif msg.get("type") == "resend" and self.bridge is not None:
                    await self._to_bridge(raw)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            writer.cancel()
            self.viewers.pop(ws, None)
            print(f"[relay] viewer left, {len(self.viewers)} total")

    async def handler(self, ws):
        path = ws.request.path
        parsed = urllib.parse.urlparse(path)
        q = urllib.parse.parse_qs(parsed.query)
        token = (q.get("token") or [""])[0]
        if parsed.path == "/bridge" and token_ok(token, self.bridge_token):
            await self.handle_bridge(ws)
        elif parsed.path == "/view" and token_ok(token, self.view_token):
            await self.handle_viewer(ws, engineer=False)
        elif parsed.path == "/view" and token_ok(token, self.engineer_token):
            await self.handle_viewer(ws, role="engineer")
        elif parsed.path == "/view" and token_ok(token, self.producer_token):
            await self.handle_viewer(ws, role="producer")
        else:
            await ws.close(code=4001, reason="bad path or token")

    async def process_request(self, connection, request):
        if request.path.startswith("/health"):
            body = json.dumps({"bridge": self.bridge is not None, "viewers": len(self.viewers),
                               "uptime_s": int(time.time() - self.stats["started"]), **{k: v for k, v in self.stats.items() if k != "started"}})
            return connection.respond(http.HTTPStatus.OK, body + "\n")
        page = BROADCAST_PAGES.get(request.path.split("?")[0])
        if page and request.headers.get("Upgrade") is None:
            try:
                with open(os.path.join(BROADCAST_DIR, page), "rb") as f:
                    body = f.read()
            except OSError:
                return connection.respond(http.HTTPStatus.NOT_FOUND, "broadcast pages are not installed\n")
            # No token is needed to load the page itself; it asks for one, and
            # every message still needs one.
            headers = Headers([("Content-Type", "text/html; charset=utf-8"),
                               ("Content-Length", str(len(body))), ("Cache-Control", "no-cache")])
            return Response(200, "OK", headers, body)
        return None


async def main_async(args):
    relay = Relay(args.bridge_token, args.view_token, args.engineer_token, args.producer_token)
    async with serve(relay.handler, args.host, args.port, process_request=relay.process_request,
                     max_size=8 * 1024 * 1024, ping_interval=20):
        print(f"[relay] listening on ws://{args.host}:{args.port}  (health: http://{args.host}:{args.port}/health)")
        await asyncio.Future()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--bridge-token", required=True)
    p.add_argument("--view-token", required=True)
    p.add_argument("--engineer-token", help="viewers with this token may send pit commands (phase 2)")
    p.add_argument("--producer-token", help="viewers with this token may cut cameras and run broadcast graphics")
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
