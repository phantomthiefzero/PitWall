"""
iRacing Suite desktop app.

Opens the suite in its own window (Microsoft Edge WebView2, built into Windows 10/11)
with the iRacing bridge running inside the app. If the window can't open, it falls
back to the default browser plus a small "running" window.
"""
import argparse
import asyncio
import os
import sys
import threading
import time
import webbrowser

from suite import __version__
from suite.bridge import Hub, LiveSource, ReplaySource, start_server
from suite.pitwall_host import PitWallHost


def log_path():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "iRacingSuite")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "suite.log")


def redirect_output_when_windowed():
    """The packaged app has no console, so write messages to a log file instead."""
    if getattr(sys, "frozen", False) and (sys.stdout is None or not sys.stdout.isatty()):
        try:
            f = open(log_path(), "a", encoding="utf-8", buffering=1)
            sys.stdout = sys.stderr = f
        except Exception:
            pass


class Engine:
    """Runs the bridge's server and data pump on a background thread."""

    def __init__(self, rate=60, pitwall_source="live"):
        self.loop = asyncio.new_event_loop()
        self.hub = Hub(LiveSource(), rate)
        self.pitwall = PitWallHost(pitwall_source)
        self.hub.extra = lambda: {"pitwall": self.pitwall.info()}
        self.pitwall.analyzer_hub = self.hub
        self.updater = None          # set by main(); suite.updater.Updater
        self.port = None
        self.error = None
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self, port):
        self._want = port
        self._thread.start()
        self._ready.wait(15)
        if self.error:
            raise self.error
        if self.port is None:
            raise RuntimeError("The bridge did not start in time.")

    def _run(self):
        asyncio.set_event_loop(self.loop)

        async def boot():
            try:
                server, self.port = await start_server(self.hub, self._want)
            except Exception as e:
                self.error = e
                self._ready.set()
                return
            await self.pitwall.start(8765)
            self._ready.set()
            self.loop.create_task(self.hub.pump())
            async with server:
                await server.serve_forever()

        self.loop.run_until_complete(boot())

    @property
    def url(self):
        return "http://127.0.0.1:%d/" % self.port

    def replay(self, path, speed=1.0):
        source = ReplaySource(path, speed)
        self.loop.call_soon_threadsafe(self.hub.set_source, source)
        return source.name

    def live(self):
        self.loop.call_soon_threadsafe(self.hub.set_source, LiveSource())


class Api:
    """Functions the pages can call from the app window (window.pywebview.api)."""

    # pywebview walks every public attribute of this object (recursively) to build
    # window.pywebview.api. Keep the engine and the native window private, or it
    # crawls the event loop, threads, iRacing reader and WinForms objects and the
    # window freezes.
    def __init__(self, engine):
        self._engine = engine
        self._window = None

    def version(self):
        return __version__

    def pick_replay(self):
        import webview
        kind = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        picked = self._window.create_file_dialog(kind, file_types=("iRacing telemetry (*.ibt)", "All files (*.*)"))
        if not picked:
            return {"ok": False}
        path = picked[0] if isinstance(picked, (list, tuple)) else picked
        try:
            return {"ok": True, "file": self._engine.replay(path)}
        except Exception as e:
            return {"ok": False, "error": "Could not open that file: %s" % e}

    def use_live(self):
        self._engine.live()
        return {"ok": True}

    def install_camera_pack(self):
        """Pick a downloaded camera pack (.zip) and copy it into iRacing's cameras folder."""
        import webview
        from suite.cameras import install_pack
        kind = webview.FileDialog.OPEN if hasattr(webview, "FileDialog") else webview.OPEN_DIALOG
        picked = self._window.create_file_dialog(kind, file_types=("Camera pack (*.zip)", "All files (*.*)"))
        if not picked:
            return {"ok": False}
        path = picked[0] if isinstance(picked, (list, tuple)) else picked
        try:
            return install_pack(path)
        except Exception as e:
            return {"ok": False, "error": "Could not install that pack: %s" % e}

    # ---- Share online (Cloudflare quick tunnel to PitWall's relay)
    def share_start(self):
        return self._engine.pitwall.share_service().start()

    def share_stop(self):
        s = self._engine.pitwall.share
        return s.stop() if s else {"state": "off"}

    # ---- Updates from the repo's GitHub releases (suite/updater.py)
    def update_info(self):
        u = self._engine.updater
        return u.info() if u else {"state": "idle", "hasKey": False, "canApply": False, "build": None}

    def update_set_key(self, token):
        from suite.updater import UpdateError
        try:
            self._engine.updater.set_key(token)
        except UpdateError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, **self._engine.updater.info()}

    def update_clear_key(self):
        self._engine.updater.clear_key()
        return self._engine.updater.info()

    def update_open_key_page(self):
        """GitHub's new-key page, in the browser. A fixed address: pages cannot open others."""
        from suite.updater import KEY_PAGE
        webbrowser.open(KEY_PAGE)
        return {"ok": True}

    def update_check(self):
        _background(self._engine.updater.check)
        return {"ok": True}

    def update_install(self):
        """Download the newest build, check it, swap it in and restart."""
        u = self._engine.updater

        def run():
            u.download()
            u.apply(lambda: quit_now(0))
        _background(run)
        return {"ok": True}

    def share_info(self):
        host = self._engine.pitwall
        s = host.share
        d = s.info() if s else {"state": "off", "downloaded": None}
        if d.get("state") == "on" and host.relay is not None:
            d["usage"] = host.relay.guest_usage()
        return d

    def open_cameras_folder(self):
        from suite.cameras import cameras_dir
        d = cameras_dir()
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)  # Windows
        except Exception as e:
            return {"ok": False, "error": str(e), "path": d}
        return {"ok": True, "path": d}


_on_quit = []


def _background(fn):
    """Run fn on its own thread; its errors are reported through its own state."""
    def run():
        try:
            fn()
        except Exception as e:
            print("background task: %s: %s" % (type(e).__name__, e))
    threading.Thread(target=run, daemon=True).start()


def selftest_rename(out):
    """Hidden, for the release build check: can this running .exe be renamed
    aside and back? That is how an update swaps the app while it runs."""
    from suite.updater import old_path
    exe = os.path.abspath(sys.executable)
    old = old_path(exe)
    try:
        if not getattr(sys, "frozen", False):
            raise RuntimeError("not a built app")
        os.replace(exe, old)
        os.replace(old, exe)
        result = "ok"
    except Exception as e:
        result = "%s: %s" % (type(e).__name__, e)
    with open(out, "w", encoding="utf-8") as f:
        f.write(result)
    return result


def quit_now(code=0):
    """End the process. Background threads (iRacing reader, thread pools) must not
    keep the app alive after its window is closed."""
    for f in _on_quit:
        try:
            f()
        except Exception:
            pass
    try:
        print("iRacing Suite closed.")
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(code)


def fallback_window(url):
    """Browser mode: open the suite in the browser and keep a small window to stop it."""
    webbrowser.open(url)
    try:
        import tkinter as tk
        root = tk.Tk()
        root.title("iRacing Suite")
        root.geometry("380x130")
        tk.Label(root, text="iRacing Suite is running.", font=("Segoe UI", 12, "bold")).pack(pady=(14, 4))
        tk.Label(root, text=url).pack()
        tk.Button(root, text="Open in browser", command=lambda: webbrowser.open(url)).pack(pady=8)
        tk.Label(root, text="Close this window to stop the suite.").pack()
        root.mainloop()
    except Exception:
        print("iRacing Suite is running at %s. Press Ctrl+C to stop." % url)
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass


def close_with_app(engine):
    """What must end with the app. The relay, PitWall's bridge, the iRacing reader and the
    web servers are threads of this process and end with it (quit_now); the share tunnel
    (cloudflared) is a separate program, so it is stopped and its link revoked first. If the
    app is killed instead of closed, Windows ends the tunnel too (share._KillWithParent)."""
    _on_quit.append(lambda: engine.pitwall.share and engine.pitwall.share.stop())


def main():
    try:
        _main()
    except SystemExit as e:
        quit_now(e.code if isinstance(e.code, int) else 0)
    except BaseException as e:   # noqa: BLE001 -- a windowed app has no console: never linger unseen
        print("iRacing Suite stopped: %s: %s" % (type(e).__name__, e))
        quit_now(1)


def _main():
    redirect_output_when_windowed()
    ap = argparse.ArgumentParser(description="iRacing Suite")
    ap.add_argument("--port", type=int, default=8123)
    ap.add_argument("--rate", type=float, default=60, help="updates per second (iRacing itself runs at 60)")
    ap.add_argument("--replay", metavar="FILE.ibt", help="start by replaying a telemetry file")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--browser", action="store_true", help="use the web browser instead of the app window")
    ap.add_argument("--pitwall-source", choices=["live", "fake"], default="live",
                    help="PitWall's data: live iRacing, or its fake 24-car field for trying it out")
    ap.add_argument("--after-update", metavar="PIDS", help=argparse.SUPPRESS)
    ap.add_argument("--selftest-rename", metavar="FILE", help=argparse.SUPPRESS)
    a = ap.parse_args()

    from suite import updater as upd
    if a.selftest_rename:
        selftest_rename(a.selftest_rename)
        quit_now(0)
    if a.after_update:
        # The previous version is closing: let it release its ports and files
        # first, or PitWall would move ports and break the OBS address.
        pids = [int(x) for x in a.after_update.split(",") if x.strip().isdigit()]
        upd.wait_for_exit(pids)
        threading.Thread(target=upd.tidy_after_update, daemon=True).start()

    engine = Engine(a.rate, a.pitwall_source)
    engine.updater = upd.Updater()
    engine.start(a.port)
    if engine.updater.exe:
        def auto_check():
            # Quietly see whether a newer build is out: at launch, then every 6 hours.
            while True:
                if engine.updater.keys.load() and engine.updater.state in ("idle", "error"):
                    try:
                        engine.updater.check()
                    except Exception:
                        pass
                time.sleep(6 * 3600)
        threading.Thread(target=auto_check, daemon=True).start()
    # A shared link must never outlive the app.
    close_with_app(engine)
    print("iRacing Suite %s on %s" % (__version__, engine.url))
    if a.replay:
        engine.replay(a.replay, a.speed)

    if not a.browser:
        try:
            import webview
            api = Api(engine)
            window = webview.create_window("iRacing Suite", engine.url, js_api=api,
                                           width=1440, height=920, min_size=(900, 600))
            api._window = window
            try:
                window.events.closed += lambda *a: quit_now(0)
            except Exception:
                pass
            webview.start()
            quit_now(0)
        except Exception as e:
            print("App window unavailable (%s); opening in the browser instead." % e)
    fallback_window(engine.url)
    quit_now(0)


if __name__ == "__main__":
    main()
