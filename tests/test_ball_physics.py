import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from ball_physics import apply_physics, simulate, solve_kick


def test_a_ground_pass_rolls_and_slows_down():
    u0, w0 = solve_kick(0.1, 20.0, 1.4, peak=0.1)
    assert w0 == 0
    s, z, peak = simulate(0.1, u0, w0, [0.35, 0.7, 1.05, 1.4])
    assert abs(s[-1] - 20.0) < 0.2
    steps = [b - a for a, b in zip([0.0] + s, s)]
    assert steps == sorted(steps, reverse=True)  # decelerating
    assert peak < 0.2


def test_a_lofted_ball_arcs_up_and_comes_down():
    u0, w0 = solve_kick(0.1, 40.0, 2.4, peak=12.0)
    s, z, peak = simulate(0.1, u0, w0, [0.6, 1.2, 1.8, 2.4])
    assert abs(s[-1] - 40.0) < 0.3
    assert abs(peak - 12.0) < 0.5
    assert z[1] > z[0] or z[2] > z[3]  # rises then falls


def test_apply_physics_replaces_a_bent_pass_with_a_straight_decelerating_one():
    times = [i / 30 for i in range(61)]
    ball = [(i * 0.5, 1.0 if 15 < i < 45 else 0.0, 0.1) for i in range(61)]  # 30 m, bent sideways
    out, n = apply_physics(ball, times, [0, 60], end=60)
    assert n == 1
    assert all(abs(b[1]) < 1e-9 for b in out[1:60])
    speeds = [math.dist(a, b) for a, b in zip(out[5:55], out[6:56])]
    assert speeds[0] > speeds[-1]  # slows down along the way


def test_the_ball_arrives_at_the_height_it_is_touched_at():
    times = [i / 30 for i in range(61)]
    ball = [(i * 0.5, 0.0, 8.0 if 5 < i < 59 else 0.1) for i in range(61)]
    ball[60] = (30.0, 0.0, 7.5)  # tracked 7.5 m up at a header: out of reach
    out, n = apply_physics(ball, times, [0, 60], end=60, touch_heights={60: (1.6, 2.3)})
    assert n == 1
    assert 1.6 <= out[60][2] <= 2.3
    assert max(abs(out[k + 1][2] - out[k][2]) for k in range(60)) < 0.6  # no pop in height
