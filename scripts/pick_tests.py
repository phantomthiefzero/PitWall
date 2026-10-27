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
    ("suite/", [SUITE, ANALYZER]),                    # bridge, share, updater, page splitting
    ("broadcast/", PITWALL[1:] + [SUITE]),
    ("client/", ["tests/test_pipeline.py", "tests/test_client.py", SUITE]),
    ("pitwall/", PITWALL + [SUITE]),
    ("relay/", PITWALL + [SUITE]),
    ("tests/analyzer_harness.js", [ANALYZER]),
    ("scripts/pick_tests.py", [SUITE]),
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
