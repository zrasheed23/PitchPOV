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
