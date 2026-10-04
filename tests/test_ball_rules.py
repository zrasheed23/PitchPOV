import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from ball_rules import (BLEND_TOP_MPS, FOOT_M, _accel, clamped_spline, close_touch_gaps, enforce_touch_rule, find_kick, fly_shot,
                        limit_player_accels)
from touch_rule import violations

DT = 1 / 30
TIMES = [i * DT for i in range(120)]


def _straight(a, b, n):
    return [tuple(p + (q - p) * k / (n - 1) for p, q in zip(a, b)) for k in range(n)]


def test_a_turn_with_nobody_near_is_found_then_flown_straight():
    # 0 -> 60: a ground pass that bends 40 degrees at frame 30 with no one there.
    ball = _straight((0, 0, 0), (9, 0, 0), 31)[:-1] + _straight((9, 0, 0), (15, 5, 0), 30)
    ball += [ball[-1]] * (120 - len(ball))
    players = [{"a": (-0.5, 0.0), "b": (15.4, 5.0), "far": (5.0, 20.0)} for _ in TIMES]
    contacts = [{"f": 0, "p": "a", "b": "R"}, {"f": 59, "p": "b", "b": "R"}]
    assert (30, "turn") in violations(ball, TIMES, contacts, players, 59, end=59)
    out, contacts, counts = enforce_touch_rule(ball, TIMES, players, contacts, [], 59, {})
    assert counts["joined"] == 1 and counts["touches_added"] == 0
    assert violations(out, TIMES, contacts, players, 59, end=59) == []
    # Straight on the ground from the first touch to the second (to the stored 2 decimals).
    length = math.dist(out[0][:2], out[59][:2])
    for k in range(1, 59):
        cross = (out[k][0] - out[0][0]) * (out[59][1] - out[0][1]) - (out[k][1] - out[0][1]) * (out[59][0] - out[0][0])
        assert abs(cross) / length < 0.01 and out[k][2] == 0


def test_a_turn_next_to_a_player_becomes_his_touch():
    ball = _straight((0, 0, 0), (9, 0, 0), 31)[:-1] + _straight((9, 0, 0), (15, 5, 0), 30)
    ball += [ball[-1]] * (120 - len(ball))
    players = [{"a": (-0.5, 0.0), "b": (15.4, 5.0), "c": (9.0, 1.8)} for _ in TIMES]
    contacts = [{"f": 0, "p": "a", "b": "R"}, {"f": 59, "p": "b", "b": "R"}]
    out, contacts, counts = enforce_touch_rule(ball, TIMES, players, contacts, [], 59, {})
    added = [c for c in contacts if c.get("s") == 2]
    assert [c["p"] for c in added] == ["c"] and counts["touches_added"] == 1
    f = added[0]["f"]
    assert math.dist(out[f][:2], players[f]["c"]) < 0.5  # at his foot
    assert violations(out, TIMES, contacts, players, 59, end=59) == []


def test_a_ball_rising_by_itself_is_a_lift():
    ball = [(k * 0.3, 0.0, 0.0 if k < 30 else 0.1 * (k - 29)) for k in range(59)]
    ball += [ball[-1]] * (120 - len(ball))
    players = [{"a": (-0.5, 0.0)} for _ in TIMES]
    kinds = {k for _, k in violations(ball, TIMES, [{"f": 0, "p": "a", "b": "R"}], players, 58, end=58)}
    assert "lift" in kinds


def test_a_late_logged_shot_is_moved_back_to_where_the_ball_left_his_foot():
    # The ball leaves the shooter at frame 40 and is 6 m away by the logged frame 50.
    ball = [(0.3, 0.0, 0.1)] * 41 + [(0.3 + 0.6 * k, 0.0, 0.1) for k in range(1, 80)]
    players = [{"s": (0.0, 0.0)} for _ in TIMES]
    assert find_kick(ball, TIMES, players, "s", 50) == 42  # last frame within 1.5 m


def test_the_shot_flies_straight_from_his_foot_to_the_aimed_crossing():
    # Tracked ball 2 m from the shooter at the kick, bending on its way to the line.
    ball = [(30.0 + 0.8 * k, 2.0 + 0.02 * k * k, 0.5) for k in range(40)]
    ball += [(52.5 + 0.5 * k, 1.0, 0.5) for k in range(1, 81)]
    ball[39] = (52.0, 1.0, 0.5)
    players = [{"s": (29.0, 0.5)} for _ in TIMES]
    out, info = fly_shot(ball, TIMES, players, 0, "s", "R", 1)
    assert info["shot"] and 20 <= info["speed"] <= 35
    assert math.dist(out[0][:2], (29.0, 0.5)) < 0.5  # at his foot
    cross = next(k for k in range(1, 120) if out[k][0] >= 52.5)
    a, b = out[0], out[cross - 1]
    for k in range(1, cross):  # every frame on the line from the foot to the crossing
        p = out[k]
        assert abs((p[0] - a[0]) * (b[1] - a[1]) - (p[1] - a[1]) * (b[0] - a[0])) < 1e-6
    assert violations(out, TIMES, [{"f": 0, "p": "s", "b": "R"}], players, 0) == []


def test_a_shooter_far_from_the_ball_is_moved_onto_it():
    ball = [(40.0 + 0.7 * k, 0.0, 0.1) for k in range(20)] + [(54.0, 0.0, 0.0)] * 100
    players = [{"s": (30.0, 10.0)} for _ in TIMES]
    out, info = fly_shot(ball, TIMES, players, 0, "s", "R", 1)
    assert info["shooter_moved"] > 10
    assert math.dist(players[0]["s"], out[0][:2]) < 0.5
    # Eased back toward his track afterwards, never accelerating harder than EASE_ACCEL
    # (his track stands still, so all his motion is the correction).
    from ball_rules import EASE_ACCEL
    assert math.dist(players[119]["s"], (30.0, 10.0)) < math.dist(players[60]["s"], (30.0, 10.0))
    dt = TIMES[1] - TIMES[0]
    acc = max(math.dist((2 * players[k]["s"][0] - players[k - 1]["s"][0] - players[k + 1]["s"][0],
                         2 * players[k]["s"][1] - players[k - 1]["s"][1] - players[k + 1]["s"][1]), (0, 0)) / dt ** 2
              for k in range(1, 119) if k != 0)
    assert acc <= EASE_ACCEL * 1.05


def test_a_mixed_up_identity_is_swapped_not_teleported():
    from identity import try_swap
    players = {"a": {"team": "home", "position": "CB"}, "b": {"team": "home", "position": "CM"},
               "o": {"team": "away", "position": "CF"}}
    # PFF has "a" 20 m from the ball the whole clip, "b" at it; StatsBomb has "a" on the ball twice.
    frames = [{"a": (0.0, 20.0), "b": (10.0 + 0.1 * k, 0.0), "o": (30.0, 0.0)} for k in range(100)]
    events = [{"f": 20, "p": "a", "xy": (12.0, 0.0)}, {"f": 80, "p": "a", "xy": (18.0, 0.0)}]
    swap = try_swap(frames, players, "a", 50, (15.0, 0.0), events)
    assert swap == ("b", 0, 99)
    assert frames[50]["a"] == (15.0, 0.0) and frames[50]["b"] == (0.0, 20.0)


def test_no_swap_when_statsbomb_fits_the_tracks_as_they_are():
    from identity import try_swap
    players = {"a": {"team": "home", "position": "CB"}, "b": {"team": "home", "position": "CM"}}
    frames = [{"a": (0.0, 20.0), "b": (15.0, 0.0)} for _ in range(100)]
    events = [{"f": 10, "p": "a", "xy": (0.0, 20.0)}, {"f": 90, "p": "a", "xy": (0.0, 20.0)},
              {"f": 30, "p": "b", "xy": (15.0, 0.0)}, {"f": 70, "p": "b", "xy": (15.0, 0.0)}]
    assert try_swap(frames, players, "a", 50, (15.0, 0.0), events) is None


def test_after_the_goal_a_correction_fades_instead_of_snapping_back():
    from ball_rules import SETTLE_MPS, settle_after_goal
    times = [k / 30 for k in range(120)]
    reference = [{"gk": (51.0, 3.0)} for _ in times]
    # Held 3 m off his track until frame 60, then (as a correction ending would) straight back on it.
    frames = [{"gk": (51.0, 0.0) if k < 60 else (51.0, 3.0)} for k in range(120)]
    settle_after_goal(frames, reference, times, 30, hold={"gk": 59})
    ys = [f["gk"][1] for f in frames]
    speeds = [abs(b - a) / (1 / 30) for a, b in zip(ys, ys[1:])]
    assert ys[59] == 0.0 and ys[119] < 3.0
    assert speeds[59] < 0.1  # held still, he sets off gently (SETTLE_RAMP_S)
    assert max(speeds) <= 2.5 * SETTLE_MPS


def test_a_player_the_tracked_ball_shows_to_be_elsewhere_never_pulls_the_ball_to_him():
    from ball_rules import meet_touches
    times = [k / 30 for k in range(100)]
    ball = [(-43.0, -15.0, 0.0)] * 100
    frames = [{"a": (6.0, 7.0)} for _ in times]  # PFF has him 50 m away all clip
    tracked = {"f": 20, "p": "a", "b": "F", "sb": "Ball Receipt*", "tr": 1}
    placed = {"f": 70, "p": "a", "b": "R", "sb": "Pass"}  # only StatsBomb puts the ball here
    _, too_far, _ = meet_touches(ball, times, frames, [placed, tracked], 99)
    assert placed in too_far and tracked in too_far
    _, too_far, _ = meet_touches(ball, times, frames, [placed], 99)
    assert not too_far  # on its own, the touch rule may still take the ball to him


def test_clamped_spline_goes_through_its_points_with_the_given_end_slopes():
    f = clamped_spline([0.0, 1.0, 2.0], [0.0, 1.0, 0.0], 1.0, -1.0)
    assert all(math.isclose(f(x), y, abs_tol=1e-9) for x, y in ((0.0, 0.0), (1.0, 1.0), (2.0, 0.0)))
    assert math.isclose((f(1e-4) - f(0.0)) / 1e-4, 1.0, abs_tol=1e-3)


def test_limit_player_accels_smooths_a_correction_jolt_but_keeps_touches():
    # PFF has him jogging; a correction jumps 1.5 m over three frames at frame 60,
    # and frame 90 is a touch (held where it is).
    ref = [{"p": (3.0 * t, 0.0)} for t in TIMES]
    frames = [{"p": (3.0 * t, 0.0 if k < 60 else 1.5)} for k, t in enumerate(TIMES)]
    for k in (59, 60, 61):
        frames[k]["p"] = (3.0 * TIMES[k], 0.5 * (k - 58))
    touch = frames[90]["p"]
    limit_player_accels(frames, ref, TIMES, {"p": {90}})
    track = [f["p"] for f in frames]
    assert max(_accel(track, TIMES, k) for k in range(6, len(TIMES) - 6)) <= 10.0
    assert frames[90]["p"] == touch


def test_a_pass_arriving_behind_him_is_met_at_his_foot_not_pulled_onto_it():
    # He runs at 8 m/s; the pass reaches the touch 1.1 m behind him. He's eased
    # back onto it (no faster than BLEND_TOP_MPS), the ball stays where it is.
    ball = [(8.0 * t - 1.1, 0.0, 0.0) for t in TIMES]
    frames = [{"p": (8.0 * t, 0.0)} for t in TIMES]
    contacts = [{"f": 90, "p": "p", "b": "F"}]
    moved, left = close_touch_gaps(ball, TIMES, frames, contacts, [], len(TIMES) - 1)
    assert left == 0 and moved
    assert abs(math.dist(ball[90][:2], frames[90]["p"]) - FOOT_M) < 0.01
    speeds = [math.dist(frames[k]["p"], frames[k - 1]["p"]) / (TIMES[k] - TIMES[k - 1]) for k in range(1, len(TIMES))]
    assert max(speeds) <= BLEND_TOP_MPS + 0.1
