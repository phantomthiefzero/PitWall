"""Broadcast, part 2: starting grid, director, livery photos (OBS), assets.

Run: python -m pytest tests -q
"""
import asyncio
import base64
import importlib.util
import json
import os
import random
import re
import sys
import tempfile
import threading
import pytest  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pitwall.commands import CommandGate  # noqa: E402
from pitwall.director import Director, group_kind, GRID_BOARD_MIN_S  # noqa: E402
from pitwall.liveries import LiveryCapture, LiveryStore, livery_key  # noqa: E402
from pitwall.normalize import Deriver, build_cars, build_session, build_state, _qual_rows  # noqa: E402
from pitwall.obs import OBSClient, OBSError, auth_string  # noqa: E402
from pitwall.sources import FakeSource  # noqa: E402

JPEG = "data:image/jpg;base64," + base64.b64encode(b"\xff\xd8\xff fake jpeg").decode()


def _relay_mod():
    spec = importlib.util.spec_from_file_location(
        "relay_server_bc2", os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------- grid

def test_grid_positions_from_zero_are_shown_from_one():
    rows = _qual_rows([{"Position": 1, "ClassPosition": 0, "CarIdx": 7, "FastestTime": 88.1},
                       {"Position": 0, "ClassPosition": 0, "CarIdx": 3, "FastestTime": 87.9},
                       {"Position": 2, "ClassPosition": 1, "CarIdx": 9, "FastestTime": -1}])
    assert [(r["pos"], r["classPos"], r["idx"]) for r in rows] == [(1, 1, 3), (2, 1, 7), (3, 2, 9)]
    assert rows[2]["timeS"] is None, "no time is not a time"
    # already 1-based: left alone
    assert _qual_rows([{"Position": 1, "CarIdx": 1}, {"Position": 2, "CarIdx": 2}])[0]["pos"] == 1


def test_grid_rows_carry_the_walk_s_numbers():
    """The grid walk's gaps come from normalize (each row's derived), against the overall pole and the class's pole
    (its first car, ClassPosition 1), two cars a row. A heat-set grid without times has places and no gaps."""
    cls = {1: 10, 2: 10, 3: 20, 4: 20, 5: 10}
    rows = _qual_rows([{"Position": 0, "ClassPosition": 0, "CarIdx": 1, "FastestTime": 100.0},
                       {"Position": 1, "ClassPosition": 1, "CarIdx": 2, "FastestTime": 100.25},
                       {"Position": 2, "ClassPosition": 0, "CarIdx": 3, "FastestTime": 104.5},
                       {"Position": 3, "ClassPosition": 1, "CarIdx": 4, "FastestTime": 105.0},
                       {"Position": 4, "ClassPosition": 2, "CarIdx": 5, "FastestTime": -1}], cls)
    d = {r["idx"]: r["derived"] for r in rows}
    assert d[1] == {"row": 1, "pole": True, "classPole": True, "poleGapS": None, "classPoleGapS": None}
    assert d[2]["poleGapS"] == 0.25 and d[2]["classPoleGapS"] == 0.25 and d[2]["row"] == 1
    assert d[3]["classPole"] and d[3]["poleGapS"] == 4.5 and d[3]["classPoleGapS"] is None and d[3]["row"] == 2
    assert d[4]["classPoleGapS"] == 0.5
    assert d[5]["poleGapS"] is None and d[5]["row"] == 3
    heat = _qual_rows([{"Position": 0, "CarIdx": 1}, {"Position": 1, "CarIdx": 2}])
    assert all(r["derived"]["poleGapS"] is None for r in heat)


def test_a_race_with_its_own_qualify_positions_uses_them():
    from pitwall.normalize import _grid
    sessions = [{"SessionNum": 2, "SessionType": "Race",
                 "QualifyPositions": [{"Position": 0, "ClassPosition": 0, "CarIdx": 5}]}]
    g = _grid({"Results": [{"Position": 0, "CarIdx": 1}]}, sessions)
    assert g["source"] == "race_qualify_positions" and g["rows"][0]["idx"] == 5
    assert _grid({"Results": [{"Position": 0, "CarIdx": 1}]}, [])["rows"][0]["idx"] == 1
    assert _grid(None, [])["rows"] == []


def test_fake_session_carries_the_grid():
    src = FakeSource(realtime=False)
    src.connect()
    g = build_session(src)["grid"]
    assert g["source"] == "qualify_results" and len(g["rows"]) == 24
    assert [r["pos"] for r in g["rows"]] == list(range(1, 25))


# --------------------------------------------------------------------- director

def _director_rig(style="cinematic"):
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    gate = CommandGate(camera=True)
    cuts = []

    def cut(num, group):
        r = gate.execute(src, {"cmd": "camera", "action": "car", "car": num, "group": group})
        cuts.append((num, group, r["ok"]))
        return r
    clock = [0.0]
    d = Director(cut, rng=random.Random(3), clock=lambda: clock[0])
    d.set_live(True)                                  # the producer turned the broadcast on
    d.configure(on=True, style=style, hold_s=6)
    return src, d, cuts, clock


def test_group_kinds_cover_iracings_groups_and_custom_ones():
    assert group_kind("Rear Chase") == "rear" and group_kind("Chopper") == "aerial" and group_kind("Far Chase") == "far"
    assert group_kind("Cockpit") == "onboard" and group_kind("TV3") == "trackside"
    assert group_kind("Scenic") == "never"
    assert group_kind("Turn 1 Hill Cam") == "trackside", "a camera pack's group is a trackside shot"


def test_director_varies_the_shots_and_never_uses_scenic():
    src, d, cuts, clock = _director_rig()
    deriver = Deriver()
    sess = build_session(src)
    names = {g["num"]: g["name"] for g in sess["cameras"]}
    for k in range(60 * 400):
        src.tick()
        if k % 8:
            continue
        st = build_state(src, deriver)
        cars = build_cars(src, sess, deriver)
        clock[0] = k / 60.0
        d.tick(sess, cars, st, [])
    assert len(cuts) > 30 and all(ok for _, _, ok in cuts)
    used = [names[g] for _, g, _ in cuts]
    assert "Scenic" not in used
    assert all(a != b for a, b in zip(used, used[1:])), "the same camera group twice in a row"
    assert len(set(used)) >= 6, f"too little variety: {set(used)}"


def test_director_leaves_the_camera_alone_during_a_replay():
    src, d, cuts, clock = _director_rig()
    deriver = Deriver()
    sess = build_session(src)
    for _ in range(60 * 30):
        src.tick()
    CommandGate(camera=True).execute(src, {"cmd": "replay", "action": "jump", "session": 1, "timeS": 0, "preroll": 0})
    for k in range(600):
        src.tick()
        if k % 8:
            continue
        st = build_state(src, deriver)
        clock[0] = k
        d.tick(sess, build_cars(src, sess, deriver), st, [])
    assert cuts == []


def test_director_shows_a_fresh_incident_first():
    src, d, cuts, clock = _director_rig()
    deriver = Deriver()
    sess = build_session(src)
    for _ in range(60):
        src.tick()
    st = build_state(src, deriver)
    cars = build_cars(src, sess, deriver)
    d.tick(sess, cars, st, [{"id": 1, "what": "contact", "idx": 11}])
    assert cuts and cuts[0][0] == str(10 + 11 * 3) and "incident" in d.current["reason"]


def test_grid_walk_visits_the_grid_in_order_with_a_chase_camera_then_stops():
    src, d, cuts, clock = _director_rig()
    deriver = Deriver()
    sess = build_session(src)
    src.tick()
    st = build_state(src, deriver)
    cars = build_cars(src, sess, deriver)
    by = {c["idx"]: c for c in cars["cars"]}
    order = [by[r["idx"]] for r in sess["grid"]["rows"]][:5]
    seen = []
    d.configure(on=False)
    d.start_grid_walk(order, hold_s=2, on_car=lambda c: seen.append(c["idx"]))
    chase = next(g["num"] for g in sess["cameras"] if g["name"] == "Far Chase")
    for step in range(20):
        clock[0] = step * 1.0
        d.tick(sess, cars, st, [])
    assert seen == [c["idx"] for c in order]
    rear = next((g["num"] for g in sess["cameras"] if g["name"] == "Rear Chase"), chase)
    used = [g for _, g, _ in cuts]
    # the pole from in front, then the shots take turns (the fake field is one class... or several: each class's
    # first car is a pole too, and gets the front shot)
    assert used[0] == rear and set(used) <= {chase, rear}
    if rear != chase and len({c.get("classId") for c in order}) == 1:
        assert used[:5] == [rear, chase, rear, chase, rear]
    assert d.owner == "you", "the walk was started by hand: the camera goes back to the producer"


# ----------------------------------------------- one owner of the camera at a time

def _frames(src, sess, deriver, seconds, every=8):
    for k in range(int(60 * seconds)):
        src.tick()
        if k % every == 0:
            yield build_state(src, deriver), build_cars(src, sess, deriver)


def test_a_cut_by_hand_takes_the_camera_from_the_director_and_it_stays_taken():
    src, d, cuts, clock = _director_rig()
    deriver, sess = Deriver(), build_session(src)
    t = 0.0
    for st, cars in _frames(src, sess, deriver, 30):
        t += 8 / 60
        clock[0] = t
        d.tick(sess, cars, st, [])
    assert cuts and d.owner == "race"
    assert d.manual() is True and d.owner == "you"
    n = len(cuts)
    for st, cars in _frames(src, sess, deriver, 120):
        t += 8 / 60
        clock[0] = t
        d.tick(sess, cars, st, [{"id": 99, "what": "contact", "idx": 3}])
    assert len(cuts) == n, "nothing may move the camera after the producer took it"
    assert d.manual() is False, "taking it again is a no-op"


def test_following_a_battle_stays_on_those_cars_varies_the_shot_then_hands_back():
    src, d, cuts, clock = _director_rig()           # the director was on
    deriver, sess = Deriver(), build_session(src)
    names = {g["num"]: g["name"] for g in sess["cameras"]}
    src.tick()
    st, cars = build_state(src, deriver), build_cars(src, sess, deriver)
    a, b = cars["cars"][4], cars["cars"][7]
    battle = {"id": "x", "cars": [a["idx"], b["idx"]], "forClassPos": 2, "forPos": 3, "classId": a["classId"],
              "gapsS": [0.4], "closingSPerLap": [], "catchLaps": []}
    cars["derived"]["battles"] = [battle]
    assert d.follow_battle([a["idx"], b["idx"]])
    for step in range(60):                           # a minute on the battle
        clock[0] = step * 1.0
        d.tick(sess, cars, st, [{"id": 5, "what": "contact", "idx": 11, "t": None}])
    on = {num for num, _, _ in cuts}
    assert on <= {str(a["num"]), str(b["num"])}, f"left the battle for {on}"
    assert on == {str(a["num"]), str(b["num"])}, "both cars are shown"
    used = [names[g] for _, g, _ in cuts]
    assert len(set(used)) >= 3 and all(x != y for x, y in zip(used, used[1:])), used
    assert d.owner == "battle"
    # the battle breaks up: after a grace period the director has the camera again
    cars["derived"]["battles"] = []
    for step in range(60, 75):
        clock[0] = step * 1.0
        d.tick(sess, cars, st, [])
    assert d.owner == "race" and d.resume is None
    # from "you", a followed battle hands back to you
    d.configure(on=False)
    d.follow_battle([a["idx"], b["idx"]])
    for step in range(80, 95):
        clock[0] = step * 1.0
        d.tick(sess, cars, st, [])
    assert d.owner == "you"


def test_turning_the_director_on_does_not_replay_old_incidents():
    src, d, cuts, clock = _director_rig()
    deriver, sess = Deriver(), build_session(src)
    for _ in range(60 * 200):
        src.tick()
    st, cars = build_state(src, deriver), build_cars(src, sess, deriver)
    old = [{"id": i, "what": "contact", "idx": i, "t": 20.0 + i} for i in range(5)]
    d.tick(sess, cars, st, old)
    assert not cuts or "incident" not in d.current["reason"]


def test_the_pace_laps_start_a_grid_walk_in_order_that_ends_at_the_green():
    src = FakeSource(realtime=False, spectator=True, grid_s=20, parade_s=200, incidents=False)
    src.connect()
    gate = CommandGate(camera=True)
    cuts = []

    def cut(num, group):
        r = gate.execute(src, {"cmd": "camera", "action": "car", "car": num, "group": group})
        cuts.append((num, group, src.t))
        return r
    t = [0.0]
    d = Director(cut, rng=random.Random(1), clock=lambda: t[0])
    d.set_live(True)                                  # the producer turned the broadcast on
    d.configure(on=True)                              # the director was running
    d.grid_hold_s = 4
    deriver, sess = Deriver(), build_session(src)
    chase = next(g["num"] for g in sess["cameras"] if g["name"] == "Chase")
    owners = []
    for k in range(60 * 260):
        src.tick()
        if k % 8:
            continue
        t[0] = src.t
        d.tick(sess, build_cars(src, sess, deriver), build_state(src, deriver), [])
        owners.append((src.t, d.owner))
    num = {c["idx"]: str(c["num"]) for c in build_cars(src, sess, deriver)["cars"]}
    grid_nums = [num[r["idx"]] for r in sess["grid"]["rows"]]
    walk = [(n, g) for n, g, at in cuts if 20 <= at < 200][:len(grid_nums)]
    assert [n for n, _ in walk] == grid_nums, "every car, down the grid, in order"
    rear = next((g["num"] for g in sess["cameras"] if g["name"] == "Rear Chase"), chase)
    far = next(g["num"] for g in sess["cameras"] if g["name"] == "Far Chase")
    assert {g for _, g in walk} <= {far, rear} and walk[0][1] == rear, "the pole from in front, then turns"
    walking = [at for at, o in owners if o == "grid"]
    assert walking and min(walking) < 21 and max(walking) < 20 + 4 * 25 + 12, "about 4 s a car (poles a little longer), from the pace laps"
    assert any(o == "race" for at, o in owners if max(walking) < at < 200), \
        "walk done before the green: the director carries on with the pace laps"
    assert all(o == "race" for at, o in owners if at > 201), "after the green, still the director"


def test_the_green_flag_ends_a_walk_that_has_not_finished_and_it_runs_once():
    src = FakeSource(realtime=False, spectator=True, grid_s=5, parade_s=30, incidents=False)
    src.connect()
    gate = CommandGate(camera=True)
    t = [0.0]
    d = Director(lambda n, g: gate.execute(src, {"cmd": "camera", "action": "car", "car": n, "group": g}),
                 rng=random.Random(1), clock=lambda: t[0])
    d.set_live(True)                                  # the producer turned the broadcast on
    d.grid_hold_s = 5                                   # 24 cars x 5 s will not fit in 25 s
    deriver, sess = Deriver(), build_session(src)
    owners = []
    for k in range(60 * 60):
        src.tick()
        if k % 8:
            continue
        t[0] = src.t
        d.tick(sess, build_cars(src, sess, deriver), build_state(src, deriver), [])
        owners.append((src.t, d.owner))
    assert any(o == "grid" for _, o in owners)
    assert all(o == "you" for at, o in owners if at > 31), "green: back to the producer, who had it before"
    assert d._grid_walked, "remembered, so it does not walk again in this session"


def test_a_producer_cut_through_the_bridge_takes_the_camera_back():
    from pitwall.bridge import Bridge

    class WS:
        def __init__(self, msgs):
            self.msgs, self.sent = [json.dumps(m) for m in msgs], []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    b = Bridge(src, "ws://127.0.0.1:1", "t", allow_camera=True, log=lambda *a: None, render_cars=False,
               livery_dir=tempfile.mkdtemp())
    b.director.set_live(True)
    b.director.configure(on=True)
    ws = WS([{"type": "cmd", "cmd": "replay", "action": "speed", "speed": 1},
             {"type": "cmd", "cmd": "camera", "action": "car", "car": "13", "group": 1, "camera": 0}])
    asyncio.run(b._consumer(ws))
    assert b.director.owner == "you"
    owners = [m["owner"] for m in ws.sent if m.get("type") == "director"]
    assert owners == ["you"], "a replay command leaves the owner alone; the cut takes it, and says so"
    # following a battle is a director command, not a hand cut
    ws = WS([{"type": "cmd", "cmd": "director", "action": "follow_battle", "cars": [1, 2]}])
    asyncio.run(b._consumer(ws))
    assert b.director.owner == "battle"


def test_a_malformed_producer_command_is_answered_and_the_bridge_carries_on():
    """One bad value from a producer ("holdS": "abc") used to raise out of the
    bridge's consumer and end the bridge process mid-broadcast."""
    from pitwall.bridge import Bridge

    class WS:
        def __init__(self, msgs):
            self.msgs, self.sent = [json.dumps(m) for m in msgs], []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    b = Bridge(src, "ws://127.0.0.1:1", "t", allow_camera=True, log=lambda *a: None, render_cars=False,
               livery_dir=tempfile.mkdtemp())
    b.director.set_live(True)
    ws = WS([{"type": "cmd", "cmd": "director", "action": "set", "holdS": "abc"},
             {"type": "cmd", "cmd": "director", "action": "follow_battle", "cars": ["x", "y"]},
             {"type": "cmd", "cmd": "director", "action": "set", "gridHoldS": "x"},
             [1, 2, 3],
             {"type": "cmd", "cmd": "director", "action": "follow_battle", "cars": [1, 2]}])
    asyncio.run(b._consumer(ws))          # must return, not raise
    results = [m for m in ws.sent if m.get("type") == "cmd_result"]
    assert [r["ok"] for r in results] == [False, False, False, True], results
    assert all("bad command" in r["error"] for r in results[:3])
    assert b.director.owner == "battle", "the good command after the bad ones still ran"


def _serve_relay(relay_mod, relay):
    from websockets.asyncio.server import serve
    return serve(relay.handler, "127.0.0.1", 0, process_request=relay.process_request,
                 max_size=relay_mod.WS_MAX_BYTES)


async def _drain(ws, quiet_s=0.5):
    got = []
    try:
        while True:
            got.append(json.loads(await asyncio.wait_for(ws.recv(), quiet_s)))
    except asyncio.TimeoutError:
        pass
    return got


def _feed(full, seq, laps=(), events=()):
    return {"type": "feed", "full": full, "seq": seq,
            "laps": [{"seq": s, "idx": 1, "lap": s} for s in laps],
            "events": [{"seq": s, "kind": "pit_in", "idx": 2} for s in events]}


def test_a_late_joiner_gets_the_race_feed_so_far():
    """The relay used to keep only the last FULL feed, which the bridge sends on a
    resend -- so a viewer joining mid-race got the feed as it was the last time
    anyone asked, and every page had to ask the bridge, which sent it to all."""
    import websockets
    relay_mod = _relay_mod()

    async def run():
        relay = relay_mod.Relay("B", "V", None, assets_dir=tempfile.mkdtemp())
        async with _serve_relay(relay_mod, relay) as srv:
            u = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
            async with websockets.connect(u + "/bridge?token=B") as br:
                await br.send(json.dumps(_feed(True, 3, laps=[1, 2], events=[3])))
                await br.send(json.dumps(_feed(False, 5, laps=[4], events=[5])))
                await br.send(json.dumps(_feed(False, 6, laps=[6])))
                await asyncio.sleep(0.2)
                async with websockets.connect(u + "/view?token=V") as v:
                    feeds = [m for m in await _drain(v) if m["type"] == "feed"]
        return feeds

    feeds = asyncio.run(run())
    assert len(feeds) == 1 and feeds[0]["full"]
    assert [x["seq"] for x in feeds[0]["laps"]] == [1, 2, 4, 6]
    assert [x["seq"] for x in feeds[0]["events"]] == [3, 5]
    assert feeds[0]["seq"] == 6


def test_a_resend_is_answered_by_the_relay_to_that_viewer_alone():
    """One guest asking 10x a second used to multiply every viewer's traffic 5x."""
    import websockets
    relay_mod = _relay_mod()
    relay_mod.RESEND_MIN_S = 1.5

    async def run():
        relay = relay_mod.Relay("B", "V", None, assets_dir=tempfile.mkdtemp())
        relay.set_guest_token("G")
        async with _serve_relay(relay_mod, relay) as srv:
            u = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
            async with websockets.connect(u + "/bridge?token=B") as br:
                await br.send(json.dumps({"type": "session", "track": {"TrackID": 1}}))
                await br.send(json.dumps(_feed(True, 1, laps=[1])))
                await asyncio.sleep(0.2)
                async with websockets.connect(u + "/view?token=G") as guest, \
                        websockets.connect(u + "/view?token=V") as other:
                    await _drain(guest, 0.1)
                    await _drain(other, 0.1)
                    await guest.send(json.dumps({"type": "resend"}))     # too soon after connecting
                    first = await _drain(guest, 0.3)
                    await asyncio.sleep(1.5)
                    for _ in range(10):                                  # then a burst
                        await guest.send(json.dumps({"type": "resend"}))
                    burst = await _drain(guest)
                    to_other = await _drain(other)
                    to_bridge = await _drain(br)
        return first, burst, to_other, to_bridge

    first, burst, to_other, to_bridge = asyncio.run(run())
    assert not [m for m in first if m["type"] == "session"], "the page was just sent everything"
    assert [m["type"] for m in burst].count("session") == 1, "one answer per RESEND_MIN_S, however often it asks"
    assert [m["type"] for m in burst].count("feed") == 1
    assert not to_other, "nobody else is sent anything"
    assert not [m for m in to_bridge if m.get("type") == "resend"], "the bridge is not involved"


def test_only_the_producer_may_send_large_messages():
    """A guest's 40 MB message used to be read and parsed, freezing the stream for
    everyone. Watching never needs more than a few hundred bytes."""
    import base64
    import websockets
    relay_mod = _relay_mod()

    async def run():
        relay = relay_mod.Relay("B", "V", None, producer_token="P", assets_dir=tempfile.mkdtemp())
        relay.set_guest_token("G")
        async with _serve_relay(relay_mod, relay) as srv:
            u = f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}"
            big = json.dumps({"type": "focus", "idx": 1, "pad": "x" * (relay_mod.VIEWER_MAX_BYTES + 10)})
            codes = {}
            for token in ("G", "V"):
                async with websockets.connect(u + f"/view?token={token}", max_size=None) as v:
                    await _drain(v, 0.2)
                    await v.send(big)
                    try:
                        await _drain(v, 1.0)
                        codes[token] = "open"
                    except websockets.exceptions.ConnectionClosed as e:
                        codes[token] = e.rcvd.code if e.rcvd else None
            async with websockets.connect(u + "/view?token=P", max_size=None) as prod:
                await _drain(prod, 0.2)
                data = base64.b64encode(b"\x89PNG" + b"\0" * 300_000).decode()
                await prod.send(json.dumps({"type": "asset_put", "name": "logo.png", "data": data}))
                res = next(m for m in await _drain(prod, 1.0) if m["type"] == "asset_result")
        return codes, res

    codes, res = asyncio.run(run())
    assert codes == {"G": 1009, "V": 1009}, codes            # 1009: message too big
    assert res["ok"], res                                      # the producer can still upload


def test_relay_refuses_an_oversized_graphics_patch_without_wiping_the_show():
    """It used to replace the whole graphics state with {}: every graphic off air."""
    import websockets
    relay_mod = _relay_mod()

    async def run():
        relay = relay_mod.Relay("B", "V", None, producer_token="P", assets_dir=tempfile.mkdtemp())
        async with _serve_relay(relay_mod, relay) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}/view?token=P", max_size=None) as p:
                await p.send(json.dumps({"type": "gfx", "set": {"tower": {"on": True}}}))
                # patches each under the per-message limit, together over the state limit
                chunk = "x" * (relay_mod.GFX_MAX_BYTES // 3)
                for i in range(4):
                    await p.send(json.dumps({"type": "gfx", "set": {f"note{i}": chunk}}))
                refused = None
                for _ in range(40):
                    m = json.loads(await asyncio.wait_for(p.recv(), 5))
                    if m.get("type") == "gfx_result" and not m["ok"]:
                        refused = m
                        break
            return relay.gfx, refused

    gfx, refused = asyncio.run(run())
    assert refused is not None, "the patch that overflows the state is refused, and the producer told"
    assert gfx.get("tower") == {"on": True}, "graphics already on air stay on air"
    assert len(json.dumps(gfx)) <= relay_mod.GFX_MAX_BYTES


def test_a_producer_who_joins_late_gets_the_camera_packs():
    """The relay kept camera_packs as a snapshot but never sent it to new viewers."""
    import websockets
    relay_mod = _relay_mod()

    async def run():
        relay = relay_mod.Relay("B", "V", None, producer_token="P", assets_dir=tempfile.mkdtemp())
        async with _serve_relay(relay_mod, relay) as srv:
            port = srv.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}/bridge?token=B") as br:
                await br.send(json.dumps({"type": "camera_packs", "dir": "x", "exists": True}))
                await asyncio.sleep(0.2)
                async with websockets.connect(f"ws://127.0.0.1:{port}/view?token=P") as p:
                    types = []
                    try:
                        while True:
                            types.append(json.loads(await asyncio.wait_for(p.recv(), 1))["type"])
                    except asyncio.TimeoutError:
                        pass
            return types

    assert "camera_packs" in asyncio.run(run())


def test_uploaded_assets_cannot_run_script_on_the_relay_origin():
    import urllib.request
    relay_mod = _relay_mod()
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "logo.svg"), "w", encoding="utf-8") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')

    async def run():
        relay = relay_mod.Relay("B", "V", None, producer_token="P", assets_dir=d)
        async with _serve_relay(relay_mod, relay) as srv:
            port = srv.sockets[0].getsockname()[1]

            def get():
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/broadcast/assets/logo.svg", timeout=5) as r:
                    return dict(r.headers)
            return await asyncio.get_running_loop().run_in_executor(None, get)

    h = asyncio.run(run())
    assert "sandbox" in h.get("Content-Security-Policy", "")
    assert h.get("X-Content-Type-Options") == "nosniff"


# --------------------------------------------------------------------- OBS + liveries

def test_obs_must_be_on_this_pc_or_the_local_network():
    """The producer sets the OBS address, and connecting runs OBS's login exchange
    with the stored password -- material to guess it offline, for whoever is at
    the far end. Only this PC and private network addresses are accepted."""
    from pitwall.bridge import Bridge
    from pitwall.obs import obs_url_problem
    for ok in ("ws://127.0.0.1:4455", "ws://localhost:4455", "ws://192.168.1.20:4455", "ws://10.0.0.5:4455",
               "ws://[::1]:4455"):
        assert obs_url_problem(ok) is None, ok
    for bad in ("ws://8.8.8.8:4455", "ws://1.1.1.1:4455", "ws://0.0.0.0:4455", "http://127.0.0.1:4455", ""):
        assert obs_url_problem(bad), bad
    src = FakeSource(realtime=False)
    src.connect()
    d = tempfile.mkdtemp()
    b = Bridge(src, "ws://127.0.0.1:1", "t", allow_camera=True, log=lambda *a: None, render_cars=False, livery_dir=d)
    before = dict(b.livery_store.cfg)
    res = b._broadcast_cmd({"type": "cmd", "cmd": "livery", "action": "setup", "url": "ws://8.8.8.8:4455",
                            "password": "secret"})
    assert not res["ok"] and "local network" in res["error"]
    assert b.livery_store.cfg == before, "nothing is saved when the address is refused"
    # a blank address keeps the one already set
    b._broadcast_cmd({"type": "cmd", "cmd": "livery", "action": "setup", "url": "", "source": "Game"})
    assert b.livery_store.cfg["url"] == before["url"] and b.livery_store.cfg["source"] == "Game"


def test_obs_auth_follows_the_protocol():
    import hashlib
    secret = base64.b64encode(hashlib.sha256(b"pw" + b"salt").digest()).decode()
    expect = base64.b64encode(hashlib.sha256((secret + "chal").encode()).digest()).decode()
    assert auth_string("pw", "salt", "chal") == expect


def _fake_obs(password=None):
    """A tiny obs-websocket v5 server: Hello, Identify, and two requests."""
    from websockets.sync.server import serve

    def handler(ws):
        hello = {"obsWebSocketVersion": "5.5.0", "rpcVersion": 1}
        if password:
            hello["authentication"] = {"challenge": "c1", "salt": "s1"}
        ws.send(json.dumps({"op": 0, "d": hello}))
        ident = json.loads(ws.recv())
        if password and ident["d"].get("authentication") != auth_string(password, "s1", "c1"):
            ws.close(4009, "Authentication failed")
            return
        ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        for raw in ws:
            m = json.loads(raw)["d"]
            ok, data = True, {}
            if m["requestType"] == "GetInputList":
                data = {"inputs": [{"inputName": "iRacing"}, {"inputName": "Webcam"}]}
            elif m["requestType"] == "GetSourceScreenshot":
                ok = m["requestData"]["sourceName"] == "iRacing"
                data = {"imageData": JPEG} if ok else {}
            ws.send(json.dumps({"op": 7, "d": {"requestType": m["requestType"], "requestId": m["requestId"],
                                               "requestStatus": {"result": ok, "code": 100 if ok else 600,
                                                                 "comment": None if ok else "No source"},
                                               "responseData": data}}))
    server = serve(handler, "127.0.0.1", 0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"ws://127.0.0.1:{server.socket.getsockname()[1]}"


def test_obs_client_identifies_lists_sources_and_takes_a_still():
    server, url = _fake_obs(password="secret")
    try:
        c = OBSClient(url, "secret")
        assert c.sources() == ["iRacing", "Webcam"]
        assert c.screenshot("iRacing") == JPEG
        try:
            c.screenshot("Nope")
            raise AssertionError("a missing source must raise")
        except OBSError as e:
            assert "No source" in str(e)
        c.close()
        try:
            OBSClient(url, "wrong").sources()
            raise AssertionError("a wrong password must raise")
        except OBSError:
            pass
        try:
            OBSClient(url, "").sources()
            raise AssertionError("no password must say so")
        except OBSError as e:
            assert "password" in str(e)
    finally:
        server.shutdown()


def test_livery_capture_files_photos_per_driver_and_car():
    server, url = _fake_obs()
    try:
        with tempfile.TemporaryDirectory() as d:
            store = LiveryStore(d)
            store.cfg.update(url=url, source="iRacing")
            out, cut_log = [], []
            cap = LiveryCapture(store, cut=lambda n, g: cut_log.append((n, g)) or {"ok": True},
                                emit=out.append)
            cap.SETTLE_S = 0.0
            car = {"idx": 4, "num": "22", "carId": 110, "userId": 300004, "teamId": 0}
            assert livery_key(car) == "110_300004"
            assert livery_key({**car, "teamId": 77}) == "110_77", "a team shares one paint"
            assert cap.snap(car) and store.has("110_300004")
            assert out[-1]["type"] == "livery" and out[-1]["image"].startswith("data:image/jpeg")
            done = threading.Event()
            cars = [car, {"idx": 5, "num": "23", "carId": 110, "userId": 300005, "teamId": 0}]
            cap.emit = lambda m: (out.append(m), m.get("type") == "livery_status" and not m["sweeping"]
                                  and m["done"] and done.set())
            assert cap.sweep(cars, group=18, skip_existing=True)
            assert done.wait(5)
            assert cut_log == [("23", 18)], "a car that already has a photo is skipped"
            assert store.has("110_300005")
            assert "password" not in cap.status_message()["config"]
    finally:
        server.shutdown()


# --------------------------------------------------------------------- relay: assets, liveries

def test_relay_takes_assets_from_the_producer_only_and_serves_them():
    import urllib.request
    import websockets
    from websockets.asyncio.server import serve
    relay_mod = _relay_mod()

    async def recv_type(ws, kind):
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 3))
            if m.get("type") == kind:
                return m

    with tempfile.TemporaryDirectory() as tmp:
        async def run():
            relay = relay_mod.Relay("B", "V", None, "P", assets_dir=tmp)
            async with serve(relay.handler, "127.0.0.1", 0, process_request=relay.process_request,
                             max_size=relay_mod.WS_MAX_BYTES) as server:
                port = server.sockets[0].getsockname()[1]
                url = f"ws://127.0.0.1:{port}"
                async with websockets.connect(url + "/view?token=P", max_size=None) as prod, \
                        websockets.connect(url + "/view?token=V", max_size=None) as view, \
                        websockets.connect(url + "/bridge?token=B", max_size=None) as bridge:
                    assert (await recv_type(view, "assets"))["files"] == []
                    png = base64.b64encode(b"\x89PNG logo").decode()
                    await view.send(json.dumps({"type": "asset_put", "name": "logo.png", "data": png}))
                    assert not (await recv_type(view, "asset_result"))["ok"]
                    await prod.send(json.dumps({"type": "asset_put", "name": "../../evil.exe", "data": png}))
                    assert not (await recv_type(prod, "asset_result"))["ok"]
                    await prod.send(json.dumps({"type": "asset_put", "name": "Series Logo.PNG", "data": png}))
                    assert (await recv_type(prod, "asset_result"))["ok"]
                    files = (await recv_type(view, "assets"))["files"]
                    assert [f["name"] for f in files] == ["Series Logo.png"]
                    loop = asyncio.get_running_loop()
                    body = await loop.run_in_executor(None, lambda: urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/broadcast/assets/Series%20Logo.png", timeout=3).read())
                    assert body == b"\x89PNG logo"
                    # liveries from the bridge are kept per car and replayed to a late viewer
                    for k in ("110_1", "110_2", "110_1"):
                        await bridge.send(json.dumps({"type": "livery", "key": k, "image": JPEG}))
                    await asyncio.sleep(0.3)
                    assert list(relay.liveries) == ["110_2", "110_1"]
                async with websockets.connect(url + "/view?token=V", max_size=None) as late:
                    keys = set()
                    try:
                        while len(keys) < 2:
                            m = json.loads(await asyncio.wait_for(late.recv(), 3))
                            if m.get("type") == "livery":
                                keys.add(m["key"])
                    except asyncio.TimeoutError:
                        pass
                    assert keys == {"110_1", "110_2"}
            return True
        assert asyncio.run(run())


# --------------------------------------------------------------------- camera packs

def test_camera_pack_installs_tracks_and_cars_folders_only():
    import zipfile
    from suite.cameras import install_pack
    with tempfile.TemporaryDirectory() as d:
        z = os.path.join(d, "pack.zip")
        with zipfile.ZipFile(z, "w") as f:
            f.writestr("Pack v2/tracks/limerock/2019/full/tv.cam", "cam")
            f.writestr("Pack v2/cars/mx5/car.cam", "cam")
            f.writestr("Pack v2/readme.txt", "hi")
            f.writestr("Pack v2/tracks/../../../evil.cam", "no")
        dest = os.path.join(d, "cameras")
        r = install_pack(z, dest)
        assert r["ok"] and sorted(r["installed"]) == ["cars/mx5/car.cam", "tracks/limerock/2019/full/tv.cam"]
        assert os.path.isfile(os.path.join(dest, "tracks", "limerock", "2019", "full", "tv.cam"))
        assert not os.path.exists(os.path.join(d, "evil.cam"))
        bare = os.path.join(d, "bare.zip")
        with zipfile.ZipFile(bare, "w") as f:
            f.writestr("tv.cam", "cam")
        r = install_pack(bare, dest)
        assert not r["ok"] and "tracks or cars" in r["error"]


# --------------------------------------------------------------------- iRacing's car renders

def test_render_params_follow_the_driver_entry_and_encode_spaces_as_percent_20():
    from pitwall.carrender import render_params, render_urls
    d = {"CarPath": "mx5 mx52016", "CarID": 67, "CarDesignStr": "3,ff0000,00ff00,0000ff",
         "CarNumberDesignStr": "0,1,ffffff,000000,ff0000", "CarNumber": "07", "LicColor": 0x0153DB,
         "CarSponsor_1": 12, "CarSponsor_2": 0, "ClubID": 4, "UserName": "Jo Racer", "TeamName": "Jo Racer"}
    p = render_params(d, r"C:\Users\a b\Documents\iRacing\paint\mx5\mx52016\car_1.tga")
    assert p["carPat"] == "3" and p["carCol"] == "ff0000,00ff00,0000ff"
    assert p["numPat"] == "0" and p["numSlnt"] == "1" and p["numcol"] == "ffffff,000000,ff0000"
    assert p["number"] == "07" and p["licCol"] == "0153db" and p["sponsors"] == "12,0"
    urls = render_urls(d, p["carCustPaint"])
    assert len(urls) == 3, "nested MX-5 folder: two folder forms, then the raw one"
    assert "carPath=mx5%5Cmx52016" in urls[0] and "carPath=mx5%2Fmx52016" in urls[1] and "carPath=mx5%20mx52016" in urls[2]
    assert "+" not in urls[0].split("?", 1)[1], "a '+' is read as a plus sign by iRacing"
    assert "carCustPaint=C%3A%5CUsers%5Ca%20b" in urls[0]
    assert len(render_urls({"CarPath": "porsche992cup", "CarID": 1}, None)) == 1


def test_trading_paints_files_are_found_team_paint_first():
    from pitwall.carrender import find_paint
    with tempfile.TemporaryDirectory() as root:
        os.makedirs(os.path.join(root, "mx5", "mx52016"))
        own = os.path.join(root, "mx5", "mx52016", "car_300.tga")
        open(own, "wb").close()
        assert find_paint("mx5 mx52016", 300, 0, root) == own
        team = os.path.join(root, "mx5", "mx52016", "car_team_77.tga")
        open(team, "wb").close()
        assert find_paint("mx5 mx52016", 300, 77, root) == team, "a team event shows the team's paint"
        assert find_paint("mx5 mx52016", 999, 0, root) is None
        assert find_paint("", 300, 0, root) is None


def test_car_renderer_renders_each_car_once_and_again_when_its_paint_changes():
    import time as _t
    from pitwall.carrender import CarRenderer
    calls, out = [], []
    PNG = b"\x89PNG fake"
    up = {"ok": False}

    def fetch(url):
        calls.append(url)
        if not up["ok"]:
            raise OSError("connection refused")
        return 200, "image/png", PNG
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "paint")
        os.makedirs(os.path.join(root, "gr86"))
        r = CarRenderer(d, out.append, fetch=fetch, root=root)
        r.RETRY_DOWN_S, r.gap_s = 0.2, 0.0
        drivers = [{"CarPath": "gr86", "CarID": 5, "UserID": 1, "TeamID": 0, "CarNumber": "1"},
                   {"CarPath": "gr86", "CarID": 5, "UserID": 2, "TeamID": 0, "CarNumber": "2"},
                   {"CarPath": "safety", "CarID": 9, "UserID": 0, "CarIsPaceCar": 1}]
        r.request(drivers)
        _t.sleep(0.4)
        assert out == [] and r.status["available"] is False, "iRacing not running: nothing yet"
        up["ok"] = True
        deadline = _t.time() + 3
        while len(out) < 2 and _t.time() < deadline:
            r._wake.set(); _t.sleep(0.05)
        assert sorted(m["key"] for m in out) == ["5_1:render", "5_2:render"]
        assert all(m["image"].startswith("data:image/png;base64,") and m["kind"] == "render" for m in out)
        n = len(calls)
        r.request(drivers)
        _t.sleep(0.3)
        assert len(calls) == n, "unchanged cars are not rendered again"
        # Trading Paints drops a paint for driver 2: rendered again, with the paint
        with open(os.path.join(root, "gr86", "car_2.tga"), "wb") as f:
            f.write(b"tga")
        r.request(drivers)
        deadline = _t.time() + 3
        while len(out) < 3 and _t.time() < deadline:
            _t.sleep(0.05)
        assert out[-1]["key"] == "5_2:render" and out[-1]["custom"] is True
        assert "carCustPaint=" in calls[-1]
        assert r.cached_message("5_1")["image"].startswith("data:image/png")


# --------------------------------------------------------------------- camera packs on disk

def test_camera_pack_folders_are_matched_to_the_track_and_cars():
    from pitwall.camerapacks import match_folder, packs_message
    folders = ["daytona 2011", "virginia 2022", "virginia", "okayama"]
    assert match_folder(folders, "virginia 2022 full") == "virginia 2022", "longest folder the name starts with"
    assert match_folder(folders, "Okayama") == "okayama"
    assert match_folder(folders, "spa 2024 gp") is None
    assert match_folder(["mx5 mx52016"], "mx5 mx52016") == "mx5 mx52016"
    with tempfile.TemporaryDirectory() as base:
        os.makedirs(os.path.join(base, "tracks", "virginia 2022", "full"))
        open(os.path.join(base, "tracks", "virginia 2022", "full", "tv.cam"), "w").close()
        open(os.path.join(base, "tracks", "virginia 2022", "readme.txt"), "w").close()
        os.makedirs(os.path.join(base, "cars", "mx5 mx52016"))
        open(os.path.join(base, "cars", "mx5 mx52016", "car.cam"), "w").close()
        sess = {"track": {"TrackName": "virginia 2022 full", "TrackDisplayName": "VIRginia"},
                "drivers": [{"CarPath": "mx5 mx52016"}, {"CarPath": "porsche992cup"}]}
        m = packs_message(sess, base)
        assert m["type"] == "camera_packs" and m["track"]["folder"] == "virginia 2022"
        assert m["track"]["files"] == ["full/tv.cam"], "only camera files are listed"
        assert [c["carPath"] for c in m["cars"]] == ["mx5 mx52016"]
        m2 = packs_message({"track": {"TrackName": "okayama full"}, "drivers": []}, base)
        assert m2["track"]["folder"] is None and m2["trackCount"] == 1
    assert packs_message(None, "/no/such/dir")["exists"] is False


def test_the_bridge_reports_camera_packs_with_the_session():
    from pitwall.bridge import Bridge
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    with tempfile.TemporaryDirectory() as base:
        b = Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None, render_cars=False, livery_dir=base)
        b.camera_packs_dir = base
        src.tick()
        out = b._build(0.0)
        packs = [m for m in out if m.get("type") == "camera_packs"]
        assert len(packs) == 1 and packs[0]["track"]["folder"] is None
        src.tick()
        assert not [m for m in b._build(1.0) if m.get("type") == "camera_packs"], "only when something changed"


def test_with_the_broadcast_off_nothing_moves_the_camera():
    """The broadcast's master switch starts off. Off: no automatic grid walk at the pace laps, no
    director, no battle, and the bridge refuses the producer's camera and replay commands."""
    from pitwall.bridge import Bridge, BROADCAST_OFF
    src = FakeSource(realtime=False, spectator=True, grid_s=5, parade_s=30, incidents=False)
    src.connect()
    gate = CommandGate(camera=True)
    cuts = []
    t = [0.0]
    d = Director(lambda n, g: cuts.append((n, g)) or gate.execute(src, {"cmd": "camera", "action": "car", "car": n, "group": g}),
                 rng=random.Random(1), clock=lambda: t[0])
    assert d.live is False and d.status()["live"] is False
    d.configure(on=True)
    assert d.owner == "you", "the director does not start with the broadcast off"
    assert d.follow_battle([1, 2]) is False and d.start_grid_walk([{"num": "1"}]) is False
    deriver, sess = Deriver(), build_session(src)
    for k in range(60 * 40):                           # through the pace laps into the race
        src.tick()
        if k % 8:
            continue
        t[0] = src.t
        d.tick(sess, build_cars(src, sess, deriver), build_state(src, deriver), [])
    assert cuts == [] and d.owner == "you" and not d._grid_walked
    # on: the director runs; off again: it stops at once
    d.set_live(True)
    d.configure(on=True)
    assert d.owner == "race"
    d.set_live(False)
    assert d.owner == "you" and d.status()["live"] is False

    class WS:
        def __init__(self, msgs):
            self.msgs, self.sent = [json.dumps(m) for m in msgs], []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

        async def send(self, raw):
            self.sent.append(json.loads(raw))
    b = Bridge(src, "ws://127.0.0.1:1", "t", allow_camera=True, log=lambda *a: None, render_cars=False,
               livery_dir=tempfile.mkdtemp())
    ws = WS([{"type": "cmd", "cmd": "camera", "action": "car", "car": "13", "group": 1, "camera": 0},
             {"type": "cmd", "cmd": "replay", "action": "speed", "speed": 1},
             {"type": "cmd", "cmd": "director", "action": "follow_battle", "cars": [1, 2]},
             {"type": "cmd", "cmd": "director", "action": "grid_walk"},
             {"type": "cmd", "cmd": "livery", "action": "sweep"},
             {"type": "cmd", "cmd": "director", "action": "set", "style": "classic"},       # settings are fine
             {"type": "cmd", "cmd": "director", "action": "broadcast", "on": True},
             {"type": "cmd", "cmd": "director", "action": "follow_battle", "cars": [1, 2]}])
    asyncio.run(b._consumer(ws))
    res = [m for m in ws.sent if m.get("type") == "cmd_result"]
    assert [r["ok"] for r in res] == [False, False, False, False, False, True, True, True], res
    assert all(r["error"] == BROADCAST_OFF for r in res[:5])
    assert b.director.live and b.director.owner == "battle"


# --------------------------------------------------------------------- car colours for map dots

def _png(w, h, pixel, ctype=6, filters=(0, 1, 2, 3, 4)):
    """A real PNG (8-bit RGBA or RGB), each row with a different filter, so the decoder's
    unfiltering is checked rather than assumed."""
    import struct as st
    import zlib as zl
    bpp = 4 if ctype == 6 else 3
    raw, prev = bytearray(), bytearray(w * bpp)
    for y in range(h):
        line = bytearray()
        for x in range(w):
            r, g, b, a = pixel(x, y)
            line += bytes((r, g, b, a)) if bpp == 4 else bytes((r, g, b))
        ft = filters[y % len(filters)]
        out = bytearray(len(line))
        for i in range(len(line)):
            left = line[i - bpp] if i >= bpp else 0
            up, ul = prev[i], (prev[i - bpp] if i >= bpp else 0)
            if ft == 0:
                p = 0
            elif ft == 1:
                p = left
            elif ft == 2:
                p = up
            elif ft == 3:
                p = (left + up) >> 1
            else:
                pa, pb, pc = abs(up - ul), abs(left - ul), abs(left + up - 2 * ul)
                p = left if pa <= pb and pa <= pc else up if pb <= pc else ul
            out[i] = (line[i] - p) & 255
        raw += bytes([ft]) + out
        prev = line

    def chunk(k, b):
        return st.pack(">I", len(b)) + k + b + st.pack(">I", zl.crc32(k + b) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", st.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0))
            + chunk(b"IDAT", zl.compress(bytes(raw))) + chunk(b"IEND", b""))


def _car_picture(body, backdrop=None):
    """A side view: a body-coloured box, black tyres, grey glass, on a transparent or plain backdrop."""
    def px(x, y):
        if 10 <= x < 70 and 14 <= y < 30:
            if 30 <= x < 50 and y < 19:
                return (120, 130, 140, 255)                # glass
            return (*body, 255)
        if (16 <= x < 26 or 54 <= x < 64) and 30 <= y < 38:
            return (12, 12, 12, 255)                       # tyres
        return (*backdrop, 255) if backdrop else (0, 0, 0, 0)
    return px


def test_a_cars_paint_colour_comes_from_its_picture():
    from pitwall.carcolor import png_color, png_pixels, design_color
    data = _png(80, 44, _car_picture((200, 30, 40)))
    w, h, px = png_pixels(data, step=1)
    assert (w, h, len(px)) == (80, 44, 80 * 44)
    assert px[20 * 80 + 12] == (200, 30, 40, 255), "every filter type undone"
    assert png_color(data) == "#c81e28"
    assert png_color(_png(80, 44, _car_picture((20, 90, 210), backdrop=(235, 235, 235)), ctype=2)) == "#145ad2", \
        "an opaque studio backdrop is not the paint"
    white = png_color(_png(80, 44, _car_picture((245, 245, 245))))
    assert white == "#f5f5f5", "a white car is white, not its tyres"
    assert png_color(b"not a png") is None
    assert design_color("1,ff3b30,ffffff,111111") == "#ff3b30" and design_color("") is None and design_color("3") is None


def test_the_renderer_reports_each_cars_colour():
    import time as _t
    from pitwall.carrender import CarRenderer
    got = {}
    data = _png(80, 44, _car_picture((0, 160, 80)))
    with tempfile.TemporaryDirectory() as d:
        r = CarRenderer(d, lambda m: None, fetch=lambda url: (200, "image/png", data), root=os.path.join(d, "paint"),
                        on_color=lambda k, c: got.__setitem__(k, c))
        r.request([{"CarPath": "gr86", "CarID": 5, "UserID": 7, "TeamID": 0, "CarNumber": "7"}])
        deadline = _t.time() + 3
        while not got and _t.time() < deadline:
            _t.sleep(0.05)
    assert got == {"5_7": "#00a050"}


# --------------------------------------------------------------------- where they're faster

def test_best_laps_timed_by_stretches_say_where_each_car_is_faster():
    """Two cars, sampled at 10 Hz as a replay is: car 1 is quicker in the first half of the lap, car 2 in
    the second. A lap with a pit visit and a lap with a jump (a reset) are not kept."""
    from pitwall.normalize import Deriver
    d = Deriver()
    K = d.STRETCHES

    def pace(car, x):                       # laps per second at lap fraction x
        quick_first = car == 1
        return 1 / 60 * (1.08 if (x < 0.5) == quick_first else 0.95)
    pos = {1: 0.5, 2: 0.5}
    t = 0.0
    for step in range(int(5 * 65 / 0.1)):
        t += 0.1
        cars = []
        for i in (1, 2):
            pos[i] += pace(i, pos[i] % 1) * 0.1
            cars.append({"idx": i, "pct": pos[i] % 1, "surface": "on_track", "onPitRoad": False, "bestLapS": None})
        d.lap_splits(cars, t)
    f = d.faster(1, 2)
    assert f is not None and len(f["gainS"]) == K
    assert all(v > 0 for v in f["gainS"][:K // 2 - 1]) and all(v < 0 for v in f["gainS"][K // 2 + 1:])
    assert abs(sum(f["gainS"]) - f["totalS"]) < 0.01
    # each lap's time: the stretch times add up to the lap
    lap1 = 1 / (1 / 60 * 1.08) * 0.5 + 1 / (1 / 60 * 0.95) * 0.5
    assert abs(f["lapA"][-1] - lap1) < 0.05, f["lapA"][-1]
    # a lap with a pit visit is not kept: car 3 only ever does pit laps
    d2 = Deriver()
    p, t = 0.5, 0.0
    for step in range(int(3 * 62 / 0.1)):
        t += 0.1
        p += 1 / 60 * 0.1
        d2.lap_splits([{"idx": 3, "pct": p % 1, "surface": "on_track", "onPitRoad": 0.2 < (p % 1) < 0.25}], t)
    assert d2._split[3]["best"] is None
    # never quicker than iRacing's own best lap for the car (a slip in the timing)
    d3 = Deriver()
    p, t = 0.5, 0.0
    for step in range(int(3 * 62 / 0.1)):
        t += 0.1
        p += 1 / 60 * 0.1
        d3.lap_splits([{"idx": 4, "pct": p % 1, "surface": "on_track", "onPitRoad": False, "bestLapS": 70.0}], t)
    assert d3._split[4]["best"] is None


TALLADEGA = "/mnt/user-data/uploads/Recordings/Talladega-Super-Speedway-Race_2026-10-25_192220.jsonl.gz"


@pytest.mark.skipif(not os.path.exists(TALLADEGA), reason="needs the Talladega capture")
def test_stretch_timed_best_laps_match_iracings_own_on_a_real_race():
    """Every car's best lap, timed here from its position alone, against iRacing's CarIdxBestLapTime:
    measured on the Talladega replay, the median difference was 0.003 s."""
    import statistics
    from pitwall.normalize import Deriver, build_session, build_state, build_cars
    from pitwall.sources import ReplaySource
    src = ReplaySource(TALLADEGA, realtime=False)
    src.connect()
    d, sess, k, cars = Deriver(), None, 0, None
    while src.tick():
        k += 1
        if sess is None or k % 600 == 0:
            sess = build_session(src)
        build_state(src, d, sess)
        cars = build_cars(src, sess, d)
    by = {c["idx"]: c for c in cars["cars"]}
    diffs = [abs(st["bestS"] - by[i]["bestLapS"]) for i, st in d._split.items()
             if st["bestS"] and by.get(i) and (by[i].get("bestLapS") or 0) > 0]
    assert len(diffs) >= 15
    assert statistics.median(diffs) < 0.01 and max(diffs) < 0.05, (statistics.median(diffs), max(diffs))
    assert len(cars["derived"]["faster"]) >= 15


def test_helmets_are_rendered_once_and_a_refusal_is_not_retried():
    """iRacing's helmet picture (pk_helmet.png, not yet seen working): the design and the driver's own helmet
    paint go in; a server that refuses it is asked once per design, and the overlay draws its own helmet."""
    import time as _t
    from pitwall.carrender import CarRenderer, helmet_params, find_helmet_paint
    assert helmet_params({"HelmetDesignStr": "65,005eb8,950002,000000"}, None) == {"size": 2, "hlmtPat": "65", "hlmtCol": "005eb8,950002,000000"}
    calls, out = [], []

    def fetch(url):
        calls.append(url)
        if "pk_helmet" in url:
            return (404, "text/html", b"no") if "UserID" not in url and "hlmtCustPaint" not in url and len(calls) < 99 and "refuse" in state else (200, "image/png", b"\x89PNG h")
        return 200, "image/png", b"\x89PNG c"
    state = set()
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "paint")
        os.makedirs(os.path.join(root, "gr86"))
        with open(os.path.join(root, "helmet_1.tga"), "wb") as f:
            f.write(b"tga")
        assert find_helmet_paint(1, root).endswith("helmet_1.tga") and find_helmet_paint(2, root) is None
        r = CarRenderer(d, out.append, fetch=fetch, root=root)
        r.gap_s, r.helmets = 0.0, True        # helmets are off until pk_helmet.png is seen working
        drivers = [{"CarPath": "gr86", "CarID": 5, "UserID": 1, "TeamID": 0, "CarNumber": "1", "HelmetDesignStr": "1,ffffff,ff0000,000000"}]
        r.request(drivers)
        deadline = _t.time() + 3
        while len(out) < 2 and _t.time() < deadline:
            _t.sleep(0.05)
        kinds = sorted(m["kind"] for m in out)
        assert kinds == ["helmet", "render"]
        h = [m for m in out if m["kind"] == "helmet"][0]
        assert h["key"] == "5_1:helmet" and h["custom"] is True
        assert any("pk_helmet.png" in u and "hlmtCustPaint=" in u for u in calls)
        n = len(calls)
        r.request(drivers)
        _t.sleep(0.3)
        assert len(calls) == n, "an unchanged helmet is not asked for again"
        # a server that refuses helmets: asked once, never again for the same design
        state.add("refuse")
        drivers2 = [{"CarPath": "gr86", "CarID": 5, "UserID": 2, "TeamID": 0, "CarNumber": "2", "HelmetDesignStr": "2,000000,ffffff,ff0000"}]
        r.request(drivers2)
        _t.sleep(0.5)
        hc = [u for u in calls if "pk_helmet" in u and "hlmtPat=2" in u]
        r.request(drivers2)
        _t.sleep(0.3)
        assert len([u for u in calls if "pk_helmet" in u and "hlmtPat=2" in u]) == len(hc) == 1
        assert not any(m["key"] == "5_2:helmet" for m in out)


# --------------------------------------------------------------------- multiclass: traffic and sector colours

def _mc_car(idx, cls, class_pos, u, lap, **kw):
    c = {"idx": idx, "classId": cls, "classPos": class_pos, "pos": idx, "surface": "on_track", "onPitRoad": False,
         "estTimeS": u * lap, "classEstLapS": lap, "pct": u, "lapCompleted": 0, "lastLapS": -1.0, "derived": {}}
    c.update(kw)
    return c


def _pace(d, idx, lap):
    """Racing laps either side of `lap` seconds, so Deriver.pace() (their median) is `lap`. Each time differs
    from the one before: iRacing's unchanged LapLastLapTime is a stale time, not a new lap."""
    t = 0.0
    d.record_lap({"idx": idx, "lapCompleted": 0, "lastLapS": -1.0, "onPitRoad": False, "derived": {}}, t)
    for k, lt in enumerate([lap - 0.05, lap + 0.05, lap - 0.05, lap + 0.05], start=1):
        t += lt
        for dt in (0.0, 4.0):                       # the lap, then a moment later (its time settles)
            d.record_lap({"idx": idx, "lapCompleted": k, "lastLapS": lt, "onPitRoad": False, "derived": {}}, t + dt)


def test_class_traffic_finds_each_leaders_next_slower_class_pack():
    d = Deriver()
    for i, lap in [(1, 100.0), (2, 101.0), (3, 110.0), (4, 110.0), (5, 110.0), (6, 110.0), (7, 110.0), (8, 125.0)]:
        _pace(d, i, lap)

    def field(t):
        return [
            _mc_car(1, 10, 1, 0.10 + t / 100.0, 100.0),            # GTP leader, 100 s laps
            _mc_car(2, 10, 2, 0.12 + t / 101.0, 101.0),            # its own class: never traffic for it
            _mc_car(3, 20, 1, 0.150 + t / 110.0, 110.0),           # GTD leader 0.05 lap ahead: 55 s away
            _mc_car(4, 20, 2, 0.152 + t / 110.0, 110.0),           # 2 s later: the same pack
            _mc_car(5, 20, 3, 0.40 + t / 110.0, 110.0),            # far up the road: past the horizon
            _mc_car(6, 20, 4, 0.151 + t / 110.0, 110.0, onPitRoad=True),   # in the pit lane: not traffic
            _mc_car(7, 20, 5, 0.153 + t / 110.0, 110.0, surface="off_track"),
            _mc_car(8, 30, 1, 0.50 + t / 125.0, 125.0)]            # a third class, far ahead
    first = d.class_traffic(field(0.0), 0.0)
    gtp = next(x for x in first if x["classId"] == 10)
    assert gtp["idx"] == 1 and gtp["aheadIdx"] == 3 and gtp["aheadClassId"] == 20
    assert abs(gtp["inS"] - 55.0) < 0.2                            # 0.05 lap at 1/100 - 1/110 a second
    assert gtp["packN"] == 2 and gtp["packIdx"] == [3, 4]          # car 5 too far, 6 in the pits, 7 off track
    # the GTD leader would reach the third class, but not within the horizon (0.35 lap at 1/110 - 1/125: 320 s)
    assert not any(x["classId"] == 20 for x in first)
    # it counts down, second by second
    later = next(x for x in d.class_traffic(field(10.0), 10.0) if x["classId"] == 10)
    assert abs(later["inS"] - 45.0) < 0.5
    # a leader in the pit lane has no traffic warning
    f = field(11.0); f[0]["onPitRoad"] = True
    assert not any(x["classId"] == 10 for x in d.class_traffic(f, 11.0))


def test_sector_colours_are_per_class():
    a = {"idx": 1, "classId": 10, "derived": {"sectorS": [30.0, 40.0, 50.0], "bestSectorS": [30.0, 39.5, 50.0]}}
    b = {"idx": 2, "classId": 10, "derived": {"sectorS": [31.0, 39.0, 52.0], "bestSectorS": [30.5, 39.0, 51.0]}}
    c = {"idx": 3, "classId": 20, "derived": {"sectorS": [60.0, 70.0, None], "bestSectorS": [60.0, 70.0, 80.0]}}
    e = {"idx": 4, "classId": 20, "derived": {}}                     # no sectors timed yet
    best = Deriver.sector_marks([a, b, c, e])
    assert best == {"10": [30.0, 39.0, 50.0], "20": [60.0, 70.0, 80.0]}
    assert a["derived"]["sectorMarks"] == ["p", "y", "p"]           # class best, neither, class best
    assert b["derived"]["sectorMarks"] == ["y", "p", "y"]           # 31 > its 30.5 best: yellow
    assert c["derived"]["sectorMarks"] == ["p", "p", None]          # the slower class is purple against its own class
    assert e["derived"]["sectorMarks"] is None


def test_multiclass_derived_values_from_the_fake_race():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    d = Deriver()
    sess, saw_traffic, saw_marks = None, 0, 0
    for k in range(60 * 420):
        src.tick()
        if k % 10:
            continue
        if sess is None:
            sess = build_session(src)
        build_state(src, d)
        out = build_cars(src, sess, d)
        by = {c["idx"]: c for c in out["cars"]}
        for x in out["derived"]["classTraffic"]:
            saw_traffic += 1
            assert by[x["idx"]]["classPos"] == 1
            assert by[x["aheadIdx"]]["classId"] != by[x["idx"]]["classId"]
            assert 0 <= x["inS"] <= Deriver.CLASS_TRAFFIC_HORIZON_S and x["packN"] >= 1
        saw_marks += sum(1 for c in out["cars"] if (c["derived"].get("sectorMarks") or []))
        assert isinstance(out["derived"]["classBestSectorS"], dict)
    assert saw_traffic > 0, "the fake's classes should meet"
    assert saw_marks > 0


# --------------------------------------------------------------------- race moments

def _rm_car(idx, cls, lap, pct, cpos, **kw):
    c = {"idx": idx, "classId": cls, "lap": lap, "pct": pct, "surface": "on_track", "onPitRoad": False,
         "classPos": cpos, "pos": cpos, "flags": [], "derived": {"liveClassPos": cpos, "livePos": cpos}}
    c.update(kw)
    return c


def test_car_flags_reach_the_feed_once_and_a_served_penalty_is_reported():
    d = Deriver()
    def tick(t, flags_1, flags_2=(), surf_2="on_track"):
        cars = [_rm_car(1, 10, 3, .5, 1, flags=list(flags_1)),
                _rm_car(2, 10, 3, .4, 2, flags=list(flags_2), surface=surf_2)]
        d.field_events(cars, t, None, {}, state="racing", session_num=2)
    tick(0, ["black"])                                   # already black-flagged when first seen: seeded silently
    tick(1, ["black", "blue"])
    assert not [e for e in d.feed_events if e["kind"] in ("car_flag", "penalty_served")]
    tick(2, [], ["furled"])
    served = [e for e in d.feed_events if e["kind"] == "penalty_served"]
    assert len(served) == 1 and served[0]["idx"] == 1 and served[0]["sessionNum"] == 2
    fl = [e for e in d.feed_events if e["kind"] == "car_flag"]
    assert [(e["idx"], e["flag"]) for e in fl] == [(2, "furled")] and fl[0]["replayT"] == 2
    tick(3, ["black"], [])                               # a new penalty
    tick(4, [], [])
    tick(5, ["black"], [])                               # again within 20 s: not repeated
    assert [(e["idx"], e["flag"]) for e in d.feed_events if e["kind"] == "car_flag"] == [(2, "furled"), (1, "black")]
    tick(6, [], [], surf_2="not_in_world")
    tick(7, [], ["black"])                               # back in the world with a flag: seeded, not news
    assert len([e for e in d.feed_events if e["kind"] == "car_flag"]) == 2


def test_a_pass_on_track_is_reported_with_its_moment_but_a_pit_stop_is_not():
    d = Deriver()
    def tick(t, cars):
        d.field_events(cars, t, None, {}, state="racing", session_num=1)
    # car 2 runs behind car 1, then goes by at t=10
    for t in range(0, 10):
        tick(t, [_rm_car(1, 10, 5, .50 + t * .001, 1), _rm_car(2, 10, 5, .495 + t * .0012, 2)])
    for t in range(10, 13):
        tick(t, [_rm_car(1, 10, 5, .51 + t * .001, 2), _rm_car(2, 10, 5, .515 + t * .0012, 1)])
    p = [e for e in d.feed_events if e["kind"] == "pass"]
    assert len(p) == 1
    assert p[0]["idx"] == 2 and p[0]["passedIdx"] == [1] and p[0]["classPos"] == 1 and p[0]["fromClassPos"] == 2
    assert p[0]["replayT"] == 10 and p[0]["sessionNum"] == 1, "the replay starts where the pass happened"
    # car 2 goes down pit road and car 1 goes by: a place gained, not a pass
    for t in range(13, 20):
        tick(t, [_rm_car(1, 10, 5, .53 + t * .001, 1), _rm_car(2, 10, 5, .52, 2, onPitRoad=True)])
    # a different class going by is not a pass for position
    for t in range(20, 26):
        tick(t, [_rm_car(1, 10, 5, .6, 1), _rm_car(3, 20, 5, .61, 1), _rm_car(4, 20, 5, .59 + t * .0001, 2)])
    assert len([e for e in d.feed_events if e["kind"] == "pass"]) == 1
    # under a caution nothing counts
    for t in range(26, 30):
        d.field_events([_rm_car(1, 10, 6, .1, 2), _rm_car(2, 10, 6, .11, 1)], t, None, {}, state="caution", session_num=1)
    assert len([e for e in d.feed_events if e["kind"] == "pass"]) == 1


def test_passes_from_the_fake_race():
    """The multiclass fake runs a whole race: every pass is in one class, between cars on track."""
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    d = Deriver()
    sess = None
    for k in range(60 * 420):
        src.tick()
        if k % 10:
            continue
        if sess is None:
            sess = build_session(src)
        build_state(src, d)
        build_cars(src, sess, d)
    passes = [e for e in d.feed_events if e["kind"] == "pass"]
    assert passes, "a 5-minute fake race should have passes"
    for e in passes:
        assert e["classPos"] < e["fromClassPos"] and e["passedIdx"] and e["idx"] not in e["passedIdx"]
        assert e["replayT"] <= e["t"]


# --------------------------------------------------------------------- qualifying

def _prof(lap_s, K=24, start=100.0, upto=None):
    """Boundary times for an even lap of lap_s seconds: best profile (from 0) or live crossings (from start)."""
    return [round(start + lap_s * k / K, 3) for k in range((upto if upto is not None else K) + 1)]


def test_quali_lap_against_the_class_pole_lap():
    d = Deriver()
    K = Deriver.STRETCHES
    cars = [{"idx": 1, "classId": 10, "bestLapS": 100.0, "onPitRoad": False},
            {"idx": 2, "classId": 10, "bestLapS": 101.0, "onPitRoad": False},
            {"idx": 3, "classId": 10, "bestLapS": 102.0, "onPitRoad": False},
            {"idx": 4, "classId": 20, "bestLapS": 110.0, "onPitRoad": False}]
    pole = [x - 100.0 for x in _prof(100.0)]
    d._split = {1: {"best": pole, "bestS": 100.0, "cross": None, "bad": False},
                2: {"best": [x - 100.0 for x in _prof(101.0)], "bestS": 101.0, "cross": None, "bad": False},
                # car 3 is half way round a lap at 100.6 s pace: 0.3 s down on pole at stretch 12
                3: {"best": [x - 100.0 for x in _prof(102.0)], "bestS": 102.0, "cross": _prof(100.6, upto=12), "bad": False},
                # car 4 is alone in its class with no timed best: no pole, no delta
                4: {"best": None, "bestS": None, "cross": _prof(110.0, upto=12), "bad": False}}
    d.quali_laps(cars)
    q = cars[2]["derived"]["qualiLap"]
    assert q["stretch"] == 12 and q["of"] == K and q["poleIdx"] == 1
    assert abs(q["deltaPoleS"] - 0.3) < 0.01 and abs(q["deltaOwnS"] - (-0.7)) < 0.01
    assert abs(q["projectedS"] - 100.3) < 0.01 and q["projectedPos"] == 2, "between the pole and car 2's 101.0"
    assert [c["derived"]["classPoleGapS"] for c in cars] == [0.0, 1.0, 2.0, 0.0]
    for c, cp in zip(cars, (1, 2, 3, 1)):
        c["classPos"] = cp
    d.best_intervals(cars)                 # the standings' Int column outside a race: to the car ahead's best
    assert [c["derived"]["classBestIntervalS"] for c in cars] == [None, 1.0, 1.0, None]
    assert cars[3]["derived"]["qualiLap"] is None
    # a slow lap (an out- or cool-down lap) is not a push lap
    d._split[3]["cross"] = _prof(110.0, upto=12)
    d.quali_laps(cars)
    assert cars[2]["derived"]["qualiLap"] is None
    # nor one with a reset or the pit lane in it, nor before two stretches are done
    d._split[3].update(cross=_prof(100.6, upto=12), bad=True)
    d.quali_laps(cars)
    assert cars[2]["derived"]["qualiLap"] is None
    d._split[3].update(cross=_prof(100.6, upto=1), bad=False)
    d.quali_laps(cars)
    assert cars[2]["derived"]["qualiLap"] is None


def test_quali_laps_from_the_fake_session():
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    d = Deriver()
    sess, seen = None, 0
    for k in range(60 * 400):
        src.tick()
        if k % 10:
            continue
        if sess is None:
            sess = build_session(src)
        build_state(src, d)
        out = build_cars(src, sess, d)
        for c in out["cars"]:
            q = (c.get("derived") or {}).get("qualiLap")
            if q:
                seen += 1
                assert q["deltaPoleS"] <= Deriver.QUALI_PUSH_S and 1 <= q["projectedPos"] <= len(out["cars"])
    assert seen > 0


# --------------------------------------------------------------------- tyre history

def test_tyre_runs_from_compound_changes_in_the_pits():
    d = Deriver()
    c = {"idx": 3, "classId": 10, "pos": 3, "classPos": 3, "lap": 1, "compound": 0, "onPitRoad": False,
         "surface": "on_track", "fastRepairsUsed": 0}
    x = d.car_extras(dict(c), 0.0, {}, None, True, False)
    assert x["tyreRuns"] == [{"compound": 0, "fromLap": 1, "laps": 0}], "seen at the start: the run's start is known"
    x = d.car_extras(dict(c, lap=12), 50.0, {}, None, True, False)
    assert len(x["tyreRuns"]) == 1 and x["tyreRuns"][0]["laps"] == 11
    x = d.car_extras(dict(c, lap=14, onPitRoad=True, compound=1), 60.0, {}, None, True, False)
    x = d.car_extras(dict(c, lap=20, compound=1), 90.0, {}, None, True, False)
    assert [(r["compound"], r["fromLap"], r["laps"]) for r in x["tyreRuns"]] == [(0, 1, 13), (1, 14, 6)]
    # first seen mid-race: the first run's start is not known
    d2 = Deriver()
    x = d2.car_extras(dict(c, lap=9), 0.0, {}, None, True, False)
    assert x["tyreRuns"] == [{"compound": 0, "fromLap": None, "laps": None}]


# --------------------------------------------------------------------- league table

def _parse_league(text):
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broadcast", "producer.html"),
                encoding="utf-8").read()
    m = re.search(r"\n  function parseLeague\(text\) \{[\s\S]*?\n  \}\n", page)
    assert m, "parseLeague not found in the producer page"
    js = m.group(0) + "\nconsole.log(JSON.stringify(parseLeague(require('fs').readFileSync(0, 'utf8'))));"
    r = subprocess.run([node, "-e", js], input=text, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_league_tables_in_the_usual_shapes():
    # a spreadsheet export with headings, quoted names and a byte order mark
    rows = _parse_league('﻿Pos,Driver,Car #,Class,Points\n2,"Smith, John",7,GT3,88\n1,Ana Lopez,12,GT3,101\n')
    assert [(r["pos"], r["name"], r["num"], r["cls"], r["points"]) for r in rows] == [
        ("1", "Ana Lopez", "12", "GT3", "101"), ("2", "Smith, John", "7", "GT3", "88")]
    # tabs (pasted from a web page), different headings, an iRacing customer id
    rows = _parse_league("Rank\tCust ID\tName\tTotal\n1\t12345\tAna Lopez\t101\n2\t678\tJohn Smith\t88")
    assert rows[0]["uid"] == "12345" and rows[1]["points"] == "88"
    # semicolons (European spreadsheets) and no headings: text is the name, the last number the points
    rows = _parse_league("Ana Lopez;101\nJohn Smith;88\nKim Park;120")
    assert [(r["pos"], r["name"]) for r in rows] == [("1", "Kim Park"), ("2", "Ana Lopez"), ("3", "John Smith")]
    # JSON from a league site
    rows = _parse_league('{"standings":[{"position":1,"driver":"Ana Lopez","points":101},{"position":2,"driver":"John Smith","points":88}]}')
    assert [(r["pos"], r["name"], r["points"]) for r in rows] == [("1", "Ana Lopez", "101"), ("2", "John Smith", "88")]
    assert _parse_league("   ") == []


# --------------------------------------------------------------------- studio mode

def test_studio_preview_and_take_agree_between_the_producer_and_the_overlay():
    """Staged changes (gfx studio.pv) are laid over what is on air the same way in the producer's controls
    and in the overlay's Preview screen; a staged removal survives the relay's merge as a marker."""
    import re
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = {}
    for page in ("producer", "overlay"):
        text = open(os.path.join(root, "broadcast", page + ".html"), encoding="utf-8").read()
        m = re.search(r"\n  function staged\(into, patch\) \{[\s\S]*?\n  \}\n", text)
        assert m, page
        src[page] = m.group(0)
        assert 'const DEL = "__pw_del__"' in text, page
    assert src["producer"] == src["overlay"], "the two pages must stage alike"
    prod = open(os.path.join(root, "broadcast", "producer.html"), encoding="utf-8").read()
    enc = re.search(r"\n  const enc = [^\n]*\n  const dec = [^\n]*\n", prod).group(0)
    js = 'const DEL = "__pw_del__";' + src["producer"] + enc + """
const onAir = {tower: {show: true, mode: "interval", rows: 20}, h2h: {cars: [1, 2], show: true}, podium: {show: false}};
const pv = enc({tower: {mode: "best"}, h2h: {cars: null}, podium: {show: true}, pip: {url: "https://x"}});
const view = staged(JSON.parse(JSON.stringify(onAir)), JSON.parse(JSON.stringify(pv)));
const take = dec(JSON.parse(JSON.stringify(pv)));
console.log(JSON.stringify({pv, view, take}));"""
    r = subprocess.run([node, "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["pv"]["h2h"]["cars"] == "__pw_del__", "a staged removal is a marker, not a null the relay would drop"
    assert out["view"] == {"tower": {"show": True, "mode": "best", "rows": 20}, "h2h": {"show": True},
                           "podium": {"show": True}, "pip": {"url": "https://x"}}
    assert out["take"]["h2h"]["cars"] is None, "Take sends the removal as the null the relay understands"
    # the relay keeps the marker while staging (its merge drops only None)
    from relay.server import merge_gfx
    g = merge_gfx({}, {"studio": {"pv": out["pv"]}})
    assert g["studio"]["pv"]["h2h"]["cars"] == "__pw_del__"


def test_the_master_switch_answers_while_the_sim_is_closed():
    """The producer's "Broadcast off/ON" looked stuck: the bridge said the switch had changed only in the stream's
    next frame, and with iRacing closed or between sessions there is none. It now says so at once."""
    from pitwall.bridge import Bridge

    class WS:
        def __init__(self, msgs):
            self.msgs, self.sent = [json.dumps(m) for m in msgs], []

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.msgs:
                raise StopAsyncIteration
            return self.msgs.pop(0)

        async def send(self, raw):
            self.sent.append(json.loads(raw))

    class Closed(FakeSource):              # iRacing not running: no frames at all
        def tick(self):
            return False

    src = Closed(realtime=False, spectator=True)
    b = Bridge(src, "ws://127.0.0.1:1", "t", allow_camera=True, log=lambda *a: None, render_cars=False,
               livery_dir=tempfile.mkdtemp())
    ws = WS([{"type": "cmd", "cmd": "director", "action": "broadcast", "on": True}])
    asyncio.run(b._consumer(ws))
    st = [m for m in ws.sent if m.get("type") == "director"]
    assert st and st[-1]["live"] is True, "the new state goes out without waiting for a frame"
    res = [m for m in ws.sent if m.get("type") == "cmd_result"]
    assert res and res[0]["ok"] and res[0]["data"]["live"] is True
    ws = WS([{"type": "cmd", "cmd": "director", "action": "broadcast", "on": False}])
    asyncio.run(b._consumer(ws))
    assert [m for m in ws.sent if m.get("type") == "director"][-1]["live"] is False


def test_director_tells_stories_and_moves_on():
    """In race mode the director used to cut every few seconds, often to a random car "in the field", and went
    back to the top battle three times in four. Now it stays with one story for a few shots, never longer than
    SEGMENT_MAX_S, and spreads the air between stories; nothing is picked at random."""
    from pitwall.director import SEGMENT_MAX_S
    src, d, cuts, clock = _director_rig()
    deriver = Deriver()
    sess = build_session(src)
    log = []                                  # (time, story key, reason)
    for k in range(60 * 480):
        src.tick()
        if k % 8:
            continue
        st = build_state(src, deriver)
        cars = build_cars(src, sess, deriver)
        clock[0] = k / 60.0
        n = len(cuts)
        recent = [e for e in deriver.feed_events[-60:] if e.get("kind") in ("race_incident", "pass", "class_lead")]
        d.tick(sess, cars, st, recent)
        if len(cuts) > n:
            log.append((clock[0], (d.segment or {}).get("key"), d.current["reason"]))
    assert len(log) > 30
    assert not [r for _, _, r in log if r == "the field"], "no random cars"
    # how long each story stayed on air, run by run
    runs, start = [], None
    for i, (t, key, _) in enumerate(log):
        if key is None:
            continue
        if i == 0 or log[i - 1][1] != key:
            start = t
        nxt = log[i + 1][0] if i + 1 < len(log) else t
        if i + 1 == len(log) or log[i + 1][1] != key:
            runs.append((key, nxt - start))
    from pitwall.director import BATTLE_HOT_MAX_S
    assert max(s for k, s in runs if not k.startswith("battle:")) <= SEGMENT_MAX_S + 12, runs
    assert max(s for _, s in runs) <= BATTLE_HOT_MAX_S + 12, runs      # a close battle may stay to its end
    keys = [k for _, k, _ in log if k]
    assert len(set(keys)) >= 4, set(keys)
    top = max(set(keys), key=keys.count)
    assert keys.count(top) / len(keys) < 0.5, f"{top} hogged the air"
    # and shots inside a story last longer than the old 6-9 s hops between random cars
    assert len(runs) < len(log) * 0.6


def test_the_checkered_flag_waits_for_the_leader():
    """iRacing raises the checkered flag when a timed race's clock runs out, a lap before the leader finishes:
    until SessionState turns checkered it is the last lap (the white flag), on air and in the feed."""
    from pitwall.normalize import race_flags
    assert race_flags(["checkered", "green"], "racing") == ["green", "white"]
    assert race_flags(["checkered", "white"], "racing") == ["white"]
    assert race_flags(["checkered"], "checkered") == ["checkered"]
    assert race_flags(["checkered"], "cool_down") == ["checkered"]
    assert race_flags(None, "racing") == []
    d = Deriver()
    base = {"timeS": 0.0, "state": "racing", "flags": ["green"]}
    d.state_events(0.0, dict(base), {}, {})
    d.state_events(30.0, dict(base, flags=["checkered"]), {}, {})          # the clock ran out
    fl = [e["flag"] for e in d.feed_events if e["kind"] == "flag"]
    assert "checkered" not in fl and "white" in fl
    d.state_events(120.0, dict(base, flags=["checkered"], state="checkered"), {}, {})   # the leader crosses
    assert [e["flag"] for e in d.feed_events if e["kind"] == "flag"][-1] == "checkered"


def test_director_follows_push_laps_outside_a_race_and_the_leader_on_the_last_lap():
    from pitwall.director import Director
    cuts = []
    t = [0.0]
    d = Director(lambda n, g: cuts.append((n, g)) or {"ok": True}, rng=random.Random(2), clock=lambda: t[0])
    d.set_live(True)
    d.configure(on=True, hold_s=6)
    sess = {"cameras": [{"num": 1, "name": "TV1"}, {"num": 2, "name": "Chase"}, {"num": 3, "name": "Cockpit"}]}
    st = {"session": {"state": "racing"}, "derived": {"liveEdge": {"atLive": True, "tLive": 500.0}}}

    def car(i, pos, **kw):
        c = {"idx": i, "num": str(i), "pos": pos, "classPos": pos, "classId": 1, "surface": "on_track", "lap": 5, "derived": {}}
        c.update(kw)
        return c
    # qualifying: car 7 is on a lap that would take pole; car 3 leads but is cruising
    q = {"isRace": False}
    cars = {"cars": [car(3, 1), car(7, 4, derived={"qualiLap": {"projectedPos": 1, "stretch": 12, "of": 24}})], "derived": q}
    for k in range(20):
        t[0] = 100 + k
        d.tick(sess, cars, st, [])
    assert d.segment["key"] == "push:7" and cuts[-1][0] == "7"
    # a push lap is followed beyond the usual story length, to the line
    for k in range(60):
        t[0] = 120 + k
        d.tick(sess, cars, st, [])
    assert d.segment["key"] == "push:7", "still on the lap after a minute"
    # a race's last lap: the leader to the line, whatever else is on
    cuts.clear()
    d2 = Director(lambda n, g: cuts.append((n, g)) or {"ok": True}, rng=random.Random(2), clock=lambda: t[0])
    d2.set_live(True)
    d2.configure(on=True, hold_s=6)
    race = {"isRace": True, "race": {"lapsToGo": 1}, "battles": [{"id": "1:4", "cars": [4, 5], "gapsS": [0.4], "closingSPerLap": [0.2], "forClassPos": 4}]}
    cars = {"cars": [car(2, 1), car(4, 4), car(5, 5)], "derived": race}
    for k in range(30):
        t[0] = 300 + k
        d2.tick(sess, cars, st, [])
    assert d2.segment["key"] == "finish" and cuts[-1][0] == "2"


def test_overlay_makes_room_for_every_graphic():
    """Every graphic that can be moved is also kept clear of the others (makeRoom): none is left out of
    ROOM_ORDER, each has one direction to slide, and the corner map steps aside for the faster panel's own."""
    import re
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    ids = set(re.findall(r'"(\w+)"', re.search(r"const LAYOUT_IDS = \[([^\]]*)\]", page).group(1)))
    order = re.findall(r'"(\w+)"', re.search(r"const ROOM_ORDER = \[([^\]]*)\]", page).group(1))
    up = set(re.findall(r'"(\w+)"', re.search(r"const ROOM_UP = new Set\(\[([^\]]*)\]", page).group(1)))
    assert set(order) == ids and len(order) == len(ids)
    assert up <= ids
    assert 'shown("map") && !!S.cars && !$("faster").classList.contains("on")' in page
    assert "if (EDIT) return;" in page.split("function makeRoom()")[1][:200]   # Arrange shows real positions


def test_every_look_moves_its_own_way():
    """Motion (theme.motion on | calm | off): every look has an entrance, an update flash and the loops, and the
    entrances never change a graphic's measured box (makeRoom measures it): clip-path, opacity and filter only."""
    import re
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    prod = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "producer.html"), encoding="utf-8").read()
    presets = re.findall(r"(\w+): \"", re.search(r"const PRESETS = \{([^}]*)\}", prod).group(1))
    css = page.split("/* ---------- MOTION")[1].split("/* ---------- /LOOKS")[0]
    for p in presets:
        if p in ("imsa", "arcade"):
            continue                                   # imsa is the default (:root), arcade keeps its own entrances
        assert ':root[data-preset="%s"]{--m-in:' % p in css
        assert ':root[data-preset="%s"] .lk-hit .inner::after' % p in css
    for name in ("mWipeL", "mWipeR", "mDrop", "mRise", "mOpen", "mPower", "mNeon"):
        body = re.search(r"@keyframes %s\{(.*?)\}\}" % name, css).group(1)
        props = set(re.findall(r"([a-z-]+):", body))
        assert props <= {"clip-path", "opacity", "filter"}, (name, props)
    assert 'dataset.motion = th.motion === "off" || th.motion === "calm"' in page
    assert ':root[data-motion="on"] .tw-head::before' in css          # the loops are Animated only
    assert 'data-motion="${k}"' in prod and "ds.motion" in prod


def test_producer_is_a_control_room():
    """The producer panel fits one screen: four columns, settings in the Setup drawer, every graphic a tile."""
    import re
    prod = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "producer.html"), encoding="utf-8").read()
    for col in ("colCam", "colField", "colGfx", "colRight"):
        assert '<div id="%s">' % col in prod
    main = prod.split('<main id="main"')[1].split("</main>")[0]
    for pane in ("show", "look", "studio", "liveries", "obs"):
        assert 'data-pane="%s"' % pane not in main                        # settings are not in the columns
        assert 'data-pane="%s"' % pane in prod.split('<div id="setup"')[1]
    layers = set(re.findall(r'\["(\w+)", "', prod.split("const LAYERS = [")[1].split("];")[0]))
    tiled = set(re.findall(r'"(\w+)"', prod.split("const TILE_GROUPS = [")[1].split("]];")[0])) - {"Always", "Race", "Alerts", "Qualifying", "Show"}
    assert layers == tiled, layers ^ tiled
    for sec in set(re.findall(r'(set[A-Z]\w+)"', prod.split("const GEAR = {")[1].split("};")[0])):
        assert 'id="%s"' % sec in prod, sec


def _walk_rig(lead_start, rate, n=24, hold=4.0):
    """A pace-lap walk against a field whose leader moves at `rate` laps a second from `lead_start` (lap + pct).
    Three classes of n/3 cars, the grid in order. Returns the cars visited, the times, and the director."""
    third = n // 3
    rows, cars = [], []
    for i in range(n):
        cls = i // third
        rows.append({"pos": i + 1, "classPos": i % third + 1, "idx": i, "derived": {"row": i // 2 + 1}})
        cars.append({"idx": i, "num": str(100 + i), "classId": cls, "pct": 0.0, "lap": 0, "pos": i + 1})
    sess = {"grid": {"rows": rows}, "cameras": [{"num": 1, "name": "Chase"}, {"num": 2, "name": "Rear Chase"}]}
    t = [0.0]
    cuts = []
    d = Director(lambda num, g: cuts.append((int(num) - 100, g, t[0])) or {"ok": True}, rng=random.Random(1), clock=lambda: t[0])
    d.set_live(True)
    d.grid_hold_s = hold
    state = {"session": {"state": "warmup", "num": 2}}
    msg = {"cars": cars, "derived": {"isRace": True}}
    for k in range(0, 4000):
        t[0] = k * 0.1
        lead = lead_start + rate * t[0]
        cars[0]["lap"], cars[0]["pct"] = int(lead), lead - int(lead)
        state = {"session": {"state": "parade_laps" if t[0] > 0.5 else "warmup", "num": 2}}
        d.tick(sess, msg, state, [])
        if d.owner != "grid" and t[0] > 5:
            break
    return [c for c, _, _ in cuts], [at for _, _, at in cuts], d, third


def test_the_grid_walk_goes_by_rows_and_fits_the_pace_laps():
    # plenty of time (the field rolls over the line first, then a whole lap): every car, in order, at its hold
    seen, at, d, third = _walk_rig(lead_start=0.97, rate=1 / 300)
    assert seen == list(range(24)) and not d.grid_short
    # a lap of ~100 s from mid-lap: less than 24 x 4 s, so the holds shorten and every car is still seen in time
    seen, at, d, third = _walk_rig(lead_start=0.2, rate=1 / 100)
    eta = 0.8 * 100
    assert seen == list(range(24)) and not d.grid_short
    assert at[-1] < eta, "done before the leader reaches the line"
    # far too little: the grid board first (every car on one screen, the camera wide on the pole), then the front
    # rows and each class's pole row, nothing cut off by the green
    seen, at, d, third = _walk_rig(lead_start=0.6, rate=1 / 100)
    assert d._board == "done" and seen[0] == 0 and at[1] - at[0] >= GRID_BOARD_MIN_S
    seen, at = seen[1:], at[1:]
    assert d.grid_short and at[-1] < 40
    assert seen[:2] == [0, 1], "the front row"
    assert third in seen and 2 * third in seen, "each class's pole"
    assert seen == sorted(seen), "still down the grid in order"
    rows_seen = {i // 2 for i in seen}
    assert all(i in seen for r in rows_seen for i in (2 * r, 2 * r + 1)), "a row is never split"


def _page_fn(path, name):
    import re
    page = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "broadcast", path), encoding="utf-8").read()
    m = re.search(r"\n  function %s\(.*?\) \{[\s\S]*?\n  \}\n" % name, page)
    assert m, name
    return m.group(0)


def _node(js, stdin=""):
    import shutil
    import subprocess
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is not installed")
    r = subprocess.run([node, "-e", js], input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_league_tables_from_web_pages_links_and_by_hand():
    """Championship points however the league has them: a standings page (its best table, entities and all), a
    Google Sheet's share link (its CSV export), and drivers found by customer id, name or car number."""
    html = """<html><head><style>td{}</style></head><body><table><tr><td>Home</td><td>Rules</td></tr></table>
    <table class="standings"><thead><tr><th>Pos</th><th>Driver</th><th>Car #</th><th>Points</th></tr></thead>
    <tbody><tr><td>1</td><td><a href="/d/1">Jos&eacute; P&eacute;rez</a></td><td>7</td><td>120</td></tr>
    <tr><td>2</td><td>Smith,&nbsp;John</td><td>12</td><td>101</td></tr>
    <tr><td>3</td><td>Kim&nbsp;Park<br>(rookie)</td><td>3</td><td>88</td></tr></tbody></table></body></html>"""
    rows = _node(_page_fn("producer.html", "parseLeague") + "\nconsole.log(JSON.stringify(parseLeague(require('fs').readFileSync(0,'utf8'))));", html)
    assert [(r["pos"], r["num"], r["points"]) for r in rows] == [("1", "7", "120"), ("2", "12", "101"), ("3", "3", "88")]
    assert rows[1]["name"] == "Smith, John" and rows[2]["name"].startswith("Kim Park")
    links = _node(_page_fn("producer.html", "leagueLink") + """
console.log(JSON.stringify([
  leagueLink("https://docs.google.com/spreadsheets/d/AbC-123/edit#gid=456"),
  leagueLink("https://docs.google.com/spreadsheets/d/e/2PACX-x/pubhtml"),
  leagueLink("https://docs.google.com/spreadsheets/d/AbC-123/export?format=csv"),
  leagueLink(" https://league.example/standings.json ")]));""")
    assert links == ["https://docs.google.com/spreadsheets/d/AbC-123/export?format=csv&gid=456",
                     "https://docs.google.com/spreadsheets/d/e/2PACX-x/pub?output=csv",
                     "https://docs.google.com/spreadsheets/d/AbC-123/export?format=csv",
                     "https://league.example/standings.json"]
    table = [{"pos": "1", "name": "José Pérez", "num": "7", "points": "120", "uid": None},
             {"pos": "2", "name": "Smith, John", "num": "12", "points": "101", "uid": None},
             {"pos": "3", "name": "Ana Lopez", "num": "33", "points": "90", "uid": "555"},
             {"pos": "4", "name": "Someone Else", "num": "9", "points": "80", "uid": None}]
    found = _node(_page_fn("overlay.html", "leagueKeys") + _page_fn("overlay.html", "leagueRowFor") + "\nconst G = () => ({});\n" +
                  "const rows = %s;\nconsole.log(JSON.stringify([" % json.dumps(table) +
                  "{name: 'Jose Perez', num: 70}, {name: 'John Smith2', num: 1}, {name: 'A. Lopez', userId: 555},"
                  "{name: 'Renamed Driver', num: 9}, {name: 'Nobody', num: 99}].map(c => (leagueRowFor(c, rows) || {}).pos || null)));")
    assert found == ["1", "2", "3", "4", None]
    # names that differ only by their digits are different drivers ("Driver 03" is not "Driver 12")
    field = [{"pos": "1", "name": "Driver 12", "points": "200"}, {"pos": "2", "name": "Driver 03", "points": "193"}]
    found = _node(_page_fn("overlay.html", "leagueKeys") + _page_fn("overlay.html", "leagueRowFor") + "\nconst G = () => ({});\n" +
                  "const rows = %s;\nconsole.log(JSON.stringify([{name: 'Driver 03'}, {name: 'Driver 12'}, {name: 'Driver 05'}]" % json.dumps(field) +
                  ".map(c => (leagueRowFor(c, rows) || {}).pos || null)));")
    assert found == ["2", "1", None]


def test_the_relay_loads_a_league_link_only_from_a_public_address():
    """From a link: the relay fetches for the producer, never this PC or the home network (a co-host's link must
    not reach into the host's network), redirects checked the same way, and a size cap."""
    import http.server
    import threading
    import relay.server as rs

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/away":
                self.send_response(302)
                self.send_header("Location", "http://192.168.1.1/admin")
                self.end_headers()
                return
            body = ("Pos,Driver,Points\n1,Ana Lopez,101\n" if self.path != "/big" else "x" * (rs.FETCH_MAX_BYTES + 10)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = "http://127.0.0.1:%d" % srv.server_address[1]
        assert not rs.fetch_text(base + "/t.csv")["ok"], "this PC is not a public address"
        assert not rs._public_host("192.168.1.1") and not rs._public_host("localhost") and not rs._public_host("10.0.0.5")
        ok = rs.fetch_text(base + "/t.csv", public=lambda h: h == "127.0.0.1")
        assert ok["ok"] and "Ana Lopez" in ok["text"] and ok["contentType"].startswith("text/csv")
        assert not rs.fetch_text(base + "/away", public=lambda h: h == "127.0.0.1")["ok"], "a redirect home is refused"
        assert not rs.fetch_text(base + "/big", public=lambda h: True)["ok"]
        for bad in ("file:///etc/passwd", "ftp://x/y", "", None, "https://"):
            assert not rs.fetch_text(bad)["ok"], bad
    finally:
        srv.shutdown()
    src = open(rs.__file__, encoding="utf-8").read()
    assert 'only the producer token can load a link' in src and "run_in_executor(None, fetch_text" in src


def test_the_grid_walk_by_hand():
    """Previous, Next, Hold and a jump to any car, from the producer; a walk by hand starts on pole and waits,
    and is never shortened to fit the pace laps. Nothing works without a walk running."""
    third = 4
    rows, cars = [], []
    for i in range(12):
        rows.append({"pos": i + 1, "classPos": i % third + 1, "idx": i, "derived": {"row": i // 2 + 1}})
        cars.append({"idx": i, "num": str(100 + i), "classId": i // third, "pct": 0.5, "lap": 0, "pos": i + 1})
    sess = {"grid": {"rows": rows}, "cameras": [{"num": 1, "name": "Chase"}, {"num": 2, "name": "Rear Chase"}]}
    t, cuts = [0.0], []
    d = Director(lambda num, g: cuts.append(int(num) - 100) or {"ok": True}, rng=random.Random(1), clock=lambda: t[0])
    msg, st = {"cars": cars, "derived": {"isRace": True}}, {"session": {"state": "warmup", "num": 2}}
    assert not d.grid_next() and not d.grid_prev() and not d.grid_jump(3) and not d.grid_pause(True)

    def run(seconds):
        for _ in range(int(seconds * 10)):
            t[0] += 0.1
            d.tick(sess, msg, st, [])
    d.set_live(True)
    d.start_grid_walk(cars, hold_s=3, manual=True)
    run(20)
    assert cuts == [0] and d.status()["gridPaused"] and d.status()["gridAt"] == 0, "on pole, waiting"
    assert d.grid_next(); run(0.3)
    assert d.grid_next(); run(0.3)
    assert cuts[-2:] == [1, 2] and d.status()["gridAt"] == 2
    assert d.grid_prev(); run(0.3)
    assert cuts[-1] == 1
    assert d.grid_jump(9); run(0.3)
    assert cuts[-1] == 9 and d.current["gridPos"] == 10
    assert not d.grid_jump(99)
    assert d.grid_pause(False); run(3.5)                           # carries on by itself from there
    assert cuts[-1] == 10
    assert d.grid_pause(True); n = len(cuts); run(10)
    assert len(cuts) == n, "held"
    d.grid_next(); run(0.3); assert cuts[-1] == 11
    assert d.grid_next() and d.owner != "grid", "Next past the last car ends the walk"
    # by hand, the pace laps never shorten it
    d.start_grid_walk(cars, hold_s=3, manual=True)
    st = {"session": {"state": "parade_laps", "num": 2}}
    for k in range(40):
        t[0] += 0.1
        cars[0]["pct"] = 0.5 + 0.02 * k
        d.tick(sess, msg, st, [])
    for _ in range(11):
        d.grid_next(); run(0.2)
    assert not d.grid_short and cuts[-12:] == list(range(12))


def test_the_bridge_takes_the_walk_by_hand_commands():
    src = open(os.path.join(os.path.dirname(__file__), "..", "pitwall", "bridge.py"), encoding="utf-8").read()
    for a in ("grid_next", "grid_prev", "grid_pause", "grid_jump"):
        assert '"%s"' % a in src
    assert 'manual=bool(msg.get("manual"))' in src
    prod = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "producer.html"), encoding="utf-8").read()
    assert 'action: "grid_jump"' in prod and 'ArrowRight: "next"' in prod and 'data-gwgo="hand"' in prod


# --------------------------------------------------------------------- the director's craft

def _craft_rig(n=6, battles=None, traffic=None, caution=False, style="cinematic"):
    cams = [{"num": i + 1, "name": nm} for i, nm in enumerate(
        ["TV1", "Chase", "Gyro", "Rear Chase", "Blimp", "Cockpit", "Far Chase"])]
    sess = {"cameras": cams, "grid": {"rows": []}}
    cars = [{"idx": i, "num": str(10 + i), "name": "Driver %02d" % i, "pos": i + 1, "classPos": i + 1, "classId": 1,
             "lap": 5, "pct": 0.1 * i, "surface": "on_track", "derived": {}} for i in range(n)]
    msg = {"cars": cars, "derived": {"isRace": True, "battles": battles or [], "classTraffic": traffic or [],
                                     "caution": {"active": caution}, "race": {"lapsToGo": 10}}}
    t = [100.0]
    log = {"cuts": [], "replay": []}

    def cut(num, g):
        log["cuts"].append((int(num) - 10, g, t[0]))
        return {"ok": True}

    def replay(cmd):
        log["replay"].append((cmd, t[0]))
        return {"ok": True}
    d = Director(cut, rng=random.Random(3), clock=lambda: t[0], replay=replay)
    d.set_live(True)
    d.configure(on=True, style=style)
    kinds = {c["num"]: group_kind(c["name"]) for c in cams}
    return d, sess, msg, t, log, kinds


def _state(t_live):
    return {"session": {"state": "racing", "num": 2}, "derived": {"liveEdge": {"atLive": True, "tLive": t_live}}}


def test_the_director_replays_a_moment_in_a_quiet_stretch_with_the_stinger():
    d, sess, msg, t, log, kinds = _craft_rig()
    contact = {"kind": "race_incident", "what": "contact", "idx": 3, "t": 470.0, "replayT": 470.0, "endT": 474.0,
               "sessionNum": 2, "id": 9, "confidence": "high"}
    stingers = []
    for k in range(400):                                   # 40 s
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [contact])
        if d.stinger and (not stingers or stingers[-1] != d.stinger):
            stingers.append(dict(d.stinger))
    jumps = [c for c, _ in log["replay"] if c.get("action") == "jump"]
    lives = [c for c, _ in log["replay"] if c.get("action") == "search"]
    assert len(jumps) == 1 and jumps[0]["timeS"] == 470.0 and jumps[0]["car"] == "13" and jumps[0]["session"] == 2
    assert [s["dir"] for s in stingers] == ["toReplay", "toLive"], "the stinger in and out"
    assert len(lives) == 1 and lives[0]["mode"] == "to_end", "and back to live"
    t_jump = next(at for c, at in log["replay"] if c.get("action") == "jump")
    t_live = next(at for c, at in log["replay"] if c.get("action") == "search")
    assert 8.0 <= t_live - t_jump <= 19.0
    assert d.rp is None and d.owner == "race"


def test_a_replay_waits_for_quiet_and_cuts_back_for_big_news():
    hot = [{"id": "1:0", "cars": [0, 1], "gapsS": [0.2], "closingSPerLap": [0.2], "forClassPos": 1}]
    d, sess, msg, t, log, kinds = _craft_rig(battles=hot)
    contact = {"kind": "race_incident", "what": "spin", "idx": 4, "t": 470.0, "replayT": 470.0, "sessionNum": 2, "id": 3}
    for k in range(300):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [contact])
    assert not log["replay"], "a fight for the lead on air: no replay"
    # under a caution the battle is not news, and the replay runs; a new leader live brings it back early
    d, sess, msg, t, log, kinds = _craft_rig(battles=hot, caution=True)
    for k in range(60):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [contact])
    assert any(c.get("action") == "jump" for c, _ in log["replay"])
    lead = {"kind": "class_lead", "idx": 2, "prevIdx": 0, "t": 507.0, "replayT": 502.0, "sessionNum": 2, "classId": 1}
    at = t[0]
    for k in range(30):
        t[0] += 0.1
        d.tick(sess, msg, _state(507.5 + k * 0.1), [contact, lead])
    out = [a for c, a in log["replay"] if c.get("action") == "search"]
    assert out and out[0] - at < 2.5, "back to live at once for the new leader"


def test_shots_come_in_an_order_and_a_close_fight_is_held():
    d, sess, msg, t, log, kinds = _craft_rig(n=3)
    d.hold_s = 3
    for k in range(400):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [])
    shots = [kinds[g] for _, g, _ in log["cuts"]]
    groups = [g for _, g, _ in log["cuts"]]
    seq = ["far", "trackside", "chase", "aerial", "far", "gyro", "trackside", "onboard"]   # the cinematic order
    # every leader story walks the cinematic order from its start, skipping only the shot already on air
    i = 0
    for a in shots:
        while seq[i % len(seq)] != a:
            i += 1
            assert i < 400, shots
        i += 1
    assert "rear" not in shots, "a car on its own is never shot looking backwards"
    assert shots.count("far") == max(shots.count(k) for k in set(shots)), "Far Chase is the main shot"
    assert all(a != b for a, b in zip(groups, groups[1:])), "never the same camera twice in a row"
    # side by side: the shot stays
    close = [{"id": "1:0", "cars": [0, 1], "gapsS": [0.15], "closingSPerLap": [0.05], "forClassPos": 1}]
    d, sess, msg, t, log, kinds = _craft_rig(battles=close)
    d.hold_s = 3
    for k in range(200):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [])
    times = [at for _, _, at in log["cuts"]]
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert gaps and min(gaps) >= 2.0 and max(gaps) >= 8.0, "held through the side by side, 8 s more at most"


def test_the_director_finds_traffic_charges_and_fastest_laps():
    traffic = [{"idx": 0, "classId": 1, "aheadIdx": 5, "aheadClassId": 2, "inS": 3.0, "packN": 4, "packIdx": [5]}]
    d, sess, msg, t, log, kinds = _craft_rig(traffic=traffic)
    for k in range(30):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [])
    assert d.segment and d.segment["key"] == "traffic:0" and kinds[log["cuts"][-1][1]] == "far"
    d, sess, msg, t, log, kinds = _craft_rig()
    fl = {"kind": "fastest_lap", "idx": 4, "t": 499.0, "lapS": 90.1, "overall": True}
    for k in range(10):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), [fl])
    assert d.segment["key"] == "fastlap:4"
    d, sess, msg, t, log, kinds = _craft_rig()
    msg["cars"][5]["derived"]["posGained"] = 9
    passes = [{"kind": "pass", "idx": 5, "t": 480.0 + i, "replayT": 480.0 + i, "sessionNum": 2, "classPos": 6 - i} for i in range(2)]
    for k in range(10):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), passes)
    stories = {s["key"] for s in d._stories(msg["cars"], {c["idx"]: c for c in msg["cars"]}, msg, passes, 500.0, t[0])}
    assert "charge:5" in stories


def test_booth_notes_are_kept_by_the_relay():
    """Booth notes (broadcast/notes.html): one per driver by iRacing user id and one for the show, written with the
    producer token, kept on disk (there again next race), never listed or served as an upload, never to guests."""
    import tempfile
    import relay.server as relay_mod
    d = tempfile.mkdtemp()
    r = relay_mod.Relay("B", "V", None, producer_token="P", assets_dir=d)
    assert r.notes == {"drivers": {}, "show": ""}
    assert r.apply_notes({"drivers": {"300000": "Say it AN-drew"}, "show": "Break at lap 10"})["ok"]
    assert r.apply_notes({"drivers": {"300001": "x" * 5000}})["ok"]
    assert len(r.notes["drivers"]["300001"]) == relay_mod.NOTE_MAX_CHARS
    assert r.apply_notes({"drivers": {"300001": None}})["ok"] and "300001" not in r.notes["drivers"]
    assert r.apply_notes({"drivers": {"300000": "   "}})["ok"] is True and "300000" not in r.notes["drivers"]
    r.apply_notes({"drivers": {"300000": "Say it AN-drew"}})
    for bad in ({"drivers": {"../x": "a"}}, {"drivers": {"12": 5}}, {"gfx": {}}, "text", {"show": 3}):
        assert not r.apply_notes(bad)["ok"], bad
    again = relay_mod.Relay("B", "V", None, producer_token="P", assets_dir=d)
    assert again.notes == {"drivers": {"300000": "Say it AN-drew"}, "show": "Break at lap 10"}
    assert all(a["name"] != ".booth_notes.json" for a in again._asset_list())
    assert relay_mod.safe_asset_name(".booth_notes.json") is None
    assert relay_mod.BROADCAST_PAGES["/broadcast/notes.html"] == "notes.html"
    src = open(os.path.join(os.path.dirname(__file__), "..", "relay", "server.py"), encoding="utf-8").read()
    snap = src.split("def _queue_snapshot")[1].split("def _gfx_message")[0]
    assert "if not guest:" in snap and "_notes_message()" in snap.split("if not guest:")[1]
    assert 'only the producer token can write notes' in src


def test_booth_notes_page_shows_only_feed_numbers():
    """The notes page puts words around numbers the feed already carries; it works none out (CLAUDE.md, hard rule)."""
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "notes.html"), encoding="utf-8").read()
    for key in ("posGained", "gridPos", "lapsLedSeen", "nextStopLapEst", "lapsThisStint", "closingSPerLap", "stopsSeen"):
        assert key in page
    # no arithmetic on two race values in the page: only unit conversions (speed) and sorting (a comparator orders
    # the cars, it shows nothing)
    import re
    page = re.sub(r"\.sort\(\(a, b\) => [^)]*\)", ".sort()", page)
    assert not re.search(r"(bestLapS|lastLapS|gapsS\[[^\]]*\]|pos)\s*-\s*\w+\.(bestLapS|lastLapS|pos)\b", page)


def test_a_standing_start_walks_the_grid_while_the_cars_get_in():
    # Summit Point, Nov 2026: a standing start has no pace laps (get in car, 10 s of warm-up, green), and the cars
    # arrive on the grid one by one during "get in car". Once most are there: the grid board (every car's picture,
    # the camera wide), then a row at a time, quickly; a car still in the garage keeps its row card, camera wide.
    n = 6
    rows = [{"pos": i + 1, "classPos": i + 1, "idx": i, "derived": {"row": i // 2 + 1}} for i in range(n)]
    cars = [{"idx": i, "num": str(100 + i), "classId": 1, "pct": 0.0, "lap": 0, "pos": i + 1,
             "surface": "not_in_world"} for i in range(n)]
    sess = {"grid": {"rows": rows}, "options": {"StandingStart": "1"},
            "cameras": [{"num": 1, "name": "Chase"}, {"num": 2, "name": "Rear Chase"}, {"num": 3, "name": "Drone Cam"}]}
    t = [0.0]
    cuts, boards = [], []
    d = Director(lambda num, g: cuts.append((int(num) - 100, g, t[0])) or {"ok": True}, rng=random.Random(1), clock=lambda: t[0])
    d.set_live(True)
    msg = {"cars": cars, "derived": {"isRace": True}}
    state = {"session": {"state": "get_in_car", "num": 2}}
    for k in range(2):
        cars[k]["surface"] = "in_pit_stall"                 # a third of the grid: not yet
    for k in range(20):
        t[0] = k * 0.1
        d.tick(sess, msg, state, [])
    assert d.owner != "grid" and not cuts
    for k in (2, 3, 4):
        cars[k]["surface"] = "in_pit_stall"                 # five of six: the walk; car 5 still in the garage
    shown = []
    for k in range(600):
        t[0] = 2.0 + k * 0.1
        d.tick(sess, msg, state, [])
        st = d.status()
        if st["gridBoard"] and (not boards or boards[-1] != st["gridBoard"]["row"]):
            boards.append(st["gridBoard"]["row"])
        if st["owner"] == "grid" and not st["gridBoard"] and st["current"] and (not shown or shown[-1] != st["current"]["car"]):
            shown.append(st["current"]["car"])
        if d.owner != "grid" and cuts:
            break
    assert d.note.startswith("grid walk done") or d.owner != "grid"
    assert cuts[0][0] == 0 and cuts[0][1] == 3, "the board: wide (the drone), on the pole car"
    assert boards == [0, 1, 2, 3], "the board's light goes down the rows"
    assert shown == [0, 1, 2, 3, 4, 5], "then every car a row at a time, the late one too"
    late = [c for c in cuts if c[2] >= cuts[-1][2] - 0.01]
    assert late[-1][0] == 4 and late[-1][1] == 3, "car 5 not out: the camera wide on its row mate"
    walk_t = [at for _, _, at in cuts]
    assert walk_t[-1] - walk_t[1] <= 6 * 2.5 + 0.5, "quick holds: a row in 5 s"
    # a rolling start keeps the pace-lap walk: nothing during "get in car"
    cuts.clear()
    d = Director(lambda num, g: cuts.append(int(num) - 100) or {"ok": True}, rng=random.Random(1), clock=lambda: t[0])
    d.set_live(True)
    for c in cars:
        c["surface"] = "in_pit_stall"
    for k in range(50):
        t[0] = 200.0 + k * 0.1
        d.tick(dict(sess, options={"StandingStart": "0"}), msg, state, [])
    assert d.owner != "grid"
    # the producer can turn the board off: straight to the cars
    d = Director(lambda num, g: cuts.append(int(num) - 100) or {"ok": True}, rng=random.Random(1), clock=lambda: t[0])
    d.set_live(True)
    d.configure(grid_board="never")
    for k in range(5):
        t[0] = 300.0 + k * 0.1
        d.tick(sess, msg, state, [])
    assert d.owner == "grid" and d.status()["gridBoard"] is None and d.current["car"] == 0


def test_replays_stay_in_their_session_and_show_a_moment_once():
    d, sess, msg, t, log, kinds = _craft_rig()
    by = {c["idx"]: c for c in msg["cars"]}
    quali_spin = {"kind": "race_incident", "what": "spin", "idx": 2, "t": 470.0, "replayT": 470.0, "sessionNum": 1}
    lead = {"kind": "class_lead", "idx": 3, "prevIdx": 1, "t": 471.0, "replayT": 466.0, "sessionNum": 2, "classId": 1}
    pas = {"kind": "pass", "idx": 3, "passedIdx": [1], "t": 470.5, "replayT": 466.5, "sessionNum": 2, "classPos": 1}
    d._keep_moments([quali_spin, lead, pas], _state(500.0))
    key, e = d._replay_candidate(by, 500.0, snum=2)
    assert e is lead, "this session's moment, never qualifying's spin on another clock"
    d._replayed.add(key)
    assert d._replay_candidate(by, 500.0, snum=2) is None, "the pass that made the lead change is the same moment"
    assert d._replay_candidate(by, 500.0, snum=1)[1] is quali_spin


def test_the_opening_lap_is_one_story_and_a_pass_in_a_fight_stays_with_the_fight():
    fight = [{"id": "3:2", "cars": [2, 3], "gapsS": [0.4], "closingSPerLap": [0.0], "forClassPos": 3}]
    d, sess, msg, t, log, kinds = _craft_rig(battles=fight)
    for c in msg["cars"]:
        c["lap"] = 1
    pas = [{"kind": "pass", "idx": 3, "passedIdx": [2], "t": 499.0 + i, "replayT": 499.0, "sessionNum": 2,
            "classPos": 3} for i in range(3)]
    for k in range(300):
        t[0] += 0.1
        d.tick(sess, msg, _state(500.0 + k * 0.1), pas)
    by = {c["idx"]: c for c in msg["cars"]}
    stories = d._stories(msg["cars"], by, msg, pas, 500.0, t[0])
    keys = [s["key"] for s in stories]
    assert "start" in keys and not any(k.startswith("pass:") for k in keys), keys
    assert d.segment["key"] == "start" and not log["replay"], "lap one: the start on air, no replays"
    assert {kinds[g] for _, g, _ in log["cuts"]} <= {"aerial", "trackside", "rear", "far"}
    msg["cars"][0]["lap"] = 2
    assert "start" not in [s["key"] for s in d._stories(msg["cars"], by, msg, pas, 500.0, t[0])]


def test_starting_grid_rows_and_camera_names_from_the_summit_point_race():
    from pitwall.normalize import grid_per_row
    assert grid_per_row("2x2 inline pole on left") == 2
    assert grid_per_row("1x1") == 1 and grid_per_row("3x3 staggered") == 3 and grid_per_row(None) == 2
    assert grid_per_row("9x9") == 2 and grid_per_row("single file") == 2
    assert group_kind("Drone Cam") == "aerial" and group_kind("Scenic Drone Cam") == "never"
    assert group_kind("Gearbox") == "detail" and group_kind("LF Susp") == "detail"


def test_the_grid_board_is_its_own_screen_and_the_walk_card_waits_for_it():
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    walk = page.split("const gridWalkCar = () => {")[1][:300]
    assert "d.gridBoard" in walk, "no walk card or grid beside it while the board is up"
    assert "renderGBoard," in page.split("const RENDERS = [")[1].split("];")[0] and 'id="gboard"' in page
    board = page.split("function renderGBoard() {")[1].split("\n  }\n")[0]
    assert "liveryHtml(c)" in board and "poleGapS" in board and "not_in_world" in board
    assert not re.search(r"timeS\s*[-+]", board), "gaps come from normalize, not worked out here"
    prod = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "producer.html"), encoding="utf-8").read()
    assert 'data-gwboard="${k}"' in prod and "gridBoard: ds.gwboard" in prod


def test_auto_graphics_use_every_graphic_but_picture_in_picture():
    # "Pick graphics for me" covers every tile the producer has (Nov 2026: it only ever used half of them). Picture in
    # picture stays the producer's: it needs their video link.
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    prod = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "producer.html"), encoding="utf-8").read()
    groups = re.findall(r'\["(\w+)", \[([^\]]*)\]\]', prod.split("const TILE_GROUPS = [")[1].split("]];")[0] + "]]")
    tiles = {t for g, names in groups if g != "Always" for t in re.findall(r'"(\w+)"', names)}
    auto = page.split("function autoWants(name) {")[1].split("\n  }\n")[0]
    ctx = page.split("const CONTEXTUAL = {")[1].split("};")[0]
    handled = set(re.findall(r'name [!=]== "(\w+)"', auto)) | set(re.findall(r'(\w+): \(\)', ctx))
    assert tiles - handled == {"pip"}, tiles - handled
    for timed in ("board", "map", "strip", "sponsors", "league"):
        assert re.search(r'name === "%s"\) return[^;]*every\(' % timed, auto), timed    # now and then, staggered


@pytest.mark.parametrize("page", ["overlay.html", "producer.html", "notes.html"])
def test_broadcast_pages_scripts_parse(page):
    # A duplicate declaration stops the whole page (Nov 2026: a second `now` in autoWants left the overlay blank but
    # for one graphic); nothing else in the suite runs these scripts end to end.
    src = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", page), encoding="utf-8").read()
    scripts = re.findall(r"<script>([\s\S]*?)</script>", src)
    assert scripts
    out = _node("const fs=require('fs');const s=JSON.parse(fs.readFileSync(0,'utf8'));const e=[];"
                "for(const x of s){try{new Function(x)}catch(err){e.push(err.message)}}console.log(JSON.stringify(e))",
                stdin=json.dumps(scripts))
    assert out == [], out


def test_race_laps_never_go_back_and_the_clock_does_not_flash():
    # Nov 2026: "back on lap 1" after the app restarted while iRacing showed a replay (whose frame carries the CarIdx
    # laps of that moment); the banner's clock rolled in every second, a flash a second.
    from pitwall.normalize import race_length
    sess = {"sessions": [{"SessionNum": 2, "SessionLaps": "unlimited", "SessionTime": "720.0000 sec"}]}
    lead = [{"idx": 3, "lapCompleted": 0, "lap": 1}]
    results = [{"Position": 1, "CarIdx": 3, "LapsComplete": 7}, {"Position": 2, "CarIdx": 4, "LapsComplete": 7}]
    d = Deriver()
    r = race_length(sess, 2, lead, 300.0, d, [], results=results)
    assert r["leaderLapsDone"] == 7, "the server's results are a floor"
    r = race_length(sess, 2, lead, 290.0, d, [], results=[])
    assert r["leaderLapsDone"] == 7, "and so is the most this session has shown"
    r = race_length(sess, 3, lead, 290.0, d, [], results=[])
    assert r["leaderLapsDone"] == 0, "a new session starts again"
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    banner = page.split("function renderBanner() {")[1].split("\n  }\n")[0]
    assert 'roll($("bnMain"), rs.main)' in banner and "\\d+:\\d\\d" in banner, "a ticking clock is not rolled"
    br = open(os.path.join(os.path.dirname(__file__), "..", "pitwall", "bridge.py"), encoding="utf-8").read()
    assert 'race["timeRemainS"] = rem' in br, "the clock keeps going while a replay holds the picture"


def test_the_flags_are_called_on_screen():
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    fc = page.split("function renderFlagCall() {")[1].split("\n  }\n")[0]
    for words in ("Green flag", "White flag", "Checkered flag", "wins", "Racing resumes"):
        assert words in fc, words
    assert "FC.f !== undefined" in fc, "the first state seen only sets the scene"
    assert 'shown("flagcall")' in fc and "renderFlagCall," in page.split("const RENDERS = [")[1].split("];")[0]


def test_white_and_checkered_without_the_flag_bits():
    # A spectator's SessionFlags never carried white or checkered at Summit Point (green at the start, then nothing).
    from pitwall.normalize import race_flags
    assert race_flags(["start_hidden"], "racing", 120.0, True) == ["start_hidden"]
    assert "white" in race_flags(["start_hidden"], "racing", 0.0, True), "a timed race's clock ran out: the last lap"
    assert "white" not in race_flags(["start_hidden"], "racing", 0.0, False), "not a timed race: the clock means nothing"
    assert "white" in race_flags([], "racing", None, False, laps_to_go=1)
    f = race_flags(["start_hidden"], "checkered", 0.0, True)
    assert "checkered" in f and "white" not in f
    assert race_flags(["checkered"], "racing", 0.0, True).count("white") == 1, "iRacing's early checkered is still the white"


def test_what_a_spectator_is_not_sent_is_filled_in_everywhere():
    # Summit Point (spectating): no white or checkered bits, and after the start no racing flag at all. The race's
    # own reading must reach race control, the race summary (the director's last lap), PitWall's header and the
    # caution reading; a timed race's last laps count as last laps too.
    from pitwall.normalize import race_length
    d = Deriver()
    d.state_events(100.0, {"flags": ["start_hidden"], "state": "racing", "raceFlags": ["start_hidden"]}, {}, {})
    d.state_events(101.0, {"flags": ["start_hidden"], "state": "racing", "raceFlags": ["start_hidden", "white"]}, {}, {})
    d.state_events(130.0, {"flags": ["start_hidden"], "state": "checkered", "raceFlags": ["start_hidden", "checkered"]}, {}, {})
    assert [e["flag"] for e in d.feed_events if e["kind"] == "flag"] == ["white", "checkered"]
    sess = {"sessions": [{"SessionNum": 2, "SessionLaps": "unlimited", "SessionTime": "720.0000 sec"}]}
    # Summit Point: the clock ran out (white), SessionState turned checkered 24 s before the leader reached the line
    # (still white), and the checkered came with the leader's crossing
    dd = Deriver()
    r = race_length(sess, 2, [{"idx": 1, "lapCompleted": 8, "lap": 9}], 0.0, dd, ["start_hidden"], state="racing")
    assert r["white"] and not r["checkered"]
    r = race_length(sess, 2, [{"idx": 1, "lapCompleted": 8, "lap": 9}], 125.0, dd, ["start_hidden"], state="checkered")
    assert r["white"] and not r["checkered"] and r["finished"] is False
    r = race_length(sess, 2, [{"idx": 1, "lapCompleted": 9, "lap": 10}], 124.0, dd, ["start_hidden"], state="checkered")
    assert r["checkered"] and not r["white"] and r["finished"] is True
    r = race_length(sess, 2, [{"idx": 1, "lapCompleted": 0, "lap": 0}], 0.0, Deriver(), ["start_hidden"], state="warmup")
    assert not r["white"], "the clock at 0 before the start is not a last lap"
    client = open(os.path.join(os.path.dirname(__file__), "..", "client", "index.html"), encoding="utf-8").read()
    assert "st.derived && st.derived.raceFlags" in client.split("function renderHeader() {")[1][:1200]
    src = open(os.path.join(os.path.dirname(__file__), "..", "pitwall", "director.py"), encoding="utf-8").read()
    assert 'race.get("lapsByTime")' in src, "no replays in a timed race's last laps either"


def test_a_caution_is_read_from_the_field_when_no_racing_flag_comes():
    from pitwall.normalize import Deriver as D
    d = D()
    cars = [{"idx": i, "onPitRoad": False, "surface": "on_track", "bestLapS": 90.0, "derived": {"speedMs": 10.0}} for i in range(8)]
    t = 0.0
    for k in range(200):
        t += 0.5
        out = d.caution(cars, ["start_hidden"], True, 2000.0, t, state="racing")
    assert out["active"] and out["inferred"], "a spectator's flags (start lights only): the field's speed says caution"
    d = D()
    for k in range(200):
        out = d.caution(cars, ["green"], True, 2000.0, k * 0.5, state="racing")
    assert not out["inferred"], "a real flag is believed"


def test_race_tv_extras_from_our_own_measures():
    # Pit stops (live, with where the car should come out, and just finished), who is closing in, recent race pace
    # against the class leader, the lap chart and a battle's history: none of it is in iRacing's data.
    src = FakeSource(realtime=False, spectator=True, incidents=False)
    src.connect()
    d, sess = Deriver(), build_session(src)
    live, done, paced, chart = None, None, False, None
    for k in range(60 * 760):
        src.tick()
        if k % 8:
            continue
        build_state(src, d, sess)
        cm = build_cars(src, sess, d)
        dv = cm["derived"]
        for p in dv["pitStops"]:
            if not p["done"] and p.get("rejoin") and live is None:
                live = p
            if p["done"] and done is None and live and p["idx"] == live["idx"]:
                done = p
        by = {c["idx"]: c for c in cm["cars"]}
        for c in dv["closing"]:
            k = d.gap_scale(by[c["idx"]].get("classId"))       # in that class's own seconds (class_scale)
            assert 0.1 * k <= c["closingSPerLap"] <= 3.0 * k and 1.0 * k - 0.005 <= c["gapS"] <= 8.0 * k + 0.005  # gapS is rounded
        paced = paced or any(c["derived"].get("recentPaceVsLeaderS") is not None for c in cm["cars"])
        chart = dv["lapChart"]
        if done and paced:
            break
    assert live and live["fromClassPos"] and live["rejoin"]["classPos"] >= 1 and live["laneS"] >= 0
    assert done and done["stationaryS"] > 0 and done["fromClassPos"] == live["fromClassPos"] and done["classPos"]
    assert paced
    leaders = [c for c in cm["cars"] if c.get("classPos") == 1 and c["derived"].get("recentPaceS")]
    assert all(c["derived"]["recentPaceVsLeaderS"] == 0 for c in leaders)
    some = next(iter(chart.values()))
    assert some[0][0] >= 1 and all(len(x) == 3 for x in some)
    # a battle's swaps survive the battle's id changing
    d2 = Deriver()
    g = [{"cars": [1, 2], "id": "1:1"}]
    d2.battle_history(g, 10.0)
    g = [{"cars": [2, 1], "id": "1:2"}]
    d2.battle_history(g, 40.0)
    g = [{"cars": [1, 2], "id": "1:1"}]
    d2.battle_history(g, 70.0)
    assert g[0]["swaps"] == 2 and g[0]["sinceS"] == 60.0


def test_race_tv_graphics_show_only_normalize_numbers():
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    for fn in ("renderPitStop", "renderClosing", "renderPace", "renderStrategy", "renderLapChart"):
        assert "function %s() {" % fn in page and fn + "," in page.split("const RENDERS = [")[1].split("];")[0], fn
    body = page.split("// ---------------------------------------------------------------- race TV extras")[1].split("// ---------------------------------------------------------------- flag calls")[0]
    for field in ("pitStops", "closing", "recentPaceS", "recentPaceVsLeaderS", "stopDue", "lapChart"):
        assert field in body, field
    assert "b.swaps" in page and "b.sinceS" in page
    board = page.split("function renderBoard() {")[1].split("\n  }\n")[0]
    assert '"Stops", "Led"' in board, "the full result at the finish"


def test_what_the_summit_point_audit_found():
    from pitwall.normalize import gap_between, race_flags
    # a battle is read from the live gap, not a stale F2Time (two cars 8 s apart were "within a second")
    d = Deriver()
    a = {"idx": 1, "classId": 1, "classPos": 4, "pos": 4, "gapValid": True, "f2TimeS": 10.0, "onPitRoad": False,
         "derived": {"liveClassIntervalS": None}}
    b = {"idx": 2, "classId": 1, "classPos": 5, "pos": 5, "gapValid": True, "f2TimeS": 10.9, "onPitRoad": False,
         "derived": {"liveClassIntervalS": 7.917, "classIntervalLaps": 0}}
    assert d.battles([a, b], {2: 1}, True) == []
    b["derived"]["liveClassIntervalS"] = 0.6
    assert d.battles([a, b], {2: 1}, True)[0]["gapsS"] == [0.6]
    # a car that passed since the order was scored is not "+1:17.0" behind (read round the lap)
    ahead = {"lap": 9, "pct": 0.50, "classId": 1, "estTimeS": 40.0, "classEstLapS": 85.0}
    passed = {"lap": 9, "pct": 0.51, "classId": 1, "estTimeS": 41.0, "classEstLapS": 85.0}
    assert gap_between(ahead, passed) == (None, None)
    # the checkered comes with the leader's crossing, not with SessionState (24 s early at Summit Point)
    assert race_flags(["start_hidden"], "checkered", 125.0, True, None, False) == ["start_hidden", "white"]
    assert race_flags(["start_hidden"], "checkered", 125.0, True, None, True) == ["start_hidden", "checkered"]
    # after the flag each car's gap is held as it finishes; one still on its last lap keeps iRacing's
    d = Deriver()
    cars = [{"idx": 0, "lapCompleted": 9, "f2TimeS": 0.0, "classId": 1, "derived": {}},
            {"idx": 1, "lapCompleted": 9, "f2TimeS": 7.38, "classId": 1, "derived": {}},
            {"idx": 2, "lapCompleted": 9, "f2TimeS": 13.94, "classId": 1, "derived": {}}]
    d.finish_gaps(cars, None, 860.0)                    # racing: the laps are noted
    cars[0]["lapCompleted"] = 10
    d.finish_gaps(cars, "checkered", 865.6)             # the winner crosses
    cars[1]["lapCompleted"], cars[1]["f2TimeS"] = 10, 7.381
    d.finish_gaps(cars, "checkered", 873.0)             # P2 crosses
    assert cars[1]["derived"]["finished"] and cars[1]["derived"]["liveGapS"] == 7.381
    assert cars[2]["derived"]["liveGapS"] == 13.94 and not cars[2]["derived"].get("finished")
    assert cars[2]["derived"]["liveIntervalS"] == round(13.94 - 7.381, 3)
    # where-faster needs two laps that compare (1:36.0 against 1:24.9 said nothing)
    page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    assert "f.comparable !== false" in page
    # a car in the lane for minutes is not a pit stop to put on air
    assert Deriver.PIT_LONG_S <= 300
    # race news is on the race's lap while spectating
    d = Deriver()
    d._player_lap, d._lead_lap = None, 6
    d.event(1.0, "pass", idx=3)
    assert d.feed_events[-1]["lap"] == 6
def test_a_close_battle_keeps_the_air_to_its_end():
    # Summit Point, Nov 2026: battles left at SEGMENT_MAX_S while within 0.1 s, the pass for the lead a second later;
    # a spin in the battle cut away to a lone trackside shot; cars swapping places made it "another" battle.
    from pitwall.director import SEGMENT_MAX_S, BATTLE_HOT_MAX_S
    close = [{"id": "1:0", "cars": [0, 1], "gapsS": [0.2], "closingSPerLap": [0.0], "forClassPos": 1}]
    loose = [{"id": "5:4", "cars": [4, 5], "gapsS": [1.6], "closingSPerLap": [0.0], "forClassPos": 5}]
    d, sess, msg, t, log, kinds = _craft_rig(n=8, battles=close + loose)
    msg["cars"][6]["derived"]["posGained"] = 9                         # a charge elsewhere, to tempt it away
    passes = [{"kind": "pass", "idx": 6, "t": 480.0 + i, "replayT": 480.0 + i, "sessionNum": 2, "classPos": 7 - i} for i in range(2)]
    spin = {"kind": "race_incident", "what": "spin", "idx": 3, "t": 540.0, "replayT": 540.0, "sessionNum": 2, "id": 77, "confidence": "high"}
    keys = []
    for k in range(int((SEGMENT_MAX_S + 60) * 10)):
        t[0] += 0.1
        tl = 500.0 + k * 0.1
        d.tick(sess, msg, _state(tl), passes + ([spin] if tl >= 540.0 else []))
        keys.append(d.segment and d.segment["key"])
    assert all(k == "battle:1:0" for k in keys[200:]), set(keys[200:])
    assert not any(c == 3 for c, _, at in log["cuts"]), "a spin elsewhere waits for a replay"
    assert not log["replay"], "no replay over a close battle"
    # the cars swap places: a new id, the same story
    msg["derived"]["battles"] = [{"id": "0:1", "cars": [1, 0], "gapsS": [0.1], "closingSPerLap": [0.0], "forClassPos": 1}] + loose
    for k in range(300):
        t[0] += 0.1
        d.tick(sess, msg, _state(600.0 + k * 0.1), [])
    assert d.segment["key"] == "battle:0:1" and d.segment["start"] < t[0] - 60
    # a spin in it: the camera stays on the battle (held), no lone trackside shot
    n = len(log["cuts"])
    spin2 = {"kind": "race_incident", "what": "spin", "idx": 1, "t": 631.0, "replayT": 631.0, "sessionNum": 2, "id": 78, "confidence": "high"}
    for k in range(40):
        t[0] += 0.1
        d.tick(sess, msg, _state(631.0 + k * 0.1), [spin2])
    assert len(log["cuts"]) == n and d.segment["key"] == "battle:0:1"
    # it opens up: away within its usual time
    msg["derived"]["battles"] = [{"id": "0:1", "cars": [1, 0], "gapsS": [1.7], "closingSPerLap": [-0.2], "forClassPos": 1}] + loose
    for k in range(100):
        t[0] += 0.1
        d.tick(sess, msg, _state(640.0 + k * 0.1), [])
    assert d.segment["key"] != "battle:0:1"
    assert BATTLE_HOT_MAX_S > SEGMENT_MAX_S
def test_car_pictures_are_asked_for_gently_and_everything_asked_of_iracing_is_logged():
    # Nov 2026: iRacing closing to the desktop now and then with the app running, no error, no crash report. The
    # pictures go one at a time with a pause, none while you drive, none at all when switched off, no helmets; and
    # what the app asked of the sim is in sim_log.txt, so the last lines before "sim gone" say what it was doing.
    import time as _t
    from pitwall import simlog
    from pitwall.carrender import CarRenderer, HELMETS, RENDER_GAP_S
    assert HELMETS is False and RENDER_GAP_S >= 1.0
    calls, out = [], []

    def fetch(url):
        calls.append((_t.time(), url))
        return 200, "image/png", b"\x89PNG c"
    with tempfile.TemporaryDirectory() as d:
        simlog.set_path(os.path.join(d, "sim_log.txt"))
        try:
            r = CarRenderer(d, out.append, fetch=fetch, root=os.path.join(d, "paint"))
            r.gap_s = 0.3
            r.paused = True
            drivers = [{"CarPath": "gr86", "CarID": 5, "UserID": u, "TeamID": 0, "CarNumber": str(u), "HelmetDesignStr": "1,ffffff,ff0000,000000"}
                       for u in (1, 2, 3)]
            r.request(drivers)
            _t.sleep(0.4)
            assert not calls, "nothing while you are driving"
            r.paused, r.enabled = False, False
            r._wake.set(); _t.sleep(0.4)
            assert not calls, "nothing when switched off"
            r.enabled = True
            r._wake.set()
            deadline = _t.time() + 4
            while len(out) < 3 and _t.time() < deadline:
                _t.sleep(0.05)
            assert len(out) == 3 and all(m["kind"] == "render" for m in out), "no helmets"
            gaps = [b[0] - a[0] for a, b in zip(calls, calls[1:])]
            assert gaps and min(gaps) >= 0.25, "one at a time, with a pause"
            # a camera command reaching the sim is logged, and the sim going away
            class IR:
                def cam_switch_num(self, *a):
                    pass
            class Src:
                def irsdk(self):
                    return IR()
            from pitwall.commands import CommandGate
            res = CommandGate(camera=True).execute(Src(), {"cmd": "camera", "action": "car", "car": "7", "group": 3})
            log = open(simlog.path(), encoding="utf-8").read()
            assert log.count("car picture: gr86") == 3 and "helmet" not in log
            if res["ok"]:                      # pyirsdk is importable here
                assert "cam_switch_num ('7', 3, 0)" in log
        finally:
            simlog.set_path(None)


def test_the_producer_can_switch_car_pictures_off():
    prod = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "producer.html"), encoding="utf-8").read()
    assert 'action: "renders"' in prod and 'data-renders="1"' in prod
    src = open(os.path.join(os.path.dirname(__file__), "..", "pitwall", "bridge.py"), encoding="utf-8").read()
    assert 'if action == "renders":' in src and '"renders")):' in src     # also with the broadcast off


def test_one_graphic_failing_never_takes_the_overlay_off_air():
    # Nov 2026: the overlay showed nothing in OBS. Every graphic was drawn in one chain inside the frame loop, so one
    # that threw stopped every graphic after it and the loop itself (requestAnimationFrame was never asked again).
    src = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
    body = src.split("function frame(t) {")[1].split("\n  }")[0]
    assert body.strip().startswith("requestAnimationFrame(frame);"), "the next frame is asked for before anything can throw"
    assert "for (const r of RENDERS) safely(r);" in body
    listed = src.split("const RENDERS = [")[1].split("];")[0]
    assert listed.count("render") >= 30 and "ctxTick(); renderGrid();" not in src
    out = _node("""
      const RENDERS=[()=>{throw new Error('boom')},()=>{globalThis.ok=1}];const failed={};
      """ + src.split("const failed = {};")[1].split("function frame(t)")[0] + """
      for (const r of RENDERS) safely(r); console.log(JSON.stringify(globalThis.ok||0));""")
    assert out == 1, "a graphic after one that throws is still drawn"


def test_a_new_session_while_the_app_runs_is_seen():
    """Nov 2026: a race, then another at another track with the app still open, and the app went on with the
    first track. iRacing opens a new sim for each session and its session info counter starts again, so the
    counter alone can meet the old value. A new attach to the sim, or a new session id, rebuilds the session
    (track, map, drivers), and another event starts the derived history afresh."""
    import copy
    from pitwall.bridge import Bridge
    src = FakeSource(realtime=False, spectator=True)
    src.connect()
    with tempfile.TemporaryDirectory() as base:
        b = Bridge(src, "ws://127.0.0.1:1", "t", log=lambda *a: None, render_cars=False, livery_dir=base)
        b.camera_packs_dir = base
        src.tick()
        out = b._build(0.0)
        first = [m for m in out if m.get("type") == "session"][0]
        old_deriver = b.deriver
        src.tick()
        assert not [m for m in b._build(0.5) if m.get("type") == "session"], "nothing new, nothing sent"
        # the next session: a new sim (attached again), another track and event, the same update counter
        wk = copy.deepcopy(src._session["WeekendInfo"])
        wk["TrackID"] = (wk.get("TrackID") or 0) + 101
        wk["TrackDisplayName"] = "Another Raceway"
        wk["SubSessionID"] = (wk.get("SubSessionID") or 0) + 7
        src._session["WeekendInfo"] = wk
        src.epoch = getattr(src, "epoch", 0) + 1
        src.tick()
        out = b._build(1.0)
        sess = [m for m in out if m.get("type") == "session"]
        assert sess and sess[0]["track"]["TrackID"] == wk["TrackID"] != first["track"]["TrackID"]
        assert b._map_key[0] == wk["TrackID"], "the map follows the new track"
        assert b.deriver is not old_deriver, "the last event's history does not carry over"
        page = open(os.path.join(os.path.dirname(__file__), "..", "broadcast", "overlay.html"), encoding="utf-8").read()
        assert 'case "session": S.session = m; if (S.map && !mapFits(S.map)) S.map = null;' in page, \
            "the overlay drops the last track's outline"
