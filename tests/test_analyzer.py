"""
Lap Analyzer logic tests. The analyzer is one HTML page with its code inline
(suite/web/analyzer/index.html); tests/analyzer_harness.js runs that code in Node
with a stand-in page, so the same functions the app runs are tested here on
synthetic .ibt files whose answers are known.

The synthetic sessions copy the awkward shapes seen in a real Indianapolis file
(2026-10-10): the lap counter going backwards (0 -> 1 -> 0, 34 -> 33), joining
mid-lap, in and out laps, a refuel, incidents. Skipped when Node is not installed.

Run:  python -m pytest tests -q
"""
import json
import os
import shutil
import struct
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGE = os.path.join(ROOT, "suite", "web", "analyzer", "index.html")
HARNESS = os.path.join(ROOT, "tests", "analyzer_harness.js")
NODE = shutil.which("node")
HZ = 60

TYPES = {1: ("<B", 1), 2: ("<i", 4), 4: ("<f", 4), 5: ("<d", 8)}


def write_ibt(path, yaml, channels, rows):
    """channels: [(name, type, unit)]; rows: list of dicts (missing names are 0)."""
    sizes = [TYPES[t][1] for _, t, _ in channels]
    offs = [sum(sizes[:i]) for i in range(len(channels))]
    bl = sum(sizes)
    nv, vho = len(channels), 144
    so = vho + nv * 144
    session = yaml.encode("latin-1") + b"\x00"
    bo = so + len(session)
    hdr = bytearray(144)
    struct.pack_into("<iiiiiiiiii", hdr, 0, 2, 1, HZ, 1, len(session), so, nv, vho, 1, bl)
    struct.pack_into("<ii", hdr, 48, len(rows), bo)
    vh = bytearray()
    for (name, t, unit), off in zip(channels, offs):
        h = bytearray(144)
        struct.pack_into("<iiii", h, 0, t, off, 1, 0)
        h[16:16 + len(name)] = name.encode()
        h[112:112 + len(unit)] = unit.encode()
        vh += h
    data = bytearray()
    fmt = "<" + "".join(TYPES[t][0][1] for _, t, _ in channels)
    for r in rows:
        data += struct.pack(fmt, *[r.get(n, 0) for n, _, _ in channels])
    with open(path, "wb") as f:
        f.write(hdr + vh + session + data)


# A real .ibt for checks against iRacing's own numbers (not in the repo; skipped when absent).
REAL_IBT = os.environ.get("PITWALL_REAL_IBT", "/mnt/user-data/uploads/ferrari296gt3_indianapolis_2022_road_2026-10-10_20-47-23.ibt")

def run_js(test_js, *files):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(test_js)
        script = f.name
    try:
        out = subprocess.run([NODE, HARNESS, PAGE, script, *files], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=120)   # Windows defaults to cp1252
    finally:
        os.remove(script)
    if out.returncode != 0:
        raise AssertionError("node failed:\n" + out.stderr[-3000:])
    lines = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
    return json.loads(lines[-1])


SESSION_YAML = """WeekendInfo:
 TrackDisplayName: Test Raceway
 TrackLength: 2.00 km
DriverInfo:
 DriverCarIdx: 0
 DriverCarFuelKgPerLtr: 0.75
 Drivers:
 - CarIdx: 0
   UserName: Test Driver
   CarPath: testcar
   CarScreenName: Test Car GT3
SplitTimeInfo:
 Sectors:
 - SectorNum: 0
   SectorStartPct: 0.000000
 - SectorNum: 1
   SectorStartPct: 0.300000
 - SectorNum: 2
   SectorStartPct: 0.600000
CarSetup:
 UpdateCount: 1
"""

# A team event as the recorder's file has it: the car's entry names the teammate who was in the car
# when the file was written; the recorder is only known by DriverUserID.
TEAM_YAML = SESSION_YAML.replace("DriverInfo:\n DriverCarIdx: 0", "WeekendInfo2:\n TeamRacing: 1\nDriverInfo:\n DriverCarIdx: 0\n DriverUserID: 222").replace(
    "   UserName: Test Driver\n", "   UserName: Team Mate\n   AbbrevName: Mate, T\n   Initials: TM\n   UserID: 111\n   TeamName: Test Team\n")

CHANNELS = [("SessionTime", 5, "s"), ("Lap", 2, ""), ("LapDistPct", 4, "%"), ("Speed", 4, "m/s"),
            ("Brake", 4, "%"), ("Throttle", 4, "%"), ("SteeringWheelAngle", 4, "rad"), ("Gear", 2, ""),
            ("LapLastLapTime", 4, "s"), ("LapCurrentLapTime", 4, "s"), ("OnPitRoad", 1, ""),
            ("FuelLevel", 4, "l"), ("PlayerCarMyIncidentCount", 2, ""), ("TrackTempCrew", 4, "C"),
            ("LFpressure", 4, "kPa"), ("RFpressure", 4, "kPa"), ("LRpressure", 4, "kPa"), ("RRpressure", 4, "kPa"),
            ("LFtempM", 4, "C")]


def sector_times(k):
    """Lap k's three sector times. They differ lap to lap so the best sectors
    come from different laps and the theoretical best beats every real lap."""
    return [6.0 + 0.10 * ((k * 3) % 4), 7.0 + 0.08 * ((k * 5) % 3), 8.0 + 0.12 * ((k * 7) % 5)]


def build_session(path, team=False):
    """Grid lap 0 in the pits, a 5 s 'lap 1' then the counter back to 0 (as seen),
    timed laps 1-6, lap 7 an in-lap, lap 8 an out-lap with a refuel, lap 9 timed,
    then 10 for a moment and back to 9 in the pits. Incident +2 on lap 4."""
    rows, t, fuel, inc, last = [], 0.0, 40.0, 0, 0.0
    bounds = [0.0, 0.3, 0.6, 1.0]
    plan = []                                   # (lap number, kind)
    plan.append((0, "grid"))
    plan.append((1, "blip"))
    plan.append((0, "rejoin"))
    for k in range(1, 7):
        plan.append((k, "lap"))
    plan.append((7, "inlap"))
    plan.append((8, "outlap"))
    plan.append((9, "lap"))
    plan.append((10, "end"))                    # the real file ran 33 -> 34 -> 33 in the pits
    plan.append((9, "end"))
    expected = {}
    for lapno, kind in plan:
        if kind in ("grid", "blip", "end"):
            n = 300 if kind != "grid" else 600
            for i in range(n):
                rows.append({"SessionTime": t, "Lap": lapno, "LapDistPct": 0.001 * i / n, "Speed": 2.0,
                             "OnPitRoad": 1, "FuelLevel": fuel, "PlayerCarMyIncidentCount": inc,
                             "TrackTempCrew": 30.0, "LapLastLapTime": last})
                t += 1 / HZ
            continue
        st = sector_times(lapno)
        if kind == "inlap":
            st = [s + 2 for s in st]
        if kind == "outlap":
            st = [s + 6 for s in st]
        start_pct = 0.5 if kind == "rejoin" else 0.0
        lap_t = sum(st)
        # time at which each pct boundary is reached
        tb = [0.0, st[0], st[0] + st[1], lap_t]
        n = int(round(lap_t * HZ))
        lap_fuel = 0.25
        for i in range(n):
            tt = i / HZ
            seg = 0 if tt < tb[1] else 1 if tt < tb[2] else 2
            p = bounds[seg] + (tt - tb[seg]) / (tb[seg + 1] - tb[seg]) * (bounds[seg + 1] - bounds[seg])
            if kind == "rejoin":
                p = start_pct + p * (1 - start_pct)
            pit = (kind == "inlap" and p > 0.9) or (kind == "outlap" and p < 0.1)
            if kind == "outlap" and abs(p - 0.05) < 0.01:
                fuel = 40.0                     # refuel in the stall
            if kind == "lap" and lapno == 4 and abs(p - 0.5) < 1e-3 and inc == 0:
                inc = 2
            rows.append({"IsOnTrack": 0 if team and lapno in (3, 4) else 1, "IsOnTrackCar": 1,
                         "SessionTime": t, "Lap": lapno, "LapDistPct": p, "Speed": 50.0, "Throttle": 1.0, "Gear": 4,
                         "OnPitRoad": 1 if pit else 0, "FuelLevel": fuel - lap_fuel * i / n,
                         "PlayerCarMyIncidentCount": inc, "TrackTempCrew": 30.0 - 0.1 * lapno,
                         "LapCurrentLapTime": tt, "LapLastLapTime": last,
                         "LFpressure": 170 + lapno, "RFpressure": 168 + lapno, "LRpressure": 165, "RRpressure": 164,
                         "LFtempM": 80.0})
            t += 1 / HZ
        fuel -= lap_fuel
        # iRacing reports the lap time a moment after the line; the analyzer matches
        # LapLastLapTime against the lap's own span, so report the true span.
        last = n / HZ if kind != "rejoin" else last
        expected.setdefault("laps", []).append((lapno, kind, n / HZ, st))
    if team:
        write_ibt(path, TEAM_YAML, CHANNELS + [("IsOnTrack", 1, ""), ("IsOnTrackCar", 1, "")], rows)
    else:
        write_ibt(path, SESSION_YAML, CHANNELS, rows)
    return expected


@unittest.skipUnless(NODE, "Node.js is not installed")
class TeamSessionTest(unittest.TestCase):
    """A team event: laps 3 and 4 were driven by a teammate (IsOnTrack 0, the car still running), and
    the session info names the teammate. Only the recorder's laps are theirs, under their own name."""

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "team.ibt")
        build_session(cls.path, team=True)
        cls.res = run_js("""
const ib = ibt(readBytes(ARGS[0])), S = sessionStats(ib);
const named = ibtWho('DriverInfo:\\n DriverUserID: 222\\n Drivers:\\n - CarIdx: 0\\n   UserName: Team Mate\\n   UserID: 111\\n - CarIdx: 1\\n   UserName: Andrew Brooks\\n   AbbrevName: Brooks, A\\n   UserID: 222\\n', 'Team Mate', true);
console.log(JSON.stringify({ who: ib.who, team: ib.team, lapWho: ib.lapObj(ib.best).who, named, solo: ibtWho('Drivers:\\n - CarIdx: 0\\n   UserName: Solo\\n', 'Solo', false),
  rows: S.rows.map(r => [r.lap, r.ok, r.note]), clean: S.clean.map(r => r.lap), best: ib.laps[ib.best].lap }));
""", cls.path)

    def test_the_teammates_laps_are_not_yours(self):
        r = self.res
        notes = {lap: (ok, note) for lap, ok, note in r["rows"] if lap in (3, 4)}
        self.assertEqual(notes, {3: (False, "not you driving"), 4: (False, "not you driving")})
        self.assertNotIn(3, r["clean"])
        self.assertNotIn(4, r["clean"])
        self.assertIn(5, r["clean"])
        self.assertNotIn(r["best"], (3, 4))

    def test_named_by_who_recorded_the_file(self):
        r = self.res
        self.assertTrue(r["team"])
        self.assertEqual(r["who"], "You")                     # the teammate's name is never used for your laps
        self.assertEqual(r["lapWho"], "You")
        self.assertEqual(r["named"], "Andrew Brooks")         # found by user id when the recorder is listed
        self.assertEqual(r["solo"], "Solo")

def build_practice(path):
    """Ten laps of about 21 s. Lap 3 goes off the track for a moment (no incident points);
    lap 5 uses iRacing's active reset back from 60% to 30% (the clock runs on: a long lap);
    lap 7 resets forward from 30% to 60% (a short lap that would look like your best);
    lap 8 is 1 s slow (traffic); lap 10 is 0.4 s quick, a real good lap."""
    rows, t, last = [], 0.0, 0.0
    times = {1: 21.0, 2: 21.1, 3: 21.05, 4: 20.95, 5: 21.0, 6: 21.08, 7: 21.0, 8: 22.0, 9: 21.02, 10: 20.6}
    for lap in range(1, 11):
        T = times[lap]
        n = int(round(T * HZ))
        k, i, jumped = 0, 0, False
        while True:
            p = k / n
            if lap == 5 and not jumped and p >= 0.6:
                k, jumped = int(0.3 * n), True
                p = k / n
            if lap == 7 and not jumped and p >= 0.3:
                k, jumped = int(0.6 * n), True
                p = k / n
            if p >= 1.0:
                break
            rows.append({"SessionTime": t, "Lap": lap, "LapDistPct": p, "Speed": 50.0, "Throttle": 1.0, "Gear": 4,
                         "LapCurrentLapTime": i / HZ, "LapLastLapTime": last, "FuelLevel": 40 - 0.01 * len(rows) / 60,
                         "PlayerTrackSurface": 0 if lap == 3 and 0.40 < p < 0.42 else 3, "TrackTempCrew": 30.0})
            t += 1 / HZ
            k += 1
            i += 1
        last = i / HZ
    for j in range(120):                                   # into the pits after lap 10
        rows.append({"SessionTime": t, "Lap": 11, "LapDistPct": 0.002 * j / 120, "Speed": 20.0, "OnPitRoad": 1,
                     "LapLastLapTime": last, "PlayerTrackSurface": 1})
        t += 1 / HZ
    write_ibt(path, SESSION_YAML, CHANNELS + [("PlayerTrackSurface", 2, "")], rows)


@unittest.skipUnless(NODE, "Node.js is not installed")
class CleanLapsTest(unittest.TestCase):
    """What counts as a clean lap: no incident, off-track moment or active reset, and a usual time."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "practice.ibt")
        build_practice(cls.path)
        cls.r = run_js("""
const ib = ibt(readBytes(ARGS[0])), S = sessionStats(ib);
const band = lapBand([84.9, 85.0, 85.1, 84.8, 85.2, 90.0, 80.0]);
console.log(JSON.stringify({ rows: S.rows.map(r => [r.lap, r.ok, r.note]), clean: S.clean.map(r => r.lap),
  cbest: S.cbest, best: ib.laps[ib.best].lap, band: [band.lo, band.hi], few: lapBand([80, 90, 100]).hi,
  usual: usualLaps([{l: 85}, {l: 85.2}, {l: 84.9}, {l: 95}, {l: 85.1}], x => x.l).map(x => x.l) }));
""", cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_resets_are_not_timed_laps(self):
        notes = {lap: (ok, note) for lap, ok, note in self.r["rows"]}
        self.assertEqual(notes[5], (False, "active reset"))
        self.assertEqual(notes[7], (False, "active reset"))
        self.assertNotEqual(self.r["best"], 7)               # the short, reset lap is never your best

    def test_clean_laps_leave_out_offs_and_unusual_times(self):
        notes = {lap: note for lap, ok, note in self.r["rows"]}
        self.assertEqual(notes[3], "off track")
        self.assertEqual(notes[8], "unusually slow, left out")
        self.assertEqual(self.r["clean"], [1, 2, 4, 6, 9, 10])   # lap 10 is quick, but a real lap
        self.assertAlmostEqual(self.r["cbest"], 20.6, places=1)

    def test_band(self):
        lo, hi = self.r["band"]
        self.assertTrue(85.5 < hi < 86.5 and 80.0 < lo < 81.0)    # 90 is slow; 80 is more than 5% quick
        self.assertIsNone(self.r["few"])                          # too few laps to call any unusual
        self.assertEqual(self.r["usual"], [85, 85.2, 84.9, 85.1])


@unittest.skipUnless(NODE, "Node.js is not installed")
class Garage61NameTest(unittest.TestCase):
    """Driver and lap time come from Garage 61's file name, with spaces (as downloaded) or underscores."""

    def test_both_name_styles(self):
        r = run_js("""
const csv = 'Speed,LapDistPct,Brake,Throttle,SteeringWheelAngle,Gear\\n' + Array.from({length: 600}, (_, i) => '50,' + (i / 599) + ',0,1,0,4').join('\\n');
const a = parse(csv, 'Garage 61 - Mathijs Wijten - Ferrari 296 GT3 - Indianapolis Motor Speedway (Road Course) - 01.22.716 - 01M2JB7ECHN.csv');
const b = parse(csv, 'Garage_61_-_nick_kalra_-_Ferrari_296_GT3_-_Indianapolis_Motor_Speedway__Road_Course__-_01_23_750_-_01M4CSEAK52.csv');
const c = parse(csv, 'my fast lap.csv');
console.log(JSON.stringify({ a: [a.who, a.lap], b: [b.who, b.lap], c: [c.who, +c.lap.toFixed(2)] }));
""")
        self.assertEqual(r["a"], ["Mathijs Wijten", 82.716])
        self.assertEqual(r["b"], ["nick kalra", 83.75])
        self.assertEqual(r["c"], ["Lap", 9.98])               # renamed: the time from its length


@unittest.skipUnless(NODE, "Node.js is not installed")
class SessionReviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.path = os.path.join(cls.dir, "session.ibt")
        cls.exp = build_session(cls.path)
        cls.res = run_js("""
const ib = ibt(readBytes(ARGS[0])), S = sessionStats(ib);
console.log(JSON.stringify({rows: S.rows.map(r => ({lap: r.lap, ok: r.ok, time: r.time, note: r.note, sec: r.sec,
  fuel: r.fuel, refuel: r.refuel, inc: r.inc, tt: r.tt, lf: r.ty.LF.p})), best: S.best, tb: S.tb, bestSec: S.bestSec,
  secs: S.secs, sd: S.sd, clean: S.clean.map(r => r.lap), fuelAvg: S.fuel}));
""", cls.path)

    def test_every_lap_listed_in_file_order_with_what_kind_it_is(self):
        rows = self.res["rows"]
        self.assertEqual([r["lap"] for r in rows], [0, 1, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 9])
        notes = {i: r["note"] for i, r in enumerate(rows)}
        self.assertEqual(notes[0], "in the pits")
        self.assertEqual(notes[2], "joined mid-lap")
        self.assertEqual(notes[9], "in lap")
        self.assertEqual(notes[10], "out lap")
        self.assertEqual(notes[12], "in the pits")
        self.assertEqual(notes[13], "in the pits")
        self.assertEqual([r["ok"] for r in rows], [False] * 3 + [True] * 6 + [False, False, True, False, False])

    def test_sectors_follow_irackings_splits_and_add_up(self):
        self.assertEqual(self.res["secs"], [0.3, 0.6])
        timed = [r for r in self.res["rows"] if r["ok"]]
        want = {k: st for k, kind, _, st in self.exp["laps"] if kind == "lap"}
        for r in timed:
            self.assertAlmostEqual(sum(r["sec"]), r["time"], places=6)
            for got, w in zip(r["sec"], want[r["lap"]]):
                self.assertAlmostEqual(got, w, delta=1.5 / HZ)

    def test_theoretical_best_is_the_best_sectors_and_never_slower(self):
        laps = [st for k, kind, _, st in self.exp["laps"] if kind == "lap"]
        best_secs = [min(st[j] for st in laps) for j in range(3)]
        self.assertAlmostEqual(self.res["tb"], sum(best_secs), delta=3 / HZ)
        self.assertLessEqual(self.res["tb"], self.res["best"] + 1e-9)
        self.assertAlmostEqual(self.res["best"], min(sum(st) for st in laps), delta=1.5 / HZ)

    def test_fuel_incidents_and_clean_laps(self):
        rows = self.res["rows"]
        out_lap = rows[10]
        self.assertTrue(out_lap["refuel"])
        self.assertIsNone(out_lap["fuel"])                  # a refuel is not "fuel used"
        for r in rows[3:9]:
            self.assertAlmostEqual(r["fuel"], 0.25, delta=0.01)
        self.assertAlmostEqual(self.res["fuelAvg"], 0.25, delta=0.01)
        self.assertEqual(rows[6]["inc"], 2)                 # lap 4
        self.assertNotIn(4, self.res["clean"])              # an incident lap is not a clean lap
        self.assertNotIn(8, self.res["clean"])              # nor a pit lap
        self.assertIn(9, self.res["clean"])
        self.assertAlmostEqual(rows[4]["lf"], 172, delta=0.01)   # lap 2: 170 + lap
        self.assertAlmostEqual(rows[4]["tt"], 29.8, delta=0.01)


@unittest.skipUnless(NODE, "Node.js is not installed")
class RacingLineTest(unittest.TestCase):
    """Two half-circle corners from GPS positions: the reference on a 50 m radius,
    you on 49 m (1 m inside) or 52 m (2 m outside). Left- and right-handers."""

    def corner(self, left, your_radius):
        return run_js("""
const la0 = 39.79, lo0 = -86.23, k = Math.cos(la0 * Math.PI / 180), left = %s, ry = %f;
const path = r => { const Lat = new Float64Array(N), Lon = new Float64Array(N);
  for (let g = 0; g < N; g++) { const th = g / (N - 1) * Math.PI, x = left ? r * Math.cos(th) : -r * Math.cos(th), y = r * Math.sin(th);
    Lat[g] = la0 + y / 110574; Lon[g] = lo0 + x / (111320 * k) } return { Lat, Lon } };
R = { A: path(ry), B: path(50), cs: [] };
const c = { s: 100, e: 900, b: { ba: 200, mg: 500, ta: 700 }, a: { ba: 210, mg: 505, ta: 690 } };
const q = lineCorner(c);
console.log(JSON.stringify({ left: q.left, turnIn: q.turnIn, apex: q.apex, exit: q.exit, words: lineWords(q.apex), svg: lineSvg(c, 200).length > 500 }));
""" % ("true" if left else "false", your_radius))

    def test_inside_and_outside_on_both_kinds_of_corner(self):
        for left in (True, False):
            q = self.corner(left, 49.0)
            self.assertEqual(q["left"], left)
            for k in ("turnIn", "apex", "exit"):
                self.assertAlmostEqual(q[k], 1.0, delta=0.05)          # 1 m inside
            self.assertIn("inside", q["words"])
            self.assertTrue(q["svg"])
            q = self.corner(left, 52.0)
            self.assertAlmostEqual(q["apex"], -2.0, delta=0.05)        # 2 m outside
            self.assertIn("outside", q["words"])


SETUP_296 = """CarSetup:
 UpdateCount: 1
 TiresAero:
  AeroBalanceCalc:
   RearWingAngle: 8 degrees
 Chassis:
  FrontBrakesLights:
   ArbBlades: 75 degrees
  LeftFront:
   RideHeight: 53.1 mm
   Camber: -4.0 deg
  RightFront:
   RideHeight: 53.1 mm
   Camber: -4.0 deg
  LeftRear:
   RideHeight: 60.4 mm
   Camber: -3.0 deg
  RightRear:
   RideHeight: 60.4 mm
   Camber: -3.0 deg
  Rear:
   ArbBlades: 90 degrees
   RearWingAngle: 8 degrees
  InCarAdjustments:
   BrakePressureBias: 50.5%
   TcSetting: 5 (TC)
  GearsDifferential:
   DiffPreload: 115 Nm
"""

HANDLING_YAML = SESSION_YAML.replace("CarPath: testcar", "CarPath: ferrari296gt3").replace(
    "CarSetup:\n UpdateCount: 1\n", SETUP_296)
HCH = [("SessionTime", 5, "s"), ("Lap", 2, ""), ("LapDistPct", 4, "%"), ("Speed", 4, "m/s"),
       ("Brake", 4, "%"), ("Throttle", 4, "%"), ("SteeringWheelAngle", 4, "rad"), ("Gear", 2, ""),
       ("LapLastLapTime", 4, "s"), ("LapCurrentLapTime", 4, "s"), ("OnPitRoad", 1, ""), ("YawRate", 4, "rad/s"),
       ("LatAccel", 4, "m/s^2"), ("VelocityX", 4, "m/s"), ("VelocityY", 4, "m/s"), ("CFSRrideHeight", 4, "m")]
K_TRUE = 50.0      # steering ratio x wheelbase of the synthetic car


def build_handling(path, factor=None, exit_countersteer=False, splitter=0.03, laps=5):
    """Laps of steady segments. factor[(phase, speed)] is steering used / steering a
    neutral car needs; 1 everywhere is a neutral car. A gentle curve gives the fit.
    exit_countersteer: on fast exits the car yaws against the steering (oversteer)."""
    factor = factor or {}
    # (seconds, speed m/s, curvature 1/m, brake, throttle, phase, speed class)
    segs = [(3.0, 60, 0.0, 0, 1, None, None), (2.0, 40, 0.0028, 0, 0.7, None, None),   # gentle: ~0.46 g
            (1.5, 30, 0.012, 0.3, 0, "entry", "slow"), (2.0, 25, 0.017, 0, 0.3, "mid", "slow"),
            (2.0, 28, 0.014, 0, 0.9, "exit", "slow"), (3.0, 62, 0.0, 0, 1, None, None),
            (1.5, 50, 0.0055, 0.3, 0, "entry", "fast"), (2.0, 46, 0.006, 0, 0.3, "mid", "fast"),
            (2.0, 48, 0.0055, 0, 0.9, "exit", "fast"), (1.0, 55, 0.0, 0, 1, None, None),
            (1.0, 18, 0.0, 0, 0.2, None, None)]      # slow link back to the line
    dist = sum(d * v for d, v, *_ in segs)
    rows, t, last = [], 0.0, 0.0
    for lapno in range(1, laps + 1):
        lap_t = sum(s[0] for s in segs)
        n = int(round(lap_t * HZ))
        pos, tt = 0.0, 0.0
        seg_i, seg_t = 0, 0.0
        for i in range(n):
            d, v, k, br, th, ph, sp = segs[seg_i]
            f = factor.get((ph, sp), 1.0) if ph else 1.0
            yaw, sw, vy = v * k, K_TRUE * k * f, -0.3 * v * k * 10
            if exit_countersteer and ph == "exit" and sp == "fast":
                sw = -0.5 * K_TRUE * k                  # opposite lock
                vy = 3.0                                 # sliding
            rows.append({"SessionTime": t, "Lap": lapno, "LapDistPct": min(pos / dist, 0.99999), "Speed": v,
                         "Brake": br, "Throttle": th, "SteeringWheelAngle": sw, "Gear": 4, "YawRate": yaw,
                         "LatAccel": v * v * k, "VelocityX": v, "VelocityY": vy, "LapCurrentLapTime": tt,
                         "LapLastLapTime": last, "CFSRrideHeight": splitter})
            pos += v / HZ
            t += 1 / HZ
            tt += 1 / HZ
            seg_t += 1 / HZ
            if seg_t >= d - 1e-9 and seg_i < len(segs) - 1:
                seg_i, seg_t = seg_i + 1, 0.0
        last = n / HZ
    # a short in-pit tail so the last lap's time is reported
    for i in range(300):
        rows.append({"SessionTime": t, "Lap": laps + 1, "LapDistPct": 0.0005, "Speed": 2, "OnPitRoad": 1,
                     "LapLastLapTime": last})
        t += 1 / HZ
    write_ibt(path, HANDLING_YAML, HCH, rows)


@unittest.skipUnless(NODE, "Node.js is not installed")
class SetupAdviceTest(unittest.TestCase):
    JS = """
const ib = ibt(readBytes(ARGS[0])), h = handlingStats(ib), F = handlingFindings(h), rows = setupRows(ib.setup);
const out = F.map(f => ({ kind: f.kind, p: f.p || null, where: f.where, changes: adviceChanges(f).map(c => adviceRow(rows, c, ib.carPath)).filter(Boolean).map(c => ({ label: c.label, word: c.word, at: c.at })) }));
console.log(JSON.stringify({ K: h.K, laps: h.laps, B: h.B, F: out, html: adviceHtml().length }));
"""

    def run_case(self, **kw):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "h.ibt")
        build_handling(p, **kw)
        return run_js(self.JS, p)

    def test_neutral_car_gets_no_suggestions(self):
        r = self.run_case()
        self.assertAlmostEqual(r["K"], K_TRUE, delta=1.0)        # recovers steering ratio x wheelbase
        self.assertEqual(r["F"], [])
        for k, b in r["B"].items():
            self.assertAlmostEqual(b["ui"], 1.0, delta=0.03, msg=k)

    def test_fast_corner_mid_understeer_points_at_aero_first(self):
        r = self.run_case(factor={("mid", "fast"): 1.35})
        us = [f for f in r["F"] if f["kind"] == "us"]
        self.assertEqual(len(us), 1)
        self.assertEqual((us[0]["p"], us[0]["where"]), ("mid", "fast"))
        labels = [c["label"] for c in us[0]["changes"]]
        self.assertEqual(labels[0], "Front ride height (both sides)")
        self.assertIn("Rear wing angle", labels)
        rear = [c for c in us[0]["changes"] if c["label"] == "Rear ARB blades"][0]
        self.assertIn("maximum", rear["at"])                     # 90 degrees on the 296: shown as blocked, not offered

    def test_slow_corner_understeer_points_at_the_bars(self):
        r = self.run_case(factor={("mid", "slow"): 1.4})
        us = [f for f in r["F"] if f["kind"] == "us"][0]
        self.assertEqual((us["p"], us["where"]), ("mid", "slow"))
        self.assertEqual(us["changes"][0]["label"], "Front ARB blades")
        self.assertEqual(us["changes"][0]["word"], "Softer")

    def test_entry_understeer_moves_brake_bias_rearward(self):
        r = self.run_case(factor={("entry", "slow"): 1.4, ("entry", "fast"): 1.4})
        us = [f for f in r["F"] if f["kind"] == "us" and f["p"] == "entry"][0]
        self.assertEqual(us["changes"][0]["label"], "Brake bias")
        self.assertEqual(us["changes"][0]["word"], "More to the rear")

    def test_power_oversteer_needs_opposite_lock_to_be_called(self):
        r = self.run_case(exit_countersteer=True)
        os_ = [f for f in r["F"] if f["kind"] == "os"]
        self.assertEqual([(f["p"], f["where"]) for f in os_], [("exit", "fast")])
        self.assertIn("Rear ARB blades", [c["label"] for c in os_[0]["changes"]])

    def test_splitter_on_the_ground_raises_the_front(self):
        r = self.run_case(splitter=0.001)
        b = [f for f in r["F"] if f["kind"] == "bottom"][0]
        self.assertEqual(b["changes"][0]["label"], "Front ride height (both sides)")
        self.assertEqual(b["changes"][0]["word"], "Higher")


# --------------------------------------------------------------------------- #
# Driver laps: any car's laps recorded from the live feed (CarIdx arrays)
# --------------------------------------------------------------------------- #
DRIVER_LAPS_JS = r"""
const yaml = `WeekendInfo:
 TrackDisplayName: Test Raceway
 TrackConfigName: Grand Prix
 TrackLength: 2.00 km
DriverInfo:
 DriverCarIdx: 63
 Drivers:
 - CarIdx: 1
   UserName: Fast Fred
   CarNumber: "7"
   IRating: 3100
   CarScreenName: Test Car GT3
   CarPath: testcar
   CarClassShortName: GT3
 - CarIdx: 2
   UserName: Pit Pete
   CarNumber: "8"
   IRating: 1500
   CarScreenName: Test Car GT3
   CarPath: testcar
 - CarIdx: 3
   UserName: Laggy Larry
   CarNumber: "9"
   IRating: 2000
   CarScreenName: Test Car GT3
   CarPath: testcar
 - CarIdx: 63
   UserName: Spectator Sam
   IsSpectator: 1
`;
const LEN = 2000;
// speed by lap position: a corner at half distance; fred's corner is quicker than "yours"
const prof = (d, cmin) => 60 - (60 - cmin) * Math.exp(-(((d - 0.5) / 0.05) ** 2));
function lapTime(cmin) { let t = 0; const n = 20000; for (let i = 0; i < n; i++) t += (LEN / n) / prof((i + 0.5) / n, cmin); return t }
liveSession(yaml);
const cars = {
  1: { d: 0.80, lap: 0, cmin: 33, pit: false, jump: false },  // joins mid-lap: that partial lap must not count
  2: { d: 0.70, lap: 0, cmin: 33, pit: false },               // pits on lap 2
  3: { d: 0.60, lap: 0, cmin: 33, jump: false },              // a position jump on lap 2
};
let t = 1000.37;                       // not on any crossing
const dt = 1 / 30, CROSS = {}, LAST = {};
const official = [];                   // [time at which iRacing reports it, idx, lap time]
for (let step = 0; step < 30 * 200; step++) {
  const P = [], L = [], G = [], RPM = [], ST = [], PIT = [], SURF = [], LL = [];
  for (let i = 0; i < 64; i++) { P.push(-1); L.push(-1); G.push(0); RPM.push(0); ST.push(0); PIT.push(false); SURF.push(-1); LL.push(-1) }
  for (const k in cars) {
    const c = cars[k], i = +k;
    let nd = c.d + prof(c.d, c.cmin) * dt / LEN;
    if (nd >= 1) {                                         // exact crossing time, for checking
      const tc = t + (1 - c.d) / (nd - c.d) * dt;
      if (CROSS[i] != null) official.push([tc + 0.4, i, tc - CROSS[i]]);
      CROSS[i] = tc; nd -= 1; c.lap++;
    }
    c.d = nd;
    let shown = c.d;
    if (i === 3 && c.lap === 2 && c.d > 0.40 && c.d < 0.45) shown = Math.min(0.999, c.d + 0.08);   // network jump
    const inPit = i === 2 && c.lap === 2 && c.d > 0.3 && c.d < 0.4;
    P[i] = +shown.toFixed(6); L[i] = c.lap; G[i] = 4; RPM[i] = 7000; ST[i] = 0.1; PIT[i] = inPit; SURF[i] = inPit ? 2 : 3;
    const done = official.filter(o => o[1] === i && o[0] <= t);
    if (done.length) LL[i] = +done[done.length - 1][2].toFixed(3);
  }
  // spectating: the player's own Lap is missing, but every car's arrays are there
  liveTick({ SessionTime: t, Lap: null, CarIdxLapDistPct: P, CarIdxLap: L, CarIdxGear: G, CarIdxRPM: RPM,
             CarIdxSteer: ST, CarIdxOnPitRoad: PIT, CarIdxTrackSurface: SURF, CarIdxLastLapTime: LL });
  t += dt;
}
const lib = ghLibLoad(), byIdx = {};
lib.forEach(e => byIdx[e.idx] = e);
const truth = lapTime(33);
const e1 = byIdx[1];
// counts of finished clean laps per car
const counts = Object.fromEntries(Object.entries(GH.cars).map(([k, s]) => [k, s.n]));
// the recorded lap against one of "yours" (corner 30 m/s, so slower), built the way rs() expects
const n = 4000, you = { n, who: 'You', lapNum: 3, D: new Float64Array(n), t: new Float64Array(n) };
['Speed','Brake','Throttle','Steer','Gear','Lat','Lon','RPM','ABS','LatG','LonG','VertG','YawRate'].forEach(k => you[k] = new Float64Array(n));
let tt = 0;
for (let i = 0; i < n; i++) { const d = i / (n - 1); you.D[i] = d; you.Speed[i] = prof(d, 30); you.Gear[i] = 4; you.t[i] = tt; tt += (LEN / (n - 1)) / prof(d, 30) }
you.lap = you.t[n - 1];
const ref = ghRef(e1), Q = limAnalyze(you, ref);
let html = '', err = null;
try { F.a = you; F.b = ref; html = limHtml() } catch (x) { err = String(x) }
GH.sel = e1.key; LIVE_CMP = 'ghost';
const lr = liveRef();
console.log(JSON.stringify({
  keys: lib.map(e => e.key), track: e1.track, trackFile: ghTrackOf(yaml),
  t1: e1.time, official1: e1.official, truth, counts,
  vmin1: Math.min(...e1.g.v), vmax1: Math.max(...e1.g.v), n1: e1.g.t.length,
  has2: !!byIdx[2], t2: byIdx[2] && byIdx[2].time, has3: !!byIdx[3], t3: byIdx[3] && byIdx[3].time,
  spectator: lib.some(e => e.name === 'Spectator Sam'),
  total: Q.total, youLap: you.lap, corners: Q.cs.map(c => ({ n: c.n, ap: c.ap, dt: c.dt, bms: c.b.ms, ams: c.a.ms })),
  htmlOk: !err && html.includes('Fast Fred') && html.includes('no pedals') === false, err,
  liveName: lr && lr.name, liveN: lr && lr.d.length, liveHalf: lr && liveRefTime(lr, 0.5),
  halfTruth: (() => { let s = 0; const m = 10000; for (let i = 0; i < m; i++) s += (LEN / 2 / m) / prof((i + 0.5) / m * 0.5, 33); return s })(),
}));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class DriverLapsTest(unittest.TestCase):
    """Record any car's laps from the live feed and compare one with your lap."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_js(DRIVER_LAPS_JS)

    def test_clean_laps_are_kept_with_the_right_time(self):
        r = self.r
        self.assertAlmostEqual(r["t1"], r["truth"], delta=0.002)       # iRacing's time, once it agreed
        self.assertTrue(r["official1"])
        self.assertEqual(r["track"], "Test Raceway · Grand Prix")
        self.assertEqual(r["track"], r["trackFile"])                    # live and files name the layout alike
        self.assertGreater(r["counts"]["1"], 3)
        self.assertEqual(r["n1"], 500)

    def test_speed_is_worked_out_from_movement(self):
        r = self.r
        self.assertAlmostEqual(r["vmax1"], 60.0, delta=0.6)
        self.assertAlmostEqual(r["vmin1"], 33.0, delta=1.0)

    def test_partial_pit_and_jumped_laps_do_not_count(self):
        r = self.r
        # car 1 joined mid-lap: its first lap is never recorded, so it has one fewer than full laps
        # car 2: the pit lap is skipped but its clean laps are kept
        self.assertTrue(r["has2"])
        self.assertAlmostEqual(r["t2"], r["truth"], delta=0.002)
        # car 3: only the lap with the jump is skipped
        self.assertTrue(r["has3"])
        self.assertAlmostEqual(r["t3"], r["truth"], delta=0.002)
        self.assertFalse(r["spectator"])
        self.assertEqual(r["counts"]["2"], r["counts"]["3"])            # each lost exactly one lap
        self.assertEqual(r["counts"]["2"], r["counts"]["1"] - 1)

    def test_comparison_finds_the_corner_and_the_time(self):
        r = self.r
        self.assertAlmostEqual(r["total"], r["youLap"] - r["truth"], delta=0.01)
        self.assertGreater(r["total"], 0.2)                             # your corner is slower
        cs = r["corners"]
        self.assertEqual(len(cs), 1)
        self.assertAlmostEqual(cs[0]["ap"] / 999, 0.5, delta=0.01)
        self.assertAlmostEqual(cs[0]["dt"], r["total"], delta=0.05)    # all the time is lost in that corner
        self.assertAlmostEqual(cs[0]["ams"], 30 * 3.6, delta=1.5)
        self.assertAlmostEqual(cs[0]["bms"], 33 * 3.6, delta=3.0)
        self.assertTrue(r["htmlOk"], r["err"])

    def test_live_graphs_can_use_a_recorded_driver(self):
        r = self.r
        self.assertEqual(r["liveName"], "#7 Fast Fred")
        self.assertEqual(r["liveN"], 500)
        self.assertAlmostEqual(r["liveHalf"], r["halfTruth"], delta=0.02)


PACE_JS = r"""
// recorded drivers at one layout and car: faster with iRating, plus noise
const L = [], ir = [1200, 1500, 1800, 2000, 2100, 2300, 2500, 2700, 2900, 3000, 3400, 4200];
ir.forEach((r, i) => L.push({ name: 'D' + i, num: String(i), ir: r, time: 90 - (r - 1200) * 0.0008 + (i % 3) * 0.05 }));
L.push({ name: 'NoRating', num: '99', ir: null, time: 80 });          // no iRating: never counted
const inRange = L.filter(e => e.ir >= 2000 && e.ir <= 3000).map(e => e.time).sort((a, b) => a - b);
const med = inRange.length % 2 ? inRange[(inRange.length - 1) / 2] : (inRange[inRange.length / 2 - 1] + inRange[inRange.length / 2]) / 2;
const mine = [89.2, 88.85, 89.0, 88.9];
const S = paceStats(L, mine, 2000, 3000, 'median'), Sf = paceStats(L, mine, 2000, 3000, 'fastest');
const few = paceStats(L, mine, 3500, 5000, 'median');
const any = paceStats(L, mine, null, null, 'median');
// the whole card, on recorded laps in the library
localStorage.setItem('ghostLaps', JSON.stringify(L.filter(e => e.ir).map((e, i) => ({ ...e, key: 'k' + i, track: 'T · GP', carPath: 'car1', at: i, g: { t: [0], v: [50], gear: [3], rpm: [1], st: [0] } }))));
GH.lib = null; GH.track = 'T · GP'; LIVE.meta = { carPath: 'car1' }; F.a = { lap: 88.85 };
const def = paceRange();
let html = '', err = null; try { html = paceHtml() } catch (x) { err = String(x) }
localStorage.setItem('irrange', JSON.stringify([null, null]));
const anyRange = paceRange();
console.log(JSON.stringify({ ref: S.ref, med, inR: S.inR.length, bestPct: S.bestPct, typ: S.typ, faster: S.faster,
  near: S.near.map(x => x.e.ir), nearMed: S.nearMed, refFast: Sf.ref, minIn: Math.min(...inRange), fewRef: few.ref,
  anyRated: any.rated.length, def, anyRange, err, hasBest: html.includes('Your best'), hasSvg: html.includes('<svg'),
  noLabels: !/\b(PRO|AM|BEGINNER|alien)\b/i.test(html) }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class PaceRatingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js(PACE_JS)

    def test_reference_is_the_middle_or_fastest_of_the_range(self):
        r = self.r
        self.assertAlmostEqual(r["ref"], r["med"], places=9)
        self.assertEqual(r["inR"], 7)                                 # 2000-3000 inclusive
        self.assertAlmostEqual(r["refFast"], r["minIn"], places=9)
        self.assertIsNone(r["fewRef"])                                # fewer than 3 drivers: no scale
        self.assertEqual(r["anyRated"], 12)                           # a driver without iRating is left out

    def test_your_laps_on_the_scale(self):
        r = self.r
        self.assertAlmostEqual(r["bestPct"], 88.85 / r["med"] * 100, places=9)
        self.assertAlmostEqual(r["typ"], (88.9 + 89.0) / 2, places=9)
        # drivers within 0.3 % of your best, and their iRatings
        for ir in r["near"]:
            self.assertGreaterEqual(ir, 1500)
        self.assertGreaterEqual(len(r["near"]), 3)
        self.assertLessEqual(r["faster"], r["inR"])

    def test_card_renders_without_skill_labels(self):
        r = self.r
        self.assertIsNone(r["err"])
        self.assertTrue(r["hasBest"])
        self.assertTrue(r["hasSvg"])
        self.assertTrue(r["noLabels"])
        self.assertEqual(r["def"], [2000, 3000])                      # your stated range until you choose
        self.assertEqual(r["anyRange"], [None, None])                 # blank boxes mean any rating


# The real Ferrari 296 GT3 setup from the Indianapolis file (2026-10-10), as iRacing writes it.
SETUP_296_YAML = """WeekendInfo:
 TrackDisplayName: Indianapolis Motor Speedway
DriverInfo:
 DriverCarIdx: 0
 Drivers:
 - CarIdx: 0
   UserName: Test Driver
   CarPath: ferrari296gt3
   CarScreenName: Ferrari 296 GT3
CarSetup:
 UpdateCount: 4
 TiresAero:
  TireType:
   TireType: Dry
  LeftFront:
   StartingPressure: 159 kPa
   LastHotPressure: 159 kPa
   LastTempsOMI: 35C, 35C, 35C
   TreadRemaining: 100%, 100%, 100%
  LeftRear:
   StartingPressure: 159 kPa
   LastHotPressure: 159 kPa
   LastTempsOMI: 35C, 35C, 35C
   TreadRemaining: 100%, 100%, 100%
  RightFront:
   StartingPressure: 159 kPa
   LastHotPressure: 159 kPa
   LastTempsIMO: 35C, 35C, 35C
   TreadRemaining: 100%, 100%, 100%
  RightRear:
   StartingPressure: 159 kPa
   LastHotPressure: 159 kPa
   LastTempsIMO: 35C, 35C, 35C
   TreadRemaining: 100%, 100%, 100%
  AeroBalanceCalc:
   FrontRhAtSpeed: 52 mm
   RearRhAtSpeed: 59 mm
   RearWingAngle: 8 degrees
   FrontDownforce: 42.9%
 Chassis:
  FrontBrakesLights:
   ArbBlades: 75 degrees
   ArbOptions: Large
   TotalToeIn: -4.1 mm
   FrontMasterCyl: 16.8 mm
   RearMasterCyl: 17.8 mm
   BrakePads: Low friction
   EnduranceLights: Not Fitted
   NightLedStripColor: OFF
   CenterFrontSplitterHeight: 50.7 mm
  LeftFront:
   CornerWeight: 3363 N
   RideHeight: 53.1 mm
   BumpRubberGap: 20 mm
   SpringRate: 190 N/mm
   Camber: -4.0 deg
  LeftRear:
   CornerWeight: 3806 N
   RideHeight: 60.4 mm
   BumpRubberGap: 32 mm
   SpringRate: 220 N/mm
   Camber: -3.0 deg
   ToeIn: +1.5 mm
  Rear:
   FuelLevel: 52.0 L
   ArbBlades: 90 degrees
   ArbOptions: Large
   RearWingAngle: 8 degrees
  InCarAdjustments:
   BrakePressureBias: 50.5%
   AbsSetting: 2 (ABS)
   TcSetting: 5 (TC)
   ThrottleShapeSetting: 1 (PED)
   DashDisplayPage: Race 
   FWtdist: 46.9%
   CrossWeight: 50.0%
  RightFront:
   CornerWeight: 3363 N
   RideHeight: 53.1 mm
   BumpRubberGap: 20 mm
   SpringRate: 190 N/mm
   Camber: -4.0 deg
  RightRear:
   CornerWeight: 3806 N
   RideHeight: 60.4 mm
   BumpRubberGap: 32 mm
   SpringRate: 220 N/mm
   Camber: -3.0 deg
   ToeIn: +1.5 mm
  GearsDifferential:
   GearStack: FIA
   FrictionFaces: 8
   DiffPreload: 115 Nm
 Dampers:
  FrontDampers:
   LowSpeedCompressionDamping: 3 clicks
   HighSpeedCompressionDamping: 7 clicks
   LowSpeedReboundDamping: 6 clicks
   HighSpeedReboundDamping: 11 clicks
  RearDampers:
   LowSpeedCompressionDamping: 4 clicks
   HighSpeedCompressionDamping: 6 clicks
   LowSpeedReboundDamping: 6 clicks
   HighSpeedReboundDamping: 11 clicks

"""

SYMPTOM_JS = r"""
const y = ARGS_YAML;
LIVE.ws = {}; LIVE.setup = setupTree(y); LIVE.meta = { carPath: 'ferrari296gt3', carName: 'Ferrari 296 GT3', track: 'Indy' };
const page = setupHtml();
const out = {}, errs = [];
for (const s of SYMPTOMS) {
  SYM = s.id;
  let h = ''; try { h = symptomHtml() } catch (x) { errs.push(s.id + ': ' + x) }
  const items = [...h.matchAll(/<li[^>]*><b>([^<]+)<\/b>: ([^<]+?), one click/g)].map(m => [m[1], m[2]]);
  const why = s.ch.map(c => { const r = adviceRow(SETUP_ROWS, c, 'ferrari296gt3'); return r ? { k: c[0], why: r.why, at: r.at, label: r.label } : null }).filter(Boolean);
  out[s.id] = { g: s.g, items, why, blocked: (h.match(/is already at its (maximum|minimum)[^;.<]*/g) || []), drive: h.includes('Check the driving too') };
}
console.log(JSON.stringify({ tab: page.includes('Fix by symptom'), out, errs, measured: symMeasured() }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class FixBySymptomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        js = "const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + SYMPTOM_JS
        cls.r = run_js(js)

    def test_every_symptom_renders_with_settings_from_this_car(self):
        r = self.r
        self.assertTrue(r["tab"])
        self.assertEqual(r["errs"], [])
        for sid, x in r["out"].items():
            self.assertTrue(x["items"] or x["blocked"], sid)

    def test_directions_agree_with_what_each_setting_does(self):
        for sid, x in self.r["out"].items():
            for c in x["why"]:
                if c["k"] in ("arbblades", "rearwingangle"):
                    if x["g"] == "Understeer":
                        self.assertNotIn("more understeer", c["why"].lower(), (sid, c))
                    if x["g"] == "Oversteer":
                        self.assertNotIn("more oversteer", c["why"].lower(), (sid, c))

    def test_this_cars_limits_and_names(self):
        o = self.r["out"]
        # the real setup has the rear bar at 90 degrees, the 296's maximum
        self.assertTrue(any("maximum (90)" in b for b in o["u_mid_slow"]["blocked"]), o["u_mid_slow"])
        self.assertEqual(o["u_mid_slow"]["items"][0], ["Front ARB blades", "softer"])
        self.assertIn(["Brake bias", "more to the front"], o["o_entry"]["items"])
        self.assertIn("Rear toe (both sides)", [i[0] for i in o["o_entry"]["items"]])
        self.assertEqual(o["r_bottom"]["items"][0], ["Front ride height (both sides)", "higher"])
        self.assertTrue(o["u_turnin"]["drive"])
        self.assertEqual(self.r["measured"], {})                      # live setup: no file laps to measure


STYLE_JS = r"""
// four laps on a 1000-point grid, 0.1 s apart; each corner carries one habit
function lap(k) {
  const X = {}; ['Speed','Brake','Throttle','Steer','t'].forEach(c => X[c] = new Float64Array(N));
  for (let g = 0; g < N; g++) { X.t[g] = g * 0.1 + k * 0.001; X.Throttle[g] = 1; X.Speed[g] = 50 }
  const ramp = (a, b, f) => { for (let g = a; g <= b; g++) f(g, (g - a) / Math.max(1, b - a)) };
  const turn = (s, ap, e, amp) => { ramp(s, ap, (g, f) => X.Steer[g] = amp * f); ramp(ap, e, (g, f) => X.Steer[g] = amp * (1 - f)) };
  // A (100-150-200): brake fully off before turning in, and snapped off
  ramp(60, 110, g => { X.Brake[g] = .9; X.Throttle[g] = 0 }); ramp(111, 200, (g, f) => X.Throttle[g] = .1 + .9 * f);
  turn(118, 150, 200, 1.2);
  // B (400-450-500): trail braking, smooth release, coasting 0.8 s, a throttle stab every lap
  ramp(360, 410, g => { X.Brake[g] = .9; X.Throttle[g] = 0 }); ramp(410, 445, (g, f) => { X.Brake[g] = .9 * (1 - f); X.Throttle[g] = 0 });
  ramp(446, 453, g => { X.Brake[g] = 0; X.Throttle[g] = 0 });
  ramp(454, 460, (g, f) => X.Throttle[g] = .6 * f); ramp(461, 465, (g, f) => X.Throttle[g] = .6 - .4 * f); ramp(466, 500, (g, f) => X.Throttle[g] = .2 + .8 * f);
  turn(405, 450, 500, 1.0);
  // C (700-750-800): no braking, a wobbling wheel
  ramp(700, 800, g => X.Throttle[g] = .5); turn(705, 750, 795, 1.0); ramp(720, 780, (g, f) => X.Steer[g] += .15 * Math.sin(f * 8 * Math.PI));   // reverses the wheel 8 times
  // D (880-920-960): pressure building to the end of the zone, then a gentle trail
  ramp(840, 900, (g, f) => { X.Brake[g] = .2 + .75 * f; X.Throttle[g] = 0 }); ramp(900, 925, (g, f) => { X.Brake[g] = .95 * (1 - f); X.Throttle[g] = 0 });
  ramp(926, 960, (g, f) => X.Throttle[g] = .3 + .7 * f); turn(905, 920, 960, 1.0);
  return X;
}
const cs = [{ n: 1, s: 100, ap: 150, e: 200 }, { n: 2, s: 400, ap: 450, e: 500 }, { n: 3, s: 700, ap: 750, e: 800 }, { n: 4, s: 880, ap: 920, e: 960 }];
const S = drivingStyle([0, 1, 2, 3].map(lap), cs);
console.log(JSON.stringify({ flags: S.per.map(p => p.flags), traits: S.traits, per: S.per }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class DrivingStyleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js(STYLE_JS)

    def test_each_habit_is_found_where_it_was_put_and_nowhere_else(self):
        f = self.r["flags"]
        self.assertEqual(sorted(f[0]), ["fast_release", "no_trail"])
        self.assertEqual(sorted(f[1]), ["coast", "stab"])
        self.assertEqual(f[2], ["saw"])
        self.assertEqual(f[3], ["late_peak"])

    def test_measured_values(self):
        p = self.r["per"]
        self.assertEqual(p[0]["trail"], 0)
        self.assertLess(p[0]["rel"], 0.3)
        self.assertEqual(p[1]["trail"], 1)
        self.assertGreater(p[1]["rel"], 1.0)
        self.assertAlmostEqual(p[1]["coast"], 1.1, delta=0.11)        # 0.8 s with no pedal plus the tail of the release
        self.assertEqual(p[1]["stab"], 1)
        self.assertEqual(p[2]["braked"], 0)                            # no braking: no trail-braking verdict
        self.assertIsNone(p[2]["trail"])
        self.assertGreaterEqual(p[2]["cor"], 2)
        self.assertGreater(p[3]["peak"], 0.7)


@unittest.skipUnless(NODE, "Node.js is not installed")
class CarHelpTest(unittest.TestCase):
    """Coaching's "or let the car help": setup changes that make up for a habit, for this car,
    and a setting two habits pull opposite ways is called out instead of suggested."""

    def test_settings_for_the_habits_and_conflicts_called_out(self):
        js = "const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + r"""
LIVE.ws = {}; LIVE.setup = setupTree(ARGS_YAML); LIVE.meta = { carPath: 'ferrari296gt3', carName: 'Ferrari 296 GT3' };
setupHtml();
const rows = setupRows(LIVE.setup), one = coachCarHelp(['trail'], rows, 'ferrari296gt3'), two = coachCarHelp(['trail', 'saw'], rows, 'ferrari296gt3');
console.log(JSON.stringify({ one: one.list.map(r => r.label), oneClash: one.clash, two: two.list.map(r => r.label), clash: two.clash, none: coachCarHelp(['brake_later'], rows, 'ferrari296gt3') }));
"""
        r = run_js(js)
        self.assertTrue(r["one"])                                     # trail braking: settings that rotate the car on entry
        self.assertEqual(r["oneClash"], [])
        self.assertTrue(any("Diff preload" in c for c in r["clash"]))  # lower to rotate on entry, higher for stability
        self.assertFalse(any("Diff preload" in l for l in r["two"]))
        self.assertIsNone(r["none"])                                  # not a habit: no setup help

LIVE_VERDICT_JS = r"""
// runVerdict on its own
const A = [90.0, 90.1, 89.9, 90.05], Bf = A.map(t => t - 0.4), Bc = A.map(t => t - 0.02);
const v1 = runVerdict(A, Bf, null, null), v2 = runVerdict(A, Bc, null, null), v3 = runVerdict(A, A.map(t => t + 0.5), null, null), v4 = runVerdict(A.slice(0, 2), Bf, null, null);
// a live session: run 1 understeers at the limit, the rear bar comes down, run 2 is neutral and quicker
const y1 = ARGS_YAML, y2 = ARGS_YAML.replace('ArbBlades: 90 degrees', 'ArbBlades: 80 degrees');
LIVE.ws = {}; LIVE.state = 'bridge';
let T = 100, lap = 1;
function drive(yaml, laps, F, lapTime) {
  liveSession(yaml);
  for (let L = 0; L < laps; L++, lap++) {
    const n = Math.round(lapTime * 60);
    for (let i = 0; i < n; i++) {
      const d = i / n, ph = (d * 8) % 1, g = 1.25 * Math.abs(Math.sin(d * 8 * Math.PI)), v = 34 + 12 * (Math.floor(d * 8) % 2);
      const yaw = g * 9.80665 / v * (Math.floor(d * 16) % 2 ? 1 : -1), k = yaw / v;
      const steer = 40 * k * (g > 0.8 ? F : 1);                 // K = 40; at the limit the car needs F times more
      const brake = ph < 0.18 ? 0.4 : 0, thr = ph > 0.30 ? 0.8 : 0.2;   // entry on the brake, exit on the throttle
      liveTick({ Lap: lap, LapDistPct: d, LapCurrentLapTime: i / 60, LapLastLapTime: lap > 1 ? lapTime : -1, Speed: v, Brake: brake, Throttle: thr,
                 SteeringWheelAngle: steer, YawRate: yaw, LatAccel: g * 9.80665 * Math.sign(yaw), OnPitRoad: false, SessionTime: T });
      T += 1 / 60;
    }
  }
  // cross the line into the next lap so the last one finishes
  liveTick({ Lap: lap, LapDistPct: 0.001, LapCurrentLapTime: 0.01, LapLastLapTime: lapTime, Speed: 40, Brake: 0, Throttle: 1, SteeringWheelAngle: 0, YawRate: 0, LatAccel: 0, OnPitRoad: false, SessionTime: T });
}
drive(y1, 4, 1.30, 60.0);
drive(y2, 4, 1.00, 59.6);
const html = liveVerdictHtml();
const V = runVerdict(LIVE.laps.filter(l => l.ver === 1 && l.valid).map(l => l.time), LIVE.laps.filter(l => l.ver === 2 && l.valid).map(l => l.time), liveRunCore(1), liveRunCore(2));
console.log(JSON.stringify({ v1: v1.call, v2: v2.call, v3: v3.call, v4: v4.call, vers: LIVE.vers.length,
  valid: LIVE.laps.map(l => [l.ver, l.valid, +l.time.toFixed(2)]), call: V.call, dt: V.dt, phases: V.phases,
  html: html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').slice(0, 1200) }));
"""


LIVE_LAPS_JS = r"""
// live laps: a formation lap whose clock starts at the green, a clean lap, an off-track lap, an incident lap, a clean lap
LIVE.ws = {}; LIVE.state = 'bridge';
let T = 50, fuel = 40, inc = 0;
function lap(no, secs, opt) {
  const n = Math.round(secs * 60), clock0 = opt.clockFrom || 0;
  for (let i = 0; i < n; i++) {
    const d = i / n, ct = i / 60 >= clock0 ? i / 60 - clock0 : 0;
    if (opt.inc && d > 0.5 && inc === opt.incBefore) inc++;
    liveTick({ Lap: no, LapDistPct: d, LapCurrentLapTime: ct, LapLastLapTime: -1, Speed: 50, Brake: d % 0.25 < 0.03 ? 0.8 : 0, Throttle: d % 0.25 < 0.05 ? 0 : 1,
               SteeringWheelAngle: 0.2, YawRate: 0.1, LatAccel: 9, LongAccel: -3, OnPitRoad: false, SessionTime: T, FuelLevel: fuel,
               PlayerTrackSurface: opt.off && d > 0.4 && d < 0.41 ? 0 : 3, PlayerCarMyIncidentCount: inc, IsOnTrack: true });
    T += 1 / 60; fuel -= opt.burn / n;
  }
}
lap(0, 70, { clockFrom: 66, burn: 1.5 });       // formation: the clock runs only for the last 4 s
lap(1, 60, { burn: 2.0 });
lap(2, 60.5, { burn: 2.0, off: true });
lap(3, 61, { burn: 2.0, inc: true, incBefore: 0 });
lap(4, 60.2, { burn: 2.0 });
liveTick({ Lap: 5, LapDistPct: 0.001, LapCurrentLapTime: 0.01, LapLastLapTime: 60.2, Speed: 50, Throttle: 1, OnPitRoad: false, SessionTime: T, FuelLevel: fuel, PlayerTrackSurface: 3, PlayerCarMyIncidentCount: inc });
console.log(JSON.stringify({ laps: LIVE.laps.map(l => [l.lap, l.valid, l.why, l.fuel == null ? null : +l.fuel.toFixed(2)]),
  roll: TELE.roll.length, rollSpan: +(TELE.roll[TELE.roll.length - 1].t - TELE.roll[0].t).toFixed(1), gg: TELE.gg.length }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class LiveLapsTest(unittest.TestCase):
    """Live laps for the Live telemetry view, Coaching and the engineer: which count, and why not."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_js(LIVE_LAPS_JS)

    def test_which_laps_count(self):
        laps = {l[0]: (l[1], l[2]) for l in self.r["laps"]}
        self.assertFalse(laps[0][0])                                  # formation: 4 s on the clock
        self.assertEqual(laps[0][1], "lap clock off (start or formation lap)")
        self.assertEqual(laps[1], (True, ""))
        self.assertEqual(laps[2], (False, "off track"))
        self.assertEqual(laps[3], (False, "incident"))
        self.assertEqual(laps[4], (True, ""))

    def test_fuel_per_full_lap_and_rolling_window(self):
        fuel = {l[0]: l[3] for l in self.r["laps"]}
        self.assertEqual(fuel[1], 2.0)
        self.assertEqual(fuel[2], 2.0)                                # off track still burns a full lap of fuel
        self.assertLessEqual(self.r["rollSpan"], 12.0)                # the inputs trace keeps 12 s
        self.assertGreater(self.r["roll"], 600)
        self.assertEqual(self.r["gg"], 120)


@unittest.skipUnless(NODE, "Node.js is not installed")
class LiveVerdictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + LIVE_VERDICT_JS)

    def test_lap_time_calls(self):
        r = self.r
        self.assertEqual((r["v1"], r["v2"], r["v3"], r["v4"]), ("faster", "close", "slower", "few"))

    def test_a_live_garage_change_gets_a_verdict(self):
        r = self.r
        self.assertEqual(r["vers"], 2)
        self.assertEqual(r["call"], "faster")
        self.assertAlmostEqual(r["dt"], -0.4, delta=0.05)
        # the lap that was running when the new setup arrived belongs to neither run
        self.assertEqual([v[:2] for v in r["valid"]], [[1, True]] * 4 + [[1, False]] + [[2, True]] * 3)
        # less steering at the limit after the change, in the phases that have enough samples
        steer = [p for p in r["phases"] if p["kind"] == "steer"]
        self.assertTrue(steer, r["phases"])
        for p in steer:
            self.assertAlmostEqual(p["ch"], 1.0 / 1.3 - 1, delta=0.03)
        self.assertIn("Did the change help?", r["html"])
        self.assertIn("Rear ARB blades", r["html"])
        self.assertIn("Faster", r["html"])


PREDICT_JS = r"""
LIVE.ws = {}; LIVE.setup = setupTree(ARGS_YAML); LIVE.meta = { carPath: 'ferrari296gt3', carName: 'Ferrari 296 GT3' };
setupHtml();
const rows = SETUP_ROWS, base = rbSettings(LIVE.setup), lkArb = rows.find(r => r.k === 'arbblades' && r.ax === 'front').lk,
      lkWing = rows.find(r => r.k === 'rearwingangle').lk;
const withV = (o, lk, v) => { const c = JSON.parse(JSON.stringify(o)); c[lk].n = v; return c };
const bal = raw => ({ mid_slow: { raw, cs: 0, n: 400 }, mid_fast: { raw: 1.10, cs: 0, n: 400 }, exit_slow: { raw: 1.0, cs: 0, n: 400 } });
const L = t => [t, t + 0.05, t - 0.05, t + 0.02];
const runs = [
  { id: 'a', track: 'Indy', set: base, laps: L(84.0), bal: bal(1.30) },
  { id: 'b', track: 'Indy', set: withV(base, lkArb, 65), laps: L(83.9), bal: bal(1.20) },                  // front bar 75 -> 65
  { id: 'c', track: 'Indy', set: withV(withV(base, lkArb, 55), lkWing, 6), laps: L(83.5), bal: bal(1.0) }, // two at once: ignored against a and b
];
const samples = rbSamples(runs), sens = rbSens(samples);
RB.cars['ferrari296gt3'] = { runs };
const ui = 1.15, h = { B: { entry_slow: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 }, entry_fast: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 },
  mid_slow: { n: 400, raw: 1.3, ui, cs: 0, abs: 0 }, mid_fast: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 },
  exit_slow: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 }, exit_fast: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 } } };
const P = predictChanges(h, rows, 'ferrari296gt3', 'Indy', sens);
const P0 = predictChanges(h, rows, 'ferrari296gt3', 'Indy', {});
RB.cars['ferrari296gt3'] = { runs: [] };
const P1 = predictChanges(h, rows, 'ferrari296gt3', 'Indy', {});
const pk = p => ({ lab: p.label, from: p.from, to: p.to, d: p.d, basis: p.basis.kind, eff: p.eff, lap: p.lap });
console.log(JSON.stringify({ samples: samples.map(s => [s.lab, s.d, s.ph, s.dt]), sensArb: sens[lkArb] && { n: sens[lkArb].n, per: sens[lkArb].ph.mid_slow.per, dt: sens[lkArb].dt.Indy.per },
  first: P.picks[0] && pk(P.picks[0]), est: P1.picks[0] && pk(P1.picks[0]), estStep: P0.picks[0] && pk(P0.picks[0]), problems: P.problems.map(f => [f.kind, f.p, f.where]) }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class PredictTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + PREDICT_JS)

    def test_only_single_setting_pairs_measure_a_setting(self):
        s = self.r["samples"]
        self.assertEqual(len(s), 1)                                     # a-b only: c differs in two settings from a, and from b
        lab, d, ph, dt = s[0]
        self.assertEqual((lab, d), ("Front ARB blades", -10))
        self.assertAlmostEqual(ph["mid_slow"], 1.20 / 1.30 - 1, places=9)
        self.assertAlmostEqual(dt, -0.1, places=9)
        self.assertAlmostEqual(self.r["sensArb"]["per"], (1.20 / 1.30 - 1) / -10, places=9)

    def test_learned_prediction_has_a_value_and_a_size(self):
        p = self.r["first"]
        per = (1.20 / 1.30 - 1) / -10
        need = 1 / 1.15 - 1
        step = 10                                                       # the values you have run: 75, 65, 55
        d = round(need / per / step) * step
        self.assertEqual(p["basis"], "learned")
        self.assertEqual((p["lab"], p["from"]), ("Front ARB blades", 75))
        self.assertEqual(p["to"], 75 + d)
        self.assertAlmostEqual(p["eff"]["mid_slow"], per * d, places=9)
        self.assertAlmostEqual(p["lap"]["dt"], -0.1 / -10 * d, places=9)

    def test_without_tests_it_is_a_labelled_estimate(self):
        p = self.r["est"]
        self.assertEqual(p["basis"], "estimate")
        self.assertEqual((p["lab"], p["from"], p["to"]), ("Front ARB blades", 75, 66))   # a tenth of 0-90, in 1-degree steps
        self.assertIsNone(p["eff"])


GARAGE_JS = r"""
const car = 'ferrari296gt3';
RB.cars[car] = { runs: [] };
const tree = y => setupTree(y);
const wing9 = ARGS_YAML.split('RearWingAngle: 8 degrees').join('RearWingAngle: 9 degrees').replace('FrontDownforce: 42.9%', 'FrontDownforce: 42.5%');
const rh = ARGS_YAML.split('RideHeight: 53.1 mm').join('RideHeight: 55.1 mm').replace('FrontRhAtSpeed: 52 mm', 'FrontRhAtSpeed: 54 mm').replace('FrontDownforce: 42.9%', 'FrontDownforce: 42.1%');
const both = wing9.split('RideHeight: 53.1 mm').join('RideHeight: 55.1 mm').replace('FrontDownforce: 42.5%', 'FrontDownforce: 41.7%').replace('FrontRhAtSpeed: 52 mm', 'FrontRhAtSpeed: 54 mm');
[ARGS_YAML, wing9, rh, both, ARGS_YAML].forEach(y => gbRecord(car, tree(y)));      // the repeat is not stored twice
const G = gbSens(RB.cars[car].garage);
const lkW = Object.keys(G).find(k => G[k].lab === 'Rear wing angle'), lkR = Object.keys(G).find(k => G[k].lab === 'Ride height');
LIVE.ws = {}; LIVE.setup = tree(ARGS_YAML); LIVE.meta = { carPath: car, carName: 'Ferrari 296 GT3' }; setupHtml();
const rows = SETUP_ROWS, rRH = rows.find(r => r.k === 'rideheight' && r.ax === 'front');
const line = gbPredictHtml({ r: rRH, d: -2 }, G, gbCalc(LIVE.setup));
console.log(JSON.stringify({ stored: RB.cars[car].garage.length, keys: Object.keys(G).length,
  wing: G[lkW] && { n: G[lkW].n, df: G[lkW].per.frontdownforce.per }, rh: G[lkR] && { n: G[lkR].n, df: G[lkR].per.frontdownforce.per, frh: G[lkR].per.frontrhatspeed.per },
  line: line.replace(/<[^>]+>/g, '') }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class GarageCalcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + GARAGE_JS)

    def test_one_click_changes_measure_the_garage_numbers(self):
        r = self.r
        self.assertEqual(r["stored"], 4)
        self.assertEqual(r["keys"], 2)                                # the two-click setup measures nothing on its own...
        self.assertAlmostEqual(r["wing"]["df"], -0.4, places=6)       # ...except against the setups one click away
        self.assertAlmostEqual(r["rh"]["df"], -0.4, places=6)         # both front corners moved together: one change
        self.assertAlmostEqual(r["rh"]["frh"], 1.0, places=6)

    def test_a_prediction_shows_the_garage_numbers_it_leads_to(self):
        line = self.r["line"]
        self.assertIn("front downforce 42.9% → 43.7%", line)
        self.assertIn("front ride height at speed 52 mm → 50.0 mm", line)


BUILD_JS = r"""
const car = 'ferrari296gt3';
LIVE.ws = {}; LIVE.setup = setupTree(ARGS_YAML); LIVE.meta = { carPath: car, carName: 'Ferrari 296 GT3' }; setupHtml();
const rows = SETUP_ROWS, base = rbSettings(LIVE.setup), txt = rbText(LIVE.setup), lkArb = rows.find(r => r.k === 'arbblades' && r.ax === 'front').lk;
const withV = (o, lk, v) => { const c = JSON.parse(JSON.stringify(o)); c[lk].n = v; return c };
const bal = m => ({ mid_slow: { raw: m, cs: 0, n: 400 }, mid_fast: { raw: 1.0, cs: 0, n: 400 }, exit_slow: { raw: 1.0, cs: 0, n: 400 } });
const L = t => [t, t + 0.05, t - 0.05, t + 0.02];
const T = 'Indianapolis Motor Speedway · Road Course';
const runs = [
  { id: 'r1', at: 1, track: T, set: base, txt, laps: L(84.0), bal: bal(1.4) },
  { id: 'r2', at: 2, track: T, set: withV(base, lkArb, 65), txt, laps: L(83.8), bal: bal(1.3) },
];
RB.cars[car] = { runs, garage: [] };
const sens = rbSens(rbSamples(runs)), cur = { set: base, txt };
const A = buildPlan(car, T, runs, cur, rows, sens);
const B = buildPlan(car, 'Barber Motorsports Park', runs, cur, rows, sens);
const val = (P, k, ax) => { const id = Object.keys(P.set).find(i => P.set[i].k === k && (!ax || P.set[i].ax === ax)); return P.set[id].n };
console.log(JSON.stringify({
  A: { base: A.base && A.base.id, why: A.why, arb: val(A, 'arbblades', 'front'), wing: val(A, 'rearwingangle'), changes: A.changes.map(c => [c.basis, c.d, c.why]) },
  B: { base: B.base && B.base.id, why: B.why, wing: val(B, 'rearwingangle'), changes: B.changes.map(c => [c.basis, c.d, c.why]), tyre: Object.values(B.txt).includes('Dry'), trim: B.trim } }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class BuildSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + BUILD_JS)

    def test_same_track_starts_from_the_fastest_run_and_applies_the_learned_fix(self):
        a = self.r["A"]
        self.assertEqual(a["base"], "r2")
        per = (1.3 / 1.4 - 1) / -10
        d = round((1 / 1.3 - 1) / per / 10) * 10                       # steps of 10, the values run
        self.assertEqual(a["arb"], 65 + d)
        self.assertEqual(a["wing"], 8)                                 # same track: wing untouched
        self.assertEqual([c[0] for c in a["changes"]], ["learned"])

    def test_new_track_moves_the_wing_for_its_downforce_level(self):
        b = self.r["B"]
        self.assertEqual(b["trim"]["name"], "High")
        self.assertEqual(b["wing"], 10)                                # Medium (Indy) -> High: +2 degrees, estimate
        self.assertIn(["estimate", 2], [c[:2] for c in b["changes"]])
        self.assertTrue(b["tyre"])                                     # text settings carried into the sheet


CAR_MANUAL_JS = r"""
// every car in the table is found by its own name, and not by an earlier, wider entry
const own = CAR_KB.map(e => [e.n, carKb('', e.n) && carKb('', e.n).n]);
const names = ['Ferrari 296 GT3', 'Ferrari 296 Challenge', 'Chevrolet Corvette Z06 GT3.R', 'Aston Martin Vantage GT3 EVO', 'Aston Martin Vantage GT4',
  'Mercedes-AMG GT3 2020', 'Mercedes-AMG GT3', 'Mercedes-AMG GT4', 'Audi R8 LMS EVO II GT3', 'Audi R8 LMS GT3', 'Ford GT GT3', 'Ford Mustang GT3',
  'Porsche 911 GT3 R (992)', 'Porsche 911 GT3 R', 'Porsche 911 Cup (992.2)', 'Porsche 911 GT3 Cup (992)', 'Ferrari 488 GT3 Evo 2020', 'Ferrari 488 GT3',
  'BMW M4 G82 GT4', 'BMW M4 GT4', 'BMW M4 GT3', 'McLaren 720S GT3 EVO', 'McLaren MP4-12C GT3', 'Dallara P217', 'BMW M Hybrid V8', 'Mazda MX-5 Cup'];
const found = names.map(n => { const k = carKb('', n); return [n, k ? k.n : null] });
// suggestions in the right direction on each car, on the same garage page (only the car changes)
function sugg(path, name, yaml) {
  LIVE.ws = {}; LIVE.setup = setupTree(yaml); LIVE.meta = { carPath: path, carName: name }; setupHtml();
  const rows = SETUP_ROWS, ar = c => { const x = adviceRow(rows, c, path); return x && { word: x.word, nd: x.nd, at: x.at } };
  const h = { B: { entry_slow: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 }, entry_fast: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 },
    mid_slow: { n: 400, raw: 1.3, ui: 1.3, cs: 0, abs: 0 }, mid_fast: { n: 400, raw: 1.3, ui: 1.3, cs: 0, abs: 0 },
    exit_slow: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 }, exit_fast: { n: 400, raw: 1, ui: 1, cs: 0, abs: 0 } } };
  RB.cars[path] = { runs: [] };
  const P = predictChanges(h, rows, path, 'Indy', {}), arb = P.picks.find(p => p.k === 'arbblades' && p.ax === 'front');
  return { softFront: ar(['arbblades', 'front', -1]), stiffRear: ar(['arbblades', 'rear', 1]), tc: ar(['tcsetting', '', 0]), pick: arb && { from: arb.from, to: arb.to } };
}
const y3 = ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 3 degrees').replace('ArbBlades: 90 degrees', 'ArbBlades: 3 degrees');
const out = {
  ferrari: sugg('ferrari296gt3', 'Ferrari 296 GT3', ARGS_YAML),
  vette: sugg('chevyvettez06rgt3', 'Chevrolet Corvette Z06 GT3.R', y3),
  aston: sugg('amvantageevogt3', 'Aston Martin Vantage GT3 EVO', y3),
  merc: sugg('mercedesamgevogt3', 'Mercedes-AMG GT3 2020', y3),
  cup: sugg('porsche9922cup', 'Porsche 911 Cup (992.2)', ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 5 degrees')),
  mcl: sugg('mclaren720sgt3', 'McLaren 720S GT3 EVO', y3),
};
const notes = kbNotesHtml(carKb('', 'Chevrolet Corvette Z06 GT3.R'), 'Daytona International Speedway').replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const nsx = kbTrim(carKb('', 'Acura NSX GT3 EVO 22'), 'Daytona International Speedway'), gt4 = kbTrim(carKb('', 'BMW M4 G82 GT4'), 'Road America');
console.log(JSON.stringify({ own, found, out, notes, nsx, gt4 }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class CarManualTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + CAR_MANUAL_JS)

    def test_every_car_matches_its_own_entry(self):
        for n, got in self.r["own"]:
            self.assertEqual(got, n)
        for n, got in self.r["found"]:
            self.assertIsNotNone(got, n)
        found = dict(self.r["found"])
        self.assertEqual(found["Mercedes-AMG GT3"], "Mercedes-AMG GT3")          # not the 2020 car
        self.assertEqual(found["Porsche 911 GT3 R"], "Porsche 911 GT3 R")        # not the 992
        self.assertEqual(found["Ferrari 488 GT3"], "Ferrari 488 GT3")            # not the EVO
        self.assertEqual(found["Ford Mustang GT3"], "Ford Mustang GT3")          # not the Ford GT

    def test_plain_car_higher_is_stiffer(self):
        f = self.r["out"]["ferrari"]
        self.assertEqual((f["softFront"]["nd"], f["stiffRear"]["nd"]), (-1, 1))
        self.assertEqual(f["pick"], {"from": 75, "to": 66})
        self.assertEqual(f["tc"]["nd"], 1)                                     # the 296: higher = more help

    def test_corvette_blades_run_the_other_way(self):
        v = self.r["out"]["vette"]
        self.assertEqual((v["softFront"]["nd"], v["stiffRear"]["nd"]), (1, -1))
        self.assertIn("a higher number on this car", v["softFront"]["word"])
        self.assertGreater(v["pick"]["to"], v["pick"]["from"])                 # softer front = a higher number

    def test_aston_only_the_front_bar_is_reversed(self):
        a = self.r["out"]["aston"]
        self.assertEqual((a["softFront"]["nd"], a["stiffRear"]["nd"]), (1, 1))

    def test_mercedes_traction_control_more_help_is_a_lower_number(self):
        m = self.r["out"]["merc"]
        self.assertEqual(m["tc"]["nd"], -1)
        self.assertIn("lower number is more help", m["tc"]["word"])

    def test_porsche_cup_bar_positions_follow_the_manual_order(self):
        c = self.r["out"]["cup"]
        self.assertEqual(c["pick"], {"from": 5, "to": 7})                      # front: 1-2-3-4-7-5-6, softer than 5 is 7
        self.assertEqual(c["softFront"]["nd"], -1)

    def test_unclear_direction_is_said_not_guessed(self):
        m = self.r["out"]["mcl"]
        self.assertEqual(m["softFront"]["nd"], 0)
        self.assertIn("unclear", m["softFront"]["word"])
        self.assertIsNone(m.get("pick"))

    def test_notes_and_trims(self):
        n = self.r["notes"]
        self.assertIn("Runs the other way on this car", n)
        self.assertIn("wing 9.5", n)
        self.assertIn("Downforce for Daytona International Speedway: Low/Medium", n)
        self.assertEqual(self.r["nsx"]["name"], "Low")                         # the Acura's own difference from the GT3 list
        self.assertEqual(self.r["gt4"]["name"], "Standard (wing 3-5)")


ENGINEER_JS = r"""
LIVE.ws = {}; LIVE.state = 'bridge';
let Tm = 100, lap = 1;
// mid-corner understeer of Fmid at the limit; entry and exit neutral
function drive(yaml, laps, Fmid, lapTime) {
  if (yaml) liveSession(yaml);
  for (let L = 0; L < laps; L++, lap++) {
    const n = Math.round(lapTime * 60);
    for (let i = 0; i < n; i++) {
      const d = i / n, ph = (d * 8) % 1, g = 1.25 * Math.abs(Math.sin(d * 8 * Math.PI)), v = 34 + 12 * (Math.floor(d * 8) % 2);
      const yaw = g * 9.80665 / v * (Math.floor(d * 16) % 2 ? 1 : -1), k = yaw / v;
      const brake = ph < 0.18 ? 0.4 : 0, thr = ph > 0.30 ? 0.8 : 0.2, mid = !brake && thr < 0.5;
      const steer = 40 * k * (g > 0.8 && mid ? Fmid : 1);
      liveTick({ Lap: lap, LapDistPct: d, LapCurrentLapTime: i / 60, LapLastLapTime: lap > 1 ? lapTime : -1, Speed: v, Brake: brake, Throttle: thr,
                 SteeringWheelAngle: steer, YawRate: yaw, LatAccel: g * 9.80665 * Math.sign(yaw), OnPitRoad: false, SessionTime: Tm });
      Tm += 1 / 60;
    }
  }
  liveTick({ Lap: lap, LapDistPct: 0.001, LapCurrentLapTime: 0.01, LapLastLapTime: lapTime, Speed: 40, Brake: 0, Throttle: 1, SteeringWheelAngle: 0, YawRate: 0, LatAccel: 0, OnPitRoad: false, SessionTime: Tm });
}
const txt = () => engHtml().replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const off = txt();
ENG.on = true; ENG.laps = 6;
drive(ARGS_YAML, 5, 1.4, 60.0);
const at5 = Object.keys(engPlans()).length, h5 = txt();
drive(null, 1, 1.4, 60.0);
const p1 = engPlans()[1], h1 = txt();
// a wrong value first, then the right one; the lap running when the setup changes does not count
liveSession(ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 70 degrees'));
const hWrong = txt();
const y2 = ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 66 degrees');
drive(y2, 7, 1.15, 59.6);
const p2 = engPlans()[3], h2 = txt();
// the next step makes it slower: it goes back, and the next fix is something else
const y3 = y2.replace('ArbBlades: 66 degrees', 'ArbBlades: ' + p2.items[0].to + ' degrees');
drive(y3, 7, 1.0, 60.4);
const p3 = engPlans()[4], h3 = txt();
const strip = p => p && { items: p.items.map(i => [i.kind, i.lab, i.from, i.to, i.basis || '']), call: p.verdict && p.verdict.call };
console.log(JSON.stringify({ off, at5, h5, p1: strip(p1), h1, hWrong, p2: strip(p2), h2, p3: strip(p3), h3, bad: ENG.bad, saved: JSON.parse(localStorage.getItem('engineer')) }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class EngineerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + ENGINEER_JS)

    def test_off_until_started(self):
        self.assertIn("Start", self.r["off"])
        self.assertIn("Drive 6 clean laps", self.r["off"])

    def test_a_plan_after_six_clean_laps(self):
        r = self.r
        self.assertEqual(r["at5"], 0)
        self.assertIn("Lap 5 of 6", r["h5"])
        self.assertEqual(r["p1"]["items"], [["fix", "Front ARB blades", 75, 66, "estimate"]])
        self.assertIn("Box now", r["h1"])
        self.assertIn("75 degrees → 66 degrees", r["h1"])

    def test_checks_what_was_entered_in_the_garage(self):
        self.assertIn("Not set yet", self.r["hWrong"])
        self.assertIn("should be 66 degrees , now 70 degrees", self.r["hWrong"])

    def test_faster_keeps_the_change_and_learns_the_size(self):
        p2 = self.r["p2"]
        self.assertEqual(p2["call"], "faster")
        kind, lab, frm, to, basis = p2["items"][0]
        self.assertEqual((kind, lab, frm, basis), ("fix", "Front ARB blades", 66, "learned"))
        self.assertLess(to, 66)
        self.assertIn("Faster", self.r["h2"])

    def test_slower_puts_it_back_and_tries_something_else(self):
        p3, p2 = self.r["p3"], self.r["p2"]
        self.assertEqual(p3["call"], "slower")
        self.assertEqual(p3["items"][0], ["back", "Front ARB blades", p2["items"][0][3], 66, ""])
        self.assertEqual(len(p3["items"]), 2)
        self.assertNotEqual(p3["items"][1][1], "Front ARB blades")              # not the same change again
        self.assertIn("the changes go back", self.r["h3"])
        self.assertEqual(self.r["saved"]["bad"]["ferrari296gt3|Indianapolis Motor Speedway"], {"arbblades@front-": 66})


LIVE_RH_JS = r"""
LIVE.ws = {}; LIVE.state = 'bridge';
liveSession(ARGS_YAML);
const base = { Lap: 1, LapDistPct: 0.5, LapCurrentLapTime: 1, LapLastLapTime: -1, Brake: 0, Throttle: 0, SteeringWheelAngle: 0, YawRate: 0, LatAccel: 0, OnPitRoad: true };
const defl = (lf, rr) => ({ LFshockDefl: lf, RFshockDefl: lf, LRshockDefl: rr, RRshockDefl: rr });
// no rest seen yet on this setup: nothing shown rather than a guess
liveTick(Object.assign({}, base, { Speed: 30 }, defl(0.050, 0.050)));
const before = [liveRh(LIVE.v, 'LF'), LIVE.rhSrc];
liveTick(Object.assign({}, base, { Speed: 0 }, defl(0.045, 0.048)));     // standing in the pits
liveTick(Object.assign({}, base, { Speed: 60, OnPitRoad: false }, defl(0.050, 0.046)));
const lf = liveRh(LIVE.v, 'LF'), rr = liveRh(LIVE.v, 'RR'), src = LIVE.rhSrc;
// a measured channel (replays of .ibt files) is used as it is
const meas = liveRh(Object.assign({ LFrideHeight: 0.0412 }, LIVE.v), 'LF');
// a new setup needs a new rest reading
liveSession(ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 70 degrees'));
liveTick(Object.assign({}, base, { Speed: 60, OnPitRoad: false }, defl(0.050, 0.046)));
const after = [liveRh(LIVE.v, 'LF'), LIVE.rhSrc];
// another car without a calibration
LIVE.meta.carPath = 'somethingelse';
const nocal = [liveRh(LIVE.v, 'LF'), LIVE.rhSrc];
console.log(JSON.stringify({ before, lf, rr, src, meas, after, nocal }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class LiveRideHeightTest(unittest.TestCase):
    """iRacing's live telemetry has no ride height channel; it is worked out from shock travel."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + LIVE_RH_JS)

    def test_from_shock_travel_anchored_at_rest(self):
        r = self.r
        self.assertEqual(r["before"], [None, "norest"])
        self.assertEqual(r["src"], "shock")
        self.assertAlmostEqual(r["lf"], 0.0531 - 1.571 * 0.005, places=9)     # garage 53.1 mm, 5 mm more travel
        self.assertAlmostEqual(r["rr"], 0.0604 - 1.446 * -0.002, places=9)
        self.assertAlmostEqual(r["meas"], 0.0412, places=9)

    def test_new_setup_or_unknown_car_shows_nothing(self):
        self.assertEqual(self.r["after"], [None, "norest"])
        self.assertEqual(self.r["nocal"], [None, "nocal"])

    @unittest.skipUnless(os.path.exists(REAL_IBT), "the real Indianapolis file is not here")
    def test_slope_fitted_from_a_real_file(self):
        r = run_js("const ib = ibt(readBytes(ARGS[0])); console.log(JSON.stringify(rhFit(ib)));", REAL_IBT)
        for c, k in {"LF": -1.571, "RF": -1.601, "LR": -1.461, "RR": -1.446}.items():
            self.assertAlmostEqual(r[c], k, delta=0.01)


ENGINEER_WAIT_JS = ENGINEER_JS.split("const txt = ")[0] + r"""
const txt = () => engHtml().replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
ENG.on = true; ENG.laps = 3;
// straight-line laps: nothing to judge the balance on
function cruise(laps) { for (let L = 0; L < laps; L++, lap++) { const n = 3600; for (let i = 0; i < n; i++) liveTick({ Lap: lap, LapDistPct: i / n, LapCurrentLapTime: i / 60, LapLastLapTime: 60, Speed: 40, Brake: 0, Throttle: 1, SteeringWheelAngle: 0, YawRate: 0, LatAccel: 0, OnPitRoad: false, SessionTime: Tm += 1 / 60 }) }
  liveTick({ Lap: lap, LapDistPct: 0.001, LapCurrentLapTime: 0.01, LapLastLapTime: 60, Speed: 40, Brake: 0, Throttle: 1, SteeringWheelAngle: 0, YawRate: 0, LatAccel: 0, OnPitRoad: false, SessionTime: Tm }) }
liveSession(ARGS_YAML);
cruise(4);
const waiting = Object.keys(engPlans()).length, hw = txt();
drive(null, 6, 1.4, 60.0);
const p = engPlans()[1];
console.log(JSON.stringify({ waiting, hw, items: p && p.items.map(i => [i.lab, i.from, i.to]) }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class EngineerWaitsForCorneringTest(unittest.TestCase):
    def test_no_frozen_plan_without_a_measured_balance(self):
        r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + ENGINEER_WAIT_JS)
        self.assertEqual(r["waiting"], 0)
        self.assertIn("not enough cornering yet", r["hw"])
        self.assertEqual(r["items"], [["Front ARB blades", 75, 66]])


COACH_JS = r"""
// a 2 km track with two corners; laps differ only in where the driver brakes for corner 1
function lap(brake1, who) {
  const n = 2000, len = 2000, D = new Float64Array(n), S = new Float64Array(n), Br = new Float64Array(n), Th = new Float64Array(n), t = new Float64Array(n);
  const corners = [[0.35, brake1], [0.75, 0.70]];        // [apex, braking point]
  for (let i = 0; i < n; i++) {
    const d = i / (n - 1); D[i] = d; let v = 60, br = 0, th = 1;
    for (const [ap, bp] of corners) {
      if (d >= bp && d < ap) { const f = (d - bp) / (ap - bp); v = 60 - 35 * f; br = 0.9; th = 0 }
      else if (d >= ap && d < ap + 0.05) { const f = (d - ap) / 0.05; v = 25 + 35 * f; br = 0; th = f > 0.2 ? 1 : 0.3 }
    }
    S[i] = v; Br[i] = br; Th[i] = th;
    t[i] = i ? t[i - 1] + (len / (n - 1)) / ((S[i] + S[i - 1]) / 2) : 0;
  }
  const z = () => new Float64Array(n);
  return { n, D, Speed: S, Brake: Br, Throttle: Th, t, lap: t[n - 1], Steer: z(), Gear: z(), Lat: z(), Lon: z(), RPM: z(), ABS: z(), LatG: z(), LonG: z(), VertG: z(), YawRate: z(), who, ext: {}, meta: {} };
}
const best = lap(0.31, 'best');
const laps = [lap(0.28, 'a'), lap(0.285, 'b'), lap(0.29, 'c'), lap(0.31, 'd'), lap(0.28, 'e')];
const avg = avgLap(laps);
const C = coachCompare(avg, best, laps);
const c1 = C.corners.find(c => Math.abs(c.ap - 350) < 40), c2 = C.corners.find(c => Math.abs(c.ap - 750) < 40);
// live laps are kept compactly and come back as laps
LIVE.meta = { carPath: 'ferrari296gt3', who: 'Me' }; LIVE.yaml = 'TrackDisplayName: Test Raceway';
const b = { d: [], t: [], Speed: [], Brake: [], thr: [], str: [], gear: [], rpm: [], LatG: [], lon: [], yaw: [], abs: [] };
for (let i = 0; i < best.n; i += 3) { b.d.push(best.D[i]); b.t.push(best.t[i]); b.Speed.push(best.Speed[i]); b.Brake.push(best.Brake[i]); b.thr.push(best.Throttle[i]);
  ['str', 'gear', 'rpm', 'LatG', 'lon', 'yaw', 'abs'].forEach(k => b[k].push(0)) }
coachSaveLive({ b, lastT: best.lap, lap: 7, ver: 1 });
const saved = coachLiveLoad()[coachLiveKey()], back = coachFromSaved(saved[0]);
const C2 = coachCompare(avgLap([back]), best, [back]);
console.log(JSON.stringify({ avgLap: avg.lap, meanLap: laps.reduce((s, l) => s + l.lap, 0) / laps.length, spread: avg.spread, bestLap: best.lap, total: C.total,
  c1: c1 && { dt: c1.dt, d: c1.d, tips: c1.tips.map(t => t.k) }, c2: c2 && { dt: c2.dt, tips: c2.tips.map(t => t.k) },
  savedN: saved.length, savedPts: saved[0].D.length, size: JSON.stringify(saved[0]).length, roundTrip: C2.total }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class CoachTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_js(COACH_JS)

    def test_average_lap_is_the_mean_time_and_typical_inputs(self):
        r = self.r
        self.assertAlmostEqual(r["avgLap"], r["meanLap"], places=6)
        self.assertGreater(r["spread"], 0)
        self.assertAlmostEqual(r["total"], r["meanLap"] - r["bestLap"], places=6)

    def test_finds_the_corner_and_says_brake_later(self):
        c1, c2 = self.r["c1"], self.r["c2"]
        self.assertGreater(c1["dt"], 0.05)
        self.assertLess(c1["d"], -30)                         # the median lap brakes ~50 m earlier
        self.assertEqual(c1["tips"][0], "brake_later")
        self.assertIn("consistency", c1["tips"])              # braking point moves lap to lap
        self.assertLess(abs(c2["dt"]), 0.02)                  # corner 2 is the same in every lap
        self.assertEqual(c2["tips"], [])

    def test_live_laps_are_saved_small_and_come_back(self):
        r = self.r
        self.assertEqual((r["savedN"], r["savedPts"]), (1, 250))
        self.assertLess(r["size"], 20000)
        self.assertLess(abs(r["roundTrip"]), 0.05)           # the same lap against itself


ENG_COLOUR_JS = ENGINEER_JS.split("const txt = ")[0] + r"""
ENG.on = true; ENG.laps = 6;
drive(ARGS_YAML, 6, 1.4, 60.0);
const plan = engPlans()[1];                                        // front bar 75 -> 66
const chk = y => engCheckHtml(plan, setupTree(y)).replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
const toward = chk(ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 70 degrees'));
const wrong = chk(ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 80 degrees'));
const untouched = chk(ARGS_YAML);
const done = chk(ARGS_YAML.replace('ArbBlades: 75 degrees', 'ArbBlades: 66 degrees').split('RearWingAngle: 8 degrees').join('RearWingAngle: 9 degrees'));
console.log(JSON.stringify({ toward, wrong, untouched, done }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class EngineerColoursTest(unittest.TestCase):
    """What the engineer asked for, against what is in the sim right now."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_js("const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + ENG_COLOUR_JS)

    def test_each_state_is_named(self):
        r = self.r
        self.assertIn("should be 66 degrees , now 70 degrees (on its way)", r["toward"])
        self.assertIn("now 80 degrees (the wrong way)", r["wrong"])
        self.assertIn("(not changed yet)", r["untouched"])
        self.assertIn("The change is in.", r["done"])
        self.assertIn("Rear wing angle 8 degrees → 9 degrees (changed, not in the plan)", r["done"])


@unittest.skipUnless(NODE, "Node.js is not installed")
class CoachThemesTest(unittest.TestCase):
    def test_gap_split_by_cause(self):
        js = r"""
const C = { total: 0.9, between: 0.2, corners: [
  { n: 4, dt: 0.21, tips: [{ k: 'brake_later' }, { k: 'coast' }] },
  { n: 2, dt: 0.20, tips: [{ k: 'apex_speed' }] },
  { n: 3, dt: 0.06, tips: [{ k: 'over_brake' }] },
  { n: 1, dt: 0.10, tips: [] },
  { n: 6, dt: 0.01, tips: [{ k: 'coast' }] } ] };
console.log(JSON.stringify({ t: coachThemes(C).map(t => [t.k, +t.dt.toFixed(3), t.corners]) }));
"""
        self.assertEqual(run_js(js)["t"], [["braking", 0.27, [4, 3]], ["mid-corner speed", 0.2, [2]], ["straights", 0.2, []],
                                      ["line and steering", 0.1, [1]]])


@unittest.skipUnless(NODE, "Node.js is not installed")
class CoachConditionsTest(unittest.TestCase):
    def test_laps_grouped_by_fuel_tyres_and_track(self):
        js = r"""
// a 10-lap stint from full, a stop, then 4 more laps; the track warms up
const conds = [];
for (let i = 1; i <= 10; i++) conds.push({ f0: 60 - 5 * (i - 1), stint: i, tt: 30 + i * 0.5 });
for (let i = 1; i <= 4; i++) conds.push({ f0: 30 - 5 * (i - 1), stint: i, tt: 36 });
conds.push(null);                                  // a Garage61 lap: no conditions
const G = coachConditions(conds), ids = Object.fromEntries(Object.entries(G).map(([k, g]) => [k, g.idx]));
console.log(JSON.stringify({ ids, none: coachConditions([{}, {}, null]) }));
"""
        r = run_js(js)
        ids = r["ids"]
        self.assertEqual(ids["heavy"], [0, 1, 2, 3])            # 45-60 L: top third of 15-60 L
        self.assertEqual(ids["light"], [6, 7, 8, 9, 10, 11, 12, 13])   # 15-30 L
        self.assertEqual(ids["cold"], [0, 1, 10, 11])           # first two laps of each stint
        self.assertEqual(ids["late"], [6, 7, 8, 9])             # lap 7+ (two thirds of 10)
        self.assertEqual(set(ids["hot"]) | set(ids["cool"]), set(range(14)))
        self.assertEqual(ids["optimal"], [4, 5, 12, 13])        # not heavy, not cold, not late
        self.assertEqual(r["none"], {})


@unittest.skipUnless(NODE and os.path.exists(REAL_IBT), "needs Node and the real Indianapolis file")
class CoachPageRealFileTest(unittest.TestCase):
    """The coaching page on Andrew's real Indianapolis laps: every section is there and says something."""

    @classmethod
    def setUpClass(cls):
        js = r"""
IB.a = ibt(readBytes(ARGS[0]));
COACH.src = 'ibt'; COACH.to = 'best';
const X = coachRun(), SD = coachSetupData(X), SDiff = coachStyleDiff(X.C.R, X.laps), PR = coachPriorities(X, SD, SDiff), html = coachBody(X);
const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');
console.log(JSON.stringify({ n: X.n, gap: X.C.total, kinds: PR.map(p => p.kind), gains: PR.map(p => +p.gain.toFixed(3)),
  titles: PR.map(p => p.title), notes: Object.keys(SDiff).length, svgs: (html.match(/<svg/g) || []).length,
  hasMap: html.indexOf('Track map coloured') >= 0, text: text.slice(0, 600), sections: ['Your next session', 'Where the time goes', 'Corner by corner', 'Consistency', 'Track limits', 'Setup'].map(s => text.indexOf(s) >= 0),
  c4: (X.C.corners.find(c => c.n === 4).cause || { steps: [] }).steps.map(s => s.k), lineMaps: (html.match(/aria-label="Your line through the corner/g) || []).length, offs: coachOffs(IB.a, X.C.R.cs) }));
"""
        cls.r = run_js(js, REAL_IBT)

    def test_sections_and_visuals(self):
        r = self.r
        self.assertEqual(r["sections"], [True] * 6)
        self.assertGreaterEqual(r["lineMaps"], 3)             # the three things and the corner cards
        self.assertTrue(r["hasMap"])
        self.assertGreaterEqual(r["svgs"], 3)                 # delta trace, map, a corner trace

    def test_corner_four_brakes_and_turns_in_early(self):
        # measured on the real laps: 9 m early on the brake and the turn-in, 3.9 m tight on the exit
        self.assertEqual(self.r["c4"], ["brake_later", "turn_later", "use_exit"])

    def test_track_limits_count_every_timed_lap(self):
        o = self.r["offs"]
        self.assertEqual(o["offsN"], 4)
        self.assertEqual(o["offs"], {"9": 1, "13": 3})
        self.assertEqual(o["incN"], 9)

    def test_priorities_mix_driving_and_setup_ranked_by_time(self):
        r = self.r
        self.assertIn("drive", r["kinds"])
        self.assertIn("setup", r["kinds"])                    # the laps understeer mid-corner
        drive = [g for k, g in zip(r["kinds"], r["gains"]) if k == "drive"]
        self.assertEqual(drive, sorted(drive, reverse=True))
        self.assertGreater(r["notes"], 0)                     # technique differences against the best lap


FACTS_JS = r"""
// a 2 km circular track (always turning left), two corners made by braking; GPS, steering, wheels and kerbs
function lap(o) {
  const n = 2000, len = 2000, Rr = len / (2 * Math.PI), la0 = 39.79, lo0 = -86.23, D = new Float64Array(n), S = new Float64Array(n), Br = new Float64Array(n), Th = new Float64Array(n), t = new Float64Array(n),
    St = new Float64Array(n), Lat = new Float64Array(n), Lon = new Float64Array(n), W = { LF: new Float64Array(n), RF: new Float64Array(n), LR: new Float64Array(n), RR: new Float64Array(n) },
    K = { LF: new Float64Array(n), RF: new Float64Array(n), LR: new Float64Array(n), RR: new Float64Array(n) }, TS = new Float64Array(n).fill(3);
  const corners = [[0.35, o.brake, o.turn], [0.75, 0.70, 0.73]];
  for (let i = 0; i < n; i++) {
    const d = i / (n - 1); D[i] = d; let v = 60, br = 0, th = 1, st = 0, out = 0;
    for (const [ap, bp, tp] of corners) {
      if (d >= bp && d < ap) { const f = (d - bp) / (ap - bp); v = 60 - 35 * f; br = 0.9 * (1 - 0.5 * f); th = 0 }
      else if (d >= ap && d < ap + 0.05) { const f = (d - ap) / 0.05; v = 25 + 35 * f; br = 0; th = f > 0.2 ? 1 : 0.3 }
      if (d >= tp && d <= ap) st = 0.5 * (d - tp) / (ap - tp); else if (d > ap && d < ap + 0.05) st = 0.5 * (1 - (d - ap) / 0.05);
    }
    if (o.tight && d >= 0.36 && d < 0.40) out = -2;                    // 2 m inside on the exit of corner 1
    S[i] = v; Br[i] = br; Th[i] = th; St[i] = st;
    const th0 = 2 * Math.PI * d, r = Rr + out; Lon[i] = lo0 + r * Math.cos(th0) / (111320 * Math.cos(la0 * Math.PI / 180)); Lat[i] = la0 + r * Math.sin(th0) / 110574;
    for (const k in W) W[k][i] = v * 0.97;
    if (o.spin && d >= 0.36 && d < 0.38) { W.LR[i] = W.RR[i] = v * 1.15 }
    if (o.kerb && d >= 0.37 && d < 0.39) for (const k in K) K[k][i] = 25;
    if (o.off && d >= 0.38 && d < 0.385) TS[i] = 0;
    t[i] = i ? t[i - 1] + (len / (n - 1)) / ((S[i] + S[i - 1]) / 2) : 0;
  }
  const z = () => new Float64Array(n), ext = { TireLF_RumblePitch: K.LF, TireRF_RumblePitch: K.RF, TireLR_RumblePitch: K.LR, TireRR_RumblePitch: K.RR, PlayerTrackSurface: TS };
  for (const k in W) ext[k + 'speed'] = W[k];
  return { n, D, Speed: S, Brake: Br, Throttle: Th, t, lap: t[n - 1], Steer: St, Gear: z().fill(3), Lat, Lon, RPM: z(), ABS: z(), LatG: z(), LonG: z(), VertG: z(), YawRate: z(), who: 'x', ext, meta: {} };
}
const best = lap({ brake: 0.31, turn: 0.33, kerb: true });
const laps = [0, 1, 2, 3, 4].map(i => lap({ brake: 0.29, turn: 0.32, tight: true, spin: true, off: i === 0 }));
const C = coachCompare(avgLap(laps), best, laps), c1 = C.corners.find(c => Math.abs(c.ap - 350) < 40), c2 = C.corners.find(c => Math.abs(c.ap - 750) < 40);
const y = c1.fx.y, b = c1.fx.b;
// practice: the best lap graded against itself, and a lap that brakes early
const T = [pracTarget(c1, 'Corner 1')], gb = pracGrade(best, T)[0], gl = pracGrade(laps[1], T)[0];
// track limits from a stand-in .ibt: two timed laps, one goes off at 38% and picks up 1x
const rows = [];for (let l = 0; l < 2; l++) for (let i = 0; i < 600; i++) { const d = i / 599; rows.push({ d, ts: l === 1 && d > 0.38 && d < 0.39 ? 0 : 3, ic: l === 1 && d > 0.385 ? 1 : 0 }) }
const V = { LapDistPct: 'd', PlayerTrackSurface: 'ts', PlayerCarMyIncidentCount: 'ic' }, ib = { _rd: (v, r) => rows[r][v], _V: V, laps: [{ ok: true, s: 0, e: 600 }, { ok: true, s: 600, e: 1200 }] };
const O = coachOffs(ib, C.R.cs);
// progress: two sessions, corner 1 better in the second
const ph = coachProgressHtml([{ at: 1, n: 5, avg: 90, best: 89, gap: 1, spread: .3, cs: { 1: .4, 2: .1 } }, { at: 2, n: 5, avg: 89.5, best: 89, gap: .5, spread: .2, cs: { 1: .1, 2: .1 } }]);
console.log(JSON.stringify({ c1n: c1.n, steps: c1.cause && c1.cause.steps.map(s => s.k), head: c1.cause && c1.cause.head, c2: c2.cause, d_ba: (y.ba - b.ba) * c1.mpg, d_tin: (y.tin - b.tin) * c1.mpg,
  line: y.line, kerbB: b.kerb, kerbY: y.kerb, spinY: y.spin, spinB: b.spin, lockY: y.lock, offY: y.offTrack, trail: y.trail,
  gb: [gb.dct, gb.dms, gb.dba, gb.ok], gl: [gl.dct > 0.05, gl.dba, gl.ok.brake], offs: O, prog: [ph.indexOf('<svg') >= 0, /Better since[^<]*Corner 1/.test(ph)], cons: coachConsist(C).map(x => x.n) }));
"""


@unittest.skipUnless(NODE, "Node.js is not installed")
class CoachCauseTest(unittest.TestCase):
    """What a driver does differently in a corner, measured from the inputs, GPS, wheels and kerbs:
    laps that brake 40 m early, turn in early, run 2 m tight on the exit, spin the rears and miss the
    exit kerb the best lap uses."""

    @classmethod
    def setUpClass(cls):
        cls.r = run_js(FACTS_JS)

    def test_the_cause_in_the_order_you_meet_it(self):
        r = self.r
        self.assertEqual(r["steps"], ["brake_later", "turn_later", "spin"])   # the three biggest, in corner order
        self.assertEqual(r["head"], "Brake later, then turn in later")
        self.assertIsNone(r["c2"])                                             # the same in every lap: nothing to say

    def test_measured_against_the_best(self):
        r = self.r
        self.assertAlmostEqual(r["d_ba"], -40, delta=3)        # brakes 0.29 instead of 0.31 of 2 km
        self.assertAlmostEqual(r["d_tin"], -14, delta=3)       # steering starts 0.01 earlier; 30% of the peak 0.0072 earlier
        self.assertAlmostEqual(r["line"]["ex"], 2, delta=.2)   # 2 m inside (tighter) at the track-out
        self.assertAlmostEqual(r["line"]["in"], 0, delta=.2)
        self.assertEqual(r["trail"], 1)

    def test_wheels_kerbs_and_track_limits(self):
        r = self.r
        self.assertEqual(r["kerbB"], {"in": False, "ap": False, "ex": True})
        self.assertEqual(r["kerbY"]["ex"], 0)
        self.assertEqual((r["spinY"], r["spinB"], r["lockY"]), (1, False, 0))
        self.assertAlmostEqual(r["offY"], 0.2)                  # one lap of five leaves the track
        self.assertEqual(r["offs"], {"laps": 2, "offs": {"1": 1}, "inc": {"1": 1}, "offsN": 1, "incN": 1})

    def test_practice_grades_a_lap_against_the_best(self):
        r = self.r
        dct, dms, dba, ok = r["gb"]
        self.assertEqual((round(dct, 6), round(dms, 6), round(dba, 6)), (0, 0, 0))
        self.assertTrue(all(ok.values()))
        self.assertTrue(r["gl"][0])                              # the early-braking lap is slower through the corner
        self.assertAlmostEqual(r["gl"][1], -40, delta=3)
        self.assertFalse(r["gl"][2])

    def test_progress_chart(self):
        self.assertEqual(self.r["prog"], [True, True])


@unittest.skipUnless(NODE, "Node.js is not installed")
class CornerFinderTest(unittest.TestCase):
    """Corners from where the car turns: esses split, a double apex split, a loaded kink kept, a twitch ignored."""

    def test_shapes(self):
        js = r"""
const G = 9.80665, B = { Speed: new Float64Array(N).fill(60), LatG: new Float64Array(N) };
const turn = (a, b, g, vmin) => { for (let i = a; i <= b; i++) { const f = Math.sin((i - a) / (b - a) * Math.PI); B.LatG[i] = g * G * f; B.Speed[i] = 60 - (60 - vmin) * f } };
turn(100, 140, -1.5, 25);                       // a left
turn(141, 175, 1.4, 28);                        // straight into a right: esses
turn(300, 340, -1.3, 30); turn(341, 400, -1.3, 32);   // one long left with two dips (double apex)
for (let i = 500; i < 540; i++) B.LatG[i] = -0.8 * G;  // a flat-out loaded kink at 60 m/s (radius ~460 m)
for (let i = 600; i < 640; i++) B.LatG[i] = 0.3 * G;   // a twitch on the straight: too little load
B.LatG[700] = 2 * G;                            // one noisy sample
const cs = findCorners(B, sma(B.Speed, 9));
console.log(JSON.stringify({ aps: cs.map(c => c.ap), n: cs.length }));
"""
        r = run_js(js)
        aps = r["aps"]
        self.assertEqual(r["n"], 5, aps)
        self.assertTrue(110 <= aps[0] <= 130 and 150 <= aps[1] <= 165, aps)          # the esses, two corners
        self.assertTrue(310 <= aps[2] <= 330 and 360 <= aps[3] <= 380, aps)          # double apex, two corners
        self.assertTrue(500 <= aps[4] < 540, aps)                                     # the kink

    @unittest.skipUnless(os.path.exists(REAL_IBT), "the real Indianapolis file is not here")
    def test_indianapolis_has_its_fourteen_turns(self):
        js = r"""
const ib = ibt(readBytes(ARGS[0])), S = sessionStats(ib), o = S.clean.slice().sort((a, b) => a.time - b.time);
const R = analyze(ib.lapObj(o[5].i), ib.lapObj(o[0].i));
console.log(JSON.stringify({ n: R.cs.length }));
"""
        self.assertEqual(run_js(js, REAL_IBT)["n"], 14)


@unittest.skipUnless(NODE, "Node.js is not installed")
class FilesMenuTest(unittest.TestCase):
    """The "Your files" menus: each file named by what it is, and matched to the car and track loaded."""

    def test_labels_and_matching(self):
        js = r"""
const ibtF = { kind: 'ibt', when: Date.UTC(2020, 9, 10, 20, 47) / 1000, car: 'Ferrari 296 GT3', track: 'Indianapolis Motor Speedway · Road Course', session: 'Race', laps: 35, best: 84.033, driver: 'Andrew Brooks' };
const g = { kind: 'g61', driver: 'nick kalra', lap: 83.75, car: 'Ferrari 296 GT3', track: 'Indianapolis Motor Speedway (Road Course)' };
console.log(JSON.stringify({ ibt: flLabel(ibtF), g61: flLabel(g), renamed: flLabel({ kind: 'g61', lap: 84.6, est: true, name: 'my fast lap.csv' }),
  same: [flSame(g, 'Ferrari 296 GT3', 'Indianapolis Motor Speedway'), flSame(ibtF, 'Ferrari 296 GT3', 'Indianapolis Motor Speedway'),
         flSame(g, 'Porsche 911 GT3 R', 'Indianapolis Motor Speedway'), flSame(g, 'Ferrari 296 GT3', 'Daytona International Speedway'), flSame(g, null, null)] }));
"""
        r = run_js(js)
        self.assertIn("Ferrari 296 GT3 · Race · 35 laps · best 1:24.033 · Andrew Brooks", r["ibt"])
        self.assertTrue(r["ibt"].startswith("Oct 10, 2020"), r["ibt"])        # when it was recorded comes first
        self.assertEqual(r["g61"], "1:23.750 · nick kalra · Ferrari 296 GT3 · Indianapolis Motor Speedway (Road Course)")   # the lap time first
        self.assertEqual(r["renamed"], "about 1:24.600 · my fast lap")   # lap time from the file's length
        self.assertEqual(r["same"], [True, True, False, False, True])
