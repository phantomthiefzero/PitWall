"""The sights of a race, not only its results: a big pack, three classes in one corner, cars side by side, a car
slicing through traffic, one on a charge, a restart, a pit lane rush, the lead changing in the pits, a front-runner
out. Shared by both analyzers (a mixin on moments.Analyzer): the whole race keeps every one; one car's keeps only those
it is part of (_wants), and the cameras follow that car.

Gaps are in seconds, each class's own race lap (from its lap times) turning lap fractions into time, so a rule reads
the same on a short oval and at Le Mans; side by side is in metres when the track's length is known. Cars are read in
track order each frame (sorted by lap fraction, the circle broken at its biggest gap, so a pack across the line is one).

    pack        PACK_MIN or more cars nose to tail, PACK_GAP_S or less between each, for SPEC_MIN_S
    multiclass  three classes and four cars within MULTI_SPAN_S (a two-class race: five cars, two of each)
    sidebyside  two cars of one class on the same lap overlapping (SIDE_M apart or less) for SIDE_MIN_S; a third car
                overlapping them at the same time makes it three wide
    train       one car going by TRAIN_MIN cars of another class or a lap down within TRAIN_WIN_S
    charge      one car gaining CHARGE_MIN places (net: a pass back and forth gains nothing) within CHARGE_WIN_S:
                a montage of its passes that gained a place for good (CHARGE_CLIPS at most)
    restart     the field back to race pace after a caution (its biggest class under CAUTION_RATIO of its race pace for
                CAUTION_MIN_S, then back over RESTART_RATIO)
    pitrush     PIT_RUSH_MIN cars (or a fifth of the field) in the pit lane at once
    pitlead     a class's lead changing hands because its leader is in the pits (whole race only)
    out         a car in the top three of its class gone from the world mid-race from the track for OUT_MIN_S: towed
                (it came back) or out (it never did); never a car that has done its laps (whole race only: one car's
                own is moments.Analyzer's "towed")

An episode (a pack, a busy corner, a rush) is followed until it has been gone SPEC_END_S (or runs SPEC_MAX_S), then
becomes one moment at its biggest instant; a kind does not repeat within SPEC_COOLDOWN_S unless clearly bigger. No
packs, busy corners or side by side in the opening FIRST_LAP_S (the start is its own moment), and no packs or busy
corners under a caution (the field is bunched on purpose: the restart is the moment).

Anything on a class's last lap is marked (last_lap) and counts LAST_LAP_BONUS more.
"""
from __future__ import annotations

import re
import statistics

NOT_IN_WORLD, OFF_TRACK, IN_STALL, APPROACHING_PITS, ON_TRACK = -1, 0, 1, 2, 3

PACK_MIN = 6
PACK_GAP_S = 0.7
MULTI_SPAN_S = 3.0
SIDE_M = 3.0                 # centre to centre: well under a car's length (~4.4 m), so they overlap; nose to tail
                             # in a draft is a car length or more
SIDE_S = 0.05                # the same in seconds when the track's length is unknown
SIDE_MIN_S = 1.5
SIDE_COOLDOWN_S = 60.0
TRAIN_MIN = 3
TRAIN_WIN_S = 20.0
CHARGE_MIN = 3
CHARGE_WIN_S = 240.0
CHARGE_CLIPS = 4
CAUTION_RATIO = 0.6
RESTART_RATIO = 0.85
CAUTION_MIN_S = 20.0
RESTART_CALM_S = 40.0        # the bunched field just after a restart is the restart, not a pack
PIT_RUSH_MIN = 5
SPEC_MIN_S = 4.0
SPEC_END_S = 4.0
SPEC_MAX_S = 180.0
SPEC_COOLDOWN_S = 90.0
FIRST_LAP_S = 60.0
OUT_MIN_S = 20.0             # gone from the world this long (a blink in a spectator's data is not a tow)
OUT_COOLDOWN_S = 300.0
LAST_LAP_BONUS = 1.3
SPECTACLE_KINDS = ("pack", "multiclass", "sidebyside", "train", "charge", "restart", "pitrush", "pitlead", "out")


def _ord(n):
    return "P%d" % n


def track_metres(weekend_info) -> float | None:
    """WeekendInfo TrackLength ("5.51 km" / "2.50 mi") in metres."""
    txt = str((weekend_info or {}).get("TrackLength") or "")
    m = re.match(r"\s*([\d.]+)\s*(km|mi)", txt)
    if not m:
        return None
    v = float(m.group(1)) * (1609.344 if m.group(2) == "mi" else 1000.0)
    return v if 200 < v < 30000 else None


class Spectacle:
    """Mixed into moments.Analyzer (which gives cars, class_pos, _add, numbers, green, checkered, cls_rate...)."""

    def _spec_init(self):
        self._spec_ep: dict = {}            # kind -> the open episode
        self._spec_last: dict = {}          # kind (or ("side", pair)) -> (t, size) of the last one made
        self._side: dict = {}               # pair -> open side by side
        self._train: dict = {}              # car -> [(t, car gone by)]
        self._charge: dict = {}             # car -> [(t, place after)]
        self._class_laps: dict = {}         # class id -> the race's lap times (its race pace)
        self._race_rate: dict = {}
        self._rate_t = -1e9
        self._caution = None
        self._prev_surf: dict = {}
        self._leaders: dict = {}
        self._lead_pending: dict = {}
        self._gone_cars: dict = {}
        self._pit_lane: dict = {}
        self._restart_t = -1e9
        self.grid_pos: dict = {}            # car -> class place at the green
        self.track_m = None

    # ------------------------------------------------------------------ helpers
    def _wants(self, cars) -> bool:
        return self.target < 0 or self.target in cars

    def _num(self, i) -> str:
        return "#%s" % self.numbers.get(i, i)

    def _car_lap(self, i):
        c = self.cars.get(i)
        return int(c.lc) + 1 if c is not None and c.lc is not None else None

    def _lap_s(self, cls) -> float | None:
        r = self._race_rate.get(cls) or self.cls_typ.get(cls) or self.cls_rate.get(cls)
        return 1.0 / r if r else None

    def _note_lap(self, cls, lt):
        if cls is not None and lt and lt > 0:
            laps = self._class_laps.setdefault(cls, [])
            laps.append(lt)
            if len(laps) > 400:
                del laps[:200]

    def _update_race_pace(self, t):
        if t - self._rate_t < 30:
            return
        self._rate_t = t
        for k, laps in self._class_laps.items():
            if len(laps) >= 3:
                m = statistics.median(laps)
                good = [x for x in laps if x < 1.1 * m]
                if good:
                    self._race_rate[k] = 1.0 / statistics.median(good)

    def _gap_s(self, a, b) -> float:
        """Seconds from car a (behind) to car b (ahead) on track, in the slower one's race lap."""
        lap = max(self._lap_s(a[2].cls) or 0, self._lap_s(b[2].cls) or 0)
        return ((b[0] - a[0]) % 1.0) * lap if lap else 99.0

    def _spec_add(self, kind, t, segs, score, label, members, focus, key=None, **extra):
        """A spectacle moment: `members` the cars in it, `focus` the car the cameras follow (one car's video: that
        car, when it is a member)."""
        members = [i for i in dict.fromkeys(members) if i is not None]
        if not self._wants(members):
            return None
        f = focus if self.target < 0 else self.target
        others = [i for i in members if i != f]
        extra.setdefault("lap", self._car_lap(f))
        m = self._add(kind, t, segs, score, label, others[:3], focus=f, spectacle=True, **extra)
        m["key"] = round(key if key is not None else t, 2)
        return m

    def _episode(self, kind, t, size, live, detail):
        """Follow one episode of a kind; returns it, finished, when it has ended (or run SPEC_MAX_S)."""
        ep = self._spec_ep.get(kind)
        if live:
            if ep is None:
                self._spec_ep[kind] = {"t0": t, "last": t, "size": size, "at": t, "detail": detail}
                return None
            ep["last"] = t
            if size > ep["size"]:
                ep["size"], ep["at"], ep["detail"] = size, t, detail
            if t - ep["t0"] < SPEC_MAX_S:
                return None
        elif ep is None or t - ep["last"] <= SPEC_END_S:
            return None
        self._spec_ep.pop(kind)
        return self._worth(kind, ep)

    def _worth(self, kind, ep):
        if ep["last"] - ep["t0"] < SPEC_MIN_S:
            return None
        last = self._spec_last.get(kind)
        if last is not None and ep["at"] - last[0] <= SPEC_COOLDOWN_S and ep["size"] < last[1] + 3:
            return None
        self._spec_last[kind] = (ep["at"], ep["size"])
        return ep

    # ------------------------------------------------------------------ every frame (after the green)
    def _spectacle(self, t):
        if self.green is None:
            return
        self._update_race_pace(t)
        if not self.grid_pos:
            for i in self.cars:
                p = self.class_pos(i)
                if p:
                    self.grid_pos[i] = p
        on = [(c.pct, i, c) for i, c in self.cars.items()
              if c.dist is not None and c.pct is not None and not c.pit
              and c.surf not in (NOT_IN_WORLD, IN_STALL, APPROACHING_PITS)]
        early = t - self.green < FIRST_LAP_S
        calm = early or self._caution is not None or t - self._restart_t < RESTART_CALM_S
        sides = set()
        if len(on) >= 2:
            on.sort(key=lambda x: x[0])
            gaps = [((on[(k + 1) % len(on)][0] - on[k][0]) % 1.0) for k in range(len(on))]
            cut = max(range(len(on)), key=lambda k: gaps[k])
            on = on[cut + 1:] + on[:cut + 1]
            chains, cur = [], [on[0]]
            for k in range(1, len(on)):
                a, b = on[k - 1], on[k]
                if self._gap_s(a, b) <= PACK_GAP_S:
                    cur.append(b)
                else:
                    chains.append(cur)
                    cur = [b]
            chains.append(cur)
            if not early:
                sides = self._side_by_side(t, on)
            self._packs(t, chains if not calm else [])
            self._multiclass(t, on if not calm else [])
        else:
            self._packs(t, [])
            self._multiclass(t, [])
        self._end_sides(t, sides)
        self._caution_watch(t)
        self._pit_rush(t)
        self._trains_and_charges(t)
        if self.target < 0:
            self._outs(t)
            self._pit_leads(t)

    # ------------------------------------------------------------------ packs and busy corners
    def _packs(self, t, chains):
        best = max(chains, key=len) if chains else []
        live = len(best) >= PACK_MIN and self._wants([x[1] for x in best])
        ep = self._episode("pack", t, len(best) if live else 0, live, [x[1] for x in best] if live else None)
        if ep:
            self._emit_pack(ep)

    def _emit_pack(self, ep):
        mem, n = ep["detail"], ep["size"]
        # track order runs forward: the last is the front of the pack; the cameras look back at it from the front
        self._spec_add("pack", ep["at"], [(ep["at"] - 8, ep["at"] + 10)], 28 + 3 * min(n, 15),
                       "A pack of %d cars nose to tail" % n, [mem[0]] + mem[1:-1] + [mem[-1]], mem[-1],
                       carsN=n, at=self.cars[mem[-1]].pct)

    def _multiclass(self, t, on):
        classes_all = {c.cls for _, _, c in on if c.cls is not None}
        best = None
        if len(classes_all) >= 2:
            three = len(classes_all) >= 3
            j = 0
            for k in range(len(on)):
                while j < k and ((on[k][0] - on[j][0]) % 1.0) * (self._lap_s(on[k][2].cls) or 0) > MULTI_SPAN_S:
                    j += 1
                win = on[j:k + 1]
                counts: dict = {}
                for _, _, c in win:
                    counts[c.cls] = counts.get(c.cls, 0) + 1
                ok = (len(counts) >= 3 and len(win) >= 4) if three else \
                    (len(counts) >= 2 and len(win) >= 5 and min(counts.values()) >= 2)
                if ok and (best is None or len(win) > len(best)) and self._wants([x[1] for x in win]):
                    best = win
        live = best is not None
        ep = self._episode("multiclass", t, len(best) if live else 0, live,
                           ([x[1] for x in best], len({c.cls for _, _, c in best}), best[-1][0]) if live else None)
        if ep:
            self._emit_multi(ep)

    def _emit_multi(self, ep):
        mem, ncls, at = ep["detail"]
        self._spec_add("multiclass", ep["at"], [(ep["at"] - 7, ep["at"] + 7)], 40 + 4 * min(ep["size"], 10),
                       "%d classes, %d cars in one corner" % (ncls, ep["size"]), mem, mem[-1], at=at,
                       carsN=ep["size"], classesN=ncls)

    # ------------------------------------------------------------------ side by side
    def _side_by_side(self, t, on) -> set:
        """Open or extend the side by side of each overlapping pair of neighbours; returns the pairs overlapping."""
        runs, cur = [], [on[0]]
        for k in range(1, len(on)):
            a, b = on[k - 1], on[k]
            same = a[2].cls == b[2].cls and round(b[2].dist - a[2].dist) == 0
            gap = (b[0] - a[0]) % 1.0
            close = gap * self.track_m <= SIDE_M if self.track_m else self._gap_s(a, b) <= SIDE_S
            if close and same:
                cur.append(b)
            else:
                runs.append(cur)
                cur = [b]
        runs.append(cur)
        seen = set()
        for run in runs:
            ids = [x[1] for x in run]
            for k in range(1, len(run)):
                key = (min(ids[k - 1], ids[k]), max(ids[k - 1], ids[k]))
                seen.add(key)
                s = self._side.get(key)
                if s is None:
                    s = self._side[key] = {"t0": t, "last": t, "at": run[k][0], "wide": [], "cls": run[k][2].cls}
                s["last"] = t
            for k in range(len(run) - 2):
                # three (or four) wide: cars all within SIDE_M of each other, not a line of overlapping pairs
                wide = [x for x in run[k:k + 4] if self._apart_m(run[k], x) <= SIDE_M]
                if len(wide) >= 3:
                    for q in range(1, len(wide)):
                        key = (min(wide[q - 1][1], wide[q][1]), max(wide[q - 1][1], wide[q][1]))
                        sd = self._side.get(key)
                        if sd is not None and len(wide) > len(sd["wide"]):
                            sd["wide"] = [x[1] for x in wide]
        return seen

    def _apart_m(self, a, b) -> float:
        gap = (b[0] - a[0]) % 1.0
        if self.track_m:
            return gap * self.track_m
        return self._gap_s(a, b) / SIDE_S * SIDE_M

    def _end_sides(self, t, seen):
        for key, s in list(self._side.items()):
            if key in seen or t - s["last"] <= 0.5:
                continue
            self._side.pop(key)
            dur = s["last"] - s["t0"]
            if dur < SIDE_MIN_S:
                continue
            wide = s["wide"]
            # a three wide is one moment, not one for each pair in it
            tag = ("side", tuple(sorted(wide)) if wide else key)
            if t - self._spec_last.get(tag, (-1e9,))[0] < SIDE_COOLDOWN_S:
                continue
            self._spec_last[tag] = (t, dur)
            i, j = key
            mem = wide or [i, j]
            pos = min([p for p in (self.class_pos(x) for x in mem) if p] or [99])
            score = 30 + min(25.0, dur * 4) + (25 if pos == 1 else 12 if pos <= 3 else 0)
            for_what = " for the lead" if pos == 1 else (" for %s" % _ord(pos)) if pos < 99 else ""
            if len(mem) >= 3:
                score += 30 + (15 if len(mem) >= 4 else 0)
                label = "%s wide - %s%s" % ("Four" if len(mem) >= 4 else "Three", ", ".join(self._num(x) for x in mem[:-1]) + " and " +
                                               self._num(mem[-1]), for_what)
            else:
                label = "%s and %s side by side%s for %.0f s" % (self._num(i), self._num(j), for_what, dur)
            self._spec_add("sidebyside", s["t0"], [(s["t0"] - 5, s["last"] + 4)], score, label, mem, mem[-1],
                           key=(s["t0"] + s["last"]) / 2, at=s["at"], duration=round(dur, 1), wide=len(mem))

    # ------------------------------------------------------------------ cautions, pits, the lead, cars out
    def _caution_watch(self, t):
        sizes = [(sum(1 for c in self.cars.values() if c.cls == k), k) for k in self._race_rate]
        if not sizes:
            return
        k = max(sizes)[1]
        now, race = self.cls_rate.get(k), self._race_rate.get(k)
        if not now or not race:
            return
        ratio = now / race
        if self._caution is None:
            if ratio < CAUTION_RATIO:
                self._caution = {"t0": t, "slow": t}
            return
        if ratio < CAUTION_RATIO:
            self._caution["slow"] = t
            return
        if ratio > RESTART_RATIO:
            c, self._caution = self._caution, None
            if c["slow"] - c["t0"] >= CAUTION_MIN_S:
                self._restart_t = t
                cars = sorted(((x.dist, i) for i, x in self.cars.items()
                               if x.cls == k and x.dist is not None and x.surf != NOT_IN_WORLD), reverse=True)
                mem = [i for _, i in cars[:6]]
                if self.target >= 0:
                    mem = [self.target] + mem
                if mem:
                    self._spec_add("restart", t, [(t - 10, t + 15)], 55, "Restart after the caution", mem, mem[0],
                                   key=t, cautionS=round(c["slow"] - c["t0"]))

    def _pit_rush(self, t):
        pit = [i for i, c in self.cars.items() if c.pit and c.surf != NOT_IN_WORLD]
        need = max(PIT_RUSH_MIN, int(0.2 * max(1, len(self.cars))))
        live = len(pit) >= need and self._wants(pit)
        ep = self._episode("pitrush", t, len(pit) if live else 0, live, pit if live else None)
        if ep:
            self._emit_rush(ep)

    def _emit_rush(self, ep):
        mem = ep["detail"]
        best = min(mem, key=lambda i: self.class_pos(i) or 99)
        self._spec_add("pitrush", ep["at"], [(ep["at"] - 8, ep["at"] + 12)], 28 + 2 * min(ep["size"], 15),
                       "Pit lane rush - %d cars in" % ep["size"], [best] + mem, best, carsN=ep["size"])

    def _done_laps(self, i):
        """The laps car i finished the race with, by the replay's own final results (None without them)."""
        for r in getattr(self, "results_all", []) or []:
            if r.get("CarIdx") == i and isinstance(r.get("LapsComplete"), (int, float)):
                return int(r["LapsComplete"])
        return None

    def _outs(self, t, final=False):
        for i, c in self.cars.items():
            prev = self._prev_surf.get(i)
            self._prev_surf[i] = c.surf
            g = self._gone_cars.get(i)
            if g is not None and (c.surf != NOT_IN_WORLD or final):
                self._gone_cars.pop(i)
                gone = (t if c.surf == NOT_IN_WORLD else g["back"] if "back" in g else t) - g["t"]
                if gone >= OUT_MIN_S and t - self._spec_last.get(("out", i), (-1e9,))[0] > OUT_COOLDOWN_S:
                    self._spec_last[("out", i)] = (g["t"], 0)
                    pos, cls = g["pos"], self._cls_word(i)
                    where = ("the %slead" % (cls + " " if cls else "")) if pos == 1 else \
                        "%s%s" % (_ord(pos), " in " + cls if cls else "")
                    back = c.surf != NOT_IN_WORLD and not final
                    self._spec_add("out", g["t"], [(g["t"] - 14, g["t"] + 2)], (35 if back else 45) +
                                   (15 if pos == 1 else 0),
                                   "%s %s from %s" % (self._num(i), "is towed" if back else "is out", where),
                                   [i], i, key=g["t"] - 4, classPos=pos, lap=g["lap"], towed=back)
                continue
            if prev is None or c.surf != NOT_IN_WORLD or prev in (NOT_IN_WORLD, IN_STALL, APPROACHING_PITS) or c.pit:
                continue
            if self.checkered is not None or t - self.green < 30:
                continue
            done = self._done_laps(i)
            if done is not None and c.lc is not None and c.lc >= done:
                continue                       # it finished: taking the flag and leaving is not going out
            pos = self.class_pos(i)
            if pos and pos <= 3:
                self._gone_cars[i] = {"t": t, "pos": pos, "lap": self._car_lap(i)}

    def _cls_word(self, i) -> str:
        names = getattr(self, "cls_names", {}) or {}
        c = self.cars.get(i)
        if c is None or len(set(names.values())) < 2:
            return ""
        return str(names.get(c.cls, ""))

    def _pit_leads(self, t):
        by_cls: dict = {}
        for i, c in self.cars.items():
            if c.pit and c.surf not in (IN_STALL, NOT_IN_WORLD) and (c.rate or 0) > 0:
                self._pit_lane[i] = t          # seen driving down the pit lane (a tow lands in the stall at once)
            if c.dist is not None and c.cls is not None and c.surf != NOT_IN_WORLD:
                if c.cls not in by_cls or c.dist > self.cars[by_cls[c.cls]].dist:
                    by_cls[c.cls] = i
        for k, lead in by_cls.items():
            old = self._leaders.get(k)
            self._leaders[k] = lead
            if old is None or old == lead:
                continue
            oc = self.cars.get(old)
            if oc is not None and (oc.pit or oc.surf in (IN_STALL, APPROACHING_PITS)) and \
                    t - self._pit_lane.get(old, -1e9) < 60:
                self._lead_pending[k] = {"t": t, "old": old, "new": lead}
        for k, p in list(self._lead_pending.items()):
            if t - p["t"] < 5.0:
                continue
            self._lead_pending.pop(k)
            if self._leaders.get(k) != p["new"]:
                continue                       # the leader came back out in front
            cls = self._cls_word(p["new"])
            self._spec_add("pitlead", p["t"], [(p["t"] - 8, p["t"] + 8)], 45,
                           "%s takes the %slead as %s pits" % (self._num(p["new"]), (cls + " ") if cls else "",
                                                               self._num(p["old"])),
                           [p["new"], p["old"]], p["new"], classPos=1)

    # ------------------------------------------------------------------ fed by the pass detectors
    def _note_traffic(self, passer, passed, t):
        self._train.setdefault(passer, []).append((t, passed))

    def _note_pass(self, passer, pos, t):
        if pos:
            self._charge.setdefault(passer, []).append((t, pos))

    @staticmethod
    def _busiest(times, win):
        """The busiest run (a, b) of sorted times, each within win of the first."""
        best, b = (0, 0), 0
        for a in range(len(times)):
            b = max(b, a)
            while b + 1 < len(times) and times[b + 1] - times[a] <= win:
                b += 1
            if b - a > best[1] - best[0]:
                best = (a, b)
        return best

    def _trains_and_charges(self, t, final=False):
        for i, tr in list(self._train.items()):
            if not final and t - tr[-1][0] <= TRAIN_WIN_S:
                continue
            self._train.pop(i)
            a, b = self._busiest([x[0] for x in tr], TRAIN_WIN_S)
            n = b - a + 1
            if n >= TRAIN_MIN:
                t0, t1 = tr[a][0], tr[b][0]
                self._spec_add("train", t0, [(t0 - 6, t1 + 4)], 30 + 6 * min(n, 8),
                               "%s slices through traffic - %d cars in %.0f s" % (self._num(i), n, t1 - t0),
                               [i] + [x[1] for x in tr[a:b + 1]], i, key=tr[(a + b) // 2][0], carsN=n)
        for i, ps in list(self._charge.items()):
            if not final and t - ps[-1][0] <= 60:
                continue
            self._charge.pop(i)
            # the run gaining the most places (a pass back and forth gains nothing)
            best, gain = None, 0
            for a in range(len(ps)):
                for b in range(a, len(ps)):
                    if ps[b][0] - ps[a][0] > CHARGE_WIN_S:
                        break
                    g = ps[a][1] + 1 - ps[b][1]
                    if g > gain:
                        best, gain = (a, b), g
            if best is not None and gain >= CHARGE_MIN:
                a, b = best
                run = ps[a:b + 1]
                n = len(run)
                start_pos, end_pos = run[0][1] + 1, run[-1][1]
                # the montage: the passes that gained a place for good, at most CHARGE_CLIPS of them
                keep = [x for k, x in enumerate(run) if all(y[1] > x[1] for y in run[:k]) or k == 0]
                keep = sorted(keep, key=lambda x: x[1])[:CHARGE_CLIPS]
                keep.sort()
                self._spec_add("charge", keep[0][0], [(pt - 6, pt + 3) for pt, _ in keep],
                               35 + 6 * min(gain, 8) + (15 if end_pos == 1 else 8 if end_pos <= 3 else 0),
                               "%s on a charge - %s to %s" % (self._num(i), _ord(start_pos), _ord(end_pos)),
                               [i], i, key=keep[-1][0], keys=[round(pt, 2) for pt, _ in keep], passes=n,
                               gained=gain, classPos=end_pos)

    # ------------------------------------------------------------------ the end
    def _spec_finish(self, t_end):
        """Everything still open when the scan ends."""
        for kind in list(self._spec_ep):
            ep = self._worth(kind, self._spec_ep.pop(kind))
            if ep is None:
                continue
            {"pack": self._emit_pack, "multiclass": self._emit_multi, "pitrush": self._emit_rush}[kind](ep)
        self._end_sides(t_end + 1e6, set())
        self._trains_and_charges(t_end, final=True)
        if self.target < 0:
            self._outs(t_end, final=True)

    def last_lap(self, moments, windows):
        """Mark the moments on a class's last lap. windows: [(t0, t1, class id or None)]."""
        for m in moments:
            if m.get("mandatory") or m["kind"] in ("finish", "start", "fastest", "pitrush"):
                continue
            f = m.get("focus", self.target)
            c = self.cars.get(f)
            for t0, t1, cls in windows:
                if t0 <= m["t"] <= t1 and (cls is None or (c is not None and c.cls == cls)):
                    m["lastLap"] = True
                    m["score"] = round(m["score"] * LAST_LAP_BONUS, 1)
                    if not m["label"].startswith("Last lap"):
                        m["label"] = "Last lap: " + m["label"][0].lower() + m["label"][1:] \
                            if not m["label"].startswith("#") else "Last lap: " + m["label"]
                    break

    def climber(self):
        """The car that gained the most places in its class from the green to the replay's own final results:
        {"idx", "from", "to", "gained"} (at least 3 places), else None."""
        best = None
        for r in getattr(self, "results_all", []) or []:
            i, cp = r.get("CarIdx"), r.get("ClassPosition")
            if not isinstance(i, int) or not isinstance(cp, (int, float)) or i not in self.grid_pos:
                continue
            to = int(cp) + 1
            gained = self.grid_pos[i] - to
            if gained >= 3 and (best is None or gained > best["gained"]):
                best = {"idx": i, "from": self.grid_pos[i], "to": to, "gained": gained}
        return best
