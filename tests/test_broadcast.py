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


def test_counted_incidents_are_seeded_silently_and_anchored_to_what_was_seen():
    d = Deriver()
    d.incidents([_icar(1, 3), _icar(2, -1)], _edge(100), 2)
    assert not d.feed_events, "a count already on the board is history, not news"
    d.incidents([_icar(1, 3, surface="off_track")], _edge(101), 2)          # leaves the track at 101
    d.incidents([_icar(1, 3)], _edge(103), 2)
    d.incidents([_icar(1, 4)], _edge(105.5), 2)                             # session info catches up
    ev = [e for e in d.feed_events if e["kind"] == "car_incident"]
    assert len(ev) == 1
    e = ev[0]
    assert e["points"] == 1 and e["total"] == 4 and e["anchor"] == "off_track" and e["replayT"] == 101
    assert e["sessionNum"] == 2 and e["causes"] == ["wheels off the racing surface"]
    # nothing seen in the telemetry: fall back to well before the count moved
    d.incidents([_icar(1, 8)], _edge(200), 2)
    e = [e for e in d.feed_events if e["kind"] == "car_incident"][-1]
    assert e["points"] == 4 and e["anchor"] == "reported" and e["replayT"] == 200 - Deriver.INC_FALLBACK_S
    # an unpublished count (-1) never produces an incident, and a drop re-seeds
    d.incidents([_icar(2, -1), _icar(1, 2)], _edge(210), 2)
    d.incidents([_icar(1, 3)], _edge(220), 2)
    assert [e["points"] for e in d.feed_events if e["kind"] == "car_incident"] == [1, 4, 1]


def test_off_tracks_are_reported_once_and_never_from_a_replay():
    d = Deriver()
    seq = [("on_track", 10), ("off_track", 10.2), ("on_track", 10.5), ("off_track", 11.0), ("on_track", 11.3)]
    for surf, t in seq:
        d.incidents([_icar(5, 0, surface=surf)], _edge(t), 1)
    offs = [e for e in d.feed_events if e["kind"] == "off_track"]
    assert len(offs) == 1, "kerb-hopping inside 5 s is one event"
    d2 = Deriver()
    for surf, t in seq:
        d2.incidents([_icar(5, 0, surface=surf)], _edge(t, live=False), 1)
    assert not [e for e in d2.feed_events if e["kind"] == "off_track"], "a replay must not re-report"


def test_slowed_sharply_compares_with_the_field_at_the_same_point_of_the_lap():
    d = Deriver()
    t, pos = 0.0, {i: i * 0.04 for i in range(10)}
    # a slow corner at 40-50% of the lap: everyone at a third of normal speed there
    def speed(p):
        return 0.004 if 0.4 <= p % 1 < 0.5 else 0.012
    stalled = None
    for step in range(6000):
        t += 0.125
        cars = []
        for i in range(10):
            v = speed(pos[i])
            if i == 3 and stalled is not None and t < stalled + 3:
                v = 0.0005                                  # car 3 spins and nearly stops
            pos[i] += v * 0.125
            cars.append(_icar(i, 0, pct=pos[i] % 1, lapc=int(pos[i])))
        if step == 4000:
            stalled = t
        d.incidents(cars, _edge(t), 1)
    slowed = [e for e in d.feed_events if e["kind"] == "slowed"]
    assert len(slowed) == 1, f"the slow corner must not count; got {slowed}"
    assert slowed[0]["idx"] == 3 and abs(slowed[0]["t"] - stalled) < 0.5


def test_fake_incidents_are_found_without_inventing_any():
    """Every observed event matches something that really happened in the fake,
    counted points add up to the truth, and the replay anchor lands on the
    incident. (The fake mirrors iRacing's shape -- session-info lag, concurrent
    incidents counting once, invisible 0x -- but it was written alongside the
    detector, so the real check is a spectated race; see VARIABLES_TO_VERIFY.)"""
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    d = Deriver()
    sess, siu = None, None
    for k in range(60 * 600):
        src.tick()
        if src.session_info_update != siu:
            sess, siu = build_session(src), src.session_info_update
        if k % 4 == 0:
            build_state(src, d)
        if k % 8 == 0:
            build_cars(src, sess, d)
    truth = src.inc_log
    ev = d.feed_events
    observed = [e for e in ev if e["kind"] in ("off_track", "slowed")]
    assert observed
    for e in observed:
        assert any(x["idx"] == e["idx"] and abs(x["t"] - e["t"]) < 3 and x["kind"] != "light_contact"
                   for x in truth), f"observed event with nothing behind it: {e}"
    counted = [e for e in ev if e["kind"] == "car_incident"]
    drivers = src.session_info("DriverInfo")["Drivers"]
    assert sum(e["points"] for e in counted) == sum(dr["TeamIncidentCount"] for dr in drivers)
    for e in counted:
        before = [x for x in truth if x["idx"] == e["idx"] and x["points"] > 0 and x["t"] <= e["t"]]
        assert before and abs(e["replayT"] - before[-1]["t"]) < 1.0 or e["anchor"] == "reported"


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
