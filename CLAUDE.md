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
- `commands.py` (phase 2 pit service) is still UNTESTED against the sim.

A recurring trap, worth stating plainly: FakeSource repeatedly encoded the same
assumption as the code under test, so the suite passed while the sim disagreed.
It has now been corrected five times -- the F2Time placeholder, gap monotonicity,
the mirrored yaw convention, impossible best laps, and single-class-only fields.
When adding a derived value, make the fake reproduce the sim's awkward shape
(placeholders, "carry over", mixed classes), not the convenient one.
