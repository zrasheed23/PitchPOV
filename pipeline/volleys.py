"""Acrobatic volleys: scissor and overhead kicks.

A foot touch (or the shot) with the ball at VOLLEY_Z or higher, by a player
side-on to the goal he attacks or with his back to it, can only be hit by
leaving the ground: the viewer plays a scissor/overhead kick for it (jump
~0.4 s before the touch, body horizontal at hip height, kicking leg over,
land on his back or side, get up). Only logged touches are considered (not
the dribble pushes or the touches the touch rule adds).

Facing: where he's running over the last FACING_S before the touch if he's
moving, else toward where the ball comes from (players face the ball).
"""

import math

VOLLEY_Z = 1.0
SIDE_ON_DEG = 60.0  # facing this far or more from the goal he attacks: side-on or away
FACING_S = 0.3
MOVING_MPS = 1.5
ARRIVE_FRAMES = 2
FOOT = {"R", "L", "F"}


def facing(player_frames, ball, times, pid, f):
    """Unit (x, y) the player faces at frame f, or None."""
    p = player_frames[f].get(pid)
    back = next((k for k in range(f, -1, -1) if times[f] - times[k] >= FACING_S), 0)
    q = player_frames[back].get(pid)
    if p is None:
        return None
    if q is not None and times[f] > times[back]:
        vx, vy = (p[0] - q[0]) / (times[f] - times[back]), (p[1] - q[1]) / (times[f] - times[back])
        if math.hypot(vx, vy) >= MOVING_MPS:
            n = math.hypot(vx, vy)
            return vx / n, vy / n
    b = ball[back]
    if b is None:
        return None
    dx, dy = b[0] - p[0], b[1] - p[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-6 else None


def find_volleys(contacts, ball, times, player_frames, attacks):
    """Mark every logged foot touch that is an acrobatic volley with "v": 1 (in
    place). attacks: {player id: +1/-1, the end he attacks}. Returns the marked
    contacts as (contact, degrees he's turned from goal, ball height)."""
    found = []
    for c in contacts:
        f, pid = c["f"], c["p"]
        # The touch frame can be a frame or two late: the ball's height as it arrives.
        z = max((ball[k][2] for k in range(max(f - ARRIVE_FRAMES, 0), f + 1) if ball[k] is not None), default=0.0)
        if c.get("s") or c["b"] not in FOOT or z < VOLLEY_Z or pid not in attacks:
            continue
        face = facing(player_frames, ball, times, pid, f)
        p = player_frames[f].get(pid)
        if face is None or p is None:
            continue
        gx, gy = attacks[pid] * 52.5 - p[0], -p[1]
        n = math.hypot(gx, gy)
        angle = math.degrees(math.acos(max(-1.0, min(1.0, (face[0] * gx + face[1] * gy) / n)))) if n > 1e-6 else 0.0
        if angle >= SIDE_ON_DEG:
            c["v"] = 1
            found.append((c, angle, z))
    return found
