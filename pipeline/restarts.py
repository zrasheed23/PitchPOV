"""Ball out of play and restarts.

When the ball leaves the pitch it stops out there; it never comes back in by
itself. It comes back with a restart, which PFF marks with
gameEvents.setpieceType on the taker's event: T throw-in (hands), C corner,
G goal kick, F free kick, K kick-off (P penalties are penalty.py). The OUT
event before it (outType T: over a touchline, W: over a goal line) is when the
ball went out; before a free kick it's the foul, the last event before it.

For every restart in a clip before the shot:
- If the ball went out (or the foul happened) inside the clip, the ball rolls on
  from there, slows to a stop within OUT_ROLL_S, and stays still.
- Throw-in: the thrower stands just behind the touchline where he throws it; the
  ball is picked up (from where it stopped, if that's where he is; otherwise a
  new ball appears) and held above his head for HOLD_S, then thrown at the
  logged frame (a hands touch) to wherever the next touch is (ball_rules flies it).
- Corner, goal kick, free kick, kick-off: the ball sits still at the right spot
  (corner arc, goal area, where the free kick is taken, centre spot) for
  PLACED_S before the kick, and the taker is at it when he kicks. If it stopped
  somewhere else it is hidden in between (a new ball, or the ball carried back).
Everything between the ball going out and the restart is dead: no touches, no
rebuilt dribbles, and the touch rule skips it.
"""

import math

from contacts import body_part

HALF_L, HALF_W = 52.5, 34.0
ROLL_OUT_DECEL = 4.0  # m/s^2: slows fast once it's off the grass and among the boards
OUT_ROLL_S = 1.5
HOLD_S = 1.0  # throw-in: ball above his head this long before the throw
PICKUP_S = 0.3
HOLD_Z = 2.3
THROW_BACK_M = 0.4  # the thrower's centre behind the touchline
PLACED_S = 2.0
TAKER_BEHIND_M = 0.35
PICKUP_NEAR_M = 1.5  # the ball stopped this close to the throw spot: he picks that one up
HIDE_AFTER_S = 0.5  # a ball that stopped somewhere else stays in view this long, then is hidden
PLAYER_EASE_S = 1.2
PLAYER_CATCHUP_MPS = 4.0  # a taker tracked far from the ball is eased in no faster than this on top of his run
AT_SPOT_M = 3.0
TAKEN_EARLY_S = 2.0  # look this far back from the logged restart for the ball still at the spot
TAKEN_LATE_S = 0.5
KINDS = {"T": "throw-in", "C": "corner", "G": "goal kick", "F": "free kick", "K": "kick-off"}
TOUCHES = {"PA", "SH", "CR", "CL", "RE", "TC", "IT"}


def find_restarts(events, frame_ms, kick_frame, player_ids):
    """[{"type", "f": frame, "p": taker, "b": body part, "out": frame the ball
    went out (or the foul), or None if before the clip}] for set pieces up to
    the kick frame."""
    if not frame_ms:
        return []
    lo, hi = frame_ms[0], frame_ms[-1]

    def frame(ms):
        return min(range(len(frame_ms)), key=lambda i: abs(frame_ms[i] - ms))

    timed = sorted((e for e in events if e.get("eventTime") is not None), key=lambda e: e["eventTime"])
    out, last_event = None, None
    found = []
    for e in timed:
        ms = e["eventTime"] * 1000
        if ms > hi:
            break
        g = e.get("gameEvents") or {}
        kind = g.get("setpieceType")
        pid = str(g.get("playerId")) if g.get("playerId") is not None else None
        if kind in KINDS and ms >= lo and pid in player_ids:
            f = frame(ms)
            if f <= kick_frame:
                prev = out if kind != "F" else last_event
                found.append({"type": kind, "f": f, "p": pid, "b": body_part(e),
                              "out": frame(prev) if prev is not None and prev >= lo else None})
        if g.get("gameEventType") == "OUT" and g.get("outType") in ("T", "W"):
            out = ms
        if kind not in KINDS:
            last_event = ms
        if kind in KINDS:
            out = None
    return found


def _spot(kind, ball_at, taker_at):
    """Where the ball is placed: the corner arc, the goal area, the centre spot,
    where the tracking has it (free kick), or behind the touchline (throw-in)."""
    x, y = ball_at if ball_at is not None else taker_at
    sx, sy = (1 if x >= 0 else -1), (1 if y >= 0 else -1)
    if kind == "C":
        return sx * (HALF_L - 0.25), sy * (HALF_W - 0.25)
    if kind == "G":
        return sx * min(max(abs(x), HALF_L - 5.5), HALF_L - 0.5), min(max(y, -9.16), 9.16)
    if kind == "K":
        return 0.0, 0.0
    if kind == "T":
        tx = taker_at[0] if taker_at is not None else x
        side = 1 if (taker_at[1] if taker_at is not None else y) >= 0 else -1
        return tx, side * (HALF_W + THROW_BACK_M)
    return min(max(x, -HALF_L), HALF_L), min(max(y, -HALF_W), HALF_W)


def _hold_player(player_frames, times, pid, first, last, xy, ease_s=PLAYER_EASE_S):
    """Put a player at xy for frames first..last, easing in before and out after
    (smoothstep over ease_s, longer if he'd gain more than PLAYER_CATCHUP_MPS on
    his tracked run). In place; returns metres moved at `last`."""
    n = len(player_frames)
    p_first, p_last = player_frames[first].get(pid), player_frames[last].get(pid)
    if p_first is None or p_last is None:
        return 0.0
    off_in = (xy[0] - p_first[0], xy[1] - p_first[1])
    off_out = (xy[0] - p_last[0], xy[1] - p_last[1])
    ease_s = max(ease_s, math.hypot(*off_in) / PLAYER_CATCHUP_MPS, math.hypot(*off_out) / PLAYER_CATCHUP_MPS)
    for k in range(n):
        p = player_frames[k].get(pid)
        if p is None:
            continue
        if first <= k <= last:
            player_frames[k][pid] = xy
            continue
        off, gap = (off_in, times[first] - times[k]) if k < first else (off_out, times[k] - times[last])
        u = 1 - gap / ease_s
        if u > 0:
            w = u * u * (3 - 2 * u)
            player_frames[k][pid] = (p[0] + off[0] * w, p[1] + off[1] * w)
    return math.hypot(*off_out)


def _roll_out(ball, times, o, stop_by, last_touch=None):
    """From frame o the ball carries on in the direction it was going (from the
    last touch, which is how it's flown there) at the speed it had, and slows to
    a stop (by frame stop_by at the latest). Returns (stop frame, stop point)."""
    b = ball[o]
    k0 = next((k for k in range(o - 1, max(o - 6, -1), -1) if ball[k] is not None), None)
    vx = vy = speed = 0.0
    if k0 is not None and times[o] > times[k0]:
        speed = math.dist(b[:2], ball[k0][:2]) / (times[o] - times[k0])
        frm = ball[last_touch] if last_touch is not None and ball[last_touch] is not None else ball[k0]
        n = math.dist(b[:2], frm[:2])
        if n > 1e-6:
            vx, vy = (b[0] - frm[0]) / n * speed, (b[1] - frm[1]) / n * speed
    speed = math.hypot(vx, vy)
    t_stop = min(speed / ROLL_OUT_DECEL, OUT_ROLL_S)
    decel = speed / t_stop if t_stop > 0 else 0.0
    z0 = b[2]
    stop = o
    for k in range(o, stop_by + 1):
        t = min(times[k] - times[o], t_stop)
        s = speed * t - 0.5 * decel * t * t
        ux, uy = (vx / speed, vy / speed) if speed > 1e-6 else (0.0, 0.0)
        drop = max(0.0, 1 - (times[k] - times[o]) / 0.3)  # a ball in the air comes down quickly
        ball[k] = (b[0] + ux * s, b[1] + uy * s, z0 * drop * drop)
        if times[k] - times[o] <= t_stop:
            stop = k
    return stop, ball[stop]


def apply_restarts(ball, times, player_frames, restarts, next_touch, last_touch=lambda f: None, last_frame=None):
    """Draw the dead ball for every restart (in place on ball and
    player_frames) and return [{"type", "f", "p", "out", "hold", "placed",
    "hidden"}] for the clip, plus dead ranges [(first, last)] where the ball is
    out of play. next_touch(f) / last_touch(f): the frame of the first touch
    after f / the last one before it, or None. last_frame: no restart is taken
    after it (the shot)."""
    info, dead = [], []
    for r in restarts:
        f, pid, kind = r["f"], r["p"], r["type"]
        taker = player_frames[f].get(pid)
        ball_at = ball[f][:2] if ball[f] is not None else None
        spot = _spot(kind, ball_at, taker)
        # PFF can log a restart late (or early): it's taken at the last frame the
        # tracked ball is still at the spot (in reach of it for a throw-in).
        last = len(ball) - 1 if last_frame is None else last_frame
        near = [k for k in range(last + 1) if -TAKEN_EARLY_S <= times[k] - times[f] <= TAKEN_LATE_S
                and ball[k] is not None and math.dist(ball[k][:2], spot) <= AT_SPOT_M]
        if near and (r["out"] is None or max(near) > r["out"]):
            f = r["f"] = max(near)
            taker = player_frames[f].get(pid)
        start = r["out"] if r["out"] is not None and r["out"] < f and ball[r["out"]] is not None else 0
        placed_from = f
        hidden = []
        # How long the ball sits at the spot (or in his hands) before it's played.
        ready = HOLD_S + PICKUP_S + 0.2 if kind == "T" else PLACED_S
        # Out (or fouled) before the clip starts: dead from its first frame.
        placed_from = max(start, next((k for k in range(f, -1, -1) if times[f] - times[k] >= ready), 0)) \
            if start > 0 else 0
        if start > 0:
            stop, at = _roll_out(ball, times, start, f, last_touch(start))
            if math.dist(at[:2], spot) <= PICKUP_NEAR_M and kind in ("T", "F"):
                placed_from = max(placed_from, stop)
                for k in range(stop, placed_from):
                    ball[k] = at
                if kind == "T":
                    spot = (at[0], spot[1])  # he throws it from where it stopped
            else:
                hide_from = next((k for k in range(stop, f + 1) if times[k] - times[stop] >= HIDE_AFTER_S), f)
                for k in range(stop + 1, hide_from):
                    ball[k] = at
                placed_from = max(placed_from, hide_from + 1)
                for k in range(hide_from, placed_from):
                    ball[k] = None
                if placed_from > hide_from:
                    hidden = [hide_from, placed_from - 1]
        hold = None
        if kind == "T":
            hold_from = max(placed_from, next((k for k in range(f, -1, -1) if times[f] - times[k] >= HOLD_S), 0))
            lift_from = max(placed_from, next((k for k in range(hold_from, -1, -1)
                                               if times[hold_from] - times[k] >= PICKUP_S), 0))
            _hold_player(player_frames, times, pid, lift_from, f, spot)
            for k in range(placed_from, f + 1):
                if k < lift_from:
                    ball[k] = (spot[0], spot[1] - math.copysign(0.3, spot[1]), 0.0)  # at his feet, in front
                elif k < hold_from:
                    w = (times[k] - times[lift_from]) / max(times[hold_from] - times[lift_from], 1e-6)
                    ball[k] = (spot[0], spot[1] - math.copysign(0.3 * (1 - w), spot[1]), HOLD_Z * w)
                else:
                    ball[k] = (spot[0], spot[1], HOLD_Z)
            hold = [hold_from, f]
        else:
            for k in range(placed_from, f + 1):
                ball[k] = (spot[0], spot[1], 0.0)
            # The taker at the ball, behind it on the line to where it's played.
            nxt = next_touch(f)
            aim = ball[nxt][:2] if nxt is not None and ball[nxt] is not None else (0.0, 0.0)
            dx, dy = aim[0] - spot[0], aim[1] - spot[1]
            n = math.hypot(dx, dy) or 1.0
            if taker is not None:
                _hold_player(player_frames, times, pid, max(f - 3, 0), f,
                             (spot[0] - dx / n * TAKER_BEHIND_M, spot[1] - dy / n * TAKER_BEHIND_M))
        dead.append((start, f))
        info.append({"type": kind, "f": f, "p": pid, "out": r["out"], "hold": hold, "placed": [placed_from, f],
                     "hidden": hidden})
    return info, dead


def redraw_roll_out(ball, times, info):
    """After the touch rule has re-flown the ball up to where it goes out, roll
    it out along that final direction (where it isn't picked up from where it
    stops, so nothing else depends on the stop point). In place."""
    for r in info:
        o = r["out"]
        if o is None or not r["hidden"] or o < 3 or ball[o] is None:
            continue
        stop, at = _roll_out(ball, times, o, r["hidden"][0] - 1, o - 3)
        for k in range(stop + 1, r["hidden"][0]):
            ball[k] = at
