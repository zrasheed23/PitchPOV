"""The ball only changes direction at a touch.

Before it crosses the goal line, a football changes direction, changes speed
suddenly or leaves the ground only when a player within reach touches it, or
when it bounces (which changes its height and takes off some speed, never its
direction on the ground). This module finds every frame where the ball in a
clip breaks that rule. build_clip runs it last; build_all lists every clip with
any, and the fixes (enforce_touch_rule) are in ball_rules.py.
"""

import math

from ball_physics import DRAG, ROLL_DECEL

WINDOW = 3  # frames each side of the frame being checked (~0.1 s)
LONG_WINDOW = 8  # ...and a wider look, for turns spread over a few frames
TURN_DEG = 15.0
TURN_MIN_MPS = 2.0  # slower than this, direction is tracking noise
SPEED_JUMP_MPS = 1.5  # speed change over WINDOW frames beyond what drag and grass take off
LIFT_MPS = 1.0  # the ball's upward speed growing this much with no touch or bounce
GROUND_Z = 0.3  # a bounce between two frames can leave the lowest one this high
REACH_M = 1.5  # player centre to ball, on the ground plane, for a touch to count
REACH_Z = 2.6
GOAL_LINE_X = 52.5


def crossing_frame(ball, goal_index):
    """First frame after the shot with the ball over the goal line it's scored
    in (or, for a goal-line clearance that never crosses, its closest approach),
    or None."""
    b0 = ball[goal_index] if goal_index < len(ball) else None
    if b0 is None:
        return None
    side = 1 if b0[0] >= 0 else -1
    best = None
    for i in range(goal_index + 1, len(ball)):
        b = ball[i]
        if b is None:
            continue
        if side * b[0] >= GOAL_LINE_X:
            return i
        if best is None or side * b[0] > side * ball[best][0]:
            best = i
    return best


def _vel(ball, times, a, b):
    pa, pb = ball[a], ball[b]
    dt = times[b] - times[a]
    return tuple((pb[d] - pa[d]) / dt for d in range(3))


def violations(ball, times, contacts, player_frames, goal_index, carries=(), end=None):
    """[(frame, kind)] where the ball breaks the rule, kind one of "turn",
    "speed", "lift" or "far touch" (a logged touch with the ball out of the
    player's reach). Checked up to the goal-line crossing (or `end`)."""
    if end is None:
        end = crossing_frame(ball, goal_index)
    if end is None:
        end = goal_index
    touches = sorted({c["f"] for c in contacts})
    held = set()
    for a, b, _ in carries:
        held.update(range(a - WINDOW, b + WINDOW + 1))
    found = []

    def touch_near(i, k):
        return any(i - k <= t <= i + k for t in touches)

    for c in contacts:
        f = c["f"]
        if f > end or f in held or ball[f] is None or c["p"] not in player_frames[f]:
            continue
        px, py = player_frames[f][c["p"]]
        if math.hypot(ball[f][0] - px, ball[f][1] - py) > REACH_M or ball[f][2] > REACH_Z:
            found.append((f, "far touch"))

    for i in range(WINDOW, end - WINDOW):  # the frame at `end` is already over the line
        if i in held or touch_near(i, WINDOW):
            continue
        a, b = i - WINDOW, i + WINDOW
        if any(ball[k] is None for k in (a, i, b)) or not times[a] < times[i] < times[b]:
            continue
        va, vb = _vel(ball, times, a, i), _vel(ball, times, i, b)
        sa, sb = math.hypot(va[0], va[1]), math.hypot(vb[0], vb[1])
        bounce = _bounce(ball, times, a, b)
        # Drag and the grass slow it a little over the window; a bounce takes off up to a fifth.
        slows = SPEED_JUMP_MPS + (times[b] - times[a]) / 2 * (DRAG * sa * sa + ROLL_DECEL) * 1.5
        kind = None
        if sa >= TURN_MIN_MPS and sb >= TURN_MIN_MPS and _angle(va, vb) > TURN_DEG:
            kind = "turn"
        elif sb - sa > SPEED_JUMP_MPS or sa - sb > slows + (0.25 * sa if bounce else 0.0):
            kind = "speed"
        elif vb[2] > 0.5 and vb[2] - va[2] > LIFT_MPS and not bounce:
            kind = "lift"
        elif i - LONG_WINDOW >= 0 and i + LONG_WINDOW <= end and not touch_near(i, LONG_WINDOW):
            a2, b2 = i - LONG_WINDOW, i + LONG_WINDOW
            if b2 < end and ball[a2] is not None and ball[b2] is not None:
                va2, vb2 = _vel(ball, times, a2, i), _vel(ball, times, i, b2)
                if (math.hypot(va2[0], va2[1]) >= TURN_MIN_MPS and math.hypot(vb2[0], vb2[1]) >= TURN_MIN_MPS
                        and _angle(va2, vb2) > TURN_DEG):
                    kind = "turn"
        if kind:
            found.append((i, kind))
    return _runs(found)


def _bounce(ball, times, a, b):
    """The ball comes down to the ground and goes back up between frames a and
    b, rising no faster than it fell (a bounce loses speed)."""
    m = min(range(a, b + 1), key=lambda k: ball[k][2] if ball[k] is not None else math.inf)
    if ball[m] is None or ball[m][2] > GROUND_Z:
        return False
    fall = max(((ball[k - 1][2] - ball[k][2]) / (times[k] - times[k - 1]) for k in range(max(1, m - 3), m + 1)
                if ball[k] is not None and ball[k - 1] is not None and times[k] > times[k - 1]), default=0.0)
    rise = max(((ball[k][2] - ball[k - 1][2]) / (times[k] - times[k - 1]) for k in range(m + 1, min(len(ball) - 1, m + 3) + 1)
                if ball[k] is not None and ball[k - 1] is not None and times[k] > times[k - 1]), default=0.0)
    return fall > 0 and rise <= fall + 0.3


def _angle(u, v):
    nu, nv = math.hypot(u[0], u[1]), math.hypot(v[0], v[1])
    cos = (u[0] * v[0] + u[1] * v[1]) / (nu * nv)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _runs(found):
    """One entry per run of neighbouring frames of the same kind (its middle frame)."""
    out = []
    found = sorted(found)
    k = 0
    while k < len(found):
        j = k
        while j + 1 < len(found) and found[j + 1][1] == found[k][1] and found[j + 1][0] - found[j][0] <= 2:
            j += 1
        out.append((found[(k + j) // 2][0], found[k][1]))
        k = j + 1
    return out
