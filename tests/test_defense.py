import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from defense import plan_defense

TIMES = [k / 30 for k in range(60)]
PLAYERS = {"d": {"team": "home"}, "a": {"team": "away"}}


def _event(kind, f=30, extra=None):
    raw = {"type": {"name": kind}} | (extra or {})
    return {"type": kind, "f": f, "p": "d", "b": "R", "xy": (10.0, 0.0), "on_ball": kind != "Duel", "raw": raw}


def _run(events, gap, speed, z=0.1):
    # The defender runs along x at `speed`, `gap` metres short of the ball at frame 30.
    ref = [{"d": (10.0 - gap + speed * (t - TIMES[30]), 0.0), "a": (20.0, 0.0)} for t in TIMES]
    ball = [(10.0, 0.0, z)] * len(TIMES)
    return plan_defense(events, [], ball, [True] * len(TIMES), TIMES, ref, PLAYERS, 59)


def test_a_low_ball_out_of_reach_at_a_run_is_a_slide():
    assert [d["kind"] for d in _run([_event("Clearance")], gap=1.4, speed=5.0)] == ["slide"]


def test_a_clearance_on_his_feet_is_just_a_kick():
    assert _run([_event("Clearance")], gap=0.4, speed=1.0) == []


def test_blocks_slide_or_stand():
    assert _run([_event("Block")], gap=2.0, speed=0.5)[0]["kind"] == "slideBlock"
    assert _run([_event("Block")], gap=0.5, speed=5.0)[0]["kind"] == "block"
    assert _run([_event("Block")], gap=2.0, speed=5.0, z=1.0)[0]["kind"] == "block"  # a high ball: on his feet


def test_a_tackle_lost_or_followed_by_the_other_team_failed():
    tackle = _event("Duel", extra={"duel": {"type": {"name": "Tackle"}, "outcome": {"name": "Lost In Play"}}})
    assert _run([tackle], gap=2.0, speed=5.0)[0]["ok"] is False
    shot = {"type": "Shot", "f": 45, "p": "a", "b": "R", "xy": (15.0, 0.0), "on_ball": True, "raw": {}}
    assert _run([_event("Block"), shot], gap=2.0, speed=5.0)[0]["ok"] is False
    assert _run([_event("Duel", extra={"duel": {"type": {"name": "Aerial Lost"}}})], gap=2.0, speed=5.0) == []
