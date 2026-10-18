"""
Share PitWall online from the suite.

The suite's relay only listens on this PC (127.0.0.1). Sharing starts a
Cloudflare quick tunnel (cloudflared, free, no account) that gives the relay a
temporary public https address. Remote crew open PitWall at that address; the
relay serves the dashboard page itself at /pitwall/.

- cloudflared is downloaded from Cloudflare's GitHub releases the first time,
  into %LOCALAPPDATA%\\iRacingSuite, not bundled with the app.
- Each time sharing starts, the relay gets a fresh guest token. The share link
  carries only that token: it can watch, it cannot touch the sim, the pit menu
  or the broadcast graphics. Stopping sharing revokes the token and disconnects
  everyone who joined with it, and the tunnel address dies with the tunnel.
- On Windows the tunnel runs inside a job object that ends it when the suite
  ends, even if the suite crashes, so a link never outlives the app.

Nothing here reads iRacing; it only moves the relay's existing stream.
"""
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
import urllib.request

DOWNLOAD_URL = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
URL_RE = re.compile(r"https://([a-z0-9-]+)\.trycloudflare\.com")
# cloudflared also names its own service (https://api.trycloudflare.com/tunnel)
# in errors; that is not a tunnel address.
NOT_TUNNELS = {"api", "www"}
READY_RE = re.compile(r"Registered tunnel connection", re.I)
# The address is printed before a connection to Cloudflare is up. If the
# "registered" line never shows (wording changes between versions), the link is
# offered anyway after this long.
READY_FALLBACK_S = 8.0
MIN_EXE_BYTES = 5 * 1024 * 1024


# cloudflared.exe is run with the user's rights, so it must be Cloudflare's own:
# a valid Authenticode signature from this organisation (verified on the real
# release, 2026-10-16: "Cloudflare, Inc.", DigiCert code-signing CA, timestamped).
# Checked after every download and before every launch -- the file in the app
# folder could have been replaced between sessions.
SIGNER_ORG = "Cloudflare, Inc."
_SIG_PS = ("$s = Get-AuthenticodeSignature -LiteralPath $env:PITWALL_SIG_PATH; "
           "[pscustomobject]@{status=[string]$s.Status; "
           "subject=$(if ($s.SignerCertificate) { $s.SignerCertificate.Subject } else { '' })} "
           "| ConvertTo-Json -Compress")


def signer_org(subject: str) -> str | None:
    """The O= (organisation) of a certificate subject, quoted or not."""
    m = re.search(r'(?:^|,\s*)O=("(?:[^"]|"")*"|[^,]*)', subject or "")
    if not m:
        return None
    v = m.group(1)
    return v[1:-1].replace('""', '"') if v.startswith('"') else v.strip()


def signature_problem(path: str) -> str | None:
    """Why this program must not be run, or None when it is signed by Cloudflare."""
    if sys.platform != "win32":
        return "the tunnel program can only be checked on Windows"
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _SIG_PS],
                           env={**os.environ, "PITWALL_SIG_PATH": path}, capture_output=True, text=True,
                           timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        sig = json.loads(r.stdout or "{}")
    except Exception as e:  # noqa: BLE001
        return "could not check its signature (%s)" % e
    if sig.get("status") != "Valid":
        return "it is not validly signed (%s)" % (sig.get("status") or "no signature")
    if signer_org(sig.get("subject", "")) != SIGNER_ORG:
        return "it is signed by %s, not %s" % (signer_org(sig.get("subject", "")) or "someone else", SIGNER_ORG)
    return None


def app_folder():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "iRacingSuite")
    os.makedirs(folder, exist_ok=True)
    return folder


def qr_svg(text):
    """The link as a QR code (SVG text), or None if segno is missing."""
    try:
        import segno
    except Exception:
        return None
    import io
    buf = io.BytesIO()
    segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2, dark="#000", light="#fff",
                                     xmldecl=False, svgns=True)
    return buf.getvalue().decode("utf-8")


def share_links(base, guest_token, producer_token=None, view_token=None):
    """Addresses to hand out once the tunnel is up. base: https://x.trycloudflare.com"""
    base = base.rstrip("/")
    wss = "wss://" + base.split("://", 1)[1]
    out = {"base": base,
           "pitwall": "%s/pitwall/?relay=%s&token=%s" % (base, wss, guest_token),
           # The Lap Analyzer: live telemetry, the car setup and the engineer, read-only.
           "analyzer": "%s/analyzer/?token=%s" % (base, guest_token)}
    if producer_token and view_token:
        # For a broadcast co-host. It cuts your camera: share it with them only.
        out["producer"] = "%s/broadcast/producer.html?token=%s&view=%s" % (base, producer_token, view_token)
    return out


class _KillWithParent:
    """Windows job object: processes in it end when the suite's process ends."""

    def __init__(self):
        self.handle = None
        if sys.platform != "win32":
            return
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [(n, ctypes.c_ulonglong) for n in
                            ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                             "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

            class BASIC(ctypes.Structure):
                _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                            ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                            ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                            ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                            ("SchedulingClass", wintypes.DWORD)]

            class EXTENDED(ctypes.Structure):
                _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                            ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                            ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

            k32.CreateJobObjectW.restype = wintypes.HANDLE
            k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
            k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
            k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
            h = k32.CreateJobObjectW(None, None)
            if not h:
                return
            info = EXTENDED()
            info.BasicLimitInformation.LimitFlags = 0x2000   # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # Extended info
                return
            self.handle = h
            self._k32 = k32
        except Exception:
            self.handle = None

    def add(self, proc):
        if self.handle is None:
            return False
        try:
            return bool(self._k32.AssignProcessToJobObject(self.handle, int(proc._handle)))
        except Exception:
            return False


class Share:
    """state: off | downloading | starting | on | error"""

    def __init__(self, relay_port_fn, set_guest_token, producer_token=None, view_token=None,
                 exe_path=None, download_url=DOWNLOAD_URL, log=print, popen=subprocess.Popen,
                 ready_fallback_s=READY_FALLBACK_S, verify=signature_problem):
        self._port = relay_port_fn
        self._set_guest = set_guest_token
        self._producer = producer_token
        self._view = view_token
        self.exe = exe_path or os.path.join(app_folder(), "cloudflared.exe")
        self.download_url = download_url
        self.log = log
        self._popen = popen
        self._verify = verify
        self._ready_fallback_s = ready_fallback_s
        self._lock = threading.Lock()
        self._proc = None
        self._gen = 0
        self._job = None
        self.state = "off"
        self.error = None
        self.progress = None
        self.url = None
        self.token = None
        self.started_at = None
        self._tail = []

    # ---------------------------------------------------------------- public
    def start(self):
        with self._lock:
            if self.state in ("downloading", "starting", "on"):
                return self.info()
            self._gen += 1
            gen = self._gen
            self.state, self.error, self.url, self.progress, self._tail = "starting", None, None, None, []
        threading.Thread(target=self._run, args=(gen,), daemon=True).start()
        return self.info()

    def stop(self):
        with self._lock:
            self._gen += 1
            proc, self._proc = self._proc, None
            self.state, self.url, self.progress, self.started_at = "off", None, None, None
            token, self.token = self.token, None
        if token:
            self._revoke()
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        return self.info()

    def info(self):
        d = {"state": self.state, "error": self.error, "progress": self.progress,
             "downloaded": os.path.isfile(self.exe), "since": self.started_at}
        if self.state == "on" and self.url and self.token:
            d["links"] = share_links(self.url, self.token, self._producer, self._view)
            d["qr"] = qr_svg(d["links"]["pitwall"])
        return d

    # ---------------------------------------------------------------- work
    def _current(self, gen):
        return gen == self._gen

    def _fail(self, gen, msg):
        with self._lock:
            if not self._current(gen):
                return
            self.state, self.error = "error", msg
            proc, self._proc = self._proc, None
            token, self.token = self.token, None
        if token:
            self._revoke()
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass
        self.log("share: " + msg)

    def _revoke(self):
        try:
            self._set_guest(None)
        except Exception as e:
            self.log("share: could not revoke the guest token: %s" % e)

    def _download(self, gen):
        self.state, self.progress = "downloading", 0.0
        part = self.exe + ".part"
        try:
            req = urllib.request.Request(self.download_url, headers={"User-Agent": "iRacingSuite"})
            with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:
                total = int(r.headers.get("Content-Length") or 0)
                got = 0
                while True:
                    if not self._current(gen):
                        raise InterruptedError
                    block = r.read(256 * 1024)
                    if not block:
                        break
                    f.write(block)
                    got += len(block)
                    self.progress = round(got / total, 3) if total else None
            with open(part, "rb") as f:
                head = f.read(2)
            if head != b"MZ" or os.path.getsize(part) < MIN_EXE_BYTES:
                raise ValueError("the download was not a Windows program")
            why = self._verify(part)
            if why:
                raise ValueError("the download is not Cloudflare's program: " + why)
            os.replace(part, self.exe)
        finally:
            try:
                os.remove(part)
            except OSError:
                pass
        self.progress = None

    def _run(self, gen):
        port = self._port()
        if not port:
            return self._fail(gen, "PitWall's relay is not running, so there is nothing to share.")
        if not os.path.isfile(self.exe):
            try:
                self._download(gen)
            except InterruptedError:
                return
            except Exception as e:
                return self._fail(gen, "Could not download Cloudflare's tunnel program (%s). "
                                       "Check the internet connection and try again." % e)
        if not self._current(gen):
            return
        why = self._verify(self.exe)
        if why:
            try:
                os.remove(self.exe)          # the next share downloads a fresh copy
            except OSError:
                pass
            return self._fail(gen, "The tunnel program in the app folder is not Cloudflare's, so it was "
                                   "not run (%s). It has been removed; Share online again to download "
                                   "a fresh copy." % why)
        self.state = "starting"
        token = secrets.token_urlsafe(16)
        try:
            self._set_guest(token)
        except Exception as e:
            return self._fail(gen, "Could not set up the share token: %s" % e)
        self.token = token
        args = [self.exe, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port]
        kw = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT, "stdin": subprocess.DEVNULL}
        if sys.platform == "win32":
            kw["creationflags"] = 0x08000000   # CREATE_NO_WINDOW: no console flashes up
        try:
            proc = self._popen(args, **kw)
        except Exception as e:
            return self._fail(gen, "Could not start the tunnel: %s" % e)
        with self._lock:
            if not self._current(gen):
                try:
                    proc.kill()
                except Exception:
                    pass
                return
            self._proc = proc
        if self._job is None:
            self._job = _KillWithParent()
        self._job.add(proc)
        threading.Thread(target=self._fallback, args=(gen,), daemon=True).start()
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace").strip() if isinstance(raw, bytes) else str(raw).strip()
            if not line:
                continue
            self._tail = (self._tail + [line])[-8:]
            if not self._current(gen):
                break
            m = next((x for x in URL_RE.finditer(line) if x.group(1) not in NOT_TUNNELS), None)
            if m and not self.url:
                self.url = m.group(0)
                self._seen_url_at = time.time()
                self.log("share: tunnel address %s" % self.url)
            if READY_RE.search(line) and self.url and self.state != "on":
                self._go_live(gen)
        if self._current(gen) and self.state != "off":
            last = self._tail[-1] if self._tail else "no output"
            self._fail(gen, "The tunnel stopped (%s)." % last[:200])

    def _go_live(self, gen):
        with self._lock:
            if self._current(gen) and self.url and self.state == "starting":
                self.state = "on"
                self.started_at = time.time()

    def _fallback(self, gen):
        deadline = time.time() + 60
        while time.time() < deadline and self._current(gen) and self.state == "starting":
            if self.url and time.time() - getattr(self, "_seen_url_at", time.time()) >= self._ready_fallback_s:
                self._go_live(gen)
                return
            time.sleep(0.2)
        if self._current(gen) and self.state == "starting":
            self._fail(gen, "Cloudflare did not hand out an address within a minute. Try again in a moment.")
