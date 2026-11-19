"""Broadcasting a replay as the race (Nov 2026: "it gets confused when I'm watching a replay to record a broadcast").

iRacing plays a replay two ways, and the stack read both as a live session:

  * A saved replay file (WeekendInfo SimMode "replay"; the Talladega capture): SessionTime runs with the tape, so the
    replay looked live. But SessionInfo's ResultsPositions are the END of the session, and normalize took them as a
    floor for the leader's laps (lap 1 of the Talladega replay read "lap 35, 15 to go"; a replay of a whole race was
    over at the green), and as practice and qualifying's best laps. The director's "back to live" was
    `replay_search to_end`: the end of the tape, the end of the race.
  * A replay of this session watched behind live (after the race, in the same sim): SessionTime is the live clock,
    so the bridge held the last live picture (hold_cars) for the whole replay, with the final results laid over it.

ReplayCast sits between the bridge and the source. While it is on:

  * the "program" is where the broadcast is on the tape: SessionTime and SessionNum read the program's time and
    session, so live_edge measures an instant replay against the program, not against the sim's live clock or the
    end of the tape;
  * ResultsPositions are hidden (they are the session's end), and SessionTimeRemain is hidden when the sim's clock
    is not the tape's;
  * "back to live" (replay search to_end, from the director or the producer) goes back to the program instead;
  * a jump of the tape that we did not ask for (the producer scrubbing in iRacing, fast-forward) moves the program
    there and the derived history starts again (update() returns "jump"); a pause holds the picture, as live.

Modes: auto (a replay file, or sitting more than BEHIND_S behind live for BEHIND_HOLD_S), on (whenever a replay
plays), off. Nothing here moves the camera: the bridge still sends every command through its CommandGate.
"""
from __future__ import annotations

import copy
import time

MODES = ("auto", "on", "off")
JUMP_S = 10.0             # the tape moving further than this between two frames (or back over 2 s) is a jump
BACK_S = 2.0
BEHIND_S = 120.0          # auto, in a live session: this far behind live ...
BEHIND_HOLD_S = 20.0      # ... for this long, and not a replay we asked for, is watching the replay
SETTLE_S = 4.0            # after going back to the program, how long the seek may take to land
LAND_S = 2.0              # within this of the program counts as landed


class ReplayCast:
    def __init__(self, src, clock=time.monotonic):
        self.src = src
        self.clock = clock
        self.mode = "auto"
        self.on = False
        self.why = None            # "file" (a saved replay), "behind" (this session's replay), "forced"
        self.prog = None           # [session, t]: where the broadcast is on the tape
        self.instant = False       # an instant replay we asked for is playing (the program waits)
        self._settle_until = None  # going back to the program: waiting for the seek to land
        self._behind_since = None
        self._results_key = None
        self._results = None
        self.view = _View(self)

    # ------------------------------------------------------------------ state
    def set_mode(self, mode) -> bool:
        if mode not in MODES:
            return False
        self.mode = mode
        return True

    def status(self) -> dict:
        return {"mode": self.mode, "on": self.on, "why": self.why, "instant": self.instant,
                "session": self.prog[0] if self.prog else None,
                "t": round(self.prog[1], 1) if self.prog else None}

    def _sim_mode(self):
        wk = self.src.session_info("WeekendInfo") or {}
        return wk.get("SimMode") if isinstance(wk, dict) else None

    def _playhead(self):
        g = self.src.get
        s, t = g("ReplaySessionNum"), g("ReplaySessionTime")
        if not isinstance(t, (int, float)):
            return None
        if not isinstance(s, int):
            s = g("SessionNum")
        return [s, float(t)]

    def _want(self, now) -> str | None:
        g = self.src.get
        if self.mode == "off" or not g("IsReplayPlaying"):
            self._behind_since = None
            return None
        if self.mode == "on":
            return "forced"
        if self._sim_mode() == "replay":
            return "file"
        t_live, t_play = g("SessionTime"), g("ReplaySessionTime")
        behind = (t_live - t_play) if isinstance(t_live, (int, float)) and isinstance(t_play, (int, float)) else None
        if self.on and self.why == "behind":
            # stays on until the tape is back at live (the sim's clock and the tape agree again)
            return "behind" if behind is None or behind > 4.0 or self.instant else None
        if behind is not None and behind > BEHIND_S and not self.instant:
            if self._behind_since is None:
                self._behind_since = now
            if now - self._behind_since >= BEHIND_HOLD_S:
                return "behind"
        else:
            self._behind_since = None
        return None

    def update(self) -> str | None:
        """Once a frame, before anything is built. Returns "start" / "stop" when the mode turns on or off and
        "jump" when the tape moved by itself: the derived history belongs to another stretch of the tape then."""
        now = self.clock()
        why = self._want(now)
        if why is None:
            if self.on:
                self.on, self.why, self.prog, self.instant, self._settle_until = False, None, None, False, None
                return "stop"
            return None
        p = self._playhead()
        if p is None:
            return None
        if not self.on:
            self.on, self.why, self.prog, self.instant, self._settle_until = True, why, p, False, None
            return "start"
        self.why = why
        if self.instant:
            return None                              # our own replay: the program waits for it
        g = self.src.get
        if self._settle_until is not None:
            if p[0] == self.prog[0] and abs(p[1] - self.prog[1]) <= LAND_S:
                self._settle_until = None            # back at the program
            elif now < self._settle_until:
                return None
            else:
                self._settle_until = None            # it never landed: the tape is where it is
                self.prog = p
                return "jump"
        if g("ReplayPlaySpeed") != 1 or g("ReplayPlaySlowMotion"):
            return None                              # paused, slow, fast or backwards: the program waits, the picture
                                                     # is held (live_edge), and where it lands is judged at 1x
        if p[0] != self.prog[0] or p[1] < self.prog[1] - BACK_S or p[1] > self.prog[1] + JUMP_S:
            self.prog = p
            return "jump"
        self.prog = [p[0], max(self.prog[1], p[1])]
        return None

    # ------------------------------------------------------------------ commands
    def command(self, cmd: dict) -> dict:
        """A replay command on its way to the gate. While on, "back to live" is back to the program, and any other
        jump or search starts an instant replay (the program waits for it)."""
        if not self.on or not isinstance(cmd, dict) or cmd.get("cmd") != "replay":
            return cmd
        action, mode = cmd.get("action"), cmd.get("mode")
        if action == "search" and mode == "to_end":
            self.instant = False
            self._settle_until = self.clock() + SETTLE_S
            return {"cmd": "replay", "action": "jump", "session": self.prog[0], "timeS": round(self.prog[1], 2),
                    "preroll": 0}
        if action == "jump" or (action == "search" and mode not in (None, "to_end")):
            self.instant = True
        return cmd

    # ------------------------------------------------------------------ the source, as the stack should see it
    def results_hidden(self, si):
        """SessionInfo without ResultsPositions (the session's end in a replay), copied once per update."""
        key = (id(si), getattr(self.src, "session_info_update", None))
        if key != self._results_key:
            out = copy.copy(si)
            sess = []
            for s in (si.get("Sessions") or []):
                s = dict(s) if isinstance(s, dict) else s
                if isinstance(s, dict):
                    s["ResultsPositions"] = None
                    s["ResultsFastestLap"] = None
                sess.append(s)
            out["Sessions"] = sess
            self._results_key, self._results = key, out
        return self._results


class _View:
    """The source as the stack reads it: the real one, with the program's clock while a replay is broadcast."""

    def __init__(self, cast: ReplayCast):
        self._cast = cast

    def __getattr__(self, name):
        return getattr(self._cast.src, name)

    @property
    def name(self):
        return self._cast.src.name

    @property
    def session_info_update(self):
        return self._cast.src.session_info_update

    def get(self, name):
        c = self._cast
        if c.on and c.prog is not None:
            if name == "SessionTime":
                return c.prog[1]
            if name == "SessionNum":
                return c.prog[0]
            if name == "SessionTimeRemain" and c.why != "file":
                return None                          # the sim's live clock, not the tape's
        return c.src.get(name)

    def session_info(self, key):
        si = self._cast.src.session_info(key)
        if self._cast.on and key == "SessionInfo" and isinstance(si, dict):
            return self._cast.results_hidden(si)
        return si
