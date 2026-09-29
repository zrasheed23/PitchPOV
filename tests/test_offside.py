import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from offside import ease_onside, find_assist, margin

TEAM = {"s": "home", "d1": "away", "d2": "away", "gk": "away"}


def test_margin_is_past_the_second_last_defender_and_the_ball():
    at = {"s": (30.0, 0.0), "d1": (28.0, 5.0), "d2": (25.0, -5.0), "gk": (50.0, 0.0)}
    assert margin(at, (10.0, 0.0), TEAM, "s", 1) == 2.0  # the keeper is last, d1 second-last
    assert margin(at, (31.0, 0.0), TEAM, "s", 1) == -1.0  # behind the ball: onside
    assert margin({**at, "s": (-5.0, 0.0)}, (10.0, 0.0), TEAM, "s", 1) is None  # own half


def _ev(f, p, kind, pass_type=None):
    raw = {"pass": {"type": {"name": pass_type}}} if pass_type else {}
    return {"f": f, "p": p, "type": kind, "on_ball": True, "raw": raw}


def test_the_assist_is_the_last_pass_the_scorer_plays_next():
    evs = [_ev(10, "a", "Pass"), _ev(20, "b", "Ball Receipt*"), _ev(30, "b", "Pass"), _ev(40, "s", "Ball Receipt*"),
           _ev(50, "s", "Shot")]
    e, f = find_assist(evs, "s", 50, [{"f": 31, "p": "b"}])
    assert e["p"] == "b" and f == 31  # played at the passer's touch
    corner = [_ev(30, "b", "Pass", "Corner"), _ev(40, "s", "Shot")]
    assert find_assist(corner, "s", 40) == (None, None)  # no offside from a corner


def test_ease_onside_moves_him_back_at_the_pass_and_not_at_his_touch():
    times = [k / 30 for k in range(120)]
    frames = [{"s": (30.0, 0.0)} for _ in times]
    ease_onside(frames, times, "s", 60, 2.0, 1, next_touch=90)
    assert frames[60]["s"] == (28.0, 0.0)
    assert frames[90]["s"] == (30.0, 0.0)
