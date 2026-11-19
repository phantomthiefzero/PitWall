"""Weather trends and the outlook."""
from __future__ import annotations

import math
import re


class Weather:
    # ------------------------------------------------------------------ #
    # Race engineer booth: lap history, pace, gaps to you, pit loss,
    # rejoin, undercut watch, traffic, and the event feed.
    # ------------------------------------------------------------------ #

    # How long to wait for CarIdxLastLapTime to change after the lap counter
    # ticks. The player's LapLastLapTime is known to lag the crossing (the Lap
    # Analyzer had to correct for exactly that); whether the per-car array does
    # is not verified, so both orders are handled: a value that already changed
    # is taken at once, one that has not is waited for.
    LAP_TIME_WAIT_S = 10.0
    PACE_LAPS = 5
    CONSISTENCY_LAPS = 5
    # name: (sample every s, samples kept, keys, minimum samples to show a trend)
    TRENDS = {"weather": (10.0, 90, ("airTempC", "trackTempC", "humidity", "precipitationPct", "windMs",
                                      "wetnessRaw", "solarAltitudeRad", "skiesRaw"), 2),
              "engine": (5.0, 360, ("waterTempC", "oilTempC", "oilPressBar"), 3)}
    TREND_DRAW_POINTS = 60      # a sparkline is ~60 px: more points draw nothing more

    def trends(self, t, weather: dict, player: dict, player_live: bool) -> dict:
        """What the weather and your engine have DONE: never a forecast (iRacing
        publishes none). Per trend: the span watched, each value's change from
        the first sample to the last, and a thinned series for a sparkline.
        Engine samples only while the player block is live -- spectating, it
        reads flat zeros. Also the session's peak combined g, from the same
        360 Hz accelerometer samples the grip trace draws."""
        out = {}
        for name, (every, keep, keys, need) in self.TRENDS.items():
            src = weather if name == "weather" else player
            hist = self._trend[name]
            last = self._trend_at[name]
            if t is not None and (name == "weather" or player_live) and (last is None or t - last >= every or t < last):
                if last is not None and t < last:
                    hist.clear()                     # the clock went back: a new session or a replay jump
                self._trend_at[name] = t
                hist.append((t, {k: src.get(k) for k in keys}))
                del hist[:-keep]
            if len(hist) < need:
                out[name + "Trend"] = None
                continue
            step = max(1, -(-len(hist) // self.TREND_DRAW_POINTS))
            tr = {"spanS": round(hist[-1][0] - hist[0][0], 1), "change": {}, "series": {}}
            for k in keys:
                vals = [s[k] for _t, s in hist if s.get(k) is not None]
                if len(vals) < 2:
                    continue
                tr["change"][k] = round(vals[-1] - vals[0], 4)
                tr["series"][k] = [round(v, 4) for v in vals[::step]] + ([round(vals[-1], 4)] if (len(vals) - 1) % step else [])
            out[name + "Trend"] = tr
        acc = player.get("accel") or {}
        if player_live:
            for lat, lon in zip(acc.get("latG") or [], acc.get("longG") or []):
                if lat is not None and lon is not None:
                    m = math.hypot(lat, lon)
                    if m > self.gg_peak:
                        self.gg_peak = m
        out["ggPeakG"] = round(self.gg_peak, 2) if self.gg_peak > 0.01 else None
        return out
    # ---- weather outlook ---------------------------------------------------
    # iRacing's own forecast and radar come from its servers to the sim's UI; the
    # SDK has none (docs/VARIABLES_TO_VERIFY.md). What can be worked out here, with
    # physics and what this session has done so far, is labelled as an estimate:
    #   * the sun's path, from the track's latitude, the date and where the sun is
    #     now (SolarAltitude), run at the sim's own clock speed;
    #   * the track temperature, from the air temperature plus a heating term in
    #     the sunlight reaching the track (clear-sky irradiance from the sun's
    #     height, cut by the cloud cover), fitted to this session's readings;
    #   * the air temperature, humidity and rain, carried on from their trend over
    #     the last 15 minutes (never more than an hour ahead);
    #   * the dew point (Magnus formula) and how fast the track is drying or wetting.
    OUTLOOK_STEPS_S = (900, 1800, 2700, 3600)
    TRACK_HEAT_PRIOR = 0.025          # degC of track above air per W/m2 of sunlight (asphalt, typical)
    SKY_COVER = {0: 0.0, 1: 0.375, 2: 0.75, 3: 1.0}      # okta fraction per Skies value
    AIR_TREND_MAX_C_PER_H = 4.0

    @staticmethod
    def irradiance(alt_rad, sky_cover):
        """Sunlight on level ground, W/m2: Haurwitz clear-sky model, then
        Kasten & Czeplak's cloud factor. Zero with the sun down."""
        if alt_rad is None or alt_rad <= 0:
            return 0.0
        sh = math.sin(alt_rad)
        clear = 1098.0 * sh * math.exp(-0.057 / sh)
        n = 0.0 if sky_cover is None else max(0.0, min(1.0, sky_cover))
        return clear * (1.0 - 0.75 * n ** 3.4)

    @staticmethod
    def dew_point(air_c, rh):
        """Magnus formula (Alduchov & Eskridge constants). rh as 0..1."""
        if air_c is None or rh is None or rh <= 0:
            return None
        g = math.log(min(rh, 1.0)) + 17.625 * air_c / (243.04 + air_c)
        return 243.04 * g / (17.625 - g)

    @staticmethod
    def _declination(day_of_year):
        return math.radians(23.44) * math.sin(math.radians(360.0 / 365.0 * (284 + day_of_year)))

    @staticmethod
    def _slope(pts):
        n = len(pts)
        if n < 3:
            return None
        mx = sum(x for x, _ in pts) / n
        my = sum(y for _, y in pts) / n
        den = sum((x - mx) ** 2 for x, _ in pts)
        return sum((x - mx) * (y - my) for x, y in pts) / den if den > 0 else None

    OUTLOOK_EVERY_S = 5.0

    def weather_outlook(self, t, weather: dict, session: dict, session_msg: dict | None) -> dict | None:
        """Worked out every few seconds (the weather moves slowly; the sun's search
        is a few thousand steps), the last answer kept in between."""
        if t is None or weather.get("airTempC") is None:
            return None
        last = self._outlook
        if last is not None and 0 <= t - last[0] < self.OUTLOOK_EVERY_S:
            return last[1]
        out = self._weather_outlook(t, weather, session, session_msg)
        self._outlook = (t, out)
        return out

    def _weather_outlook(self, t, weather: dict, session: dict, session_msg: dict | None) -> dict | None:
        hist = [(ht, h) for ht, h in self._trend["weather"]]
        opts = (session_msg or {}).get("options") or {}
        track = (session_msg or {}).get("track") or {}
        out = {"dewPointC": None, "sun": None, "steps": [], "trackModel": None, "rain": None, "clockSpeed": None}
        dp = self.dew_point(weather.get("airTempC"), weather.get("humidity"))
        out["dewPointC"] = None if dp is None else round(dp, 1)
        # the sim clock: how many sim seconds pass per real second (time of day can be sped up)
        tod = session.get("timeOfDayS")
        self._tod.append((t, tod))
        del self._tod[:-60]
        speed = None
        pts = [(a, b) for a, b in self._tod if b is not None]
        if len(pts) >= 2 and pts[-1][0] - pts[0][0] >= 20:
            r = (pts[-1][1] - pts[0][1]) / (pts[-1][0] - pts[0][0])
            if 0.5 < r < 200:
                speed = r
        if speed is None:
            try:
                f = float(str(opts.get("EarthRotationSpeedupFactor")).split()[0])
            except (TypeError, ValueError, IndexError):
                f = None
            speed = f if f and f > 0 else 1.0
        out["clockSpeed"] = round(speed, 2)
        # the sun: hour angle now from where the sun is, then forward at the sim's clock
        try:                                             # "37.161648 m": a number with a unit
            lat = float(str(track.get("TrackLatitude")).split()[0])
        except (TypeError, ValueError, IndexError):
            lat = None
        alt = weather.get("solarAltitudeRad")
        date = str(opts.get("Date") or "")
        sun = None
        alt_fn = None
        if lat is not None and alt is not None and re.match(r"^\d{4}-\d{2}-\d{2}", date):
            y, mo, d = (int(x) for x in date[:10].split("-"))
            doy = (mo - 1) * 30.4 + d
            phi, dec = math.radians(lat), self._declination(doy)
            c = math.cos(phi) * math.cos(dec)
            if abs(c) > 1e-6:
                cos_h = (math.sin(alt) - math.sin(phi) * math.sin(dec)) / c
                cos_h = max(-1.0, min(1.0, cos_h))
                h_abs = math.acos(cos_h)
                alts = [a for _t, a in ((ht, hh.get("solarAltitudeRad")) for ht, hh in hist) if a is not None]
                if len(alts) >= 2 and abs(alts[-1] - alts[0]) > 1e-4:
                    rising = alts[-1] > alts[0]
                else:
                    rising = tod is not None and tod < 12.5 * 3600
                h_now = -h_abs if rising else h_abs
                w = 2 * math.pi / 86400.0 * speed               # hour angle per real second

                def alt_at(dt):
                    hh = h_now + w * dt
                    return math.asin(max(-1.0, min(1.0, math.sin(phi) * math.sin(dec) + c * math.cos(hh))))

                def crossing(target_rad, going_down):
                    step = 120.0
                    prev = alt_at(0.0)
                    for k in range(1, int(86400 / speed / step) + 2):
                        a = alt_at(k * step)
                        if (going_down and prev >= target_rad > a) or (not going_down and prev < target_rad <= a):
                            return round(k * step)
                        prev = a
                    return None
                horizon = math.radians(-0.833)
                sun = {"altDeg": round(math.degrees(alt), 1), "rising": rising,
                       "setsInS": crossing(horizon, True) if alt > horizon else None,
                       "risesInS": crossing(horizon, False) if alt <= horizon else None}
                out["sun"] = sun
                alt_fn = alt_at
        # the track's heating in the sun, fitted to this session where the sun has moved enough
        cover = self.SKY_COVER.get(weather.get("skiesRaw"))
        samples = []
        for _ht, hh in hist:
            if None in (hh.get("trackTempC"), hh.get("airTempC")):
                continue
            samples.append((self.irradiance(hh.get("solarAltitudeRad"), self.SKY_COVER.get(hh.get("skiesRaw"))),
                            hh["trackTempC"] - hh["airTempC"]))
        b, basis = self.TRACK_HEAT_PRIOR, "typical"
        if len(samples) >= 6 and max(x for x, _ in samples) - min(x for x, _ in samples) >= 150:
            fit = self._slope(samples)
            if fit is not None and 0.0 < fit < 0.08:
                b, basis = fit, "fitted"
        a = (sum(y - b * x for x, y in samples) / len(samples)) if samples else \
            ((weather.get("trackTempC") or weather["airTempC"]) - weather["airTempC"]
             - b * self.irradiance(alt, cover))
        out["trackModel"] = {"basis": basis, "cPerWm2": round(b, 4), "offsetC": round(a, 2),
                             "sunWm2": round(self.irradiance(alt, cover))}
        # trends over the last 15 minutes
        def trend(key):
            p = [(ht, hh.get(key)) for ht, hh in hist if hh.get(key) is not None and t - ht <= 900]
            if len(p) < 6 or p[-1][0] - p[0][0] < 120:
                return None
            return self._slope(p)
        air_s = trend("airTempC")
        if air_s is not None:
            cap = self.AIR_TREND_MAX_C_PER_H / 3600
            air_s = max(-cap, min(cap, air_s))
        hum_s, rain_s, wet_s = trend("humidity"), trend("precipitationPct"), trend("wetnessRaw")
        sun_now = self.irradiance(alt, cover)
        for dt in self.OUTLOOK_STEPS_S:
            air = weather["airTempC"] + (air_s or 0.0) * dt
            sun_alt = alt_fn(dt) if alt_fn else alt
            # anchored on the reading now: only the changes in air and sunlight move it
            track_now = weather.get("trackTempC")
            track_c = (track_now + (air - weather["airTempC"]) + b * (self.irradiance(sun_alt, cover) - sun_now)
                       if track_now is not None else air + a + b * self.irradiance(sun_alt, cover))
            hum = weather.get("humidity")
            if hum is not None and hum_s is not None:
                hum = max(0.0, min(1.0, hum + hum_s * dt))
            rain = weather.get("precipitationPct")
            if rain is not None and rain_s is not None:
                rain = max(0.0, min(1.0, rain + rain_s * dt))
            out["steps"].append({"inS": dt, "airC": round(air, 1), "trackC": round(track_c, 1),
                                 "sunAltDeg": None if sun_alt is None else round(math.degrees(sun_alt), 1),
                                 "humidity": None if hum is None else round(hum, 3),
                                 "rainPct": None if rain is None else round(rain, 3)})
        wet = weather.get("wetnessRaw")
        if wet is not None or rain_s is not None:
            per10 = None if wet_s is None else round(wet_s * 600, 2)
            nxt = None
            if wet_s and abs(wet_s) > 1e-5 and wet is not None and wet >= 1:
                target = math.floor(wet) - 1 if wet_s < 0 else math.floor(wet) + 1
                if 1 <= target <= 7:
                    nxt = {"level": target, "inS": round(abs((target - wet) / wet_s))}
            out["rain"] = {"rainTrendPer10Min": None if rain_s is None else round(rain_s * 600, 3),
                           "wetnessTrendPer10Min": per10, "nextWetness": nxt,
                           "state": None if wet_s is None or abs(wet_s * 600) < 0.05
                           else ("drying" if wet_s < 0 else "getting wetter")}
        return out
