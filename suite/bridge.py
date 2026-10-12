"""
Built-in iRacing bridge for the suite.

Reads iRacing's live telemetry (or replays an .ibt file) and serves it to the
suite's pages over a local connection on 127.0.0.1 only. Read-only: it never
changes anything in iRacing.
"""
import asyncio
import base64
import hashlib
import json
import math
import mmap
import os
import struct
import sys
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
SUITE_LINK = (b'<a href="/" style="position:fixed;left:10px;bottom:46px;z-index:99999;padding:5px 11px;'
              b'border-radius:7px;background:rgba(20,22,26,.85);color:#e8e9eb;font:13px Segoe UI,system-ui,sans-serif;'
              b'text-decoration:none;border:1px solid rgba(255,255,255,.18)">&larr; Suite</a>')


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


class LiveSource:
    """Live data from iRacing's shared memory (Windows only)."""
    mode = "live"

    def __init__(self):
        self.ir = irsdk.IRSDK() if irsdk else None
        self.names = None
        self.last_update = None
        self.next_try = 0.0

    def _reset(self):
        self.names = None
        self.last_update = None

    def poll(self):
        ir = self.ir
        if ir is None:
            return False, None, None
        if not (ir.is_initialized and ir.is_connected):
            if ir.is_initialized:
                ir.shutdown()
                self._reset()
            now = time.monotonic()
            if now < self.next_try:
                return False, None, None
            self.next_try = now + 2.0
            try:
                ok = ir.startup()
            except Exception:
                ok = False
            if not (ok and ir.is_connected):
                return False, None, None
        try:
            ir.freeze_var_buffer_latest()
            if self.names is None:
                self.names = [h.name for h in ir._var_headers if h.count == 1]
            values = {}
            for name in self.names:
                try:
                    values[name] = clean(ir[name])
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

    def __init__(self, writer):
        self.w = writer

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
        for c in list(self.clients):
            try:
                await asyncio.wait_for(c.send_text(text), 1.0)
            except Exception:
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
            await asyncio.sleep(max(0.0, period - (time.monotonic() - start)))


TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".json": "application/json", ".png": "image/png",
         ".svg": "image/svg+xml", ".ico": "image/x-icon", ".woff2": "font/woff2"}


async def http_reply(writer, status, body=b"", ctype="text/plain; charset=utf-8"):
    head = ("HTTP/1.1 %s\r\nContent-Type: %s\r\nContent-Length: %d\r\nCache-Control: no-store\r\n"
            "Connection: close\r\n\r\n" % (status, ctype, len(body)))
    writer.write(head.encode("latin-1") + body)
    await writer.drain()


async def ws_session(hub, reader, writer, key):
    accept = base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
    writer.write(("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                  "Sec-WebSocket-Accept: %s\r\n\r\n" % accept).encode())
    await writer.drain()
    client = Client(writer)
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
            if path.split("?")[0] == "/ws" and headers.get("upgrade", "").lower() == "websocket":
                await ws_session(hub, reader, writer, headers.get("sec-websocket-key", ""))
                return
            if path.split("?")[0] == "/api/info":
                await http_reply(writer, "200 OK", json.dumps(hub.info()).encode(), "application/json")
                return
            if path.split("?")[0] == "/favicon.ico":
                await http_reply(writer, "204 No Content")
                return
            if path.split("?")[0] in ("/analyzer", "/pitwall"):
                writer.write(("HTTP/1.1 301 Moved\r\nLocation: %s/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n" % path.split("?")[0]).encode())
                await writer.drain()
                return
            full = resolve(path)
            if not full:
                await http_reply(writer, "404 Not Found", b"Not found")
                return
            with open(full, "rb") as f:
                body = f.read()
            if full.startswith(os.path.realpath(pitwall_client_root())) and full.endswith(".html"):
                body = body.replace(b"</body>", SUITE_LINK + b"</body>", 1)
            await http_reply(writer, "200 OK", body, TYPES.get(os.path.splitext(full)[1].lower(), "application/octet-stream"))
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
