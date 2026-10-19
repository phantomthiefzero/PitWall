"""
Self-test for the suite app's engine. Runs on any OS. It writes a small synthetic
.ibt file and replays it through the suite's bridge, runs PitWall's fake field
through PitWall's own bridge and relay inside the suite, and checks the pages and
both live streams end to end.

Run:  python -m pytest tests -q   (or: python -m unittest tests.test_suite -v)
"""
import asyncio
import base64
import gzip
import json
import os
import pathlib
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


def fetch(url, gzip_ok=True, timeout=5):
    """GET like a browser: ask for compression and unpack it. Returns (status,
    headers, body as served before compression). Large pages are fetched this way
    because, uncompressed, they are exactly what stalled behind Norton 360's web
    inspection (see suite.bridge.GZIP_MIN) -- a test that fetches them raw fails
    on such a PC for reasons that have nothing to do with the code under test."""
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"} if gzip_ok else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
        return r.status, r.headers, body


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def on_disk(*parts):
    with open(os.path.join(ROOT, *parts), "rb") as f:
        return f.read()


def ws_handshake(port, host=None, origin=None, timeout=10):
    """Open /ws and return (socket, status line)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    extra = "Origin: %s\r\n" % origin if origin else ""
    s.sendall(("GET /ws HTTP/1.1\r\nHost: %s\r\n%sUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n"
               % (host or "127.0.0.1:%d" % port, extra, key)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    return s, buf.split(b"\r\n")[0]


def ws_messages(port, count, timeout=10):
    """Tiny WebSocket client: handshake, then read `count` text frames."""
    s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall(("GET /ws HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (port, key)).encode())
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
        status, _headers, body = fetch(self.engine.url.rstrip("/") + path)
        return status, body

    def test_pages(self):
        for path in ("/", "/analyzer/", "/analyzer/track-map.png", "/pitwall/"):
            status, body = self.get(path)
            self.assertEqual(status, 200, path)
            if path.endswith("/"):
                self.assertIn(b"<html", body[:400].lower(), path)

    def test_large_analyzer_page_arrives_in_pieces_that_join_back(self):
        import re
        base = self.engine.url.rstrip("/")
        disk = on_disk("suite", "web", "analyzer", "index.html")
        page = fetch(base + "/analyzer/")[2]
        urls = re.findall(rb'"(/analyzer/_part[^"]+)"', page)
        self.assertGreaterEqual(len(urls), 2)                     # it is over the target today
        parts = [fetch(base + u.decode()) for u in urls]
        for status, headers, _body in parts:
            self.assertEqual(headers.get("Content-Type"), "text/javascript; charset=utf-8")
        code = b"".join(p[2] for p in parts)
        main = max(re.findall(rb"<script>([\s\S]*?)</script>", disk), key=len)
        self.assertEqual(code, main)
        self.assertEqual(page.replace(re.findall(rb"<script>\(async\(\)=>[\s\S]*?</script>", page)[0], b"<script>" + main + b"</script>"), disk)
        with self.assertRaises(Exception):
            fetch(base + "/analyzer/_part9.nope.js")

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

    def test_pages_are_compressed_for_browsers(self):
        from suite.bridge import SUITE_LINK
        base = self.engine.url.rstrip("/")
        expect = {"/": on_disk("suite", "web", "index.html"),
                  "/analyzer/": __import__("suite.pagesplit", fromlist=["x"]).split_page(on_disk("suite", "web", "analyzer", "index.html"), "/analyzer/")[0],
                  "/pitwall/": on_disk("client", "index.html").replace(b"</body>", SUITE_LINK + b"</body>", 1)}
        for path, want in expect.items():
            _s, headers, body = fetch(base + path)
            self.assertEqual(headers.get("Content-Encoding"), "gzip", path)
            self.assertEqual(headers.get("Vary"), "Accept-Encoding", path)
            self.assertEqual(body, want, path)
        # not asked for: sent as it is (a small page, under the size that stalls)
        _s, headers, body = fetch(base + "/", gzip_ok=False)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(body, expect["/"])
        # a PNG is compressed already
        _s, headers, body = fetch(base + "/analyzer/track-map.png")
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(body, on_disk("suite", "web", "analyzer", "track-map.png"))

    def test_everything_served_arrives_under_the_stall_size(self):
        """Behind security software that inspects local web traffic (seen with Norton
        360), web replies over ~128 KB stalled at exactly 130,560 bytes about 1 time
        in 70, and the page hung. Every page and file the suite and its relay serve
        must arrive smaller than that, as a browser receives it (compressed where it
        can be). A new large file will fail here rather than hang for a user."""
        from suite.bridge import compressible, TYPES
        root = ROOT
        limit = 120 * 1024
        too_big = []
        for folder in ("suite/web", "client", "broadcast"):
            for dirpath, _dirs, files in os.walk(os.path.join(root, folder)):
                for name in files:
                    path = os.path.join(dirpath, name)
                    with open(path, "rb") as f:
                        data = f.read()
                    ctype = TYPES.get(os.path.splitext(name)[1].lower(), "application/octet-stream")
                    if name.endswith(".html"):
                        # what the server really sends: large pages go as a page plus pieces
                        from suite.pagesplit import split_page
                        page, parts = split_page(data, "/x/")
                        replies = [page] + list(parts.values())
                    else:
                        replies = [data]
                    for body in replies:
                        sent = len(gzip.compress(body, 6)) if compressible(ctype) else len(body)
                        if sent >= limit:
                            too_big.append((os.path.relpath(path, root), sent))
        self.assertEqual(too_big, [], "files that would be sent at %d bytes or more" % limit)

    def test_other_websites_are_refused(self):
        """Any page in the user's browser can open a WebSocket to localhost, and /ws
        carries live telemetry and the session's driver list; /api/info carries
        PitWall's producer token, readable by a DNS-rebinding page. Both refused."""
        port = self.engine.port
        for origin in ("https://evil.example", "null"):
            s, status = ws_handshake(port, origin=origin)
            s.close()
            self.assertIn(b"403", status, origin)
        s, status = ws_handshake(port, origin="http://127.0.0.1:%d" % port)
        s.close()
        self.assertIn(b"101", status, "the suite's own pages must still connect")
        s, status = ws_handshake(port, host="rebind.evil.example")
        s.close()
        self.assertIn(b"403", status)
        req = urllib.request.Request(self.engine.url.rstrip("/") + "/api/info",
                                     headers={"Host": "rebind.evil.example"})
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(cm.exception.code, 403)


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
        html = fetch(self.engine.url + "pitwall/")[2]
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
                                  "share_start", "share_stop", "share_info",
                                  "update_info", "update_set_key", "update_clear_key", "update_check", "update_install",
                                  "update_open_key_page"})
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
        kw.setdefault("verify", lambda p: None)      # the fake tunnel programs here are not signed
        sh = Share(lambda: 8765, self.guest.append, producer_token="P", view_token="V", exe_path=path,
                   log=lambda *a: None, popen=popen, **kw)
        return sh, made

    def test_links_and_qr(self):
        from suite.share import share_links, qr_svg
        l = share_links("https://a-b.trycloudflare.com/", "G", "P", "V")
        self.assertEqual(l["pitwall"], "https://a-b.trycloudflare.com/pitwall/?relay=wss://a-b.trycloudflare.com&token=G")
        self.assertEqual(l["producer"], "https://a-b.trycloudflare.com/broadcast/producer.html?token=P&view=V")
        self.assertEqual(l["analyzer"], "https://a-b.trycloudflare.com/analyzer/?token=G")
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
        sh, _ = self._share([FakeTunnel(TUNNEL_OUTPUT)], exe=False, download_url=pathlib.Path(bad).as_uri())
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"), sh.info())
        self.assertIn("not a Windows program", sh.info()["error"])
        self.assertFalse(os.path.exists(sh.exe))
        sh.download_url = pathlib.Path(good).as_uri()
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "on"), sh.info())
        self.assertTrue(os.path.isfile(sh.exe))
        sh.stop()

    def test_a_tunnel_program_that_is_not_cloudflares_is_never_run(self):
        """It runs with the user's rights: only Cloudflare's own, by signature."""
        from suite import share as share_mod
        # swapped on disk between sessions: refused, removed, not run
        sh, made = self._share([FakeTunnel(TUNNEL_OUTPUT)], verify=lambda p: "it is signed by Evil Corp")
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"), sh.info())
        self.assertIn("not Cloudflare's", sh.info()["error"])
        self.assertEqual(made, [], "the program was run")
        self.assertFalse(os.path.exists(sh.exe), "the program was kept")
        self.assertEqual(self.guest, [], "a share token was set up anyway")
        # a fresh download that is not Cloudflare's: refused before it is kept
        good = os.path.join(tempfile.mkdtemp(), "dl.exe")
        with open(good, "wb") as f:
            f.write(b"MZ" + b"\0" * share_mod.MIN_EXE_BYTES)
        sh, made = self._share([FakeTunnel(TUNNEL_OUTPUT)], exe=False, download_url=pathlib.Path(good).as_uri(),
                               verify=lambda p: "it is not validly signed (NotSigned)")
        sh.start()
        self.assertTrue(_wait(lambda: sh.state == "error"), sh.info())
        self.assertIn("not Cloudflare's program", sh.info()["error"])
        self.assertFalse(os.path.exists(sh.exe))
        self.assertEqual(made, [])

    def test_signer_organisation_is_read_from_the_certificate(self):
        from suite.share import signer_org
        self.assertEqual(signer_org('CN="Cloudflare, Inc.", O="Cloudflare, Inc.", L=San Francisco, C=US'),
                         "Cloudflare, Inc.")
        self.assertEqual(signer_org("CN=Cloudflare, O=Evil Corp, C=US"), "Evil Corp")   # a CN alone proves nothing
        self.assertIsNone(signer_org("CN=nobody"))

    @unittest.skipUnless(sys.platform == "win32", "Authenticode is Windows")
    def test_signature_check_on_real_files(self):
        from suite.share import signature_problem
        unsigned = os.path.join(tempfile.mkdtemp(), "fake.exe")
        with open(unsigned, "wb") as f:
            f.write(b"MZ" + b"\0" * 4096)
        self.assertIn("not validly signed", signature_problem(unsigned))
        # validly signed, but by Microsoft: still refused
        notepad = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "notepad.exe")
        why = signature_problem(notepad)
        self.assertIsNotNone(why)
        self.assertNotIn("Cloudflare, Inc., not", why or "")

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
        html = fetch(http + "/pitwall/")[2]
        self.assertIn(b"<title>", html)
        self.assertNotIn(b"&larr; Suite", html)           # the suite's way home is not for remote crew

    def test_share_panel_reports_what_guests_really_use(self):
        """The Share panel shows how many are watching through the link and the
        upload they use, counted on the wire: after compression, which message
        sizes overstate about five times."""
        import websockets.sync.client as wsc
        host = self.engine.pitwall
        host._set_guest_token("guest-usage-1")
        self.assertTrue(_wait(lambda: host.relay.guest_token == "guest-usage-1"))
        before = host.relay.guest_bytes
        msg_bytes = 0
        with wsc.connect("%s/view?token=guest-usage-1" % self.relay_url, open_timeout=5,
                         compression="deflate", max_size=None) as ws:
            end = time.time() + 3
            while time.time() < end:
                try:
                    msg_bytes += len(ws.recv(timeout=1))
                except TimeoutError:
                    pass
            self.assertEqual(host.relay.guest_usage()["guests"], 1)
            wire = host.relay.guest_bytes - before
        host._set_guest_token(None)
        self.assertGreater(msg_bytes, 10_000, "the guest should have been sent the stream")
        self.assertGreater(wire, 0)
        self.assertLess(wire, msg_bytes * 0.6, "counted before compression, not on the wire")
        self.assertTrue(_wait(lambda: host.relay.guest_usage()["guests"] == 0))

    def test_relay_compresses_its_pages_for_browsers(self):
        """The dashboard is ~160 KB: compressed, it loads faster over a share link
        and stays under the size that stalled behind Norton (see GZIP_MIN)."""
        http = self.relay_url.replace("ws://", "http://")
        for path, want in (("/pitwall/", on_disk("client", "index.html")),
                           ("/broadcast/producer.html", on_disk("broadcast", "producer.html")),
                           ("/broadcast/overlay.html", on_disk("broadcast", "overlay.html"))):
            req = urllib.request.Request(http + path, headers={"Accept-Encoding": "gzip, deflate"})
            with urllib.request.urlopen(req, timeout=5) as r:
                self.assertEqual(r.headers.get("Content-Encoding"), "gzip", path)
                packed = r.read()
            self.assertEqual(gzip.decompress(packed), want, path)
            self.assertLess(len(packed), len(want) // 2, path)
        # not asked for: sent as it is (a broadcast page, under the size that stalls)
        _s, headers, body = fetch(http + "/broadcast/overlay.html", gzip_ok=False)
        self.assertIsNone(headers.get("Content-Encoding"))
        self.assertEqual(body, on_disk("broadcast", "overlay.html"))

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

    def test_relay_serves_the_lap_analyzer(self):
        """Through the share link too, the page arrives in pieces under the stall size
        and the pieces join back into the script exactly as written."""
        import re
        http = self.relay_url.replace("ws://", "http://")
        html = fetch(http + "/analyzer/?token=x")[2]
        disk = on_disk("suite", "web", "analyzer", "index.html")
        urls = re.findall(rb'"(/analyzer/_part[^"]+)"', html)
        if urls:
            code = b"".join(fetch(http + u.decode())[2] for u in urls)
            self.assertIn(code, disk)
            self.assertIn(b"LIVE_SHARE", code)
        else:
            self.assertIn(b"LIVE_SHARE", html)

    def test_analyzer_link_streams_the_live_session_read_only(self):
        """An engineer on the share link gets what the local analyzer gets: the
        session (with the car setup) and telemetry, at half rate, and nothing they
        send reaches iRacing. Stopping the share cuts them off."""
        import websockets.sync.client as wsc
        from websockets.exceptions import ConnectionClosed
        host = self.engine.pitwall
        with self.assertRaises(Exception):
            with wsc.connect("%s/analyzer/ws?token=nope" % self.relay_url, open_timeout=5) as ws:
                ws.recv(timeout=3)
        host._set_guest_token("guest-ana-1")
        self.assertTrue(_wait(lambda: host.relay.guest_token == "guest-ana-1"))
        with wsc.connect("%s/analyzer/ws?token=guest-ana-1" % self.relay_url, open_timeout=5, max_size=None) as ws:
            seen, ticks = [], 0
            end = time.time() + 4
            ws.send(json.dumps({"type": "cmd", "cmd": "camera", "car": 1}))   # ignored
            while time.time() < end:
                try:
                    m = json.loads(ws.recv(timeout=1))
                except TimeoutError:
                    continue
                seen.append(m["type"])
                ticks += m["type"] == "tick"
            self.assertEqual(seen[:2], ["hello", "status"])
            self.assertIn("session", seen)
            self.assertGreater(ticks, 5)
            self.assertLess(ticks, 4 * 20 * 0.75)            # the replay runs at 20 a second: about half
            self.assertEqual(host.relay.guest_usage()["guests"], 1)
            host._set_guest_token(None)
            with self.assertRaises(ConnectionClosed) as cm:
                while True:
                    ws.recv(timeout=5)
            self.assertEqual(cm.exception.rcvd.code, 4003)
        self.assertTrue(_wait(lambda: not any(type(c).__name__ == "RemoteClient" for c in self.engine.hub.clients)))


if __name__ == "__main__":
    unittest.main(verbosity=2)


# --------------------------------------------------------------------------- #
# Updates from GitHub releases
# --------------------------------------------------------------------------- #
class _MemKeys:
    def __init__(self, t=None):
        self.t = t

    def load(self):
        return self.t

    def save(self, t):
        self.t = t

    def clear(self):
        self.t = None


class _Resp:
    def __init__(self, body):
        import io
        self._b = io.BytesIO(body)

    def read(self, n=-1):
        return self._b.read(n)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _release(build=35, body=b"MZ" + b"x" * 3000, digest=True, commit="newsha"):
    import hashlib
    return {"tag_name": "build-%d" % build, "target_commitish": commit, "published_at": "2026-10-16T01:00:00Z",
            "assets": [{"name": "iRacing.Suite.exe", "size": len(body),
                        "url": "https://api.github.com/repos/phantomthiefzero/PitWall/releases/assets/777",
                        **({"digest": "sha256:" + hashlib.sha256(body).hexdigest()} if digest else {})}]}


class FakeGitHub:
    """Answers the three calls the updater makes, and records each request."""
    def __init__(self, rel, exe_body, status=200, commits=None):
        self.rel, self.exe_body, self.status, self.reqs = rel, exe_body, status, []
        self.commits = commits if commits is not None else [
            {"commit": {"message": "Merge pull request #20 from x/y\n\nz"}},
            {"commit": {"message": "Older change\n\nbody"}},
            {"commit": {"message": "Newest change"}}]

    def __call__(self, req, timeout=None):
        import urllib.error
        self.reqs.append(req)
        if self.status != 200:
            raise urllib.error.HTTPError(req.full_url, self.status, "nope", {}, None)
        url = req.full_url
        if url.endswith("/releases/latest"):
            return _Resp(json.dumps(self.rel).encode())
        if "/compare/" in url:
            return _Resp(json.dumps({"commits": self.commits}).encode())
        if "/releases/assets/" in url:
            return _Resp(self.exe_body)
        raise AssertionError(url)


class UpdaterTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.exe = os.path.join(self.dir, "iRacing Suite.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ-old-version")

    def _up(self, gh, key="github_pat_" + "a" * 40, build=32, commit="oldsha"):
        from suite.updater import Updater
        return Updater(keys=_MemKeys(key), opener=gh, exe_path=self.exe, build=build, commit=commit,
                       log=lambda *a: None)

    def test_parse_release(self):
        from suite.updater import parse_release, UpdateError
        r = parse_release(_release(35))
        self.assertEqual((r["build"], r["commit"], r["name"]), (35, "newsha", "iRacing.Suite.exe"))
        self.assertEqual(len(r["sha256"]), 64)
        with self.assertRaises(UpdateError):
            parse_release({"tag_name": "v1.0", "assets": []})
        with self.assertRaises(UpdateError):
            parse_release({"tag_name": "build-3", "assets": [{"name": "notes.txt"}]})

    def test_check_finds_a_newer_build_and_what_changed(self):
        gh = FakeGitHub(_release(35), b"")
        up = self._up(gh)
        i = up.check()
        self.assertTrue(i["available"])
        self.assertEqual(i["latest"]["build"], 35)
        self.assertEqual(i["changes"], ["Newest change", "Older change"])   # newest first, merges left out
        self.assertIn("/compare/oldsha...newsha", gh.reqs[1].full_url)
        self.assertFalse(self._up(FakeGitHub(_release(32), b"")).check()["available"])

    def test_key_goes_only_to_github_api_and_not_to_redirects(self):
        gh = FakeGitHub(_release(35), b"")
        self._up(gh).check()
        for r in gh.reqs:
            self.assertTrue(r.full_url.startswith("https://api.github.com/"))
            self.assertIn("Bearer github_pat_", r.unredirected_hdrs.get("Authorization", ""))
            self.assertNotIn("Authorization", r.headers)     # headers a redirect would copy

    def test_bad_or_rejected_key_is_not_kept(self):
        from suite.updater import UpdateError
        up = self._up(FakeGitHub(_release(35), b"", status=401), key=None)
        with self.assertRaises(UpdateError):
            up.set_key("hello")                               # not a GitHub key
        with self.assertRaises(UpdateError) as cm:
            up.set_key("github_pat_" + "b" * 40)              # GitHub says no
        self.assertIn("did not accept", str(cm.exception))
        self.assertIsNone(up.keys.load())
        up404 = self._up(FakeGitHub(_release(35), b"", status=404), key=None)
        with self.assertRaises(UpdateError) as cm:
            up404.set_key("github_pat_" + "c" * 40)
        self.assertIn("Only select", str(cm.exception))

    def test_download_checks_size_and_fingerprint(self):
        from suite.updater import UpdateError
        good = b"MZ" + os.urandom(5000)
        rel = _release(35, good)
        up = self._up(FakeGitHub(rel, good[:-10]))           # cut short
        with self.assertRaises(UpdateError):
            up.download()
        up = self._up(FakeGitHub(rel, b"MZ" + os.urandom(5000)))   # same size, wrong bytes
        with self.assertRaises(UpdateError) as cm:
            up.download()
        self.assertIn("fingerprint", str(cm.exception))
        self.assertEqual(sorted(os.listdir(self.dir)), ["iRacing Suite.exe"])   # nothing left behind
        up = self._up(FakeGitHub(rel, good))
        up.download()
        self.assertEqual(up.state, "ready")
        with open(up._new_path, "rb") as f:
            self.assertEqual(f.read(), good)
        self.assertEqual(os.path.dirname(up._new_path), self.dir)   # same drive: the swap is a rename

    def test_apply_swaps_restarts_and_tidies(self):
        from suite import updater
        good = b"MZ-new-version" + os.urandom(100)
        up = self._up(FakeGitHub(_release(35, good), good))
        up.download()
        started, quit_ = [], []
        up.apply(lambda: quit_.append(1), popen=lambda args, **kw: started.append((args, kw)))
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), good)
        old = updater.old_path(self.exe)
        with open(old, "rb") as f:
            self.assertEqual(f.read(), b"MZ-old-version")
        args, kw = started[0]
        self.assertEqual(args[:2], [os.path.abspath(self.exe), "--after-update"])
        self.assertIn(str(os.getpid()), args[2].split(","))
        self.assertEqual(kw["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")
        self.assertEqual(quit_, [1])
        self.assertTrue(updater.tidy_after_update(self.exe))
        self.assertFalse(os.path.exists(old))

    def test_apply_needs_a_download_and_puts_things_back_on_failure(self):
        from suite.updater import UpdateError
        up = self._up(FakeGitHub(_release(35), b""))
        with self.assertRaises(UpdateError):
            up.apply(lambda: None, popen=lambda *a, **k: None)
        good = b"MZ" + b"n" * 50
        up = self._up(FakeGitHub(_release(35, good), good))
        up.download()
        os.remove(up._new_path)                               # vanished before the swap
        with self.assertRaises(UpdateError):
            up.apply(lambda: None, popen=lambda *a, **k: None)
        with open(self.exe, "rb") as f:
            self.assertEqual(f.read(), b"MZ-old-version")     # the app is untouched

    def test_from_source_cannot_update(self):
        from suite.updater import Updater, UpdateError
        up = Updater(keys=_MemKeys("github_pat_" + "a" * 40), opener=FakeGitHub(_release(35), b""),
                     exe_path=None, build=None, commit=None, log=lambda *a: None)
        up.exe = None
        self.assertFalse(up.info()["canApply"])
        with self.assertRaises(UpdateError):
            up.download()

    def test_key_store_round_trip(self):
        from suite.updater import KeyStore
        path = os.path.join(self.dir, "update.json")
        ks = KeyStore(path)
        self.assertIsNone(ks.load())
        tok = "github_pat_" + "Z9" * 30
        ks.save(tok)
        self.assertEqual(ks.load(), tok)
        if sys.platform == "win32":
            with open(path, encoding="utf-8") as f:
                self.assertNotIn(tok, f.read())               # encrypted for this Windows account
        ks.clear()
        self.assertIsNone(ks.load())

    def test_new_copy_waits_for_the_old_one(self):
        import subprocess
        from suite.updater import wait_for_exit
        p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(0.8)"])
        t = time.time()
        wait_for_exit([p.pid], timeout=10)
        p.wait()
        self.assertGreater(time.time() - t, 0.5)
        t = time.time()
        wait_for_exit([999999999], timeout=10)               # already gone: no wait
        self.assertLess(time.time() - t, 0.5)


class CarArraysTest(unittest.TestCase):
    """Every car's arrays go to the Lap Analyzer so it can record any driver's laps."""

    def test_rounded_and_safe_for_json(self):
        from suite.bridge import CARIDX_SEND, car_array
        self.assertEqual(car_array([0.1234567891, -1.0, float("nan")], 6), [0.123457, -1.0, None])
        self.assertEqual(car_array([7123.6, 0.0], 0), [7124, 0])
        self.assertEqual(car_array([3, -1, True], None), [3, -1, True])
        json.dumps(car_array([float("inf"), 1.5], 3))
        # position needs to be fine enough for lap times: 1e-6 of a 6 km lap is 6 mm
        self.assertEqual(CARIDX_SEND["CarIdxLapDistPct"], 6)
        for name in ("CarIdxLap", "CarIdxOnPitRoad", "CarIdxTrackSurface", "CarIdxLastLapTime"):
            self.assertIn(name, CARIDX_SEND)


class PickTestsTest(unittest.TestCase):
    """Pull requests run the tests for what they change (scripts/pick_tests.py)."""

    def test_choices(self):
        sys.path.insert(0, os.path.join(ROOT, "scripts"))
        try:
            from pick_tests import pick
        finally:
            sys.path.pop(0)
        self.assertEqual(pick(["suite/web/analyzer/index.html"]), ["tests/test_analyzer.py", "tests/test_suite.py"])
        self.assertEqual(pick(["pitwall/normalize.py", "README.md"])[0], "tests/test_pipeline.py")
        self.assertEqual(pick(["requirements.txt"]), ["tests"])                  # unknown: everything
        self.assertEqual(pick([".github/workflows/build.yml"]), ["tests"])
        self.assertEqual(pick(["docs/CAR_MANUALS.md"]), ["tests/test_suite.py"])  # always something


# --------------------------------------------------------------------------- #
# Closing the app ends everything it started.
# --------------------------------------------------------------------------- #

APP_CHILD = r"""
import json, subprocess, sys, threading, time
sys.path.insert(0, sys.argv[1])
from suite import app
tunnel, mode = sys.argv[2], sys.argv[3]
e = app.Engine(rate=20, pitwall_source="fake")
e.start(18640)
sh = e.pitwall.share_service(exe_path=tunnel, verify=lambda p: None,
                             popen=lambda args, **kw: subprocess.Popen([sys.executable, tunnel], **kw))
app.close_with_app(e)                      # what main() wires up
sh.start()
t0 = time.time()
while sh.state != "on" and time.time() - t0 < 20:
    time.sleep(0.05)
print(json.dumps({"state": sh.state, "tunnel": sh._proc.pid if sh._proc else None, "ports": [e.port, e.pitwall.port]}), flush=True)
if mode == "close":                        # the window's close event runs quit_now on the GUI thread
    threading.Timer(0.3, app.quit_now).start()
threading.Event().wait()
"""

FAKE_CLOUDFLARED = "import sys, time\n" + "".join("print(%r, flush=True)\n" % ln for ln in TUNNEL_OUTPUT) + "time.sleep(600)\n"


def _alive(pid):
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32")
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return code.value == 259                           # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:                                                    # a finished child not yet reaped is a zombie
        with open("/proc/%d/stat" % pid) as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _port_free(port):
    """Nothing is listening there any more (a closed connection may still be in TIME_WAIT,
    which does not stop the app listening again: asyncio servers reuse the address)."""
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        return False
    except OSError:
        return True
    finally:
        s.close()


class AppClosesTest(unittest.TestCase):
    """Run the app's engine with PitWall and a share tunnel (a stand-in cloudflared, a real
    separate program), then close it: nothing it started may keep running."""

    def _run(self, mode):
        import subprocess
        d = tempfile.mkdtemp()
        tunnel = os.path.join(d, "cloudflared.py")
        with open(tunnel, "w") as f:
            f.write(FAKE_CLOUDFLARED)
        child = os.path.join(d, "app_child.py")
        with open(child, "w") as f:
            f.write(APP_CHILD)
        p = subprocess.Popen([sys.executable, child, ROOT, tunnel, mode], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        info = None
        for line in p.stdout:
            if line.startswith("{"):
                info = json.loads(line)
                break
        self.assertIsNotNone(info, "the app did not start")
        self.assertEqual(info["state"], "on")
        self.assertTrue(_alive(info["tunnel"]))
        if mode == "kill":
            p.kill()
        code = p.wait(timeout=20)
        return info, code

    def _gone(self, info):
        self.assertTrue(_wait(lambda: not _alive(info["tunnel"]), 10), "the tunnel outlived the app")
        for port in info["ports"]:
            self.assertTrue(_wait(lambda: _port_free(port), 10), "port %d still taken" % port)

    def test_closing_the_window_ends_everything(self):
        info, code = self._run("close")
        self.assertEqual(code, 0)
        self._gone(info)

    @unittest.skipUnless(sys.platform == "win32", "the kill-with-the-app job is Windows only")
    def test_killed_app_takes_the_tunnel_with_it(self):
        info, _ = self._run("kill")                         # Task Manager, a crash
        self._gone(info)
