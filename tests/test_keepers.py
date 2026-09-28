import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from keepers import SPRINT_MPS, line_spot, place_keepers

DT = 1 / 30
TIMES = [i * DT for i in range(300)]


def test_line_spot_is_between_the_ball_and_the_goal_centre():
    x, y = line_spot((30.0, 10.0), 1)  # ball 25 m out, to his right
    assert 46 < x < 52 and 0 < y < 3
    # On the line from the ball to (52.5, 0).
    assert abs((x - 52.5) * 10.0 - y * (30.0 - 52.5)) < 1e-6
    far, near = line_spot((5.0, 0.0), 1), line_spot((46.0, 0.0), 1)
    assert math.isclose(52.5 - far[0], 6.0) and math.isclose(52.5 - near[0], 1.0)


def test_a_drifting_keeper_is_brought_back_no_faster_than_a_sprint():
    # Ball in his half, out wide; the tracking drifts him 25 m out of goal, nowhere near it.
    ball = [(10.0, 25.0, 0.0)] * 300
    frames = [{"gk": (50.0 - 25.0 * min(k / 100, 1), 0.0), "other": (-50.0, 0.0)} for k in range(300)]
    moved = place_keepers(frames, TIMES, ball, ["gk", "other"], 299)
    spot = line_spot((10.0, 25.0), 1)
    assert moved["gk"] > 20
    assert all(math.dist(f["gk"], spot) < 1e-6 for f in frames)
    for a, b in zip(frames, frames[1:]):
        assert math.dist(a["gk"], b["gk"]) <= SPRINT_MPS * DT + 1e-9


def test_a_sweeping_run_with_the_ball_near_him_is_kept():
    # He runs out of his area to a through ball at (29, 5) and stays there 2 s.
    ball = [(29.0, 5.0, 0.0)] * 300
    track = []
    for k in range(300):
        w = min(max((k - 100) / 70, 0), 1) - min(max((k - 240) / 60, 0), 1)
        track.append((45.0 - 15.0 * w, 5.0 * w))
    frames = [{"gk": p, "other": (-50.0, 0.0)} for p in track]
    place_keepers(frames, TIMES, ball, ["gk", "other"], 299)
    assert frames[200]["gk"] == (30.0, 5.0)
