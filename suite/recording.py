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
                "ticks": self.ticks, "bytes": size}

    def start(self):
        with self._lock:
            if self.state in ("waiting", "recording", "saving"):
                return self.info()
            folder = self._folder or recordings_dir()
            stamp = _dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            self.path = os.path.join(folder, "session_%s.jsonl.gz" % stamp)
            self.state, self.error, self.ticks, self.started = "waiting", None, 0, None
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
                self.ticks = n

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
