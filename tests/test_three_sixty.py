import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import three_sixty as ts
from identity import blend_swap, keeps_shot

PLAYERS = {f"h{i}": {"team": "home", "position": "CM"} for i in range(8)}
PLAYERS |= {f"a{i}": {"team": "away", "position": "CM"} for i in range(8)}
SPOTS = {pid: ((i % 8) * 6.0 - 20, (i // 8) * 15.0 - 7) for i, pid in enumerate(sorted(PLAYERS))}


def test_hungarian_finds_the_cheapest_assignment():
    cost = [[4, 1, 3], [2, 0, 5]]
    assert ts.hungarian(cost) == [1, 0]  # 1 + 2 beats 0 + 4 and the rest


def test_match_frame_undoes_a_whole_frame_shift():
    # The 360 frame is every tracked player moved 4 m in y (the camera's fit):
    # everyone is matched to himself, the shift found.
    frame = {"points": [(PLAYERS[p]["team"], (x, y + 4.0), None) for p, (x, y) in SPOTS.items()]}
    m = ts.match_frame(frame, dict(SPOTS), PLAYERS)
    assert all(math.dist(m[p], SPOTS[p]) < 0.01 for p in SPOTS)
    assert math.isclose(frame["shift"][1], -4.0, abs_tol=0.01)


def test_correct_tracks_moves_a_track_that_is_off_in_several_frames():
    n = 90
    times = [k / 30 for k in range(n)]
    frames = [{p: xy for p, xy in SPOTS.items()} for _ in range(n)]
    for f in frames:
        f["h0"] = (SPOTS["h0"][0] + 6.0, SPOTS["h0"][1])  # PFF has him 6 m off throughout
    views = [{"f": k, "actor": "a1", "points": [(PLAYERS[p]["team"], SPOTS[p], "a1" if p == "a1" else None)
                                                for p in SPOTS]} for k in (15, 45, 75)]
    moved = ts.correct_tracks(views, frames, times, PLAYERS)
    assert set(moved) == {"h0"}
    assert math.dist(frames[45]["h0"], SPOTS["h0"]) < 0.5


def test_one_off_frame_does_not_move_a_moving_track():
    n = 90
    times = [k / 30 for k in range(n)]
    frames = [{p: (xy[0] + k * 0.1, xy[1]) for p, xy in SPOTS.items()} for k in range(n)]  # all running 3 m/s
    views = []
    for k in (15, 45, 75):
        pts = [(PLAYERS[p]["team"], frames[k][p], None) for p in SPOTS]
        if k == 45:  # h0 once 6 m off
            pts = [(s, (xy[0] + 6.0, xy[1]) if xy == frames[k]["h0"] else xy, n_) for s, xy, n_ in pts]
        views.append({"f": k, "actor": "a1", "points": pts})
    assert ts.correct_tracks(views, frames, times, PLAYERS) == {}


def test_fix_labels_swaps_a_named_player_onto_the_track_at_his_spot():
    n = 60
    times = [k / 30 for k in range(n)]
    # PFF has h0 and h1 the wrong way round the whole clip (never close to each other).
    frames = [{**SPOTS, "h0": SPOTS["h1"], "h1": SPOTS["h0"]} for _ in range(n)]
    views = [{"f": k, "actor": "h0", "points": [(PLAYERS[p]["team"], SPOTS[p], p if p in ("h0", "h1") else None)
                                                for p in SPOTS]} for k in (10, 30, 50)]
    swaps = ts.fix_labels(views, frames, times, PLAYERS)
    assert [(a, b) for a, b, *_ in swaps] == [("h0", "h1")]
    assert frames[30]["h0"] == SPOTS["h0"]


def test_no_swap_takes_the_scorer_off_the_shot():
    frames = [{"s": (0.0, 0.0), "t": (10.0, 0.0)} for _ in range(5)]
    assert not keeps_shot(frames, "s", "t", 0, 4, ("s", 2, (0.0, 0.0)))
    assert keeps_shot(frames, "s", "t", 0, 4, ("s", 2, (10.0, 0.0)))  # the swap brings him to it
    assert keeps_shot(frames, "s", "t", 3, 4, ("s", 2, (0.0, 0.0)))  # the kick is outside the stretch


def test_blend_swap_leaves_no_jump_in_speed():
    # Two players meet: a runs at 7 m/s, b stands. Swapped from frame 30, a
    # takes over b's standing track: without the blend his speed drops to 0 at once.
    n = 90
    times = [k / 30 for k in range(n)]
    frames = [{"a": (k * 7 / 30, 0.0), "b": (30 * 7 / 30 + 0.5, 0.0)} for k in range(n)]
    for k in range(30, n):
        frames[k]["a"], frames[k]["b"] = frames[k]["b"], frames[k]["a"]
    blend_swap(frames, times, "a", "b", 30, n - 1)
    worst = 0.0
    for k in range(6, n - 6):
        v1 = (frames[k]["a"][0] - frames[k - 6]["a"][0]) / 0.2
        v2 = (frames[k + 6]["a"][0] - frames[k]["a"][0]) / 0.2
        worst = max(worst, abs(v2 - v1) / 0.2)
    assert worst < 12.0
