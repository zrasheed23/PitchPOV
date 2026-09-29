import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from accuracy import check_clip


def _clip():
    frames = []
    for k in range(60):
        x = 40.0 + 0.6 * max(k - 30, 0)  # still at his foot, then an 18 m/s shot from frame 30
        frames.append({"t": k / 30, "b": [x, 0.0, 0.1],
                       "p": {"a": [39.7, 0.0], "gk": [51.0, 2.0], "d": [30.0, 5.0]}})
    return {"frames": frames, "goalFrame": 30, "kickFrame": 30, "scorerId": "a",
            "players": [{"id": "a", "name": "A", "position": "CF"}, {"id": "gk", "name": "K", "position": "GK"},
                        {"id": "d", "name": "D", "position": "CB"}],
            "contacts": [{"f": 30, "p": "a", "b": "R"}], "carries": [], "restarts": []}


def test_a_clean_clip_has_no_findings():
    assert not any(check_clip(_clip()).values())


def test_each_check_finds_its_problem():
    clip = _clip()
    clip["contacts"].append({"f": 10, "p": "d", "b": "R"})  # D is 10 m from the ball
    clip["frames"][20]["p"]["d"] = [45.0, 5.0]  # a 15 m jump in a third of a second
    clip["keeperDive"] = {"keeper": "gk", "kind": "dive", "f": 25, "dir": 1, "stretch": 1, "height": 0.1, "reached": False}
    sb = [{"f": 12, "p": "gk", "type": "Clearance", "on_ball": True}]  # no touch by the keeper
    found = check_clip(clip, sb, {"y": 2.0, "z": 0.1})
    assert found["touch far"] and found["sprint"] and found["statsbomb missing"] and found["crossing off"]
    assert any("before the kick" in f for f in found["keeper dive"])


def test_statsbomb_touches_lead_and_replace_pff_duplicates():
    from statsbomb import merge_touches
    times = [k / 30 for k in range(100)]
    ball = [(float(k) * 0.2, 0.0, 0.0) for k in range(100)]
    tracked = [True] * 100
    raw = {"type": {"name": "Clearance"}}
    sb = [{"f": 40, "p": "cb", "b": "H", "type": "Clearance", "on_ball": True, "xy": (8.0, 0.0), "raw": raw},
          {"f": 60, "p": "d", "b": "F", "type": "Ball Receipt*", "on_ball": True, "xy": (20.0, 5.0), "raw": raw},
          {"f": 90, "p": "s", "b": "R", "type": "Ball Recovery", "on_ball": True, "xy": (18.0, 0.0), "raw": raw}]
    pff = [{"f": 42, "p": "cb", "b": "F"}, {"f": 92, "p": "s", "b": "L"}]
    contacts, out, placed, _, counts = merge_touches(pff, sb, ball, tracked, times, 92, shooter="s")
    assert [(c["f"], c["p"], c["b"]) for c in contacts] == [(40, "cb", "H"), (60, "d", "F"), (92, "s", "L")]
    assert placed == [60] and out[60] == (20.0, 5.0, 0.11)  # the tracked ball is nowhere near: put there
    assert counts["added"] == 1 and counts["pff_replaced"] == 1
