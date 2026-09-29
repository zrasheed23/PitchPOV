"""Offside at the assist: every goal stood, so the scorer was onside when the
last pass to him was played.

The assist is the last StatsBomb pass before the kick whose next on-ball
action (by someone other than the passer) is the scorer's; passes from a
throw-in, corner or goal kick are left out (no offside from those). It's
played at the passer's touch (his contact within a few frames of the event),
else at the event. The scorer is offside if he's in the opponents' half and
past both the second-last defender (the keeper counts) and the ball.

A replay with him offside has the run timed wrong (PFF's estimate of a
player the camera can't see, or a pass timed early): ease_onside moves him
back to just onside at the pass, fading the correction out before and after
so he still meets the ball where he touches it.
"""

import math

TOLERANCE_M = 0.5  # past the line by more than this is offside (the data's own error is about this)
ONSIDE_M = 0.2  # eased back to this far onside
NO_OFFSIDE = {"Throw-in", "Corner", "Goal Kick"}
TOUCH_FRAMES = 6
ACCEL = 4.0


def find_assist(statsbomb, scorer, kick, contacts=()):
    """(pass event, frame it's played) or (None, None)."""
    evs = sorted((e for e in statsbomb if e["on_ball"] and e["f"] <= kick), key=lambda e: e["f"])
    best = None
    for n, e in enumerate(evs):
        if e["type"] != "Pass" or e["p"] == scorer:
            continue
        nxt = next((x for x in evs[n + 1:] if x["p"] != e["p"]), None)
        if nxt is None or nxt["p"] != scorer:
            continue
        if ((e["raw"].get("pass") or {}).get("type") or {}).get("name") in NO_OFFSIDE:
            continue
        best = e
    if best is None:
        return None, None
    touch = min((c for c in contacts if c["p"] == best["p"] and abs(c["f"] - best["f"]) <= TOUCH_FRAMES),
                key=lambda c: abs(c["f"] - best["f"]), default=None)
    return best, (touch["f"] if touch else best["f"])


def margin(at, ball_xy, team, scorer, side):
    """Metres the scorer is past the offside line at this moment (positive:
    offside), or None if he's in his own half (or anything is missing).
    at: {player id: (x, y)}; team: {player id: "home"/"away"}; side: +1 if
    he attacks the goal at x = +52.5."""
    me = at.get(scorer)
    if me is None or ball_xy is None or side * me[0] <= 0:
        return None
    theirs = sorted((side * xy[0] for pid, xy in at.items() if team.get(pid) != team.get(scorer)), reverse=True)
    if len(theirs) < 2:
        return None
    line = max(theirs[1], side * ball_xy[0])
    return side * me[0] - line


def ease_onside(player_frames, times, scorer, f, by, side, next_touch=None):
    """Move the scorer back (toward his own goal) by `by` metres at frame f,
    eased in before and out after (at most ACCEL on top of his run), and back
    to nothing by his next touch. In place; returns metres moved."""
    n = len(player_frames)
    half = math.sqrt(6 * by / ACCEL)
    t0 = times[f]
    t_end = times[next_touch] if next_touch is not None else t0 + half
    after = max(min(half, t_end - t0), 1e-3)
    for k in range(n):
        if scorer not in player_frames[k]:
            continue
        d = times[k] - t0
        u = 1 - (-d / half if d < 0 else d / after)
        if u <= 0:
            continue
        w = u * u * (3 - 2 * u)
        x, y = player_frames[k][scorer]
        player_frames[k][scorer] = (x - side * by * w, y)
    return by


RETIME_MAX_S = 0.5  # StatsBomb's and PFF's event times are this rough
RUN_MPS = 7.5  # the scorer's average run from onside at the pass to his touch, at most (an eased run peaks ~1.3x that, under the 9.5 m/s cap)


def retime_pass(ball, times, contacts, player_frames, scorer, af, nxt, fly):
    """If the scorer can't get from where he is at the pass (frame af, onside)
    to where he touches the ball next (frame nxt) at RUN_MPS, the pass was
    played earlier than the data has it: move the passer's touch earlier (at
    most RETIME_MAX_S), squeeze the ball's path from the touch before it into
    the shorter time, and re-fly the pass (fly: ball_rules.fly). In place on
    ball and contacts; returns the new pass frame (af if unchanged)."""
    p0, p1 = player_frames[af].get(scorer), player_frames[nxt].get(scorer)
    if p0 is None or p1 is None or ball[af] is None:
        return af
    need = math.dist(p0, p1) / RUN_MPS - (times[nxt] - times[af])
    if need <= 0:
        return af
    prev = max((c["f"] for c in contacts if c["f"] < af), default=0)
    shift = min(need, RETIME_MAX_S)
    new = next((k for k in range(af, prev, -1) if times[af] - times[k] >= shift), None)
    if new is None or new - prev < 4:
        return af
    old = list(ball)
    span_old, span_new = times[af] - times[prev], times[new] - times[prev]
    for k in range(prev + 1, new + 1):  # the ball's way to the passer, in less time
        t = times[prev] + (times[k] - times[prev]) * span_old / span_new
        j = min(max(next((i for i in range(prev, af + 1) if times[i] >= t), af), prev + 1), af)
        a, b = old[j - 1], old[j]
        if a is None or b is None:
            continue
        w = (t - times[j - 1]) / (times[j] - times[j - 1]) if times[j] > times[j - 1] else 1.0
        ball[k] = tuple(u + (v - u) * w for u, v in zip(a, b))
    ball[new] = old[af]
    for c in contacts:
        if c["f"] == af:
            c["f"] = new
    fly(ball, times, new, nxt, fixed_end=True)
    return new
