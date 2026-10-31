"""Broadcast, part 2: starting grid, director, livery photos (OBS), assets.

Run: python -m pytest tests -q
"""
import asyncio
import base64
import importlib.util
import json
import os
import random
import sys
import tempfile
import threading
import pytest  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pitwall.commands import CommandGate  # noqa: E402
from pitwall.director import Director, group_kind  # noqa: E402
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
    assert group_kind("Rear Chase") == "rear" and group_kind("Chopper") == "aerial"
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
    chase = next(g["num"] for g in sess["cameras"] if g["name"] == "Chase")
    for step in range(20):
        clock[0] = step * 1.0
        d.tick(sess, cars, st, [])
    assert seen == [c["idx"] for c in order]
    assert {g for _, g, _ in cuts} == {chase}
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
    assert {g for _, g in walk} == {chase}
    walking = [at for at, o in owners if o == "grid"]
    assert walking and min(walking) < 21 and max(walking) < 20 + 4 * 25, "about 4 s a car, from the pace laps"
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
        r.RETRY_DOWN_S = 0.2
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
