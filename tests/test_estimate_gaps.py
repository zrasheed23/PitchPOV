import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from estimate_gaps import PASS_MPS, estimate_gaps

T = [i / 30 for i in range(120)]
# Player A runs along x at 5 m/s; player B stands 30 m away.
PLAYERS = [{"A": (i / 30 * 5, 0.0), "B": (30.0, 0.0)} for i in range(120)]


def at_a(i):
    return (PLAYERS[i]["A"][0] + 0.5, 0.0, 0.11)


def speeds(path):
    return [math.dist(a, b) / (tb - ta) for a, b, ta, tb in zip(path, path[1:], T, T[1:])]


def test_a_pass_holds_then_travels_to_where_the_ball_reappears():
    ball = [at_a(i) for i in range(20)] + [None] * 60 + [(30.3, 0.0, 0.11)] * 40
    out, ranges = estimate_gaps(ball, T, PLAYERS, end=110)
    assert ranges == [[20, 79]]
    assert all(b is not None for b in out)
    assert max(speeds(out)) < PASS_MPS * 1.2  # no jumps, no teleport at the ends
    assert max(b[2] for b in out[20:80]) > 2  # a 26 m pass along the ground gets lofted


def test_a_dribble_follows_the_player():
    ball = [at_a(i) for i in range(20)] + [None] * 60 + [at_a(i) for i in range(80, 120)]
    out, _ = estimate_gaps(ball, T, PLAYERS, end=110)
    assert abs(out[50][0] - at_a(50)[0]) < 0.1


def test_a_gap_at_the_start_follows_whoever_has_it_first():
    ball = [None] * 30 + [at_a(i) for i in range(30, 120)]
    out, ranges = estimate_gaps(ball, T, PLAYERS, end=110)
    assert ranges == [[0, 29]]
    assert abs(out[0][0] - at_a(0)[0]) < 0.1


def test_nothing_after_the_shot_is_touched():
    ball = [at_a(i) for i in range(20)] + [None] * 100
    out, ranges = estimate_gaps(ball, T, PLAYERS, end=20)
    assert ranges == [] and out[50] is None
