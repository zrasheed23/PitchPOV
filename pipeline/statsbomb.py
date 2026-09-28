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
ALIGN_MATCH_S = 1.5  # a logged PFF touch by the same player this close lines the clip's StatsBomb times up
ALIGN_MAX_S = 1.0


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


def clip_events(sb, frame_ms, player_id, attack, logged=()):
    """The StatsBomb events inside a clip: dicts as match_events plus "f" (the
    nearest clip frame), "p" (our player id) and "xy" (our pitch). sb:
    match_events output; player_id(side, number) -> our id or None;
    attack[side]: +1/-1; logged: [(frame, player id)] PFF touches, used to
    line the clip's times up (median gap to the same player's StatsBomb touch,
    within ALIGN_MATCH_S, at most ALIGN_MAX_S). Returns (events, shift s)."""
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
    gaps = []
    for f, pid in logged:
        t = frame_ms[f] / 1000
        near = [e["t"] - t for e in evs if e["on_ball"] and e["p"] == pid and abs(e["t"] - t) <= ALIGN_MATCH_S]
        if near:
            gaps.append(min(near, key=abs))
    shift = 0.0
    if len(gaps) >= 2:
        gaps.sort()
        shift = max(-ALIGN_MAX_S, min(ALIGN_MAX_S, gaps[len(gaps) // 2]))
    out = []
    for e in evs:
        t = e["t"] - shift
        if not lo <= t <= hi:
            continue
        f = min(range(len(frame_ms)), key=lambda i: abs(frame_ms[i] / 1000 - t))
        out.append(dict(e, t=t, f=f))
    return out, shift
