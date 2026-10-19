# Pitwall — notes for Claude Code

Read README.md, docs/ARCHITECTURE.md, docs/PROTOCOL.md first.

Hard rules
- Any number shown to a user is either an iRacing variable copied through
  `pitwall/normalize.py` or lives under a `derived` key. No exceptions, no
  "helpful" estimates in the client.
- `pitwall/commands.py` is the only module that may call pyirsdk broadcast
  functions. Keep the allowlist there.
- Tests in `tests/test_pipeline.py` must pass: `python -m pytest tests -q`.
  Add a test for every new derived value.
- Do not add a build step to `client/`. One HTML file.
- Tab vs spaces: this repo is 4-space Python and 2-space HTML/JS (not the Godot projects' tabs).

Verification status
- `LiveSource` HAS now been run against iRacing (Daytona and VIR, driving,
  spectating and replays). Findings and corrections live in
  docs/VARIABLES_TO_VERIFY.md -- read it before trusting any variable.
- Captures can drive the whole stack without the sim:
  `python -m pitwall stream --source replay --path recordings/x.jsonl.gz --loop`.
  Use it. Several bugs were only visible against a real field.
- `commands.py` (phase 2 pit service) is still UNTESTED against the sim. So are its camera
  and replay commands (broadcast); see VARIABLES_TO_VERIFY.md, "Broadcast".

Spectator first: the booth is mostly used spectating. Relative values follow the
REFERENCE car (`cars.derived.ref`: your car when driving, else the camera car), never
PlayerCarIdx directly -- while spectating that is slot 63, not a car. Only fuel, tyre
sets, in-car settings and CarLeftRight are your-car-only.
- A PitWall viewer's choices change only their own screen. Following another car is per
  viewer: the bridge computes a `RefView` per followed car (normalize.relative_to, same
  methods, own history) and the relay sends each car's `refview` only to its viewers.
  Nothing a view or guest token sends may change what another viewer sees. The broadcast
  is the exception by design: producer graphics and camera are shared and synced.

A recurring trap, worth stating plainly: FakeSource repeatedly encoded the same
assumption as the code under test, so the suite passed while the sim disagreed.
It has now been corrected five times -- the F2Time placeholder, gap monotonicity,
the mirrored yaw convention, impossible best laps, and single-class-only fields.
When adding a derived value, make the fake reproduce the sim's awkward shape
(placeholders, "carry over", mixed classes), not the convenient one.

iRacing Suite app (suite/)
- The suite hosts PitWall without changing it: `suite/pitwall_host.py` runs
  `relay.server.Relay` and `pitwall.bridge.Bridge` in the app's event loop, bound
  to 127.0.0.1 with random tokens. Keep PitWall usable on its own (run-local.ps1,
  `python -m pitwall stream`); the suite is an extra way to run it.
- The Lap Analyzer is one HTML file: `suite/web/analyzer/index.html`. No build step.
  Its logic is tested in Node: `tests/analyzer_harness.js` runs the page's own script with a
  stand-in page, and `tests/test_analyzer.py` feeds it synthetic .ibt files shaped like real ones
  (lap counter going back, mid-lap joins, pit laps, refuels). Skipped when Node is missing.
- Size: every reply must stay under ~128 KB on the wire (Norton stall, see suite/bridge.py GZIP_MIN).
  The analyzer passed that, so `suite/pagesplit.py` serves it as a small page plus script pieces
  that a loader joins and runs as one script; the file on disk stays one HTML file. Both the suite
  server and the relay (`relay.analyzer_serve`) use it. test_suite checks every reply's size.
- Setup directions are per car: `CAR_KB` in the analyzer (from iRacing's manuals, docs/CAR_MANUALS.md).
  A suggestion is a meaning ("stiffer", "more TC help"); `kbSign` turns it into a number direction for the
  car. Never assume higher = stiffer: on the Corvette it is softer, on Mercedes TC higher is less help.
- `suite/bridge.py` uses only the standard library (plus pyirsdk). Its replay reads
  .ibt files; PitWall's replay reads its own .jsonl.gz captures. They are different.
- `tests/test_suite.py` must pass along with `tests/test_pipeline.py`.
- Share online (`suite/share.py`): a Cloudflare quick tunnel to the suite's relay. Links carry
  only the relay's revocable guest token, never the kept view or producer tokens (the co-host
  producer link is shown separately, folded away). The relay serves the dashboard at /pitwall/.
  It also serves the Lap Analyzer at /analyzer/ with a read-only feed at /analyzer/ws (any viewer
  token): `suite.bridge.relay_feed` puts the suite Hub's messages through a RemoteClient that never
  blocks the pump (half-rate ticks, oldest ticks dropped when slow). The local /ws stays loopback-only.

- Updates (`suite/updater.py`): the release build stamps `suite/_build.py` (BUILD = run number,
  COMMIT); the app compares it with the newest `build-N` release, downloads next to itself,
  verifies size + SHA-256, renames itself aside and restarts with `--after-update PIDS` (waits
  for the old copy so the ports stay the same). The GitHub key goes only to api.github.com
  (unredirected header). The build runs `--selftest-rename` on the real .exe.

Broadcast (broadcast/)
- `overlay.html` (OBS source) and `producer.html` (you + co-host) are single files, no build
  step. Same provenance rule for what is COMPUTED (only in normalize.py, under `derived`),
  but the on-air overlay does not mark derived values: the dotted underline was distracting
  on a stream. The producer panel still marks them for the people running the show.
- Live gaps come from CarIdxEstTime (normalize.live_gaps); CarIdxF2Time only refreshes when
  iRacing's timing catches up with a car, which froze the tower for most of a lap.
- Race length comes from the leader and SessionLaps (normalize.race_length), never from
  SessionLapsRemainEx, which is the player's own count (a spectator has none).
- Other cars' incident counts are hidden from a spectator until the finish (observed), so
  the live incident list is predicted from telemetry (Deriver.incidents); the official counts
  are compared with the prediction when they appear.
- Director (pitwall/director.py) and livery photos (pitwall/liveries.py, pitwall/obs.py) only
  move the camera through CommandGate, under --allow-camera.
- One owner of the camera at a time (director.py): you / director / grid walk / followed battle.
  A producer `camera` command always takes it back (bridge._consumer calls Director.manual()).
  Anything new that moves the camera must go through the Director's owner, not around it.
- `tests/test_broadcast.py` and `tests/test_broadcast2.py` must pass with the others.
- Graphics state ("gfx") lives in the relay, not the bridge. Camera/replay commands go
  through the bridge under `--allow-camera` and the producer token, separate from pit.
- The relay serves both pages at /broadcast/. The suite runs the bridge with camera control
  on and keeps the view/producer tokens in %LOCALAPPDATA%\iRacingSuite\broadcast.json so the
  OBS source address survives restarts.
