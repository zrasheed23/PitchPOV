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

Before that, follow_carries puts the carrier with the ball: during a
StatsBomb carry whose start the tracked ball backs, PFF's track for the
carrier can trail the ball by metres (Rashford v Wales: 6-10 m for two
seconds), and then no dribble is found at all. He's eased onto the ball.
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


def rebuild_dribbles(ball, times, player_frames, contacts, end, sb_carries=None):
    """Rebuild every dribble before frame `end`. Changes player_frames in place
    (smoothed carriers). sb_carries: StatsBomb's carries [(first, last, player
    id)], when it covers the clip: then only a stretch inside one of his carries
    is a dribble (anyone else keeping the ball near him isn't touching it).
    Returns (ball, added contacts, carries, rebuilt), where carries are [first,
    last, player id] stretches left to the viewer and rebuilt are the (first,
    last) stretches replaced here."""
    out = list(ball)
    added, carries = [], []
    rebuilt = []
    logged_by = {}
    for c in contacts:
        logged_by.setdefault(c["p"], []).append(c["f"])
    for first, last, pid in find_dribbles(ball, times, player_frames, contacts, end):
        if sb_carries is not None and not any(p == pid and a - 3 <= last and first <= b + 3 for a, b, p in sb_carries):
            continue
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


FOLLOW_M = 1.0  # during a carry the carrier's centre is at most this far from the ball
FOLLOW_MIN_S = 0.5
FOLLOW_AGREE_M = 3.0  # the tracked ball this close to StatsBomb's carry start backs it
FOLLOW_FIND_S = 0.5
FOLLOW_HIGH_Z = 1.0  # a ball higher than this (still dropping to him) doesn't place him
FOLLOW_MAX_M = 12.0  # more than this off is a wrong track, not a lagging one: left alone
FOLLOW_SIGMA_S = 0.5
FOLLOW_HOLD_S = 2.0  # his next StatsBomb action this soon after the carry: he stays with the ball until then
EASE_ACCEL = 4.0  # m/s^2 on top of his own run while easing on or off (as ball_rules.EASE_ACCEL)
EASE_CATCHUP_MPS = 3.0  # ...and at most this much speed (smoothstep peaks at 1.5 d / T)


def _ease_s(d):
    return max(0.5, math.sqrt(6 * d / EASE_ACCEL), 1.5 * d / EASE_CATCHUP_MPS)


def follow_carries(ball, tracked, times, player_frames, events, end):
    """Ease each StatsBomb carrier onto the ball for the length of his carry
    (events: the clip's StatsBomb events; only carries before frame `end`
    whose start the tracked ball backs), placed from the frames where the feed
    really has the ball (tracked) and filled in between. In place on player_frames; returns
    [(player id, first frame, largest move m)]."""
    out = []
    n = len(times)
    if n < 2:
        return out
    dt = (times[-1] - times[0]) / (n - 1)
    for e in events:
        dur = e["raw"].get("duration") or 0.0 if e["type"] == "Carry" else 0.0
        pid, a = e["p"], e["f"]
        if dur < FOLLOW_MIN_S or a >= end or pid not in player_frames[a]:
            continue
        b = min(next((k for k in range(a, n) if times[k] - times[a] >= dur), n - 1), end - 1)
        find = int(round(FOLLOW_FIND_S / dt))
        if not any(tracked[k] and ball[k] is not None and math.dist(ball[k][:2], e["xy"]) <= FOLLOW_AGREE_M
                   for k in range(max(a - find, 0), min(a + find, n - 1) + 1)):
            continue
        offsets = {}
        for k in range(a, b + 1):
            bk, pk = ball[k], player_frames[k].get(pid)
            if not tracked[k] or bk is None or pk is None or bk[2] > FOLLOW_HIGH_Z:
                continue  # only where the feed really has the ball, on the ground
            d = math.hypot(bk[0] - pk[0], bk[1] - pk[1])
            offsets[k] = ((bk[0] - pk[0]) * (1 - FOLLOW_M / d), (bk[1] - pk[1]) * (1 - FOLLOW_M / d)) if d > FOLLOW_M else (0.0, 0.0)
        if not offsets or max(math.hypot(*o) for o in offsets.values()) > FOLLOW_MAX_M:
            continue
        # Fill the frames with no placing ball from their neighbours, then smooth.
        keys = sorted(offsets)
        full = []
        for k in range(a, b + 1):
            if k in offsets:
                full.append(offsets[k])
                continue
            lo = max((j for j in keys if j < k), default=None)
            hi = min((j for j in keys if j > k), default=None)
            if lo is None or hi is None:
                full.append(offsets[lo if lo is not None else hi])
            else:
                w = (k - lo) / (hi - lo)
                full.append(tuple(p + (q - p) * w for p, q in zip(offsets[lo], offsets[hi])))
        sigma = FOLLOW_SIGMA_S / dt
        r = int(math.ceil(3 * sigma))
        smooth = []
        for i in range(len(full)):
            ws = [(math.exp(-((j - i) ** 2) / (2 * sigma * sigma)), full[j]) for j in range(max(i - r, 0), min(i + r + 1, len(full)))]
            tot = sum(w for w, _ in ws)
            smooth.append((sum(w * o[0] for w, o in ws) / tot, sum(w * o[1] for w, o in ws) / tot))
        # Ease on before the carry and off after it (or after his next action, if
        # it's soon: a carry ends as he passes or shoots), gently.
        on, off = smooth[0], smooth[-1]
        nxt = min((x["f"] for x in events if x["p"] == pid and x.get("on_ball") and b < x["f"] < end
                   and times[x["f"]] - times[b] <= FOLLOW_HOLD_S), default=None)
        held = nxt if nxt is not None else b
        t_on, t_off = _ease_s(math.hypot(*on)), _ease_s(math.hypot(*off))
        for k in range(n):
            if pid not in player_frames[k]:
                continue
            if a <= k <= b:
                o = smooth[k - a]
            elif b < k <= held:
                o = off
            elif k < a and times[a] - times[k] < t_on:
                u = 1 - (times[a] - times[k]) / t_on
                o = (on[0] * u * u * (3 - 2 * u), on[1] * u * u * (3 - 2 * u))
            elif k > held and times[k] - times[held] < t_off:
                u = 1 - (times[k] - times[held]) / t_off
                o = (off[0] * u * u * (3 - 2 * u), off[1] * u * u * (3 - 2 * u))
            else:
                continue
            x, y = player_frames[k][pid]
            player_frames[k][pid] = (x + o[0], y + o[1])
        out.append((pid, a, max(math.hypot(*o) for o in smooth)))
    return out
