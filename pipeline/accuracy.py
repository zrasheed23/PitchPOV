"""Accuracy report: does a clip break realism anywhere?

Runs on every clip as written (what the viewer plays), so problems surface
without watching everything. Each check returns its findings; a clip with
any is flagged and goes on the review list:
- "touch far": at a touch the ball is more than TOUCH_M from the player.
- "no-touch turn": the ball turns, speeds up or rises with no touch (touch_rule.py).
- "keeper dive": the keeper takes off before the kick, or dives away from
  where the ball comes level with him.
- "statsbomb missing": a StatsBomb on-ball action before the shot with no
  touch by that player within MATCH_S.
- "crossing off": the shot crosses more than CROSS_M from StatsBomb's point.
- "sprint": a player moves faster than SPRINT_MPS (over SPRINT_WINDOW frames).
- "keeper wide": before the kick, a keeper in his own box, outside his
  posts, with the ball in that box, and either more than WIDE_M from
  StatsBomb's freeze-frame spot and wider than it (the defending keeper,
  within FREEZE_WINDOW_S of the kick) or more than WIDE_M outside the post line on
  the side away from the ball. (Keepers narrow the angle.)
- "shot spot": the ball at the kick more than SHOT_SPOT_M from where
  StatsBomb has the shot taken.
- "body pass": the ball goes through a player with no touch by him then
  (players are legs, torso and head: body_radius), up to the goal line; the
  keeper at the shot by his planned reaction (a block the ball can't clear,
  or a dive whose hands get to it).
"""

import math

from touch_rule import violations

CHECKS = ("touch far", "no-touch turn", "keeper dive", "statsbomb missing", "crossing off", "sprint", "keeper wide",
          "shot spot", "body pass")
TOUCH_M = 1.2
MATCH_S = 0.5
CROSS_M = 0.6
SPRINT_MPS = 10.5  # the fastest footballers top out around 10 m/s
SPRINT_WINDOW = 6  # frames (0.2 s): single-frame speeds are tracking noise
HALF_L, POST_Y, AREA_X, AREA_Y = 52.5, 3.66, 52.5 - 16.5, 20.16
POST_MARGIN_M = 0.3
WIDE_M = 2.0  # off StatsBomb's freeze-frame spot, or beyond the post away from the ball, by more than this
FREEZE_WINDOW_S = 1.0  # the freeze frame speaks for this long before the kick
SHOT_SPOT_M = 2.0


BALL_R = 0.11
# A player's body by height (m): legs, torso (and arms at his sides), head.
BODY = ((0.9, 0.18), (1.5, 0.22), (1.85, 0.12))
TOUCH_NEAR_FRAMES = 4


def body_radius(z):
    """His body's radius at height z (None above his head)."""
    return next((r for top, r in BODY if z <= top), None)


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
        # Where the ball comes level with him (the line, for a keeper on it).
        k0 = players[kick].get(dive["keeper"])
        if k0 is not None and cross is not None:
            depth = min(side * k0[0], HALF_L)
            arr = next((k for k in range(kick + 1, cross[0] + 1) if ball[k] and side * ball[k][0] >= depth), cross[0])
            k = players[arr].get(dive["keeper"], k0)
            gap = ball[arr][1] - k[1]
            if abs(gap) > 0.3 and (gap > 0) != (dive["dir"] > 0):
                found["keeper dive"].append(f"dives away from the ball ({gap:+.1f} m)")

    touched = [(c["f"], c["p"]) for c in clip.get("contacts", [])]
    window = MATCH_S / (times[1] - times[0]) if len(times) > 1 else 15
    for e in statsbomb:
        goal_shot = e["type"] == "Shot" and (e["raw"].get("shot") or {}).get("outcome", {}).get("name") == "Goal"
        if not e["on_ball"] or e["f"] >= kick - 3 or goal_shot:  # the goal is the clip's own shot
            continue
        if not any(p == e["p"] and abs(f - e["f"]) <= window for f, p in touched):
            found["statsbomb missing"].append(f"frame {e['f']} {e['type']} by {names.get(e['p'], e['p'])}")

    if placement and "y" in placement and cross is not None and not clip.get("cleared"):
        _, (x, y, z) = cross
        d = math.hypot(y - placement["y"], z - placement.get("z", z))
        if d > CROSS_M:
            found["crossing off"].append(f"{d:.2f} m from StatsBomb")

    shot = next((e for e in statsbomb if e["type"] == "Shot" and e["p"] == clip.get("scorerId")
                 and (e["raw"].get("shot") or {}).get("outcome", {}).get("name") == "Goal"), None)
    if shot is not None and fr[kick]["b"]:
        d = math.dist(fr[kick]["b"][:2], shot["xy"])
        if d > SHOT_SPOT_M:
            found["shot spot"].append(f"kicked {d:.1f} m from StatsBomb's shot location")

    # The ball through a body, up to the goal line.
    touch_at = {}
    for c in clip.get("contacts", []):
        touch_at.setdefault(c["p"], []).append(c["f"])
    dead = set()
    for a, b_, _ in held:
        dead.update(range(a, b_ + 1))
    keeper = (dive or {}).get("keeper")
    end = cross[0] if cross is not None else len(fr)
    passes = {}
    for k in range(min(end, len(fr))):
        b = ball[k]
        if b is None or k in dead:
            continue
        r = body_radius(b[2])
        if r is None:
            continue
        for pid, xy in players[k].items():
            if pid == keeper and k >= kick and not (dive["kind"] == "block" and dive.get("through") != "legs"):
                continue  # a dive (or a ball through his legs) is judged by his planned reaction below
            if any(abs(k - f) <= TOUCH_NEAR_FRAMES for f in touch_at.get(pid, ())):
                continue
            if math.hypot(b[0] - xy[0], b[1] - xy[1]) < r + BALL_R - 0.03:
                passes.setdefault(pid, []).append(k)
    for pid, ks in passes.items():
        runs = sum(1 for n, k in enumerate(ks) if n == 0 or k - ks[n - 1] > 3)
        found["body pass"].append(f"{names.get(pid, pid)} {runs}x from frame {ks[0]}")
    if dive:
        r = body_radius(dive["height"])
        if dive["kind"] == "block" and dive.get("through") != "legs" and r is not None \
                and dive["gap"] < r + BALL_R + 0.05:
            found["body pass"].append(f"through the keeper ({dive['gap']:.2f} m from him at {dive['height']:.1f} m)")
        if dive["kind"] == "dive" and dive.get("reached"):
            found["body pass"].append("through the keeper's hands")

    for pid in names:
        worst = 0.0
        for k in range(SPRINT_WINDOW, len(fr)):
            a, b = players[k - SPRINT_WINDOW].get(pid), players[k].get(pid)
            if a and b and times[k] > times[k - SPRINT_WINDOW]:
                worst = max(worst, math.dist(a, b) / (times[k] - times[k - SPRINT_WINDOW]))
        if worst > SPRINT_MPS:
            found["sprint"].append(f"{names[pid]} {worst:.1f} m/s")

    freeze = tuple(placement["keeper"]) if placement and placement.get("keeper") else None
    freeze_from = next((k for k in range(kick, -1, -1) if times[kick] - times[k] > FREEZE_WINDOW_S), 0)
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
            if not (in_box(q) and in_box(b) and abs(q[1]) > POST_Y + POST_MARGIN_M):
                continue
            # Keepers narrow the angle. Near the kick, StatsBomb's freeze frame says
            # where he was; otherwise only well outside the post away from the ball.
            if freeze is not None and p["id"] == keeper and k >= freeze_from:
                if math.dist(q, freeze) > WIDE_M and abs(q[1]) > abs(freeze[1]):  # wider than StatsBomb has him
                    frames.append(k)
            elif abs(q[1]) > POST_Y + WIDE_M and (q[1] > 0) != (b[1] > 0):
                frames.append(k)
        if frames:
            found["keeper wide"].append(f"{p['name']} {len(frames)} frames from {frames[0]}")
    return found
