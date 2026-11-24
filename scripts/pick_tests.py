"""Which test files a pull request needs, from the files it changes.

Usage (CI):  git diff --name-only origin/master...HEAD | python scripts/pick_tests.py
Prints test file paths separated by spaces, or "tests" for everything.

Pull requests run only the tests for what they touch; every merge to master still
runs the whole suite before the app is built for release, so nothing slips through.
Anything not listed here (requirements, the workflow, build files, a new folder) runs
everything.
"""
import os
import sys

ALL = "tests"
SUITE = "tests/test_suite.py"
ANALYZER = "tests/test_analyzer.py"
PITWALL = ["tests/test_pipeline.py", "tests/test_broadcast.py", "tests/test_broadcast2.py"]

# first match wins
RULES = [
    ("docs/", []),
    ("suite/web/analyzer/", [ANALYZER, SUITE]),      # the suite serves it and checks its size
    ("suite/web/", [SUITE]),
    ("suite/second_camera.py", [SUITE, "tests/test_second_camera.py"]),
    ("suite/", [SUITE, ANALYZER]),                    # bridge, share, updater, page splitting
    ("broadcast/", PITWALL[1:] + [SUITE]),
    ("pitwall/director_scorecard.py", ["tests/test_scorecard.py"]),
    ("pitwall/director", PITWALL + [SUITE, "tests/test_scorecard.py"]),   # the director and its report
    ("scripts/director_scorecard.py", ["tests/test_scorecard.py"]),
    ("pitwall/bridge.py", PITWALL + [SUITE, "tests/test_second_camera.py"]),
    ("relay/", PITWALL + [SUITE, "tests/test_second_camera.py"]),
    ("client/", ["tests/test_pipeline.py", "tests/test_client.py", SUITE]),
    ("pitwall/commands.py", PITWALL + [SUITE, "tests/test_highlights.py"]),
    ("pitwall/obs.py", PITWALL + [SUITE, "tests/test_highlights.py"]),
    ("pitwall/sources.py", PITWALL + [SUITE, "tests/test_highlights.py"]),
    ("pitwall/recorder.py", PITWALL + [SUITE, "tests/test_highlights.py"]),
    ("pitwall/", PITWALL + [SUITE]),
    ("tests/analyzer_harness.js", [ANALYZER]),
    ("scripts/pick_tests.py", [SUITE]),
    ("highlights/", ["tests/test_highlights.py"]),
    ("run_highlights.py", ["tests/test_highlights.py"]),
]


def pick(paths):
    out = []
    for p in (x.strip().replace("\\", "/") for x in paths):
        if not p:
            continue
        if p.endswith(".md"):
            continue
        if p.startswith("tests/test_") and p.endswith(".py"):
            hit = [p] if os.path.exists(p) else []          # a deleted test file
        else:
            hit = next((t for pre, t in RULES if p.startswith(pre)), None)
            if hit is None:
                return [ALL]
        out += [t for t in hit if t not in out]
    return out or [SUITE]           # always run something


if __name__ == "__main__":
    print(" ".join(pick(sys.stdin.read().splitlines())))
