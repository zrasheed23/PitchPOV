import bz2
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from cut_clip import clamp_z, fill_gaps, read_windows, to_standard_pitch


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
    windows = read_windows(path, [111, 222, 333], before_s=3, after_s=2)
    assert set(windows) == {111, 222}

    frames, goal_index = windows[111]
    assert frames[goal_index]["frameNum"] == 50
    assert frames[0]["frameNum"] == 20 and frames[-1]["frameNum"] == 70

    frames, goal_index = windows[222]
    assert frames[goal_index]["frameNum"] == 120
    assert frames[0]["frameNum"] == 90 and frames[-1]["frameNum"] == 140
