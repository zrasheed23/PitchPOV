import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from goals import build_index, find_goals, minute_label, stage_label

HOME_PLAYER = (10, "Home Striker")
AWAY_PLAYER = (20, "Away Defender")


def ev(eid, t, kind="OTB", side="home", player=HOME_PLAYER, shot=None, out=None, setpiece="O", period=1):
    """A minimal PFF event with just the fields goal detection reads."""
    return {
        "gameId": 1,
        "gameEventId": eid,
        "eventTime": t,
        "gameEvents": {
            "gameEventType": kind,
            "homeTeam": side == "home",
            "period": period,
            "playerId": player[0] if kind == "OTB" else None,
            "playerName": player[1] if kind == "OTB" else None,
            "setpieceType": setpiece if kind == "OTB" else None,
            "outType": out,
            "startGameClock": int(t),
            "startFormattedGameClock": f"{int(t) // 60:02d}:{int(t) % 60:02d}",
        },
        "possessionEvents": {
            "gameClock": int(t),
            "formattedGameClock": f"{int(t) // 60:02d}:{int(t) % 60:02d}",
            "shotOutcomeType": shot,
            "shooterPlayerId": player[0] if shot else None,
            "shooterPlayerName": player[1] if shot else None,
        },
    }


def goal_shot(eid, t, side="home", player=HOME_PLAYER, **kw):
    return ev(eid, t, side=side, player=player, shot="G", **kw)


def marker(eid, t, for_side):
    return ev(eid, t, kind="OUT", out="H" if for_side == "home" else "A")


def kickoff(eid, t, side):
    return ev(eid, t, side=side, setpiece="K")


def end(eid, t, period):
    return ev(eid, t, kind="END", period=period)


def test_goal_shot_is_found():
    events = [goal_shot(1, 100), marker(2, 101, "home"), kickoff(3, 150, "away"), end(4, 3000, 2)]
    goals, problems, _ = find_goals(events)
    assert [g["gameEventId"] for g in goals] == [1]
    assert goals[0]["scorer"] == "Home Striker"
    assert goals[0]["ownGoal"] is False
    assert problems == []


def test_shootout_kicks_are_excluded_but_extra_time_and_penalties_kept():
    events = [
        goal_shot(1, 100, period=1, setpiece="P"),  # in-game penalty
        marker(2, 101, "home"),
        kickoff(3, 150, "away"),
        goal_shot(4, 7000, period=4),  # extra-time goal, labelled period 4
        marker(5, 7001, "home"),
        kickoff(6, 7050, "away"),
        end(7, 7500, 4),
        # Shootout: also period 4, but after the last END.
        goal_shot(8, 7800, period=4, setpiece="P"),
        marker(9, 7801, "home"),
        goal_shot(10, 7900, side="away", player=AWAY_PLAYER, period=4, setpiece="P"),
        marker(11, 7901, "away"),
    ]
    goals, _, _ = find_goals(events)
    assert [g["gameEventId"] for g in goals] == [1, 4]


def test_own_goal_is_credited_to_the_defender():
    events = [
        ev(1, 200, side="home", player=HOME_PLAYER),  # home player crosses...
        ev(2, 201, side="away", player=AWAY_PLAYER, setpiece="O"),  # ...away defender turns it in
        marker(3, 202, "home"),
        kickoff(4, 250, "away"),
        end(5, 3000, 2),
    ]
    goals, problems, _ = find_goals(events)
    assert len(goals) == 1
    g = goals[0]
    assert g["gameEventId"] == 2
    assert g["scorer"] == "Away Defender"
    assert g["ownGoal"] is True
    assert g["side"] == "away"
    assert g["forSide"] == "home"
    assert problems == []


def test_own_goal_logged_as_a_goal_shot():
    events = [
        goal_shot(1, 200, side="away", player=AWAY_PLAYER),
        marker(2, 201, "home"),
        kickoff(3, 250, "away"),
        end(4, 3000, 2),
    ]
    goals, problems, _ = find_goals(events)
    assert len(goals) == 1
    assert goals[0]["ownGoal"] is True and goals[0]["forSide"] == "home"
    assert problems == []


def test_marker_without_kickoff_is_not_a_goal():
    # e.g. a disallowed goal: the restart is a free kick, not a kickoff.
    events = [
        ev(1, 200, side="away", player=AWAY_PLAYER),
        marker(2, 201, "home"),
        ev(3, 260, side="away", player=AWAY_PLAYER, setpiece="F"),
        end(4, 3000, 2),
    ]
    goals, problems, _ = find_goals(events)
    assert goals == []
    assert len(problems) == 1


def test_goal_shot_without_marker_is_disallowed():
    # VAR/offside: PFF logs the shot as a goal but writes no OUT goal marker.
    events = [goal_shot(1, 100), ev(2, 150, side="away", setpiece="F"),
              goal_shot(3, 400), marker(4, 401, "home"), kickoff(5, 450, "away"), end(6, 3000, 2)]
    goals, problems, disallowed = find_goals(events)
    assert [g["gameEventId"] for g in goals] == [3]
    assert [g["gameEventId"] for g in disallowed] == [1]
    assert disallowed[0]["scorer"] == "Home Striker"
    assert problems == []


def test_cross_that_goes_in_is_credited_to_the_player_on_the_ball():
    # Like Bruno Fernandes v Uruguay: shotOutcomeType G on a cross, no shooter.
    shot = goal_shot(1, 100)
    shot["possessionEvents"]["shooterPlayerId"] = None
    shot["possessionEvents"]["shooterPlayerName"] = None
    goals, _, _ = find_goals([shot, marker(2, 101, "home"), kickoff(3, 150, "away"), end(4, 3000, 2)])
    assert goals[0]["scorer"] == "Home Striker"
    assert goals[0]["scorerId"] == str(HOME_PLAYER[0])


def test_minute_label():
    assert minute_label(1, 35 * 60 + 21) == "36'"
    assert minute_label(1, 45 * 60 + 30) == "45+1'"
    assert minute_label(2, 79 * 60 + 24) == "80'"
    assert minute_label(4, 107 * 60 + 57) == "108'"
    assert minute_label(4, 120 * 60 + 5) == "120+1'"


def test_stage_label():
    assert stage_label(2) == "Group stage · Matchday 2"
    assert stage_label(8) == "Final"


def meta(game_id, date, week=1):
    return {
        "id": game_id,
        "date": date,
        "week": week,
        "homeTeam": {"id": "1", "name": "Homeland", "shortName": "HOM"},
        "awayTeam": {"id": "2", "name": "Awayland", "shortName": "AWY"},
    }


def goal(game_id, eid, t, side, own=False, period=1):
    for_side = {"home": "away", "away": "home"}[side] if own else side
    return {
        "gameId": game_id, "gameEventId": eid, "eventTime": t, "period": period,
        "gameClock": t, "clock": f"{t // 60:02d}:{t % 60:02d}",
        "scorerId": "10" if side == "home" else "20",
        "scorer": "Home Striker" if side == "home" else "Away Defender",
        "side": side, "ownGoal": own, "forSide": for_side,
    }


def test_index_sorted_by_date_then_match_time_with_running_score():
    later = meta("200", "2022-11-25T13:00:00")
    earlier = meta("100", "2022-11-21T16:00:00")
    index = build_index([
        (later, [goal("200", 5, 600, "home")]),
        (earlier, [
            goal("100", 3, 3000, "home", period=2),
            goal("100", 1, 300, "away"),
            goal("100", 2, 1200, "away", own=True),  # counts for home
        ]),
    ])
    assert [e["gameEventId"] for e in index] == [1, 2, 3, 5]

    first, own, third, other = index
    assert first["clip"] == "100_1.json"
    assert (first["team"], first["opponent"]) == ("Awayland", "Homeland")
    assert (first["score"]["home"], first["score"]["away"]) == (0, 1)
    assert own["ownGoal"] is True
    assert own["team"] == "Awayland"  # listed under the defender who scored it
    assert (own["score"]["home"], own["score"]["away"]) == (1, 1)
    assert (third["score"]["home"], third["score"]["away"]) == (2, 1)
    assert third["minute"] == "51'"
    assert (other["score"]["home"], other["score"]["away"]) == (1, 0)
    assert first["stage"] == "Group stage · Matchday 1"
