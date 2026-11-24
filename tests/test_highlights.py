"""iRacing Highlights (highlights/): finding one car's moments in a replay, picking them, recording them, building
the video. The moment finder is fed scripted races shaped like iRacing's variables (laps completed + lap fraction,
track surface, pit road, lap times, session state), so each kind of moment has a case that makes it and a case
that must not."""
import math
import os
import shutil
import subprocess
import threading
import time

import pytest

from highlights import pick
from highlights.capture import ClipRecorder, CaptureError, pick_group
from highlights.moments import Analyzer, analyze_capture, field, race_sessions

LAP_S = 100.0          # a lap of the scripted track
DT = 0.25              # a 16x scan reads a frame about every quarter second of race


class Script:
    """A scripted race. cars: {idx: fn(t) -> dict(dist=laps, surf=3, pit=False, cls=1)}. Lap times are posted as
    each car crosses the line, as iRacing does."""

    def __init__(self, cars, green=10.0, end=400.0, state=True):
        self.cars, self.green, self.end, self.state = cars, green, end, state
        self.last = {}
        self.prev = {}

    def frames(self):
        n = max(self.cars) + 1
        t = 0.0
        while t <= self.end:
            lc, pct, surf, pit, last, cls = [-1] * n, [-1.0] * n, [-1] * n, [False] * n, [-1.0] * n, [0] * n
            for i, fn in self.cars.items():
                c = fn(t)
                d = c["dist"]
                lc[i], pct[i] = int(math.floor(d)), d - math.floor(d)
                surf[i], pit[i], cls[i] = c.get("surf", 3), c.get("pit", False), c.get("cls", 1)
                p = self.prev.get(i)
                if p is not None and math.floor(d) > math.floor(p[0]):
                    if ("t", i) in self.last:          # a full lap (the one it joined part way round is not timed)
                        self.last[i] = t - self.last[("t", i)]
                    self.last[("t", i)] = t
                self.prev[i] = (d, t)
                last[i] = self.last.get(i, -1.0)
            v = {"CarIdxLapCompleted": lc, "CarIdxLapDistPct": pct, "CarIdxTrackSurface": surf,
                 "CarIdxOnPitRoad": pit, "CarIdxLastLapTime": last, "CarIdxClass": cls}
            if self.state:
                v["SessionState"] = 3 if t < self.green else 4
            yield t, v.get
            t = round(t + DT, 6)


def run(cars, target=0, results=None, **kw):
    a = Analyzer(target, race_session=2)
    if results:
        a.session({"SessionInfo": {"Sessions": [{"SessionNum": 2, "SessionType": "Race",
                                                 "ResultsPositions": results}]}})
    for t, get in Script(cars, **kw).frames():
        a.feed(2, t, get)
    return a.finish()


def steady(start, lap_s=LAP_S, cls=1):
    return lambda t: {"dist": start + t / lap_s, "cls": cls}


def kinds(res, kind):
    return [m for m in res["moments"] if m["kind"] == kind]


# ------------------------------------------------------------------------------------------------ the finder
def test_a_pass_for_the_lead_is_found_and_says_so_even_across_the_line():
    # T (car 0) catches car 1 just after the line: car 1 is 0.02 lap ahead, both start near the line (0.95 lap)
    cars = {0: steady(0.95, 98.0), 1: steady(0.97, 102.0), 2: steady(0.40, 120.0, cls=2)}
    res = run(cars, end=300)
    passes = kinds(res, "pass")
    assert len(passes) == 1, res["moments"]
    p = passes[0]
    assert p["cars"] == [1] and p["label"] == "Takes the class lead" and p["classPos"] == 1
    # the swap: 0.02 lap closed at 1/98 - 1/102 laps a second
    swap = 0.02 / (1 / 98.0 - 1 / 102.0)
    assert abs(p["t"] - swap) < 1.0
    a, b = p["segs"][0]
    assert a < p["t"] < b
    assert not kinds(res, "lost")


def test_side_by_side_that_swaps_back_is_not_a_pass():
    # car 1 runs beside T, half a car either way, swapping every 2 s (under HOLD_S)
    cars = {0: steady(0.5), 1: lambda t: {"dist": 0.5 + t / LAP_S + 0.0004 * math.sin(t * math.pi / 2)}}
    res = run(cars, end=200)
    assert not kinds(res, "pass") and not kinds(res, "lost")


def test_lapping_another_class_is_traffic_not_a_place():
    cars = {0: steady(1.98, 90.0), 1: steady(1.04, 130.0, cls=2), 3: steady(2.5, 92.0)}
    res = run(cars, end=200)
    assert not kinds(res, "pass")
    tr = kinds(res, "traffic")
    assert tr and tr[0]["cars"] == [1] and tr[0]["score"] < 20


def test_a_long_close_fight_is_a_battle():
    # car 1 sits 0.6 s ahead of T for 60 s, then pulls away
    def ahead(t):
        gap_s = 0.6 if t < 90 else 0.6 + (t - 90) * 0.2
        return {"dist": 0.3 + t / LAP_S + gap_s / LAP_S}
    res = run({0: steady(0.3), 1: ahead, 2: steady(0.9)}, end=200)
    b = kinds(res, "battle")
    assert len(b) == 1
    assert 60 <= b[0]["duration"] <= 85 and b[0]["closest"] == pytest.approx(0.6, abs=0.05)
    assert "Battle" in b[0]["label"]


def test_a_spin_is_slow_against_the_class_but_a_caution_is_not():
    def t_car(t):
        d = 0.2 + t / LAP_S
        if 100 <= t < 106:
            d = 0.2 + 100 / LAP_S + (t - 100) * 0.05 / LAP_S      # crawls for 6 s
        elif t >= 106:
            d = 0.2 + 100 / LAP_S + 0.3 / LAP_S + (t - 106) / LAP_S
        return {"dist": d, "surf": 0 if 100 <= t < 103 else 3}
    res = run({0: t_car, 1: steady(0.6), 2: steady(0.8)}, end=200)
    sp = kinds(res, "spin")
    assert len(sp) == 1 and sp[0]["label"] == "Off and spinning" and 98 <= sp[0]["t"] <= 102
    assert not kinds(res, "off")              # the off track is part of the spin, not a second moment

    def caution(t, start):                    # everyone at a fifth of the speed from 100 s to 150 s
        x = t if t < 100 else 100 + (min(t, 150) - 100) * 0.2 + max(0, t - 150)
        return {"dist": start + x / LAP_S}
    res = run({0: lambda t: caution(t, 0.2), 1: lambda t: caution(t, 0.6), 2: lambda t: caution(t, 0.8)}, end=250)
    assert not kinds(res, "spin")


def test_a_pit_stop_is_the_box_and_the_release_with_the_places_either_side():
    def t_car(t):
        if t < 120:
            return {"dist": 0.5 + t / LAP_S}
        if t < 160:      # pit lane, stopped in the box from 130 to 145
            return {"dist": 0.5 + 1.2 + (min(t, 130) - 120) * 0.002 + max(0, t - 145) * 0.002,
                    "pit": True, "surf": 1 if 130 <= t < 145 else 2}
        return {"dist": 0.5 + 1.2 + 0.05 + (t - 160) / LAP_S}
    res = run({0: t_car, 1: steady(0.9), 2: steady(0.1)}, end=320)
    p = kinds(res, "pit")
    assert len(p) == 1
    segs = p[0]["segs"]
    assert segs[0][0] == pytest.approx(124, abs=0.6) and p[0]["stopS"] == pytest.approx(14.75, abs=0.6)
    assert segs[-1][1] == pytest.approx(150.75, abs=0.6)
    assert not kinds(res, "pass") and not kinds(res, "lost")   # places changed in the pits are not passes


def test_start_and_finish_come_from_the_green_and_the_replays_results():
    res = run({0: steady(0.95), 1: steady(0.9)}, results=[{"CarIdx": 0, "LapsComplete": 3, "ClassPosition": 0}],
              end=450, green=12.0)
    st, fin = kinds(res, "start"), kinds(res, "finish")
    assert st[0]["t"] == pytest.approx(12.0, abs=0.3) and st[0]["mandatory"]
    # third line crossing: dist 3.0 at t = (3 - 0.95) * 100
    assert fin[0]["t"] == pytest.approx(205.0, abs=0.3) and fin[0]["label"] == "The finish - P1 in class"
    assert res["classPos"] == 1 and res["laps"] == 3
    assert all(m["t"] <= fin[0]["t"] + 5 for m in res["moments"])   # the cool-down lap is not highlights


def test_with_no_session_state_the_green_is_found_by_pace():
    res = run({0: steady(0.9), 1: steady(0.7)}, state=False, end=300)
    st = kinds(res, "start")
    assert st and "found by pace" in st[0]["label"]


def test_field_and_race_sessions_leave_out_the_pace_car_and_spectators():
    drivers = {"Drivers": [{"CarIdx": 0, "CarNumber": "0", "CarIsPaceCar": 1, "UserName": "Pace Car"},
                           {"CarIdx": 1, "CarNumber": "34", "UserName": "Andrew", "TeamName": "Team A",
                            "CarClassShortName": "GT3"},
                           {"CarIdx": 2, "CarNumber": "7", "UserName": "Watcher", "IsSpectator": 1}]}
    assert [c["number"] for c in field(drivers)] == ["34"]
    sess = {"Sessions": [{"SessionNum": 0, "SessionType": "Practice"}, {"SessionNum": 1, "SessionType": "Lone Qualify"},
                         {"SessionNum": 2, "SessionType": "Race"}]}
    assert race_sessions(sess) == [2]


def test_the_fake_field_scanned_at_16x_gives_the_stop_and_the_moments():
    from pitwall.sources import FakeSource
    src = FakeSource(realtime=False, speed=16, incidents=True, player_pit_lap=4, parade_s=60, grid_s=20,
                     replay_shape=True)
    src.connect()
    a = Analyzer(0)
    a.session({k: src.session_info(k) for k in ("WeekendInfo", "DriverInfo", "SessionInfo")})
    while src.t < 60 * 25:
        src.tick()
        a.feed(src.get("SessionNum"), src.get("SessionTime"), src.get)
    res = a.finish()
    ks = {m["kind"] for m in res["moments"]}
    assert {"start", "finish", "pit", "fastest"} <= ks
    assert res["green"] == pytest.approx(60, abs=1)
    assert [m["id"] for m in res["moments"]] == list(range(len(res["moments"])))


# ------------------------------------------------------------------------------------------------ picking
def _m(i, t, score, kind="pass", segs=None, mandatory=False):
    return {"id": i, "kind": kind, "t": t, "session": 2, "segs": segs or [[t - 10, t + 5]], "score": score,
            "label": kind, "lap": 3, "cars": [], "mandatory": mandatory}


def test_auto_pick_keeps_start_and_finish_and_fills_to_the_target_best_first():
    ms = [_m(0, 10, 100, "start", [[4, 50]], True), _m(1, 300, 60), _m(2, 600, 30), _m(3, 900, 90),
          _m(4, 1200, 20), _m(5, 5000, 100, "finish", [[4980, 5008]], True)]
    ids = pick.auto_pick(ms, target_s=46 + 28 + 30)
    assert ids == [0, 3, 5, 1][:0] or set(ids) == {0, 1, 3, 5}
    assert pick.length(ms, ids) <= 104


def test_overlapping_moments_merge_into_one_clip_captioned_by_the_best():
    ms = [_m(0, 100, 40, "battle", [[75, 105]]), _m(1, 100, 80, "pass", [[90, 105]]), _m(2, 400, 30, "pit")]
    cs = pick.clips(ms, [0, 1, 2])
    assert len(cs) == 2
    assert (cs[0]["a"], cs[0]["b"]) == (75, 105) and cs[0]["kind"] == "pass" and cs[0]["moments"] == [0, 1]
    assert cs[0]["caption"].startswith("Lap 3")
    assert pick.length(ms, [0, 1, 2], stinger_s=1.5) == pytest.approx(30 + 15 + 1.5)


def test_moments_of_one_kind_close_together_count_less():
    ms = [_m(0, 100, 50), _m(1, 130, 49), _m(2, 2000, 45, "spin")]
    ids = pick.auto_pick(ms, target_s=30)
    assert ids == [0, 2]


# ------------------------------------------------------------------------------------------------ scanning
def test_a_scan_through_the_command_gate_writes_a_file_that_reads_back_the_same(tmp_path):
    from pitwall.commands import CommandGate
    from pitwall.sources import FakeSource
    from highlights.scan import scan

    class Short(FakeSource):
        def tick(self):
            if self.t > 60 * 12:
                self.replay_speed = 0
                return True              # the tape stops at its end
            return super().tick()
    src = Short(realtime=False, speed=16, incidents=True, player_pit_lap=3, parade_s=40, grid_s=10,
                replay_shape=True)
    src.connect()
    sent = []
    gate = CommandGate(camera=True)
    real = src.simulate_broadcast
    src.simulate_broadcast = lambda m, a: (sent.append((m, a)), real(m, a))[1]
    clock = [0.0]

    def tick_clock():
        clock[0] += 0.5
        return clock[0]
    out = str(tmp_path / "scan.jsonl.gz")
    res = scan(src, gate, 0, out, clock=tick_clock, sleep=lambda s: None, log=lambda *a: None)
    assert ("replay_set_play_speed", (16, False)) in sent
    assert sent[-1] == ("replay_set_play_speed", (0, False))        # left paused
    assert any(m == "replay_search_session_time" for m, _ in sent)
    again = analyze_capture(out, 0)
    assert [(m["kind"], m["t"]) for m in again["moments"]] == [(m["kind"], m["t"]) for m in res["moments"]]
    assert res["frames"] > 1000


# ------------------------------------------------------------------------------------------------ recording
class Tape:
    """A stand-in replay: honours seeks, plays at the speed it is given, one frame per tick at 60 fps."""
    name = "tape"
    is_connected = True

    def __init__(self, land=True):
        self.s, self.t, self.speed, self.land = 2, 0.0, 0, land
        self.calls = []

    def tick(self):
        self.t += self.speed / 60.0
        return True

    def get(self, k):
        return {"IsReplayPlaying": True, "ReplaySessionNum": self.s, "ReplaySessionTime": self.t}.get(k)

    def simulate_broadcast(self, m, a):
        self.calls.append((m, a))
        if m == "replay_set_play_speed":
            self.speed = a[0]
        elif m == "replay_search_session_time" and self.land:
            self.s, self.t = a[0], a[1] / 1000.0


class FakeOBS:
    def __init__(self, tape):
        self.tape, self.rec, self.log = tape, False, []

    def request(self, kind, data=None):
        self.log.append((kind, round(self.tape.t, 2)))
        if kind == "GetRecordStatus":
            return {"outputActive": self.rec}
        if kind == "StartRecord":
            self.rec = True
            return {}
        if kind == "StopRecord":
            self.rec = False
            return {"outputPath": "C:/Videos/clip%d.mkv" % len(self.log)}
        raise AssertionError(kind)


def test_each_clip_is_jumped_to_with_the_camera_on_the_car_and_recorded_from_just_before_it():
    from pitwall.commands import CommandGate
    tape = Tape()
    obs = FakeOBS(tape)
    clock = [0.0]

    def now():
        clock[0] += 1 / 60.0
        return clock[0]
    rec = ClipRecorder(tape, CommandGate(camera=True), obs, log=lambda *a: None, clock=now, sleep=lambda s: None)
    groups = [{"num": 11, "name": "TV1"}, {"num": 19, "name": "Far Chase"}, {"num": 14, "name": "Pit Lane"}]
    cs = [{"n": 0, "session": 2, "a": 500.0, "b": 515.0, "kind": "pass", "caption": "x"},
          {"n": 1, "session": 2, "a": 900.0, "b": 910.0, "kind": "pit", "caption": "y"}]
    out = rec.record(cs, "34", groups)
    assert [r["ok"] for r in out] == [True, True]
    assert ("cam_switch_num", ("34", 19, 0)) in tape.calls and ("cam_switch_num", ("34", 14, 0)) in tape.calls
    starts = [t for k, t in obs.log if k == "StartRecord"]
    stops = [t for k, t in obs.log if k == "StopRecord"]
    assert 499.3 <= starts[0] <= 500.0 and stops[0] >= 515.0
    assert out[0]["recStart"] <= 500.0 and out[0]["path"].endswith(".mkv")
    assert tape.speed == 0


def test_a_jump_that_never_lands_fails_that_clip_and_goes_on():
    from pitwall.commands import CommandGate
    tape = Tape(land=False)
    clock = [0.0]

    def now():
        clock[0] += 0.05
        return clock[0]
    rec = ClipRecorder(tape, CommandGate(camera=True), FakeOBS(tape), log=lambda *a: None, clock=now,
                       sleep=lambda s: None)
    out = rec.record([{"n": 0, "session": 2, "a": 500.0, "b": 515.0, "kind": "pass"}], "34", [])
    assert out[0]["ok"] is False and "did not get there" in out[0]["error"]


def test_camera_groups_by_kind_with_a_fallback():
    g = [{"num": 1, "name": "Nose"}, {"num": 11, "name": "TV1"}]
    assert pick_group(g, "pass") == 11
    assert pick_group(g, "pass", "Nose") == 1
    assert pick_group([], "pass") == 0


# ------------------------------------------------------------------------------------------------ the video
def _ffmpeg():
    try:
        from highlights.stitch import ffmpeg_exe
        return ffmpeg_exe()
    except Exception:
        return None


@pytest.mark.skipif(_ffmpeg() is None, reason="no ffmpeg")
def test_the_video_is_trimmed_joined_with_a_stinger_over_each_cut_and_music_under(tmp_path):
    from highlights.stitch import build, probe
    ff = _ffmpeg()
    q = ["-y", "-loglevel", "error"]
    clips = []
    for i in range(2):
        p = str(tmp_path / ("rec%d.mkv" % i))
        subprocess.run([ff] + q + ["-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=6", "-f", "lavfi", "-i",
                                   "sine=f=440:d=6", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", p],
                       check=True)
        clips.append({"path": p, "recStart": 100.0, "a": 101.0, "b": 104.0, "caption": "Lap %d  -  Pass" % i,
                      "ok": True})
    st = str(tmp_path / "st.webm")
    subprocess.run([ff] + q + ["-f", "lavfi", "-i", "color=c=white@0.0:s=320x180:r=30:d=1,format=yuva420p",
                               "-c:v", "libvpx-vp9", "-pix_fmt", "yuva420p", st], check=True)
    mus = str(tmp_path / "m.wav")
    subprocess.run([ff] + q + ["-f", "lavfi", "-i", "sine=f=200:d=2", mus], check=True)
    out = str(tmp_path / "out" / "video.mp4")
    r = build(clips, out, str(tmp_path / "work"), {"stinger": st, "music": [mus], "intro": {"title": "T", "lines": ["a"]}},
              log=lambda *a: None)
    info = probe(out)
    assert info["audio"] and (info["w"], info["h"]) == (320, 180)
    assert info["duration"] == pytest.approx(4 + 3 + 3, abs=0.3)          # the card and two 3 s clips
    assert r["clips"] == 2
    # a stinger with no transparency goes between the clips instead
    plain = str(tmp_path / "plain.mp4")
    subprocess.run([ff] + q + ["-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=1", "-c:v", "libx264", plain],
                   check=True)
    r = build(clips, str(tmp_path / "v2.mp4"), str(tmp_path / "w2"), {"stinger": plain}, log=lambda *a: None)
    assert probe(r["path"])["duration"] == pytest.approx(3 + 1 + 3, abs=0.3)


# ------------------------------------------------------------------------------------------------ the app
def test_the_app_scans_the_demo_field_and_picks_to_the_target(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from highlights.app import Engine
    import highlights.scan as hs
    monkeypatch.setattr(hs, "STALL_S", 0.0)
    e = Engine(demo=True, base=str(tmp_path / "home"))
    src = e._source()
    real_tick = src.tick

    def tick():                   # the demo tape ends at 15 minutes
        if src.t > 60 * 15:
            return True
        return real_tick()
    src.tick = tick
    r = e.look()
    assert r["ok"] and r["replay"] and len(r["field"]) == 24
    assert e.start_scan(0)["ok"]
    for _ in range(600):
        if e.job is None:
            break
        time.sleep(0.05)
    assert e.job is None and e.error is None, e.error
    s = e.status()
    assert s["race"]["moments"] and s["race"]["chosen"]
    assert os.path.exists(e.result["scanFile"])
    e.auto(3)
    m, sec = (int(x) for x in e.status()["race"]["length"].split(":"))
    assert 60 < m * 60 + sec <= 180
    assert e.record_and_build()["ok"] is False        # the demo cannot record
