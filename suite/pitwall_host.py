"""
Hosts PitWall inside the suite.

Runs PitWall's own relay and bridge, unchanged, in the suite's event loop: the
same thing run-local.ps1 does in three windows. The relay binds 127.0.0.1 only
and gets fresh random tokens each launch, so nothing is reachable from outside
this PC. The dashboard (client/index.html) is served by the suite at /pitwall/.
"""
import asyncio
import secrets


class PitWallHost:
    def __init__(self, source_kind="live", log=print):
        self.source_kind = source_kind
        self.log = log
        self.port = None
        self.error = None
        self.bridge = None
        self.view_token = secrets.token_urlsafe(16)
        self._bridge_token = secrets.token_urlsafe(16)

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
        relay = Relay(self._bridge_token, self.view_token, None)
        for p in range(port, port + 10):
            try:
                self._server = await serve(relay.handler, "127.0.0.1", p, process_request=relay.process_request,
                                           max_size=8 * 1024 * 1024, ping_interval=20)
                self.port = p
                break
            except OSError:
                continue
        if self.port is None:
            self.error = "No free port for PitWall's relay between %d and %d" % (port, port + 9)
            self.log(self.error)
            return
        try:
            kw = {"realtime": True} if self.source_kind == "fake" else {}
            src = make_source(self.source_kind, None, **kw)
        except Exception as e:
            self.error = "PitWall could not open iRacing's telemetry: %s: %s" % (type(e).__name__, e)
            self.log(self.error)
            return
        self.bridge = Bridge(src, "ws://127.0.0.1:%d" % self.port, self._bridge_token, log=self.log)
        asyncio.get_running_loop().create_task(self.bridge.run())
        self.log("PitWall relay on ws://127.0.0.1:%d, bridge source: %s" % (self.port, self.source_kind))

    def info(self):
        return {"ok": self.port is not None and self.error is None,
                "relay": "ws://127.0.0.1:%d" % self.port if self.port else None,
                "token": self.view_token if self.port else None,
                "source": self.source_kind, "error": self.error}
