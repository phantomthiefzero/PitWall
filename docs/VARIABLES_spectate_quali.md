# Variable behavior report

Recording: `recordings/spectate-quali-vir.jsonl.gz`  
Ticks: 6001 over 600.0 s  
Session-info updates seen: 34  
Time by segment (s): {'replay': 457.7, 'track': 102.4, 'pit': 39.9}

Labels: **live** changes while driving; **slow** changes rarely (counters, flags); **pit-only** changes only in pit activity; **static** never changed; **absent** never present; **live(replay)** changes at live rates but was only ever seen in a replay, which proves iRacing records it, not that it is populated in a live session; **replay-blank** present live but not in replay.

| Variable | Type | N | Unit | Label | Present | Δ/s track | Δ/s pit | Δ/s replay | Sample | Description |
|---|---|---|---|---|---|---|---|---|---|---|
| `AirDensity` | float | 1 | kg/m^3 | **live** | 100% | 5.409 | 8.626 | 9.327 | 1.1657299995422363 | Density of air at start/finish line |
| `AirPressure` | float | 1 | Pa | **live** | 100% | 8.446 | 5.517 | 3.491 | 99456.0 | Pressure of air at start/finish line |
| `AirTemp` | float | 1 | C | **live** | 100% | 9.998 | 10.005 | 10.0 | 21.460247039794922 | Temperature of air at start/finish line |
| `Brake` | float | 1 | % | **live** | 100% | 0.849 | 0.025 | 0.0 | 1.0 | 0=brake released to 1=max pedal force |
| `BrakeABSactive` | bool | 1 |  | **slow** | 100% | 0.225 | 0.05 | 0.002 | False | true if abs is currently reducing brake force pressure |
| `BrakeRaw` | float | 1 | % | **live** | 100% | 0.83 | 0.0 | 0.002 | 0.0 | Raw brake input 0=brake released to 1=max pedal force |
| `CamCameraNumber` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.031 | 4 | Active camera number |
| `CamCameraState` | bitfield | 1 | irsdk_CameraState | **pit-only** | 100% | 0.01 | 0.05 | 0.009 | 81 | State of camera system |
| `CamCarIdx` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.011 | 1 | Active camera's focus car index |
| `CamGroupNumber` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.009 | 11 | Active camera group number |
| `CarDistAhead` | float | 1 | m | **live** | 100% | 9.188 | 0.351 | 0.0 | 500000.0 | Distance to first car in front of player in meters |
| `CarDistBehind` | float | 1 | m | **live** | 100% | 9.188 | 0.351 | 0.0 | 500000.0 | Distance to first car behind player in meters |
| `CarIdxBestLapNum` | int | 64 |  | **slow** | 100% | 0.078 | 0.025 | 0.044 | [64 values] | Cars best lap number |
| `CarIdxBestLapTime` | float | 64 | s | **slow** | 100% | 0.078 | 0.025 | 0.044 | [64 values] | Cars best lap time |
| `CarIdxClass` | int | 64 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | [64 values] | Cars class id by car index |
| `CarIdxClassPosition` | int | 64 |  | **slow** | 100% | 0.029 | 0.0 | 0.022 | [64 values] | Cars class position in race by car index |
| `CarIdxEstTime` | float | 64 | s | **live** | 100% | 9.998 | 4.514 | 8.582 | [64 values] | Estimated time to reach current location on track |
| `CarIdxF2Time` | float | 64 | s | **slow** | 100% | 0.029 | 0.0 | 0.024 | [64 values] | Race time behind leader or fastest lap time otherwise |
| `CarIdxFastRepairsUsed` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | How many fast repairs each car has used |
| `CarIdxGear` | int | 64 |  | **live** | 100% | 1.299 | 0.652 | 2.392 | [64 values] | -1=reverse  0=neutral  1..n=current gear by car index |
| `CarIdxLap` | int | 64 |  | **slow** | 100% | 0.107 | 0.502 | 0.229 | [64 values] | Laps started by car index |
| `CarIdxLapCompleted` | int | 64 |  | **slow** | 100% | 0.107 | 0.05 | 0.151 | [64 values] | Laps completed by car index |
| `CarIdxLapDistPct` | float | 64 | % | **live** | 100% | 9.998 | 4.614 | 8.628 | [64 values] | Percentage distance around lap by car index |
| `CarIdxLastLapTime` | float | 64 | s | **slow** | 100% | 0.078 | 0.025 | 0.048 | [64 values] | Cars last lap time |
| `CarIdxOnPitRoad` | bool | 64 |  | **slow** | 100% | 0.049 | 0.476 | 0.103 | [64 values] | On pit road between the cones by car index |
| `CarIdxP2P_Count` | int | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Push2Pass count of usage (or remaining in Race) |
| `CarIdxP2P_Status` | bool | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Push2Pass active or not |
| `CarIdxPaceFlags` | bitfield | 64 | irsdk_PaceFlags | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Pacing status flags for each car |
| `CarIdxPaceLine` | int | 64 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | [64 values] | What line cars are pacing in  or -1 if not pacing |
| `CarIdxPaceRow` | int | 64 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | [64 values] | What row cars are pacing in  or -1 if not pacing |
| `CarIdxPosition` | int | 64 |  | **slow** | 100% | 0.029 | 0.0 | 0.022 | [64 values] | Cars position in race by car index |
| `CarIdxQualTireCompound` | int | 64 |  | **slow** | 100% | 0.02 | 0.0 | 0.015 | [64 values] | Cars Qual tire compound |
| `CarIdxQualTireCompoundLocked` | bool | 64 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | [64 values] | Cars Qual tire compound is locked-in |
| `CarIdxRPM` | float | 64 | revs/min | **live** | 100% | 9.998 | 10.005 | 9.845 | [64 values] | Engine rpm by car index |
| `CarIdxSessionFlags` | bitfield | 64 | irsdk_Flags | **pit-only** | 100% | 0.0 | 0.125 | 0.042 | [64 values] | Session flags for each player |
| `CarIdxSteer` | float | 64 | rad | **live** | 100% | 9.852 | 10.005 | 9.993 | [64 values] | Steering wheel angle by car index |
| `CarIdxTireCompound` | int | 64 |  | **slow** | 100% | 0.059 | 0.426 | 0.118 | [64 values] | Cars current tire compound |
| `CarIdxTrackSurface` | int | 64 | irsdk_TrkLoc | **slow** | 100% | 0.146 | 0.502 | 0.299 | [64 values] | Track surface type by car index |
| `CarIdxTrackSurfaceMaterial` | int | 64 | irsdk_TrkSurf | **live** | 100% | 0.576 | 0.627 | 1.033 | [64 values] | Track surface material type by car index |
| `CarLeftRight` | int | 1 | irsdk_CarLeftRight | **pit-only** | 100% | 0.0 | 0.025 | 0.0 | 0 | Notify if car is to the left or right of driver |
| `ChanAvgLatency` | float | 1 | s | **live** | 100% | 9.989 | 7.172 | 9.274 | 0.0614449568092823 | Communications average latency |
| `ChanClockSkew` | float | 1 | s | **slow** | 100% | 0.146 | 0.176 | 0.981 | 0.0 | Communications server clock skew |
| `ChanLatency` | float | 1 | s | **slow** | 100% | 0.146 | 0.176 | 0.492 | 0.0666666701436042 | Communications latency |
| `ChanPartnerQuality` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 1.0 | Partner communications quality |
| `ChanQuality` | float | 1 | % | **live** | 100% | 0.742 | 0.025 | 5.324 | 0.9975122809410095 | Communications quality |
| `Clutch` | float | 1 | % | **live** | 100% | 0.586 | 0.276 | 0.0 | 0.0 | 0=disengaged to 1=fully engaged |
| `ClutchRaw` | float | 1 | % | **pit-only** | 100% | 0.01 | 0.025 | 0.004 | 0.0 | Raw clutch input 0=disengaged to 1=fully engaged |
| `CpuUsageBG` | float | 1 | % | **live** | 100% | 8.339 | 8.501 | 8.652 | 0.1480002552270889 | Percent of available time bg thread took with a 1 sec avg |
| `CpuUsageFG` | float | 1 | % | **live** | 100% | 9.998 | 9.83 | 9.976 | 0.4579652845859527 | Percent of available time fg thread took with a 1 sec avg |
| `DCDriversSoFar` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Number of team drivers who have run a stint |
| `DCLapStatus` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 2 | Status of driver change lap requirements |
| `DisplayUnits` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Default units for the user interface 0 = english 1 = metric |
| `DriverMarker` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Driver activated flag |
| `Engine0_RPM` | float | 1 | revs/min | **live** | 100% | 9.998 | 9.88 | 1.462 | 0.0043401876464486 | Engine0Engine rpm |
| `EngineWarnings` | bitfield | 1 | irsdk_EngineWarnings | **pit-only** | 100% | 0.01 | 0.226 | 0.004 | 0 | Bitfield for warning lights |
| `EnterExitReset` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.075 | 0.004 | 0 | Indicate action the reset key will take 0 enter 1 exit 2 reset |
| `FastRepairAvailable` | int | 1 |  | **pit-only** | 100% | 0.0 | 0.025 | 0.0 | 0 | How many fast repairs left  255 is unlimited |
| `FastRepairUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many fast repairs used so far |
| `FogLevel` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Fog level at start/finish line |
| `FrameRate` | float | 1 | fps | **live** | 100% | 9.998 | 9.328 | 9.974 | 82.59410095214844 | Average frames per second |
| `FrontTireSetsAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many front tire sets are remaining  255 is unlimited |
| `FrontTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many front tire sets used so far |
| `FuelLevel` | float | 1 | l | **live** | 100% | 9.998 | 9.303 | 0.004 | 0.0 | Liters of fuel remaining |
| `FuelLevelPct` | float | 1 | % | **live** | 100% | 9.998 | 9.303 | 0.004 | 0.0 | Percent fuel remaining |
| `FuelPress` | float | 1 | bar | **slow** | 100% | 0.01 | 0.0 | 1.457 | 0.0 | Engine fuel pressure |
| `FuelUsePerHour` | float | 1 | kg/h | **live** | 100% | 9.989 | 1.454 | 0.002 | 0.0 | Engine fuel used instantaneous |
| `Gear` | int | 1 |  | **slow** | 100% | 0.43 | 0.05 | 0.0 | 0 | -1=reverse  0=neutral  1..n=current gear |
| `GpuUsage` | float | 1 | % | **live** | 100% | 9.998 | 9.83 | 9.98 | 0.6777891516685486 | Percent of available time gpu took with a 1 sec avg |
| `HandbrakeRaw` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Raw handbrake input 0=handbrake released to 1=max force |
| `IsDiskLoggingActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 0=disk based telemetry file not being written  1=being written |
| `IsDiskLoggingEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 0=disk based telemetry turned off  1=turned on |
| `IsGarageVisible` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 1=Garage screen is visible |
| `IsInGarage` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 1=Car in garage physics running |
| `IsOnTrack` | bool | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.004 | False | 1=Car on track physics running with player in car |
| `IsOnTrackCar` | bool | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | False | 1=Car on track physics running |
| `IsReplayPlaying` | bool | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.004 | True | 0=replay not playing  1=replay playing |
| `LFTiresAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many left front tires are remaining  255 is unlimited |
| `LFTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many left front tires used so far |
| `LFbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | LF brake line pressure |
| `LFcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | LF tire cold pressure  as set in the garage |
| `LFodometer` | float | 1 | m | **slow** | 100% | 0.303 | 0.0 | 0.002 | 0.0 | LF distance tire traveled since being placed on car |
| `LFshockDefl` | float | 1 | m | **live** | 100% | 9.998 | 9.955 | 2.1 | 0.0801132917404174 | LF shock deflection |
| `LFshockDefl_ST` | float | 6 | m | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | LF shock deflection at 360 Hz |
| `LFshockVel` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | 0.7464497089385986 | LF shock velocity |
| `LFshockVel_ST` | float | 6 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | LF shock velocity at 360 Hz |
| `LFtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | LF tire left carcass temperature |
| `LFtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | LF tire middle carcass temperature |
| `LFtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | LF tire right carcass temperature |
| `LFwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | LF tire left percent tread remaining |
| `LFwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | LF tire middle percent tread remaining |
| `LFwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | LF tire right percent tread remaining |
| `LRTiresAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many left rear tires are remaining  255 is unlimited |
| `LRTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many left rear tires used so far |
| `LRbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | LR brake line pressure |
| `LRcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | LR tire cold pressure  as set in the garage |
| `LRodometer` | float | 1 | m | **slow** | 100% | 0.303 | 0.0 | 0.002 | 0.0 | LR distance tire traveled since being placed on car |
| `LRshockDefl` | float | 1 | m | **live** | 100% | 9.998 | 9.855 | 2.089 | 0.0596669614315032 | LR shock deflection |
| `LRshockDefl_ST` | float | 6 | m | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | LR shock deflection at 360 Hz |
| `LRshockVel` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | 0.6470640897750854 | LR shock velocity |
| `LRshockVel_ST` | float | 6 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | LR shock velocity at 360 Hz |
| `LRtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | LR tire left carcass temperature |
| `LRtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | LR tire middle carcass temperature |
| `LRtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | LR tire right carcass temperature |
| `LRwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | LR tire left percent tread remaining |
| `LRwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | LR tire middle percent tread remaining |
| `LRwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | LR tire right percent tread remaining |
| `Lap` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.0 | 0 | Laps started count |
| `LapBestLap` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players best lap number |
| `LapBestLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players best lap time |
| `LapBestNLapLap` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Player last lap in best N average lap time |
| `LapBestNLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Player best N average lap time |
| `LapCompleted` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.075 | 0.002 | 0 | Laps completed count |
| `LapCurrentLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Estimate of players current lap time as shown in F3 box |
| `LapDeltaToBestLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for best lap |
| `LapDeltaToBestLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for best lap |
| `LapDeltaToBestLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for best lap is valid |
| `LapDeltaToOptimalLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for optimal lap |
| `LapDeltaToOptimalLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for optimal lap |
| `LapDeltaToOptimalLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for optimal lap is valid |
| `LapDeltaToSessionBestLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for session best lap |
| `LapDeltaToSessionBestLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for session best lap |
| `LapDeltaToSessionBestLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for session best lap is valid |
| `LapDeltaToSessionLastlLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for session last lap |
| `LapDeltaToSessionLastlLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for session last lap |
| `LapDeltaToSessionLastlLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for session last lap is valid |
| `LapDeltaToSessionOptimalLap` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_DD` | float | 1 | s/s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Rate of change of delta time for session optimal lap |
| `LapDeltaToSessionOptimalLap_OK` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Delta time for session optimal lap is valid |
| `LapDist` | float | 1 | m | **live** | 100% | 9.989 | 1.83 | 0.002 | 0.0 | Meters traveled from S/F this lap |
| `LapDistPct` | float | 1 | % | **live** | 100% | 9.989 | 1.83 | 0.002 | 0.0 | Percentage distance around lap |
| `LapLasNLapSeq` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Player num consecutive clean laps completed for N average |
| `LapLastLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Players last lap time |
| `LapLastNLapTime` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Player last N average lap time |
| `LatAccel` | float | 1 | m/s^2 | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | Lateral acceleration (including gravity) |
| `LatAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Lateral acceleration (including gravity) at 360 Hz |
| `LeftTireSetsAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many left tire sets are remaining  255 is unlimited |
| `LeftTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many left tire sets used so far |
| `LoadNumTextures` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | True if the car_num texture will be loaded |
| `LongAccel` | float | 1 | m/s^2 | **live** | 100% | 9.998 | 10.005 | 2.111 | 0.0 | Longitudinal acceleration (including gravity) |
| `LongAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Longitudinal acceleration (including gravity) at 360 Hz |
| `ManifoldPress` | float | 1 | bar | **live** | 100% | 9.969 | 9.579 | 0.815 | 1.0 | Engine manifold pressure |
| `ManualBoost` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Hybrid manual boost state |
| `ManualNoBoost` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Hybrid manual no boost state |
| `MemPageFaultSec` | float | 1 |  | **live** | 100% | 0.928 | 1.329 | 1.147 | 3.0 | Memory page faults per second |
| `MemSoftPageFaultSec` | float | 1 |  | **live** | 100% | 1.933 | 1.981 | 1.934 | 84342.0 | Memory soft page faults per second |
| `OilLevel` | float | 1 | l | **static** | 100% | 0.0 | 0.0 | 0.0 | 5.019999980926514 | Engine oil level |
| `OilPress` | float | 1 | bar | **live** | 100% | 0.889 | 4.514 | 0.039 | 0.0 | Engine oil pressure |
| `OilTemp` | float | 1 | C | **live** | 100% | 9.989 | 9.654 | 0.024 | 77.0 | Engine oil temperature |
| `OkToReloadTextures` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | True if it is ok to reload car textures at this time |
| `OnPitRoad` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.075 | 0.0 | False | Is the player car on pit road between the cones |
| `P2P_Count` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | 0 | Push2Pass count of usage (or remaining in Race) on your car |
| `P2P_Status` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Push2Pass active or not on your car |
| `PaceMode` | int | 1 | irsdk_PaceMode | **slow** | 100% | 0.0 | 0.0 | 0.002 | 4 | Are we pacing or not |
| `PitOptRepairLeft` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Time left for optional repairs if repairs are active |
| `PitRepairLeft` | float | 1 | s | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Time left for mandatory pit repairs if repairs are active |
| `PitSvFlags` | bitfield | 1 | irsdk_PitSvFlags | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Bitfield of pit service checkboxes |
| `PitSvFuel` | float | 1 | l or kWh | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pit service fuel add amount |
| `PitSvLFP` | float | 1 | kPa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pit service left front tire pressure |
| `PitSvLRP` | float | 1 | kPa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pit service left rear tire pressure |
| `PitSvRFP` | float | 1 | kPa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pit service right front tire pressure |
| `PitSvRRP` | float | 1 | kPa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pit service right rear tire pressure |
| `PitSvTireCompound` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Pit service pending tire compound |
| `Pitch` | float | 1 | rad | **live** | 100% | 9.989 | 9.955 | 2.108 | 0.0 | Pitch orientation |
| `PitchRate` | float | 1 | rad/s | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | Pitch rate |
| `PitchRate_ST` | float | 6 | rad/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Pitch rate at 360 Hz |
| `PitsOpen` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | True if pit stop is allowed for the current player |
| `PitstopActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Is the player getting pit stop service |
| `PlayerCarClass` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | 4012 | Player car class id |
| `PlayerCarClassPosition` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | 0 | Players class position in race |
| `PlayerCarDriverIncidentCount` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Teams current drivers incident count for this session |
| `PlayerCarDryTireSetLimit` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players dry tire set limit |
| `PlayerCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 63 | Players carIdx |
| `PlayerCarInPitStall` | bool | 1 |  | **pit-only** | 100% | 0.0 | 0.075 | 0.0 | False | Players car is properly in their pitstall |
| `PlayerCarMyIncidentCount` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players own incident count for this session |
| `PlayerCarPitSvStatus` | int | 1 | irsdk_PitSvStatus | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Players car pit service status bits |
| `PlayerCarPosition` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.004 | 0 | Players position in race |
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
| `PlayerTrackSurface` | int | 1 | irsdk_TrkLoc | **slow** | 100% | 0.068 | 0.075 | 0.002 | -1 | Players car track surface type |
| `PlayerTrackSurfaceMaterial` | int | 1 | irsdk_TrkSurf | **slow** | 100% | 0.234 | 0.1 | 0.002 | -1 | Players car track surface material type |
| `Precipitation` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Precipitation at start/finish line |
| `PushToPass` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Push to pass button state |
| `PushToTalk` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Push to talk button state |
| `RFTiresAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many right front tires are remaining  255 is unlimited |
| `RFTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many right front tires used so far |
| `RFbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | RF brake line pressure |
| `RFcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | RF tire cold pressure  as set in the garage |
| `RFodometer` | float | 1 | m | **slow** | 100% | 0.303 | 0.0 | 0.002 | 0.0 | RF distance tire traveled since being placed on car |
| `RFshockDefl` | float | 1 | m | **live** | 100% | 9.998 | 9.93 | 2.091 | 0.0801477134227752 | RF shock deflection |
| `RFshockDefl_ST` | float | 6 | m | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | RF shock deflection at 360 Hz |
| `RFshockVel` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | 0.7641441822052002 | RF shock velocity |
| `RFshockVel_ST` | float | 6 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | RF shock velocity at 360 Hz |
| `RFtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | RF tire left carcass temperature |
| `RFtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | RF tire middle carcass temperature |
| `RFtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | RF tire right carcass temperature |
| `RFwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | RF tire left percent tread remaining |
| `RFwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | RF tire middle percent tread remaining |
| `RFwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | RF tire right percent tread remaining |
| `RPM` | float | 1 | revs/min | **live** | 100% | 9.989 | 9.529 | 0.004 | 300.0 | Engine rpm |
| `RRTiresAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many right rear tires are remaining  255 is unlimited |
| `RRTiresUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many right rear tires used so far |
| `RRbrakeLinePress` | float | 1 | bar | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | RR brake line pressure |
| `RRcoldPressure` | float | 1 | kPa | **static** | 100% | 0.0 | 0.0 | 0.0 | 172.36900329589844 | RR tire cold pressure  as set in the garage |
| `RRodometer` | float | 1 | m | **slow** | 100% | 0.303 | 0.0 | 0.002 | 0.0 | RR distance tire traveled since being placed on car |
| `RRshockDefl` | float | 1 | m | **live** | 100% | 9.989 | 9.905 | 2.095 | 0.0596861243247985 | RR shock deflection |
| `RRshockDefl_ST` | float | 6 | m | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | RR shock deflection at 360 Hz |
| `RRshockVel` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | 0.6588979363441467 | RR shock velocity |
| `RRshockVel_ST` | float | 6 | m/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | RR shock velocity at 360 Hz |
| `RRtempCL` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | RR tire left carcass temperature |
| `RRtempCM` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | RR tire middle carcass temperature |
| `RRtempCR` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.002 | 46.902862548828125 | RR tire right carcass temperature |
| `RRwearL` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | RR tire left percent tread remaining |
| `RRwearM` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | RR tire middle percent tread remaining |
| `RRwearR` | float | 1 | % | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1.0 | RR tire right percent tread remaining |
| `RaceLaps` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.009 | 0 | Laps completed in race |
| `RadioTransmitCarIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | -1 | The car index of the current person speaking on the radio |
| `RadioTransmitFrequencyIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | The frequency index of the current person speaking on the radio |
| `RadioTransmitRadioIdx` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | The radio index of the current person speaking on the radio |
| `RearTireSetsAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many rear tire sets are remaining  255 is unlimited |
| `RearTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many rear tire sets used so far |
| `RelativeHumidity` | float | 1 | % | **live** | 100% | 9.998 | 10.005 | 10.0 | 0.905181884765625 | Relative Humidity at start/finish line |
| `ReplayFrameNum` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 10.0 | 3569 | Integer replay frame number (60 per second) |
| `ReplayFrameNumEnd` | int | 1 |  | **live** | 100% | 9.998 | 10.005 | 0.007 | 1 | Integer replay frame number from end of tape |
| `ReplayPlaySlowMotion` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | 0=not slow motion  1=replay is in slow motion |
| `ReplayPlaySpeed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 1 | Replay playback speed |
| `ReplaySessionNum` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.004 | 1 | Replay session number |
| `ReplaySessionTime` | double | 1 | s | **pit-only** | 100% | 0.01 | 0.025 | 10.0 | 24.183332951863605 | Seconds since replay session start |
| `RightTireSetsAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many right tire sets are remaining  255 is unlimited |
| `RightTireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many right tire sets used so far |
| `Roll` | float | 1 | rad | **live** | 100% | 9.989 | 10.005 | 2.111 | 0.0 | Roll orientation |
| `RollRate` | float | 1 | rad/s | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | Roll rate |
| `RollRate_ST` | float | 6 | rad/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Roll rate at 360 Hz |
| `SessionFlags` | bitfield | 1 | irsdk_Flags | **slow** | 100% | 0.0 | 0.0 | 0.004 | 268435456 | Session flags |
| `SessionJokerLapsRemain` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Joker laps remaining to be taken |
| `SessionLapsRemain` | int | 1 |  | **slow** | 100% | 0.039 | 0.0 | 0.024 | 1 | Old laps left till session ends use SessionLapsRemainEx |
| `SessionLapsRemainEx` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.002 | 2 | New improved laps left till session ends |
| `SessionLapsTotal` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.002 | 2 | Total number of laps in session |
| `SessionNum` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.002 | 1 | Session number |
| `SessionOnJokerLap` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Player is currently completing a joker lap |
| `SessionState` | int | 1 | irsdk_SessionState | **slow** | 100% | 0.01 | 0.0 | 0.007 | 4 | Session state |
| `SessionTick` | int | 1 |  | **live** | 100% | 9.998 | 10.005 | 10.0 | 40860 | Current update number |
| `SessionTime` | double | 1 | s | **live** | 100% | 9.998 | 10.005 | 10.0 | 25.183333335071655 | Seconds since session start |
| `SessionTimeOfDay` | float | 1 | s | **live** | 100% | 1.006 | 1.003 | 1.001 | 32125.0 | Time of day in seconds |
| `SessionTimeRemain` | double | 1 | s | **live** | 100% | 9.803 | 10.005 | 9.666 | 454.833333331595 | Seconds left till session ends |
| `SessionTimeTotal` | double | 1 | s | **slow** | 100% | 0.0 | 0.0 | 0.002 | 480.0 | Total number of seconds in session |
| `SessionUniqueID` | int | 1 |  | **slow** | 100% | 0.0 | 0.0 | 0.002 | 2 | Session ID |
| `ShiftGrindRPM` | float | 1 | RPM | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | RPM of shifter grinding noise |
| `ShiftIndicatorPct` | float | 1 | % | **live** | 100% | 4.784 | 0.075 | 0.002 | 0.0 | DEPRECATED use DriverCarSLBlinkRPM instead |
| `ShiftPowerPct` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Friction torque applied to gears when shifting or grinding |
| `Shifter` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | Log inputs from the players shifter control |
| `Skies` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 2 | Skies (0=clear/1=p cloudy/2=m cloudy/3=overcast) |
| `SolarAltitude` | float | 1 | rad | **live** | 100% | 9.998 | 10.005 | 10.0 | 0.4174047708511352 | Sun angle above horizon in radians |
| `SolarAzimuth` | float | 1 | rad | **live** | 100% | 9.998 | 10.005 | 10.0 | 1.7413240671157837 | Sun angle clockwise from north in radians |
| `Speed` | float | 1 | m/s | **live** | 100% | 9.989 | 10.005 | 2.113 | 0.0 | GPS vehicle speed |
| `SteeringFFBEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | Force feedback is enabled |
| `SteeringWheelAngle` | float | 1 | rad | **live** | 100% | 8.905 | 3.26 | 0.004 | 0.0 | Steering wheel angle |
| `SteeringWheelAngleMax` | float | 1 | rad | **static** | 100% | 0.0 | 0.0 | 0.0 | 13.864885330200195 | Steering wheel max angle |
| `SteeringWheelLimiter` | float | 1 | % | **live** | 100% | 9.989 | 0.827 | 0.002 | 0.0 | Force feedback limiter strength limits impacts and oscillation |
| `SteeringWheelMaxForceNm` | float | 1 | N*m | **static** | 100% | 0.0 | 0.0 | 0.0 | 28.33333396911621 | Value of strength or max force slider in Nm for FFB |
| `SteeringWheelPctDamper` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Force feedback % max damping |
| `SteeringWheelPctIntensity` | float | 1 | % | **pit-only** | 100% | 0.01 | 0.025 | 0.004 | 0.0 | Force feedback % max intensity |
| `SteeringWheelPctSmoothing` | float | 1 | % | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Force feedback % max smoothing |
| `SteeringWheelPctTorque` | float | 1 | % | **live** | 100% | 9.998 | 9.98 | 0.004 | 0.0 | Force feedback % max torque on steering shaft unsigned |
| `SteeringWheelPctTorqueSign` | float | 1 | % | **live** | 100% | 9.998 | 9.98 | 0.004 | 0.0 | Force feedback % max torque on steering shaft signed |
| `SteeringWheelPctTorqueSignStops` | float | 1 | % | **live** | 100% | 9.989 | 10.005 | 0.004 | 0.0 | Force feedback % max torque on steering shaft signed stops |
| `SteeringWheelPeakForceNm` | float | 1 | N*m | **static** | 100% | 0.0 | 0.0 | 0.0 | -1.0 | Peak torque mapping to direct input units for FFB |
| `SteeringWheelTorque` | float | 1 | N*m | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0849502757191658 | Output torque on steering shaft |
| `SteeringWheelTorque_ST` | float | 6 | N*m | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Output torque on steering shaft at 360 Hz |
| `SteeringWheelUseLinear` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | True | True if steering wheel force is using linear mode |
| `Throttle` | float | 1 | % | **live** | 100% | 1.963 | 0.577 | 0.0 | 0.0 | 0=off throttle to 1=full throttle |
| `ThrottleRaw` | float | 1 | % | **live** | 100% | 1.67 | 0.476 | 0.0 | 0.0 | Raw throttle input 0=off throttle to 1=full throttle |
| `TireLF_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.391 | 0.0 | 0.0 | 0.0 | Players LF Tire Sound rumblestrip pitch |
| `TireLR_RumblePitch` | float | 1 | Hz | **slow** | 100% | 0.4 | 0.0 | 0.0 | 0.0 | Players LR Tire Sound rumblestrip pitch |
| `TireRF_RumblePitch` | float | 1 | Hz | **live** | 100% | 0.615 | 0.0 | 0.017 | 0.0 | Players RF Tire Sound rumblestrip pitch |
| `TireRR_RumblePitch` | float | 1 | Hz | **live** | 100% | 0.644 | 0.0 | 0.02 | 0.0 | Players RR Tire Sound rumblestrip pitch |
| `TireSetsAvailable` | int | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0 | How many tire sets are remaining  255 is unlimited |
| `TireSetsUsed` | int | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0 | How many tire sets used so far |
| `TrackTemp` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.116 | 22.22222900390625 | Deprecated  set to TrackTempCrew |
| `TrackTempCrew` | float | 1 | C | **slow** | 100% | 0.0 | 0.0 | 0.116 | 22.22222900390625 | Temperature of track measured by crew around track |
| `TrackWetness` | int | 1 | irsdk_TrackWetness | **slow** | 100% | 0.0 | 0.0 | 0.004 | 1 | How wet is the average track surface |
| `VelocityX` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | X velocity |
| `VelocityX_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | X velocity |
| `VelocityY` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | Y velocity |
| `VelocityY_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Y velocity |
| `VelocityZ` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | Z velocity |
| `VelocityZ_ST` | float | 6 | m/s at 360 Hz | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Z velocity |
| `VertAccel` | float | 1 | m/s^2 | **live** | 100% | 9.998 | 10.005 | 2.111 | 0.0 | Vertical acceleration (including gravity) |
| `VertAccel_ST` | float | 6 | m/s^2 | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Vertical acceleration (including gravity) at 360 Hz |
| `VidCapActive` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | True if video currently being captured |
| `VidCapEnabled` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | True if video capture system is enabled |
| `Voltage` | float | 1 | V | **slow** | 100% | 0.0 | 0.0 | 0.002 | 14.300000190734863 | Engine voltage |
| `WaterLevel` | float | 1 | l | **static** | 100% | 0.0 | 0.0 | 0.0 | 7.400000095367432 | Engine coolant level |
| `WaterTemp` | float | 1 | C | **live** | 100% | 9.989 | 9.654 | 0.02 | 77.0 | Engine coolant temp |
| `WeatherDeclaredWet` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | The steward says rain tires can be used |
| `WindDir` | float | 1 | rad | **live** | 100% | 9.989 | 10.005 | 10.0 | 6.187571048736572 | Wind direction at start/finish line |
| `WindVel` | float | 1 | m/s | **live** | 100% | 9.998 | 10.005 | 10.0 | 3.446840286254883 | Wind velocity at start/finish line |
| `Yaw` | float | 1 | rad | **live** | 100% | 9.989 | 8.626 | 1.984 | 0.0 | Yaw orientation |
| `YawNorth` | float | 1 | rad | **live** | 100% | 9.989 | 8.626 | 1.984 | 0.0 | Yaw orientation relative to north |
| `YawRate` | float | 1 | rad/s | **live** | 100% | 9.998 | 10.005 | 2.113 | 0.0 | Yaw rate |
| `YawRate_ST` | float | 6 | rad/s | **live** | 100% | 9.998 | 10.005 | 2.115 | [6 values] | Yaw rate at 360 Hz |
| `dcABS` | float | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | In car abs adjustment |
| `dcBrakeBias` | float | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | In car brake bias adjustment |
| `dcHeadlightFlash` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car headlight flash control active |
| `dcPitSpeedLimiterToggle` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | Track if pit speed limiter system is enabled |
| `dcStarter` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car trigger car starter |
| `dcToggleWindshieldWipers` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car turn wipers on or off |
| `dcTractionControl` | float | 1 |  | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | In car traction control adjustment |
| `dcTriggerWindshieldWipers` | bool | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | False | In car momentarily turn on wipers |
| `dpFastRepair` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop fast repair set |
| `dpFuelAddKg` | float | 1 | kg | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop fuel add amount |
| `dpFuelAutoFillActive` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop auto fill fuel next stop flag |
| `dpFuelAutoFillEnabled` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop auto fill fuel system enabled |
| `dpFuelFill` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop fuel fill flag |
| `dpLFTireChange` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop lf tire change request |
| `dpLFTireColdPress` | float | 1 | Pa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pitstop lf tire cold pressure adjustment |
| `dpLRTireChange` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop lr tire change request |
| `dpLRTireColdPress` | float | 1 | Pa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pitstop lr tire cold pressure adjustment |
| `dpRFTireChange` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop rf tire change request |
| `dpRFTireColdPress` | float | 1 | Pa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pitstop rf cold tire pressure adjustment |
| `dpRRTireChange` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop rr tire change request |
| `dpRRTireColdPress` | float | 1 | Pa | **pit-only** | 100% | 0.01 | 0.025 | 0.002 | 0.0 | Pitstop rr cold tire pressure adjustment |
| `dpWindshieldTearoff` | float | 1 |  | **static** | 100% | 0.0 | 0.0 | 0.0 | 0.0 | Pitstop windshield tearoff |
