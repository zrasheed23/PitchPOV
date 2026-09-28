"""The ball in the net, simulated from how it crosses the line.

After the ball crosses the goal line the tracking is no use (it loses the
ball or finds it in the net at odd spots). So the ball carries on exactly as
it crossed (same point, direction, speed, height and climb/drop), flies free
until it reaches the netting, which gives like a spring and stops it about
NET_DEPTH behind the line; the side netting and the roof net stop it too.
Then it drops, bounces weakly and rolls to rest on the grass inside the goal.
"""

import math

from ball_physics import DRAG, G

GOAL_LINE_X, POST_Y, BAR_Z = 52.5, 3.66, 2.44
NET_DEPTH = 2.0
NET_GIVE_M = 0.6  # the back netting starts catching the ball this far before NET_DEPTH
NET_STIFF = 400.0  # 1/s^2: spring of the netting as it stretches
NET_DAMP = 18.0  # 1/s
NET_RETURN_MPS = 0.8  # the most the netting pushes it back out
SIDE_M = POST_Y - 0.12  # ball centre can't get past the side netting / roof
ROOF_Z = BAR_Z - 0.12
NET_KEEP = 0.25  # speed kept off the side/roof netting
GROUND_BOUNCE = 0.35
ROLL_STOP = 3.0  # m/s^2 on the grass inside the goal
STEP = 1 / 240


def into_net(point, velocity, times, side):
    """Positions at `times` (seconds after the ball crosses the line at
    `point` with 3-D `velocity`), for a goal at x = side * 52.5."""
    # Work in depth behind the line (d), across (y) and up (z).
    d, y, z = 0.0, point[1], max(point[2], 0.0)
    vd, vy, vz = side * velocity[0], velocity[1], velocity[2]
    vd = max(vd, 1.0)
    t, i = 0.0, 0
    out = []
    rolling = False
    while i < len(times):
        while i < len(times) and times[i] <= t + 1e-9:
            out.append((side * (GOAL_LINE_X + d), y, z))
            i += 1
        if i >= len(times):
            break
        speed = math.sqrt(vd * vd + vy * vy + vz * vz)
        ad, ay, az = -DRAG * speed * vd, -DRAG * speed * vy, -G - DRAG * speed * vz
        stretch = d - (NET_DEPTH - NET_GIVE_M)
        if stretch > 0:  # the back netting gives, stops it and lets it drop (barely pushing it back)
            ad += -NET_STIFF * stretch - NET_DAMP * vd
            ay += -NET_DAMP * vy * 0.5
            if vd < -NET_RETURN_MPS:
                vd = -NET_RETURN_MPS
        if rolling:
            az, vz = 0.0, 0.0
            s = math.hypot(vd, vy)
            if s > 1e-6:
                slow = min(ROLL_STOP * STEP, s)
                vd -= vd / s * slow
                vy -= vy / s * slow
        else:
            vz += az * STEP
        vd += ad * STEP
        vy += ay * STEP
        d += vd * STEP
        y += vy * STEP
        z += vz * STEP
        if abs(y) > SIDE_M:
            y = math.copysign(SIDE_M, y)
            vy = -vy * NET_KEEP
        if z > ROOF_Z:
            z, vz = ROOF_Z, -abs(vz) * NET_KEEP
        if d > NET_DEPTH:
            d, vd = NET_DEPTH, -abs(vd) * NET_KEEP
        if d < 0.15 and vd < 0:  # it doesn't come back out over the line
            d, vd = 0.15, 0.0
        if z <= 0 and not rolling:
            z = 0.0
            if -vz * GROUND_BOUNCE < 0.5:
                rolling = True
            vz = -vz * GROUND_BOUNCE
        t += STEP
    return out


def across_the_line(ball, times, kick, side, frames=2):
    """(degrees the ball turns on the ground plane, fractional change in its
    ground speed, whether the netting (side, roof or back) catches it) between
    the `frames` before it crosses the line and the `frames` after; None if it
    never crosses. A bounce on the line flips its climb, not its direction."""
    k = next((i for i in range(kick + 1, len(ball)) if ball[i] is not None and side * ball[i][0] >= GOAL_LINE_X), None)
    if k is None or k - frames - 1 < 0 or k + frames >= len(ball) or any(ball[i] is None for i in range(k - frames - 1, k + frames + 1)):
        return None

    def vel(a, b):
        dt = times[b] - times[a]
        return [(ball[b][i] - ball[a][i]) / dt for i in range(2)]

    va, vb = vel(k - frames - 1, k - 1), vel(k, k + frames)
    na, nb = math.hypot(*va), math.hypot(*vb)
    if na < 1e-6 or nb < 1e-6:
        return None
    cos = (va[0] * vb[0] + va[1] * vb[1]) / (na * nb)
    netting = any(abs(ball[i][1]) >= SIDE_M - 0.12 or ball[i][2] >= ROOF_Z - 0.12
                  or side * ball[i][0] - GOAL_LINE_X >= NET_DEPTH - NET_GIVE_M for i in range(k, k + frames + 1))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos)))), nb / na - 1, netting
