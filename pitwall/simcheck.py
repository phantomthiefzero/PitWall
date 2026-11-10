"""The camera and replay check: each command the broadcast relies on, sent to iRacing once, and what iRacing did.

The camera cuts, iRacing's own choices, pause / slow motion / play, the seek the director's replays use, and the
way back to live were written from pyirsdk's API and never run against the sim (docs/VARIABLES_TO_VERIFY.md,
"Broadcast"). This sends each one through the same CommandGate as the producer and the director and watches the
telemetry for the answer: CamCarIdx / CamGroupNumber for a cut, ReplayPlaySpeed / ReplayPlaySlowMotion for the
speed, ReplaySessionTime against SessionTime for a seek and for live. Each step is passed, failed (what was read
instead), or not sent (the error). The camera goes back where it was at the end, and live.

It runs on its own thread; the telemetry it reads is the bridge's copy from its last frame (read_vars), never the
source from another thread. It needs the broadcast on (it moves the camera) and takes the camera from the director
for as long as it runs. Every step and result goes to sim_log.txt, and the whole result to sim_check.json beside it.
"""
from __future__ import annotations

import json
import os
import threading
import time

VARS = ("CamCarIdx", "CamGroupNumber", "CamCameraNumber", "ReplayPlaySpeed", "ReplayPlaySlowMotion",
        "ReplaySessionTime", "ReplaySessionNum", "ReplayFrameNum", "ReplayFrameNumEnd", "SessionTime", "SessionNum",
        "IsReplayPlaying")
WAIT_S = 4.0          # how long a step waits for iRacing's answer
LIVE_S = 4.0          # within this of SessionTime is live (RobertsmaniaReplay's tolerance)
SEEK_BACK_S = 30.0    # the seek goes this far back


class SimCheck:
    def __init__(self, gate, src, read_vars, emit, log=print, sleep=time.sleep, clock=time.monotonic):
        self.gate, self.src, self.read, self.emit, self.log = gate, src, read_vars, emit, log
        self.sleep, self.clock = sleep, clock
        self.running = False
        self.result: dict = {"type": "simcheck", "running": False, "steps": []}
        self._stop = threading.Event()

    # ------------------------------------------------------------------ #
    def start(self, session: dict, cars_msg: dict) -> dict:
        if self.running:
            return {"ok": False, "error": "the check is already running"}
        plan = self.plan(session, cars_msg)
        if isinstance(plan, str):
            return {"ok": False, "error": plan}
        self.running = True
        self._stop.clear()
        self.result = {"type": "simcheck", "running": True, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "steps": [{"name": s["name"], "state": "waiting"} for s in plan]}
        self.emit(dict(self.result))
        threading.Thread(target=self._run, args=(plan,), daemon=True).start()
        return {"ok": True}

    def stop(self):
        self._stop.set()

    # ------------------------------------------------------------------ #
    def plan(self, session: dict, cars_msg: dict):
        """The steps, from this session's cars and camera groups; a string says why there can be no check."""
        v = self.read() or {}
        groups = [g for g in (session or {}).get("cameras") or [] if g.get("num") is not None]
        if not groups:
            return "no camera groups in this session yet: wait for iRacing to send its session info"
        drivers = {d.get("CarIdx"): d for d in (session or {}).get("drivers") or []}
        on = sorted((c for c in (cars_msg or {}).get("cars") or []
                     if c.get("surface") in ("on_track", "off_track", "in_pit_stall", "approaching_pits")
                     and (drivers.get(c["idx"]) or {}).get("CarNumber") and not (drivers.get(c["idx"]) or {}).get("CarIsPaceCar")),
                    key=lambda c: (c.get("pos") or 999, c["idx"]))
        if len(on) < 2:
            return "the check needs two cars in the world: join a session with cars on track"
        a, b = on[0], on[1]
        num = lambda c: str(drivers[c["idx"]]["CarNumber"])
        name = lambda c: "#%s %s" % (num(c), drivers[c["idx"]].get("UserName") or "")
        lower = {g["num"]: (g.get("name") or "").lower() for g in groups}
        g1 = next((n for n, x in lower.items() if "chase" in x and "far" not in x and "rear" not in x), None) or groups[0]["num"]
        g2 = next((n for n, x in lower.items() if n != g1 and ("tv" in x or "cockpit" in x)), None) or \
            next((g["num"] for g in groups if g["num"] != g1), g1)
        gname = lambda n: next((g.get("name") for g in groups if g["num"] == n), str(n))
        leader = next((c for c in on if c.get("pos") == 1), None)
        home = (v.get("CamCarIdx"), v.get("CamGroupNumber"))
        home_car = next((c for c in on if c["idx"] == home[0]), None)
        cam = lambda c, g: {"cmd": "camera", "action": "car", "car": num(c), "group": g, "camera": 0}
        steps = [
            {"name": "Cut to %s (%s)" % (name(a), gname(g1)), "cmds": [cam(a, g1)],
             "want": lambda x: x.get("CamCarIdx") == a["idx"] and x.get("CamGroupNumber") == g1},
            {"name": "Cut to %s, same camera" % name(b), "cmds": [cam(b, g1)],
             "want": lambda x: x.get("CamCarIdx") == b["idx"]},
            {"name": "Change the camera to %s" % gname(g2), "cmds": [cam(b, g2)],
             "want": lambda x: x.get("CamGroupNumber") == g2 and x.get("CamCarIdx") == b["idx"]},
        ]
        if leader is not None:
            steps.append({"name": "Let iRacing choose: the leader (%s)" % name(leader),
                          "cmds": [{"cmd": "camera", "action": "focus", "focus": "leader", "group": g1, "camera": 0}],
                          "want": lambda x: x.get("CamCarIdx") == leader["idx"]})
        steps += [
            {"name": "Pause", "cmds": [{"cmd": "replay", "action": "speed", "speed": 0}],
             "want": lambda x: x.get("ReplayPlaySpeed") == 0},
            {"name": "Slow motion", "cmds": [{"cmd": "replay", "action": "speed", "speed": 2, "slow": True}],
             "want": lambda x: bool(x.get("ReplayPlaySlowMotion"))},
            {"name": "Play", "cmds": [{"cmd": "replay", "action": "speed", "speed": 1, "slow": False}],
             "want": lambda x: x.get("ReplayPlaySpeed") == 1 and not x.get("ReplayPlaySlowMotion")},
            {"name": "Replay a moment %d s ago (the director's replays)" % SEEK_BACK_S, "seek": True,
             "want": None},
            {"name": "Back to live", "cmds": [{"cmd": "replay", "action": "search", "mode": "to_end"},
                                              {"cmd": "replay", "action": "speed", "speed": 1, "slow": False}],
             "want": lambda x: _live(x) and x.get("ReplayPlaySpeed") == 1},
        ]
        if home_car is not None and home[1] is not None:
            steps.append({"name": "Camera back where it was (%s)" % name(home_car), "cmds": [cam(home_car, home[1])],
                          "want": lambda x: x.get("CamCarIdx") == home[0] and x.get("CamGroupNumber") == home[1]})
        return steps

    # ------------------------------------------------------------------ #
    def _send(self, cmd) -> dict:
        try:
            return self.gate.execute(self.src, dict(cmd))
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    def _wait(self, want, timeout=WAIT_S):
        """Polls the bridge's copy of the telemetry until want() holds; (ok, seconds, what was read last)."""
        t0 = self.clock()
        x = self.read() or {}
        while self.clock() - t0 < timeout and not self._stop.is_set():
            x = self.read() or {}
            if want(x):
                return True, self.clock() - t0, x
            self.sleep(0.1)
        return False, self.clock() - t0, x

    def _run(self, plan):
        from . import simlog
        simlog.note("camera check: start (%d steps)" % len(plan))
        simulated = False
        try:
            for i, step in enumerate(plan):
                if self._stop.is_set():
                    self._set(i, state="stopped")
                    continue
                self._set(i, state="running")
                if step.get("seek"):
                    v = self.read() or {}
                    snum, now = v.get("SessionNum"), v.get("SessionTime")
                    if snum is None or now is None:
                        self._set(i, state="not sent", error="no session clock yet")
                        continue
                    target = max(0.0, float(now) - SEEK_BACK_S)
                    cmds = [{"cmd": "replay", "action": "jump", "session": int(snum), "timeS": round(target, 2), "preroll": 0}]
                    want = lambda x, target=target, now=now: x.get("ReplaySessionTime") is not None and \
                        abs(float(x["ReplaySessionTime"]) - (target + (float(x.get("SessionTime") or now) - float(now)))) < 3.0
                else:
                    cmds, want = step["cmds"], step["want"]
                err = None
                for c in cmds:
                    r = self._send(c)
                    simulated = simulated or bool(r.get("simulated"))
                    if not r.get("ok"):
                        err = r.get("error") or "refused"
                        break
                if err:
                    self._set(i, state="not sent", error=err)
                    simlog.note("camera check: %s: not sent: %s" % (step["name"], err))
                    continue
                ok, took, x = self._wait(want)
                seen = {k: x.get(k) for k in ("CamCarIdx", "CamGroupNumber", "ReplayPlaySpeed", "ReplayPlaySlowMotion",
                                              "ReplaySessionTime", "SessionTime", "ReplayFrameNumEnd")}
                self._set(i, state="passed" if ok else "failed", seconds=round(took, 1), read=seen)
                simlog.note("camera check: %s: %s in %.1f s %s" % (step["name"], "passed" if ok else "FAILED", took, seen))
                self.sleep(0.6)               # let the picture settle before the next
        finally:
            # whatever happened, the replay goes back to live
            if any(s.get("state") not in ("passed",) for s in self.result["steps"]):
                for c in ({"cmd": "replay", "action": "search", "mode": "to_end"},
                          {"cmd": "replay", "action": "speed", "speed": 1, "slow": False}):
                    self._send(c)
            self.running = False
            self.result = dict(self.result, running=False, simulated=simulated,
                               finished=time.strftime("%Y-%m-%d %H:%M:%S"))
            self._save()
            n = sum(1 for s in self.result["steps"] if s.get("state") == "passed")
            simlog.note("camera check: done, %d of %d passed%s" % (n, len(self.result["steps"]), " (stand-in)" if simulated else ""))
            self.log("camera check: %d of %d passed" % (n, len(self.result["steps"])))
            self.emit(dict(self.result))

    def _set(self, i, **kw):
        steps = list(self.result["steps"])
        steps[i] = dict(steps[i], **kw)
        self.result = dict(self.result, steps=steps)
        self.emit(dict(self.result))

    def _save(self):
        try:
            from . import simlog
            p = os.path.join(os.path.dirname(simlog.path()), "sim_check.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump(self.result, f, indent=1)
            self.result = dict(self.result, file=p)
        except Exception:  # noqa: BLE001
            pass


def _live(x) -> bool:
    rt, st = x.get("ReplaySessionTime"), x.get("SessionTime")
    return rt is not None and st is not None and abs(float(st) - float(rt)) < LIVE_S
