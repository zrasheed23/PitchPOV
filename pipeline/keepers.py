"""Keepers stay where keepers stand.

PFF estimates players it can't see, and keepers are off camera most of the
time, so their tracks drift: Lloris runs 20 m out of his goal before Di
María's goal in the final. For every keeper in every clip:
- He stays in his penalty area. He only leaves it when the tracking has him
  out there for SWEEP_MIN_S or more with the ball within SWEEP_BALL_M of him
  (sweeping, or coming for a through ball); that run is kept as tracked.
- When the ball is in his half, he stands on the line from the ball to the
  centre of his goal, OFF_LINE_M[0] m out of it with the ball close and up to
  OFF_LINE_M[1] m with it far away.
- Around his own logged touches (where the tracking has him at the ball) he
  is left where the tracking has him.
- The corrected track never moves faster than SPRINT_MPS: it is eased onto
  and off the rule, both ways in time.
Until the shot; after it he eases back onto his tracked run (the viewer
animates the dive). Penalty keepers are placed by penalty.py instead.
"""

import math

HALF_L = 52.5
AREA_DEPTH, AREA_HALF_W = 16.5, 20.16
IN_AREA_MARGIN = 0.3
SWEEP_MIN_S = 1.0
SWEEP_BALL_M = 8.0
OFF_LINE_M = (1.0, 6.0)
BALL_NEAR_M, BALL_FAR_M = 8.0, 40.0  # ball this close to his goal: OFF_LINE_M[0]; this far: OFF_LINE_M[1]
MIN_OFF_LINE_M = 0.3
SPRINT_MPS = 7.5
TOUCH_KEEP_S = 0.5  # leave him as tracked this long either side of his own touch
TOUCH_TRACKED_M = 3.0  # ...if the tracking has him this close to the ball then


def goals_of(player_frames, keepers):
    """{keeper id: +1/-1, the end he defends}: the two keepers get the two ends
    whichever way their average positions sit closer to them."""
    mean = {}
    for pid in keepers:
        xs = [f[pid][0] for f in player_frames if pid in f]
        if xs:
            mean[pid] = sum(xs) / len(xs)
    ids = sorted(mean)
    if len(ids) == 2:
        a, b = ids
        if abs(mean[a] - HALF_L) + abs(mean[b] + HALF_L) <= abs(mean[a] + HALF_L) + abs(mean[b] - HALF_L):
            return {a: 1, b: -1}
        return {a: -1, b: 1}
    return {pid: (1 if x >= 0 else -1) for pid, x in mean.items()}


def _in_area(xy, side, margin=0.0):
    return side * xy[0] >= HALF_L - AREA_DEPTH - margin and abs(xy[1]) <= AREA_HALF_W + margin


def _clamp_area(xy, side):
    u = min(max(side * xy[0], HALF_L - AREA_DEPTH + IN_AREA_MARGIN), HALF_L - MIN_OFF_LINE_M)
    y = min(max(xy[1], -AREA_HALF_W + IN_AREA_MARGIN), AREA_HALF_W - IN_AREA_MARGIN)
    return side * u, y


def line_spot(ball_xy, side):
    """On the line from the ball to the centre of his goal, 1-6 m out of it."""
    gx = side * HALF_L
    dx, dy = ball_xy[0] - gx, ball_xy[1]
    d = math.hypot(dx, dy)
    w = min(max((d - BALL_NEAR_M) / (BALL_FAR_M - BALL_NEAR_M), 0.0), 1.0)
    off = OFF_LINE_M[0] + (OFF_LINE_M[1] - OFF_LINE_M[0]) * w
    if d < 1e-6:
        return gx - side * off, 0.0
    return _clamp_area((gx + dx / d * min(off, d), dy / d * min(off, d)), side)


def _sweeps(track, ball, times, side):
    """Frames of sustained runs out of his area with the ball near him."""
    keep = set()
    k = 0
    n = len(track)
    while k < n:
        if track[k] is None or _in_area(track[k], side, margin=1.0):
            k += 1
            continue
        j = k
        while j + 1 < n and track[j + 1] is not None and not _in_area(track[j + 1], side, margin=1.0):
            j += 1
        near = any(ball[i] is not None and math.dist(ball[i][:2], track[i]) <= SWEEP_BALL_M for i in range(k, j + 1))
        if times[j] - times[k] >= SWEEP_MIN_S and near:
            keep.update(range(k, j + 1))
        k = j + 1
    return keep


def _limit_speed(target, times, max_mps, fixed=()):
    """The track nearest `target` that never moves faster than max_mps: eased
    forward in time, then backward, so it gets where it must be on time.
    Frames in `fixed` stay exactly on target (the others ease around them)."""
    out = list(target)
    for rng in (range(1, len(out)), range(len(out) - 2, -1, -1)):
        for k in rng:
            if k in fixed:
                continue
            j = k - 1 if rng.step == 1 else k + 1
            step = max_mps * abs(times[k] - times[j])
            dx, dy = out[k][0] - out[j][0], out[k][1] - out[j][1]
            d = math.hypot(dx, dy)
            if d > step:
                out[k] = (out[j][0] + dx / d * step, out[j][1] + dy / d * step)
    return out


def place_keepers(player_frames, times, ball, keepers, end, touches=(), skip=()):
    """Apply the keeper rules to frames up to `end` (the shot), in place.
    keepers: GK ids; touches: [(frame, player id)] logged touches; skip:
    keepers to leave alone (a penalty's). Returns {keeper id: largest move (m)}."""
    moved = {}
    last_ball = None
    ball_at = []
    for b in ball:
        last_ball = b if b is not None else last_ball
        ball_at.append(last_ball)
    for pid, side in goals_of(player_frames, keepers).items():
        if pid in skip:
            continue
        track = [f.get(pid) for f in player_frames]
        if any(p is None for p in track):
            continue
        keep = _sweeps(track, ball_at, times, side)
        for f, who in touches:
            if who == pid and ball_at[f] is not None and math.dist(ball_at[f][:2], track[f]) <= TOUCH_TRACKED_M:
                keep.update(k for k in range(len(track)) if abs(times[k] - times[f]) <= TOUCH_KEEP_S)
        target = []
        for k, p in enumerate(track):
            b = ball_at[k]
            if k > end or k in keep:
                target.append(p)
            elif b is not None and side * b[0] > 0:  # the ball in his half
                target.append(line_spot(b[:2], side))
            else:
                target.append(_clamp_area(p, side) if not _in_area(p, side) else p)
        # After the shot he drifts back onto his tracked run.
        new = _limit_speed(target, times, SPRINT_MPS, keep)
        moved[pid] = max(math.dist(a, b) for a, b in zip(track, new))
        for k, xy in enumerate(new):
            player_frames[k][pid] = xy
    return moved
