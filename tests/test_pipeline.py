"""Run: python -m pytest tests -q   (or python tests/test_pipeline.py)

These tests prove the DERIVED parts of the pipeline against known ground
truth. They cannot prove anything about what iRacing itself reports; that is
what `pitwall record` + `pitwall analyze` on the sim PC are for.
"""
import json
import math
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pitwall import trackmap  # noqa: E402
from pitwall.analyze import analyze  # noqa: E402
from pitwall.normalize import (Deriver, build_cars, build_session, build_state, decode_bits,  # noqa: E402
                               f2_is_real, FLAG_BITS)
from pitwall.recorder import record  # noqa: E402
from pitwall.sources import FakeSource, synthetic_track  # noqa: E402


def _run_fake_lap(hz=60):
    """Drive the fake player one full lap and collect map samples."""
    src = FakeSource(realtime=False)
    src.connect()
    coll = trackmap.LapCollector()
    for _ in range(hz * 400):
        src.tick()
        lap = coll.push(src.get("SessionTime"), src.get("LapDistPct"), src.get("VelocityX"),
                        src.get("VelocityY"), src.get("YawNorth"), on_track=True, on_pit=src.get("OnPitRoad"))
        if lap:
            return src, lap
    raise AssertionError("no lap collected")


def test_trackmap_matches_ground_truth():
    src, lap = _run_fake_lap()
    tm = trackmap.build_from_lap(lap, 9999, "Grand Prix")
    # ground truth outline resampled onto the same LapDistPct bins
    truth = [[*src.pos_at(b / tm.bins)] for b in range(tm.bins)]
    truth_norm, truth_span = trackmap.normalize_points(truth)
    errs = [math.hypot(a[0] - b[0], a[1] - b[1]) * truth_span for a, b in zip(tm.points, truth_norm)]
    mean_err, max_err = sum(errs) / len(errs), max(errs)
    print(f"track map: lap {tm.lap_length_m} m (truth {src.length:.1f}), closure {tm.closure_error_m} m, "
          f"mean err {mean_err:.1f} m, max err {max_err:.1f} m")
    assert abs(tm.lap_length_m - src.length) / src.length < 0.02
    assert mean_err < 10.0, mean_err     # a 5 km lap reconstructed to within ~10 m average
    assert max_err < 30.0, max_err


def test_lap_collector_rejects_pit_lap():
    coll = trackmap.LapCollector(min_samples=5)
    # one lap crossing, then a pit visit mid-lap, then another crossing -> no lap
    seq = [(0.95, False), (0.02, False), (0.3, False), (0.5, True), (0.7, False), (0.98, False), (0.03, False)]
    got = None
    for i, (pct, pit) in enumerate(seq):
        got = coll.push(float(i), pct, 50.0, 0.0, 0.0, on_track=True, on_pit=pit)
    assert got is None


def test_normalizer_shapes():
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(120):
        src.tick()
    sess = build_session(src)
    st = build_state(src, Deriver())
    cars = build_cars(src, sess)
    assert sess["track"]["TrackID"] == 9999 and len(sess["drivers"]) == 24
    assert st["player"]["carIdx"] == 0 and st["player"]["speedMs"] > 0
    assert st["session"]["flags"] == ["green"]
    assert st["weather"]["wetness"] == "dry"
    assert len(cars["cars"]) == 24
    leader = min(cars["cars"], key=lambda c: c["pos"])
    assert leader["derived"]["intervalS"] is None          # leader has nobody ahead
    second = [c for c in cars["cars"] if c["pos"] == 2][0]
    # 120 ticks in, nobody has completed a lap, so iRacing has no gap to report
    # and F2Time is still the 0.001-per-place placeholder. No number is correct
    # here; see test_intervals_appear_once_cars_complete_laps for the real case.
    assert second["derived"]["intervalS"] is None
    json.dumps([sess, st, cars])                            # must be JSON-safe


def test_derived_fuel_needs_completed_laps():
    src = FakeSource(realtime=False)
    src.connect()
    d = Deriver()
    st = build_state(src, d)
    assert st["derived"]["fuelPerLap"] is None              # no evidence yet -> no number
    # ~3 laps: the fake starts part-way round, and that first partial lap is
    # (rightly) not counted as a lap of fuel, so two full crossings are needed.
    for _ in range(60 * 360):
        src.tick()
        st = build_state(src, d)
    assert st["derived"]["fuelPerLap"] is not None and st["derived"]["fuelPerLap"] > 0
    assert st["derived"]["lapsOfFuel"] > 0


def test_decode_bits():
    assert decode_bits(0x0004 | 0x0020, FLAG_BITS) == ["green", "blue"]
    assert decode_bits(None, FLAG_BITS) == []


def test_record_and_analyze_roundtrip():
    src = FakeSource(realtime=False)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "r.jsonl.gz")
        n = record(src, out, hz=10, max_ticks=300, clock="sim", log=lambda *a: None)
        assert n == 300
        rep = analyze(out)
        assert rep["ticks"] == 300
        assert rep["vars"]["Speed"]["label"] == "live"
        assert rep["vars"]["CarIdxLapDistPct"]["label"] == "live"
        assert rep["vars"]["LFwearM"]["label"] == "static"    # fake never pits the player
        assert rep["vars"]["SessionNum"]["label"] == "static"


def test_f2time_placeholder_is_not_an_interval():
    """iRacing sends 0.001 * (pos - 1) for F2Time until a car completes a lap.

    Ground truth captured from a real replay at Daytona (GR86 race, lap 1):
    every car carried the placeholder and no car had a last lap time yet.
    Differencing those must not produce a ~1 ms interval.
    """
    placeholder = [{"pos": p, "f2TimeS": round(0.001 * (p - 1), 4), "lastLapS": -1.0}
                   for p in range(1, 13)]
    assert not any(f2_is_real(c) for c in placeholder)

    # The real transition, also captured live: leader crosses the line, P2/P3
    # get real gaps, P10 is still on lap 1 and still on the placeholder.
    assert f2_is_real({"pos": 2, "f2TimeS": 2.3722, "lastLapS": 122.892})
    assert f2_is_real({"pos": 3, "f2TimeS": 2.4418, "lastLapS": 123.104})
    assert not f2_is_real({"pos": 10, "f2TimeS": 0.0090, "lastLapS": -1.0})

    # A present F2Time with no lap time is never trustworthy.
    assert not f2_is_real({"pos": 4, "f2TimeS": 0.003, "lastLapS": 0.0})
    assert not f2_is_real({"pos": 4, "f2TimeS": None, "lastLapS": 90.0})


def test_intervals_suppressed_until_cars_have_a_lap():
    """build_cars must emit None, not a fabricated gap, on lap 1."""
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(120):
        src.tick()
    sess = build_session(src)
    cars = build_cars(src, sess)
    for c in cars["cars"]:
        if not f2_is_real(c):
            assert c["derived"]["intervalS"] is None, (c["idx"], c["derived"])


def test_intervals_appear_once_cars_complete_laps():
    """Once cars have real lap times, F2Time is a real gap and intervals appear."""
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(60 * 220):                               # long enough for laps
        src.tick()
    sess = build_session(src)
    cars = build_cars(src, sess)
    scored = [c for c in cars["cars"] if c["pos"] and c["lastLapS"] > 0]
    assert len(scored) > 1, "fake never completed a lap"
    withgap = [c for c in sorted(scored, key=lambda c: c["pos"])[1:]
               if c["derived"]["intervalS"] is not None]
    assert withgap, "no intervals derived after laps were completed"
    for c in withgap:
        assert c["derived"]["intervalS"] >= 0, c["derived"]["intervalS"]
    # and they must be real gaps, not the 1 ms-per-place placeholder pattern
    assert max(c["derived"]["intervalS"] for c in withgap) > 0.01


def test_replay_recording_labels_fast_vars_as_live_replay():
    """A replay recording has no on-track segment; fast vars must not read 'slow'.

    Regression for a real recording (Daytona GR86 replay, 1201 ticks / 120 s)
    where Speed changed on 1200 ticks and was still labelled 'slow' because
    IsOnTrack is false throughout a replay, leaving rate_track as None.
    """
    class ReplaySource(FakeSource):
        """FakeSource as iRacing presents it during replay playback."""
        def get(self, name):
            if name == "IsReplayPlaying":
                return True
            if name in ("IsOnTrack", "IsOnTrackCar"):
                return False
            return super().get(name)

    src = ReplaySource(realtime=False)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "replay.jsonl.gz")
        record(src, out, hz=10, max_ticks=300, clock="sim", log=lambda *a: None)
        rep = analyze(out)
    assert "track" not in rep["segments_s"], rep["segments_s"]
    assert rep["segments_s"]["replay"] > 0
    speed = rep["vars"]["Speed"]
    assert speed["rate_track"] is None
    assert speed["rate_replay"] >= 0.5
    assert speed["label"] == "live(replay)", speed
    # things that genuinely never change are still static, not upgraded
    assert rep["vars"]["SessionNum"]["label"] == "static"


def test_record_stops_when_the_sim_goes_away():
    """record() must honour its duration even if the source stops ticking.

    Regression: the duration and max_ticks checks lived only at the bottom of
    the loop, which the disconnect branch skipped with `continue`. Quitting the
    sim partway through a long recording hung the process forever and left the
    gzip stream unfinalised, so the partial recording could not be read back.
    """
    class DeadSource(FakeSource):
        """Connects, ticks a few times, then goes away like a closed sim."""
        def __init__(self):
            super().__init__(realtime=False)
            self._ticks = 0

        @property
        def is_connected(self):
            return self._ticks < 5

        def tick(self):
            self._ticks += 1
            if self._ticks < 5:
                return super().tick()
            return False

    from pitwall.recorder import iter_recording
    src = DeadSource()
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "dead.jsonl.gz")
        t0 = time.time()
        n = record(src, out, hz=50, duration_s=1.0, clock="wall", log=lambda *a: None)
        elapsed = time.time() - t0
        # read inside the temp dir's lifetime -- the gzip must be complete and
        # readable, which is the half of the bug that loses your recording
        recs = list(iter_recording(out))
    assert elapsed < 20, f"record() did not stop after the source died ({elapsed:.1f}s)"
    assert n >= 0
    assert any(r.get("type") == "header" for r in recs), "recording was not finalised"


def test_f2time_placeholder_survives_a_completed_lap():
    """A real lap time does NOT guarantee F2Time is a real gap.

    Ground truth: one frame from a spectated race at VIR North. P4 carried a
    real 2.8702 while P5 and P6 carried placeholders of 0.0020 and 0.0050
    despite both having genuine last-lap times. The first version of this gate
    trusted the lap time and produced -2.868.
    """
    assert f2_is_real({"pos": 4, "f2TimeS": 2.8702, "lastLapS": 103.359})
    assert not f2_is_real({"pos": 2, "f2TimeS": 0.0010, "lastLapS": 101.674})
    # the leader legitimately reports 0.0 and must stay usable as the baseline
    assert f2_is_real({"pos": 1, "f2TimeS": 0.0, "lastLapS": 100.493})


def test_intervals_are_never_negative_or_placeholder():
    """Time behind the leader cannot decrease down the running order.

    Replays the real VIR frame through the ordering logic. Across the full
    15-minute race this rule took 1459 negative intervals to zero.
    """
    frame = [  # (pos, f2TimeS, lastLapS) exactly as captured
        (1, 0.0000, 100.493), (2, 1.0962, 101.587), (3, 1.1817, 101.674),
        (4, 2.8702, 103.359), (5, 0.0020, 104.496), (6, 0.0050, 104.750),
        (7, 0.0060, -1.000), (8, 0.0070, -1.000),
    ]
    cars = [{"pos": p, "f2TimeS": f, "lastLapS": l, "derived": {"intervalS": None}}
            for p, f, l in frame]
    prev_real = None
    for c in cars:
        if not f2_is_real(c):
            continue
        if prev_real is None:
            prev_real = c["f2TimeS"]
            continue
        if c["f2TimeS"] < prev_real:
            continue
        c["derived"]["intervalS"] = round(c["f2TimeS"] - prev_real, 3)
        prev_real = c["f2TimeS"]

    got = [c["derived"]["intervalS"] for c in cars]
    assert all(v is None or v >= 0 for v in got), got
    assert all(v is None or v > 0.0011 for v in got), got
    # P2..P4 are real and must produce gaps; P5 onward must produce none
    assert got[1] is not None and got[2] is not None and got[3] is not None
    assert got[4] is None and got[5] is None and got[6] is None and got[7] is None


def test_yaw_convention_is_not_mirrored():
    """Pin the heading convention itself, not just self-consistency.

    The original bug was that FakeSource and trackmap.integrate() used the same
    wrong convention, so they inverted each other and the ground-truth test
    passed at 1.5 m while every REAL track came out mirrored. These assertions
    depend on neither module agreeing with the other.
    """
    from pitwall.trackmap import LapSample, integrate

    # Pure forward motion at yaw = 0 must travel along -X. heading = (-cos, sin).
    flat = [LapSample(t=0.0, pct=0.0, vx=10.0, vy=0.0, yaw=0.0),
            LapSample(t=1.0, pct=0.5, vx=10.0, vy=0.0, yaw=0.0)]
    pts, _ = integrate(flat)
    assert pts[-1][1] < -5.0, pts[-1]
    assert abs(pts[-1][2]) < 1e-6, pts[-1]

    # Chirality: a car holding constant speed with steadily increasing yaw traces
    # a circle. Which way it winds is exactly what the mirror bug got backwards,
    # so the sign of the enclosed area is the real regression guard.
    n = 360
    circle = [LapSample(t=i / 10.0, pct=i / n, vx=10.0, vy=0.0,
                        yaw=2 * math.pi * i / n) for i in range(n + 1)]
    pts, _ = integrate(circle)
    area = 0.0
    for a, b in zip(pts, pts[1:]):
        area += a[1] * b[2] - b[1] * a[2]
    assert area < 0, f"winding flipped -- the mirror bug is back (area {area:.1f})"


def test_gap_column_is_gated_like_the_interval():
    """A placeholder is not a gap whether you difference it or print it directly.

    The client showed "+0.001, +0.002, +0.003" down the order on lap 1 as gaps to
    the leader, because only intervalS was gated. gapValid applies the same rule
    to the raw f2TimeS the client prints.
    """
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(120):
        src.tick()
    sess = build_session(src)
    cars = build_cars(src, sess)
    scored = [c for c in cars["cars"] if c["pos"]]
    assert scored, "no scored cars"
    # lap 1: nobody has a lap time, so no gap is showable and none is claimed
    for c in scored:
        assert "gapValid" in c
        if not f2_is_real(c):
            assert c["gapValid"] is False, (c["pos"], c["f2TimeS"])

    # once laps are done, the leader and the field carry showable gaps again
    for _ in range(60 * 220):
        src.tick()
    cars = build_cars(src, sess)
    valid = [c for c in cars["cars"] if c.get("gapValid")]
    assert len(valid) > 1, "no showable gaps after laps were completed"
    for c in valid:
        assert c["f2TimeS"] is not None and c["f2TimeS"] >= 0


def test_fuel_to_finish_needs_both_a_burn_rate_and_a_race_length():
    """It is silent unless both inputs exist -- neither may be guessed."""
    d = Deriver()
    # no burn rate yet
    out = d.fuel(0, 50.0, laps_remain_ex=20)
    assert out["fuelToFinishL"] is None and out["fuelMarginL"] is None
    assert out["lapsRemaining"] == 20 and out["lapsRemainingIsEstimate"] is False

    # unlimited laps and no clock -> no race length, so still silent
    src = FakeSource(realtime=False)
    src.connect()
    d2 = Deriver()
    for _ in range(60 * 260):
        src.tick()
        st = build_state(src, d2)
    assert st["derived"]["fuelPerLap"] is not None
    out2 = d2.fuel(st["player"]["lapCompleted"], st["player"]["fuelL"],
                   laps_remain_ex=32767, time_remain_s=None, lap_time_s=None)
    assert out2["lapsRemaining"] is None
    assert out2["fuelToFinishL"] is None

    # a timed session projects laps from the clock, and says that it did
    out3 = d2.fuel(st["player"]["lapCompleted"], st["player"]["fuelL"],
                   laps_remain_ex=32767, time_remain_s=600.0, lap_time_s=120.0)
    assert out3["lapsRemaining"] == 5 and out3["lapsRemainingIsEstimate"] is True
    # computed from the full-precision burn rate and rounded once, so compare with
    # tolerance rather than against the already-rounded fuelPerLap
    assert abs(out3["fuelToFinishL"] - out3["fuelPerLap"] * 5) < 0.02
    assert abs(out3["fuelMarginL"] - (st["player"]["fuelL"] - out3["fuelToFinishL"])) < 0.02
    assert out3["fuelMarginL"] > 0        # plenty aboard for only 5 laps

    # and the sign flips when the race is long enough to run us dry
    out4 = d2.fuel(st["player"]["lapCompleted"], st["player"]["fuelL"],
                   laps_remain_ex=200, time_remain_s=None, lap_time_s=None)
    assert out4["lapsRemaining"] == 200 and out4["lapsRemainingIsEstimate"] is False
    assert out4["fuelMarginL"] < 0, "200 laps on one tank should read short"


def test_closing_rate_resets_when_the_car_ahead_changes():
    """The interval measures a pair. Change the pair and the old samples are void.

    Carrying them across reads the discontinuity as a closing rate -- observed
    live as +48.17 s/lap, which nothing in road racing does.
    """
    d = Deriver()
    # steady 0.1 s/s closing on car 5, over a long enough window to report
    for i in range(40):
        out = d.closing(1, float(i), 20.0 - i * 0.1, 100.0, ahead_idx=5)
    assert out["closingSPerLap"] is not None and out["closingSPerLap"] > 0

    # the car ahead becomes a different car, and the gap jumps accordingly
    out = d.closing(1, 40.0, 200.0, 100.0, ahead_idx=9)
    assert out["closingSPerLap"] is None, "history was carried across a changed pair"
    # and it stays quiet until a fresh window has built up against the new car
    for i in range(41, 50):
        out = d.closing(1, float(i), 200.0, 100.0, ahead_idx=9)
    assert out["closingSPerLap"] is None or abs(out["closingSPerLap"]) < 1.0


def test_closing_rate_never_reports_an_absurd_value():
    """A rate nothing can physically do means the series was not continuous."""
    d = Deriver()
    for i in range(40):
        d.closing(2, float(i), 10.0, 100.0, ahead_idx=3)
    # a sudden 500 s jump with the same car ahead (a scoring glitch, a lapping)
    out = None
    for i in range(40, 50):
        out = d.closing(2, float(i), 10.0 - (i - 39) * 50.0, 100.0, ahead_idx=3)
    assert out["closingSPerLap"] is None or abs(out["closingSPerLap"]) <= Deriver.GAP_MAX_RATE_S_PER_LAP


def test_pit_tracking_ignores_the_session_start_release():
    """A car already in its box when we connect has not just driven in.

    Seeding the state from False instead of from what the car is actually doing
    counted every car's release at the green as a completed 204 s stop -- 13 of
    16 cars in the recorded VIR race.
    """
    d = Deriver()
    # first sight: already on pit road, sitting in the box
    out = d.pit(7, 0.0, True, True, 0)
    assert out["stopsSeen"] == 0 and out["inPit"] is True
    for t in range(1, 20):
        out = d.pit(7, float(t), True, True, 0)
    out = d.pit(7, 20.0, False, False, 0)          # released for the green
    assert out["stopsSeen"] == 0, "session-start release counted as a stop"

    # a genuine stop later: enter the lane, sit in the box, leave
    d.pit(7, 100.0, True, False, 5)                # enters pit road on lap 5
    d.pit(7, 101.0, True, True, 5)                 # stationary
    d.pit(7, 126.0, True, False, 5)                # pulls away after 25 s
    out = d.pit(7, 130.0, False, False, 5)         # rejoins
    assert out["stopsSeen"] == 1
    assert out["lastStopLap"] == 5
    assert abs(out["lastStopS"] - 25.0) < 0.05     # stationary time, not lane time
    out = d.pit(7, 400.0, False, False, 9)
    assert out["lapsSincePit"] == 4


def test_pit_tracking_matches_the_recorded_race():
    """Ground truth from the VIR race: three stops, with known box times."""
    d = Deriver()
    # car 8's stop, as captured: entered 450.8, box 450.8 -> 603.2, left lane 613.6
    d.pit(8, 400.0, False, False, 1)
    d.pit(8, 450.8, True, True, 2)
    d.pit(8, 603.2, True, False, 2)
    out = d.pit(8, 613.6, False, False, 2)
    assert out["stopsSeen"] == 1 and out["lastStopLap"] == 2
    assert abs(out["lastStopS"] - 152.4) < 0.1


def test_replay_source_drives_the_pipeline_from_a_recording():
    """A capture must be able to drive the stack exactly as the sim would.

    Without this the only way to exercise the client against a real race was to
    be in one, and every bug found today had to be chased by hand-replaying
    recordings in Python.
    """
    from pitwall.sources import make_source
    src = FakeSource(realtime=False)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "cap.jsonl.gz")
        n = record(src, out, hz=10, max_ticks=200, clock="sim", log=lambda *a: None)
        assert n == 200
        rp = make_source("replay", out, realtime=False)
        assert rp.connect() is True
        assert rp.name == "replay"
        frames = 0
        while rp.tick():
            frames += 1
            assert rp.get("SessionTime") is not None
        assert frames == 200, frames
        assert rp.is_connected is False          # exhausted, and says so
        assert len(rp.var_headers()) > 50
        assert rp.session_info("WeekendInfo") is not None


def test_sector_times_sum_to_the_lap_time():
    """The independent check: iRacing reports the lap, we derive the sectors.

    Sampling is only a few Hz, so the crossing is interpolated rather than taken
    at the sample tick -- without that every sector quantises to the sample
    interval. Against the real VIR race the sums matched iRacing's own lastLapS
    to within a millisecond.
    """
    src = FakeSource(realtime=False)
    src.connect()
    d = Deriver()
    sess = None
    cars = None
    for i in range(60 * 400):
        src.tick()
        if i % 4 == 0:
            if sess is None:
                sess = build_session(src)
            cars = build_cars(src, sess, d)
    scored = [c for c in cars["cars"]
              if c["derived"].get("sectorS") and all(v is not None for v in c["derived"]["sectorS"])
              and c["lastLapS"] and c["lastLapS"] > 0]
    assert scored, "no car completed a full set of sectors"
    checked = 0
    for c in scored:
        total = sum(c["derived"]["sectorS"])
        # only meaningful when the sectors and the reported lap are the same lap
        if abs(total - c["lastLapS"]) < 2.0:
            assert abs(total - c["lastLapS"]) < 0.25, (c["idx"], total, c["lastLapS"])
            checked += 1
    assert checked, "no car had a comparable sector set and lap time"


def test_fuel_save_target_appears_only_when_short():
    d = Deriver()
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(60 * 260):
        src.tick()
        st = build_state(src, d)
    fuel = st["player"]["fuelL"]
    # comfortably enough fuel -> no save instruction
    ok = d.fuel(st["player"]["lapCompleted"], fuel, laps_remain_ex=2)
    assert ok["fuelMarginL"] > 0 and ok["fuelSavePerLapL"] is None
    # not enough -> the shortfall spread across the remaining laps
    short = d.fuel(st["player"]["lapCompleted"], fuel, laps_remain_ex=200)
    assert short["fuelMarginL"] < 0
    assert short["fuelSavePerLapL"] is not None
    assert abs(short["fuelSavePerLapL"] - abs(short["fuelMarginL"]) / 200) < 0.01


def test_stint_resets_at_a_pit_stop():
    d = Deriver()
    for lap in range(1, 9):                    # eight laps, steadily slower
        out = d.stint(4, lap, 90.0 + lap * 0.1, stops_seen=0)
    assert out["stintLaps"] == 8
    assert out["degSPerLap"] is not None and out["degSPerLap"] > 0.05, out
    out = d.stint(4, 9, 90.9, stops_seen=1)    # pitted: the stint is over
    assert out["stintLaps"] <= 1, "stint carried across a pit stop"
    assert out["degSPerLap"] is None


def test_replay_loop_signals_a_restart():
    """A looping replay repeats the session, so derived history must not carry.

    Without this the stop counter kept climbing each pass -- the standings showed
    x2, x4, x5 for cars that stopped once, which is a wrong number presented as
    fact, exactly what the rest of this pipeline exists to avoid.
    """
    from pitwall.sources import make_source
    src = FakeSource(realtime=False)
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "loop.jsonl.gz")
        record(src, out, hz=10, max_ticks=50, clock="sim", log=lambda *a: None)
        rp = make_source("replay", out, realtime=False, loop=True)
        rp.connect()
        assert rp.restarts == 0
        for _ in range(50):
            assert rp.tick() is True
        assert rp.restarts == 0, "restart signalled before the end"
        rp.tick()                       # first tick of the second pass
        assert rp.restarts == 1
        for _ in range(50):
            rp.tick()
        assert rp.restarts == 2


def test_bridge_resets_derived_history_on_a_session_change():
    """Qualifying history must not follow a car into the race.

    A stop taken in qualifying is not a race stop, and a stint does not span the
    two. The real spectated capture crosses SessionNum 1 -> 2 partway through,
    which is what caught this: the reset existed only for a looping replay.
    """
    from pitwall.bridge import Bridge

    class Src(FakeSource):
        """Fake whose SessionNum flips once, like a quali -> race transition."""
        def __init__(self):
            super().__init__(realtime=False)
            self.session_num = 1

        def get(self, name):
            if name == "SessionNum":
                return self.session_num
            return super().get(name)

    src = Src()
    src.connect()
    b = Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None)
    first = b.deriver
    b._session_num = 1

    # same session: the deriver is kept
    src.tick()
    st = build_state(src, b.deriver)
    assert b.deriver is first

    # emulate the bridge's per-state check
    def step():
        n = src.get("SessionNum")
        if n is not None and n != b._session_num:
            prev, b._session_num = b._session_num, n
            if prev is not None:
                b.deriver = Deriver()

    step()
    assert b.deriver is first, "reset fired without a session change"
    src.session_num = 2
    step()
    assert b.deriver is not first, "derived history carried across a session change"


def test_lap_deltas_are_gated_on_their_validity_flag():
    """iRacing reports 0.0 with _OK false before there is a reference lap.

    Rendering that as "+0.000" is a confident wrong number, and it was on screen
    the whole time -- the delta readout never moved off zero.
    """
    from pitwall.normalize import _delta
    vals = {"LapDeltaToBestLap": 0.0, "LapDeltaToBestLap_DD": 0.0, "LapDeltaToBestLap_OK": False}
    out = _delta(vals.get, "LapDeltaToBestLap", "deltaToBest")
    assert out == {"deltaToBestS": None, "deltaToBestRate": None}

    vals = {"LapDeltaToBestLap": 3.41, "LapDeltaToBestLap_DD": 0.0088, "LapDeltaToBestLap_OK": True}
    out = _delta(vals.get, "LapDeltaToBestLap", "deltaToBest")
    assert out["deltaToBestS"] == 3.41
    assert abs(out["deltaToBestRate"] - 0.0088) < 1e-9

    # a missing flag is not a valid delta either
    out = _delta({}.get, "LapDeltaToBestLap", "deltaToBest")
    assert out["deltaToBestS"] is None


def test_theoretical_best_is_the_sum_of_own_best_sectors():
    """And the gap to the real best lap is time already driven, not predicted."""
    src = FakeSource(realtime=False)
    src.connect()
    d = Deriver()
    sess = None
    cars = None
    for i in range(60 * 500):
        src.tick()
        if i % 4 == 0:
            if sess is None:
                sess = build_session(src)
            cars = build_cars(src, sess, d)
    withtheo = [c for c in cars["cars"] if c["derived"].get("theoreticalBestS") is not None]
    assert withtheo, "no car accumulated a full set of best sectors"
    for c in withtheo:
        bs = c["derived"]["bestSectorS"]
        assert abs(c["derived"]["theoreticalBestS"] - sum(bs)) < 0.002
        if c["derived"].get("bestLapLeftS") is not None:
            # a real lap can never beat the sum of the best sectors of its own laps
            assert c["derived"]["bestLapLeftS"] >= -0.002, c["derived"]


def test_theoretical_best_is_never_slower_than_the_best_lap():
    """When the sectors we have timed sum to more than iRacing's best lap, we have
    not seen enough to know a theoretical best, so none is shown."""
    from pitwall.normalize import theoretical_best
    assert theoretical_best([30.0, 40.0, 25.0], 96.0) == (95.0, 1.0)
    assert theoretical_best([30.0, 40.0, 26.0], 95.98) == (95.98, 0.0)   # within timing slack: the lap bounds it
    assert theoretical_best([30.0, 40.0, 27.0], 96.0) == (None, None)    # 1 s slower: not enough seen
    assert theoretical_best([30.0, None, 25.0], 96.0) == (None, None)    # a sector never timed
    assert theoretical_best([30.0, 40.0, 25.0], -1.0) == (95.0, None)    # no best lap reported yet


def test_theoretical_best_when_the_best_lap_was_set_before_connecting():
    """The sim's awkward shape, which the fake used to spare us: iRacing reports
    a best lap set before we started watching, while our sectors only cover laps
    since. In the practice recording that made the 'theoretical' 5.86 s slower
    than the real best. Here the field runs a while unwatched first."""
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(60 * 300):                  # five minutes nobody is watching
        src.tick()
    d, sess, cars = Deriver(), None, None
    for i in range(60 * 400):
        src.tick()
        if i % 4 == 0:
            sess = sess or build_session(src)
            cars = build_cars(src, sess, d)
    shown = hidden = 0
    for c in cars["cars"]:
        dd, best = c["derived"], c.get("bestLapS")
        theo = dd.get("theoreticalBestS")
        if theo is not None and best and best > 0:
            shown += 1
            assert theo <= best + 1e-9, (c["idx"], theo, best)
            assert dd["bestLapLeftS"] >= 0, (c["idx"], dd["bestLapLeftS"])
        bs = dd.get("bestSectorS")
        if bs and all(v is not None for v in bs) and best and best > 0 and sum(bs) > best + 0.05:
            hidden += 1
            assert theo is None and dd.get("bestLapLeftS") is None, (c["idx"], theo)
    assert shown, "no car showed a theoretical best at all"
    assert hidden, "the scenario never produced a best lap from before we were watching"


def test_multiclass_fields_carry_class_identity():
    """Class colour, id and per-class position must reach the client.

    FakeSource was single-make, so none of the multiclass handling could be
    exercised against it -- the same blind spot that hid the F2Time placeholder
    and the mirrored map. It now fields three classes.
    """
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(60 * 160):
        src.tick()
    sess = build_session(src)
    cars = build_cars(src, sess, Deriver())
    scored = [c for c in cars["cars"] if c["pos"]]
    assert scored

    ids = {c["classId"] for c in scored}
    assert len(ids) > 1, "fake field is not multiclass"
    for c in scored:
        assert c["classColor"] is not None, c["idx"]
        assert 0 <= c["classColor"] <= 0xFFFFFF
        assert c["className"]

    # class position must rank within the class, not repeat the overall order
    by_class = {}
    for c in sorted(scored, key=lambda x: x["pos"]):
        by_class.setdefault(c["classId"], []).append(c["classPos"])
    for cid, seq in by_class.items():
        assert seq == list(range(1, len(seq) + 1)), (cid, seq)
    # and with several classes, someone's class position must differ from overall
    assert any(c["classPos"] != c["pos"] for c in scored)


def test_driver_licence_and_irating_reach_the_car():
    """The Watching panel shows them beside the name, so they must be per-car."""
    src = FakeSource(realtime=False)
    src.connect()
    for _ in range(120):
        src.tick()
    sess = build_session(src)
    cars = build_cars(src, sess, Deriver())
    named = [c for c in cars["cars"] if c["name"]]
    assert named
    for c in named:
        assert c["licString"], c["idx"]
        assert c["licColor"] is not None and 0 <= c["licColor"] <= 0xFFFFFF
        assert c["iRating"] is not None
    # more than one licence class present, so the colouring is actually exercised
    assert len({c["licString"][0] for c in named}) > 1


def test_relay_does_not_stall_behind_a_viewer_that_stops_reading():
    """One dead phone must not freeze every other engineer.

    Measured against the old fan-out, which awaited each viewer in turn: a viewer
    that stopped reading took a healthy one from 20 msg/s to 0 within 5 s, for as
    long as the dead connection lasted. Now each viewer has its own bounded queue
    and the slow one is dropped instead.
    """
    import asyncio
    import base64
    import importlib.util
    import socket

    import websockets
    from websockets.asyncio.server import serve

    spec = importlib.util.spec_from_file_location(
        "relay_server", os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"))
    relay_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay_mod)

    # Enough messages to overflow the per-viewer queue several times over, so
    # the test keeps meaning the same thing if VIEWER_QUEUE is tuned.
    N, PAYLOAD = max(1500, relay_mod.VIEWER_QUEUE * 3), "x" * 4000

    async def run():
        relay = relay_mod.Relay("B", "V", None)
        async with serve(relay.handler, "127.0.0.1", 0, max_size=8 * 1024 * 1024) as server:
            port = server.sockets[0].getsockname()[1]
            # a viewer that completes the handshake and never reads again
            stuck = socket.socket()
            stuck.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
            stuck.connect(("127.0.0.1", port))
            key = base64.b64encode(os.urandom(16)).decode()
            stuck.send((f"GET /view?token=V HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\n"
                        f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                        f"Sec-WebSocket-Version: 13\r\n\r\n").encode())
            got = 0
            try:
                async with websockets.connect(f"ws://127.0.0.1:{port}/view?token=V", max_size=None) as good:
                    await asyncio.sleep(0.2)            # both viewers registered
                    async with websockets.connect(f"ws://127.0.0.1:{port}/bridge?token=B") as bridge:
                        async def produce():
                            # ~400 msg/s of 4 KB: twenty times a real bridge's rate,
                            # enough to fill the stuck socket's buffers in about a second
                            for i in range(N):
                                await bridge.send(json.dumps({"type": "tick", "i": i, "pad": PAYLOAD}))
                                await asyncio.sleep(0.0025)
                        producer = asyncio.ensure_future(produce())
                        try:
                            while got < N:
                                m = json.loads(await asyncio.wait_for(good.recv(), 5))
                                if m.get("type") == "tick":
                                    got += 1
                        except asyncio.TimeoutError:
                            pass                        # stalled: the assert below says so
                        producer.cancel()
            finally:
                # Closing the stuck socket unblocks a relay that is awaiting it,
                # so a regression fails here instead of hanging the server shutdown.
                stuck.close()
            return got, relay.stats.get("dropped_slow", 0)

    got, dropped = asyncio.run(run())
    assert got == N, f"healthy viewer received {got} of {N} messages beside a stuck one"
    assert dropped == 1, "the stuck viewer should have been dropped"


def test_bridge_survives_a_frame_the_normalizer_cannot_handle():
    """An exception in normalize used to escape run() and end the stream mid-race."""
    import asyncio

    from pitwall import bridge as bridge_mod

    src = FakeSource(realtime=False)
    src.connect()
    b = bridge_mod.Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None)
    real, calls = bridge_mod.build_state, {"n": 0}

    def flaky(s, d):
        # Count only this test's source: another bridge running in the same test process (the
        # suite tests keep one going) calls build_state too, and must not use up the bad frame.
        if s is not src:
            return real(s, d)
        calls["n"] += 1
        if calls["n"] == 3:
            raise KeyError("a variable this build has never seen")
        return real(s, d)

    class Done(Exception):
        pass

    class WS:
        sent = []

        async def send(self, raw):
            self.sent.append(json.loads(raw)["type"])
            if self.sent.count("state") >= 10:
                raise Done

    bridge_mod.build_state = flaky
    b.state_dt = b.cars_dt = 0.0
    try:
        asyncio.run(b._producer(WS()))
    except Done:
        pass
    finally:
        bridge_mod.build_state = real
    assert b.stats["frame_errors"] == 1
    assert WS.sent.count("state") >= 10, "the bridge stopped streaming after the bad frame"


# --------------------------------------------------------------------------- #
# Race engineer booth: lap history, gaps to you, pit loss, rejoin, undercut,
# traffic, events and the feed.
# --------------------------------------------------------------------------- #

def _car(idx, **kw):
    c = {"idx": idx, "lapCompleted": 0, "lastLapS": -1.0, "onPitRoad": False, "pos": idx, "classPos": idx,
         "f2TimeS": 0.0, "gapValid": True, "classId": 10, "surface": "on_track", "pct": 0.0, "derived": {}}
    c.update(kw)
    return c


def _laps(d, idx, times, t0=0.0):
    """Feed a run of completed laps through record_lap; returns the time reached."""
    t = t0
    c = _car(idx, lapCompleted=0, lastLapS=-1.0)
    d.record_lap(c, t)
    for k, lt in enumerate(times, start=1):
        t += lt
        c = _car(idx, lapCompleted=k, lastLapS=lt)
        d.record_lap(c, t)
    return t


def test_lap_entries_take_a_lap_time_that_arrives_late():
    """The player's LapLastLapTime is known to update after the line; whether the
    per-car array does is unverified. Both orders must credit the right lap."""
    d = Deriver()
    d.record_lap(_car(3, lapCompleted=4, lastLapS=101.0), 0.0)        # first sight: 101 is an old lap
    assert d.record_lap(_car(3, lapCompleted=5, lastLapS=101.0), 100.0) is None   # line crossed, time not in yet
    done = d.record_lap(_car(3, lapCompleted=5, lastLapS=99.5), 100.6)           # ...arrives 0.6 s later
    assert done and done["lap"] == 5 and done["lapS"] == 99.5
    # a time that is already new at the crossing is taken at once
    done = d.record_lap(_car(3, lapCompleted=6, lastLapS=98.7), 199.0)
    assert done and done["lap"] == 6 and done["lapS"] == 98.7
    # and one that never changes is given up on, not credited with the old value
    assert d.record_lap(_car(3, lapCompleted=7, lastLapS=98.7), 298.0) is None
    done = d.record_lap(_car(3, lapCompleted=7, lastLapS=98.7), 298.0 + Deriver.LAP_TIME_WAIT_S)
    assert done and done["lap"] == 7 and done["lapS"] is None


def test_pace_ignores_pit_laps_and_wants_two_laps():
    d = Deriver()
    _laps(d, 1, [100.0])
    assert d.pace(1) is None, "one lap is not a pace"
    d = Deriver()
    t = _laps(d, 1, [100.0, 101.0])
    assert d.pace(1) == 100.5
    # an in-lap and an out-lap: the car is on pit road during both
    d.record_lap(_car(1, lapCompleted=2, lastLapS=101.0, onPitRoad=True), t + 50)
    d.record_lap(_car(1, lapCompleted=3, lastLapS=130.0, onPitRoad=True), t + 130)
    d.record_lap(_car(1, lapCompleted=4, lastLapS=160.0, onPitRoad=False), t + 290)
    assert [e["pit"] for e in d._laps[1][-2:]] == [True, True]
    assert d.pace(1) == 100.5, "pit laps leaked into the pace"


def test_pit_lane_loss_is_lane_time_less_stationary_less_pace():
    """Lane loss = time between the pit-road lines - stationary - same stretch at pace."""
    d = Deriver()
    t = _laps(d, 5, [99.9, 100.0, 100.1])                  # pace 100.0 (distinct times:
    d.pit(5, t, False, False, 3, pct=0.90)                 # an identical one reads as not-yet-updated)                 # seed on track
    d.pit(5, t + 5, True, False, 3, pct=0.95)              # pit entry line at 0.95
    d.pit(5, t + 15, True, True, 3, pct=0.99)              # stops in the box
    d.pit(5, t + 35, True, False, 4, pct=0.99)             # 20 s stationary
    d.pit(5, t + 55, False, False, 4, pct=0.05)            # exit line at 0.05: 50 s in the lane
    pl = d.pit_loss()
    # 50 s in the lane - 20 s stationary - 0.10 lap x 100 s = 20 s
    assert pl["samples"] == 1 and abs(pl["laneLossS"] - 20.0) < 0.01
    assert pl["boxMedianS"] == 20.0


def test_pit_lane_loss_ignores_a_reset_to_the_pits():
    """A reset puts the car in its box without driving the lane. It still counts
    as a stop seen, but timing it as lane loss would be nonsense."""
    d = Deriver()
    t = _laps(d, 5, [99.9, 100.0, 100.1])
    d.pit(5, t, False, False, 3, pct=0.40)
    d.pit(5, t + 1, True, True, 3, pct=0.40)               # on pit road...
    d.pit(5, t + 2, True, True, 3, pct=0.99)               # ...and teleported to the box
    d.pit(5, t + 30, True, False, 3, pct=0.99)
    d.pit(5, t + 40, False, False, 4, pct=0.05)
    assert d.pit_loss()["samples"] == 0


def test_rejoin_places_you_by_your_gap_plus_the_loss():
    d = Deriver()
    _laps(d, 0, [99.9, 100.1])                             # your pace: 100.0
    cars = [_car(1, f2TimeS=0.0), _car(2, f2TimeS=5.0), _car(0, f2TimeS=10.0),
            _car(3, f2TimeS=18.0), _car(4, f2TimeS=31.0, classId=20), _car(5, f2TimeS=40.0)]
    r = d.predict_rejoin(cars, 0, 25.0)                     # virtual gap 35
    assert r["pos"] == 5 and r["classPos"] == 4             # behind 1, 2, 3 and car 4 (other class)
    assert r["ahead"] == {"idx": 3, "gapS": 17.0} and r["behind"] == {"idx": 5, "gapS": 5.0}
    # on track: car 4 is 4 s up the road, car 5 is 5 s back, car 1 is 35 s ahead -> not near
    assert r["traffic"] == []
    r = d.predict_rejoin(cars, 0, 22.0)                     # virtual 32: car 4 is 1 s ahead on track
    assert r["traffic"] == [{"idx": 4, "onTrackS": -1.0}]
    # a lapped car one full lap (100 s) behind on time is right beside you on track
    cars.append(_car(6, f2TimeS=133.5))
    r = d.predict_rejoin(cars, 0, 22.0)
    assert {"idx": 6, "onTrackS": 1.5} in r["traffic"]
    # no trustworthy gap for you: say nothing
    cars[2]["gapValid"] = False
    assert d.predict_rejoin(cars, 0, 22.0) is None


def test_gaps_to_you_and_to_the_class_leader():
    src = FakeSource(realtime=False, speed=4.0)
    src.connect()
    d = Deriver()
    sess = build_session(src)
    for i in range(15 * 60 * 6):                           # 6 sim-minutes at 4x steps
        src.tick()
        if i % 4 == 0:
            cars = build_cars(src, sess, d)
    by = {c["idx"]: c for c in cars["cars"]}
    me = by[0]
    assert me["gapValid"]
    checked = 0
    for c in cars["cars"]:
        dd = c["derived"]
        if c is me or not c["gapValid"]:
            assert dd["toPlayerS"] is None
            continue
        assert abs(dd["toPlayerS"] - (c["f2TimeS"] - me["f2TimeS"])) < 6e-4     # rounded to 1 ms
        lead = next(x for x in cars["cars"] if x["classId"] == c["classId"] and x["classPos"] == 1)
        if lead["gapValid"]:
            assert abs(dd["classGapS"] - (c["f2TimeS"] - lead["f2TimeS"])) < 6e-4
        else:
            assert dd["classGapS"] is None
        checked += 1
    assert checked > 10
    # outside a race F2Time is a best lap, not a gap: nothing is derived from it
    src._vars["SessionNum"] = 0
    cars = build_cars(src, sess, d)
    assert all(c["derived"]["toPlayerS"] is None and c["derived"]["classGapS"] is None for c in cars["cars"])
    assert cars["derived"]["isRace"] is False and cars["derived"]["rejoin"] is None


def test_you_gain_is_positive_when_it_is_good_for_you():
    """One sign for both sides: a car ahead coming back, or a car behind dropping back."""
    d = Deriver()
    for k in range(13):                                    # car ahead, -5.0 -> -4.0 over 60 s
        out = d.to_player_trend(1, k * 5.0, -5.0 + k / 12, 100.0, 0)
    assert out["youGainSPerLap"] > 0 and abs(out["youGainSPerLap"] - 100 / 60) < 0.01
    assert out["youCatchLaps"] == round(4.0 / out["youGainSPerLap"], 1)
    for k in range(13):                                    # car behind, 2.0 -> 1.5: it is catching you
        out = d.to_player_trend(2, k * 5.0, 2.0 - 0.5 * k / 12, 100.0, 0)
    assert out["youGainSPerLap"] < 0 and out["catchesYouLaps"] == round(1.5 / -out["youGainSPerLap"], 1)
    out = d.to_player_trend(2, 65.0, 1.5, 100.0, 7)         # a different player car: history dropped
    assert out["youGainSPerLap"] is None


def test_traffic_meeting_time_and_place():
    d = Deriver()
    _laps(d, 0, [99.9, 100.1])                             # you: 100 s pace
    _laps(d, 1, [109.9, 110.1])                            # slower car: 110
    _laps(d, 2, [89.9, 90.1])                              # faster car: 90
    cars = [_car(0, pct=0.20), _car(1, pct=0.25), _car(2, pct=0.15)]
    tr = {x["idx"]: x for x in d.traffic(cars, 0)}
    # 0.05 lap closes at 1/100 - 1/110 laps per second -> 55 s, 0.55 lap on
    assert tr[1]["kind"] == "you_catch" and abs(tr[1]["inS"] - 55.0) < 0.05
    assert abs(tr[1]["atPct"] - 0.75) < 1e-4
    # faster car 0.05 behind closes at 1/90 - 1/100 -> 45 s
    assert tr[2]["kind"] == "catches_you" and abs(tr[2]["inS"] - 45.0) < 0.05
    assert abs(tr[2]["atPct"] - 0.65) < 1e-4
    # beyond the horizon, or in the pits: not listed
    cars[1]["pct"] = 0.40
    cars[2]["surface"] = "approaching_pits"
    assert d.traffic(cars, 0) == []


_RACE = {}


def _fake_race():
    """One 25-minute fake race, run once and shared. The player stops on lap 7.

    Records, for every stop by a non-player car once a pit loss is known, the
    rejoin prediction made at pit entry (using the stationary time it really
    took) and where the car actually was 8 s after rejoining.
    """
    if _RACE:
        return _RACE
    import copy
    src = FakeSource(realtime=False, speed=4.0, player_pit_lap=7)
    src.connect()
    d = Deriver()
    sess = build_session(src)
    prev, pending, checks, uc = {}, {}, [], []
    for i in range(int(25 * 60 * 60 / 4)):
        src.tick()
        if i % 2 == 0:
            build_state(src, d)
        if i % 4:
            continue
        cars = build_cars(src, sess, d)
        t = cars["t"]
        by = {c["idx"]: c for c in cars["cars"]}
        for idx, c in by.items():
            was, now = prev.get(idx), bool(c["onPitRoad"])
            if idx != 0 and was is False and now and d.pit_loss()["laneLossS"] is not None:
                pending[idx] = {"snap": copy.deepcopy(cars["cars"]), "loss": d.pit_loss()["laneLossS"]}
            if was and not now and idx in pending:
                pending[idx].update(exitT=t, box=c["derived"]["lastStopS"])
            prev[idx] = now
        for idx, p in list(pending.items()):
            if "exitT" in p and t - p["exitT"] >= 8:
                r = d.predict_rejoin(p["snap"], idx, p["loss"] + (p["box"] or 0))
                if r:
                    checks.append((r["classPos"], by[idx]["classPos"]))
                del pending[idx]
        loss = d.typical_loss()
        for u in cars["derived"]["undercut"]:
            uc.append((dict(u), loss))
    _RACE.update(d=d, cars=cars, checks=checks, uc=uc)
    return _RACE


def test_rejoin_prediction_matches_where_cars_actually_rejoin():
    """The prediction made at pit entry against the class position the car really
    had once back up to speed. It assumes everyone else keeps their gap, so a
    rival pitting in the same window can move it by a place."""
    r = _fake_race()
    checks = r["checks"]
    assert len(checks) >= 5, checks
    within1 = sum(1 for p, a in checks if abs(p - a) <= 1)
    exact = sum(1 for p, a in checks if p == a)
    assert within1 >= 0.8 * len(checks), checks
    assert exact >= 0.5 * len(checks), checks


def test_pit_lane_loss_matches_the_fake_pit_lane():
    """The fake lane runs 0.955 -> 0.045 (0.09 lap) at 16.7 m/s, so a car loses
    0.09 * length / 16.7 minus the same 0.09 lap at its own pace."""
    r = _fake_race()
    d = r["d"]
    src = FakeSource(realtime=False)
    lane_t = 0.09 * src.length / FakeSource.PIT_LANE_MS
    assert d._loss, "no lane loss measured in a 25-minute race"
    for s in d._loss:
        expect = lane_t - 0.09 * d.pace(s["idx"])
        # entry, exit and the box are each sampled at ~4 Hz of sim time, and the
        # pace drifts a little: within 0.8 s
        assert abs(s["laneLossS"] - expect) < 0.8, (s, expect)


def test_undercut_watch_after_your_stop():
    r = _fake_race()
    ys = [(u, loss) for u, loss in r["uc"] if u["kind"] == "you_stopped"]
    assert ys, "the player stopped on lap 7 but no pit battle was opened"
    for u, loss in ys:
        if u["afterTheirStopS"] is not None:
            assert abs(u["afterTheirStopS"] - (u["toPlayerS"] + loss)) < 0.02
        if len(u["series"]) >= 2:
            assert u["trendSPerLap"] == round(u["series"][-1] - u["series"][-2], 2)


def test_race_events_from_the_fake_race():
    d = _fake_race()["d"]
    kinds = [e["kind"] for e in d.feed_events]
    assert kinds.count("pit_out") >= 3 and kinds.count("pit_in") >= kinds.count("pit_out")
    outs = [e for e in d.feed_events if e["kind"] == "pit_out" and e.get("laneLossS") is not None]
    assert outs and all(e["stationaryS"] > 0 for e in outs)
    assert "fastest_lap" in kinds
    seqs = [e["seq"] for e in d.feed_events] + [x["seq"] for x in d.feed_laps]
    assert len(seqs) == len(set(seqs)), "feed sequence numbers repeat"
    # every completed lap of the player is in the lap history, in order
    mine = [x["lap"] for x in d.feed_laps if x["idx"] == 0]
    assert mine == sorted(mine) and len(mine) >= 10


def test_fastest_lap_events_skip_what_was_already_on_the_board():
    d = Deriver()
    cars = [_car(1, bestLapS=100.0), _car(2, bestLapS=101.0, classId=20)]
    d.field_events(cars, 0.0, None, {})
    assert not [e for e in d.feed_events if e["kind"] == "fastest_lap"]
    cars[1]["bestLapS"] = 100.5                                # class best, not overall
    d.field_events(cars, 1.0, None, {})
    cars[0]["bestLapS"] = 99.0                                 # overall best
    d.field_events(cars, 2.0, None, {})
    fl = [e for e in d.feed_events if e["kind"] == "fastest_lap"]
    assert [(e["idx"], e["overall"]) for e in fl] == [(2, False), (1, True)]


def test_state_events_incidents_flags_weather_engine():
    d = Deriver()
    d.incident_limit = 17
    base = lambda **kw: ({"flags": kw.get("flags", ["green"])},
                         {"lap": 3, "incidents": {"my": kw.get("inc", 2)},
                          "engineWarnings": kw.get("warn", []), "towTimeS": kw.get("tow", 0.0)},
                         {"wetness": kw.get("wet", "dry"), "precipitationPct": kw.get("rain", 0.0),
                          "declaredWet": kw.get("dw", False)})
    d.state_events(0.0, *base())                               # seeds silently
    assert d.feed_events == []
    d.state_events(1.0, *base(inc=4))
    d.state_events(2.0, *base(inc=4, warn=["pit_speed_limiter"]))     # routine: not news
    d.state_events(3.0, *base(inc=4, warn=["water_temp", "pit_speed_limiter"]))
    d.state_events(4.0, *base(inc=4, flags=["yellow"]))
    d.state_events(5.0, *base(inc=4, flags=["green"]))
    d.state_events(6.0, *base(inc=4, flags=["yellow"]))       # back within 20 s: not repeated
    d.state_events(7.0, *base(inc=4, rain=0.2, wet="lightly_wet", dw=True))
    d.state_events(8.0, *base(inc=4, rain=0.2, wet="lightly_wet", dw=True, tow=30.0))
    got = [(e["kind"], {k: v for k, v in e.items() if k not in ("t", "lap", "seq", "kind")}) for e in d.feed_events]
    inc = [g[1] for g in got if g[0] == "incident"]
    assert len(inc) == 1 and {k: inc[0][k] for k in ("delta", "total", "limit")} == {"delta": 2, "total": 4, "limit": 17}
    assert "idx" in inc[0] and "pct" in inc[0]   # for the map's incident marks
    assert ("engine", {"warnings": ["water_temp"]}) in got
    assert [g for g in got if g[0] == "flag"] == [("flag", {"flag": "yellow"}), ("flag", {"flag": "green"})]
    assert ("rain_start", {"value": 0.2}) in got and ("declared_wet", {"value": True}) in got
    assert ("track_wetness", {"value": "lightly_wet", "prev": "dry"}) in got
    assert ("tow", {"towS": 30.0}) in got


def test_incident_limit_comes_from_the_session():
    src = FakeSource(realtime=False)
    src.connect()
    sess = build_session(src)
    assert sess["options"]["IncidentLimit"] == 17
    d = Deriver()
    build_cars(src, sess, d)
    assert d.incident_limit == 17
    sess["options"]["IncidentLimit"] = "unlimited"
    build_cars(src, sess, d)
    assert d.incident_limit is None


def test_bridge_sends_the_feed_whole_then_only_what_is_new():
    from pitwall import bridge as bridge_mod
    from relay.server import msg_is_full
    src = FakeSource(realtime=False, speed=4.0)
    src.connect()
    b = bridge_mod.Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None)
    feeds, now = [], 0.0
    for i in range(15 * 60 * 3):
        src.tick()
        now += 1.0
        feeds += [m for m in b._build(now) if m["type"] == "feed"]
    assert feeds[0]["full"] is True and msg_is_full(json.dumps(feeds[0], separators=(",", ":")))
    inc = [m for m in feeds[1:] if not m["full"]]
    assert inc, "no incremental feed messages"
    last = feeds[0]["seq"]
    for m in inc:
        seqs = [x["seq"] for x in m["laps"] + m["events"]]
        assert seqs and min(seqs) > last and max(seqs) == m["seq"]
        last = m["seq"]
    b._feed_full_due = True                                    # a viewer asked for a resend
    src.tick()
    now += 1.0
    full = [m for m in b._build(now) if m["type"] == "feed"]
    assert full and full[0]["full"] and len(full[0]["laps"]) == len(b.deriver.feed_laps)


# --------------------------------------------------------------------------- #
# Engineer: fuel & stops, lap log, head-to-head, spotter.
# --------------------------------------------------------------------------- #

from pitwall.normalize import CAR_LEFT_RIGHT, fuel_plan, track_length_m, vs_you  # noqa: E402

# Fuel used per lap in the real Indianapolis capture (ferrari296gt3, 2026-10-10),
# in order from lap 1, with whether the lap touched pit road. Lap 20 is the in-lap
# (2.21 L at the limiter), lap 21 the out-lap with ~31 L added.
INDY_FUEL = [(2.41, False), (2.48, False), (2.48, False), (2.45, False), (2.49, False), (2.44, False),
             (2.44, False), (2.47, False), (2.51, False), (2.51, False), (2.50, False), (2.47, False),
             (2.45, False), (2.49, False), (2.48, False), (2.49, False), (2.49, False), (2.48, False),
             (2.46, False), (2.21, True), (-28.55, True), (2.49, False), (2.49, False), (2.47, False)]


def _drive_fuel(d, laps, start=50.0, first_lap=0):
    """Feed fuel() the way the bridge does: mid-lap samples, then the line."""
    fuel, out = start, None
    d.fuel(first_lap, fuel)                                    # first sight, on the line
    for k, (used, pit) in enumerate(laps, start=first_lap + 1):
        out = d.fuel(k - 1, fuel - used / 2, on_pit_road=pit)  # half-way round
        fuel -= used
        out = d.fuel(k, fuel, on_pit_road=pit and k - first_lap == len(laps))
    return out


def test_fuel_burn_leaves_out_pit_laps_real_indianapolis():
    """The in-lap burned 2.21 L against ~2.47 on a racing lap. Averaging it in
    would make the car look more economical than it is."""
    d = Deriver()
    out = _drive_fuel(d, INDY_FUEL[:20])                       # up to and including the in-lap
    green = [u for u, p in INDY_FUEL[:20] if not p][-5:]
    assert abs(out["fuelPerLap"] - sum(green) / 5) < 0.002, out["fuelPerLap"]
    assert d._fuel_by_lap[20]["pit"] is True
    out = _drive_fuel(d, INDY_FUEL[20:24], start=20.0, first_lap=20)
    assert d._fuel_by_lap[21]["refuel"] is True and d._fuel_by_lap[21]["usedL"] is None
    assert 2.4 < out["fuelPerLap"] < 2.55


def test_fuel_skips_a_partial_first_lap_and_a_counter_that_goes_back():
    d = Deriver()
    d.fuel(4, 50.0)                                            # connected part-way round lap 5
    d.fuel(5, 49.1)                                            # 0.9 L: only part of a lap
    assert d._fuel_per_lap_samples == [] and d._fuel_by_lap[5]["usedL"] is None
    d.fuel(6, 46.6)
    assert d._fuel_per_lap_samples == [2.5] or abs(d._fuel_per_lap_samples[0] - 2.5) < 1e-9
    d.fuel(5, 46.5)                                            # the counter went back (seen at Indianapolis)
    d.fuel(6, 45.9)                                            # ...and forward again: not a lap
    assert len(d._fuel_per_lap_samples) == 1


def test_fuel_plan_no_stop():
    # 30 L, 2.5 L/lap, half-way round lap 16 of 25 (10 to go counting this one)
    p = fuel_plan(30.0, 2.5, 10, False, 0.5, 16, 100.0)
    assert p["stops"] == 0 and p["finishLap"] == 25
    assert p["needL"] == 23.75 and p["spareAtFlagL"] == 6.25 and p["spareAtFlagLaps"] == 2.5


def test_fuel_plan_one_stop_window_and_amounts():
    # 20 L, 2.5 L/lap, on the line at lap 6 with 20 to go (flag after lap 25),
    # 40 L tank. Need 50, short 30: one stop.
    p = fuel_plan(20.0, 2.5, 20, False, 0.0, 6, 40.0, menu_fuel_l=35.0, menu_fuel_on=True)
    assert p["stops"] == 1 and not p["fillToFull"]
    # latest: 20 L reaches the end of lap 6 + 7 = 13 (8 lines at 2.5 L)
    # earliest: after lap 9 the 16 laps left need 40 L, a full tank
    assert p["windowOpenLap"] == 9 and p["windowCloseLap"] == 13 and not p["boxNow"]
    assert p["addThisLapL"] is None, "boxing now cannot reach the flag on one stop"
    assert p["addAtOpenL"] == 30.0 and p["addAtCloseL"] == 30.0
    assert p["addAtOpenSpareL"] == 30.0                        # a spare lap does not fit in the tank
    assert p["menuVsPlanL"] == 5.0                             # menu 35 against 30 needed


def test_fuel_plan_two_stops_fill_and_box_now():
    # 20 L, 2.5 L/lap, 40 to go from lap 6, 40 L tank: short 80 -> two stops, fill at the first
    p = fuel_plan(20.0, 2.5, 40, False, 0.0, 6, 40.0)
    assert p["stops"] == 2 and p["fillToFull"] and p["windowCloseLap"] == 13
    assert p["addAtCloseL"] == 40.0
    # 1.0 L at 80 % of a lap: just enough to reach the line -- this lap is the last chance
    p = fuel_plan(1.0, 2.5, 10, False, 0.8, 6, 40.0)
    assert p["boxNow"] and p["windowCloseLap"] == 6
    # 0.3 L at half-way: cannot finish this lap
    p = fuel_plan(0.3, 2.5, 10, False, 0.5, 6, 40.0)
    assert p["boxNow"] and p["windowCloseLap"] is None
    # no burn measured yet: no plan
    assert fuel_plan(20.0, None, 10, False, 0.5, 6, 40.0)["stops"] is None


def test_usable_tank_comes_from_the_session():
    src = FakeSource(realtime=False)
    src.connect()
    sess = build_session(src)
    d = Deriver()
    build_cars(src, sess, d)
    assert d.tank_l == 110.0
    sess["driverCar"]["DriverCarMaxFuelPct"] = 0.5            # a series restricting the tank
    build_cars(src, sess, d)
    assert d.tank_l == 55.0


def test_sector_timing_waits_for_a_line_and_keeps_each_lap():
    """30 s laps at constant speed, sampled every 0.25 s; S1/S2/S3 start at
    0, 0.33, 0.66, so on a full lap they are 9.9, 9.9 and 10.2 s."""
    d = Deriver()
    starts = [0.0, 0.33, 0.66]
    t, pct, out = 0.0, 0.20, None

    def run_to(stop_t, on_track=lambda p: True):
        nonlocal t, pct, out
        while t < stop_t - 1e-9:
            t = round(t + 0.25, 4)
            pct = (pct + 0.25 / 30.0) % 1.0
            out = d.sectors(1, t, pct, starts, on_track(pct))

    d.sectors(1, t, pct, starts, True)                        # first sight inside sector 1
    run_to(15.0)                                              # through the S2 line and the S3 line
    assert out["sectorS"][0] is None, "a partial sector was banked"
    assert abs(out["sectorS"][1] - 9.9) < 1e-6
    run_to(24.5)                                              # across the start/finish line
    lap = d._sect[1]["lapDone"][1]
    assert lap[0] is None and abs(lap[1] - 9.9) < 1e-6 and abs(lap[2] - 10.2) < 1e-6
    # next lap: an off in S2 leaves S2 empty for THAT lap, not last lap's 9.9
    run_to(54.5, on_track=lambda p: not (0.45 < p < 0.5))
    lap = d._sect[1]["lapDone"][1]
    assert abs(lap[0] - 9.9) < 1e-6 and lap[1] is None and abs(lap[2] - 10.2) < 1e-6


def test_lap_log_entries_carry_sectors_fuel_and_incidents():
    r = _fake_race()
    d = r["d"]
    mine = [e for e in d.feed_laps if e["idx"] == 0]
    green = [e for e in mine if e["lapS"] and not e["pit"] and e["sectors"] and all(e["sectors"])]
    assert len(green) >= 5
    for e in green:
        assert abs(sum(e["sectors"]) - e["lapS"]) < 0.35, e    # timed here vs iRacing's lap
        assert e["fuelL"] and 2.0 < e["fuelL"] < 5.0, e
        assert e["incL"] == 0
    assert any(e.get("refuel") for e in mine), "the stop lap was not marked as a refuel"
    others = [e for e in d.feed_laps if e["idx"] != 0]
    assert all("fuelL" not in e for e in others), "fuel is only known for your own car"


def test_head_to_head_is_their_time_minus_yours():
    me = _car(0, lastLapS=100.0, bestLapS=99.0, derived={"bestSectorS": [33.0, 33.0, 33.0]})
    c = _car(1, lastLapS=99.5, bestLapS=99.4, derived={"bestSectorS": [32.9, 33.2, None]})
    v = vs_you(c, me, 99.6, 100.2)
    assert v == {"lastLapS": -0.5, "bestLapS": 0.4, "paceS": -0.6, "bestSectorS": [-0.1, 0.2, None]}
    assert vs_you(_car(2, lastLapS=-1.0), me, None, 100.0)["lastLapS"] is None


def test_spotter_distances_and_closing():
    d = Deriver()
    for k in range(12):                                        # 1.1 s at ~10 Hz
        t = k * 0.1
        cars = [_car(0, pct=0.5),
                _car(1, pct=0.5 + (50.0 - 5.0 * t) / 5000.0),   # 50 m ahead, you close at 5 m/s
                _car(2, pct=0.5 - (100.0 - 3.0 * t) / 5000.0),  # 100 m behind, closing at 3 m/s
                _car(3, pct=0.5 + 200.0 / 5000.0),             # out of range
                _car(4, pct=0.49, onPitRoad=True)]             # in the pit lane alongside
        near = {n["idx"]: n for n in d.nearby(cars, 0, 5000.0, t)}
    assert set(near) == {1, 2, 4}
    assert abs(near[1]["meters"] - (50.0 - 5.5)) < 0.2 and abs(near[1]["closingMs"] - 5.0) < 0.15
    assert abs(near[2]["meters"] + (100.0 - 3.3)) < 0.2 and abs(near[2]["closingMs"] - 3.0) < 0.15
    assert near[4]["inPit"] is True
    assert d.nearby(cars, 0, None, 2.0) == []                  # no track length: nothing


def test_track_length_and_spotter_decoding():
    assert track_length_m({"track": {"TrackLength": "5.10 km"}}) == 5100.0
    assert abs(track_length_m({"track": {"TrackLength": "2.50 mi"}}) - 4023.36) < 0.01
    assert track_length_m({"track": {"TrackLength": None}}) is None
    src = FakeSource(realtime=False)
    src.connect()
    d = Deriver()
    for raw, name in CAR_LEFT_RIGHT.items():
        src._vars["CarLeftRight"] = raw
        st = build_state(src, d)
        assert st["player"]["carLeftRight"] == name and st["player"]["carLeftRightRaw"] == raw


def test_fuel_to_finish_and_the_plan_agree():
    """Two panels showing two different 'fuel needed' numbers is worse than one."""
    src = FakeSource(realtime=False, speed=4.0)
    src.connect()
    d = Deriver()
    sess = build_session(src)
    checked = 0
    for i in range(15 * 60 * 9):
        src.tick()
        if i % 4 == 0:
            build_cars(src, sess, d)
        st = build_state(src, d)
        dv = st["derived"]
        if dv["fuelToFinishL"] is not None and dv["plan"]["needL"] is not None:
            assert abs(dv["fuelToFinishL"] - dv["plan"]["needL"]) < 0.011
            checked += 1
    assert checked > 100


# --------------------------------------------------------------------------- #
# Spectator first: follow any car, and everything known about every car.
# --------------------------------------------------------------------------- #

def _spectate(src_kw=None, minutes=4.0, every=2, cam=5, sess_follow=True):
    src = FakeSource(realtime=False, **(src_kw or {}))
    src.connect()
    src.cam["car"] = cam
    d = Deriver()
    sess, siu = build_session(src), src.session_info_update
    st = cars = None
    for i in range(int(60 * 60 * minutes / (src_kw or {}).get("speed", 1.0))):
        src.tick()
        if sess_follow and src.session_info_update != siu:
            sess, siu = build_session(src), src.session_info_update
        if i % every == 0:
            st = build_state(src, d)
            cars = build_cars(src, sess, d)
    return src, d, st, cars


def test_shared_reference_is_your_car_else_the_camera():
    """The shared stream's reference is not choosable: a viewer who follows
    another car gets a RefView of their own (see the tests below)."""
    # driving: your own car
    src = FakeSource(realtime=False)
    src.connect()
    sess, d = build_session(src), Deriver()
    cars = build_cars(src, sess, d)
    assert cars["derived"]["ref"] == {"idx": 0, "source": "your_car", "own": 0, "camera": 0}
    assert not hasattr(d, "focus_manual")
    # spectating: no car of ours (PlayerCarIdx 63), so the camera car
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    src.cam["car"] = 5
    src.tick()
    sess, d = build_session(src), Deriver()
    r = build_cars(src, sess, d)["derived"]["ref"]
    assert r == {"idx": 5, "source": "camera", "own": None, "camera": 5}


def test_switching_the_followed_car_is_not_a_position_change():
    src, d, st, cars = _spectate({"speed": 4.0}, minutes=6.0, cam=5)
    before = [e for e in d.feed_events if e["kind"] == "position"]
    sess = build_session(src)
    for cam in (11, 2, 14):                                     # three camera cuts
        src.cam["car"] = cam
        for _ in range(40):
            src.tick()
            build_state(src, d)
            build_cars(src, sess, d)
    after = [e for e in d.feed_events if e["kind"] == "position"]
    assert len(after) - len(before) <= 1, after[len(before):]   # one real change at most, not one per cut
    assert d._uc == {} or all(r["kind"] in ("they_stopped", "you_stopped") for r in d._uc.values())


def test_gap_to_the_followed_car_at_each_line():
    d = Deriver()
    d._laps = {1: [{"lap": 3, "t": 300.0, "tx": 300.0}, {"lap": 4, "t": 400.0, "tx": 400.2}],
               2: [{"lap": 3, "t": 302.5}, {"lap": 4, "t": 401.0, "tx": 401.05}],
               3: [{"lap": 2, "t": 280.0}]}
    d.ref_idx = 1
    assert d.ref_gaps() == {"2": [[3, 2.5], [4, 0.85]]}       # car 3 shares no lap with the reference
    d.ref_idx = 2
    assert d.ref_gaps() == {"1": [[3, -2.5], [4, -0.85]]}


def test_a_followed_car_gets_its_own_view_and_the_shared_stream_stays_put():
    """A viewer following car 9 is sent a refview measured from car 9. The shared
    stream (cars, refgaps, the feed) keeps following your car for everyone."""
    from pitwall import bridge as bridge_mod
    src = FakeSource(realtime=False, speed=4.0)
    src.connect()
    b = bridge_mod.Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None, render_cars=False)
    msgs, now = [], 0.0
    for _ in range(15 * 60 * 3):
        src.tick()
        now += 1.0
        msgs += b._build(now)
    rg = [m for m in msgs if m["type"] == "refgaps"]
    assert rg and rg[-1]["ref"] == 0 and rg[-1]["series"]
    assert not [m for m in msgs if m["type"] == "refview"], "nobody follows a car: no views"
    b._set_refs([9], fresh=9)                                   # a viewer chose car 9
    out = []
    for _ in range(40):
        src.tick()
        now += 1.0
        out += b._build(now)
    views = [m for m in out if m["type"] == "refview"]
    assert views and views[0]["full"] and all(v["ref"] == 9 for v in views)
    assert views[0]["derived"]["ref"]["idx"] == 9 and views[0]["derived"]["ref"]["source"] == "chosen"
    assert views[0]["gaps"], "the per-lap gaps to car 9 come with its view"
    assert "9" in views[0]["cars"] and views[0]["cars"]["9"]["toPlayerS"] is None
    # the shared stream did not move
    assert b.deriver.ref_idx == 0
    shared = [m for m in out if m["type"] == "cars" and m.get("full")]
    assert shared and shared[-1]["derived"]["ref"]["idx"] == 0
    assert all(m["ref"] == 0 for m in out if m["type"] == "refgaps")
    # nobody follows it any more: the view stops
    b._set_refs([])
    src.tick()
    assert not [m for m in b._build(now + 100.0) if m["type"] == "refview"]


def test_a_chosen_car_matches_the_shared_stream_for_the_same_car():
    """relative_to must compute exactly what build_cars computes when the shared
    stream follows the same car: same methods, separate history. Checked at every
    update of a race with stops, for every relative field."""
    from pitwall.normalize import REF_CAR_FIELDS, RefView, relative_to
    src = FakeSource(realtime=False, speed=4.0)
    src.connect()
    sess, siu = build_session(src), src.session_info_update
    d = Deriver()
    view = RefView(d, 0)                   # car 0 is also the shared reference here
    seen = {k: 0 for k in REF_CAR_FIELDS + ("rejoin", "undercut", "traffic", "nearby", "gaps")}
    for i in range(60 * 60 * 12 // 4):     # long enough for pit stops, so undercut is exercised
        src.tick()
        if src.session_info_update != siu:
            sess, siu = build_session(src), src.session_info_update
        if i % 4:
            continue
        build_state(src, d)
        cars = build_cars(src, sess, d)
        rv = relative_to(view, cars, sess, cars["t"])
        for c in cars["cars"]:
            for k in REF_CAR_FIELDS:
                assert c["derived"].get(k) == rv["cars"][str(c["idx"])][k], (i, c["idx"], k)
                seen[k] += rv["cars"][str(c["idx"])][k] is not None
        for k in ("rejoin", "undercut", "traffic", "nearby"):
            assert cars["derived"][k] == rv["derived"][k], (i, k)
            seen[k] += bool(rv["derived"][k])
        assert d.ref_gaps() == rv["gaps"]
        seen["gaps"] += bool(rv["gaps"])
    assert all(n > 0 for k, n in seen.items() if k not in ("youCatchLaps", "catchesYouLaps")), seen


def test_a_refview_keeps_its_own_history_and_nothing_else():
    from pitwall.normalize import RefView
    d = Deriver()
    v = RefView(d, 4)
    v._near_hist[1] = [(0.0, 10.0)]
    assert d._near_hist == {}, "a viewer's history never leaks into the shared stream"
    assert v.GAP_SAMPLE_S == d.GAP_SAMPLE_S              # everything else is read from the shared Deriver
    try:
        v.tank_l = 50.0
    except AttributeError:
        pass
    else:
        raise AssertionError("writing shared state through a RefView must fail loudly")


def test_gap_series_sent_as_new_points_rebuild_the_whole_chart():
    """refgaps went out whole whenever any car finished a lap. Now: whole on a
    reference change, then only new points. Merged the way the dashboard merges
    them, they must give exactly the series the Deriver holds."""
    from pitwall import bridge as bridge_mod
    src = FakeSource(realtime=False, speed=4.0)
    src.connect()
    b = bridge_mod.Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None, render_cars=False)
    now, msgs = 0.0, []
    for _ in range(15 * 60 * 4):
        src.tick()
        now += 1.0
        msgs += [m for m in b._build(now) if m["type"] == "refgaps"]
    got = None
    for m in msgs:                                    # client/index.html's merge
        if m["full"] or got is None or got["ref"] != m["ref"]:
            if m["full"]:
                got = {"ref": m["ref"], "series": {k: list(v) for k, v in m["series"].items()}}
        else:
            for k, pts in m["series"].items():
                got["series"].setdefault(k, []).extend(pts)
                del got["series"][k][:-200]
    want = b.deriver.ref_gaps()
    assert got is not None and got["ref"] == b.deriver.ref_idx
    assert got["series"] == {k: v[-200:] for k, v in want.items() if v}
    assert sum(1 for m in msgs if m["full"]) == 1, "whole only once, for the one reference"
    whole = sum(len(json.dumps(b.deriver.ref_gaps())) for _ in msgs)   # what sending it whole every time costs at the end
    assert sum(len(json.dumps(m)) for m in msgs) < whole / 3


def test_a_late_joiner_gets_the_whole_gap_chart():
    import asyncio
    import importlib.util
    import websockets
    from websockets.asyncio.server import serve
    spec = importlib.util.spec_from_file_location(
        "relay_server4", os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"))
    relay_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay_mod)

    async def run():
        relay = relay_mod.Relay("B", "V", None)
        async with serve(relay.handler, "127.0.0.1", 0) as server:
            u = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}"
            async with websockets.connect(u + "/bridge?token=B") as br:
                await br.send(json.dumps({"type": "refgaps", "full": True, "ref": 0, "series": {"1": [[1, 2.0]]}}))
                await br.send(json.dumps({"type": "refgaps", "full": False, "ref": 0,
                                          "series": {"1": [[2, 2.5]], "3": [[2, -1.0]]}}))
                await asyncio.sleep(0.2)
                async with websockets.connect(u + "/view?token=V") as v:
                    got = []
                    try:
                        while True:
                            got.append(json.loads(await asyncio.wait_for(v.recv(), 0.5)))
                    except asyncio.TimeoutError:
                        pass
        return [m for m in got if m["type"] == "refgaps"]

    rg = asyncio.run(run())
    assert len(rg) == 1 and rg[0]["full"] and rg[0]["ref"] == 0
    assert rg[0]["series"] == {"1": [[1, 2.0], [2, 2.5]], "3": [[2, -1.0]]}


def test_trends_and_peak_g_are_derived_in_the_bridge():
    """Weather and engine trends and the peak g were computed in the dashboard,
    against the rule that every number comes from normalize.py."""
    d = Deriver()
    w = {"airTempC": 20.0, "trackTempC": 30.0, "humidity": 0.5, "precipitationPct": 0.0, "windMs": 3.0}
    p = {"waterTempC": 90.0, "oilTempC": 100.0, "oilPressBar": 4.0,
         "accel": {"latG": [0.5, 1.2, -1.6], "longG": [-0.3, 0.9, 0.4]}}
    out = d.trends(0.0, w, p, player_live=True)
    assert out["weatherTrend"] is None and out["engineTrend"] is None   # one sample is not a trend
    assert out["ggPeakG"] == round(math.hypot(-1.6, 0.4), 2)            # the largest of the three: 1.65 g
    for i in range(1, 400):
        w = {**w, "airTempC": 20.0 + i * 0.01, "trackTempC": 30.0 - i * 0.02}
        p = {**p, "waterTempC": 90.0 + i * 0.005, "accel": {"latG": [0.1], "longG": [0.1]}}
        out = d.trends(i * 1.0, w, p, player_live=True)
    wt, et = out["weatherTrend"], out["engineTrend"]
    # 10 s samples, the last 90 kept: 15 minutes of a 400 s run is all of it
    assert wt["spanS"] == 390.0
    assert wt["change"]["airTempC"] == round(3.9, 4) and wt["change"]["trackTempC"] == round(-7.8, 4)
    assert wt["change"]["humidity"] == 0.0
    assert len(wt["series"]["airTempC"]) <= Deriver.TREND_DRAW_POINTS + 1
    assert wt["series"]["airTempC"][-1] == round(20.0 + 390 * 0.01, 4)  # the line ends at the latest sample
    assert et["change"]["waterTempC"] == round(395 * 0.005, 4)
    # spectating: the player block reads zeros, so the engine trend stops sampling
    before = d.trends(500.0, w, {**p, "waterTempC": 0.0}, player_live=False)["engineTrend"]
    assert before["change"]["waterTempC"] == et["change"]["waterTempC"]
    # and a state message carries them
    src = FakeSource(realtime=False)
    src.connect()
    d2, st = Deriver(), None
    for i in range(60 * 30):
        src.tick()
        if i % 4 == 0:
            st = build_state(src, d2)
    assert st["derived"]["weatherTrend"] is not None and "airTempC" in st["derived"]["weatherTrend"]["change"]


def test_laps_led_and_top_speed_count_the_whole_race():
    """The lap list keeps 200 laps per car, and laps led / top speed were counted
    from it -- so past lap 200 a car's laps led silently stopped growing."""
    d = Deriver()
    for lap in range(1, 301):
        d._lapst[7] = {"pend": {"idx": 7, "lap": lap, "lapS": 90.0 + (lap % 3) * 0.1, "pit": False,
                                "pos": 1, "classPos": 1, "topSpeedMs": 80.0 + (5.0 if lap == 10 else 0.0),
                                "jump": False, "_td": True, "t": lap * 90.0, "sectors": None}}
        d._finish_lap(7, d._lapst[7])
    assert len(d._laps[7]) == 200
    ex = d.car_extras({"idx": 7, "pos": 1, "classPos": 1, "lap": 301}, 300 * 90.0, {}, None, True, False)
    assert ex["lapsLedSeen"] == 300 and ex["classLapsLedSeen"] == 300
    assert ex["topSpeedBestMs"] == 85.0, "the fastest lap was lap 10, long out of the 200 kept"
    assert d.pace(7) is not None and ex["consistencyS"] is not None


def test_relay_keeps_each_viewers_followed_car_to_that_viewer():
    """Following a car changes nothing for anyone else: the bridge is told which
    cars are wanted, and each car's view goes only to the viewers following it."""
    import asyncio
    import importlib.util
    import websockets
    from websockets.asyncio.server import serve
    spec = importlib.util.spec_from_file_location(
        "relay_server2", os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"))
    relay_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay_mod)

    async def drain(ws, t=0.6):
        got = []
        try:
            while True:
                got.append(json.loads(await asyncio.wait_for(ws.recv(), t)))
        except asyncio.TimeoutError:
            pass
        return got

    async def run():
        relay = relay_mod.Relay("B", "V", None)
        relay.set_guest_token("G")                   # viewer a joins with a share link
        async with serve(relay.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            u = f"ws://127.0.0.1:{port}"
            async with websockets.connect(u + "/bridge?token=B") as bridge, \
                    websockets.connect(u + "/view?token=G") as a, websockets.connect(u + "/view?token=V") as b:
                await asyncio.sleep(0.2)
                for m in ({"type": "focus", "idx": "5; drop"}, {"type": "focus", "idx": True},
                          {"type": "focus", "idx": 99}, {"type": "focus", "idx": 5, "extra": "x" * 50}):
                    await a.send(json.dumps(m))
                to_bridge = [m for m in await drain(bridge) if m.get("type") == "refs"]
                for ref in (5, 7):
                    await bridge.send(json.dumps({"type": "refview", "full": True, "ref": ref, "cars": {}}))
                await bridge.send(json.dumps({"type": "cars", "full": True, "t": 1, "cars": {}}))
                got_a, got_b = await drain(a), await drain(b)
                await a.close()
                after = [m for m in await drain(bridge) if m.get("type") == "refs"]
            return to_bridge, got_a, got_b, after

    to_bridge, got_a, got_b, after = asyncio.run(run())
    assert to_bridge == [{"type": "refs", "idxs": [5], "fresh": 5}], to_bridge   # only the clean choice
    assert [m["ref"] for m in got_a if m["type"] == "refview"] == [5]            # a guest, following 5
    assert not [m for m in got_b if m["type"] == "refview"]                       # b follows nothing
    assert any(m["type"] == "cars" for m in got_b), "the shared stream still reaches everyone"
    assert after == [{"type": "refs", "idxs": [], "fresh": None}]                 # a left: view no longer wanted


def test_relay_caps_how_many_cars_are_followed_at_once():
    import asyncio
    import importlib.util
    import websockets
    from websockets.asyncio.server import serve
    spec = importlib.util.spec_from_file_location(
        "relay_server3", os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"))
    relay_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(relay_mod)
    n = relay_mod.MAX_REFS

    async def run():
        relay = relay_mod.Relay("B", "V", None)
        async with serve(relay.handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            viewers = [await websockets.connect(f"ws://127.0.0.1:{port}/view?token=V") for _ in range(n + 2)]
            results = []
            for i, v in enumerate(viewers):
                await v.send(json.dumps({"type": "focus", "idx": i if i <= n else 0}))
                while True:
                    m = json.loads(await asyncio.wait_for(v.recv(), 2))
                    if m.get("type") == "focus_result":
                        results.append(m["ok"])
                        break
            for v in viewers:
                await v.close()
            return results

    results = asyncio.run(run())
    assert results[:n] == [True] * n
    assert results[n] is False, "one car more than the cap is refused"
    assert results[n + 1] is True, "following a car someone already follows costs nothing"


def test_speed_from_position_matches_the_fake():
    """Spectating, the player block's Speed follows the camera car: the fake's
    true speed for that car, against the speed worked out from positions."""
    import statistics
    src = FakeSource(realtime=False, spectator=True, incidents=False)
    src.connect()
    src.cam["car"] = 5
    sess, d = build_session(src), Deriver()
    errs = []
    for i in range(60 * 200):
        src.tick()
        if i % 2 == 0:
            st = build_state(src, d)
            cars = build_cars(src, sess, d)
            c = next(x for x in cars["cars"] if x["idx"] == 5)
            if c["derived"]["speedMs"] is not None and st["player"]["speedMs"]:
                errs.append(abs(c["derived"]["speedMs"] - st["player"]["speedMs"]))
    errs.sort()
    assert len(errs) > 5000
    assert errs[len(errs) // 2] < 0.3, errs[len(errs) // 2]
    assert errs[int(len(errs) * 0.95)] < 2.5, errs[int(len(errs) * 0.95)]


def test_speed_restarts_after_a_jump_and_ignores_no_track_length():
    d = Deriver()
    c = _car(1, pct=0.10)
    assert d.car_speed(c, 0.0, 5000.0) is None                 # needs two samples
    c["pct"] = 0.10 + 25 / 5000                                # 25 m in half a second
    assert abs(d.car_speed(c, 0.5, 5000.0) - 50.0) < 1e-6
    c["pct"] = 0.60                                            # teleported half a lap
    assert d.car_speed(c, 1.0, 5000.0) is None
    c["pct"] = 0.61
    assert d.car_speed(c, 3.0, 5000.0) is None                 # two seconds without a sample: start again
    assert d.car_speed(c, 3.5, None) is None


def test_every_car_gets_its_stints_grid_and_consistency():
    d = Deriver()
    # car 5: seen from the start, stops after lap 12 and lap 25
    d.pit(5, 0.0, False, False, 0, pct=0.0)
    for lap, t in ((12, 1000.0), (25, 2100.0)):
        d.pit(5, t, True, False, lap, pct=0.95)
        d.pit(5, t + 5, True, True, lap, pct=0.99)
        d.pit(5, t + 30, True, False, lap + 1, pct=0.99)
        d.pit(5, t + 40, False, False, lap + 1, pct=0.05)
    c = _car(5, lap=30, pos=4, classPos=2, fastRepairsUsed=1, compound=0, lapCompleted=29)
    # through the real path (record_lap -> _finish_lap), which keeps the running
    # figures that laps led, top speed, pace and consistency are read from
    for k, s in ((1, 100.0), (2, 100.4), (3, 99.8), (4, 100.2), (5, 100.0), (6, 100.6)):
        d._lapst[5] = {"pend": {"idx": 5, "lap": k, "lapS": s, "pit": False, "pos": 1 if k < 3 else 4,
                                "classPos": 1 if k < 5 else 2, "t": k * 100.0, "sectors": None}}
        d._finish_lap(5, d._lapst[5])
    x = d.car_extras(c, 2200.0, {5: {"pos": 9, "classPos": 4}}, 2, True, False)
    assert x["stintsSeen"] == [12, 12] and x["stintStartLap"] == 26
    assert x["lapsThisStint"] == 4 and x["nextStopLapEst"] == 38
    assert x["gridPos"] == 9 and x["posGained"] == 5 and x["classPosGained"] == 2
    assert x["lapsLedSeen"] == 2 and x["classLapsLedSeen"] == 4
    assert x["fastRepairsLeft"] == 1
    laps = [100.4, 99.8, 100.2, 100.0, 100.6]
    m = sum(laps) / 5
    assert abs(x["consistencyS"] - (sum((v - m) ** 2 for v in laps) / 5) ** 0.5) < 1e-3
    # a stint that began before we connected has no known length
    d2 = Deriver()
    d2.pit(6, 0.0, False, False, 14, pct=0.5)
    d2.pit(6, 10.0, True, False, 20, pct=0.95)
    d2.pit(6, 50.0, False, False, 21, pct=0.05)
    x = d2.car_extras(_car(6, lap=23), 60.0, {}, None, True, False)
    assert x["stintsSeen"] == [] and x["stintStartLap"] == 21 and x["fastRepairsLeft"] is None


def test_compound_changes_and_pace_by_compound():
    d = Deriver()
    cars = [_car(i, classId=10, compound=0) for i in range(4)] + [_car(9, classId=10, compound=1)]
    for c in cars:
        d.car_extras(c, 0.0, {}, None, True, False)
    for i, s in enumerate((100.0, 101.0, 102.0, 103.0)):
        d._laps[i] = [{"lap": 5, "lapS": s, "pit": False}]
    d._laps[9] = [{"lap": 5, "lapS": 98.0, "pit": False}]
    cp = {o["compound"]: o for o in d.compound_pace(cars)}
    assert cp[0]["medianLastLapS"] == 101.5 and cp[1]["medianLastLapS"] == 98.0
    assert cp[1]["fastest"] and cp[0]["vsFastestS"] == 3.5
    cars[0]["compound"] = 1                                    # a switch
    x = d.car_extras(cars[0], 10.0, {}, None, True, False)
    assert x["compoundChanges"] == 1
    ev = [e for e in d.feed_events if e["kind"] == "compound_change"]
    assert ev and ev[-1]["fromCompound"] == 0 and ev[-1]["toCompound"] == 1


def test_team_race_driver_stints_from_the_session_info():
    src, d, st, cars = _spectate({"speed": 4.0, "team": True}, minutes=16.0, cam=5)
    swaps = [e for e in d.feed_events if e["kind"] == "driver_change"]
    assert swaps, "no driver swap seen in a 16-minute team race"
    e = swaps[0]
    c = next(x for x in cars["cars"] if x["idx"] == e["idx"])
    drv = c["derived"]["drivers"]
    assert len(drv) >= 2 and sum(1 for x in drv if x["current"]) == 1
    assert sum(x["timeS"] for x in drv) > 600
    # outside a team race, a single driver is not listed
    src2, d2, _, cars2 = _spectate({"speed": 4.0}, minutes=2.0)
    assert all(x["derived"]["drivers"] is None for x in cars2["cars"])


def test_refuel_rate_is_measured_at_your_stops():
    d = Deriver()
    d.refuel(0.0, 5.0, False)
    t, f = 100.0, 5.0
    for k in range(60):                                        # 2 s settling in the box
        d.refuel(t, f, True)
        t += 1 / 30
    for k in range(int(12 * 30)):                              # 12 s at 2.6 L/s
        f += 2.6 / 30
        t += 1 / 30
        d.refuel(t, f, True)
    for k in range(60):
        t += 1 / 30
        d.refuel(t, f, True)
    d.refuel(t, f, False)                                      # leaves the box
    assert abs(d.refuel_rate() - 2.6) < 0.01
    # and on the fake, whose stop refuels at 2.6 L/s
    src, d, st, cars = _spectate({"speed": 4.0, "player_pit_lap": 3}, minutes=8.0, sess_follow=False)
    assert abs(st["derived"]["refuelLps"] - 2.6) < 0.1, st["derived"]["refuelLps"]


def test_in_car_adjustments_are_reported_once_settled():
    d = Deriver()
    dc = {"brakeBias": 54.0, "tractionControl": 5.0, "dashPage": 1}
    d.adjustments(0.0, dc, 3, True)                            # seeds
    for k, bb in enumerate((54.5, 55.0, 55.5)):                # three clicks in a second
        d.adjustments(0.3 * (k + 1), dict(dc, brakeBias=bb), 3, True)
    d.adjustments(1.5, dict(dc, brakeBias=55.5, dashPage=4), 3, True)   # page flips are not news
    assert [e for e in d.feed_events if e["kind"] == "adjust"] == []
    d.adjustments(5.0, dict(dc, brakeBias=55.5), 4, True)
    adj = [e for e in d.feed_events if e["kind"] == "adjust"]
    assert len(adj) == 1 and adj[0]["control"] == "brakeBias"
    assert adj[0]["fromValue"] == 54.0 and adj[0]["toValue"] == 55.5 and adj[0]["onLap"] == 3
    d.adjustments(6.0, dict(dc, brakeBias=None), 4, False)     # spectating: nothing
    assert len([e for e in d.feed_events if e["kind"] == "adjust"]) == 1


def test_any_car_lap_entries_carry_top_speed_and_crossing_time():
    r = _fake_race()
    entries = [e for e in r["d"].feed_laps if e["idx"] == 7 and e["lapS"] and not e["pit"]]
    assert len(entries) >= 5
    assert all(e["topSpeedMs"] and 40 < e["topSpeedMs"] < 120 for e in entries)
    assert all("tx" in e and abs(e["tx"] - e["t"]) < 1.0 for e in entries if e["sectors"])


def test_per_car_steering_and_tyre_names_pass_through():
    src = FakeSource(realtime=False)
    src.connect()
    sess = build_session(src)
    assert sess["driverTires"] == [{"index": 0, "type": "Hard"}, {"index": 1, "type": "Wet"}]
    cars = build_cars(src, sess, Deriver())
    assert all("steerRad" in c for c in cars["cars"])


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
