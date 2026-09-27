"""Name the players in PFF's swapped extra-time periods from StatsBomb events.

In SWAPPED_PERIODS (cut_clip.py) each tracking list carries the other team's
positions under its own team's shirt numbers, and which number sits on which
player changes every few minutes, even without a substitution. label_pairing
(same number, else position group) gets the keepers right but most outfield
names wrong.

StatsBomb's free event data names the player on every event and says where he
was. Inside one clip's window (~21 s) the slots hardly change, so each
StatsBomb event there votes: the nearest tracked player of that team (within
VOTE_M) is that player. A slot takes the name it gets at least MIN_VOTES
votes for, with at least MIN_SHARE of its votes; the rest keep label_pairing.
Calibrated on Morocco v Spain's normal time (labels there are right): a slot
that passes this test has the right name 95% of the time.

StatsBomb times run from each period's start; PFF's from the start of the
video. The offset per period is the median gap between the same player's
touches in both feeds at about the same game clock.

Data: StatsBomb Open Data (data/statsbomb, cached by shot_placement.py; fetch
lineups with curl if missing).
"""

import json
import math
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

CACHE = Path("data/statsbomb")
COMPETITION, SEASON = 43, 106
VOTE_M = 5.0
MIN_VOTES = 2
MIN_SHARE = 0.6
CLOCK_MATCH_S = 5  # same player, game clocks this close: the same touch in both feeds
TOUCHES = {"PA", "SH", "CR", "CL", "RE", "TC", "IT"}


def _ts(stamp):
    h, m, s = stamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


@lru_cache(maxsize=None)
def _statsbomb_match(home, away):
    path = CACHE / "matches" / str(COMPETITION) / f"{SEASON}.json"
    if not path.exists():
        return None
    for m in json.loads(path.read_text()):
        if {m["home_team"]["home_team_name"], m["away_team"]["away_team_name"]} == {home, away}:
            return m["match_id"]
    return None


def statsbomb_events(meta, roster, events):
    """[(period, video time s, (side, shirt number), (x, y) in StatsBomb yards,
    the team attacking +x)] for every StatsBomb event with a player and a
    location, or None if the match isn't in the cache."""
    sides = {"home": meta["homeTeam"], "away": meta["awayTeam"]}
    sb_id = _statsbomb_match(sides["home"]["name"], sides["away"]["name"])
    ev_path, lu_path = CACHE / "events" / f"{sb_id}.json", CACHE / "lineups" / f"{sb_id}.json"
    if sb_id is None or not ev_path.exists() or not lu_path.exists():
        return None
    side_of = {t["name"]: s for s, t in sides.items()}
    number = {p["player_id"]: (side_of[t["team_name"]], str(p["jersey_number"]))
              for t in json.loads(lu_path.read_text()) if t["team_name"] in side_of for p in t["lineup"]}
    sb = [(e["period"], _ts(e["timestamp"]), e["minute"] * 60 + e["second"], number[e["player"]["id"]], e["location"])
          for e in json.loads(ev_path.read_text())
          if e["period"] <= 4 and "player" in e and "location" in e and e["player"]["id"] in number]
    shirt = {r["player"]["id"]: ("home" if r["team"]["id"] == sides["home"]["id"] else "away", str(r["shirtNumber"]))
             for r in roster}
    by_who = defaultdict(list)
    for p, t, clock, who, _ in sb:
        by_who[(p, who)].append((clock, t))
    gaps = defaultdict(list)
    for e in events:
        g = e.get("gameEvents") or {}
        if ((e.get("possessionEvents") or {}).get("possessionEventType") not in TOUCHES or e.get("eventTime") is None
                or g.get("startGameClock") is None or str(g.get("playerId")) not in shirt):
            continue
        p = g["period"]
        near = [e["eventTime"] - t for clock, t in by_who[(p, shirt[str(g["playerId"])])]
                if abs(clock - g["startGameClock"]) <= CLOCK_MATCH_S]
        if len(near) == 1:
            gaps[p].append(near[0])
    offset = {p: sorted(d)[len(d) // 2] for p, d in gaps.items() if d}
    return [(p, t + offset[p], who, loc) for p, t, _, who, loc in sb if p in offset]


def window_votes(sb, period, times, lists_at, length, width):
    """Clear votes {(team side, slot label): shirt number} for one clip window.

    times: video time (s) of each frame, all in `period`. lists_at(i) gives
    {team side: [(label, x, y)]} at frame i, the list carrying that team's
    positions. StatsBomb has every team attacking +x; which way that is on our
    pitch is whichever puts its events nearer the team's tracked players."""
    lo, hi = times[0], times[-1]
    evs = [(t, who, loc) for p, t, who, loc in sb if p == period and lo <= t <= hi]

    def frame(t):
        return min(range(len(times)), key=lambda i: abs(times[i] - t))

    def to_pitch(loc, flip):
        x, y = (loc[0] / 120 - 0.5) * length, -(loc[1] / 80 - 0.5) * width
        return (-x, -y) if flip else (x, y)

    counts = defaultdict(Counter)
    for side in ("home", "away"):
        mine = [(frame(t), who, loc) for t, who, loc in evs if who[0] == side]
        if not mine:
            continue

        def spread(flip):
            ds = sorted(min(math.dist((x, y), to_pitch(loc, flip)) for _, x, y in lists_at(i)[side])
                        for i, _, loc in mine)
            return ds[len(ds) // 2]

        flip = spread(True) < spread(False)
        for i, (_, num), loc in mine:
            at = to_pitch(loc, flip)
            label, x, y = min(lists_at(i)[side], key=lambda e: math.dist(e[1:], at))
            if math.dist((x, y), at) <= VOTE_M:
                counts[(side, label)][num] += 1
    clear, best = {}, {}
    for key, c in counts.items():
        (num, n), total = c.most_common(1)[0], sum(c.values())
        if n < MIN_VOTES or n / total < MIN_SHARE:
            continue
        rival = best.get((key[0], num))
        if rival is not None and rival[1] >= n:
            continue
        if rival is not None:
            del clear[rival[0]]
        clear[key] = num
        best[(key[0], num)] = (key, n)
    return clear
