import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from quality import check_quality, score

DT = 1 / 30
N = 90
KICK = 60


def _clip(ball=None, tracks=None, **extra):
    """A clip with the ball flying from (40, 0) at the kick into the goal at x = 52.5."""
    ball = ball or [(40.0, 0.0, 0.1)] * (KICK + 1) + [(40.0 + 0.6 * k, 0.0, 0.3) for k in range(1, N - KICK)]
    tracks = tracks or {"s": [(39.7, 0.0)] * N, "gk": [(51.5, 2.0)] * N, "d": [(45.0, -5.0)] * N}
    players = [{"id": "s", "name": "Shooter", "team": "home", "position": "CF"},
               {"id": "gk", "name": "Keeper", "team": "away", "position": "GK"},
               {"id": "d", "name": "Defender", "team": "away", "position": "CB"}]
    frames = [{"t": round(k * DT, 3), "b": list(ball[k]), "p": {pid: list(t[k]) for pid, t in tracks.items()}}
              for k in range(N)]
    return {"frames": frames, "players": players, "kickFrame": KICK, "goalFrame": KICK, "scorerId": "s",
            "contacts": [{"f": KICK, "p": "s", "b": "R"}], "keeperDive": None, "shotPose": "none"} | extra


def _goal(xy=(40.0, 0.0), ff=()):
    return {"type": "Shot", "p": "s", "f": KICK, "xy": xy, "on_ball": True, "ff": list(ff),
            "raw": {"shot": {"outcome": {"name": "Goal"}}}}


def test_a_clean_clip_passes():
    q = check_quality(_clip(), [_goal(ff=[("gk", "away", (51.6, 2.1), True), ("d", "away", (45.5, -5.0), False)])],
                      {"y": 0.0, "z": 0.3})
    assert not any(q["checks"].values()) and q["score"] == 0
    assert q["freeze"]["players"] == 2 and q["freeze"]["max"] < 1


def test_shot_origin_and_end_location_are_measured_against_statsbomb():
    q = check_quality(_clip(), [_goal(xy=(49.0, 3.0))], {"y": 2.0, "z": 0.3})
    assert len(q["checks"]["shot origin"]) == 2  # the ball and the shooter
    assert q["checks"]["end location"]


def test_freeze_frame_distance_and_wrong_labels():
    # StatsBomb's defender stands where the keeper's track is; the keeper 8 m out.
    q = check_quality(_clip(), [_goal(ff=[("d", "away", (51.4, 2.0), False), ("gk", "away", (43.0, 9.0), True)])])
    assert q["checks"]["freeze frame"]  # max off
    assert any("Defender" in t for t in q["checks"]["wrong label"])


def test_touches_need_a_statsbomb_event_by_the_same_player():
    clip = _clip()
    clip["contacts"].insert(0, {"f": 20, "p": "d", "b": "F"})
    q = check_quality(clip, [_goal(), {"type": "Pass", "p": "s", "f": 20, "xy": (40.0, 0.0), "on_ball": True,
                                       "ff": [], "raw": {}}])
    assert q["checks"]["event sequence"]


def test_people_do_not_teleport_or_accelerate_like_rockets():
    tracks = {"s": [(39.7, 0.0)] * N, "gk": [(51.5, 2.0)] * 40 + [(51.5, 3.0)] * (N - 40), "d": [(45.0, -5.0)] * N}
    q = check_quality(_clip(tracks=tracks))
    assert q["checks"]["teleport"] and q["checks"]["acceleration"]


def test_a_beaten_keeper_is_not_in_the_path_and_acrobatics_are_flagged():
    dive = {"keeper": "gk", "kind": "block", "through": "side", "gap": 0.4, "height": 1.2}
    q = check_quality(_clip(keeperDive=dive, shotPose="scissor"))
    assert q["checks"]["keeper in path"] and q["checks"]["acrobatic"]
    assert not check_quality(_clip(keeperDive=dict(dive, gap=0.8)))["checks"]["keeper in path"]


def test_score_weights_the_worst_finding_per_check():
    assert score({"acrobatic": [(1.0, "x")]}) == 1.0
    assert score({"shot origin": [(5.0, "x"), (1.0, "y")]}) == 3.0 * (3.0 + 0.25)
