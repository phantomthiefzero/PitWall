"""Your telemetry folders: the .ibt files iRacing writes and the Garage 61 lap CSVs you
download, listed with what each one is (car, track, session, date, laps, best lap)
instead of a file name, so the Lap Analyzer can open them from a menu.

Looks in iRacing's own telemetry folder (Documents\\iRacing\\telemetry, also under
OneDrive) and in any folders you add, for example one you copy files into from your
racing rig. The list of folders is kept in %LOCALAPPDATA%\\iRacingSuite\\library.json.

Files are served to the page in pieces under the ~128 KB reply size that stalls behind
some security software (see GZIP_MIN in suite/bridge.py), and only files with these
extensions inside these folders: a path from the page is never trusted on its own.

Standard library only.
"""
import hashlib
import json
import os
import re
import struct
import threading
import time

EXTS = (".ibt", ".csv")
CHUNK = 96 * 1024            # bytes per reply
MAX_FILES = 5000             # per scan, newest first
MAX_DEPTH = 4                # folders below a chosen folder
PAGE = 400                   # files per reply of the list


def app_folder():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "iRacingSuite")
    os.makedirs(d, exist_ok=True)
    return d


def iracing_folders():
    """Where iRacing saves telemetry on this PC (only the ones that exist)."""
    home = os.path.expanduser("~")
    cands = [os.path.join(home, "Documents", "iRacing", "telemetry"),
             os.path.join(home, "OneDrive", "Documents", "iRacing", "telemetry")]
    up = os.environ.get("USERPROFILE")
    if up:
        cands.append(os.path.join(up, "Documents", "iRacing", "telemetry"))
    out = []
    for c in cands:
        r = os.path.realpath(c)
        if os.path.isdir(r) and r not in out:
            out.append(r)
    return out


# --------------------------------------------------------------------------- #
# What a file is
# --------------------------------------------------------------------------- #

def _yaml_val(y, key):
    m = re.search(r"^\s*%s:\s*([^\n]*)$" % re.escape(key), y, re.M)
    return m.group(1).strip() if m else ""


def _driver_block(y, idx=None, uid=None):
    for blk in re.split(r"\n\s*-\s*CarIdx:", y)[1:]:
        head = blk.split("\n", 1)[0].strip()
        if idx is not None and head == str(idx):
            return blk
        if uid is not None and re.search(r"\n\s*UserID:\s*%s\s*(\n|$)" % re.escape(str(uid)), blk):
            return blk
    return ""


def ibt_info(path):
    """Read the header, the session info and the last sample of an .ibt (a few KB, not
    the whole file). Raises ValueError when it is not an iRacing telemetry file."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        hdr = f.read(144)
        if len(hdr) < 144:
            raise ValueError("too short")
        ver, _st, tick, _siu, sl, so, nv, vo, _nb, bl = struct.unpack_from("<10i", hdr, 0)
        bo = struct.unpack_from("<i", hdr, 52)[0]
        start, _t0, t1, laps, recs = struct.unpack_from("<qddii", hdr, 112)
        if ver < 1 or nv <= 0 or bl <= 0 or sl <= 0 or so <= 0 or so + sl > size:
            raise ValueError("not an .ibt")
        f.seek(vo)
        vh = f.read(nv * 144)
        f.seek(so)
        y = f.read(sl).split(b"\0", 1)[0].decode("latin-1")
        rc = (size - bo) // bl
        last = b""
        if rc > 0:
            f.seek(bo + (rc - 1) * bl)
            last = f.read(bl)
    var = {}
    for i in range(nv):
        h = vh[i * 144:(i + 1) * 144]
        if len(h) < 144:
            break
        t, off, cnt = struct.unpack_from("<iii", h, 0)
        name = h[16:48].split(b"\0", 1)[0].decode("latin-1")
        var[name] = (t, off, cnt)

    def read(name):
        v = var.get(name)
        if not v or len(last) < v[1] + 8:
            return None
        t, off, _ = v
        fmt = {1: "<B", 2: "<i", 3: "<I", 4: "<f", 5: "<d"}.get(t)
        return struct.unpack_from(fmt, last, off)[0] if fmt else None

    if "Lap" not in var or "Speed" not in var:
        raise ValueError("not an .ibt")
    di = _yaml_val(y, "DriverCarIdx")
    uid = _yaml_val(y, "DriverUserID")
    car_blk = _driver_block(y, idx=di)
    me_blk = _driver_block(y, uid=uid) if uid else ""
    team = _yaml_val(y, "TeamRacing") == "1"
    who = _yaml_val(me_blk, "UserName") or ("" if team else _yaml_val(car_blk, "UserName"))
    track = _yaml_val(y, "TrackDisplayName")
    cfg = _yaml_val(y, "TrackConfigName")
    sess_names = re.findall(r"^\s*SessionType:\s*([^\n]+)$", y, re.M)
    sn = read("SessionNum")
    session = sess_names[int(sn)].strip() if sn is not None and 0 <= int(sn) < len(sess_names) else ""
    best = read("LapBestLapTime")
    tick = tick or 60
    return {
        "kind": "ibt",
        "car": _yaml_val(car_blk, "CarScreenName"),
        "carPath": _yaml_val(car_blk, "CarPath"),
        "track": track + (" · " + cfg if cfg and cfg.lower() not in track.lower() else ""),
        "event": _yaml_val(y, "EventType"),
        "session": session,
        "driver": who,
        "team": team,
        "when": start if start > 946684800 else None,        # when the recording started (seconds since 1970)
        "laps": laps if laps > 0 else None,
        "best": round(best, 3) if best and 20 < best < 3600 else None,
        "minutes": round(((recs or rc) / tick) / 60, 1),
    }


# Garage 61 names its downloads "Garage 61 - driver - car - track - 01.22.716 - id.csv"; some
# browsers and older downloads have underscores for the spaces, dots and parentheses instead.
G61_RE = re.compile(r"^Garage[ _]61[ _]-[ _](.+?)[ _]-[ _](.+?)[ _]-[ _](.+?)[ _]-[ _](\d+)[._](\d\d)[._](\d{3})[ _]-[ _]", re.I)


CACHE_V = 2      # bump when what is read from a file changes, so the cached names are read again


def g61_info(path):
    """A Garage 61 lap CSV, from the name Garage 61 gives it: driver, car, track, lap time."""
    name = os.path.basename(path)
    m = G61_RE.match(name)
    if not m:
        # Renamed: the lap time from the length. Garage 61 records 60 samples a second, one row each.
        with open(path, "rb") as f:
            head = f.readline().decode("utf-8", "replace")
            if "LapDistPct" not in head or "Speed" not in head:
                raise ValueError("not a Garage 61 telemetry CSV")
            rows = sum(chunk.count(b"\n") for chunk in iter(lambda: f.read(1 << 20), b""))
        return {"kind": "g61", "driver": "", "car": "", "track": "", "lap": round(rows / 60, 1) if rows > 600 else None, "est": True}
    nice = lambda s: re.sub(r"\s+", " ", s.replace("__", " (", 1).replace("_", " ")).strip()
    track = nice(m.group(3))
    if "(" in track and not track.endswith(")"):
        track += ")"
    return {"kind": "g61", "driver": m.group(1).replace("_", " "), "car": m.group(2).replace("_", " "),
            "track": track, "lap": int(m.group(4)) * 60 + int(m.group(5)) + int(m.group(6)) / 1000}


# --------------------------------------------------------------------------- #
# The library
# --------------------------------------------------------------------------- #

def _token(path):
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]


class Library:
    def __init__(self, folder=None, iracing=None):
        self.dir = folder or app_folder()
        self.conf_path = os.path.join(self.dir, "library.json")
        self.cache_path = os.path.join(self.dir, "library_cache.json")
        self._iracing = iracing
        self._lock = threading.Lock()
        self.custom = []
        self.cache = {}
        self._last = None
        self._ids = {}
        try:
            with open(self.conf_path, encoding="utf-8") as f:
                self.custom = [p for p in json.load(f).get("folders", []) if isinstance(p, str)]
        except Exception:
            pass
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                self.cache = json.load(f) or {}
        except Exception:
            pass

    # ---- folders
    def iracing(self):
        return self._iracing() if callable(self._iracing) else (self._iracing if self._iracing is not None else iracing_folders())

    def folders(self):
        out = [{"path": p, "auto": True, "exists": True} for p in self.iracing()]
        for p in self.custom:
            if not any(o["path"] == p for o in out):
                out.append({"path": p, "auto": False, "exists": os.path.isdir(p)})
        return out

    def add_folder(self, path):
        p = os.path.realpath(os.path.expanduser((path or "").strip().strip('"')))
        if not path or not os.path.isdir(p):
            return {"ok": False, "error": "That folder does not exist on this PC."}
        with self._lock:
            if p not in self.custom:
                self.custom.append(p)
                self._save()
        return {"ok": True, "folders": self.folders()}

    def remove_folder(self, path):
        with self._lock:
            self.custom = [p for p in self.custom if p != path]
            self._save()
        return {"ok": True, "folders": self.folders()}

    def _save(self):
        tmp = self.conf_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"folders": self.custom}, f, indent=1)
        os.replace(tmp, self.conf_path)

    def _roots(self):
        return [os.path.realpath(f["path"]) for f in self.folders() if f["exists"]]

    # ---- files
    def _sub(self, p):
        """The folder a file is in, as shown: the chosen folder's name and any folders below it."""
        for root in self._roots():
            if p.startswith(root.rstrip(os.sep) + os.sep):
                return os.path.relpath(os.path.dirname(p), os.path.dirname(root))
        return os.path.basename(os.path.dirname(p))

    def _walk(self, root):
        root_depth = root.rstrip(os.sep).count(os.sep)
        for d, subs, files in os.walk(root):
            if d.count(os.sep) - root_depth >= MAX_DEPTH:
                subs[:] = []
            subs[:] = [s for s in subs if not s.startswith(".")]
            for n in files:
                if n.lower().endswith(EXTS):
                    yield os.path.join(d, n)

    def scan(self):
        """Every telemetry file in the folders, newest first, with what it is."""
        seen, found = set(), []
        for root in self._roots():
            for p in self._walk(root):
                rp = os.path.realpath(p)
                if rp in seen:
                    continue
                seen.add(rp)
                try:
                    st = os.stat(rp)
                except OSError:
                    continue
                found.append((st.st_mtime, st.st_size, rp))
        found.sort(reverse=True)
        found = found[:MAX_FILES]
        out, changed, keep, ids = [], False, {}, {}
        for mtime, size, p in found:
            key = "%s|%d|%d|%d" % (p, size, int(mtime), CACHE_V)
            info = self.cache.get(key)
            if info is None:
                try:
                    info = ibt_info(p) if p.lower().endswith(".ibt") else g61_info(p)
                except Exception:
                    info = {"kind": "bad"}
                changed = True
            keep[key] = info
            if info.get("kind") == "bad":
                continue
            d = dict(info)
            tok = _token(p)
            ids[tok] = p
            d.update({"id": tok, "name": os.path.basename(p), "sub": self._sub(p), "size": size, "mtime": int(mtime)})
            if d["kind"] == "ibt" and not d.get("when"):
                d["when"] = int(mtime)
            out.append(d)
        self._ids = ids
        if changed or len(keep) != len(self.cache):
            self.cache = keep
            try:
                tmp = self.cache_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(keep, f)
                os.replace(tmp, self.cache_path)
            except Exception:
                pass
        return out

    def path_of(self, tok):
        """The file for an id from the page: only a file of the last listing, still a telemetry
        file inside one of the folders. A path from the page is never used."""
        if self._last is None:
            self.scan_ids()
        p = (self._ids or {}).get(tok or "")
        if not p or not p.lower().endswith(EXTS):
            return None
        rp = os.path.realpath(p)
        for root in self._roots():
            if rp.startswith(root.rstrip(os.sep) + os.sep) and os.path.isfile(rp):
                return rp
        return None

    def scan_ids(self):
        self._last = self.scan()

    def chunk(self, tok, off):
        """(total size, bytes from off) of an allowed file, at most CHUNK bytes; None when not allowed."""
        p = self.path_of(tok)
        if not p:
            return None
        size = os.path.getsize(p)
        off = max(0, min(int(off), size))
        with open(p, "rb") as f:
            f.seek(off)
            return size, f.read(CHUNK)

    def info(self, page=0):
        """One page of the list (PAGE files), so no reply passes the stall size. Page 0 scans the
        folders again; later pages come from that scan."""
        t0 = time.time()
        if page == 0 or self._last is None:
            self._last = self.scan()
        files = self._last
        return {"folders": self.folders() if page == 0 else None, "files": files[page * PAGE:(page + 1) * PAGE],
                "total": len(files), "pages": max(1, -(-len(files) // PAGE)), "chunk": CHUNK, "ms": int((time.time() - t0) * 1000)}
