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
class DrivingTabTest(unittest.TestCase):
    def test_conflicting_suggestions_are_called_out(self):
        js = "const ARGS_YAML = " + json.dumps(SETUP_296_YAML) + ";\n" + r"""
LIVE.ws = {}; LIVE.setup = setupTree(ARGS_YAML); LIVE.meta = { carPath: 'ferrari296gt3', carName: 'Ferrari 296 GT3' };
setupHtml();
IB.a = { laps: [{ ok: true, time: 90 }], best: 0, _style: { nLaps: 5, traits: { no_trail: [2], saw: [2] }, per: [] } };
const h = drivingHtml();
console.log(JSON.stringify({ note: /Note:<\/b> ([^<]*?) (is|are) suggested in opposite/.exec(h), hasCoast: h.includes('Brake fully off before turning in') }));
"""
        r = run_js(js)
        self.assertTrue(r["hasCoast"])
        self.assertIsNotNone(r["note"])
        self.assertIn("Diff preload", r["note"][1])                   # lower to rotate on entry, higher for stability


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
