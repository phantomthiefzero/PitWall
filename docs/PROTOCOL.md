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

### `state` (bridge, 15 Hz, delta-encoded)
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
           "isOnTrack","isInGarage","isReplayPlaying","replayFrame"},
 "camera":{"carIdx","group","num","state":[...],"stateRaw","replaySpeed","replaySlowMotion",
           "replaySessionNum","replaySessionTimeS","replayFramesFromEnd"},
 "derived":{"fuelPerLap","lapsOfFuel","fuelPerLapSamples"}}
```
`camera` is what the broadcast is looking at: `CamCarIdx`, `CamGroupNumber`, `CamCameraNumber`,
`CamCameraState` (decoded) and the replay variables. Spectating is a replay at `replaySpeed` 1;
any other speed, or slow motion, means the picture is not the race as it happens.

### `cars` (bridge, 8 Hz, delta-encoded; keyed by car index as a string)
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
Event kinds: `pit_in`, `pit_out` (stationaryS, laneLossS), `fastest_lap` (lapS, classId,
overall), `incident` (delta, total, limit), `behind_closing` / `you_closing` (gapS,
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
Two kinds, kept apart on the wire:

| | Source | Points? | Timing |
|---|---|---|---|
| `car_incident` | `DriverInfo/Drivers[].TeamIncidentCount` rising (plain per car as `cars.<idx>.incidents`; `driverIncidents` is `CurDriverIncidentCount`) | yes, iRacing's own | stamped when the session info changed, which is after the event |
| `off_track` | `CarIdxTrackSurface` on_track -> off_track | no | stamped at the frame it happened |
| `slowed` | derived: under 35% of the field's speed at that 0.5% of the lap for 1 s | no | stamped when the slowdown began |

Point values (Official Sporting Code 3.5.1): light contact 0x, wheels off 1x, loss of control 2x,
contact with an object 2x, heavy contact with another car 4x on pavement and 2x on dirt
(`surface` comes from `WeekendInfo/TrackType`). Concurrent incidents count only the most
serious (3.5.2). `causes` lists the table entries matching the rise; `single` is false when no
single entry matches (3x, 5x), and `maybeSeveral` is true for any rise over 1x, because two
incidents can land between two session-info updates. 0x never moves a count.

`replayT` is where a replay of it should start, on the session clock of `sessionNum`: for a
counted incident, the latest `off_track`/`slowed` of the same car in the 30 s before (`anchor`
names which), else 10 s before it was counted (`anchor: "reported"`). Observed events are only
detected at the live edge, so watching a replay never re-reports them. A count that is absent
or negative is "not published" and produces nothing.

`state.derived.liveEdge` = `{tLive, tPlay, behindLiveS, atLive}`: SessionTime, the replay
playhead (ReplaySessionTime while a replay is playing), the difference, and whether the
picture is within 4 s of live at normal speed. While not at live, the CarIdx data is the past,
so race events (`field_events`) are not produced. `cars.derived.liveEdge` is the same.

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
`replay {label, car}` (caption for the Replay tag; shown only while not at the live edge). A rejected patch gets
`gfx_result {ok:false, error}`.

### `resend` (viewer → bridge)
Asks for `session`, `map` and fresh keyframes. Sent by the client on connect.

### `relay` (relay → viewer)
`{"type":"relay","bridge":"connected"|"disconnected","engineer":bool,"role":"view"|"engineer"|"producer","viewers":n}`

Tokens: view (read-only), engineer (pit commands), producer (camera, replay, gfx).
