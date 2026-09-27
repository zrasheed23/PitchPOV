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
from contacts import find_contacts
from estimate_gaps import estimate_gaps
from goals import clip_name, find_goals

RAW = Path("data/raw")
OVERRIDES = Path(__file__).resolve().parent / "overrides.json"
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


def read_windows(tracking_path, game_event_ids, before_s=BEFORE_S, after_s=AFTER_S, periods=None):
    """Cut the window around every event in one pass over the tracking file.

    Returns {game_event_id: (frames, goal_index)}; ids never seen are missing.
    If given, the set `periods` collects every period seen in the file (only
    complete when some id is missing, since the pass then reads to the end).
    """
    wanted = set(game_event_ids)
    buffer = deque()
    open_windows = {}  # id -> (goal_time, frames, goal_index)
    done = {}
    last_ms = None
    with bz2.open(tracking_path, "rt") as f:
        for line in f:
            frame = json.loads(line)
            if periods is not None:
                periods.add(frame.get("period"))
            if frame.get("videoTimeMs") is None:
                continue
            t = frame["videoTimeMs"] / 1000
            if last_ms is not None and frame["videoTimeMs"] <= last_ms:
                # PFF repeats a frame's video time around some events (up to ~15 frames
                # with the same timestamp and the ball wobbling). Keep only the first;
                # if the goal is tagged on a repeat, the window opens at the kept frame.
                eid = frame.get("game_event_id")
                if eid in wanted and eid not in open_windows and eid not in done:
                    open_windows[eid] = (buffer[-1]["videoTimeMs"] / 1000, list(buffer), len(buffer) - 1)
                continue
            last_ms = frame["videoTimeMs"]
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
    return drop_repeats(path)


def drop_repeats(path, max_run=2):
    """PFF's ball often updates only every other frame, repeating the last position in between.
    Played back as-is the ball moves, stops, moves, stops (a 15 Hz stutter). Blank out the
    repeats in short runs so fill_gaps interpolates them; long runs (a ball sitting still,
    e.g. on the penalty spot) are left alone."""
    out = list(path)
    i = 0
    while i < len(out):
        j = i + 1
        while j < len(out) and out[j] is not None and out[i] is not None and out[j] == out[i]:
            j += 1
        if out[i] is not None and 1 < j - i <= max_run and j < len(out) and out[j] is not None:
            for k in range(i + 1, j):
                out[k] = None
        i = j
    return out


OUT_OF_PLAY_M = 3.0  # this far past a touchline or goal line, the tracking has lost the ball


def drop_out_of_play(path, end):
    """Blank ball positions well outside the pitch before the shot (frame `end`).
    Both feeds sometimes latch onto something else for a second or two: in
    Saudi Arabia v Argentina (52:43) the raw ball flies 5 m past the goal line
    and 13 m up, then loops back to the shooter. A real ball that far out would
    mean a restart, so treat those frames as missing and let the other feed or
    the gap fill cover them."""
    out = list(path)
    for i in range(min(end, len(out))):
        b = out[i]
        if b is not None and (abs(b[0]) > PITCH_LENGTH / 2 + OUT_OF_PLAY_M or abs(b[1]) > PITCH_WIDTH / 2 + OUT_OF_PLAY_M):
            out[i] = None
    return out


BORROW_BLEND = 9  # frames over which a borrowed stretch eases onto the chosen feed
BORROW_MAX_OFFSET_M = 4.0


def borrow_gaps(primary, secondary, end):
    """Fill long gaps (longer than fill_gaps bridges) in the chosen ball feed with
    the other feed, for frames before `end` (the shot). PFF's two feeds often
    lose the ball at different times, so this keeps the ball on screen during the
    build-up instead of vanishing for seconds. Where the feeds disagree, the
    borrowed stretch is shifted to meet the chosen feed at both ends, easing in
    over BORROW_BLEND frames, so there's no jump at the seams.
    Returns (path, number of frames borrowed)."""
    out = list(primary)
    borrowed = 0
    i = 0
    while i < end:
        if out[i] is not None:
            i += 1
            continue
        j = i
        while j < len(out) and out[j] is None:
            j += 1
        if j - i > MAX_GAP_FRAMES:
            def offset(k):
                if 0 <= k < len(primary) and primary[k] is not None and secondary[k] is not None:
                    return tuple(p - s for p, s in zip(primary[k], secondary[k]))
                return None
            left, right = offset(i - 1), offset(j)
            if any(o is not None and math.hypot(o[0], o[1]) > BORROW_MAX_OFFSET_M for o in (left, right)):
                i = j  # the feeds disagree by metres here: easing between them would look like a fast slide
                continue
            for k in range(i, min(j, end)):
                s = secondary[k]
                if s is None:
                    continue
                wl = max(0.0, 1 - (k - i + 1) / BORROW_BLEND) if left else 0.0
                wr = max(0.0, 1 - (j - k) / BORROW_BLEND) if right else 0.0
                v = list(s)
                for d in range(3):
                    v[d] += (left[d] * wl if left else 0.0) + (right[d] * wr if right else 0.0)
                v[2] = max(v[2], 0.0)
                out[k] = tuple(v)
                borrowed += 1
        i = j
    return out, borrowed


JUMP_MPS = 40.0  # faster than this between two frames is a tracking jump, not a kick
GLIDE_MPS = 25.0  # replace a jump with a glide at about this speed
MAX_GLIDE_HALF_S = 1.5


def smooth_jumps(ball, times):
    """Replace teleports with a glide. When the tracking re-finds the ball it can
    jump 10-40 m in one or two frames (hundreds of m/s on screen). Spread each
    jump over enough frames to cover it at GLIDE_MPS, centred on the jump, by
    interpolating between the nearest real positions either side.
    Returns (path, number of jumps smoothed)."""
    out = list(ball)
    present = [i for i, b in enumerate(out) if b is not None]
    fixed = 0
    k = 1
    while k < len(present):
        p, i = present[k - 1], present[k]
        dt = times[i] - times[p]
        d = math.dist(out[p], out[i])
        if dt <= 0 or d / dt <= JUMP_MPS:
            k += 1
            continue
        # Widen the window until the glide across it is no faster than GLIDE_MPS
        # (jumps often come in pairs, so the far side can be further than d).
        half = d / GLIDE_MPS / 2
        while True:
            lo = next((present[m] for m in range(k - 1, -1, -1) if times[present[m]] <= times[p] - half), present[0])
            hi = next((present[m] for m in range(k, len(present)) if times[present[m]] >= times[i] + half), present[-1])
            span = times[hi] - times[lo]
            wide_enough = span > 0 and math.dist(out[lo], out[hi]) / span <= GLIDE_MPS * 1.2
            if wide_enough or (lo == present[0] and hi == present[-1]) or half > MAX_GLIDE_HALF_S:
                break
            half += 0.1
        a, b = out[lo], out[hi]
        for m in range(lo + 1, hi):
            w = (times[m] - times[lo]) / span if span > 0 else 1.0
            out[m] = tuple(av + (bv - av) * w for av, bv in zip(a, b))
        fixed += 1
        present = [m for m, b in enumerate(out) if b is not None]
        k = next((n for n, m in enumerate(present) if m > hi), len(present))
    return out, fixed


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


def load_overrides(path=OVERRIDES):
    """Hand fixes: {clip file name: {"ballSource": "smoothed" | "raw", "note": ...}},
    or {"exclude": true, "note": ...} to leave a goal out of the clips and index."""
    if not path.exists():
        return {}
    overrides = load_json(path)
    for name, o in overrides.items():
        if not o.get("note"):
            raise ValueError(f"{path}: {name} needs a note")
        if o.get("exclude") is not True and o.get("ballSource") not in BALL_SOURCES:
            raise ValueError(f"{path}: {name} needs a ballSource in {BALL_SOURCES} or \"exclude\": true")
    return overrides


def pick_ball_source(raw_distance, override=None):
    """(source, automatic choice): an override's ballSource wins over choose_ball_source."""
    auto = choose_ball_source(raw_distance)
    return (override or {}).get("ballSource", auto), auto


def load_match(game_id):
    meta = load_json(RAW / "metadata" / f"{game_id}.json")[0]
    roster = load_json(RAW / "rosters" / f"{game_id}.json")
    events = load_json(RAW / "events" / f"{game_id}.json")
    return meta, roster, events


def build_clip(meta, roster, goal, frames, goal_index, override=None, events=None):
    """Turn one goal's raw tracking window into the clip dict. goal is a find_goals
    entry; override is its overrides.json entry, if any; events (the match's event
    list) gives every touch of the ball for the viewer."""
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
    paths = {source: drop_out_of_play(ball_path(frames, source, length, width), goal_index) for source in BALL_SOURCES}
    raw_distance = raw_ball_distance(paths["raw"], team, goal_index)
    ball_source, auto_source = pick_ball_source(raw_distance, override)
    other = "raw" if ball_source == "smoothed" else "smoothed"
    chosen, borrowed = borrow_gaps(paths[ball_source], paths[other], goal_index)
    # Where the ball is still missing before the shot, estimate it from where it
    # was last seen, where it reappears and who is near it (see estimate_gaps.py).
    chosen, estimated = estimate_gaps(fill_gaps(chosen), times, player_frames, goal_index)
    ball, correction = correct_goal_mouth(chosen, times, goal_index)
    ball, jumps = smooth_jumps(ball, times)
    missing_ball = sum(p is None for p in paths[ball_source])

    out_frames = []
    for t, b, ps in zip(times, ball, player_frames):
        out_frames.append({
            "t": round(t, 3),  # 2 decimals made frame gaps alternate 0.03/0.04 s: a visible 10 Hz judder
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
        "ballEstimated": estimated,  # [first, last] frame ranges where the ball position is estimated
        "contacts": find_contacts(events or [], [f["videoTimeMs"] for f in frames], set(players)),
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
        "auto_source": auto_source,
        "override": override,
        "coverage": {src: sum(p is not None for p in path) / len(path) for src, path in paths.items()},
        "max_ball_speed": max_ball_speed(ball, times),
        "borrowed": borrowed,
        "jumps_smoothed": jumps,
        "estimated_frames": sum(b - a + 1 for a, b in estimated),
    }
    return clip, stats


def max_ball_speed(ball, times, window=3):
    """Fastest the ball moves (m/s) over any `window` frames. Real shots top out
    around 35 m/s; much more means the tracking skipped or compressed a pass."""
    best = 0.0
    for i in range(window, len(ball)):
        a, b = ball[i - window], ball[i]
        dt = times[i] - times[i - window]
        if a is not None and b is not None and dt > 0:
            best = max(best, math.dist(a, b) / dt)
    return best


def write_clip(clip, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(clip, f, separators=(",", ":"), ensure_ascii=False)


def main():
    game_id, game_event_id, out_path = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])
    meta, roster, events = load_match(game_id)
    goals, _, _ = find_goals(events)
    goal = next((g for g in goals if g["gameEventId"] == game_event_id), None)
    if goal is None:
        sys.exit(f"{game_event_id} is not a goal in game {game_id}")
    frames, goal_index = read_window(RAW / "tracking" / f"{game_id}.jsonl.bz2", game_event_id)
    override = load_overrides().get(clip_name(goal))
    clip, stats = build_clip(meta, roster, goal, frames, goal_index, override, events)
    write_clip(clip, out_path)
    c = stats["correction"]
    print(f"frames {stats['first_frame']}..{stats['last_frame']}: {stats['frames']}")
    print(f"ball missing: {stats['missing_ball']} frames, after gap fill and correction: {stats['still_missing_ball']}")
    d = stats["raw_distance"]
    print(f"ball source: {stats['ball_source']} (raw ball "
          f"{'missing' if d is None else f'{d:.1f} m'} from the nearest scoring-team player at the shot)")
    if stats["override"]:
        print(f"  overridden (automatic choice: {stats['auto_source']}): {stats['override']['note']}")
    print(f"goal-mouth correction: {c['reason'] or 'none'}, shift {c['shift']:.2f} m"
          f"{', NEEDS REVIEW' if c['needs_review'] else ''}")
    print(f"size: {out_path.stat().st_size / 1024:.1f} KB")


if __name__ == "__main__":
    main()
