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
        self.assertEqual(public, {"version", "pick_replay", "use_live", "install_camera_pack", "open_cameras_folder",
                                  "share_start", "share_stop", "share_info"})
        for n in public:
            self.assertTrue(inspect.ismethod(getattr(api, n)), n)


# --------------------------------------------------------------------------- #
# Share online: a Cloudflare quick tunnel to PitWall's relay
# --------------------------------------------------------------------------- #
class FakeTunnel:
    """Stands in for cloudflared: prints what it prints, runs until ended."""
    def __init__(self, lines, exit_after=False):
        import queue
        self.args = None
        self._q = queue.Queue()
        for ln in lines:
            self._q.put(ln.encode() + b"\n")
        if exit_after:
            self._q.put(b"")
        self.terminated = False
        self._handle = 0
        self.stdout = self

    def readline(self):
        return self._q.get()

    def terminate(self):
        self.terminated = True
        self._q.put(b"")

    kill = terminate

    def wait(self, timeout=None):
        return 0


TUNNEL_OUTPUT = [
    "2026-10-15T09:00:00Z INF Requesting new quick Tunnel on trycloudflare.com...",
    "2026-10-15T09:00:01Z INF +--------------------------------------------------------------------------------------------+",
    "2026-10-15T09:00:01Z INF |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |",
    "2026-10-15T09:00:01Z INF |  https://bright-lane-pit-wall.trycloudflare.com                                             |",
    "2026-10-15T09:00:02Z INF Registered tunnel connection connIndex=0 connection=abc event=0 ip=198.41.200.1 location=ord07 protocol=quic",
]


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class ShareTest(unittest.TestCase):
    def _share(self, procs, exe=True, **kw):
        from suite.share import Share
        self.tmp = tempfile.mkdtemp()
        path = os.path.join(self.tmp, "cloudflared.exe")
        if exe:
            with open(path, "wb") as f:
                f.write(b"MZ")
        self.guest = []
        made = []

        def popen(args, **_):
            p = procs.pop(0)
            p.args = args
            made.append(p)
            return p
        sh = Share(lambda: 8765, self.guest.append, producer_token="P", view_token="V", exe_path=path,
                   log=lambda *a: None, popen=popen, **kw)
        return sh, made

    def test_links_and_qr(self):
        from suite.share import share_links, qr_svg
        l = share_links("https://a-b.trycloudflare.com/", "G", "P", "V")
        self.assertEqual(l["pitwall"], "https://a-b.trycloudflare.com/pitwall/?relay=wss://a-b.trycloudflare.com&token=G")
        self.assertEqual(l["producer"], "https://a-b.trycloudflare.com/broadcast/producer.html?token=P&view=V")
        self.assertNotIn("producer", share_links("https://x.trycloudflare.com", "G"))
        svg = qr_svg(l["pitwall"])
        self.assertTrue(svg and svg.lstrip().startswith("<svg"), svg and svg[:60])

    def test_start_goes_live_then_stop_revokes(self):
        sh, made = self._share([FakeTunnel(TUNNEL_OUTPUT)])
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "on"), sh.info())
        info = sh.info()
        tok = self.guest[0]
        self.assertTrue(tok and len(tok) >= 16)
        self.assertEqual(info["links"]["base"], "https://bright-lane-pit-wall.trycloudflare.com")
        self.assertIn("token=" + tok, info["links"]["pitwall"])
        self.assertNotIn("token=V", info["links"]["pitwall"])     # never the kept view token
        self.assertEqual(info["links"]["pitwall"].split("token=")[1], tok)   # only the guest token
        self.assertEqual(made[0].args[-2:], ["--url", "http://127.0.0.1:8765"])
        self.assertIn("--no-autoupdate", made[0].args)
        sh.stop()
        self.assertEqual(sh.state, "off")
        self.assertTrue(made[0].terminated)
        self.assertEqual(self.guest[-1], None)                       # revoked
        self.assertNotIn("links", sh.info())

    def test_each_share_gets_a_new_token(self):
        sh, _ = self._share([FakeTunnel(TUNNEL_OUTPUT), FakeTunnel(TUNNEL_OUTPUT)])
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "on"))
        sh.stop()
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "on"))
        toks = [t for t in self.guest if t]
        self.assertEqual(len(toks), 2)
        self.assertNotEqual(toks[0], toks[1])
        sh.stop()

    def test_address_without_registered_line_goes_live_after_a_wait(self):
        sh, _ = self._share([FakeTunnel(TUNNEL_OUTPUT[:4])], ready_fallback_s=0.3)
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "on", 3), sh.info())
        sh.stop()

    def test_tunnel_that_dies_is_an_error_and_revokes(self):
        sh, _ = self._share([FakeTunnel(["ERR failed to request quick Tunnel: 429 Too Many Requests"], exit_after=True)])
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"), sh.info())
        self.assertIn("429", sh.info()["error"])
        self.assertEqual(self.guest[-1], None)
        self.assertNotIn("links", sh.info())

    def test_cloudflares_own_api_address_is_not_the_link(self):
        """Seen with the real cloudflared 2026.10.0 when it cannot reach Cloudflare."""
        line = ('failed to request quick Tunnel: Post "https://api.trycloudflare.com/tunnel": '
                'dial tcp: lookup api.trycloudflare.com: no such host')
        sh, _ = self._share([FakeTunnel([TUNNEL_OUTPUT[0], line], exit_after=True)], ready_fallback_s=0.1)
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"), sh.info())
        self.assertIsNone(sh.url)
        self.assertIn("no such host", sh.info()["error"])

    def test_download_checks_it_is_a_program(self):
        from suite import share as share_mod
        good = os.path.join(tempfile.mkdtemp(), "good.exe")
        with open(good, "wb") as f:
            f.write(b"MZ" + b"\0" * share_mod.MIN_EXE_BYTES)
        bad = os.path.join(tempfile.mkdtemp(), "bad.html")
        with open(bad, "wb") as f:
            f.write(b"<html>rate limited</html>" * 300000)
        sh, _ = self._share([FakeTunnel(TUNNEL_OUTPUT)], exe=False, download_url="file://" + bad)
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"), sh.info())
        self.assertIn("not a Windows program", sh.info()["error"])
        self.assertFalse(os.path.exists(sh.exe))
        sh.download_url = "file://" + good
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "on"), sh.info())
        self.assertTrue(os.path.isfile(sh.exe))
        sh.stop()

    def test_no_relay_means_no_share(self):
        from suite.share import Share
        sh = Share(lambda: None, lambda t: None, exe_path="x", log=lambda *a: None)
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"))
        self.assertIn("not running", sh.info()["error"])


class GuestTokenTest(unittest.TestCase):
    """The relay's guest token, through the suite's own relay."""

    def setUp(self):
        if not hasattr(SuiteTest, "engine"):
            SuiteTest.setUpClass()
        self.engine = SuiteTest.engine
        self.relay_url = self.engine.pitwall.info()["relay"]

    def test_relay_serves_the_dashboard(self):
        http = self.relay_url.replace("ws://", "http://")
        with urllib.request.urlopen(http + "/pitwall/", timeout=5) as r:
            html = r.read()
        self.assertIn(b"<title>", html)
        self.assertNotIn(b"&larr; Suite", html)           # the suite's way home is not for remote crew

    def test_guest_watches_cannot_command_and_is_cut_off_when_sharing_stops(self):
        import websockets.sync.client as wsc
        from websockets.exceptions import ConnectionClosed
        host = self.engine.pitwall
        host._set_guest_token("guest-abc-123")
        self.assertTrue(_wait(lambda: host.relay.guest_token == "guest-abc-123"))
        with wsc.connect("%s/view?token=guest-abc-123" % self.relay_url, open_timeout=5) as ws:
            first = json.loads(ws.recv(timeout=5))
            self.assertEqual(first.get("role"), "guest")
            ws.send(json.dumps({"type": "cmd", "cmd": "camera", "car": 1}))
            ws.send(json.dumps({"type": "gfx", "set": {"x": 1}}))
            errs = []
            end = time.time() + 5
            while time.time() < end and len(errs) < 2:
                m = json.loads(ws.recv(timeout=5))
                if m.get("type") in ("cmd_result", "gfx_result"):
                    errs.append(m)
            self.assertEqual([e["ok"] for e in errs], [False, False])
            host._set_guest_token(None)
            with self.assertRaises(ConnectionClosed) as cm:
                while True:
                    ws.recv(timeout=5)
            self.assertEqual(cm.exception.rcvd.code, 4003)
        with self.assertRaises(Exception):
            with wsc.connect("%s/view?token=guest-abc-123" % self.relay_url, open_timeout=5) as ws:
                ws.recv(timeout=3)
