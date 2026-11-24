"""Apps in the suite that are built but not offered yet. Off: no home tile, no page (404), no engine, nothing on the
app's api. The code, its tests and the release build stay as they are, so turning one on is this one line.

Highlights (highlights/): off by the user's call, Nov 2026 ("keep it out until I decide to include it"). For
development: IRACING_SUITE_HIGHLIGHTS=1 turns it on for that run.
"""
import os

HIGHLIGHTS = os.environ.get("IRACING_SUITE_HIGHLIGHTS") == "1"
