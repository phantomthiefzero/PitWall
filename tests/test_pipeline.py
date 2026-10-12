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
    for _ in range(60 * 200):                               # ~2 laps
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

    N, PAYLOAD = 1500, "x" * 4000

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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
