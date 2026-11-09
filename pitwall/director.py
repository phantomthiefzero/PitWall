"""Who moves the camera, and the auto-director.

One owner at a time. Before this, the auto-director, the grid walk and the
producer's own cuts all moved iRacing's camera independently: the director cut
away seconds after the producer chose a car, and a battle the producer picked
was abandoned on the next shot. Now there is exactly one owner, and it is shown
in the producer panel:

    "you"     the producer. Every cut made by hand -- a car, a camera group,
              iRacing's own "most exciting" -- takes the camera back from
              whatever had it, and nothing moves it again until asked.
    "race"    the auto-director picks the car and the shot from the race.
    "grid"    the grid walk: down the starting grid one car at a time, in
              order, chase camera, holding each one. Starts on its own when the
              pace laps begin (can be turned off); ends at the green flag.
    "battle"  follows the cars of one battle, varying the shot between them,
              until the battle is over.

"grid" and "battle" are temporary: when they end, the camera goes back to
whoever had it before ("race" or "you"). Nothing acts while a replay is on
screen, nor while livery photos are being taken; the owner carries on at live.

"race" mode tells stories, not shots. Each candidate gets an interest score from what the race is doing:

    a fresh incident (contact, spin)          -> cut at once, from trackside
    the start                                 -> wide, the leader
    a lead change, a pass for position        -> the cars involved
    a battle: how close, closing or not, what it is for, how many cars
    a class leader, more the longer since it was last shown
    a top car stopped in its pit box

The director stays with the best story for a few shots, changing angle within it (SEGMENT_MIN_S to SEGMENT_MAX_S),
and a story it has just shown scores lower for a while (FATIGUE_S), so it neither hops between random cars every
few seconds nor sits on one battle all race. Nothing is picked for its own sake: no random car "in the field".

Every cut goes through the same command gate as a producer's.
"""
from __future__ import annotations

import math
import random
import time

# camera group names (lower case) by kind; anything not listed is "trackside",
# which is where camera packs' custom groups land.
GROUP_KIND = {
    # Far Chase is the race's main shot (the car and the road ahead of it, the cars it is racing); the tight Chase
    # is a closer look; Rear Chase looks BACK from in front of the car, so it is for what is behind it (a chaser in
    # a battle, the pack behind the leader at the start, a car on the grid from the front), never a car on its own
    # (Summit Point, Nov 2026: Rear Chase was the director's most used shot, 21 of 101 cuts).
    "chase": "chase", "far chase": "far", "rear chase": "rear",
    "chopper": "aerial", "blimp": "aerial", "gyro": "gyro",
    "cockpit": "onboard", "roll bar": "onboard", "nose": "onboard",
    # close-ups on a wheel or the gearbox: a moment's detail, not a shot to sit on (Summit Point, Nov 2026: the
    # director spent battles on LR Susp and Gearbox)
    "gearbox": "detail", "lf susp": "detail", "rf susp": "detail", "lr susp": "detail", "rr susp": "detail",
    "pit lane": "pit", "pit lane 2": "pit", "scenic": "never",
}

STYLES = {
    # weights per kind
    "cinematic": {"far": 5, "trackside": 4, "chase": 2, "gyro": 2, "aerial": 2, "onboard": 2},
    "tv": {"trackside": 6, "far": 4, "chase": 1, "aerial": 1},
    "onboard": {"onboard": 5, "far": 3, "chase": 1, "trackside": 1},
}

# A battle, shot by shot: (kind, which car). "far" on the LAST car shows the whole fight ahead of it (the main
# shot, twice a round); "rear" on the car in FRONT looks back and shows the chaser behind (once); "chase" on the
# LAST car is the closer look.
BATTLE_SHOTS = [("far", "back"), ("trackside", "back"), ("rear", "front"), ("far", "back"),
                ("aerial", "front"), ("chase", "back"), ("onboard", "back")]

# The grid walk, car by car: the pole and each class's pole from in front (rear chase: the car coming at the
# camera, livery and driver), held longer; the rest take turns between that and the chase, so the walk does not
# look the same 30 times over.
GRID_SHOTS = ["rear chase", "far chase"]
GRID_POLE_HOLD = 1.6         # a pole sitter's hold, times the walk's
# The walk fits the pace laps: while they run, the time until the leader reaches the line (its pace over the last
# few seconds, the distance to the next crossing; one that is just ahead is the line the field rolls over on its
# way, so a lap more) is the budget. Too little for every car at its hold: shorter holds, down to a row in
# GRID_ROW_MIN_S; too little for that: the front rows and each class's pole row, the rest left out. iRacing gives
# no count of pace laps, so a walk that ends early hands back to the director (better than cut off by the green).
GRID_ROW_MIN_S = 5.0
GRID_SAFETY = 0.85
GRID_FRONT_ROWS = 3
GRID_JUST_AHEAD = 0.15       # a line closer than this (in laps) at the start is the one the field rolls over
# The grid board (the overlay's gboard): the whole grid on one screen, every car's picture in its slot, a light
# running down it from pole while the camera stays wide. It goes first when the camera walk will not fit: always
# at a standing start (no pace laps: the cars are still arriving on the grid), and on the pace laps when every car
# even at the shortest holds would not fit. Then the walk carries on a row at a time with the row cards (the front first).
# Its length: GRID_BOARD_BASE_S plus GRID_BOARD_ROW_S a row, within GRID_BOARD_MIN_S..GRID_BOARD_MAX_S; the first
# GRID_BOARD_IN_S is the board coming in, then the light goes down a row at a time.
GRID_BOARD_BASE_S = 4.0
GRID_BOARD_ROW_S = 1.2
GRID_BOARD_MIN_S = 8.0
GRID_BOARD_MAX_S = 18.0
GRID_BOARD_IN_S = 1.5
GRID_PLAN_WAIT_S = 3.0       # on the pace laps, how long the walk waits for the pace before deciding
GRID_WIDE = ("aerial", "trackside", "far", "chase")    # the board's shot (and a car not on the grid yet): wide, over the grid

# Shots in a story come in an order, not by chance: wide to set the scene, then closer, then inside the car (each
# style its own order; a story can bring its own: traffic is best from behind the leader, a fresh fastest lap from
# close). Kinds the track has no camera for are skipped.
SEQUENCES = {
    "cinematic": ["far", "trackside", "chase", "aerial", "far", "gyro", "trackside", "onboard"],
    "tv": ["far", "trackside", "trackside", "far", "aerial", "trackside"],
    "onboard": ["far", "onboard", "chase", "onboard", "trackside", "onboard"],
}
STORY_SEQ = {
    "traffic": ["far", "aerial", "chase", "far"],
    "fastlap": ["far", "trackside", "chase"],
    "finish": ["trackside", "far", "aerial", "trackside"],
    "charge": ["far", "trackside", "onboard", "far"],
    # the first lap: the front of the field from above and from the side, the pack behind the leader, the pack ahead
    "start": ["aerial", "trackside", "rear", "aerial", "far", "trackside"],
}
MOVE_GAP_S = 0.3            # a battle this close and closing: the move may come, so inside the chasing car
HOLD_CLOSE_S = 0.25         # side by side: the shot is held (2 s at a time, HOLD_MAX_S at most) instead of cut away
HOLD_MAX_S = 8.0

# Replays: in a quiet stretch (or under a caution) the best recent moment again, from another angle, with the
# stinger in and out (the overlay runs it from the director's status; the camera moves at its cut point).
REPLAY_AFTER_S = 8.0        # a moment this fresh is still live news, not a replay
REPLAY_WINDOW_S = 150.0     # and one older than this is too old
REPLAY_GAP_S = 120.0        # at least this between two replays (a 12-minute sprint at Summit Point got seven at 75)
REPLAY_MIN = 50.0           # a moment worth less than this (after its age) is not replayed
REPLAY_SAME_S = 15.0        # a pass and the lead change it made are one moment: never both
REPLAY_CALM = 62.0          # only when nothing live scores above this
REPLAY_PREROLL_S = 3.0

FRESH_INCIDENT_S = 20.0     # an incident older than this is not news
FRESH_MOMENT_S = 12.0       # a pass or a lead change older than this is not worth cutting to
SEGMENT_MIN_S = 14.0        # a story gets at least this long (two or three shots) unless something bigger happens
SEGMENT_MAX_S = 40.0        # and at most this, then another story gets the air
FATIGUE_S = 150.0           # a story just shown scores lower for this long
INTERRUPT_BY = 35.0         # a new story must beat the one on air by this much to cut in before SEGMENT_MIN_S
BATTLE_LOST_S = 8.0         # a followed battle missing this long is over
# A close battle is not cut away from (Summit Point, Nov 2026: four battles left at SEGMENT_MAX_S while still within
# 0.1-0.7 s, one with the pass for the lead a second later; others for a replay or a car "closing in"). A battle
# within BATTLE_HOT_S, or closing within BATTLE_WARM_S, is "hot": it may stay up to BATTLE_HOT_MAX_S, tires the
# audience much less (BATTLE_HOT_FATIGUE), and a story must beat it by BATTLE_STAY more to take the air. A battle
# that drops out of the list for a moment (the gap opening as the move is made) is kept BATTLE_GRACE_S, and one
# whose cars swap places (a new id) is the same story.
BATTLE_HOT_S = 0.8
BATTLE_WARM_S = 1.2
BATTLE_HOT_MAX_S = 240.0
BATTLE_HOT_FATIGUE = 0.08
BATTLE_STAY = 30.0
BATTLE_GRACE_S = 6.0
# Going for it (normalize attacks: the live gap over the last seconds, not the closing rate per lap): the camera goes
# there before the move, so the pass is live, not its aftermath. Measured with scripts/director_report.py.
ATTACK_INTERRUPT_BY = 5.0    # an attack may cut into a story younger than SEGMENT_MIN_S if it beats it by this
ATTACK_ONBOARD_S = 0.5
ATTACK_TOP = 6               # an attack for a place further back is a story only inside a battle already listed       # (class seconds) an attack this close goes inside the chasing car
SEEN_LIVE_S = 6.0            # a pass counts as shown live when the camera was on either car this long before it
INCIDENT_FIGHT = 55.0        # a lone spin waits for a replay while a fight this good for a better place is on


def group_kind(name: str | None) -> str:
    n = (name or "").strip().lower()
    if n in GROUP_KIND:
        return GROUP_KIND[n]
    # camera packs name their own: "Scenic Drone Cam" is scenery, "Drone Cam" flies above
    if "scenic" in n:
        return "never"
    if any(w in n for w in ("drone", "heli", "blimp", "chopper", "aerial", "sky")):
        return "aerial"
    return "trackside"


class Director:
    def __init__(self, cut, log=print, rng=None, clock=time.monotonic, replay=None):
        self.cut = cut                    # cut(car_number: str, group: int) -> dict result
        self.replay_fn = replay           # replay(cmd: dict) -> dict result, a replay command through CommandGate
        self.replays_on = True            # the director's own replays (producer: Setup, the director)
        self.stinger_on = True            # with the stinger in and out (the producer's "Transition stinger on replays")
        self.stinger_cut_s = 0.42         # where the look's stinger covers the picture (the producer keeps it in step)
        self.stinger = None               # {"seq", "dir"}: the overlay runs a stinger when seq changes
        self.rp = None                    # the replay on air: {"phase", "at", "moment", "car", "label", "key"}
        self._replayed = set()
        self._last_replay_at = -1e9
        self._moments: dict = {}          # key -> feed event, the last few minutes of replayable moments
        self._cam_hist: list = []         # (race time, car on camera) live, the last few minutes
        self.log = log
        self.rng = rng or random.Random()
        self.clock = clock
        # The broadcast's master switch (the producer panel's "Broadcast on"). Off, which is how every
        # app start begins, nothing here moves the camera: no director, no automatic grid walk at the
        # pace laps, no followed battle, and the bridge refuses the producer's camera, replay and
        # photo commands too. Only the person running the show turns it on.
        self.live = False
        # owner: on=False is "you"; on=True with mode race / grid / battle
        self.on = False
        self.mode = "race"
        self.resume = None                # owner to return to after grid / battle: "race" or None
        self.style = "cinematic"
        self.hold_s = 8.0
        self.auto_grid = True             # walk the grid by itself when the pace laps start
        self.grid = []                    # car dicts still to visit
        self.grid_total = 0
        self.grid_hold_s = 5.0
        self.grid_by_row = True           # the walk goes a row at a time (both cars, inside then outside)
        self.grid_short = False           # the walk was shortened to fit the pace laps
        self.grid_board = "auto"          # the grid board first: auto (when the walk will not fit) / always / never
        self.grid_board_row = None        # the board on air: the row its light is on (0 while it comes in)
        self._board_rows = 1
        self._board = None                # this walk: None undecided, "on" showing, "done", "no"
        # By hand (the producer's Previous / Next / Hold, or "Walk by hand"): a held walk stays on its car until told;
        # it is never shortened. The whole order is kept, so Previous and a jump to any car work.
        self.grid_paused = False
        self.grid_manual = False
        self._grid_order = []
        self._grid_force = False
        self._pace = []                   # (t, laps) of the leader during the pace laps
        self.pace_eta = None              # seconds until the leader reaches the line, while the pace laps run
        self.on_grid_car = None           # callback(car) when the walk reaches a car
        self.battle = None                # {"cars": [idx...], "lostAt": t|None, "shot": n}
        self.battle_hold_s = 6.0
        self.note = None                  # why the owner last changed, for the panel
        self._next_at = 0.0
        self._last_group = None
        self._last_kind = None
        self._shown_incidents = set()
        self._battle_flip = False
        self._shots = 0
        self._grid_walked = set()         # session numbers already walked automatically
        self.segment = None               # the story on air: {"key", "why", "cars", "start", "shot"}
        self._aired: dict = {}            # story key -> [(start, end), ...] in the last FATIGUE_S
        self._lead_shown: dict = {}       # classId -> when its leader was last on air
        self._last_state = None
        self.current = None               # {"car", "num", "group", "groupName", "reason"}
        self.changed = True

    # ------------------------------------------------------------------ owner
    @property
    def owner(self) -> str:
        return self.mode if self.on else "you"

    def _set_owner(self, owner, note=None):
        self.rp = None                    # a replay belongs to the director that started it
        if owner in (None, "you"):
            self.on, self.mode = False, "race"
        else:
            self.on, self.mode = True, owner
        if owner != "grid":
            self.grid = []
        if owner != "battle":
            self.battle = None
        self.note = note
        self._next_at = 0.0
        self.changed = True

    def _end_temporary(self, note):
        back, self.resume = self.resume, None
        self._set_owner(back, note)

    def set_live(self, on: bool):
        """The master switch. Off stops whatever owns the camera and leaves it where it is."""
        self.live = bool(on)
        self.resume = None
        self._set_owner(None, "broadcast on" if self.live else "broadcast off")
        if not self.live:
            self.current = None
        self.changed = True

    def configure(self, on=None, style=None, hold_s=None, auto_grid=None, grid_rows=None,
                  replays=None, stinger=None, stinger_cut_ms=None, grid_board=None):
        if on and not self.live:
            on = None                                 # the director runs only with the broadcast on
        if on is not None:
            self.resume = None
            self._set_owner("race" if on else None, "director on" if on else "director off")
        if style in STYLES:
            self.style = style
        if hold_s is not None:
            self.hold_s = max(3.0, min(30.0, float(hold_s)))
        if auto_grid is not None:
            self.auto_grid = bool(auto_grid)
        if grid_rows is not None:
            self.grid_by_row = bool(grid_rows)
        if grid_board in ("auto", "always", "never"):
            self.grid_board = grid_board
        if replays is not None:
            self.replays_on = bool(replays)
        if stinger is not None:
            self.stinger_on = bool(stinger)
        if stinger_cut_ms is not None:
            self.stinger_cut_s = max(0.0, min(4.0, float(stinger_cut_ms) / 1000.0))
        self.changed = True

    def manual(self, why="you cut the camera"):
        """The producer cut by hand: they have the camera now, from anything."""
        if not self.on:
            return False
        self.resume = None
        self._set_owner(None, why)
        return True

    def start_grid_walk(self, cars: list[dict], hold_s: float | None = None, on_car=None, manual: bool = False,
                        standing: bool = False):
        if not self.live:
            return False
        self._standing = bool(standing)
        self._board = "no" if manual else None
        self._board_t0 = None
        self.grid_board_row = None
        self._walk_t0 = self.clock()
        self.grid = [c for c in cars if c.get("num") is not None]
        self.grid_total = len(self.grid)
        self._grid_order = list(self.grid)
        self.grid_short = False
        self.grid_manual = self.grid_paused = bool(manual)
        self._grid_row = None
        self._grid_force = True                       # the first car at once, held or not
        # the pole and the first car of each class in this order (the walk goes down the grid)
        seen_cls, self._grid_poles = set(), set()
        for c in self.grid:
            if c.get("classId") not in seen_cls:
                seen_cls.add(c.get("classId"))
                self._grid_poles.add(c.get("idx"))
        if hold_s is not None:
            self.grid_hold_s = max(2.0, min(20.0, float(hold_s)))
        self.on_grid_car = on_car
        if self.owner not in ("grid", "battle"):
            self.resume = "race" if self.owner == "race" else None
        self.on, self.mode = True, "grid"
        self.battle = None
        self.note = "walking the grid"
        self._next_at = 0.0
        self.changed = True
        return True

    # ------------------------------------------------------------------ the walk by hand
    def _grid_at(self) -> int:
        """Where the walk is: the index in its order of the car on camera (-1 before the first)."""
        cur = (self.current or {}).get("car") if self.owner == "grid" else None
        return next((i for i, c in enumerate(self._grid_order) if c.get("idx") == cur), -1)

    def grid_goto(self, k: int) -> bool:
        """Put the walk on the k-th car of its order at once (Previous, Next, a click on a driver)."""
        if self.owner != "grid" or not self._grid_order:
            return False
        k = max(0, min(len(self._grid_order) - 1, int(k)))
        if self._board == "on":
            self._board, self.grid_board_row = "done", None    # by hand: off the board, to that car
        self.grid = list(self._grid_order[k:])
        self._grid_row = None
        self._grid_force = True
        self._next_at = 0.0
        self.changed = True
        return True

    def grid_next(self) -> bool:
        if self.owner != "grid":
            return False
        if not self.grid:                              # past the last car: the walk is done
            self.current = None
            self._end_temporary("grid walk done")
            return True
        return self.grid_goto(self._grid_at() + 1)

    def grid_prev(self) -> bool:
        return self.grid_goto(max(0, self._grid_at() - 1)) if self.owner == "grid" else False

    def grid_jump(self, car_idx) -> bool:
        k = next((i for i, c in enumerate(self._grid_order) if c.get("idx") == car_idx), None)
        return self.grid_goto(k) if k is not None else False

    def grid_pause(self, on: bool | None = None) -> bool:
        """Hold the walk on its car (on=True), or let it carry on by itself (False); None toggles."""
        if self.owner != "grid":
            return False
        self.grid_paused = (not self.grid_paused) if on is None else bool(on)
        if not self.grid_paused:
            self._next_at = self.clock() + self.grid_hold_s
        self.changed = True
        return True

    def follow_battle(self, car_idxs: list[int]):
        cars = [int(i) for i in car_idxs if i is not None]
        if len(cars) < 2 or not self.live:
            return False
        if self.owner not in ("grid", "battle"):
            self.resume = "race" if self.owner == "race" else None
        self.on, self.mode = True, "battle"
        self.grid = []
        self.battle = {"cars": cars, "lostAt": None, "shot": 0}
        self.note = "following a battle"
        self._next_at = 0.0
        self.changed = True
        return True

    def end_battle(self):
        """Stop following a battle: the camera goes back to whoever had it before (the director, or you)."""
        if self.on and self.mode == "battle":
            self._end_temporary("stopped following the battle")
            return True
        return False

    def stop(self):
        """Stop whatever is running; the producer has the camera."""
        self.resume = None
        self._set_owner(None, "stopped")

    def status(self) -> dict:
        return {"type": "director", "live": self.live, "on": self.on, "mode": self.mode, "owner": self.owner,
                "resume": self.resume, "style": self.style, "holdS": self.hold_s,
                "autoGrid": self.auto_grid, "gridHoldS": self.grid_hold_s,
                "gridLeft": len(self.grid) if self.owner == "grid" else None,
                "gridTotal": self.grid_total if self.owner == "grid" else None,
                "gridByRow": self.grid_by_row, "gridShort": self.grid_short if self.owner == "grid" else None,
                "gridPaused": self.grid_paused if self.owner == "grid" else None,
                "gridManual": self.grid_manual if self.owner == "grid" else None,
                "gridAt": self._grid_at() if self.owner == "grid" else None,
                "gridBoard": ({"row": self.grid_board_row, "rows": self._board_rows}
                              if self.owner == "grid" and self._board == "on" else None),
                "gridBoardMode": self.grid_board,
                "replays": self.replays_on, "stinger": self.stinger,
                "replay": {"label": self.rp["label"], "car": self.rp["car"], "phase": self.rp["phase"]} if self.rp else None,
                "battle": list(self.battle["cars"]) if self.battle else None,
                "note": self.note, "current": self.current, "styles": sorted(STYLES)}

    # ------------------------------------------------------------------ shots
    def _groups(self, session) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for g in (session or {}).get("cameras") or []:
            if g.get("scenic"):
                continue
            k = group_kind(g.get("name"))
            if k == "never":
                continue
            out.setdefault(k, []).append(g)
        # no Far Chase (a camera pack without one): the Chase does its job
        if not out.get("far") and out.get("chase"):
            out["far"] = list(out["chase"])
        return out

    def _pick_group(self, groups, kinds_weights: dict) -> dict | None:
        options = []
        for kind, w in kinds_weights.items():
            for g in groups.get(kind, []):
                if g["num"] == self._last_group and sum(len(groups.get(k, [])) for k in kinds_weights) > 1:
                    continue
                # do not repeat the same KIND twice in a row either, unless it is all there is
                ww = w * (0.35 if kind == self._last_kind else 1.0)
                options.append((ww, kind, g))
        if not options:
            return None
        total = sum(o[0] for o in options)
        r = self.rng.random() * total
        for w, kind, g in options:
            r -= w
            if r <= 0:
                return {**g, "kind": kind}
        return {**options[-1][2], "kind": options[-1][1]}

    def _do_cut(self, car: dict, group: dict, reason: str):
        res = self.cut(str(car["num"]), int(group["num"]))
        ok = res.get("ok", False) if isinstance(res, dict) else bool(res)
        if ok:
            self._last_group = group["num"]
            self._last_kind = group.get("kind")
            self._shots += 1
            self.current = {"car": car.get("idx"), "num": car.get("num"), "group": group["num"],
                            "groupName": group.get("name"), "reason": reason}
            self.changed = True
        return ok

    def new_event(self):
        """Another event in the same run of the app: its moments and what was replayed belong to the last one
        (they are keyed by session number, which starts again)."""
        self._snum = object()

    # ------------------------------------------------------------------ tick
    def tick(self, session: dict | None, cars_msg: dict | None, state: dict | None,
             recent_incidents: list, busy: bool = False):
        if not self.live or not session or not cars_msg:
            return                                   # broadcast off: nothing runs, not even the grid walk
        race_state = ((state or {}).get("session") or {}).get("state")
        snum = ((state or {}).get("session") or {}).get("num")
        if snum != getattr(self, "_snum", None):
            self._snum, self._moments, self._replayed = snum, {}, set()
        self._watch_session(session, cars_msg, state, race_state)
        self._sample_pace(session, cars_msg, race_state, self.clock())
        self._keep_moments(recent_incidents, state)
        if not self.on:
            return
        now = self.clock()
        if self.rp is not None:
            self._replay_step(now, cars_msg, recent_incidents, state)
            return                                   # the director's own replay: it brings the camera back itself
        edge = ((state or {}).get("derived") or {}).get("liveEdge") or {}
        if edge.get("atLive") is False or busy:
            return                                   # a replay / a photo sweep: leave the camera alone
        if self.mode == "grid" and self._board_step(session, cars_msg, race_state, now):
            return                                   # the grid board is on air (or the walk waits for the pace)
        if self.mode == "grid" and self.grid_paused and not self._grid_force:
            return                                   # held by the producer: stays on this car
        if now < self._next_at and not self._grid_force:
            return
        self._grid_force = False
        groups = self._groups(session)
        if self.mode == "grid":
            self._grid_step(groups, now, session, cars_msg)
            return
        cars = [c for c in cars_msg.get("cars") or [] if c.get("num") is not None and c.get("surface") != "not_in_world"]
        if not cars:
            return
        by = {c["idx"]: c for c in cars}
        if self.mode == "battle":
            self._battle_step(groups, by, cars_msg, now)
            return
        self._race_step(groups, cars, by, cars_msg, state, race_state, recent_incidents, edge, now)

    def _watch_session(self, session, cars_msg, state, race_state):
        """The pace laps starting begin the grid walk; the green flag ends it."""
        was, self._last_state = self._last_state, race_state
        is_race = bool((cars_msg.get("derived") or {}).get("isRace"))
        snum = ((state or {}).get("session") or {}).get("num")
        if self.mode == "grid" and self.on and race_state == "racing" and was in ("parade_laps", "warmup", "get_in_car"):
            self._end_temporary("the green flag: grid walk over")
            return
        if (self.auto_grid and is_race and race_state == "parade_laps" and was != "parade_laps"
                and snum not in self._grid_walked and self.owner not in ("battle", "grid")):   # a walk already running (by hand) carries on
            rows = (session.get("grid") or {}).get("rows") or []
            by = {c["idx"]: c for c in cars_msg.get("cars") or []}
            order = [by[r["idx"]] for r in rows if r.get("idx") in by]
            if order:
                self._grid_walked.add(snum)
                self.start_grid_walk(order, on_car=self.on_grid_car)
                self.note = "pace laps: walking the grid"
            return
        # A standing start has no pace laps (Summit Point, Nov 2026: get in car, ten seconds of warm-up, green): the
        # field forms up on the grid during "get in car", so the walk goes then, once most of the grid is there.
        # Cars still to arrive are left to the end of the walk (_grid_step).
        standing = str(((session or {}).get("options") or {}).get("StandingStart") or "0") not in ("0", "False", "false", "")
        if (self.auto_grid and is_race and standing and race_state in ("get_in_car", "warmup")
                and snum not in self._grid_walked and self.owner not in ("battle", "grid")):
            rows = (session.get("grid") or {}).get("rows") or []
            by = {c["idx"]: c for c in cars_msg.get("cars") or []}
            order = [by[r["idx"]] for r in rows if r.get("idx") in by]
            there = [c for c in order if c.get("surface") != "not_in_world"]
            if order and len(there) >= max(2, int(0.6 * len(order))):
                self._grid_walked.add(snum)
                self.start_grid_walk(order, on_car=self.on_grid_car, standing=True)
                self.note = "standing start: walking the grid"

    def _race_step(self, groups, cars, by, cars_msg, state, race_state, recent_incidents, edge, now):
        weights = STYLES.get(self.style, STYLES["cinematic"])
        hold = self.hold_s * (0.75 + self.rng.random() * 0.5)
        leader = min((c for c in cars if c.get("pos")), key=lambda c: c["pos"], default=None)
        t_live = edge.get("tLive")
        # what the camera showed live, by the race's clock: a pass it missed is replayed first (_replay_candidate)
        if t_live is not None and self.current:
            h = self._cam_hist
            if not h or h[-1][1] != self.current.get("car") or t_live - h[-1][0] >= 1.0:
                h.append((t_live, self.current.get("car")))
            while h and t_live - h[0][0] > REPLAY_WINDOW_S + SEEN_LIVE_S:
                h.pop(0)
        # 1. a fresh incident worth showing (one from minutes ago is not news:
        #    turning the director on must not replay the whole feed)
        for inc in reversed(recent_incidents):
            if inc.get("id") in self._shown_incidents or inc.get("what") not in ("contact", "spin", "lost_speed"):
                continue
            self._shown_incidents.add(inc.get("id"))
            if inc.get("what") == "lost_speed" and inc.get("confidence") == "low":
                continue
            if t_live is not None and inc.get("t") is not None and t_live - inc["t"] > FRESH_INCIDENT_S:
                continue
            c = by.get(inc.get("idx"))
            if c and self.segment and self.segment["key"].startswith(("battle:", "attack:")) and c["idx"] in self.segment["cars"]:
                # it happened in the battle on air: the camera is already on it, so no cut away to a lone trackside
                # shot (Summit Point: three battles cut off at their climax this way); hold the shot through it
                self._next_at = max(self._next_at, now + 6.0)
                return
            if c and self._hot_on_air(cars, by, cars_msg, recent_incidents, t_live, now):
                continue                          # a close battle on air keeps it: the spin elsewhere comes as a replay
            if c and inc.get("what") != "contact" and any(
                    (st.get("fp") or 99) < (c.get("classPos") or 99) and st["score"] >= INCIDENT_FIGHT
                    for st in self._stories(cars, by, cars_msg, recent_incidents, t_live, now)):
                continue                          # a lone car, while a fight for a better place is on: a replay later
                                                  # (Summit Point: four spins took the camera through eleven passes)
            if c:
                g = self._pick_group(groups, {"trackside": 5, "far": 2, "aerial": 1})
                if g and self._do_cut(c, g, f"incident: {inc.get('what')}"):
                    self._next_at = now + max(hold, 6.0)
                    self._end_segment(now)
                    return
        # 1b. side by side: stay with it rather than cut away mid-move
        if self._hold_close(cars_msg, now):
            return
        # 2. the start: wide
        if race_state in ("parade_laps", "warmup"):
            if leader:
                g = self._pick_group(groups, {"aerial": 4, "trackside": 2, "far": 1})
                if g and self._do_cut(leader, g, "the start"):
                    self._next_at = now + hold
                    self._end_segment(now)
                    return
        # 3. a quiet stretch (or a caution): a replay of the best recent moment
        if self._maybe_replay(groups, cars, by, cars_msg, recent_incidents, t_live, race_state, now):
            return
        # 4. a story
        self._story_step(groups, cars, by, cars_msg, recent_incidents, t_live, weights, hold, now)

    # ------------------------------------------------------------------ stories
    def _end_segment(self, now):
        if self.segment:
            self._aired.setdefault(self.segment["key"], []).append((self.segment["start"], now))
        self.segment = None

    def _aired_s(self, key, now):
        spans = [(a, b) for a, b in self._aired.get(key, []) if b > now - FATIGUE_S]
        self._aired[key] = spans
        return sum(b - max(a, now - FATIGUE_S) for a, b in spans)

    def _stories(self, cars, by, cars_msg, moments, t_live, now) -> list[dict]:
        """Every story worth a camera now, with its interest score (before fatigue)."""
        out = []
        multi = len({c.get("classId") for c in cars if c.get("classPos")}) > 1
        # the first lap: the front of the field is the story (lap one's passes are part of it, not a new story each)
        if self._opening(cars, cars_msg):
            top = sorted((c for c in cars if c.get("pos")), key=lambda c: c["pos"])[:3]
            if top:
                out.append({"key": "start", "score": 88, "cars": [c["idx"] for c in top], "seq": "start",
                            "why": "the start", "max": 150.0})
        # a pass between two cars still racing each other is that battle's story, not a new one (Summit Point:
        # a story every 8 s on lap one, one per pass)
        fights = [set(b.get("cars") or []) for b in (cars_msg.get("derived") or {}).get("battles") or []]
        self._pass_in = {}
        # lead changes and passes for position, just now
        for e in moments:
            if t_live is not None and e.get("t") is not None and t_live - e["t"] > FRESH_MOMENT_S:
                continue
            if e.get("kind") == "class_lead" and e.get("idx") in by:
                out.append({"key": "lead-change:%s" % e.get("classId"), "score": 85, "why": "new leader",
                            "cars": [e["idx"]] + ([e["prevIdx"]] if e.get("prevIdx") in by else [])})
            elif e.get("kind") == "pass" and e.get("idx") in by:
                cp = e.get("classPos") or 99
                fight = next((f for f in fights if e["idx"] in f and set(e.get("passedIdx") or []) & f), None)
                if fight is not None:
                    self._pass_in[frozenset(fight)] = max(self._pass_in.get(frozenset(fight), 0), 25 if cp <= 3 else 15)
                    continue
                out.append({"key": "pass:%s:%s" % (e["idx"], cp), "score": 50 + (25 if cp <= 3 else 10 if cp <= 6 else 0),
                            "why": "a pass for %sP%s" % ("class " if multi else "", cp),
                            "cars": [x for x in (e.get("passedIdx") or [])[:1] if x in by] + [e["idx"]]})
        # battles: how close, closing or not, what they are for, how many cars
        start_cars = set(out[0]["cars"]) if out and out[0]["key"] == "start" else set()
        for b in (cars_msg.get("derived") or {}).get("battles") or []:
            cs = [i for i in b.get("cars") or [] if i in by]
            if start_cars & set(cs):
                continue                              # lap one: a fight at the front is part of the start
            if len(cs) < 2 or any(by[i].get("onPitRoad") for i in cs):
                continue
            # a gap in seconds of this class (normalize class_scale: an MX-5 is close at more seconds, a GTP at fewer)
            k = b.get("scale") or 1.0
            gap = min(b.get("gapsS") or [9.9]) / k
            closing = max([x for x in b.get("closingSPerLap") or [] if x is not None] or [0.0]) / k
            fp = b.get("forClassPos") or 99
            score = 35 + (30 if gap < 0.3 else 18 if gap < 0.6 else 6) + (12 if closing > 0.1 else 0) \
                + (30 if fp == 1 else 18 if fp <= 3 else 8 if fp <= 6 else 0) + 5 * (len(cs) - 2) \
                + self._pass_in.get(frozenset(b.get("cars") or []), 0)        # places just changed in it
            hot = gap < BATTLE_HOT_S or (gap < BATTLE_WARM_S and closing > 0.1)
            out.append({"key": "battle:%s" % b.get("id"), "score": score, "cars": cs, "fp": fp,
                        "why": "battle for %sP%s" % ("class " if multi else "", fp),
                        **({"hot": True, "max": BATTLE_HOT_MAX_S, "fatigue": BATTLE_HOT_FATIGUE} if hot else {})})
        # someone going for the car ahead right now: inside a battle already listed, that battle is the story, made
        # bigger and held; otherwise a story of its own (a car 1.2 s back with a run is not a battle yet)
        for a in (cars_msg.get("derived") or {}).get("attacks") or []:
            cs = [i for i in a.get("cars") or [] if i in by]
            if len(cs) < 2 or start_cars & set(cs) or any(by[i].get("onPitRoad") for i in cs):
                continue
            k = a.get("scale") or 1.0
            gap, gain, fp = (a.get("gapS") or 9.9) / k, (a.get("gainedS") or 0.0) / k, a.get("forClassPos") or 99
            boost = 12 + 14 * min(1.0, gain / 0.4) + (6 if gap < 0.5 else 0)
            hot = {"hot": True, "urgent": True, "attack": cs[-1], "fatigue": BATTLE_HOT_FATIGUE}
            st = next((x for x in out if x["key"].startswith("battle:") and set(cs) <= set(x["cars"])), None)
            if st is not None:
                st.update(hot, score=st["score"] + boost, max=max(st.get("max", SEGMENT_MAX_S), BATTLE_HOT_MAX_S))
                continue
            if fp > ATTACK_TOP:
                continue                              # further back, only as part of a battle already listed
            out.append({"key": "attack:%s:%s" % (cs[0], cs[1]), "cars": cs, "max": 60.0, "fp": fp,
                        "score": 40 + boost + (30 if fp == 1 else 18 if fp <= 3 else 8),
                        "why": "going for %sP%s" % ("class " if multi else "", fp), **hot})
        # practice and qualifying: a push lap, more the better the place it is on for and the nearer the line;
        # followed to the end of the lap (it drops out of the list when the lap ends or goes off)
        is_race = bool((cars_msg.get("derived") or {}).get("isRace"))
        for c in cars:
            q = (c.get("derived") or {}).get("qualiLap")
            if is_race or not q or c.get("onPitRoad"):
                continue
            pp = q.get("projectedPos") or 99
            score = 45 + (35 if pp == 1 else 22 if pp <= 3 else 10 if pp <= 10 else 0) + 12 * (q.get("stretch") or 0) / max(1, q.get("of") or 24)
            out.append({"key": "push:%s" % c["idx"], "score": score, "cars": [c["idx"]], "max": 150.0,
                        "why": "a flying lap, on for P%s" % pp})
        # the last lap: the leader to the line
        race = (cars_msg.get("derived") or {}).get("race") or {}
        if race.get("lapsToGo") == 1 or race.get("white"):
            lead = min((c for c in cars if c.get("pos")), key=lambda c: c["pos"], default=None)
            if lead is not None:
                out.append({"key": "finish", "score": 95, "cars": [lead["idx"]], "max": 240.0, "why": "the last lap", "seq": "finish"})
        caution = bool(((cars_msg.get("derived") or {}).get("caution") or {}).get("active"))
        if caution:                                  # nobody is racing behind the pace car: battles are not news
            for st in out:
                if st["key"].startswith("battle:"):
                    st["score"] *= 0.35
        if is_race and not caution:
            # a class leader reaching a slower class's pack: TV's favourite multi-class shot
            for tr in (cars_msg.get("derived") or {}).get("classTraffic") or []:
                if tr.get("idx") in by and tr.get("inS") is not None and tr["inS"] <= 12:
                    out.append({"key": "traffic:%s" % tr["idx"], "score": 52 + (10 if tr["inS"] < 5 else 0) + (8 if (tr.get("packN") or 0) >= 3 else 0),
                                "cars": [tr["idx"]], "seq": "traffic", "why": "the leader into traffic", "max": 30.0})
            # a car coming through the field: well up on the start, and passing people lately
            passers = {e.get("idx") for e in self._moments.values() if e.get("kind") == "pass"
                       and t_live is not None and e.get("t") is not None and t_live - e["t"] < 120}
            for c in cars:
                g = (c.get("derived") or {}).get("posGained")
                if g is not None and g >= 5 and (c.get("classPos") or 99) <= 15 and c["idx"] in passers:
                    out.append({"key": "charge:%s" % c["idx"], "score": 38 + min(20, 2 * g), "cars": [c["idx"]],
                                "seq": "charge", "why": "coming through the field (up %d)" % g})
            # closing on the car ahead, not there yet: the battle to come
            by_cls = {}
            for c in cars:
                if c.get("classPos"):
                    by_cls[(c.get("classId"), c["classPos"])] = c
            for c in cars:
                d = c.get("derived") or {}
                ahead = by_cls.get((c.get("classId"), (c.get("classPos") or 0) - 1))
                gap, cl, laps = d.get("liveClassIntervalS"), d.get("closingSPerLap"), d.get("catchLaps")
                if ahead and gap is not None and 0.6 < gap < 3.0 and cl and cl > 0.15 and laps is not None and laps <= 3 \
                        and (c.get("classPos") or 99) <= 10 and not c.get("onPitRoad") and not ahead.get("onPitRoad"):
                    out.append({"key": "closing:%s:%s" % (ahead["idx"], c["idx"]), "score": 34 + (15 if c["classPos"] <= 3 else 0),
                                "cars": [ahead["idx"], c["idx"]], "why": "closing in for P%s" % ahead.get("classPos")})
            # multiclass: a faster class's car about to go past slower cars (normalize throughTraffic), not only the
            # leaders; a fight of its class going through them is that fight's story, made bigger (traffic decides it)
            fights = {i: st for st in out if st["key"].startswith(("battle:", "attack:")) for i in st["cars"]}
            for tr in (cars_msg.get("derived") or {}).get("throughTraffic") or []:
                f, cp = tr.get("idx"), tr.get("classPos") or 99
                slow = [i for i in tr.get("throughIdx") or [] if i in by]
                if f not in by or not slow or cp > 10:
                    continue
                if f in fights:
                    fights[f]["score"] += 12
                    continue
                out.append({"key": "through:%s" % f, "cars": [slow[0], f], "max": 25.0, "why": "through traffic",
                            "score": 38 + (25 if cp == 1 else 15 if cp <= 3 else 6) + 4 * min(4, len(slow))})
        # a fastest lap just set: the car that did it
        for e in moments:
            if e.get("kind") == "fastest_lap" and e.get("idx") in by and t_live is not None and e.get("t") is not None \
                    and t_live - e["t"] < 25:
                out.append({"key": "fastlap:%s" % e["idx"], "score": 48 + (8 if e.get("overall") else 0), "cars": [e["idx"]],
                            "seq": "fastlap", "why": "the fastest lap", "max": 20.0})
        # each class's leader, more interesting the longer it has been off air
        leaders = {}
        for c in cars:
            if c.get("classPos") == 1 and not c.get("onPitRoad"):
                leaders[c.get("classId")] = c
        for cid, c in leaders.items():
            since = now - self._lead_shown.get(str(cid), now - 120.0)
            out.append({"key": "leader:%s" % cid, "score": 18 + min(32.0, since / 6.0), "cars": [c["idx"]],
                        "why": "the %sleader" % ("class " if multi else "")})
        # a car near the front stopped in its box
        for c in cars:
            if c.get("surface") == "in_pit_stall" and (c.get("classPos") or 99) <= 5:
                out.append({"key": "pit:%s" % c["idx"], "score": 30, "cars": [c["idx"]], "why": "pit stop", "pit": True})
        return out

    def _hot_on_air(self, cars, by, cars_msg, moments, t_live, now) -> bool:
        """The story on air is a close battle (BATTLE_HOT_S) or someone going for it (an attack)."""
        seg = self.segment
        if not seg or not seg["key"].startswith(("battle:", "attack:")):
            return False
        stories = self._battle_continuity(self._stories(cars, by, cars_msg, moments, t_live, now), seg, now)
        return any(st.get("hot") and st["key"] == seg["key"] for st in stories)

    def _battle_continuity(self, stories, seg, now):
        """The battle on air under another id (its cars swapped places), or gone from the list for a moment."""
        if not seg or not seg["key"].startswith(("battle:", "attack:")):
            return stories
        if any(st["key"] == seg["key"] for st in stories):
            self._battle_seen = (now, next(st for st in stories if st["key"] == seg["key"]))
            return stories
        same = next((st for st in stories if st["key"].startswith(("battle:", "attack:")) and len(set(st["cars"]) & set(seg["cars"])) >= 2), None)
        if same is not None:
            self._aired[same["key"]] = self._aired.pop(seg["key"], [])
            seg["key"] = same["key"]
            self._battle_seen = (now, same)
            return stories
        seen = getattr(self, "_battle_seen", None)
        if seen and seen[1]["key"] == seg["key"] and now - seen[0] < BATTLE_GRACE_S:
            return stories + [dict(seen[1], score=seen[1]["score"] - 5 * (now - seen[0]))]
        return stories

    def _story_step(self, groups, cars, by, cars_msg, moments, t_live, weights, hold, now):
        seg = self.segment
        best, best_s = None, -1e9
        stories = self._battle_continuity(self._stories(cars, by, cars_msg, moments, t_live, now), seg, now)
        for st in stories:
            sc = st["score"] - st.get("fatigue", 0.4) * self._aired_s(st["key"], now)
            if seg and st["key"] == seg["key"]:
                age = now - seg["start"]
                sc += 25 if age < SEGMENT_MIN_S else -200 if age >= st.get("max", SEGMENT_MAX_S) else 0
                if st.get("hot") and age >= SEGMENT_MIN_S and age < st.get("max", SEGMENT_MAX_S):
                    sc += BATTLE_STAY                 # still close: something must be much bigger to take the air
            st["now"] = sc
            if sc > best_s:
                best, best_s = st, sc
        if best is None:
            return
        on_air = next((x for x in [best] if seg and x["key"] == seg["key"]), None)
        if seg and best["key"] != seg["key"] and now - seg["start"] < SEGMENT_MIN_S:
            # a story still running keeps the air unless the new one is much bigger
            cur = next((st for st in stories if st["key"] == seg["key"]), None)
            by_ = ATTACK_INTERRUPT_BY if cur is not None and best.get("urgent") and not cur.get("hot") else INTERRUPT_BY
            if cur is not None and best_s < cur["score"] - cur.get("fatigue", 0.4) * self._aired_s(cur["key"], now) + 25 + by_:
                best, on_air = cur, cur
        if not on_air:
            if seg:
                self._aired.setdefault(seg["key"], []).append((seg["start"], now))
            seg = self.segment = {"key": best["key"], "why": best["why"], "cars": best["cars"], "start": now, "shot": 0,
                                  "seq": best.get("seq"), "moveAt": -1e9}
        else:
            seg["cars"] = best["cars"]
        # the shot within the story: battles go round the battle's angles, the rest by style
        cs = [by[i] for i in seg["cars"] if i in by]
        if not cs:
            self.segment = None
            return
        if best.get("pit") and groups.get("pit"):
            car, g = cs[0], self._pick_group(groups, {"pit": 1})
        elif seg.get("seq") == "start":
            g = self._sequence_shot(groups, seg, weights)
            car = cs[-1] if g and g.get("kind") in ("chase", "far") else cs[0]
        elif len(cs) >= 2 and self._move_coming(cars_msg, seg, now) and groups.get("onboard"):
            # close and closing: inside the chasing car, where the move would come from (once in a while)
            car, g = cs[-1], self._pick_group(groups, {"onboard": 1})
            seg["moveAt"] = now
        elif len(cs) >= 2:
            car, g = None, None
            for k in range(len(BATTLE_SHOTS)):
                kind, who = BATTLE_SHOTS[(seg["shot"] + k) % len(BATTLE_SHOTS)]
                if self._fresh(groups, kind):
                    car, g = (cs[0] if who == "front" else cs[-1]), self._pick_group(groups, {kind: 1})
                    seg["shot"] = (seg["shot"] + k + 1) % len(BATTLE_SHOTS)
                    break
        else:
            car, g = cs[0], self._sequence_shot(groups, seg, weights)
        if car is not None and g and self._do_cut(car, g, seg["why"]):
            if seg["key"].startswith("leader:"):
                self._lead_shown[seg["key"].split(":", 1)[1]] = now
            for c in cs:
                if c.get("classPos") == 1:
                    self._lead_shown[str(c.get("classId"))] = now
            self._next_at = now + hold

    # ------------------------------------------------------------------ shots in order
    def _fresh(self, groups, kind) -> bool:
        """The track has a camera of this kind other than the one on air (a new story starting on the shot the
        last one ended on would be no cut at all: two stories, one picture)."""
        return any(g["num"] != self._last_group for g in groups.get(kind) or [])

    def _sequence_shot(self, groups, seg, weights):
        """The next shot of a single-car story: the story's own order, else the style's, skipping kinds the track has
        no camera for; within a kind the group avoids repeating the last one."""
        seq = STORY_SEQ.get(seg.get("seq")) or SEQUENCES.get(self.style) or SEQUENCES["cinematic"]
        for k in range(len(seq)):
            kind = seq[(seg["shot"] + k) % len(seq)]
            if self._fresh(groups, kind):
                seg["shot"] = (seg["shot"] + k + 1) % len(seq)
                return self._pick_group(groups, {kind: 1})
        return self._pick_group(groups, weights)

    def _opening(self, cars, cars_msg) -> bool:
        """The race's first lap: the leader has not yet started its second."""
        if not (cars_msg.get("derived") or {}).get("isRace"):
            return False
        lead = min((c for c in cars if c.get("pos")), key=lambda c: c["pos"], default=None)
        return lead is not None and lead.get("lap") is not None and lead["lap"] <= 1

    def _battle_of(self, cars_msg, cars) -> dict | None:
        want = set(cars or [])
        for b in (cars_msg.get("derived") or {}).get("battles") or []:
            if len(want & set(b.get("cars") or [])) >= 2:
                return b
        return None

    def _move_coming(self, cars_msg, seg, now) -> bool:
        b = self._battle_of(cars_msg, seg.get("cars"))
        if now - seg.get("moveAt", -1e9) < 20.0:
            return False
        gaps, cl = (b or {}).get("gapsS") or [], [x for x in (b or {}).get("closingSPerLap") or [] if x is not None]
        if b and bool(gaps) and min(gaps) < MOVE_GAP_S * (b.get("scale") or 1.0) and bool(cl) and max(cl) > 0:
            return True
        # going for it right now (normalize attacks) and already close: inside the chaser for the move
        cs = set(seg.get("cars") or [])
        return any(set(a.get("cars") or []) <= cs and (a.get("gapS") or 9.9) < ATTACK_ONBOARD_S * (a.get("scale") or 1.0)
                   for a in (cars_msg.get("derived") or {}).get("attacks") or [])

    def _hold_close(self, cars_msg, now) -> bool:
        """The cars on air side by side: hold the shot 2 s more (HOLD_MAX_S at most per shot)."""
        seg = self.segment
        if not seg or len(seg.get("cars") or []) < 2:
            return False
        b = self._battle_of(cars_msg, seg["cars"])
        if not b or not b.get("gapsS") or min(b["gapsS"]) >= HOLD_CLOSE_S * (b.get("scale") or 1.0):
            seg["held"] = 0.0
            return False
        if seg.get("held", 0.0) >= HOLD_MAX_S:
            seg["held"] = 0.0
            return False
        seg["held"] = seg.get("held", 0.0) + 2.0
        self._next_at = now + 2.0
        return True

    # ------------------------------------------------------------------ replays
    def _keep_moments(self, recent, state):
        for e in recent or []:
            if e.get("kind") not in ("pass", "class_lead", "race_incident"):
                continue                              # what a replay can show (and a pass is what a charge is made of)
            key = "%s:%s:%s:%s" % (e.get("sessionNum"), e.get("kind"), e.get("idx"), e.get("replayT") if e.get("replayT") is not None else e.get("t"))
            self._moments.setdefault(key, e)
        t_live = (((state or {}).get("derived") or {}).get("liveEdge") or {}).get("tLive")
        if t_live is not None and len(self._moments) > 40:
            self._moments = {k: e for k, e in self._moments.items() if e.get("t") is None or t_live - e["t"] < REPLAY_WINDOW_S * 2}

    def _seen_live(self, e) -> bool:
        """The camera was on the passer or the passed car (live) in the SEEN_LIVE_S before the pass was reported."""
        pair = {e.get("idx")} | set((e.get("passedIdx") or [])[:1])
        t, h = e.get("t"), self._cam_hist
        if t is None:
            return False
        held = [c for ts, c in h if ts <= t - SEEN_LIVE_S][-1:]      # on air as the window opened, held into it
        return any(c in pair for c in held) or any(t - SEEN_LIVE_S < ts <= t and c in pair for ts, c in h)

    def _replay_candidate(self, by, t_live, snum=None):
        best, best_s = None, REPLAY_MIN
        done = [self._moments[k] for k in self._replayed if k in self._moments]
        for key, e in self._moments.items():
            if key in self._replayed or e.get("replayT") is None or e.get("sessionNum") is None or e.get("idx") not in by:
                continue
            if snum is not None and e.get("sessionNum") != snum:
                continue                              # a moment from qualifying is not this race's (and its clock is another)
            cars = {e.get("idx")} | set(e.get("passedIdx") or []) | ({e.get("prevIdx")} if e.get("prevIdx") is not None else set())
            if any(abs(e["replayT"] - x["replayT"]) < REPLAY_SAME_S and cars & ({x.get("idx")} | set(x.get("passedIdx") or [])
                   | ({x.get("prevIdx")} if x.get("prevIdx") is not None else set())) for x in done if x.get("sessionNum") == e.get("sessionNum")):
                continue                              # the same moment, already shown
            if t_live is None or e.get("t") is None or not (REPLAY_AFTER_S <= t_live - e["t"] <= REPLAY_WINDOW_S):
                continue
            k, what = e.get("kind"), e.get("what")
            if k == "race_incident":
                if what not in ("contact", "spin", "lost_speed") or (what == "lost_speed" and e.get("confidence") == "low"):
                    continue
                score = {"contact": 75, "spin": 65, "lost_speed": 45}[what]
            elif k == "class_lead":
                score = 80
            elif k == "pass":
                # a pass the camera missed comes first (to P10); one it showed live is only seen again near the front
                cp = e.get("classPos") or 99
                score = 45 + (20 if cp <= 3 else 10 if cp <= 10 else 0) + (-10 if self._seen_live(e) else 15)
            else:
                continue
            score -= (t_live - e["t"]) / 10.0                 # the fresher the better
            if score > best_s:
                best, best_s = (key, e), score
        return best

    def _maybe_replay(self, groups, cars, by, cars_msg, moments, t_live, race_state, now) -> bool:
        if not (self.replay_fn and self.replays_on) or now - self._last_replay_at < REPLAY_GAP_S:
            return False
        caution = bool(((cars_msg.get("derived") or {}).get("caution") or {}).get("active"))
        race = (cars_msg.get("derived") or {}).get("race") or {}
        left = race.get("lapsToGo") if race.get("lapsToGo") is not None else race.get("lapsByTime")
        if race_state != "racing" or race.get("white") or (left is not None and left <= 2):   # a timed race too
            return False
        if self._opening(cars, cars_msg):
            return False                              # the start is live television: no looking back yet
        if self.segment and now - self.segment["start"] < SEGMENT_MIN_S:
            return False                              # let the story on air have its time first
        stories = self._stories(cars, by, cars_msg, moments, t_live, now)
        if self.segment and any(st.get("hot") and st["key"] == self.segment["key"] for st in stories):
            return False                              # a close battle on air is not left for a replay
        if not caution and any(st.get("urgent") for st in stories):
            return False                              # someone going for it right now: a pass may come any second
        if not caution:
            live = max((st["score"] - st.get("fatigue", 0.4) * self._aired_s(st["key"], now)
                        for st in stories), default=0.0)
            if live >= REPLAY_CALM:
                return False
        pick = self._replay_candidate(by, t_live, getattr(self, "_snum", None))
        if not pick:
            return False
        key, e = pick
        c = by[e["idx"]]
        kind = e.get("kind")
        g = self._pick_group(groups, {"trackside": 3, "aerial": 2} if kind == "race_incident" else
                             {"far": 3, "trackside": 1} if kind == "pass" else {"trackside": 2, "aerial": 1})
        if not g:
            return False
        what = {"race_incident": {"contact": "contact", "spin": "spin", "lost_speed": "trouble"}.get(e.get("what"), "incident"),
                "pass": "the pass", "class_lead": "the lead change"}.get(kind, "replay")
        span = (e.get("endT") - e["replayT"]) if e.get("endT") is not None else 0.0
        dur = max(8.0, min(18.0, span + REPLAY_PREROLL_S + 5.0)) if kind == "race_incident" else REPLAY_PREROLL_S + 8.0
        self._replayed.add(key)
        self._last_replay_at = now
        self._end_segment(now)
        self.rp = {"phase": "in", "at": now + (self.stinger_cut_s if self.stinger_on else 0.0), "moment": e,
                   "car": e["idx"], "num": c.get("num"), "group": g, "dur": dur, "key": key,
                   "label": "%s · %s" % (str(c.get("name") or "").split(" ")[-1] or "#%s" % c.get("num"), what)}
        if self.stinger_on:
            self._fire_stinger("toReplay")
        self.note = "replay: " + what
        self.changed = True
        return True

    def _fire_stinger(self, direction):
        seq = (self.stinger or {}).get("seq", 0) + 1
        self.stinger = {"seq": seq, "dir": direction}
        self.changed = True

    def _replay_step(self, now, cars_msg, recent, state):
        rp = self.rp
        if rp["phase"] == "in" and now >= rp["at"]:
            e = rp["moment"]
            res = self.replay_fn({"cmd": "replay", "action": "jump", "session": e["sessionNum"], "timeS": e["replayT"],
                                  "preroll": REPLAY_PREROLL_S, "car": str(rp["num"]), "group": int(rp["group"]["num"])})
            ok = res.get("ok", False) if isinstance(res, dict) else bool(res)
            if not ok:
                self.rp = None
                self.note = "replay refused: " + str((res or {}).get("error") if isinstance(res, dict) else "")
                self._next_at = now
                self.changed = True
                return
            rp["phase"], rp["at"] = "play", now + rp["dur"]
            self.current = {"car": rp["car"], "num": rp["num"], "group": rp["group"]["num"],
                            "groupName": rp["group"].get("name"), "reason": "replay: " + rp["label"]}
            self.changed = True
        elif rp["phase"] == "play":
            # back early for something big happening live: a fresh contact or spin, a new leader
            t_live = (((state or {}).get("derived") or {}).get("liveEdge") or {}).get("tLive")
            urgent = any(x.get("t") is not None and t_live is not None and 0 <= t_live - x["t"] < 6 and
                         (x.get("kind") == "class_lead" or (x.get("kind") == "race_incident" and x.get("what") in ("contact", "spin")))
                         for x in recent or [])
            if now >= rp["at"] or urgent:
                rp["phase"], rp["at"] = "out", now + (self.stinger_cut_s if self.stinger_on else 0.0)
                if self.stinger_on:
                    self._fire_stinger("toLive")
                self.changed = True
        elif rp["phase"] == "out" and now >= rp["at"]:
            self.replay_fn({"cmd": "replay", "action": "search", "mode": "to_end"})
            self.rp = None
            self.note = "back to live"
            self._next_at = now + 0.6                 # let the picture come back before the next cut
            self.changed = True

    def _battle_step(self, groups, by, cars_msg, now):
        b = self.battle
        want = set(b["cars"])
        battles = ((cars_msg.get("derived") or {}).get("battles")) or []
        # the same battle: shares at least two cars (it may have grown or shrunk)
        match = max(battles, key=lambda x: len(want & set(x["cars"])), default=None)
        if match and len(want & set(match["cars"])) >= 2:
            b["cars"], b["lostAt"] = list(match["cars"]), None
        else:
            b["lostAt"] = b["lostAt"] if b["lostAt"] is not None else now
            if now - b["lostAt"] >= BATTLE_LOST_S:
                self._end_temporary("the battle is over")
                return
        front, back = by.get(b["cars"][0]), by.get(b["cars"][-1])
        if not front or not back:
            return
        for k in range(len(BATTLE_SHOTS)):
            kind, who = BATTLE_SHOTS[(b["shot"] + k) % len(BATTLE_SHOTS)]
            if not groups.get(kind):
                continue
            g = self._pick_group(groups, {kind: 1})
            car = front if who == "front" else back
            if g and self._do_cut(car, g, "battle"):
                b["shot"] = (b["shot"] + k + 1) % len(BATTLE_SHOTS)
                self._next_at = now + self.battle_hold_s
                return
        self._next_at = now + 1.0

    # ------------------------------------------------------------------ the grid walk's time
    def _sample_pace(self, session, cars_msg, race_state, now):
        if race_state != "parade_laps":
            self._pace, self.pace_eta, self._pace_target = [], None, None
            return
        rows = ((session or {}).get("grid") or {}).get("rows") or []
        lead = rows[0]["idx"] if rows else None
        car = next((c for c in cars_msg.get("cars") or [] if c.get("idx") == lead), None)
        if car is None or car.get("pct") is None or car.get("lap") is None or car["pct"] < 0:
            return
        d = car["lap"] + car["pct"]
        self._pace.append((now, d))
        self._pace = [p for p in self._pace if now - p[0] <= 8.0]
        (t0, d0), (t1, d1) = self._pace[0], self._pace[-1]
        if t1 - t0 < 2.0 or d1 <= d0:
            return
        rate = (d1 - d0) / (t1 - t0)                      # laps a second, at the pace car's speed
        if getattr(self, "_pace_target", None) is None:
            # the line ahead when the pace laps began; one just ahead is the one the field rolls over on its way
            self._pace_target = math.floor(d0) + 1 + (1 if math.floor(d0) + 1 - d0 < GRID_JUST_AHEAD else 0)
        while self._pace_target <= d1:
            self._pace_target += 1                        # still pacing after that line: another lap
        self.pace_eta = (self._pace_target - d1) / rate

    def _grid_rows(self, session) -> dict:
        out = {}
        for r in ((session or {}).get("grid") or {}).get("rows") or []:
            row = (r.get("derived") or {}).get("row") or ((r.get("pos") or 0) + 1) // 2
            out[r.get("idx")] = row
        return out

    def _grid_plan(self, session, car) -> float:
        """The hold for this car: the walk's, or shorter to fit the pace laps, leaving rows out when even the
        shortest holds do not fit (planned at the first car of each row, so a row is never split)."""
        hold = self.grid_hold_s
        rows_of = self._grid_rows(session)
        unit = (lambda c: rows_of.get(c.get("idx"), c.get("idx"))) if self.grid_by_row else (lambda c: c.get("idx"))
        units, order = {}, []
        for c in [car] + self.grid:
            u = unit(c)
            if u not in units:
                units[u] = []
                order.append(u)
            units[u].append(c)
        if self.grid_paused or self.grid_manual:
            return hold                               # by hand: the producer sets the pace, nothing is left out
        if getattr(self, "_standing", False):
            return min(hold, GRID_ROW_MIN_S / 2)      # a standing start: no pace to fit, little time, a row in 5 s
        if self.pace_eta is None:
            return hold
        budget = self.pace_eta * GRID_SAFETY
        n = sum(len(v) for v in units.values())
        if n * hold <= budget:
            return hold
        per_car_min = GRID_ROW_MIN_S / 2
        if n * per_car_min <= budget:
            return max(per_car_min, budget / n)
        # leave rows out: the front rows and the rows with a class's pole first, then the rest from the front
        poles = getattr(self, "_grid_poles", set())
        front = lambda u: (rows_of.get(units[u][0].get("idx")) or 99) <= GRID_FRONT_ROWS
        polar = [u for u in order if any(c.get("idx") in poles for c in units[u])]   # the pole and each class's
        first = polar + [u for u in order if front(u) and u not in polar]
        rest = [u for u in order if u not in first]
        keep, used = [], 0.0
        for u in first + rest:
            need = len(units[u]) * per_car_min
            if used + need > budget and keep:
                continue
            keep.append(u)
            used += need
        if order[0] not in keep:
            keep.insert(0, order[0])
        dropped = [u for u in order if u not in keep]
        if dropped:
            self.grid_short = True
            self.note = "short walk: the front of the grid and each class's pole"
            kept = {id(c) for u in keep for c in units[u]}
            self.grid = [c for c in self.grid if id(c) in kept]
        return per_car_min

    def _grid_step(self, groups, now, session=None, cars_msg=None):
        if not self.grid:
            self.current = None
            self._end_temporary("grid walk done")
            return
        car = self.grid.pop(0)
        live = {c.get("idx"): c for c in (cars_msg or {}).get("cars") or []}
        rows_of = self._grid_rows(session)
        row_start = not self.grid_by_row or getattr(self, "_grid_row", None) != rows_of.get(car.get("idx"))
        self._grid_row = rows_of.get(car.get("idx"))
        if row_start:
            self._grid_hold = self._grid_plan(session, car)
        k = self.grid_total - len(self.grid) - 1                 # this car's place in the walk, from 0
        pole = car.get("idx") in getattr(self, "_grid_poles", set())
        hold = getattr(self, "_grid_hold", self.grid_hold_s)
        # not on the grid yet (still in the garage at a standing start): its row card still shows it (the picture
        # is iRacing's render, not the camera's), with the camera wide over the grid on a car that is there
        if live and (car.get("idx") not in live or live[car.get("idx")].get("surface") == "not_in_world"):
            row = rows_of.get(car.get("idx"))
            there = [c for c in self._grid_order if c.get("idx") in live and live[c["idx"]].get("surface") != "not_in_world"]
            mate = next((c for c in there if row is not None and rows_of.get(c.get("idx")) == row), None)
            on = mate or (there[0] if there else None)
            g = self._wide_group(groups)
            if on is not None and g is not None and self._do_cut(on, g, "starting grid: not out yet"):
                pass
            self.current = {"car": car.get("idx"), "num": car.get("num"), "group": (g or {}).get("num"),
                            "groupName": (g or {}).get("name"), "reason": "starting grid: not out yet",
                            "camCar": on.get("idx") if on else None}
            self.current["gridPos"] = self._grid_at() + 1
            self.changed = True
            self._next_at = now + hold
            return
        by_name = {(g.get("name") or "").lower(): g for gs in groups.values() for g in gs}
        want = GRID_SHOTS[0] if pole else GRID_SHOTS[k % len(GRID_SHOTS)]
        pick = by_name.get(want) or by_name.get("far chase") or by_name.get("chase")
        if pick is None:
            chase = groups.get("chase") or groups.get("trackside") or []
            if not chase:
                return
            pick = chase[0]
        g = {**pick, "kind": group_kind(pick.get("name"))}
        if self._do_cut(car, g, "starting grid" + (": pole" if pole else "")):
            self.current["gridPos"] = self._grid_at() + 1
            if self.on_grid_car:
                self.on_grid_car(car)
        self.changed = True
        self._next_at = now + hold * (GRID_POLE_HOLD if pole and hold >= self.grid_hold_s else 1.0)

    def _wide_group(self, groups):
        for kind in GRID_WIDE:
            if groups.get(kind):
                g = groups[kind][0]
                return {**g, "kind": kind}
        return None

    def _board_step(self, session, cars_msg, race_state, now) -> bool:
        """The grid board first, when the walk will not fit (see GRID_BOARD_*). True while it holds the camera
        (or the walk waits a moment for the pace laps' pace to decide)."""
        if self._board in ("done", "no"):
            return False
        rows_of = self._grid_rows(session)
        if self._board is None:
            if self.grid_board == "never":
                self._board = "no"
            elif self.grid_board == "always" or getattr(self, "_standing", False):
                self._board = "on"
            elif race_state == "parade_laps" and self.pace_eta is None and now - getattr(self, "_walk_t0", now) < GRID_PLAN_WAIT_S:
                return True                           # a moment for the pace: the camera stays where it is
            else:
                # only when even the shortest holds would leave cars out: then every car is seen on the board
                n = len(self.grid)
                self._board = "on" if (self.pace_eta is not None and n * GRID_ROW_MIN_S / 2 > self.pace_eta * GRID_SAFETY) else "no"
            if self._board != "on":
                return False
            self._board_rows = max([rows_of.get(c.get("idx")) or 0 for c in self.grid] or [1]) or 1
            self._board_len = max(GRID_BOARD_MIN_S, min(GRID_BOARD_MAX_S, GRID_BOARD_BASE_S + GRID_BOARD_ROW_S * self._board_rows))
            self._board_t0 = now
            self.grid_board_row = 0
            self.note = "the grid board" + (": standing start" if getattr(self, "_standing", False) else "")
            # the shot: wide over the grid, on the pole car if it is there, else the first car that is
            live = {c.get("idx"): c for c in cars_msg.get("cars") or []}
            there = [c for c in self.grid if c.get("idx") in live and live[c["idx"]].get("surface") != "not_in_world"]
            g = self._wide_group(self._groups(session))
            if there and g is not None:
                self._do_cut(there[0], g, "starting grid: the board")
            self.changed = True
            return True
        el = now - self._board_t0
        if el >= self._board_len:
            self._board, self.grid_board_row = "done", None
            self._grid_force, self._grid_row = True, None   # the row cards at once
            self.changed = True
            return False
        per = (self._board_len - GRID_BOARD_IN_S) / self._board_rows
        row = 0 if el < GRID_BOARD_IN_S else min(self._board_rows, int((el - GRID_BOARD_IN_S) / per) + 1)
        if row != self.grid_board_row:
            self.grid_board_row = row
            self.changed = True
        return True
