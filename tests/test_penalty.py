import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from penalty import (GOAL_LINE_X, SPOT_CLEAR_M, SPOT_X, TAKER_FROM_BALL_M, find_keeper, is_legal,
                     nearest_legal, pin_ball, place_players)

FPS = 30
KICK = 60  # kick at t = 2 s


def test_pin_ball_holds_the_ball_on_the_spot_until_the_kick():
    ball = [(40.0 + i * 0.01, 0.5, 0.2) for i in range(10)] + [(45.0, 1.0, 0.5), None]
    out = pin_ball(ball, 9, side=1)
    assert out[:10] == [(SPOT_X, 0.0, 0.0)] * 10
    assert out[10:] == ball[10:]
    assert pin_ball(ball, 3, side=-1)[0] == (-SPOT_X, 0.0, 0.0)


@pytest.mark.parametrize("side", [1, -1])
def test_nearest_legal(side):
    assert nearest_legal((side * 20.0, 5.0), side) == (side * 20.0, 5.0)  # already legal: unchanged
    # Inside the area, well away from the spot: out through the nearest edge.
    x, y = nearest_legal((side * 38.0, 15.0), side)
    assert is_legal((x, y), side)
    assert y == pytest.approx(15.0) and side * x < 36.0
    x, y = nearest_legal((side * 48.0, 19.0), side)
    assert is_legal((x, y), side) and abs(y) > 20.16
    # On the spot: out onto the arc, at least 9.15 m away.
    for xy in [(side * SPOT_X, 0.0), (side * (SPOT_X - 2), 1.0), (side * 34.0, 3.0)]:
        x, y = nearest_legal(xy, side)
        assert is_legal((x, y), side)
        assert math.dist((x, y), (side * SPOT_X, 0.0)) >= SPOT_CLEAR_M


def setup(side=1):
    """Taker 3 m behind the spot, keeper 2 m off his line, a defender in the
    area, one standing on the spot and one walking along the edge of the arc."""
    n = 150
    times = [i / FPS for i in range(n)]
    frames = []
    for i in range(n):
        t = times[i]
        frames.append({
            "taker": (side * (SPOT_X - 3.0), 1.0),
            "keeper": (side * (GOAL_LINE_X - 2.0), 5.0),
            "defender": (side * 44.0, -8.0 + 0.5 * t),
            "on_spot": (side * (SPOT_X + 0.02 * math.sin(7 * t)), 0.02 * math.cos(9 * t)),
            "walker": (side * 35.0, -10.0 + 4.0 * t),
            "far": (side * 10.0, 0.0),
        })
    return frames, times


@pytest.mark.parametrize("side", [1, -1])
def test_penalty_setup_is_legal_until_the_kick_and_blends_back(side):
    frames, times = setup(side)
    out, moved = place_players(frames, times, KICK, side, "taker", "keeper")
    assert moved > 0
    for i in range(KICK + 1):
        for pid in ("defender", "on_spot", "walker", "far"):
            assert is_legal(out[i][pid], side, tol=1e-6), (i, pid, out[i][pid])
        assert out[i]["keeper"][0] == side * GOAL_LINE_X
        assert out[i]["keeper"][1] == pytest.approx(3.66)  # between the posts
        assert out[i]["far"] == frames[i]["far"]
    # The taker is at the ball as he kicks it, and untouched long before.
    assert math.dist(out[KICK]["taker"], (side * SPOT_X, 0.0)) == pytest.approx(TAKER_FROM_BALL_M)
    assert out[0]["taker"] == frames[0]["taker"]
    # Back on the tracking once the blend is over.
    assert out[-1] == frames[-1]
    # Nothing jumps: no one gains more than a sprint on his tracked movement.
    for i in range(1, len(out)):
        dt = times[i] - times[i - 1]
        for pid in out[i]:
            extra = math.dist(out[i][pid], out[i - 1][pid]) - math.dist(frames[i][pid], frames[i - 1][pid])
            assert extra / dt < 9.0, (i, pid)


def test_find_keeper_picks_the_goalkeeper_in_the_goal():
    frames = [{"gk_home": (-30.0, 0.0), "gk_away": (51.8, 0.4), "striker": (41.0, 0.0)}]
    assert find_keeper(frames, 0, 1, ["gk_home", "gk_away"]) == "gk_away"
    assert find_keeper(frames, 0, -1, ["gk_home", "gk_away"]) is None
