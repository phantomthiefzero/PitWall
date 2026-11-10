"""How did the director do? The home page's check of a recording (pitwall.director_report), on its own thread.

The recordings are the Record button's (Documents\\iRacingSuite\\Recordings) or a file picked in the app's own
file dialog. The page names one by its place in the last listing, never by a path (as the Lap Analyzer's files).
Playing a race through the director takes a minute or two for a short race, longer for a long one; the page polls
info() for how far it has got.
"""
from __future__ import annotations

import glob
import os
import threading
import time


class DirectorCheck:
    def __init__(self, folder=None, log=print):
        self.folder = folder
        self.log = log
        self._listed: list = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.state = {"state": "idle"}

    def _dir(self):
        if self.folder:
            return self.folder
        from suite.recording import recordings_dir
        return recordings_dir()

    def listing(self) -> dict:
        """The recordings, newest first: [{id, name, mb, when}]."""
        from suite.recording import name_from_capture
        files = sorted(glob.glob(os.path.join(self._dir(), "*.jsonl.gz")), key=os.path.getmtime, reverse=True)[:40]
        self._listed = files
        out = []
        for i, p in enumerate(files):
            out.append({"id": i, "name": name_from_capture(p) or os.path.basename(p), "file": os.path.basename(p),
                        "mb": round(os.path.getsize(p) / 1048576, 1), "when": time.strftime("%d %b %H:%M", time.localtime(os.path.getmtime(p)))})
        return {"ok": True, "files": out}

    def start(self, which) -> dict:
        """which: an id from the last listing, or a path the app's own file dialog returned (never the page's)."""
        if isinstance(which, int) and not isinstance(which, bool):
            if not (0 <= which < len(self._listed)):
                return {"ok": False, "error": "That recording is no longer in the list."}
            path = self._listed[which]
        else:
            return {"ok": False, "error": "Pick a recording from the list."}
        return self._begin(path)

    def _begin(self, path) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return {"ok": False, "error": "A check is already running."}
            self._stop.clear()
            self.state = {"state": "running", "file": os.path.basename(path), "atS": 0.0, "started": time.time()}
            self._thread = threading.Thread(target=self._run, args=(path,), daemon=True)
            self._thread.start()
        return {"ok": True, **self.info()}

    def stop(self) -> dict:
        self._stop.set()
        return {"ok": True}

    def info(self) -> dict:
        st = dict(self.state)
        if st.get("state") == "running":
            st["seconds"] = round(time.time() - st.get("started", time.time()))
        return st

    def _run(self, path):
        from pitwall.director_report import report
        try:
            def progress(t):
                self.state = dict(self.state, atS=round(t, 1))
            r = report(path, progress=progress, stop=self._stop.is_set)
            if self._stop.is_set():
                self.state = {"state": "idle"}
                return
            self.state = {"state": "done", "file": os.path.basename(path), "report": r}
            self.log("director check: %s: %s" % (os.path.basename(path), r["afterStart"]))
        except Exception as e:  # noqa: BLE001
            self.state = {"state": "error", "file": os.path.basename(path), "error": "%s: %s" % (type(e).__name__, e)}
