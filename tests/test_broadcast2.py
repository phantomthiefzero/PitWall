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
    d.start_grid_walk(order, hold_s=2, on_car=lambda c: seen.append(c["idx"]))
    chase = next(g["num"] for g in sess["cameras"] if g["name"] == "Chase")
    for step in range(20):
        clock[0] = step * 1.0
        d.tick(sess, cars, st, [])
    assert seen == [c["idx"] for c in order]
    assert {g for _, g, _ in cuts} == {chase}
    assert d.on is False and d.mode == "race"


# --------------------------------------------------------------------- OBS + liveries

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
