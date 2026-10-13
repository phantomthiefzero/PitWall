# iRacing Suite

One Windows app for the iRacing tools, fed by a built-in telemetry bridge:

| App | What it does |
|---|---|
| **Lap Analyzer** | Compare laps against your best or a reference, read the car setup in the iRacing garage layout, watch live graphs, and see each setup change's effect run by run |
| **PitWall** | Engineer and spotter dashboard: track map, standings, flags, weather, car systems (everything below this section) |
| **Broadcast** | TV-style overlay for OBS when you spectate a race, run from a producer panel by you and a co-host (see [Broadcast](#broadcast)) |

## Using the app

1. Open this repo's **Releases** page and download **iRacing Suite.exe** from the newest build.
2. Double-click it. The first time, Windows may say "Windows protected your PC" because the app isn't code-signed: click **More info**, then **Run anyway**.
3. The suite opens in its own window. Start iRacing and get in the car; the home screen turns green when it's live.
4. No iRacing handy? **Replay a telemetry file…** plays any `.ibt` back as if live (Lap Analyzer).

Everything runs on this PC and only reads iRacing's telemetry. PitWall's relay runs inside the app on
127.0.0.1 with fresh tokens each launch, so it is not reachable from other machines. Sharing PitWall
with remote crew still uses a public relay as described below. A log is written to
`%LOCALAPPDATA%\iRacingSuite\suite.log`.

## How the app is put together

| Path | Purpose |
|---|---|
| `suite/app.py` | the app: window (pywebview / Edge WebView2), runs everything on a background thread |
| `suite/bridge.py` | the Lap Analyzer's bridge: live iRacing or `.ibt` replay, serves the pages and `/ws` (standard library only) |
| `suite/pitwall_host.py` | runs PitWall's own `relay/server.py` and `pitwall/bridge.py`, unchanged, inside the app |
| `suite/web/` | home screen and the Lap Analyzer (`analyzer/index.html`); PitWall's dashboard is served from `client/` |
| `tests/test_suite.py` | replays a synthetic `.ibt`, streams PitWall's fake field through the in-app relay, checks pages and streams |
| `.github/workflows/build.yml` | on every push to master: all tests, then builds `iRacing Suite.exe` on Windows and publishes it as a release |

Run from source: `python -m pip install -r requirements.txt`, then `python -m suite`
(`--browser` to use the browser, `--pitwall-source fake` for PitWall's fake field).

# Broadcast

A broadcast overlay for races you spectate, built on PitWall's bridge and relay.

| Graphic | What it shows |
|---|---|
| Timing tower | running order with interval, gap to leader, best or last lap, or stops seen; class colours; pit tag; the camera car highlighted; fastest lap in purple; rows slide on overtakes; keeps the camera car on the tower in a big field |
| Session banner | track, laps or time left, the racing flag (start-light and service bits are left out), and a Replay / Paused / Slow-mo tag whenever the picture is not live |
| Battle box | the closest fight, or one you pick: cars, gaps, and the closing trend when iRacing's gaps support one |
| Driver card | whoever the camera is on (or a pinned driver): number, name, licence, iRating, car, class, position, last and best lap, stint and stops seen |
| Track map | outline built from a lap, class-coloured cars, camera car and battle cars marked |
| Race control | fastest laps, lead changes in every class, pit stops, flags, rain |
| Standings board | the whole field, two columns, for breaks |

The producer panel switches each graphic on and off, picks tower modes and classes, pins the
driver card, puts a battle on air, cuts iRacing's camera to any driver in any camera group
(or lets iRacing choose: most exciting, leader, incidents), and runs the replay (pause,
slow motion, previous incident, back to live). Two people can use it at once; both always
see the same state.

Same accuracy rule as everything else here: numbers come from iRacing or are derived in
`pitwall/normalize.py`, and derived ones have a dotted underline on air. Camera and replay
control has NOT been run against the sim yet; see `docs/VARIABLES_TO_VERIFY.md`, "Broadcast".

### In the suite (one PC)

1. Open the suite, join the race as a spectator in iRacing.
2. On the home screen, **Broadcast → Copy OBS address**. In OBS add a **Browser Source**,
   1920 × 1080, paste the address, and put it above your iRacing capture. The address stays
   the same every launch, so this is a one-time step.
3. **Open producer panel**. Turn graphics on, tap a driver to cut the camera to them.

For separate OBS sources per graphic (to move or crop them in OBS), add `&only=tower` (or
`banner`, `battle`, `card`, `map`, `ticker`, `board`) to the address.

### With a co-host

The suite's relay only listens on this PC. A co-host needs a public address for it. The
least setup is a Cloudflare quick tunnel (free, no account), run on the PC with the suite:

```
cloudflared tunnel --url http://127.0.0.1:8765
```

It prints an `https://<random>.trycloudflare.com` address. Send your co-host the producer
link with that address in place of `http://127.0.0.1:8765` (the token stays the same). The
producer link controls your camera and graphics, so only share it with your co-host; the
address changes every time the tunnel starts.

A permanent relay (a small VPS) works the same way as for PitWall below; start it with a
producer token and the bridge with `--allow-camera`:

```
python relay/server.py --port 8765 --bridge-token B --view-token V --producer-token P
py -3 -m pitwall stream --source live --relay wss://relay.example.com --token B --allow-camera
```

then `https://relay.example.com/broadcast/overlay.html?token=V` in OBS and
`https://relay.example.com/broadcast/producer.html?token=P` for each producer.

Try it without iRacing: `py -3 -m pitwall stream --source fake --spectator --allow-camera ...`
runs a fake spectated multiclass race whose camera responds to the panel (results say
"fake field").

# PitWall

An engineer/spotter companion for iRacing. The driver's PC runs a **bridge** that
reads iRacing's shared-memory telemetry and streams it to a **relay**; remote
engineers and spotters open the **web client** in a browser.

Design rule: nothing is displayed as fact unless iRacing reported it. Computed
values are visibly marked, values the sim only refreshes at pit stops are marked
as snapshots, and missing values are shown as missing. See
`docs/VARIABLES_TO_VERIFY.md` for the accuracy checklist and how to complete it.

```
driver PC                       anywhere public               each engineer/spotter
+-----------------+   ws    +--------------------+   ws    +----------------------+
| iRacing         |         | relay/server.py    |         | client/index.html    |
|  shared memory  | ------> |  1 bridge in       | ------> |  track map, standings|
| pitwall bridge  | <------ |  N viewers out     | <------ |  car, weather        |
+-----------------+  cmds   |  token auth        |  cmds   +----------------------+
                            +--------------------+
```

## What is in the box

| Path | Purpose | Verified here? |
|---|---|---|
| `pitwall/sources.py` | live iRacing (pyirsdk), memory-dump playback, fake 24-car field | fake: yes. live: **needs your PC** |
| `pitwall/normalize.py` | raw variables → protocol messages, enum decoding, derived values | yes, against fake |
| `pitwall/trackmap.py` | builds the track outline from one lap | yes: 1.5 m mean error on a 5.1 km synthetic lap |
| `pitwall/recorder.py`, `analyze.py` | phase-1 truth harness | yes |
| `pitwall/bridge.py`, `delta.py` | streaming, delta encoding (~30 KB/s for 24 cars) | yes, end to end |
| `pitwall/commands.py` | pit-service commands into iRacing (phase 2), off by default | gate logic yes; actual sim call **needs your PC** |
| `relay/server.py` | fan-out with bridge / view / engineer / producer tokens; broadcast gfx state; serves /broadcast/ | yes |
| `client/index.html` | dashboard, no build step | rendered headless against the fake stream |
| `tests/test_pipeline.py` | proofs for the derived parts | passing |

Everything marked "needs your PC" touches Windows-only shared memory. It follows
pyirsdk's documented API, but it has not run against a real session yet.

## Setup on the driver's PC (Windows)

```
py -3 -m pip install -r requirements.txt
```

Sanity check with the simulation (no iRacing needed):

```
py -3 -m pitwall check --source fake
py -3 -m pytest tests -q          (or: py -3 tests\test_pipeline.py)
```

With iRacing running and you in the car:

```
py -3 -m pitwall check --source live
```

If that prints a normalized frame with your name in `drivers`, the live source works.
If it prints "could not connect", iRacing is not in a session yet.

## Phase 1 — record, then decide what is true

```
py -3 -m pitwall record --source live --out practice.jsonl.gz --hz 10 --minutes 15
py -3 -m pitwall analyze practice.jsonl.gz --md docs\VARIABLES_practice.md
```

Do this for practice, qualifying, a race with a pit stop, a rain session, a
replay, and a spectator join. Fill in the "Observed" column of
`docs/VARIABLES_TO_VERIFY.md`. Anything that disagrees with the claim changes the
client's treatment before it is trusted.

To build (and cache) the track outline from a recording that contains a full clean lap:

```
py -3 -m pitwall map race.jsonl.gz
```

The bridge also builds it automatically the first time you complete a clean lap
on a track, and caches it under `%USERPROFILE%\.pitwall\maps`.

If you want me to check normalization against a real frame without you
streaming anything: `py -3 -m irsdk --dump snapshot.bin` while in the car, and
send `snapshot.bin`. `pitwall check --source dump --path snapshot.bin` runs anywhere.

## Running it for real

Relay (a small VPS, Fly.io, Railway, or a machine at home with a forwarded port):

```
python relay/server.py --port 8765 --bridge-token <secret1> --view-token <secret2> [--engineer-token <secret3>]
```

Health check: `http://host:8765/health`.

Measured locally against a replayed 18-car race (`--source replay`), so these are
real numbers rather than estimates:

| Viewers | Per viewer | Relay egress |
|---|---|---|
| 1 | 15.5 msg/s, 11.4 KB/s | 11 KB/s |
| 4 | 15.5 msg/s, 10.3 KB/s | 41 KB/s |
| 12 | 15.5 msg/s, 10.4 KB/s | 124 KB/s |
| 25 | 15.5 msg/s, 11.4 KB/s | 286 KB/s |

No per-viewer degradation up to 25 viewers; egress scales linearly, so budget
about 11 KB/s per engineer (~2.3 Mbit/s at 25). Token handling was checked the
same way: a wrong token, a missing token, a view token on the bridge endpoint and
a bridge token on the view endpoint are all refused (close code 4001).

**One bridge per relay.** A second bridge with the correct token takes the slot
and the displaced one stops with a message rather than reconnecting -- otherwise
the two replace each other indefinitely and viewers get a fresh session/map burst
every few seconds while neither bridge holds the stream.

**Tokens travel in the query string**, so they land in relay logs and browser
history. Treat them as shared secrets to be rotated, and put the relay behind TLS
before it faces the internet. Put it behind TLS (Caddy / nginx, or the
platform's built-in) so the client can use `wss://`; browsers on https pages
refuse plain `ws://`.

Bridge on the driver's PC:

```
py -3 -m pitwall stream --source live --relay wss://relay.example.com --token <secret1>
```

Client: serve `client/index.html` from anywhere static (the relay host, GitHub
Pages, a Dropbox link) and open

```
https://.../index.html?relay=wss://relay.example.com&token=<secret2>
```

or leave the parameters off and type them into the connect form.

Local test of the whole stack in three terminals:

```
python relay/server.py --port 8765 --bridge-token B --view-token V
py -3 -m pitwall stream --source fake --relay ws://127.0.0.1:8765 --token B
cd client && python -m http.server 8080   ->  http://localhost:8080/?relay=ws://127.0.0.1:8765&token=V
```

## Phase 2 — commands into iRacing

Only pit-service commands are possible through the SDK (fuel, tires, compound,
fast repair, windshield, clears). They are implemented in `pitwall/commands.py`
and gated three ways: the bridge must run with `--allow-commands`, the relay must
be started with `--engineer-token`, and the viewer must connect with that token.
In-car adjustments (brake bias, ARBs, TC, ABS, mix) are **not** reachable through
the SDK and are deliberately not implemented; they would need keystroke injection.

The client has no command UI yet. That is intentional: phase 1 first.

## Repo layout

```
pitwall/        bridge package (python -m pitwall ...)
relay/          relay server
client/         dashboard (single file)
broadcast/      OBS overlay and producer panel (single files)
docs/           PROTOCOL.md, ARCHITECTURE.md, VARIABLES_TO_VERIFY.md
tests/          proofs for derived logic
```
