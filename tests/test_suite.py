"""
Self-test for the suite app's engine. Runs on any OS. It writes a small synthetic
.ibt file and replays it through the suite's bridge, runs PitWall's fake field
through PitWall's own bridge and relay inside the suite, and checks the pages and
both live streams end to end.

Run:  python -m pytest tests -q   (or: python -m unittest tests.test_suite -v)
"""
import asyncio
import base64
import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suite.app import Engine  # noqa: E402

YAML = """WeekendInfo:
 TrackDisplayName: Test Raceway
DriverInfo:
 DriverCarIdx: 0
 Drivers:
 - CarIdx: 0
   UserName: Test Driver
   CarPath: testcar
   CarScreenName: Test Car GT3

CarSetup:
 UpdateCount: 1
 Chassis:
  LeftFront:
   SpringRate: 190 N/mm
   RideHeight: 53.1 mm
"""

VARS = [("Lap", 2), ("LapDistPct", 4), ("Speed", 4), ("LapCurrentLapTime", 4), ("LFrideHeight", 4)]


def write_ibt(path, rows=600):
    """Minimal .ibt: header, variable headers, session text, then data rows at 60 Hz."""
    nv, vho, so = len(VARS), 144, 144 + len(VARS) * 144
    session = YAML.encode("latin-1") + b"\x00"
    bo = so + len(session)
    bl = 4 * nv
    hdr = bytearray(144)
    struct.pack_into("<iiiiiiiiii", hdr, 0, 2, 1, 60, 1, len(session), so, nv, vho, 1, bl)
    struct.pack_into("<ii", hdr, 48, 0, bo)
    vh = bytearray()
    for i, (name, t) in enumerate(VARS):
        h = bytearray(144)
        struct.pack_into("<iiii", h, 0, t, i * 4, 1, 0)
        h[16:16 + len(name)] = name.encode()
        vh += h
    data = bytearray()
    for r in range(rows):
        lap = 1 + r // 300
        d = (r % 300) / 300
        data += struct.pack("<iffff", lap, d, 50.0, (r % 300) / 60, 0.05)
    with open(path, "wb") as f:
        f.write(hdr + vh + session + data)


def ws_messages(port, count, timeout=10):
    """Tiny WebSocket client: handshake, then read `count` text frames."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET /ws HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % key).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        buf += s.recv(4096)
    head, buf = buf.split(b"\r\n\r\n", 1)
    assert b"101" in head.split(b"\r\n")[0], head

    def need(n):
        nonlocal buf
        while len(buf) < n:
            chunk = s.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        out, buf = buf[:n], buf[n:]
        return out

    out = []
    while len(out) < count:
        b0, b1 = need(2)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", need(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", need(8))[0]
        payload = need(n)
        if b0 & 0x0F == 1:
            out.append(json.loads(payload.decode()))
    s.close()
    return out


class SuiteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.ibt = os.path.join(cls.tmp, "test.ibt")
        write_ibt(cls.ibt)
        cls.engine = Engine(rate=20, pitwall_source="fake")
        cls.engine.start(18123)
        cls.engine.replay(cls.ibt, speed=1.0)
        time.sleep(0.5)

    def get(self, path):
        with urllib.request.urlopen(self.engine.url.rstrip("/") + path, timeout=5) as r:
            return r.status, r.read()

    def test_pages(self):
        for path in ("/", "/analyzer/", "/analyzer/track-map.png", "/pitwall/"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            if path.endswith("/"):
                self.assertIn(b"<html", body[:400].lower(), path)

    def test_no_escape_from_web_folder(self):
        with self.assertRaises(Exception):
            self.get("/../suite/app.py")

    def test_info(self):
        status, body = self.get("/api/info")
        info = json.loads(body)
        self.assertEqual(info["mode"], "replay")
        self.assertEqual(info["file"], "test.ibt")

    def test_stream(self):
        msgs = ws_messages(self.engine.port, 6)
        types = [m["type"] for m in msgs]
        self.assertEqual(types[0], "hello")
        self.assertIn("session", types)
        session = next(m for m in msgs if m["type"] == "session")
        self.assertIn("CarSetup:", session["yaml"])
        self.assertIn("SpringRate: 190 N/mm", session["yaml"])
        tick = next(m for m in msgs if m["type"] == "tick")
        self.assertEqual(set(tick["v"]), {n for n, _ in VARS})
        self.assertAlmostEqual(tick["v"]["Speed"], 50.0, places=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PitWallInSuiteTest(unittest.TestCase):
    """PitWall's relay and bridge run inside the suite and stream to its dashboard."""

    def setUp(self):
        self.engine = SuiteTest.engine if hasattr(SuiteTest, "engine") else None
        if self.engine is None:
            SuiteTest.setUpClass()
            self.engine = SuiteTest.engine

    def test_info_and_dashboard(self):
        with urllib.request.urlopen(self.engine.url + "api/info", timeout=5) as r:
            pw = json.loads(r.read())["pitwall"]
        self.assertTrue(pw["ok"], pw)
        self.assertTrue(pw["relay"].startswith("ws://127.0.0.1:"))
        with urllib.request.urlopen(self.engine.url + "pitwall/", timeout=5) as r:
            html = r.read()
        self.assertIn(b"&larr; Suite", html)

    def test_relay_streams_pitwall_messages(self):
        import websockets.sync.client as wsc
        info = self.engine.pitwall.info()
        url = "%s/view?token=%s" % (info["relay"], info["token"])
        seen = set()
        deadline = time.time() + 15
        with wsc.connect(url, open_timeout=5) as ws:
            while time.time() < deadline and not {"session", "state", "cars"} <= seen:
                try:
                    msg = json.loads(ws.recv(timeout=2))
                except TimeoutError:
                    continue
                seen.add(msg.get("type"))
        self.assertTrue({"session", "state", "cars"} <= seen, seen)

    def test_wrong_token_is_refused(self):
        import websockets.sync.client as wsc
        info = self.engine.pitwall.info()
        with self.assertRaises(Exception):
            with wsc.connect("%s/view?token=wrong" % info["relay"], open_timeout=5) as ws:
                ws.recv(timeout=3)


class AppWindowApiTest(unittest.TestCase):
    def test_api_exposes_only_its_functions(self):
        """pywebview walks every public attribute of js_api recursively. A public
        engine or window attribute made it crawl the event loop, threads and native
        window objects, freezing the app. Only the page-callable functions may be
        public."""
        import inspect
        from suite.app import Api
        api = Api(engine=object())
        api._window = object()
        public = {n for n in dir(api) if not n.startswith("_")}
        self.assertEqual(public, {"version", "pick_replay", "use_live"})
        for n in public:
            self.assertTrue(inspect.ismethod(getattr(api, n)), n)
