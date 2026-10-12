# Variable behavior report

Recording: `recordings/practice-pitstop.jsonl.gz`  
Ticks: 5401 over 540.0 s  
Session-info updates seen: 7  
Time by segment (s): {'pit': 358.0, 'track': 182.0}

Labels: **live** changes while driving; **slow** changes rarely (counters, flags); **pit-only** changes only in pit activity; **static** never changed; **absent** never present; **live(replay)** changes at live rates but was only ever seen in a replay, which proves iRacing records it, not that it is populated in a live session; **replay-blank** present live but not in replay.

| Variable | Type | N | Unit | Label | Present | Δ/s track | Δ/s pit | Δ/s replay | Sample | Description |
|---|---|---|---|---|---|---|---|---|---|---|
| `AirDensity` | float | 1 | kg/m^3 | **live** | 100% | 9.346 | 9.307 | – | 1.1766693592071533 | Density of air at start/finish line |
| `AirPressure` | float | 1 | Pa | **slow** | 100% | 0.313 | 0.313 | – | 101527.0 | Pressure of air at start/finish line |
| `AirTemp` | float | 1 | C | **live** | 100% | 10.0 | 10.0 | – | 24.19622230529785 | Temperature of air at start/finish line |
| `Brake` | float | 1 | % | **live** | 100% | 0.67 | 0.025 | – | 1.0 | 0=brake released to 1=max pedal force |
| `BrakeABSactive` | bool | 1 |  | **slow** | 100% | 0.143 | 0.0 | – | False | true if abs is currently reducing brake force pressure |
| `BrakeRaw` | float | 1 | % | **live** | 100% | 0.648 | 0.022 | – | 0.0 | Raw brake input 0=brake released to 1=max pedal force |
| `CamCameraNumber` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | Active camera number |
| `CamCameraState` | bitfield | 1 | irsdk_CameraState | **static** | 100% | 0.0 | 0.0 | – | 80 | State of camera system |
| `CamCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Active camera's focus car index |
| `CamGroupNumber` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 9 | Active camera group number |
| `CarDistAhead` | float | 1 | m | **static** | 100% | 0.0 | 0.0 | – | 500000.0 | Distance to first car in front of player in meters |
| `CarDistBehind` | float | 1 | m | **static** | 100% | 0.0 | 0.0 | – | 500000.0 | Distance to first car behind player in meters |
| `CarIdxBestLapNum` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars best lap number |
| `CarIdxBestLapTime` | float | 64 | s | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars best lap time |
| `CarIdxClass` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars class id by car index |
| `CarIdxClassPosition` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars class position in race by car index |
| `CarIdxEstTime` | float | 64 | s | **live** | 100% | 10.0 | 6.307 | – | [64 values] | Estimated time to reach current location on track |
| `CarIdxF2Time` | float | 64 | s | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Race time behind leader or fastest lap time otherwise |
| `CarIdxFastRepairsUsed` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | How many fast repairs each car has used |
| `CarIdxGear` | int | 64 |  | **slow** | 100% | 0.456 | 0.022 | – | [64 values] | -1=reverse  0=neutral  1..n=current gear by car index |
| `CarIdxLap` | int | 64 |  | **slow** | 100% | 0.005 | 0.0 | – | [64 values] | Laps started by car index |
| `CarIdxLapCompleted` | int | 64 |  | **slow** | 100% | 0.005 | 0.0 | – | [64 values] | Laps completed by car index |
| `CarIdxLapDistPct` | float | 64 | % | **live** | 100% | 10.0 | 5.595 | – | [64 values] | Percentage distance around lap by car index |
| `CarIdxLastLapTime` | float | 64 | s | **slow** | 100% | 0.005 | 0.0 | – | [64 values] | Cars last lap time |
| `CarIdxOnPitRoad` | bool | 64 |  | **pit-only** | 100% | 0.0 | 0.003 | – | [64 values] | On pit road between the cones by car index |
| `CarIdxP2P_Count` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Push2Pass count of usage (or remaining in Race) |
| `CarIdxP2P_Status` | bool | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Push2Pass active or not |
| `CarIdxPaceFlags` | bitfield | 64 | irsdk_PaceFlags | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Pacing status flags for each car |
| `CarIdxPaceLine` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | What line cars are pacing in  or -1 if not pacing |
| `CarIdxPaceRow` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | What row cars are pacing in  or -1 if not pacing |
| `CarIdxPosition` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars position in race by car index |
| `CarIdxQualTireCompound` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars Qual tire compound |
| `CarIdxQualTireCompoundLocked` | bool | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars Qual tire compound is locked-in |
| `CarIdxRPM` | float | 64 | revs/min | **live** | 100% | 10.0 | 0.598 | – | [64 values] | Engine rpm by car index |
| `CarIdxSessionFlags` | bitfield | 64 | irsdk_Flags | **pit-only** | 100% | 0.011 | 0.003 | – | [64 values] | Session flags for each player |
| `CarIdxSteer` | float | 64 | rad | **live** | 100% | 7.451 | 0.589 | – | [64 values] | Steering wheel angle by car index |
| `CarIdxTireCompound` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | – | [64 values] | Cars current tire compound |
| `CarIdxTrackSurface` | int | 64 | irsdk_TrkLoc | **pit-only** | 100% | 0.016 | 0.008 | – | [64 values] | Track surface type by car index |
| `CarIdxTrackSurfaceMaterial` | int | 64 | irsdk_TrkSurf | **slow** | 100% | 0.126 | 0.006 | – | [64 values] | Track surface material type by car index |
| `CarLeftRight` | int | 1 | irsdk_CarLeftRight | **static** | 100% | 0.0 | 0.0 | – | 1 | Notify if car is to the left or right of driver |
| `ChanAvgLatency` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 0.0333330780267715 | Communications average latency |
| `ChanClockSkew` | float | 1 | s | **slow** | 100% | 0.011 | 0.0 | – | 0.0 | Communications server clock skew |
| `ChanLatency` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 0.0333333350718021 | Communications latency |
| `ChanPartnerQuality` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 1.0 | Partner communications quality |
| `ChanQuality` | float | 1 | % | **slow** | 100% | 0.11 | 0.0 | – | 0.9999847412109375 | Communications quality |
| `Clutch` | float | 1 | % | **live** | 100% | 0.643 | 0.098 | – | 0.0 | 0=disengaged to 1=fully engaged |
| `ClutchRaw` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 1.0 | Raw clutch input 0=disengaged to 1=fully engaged |
| `CpuUsageBG` | float | 1 | % | **live** | 100% | 8.852 | 8.729 | – | 0.1490001529455185 | Percent of available time bg thread took with a 1 sec avg |
| `CpuUsageFG` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | – | 0.4626394510269165 | Percent of available time fg thread took with a 1 sec avg |
| `DCDriversSoFar` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | Number of team drivers who have run a stint |
| `DCLapStatus` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 2 | Status of driver change lap requirements |
| `DisplayUnits` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Default units for the user interface 0 = english 1 = metric |
| `DriverMarker` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Driver activated flag |
| `Engine0_RPM` | float | 1 | revs/min | **live** | 100% | 10.0 | 5.086 | – | 0.0 | Engine0Engine rpm |
| `EngineWarnings` | bitfield | 1 | irsdk_EngineWarnings | **pit-only** | 100% | 0.0 | 0.014 | – | 414 | Bitfield for warning lights |
| `EnterExitReset` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.008 | – | 1 | Indicate action the reset key will take 0 enter 1 exit 2 reset |
| `FastRepairAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | How many fast repairs left  255 is unlimited |
| `FastRepairUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | How many fast repairs used so far |
| `FogLevel` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Fog level at start/finish line |
| `FrameRate` | float | 1 | fps | **live** | 100% | 10.0 | 10.0 | – | 108.12355041503906 | Average frames per second |
| `FrontTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many front tire sets are remaining  255 is unlimited |
| `FrontTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many front tire sets used so far |
| `FuelLevel` | float | 1 | l | **live** | 100% | 10.0 | 0.589 | – | 83.33195495605469 | Liters of fuel remaining |
| `FuelLevelPct` | float | 1 | % | **live** | 100% | 10.0 | 0.589 | – | 0.9999834299087524 | Percent fuel remaining |
| `FuelPress` | float | 1 | bar | **pit-only** | 100% | 0.0 | 4.475 | – | 0.0 | Engine fuel pressure |
| `FuelUsePerHour` | float | 1 | kg/h | **live** | 100% | 10.0 | 0.55 | – | 1.0413849353790283 | Engine fuel used instantaneous |
| `Gear` | int | 1 |  | **slow** | 100% | 0.456 | 0.022 | – | -1 | -1=reverse  0=neutral  1..n=current gear |
| `GpuUsage` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | – | 0.415411114692688 | Percent of available time gpu took with a 1 sec avg |
| `HandbrakeRaw` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Raw handbrake input 0=handbrake released to 1=max force |
| `IsDiskLoggingActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | 0=disk based telemetry file not being written  1=being written |
| `IsDiskLoggingEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | 0=disk based telemetry turned off  1=turned on |
| `IsGarageVisible` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | 1=Garage screen is visible |
| `IsInGarage` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | 1=Car in garage physics running |
| `IsOnTrack` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | True | 1=Car on track physics running with player in car |
| `IsOnTrackCar` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | True | 1=Car on track physics running |
| `IsReplayPlaying` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | 0=replay not playing  1=replay playing |
| `LFTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many left front tires are remaining  255 is unlimited |
| `LFTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many left front tires used so far |
| `LFbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | – | 0.0 | LF brake line pressure |
| `LFcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | LF tire cold pressure  as set in the garage |
| `LFodometer` | float | 1 | m | **slow** | 100% | 0.39 | 0.008 | – | 0.0 | LF distance tire traveled since being placed on car |
| `LFshockDefl` | float | 1 | m | **live** | 100% | 10.0 | 1.232 | – | 0.0805616378784179 | LF shock deflection |
| `LFshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 1.243 | – | [6 values] | LF shock deflection at 360 Hz |
| `LFshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | -1.202728472371461 | LF shock velocity |
| `LFshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | LF shock velocity at 360 Hz |
| `LFtempCL` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 66.6083984375 | LF tire left carcass temperature |
| `LFtempCM` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 68.29013061523438 | LF tire middle carcass temperature |
| `LFtempCR` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 73.47811889648438 | LF tire right carcass temperature |
| `LFwearL` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9697905778884888 | LF tire left percent tread remaining |
| `LFwearM` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9635757207870483 | LF tire middle percent tread remaining |
| `LFwearR` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9613306522369385 | LF tire right percent tread remaining |
| `LRTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many left rear tires are remaining  255 is unlimited |
| `LRTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many left rear tires used so far |
| `LRbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | – | 0.0 | LR brake line pressure |
| `LRcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | LR tire cold pressure  as set in the garage |
| `LRodometer` | float | 1 | m | **slow** | 100% | 0.39 | 0.008 | – | 0.0 | LR distance tire traveled since being placed on car |
| `LRshockDefl` | float | 1 | m | **live** | 100% | 10.0 | 10.0 | – | 0.0614269673824310 | LR shock deflection |
| `LRshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | – | [6 values] | LR shock deflection at 360 Hz |
| `LRshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | -0.006014216225594 | LR shock velocity |
| `LRshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | LR shock velocity at 360 Hz |
| `LRtempCL` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 67.82791137695312 | LR tire left carcass temperature |
| `LRtempCM` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 69.919921875 | LR tire middle carcass temperature |
| `LRtempCR` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 72.80020141601562 | LR tire right carcass temperature |
| `LRwearL` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.986982524394989 | LR tire left percent tread remaining |
| `LRwearM` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9822568297386169 | LR tire middle percent tread remaining |
| `LRwearR` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9831576347351074 | LR tire right percent tread remaining |
| `Lap` | int | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | 4 | Laps started count |
| `LapBestLap` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 2 | Players best lap number |
| `LapBestLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 131.97140502929688 | Players best lap time |
| `LapBestNLapLap` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Player last lap in best N average lap time |
| `LapBestNLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Player best N average lap time |
| `LapCompleted` | int | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | 3 | Laps completed count |
| `LapCurrentLapTime` | float | 1 | s | **live** | 100% | 10.0 | 10.0 | – | 149.24290466308594 | Estimate of players current lap time as shown in F3 box |
| `LapDeltaToBestLap` | float | 1 | s | **live** | 100% | 9.67 | 0.0 | – | 0.0 | Delta time for best lap |
| `LapDeltaToBestLap_DD` | float | 1 | s/s | **live** | 100% | 9.665 | 0.0 | – | 0.0 | Rate of change of delta time for best lap |
| `LapDeltaToBestLap_OK` | bool | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | False | Delta time for best lap is valid |
| `LapDeltaToOptimalLap` | float | 1 | s | **live** | 100% | 9.67 | 0.0 | – | 0.0 | Delta time for optimal lap |
| `LapDeltaToOptimalLap_DD` | float | 1 | s/s | **live** | 100% | 9.665 | 0.0 | – | 0.0 | Rate of change of delta time for optimal lap |
| `LapDeltaToOptimalLap_OK` | bool | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | False | Delta time for optimal lap is valid |
| `LapDeltaToSessionBestLap` | float | 1 | s | **live** | 100% | 9.67 | 0.0 | – | 0.0 | Delta time for session best lap |
| `LapDeltaToSessionBestLap_DD` | float | 1 | s/s | **live** | 100% | 9.665 | 0.0 | – | 0.0 | Rate of change of delta time for session best lap |
| `LapDeltaToSessionBestLap_OK` | bool | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | False | Delta time for session best lap is valid |
| `LapDeltaToSessionLastlLap` | float | 1 | s | **live** | 100% | 9.67 | 0.0 | – | 0.0 | Delta time for session last lap |
| `LapDeltaToSessionLastlLap_DD` | float | 1 | s/s | **live** | 100% | 9.665 | 0.0 | – | 0.0 | Rate of change of delta time for session last lap |
| `LapDeltaToSessionLastlLap_OK` | bool | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | False | Delta time for session last lap is valid |
| `LapDeltaToSessionOptimalLap` | float | 1 | s | **live** | 100% | 9.67 | 0.0 | – | 0.0 | Delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_DD` | float | 1 | s/s | **live** | 100% | 9.665 | 0.0 | – | 0.0 | Rate of change of delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_OK` | bool | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | False | Delta time for session optimal lap is valid |
| `LapDist` | float | 1 | m | **live** | 100% | 10.0 | 6.307 | – | 238.5668487548828 | Meters traveled from S/F this lap |
| `LapDistPct` | float | 1 | % | **live** | 100% | 10.0 | 5.595 | – | 0.0419473685324192 | Percentage distance around lap |
| `LapLasNLapSeq` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Player num consecutive clean laps completed for N average |
| `LapLastLapTime` | float | 1 | s | **slow** | 100% | 0.005 | 0.0 | – | -1.0 | Players last lap time |
| `LapLastNLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Player last N average lap time |
| `LatAccel` | float | 1 | m/s^2 | **live** | 100% | 10.0 | 10.0 | – | -0.371180474758148 | Lateral acceleration (including gravity) |
| `LatAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Lateral acceleration (including gravity) at 360 Hz |
| `LeftTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many left tire sets are remaining  255 is unlimited |
| `LeftTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many left tire sets used so far |
| `LoadNumTextures` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | True if the car_num texture will be loaded |
| `LongAccel` | float | 1 | m/s^2 | **live** | 100% | 10.0 | 10.0 | – | -0.35187828540802 | Longitudinal acceleration (including gravity) |
| `LongAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Longitudinal acceleration (including gravity) at 360 Hz |
| `ManifoldPress` | float | 1 | bar | **live** | 100% | 9.478 | 0.648 | – | 1.0154608488082886 | Engine manifold pressure |
| `ManualBoost` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Hybrid manual boost state |
| `ManualNoBoost` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Hybrid manual no boost state |
| `MemPageFaultSec` | float | 1 |  | **live** | 100% | 0.764 | 0.927 | – | 5.0 | Memory page faults per second |
| `MemSoftPageFaultSec` | float | 1 |  | **live** | 100% | 1.94 | 1.938 | – | 48604.0 | Memory soft page faults per second |
| `OilLevel` | float | 1 | l | **static** | 100% | 0.0 | 0.0 | – | 5.019999980926514 | Engine oil level |
| `OilPress` | float | 1 | bar | **slow** | 100% | 0.071 | 0.763 | – | 0.0 | Engine oil pressure |
| `OilTemp` | float | 1 | C | **live** | 100% | 10.0 | 0.614 | – | 94.61505889892578 | Engine oil temperature |
| `OkToReloadTextures` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | True | True if it is ok to reload car textures at this time |
| `OnPitRoad` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | True | Is the player car on pit road between the cones |
| `P2P_Count` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Push2Pass count of usage (or remaining in Race) on your car |
| `P2P_Status` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Push2Pass active or not on your car |
| `PaceMode` | int | 1 | irsdk_PaceMode | **static** | 100% | 0.0 | 0.0 | – | 4 | Are we pacing or not |
| `PitOptRepairLeft` | float | 1 | s | **pit-only** | 100% | 0.0 | 0.651 | – | 133.62765502929688 | Time left for optional repairs if repairs are active |
| `PitRepairLeft` | float | 1 | s | **pit-only** | 100% | 0.0 | 8.759 | – | 313.8456726074219 | Time left for mandatory pit repairs if repairs are active |
| `PitSvFlags` | bitfield | 1 | irsdk_PitSvFlags | **pit-only** | 100% | 0.0 | 0.003 | – | 0 | Bitfield of pit service checkboxes |
| `PitSvFuel` | float | 1 | l or kWh | **static** | 100% | 0.0 | 0.0 | – | 83.33333587646484 | Pit service fuel add amount |
| `PitSvLFP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pit service left front tire pressure |
| `PitSvLRP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pit service left rear tire pressure |
| `PitSvRFP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pit service right front tire pressure |
| `PitSvRRP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pit service right rear tire pressure |
| `PitSvTireCompound` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Pit service pending tire compound |
| `Pitch` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | – | 0.0265924390405416 | Pitch orientation |
| `PitchRate` | float | 1 | rad/s | **live** | 100% | 10.0 | 10.0 | – | -0.006808508653193 | Pitch rate |
| `PitchRate_ST` | float | 6 | rad/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Pitch rate at 360 Hz |
| `PitsOpen` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | True | True if pit stop is allowed for the current player |
| `PitstopActive` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | True | Is the player getting pit stop service |
| `PlayerCarClass` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Player car class id |
| `PlayerCarClassPosition` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | Players class position in race |
| `PlayerCarDriverIncidentCount` | int | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | 3 | Teams current drivers incident count for this session |
| `PlayerCarDryTireSetLimit` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Players dry tire set limit |
| `PlayerCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Players carIdx |
| `PlayerCarInPitStall` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | True | Players car is properly in their pitstall |
| `PlayerCarMyIncidentCount` | int | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | 3 | Players own incident count for this session |
| `PlayerCarPitSvStatus` | int | 1 | irsdk_PitSvStatus | **pit-only** | 100% | 0.0 | 0.006 | – | 1 | Players car pit service status bits |
| `PlayerCarPosition` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | Players position in race |
| `PlayerCarPowerAdjust` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Players power adjust |
| `PlayerCarSLBlinkRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | – | 7210.0 | Shift light blink rpm |
| `PlayerCarSLFirstRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | – | 6130.0 | Shift light first light rpm |
| `PlayerCarSLLastRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | – | 6800.0 | Shift light last light rpm |
| `PlayerCarSLShiftRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | – | 6690.0 | Shift light shift rpm |
| `PlayerCarTeamIncidentCount` | int | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | 3 | Players team incident count for this session |
| `PlayerCarTowTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Players car is being towed if time is greater than zero |
| `PlayerCarWeightPenalty` | float | 1 | kg | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Players weight penalty |
| `PlayerFastRepairsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Players car number of fast repairs used |
| `PlayerIncidents` | int | 1 | irsdk_IncidentFlags | **static** | 100% | 0.0 | 0.0 | – | 0 | Log incidents that the player recieved |
| `PlayerTireCompound` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Players car current tire compound |
| `PlayerTrackSurface` | int | 1 | irsdk_TrkLoc | **pit-only** | 100% | 0.016 | 0.008 | – | 1 | Players car track surface type |
| `PlayerTrackSurfaceMaterial` | int | 1 | irsdk_TrkSurf | **slow** | 100% | 0.126 | 0.006 | – | 5 | Players car track surface material type |
| `Precipitation` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Precipitation at start/finish line |
| `PushToPass` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Push to pass button state |
| `PushToTalk` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Push to talk button state |
| `RFTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many right front tires are remaining  255 is unlimited |
| `RFTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many right front tires used so far |
| `RFbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | – | 0.0 | RF brake line pressure |
| `RFcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | RF tire cold pressure  as set in the garage |
| `RFodometer` | float | 1 | m | **slow** | 100% | 0.39 | 0.008 | – | 0.0 | RF distance tire traveled since being placed on car |
| `RFshockDefl` | float | 1 | m | **live** | 100% | 10.0 | 9.991 | – | 0.1134389638900756 | RF shock deflection |
| `RFshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | – | [6 values] | RF shock deflection at 360 Hz |
| `RFshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | -0.008980551734566 | RF shock velocity |
| `RFshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | RF shock velocity at 360 Hz |
| `RFtempCL` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 83.24728393554688 | RF tire left carcass temperature |
| `RFtempCM` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 76.43597412109375 | RF tire middle carcass temperature |
| `RFtempCR` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 72.68215942382812 | RF tire right carcass temperature |
| `RFwearL` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9502403736114502 | RF tire left percent tread remaining |
| `RFwearM` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9540205001831055 | RF tire middle percent tread remaining |
| `RFwearR` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9805951714515686 | RF tire right percent tread remaining |
| `RPM` | float | 1 | revs/min | **live** | 100% | 10.0 | 0.598 | – | 300.0 | Engine rpm |
| `RRTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many right rear tires are remaining  255 is unlimited |
| `RRTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many right rear tires used so far |
| `RRbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | – | 0.0 | RR brake line pressure |
| `RRcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | RR tire cold pressure  as set in the garage |
| `RRodometer` | float | 1 | m | **slow** | 100% | 0.39 | 0.008 | – | 0.0 | RR distance tire traveled since being placed on car |
| `RRshockDefl` | float | 1 | m | **live** | 100% | 10.0 | 9.997 | – | 0.0361969470977783 | RR shock deflection |
| `RRshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | – | [6 values] | RR shock deflection at 360 Hz |
| `RRshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | 0.0062985434196889 | RR shock velocity |
| `RRshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | RR shock velocity at 360 Hz |
| `RRtempCL` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 59.502593994140625 | RR tire left carcass temperature |
| `RRtempCM` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 56.678314208984375 | RR tire middle carcass temperature |
| `RRtempCR` | float | 1 | C | **static** | 100% | 0.0 | 0.0 | – | 56.4593505859375 | RR tire right carcass temperature |
| `RRwearL` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9847478866577148 | RR tire left percent tread remaining |
| `RRwearM` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9860166907310486 | RR tire middle percent tread remaining |
| `RRwearR` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.9915650486946106 | RR tire right percent tread remaining |
| `RaceLaps` | int | 1 |  | **slow** | 100% | 0.005 | 0.0 | – | 4 | Laps completed in race |
| `RadioTransmitCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | -1 | The car index of the current person speaking on the radio |
| `RadioTransmitFrequencyIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | The frequency index of the current person speaking on the radio |
| `RadioTransmitRadioIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | The radio index of the current person speaking on the radio |
| `RearTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many rear tire sets are remaining  255 is unlimited |
| `RearTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many rear tire sets used so far |
| `RelativeHumidity` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | – | 0.9612621068954468 | Relative Humidity at start/finish line |
| `ReplayFrameNum` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Integer replay frame number (60 per second) |
| `ReplayFrameNumEnd` | int | 1 |  | **live** | 100% | 10.0 | 10.0 | – | 65794 | Integer replay frame number from end of tape |
| `ReplayPlaySlowMotion` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | 0=not slow motion  1=replay is in slow motion |
| `ReplayPlaySpeed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | Replay playback speed |
| `ReplaySessionNum` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | -1 | Replay session number |
| `ReplaySessionTime` | double | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Seconds since replay session start |
| `RightTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many right tire sets are remaining  255 is unlimited |
| `RightTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many right tire sets used so far |
| `Roll` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | – | -0.015331884846091 | Roll orientation |
| `RollRate` | float | 1 | rad/s | **live** | 100% | 10.0 | 10.0 | – | -0.000245213974267 | Roll rate |
| `RollRate_ST` | float | 6 | rad/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Roll rate at 360 Hz |
| `SessionFlags` | bitfield | 1 | irsdk_Flags | **pit-only** | 100% | 0.011 | 0.003 | – | 269746688 | Session flags |
| `SessionJokerLapsRemain` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Joker laps remaining to be taken |
| `SessionLapsRemain` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 32767 | Old laps left till session ends use SessionLapsRemainEx |
| `SessionLapsRemainEx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 32767 | New improved laps left till session ends |
| `SessionLapsTotal` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 32767 | Total number of laps in session |
| `SessionNum` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0 | Session number |
| `SessionOnJokerLap` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Player is currently completing a joker lap |
| `SessionState` | int | 1 | irsdk_SessionState | **static** | 100% | 0.0 | 0.0 | – | 4 | Session state |
| `SessionTick` | int | 1 |  | **live** | 100% | 10.0 | 10.0 | – | 67589 | Current update number |
| `SessionTime` | double | 1 | s | **live** | 100% | 10.0 | 10.0 | – | 1122.9999997449945 | Seconds since session start |
| `SessionTimeOfDay` | float | 1 | s | **live** | 100% | 1.0 | 1.003 | – | 33522.0 | Time of day in seconds |
| `SessionTimeRemain` | double | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 604800.0 | Seconds left till session ends |
| `SessionTimeTotal` | double | 1 | s | **static** | 100% | 0.0 | 0.0 | – | 604800.0 | Total number of seconds in session |
| `SessionUniqueID` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | Session ID |
| `ShiftGrindRPM` | float | 1 | RPM | **static** | 100% | 0.0 | 0.0 | – | 0.0 | RPM of shifter grinding noise |
| `ShiftIndicatorPct` | float | 1 | % | **live** | 100% | 6.72 | 0.003 | – | 1.0 | DEPRECATED use DriverCarSLBlinkRPM instead |
| `ShiftPowerPct` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Friction torque applied to gears when shifting or grinding |
| `Shifter` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.011 | – | 0 | Log inputs from the players shifter control |
| `Skies` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 2 | Skies (0=clear/1=p cloudy/2=m cloudy/3=overcast) |
| `SolarAltitude` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | – | 0.4143463373184204 | Sun angle above horizon in radians |
| `SolarAzimuth` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | – | 1.964440107345581 | Sun angle clockwise from north in radians |
| `Speed` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | 0.0038120353128761 | GPS vehicle speed |
| `SteeringFFBEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | True | Force feedback is enabled |
| `SteeringWheelAngle` | float | 1 | rad | **live** | 100% | 7.451 | 0.589 | – | -0.029921889305114 | Steering wheel angle |
| `SteeringWheelAngleMax` | float | 1 | rad | **static** | 100% | 0.0 | 0.0 | – | 13.864885330200195 | Steering wheel max angle |
| `SteeringWheelLimiter` | float | 1 | % | **slow** | 100% | 0.319 | 0.0 | – | 0.0 | Force feedback limiter strength limits impacts and oscillation |
| `SteeringWheelMaxForceNm` | float | 1 | N*m | **static** | 100% | 0.0 | 0.0 | – | 28.33333396911621 | Value of strength or max force slider in Nm for FFB |
| `SteeringWheelPctDamper` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Force feedback % max damping |
| `SteeringWheelPctIntensity` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.5 | Force feedback % max intensity |
| `SteeringWheelPctSmoothing` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Force feedback % max smoothing |
| `SteeringWheelPctTorque` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | – | 0.0477624014019966 | Force feedback % max torque on steering shaft unsigned |
| `SteeringWheelPctTorqueSign` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | – | -0.047762401401996 | Force feedback % max torque on steering shaft signed |
| `SteeringWheelPctTorqueSignStops` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | – | -0.015763919800519 | Force feedback % max torque on steering shaft signed stops |
| `SteeringWheelPeakForceNm` | float | 1 | N*m | **static** | 100% | 0.0 | 0.0 | – | -1.0 | Peak torque mapping to direct input units for FFB |
| `SteeringWheelTorque` | float | 1 | N*m | **live** | 100% | 10.0 | 10.0 | – | -1.353268027305603 | Output torque on steering shaft |
| `SteeringWheelTorque_ST` | float | 6 | N*m | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Output torque on steering shaft at 360 Hz |
| `SteeringWheelUseLinear` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | True | True if steering wheel force is using linear mode |
| `Throttle` | float | 1 | % | **live** | 100% | 1.308 | 0.061 | – | 0.0 | 0=off throttle to 1=full throttle |
| `ThrottleRaw` | float | 1 | % | **live** | 100% | 1.005 | 0.056 | – | 0.0 | Raw throttle input 0=off throttle to 1=full throttle |
| `TireLF_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.033 | 0.0 | – | 0.0 | Players LF Tire Sound rumblestrip pitch |
| `TireLR_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.022 | 0.0 | – | 0.0 | Players LR Tire Sound rumblestrip pitch |
| `TireRF_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.093 | 0.0 | – | 0.0 | Players RF Tire Sound rumblestrip pitch |
| `TireRR_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.082 | 0.0 | – | 0.0 | Players RR Tire Sound rumblestrip pitch |
| `TireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many tire sets are remaining  255 is unlimited |
| `TireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1 | How many tire sets used so far |
| `TrackTemp` | float | 1 | C | **pit-only** | 100% | 0.0 | 0.003 | – | 25.0 | Deprecated  set to TrackTempCrew |
| `TrackTempCrew` | float | 1 | C | **pit-only** | 100% | 0.0 | 0.003 | – | 25.0 | Temperature of track measured by crew around track |
| `TrackWetness` | int | 1 | irsdk_TrackWetness | **static** | 100% | 0.0 | 0.0 | – | 1 | How wet is the average track surface |
| `VelocityX` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | -0.000539539556484 | X velocity |
| `VelocityX_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 10.0 | 10.0 | – | [6 values] | X velocity |
| `VelocityY` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | -0.003773660166189 | Y velocity |
| `VelocityY_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Y velocity |
| `VelocityZ` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | 0.0084727974608540 | Z velocity |
| `VelocityZ_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Z velocity |
| `VertAccel` | float | 1 | m/s^2 | **live** | 100% | 10.0 | 10.0 | – | 9.80494213104248 | Vertical acceleration (including gravity) |
| `VertAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Vertical acceleration (including gravity) at 360 Hz |
| `VidCapActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | True if video currently being captured |
| `VidCapEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | True if video capture system is enabled |
| `Voltage` | float | 1 | V | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Engine voltage |
| `WaterLevel` | float | 1 | l | **static** | 100% | 0.0 | 0.0 | – | 7.400000095367432 | Engine coolant level |
| `WaterTemp` | float | 1 | C | **live** | 100% | 10.0 | 0.614 | – | 83.92826843261719 | Engine coolant temp |
| `WeatherDeclaredWet` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | The steward says rain tires can be used |
| `WindDir` | float | 1 | rad | **live** | 100% | 10.0 | 9.997 | – | 5.1017842292785645 | Wind direction at start/finish line |
| `WindVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | – | 4.243819713592529 | Wind velocity at start/finish line |
| `Yaw` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | – | 0.0188466440886259 | Yaw orientation |
| `YawNorth` | float | 1 | rad | **live** | 100% | 10.0 | 9.796 | – | 3.7553658485412598 | Yaw orientation relative to north |
| `YawRate` | float | 1 | rad/s | **live** | 100% | 10.0 | 10.0 | – | 0.0002028976305155 | Yaw rate |
| `YawRate_ST` | float | 6 | rad/s | **live** | 100% | 10.0 | 10.0 | – | [6 values] | Yaw rate at 360 Hz |
| `dcABS` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 2.0 | In car abs adjustment |
| `dcBrakeBias` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 50.0 | In car brake bias adjustment |
| `dcHeadlightFlash` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | In car headlight flash control active |
| `dcPitSpeedLimiterToggle` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | Track if pit speed limiter system is enabled |
| `dcStarter` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | In car trigger car starter |
| `dcToggleWindshieldWipers` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | In car turn wipers on or off |
| `dcTractionControl` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 1.0 | In car traction control adjustment |
| `dcTriggerWindshieldWipers` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | – | False | In car momentarily turn on wipers |
| `dpFastRepair` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Pitstop fast repair set |
| `dpFuelAddKg` | float | 1 | kg | **static** | 100% | 0.0 | 0.0 | – | 83.33333587646484 | Pitstop fuel add amount |
| `dpFuelAutoFillActive` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Pitstop auto fill fuel next stop flag |
| `dpFuelAutoFillEnabled` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | – | 0.0 | Pitstop auto fill fuel system enabled |
| `dpFuelFill` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Pitstop fuel fill flag |
| `dpLFTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Pitstop lf tire change request |
| `dpLFTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pitstop lf tire cold pressure adjustment |
| `dpLRTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Pitstop lr tire change request |
| `dpLRTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pitstop lr tire cold pressure adjustment |
| `dpRFTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Pitstop rf tire change request |
| `dpRFTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pitstop rf cold tire pressure adjustment |
| `dpRRTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Pitstop rr tire change request |
| `dpRRTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | – | 172.36900329589844 | Pitstop rr cold tire pressure adjustment |
| `dpWindshieldTearoff` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.003 | – | 0.0 | Pitstop windshield tearoff |
