# Variable behavior report

Recording: `recordings/spectate-race-vir.jsonl.gz`  
Ticks: 9001 over 900.0 s  
Session-info updates seen: 86  
Time by segment (s): {'replay': 504.5, 'pit': 15.8, 'track': 379.7}

Labels: **live** changes while driving; **slow** changes rarely (counters, flags); **pit-only** changes only in pit activity; **static** never changed; **absent** never present; **live(replay)** changes at live rates but was only ever seen in a replay, which proves iRacing records it, not that it is populated in a live session; **replay-blank** present live but not in replay.

| Variable | Type | N | Unit | Label | Present | Δ/s track | Δ/s pit | Δ/s replay | Sample | Description |
|---|---|---|---|---|---|---|---|---|---|---|
| `AirDensity` | float | 1 | kg/m^3 | **live** | 100% | 9.787 | 10.0 | 9.756 | 1.1602363586425781 | Density of air at start/finish line |
| `AirPressure` | float | 1 | Pa | **live** | 100% | 5.496 | 3.861 | 6.03 | 99520.203125 | Pressure of air at start/finish line |
| `AirTemp` | float | 1 | C | **live** | 100% | 10.0 | 10.0 | 10.0 | 22.871543884277344 | Temperature of air at start/finish line |
| `Brake` | float | 1 | % | **live** | 100% | 0.616 | 0.063 | 0.0 | 1.0 | 0=brake released to 1=max pedal force |
| `BrakeABSactive` | bool | 1 |  | **slow** | 100% | 0.237 | 0.127 | 0.0 | False | true if abs is currently reducing brake force pressure |
| `BrakeRaw` | float | 1 | % | **live** | 100% | 0.59 | 0.0 | 0.0 | 0.0 | Raw brake input 0=brake released to 1=max pedal force |
| `CamCameraNumber` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | Active camera number |
| `CamCameraState` | bitfield | 1 | irsdk_CameraState | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 81 | State of camera system |
| `CamCarIdx` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.052 | 62 | Active camera's focus car index |
| `CamGroupNumber` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.004 | 9 | Active camera group number |
| `CarDistAhead` | float | 1 | m | **live** | 100% | 10.0 | 2.532 | 0.002 | 500000.0 | Distance to first car in front of player in meters |
| `CarDistBehind` | float | 1 | m | **live** | 100% | 10.0 | 2.532 | 0.002 | 500000.0 | Distance to first car behind player in meters |
| `CarIdxBestLapNum` | int | 64 |  | **slow** | 100% | 0.034 | 0.0 | 0.071 | [64 values] | Cars best lap number |
| `CarIdxBestLapTime` | float | 64 | s | **slow** | 100% | 0.034 | 0.0 | 0.071 | [64 values] | Cars best lap time |
| `CarIdxClass` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Cars class id by car index |
| `CarIdxClassPosition` | int | 64 |  | **slow** | 100% | 0.045 | 0.0 | 0.03 | [64 values] | Cars class position in race by car index |
| `CarIdxEstTime` | float | 64 | s | **live** | 100% | 10.0 | 10.0 | 9.5 | [64 values] | Estimated time to reach current location on track |
| `CarIdxF2Time` | float | 64 | s | **slow** | 100% | 0.066 | 0.0 | 0.101 | [64 values] | Race time behind leader or fastest lap time otherwise |
| `CarIdxFastRepairsUsed` | int | 64 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | [64 values] | How many fast repairs each car has used |
| `CarIdxGear` | int | 64 |  | **live** | 100% | 3.684 | 2.468 | 3.687 | [64 values] | -1=reverse  0=neutral  1..n=current gear by car index |
| `CarIdxLap` | int | 64 |  | **slow** | 100% | 0.192 | 0.38 | 0.226 | [64 values] | Laps started by car index |
| `CarIdxLapCompleted` | int | 64 |  | **slow** | 100% | 0.187 | 0.38 | 0.206 | [64 values] | Laps completed by car index |
| `CarIdxLapDistPct` | float | 64 | % | **live** | 100% | 10.0 | 10.0 | 9.512 | [64 values] | Percentage distance around lap by car index |
| `CarIdxLastLapTime` | float | 64 | s | **slow** | 100% | 0.087 | 0.0 | 0.153 | [64 values] | Cars last lap time |
| `CarIdxOnPitRoad` | bool | 64 |  | **slow** | 100% | 0.024 | 0.063 | 0.046 | [64 values] | On pit road between the cones by car index |
| `CarIdxP2P_Count` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Push2Pass count of usage (or remaining in Race) |
| `CarIdxP2P_Status` | bool | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Push2Pass active or not |
| `CarIdxPaceFlags` | bitfield | 64 | irsdk_PaceFlags | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Pacing status flags for each car |
| `CarIdxPaceLine` | int | 64 |  | **slow** | 100% | 0.034 | 0.0 | 0.002 | [64 values] | What line cars are pacing in  or -1 if not pacing |
| `CarIdxPaceRow` | int | 64 |  | **slow** | 100% | 0.034 | 0.0 | 0.002 | [64 values] | What row cars are pacing in  or -1 if not pacing |
| `CarIdxPosition` | int | 64 |  | **slow** | 100% | 0.045 | 0.0 | 0.03 | [64 values] | Cars position in race by car index |
| `CarIdxQualTireCompound` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Cars Qual tire compound |
| `CarIdxQualTireCompoundLocked` | bool | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Cars Qual tire compound is locked-in |
| `CarIdxRPM` | float | 64 | revs/min | **live** | 100% | 10.0 | 10.0 | 9.96 | [64 values] | Engine rpm by car index |
| `CarIdxSessionFlags` | bitfield | 64 | irsdk_Flags | **slow** | 100% | 0.016 | 0.0 | 0.03 | [64 values] | Session flags for each player |
| `CarIdxSteer` | float | 64 | rad | **live** | 100% | 10.0 | 10.0 | 10.0 | [64 values] | Steering wheel angle by car index |
| `CarIdxTireCompound` | int | 64 |  | **slow** | 100% | 0.037 | 0.0 | 0.057 | [64 values] | Cars current tire compound |
| `CarIdxTrackSurface` | int | 64 | irsdk_TrkLoc | **slow** | 100% | 0.158 | 0.063 | 0.23 | [64 values] | Track surface type by car index |
| `CarIdxTrackSurfaceMaterial` | int | 64 | irsdk_TrkSurf | **live** | 100% | 1.201 | 0.127 | 1.231 | [64 values] | Track surface material type by car index |
| `CarLeftRight` | int | 1 | irsdk_CarLeftRight | **slow** | 100% | 0.011 | 0.0 | 0.0 | 1 | Notify if car is to the left or right of driver |
| `ChanAvgLatency` | float | 1 | s | **live** | 100% | 8.175 | 10.0 | 7.829 | 0.0671936795115470 | Communications average latency |
| `ChanClockSkew` | float | 1 | s | **slow** | 100% | 0.142 | 0.506 | 0.176 | 0.0 | Communications server clock skew |
| `ChanLatency` | float | 1 | s | **slow** | 100% | 0.142 | 0.506 | 0.176 | 0.0666666701436042 | Communications latency |
| `ChanPartnerQuality` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.004 | 1.0 | Partner communications quality |
| `ChanQuality` | float | 1 | % | **live** | 100% | 3.213 | 0.127 | 1.144 | 0.9977928400039673 | Communications quality |
| `Clutch` | float | 1 | % | **live** | 100% | 0.685 | 1.013 | 0.0 | 0.0 | 0=disengaged to 1=fully engaged |
| `ClutchRaw` | float | 1 | % | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Raw clutch input 0=disengaged to 1=fully engaged |
| `CpuUsageBG` | float | 1 | % | **live** | 100% | 8.483 | 8.987 | 8.809 | 0.2290003746747970 | Percent of available time bg thread took with a 1 sec avg |
| `CpuUsageFG` | float | 1 | % | **live** | 100% | 10.0 | 9.747 | 9.99 | 0.9489650130271912 | Percent of available time fg thread took with a 1 sec avg |
| `DCDriversSoFar` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Number of team drivers who have run a stint |
| `DCLapStatus` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 2 | Status of driver change lap requirements |
| `DisplayUnits` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Default units for the user interface 0 = english 1 = metric |
| `DriverMarker` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Driver activated flag |
| `Engine0_RPM` | float | 1 | revs/min | **live** | 100% | 10.0 | 9.873 | 7.169 | 0.0003577419847715 | Engine0Engine rpm |
| `EngineWarnings` | bitfield | 1 | irsdk_EngineWarnings | **pit-only** | 100% | 0.0 | 0.253 | 0.002 | 30 | Bitfield for warning lights |
| `EnterExitReset` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.127 | 0.002 | 0 | Indicate action the reset key will take 0 enter 1 exit 2 reset |
| `FastRepairAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many fast repairs left  255 is unlimited |
| `FastRepairUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many fast repairs used so far |
| `FogLevel` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Fog level at start/finish line |
| `FrameRate` | float | 1 | fps | **live** | 100% | 9.997 | 9.051 | 9.99 | 64.0545654296875 | Average frames per second |
| `FrontTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many front tire sets are remaining  255 is unlimited |
| `FrontTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many front tire sets used so far |
| `FuelLevel` | float | 1 | l | **live** | 100% | 10.0 | 9.114 | 0.002 | 49.309696197509766 | Liters of fuel remaining |
| `FuelLevelPct` | float | 1 | % | **live** | 100% | 10.0 | 9.114 | 0.002 | 0.5917163491249084 | Percent fuel remaining |
| `FuelPress` | float | 1 | bar | **pit-only** | 100% | 0.0 | 0.063 | 7.169 | 4.755350801133318e | Engine fuel pressure |
| `FuelUsePerHour` | float | 1 | kg/h | **live** | 100% | 9.839 | 6.962 | 0.0 | 1.023878812789917 | Engine fuel used instantaneous |
| `Gear` | int | 1 |  | **slow** | 100% | 0.435 | 0.316 | 0.0 | 0 | -1=reverse  0=neutral  1..n=current gear |
| `GpuUsage` | float | 1 | % | **live** | 100% | 10.0 | 9.81 | 9.992 | 0.9290134310722351 | Percent of available time gpu took with a 1 sec avg |
| `HandbrakeRaw` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Raw handbrake input 0=handbrake released to 1=max force |
| `IsDiskLoggingActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 0=disk based telemetry file not being written  1=being written |
| `IsDiskLoggingEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 0=disk based telemetry turned off  1=turned on |
| `IsGarageVisible` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 1=Garage screen is visible |
| `IsInGarage` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 1=Car in garage physics running |
| `IsOnTrack` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | False | 1=Car on track physics running with player in car |
| `IsOnTrackCar` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | 1=Car on track physics running |
| `IsReplayPlaying` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | True | 0=replay not playing  1=replay playing |
| `LFTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many left front tires are remaining  255 is unlimited |
| `LFTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many left front tires used so far |
| `LFbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | LF brake line pressure |
| `LFcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | LF tire cold pressure  as set in the garage |
| `LFodometer` | float | 1 | m | **slow** | 100% | 0.319 | 0.0 | 0.002 | 0.0 | LF distance tire traveled since being placed on car |
| `LFshockDefl` | float | 1 | m | **live** | 100% | 9.997 | 10.0 | 9.744 | 0.0823133587837219 | LF shock deflection |
| `LFshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | LF shock deflection at 360 Hz |
| `LFshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 0.0001268356281798 | LF shock velocity |
| `LFshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | LF shock velocity at 360 Hz |
| `LFtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 52.455047607421875 | LF tire left carcass temperature |
| `LFtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 52.792449951171875 | LF tire middle carcass temperature |
| `LFtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 53.502044677734375 | LF tire right carcass temperature |
| `LFwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9811776280403137 | LF tire left percent tread remaining |
| `LFwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9807402491569519 | LF tire middle percent tread remaining |
| `LFwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.980475127696991 | LF tire right percent tread remaining |
| `LRTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many left rear tires are remaining  255 is unlimited |
| `LRTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many left rear tires used so far |
| `LRbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | LR brake line pressure |
| `LRcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | LR tire cold pressure  as set in the garage |
| `LRodometer` | float | 1 | m | **slow** | 100% | 0.319 | 0.0 | 0.002 | 0.0 | LR distance tire traveled since being placed on car |
| `LRshockDefl` | float | 1 | m | **live** | 100% | 9.997 | 10.0 | 9.748 | 0.0486851930618286 | LR shock deflection |
| `LRshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | 9.996 | [6 values] | LR shock deflection at 360 Hz |
| `LRshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | -3.156742241117172 | LR shock velocity |
| `LRshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | LR shock velocity at 360 Hz |
| `LRtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 50.720916748046875 | LR tire left carcass temperature |
| `LRtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 51.588348388671875 | LR tire middle carcass temperature |
| `LRtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 52.003631591796875 | LR tire right carcass temperature |
| `LRwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9953202605247498 | LR tire left percent tread remaining |
| `LRwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9942731261253357 | LR tire middle percent tread remaining |
| `LRwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9942178726196289 | LR tire right percent tread remaining |
| `Lap` | int | 1 |  | **pit-only** | 100% | 0.018 | 0.063 | 0.0 | 0 | Laps started count |
| `LapBestLap` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players best lap number |
| `LapBestLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players best lap time |
| `LapBestNLapLap` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Player last lap in best N average lap time |
| `LapBestNLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Player best N average lap time |
| `LapCompleted` | int | 1 |  | **pit-only** | 100% | 0.018 | 0.063 | 0.0 | -1 | Laps completed count |
| `LapCurrentLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Estimate of players current lap time as shown in F3 box |
| `LapDeltaToBestLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for best lap |
| `LapDeltaToBestLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for best lap |
| `LapDeltaToBestLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for best lap is valid |
| `LapDeltaToOptimalLap` | float | 1 | s | **live** | 100% | 2.175 | 0.0 | 0.0 | 0.0 | Delta time for optimal lap |
| `LapDeltaToOptimalLap_DD` | float | 1 | s/s | **live** | 100% | 2.175 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for optimal lap |
| `LapDeltaToOptimalLap_OK` | bool | 1 |  | **slow** | 100% | 0.016 | 0.0 | 0.0 | False | Delta time for optimal lap is valid |
| `LapDeltaToSessionBestLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for session best lap |
| `LapDeltaToSessionBestLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for session best lap |
| `LapDeltaToSessionBestLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for session best lap is valid |
| `LapDeltaToSessionLastlLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for session last lap |
| `LapDeltaToSessionLastlLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for session last lap |
| `LapDeltaToSessionLastlLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for session last lap is valid |
| `LapDeltaToSessionOptimalLap` | float | 1 | s | **live** | 100% | 3.089 | 0.0 | 0.002 | 0.0 | Delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_DD` | float | 1 | s/s | **live** | 100% | 3.084 | 0.0 | 0.002 | 0.0 | Rate of change of delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_OK` | bool | 1 |  | **slow** | 100% | 0.008 | 0.0 | 0.002 | False | Delta time for session optimal lap is valid |
| `LapDist` | float | 1 | m | **live** | 100% | 9.913 | 8.228 | 0.012 | 3575.150390625 | Meters traveled from S/F this lap |
| `LapDistPct` | float | 1 | % | **live** | 100% | 9.913 | 8.228 | 0.012 | 0.9870092272758484 | Percentage distance around lap |
| `LapLasNLapSeq` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Player num consecutive clean laps completed for N average |
| `LapLastLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players last lap time |
| `LapLastNLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Player last N average lap time |
| `LatAccel` | float | 1 | m/s^2 | **live** | 100% | 10.0 | 10.0 | 10.0 | 0.0292660389095544 | Lateral acceleration (including gravity) |
| `LatAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Lateral acceleration (including gravity) at 360 Hz |
| `LeftTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many left tire sets are remaining  255 is unlimited |
| `LeftTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many left tire sets used so far |
| `LoadNumTextures` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | True if the car_num texture will be loaded |
| `LongAccel` | float | 1 | m/s^2 | **live** | 100% | 10.0 | 10.0 | 10.0 | -0.405296117067337 | Longitudinal acceleration (including gravity) |
| `LongAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Longitudinal acceleration (including gravity) at 360 Hz |
| `ManifoldPress` | float | 1 | bar | **live** | 100% | 9.926 | 9.43 | 5.982 | 0.995180070400238 | Engine manifold pressure |
| `ManualBoost` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Hybrid manual boost state |
| `ManualNoBoost` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Hybrid manual no boost state |
| `MemPageFaultSec` | float | 1 |  | **live** | 100% | 1.109 | 1.266 | 1.306 | 1.0 | Memory page faults per second |
| `MemSoftPageFaultSec` | float | 1 |  | **live** | 100% | 1.938 | 1.962 | 1.935 | 106275.0 | Memory soft page faults per second |
| `OilLevel` | float | 1 | l | **static** | 100% | 0.0 | 0.0 | 0.0 | 5.019999980926514 | Engine oil level |
| `OilPress` | float | 1 | bar | **slow** | 100% | 0.408 | 4.43 | 0.002 | 0.0 | Engine oil pressure |
| `OilTemp` | float | 1 | C | **live** | 100% | 10.0 | 9.557 | 0.002 | 80.56196594238281 | Engine oil temperature |
| `OkToReloadTextures` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | True if it is ok to reload car textures at this time |
| `OnPitRoad` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | True | Is the player car on pit road between the cones |
| `P2P_Count` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Push2Pass count of usage (or remaining in Race) on your car |
| `P2P_Status` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Push2Pass active or not on your car |
| `PaceMode` | int | 1 | irsdk_PaceMode | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | Are we pacing or not |
| `PitOptRepairLeft` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Time left for optional repairs if repairs are active |
| `PitRepairLeft` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Time left for mandatory pit repairs if repairs are active |
| `PitSvFlags` | bitfield | 1 | irsdk_PitSvFlags | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0 | Bitfield of pit service checkboxes |
| `PitSvFuel` | float | 1 | l or kWh | **pit-only** | 100% | 0.0 | 0.063 | 0.0 | 0.0 | Pit service fuel add amount |
| `PitSvLFP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pit service left front tire pressure |
| `PitSvLRP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pit service left rear tire pressure |
| `PitSvRFP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pit service right front tire pressure |
| `PitSvRRP` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pit service right rear tire pressure |
| `PitSvTireCompound` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Pit service pending tire compound |
| `Pitch` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | 9.913 | 0.0424916706979274 | Pitch orientation |
| `PitchRate` | float | 1 | rad/s | **live** | 100% | 10.0 | 10.0 | 10.0 | -4.620045274350559 | Pitch rate |
| `PitchRate_ST` | float | 6 | rad/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Pitch rate at 360 Hz |
| `PitsOpen` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | True if pit stop is allowed for the current player |
| `PitstopActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Is the player getting pit stop service |
| `PlayerCarClass` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 4012 | Player car class id |
| `PlayerCarClassPosition` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players class position in race |
| `PlayerCarDriverIncidentCount` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Teams current drivers incident count for this session |
| `PlayerCarDryTireSetLimit` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players dry tire set limit |
| `PlayerCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 63 | Players carIdx |
| `PlayerCarInPitStall` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | True | Players car is properly in their pitstall |
| `PlayerCarMyIncidentCount` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players own incident count for this session |
| `PlayerCarPitSvStatus` | int | 1 | irsdk_PitSvStatus | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players car pit service status bits |
| `PlayerCarPosition` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players position in race |
| `PlayerCarPowerAdjust` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players power adjust |
| `PlayerCarSLBlinkRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | 0.0 | 7210.0 | Shift light blink rpm |
| `PlayerCarSLFirstRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | 0.0 | 6130.0 | Shift light first light rpm |
| `PlayerCarSLLastRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | 0.0 | 6800.0 | Shift light last light rpm |
| `PlayerCarSLShiftRPM` | float | 1 | revs/min | **static** | 100% | 0.0 | 0.0 | 0.0 | 6690.0 | Shift light shift rpm |
| `PlayerCarTeamIncidentCount` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players team incident count for this session |
| `PlayerCarTowTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players car is being towed if time is greater than zero |
| `PlayerCarWeightPenalty` | float | 1 | kg | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players weight penalty |
| `PlayerFastRepairsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players car number of fast repairs used |
| `PlayerIncidents` | int | 1 | irsdk_IncidentFlags | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Log incidents that the player recieved |
| `PlayerTireCompound` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | -1 | Players car current tire compound |
| `PlayerTrackSurface` | int | 1 | irsdk_TrkLoc | **slow** | 100% | 0.024 | 0.063 | 0.002 | 1 | Players car track surface type |
| `PlayerTrackSurfaceMaterial` | int | 1 | irsdk_TrkSurf | **slow** | 100% | 0.137 | 0.127 | 0.0 | 1 | Players car track surface material type |
| `Precipitation` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Precipitation at start/finish line |
| `PushToPass` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Push to pass button state |
| `PushToTalk` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Push to talk button state |
| `RFTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many right front tires are remaining  255 is unlimited |
| `RFTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many right front tires used so far |
| `RFbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | RF brake line pressure |
| `RFcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | RF tire cold pressure  as set in the garage |
| `RFodometer` | float | 1 | m | **slow** | 100% | 0.319 | 0.0 | 0.002 | 0.0 | RF distance tire traveled since being placed on car |
| `RFshockDefl` | float | 1 | m | **live** | 100% | 10.0 | 10.0 | 9.724 | 0.0821272134780883 | RF shock deflection |
| `RFshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | RF shock deflection at 360 Hz |
| `RFshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 5.294420407153666e | RF shock velocity |
| `RFshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | RF shock velocity at 360 Hz |
| `RFtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 51.584320068359375 | RF tire left carcass temperature |
| `RFtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 49.924102783203125 | RF tire middle carcass temperature |
| `RFtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 49.164642333984375 | RF tire right carcass temperature |
| `RFwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9933072328567505 | RF tire left percent tread remaining |
| `RFwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9970304369926453 | RF tire middle percent tread remaining |
| `RFwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9985687136650085 | RF tire right percent tread remaining |
| `RPM` | float | 1 | revs/min | **live** | 100% | 10.0 | 9.24 | 0.002 | 300.0 | Engine rpm |
| `RRTiresAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many right rear tires are remaining  255 is unlimited |
| `RRTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many right rear tires used so far |
| `RRbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | RR brake line pressure |
| `RRcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | RR tire cold pressure  as set in the garage |
| `RRodometer` | float | 1 | m | **slow** | 100% | 0.319 | 0.0 | 0.002 | 0.0 | RR distance tire traveled since being placed on car |
| `RRshockDefl` | float | 1 | m | **live** | 100% | 9.997 | 10.0 | 9.734 | 0.0500833690166473 | RR shock deflection |
| `RRshockDefl_ST` | float | 6 | m | **live** | 100% | 10.0 | 10.0 | 9.996 | [6 values] | RR shock deflection at 360 Hz |
| `RRshockVel` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 5.4644773626932874 | RR shock velocity |
| `RRshockVel_ST` | float | 6 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | RR shock velocity at 360 Hz |
| `RRtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 51.27423095703125 | RR tire left carcass temperature |
| `RRtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 50.605194091796875 | RR tire middle carcass temperature |
| `RRtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 49.552764892578125 | RR tire right carcass temperature |
| `RRwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9928191900253296 | RR tire left percent tread remaining |
| `RRwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9930263757705688 | RR tire middle percent tread remaining |
| `RRwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 0.9987149834632874 | RR tire right percent tread remaining |
| `RaceLaps` | int | 1 |  | **slow** | 100% | 0.008 | 0.0 | 0.01 | 0 | Laps completed in race |
| `RadioTransmitCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | -1 | The car index of the current person speaking on the radio |
| `RadioTransmitFrequencyIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | The frequency index of the current person speaking on the radio |
| `RadioTransmitRadioIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | The radio index of the current person speaking on the radio |
| `RearTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many rear tire sets are remaining  255 is unlimited |
| `RearTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many rear tire sets used so far |
| `RelativeHumidity` | float | 1 | % | **live** | 100% | 10.0 | 10.0 | 10.0 | 0.8863946795463562 | Relative Humidity at start/finish line |
| `ReplayFrameNum` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 10.0 | 34940 | Integer replay frame number (60 per second) |
| `ReplayFrameNumEnd` | int | 1 |  | **live** | 100% | 10.0 | 10.0 | 0.006 | 1201 | Integer replay frame number from end of tape |
| `ReplayPlaySlowMotion` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 0=not slow motion  1=replay is in slow motion |
| `ReplayPlaySpeed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | Replay playback speed |
| `ReplaySessionNum` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 2 | Replay session number |
| `ReplaySessionTime` | double | 1 | s | **pit-only** | 100% | 0.0 | 0.063 | 10.0 | 56.01333363850911 | Seconds since replay session start |
| `RightTireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many right tire sets are remaining  255 is unlimited |
| `RightTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many right tire sets used so far |
| `Roll` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | 10.0 | 0.0023254875559359 | Roll orientation |
| `RollRate` | float | 1 | rad/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 7.673523896301049e | Roll rate |
| `RollRate_ST` | float | 6 | rad/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Roll rate at 360 Hz |
| `SessionFlags` | bitfield | 1 | irsdk_Flags | **slow** | 100% | 0.013 | 0.0 | 0.002 | 268435968 | Session flags |
| `SessionJokerLapsRemain` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Joker laps remaining to be taken |
| `SessionLapsRemain` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 32767 | Old laps left till session ends use SessionLapsRemainEx |
| `SessionLapsRemainEx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 32767 | New improved laps left till session ends |
| `SessionLapsTotal` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 32767 | Total number of laps in session |
| `SessionNum` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 2 | Session number |
| `SessionOnJokerLap` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Player is currently completing a joker lap |
| `SessionState` | int | 1 | irsdk_SessionState | **slow** | 100% | 0.003 | 0.0 | 0.004 | 1 | Session state |
| `SessionTick` | int | 1 |  | **live** | 100% | 10.0 | 10.0 | 10.0 | 73431 | Current update number |
| `SessionTime` | double | 1 | s | **live** | 100% | 10.0 | 10.0 | 10.0 | 77.01666667014051 | Seconds since session start |
| `SessionTimeOfDay` | float | 1 | s | **live** | 100% | 1.001 | 0.949 | 1.001 | 35777.0 | Time of day in seconds |
| `SessionTimeRemain` | double | 1 | s | **live** | 100% | 6.966 | 0.0 | 9.407 | 41.99999999652616 | Seconds left till session ends |
| `SessionTimeTotal` | double | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 900.0 | Total number of seconds in session |
| `SessionUniqueID` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 3 | Session ID |
| `ShiftGrindRPM` | float | 1 | RPM | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | RPM of shifter grinding noise |
| `ShiftIndicatorPct` | float | 1 | % | **live** | 100% | 5.144 | 0.127 | 0.0 | 0.0 | DEPRECATED use DriverCarSLBlinkRPM instead |
| `ShiftPowerPct` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Friction torque applied to gears when shifting or grinding |
| `Shifter` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Log inputs from the players shifter control |
| `Skies` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 2 | Skies (0=clear/1=p cloudy/2=m cloudy/3=overcast) |
| `SolarAltitude` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | 10.0 | 0.6230772137641907 | Sun angle above horizon in radians |
| `SolarAzimuth` | float | 1 | rad | **live** | 100% | 10.0 | 10.0 | 10.0 | 1.9323352575302124 | Sun angle clockwise from north in radians |
| `Speed` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 0.0001253135997103 | GPS vehicle speed |
| `SteeringFFBEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | Force feedback is enabled |
| `SteeringWheelAngle` | float | 1 | rad | **live** | 100% | 8.388 | 9.051 | 0.002 | 0.0 | Steering wheel angle |
| `SteeringWheelAngleMax` | float | 1 | rad | **static** | 100% | 0.0 | 0.0 | 0.0 | 13.864885330200195 | Steering wheel max angle |
| `SteeringWheelLimiter` | float | 1 | % | **live** | 100% | 5.091 | 8.924 | 0.0 | 0.0 | Force feedback limiter strength limits impacts and oscillation |
| `SteeringWheelMaxForceNm` | float | 1 | N*m | **static** | 100% | 0.0 | 0.0 | 0.0 | 28.33333396911621 | Value of strength or max force slider in Nm for FFB |
| `SteeringWheelPctDamper` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Force feedback % max damping |
| `SteeringWheelPctIntensity` | float | 1 | % | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Force feedback % max intensity |
| `SteeringWheelPctSmoothing` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Force feedback % max smoothing |
| `SteeringWheelPctTorque` | float | 1 | % | **live** | 100% | 9.995 | 9.937 | 0.002 | 0.0 | Force feedback % max torque on steering shaft unsigned |
| `SteeringWheelPctTorqueSign` | float | 1 | % | **live** | 100% | 9.995 | 10.0 | 0.002 | 0.0 | Force feedback % max torque on steering shaft signed |
| `SteeringWheelPctTorqueSignStops` | float | 1 | % | **live** | 100% | 9.995 | 10.0 | 0.002 | 0.0 | Force feedback % max torque on steering shaft signed stops |
| `SteeringWheelPeakForceNm` | float | 1 | N*m | **static** | 100% | 0.0 | 0.0 | 0.0 | -1.0 | Peak torque mapping to direct input units for FFB |
| `SteeringWheelTorque` | float | 1 | N*m | **live** | 100% | 10.0 | 10.0 | 10.0 | 2.5573689937591553 | Output torque on steering shaft |
| `SteeringWheelTorque_ST` | float | 6 | N*m | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Output torque on steering shaft at 360 Hz |
| `SteeringWheelUseLinear` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | True if steering wheel force is using linear mode |
| `Throttle` | float | 1 | % | **live** | 100% | 1.725 | 0.886 | 0.0 | 0.0 | 0=off throttle to 1=full throttle |
| `ThrottleRaw` | float | 1 | % | **live** | 100% | 1.385 | 0.823 | 0.0 | 0.0 | Raw throttle input 0=off throttle to 1=full throttle |
| `TireLF_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.321 | 0.0 | 0.05 | 0.0 | Players LF Tire Sound rumblestrip pitch |
| `TireLR_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.303 | 0.0 | 0.048 | 0.0 | Players LR Tire Sound rumblestrip pitch |
| `TireRF_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.398 | 0.0 | 0.044 | 0.0 | Players RF Tire Sound rumblestrip pitch |
| `TireRR_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.424 | 0.0 | 0.044 | 0.0 | Players RR Tire Sound rumblestrip pitch |
| `TireSetsAvailable` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How many tire sets are remaining  255 is unlimited |
| `TireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many tire sets used so far |
| `TrackTemp` | float | 1 | C | **slow** | 100% | 0.008 | 0.0 | 0.004 | 26.111114501953125 | Deprecated  set to TrackTempCrew |
| `TrackTempCrew` | float | 1 | C | **slow** | 100% | 0.008 | 0.0 | 0.004 | 26.111114501953125 | Temperature of track measured by crew around track |
| `TrackWetness` | int | 1 | irsdk_TrackWetness | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | How wet is the average track surface |
| `VelocityX` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | -0.000107659747300 | X velocity |
| `VelocityX_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | X velocity |
| `VelocityY` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 6.41317164991051e- | Y velocity |
| `VelocityY_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Y velocity |
| `VelocityZ` | float | 1 | m/s | **live** | 100% | 10.0 | 10.0 | 10.0 | 2.0716165636258665 | Z velocity |
| `VelocityZ_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Z velocity |
| `VertAccel` | float | 1 | m/s^2 | **live** | 100% | 10.0 | 10.0 | 9.996 | 9.798622131347656 | Vertical acceleration (including gravity) |
| `VertAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Vertical acceleration (including gravity) at 360 Hz |
| `VidCapActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | True if video currently being captured |
| `VidCapEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | True if video capture system is enabled |
| `Voltage` | float | 1 | V | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Engine voltage |
| `WaterLevel` | float | 1 | l | **static** | 100% | 0.0 | 0.0 | 0.0 | 7.400000095367432 | Engine coolant level |
| `WaterTemp` | float | 1 | C | **live** | 100% | 10.0 | 9.557 | 0.002 | 78.19342803955078 | Engine coolant temp |
| `WeatherDeclaredWet` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | The steward says rain tires can be used |
| `WindDir` | float | 1 | rad | **live** | 100% | 9.997 | 10.0 | 9.998 | 6.804914474487305 | Wind direction at start/finish line |
| `WindVel` | float | 1 | m/s | **live** | 100% | 9.995 | 10.0 | 10.0 | 3.8991007804870605 | Wind velocity at start/finish line |
| `Yaw` | float | 1 | rad | **live** | 100% | 9.995 | 10.0 | 7.637 | 2.949983596801758 | Yaw orientation |
| `YawNorth` | float | 1 | rad | **live** | 100% | 9.987 | 10.0 | 7.637 | 1.3199787139892578 | Yaw orientation relative to north |
| `YawRate` | float | 1 | rad/s | **live** | 100% | 10.0 | 10.0 | 10.0 | -9.65534127317369e | Yaw rate |
| `YawRate_ST` | float | 6 | rad/s | **live** | 100% | 10.0 | 10.0 | 10.0 | [6 values] | Yaw rate at 360 Hz |
| `dcABS` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 2.0 | In car abs adjustment |
| `dcBrakeBias` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 50.0 | In car brake bias adjustment |
| `dcHeadlightFlash` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car headlight flash control active |
| `dcPitSpeedLimiterToggle` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Track if pit speed limiter system is enabled |
| `dcStarter` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car trigger car starter |
| `dcToggleWindshieldWipers` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car turn wipers on or off |
| `dcTractionControl` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1.0 | In car traction control adjustment |
| `dcTriggerWindshieldWipers` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car momentarily turn on wipers |
| `dpFastRepair` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop fast repair set |
| `dpFuelAddKg` | float | 1 | kg | **pit-only** | 100% | 0.0 | 0.063 | 0.0 | 0.0 | Pitstop fuel add amount |
| `dpFuelAutoFillActive` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop auto fill fuel next stop flag |
| `dpFuelAutoFillEnabled` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop auto fill fuel system enabled |
| `dpFuelFill` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop fuel fill flag |
| `dpLFTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop lf tire change request |
| `dpLFTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pitstop lf tire cold pressure adjustment |
| `dpLRTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop lr tire change request |
| `dpLRTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pitstop lr tire cold pressure adjustment |
| `dpRFTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop rf tire change request |
| `dpRFTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pitstop rf cold tire pressure adjustment |
| `dpRRTireChange` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop rr tire change request |
| `dpRRTireColdPress` | float | 1 | Pa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | Pitstop rr cold tire pressure adjustment |
| `dpWindshieldTearoff` | float | 1 |  | **pit-only** | 100% | 0.0 | 0.063 | 0.002 | 0.0 | Pitstop windshield tearoff |
