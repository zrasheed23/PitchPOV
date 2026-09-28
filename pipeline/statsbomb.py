"""StatsBomb Open Data events, on PFF's clock and our pitch.

StatsBomb's free 2022 World Cup events (data/statsbomb, cached by
shot_placement.py; fetch lineups with curl if missing) have every on-ball
action with its player, location and body part. Their times run from each
period's start; PFF's from the start of the video. The offset per period is
the median gap between the same player's touches in both feeds at about the
same game clock. match_events loads a match once; clip_events picks out one
clip's window, converts locations to our pitch and lines the times up with
the clip's logged touches.

StatsBomb coordinates: 120 x 80, the team on the ball attacking toward
x = 120, y increasing to the attacker's right.
"""

import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

CACHE = Path("data/statsbomb")
COMPETITION, SEASON = 43, 106
CLOCK_MATCH_S = 5  # same player, game clocks this close: the same touch in both feeds
PFF_TOUCHES = {"PA", "SH", "CR", "CL", "RE", "TC", "IT"}
# Keeper actions that are a touch of the ball (not "Shot Faced", "Goal Conceded"...).
KEEPER_TOUCHES = {"Collected", "Punch", "Save", "Smother", "Keeper Sweeper", "Shot Saved"}
BODY = {"Head": "H", "Right Foot": "R", "Left Foot": "L", "Keeper Arm": "X", "Both Hands": "X", "Left Hand": "X",
        "Right Hand": "X"}
PITCH_L, PITCH_W = 105.0, 68.0
ALIGN_MAX_S = 10.0
OFFSET_PULL = 1.5  # m per second away from the period's offset
SHIFT_CAP_M = 15.0  # a worse miss than this counts as this much (a few mislabelled events can't dominate)


def _ts(stamp):
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@lru_cache(maxsize=None)
def _match_id(home, away):
    path = CACHE / "matches" / str(COMPETITION) / f"{SEASON}.json"
    if not path.exists():
        return None
    for m in json.loads(path.read_text()):
        if {m["home_team"]["home_team_name"], m["away_team"]["away_team_name"]} == {home, away}:
            return m["match_id"]
    return None


def on_ball(e):
    """The event is a touch of the ball (StatsBomb type and outcome)."""
    kind = e["type"]["name"]
    if kind in ("Pass", "Shot", "Clearance", "Block", "Interception", "Dribble", "Miscontrol"):
        return True
    if kind == "Ball Receipt*":
        return (e.get("ball_receipt") or {}).get("outcome", {}).get("name") != "Incomplete"
    if kind == "Ball Recovery":
        return not (e.get("ball_recovery") or {}).get("recovery_failure")
    if kind == "Goal Keeper":
        return (e.get("goalkeeper") or {}).get("type", {}).get("name") in KEEPER_TOUCHES
    return False


def body_part(e):
    """R/L foot, H head, X hands, F foot (side unknown)."""
    detail = e.get(e["type"]["name"].lower().replace(" ", "_")) or {}
    part = (detail.get("body_part") or {}).get("name")
    if e["type"]["name"] == "Goal Keeper":
        return "X"
    return BODY.get(part, "F")


def match_events(meta, roster, events):
    """Every StatsBomb event with a player and a location, as dicts {"period",
    "t": PFF video time (s), "side", "number", "type", "on_ball", "b": body
    part, "loc", "raw": the StatsBomb event}; None if the match isn't cached."""
    return _match_events(str(meta["id"]), meta["homeTeam"]["name"], meta["awayTeam"]["name"],
                         meta["homeTeam"]["id"], _roster_key(roster), _events_key(events))


def _roster_key(roster):
    return tuple(sorted((r["player"]["id"], r["team"]["id"], str(r["shirtNumber"])) for r in roster))


def _events_key(events):
    out = []
    for e in events:
        g = e.get("gameEvents") or {}
        if ((e.get("possessionEvents") or {}).get("possessionEventType") in PFF_TOUCHES and e.get("eventTime") is not None
                and g.get("startGameClock") is not None and g.get("playerId") is not None):
            out.append((e["eventTime"], g["period"], g["startGameClock"], str(g["playerId"])))
    return tuple(out)


@lru_cache(maxsize=None)
def _match_events(game_id, home, away, home_id, roster, touches):
    sb_id = _match_id(home, away)
    ev_path, lu_path = CACHE / "events" / f"{sb_id}.json", CACHE / "lineups" / f"{sb_id}.json"
    if sb_id is None or not ev_path.exists() or not lu_path.exists():
        return None
    side_of = {home: "home", away: "away"}
    number = {p["player_id"]: (side_of[t["team_name"]], str(p["jersey_number"]))
              for t in json.loads(lu_path.read_text()) if t["team_name"] in side_of for p in t["lineup"]}
    raw = [e for e in json.loads(ev_path.read_text())
           if e["period"] <= 4 and "player" in e and "location" in e and e["player"]["id"] in number]
    shirt = {pid: ("home" if team == home_id else "away", num) for pid, team, num in roster}
    by_who = defaultdict(list)
    for e in raw:
        by_who[(e["period"], number[e["player"]["id"]])].append((e["minute"] * 60 + e["second"], _ts(e["timestamp"])))
    gaps = defaultdict(list)
    for t, period, clock, pid in touches:
        if pid not in shirt:
            continue
        near = [t - sbt for c, sbt in by_who[(period, shirt[pid])] if abs(c - clock) <= CLOCK_MATCH_S]
        if len(near) == 1:
            gaps[period].append(near[0])
    offset = {p: sorted(d)[len(d) // 2] for p, d in gaps.items() if d}
    out = []
    for e in raw:
        p = e["period"]
        if p not in offset:
            continue
        side, num = number[e["player"]["id"]]
        out.append({"period": p, "t": _ts(e["timestamp"]) + offset[p], "side": side, "number": num,
                    "type": e["type"]["name"], "on_ball": on_ball(e), "b": body_part(e), "loc": e["location"],
                    "raw": e})
    return tuple(out)


def to_pitch(loc, attack):
    """A StatsBomb location -> our (x, y), for a team attacking x = attack * 52.5."""
    x = (loc[0] / 120 - 0.5) * PITCH_L
    y = -(loc[1] / 80 - 0.5) * PITCH_W
    return attack * x, attack * y


def clip_events(sb, frame_ms, player_id, attack, where=None):
    """The StatsBomb events inside a clip: dicts as match_events plus "f" (the
    nearest clip frame), "p" (our player id) and "xy" (our pitch). sb:
    match_events output; player_id(side, number) -> our id or None;
    attack[side]: +1/-1; where(player id, PFF video time s) -> (his tracked
    (x, y) or None, the tracked ball's (x, y) or None), used to line the clip's
    times up. Returns (events, shift s)."""
    if not sb or not frame_ms:
        return [], 0.0
    lo, hi = frame_ms[0] / 1000, frame_ms[-1] / 1000
    evs = []
    for e in sb:
        if not lo - ALIGN_MAX_S <= e["t"] <= hi + ALIGN_MAX_S:
            continue
        pid = player_id(e["side"], e["number"])
        if pid is None:
            continue
        evs.append(dict(e, p=pid, xy=to_pitch(e["loc"], attack[e["side"]])))
    # PFF's video clock drifts against the period clock (up to ~5 s by the
    # goal), so each clip gets its own shift: the one where StatsBomb's event
    # locations best match where the tracking has those players and the ball
    # (measured on both sides), searched within ALIGN_MAX_S. (Pinning the
    # StatsBomb goal to the kick instead matched the tracking and PFF's own
    # touches worse across all clips.)
    shift = 0.0
    if where is not None:
        on = [e for e in evs if e["on_ball"]]

        def miss(sh):
            ds = []
            for e in on:
                p, b = where(e["p"], e["t"] - sh)
                if p is not None:
                    ds.append(min(math.dist(p, e["xy"]), SHIFT_CAP_M))
                if b is not None:  # the ball moves fast: it pins the time down
                    ds.append(min(math.dist(b, e["xy"]), SHIFT_CAP_M))
            return sum(ds) / len(ds) if len(ds) >= 3 else math.inf

        # A shift far from the period's offset needs better evidence.
        def cost(sh):
            return miss(sh) + OFFSET_PULL * abs(sh)

        coarse = min((k / 10 for k in range(-int(ALIGN_MAX_S * 10), int(ALIGN_MAX_S * 10) + 1)),
                     key=lambda sh: (cost(sh), abs(sh)))
        if cost(coarse) < math.inf:
            shift = min((coarse + k / 100 for k in range(-10, 11)), key=cost)
    out = []
    for e in evs:
        t = e["t"] - shift
        if not lo <= t <= hi:
            continue
        f = min(range(len(frame_ms)), key=lambda i: abs(frame_ms[i] / 1000 - t))
        out.append(dict(e, t=t, f=f))
    return out, shift


FIND_S = 0.4  # look this far either side of a StatsBomb touch for the tracked ball at its spot
AT_SPOT_M = 2.0  # the tracked ball this close to StatsBomb's location is the touch
SAME_TOUCH_S = 0.3  # a PFF touch by the same player this close is the same touch
TOUCH_Z = {"H": 1.9, "X": 1.3}  # ball height where it's placed: head, hands; feet on the ground
HIGH_PASS_PEAK_M = 3.0
MAX_CONNECT_MPS = 35.0
TRACKED_AGREE_M = 2.0  # faster than this between two touches, one of them is mistimed  # a StatsBomb "High Pass" (crosses, long balls) goes at least this high


def merge_touches(contacts, sb, ball, tracked, times, last_frame, shooter=None, player_frames=None):
    """StatsBomb's on-ball actions as the clip's touches, merged with PFF's.

    Each StatsBomb touch before `last_frame` (not the goal itself) is timed to
    the frame within FIND_S where the tracked ball is nearest its location; if
    the tracked ball is never within AT_SPOT_M of it but is at the tracked
    player's feet (TRACKED_AGREE_M), the touch is there (two measured sources
    against one); otherwise the ball is put at StatsBomb's spot (at head or
    hand height for those body parts) and the path is re-drawn through it later. A PFF touch by the same player within SAME_TOUCH_S is
    the same touch and is dropped. The shooter's actions within SAME_TOUCH_S
    of `last_frame` (the goal) are the shot itself: PFF's shot touch stays. Returns (contacts, ball, placed frames,
    lofted {frame: minimum peak}, counts)."""
    out, ball = list(ball), list(ball)
    fps_dt = (times[-1] - times[0]) / max(len(times) - 1, 1)
    find = int(round(FIND_S / fps_dt))
    same = int(round(SAME_TOUCH_S / fps_dt))
    counts = {"statsbomb": 0, "added": 0, "pff_replaced": 0, "placed": 0, "dropped": 0, "tracked": 0}
    touches, placed, lofted = [], [], {}
    wanted = []
    for e in sorted(sb, key=lambda e: e["f"]):
        if not e["on_ball"] or e["f"] > last_frame:
            continue
        shot = e["raw"].get("shot") or {}
        if e["type"] == "Shot" and shot.get("outcome", {}).get("name") == "Goal":
            continue  # the goal is the clip's own shot
        if e["p"] == shooter and last_frame - e["f"] <= same:
            continue  # the shot itself
        if wanted and wanted[-1]["p"] == e["p"] and e["f"] - wanted[-1]["f"] <= 3:
            continue  # a recovery and the carry or pass that starts from it: one touch
        wanted.append(e)
    for n, e in enumerate(wanted):
        # Timed between its neighbours, so the touches keep StatsBomb's order.
        lo = max(e["f"] - find, touches[-1]["f"] + 2 if touches else 0, 0)
        hi = min(e["f"] + find, wanted[n + 1]["f"] - 2 if n + 1 < len(wanted) else last_frame, last_frame)
        best = None
        for k in range(lo, hi + 1):
            if tracked[k] and ball[k] is not None:
                d = math.dist(ball[k][:2], e["xy"])
                if d <= AT_SPOT_M and (best is None or d < best[0]):
                    best = (d, k)
        f = best[1] if best else min(max(e["f"], lo), max(hi, lo))
        if best is None and player_frames is not None:
            # The tracked ball and the tracked player agree with each other but
            # not with StatsBomb's spot: the touch is where the tracking has it.
            agree = None
            for k in range(lo, hi + 1):
                p = player_frames[k].get(e["p"])
                if tracked[k] and ball[k] is not None and p is not None:
                    d = math.hypot(ball[k][0] - p[0], ball[k][1] - p[1])
                    if d <= TRACKED_AGREE_M and (agree is None or d < agree[0]):
                        agree = (d, k)
            if agree is not None:
                best, f = agree, agree[1]
                counts["tracked"] += 1
        if best is None:
            z = TOUCH_Z.get(e["b"], 0.11)
            ball[f] = (e["xy"][0], e["xy"][1], z)
            placed.append(f)
            counts["placed"] += 1
        c = {"f": f, "p": e["p"], "b": e["b"], "sb": e["type"]} | ({"tr": 1} if best is not None else {})
        if e["type"] == "Pass" and (e["raw"].get("pass") or {}).get("height", {}).get("name") == "High Pass":
            lofted[f] = HIGH_PASS_PEAK_M
        touches.append(c)
        counts["statsbomb"] += 1
    # Two touches no kick could connect (StatsBomb times are rough): slide the
    # later one within its window, else drop whichever isn't backed by the tracking.
    k = 1
    while k < len(touches):
        a, b = touches[k - 1], touches[k]
        need = lambda fa, fb: (math.dist(ball[fa][:2], ball[fb][:2]) / (times[fb] - times[fa])
                               if times[fb] > times[fa] else math.inf)
        if need(a["f"], b["f"]) <= MAX_CONNECT_MPS:
            k += 1
            continue
        nxt = touches[k + 1]["f"] - 2 if k + 1 < len(touches) else last_frame
        later = next((f for f in range(b["f"] + 1, min(b["f"] + find, nxt) + 1)
                      if need(a["f"], f) <= MAX_CONNECT_MPS), None)
        if later is not None and b["f"] in placed:
            ball[later], ball[b["f"]] = ball[b["f"]], out[b["f"]]
            placed[placed.index(b["f"])] = later
            b["f"] = later
            k += 1
            continue
        drop = k if b["f"] in placed or a["f"] not in placed else k - 1
        gone = touches.pop(drop)
        if gone["f"] in placed:
            placed.remove(gone["f"])
            ball[gone["f"]] = out[gone["f"]]
            counts["placed"] -= 1
        counts["statsbomb"] -= 1
        counts["dropped"] += 1
    # A touch placed in the clip's first frames: the ball was already there.
    if touches and touches[0]["f"] in placed and times[touches[0]["f"]] - times[0] < 0.2:
        for k in range(touches[0]["f"]):
            ball[k] = ball[touches[0]["f"]]
    counts["added"] = sum(1 for t in touches  # actions PFF doesn't have
                          if not any(p["p"] == t["p"] and abs(p["f"] - t["f"]) <= same for p in contacts))
    kept = []
    for c in contacts:
        shot = c["p"] == shooter and last_frame - c["f"] <= same
        if not shot and any(t["p"] == c["p"] and abs(t["f"] - c["f"]) <= same for t in touches) and c["f"] <= last_frame:
            counts["pff_replaced"] += 1
            continue
        kept.append(c)
    merged = sorted(kept + touches, key=lambda c: c["f"])
    # Two touches on one frame: StatsBomb's wins.
    final = []
    for c in merged:
        if final and final[-1]["f"] == c["f"]:
            if "sb" in c and "sb" not in final[-1]:
                final[-1] = c
            continue
        final.append(c)
    return final, ball, placed, lofted, counts
