"""Penalty kicks: a still ball on the spot and a legal setup until the kick.

PFF marks a penalty with gameEvents.setpieceType == "P" on the shot. The
tracking around a penalty is messy: the ball drifts around the spot or goes
missing, the players (PFF estimates) stand inside the area, and the taker can
be 2-4 m from the ball when he kicks it. Until the kick frame (goalFrame, which
matches the ball leaving the spot to within ~0.1 s):
- the ball sits exactly on the spot, 11 m from the goal line, centred;
- every player except the taker and the defending keeper is outside the
  penalty area and at least 9.15 m from the spot, moved to the nearest such
  point (the tracked position if it is already legal);
- the keeper stands on his line, between the posts;
- the taker is eased onto the ball over his run-up, so his foot meets it.
After the kick the moved players blend back onto their tracked positions, over
at least BLEND_S, accelerating no harder than EASE_ACCEL, and slowly enough that no one gains more than CATCHUP_MPS on
his tracked run (PFF often has them several metres inside the area at the kick).
"""

import math

GOAL_LINE_X = 52.5
POST_Y = 3.66
SPOT_X = GOAL_LINE_X - 11.0
AREA_X = GOAL_LINE_X - 16.5
AREA_HALF_WIDTH = 20.16
SPOT_CLEAR_M = 9.15
MARGIN_M = 0.25  # stand this far clear of the line, not on it
TAKER_FROM_BALL_M = 0.5  # the taker's centre from the ball as he kicks it
RUNUP_S = 2.0  # ease the taker onto the ball over this long before the kick
BLEND_S = 0.5  # ease everyone back onto the tracking over at least this long after the kick
CATCHUP_MPS = 5.0  # ...and no faster than this on top of their own movement
EASE_ACCEL = 4.0  # ...and no harder than this (m/s^2; smoothstep peaks at 6 d / T^2), as ball_rules.EASE_ACCEL
KEEPER_MAX_M = 6.0  # the defending keeper is the goalkeeper this close to the goal line at the kick


def spot(side):
    return (side * SPOT_X, 0.0)


def pin_ball(ball, kick, side):
    """The ball on the spot, on the ground, for every frame up to the kick."""
    out = list(ball)
    x, y = spot(side)
    for i in range(min(kick + 1, len(out))):
        out[i] = (x, y, 0.0)
    return out


def is_legal(xy, side, tol=1e-6):
    """Outside the penalty area (with MARGIN_M) and SPOT_CLEAR_M (+ MARGIN_M) from the spot."""
    u, y = side * xy[0], xy[1]
    in_area = u > AREA_X - MARGIN_M + tol and abs(y) < AREA_HALF_WIDTH + MARGIN_M - tol
    return not in_area and math.dist((u, y), (SPOT_X, 0.0)) >= SPOT_CLEAR_M + MARGIN_M - tol


def nearest_legal(xy, side):
    """The nearest point to xy that is_legal (xy itself if it already is)."""
    if is_legal(xy, side):
        return xy
    u, y = side * xy[0], xy[1]
    front = AREA_X - MARGIN_M
    edge = AREA_HALF_WIDTH + MARGIN_M
    r = SPOT_CLEAR_M + MARGIN_M
    candidates = [(front, y), (u, edge if y >= 0 else -edge)]
    d = math.dist((u, y), (SPOT_X, 0.0))
    if d > 1e-9:
        candidates.append((SPOT_X + (u - SPOT_X) * r / d, y * r / d))
    else:
        candidates.append((SPOT_X - r, 0.0))
    # Where the arc around the spot meets the front of the area.
    h = math.sqrt(max(r * r - (SPOT_X - front) ** 2, 0.0))
    candidates += [(front, h), (front, -h)]
    legal = [c for c in candidates if is_legal((side * c[0], c[1]), side, tol=1e-6)]
    u2, y2 = min(legal, key=lambda c: math.dist(c, (u, y)))
    return (side * u2, y2)


def keeper_on_line(xy, side):
    return (side * GOAL_LINE_X, min(max(xy[1], -POST_Y), POST_Y))


def find_keeper(player_frames, kick, side, goalkeepers):
    """The goalkeeper standing nearest the goal at the kick, or None if none is
    near it. Either team's: a label swap PFF has that cut_clip doesn't undo
    yet shouldn't move the man in goal out of the area."""
    at = player_frames[kick]
    near = [(abs(at[pid][0] - side * GOAL_LINE_X), pid) for pid in goalkeepers if pid in at]
    near = [(d, pid) for d, pid in near if d <= KEEPER_MAX_M]
    return min(near)[1] if near else None


def _ease(w):
    return 0.5 - 0.5 * math.cos(math.pi * min(max(w, 0.0), 1.0))


def legal_track(track, times, side):
    """A legal position for each (x, y) in one player's track (None where he is
    missing). Taking the nearest legal point frame by frame can jump: a player
    PFF puts on the spot is equally near every point of the arc around it. So
    the legal point follows his movement from the frame before and is pulled
    toward his tracked position no faster than CATCHUP_MPS, which keeps the
    point being made legal close to the edge, where the nearest legal point
    moves smoothly."""
    out, prev, prev_xy = [], None, None
    for i, xy in enumerate(track):
        if xy is None:
            out.append(None)
            prev = None
            continue
        want = xy
        if prev is not None:
            want = (prev[0] + xy[0] - prev_xy[0], prev[1] + xy[1] - prev_xy[1])
            gap = math.dist(want, xy)
            step = min(gap, CATCHUP_MPS * max(times[i] - times[i - 1], 0.0))
            if gap > 1e-9:
                want = (want[0] + (xy[0] - want[0]) * step / gap, want[1] + (xy[1] - want[1]) * step / gap)
        prev, prev_xy = nearest_legal(want, side), xy
        out.append(prev)
    return out


def place_players(player_frames, times, kick, side, taker, keeper):
    """Return (new player_frames, the largest move in metres). player_frames is
    a list of {player id: (x, y)}; taker and keeper are player ids (or None)."""
    out = [dict(ps) for ps in player_frames]
    ball = spot(side)

    # Where each player stands up to the kick.
    before = {}
    for pid in player_frames[kick]:
        track = [player_frames[i].get(pid) for i in range(kick + 1)]
        if pid == keeper:
            before[pid] = [keeper_on_line(xy, side) if xy else None for xy in track]
        elif pid != taker:
            before[pid] = legal_track(track, times, side)

    # Each player's move at the kick, and how long it takes to fade out.
    at_kick = {}
    for pid, xy in player_frames[kick].items():
        if pid == taker:
            dx, dy = xy[0] - ball[0], xy[1] - ball[1]
            n = math.hypot(dx, dy)
            ux, uy = (dx / n, dy / n) if n > 1e-6 else (-side, 0.0)
            target = (ball[0] + ux * TAKER_FROM_BALL_M, ball[1] + uy * TAKER_FROM_BALL_M)
        else:
            target = before[pid][kick]
        at_kick[pid] = (target[0] - xy[0], target[1] - xy[1])
    fade = {pid: max(BLEND_S, math.hypot(*off) / CATCHUP_MPS, math.sqrt(6 * math.hypot(*off) / EASE_ACCEL))
            for pid, off in at_kick.items()}
    longest = max(fade.values(), default=BLEND_S)

    biggest = 0.0
    for i in range(len(out)):
        dt = times[i] - times[kick]
        if dt > longest:
            break
        for pid, xy in player_frames[i].items():
            if pid not in at_kick:
                continue
            if dt > 0:  # after the kick: the move made at the kick, fading out
                w = _ease(1 - dt / fade[pid])
                off = (at_kick[pid][0] * w, at_kick[pid][1] * w)
            elif pid == taker:  # eased onto the ball over his run-up
                w = _ease(1 + dt / RUNUP_S)
                off = (at_kick[pid][0] * w, at_kick[pid][1] * w)
            else:
                fixed = before[pid][i]
                off = (fixed[0] - xy[0], fixed[1] - xy[1])
            if off != (0.0, 0.0):
                out[i][pid] = (xy[0] + off[0], xy[1] + off[1])
                biggest = max(biggest, math.hypot(*off))
    return out, biggest
