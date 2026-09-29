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

The dive (plan_dive) goes toward where the shot reaches him (the crossing,
StatsBomb's, for a keeper on his line): no earlier than REACTION_S after the
kick and WAIT_S before the ball arrives, late and partial if it gets there
sooner, as high as the ball, stretching up to REACH_M; farther than that he
dives and misses. A ball within BLOCK_M of him is a block or crouch at its
height, no dive, and it beats him.
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
REACTION_S = 0.25
WAIT_S = 0.55  # he takes off no earlier than this before the ball reaches him
LATE_MIN = 0.15  # the least of a full stretch a late dive gets
HANDS_CLEAR_M = 0.1
DIVE_FULL_S = 0.4  # take-off to full stretch
REACH_M = 2.5
BLOCK_M = 0.9
EASE_ACCEL = 4.0  # as ball_rules.EASE_ACCEL: corrections ease in no harder than this (m/s^2)


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


SMOOTH_S = 0.4  # a keeper correction is smoothed over this (sigma), so he doesn't lurch as it changes


def _smooth(offsets, times, fixed=()):
    """Gaussian-smoothed offsets (sigma SMOOTH_S); `fixed` frames keep theirs."""
    n = len(offsets)
    if n < 2:
        return list(offsets)
    sigma = SMOOTH_S / ((times[-1] - times[0]) / (n - 1))
    r = int(math.ceil(3 * sigma))
    weights = [math.exp(-(j * j) / (2 * sigma * sigma)) for j in range(r + 1)]
    out = []
    for k in range(n):
        if k in fixed:
            out.append(offsets[k])
            continue
        sx = sy = sw = 0.0
        for j in range(max(0, k - r), min(n, k + r + 1)):
            w = weights[abs(j - k)]
            sx += offsets[j][0] * w
            sy += offsets[j][1] * w
            sw += w
        out.append((sx / sw, sy / sw))
    return out


def place_keepers(player_frames, times, ball, keepers, end, touches=(), skip=()):
    """Correct implausible keeper positions up to `end` (the shot), in place;
    after it the correction at `end` is held (dropping it there sent keepers
    sprinting off at the kick; the post-goal settle fades it slowly).
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
        last = min(end, len(track) - 1)
        for k in range(last + 1, len(track)):
            offsets[k] = offsets[last]  # held after the shot
        fixed = keep | {k for k in range(len(track)) if k > end}
        offsets = _smooth(offsets, times, fixed)
        offsets = _limit(offsets, times, SPRINT_MPS, fixed)
        moved[pid] = max(math.hypot(*o) for o in offsets)
        for k, (p, o) in enumerate(zip(track, offsets)):
            player_frames[k][pid] = (p[0] + o[0], p[1] + o[1])
    return moved


def defending_keeper(player_frames, keepers, side):
    """The keeper defending the goal at x = side * 52.5, or None."""
    return next((pid for pid, s in goals_of(player_frames, keepers).items() if s == side), None)


def ease_to_freeze_frame(player_frames, times, pid, kick, spot):
    """Ease the keeper onto StatsBomb's spot over FREEZE_EASE_S (longer for a
    big move, so he accelerates no harder than EASE_ACCEL) before the kick,
    hold him there for DIVE_HOLD_S after it, then ease back onto his track no
    faster than SPRINT_MPS. In place; returns how far PFF had him from it."""
    p = player_frames[kick].get(pid)
    if p is None:
        return None
    gap = math.dist(p, spot)
    t0 = times[kick]
    ease_s = max(FREEZE_EASE_S, math.sqrt(6 * gap / EASE_ACCEL))  # no harder than EASE_ACCEL
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
            u = 1 - (t0 - times[k]) / ease_s
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
    "f": the frame he reacts, "dir": +1/-1 along pitch y toward the ball,
    "stretch": 0..1 of a full dive, "height": the ball's height where it
    reaches him, "reached": he gets to it, "gap": metres from him to the ball
    there, "arrive_f": the frame it gets there}. None if the ball never
    reaches his line.

    Where it reaches him: where the ball comes level with him (for a keeper on
    his line, the crossing point, which is StatsBomb's). A ball within BLOCK_M
    of him there is a block or crouch at its height, not a dive. He never
    takes off before kick + REACTION_S, nor more than WAIT_S before the ball
    gets there (a slow shot: he waits, then dives); a ball that arrives sooner
    than DIVE_FULL_S after he can react gets a late, partial dive."""
    k0 = player_frames[kick].get(pid)
    if k0 is None:
        return None
    cross = next((k for k in range(kick + 1, len(ball)) if ball[k] is not None and ball[k - 1] is not None
                  and side * ball[k][0] >= HALF_L), None)
    if cross is None:
        return None
    depth = min(side * k0[0], HALF_L)
    arrive = next((k for k in range(kick + 1, cross + 1) if ball[k] is not None and ball[k - 1] is not None
                   and side * ball[k][0] >= depth), cross)
    a, b = ball[arrive - 1], ball[arrive]
    level = depth if arrive < cross else HALF_L
    w = (level - side * a[0]) / (side * b[0] - side * a[0]) if b[0] != a[0] else 1.0
    w = min(max(w, 0.0), 1.0)
    y, z = a[1] + (b[1] - a[1]) * w, a[2] + (b[2] - a[2]) * w
    t_arrive = times[arrive - 1] + (times[arrive] - times[arrive - 1]) * w
    keeper = player_frames[arrive].get(pid, k0)
    lateral = y - keeper[1]
    # A shot across him: what matters is how close it passes (its closest approach).
    near = min((k for k in range(kick + 1, cross + 1) if ball[k] is not None and player_frames[k].get(pid)),
               key=lambda k: math.hypot(ball[k][0] - player_frames[k][pid][0], ball[k][1] - player_frames[k][pid][1]),
               default=None)
    if near is not None:
        q = player_frames[near][pid]
        closest = math.hypot(ball[near][0] - q[0], ball[near][1] - q[1])
        if closest < abs(lateral):
            arrive, keeper, z = near, q, ball[near][2]
            lateral = math.copysign(closest, ball[near][1] - q[1] if abs(ball[near][1] - q[1]) > 1e-6 else lateral or 1.0)
            t_arrive = times[near]
    react = times[kick] + REACTION_S
    start = max(react, t_arrive - WAIT_S)
    f = next((k for k in range(kick, len(times)) if times[k] >= start - 1e-9), len(times) - 1)
    info = {"keeper": pid, "height": round(z, 2), "dir": 1 if lateral >= 0 else -1, "f": f,
            "gap": round(abs(lateral), 2), "arrive_f": arrive}
    if abs(lateral) < BLOCK_M:
        return info | {"kind": "block", "stretch": 0.0, "reached": True}
    time = max(t_arrive - start, 0.0)
    reach = min(abs(lateral), REACH_M) * min(max(time / DIVE_FULL_S, LATE_MIN), 1.0)
    # It's a goal: his fingertips end HANDS_CLEAR_M short of it, never through it.
    reach = min(reach, abs(lateral) - HANDS_CLEAR_M)
    return info | {"kind": "dive", "stretch": round(max(reach, 0.3) / REACH_M, 2), "reached": False,
                   "short": round(abs(lateral) - reach, 2)}


BODY_R = ((0.9, 0.18), (1.5, 0.22), (1.85, 0.12))  # legs, torso, head (as accuracy.py)
BALL_R = 0.11
CLEAR_M = 0.1  # the ball clears him by this much
# A beaten keeper is never in the shot's path: from knee to head height his
# hands (out in the ready stance, this far from his centre) must miss it too.
HANDS_M = 0.5
HANDS_Z = (0.6, 1.85)


def keeper_radius(z):
    """How far from a set keeper's centre he blocks a ball at height z: his
    hands from knee to head height, else his body; None above his head."""
    if HANDS_Z[0] <= z <= HANDS_Z[1]:
        return HANDS_M
    return next((r for top, r in BODY_R if z <= top), None)
LEGS_Z, LEGS_GAP = 0.35, 0.22  # a ball this low and this central goes through his legs (legs apart: feet ~0.4 m out)
FREEZE_SLACK_M = 1.0  # he may stand at most this far from StatsBomb's spot to let it past


def beat_keeper(dive, player_frames, times, kick, freeze_spot=None, ball_at=None, tracked_spot=None):
    """A block the ball can't clear: through his legs if it's low and central,
    else he stands just far enough to the side (at most FREEZE_SLACK_M from
    StatsBomb's spot, or from where he was) for it to clear him, hands
    included (keeper_radius), by CLEAR_M, from the kick until it's past. He
    steps to the side of the shot's path where PFF tracked him (tracked_spot,
    the other measurement of where he was), else the side he's on. In place;
    returns the plan with "through" ("legs", "side", "over") and "shifted" (m)."""
    if not dive or dive["kind"] != "block":
        return dive
    z, gap = dive["height"], dive["gap"]
    r = keeper_radius(z)
    if r is None:
        return dive | {"through": "over"}
    if z <= LEGS_Z and gap <= LEGS_GAP:
        return dive | {"through": "legs"}
    pid = dive["keeper"]
    here = player_frames[kick][pid]
    base = freeze_spot or here
    end = min(dive["arrive_f"] + int(round(0.6 / max(times[1] - times[0], 1e-6))), len(times) - 1)

    def closest():
        """(clearance, distance, closest ball point (x, y), his position, the
        ball's direction there) while the ball goes past."""
        best = None
        for k in range(kick, min(end, len(ball_at) - 1)):
            p0, p1, q = ball_at[k], ball_at[k + 1], player_frames[k][pid]
            if p0 is None or p1 is None:
                continue
            vx, vy = p1[0] - p0[0], p1[1] - p0[1]
            L = vx * vx + vy * vy
            t = 0.0 if L < 1e-9 else min(max(((q[0] - p0[0]) * vx + (q[1] - p0[1]) * vy) / L, 0.0), 1.0)
            cx, cy, cz = p0[0] + vx * t, p0[1] + vy * t, p0[2] + (p1[2] - p0[2]) * t
            r = keeper_radius(cz)
            if r is not None:
                d = math.hypot(q[0] - cx, q[1] - cy)
                if best is None or d - r < best[0]:
                    best = (d - r - BALL_R, d, (cx, cy), q, (vx, vy))
        return best

    total = 0.0
    for _ in range(4):
        best = closest()
        if best is None or best[0] >= CLEAR_M - 0.005:
            break
        clearance, _, (cx, cy), q, (vx, vy) = best
        room = FREEZE_SLACK_M - math.dist(player_frames[kick][pid], base)
        shift = min(CLEAR_M - clearance, max(room, 0.0))
        if shift <= 0.005:
            break
        ux, uy = q[0] - cx, q[1] - cy
        n = math.hypot(ux, uy)
        ux, uy = (ux / n, uy / n) if n > 1e-6 else (0.0, -dive["dir"])
        vn = math.hypot(vx, vy)
        if tracked_spot is not None and vn > 1e-9 and total == 0.0:
            # Square to the path, toward the side PFF tracked him on (if it's clearly one side).
            px, py = -vy / vn, vx / vn
            lean = (tracked_spot[0] - cx) * px + (tracked_spot[1] - cy) * py
            if abs(lean) > r + BALL_R:
                ux, uy = (px, py) if lean > 0 else (-px, -py)
                shift = min(CLEAR_M + r + BALL_R + ((q[0] - cx) * ux + (q[1] - cy) * uy) * -1.0, max(room, 0.0))
        for k, frame in enumerate(player_frames):
            if pid not in frame:
                continue
            ramp = math.sqrt(6 * shift / EASE_ACCEL)
            if kick <= k <= end:
                w = 1.0
            elif k < kick:
                w = max(0.0, 1 - (times[kick] - times[k]) / max(0.3, ramp))
            else:
                w = max(0.0, 1 - (times[k] - times[end]) / max(0.5, ramp))
            if w > 0:
                x, y = frame[pid]
                e = w * w * (3 - 2 * w)
                frame[pid] = (x + ux * shift * e, y + uy * shift * e)
        total += shift
    best = closest()
    gap = best[1] if best else gap
    side = dive["dir"]
    if best is not None and abs(best[2][1] - best[3][1]) > 1e-6:
        side = 1 if best[2][1] > best[3][1] else -1  # he may have stepped to the other side of it
    return dive | {"through": "side", "shifted": round(total, 2), "gap": round(gap, 2), "dir": side}
