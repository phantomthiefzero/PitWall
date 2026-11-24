"""Highlights, one of the suite's apps: scan a race's replay for one car, pick the moments, record them with OBS,
build the videos (the full video and vertical shorts, with stingers, captions, music).

The suite's window exposes Api as window.pywebview.api.highlights (suite/app.py); its page is
suite/web/highlights/index.html, which polls status(). One job at a time runs on a worker thread (scan, record,
build); the iRacing source is only touched by that thread, or by look() while nothing runs. With the suite's
--pitwall-source fake it runs on the fake field (demo: scan and pick only, no OBS).
"""
from __future__ import annotations

import json
import os
import re
import sys
import threading
import time
import traceback

from .capture import ClipRecorder
from .moments import analyze_capture, capture_field, fmt_lap
from .pick import (BEST_SPEEDS, SINGLE_SPEEDS, auto_pick, best_of, clips as make_clips, hook_first, length,
                   short_length, singles)
from .scan import ScanError, camera_groups, look, scan
from .stitch import playable, BuildError, build, probe
from . import shots as shotlib
from . import story
from .stingers import STYLES as STINGER_STYLES

DEFAULTS = {
    "obsUrl": "ws://127.0.0.1:4455", "obsPassword": "", "camera": "auto", "stinger": "builtin:chevrons", "music": [],
    "musicVolume": 0.35, "gameVolume": 0.7, "targetMin": 8, "captions": True, "cards": True,
    "kinds": ["pass", "lost", "battle", "off", "spin", "crash", "towed", "drama", "pit", "fastest",
              "pack", "multiclass", "sidebyside", "train", "charge", "restart", "pitrush", "pitlead", "out"],
    "kindsKnown": [],           # the kinds there were when the settings were saved: a newer kind starts ticked
    # vertical shorts (9:16) for YouTube Shorts, Instagram Reels, TikTok
    "makeFull": True, "makeBestOf": True, "shortSingles": 3, "shortMaxS": 30, "shortFrame": "fill",
    "shortReplay": True,
    # the story: replays from other cameras for the best moments, the cold open
    "angleMoments": 3, "anglesEach": 2, "teaser": True,
    # seconds of building per second of video, measured on this PC by each build (for the estimates)
    "buildRate": {},
}
AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac")


def home() -> str:
    """Documents\\iRacingSuite\\Highlights: Scans, Videos and the build's Work folder."""
    docs = os.path.join(os.path.expanduser("~"), "Documents")
    base = os.path.join(docs if os.path.isdir(docs) else os.path.expanduser("~"), "iRacingSuite", "Highlights")
    os.makedirs(base, exist_ok=True)
    return base


def settings_path() -> str:
    root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    d = os.path.join(root, "iRacingSuite")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "highlights.json")


WHOLE_RACE = -1             # the page's "whole race" choice in place of a car
WHOLE = {"idx": None, "number": "", "driver": "", "team": "", "cls": "", "whole": True}


def _producer_cameras(key):
    try:
        from pitwall.liveries import LiveryStore
        return ((LiveryStore().cfg.get("cameras") or {}).get(key)) or {}
    except Exception:  # noqa: BLE001
        return {}


def safe_name(s) -> str:
    s = re.sub(r"[^A-Za-z0-9 _.-]+", "", str(s or "")).strip()
    return re.sub(r"\s+", " ", s)[:100] or "race"


def clock_str(s) -> str:
    if not isinstance(s, (int, float)):
        return ""
    s = int(max(0, s))
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return "%d:%02d:%02d" % (h, m, sec) if h else "%d:%02d" % (m, sec)


class Engine:
    def __init__(self, demo=False, base=None, camera_busy=lambda: None, cam_setup=None):
        self.demo = demo
        self.base = base or home()
        # The suite's Broadcast also drives iRacing's camera and replay: while its auto-director is on, this says
        # why, and Highlights waits (two owners of one camera would fight over every cut).
        self.camera_busy = camera_busy
        # The producer's word on this track's cameras ("Your cameras", PitWall's obs.json): track key -> setup
        self.cam_setup = cam_setup or _producer_cameras
        self.job_t0 = None
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
        self.videos: list[dict] = []
        self.cameras: list[dict] = []

    # ------------------------------------------------------------------ settings, log
    def _load(self):
        try:
            with open(settings_path(), encoding="utf-8") as f:
                saved = json.load(f)
            for k in DEFAULTS:
                if k in saved:
                    self.settings[k] = saved[k]
            known = set(saved.get("kindsKnown") or ["pass", "lost", "battle", "off", "spin", "crash", "towed",
                                                     "drama", "pit", "fastest", "traffic"])
            new = [k for k in DEFAULTS["kinds"] if k not in known]
            if new and isinstance(self.settings.get("kinds"), list):
                self.settings["kinds"] = list(self.settings["kinds"]) + new
        except (OSError, ValueError):
            pass
        self.settings["kindsKnown"] = list(dict.fromkeys(DEFAULTS["kinds"] + ["traffic"]))

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
        if r.get("ok"):
            from .moments import race_length_guess
            r["raceS"] = race_length_guess((src.session_info("SessionInfo") or {}))
            r["scanS"] = story.scan_estimate(r["raceS"])
            r["scanText"] = story.minutes(r["scanS"]) if r["scanS"] else ""
        self.looked = r
        self.cameras = r.get("cameras") or []
        return r

    # ------------------------------------------------------------------ jobs
    def _run(self, name, fn):
        if self.job:
            return {"ok": False, "error": "Busy: %s" % self.job}
        self.job, self.error = name, None
        self.stop_flag.clear()
        self.progress = {"done": 0, "of": 0, "text": "", "eta": None}
        self.job_t0 = time.monotonic()

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
                if self.error:
                    # the bar and its words stop with the job (they looked like a scan still running)
                    self.progress = {"done": 0, "of": 0, "text": "Stopped: " + self.error, "eta": None}
                self.job = None
        threading.Thread(target=go, daemon=True).start()
        return {"ok": True}

    def stop(self, wait=0.0):
        """Stop the job in hand; wait: seconds to wait for it to let go (the suite quitting)."""
        self.stop_flag.set()
        end = time.monotonic() + float(wait or 0)
        while self.job and time.monotonic() < end:
            time.sleep(0.05)
        return {"ok": True}

    def _camera_free(self):
        why = None
        try:
            why = self.camera_busy()
        except Exception:  # noqa: BLE001
            pass
        return why

    def start_scan(self, idx):
        why = self._camera_free()
        if why:
            return {"ok": False, "error": why}
        if idx == WHOLE_RACE:
            car = dict(WHOLE)
        else:
            car = next((c for c in (self.looked or {}).get("field") or [] if c["idx"] == idx), None)
        if car is None:
            return {"ok": False, "error": "Pick a car first (Check iRacing)."}
        self.target = car

        def job():
            with self.lock:
                src = self._source()
                name = "%s %s %s" % (time.strftime("%Y-%m-%d %H%M"), safe_name((self.looked or {}).get("track")),
                                     "race" if car["idx"] is None else "#" + car["number"])
                os.makedirs(os.path.join(self.base, "Scans"), exist_ok=True)
                path = os.path.join(self.base, "Scans", safe_name(name) + ".jsonl.gz")
                self.progress["text"] = "Jumping to the start of the race..."

                race_s = (self.looked or {}).get("raceS")
                t_wall, t_first = time.monotonic(), [None]

                def prog(t):
                    if t_first[0] is None:
                        t_first[0] = t
                    read = t - t_first[0]
                    eta = None
                    if race_s and read > 30:
                        rate = read / max(1e-3, time.monotonic() - t_wall)      # race seconds a second
                        eta = max(0.0, (race_s + 120 - read) / max(rate, 1.0))
                    self.progress.update(text="Scanning: %s of the race read" % clock_str(read), eta=eta,
                                         done=min(read, race_s or read), of=race_s or 0)
                res = scan(src, self.gate, car["idx"], path, stop=self.stop_flag.is_set, progress=prog, log=self.log)
            self._took(res)
            self.progress["text"] = "Scan done (%s): %d moments found. Saved as Documents\\iRacingSuite\\Highlights\\" \
                "Scans\\%s.%s" % (res.get("ended"), len(res["moments"]), os.path.basename(path),
                                    " The replay is back at the start of the race." if res.get("frameMap") else "")
        return self._run("scan", job)

    def open_scan(self, path, idx=None):
        """A saved scan (or any suite Recording) without iRacing: the field in it, then its moments for a car."""
        info = capture_field(path)
        if idx is None:
            return {"ok": True, "field": info["field"], "races": info["races"], "path": path}
        car = dict(WHOLE) if idx == WHOLE_RACE else next((c for c in info["field"] if c["idx"] == idx), None)
        if car is None:
            return {"ok": False, "error": "That car is not in the file."}
        self.target = car
        self.cameras = camera_groups(info["info"].get("CameraInfo"))

        def job():
            self.progress["text"] = "Reading %s..." % os.path.basename(path)
            res = analyze_capture(path, car["idx"], progress=lambda t: self.progress.update(
                text="Reading: %s of the race" % clock_str(t)))
            res["scanFile"] = path
            self._took(res)
            self.progress["text"] = "%d moments found" % len(res["moments"])
        return self._run("scan", job)

    def _took(self, res):
        self.result = res
        self.recorded, self.video, self.videos = [], None, []
        self.auto(self.settings.get("targetMin", 8))

    def _stinger_s(self):
        st = self.settings.get("stinger")
        if not st or str(st).startswith("builtin:") or not os.path.exists(st):
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
        cards += 3.0 if self.settings.get("teaser", True) else 0.0
        kinds = set(self.settings.get("kinds") or DEFAULTS["kinds"])
        target = float(minutes) * 60

        def total(budget):
            self.chosen = auto_pick(self.result["moments"], budget, self._stinger_s(), kinds=kinds)
            full = next((o for o in self.outputs() if o["key"] == "full"), None)
            return (full["length"] + self._replay_s(full) + cards - 8.0 * bool(self.settings.get("cards"))) \
                if full else 0.0
        # the replays from other cameras count toward the length asked for: the most moments whose whole video
        # (clips, replays, cold open, cards) still fits
        lo, hi = 0.0, max(0.0, target - cards)
        if total(hi) > target + 1.0:
            for _ in range(10):
                mid = (lo + hi) / 2
                if total(mid) <= target + 1.0:
                    lo = mid
                else:
                    hi = mid
            total(lo)
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
        who = "race in review" if r.get("mode") == "race" else "#%s" % car.get("number", "")
        return safe_name("%s %s %s" % (r.get("track") or "Race", who, time.strftime("%Y-%m-%d %H%M")))

    # ------------------------------------------------------------------ what gets made
    def outputs(self) -> list[dict]:
        """The videos to make: the full video (16:9, your picks), a best-of short and one short per top moment
        (9:16, picked here from every moment, hook first)."""
        r = self.result
        if not r:
            return []
        ms, s = r["moments"], self.settings
        kinds = set(s.get("kinds") or DEFAULTS["kinds"])
        st = self._stinger_s()
        replay = bool(s.get("shortReplay", True))
        max_s = float(s.get("shortMaxS", 45))
        out = []
        if s.get("makeFull", True) and self.chosen:
            out.append({"key": "full", "name": "Highlights", "ids": list(self.chosen), "vertical": False,
                        "length": length(ms, self.chosen, st) + (8.0 if s.get("cards") else 0.0)})
        if s.get("makeBestOf", True):
            ids = best_of(ms, max_s, st, kinds, replay)
            if ids:
                out.append({"key": "best", "name": "Short - best of", "ids": ids, "vertical": True,
                            "length": short_length(hook_first(make_clips(ms, ids), replay), st, BEST_SPEEDS)})
        for k, ids in enumerate(singles(ms, int(s.get("shortSingles", 3) or 0), max_s, kinds, replay)):
            m = next(x for x in ms if x["id"] == ids[0])
            out.append({"key": "s%d" % (k + 1), "name": "Short %d - %s" % (k + 1, m["label"]), "ids": ids,
                        "vertical": True,
                        "length": short_length(hook_first(make_clips(ms, ids), replay), st, SINGLE_SPEEDS)})
        return out

    def _cams(self):
        r = self.result or {}
        info = r.get("cameraInfo") or {}
        return shotlib.Cameras(info, self.cam_setup(r.get("trackKey")) if r.get("trackKey") else {}, r.get("lapTime"))

    def _number(self, idx):
        r = self.result or {}
        n = (r.get("numbers") or {}).get(idx)
        if n is None and self.target and idx == self.target.get("idx"):
            n = self.target.get("number")
        return str(n if n is not None else idx)

    def plan(self):
        """What will be recorded (each clip with its shots, the angles) for what is ticked now; worked out again
        only when the picks or the settings change (the page asks twice a second)."""
        outs = self.outputs()
        if not self.result or not outs:
            return {"main": [], "angles": {}, "jobs": []}
        key = json.dumps([id(self.result), [o["ids"] for o in outs], self.settings], sort_keys=True, default=str)
        if getattr(self, "_plan_key", None) != key:
            self._plan = story.camera_plan(self.result, outs, self.settings, self._cams(), self._number)
            self._plan_key = key
        return self._plan

    def _replay_s(self, out) -> float:
        """Seconds the other-angle replays add to an output (a short: its hook's only)."""
        p = self.plan()
        cs = make_clips(self.result["moments"], out["ids"])
        if out["vertical"]:
            cs = cs and [max(cs, key=lambda c: c["score"])]
        return sum((j["b"] - j["a"]) / j["speed"] for c in cs for j in p["angles"].get(c["best"], []))

    def estimates(self) -> dict:
        outs = self.outputs()
        jobs = self.plan()["jobs"] if outs else []
        rec = shotlib.record_s(jobs)
        bld = story.build_estimate(outs, self.settings.get("buildRate") or {})
        return {"record": rec, "build": bld, "recordText": story.minutes(rec), "buildText": story.minutes(bld),
                "recordings": len(jobs), "angles": sum(len(v) for v in self.plan()["angles"].values()) if outs else 0}

    def record_and_build(self):
        if not self.result:
            return {"ok": False, "error": "Nothing picked."}
        if self.demo:
            return {"ok": False, "error": "The demo field cannot be recorded (no iRacing, no OBS)."}
        why = self._camera_free()
        if why:
            return {"ok": False, "error": why}
        outs = self.outputs()
        if not outs:
            return {"ok": False, "error": "Nothing to make: tick the full video or a short."}
        jobs = self.plan()["jobs"]

        def job():
            obs = self._obs()
            obs.connect()
            total = shotlib.record_s(jobs)
            with self.lock:
                src = self._source()
                rec = ClipRecorder(src, self.gate, obs, log=self.log, stop=self.stop_flag.is_set,
                                   frame_map=(self.result or {}).get("frameMap"))

                def prog(i, n):
                    left = shotlib.record_s(jobs[i:])
                    self.progress.update(done=total - left, of=total, eta=left,
                                         text="Recording %d of %d" % (min(i + 1, n), n))
                prog(0, len(jobs))
                got = rec.record(jobs, self.target["number"], self.cameras, self.settings.get("camera"),
                                 stop=self.stop_flag.is_set, progress=prog)
            obs.close()
            self.recorded = got
            bad = [r for r in got if not r.get("ok")]
            if bad:
                self.log("%d recording(s) failed and are left out" % len(bad))
            if self.stop_flag.is_set():
                self.progress["text"] = "Stopped. %d clips recorded." % sum(1 for r in got if r.get("ok"))
                return
            self._build()
        return self._run("record", job)

    def rebuild(self):
        if not self.recorded:
            return {"ok": False, "error": "Record the clips first."}
        return self._run("build", self._build)

    def _inputs(self, out) -> list[dict]:
        return story.inputs(out, self.result, self.recorded, self.settings, log=self.log)

    def _build(self):
        self.job = "build"
        name = self._video_name()
        r, car = self.result or {}, self.target or {}
        st = self.settings.get("stinger") or None
        base = {"stinger": st, "captions": self.settings.get("captions", True),
                "stingerMark": ("#%s" % (r.get("number") or car.get("number")) if (r.get("number") or car.get("number"))
                                else None) if st else None,
                "stingerSub": r.get("team") or None,
                "music": [m for m in self.settings.get("music") or [] if os.path.exists(m)],
                "musicVolume": float(self.settings.get("musicVolume", 0.35)),
                "gameVolume": float(self.settings.get("gameVolume", 0.7))}
        if not base["music"]:
            base["gameVolume"] = 1.0
        outs = [o for o in self.outputs() if self._inputs(o)] if self.recorded else []
        est = {o["key"]: story.build_estimate([o], self.settings.get("buildRate") or {}) for o in outs}
        total_est = sum(est.values()) or 1.0
        done_est = 0.0
        self.videos = []
        intro, outro = story.cards(r, car)
        for n, o in enumerate(outs):
            opts = dict(base)
            inputs = self._inputs(o)
            if o["vertical"]:
                opts.update(vertical=True, frame=self.settings.get("shortFrame", "fill"), captions=True, loop=True,
                            maxS=float(self.settings.get("shortMaxS", 30)))
            else:
                if self.settings.get("cards"):
                    opts["intro"], opts["outro"] = intro, outro
                if self.settings.get("teaser", True):
                    opts["teaser"] = story.teaser(r, self.recorded, o["ids"])
            fname = name if o["key"] == "full" else "%s - %s" % (name, safe_name(o["name"]))
            out = os.path.join(self.base, "Videos", fname + ".mp4")
            work = os.path.join(self.base, "Work", fname)
            t0 = time.monotonic()

            def prog(i, k, o=o, n=n, t0=t0, before=done_est):
                frac = i / max(1, k)
                spent = time.monotonic() - t0
                mine = est[o["key"]] if frac < 0.15 else spent / max(frac, 1e-3)
                left = max(0.0, mine - spent) + (total_est - before - est[o["key"]])
                self.progress.update(done=before + est[o["key"]] * frac, of=total_est, eta=left,
                                     text="Making %s (video %d of %d)" % (o["name"], n + 1, len(outs)))
            prog(0, 1)
            res = build(inputs, out, work, opts, log=self.log, progress=prog)
            res["name"] = o["name"]
            self.videos.append(res)
            done_est += est[o["key"]]
            # what building took here, per second of video, for the next estimate
            took = time.monotonic() - t0
            rates = dict(self.settings.get("buildRate") or {})
            kind = "tall" if o["vertical"] else "wide"
            if res.get("duration"):
                new = max(0.05, (took - story.BUILD_EACH_S) / res["duration"])
                rates[kind] = round(new if kind not in rates else 0.5 * rates[kind] + 0.5 * new, 3)
                self.save_settings({"buildRate": rates})
            try:
                import shutil
                shutil.rmtree(work, ignore_errors=True)
            except Exception:  # noqa: BLE001
                pass
        self.video = self.videos[0] if self.videos else None
        self.progress.update(eta=None, text="%d video%s ready in %s" % (
            len(self.videos), "" if len(self.videos) == 1 else "s", os.path.join(self.base, "Videos")))

    # ------------------------------------------------------------------ what the page shows
    def status(self):
        r = self.result
        prog = dict(self.progress)
        if self.job and prog.get("eta") is not None:
            prog["etaText"] = story.minutes(prog["eta"]) + " left"
        out = {"job": self.job, "progress": prog, "error": self.error, "logs": self.logs[-12:],
               "stingerStyles": [{"key": "builtin:" + k, "name": v} for k, v in STINGER_STYLES.items()],
               "settings": self.settings, "target": self.target, "video": self.video, "demo": self.demo,
               "videos": [{"name": v.get("name"), "path": v["path"], "duration": v["duration"],
                           "vertical": v.get("vertical")} for v in self.videos],
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
                           "raceLength": clock_str((r.get("finish") or 0) - green) if r.get("finish") else "",
                           "outputs": [{"key": o["key"], "name": o["name"], "vertical": o["vertical"],
                                        "length": clock_str(min(o["length"] + self._replay_s(o),
                                                                float(self.settings.get("shortMaxS", 30)))
                                                            if o["vertical"] else
                                                            o["length"] + self._replay_s(o) +
                                                            (3.0 if self.settings.get("teaser", True) else 0)),
                                        "clips": len(make_clips(r["moments"], o["ids"]))}
                                       for o in self.outputs()],
                           "mode": r.get("mode", "car"), "fieldSize": r.get("fieldSize"),
                           "classes": r.get("classes") or [],
                           "winners": ["%s#%s" % ((w["cls"] + " ") if w.get("cls") else "", w["number"])
                                       for w in r.get("winners") or []],
                           "team": r.get("team"), "number": r.get("number"), "drivers": r.get("drivers") or [],
                           "stints": len(r.get("stints") or []), "startPos": r.get("startPos"),
                           "estimates": self.estimates()}
        return out


class Api:
    """What the page can call: window.pywebview.api.highlights.<name>. The engine and the window stay private
    (pywebview walks public attributes). engine: the Engine, or a function giving it (the suite makes its window's
    Api before anything is called); window: a function giving the suite's window (it is made after this)."""

    def __init__(self, engine, window=lambda: None):
        self._get = engine if callable(engine) and not isinstance(engine, Engine) else (lambda: engine)
        self._win = window

    @property
    def _engine(self):
        return self._get()

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
        picked = self._win().create_file_dialog(k, **kw)
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
        got = self._dialog("OPEN", ("Videos and animated GIFs (*.webm;*.mov;*.gif;*.mp4;*.mkv;*.avi)", "All files (*.*)"))
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
        bad = []
        for g in got:
            if g.lower().endswith(AUDIO_EXT) and g not in music:
                if playable(g):
                    music.append(g)
                else:
                    bad.append(os.path.basename(g))
        self._engine.save_settings({"music": music})
        out = {"ok": True, "music": music}
        if bad:
            out["error"] = ("Not added: %s. It is not a sound file that can be played (it may be a web page saved "
                            "with a sound name, or a download that did not finish). Download it again." %
                            ", ".join(bad))
        return out

    def remove_music(self, i):
        music = list(self._engine.settings.get("music") or [])
        if 0 <= int(i) < len(music):
            music.pop(int(i))
        self._engine.save_settings({"music": music})
        return {"ok": True, "music": music}

    def open_folder(self, what="videos", i=0):
        path = os.path.join(self._engine.base, "Videos")
        if what == "scans":
            path = os.path.join(self._engine.base, "Scans")
            os.makedirs(path, exist_ok=True)
        vs = self._engine.videos
        if what == "video" and vs and 0 <= int(i) < len(vs):
            path = vs[int(i)]["path"]
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
