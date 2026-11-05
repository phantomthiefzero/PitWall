"""What this app asked of iRacing, with the time, in one small file.

iRacing closing to the desktop with no error or crash report (Nov 2026) leaves nothing to go on. Everything that
reaches into the sim beyond reading telemetry is written here: car and helmet pictures from its local picture
server, camera and replay commands, pit commands, and the moments the sim appears and disappears. After a crash,
the last lines before "sim gone" say what the app was doing then.

The file is ~/.pitwall/sim_log.txt (the folder the car pictures are kept in), at most ~512 KB, then it starts again
and keeps the previous one as sim_log.old.txt. Writing never raises: a log must not be what breaks something.
"""
from __future__ import annotations

import os
import threading
import time

MAX_BYTES = 512 * 1024
_lock = threading.Lock()
_path: str | None = None


def path() -> str:
    global _path
    if _path is None:
        from .liveries import default_dir
        _path = os.path.join(default_dir(), "sim_log.txt")
    return _path


def set_path(p: str | None) -> None:
    global _path
    _path = p


def note(what: str) -> None:
    try:
        p = path()
        line = time.strftime("%Y-%m-%d %H:%M:%S") + "  " + str(what).replace("\n", " ")[:300] + "\n"
        with _lock:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            if os.path.isfile(p) and os.path.getsize(p) > MAX_BYTES:
                os.replace(p, p[:-4] + ".old.txt")
            with open(p, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception:  # noqa: BLE001
        pass
