"""Players don't stand inside each other.

PFF estimates players it can't see and the pipeline eases players onto
touches, spots and 360 frames, so two tracks can end up on top of each other
(66 clips had two centres under 0.4 m apart around the kick). Two players'
centres stay at least MIN_M apart, or DUEL_M for two opponents both within
DUEL_BALL_M of the ball (a duel or an aerial challenge: bodies meet there).

Only one of the two moves, the one whose position is least supported:
never a locked position (a touch, the shooter at the kick, a keeper's dive, a
player placed from a 360 frame or StatsBomb event); else the one farther from
the ball (PFF's tracks off the ball are its estimates). He's pushed straight
away from the other by the shortfall, and each player's pushes are spread in
time (held over HOLD_S either side, then smoothed) so nobody jumps; a push
fades out before his locked frames.
"""

import math

MIN_M = 0.6
DUEL_M = 0.4
DUEL_BALL_M = 1.5
SLACK_M = 0.02
HOLD_S = 0.3
SMOOTH_S = 0.25
LOCK_FADE_S = 0.2
ROUNDS = 4


def min_gap(a, b, pa, pb, ball, players):
    """The smallest centre-to-centre distance allowed for players a and b."""
    if (players[a]["team"] != players[b]["team"] and ball is not None
            and math.dist(pa, ball[:2]) <= DUEL_BALL_M and math.dist(pb, ball[:2]) <= DUEL_BALL_M):
        return DUEL_M
    return MIN_M


def overlaps(player_frames, ball, players, frames=None):
    """[(frame, a, b, distance, allowed)] for every pair closer than allowed."""
    out = []
    for k in (range(len(player_frames)) if frames is None else frames):
        ps = list(player_frames[k].items())
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                (a, pa), (b, pb) = ps[i], ps[j]
                d = math.dist(pa, pb)
                if d < MIN_M:
                    allowed = min_gap(a, b, pa, pb, ball[k], players)
                    if d < allowed:
                        out.append((k, a, b, d, allowed))
    return out


def separate(player_frames, times, ball, players, locked):
    """Push players apart (see the module doc). locked: {player id: set of
    frames} he mustn't move at. In place; returns {player id: largest push (m)}."""
    n = len(player_frames)
    dt = (times[-1] - times[0]) / max(n - 1, 1)
    hold, reach = int(round(HOLD_S / dt)), int(3 * SMOOTH_S / dt) + 1
    fade = max(int(round(LOCK_FADE_S / dt)), 1)
    moved = {}
    for _ in range(ROUNDS):
        push = {}  # pid -> {frame: (dx, dy)}
        for k, a, b, d, allowed in overlaps(player_frames, ball, players):
            pa, pb = player_frames[k][a], player_frames[k][b]
            free = [p for p in (a, b) if k not in locked.get(p, ())]
            if not free:
                continue
            if len(free) == 2 and ball[k] is not None:  # the one farther from the ball
                free.sort(key=lambda p: -math.dist(player_frames[k][p], ball[k][:2]))
            m = free[0]
            o = b if m == a else a
            me, other = player_frames[k][m], player_frames[k][o]
            ux, uy = me[0] - other[0], me[1] - other[1]
            u = math.hypot(ux, uy)
            if u < 1e-6:  # dead on top of each other: step away from the ball (or along x)
                ux, uy = ((me[0] - ball[k][0], me[1] - ball[k][1]) if ball[k] is not None else (1.0, 0.0))
                u = math.hypot(ux, uy) or 1.0
            need = allowed - d + SLACK_M
            v = (ux / u * need, uy / u * need)
            cur = push.setdefault(m, {}).get(k)
            if cur is None or math.hypot(*v) > math.hypot(*cur):
                push[m][k] = v
        if not push:
            break
        for pid, at in push.items():
            # Hold each push over HOLD_S either side (the largest wins), then smooth.
            held = [(0.0, 0.0)] * n
            for k, v in at.items():
                for j in range(max(0, k - hold), min(n, k + hold + 1)):
                    if math.hypot(*v) > math.hypot(*held[j]):
                        held[j] = v
            smooth = []
            for k in range(n):
                ws = [(math.exp(-((j - k) * dt / SMOOTH_S) ** 2 / 2), held[j])
                      for j in range(max(0, k - reach), min(n, k + reach + 1))]
                tot = sum(w for w, _ in ws)
                smooth.append((sum(w * v[0] for w, v in ws) / tot, sum(w * v[1] for w, v in ws) / tot))
            # Nothing at his locked frames, fading in and out around them.
            lock = locked.get(pid, set())
            for k in range(n):
                near = min((abs(k - f) for f in lock if abs(k - f) <= fade), default=None)
                w = 1.0 if near is None else near / fade
                if w <= 0 or pid not in player_frames[k]:
                    continue
                x, y = player_frames[k][pid]
                dx, dy = smooth[k][0] * w, smooth[k][1] * w
                player_frames[k][pid] = (x + dx, y + dy)
                moved[pid] = max(moved.get(pid, 0.0), math.hypot(dx, dy))
    return moved
