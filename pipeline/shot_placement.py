"""Where each goal crossed the line, from StatsBomb's open 2022 World Cup data.

PFF's tracking usually loses a hard shot, and its events don't say where in the
goal it went (only which third of the height). StatsBomb's free event data does:
every shot has an end_location (x, y, z) in the goal mouth. This script
downloads it, matches each StatsBomb goal to our clips (same match, scoring
team, clock within a few seconds; scorer name as a tie-break) and writes
pipeline/shot_placement.json: clip name -> {"y", "z"} in our pitch coordinates
at the goal line, which the goal-mouth correction aims the shot at.

StatsBomb coordinates: 120 x 80 yards, every team attacking toward x = 120,
y increasing to the attacker's right, posts at y = 36 and 44, z in yards.
Checked against the tracking: on the 79 goals where both put the ball more
than 1.5 m off centre, the sign below agrees on 60; the other 19 are goals the
tracking has going in on the wrong side.

Data: StatsBomb Open Data (https://github.com/statsbomb/open-data), used under
its licence with attribution.

Usage: python pipeline/shot_placement.py   (run from the repo root, needs clips/index.json)
"""

import json
import unicodedata
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"
COMPETITION, SEASON = 43, 106  # FIFA World Cup 2022
CACHE = Path("data/statsbomb")
OUT = Path(__file__).resolve().parent / "shot_placement.json"
YARD = 0.9144
POST_Y = 3.66 - 0.3  # keep the ball inside the posts
BAR_Z = 2.44 - 0.2
MAX_CLOCK_GAP_S = 5  # with the scorer's name matching, up to 2 minutes


def fetch(path):
    local = CACHE / path
    if not local.exists():
        local.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{BASE}/{path}") as r:
            local.write_bytes(r.read())
    return json.loads(local.read_text())


def statsbomb_goals():
    goals = []
    for m in fetch(f"matches/{COMPETITION}/{SEASON}.json"):
        home, away = m["home_team"]["home_team_name"], m["away_team"]["away_team_name"]
        for e in fetch(f"events/{m['match_id']}.json"):
            if e["period"] > 4:
                continue  # penalty shootout
            kind = e["type"]["name"]
            if kind == "Shot" and e["shot"]["outcome"]["name"] == "Goal":
                end = e["shot"].get("end_location")
            elif kind == "Own Goal Against":
                end = None
            else:
                continue
            goals.append({"home": home, "away": away, "team": e["team"]["name"], "player": e["player"]["name"],
                          "clock": e["minute"] * 60 + e["second"], "end": end})
    return goals


def ascii_lower(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()


def match(index, goals):
    """{clip name: StatsBomb goal} for every clip we can pair up."""
    out, used = {}, set()
    for g in index:
        m, s = (g["clock"].split(":"))
        t = int(m) * 60 + int(s)
        best = None
        for i, sb in enumerate(goals):
            if i in used or {sb["home"], sb["away"]} != {g["team"], g["opponent"]} or sb["team"] != g["team"]:
                continue
            gap = abs(sb["clock"] - t)
            named = any(w in ascii_lower(sb["player"]) for w in ascii_lower(g["scorer"]).split() if len(w) > 2)
            if gap <= MAX_CLOCK_GAP_S or (named and gap <= 120):
                if best is None or gap < best[0]:
                    best = (gap, i)
        if best:
            used.add(best[1])
            out[g["clip"]] = goals[best[1]]
    return out


def to_pitch(end, side):
    """StatsBomb end_location -> (y, z) in our pitch coordinates at the goal line.
    side: +1 if the goal is at x = +52.5. The attacker's right is -y attacking +x."""
    right = (end[1] - 40) * YARD
    y = max(-POST_Y, min(POST_Y, -side * right))
    z = max(0.0, min(BAR_Z, end[2] * YARD)) if len(end) > 2 else None
    return y, z


def attacking_side(clip):
    frames, gf = clip["frames"], clip["goalFrame"]
    for d in range(len(frames)):
        for k in (gf - d, gf + d):
            if 0 <= k < len(frames) and frames[k]["b"]:
                return 1 if frames[k]["b"][0] >= 0 else -1
    return 1


def main():
    index = json.loads(Path("clips/index.json").read_text())
    matched = match(index, statsbomb_goals())
    out = {}
    for name, sb in sorted(matched.items()):
        if not sb["end"]:
            continue  # own goals: StatsBomb has no end location
        clip = json.loads((Path("clips") / name).read_text())
        y, z = to_pitch(sb["end"], attacking_side(clip))
        out[name] = {"y": round(y, 2)} | ({"z": round(z, 2)} if z is not None else {})
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    print(f"{len(matched)} of {len(index)} clips matched to StatsBomb, {len(out)} with a placement -> {OUT.name}")
    missing = [g["clip"] for g in index if g["clip"] not in matched]
    if missing:
        print("not matched:", ", ".join(missing))


if __name__ == "__main__":
    main()
