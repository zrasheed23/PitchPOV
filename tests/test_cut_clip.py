import math
import bz2
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from cut_clip import (OVERRIDES, RAW_BALL_MAX_M, ball_path, ball_position, choose_ball_source, clamp_z, fill_gaps,
                      load_overrides, pick_ball_source, raw_ball_distance, read_windows, to_standard_pitch)


def test_clamp_z():
    assert clamp_z(-4.4) == 0.0
    assert clamp_z(1.5) == 1.5


def test_standard_pitch_is_identity_for_105x68():
    assert to_standard_pitch(10.0, -5.0, 105.0, 68.0) == (10.0, -5.0)


def test_standard_pitch_scales_other_sizes():
    x, y = to_standard_pitch(55.0, 35.0, 110.0, 70.0)
    assert (x, y) == (52.5, 34.0)


def test_fill_short_gap_linearly():
    vals = [(0.0, 0.0), None, None, (3.0, 6.0)]
    assert fill_gaps(vals) == [(0.0, 0.0), (1.0, 2.0), (2.0, 4.0), (3.0, 6.0)]


def test_long_gap_and_edges_stay_none():
    vals = [None, (0.0,), None, None, None, (4.0,), None]
    assert fill_gaps(vals, max_gap=2) == vals


def test_read_windows_cuts_several_goals_in_one_pass(tmp_path):
    # 10 fps for 20 s; events at t = 5 s and t = 12 s, one id that never appears.
    path = tmp_path / "t.jsonl.bz2"
    with bz2.open(path, "wt") as f:
        for i in range(200):
            eid = {50: 111, 120: 222}.get(i)
            f.write(json.dumps({"frameNum": i, "videoTimeMs": i * 100, "game_event_id": eid}) + "\n")
    periods = set()
    windows = read_windows(path, [111, 222, 333], before_s=3, after_s=2, periods=periods)
    assert set(windows) == {111, 222}
    assert periods == {None}  # these frames carry no period

    frames, goal_index = windows[111]
    assert frames[goal_index]["frameNum"] == 50
    assert frames[0]["frameNum"] == 20 and frames[-1]["frameNum"] == 70

    frames, goal_index = windows[222]
    assert frames[goal_index]["frameNum"] == 120
    assert frames[0]["frameNum"] == 90 and frames[-1]["frameNum"] == 140


def test_ball_position_reads_either_source():
    frame = {"ballsSmoothed": {"x": 1.0, "y": 2.0, "z": None},
             "balls": [{"x": 3.0, "y": 4.0, "z": 0.5}]}
    assert ball_position(frame, "smoothed") == (1.0, 2.0, 0.0)
    assert ball_position(frame, "raw") == (3.0, 4.0, 0.5)
    assert ball_position({"ballsSmoothed": None, "balls": []}, "raw") is None


def test_ball_path_converts_and_clamps():
    frames = [{"balls": [{"x": 55.0, "y": 35.0, "z": -0.2}]}, {"balls": []}]
    assert ball_path(frames, "raw", 110.0, 70.0) == [(52.5, 34.0, 0.0), None]


def test_raw_ball_distance_is_to_the_nearest_teammate_at_the_shot():
    raw = [(40.0, 0.0, 0.0), None, (42.0, 0.0, 0.0)]  # gap-filled to (41, 0) at the shot
    team = [(50.0, 0.0), (41.0, 3.0)]
    assert raw_ball_distance(raw, team, 1) == pytest.approx(3.0)
    assert raw_ball_distance([None, None, None], team, 1) is None


def test_raw_by_default_smoothed_when_raw_is_far_or_missing():
    assert choose_ball_source(0.5) == "raw"
    assert choose_ball_source(RAW_BALL_MAX_M) == "raw"
    assert choose_ball_source(RAW_BALL_MAX_M + 0.1) == "smoothed"  # like Di María, 13 m
    assert choose_ball_source(None) == "smoothed"


def test_override_wins_over_the_automatic_choice():
    override = {"ballSource": "smoothed", "note": "raw ball ~8 m from Mbappé at the shot"}
    assert pick_ball_source(7.7) == ("raw", "raw")
    assert pick_ball_source(7.7, override) == ("smoothed", "raw")
    assert pick_ball_source(13.3, {"ballSource": "raw", "note": "x"}) == ("raw", "smoothed")


def test_load_overrides_checks_entries(tmp_path):
    assert load_overrides(tmp_path / "missing.json") == {}
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"1_2.json": {"ballSource": "smoothed", "note": "why"}}))
    assert load_overrides(path)["1_2.json"]["ballSource"] == "smoothed"
    path.write_text(json.dumps({"1_3.json": {"exclude": True, "note": "not a goal"}}))
    assert load_overrides(path)["1_3.json"]["exclude"] is True
    for bad in ({"ballSource": "balls", "note": "why"}, {"ballSource": "raw"}, {"exclude": True}):
        path.write_text(json.dumps({"1_2.json": bad}))
        with pytest.raises(ValueError):
            load_overrides(path)


def test_checked_in_overrides_are_valid():
    assert load_overrides(OVERRIDES)["10517_6738451.json"]["ballSource"] == "smoothed"


def test_drop_repeats_blanks_every_other_frame_stutter():
    from cut_clip import drop_repeats
    a, b, c = (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (3.0, 0.0, 0.0)
    assert drop_repeats([a, a, b, b, c]) == [a, None, b, None, c]


def test_drop_repeats_keeps_a_ball_at_rest():
    from cut_clip import drop_repeats
    spot, kicked = (41.5, 0.0, 0.0), (43.0, 0.5, 0.2)
    path = [spot] * 30 + [kicked]
    assert drop_repeats(path) == path


def test_read_windows_drops_frames_that_repeat_a_video_time(tmp_path):
    # 10 fps; frames 51-55 repeat frame 50's time, and the goal is tagged on one of the repeats.
    path = tmp_path / "t.jsonl.bz2"
    with bz2.open(path, "wt") as f:
        for i in range(120):
            ms = 5000 if 50 <= i <= 55 else (i * 100 if i < 50 else (i - 5) * 100)
            eid = 111 if i == 53 else None
            f.write(json.dumps({"frameNum": i, "videoTimeMs": ms, "game_event_id": eid}) + "\n")
    frames, goal_index = read_windows(path, [111], before_s=3, after_s=2)[111]
    times = [fr["videoTimeMs"] for fr in frames]
    assert times == sorted(set(times))  # strictly increasing, no repeats
    assert frames[goal_index]["frameNum"] == 50  # the kept copy of the tagged time


def test_borrow_gaps_fills_long_gaps_from_the_other_feed_without_seams():
    from cut_clip import MAX_GAP_FRAMES, borrow_gaps
    n = 60
    other = [(float(i), 0.0, 0.0) for i in range(n)]
    # Chosen feed agrees with the other but is 1 m higher in y, and loses the ball for 30 frames.
    chosen = [(float(i), 1.0, 0.0) if not 10 <= i < 40 else None for i in range(n)]
    out, borrowed = borrow_gaps(chosen, other, end=n)
    assert borrowed == 30 > MAX_GAP_FRAMES
    assert all(b is not None for b in out)
    # Next to the seams the borrowed ball is shifted onto the chosen feed (y close to 1).
    assert abs(out[10][1] - 1.0) < 0.2 and abs(out[39][1] - 1.0) < 0.2
    # In the middle it follows the other feed.
    assert out[25] == other[25]
    # Nothing after `end` is touched.
    out2, _ = borrow_gaps(chosen, other, end=5)
    assert out2[20] is None


def test_drop_out_of_play_blanks_the_ball_far_outside_the_pitch_before_the_shot():
    from cut_clip import drop_out_of_play
    path = [(50.0, 0.0, 0.0), (57.0, 9.0, 4.7), (40.0, 36.0, 1.0), (54.0, 1.0, 0.2), (56.0, 0.0, 0.0)]
    # Frames 0-3 are before the shot; frame 4 is the ball in the net and stays.
    assert drop_out_of_play(path, end=4) == [(50.0, 0.0, 0.0), None, (40.0, 36.0, 1.0), (54.0, 1.0, 0.2), (56.0, 0.0, 0.0)]


def test_smooth_jumps_turns_a_teleport_into_a_glide():
    from cut_clip import GLIDE_MPS, JUMP_MPS, smooth_jumps
    times = [i / 30 for i in range(90)]
    # Ball rolling slowly, then the tracking jumps it 20 m in one frame.
    ball = [(i * 0.1, 0.0, 0.0) for i in range(45)] + [(20 + i * 0.1, 0.0, 0.0) for i in range(45)]
    out, fixed = smooth_jumps(ball, times)
    assert fixed == 1
    speeds = [math.dist(a, b) / (tb - ta) for a, b, ta, tb in zip(out, out[1:], times, times[1:])]
    assert max(speeds) < JUMP_MPS
    assert max(speeds) > GLIDE_MPS * 0.8  # still covers the distance, just not instantly
    assert out[0] == ball[0] and out[-1] == ball[-1]


def test_smooth_jumps_leaves_normal_play_alone():
    from cut_clip import smooth_jumps
    times = [i / 30 for i in range(60)]
    ball = [(i * 0.9, 0.0, 0.3) for i in range(60)]  # a 27 m/s shot
    assert smooth_jumps(ball, times) == (ball, 0)


def test_a_slow_tracked_shot_is_retimed_to_a_real_shot_speed():
    from cut_clip import retime_shot
    times = [i / 30 for i in range(120)]
    # Shot at frame 10 from 18 m out, tracked gliding to the line in 2 s (9 m/s), then 1 m into the net.
    ball = [(34.5, 0.0, 0.1)] * 11 + [(34.5 + 18 * (k / 60), 0.0, 1.0) for k in range(1, 61)] + \
           [(52.5 + min((k + 1) / 20, 1.0), 0.0, 1.0) for k in range(49)]
    out, cross = retime_shot(ball, times, 10, 70, "R")
    assert abs((times[cross] - times[10]) - 0.9) < 0.05  # 18 m at 20 m/s
    assert out[cross] == ball[70]  # still crosses where it did
    assert out[cross + 5] == ball[75]  # and the ball in the net plays on from there
    # A header is allowed to be slower; a shot that's already fast isn't touched.
    assert retime_shot(ball, times, 10, 70, "H")[1] < 70
    fast = ball[:11] + [(34.5 + 18 * (k / 20), 0.0, 1.0) for k in range(1, 21)] + ball[71:]
    assert retime_shot(fast, times, 10, 30, "R")[1] == 30
