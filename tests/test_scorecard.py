"""The director's scorecard (pitwall.director_scorecard): a change to the director is judged on every recording,
several runs each, against the kept range, never on one run of one race."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pitwall import director_scorecard as sc  # noqa: E402


def runs(*rows):
    return [dict(zip(sc.METRICS, r)) for r in rows]


def test_worse_only_when_outside_the_kept_range_by_more_than_a_pass():
    kept = {"passes": 57, "runs": runs((22, 49, 25, 11), (24, 50, 20, 8), (22, 53, 22, 8))}
    same = {"passes": 57, "runs": runs((21, 49, 23, 11), (22, 50, 24, 10))}        # Talladega's swing: no verdict
    assert sc.compare(same, kept)["verdict"] == "same"
    worse = {"passes": 57, "runs": runs((18, 44, 29, 7), (19, 45, 28, 6))}
    c = sc.compare(worse, kept)
    assert c["verdict"] == "worse"
    assert c["metrics"]["live"]["verdict"] == "worse" and c["metrics"]["missed"]["verdict"] == "worse"
    better = {"passes": 57, "runs": runs((27, 55, 15, 12), (28, 56, 16, 13))}
    assert sc.compare(better, kept)["verdict"] == "better"
    assert sc.compare(better, None)["verdict"] == "new"


def test_a_race_with_a_different_number_of_passes_is_compared_as_shares():
    kept = {"passes": 20, "runs": runs((5, 10, 8, 4))}
    now = {"passes": 40, "runs": runs((10, 20, 16, 8))}       # normalize found twice as many: the same shares
    assert sc.compare(now, kept)["verdict"] == "same"


def test_the_kept_scores_are_saved_by_name_and_the_script_finds_recordings():
    d = tempfile.mkdtemp()
    p = os.path.join(d, "base.json")
    sc.save({"a.jsonl.gz": {"passes": 2, "runs": runs((1, 1, 1, 0))}}, p)
    sc.save({"b.jsonl.gz": {"passes": 3, "runs": runs((2, 2, 1, 0))}}, p)
    kept = sc.load(p)
    assert sorted(kept) == ["a.jsonl.gz", "b.jsonl.gz"], "a second save adds, it does not replace"
    for n in ("x.jsonl.gz", "y.txt"):
        open(os.path.join(d, n), "w").close()
    assert [os.path.basename(f) for f in sc.recordings([d])] == ["x.jsonl.gz"]
    assert "[same]" in sc.line("x", kept["a.jsonl.gz"], sc.compare(kept["a.jsonl.gz"], kept["a.jsonl.gz"]))


def test_the_kept_scores_in_the_repository_cover_the_four_recordings():
    kept = sc.load()
    names = " ".join(kept)
    for race in ("Summit-Point", "Talladega", "Suzuka", "Circuit-des-24-Heures-du-Mans"):
        assert race in names, race
    for v in kept.values():
        assert v["passes"] >= 0 and len(v["runs"]) >= 2 and all(set(sc.METRICS) <= set(r) for r in v["runs"])
    json.dumps(kept)
