import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from net import NET_DEPTH, SIDE_M, into_net

TIMES = [k / 30 for k in range(200)]


def test_the_ball_carries_on_as_it_crossed_then_stops_in_the_net():
    v = (22.0, -3.0, 1.0)
    path = into_net((52.5, 1.0, 0.8), v, TIMES, 1)
    # First frame after the line: same direction and (nearly) the same speed.
    step = [(path[1][i] - path[0][i]) * 30 for i in range(3)]
    cos = sum(a * b for a, b in zip(step, v)) / (math.hypot(*step) * math.hypot(*v))
    assert cos > 0.99 and abs(math.hypot(*step) / math.hypot(*v) - 1) < 0.15
    # Never deeper than the net, always inside the posts, ends at rest on the grass.
    assert all(52.5 <= x <= 52.5 + NET_DEPTH + 1e-9 and abs(y) <= SIDE_M + 1e-9 for x, y, _ in path)
    assert path[-1][2] == 0.0 and math.dist(path[-1], path[-2]) < 1e-3


def test_the_other_goal_works_the_same_way():
    path = into_net((-52.5, 0.0, 0.3), (-15.0, 0.0, 0.0), TIMES, -1)
    assert all(-52.5 - NET_DEPTH - 1e-9 <= x <= -52.5 for x, _, _ in path)
