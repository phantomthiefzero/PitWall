"""The co-host's second camera (suite/second_camera.py): its message logic, and the whole loop
against a real relay and bridge on a fake spectated race."""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from suite.second_camera import SecondCamera, remote_from_link, CAM_SETTLE_S, HEARTBEAT_S  # noqa: E402


def test_links():
    assert remote_from_link("https://abc.trycloudflare.com/broadcast/producer.html?token=P1&view=V") == \
        ("wss://abc.trycloudflare.com/view?token=P1", "P1")
    assert remote_from_link("http://127.0.0.1:8765/broadcast/producer.html?token=x%2By") == \
        ("ws://127.0.0.1:8765/view?token=x%2By", "x+y")
    # a producer page served from elsewhere, pointing at the relay with ?relay=
    assert remote_from_link("https://h.example/broadcast/producer.html?relay=wss://r.example.com&token=T") == \
        ("wss://r.example.com/view?token=T", "T")
    for bad in ("", "not a link", "https://abc.trycloudflare.com/broadcast/producer.html", "ftp://x/?token=1"):
        assert remote_from_link(bad) is None


def _session():
    return {"type": "session", "drivers": [{"CarIdx": 3, "CarNumber": "12", "UserName": "Ana Lopez", "UserID": 99},
                                           {"CarIdx": 5, "CarNumber": "7", "UserName": "John Smith", "UserID": 98}],
            "cameras": [{"num": 9, "name": "Cockpit"}, {"num": 11, "name": "TV1"}]}


def test_reports_the_camera_car_once_it_holds():
    sc = SecondCamera(lambda: None, log=lambda *_: None)
    sc.remote_ok = True
    sc.on_local(_session(), now=0.0)
    assert sc.on_local({"type": "state", "full": True, "camera": {"carIdx": 3, "group": 11}}, now=1.0) == []
    assert sc.on_local({"type": "state", "camera": {"carIdx": 5}}, now=1.2) == [], "car 3 did not hold: never reported"
    out = sc.on_local({}, now=1.2 + CAM_SETTLE_S + 0.1)
    assert len(out) == 1
    cam = out[0]["set"]["pip"]["second"]["cam"]
    assert cam == {"idx": 5, "num": "7", "uid": 98, "name": "John Smith"} and out[0]["type"] == "gfx"
    assert sc.on_local({}, now=5.0) == []                                       # nothing new
    assert len(sc.on_local({}, now=5.0 + HEARTBEAT_S)) == 1, "a heartbeat, so the host knows it is alive"


def test_follows_the_hosts_picks_only_while_the_hosts_broadcast_is_on():
    sc = SecondCamera(lambda: None, log=lambda *_: None)
    sc.on_local(_session(), now=0.0)
    sc.on_local({"type": "state", "full": True, "camera": {"carIdx": 3, "group": 11}}, now=0.0)
    want = {"type": "gfx", "state": {"pip": {"want": {"num": "7", "group": "cockpit", "seq": 1}}}}
    assert sc.on_remote(want) == [], "the host's broadcast is off: held"
    out = sc.on_remote({"type": "director", "live": True})
    assert out == [{"type": "cmd", "cmd": "camera", "action": "car", "car": "7", "group": 9, "camera": 0}], \
        "the held pick, on this PC's own Cockpit group"
    assert sc.on_remote(want) == [], "the same pick is not repeated"
    out = sc.on_remote({"type": "gfx", "state": {"pip": {"want": {"num": "12", "group": "a shot this PC lacks", "seq": 2}}}})
    assert out[0]["car"] == "12" and out[0]["group"] == 11, "an unknown shot keeps this camera's own group"
    assert sc.on_remote({"type": "gfx", "state": {"pip": {}}}) == []


def test_hands_the_camera_over_and_back():
    sc = SecondCamera(lambda: None, log=lambda *_: None)
    hello = sc.hello_local()
    assert {"type": "cmd", "cmd": "director", "action": "broadcast", "on": True} in hello
    assert any(m.get("action") == "set" and m.get("on") is False and m.get("autoGrid") is False for m in hello)
    assert sc.goodbye_remote()["set"] == {"pip": {"second": None}}
    assert sc.start("not a link")["state"] == "error"


@pytest.mark.timeout(120)
def test_the_whole_loop_against_a_real_relay_and_bridge():
    """One suite plays both parts: the host (its relay, a producer asking for a car) and the co-host
    (the second camera, cutting this suite's own fake spectated sim and reporting the car back)."""
    import pitwall.sources as ps
    from suite.pitwall_host import PitWallHost
    import websockets.sync.client as wsc

    real = ps.make_source

    def make(kind, path=None, **kw):                       # only this test's own kind is changed
        if kind == "fake-second-camera-test":
            return ps.FakeSource(realtime=True, speed=4.0, spectator=True)
        return real(kind, path, **kw)

    tmp = tempfile.mkdtemp()
    host = PitWallHost("fake-second-camera-test", log=lambda *_: None, token_path=os.path.join(tmp, "t.json"))
    loop = asyncio.new_event_loop()
    ps.make_source = make
    try:
        started = threading.Event()

        def run():
            asyncio.set_event_loop(loop)

            async def boot():
                await host.start(18470)
                started.set()
                while True:
                    await asyncio.sleep(3600)
            try:
                loop.run_until_complete(boot())
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()
        assert started.wait(20) and host.port, host.error
    finally:
        ps.make_source = real
    link = "http://127.0.0.1:%d/broadcast/producer.html?token=%s&view=%s" % (host.port, host.producer_token, host.view_token)
    sc = SecondCamera(host.local_producer, log=lambda *_: None)
    with wsc.connect("ws://127.0.0.1:%d/view?token=%s" % (host.port, host.producer_token), open_timeout=10) as prod:
        gfx, session, director = {}, {}, {}

        def pump(until, timeout=30.0):
            end = time.time() + timeout
            while time.time() < end:
                if until():
                    return True
                try:
                    m = json.loads(prod.recv(timeout=0.5))
                except TimeoutError:
                    continue
                if m.get("type") == "gfx":
                    gfx.clear(); gfx.update(m.get("state") or {})
                elif m.get("type") == "session":
                    session.update(m)
                elif m.get("type") == "director":
                    director.update(m)
            return until()

        prod.send(json.dumps({"type": "resend"}))
        assert pump(lambda: session.get("drivers"))
        st = sc.start(link, "Co-host")
        assert st["state"] in ("connecting", "on"), st
        try:
            assert pump(lambda: sc.info()["state"] == "on")
            assert pump(lambda: director.get("live") is True), "the second camera turned this suite's switch on"
            assert pump(lambda: ((gfx.get("pip") or {}).get("second") or {}).get("cam")), "it reports its camera car"
            first = gfx["pip"]["second"]["cam"]
            target = next(d for d in session["drivers"] if d.get("CarNumber") not in (None, first.get("num"))
                          and not d.get("CarIsPaceCar") and not d.get("IsSpectator"))
            prod.send(json.dumps({"type": "gfx", "set": {"pip": {"want": {"num": str(target["CarNumber"]), "seq": 1}}}}))
            assert pump(lambda: ((gfx.get("pip") or {}).get("second") or {}).get("cam", {}).get("num") == target["CarNumber"]), \
                "the camera went to the car the host picked, and the caption knows"
        finally:
            sc.stop()
        assert pump(lambda: "second" not in (gfx.get("pip") or {})), "stopping hands the caption back"
        assert pump(lambda: director.get("live") is False), "and turns this suite's switch off again"
    loop.call_soon_threadsafe(loop.stop)
