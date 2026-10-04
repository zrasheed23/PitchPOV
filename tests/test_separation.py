import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from ball_rules import blend_to_touch
from separation import MIN_M, overlaps, separate

TIMES = [k / 30 for k in range(90)]
PLAYERS = {"a": {"team": "home"}, "b": {"team": "home"}, "c": {"team": "away"}}


def _frames(a, b, c=(20.0, 0.0)):
    return [{"a": a, "b": b, "c": c} for _ in TIMES]


def test_teammates_are_pushed_apart_the_one_off_the_ball_moves():
    frames = _frames((0.0, 0.0), (0.3, 0.0))
    ball = [(-0.5, 0.0, 0.1)] * len(TIMES)  # nearer a: b moves
    moved = separate(frames, TIMES, ball, PLAYERS, {})
    assert set(moved) == {"b"} and frames[45]["a"] == (0.0, 0.0)
    assert math.dist(frames[45]["a"], frames[45]["b"]) >= MIN_M - 1e-6


def test_opponents_in_a_duel_may_be_closer_and_locked_players_stay():
    frames = _frames((0.0, 0.0), (5.0, 5.0), (0.45, 0.0))
    ball = [(0.2, 0.3, 0.1)] * len(TIMES)  # both within 1.5 m of the ball: 0.4 m is enough
    assert overlaps(frames, ball, PLAYERS) == []
    frames = _frames((0.0, 0.0), (0.3, 0.0))
    ball = [(10.0, 0.0, 0.1)] * len(TIMES)
    separate(frames, TIMES, ball, PLAYERS, {"b": set(range(90))})  # b is locked: a moves
    assert frames[45]["b"] == (0.3, 0.0) and math.dist(frames[45]["a"], frames[45]["b"]) >= MIN_M - 1e-6


def test_blend_to_touch_puts_him_at_the_ball_and_keeps_his_run_away_from_it():
    frames = [{"p": (4.0 * t, 0.0)} for t in TIMES]  # jogging 4 m/s
    assert blend_to_touch(frames, TIMES, "p", 45, (4.0 * TIMES[45], 5.0)) == 1.0
    assert math.dist(frames[45]["p"], (4.0 * TIMES[45], 5.0)) < 1e-9
    assert frames[0]["p"] == (0.0, 0.0)


def test_blend_to_touch_refuses_a_sprint():
    frames = [{"p": (0.0, 0.0)} for _ in TIMES]
    assert blend_to_touch(frames, TIMES, "p", 45, (30.0, 0.0)) is None  # 30 m in 1.5 s
    assert frames[45]["p"] == (0.0, 0.0)
