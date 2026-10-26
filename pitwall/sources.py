"""Telemetry sources.

Every source exposes the same tiny interface so that everything above this
layer (normalize, recorder, bridge) never knows whether it is talking to
iRacing, a memory dump, or a simulation.

    source.connect() -> bool
    source.tick()    -> bool        advance one frame; False if no new data
    source.get(name) -> value|None  raw iRacing variable, None if absent
    source.var_headers() -> list[dict]  name/type/count/unit/desc for every var
    source.session_info(key) -> dict|None   parsed YAML section (WeekendInfo, DriverInfo, ...)
    source.session_info_update -> int       increments when YAML changes
    source.is_connected -> bool
    source.close()

Sources:
    LiveSource  - real iRacing via pyirsdk shared memory (Windows only)
    DumpSource  - a memory dump produced by `python -m irsdk --dump file.bin`
                  (works on any OS; one frozen frame, useful for schema checks)
    FakeSource  - a simulated 24-car field on a synthetic track. Emits the
                  same variable names iRacing does, with physically consistent
                  VelocityX / YawNorth / LapDistPct so the track-map math can
                  be verified against a known outline.
"""
from __future__ import annotations

import math
import random
import time
from typing import Any

# --------------------------------------------------------------------------- #
# Base
# --------------------------------------------------------------------------- #


class TelemetrySource:
    name = "base"

    def connect(self) -> bool:
        raise NotImplementedError

    def tick(self) -> bool:
        raise NotImplementedError

    def get(self, name: str) -> Any:
        raise NotImplementedError

    def var_headers(self) -> list[dict]:
        raise NotImplementedError

    def session_info(self, key: str) -> Any:
        raise NotImplementedError

    @property
    def session_info_update(self) -> int:
        raise NotImplementedError

    @property
    def is_connected(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        pass

    # Only LiveSource can send commands. Others raise so nobody can
    # accidentally "succeed" at a pit command against a fake.
    def irsdk(self):
        raise RuntimeError(f"{self.name} source cannot send commands to iRacing")


# --------------------------------------------------------------------------- #
# Live (pyirsdk shared memory)
# --------------------------------------------------------------------------- #


class LiveSource(TelemetrySource):
    name = "live"

    def __init__(self):
        import irsdk  # imported lazily so non-Windows machines can still use FakeSource

        self._irsdk_mod = irsdk
        self.ir = irsdk.IRSDK()
        self._names: set[str] = set()

    def connect(self) -> bool:
        ok = self.ir.startup()
        if ok:
            self._names = set(self.ir.var_headers_names)
        return bool(ok)

    def tick(self) -> bool:
        if not self.ir.is_initialized or not self.ir.is_connected:
            # try to re-attach (sim was closed / reopened)
            self.ir.shutdown()
            if not self.connect():
                return False
        # freeze gives us one consistent frame for every get() this tick
        self.ir.freeze_var_buffer_latest()
        return True

    def get(self, name: str) -> Any:
        if name not in self._names:
            return None
        try:
            return self.ir[name]
        except Exception:
            return None

    def var_headers(self) -> list[dict]:
        out = []
        for h in self.ir._var_headers:  # pyirsdk keeps these private; read-only use
            out.append(dict(name=h.name, type=h.type, count=h.count,
                            unit=h.unit, desc=h.desc, count_as_time=h.count_as_time))
        return out

    def session_info(self, key: str) -> Any:
        try:
            return self.ir[key]
        except Exception:
            return None

    @property
    def session_info_update(self) -> int:
        try:
            return int(self.ir.session_info_update)
        except Exception:
            return -1

    @property
    def is_connected(self) -> bool:
        try:
            return bool(self.ir.is_initialized and self.ir.is_connected)
        except Exception:
            return False

    def close(self) -> None:
        self.ir.shutdown()

    def irsdk(self):
        return self.ir


# --------------------------------------------------------------------------- #
# Dump-file playback (single frozen frame)
# --------------------------------------------------------------------------- #


class DumpSource(LiveSource):
    """Reads a raw memory-map dump. Useful to check normalization against a
    real frame without iRacing running. Produce one on the sim PC with:
        python -m irsdk --dump snapshot.bin
    """
    name = "dump"

    def __init__(self, path: str):
        super().__init__()
        self.path = path

    def connect(self) -> bool:
        ok = self.ir.startup(test_file=self.path)
        if ok:
            self._names = set(self.ir.var_headers_names)
        return bool(ok)

    def tick(self) -> bool:
        if not self.ir.is_initialized:
            return self.connect()
        self.ir.freeze_var_buffer_latest()
        return True

    @property
    def is_connected(self) -> bool:
        return bool(self.ir.is_initialized)

    def irsdk(self):
        raise RuntimeError("dump source cannot send commands to iRacing")


# --------------------------------------------------------------------------- #
# Fake field on a synthetic track
# --------------------------------------------------------------------------- #

# Variable headers the fake advertises. Types mirror irsdk VAR_TYPE_MAP
# (1=bool, 2=int, 3=bitfield, 4=float, 5=double). count 64 == CarIdx array.
_FAKE_HEADERS: list[tuple[str, int, int, str, str]] = [
    # session
    ("SessionTime", 5, 1, "s", "Seconds since session start"),
    ("SessionNum", 2, 1, "", "Session number"),
    ("SessionState", 2, 1, "irsdk_SessionState", "Session state"),
    ("SessionFlags", 3, 1, "irsdk_Flags", "Session flags"),
    ("PitsOpen", 1, 1, "", "True if pit stop is allowed for the current player"),
    ("SessionTimeRemain", 5, 1, "s", "Seconds left till session ends"),
    ("SessionLapsRemainEx", 2, 1, "", "New improved laps left till session ends"),
    ("SessionTimeOfDay", 4, 1, "s", "Time of day in seconds"),
    ("PlayerCarIdx", 2, 1, "", "Players carIdx"),
    ("IsReplayPlaying", 1, 1, "", "0=replay not playing 1=replay playing"),
    ("ReplayFrameNum", 2, 1, "", "Integer replay frame number"),
    ("IsOnTrack", 1, 1, "", "1=Car on track physics running with player in car"),
    ("IsOnTrackCar", 1, 1, "", "1=Car on track physics running"),
    ("IsInGarage", 1, 1, "", "1=Car in garage physics running"),
    # weather
    ("AirTemp", 4, 1, "C", "Temperature of air at start/finish line"),
    ("TrackTemp", 4, 1, "C", "Deprecated, set to TrackTempCrew"),
    ("TrackTempCrew", 4, 1, "C", "Temperature of track measured by crew around track"),
    ("AirPressure", 4, 1, "Pa", "Pressure of air at start/finish line"),
    ("AirDensity", 4, 1, "kg/m^3", "Density of air at start/finish line"),
    ("WindVel", 4, 1, "m/s", "Wind velocity at start/finish line"),
    ("WindDir", 4, 1, "rad", "Wind direction at start/finish line"),
    ("RelativeHumidity", 4, 1, "%", "Relative Humidity at start/finish line"),
    ("Skies", 2, 1, "", "Skies (0=clear/1=p cloudy/2=m cloudy/3=overcast)"),
    ("FogLevel", 4, 1, "%", "Fog level at start/finish line"),
    ("Precipitation", 4, 1, "%", "Precipitation at start/finish line"),
    ("TrackWetness", 2, 1, "irsdk_TrackWetness", "How wet is the average track surface"),
    ("WeatherDeclaredWet", 1, 1, "", "The steward says rain tires can be used"),
    ("SolarAltitude", 4, 1, "rad", "Sun angle above horizon in radians"),
    ("SolarAzimuth", 4, 1, "rad", "Sun angle clockwise from north in radians"),
    # player car
    ("Speed", 4, 1, "m/s", "GPS vehicle speed"),
    ("RPM", 4, 1, "revs/min", "Engine rpm"),
    ("Gear", 2, 1, "", "-1=reverse 0=neutral 1..n=current gear"),
    ("Throttle", 4, 1, "%", "0=off throttle to 1=full throttle"),
    ("Brake", 4, 1, "%", "0=brake released to 1=max pedal force"),
    ("Clutch", 4, 1, "%", "0=disengaged to 1=fully engaged"),
    ("SteeringWheelAngle", 4, 1, "rad", "Steering wheel angle"),
    ("FuelLevel", 4, 1, "l", "Liters of fuel remaining"),
    ("FuelLevelPct", 4, 1, "%", "Percent fuel remaining"),
    ("FuelUsePerHour", 4, 1, "kg/h", "Engine fuel used instantaneous"),
    ("WaterTemp", 4, 1, "C", "Engine coolant temp"),
    ("OilTemp", 4, 1, "C", "Engine oil temperature"),
    ("OilPress", 4, 1, "bar", "Engine oil pressure"),
    ("Voltage", 4, 1, "V", "Engine voltage"),
    ("EngineWarnings", 3, 1, "irsdk_EngineWarnings", "Bitfield for warning lights"),
    ("Lap", 2, 1, "", "Laps started count"),
    ("LapCompleted", 2, 1, "", "Laps completed count"),
    ("LapDistPct", 4, 1, "%", "Percentage distance around lap"),
    ("LapDist", 4, 1, "m", "Meters traveled from S/F this lap"),
    ("LapLastLapTime", 4, 1, "s", "Players last lap time"),
    ("LapBestLapTime", 4, 1, "s", "Players best lap time"),
    ("LapCurrentLapTime", 4, 1, "s", "Estimate of players current lap time"),
    ("LapDeltaToBestLap", 4, 1, "s", "Delta time for best lap"),
    ("LapDeltaToSessionBestLap", 4, 1, "s", "Delta time for session best lap"),
    ("OnPitRoad", 1, 1, "", "Is the player car on pit road between the cones"),
    ("PlayerCarInPitStall", 1, 1, "", "Players car is properly in there pitstall"),
    ("CarLeftRight", 2, 1, "irsdk_CarLeftRight", "Notify if car is to the left or right of driver"),
    ("PlayerCarPosition", 2, 1, "", "Players position in race"),
    ("PlayerCarClassPosition", 2, 1, "", "Players class position in race"),
    ("PlayerCarMyIncidentCount", 2, 1, "", "Players own incident count for this session"),
    ("PlayerCarTeamIncidentCount", 2, 1, "", "Players team incident count for this session"),
    ("PlayerCarDriverIncidentCount", 2, 1, "", "Teams current drivers incident count for this session"),
    ("PlayerCarTowTime", 4, 1, "s", "Players car is being towed if time is greater than zero"),
    ("PlayerTrackSurface", 2, 1, "irsdk_TrkLoc", "Players car track surface type"),
    ("PitRepairLeft", 4, 1, "s", "Time left for mandatory pit repairs if repairs are active"),
    ("PitOptRepairLeft", 4, 1, "s", "Time left for optional repairs if repairs are active"),
    ("PitSvFlags", 3, 1, "irsdk_PitSvFlags", "Bitfield of pit service checkboxes"),
    ("PitSvFuel", 4, 1, "l or kWh", "Pit service fuel add amount"),
    ("PitSvLFP", 4, 1, "kPa", "Pit service left front tire pressure"),
    ("PitSvRFP", 4, 1, "kPa", "Pit service right front tire pressure"),
    ("PitSvLRP", 4, 1, "kPa", "Pit service left rear tire pressure"),
    ("PitSvRRP", 4, 1, "kPa", "Pit service right rear tire pressure"),
    ("PitSvTireCompound", 2, 1, "", "Pit service pending tire compound"),
    ("PitstopActive", 1, 1, "", "Is the player getting pit stop service"),
    ("dcBrakeBias", 4, 1, "", "In car brake bias adjustment"),
    ("dcABS", 4, 1, "", "In car abs adjustment"),
    ("dcTractionControl", 4, 1, "", "In car traction control adjustment"),
    ("dcAntiRollFront", 4, 1, "", "In car front anti roll bar adjustment"),
    ("dcAntiRollRear", 4, 1, "", "In car rear anti roll bar adjustment"),
    ("dcFuelMixture", 4, 1, "", "In car fuel mixture adjustment"),
    ("VelocityX", 4, 1, "m/s", "X velocity"),
    ("VelocityY", 4, 1, "m/s", "Y velocity"),
    ("YawNorth", 4, 1, "rad", "Yaw orientation relative to north"),
    ("Lat", 5, 1, "deg", "Latitude in decimal degrees"),
    ("Lon", 5, 1, "deg", "Longitude in decimal degrees"),
    ("LatAccel", 4, 1, "m/s^2", "Lateral acceleration (including gravity)"),
    ("LongAccel", 4, 1, "m/s^2", "Longitudinal acceleration (including gravity)"),
    # tires (iRacing reports these per corner; wear/tread only refresh at pit stops)
    *[(f"{c}temp{p}", 4, 1, "C", f"{c} tire {p} carcass temperature")
      for c in ("LF", "RF", "LR", "RR") for p in ("CL", "CM", "CR")],
    *[(f"{c}wear{p}", 4, 1, "%", f"{c} tire {p} percent tread remaining")
      for c in ("LF", "RF", "LR", "RR") for p in ("L", "M", "R")],
    *[(f"{c}coldPressure", 4, 1, "kPa", f"{c} tire cold pressure as set in the pits")
      for c in ("LF", "RF", "LR", "RR")],
    # CarIdx arrays
    ("CarIdxLap", 2, 64, "", "Laps started by car index"),
    ("CarIdxLapCompleted", 2, 64, "", "Laps completed by car index"),
    ("CarIdxLapDistPct", 4, 64, "%", "Percentage distance around lap by car index"),
    ("CarIdxPosition", 2, 64, "", "Cars position in race by car index"),
    ("CarIdxClassPosition", 2, 64, "", "Cars class position in race by car index"),
    ("CarIdxClass", 2, 64, "", "Cars class id by car index"),
    ("CarIdxF2Time", 4, 64, "s", "Race time behind leader or fastest lap time otherwise"),
    ("CarIdxEstTime", 4, 64, "s", "Estimated time to reach current location on track"),
    ("CarIdxOnPitRoad", 1, 64, "", "On pit road between the cones by car index"),
    ("CarIdxTrackSurface", 2, 64, "irsdk_TrkLoc", "Track surface type by car index"),
    ("CarIdxLastLapTime", 4, 64, "s", "Cars last lap time"),
    ("CarIdxBestLapTime", 4, 64, "s", "Cars best lap time"),
    ("CarIdxBestLapNum", 2, 64, "", "Cars best lap number"),
    ("CarIdxTireCompound", 2, 64, "", "Cars current tire compound"),
    ("CarIdxSessionFlags", 3, 64, "irsdk_Flags", "Session flags by car index"),
    ("CarIdxFastRepairsUsed", 2, 64, "", "How many fast repairs each car has used"),
    ("CarIdxP2P_Status", 1, 64, "", "Push2Pass active or not"),
    ("CarIdxP2P_Count", 2, 64, "", "Push2Pass count of usage (or remaining in Race)"),
    ("CarIdxGear", 2, 64, "", "-1=reverse 0=neutral 1..n=current gear by car index"),
    ("CarIdxRPM", 4, 64, "revs/min", "Engine rpm by car index"),
    ("CarIdxSteer", 4, 64, "rad", "Steering wheel angle by car index"),
    ("CarIdxPaceFlags", 3, 64, "irsdk_PaceFlags", "Pacing status flags for each car"),
]


def synthetic_track(n: int = 2000) -> list[tuple[float, float]]:
    """A closed, non-circular outline (~5.1 km) with straights and mixed-radius
    corners so map reconstruction has something non-trivial to match.
    Returns points in meters, roughly centered on origin."""
    pts = []
    for i in range(n):
        t = 2 * math.pi * i / n
        # base ellipse + harmonics = kinks and a long back straight
        r = 600 + 180 * math.cos(3 * t) + 90 * math.sin(5 * t + 0.7) + 60 * math.cos(2 * t + 2.1)
        x = r * math.cos(t) * 1.25
        y = r * math.sin(t) * 0.75
        pts.append((x, y))
    return pts


def _arc_table(pts):
    """Cumulative arc length and total length for a closed polyline."""
    cum = [0.0]
    for i in range(1, len(pts) + 1):
        a, b = pts[i - 1], pts[i % len(pts)]
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    return cum, cum[-1]


class _FakeCar:
    def __init__(self, idx, rng, base_lap):
        self.idx = idx
        self.pct = rng.random()
        self.laps_completed = 0
        self.base_lap = base_lap
        self.last_lap = 0.0
        self.best_lap = 0.0
        self.lap_start_t = 0.0
        self.on_pit = False
        # None, "lane_in", "box" or "lane_out". A stop is a sequence, as in the
        # sim: drive in at the limiter, sit stationary in the box (TrackSurface
        # in_pit_stall), drive out at the limiter.
        self.pit_phase = None
        self.pit_planned = False
        self.box_until = 0.0
        self.compound = 0
        self.fast_repairs = 0
        self.gear = 4
        self.rpm = 7000.0


class FakeSource(TelemetrySource):
    """24 cars lapping a synthetic ~5.1 km circuit at ~90 s/lap.

    Player is CarIdx 0. Everything the normalizer asks for exists here with the
    same names/units as iRacing, so the full pipeline can run without the sim.
    Speed varies with local curvature, and VelocityX / YawNorth / LapDistPct are
    consistent with the outline returned by synthetic_track(), which is what
    tests/test_trackmap.py checks.
    """
    name = "fake"
    HZ = 60

    # Pit lane geometry on the synthetic circuit: the lane leaves the track just
    # before the line, the boxes sit on the line, the lane rejoins just after it.
    PIT_IN_PCT = 0.955
    PIT_OUT_PCT = 0.045
    PIT_LANE_MS = 16.7          # 60 km/h limiter

    # Camera groups in iRacing's CameraInfo shape. Real group numbers differ by
    # track, which is why the producer panel reads them from the session.
    CAMERA_GROUPS = [
        (1, "Nose"), (2, "Gearbox"), (3, "Roll Bar"), (4, "LF Susp"), (5, "LR Susp"), (6, "Gyro"),
        (7, "RF Susp"), (8, "RR Susp"), (9, "Cockpit"), (10, "Scenic"), (11, "TV1"), (12, "TV2"),
        (13, "TV3"), (14, "Pit Lane"), (15, "Pit Lane 2"), (16, "Blimp"), (17, "Chopper"),
        (18, "Chase"), (19, "Far Chase"), (20, "Rear Chase"),
    ]

    def __init__(self, seed: int = 7, n_cars: int = 24, realtime: bool = True,
                 speed: float = 1.0, player_pit_lap: int | None = None, spectator: bool = False,
                 incidents: bool | None = None, incident_every_s: float = 25.0,
                 hide_incident_counts: bool | None = None, parade_s: float = 0.0,
                 grid_s: float = 0.0, team: bool = False, caution: tuple | None = None,
                 pits_closed_s: float = 0.0, replay_shape: bool = False):
        # replay_shape=True: what iRacing sends while a REPLAY plays (seen at
        # Talladega, 2026-10): SessionFlags 0 throughout (no green, no caution,
        # no per-car flags), PitsOpen always True, CarIdxEstTime 0.0 for every
        # car and CarClassEstLapTime 1.0. The field still crawls under a caution.
        self.replay_shape = replay_shape
        # caution=(start_s, end_s): a full-course caution, as on an oval (iRacing
        # runs none in road racing). The field crawls behind the pace car, the
        # flag reads caution (waving for the first seconds), and the pits stay
        # closed for pits_closed_s after it comes out.
        self.caution = caution
        self.pits_closed_s = pits_closed_s
        # team=True: a team race. Each car's driver swaps at every stop, and the
        # session info names the new driver -- iRacing's shape (DriverInfo gives
        # the CURRENT driver per car, and the session info update count ticks).
        self.team = team
        # spectator=True behaves like a spectator slot, as observed live:
        # PlayerCarIdx is 63 (not a car), the session is a replay at speed 1,
        # kinematics follow the camera car, and private car state (fuel, tyres,
        # dc*, pit service, incidents) is absent.
        self.spectator = spectator
        self.parade_s = parade_s
        # grid_s: before the start iRacing's field sits still on track (warmup)
        # and then crawls round behind the pace car (parade_laps). A detector
        # that learns "normal" speed must not learn it there, nor flag it.
        self.grid_s = grid_s
        self.cam = {"car": 0, "group": 11, "num": 1}
        self.replay_speed, self.replay_slow = 1, False
        self.replay_behind = 0.0
        # Incidents, in iRacing's shape (opt-in, on by default for a spectator
        # field so older tests keep their exact race). What makes them awkward,
        # and so worth faking: the count lives in the SESSION INFO and moves
        # seconds after the event; concurrent incidents count only the most
        # serious one ("2x that causes a 4x" is 4x); light contact is 0x and
        # never moves the count; and a count can rise with nothing visible in
        # the telemetry (contact, a wall) or a car can leave the track and earn
        # nothing yet. Their own RNG, so they do not disturb the field's.
        self.incidents_on = spectator if incidents is None else incidents
        self.incident_every_s = incident_every_s
        # Spectators do not see other cars' counts until the race is over.
        self.hide_incident_counts = spectator if hide_incident_counts is None else hide_incident_counts
        self.inc_rng = random.Random(seed + 101)
        self.inc_effect: dict[int, dict] = {}     # idx -> {"until", "speed", "off_until"}
        self.inc_pending: dict[int, dict] = {}    # idx -> {"at", "points"} awaiting the session update
        self.inc_log: list[dict] = []             # ground truth for tests: what really happened
        self.inc_hidden: dict[int, int] = {}      # counted but not yet published
        self.rng = random.Random(seed)
        self.realtime = realtime
        # speed > 1 runs the race faster than real time (for looking at a whole
        # race in a few minutes); it scales the simulated clock, not the rate.
        self.speed = speed
        # Lap on which the player car makes one stop (None = never). Opt-in so the
        # older tests keep a player that never pits.
        self.player_pit_lap = player_pit_lap
        self.n_cars = n_cars
        self.track = synthetic_track()
        self.cum, self.length = _arc_table(self.track)
        self.t = 0.0
        self.frame = 0
        self._siu = 1
        # Three classes, because a single-make fake cannot exercise any of the
        # multiclass handling -- class colours, class position, or standings split
        # by class. Colours and RelSpeed follow iRacing's own shape: a 24-bit RGB
        # int per class, and a relative-speed rank that orders the classes.
        self.classes = [
            {"id": 10, "short": "GT3", "name": "Fake GT3", "color": 0xFF4C4C, "rel": 100, "lap": 88.0},
            {"id": 20, "short": "GT4", "name": "Fake GT4", "color": 0x4CC9FF, "rel": 88, "lap": 96.0},
            {"id": 30, "short": "TCR", "name": "Fake TCR", "color": 0x8BE04C, "rel": 78, "lap": 103.0},
        ]
        self.cars = []
        for i in range(n_cars):
            cl = self.classes[i % len(self.classes)]
            car = _FakeCar(i, self.rng, cl["lap"] + self.rng.random() * 4.0)
            car.cls = cl
            self.cars.append(car)
        self.cars[0].base_lap = self.cars[0].cls["lap"] + 1.5
        self.fuel = 60.0
        self.fuel_max = 110.0
        self.air = 22.0
        self.track_temp = 31.0
        self.wetness = 1
        self.precip = 0.0
        self._last_wall = None
        self._connected = False
        self._headers = [dict(name=n, type=t, count=c, unit=u, desc=d, count_as_time=False)
                         for (n, t, c, u, d) in _FAKE_HEADERS]
        self._vars: dict[str, Any] = {}
        self._session = self._build_session_info()

    def _in_caution(self) -> bool:
        return bool(self.caution) and self.caution[0] <= self.t < self.caution[1]

    def _flags(self) -> int:
        if not self._in_caution():
            return 0x0004                                    # green
        waving = self.t - self.caution[0] < 5.0
        return 0x4000 | (0x8000 if waving else 0)            # caution (waving at first)

    @staticmethod
    def _sun_alt(tod_s):
        import math
        phi, dec = math.radians(37.0), math.radians(23.3)
        h = math.radians((tod_s / 3600.0 - 13.0) * 15.0)
        return math.asin(math.sin(phi) * math.sin(dec) + math.cos(phi) * math.cos(dec) * math.cos(h))

    def _pits_open(self) -> bool:
        return not (self._in_caution() and self.t - self.caution[0] < self.pits_closed_s)

    # --- geometry helpers -------------------------------------------------
    def pos_at(self, pct: float) -> tuple[float, float]:
        d = (pct % 1.0) * self.length
        n = len(self.track)
        lo, hi = 0, n
        while lo < hi:  # bisect on cumulative arc length
            mid = (lo + hi) // 2
            if self.cum[mid + 1] < d:
                lo = mid + 1
            else:
                hi = mid
        i = min(lo, n - 1)
        a, b = self.track[i], self.track[(i + 1) % n]
        seg = self.cum[i + 1] - self.cum[i]
        f = 0.0 if seg == 0 else (d - self.cum[i]) / seg
        return (a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f)

    def heading_at(self, pct: float) -> float:
        """YawNorth in iRacing's convention, so the map test proves the real thing.

        iRacing's heading works out to (-cos(yaw), sin(yaw)), not (cos(yaw),
        sin(yaw)) -- verified by rebuilding a real lap and comparing against
        iRacing's own track map. This used to return a plain atan2(dy, dx),
        which trackmap.integrate() inverted exactly, so the pair agreed with
        each other and disagreed with the sim: every real outline came out
        mirrored while the test passed at 1.5 m.
        """
        p0 = self.pos_at(pct - 0.0005)
        p1 = self.pos_at(pct + 0.0005)
        return math.atan2(p1[1] - p0[1], -(p1[0] - p0[0]))

    CSF_STEPS = 20000

    def curvature_speed_factor(self, pct: float) -> float:
        """Looked up from a table built once (it was ~40% of a simulated race's time:
        four track look-ups per car per tick). 20,000 steps round the lap, linear in
        between, so it matches the direct calculation to well under 0.1%."""
        tab = getattr(self, "_csf", None)
        if tab is None:
            M = self.CSF_STEPS
            tab = self._csf = [self._curvature_speed_factor(i / M) for i in range(M)]
        x = (pct % 1.0) * self.CSF_STEPS
        i = int(x)
        f = x - i
        return tab[i % self.CSF_STEPS] * (1.0 - f) + tab[(i + 1) % self.CSF_STEPS] * f

    def _curvature_speed_factor(self, pct: float) -> float:
        h0 = self.heading_at(pct - 0.003)
        h1 = self.heading_at(pct + 0.003)
        dh = abs((h1 - h0 + math.pi) % (2 * math.pi) - math.pi)
        # more heading change per meter -> slower. 0.55 .. 1.0
        return max(0.55, 1.0 - dh * 9.0)

    def _class_positions(self, pos):
        """Rank within each class, derived from the overall order."""
        out = [0] * 64
        counters = {}
        for _, i in sorted((pos[c.idx], c.idx) for c in self.cars if pos[c.idx]):
            cid = self.cars[i].cls["id"]
            counters[cid] = counters.get(cid, 0) + 1
            out[i] = counters[cid]
        return out

    def _qual_order(self):
        order = sorted(self.cars, key=lambda c: c.base_lap)
        seen = {}
        out = []
        for c in order:
            cp = seen.get(c.cls["id"], 0)
            seen[c.cls["id"]] = cp + 1
            out.append((cp, c))
        return out

    def _est_lap(self, i):
        # CarIdxEstTime runs against the CLASS reference lap, not the car's own.
        return self.cars[i].cls["lap"] if i < self.n_cars else 90.0

    # --- session YAML stand-in ------------------------------------------------
    def _build_session_info(self) -> dict:
        drivers = []
        for c in self.cars:
            second = getattr(c, "driver", 0) == 1
            drivers.append({
                "CarIdx": c.idx,
                "UserName": (f"Driver {c.idx:02d}" if c.idx else "Andrew") + (" (B)" if second else ""),
                "TeamName": f"Team {c.idx // 3}",
                "CarNumber": str(10 + c.idx * 3),
                "CarClassID": c.cls["id"],
                "CarClassShortName": c.cls["short"],
                "CarScreenNameShort": c.cls["name"],
                "CarClassColor": c.cls["color"],
                "CarClassRelSpeed": c.cls["rel"],
                "CarClassEstLapTime": 1.0 if getattr(self, "replay_shape", False) else c.cls["lap"],
                "CarDesignStr": ["0,ff3b30,ffffff,111111", "3,1c6dd0,f2c200,ffffff", "7,0b8f4d,ffffff,000000"][c.idx % 3],
                "HelmetDesignStr": "1,ffffff,ff3b30,111111",
                "CarID": 100 + c.cls["id"], "UserID": 300000 + c.idx + (500 if second else 0),
                "TeamID": (900 + c.idx) if self.team else 0,
                # Spread licences across classes with iRacing's own colours, so the
                # badge path is exercised rather than assumed: A blue, B green,
                # C yellow, D orange.
                "IRating": 500 + c.idx * 137,
                "LicString": ["A 3.50", "B 2.67", "C 2.13", "D 3.35"][c.idx % 4],
                "LicColor": [0x0153DB, 0x00C702, 0xFEEC04, 0xFC8A27][c.idx % 4],
                "IsSpectator": 0,
                "CarIsPaceCar": 0,
                "CurDriverIncidentCount": 0,
                "TeamIncidentCount": 0,
            })
        return {
            "WeekendInfo": {
                "TrackName": "fake_circuit",
                "TrackID": 9999,
                "TrackDisplayName": "Fake International Circuit",
                "TrackDisplayShortName": "Fake",
                "TrackConfigName": "Grand Prix",
                "TrackLength": f"{self.length / 1000:.2f} km",
                "TrackNumTurns": 11, "TrackDynamicTrack": 1, "TrackCleanup": 0,
                "TrackPitSpeedLimit": "60.00 kph",
                "TrackType": "road course",
                # as iRacing writes them: numbers with units
                "TrackLatitude": "37.000000 m", "TrackLongitude": "-79.000000 m", "TrackAltitude": "150.00 m",
                "EventType": "Race",
                "Category": "Road",
                "SessionID": 1,
                "SubSessionID": 1,
                "TeamRacing": 1 if self.team else 0,
                # iRacing writes the limit as a number, or the word "unlimited".
                "WeekendOptions": {"IncidentLimit": 17, "FastRepairsLimit": 1,
                                   "StandingStart": 0, "NumStarters": self.n_cars,
                                   "Date": "2026-06-15", "TimeOfDay": "2:00 pm",
                                   "EarthRotationSpeedupFactor": 1, "WeatherType": "Dynamic", "Skies": "Partly Cloudy"},
            },
            "DriverInfo": {
                "DriverCarIdx": 0,
                # As in the real Indianapolis session info: the compounds the
                # player's car class can run, by CarIdxTireCompound index.
                "DriverTires": [{"TireIndex": 0, "TireCompoundType": "Hard"},
                                {"TireIndex": 1, "TireCompoundType": "Wet"}],
                "DriverCarFuelMaxLtr": self.fuel_max,
                "DriverCarMaxFuelPct": 1.0,
                "DriverCarSLShiftRPM": 7600,
                "DriverCarRedLine": 8000,
                "DriverPitTrkPct": 0.985,
                "Drivers": drivers,
            },
            "SessionInfo": {
                "Sessions": [
                    # SessionTrackRubberState mirrors what iRacing really sends: the
                    # first session carries a description, later ones say "carry over"
                    # -- which describes inheritance, not condition. The client has to
                    # resolve that backwards, so the fake must reproduce it.
                    {"SessionNum": 0, "SessionType": "Practice", "SessionLaps": "unlimited",
                     "SessionTime": "1800.0000 sec", "SessionTrackRubberState": "moderate usage"},
                    {"SessionNum": 1, "SessionType": "Race", "SessionLaps": 25,
                     "SessionTime": "unlimited", "SessionTrackRubberState": "carry over"},
                ]
            },
            "CameraInfo": {"Groups": [
                {"GroupNum": n, "GroupName": name, **({"IsScenic": True} if name == "Scenic" else {}),
                 "Cameras": [{"CameraNum": k, "CameraName": f"Cam{name.replace(' ', '')}{k if k > 1 else ''}"}
                             for k in range(1, (6 if name.startswith("TV") else 2) + 1)]}
                for n, name in self.CAMERA_GROUPS]},
            # As iRacing writes it: positions from 0.
            "QualifyResultsInfo": {"Results": [
                {"Position": p, "ClassPosition": cp, "CarIdx": c.idx, "FastestLap": 3,
                 "FastestTime": round(c.base_lap - 2.0, 3)}
                for p, (cp, c) in enumerate(self._qual_order())]},
            "SplitTimeInfo": {"Sectors": [{"SectorNum": 0, "SectorStartPct": 0.0},
                                          {"SectorNum": 1, "SectorStartPct": 0.33},
                                          {"SectorNum": 2, "SectorStartPct": 0.66}]},
        }

    # --- interface ----------------------------------------------------------
    def connect(self) -> bool:
        self._connected = True
        self._last_wall = time.perf_counter()
        self._step(0.0)
        return True

    def tick(self) -> bool:
        if self.realtime:
            now = time.perf_counter()
            dt = now - self._last_wall
            if dt < 1.0 / self.HZ:
                time.sleep(1.0 / self.HZ - dt)
                now = time.perf_counter()
                dt = now - self._last_wall
            self._last_wall = now
        else:
            dt = 1.0 / self.HZ
        self._step(dt * self.speed)
        return True

    def _step(self, dt: float):
        self.t += dt
        self.frame += 1
        rng = self.rng
        # slow weather drift so the weather panel visibly changes
        # Random walk alone averages to nothing, so the fake's weather never
        # actually went anywhere and the client's trend readout could only ever
        # say "steady". Real conditions drift with the session, so add a slow
        # deterministic component on top of the noise -- roughly half a degree a
        # minute, the scale an engineer would actually react to.
        self.air += (rng.random() - 0.5) * 0.002 + math.cos(self.t / 300.0) * 0.00015
        self.track_temp += (rng.random() - 0.5) * 0.004 + math.cos(self.t / 260.0) * 0.00030
        v = {}
        pcts, laps, lapsc, onpit, surf, last, best, comp, fr, gear, rpm = ([0] * 64 for _ in range(11))
        comp = [-1] * 64                          # iRacing: -1 = not known (empty slot, not loaded)
        f2 = [0.0] * 64
        for c in self.cars:
            if c.pit_phase == "box":
                spd = 0.0
                if self.t >= c.box_until:
                    c.pit_phase = "lane_out"
            elif c.pit_phase is not None:
                spd = self.PIT_LANE_MS
            elif self.t < self.grid_s:
                spd = 0.0                       # sitting on the grid, on track, still
            else:
                spd = (self.length / c.base_lap) * self.curvature_speed_factor(c.pct) / 0.83
                spd += math.sin(self.t * 0.5 + c.idx) * 0.8
                if c.compound == 1:
                    spd *= 0.96                 # the other compound: ~4 % off in the dry
                if self.t < self.parade_s and self.grid_s:
                    spd *= 0.4                  # behind the pace car
                if self._in_caution():
                    spd *= 0.45                 # under caution, behind the pace car
                eff = self.inc_effect.get(c.idx)
                if eff and self.t < eff["until"]:
                    spd *= -0.25 if self.t < eff.get("back_until", 0) else eff["speed"]
            c.v = abs(spd)                      # what the car is really doing (Speed is a magnitude)
            new_pct = max(0.0, c.pct + spd * dt / self.length)
            if c.pit_phase is None and c.pit_planned and c.pct < self.PIT_IN_PCT <= new_pct:
                c.pit_phase, c.pit_planned = "lane_in", False
            if new_pct >= 1.0:
                new_pct -= 1.0
                lap_time = self.t - c.lap_start_t
                c.lap_start_t = self.t
                # Cars spawn at random points on the lap, so the FIRST crossing
                # always completes a partial lap. The old escape hatch (or
                # lap_time > 60) let a car starting near half distance bank a 63 s
                # "lap" it never drove, which then stood as its best -- 65 s bests
                # against 115 s laps. A lap only counts once a full one has been
                # run.
                if c.laps_completed >= 1:
                    c.last_lap = lap_time
                    c.best_lap = lap_time if c.best_lap == 0 else min(c.best_lap, lap_time)
                c.laps_completed += 1
                if c.pit_phase == "lane_in":
                    # The boxes are on the line: stop there, stationary.
                    new_pct = 0.0
                    c.pit_phase = "box"
                    c.box_until = self.t + 18.0 + rng.random() * 22.0
                    c.compound = (c.compound + 1) % 2
                    if c.idx == 0:
                        # Fuel goes in at a rate, as in the sim: 2.60 L/s in the
                        # real Indianapolis stop. The stop lasts at least as long.
                        self.fuel_target = min(self.fuel_max, 60.0)
                        c.box_until = max(c.box_until, self.t + (self.fuel_target - self.fuel) / 2.6 + 1.0)
                    if self.team:
                        c.driver = 1 - getattr(c, "driver", 0)
                        self._session = self._build_session_info()
                        self._siu += 1
                # occasional stop on the NEXT lap for a non-player car
                if c.idx != 0 and rng.random() < 0.08 and c.laps_completed > 2:
                    c.pit_planned = True
                if c.idx == 0 and self.player_pit_lap is not None and c.laps_completed == self.player_pit_lap - 1:
                    c.pit_planned = True
            if c.pit_phase == "lane_out" and self.PIT_OUT_PCT <= new_pct < 0.5:
                c.pit_phase = None
            c.on_pit = c.pit_phase is not None
            c.pct = new_pct
            c.rpm = 5200 + 2800 * self.curvature_speed_factor(c.pct)
            c.gear = 2 + int(4 * self.curvature_speed_factor(c.pct))
            i = c.idx
            pcts[i] = c.pct
            laps[i] = c.laps_completed + 1
            lapsc[i] = c.laps_completed
            onpit[i] = c.on_pit
            surf[i] = 1 if c.pit_phase == "box" else 2 if c.on_pit else 3
            last[i] = c.last_lap
            best[i] = c.best_lap
            # As in the sim at the start of a session: every car's compound reads -1
            # until that car has loaded (staggered over the first ~10 s), and one car
            # drops back to -1 for a moment. None of that is a tyre change.
            loading = self.t < 1.0 + (i * 0.37) % 9.0 or (i == 3 and 12.0 <= self.t < 13.0)
            comp[i] = -1 if loading else c.compound
            fr[i] = c.fast_repairs
            gear[i] = c.gear
            rpm[i] = c.rpm
        if self.incidents_on:
            self._incidents_step(dt, surf)
        for i in range(self.n_cars, 64):
            surf[i] = -1
            pcts[i] = -1.0
        # positions: total distance
        order = sorted(self.cars, key=lambda c: c.laps_completed + c.pct, reverse=True)
        pos = [0] * 64
        leader_dist = order[0].laps_completed + order[0].pct
        for p, c in enumerate(order, start=1):
            pos[c.idx] = p
            if c.last_lap > 0:
                gap_laps = leader_dist - (c.laps_completed + c.pct)
                # Gap is time behind the LEADER, so it scales with the leader's
                # lap time. Using each car's own base_lap made the series
                # non-monotonic with position and produced negative intervals.
                f2[c.idx] = gap_laps * order[0].base_lap  # approximate seconds
            else:
                # Match iRacing: with no completed lap there is no gap to report,
                # and F2Time carries a placeholder of 0.001 * (position - 1).
                # Verified against a live replay (Daytona, GR86 race, lap 1).
                f2[c.idx] = round(0.001 * (p - 1), 4)
        player = self.cars[0]
        if self.spectator:
            # Kinematics follow the camera car; there is no car of our own.
            player = self.cars[self.cam["car"]] if self.cam["car"] < self.n_cars else self.cars[0]
        self._pos = pos
        # Speed is the speed the car actually moved at this step. It used to be
        # recomputed from a formula without the pace wobble and the compound
        # penalty, so the fake's Speed disagreed with its own positions.
        speed = getattr(player, "v", 0.0)
        heading = self.heading_at(player.pct)
        fuel_rate_kg_h = 95.0 * (0.4 + 0.6 * self.curvature_speed_factor(player.pct))
        # At the limiter the engine barely works, and stationary it idles: the
        # real Indianapolis in-lap burned 2.21 L against 2.44-2.51 on a racing
        # lap. A fake that burned full rate in the lane would hide that.
        if player.pit_phase == "box":
            fuel_rate_kg_h = 2.0
        elif player.on_pit:
            fuel_rate_kg_h *= 0.35
        # iRacing's spotter (irsdk_CarLeftRight). The fake has no lateral
        # positions, so a car within 5 m along the track is put on the left or
        # the right by its index; what matters is that every value can occur.
        left = right = 0
        for c in self.cars[1:]:
            if c.on_pit or player.on_pit:
                continue
            dm = ((c.pct - player.pct + 0.5) % 1.0 - 0.5) * self.length
            if abs(dm) < 5.0:
                if c.idx % 2:
                    right += 1
                else:
                    left += 1
        car_lr = (1 if not (left or right) else 4 if left and right else
                  5 if left >= 2 else 6 if right >= 2 else 2 if left else 3)
        self.fuel -= (fuel_rate_kg_h / 3600.0) * dt / 0.75  # kg/h -> l/s with ~0.75 kg/l
        if self.cars[0].pit_phase == "box" and getattr(self, "fuel_target", None) is not None:
            self.fuel = min(self.fuel_target, self.fuel + 2.6 * dt)
        x, y = self.pos_at(player.pct)
        v.update({
            "SessionTime": self.t, "SessionNum": 1,
            "SessionState": 2 if self.t < self.grid_s else 3 if self.t < self.parade_s else 4,
            "SessionFlags": self._flags(), "PitsOpen": self._pits_open(), "SessionTimeRemain": 604800.0,
            "SessionLapsRemainEx": max(0, 25 - player.laps_completed),
            "SessionTimeOfDay": 14 * 3600 + self.t, "PlayerCarIdx": 0,
            "IsReplayPlaying": False, "ReplayFrameNum": self.frame,
            "IsOnTrack": True, "IsOnTrackCar": True, "IsInGarage": False,
            "AirTemp": self.air, "TrackTemp": self.track_temp, "TrackTempCrew": self.track_temp,
            "AirPressure": 101325.0, "AirDensity": 1.19, "WindVel": 3.2, "WindDir": 1.1,
            "RelativeHumidity": 0.48, "Skies": 1, "FogLevel": 0.0, "Precipitation": self.precip,
            "TrackWetness": self.wetness, "WeatherDeclaredWet": False,
            # the sun as it really moves at 37 N in mid June, solar noon at 13:00 local
            "SolarAltitude": self._sun_alt(14 * 3600 + self.t), "SolarAzimuth": 3.4,
            "Speed": speed, "RPM": player.rpm, "Gear": player.gear,
            "Throttle": min(1.0, self.curvature_speed_factor(player.pct) * 1.1),
            "Brake": max(0.0, 0.9 - self.curvature_speed_factor(player.pct)) * 1.5,
            "Clutch": 1.0, "SteeringWheelAngle": math.sin(self.t) * 0.4,
            "FuelLevel": max(0.0, self.fuel), "FuelLevelPct": max(0.0, self.fuel) / self.fuel_max,
            "FuelUsePerHour": fuel_rate_kg_h,
            "WaterTemp": 88.0 + math.sin(self.t / 30) * 2, "OilTemp": 104.0, "OilPress": 4.2, "Voltage": 13.8,
            "EngineWarnings": 0,
            "Lap": player.laps_completed + 1, "LapCompleted": player.laps_completed,
            "LapDistPct": player.pct, "LapDist": player.pct * self.length,
            "LapLastLapTime": player.last_lap, "LapBestLapTime": player.best_lap,
            "LapCurrentLapTime": self.t - player.lap_start_t,
            "LapDeltaToBestLap": math.sin(self.t / 7) * 0.3, "LapDeltaToSessionBestLap": 0.4,
            "OnPitRoad": player.on_pit, "PlayerCarInPitStall": player.pit_phase == "box",
            "CarLeftRight": car_lr,
            "PlayerCarPosition": pos[0], "PlayerCarClassPosition": pos[0],
            "PlayerCarMyIncidentCount": 2, "PlayerCarTeamIncidentCount": 2, "PlayerCarDriverIncidentCount": 2,
            "PlayerCarTowTime": 0.0,
            "PlayerTrackSurface": 1 if player.pit_phase == "box" else 2 if player.on_pit else 3,
            "PitRepairLeft": 0.0, "PitOptRepairLeft": 0.0,
            "PitSvFlags": 0x1F, "PitSvFuel": 45.0, "PitSvLFP": 165.0, "PitSvRFP": 165.0,
            "PitSvLRP": 160.0, "PitSvRRP": 160.0, "PitSvTireCompound": 0, "PitstopActive": False,
            "dcBrakeBias": 54.5, "dcABS": 3.0, "dcTractionControl": 5.0,
            "dcAntiRollFront": 2.0, "dcAntiRollRear": 3.0, "dcFuelMixture": 2.0,
            "VelocityX": speed, "VelocityY": 0.0, "YawNorth": heading,
            "Lat": 44.9 + y / 111320.0, "Lon": -92.9 + x / (111320.0 * math.cos(math.radians(44.9))),
            "LatAccel": 0.0, "LongAccel": 0.0,
            "CarIdxLap": laps, "CarIdxLapCompleted": lapsc, "CarIdxLapDistPct": pcts,
            "CarIdxPosition": pos, "CarIdxClassPosition": self._class_positions(pos),
            "CarIdxClass": [self.cars[i].cls["id"] if i < self.n_cars else 0 for i in range(64)],
            "CarIdxF2Time": f2, "CarIdxEstTime": [p * self._est_lap(i) if p >= 0 else 0.0 for i, p in enumerate(pcts)],
            "CarIdxOnPitRoad": onpit, "CarIdxTrackSurface": surf,
            "CarIdxLastLapTime": last, "CarIdxBestLapTime": best, "CarIdxBestLapNum": lapsc,
            "CarIdxTireCompound": comp, "CarIdxSessionFlags": [0] * 64,
            "CarIdxFastRepairsUsed": fr, "CarIdxP2P_Status": [False] * 64, "CarIdxP2P_Count": [0] * 64,
            "CarIdxGear": gear, "CarIdxRPM": rpm, "CarIdxSteer": [0.0] * 64, "CarIdxPaceFlags": [0] * 64,
            "CamCarIdx": self.cam["car"] if self.spectator else 0,
            "CamGroupNumber": self.cam["group"], "CamCameraNumber": self.cam["num"],
            "CamCameraState": 0x0051 if self.spectator else 0x0000,
            "ReplayPlaySpeed": self.replay_speed, "ReplayPlaySlowMotion": self.replay_slow,
            "ReplaySessionNum": 1, "ReplaySessionTime": self.t - self.replay_behind, "ReplayFrameNumEnd": 1,
        })
        if self.replay_shape:
            v.update({"SessionFlags": 0, "PitsOpen": True, "IsReplayPlaying": True,
                      "CarIdxEstTime": [0.0] * 64, "CarIdxSessionFlags": [0] * 64})
        if self.spectator:
            v.update({"PlayerCarIdx": 63, "IsReplayPlaying": True, "IsInGarage": False})
            for k in ("FuelLevel", "FuelLevelPct", "FuelUsePerHour", "PlayerCarPosition",
                      "PlayerCarClassPosition", "PlayerCarMyIncidentCount", "PlayerCarTeamIncidentCount",
                      "PlayerCarDriverIncidentCount", "PitSvFlags", "PitSvFuel", "dcBrakeBias", "dcABS",
                      "dcTractionControl", "dcAntiRollFront", "dcAntiRollRear", "dcFuelMixture",
                      "LapLastLapTime", "LapBestLapTime", "LapCurrentLapTime", "LapDeltaToBestLap",
                      "LapDeltaToSessionBestLap", "PlayerCarInPitStall"):
                v[k] = None
            self._vars = v
            return
        for c in ("LF", "RF", "LR", "RR"):
            for p, base in (("CL", 84), ("CM", 88), ("CR", 86)):
                v[f"{c}temp{p}"] = base + math.sin(self.t / 20 + hash(c) % 7) * 4
            for p in ("L", "M", "R"):
                v[f"{c}wear{p}"] = 0.97  # only changes at pit stops in the real sim
            v[f"{c}coldPressure"] = 165.0
        self._vars = v

    def get(self, name: str) -> Any:
        return self._vars.get(name)

    # Kinds of incident the fake field has:
    # (name, points on pavement, speed factor, seconds, seconds off track,
    #  takes a second car with it, seconds rolling backwards first)
    FAKE_INCIDENTS = [
        ("wheels_off", 1, 0.85, 1.5, 1.5, False, 0.0),
        ("spin", 2, 0.05, 3.0, 1.2, False, 0.8),
        ("wall", 2, 0.30, 2.5, 0.0, False, 0.0),
        ("heavy_contact", 4, 0.20, 2.5, 0.0, True, 0.0),
        ("light_contact", 0, 0.80, 0.6, 0.0, True, 0.0),
    ]
    FAKE_INCIDENT_WEIGHTS = [45, 20, 10, 10, 15]

    def _incidents_step(self, dt: float, surf: list):
        r = self.inc_rng
        # start one now and then, on a car that is racing
        if dt > 0 and self.t > max(20, self.parade_s + 20) and r.random() < dt / self.incident_every_s:
            cands = [c for c in self.cars if c.pit_phase is None and c.idx not in self.inc_effect]
            if cands:
                name, pts, speed, dur, off, pair, back = r.choices(self.FAKE_INCIDENTS, self.FAKE_INCIDENT_WEIGHTS)[0]
                if pair:
                    # contact needs two cars together: start from a car that has one
                    def has_neighbour(x):
                        return any(o is not x and abs((x.laps_completed + x.pct) - (o.laps_completed + o.pct)) < 0.003
                                   for o in cands)
                    cands = [x for x in cands if has_neighbour(x)]
                    if not cands:
                        return
                c = r.choice(cands)
                who = [c]
                if pair:
                    # the car closest behind it on the road, if it is close enough to touch
                    def behind(o):
                        d = abs((c.laps_completed + c.pct) - (o.laps_completed + o.pct))
                        return d if d < 0.003 else None
                    near = [(behind(o), o) for o in cands if o is not c and behind(o) is not None]
                    if not near:
                        return
                    who.append(min(near, key=lambda x: x[0])[1])
                for car in who:
                    self.inc_effect[car.idx] = {"until": self.t + dur, "speed": speed, "off_until": self.t + off,
                                                "back_until": self.t + back}
                    self.inc_log.append({"t": self.t, "idx": car.idx, "kind": name, "points": pts,
                                         "with": [o.idx for o in who if o is not car]})
                    if pts:
                        p = self.inc_pending.get(car.idx)
                        if p:   # concurrent with one still pending: only the most serious counts
                            p["points"] = max(p["points"], pts)
                        else:
                            self.inc_pending[car.idx] = {"at": self.t + 1.5 + r.random() * 4.5, "points": pts}
        for i, eff in list(self.inc_effect.items()):
            if self.t < eff["off_until"] and surf[i] == 3:
                surf[i] = 0                       # off_track
            if self.t >= eff["until"] and self.t >= eff["off_until"]:
                del self.inc_effect[i]
        # The session info catches up a few seconds later -- unless counts are
        # hidden, as they are from a spectator during a race (observed live:
        # every count arrived only after the finish).
        due = [i for i, p in self.inc_pending.items() if self.t >= p["at"]]
        if due and self.hide_incident_counts:
            for i in due:
                self.inc_hidden[i] = self.inc_hidden.get(i, 0) + self.inc_pending.pop(i)["points"]
            return
        if due:
            drivers = {d["CarIdx"]: d for d in self._session["DriverInfo"]["Drivers"]}
            for i in due:
                p = self.inc_pending.pop(i)
                d = drivers[i]
                d["TeamIncidentCount"] += p["points"]
                d["CurDriverIncidentCount"] += p["points"]
            self._siu += 1

    def publish_incident_counts(self):
        """What iRacing does at the finish: every count appears at once."""
        drivers = {d["CarIdx"]: d for d in self._session["DriverInfo"]["Drivers"]}
        for i, p in self.inc_pending.items():
            self.inc_hidden[i] = self.inc_hidden.get(i, 0) + p["points"]
        for i, pts in self.inc_hidden.items():
            drivers[i]["TeamIncidentCount"] += pts
            drivers[i]["CurDriverIncidentCount"] += pts
        self.inc_pending.clear()
        self.inc_hidden.clear()
        self.hide_incident_counts = False
        self._siu += 1

    # A stand-in for iRacing's camera so the producer panel can be tried without
    # the sim. commands.py calls it in place of pyirsdk and marks the result
    # "simulated"; it is never reported as reaching iRacing. Unknown car numbers
    # raise here, where the real sim would silently ignore them.
    def simulate_broadcast(self, method: str, args: tuple):
        if method in ("cam_switch_num", "cam_switch_pos"):
            who, group, cam = args
            car = None
            if method == "cam_switch_pos":
                car = next((c.idx for c in self.cars if self._pos[c.idx] == who), None)
            elif isinstance(who, int) and who < 0:
                if who == -2:        # leader
                    car = next((c.idx for c in self.cars if self._pos[c.idx] == 1), None)
                else:                # most exciting / incident: iRacing's choice; the fake keeps the car
                    car = self.cam["car"]
            else:
                drivers = self._session["DriverInfo"]["Drivers"]
                car = next((d["CarIdx"] for d in drivers if d["CarNumber"] == str(who)), None)
            if car is None:
                raise ValueError(f"no car for {method} {who!r} in the fake field")
            if group and group not in {n for n, _ in self.CAMERA_GROUPS}:
                raise ValueError(f"no camera group {group}")
            self.cam = {"car": car, "group": group or self.cam["group"], "num": cam or 1}
        elif method == "replay_set_play_speed":
            self.replay_speed, self.replay_slow = args[0], bool(args[1])
        elif method == "replay_search":
            if args[0] == "to_end":
                self.replay_speed, self.replay_slow = 1, False
                self.replay_behind = 0.0
        elif method == "replay_search_session_time":
            # The fake cannot rewind its field; it moves the playhead clock only,
            # which is what the live-edge logic reads.
            session, ms = args
            self.replay_behind = max(0.0, self.t - ms / 1000.0)
        else:
            raise ValueError(f"fake source cannot simulate {method}")

    def var_headers(self) -> list[dict]:
        return list(self._headers)

    def session_info(self, key: str) -> Any:
        return self._session.get(key)

    @property
    def session_info_update(self) -> int:
        return self._siu

    @property
    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        self._connected = False


class ReplaySource(TelemetrySource):
    """Replays a `pitwall record` capture through the normal pipeline.

    Exists because the only way to exercise the client against a real race was to
    be in one. Every recording is a full frame-by-frame capture of every variable,
    so it can drive the bridge exactly as the sim would -- which makes captured
    sessions into repeatable fixtures for the whole stack, not just for offline
    analysis in Python.

    realtime=True paces playback by the recording's own wall clock; False runs as
    fast as the consumer can take it. `loop` restarts at the end, which is handy
    for leaving a session up on screen.
    """

    name = "replay"

    def __init__(self, path: str, realtime: bool = True, speed: float = 1.0, loop: bool = False):
        self.path = path
        self.realtime = realtime
        self.speed = max(0.01, speed)
        self.loop = loop
        self._records: list[dict] = []
        self._headers: list[dict] = []
        self._i = 0
        self._vars: dict[str, Any] = {}
        self._info: dict[str, Any] = {}
        self._update = 0
        self._connected = False
        self._t0 = None
        self._wall0 = None
        self.restarts = 0        # bumped on each loop, so consumers can reset history

    def connect(self) -> bool:
        from .recorder import iter_recording
        self._records = []
        for rec in iter_recording(self.path):
            if rec.get("type") == "header":
                self._headers = rec.get("vars") or []
            else:
                self._records.append(rec)
        self._i = 0
        self._connected = bool(self._records)
        return self._connected

    def _apply(self, rec: dict) -> bool:
        """Returns True if this record produced a new frame of variables."""
        if rec.get("type") == "session":
            self._info = rec.get("info") or {}
            self._update = rec.get("update", self._update)
            return False
        if rec.get("type") == "tick":
            self._vars = rec.get("vars") or {}
            return True
        return False

    def tick(self) -> bool:
        while True:
            if self._i >= len(self._records):
                if not self.loop:
                    self._connected = False
                    return False
                # Looping replays the same session, so anything accumulated across
                # it -- stop counts, stint history, gap trends -- would be double
                # counted. Signal the wrap so the consumer can start clean.
                self._i = 0
                self._t0 = self._wall0 = None
                self.restarts += 1
            rec = self._records[self._i]
            self._i += 1
            got = self._apply(rec)
            if not got:
                continue
            if self.realtime:
                wall = rec.get("wall")
                if wall is not None:
                    if self._t0 is None:
                        self._t0, self._wall0 = wall, time.time()
                    else:
                        due = self._wall0 + (wall - self._t0) / self.speed
                        delay = due - time.time()
                        if delay > 0:
                            time.sleep(min(delay, 1.0))
            return True

    def get(self, name: str) -> Any:
        return self._vars.get(name)

    def var_headers(self) -> list[dict]:
        return self._headers

    def session_info(self, key: str) -> Any:
        return self._info.get(key)

    @property
    def session_info_update(self) -> int:
        return self._update

    @property
    def is_connected(self) -> bool:
        return self._connected

    def close(self) -> None:
        self._records = []
        self._connected = False


def make_source(kind: str, path: str | None = None, **kw) -> TelemetrySource:
    if kind == "live":
        return LiveSource()
    if kind == "dump":
        if not path:
            raise ValueError("dump source needs a path")
        return DumpSource(path)
    if kind == "fake":
        return FakeSource(**kw)
    if kind == "replay":
        if not path:
            raise ValueError("replay source needs a path to a .jsonl.gz recording")
        return ReplaySource(path, **kw)
    raise ValueError(f"unknown source kind {kind!r}")
