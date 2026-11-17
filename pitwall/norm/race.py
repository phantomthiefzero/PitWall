"""The race as a whole: flags, length, strength of field, results."""
from __future__ import annotations

import math
from .fuel import timed_laps_left
from .gaps import _parse_laps, _parse_secs


def race_flags(flags, state, time_remain_s=None, timed=False, laps_to_go=None, finished=None) -> list:
    """The flags as the race sees them: checkered only once the LEADER has taken it.

    iRacing raises the checkered bit the moment a timed race's clock runs out, while the leader still has the last
    lap to drive (the overlay waved it a couple of minutes early), and SessionState turned "checkered" 24 s before
    the leader reached the line (Summit Point, Nov 2026: state at 841.9 s, the leader crossing at 865.6 s). A
    spectator's SessionFlags carry neither white nor checkered (green at the start, then nothing; replays carry no
    flags at all). So: `finished` (Deriver.race_end: the leader's first crossing after the end came) decides the
    checkered; until then the end coming (the bit, the state, a timed race's clock out, one lap to go) is the white,
    the last lap. With no `finished` known (a practice, or joined after the finish) the state is believed."""
    out = [f for f in (flags or []) if f not in ("checkered", "white")]
    raw_end = "checkered" in (flags or []) or "white" in (flags or [])
    ending = raw_end or state in ("checkered", "cool_down") or laps_to_go == 1 or (
        state == "racing" and timed and time_remain_s is not None and 0 <= time_remain_s <= 0.5)
    if finished or (finished is None and state in ("checkered", "cool_down")):
        out.append("checkered")
    elif ending:
        out.append("white")
    return out


def race_length(session_msg, session_num, ordered: list, time_remain_s, deriver, flags, results=None, state=None) -> dict:
    """How much of the session is left, from the LEADER, not the player.

    SessionLapsRemainEx is the player's own count, and a spectator has no car:
    a lap-limited race then showed the session clock, which ran on past the
    chequered flag. The lap limit comes from the session info (SessionLaps) and
    the leader's completed laps; the time limit from SessionTimeRemain. When a
    race has both, the one that ends it first is estimated from the leader's
    pace (derived)."""
    sess = next((s for s in (session_msg or {}).get("sessions") or [] if s.get("SessionNum") == session_num), {})
    total = _parse_laps(sess.get("SessionLaps"))
    limit_s = _parse_secs(sess.get("SessionTime"))
    leader = ordered[0] if ordered else None
    done = leader.get("lapCompleted") if leader else None
    if done is not None and done < 0:
        done = 0
    # The leader's laps never go back within a session (Nov 2026: "back on lap 1" after the app was restarted while
    # iRacing showed a replay, whose frame carries the CarIdx laps of that moment): iRacing's scored results
    # (ResultsPositions, kept current by the server) and the most this session has shown are a floor.
    floor = max([r.get("LapsComplete") for r in results or []
                 if r.get("Position") == 1 and isinstance(r.get("LapsComplete"), int)] or [-1])
    if deriver is not None:
        seen = getattr(deriver, "_race_laps_seen", {})
        floor = max(floor, seen.get(session_num, -1))
        if done is not None or floor >= 0:
            seen[session_num] = max(floor, done if done is not None else -1)
            deriver._race_laps_seen = seen
    if floor >= 0 and (done is None or done < floor):
        done = floor
    remain = time_remain_s if time_remain_s is not None and 0 <= time_remain_s < 7 * 86400 else None
    to_go = max(0, total - done) if total and done is not None else None
    pace = deriver.pace(leader["idx"]) if (deriver is not None and leader) else None
    by_time = None
    if limit_s and remain is not None and pace:
        # the leader's laps still to run, the one in progress counted: from where the leader is on the lap
        # (timed_laps_left), not the clock over the lap time alone, which was a lap short for most of every lap
        by_time = timed_laps_left(remain, leader.get("pct"), pace, {"me": True, "white": "white" in (flags or [])}) if remain > 0 else 0
    if total and (not limit_s or by_time is None or to_go is None or to_go <= by_time):
        ends = "laps"
    elif limit_s:
        ends = "time"
    else:
        ends = "laps" if total else None
    finished = None
    if state is not None:
        # white and checkered as build_state reads them (a spectator's SessionFlags carry neither)
        if deriver is not None:
            finished = deriver.race_end(session_num, state, done, total, bool(limit_s), remain,
                                        {c["idx"]: c.get("lapCompleted") for c in ordered if c.get("surface") != "not_in_world"})
        flags = race_flags(flags, state, remain, bool(limit_s), to_go, finished)
    return {"totalLaps": total, "leaderLapsDone": done,
            "leaderLap": leader.get("lap") if leader else None,
            "lapsToGo": to_go, "timeRemainS": remain if limit_s or not total else remain,
            "timeLimited": bool(limit_s), "lapsByTime": by_time, "endsBy": ends,
            "white": "white" in (flags or []), "checkered": "checkered" in (flags or []), "finished": finished}


SOF_K = 1600 / math.log(2)


def _sof(ratings):
    """iRacing's strength-of-field formula over the drivers' iRatings:
    (1600 / ln 2) * ln(n / sum(exp(-iR * ln 2 / 1600))). iRacing does not publish
    the number in the telemetry, so it is computed here from DriverInfo."""
    rs = [r for r in ratings if isinstance(r, (int, float)) and r > 0]
    if not rs:
        return None
    return int(round(SOF_K * math.log(len(rs) / sum(math.exp(-r / SOF_K) for r in rs))))


def strength_of_field(cars: list) -> dict | None:
    """{"overall", "cars", "classes": [{"classId","className","sof","cars"}]} from the
    entry list (spectators and the pace car are not in it). Drivers without an
    iRating (AI, rookies at 0) are left out."""
    rated = [c for c in cars if isinstance(c.get("iRating"), (int, float)) and c["iRating"] > 0]
    if not rated:
        return None
    classes = {}
    for c in rated:
        classes.setdefault(c.get("classId"), []).append(c)
    rows = [{"classId": k, "className": v[0].get("className"), "sof": _sof([c["iRating"] for c in v]), "cars": len(v)}
            for k, v in classes.items()]
    rows.sort(key=lambda r: -(r["sof"] or 0))
    return {"overall": _sof([c["iRating"] for c in rated]), "cars": len(rated), "classes": rows}


# --------------------------------------------------------------------------- #
# Watching a replay: hold the live picture
# --------------------------------------------------------------------------- #

def results_positions(session_info: dict | None, session_num) -> list:
    """SessionInfo/Sessions[session_num]/ResultsPositions: iRacing's scored results,
    which the server keeps updating while a replay is being watched (the CarIdx*
    telemetry then comes from the replay frame instead)."""
    for s in (session_info or {}).get("Sessions") or []:
        if s.get("SessionNum") == session_num:
            return [r for r in (s.get("ResultsPositions") or []) if isinstance(r, dict)]
    return []


def hold_cars(last_live: dict, results: list, edge: dict, t) -> dict:
    """The cars message while a replay is being watched behind live: the last live
    message, with what iRacing's results say now laid over it (position, class
    position, laps, last and best lap). Everything measured from track positions
    (gaps, trends, the map) stays as it was at the moment the replay began, and
    derived.held says so. The derived history is not fed from the replay at all."""
    import copy
    msg = copy.deepcopy(last_live)
    msg["t"] = t
    by = {c["idx"]: c for c in msg.get("cars") or []}
    updated = 0
    for r in results:
        c = by.get(r.get("CarIdx"))
        if c is None:
            continue
        pos, cpos = r.get("Position"), r.get("ClassPosition")
        d = c.setdefault("derived", {})
        if isinstance(pos, int) and pos > 0:
            c["pos"] = d["livePos"] = pos
            updated += 1
        if isinstance(cpos, int) and cpos >= 0:
            c["classPos"] = d["liveClassPos"] = cpos + 1         # 0-based in the results
        if isinstance(r.get("LapsComplete"), int) and r["LapsComplete"] >= 0:
            c["lapCompleted"] = r["LapsComplete"]
        if isinstance(r.get("LastTime"), (int, float)) and r["LastTime"] > 0:
            c["lastLapS"] = r["LastTime"]
        if isinstance(r.get("FastestTime"), (int, float)) and r["FastestTime"] > 0:
            c["bestLapS"] = r["FastestTime"]
    held = (last_live.get("derived") or {}).get("held") or {}
    msg.setdefault("derived", {})["held"] = {
        "sinceT": held.get("sinceT", last_live.get("t")),     # the live moment the picture is from
        "behindLiveS": edge.get("behindLiveS"),
        "fromResults": updated > 0,
    }
    return msg


def race_laps_left(race: dict | None):
    """Laps the leader still has to run: the lap count, or the clock at the leader's pace."""
    if not race:
        return None
    if race.get("endsBy") == "laps" and race.get("lapsToGo") is not None:
        return race["lapsToGo"]
    return race.get("lapsByTime")


def _is_race(session_msg: dict | None, session_num) -> bool:
    for s in (session_msg or {}).get("sessions") or []:
        if s.get("SessionNum") == session_num:
            return "race" in str(s.get("SessionType") or "").lower()
    return False
