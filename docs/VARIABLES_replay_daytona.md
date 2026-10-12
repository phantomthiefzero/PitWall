# Variable behavior report

Recording: `recordings/replay-daytona-gr86.jsonl.gz`  
Ticks: 1201 over 120.0 s  
Session-info updates seen: 1  
Time by segment (s): {'replay': 120.0}

Labels: **live** changes while driving; **slow** changes rarely (counters, flags); **pit-only** changes only in pit activity; **static** never changed; **absent** never present; **live(replay)** changes at live rates but was only ever seen in a replay, which proves iRacing records it, not that it is populated in a live session; **replay-blank** present live but not in replay.

| Variable | Type | N | Unit | Label | Present | Δ/s track | Δ/s pit | Δ/s replay | Sample | Description |
|---|---|---|---|---|---|---|---|---|---|---|
| `AirDensity` | float | 1 | kg/m^3 | **live(replay)** | 100% | – | – | 8.64 | 1.1915833950042725 | Density of air at start/finish line |
| `AirPressure` | float | 1 | Pa | **live(replay)** | 100% | – | – | 2.3 | 101598.859375 | Pressure of air at start/finish line |
| `AirTemp` | float | 1 | C | **live(replay)** | 100% | – | – | 9.999 | 21.32032012939453 | Temperature of air at start/finish line |
| `Brake` | float | 1 | % | **live(replay)** | 100% | – | – | 1.192 | 0.0 | 0=brake released to 1=max pedal force |
| `BrakeABSactive` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | true if abs is currently reducing brake force pressure |
| `BrakeRaw` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Raw brake input 0=brake released to 1=max pedal force |
| `CamCameraNumber` | int | 1 |  | **slow** | 100% | – | – | 0.075 | 3 | Active camera number |
| `CamCameraState` | bitfield | 1 | irsdk_CameraState | **static** | 100% | – | – | 0.0 | 81 | State of camera system |
| `CamCarIdx` | int | 1 |  | **static** | 100% | – | – | 0.0 | 13 | Active camera's focus car index |
| `CamGroupNumber` | int | 1 |  | **static** | 100% | – | – | 0.0 | 18 | Active camera group number |
| `CarDistAhead` | float | 1 | m | **live(replay)** | 100% | – | – | 9.999 | 18.45635986328125 | Distance to first car in front of player in meters |
| `CarDistBehind` | float | 1 | m | **live(replay)** | 100% | – | – | 9.99 | 1.85882568359375 | Distance to first car behind player in meters |
| `CarIdxBestLapNum` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Cars best lap number |
| `CarIdxBestLapTime` | float | 64 | s | **static** | 100% | – | – | 0.0 | [64 values] | Cars best lap time |
| `CarIdxClass` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Cars class id by car index |
| `CarIdxClassPosition` | int | 64 |  | **slow** | 100% | – | – | 0.05 | [64 values] | Cars class position in race by car index |
| `CarIdxEstTime` | float | 64 | s | **static** | 100% | – | – | 0.0 | [64 values] | Estimated time to reach current location on track |
| `CarIdxF2Time` | float | 64 | s | **slow** | 100% | – | – | 0.058 | [64 values] | Race time behind leader or fastest lap time otherwise |
| `CarIdxFastRepairsUsed` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | How many fast repairs each car has used |
| `CarIdxGear` | int | 64 |  | **live(replay)** | 100% | – | – | 2.783 | [64 values] | -1=reverse  0=neutral  1..n=current gear by car index |
| `CarIdxLap` | int | 64 |  | **slow** | 100% | – | – | 0.142 | [64 values] | Laps started by car index |
| `CarIdxLapCompleted` | int | 64 |  | **slow** | 100% | – | – | 0.142 | [64 values] | Laps completed by car index |
| `CarIdxLapDistPct` | float | 64 | % | **live(replay)** | 100% | – | – | 9.999 | [64 values] | Percentage distance around lap by car index |
| `CarIdxLastLapTime` | float | 64 | s | **slow** | 100% | – | – | 0.142 | [64 values] | Cars last lap time |
| `CarIdxOnPitRoad` | bool | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | On pit road between the cones by car index |
| `CarIdxP2P_Count` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Push2Pass count of usage (or remaining in Race) |
| `CarIdxP2P_Status` | bool | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Push2Pass active or not |
| `CarIdxPaceFlags` | bitfield | 64 | irsdk_PaceFlags | **static** | 100% | – | – | 0.0 | [64 values] | Pacing status flags for each car |
| `CarIdxPaceLine` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | What line cars are pacing in  or -1 if not pacing |
| `CarIdxPaceRow` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | What row cars are pacing in  or -1 if not pacing |
| `CarIdxPosition` | int | 64 |  | **slow** | 100% | – | – | 0.05 | [64 values] | Cars position in race by car index |
| `CarIdxQualTireCompound` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Cars Qual tire compound |
| `CarIdxQualTireCompoundLocked` | bool | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Cars Qual tire compound is locked-in |
| `CarIdxRPM` | float | 64 | revs/min | **live(replay)** | 100% | – | – | 7.732 | [64 values] | Engine rpm by car index |
| `CarIdxSessionFlags` | bitfield | 64 | irsdk_Flags | **static** | 100% | – | – | 0.0 | [64 values] | Session flags for each player |
| `CarIdxSteer` | float | 64 | rad | **live(replay)** | 100% | – | – | 9.999 | [64 values] | Steering wheel angle by car index |
| `CarIdxTireCompound` | int | 64 |  | **static** | 100% | – | – | 0.0 | [64 values] | Cars current tire compound |
| `CarIdxTrackSurface` | int | 64 | irsdk_TrkLoc | **slow** | 100% | – | – | 0.117 | [64 values] | Track surface type by car index |
| `CarIdxTrackSurfaceMaterial` | int | 64 | irsdk_TrkSurf | **live(replay)** | 100% | – | – | 2.025 | [64 values] | Track surface material type by car index |
| `CarLeftRight` | int | 1 | irsdk_CarLeftRight | **static** | 100% | – | – | 0.0 | 0 | Notify if car is to the left or right of driver |
| `ChanAvgLatency` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Communications average latency |
| `ChanClockSkew` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Communications server clock skew |
| `ChanLatency` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Communications latency |
| `ChanPartnerQuality` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Partner communications quality |
| `ChanQuality` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Communications quality |
| `Clutch` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | 0=disengaged to 1=fully engaged |
| `ClutchRaw` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Raw clutch input 0=disengaged to 1=fully engaged |
| `CpuUsageBG` | float | 1 | % | **live(replay)** | 100% | – | – | 7.674 | 0.0990002602338790 | Percent of available time bg thread took with a 1 sec avg |
| `CpuUsageFG` | float | 1 | % | **live(replay)** | 100% | – | – | 9.999 | 1.3123383522033691 | Percent of available time fg thread took with a 1 sec avg |
| `DCDriversSoFar` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Number of team drivers who have run a stint |
| `DCLapStatus` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Status of driver change lap requirements |
| `DisplayUnits` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Default units for the user interface 0 = english 1 = metric |
| `DriverMarker` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Driver activated flag |
| `Engine0_RPM` | float | 1 | revs/min | **static** | 100% | – | – | 0.0 | 0.0037650233134627 | Engine0Engine rpm |
| `EngineWarnings` | bitfield | 1 | irsdk_EngineWarnings | **static** | 100% | – | – | 0.0 | 0 | Bitfield for warning lights |
| `EnterExitReset` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Indicate action the reset key will take 0 enter 1 exit 2 reset |
| `FastRepairAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many fast repairs left  255 is unlimited |
| `FastRepairUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many fast repairs used so far |
| `FogLevel` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Fog level at start/finish line |
| `FrameRate` | float | 1 | fps | **live(replay)** | 100% | – | – | 9.999 | 47.046546936035156 | Average frames per second |
| `FrontTireSetsAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many front tire sets are remaining  255 is unlimited |
| `FrontTireSetsUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many front tire sets used so far |
| `FuelLevel` | float | 1 | l | **static** | 100% | – | – | 0.0 | 0.0 | Liters of fuel remaining |
| `FuelLevelPct` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Percent fuel remaining |
| `FuelPress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 0.0 | Engine fuel pressure |
| `FuelUsePerHour` | float | 1 | kg/h | **static** | 100% | – | – | 0.0 | 0.0 | Engine fuel used instantaneous |
| `Gear` | int | 1 |  | **slow** | 100% | – | – | 0.258 | 2 | -1=reverse  0=neutral  1..n=current gear |
| `GpuUsage` | float | 1 | % | **live(replay)** | 100% | – | – | 9.999 | 1.2779003381729126 | Percent of available time gpu took with a 1 sec avg |
| `HandbrakeRaw` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Raw handbrake input 0=handbrake released to 1=max force |
| `IsDiskLoggingActive` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 0=disk based telemetry file not being written  1=being written |
| `IsDiskLoggingEnabled` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 0=disk based telemetry turned off  1=turned on |
| `IsGarageVisible` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 1=Garage screen is visible |
| `IsInGarage` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 1=Car in garage physics running |
| `IsOnTrack` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 1=Car on track physics running with player in car |
| `IsOnTrackCar` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 1=Car on track physics running |
| `IsReplayPlaying` | bool | 1 |  | **static** | 100% | – | – | 0.0 | True | 0=replay not playing  1=replay playing |
| `LFTiresAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many left front tires are remaining  255 is unlimited |
| `LFTiresUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many left front tires used so far |
| `LFbrakeLinePress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 0.0 | LF brake line pressure |
| `LFcoldPressure` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 172.36900329589844 | LF tire cold pressure  as set in the garage |
| `LFodometer` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0 | LF distance tire traveled since being placed on car |
| `LFshockDefl` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0801144540309906 | LF shock deflection |
| `LFshockDefl_ST` | float | 6 | m | **static** | 100% | – | – | 0.0 | [6 values] | LF shock deflection at 360 Hz |
| `LFshockVel` | float | 1 | m/s | **static** | 100% | – | – | 0.0 | -0.229844763875007 | LF shock velocity |
| `LFshockVel_ST` | float | 6 | m/s | **static** | 100% | – | – | 0.0 | [6 values] | LF shock velocity at 360 Hz |
| `LFtempCL` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | LF tire left carcass temperature |
| `LFtempCM` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | LF tire middle carcass temperature |
| `LFtempCR` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | LF tire right carcass temperature |
| `LFwearL` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | LF tire left percent tread remaining |
| `LFwearM` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | LF tire middle percent tread remaining |
| `LFwearR` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | LF tire right percent tread remaining |
| `LRTiresAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many left rear tires are remaining  255 is unlimited |
| `LRTiresUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many left rear tires used so far |
| `LRbrakeLinePress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 0.0 | LR brake line pressure |
| `LRcoldPressure` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 172.36900329589844 | LR tire cold pressure  as set in the garage |
| `LRodometer` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0 | LR distance tire traveled since being placed on car |
| `LRshockDefl` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0596670210361480 | LR shock deflection |
| `LRshockDefl_ST` | float | 6 | m | **static** | 100% | – | – | 0.0 | [6 values] | LR shock deflection at 360 Hz |
| `LRshockVel` | float | 1 | m/s | **static** | 100% | – | – | 0.0 | -0.210684210062026 | LR shock velocity |
| `LRshockVel_ST` | float | 6 | m/s | **static** | 100% | – | – | 0.0 | [6 values] | LR shock velocity at 360 Hz |
| `LRtempCL` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | LR tire left carcass temperature |
| `LRtempCM` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | LR tire middle carcass temperature |
| `LRtempCR` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | LR tire right carcass temperature |
| `LRwearL` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | LR tire left percent tread remaining |
| `LRwearM` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | LR tire middle percent tread remaining |
| `LRwearR` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | LR tire right percent tread remaining |
| `Lap` | int | 1 |  | **slow** | 100% | – | – | 0.008 | 1 | Laps started count |
| `LapBestLap` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Players best lap number |
| `LapBestLapTime` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Players best lap time |
| `LapBestNLapLap` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Player last lap in best N average lap time |
| `LapBestNLapTime` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Player best N average lap time |
| `LapCompleted` | int | 1 |  | **slow** | 100% | – | – | 0.008 | 0 | Laps completed count |
| `LapCurrentLapTime` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Estimate of players current lap time as shown in F3 box |
| `LapDeltaToBestLap` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Delta time for best lap |
| `LapDeltaToBestLap_DD` | float | 1 | s/s | **static** | 100% | – | – | 0.0 | 0.0 | Rate of change of delta time for best lap |
| `LapDeltaToBestLap_OK` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Delta time for best lap is valid |
| `LapDeltaToOptimalLap` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Delta time for optimal lap |
| `LapDeltaToOptimalLap_DD` | float | 1 | s/s | **static** | 100% | – | – | 0.0 | 0.0 | Rate of change of delta time for optimal lap |
| `LapDeltaToOptimalLap_OK` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Delta time for optimal lap is valid |
| `LapDeltaToSessionBestLap` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Delta time for session best lap |
| `LapDeltaToSessionBestLap_DD` | float | 1 | s/s | **static** | 100% | – | – | 0.0 | 0.0 | Rate of change of delta time for session best lap |
| `LapDeltaToSessionBestLap_OK` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Delta time for session best lap is valid |
| `LapDeltaToSessionLastlLap` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Delta time for session last lap |
| `LapDeltaToSessionLastlLap_DD` | float | 1 | s/s | **static** | 100% | – | – | 0.0 | 0.0 | Rate of change of delta time for session last lap |
| `LapDeltaToSessionLastlLap_OK` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Delta time for session last lap is valid |
| `LapDeltaToSessionOptimalLap` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_DD` | float | 1 | s/s | **static** | 100% | – | – | 0.0 | 0.0 | Rate of change of delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_OK` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Delta time for session optimal lap is valid |
| `LapDist` | float | 1 | m | **live(replay)** | 100% | – | – | 9.999 | 940.81005859375 | Meters traveled from S/F this lap |
| `LapDistPct` | float | 1 | % | **live(replay)** | 100% | – | – | 9.999 | 0.1991574019193649 | Percentage distance around lap |
| `LapLasNLapSeq` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Player num consecutive clean laps completed for N average |
| `LapLastLapTime` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Players last lap time |
| `LapLastNLapTime` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Player last N average lap time |
| `LatAccel` | float | 1 | m/s^2 | **live(replay)** | 100% | – | – | 9.999 | -12.79876232147216 | Lateral acceleration (including gravity) |
| `LatAccel_ST` | float | 6 | m/s^2 | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Lateral acceleration (including gravity) at 360 Hz |
| `LeftTireSetsAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many left tire sets are remaining  255 is unlimited |
| `LeftTireSetsUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many left tire sets used so far |
| `LoadNumTextures` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | True if the car_num texture will be loaded |
| `LongAccel` | float | 1 | m/s^2 | **live(replay)** | 100% | – | – | 9.999 | 0.4628142118453979 | Longitudinal acceleration (including gravity) |
| `LongAccel_ST` | float | 6 | m/s^2 | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Longitudinal acceleration (including gravity) at 360 Hz |
| `ManifoldPress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 1.0 | Engine manifold pressure |
| `ManualBoost` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Hybrid manual boost state |
| `ManualNoBoost` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Hybrid manual no boost state |
| `MemPageFaultSec` | float | 1 |  | **live(replay)** | 100% | – | – | 1.283 | 13.0 | Memory page faults per second |
| `MemSoftPageFaultSec` | float | 1 |  | **live(replay)** | 100% | – | – | 1.925 | 69615.0 | Memory soft page faults per second |
| `OilLevel` | float | 1 | l | **static** | 100% | – | – | 0.0 | 5.019999980926514 | Engine oil level |
| `OilPress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 0.0 | Engine oil pressure |
| `OilTemp` | float | 1 | C | **static** | 100% | – | – | 0.0 | 77.0 | Engine oil temperature |
| `OkToReloadTextures` | bool | 1 |  | **static** | 100% | – | – | 0.0 | True | True if it is ok to reload car textures at this time |
| `OnPitRoad` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Is the player car on pit road between the cones |
| `P2P_Count` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Push2Pass count of usage (or remaining in Race) on your car |
| `P2P_Status` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Push2Pass active or not on your car |
| `PaceMode` | int | 1 | irsdk_PaceMode | **static** | 100% | – | – | 0.0 | 4 | Are we pacing or not |
| `PitOptRepairLeft` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Time left for optional repairs if repairs are active |
| `PitRepairLeft` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Time left for mandatory pit repairs if repairs are active |
| `PitSvFlags` | bitfield | 1 | irsdk_PitSvFlags | **static** | 100% | – | – | 0.0 | 0 | Bitfield of pit service checkboxes |
| `PitSvFuel` | float | 1 | l or kWh | **static** | 100% | – | – | 0.0 | 0.0 | Pit service fuel add amount |
| `PitSvLFP` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 0.0 | Pit service left front tire pressure |
| `PitSvLRP` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 0.0 | Pit service left rear tire pressure |
| `PitSvRFP` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 0.0 | Pit service right front tire pressure |
| `PitSvRRP` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 0.0 | Pit service right rear tire pressure |
| `PitSvTireCompound` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Pit service pending tire compound |
| `Pitch` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.999 | 0.0052452445961534 | Pitch orientation |
| `PitchRate` | float | 1 | rad/s | **live(replay)** | 100% | – | – | 9.999 | -0.013803215697407 | Pitch rate |
| `PitchRate_ST` | float | 6 | rad/s | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Pitch rate at 360 Hz |
| `PitsOpen` | bool | 1 |  | **static** | 100% | – | – | 0.0 | True | True if pit stop is allowed for the current player |
| `PitstopActive` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Is the player getting pit stop service |
| `PlayerCarClass` | int | 1 |  | **static** | 100% | – | – | 0.0 | 4012 | Player car class id |
| `PlayerCarClassPosition` | int | 1 |  | **slow** | 100% | – | – | 0.017 | 12 | Players class position in race |
| `PlayerCarDriverIncidentCount` | int | 1 |  | **static** | 100% | – | – | 0.0 | 3 | Teams current drivers incident count for this session |
| `PlayerCarDryTireSetLimit` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Players dry tire set limit |
| `PlayerCarIdx` | int | 1 |  | **static** | 100% | – | – | 0.0 | 13 | Players carIdx |
| `PlayerCarInPitStall` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Players car is properly in their pitstall |
| `PlayerCarMyIncidentCount` | int | 1 |  | **static** | 100% | – | – | 0.0 | 3 | Players own incident count for this session |
| `PlayerCarPitSvStatus` | int | 1 | irsdk_PitSvStatus | **static** | 100% | – | – | 0.0 | 0 | Players car pit service status bits |
| `PlayerCarPosition` | int | 1 |  | **slow** | 100% | – | – | 0.017 | 12 | Players position in race |
| `PlayerCarPowerAdjust` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Players power adjust |
| `PlayerCarSLBlinkRPM` | float | 1 | revs/min | **static** | 100% | – | – | 0.0 | 7210.0 | Shift light blink rpm |
| `PlayerCarSLFirstRPM` | float | 1 | revs/min | **static** | 100% | – | – | 0.0 | 6130.0 | Shift light first light rpm |
| `PlayerCarSLLastRPM` | float | 1 | revs/min | **static** | 100% | – | – | 0.0 | 6800.0 | Shift light last light rpm |
| `PlayerCarSLShiftRPM` | float | 1 | revs/min | **static** | 100% | – | – | 0.0 | 6690.0 | Shift light shift rpm |
| `PlayerCarTeamIncidentCount` | int | 1 |  | **static** | 100% | – | – | 0.0 | 3 | Players team incident count for this session |
| `PlayerCarTowTime` | float | 1 | s | **static** | 100% | – | – | 0.0 | 0.0 | Players car is being towed if time is greater than zero |
| `PlayerCarWeightPenalty` | float | 1 | kg | **static** | 100% | – | – | 0.0 | 0.0 | Players weight penalty |
| `PlayerFastRepairsUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Players car number of fast repairs used |
| `PlayerIncidents` | int | 1 | irsdk_IncidentFlags | **static** | 100% | – | – | 0.0 | 0 | Log incidents that the player recieved |
| `PlayerTireCompound` | int | 1 |  | **static** | 100% | – | – | 0.0 | -1 | Players car current tire compound |
| `PlayerTrackSurface` | int | 1 | irsdk_TrkLoc | **static** | 100% | – | – | 0.0 | 3 | Players car track surface type |
| `PlayerTrackSurfaceMaterial` | int | 1 | irsdk_TrkSurf | **slow** | 100% | – | – | 0.092 | 2 | Players car track surface material type |
| `Precipitation` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Precipitation at start/finish line |
| `PushToPass` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Push to pass button state |
| `PushToTalk` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Push to talk button state |
| `RFTiresAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many right front tires are remaining  255 is unlimited |
| `RFTiresUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many right front tires used so far |
| `RFbrakeLinePress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 0.0 | RF brake line pressure |
| `RFcoldPressure` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 172.36900329589844 | RF tire cold pressure  as set in the garage |
| `RFodometer` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0 | RF distance tire traveled since being placed on car |
| `RFshockDefl` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.080148845911026 | RF shock deflection |
| `RFshockDefl_ST` | float | 6 | m | **static** | 100% | – | – | 0.0 | [6 values] | RF shock deflection at 360 Hz |
| `RFshockVel` | float | 1 | m/s | **static** | 100% | – | – | 0.0 | -0.212224677205085 | RF shock velocity |
| `RFshockVel_ST` | float | 6 | m/s | **static** | 100% | – | – | 0.0 | [6 values] | RF shock velocity at 360 Hz |
| `RFtempCL` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | RF tire left carcass temperature |
| `RFtempCM` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | RF tire middle carcass temperature |
| `RFtempCR` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | RF tire right carcass temperature |
| `RFwearL` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | RF tire left percent tread remaining |
| `RFwearM` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | RF tire middle percent tread remaining |
| `RFwearR` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | RF tire right percent tread remaining |
| `RPM` | float | 1 | revs/min | **live(replay)** | 100% | – | – | 7.732 | 5111.81884765625 | Engine rpm |
| `RRTiresAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many right rear tires are remaining  255 is unlimited |
| `RRTiresUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many right rear tires used so far |
| `RRbrakeLinePress` | float | 1 | bar | **static** | 100% | – | – | 0.0 | 0.0 | RR brake line pressure |
| `RRcoldPressure` | float | 1 | kPa | **static** | 100% | – | – | 0.0 | 172.36900329589844 | RR tire cold pressure  as set in the garage |
| `RRodometer` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0 | RR distance tire traveled since being placed on car |
| `RRshockDefl` | float | 1 | m | **static** | 100% | – | – | 0.0 | 0.0596862137317657 | RR shock deflection |
| `RRshockDefl_ST` | float | 6 | m | **static** | 100% | – | – | 0.0 | [6 values] | RR shock deflection at 360 Hz |
| `RRshockVel` | float | 1 | m/s | **static** | 100% | – | – | 0.0 | -0.198830470442771 | RR shock velocity |
| `RRshockVel_ST` | float | 6 | m/s | **static** | 100% | – | – | 0.0 | [6 values] | RR shock velocity at 360 Hz |
| `RRtempCL` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | RR tire left carcass temperature |
| `RRtempCM` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | RR tire middle carcass temperature |
| `RRtempCR` | float | 1 | C | **static** | 100% | – | – | 0.0 | 47.110076904296875 | RR tire right carcass temperature |
| `RRwearL` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | RR tire left percent tread remaining |
| `RRwearM` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | RR tire middle percent tread remaining |
| `RRwearR` | float | 1 | % | **static** | 100% | – | – | 0.0 | 1.0 | RR tire right percent tread remaining |
| `RaceLaps` | int | 1 |  | **slow** | 100% | – | – | 0.008 | 1 | Laps completed in race |
| `RadioTransmitCarIdx` | int | 1 |  | **static** | 100% | – | – | 0.0 | -1 | The car index of the current person speaking on the radio |
| `RadioTransmitFrequencyIdx` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | The frequency index of the current person speaking on the radio |
| `RadioTransmitRadioIdx` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | The radio index of the current person speaking on the radio |
| `RearTireSetsAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many rear tire sets are remaining  255 is unlimited |
| `RearTireSetsUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many rear tire sets used so far |
| `RelativeHumidity` | float | 1 | % | **slow** | 100% | – | – | 0.217 | 0.917576789855957 | Relative Humidity at start/finish line |
| `ReplayFrameNum` | int | 1 |  | **live(replay)** | 100% | – | – | 9.999 | 49299 | Integer replay frame number (60 per second) |
| `ReplayFrameNumEnd` | int | 1 |  | **live(replay)** | 100% | – | – | 9.999 | 68843 | Integer replay frame number from end of tape |
| `ReplayPlaySlowMotion` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | 0=not slow motion  1=replay is in slow motion |
| `ReplayPlaySpeed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 1 | Replay playback speed |
| `ReplaySessionNum` | int | 1 |  | **static** | 100% | – | – | 0.0 | 2 | Replay session number |
| `ReplaySessionTime` | double | 1 | s | **live(replay)** | 100% | – | – | 9.999 | 222.7299926757813 | Seconds since replay session start |
| `RightTireSetsAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many right tire sets are remaining  255 is unlimited |
| `RightTireSetsUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many right tire sets used so far |
| `Roll` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.999 | -0.022398274391889 | Roll orientation |
| `RollRate` | float | 1 | rad/s | **live(replay)** | 100% | – | – | 9.999 | -0.008650661446154 | Roll rate |
| `RollRate_ST` | float | 6 | rad/s | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Roll rate at 360 Hz |
| `SessionFlags` | bitfield | 1 | irsdk_Flags | **static** | 100% | – | – | 0.0 | 0 | Session flags |
| `SessionJokerLapsRemain` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Joker laps remaining to be taken |
| `SessionLapsRemain` | int | 1 |  | **static** | 100% | – | – | 0.0 | 32767 | Old laps left till session ends use SessionLapsRemainEx |
| `SessionLapsRemainEx` | int | 1 |  | **static** | 100% | – | – | 0.0 | 32767 | New improved laps left till session ends |
| `SessionLapsTotal` | int | 1 |  | **static** | 100% | – | – | 0.0 | 32767 | Total number of laps in session |
| `SessionNum` | int | 1 |  | **static** | 100% | – | – | 0.0 | 2 | Session number |
| `SessionOnJokerLap` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Player is currently completing a joker lap |
| `SessionState` | int | 1 | irsdk_SessionState | **static** | 100% | – | – | 0.0 | 4 | Session state |
| `SessionTick` | int | 1 |  | **live(replay)** | 100% | – | – | 9.999 | 20766 | Current update number |
| `SessionTime` | double | 1 | s | **live(replay)** | 100% | – | – | 9.999 | 222.7299926757813 | Seconds since session start |
| `SessionTimeOfDay` | float | 1 | s | **live(replay)** | 100% | – | – | 1.0 | 31722.0 | Time of day in seconds |
| `SessionTimeRemain` | double | 1 | s | **live(replay)** | 100% | – | – | 9.999 | 677.2700073242187 | Seconds left till session ends |
| `SessionTimeTotal` | double | 1 | s | **static** | 100% | – | – | 0.0 | 900.0 | Total number of seconds in session |
| `SessionUniqueID` | int | 1 |  | **static** | 100% | – | – | 0.0 | 3 | Session ID |
| `ShiftGrindRPM` | float | 1 | RPM | **static** | 100% | – | – | 0.0 | 0.0 | RPM of shifter grinding noise |
| `ShiftIndicatorPct` | float | 1 | % | **live(replay)** | 100% | – | – | 4.158 | 0.0 | DEPRECATED use DriverCarSLBlinkRPM instead |
| `ShiftPowerPct` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Friction torque applied to gears when shifting or grinding |
| `Shifter` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | Log inputs from the players shifter control |
| `Skies` | int | 1 |  | **static** | 100% | – | – | 0.0 | 1 | Skies (0=clear/1=p cloudy/2=m cloudy/3=overcast) |
| `SolarAltitude` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.999 | 0.4454562664031982 | Sun angle above horizon in radians |
| `SolarAzimuth` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.999 | 1.5336582660675049 | Sun angle clockwise from north in radians |
| `Speed` | float | 1 | m/s | **live(replay)** | 100% | – | – | 9.999 | 23.303796768188477 | GPS vehicle speed |
| `SteeringFFBEnabled` | bool | 1 |  | **static** | 100% | – | – | 0.0 | True | Force feedback is enabled |
| `SteeringWheelAngle` | float | 1 | rad | **live(replay)** | 100% | – | – | 8.324 | -0.730620026588439 | Steering wheel angle |
| `SteeringWheelAngleMax` | float | 1 | rad | **static** | 100% | – | – | 0.0 | 13.864885330200195 | Steering wheel max angle |
| `SteeringWheelLimiter` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback limiter strength limits impacts and oscillation |
| `SteeringWheelMaxForceNm` | float | 1 | N*m | **static** | 100% | – | – | 0.0 | 28.33333396911621 | Value of strength or max force slider in Nm for FFB |
| `SteeringWheelPctDamper` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback % max damping |
| `SteeringWheelPctIntensity` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback % max intensity |
| `SteeringWheelPctSmoothing` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback % max smoothing |
| `SteeringWheelPctTorque` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback % max torque on steering shaft unsigned |
| `SteeringWheelPctTorqueSign` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback % max torque on steering shaft signed |
| `SteeringWheelPctTorqueSignStops` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Force feedback % max torque on steering shaft signed stops |
| `SteeringWheelPeakForceNm` | float | 1 | N*m | **static** | 100% | – | – | 0.0 | -1.0 | Peak torque mapping to direct input units for FFB |
| `SteeringWheelTorque` | float | 1 | N*m | **static** | 100% | – | – | 0.0 | 0.0 | Output torque on steering shaft |
| `SteeringWheelTorque_ST` | float | 6 | N*m | **static** | 100% | – | – | 0.0 | [6 values] | Output torque on steering shaft at 360 Hz |
| `SteeringWheelUseLinear` | bool | 1 |  | **static** | 100% | – | – | 0.0 | True | True if steering wheel force is using linear mode |
| `Throttle` | float | 1 | % | **live(replay)** | 100% | – | – | 2.65 | 0.4000000357627868 | 0=off throttle to 1=full throttle |
| `ThrottleRaw` | float | 1 | % | **static** | 100% | – | – | 0.0 | 0.0 | Raw throttle input 0=off throttle to 1=full throttle |
| `TireLF_RumblePitch` | float | 1 | Hz | **slow** | 100% | – | – | 0.083 | 0.0 | Players LF Tire Sound rumblestrip pitch |
| `TireLR_RumblePitch` | float | 1 | Hz | **slow** | 100% | – | – | 0.092 | 0.0 | Players LR Tire Sound rumblestrip pitch |
| `TireRF_RumblePitch` | float | 1 | Hz | **slow** | 100% | – | – | 0.058 | 0.0 | Players RF Tire Sound rumblestrip pitch |
| `TireRR_RumblePitch` | float | 1 | Hz | **slow** | 100% | – | – | 0.067 | 0.0 | Players RR Tire Sound rumblestrip pitch |
| `TireSetsAvailable` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many tire sets are remaining  255 is unlimited |
| `TireSetsUsed` | int | 1 |  | **static** | 100% | – | – | 0.0 | 0 | How many tire sets used so far |
| `TrackTemp` | float | 1 | C | **static** | 100% | – | – | 0.0 | 22.77777099609375 | Deprecated  set to TrackTempCrew |
| `TrackTempCrew` | float | 1 | C | **static** | 100% | – | – | 0.0 | 22.77777099609375 | Temperature of track measured by crew around track |
| `TrackWetness` | int | 1 | irsdk_TrackWetness | **static** | 100% | – | – | 0.0 | 1 | How wet is the average track surface |
| `VelocityX` | float | 1 | m/s | **live(replay)** | 100% | – | – | 9.999 | 23.30105972290039 | X velocity |
| `VelocityX_ST` | float | 6 | m/s at 360 Hz | **live(replay)** | 100% | – | – | 9.999 | [6 values] | X velocity |
| `VelocityY` | float | 1 | m/s | **live(replay)** | 100% | – | – | 9.999 | 0.3571529388427734 | Y velocity |
| `VelocityY_ST` | float | 6 | m/s at 360 Hz | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Y velocity |
| `VelocityZ` | float | 1 | m/s | **live(replay)** | 100% | – | – | 9.999 | 0.1156195104122161 | Z velocity |
| `VelocityZ_ST` | float | 6 | m/s at 360 Hz | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Z velocity |
| `VertAccel` | float | 1 | m/s^2 | **live(replay)** | 100% | – | – | 9.999 | 13.0404691696167 | Vertical acceleration (including gravity) |
| `VertAccel_ST` | float | 6 | m/s^2 | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Vertical acceleration (including gravity) at 360 Hz |
| `VidCapActive` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | True if video currently being captured |
| `VidCapEnabled` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | True if video capture system is enabled |
| `Voltage` | float | 1 | V | **static** | 100% | – | – | 0.0 | 14.300000190734863 | Engine voltage |
| `WaterLevel` | float | 1 | l | **static** | 100% | – | – | 0.0 | 7.400000095367432 | Engine coolant level |
| `WaterTemp` | float | 1 | C | **static** | 100% | – | – | 0.0 | 77.0 | Engine coolant temp |
| `WeatherDeclaredWet` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | The steward says rain tires can be used |
| `WindDir` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.999 | 5.16633415222168 | Wind direction at start/finish line |
| `WindVel` | float | 1 | m/s | **live(replay)** | 100% | – | – | 9.999 | 5.337760925292969 | Wind velocity at start/finish line |
| `Yaw` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.99 | 2.042137622833252 | Yaw orientation |
| `YawNorth` | float | 1 | rad | **live(replay)** | 100% | – | – | 9.99 | 1.7320749759674072 | Yaw orientation relative to north |
| `YawRate` | float | 1 | rad/s | **live(replay)** | 100% | – | – | 9.999 | -0.458700090646743 | Yaw rate |
| `YawRate_ST` | float | 6 | rad/s | **live(replay)** | 100% | – | – | 9.999 | [6 values] | Yaw rate at 360 Hz |
| `dcABS` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | In car abs adjustment |
| `dcBrakeBias` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | In car brake bias adjustment |
| `dcHeadlightFlash` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | In car headlight flash control active |
| `dcPitSpeedLimiterToggle` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | Track if pit speed limiter system is enabled |
| `dcStarter` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | In car trigger car starter |
| `dcToggleWindshieldWipers` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | In car turn wipers on or off |
| `dcTractionControl` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | In car traction control adjustment |
| `dcTriggerWindshieldWipers` | bool | 1 |  | **static** | 100% | – | – | 0.0 | False | In car momentarily turn on wipers |
| `dpFastRepair` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop fast repair set |
| `dpFuelAddKg` | float | 1 | kg | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop fuel add amount |
| `dpFuelAutoFillActive` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop auto fill fuel next stop flag |
| `dpFuelAutoFillEnabled` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop auto fill fuel system enabled |
| `dpFuelFill` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop fuel fill flag |
| `dpLFTireChange` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop lf tire change request |
| `dpLFTireColdPress` | float | 1 | Pa | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop lf tire cold pressure adjustment |
| `dpLRTireChange` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop lr tire change request |
| `dpLRTireColdPress` | float | 1 | Pa | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop lr tire cold pressure adjustment |
| `dpRFTireChange` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop rf tire change request |
| `dpRFTireColdPress` | float | 1 | Pa | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop rf cold tire pressure adjustment |
| `dpRRTireChange` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop rr tire change request |
| `dpRRTireColdPress` | float | 1 | Pa | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop rr cold tire pressure adjustment |
| `dpWindshieldTearoff` | float | 1 |  | **static** | 100% | – | – | 0.0 | 0.0 | Pitstop windshield tearoff |
