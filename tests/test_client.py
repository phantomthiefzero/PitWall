"""PitWall's page (client/index.html): pieces of its script run in Node."""
import json
import os
import shutil
import subprocess

import pytest


@pytest.mark.skipif(not shutil.which("node"), reason="needs Node.js")
def test_radio_calls_read_the_alarm_text_as_speech():
    """PitWall's radio says what the alarm bar says, written for the ear."""
    with open(os.path.join(os.path.dirname(__file__), "..", "client", "index.html"), encoding="utf-8") as f:
        s = f.read()
    fn = s[s.index("  function spoken(html) {"):s.index("  function say(html) {")]
    cases = ['<span class="derived">#12 0.45 s behind you</span>', "Box this lap · last chance on fuel",
             "Incidents 15x of 17", "Pit window open · laps 20–24"]
    out = subprocess.run(["node", "-e", fn + "for (const t of " + json.dumps(cases) + ") console.log(spoken(t));"],
                         capture_output=True, text=True, encoding="utf-8", timeout=30).stdout.splitlines()
    assert out == ["car 12 0.45 seconds behind you", "Box this lap, last chance on fuel", "Incidents 15 of 17",
                   "Pit window open, laps 20 to 24"]
