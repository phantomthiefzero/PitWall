# Variables to verify before any feature ships

Every claim below is a hypothesis about iRacing's telemetry until a recording
proves it. `python -m pitwall analyze <recording> --md docs/VARIABLES_<session>.md`
produces the evidence; copy the observed label into the "Observed" column and
change the UI treatment if it disagrees.

Recordings needed (each ~10–20 min, `--hz 10` is plenty for classification):

1. `practice.jsonl.gz` — drive laps, leave the car, come back
2. `qual.jsonl.gz`
3. `race.jsonl.gz` — full race with at least one pit stop taking tires + fuel + a repair
4. `rain.jsonl.gz` — any session with rain enabled, ideally dry→wet transition
5. `replay.jsonl.gz` — open the race replay afterward and let it play
6. `spectate.jsonl.gz` — join a session as a spectator (expect most player-car vars blank)

## Group A — expected live while driving

| Variable(s) | Claim | Observed | UI treatment if confirmed |
|---|---|---|---|
| `CarIdxLapDistPct`, `CarIdxPosition`, `CarIdxClassPosition`, `CarIdxLap`, `CarIdxLapCompleted` | live for every car in the field, 60 Hz | | plain |
| `CarIdxF2Time` | race: seconds behind leader; practice/qual: fastest lap | | plain; interval derived from it |
| `CarIdxEstTime` | live; wet-adjusted since 2024 S2 | | plain |
| `CarIdxOnPitRoad`, `CarIdxTrackSurface` | live | | plain |
| `CarIdxLastLapTime`, `CarIdxBestLapTime` | update at S/F crossing | | plain |
| `CarIdxTireCompound`, `CarIdxFastRepairsUsed`, `CarIdxP2P_*`, `CarIdxSessionFlags` | update on event | | plain |
| `CarIdxGear`, `CarIdxRPM`, `CarIdxSteer` | live for all cars (nice for spotters) | | plain |
| `SessionFlags`, `SessionState`, `SessionTimeRemain`, `SessionLapsRemainEx` | live | live. `SessionFlags` decode confirmed against real data: 0x10140200 -> one_lap_to_green, servicible, repair, start_hidden. NB it mixes racing flags with start-light and car-service state; split before showing as a flag banner. | plain |
| `Speed`, `RPM`, `Gear`, `Throttle`, `Brake`, `Clutch`, `SteeringWheelAngle` | live (player only) | | plain |
| `FuelLevel`, `FuelLevelPct`, `FuelUsePerHour` | live | live on track (10 changes/s). Refuel is a step UP mid-stop; a car reset is a step DOWN to the session default (83.33 -> 49.33 L observed). | plain; fuel/lap derived |
| `WaterTemp`, `OilTemp`, `OilPress`, `Voltage`, `EngineWarnings` | live | | plain |
| `dcBrakeBias`, `dcABS`, `dcTractionControl`, `dcAntiRollFront/Rear`, `dcFuelMixture`, … | present only for cars that have the control; change on driver input | | plain; hide when null |
| `AirTemp`, `TrackTempCrew`, `WindVel`, `WindDir`, `RelativeHumidity`, `Skies`, `FogLevel` | slow drift | | plain |
| `Precipitation`, `TrackWetness`, `WeatherDeclaredWet` | change during rain; `TrackWetness` is an estimate enum | | plain, labeled "estimate" for wetness |
| `LFtempCL/CM/CR` etc. (carcass temps) | **believed live** — this is the one I am least sure of | **NOT live**, confirmed repeatedly: 0 changes across 5401 ticks / 9 min (GR86, spanning a pit stop), and 0 changes across 60 s of driving in a 296 GT3. But the update trigger is NOT yet characterised — it is not pit-only (it changed mid-stint in the GT3 with no stop) and it is not per-lap (tested directly at a start/finish crossing: LapCompleted 1 -> 2 with temps identical 1 s later). **Do not claim a trigger until it is isolated.** Separately: this is CARCASS temperature (35-37 C observed) and is a different physical quantity from the tread/surface temperature the in-car dash shows (63-74 C for the same tyres at the same moment). | **snapshot** — grey; label it "snapshot", not "at last pit stop" |
| `VelocityX`, `VelocityY`, `YawNorth`, `Lat`, `Lon` | live; used only for map building | | not displayed |

## Group B — expected to refresh only at pit stops (snapshot)

| Variable(s) | Claim | Observed | UI treatment |
|---|---|---|---|
| `LFwearL/M/R` etc. | frozen until the car is serviced | **Confirmed, with an important caveat.** Updates once on pit-box entry, then frozen through service, pit exit and the whole out-lap (verified at two separate stops). The reading is of *the tires that were on the car at that stop* — so after a tire change it describes the REMOVED set, not what you are driving on. A car reset also refreshes it (back to 1.0). | grey + "at last pit stop"; never present as current |
| `LFtempL/M/R` (tread, if present) | frozen until serviced | Not present in this car's variable set; carcass temps (`LFtempC*`) are the ones available, and they are snapshots — see Group A. | grey + "at last pit stop" |
| `LFcoldPressure` etc. | set at pit stop | Confirmed: 0 changes across the 9-min pit-stop recording. | grey |
| `PitRepairLeft`, `PitOptRepairLeft` | non-zero only while in the pit stall with damage | Confirmed. `PitRepairLeft` counts down live in the box (labelled `pit-only`, 3136 changes, 8.76/s in pit vs 0/s on track). `PitOptRepairLeft` holds static while mandatory repairs run first. Both 0.0 after a car reset. | show only when > 0 |

## Group C — derived here (must be validated numerically)

| Derived value | Inputs | Validation |
|---|---|---|
| track outline | `VelocityX/Y`, `YawNorth`, `LapDistPct` over one clean lap | **Was MIRRORED until fixed.** `YawNorth` is not a counterclockwise angle from +X; heading is `(-cos(yaw), sin(yaw))`. Treating it the other way built a world frame of the wrong handedness, so every outline came out mirrored -- caught by eye at VIR North, where the turn-1 hairpin sat top-right instead of top-left. Verified by rebuilding a real lap and comparing against iRacing's own map. **The unit test did not catch it** because `FakeSource.heading_at()` emitted the same wrong convention, so the two inverted each other and the ground-truth test passed at 1.5 m while every real track was backwards. Both were changed together, and a convention test now pins the chirality independently of either module. Any map cached before this fix is mirrored and must be rebuilt. **Length accuracy was never affected:** Daytona Road Course: `lapLengthM` 5706.9 vs WeekendInfo `TrackLength` 5687.3 m = +19.6 m (0.34%); `closureErrorM` 17.8 (under the 30 m bar). NB iRacing's own two figures disagree by 43 m (`TrackLength` 5.6873 km vs `TrackLengthOfficial` 5.73 km), so there is no single ground truth to hit exactly. |
| interval to car ahead | `CarIdxF2Time` of consecutive positions | race only. **Validated against a real 15-minute race** (spectated, VIR North, 9001 ticks): 78025 intervals emitted, 0 negative, 0 placeholder-looking. Two fixes were needed. **First bug:** before a car completes a lap, `CarIdxF2Time` is a placeholder of exactly 0.001 * (position - 1), not a gap. Differencing it produced a fabricated ~1 ms interval. The placeholder clears PER CAR as each completes a lap (observed: leader crossed the line and P2/P3 went to 2.3722 / 2.4418 while P10, still on lap 1, stayed on 0.0090). Now gated on the car having a positive `CarIdxLastLapTime`. **Second bug: that gate was not enough.** A real lap time does NOT guarantee a real F2Time. Mid-race at VIR, P4 carried 2.8702 while P5 and P6 carried placeholders of 0.0020 and 0.0050 *despite both having genuine last-lap times* (104.496, 104.750) -- 1459 negative intervals over one race. Fixed with two further rules: (a) F2Time must not DECREASE down the running order, so when the sim's gap disagrees with its scored position we show nothing rather than invent a gap; (b) reject any car from P2 back whose F2Time equals 0.001 * (position - 1) exactly. Rule (b) deliberately exempts the leader, which legitimately reports 0.0 and is the baseline the rest of the field is measured against -- rejecting it would suppress every interval. |
| fuel per lap, laps of fuel | `FuelLevel` at consecutive `LapCompleted` increments | compare against the in-sim fuel calculator over 5 laps. First real evidence: 1.58 L/lap over 3 laps in a GR86 at Daytona -> 27.9 laps of fuel. Refuelling and car resets are correctly rejected by the `0 < used < 25` guard, but note that guard also silently drops any car genuinely burning more than 25 L/lap, leaving `fuelPerLap` null with nothing explaining why. |
| fuel to finish | `fuelPerLap` x laps remaining, vs fuel aboard | Silent unless BOTH exist. Lap-limited sessions state laps outright (`SessionLapsRemainEx`, with 32767 meaning unlimited); timed ones are projected from `SessionTimeRemain` / last lap and flagged `lapsRemainingIsEstimate`. |
| closing rate, catch laps | least-squares slope of `intervalS` over a 90 s window x lap time | Positive = catching. **Must reset when the car ahead changes** -- the interval then measures a different pair, and carrying samples across reads the discontinuity as a rate (observed live: +48.17 s/lap). Also clamped at 15 s/lap: anything larger means the series was not continuous. Verified over 28234 samples with zero absurd values. |
| competitor stops | `CarIdxOnPitRoad` and `CarIdxTrackSurface == in_pit_stall` transitions | Both are populated for every car. Validated against the recorded VIR race: 3 genuine stops with box times matching the raw events exactly (car 8, lap 2, 152.4 s = 603.2 - 450.8). **Two traps.** A car released from the pits at the session start looks like a car leaving a stop, so an exit only counts if we watched the entry. And a car already in its box when we connect must seed its state from what it is doing, not from False -- otherwise its release at the green is counted as a stop, which put a bogus 204 s stop on 13 of 16 cars. The count is stops SEEN since connecting, never stops made, and the UI says so. |
| stint pace / degradation | least-squares slope of lap time across the current stint | The usable stand-in for the tyre wear iRacing will not give us. Resets at every pit stop, needs 4 laps before it reports. |
| sector times | `CarIdxLapDistPct` crossings of `SplitTimeInfo` boundaries against `SessionTime` | iRacing publishes sector BOUNDARIES but not per-car sector times. **Validated independently:** derived sectors sum to iRacing's own `lastLapS` to within a millisecond (VIR: 95.615 vs 95.615, 97.671 vs 97.670). That only holds because the crossing is interpolated between samples -- taking the sample tick quantises every sector to the sample interval. Voided by an off, a pit visit or a position jump rather than reported through one. |
| fuel save target | shortfall / laps remaining | Only when short. Turns "you are 2.3 gal down" into "save 0.12 a lap". |
| lap history (`feed.laps`) | `CarIdxLapCompleted` increments; time from `CarIdxLastLapTime` | Takes the time if it already changed at the crossing, else waits up to 10 s for it to change. The player's `LapLastLapTime` is known to lag the line (the Lap Analyzer corrects for it); **whether `CarIdxLastLapTime` lags is not verified** -- both orders are handled and unit-tested. A lap identical to the previous one to the millisecond reads as not-yet-updated and is recorded with no time. |
| pace | median of the last 5 laps with a time and no pit road | Two laps minimum. Pit laps flagged by `CarIdxOnPitRoad` during the lap. |
| gap to you, gap to class leader | differences of `CarIdxF2Time` | Race only, and only between cars whose F2Time passed the placeholder/order checks above. |
| you gain / catch laps | least-squares slope of gap-to-you over 90 s x your pace | One sign for both sides: + is good for you. Samples kept 0.5 s apart (also applied to the closing rate) so the bridge does not burn CPU beside the sim. |
| pit lane loss | lane time (pit-road lines) - stationary time - the same stretch at that car's pace | **Validated on the fake lane** (0.09 lap at 60 km/h) to within 0.8 s per stop. Leaves out braking before the entry line and accelerating after the exit line, so it reads slightly low on a real track. A reset into the box (position jump while on pit road) voids the sample. **Not yet checked against a real stop.** |
| rejoin position | your F2Time + loss, placed among every trusted F2Time | **Validated on the fake race**: class position at pit entry vs actual after rejoining, exact in most stops, within one in at least 80%. Assumes everyone keeps their gap; a rival pitting in the same window moves it. Traffic at the exit folds the race-time separation into one lap of your pace. |
| undercut / overcut | gap to you -/+ typical loss (lane loss + median stationary) | Opens when a class rival within 40 s stops (or you do); series taken only at crossings after the stopper is back on track; settles when both have stopped and rejoined. |
| traffic meeting | lap-fraction gap / (1/your pace - 1/their pace) | Constant-pace arithmetic; the place is approximate because speed varies around a lap. Unit-tested. |
| events (`feed.events`) | transitions in the values above and in `SessionFlags`, incidents, `EngineWarnings`, `PlayerCarTowTime`, weather | Seeded silently on first sight, so only what is watched happening is reported. Pit limiter and rev limiter warnings are routine and skipped. A flag is not repeated within 20 s. |

## Group D — believed unavailable (do not fake)

Other cars' fuel, tire state, damage, pedal inputs, setup. Player damage as a
model (only repair *times* exist, and only in the pit stall).

**Live tyre temperatures and pressures for your own car — confirmed unavailable.**
Tested in two cars, including one whose in-car display shows the data:

| | Toyota GR86 | Ferrari 296 GT3 |
|---|---|---|
| total variables | 331 | 336 |
| tyre/temp/pressure variables | 77 | 77 (identical set, none added) |
| any live tyre variable | none | none |

In the GT3, `LFtempCM` held at exactly 34.70 and `LFwearM` at 1.0000 across pit
exit and on-track running to 55 m/s — identical to two decimal places, not
drifting. In the GR86, all 66 per-corner tyre variables were `static` (0 changes)
across 5401 ticks spanning a full stop and an out-lap.

The complete per-corner TELEMETRY set is: `{LF,RF,LR,RR}tempC{L,M,R}` (carcass,
snapshot), `{...}wear{L,M,R}` (snapshot), `{...}coldPressure` (a SETTING, not a
measurement — it equals `dp*TireColdPress`, the pit-service value, and reads
identically on all four corners), and `{...}brakeLinePress` (present but never
populated: 0.0 in both cars).

An exhaustive scan settles it: of all 336 variables sampled over 30 s of driving,
105 changed, and the only temp/pressure ones among them were `AirTemp`,
`AirPressure`, `WaterTemp`, `OilTemp`, `OilPress`, `ManifoldPress` — engine and
ambient. Per-corner live data does exist (`*shockDefl`, `*shockVel`, `*odometer`
and their 360 Hz `_ST` variants), so iRacing is willing to publish per-wheel
telemetry; it simply does not publish tyre temps or pressures.

**The session YAML carries more than the telemetry variables do.**
`CarSetup/TiresAero/{LeftFront,RightFront,LeftRear,RightRear}` has:

| Field | Meaning |
|---|---|
| `StartingPressure` | what was set for the stint |
| `LastHotPressure` | hot pressure measured at the last stop — a MEASUREMENT, unlike `coldPressure` |
| `LastTempsOMI` / `LastTempsIMO` | per-corner tread temps at the last stop |

These are snapshots too — verified frozen at 159 kPa / 35C across 2 min of driving
to 167 mph — but `LastHotPressure` is strictly better data than the `coldPressure`
the client currently shows, because it is something the tyre did rather than
something the driver dialled in.

**Ordering gotcha:** left corners use `LastTempsOMI` (Outer, Middle, Inner) and
right corners use `LastTempsIMO` (Inner, Middle, Outer). The array order is
mirrored between sides. Reading both as a flat triple would silently swap inner
and outer edges on one side of the car — exactly the reading an engineer uses to
judge camber.

The GT3's five extra variables are all driver controls — `dcDashPage`,
`dcDashPage2`, `dcLowFuelAccept`, `dcThrottleShape`, `dcTractionControlToggle`.
Notably `dcDashPage` tells you which dash page the driver has selected but not
what is rendered on it: the sim draws those displays from internal physics state
the SDK does not publish. Do not promise an engineer live tyre data.

## Context questions the recordings answer

- Which player-car variables go blank when you leave the car (`IsOnTrack` = 0)?
- Which go blank in replay (`IsReplayPlaying` = 1)? Expect most `dc*`, tire, and engine values to.
- Which go blank for a spectator? Expect everything except `CarIdx*`, session and weather.
- Does `CarIdxLapDistPct` for cars in the garage read −1 or 0?
- Does `SessionTimeRemain` read a huge number (>1e6) for lap-limited sessions? (client treats > 3 days as "no limit")

## Verified sessions

| Recording | Session | What it covers |
|---|---|---|
| `recordings/replay-daytona-gr86.jsonl.gz` | replay, Daytona Moto, GR86 field | 1201 ticks / 120 s. Race start under green. No player-car data (replays carry none). |
| `recordings/practice-pitstop.jsonl.gz` | test drive, Daytona Road Course | 5401 ticks / 540 s. Spans a full pit stop (358 s in pit, 182 s on track) — the first recording with both segments, so `live` / `pit-only` labels engage. |
| `recordings/spectate-quali-vir.jsonl.gz` | spectated Lone Qualify, VIR North | 6001 ticks / 600 s. Segments: replay 457.7 s, track 102.4 s, pit 39.9 s. |
| `recordings/spectate-race-vir.jsonl.gz` | spectated race, VIR North | 9001 ticks / 900 s. A settled field with real gaps -- the only recording that properly exercises `intervalS`. |

Still missing from the six the README asks for: qualifying, a rain session, and a
spectator join. Fuel, tyre and `dc*` behaviour is now covered by the test drive.

Add a seventh that the README does not list: **an oval race**. It is the only
way to exercise the caution and pace-car path (see below), and no amount of road
running substitutes for it.

## Findings that are not about a single variable

- **`PitSvFlags` is the request for your NEXT stop, not a record of the last one.**
  It read empty for the entire duration of a stop and only armed to 0x3f
  (4 tyres + fuel + windshield) at pit *exit*. So it cannot be used to detect what
  was serviced at the stop that just happened — by the time you are in the box it
  has already cleared. The dashboard's "Next stop:" label is correct as written.

- **A car reset refreshes the tyre snapshot and clears damage.** Observed: wear
  back to 1.0, carcass temps back to ~45.9 C (ambient), `PitRepairLeft` and
  `PitOptRepairLeft` both 0.0, engine warnings cleared. Fuel is reset to the
  session default rather than preserved or filled (83.33 -> 49.33 L). Incident
  count and lap counter both persist. So "refreshes only at pit stops" is not
  quite right: pit-box entry AND a reset both update it.

- **Right after a replay scrub, only the camera-focused car has data.** For a
  moment `CarIdxLapDistPct` reported 1 live car out of 19, with the rest at -1.
  A bridge polling across a scrub will see the field vanish and must not treat
  that as cars leaving the session.

- **`ResultsPositions` in a replay reports the END of the session, not the frame
  you are scrubbed to.** Observed at a parade-lap frame: `CarIdxLap` was 0-1
  while `ResultsPositions` reported LapsComplete of 2, 7 and 8. It looks like a
  clean fallback for the zeroed `CarIdxPosition` during parade laps. It is not —
  using it would show the finishing order while the engineer watches lap one.

- **`{LF,RF,LR,RR}brakeLinePress` is present but never populated.** Reads 0.0 and
  never changes, in both the GR86 and the 296 GT3. It is in the variable list, so
  it is not "absent" — but it carries no data in either car tested. Treat
  present-but-empty as its own category: a variable another car might populate.

- **The variable list is additive per car, not per-car bespoke.** The GT3's list is
  a strict superset of the GR86's — nothing the GR86 has is missing from the GT3.
  So a variable reading 0.0 in one car is worth re-checking in another before
  concluding iRacing does not provide it.

- **The caution / pace-car path is OVAL-ONLY and cannot be tested on a road
  course.** iRacing does not run full-course cautions in road racing -- road
  gets local yellows at most. So these bits and fields can only ever be
  exercised by an oval session, and no road recording covers them however long
  it runs:

  | Surface | Never yet seen real data |
  |---|---|
  | `FLAG_BITS` | `caution` (0x4000), `caution_waving` (0x8000), `green_held` (0x400), `ten_to_go` (0x800), `five_to_go` (0x1000) |
  | state | `paceMode` (from `PaceMode`) |
  | per car | `paceFlagsRaw` (from `CarIdxPaceFlags`) |
  | client | the "Full course yellow" banner (index.html, flag priority list) |

  Note also that `paceMode` and `paceFlagsRaw` are passed through as raw ints
  with no decode table, unlike every other enum in normalize.py. They are not
  displayed, so nothing false is shown -- but they are unmapped, and mapping
  them needs an oval session to see which values actually occur.

- **iRacing labels latitude and longitude in metres.** `TrackLatitude` comes
  through as `'29.187712 m'` straight from `WeekendInfo`. The value is degrees.
  `normalize.py` copies it verbatim, which is correct; the client must never
  render that unit string.

- **CORRECTION — the player block is not reliably alive while spectating.** The
  entry below was written from the recorded spectator capture, where `Speed`,
  `VelocityX`, `YawNorth` and `LapDistPct` all moved. A later LIVE spectator
  session at the same track showed the opposite: `Speed` 0, `VelocityX` 0, `RPM`
  300 (idle), `Gear` 0 and every `LatAccel_ST` sample flat zero, with
  `IsOnTrack` false and `PlayerCarIdx` 63, while the field around it was racing.
  So it behaves both ways and neither can be assumed. Anything sourced from the
  player block must be gated on `IsOnTrack` (or a non-idle speed/RPM) rather than
  on being in a spectator session or not. Per-car data (`CarIdx*`) was live in
  both cases.

- **Spectating is a replay, and the player-car blackout is SELECTIVE.**
  `IsReplayPlaying` is true with `ReplayPlaySpeed` 1 while spectating, and
  `PlayerCarIdx` is 63 (a spectator slot), not the camera car. But the player-car
  *kinematics* still populate, following whoever the camera is on: `Speed`,
  `VelocityX`, `VelocityY`, `YawNorth` and `LapDistPct` were all `live` with
  1000-2400 changes over 600 s. What stays blank is private car state -- fuel,
  tyres, `dc*`. So a spotter-side bridge gets session, standings, weather AND car
  kinematics; it does not get the driver's fuel or tyres.

- **A track map can be built without driving the session car.** Because those
  kinematics follow the camera car, `LapCollector` accepts spectator samples
  whenever `IsOnTrack` is true and `OnPitRoad` is false. The qualifying recording
  failed only because its on-track window was fragmented ("no complete clean
  lap"), not because the inputs were missing. A ghost lap during a spectated race
  built VIR North to 3615.1 m against iRacing's 3622.2 m (-0.20%, closure error
  16.9 m). And since `bridge.py` loads a cached map keyed on
  (TrackID, TrackConfigName) BEFORE it tries to build one, a map only has to be
  produced once per track by any means -- every later session there, spectating
  included, gets it from cache.

- **Track rubber is a written description, and it is a SETTING not a reading.**
  `SessionInfo/Sessions[N]/SessionTrackRubberState` carries iRacing's own wording
  ("moderate usage", "moderately high usage", "carry over"). Verified unchanged
  across 86 session-info updates over 15 minutes of racing: it describes the
  track at the START of that session. `WeekendInfo/TrackDynamicTrack` was 1 in
  every session recorded, meaning the surface then evolves and the sim never
  reports the evolved state -- so this gets the grey "snapshot, not live"
  treatment, never presented as current.

  Two traps. Sessions after the first usually read **"carry over"**, which
  describes inheritance rather than condition; shown alone it tells an engineer
  nothing, so the client walks back to the nearest earlier session naming a real
  state and shows both ("moderate usage (carried over from Practice)"). And
  `WeekendInfo/TrackState` is **not** track condition -- it read `'VA'`, the US
  state the circuit is in.

- **There is no weather forecast and no radar in the SDK.** Every weather
  variable is `count=1` -- a single scalar for right now: `AirTemp`, `TrackTemp`,
  `Skies`, `Precipitation`, `RelativeHumidity`, `FogLevel`, `WindVel`, `WindDir`,
  `TrackWetness`, `WeatherDeclaredWet`. No variable name contains forecast,
  radar, future, predict, grid or cell, and the session YAML carries only the
  weather SETTINGS (`WeatherType`, `Skies`, starting temps).

  The radar is the harder no: a radar needs precipitation across an area, and
  iRacing reports one number for the whole track. There is no spatial data to
  draw. iRacing's own weather window is fed by their weather service, the same
  server-side pattern as the track map SVGs.

  What the client shows instead is an **observed trend** -- how conditions have
  moved since the viewer connected, with sparklines and deltas, marked as
  computed here. It only ever looks backwards, and is never labelled a forecast.
  Note that a temperature DELTA converts by scale alone: 2 C of cooling is 3.6 F,
  not 35.6 F. Applying the absolute-temperature offset to a difference is an easy
  and very visible bug.

## Replaying a capture through the whole stack

Any `pitwall record` capture can now drive the bridge exactly as the sim would:

```
python -m pitwall stream --source replay --path recordings/race.jsonl.gz \
    --relay ws://127.0.0.1:8765 --token B --speed 3 --loop
```

`--speed` scales playback, `--fast` runs flat out, `--loop` restarts at the end.
This turns captured sessions into repeatable fixtures for the entire stack rather
than just for offline analysis, and it is how the standings, closing rates, pit
stops and flag badges were verified against a real race with iRacing closed.
It immediately showed a stop count rendering that the fake had never produced.
