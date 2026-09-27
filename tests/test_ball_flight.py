import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from ball_flight import count_kinks, straighten_free_flight

T = [i / 30 for i in range(60)]
# A passes from (0, 0) at frame 0 to B standing at (29.8, 0); the tracking bends the ball 1.5 m sideways mid-flight.
BALL = [(i * 0.5, 1.5 if 20 < i < 35 else 0.0, 0.1) for i in range(60)]
PLAYERS = [{"A": (0.0, 0.0) if i < 5 else (-5.0, 0.0), "B": (29.8, 0.0)} for i in range(60)]


def test_a_ball_in_flight_travels_straight_between_touches():
    assert count_kinks(BALL, T, PLAYERS, [0]) > 0
    out, n = straighten_free_flight(BALL, PLAYERS, [0, 59], end=59)
    assert n == 1
    assert count_kinks(out, T, PLAYERS, [0, 59]) == 0
    assert all(abs(b[1]) < 1e-9 for b in out)  # on the line from A to B
    assert out[0] == BALL[0] and out[59] == BALL[59]  # touches untouched


def test_height_is_kept_so_bounces_and_lofts_survive():
    lofted = [(x, y, 5.0 if 10 < i < 50 else z) for i, (x, y, z) in enumerate(BALL)]
    out, _ = straighten_free_flight(lofted, PLAYERS, [0, 59], end=59)
    assert [b[2] for b in out] == [b[2] for b in lofted]


def test_nothing_after_the_shot_changes():
    out, n = straighten_free_flight(BALL, PLAYERS, [0], end=10)
    assert out[30] == BALL[30]
