"""iRacing Highlights: its own app window. Scan a race's replay for one car, pick the moments, record them with OBS,
build one shareable video (stingers, captions, music).

The page (highlights/web/index.html) calls Api through window.pywebview.api and polls status(). One job at a time
runs on a worker thread (scan, record, build); the iRacing source is only touched by that thread, or by look()
while nothing runs.

    python -m highlights            the app
    python -m highlights --demo     the fake field in place of iRacing (scan and pick only: no OBS)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import traceback

from . import __version__
from .capture import ClipRecorder
from .moments import analyze_capture, capture_field, fmt_lap
from .pick import auto_pick, clips as make_clips, length
from .scan import ScanError, camera_groups, look, scan
from .stitch import BuildError, build, probe

DEFAULTS = {
    "obsUrl": "ws://127.0.0.1:4455", "obsPassword": "", "camera": "auto", "stinger": "", "music": [],
    "musicVolume": 0.35, "gameVolume": 0.7, "targetMin": 8, "captions": True, "cards": True,
    "kinds": ["pass", "lost", "battle", "off", "spin", "towed", "drama", "pit", "fastest"],
}
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")


def home() -> str:
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "iRacingHighlights")
    os.makedirs(base, exist_ok=True)
    return base


def settings_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(root, "iRacingHighlights")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "settings.json")


def safe_name(s) -> str:
    s = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:60] or "race"


def clock_str(s) -> str:
    if not isinstance(s, (int, float)):
        return ""
    s = int(max(0, s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, sec) if h else "%d:%02d" % (m, sec)


class Engine:
    def __init__(self, demo=False, base=None):
        self.demo = demo
        self.base = base or home()
        self.settings = dict(DEFAULTS)
        self._load()
        self.lock = threading.Lock()
        self.src = None
        self.gate = None
        self.job = None              # "scan" | "record" | "build"
        self.stop_flag = threading.Event()
        self.progress = {"done": 0, "of": 0, "text": ""}
        self.logs: list[str] = []
        self.error = None
        self.looked = None
        self.result = None           # the scan's result (moments)
        self.chosen: list[int] = []
        self.target = None           # {idx, number, driver, ...}
        self.recorded: list[dict] = []
        self.video = None
        self.cameras: list[dict] = []

    # ------------------------------------------------------------------ settings, log
    def _load(self):
        try:
            with open(settings_path(), encoding="utf-8") as f:
                saved = json.load(f)
            for k in DEFAULTS:
                if k in saved:
                    self.settings[k] = saved[k]
        except (OSError, ValueError):
            pass

    def save_settings(self, s: dict):
        for k in DEFAULTS:
            if k in (s or {}):
                self.settings[k] = s[k]
        try:
            with open(settings_path(), "w", encoding="utf-8") as f:
                json.dump(self.settings, f, indent=1)
        except OSError:
            pass
        return self.settings

    def log(self, *a):
        line = " ".join(str(x) for x in a)
        self.logs.append(time.strftime("%H:%M:%S ") + line)
        del self.logs[:-200]
        print(line)

    # ------------------------------------------------------------------ iRacing
    def _source(self):
        if self.src is None:
            from pitwall.commands import CommandGate
            if self.demo:
                from pitwall.sources import FakeSource
                self.src = FakeSource(realtime=False, speed=16, incidents=True, player_pit_lap=6, parade_s=60,
                                      grid_s=20, replay_shape=True, incident_every_s=45)
            else:
                from pitwall.sources import LiveSource
                self.src = LiveSource()
            self.src.connect()
            self.gate = CommandGate(enabled=False, camera=True)
        return self.src

    def look(self):
        if self.job:
            return {"ok": False, "why": "Busy: %s" % self.job}
        try:
            with self.lock:
                src = self._source()
                r = look(src)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "why": "Cannot read iRacing: %s" % e}
        self.looked = r
        self.cameras = r.get("cameras") or []
        return r

    # ------------------------------------------------------------------ jobs
    def _run(self, name, fn):
        if self.job:
            return {"ok": False, "error": "Busy: %s" % self.job}
        self.job, self.error = name, None
        self.stop_flag.clear()
        self.progress = {"done": 0, "of": 0, "text": ""}

        def go():
            try:
                fn()
            except (ScanError, BuildError) as e:
                self.error = str(e)
                self.log("error:", e)
            except Exception as e:  # noqa: BLE001
                self.error = "%s: %s" % (type(e).__name__, e)
                self.log(traceback.format_exc())
            finally:
                self.job = None
        threading.Thread(target=go, daemon=True).start()
        return {"ok": True}

    def stop(self):
        self.stop_flag.set()
        return {"ok": True}

    def start_scan(self, idx):
        car = next((c for c in (self.looked or {}).get("field") or [] if c["idx"] == idx), None)
        if car is None:
            return {"ok": False, "error": "Pick a car first (Check iRacing)."}
        self.target = car

        def job():
            with self.lock:
                src = self._source()
                name = "%s %s #%s" % (time.strftime("%Y-%m-%d %H%M"), safe_name((self.looked or {}).get("track")),
                                      car["number"])
                os.makedirs(os.path.join(self.base, "Scans"), exist_ok=True)
                path = os.path.join(self.base, "Scans", safe_name(name) + ".jsonl.gz")
                self.progress["text"] = "Jumping to the start of the race..."

                def prog(t):
                    self.progress["text"] = "Scanning: %s of the race read" % clock_str(t)
                res = scan(src, self.gate, idx, path, stop=self.stop_flag.is_set, progress=prog, log=self.log)
            self._took(res)
            self.progress["text"] = "Scan done (%s): %d moments found" % (res.get("ended"), len(res["moments"]))
        return self._run("scan", job)

    def open_scan(self, path, idx=None):
        """A saved scan (or any suite Recording) without iRacing: the field in it, then its moments for a car."""
        info = capture_field(path)
        if idx is None:
            return {"ok": True, "field": info["field"], "races": info["races"], "path": path}
        car = next((c for c in info["field"] if c["idx"] == idx), None)
        if car is None:
            return {"ok": False, "error": "That car is not in the file."}
        self.target = car
        self.cameras = camera_groups(info["info"].get("CameraInfo"))

        def job():
            self.progress["text"] = "Reading %s..." % os.path.basename(path)
            res = analyze_capture(path, idx, progress=lambda t: self.progress.update(
                text="Reading: %s of the race" % clock_str(t)))
            res["scanFile"] = path
            self._took(res)
            self.progress["text"] = "%d moments found" % len(res["moments"])
        return self._run("scan", job)

    def _took(self, res):
        self.result = res
        self.recorded, self.video = [], None
        self.auto(self.settings.get("targetMin", 8))

    def _stinger_s(self):
        st = self.settings.get("stinger")
        if not st or not os.path.exists(st):
            return 0.0
        try:
            info = probe(st)
        except BuildError:
            return 0.0
        return 0.0 if info["alpha"] else (info["duration"] or 0.0)

    def auto(self, minutes):
        if not self.result:
            return {"ok": False}
        self.settings["targetMin"] = minutes
        cards = 2 * 4.0 if self.settings.get("cards") else 0.0
        self.chosen = auto_pick(self.result["moments"], float(minutes) * 60 - cards, self._stinger_s(),
                                kinds=set(self.settings.get("kinds") or DEFAULTS["kinds"]))
        return {"ok": True}

    def set_chosen(self, ids):
        self.chosen = sorted(int(i) for i in ids)
        return {"ok": True}

    def _obs(self):
        from pitwall.obs import OBSClient, obs_url_problem
        bad = obs_url_problem(self.settings.get("obsUrl"))
        if bad:
            raise BuildError("OBS address: %s" % bad)
        return OBSClient(self.settings.get("obsUrl"), self.settings.get("obsPassword"))

    def test_obs(self):
        try:
            c = self._obs()
            v = c.connect()
            st = c.request("GetRecordStatus")
            d = c.request("GetRecordDirectory")
            c.close()
            return {"ok": True, "text": "OBS %s connected. Recordings go to %s%s" % (
                v, d.get("recordDirectory"), " (it is recording now)" if st.get("outputActive") else "")}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "text": str(e)}

    def _video_name(self):
        r, car = self.result or {}, self.target or {}
        return safe_name("%s #%s %s" % (r.get("track") or "Race", car.get("number", ""), time.strftime("%Y-%m-%d %H%M")))

    def record_and_build(self):
        if not self.result or not self.chosen:
            return {"ok": False, "error": "Nothing picked."}
        if self.demo:
            return {"ok": False, "error": "The demo field cannot be recorded (no iRacing, no OBS)."}
        cs = make_clips(self.result["moments"], self.chosen)

        def job():
            obs = self._obs()
            obs.connect()
            with self.lock:
                src = self._source()
                rec = ClipRecorder(src, self.gate, obs, log=self.log)

                def prog(i, n):
                    self.progress.update(done=i, of=n, text="Recording clip %d of %d" % (min(i + 1, n), n))
                self.progress.update(done=0, of=len(cs), text="Recording clip 1 of %d" % len(cs))
                self.recorded = rec.record(cs, self.target["number"], self.cameras, self.settings.get("camera"),
                                           stop=self.stop_flag.is_set, progress=prog)
            obs.close()
            bad = [r for r in self.recorded if not r.get("ok")]
            if bad:
                self.log("%d clip(s) could not be recorded and are left out" % len(bad))
            if self.stop_flag.is_set():
                self.progress["text"] = "Stopped. %d clips recorded." % sum(1 for r in self.recorded if r.get("ok"))
                return
            self._build()
        return self._run("record", job)

    def rebuild(self):
        if not self.recorded:
            return {"ok": False, "error": "Record the clips first."}
        return self._run("build", self._build)

    def _build(self):
        self.job = "build"
        name = self._video_name()
        out = os.path.join(self.base, "Videos", name + ".mp4")
        work = os.path.join(self.base, "Work", name)
        r, car = self.result or {}, self.target or {}
        opts = {"stinger": self.settings.get("stinger") or None, "captions": self.settings.get("captions", True),
                "music": [m for m in self.settings.get("music") or [] if os.path.exists(m)],
                "musicVolume": float(self.settings.get("musicVolume", 0.35)),
                "gameVolume": float(self.settings.get("gameVolume", 0.7))}
        if not opts["music"]:
            opts["gameVolume"] = 1.0
        if self.settings.get("cards"):
            who = "#%s  %s" % (car.get("number", ""), car.get("team") or car.get("driver") or "")
            opts["intro"] = {"title": r.get("event") or r.get("track") or "Race highlights",
                             "lines": [x for x in [r.get("track") if r.get("event") else "", who.strip(),
                                                   car.get("cls") or ""] if x]}
            fin = "Finished P%s in class" % r["classPos"] if r.get("classPos") else "Finished"
            lines = []
            if r.get("laps"):
                lines.append("%s laps" % r["laps"])
            best = next((m for m in r.get("moments") or [] if m["kind"] == "fastest"), None)
            if best and best.get("lapTime"):
                lines.append("Best lap %s" % fmt_lap(best["lapTime"]))
            opts["outro"] = {"title": fin, "lines": lines}
        good = [x for x in self.recorded if x.get("ok")]
        self.progress.update(done=0, of=len(good) + 2, text="Building the video...")

        def prog(i, n):
            self.progress.update(done=i, of=n, text="Building the video (%d of %d)" % (i, n))
        res = build(good, out, work, opts, log=self.log, progress=prog)
        self.video = res
        self.progress["text"] = "Video ready: %s (%s)" % (os.path.basename(out), clock_str(res["duration"]))
        try:
            import shutil
            shutil.rmtree(work, ignore_errors=True)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ what the page shows
    def status(self):
        r = self.result
        out = {"job": self.job, "progress": self.progress, "error": self.error, "logs": self.logs[-12:],
               "settings": self.settings, "target": self.target, "video": self.video, "demo": self.demo,
               "cameras": self.cameras, "recorded": [{"n": x["n"], "ok": x.get("ok"), "error": x.get("error"),
                                                      "caption": x.get("caption")} for x in self.recorded]}
        if r:
            green = r.get("green") or 0.0
            ms = []
            for m in r["moments"]:
                ms.append({"id": m["id"], "kind": m["kind"], "label": m["label"], "lap": m.get("lap"),
                           "score": m["score"], "mandatory": m.get("mandatory", False),
                           "at": clock_str(m["t"] - green), "len": round(sum(b - a for a, b in m["segs"]), 1)})
            st = self._stinger_s()
            cs = make_clips(r["moments"], self.chosen)
            cards = 8.0 if self.settings.get("cards") else 0.0
            out["race"] = {"track": r.get("track"), "event": r.get("event"), "classPos": r.get("classPos"),
                           "laps": r.get("laps"), "lapTime": fmt_lap(r.get("lapTime")), "scanFile": r.get("scanFile"),
                           "moments": ms, "chosen": self.chosen, "clips": len(cs),
                           "length": clock_str(length(r["moments"], self.chosen, st) + cards),
                           "raceLength": clock_str((r.get("finish") or 0) - green) if r.get("finish") else ""}
        return out


class Api:
    """What the page can call (window.pywebview.api). The engine and window stay private: pywebview walks public
    attributes."""

    def __init__(self, engine):
        self._engine = engine
        self._window = None

    def version(self):
        return __version__

    def status(self):
        return self._engine.status()

    def look(self):
        return self._engine.look()

    def start_scan(self, idx):
        return self._engine.start_scan(int(idx))

    def stop(self):
        return self._engine.stop()

    def auto_pick(self, minutes):
        return self._engine.auto(float(minutes))

    def set_chosen(self, ids):
        return self._engine.set_chosen(ids)

    def save_settings(self, s):
        return self._engine.save_settings(s)

    def test_obs(self):
        return self._engine.test_obs()

    def record_and_build(self):
        return self._engine.record_and_build()

    def rebuild(self):
        return self._engine.rebuild()

    def _dialog(self, kind, types=None, multiple=False):
        import webview
        k = getattr(webview, "FileDialog", None)
        k = getattr(k, kind) if k else getattr(webview, kind + "_DIALOG")
        kw = {"allow_multiple": multiple}
        if types:
            kw["file_types"] = types
        picked = self._window.create_file_dialog(k, **kw)
        if not picked:
            return []
        return list(picked) if isinstance(picked, (list, tuple)) else [picked]

    def open_scan(self, idx=None, path=None):
        if path is None:
            got = self._dialog("OPEN", ("Scans and recordings (*.jsonl.gz)", "All files (*.*)"))
            if not got:
                return {"ok": False}
            path = got[0]
        try:
            return self._engine.open_scan(path, None if idx is None else int(idx))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "Could not read that file: %s" % e}

    def pick_stinger(self):
        got = self._dialog("OPEN", ("Videos (*.webm;*.mov;*.mp4;*.mkv;*.avi)", "All files (*.*)"))
        if not got:
            return {"ok": False}
        try:
            info = probe(got[0])
        except BuildError as e:
            return {"ok": False, "error": str(e)}
        self._engine.save_settings({"stinger": got[0]})
        return {"ok": True, "path": got[0], "alpha": info["alpha"], "duration": info["duration"]}

    def clear_stinger(self):
        self._engine.save_settings({"stinger": ""})
        return {"ok": True}

    def stinger_info(self):
        st = self._engine.settings.get("stinger")
        if not st or not os.path.exists(st):
            return {"ok": False}
        try:
            info = probe(st)
        except BuildError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "path": st, "name": os.path.basename(st), "alpha": info["alpha"],
                "duration": info["duration"]}

    def add_music(self):
        got = self._dialog("OPEN", ("Music (*.mp3;*.wav;*.ogg;*.flac;*.m4a;*.aac)", "All files (*.*)"), multiple=True)
        music = list(self._engine.settings.get("music") or [])
        for g in got:
            if g.lower().endswith(AUDIO_EXT) and g not in music:
                music.append(g)
        self._engine.save_settings({"music": music})
        return {"ok": True, "music": music}

    def remove_music(self, i):
        music = list(self._engine.settings.get("music") or [])
        if 0 <= int(i) < len(music):
            music.pop(int(i))
        self._engine.save_settings({"music": music})
        return {"ok": True, "music": music}

    def open_folder(self, what="videos"):
        path = os.path.join(self._engine.base, "Videos")
        v = self._engine.video
        if what == "video" and v:
            path = v["path"]
        os.makedirs(os.path.join(self._engine.base, "Videos"), exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606
            else:
                import subprocess
                subprocess.Popen(["xdg-open", path])
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        return {"ok": True}


def web_page() -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, "highlights", "web", "index.html")


def main():
    ap = argparse.ArgumentParser(description="iRacing Highlights")
    ap.add_argument("--demo", action="store_true", help="the fake field in place of iRacing")
    a = ap.parse_args()
    engine = Engine(demo=a.demo)
    import webview
    api = Api(engine)
    window = webview.create_window("iRacing Highlights", web_page(), js_api=api, width=1180, height=900,
                                   min_size=(860, 600))
    api._window = window
    webview.start()
    os._exit(0)


if __name__ == "__main__":
    main()
