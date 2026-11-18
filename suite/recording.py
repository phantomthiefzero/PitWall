"""Record this session: the home page's Record button.

Writes the same capture as `python -m pitwall record` (every raw iRacing variable, 10 a
second, gzip'd JSON lines) into Documents\\iRacingSuite\\Recordings, on its own thread: the
sim lookup and the writing must never run on the app's event loop. Works while you drive,
spectate or watch a replay in iRacing. A capture drives the whole PitWall stack later
(`python -m pitwall stream --source replay --path <file>`), which is how a session is looked
at again, or sent in for a bug.
"""
from __future__ import annotations

import datetime as _dt
import gzip
import json
import os
import re
import threading
import time


def recordings_dir():
    base = os.path.join(os.path.expanduser("~"), "Documents")
    if not os.path.isdir(base):
        base = os.path.expanduser("~")
    path = os.path.join(base, "iRacingSuite", "Recordings")
    os.makedirs(path, exist_ok=True)
    return path


def _slug(s):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(s or "")).strip("-")[:60]


def name_from_capture(path):
    """A readable name from the capture's own session info: track and session type."""
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i > 50:
                    break
                rec = json.loads(line)
                if rec.get("type") != "session":
                    continue
                info = rec.get("info") or {}
                wk = info.get("WeekendInfo") or {}
                track = wk.get("TrackDisplayName") or wk.get("TrackName")
                kind = ""
                sess = (info.get("SessionInfo") or {}).get("Sessions") or []
                if sess:
                    kind = sess[-1].get("SessionType") or ""
                return "-".join(x for x in (_slug(track), _slug(kind)) if x)
    except Exception:
        return ""
    return ""


class SessionRecorder:
    HZ = 10.0

    def __init__(self, make_source=None, folder=None, log=print):
        self._make_source = make_source
        self._folder = folder
        self.log = log
        self._lock = threading.Lock()
        self._stop = None
        self._thread = None
        self.state = "off"            # off / waiting / recording / saving / error
        self.error = None
        self.path = None              # the file being written, then the saved file
        self.started = None
        self.ticks = 0
        self.auto = False             # started by AutoRecorder (subject to its rules), not by the button
        self.last_tick = None         # wall time of the last tick written: the sim gone shows as this standing still

    def _source(self):
        if self._make_source:
            return self._make_source()
        from pitwall.sources import LiveSource
        return LiveSource()

    def info(self):
        size = None
        if self.path:
            try:
                size = os.path.getsize(self.path)
            except OSError:
                pass
        return {"state": self.state, "error": self.error, "path": self.path,
                "name": os.path.basename(self.path) if self.path else None,
                "seconds": round(time.time() - self.started) if self.started and self.state == "recording" else None,
                "ticks": self.ticks, "bytes": size, "auto": self.auto}

    def start(self, auto=False):
        with self._lock:
            if self.state in ("waiting", "recording", "saving"):
                return self.info()
            folder = self._folder or recordings_dir()
            stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            self.path = os.path.join(folder, "session_%s.jsonl.gz" % stamp)
            self.state, self.error, self.ticks, self.started = "waiting", None, 0, None
            self.auto, self.last_tick = bool(auto), None
            self._stop = threading.Event()
            self._thread = threading.Thread(target=self._run, args=(self._stop, self.path), daemon=True)
            self._thread.start()
        return self.info()

    def stop(self, wait=5.0):
        with self._lock:
            ev, th = self._stop, self._thread
        if ev is not None:
            ev.set()
        if th is not None:
            th.join(wait)
        return self.info()

    def _run(self, stop, path):
        from pitwall.recorder import record
        try:
            src = self._source()
            while not stop.is_set():                  # wait for the sim, without giving up
                if src.connect():
                    break
                stop.wait(2.0)
            if stop.is_set():
                self.state, self.path = "off", None
                return
            self.state, self.started = "recording", time.time()

            def progress(n):
                self.ticks, self.last_tick = n, time.time()

            # record() connects again; a connected source just says yes
            record(src, path, hz=self.HZ, stop=stop, progress=progress, log=self.log)
            self.state = "saving"
            nice = name_from_capture(path)
            if nice:
                new = os.path.join(os.path.dirname(path), "%s_%s" % (nice, os.path.basename(path)[len("session_"):]))
                try:
                    os.replace(path, new)
                    path = new
                except OSError:
                    pass
            self.path, self.state = path, "saved"
            self.log("recording saved: %s (%d ticks)" % (path, self.ticks))
        except Exception as e:
            self.state, self.error = "error", "Recording stopped: %s" % e
            self.log(self.error)


# --------------------------------------------------------------------------- #
# Record automatically (the home page's "Record automatically"): the Record button was easy to forget, and every race
# recorded is one more the director can be checked against (scripts/director_scorecard.py). The recorder waits for the
# sim, records while it ticks, and when the sim has been gone GONE_S (iRacing opens a new sim for each session) the file
# is saved and the next one waits. "races" keeps a recording only when a race was run in it (a session of type Race
# going green); "all" keeps every one. Only recordings made this way are ever deleted: the oldest go when they pass
# keepGB together, never the newest. Pressing Stop while one runs saves it and records nothing more until the sim
# closes (your Stop is not overruled). Settings: %LOCALAPPDATA%\iRacingSuite\recording.json.
# --------------------------------------------------------------------------- #
AUTO_MODES = ("races", "all", "off")
AUTO_DEFAULT = {"mode": "races", "keepGB": 3.0}
GONE_S = 30.0
INDEX = ".auto_recordings.json"


def settings_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "iRacingSuite")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "recording.json")


def had_race(path) -> bool:
    """Was a race run in this capture: a tick in a session of type Race with the race going (green or later)."""
    races = set()
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("type") == "session":
                    for x in ((rec.get("info") or {}).get("SessionInfo") or {}).get("Sessions") or []:
                        if "race" in str(x.get("SessionType") or "").lower():
                            races.add(x.get("SessionNum"))
                elif rec.get("type") == "tick" and races:
                    v = rec.get("vars") or {}
                    if v.get("SessionNum") in races and (v.get("SessionState") or 0) >= 4:
                        return True
    except Exception:
        return False
    return False


def prune(folder, keep_bytes, log=print):
    """Delete the oldest recordings this app made automatically until they fit in keep_bytes (never the newest)."""
    idx = os.path.join(folder, INDEX)
    try:
        with open(idx, encoding="utf-8") as f:
            names = [n for n in json.load(f) if isinstance(n, str)]
    except (OSError, ValueError):
        names = []
    have = [n for n in names if os.path.isfile(os.path.join(folder, n))]
    size = lambda n: os.path.getsize(os.path.join(folder, n))
    total = sum(size(n) for n in have)
    gone = []
    while len(have) > 1 and total > keep_bytes:
        n = have.pop(0)
        try:
            total -= size(n)
            os.remove(os.path.join(folder, n))
            gone.append(n)
        except OSError:
            pass
    try:
        with open(idx, "w", encoding="utf-8") as f:
            json.dump(have, f)
    except OSError:
        pass
    for n in gone:
        log("auto recording removed (over the space kept for them): %s" % n)
    return gone


def remember(folder, name):
    idx = os.path.join(folder, INDEX)
    try:
        with open(idx, encoding="utf-8") as f:
            names = json.load(f)
    except (OSError, ValueError):
        names = []
    names = [n for n in names if n != name] + [name]
    try:
        with open(idx, "w", encoding="utf-8") as f:
            json.dump(names, f)
    except OSError:
        pass


class AutoRecorder:
    def __init__(self, recorder, sim_running=lambda: False, settings_file=None, folder=None, log=print, clock=time.time):
        self.rec, self.sim_running, self.log, self.clock = recorder, sim_running, log, clock
        self._file, self._folder = settings_file, folder
        self.paused = False           # the user stopped one: nothing more until the sim has closed
        self.last = None              # what happened to the last automatic recording (kept / removed / ...)
        self._stop = threading.Event()
        self._thread = None
        self.settings = self._load()

    def _load(self):
        try:
            with open(self._file or settings_path(), encoding="utf-8") as f:
                d = json.load(f)
            mode = d.get("mode") if d.get("mode") in AUTO_MODES else AUTO_DEFAULT["mode"]
            keep = float(d.get("keepGB", AUTO_DEFAULT["keepGB"]))
            return {"mode": mode, "keepGB": min(200.0, max(0.5, keep))}
        except (OSError, ValueError, TypeError):
            return dict(AUTO_DEFAULT)

    def configure(self, mode=None, keep_gb=None):
        if mode in AUTO_MODES:
            self.settings["mode"] = mode
        if keep_gb is not None:
            try:
                self.settings["keepGB"] = min(200.0, max(0.5, float(keep_gb)))
            except (TypeError, ValueError):
                pass
        try:
            with open(self._file or settings_path(), "w", encoding="utf-8") as f:
                json.dump(self.settings, f)
        except OSError:
            pass
        if self.settings["mode"] == "off" and self.rec.auto and self.rec.state == "waiting":
            self.rec.stop()           # nothing recorded yet: just stop waiting
        return self.info()

    def info(self):
        return dict(self.settings, paused=self.paused, last=self.last)

    def user_stopped(self):
        """The Stop button: save, and record nothing more until the sim has closed."""
        if self.settings["mode"] != "off":
            self.paused = True

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()

    def _run(self):
        while not self._stop.wait(2.0):
            try:
                self.step()
            except Exception as e:      # never let the loop die
                self.log("auto recording: %s" % e)

    def step(self):
        """One look (every 2 s on its own thread; called directly in the tests)."""
        rec, mode = self.rec, self.settings["mode"]
        if self.paused:
            if not self.sim_running() and rec.state not in ("waiting", "recording", "saving"):
                self.paused = False
            return
        if rec.state == "recording" and rec.auto and rec.last_tick and self.clock() - rec.last_tick > GONE_S:
            rec.stop()                  # the sim has been gone a while: this session is over
            self._after(rec.path)
            return
        if mode != "off" and rec.state in ("off", "saved", "error"):
            rec.start(auto=True)

    def _after(self, path):
        if not path or not os.path.isfile(path):
            return
        folder, name = os.path.dirname(path), os.path.basename(path)
        if self.settings["mode"] == "races" and not had_race(path):
            try:
                os.remove(path)
                self.last = {"name": name, "kept": False, "why": "no race in it"}
                self.log("auto recording removed (no race in it): %s" % name)
            except OSError:
                pass
            self.rec.path = None
            return
        remember(folder, name)
        self.last = {"name": name, "kept": True}
        prune(folder, self.settings["keepGB"] * 1024 ** 3, self.log)
