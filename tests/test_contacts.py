import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from contacts import find_contacts


def ev(kind, t, pid, body=None, initial=None):
    e = {"eventTime": t, "gameEvents": {"playerId": pid}, "possessionEvents": {"possessionEventType": kind, "bodyType": body}}
    if initial:
        e["initialTouch"] = {"initialBodyType": initial}
    return e


def test_find_contacts_maps_events_to_frames_and_body_parts():
    frame_ms = [1000 + i * 100 for i in range(50)]  # 10 fps from t = 1 s
    events = [
        ev("IT", 1.5, 7, initial="L"),  # first touch with the left foot
        ev("PA", 2.0, 7, body="R"),  # pass with the right
        ev("CH", 2.2, 9),  # challenge: not a touch
        ev("SH", 3.0, 9, body="HE"),  # header
        ev("PA", 9.0, 7, body="R"),  # outside the clip
        ev("PA", 2.5, 99, body="R"),  # player not in the clip
    ]
    assert find_contacts(events, frame_ms, {"7", "9"}) == [
        {"f": 5, "p": "7", "b": "L"},
        {"f": 10, "p": "7", "b": "R"},
        {"f": 20, "p": "9", "b": "H"},
    ]


from contacts import align_contacts

T = [i / 30 for i in range(60)]


def setup():
    # A stands at (0, 0) and shoots at frame 30: the ball sits at his feet, then flies off along x.
    players = [{"A": (0.0, 0.0), "B": (20.0, 5.0)} for _ in T]
    ball = [(0.5, 0.0, 0.1) if i <= 30 else (0.5 + (i - 30) * 1.2, 0.0, 0.3) for i in range(60)]
    return players, ball, [True] * 60


def test_a_shot_logged_late_is_moved_back_to_when_the_ball_left_his_foot():
    players, ball, tracked = setup()
    out, _, n = align_contacts([{"f": 36, "p": "A", "b": "R"}], ball, tracked, players, T, goal_index=40)
    assert out == [{"f": 30, "p": "A", "b": "R"}] and n["moved"] == 1


def test_touches_after_the_shot_are_dropped_and_a_late_shot_goes_to_the_shot_frame():
    players, ball, tracked = setup()
    out, _, n = align_contacts([{"f": 50, "p": "A", "b": "R"}], ball, tracked, players, T, goal_index=30)
    assert out == [{"f": 30, "p": "A", "b": "R"}] and n["after_shot"] == 1


def test_a_touch_in_a_tracking_gap_puts_the_ball_at_his_feet():
    players, ball, tracked = setup()
    tracked = [not (15 <= i <= 25) for i in range(60)]
    ball[20] = (0.0, 2.0, 0.1)  # gap-filled, 3 m from B
    out, ball2, n = align_contacts([{"f": 20, "p": "B", "b": "H"}], ball, tracked, [{**p, "B": (0.0, 5.0)} for p in players], T, 40)
    assert out and n["placed"] == 1
    assert abs(ball2[20][1] - 4.6) < 1e-9 and ball2[20][2] == 1.9  # at his head, on the ball's side


def test_a_touch_with_the_tracked_ball_somewhere_else_is_dropped():
    players, ball, tracked = setup()
    out, _, n = align_contacts([{"f": 10, "p": "B", "b": "R"}], ball, tracked, players, T, 40)
    assert out == [] and n["dropped"] == 1
