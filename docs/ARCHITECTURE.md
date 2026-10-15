# Architecture

## Layers (dependencies point down only)

```
client/index.html          renders protocol messages; never computes racing math
broadcast/overlay.html     OBS overlay; same rule. producer.html drives it through relay gfx
relay/server.py            fan-out + auth + broadcast gfx state; never interprets telemetry
pitwall/bridge.py          schedules, delta-encodes, forwards commands
pitwall/delta.py           keyframes + diffs
pitwall/normalize.py       raw vars -> messages; the ONLY place derived values are computed
pitwall/trackmap.py        outline from a lap (derived; validated by tests)
pitwall/recorder.py        raw capture, no interpretation
pitwall/analyze.py         classifies raw behavior into VARIABLES.md
pitwall/commands.py        the ONLY module that sends anything into iRacing
pitwall/sources.py         live / dump / fake, one interface
```

## Why a relay
iRacing telemetry is a memory-mapped file on the driver's Windows PC. Remote
people cannot read it. The bridge reads it locally and pushes; the relay lets
viewers connect without opening the driver's home network and gives one place
for tokens. Latency is one hop; at 30 Hz state / 15 Hz cars (the default) the stream measured ~75 KB/s
with 24 cars after delta encoding; the suite runs 60/30 on one PC, ~130 KB/s.

## Why "derived" is a wire-level concept
The user requirement is that every displayed feature is accurate. The cleanest
way to enforce that across three codebases is to make provenance part of the
message shape: raw values are plain keys; computed values live under `derived`.
The client cannot accidentally present a computed number as a reported one.

## Threading / async
`bridge.py` runs the source's blocking `tick()` in a thread executor so the
pyirsdk data-valid wait (32 ms) never blocks the websocket. The fake source
sleeps to 60 Hz in the same call.

## Track map
`LapCollector` waits for a start/finish crossing, collects until the next
crossing, and throws the lap away if the car left the track or visited the pits.
`build_from_lap` integrates rotated velocity, force-closes the loop, resamples
to 720 bins by LapDistPct, normalizes to a unit box. Cached per (TrackID, config).
Test result on the synthetic circuit at 60 Hz: closure 4.0 m, mean error 1.5 m,
max 3.2 m over 5.09 km. Expect worse on real data (curbs, slides); the message
carries `closureErrorM` so the client can warn.

## What is NOT here yet
- command UI in the client (phase 2)
- multi-class handling beyond showing class name/position (needs a real multi-class recording to design against)
- sector times per car (derive from CarIdxLapDistPct crossings of SplitTimeInfo sectors — needs validation)
- a Godot client (same protocol; WebSocketPeer)
- an auto-director (camera choices made by rule); battles are derived, cuts are still manual

## Broadcast
Graphics state is a relay concern, not a bridge concern: it belongs to the people producing
the show, survives a bridge restart, and must be shared by two producers and any number of
OBS sources. Camera and replay control go the other way, through the bridge, because only
the bridge can talk to iRacing; they have their own gate (`--allow-camera`) and their own
token (producer), separate from pit service. While spectating, the player-car kinematics
follow the camera car, so the map builder voids a lap whenever `CamCarIdx` changes.
