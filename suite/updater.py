"""
Update the suite from the repo's GitHub releases.

Every push to master builds "iRacing Suite.exe" and publishes it as release
"build-<N>". The repo is public, so no key is needed to read its releases. A key
pasted while the repo was private (a fine-grained token, Contents: read-only) is
still sent if there is one, kept in %LOCALAPPDATA%\\iRacingSuite encrypted with
Windows' DPAPI; if GitHub refuses it, the request is made again without it.

Updating:
1. check()    -- the latest release, its build number, and the commits since ours
2. download() -- the .exe next to the running one (same drive, so the swap is a
                 rename), checked against the size and SHA-256 GitHub publishes
3. apply()    -- rename the running .exe aside (Windows allows renaming a running
                 program, not overwriting it), put the new one in its place, start
                 it with --after-update so it waits for this process to end before
                 taking the ports, and quit. The new one deletes the old file.

Nothing here touches iRacing. The key is sent only to api.github.com, never to
the download host GitHub redirects to.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "phantomthiefzero/PitWall"
API = "https://api.github.com"
KEY_PAGE = "https://github.com/settings/personal-access-tokens/new"
TAG_RE = re.compile(r"^build-(\d+)$")
TOKEN_RE = re.compile(r"^(github_pat_|ghp_)[A-Za-z0-9_]{20,255}$")


def current_build():
    """(build number, commit) baked in by the release build, or (None, None) from source."""
    try:
        from suite import _build
        return int(_build.BUILD), str(_build.COMMIT)
    except Exception:
        return None, None


def app_folder():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "iRacingSuite")
    os.makedirs(folder, exist_ok=True)
    return folder


# ---------------------------------------------------------------- key storage
def _dpapi(data, protect):
    """Windows DPAPI for the current user. Returns None where unavailable."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    buf = ctypes.create_string_buffer(data, len(data))
    src = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    fn = crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out))   # UI_FORBIDDEN
    if not ok:
        raise OSError("DPAPI failed (%d)" % ctypes.get_last_error())
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        k32.LocalFree(out.pbData)


class KeyStore:
    def __init__(self, path=None):
        self.path = path or os.path.join(app_folder(), "update.json")

    def save(self, token):
        raw = token.encode("utf-8")
        enc = _dpapi(raw, True)
        rec = {"dpapi": base64.b64encode(enc).decode()} if enc is not None else {"plain": token}
        tmp = self.path + ".part"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f)
        os.replace(tmp, self.path)

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return None
        try:
            if "dpapi" in rec:
                dec = _dpapi(base64.b64decode(rec["dpapi"]), False)
                return dec.decode("utf-8") if dec else None
            return rec.get("plain") or None
        except Exception:
            return None

    def clear(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------- GitHub
class UpdateError(Exception):
    pass


def _api_get(path, token, opener=None, accept="application/vnd.github+json", timeout=20):
    """A GitHub API read. The repo is public: no key is needed. An old key (kept from
    when it was private) is sent if there is one, and if GitHub refuses it -- expired,
    or made for the old private repo -- the same request goes again without it."""
    req = urllib.request.Request(API + path, headers={"Accept": accept, "User-Agent": "iRacingSuite-updater",
                                                      "X-GitHub-Api-Version": "2022-11-28"})
    # An unredirected header is not copied onto a redirect, so the key never
    # reaches the storage host a download is redirected to.
    if token:
        req.add_unredirected_header("Authorization", "Bearer " + token)
    try:
        return (opener or urllib.request.urlopen)(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if token and e.code in (401, 403, 404):
            return _api_get(path, None, opener, accept, timeout)
        if e.code == 404:
            raise UpdateError("No builds are published yet.")
        if e.code == 403:
            raise UpdateError("GitHub is limiting requests from this connection for now. Try again in an hour.")
        raise UpdateError("GitHub answered %d %s." % (e.code, e.reason))
    except urllib.error.URLError as e:
        raise UpdateError("Could not reach GitHub (%s). Check the internet connection." % e.reason)


def parse_release(rel):
    m = TAG_RE.match(str(rel.get("tag_name") or ""))
    if not m:
        raise UpdateError("The newest release (%r) is not one of the app's builds." % rel.get("tag_name"))
    exes = [a for a in rel.get("assets") or [] if str(a.get("name", "")).lower().endswith(".exe")]
    if not exes:
        raise UpdateError("The newest release has no .exe attached yet. Try again in a few minutes.")
    a = exes[0]
    digest = a.get("digest") or ""
    return {"build": int(m.group(1)), "tag": rel["tag_name"], "commit": rel.get("target_commitish"),
            "published": rel.get("published_at"), "size": int(a.get("size") or 0),
            "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else None,
            "asset": a.get("url"), "name": a.get("name")}


class Updater:
    """state: idle | checking | downloading | ready | applying | error"""

    def __init__(self, keys=None, opener=None, exe_path=None, build=None, commit=None, log=print):
        self.keys = keys or KeyStore()
        self._opener = opener
        frozen = getattr(sys, "frozen", False)
        self.exe = exe_path or (sys.executable if frozen else None)
        b, c = current_build()
        self.build = build if build is not None else b
        self.commit = commit if commit is not None else c
        self.log = log
        self.state = "idle"
        self.error = None
        self.latest = None
        self.changes = []
        self.progress = None
        self.checked_at = None
        self._new_path = None

    # ---- key
    def set_key(self, token):
        token = (token or "").strip()
        if not TOKEN_RE.match(token):
            raise UpdateError("That does not look like a GitHub key. It starts with github_pat_ "
                              "(or ghp_ for a classic key).")
        old = self.keys.load()
        self.keys.save(token)
        try:
            self.check()
        except UpdateError:
            # A key that does not work is not kept.
            if old:
                self.keys.save(old)
            else:
                self.keys.clear()
            raise

    def clear_key(self):
        self.keys.clear()
        self.latest, self.changes, self.state, self.error = None, [], "idle", None

    # ---- check
    def info(self):
        lt = self.latest
        # hasKey stays true: no key is needed any more (the home page asked for one when it was false)
        return {"build": self.build, "commit": (self.commit or "")[:7] or None, "hasKey": True,
                "canApply": bool(self.exe), "state": self.state, "error": self.error, "progress": self.progress,
                "checkedAt": self.checked_at,
                "latest": {"build": lt["build"], "published": lt["published"], "sizeMB": round(lt["size"] / 2**20, 1)}
                if lt else None,
                "available": bool(lt and (self.build is None or lt["build"] > self.build)),
                "changes": self.changes}

    def _token(self):
        """An old key if one is kept, else None: the public repo needs none."""
        return self.keys.load() or None

    def check(self):
        token = self._token()
        self.state, self.error = "checking", None
        try:
            with _api_get("/repos/%s/releases/latest" % REPO, token, self._opener) as r:
                rel = parse_release(json.loads(r.read()))
            self.latest = rel
            self.changes = []
            if self.commit and rel.get("commit") and rel["build"] != self.build:
                self.changes = self._changes(token, self.commit, rel["commit"])
            self.checked_at = time.time()
            self.state = "idle"
        except UpdateError as e:
            self.state, self.error = "error", str(e)
            raise
        return self.info()

    def _changes(self, token, base, head):
        """What changed between our build and the new one: commit subjects (merges left out)."""
        try:
            with _api_get("/repos/%s/compare/%s...%s" % (REPO, base, head), token, self._opener) as r:
                data = json.loads(r.read())
        except (UpdateError, ValueError):
            return []
        out = []
        for c in data.get("commits") or []:
            msg = ((c.get("commit") or {}).get("message") or "").split("\n", 1)[0].strip()
            if msg and not msg.startswith("Merge pull request") and not msg.startswith("Merge branch"):
                out.append(msg)
        return out[-20:][::-1]

    # ---- download
    def download(self):
        if not self.exe:
            raise UpdateError("Updating works in the installed app (iRacing Suite.exe), not when run from source.")
        token = self._token()
        if self.latest is None:
            self.check()
        rel = self.latest
        if self.build is not None and rel["build"] <= self.build:
            raise UpdateError("Already on the newest build.")
        folder = os.path.dirname(os.path.abspath(self.exe))
        part = os.path.join(folder, ".iRacing Suite.update.part")
        new = os.path.join(folder, ".iRacing Suite.update.exe")
        self.state, self.error, self.progress = "downloading", None, 0.0
        h = hashlib.sha256()
        try:
            with _api_get("/repos/%s/releases/assets/%s" % (REPO, rel["asset"].rsplit("/", 1)[-1]), token,
                          self._opener, accept="application/octet-stream", timeout=60) as r, \
                    open(part, "wb") as f:
                got = 0
                while True:
                    block = r.read(256 * 1024)
                    if not block:
                        break
                    f.write(block)
                    h.update(block)
                    got += len(block)
                    self.progress = round(got / rel["size"], 3) if rel["size"] else None
            if rel["size"] and got != rel["size"]:
                raise UpdateError("The download was cut short (%d of %d bytes). Try again." % (got, rel["size"]))
            if rel["sha256"] and h.hexdigest() != rel["sha256"]:
                raise UpdateError("The download does not match GitHub's fingerprint for it, so it was thrown away.")
            with open(part, "rb") as f:
                if f.read(2) != b"MZ":
                    raise UpdateError("The download was not a Windows program.")
            os.replace(part, new)
        except PermissionError:
            self.state, self.error = "error", ("Cannot write next to the app in %s. Move iRacing Suite.exe to a "
                                               "folder you own (Desktop or Documents) and try again." % folder)
            raise UpdateError(self.error)
        except UpdateError as e:
            self.state, self.error = "error", str(e)
            raise
        except OSError as e:
            self.state, self.error = "error", "Download failed: %s" % e
            raise UpdateError(self.error)
        finally:
            self.progress = None
            try:
                os.remove(part)
            except OSError:
                pass
        self._new_path = new
        self.state = "ready"
        return self.info()

    # ---- swap and restart
    def apply(self, quit_fn, popen=subprocess.Popen):
        if self.state != "ready" or not self._new_path or not os.path.isfile(self._new_path):
            raise UpdateError("Download the update first.")
        exe = os.path.abspath(self.exe)
        old = old_path(exe)
        self.state = "applying"
        try:
            if os.path.exists(old):
                _retry(os.remove, old)
            _retry(os.replace, exe, old)               # a running program can be renamed, not overwritten
            try:
                _retry(os.replace, self._new_path, exe)
            except OSError:
                _retry(os.replace, old, exe)           # put it back; nothing changed
                raise
        except OSError as e:
            self.state, self.error = "error", "Could not swap in the new version: %s" % e
            raise UpdateError(self.error)
        args = [exe, "--after-update", ",".join(str(p) for p in _our_pids())]
        kw = {"cwd": os.path.dirname(exe), "env": clean_env(), "close_fds": True}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        try:
            popen(args, **kw)
        except OSError as e:
            # The new file is in place; it starts next time the app is opened.
            self.state, self.error = "error", "Updated, but could not restart it (%s). Close and reopen the app." % e
            raise UpdateError(self.error)
        self.log("update: started build %s, quitting" % (self.latest or {}).get("build"))
        quit_fn()


def _retry(fn, *args, tries=10, wait=0.5):
    """Antivirus often holds a just-written program for a moment while it scans it."""
    for i in range(tries):
        try:
            return fn(*args)
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(wait)


def old_path(exe):
    return os.path.join(os.path.dirname(exe), ".iRacing Suite.old.exe")


def _our_pids():
    """This process, and on a one-file build its bootloader parent, which holds
    the .exe and cleans up the unpacked files when we end."""
    pids = [os.getpid()]
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        pids.append(os.getppid())
    return pids


def clean_env():
    """The environment for the new copy: without this process's unpacked-bundle
    settings, so it unpacks and runs its own files rather than ours."""
    env = dict(os.environ)
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.pop("_MEIPASS2", None)
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        for k in [k for k, v in env.items() if mei in v and k != "PATH"]:
            env.pop(k)
        env["PATH"] = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if mei not in p)
    return env


def wait_for_exit(pids, timeout=20.0):
    """--after-update: wait for the old copy to let go of its ports and files."""
    end = time.time() + timeout
    for pid in pids:
        while time.time() < end and _alive(pid):
            time.sleep(0.1)


def _alive(pid):
    if pid <= 0 or pid == os.getpid():
        return False
    if sys.platform == "win32":
        import ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x00100000, False, pid)            # SYNCHRONIZE
        if not h:
            return False
        try:
            return k32.WaitForSingleObject(h, 0) == 0x00000102  # WAIT_TIMEOUT: still running
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def tidy_after_update(exe=None):
    """Delete the previous version left beside the app. Returns True if removed."""
    exe = exe or (sys.executable if getattr(sys, "frozen", False) else None)
    if not exe:
        return False
    for _ in range(30):
        try:
            os.remove(old_path(os.path.abspath(exe)))
            return True
        except FileNotFoundError:
            return False
        except OSError:
            time.sleep(0.2)    # its old bootloader may still be finishing
    return False
