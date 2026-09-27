"""Real ball physics between touches.

Between two touches nobody is in contact with the ball, so it moves as a
football does: in the air it falls under gravity and slows with air drag; when
it lands it bounces, losing energy and some speed each time; once it stops
bouncing it rolls and slows on the grass. No spin, so it travels in a straight
line on the ground.

For each free-flight stretch we know where it leaves one player and where the
next touch happens, and how long it takes. We solve for the kick (horizontal
speed and upward speed) that makes a simulated ball cover that distance in
that time, with the upward speed chosen so it reaches the same peak height as
the tracked ball (a ground pass stays on the ground, a lofted ball gets its
arc). The tracked path is then replaced by the simulated one.
"""

import math

G = 9.81
DRAG = 0.0135  # 1/m: 0.5 * air density * Cd * area / mass for a size-5 ball
RESTITUTION = 0.55  # vertical speed kept at a bounce
BOUNCE_GRIP = 0.8  # horizontal speed kept at a bounce
ROLL_DECEL = 0.9  # m/s^2 on grass
STOP_BOUNCING = 0.8  # below this vertical speed a bounce becomes a roll
STEP = 1 / 120
MAX_KICK = 42.0  # m/s: faster than the hardest shots on record
GROUND_PEAK = 0.4  # tracked balls lower than this were played along the ground

MIN_FLIGHT_S = 0.25  # shorter stretches are left as they are
MAX_FLIGHT_S = 6.0


def simulate(z0, u0, w0, times):
    """Horizontal distance and height at each of `times` (seconds from the kick,
    ascending) for a ball kicked from height z0 with horizontal speed u0 and
    upward speed w0. Returns (distances, heights, peak height)."""
    s, z, u, w, t = 0.0, max(z0, 0.0), u0, w0, 0.0
    rolling = z <= 0 and w <= 0
    peak = z
    out_s, out_z = [], []
    i = 0
    end = times[-1] if times else 0.0
    while i < len(times):
        while i < len(times) and times[i] <= t + 1e-9:
            out_s.append(s)
            out_z.append(z)
            i += 1
        if t > end:
            break
        if rolling:
            u = max(0.0, u - (ROLL_DECEL + DRAG * u * u) * STEP)
            s += u * STEP
        else:
            v = math.hypot(u, w)
            u -= DRAG * v * u * STEP
            w -= (G + DRAG * v * w) * STEP
            s += u * STEP
            z += w * STEP
            if z <= 0 and w < 0:
                z = 0.0
                w = -w * RESTITUTION
                u *= BOUNCE_GRIP
                if w < STOP_BOUNCING:
                    w = 0.0
                    rolling = True
            peak = max(peak, z)
        t += STEP
    while len(out_s) < len(times):
        out_s.append(s)
        out_z.append(z)
    return out_s, out_z, peak


def _solve_speed(z0, w0, times, distance):
    """Horizontal kick speed that covers `distance` by the last of `times`."""
    lo, hi = 0.0, MAX_KICK
    if simulate(z0, hi, w0, times[-1:])[0][-1] < distance:
        return None
    for _ in range(22):
        mid = (lo + hi) / 2
        if simulate(z0, mid, w0, times[-1:])[0][-1] < distance:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def solve_kick(z0, distance, duration, peak):
    """(u0, w0) for a ball kicked from z0 that travels `distance` in `duration`
    seconds and peaks near `peak`. None if no real kick can do it."""
    times = [duration]
    if peak < max(GROUND_PEAK, z0 + 0.2):
        u0 = _solve_speed(z0, 0.0, times, distance)
        return None if u0 is None else (u0, 0.0)
    lo, hi = 0.0, 25.0
    best = None
    for _ in range(16):
        w0 = (lo + hi) / 2
        u0 = _solve_speed(z0, w0, times, distance)
        if u0 is None:  # too much loft to cover the distance in time
            hi = w0
            continue
        best = (u0, w0)
        if simulate(z0, u0, w0, times)[2] < peak:
            lo = w0
        else:
            hi = w0
    return best


def apply_physics(ball, times, anchors, end, touch_heights=None):
    """Replace every free-flight stretch between consecutive anchor frames (up to
    frame `end`) with a simulated ball. touch_heights maps a frame to the height
    range (lo, hi) the ball can be at when touched there (feet, head, hands), so
    a tracked height a player couldn't reach gets pulled into range first.
    Returns (ball, stretches simulated)."""
    out = list(ball)
    anchors = sorted(a for a in set(anchors) if a <= end)
    for a, (lo, hi) in (touch_heights or {}).items():
        if a < len(out) and out[a] is not None:
            x, y, z = out[a]
            out[a] = (x, y, min(max(z, lo), hi))
    done = 0
    for a, b in zip(anchors, anchors[1:]):
        if out[a] is None or out[b] is None:
            continue
        duration = times[b] - times[a]
        if not MIN_FLIGHT_S <= duration <= MAX_FLIGHT_S:
            continue
        ax, ay, az = out[a]
        bx, by, _ = out[b]
        distance = math.hypot(bx - ax, by - ay)
        if distance < 0.5:
            continue
        peak = max((out[k][2] for k in range(a, b + 1) if out[k] is not None), default=0.0)
        kick = solve_kick(az, distance, duration, peak)
        if kick is None:
            continue
        rel = [times[k] - times[a] for k in range(a + 1, b + 1)]
        s, z, _ = simulate(az, kick[0], kick[1], rel)
        # The kick is solved for distance and peak; nudge the height by a straight
        # ramp so the ball also arrives at the height it's touched at (no pop at the touch).
        miss = out[b][2] - z[-1]
        for n, k in enumerate(range(a + 1, b)):
            w = s[n] / distance
            ramp = miss * rel[n] / duration
            out[k] = (ax + (bx - ax) * w, ay + (by - ay) * w, max(z[n] + ramp, 0.0))
        done += 1
    return out, done
