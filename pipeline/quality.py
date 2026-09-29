"""Quality checks: every clip against the measured data, one score per clip.

Runs on the clip as written (what the viewer plays) with the clip's StatsBomb
events and its shot_placement.json entry, so clips nobody has watched can be
ranked by how likely they are to look wrong. Each check returns findings with
a severity (1.0 = just over its threshold); a clip with none passes.

- "freeze frame": each player in StatsBomb's freeze frame at the shot against
  the nearest tracked player of his team at the kick (median and max).
- "wrong label": a named freeze-frame player whose own track is FF_NAME_M or
  more from where StatsBomb has him, with a teammate's track right there.
- "shot origin": the ball at the kick, and the shooter, at StatsBomb's shot
  location.
- "end location": the ball crossing the goal line at StatsBomb's end location.
- "offside at assist": the scorer offside when the last pass to him is played
  (offside.py); every goal stood.
- "event sequence": every touch before the kick matches a StatsBomb event by
  the same player (within EVENT_S, and EVENT_M of its location); dribble
  pushes match his carry.
- "carry": during a StatsBomb carry the ball stays at the carrier's feet.
- "acceleration", "teleport", "keeper speed", "post-goal": players move like
  people (the post-goal seconds with a lower bar: broadcast tracking there is
  often replays and celebrations).
- "keeper in path": a beaten keeper stands clear of the shot, hands included.
- "acrobatic": a scissor or bicycle kick (rare: always worth a look).
The accuracy report's checks (accuracy.py) are folded in as well.
"""

import math
import statistics

from accuracy import BALL_R, _crossing, _side
from keepers import keeper_radius
from offside import TOLERANCE_M as OFFSIDE_M, find_assist, margin

FF_MEDIAN_M = 2.0
FF_MAX_M = 5.0
FF_NAME_M = 3.0
FF_NAME_NEAR_M = 1.5
ORIGIN_BALL_M = 2.0
ORIGIN_SHOOTER_M = 1.5
END_M = 0.6
EVENT_S = 1.0
EVENT_M = 5.0  # StatsBomb's locations sit 1-2 m from the tracking (freeze frames, median): 5 m is a real miss
CARRY_M = 3.0
CARRY_GAP_S = 0.3
CARRY_MIN_S = 0.6
CARRY_LOFT_Z = 1.5
WINDOW = 6  # frames (0.2 s) for speeds and accelerations
ACCEL_MPS2 = 12.0  # beyond any footballer's acceleration or braking
TELEPORT_MPS = 15.0  # between two frames
KEEPER_MPS = 8.0
POST_SPEED_MPS = 9.0
POST_ACCEL_MPS2 = 8.0
ACROBATIC = ("scissor", "bicycle")
SEVERITY_CAP = 3.0

WEIGHTS = {"shot origin": 3.0, "offside at assist": 2.0, "end location": 2.0, "freeze frame": 1.5, "wrong label": 1.5,
           "event sequence": 1.5, "carry": 1.5, "keeper in path": 2.0, "acrobatic": 1.0, "acceleration": 1.0,
           "teleport": 2.0, "keeper speed": 1.0, "post-goal": 1.5,
           # from accuracy.py ("shot spot" and "crossing off" are covered by shot origin and end location)
           "shooter": 3.0, "touch far": 1.5, "no-touch turn": 1.5, "keeper dive": 1.5, "statsbomb missing": 1.0, "sprint": 1.0,
           "keeper wide": 1.0, "body pass": 1.5}
CHECKS = tuple(WEIGHTS)


def _goal_shot(clip, statsbomb):
    return next((e for e in statsbomb if e["type"] == "Shot" and e["p"] == clip.get("scorerId")
                 and (e["raw"].get("shot") or {}).get("outcome", {}).get("name") == "Goal"), None)


def freeze_frame(clip, shot):
    """(findings, stats): the freeze frame against the tracked players at the kick."""
    found = {"freeze frame": [], "wrong label": []}
    if shot is None or not shot.get("ff"):
        return found, None
    kick = clip.get("kickFrame", clip["goalFrame"])
    at = clip["frames"][kick]["p"]
    team = {p["id"]: p["team"] for p in clip["players"]}
    names = {p["id"]: p["name"] for p in clip["players"]}
    near = []
    for pid, side, xy, _ in shot["ff"]:
        mates = [(math.dist(q, xy), other) for other, q in at.items() if team.get(other) == side]
        if not mates:
            continue
        d, who = min(mates)
        near.append((d, pid, who))
        own = at.get(pid)
        if pid is not None and own is not None and who != pid and d <= FF_NAME_NEAR_M:
            gap = math.dist(own, xy)
            if gap >= FF_NAME_M:
                found["wrong label"].append((gap / FF_NAME_M, f"{names.get(pid)}: his track is {gap:.1f} m from "
                                             f"StatsBomb's spot, {names.get(who)}'s is {d:.1f} m"))
    if not near:
        return found, None
    ds = [d for d, _, _ in near]
    med, worst = statistics.median(ds), max(near)
    if med > FF_MEDIAN_M:
        found["freeze frame"].append((med / FF_MEDIAN_M, f"median {med:.1f} m"))
    if worst[0] > FF_MAX_M:
        found["freeze frame"].append((worst[0] / FF_MAX_M, f"max {worst[0]:.1f} m (nearest to "
                                                           f"{names.get(worst[1], 'an unnamed player')}'s spot)"))
    return found, {"median": round(med, 2), "max": round(worst[0], 2), "players": len(ds)}


def shot_origin(clip, shot):
    out = []
    kick = clip.get("kickFrame", clip["goalFrame"])
    fr = clip["frames"][kick]
    if shot is None or not fr["b"]:
        return out
    d = math.dist(fr["b"][:2], shot["xy"])
    if d > ORIGIN_BALL_M:
        out.append((d / ORIGIN_BALL_M, f"ball kicked {d:.1f} m from StatsBomb's shot location"))
    p = fr["p"].get(clip["scorerId"])
    if p is not None:
        d = math.dist(p, shot["xy"])
        if d > ORIGIN_SHOOTER_M:
            out.append((d / ORIGIN_SHOOTER_M, f"shooter {d:.1f} m from StatsBomb's shot location"))
    return out


def end_location(clip, placement):
    if not placement or "y" not in placement or clip.get("cleared"):
        return []
    cross = _crossing(clip, _side(clip))
    if cross is None:
        return [(SEVERITY_CAP, "the ball never crosses the goal line")]
    _, (_, y, z) = cross
    d = math.hypot(y - placement["y"], z - placement.get("z", z))
    return [(d / END_M, f"crosses {d:.2f} m from StatsBomb's end location")] if d > END_M else []


def _carry_frames(e, times):
    """A StatsBomb carry (or dribble) as (first frame, last frame) in the clip."""
    t0 = times[e["f"]]
    t1 = t0 + (e["raw"].get("duration") or 0.0)
    return e["f"], next((k for k in range(e["f"], len(times)) if times[k] >= t1), len(times) - 1)


def event_sequence(clip, statsbomb):
    """Touches before the kick with no StatsBomb event behind them."""
    out = []
    if not any(e["on_ball"] for e in statsbomb):
        return out  # StatsBomb doesn't cover the clip
    fr, times = clip["frames"], [f["t"] for f in clip["frames"]]
    kick = clip.get("kickFrame", clip["goalFrame"])
    names = {p["id"]: p["name"] for p in clip["players"]}
    carries = [(e["p"], *_carry_frames(e, times)) for e in statsbomb if e["type"] in ("Carry", "Dribble")]
    half = max(1, round(0.5 / max(times[1] - times[0], 1e-6))) if len(times) > 1 else 15
    for c in clip.get("contacts", []):
        f, pid = c["f"], c["p"]
        if f >= kick:
            continue
        if c.get("s") == 1 and any(p == pid and a - half <= f <= b + half for p, a, b in carries):
            continue  # a dribble push in his carry
        mine = [e for e in statsbomb if e["p"] == pid and e["on_ball"] and abs(times[e["f"]] - times[f]) <= EVENT_S]
        if not mine:
            out.append((1.0, f"frame {f} {names.get(pid)}: no StatsBomb event by him within {EVENT_S:.0f} s"))
            continue
        b = fr[f]["b"]
        if b:
            e = min(mine, key=lambda e: math.dist(b[:2], e["xy"]))
            d = math.dist(b[:2], e["xy"])
            if d > EVENT_M:
                out.append((d / EVENT_M, f"frame {f} {names.get(pid)}: {d:.1f} m from StatsBomb's {e['type']}"))
    return out


def carry(clip, statsbomb):
    """StatsBomb carries where the ball leaves the carrier's feet."""
    out = []
    fr, times = clip["frames"], [f["t"] for f in clip["frames"]]
    kick = clip.get("kickFrame", clip["goalFrame"])
    names = {p["id"]: p["name"] for p in clip["players"]}
    for e in statsbomb:
        if e["type"] != "Carry" or (e["raw"].get("duration") or 0.0) < CARRY_MIN_S:
            continue
        a, b = _carry_frames(e, times)
        b = min(b, kick - 1)
        run, run_from, worst, longest, high = 0, None, 0.0, 0.0, 0.0
        for k in range(a, b + 1):
            ball, p = fr[k]["b"], fr[k]["p"].get(e["p"])
            if not ball or p is None:
                continue
            d = math.dist(ball[:2], p)
            if times[k] - times[a] > 0.3:
                high = max(high, ball[2])
            if d > CARRY_M:
                run_from = k if run_from is None else run_from
                worst = max(worst, d)
                longest = max(longest, times[k] - times[run_from])
            else:
                run_from = None
        if longest >= CARRY_GAP_S:
            out.append((worst / CARRY_M, f"frame {a} {names.get(e['p'])}'s carry: ball up to {worst:.1f} m from "
                                         f"him for {longest:.1f} s"))
        if high > CARRY_LOFT_Z:
            out.append((high / CARRY_LOFT_Z, f"frame {a} {names.get(e['p'])}'s carry: ball {high:.1f} m up"))
    return out


def movement(clip):
    """Accelerations, teleports, keeper speeds, and the post-goal seconds."""
    found = {"acceleration": [], "teleport": [], "keeper speed": [], "post-goal": []}
    fr, times = clip["frames"], [f["t"] for f in clip["frames"]]
    n = len(fr)
    cross = _crossing(clip, _side(clip))
    after = cross[0] if cross is not None else n
    for p in clip["players"]:
        pid, name = p["id"], p["name"]
        track = [f["p"].get(pid) for f in fr]
        if any(q is None for q in track) or n <= 2 * WINDOW:
            continue
        v = [None] * n
        for k in range(WINDOW, n):
            dt = times[k] - times[k - WINDOW]
            v[k] = ((track[k][0] - track[k - WINDOW][0]) / dt, (track[k][1] - track[k - WINDOW][1]) / dt)
        acc = [(0.0, 0)] + [(math.dist(v[k], v[k - WINDOW]) / (times[k] - times[k - WINDOW]), k)
                            for k in range(2 * WINDOW, n)]
        a, k = max(acc)
        if a > ACCEL_MPS2:
            found["acceleration"].append((a / ACCEL_MPS2, f"{name} {a:.0f} m/s² at frame {k}"))
        jump = max((math.dist(track[k], track[k - 1]) / (times[k] - times[k - 1]), k) for k in range(1, n))
        if jump[0] > TELEPORT_MPS:
            found["teleport"].append((jump[0] / TELEPORT_MPS, f"{name} {jump[0]:.0f} m/s at frame {jump[1]}"))
        if p["position"] == "GK":
            s, k = max((math.hypot(*v[k]), k) for k in range(WINDOW, n))
            if s > KEEPER_MPS:
                found["keeper speed"].append((s / KEEPER_MPS, f"{name} {s:.1f} m/s at frame {k}"))
        post_a = max(((a, k) for a, k in acc if k >= after), default=(0.0, None))
        post_s = max(((math.hypot(*v[k]), k) for k in range(max(after, WINDOW), n)), default=(0.0, None))
        if post_a[0] > POST_ACCEL_MPS2:
            found["post-goal"].append((post_a[0] / POST_ACCEL_MPS2, f"{name} {post_a[0]:.0f} m/s² at frame {post_a[1]}"))
        if post_s[0] > POST_SPEED_MPS:
            found["post-goal"].append((post_s[0] / POST_SPEED_MPS, f"{name} {post_s[0]:.1f} m/s at frame {post_s[1]}"))
    return found


def keeper_in_path(clip):
    dive = clip.get("keeperDive")
    if not dive:
        return []
    r = keeper_radius(dive["height"])
    if dive["kind"] == "block" and dive.get("through") != "legs" and r is not None and dive["gap"] < r + BALL_R:
        need = r + BALL_R
        return [(1.0 + (need - dive["gap"]) / need, f"the shot passes {dive['gap']:.2f} m from the beaten keeper "
                                                    f"at {dive['height']:.1f} m (he blocks {r} m out there)")]
    if dive["kind"] == "dive" and dive.get("reached"):
        return [(1.0, "the beaten keeper's dive gets to the ball")]
    return []


def score(found):
    """One number per clip: each check's worst severity (capped), plus a little
    per extra finding, weighted."""
    total = 0.0
    for check, items in found.items():
        if items:
            worst = min(max(s for s, _ in items), SEVERITY_CAP)
            total += WEIGHTS.get(check, 1.0) * (worst + min(0.25 * (len(items) - 1), 1.0))
    return round(total, 2)


def offside_at_assist(clip, statsbomb):
    """The scorer past the second-last defender and the ball when the last pass to him is played (offside.py)."""
    if clip.get("ownGoal") or clip.get("penalty"):
        return []
    kick = clip.get("kickFrame", clip["goalFrame"])
    e, f = find_assist(statsbomb, clip["scorerId"], kick, clip.get("contacts", []))
    if e is None:
        return []
    fr = clip["frames"][f]
    team = {p["id"]: p["team"] for p in clip["players"]}
    m = margin(fr["p"], fr["b"][:2] if fr["b"] else None, team, clip["scorerId"], _side(clip))
    if m is None or m <= OFFSIDE_M:
        return []
    names = {p["id"]: p["name"] for p in clip["players"]}
    return [(m / OFFSIDE_M, f"{m:.1f} m offside at frame {f} ({names.get(e['p'], e['p'])}'s pass)")]


def check_quality(clip, statsbomb=(), placement=None, accuracy=None):
    """{"checks": {check: [text, ...]}, "severity": {check: worst}, "score",
    "freeze": {"median", "max", "players"} or None} for one clip dict."""
    statsbomb = list(statsbomb)
    shot = _goal_shot(clip, statsbomb)
    found = {c: [] for c in CHECKS}
    ff, freeze = freeze_frame(clip, shot)
    found.update(ff)
    found["shot origin"] = shot_origin(clip, shot)
    found["offside at assist"] = offside_at_assist(clip, statsbomb)
    found["end location"] = end_location(clip, placement)
    found["event sequence"] = event_sequence(clip, statsbomb)
    found["carry"] = carry(clip, statsbomb)
    found.update(movement(clip))
    found["keeper in path"] = keeper_in_path(clip)
    if clip.get("shotPose") in ACROBATIC:
        found["acrobatic"] = [(1.0, f"{clip['shotPose']} kick: acrobatic poses need a look")]
    for check, items in (accuracy or {}).items():
        if check in found:
            found[check] = [(1.0, text) for text in items]
    return {"checks": {c: [t for _, t in items] for c, items in found.items()},
            "severity": {c: round(max(s for s, _ in items), 2) for c, items in found.items() if items},
            "score": score(found), "freeze": freeze}
