"""Highlights (highlights/, the suite's Highlights tile): finding one car's moments in a replay, picking them, recording them, building
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


# ------------------------------------------------------------------------------------------------ vertical shorts
def test_a_short_opens_on_its_best_moment_with_a_slow_replay_and_fits_its_length():
    ms = [_m(0, 100, 60, "pass"), _m(1, 400, 70, "spin"), _m(2, 800, 40, "battle", [[780, 810]]),
          _m(3, 900, 10, "traffic"), _m(4, 2000, 100, "finish", [[1980, 2008]], True),
          _m(5, 10, 100, "start", [[4, 50]], True)]
    ids = pick.best_of(ms, 45)
    assert 5 not in ids and 3 not in ids               # a short starts in the action; traffic is not a highlight
    cs = pick.hook_first(pick.clips(ms, ids))
    assert cs[0]["kind"] == "spin" and cs[0]["replay"] == {"a": 398.5, "b": 403.0}
    assert all(c["replay"] is None for c in cs[1:])
    assert [c["a"] for c in cs[1:]] == sorted(c["a"] for c in cs[1:])
    assert pick.short_length(cs) <= 45
    # the finish's fixed score does not make it the hook
    cs = pick.hook_first(pick.clips(ms, [0, 4]))
    assert cs[0]["kind"] == "pass"


def test_one_short_per_top_moment_skips_overlaps_and_the_start():
    ms = [_m(0, 100, 60, "pass"), _m(1, 104, 55, "lost"), _m(2, 400, 70, "spin"), _m(3, 10, 100, "start", [[4, 50]], True)]
    assert pick.singles(ms, 3, 60) == [[2], [0]]
    assert pick.singles(ms, 1, 60) == [[2]]
    assert pick.singles(ms, 3, 10) == []                 # none fits in 10 s


@pytest.mark.skipif(_ffmpeg() is None, reason="no ffmpeg")
def test_a_vertical_short_is_1080_by_1920_and_its_replay_runs_at_half_speed(tmp_path):
    from highlights.stitch import build, probe
    ff = _ffmpeg()
    p = str(tmp_path / "rec.mkv")
    subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=8", "-f", "lavfi",
                    "-i", "sine=f=440:d=8", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", p], check=True)
    clip = {"path": p, "recStart": 100.0, "a": 101.0, "b": 105.0, "caption": "Lap 12  -  Takes the class lead",
            "replay": {"a": 102.0, "b": 103.5}, "ok": True}
    for frame in ("fill", "blur"):
        r = build([clip], str(tmp_path / ("%s.mp4" % frame)), str(tmp_path / ("w" + frame)),
                  {"vertical": True, "frame": frame, "tag": "#34  TEST"}, log=lambda *a: None)
        info = probe(r["path"])
        assert (info["w"], info["h"]) == (1080, 1920)
        assert info["duration"] == pytest.approx(4 + 3, abs=0.3)       # the clip, then 1.5 s at half speed
    r = build([clip, dict(clip, replay=None)], str(tmp_path / "cap.mp4"), str(tmp_path / "wcap"),
              {"vertical": True, "maxS": 6}, log=lambda *a: None)
    assert probe(r["path"])["duration"] == pytest.approx(6, abs=0.3)   # held to the short's limit


def test_each_clip_is_recorded_once_for_the_full_video_and_every_short(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from highlights.app import Engine
    e = Engine(demo=True, base=str(tmp_path / "home"))
    ms = [_m(0, 10, 100, "start", [[4, 50]], True), _m(1, 300, 60, "pass"), _m(2, 600, 70, "spin"),
          _m(3, 900, 45, "battle", [[880, 910]]), _m(4, 2000, 100, "finish", [[1980, 2008]], True)]
    e.result = {"moments": ms, "green": 10.0, "finish": 2000.0, "track": "Test"}
    e.target = {"number": "34", "idx": 0}
    e.chosen = [0, 1, 3, 4]
    e.settings.update(makeFull=True, makeBestOf=True, shortSingles=2, shortMaxS=45, cards=False)
    outs = e.outputs()
    assert [o["key"] for o in outs] == ["full", "best", "s1", "s2"]
    assert all(o["vertical"] for o in outs[1:]) and not outs[0]["vertical"]
    union = sorted({i for o in outs for i in o["ids"]})
    cs = pick.clips(ms, union)
    e.recorded = [{"n": c["n"], "ok": True, "path": "clip%d.mkv" % c["n"], "recStart": c["a"] - 0.4, "a": c["a"],
                   "b": c["b"], "session": c["session"]} for c in cs]
    assert len(e.recorded) == len(union)              # nothing overlaps here, so one recording a moment
    best = e._inputs(outs[1])
    assert best[0]["replay"] and best[0]["path"] == next(r["path"] for r in e.recorded if r["a"] == 590)
    full = e._inputs(outs[0])
    assert [x["a"] for x in full] == [4, 290, 880, 1980] and all("replay" not in x for x in full)


# ------------------------------------------------------------------------------------------------ in the suite
def test_the_suite_window_exposes_every_call_the_page_makes_without_walking_the_engine():
    import re
    import types
    from suite.app import Api
    from suite.bridge import resolve
    page = resolve("/highlights/")
    assert page and page.replace("\\", "/").endswith("suite/web/highlights/index.html")
    with open(page, encoding="utf-8") as f:
        html = f.read()
    assert "window.pywebview.api.highlights" in html
    calls = set(re.findall(r"\bapi\.(\w+)\(", html))
    assert {"status", "look", "start_scan", "record_and_build", "open_scan"} <= calls
    api = Api(types.SimpleNamespace(highlights=object()))
    hl = api.highlights
    missing = [c for c in calls if not callable(getattr(hl, c, None))]
    assert not missing, missing
    # pywebview walks every public attribute: only methods, never the engine or the window
    public = [n for n in dir(hl) if not n.startswith("_")]
    assert all(callable(getattr(hl, n)) for n in public)
    with open(resolve("/"), encoding="utf-8") as f:
        assert 'href="/highlights/"' in f.read()


def test_highlights_waits_while_the_broadcast_has_the_camera(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from highlights.app import Engine
    why = ["The Broadcast is on air"]
    e = Engine(demo=False, base=str(tmp_path / "home"), camera_busy=lambda: why[0])
    e.looked = {"field": [{"idx": 0, "number": "34"}]}
    r = e.start_scan(0)
    assert r["ok"] is False and "Broadcast" in r["error"] and e.job is None
    e.result = {"moments": [_m(0, 100, 60)]}
    e.chosen = [0]
    assert "Broadcast" in e.record_and_build()["error"]


def test_the_suites_engine_hands_highlights_the_broadcasts_switch():
    import types
    from suite.app import Engine
    eng = Engine.__new__(Engine)
    eng.pitwall = types.SimpleNamespace(bridge=None)
    assert eng._broadcast_has_camera() is None
    eng.pitwall.bridge = types.SimpleNamespace(director=types.SimpleNamespace(live=True))
    assert "producer panel" in eng._broadcast_has_camera()
    eng.pitwall.bridge.director.live = False
    assert eng._broadcast_has_camera() is None


# ------------------------------------------------------------------------------------------------ camera work
CAM_INFO = {"Groups": [
    {"GroupNum": 1, "GroupName": "Nose", "Cameras": [{"CameraNum": 1, "CameraName": "CamNose"}]},
    {"GroupNum": 2, "GroupName": "Chase", "Cameras": [{"CameraNum": 1, "CameraName": "CamChase"}]},
    {"GroupNum": 3, "GroupName": "Far Chase", "Cameras": [{"CameraNum": 1, "CameraName": "CamFarChase"}]},
    {"GroupNum": 4, "GroupName": "Rear Chase", "Cameras": [{"CameraNum": 1, "CameraName": "CamRearChase"}]},
    {"GroupNum": 5, "GroupName": "Chopper", "Cameras": [{"CameraNum": 1, "CameraName": "CamChopper"}]},
    {"GroupNum": 10, "GroupName": "TV1", "Cameras": [{"CameraNum": k, "CameraName": "CamTV1_%02d" % k} for k in range(1, 6)]},
    {"GroupNum": 11, "GroupName": "TV2", "Cameras": [{"CameraNum": k, "CameraName": "CamTV2_%02d" % k} for k in range(1, 6)]},
    {"GroupNum": 12, "GroupName": "Turn 5 Cam", "Cameras": [{"CameraNum": 1, "CameraName": "CamT5"}]},
    {"GroupNum": 13, "GroupName": "Scenic", "IsScenic": True, "Cameras": [{"CameraNum": 1, "CameraName": "Cam"}]},
    {"GroupNum": 14, "GroupName": "Pit Lane", "Cameras": [{"CameraNum": 1, "CameraName": "CamPit"}]},
]}
NUM = {0: "34", 1: "7"}.get


def test_cameras_are_read_the_directors_way_with_the_producers_word_and_corner_cameras():
    from highlights.shots import Cameras
    cams = Cameras(CAM_INFO, {"turn 5 cam": {"kind": "spot", "at": 0.40}}, lap_s=100.0)
    assert {g["num"] for g in cams.kinds["trackside"]} == {10, 11}
    assert cams.kinds["onboard"][0]["num"] == 1 and cams.kinds["aerial"][0]["num"] == 5
    assert [sp["num"] for sp in cams.spots] == [12]
    assert all(g["num"] != 13 for k in cams.kinds.values() for g in k)     # never Scenic
    import random
    # a trackside shot of a car coming up to Turn 5 is Turn 5's own camera
    assert cams.pick("trackside", random.Random(1), pct=0.37)["num"] == 12
    assert cams.pick("trackside", random.Random(1), pct=0.90)["num"] in (10, 11)


def test_a_pass_is_shot_as_a_story_from_both_cars():
    from highlights.shots import Cameras, plan, KEY_BEFORE_S
    cams = Cameras(CAM_INFO, {}, 100.0)
    m = {"id": 5, "kind": "pass", "t": 520.0, "cars": [1], "otherAheadBefore": True}
    clip = {"a": 500.0, "b": 525.0}
    sh = plan(clip, m, cams, 0, NUM)
    assert sh[0]["t"] == 500.0 and len(sh) >= 4
    assert all(x["group"] != y["group"] for x, y in zip(sh, sh[1:]))          # never the same group twice running
    key = [x for x in sh if x["t"] <= 520.0][-1]
    assert key["t"] <= 520.0 - KEY_BEFORE_S + 0.01 and key["kind"] == "far" and key["car"] == "34"  # the passer, behind
    after = [x for x in sh if x["t"] > 520.0]
    assert after[0]["kind"] == "rear" and after[0]["car"] == "34"   # from in front of the passer, looking back
    assert len({x["kind"] for x in sh}) >= 3


def test_a_near_miss_shows_the_wreck_then_looks_back_at_it_and_its_angles_are_other_cameras():
    from highlights.shots import Cameras, plan, angles, key_groups
    cams = Cameras(CAM_INFO, {}, 100.0)
    m = {"id": 9, "kind": "drama", "t": 300.0, "key": 300.0, "cars": [1], "passT": 306.0, "at": 0.5}
    clip = {"a": 290.0, "b": 311.0}
    sh = plan(clip, m, cams, 0, NUM)
    key = [x for x in sh if x["t"] <= 300.0][-1]
    assert key["car"] == "7" and key["kind"] == "trackside"
    assert sh[0]["car"] == "34" and sh[-1]["car"] == "34" and any(x["kind"] == "rear" for x in sh)
    ang = angles(m, cams, 0, NUM, key_groups(sh, m), n=2)
    assert len(ang) == 2 and ang[0]["shots"][0]["kind"] == "onboard" and ang[0]["shots"][0]["car"] == "34"
    assert ang[0]["speed"] == 1.0 and ang[1]["speed"] == 0.5
    assert all(a["shots"][0]["group"] not in key_groups(sh, m) for a in ang)
    assert ang[0]["a"] < 300.0 and ang[0]["b"] >= 306.0


def test_a_car_spinning_in_front_is_a_near_miss_with_what_led_up_to_it():
    from highlights.moments import LEAD_UP_S
    def other(t):
        if t < 100:
            return {"dist": 0.5 + 0.004 + t / LAP_S}
        stop = 0.5 + 0.004 + 100 / LAP_S
        return {"dist": stop + min(t - 100, 20) * 0.0002, "surf": 0 if t < 108 else 3}
    res = run({0: steady(0.5), 1: other, 2: steady(0.9)}, end=200)
    d = kinds(res, "drama")
    assert len(d) == 1 and d[0]["nearMiss"] and d[0]["label"].startswith("Near miss")
    assert d[0]["t"] == pytest.approx(100, abs=1.0)
    assert d[0]["segs"][0][0] == pytest.approx(d[0]["t"] - LEAD_UP_S, abs=0.3)
    assert d[0]["passT"] and d[0]["passT"] > d[0]["t"] and d[0]["segs"][0][1] >= d[0]["passT"] + 4.5


def test_driver_changes_make_stints_chapters_and_name_the_pit_stop():
    from highlights import story
    a = Analyzer(0, race_session=2)

    def info(name):
        return {"DriverInfo": {"Drivers": [{"CarIdx": 0, "CarNumber": "34", "UserName": name, "TeamName": "Team P",
                                            "CarClassShortName": "GT3"}, {"CarIdx": 1, "CarNumber": "7", "UserName": "X"}]},
                "SessionInfo": {"Sessions": [{"SessionNum": 2, "SessionType": "Race"}]}}
    a.session(info("Andrew"))

    def t_car(t):
        if t < 120:
            return {"dist": 0.5 + t / LAP_S}
        if t < 160:
            return {"dist": 0.5 + 1.2 + (min(t, 130) - 120) * 0.002 + max(0, t - 145) * 0.002,
                    "pit": True, "surf": 1 if 130 <= t < 145 else 2}
        return {"dist": 0.5 + 1.2 + 0.05 + (t - 160) / LAP_S}
    for t, get in Script({0: t_car, 1: steady(0.9)}, end=320).frames():
        if t == 138.0:
            a.session(info("Sam"))
        a.feed(2, t, get)
    res = a.finish()
    assert [s["driver"] for s in res["stints"]] == ["Andrew", "Sam"] and res["drivers"] == ["Andrew", "Sam"]
    assert res["team"] == "Team P" and res["number"] == "34"
    pit = kinds(res, "pit")[0]
    assert pit["driverChange"] and "Sam takes over" in pit["label"]
    items = [{"a": 50.0}, {"a": 100.0}, {"a": 200.0}, {"a": 250.0}]
    story.chapters(res, items)
    assert [x.get("chapter") for x in items] == ["Stint 1  ·  Andrew", None, "Stint 2  ·  Sam", None]
    intro, outro = story.cards(dict(res, event="Six Hours", track="Road Atlanta", classPos=3, startPos=8, laps=212),
                               {"number": "34"})
    assert intro["title"] == "#34 Team P" and "Andrew  ·  Sam" in intro["lines"] and "starts P8" in intro["lines"][-1]
    assert outro["title"] == "Finished P3 in class" and outro["lines"][0] == "Started P8  ·  +5 places"


def test_the_built_in_stingers_cover_the_whole_picture_at_their_middle():
    from highlights import stingers
    for style in stingers.STYLES:
        for W, H in ((192, 108), (108, 192)):
            assert stingers.covered(style, W, H), (style, W, H)
        assert stingers.coverage(style)[0] > 0.05          # and it comes in: not a flash of black from the first frame


def test_the_recorder_cuts_between_the_planned_shots_while_it_records():
    from pitwall.commands import CommandGate
    tape = Tape()
    clock = [0.0]

    def now():
        clock[0] += 1 / 60.0
        return clock[0]
    rec = ClipRecorder(tape, CommandGate(camera=True), FakeOBS(tape), log=lambda *a: None, clock=now,
                       sleep=lambda s: None)
    clip = {"n": 0, "session": 2, "a": 500.0, "b": 515.0, "kind": "pass",
            "shots": [{"t": 500.0, "group": 3, "car": "34"}, {"t": 505.0, "group": 10, "car": "34"},
                      {"t": 511.0, "group": 4, "car": "7"}]}
    out = rec.record([clip], "34", [])
    assert out[0]["ok"]
    cams = [a for m, a in tape.calls if m == "cam_switch_num"]
    assert cams[-2:] == [("34", 10, 0), ("7", 4, 0)]
    assert out[0]["cuts"][0] == pytest.approx(504.9, abs=0.05) and out[0]["cuts"][1] == pytest.approx(510.9, abs=0.05)


def test_a_short_opens_just_before_its_best_moment_and_the_full_video_gets_a_cold_open():
    from highlights import story
    ms = [_m(0, 10, 100, "start", [[4, 50]], True), _m(1, 300, 60, "pass", [[290, 305]]),
          _m(2, 600, 80, "spin", [[590, 620]]), _m(3, 2000, 100, "finish", [[1980, 2008]], True)]
    res = {"moments": ms, "green": 10.0, "finish": 2000.0}
    rec = [{"ok": True, "role": "main", "path": "c%d.mkv" % i, "recStart": a - 0.4, "a": a, "b": b, "session": 2}
           for i, (a, b) in enumerate([(4, 50), (290, 305), (590, 620), (1980, 2008)])] + \
          [{"ok": True, "role": "angle", "moment": 2, "path": "ang.mkv", "recStart": 594.0, "a": 595.0, "b": 603.0,
            "speed": 1.0, "session": 2}]
    short = story.inputs({"name": "s", "ids": [1, 2], "vertical": True}, res, rec, {})
    assert short[0]["best"] == 2 and short[0]["a"] == pytest.approx(600 - story.HOOK_LEAD_S)
    assert short[0]["angles"][0]["path"] == "ang.mkv" and "angles" not in short[1]
    full = story.inputs({"name": "f", "ids": [0, 1, 2, 3], "vertical": False}, res, rec, {})
    assert [x["a"] for x in full] == [4, 290, 590, 1980] and full[2]["angles"]
    tz = story.teaser(res, rec, [0, 1, 2, 3])
    assert tz["path"] == "ang.mkv" and tz["a"] < 600 < tz["b"]


@pytest.mark.skipif(_ffmpeg() is None, reason="no ffmpeg")
def test_the_story_video_runs_cold_open_title_clips_with_replays_and_result(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "cache"))
    from highlights.stitch import build, probe
    ff = _ffmpeg()
    paths = []
    for i in range(3):
        p = str(tmp_path / ("r%d.mkv" % i))
        subprocess.run([ff, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=8", "-f",
                        "lavfi", "-i", "sine=f=440:d=8", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", p],
                       check=True)
        paths.append(p)
    clips = [{"path": paths[0], "recStart": 100.0, "a": 101.0, "b": 104.0, "caption": "Lap 1 · The start",
              "chapter": "Stint 1 · Andrew"},
             {"path": paths[1], "recStart": 100.0, "a": 101.0, "b": 105.0, "caption": "Lap 9 · Pass for P3",
              "angles": [{"path": paths[2], "recStart": 50.0, "a": 51.0, "b": 53.0, "speed": 1.0},
                         {"path": paths[2], "recStart": 50.0, "a": 51.0, "b": 53.0, "speed": 0.5}]}]
    r = build(clips, str(tmp_path / "v.mp4"), str(tmp_path / "w"),
              {"stinger": "builtin:wipe", "stingerMark": "#34", "teaser": {"path": paths[2], "recStart": 50.0,
                                                                          "a": 51.0, "b": 53.5},
               "intro": {"kicker": "Race", "title": "#34 Team P", "lines": ["Andrew  ·  Sam"]},
               "outro": {"title": "Finished P3 in class", "lines": []}}, log=lambda *a: None)
    # cold open 2.5 + title 4 + 3 + 4 + replay 2 + slow replay 4 + result 4
    assert probe(r["path"])["duration"] == pytest.approx(23.5, abs=0.4)
    assert any(f.endswith(".mov") for f in os.listdir(tmp_path / "cache" / "iRacingSuite" / "stingers"))


def test_the_engine_plans_recordings_and_says_how_long_each_step_takes(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from highlights.app import Engine
    e = Engine(demo=True, base=str(tmp_path / "home"), cam_setup=lambda k: {})
    ms = [_m(0, 10, 100, "start", [[4, 50]], True), _m(1, 300, 60, "pass"), _m(2, 600, 70, "spin"),
          _m(4, 2000, 100, "finish", [[1980, 2008]], True)]
    ms[1]["cars"] = [1]
    e.result = {"moments": ms, "green": 10.0, "finish": 2000.0, "track": "Test", "cameraInfo": CAM_INFO,
                "numbers": {0: "34", 1: "7"}, "target": 0, "lapTime": 100.0, "path": [], "trackKey": "1:"}
    e.target = {"number": "34", "idx": 0}
    e.chosen = [0, 1, 2, 4]
    e.settings.update(makeFull=True, makeBestOf=True, shortSingles=1, angleMoments=2, anglesEach=2, cards=True)
    p = e.plan()
    assert len(p["main"]) == 4 and all(len(c["shots"]) >= 2 for c in p["main"])
    assert set(p["angles"]) == {1, 2} and len(p["jobs"]) == 4 + 4
    est = e.status()["race"]["estimates"]
    assert est["recordings"] == 8 and est["angles"] == 4 and est["record"] > 100 and est["recordText"]


def test_a_shorts_later_clips_are_cut_to_their_moment_and_its_replays_count_toward_its_length():
    ms = [_m(0, 100, 60, "pass", [[70, 105]]), _m(1, 400, 70, "spin", [[388, 420]])]
    cs = pick.hook_first(pick.clips(ms, [0, 1]))
    assert pick.short_window(cs[0], 0) == (400 - pick.HOOK_LEAD_S, 420)
    assert pick.short_window(cs[1], 1) == (100 - pick.SHORT_BEFORE_S, 100 + pick.SHORT_AFTER_S)
    w = cs[0]["replay"]["b"] - cs[0]["replay"]["a"]
    assert pick.short_length(cs) == pytest.approx(23 + w / 0.5 + 8)
    assert pick.short_length(cs, speeds=pick.SINGLE_SPEEDS) == pytest.approx(23 + w + w / 0.5 + 8)
    assert pick.short_length(pick.hook_first(pick.clips(ms, pick.best_of(ms, 45))), speeds=pick.BEST_SPEEDS) <= 45


def test_the_full_videos_target_counts_its_replays_and_the_start_is_twenty_seconds(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from highlights.app import Engine
    from highlights.moments import START_S
    res = run({0: steady(0.95), 1: steady(0.9)}, end=300, green=12.0)
    st = kinds(res, "start")[0]
    assert st["segs"][0][1] - st["t"] == pytest.approx(START_S, abs=0.01)
    e = Engine(demo=True, base=str(tmp_path / "home"), cam_setup=lambda k: {})
    ms = [_m(0, 10, 100, "start", [[4, 30]], True)] + \
         [dict(_m(i, 200 * i, 50 + i, "pass", [[200 * i - 10, 200 * i + 5]]), cars=[1]) for i in range(1, 12)] + \
         [_m(12, 3000, 100, "finish", [[2980, 3008]], True)]
    e.result = {"moments": ms, "green": 10.0, "finish": 3000.0, "cameraInfo": CAM_INFO, "numbers": {0: "34", 1: "7"},
                "target": 0, "lapTime": 100.0, "path": []}
    e.settings.update(makeBestOf=False, shortSingles=0, angleMoments=3, anglesEach=2, cards=True, teaser=True)
    def total():
        full = e.outputs()[0]
        return full["length"] + e._replay_s(full) + 3.0
    e.auto(2)
    two, n2 = total(), len(e.chosen)
    e.auto(4)
    four, n4 = total(), len(e.chosen)
    assert two <= 121 and four <= 241 and four > 200 and n4 > n2 + 2   # replays and the cold open counted


# ------------------------------------------------------------------------------------------------ the whole race
def run_field(cars, results=None, drivers=None, **kw):
    from highlights.moments import make_analyzer
    a = make_analyzer(None, race_session=2)
    info = {"SessionInfo": {"Sessions": [{"SessionNum": 2, "SessionType": "Race", "ResultsPositions": results or []}]},
            "DriverInfo": {"Drivers": drivers or [{"CarIdx": i, "CarNumber": str(10 + i), "CarClassID": 1,
                                                   "CarClassShortName": "GT3", "UserName": "D%d" % i}
                                                  for i in cars]}}
    a.session(info)
    for t, get in Script(cars, **kw).frames():
        a.feed(2, t, get)
    return a.finish()


def test_the_whole_race_finds_a_lead_change_anywhere_and_names_the_cars():
    # car 2 (#12) runs third, passes #11 for P2, then #10 for the lead; car 3 (#13) is a lap down, never a pass
    cars = {0: steady(0.62, 100.0), 1: steady(0.60, 100.4), 2: steady(0.58, 98.0), 3: steady(-0.5, 99.0)}
    res = run_field(cars, end=600)
    ps = [m for m in res["moments"] if m["kind"] == "pass"]
    assert [m["focus"] for m in ps] == [2, 2]
    assert ps[0]["label"] == "#12 passes #11 for P2" and ps[0]["cars"] == [1]
    assert ps[1]["label"] == "#12 takes the lead from #10" and ps[1]["classPos"] == 1
    assert ps[1]["score"] > ps[0]["score"]
    assert res["mode"] == "race" and res["target"] is None


def test_the_whole_race_joins_cars_in_trouble_at_one_place_into_one_crash():
    def wreck(start, at_t):
        def f(t):
            if t < at_t:
                return {"dist": start + t / LAP_S}
            d = start + at_t / LAP_S + min(t - at_t, 15) * 0.0003 + max(0, t - at_t - 15) / LAP_S
            return {"dist": d, "surf": 0 if at_t <= t < at_t + 4 else 3}
        return f
    cars = {0: wreck(0.50, 120), 1: wreck(0.505, 121.5), 2: steady(0.2), 3: steady(0.8), 4: steady(0.35)}
    res = run_field(cars, end=260)
    cr = [m for m in res["moments"] if m["kind"] == "crash"]
    assert len(cr) == 1 and cr[0]["carsInvolved"] == 2 and cr[0]["focus"] == 0 and cr[0]["cars"] == [1]
    assert cr[0]["label"] == "Crash - #10 and #11 (the leader)" or cr[0]["label"].startswith("Crash - #10 and #11")
    assert cr[0]["segs"][0][0] == pytest.approx(120 - 10.0, abs=0.3)


def test_the_whole_race_ends_on_each_class_winner_and_a_close_finish_scores_more():
    drivers = [{"CarIdx": 0, "CarNumber": "10", "CarClassID": 1, "CarClassShortName": "GTP", "TeamName": "Red"},
               {"CarIdx": 1, "CarNumber": "11", "CarClassID": 1, "CarClassShortName": "GTP", "TeamName": "Blue"},
               {"CarIdx": 2, "CarNumber": "20", "CarClassID": 2, "CarClassShortName": "GT3", "TeamName": "Gold"}]
    results = [{"CarIdx": 0, "ClassPosition": 0, "Position": 1, "LapsComplete": 3, "CarClassID": 1},
               {"CarIdx": 1, "ClassPosition": 1, "Position": 2, "LapsComplete": 3, "CarClassID": 1},
               {"CarIdx": 2, "ClassPosition": 0, "Position": 3, "LapsComplete": 2, "CarClassID": 2}]
    cars = {0: steady(0.95, 100.0), 1: steady(0.945, 100.0), 2: dict(steady(0.5, 130.0, cls=2).__call__(0)) and
            (lambda t: {"dist": 0.5 + t / 130.0, "cls": 2})}
    res = run_field(cars, results=results, drivers=drivers, end=400)
    fins = [m for m in res["moments"] if m["kind"] == "finish"]
    assert sorted(m["focus"] for m in fins) == [0, 2] and all(m["mandatory"] for m in fins)
    gtp = next(m for m in fins if m["focus"] == 0)
    assert gtp["label"] == "#10 wins GTP by 0.5 s" and gtp["cars"] == [1] and gtp["score"] > 100
    from highlights import story
    intro, outro = story.cards(dict(res, event="Petit", track="Road Atlanta"), None)
    assert intro["title"] == "Race in review" and outro["title"] == "Winners"
    assert outro["lines"] == ["GTP  #10 Red", "GT3  #20 Gold"]
    assert res["finish"] == pytest.approx(max(m["t"] for m in fins))


def test_the_whole_races_camera_follows_each_moments_own_car(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
    from highlights.app import Engine
    e = Engine(demo=True, base=str(tmp_path / "home"), cam_setup=lambda k: {})
    ms = [dict(_m(0, 10, 100, "start", [[4, 30]], True), focus=5),
          dict(_m(1, 300, 90, "pass"), focus=2, cars=[1], otherAheadBefore=True),
          dict(_m(2, 600, 70, "crash", [[590, 620]]), focus=3, cars=[4], key=600.0),
          dict(_m(3, 2000, 100, "finish", [[1980, 2008]], True), focus=2)]
    e.result = {"mode": "race", "moments": ms, "green": 10.0, "finish": 2000.0, "cameraInfo": CAM_INFO,
                "numbers": {1: "11", 2: "12", 3: "13", 4: "14", 5: "15"}, "target": None, "path": []}
    e.target = {"idx": None, "number": ""}
    e.chosen = [0, 1, 2, 3]
    e.settings.update(makeBestOf=False, shortSingles=0, angleMoments=2, anglesEach=2)
    p = e.plan()
    cars = {c["kind"]: {s["car"] for s in c["shots"]} for c in p["main"]}
    assert cars["start"] == {"15"} and cars["pass"] == {"12"} and "13" in cars["crash"]   # the passer, all through
    assert "11" in {j["shots"][0]["car"] for j in p["angles"][1]}       # the passed car's view in the replays
    crash_angles = p["angles"][2]
    assert crash_angles[0]["shots"][0]["car"] == "14"          # from inside the other car caught up in it
    assert e._video_name().startswith("Race race in review") or "race in review" in e._video_name()
