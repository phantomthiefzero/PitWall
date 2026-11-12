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
  - Windows' default text encoding is cp1252, not UTF-8: text passed to or read from a subprocess (Node for the
    page tests) gives `encoding="utf-8"` explicitly, or a byte-order mark or a non-Latin name fails only there.
- Do not add a build step to `client/`. One HTML file. Its only other files are the booth look's
  fonts in `client/assets/` (licences in LICENSES.md), served by name by the relay
  (`relay.server.client_asset`, .woff2 only) and the suite; without them the page falls back to
  system fonts. The booth look (default) and the classic look are the same page: colours are tokens
  on `:root`, booth overrides under `:root[data-skin="booth"]`, and canvases read them via `CV`.
  Screen settings (units, look, alarm sound, the provenance legend) live in one header menu (#screenMenu).
  Layout presets (Everything / Engineer / Spotter / Strategist / Crew) are in that menu too: CSS only, per viewer
  (`data-role` on :root, localStorage pitwall.role); a phone with no saved choice starts on Crew (#crewPanel).
  The header's laps come from `cars.derived.race` (the leader), as on the broadcast; a lap race with no
  time limit shows laps to go on the big display. Race control lists by event time, not arrival.
  The spotter list is ordered by side, not distance: cars ahead always above the followed car, cars
  behind always below, nearest next to it, three places each side (empty places kept).
- Looks (suite:look = basic | pitwall | arcade, home Settings > Style): one setting for the home page, the Lap Analyzer
  and PitWall. Pit wall and Arcade only restyle (CSS under `:root[data-look=...]`, the same generated block in the home
  page and the analyzer) and add a bar across the top (lkHud) whose numbers are ones the page already shows; the words
  never change with the look. In PitWall, Pit wall is the booth look and Arcade sits on top of it (Look button: booth,
  arcade, classic). Arcade is an arcade RACER, not a fighting game (the user's call): call-outs that drive in and
  brake (lkPop), five start lights before Coaching's result (lkStart), the clean-lap streak, checkered flags, speed
  lines, kerbs and gravel drawn on screen; no VS, rounds or fighter voices. It follows real events only: a finished live
  lap (lkLapEvent), places gained in a race while racing (lkTickEvents: not in practice or qualifying, where the
  order is by lap time; the first tick of a session only sets the scene), the white and checkered flags. A flag is
  called at most once a minute, green only after a caution or a red (local yellows come and go all race). Sound is an ANNOUNCER only, never sound effects (the
  user's call: the sim makes the racing sounds): lkSay plays client/assets/voice-<f|m>-<line>.mp3 (LK_LINES; Kokoro TTS,
  Apache 2.0, see LICENSES.md), one line at a time, in Arcade only, when suite:announcer is 'f' or 'm' (off by
  default). Effects are drawn on one canvas (lkFx) only while something moves. Fonts: Exo 2 (italic 900
  for the big type) and Russo One in client/assets (OFL).
- Style consistency (checked across every page and look): text at least 4.5:1 against what it sits on (3:1 for big
  or bold), light-theme colours darker than the dark-theme ones for that reason (--you, --good, --bad, --acc); one
  button style per look (Basic: the Clear button's, set on `button`; class rules refine it); buttons and fields in
  the page's font; Pit wall and Arcade square (inline rounded boxes included), Basic rounded; text on a coloured fill
  picks its ink by contrast (PitWall inkOn); on the broadcast, secondary text on a header uses the header's text
  colour, not --muted (which is for rows).
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
It has now been corrected seven times -- the F2Time placeholder, gap monotonicity,
the yaw convention (mirrored, then turned 90 degrees; settled against GPS), impossible best laps,
single-class-only fields, and CarIdxEstTime as a straight line in LapDistPct (Nov 2026: the pit-lane loss). Where a real file carries a second measure of the same thing (GPS
against velocity and heading), test against that rather than the fake.
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
  the time goes, a numbered track map kept beside it all (coachMapSvg; GPS, else traced from yaw rate for live laps), corners, consistency, track limits, progress, conditions, pace rating; one line points to Setup
  when the car costs time); Setup is the car only (how it handles on your laps and what to change, tyres on your
  lap, the live engineer and runs, the setup sheet); the Lap Analyzer is laps already driven (your laps, your lap
  against a reference, every channel, driver laps). The live connection is a one-line bar (#liveBar) on all of them.
  A live lap counts (valid) only when the car covered it (distance) and the lap clock agrees with the time it
  took (a formation lap's clock starts at the green), with no pit, incident, off-track moment or reset.
  Coaching's map replay: a live lap's clock is cleaned when saved (coachCompact) and repaired when read (coFixT);
  a new live lap waits while a replay plays, pauses or is zoomed (coachLater). Anything zoomed (a corner on the map,
  a chart in the zoom box) goes back to normal with a second click.
  Speed: "your laps" (renderOwn) and Coaching (renderCoach) are drawn only while their view is on screen, and once
  after kept files are opened again (KEEP.restoring), never for a hidden view (they took seconds each and froze every
  page load); Coaching's result is kept while its inputs are the same (coachRun memo, coachKey).
  Which card shows where is the `body[data-view=...]` CSS at the top of the page. Switching views happens in
  the page (showView / viewShown, history.pushState): no reload, so nothing loaded is lost. The files in the
  two boxes, the lap picked from each and Coaching's Garage 61 laps are kept in IndexedDB (KEEP) and opened
  again when the page loads (after visiting PitWall or the home page); Clear forgets them.
- Calm look (the "Calm" CSS block at the end of the analyzer's <style>): one header row (Suite, views, theme);
  sections are separated by space and a hairline, not boxes; only things you act on get a surface (tiles, your
  3 things). A long grey explanation right after a heading folds behind a small (i) button (hintTidy, plain text
  only). Colour means something is off: "balanced" and good states stay plain or soft, never solid blocks.
  Don't add "click to open" to a summary: the arrow says it.
- One-lap files (laps cut out of a session and shared for coaching, e.g. AlxanderSpetz_..._2.01.148_...ibt): no
  LapLastLapTime at all, one sample on the lap before and one on the next. The lap is timed by the clock from line
  to line with the crossings placed between samples (library._clock_lap and the analyzer's lap parser, the same
  way: 2:01.148 and 1:49.898 as named); under a second of a lap at either end of a file is not a lap. Two laps are
  compared only on the same track and layout (sameWhere: car path or name, track by name, unknown counts as the
  same); a different car is shown with a warning in the Lap Analyzer and never used as Coaching's reference.
  library._driver_block skips results lists ("ResultsFastestLap: - CarIdx: 0" had blanked the car's name).
- Driving style per kind of car (CAR_STYLE, carStyle(carPath, name): gt3, gt4, cup, supercar, proto, formula,
  formula_light, momentum, fwd; every CAR_KB car has one but the Cross Car). Coaching's advice stays relative to the
  reference lap; coWeight scales each piece of it in coachCause: entry advice (braking, turn-in, apex) by the
  style's entry, exit advice (throttle, wheelspin, exit width, early apex) by its exit and by exitWorth of the straight
  after (coFullAfter: how far the reference stays flat out after the corner, 400 m = 1, from 0.7 to 1.5). A GT3 is a V
  (exits first), a prototype a U (entry and minimum speed first), a momentum car keeps speed above all: measured on
  a Porsche 911 GT3 R lap at Road America and a Dallara P217 lap at Suzuka (one each, a guide). Coaching shows "How a
  GT3 is driven" and, per corner, "Exit first" before a long straight or "Only N m flat out" before a close corner.
- By kind of car, beyond Coaching (Nov 2026): Setup's fixes for a symptom (SYMPTOMS ch, adviceChanges) go through
  styleChanges: the usual order nudged by SETUP_SHIFT (GT cars: aids earlier; prototype and formula: aero first where
  fast, a front wing fix added, aero later in slow corners; momentum: bars and alignment first, the wing last; FWD:
  the rear axle first), ride height still last; styleDrive adds the kind's driving note (no TC: the right foot is the
  traction control). Open-wheel cars without manual notes are in CAR_KB by name with aids:0 / wings:0 (Vee, FF1600,
  Skip Barber, USF 2000, Pro 2000, FR 2.0, F3, SF Lights, Indy NXT, DW12, W13, MP4-30, FW31, Lotus 49/79); a
  wingless car is never offered a wing (adviceRow), and a wing is found however the sheet names it (wingRow).
  Coaching's inconsistency thresholds scale with the lap (consistTol: 90 s = 1, 0.7 to 1.5; momentum 0.85); live
  practice shows first what the kind is about (CAR_STYLE watch: throttle, speed or brake; pracWatch).
  PitWall and the broadcast read gaps in each class's seconds (normalize class_scale: REF_SPEED_MS 50 m/s over the
  class's average speed, 0.6 to 1.6; battles, closing in, the director's hot / move / hold, PitWall's 'close
  behind' via derived.gapScale) and tyre fall-off against the class's lap (lap_scale, 90 s = 1).
- Setup directions are per car: `CAR_KB` in the analyzer (from iRacing's manuals, docs/CAR_MANUALS.md).
  A suggestion is a meaning ("stiffer", "more TC help"); `kbSign` turns it into a number direction for the
  car. Never assume higher = stiffer: on the Corvette it is softer, on Mercedes TC higher is less help.
  Ride heights are suggested last (`rhLast`, and last in every symptom's `ch` list): most setups already run the
  car as low as it is allowed to go, so springs, bump rubbers, bars and wing come first.
- `suite/bridge.py` uses only the standard library (plus pyirsdk). Its replay reads
  .ibt files; PitWall's replay reads its own .jsonl.gz captures. They are different.
- `tests/test_suite.py` must pass along with `tests/test_pipeline.py`.
- Share online (`suite/share.py`): a Cloudflare quick tunnel to the suite's relay. Links carry
  only the relay's revocable guest token, never the kept view or producer tokens (the co-host
  producer link is shown separately, folded away). The relay serves the dashboard at /pitwall/.
  It also serves the Lap Analyzer at /analyzer/ with a read-only feed at /analyzer/ws (any viewer
  token): `suite.bridge.relay_feed` puts the suite Hub's messages through a RemoteClient that never
  blocks the pump (half-rate ticks, always the ones carrying every car's arrays; oldest ticks dropped when slow). The local /ws stays loopback-only.

- A new session while the app runs (Nov 2026: a second race at another track and the app stayed on the first):
  iRacing opens a new sim for each session and its session info counter starts again, so the bridge rebuilds the
  session on a new attach (LiveSource.epoch) or a new SessionUniqueID as well as on the counter; another event
  (WeekendInfo SessionID / SubSessionID / track) starts the derived history afresh (Bridge._event_key,
  Director.new_event). The overlay drops a map not of the session's track (mapFits, as PitWall's
  mapMatchesSession). The suite's LiveSource resends the session info on a new SessionUniqueID; its Hub looks for
  track outlines again on a new track and when a session ends (track_maps_soon, one run at a time); the home
  page's last session looks at the folders again each time and reloads after the sim closes.
- Track maps: the suite builds PitWall's outline for each track from your .ibt files by GPS (`Library.build_maps`, on
  its own thread at start) into PitWall's cache (`pitwall.trackmap`, north up, `FRAME`); the bridge looks again every
  10 s while it has none. PitWall's own builder needs your car on track; spectating and replays have no kinematics.
- Your files (`suite/library.py`): the Lap Analyzer's "Your files" menus list the .ibt and Garage61 .csv
  files in iRacing's telemetry folder and folders you add (kept in %LOCALAPPDATA%\iRacingSuite\library.json),
  named by what they are (read from the header and last sample, cached). Only the suite's own server serves
  them, by an id from the last listing, never a path from the page; over one WebSocket (/api/library/stream),
  with 96 KB pieces (/api/library/file) as the fallback. The relay never serves them.
- Your last session (home page, `library.session_summary`): a lap's time is the first new LapLastLapTime after
  its OWN last sample (iRacing updates it after the line); a lap is timed as the analyzer times it (from the line, no pit
  lane, its clock agreeing with the time it took: "22 of 29", not of every lap), and clean laps are counted as the Lap Analyzer counts
  them (lap_band = lapBand, then within 7%): the two pages must give the same numbers for the same file.
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
- Where they're faster (normalize `lap_splits`, `faster`, `derived.faster`): best laps timed by position over 24
  stretches, every car against the car ahead in class; PitWall's panel and the overlay's `faster` graphic.
- Multiclass (normalize `class_traffic`, `sector_marks`): the overlay's Traffic warning (a class leader reaching a
  slower class's pack), fastest-lap alerts per class (from feed fastest_lap events), and sector colours per class
  (tower "Sectors" mode, the driver card). Sector purple is the class's best, never the overall best.
- Race moments (normalize `_car_flag_events`, `_pass_events`): car_flag / penalty_served from CarIdxSessionFlags (the
  overlay's race control alert; the meatball stays the damage incident's), passes for position in class. The
  producer's "Replay ready" list shows every moment with sessionNum + replayT in a few words, each with Play
  (jumpTo, through cmd(): refused with the broadcast off). Head to head (gfx h2h.cars, else the battle on air) and
  the onboard graphic (the camera car's CarIdxGear / CarIdxRPM / CarIdxSteer, derived.speedMs) show only values
  the page already has; the gap only between class neighbours.
- The show (producer's Studio tab): show screens (gfx scene: starting soon with the entry by class, intermission with
  the order, closing with each class's top three; full screen, every other graphic hidden while one is up), the podium,
  commentator straps (timed in the overlay from when the strap arrived, never by comparing two machines' clocks) and
  sponsor logos (the producer's uploaded files). The countdown is to a time the producer set, not a race figure.
- Qualifying, F1 and oval extras: `Deriver.quali_laps` (derived.qualiLap: a push lap's time to the last stretch
  boundary against the class pole lap's, both from lap_splits, and where it would put the car; classPoleGapS), the
  tower's "To pole" (gap mode outside a race), a cut line (gfx tower.cut), columns taking turns (gfx tower.rotate),
  tyre runs (derived.tyreRuns: compound changes only, so two stints on one compound are one run), the speed trap
  (derived.topSpeedBestMs) and the running-order crawl. League standings come from the producer's file or paste
  (parseLeague, tested in Node): the points are the league's own, never worked out here.
- Picture in picture (gfx pip): the producer pastes a video link (VDO.Ninja's view link; https only, or this PC) and
  the overlay plays it in a framed window with a caption (the car picked, or the second camera's car). The second
  camera (`suite/second_camera.py`, the co-host's home page) is the co-host's own suite tied to the host's relay with
  the co-host link: it reports its sim's camera car (pip.second.cam; staleness timed by the receiver, never by the two
  PCs' clocks) and cuts its own camera to the host's picks (pip.want, a new seq each) through its own bridge, only while
  the host's broadcast is on. Starting it turns the co-host's own master switch on and their director and grid walk
  off (their explicit hand-over); stopping or quitting turns the switch off and clears pip.second.
- Monitor and studio mode (producer, top): Program (and Preview) screens are the overlay itself (overlay.html
  &monitor=1: no sound, no stinger; &preview=1 lays gfx studio.pv over what is on air) scaled over this viewer's video
  (OBS Virtual Camera with the game only, a VDO.Ninja link, or black; kept in localStorage, never sent). Studio mode
  (gfx studio.on) stages every change except studio, stinger, replay, photos and scenes into studio.pv; a staged
  removal is the marker "__pw_del__" (the relay's merge drops nulls) and Take sends it as null with studio.pv cleared,
  in one message, after the stinger if chosen, then any held camera cut (studio.cut, through cmd(): the master switch
  and the Director's owner as for any cut). The producer's G() reads the staged view, so its controls show Preview.
  Saved scenes (gfx scenes) hold every layer's show and the graphics' settings, never the look, files, lists or a
  moment's state; recalling one goes through setGfx, so into Preview in studio mode.
- The graphics state is capped at 64 KB by the relay (a patch that would pass it is refused; the producer then
  asks for the real state again) and is never sent to share-link guests. A show screen hides every other graphic on
  every OBS source, not only the one showing the screen. Default positions keep the graphics that are on together
  apart (the speed trap above the card's right end, picture in picture beside the map); the producer's Position
  settings move any of them. Graphics on air together never overlap (overlay makeRoom, every 250 ms): placed in
  ROOM_ORDER by measured size, one that would touch a graphic already placed slides along its edge (ROOM_UP slide up,
  the rest down, then the other way, at most ROOM_SLIDE) or steps off air (.roomoff) while there is no room; one
  placed by hand in Arrange stays put; Arrange mode shows the real positions. The corner track map steps aside while
  "where they're faster" (which draws its own map, colours only: no cars on it, the user's call) is up.
- Camera in the app window: WebView2 asks the app before a page may use a camera, and pywebview never answers
  (refused: the monitor showed "no camera access"). suite.app.allow_local_camera answers it on the window's UI thread,
  camera only and only for the suite's own pages (camera_allowed: http on 127.0.0.1 / localhost; anything else asking
  for a camera or microphone is told no). The handler alone still left it refused, so the window's browser is also
  started with --use-fake-ui-for-media-stream (grant_media_in_window, WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS, set before
  webview.start; it wins over pywebview's own arguments, so its flag is repeated). If it is still refused the producer
  shows why (Api.camera_status: flag, handler, last request, error) and offers Api.open_in_browser (own addresses only).
- Producer layout (the control room): no page scroll from 1180 px wide; four columns, each scrolling on its own:
  the monitor and Cameras (director and replay first), the Field (the Card button shows on hover), On air (Auto
  graphics, then a tile per graphic in groups; ⚙ opens its settings), and Replays / Incidents / Battles / Traffic /
  Log as tabs. Every setting lives in the Setup drawer (Graphics, Look, Studio, Liveries, OBS; Esc closes; data-setup
  opens it at a section). Long notes fold behind (i) (hintTidy). Keep it so: a new setting goes into Setup, not the
  columns, and a new graphic is a tile (TILE_GROUPS, SHORT, GEAR).
- Grid walk (director GRID_SHOTS, overlay gwalk + gladder): the pole and each class's first car from in front (rear
  chase) and held GRID_POLE_HOLD times longer, the rest from in front too (one angle: see "The walk on screen"). On air it is a TV
  grid slot (gwalk, bottom left: place, row, livery, name, quali lap, gaps to pole and class pole, the lap it was set
  on, a pole badge, the class's name and size at its first car) and the grid beside it (gladder, top right: two
  staggered columns, the car lit, the ones walked dimmed). Gaps and rows come from normalize (_grid_derived, each grid
  row's `derived`); the driver card stays down while walking. Arrange's "show every graphic" lays both on the pole.
  A row at a time by default (Director.grid_by_row, gridRows; both cars on screen, the camera on each in turn; a row
  is never split). It fits the pace laps (_sample_pace: the leader's pace and the line ahead; one just ahead at the
  start is the one the field rolls over, so a lap more): shorter holds down to GRID_ROW_MIN_S a row, then only the
  front GRID_FRONT_ROWS rows and each class's pole row (grid_short). iRacing gives no count of pace laps, so ending
  early (back to the director) is the safe side. tests: test_the_grid_walk_goes_by_rows_and_fits_the_pace_laps.
  The grid board (overlay gboard, director gridBoard {row, rows}): every car on one screen in its grid slot with
  iRacing's render, qualifying lap and gap to pole, a light going down the rows from pole while the camera stays wide
  (GRID_WIDE), then the row cards (the bigger gw-pair). It goes first when the camera walk will not fit: always at a
  standing start, and on the pace laps when even the shortest holds would leave cars out (the walk waits up to
  GRID_PLAN_WAIT_S for the pace to decide); then the pole rows first, then the front. Producer Setup: Automatic /
  Always / Never (director gridBoard). By hand, Next/Previous leave the board.
  A standing start has no pace laps (Summit Point: get in car, 10 s warm-up, green): the walk runs during get_in_car
  once 60% of the grid is on it, board first, a row in 5 s; a car not on the grid yet keeps its slot and row card
  ("Not on the grid yet"), the camera on its row mate. Rows count by StartingGrid (normalize.grid_per_row,
  grid.perRow). tests: test_a_standing_start_walks_the_grid_while_the_cars_get_in.
  By hand (Director.grid_next / grid_prev / grid_pause / grid_jump; bridge actions of the same names, only while a
  walk has the camera): the producer's Previous / Hold / Next under the camera owner, ← → and the space bar, and a
  click on a driver on the grid moves the walk to them instead of ending it. "Walk by hand" (grid_walk manual) starts
  on pole held; a held or by-hand walk is never shortened, and the pace laps starting do not replace it.
  The walk on screen, fixed against Summit Point (Nov 2026, the user's "all sorts of weird things"; the capture's
  start played through the director, cut by cut): ONE angle down the grid, from in front (GRID_SHOTS: Rear Chase;
  it took turns front / back every 2.6 s); a row is one cut, the card's light moves to the second car, which gets
  its own cut (same angle) only with GRID_RECUT_S a car, by hand, or for livery photos (each_car); a car not on the
  grid yet never moves the camera (its row mate has the picture, or the shot stays: it went to the helicopter and
  back); when the walk is done the camera goes to the pole from the start / finish camera, else wide (_grid_finish,
  "the start: the grid"), stays through the warm-up and START_HOLD_S of the launch (it sat on the back row at the
  lights); a standing walk still going at the warm-up wraps up in GRID_WARMUP_S; starting the director during a walk
  no longer ends it (configure: resume race); the overlay's paged grid stays off while the director walks by itself
  (status gridAuto: it came back as "Positions 11-12 of 12" before the green). Replay the start before changing it:
  the scratch harness plays the capture with the camera following the director's cuts.
  test: test_the_grid_walk_is_one_steady_walk_and_the_lights_are_watched_from_the_front.
- Camera packs (director camera_role, Director.set_cameras / cameras; Nov 2026, Summit Point: the pack's Turn 1 / 3 /
  5 / 9 / 10 and SF Line were taken for TV cameras, 21 of 95 cuts to a corner's camera with the car anywhere on the
  lap). A group is read by its name, then by what its cameras were built from (iRacing names them: "CamPit Lane 4"
  is a fixed camera, "CamBlimp01" flies), then by how many it has: several follow a car round the lap (trackside),
  one alone stands at one place ("spot"). A spot is never in the trackside pool: it replaces a trackside shot only
  while the car is SPOT_AHEAD_S before it or just past (_spot_for in _do_cut; a car that may be standing still, an
  incident: only one right by it), and the shot ends as the car goes by (tick). So the director must know where it
  stands: the start / finish line by its name (0), the rest from the producer (Setup > Auto-director > Your cameras:
  a kind for any group, "% of the lap" or "It is here" = where the car on camera is now; bridge director action
  camera, kept per track in obs.json cameras, allowed with the broadcast off). Unplaced, a spot is left alone.
  Placing on the track map (producer camMap, drawCamMap / camNearest; the relay's "map" outline, points[i] = i / n of
  the lap, only this session's track): "Place them on the map" takes each unplaced spot in turn, a click on the
  track sets its place; the car on camera is drawn to check against the picture. No outline (a track never driven):
  "It is here". The spots are listed first.
  iRacing gives no camera positions and the turn numbers cannot be read off the lap. A new shot kind goes into KINDS
  and the producer's CAM_KIND. tests: test_the_director_knows_what_a_camera_packs_cameras_are,
  test_the_producer_places_the_cameras_and_the_track_keeps_them.
- Championship (gfx league.rows, the league's own points): from a link (the relay fetches it for the producer token:
  relay.fetch_text, public addresses only, redirects checked, 2 MB, off the loop; a Google Sheet's share link becomes
  its CSV export; "keep it up to date" reloads every 10 min), a file or paste (CSV, TSV, JSON, or a web page: its best
  <table>), or typed and fixed in the table editor. Drivers are found by customer id, else name (accents, "Last,
  First"; iRacing's added digits only when one row fits), else a car number only one row has (leagueRowFor, the same
  in the overlay and the producer, which says who it did not find). Shown on the League graphic and the grid walk.
- Booth notes (broadcast/notes.html, the producer header's "Booth notes", opens on its own for a second screen): for the
  commentators. The driver on camera (or one picked) with what is worth saying (start to now, laps led, best and last
  lap, stint, stops, next stop by their longest stint, consistency, top speed, iRating, tyre runs, their battle),
  storylines (battles, movers, class leaders and best laps, strategy), race news from the feed's events, the field,
  and their own notes: one per driver by iRacing user id and one for the show, kept by the relay on disk
  (Relay.notes, `.booth_notes.json` in the uploads folder; message `notes`, producer token only, never to guests).
  The page words the feed's numbers and works none out (the test checks for arithmetic on race values).
- Show screens move slowly and quietly (drifting glows, a light sweeping by, specks rising, the stripes easing;
  the words and lists come in once per screen change, never on each refresh). Each screen can use your own file
  instead (gfx scene.files[mode]: an uploaded picture or looping muted video, or an https page) with a dark backing
  under our words, or alone (scene.bare[mode]).
- Stingers: built-in styles (overlay STINGERS + CSS `#stinger.run[data-style=...]`: wipe, chevrons, checker,
  panels, iris), each covering the whole picture from 42% to 58% of its length, where the cut falls; or your own
  video (theme.stinger.video, every uploaded video is listed in Look > Stingers, each with its own cut point in
  theme.stinger.cuts). Adding a built-in: one STINGERS entry and its CSS, and keep it covering 42-58%. The stinger's
  own settings are never staged in studio mode. The first graphics state an overlay sees only sets the stinger mark.
- The overlay's frame loop asks for the next frame first and draws every graphic on its own (RENDERS, safely()): one
  graphic throwing on some state of the race once stopped every graphic after it and the loop itself, and OBS showed
  nothing until the source was reloaded. A new graphic goes into RENDERS. Errors go to the console once each.
- Words are never cut off on air (fitWords, every render frame): a text box with text-overflow:ellipsis (found from
  the style sheet, so a new graphic's boxes are covered) that is too narrow first gets smaller, to FIT_MIN 70% (a box's --fit-min may allow less: the grid walk's grid .58); a name
  (.pnm from nameHtml, never .pn, the panels' class; and the card's and grid walk's names) that would need more than NAME_SHRINK loses words from the
  front instead (nameShorter: "C. Andres Arteaga Narvaez" -> "C. Narvaez" -> "Narvaez"), "…" last. Refitted when the
  words change (MutationObserver), a box gets or changes width, the look changes or fonts load. Boxes are written
  through setH (compares with what was last written, never with innerHTML, which a fitted name changes). A new name
  goes through nameHtml; a new box comparing innerHTML would be rewritten every frame.
- Arcade on air (Nov 2026, the user's "flashy, exciting, very animated"; overlay ARCADE FX CSS block and the AX code):
  full-screen call-outs (axCall: one at a time from a queue of 3, chrome letters that drive in, brake, hold and whip
  off, a band behind them) for the feed's own events only: a pass for position in a race (axEvent: "Overtake!",
  "For the lead!"; top 10 of the class by default, gfx arcade.passTop; one per 10 s, the lead after 4 s) and a fastest
  lap (one per class per 15 s), and in renderFlagCall the flag calls (green with five lights going out, final lap,
  winner with confetti and checkered flags, a caution), which then replace the flag banner. #arcx lies over every
  graphic, under the stinger, outside makeRoom; its canvas (axFx) runs only while something moves. Never with the
  broadcast off, in Preview or Arrange, or Still; gfx arcade.callouts (producer, beside the announcer) turns them off.
  The announcer adds "overtake" and "lead". Kerbs under every graphic run at the race's mood (data-ax: green, final,
  caution, done; axMood), graphics pulse yellow under a caution and come on with a checkered-edge wipe (axIn). Tower:
  gold streak for a place gained, red flicker for one lost, the leader's sweep, "On fire" for two passes in 150 s of
  the feed (axHot), the class's fastest lap glowing. Battle: a heat meter in the class's seconds (b.scale), side by side
  under a tenth, a spin when the two swap, "Swapped ×n" from battle_history. Driver card: "+n places" (posGained 3+)
  and the clean-lap streak (normalize cleanStreak, 3+; "Streak over" shatters). A lap time counts up to the feed's value
  (axRoll; the card's on a new car, the fastest-lap alert, the call-out): the last number shown is always the real one.
  Show screens: the field's names race past behind the starting-soon / intermission / closing words, a chrome
  countdown; the podium rises step by step under spotlights (rebuilt only when who stands on it changes: pdSig); the
  grid walk's slot is stamped in. Stinger "Checkered tiles" (any look). tests: test_arcade_calls_the_big_moments_and_keeps_its_rules,
  test_a_clean_streak_counts_racing_laps_without_an_incident. The podium sits clear of the tower (it was always off
  air beside it: makeRoom had no room), and a drawn helmet is the same markup every time (no counter in its clip id:
  setH and the podium compare it).
- What the Arcade and words-fit work taught, checked across the pages (Nov 2026; test_what_the_arcade_work_taught_holds_everywhere):
  a class the script makes for its own use must not be a styled class already (names were .pn, the panels' class);
  nothing is written again when it is the same (overlay setT / setH, PitWall setText: its g-g note and "last update"
  were replaced 60 times a second); markup must be the same for the same thing (a counter in an id rebuilt the podium
  every second and kept restarting its animation); a big graphic must have room beside what is usually on air
  (the standings board, the grid, the league and the lap chart come before the tower in ROOM_ORDER, so the tower steps
  aside: the finish's standings never came on air); a keyframe that holds must say so at the hold (axDrive's blur ran
  through it); a producer list is not rebuilt while it is open (h2hPick, like pipCar).
- Page size: the overlay is past 128 KB uncompressed. Every browser asks for gzip (~40 KB on the wire); keep it so.
- Live gaps come from CarIdxEstTime (normalize.live_gaps); CarIdxF2Time only refreshes when
  iRacing's timing catches up with a car, which froze the tower for most of a lap.
- Race length comes from the leader and SessionLaps (normalize.race_length), never from
  SessionLapsRemainEx, which is the player's own count (a spectator has none).
- Other cars' incident counts are hidden from a spectator until the finish (observed), so
  the live incident list is predicted from telemetry (Deriver.incidents); the official counts
  are compared with the prediction when they appear.
- Asking iRacing for things (Nov 2026: the sim closing to the desktop now and then with the app running, no error,
  no crash report): car pictures one at a time RENDER_GAP_S apart, never while you drive (renderer.paused from
  IsOnTrack), none when the producer switches them off (Liveries: "Ask iRacing for car pictures", livery action
  renders, kept in obs.json), helmet pictures (carrender.HELMETS, on: seen working) only once no car picture waits. Everything
  that reaches into the sim beyond reading telemetry is written to sim_log.txt beside the pictures (pitwall.simlog:
  pictures, camera / replay / pit commands, "sim found" / "sim gone"); never let logging raise.
- Director (pitwall/director.py) and livery photos (pitwall/liveries.py, pitwall/obs.py) only
  move the camera through CommandGate, under --allow-camera.
- Master switch (Director.live, the producer panel's "Broadcast off/ON" at the top): off at every start.
  Off, nothing moves the camera (director, the pace-lap grid walk, battle following, livery photos, the
  producer's cuts and replays are refused by the bridge) and the overlay shows nothing. Anything new that
  moves the camera or puts something on air must respect it.
  The bridge answers a director command with the new status at once, sim or no sim: its frame loop only runs while
  iRacing ticks, so status left for the next frame never arrived with the sim closed (the switch looked stuck).
- The race director tells stories (director._stories / _story_step): incidents cut in at once; otherwise lead
  changes, passes for position (feed events the bridge passes in), battles scored by gap, closing, what they are for
  and size, each class leader (more the longer off air) and top cars in their box. It stays with a story for
  SEGMENT_MIN_S..SEGMENT_MAX_S going round its angles; a story just aired scores lower for FATIGUE_S; nothing is
  picked at random. tests: test_director_tells_stories_and_moves_on.
  Also: a class leader reaching a slower class's pack (classTraffic), a car coming through the field (posGained 5+ and
  passing lately), one closing on the car ahead (catchLaps), a fresh fastest lap; under a caution battles count for
  little. Shots come in an order (SEQUENCES per style, STORY_SEQ per story), not by chance; a battle close and closing
  (MOVE_GAP_S) goes onboard the chaser now and then; side by side (HOLD_CLOSE_S) the shot is held up to HOLD_MAX_S.
  Replays (Director.replays_on, producer Setup > Auto-director): in a quiet stretch (nothing live above REPLAY_CALM) or
  under a caution, the best recent moment (contact, spin, a top-3 pass, a lead change; 8-150 s old, one per REPLAY_GAP_S)
  through the bridge's _replay (CommandGate, its own thread); the stinger in and out is the overlay's, fired from the
  director's status (stinger {seq, dir}) with the camera moving at stinger_cut_s (the producer keeps it in step with
  the look); back to live early for a fresh contact, spin or lead change. Never in the last two laps, never on the
  opening lap (the start is its own story, "start": the front of the field, wide shots, up to 150 s), only from the
  session on air (moments keyed by sessionNum), one moment once (a pass and the lead change it made are one).
  A pass between two cars still in a battle adds to that battle's score instead of making its own story.
  A close battle keeps the air to its end (Summit Point: four left at SEGMENT_MAX_S within 0.1-0.7 s, the pass for
  the lead a second later): "hot" (within BATTLE_HOT_S, or closing within BATTLE_WARM_S) it may stay to
  BATTLE_HOT_MAX_S, tires slowly (BATTLE_HOT_FATIGUE) and another story needs BATTLE_STAY more to take over; no
  replay over it, and a spin elsewhere waits for its replay. An incident to a car IN the battle on air holds the shot
  (no cut to a lone trackside angle). Its cars swapping places (a new id) or the battle dropping out of the list for a
  moment (BATTLE_GRACE_S) is the same story (_battle_continuity). On the opening lap a fight among the front three
  is part of "the start". tests: test_a_close_battle_keeps_the_air_to_its_end.
  Camera packs: names with "scenic" are never used, drone/heli/sky are aerial; Gearbox and Susp cams are "detail".
  Far Chase is the main shot (its own kind "far"; the Chase stands in where a pack has none): first in every
  sequence and twice a round in a battle (on the last car: the whole fight). Rear Chase looks back, so it is only
  for what is behind a car: the chaser in a battle (on the front car), the pack behind the leader at the start, a
  car on the grid from the front; never a car on its own (Summit Point: it was the most used shot, 21 of 101).
  A new shot is never the camera already on air (_fresh). tests: test_shots_come_in_an_order_and_a_close_fight_is_held.
- The director and the action (Nov 2026, measured with `scripts/director_report.py <capture>`: every pass for position
  in a race, live / in the picture / late / missed / replayed; run it before and after a change to the director):
  attacks (normalize `attacks`: the live class gap over the last 6 s against the same car ahead, 0.3-1.5 s and 0.15 s
  gained, class seconds) are a story of their own to class P6 (ATTACK_TOP) or make the battle they are in bigger and
  hot ("urgent": may cut into a young story by ATTACK_INTERRUPT_BY; no replay while one is on; onboard the chaser under
  ATTACK_ONBOARD_S); a lone spin waits for a replay while a fight scoring INCIDENT_FIGHT+ for a better place is on;
  the director keeps what the camera showed by the race's clock (_cam_hist) and a pass it missed (to P10) is replayed
  before one it showed (_seen_live); a faster class's top ten going through slower cars is a story (normalize
  `throughTraffic`; a fight of its class going through them scores more). What the two captures said: Summit Point's
  passes are nearly all on the opening lap (36 of 42; 6 after it, too few to tune on), and at Talladega (a pack) an
  attack seldom comes before a pass (the gaps swing all the time: hence the 0.3 s floor). More captures of road
  races will say more; tests: test_an_attack_is_seen_as_it_happens_and_through_traffic_is_a_story,
  test_the_director_goes_to_an_attack_and_replays_the_passes_it_missed.
- A big field (Suzuka, Nov 2026: 39 cars, GTP and GT3, 46 minutes, a rolling start, a pack with 18 corner cameras
  loaded mid-race: the camera list and its group numbers change in a session). Before: 84% of the race on battles,
  one for 205 s, the GT3 lead changing hands four times in 30 s unseen, two replays all race. Now a close battle
  keeps the air past BATTLE_SHARE_MAX_S only while nothing within SHARE_WITHIN of its score has been off air
  SHARE_REST_S (two good fights take turns), and a lead change or a podium-place pass the camera missed is OWED a
  replay (_replay_candidate owed: no waiting for a quiet stretch, which a big field never has, nor for the battle on
  air to cool unless its cars are right together; REPLAY_OWED_GAP_S): longest stay 99 s, 10 replays, 6 of the 13
  podium-place passes not live replayed (5 of the 18 live, as before). One camera cannot be live on 300 passes:
  judge the director on the top 3.
  Pack names: "Start Cam" is the line's camera, "Drone Turn 1" / "Drone Spoon" stand at one place (_PLACE_NAMES).
  test: test_close_battles_share_the_air_and_a_missed_lead_change_is_owed_a_replay.
- Checking it on the sim (Nov 2026). The camera and replay check (producer Setup > Check; pitwall/simcheck.py, bridge
  `simcheck` command, producer token, the broadcast on; it takes the camera from the director): each command the
  broadcast uses, sent once through the CommandGate, and the telemetry's answer (CamCarIdx / CamGroupNumber,
  ReplayPlaySpeed / SlowMotion, ReplaySessionTime against SessionTime): worked / did not work (what was read) / not
  sent (the error); back to live and the camera back where it was at the end; sim_log.txt and sim_check.json. It reads
  the bridge's copy of the telemetry from its last frame (_simvars), never the source from its own thread.
  How did the director do? (home page; suite/director_check.py over pitwall/director_report.py, which
  scripts/director_report.py also uses): a recording from the Record button's folder by its id in the listing (or the
  app's own file dialog), played through the director on its own thread; after the opening lap and the top 3, live /
  in the picture / replayed, and every pass with the drivers' names. Bridge._emit looks its list up when the callback
  runs (a bound `self._extra.append` went to the list of that moment, swapped out by the frame loop).
  tests: test_the_camera_check_sends_each_command_once_and_reads_what_happened,
  test_the_camera_check_needs_the_broadcast_on_and_takes_the_camera, test_the_home_page_checks_how_the_director_did.
- Auto ("Pick graphics for me") is contextual (overlay ctxWants / CONTEXTUAL): the driver card comes up for a few
  seconds when the camera finds a new car and now and then on a long shot (never over a battle box or a qualifying
  lap, which name the cars), the battle box shows the battle the camera is on, the onboard graphic follows onboard
  cameras, the speed trap comes up now and then outside a race; a pinned card or a battle picked by hand still rules.
  Auto also runs every other tile (autoWants; test_auto_graphics_use_every_graphic_but_picture_in_picture): head to
  head on a top-five battle held 12 s, the track map and crawl under a caution and now and then, the standings now
  and then and 35 s at the finish then the podium, tyres as the camera car leaves the pits, the league before the
  start, sponsors now and then; the timed ones are staggered (every(): this machine's clock). Picture in picture
  stays the producer's (it needs their link). Outside a race the tower's Interval column reads the gap to pole. The director also follows push laps in practice
  and qualifying to the line (stories "push", up to 150 s) and the leader on the last lap ("finish").
- The checkered flag waits for the leader (normalize.race_flags, state derived.raceFlags): iRacing raises the bit when
  a timed race's clock runs out, and SessionState turned checkered 24 s before the leader reached the line (Summit
  Point). Deriver.race_end decides it: a lap race when the leader has the laps, else the leader's first crossing after
  the end came (clock out, or the state); until then it is the white flag (race.finished, raceFlags). After it, each
  car's gap is held as it takes the flag (finish_gaps: F2Time at the line, laps down); one still on its last lap shows
  iRacing's at its last crossing.
  A spectator's SessionFlags carry no white or checkered at all (Summit Point), so race_flags also reads them from the
  race: checkered at the leader's crossing (race_end), white from the end coming (a timed race's clock out, one lap
  to go, the state) until then.
  After the checkered flag in a race (the user's call, Nov 2026) the racing graphics leave the air (overlay FINISH_OFF:
  battle, h2h, closing, pace, strategy, card, race control's alerts and the like; gfx finish.solo false keeps them) and
  the director shows each class's podium in turn on big, slow shots (_after_finish, STORY_SEQ "after": aerial first,
  AFTER_HOLD_S or more each; no incidents, replays or battles). Arcade's final-lap kerbs run at .9 s (.45 s strobed).
  tests: test_after_the_flag_the_racing_graphics_go_and_the_camera_turns_cinematic.
  The podium and everything after the race wait for the FIELD, not the winner (the user's call, Nov 2026; Le Mans,
  two classes: the podium was up 60 s after the overall winner, the GR86s' winner crossed at 122 s, the last car at
  176 s). normalize race.allFinished / finishLeft (Deriver._finish_all): every car still running has taken the flag;
  one gone from the world, in the pit lane or standing still FINISH_STUCK_S does not hold it up, and FINISH_WAIT_LAPS
  of the slowest running car's lap after the winner ends it anyway. Who has crossed is read from every car (iRacing
  gives every car position -1 a little after the winner's flag), and the winner is whoever crosses first after the
  end came (race_end lcs, _end_snap: car 10 passed car 4 on the last lap and crossed first while iRacing still had
  car 4 first; the real winner was never marked finished). Until the field is in, the director shows each class's
  leader coming to win it, the fights still racing to the line and the next car to the flag (_finishing_stories;
  cars that have finished are no story, and normalize's battles and attacks leave them out); then _after_finish and
  the reel. The overlay's Auto: the result while the field comes in and FINISH_BOARD_MS after, the lap chart
  FINISH_CHART_MS, then the podium (CTX.allAt). A feed without allFinished keeps the winner's flag.
  The racing graphics go in two steps: the race-wide ones (FINISH_OFF: closing, pace, strategy, pit stops, penalties
  and the like) at the winner's flag; those that follow cars still racing (FINISH_OFF_ALL: battle, head to head, the
  card, onboard, the map, race control) when the field is in (fieldIn); no card or onboard for a car that has finished.
  test: test_the_podium_waits_for_the_whole_field_to_finish.
  The overlay calls them on screen (flagcall, a tile under Alerts, on by default): green as a race goes racing or
  after a caution or red, white for the final lap, checkered with the winner; only on a change it sees.
  The leader's laps never go back in a session (race_length: ResultsPositions and the most seen are a floor), and
  while a replay holds the picture the bridge keeps the race's clock (SessionTimeRemain) and laps (results) running.
  Every clock on air changes in place (roll() leaves one that ticks, CLOCK_RE: the banner's and then the tower's
  flashed each second); only laps and words roll in.
  What a spectator is not sent is filled in everywhere it is used: raceFlags feeds race control's flag events
  (state_events), the race summary's white / checkered (race_length, state given), PitWall's header, and the
  director (the finish story; no replays in a timed race's last laps, lapsByTime). The caution is read from the
  field's speed whenever no racing flag comes (a spectator's flags carry only start lights after the start), not
  only when there are no bits at all. tests: test_what_a_spectator_is_not_sent_is_filled_in_everywhere.
- Accuracy audit against the driver's own race file (Nov 2026: Indianapolis road course, Ferrari 296 GT3, 45 minutes,
  one stop; the .ibt read independently with irsdk and compared with what the pages say). Agreed: all 29 timed laps'
  times with iRacing's LapLastLapTime and their fuel with the tank's level line to line; Coaching's gap is exactly
  the average lap minus the best lap. Wrong, now fixed: a timed race's laps to go (PitWall's fuel and Live
  telemetry's "fuel to the finish") were the clock over the last lap's time, as if the car were on the line now: a
  lap short for ~97% of every lap of the last stint (half way round lap 30, 129 s left at 85 s a lap: "2 laps" and
  1.5 laps of fuel with 2.5 laps to drive), and the last lap could be an in- or out-lap (98 s, 122 s). Now
  normalize.timed_laps_left / the analyzer's timedLapsLeft (the same sum): the flag falls at the leader's first
  crossing after the clock and each car's at its own next one, so the count uses where the car is on its lap, the
  leader's place and pace when known, the car's usual lap (Deriver.own_lap_time: the median of its last five laps
  off the pit lane; the analyzer: its valid laps), the white flag and the clock being out; where a pace 1.5%
  quicker (TIMED_PACE_MARGIN) would mean a lap more, fuel counts that lap. Right on every tick of the last stint of
  that race; far from the end it stays an estimate (a lap either way) and says so. race_length's lapsByTime uses the
  same sum. test: test_a_timed_races_laps_left_depend_on_where_the_car_is_on_the_lap. Check a number against a
  second measure in the driver's own file before trusting it; the captures are spectator's and carry no fuel.
  Coaching, same file (21 laps against the best lap): the time lost in each of the 14 corners agrees with the
  file's own clock to 0.005 s (the average lap IS a sum over the laps). What you do in a corner did not: it was read
  off the average lap's trace, whose slowest point is not the laps' slowest speed and whose pedal thresholds are
  nobody's (corner 8 "2.3 km/h quicker at the apex" where the laps' middle was level; corner 13's throttle "8.5 m
  late" where it was 3.9 m, 5.9 m once read finely; braking points in steps of the 4.25 m grid). Now coachCompare takes the MEDIAN over the
  laps, each measured on its own against the reference (c.d, c.dv, c.dm, c.dp; c.trace keeps the old; three laps or
  more, a pedal point only from laps that used it), and a corner within CO_LEVEL_S of the reference is level: no
  tips, no cause (it offered "get 1.5 m closer to the apex" in a corner taken 0.02 s quicker than the best lap).
  Setup on the same file: mid-corner understeer, front bar softer / rear bar stiffer (said to be at its maximum,
  90) / wing / front ride height last: the directions match the 296's manual (0 softest, 90 stiffest). The
  understeer measure itself (steering used against neutral) has no second measure in the file: not verified.
  test: test_coaching_measures_each_lap_and_says_nothing_where_no_time_is_lost.
  More of the driver's files (E:\iRacing Telemetry Files: Ferrari 296 GT3 at Sebring and Suzuka, Mustang GT3 at
  Sebring, Formula iR-04 at Watkins Glen, two files): a lap within 0.05 s of the one before was given ITS time
  (the analyzer's lap parser took the first LapLastLapTime near the lap's clock; iRacing posts it a moment after
  the line and the variable holds the lap before's until then): Suzuka 119.888 for 119.911, Sebring 121.875 for
  121.915, Watkins Glen's best lap 107.771 for 107.768. Now the first NEW value after the lap's own last sample
  (as library.session_summary and the live path already did). After it every timed lap in all six files has
  iRacing's time and the tank's fuel, and Coaching's time lost per corner agrees with each file's clock to 0.007 s
  (13 to 17 corners a file). The fake posted lap times on the line, to the sample;
  test_a_lap_is_not_given_the_time_of_the_lap_before posts them late and exact, as the sim does.
  A track's corners are the same in every session (trackCorners, the corner book: localStorage suite:cornerBook, by
  track): the two Watkins Glen files found 14 and 13 corners (a kink on the edge of counting; the corners come from
  the reference lap's load), so "Corner 9" was two places. The first lap analysed at a track writes its corners;
  after that a corner found again keeps this lap's measure, one the lap does not show stays at the book's place
  (its time still counted), one the book does not have is not a corner there; ovals are left as found; cbForget
  starts a track again. The book is this browser's: an engineer on the share link has their own, so the numbers can
  differ between two people until the book travels with the session (not done).
  test: test_a_tracks_corners_are_the_same_corners_in_every_session.
  Live telemetry against the file view (three of those files played through liveTick as if live): the same laps
  count (live valid = the file's clean laps), with the same times and fuel.
- Methods review: how the data is read and what the advice stands on (Nov 2026, the driver's files; 292 channels a
  file at 60 Hz). Sound, checked in the files: Brake is the pedal the car gets (the driver's force curve applied;
  BrakeRaw is the foot, 0.04-0.19 apart on average) and ABS does not pull it down, so it is the right braking signal;
  Throttle equals ThrottleRaw; tyre temperatures in a FILE are the surface ones (LFtempL/M/R, live at 60 Hz) and
  Setup uses those; the carcass temperatures and wear (tempCL/CM/CR, wearL/M/R) only change in the pit stall, in a
  file as live (the Live page's runs table now says "at the last stop"); understeer is steering against the
  low-load fit (the usual understeer gradient; VelocityY gives body slip for oversteer). Not good enough, fixed:
  pedal points were grid points (N = 1000 a lap: 4 m at Indianapolis, 6 m at Suzuka, 14 m at Le Mans) and
  Coaching's advice starts at 6 m, so one grid point made "Brake 6 m later" / "Turn in 6 m sooner" at Suzuka. Now
  read between the grid's points from the lap's own samples (crossPt, rawCross: met's baF / taF, coachFacts' baF /
  taF / tinF; the integer ones stay for indexing; coachFactsSum's medians use the fine ones): a braking point's
  error against the file went from a median of 0.7-1.8 m to 0.2-0.6 m, Suzuka's corner 8 reads "Brake 11 m later".
  A new distance in advice must come from a fine reading, not a grid index. The slowest point's place (mg) is still
  a grid point (a flat minimum has no sharper place). test:
  test_pedal_points_are_read_between_the_grids_points_from_the_laps_own_samples.
- The open parts, checked against the driver's files (Nov 2026). Garage 61: a CSV is the same channels as the .ibt to
  the sample (his 1:24.636 is lap 23 of the 19-49 session), and Coaching's time lost per corner against each of the
  six Indianapolis laps agrees with both files' clocks to 0.001 s; "a lap a little quicker than yours" fell back to
  the fastest one (fixed). The slowest point of a corner is read from the lap's own samples (rawMin: the slowest sample
  inside the corner and a parabola through its neighbours; 0.6-1.2 m off as a grid point, 0.05-0.13 m now; mgF).
  Live telemetry: sector times are placed between the two ticks either side of the line (they read up to 0.05 s
  late; now 0.017 s against the file, which is the lap clock's own offset); the predicted lap, lap stats, top speed,
  brake split and ABS agree with the file. PitWall's your-car values through normalize (an .ibt as a source, the
  player's car in its slot): a stint's laps took the time of the lap before (iRacing posts LastLapTime ~1.6 s after
  the counter moves), so the in-lap opened the next stint ("avg 1:29.2" for 1:24.8 laps): Deriver.stint waits for a
  new time, and its average is of racing laps; a stop's stationary time is the car standing still (CarIdxLapDistPct
  not moving: 13.2 s, not the stall surface's 14.6 s; STILL_PCT_S); the lane loss counts the stretch beside the lane
  as its share of the lap's TIME (the class profile, _u_of_pct) and the slowing for the entry and the speed after the
  exit (PIT_BEFORE_U / PIT_AFTER_U): 28.3 s became 37.0 s, the file says 36.8-37.0 (FakeSource's CarIdxEstTime was a
  straight line in LapDistPct, the convenient shape; it now follows the track's speed: lap_time_share); your own
  incidents (PlayerCarMyIncidentCount rising) end the clean-lap streak. Balance: steering per curve is not level
  through a corner on ANY car (less braking in, most mid-corner, more in fast corners); read against the run's own
  middle, every file showed mid-corner "understeer", the six fastest Garage 61 laps included (+8 to +42%). Each
  phase is now read against what fast laps do in it: the reference laps for this car and track when loaded, else
  BAL_TYPICAL (16 laps and sessions, 6 cars); the fake (SIM_SHAPE) steers in that shape. On his Indianapolis race
  the car reads balanced and Coaching no longer sends him to Setup. Still not verified: whether a phase's excess
  steering IS what a setup change fixes (no second measure: VelocityY's rear-axle slip agreed only loosely, r -0.2
  to -0.5). The corner book and names travel with the session: the driver's page POSTs them to the suite
  (/api/corners, this PC only, clean_corners), which sends {type: corners} to the share link's viewers on connect
  and on change; a viewer takes them for the driver's tracks (cornersTake) and never sends its own.
  tests: test_the_open_parts_slowest_point_garage_61_choice_live_sectors_and_balance, GarageSixtyOneAgainstTheFileTest,
  test_a_viewer_takes_the_drivers_corner_book, test_a_stints_laps_get_their_own_times,
  test_a_stop_is_timed_standing_still_and_your_incidents_end_the_clean_streak, test_the_corner_book_travels_with_the_session.
- PitWall's dashboard, audited (Nov 2026): "Your car" says it is spectating instead of showing empty panels; outside a
  race the standings' Int and Gap come from best laps (derived.classBestIntervalS, classPoleGapS); race strategy's best
  option is the best projected finish (time only between equal finishes), its tyre stop is not offered under a caution
  or from a fall-off over TYRE_DEG_MAX, and a forecast lap already gone by is not shown; where-faster honours comparable.
- Whole-app audit (Nov 2026: every page in every look, desktop and phone, against the Summit Point capture and a real
  .ibt, for script errors, contrast, cut-off words and overflow): lkFx gives a line a colour when none is passed (the
  start lights threw in the draw loop); the overlay's gained / lost colours follow the panel's ink (--gain / --lose set in
  applyTheme: dark pair on a light look) and the podium's header line uses --headText; booth notes fit a phone; PitWall's
  standings show an initial for the first name where narrow (nameCell); small buttons get a bigger touch area on
  coarse pointers (::after, no layout change); the analyzer's white-on-colour pills are #167d42 / #9a6500 and amber
  text is --amb (darker in the light theme). test: test_what_the_november_audit_found.
- Added Nov 2026 (the user's pick of ten). Broadcast: the highlight reel (director _reel_picks / _reel_next: after the
  flag, REEL_AFTER_S on, the race's best REEL_MAX moments in order, back to back, one stinger in, then the podium
  shots; status reel {i, n}; producer "Highlight reel after the flag", needs replays on); pressure in a battle
  (normalize battle_pressure -> battles[].pressure, the battle box's bar: "Holding / Under pressure / Move coming",
  a measure, never called a chance); the battle on the track map (the stretch between each pair, coloured by
  closingSPerLap); the league sheet's own Wins / Podiums / Last race columns on the grid walk (seasonText); the lap
  chart first plays the race as bars changing places (lapChartRace, gfx lapchart.race; only places from lap_chart,
  a car with none for a lap dimmed and without a number). PitWall: Radio calls cycles off / team voice (hers, his) /
  browser voice (pitwall.radio = f | m | on | off): RADIO_LINE maps an alarm to a recorded line
  (client/assets/voice-<f|m>-<line>.mp3, made as the announcer's), one at a time, a call with a number in it still
  read by the browser; "Fuel is good to the end" (fuel_ok) once when the plan stops needing a stop; Glance layout
  (data-role glance, #glancePanel: one value filling the screen, swipe / tap / arrows; every value one the page already
  has). Lap Analyzer: Practice's first target is the session's goal with one light for the lap just driven (.cogoal);
  "Save a session report" (coachReportHtml: Coaching's own markup as a page, no controls, light theme, through
  Api.save_report in the app). Already there, so not built again: setup changes with before / after laps ("Your
  setup changes here", Runs by setup) and spoken alarms. tests: test_the_highlight_reel_plays_the_race_again_after_the_flag,
  test_pressure_in_a_battle_and_the_broadcast_extras, test_pitwall_team_radio_and_the_glance_view,
  test_coaching_has_a_goal_light_and_a_session_report (test_analyzer).
- Visual audit of the broadcast, Arcade above all (Nov 2026: every graphic through the Summit Point capture, qualifying
  to the podium, in every look, looked at and measured; the harness plays the capture at real speed from a chosen
  moment and checks overlaps, words cut off, off-screen boxes and name styles). What it found, each now in place:
  a dead-reckoned outline must be plausible (TrackMap.plausible: closes within MAX_CLOSURE of its length, MIN_LAP_M or
  more, the session's TrackLength within MAX_LENGTH_OFF; checked when built and when loaded: spectating had cached a
  424 m figure of eight for Summit Point and every car sat on one spot of it); makeRoom counts what is drawn outside
  a box (Arcade's kerb below, the card's badges above); "On fire" only for a car racing, from passes after the opening
  lap; the call-outs' letters have no text-stroke (it drew the glyphs' inner contours) and on the opening lap only a
  pass for the lead is called; head to head waits for the opening lap to end; race control's queue drops news from
  another session or over a minute old; a rule on "X span" must not catch a name (nameHtml's span.pnm: the grid
  board's names were 15 px, now ".who>span"); the smaller graphics (.mini, .tr, .hh, .cl, .ob, .pen, .fla, the podium)
  wear Arcade as the tower does; the grid walk's grid is as tall as the grid; the names of one table are one size
  (fitWords, .tb-r / .mini-r) and Strategy widens with its next-stop column; the lap chart's race puts a car with no
  place for the lap below the others. PitWall: a second family name gives way where the table is narrow (nameCell
  .nm-m), Arcade's title trail no longer runs into the session's name. test: test_the_visual_audit_of_the_broadcast_and_arcade.
- While a race is racing the picture is the cars' (the user's call, Nov 2026: the graphics covered most of the screen
  too often; measured mid-race in Arcade with Auto on, the middle of the picture went from 26% covered, 81% at worst,
  to 2% and 19%): overlay raceTight (a race, racing, not finished, not Arrange; gfx race.compact, producer Setup >
  Graphics) makes the middle graphics smaller (RACE_SCALE on top of theme.size and each one's own size, RACE_ORIGIN
  towards their edge; the tower, the clock, the boards and the podium keep theirs), Arcade's pass and lap call-outs
  a strip low on the screen (AX_SMALL, .ax-mini; the flags keep the full screen), the battle box gives way to head to
  head for the pair, and Auto keeps the standings board for a caution. Full size before the green and after the flag.
  A new race graphic in the middle of the picture gets a RACE_SCALE entry. Measure with the coverage harness before
  making anything bigger. test: test_while_racing_the_graphics_leave_the_picture_to_the_cars.
- Looks: Pit wall's bar shows only its numbers under 640 px; :root[data-look] sets overflow-anchor:none (the bar put
  in after load scrolled the page by its height through scroll anchoring, hiding the title).
- Audited against Summit Point (Nov 2026), each now in place: battles are read from the live class gap (a stale
  F2Time had two cars 8 s apart "within a second"; the battle box showed 7.917); a car ahead of the one scored ahead
  has no live gap (gap_between: it read +1:17.0 round the lap); practice and qualifying best laps come from
  ResultsPositions (telemetry had -1 for the pole and a lap that did not count as "pole"); a car in the lane past
  PIT_LONG_S is not a pit stop on air; where-faster needs comparable laps (FASTER_COMPARABLE); a car first seen off
  the grid gets its first stint when it appears; the next stop is a lap still to come from a stint of STINT_MIN_LAPS+;
  race news carries the race's lap while spectating (the leader's); the driver card shows the gap to the car ahead in
  a race and to pole outside one; strategy hides the next-stop column when none is known; the lap chart labels sit
  at each car's place now. tests: test_what_the_summit_point_audit_found.
- Broadcast audit (Nov 2026, Summit Point quali / grid / race / finish and Talladega, every tile on its own):
  before a race has positions the tower, the standings and the crawl show the starting grid with qualifying laps
  (overlay gridField / racingField; the crawl hides with no cars); a car gone from the session before the flag reads
  "Out" (tower, standings, crawl, producer field), never a stale or negative gap (finish_gaps drops negative
  intervals); the onboard graphic hides for it; a race's best and last laps come from the results when telemetry has
  -1 (fromResults); nothing is due and nobody is closing in after the flag; closing in shows the tower's live gap;
  a pack (Talladega, 17 cars) is split at its biggest gaps into fights of BATTLE_MAX_CARS; a race's opening lap is
  not a racing lap (consistency, pace); tyre runs end at the last lap seen. tests:
  test_the_overlay_before_the_start_and_after_the_finish, test_a_pack_is_split_into_fights,
  test_after_the_flag_nothing_is_due_and_nobody_is_closing, test_a_race_opening_lap_is_not_a_racing_lap.
- Race TV graphics, from our own measures (Deriver.race_tv, battle_history, lap_chart; tiles under Race and Show):
  Pit stop (the camera car's, else the best placed in the lane: the box time running, where it should come out,
  then its stationary time and the place it came out in), Closing in (a car catching the one ahead in class, s a
  lap, laps to catch or "not in time"), Race pace (last five racing laps against the class leader), Strategy
  (stops, tyre, laps this stint, next stop from stopDue), Lap chart (every car's place lap by lap; the finish board
  35 s, the lap chart to 60 s, then the podium), the full result (+ stops and laps led) and the battle box's
  history (swapped n times, together m:ss). tests: test_race_tv_extras_from_our_own_measures.
- Producer buttons: camera, replay and director buttons are off while the broadcast is off (the bridge refuses
  them); with the director, grid walk or a followed battle holding the camera they are dimmed and a line says a
  click takes it back. The battle list's buttons all switch off with a second click (unfollow: director end_battle).
- Arrange graphics (producer): the overlay with &edit=1 (or all) is draggable and resizable; it posts each change to
  the producer (postMessage pwLayout), which saves theme.layout; theme.size scales every graphic but the crawl.
- One owner of the camera at a time (director.py): you / director / grid walk / followed battle.
  A producer `camera` command always takes it back (bridge._consumer calls Director.manual()).
  Anything new that moves the camera must go through the Director's owner, not around it.
- Motion on air (overlay MOTION CSS block; producer Look > Motion: Animated / Calm / Still, gfx theme.motion): every look
  moves its own way (data-preset): IMSA wipes in with the highlight leading, Pit board drops, Daylight rises, Night neon
  flickers on, Heritage opens from the centre, Pit wall powers on; Arcade keeps its own. Entrances use clip-path,
  opacity and filter only (makeRoom measures the boxes). Restarted one-shots from script: lk-hit (hit(): something new
  on a graphic already up), m-roll (roll(): a lap count that changed), mv-up / mv-dn (a car gained or lost a place).
  Animated adds the loops (a glint across the headers every 22 s, a flag that is out rippling); Calm drops them; Still
  stops the graphics (stingers and show screens still move).
- Looks on air: the producer's look presets Pit wall and Arcade restyle the graphics (overlay CSS under `:root[data-look]`),
  not just the colours. The driver card shows the driver's picture (gfx.photos by iRacing user id, uploaded in the
  producer's Liveries tab; without one, nothing: no drawn stand-in, the user's call) and the helmet
  (carrender's pk_helmet.png when iRacing gives one -- seen working, VARIABLES_TO_VERIFY "Helmets" -- else drawn). Arcade's
  announcer (the suite's voice-<f|m>-*.mp3 from the relay, no sound effects: a battle coming on and the race's flags;
  gfx sound.on, off by default; gfx sound.man picks the man's voice) never speaks with the master switch off.
- `tests/test_broadcast.py` and `tests/test_broadcast2.py` must pass with the others.
- Graphics state ("gfx") lives in the relay, not the bridge. Camera/replay commands go
  through the bridge under `--allow-camera` and the producer token, separate from pit.
- The relay serves both pages at /broadcast/. The suite runs the bridge with camera control
  on and keeps the view/producer tokens in %LOCALAPPDATA%\iRacingSuite\broadcast.json so the
  OBS source address survives restarts.
