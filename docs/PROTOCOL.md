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
 "derived":{"fuelPerLap","lapsOfFuel","fuelPerLapSamples"}}
```

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
            "traffic":[{"idx","kind":"you_catch"|"catches_you","inS","atPct"}]}}
```
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
`declared_wet`, `pit_battle` (preS, nowS, first).

`session.options` carries `IncidentLimit` (a number, or "unlimited"),
`FastRepairsLimit`, `StandingStart` and `NumStarters` from WeekendOptions.

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

### `resend` (viewer → bridge)
Asks for `session`, `map` and fresh keyframes. Sent by the client on connect.

### `relay` (relay → viewer)
`{"type":"relay","bridge":"connected"|"disconnected","engineer":bool,"viewers":n}`
