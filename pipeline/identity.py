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
ACCEL = 4.0  # m/s²: the most a correction (here: the change of tracks at a swap's ends) adds to a player's movement
SWAP_BLEND_S = 0.3  # a swap's ends take at least twice this


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


def blend_swap(player_frames, times, a, b, lo, hi):
    """Where a swapped stretch starts or ends inside the clip the two tracks
    were close (_stretch) but not together, and moving differently: each one
    goes from his old track to his new one along a curve that matches both in
    position and speed (cubic Hermite), taking long enough that neither the
    jump nor the change of speed needs more than ACCEL. In place."""
    n = len(player_frames)

    def vel(pid, k):
        k0, k1 = max(k - 2, 0), min(k + 2, n - 1)
        p0, p1 = player_frames[k0].get(pid), player_frames[k1].get(pid)
        dt = times[k1] - times[k0]
        return ((p1[0] - p0[0]) / dt, (p1[1] - p0[1]) / dt) if p0 and p1 and dt > 0 else (0.0, 0.0)

    for edge in (lo, hi + 1):
        if not 2 < edge < n - 3:
            continue
        for pid in (a, b):
            p0, p1 = player_frames[edge - 1].get(pid), player_frames[edge].get(pid)
            if p0 is None or p1 is None:
                continue
            jump = math.dist(p0, p1)
            dv = math.dist(vel(pid, edge - 3), vel(pid, edge + 2))
            span = max(2 * SWAP_BLEND_S, math.sqrt(6 * jump / ACCEL), 1.5 * dv / ACCEL)
            t0 = (times[edge - 1] + times[edge]) / 2
            ks = next((k for k in range(edge - 1, -1, -1) if times[k] <= t0 - span / 2), 0)
            ke = next((k for k in range(edge, n) if times[k] >= t0 + span / 2), n - 1)
            if pid not in player_frames[ks] or pid not in player_frames[ke]:
                continue
            ps, pe = player_frames[ks][pid], player_frames[ke][pid]
            vs, ve = vel(pid, ks), vel(pid, ke)
            T = times[ke] - times[ks]
            for k in range(ks + 1, ke):
                if pid not in player_frames[k] or T <= 0:
                    continue
                u = (times[k] - times[ks]) / T
                h00, h10, h01, h11 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u, -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
                player_frames[k][pid] = tuple(h00 * ps[d] + h10 * T * vs[d] + h01 * pe[d] + h11 * T * ve[d]
                                              for d in range(2))


def try_swap(player_frames, players, pid, f, spot, events=(), shot=None, times=None):
    """If a teammate of `pid` is within AT_BALL_M of `spot` at frame f and the
    swap fits StatsBomb's events better, swap the two tracks over the stretch
    (in place). events: the clip's StatsBomb events ("f", "p", "xy"); the touch
    itself counts as one. shot: (shooter id, kick frame, ball xy): no swap takes
    the shooter away from the ball at the kick (keeps_shot). times: frame times,
    to blend the swap's ends (blend_swap). Returns (other id, first, last) or None."""
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
    if times is not None:
        blend_swap(player_frames, times, pid, other, lo, hi)
    return other, lo, hi
