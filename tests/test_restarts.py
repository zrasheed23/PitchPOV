import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from restarts import HOLD_Z, apply_restarts, find_restarts

DT = 1 / 30
TIMES = [i * DT for i in range(300)]
MS = [t * 1000 + 10_000 for t in TIMES]


def _event(t, kind=None, out=None, pid=7, body=None):
    return {"eventTime": 10 + t, "gameEvents": {"setpieceType": kind or "O", "playerId": pid,
                                                "gameEventType": "OUT" if out else "OTB", "outType": out},
            "possessionEvents": {"possessionEventType": "PA", "bodyType": body}}


def test_restarts_are_found_with_the_out_before_them():
    events = [_event(1.0, out="T"), _event(6.0, "T", body="TWOHANDS"), _event(7.0), _event(8.0, "C", pid=9)]
    found = find_restarts(events, MS, 280, {"7", "9"})
    assert [(r["type"], r["f"], r["p"], r["b"], r["out"]) for r in found] == [
        ("T", 180, "7", "X", 30), ("C", 240, "9", "F", None)]


def test_a_throw_in_stops_out_of_play_and_is_held_over_his_head():
    # The ball goes over the touchline at frame 30 moving at 6 m/s, then the
    # tracking drifts it back onto the pitch; the throw is at frame 180.
    ball = [(0.0, 30.0 + 0.2 * k, 0.0) for k in range(31)] + [(0.0, 36.0 - 0.05 * k, 0.0) for k in range(1, 270)]
    players = [{"7": (1.0, 33.0), "8": (0.0, 20.0)} for _ in TIMES]
    restarts = [{"type": "T", "f": 180, "p": "7", "b": "X", "out": 30}]
    info, dead = apply_restarts(ball, TIMES, players, restarts, lambda f: 250)
    assert dead == [(30, 180)]
    assert all(b is None or abs(b[1]) >= 34 for b in ball[30:181])  # never back on the pitch by itself
    hold = info[0]["hold"]
    assert TIMES[180] - TIMES[hold[0]] >= 0.99
    for k in range(hold[0], 181):
        assert ball[k][2] == HOLD_Z and ball[k][:2] == players[k]["7"]  # over his head
    assert abs(players[180]["7"][1]) > 34  # behind the line


def test_a_corner_is_placed_still_on_the_arc_with_the_taker_at_it():
    ball = [(40.0 + 0.1 * k, -20.0, 0.0) for k in range(300)]
    players = [{"9": (50.0, -30.0), "8": (45.0, 0.0)} for _ in TIMES]
    info, dead = apply_restarts(ball, TIMES, players, [{"type": "C", "f": 120, "p": "9", "b": "R", "out": None}],
                                lambda f: 200)
    assert dead == [(0, 120)]
    assert all(b == (52.25, -33.75, 0.0) for b in ball[:121])
    x, y = players[120]["9"]
    assert abs(x - 52.25) < 0.5 and abs(y + 33.75) < 0.5
