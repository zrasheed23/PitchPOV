import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from shot_placement import BAR_Z, POST_Y, YARD, to_pitch


def test_centre_of_the_goal_is_y_zero_at_either_end():
    assert to_pitch([120, 40, 1.0], 1) == (pytest.approx(0.0), pytest.approx(YARD))
    assert to_pitch([120, 40, 1.0], -1)[0] == pytest.approx(0.0)


def test_attackers_right_is_minus_y_attacking_plus_x_and_plus_y_attacking_minus_x():
    # Mbappé's volley in the final: 1.5 yd to the attacker's right of centre, low.
    y, z = to_pitch([120, 41.5, 0.3], 1)
    assert y == pytest.approx(-1.5 * YARD) and z == pytest.approx(0.3 * YARD)
    assert to_pitch([120, 41.5, 0.3], -1)[0] == pytest.approx(1.5 * YARD)
    assert to_pitch([120, 37.0, 0.3], 1)[0] == pytest.approx(3.0 * YARD)  # attacker's left


def test_kept_inside_the_posts_and_under_the_bar():
    assert to_pitch([120, 44.5, 3.0], 1) == (pytest.approx(-POST_Y), pytest.approx(BAR_Z))
    assert to_pitch([120, 35.0, -0.2], 1) == (pytest.approx(POST_Y), 0.0)


def test_no_height_gives_no_z():
    assert to_pitch([120, 40], 1)[1] is None
