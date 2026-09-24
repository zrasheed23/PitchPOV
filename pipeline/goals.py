"""Find the goals in a match's PFF events and build the goal index.

Goals are shots with shotOutcomeType == "G". PFF also writes an OUT event with
outType "H"/"A" (a goal for the home/away team) after every goal; an OUT marker
with no matching goal shot, followed by the conceding team's kickoff, is an own
goal, credited to the conceding team's last player on the ball. A goal shot
with no OUT marker is a disallowed goal (VAR/offside) and is left out.
Shootout kicks (anything after the last END event) are left out.
"""

import math

GOAL_MARKERS = {"H": "home", "A": "away"}
OTHER_SIDE = {"home": "away", "away": "home"}

# Period -> the minute its regular time ends, for stoppage labels like 45+2'.
PERIOD_END_MINUTE = {1: 45, 2: 90, 3: 105, 4: 120}

# PFF's "week" is the round of the tournament (confirmed by the match counts,
# 16/16/16/8/4/2/1/1). Weeks 1–3 are group matchdays.
STAGES = {4: "Round of 16", 5: "Quarter-final", 6: "Semi-final", 7: "Third place", 8: "Final"}


def _side(event):
    return "home" if event["gameEvents"]["homeTeam"] else "away"


def _clock(event):
    pe = event.get("possessionEvents") or {}
    ge = event["gameEvents"]
    if pe.get("gameClock") is not None:
        return pe["gameClock"], pe["formattedGameClock"]
    return ge["startGameClock"], ge["startFormattedGameClock"]


def _goal(event, scorer_id, scorer_name, side, own_goal):
    seconds, formatted = _clock(event)
    return {
        "gameId": str(event["gameId"]),
        "gameEventId": event["gameEventId"],
        "eventTime": event["eventTime"],
        "period": event["gameEvents"]["period"],
        "gameClock": seconds,
        "clock": formatted,
        "scorerId": str(scorer_id),
        "scorer": scorer_name,
        "side": side,  # the scorer's team
        "ownGoal": own_goal,
        "forSide": OTHER_SIDE[side] if own_goal else side,  # the team the goal counts for
    }


def find_goals(events):
    """Return (goals, problems, disallowed) for one match, each in match order.

    disallowed holds goal shots with no OUT goal marker (VAR/offside), in the
    same shape as goals. problems lists OUT markers that are neither a goal
    shot nor a clear own goal.
    """
    events = sorted(events, key=lambda e: e["eventTime"])
    ends = [e["eventTime"] for e in events if e["gameEvents"]["gameEventType"] == "END"]
    last_end = max(ends) if ends else math.inf
    in_game = [e for e in events if e["eventTime"] <= last_end]

    goals, problems = [], []
    claimed = set()  # gameEventIds of goal shots matched to a marker
    own_goal_shots = set()
    prev_marker_i = -1
    for i, e in enumerate(in_game):
        ge = e["gameEvents"]
        if ge["gameEventType"] != "OUT" or ge.get("outType") not in GOAL_MARKERS:
            continue
        for_side = GOAL_MARKERS[ge["outType"]]
        since_last = in_game[prev_marker_i + 1 : i]
        prev_marker_i = i

        shot = next(
            (s for s in reversed(since_last)
             if (s.get("possessionEvents") or {}).get("shotOutcomeType") == "G"
             and s["gameEventId"] not in claimed),
            None,
        )
        if shot is not None and _side(shot) == for_side:
            claimed.add(shot["gameEventId"])
            continue

        # No goal shot by the scoring team: an own goal if the conceding team
        # kicks off next. Otherwise (e.g. a disallowed goal) skip it.
        restart = next((n for n in in_game[i + 1 :] if n["gameEvents"]["gameEventType"] != "OUT"), None)
        conceding = OTHER_SIDE[for_side]
        kicks_off = restart is None or restart["gameEvents"]["gameEventType"] == "END" or (
            restart["gameEvents"].get("setpieceType") == "K" and _side(restart) == conceding
        )
        toucher = next(
            (s for s in reversed(since_last)
             if s["gameEvents"]["gameEventType"] == "OTB"
             and s["gameEvents"].get("playerId") is not None
             and _side(s) == conceding),
            None,
        )
        if not kicks_off or toucher is None:
            problems.append(f"game {e['gameId']}: OUT goal marker at {e['eventTime']:.1f}s "
                            f"({for_side}) with no goal shot, skipped")
            continue
        if shot is not None and shot["gameEventId"] == toucher["gameEventId"]:
            own_goal_shots.add(shot["gameEventId"])  # PFF logged the own goal as a goal shot
        ge_t = toucher["gameEvents"]
        goals.append(_goal(toucher, ge_t["playerId"], ge_t["playerName"], conceding, own_goal=True))

    disallowed = []
    for s in in_game:
        pe = s.get("possessionEvents") or {}
        if pe.get("shotOutcomeType") != "G" or s["gameEventId"] in own_goal_shots:
            continue
        # A cross that goes straight in has no shooter: use the player on the ball.
        scorer_id = pe.get("shooterPlayerId") or s["gameEvents"].get("playerId")
        scorer_name = pe.get("shooterPlayerName") or s["gameEvents"].get("playerName")
        goal = _goal(s, scorer_id, scorer_name, _side(s), own_goal=False)
        (goals if s["gameEventId"] in claimed else disallowed).append(goal)

    goals.sort(key=lambda g: g["eventTime"])
    return goals, problems, disallowed


def minute_label(period, game_clock_s):
    """Broadcast-style minute: 35:21 -> 36', 45:30 in the first half -> 45+1'."""
    minute = int(game_clock_s // 60) + 1
    end = PERIOD_END_MINUTE.get(period)
    if end is not None and minute > end:
        return f"{end}+{minute - end}'"
    return f"{minute}'"


def stage_label(week):
    if week in (1, 2, 3):
        return f"Group stage · Matchday {week}"
    return STAGES.get(week)


def clip_name(goal):
    return f"{goal['gameId']}_{goal['gameEventId']}.json"


def build_index(matches):
    """One index entry per goal, sorted by match date then time in the match.

    matches: list of (meta, goals), where meta is the PFF metadata object and
    goals is that match's find_goals output.
    """
    entries = []
    for meta, goals in matches:
        teams = {"home": meta["homeTeam"], "away": meta["awayTeam"]}
        score = {"home": 0, "away": 0}
        for g in sorted(goals, key=lambda g: g["eventTime"]):
            score[g["forSide"]] += 1
            team, opponent = teams[g["side"]], teams[OTHER_SIDE[g["side"]]]
            entries.append({
                "gameId": g["gameId"],
                "gameEventId": g["gameEventId"],
                "clip": clip_name(g),
                "date": meta["date"],
                "stage": stage_label(meta.get("week")),
                "scorer": g["scorer"],
                "scorerId": g["scorerId"],
                "team": team["name"],
                "teamShort": team["shortName"],
                "opponent": opponent["name"],
                "opponentShort": opponent["shortName"],
                "period": g["period"],
                "clock": g["clock"],
                "minute": minute_label(g["period"], g["gameClock"]),
                "score": {
                    "home": score["home"],
                    "away": score["away"],
                    "homeShort": teams["home"]["shortName"],
                    "awayShort": teams["away"]["shortName"],
                },
                "ownGoal": g["ownGoal"],
                "_order": (g["period"], g["gameClock"], g["eventTime"]),
            })
    entries.sort(key=lambda e: (e["date"], e["gameId"], e["_order"]))
    for e in entries:
        del e["_order"]
    return entries

