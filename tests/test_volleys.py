import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from volleys import find_volleys

DT = 1 / 30
TIMES = [i * DT for i in range(40)]


def _setup(ball_z, facing_x):
    # He stands at (40, 0) attacking +x; the ball comes in from facing_x * 5 m.
    ball = [(40.0 + facing_x * 5 * (1 - k / 30), 0.0, ball_z) for k in range(40)]
    players = [{"a": (40.0, 0.0)} for _ in TIMES]
    return ball, players


def test_a_high_ball_with_his_back_to_goal_is_an_overhead_kick():
    ball, players = _setup(1.3, -1)  # he faces -x, away from the goal he attacks
    contacts = [{"f": 30, "p": "a", "b": "R"}]
    found = find_volleys(contacts, ball, TIMES, players, {"a": 1})
    assert len(found) == 1 and contacts[0]["v"] == 1 and found[0][1] > 170


def test_facing_goal_or_a_low_ball_or_a_header_is_not():
    ball, players = _setup(1.3, 1)  # facing the goal
    assert find_volleys([{"f": 30, "p": "a", "b": "R"}], ball, TIMES, players, {"a": 1}) == []
    ball, players = _setup(0.6, -1)  # ball too low
    assert find_volleys([{"f": 30, "p": "a", "b": "R"}], ball, TIMES, players, {"a": 1}) == []
    ball, players = _setup(1.3, -1)
    assert find_volleys([{"f": 30, "p": "a", "b": "H"}], ball, TIMES, players, {"a": 1}) == []
    assert find_volleys([{"f": 30, "p": "a", "b": "R", "s": 2}], ball, TIMES, players, {"a": 1}) == []
