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
It has now been corrected six times -- the F2Time placeholder, gap monotonicity,
the yaw convention (mirrored, then turned 90 degrees; settled against GPS), impossible best laps,
and single-class-only fields. Where a real file carries a second measure of the same thing (GPS
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

- Track maps: the suite builds PitWall's outline for each track from your .ibt files by GPS (`Library.build_maps`, on
  its own thread at start) into PitWall's cache (`pitwall.trackmap`, north up, `FRAME`); the bridge looks again every
  10 s while it has none. PitWall's own builder needs your car on track; spectating and replays have no kinematics.
- Your files (`suite/library.py`): the Lap Analyzer's "Your files" menus list the .ibt and Garage61 .csv
  files in iRacing's telemetry folder and folders you add (kept in %LOCALAPPDATA%\iRacingSuite\library.json),
  named by what they are (read from the header and last sample, cached). Only the suite's own server serves
  them, by an id from the last listing, never a path from the page; over one WebSocket (/api/library/stream),
  with 96 KB pieces (/api/library/file) as the fallback. The relay never serves them.
- Your last session (home page, `library.session_summary`): a lap's time is the first new LapLastLapTime after
  its OWN last sample (iRacing updates it after the line), and clean laps are counted as the Lap Analyzer counts
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
  chase) and held GRID_POLE_HOLD times longer, the rest take turns between rear chase and chase. On air it is a TV
  grid slot (gwalk, bottom left: place, row, livery, name, quali lap, gaps to pole and class pole, the lap it was set
  on, a pole badge, the class's name and size at its first car) and the grid beside it (gladder, top right: two
  staggered columns, the car lit, the ones walked dimmed). Gaps and rows come from normalize (_grid_derived, each grid
  row's `derived`); the driver card stays down while walking. Arrange's "show every graphic" lays both on the pole.
- Show screens move slowly and quietly (drifting glows, a light sweeping by, specks rising, the stripes easing;
  the words and lists come in once per screen change, never on each refresh). Each screen can use your own file
  instead (gfx scene.files[mode]: an uploaded picture or looping muted video, or an https page) with a dark backing
  under our words, or alone (scene.bare[mode]).
- Stingers: built-in styles (overlay STINGERS + CSS `#stinger.run[data-style=...]`: wipe, chevrons, checker,
  panels, iris), each covering the whole picture from 42% to 58% of its length, where the cut falls; or your own
  video (theme.stinger.video, every uploaded video is listed in Look > Stingers, each with its own cut point in
  theme.stinger.cuts). Adding a built-in: one STINGERS entry and its CSS, and keep it covering 42-58%. The stinger's
  own settings are never staged in studio mode. The first graphics state an overlay sees only sets the stinger mark.
- Page size: the overlay is past 128 KB uncompressed. Every browser asks for gzip (~40 KB on the wire); keep it so.
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
  The bridge answers a director command with the new status at once, sim or no sim: its frame loop only runs while
  iRacing ticks, so status left for the next frame never arrived with the sim closed (the switch looked stuck).
- The race director tells stories (director._stories / _story_step): incidents cut in at once; otherwise lead
  changes, passes for position (feed events the bridge passes in), battles scored by gap, closing, what they are for
  and size, each class leader (more the longer off air) and top cars in their box. It stays with a story for
  SEGMENT_MIN_S..SEGMENT_MAX_S going round its angles; a story just aired scores lower for FATIGUE_S; nothing is
  picked at random. tests: test_director_tells_stories_and_moves_on.
- Auto ("Pick graphics for me") is contextual (overlay ctxWants / CONTEXTUAL): the driver card comes up for a few
  seconds when the camera finds a new car and now and then on a long shot (never over a battle box or a qualifying
  lap, which name the cars), the battle box shows the battle the camera is on, the onboard graphic follows onboard
  cameras, the speed trap comes up now and then outside a race; a pinned card or a battle picked by hand still rules.
  Outside a race the tower's Interval column reads the gap to pole. The director also follows push laps in practice
  and qualifying to the line (stories "push", up to 150 s) and the leader on the last lap ("finish").
- The checkered flag waits for the leader (normalize.race_flags, state derived.raceFlags): iRacing raises it when a
  timed race's clock runs out, a lap early; until SessionState turns checkered it reads as the white flag.
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
  (carrender's pk_helmet.png when iRacing gives one -- unverified, VARIABLES_TO_VERIFY "Helmets" -- else drawn). Arcade's
  announcer (the suite's voice-<f|m>-*.mp3 from the relay, no sound effects: a battle coming on and the race's flags;
  gfx sound.on, off by default; gfx sound.man picks the man's voice) never speaks with the master switch off.
- `tests/test_broadcast.py` and `tests/test_broadcast2.py` must pass with the others.
- Graphics state ("gfx") lives in the relay, not the bridge. Camera/replay commands go
  through the bridge under `--allow-camera` and the producer token, separate from pit.
- The relay serves both pages at /broadcast/. The suite runs the bridge with camera control
  on and keeps the view/producer tokens in %LOCALAPPDATA%\iRacingSuite\broadcast.json so the
  OBS source address survives restarts.
