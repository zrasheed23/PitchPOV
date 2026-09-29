"""PFF identity mix-ups: two teammates' tracks carry each other's names.

When a touch's player (StatsBomb or PFF names him) is far from a well-supported
touch while a teammate is right at it, PFF has most likely given the two
players each other's tracks for a while. Moving the named player tens of
metres would be a teleport; swapping the two identities is what happened.

The swap covers the stretch around the touch bounded by where the two tracks
come within CLOSE_M of each other (where a tracker can mix them up), or the
clip's ends, and is only made if StatsBomb's events for the two players in
that stretch fit the swapped tracks better.
"""

import math

AT_BALL_M = 3.5  # a teammate this close to the touch may be the one on the ball (StatsBomb decides)
CLOSE_M = 2.0  # tracks this close can have been mixed up here
MIN_GAIN_M = 1.0  # StatsBomb's events must fit the swap better by at least this much in total


def _stretch(player_frames, a_id, b_id, f):
    """Frames around f bounded by where the two tracks come within CLOSE_M."""
    n = len(player_frames)

    def close(k):
        a, b = player_frames[k].get(a_id), player_frames[k].get(b_id)
        return a is not None and b is not None and math.dist(a, b) <= CLOSE_M

    lo = next((k for k in range(f, -1, -1) if close(k)), 0)
    hi = next((k for k in range(f, n) if close(k)), n - 1)
    return lo, hi


def _fit(player_frames, events, ids, lo, hi, swapped):
    """Total distance from StatsBomb's events by these players (in lo..hi) to
    the track carrying each one's name (or the other's, if swapped)."""
    a_id, b_id = ids
    total = 0.0
    for e in events:
        if not lo <= e["f"] <= hi or e["p"] not in ids:
            continue
        who = e["p"] if not swapped else (b_id if e["p"] == a_id else a_id)
        p = player_frames[e["f"]].get(who)
        if p is not None:
            total += min(math.dist(p, e["xy"]), 30.0)
    return total


def keeps_shot(player_frames, a_id, b_id, lo, hi, shot):
    """False if swapping a_id and b_id over lo..hi would take the shooter
    farther from the ball at the kick. shot: (shooter id, kick frame, ball xy)."""
    if shot is None:
        return True
    shooter, kick, spot = shot
    if shooter not in (a_id, b_id) or not lo <= kick <= hi:
        return True
    other = b_id if shooter == a_id else a_id
    mine, theirs = player_frames[kick].get(shooter), player_frames[kick].get(other)
    return mine is None or theirs is None or math.dist(theirs, spot) < math.dist(mine, spot)


def try_swap(player_frames, players, pid, f, spot, events=(), shot=None):
    """If a teammate of `pid` is within AT_BALL_M of `spot` at frame f and the
    swap fits StatsBomb's events better, swap the two tracks over the stretch
    (in place). events: the clip's StatsBomb events ("f", "p", "xy"); the touch
    itself counts as one. shot: (shooter id, kick frame, ball xy): no swap takes
    the shooter away from the ball at the kick (keeps_shot). Returns (other id,
    first, last) or None."""
    here = player_frames[f]
    if pid not in here:
        return None
    team = players[pid]["team"]
    mates = [(math.dist(xy, spot), q) for q, xy in here.items()
             if q != pid and players.get(q, {}).get("team") == team and players[q]["position"] != "GK"]
    if players[pid]["position"] == "GK" or not mates:
        return None
    d, other = min(mates)
    if d > AT_BALL_M:
        return None
    lo, hi = _stretch(player_frames, pid, other, f)
    if not keeps_shot(player_frames, pid, other, lo, hi, shot):
        return None
    evs = list(events) + [{"f": f, "p": pid, "xy": spot}]
    if _fit(player_frames, evs, (pid, other), lo, hi, True) > _fit(player_frames, evs, (pid, other), lo, hi, False) - MIN_GAIN_M:
        return None
    for k in range(lo, hi + 1):
        a, b = player_frames[k].get(pid), player_frames[k].get(other)
        if a is not None and b is not None:
            player_frames[k][pid], player_frames[k][other] = b, a
    return other, lo, hi
