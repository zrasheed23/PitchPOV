"""Cut a ~21.5 s tracking clip around one goal and write it as compact JSON.

Usage: python pipeline/cut_clip.py GAME_ID GAME_EVENT_ID OUT_PATH
To cut every goal in the tournament, use build_all.py.
"""

import bz2
import json
import math
import sys
from collections import deque
from pathlib import Path

from goal_mouth import correct_goal_mouth
from goals import find_goals

RAW = Path("data/raw")
BEFORE_S = 15.0
AFTER_S = 6.5  # goalT is the shot; the ball crosses the line ~1.3 s later
MAX_GAP_FRAMES = 15  # fill ball gaps up to ~0.5 s; longer gaps stay null
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
BALL_SOURCES = ("raw", "smoothed")
RAW_BALL_MAX_M = 8.0  # use raw unless its ball is farther than this from every scoring-team player at the shot


def load_json(path):
    with open(path) as f:
        return json.load(f)


def read_windows(tracking_path, game_event_ids, before_s=BEFORE_S, after_s=AFTER_S):
    """Cut the window around every event in one pass over the tracking file.

    Returns {game_event_id: (frames, goal_index)}; ids never seen are missing.
    """
    wanted = set(game_event_ids)
    buffer = deque()
    open_windows = {}  # id -> (goal_time, frames, goal_index)
    done = {}
    with bz2.open(tracking_path, "rt") as f:
        for line in f:
            frame = json.loads(line)
            if frame.get("videoTimeMs") is None:
                continue
            t = frame["videoTimeMs"] / 1000
            buffer.append(frame)
            while buffer[0]["videoTimeMs"] / 1000 < t - before_s:
                buffer.popleft()
            for eid, (goal_time, frames, goal_index) in list(open_windows.items()):
                if t <= goal_time + after_s:
                    frames.append(frame)
                else:
                    done[eid] = (frames, goal_index)
                    del open_windows[eid]
            eid = frame.get("game_event_id")
            if eid in wanted and eid not in open_windows and eid not in done:
                open_windows[eid] = (t, list(buffer), len(buffer) - 1)
            if not open_windows and len(done) == len(wanted):
                break
    for eid, (_, frames, goal_index) in open_windows.items():
        done[eid] = (frames, goal_index)
    return done


def read_window(tracking_path, game_event_id, before_s=BEFORE_S, after_s=AFTER_S):
    """Return (frames, goal_index) for the raw frames within the window around the event."""
    windows = read_windows(tracking_path, [game_event_id], before_s, after_s)
    if game_event_id not in windows:
        raise ValueError(f"game_event_id {game_event_id} not found in tracking")
    return windows[game_event_id]


def clamp_z(z):
    return max(z, 0.0)


def fill_gaps(values, max_gap=MAX_GAP_FRAMES):
    """Linearly interpolate runs of None (tuples of floats) no longer than max_gap.

    Gaps at the start/end of the list, or longer than max_gap, are left as None.
    """
    out = list(values)
    i = 0
    while i < len(out):
        if out[i] is not None:
            i += 1
            continue
        start = i
        while i < len(out) and out[i] is None:
            i += 1
        gap = i - start
        if start == 0 or i == len(out) or gap > max_gap:
            continue
        a, b = out[start - 1], out[i]
        for k in range(gap):
            w = (k + 1) / (gap + 1)
            out[start + k] = tuple(av + (bv - av) * w for av, bv in zip(a, b))
    return out


def to_standard_pitch(x, y, length, width):
    """Scale centered coordinates from a length × width pitch to 105 × 68."""
    return x * PITCH_LENGTH / length, y * PITCH_WIDTH / width


def ball_position(frame, source="smoothed"):
    """(x, y, z) from ballsSmoothed or, for source "raw", the raw balls list."""
    if source == "smoothed":
        ball = frame.get("ballsSmoothed")
    else:
        ball = (frame.get("balls") or [None])[0]
    if not ball or ball.get("x") is None or ball.get("y") is None:
        return None
    return ball["x"], ball["y"], ball.get("z") or 0.0


def ball_path(frames, source, length, width):
    """The ball per frame on the standard pitch with z clamped; None where missing."""
    path = []
    for frame in frames:
        pos = ball_position(frame, source)
        if pos is not None:
            x, y = to_standard_pitch(pos[0], pos[1], length, width)
            pos = (x, y, clamp_z(pos[2]))
        path.append(pos)
    return path


def raw_ball_distance(raw_path, team_positions, goal_index):
    """Distance from the gap-filled raw ball at goalFrame to the nearest of
    team_positions [(x, y), ...], or None if there's no raw ball there."""
    ball = fill_gaps(raw_path)[goal_index]
    if ball is None or not team_positions:
        return None
    return min(math.dist(ball[:2], xy) for xy in team_positions)


def choose_ball_source(raw_distance):
    """Raw, unless its ball is missing at the shot or too far from the scoring team."""
    if raw_distance is None or raw_distance > RAW_BALL_MAX_M:
        return "smoothed"
    return "raw"


def load_match(game_id):
    meta = load_json(RAW / "metadata" / f"{game_id}.json")[0]
    roster = load_json(RAW / "rosters" / f"{game_id}.json")
    events = load_json(RAW / "events" / f"{game_id}.json")
    return meta, roster, events


def build_clip(meta, roster, goal, frames, goal_index):
    """Turn one goal's raw tracking window into the clip dict. goal is a find_goals entry."""
    pitch = meta["stadium"]["pitches"][0]
    length, width = pitch["length"], pitch["width"]
    by_shirt = {(r["team"]["id"], r["shirtNumber"]): r for r in roster}
    sides = {"home": meta["homeTeam"], "away": meta["awayTeam"]}

    players = {}
    player_frames = []
    for frame in frames:
        positions = {}
        for side, team in sides.items():
            for p in frame.get(f"{side}PlayersSmoothed") or []:
                r = by_shirt.get((team["id"], p["jerseyNum"]))
                if r is None:
                    raise ValueError(f"no roster entry for {team['name']} #{p['jerseyNum']}")
                pid = r["player"]["id"]
                players.setdefault(pid, {
                    "id": pid,
                    "name": r["player"]["nickname"],
                    "number": int(r["shirtNumber"]),
                    "team": side,
                    "position": r["positionGroupType"],
                })
                positions[pid] = to_standard_pitch(p["x"], p["y"], length, width)
        player_frames.append(positions)

    times = [(frame["videoTimeMs"] - frames[0]["videoTimeMs"]) / 1000 for frame in frames]
    # The scorer's team: for an own goal that's the conceding player on the ball.
    team = [xy for pid, xy in player_frames[goal_index].items() if players[pid]["team"] == goal["side"]]
    paths = {source: ball_path(frames, source, length, width) for source in BALL_SOURCES}
    raw_distance = raw_ball_distance(paths["raw"], team, goal_index)
    ball_source = choose_ball_source(raw_distance)
    ball, correction = correct_goal_mouth(fill_gaps(paths[ball_source]), times, goal_index)
    missing_ball = sum(p is None for p in paths[ball_source])

    out_frames = []
    for t, b, ps in zip(times, ball, player_frames):
        out_frames.append({
            "t": round(t, 2),
            "b": [round(v, 2) for v in b] if b is not None else None,
            "p": {pid: [round(x, 2), round(y, 2)] for pid, (x, y) in ps.items()},
        })

    def team_meta(side):
        kit = meta[f"{side}TeamKit"]
        return {
            "id": sides[side]["id"],
            "name": sides[side]["name"],
            "shortName": sides[side]["shortName"],
            "color": kit["primaryColor"],
            "textColor": kit["primaryTextColor"],
            "secondaryColor": kit["secondaryColor"],
        }

    clip = {
        "gameId": str(meta["id"]),
        "gameEventId": goal["gameEventId"],
        "scorer": goal["scorer"],
        "scorerId": goal["scorerId"],
        "scorerTeam": goal["side"],
        "ownGoal": goal["ownGoal"],
        "clock": goal["clock"],
        "period": goal["period"],
        "fps": meta["fps"],
        "goalFrame": goal_index,
        "goalT": out_frames[goal_index]["t"],
        "ballSource": ball_source,
        "ballCorrected": correction["corrected"],
        "needsReview": correction["needs_review"],
        "teams": {"home": team_meta("home"), "away": team_meta("away")},
        "players": list(players.values()),
        "frames": out_frames,
    }
    stats = {
        "frames": len(out_frames),
        "missing_ball": missing_ball,
        "still_missing_ball": sum(b is None for b in ball),
        "first_frame": frames[0]["frameNum"],
        "last_frame": frames[-1]["frameNum"],
        "correction": correction,
        "ball_source": ball_source,
        "raw_distance": raw_distance,
        "coverage": {src: sum(p is not None for p in path) / len(path) for src, path in paths.items()},
    }
    return clip, stats


def write_clip(clip, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(clip, f, separators=(",", ":"), ensure_ascii=False)


def main():
    game_id, game_event_id, out_path = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    meta, roster, events = load_match(game_id)
    goals, _ = find_goals(events)
    goal = next((g for g in goals if g["gameEventId"] == game_event_id), None)
    if goal is None:
        sys.exit(f"{game_event_id} is not a goal in game {game_id}")
    frames, goal_index = read_window(RAW / "tracking" / f"{game_id}.jsonl.bz2", game_event_id)
    clip, stats = build_clip(meta, roster, goal, frames, goal_index)
    write_clip(clip, out_path)
    c = stats["correction"]
    print(f"frames {stats['first_frame']}..{stats['last_frame']}: {stats['frames']}")
    print(f"ball missing: {stats['missing_ball']} frames, after gap fill and correction: {stats['still_missing_ball']}")
    d = stats["raw_distance"]
    print(f"ball source: {stats['ball_source']} (raw ball "
          f"{'missing' if d is None else f'{d:.1f} m'} from the nearest scoring-team player at the shot)")
    print(f"goal-mouth correction: {c['reason'] or 'none'}, shift {c['shift']:.2f} m"
          f"{', NEEDS REVIEW' if c['needs_review'] else ''}")
    print(f"size: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
