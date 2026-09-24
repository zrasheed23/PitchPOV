import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from goal_mouth import BAR_Z, GOAL_LINE_X, MARGIN, MAX_CARRY_M, NET_DEPTH, POST_Y, correct_goal_mouth, goal_mouth_distance

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


def test_ball_that_vanishes_near_goal_is_carried_in():
    ball, times = path((52.5, 5.0, 0.3), vanish_at=GOAL + 25)  # vanishes ~2 m out
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "short" and not info["needs_review"]
    assert 0 < info["carried"] <= MAX_CARRY_M
    assert out[: GOAL + 25] == ball[: GOAL + 25]
    assert abs(crossing_point(out)[1]) <= POST_Y - MARGIN + 1e-9
    assert_rests_in_net(out)


def test_ball_that_vanishes_far_from_goal_is_left_for_review():
    ball, times = path((52.5, 0.0, 0.3), vanish_at=GOAL + 10)  # vanishes ~8 m out
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["needs_review"] is True and info["corrected"] is False
    assert info["reason"] == "not near goal"
    assert out == ball  # no invented flight


def test_ball_tracked_to_the_end_but_never_near_goal_is_left_for_review():
    ball, times = path((20.0, 10.0, 0.0), side=1)  # heads back upfield
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["needs_review"] is True
    assert out == ball


def clearance(closest_x, y=1.0, n=150):
    """Shot at frame GOAL runs in 0.5 s to closest_x, then is cleared back upfield."""
    ball = []
    for i in range(n):
        if i <= GOAL:
            x = 40.0
        elif i <= GOAL + 15:
            x = 40.0 + (closest_x - 40.0) * (i - GOAL) / 15
        else:
            x = closest_x - 0.3 * (i - GOAL - 15)
        ball.append((x, y, 0.2))
    return ball, [i / FPS for i in range(n)]


def test_goal_line_clearance_is_pulled_just_over_the_line_and_kept():
    ball, times = clearance(51.4)  # like Messi 108': ~1 m short, then cleared
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "clearance" and info["corrected"] and not info["needs_review"]
    assert info["shift"] == pytest.approx(GOAL_LINE_X + MARGIN - 51.4)
    closest = max(out, key=lambda b: b[0])
    assert closest[0] == pytest.approx(GOAL_LINE_X + MARGIN)
    # Blends back to the real path ~0.5 s either side; the clearance stays.
    c = GOAL + 15
    assert out[: GOAL + 1] == ball[: GOAL + 1]
    assert out[c + 16 :] == ball[c + 16 :]
    assert all(o[1:] == b[1:] for o, b in zip(out, ball))  # only x moves
    # No jump: the shift changes by at most ~0.15 m between frames.
    extra = [abs((o2[0] - o1[0]) - (b2[0] - b1[0])) for o1, o2, b1, b2 in zip(out, out[1:], ball, ball[1:])]
    assert max(extra) < 0.15


def test_ball_that_stops_short_and_is_cleared_from_far_out_is_not_a_clearance():
    ball, times = clearance(49.0)  # 3.5 m short: not a goal-line clearance
    _, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "not near goal" and info["needs_review"]


def test_goal_mouth_distance():
    assert goal_mouth_distance((52.5, 1.0, 1.0), 1) == 0.0
    assert goal_mouth_distance((49.5, 7.66, 0.0), 1) == pytest.approx(5.0)
    assert goal_mouth_distance((-50.5, 0.0, 0.0), -1) == pytest.approx(2.0)


def test_goal_at_the_negative_end():
    ball, times = path((-52.5, 6.0, 0.4), side=-1, vanish_at=GOAL + 40)
    out, info = correct_goal_mouth(ball, times, GOAL)
    assert info["reason"] == "wide"
    assert crossing_point(out, side=-1)[1] == pytest.approx(POST_Y - MARGIN)
    assert_rests_in_net(out, side=-1)
