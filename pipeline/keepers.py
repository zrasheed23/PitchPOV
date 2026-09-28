"""Keepers: PFF's track where it's believable, StatsBomb at the shot, and the dive.

PFF estimates players it can't see, and keepers are off camera most of the
time, so their tracks drift. The track is kept unless it's implausible, and
then corrected only as far as needed:
- out of his penalty area with the ball not near him (a sustained run out
  with the ball within SWEEP_BALL_M is sweeping, and is kept);
- outside the posts (sideways) while the ball is in his box;
- more than OFF_LINE_MAX_M from the line from the ball to the centre of his goal.
Around his own logged touches, where the tracking has him at the ball, he is
left as tracked. Corrections change no faster than SPRINT_MPS.

At the shot StatsBomb's freeze frame says where he really was: he is eased
onto that spot over FREEZE_EASE_S before the kick, held there while he reacts
and dives, and eased back onto his track afterwards (ease_to_freeze_frame).

The dive (plan_dive) goes toward where the shot crosses the line (StatsBomb):
after a REACTION_S reaction, late and partial if the ball gets there sooner,
as high as the ball, stretching up to REACH_M; farther than that he dives and
misses. A ball straight at him (within BLOCK_M) is a block or crouch, no dive.
Penalty keepers are placed by penalty.py until the kick.
"""

import math

HALF_L = 52.5
POST_Y = 3.66
AREA_DEPTH, AREA_HALF_W = 16.5, 20.16
IN_AREA_MARGIN = 0.3
SWEEP_MIN_S = 1.0
SWEEP_BALL_M = 8.0
OFF_LINE_MAX_M = 8.0
SPRINT_MPS = 7.5
TOUCH_KEEP_S = 0.5  # leave him as tracked this long either side of his own touch
TOUCH_TRACKED_M = 3.0  # ...if the tracking has him this close to the ball then
FREEZE_EASE_S = 1.0
DIVE_HOLD_S = 1.8  # held on his spot after the kick while he dives and lands
REACTION_S = 0.2
DIVE_FULL_S = 0.4  # take-off to full stretch
REACH_M = 2.5
BLOCK_M = 0.6


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
    u = min(max(side * xy[0], HALF_L - AREA_DEPTH + IN_AREA_MARGIN), HALF_L - 0.3)
    y = min(max(xy[1], -AREA_HALF_W + IN_AREA_MARGIN), AREA_HALF_W - IN_AREA_MARGIN)
    return side * u, y


def plausible_spot(p, ball_xy, side):
    """The nearest believable spot to p: within OFF_LINE_MAX_M of the ball-goal
    line, inside the posts if the ball is in his box, inside his area."""
    x, y = p
    if ball_xy is not None:
        gx = side * HALF_L
        dx, dy = ball_xy[0] - gx, ball_xy[1]
        n = math.hypot(dx, dy)
        if n > 1e-6:
            ux, uy = dx / n, dy / n
            along = (x - gx) * ux + y * uy
            cx, cy = gx + ux * along, uy * along  # nearest point on the ball-goal line
            off = math.hypot(x - cx, y - cy)
            if off > OFF_LINE_MAX_M:
                x, y = cx + (x - cx) * OFF_LINE_MAX_M / off, cy + (y - cy) * OFF_LINE_MAX_M / off
        if _in_area(ball_xy, side):
            y = min(max(y, -POST_Y), POST_Y)
    if not _in_area((x, y), side):
        x, y = _clamp_area((x, y), side)
    return x, y


def _sweeps(track, ball, times, side):
    """Frames of sustained runs out of his area with the ball near him."""
    keep = set()
    k, n = 0, len(track)
    while k < n:
        if _in_area(track[k], side, margin=1.0):
            k += 1
            continue
        j = k
        while j + 1 < n and not _in_area(track[j + 1], side, margin=1.0):
            j += 1
        near = any(ball[i] is not None and math.dist(ball[i][:2], track[i]) <= SWEEP_BALL_M for i in range(k, j + 1))
        if times[j] - times[k] >= SWEEP_MIN_S and near:
            keep.update(range(k, j + 1))
        k = j + 1
    return keep


def _limit(offsets, times, max_mps, fixed=()):
    """Offsets whose change never exceeds max_mps, as close to `offsets` as that
    allows (eased forward in time, then backward). `fixed` frames stay put."""
    out = list(offsets)
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
    """Correct implausible keeper positions up to `end` (the shot), in place.
    keepers: GK ids; touches: [(frame, player id)] logged touches; skip: keepers
    to leave alone (a penalty's). Returns {keeper id: largest correction (m)}."""
    moved = {}
    ball_at, last = [], None
    for b in ball:
        last = b if b is not None else last
        ball_at.append(last)
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
        offsets = []
        for k, p in enumerate(track):
            if k > end or k in keep:
                offsets.append((0.0, 0.0))
                continue
            b = ball_at[k]
            q = plausible_spot(p, b[:2] if b is not None else None, side)
            offsets.append((q[0] - p[0], q[1] - p[1]))
        fixed = keep | {k for k in range(len(track)) if k > end}
        offsets = _limit(offsets, times, SPRINT_MPS, fixed)
        moved[pid] = max(math.hypot(*o) for o in offsets)
        for k, (p, o) in enumerate(zip(track, offsets)):
            player_frames[k][pid] = (p[0] + o[0], p[1] + o[1])
    return moved


def defending_keeper(player_frames, keepers, side):
    """The keeper defending the goal at x = side * 52.5, or None."""
    return next((pid for pid, s in goals_of(player_frames, keepers).items() if s == side), None)


def ease_to_freeze_frame(player_frames, times, pid, kick, spot):
    """Ease the keeper onto StatsBomb's spot over FREEZE_EASE_S before the kick,
    hold him there for DIVE_HOLD_S after it, then ease back onto his track no
    faster than SPRINT_MPS. In place; returns how far PFF had him from it."""
    p = player_frames[kick].get(pid)
    if p is None:
        return None
    gap = math.dist(p, spot)
    t0 = times[kick]
    hold_end = next((k for k in range(kick, len(times)) if times[k] - t0 >= DIVE_HOLD_S), len(times) - 1)
    offsets = []
    for k, frame in enumerate(player_frames):
        q = frame.get(pid)
        if q is None:
            offsets.append((0.0, 0.0))
            continue
        if kick <= k <= hold_end:
            offsets.append((spot[0] - q[0], spot[1] - q[1]))
        elif k < kick:
            u = 1 - (t0 - times[k]) / FREEZE_EASE_S
            w = 0.0 if u <= 0 else u * u * (3 - 2 * u)
            offsets.append(((spot[0] - p[0]) * w, (spot[1] - p[1]) * w))
        else:
            offsets.append((0.0, 0.0))
    fixed = set(range(0, kick + 1)) | set(range(kick, hold_end + 1))
    offsets = _limit(offsets, times, SPRINT_MPS, fixed)
    for k, o in enumerate(offsets):
        q = player_frames[k].get(pid)
        if q is not None:
            player_frames[k][pid] = (q[0] + o[0], q[1] + o[1])
    return gap


def plan_dive(ball, times, player_frames, pid, kick, side):
    """How the keeper reacts to the shot: {"keeper", "kind": "dive" | "block",
    "f": take-off frame, "dir": +1/-1 along pitch y, "stretch": 0..1 of a full
    dive, "height": the ball's height at the line, "reached": he gets to it}.
    None if the ball never reaches his line."""
    k0 = player_frames[kick].get(pid)
    if k0 is None:
        return None
    cross = next((k for k in range(kick + 1, len(ball)) if ball[k] is not None and ball[k - 1] is not None
                  and side * ball[k][0] >= HALF_L), None)
    if cross is None:
        return None
    a, b = ball[cross - 1], ball[cross]
    w = (HALF_L - side * a[0]) / (side * b[0] - side * a[0]) if b[0] != a[0] else 1.0
    y, z = a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w
    t_cross = times[cross - 1] + (times[cross] - times[cross - 1]) * w
    lateral = y - k0[1]
    # Where he stands, off his line: the ball passes him there before the line.
    t_at_him = t_cross
    if cross - 1 > kick:
        for k in range(kick + 1, cross):
            if ball[k] is not None and side * ball[k][0] >= side * k0[0]:
                t_at_him = times[k]
                break
    info = {"keeper": pid, "height": round(z, 2), "dir": 1 if lateral >= 0 else -1}
    if abs(lateral) <= BLOCK_M:
        f = next((k for k in range(kick, len(times)) if times[k] - times[kick] >= REACTION_S), kick)
        return info | {"kind": "block", "f": f, "stretch": 0.0, "reached": True}
    start = times[kick] + REACTION_S
    time = max(t_at_him - start, 0.0)
    # Late: he takes off as the ball arrives and only gets part of the way.
    reach = min(abs(lateral), REACH_M) * min(time / DIVE_FULL_S, 1.0)
    take_off = min(start, t_at_him)
    f = next((k for k in range(kick, len(times)) if times[k] >= take_off), kick)
    return info | {"kind": "dive", "f": f, "stretch": round(max(reach, 0.3) / REACH_M, 2),
                   "reached": abs(lateral) - reach <= 0.3}
