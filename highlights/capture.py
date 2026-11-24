"""Recording the chosen clips: for each, jump the replay there, point the camera at the car, record with OBS.

Per clip: pause; jump to LEAD_S before it with the camera on the car (pitwall.commands plan_jump: camera, seek,
settle, play at 1x); wait for the tape to land; cut to the car again (a cut sent during a seek can be lost); start
OBS recording a moment before the clip begins; stop it when the tape passes the clip's end; pause. The tape time
when OBS answered is kept with the file, so the video builder trims the lead-in exactly.

Only the clips are recorded (the race is never recorded whole). OBS writes the files into its own recording
folder (OBS > Settings > Output); the builder reads them from there.
"""
from __future__ import annotations

import time

from .moments import tape_time

LEAD_S = 3.0            # start playing this long before the clip (the seek and the camera settle)
EARLY_S = 0.6           # start recording this long before the clip (OBS takes a moment to start writing)
LAND_S = 3.0
LAND_WAIT_S = 10.0
STALL_S = 6.0

# Shot per kind of moment, first match in the track's camera groups wins (names as iRacing gives them).
KIND_CAMERAS = {
    "start": ["TV1", "Far Chase", "Chase"],
    "finish": ["TV1", "TV2", "Far Chase"],
    "pass": ["Far Chase", "Chase", "TV1"],
    "lost": ["Far Chase", "Chase", "TV1"],
    "traffic": ["Far Chase", "Chase", "TV1"],
    "battle": ["Far Chase", "Chase", "TV1"],
    "off": ["TV1", "TV2", "TV3", "Far Chase"],
    "spin": ["TV1", "TV2", "TV3", "Far Chase"],
    "towed": ["TV1", "TV2", "Far Chase"],
    "drama": ["TV1", "TV2", "Far Chase"],
    "pit": ["Pit Lane", "Pit Lane 2", "TV1"],
    "fastest": ["Chase", "Far Chase", "Cockpit"],
}


class CaptureError(RuntimeError):
    pass


def pick_group(groups, kind, pref="auto") -> int:
    """A camera group number for this clip. pref: "auto" (by kind) or a group name."""
    by_name = {str(g["name"]).lower(): g["num"] for g in groups or []}
    if pref and pref != "auto" and str(pref).lower() in by_name:
        return by_name[str(pref).lower()]
    for name in KIND_CAMERAS.get(kind, []) + ["TV1", "Far Chase", "Chase"]:
        if name.lower() in by_name:
            return by_name[name.lower()]
    return groups[0]["num"] if groups else 0


class ClipRecorder:
    def __init__(self, src, gate, obs, log=print, clock=time.monotonic, sleep=time.sleep, stop=None):
        self.src, self.gate, self.obs, self.log = src, gate, obs, log
        self.clock, self.sleep = clock, sleep
        self.stop = stop            # true: give up the clip in hand at once (OBS is still stopped, the tape paused)

    def _send(self, cmd):
        r = self.gate.execute(self.src, cmd)
        if not r.get("ok"):
            raise CaptureError("iRacing did not take the command: %s" % r.get("error"))

    def _tape(self):
        """Ticks the source until a new frame; returns (session, t) or None."""
        if self.src.tick():
            return tape_time(self.src.get)
        if not self.src.is_connected:
            raise CaptureError("iRacing closed.")
        return None

    def _wait(self, cond, limit, what):
        end = self.clock() + limit
        last_t, moved = None, self.clock()
        while True:
            if self.stop is not None and self.stop():
                raise CaptureError("stopped")
            p = self._tape()
            if p is not None:
                if cond(*p):
                    return p
                if p[1] != last_t:
                    last_t, moved = p[1], self.clock()
            now = self.clock()
            if limit is not None and now > end:
                raise CaptureError("the replay did not get there (%s)" % what)
            if now - moved > STALL_S:
                raise CaptureError("the replay stopped moving (%s)" % what)
            self.sleep(0.01)

    def record_one(self, clip, car_number, group) -> dict:
        s, a, b = clip["session"], clip["a"], clip["b"]
        self._send({"cmd": "replay", "action": "speed", "speed": 0})
        target = max(0.0, a - LEAD_S)

        def landed(ss, t):
            return ss == s and abs(t - target) <= LAND_S

        for attempt in (1, 2):
            self._send({"cmd": "replay", "action": "jump", "session": s, "timeS": a, "preroll": LEAD_S,
                        "car": car_number, "group": group})
            try:
                self._wait(landed, LAND_WAIT_S, "jump to %.0f s" % a)
                break
            except CaptureError:
                if attempt == 2:
                    raise
        self._send({"cmd": "camera", "action": "car", "car": car_number, "group": group, "camera": 0})
        self._wait(lambda ss, t: ss == s and t >= a - EARLY_S, (a - target) + 15, "clip start")
        st = self.obs.request("GetRecordStatus")
        if st.get("outputActive"):
            raise CaptureError("OBS is already recording: stop that recording first")
        self.obs.request("StartRecord")
        p = self._wait(lambda ss, t: True, 5, "recording start")
        rec_start = p[1]
        try:
            self._wait(lambda ss, t: ss != s or t >= b, (b - a) + 30, "clip end")
        finally:
            r = self.obs.request("StopRecord")
            self._send({"cmd": "replay", "action": "speed", "speed": 0})
        path = (r or {}).get("outputPath")
        if not path:
            raise CaptureError("OBS did not say where it saved the recording")
        return {"n": clip["n"], "path": path, "recStart": rec_start, "a": a, "b": b, "ok": True}

    def record(self, clips, car_number, groups, pref="auto", stop=None, progress=None) -> list[dict]:
        out = []
        for i, c in enumerate(clips):
            if stop is not None and stop():
                break
            g = pick_group(groups, c.get("kind"), pref)
            self.log("clip %d/%d: %s (%.0f-%.0f s, camera group %s)" % (i + 1, len(clips), c.get("caption"),
                                                                        c["a"], c["b"], g))
            try:
                res = self.record_one(c, car_number, g)
            except CaptureError as e:
                res = {"n": c["n"], "ok": False, "error": str(e)}
                self.log("clip %d failed: %s" % (i + 1, e))
            res["caption"] = c.get("caption")
            res["kind"] = c.get("kind")
            out.append(res)
            if progress is not None:
                progress(i + 1, len(clips))
        return out
