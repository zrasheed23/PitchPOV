"""Estimate where the ball was while the tracking lost it.

Both PFF ball feeds drop the ball for a second or more at a time, mostly when
it's at a player's feet in a crowd or high in the air. For every gap before the
shot we know where the ball was last seen and where it reappears, and where all
22 players were. From that:

- Same player at both ends (within OWNER_M of the ball): he kept it. The ball
  runs at his feet, a little ahead of him, easing from where it was last seen
  to where it reappears.
- Different players (or nobody) at the ends: a pass. The ball stays with the
  first player, then travels to where it reappears at PASS_MPS, leaving just
  early enough to arrive on time. Long passes between two balls on the ground
  get a lofted arc.
- A gap at the very start of the clip: the ball is with whoever has it when it
  first appears, so it follows him back to the start.

Every estimated stretch is returned as a frame range so the viewer can show it
differently if it wants to.
"""

import math

OWNER_M = 2.5  # a player this close to the ball has it
FEET_AHEAD_M = 0.5  # a dribbled ball sits this far ahead of the player
PASS_MPS = 16.0  # typical pass speed
LOFT_MIN_M = 25.0  # a pass longer than this between two grounded balls is lofted
LOFT_RATIO = 0.12  # arc height as a fraction of the pass length
BLEND_S = 0.3  # ease from the tracked ball onto the player (and back) over this long


def _owner(ball, players):
    """Id of the nearest player within OWNER_M of the ball, or None."""
    best, best_d = None, OWNER_M
    for pid, (x, y) in players.items():
        d = math.hypot(ball[0] - x, ball[1] - y)
        if d <= best_d:
            best, best_d = pid, d
    return best


def _feet(player_frames, pid, i, times):
    """Where a dribbled ball sits at frame i: a little ahead of the player."""
    x, y = player_frames[i][pid]
    j0, j1 = max(i - 3, 0), min(i + 3, len(player_frames) - 1)
    a, b = player_frames[j0].get(pid, (x, y)), player_frames[j1].get(pid, (x, y))
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n < 0.05 or times[j1] <= times[j0]:
        return (x, y, 0.11)
    return (x + dx / n * FEET_AHEAD_M, y + dy / n * FEET_AHEAD_M, 0.11)


def _lerp(a, b, w):
    return tuple(av + (bv - av) * w for av, bv in zip(a, b))


def _ease(w):
    w = min(max(w, 0.0), 1.0)
    return w * w * (3 - 2 * w)


def estimate_gaps(ball, times, player_frames, end):
    """Fill every run of None before frame `end` (the shot). Returns (ball, ranges)
    where ranges is a list of [first, last] frame indices that were estimated."""
    out = list(ball)
    ranges = []
    n = min(end, len(out))
    i = 0
    while i < n:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j] is None:
            j += 1
        if j >= len(out):  # no ball after this before the clip ends: leave it
            break
        after = out[j]
        before = out[i - 1] if i > 0 else None
        a_owner = _owner(before, player_frames[i - 1]) if before is not None else None
        b_owner = _owner(after, player_frames[j])

        if before is None:
            # Start of the clip: whoever has it when it appears had it before.
            for k in range(i, j):
                if b_owner and b_owner in player_frames[k]:
                    out[k] = _feet(player_frames, b_owner, k, times)
                else:
                    out[k] = after
            # Ease onto the tracked position where it appears.
            for k in range(i, j):
                w = _ease(1 - (times[j] - times[k]) / BLEND_S)
                out[k] = _lerp(out[k], after, w)
        elif a_owner is not None and a_owner == b_owner:
            # Same player: a dribble.
            for k in range(i, j):
                feet = _feet(player_frames, a_owner, k, times) if a_owner in player_frames[k] else _lerp(before, after, 0.5)
                w_in = _ease((times[k] - times[i - 1]) / BLEND_S)
                w_out = _ease((times[j] - times[k]) / BLEND_S)
                p = _lerp(before, feet, w_in)
                out[k] = _lerp(after, p, w_out)
        else:
            # A pass: hold with the first player, then travel to where it reappears.
            gap_s = times[j] - times[i - 1]
            dist = math.hypot(after[0] - before[0], after[1] - before[1])
            travel = min(gap_s, max(dist / PASS_MPS, 0.2))
            release = times[j] - travel
            start = before
            for k in range(i, j):
                if times[k] < release and a_owner and a_owner in player_frames[k]:
                    feet = _feet(player_frames, a_owner, k, times)
                    out[k] = _lerp(before, feet, _ease((times[k] - times[i - 1]) / BLEND_S))
                    start = out[k]
                elif times[k] < release:
                    out[k] = before
                    start = before
            lofted = dist > LOFT_MIN_M and before[2] < 0.5 and after[2] < 0.5
            t0 = max(release, times[i - 1])
            for k in range(i, j):
                if times[k] < release:
                    continue
                w = (times[k] - t0) / (times[j] - t0) if times[j] > t0 else 1.0
                x = start[0] + (after[0] - start[0]) * w
                y = start[1] + (after[1] - start[1]) * w
                z = start[2] + (after[2] - start[2]) * w
                if lofted:
                    z += 4 * LOFT_RATIO * dist * w * (1 - w)
                out[k] = (x, y, max(z, 0.0))
        ranges.append([i, j - 1])
        i = j
    return out, ranges
