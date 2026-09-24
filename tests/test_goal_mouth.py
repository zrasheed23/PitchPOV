import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from goal_mouth import BAR_Z, GOAL_LINE_X, MARGIN, NET_DEPTH, POST_Y, correct_goal_mouth

FPS = 30
GOAL = 30  # shot at frame 30 (t = 1 s)


def path(end, n=150, vanish_at=None, side=1):
    """Ball standing still until the shot, then a straight line to `end` at
    frame GOAL + 30, carrying on at the same speed; None from vanish_at on."""
    start = (side * 40.0, 0.0, 0.2)
    ball = []
    for i in range(n):
        w = max(i - GOAL, 0) / 30
        ball.append(tuple(s + (e - s) * w for s, e in zip(start, end)))
    if vanish_at is not None:
        ball[vanish_at:] = [None] * (n - vanish_at)
    times = [i / FPS for i in range(n)]
    return ball, times


def crossing_point(ball, side=1):
    for a, b in zip(ball, ball[1:]):
        if side * a[0] < GOAL_LINE_X <= side * b[0]:
            w = (GOAL_LINE_X - side * a[0]) / (side * b[0] - side * a[0])
            return tuple(av + (bv - av) * w for av, bv in zip(a, b))
    return None


def assert_rests_in_net(ball, side=1):
    assert all(b is not None for b in ball[GOAL:])
    x, y, z = ball[-1]
    assert x == pytest.approx(side * (GOAL_LINE_X + NET_DEPTH))
    assert abs(y) <= POST_Y - MARGIN + 1e-9
    assert z == 0.0


def test_shot_inside_the_posts_is_not_shifted():
    ball, times = path((52.5, 1.0, 0.5))
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["corrected"] is False
    assert out[: GOAL + 30] == ball[: GOAL + 30]
    assert_rests_in_net(out)


def test_wide_shot_is_pulled_inside_the_post_without_a_jump():
    ball, times = path((52.5, -4.7, 0.2), vanish_at=GOAL + 35)  # like Di María
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["corrected"] is True and info["reason"] == "wide"
    assert info["shift"] == pytest.approx(4.7 - (POST_Y - MARGIN))

    y = crossing_point(out)[1]
    assert y == pytest.approx(-(POST_Y - MARGIN))
    # Nothing before the shot moves, and the shift grows smoothly from zero.
    assert out[: GOAL + 1] == ball[: GOAL + 1]
    steps = [math.dist(a, b) for a, b in zip(out[GOAL - 1 : GOAL + 30], out[GOAL : GOAL + 31])]
    assert max(steps) < 0.6
    assert_rests_in_net(out)


def test_shot_over_the_bar_is_brought_under_it():
    ball, times = path((52.5, 0.5, 3.2))
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "high"
    assert crossing_point(out)[2] == pytest.approx(BAR_Z - MARGIN)
    assert_rests_in_net(out)


def test_ball_that_vanishes_short_of_the_line_is_carried_in():
    ball, times = path((52.5, 5.0, 0.3), vanish_at=GOAL + 10)
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "short"
    assert info["extrapolated"] > 0
    assert abs(crossing_point(out)[1]) <= POST_Y - MARGIN + 1e-9
    assert_rests_in_net(out)


def test_ball_missing_from_the_shot_heads_for_goal():
    ball, times = path((52.5, 0.0, 0.3), vanish_at=GOAL + 1)
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "short"
    assert_rests_in_net(out)


def test_goal_at_the_negative_end():
    ball, times = path((-52.5, 6.0, 0.4), side=-1, vanish_at=GOAL + 40)
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "wide"
    assert crossing_point(out, side=-1)[1] == pytest.approx(POST_Y - MARGIN)
    assert_rests_in_net(out, side=-1)
