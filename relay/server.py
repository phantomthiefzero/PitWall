"""Pitwall relay. Runs anywhere with a public address (cheap VPS, Fly, Railway).

    python relay/server.py --port 8765 --bridge-token SECRET1 --view-token SECRET2 [--engineer-token SECRET3]

Paths:
    /bridge?token=BRIDGE_TOKEN     exactly one bridge (the driver's PC). A new one replaces the old.
    /view?token=VIEW_TOKEN         any number of dashboards. Get a snapshot on connect.

Viewer -> bridge messages are only forwarded when the viewer authenticated
with the ENGINEER token; the view token is read-only. Everything else is a
dumb fan-out: the relay never interprets telemetry.
"""
from __future__ import annotations

import argparse
import asyncio
import hmac
import http
import json
import time
import urllib.parse

import websockets
from websockets.asyncio.server import serve

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


def token_ok(given: str, expected: str | None) -> bool:
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


def msg_is_full(raw: str) -> bool:
    # cheap check without a second json.loads on every frame
    return '"full":true' in raw[:60] or '"full": true' in raw[:60]


class Relay:
    def __init__(self, bridge_token: str, view_token: str, engineer_token: str | None):
        self.bridge_token = bridge_token
        self.view_token = view_token
        self.engineer_token = engineer_token
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

    async def handle_viewer(self, ws, engineer: bool):
        q: asyncio.Queue = asyncio.Queue(maxsize=VIEWER_QUEUE)
        # Snapshot and status go through the same queue as the live stream, so
        # they arrive in order and the writer task is the only thing sending.
        q.put_nowait(json.dumps({"type": "relay", "bridge": "connected" if self.bridge else "disconnected",
                                 "engineer": engineer, "viewers": len(self.viewers) + 1}))
        for t in ("hello", "session", "map", "state", "cars", "feed"):
            if t in self.snapshot:
                q.put_nowait(self.snapshot[t])
        self.viewers[ws] = q
        writer = asyncio.ensure_future(self._writer(ws, q))
        print(f"[relay] viewer connected ({'engineer' if engineer else 'view'}), {len(self.viewers)} total")

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
                if msg.get("type") == "cmd":
                    if not engineer:
                        reply({"type": "cmd_result", "ok": False, "echo": msg, "error": "this connection is view-only"})
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
            await self.handle_viewer(ws, engineer=True)
        else:
            await ws.close(code=4001, reason="bad path or token")

    async def process_request(self, connection, request):
        if request.path.startswith("/health"):
            body = json.dumps({"bridge": self.bridge is not None, "viewers": len(self.viewers),
                               "uptime_s": int(time.time() - self.stats["started"]), **{k: v for k, v in self.stats.items() if k != "started"}})
            return connection.respond(http.HTTPStatus.OK, body + "\n")
        return None


async def main_async(args):
    relay = Relay(args.bridge_token, args.view_token, args.engineer_token)
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
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
