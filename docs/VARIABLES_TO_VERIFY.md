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

## Broadcast (camera, replay, battles, incidents) — added Oct 2026, NOT yet run against the sim

| Item | Claim | Evidence so far | Check on the sim PC |
|---|---|---|---|
| `CamCarIdx` | follows every camera cut while spectating | 62 distinct changes in `spectate-race-vir` (the analyzer labelled it `pit-only`, an artefact of when cuts happened) | cut cameras in the producer panel; the tower highlight and driver card follow |
| `CamGroupNumber`, `CamCameraNumber` | index `CameraInfo/Groups` | present in both spectate and replay captures | the panel's highlighted group matches iRacing's camera screen |
| `CamCameraState` | 81 (0x51) while spectating = session screen + auto shot + key acceleration | observed value 81 in both captures | none needed for display |
| `ReplayPlaySpeed`, `ReplayPlaySlowMotion` | 1 / false at the live edge while spectating | static 1 / false across 900 s spectated | pause, slow-mo, play from the panel; the overlay shows Paused / Slow-mo / nothing |
| `ReplayFrameNumEnd` | frames from the end of the tape | ~1200 while spectating at speed 1 | **not displayed**: what the live edge reads as is unknown |
| `cam_switch_num(car, group, cam)` | cuts to that car number | pyirsdk API only | tap drivers in the field list |
| `cam_switch_num(-1/-2/-3, ...)` | iRacing chooses: most exciting / leader / incident (irsdk_csMode) | pyirsdk API only | the three "Let iRacing choose" buttons |
| `replay_set_play_speed`, `replay_search` | speed 0 pauses, `to_end` returns to live, `prev/next_incident` jump | pyirsdk API only | Replay buttons; "Back to live" must land at speed 1 |
| `derived.battles` | class neighbours under 1.0 s (held to 1.5 s) | unit-tested; fake race checked rule by rule | watch a real multiclass race: no cross-class "battles", no flicker at 1.0 s |
| `class_lead` event | every class, no player car needed | unit-tested | spectate a race with a lead change |

| `DriverInfo/Drivers[].TeamIncidentCount`, `CurDriverIncidentCount` | populated for EVERY car while spectating, and refreshed with the session info a few seconds after the incident | **unknown and the biggest open question.** One replay tool's author found incident counts "only available for our car"; that may mean the telemetry `PlayerCar*IncidentCount` variables rather than the session info. If others' counts read -1 or never move, the Points list stays empty and only Off track / Slowed remain | spectate a race; watch a car spin; check the Points list gets it, and note how many seconds after the spin it arrives |
| session-info refresh rate | fast enough that one count rise is usually one incident | unknown | compare rises against what you watched; a 1x+2x pair arriving as one 3x shows up as "more than one incident" |
| `CarIdxTrackSurface` off_track | matches iRacing's "wheels off" | used the same way by RobertsmaniaReplay; not compared with 1x awards here | count Off track events against 1x awards for the same car |
| `ReplaySessionTime` vs `SessionTime` | within 4 s at the live edge while spectating | the 4 s tolerance is RobertsmaniaReplay's, not measured here | watch the Live/Replay badge in the producer header while spectating normally: it must say Live |
| `replay_search_session_time(session, ms)` | seeks the replay to that moment | pyirsdk API; RobertsmaniaReplay uses the same broadcast message | press Replay on an incident; the picture shows the car a few seconds before it |
| `derived` slowed | spins and crashes, not slow corners or the start | unit-tested, and on the fake (all detections matched a real fake incident) | watch for false Slowed events at hairpins, at the start, and under yellow |

| Other cars' `TeamIncidentCount` while spectating | hidden until the race ends | **Observed live (Oct 2026): none moved during the race; all arrived after the finish.** The live list is therefore predicted from telemetry | the producer's post-race tally shows how close the prediction was: note the "predicted exactly" count after each race |
| predicted incidents (`race_incident`) | off track ~ iRacing's 1x; backwards or off-and-slow ~ spin 2x; slow alone ~ 2x; two cars slowing together ~ 4x contact | the fake's incidents were written alongside the detector, so passing on it proves the logic only | compare with the official tally after a race; look for kerb-hopping "off tracks" iRacing did not count, and contacts it saw as two separate slowdowns |
| incident watch window (`SessionState`, `SessionFlags`) | only `racing` / `checkered` (cars still to take the flag), never under `caution`, `caution_waving` or `red` | **Observed live (Oct 2026): before this, 3000+ "incidents" were predicted on the grid** -- parked cars re-opened a "stopped" incident every few seconds. A stopped car is now one incident per stop | watch the count stay at 0 through gridding and pace laps; check a restart after a caution is not a burst of "lost speed" |
| `CarIdxEstTime` live gaps | track iRacing's own F2Time gaps between updates, and move every frame | on the fake: median difference under 0.5 s | watch a close pair: the live interval should move smoothly and land on iRacing's gap at the line |
| `SessionLaps` + leader laps | a lap-limited race ends when the leader completes `SessionLaps` | the clock ran past the finish in a real race because the old code used the spectator's own laps remaining | the banner should say "Final lap" as the leader starts it |
| `QualifyResultsInfo/Results` `Position` | 0-based | iracedeck issue #624 says so | the grid shows pole as 1 |
| `CarDesignStr` | "pattern,colour1,colour2,colour3" | goirsdk field list | fallback car colours match the default paint |
| iRacing's local render server `127.0.0.1:32034/pk_car.png` | draws a car with its paint scheme, number, and the custom paint .tga given in `carCustPaint` | undocumented by iRacing; parameters from SIMRacingApps and the halvar20000/iracing-overlays livery overlay (which found the %20 and nested-MX-5 traps); tested here only against a stand-in | with iRacing running, the Liveries tab should count pictures for the whole field within a minute; a driver with a Trading Paints paint should show it; watch for every car looking like a Skip Barber/FF1600 (an unknown CarPath) |
| Trading Paints paint files | `Documents\iRacing\paint\<CarPath>\car_<UserID>.tga`, team events `car_team_<TeamID>.tga` | as used by the same overlay | compare one driver's picture with their paint on track |
| OBS `GetSourceScreenshot` | a still of the iRacing source without the overlay | obs-websocket v5 protocol docs; tested against a stand-in server | Liveries tab: connect, photograph one car, check it on the driver card |

Two traps found while building it. Camera cuts splice the spectator kinematics from two cars,
so the map builder now voids its lap on any `CamCarIdx` change (tested). And `lead_change`
needed a player car, so a spectator never saw a lead change; `class_lead` covers that.

### Engineer additions (fuel planner, lap log, head-to-head, spotter)

| Derived value | Inputs | Validation |
|---|---|---|
| fuel per lap (changed) | as before, now leaving out laps that touched pit road and the partial lap after connecting | **From the real Indianapolis capture**: racing laps 2.44-2.51 L, in-lap 2.21 L. Counting the in-lap (or a partial first lap) understated the burn -- the dangerous direction. The lap counter also went back (33 -> 32 at the end, 0 -> -1 -> 0 on the grid); that restarts the count. Tested with the real per-lap numbers. |
| fuel plan | burn, fuel aboard, `DriverCarFuelMaxLtr` x `DriverCarMaxFuelPct`, laps left, `LapDistPct`, `PitSvFuel` | Unit-tested arithmetic. **Assumes `SessionLapsRemainEx` counts the lap in progress** (25 on lap 1 of 25), as the fake does; not yet confirmed on a lap-limited race -- the panel shows "flag at the end of lap N" so it can be checked at a glance. Timed races estimate laps from the clock, flagged as such. |
| sector times (fixed) | as before | First sight of a car mid-sector used to time that partial sector and bank it as the car's best (22.3 s against a real 38.9 s in the fake), corrupting its theoretical best. Timing now starts at the next sector line. Each lap's own sectors are kept separately, so a void sector is empty for that lap rather than showing the previous lap's. |
| head-to-head (`vsYou`) | the two cars' last/best laps, pace, best sectors | Differences only; unit-tested. |
| spotter distances (`nearby`) | `CarIdxLapDistPct` gap x `TrackLength` | `TrackLength` is rounded to 10 m in the session info, about 0.2 % -- under a metre at spotter range. Closing is a 1.5 s least-squares slope. Unit-tested. |
| `CarLeftRight` | plain | **Never yet seen from the live sim** -- it is not saved in .ibt files and no live capture includes it. Decoded with irsdk's own table; the fake produces every value. |

### Spectator-first additions

| Value | Inputs | Validation |
|---|---|---|
| reference car | chosen > `PlayerCarIdx` (if a car) > `CamCarIdx` | Unit-tested in all three modes. Switching cars resets everything measured from the old one (no fake "P8 -> P3"). |
| per-car speed | `CarIdxLapDistPct` change x `TrackLength` / time | **On the fake against its true speed** (camera car, spectating): median error < 0.3 m/s, 95th percentile < 2.5 m/s (smoothing lag under hard braking). **Not yet compared with the sim**: if the sim's network-updated positions for other cars are jerky, the smoothing (0.1 s) may need raising. |
| gap to the followed car per lap | each car's line-crossing time minus the reference's, same lap number | Unit-tested. |
| stints, next stop | stops watched entry to exit | A stint is only counted if we saw it start (race start or a watched stop). "Next stop" is stint start + longest stint: a pattern, not fuel. |
| refuel rate | your `FuelLevel` rising while `PlayerCarInPitStall` | **Real Indianapolis stop: 2.60 L/s** (31 L in ~12 s). Unit-tested; the fake now refuels at that rate. |
| tyre sets | `TireSetsUsed`, `TireSetsAvailable`, `*TiresUsed` etc. | Present in the real 296 .ibt (288 vars). Plain. |
| compound names | `DriverInfo/DriverTires` | Real file: index 0 "Hard", 1 "Wet". This list is for YOUR car's class; other classes show the index only. |
| driver changes | `DriverInfo/Drivers[].UserID` for a CarIdx changing in the session info | **Not yet seen in a real team race.** Assumes DriverInfo lists the CURRENT driver per car and is refreshed at a swap; the fake does exactly that. |
| `CarIdxSteer` | plain, every car | Present in the variable list; not yet compared with what is seen on screen. |

### Share online (suite)

| Item | Validation |
|---|---|
| cloudflared output | **Real cloudflared 2026.10.0 (Linux) run** through `suite/share.py`: startup lines as expected; with Cloudflare unreachable it named `https://api.trycloudflare.com/tunnel` in its error, which was first mistaken for the link (fixed, tested). **A real share worked on Andrew's PC (2026-10-15)**: Share online produced a link and the dashboard opened through it in a browser and ran solidly. (Whether the "Registered tunnel connection" line or the 8 s fallback made it go live was not recorded.) |
| Windows job object (tunnel ends with the app) | **Not yet run on Windows.** Without it the tunnel is still stopped on a normal close. |
| Relay through the tunnel | **WebSockets through Cloudflare's tunnel work** (the 2026-10-15 share: live dashboard through the link). Guest token, read-only, 4003 on stop: tested on the local relay. **Stop sharing verified for real (2026-10-15)**: the open link stopped working. |

Lap Analyzer over the share link (`/analyzer/?token=GUEST`): tested through the suite's own relay
(session, half-rate telemetry, cut off with 4003 when sharing stops). **Not yet tried through a real
Cloudflare tunnel**: check the page connects over wss:// and that the engineer's view keeps up.
Measured on Andrew's Indianapolis .ibt: a telemetry message is ~6.4 KB, ~0.3 KB after the
connection's compression, so ~10 KB/s per engineer at 30 a second.

### Race strategy (Oct 2026) -- NOT yet run against the sim

| Value | Inputs | Validation |
|---|---|---|
| caution | `SessionFlags` caution / caution_waving, `PitsOpen` | Unit-tested and in the fake (oval-style caution, pits closed for its first minute). **`PitsOpen` never read from the sim yet**: check it exists and flips under a caution on an oval. Road racing in iRacing has no full-course cautions, so this path only matters on ovals. |
| stop cost under caution | stops watched this session, re-scaled by the field's slowdown (lap pace / speed now) | Unit-tested; ~2.2x slowdown in the fake. **Check against a real stop under caution** (the pace car's speed, the lane speed and whether the field bunches change it). |
| rivals' next stop | stint lengths watched (own longest, class middle, your tank for the same car) | Unit-tested. A forecast, not a reading: a car that short-fills or saves fuel will not match. The fake's other cars stop at random, so their forecasts in the fake look poor. |
| finish projection per option | time behind the leader + pace x laps to go + stops owed x green loss | Unit-tested. Assumes everyone keeps their pace: compare with a real finish. |
| driver change | `DCLapStatus`, `DCDriversSoFar` | **Shown raw; meaning not checked.** The team panel's shares are from the driver names seen in the car. |

### Live timing, replays, weather outlook (Oct 2026) -- NOT yet run against the sim

| Value | Inputs | Validation |
|---|---|---|
| running order (`livePos`) | `CarIdxLap` + `CarIdxLapDistPct` | Unit-tested, including a lap counter stepping before the lap fraction. **Check at a real start and through pit stops** that it never flickers between two cars side by side. |
| held picture while a replay is watched | `IsReplayPlaying`, `ReplaySessionTime`; `SessionInfo/Sessions/ResultsPositions` | Bridge-tested with a fake replay. **Assumed, not seen: that ResultsPositions keeps updating from the server while the replay plays.** If it does not, positions hold too, and the note says so. |
| meeting time and place | `CarIdxEstTime`, `CarClassEstLapTime`, lap pace | Unit-tested on a lap with slow and fast parts: counts down evenly and stays in one place. **Check the marker on a real track.** |
| weather outlook | `SolarAltitude`, `TrackLatitude`, WeekendOptions `Date`, `SessionTimeOfDay`, temps, humidity, `Skies`, `TrackWetness` | Physics checked against the fake's real sun path (37 N, June). **Not compared with iRacing's own forecast.** The track-heating coefficient is a typical asphalt value until a session has enough sun change to fit it. `AirPressure` is assumed to be in pascals; `WindDir` is shown as given (compass words), its convention (from or towards) not checked. |
| race report in the app window | pywebview save dialog | Unit-tested with a stand-in window. |

### Lap Analyzer: driver laps (any car as a reference)

| Value | Inputs | Validation |
|---|---|---|
| another car's lap time | `CarIdxLapDistPct` crossing 1 -> 0 between two updates, interpolated; replaced by `CarIdxLastLapTime` when within 0.1 s | Unit-tested at 30 updates/s with crossings between updates: within 0.002 s. On Andrew's Indy lap replayed through the recorder: 83.363 s against 83.361 s. **Not yet seen against the sim's own network-updated positions.** |
| clean lap | no pit road, `CarIdxTrackSurface` OnTrack (3) throughout, no gap over 1 s, no backwards step over 0.2 % or forward jump over 5 % | Unit-tested (joined mid-lap, pit lap, position jump each lose exactly that lap). **If the sim's positions for other cars step backwards slightly, every lap would be thrown out: check the count in a real session.** |
| pace rating (% of recorded drivers) | 100% = middle (or fastest) best lap of recorded drivers in an iRating range, same layout and car (`DriverInfo/Drivers[].IRating` at the time of the session) | Unit-tested. Research (2026-10-16): the 100-107% scale is a community convention from F1's 107% rule; sites that attach skill words to its bands say they are subjective and cite no data, and the one site that published pace by iRating (simracingstats.com) stops in 2023. So no band labels are shown; the iRating comparison uses only drivers recorded here. |
| speed, gear, steering of another car | speed from lap-distance movement x `TrackLength`; `CarIdxGear`, `CarIdxSteer` plain | Speed unit-tested (corner minimum within 1 m/s). Gear and steering not yet compared with the sim. |

### Lap Analyzer: setup help (Fix by symptom, Your driving, live verdicts)

| Value | Inputs | Validation |
|---|---|---|
| driving habits per corner | `Brake`, `Throttle`, `SteeringWheelAngle`, lap time, on the clean laps of a file | Thresholds set on Andrew's Indianapolis laps (2026-10-10); unit-tested with each habit placed in its own corner. |
| live balance per run | `SteeringWheelAngle`, `YawRate`, `LatAccel`, `Speed`, `Brake`, `Throttle`, `VelocityX/Y` live, every other update, clean laps only | Same measure as the file-based Suggestions (tested on files and on a simulated live session). **Not yet seen on a real live session**: check that the steering fit finds enough gentle cornering at each track. |
| "did the change help" | median clean lap of each run; called only beyond twice the standard error and 0.05 s | Unit-tested. A lap in progress when the setup changes is dropped (setups only change in the garage). |
| predicted changes | pairs of your runs (files or live) differing in one setting: effect per unit on each phase's steering at the limit (any track) and on lap time (same track) | Unit-tested against hand arithmetic. Size = steering to remove to reach the run's middle / effect per unit, rounded to the steps you have run, within the car's limits. Untested settings: knowledge-table direction and a typical first step (PRIOR_STEP), labelled as an estimate. **Real accuracy unknown until a few changes have been tested.** |
| garage calculations | `CarSetup` AeroBalanceCalc (front downforce, ride heights at speed), splitter height, weights, for every setup seen | Exact for the garage's own numbers when a pair differs in one click (linear per setting; combined changes are summed, which is an approximation). Unit-tested. |
| whole setup for a track | your fastest run there, else the latest at the same downforce level (296: iRacing's trim list), else the current setup; wing moved by your best setups per level or 1 degree per half level (estimate); predicted fixes applied | Unit-tested. The sheet is for typing into the garage: .sto files cannot be written. |
| coaching: average vs best | median of the clean laps at every distance point (time = mean of the lap times); per corner against the best: time, braking point, apex speed, throttle point, coasting, lap-to-lap spread | Checked on Andrew's Indianapolis .ibt (22-lap average 1:24.938 vs best 1:24.033). Garage61 CSV laps carry no setup or balance, so they get driving tips only. |
| live ride height | `{LF,RF,LR,RR}shockDefl` live; slope per corner fitted on an .ibt of the car (`*rideHeight` vs `*shockDefl`, laps above 20 m/s); offset = garage `RideHeight` with the shock deflection when the car stands still on the current setup | **iRacing's live telemetry has no `*rideHeight` channels** (only .ibt files do): a real live session (2026-10-18, 296 GT3 at Indy, through a share link) showed none, and the 336-variable live scan above lists shock deflection but no ride height. Fit on Andrew's 296 Indy .ibt: slope -1.57/-1.60/-1.46/-1.45 (LF/RF/LR/RR), 2.2-2.5 mm residual (tyre squash). **Check against an .ibt of the same run** once. |
| engineer (live, N clean laps per round) | the live balance per run and "did the change help" above; plan made once per run after N clean laps; changes found back in the garage by comparing the new setup's values | Simulated three-round session unit-tested (plan, wrong value caught, faster kept with a learned size, slower put back and not repeated past that value at this track). **Not yet run on a real session.** Check that the out lap after a garage reset is not counted. |
| per-car directions (CAR_KB) | iRacing's user manuals, October 2026: reversed scales, TC/ABS meaning, positions out of number order, limits, aero targets, downforce trims; see docs/CAR_MANUALS.md | Matched by CarPath + CarScreenName; only the Ferrari 296 GT3's names are confirmed from a real file. **Check the matching on each new car** (Notes tab shows the car the analyzer found). McLaren 720S front ARB and Mercedes GT3 2020 ABS: manual unclear, the app says so. |
| .sto setup files | -- | **Not readable**: iRacing encrypts them (Braking Lab's setup FAQ; the sample is high-entropy). Garage .htm exports are the readable route; waiting on a sample. |

| active reset (lone practice) | the lap distance jumps mid-lap (back more than 0.2-0.3%, or on faster than the car can move) while the lap clock runs: .ibt laps get `reset` (note "active reset", not timed), live laps are not saved for Coaching, PitWall lap entries get `reset` (kept out of pace) | From iRacing's description (the car is put back at the saved point, the current lap time is invalidated). Tested on synthetic files only. **Check on a real .ibt with resets**: whether `Lap` / `LapCurrentLapTime` also change at a reset, and what `LapLastLapTime` says for that lap. |
| clean laps | no pit, incident (`PlayerCarMyIncidentCount`), moment off track (`PlayerTrackSurface` = 0) or reset; time within the usual range (median, slow side max(3 robust sd, 1%), quick side max(4, 5%)) and 7% of the fastest such lap | `PlayerTrackSurface` 0 = off track per the SDK's enum; seen in the 296 Indy file only with incidents. |

| Live telemetry (analyzer view=live) | your car's own variables: `SteeringWheelPctTorqueSign` (force at its limit = 0.99+), `{LF,RF,LR,RR}brakeLinePress` (bar), `BrakeABSactive`, `{..}shockDefl`, `EngineWarnings`, `SessionFlags`, `PaceMode` (shown only while `SessionState` is parade or a caution flag is out: it holds the start type all race), `PitSvFlags`/`PitSvFuel`/`dp*TireColdPress`, `PlayerTireCompound` (named from `DriverTires`), `TireSetsAvailable`, `FastRepairAvailable`, `ManifoldPress`, `P2P_Count`, `WindDir`/`WindVel`/`YawNorth`, `CarDistAhead`/`CarDistBehind`, and `CarIdxPosition`/`CarIdxEstTime` (now sent to the analyzer) | Seen in the 296 Indy .ibt: brake line pressure 0 at rest, up to ~95 bar; `PaceMode` stays 1 all race; `FuelUsePerHour` swings with every lift (so the page uses fuel used this lap instead). **To check live:** that `WindDir` is where the wind comes FROM (head/tail wind would be reversed otherwise); "shocks at the stop" (a corner at 97% of the deepest travel seen this session, once that has stood 30 s) against a setup known to hit its bump stops; the race-order gaps against PitWall's. |

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
running substitutes for it. A REPLAY of one now exists (below); a live oval race,
driven or spectated, is still needed for the flags.

### Talladega replay (NASCAR trucks, Oct 2026)

`Talladega-Super-Speedway-Race_2026-10-25_192220.jsonl.gz` (not in the repo; 2490 s, 26
trucks, start, three cautions, green-flag stops), recorded while a replay played
(IsReplayPlaying True, PlayerCarIdx 63, pace car idx 62). What a replay does NOT carry:

- **SessionFlags is 0 throughout** -- no green, no caution, no per-car flags
  (CarIdxSessionFlags 0, CarIdxPaceFlags 0). PitsOpen is True throughout. So the
  caution is read from the field's speed instead (`caution.inferred`, PROTOCOL.md);
  it found the three cautions to within ~20 s of the field slowing. Whether LIVE
  ovals send the caution bits is still unverified.
- **CarIdxEstTime is 0.0 for every car** and **CarClassEstLapTime is 1.0**. Gaps
  were all 0.000. Now: an EstTime of 0 is unknown, a class lap under 5 s is
  unknown, and the gap falls back to the car's best lap.
- **The camera is on the pace car ~40% of the time** under caution: the reference
  stays on the last car the camera showed (`ref.source = "camera_last"`).
- **Cars blink out of the world** (not_in_world for 1-3 s, then back where they
  were): one driver a dozen times in 10 minutes. Not a tow: a tow now needs the car
  back in its stall / on pit road, or gone 8 s.
- **The field spawns into its pit boxes** at the start (not_in_world -> in_pit_stall)
  and drives out: not a stop (no lane entry was watched).
- A lap behind the pace car took ~137 s against ~52 s green: laps over 1.5x the
  car's best are kept out of pace.

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
