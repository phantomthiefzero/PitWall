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


def run_js(test_js, *files):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(test_js)
        script = f.name
    try:
        out = subprocess.run([NODE, HARNESS, PAGE, script, *files], capture_output=True, text=True, timeout=120)
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


def build_session(path):
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
            rows.append({"SessionTime": t, "Lap": lapno, "LapDistPct": p, "Speed": 50.0, "Throttle": 1.0, "Gear": 4,
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
    write_ibt(path, SESSION_YAML, CHANNELS, rows)
    return expected


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
