"""Goal-mouth correction: make the ball actually go in.

PFF usually loses the ball just after a shot, and ballsSmoothed then carries
it on in a straight line from the shooter, which can cross the goal line
outside the posts (Di María's goes ~1 m wide) before the ball disappears.

For the post-shot path:
- Find where it crosses the goal line. If that's wide of a post, over the bar,
  or the ball never gets there, shift the path so it crosses 0.3 m inside the
  nearest post and under the bar. The shift ramps linearly from 0 at the shot
  to the full amount at the crossing, so nothing jumps.
- After the crossing, carry the ball ~2 m into the net, slowing to a stop, and
  leave it resting on the ground there for the rest of the clip.
"""

import math

GOAL_LINE_X = 52.5
POST_Y = 3.66
BAR_Z = 2.44
MARGIN = 0.3
NET_DEPTH = 2.0
MIN_NET_SPEED = 4.0  # m/s; floor for the speed the ball enters the net at
EXTRAPOLATE_SPEED = 15.0  # m/s toward goal when the ball vanishes at the shot
VELOCITY_WINDOW = 5  # frames used to estimate the ball's velocity


def goal_side(ball, goal_index):
    """+1 if the goal at x = +52.5 is being scored in, else -1: the half the
    ball is in at the shot (nearest frame with a ball)."""
    for d in range(len(ball)):
        for i in (goal_index - d, goal_index + d):
            if 0 <= i < len(ball) and ball[i] is not None:
                return 1 if ball[i][0] >= 0 else -1
    return 1


def _lerp(a, b, w):
    return tuple(av + (bv - av) * w for av, bv in zip(a, b))


def _find_crossing(ball, times, start, side):
    """First crossing of the goal line after `start`, bridging null gaps.

    Returns (frame index at/after the crossing, crossing time, crossing point),
    or None if the ball never reaches the line. Fills null gaps before the
    crossing in place.
    """
    j = start
    for k in range(start + 1, len(ball)):
        if ball[k] is None:
            continue
        xj, xk = side * ball[j][0], side * ball[k][0]
        if xj < GOAL_LINE_X <= xk:
            w = (GOAL_LINE_X - xj) / (xk - xj)
            point = _lerp(ball[j], ball[k], w)
            t = times[j] + (times[k] - times[j]) * w
            _fill(ball, times, j, k)
            return k, t, point
        _fill(ball, times, j, k)
        j = k
    return None


def _fill(ball, times, j, k):
    for i in range(j + 1, k):
        w = (times[i] - times[j]) / (times[k] - times[j])
        ball[i] = _lerp(ball[j], ball[k], w)


def _extrapolate(ball, times, start, side):
    """Carry the ball on in a straight line from its last known point until it
    reaches the goal line. Returns the same tuple as _find_crossing."""
    j = max(i for i in range(start, len(ball)) if ball[i] is not None)
    i0 = max(start, j - VELOCITY_WINDOW)
    v = None
    if j > i0 and times[j] > times[i0]:
        dt = times[j] - times[i0]
        v = tuple((b - a) / dt for a, b in zip(ball[i0], ball[j]))
    if v is None or side * v[0] < 2.0:
        # No usable velocity (or it points away from goal): head for the goal.
        dx, dy = side * GOAL_LINE_X - ball[j][0], -ball[j][1]
        d = math.hypot(dx, dy) or 1.0
        v = (dx / d * EXTRAPOLATE_SPEED, dy / d * EXTRAPOLATE_SPEED, 0.0)

    t_cross = times[j] + (GOAL_LINE_X - side * ball[j][0]) / (side * v[0])
    latest = times[-1] - 0.3  # leave a moment to see it hit the net
    if t_cross > latest and latest > times[j]:
        v = tuple(c * (t_cross - times[j]) / (latest - times[j]) for c in v)
        t_cross = latest

    def at(t):
        x, y, z = (p + c * (t - times[j]) for p, c in zip(ball[j], v))
        return (x, y, max(z, 0.0))

    k = j + 1
    while k < len(ball) and times[k] < t_cross:
        ball[k] = at(times[k])
        k += 1
    return k, t_cross, at(t_cross)


def correct_goal_mouth(ball, times, goal_index):
    """Return (corrected ball list, info). ball is a list of (x, y, z) or None.

    info: {"corrected": bool, "reason": None | "wide" | "high" | "short",
           "shift": metres the crossing point moved, "extrapolated": metres
           of path invented after the ball vanished}.
    """
    ball = list(ball)
    info = {"corrected": False, "reason": None, "shift": 0.0, "extrapolated": 0.0}
    start = next((i for i in range(goal_index, -1, -1) if ball[i] is not None), None)
    if start is None:
        info["reason"] = "no ball"
        return ball, info
    side = goal_side(ball, goal_index)

    crossing = _find_crossing(ball, times, start, side)
    if crossing is None:
        last = max(i for i in range(start, len(ball)) if ball[i] is not None)
        k, t_cross, point = _extrapolate(ball, times, start, side)
        info["extrapolated"] = math.dist(ball[last][:2], point[:2])
        info["reason"] = "short"
    else:
        k, t_cross, point = crossing
        if abs(point[1]) > POST_Y:
            info["reason"] = "wide"
        elif point[2] > BAR_Z:
            info["reason"] = "high"

    _, yc, zc = point
    if info["reason"]:
        info["corrected"] = True
        max_y = POST_Y - MARGIN
        dy = min(max(yc, -max_y), max_y) - yc
        dz = min(zc, BAR_Z - MARGIN) - zc
        info["shift"] = math.hypot(dy, dz)
        t0 = times[goal_index]
        for i in range(goal_index, k):
            if ball[i] is None:
                continue
            w = min(max((times[i] - t0) / (t_cross - t0), 0.0), 1.0) if t_cross > t0 else 1.0
            x, y, z = ball[i]
            ball[i] = (x, y + dy * w, max(z + dz * w, 0.0))
        point = (point[0], yc + dy, zc + dz)

    _into_net(ball, times, k, t_cross, point, side)
    return ball, info


def _into_net(ball, times, k, t_cross, point, side):
    """From the crossing, ease the ball ~NET_DEPTH m into the net and rest it."""
    prev = next((i for i in range(k - 1, -1, -1) if ball[i] is not None and times[i] < t_cross), None)
    speed = MIN_NET_SPEED
    vy_per_x = 0.0
    if prev is not None and t_cross > times[prev]:
        dt = t_cross - times[prev]
        vx = (point[0] - ball[prev][0]) / dt
        vy = (point[1] - ball[prev][1]) / dt
        speed = max(math.hypot(vx, vy), MIN_NET_SPEED)
        if abs(vx) > 1e-6:
            vy_per_x = side * vy / abs(vx)

    max_y = POST_Y - MARGIN
    rest = (
        side * (GOAL_LINE_X + NET_DEPTH),
        min(max(point[1] + vy_per_x * NET_DEPTH, -max_y), max_y),
        0.0,
    )
    dist = math.dist(point, rest)
    # Constant deceleration: starts at `speed`, stops at the rest point.
    duration = 2 * dist / speed if speed > 0 else 0.0
    for i in range(k, len(ball)):
        w = min((times[i] - t_cross) / duration, 1.0) if duration > 0 else 1.0
        w = 1 - (1 - max(w, 0.0)) ** 2
        ball[i] = _lerp(point, rest, w)
