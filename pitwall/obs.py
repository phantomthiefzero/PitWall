"""A small OBS Studio client (obs-websocket protocol v5, built into OBS 28+).

Used for one thing: taking a still of the iRacing capture source, so the
broadcast can show each car's livery exactly as iRacing draws it -- Trading
Paints paints included, because Trading Paints only delivers textures that
iRacing then puts on the car. Free, no plugin: OBS > Tools > WebSocket Server
Settings > Enable.

Protocol (https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md):
    server  op 0 Hello       {obsWebSocketVersion, rpcVersion, authentication?: {challenge, salt}}
    client  op 1 Identify    {rpcVersion: 1, authentication?: base64(sha256(base64(sha256(password + salt)) + challenge))}
    server  op 2 Identified
    client  op 6 Request     {requestType, requestId, requestData}
    server  op 7 RequestResponse {requestType, requestId, requestStatus: {result, code, comment}, responseData}

Synchronous on purpose: it runs on a worker thread beside the bridge.
"""
from __future__ import annotations

import base64
import hashlib
import itertools
import json
import threading


def obs_url_problem(url: str) -> str | None:
    """Why this OBS address is refused, or None if it is fine.

    OBS runs on this PC or on a streaming PC on the same network. The address is
    set from the producer panel, and connecting runs OBS's login exchange with
    the stored password -- which hands anyone at the far end material to guess
    that password offline. So only this PC and private network addresses are
    accepted; a hostname must resolve to nothing else.
    """
    import ipaddress
    import socket
    import urllib.parse
    try:
        u = urllib.parse.urlparse((url or "").strip())
        host, port = u.hostname, u.port
    except ValueError:
        return "that is not a valid address"
    if u.scheme not in ("ws", "wss") or not host:
        return "the OBS address starts ws:// (OBS > Tools > WebSocket Server Settings shows it)"
    try:
        addrs = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            addrs = {ipaddress.ip_address(a[4][0].split("%")[0])
                     for a in socket.getaddrinfo(host, port or 4455, proto=socket.IPPROTO_TCP)}
        except OSError:
            return f"cannot find {host} on this network"
    for a in addrs:
        if a.is_unspecified or not (a.is_loopback or a.is_private or a.is_link_local):
            return "OBS must be on this PC or your local network, not an internet address"
    return None


def auth_string(password: str, salt: str, challenge: str) -> str:
    secret = base64.b64encode(hashlib.sha256((password + salt).encode()).digest()).decode()
    return base64.b64encode(hashlib.sha256((secret + challenge).encode()).digest()).decode()


class OBSError(RuntimeError):
    pass


class OBSClient:
    def __init__(self, url: str = "ws://127.0.0.1:4455", password: str = "", timeout: float = 5.0):
        self.url = url
        self.password = password or ""
        self.timeout = timeout
        self._ws = None
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ #
    def connect(self):
        from websockets.sync.client import connect
        try:
            cm = connect(self.url, open_timeout=self.timeout, max_size=64 * 1024 * 1024)
            # websockets 15+ wants the context-manager form; older ones return the
            # connection itself, whose __enter__ returns itself.
            ws = cm.__enter__()
        except Exception as e:  # noqa: BLE001
            raise OBSError(f"cannot reach OBS at {self.url} ({type(e).__name__}: {e}). "
                           "In OBS: Tools > WebSocket Server Settings > Enable WebSocket server") from e
        hello = json.loads(ws.recv(timeout=self.timeout))
        if hello.get("op") != 0:
            ws.close()
            raise OBSError("that address is not an OBS WebSocket server (no Hello)")
        d = hello.get("d") or {}
        ident = {"rpcVersion": 1}
        auth = d.get("authentication")
        if auth:
            if not self.password:
                ws.close()
                raise OBSError("OBS asks for a password: copy it from Tools > WebSocket Server Settings > Show Connect Info")
            ident["authentication"] = auth_string(self.password, auth["salt"], auth["challenge"])
        ws.send(json.dumps({"op": 1, "d": ident}))
        try:
            ok = json.loads(ws.recv(timeout=self.timeout))
        except Exception as e:  # noqa: BLE001  (OBS closes the socket on a wrong password)
            raise OBSError("OBS refused the connection: check the password") from e
        if ok.get("op") != 2:
            ws.close()
            raise OBSError("OBS did not accept the identification")
        self._ws = ws
        return d.get("obsWebSocketVersion")

    def close(self):
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:  # noqa: BLE001
                pass
            self._ws = None

    def request(self, request_type: str, data: dict | None = None) -> dict:
        with self._lock:
            if self._ws is None:
                self.connect()
            rid = str(next(self._ids))
            msg = {"op": 6, "d": {"requestType": request_type, "requestId": rid, "requestData": data or {}}}
            try:
                self._ws.send(json.dumps(msg))
                while True:
                    r = json.loads(self._ws.recv(timeout=self.timeout))
                    if r.get("op") == 7 and (r.get("d") or {}).get("requestId") == rid:
                        break
            except Exception as e:  # noqa: BLE001
                self.close()
                raise OBSError(f"lost OBS ({type(e).__name__}: {e})") from e
            d = r["d"]
            st = d.get("requestStatus") or {}
            if not st.get("result"):
                raise OBSError(f"OBS {request_type} failed: {st.get('comment') or st.get('code')}")
            return d.get("responseData") or {}

    # ------------------------------------------------------------------ #
    def sources(self) -> list[str]:
        """Names of inputs that can be photographed (captures, media, ...)."""
        r = self.request("GetInputList")
        return [i.get("inputName") for i in r.get("inputs") or [] if i.get("inputName")]

    def screenshot(self, source: str, width: int = 800, quality: int = 80) -> str:
        """A JPEG data URI of `source` as OBS sees it, without the overlay on top
        (the overlay is a different source)."""
        r = self.request("GetSourceScreenshot", {"sourceName": source, "imageFormat": "jpg",
                                                 "imageWidth": int(width), "imageCompressionQuality": int(quality)})
        img = r.get("imageData") or ""
        if not img.startswith("data:image"):
            raise OBSError("OBS returned no image")
        return img
