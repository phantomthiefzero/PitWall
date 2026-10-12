"""Record every raw iRacing variable, unmodified, for later analysis.

Output: gzip'd JSON lines.
    {"type":"header","recordedAt":..., "source":..., "hz":..., "vars":[{name,type,count,unit,desc},...]}
    {"type":"session","update":N,"info":{WeekendInfo:...,DriverInfo:...,SessionInfo:...}}   (when YAML changes)
    {"type":"tick","wall":<unix>,"n":<frame>,"vars":{name:value,...}}                       (at --hz)

Nothing is interpreted here. That is the point: analyze.py decides what each
variable does, from evidence, and produces docs/VARIABLES.md.
"""
from __future__ import annotations

import gzip
import json
import math
import time

from .sources import TelemetrySource

SESSION_KEYS = ("WeekendInfo", "DriverInfo", "SessionInfo", "SplitTimeInfo", "CarSetup",
                "QualifyResultsInfo", "CameraInfo", "RadioInfo")


def _clean(v):
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
        return None
    if isinstance(v, list):
        return [_clean(x) for x in v]
    return v


def record(src: TelemetrySource, out_path: str, hz: float = 10.0, duration_s: float | None = None,
           max_ticks: int | None = None, clock: str = "wall", log=print) -> int:
    """clock="wall" paces by wall time (live sim). clock="sim" paces by SessionTime,
    for sources that run faster than real time (fake --fast)."""
    if not src.connect():
        raise RuntimeError("could not connect to telemetry source (is iRacing running and are you in a session?)")
    headers = src.var_headers()
    names = [h["name"] for h in headers]
    interval = 1.0 / hz
    n = 0
    last_siu = None
    t0 = time.time()

    def now_fn():
        if clock == "sim":
            v = src.get("SessionTime")
            return float(v) if v is not None else time.time()
        return time.time()

    next_write = None
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"type": "header", "recordedAt": t0, "source": src.name, "hz": hz,
                            "vars": headers}) + "\n")
        log(f"recording {len(names)} variables at {hz:g} Hz -> {out_path}")
        while True:
            # Stop conditions are checked FIRST, before the tick. They used to live
            # only at the bottom of the loop, which the `continue` below skipped --
            # so if the sim closed mid-recording the loop waited for it forever,
            # never hit the duration check, and never closed the gzip stream. A
            # long race recording where you quit the sim partway produced a hung
            # process and an unreadable file.
            if duration_s is not None and time.time() - t0 >= duration_s:
                break
            if max_ticks is not None and n >= max_ticks:
                break
            if not src.tick():
                time.sleep(0.5)
                if not src.is_connected:
                    log("waiting for sim...")
                continue
            now = now_fn()
            if next_write is None:
                next_write = now
            if now < next_write:
                continue
            next_write += interval
            if now - next_write > 5 * interval:  # clock jumped (replay seek, sim restart): resync
                next_write = now
            siu = src.session_info_update
            if siu != last_siu:
                info = {k: src.session_info(k) for k in SESSION_KEYS}
                f.write(json.dumps({"type": "session", "update": siu, "wall": now,
                                    "info": info}, default=str) + "\n")
                last_siu = siu
            vars_ = {name: _clean(src.get(name)) for name in names}
            f.write(json.dumps({"type": "tick", "wall": now, "n": n, "vars": vars_}) + "\n")
            n += 1
            if n % int(hz * 30) == 0:
                log(f"  {n} ticks, {time.time() - t0:.0f}s wall")
    src.close()
    log(f"done: {n} ticks")
    return n


def iter_recording(path: str):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)
