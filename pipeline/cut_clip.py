"""Cut a ~21.5 s tracking clip around one goal and write it as compact JSON.

Usage: python pipeline/cut_clip.py GAME_ID GAME_EVENT_ID OUT_PATH
"""

import bz2
import json
import sys
from collections import deque
from pathlib import Path

RAW = Path("data/raw")
BEFORE_S = 15.0
AFTER_S = 6.5  # goalT is the shot; the ball crosses the line ~1.3 s later
MAX_GAP_FRAMES = 15  # fill ball gaps up to ~0.5 s; longer gaps stay null
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0


def load_json(path):
    with open(path) as f:
        return json.load(f)


def read_window(tracking_path, game_event_id, before_s=BEFORE_S, after_s=AFTER_S):
    """Return (frames, goal_index) for the raw frames within the window around the event."""
    buffer = deque()
    goal_time = None
    frames = None
    with bz2.open(tracking_path, "rt") as f:
        for line in f:
            frame = json.loads(line)
            if frame.get("videoTimeMs") is None:
                continue
            t = frame["videoTimeMs"] / 1000
            if goal_time is None:
                buffer.append(frame)
                while buffer and buffer[0]["videoTimeMs"] / 1000 < t - before_s:
                    buffer.popleft()
                if frame.get("game_event_id") == game_event_id:
                    goal_time = t
                    frames = list(buffer)
                    goal_index = len(frames) - 1
            elif t <= goal_time + after_s:
                frames.append(frame)
            else:
                break
    if goal_time is None:
        raise ValueError(f"game_event_id {game_event_id} not found in tracking")
    return frames, goal_index


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


def ball_position(frame):
    ball = frame.get("ballsSmoothed")
    if not ball or ball.get("x") is None or ball.get("y") is None:
        return None
    return ball["x"], ball["y"], ball.get("z") or 0.0


def build_clip(game_id, game_event_id):
    meta = load_json(RAW / "metadata" / f"{game_id}.json")[0]
    roster = load_json(RAW / "rosters" / f"{game_id}.json")
    events = load_json(RAW / "events" / f"{game_id}.json")
    event = next(e for e in events if e["gameEventId"] == game_event_id)
    pitch = meta["stadium"]["pitches"][0]
    length, width = pitch["length"], pitch["width"]

    frames, goal_index = read_window(
        RAW / "tracking" / f"{game_id}.jsonl.bz2", game_event_id
    )

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

    raw_ball = []
    for frame in frames:
        pos = ball_position(frame)
        if pos is not None:
            x, y = to_standard_pitch(pos[0], pos[1], length, width)
            pos = (x, y, clamp_z(pos[2]))
        raw_ball.append(pos)
    missing_ball = sum(p is None for p in raw_ball)
    ball = fill_gaps(raw_ball)

    t0 = frames[0]["videoTimeMs"]
    out_frames = []
    for frame, b, ps in zip(frames, ball, player_frames):
        out_frames.append({
            "t": round((frame["videoTimeMs"] - t0) / 1000, 2),
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
        }

    pe = event["possessionEvents"]
    clip = {
        "gameId": game_id,
        "gameEventId": game_event_id,
        "scorer": pe["shooterPlayerName"],
        "clock": pe["formattedGameClock"],
        "period": event["gameEvents"]["period"],
        "fps": meta["fps"],
        "goalFrame": goal_index,
        "goalT": out_frames[goal_index]["t"],
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
    }
    return clip, stats


def main():
    game_id, game_event_id, out_path = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    clip, stats = build_clip(game_id, game_event_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(clip, f, separators=(",", ":"), ensure_ascii=False)
    print(f"frames {stats['first_frame']}..{stats['last_frame']}: {stats['frames']}")
    print(f"ball missing: {stats['missing_ball']} frames, after gap fill: {stats['still_missing_ball']}")
    print(f"size: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
