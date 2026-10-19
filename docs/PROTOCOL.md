# Pitwall wire protocol (schema 1)

All messages are JSON objects with a `type`. Transport is WebSocket text frames.
Bridge → relay → viewers. Viewers → relay → bridge only for `cmd` and `resend`.

## Provenance rule

| Where a value lives | What it means |
|---|---|
| plain key (`player.fuelL`, `weather.airTempC`, `cars.<idx>.f2TimeS`) | copied verbatim from an iRacing variable, unit converted only where the key name says so |
| under a `derived` key | computed by `pitwall/normalize.py`; the UI must mark it (dotted underline) |
| `player.tires.*.wearPct`, tread temps | copied verbatim, but iRacing only refreshes these at pit stops; the UI marks them "pit snapshot" |
| `null` | iRacing did not provide it in this session / car / context. Never substituted. |

## Messages

### `hello` (bridge, on connect)
```json
{"type":"hello","role":"bridge","schema":1,"source":"live","commands":false}
```

### `session` (bridge, whenever iRacing's session YAML changes)
Subset of the YAML string. `drivers[]` is the entry list (CarIdx, UserName,
TeamName, CarNumber, CarClassID, CarClassShortName, CarScreenNameShort, IRating,
LicString, IsSpectator, CarIsPaceCar, ...). `sessions[]` describes each session in
the event (practice/qual/race). `sectors[]` gives sector start percentages.
`track` and `event` carry WeekendInfo fields. `driverCar` carries fuel capacity,
shift lights, redline, pit-lane position for the player's car.

`cameras[]` is `CameraInfo/Groups`: `{num, name, scenic, cameras:[{num, name}]}`. Group numbers
differ by track, so camera commands always take them from here.

### `map` (bridge, once per track config; from cache or built from the first clean lap)
```json
{"type":"map","trackId":123,"config":"Grand Prix","bins":720,
 "points":[[x,y],...720 pairs in a unit box, index = LapDistPct*720],
 "startFinishIndex":0,"lapLengthM":5085.4,"closureErrorM":3.9,"source":"velocity_integration"}
```
Entirely derived (see `pitwall/trackmap.py`). `closureErrorM` is how far the raw
integration missed closing the loop before correction; treat > 30 m as suspect.

### `state` (bridge, 30 Hz by default, 60 Hz in the suite; delta-encoded)
```json
{"type":"state","full":true,"t":<SessionTime>,
 "session":{"num","state","stateRaw","flags":["green"],"flagsRaw","timeS","timeRemainS","lapsRemain","timeOfDayS","paceMode"},
 "weather":{"airTempC","trackTempC","airPressurePa","airDensity","windMs","windDirRad","humidity","skies","fogPct",
            "precipitationPct","wetness","wetnessRaw","declaredWet","solarAltitudeRad","solarAzimuthRad"},
 "player":{"carIdx","speedMs","rpm","gear","throttle","brake","clutch","steerRad","fuelL","fuelPct","fuelUsePerHourKg",
           "waterTempC","oilTempC","oilPressBar","fuelPressBar","voltage","engineWarnings":[...],"lap","lapCompleted",
           "lapPct","lastLapS","bestLapS","currentLapS","deltaToBestS","deltaToSessionBestS","onPitRoad","inPitStall",
           "pitstopActive","trackSurface","position","classPosition","incidents":{"my","team","driver"},"towTimeS",
           "pitRepairLeftS","pitOptRepairLeftS",
           "pitService":{"flags":[...],"fuel","pressuresKPa":{LF,RF,LR,RR},"tireCompound"},
           "dc":{"brakeBias","abs","tractionControl","antiRollFront","antiRollRear","fuelMixture",...},
           "tires":{"LF":{"carcassTempC":[l,m,r],"wearPct":[l,m,r],"coldPressureKPa","pressureKPa"},...},
           "isOnTrack","isInGarage","isReplayPlaying","replayFrame",
           "carLeftRight":"off"|"clear"|"car_left"|"car_right"|"car_left_right"|"two_cars_left"|"two_cars_right",
           "carLeftRightRaw"},
 "camera":{"carIdx","group","num","state":[...],"stateRaw","replaySpeed","replaySlowMotion",
           "replaySessionNum","replaySessionTimeS","replayFramesFromEnd"},
 "derived":{"fuelPerLap","lapsOfFuel","fuelPerLapSamples",...,
            "plan":{"tankL","lapsLeft","lapsLeftIsEstimate","finishLap","needL","stops","windowOpenLap",
                    "windowCloseLap","boxNow","fillToFull","addThisLapL","addThisLapSpareL","addAtOpenL",
                    "addAtOpenSpareL","addAtCloseL","spareAtFlagL","spareAtFlagLaps","menuFuelL","menuVsPlanL"},
            "weatherTrend":{"spanS","change":{key:delta},"series":{key:[...]}} | null,
            "engineTrend":{...same shape...} | null, "ggPeakG"}}
```
`camera` is what the broadcast is looking at: `CamCarIdx`, `CamGroupNumber`, `CamCameraNumber`,
`CamCameraState` (decoded) and the replay variables. Spectating is a replay at `replaySpeed` 1;
any other speed, or slow motion, means the picture is not the race as it happens.

### `cars` (bridge, 15 Hz by default, 30 Hz in the suite; delta-encoded; keyed by car index as a string)
```json
{"type":"cars","full":true,"t":<SessionTime>,
 "cars":{"12":{"idx":12,"num":"34","name":"...","team":"...","classId","className","car","iRating",
               "pos","classPos","lap","lapCompleted","pct","f2TimeS","estTimeS","onPitRoad","surface",
               "lastLapS","bestLapS","bestLapNum","compound","flags":[...],"fastRepairsUsed",
               "p2pActive","p2pCount","gear","rpm","paceFlagsRaw",
               "derived":{"intervalS","closingSPerLap","catchLaps","stopsSeen","lastStopS","lastStopLap",
                          "lapsSincePit","inPit","stintLaps","stintAvgS","degSPerLap","sectorS","bestSectorS",
                          "theoreticalBestS","bestLapLeftS",
                          "toPlayerS","classGapS","paceS","youGainSPerLap","youCatchLaps","catchesYouLaps"}}},
 "derived":{"sessionBestSectorS":[...],"isRace":true,
            "pitLoss":{"laneLossS","samples","boxMedianS","boxSamples"},
            "rejoin":{...pitLoss, "options":[{"kind":"zero_stationary"|"typical_stop"|"your_last_stop",
                      "stationaryS","lossS","pos","classPos","ahead":{"idx","gapS"},"behind":{"idx","gapS"},
                      "traffic":[{"idx","onTrackS"}]}]},
            "undercut":[{"idx","kind":"they_stopped"|"you_stopped","stopLap","lapsSince","preStopToPlayerS",
                         "toPlayerS","inPit","series":[...],"trendSPerLap","settledToPlayerS",
                         "afterYourStopS"|"afterTheirStopS"}],
            "traffic":[{"idx","kind":"you_catch"|"catches_you","inS","atPct"}],
            "battles":[{"id","classId","cars":[idx,...],"gapsS":[...],"closingSPerLap":[...],
                        "catchLaps":[...],"forClassPos","forPos"}]}}
```
`battles` (race only): cars that are neighbours IN THEIR CLASS with trusted F2Times less than
1.0 s apart; a pair stays in until the gap passes 1.5 s. Pairs that touch form one group,
front to back. `gapsS[k]` is between `cars[k]` and `cars[k+1]`. `closingSPerLap[k]` / `catchLaps[k]`
are the behind car's own closing trend, and only when the pair is also adjacent overall --
otherwise null, because that trend is measured against some other car. Neither car may be on
pit road. Ordered by class position of the front car.
Engineer additions: each car's `derived.vsYou` is {"lastLapS","bestLapS","paceS","bestSectorS":[...]},
that car minus you (negative = they are quicker). The cars message's `derived.nearby` lists cars
within 150 m along the track: [{"idx","meters" (+ ahead / - behind),"closingMs" (+ = the gap is
shrinking),"inPit"}], from the lap-distance gap and the session's TrackLength. `state.derived.plan`
is the fuel and stop plan (see normalize.fuel_plan for exactly what each key means). Lap entries in
the feed also carry `sectors` (that lap's own sector times, null where void) and, for your car only,
`fuelL`, `refuel` and `incL`.

Sign conventions: `toPlayerS` is + when that car is BEHIND you. `youGainSPerLap` is + when the
trend is good for you (a car ahead coming back, a car behind dropping back). `onTrackS` uses
the same sign as `toPlayerS`. Race-only values (`toPlayerS`, `classGapS`, `rejoin`,
`undercut`) are null or empty outside a race, where F2Time is a best lap rather than a gap.
`f2TimeS` is iRacing's `CarIdxF2Time`: in a race it is time behind the leader;
in practice/qualifying it is the car's fastest lap. `derived.intervalS` is only
meaningful in a race.

### `feed` (bridge, append-only: lap history and race events)
```json
{"type":"feed","full":true,"seq":812,
 "laps":[{"seq","idx","lap","lapS","pos","classPos","gapS","classGapS","toPlayerS","pit","t"}],
 "events":[{"seq","t","lap","kind",...}]}
```
`full:true` carries everything this run of the session has produced (sent on connect,
on `resend`, and when the derived history resets); `full:false` carries only entries with
`seq` above the previous message's. Clients ignore partial feeds until they have a full
one, and drop entries whose `seq` they have already seen. Everything in it is derived.
Event kinds: `pit_in` (carLap, pos, classPos, towed -- from the track straight into the box),
`pit_out` (stationaryS, laneLossS, stop -- stops seen, pos, classPos), `fastest_lap` (lapS, classId,
overall, lapNum, prevS -- never a car's first lap), `incident` (delta, total, limit), `behind_closing` / `you_closing` (gapS,
rateSPerLap), `position` (fromPos, toPos -- class position), `lead_change`, `flag`,
`engine` (warnings), `tow` (towS), `rain_start`, `rain_stop`, `track_wetness`,
`declared_wet`, `pit_battle` (preS, nowS, first),
`car_incident` (idx, points, total, driverTotal, limit, lap, sessionNum, replayT, anchor, surface,
causes, single, maybeSeveral -- a car's counted incident points went up; see Incidents below),
`off_track` and `slowed` (idx, lap, pct, sessionNum, replayT -- seen in the telemetry, not points),
`class_lead` (idx, prevIdx, classId -- every
class, held 5 s, with or without a player car; `lead_change` remains the player's class only).

`session.options` carries `IncidentLimit` (a number, or "unlimited"),
`FastRepairsLimit`, `StandingStart` and `NumStarters` from WeekendOptions.

### Incidents
iRacing hides other cars' incident counts from a spectator until the race is over (observed:
every count appeared only after the chequered flag). So the live list is a prediction from
telemetry, and the official counts are compared with it when they arrive.

`race_incident` (one per incident, emitted once it has settled: 3 s with no new signal):
`{id, idx, what, parts, likelyPoints, confidence, signals, near, contactWith, lap, pct, classId,
sessionNum, replayT, endT, surface}`.

| signal | source |
|---|---|
| `off_track` | `CarIdxTrackSurface` on_track -> off_track (iRacing's own flag) |
| `slowed` / `stopped` | derived: under 35% / 5% of the field's speed at that 0.5% of the lap, for 1 s / 2 s |
| `backwards` | derived: lap distance falling faster than 0.003 lap/s (facing the wrong way) |
| `tow` | the car leaves the world, or appears in its pit box without driving the lane |
| `damage` | `CarIdxSessionFlags` gains `repair` |
| `near` / `contactWith` | cars within 0.4% of a lap when it began / and losing speed with it |

`what` is the most serious of `parts`: `contact` (lost speed with a car alongside that lost it
too), `spin` (backwards, or off the track while losing speed), `lost_speed` (loss of control or
a wall), `off_track`, `tow`, `damage`. `likelyPoints` follows the Sporting Code 3.5.1 table
(paved: off 1x, loss of control / object 2x, heavy contact 4x; dirt: contact 2x) taking the
most serious part (3.5.2). Detected only at the live edge, so a replay never re-reports one.
`replayT` is where a replay of it starts, on the session clock of `sessionNum`.

`official_incidents` `{sessionNum, cars: [{idx, official, predicted}]}`: when most of the field's
counts step at once (the post-race publication), each car's official total against the sum of
its predicted `likelyPoints`. `cars.derived.predictedIncidents` is that running sum per car.
`car_incident` is still produced for a single car's count rising (a session where counts are
visible), with the earlier anchor rules. Point table helpers: `incident_causes()`.

`state.derived.liveEdge` = `{tLive, tPlay, behindLiveS, atLive}`: SessionTime, the replay
playhead (ReplaySessionTime while a replay is playing), the difference, and whether the
picture is within 4 s of live at normal speed. While not at live, the CarIdx data is the past,
so race events and incident detection pause. `cars.derived.liveEdge` is the same.

### Live gaps and race length (cars.derived)
Per car, from CarIdxEstTime, live rather than waiting for F2Time: `liveIntervalS`/`intervalLaps`
(car ahead overall), `liveGapS`/`gapLaps` (leader), `liveClassGapS`/`classGapLaps` (class leader),
`liveClassIntervalS`/`classIntervalLaps` (car ahead in class). Same class: EstTime(ahead) -
EstTime(behind) plus a class lap (`classEstLapS`, from CarClassEstLapTime) per whole lap; across
classes the lap fraction is scaled by the behind car's class lap. Whole laps are separate so a
lapped car reads "+1 lap".

`cars.derived.race` = `{totalLaps, leaderLapsDone, leaderLap, lapsToGo, timeRemainS, timeLimited,
lapsByTime, endsBy, white, checkered}`: laps from the session's SessionLaps and the LEADER's
completed laps (SessionLapsRemainEx is the player's own and empty for a spectator); time from
SessionTimeRemain; with both limits, `endsBy` picks whichever ends first at the leader's pace.

### Broadcast messages
- `session.grid` `{source, sessionNum, rows: [{pos, classPos, idx, timeS, lap}]}` from a race's
  QualifyPositions or QualifyResultsInfo; iRacing's 0-based positions shown from 1.
- `director` (bridge): `{owner: you|race|grid|battle, on, mode, resume, style, holdS, autoGrid,
  gridHoldS, gridLeft, gridTotal, battle: [idx]|null, note, current: {car, num, group, groupName,
  reason, gridPos?}, styles}`. One owner moves the camera at a time. Any `camera` command (a cut by
  hand) makes the owner `you`; `grid` and `battle` are temporary and hand back to `resume` when they
  end. The grid walk starts by itself when a race's pace laps begin (`autoGrid`) and ends at the green.
- `livery` (bridge -> relay keeps one per key): `{key: "<car>:<kind>", car: "<CarID>_<TeamID or UserID>",
  kind: "render"|"photo", image: data URI, at, custom?}`. `render` is iRacing's own picture of the car
  from its local server (pitwall/carrender.py), with the Trading Paints paint when one is on disk
  (`custom: true`); `photo` is an OBS still from the broadcast.
- `livery_status` (bridge): `{connected, error, sweeping, done, total, lastKey, config}`.
- `assets` (relay): `{files: [{name, bytes, type}]}`; served at `/broadcast/assets/<name>`.
- `asset_put` / `asset_del` (producer -> relay): `{name, data: base64}` / `{name}`; 30 MB cap,
  images, video, fonts only.
- cars carry `classEstLapS`, `carDesign` (CarDesignStr), `helmetDesign`, `carId`, `userId`, `teamId`;
  session drivers also carry CarNumberDesignStr, CarSponsor_1/2 and ClubID for the car renders.

### Following a car (spectator first)

Every relative value (`toPlayerS`, `youGainSPerLap`, `vsYou`, `rejoin`, `undercut`, `traffic`,
`nearby`, battle, gap chart) is measured from a reference car. In the SHARED stream that
is reported as `cars.derived.ref = {"idx", "source": "your_car"|"camera", "own", "camera"}`:
your own car when driving, else the camera car while spectating. No viewer can move it.
The "player"/"you" key names are kept for compatibility; they mean the reference car.
Fuel, tyre sets, in-car settings and iRacing's left/right spotter exist for your own car
only (`ref.own`).

A viewer may follow another car **for themselves only**:

- `{"type":"focus","idx":N}` (viewer → relay) chooses it; `"idx":null` goes back to
  automatic. Any role may send it, guests included, because it changes nothing for anyone
  else. The relay answers `{"type":"focus_result","ok","idx","error"}`; it refuses a car
  beyond `MAX_REFS` (12) different cars followed at once. A viewer re-sends its choice
  after a reconnect; the relay forgets it when the viewer leaves.
- `{"type":"refs","idxs":[N,...],"fresh":N|null}` (relay → bridge): the cars viewers
  follow, sent whenever that set changes and to a bridge that connects while any are
  followed. `fresh` is a car just chosen; its next view is sent whole.
- `{"type":"refview","ref":N,"full",...}` (bridge → relay → ONLY the viewers following N),
  delta-encoded like `cars`, at the cars rate: `cars` (`"<idx>"` → the per-car fields in
  `normalize.REF_CAR_FIELDS`, measured from N), `derived` (`ref` with `"source":"chosen"`,
  `rejoin`, `undercut`, `traffic`, `nearby`) and `gaps` (as `refgaps.series`, from N).
  Computed by `normalize.relative_to` with a `RefView`: the same Deriver methods as the
  shared stream, with that car's own history. The client layers it over the shared `cars`.

`{"type":"refgaps","full":true|false,"ref":N,"series":{"<idx>":[[lap,gapS],...]}}` (bridge):
each car's gap to the shared reference at each lap line -- the moment it crossed minus the
moment the reference crossed (sector-timer interpolated when available). Derived. `full:true`
on a reference change, a reconnect or a resend; otherwise only the points added since (a gap
at a lap line never changes once it exists): append them per car, keeping 200 per car. The
relay keeps the merged chart, so a viewer that joins late is sent it whole.

`derived.weatherTrend` / `derived.engineTrend` (state): what the weather and your engine have
done this session, from samples every 10 s (15 min kept) and 5 s (30 min kept, only while the
player block is live). `change` is last minus first sample over `spanS`; `series` is thinned to
at most ~60 points for a sparkline. Never a forecast: iRacing publishes none. `ggPeakG` is the
session's peak combined g from the 360 Hz accelerometer samples.

Per-car additions under `derived` (any car): `speedMs` (from lap-distance movement and the
track length; iRacing does not give other cars' speed), `topSpeedBestMs`, `consistencyS`,
`gridPos`, `gridClassPos`, `posGained`, `classPosGained`, `lapsLedSeen`, `classLapsLedSeen`,
`stintsSeen`, `stintStartLap`, `lapsThisStint`, `longestStintLaps`, `nextStopLapEst`,
`compoundChanges`, `fastRepairsLeft`, `drivers` (team races: [{"name","timeS","laps","current",
"sharePct" (share of the time driven since we connected),"evenPct" (100 / drivers),"next" (the driver
not in the car with the least time: a suggestion for an even rotation, not the event's rule)}]),
`stopDue` (below).
Plain per car: `steerRad` (CarIdxSteer). Lap entries also carry `topSpeedMs` and `tx`
(interpolated line crossing). `cars.derived.compoundPace`: per class and compound, the
middle of the cars' last racing laps, `fastest`, `vsFastestS`. Session: `driverTires`
(compound names by index, for your car's class). State (your car): `player.tireSets`,
`player.fastRepairs`, `derived.refuelLps` (measured at your stops), `derived.plan.fuelTimeS`.
New events: `compound_change`, `driver_change`, `adjust` (your in-car changes, reported
once a control has been left alone for 3 s); `behind_closing`, `you_closing`, `position`
and `pit_battle` carry `ref` (the car they were measured from); your `incident` carries `pct`.

### What race control leaves out (cars that are not really doing anything)

`compound` is null where iRacing reports -1 (a car not loaded into the world yet, or gone from
it). A `compound_change` or `driver_change` is announced only within 120 s of the car being on
pit road or in its box (both are only possible there; a difference anywhere else is the session
info catching up and is taken in quietly), and never while a replay is being watched. Pit events
skip cars loading into or leaving the world, and run only in the `parade_laps` and `racing` states
(not cars leaving the pits for the grid, or driving in after the flag). Engine warnings need the
player in the car; oil and fuel pressure need the engine running (500 rpm). Track wetness from or to
"unknown" is not a change.

`cars.derived.sof = {"overall","cars","classes":[{"classId","className","sof","cars"}]}`: strength of
field with iRacing's formula, (1600 / ln 2) x ln(n / sum(exp(-iR x ln 2 / 1600))), over the entries'
`IRating` from the session info (0 or missing left out). iRacing does not publish it in the telemetry.

### Race strategy (cars.derived, race only)

iRacing shares no fuel or tyre data for another car, so a rival's next stop is read from how
long its stints run. Each car's `derived.stopDue = {"lap","lapsLeft","rangeLaps","basis",
"stopsOwed","overdue"}` or null (no stint start seen). `basis`: `your_fuel` (your car: laps
into the stint plus the fuel aboard / burn), `own_stints` (its longest watched stint),
`class_stints` (the middle of the class's watched stints, at least 2; the class's longest once
the car is already past the middle), `your_tank` (a car of the same model as yours: your tank /
your burn). `stopsOwed` needs the race length (`race_laps_left`: `lapsToGo` for a lap race,
else `lapsByTime`).

`derived.caution = {"active","since","pitsOpen","fieldSlowdown","lossGreenS","lossNowS","savingS"}`:
`active` from SessionFlags caution / caution_waving (iRacing runs full-course cautions on
ovals; road races have none). `pitsOpen` is iRacing's `PitsOpen` (null when absent).
`fieldSlowdown`: the middle, over cars on track (at least 3), of (track length / the car's lap
pace) / its speed now. `lossNowS` re-scales the stops watched this session: the lane takes as
long as ever, the field covers `fieldSlowdown` times less track meanwhile, so the loss is the
lane drive minus the same stretch at caution speed, plus the typical stationary time.

`derived.strategy` (shared stream: the reference car; each `refview` carries its own for the
followed car) = `{"basis":"fuel"|"stints","lapsToGo","lossGreenS","options":[...],"best","targets"}`
or null. `basis` is `fuel` for your own car with a fuel plan, else the car's stints. Each option
`{"kind","stops","lossS","netS","finishClassPos","finishAhead","finishBehind","withPace",...}`:
`no_stop` (+`spareL`), `box_now` (+`lap`,`addL`,`rejoin`), `box_latest` (+`lap`,`addL`),
`stretch` (one stop fewer: +`saveLPerLap`,`savePct`, offered up to 15 % of the burn),
`as_forecast` (from `stopDue`), `tyres` (an extra stop: +`tyreGainS` = the stint's
`degSPerLap` x laps on the tyres x laps to go, offered from 0.03 s/lap and 5 laps; `netS` =
`lossS` - `tyreGainS`), `caution_now` (a stop now at `caution.lossNowS`, plus any further stop
still needed at the green loss; only while the pits are not closed). `best` is the lowest
`netS`. The finish projection: each class car's time behind the leader + its pace x laps to go
(when every car has a pace) + the stops `stopDue` says it still owes x the green loss;
`finishAhead`/`finishBehind` = `{"idx","s","guessed"}` (`guessed`: that car's stops unknown).
`targets = {"catch":{"idx","gapS","needSPerLap","gainingSPerLap","catchLaps","targetLapS",
"onCourse"},"hold":{"idx","gapS","closingSPerLap","catchesInLaps","beforeFlag"}}`: the nearest
class car ahead and behind; `targetLapS` = the car's pace minus what it needs per lap.

`stint` trend (`degSPerLap`) uses racing laps only: a lap more than 7 % slower than the
stint's quickest (out-lap, caution, spin) is left out.

State (your car): `player.driverChange = {"lapStatus","driversSoFar"}` (DCLapStatus,
DCDriversSoFar, unverified).

The dashboard's alarms (box now, window open, fuel low, caution, a car close behind or due to
catch before the flag, rain, a black/repair flag on the followed car, incidents near the limit)
are thresholds over these values, per viewer and never sent anywhere. The race report is
built in the page from what it has received (feed, cars, alarms).

### Delta encoding
`full:true` = complete message (sent every 5 s and on every reconnect / `resend`).
`full:false` = only keys that changed since the last message of that type; nested
objects recurse, lists and scalars replace. Client merges with a deep merge.

### `cmd` (viewer → bridge, engineer token only; phase 2)
```json
{"type":"cmd","cmd":"pit","action":"fuel","value":40}
```
Actions: `clear, windshield, fuel(L), lf/rf/lr/rr(kPa, 0=keep), clear_tires,
fast_repair, clear_windshield, clear_fast_repair, clear_fuel, tire_compound(idx)`.
The bridge answers with `cmd_result {ok, echo, error}` and the relay copies it to
the sender. The bridge refuses everything unless started with `--allow-commands`.

Broadcast commands (producer token only; the bridge refuses them unless started with
`--allow-camera`, a separate gate from `--allow-commands`):
```json
{"type":"cmd","cmd":"camera","action":"car","car":"34","group":11,"camera":0}
{"type":"cmd","cmd":"camera","action":"position","position":1,"group":11}
{"type":"cmd","cmd":"camera","action":"focus","focus":"exciting"|"leader"|"incident","group":11}
{"type":"cmd","cmd":"replay","action":"speed","speed":0,"slow":false}
{"type":"cmd","cmd":"replay","action":"search","mode":"to_end"|"prev_incident"|"next_incident"|"prev_lap"|...}
{"type":"cmd","cmd":"replay","action":"jump","session":2,"timeS":812.4,"preroll":5,"car":"34","group":11}
```
Director and liveries (producer token, `--allow-camera`):
```json
{"type":"cmd","cmd":"director","action":"set","on":true,"style":"cinematic"|"onboard"|"tv","holdS":8,"autoGrid":true,"gridHoldS":5}
{"type":"cmd","cmd":"director","action":"grid_walk","holdS":5,"capture":true}
{"type":"cmd","cmd":"director","action":"follow_battle","cars":[12,7]}
{"type":"cmd","cmd":"director","action":"stop"}
{"type":"cmd","cmd":"livery","action":"setup","url":"ws://127.0.0.1:4455","password":"...","source":"iRacing","group":"Chase","auto":true}
{"type":"cmd","cmd":"livery","action":"sweep","missingOnly":false}
{"type":"cmd","cmd":"livery","action":"snap","idx":12}
{"type":"cmd","cmd":"livery","action":"stop"}
```
`jump` cuts to the car (when given), seeks to `timeS - preroll` with
`irsdk_BroadcastReplaySearchSessionTime`, waits 0.4 s for the seek to settle, then plays at
speed 1. It runs on a worker thread so the stream does not stall.
`car` is the car NUMBER as a string (leading zeros matter); `camera` 0 lets iRacing pick within
the group. Against the fake source `cmd_result` carries `"simulated": true`.

### `gfx` (producer → relay → every viewer)
Broadcast graphics state, owned by the relay (not the bridge). A producer sends a patch:
```json
{"type":"gfx","set":{"tower":{"show":true,"mode":"best"}},"by":"Andrew"}
```
The relay deep-merges it (`null` deletes a key) and sends the whole state to everyone:
`{"type":"gfx","rev":12,"state":{...},"by":"Andrew"}`; new viewers get it on connect. Keys the
overlay reads: `tower {show, mode: interval|leader|best|last|stops|incidents, class, rows}`,
`banner {show}`, `card {show, follow: camera|pinned, idx}`, `battle {show, mode: auto|pick, cars}`,
`map {show}`, `ticker {show, pits, incidents}`, `board {show, class}`,
`replay {label, car, show}` (caption for the Replay bug; shown only while not at the live edge),
`grid {mode: auto|on|off, followCamera, solo}`, `stinger {seq, dir, at, enabled}` (a new `seq`
plays the transition; the producer sends the camera command at `theme.stinger.cutMs`),
`ticker {..., incidents, minIncident}`, `tower {layout: overall|classes|class, perClass, ...,
mode: ...|gap|pits|incidents}`, and `theme`: `{preset: imsa|pitboard|daylight, colors: {ink, ink2,
head, headText, text, muted, accent, accentText, fl, plate}, fonts: {display, body}, fontAssets,
caps, radius, names: initial|last|full, rowHeight, text: {series, event, gridTitle}, logo,
bannerLogo, stinger: {video, logo, durationMs, cutMs, sound}, livery: {source: render|photo, x, y, zoom},
layout: {<graphic>: {x, y, s}}}`. A rejected patch gets
`gfx_result {ok:false, error}`.

### `resend` (viewer → relay)
Asks for everything a page needs to start (`session`, `map`, the latest keyframes, the
race feed and gap chart so far, graphics, assets). The relay answers from what it holds,
to that viewer alone, at most every 5 s; the bridge is not involved. A page sends it on
connect, which the snapshot it was just sent already covers.

Message size: only the producer may send large messages (asset uploads). Every other
viewer is limited to 64 KB per message and disconnected (1009) beyond it.

### `relay` (relay → viewer)
`{"type":"relay","bridge":"connected"|"disconnected","engineer":bool,"role":"view"|"guest"|"engineer"|"producer","viewers":n}`

Tokens: view (read-only), engineer (pit commands), producer (camera, replay, gfx), guest
(read-only, revocable: set by the suite's Share online for the links it hands out;
`Relay.set_guest_token(None)` closes every guest with code **4003** "sharing stopped").
Close codes: 4001 bad path or token, 4002 viewer fell behind, 4003 sharing stopped.
The relay serves the dashboard at `/pitwall/`; with `?token=` it connects to its own host.
