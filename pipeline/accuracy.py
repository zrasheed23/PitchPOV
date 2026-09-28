"""Accuracy report: does a clip break realism anywhere?

Runs on every clip as written (what the viewer plays), so problems surface
without watching everything. Each check returns its findings; a clip with
any is flagged and goes on the review list:
- "touch far": at a touch the ball is more than TOUCH_M from the player.
- "no-touch turn": the ball turns, speeds up or rises with no touch (touch_rule.py).
- "keeper dive": the keeper takes off before the kick, or dives away from the ball.
- "statsbomb missing": a StatsBomb on-ball action before the shot with no
  touch by that player within MATCH_S.
- "crossing off": the shot crosses more than CROSS_M from StatsBomb's point.
- "sprint": a player moves faster than SPRINT_MPS (over SPRINT_WINDOW frames).
- "keeper wide": before the kick, a keeper in his own box and outside his
  posts (by more than POST_MARGIN_M) while the ball is in that box.
"""

import math

from touch_rule import violations

CHECKS = ("touch far", "no-touch turn", "keeper dive", "statsbomb missing", "crossing off", "sprint", "keeper wide")
TOUCH_M = 1.2
MATCH_S = 0.5
CROSS_M = 0.5
SPRINT_MPS = 10.5  # the fastest footballers top out around 10 m/s
SPRINT_WINDOW = 6  # frames (0.2 s): single-frame speeds are tracking noise
HALF_L, POST_Y, AREA_X, AREA_Y = 52.5, 3.66, 52.5 - 16.5, 20.16
POST_MARGIN_M = 0.3


def _side(clip):
    k = clip.get("kickFrame", clip["goalFrame"])
    b = clip["frames"][k]["b"]
    return 1 if b and b[0] >= 0 else -1


def _crossing(clip, side):
    fr = clip["frames"]
    k0 = clip.get("kickFrame", clip["goalFrame"])
    for k in range(k0 + 1, len(fr)):
        a, b = fr[k - 1]["b"], fr[k]["b"]
        if a and b and side * a[0] < HALF_L <= side * b[0]:
            w = (HALF_L - side * a[0]) / (side * b[0] - side * a[0])
            return k, tuple(p + (q - p) * w for p, q in zip(a, b))
    return None


def check_clip(clip, statsbomb=(), placement=None):
    """{check: [finding, ...]} for one clip dict. statsbomb: the clip's
    StatsBomb events (statsbomb.clip_events: "f", "p", "type", "on_ball");
    placement: its shot_placement.json entry."""
    fr = clip["frames"]
    kick = clip.get("kickFrame", clip["goalFrame"])
    side = _side(clip)
    names = {p["id"]: p["name"] for p in clip["players"]}
    found = {c: [] for c in CHECKS}

    for c in clip.get("contacts", []):
        b, p = fr[c["f"]]["b"], fr[c["f"]]["p"].get(c["p"])
        if b and p:
            d = math.hypot(b[0] - p[0], b[1] - p[1])
            if d > TOUCH_M:
                found["touch far"].append(f"frame {c['f']} {names.get(c['p'])} {d:.1f} m")

    ball = [tuple(f["b"]) if f["b"] else None for f in fr]
    times = [f["t"] for f in fr]
    players = [{k: tuple(v) for k, v in f["p"].items()} for f in fr]
    held = list(clip.get("carries", [])) + [[r["out"] if r["out"] is not None else 0, r["f"], ""]
                                            for r in clip.get("restarts", [])]
    for f, kind in violations(ball, times, clip.get("contacts", []), players, clip["goalFrame"], held):
        if kind != "far touch":  # "touch far" covers those
            found["no-touch turn"].append(f"frame {f} {kind}")

    dive = clip.get("keeperDive")
    cross = _crossing(clip, side)
    if dive and dive["kind"] == "dive":
        if dive["f"] < kick:
            found["keeper dive"].append(f"takes off {dive['f'] - kick} frames before the kick")
        if cross is not None:
            k = players[min(cross[0], len(players) - 1)].get(dive["keeper"])
            if k is not None:
                gap = cross[1][1] - k[1]
                if abs(gap) > 0.3 and (gap > 0) != (dive["dir"] > 0):
                    found["keeper dive"].append(f"dives away from the ball ({gap:+.1f} m)")

    touched = [(c["f"], c["p"]) for c in clip.get("contacts", [])]
    window = MATCH_S / (times[1] - times[0]) if len(times) > 1 else 15
    for e in statsbomb:
        if not e["on_ball"] or e["f"] >= kick - 3:
            continue
        if not any(p == e["p"] and abs(f - e["f"]) <= window for f, p in touched):
            found["statsbomb missing"].append(f"frame {e['f']} {e['type']} by {names.get(e['p'], e['p'])}")

    if placement and "y" in placement and cross is not None and not clip.get("cleared"):
        _, (x, y, z) = cross
        d = math.hypot(y - placement["y"], z - placement.get("z", z))
        if d > CROSS_M:
            found["crossing off"].append(f"{d:.2f} m from StatsBomb")

    for pid in names:
        worst = 0.0
        for k in range(SPRINT_WINDOW, len(fr)):
            a, b = players[k - SPRINT_WINDOW].get(pid), players[k].get(pid)
            if a and b and times[k] > times[k - SPRINT_WINDOW]:
                worst = max(worst, math.dist(a, b) / (times[k] - times[k - SPRINT_WINDOW]))
        if worst > SPRINT_MPS:
            found["sprint"].append(f"{names[pid]} {worst:.1f} m/s")

    for p in clip["players"]:
        if p["position"] != "GK":
            continue
        frames = []
        for k in range(min(kick, len(fr) - 1) + 1):
            q, b = players[k].get(p["id"]), ball[k]
            if q is None or b is None:
                continue
            s = 1 if q[0] >= 0 else -1
            in_box = lambda xy: s * xy[0] >= AREA_X and abs(xy[1]) <= AREA_Y
            if in_box(q) and in_box(b) and abs(q[1]) > POST_Y + POST_MARGIN_M:
                frames.append(k)
        if frames:
            found["keeper wide"].append(f"{p['name']} {len(frames)} frames from {frames[0]}")
    return found
