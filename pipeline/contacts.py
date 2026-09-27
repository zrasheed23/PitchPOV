"""Every touch of the ball in a clip, from PFF's event data.

PFF logs each on-ball action with its video time, the player and the body
part: passes, shots, crosses, clearances, rebounds, touches, and the first
touch when receiving (IT, with initialTouch.initialBodyType). The viewer uses
these to make the player's foot, head or hands actually meet the ball at that
moment, so contacts are visible instead of the ball floating past people.
"""

import math

ACTIONS = {"PA", "SH", "CR", "CL", "RE", "TC", "IT"}  # skip CH (challenge) and BC (carry): no single touch

# PFF body types -> R/L foot, H head, X hands (throw-ins, keeper), F foot (side unknown).
BODY = {"R": "R", "L": "L", "HE": "H", "TWOHANDS": "X", "RH": "X", "LH": "X", "HA": "X"}

MIN_GAP_FRAMES = 4  # drop repeat logs of the same touch


def body_part(event):
    pe = event.get("possessionEvents") or {}
    raw = pe.get("bodyType")
    if not raw and pe.get("possessionEventType") == "IT":
        raw = (event.get("initialTouch") or {}).get("initialBodyType")
    return BODY.get(raw or "", "F")


def find_contacts(events, frame_ms, player_ids):
    """events: the match's event list. frame_ms: videoTimeMs of each clip frame.
    player_ids: ids (str) of players in the clip.
    Returns [{"f": frame index, "p": player id, "b": body part}] in time order."""
    if not frame_ms:
        return []
    lo, hi = frame_ms[0], frame_ms[-1]
    found = []
    for e in events:
        pe = e.get("possessionEvents") or {}
        if pe.get("possessionEventType") not in ACTIONS:
            continue
        t = e.get("eventTime")
        pid = (e.get("gameEvents") or {}).get("playerId")
        if t is None or pid is None:
            continue
        ms = t * 1000
        if not lo <= ms <= hi:
            continue
        pid = str(pid)
        if pid not in player_ids:
            continue
        f = min(range(len(frame_ms)), key=lambda i: abs(frame_ms[i] - ms))
        found.append({"f": f, "p": pid, "b": body_part(e)})
    found.sort(key=lambda c: c["f"])
    out = []
    for c in found:
        if out and out[-1]["p"] == c["p"] and c["f"] - out[-1]["f"] < MIN_GAP_FRAMES:
            continue
        out.append(c)
    return out


# --- Lining touches up with the tracking ------------------------------------
#
# PFF's event times and the tracking don't always agree: a shot can be logged
# half a second after the ball has left the foot, a pass before it's played. At
# a mistimed touch the ball is metres from the player, and the viewer pulled it
# back onto his foot: the ball was shot, flew a few metres, came back to the
# foot and was shot again. So each touch is checked against the tracking:
# - moved to the nearby frame where the tracked ball is at his feet, if there is one;
# - if the tracking has no ball there (a gap the pipeline filled in), the event is
#   trusted and the ball is put at his feet;
# - otherwise (the tracked ball is somewhere else) the touch is dropped, except
#   the shot itself, which is kept either way.
# Touches after the shot are dropped too: the ball is on its way in or in the net.

ALIGN_WINDOW_S = 0.4
AT_FEET_M = 1.5  # player centre to ball: a stretched leg plus tracking error
SNAP_MAX_M = 8.0  # a filled-in ball farther than this from him is too far to trust the event
PLACE_MAX_MPS = 30.0  # ...or if getting the ball to his feet would need it to move faster than this
SHOT_FRAMES = 3  # a touch this close before the shot frame is the shot: always kept
TOUCH_Z = {"H": 1.9, "X": 1.3}  # height of the ball at a header / hands; feet are on the ground
FOOT_OFFSET_M = 0.4


def align_contacts(contacts, ball, tracked, player_frames, times, goal_index):
    """contacts: find_contacts output. ball: the gap-filled ball per frame.
    tracked[i]: the ball feed really had the ball at frame i. Returns
    (contacts, ball, counts) with the touches moved, trusted (ball placed at his
    feet) or dropped, and counts of each."""
    out, ball = [], list(ball)
    counts = {"kept": 0, "moved": 0, "placed": 0, "dropped": 0, "after_shot": 0}
    fps_dt = (times[-1] - times[0]) / max(len(times) - 1, 1)
    window = int(round(ALIGN_WINDOW_S / fps_dt)) if fps_dt > 0 else 0

    def gap(k, pid):
        b, p = ball[k], player_frames[k].get(pid)
        if b is None or p is None:
            return None
        return math.hypot(b[0] - p[0], b[1] - p[1])

    late = []
    for c in contacts:
        f, pid = c["f"], c["p"]
        if f > goal_index:
            late.append(c)
            counts["after_shot"] += 1
            continue
        d = gap(f, pid)
        if d is not None and d <= AT_FEET_M:
            out.append(c)
            counts["kept"] += 1
            continue
        # Nearest frame (in time) within the window where the tracked ball is at his feet.
        best = None
        for off in sorted(range(-window, window + 1), key=abs):
            k = f + off
            if 0 <= k <= goal_index and tracked[k]:
                dk = gap(k, pid)
                if dk is not None and dk <= AT_FEET_M:
                    best = k
                    break
        if best is not None:
            out.append({**c, "f": best})
            counts["moved"] += 1
            continue
        p = player_frames[f].get(pid)
        if not tracked[f] and d is not None and d <= SNAP_MAX_M and p is not None:
            b = ball[f]
            dx, dy = b[0] - p[0], b[1] - p[1]
            n = math.hypot(dx, dy) or 1.0
            spot = (p[0] + dx / n * FOOT_OFFSET_M, p[1] + dy / n * FOOT_OFFSET_M, TOUCH_Z.get(c["b"], 0.1))
            if _place(ball, tracked, times, f, spot, goal_index):
                out.append(c)
                counts["placed"] += 1
                continue
        if goal_index - SHOT_FRAMES <= f <= goal_index:
            out.append(c)
            counts["kept"] += 1
            continue
        counts["dropped"] += 1

    # A shot logged late: if nothing touches it at the shot, and a touch logged
    # after it is by the player on the ball at the shot, it was the shot.
    if not any(goal_index - 3 <= c["f"] <= goal_index for c in out):
        shooter = next((c for c in late if (gap(goal_index, c["p"]) or math.inf) <= 2.0), None)
        if shooter:
            out.append({**shooter, "f": goal_index})
            counts["moved"] += 1

    # Moving touches can bring two of one player's together: keep the first.
    out.sort(key=lambda c: c["f"])
    deduped = []
    for c in out:
        if deduped and deduped[-1]["p"] == c["p"] and c["f"] - deduped[-1]["f"] < MIN_GAP_FRAMES:
            continue
        deduped.append(c)
    return deduped, ball, counts


def _place(ball, tracked, times, f, spot, goal_index):
    """Put the ball at `spot` at frame f, inside a stretch the tracking missed, and
    re-draw that stretch as straight lines from the last tracked ball to the spot
    and on to the next tracked ball (or the shot), so there's no jump. False (and
    nothing changed) if that would need the ball to move faster than PLACE_MAX_MPS."""
    n = len(ball)
    a = f
    while a > 0 and not tracked[a - 1]:
        a -= 1
    b = f
    while b < n - 1 and not tracked[b + 1] and b + 1 < goal_index:
        b += 1
    left = a - 1 if a > 0 and ball[a - 1] is not None else None
    right = b + 1 if b + 1 < n and ball[b + 1] is not None else None
    for e in (left, right):
        if e is not None and times[e] != times[f]:
            if math.dist(ball[e][:2], spot[:2]) / abs(times[e] - times[f]) > PLACE_MAX_MPS:
                return False
    for lo, hi in ((left, f), (f, right)):
        if lo is None or hi is None:
            continue
        p, q = (ball[lo], spot) if hi == f else (spot, ball[hi])
        for k in range(lo + 1, hi):
            w = (times[k] - times[lo]) / (times[hi] - times[lo])
            ball[k] = tuple(pv + (qv - pv) * w for pv, qv in zip(p, q))
    ball[f] = spot
    return True
