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
        if "CarPath:" not in blk:          # a results list ("ResultsFastestLap: - CarIdx: 0") is not a driver
            continue
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


CACHE_V = 3      # bump when what is read from a file changes, so the cached names are read again


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
# Your records: best laps per car and track, and the moments worth remembering
# --------------------------------------------------------------------------- #

def _norm(s):
    return re.sub(r"[^a-z0-9]+", "", str(s or "").lower())


def _person(s):
    """iRacing adds digits to repeated names ("Andrew Brooks6"); Garage 61 writes the plain name."""
    return re.sub(r"\d+$", "", _norm(s))


def records(files, me=None):
    """From the listed files (Library.scan): your best lap for each car and track, how many sessions,
    when you last drove it, and milestones in date order (first time at a track in a car, each new
    best, each whole second broken). Only your own laps: the driver who recorded most of the .ibt
    files is you, unless `me` says otherwise; team events count only when you are the driver named.
    Times are iRacing's (LapBestLapTime at the end of the file) or Garage 61's (its file name)."""
    files = [f for f in files if f.get("kind") in ("ibt", "g61") and f.get("car") and f.get("track")]
    if me is None:
        names = {}
        for f in files:
            if f["kind"] == "ibt" and f.get("driver"):
                names[_person(f["driver"])] = names.get(_person(f["driver"]), 0) + 1
        me = max(names, key=names.get) if names else None
    else:
        me = _person(me)
    laps = []
    for f in files:
        t = f.get("best") if f["kind"] == "ibt" else (None if f.get("est") else f.get("lap"))
        if not t or not (20 < t < 3600) or (me and _person(f.get("driver")) != me):
            continue
        when = f.get("when") or f.get("mtime")
        laps.append({"car": f["car"], "track": f["track"], "key": _norm(f["car"]) + "|" + _norm(f["track"].replace("·", " ")),
                     "t": round(t, 3), "when": when, "kind": f["kind"], "session": f.get("session") or "", "id": f.get("id")})
    laps.sort(key=lambda x: (x["when"] or 0, x["t"]))
    by, miles = {}, []
    for x in laps:
        r = by.get(x["key"])
        if r is None:
            r = by[x["key"]] = {"car": x["car"], "track": x["track"], "best": x["t"], "bestWhen": x["when"], "bestKind": x["kind"],
                                "sessions": 0, "first": x["when"], "last": x["when"], "history": []}
            miles.append({"when": x["when"], "kind": "first", "car": x["car"], "track": x["track"], "t": x["t"]})
        else:
            if x["t"] < r["best"] - 0.0005:
                m = {"when": x["when"], "kind": "best", "car": x["car"], "track": x["track"], "t": x["t"], "was": r["best"],
                     "by": round(r["best"] - x["t"], 3)}
                if int(x["t"]) < int(r["best"]):          # a new best that also breaks a whole second: one milestone
                    m.update(kind="barrier", under=int(r["best"]))
                miles.append(m)
                r.update(best=x["t"], bestWhen=x["when"], bestKind=x["kind"])
        r["sessions"] += 1
        r["last"] = max(r["last"] or 0, x["when"] or 0) or None
        r["history"].append([x["when"], x["t"]])
    rows = sorted(by.values(), key=lambda r: -(r["last"] or 0))
    return {"me": me, "records": rows, "milestones": miles[::-1][:40]}


# --------------------------------------------------------------------------- #
# Your last session, for the home page: laps, clean laps, best, how consistent
# --------------------------------------------------------------------------- #

def _ibt_channels(path, names):
    """Every sample of the named one-value channels of an .ibt, as arrays, plus the tick rate and the row count.
    Missing channels are left out. One pass per channel over the file's rows (struct does the walking)."""
    import array
    import mmap
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            i = lambda o: struct.unpack_from("<i", mm, o)[0]
            tick, sl, so, nv, vo, bl, bo = i(8) or 60, i(16), i(20), i(24), i(28), i(36), i(52)
            if nv <= 0 or bl <= 0 or so + sl > size:
                raise ValueError("not an .ibt")
            fmt = {1: ("?", 1, "b"), 2: ("i", 4, "i"), 3: ("I", 4, "I"), 4: ("f", 4, "f"), 5: ("d", 8, "d")}
            var = {}
            for k in range(nv):
                q = vo + k * 144
                t, off, cnt = struct.unpack_from("<iii", mm, q)
                name = bytes(mm[q + 16:q + 48]).split(b"\0")[0].decode("latin-1")
                if cnt == 1 and t in fmt and name in names:
                    var[name] = (fmt[t], off)
            rows = (size - bo) // bl
            body = memoryview(mm)[bo:bo + rows * bl]
            out = {}
            try:
                for name, ((code, w, ac), off) in var.items():
                    one = struct.Struct("<%dx%s%dx" % (off, code, bl - off - w))
                    out[name] = array.array(ac, (v for (v,) in one.iter_unpack(body)))
            finally:
                body.release()
            return out, tick, rows
        finally:
            mm.close()


SUMMARY_CHANNELS = ("Lap", "LapDistPct", "SessionTime", "LapCurrentLapTime", "LapLastLapTime", "OnPitRoad",
                    "IsOnTrack", "PlayerTrackSurface", "PlayerCarMyIncidentCount")


def session_summary(path, step=None):
    """Your laps in one .ibt, timed and judged exactly as the Lap Analyzer does it (its ibt() lap parser and
    sessionStats; CLAUDE.md "Your last session": the two must give the same numbers for the same file, and
    tests/test_analyzer.py OneLapTimerTest runs both on every test file and on the driver's own files).

    A lap is the samples on one Lap number. Its time is the first NEW LapLastLapTime after its own last sample that
    agrees with its own clock (LapCurrentLapTime) to 0.05 s: iRacing posts it a moment after the line and until then
    the variable holds the lap before's. A file that ends just after the line (a lap cut out of a session) is timed
    by the clock, line to line, the crossings placed between samples. A lap is TIMED when it was driven from the
    line to the line (d0 < 0.05, d1 > 0.95), at least 100 samples, its time within 0.25 s of the time it took, with
    no moment on pit road, not driven by someone else (IsOnTrack), and no active reset (the lap distance jumping);
    CLEAN when timed with no incident points and no moment off the track (summarize_laps adds the usual-time band
    and the 7%). Returns [{lap, t, clean, timed}] for every lap that has a time. Raises ValueError for a file it
    cannot read. `step` is no longer used (every sample is read)."""
    ch, tick, rc = _ibt_channels(path, SUMMARY_CHANNELS)
    if "Lap" not in ch or "LapLastLapTime" not in ch or "LapDistPct" not in ch:
        raise ValueError("no lap channels")
    lc, pct, st, cur, llt = ch["Lap"], ch["LapDistPct"], ch.get("SessionTime"), ch.get("LapCurrentLapTime"), ch["LapLastLapTime"]
    pitc, ontr, surf, inc = ch.get("OnPitRoad"), ch.get("IsOnTrack"), ch.get("PlayerTrackSurface"), ch.get("PlayerCarMyIncidentCount")
    out, s = [], 0
    while s < rc:
        e = s
        while e < rc and lc[e] == lc[s]:
            e += 1
        span = (st[e - 1] - st[s] + 1.0 / tick) if st is not None else (e - s) / tick
        tc = cur[e - 1] if cur is not None else span
        told = False
        if e < rc:
            was = llt[e - 1]
            for r in range(e, min(rc, e + 900)):
                v = llt[r]
                if v > 0 and v != was and abs(v - tc) < 0.05:
                    tc, told = v, True
                    break
        if e < rc and not told and s > 0 and st is not None and rc - e < 4 * tick and pct[s] < 0.05:
            def cross(r):
                a, b, ta, tb = pct[r - 1], pct[r], st[r - 1], st[r]
                sp = (1.0 - a) + b
                return ta + (tb - ta) * (1.0 - a) / sp if a >= b and sp > 0 else tb
            c = cross(e) - cross(s)
            if c > 20 and abs(c - tc) < 0.5:
                tc = c
        t = tc if e < rc else 0.0
        d0, d1 = pct[s], pct[e - 1]
        pit = pitc is not None and any(pitc[r] for r in range(s, e))
        away = False
        if ontr is not None and not pit:
            n = len(range(s, e, 10))
            n0 = sum(1 for r in range(s, e, 10) if not ontr[r])
            away = n > 0 and n0 / n > 0.2
        reset = False
        if not pit:
            pp = d0
            for r in range(s + 1, e):
                p = pct[r]
                dd = p - pp
                if not (pp > 0.9 and p < 0.1) and (dd < -0.002 or dd > 0.006):
                    reset = True
                    break
                pp = p
        off = surf is not None and not pit and any(surf[r] == 0 for r in range(s, e, 2))
        ok = (e < rc and t > 0 and abs(t - span) < 0.25 and e - s >= 100 and d0 < 0.05 and d1 > 0.95
              and not pit and not away and not reset)
        points = (inc[e - 1] - inc[s]) if inc is not None else 0
        if t and 20 < t < 3600 and (ok or e - s >= tick):
            out.append({"lap": lc[s], "t": round(t, 3), "clean": bool(ok and not points and not off), "timed": bool(ok)})
        s = e
    return out


def lap_band(times):
    """The Lap Analyzer's lapBand: the usual lap times, from the median and a robust spread (MAD).
    Returns (lo, hi); with fewer than 4 times every time is usual."""
    t = sorted(x for x in times if x and x > 0)
    n = len(t)
    if n < 4:
        return float("-inf"), float("inf")
    med = lambda a: a[len(a) // 2] if len(a) % 2 else (a[len(a) // 2 - 1] + a[len(a) // 2]) / 2
    m = med(t)
    rsd = 1.4826 * med(sorted(abs(x - m) for x in t))
    return m - max(4 * rsd, 0.05 * m), m + max(3 * rsd, 0.01 * m)


def summarize_laps(laps):
    """Best, median and spread of the clean laps, as the Lap Analyzer counts them (CLAUDE.md, "clean laps"):
    clean, a usual time (lap_band), and within 7% of the quickest of those."""
    clean = [x["t"] for x in laps if x["clean"]]
    lo, hi = lap_band(clean)
    clean = [t for t in clean if lo <= t <= hi]
    if clean:
        f = min(clean)
        clean = [t for t in clean if t <= f * 1.07]
    srt = sorted(clean)
    med = srt[len(srt) // 2] if srt else None
    sd = None
    if len(srt) >= 3:
        m = sum(srt) / len(srt)
        sd = round((sum((t - m) ** 2 for t in srt) / (len(srt) - 1)) ** 0.5, 3)
    return {"timed": sum(1 for x in laps if x.get("timed", True)), "clean": len(srt), "best": srt[0] if srt else (min((x["t"] for x in laps), default=None)),
            "median": med, "spreadS": sd, "bestIsClean": bool(srt)}


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

    def build_maps(self, log=print, files=None):
        """Track outlines for PitWall from your .ibt files (GPS), newest first, one per track,
        saved where PitWall looks for them (pitwall.trackmap). A track that already has a GPS
        outline is skipped; each file is tried once per run of the app."""
        from pitwall import trackmap
        tried = self.__dict__.setdefault("_map_tried", set())
        files = files if files is not None else (self._last if self._last is not None else self.scan())
        made, have = [], set()
        for d in files:
            if d.get("kind") != "ibt":
                continue
            p = (self._ids or {}).get(d.get("id"))
            if not p or p in tried:
                continue
            tried.add(p)
            try:
                tid, cfg, name = trackmap.ibt_track(p)
                key = (tid, cfg or "default")
                if tid is None or key in have:
                    continue
                old = trackmap.load(*key)
                if old is not None and old.source == "gps":
                    have.add(key)
                    continue
                tm = trackmap.build_from_ibt(p)
                if tm is None or (old is not None and tm.source != "gps"):
                    continue
                trackmap.save(tm)
                have.add(key)
                made.append(name or str(tid))
                log("track map for %s from %s (%s)" % (name or tid, os.path.basename(p), tm.source))
            except Exception as e:  # a damaged file must not stop the others
                log("track map from %s: %s: %s" % (os.path.basename(p), type(e).__name__, e))
        return made

    def last_session(self):
        """The newest .ibt of yours with timed laps, summarized (cached per file and size). The folders are
        looked at again each time: the listing kept from the start never had the sessions driven since."""
        files = self._last = self.scan()
        cache = self.__dict__.setdefault("_sess_cache", {})
        for d in [f for f in files if f.get("kind") == "ibt"][:6]:
            p = (self._ids or {}).get(d.get("id"))
            if not p:
                continue
            key = (p, d.get("size"), d.get("mtime"))
            if key not in cache:
                try:
                    laps = session_summary(p)
                except Exception:
                    laps = []
                cache[key] = laps
            laps = cache[key]
            if laps:
                return dict({k: d.get(k) for k in ("id", "car", "track", "when", "session", "driver", "name")},
                            **summarize_laps(laps))
        return None

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
