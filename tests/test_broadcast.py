"""Broadcast: camera/replay commands, battles, graphics state, spectator shape.

Run: python -m pytest tests -q
"""
import asyncio
import importlib.util
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pitwall import trackmap  # noqa: E402
from pitwall.commands import CommandGate, plan_camera, plan_replay  # noqa: E402
from pitwall.normalize import Deriver, build_cars, build_session, build_state  # noqa: E402
from pitwall.sources import FakeSource  # noqa: E402


def _relay_mod():
    spec = importlib.util.spec_from_file_location(
        "relay_server_bc", os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------- commands

def test_camera_commands_validate_before_anything_is_sent():
    assert plan_camera({"action": "car", "car": "07", "group": 11}) == ("cam_switch_num", ("07", 11, 0))
    assert plan_camera({"action": "position", "position": 3, "group": 12, "camera": 2}) == ("cam_switch_pos", (3, 12, 2))
    # iRacing's own shot choice goes in the car-number slot as irsdk_csMode
    assert plan_camera({"action": "focus", "focus": "exciting"}) == ("cam_switch_num", (-1, 0, 0))
    assert plan_camera({"action": "focus", "focus": "leader"})[1][0] == -2
    for bad in ({"action": "car", "car": "7a"}, {"action": "car", "car": "1234"}, {"action": "car"},
                {"action": "position", "position": 0}, {"action": "focus", "focus": "random"},
                {"action": "car", "car": "1", "group": 999}, {"action": "teleport"}):
        with pytest.raises(ValueError):
            plan_camera(bad)
    assert plan_replay({"action": "speed", "speed": 0}) == ("replay_set_play_speed", (0, False))
    assert plan_replay({"action": "search", "mode": "to_end"}) == ("replay_search", ("to_end",))
    with pytest.raises(ValueError):
        plan_replay({"action": "search", "mode": "erase_tape"})
    with pytest.raises(ValueError):
        plan_replay({"action": "speed", "speed": 99})


def test_camera_and_pit_gates_are_independent():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    src.tick()
    cam = {"cmd": "camera", "action": "car", "car": "34", "group": 12}
    off = CommandGate().execute(src, cam)
    assert not off["ok"] and "--allow-camera" in off["error"]
    pit_only = CommandGate(True).execute(src, cam)
    assert not pit_only["ok"], "pit permission must not open the camera"
    cam_only = CommandGate(False, camera=True)
    res = cam_only.execute(src, {"cmd": "pit", "action": "fuel", "value": 10})
    assert not res["ok"] and "--allow-commands" in res["error"]


def test_fake_camera_cut_reaches_the_state_message_and_says_it_was_simulated():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    src.tick()
    gate = CommandGate(camera=True)
    # car number "34" is CarIdx 8 in the fake (10 + idx * 3)
    res = gate.execute(src, {"cmd": "camera", "action": "car", "car": "34", "group": 12})
    assert res["ok"] and res["simulated"] is True
    src.tick()
    st = build_state(src, Deriver())
    assert st["camera"]["carIdx"] == 8 and st["camera"]["group"] == 12
    assert st["player"]["carIdx"] == 63, "a spectator slot is not a car"
    bad = gate.execute(src, {"cmd": "camera", "action": "car", "car": "999", "group": 12})
    assert not bad["ok"]
    gate.execute(src, {"cmd": "replay", "action": "speed", "speed": 0})
    src.tick()
    assert build_state(src, Deriver())["camera"]["replaySpeed"] == 0
    gate.execute(src, {"cmd": "replay", "action": "search", "mode": "to_end"})
    src.tick()
    assert build_state(src, Deriver())["camera"]["replaySpeed"] == 1


def test_a_recorded_replay_cannot_pretend_to_move_the_camera():
    class NoSim:
        name = "replay"

        def irsdk(self):
            raise RuntimeError("replay source cannot send commands to iRacing")
    res = CommandGate(camera=True).execute(NoSim(), {"cmd": "camera", "action": "car", "car": "1"})
    assert not res["ok"] and "cannot send" in res["error"] and "simulated" not in res


def test_spectator_session_carries_camera_groups_and_no_private_car_data():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    src.tick()
    sess = build_session(src)
    names = [g["name"] for g in sess["cameras"]]
    assert "TV1" in names and "Scenic" in names
    assert next(g for g in sess["cameras"] if g["name"] == "Scenic")["scenic"] is True
    st = build_state(src, Deriver())
    assert st["player"]["fuelL"] is None and st["player"]["dc"]["brakeBias"] is None
    assert st["camera"]["stateRaw"] == 81, "the value observed while spectating at VIR"


# --------------------------------------------------------------------- battles

def _car(idx, pos, cls, cpos, f2, last=90.0, pit=False, interval=None, closing=None, catch=None):
    return {"idx": idx, "pos": pos, "classId": cls, "classPos": cpos, "f2TimeS": f2, "lastLapS": last,
            "onPitRoad": pit, "gapValid": f2 is not None,
            "derived": {"intervalS": interval, "closingSPerLap": closing, "catchLaps": catch}}


def _ahead(cars):
    order = sorted(cars, key=lambda c: c["pos"])
    return {b["idx"]: a["idx"] for a, b in zip(order, order[1:])}


def test_battles_are_within_a_class_not_across_classes():
    # Overall: GT3 #1, GT4 #2 0.4 s behind it (traffic, not a battle), GT3 #3 0.7 s
    # behind the GT3 ahead of it. The GT3 pair is not adjacent overall.
    cars = [_car(1, 1, 10, 1, 0.0), _car(2, 2, 20, 1, 0.4), _car(3, 3, 10, 2, 0.7, closing=0.3, catch=1.3)]
    b = Deriver().battles(cars, _ahead(cars), True)
    assert len(b) == 1
    assert b[0]["cars"] == [1, 3] and b[0]["gapsS"] == [0.7] and b[0]["forClassPos"] == 1
    # the behind car's closing rate is on the GT4, not on car 1, so it is withheld
    assert b[0]["closingSPerLap"] == [None] and b[0]["catchLaps"] == [None]


def test_battle_closing_rate_is_passed_through_when_the_pair_is_adjacent_overall():
    cars = [_car(1, 1, 10, 1, 0.0), _car(3, 2, 10, 2, 0.6, closing=0.25, catch=2.4)]
    b = Deriver().battles(cars, _ahead(cars), True)
    assert b[0]["closingSPerLap"] == [0.25] and b[0]["catchLaps"] == [2.4]


def test_battles_merge_into_trains_and_rank_by_position():
    cars = [_car(i, i, 10, i, f2) for i, f2 in ((1, 0.0), (2, 0.5), (3, 1.3), (4, 9.0), (5, 9.6))]
    b = Deriver().battles(cars, _ahead(cars), True)
    assert [x["cars"] for x in b] == [[1, 2, 3], [4, 5]]
    assert b[0]["gapsS"] == [0.5, 0.8]
    assert b[0]["id"] == "10:1"


def test_battles_hold_until_the_gap_clearly_opens():
    d = Deriver()
    def at(gap):
        cars = [_car(1, 1, 10, 1, 0.0), _car(2, 2, 10, 2, gap)]
        return d.battles(cars, _ahead(cars), True)
    assert at(1.2) == [], "1.2 s is not close enough to start a battle"
    assert at(0.9)
    assert at(1.2), "once on, a battle survives a gap of 1.2 s"
    assert at(1.6) == []
    assert at(1.2) == [], "and after it ends, 1.2 s does not restart it"


def test_battles_ignore_untrusted_gaps_pit_lane_and_non_race_sessions():
    cars = [_car(1, 1, 10, 1, 0.0), _car(2, 2, 10, 2, None), _car(3, 3, 10, 3, 0.4)]
    # car 2 has no trusted F2Time: 1 and 3 are not neighbours, so no battle is invented
    assert Deriver().battles(cars, _ahead(cars), True) == []
    cars = [_car(1, 1, 10, 1, 0.0), _car(2, 2, 10, 2, 0.4, pit=True)]
    assert Deriver().battles(cars, _ahead(cars), True) == []
    cars = [_car(1, 1, 10, 1, 0.0), _car(2, 2, 10, 2, 0.4)]
    assert Deriver().battles(cars, _ahead(cars), False) == []


def test_battles_from_the_fake_race_respect_every_rule():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    d = Deriver()
    sess = None
    seen = 0
    for k in range(60 * 600):
        src.tick()
        if k % 8:
            continue
        if sess is None:
            sess = build_session(src)
        build_state(src, d)
        out = build_cars(src, sess, d)
        by = {c["idx"]: c for c in out["cars"]}
        for b in out["derived"]["battles"]:
            seen += 1
            classes = {by[i]["classId"] for i in b["cars"]}
            assert len(classes) == 1
            cps = [by[i]["classPos"] for i in b["cars"]]
            assert cps == list(range(cps[0], cps[0] + len(cps))), "battle cars are class neighbours"
            assert all(0 <= g <= Deriver.BATTLE_LEAVE_S for g in b["gapsS"])
            assert not any(by[i]["onPitRoad"] for i in b["cars"])
    assert seen > 0, "the fake race should produce at least one battle"


# --------------------------------------------------------------------- events

def test_class_leader_changes_are_reported_without_a_player_car():
    d = Deriver()
    def tick(t, leader):
        cars = [{"idx": 1, "classId": 10, "classPos": 1 if leader == 1 else 2, "onPitRoad": False},
                {"idx": 2, "classId": 10, "classPos": 1 if leader == 2 else 2, "onPitRoad": False}]
        d.field_events(cars, t, 63, {})
    for t in range(0, 10):
        tick(t, 1)
    for t in range(10, 13):
        tick(t, 2)
    assert not [e for e in d.feed_events if e["kind"] == "class_lead"], "a pass must hold 5 s first"
    for t in range(13, 20):
        tick(t, 2)
    ev = [e for e in d.feed_events if e["kind"] == "class_lead"]
    assert len(ev) == 1 and ev[0]["idx"] == 2 and ev[0]["prevIdx"] == 1 and ev[0]["classId"] == 10


# --------------------------------------------------------------------- track map

def test_a_camera_cut_voids_the_map_lap_in_progress():
    """While spectating the kinematics follow the camera car; a lap spliced from
    two cars would be cached as the track's outline for good."""
    coll = trackmap.LapCollector(min_samples=5)
    t = 0.0
    def feed(car, pcts):
        nonlocal t
        out = None
        for p in pcts:
            t += 0.1
            out = coll.push(t, p, 50.0, 0.0, 0.0, car=car) or out
        return out
    feed(4, [0.95, 0.99, 0.01, 0.2, 0.4])          # armed on car 4
    feed(9, [0.6, 0.8, 0.95])                        # cut to car 9 mid-lap
    assert feed(9, [0.02]) is None, "the spliced lap must not be returned"
    assert feed(9, [0.2, 0.4, 0.6, 0.8, 0.9, 0.97, 0.03]) is not None, "a clean lap on one car still counts"


# --------------------------------------------------------------------- relay

def test_relay_gfx_state_roles_and_snapshot():
    import websockets
    from websockets.asyncio.server import serve
    relay_mod = _relay_mod()

    async def recv_type(ws, kind, timeout=3):
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), timeout))
            if m.get("type") == kind:
                return m

    async def run():
        relay = relay_mod.Relay("B", "V", "E", "P")
        async with serve(relay.handler, "127.0.0.1", 0, process_request=relay.process_request) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"
            async with websockets.connect(url + "/view?token=P") as prod, \
                    websockets.connect(url + "/view?token=V") as view, \
                    websockets.connect(url + "/view?token=E") as eng, \
                    websockets.connect(url + "/bridge?token=B") as bridge:
                hello = await recv_type(prod, "relay")
                assert hello["role"] == "producer"
                assert (await recv_type(prod, "gfx"))["state"] == {}
                await prod.send(json.dumps({"type": "gfx", "set": {"tower": {"show": True, "mode": "best"}}, "by": "Andrew"}))
                g = await recv_type(view, "gfx")
                while g["state"] == {}:
                    g = await recv_type(view, "gfx")
                assert g["state"] == {"tower": {"show": True, "mode": "best"}} and g["by"] == "Andrew"
                await prod.send(json.dumps({"type": "gfx", "set": {"tower": {"mode": None}, "card": {"show": False}}}))
                g = await recv_type(prod, "gfx")
                while "card" not in g["state"]:
                    g = await recv_type(prod, "gfx")
                assert g["state"] == {"tower": {"show": True}, "card": {"show": False}}, "patches merge; None deletes"
                # a view token cannot change graphics; an engineer cannot cut cameras;
                # a producer cannot touch pit service
                await view.send(json.dumps({"type": "gfx", "set": {"tower": {"show": False}}}))
                assert not (await recv_type(view, "gfx_result"))["ok"]
                await eng.send(json.dumps({"type": "cmd", "cmd": "camera", "action": "car", "car": "1"}))
                assert "producer" in (await recv_type(eng, "cmd_result"))["error"]
                await prod.send(json.dumps({"type": "cmd", "cmd": "pit", "action": "fuel", "value": 1}))
                assert "engineer" in (await recv_type(prod, "cmd_result"))["error"]
                # the producer's camera command does reach the bridge
                await prod.send(json.dumps({"type": "cmd", "cmd": "camera", "action": "car", "car": "1"}))
                fwd = json.loads(await asyncio.wait_for(bridge.recv(), 3))
                assert fwd["cmd"] == "camera"
            # an OBS source that reloads gets the current graphics state at once
            async with websockets.connect(url + "/view?token=V") as late:
                g = await recv_type(late, "gfx")
                assert g["state"] == {"tower": {"show": True}, "card": {"show": False}}
                assert relay.gfx_rev == 2
        return True

    assert asyncio.run(run())


def test_relay_serves_the_broadcast_pages_without_a_token():
    import urllib.request
    from websockets.asyncio.server import serve
    relay_mod = _relay_mod()
    got = {}

    async def run():
        relay = relay_mod.Relay("B", "V", None, "P")
        async with serve(relay.handler, "127.0.0.1", 0, process_request=relay.process_request) as server:
            port = server.sockets[0].getsockname()[1]
            loop = asyncio.get_running_loop()
            for path in ("/broadcast/overlay.html?token=V", "/broadcast/producer.html", "/broadcast/../relay/server.py"):
                def fetch(p=path):
                    try:
                        with urllib.request.urlopen(f"http://127.0.0.1:{port}{p}", timeout=3) as r:
                            return r.status, r.read()
                    except Exception as e:  # noqa: BLE001
                        return getattr(e, "code", None), b""
                got[path] = await loop.run_in_executor(None, fetch)

    asyncio.run(run())
    st, body = got["/broadcast/overlay.html?token=V"]
    assert st == 200 and b"Broadcast Overlay" in body
    st, body = got["/broadcast/producer.html"]
    assert st == 200 and b"Broadcast Producer" in body
    assert got["/broadcast/../relay/server.py"][0] != 200 or b"Relay(" not in got["/broadcast/../relay/server.py"][1]


def test_suite_keeps_overlay_tokens_across_launches():
    from suite.pitwall_host import load_tokens
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "broadcast.json")
        a = load_tokens(path)
        b = load_tokens(path)
        assert a == b and a["view"] != a["producer"]
        with open(path, "w") as f:
            f.write("not json")
        c = load_tokens(path)
        assert c["view"] and c != a


# --------------------------------------------------------------------- incidents

from pitwall.commands import plan_jump  # noqa: E402
from pitwall.normalize import incident_causes, live_edge, surface_kind  # noqa: E402


def test_incident_point_table_follows_the_sporting_code():
    assert incident_causes(1)["causes"] == ["wheels off the racing surface"]
    assert set(incident_causes(2)["causes"]) == {"loss of control", "contact with an object"}
    assert incident_causes(4)["causes"] == ["heavy contact with another car"]
    # dirt: heavy contact is 2x, and there is no 4x
    assert "heavy contact with another car" in incident_causes(2, "dirt")["causes"]
    assert incident_causes(4, "dirt") == {"causes": [], "single": False, "maybeSeveral": True}
    # a 3x rise is no single incident: it can only be several
    r = incident_causes(3)
    assert r["causes"] == [] and not r["single"] and r["maybeSeveral"]
    assert incident_causes(1)["maybeSeveral"] is False
    assert surface_kind("dirt oval") == "dirt" and surface_kind("road course") == "paved"
    assert surface_kind(None) == "paved"


def _edge(t, live=True):
    return {"tLive": t, "tPlay": t if live else t - 30, "behindLiveS": 0 if live else 30, "atLive": live}


def _icar(idx, inc, surface="on_track", pct=0.5, lapc=3, pit=False):
    return {"idx": idx, "incidents": inc, "driverIncidents": inc, "surface": surface, "pct": pct,
            "lapCompleted": lapc, "lap": lapc + 1, "onPitRoad": pit}


def _settle(d, cars_fn, t0, t1, step=0.125, live=True):
    t = t0
    while t < t1:
        d.incidents(cars_fn(t), _edge(t, live), 1)
        t += step
    return t


def _races(d):
    return [e for e in d.feed_events if e["kind"] == "race_incident"]


def test_an_off_track_becomes_one_predicted_1x_incident_and_never_from_a_replay():
    d = Deriver()
    surf = lambda t: "off_track" if 10.2 <= t < 10.5 or 11.0 <= t < 11.3 else "on_track"
    _settle(d, lambda t: [_icar(5, None, surface=surf(t))], 10, 20)
    inc = _races(d)
    assert len(inc) == 1, "kerb-hopping inside a few seconds is one incident"
    assert inc[0]["what"] == "off_track" and inc[0]["likelyPoints"] == 1 and inc[0]["confidence"] == "high"
    assert abs(inc[0]["replayT"] - 10.2) < 0.2
    d2 = Deriver()
    _settle(d2, lambda t: [_icar(5, None, surface=surf(t))], 10, 20, live=False)
    assert not _races(d2), "watching a replay must not re-report"


def _field_run(d, special, steps=6000, n=10, start_step=4000, gap=0.04):
    """Ten cars lapping a track with a slow corner at 40-50% of the lap.
    special(i, t, t_event) -> speed override or None."""
    t, pos = 0.0, {i: i * gap for i in range(n)}
    def speed(p):
        return 0.004 if 0.4 <= p % 1 < 0.5 else 0.012
    t_event = None
    for step in range(steps):
        t += 0.125
        if step == start_step:
            t_event = t
        cars = []
        for i in range(n):
            v = speed(pos[i])
            if t_event is not None:
                o = special(i, t, t_event)
                if o is not None:
                    v = o
            pos[i] += v * 0.125
            cars.append(_icar(i, None, pct=pos[i] % 1, lapc=int(pos[i])))
        d.incidents(cars, _edge(t), 1)
    return t_event


def test_a_slow_corner_is_not_an_incident_but_a_car_losing_its_speed_is():
    d = Deriver()
    t_ev = _field_run(d, lambda i, t, te: 0.0005 if i == 3 and t < te + 3 else None)
    inc = _races(d)
    assert len(inc) == 1, f"the slow corner must not count; got {inc}"
    assert inc[0]["idx"] == 3 and inc[0]["what"] == "lost_speed" and inc[0]["likelyPoints"] == 2
    assert abs(inc[0]["t"] - t_ev) < 0.5


def test_a_car_rolling_backwards_is_a_spin():
    d = Deriver()
    _field_run(d, lambda i, t, te: (-0.004 if t < te + 0.6 else 0.0005) if i == 3 and t < te + 3 else None)
    inc = _races(d)
    assert len(inc) == 1 and inc[0]["what"] == "spin" and "backwards" in inc[0]["signals"]


def test_two_cars_slowing_together_side_by_side_is_contact():
    d = Deriver()
    # cars 3 and 4 run nose to tail; both lose their speed at the same moment
    t_ev = _field_run(d, lambda i, t, te: 0.0008 if i in (3, 4) and t < te + 2.5 else None,
                      n=10, gap=0.04)
    inc = _races(d)
    assert len(inc) == 2 and all(e["what"] == "lost_speed" and not e["contactWith"] for e in inc), \
        "0.04 laps apart is not alongside: two separate slowdowns, not contact"
    d = Deriver()
    def special(i, t, te):
        return 0.0008 if i in (3, 4) and t < te + 2.5 else None
    t, pos = 0.0, {i: i * 0.04 for i in range(10)}
    pos[4] = pos[3] - 0.002                       # right behind car 3
    t_ev = None
    for step in range(6000):
        t += 0.125
        if step == 4000:
            t_ev = t
        cars = []
        for i in range(10):
            v = 0.004 if 0.4 <= pos[i] % 1 < 0.5 else 0.012
            if t_ev is not None and special(i, t, t_ev) is not None:
                v = special(i, t, t_ev)
            pos[i] += v * 0.125
            cars.append(_icar(i, None, pct=pos[i] % 1, lapc=int(pos[i])))
        d.incidents(cars, _edge(t), 1)
    inc = _races(d)
    assert {e["idx"] for e in inc} == {3, 4}
    assert all(e["what"] == "contact" and e["likelyPoints"] == 4 for e in inc)
    assert inc[0]["contactWith"] in ([3], [4])


def test_a_car_parked_for_a_minute_is_one_incident_not_one_every_few_seconds():
    """Seen live: on the grid the counter climbed past 3000. A stopped car kept
    re-opening a fresh 'stopped' incident every time the last one settled."""
    d = Deriver()
    _field_run(d, lambda i, t, te: 0.0 if i == 3 and t < te + 60 else None, steps=6000)
    inc = [e for e in _races(d) if e["idx"] == 3]
    assert len(inc) == 1, f"one stop, one incident; got {len(inc)}"


def _staged_run(d, phases, n=10):
    """phases: [(seconds, state, flags, speed_factor)] -- the field laps a track
    with a slow corner; a factor of 0 sits still on track, as on the grid."""
    t, pos = 0.0, {i: i * 0.04 for i in range(n)}
    for secs, state, flags, k in phases:
        for _ in range(int(secs / 0.125)):
            t += 0.125
            cars = []
            for i in range(n):
                pos[i] += (0.004 if 0.4 <= pos[i] % 1 < 0.5 else 0.012) * k * 0.125
                cars.append(_icar(i, None, pct=pos[i] % 1, lapc=int(pos[i])))
            d.incidents(cars, _edge(t), 1, state=state, flags=flags)
    return t


def test_nothing_is_an_incident_on_the_grid_behind_the_pace_car_or_under_caution():
    d = Deriver()
    _staged_run(d, [(30, "warmup", [], 1.0),          # rolling out to the grid
                    (240, "warmup", [], 0.0),         # parked on the grid, on track
                    (200, "parade_laps", [], 0.35),   # pace laps
                    (600, "racing", ["green"], 1.0),
                    (120, "racing", ["caution_waving"], 0.3),
                    (60, "racing", ["caution"], 0.0),  # field stopped under red-ish caution
                    (300, "racing", ["green"], 1.0)])
    assert not _races(d), f"{len(_races(d))} predicted with no incident on track"
    # and the detector is still alive afterwards
    d2 = Deriver()
    _staged_run(d2, [(240, "warmup", [], 0.0), (200, "parade_laps", [], 0.35), (600, "racing", [], 1.0)])
    t, pos = 1040.0, None
    for step in range(40):                               # car 3 stops dead in the race
        t += 0.125
        cars = [_icar(i, None, pct=(0.1 * i + t * 0.012) % 1 if i != 3 else 0.3, lapc=int(t * 0.012))
                for i in range(10)]
        d2.incidents(cars, _edge(t), 1, state="racing", flags=[])
    for step in range(40):
        t += 0.125
        cars = [_icar(i, None, pct=(0.1 * i + t * 0.012) % 1, lapc=int(t * 0.012)) for i in range(10)]
        d2.incidents(cars, _edge(t), 1, state="racing", flags=[])
    assert [e["idx"] for e in _races(d2)] == [3]


def test_fake_race_start_predicts_nothing_before_the_green():
    src = FakeSource(realtime=False, spectator=True, grid_s=150, parade_s=330, incident_every_s=20)
    src.connect()
    d = Deriver()
    sess, siu = None, None
    for k in range(60 * 700):
        src.tick()
        if src.session_info_update != siu:
            sess, siu = build_session(src), src.session_info_update
        if k % 4 == 0:
            build_state(src, d)
        if k % 8 == 0:
            build_cars(src, sess, d)
    inc = _races(d)
    assert not [e for e in inc if e["t"] < 330], "predicted incidents before the green"
    assert inc, "and real ones after it are still seen"


def test_contact_is_2x_on_dirt():
    from pitwall.normalize import Deriver as Dv
    assert Dv.LIKELY_POINTS["dirt"]["contact"] == 2 and Dv.LIKELY_POINTS["paved"]["contact"] == 4


def test_official_counts_after_the_finish_are_compared_with_the_prediction():
    d = Deriver()
    cars = [_icar(i, 0) for i in range(6)]
    d.incidents(cars, _edge(10), 1)                                      # all published as 0
    # one car's count rises mid-race (a non-spectator view): a counted incident
    cars[1] = _icar(1, 2)
    d.incidents(cars, _edge(20), 1)
    assert [e["kind"] for e in d.feed_events] == ["car_incident"]
    # then the whole field steps at once: the post-race publication
    d._incst()["predicted"].update({2: 3, 3: 1})
    cars = [_icar(i, n) for i, n in enumerate([0, 2, 4, 1, 2, 7])]
    d.incidents(cars, _edge(900), 1)
    tally = [e for e in d.feed_events if e["kind"] == "official_incidents"]
    assert len(tally) == 1
    rows = {r["idx"]: r for r in tally[0]["cars"]}
    assert rows[2] == {"idx": 2, "official": 4, "predicted": 3}
    assert rows[5]["official"] == 7 and rows[5]["predicted"] == 0
    assert len([e for e in d.feed_events if e["kind"] == "car_incident"]) == 1, "the tally is not 5 incidents"


def test_fake_incidents_are_classified_and_the_official_tally_matches():
    """On the fake: every predicted incident matches one that happened, with the
    right kind, none are missed (0x light contact is correctly invisible), and
    the post-race official counts equal the prediction. The fake was written
    alongside the detector, so this proves the logic, not iRacing; the real check
    is the official-vs-predicted tally after a spectated race."""
    src = FakeSource(realtime=False, spectator=True, incident_every_s=15)
    src.connect()
    d = Deriver()
    sess, siu = None, None
    for k in range(60 * 900):
        src.tick()
        if k == 60 * 860:
            src.incidents_on = False          # let the last ones settle before the finish
        if k == 60 * 880:
            src.publish_incident_counts()
        if src.session_info_update != siu:
            sess, siu = build_session(src), src.session_info_update
        if k % 4 == 0:
            build_state(src, d)
        if k % 8 == 0:
            build_cars(src, sess, d)
    truth = src.inc_log
    expect = {"wheels_off": "off_track", "spin": "spin", "wall": "lost_speed", "heavy_contact": "contact"}
    inc = _races(d)
    assert len(inc) > 10
    for e in inc:
        m = [x for x in truth if x["idx"] == e["idx"] and abs(x["t"] - e["t"]) < 3]
        assert m and expect.get(m[0]["kind"]) == e["what"], f"{e['what']} for {m}"
    for x in truth:
        if x["points"] and x["t"] < src.t - 10:
            assert any(e["idx"] == x["idx"] and abs(e["t"] - x["t"]) < 3 for e in inc), f"missed {x}"
    tally = [e for e in d.feed_events if e["kind"] == "official_incidents"][-1]
    assert sum(r["official"] for r in tally["cars"]) == sum(r["predicted"] for r in tally["cars"])


# --------------------------------------------------------------------- live gaps / race length

from pitwall.normalize import gap_between, race_length  # noqa: E402


def _gcar(cls, lap, pct, est, est_lap=90.0):
    return {"classId": cls, "lap": lap, "pct": pct, "estTimeS": est, "classEstLapS": est_lap}


def test_live_gap_uses_est_time_within_a_class_and_counts_whole_laps():
    a, b = _gcar(1, 5, 0.50, 45.0), _gcar(1, 5, 0.45, 40.6)
    assert gap_between(a, b) == (4.4, 0)
    # straddling the line: a just crossed, b about to
    a, b = _gcar(1, 6, 0.01, 0.9), _gcar(1, 5, 0.98, 88.0)
    assert gap_between(a, b) == (2.9, 0)
    # a full lap and a bit ahead: +1 lap
    s, laps = gap_between(_gcar(1, 7, 0.50, 45.0), _gcar(1, 6, 0.40, 36.0))
    assert laps == 1 and abs(s - 99.0) < 1e-6
    # another class: no shared time curve, the behind car's lap scales the fraction
    s, laps = gap_between(_gcar(1, 5, 0.50, 40.0, 80.0), _gcar(2, 5, 0.40, 40.0, 100.0))
    assert laps == 0 and abs(s - 10.0) < 1e-6
    # nonsense in, nothing out
    assert gap_between(_gcar(1, 5, -1, 0), _gcar(1, 5, 0.4, 36)) == (None, None)


def test_live_intervals_track_iracings_own_gaps_on_the_fake():
    src = FakeSource(realtime=False, spectator=True, incidents=False)
    src.connect()
    d = Deriver()
    sess = build_session(src)
    diffs = []
    for k in range(60 * 600):
        src.tick()
        if k % 8:
            continue
        build_state(src, d)
        for c in build_cars(src, sess, d)["cars"]:
            dd = c["derived"]
            if dd.get("intervalS") is not None and dd.get("liveIntervalS") is not None and dd.get("intervalLaps") == 0:
                diffs.append(abs(dd["liveIntervalS"] - dd["intervalS"]))
    diffs.sort()
    assert diffs and diffs[len(diffs) // 2] < 0.5


def test_a_lap_limited_race_counts_down_the_leaders_laps_not_the_clock():
    sess = {"sessions": [{"SessionNum": 2, "SessionType": "Race", "SessionLaps": 30, "SessionTime": "unlimited"}]}
    leader = {"idx": 1, "lapCompleted": 12, "lap": 13}
    r = race_length(sess, 2, [leader], 604800.0, None, ["green"])
    assert r["endsBy"] == "laps" and r["lapsToGo"] == 18 and r["leaderLap"] == 13 and r["totalLaps"] == 30

    class P:
        def pace(self, i):
            return 100.0
    sess = {"sessions": [{"SessionNum": 2, "SessionType": "Race", "SessionLaps": "unlimited",
                          "SessionTime": "2400.0000 sec"}]}
    r = race_length(sess, 2, [leader], 450.0, P(), [])
    assert r["endsBy"] == "time" and r["timeRemainS"] == 450.0 and r["lapsByTime"] == 5
    # both limits: whichever comes first
    sess = {"sessions": [{"SessionNum": 2, "SessionType": "Race", "SessionLaps": 20,
                          "SessionTime": "2400.0000 sec"}]}
    assert race_length(sess, 2, [leader], 450.0, P(), [])["endsBy"] == "time"      # 8 to go, 5 by time
    assert race_length(sess, 2, [leader], 2000.0, P(), [])["endsBy"] == "laps"     # 8 to go, 20 by time


# --------------------------------------------------------------------- replay jump

def test_replay_jump_cuts_seeks_settles_then_plays():
    steps = plan_jump({"session": 2, "timeS": 812.4, "preroll": 5, "car": "34", "group": 11})
    assert steps == [("cam_switch_num", ("34", 11, 0)), ("replay_search_session_time", (2, 807400)),
                     ("sleep", (0.4,)), ("replay_set_play_speed", (1, False))]
    assert plan_jump({"session": 0, "timeS": 3, "preroll": 5})[0] == ("replay_search_session_time", (0, 0))
    for bad in ({"session": 0}, {"session": 0, "timeS": "x"}, {"session": 0, "timeS": 5, "preroll": 99},
                {"session": 99, "timeS": 5}, {"session": 0, "timeS": 5, "car": "abc"}):
        with pytest.raises(ValueError):
            plan_jump(bad)


def test_replay_jump_leaves_the_live_edge_and_back_to_live_returns():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    for _ in range(60 * 60):
        src.tick()
    gate = CommandGate(camera=True)
    assert live_edge(src.get)["atLive"]
    res = gate.execute(src, {"cmd": "replay", "action": "jump", "session": 1, "timeS": 30, "preroll": 5,
                             "car": "34", "group": 12})
    assert res["ok"] and res["simulated"]
    src.tick()
    edge = live_edge(src.get)
    assert not edge["atLive"] and edge["behindLiveS"] > 20
    st = build_state(src, Deriver())
    assert st["derived"]["liveEdge"]["atLive"] is False and st["camera"]["carIdx"] == 8
    gate.execute(src, {"cmd": "replay", "action": "search", "mode": "to_end"})
    src.tick()
    assert live_edge(src.get)["atLive"]
