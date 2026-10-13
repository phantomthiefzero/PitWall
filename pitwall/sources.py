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

    def __init__(self, seed: int = 7, n_cars: int = 24, realtime: bool = True,
                 speed: float = 1.0, player_pit_lap: int | None = None):
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

    def curvature_speed_factor(self, pct: float) -> float:
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

    # --- session YAML stand-in ------------------------------------------------
    def _build_session_info(self) -> dict:
        drivers = []
        for c in self.cars:
            drivers.append({
                "CarIdx": c.idx,
                "UserName": f"Driver {c.idx:02d}" if c.idx else "Andrew",
                "TeamName": f"Team {c.idx // 3}",
                "CarNumber": str(10 + c.idx * 3),
                "CarClassID": c.cls["id"],
                "CarClassShortName": c.cls["short"],
                "CarScreenNameShort": c.cls["name"],
                "CarClassColor": c.cls["color"],
                "CarClassRelSpeed": c.cls["rel"],
                # Spread licences across classes with iRacing's own colours, so the
                # badge path is exercised rather than assumed: A blue, B green,
                # C yellow, D orange.
                "IRating": 500 + c.idx * 137,
                "LicString": ["A 3.50", "B 2.67", "C 2.13", "D 3.35"][c.idx % 4],
                "LicColor": [0x0153DB, 0x00C702, 0xFEEC04, 0xFC8A27][c.idx % 4],
                "IsSpectator": 0,
                "CarIsPaceCar": 0,
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
                "EventType": "Race",
                "Category": "Road",
                "SessionID": 1,
                "SubSessionID": 1,
                "TeamRacing": 0,
                # iRacing writes the limit as a number, or the word "unlimited".
                "WeekendOptions": {"IncidentLimit": 17, "FastRepairsLimit": 1,
                                   "StandingStart": 0, "NumStarters": self.n_cars},
            },
            "DriverInfo": {
                "DriverCarIdx": 0,
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
        f2 = [0.0] * 64
        for c in self.cars:
            if c.pit_phase == "box":
                spd = 0.0
                if self.t >= c.box_until:
                    c.pit_phase = "lane_out"
            elif c.pit_phase is not None:
                spd = self.PIT_LANE_MS
            else:
                spd = (self.length / c.base_lap) * self.curvature_speed_factor(c.pct) / 0.83
                spd += math.sin(self.t * 0.5 + c.idx) * 0.8
            new_pct = c.pct + spd * dt / self.length
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
                        self.fuel = min(self.fuel_max, 60.0)
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
            comp[i] = c.compound
            fr[i] = c.fast_repairs
            gear[i] = c.gear
            rpm[i] = c.rpm
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
        speed = (0.0 if player.pit_phase == "box" else self.PIT_LANE_MS if player.on_pit
                 else (self.length / player.base_lap) * self.curvature_speed_factor(player.pct) / 0.83)
        heading = self.heading_at(player.pct)
        fuel_rate_kg_h = 95.0 * (0.4 + 0.6 * self.curvature_speed_factor(player.pct))
        self.fuel -= (fuel_rate_kg_h / 3600.0) * dt / 0.75  # kg/h -> l/s with ~0.75 kg/l
        x, y = self.pos_at(player.pct)
        v.update({
            "SessionTime": self.t, "SessionNum": 1, "SessionState": 4,
            "SessionFlags": 0x0004, "SessionTimeRemain": 604800.0,
            "SessionLapsRemainEx": max(0, 25 - player.laps_completed),
            "SessionTimeOfDay": 14 * 3600 + self.t, "PlayerCarIdx": 0,
            "IsReplayPlaying": False, "ReplayFrameNum": self.frame,
            "IsOnTrack": True, "IsOnTrackCar": True, "IsInGarage": False,
            "AirTemp": self.air, "TrackTemp": self.track_temp, "TrackTempCrew": self.track_temp,
            "AirPressure": 101325.0, "AirDensity": 1.19, "WindVel": 3.2, "WindDir": 1.1,
            "RelativeHumidity": 0.48, "Skies": 1, "FogLevel": 0.0, "Precipitation": self.precip,
            "TrackWetness": self.wetness, "WeatherDeclaredWet": False,
            "SolarAltitude": 0.9, "SolarAzimuth": 3.4,
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
            "CarIdxF2Time": f2, "CarIdxEstTime": [p * 90.0 if p >= 0 else 0.0 for p in pcts],
            "CarIdxOnPitRoad": onpit, "CarIdxTrackSurface": surf,
            "CarIdxLastLapTime": last, "CarIdxBestLapTime": best, "CarIdxBestLapNum": lapsc,
            "CarIdxTireCompound": comp, "CarIdxSessionFlags": [0] * 64,
            "CarIdxFastRepairsUsed": fr, "CarIdxP2P_Status": [False] * 64, "CarIdxP2P_Count": [0] * 64,
            "CarIdxGear": gear, "CarIdxRPM": rpm, "CarIdxSteer": [0.0] * 64, "CarIdxPaceFlags": [0] * 64,
        })
        for c in ("LF", "RF", "LR", "RR"):
            for p, base in (("CL", 84), ("CM", 88), ("CR", 86)):
                v[f"{c}temp{p}"] = base + math.sin(self.t / 20 + hash(c) % 7) * 4
            for p in ("L", "M", "R"):
                v[f"{c}wear{p}"] = 0.97  # only changes at pit stops in the real sim
            v[f"{c}coldPressure"] = 165.0
        self._vars = v

    def get(self, name: str) -> Any:
        return self._vars.get(name)

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
