"""
Hosts PitWall inside the suite.

Runs PitWall's own relay and bridge, unchanged, in the suite's event loop: the
same thing run-local.ps1 does in three windows. The relay binds 127.0.0.1 only,
so nothing is reachable from outside this PC. The dashboard (client/index.html)
is served by the suite at /pitwall/, and the broadcast pages by the relay
itself at /broadcast/.

Tokens: the bridge token is fresh each launch. The view and producer tokens are
kept in %LOCALAPPDATA%\\iRacingSuite\\broadcast.json, because an OBS browser
source stores the overlay address once and a token that changed every launch
would blank the overlay on the next stream.
"""
import asyncio
import json
import os
import secrets


def _token_file():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "iRacingSuite")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "broadcast.json")


def load_tokens(path=None):
    """Read the kept view/producer tokens, creating them the first time."""
    path = path or _token_file()
    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
        if isinstance(t.get("view"), str) and isinstance(t.get("producer"), str) and t["view"] != t["producer"]:
            return t
    except (OSError, ValueError, AttributeError):
        pass
    t = {"view": secrets.token_urlsafe(16), "producer": secrets.token_urlsafe(16)}
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(t, f)
    except OSError:
        pass   # still works this launch; the overlay address just will not survive a restart
    return t


class PitWallHost:
    def __init__(self, source_kind="live", log=print, token_path=None):
        self.source_kind = source_kind
        self.log = log
        self.port = None
        self.error = None
        self.bridge = None
        tokens = load_tokens(token_path)
        self.view_token = tokens["view"]
        self.producer_token = tokens["producer"]
        self._bridge_token = secrets.token_urlsafe(16)
        self.relay = None
        self._loop = None
        self.share = None
        self.analyzer_hub = None      # suite.bridge.Hub, set by the app

    async def start(self, port=8765):
        try:
            from websockets.asyncio.server import serve
            from relay.server import Relay
            from pitwall.bridge import Bridge
            from pitwall.sources import make_source
        except Exception as e:  # missing package in a broken install
            self.error = "PitWall could not load: %s: %s" % (type(e).__name__, e)
            self.log(self.error)
            return
        self._loop = asyncio.get_running_loop()
        relay = Relay(self._bridge_token, self.view_token, None, self.producer_token,
                      assets_dir=os.path.join(os.path.dirname(_token_file()), "broadcast_assets"))
        for p in range(port, port + 10):
            try:
                self._server = await serve(relay.handler, "127.0.0.1", p, process_request=relay.process_request,
                                           max_size=40 * 1024 * 1024, ping_interval=20)
                self.port = p
                break
            except OSError:
                continue
        if self.analyzer_hub is not None:
            # The Lap Analyzer through the same address, so a share link can show it.
            from suite.bridge import relay_feed, analyzer_page, analyzer_reply
            hub = self.analyzer_hub
            relay.analyzer_feed = lambda ws: relay_feed(hub, ws)
            relay.analyzer_page = analyzer_page()
            relay.analyzer_serve = analyzer_reply
        self.relay = relay
        if self.port is None:
            self.error = "No free port for PitWall's relay between %d and %d" % (port, port + 9)
            self.log(self.error)
            return
        try:
            kw = {"realtime": True, "incidents": True} if self.source_kind == "fake" else {}
            src = make_source(self.source_kind, None, **kw)
        except Exception as e:
            self.error = "PitWall could not open iRacing's telemetry: %s: %s" % (type(e).__name__, e)
            self.log(self.error)
            return
        # Camera control is on: the only way to reach this relay is from this PC,
        # with the producer token, and cutting cameras is the producer's job.
        # Pit commands stay off, as before.
        # Everything stays on this PC, so PitWall runs at the Lap Analyzer's
        # pace: your car at 60 updates a second (iRacing's own rate), the field
        # at 30. Measured cost: ~0.2 ms per player message, ~2.5 ms per field
        # message including encoding, a few percent of one core.
        self.bridge = Bridge(src, "ws://127.0.0.1:%d" % self.port, self._bridge_token,
                             state_hz=60.0, cars_hz=30.0, allow_camera=True, log=self.log)
        # Keep the task: asyncio holds only a weak reference to a running task,
        # and a bridge that ends must say so rather than leave PitWall silently dead.
        self._bridge_task = asyncio.get_running_loop().create_task(self.bridge.run())
        self._bridge_task.add_done_callback(self._bridge_ended)
        self.log("PitWall relay on ws://127.0.0.1:%d, bridge source: %s" % (self.port, self.source_kind))

    def _bridge_ended(self, task):
        if task.cancelled():
            return
        e = task.exception()
        self.error = ("PitWall's bridge stopped: %s: %s" % (type(e).__name__, e)) if e else \
            "PitWall's bridge stopped. Restart the suite to bring it back."
        self.log(self.error)

    def _set_guest_token(self, token):
        """Called from the share thread; the relay lives on the event loop."""
        if self.relay is None or self._loop is None:
            raise RuntimeError("the relay is not running")
        self._loop.call_soon_threadsafe(self.relay.set_guest_token, token)

    def share_service(self, **kw):
        """The "Share online" tunnel for this relay (created on first use)."""
        if self.share is None:
            from suite.share import Share
            self.share = Share(lambda: self.port if self.error is None else None, self._set_guest_token,
                               producer_token=self.producer_token, view_token=self.view_token, log=self.log, **kw)
        return self.share

    def info(self):
        ok = self.port is not None and self.error is None
        base = "http://127.0.0.1:%d/broadcast/" % self.port if self.port else None
        return {"ok": ok,
                "relay": "ws://127.0.0.1:%d" % self.port if self.port else None,
                "token": self.view_token if self.port else None,
                "source": self.source_kind, "error": self.error,
                "broadcast": {
                    "overlay": base + "overlay.html?token=" + self.view_token,
                    "producer": base + "producer.html?token=" + self.producer_token + "&view=" + self.view_token,
                } if ok else None}
