# Pitwall — notes for Claude Code

Read README.md, docs/ARCHITECTURE.md, docs/PROTOCOL.md first. This file is the rules. The story behind each one
(which recording or file showed it, what was measured, what was wrong before) is in docs/NOTES.md: search it for the
names given here before changing an area. A new fix: its story goes in docs/NOTES.md, the rule it taught goes here.

## Hard rules
- Any number shown to a user is either an iRacing variable copied through `pitwall/normalize.py` or lives under a
  `derived` key. No exceptions, no "helpful" estimates in a page. normalize.py is a front door: the code is in
  `pitwall/norm/` by topic (common, session, gaps, race, fuel, state; the Deriver from d_base, d_laps, d_pits,
  d_traffic, d_events, d_incidents, d_finish, d_tv, d_strategy, d_weather). Import from `pitwall.normalize`; add new
  code to the topic's module.
- `pitwall/commands.py` is the only module that may call pyirsdk broadcast functions. Keep the allowlist there.
- Every camera or replay move goes through the CommandGate under --allow-camera, through the Director's one owner of
  the camera (you / director / grid walk / followed battle; a producer `camera` command takes it back), and respects
  the master switch (Director.live, off at every start: off, nothing moves the camera and the overlay shows nothing).
- Add a test for every new derived value. No build step for any page: client/, the analyzer, overlay, producer and
  notes are single HTML files.
- 4-space Python, 2-space HTML/JS.
- Audio is off by default, always. Sound is an announcer only, never sound effects (the sim makes the racing sounds).

## Tests and CI
- `python -m pytest tests -q -n auto` (~5 min). test_pipeline, test_suite, test_broadcast, test_broadcast2,
  test_analyzer (Node; skipped without it), test_scorecard must pass.
- A pull request runs only the tests for the files it changes (scripts/pick_tests.py: add a rule for a new area);
  every push to master runs everything before the release build; docs-only changes run nothing.
- CI is Windows, in parallel, 300 s per test. What it taught:
  - Tests share a process with servers other tests left running: never patch a module-level function without
    checking the caller is yours (the bridge passes its replay view, `b.view`, not the source), and never assume a
    port is free: ask the object you started.
  - Nothing that may touch the network, a socket, a subprocess or a dialog runs on an event loop (thread or
    run_in_executor): a refused local connection takes a second or more on Windows.
  - Text to or from a subprocess gives `encoding="utf-8"` (Windows' default is cp1252).
- The release build uses `--collect-submodules pitwall`; a new package under pitwall/ is picked up.

## Verifying against the sim
- docs/VARIABLES_TO_VERIFY.md says what each variable really does (Daytona, VIR, Talladega, Summit Point, Suzuka,
  Le Mans, Indianapolis). Read it before trusting one. `commands.py` pit service is still untested on the sim.
- Captures drive the whole stack without the sim: `python -m pitwall stream --source replay --path x.jsonl.gz --loop`.
  The user's are in Documents\iRacingSuite\Recordings (four races; auto-recording adds more). Use them: many bugs were
  only visible against a real field.
- FakeSource has encoded the code's own assumption seven times (F2Time placeholder, gap monotonicity, the yaw
  convention, impossible best laps, single-class fields, EstTime straight in LapDistPct). Make the fake reproduce
  the sim's awkward shape, and where a real file carries a second measure of the same thing, test against that.
- Replays have their own shape: no flags, CarIdxEstTime 0, CarClassEstLapTime 1.0, the camera on the pace car
  (`FakeSource(replay_shape=True)`). A saved replay file also says SimMode "replay", runs SessionTime with the tape,
  has SessionTimeRemain 604800 and the session's FINAL ResultsPositions. The bridge reads the source through
  `ReplayCast.view` (pitwall/replaycast.py): while a replay is broadcast the tape is the race (program clock, no final
  results, "back to live" = back to the show, a scrub restarts the history). NOTES: "Broadcasting a replay".
- Check a number against a second measure in the driver's own file before trusting it (his .ibt files are in
  E:\iRacing Telemetry Files; the captures are a spectator's and carry no fuel). NOTES: "Accuracy audit",
  "Methods review", "The open parts".

## Spectator first
- The booth is mostly used spectating. Relative values follow the REFERENCE car (`cars.derived.ref`: your car when
  driving, else the camera car), never PlayerCarIdx (slot 63 while spectating). Only fuel, tyre sets, in-car
  settings and CarLeftRight are your-car-only.
- What a spectator is not sent is filled in everywhere it is used: white / checkered from the race (raceFlags), the
  caution from the field's speed, other cars' incidents predicted from telemetry (official counts hidden until the
  finish), race length from the leader and SessionLaps (never SessionLapsRemainEx). NOTES: "What a spectator".
- Live gaps come from CarIdxEstTime (normalize.live_gaps), never F2Time alone (it freezes for most of a lap). Gaps
  are read in each class's seconds (class_scale) and tyre fall-off against the class's lap (lap_scale).
- A viewer's choices change only their own screen (per-viewer RefView). The broadcast is the exception: producer
  graphics and camera are shared.

## The user's calls (keep them)
- Arcade is an arcade racer, not a fighting game: no VS, rounds or fighter voices. It follows real events only.
- No drawn stand-in for a driver's photo. "Where they're faster" draws colours only, no cars.
- After the checkered flag the racing graphics leave the air and the camera turns cinematic; the podium and the
  finish wait for the whole FIELD, not the winner (race.allFinished).
- While a race is racing the picture is the cars': middle graphics smaller (RACE_SCALE), Arcade call-outs a strip.
  Measure with the coverage harness before making anything bigger.
- The producer's Setup starts Simple; Go live is one press.

## iRacing Suite app (suite/)
- The suite hosts PitWall without changing it (`suite/pitwall_host.py`: Relay + Bridge in the app's loop, 127.0.0.1,
  random tokens). PitWall must stay usable on its own (run-local.ps1, `python -m pitwall stream`).
- Every reply stays under ~128 KB on the wire (Norton stall): the analyzer is served in pieces (`suite/pagesplit.py`),
  the overlay gzip'd. test_suite checks every reply's size.
- `suite/bridge.py`: standard library plus pyirsdk only; its replay reads .ibt files (PitWall's reads .jsonl.gz).
- Your files (`suite/library.py`) are served only by the suite's own server, by an id from the last listing, never a
  path from the page. The relay never serves them. Share links carry only the relay's revocable guest token.
- A new session while the app runs: rebuild on a new attach (LiveSource.epoch), a new SessionUniqueID or the counter;
  a new event (Bridge._event_key) starts the derived history again. NOTES: "A new session while the app runs".
- Home page: buttons are built when what they offer changes, never on every message, and answer a click at once.
  Hub.send_all sends to every client at once. Its /ws asks for no telemetry (`?ticks=0`).
- One lap timer: `library.session_summary` is a line-for-line port of the analyzer's lap parser and sessionStats; a
  change to how laps are timed goes into both (OneLapTimerTest says when they part). "Your last session" must give
  the same numbers as the Lap Analyzer for the same file: a lap's time is the first NEW LapLastLapTime after its own
  last sample (iRacing posts it a moment after the line); clean laps counted as the analyzer counts them.
- Recording: the Record button and Record automatically (`suite/recording.py`; Races by default) write captures on
  their own thread; only files the auto-recorder made are ever deleted, never the newest.
- Ready to go live? (`suite/health.py`) only looks; every line that is off says what to do.
- Asking iRacing for things beyond telemetry (car and helmet pictures, camera, replay, pit) is spaced out, never while
  you drive, and logged to sim_log.txt (pitwall.simlog); logging never raises.
- Updates (`suite/updater.py`): BUILD = run number + BUILD_OFFSET 100; verify size + SHA-256; the repo is public.

## Lap Analyzer (suite/web/analyzer/index.html)
- Tested in Node (`tests/analyzer_harness.js` runs the page's own script). Synthetic .ibt files in test_analyzer are
  shaped like real ones (lap counter going back, mid-lap joins, pit laps, refuels, late lap times).
- Words mean one thing each: "your lap" (left box), "the reference" / "reference lap" (right box), "your best lap"
  (fastest clean lap; never "fastest lap"), "your average lap" (never "typical"), "median lap", "clean laps" (no pit,
  incident, off track or reset, a usual time (lapBand), within 7% of the fastest of them), "timed laps", "Garage 61"
  (with the space), "Corner 4" in sentences, "C4" only on charts.
- Four views, one job each: Live (the car right now, your own telemetry, nothing about files), Coaching (the driving
  only), Setup (the car only), Lap Analyzer (laps already driven). Switching views never reloads the page.
- Coaching measures each lap on its own against the reference and takes the median; a corner within CO_LEVEL_S is
  level (no tips). A distance in advice comes from a fine reading between the grid's points (crossPt / rawCross /
  rawMin), never a grid index. A track's corners are the same in every session (the corner book).
- Advice depends on the kind of car (CAR_STYLE, coWeight, SETUP_SHIFT); setup directions are per car (`CAR_KB`,
  `kbSign`): never assume higher = stiffer. Ride heights are suggested last. A wingless car is never offered a wing.
  Balance is read against what fast laps do in each phase, not against the run's own middle.
- Only redraw a view while it is on screen (renderOwn, renderCoach); Coaching's result is memoised (coachKey).
- A panel that is redrawn as data comes in is never rebuilt while one of its menus is open (liveRender waits on
  liveMenuOpen); a menu lets go of focus once picked. Setup's "Test one change": the driver picks the laps each run.
- Calm look: space and hairlines, not boxes; colour means something is off; long hints fold behind (i).

## PitWall dashboard (client/)
- One HTML file; fonts in client/assets (licences in LICENSES.md). Booth and classic are the same page: colour tokens
  on `:root`, booth under `:root[data-skin="booth"]`, canvases read them via `CV`.
- Screen settings and layout presets live in the header menu (#screenMenu); presets are CSS only, per viewer.
- The spotter list is ordered by side (ahead above, behind below, three each side, empty places kept).

## Looks and style (every page)
- suite:look = basic | pitwall | arcade: one setting for the home page, the analyzer and PitWall. Pit wall and Arcade
  only restyle (CSS under `:root[data-look=...]`) and add a bar whose numbers the page already shows.
- Text at least 4.5:1 against what it sits on (3:1 big or bold); light-theme colours darker than dark-theme ones;
  one button style per look; Pit wall and Arcade square, Basic rounded; ink on a coloured fill by contrast.
- Nothing is written again when it is the same (setT / setH / setText); markup is the same for the same thing (no
  counters in ids); a class the script makes for its own use is not a styled class already.

## Broadcast (broadcast/)
- overlay.html (OBS), producer.html (you + co-host), notes.html (commentators): single files. The overlay does not
  mark derived values; the producer does. Booth notes word the feed's numbers and work none out.
- Graphics state ("gfx") lives in the relay, capped at 64 KB, never sent to share-link guests.
- Overlay: every graphic is drawn on its own in RENDERS (safely()): a new graphic goes into RENDERS, gets a producer
  tile (TILE_GROUPS, SHORT, GEAR), a place in ROOM_ORDER (graphics on air never overlap: makeRoom) and, if it sits in
  the middle of the picture, a RACE_SCALE entry. Words are never cut off (fitWords): names go through nameHtml
  (span.pnm), boxes through setH. Entrances use clip-path, opacity and filter only.
- Producer: no page scroll from 1180 px; four columns. A new setting goes in the Setup drawer (data-adv unless most
  shows need it), never the columns. Studio mode stages changes into studio.pv (removal = "__pw_del__").
- Stingers cover the whole picture from 42% to 58% of their length (the cut). A new built-in: one STINGERS entry and
  its CSS.
- Arcade on air: call-outs only for the feed's own events, never with the broadcast off, in Preview, Arrange or
  Still. The last number shown in a counting-up time is always the real one.
- Positions for a camera at one place come from the producer (Your cameras, kept per track); iRacing gives none.
- The checkered flag waits for the leader's crossing (Deriver.race_end), not iRacing's bit or SessionState; each car's
  gap is held as it takes the flag. The leader's laps never go back in a session.
- Grid walk: one angle (Rear Chase from in front), a row a cut, fits the pace laps (ending early is the safe side);
  the grid board first when the walk will not fit. Replay the start through the harness before changing it.
- Championship points are the league's own (from a link, file or paste), never worked out here.

## The director (pitwall/director.py)
- It tells stories (incidents at once; lead changes, passes, battles scored by gap and closing, class leaders, attacks,
  traffic) with shots in an order, never at random. A close battle keeps the air to its end; two good fights take
  turns; a missed lead change or podium pass is owed a replay; a fight on the last lap for a class win scores most.
- Replays: never on the opening lap or the last two laps, only from the session on air, one moment once; the stinger
  is the overlay's. Rear Chase only for what is behind a car; Far Chase is the main shot; a new shot is never the one
  on air.
- Before and after every change to the director (or normalize's battles, passes, attacks), run
  `python scripts/director_scorecard.py` (three seeds a recording, against docs/director_baseline.json; exit 1 when
  worse) and save the new range with --save in the same pull request. Judge it on the top 3: one camera cannot be
  live on 300 passes. `scripts/director_report.py <capture>` lists every pass.
- The camera and replay check (producer Setup > Check, pitwall/simcheck.py) sends each command once and reads what
  happened; it reads the bridge's copy of the telemetry (_simvars), never the source from its own thread.

## Highlights (highlights/, page suite/web/highlights/)
- One of the suite's apps (a home tile), not a separate program. The suite's Engine makes highlights.app.Engine
  (demo with `--pitwall-source fake`) and its Api is window.pywebview.api.highlights (Api.highlights): public
  attributes there are methods only (pywebview walks them). The page works only in the app window (dialogs, OBS).
  highlights imports pitwall (sources, commands, obs, recorder); pitwall never imports it.
- It never moves the camera while the Broadcast's director is live (Engine._broadcast_has_camera): one camera, one
  owner. Quitting the suite stops a job in hand (OBS stopped, the replay paused).
- It scans a race's replay at 16x (scan.py), finds one car's moments from raw variables only (moments.py: race
  distance from CarIdxLapCompleted + CarIdxLapDistPct unwrapped at the line, CarIdxTrackSurface, CarIdxOnPitRoad,
  CarIdxLastLapTime, CarIdxClass, SessionState), picks them to a target length (pick.py), records only those clips with
  OBS (capture.py) and joins them with ffmpeg (stitch.py). Times are the tape's (session, seconds).
- Every replay and camera move goes through pitwall.commands.CommandGate (camera gate on). The race is never
  recorded whole.
- A spin is a car far slower than its own class right now (a caution or the opening lap is not a spin); a pass must
  stand HOLD_S (side by side is not a pass); a car of another class or a lap apart is traffic. The start and the finish
  are always in the video; the finish comes from the replay's own final ResultsPositions when it has them.
- Music only when tracks are picked (off by default); no sound effects. The stinger's middle (STINGER_CUT) hides the
  cut when it has transparency; without, it plays between clips.
- ffmpeg gets text in files (textfile=) and a font copied into the work folder: no Windows path inside a filter.
  The ffmpeg is imageio-ffmpeg's (requirements); a build without drawtext loses captions, not the video.
- Camera work is the director's (highlights/shots.py): groups read by a pitwall Director (camera_role, the
  producer's "Your cameras" for the track from obs.json, corner spots via _spot_for); every clip is a story of shots
  (lead-up from LEAD, the KEY shot from KEY_BEFORE_S to KEY_AFTER_S over the moment, AFTER), cut during the recording
  (capture CUT_EARLY_S); never the same group twice running, and the shot before the key never takes the key's kind.
  Battles use BATTLE_SHOTS with back/front swapped at a pass. A wreck nearby: the key shot is on the car in trouble,
  then Rear Chase on T looking back. The best moments (angleMoments, scored with the finish at 30) are recorded again
  from anglesEach other cameras (ANGLES) and play after the clip, the last at half speed.
- Wrecks and spins carry LEAD_UP_S of what led up to them; a car in trouble that T comes upon is followed until T is
  clear (a near miss within NEAR_MISS_S). Driver changes come from the target car's UserName changing in DriverInfo
  (stints): chapters on the full video, "Driver change" pit stops, the drivers on the cards.
- The story (highlights/story.py): full video = cold open (best moment, no words) > title card > clips in race order
  with chapters and replays > result card. Words: TEXT_S on screen and gone, small; tall videos keep them below
  SAFE_TOP (Instagram covers the top ~14% and bottom ~35%). Built-in stingers (stingers.py) cover COVER_A..COVER_B
  (test: covered()) and show the car number and team.
- Shorts (pick.best_of / singles / hook_first; stitch vertical=True): 9:16 1080 x 1920, opening HOOK_LEAD_S before the
  best moment, then its other-angle replays (else a half-speed replay of the same recording), the rest in race order;
  no start, no cards; held to shortMaxS (30 s default: completion beats length); no fade at the end (loop).
- A short counts its replays in its length (pick.short_length speeds: SINGLE_SPEEDS for a one-moment short,
  BEST_SPEEDS for the best-of) and cuts its later clips to short_window; the full video's target counts replays, the
  cold open and the cards (Engine.auto searches the budget). The start is START_S (20 s) of racing.
- Every step has an estimate (scan: the replay's own results / 16; record: shots.record_s; build: buildRate, measured
  by each build on this PC) and a live "left" while it runs. The full video uses the user's ticks, the shorts pick from every moment. Every
  clip any output needs is recorded once (the union, merged), and each output is cut from the recording that covers it.
- Not yet run against the sim: docs/VARIABLES_TO_VERIFY.md "Replay highlights".
