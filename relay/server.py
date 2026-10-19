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
import gzip
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

SNAPSHOT_TYPES = ("session", "map", "state", "cars", "hello", "feed", "director", "livery_status",
                  "camera_packs", "refgaps")

# Messages a viewer may have queued before it is judged too slow and dropped.
# (See the figure below for how many seconds of stream that is.) Each viewer drains its own queue, so a
# phone that backgrounds its tab or loses signal can no longer stall the
# fan-out -- measured before this existed: one viewer that stopped reading took
# every other viewer from 20 msg/s to 0 within 5 s. A dropped viewer
# reconnects and gets a fresh snapshot, which a delta stream needs anyway after
# a gap.
# 1000: the suite streams ~80 msg/s since the rates went up (60/30), so 200 was
# only ~2.5 s of slack -- a phone locking its screen for a moment would be
# dropped. 1000 is ~12 s at that rate and ~25 s at the standalone 30/15.
VIEWER_QUEUE = 1000
CLOSE_TOO_SLOW = 4002

# Which viewer role may send which command kind on to the bridge.
# Cars that viewers may follow individually at once (the bridge computes one
# view per car). Matches pitwall.bridge.MAX_REFS.
MAX_REFS = 12

CMD_ROLE = {"pit": "engineer", "camera": "producer", "replay": "producer",
            "director": "producer", "livery": "producer"}

# What a viewer may send in one message. Only the producer uploads files (logos,
# stingers, fonts); anyone else sends a few hundred bytes at most, and a 40 MB
# message from a guest on a share link froze the stream for everyone ~0.1 s.
VIEWER_MAX_BYTES = 64 * 1024

# A viewer's "resend" is answered by the relay from what it holds, to that viewer
# only, at most this often. It used to be forwarded to the bridge, which sent the
# session, the whole race feed and fresh keyframes to EVERY viewer: one guest
# asking 10x a second took everyone from 136 to 695 KB/s, and six hours into a
# race the feed alone was 1 MB per viewer per ask.
RESEND_MIN_S = 5.0

# The race feed (lap history and events) as the bridge keeps it; the relay keeps
# the same, current, so a late joiner gets it without asking the bridge.
FEED_MAX_LAPS, FEED_MAX_EVENTS = 6000, 1500   # pitwall.normalize.Deriver.FEED_MAX_*

# A graphics patch is a few toggles and car indexes; anything bigger is a bug or
# abuse, and the merged state is copied to every viewer.
GFX_MAX_BYTES = 64 * 1024

# Producer uploads (logos, fonts, stinger videos) and the bridge's livery photos.
ASSET_MAX_BYTES = 30 * 1024 * 1024
ASSET_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif", ".svg": "image/svg+xml", ".webm": "video/webm", ".mp4": "video/mp4",
               ".mov": "video/quicktime", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf",
               ".otf": "font/otf"}
LIVERY_MAX = 200
WS_MAX_BYTES = 40 * 1024 * 1024


def default_assets_dir() -> str:
    return os.path.join(os.path.expanduser("~"), ".pitwall", "broadcast_assets")


def safe_asset_name(name) -> str | None:
    """A plain file name with an allowed extension, or None. No folders, no dots
    at the front, nothing that could step outside the assets folder."""
    if not isinstance(name, str):
        return None
    base = os.path.basename(name.replace("\\", "/")).strip()
    stem, ext = os.path.splitext(base)
    if not stem or base.startswith(".") or ext.lower() not in ASSET_TYPES or len(base) > 120:
        return None
    if any(ch in base for ch in '<>:"|?*') or any(ord(ch) < 32 for ch in base):
        return None
    return stem + ext.lower()

# Next to relay/ in the repo; inside the suite's .exe, in the unpacked bundle.
BROADCAST_DIR = os.path.join(getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "broadcast")
# PitWall's dashboard, served here too so crew reaching this relay from outside
# (a VPS, or the suite's "Share online" tunnel) can load it from the same address.
CLIENT_PAGE = os.path.join(os.path.dirname(BROADCAST_DIR), "client", "index.html")


def page_response(request, body: bytes) -> Response:
    """An HTML page, compressed for any browser that accepts it. The dashboard is
    ~160 KB: compressed it loads faster over a share link, and it stays under
    ~128 KB on the wire, the size above which uncompressed local web replies
    stalled on a PC whose security software inspects them (seen with Norton 360)."""
    headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-cache")]
    if "gzip" in (request.headers.get("Accept-Encoding") or "").lower():
        body = gzip.compress(body, compresslevel=6, mtime=0)
        headers += [("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding")]
    return Response(200, "OK", Headers(headers + [("Content-Length", str(len(body)))]), body)
CLIENT_PATHS = ("/pitwall/", "/pitwall", "/pitwall/index.html")
# The dashboard's fonts (client/assets): served by exact name, nothing else in that folder.
CLIENT_ASSETS = os.path.join(os.path.dirname(CLIENT_PAGE), "assets")
CLIENT_ASSET_TYPES = {".woff2": "font/woff2"}


def client_asset(path: str):
    """(body, content type) for /pitwall/assets/<name>, or None. Only plain names
    with an allowed extension, read from client/assets; never a path."""
    if not path.startswith("/pitwall/assets/"):
        return None
    name = path[len("/pitwall/assets/"):]
    ext = os.path.splitext(name)[1].lower()
    if not name or ext not in CLIENT_ASSET_TYPES or not all(ch.isalnum() or ch in "-_." for ch in name) or name.startswith("."):
        return None
    try:
        with open(os.path.join(CLIENT_ASSETS, name), "rb") as f:
            return f.read(), CLIENT_ASSET_TYPES[ext]
    except OSError:
        return None
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
                 producer_token: str | None = None, assets_dir: str | None = None):
        self.assets_dir = assets_dir or default_assets_dir()
        self.liveries: dict[str, str] = {}      # key -> raw "livery" message, newest kept
        self.bridge_token = bridge_token
        self.view_token = view_token
        self.engineer_token = engineer_token
        self.producer_token = producer_token
        # A second, revocable watch-only token for links handed out while
        # sharing online. Setting a new one (or None) disconnects its holders.
        self.guest_token: str | None = None
        # Optional Lap Analyzer feed (set by the iRacing Suite): an async callable
        # taking the WebSocket of a viewer at /analyzer/ws, and the page to serve at
        # /analyzer/. Any viewer token works; it is read-only.
        self.analyzer_feed = None
        self.analyzer_page: str | None = None
        # Or a callable(path) -> (body, content type) | None that serves the page and its
        # pieces (the suite's, which splits a large page under the stall size).
        self.analyzer_serve = None
        self._guests: set = set()
        # The car each viewer follows, for that viewer alone. Choosing one never
        # changes what anyone else sees: the bridge computes a view per chosen car
        # and only that car's viewers are sent it.
        self.view_ref: dict = {}     # ws -> carIdx
        self.gfx: dict = {}
        self.gfx_rev = 0
        self.bridge = None
        self.viewers: dict = {}      # ws -> asyncio.Queue of outgoing frames
        self.snapshot: dict[str, str] = {}
        self.stats = {"in": 0, "out": 0, "dropped_slow": 0, "started": time.time()}
        self.feed: dict | None = None          # the race feed, kept current from the bridge's deltas
        self._feed_raw: str | None = None      # its JSON, made once per change
        # Bytes actually written to the network for share-link guests (after
        # compression -- message sizes overstate it ~5x), for the Share panel.
        self.guest_bytes = 0
        self._guest_rate = (time.monotonic(), 0, 0.0)

    async def _fanout(self, raw: str, only=None):
        # Never awaits a viewer: each one has its own queue and writer task.
        for ws, q in list(self.viewers.items()):
            if only is not None and not only(ws):
                continue
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

    async def _send_refs(self, fresh=None):
        """Tell the bridge which cars viewers follow. `fresh`: just chosen, so
        the bridge sends that car's next view whole, not as a delta."""
        if self.bridge is not None:
            await self._to_bridge(json.dumps({"type": "refs", "idxs": sorted(set(self.view_ref.values())),
                                              "fresh": fresh}))

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
        if self.view_ref:            # a new bridge starts with none; tell it who follows what
            await self._send_refs()
        try:
            async for raw in ws:
                self.stats["in"] += 1
                try:
                    obj = json.loads(raw)
                    t = obj.get("type")
                except Exception:
                    continue
                if t == "refview":
                    # Measured from one viewer-chosen car: only its viewers get it.
                    ref = obj.get("ref")
                    await self._fanout(raw, only=lambda v, ref=ref: self.view_ref.get(v) == ref)
                    await asyncio.sleep(0)
                    continue
                if t == "feed":
                    self._merge_feed(obj)
                elif t == "refgaps":
                    self._merge_refgaps(obj)
                elif t in SNAPSHOT_TYPES and (t not in ("state", "cars") or msg_is_full(raw)):
                    self.snapshot[t] = raw
                elif t == "livery":
                    # One photo per car, kept so an OBS source that reloads gets them all.
                    try:
                        key = str(json.loads(raw).get("key"))
                    except Exception:
                        key = None
                    if key:
                        self.liveries.pop(key, None)
                        self.liveries[key] = raw
                        while len(self.liveries) > LIVERY_MAX:
                            self.liveries.pop(next(iter(self.liveries)))
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

    def _count_wire(self, ws):
        """Count what this connection really sends. websockets writes every frame
        through transport.write, after permessage-deflate, so wrapping it per
        connection measures the network cost and nothing else."""
        transport = ws.transport
        write = transport.write

        def counted(data, _write=write):
            self.guest_bytes += len(data)
            _write(data)
        try:
            transport.write = counted
        except (AttributeError, TypeError):
            pass                          # a transport that will not take it: just not counted

    def guest_usage(self) -> dict:
        """Share-link viewers and the upload they use, averaged over >= 2 s."""
        now = time.monotonic()
        t0, b0, rate = self._guest_rate
        if now - t0 >= 2.0:
            rate = (self.guest_bytes - b0) / (now - t0)
            self._guest_rate = (now, self.guest_bytes, rate)
        return {"guests": len(self._guests), "upBytesPerS": round(rate)}

    def _merge_feed(self, obj: dict):
        """The feed is append-only: a full copy replaces, a delta's new entries are
        added. Kept current so a late joiner's snapshot is the race so far, not the
        feed as it was the last time somebody asked the bridge for it."""
        if obj.get("full"):
            self.feed = {"type": "feed", "full": True, "seq": obj.get("seq") or 0,
                         "laps": list(obj.get("laps") or []), "events": list(obj.get("events") or [])}
        elif self.feed is not None:
            self.feed["laps"].extend(obj.get("laps") or [])
            self.feed["events"].extend(obj.get("events") or [])
            del self.feed["laps"][:-FEED_MAX_LAPS]
            del self.feed["events"][:-FEED_MAX_EVENTS]
            self.feed["seq"] = max(self.feed["seq"], obj.get("seq") or 0)
        else:
            return                      # a delta before any full copy: nothing to add it to
        self._feed_raw = None

    def _merge_refgaps(self, obj: dict):
        """Gap series to the shared reference: whole when it changes, then only new
        points, which are kept here so a late joiner gets the whole chart."""
        cur = self.snapshot.get("refgaps")
        base = json.loads(cur) if cur else None
        if obj.get("full") or base is None or base.get("ref") != obj.get("ref"):
            if not obj.get("full"):
                return                      # points with nothing to add them to
            base = {"type": "refgaps", "full": True, "ref": obj.get("ref"), "series": {}}
            base["series"] = dict(obj.get("series") or {})
        else:
            for k, pts in (obj.get("series") or {}).items():
                s = base["series"].setdefault(k, [])
                s.extend(pts)
                del s[:-200]                # as many laps as the bridge keeps per car
        self.snapshot["refgaps"] = json.dumps(base, separators=(",", ":"))

    def _feed_message(self) -> str | None:
        if self.feed is None:
            return None
        if self._feed_raw is None:
            self._feed_raw = json.dumps(self.feed, separators=(",", ":"))
        return self._feed_raw

    def _queue_snapshot(self, q: asyncio.Queue):
        """Everything a page needs to start, in the order it needs it."""
        for t in ("hello", "session", "map", "state", "cars", "feed", "director", "livery_status", "refgaps", "camera_packs"):
            raw = self._feed_message() if t == "feed" else self.snapshot.get(t)
            if raw:
                q.put_nowait(raw)
        q.put_nowait(self._gfx_message())
        q.put_nowait(self._assets_message())

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
        self._queue_snapshot(q)
        snapshot_at = time.monotonic()
        self.viewers[ws] = q
        if role != "producer":
            # Only the producer uploads files; see VIEWER_MAX_BYTES.
            ws.protocol.max_message_size = VIEWER_MAX_BYTES
        if role == "guest":
            self._guests.add(ws)
            self._count_wire(ws)
        # Livery photos are large; they go after the live stream has started,
        # through the same queue, a few at a time so a slow link is not dropped.
        pending_liveries = list(self.liveries.values())
        writer = asyncio.ensure_future(self._writer(ws, q))

        async def send_liveries():
            for raw in pending_liveries:
                while q.qsize() > VIEWER_QUEUE // 2:
                    await asyncio.sleep(0.05)
                try:
                    q.put_nowait(raw)
                except asyncio.QueueFull:
                    return
        lv_task = asyncio.ensure_future(send_liveries())
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
                if msg.get("type") in ("asset_put", "asset_del"):
                    if role != "producer":
                        reply({"type": "asset_result", "ok": False, "error": "only the producer token can change assets"})
                    else:
                        r = self._asset_op(msg)
                        reply(r)
                        if r.get("ok"):
                            await self._fanout(self._assets_message())
                elif msg.get("type") == "gfx":
                    if role != "producer":
                        reply({"type": "gfx_result", "ok": False, "error": "only the producer token can change graphics"})
                    elif not isinstance(msg.get("set"), dict) or len(raw) > GFX_MAX_BYTES:
                        reply({"type": "gfx_result", "ok": False, "error": "a gfx message needs a small 'set' object"})
                    else:
                        # Merge into a copy: a patch that would push the state over
                        # the limit is refused. It used to replace the state with {},
                        # which took every graphic off air in the middle of a show.
                        merged = merge_gfx(json.loads(json.dumps(self.gfx)), msg["set"])
                        if len(json.dumps(merged)) > GFX_MAX_BYTES:
                            reply({"type": "gfx_result", "ok": False,
                                   "error": "graphics state would exceed %d KB; patch refused" % (GFX_MAX_BYTES // 1024)})
                            continue
                        self.gfx = merged
                        self.gfx_rev += 1
                        await self._fanout(self._gfx_message(by=msg.get("by") if isinstance(msg.get("by"), str) else None))
                elif msg.get("type") == "cmd":
                    needs = CMD_ROLE.get(msg.get("cmd"))
                    if role != needs:
                        why = ("this connection is view-only" if role in ("view", "guest") else
                               f"{msg.get('cmd')!r} commands need the {needs or 'right'} token")
                        reply({"type": "cmd_result", "ok": False, "echo": msg, "error": why})
                    elif self.bridge is None:
                        reply({"type": "cmd_result", "ok": False, "echo": msg, "error": "no bridge connected"})
                    else:
                        await self._to_bridge(raw)
                elif msg.get("type") == "resend":
                    # From what the relay holds, to this viewer alone. A page asks
                    # right after connecting, when it has just been sent all of it.
                    if time.monotonic() - snapshot_at >= RESEND_MIN_S:
                        snapshot_at = time.monotonic()
                        try:
                            self._queue_snapshot(q)
                        except asyncio.QueueFull:
                            pass       # already being dropped for falling behind
                elif msg.get("type") == "focus":
                    # Which car THIS viewer follows. Any role may choose, guests
                    # included, because it changes nothing for anyone else.
                    idx = msg.get("idx")
                    if not (idx is None or (isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < 64)):
                        continue
                    if idx is not None and idx not in self.view_ref.values() and                             len(set(self.view_ref.values())) >= MAX_REFS:
                        reply({"type": "focus_result", "ok": False, "idx": idx,
                               "error": f"{MAX_REFS} cars are already being followed on this relay; "
                                        "follow one of those, or automatic"})
                        continue
                    if idx is None:
                        self.view_ref.pop(ws, None)
                    else:
                        self.view_ref[ws] = idx
                    reply({"type": "focus_result", "ok": True, "idx": idx})
                    await self._send_refs(fresh=idx)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            writer.cancel()
            lv_task.cancel()
            self.viewers.pop(ws, None)
            self._guests.discard(ws)
            if self.view_ref.pop(ws, None) is not None:
                await self._send_refs()
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
        elif parsed.path == "/view" and self.guest_token and token_ok(token, self.guest_token):
            await self.handle_viewer(ws, role="guest")
        elif parsed.path == "/view" and token_ok(token, self.engineer_token):
            await self.handle_viewer(ws, role="engineer")
        elif parsed.path == "/view" and token_ok(token, self.producer_token):
            await self.handle_viewer(ws, role="producer")
        elif parsed.path == "/analyzer/ws" and self.analyzer_feed and self._role(token):
            await self.handle_analyzer(ws, self._role(token))
        else:
            await ws.close(code=4001, reason="bad path or token")

    def _role(self, token: str) -> str | None:
        for role, expected in (("view", self.view_token), ("guest", self.guest_token),
                               ("engineer", self.engineer_token), ("producer", self.producer_token)):
            if expected and token_ok(token, expected):
                return role
        return None

    async def handle_analyzer(self, ws, role: str):
        """The Lap Analyzer's live feed (telemetry and the session with the car
        setup), read-only. Guests are counted and dropped with the share like any
        other share-link viewer."""
        if role == "guest":
            self._guests.add(ws)
            self._count_wire(ws)
        ws.protocol.max_message_size = VIEWER_MAX_BYTES
        print(f"[relay] analyzer viewer joined ({role})")
        try:
            await self.analyzer_feed(ws)
        finally:
            self._guests.discard(ws)

    def set_guest_token(self, token: str | None):
        """Replace the guest token; everyone who joined with the old one is
        disconnected. Call on the relay's event loop."""
        self.guest_token = token or None
        for ws in list(self._guests):
            self._guests.discard(ws)
            self.viewers.pop(ws, None)
            asyncio.ensure_future(ws.close(code=4003, reason="sharing stopped"))

    # ------------------------------------------------------------ assets
    def _asset_list(self) -> list[dict]:
        try:
            names = sorted(os.listdir(self.assets_dir))
        except OSError:
            return []
        out = []
        for n in names:
            if safe_asset_name(n) == n:
                try:
                    out.append({"name": n, "bytes": os.path.getsize(os.path.join(self.assets_dir, n)),
                                "type": ASSET_TYPES[os.path.splitext(n)[1].lower()]})
                except OSError:
                    pass
        return out

    def _assets_message(self) -> str:
        return json.dumps({"type": "assets", "files": self._asset_list()})

    def _asset_op(self, msg: dict) -> dict:
        import base64
        name = safe_asset_name(msg.get("name"))
        if not name:
            return {"type": "asset_result", "ok": False,
                    "error": "use a plain file name ending in " + ", ".join(sorted(ASSET_TYPES))}
        path = os.path.join(self.assets_dir, name)
        if msg["type"] == "asset_del":
            try:
                os.remove(path)
            except OSError as e:
                return {"type": "asset_result", "ok": False, "error": str(e)}
            return {"type": "asset_result", "ok": True, "name": name, "deleted": True}
        data = msg.get("data")
        if isinstance(data, str) and data.startswith("data:"):
            data = data.split(",", 1)[-1]
        try:
            blob = base64.b64decode(data or "", validate=True)
        except Exception:
            return {"type": "asset_result", "ok": False, "error": "data must be base64"}
        if not blob or len(blob) > ASSET_MAX_BYTES:
            return {"type": "asset_result", "ok": False, "error": f"files must be under {ASSET_MAX_BYTES // 2**20} MB"}
        os.makedirs(self.assets_dir, exist_ok=True)
        with open(path + ".part", "wb") as f:
            f.write(blob)
        os.replace(path + ".part", path)
        return {"type": "asset_result", "ok": True, "name": name, "bytes": len(blob)}

    async def process_request(self, connection, request):
        if request.path.startswith("/health"):
            body = json.dumps({"bridge": self.bridge is not None, "viewers": len(self.viewers),
                               "uptime_s": int(time.time() - self.stats["started"]), **{k: v for k, v in self.stats.items() if k != "started"}})
            return connection.respond(http.HTTPStatus.OK, body + "\n")
        path = request.path.split("?")[0]
        if path.startswith("/broadcast/assets/") and request.headers.get("Upgrade") is None:
            name = safe_asset_name(urllib.parse.unquote(path[len("/broadcast/assets/"):]))
            full = os.path.join(self.assets_dir, name) if name else None
            if not full or not os.path.isfile(full):
                return connection.respond(http.HTTPStatus.NOT_FOUND, "no such asset\n")
            with open(full, "rb") as f:
                body = f.read()
            headers = Headers([("Content-Type", ASSET_TYPES[os.path.splitext(name)[1].lower()]),
                               ("Content-Length", str(len(body))), ("Cache-Control", "no-cache"),
                               ("Access-Control-Allow-Origin", "*"),
                               # Uploads are served from the same origin as the
                               # producer page; an uploaded SVG opened directly must
                               # not be able to run script there.
                               ("Content-Security-Policy", "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"),
                               ("X-Content-Type-Options", "nosniff")])
            return Response(200, "OK", headers, body)
        if path.startswith("/pitwall/assets/") and request.headers.get("Upgrade") is None:
            got = client_asset(path)
            if not got:
                return connection.respond(http.HTTPStatus.NOT_FOUND, "not found\n")
            body, ctype = got
            # fonts are already compressed; they change only with the app, so a day's cache is fine
            return Response(200, "OK", Headers([("Content-Type", ctype), ("Cache-Control", "max-age=86400"),
                                                ("Content-Length", str(len(body)))]), body)
        if path in CLIENT_PATHS and request.headers.get("Upgrade") is None:
            try:
                with open(CLIENT_PAGE, "rb") as f:
                    body = f.read()
            except OSError:
                return connection.respond(http.HTTPStatus.NOT_FOUND, "the PitWall page is not installed\n")
            return page_response(request, body)
        if self.analyzer_serve and path.startswith("/analyzer") and request.headers.get("Upgrade") is None:
            got = self.analyzer_serve(path)
            if got:
                body, ctype = got
                if ctype.startswith("text/html"):
                    return page_response(request, body)
                headers = [("Content-Type", ctype), ("Cache-Control", "no-cache")]
                if "gzip" in (request.headers.get("Accept-Encoding") or "").lower():
                    body = gzip.compress(body, compresslevel=6, mtime=0)
                    headers += [("Content-Encoding", "gzip"), ("Vary", "Accept-Encoding")]
                return Response(200, "OK", Headers(headers + [("Content-Length", str(len(body)))]), body)
        if path in ("/analyzer/", "/analyzer", "/analyzer/index.html") and self.analyzer_page and request.headers.get("Upgrade") is None:
            try:
                with open(self.analyzer_page, "rb") as f:
                    body = f.read()
            except OSError:
                return connection.respond(http.HTTPStatus.NOT_FOUND, "the Lap Analyzer page is not installed\n")
            return page_response(request, body)
        page = BROADCAST_PAGES.get(path)
        if page and request.headers.get("Upgrade") is None:
            try:
                with open(os.path.join(BROADCAST_DIR, page), "rb") as f:
                    body = f.read()
            except OSError:
                return connection.respond(http.HTTPStatus.NOT_FOUND, "broadcast pages are not installed\n")
            # No token is needed to load the page itself; it asks for one, and
            # every message still needs one.
            return page_response(request, body)
        return None


async def main_async(args):
    relay = Relay(args.bridge_token, args.view_token, args.engineer_token, args.producer_token,
                  assets_dir=args.assets_dir)
    async with serve(relay.handler, args.host, args.port, process_request=relay.process_request,
                     max_size=WS_MAX_BYTES, ping_interval=20):
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
    p.add_argument("--assets-dir", help="where producer uploads (logos, stingers, fonts) are kept "
                                        "(default ~/.pitwall/broadcast_assets)")
    args = p.parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
