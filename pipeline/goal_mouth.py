"""Goal-mouth correction: make the ball actually go in.

PFF usually loses the ball just after a shot, and its path can then cross the
goal line outside the posts (Di María's smoothed ball goes ~1 m wide) or stop
short of it. For the post-shot path:
- Goal-line clearance: the ball comes within 1.5 m of the line between the
  posts, then moves away before it crosses (Messi 108', cleared by Koundé from
  behind the line). Shift the path locally so its closest point is 0.3 m over
  the line, blending back to the real path over ~0.5 s each side. The clearance
  stays; the ball is not carried into the net.
- Crosses the line: if wide of a post or over the bar, shift the path so it
  crosses 0.3 m inside the nearest post and under the bar. The shift ramps
  linearly from 0 at the shot to the full amount at the crossing, so nothing
  jumps. Then carry the ball ~2 m into the net, slowing to a stop, and leave it
  resting on the ground there for the rest of the clip.
- Never crosses: if the ball vanishes within 5 m of the goal mouth, carry it
  straight in from there, then into the net.
- Never gets near the goal (mostly headers, where tracking loses the ball the
  moment it leaves the head): replace the post-shot path with a straight shot
  from the ball at the shot into the goal at SHOT_SPEED, aimed along the
  ball's first ~0.25 s of travel when that points at the goal, else at the
  nearest point of the goal mouth. Reported as "synthesized" so it can be
  checked by eye.
"""

import math

GOAL_LINE_X = 52.5
POST_Y = 3.66
BAR_Z = 2.44
MARGIN = 0.3
NET_DEPTH = 2.0
MIN_NET_SPEED = 4.0  # m/s; floor for the speed the ball enters the net at
CARRY_SPEED = 15.0  # m/s when carrying a vanished ball into the goal
MAX_CARRY_M = 5.0  # only carry a vanished ball in from this close to the goal mouth
CLEARANCE_NEAR_M = 1.5  # a ball this close to the line, then moving away, was cleared
CLEARANCE_RETREAT_M = 1.0  # how far back from its closest point counts as moving away
CLEARANCE_BLEND_S = 0.5  # blend the clearance shift in and out over this long
SHOT_SPEED = 20.0  # m/s for a synthesized shot
AIM_LOOKAHEAD_S = 0.25  # how much of the real post-shot path sets the aim
HEADER_MAX_Z = 2.3  # a ball higher than this at the shot can't be touching the scorer
HEADER_Z = 1.7  # ball height at a header (the viewer's players are 1.8 m tall)
HEADER_BLEND_S = 0.6  # lower the incoming ball to head height over this long


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


def goal_mouth_distance(point, side):
    """Distance from a ball (x, y, z) to the nearest point of the goal mouth."""
    mouth = (
        side * GOAL_LINE_X,
        min(max(point[1], -POST_Y), POST_Y),
        min(max(point[2], 0.0), BAR_Z),
    )
    return math.dist(point, mouth)


def _find_clearance(ball, start, end, side):
    """A goal-line clearance between `start` and `end` (exclusive): the ball
    gets within CLEARANCE_NEAR_M of the line between the posts and under the bar,
    then retreats CLEARANCE_RETREAT_M before `end`. Returns the index of the
    closest approach, or None."""
    seen = [i for i in range(start, end) if ball[i] is not None]
    if not seen:
        return None
    c = max(seen, key=lambda i: side * ball[i][0])
    x, y, z = ball[c]
    if GOAL_LINE_X - side * x > CLEARANCE_NEAR_M or abs(y) > POST_Y or z > BAR_Z:
        return None
    if any(side * ball[i][0] < side * x - CLEARANCE_RETREAT_M for i in seen if i > c):
        return c
    return None


def _shift_over_line(ball, times, c, goal_index, side):
    """Shift the path around frame c so ball[c] is MARGIN over the line,
    easing in and out over CLEARANCE_BLEND_S (never before the shot).
    Returns the shift in metres."""
    dx = side * (GOAL_LINE_X + MARGIN) - ball[c][0]
    before = min(CLEARANCE_BLEND_S, times[c] - times[goal_index])
    for i in range(goal_index, len(ball)):
        if ball[i] is None:
            continue
        d = times[i] - times[c]
        half = before if d < 0 else CLEARANCE_BLEND_S
        if abs(d) > half:
            continue
        w = 0.5 * (1 + math.cos(math.pi * d / half)) if half > 0 else 1.0
        x, y, z = ball[i]
        ball[i] = (x + dx * w, y, z)
    return abs(dx)


def _carry_in(ball, times, j, side):
    """Carry the ball in a straight line at CARRY_SPEED from frame j to 0.3 m
    inside the nearest post and under the bar. Returns the same tuple as
    _find_crossing."""
    max_y = POST_Y - MARGIN
    x, y, z = ball[j]
    target = (side * GOAL_LINE_X, min(max(y, -max_y), max_y), min(z, BAR_Z - MARGIN))
    t_cross = times[j] + math.dist(ball[j], target) / CARRY_SPEED
    k = j + 1
    while k < len(ball) and times[k] < t_cross:
        w = (times[k] - times[j]) / (t_cross - times[j])
        ball[k] = _lerp(ball[j], target, w)
        k += 1
    return k, t_cross, target


def correct_goal_mouth(ball, times, goal_index):
    """Return (corrected ball list, info). ball is a list of (x, y, z) or None.

    info: {"corrected": bool,
           "reason": None | "wide" | "high" | "clearance" | "short"
                     | "synthesized" | "no ball",
           "shift": metres the crossing (or clearance) point moved,
           "carried": metres of path invented after the ball vanished,
           "needs_review": True when the path was left alone and won't go in}.
    """
    ball = list(ball)
    info = {"corrected": False, "reason": None, "shift": 0.0, "carried": 0.0, "needs_review": False}
    start = next((i for i in range(goal_index, -1, -1) if ball[i] is not None), None)
    if start is None:
        info["reason"] = "no ball"
        info["needs_review"] = True
        return ball, info
    side = goal_side(ball, goal_index)

    crossing = _find_crossing(list(ball), times, start, side)
    end = crossing[0] if crossing else len(ball)
    c = _find_clearance(ball, start, end, side)
    if c is not None:
        info.update(corrected=True, reason="clearance")
        info["shift"] = _shift_over_line(ball, times, c, goal_index, side)
        return ball, info

    crossing = _find_crossing(ball, times, start, side)
    if crossing is None:
        j = max(i for i in range(start, len(ball)) if ball[i] is not None)
        if j == len(ball) - 1 or goal_mouth_distance(ball[j], side) > MAX_CARRY_M:
            k, t_cross, point = _synthesize_shot(ball, times, start, side)
            info.update(corrected=True, reason="synthesized", carried=math.dist(ball[start], point))
            _into_net(ball, times, k, t_cross, point, side)
            return ball, info
        k, t_cross, point = _carry_in(ball, times, j, side)
        info.update(corrected=True, reason="short", carried=math.dist(ball[j], point))
        _into_net(ball, times, k, t_cross, point, side)
        return ball, info

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


def _synthesize_shot(ball, times, start, side):
    """Overwrite the path after `start` with a straight shot into the goal at
    SHOT_SPEED. Returns the same tuple as _find_crossing."""
    max_y = POST_Y - MARGIN
    x0, y0, z0 = ball[start]
    if z0 > HEADER_MAX_Z:
        # PFF's smoothed ball follows the scorer in x/y but can keep the cross's height
        # (4+ m at Gakpo's header). Bring it down to his head, easing in over the last
        # HEADER_BLEND_S so the incoming cross meets him instead of passing overhead.
        dz = HEADER_Z - z0
        for i in range(start, -1, -1):
            if times[start] - times[i] > HEADER_BLEND_S:
                break
            if ball[i] is not None:
                w = 1 - (times[start] - times[i]) / HEADER_BLEND_S
                x, y, z = ball[i]
                ball[i] = (x, y, max(z + dz * w, 0.0))
        z0 = HEADER_Z
    gx = side * GOAL_LINE_X
    aim_y = min(max(y0, -max_y), max_y)  # default: nearest point of the goal mouth
    later = next((i for i in range(start + 1, len(ball))
                  if ball[i] is not None and times[i] - times[start] >= AIM_LOOKAHEAD_S), None)
    if later is not None:
        dx, dy = ball[later][0] - x0, ball[later][1] - y0
        if dx * side > 0.3:  # moving toward the goal: follow that direction to the line
            y_line = y0 + dy * (gx - x0) / dx
            if abs(y_line) <= POST_Y + 2.0:  # roughly on target; keep the aim, just inside the posts
                aim_y = min(max(y_line, -max_y), max_y)
    target = (gx, aim_y, min(max(z0, 0.3), BAR_Z - MARGIN))
    t_cross = times[start] + math.dist(ball[start], target) / SHOT_SPEED
    k = start + 1
    while k < len(ball) and times[k] < t_cross:
        w = (times[k] - times[start]) / (t_cross - times[start])
        ball[k] = _lerp(ball[start], target, w)
        k += 1
    return k, t_cross, target
