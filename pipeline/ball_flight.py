"""Keep the ball on a straight line whenever nobody is touching it.

Tracking noise, feed switches and gap estimates can all bend the ball's path in
mid-air or mid-roll, which reads as the ball changing direction by itself. A
ball only changes direction when a player touches it (or it hits the post, net
or ground, which only changes its height or happens after the shot). So:

- Anchor frames are the logged touches (contacts.py) plus every frame where a
  player is close enough to be playing the ball (dribbles aren't logged touch
  by touch).
- Between two anchors the ball is in free flight: its ground track becomes the
  straight line between them. Its progress along that line follows how far it
  travelled in the data, so it still slows down naturally; its height is kept,
  so bounces and lofted balls stay as they were.

Only frames before the shot are changed; the shot itself is handled by the
goal-mouth correction.
"""

import math

CLOSE_M = 1.5  # a player this close (on the ground plane) may be playing the ball
REACH_Z = 2.3  # ...if the ball is low enough to reach
KINK_DEG = 25.0  # a turn sharper than this, away from any player, is a kink (for the audit)
KINK_MIN_MPS = 3.0


def anchor_frames(ball, player_frames, contact_frames):
    """Frames where a player is (or may be) touching the ball."""
    anchors = set(contact_frames)
    for i, b in enumerate(ball):
        if b is None or b[2] > REACH_Z:
            continue
        for x, y in player_frames[i].values():
            if math.hypot(b[0] - x, b[1] - y) <= CLOSE_M:
                anchors.add(i)
                break
    return anchors


def straighten_free_flight(ball, player_frames, contact_frames, end):
    """Straighten every free-flight stretch that ends at or before frame `end`.
    Returns (ball, number of stretches straightened)."""
    out = list(ball)
    anchors = sorted(a for a in anchor_frames(ball, player_frames, contact_frames) if a <= end)
    fixed = 0
    for a, b in zip(anchors, anchors[1:]):
        if b - a < 3 or out[a] is None or out[b] is None or any(out[k] is None for k in range(a + 1, b)):
            continue
        # Distance travelled along the tracked path, frame by frame.
        cum = [0.0]
        for k in range(a + 1, b + 1):
            cum.append(cum[-1] + math.hypot(out[k][0] - out[k - 1][0], out[k][1] - out[k - 1][1]))
        total = cum[-1]
        ax, ay = out[a][0], out[a][1]
        bx, by = out[b][0], out[b][1]
        for n, k in enumerate(range(a + 1, b), start=1):
            w = cum[n] / total if total > 1e-6 else n / (b - a)
            out[k] = (ax + (bx - ax) * w, ay + (by - ay) * w, out[k][2])
        fixed += 1
    return out, fixed


def count_kinks(ball, times, player_frames, contact_frames, end=None):
    """Sharp turns of the ball with nobody near it (for the audit)."""
    anchors = anchor_frames(ball, player_frames, contact_frames)
    near = set()
    for a in anchors:
        near.update(range(a - 2, a + 3))
    stop = len(ball) - 3 if end is None else min(end, len(ball) - 3)
    kinks = 0
    for i in range(3, stop):
        p, q, r = ball[i - 3], ball[i], ball[i + 3]
        if p is None or q is None or r is None or i in near:
            continue
        u = (q[0] - p[0], q[1] - p[1])
        v = (r[0] - q[0], r[1] - q[1])
        nu, nv = math.hypot(*u), math.hypot(*v)
        dt = times[i + 3] - times[i]
        if dt <= 0 or nu / dt < KINK_MIN_MPS or nv / dt < KINK_MIN_MPS:
            continue
        cos = (u[0] * v[0] + u[1] * v[1]) / (nu * nv)
        if math.degrees(math.acos(max(-1.0, min(1.0, cos)))) > KINK_DEG:
            kinks += 1
    return kinks
