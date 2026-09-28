import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from keepers import SPRINT_MPS, ease_to_freeze_frame, place_keepers, plan_dive, plausible_spot

DT = 1 / 30
TIMES = [i * DT for i in range(300)]


def test_a_believable_keeper_is_left_alone():
    ball = [(20.0, 5.0, 0.0)] * 300
    track = [(49.0, 0.5 + 0.01 * k) for k in range(300)]
    frames = [{"gk": p, "other": (-50.0, 0.0)} for p in track]
    moved = place_keepers(frames, TIMES, ball, ["gk", "other"], 299)
    assert moved["gk"] == 0 and [f["gk"] for f in frames] == track


def test_only_the_implausible_part_is_corrected():
    # Outside the posts with the ball in his box: back to the post, not to the middle.
    assert plausible_spot((50.0, 6.0), (45.0, 10.0), 1) == (50.0, 3.66)
    # Out of his area with the ball far away: back to the edge of the area.
    x, y = plausible_spot((30.0, 0.0), (-10.0, 0.0), 1)
    assert math.isclose(x, 36.3) and y == 0.0
    # More than 8 m from the ball-goal line: to 8 m from it.
    x, y = plausible_spot((48.0, -12.0), (0.0, 0.0), 1)
    assert math.isclose(abs(y), 8.0)


def test_corrections_never_move_faster_than_a_sprint():
    ball = [(-10.0, 0.0, 0.0)] * 300
    track = [(50.0 - 25.0 * min(k / 100, 1), 0.0) for k in range(300)]  # drifts 25 m out, ball far away
    frames = [{"gk": p, "other": (-50.0, 0.0)} for p in track]
    place_keepers(frames, TIMES, ball, ["gk", "other"], 299)
    assert all(frames[k]["gk"][0] >= 36.3 - 1e-6 for k in range(150, 300))
    for a, b in zip(frames, frames[1:]):
        assert math.dist(a["gk"], b["gk"]) <= SPRINT_MPS * DT + 0.9 * DT  # his own run plus the correction


def test_a_sweeping_run_with_the_ball_near_him_is_kept():
    ball = [(29.0, 5.0, 0.0)] * 300
    track = []
    for k in range(300):
        w = min(max((k - 100) / 70, 0), 1) - min(max((k - 240) / 60, 0), 1)
        track.append((45.0 - 15.0 * w, 5.0 * w))
    frames = [{"gk": p, "other": (-50.0, 0.0)} for p in track]
    place_keepers(frames, TIMES, ball, ["gk", "other"], 299)
    assert frames[200]["gk"] == (30.0, 5.0)


def test_the_keeper_is_on_the_statsbomb_spot_at_the_kick_and_holds_it():
    frames = [{"gk": (50.0, 0.0)} for _ in TIMES]
    gap = ease_to_freeze_frame(frames, TIMES, "gk", 150, (48.0, 2.0))
    assert math.isclose(gap, math.hypot(2, 2))
    assert frames[150]["gk"] == (48.0, 2.0) and frames[180]["gk"] == (48.0, 2.0)
    assert frames[100]["gk"] == (50.0, 0.0)  # 1.7 s before: still on his track
    assert frames[299]["gk"] == (50.0, 0.0)  # and back on it afterwards


def _shot(y_end, z=0.5, frames_to_line=20):
    ball = [(40.0, 0.0, z)] * 151
    ball += [(40.0 + 13.0 * k / frames_to_line, y_end * k / frames_to_line, z) for k in range(1, 150)]
    return ball


def test_the_dive_goes_toward_the_crossing_and_misses_out_of_reach():
    frames = [{"gk": (51.0, 0.0)} for _ in TIMES]
    d = plan_dive(_shot(3.0), TIMES, frames, "gk", 150, 1)
    assert d["kind"] == "dive" and d["dir"] == 1 and d["reached"] is False and d["stretch"] > 0.5
    assert d["f"] == 156  # 0.2 s reaction
    d = plan_dive(_shot(-1.5, z=2.0), TIMES, frames, "gk", 150, 1)
    assert d["dir"] == -1 and d["height"] == 2.0 and d["reached"] is True


def test_a_fast_shot_gets_a_late_partial_dive_and_one_at_him_a_block():
    frames = [{"gk": (51.0, 0.0)} for _ in TIMES]
    slow, fast = (plan_dive(_shot(2.0, frames_to_line=n), TIMES, frames, "gk", 150, 1) for n in (30, 8))
    assert fast["stretch"] < slow["stretch"]
    assert plan_dive(_shot(0.3), TIMES, frames, "gk", 150, 1)["kind"] == "block"
