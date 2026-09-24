import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from cut_clip import clamp_z, fill_gaps, to_standard_pitch


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
