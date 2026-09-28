"""Make the ball obey the touch rule (touch_rule.py).

1. The shot (fly_shot): the ball is at the shooter's foot (head, hands) when
   he hits it, then flies as one kick straight to where it crosses the line
   (StatsBomb's end location after the goal-mouth correction), at a real shot
   speed. It only changes direction on the way if a logged keeper or defender
   touch has the ball near him. Past the line it's simulated into the net
   (net.py), carrying on exactly as it crossed.
2. Every other touch has the ball within reach of the player touching it.
3. Between two touches (enforce_touch_rule): wherever the ball still turns,
   changes speed or rises by itself, either
   (a) a player of either team is within ADD_TOUCH_M: that's a touch the event
       data missed. Add it (foot, head if high, hands for a keeper) and fly the
       ball to him and on from him; or
   (b) nobody is near: fly the ball as one kick from the previous touch to the
       next one, straight on the ground.
   Flights use ball_physics (gravity, drag, bounces, rolling); where no real
   kick fits, the ball goes in a straight line at a steady speed.
"""

import math

from ball_physics import MIN_FLIGHT_S, simulate, solve_kick, solve_to_height
from goal_mouth import GOAL_LINE_X
from net import into_net
from touch_rule import REACH_M, REACH_Z, WINDOW, violations

ADD_TOUCH_M = 2.5
FOOT_M = 0.35  # the ball at a foot touch, from the player's centre
HEAD_Z = 1.8
MAX_JOIN_S = 15.0  # longer than this between touches: a straight line, no solver
# Heights the ball can be at when touched (m): feet, head, hands.
PART_Z = {"H": (1.5, 2.4), "X": (0.3, 2.6)}
FOOT_Z = (0.0, 1.2)
HEAD_FROM_Z = 1.3  # a ball higher than this is headed (or handled by a keeper)
KEEPER_HANDS_Z = 0.6
AREA_X, AREA_Y = 52.5 - 16.5, 20.16
# Shot speed over its flight (m/s): slower is the tracking losing it, faster is the feed jumping.
MIN_SHOT_MPS = {"H": 11.0, "X": 8.0}
MIN_KICK_MPS = 20.0
MAX_SHOT_MPS = 35.0
MIN_SHOT_S = 0.25
PEAK_TRIES = 8


def part_z(part):
    return PART_Z.get(part, FOOT_Z)


def touch_spot(player, ball, part, toward=None):
    """Where the ball is when `player` (x, y) touches it with `part`: at his
    foot FOOT_M out toward `toward` (default: where the ball is), at his head,
    or in his hands; its height kept inside what that part can reach."""
    tx, ty = toward if toward is not None else ball[:2]
    dx, dy = tx - player[0], ty - player[1]
    n = math.hypot(dx, dy)
    off = 0.15 if part in ("H", "X") else FOOT_M
    x, y = (player[0] + dx / n * off, player[1] + dy / n * off) if n > 1e-6 else player
    lo, hi = part_z(part)
    z = HEAD_Z if part == "H" and not lo <= ball[2] <= hi else min(max(ball[2], lo), hi)
    return x, y, z


def _part(z, pid, players, keepers):
    """Foot, head or (a keeper in his own area) hands, for a ball at height z."""
    if pid in keepers and z > KEEPER_HANDS_Z and _in_own_area(players[pid], keepers[pid]):
        return "X"
    return "H" if z > HEAD_FROM_Z else "F"


def _in_own_area(xy, side_sign):
    """side_sign: +1 if his goal is at x = +52.5."""
    return side_sign * xy[0] > AREA_X and abs(xy[1]) < AREA_Y


def _straight(ball, times, a, b):
    pa, pb = ball[a], ball[b]
    for k in range(a + 1, b):
        w = (times[k] - times[a]) / (times[b] - times[a])
        ball[k] = tuple(p + (q - p) * w for p, q in zip(pa, pb))


def _kick(z0, distance, duration, peak, z_end, z_range):
    """A kick that covers `distance` in `duration` and arrives inside z_range,
    as near z_end as it can: the loft is adjusted until it does. Returns
    (u0, w0, arrival height) or None."""
    lo, hi = z_range
    kick = solve_kick(z0, distance, duration, peak)
    if kick is None:
        return None
    for _ in range(PEAK_TRIES):
        z = simulate(z0, kick[0], kick[1], [duration])[1][-1]
        if lo <= z <= hi and abs(z - z_end) < 0.25:
            return kick[0], kick[1], z
        # Too low: loft it more; too high: less.
        peak = max(0.0, peak + (z_end - z) * 0.8)
        nxt = solve_kick(z0, distance, duration, peak)
        if nxt is None:
            break
        kick = nxt
    z = simulate(z0, kick[0], kick[1], [duration])[1][-1]
    return (kick[0], kick[1], z) if lo <= z <= hi else None


def fly(ball, times, a, b, z_range=FOOT_Z, fixed_end=False):
    """Re-draw frames a+1..b-1 as one kick from ball[a] to ball[b], in place.
    The arrival height may move inside z_range (the part touching it at b),
    unless fixed_end (the shot: its start is set). Returns "physics" or "straight"."""
    pa, pb = ball[a], ball[b]
    if fixed_end:
        z_range = (pb[2] - 0.25, pb[2] + 0.25)
    duration = times[b] - times[a]
    distance = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
    if distance >= 0.3 and MIN_FLIGHT_S <= duration <= MAX_JOIN_S:
        peak = max(ball[k][2] for k in range(a, b + 1) if ball[k] is not None)
        kick = _kick(pa[2], distance, duration, peak, pb[2], z_range)
        if kick is not None:
            u0, w0, z_end = kick
            rel = [times[k] - times[a] for k in range(a + 1, b + 1)]
            s, z, _ = simulate(pa[2], u0, w0, rel)
            miss = pb[2] - z_end if fixed_end else 0.0  # at most 0.25 m, spread over the flight
            for n, k in enumerate(range(a + 1, b)):
                w = s[n] / distance
                ball[k] = (pa[0] + (pb[0] - pa[0]) * w, pa[1] + (pb[1] - pa[1]) * w,
                           max(z[n] + miss * rel[n] / duration, 0.0))
            if not fixed_end:
                ball[b] = (pb[0], pb[1], z_end)
            return "physics"
        lo, hi = z_range
        kick = solve_to_height(pa[2], distance, duration, min(max(pb[2], lo), hi))
        if kick is not None:  # the flattest kick that meets him at a height he can reach
            rel = [times[k] - times[a] for k in range(a + 1, b + 1)]
            s, z, _ = simulate(pa[2], kick[0], kick[1], rel)
            for n, k in enumerate(range(a + 1, b)):
                w = s[n] / distance
                ball[k] = (pa[0] + (pb[0] - pa[0]) * w, pa[1] + (pb[1] - pa[1]) * w, max(z[n], 0.0))
            ball[b] = (pb[0], pb[1], z[-1]) if not fixed_end else pb
            return "physics"
    _straight(ball, times, a, b)
    if max(pa[2], pb[2]) <= 0.3:  # along the ground
        for k in range(a + 1, b):
            ball[k] = (ball[k][0], ball[k][1], 0.0)
    return "straight"


def shot_contact(contacts, goal, goal_index, window=12):
    """The scorer's touch that is the shot (the last one by him up to the goal
    frame, at most `window` frames before it), or None."""
    return next((c for c in reversed(contacts)
                 if goal_index - window <= c["f"] <= goal_index and c["p"] == goal["scorerId"]), None)


KICK_LOOKBACK_S = 1.0
KICK_NEAR_M = 1.5
KICK_MAX_M = 3.0


def find_kick(ball, times, player_frames, shooter, frame, part="F"):
    """The frame the shot really left the shooter: PFF often logs it late, when
    the tracked ball is already metres toward goal (or at the keeper). Looking
    back up to KICK_LOOKBACK_S from the logged frame: the last frame with the
    ball within KICK_NEAR_M of him (and at a height he can play), else the
    nearest one if within KICK_MAX_M, else the logged frame."""
    lo, hi = part_z(part)
    near = []
    for k in range(frame, -1, -1):
        if times[frame] - times[k] > KICK_LOOKBACK_S:
            break
        b, p = ball[k], player_frames[k].get(shooter)
        if b is None or p is None or not lo - 0.3 <= b[2] <= hi + 0.3:
            continue
        near.append((math.hypot(b[0] - p[0], b[1] - p[1]), k))
    close = [k for d, k in near if d <= KICK_NEAR_M]
    if close:
        return max(close)
    best = min(near, default=None)
    return best[1] if best is not None and best[0] <= KICK_MAX_M else frame


SHOOTER_TRUST_M = 3.0  # farther than this from the ball at the kick, his tracked position is the wrong one
SHOOTER_EASE_S = 1.5
SHOOTER_CATCHUP_MPS = 4.0  # the most he's moved on top of his own run


def move_shooter(player_frames, times, pid, kick, ball_xy, toward):
    """Ease the shooter onto the ball at the kick: his centre FOOT_M behind it
    (away from `toward`), blending in before the kick and out after it, over
    at least SHOOTER_EASE_S and slowly enough that he never gains more than
    SHOOTER_CATCHUP_MPS on his tracked run. In place; returns metres moved."""
    p = player_frames[kick][pid]
    dx, dy = toward[0] - ball_xy[0], toward[1] - ball_xy[1]
    n = math.hypot(dx, dy) or 1.0
    spot = (ball_xy[0] - dx / n * FOOT_M, ball_xy[1] - dy / n * FOOT_M)
    off = (spot[0] - p[0], spot[1] - p[1])
    gap = math.hypot(*off)
    ease = max(SHOOTER_EASE_S, gap / SHOOTER_CATCHUP_MPS)
    for k, frame in enumerate(player_frames):
        if pid not in frame:
            continue
        u = 1 - abs(times[k] - times[kick]) / ease
        if u <= 0:
            continue
        w = u * u * (3 - 2 * u)
        x, y = frame[pid]
        frame[pid] = (x + off[0] * w, y + off[1] * w)
    return gap


def _crossing(ball, times, start, side):
    """(first frame over the line, crossing time, crossing point) after `start`, or None."""
    for k in range(start + 1, len(ball)):
        if ball[k] is None or ball[k - 1] is None:
            continue
        xa, xb = side * ball[k - 1][0], side * ball[k][0]
        if xa < GOAL_LINE_X <= xb:
            w = (GOAL_LINE_X - xa) / (xb - xa)
            return k, times[k - 1] + (times[k] - times[k - 1]) * w, tuple(
                p + (q - p) * w for p, q in zip(ball[k - 1], ball[k]))
    return None


def fly_shot(ball, times, player_frames, kick, shooter, part, side, penalty=False, cleared_after=None,
             deflections=()):
    """Fly the shot from the shooter's foot at frame `kick` to where the path
    (already aimed by the goal-mouth correction) crosses the goal line, then
    into the net. For a goal-line clearance (cleared_after set) the target is
    the ball's closest approach, and the tracked clearance plays on from there.
    deflections: logged touches after the shot by the other team
    [{"f", "p", "b"}]; one is kept as a change of direction if the ball is
    within reach of him then. May move the shooter in player_frames (see
    move_shooter). Returns (ball, info)."""
    out = list(ball)
    info = {"shot": False, "speed": None, "deflected": [], "at_foot_moved": 0.0, "shooter_moved": 0.0}
    if out[kick] is None:
        return out, info
    cross = _crossing(out, times, kick, side)
    if cleared_after is not None:
        span = [i for i in range(kick + 1, min(cleared_after, len(out))) if out[i] is not None]
        if not span:
            return out, info
        c = max(span, key=lambda i: side * out[i][0])
        cross = (c, times[c], out[c])
    if cross is None:
        return out, info
    k_cross, t_cross, target = cross
    # The ball at his foot (on the spot for a penalty). If he's far from it, the
    # tracking has him in the wrong place: he's moved onto the ball instead.
    if penalty or shooter not in player_frames[kick]:
        start = out[kick]
    else:
        p = player_frames[kick][shooter]
        if math.hypot(out[kick][0] - p[0], out[kick][1] - p[1]) > SHOOTER_TRUST_M:
            info["shooter_moved"] = move_shooter(player_frames, times, shooter, kick, out[kick][:2], target[:2])
        start = touch_spot(player_frames[kick][shooter], out[kick], part, toward=target[:2])
    info["at_foot_moved"] = math.dist(start[:2], out[kick][:2])
    # Waypoints: the kick, any deflection with the ball near him, the line.
    points = [(kick, times[kick], start)]
    for c in sorted(deflections, key=lambda c: c["f"]):
        f = c["f"]
        if not kick < f < k_cross or out[f] is None or c["p"] not in player_frames[f]:
            continue
        p = player_frames[f][c["p"]]
        if math.hypot(out[f][0] - p[0], out[f][1] - p[1]) <= REACH_M and out[f][2] <= REACH_Z:
            points.append((f, times[f], touch_spot(p, out[f], c["b"])))
            info["deflected"].append(c)
    # Retime: the whole flight at a believable speed (the tracking loses hard shots).
    length = sum(math.dist(p[2][:2], q[2][:2]) for p, q in zip(points, points[1:] + [(k_cross, t_cross, target)]))
    took = t_cross - times[kick]
    floor = MIN_SHOT_MPS.get(part, MIN_KICK_MPS)
    want = took
    if took < length / MAX_SHOT_MPS:
        want = length / MAX_SHOT_MPS
    elif took > max(length / floor, MIN_SHOT_S):
        want = max(length / floor, MIN_SHOT_S)
    scale = want / took if took > 0 else 1.0
    t0 = times[kick]
    points = [(f, t0 + (t - t0) * scale, p) for f, t, p in points]
    points.append((None, t0 + want, target))
    old = list(out)
    v_end = None  # the ball's velocity as it crosses the line
    for (_, ta, pa), (_, tb, pb) in zip(points, points[1:]):
        distance = math.hypot(pb[0] - pa[0], pb[1] - pa[1])
        peak = max([pa[2], pb[2]] + [old[k][2] for k in range(kick, k_cross) if old[k] is not None
                                     and ta <= t0 + (times[k] - t0) * scale <= tb])
        kick_v = None
        if tb - ta > 0.05 and distance > 0.3:
            kick_v = solve_to_height(pa[2], distance, tb - ta, pb[2])
            if kick_v is None:
                kick_v = _kick(pa[2], distance, tb - ta, peak, pb[2], (pb[2] - 0.05, pb[2] + 0.05))
        for k in range(kick, len(out)):
            if not ta < times[k] < tb:
                continue
            if kick_v is not None:
                s, z, _ = simulate(pa[2], kick_v[0], kick_v[1], [times[k] - ta])
                w = s[-1] / distance if distance > 1e-6 else 1.0
                zk = z[-1]
            else:
                w = (times[k] - ta) / (tb - ta)
                zk = pa[2] + (pb[2] - pa[2]) * w
            out[k] = (pa[0] + (pb[0] - pa[0]) * w, pa[1] + (pb[1] - pa[1]) * w, max(zk, 0.0))
        ux, uy = ((pb[0] - pa[0]) / distance, (pb[1] - pa[1]) / distance) if distance > 1e-6 else (0.0, 0.0)
        if kick_v is not None:
            dur, eps = tb - ta, 0.004
            s2, z2, _ = simulate(pa[2], kick_v[0], kick_v[1], [dur - eps, dur])
            v_end = (ux * (s2[1] - s2[0]) / eps, uy * (s2[1] - s2[0]) / eps, (z2[1] - z2[0]) / eps)
        else:
            v_end = ((pb[0] - pa[0]) / (tb - ta), (pb[1] - pa[1]) / (tb - ta), (pb[2] - pa[2]) / (tb - ta))
        for f, t, p in points:
            if f is not None and t == ta:
                out[f] = p
    out[kick] = start
    t_new = points[-1][1]
    k_new = next((k for k in range(kick + 1, len(out)) if times[k] >= t_new), len(out))
    if cleared_after is None:
        # Past the line: simulated into the net from exactly how it crossed (net.py).
        rel = [times[k] - t_new for k in range(k_new, len(out))]
        for k, p in zip(range(k_new, len(out)), into_net(target, v_end, rel, side)):
            out[k] = p
    else:
        # The clearance plays on from the new crossing time.
        for k in range(k_new, len(out)):
            out[k] = old[min(k_cross + (k - k_new), len(old) - 1)]
    info["shot"] = True
    info["speed"] = length / want if want > 0 else None
    return out, info


def enforce_touch_rule(ball, times, player_frames, contacts, carries, goal_index, keepers, moved=()):
    """Fix every rule break before the shot (goal_index: the frame the shot is
    kicked, whose ball stays put). keepers: {player id: +1/-1, the goal he
    defends}. moved: touch frames whose ball was moved (the shot, put at his
    foot): the ball is re-drawn to meet it. Returns (ball, contacts, counts)."""
    out = list(ball)
    contacts = [dict(c) for c in contacts]
    counts = {"moved_to_foot": 0, "touches_added": 0, "joined": 0, "straight": 0}
    inside = set()
    for a, b, _ in carries:
        inside.update(range(a + 1, b))

    # 2. Every touch up to the shot has the ball within his reach.
    dirty = set(moved)
    for c in contacts:
        f = c["f"]
        if f >= goal_index or f in inside or out[f] is None or c["p"] not in player_frames[f]:
            continue
        p = player_frames[f][c["p"]]
        lo, hi = part_z(c["b"])
        if math.hypot(out[f][0] - p[0], out[f][1] - p[1]) > REACH_M - 0.05 or not lo <= out[f][2] <= hi:
            out[f] = touch_spot(p, out[f], c["b"])
            dirty.add(f)
            counts["moved_to_foot"] += 1

    # 3. Between touches. Checked on the ball as the clip stores it (2 decimals).
    for _ in range(4):
        out = [tuple(round(v, 2) for v in b) if b is not None else None for b in out]
        bounds = sorted({0, goal_index} | {c["f"] for c in contacts if c["f"] <= goal_index}
                        | {x for a, b, _ in carries for x in (a, b) if b <= goal_index})
        bounds = [f for f in bounds if f not in inside]
        bad = violations(out, times, contacts, player_frames, goal_index, carries, end=goal_index)
        bad = [f for f, kind in bad if kind != "far touch"]
        stretches = []
        for a, b in zip(bounds, bounds[1:]):
            if (a in inside or b - a < 2 or out[a] is None or out[b] is None
                    or any(ca <= a and b <= cb for ca, cb, _ in carries)):  # a carry or dead ball: left as it is
                continue
            if any(a < f < b for f in bad) or a in dirty or b in dirty:
                stretches.append((a, b, [f for f in bad if a < f < b]))
        if not stretches:
            break
        dirty = set()
        part_at = {c["f"]: c["b"] for c in contacts}
        for a, b, turns in stretches:
            added = []
            for v in turns:
                if any(abs(v - f) < 2 * WINDOW + 2 for f in added + [a, b]):
                    continue
                best = None
                for k in range(max(a + 2, v - WINDOW), min(b - 2, v + WINDOW) + 1):
                    if out[k] is None or out[k][2] > REACH_Z:
                        continue
                    for pid, (x, y) in player_frames[k].items():
                        d = math.hypot(out[k][0] - x, out[k][1] - y)
                        if d <= ADD_TOUCH_M and (best is None or d < best[0]):
                            best = (d, k, pid)
                if best is None:
                    continue
                _, k, pid = best
                if any(abs(k - f) < 2 * WINDOW for f in added):
                    continue
                part = _part(out[k][2], pid, player_frames[k], keepers)
                out[k] = touch_spot(player_frames[k][pid], out[k], part)
                contacts.append({"f": k, "p": pid, "b": part, "s": 2})
                part_at[k] = part
                added.append(k)
                counts["touches_added"] += 1
            anchors = sorted({a, b, *added})
            if not added:
                counts["joined"] += 1
            for u, w in zip(anchors, anchors[1:]):
                # A touch added here can be foot, head or hands: whatever height the ball arrives at.
                z_range = (0.0, PART_Z["H"][1]) if w in added else part_z(part_at.get(w, "F"))
                if fly(out, times, u, w, z_range, fixed_end=w == goal_index) == "straight":
                    counts["straight"] += 1
            for c in contacts:
                if c["f"] in added and c.get("s") == 2:
                    c["b"] = _part(out[c["f"]][2], c["p"], player_frames[c["f"]], keepers)
        contacts.sort(key=lambda c: c["f"])
    out = [tuple(round(v, 2) for v in b) if b is not None else None for b in out]
    return out, contacts, counts
