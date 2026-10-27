"""
Built-in iRacing bridge for the suite.

Reads iRacing's live telemetry (or replays an .ibt file) and serves it to the
suite's pages over a local connection on 127.0.0.1 only. Read-only: it never
changes anything in iRacing.
"""
import asyncio
import collections
import base64
import gzip
import hashlib
import json
import math
import mmap
import os
import struct
import sys
import threading
import time

IRSDK_ERROR = None
try:
    import irsdk  # pyirsdk
except Exception as e:  # not installed or failed to load
    irsdk = None
    IRSDK_ERROR = "%s: %s" % (type(e).__name__, e)

from suite import __version__ as VERSION


def _base():
    """Folder that holds suite/ and client/: the repo in development, the unpacked bundle in the .exe."""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def web_root():
    return os.path.join(_base(), "suite", "web")


def pitwall_client_root():
    return os.path.join(_base(), "client")


# Shown on PitWall's dashboard when it runs inside the suite, so the app window has a way home.
# Added while serving; PitWall's client/index.html itself is not modified.
# The way back to the suite home, added to PitWall's page when the suite serves it. It goes at
# the start of the page's footer bar (it used to float over the bottom of the panels and covered
# the last race-control line); a page without a footer keeps it floating in the corner.
SUITE_LINK = (b'<a id="suiteBack" href="/" style="position:fixed;left:10px;bottom:46px;z-index:99999;padding:3px 10px;'
              b'border-radius:7px;background:rgba(20,22,26,.85);color:#e8e9eb;font:13px Segoe UI,system-ui,sans-serif;'
              b'text-decoration:none;border:1px solid rgba(255,255,255,.18);white-space:nowrap">&larr; Suite</a>'
              b'<script>(function(){var a=document.getElementById("suiteBack"),f=document.querySelector("footer");'
              b'if(a&&f){a.style.position="static";f.insertBefore(a,f.firstChild)}})()</script>')


def clean(x):
    """Make a telemetry value safe for JSON."""
    if isinstance(x, bool):
        return x
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return round(x, 5)
    if isinstance(x, (bytes, bytearray)):
        return x[0] if len(x) else None
    return x


# Every car's position, lap and drivetrain, sent on every other update (30 a second
# at the suite's rate) so the Lap Analyzer can record any driver's laps as a
# reference. iRacing gives other cars' position, lap, gear, rpm and steering, but
# not their pedals, speed, g-forces or GPS.
CARIDX_SEND = {"CarIdxLapDistPct": 6, "CarIdxLap": None, "CarIdxGear": None, "CarIdxRPM": 0,
               "CarIdxSteer": 3, "CarIdxOnPitRoad": None, "CarIdxTrackSurface": None, "CarIdxLastLapTime": 3,
               # the race order and each car's own timing: Live telemetry's gaps to the car ahead and behind
               "CarIdxPosition": None, "CarIdxEstTime": 2}
CARIDX_EVERY = 2


def car_array(values, places):
    """One CarIdx array, safe for JSON and rounded to keep each update small."""
    out = []
    for x in values:
        if isinstance(x, float) and not isinstance(x, bool):
            if math.isnan(x) or math.isinf(x):
                x = None
            elif places is not None:
                x = round(x, places) if places else int(round(x))   # not clean(): it keeps only 5 places
            else:
                x = round(x, 5)
        else:
            x = clean(x)
        out.append(x)
    return out


class LiveSource:
    """Live data from iRacing's shared memory (Windows only)."""
    mode = "live"

    def __init__(self):
        self.ir = irsdk.IRSDK() if irsdk else None
        self.names = None
        self.arrays = None
        self.ticks = 0
        self.last_update = None
        self.next_try = 0.0
        self._starting = None

    def _reset(self):
        self.names = None
        self.arrays = None
        self.last_update = None

    def poll(self):
        ir = self.ir
        if ir is None:
            return False, None, None
        if self._starting is not None:
            if self._starting.is_alive():
                return False, None, None
            self._starting = None
        if not (ir.is_initialized and ir.is_connected):
            if ir.is_initialized:
                ir.shutdown()
                self._reset()
            now = time.monotonic()
            if now < self.next_try:
                return False, None, None
            self.next_try = now + 2.0
            # Looking for the sim asks iRacing's local web port (pyirsdk's startup). With the sim
            # closed, Windows takes a second or more to refuse that connection, and poll() runs on
            # the event loop: every page, the relay and the share link froze for that long every
            # 2 s. So the look happens on its own thread; the next poll after it picks it up.
            def look():
                try:
                    ir.startup()
                except Exception:
                    pass
            self._starting = threading.Thread(target=look, name="iracing-startup", daemon=True)
            self._starting.start()
            return False, None, None
        try:
            ir.freeze_var_buffer_latest()
            if self.names is None:
                self.names = [h.name for h in ir._var_headers if h.count == 1]
                self.arrays = [h.name for h in ir._var_headers if h.name in CARIDX_SEND and h.count > 1]
            values = {}
            for name in self.names:
                try:
                    values[name] = clean(ir[name])
                except Exception:
                    pass
            self.ticks += 1
            if self.ticks % CARIDX_EVERY == 0:
                for name in self.arrays:
                    try:
                        values[name] = car_array(ir[name], CARIDX_SEND[name])
                    except Exception:
                        pass
            yaml = None
            hdr = ir._header
            if hdr.session_info_update != self.last_update:
                self.last_update = hdr.session_info_update
                raw = ir._shared_mem[hdr.session_info_offset:hdr.session_info_offset + hdr.session_info_len]
                raw = bytes(raw).rstrip(b"\x00")
                try:
                    yaml = raw.decode("utf-8")
                except UnicodeDecodeError:
                    yaml = raw.decode("cp1252", errors="replace")
            return True, yaml, values
        except Exception:
            try:
                ir.shutdown()
            except Exception:
                pass
            self._reset()
            return False, None, None
        finally:
            try:
                ir.unfreeze_var_buffer_latest()
            except Exception:
                pass


class ReplaySource:
    """Plays an .ibt telemetry file back as if it were live. Good for testing."""
    mode = "replay"
    FMT = {1: "<?", 2: "<i", 3: "<I", 4: "<f", 5: "<d"}

    def __init__(self, path, speed=1.0, start_lap=None, loop=True, test_setup_change=0):
        self.name = os.path.basename(path)
        self.f = open(path, "rb")
        self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        I = lambda o: struct.unpack_from("<i", self.mm, o)[0]
        self.tick = I(8) or 60
        sl, so, nv, vo = I(16), I(20), I(24), I(28)
        self.bl, self.bo = I(36), I(52)
        self.rows = (len(self.mm) - self.bo) // self.bl
        self.yaml = bytes(self.mm[so:so + sl]).rstrip(b"\x00").decode("latin-1")
        self.vars = []
        lap_var = None
        for i in range(nv):
            p = vo + i * 144
            t, off, cnt = struct.unpack_from("<iii", self.mm, p)
            name = bytes(self.mm[p + 16:p + 48]).split(b"\x00")[0].decode("latin-1")
            if cnt == 1 and t in self.FMT:
                self.vars.append((name, self.FMT[t], off))
                if name == "Lap":
                    lap_var = (self.FMT[t], off)
        self.row0 = 0
        if start_lap is not None and lap_var:
            for r in range(self.rows):
                if struct.unpack_from(lap_var[0], self.mm, self.bo + r * self.bl + lap_var[1])[0] >= start_lap:
                    self.row0 = r
                    break
        self.speed = speed
        self.loop = loop
        self.t0 = None
        self.sent = False
        self.change_every = test_setup_change
        self.first_lap = None
        self.changes = 0

    def poll(self):
        now = time.monotonic()
        if self.t0 is None:
            self.t0 = now
        row = self.row0 + int((now - self.t0) * self.tick * self.speed)
        if row >= self.rows:
            if not self.loop:
                return False, None, None
            self.t0, row = now, self.row0
        base = self.bo + row * self.bl
        values = {n: clean(struct.unpack_from(fmt, self.mm, base + off)[0]) for n, fmt, off in self.vars}
        yaml = None
        if not self.sent:
            yaml, self.sent = self.yaml, True
        if self.change_every and "Lap" in values:
            if self.first_lap is None:
                self.first_lap = values["Lap"]
            due = (values["Lap"] - self.first_lap) // self.change_every
            if due > self.changes:
                # Test aid only: pretend the driver softened the springs in the garage.
                self.changes = due
                yaml = self.yaml.replace("SpringRate: 190 N/mm", "SpringRate: %d N/mm" % (190 - 10 * due))
        return True, yaml, values


WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


class Client:
    """One connected app, speaking the WebSocket protocol."""

    def __init__(self, writer, ticks=True):
        self.w = writer
        self.wants_ticks = ticks    # False: status and session only (the home page needs no telemetry)

    async def send_text(self, text):
        data = text.encode("utf-8")
        n = len(data)
        if n < 126:
            head = bytes([0x81, n])
        elif n < 65536:
            head = bytes([0x81, 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 127]) + struct.pack(">Q", n)
        self.w.write(head + data)
        await self.w.drain()

    async def send_binary(self, data):
        n = len(data)
        head = bytes([0x82, n]) if n < 126 else bytes([0x82, 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x82, 127]) + struct.pack(">Q", n)
        self.w.write(head + data)
        await self.w.drain()

    async def send_ctrl(self, opcode, payload=b""):
        self.w.write(bytes([0x80 | opcode, len(payload)]) + payload)
        await self.w.drain()


class Hub:
    def __init__(self, source, rate):
        self.source = source
        self.rate = rate
        self.clients = set()
        self.connected = False
        self.yaml = None
        self.update = 0
        self.extra = None  # callable adding app-level info (PitWall) to /api/info

    async def send(self, client, obj):
        try:
            await asyncio.wait_for(client.send_text(json.dumps(obj, separators=(",", ":"))), 2.0)
            return True
        except Exception:
            self.clients.discard(client)
            return False

    async def send_all(self, obj):
        if not self.clients:
            return
        text = json.dumps(obj, separators=(",", ":"))
        tick = obj.get("type") == "tick"
        to = [c for c in self.clients if not tick or getattr(c, "wants_ticks", True)]
        if not to:
            return
        # All at once: one slow client must not hold the others (or the pump) up.
        results = await asyncio.gather(*(asyncio.wait_for(c.send_text(text), 1.0) for c in to), return_exceptions=True)
        for c, r in zip(to, results):
            if isinstance(r, BaseException):
                self.clients.discard(c)

    def set_source(self, source):
        """Switch between live iRacing and a replay. Safe to call from the event loop thread."""
        self.source = source
        self.connected = None  # forces a fresh status message
        self.yaml = None

    def info(self):
        return {"mode": self.source.mode, "connected": bool(self.connected), "sdk": irsdk is not None, "sdk_error": IRSDK_ERROR,
                "file": getattr(self.source, "name", None), "clients": len(self.clients),
                **(self.extra() if self.extra else {})}

    async def pump(self):
        period = 1.0 / self.rate
        while True:
            start = time.monotonic()
            try:
                connected, yaml, values = self.source.poll()
            except Exception as e:
                print("Read error:", e)
                connected, yaml, values = False, None, None
            if connected != self.connected:
                self.connected = connected
                await self.send_all({"type": "status", "connected": connected, "source": self.source.mode})
            if yaml:
                self.yaml = yaml
                self.update += 1
                await self.send_all({"type": "session", "yaml": yaml, "update": self.update})
            if values:
                await self.send_all({"type": "tick", "v": values})
                # The home page asks for no telemetry; once a second it gets a glance for its tiles.
                if time.monotonic() >= getattr(self, "_glance_at", 0.0):
                    self._glance_at = time.monotonic() + 1.0
                    g = glance(values)
                    to = [c for c in self.clients if not getattr(c, "wants_ticks", True)]
                    if to:
                        text = json.dumps({"type": "glance", "v": g}, separators=(",", ":"))
                        res = await asyncio.gather(*(asyncio.wait_for(c.send_text(text), 1.0) for c in to), return_exceptions=True)
                        for c, r in zip(to, res):
                            if isinstance(r, BaseException):
                                self.clients.discard(c)
            await asyncio.sleep(max(0.0, period - (time.monotonic() - start)))


GLANCE = ("IsOnTrack", "OnPitRoad", "Speed", "Gear", "Lap", "LapCurrentLapTime", "LapLastLapTime", "LapBestLapTime",
          "PlayerCarPosition", "PlayerCarClassPosition", "SessionFlags", "SessionTimeRemain", "SessionLapsRemainEx",
          "SessionState", "FuelLevel", "IsReplayPlaying")


def glance(values):
    """The few of iRacing's variables the home page's tiles show (copied as they are)."""
    return {k: values.get(k) for k in GLANCE if values.get(k) is not None}


TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".png": "image/png",
         ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2"}


# Text replies are compressed for any browser that accepts it. Besides being
# smaller, it keeps the pages under ~128 KB on the wire: on a PC where security
# software inspects local web traffic (seen with Norton 360), uncompressed web
# replies over that size stalled at exactly 130,560 bytes about 1 time in 70
# and the page hung, while the same page compressed to 65 KB never did in 600.
GZIP_MIN = 1024


def compressible(ctype: str) -> bool:
    return ctype.startswith("text/") or any(k in ctype for k in ("json", "javascript", "svg"))


def accepts_gzip(headers: dict) -> bool:
    return "gzip" in (headers.get("accept-encoding") or "").lower()


async def http_reply(writer, status, body=b"", ctype="text/plain; charset=utf-8", gzip_ok=False):
    extra = ""
    if gzip_ok and len(body) >= GZIP_MIN and compressible(ctype):
        body = gzip.compress(body, compresslevel=6, mtime=0)
        extra = "Content-Encoding: gzip\r\nVary: Accept-Encoding\r\n"
    head = ("HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\n%s"
            "Connection: close\r\n\r\n" % (status, ctype, len(body), extra))
    writer.write(head.encode("latin-1") + body)
    await writer.drain()


async def ws_session(hub, reader, writer, key, ticks=True):
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
    await writer.drain()
    client = Client(writer, ticks)
    await hub.send(client, {"type": "hello", "bridge": VERSION, "mode": hub.source.mode, "rate": hub.rate})
    await hub.send(client, {"type": "status", "connected": bool(hub.connected), "source": hub.source.mode})
    if hub.yaml:
        await hub.send(client, {"type": "session", "yaml": hub.yaml, "update": hub.update})
    hub.clients.add(client)
    try:
        while True:  # read-only: only ping and close are handled
            b0, b1 = await reader.readexactly(2)
            opcode, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", await reader.readexactly(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", await reader.readexactly(8))[0]
            if n > WS_MAX_IN:
                break
            mask = await reader.readexactly(4) if b1 & 0x80 else b"\x00\x00\x00\x00"
            data = bytes(x ^ mask[i % 4] for i, x in enumerate(await reader.readexactly(n)))
            if opcode == 8:
                await client.send_ctrl(8)
                break
            if opcode == 9:
                await client.send_ctrl(10, data[:125])
    except Exception:
        pass
    finally:
        hub.clients.discard(client)


class RemoteClient:
    """A Lap Analyzer viewer reaching this app through PitWall's relay (a share link).

    Sending never waits on the network: the data pump hands every message over and
    one task writes them out. Over the internet telemetry goes at half rate, and when
    the link cannot keep up the oldest telemetry is dropped; session and status
    messages (the setup, the driver list) never are. So a slow viewer cannot slow
    the driver's own screens down.
    """
    TICK_EVERY = 2
    MAX_TICKS = 8

    def __init__(self, ws):
        self.ws = ws
        self.q = collections.deque()
        self.ev = asyncio.Event()
        self.n = 0
        self.ticks = 0
        self.dead = False

    async def send_text(self, text):
        if self.dead:
            raise ConnectionError("viewer gone")
        tick = text.startswith('{"type":"tick"')
        if tick:
            self.n += 1
            # Every car's arrays ride on every other tick (CARIDX_EVERY). Halving by count alone could keep
            # only the ticks without them, for good, depending on when the viewer joined: so the ticks with
            # the arrays are the ones kept, and plain halving is only for a feed that has none.
            if '"CarIdxLapDistPct"' in text:
                self.arr_n = self.n
            elif self.n - getattr(self, "arr_n", -100) <= CARIDX_EVERY + 1 or self.n % self.TICK_EVERY:
                return
            if self.ticks >= self.MAX_TICKS:
                for i, (is_tick, _) in enumerate(self.q):
                    if is_tick:
                        del self.q[i]
                        self.ticks -= 1
                        break
            self.ticks += 1
        self.q.append((tick, text))
        self.ev.set()

    async def run(self):
        while True:
            await self.ev.wait()
            self.ev.clear()
            while self.q:
                tick, text = self.q.popleft()
                if tick:
                    self.ticks -= 1
                await self.ws.send(text)


async def relay_feed(hub, ws):
    """Serve one analyzer viewer on the relay's WebSocket `ws`, the same messages as /ws."""
    c = RemoteClient(ws)
    await c.send_text(json.dumps({"type": "hello", "bridge": VERSION, "mode": hub.source.mode, "rate": hub.rate // RemoteClient.TICK_EVERY, "remote": True}))
    await c.send_text(json.dumps({"type": "status", "connected": bool(hub.connected), "source": hub.source.mode}))
    if hub.yaml:
        await c.send_text(json.dumps({"type": "session", "yaml": hub.yaml, "update": hub.update}))
    hub.clients.add(c)
    writer = asyncio.ensure_future(c.run())
    try:
        async for _ in ws:          # read-only: whatever a viewer sends is ignored
            pass
    except Exception:
        pass
    finally:
        c.dead = True
        hub.clients.discard(c)
        writer.cancel()


def analyzer_page():
    return os.path.join(web_root(), "analyzer", "index.html")


def analyzer_reply(path):
    """The Lap Analyzer as it is sent: (body, content type) for its page or one of its
    script pieces (suite/pagesplit.py), or None for any other path."""
    from suite.pagesplit import split_page, part, PART_PREFIX
    p = path.split("?")[0]
    if p in ("/analyzer", "/analyzer/", "/analyzer/index.html") or p.startswith("/analyzer/" + PART_PREFIX):
        try:
            with open(analyzer_page(), "rb") as f:
                data = f.read()
        except OSError:
            return None
        if p.startswith("/analyzer/" + PART_PREFIX):
            body = part(data, "/analyzer/", p[len("/analyzer/"):])
            return (body, TYPES[".js"]) if body is not None else None
        return split_page(data, "/analyzer/")[0], TYPES[".html"]
    return None


def resolve(path):
    """Map a URL path to a file inside the web folder (or PitWall's client folder); never outside them."""
    rel = path.split("?")[0].lstrip("/")
    if rel == "pitwall" or rel.startswith("pitwall/"):
        root = os.path.realpath(pitwall_client_root())
        rel = rel[len("pitwall"):].lstrip("/")
    else:
        root = os.path.realpath(web_root())
    if rel == "" or rel.endswith("/"):
        rel += "index.html"
    full = os.path.realpath(os.path.join(root, *[p for p in rel.split("/") if p not in ("", ".", "..")]))
    if not (full == root or full.startswith(root + os.sep)):
        return None
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    return full if os.path.isfile(full) else None


LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def _hostname(value: str) -> str:
    """'127.0.0.1:8123' -> '127.0.0.1', 'http://localhost:8123' -> 'localhost'."""
    v = value.strip().lower()
    if "://" in v:
        v = v.split("://", 1)[1]
    v = v.split("/", 1)[0]
    if v.startswith("["):
        return v.split("]", 1)[0] + "]"
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def request_allowed(headers: dict) -> bool:
    """Only this PC's own pages may use the server.

    Binding 127.0.0.1 keeps other machines out, but not other websites: any page
    open in the user's browser can open a WebSocket to localhost (browsers apply
    no same-origin rule to WebSockets), and a DNS-rebinding page can even read
    plain HTTP replies. /ws carries live telemetry and the session's driver list,
    and /api/info carries PitWall's view and producer tokens. So the Host must be
    a loopback name (defeats rebinding) and a browser's Origin, when it sends
    one, must be this PC too. The app window and local tools pass both checks.
    """
    host = headers.get("host")
    if host is not None and _hostname(host) not in LOCAL_HOSTS:
        return False
    origin = headers.get("origin")
    # "null" is refused too: a sandboxed iframe on any website sends exactly that.
    if origin is not None and _hostname(origin) not in LOCAL_HOSTS:
        return False
    return True


# Incoming frames are only pings and closes; anything bigger is not a client of ours.
WS_MAX_IN = 64 * 1024


_LIB = None


def library():
    """Your telemetry folders (suite/library.py), made on first use."""
    global _LIB
    if _LIB is None:
        from suite.library import Library
        _LIB = Library()
    return _LIB


async def library_reply(writer, path, headers):
    """/api/library?page=N            the list of files, one page at a time
       /api/library/file?id=..&off=N  a piece of one listed file (application/octet-stream)
       /api/library/folders?add=PATH | remove=PATH
    Local only, like the rest of this server (request_allowed); the relay never serves these."""
    from urllib.parse import parse_qs, urlsplit
    u = urlsplit(path)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    loop = asyncio.get_running_loop()
    lib = library()
    try:
        if u.path == "/api/library/file":
            got = await loop.run_in_executor(None, lib.chunk, q.get("id", ""), int(q.get("off", "0") or 0))
            if got is None:
                await http_reply(writer, "404 Not Found", b"Not found")
            else:
                await http_reply(writer, "200 OK", got[1], "application/octet-stream")
            return
        if u.path == "/api/library/folders":
            if "add" in q:
                out = await loop.run_in_executor(None, lib.add_folder, q["add"])
            elif "remove" in q:
                out = await loop.run_in_executor(None, lib.remove_folder, q["remove"])
            else:
                out = {"ok": True, "folders": lib.folders()}
        elif u.path == "/api/trackmap":
            # PitWall's outline for a track (pitwall.trackmap's cache, north up), for the Live view's map
            def tm():
                from pitwall import trackmap
                t = trackmap.load(int(q.get("id", "0") or 0), q.get("config") or "default")
                return {"points": t.points, "source": t.source, "lapLengthM": t.lap_length_m} if t else {"points": None}
            out = await loop.run_in_executor(None, tm)
        elif u.path == "/api/library":
            out = await loop.run_in_executor(None, lib.info, max(0, int(q.get("page", "0") or 0)))
        elif u.path == "/api/library/records":
            def recs():
                from suite.library import records
                lib.scan_ids()
                return records(lib._last or [])
            out = await loop.run_in_executor(None, recs)
        else:
            await http_reply(writer, "404 Not Found", b"Not found")
            return
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    await http_reply(writer, "200 OK", json.dumps(out).encode(), "application/json", gzip_ok=accepts_gzip(headers))


STREAM_FRAME = 64 * 1024


async def library_stream(reader, writer, path, key):
    """One listed file over a WebSocket: a text frame {"size": n}, then the bytes in binary frames.
    Much faster than thousands of small replies (a browser opens only six at a time), and a
    WebSocket is the same path live telemetry takes, so the reply-size stall does not apply."""
    from urllib.parse import parse_qs, urlsplit
    q = {k: v[0] for k, v in parse_qs(urlsplit(path).query).items()}
    loop = asyncio.get_running_loop()
    p = await loop.run_in_executor(None, library().path_of, q.get("id", ""))
    if not p:
        await http_reply(writer, "404 Not Found", b"Not found")
        return
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
    await writer.drain()
    c = Client(writer)
    with open(p, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        await c.send_text(json.dumps({"size": size}))
        sent = 0
        while sent < size:
            data = await loop.run_in_executor(None, f.read, STREAM_FRAME)
            if not data:
                break
            await c.send_binary(data)
            sent += len(data)
    await c.send_ctrl(0x8, struct.pack(">H", 1000))


def make_handler(hub):
    async def handle(reader, writer):
        try:
            req = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            lines = req.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            path = parts[1] if len(parts) > 1 else "/"
            headers = {}
            for line in lines[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    headers[k.strip().lower()] = v.strip()
            if not request_allowed(headers):
                await http_reply(writer, "403 Forbidden", b"Forbidden")
                return
            if path.split("?")[0] == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                await ws_session(hub, reader, writer, headers.get("sec-websocket-key", ""),
                                 ticks="ticks=0" not in path.partition("?")[2].split("&"))
                return
            if path.split("?")[0] == "/api/info":
                await http_reply(writer, "200 OK", json.dumps(hub.info()).encode(), "application/json",
                                 gzip_ok=accepts_gzip(headers))
                return
            if path.split("?")[0] == "/api/library/stream" and headers.get("upgrade", "").lower() == "websocket":
                await library_stream(reader, writer, path, headers.get("sec-websocket-key", ""))
                return
            if path.split("?")[0].startswith("/api/library") or path.split("?")[0] == "/api/trackmap":
                await library_reply(writer, path, headers)
                return
            if path.split("?")[0] == "/favicon.ico":
                await http_reply(writer, "204 No Content")
                return
            if path.split("?")[0] in ("/analyzer", "/pitwall"):
                # keep the query: a dashboard link carries the relay address and token in it
                base, _, query = path.partition("?")
                writer.write(("HTTP/1.1 301 Moved\r\nLocation: %s/%s\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                              % (base, "?" + query if query else "")).encode())
                await writer.drain()
                return
            ana = analyzer_reply(path) if path.split("?")[0] != "/analyzer" else None
            if ana:
                await http_reply(writer, "200 OK", ana[0], ana[1], gzip_ok=accepts_gzip(headers))
                return
            full = resolve(path)
            if not full:
                await http_reply(writer, "404 Not Found", b"Not found")
                return
            with open(full, "rb") as f:
                body = f.read()
            if full.startswith(os.path.realpath(pitwall_client_root())) and full.endswith(".html"):
                body = body.replace(b"</body>", SUITE_LINK + b"</body>", 1)
            await http_reply(writer, "200 OK", body, TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"),
                             gzip_ok=accepts_gzip(headers))
        except Exception:
            pass
        finally:
            try:
                writer.close()
            except Exception:
                pass
    return handle


async def start_server(hub, port):
    """Start the server on the first free port from `port`. Returns (server, port)."""
    last = None
    for p in range(port, port + 10):
        try:
            server = await asyncio.start_server(make_handler(hub), "127.0.0.1", p)
            return server, p
        except OSError as e:
            last = e
    raise OSError("No free port between %d and %d (%s)" % (port, port + 9, last))
