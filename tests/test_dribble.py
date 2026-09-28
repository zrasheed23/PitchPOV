import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from dribble import find_dribbles, rebuild_dribbles, smooth_carrier

T = [i / 30 for i in range(91)]  # 3 s


def a_dribble(jitter=0.6):
    """Player A runs along x at 5 m/s with the ball wobbling around him (as tracked);
    B stands far away."""
    players, ball = [], []
    for i, t in enumerate(T):
        x = 5 * t
        players.append({"A": (x, 0.05 * math.sin(i * 1.7)), "B": (30.0, 10.0)})
        ball.append((x + 0.6 + jitter * math.sin(i * 0.9), jitter * math.cos(i * 1.3), 0.1))
    return ball, players


def test_a_player_with_the_ball_at_his_feet_is_a_dribble():
    ball, players = a_dribble()
    assert find_dribbles(ball, T, players, [], end=90) == [(0, 89, "A")]


def test_a_touch_by_someone_else_ends_it():
    ball, players = a_dribble()
    found = find_dribbles(ball, T, players, [{"f": 45, "p": "B", "b": "R"}], end=90)
    assert found[0][1] < 45


def test_the_rebuilt_ball_is_pushed_along_touch_by_touch():
    ball, players = a_dribble()
    out, added, carries, rebuilt = rebuild_dribbles(ball, T, players, [], end=90)
    assert rebuilt and not carries
    frames = [c["f"] for c in added]
    assert len(frames) >= 4 and all(c["s"] == 1 and c["p"] == "A" for c in added)
    gaps = [T[b] - T[a] for a, b in zip(frames, frames[1:])]
    assert all(0.3 - 1e-9 <= g <= 0.6 + 1e-9 for g in gaps)
    # At each touch the ball is at his foot, and it stays on the ground and near him.
    for f in frames:
        assert math.hypot(out[f][0] - players[f]["A"][0], out[f][1] - players[f]["A"][1]) < 0.6
    first, last = rebuilt[0]
    assert all(out[k][2] < 0.2 for k in range(first + 1, last))  # along the ground
    assert all(math.hypot(out[k][0] - players[k]["A"][0], out[k][1] - players[k]["A"][1]) < 2.8 for k in range(first, last))
    # Between touches it only slows down (a push, then rolling), never speeds up by itself.
    for a, b in zip(frames, frames[1:]):
        steps = [math.dist(out[k][:2], out[k + 1][:2]) for k in range(a, b - 1)]
        assert all(s2 <= s1 + 1e-6 for s1, s2 in zip(steps, steps[1:]))


def test_a_dribble_it_cant_rebuild_is_left_as_a_carry():
    ball, players = a_dribble()
    # The tracking has the ball teleport 2.5 m sideways and back every few frames.
    ball = [(x, y + (2.5 if i % 7 == 3 else 0), z) for i, (x, y, z) in enumerate(ball)]
    out, added, carries, rebuilt = rebuild_dribbles(ball, T, players, [], end=90)
    assert out[3] == ball[3] or rebuilt  # untouched unless it could be rebuilt
    assert carries or rebuilt


def test_the_carrier_is_smoothed_only_around_the_dribble():
    ball, players = a_dribble()
    before = [dict(p) for p in players]
    smooth_carrier(players, T, 30, 60, "A")
    wobble = lambda ps, lo, hi: sum(abs(ps[i]["A"][1]) for i in range(lo, hi))
    assert wobble(players, 30, 60) < wobble(before, 30, 60) * 0.3
    assert players[5] == before[5] and players[85] == before[85]
    assert all(players[i]["B"] == before[i]["B"] for i in range(91))


def test_a_statsbomb_carrier_trailing_the_ball_is_eased_onto_it():
    from dribble import FOLLOW_M, follow_carries
    times = [k / 30 for k in range(200)]
    ball = [(20.0 + 0.2 * k, 0.0, 0.0) for k in range(200)]  # 6 m/s along the ground
    frames = [{"r": (20.0 + 0.2 * k - 6.0, 0.0)} for k in range(200)]  # PFF has him 6 m behind it
    carry = {"type": "Carry", "p": "r", "f": 100, "xy": (40.0, 0.0), "raw": {"duration": 2.0}}
    moved = follow_carries(ball, [True] * 200, times, frames, [carry], 190)
    assert moved and moved[0][2] > 4.5
    assert all(math.dist(frames[k]["r"], ball[k][:2]) <= FOLLOW_M + 0.3 for k in range(115, 150))
    assert frames[5]["r"] == (20.0 + 0.2 * 5 - 6.0, 0.0)  # well before (the 5 m eases on over 2.7 s): as tracked
    # Not backed by the tracked ball at its start: left alone.
    frames = [{"r": (20.0 + 0.2 * k - 6.0, 0.0)} for k in range(200)]
    assert not follow_carries(ball, [True] * 200, times, frames, [dict(carry, xy=(10.0, 20.0))], 190)
