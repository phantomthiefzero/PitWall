"""Second camera: the co-host's iRacing as the broadcast's picture-in-picture camera.

iRacing draws one camera per copy of the sim, so an F1-style picture-in-picture needs a second
copy: the co-host spectating the same session on their own PC. Their video reaches the host
through VDO.Ninja (a browser link the host pastes into the producer panel); this module is the
part that ties their sim to the host's broadcast. Run in the CO-HOST's suite, with the host's
co-host (producer) link:

- it tells the host which car the co-host's camera is on (gfx pip.second.cam), so the picture-in-
  picture caption names the right driver without anyone typing it;
- it follows the host's picks: when the host's producer asks for a car (gfx pip.want, a new seq),
  this suite cuts the co-host's own camera to it, through the co-host's own bridge (CommandGate,
  the Director's owner) like any producer cut.

Camera moves happen only while the host's broadcast is on (the host's master switch, its
"director" message). Starting a second camera is the co-host's own choice to hand their camera
over, so it turns their own master switch on (their bridge refuses camera commands otherwise)
and their own director and grid walk off, so nothing on their side fights the host's picks;
stopping turns their switch off again.

Runs on its own threads (two WebSocket connections): nothing here touches the app's event loop.
The message handling is plain methods (on_local, on_remote) so it is tested without sockets.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse

HEARTBEAT_S = 60.0          # pip.second.seen is refreshed this often, so the host can tell it is alive
CAM_SETTLE_S = 0.6          # the camera car must hold this long before the host is told (a cut can pass cars)


def remote_from_link(link: str):
    """(ws URL of the host's relay viewer endpoint, producer token) from a co-host link such as
    https://name.trycloudflare.com/broadcast/producer.html?token=P&view=V. None if it is not one."""
    try:
        u = urllib.parse.urlparse((link or "").strip())
    except ValueError:
        return None
    if u.scheme not in ("http", "https", "ws", "wss") or not u.netloc:
        return None
    q = urllib.parse.parse_qs(u.query)
    tok = (q.get("token") or [""])[0]
    if not tok:
        return None
    scheme = {"http": "ws", "https": "wss"}.get(u.scheme, u.scheme)
    relay = (q.get("relay") or [""])[0]
    base = relay.rstrip("/") if relay.startswith(("ws://", "wss://")) else "%s://%s" % (scheme, u.netloc)
    return base + "/view?token=" + urllib.parse.quote(tok), tok


def _merge(into: dict, frm: dict) -> dict:
    """The producer page's merge: a delta's None deletes, dicts merge, anything else replaces."""
    for k, v in frm.items():
        if v is None:
            into.pop(k, None)
        elif isinstance(v, dict) and isinstance(into.get(k), dict):
            _merge(into[k], v)
        else:
            into[k] = v
    return into


class SecondCamera:
    def __init__(self, local_fn, log=print, clock=time.time):
        """local_fn() -> (ws URL of this suite's relay viewer endpoint, its producer token) or None."""
        self._local_fn = local_fn
        self.log = log
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads = []
        self._send_local = self._send_remote = None
        self.reset()
        self.state, self.error, self.host = "off", None, None

    def reset(self):
        self.name = ""
        self.drivers: dict = {}        # carIdx -> {num, name, uid}
        self.groups: dict = {}         # group name (lower case) -> num, this PC's cameras
        self.cam = None                # this sim's camera car idx, as last told to the host
        self._cam_cand = (None, 0.0)
        self.state_msg: dict = {}
        self.host_live = False
        self.want_seq = None           # the last pip.want seq acted on (or seen when it could not be)
        self.last_want = None
        self._seen_at = 0.0
        self.remote_ok = self.local_ok = False

    # ------------------------------------------------------------ the logic
    def _second(self, now):
        d = self.drivers.get(self.cam) or {}
        cam = {"idx": self.cam, "num": d.get("num"), "uid": d.get("uid"), "name": d.get("name")} if self.cam is not None else None
        return {"type": "gfx", "set": {"pip": {"second": {"name": self.name or "Second camera", "cam": cam,
                                                           "seen": int(now * 1000)}}},
                "by": (self.name or "Second camera") + " (second camera)"}

    def hello_local(self):
        """What goes to this suite's own bridge on connect: the master switch on, the director and
        the grid walk off (the host decides where this camera looks)."""
        return [{"type": "cmd", "cmd": "director", "action": "broadcast", "on": True},
                {"type": "cmd", "cmd": "director", "action": "set", "on": False, "autoGrid": False},
                {"type": "resend"}]

    def on_local(self, m: dict, now=None):
        """A message from this suite's relay. Returns messages for the host's relay."""
        now = self.clock() if now is None else now
        t = m.get("type")
        if t == "session":
            self.drivers = {d.get("CarIdx"): {"num": d.get("CarNumber"), "name": d.get("UserName"), "uid": d.get("UserID")}
                            for d in (m.get("drivers") or []) if d.get("CarIdx") is not None}
            self.groups = {str(g.get("name") or "").lower(): g.get("num") for g in (m.get("cameras") or []) if g.get("num") is not None}
        elif t == "state":
            if m.get("full") or not self.state_msg:
                self.state_msg = dict(m)
            else:
                _merge(self.state_msg, m)
        out = []
        cam = ((self.state_msg.get("camera") or {}).get("carIdx")) if self.state_msg else None
        if cam is not None and cam != self._cam_cand[0]:
            self._cam_cand = (cam, now)
        cand, since = self._cam_cand
        if cand is not None and cand != self.cam and now - since >= CAM_SETTLE_S and cand in self.drivers:
            self.cam = cand
            self._seen_at = now
            out.append(self._second(now))
        elif self.remote_ok and now - self._seen_at >= HEARTBEAT_S:
            self._seen_at = now
            out.append(self._second(now))
        return out

    def on_remote(self, m: dict, now=None):
        """A message from the host's relay. Returns messages for this suite's relay (camera cuts)."""
        t = m.get("type")
        if t == "director":
            self.host_live = bool(m.get("live"))
        if t != "gfx" and t != "director":
            return []
        want = ((m.get("state") or {}).get("pip") or {}).get("want") if t == "gfx" else self.last_want
        if t == "gfx":
            self.last_want = want
        if not isinstance(want, dict) or want.get("seq") is None or want.get("seq") == self.want_seq:
            return []
        if not self.host_live:
            return []                                  # held: acted on if the host turns the broadcast on
        self.want_seq = want.get("seq")
        if want.get("num") in (None, ""):
            return []
        group = None
        if want.get("group"):
            group = self.groups.get(str(want["group"]).lower())
        if group is None:
            group = (self.state_msg.get("camera") or {}).get("group") or 0
        return [{"type": "cmd", "cmd": "camera", "action": "car", "car": str(want["num"]), "group": group, "camera": 0}]

    def goodbye_remote(self):
        return {"type": "gfx", "set": {"pip": {"second": None}}, "by": (self.name or "Second camera") + " (second camera)"}

    # ------------------------------------------------------------ running it
    def start(self, link, name=""):
        with self._lock:
            if self.state in ("connecting", "on"):
                return self.info()
            rem = remote_from_link(link)
            if rem is None:
                self.state, self.error = "error", "That is not a co-host link. Ask the host for the Broadcast co-host link (Share online)."
                return self.info()
            loc = self._local_fn()
            if not loc:
                self.state, self.error = "error", "Broadcast is not running in this app yet."
                return self.info()
            self.reset()
            self.name = (name or "").strip()[:40]
            self.host = urllib.parse.urlparse(rem[0]).netloc
            self.state, self.error = "connecting", None
            self._stop.clear()
            self._threads = [threading.Thread(target=self._run_local, args=(loc[0],), daemon=True, name="second-cam-local"),
                             threading.Thread(target=self._run_remote, args=(rem[0],), daemon=True, name="second-cam-remote")]
            for th in self._threads:
                th.start()
        return self.info()

    def stop(self, wait=2.0):
        with self._lock:
            if self.state == "off":
                return self.info()
        # the goodbyes go out while both connections are still open, then the threads end
        for fn, msg in ((self._send_remote, self.goodbye_remote()),
                        (self._send_local, {"type": "cmd", "cmd": "director", "action": "broadcast", "on": False})):
            try:
                if fn:
                    fn(msg)
            except Exception:
                pass
        self._stop.set()
        for th in self._threads:
            th.join(timeout=wait)
        with self._lock:
            self.state, self._threads = "off", []
            self._send_local = self._send_remote = None
        return self.info()

    def info(self):
        d = self.drivers.get(self.cam) or {}
        return {"state": self.state, "error": self.error, "host": self.host, "name": self.name,
                "hostConnected": self.remote_ok, "simConnected": self.local_ok, "hostLive": self.host_live,
                "camCar": {"num": d.get("num"), "name": d.get("name")} if self.cam is not None else None,
                "want": self.last_want}

    def _loop(self, url, which, handle):
        from websockets.sync.client import connect
        delay = 1.0
        while not self._stop.is_set():
            try:
                with connect(url, open_timeout=10, max_size=64 * 1024 * 1024, close_timeout=2) as ws:
                    sender = lambda obj, _ws=ws: _ws.send(json.dumps(obj))
                    if which == "local":
                        self._send_local, self.local_ok = sender, True
                        for msg in self.hello_local():
                            sender(msg)
                    else:
                        self._send_remote, self.remote_ok = sender, True
                        self._seen_at = 0.0                  # tell the host again at once
                        sender({"type": "resend"})
                    with self._lock:
                        if self.state == "connecting" and self.remote_ok and self.local_ok:
                            self.state, self.error = "on", None
                    delay = 1.0
                    while not self._stop.is_set():
                        try:
                            raw = ws.recv(timeout=1.0)
                        except TimeoutError:
                            raw = None
                        if raw is None and which == "remote":
                            continue
                        if raw and which == "local" and '"type":"cars"' in raw[:40]:
                            continue                         # the field, 30 a second: not needed here
                        try:
                            msg = json.loads(raw) if raw else {}
                        except (ValueError, TypeError):
                            continue
                        for out in handle(msg):
                            target = self._send_remote if which == "local" else self._send_local
                            if target:
                                try:
                                    target(out)
                                except Exception as e:
                                    self.log("second camera: send failed: %s" % e)
            except Exception as e:
                if self._stop.is_set():
                    break
                code = getattr(getattr(e, "rcvd", None), "code", None)
                self.error = ("The host's relay refused the link (it may have changed: ask for a new one)."
                              if which == "remote" and code == 4001 else
                              "Lost the %s: %s" % ("host's broadcast" if which == "remote" else "connection to this app's PitWall", e))
                self.log("second camera (%s): %s" % (which, e))
            finally:
                if which == "local":
                    self._send_local, self.local_ok = None, False
                else:
                    self._send_remote, self.remote_ok = None, False
            if self._stop.wait(delay):
                break
            delay = min(10.0, delay * 2)

    def _run_local(self, url):
        self._loop(url, "local", self.on_local)

    def _run_remote(self, url):
        self._loop(url, "remote", self.on_remote)
