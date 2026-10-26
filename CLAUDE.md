# Pitwall — notes for Claude Code

Read README.md, docs/ARCHITECTURE.md, docs/PROTOCOL.md first.

Hard rules
- Any number shown to a user is either an iRacing variable copied through
  `pitwall/normalize.py` or lives under a `derived` key. No exceptions, no
  "helpful" estimates in the client.
- `pitwall/commands.py` is the only module that may call pyirsdk broadcast
  functions. Keep the allowlist there.
- Tests in `tests/test_pipeline.py` must pass: `python -m pytest tests -q`.
  Faster with pytest-xdist: `python -m pytest tests -q -n auto` (the whole suite ~4 min instead of ~12).
  CI: a pull request runs only the tests for the files it changes (scripts/pick_tests.py); every push
  to master runs everything before the release build. Docs-only changes do not run CI.
  Add a test for every new derived value.
- CI runs on Windows with tests in parallel (-n auto); a test hung for 300 s fails with a stack dump
  (pytest-timeout). Failures there so far, and the rule each one taught:
  - A test may share its process with servers other tests left running (SuiteTest's Engine and its
    PitWall bridge). Never patch a module-level function others call without checking the caller
    is yours, and never assume a port is free or stays free: ask the object you started for its port.
  - Windows is slow where Linux is instant: a refused local connection takes a second or more. Nothing
    that may touch the network, a socket, a subprocess or a dialog runs on an event loop: use a thread
    or run_in_executor (suite.bridge.LiveSource looks for the sim on its own thread for this reason).
- Do not add a build step to `client/`. One HTML file. Its only other files are the booth look's
  fonts in `client/assets/` (licences in LICENSES.md), served by name by the relay
  (`relay.server.client_asset`, .woff2 only) and the suite; without them the page falls back to
  system fonts. The booth look (default) and the classic look are the same page: colours are tokens
  on `:root`, booth overrides under `:root[data-skin="booth"]`, and canvases read them via `CV`.
  Screen settings (units, look, alarm sound, the provenance legend) live in one header menu (#screenMenu).
  The header's laps come from `cars.derived.race` (the leader), as on the broadcast; a lap race with no
  time limit shows laps to go on the big display. Race control lists by event time, not arrival.
  The spotter list is ordered by side, not distance: cars ahead always above the followed car, cars
  behind always below, nearest next to it, three places each side (empty places kept).
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
Replays are their own awkward shape (Talladega, VARIABLES_TO_VERIFY.md): no flags at all,
CarIdxEstTime 0, CarClassEstLapTime 1.0, the camera on the pace car. `FakeSource(replay_shape=True)`
reproduces it; anything that reads flags, EstTime or the class lap must still work there.

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
- Words in the Lap Analyzer (Live, Coaching, Setup, Lap Analyzer views) and the home page mean one thing each:
  "your lap" (the left box), "the reference" / "reference lap" (the right box; which lap it is is said once,
  in its box), "your best lap" (fastest clean lap; never "fastest lap" for this), "your average lap"
  (Coaching; never "typical"), "median lap" (a middle lap time), "clean laps" (no pit, incident, off
  track or active reset, a usual time (lapBand/usualLaps), within 7% of the fastest of them), "timed laps" (complete and timed), "Garage 61" (with the space), "Corner 4" in
  sentences and tables, "C4" only on charts and the corner strip. Views: Live, Coaching, Setup, Lap Analyzer.
- The four views each do one job, and must stay that way: Live telemetry (view=live, its own tile on the home
  page) is what the car is doing right now, for an engineer watching: speed, gear, revs, pedals, steering, the
  last 12 s of inputs, g, the wheel's force, warnings (flags, engine, incident limit, pacing), live sectors and a
  predicted lap, this lap against the picked lap (delta, speed, pedals, steering), lap-by-lap input stats, fuel to
  the finish, the cars around (race-order gaps from CarIdxPosition/CarIdxEstTime), the car's systems, brakes and
  shocks per corner, tyres and the next stop, conditions (wind against the car's heading) and recent laps; nothing
  about files. All of it is your car's own telemetry. Coaching is the driving only (gap, 3 things, where
  the time goes, corners, consistency, track limits, progress, conditions, pace rating; one line points to Setup
  when the car costs time); Setup is the car only (how it handles on your laps and what to change, tyres on your
  lap, the live engineer and runs, the setup sheet); the Lap Analyzer is laps already driven (your laps, your lap
  against a reference, every channel, driver laps). The live connection is a one-line bar (#liveBar) on all of them.
  A live lap counts (valid) only when the car covered it (distance) and the lap clock agrees with the time it
  took (a formation lap's clock starts at the green), with no pit, incident, off-track moment or reset.
  Which card shows where is the `body[data-view=...]` CSS at the top of the page. Switching views happens in
  the page (showView / viewShown, history.pushState): no reload, so nothing loaded is lost. The files in the
  two boxes, the lap picked from each and Coaching's Garage 61 laps are kept in IndexedDB (KEEP) and opened
  again when the page loads (after visiting PitWall or the home page); Clear forgets them.
- Calm look (the "Calm" CSS block at the end of the analyzer's <style>): one header row (Suite, views, theme);
  sections are separated by space and a hairline, not boxes; only things you act on get a surface (tiles, your
  3 things). A long grey explanation right after a heading folds behind a small (i) button (hintTidy, plain text
  only). Colour means something is off: "balanced" and good states stay plain or soft, never solid blocks.
  Don't add "click to open" to a summary: the arrow says it.
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
  blocks the pump (half-rate ticks, always the ones carrying every car's arrays; oldest ticks dropped when slow). The local /ws stays loopback-only.

- Your files (`suite/library.py`): the Lap Analyzer's "Your files" menus list the .ibt and Garage61 .csv
  files in iRacing's telemetry folder and folders you add (kept in %LOCALAPPDATA%\iRacingSuite\library.json),
  named by what they are (read from the header and last sample, cached). Only the suite's own server serves
  them, by an id from the last listing, never a path from the page; over one WebSocket (/api/library/stream),
  with 96 KB pieces (/api/library/file) as the fallback. The relay never serves them.
- Home page (`suite/web/index.html`): its /ws asks for no telemetry (`?ticks=0`); Hub.send_all sends to
  every client at once, so one slow client cannot hold the pump. Buttons are built when what they offer
  changes, never on every message (a rebuilt button lost the click), and answer a click at once (disabled,
  "Working…"). The app quits as its window starts closing (events.closing); quit_now gives the share
  tunnel a few seconds side by side and no more.
- Record this session (`suite/recording.py`): the home page's Record button writes the same capture as
  `pitwall record` (10 Hz, every raw variable) into Documents\iRacingSuite\Recordings, on its own thread;
  waits for the sim, stops cleanly (also when the app closes, via _on_quit), and names the file by track
  and session type. `pitwall.recorder.record` takes `stop` and `progress` for this.
- Updates (`suite/updater.py`): the release build stamps `suite/_build.py` (BUILD = run number +
  BUILD_OFFSET 100, since the repo moved to a new public home after build 100; COMMIT); the app compares it with the newest `build-N` release, downloads next to itself,
  verifies size + SHA-256, renames itself aside and restarts with `--after-update PIDS` (waits
  for the old copy so the ports stay the same). The repo is public: no key is needed; an old
  key is still sent if kept, only to api.github.com (unredirected header), and dropped if refused. The build runs `--selftest-rename` on the real .exe.

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
- Master switch (Director.live, the producer panel's "Broadcast off/ON" at the top): off at every start.
  Off, nothing moves the camera (director, the pace-lap grid walk, battle following, livery photos, the
  producer's cuts and replays are refused by the bridge) and the overlay shows nothing. Anything new that
  moves the camera or puts something on air must respect it.
- One owner of the camera at a time (director.py): you / director / grid walk / followed battle.
  A producer `camera` command always takes it back (bridge._consumer calls Director.manual()).
  Anything new that moves the camera must go through the Director's owner, not around it.
- `tests/test_broadcast.py` and `tests/test_broadcast2.py` must pass with the others.
- Graphics state ("gfx") lives in the relay, not the bridge. Camera/replay commands go
  through the bridge under `--allow-camera` and the producer token, separate from pit.
- The relay serves both pages at /broadcast/. The suite runs the bridge with camera control
  on and keeps the view/producer tokens in %LOCALAPPDATA%\iRacingSuite\broadcast.json so the
  OBS source address survives restarts.
