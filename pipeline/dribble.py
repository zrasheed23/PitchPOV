"""Dribbles: the ball pushed along by one player, touch by touch.

PFF logs a dribble as one carry (BC) with no individual touches, and in between
the tracked ball wanders around the carrier's position (about 2 m/s of jitter),
so on screen the ball floats around a player whose feet never meet it.

For each dribble we:
1. Smooth the carrier's own track, since part of the wobble is the player's
   tracked position shaking, not the ball.
2. Add a touch every stride or two (timed to his running cycle, so it's the same
   foot each time) with the ball at his foot, a little ahead, on the side the
   tracking puts it.
3. Between touches, roll the ball as a real pushed ball (ball_physics.py): it
   leaves the foot quickly, slows on the grass and he catches up with it.

The added touches go into the clip's contacts (flagged "s": 1), so the viewer
swings the foot. If a dribble can't be rebuilt believably (the solver can't fit
a push, or the rebuilt ball ends up too far from him), the tracked ball is kept
and the stretch is listed as a carry, and the viewer holds the ball at his feet
there instead.
"""

import math

from ball_physics import apply_physics

CARRY_M = 1.8  # the ball this close to one player (on the ground plane) is his
PUSH_M = 3.5  # ...and still his if pushed up to this far ahead, briefly
PUSH_FRAMES = 10
LOW_Z = 0.7  # a dribbled ball stays below this
MIN_DRIBBLE_S = 0.8
STRIDE_M = 2.4  # metres per running cycle (two steps), as in the viewer
SPRINT_MPS = 6.0  # faster than this, a touch every two strides
MIN_TOUCH_GAP_S = 0.3
MAX_TOUCH_GAP_S = 0.6
FOOT_AHEAD_M = 0.5  # the ball at a touch, from the player's centre
SMOOTH_SIGMA_S = 0.2
SMOOTH_BLEND_S = 0.3  # ease the smoothed track in and out at the ends
MAX_FROM_CARRIER_M = 2.8  # a rebuilt ball farther than this from him is rejected
MAX_PUSH_MPS = 14.0  # ...and so is one moving faster than this between two frames


def _nearest(ball, players):
    best = (math.inf, None)
    for pid, (x, y) in players.items():
        d = math.hypot(ball[0] - x, ball[1] - y)
        if d < best[0]:
            best = (d, pid)
    return best


def find_dribbles(ball, times, player_frames, contacts, end):
    """[(first, last, player id)] stretches before frame `end` where one player
    has the ball at his feet for at least MIN_DRIBBLE_S."""
    touched_by = {c["f"]: c["p"] for c in contacts}
    out = []
    run = None  # [first, last, pid, frames since he was within CARRY_M]
    for i in range(min(end, len(ball))):
        b = ball[i]
        d, pid = _nearest(b, player_frames[i]) if b is not None and b[2] <= LOW_Z else (math.inf, None)
        other_touch = i in touched_by and run is not None and touched_by[i] != run[2]
        if run is not None and not other_touch:
            mine = player_frames[i].get(run[2])
            dm = math.hypot(b[0] - mine[0], b[1] - mine[1]) if b is not None and mine and b[2] <= LOW_Z else math.inf
            if dm <= CARRY_M and pid == run[2]:
                run[1], run[3] = i, 0
                continue
            if dm <= PUSH_M and run[3] < PUSH_FRAMES:
                run[3] += 1
                continue
        if run is not None and times[run[1]] - times[run[0]] >= MIN_DRIBBLE_S:
            out.append((run[0], run[1], run[2]))
        run = [i, i, pid, 0] if d <= CARRY_M else None
    if run is not None and times[run[1]] - times[run[0]] >= MIN_DRIBBLE_S:
        out.append((run[0], run[1], run[2]))
    return out


def smooth_carrier(player_frames, times, first, last, pid):
    """Gaussian-smooth one player's track over [first, last], easing in and out."""
    n = len(player_frames)
    blend = int(round(SMOOTH_BLEND_S / (times[1] - times[0]))) if n > 1 else 0
    sigma = SMOOTH_SIGMA_S / (times[1] - times[0]) if n > 1 else 1
    lo, hi = max(0, first - blend), min(n - 1, last + blend)
    r = int(math.ceil(sigma * 3))
    raw = {i: player_frames[i].get(pid) for i in range(max(0, lo - r), min(n, hi + r + 1))}
    for i in range(lo, hi + 1):
        if raw.get(i) is None:
            continue
        sx = sy = sw = 0.0
        for j in range(i - r, i + r + 1):
            p = raw.get(j)
            if p is None:
                continue
            w = math.exp(-((j - i) ** 2) / (2 * sigma * sigma))
            sx += p[0] * w
            sy += p[1] * w
            sw += w
        # Full smoothing inside the dribble, easing to the raw track outside it.
        edge = min(i - lo, hi - i)
        k = 1.0 if first <= i <= last else min(edge / max(blend, 1), 1.0)
        x, y = raw[i]
        player_frames[i][pid] = (x + (sx / sw - x) * k, y + (sy / sw - y) * k)


def _velocity(player_frames, i, pid, times, half=3):
    a = player_frames[max(0, i - half)].get(pid)
    b = player_frames[min(len(player_frames) - 1, i + half)].get(pid)
    dt = times[min(len(times) - 1, i + half)] - times[max(0, i - half)]
    if a is None or b is None or dt <= 0:
        return 0.0, 0.0
    return (b[0] - a[0]) / dt, (b[1] - a[1]) / dt


def touch_frames(first, last, pid, times, player_frames, logged):
    """Frames for the touches in a dribble: the logged ones by him, plus one every
    stride (two when sprinting), no closer than MIN_TOUCH_GAP_S and no further
    apart than MAX_TOUCH_GAP_S."""
    out = sorted(set(f for f in logged if first <= f <= last))
    run = 0.0
    last_touch = first
    for i in range(first + 1, last + 1):
        a, b = player_frames[i - 1].get(pid), player_frames[i].get(pid)
        if a and b:
            run += math.hypot(b[0] - a[0], b[1] - a[1])
        if i in out:
            run, last_touch = 0.0, i
            continue
        vx, vy = _velocity(player_frames, i, pid, times)
        per_touch = STRIDE_M * (2 if math.hypot(vx, vy) > SPRINT_MPS else 1)
        gap = times[i] - times[last_touch]
        upcoming = next((f for f in out if f > i), last)
        if times[upcoming] - times[i] < MIN_TOUCH_GAP_S:
            continue
        if gap >= MIN_TOUCH_GAP_S and (run >= per_touch or gap >= MAX_TOUCH_GAP_S):
            out.append(i)
            out.sort()
            run, last_touch = 0.0, i
    return out


def foot_spot(ball, player, velocity):
    """Where the ball is at a touch: FOOT_AHEAD_M from his centre, mostly in the
    direction he's running, turned toward the side the tracking has the ball."""
    fx, fy = velocity
    fl = math.hypot(fx, fy)
    tx, ty = ball[0] - player[0], ball[1] - player[1]
    tl = math.hypot(tx, ty)
    if fl < 1.0:  # barely moving: trust the tracked side
        fx, fy, fl = tx, ty, tl
    if fl < 1e-6:
        return player[0], player[1], 0.0
    dx, dy = fx / fl * 0.65, fy / fl * 0.65
    if tl > 1e-6:
        dx += tx / tl * 0.35
        dy += ty / tl * 0.35
    dl = math.hypot(dx, dy) or 1.0
    return player[0] + dx / dl * FOOT_AHEAD_M, player[1] + dy / dl * FOOT_AHEAD_M, 0.0


def rebuild_dribbles(ball, times, player_frames, contacts, end):
    """Rebuild every dribble before frame `end`. Changes player_frames in place
    (smoothed carriers). Returns (ball, added contacts, carries, rebuilt), where
    carries are [first, last, player id] stretches left to the viewer and rebuilt
    are the (first, last) stretches replaced here."""
    out = list(ball)
    added, carries = [], []
    rebuilt = []
    logged_by = {}
    for c in contacts:
        logged_by.setdefault(c["p"], []).append(c["f"])
    for first, last, pid in find_dribbles(ball, times, player_frames, contacts, end):
        smooth_carrier(player_frames, times, first, last, pid)
        touches = touch_frames(first, last, pid, times, player_frames, logged_by.get(pid, []))
        logged = set(logged_by.get(pid, []))
        trial = list(out)
        for f in touches:
            if f in logged or f in (first, last) or trial[f] is None:
                continue  # logged touches and the ends keep the tracked ball
            trial[f] = foot_spot(trial[f], player_frames[f][pid], _velocity(player_frames, f, pid, times))
        anchors = sorted(set(touches) | {first, last})
        # A dribble is played along the ground: flatten it so each push is solved as a roll.
        for k in range(first + 1, last):
            if trial[k] is not None and k not in anchors:
                trial[k] = (trial[k][0], trial[k][1], 0.0)
        rolled, _ = apply_physics(trial, times, anchors, last)
        # Pushes the solver couldn't fit (very short ones) glide straight between touches.
        for a, b in zip(anchors, anchors[1:]):
            for k in range(a + 1, b):
                if rolled[k] is not None and rolled[k] == trial[k] and rolled[a] and rolled[b]:
                    w = (times[k] - times[a]) / (times[b] - times[a])
                    rolled[k] = tuple(p + (q - p) * w for p, q in zip(rolled[a], rolled[b]))
        if not _believable(rolled, times, player_frames, first, last, pid):
            carries.append([first, last, pid])
            continue
        out[first:last + 1] = rolled[first:last + 1]
        added += [{"f": f, "p": pid, "b": "F", "s": 1} for f in touches if f not in logged and f not in (first, last)]
        rebuilt.append((first, last))
    return out, added, carries, rebuilt


def _believable(ball, times, player_frames, first, last, pid):
    """The rebuilt ball stays near him and never moves faster than a push."""
    for k in range(first, last + 1):
        b, p = ball[k], player_frames[k].get(pid)
        if b is None or p is None or math.hypot(b[0] - p[0], b[1] - p[1]) > MAX_FROM_CARRIER_M:
            return False
        if k > first and ball[k - 1] is not None:
            dt = times[k] - times[k - 1]
            if dt > 0 and math.hypot(b[0] - ball[k - 1][0], b[1] - ball[k - 1][1]) / dt > MAX_PUSH_MPS:
                return False
    return True
