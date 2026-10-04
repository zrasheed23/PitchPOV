import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from volleys import shot_pose

DT = 1 / 30
TIMES = [i * DT for i in range(60)]


def _setup(z, shot_dir):
    # He stands at (40, 0) facing +y (toward where the ball came from); the shot goes along shot_dir.
    ball = [(40.0, 5.0 * (1 - k / 30), z) for k in range(31)]
    ball += [(40.0 + shot_dir[0] * 0.8 * k, shot_dir[1] * 0.8 * k, z) for k in range(1, 30)]
    return ball, [{"a": (40.0, 0.0)} for _ in TIMES]


def test_a_high_volley_side_on_is_a_scissor_kick():
    ball, players = _setup(1.1, (1, 0))
    shot = {"f": 30, "p": "a", "b": "R"}
    assert shot_pose(shot, "Volley", ball, TIMES, players, ball)[0] == "scissor" and shot["v"] == "scissor"


def test_a_low_or_square_volley_stays_on_the_ground():
    ball, players = _setup(0.6, (1, 0))
    assert shot_pose({"f": 30, "p": "a", "b": "R"}, "Volley", ball, TIMES, players, ball)[0] == "volley"
    ball, players = _setup(1.1, (0, -1))  # straight back where he's facing: not side-on
    assert shot_pose({"f": 30, "p": "a", "b": "R"}, "Volley", ball, TIMES, players, ball)[0] == "volley"


def test_an_acrobatic_pose_needs_a_measured_height_a_foot_can_reach():
    # Side-on, and the clip's ball at 1.1 m, but the feed measured it at 4.3 m
    # (no foot is there: the feed's height is wrong) or not at all: a volley.
    ball, players = _setup(1.1, (1, 0))
    feed = [(x, y, 4.3) for x, y, _ in ball]
    assert shot_pose({"f": 30, "p": "a", "b": "R"}, "Volley", ball, TIMES, players, feed)[0] == "volley"
    assert shot_pose({"f": 30, "p": "a", "b": "R"}, "Volley", ball, TIMES, players, [None] * len(ball))[0] == "volley"
    assert shot_pose({"f": 30, "p": "a", "b": "R"}, "Volley", ball, TIMES, players)[0] == "volley"
    # One measured sample near the contact is enough.
    sparse = [b if k == 31 else None for k, b in enumerate(ball)]
    assert shot_pose({"f": 30, "p": "a", "b": "R"}, "Volley", ball, TIMES, players, sparse)[0] == "scissor"


def test_only_statsbomb_decides():
    ball, players = _setup(1.4, (1, 0))
    for technique, pose in (("Normal", None), ("Lob", None), ("Diving Header", None), ("Half Volley", "half"),
                            ("Overhead Kick", "bicycle"), (None, None)):
        shot = {"f": 30, "p": "a", "b": "R"}
        assert shot_pose(shot, technique, ball, TIMES, players, ball)[0] == pose
        assert shot.get("v") == pose


def test_moving_the_contact_a_couple_of_frames_does_not_flip_the_pose():
    # A side-on volley met at 1.2 m, the ball dropping ~0.13 m a frame as it
    # arrives: the measured height near the contact decides, by majority over
    # +-2 frames, for kick frames from one early to two late.
    ball = [(40.0, 5.0 * (1 - k / 30), 1.2 + 0.135 * (30 - k)) for k in range(31)]
    ball += [(40.0 + 0.8 * k, 0.0, 1.2) for k in range(1, 30)]
    players = [{"a": (40.0, 0.0)} for _ in TIMES]
    for f in (29, 30, 31, 32):
        shot = {"f": f, "p": "a", "b": "R"}
        assert shot_pose(shot, "Volley", ball, TIMES, players, ball)[0] == "scissor", f
