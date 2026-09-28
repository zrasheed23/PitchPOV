"""Cut a ~21.5 s tracking clip around one goal and write it as compact JSON.

Usage: python pipeline/cut_clip.py GAME_ID GAME_EVENT_ID OUT_PATH
To cut every goal in the tournament, use build_all.py.
"""

import bisect
import bz2
import itertools
import json
import math
import sys
from collections import deque
from functools import lru_cache
from pathlib import Path

from goal_mouth import correct_goal_mouth, goal_side
from accuracy import check_clip
from ball_flight import anchor_frames, count_kinks, straighten_free_flight
from ball_physics import apply_physics
from ball_rules import enforce_touch_rule, find_kick, fly_shot, limit_player_speeds, meet_touches, shot_contact
from contacts import align_contacts, find_contacts
from dribble import rebuild_dribbles
from estimate_gaps import estimate_gaps
from goals import clip_name, find_goals
from keepers import defending_keeper, ease_to_freeze_frame, place_keepers, plan_dive
from net import across_the_line
from penalty import find_keeper, pin_ball, place_players
from restarts import apply_restarts, find_restarts, redraw_roll_out
from statsbomb import clip_events, match_events, merge_touches
from touch_rule import violations
from volleys import shot_pose
import relabel

RAW = Path("data/raw")
OVERRIDES = Path(__file__).resolve().parent / "overrides.json"
SHOT_PLACEMENT = Path(__file__).resolve().parent / "shot_placement.json"  # written by shot_placement.py
BEFORE_S = 15.0
AFTER_S = 6.5  # goalT is the shot; the ball crosses the line ~1.3 s later
MAX_GAP_FRAMES = 15  # fill ball gaps up to ~0.5 s; longer gaps stay null
PITCH_LENGTH = 105.0
PITCH_WIDTH = 68.0
BALL_SOURCES = ("raw", "smoothed")
RAW_BALL_MAX_M = 8.0  # use raw unless its ball is farther than this from every scoring-team player at the shot
# Periods where PFF's tracking puts each team's positions under the other
# team's labels, for the whole period (see player_lists): the keepers defend
# the wrong goals for the metadata's extra-time ends, and 79-90% of logged
# touches are nearer the other team's player with that shirt number (2-13% in
# the other periods, spread evenly).
SWAPPED_PERIODS = {"10517": {3, 4}, "10506": {3, 4}}  # the final, Japan v Croatia: all of extra time
# Where each positionGroupType plays (across, up the pitch), to pair up labels by role.
ROLE_SPOT = {"GK": (0, -10), "LCB": (-1, 1), "MCB": (0, 1), "RCB": (1, 1), "LB": (-2, 1.5), "RB": (2, 1.5),
             "LWB": (-2, 2), "RWB": (2, 2), "DM": (0, 2), "CM": (0, 2.5), "AM": (0, 3),
             "LW": (-2, 3.5), "RW": (2, 3.5), "CF": (0, 4)}


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


@lru_cache(maxsize=None)
def label_pairing(home_numbers, away_numbers, roles):
    """{home shirt number: away shirt number} pairing the two teams' players on
    the pitch: the same number where both teams have it, the rest by position
    (least total distance between their ROLE_SPOTs). roles: tuple of
    ((side, number), positionGroupType)."""
    role = dict(roles)
    pair = {n: n for n in home_numbers if n in away_numbers}
    left_h = [n for n in home_numbers if n not in pair]
    left_a = [n for n in away_numbers if n not in pair]

    def cost(h, a):
        return math.dist(ROLE_SPOT.get(role.get(("home", h)), (0, 2.5)), ROLE_SPOT.get(role.get(("away", a)), (0, 2.5)))

    if len(left_h) > len(left_a):
        left_h, left_a, flip = left_a, left_h, True
        cost_ = lambda x, y: cost(y, x)
    else:
        flip, cost_ = False, cost
    best = min(itertools.permutations(left_a, len(left_h)),
               key=lambda perm: sum(cost_(x, y) for x, y in zip(left_h, perm)), default=())
    for x, y in zip(left_h, best):
        pair[y if flip else x] = x if flip else y
    return pair


def player_lists(frame, game_id, roles=(), votes=None):
    """{"home": [...], "away": [...]}: the frame's players, undoing PFF's swap in
    SWAPPED_PERIODS. There each list's shirt numbers are its own team's players
    on the pitch (they change at that team's substitutions) but the positions
    are the other team's: the positions under home #10 are away #10's. Where
    the other team has no such number, labels pair up by position (GK with GK,
    LCB with LCB, CF with CF: see label_pairing), which is what the logged
    touches show. roles: ((side, number), positionGroupType) pairs.
    votes: {(team side, label in the list carrying that team): shirt number}
    from StatsBomb (relabel.window_votes); they win over the pairing."""
    home = frame.get("homePlayersSmoothed") or []
    away = frame.get("awayPlayersSmoothed") or []
    if frame.get("period") not in SWAPPED_PERIODS.get(str(game_id), ()):
        return {"home": home, "away": away}
    h_nums = tuple(sorted({p["jerseyNum"] for p in home}, key=str))
    a_nums = tuple(sorted({p["jerseyNum"] for p in away}, key=str))
    # to_home: label in the away list -> home number; to_away: label in the home list -> away number
    to_home, to_away = {}, {}
    for (side, label), num in (votes or {}).items():
        if side == "home" and label in a_nums and num in h_nums:
            to_home[label] = num
        elif side == "away" and label in h_nums and num in a_nums:
            to_away[label] = num
    if to_home or to_away:
        pair = label_pairing(tuple(n for n in h_nums if n not in to_home.values()),
                             tuple(n for n in a_nums if n not in to_home), roles)
        to_home |= {a: h for h, a in pair.items()}
        pair = label_pairing(tuple(n for n in h_nums if n not in to_away),
                             tuple(n for n in a_nums if n not in to_away.values()), roles)
        to_away |= pair
    else:
        to_away = label_pairing(h_nums, a_nums, roles)
        to_home = {a: h for h, a in to_away.items()}
    # Entries with no partner (a substitution frame where one list has 12) are dropped.
    return {"home": [{**p, "jerseyNum": to_home[p["jerseyNum"]]} for p in away if p["jerseyNum"] in to_home],
            "away": [{**p, "jerseyNum": to_away[p["jerseyNum"]]} for p in home if p["jerseyNum"] in to_away]}


def swapped_votes(meta, roster, events, frames):
    """StatsBomb votes (relabel.py) for a clip window in a swapped period, else None."""
    game_id = str(meta["id"])
    periods = {f.get("period") for f in frames} & SWAPPED_PERIODS.get(game_id, set())
    if len(periods) != 1 or not events:
        return None
    period = periods.pop()
    sb = relabel.statsbomb_events(meta, roster, events)
    if sb is None:
        return None
    pitch = meta["stadium"]["pitches"][0]
    idx = [i for i, f in enumerate(frames) if f.get("period") == period]

    def lists_at(n):
        f = frames[idx[n]]
        home, away = f.get("homePlayersSmoothed") or [], f.get("awayPlayersSmoothed") or []
        # swapped: the away list carries the home team's positions
        return {"home": [(p["jerseyNum"], p["x"], p["y"]) for p in away],
                "away": [(p["jerseyNum"], p["x"], p["y"]) for p in home]}

    return relabel.window_votes(sb, period, [frames[i]["videoTimeMs"] / 1000 for i in idx], lists_at,
                                pitch["length"], pitch["width"])


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
LOST_TAIL_FRAMES = 15  # a jump followed by at most this many frames and then no ball at all is dropped


def smooth_jumps(ball, times, end=None, start=0):
    """Replace teleports with a glide. When the tracking re-finds the ball it can
    jump 10-40 m in one or two frames (hundreds of m/s on screen). Spread each
    jump over enough frames to cover it at GLIDE_MPS, centred on the jump, by
    interpolating between the nearest real positions either side. A jump the
    feed makes just before losing the ball for good (after a goal-line
    clearance) has nothing to glide to: those last frames are dropped. Only
    frames from `start` to `end` (default: all) are looked at or changed.
    Returns (path, number of jumps smoothed)."""
    out = list(ball)
    last = len(out) - 1 if end is None else min(end, len(out) - 1)
    final = max((n for n, b in enumerate(out) if b is not None), default=-1)  # the clip's last ball
    present = [i for i, b in enumerate(out) if b is not None and start <= i <= last]
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
        if not wide_enough and hi == present[-1] == final and hi - i < LOST_TAIL_FRAMES:
            for m in range(i, hi + 1):
                out[m] = None
            fixed += 1
            break
        a, b = out[lo], out[hi]
        for m in range(lo + 1, hi):
            w = (times[m] - times[lo]) / span if span > 0 else 1.0
            out[m] = tuple(av + (bv - av) * w for av, bv in zip(a, b))
        fixed += 1
        present = [m for m, b in enumerate(out) if b is not None and start <= m <= last]
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
    or {"exclude": true, "note": ...} to leave a goal out of the clips and index.
    An entry can also set "aim": {"y": metres, "z": metres} (either optional),
    where the shot crosses the goal line, for goals the tracking puts in the
    wrong place (PFF events have no left/right placement)."""
    if not path.exists():
        return {}
    overrides = load_json(path)
    for name, o in overrides.items():
        if not o.get("note"):
            raise ValueError(f"{path}: {name} needs a note")
        if o.get("exclude") is not True and o.get("ballSource") not in BALL_SOURCES:
            raise ValueError(f"{path}: {name} needs a ballSource in {BALL_SOURCES} or \"exclude\": true")
        aim = o.get("aim")
        if aim is not None and (not isinstance(aim, dict) or not aim or set(aim) - {"y", "z"}
                                or not all(isinstance(v, (int, float)) for v in aim.values())):
            raise ValueError(f"{path}: {name} aim must be {{\"y\": metres, \"z\": metres}}")
    return overrides


def load_shot_placement(path=SHOT_PLACEMENT):
    """{clip file name: {"y", "z"}}: where StatsBomb has each goal crossing the line."""
    return load_json(path) if path.exists() else {}


def shot_aim(goal, placement=None, override=None):
    """Where the goal-mouth correction aims the crossing (see goal_mouth.aim_point),
    and where that came from: a hand-set aim in overrides.json wins, then
    StatsBomb's end location, then PFF's height third."""
    hand = (override or {}).get("aim")
    if placement and "y" in placement:
        aim, source = {k: placement[k] for k in ("y", "z") if k in placement}, "statsbomb"
    elif goal.get("shotHeight"):
        aim, source = {"height": goal["shotHeight"]}, "pff height"
    else:
        aim, source = {}, None
    if hand:
        aim.update(hand)
        source = "override"
    return aim, source


def pick_ball_source(raw_distance, override=None):
    """(source, automatic choice): an override's ballSource wins over choose_ball_source."""
    auto = choose_ball_source(raw_distance)
    return (override or {}).get("ballSource", auto), auto


def load_match(game_id):
    meta = load_json(RAW / "metadata" / f"{game_id}.json")[0]
    roster = load_json(RAW / "rosters" / f"{game_id}.json")
    events = load_json(RAW / "events" / f"{game_id}.json")
    return meta, roster, events


def build_clip(meta, roster, goal, frames, goal_index, override=None, events=None, placement=None):
    """Turn one goal's raw tracking window into the clip dict. goal is a find_goals
    entry; override is its overrides.json entry, if any; events (the match's event
    list) gives every touch of the ball for the viewer; placement is its
    shot_placement.json entry, if any."""
    pitch = meta["stadium"]["pitches"][0]
    length, width = pitch["length"], pitch["width"]
    by_shirt = {(r["team"]["id"], r["shirtNumber"]): r for r in roster}
    sides = {"home": meta["homeTeam"], "away": meta["awayTeam"]}

    roles = tuple(sorted(((side, r["shirtNumber"]), r["positionGroupType"])
                         for side, team in sides.items() for r in roster if r["team"]["id"] == team["id"]))
    votes = swapped_votes(meta, roster, events, frames)
    players = {}
    player_frames = []
    for frame in frames:
        positions = {}
        lists = player_lists(frame, meta["id"], roles, votes)
        for side, team in sides.items():
            for p in lists[side]:
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
    fps = meta["fps"]
    # The scorer's team: for an own goal that's the conceding player on the ball.
    team = [xy for pid, xy in player_frames[goal_index].items() if players[pid]["team"] == goal["side"]]
    paths = {source: drop_out_of_play(ball_path(frames, source, length, width), goal_index) for source in BALL_SOURCES}
    raw_distance = raw_ball_distance(paths["raw"], team, goal_index)
    ball_source, auto_source = pick_ball_source(raw_distance, override)
    other = "raw" if ball_source == "smoothed" else "smoothed"
    chosen, borrowed = borrow_gaps(paths[ball_source], paths[other], goal_index)
    tracked = [b is not None for b in chosen]  # frames where a feed really has the ball
    # Where the ball is still missing before the shot, estimate it from where it
    # was last seen, where it reappears and who is near it (see estimate_gaps.py).
    chosen, estimated = estimate_gaps(fill_gaps(chosen), times, player_frames, goal_index)
    penalty = bool(goal.get("penalty"))
    side = goal_side(chosen, goal_index)
    moved = 0.0
    if penalty:
        # A still ball on the spot and a legal setup until the kick (penalty.py).
        chosen = pin_ball(chosen, goal_index, side)
        keeper = find_keeper(player_frames, goal_index, side,
                             [pid for pid, p in players.items() if p["position"] == "GK" and pid != goal["scorerId"]])
        player_frames, moved = place_players(player_frames, times, goal_index, side, goal["scorerId"], keeper)
    # Keepers stay in their area, on the ball-goal line when it's in their half (keepers.py).
    frame_ms = [f["videoTimeMs"] for f in frames]
    logged = [(c["f"], c["p"]) for c in find_contacts(events or [], frame_ms, set(players))]
    keeper_moves = place_keepers(player_frames, times, chosen, [pid for pid, p in players.items() if p["position"] == "GK"],
                                 goal_index, logged, skip={keeper} if penalty else set())
    aim, aim_source = shot_aim(goal, placement, override)
    ball, correction = correct_goal_mouth(chosen, times, goal_index, aim)
    # Only up to the shot: after it the path is the correction's (and physics_shot
    # re-flies the shot), and a glide there would undo where the correction aims it.
    # After a goal-line clearance the tracked ball plays on: smooth that part too.
    ball, jumps = smooth_jumps(ball, times, end=goal_index)
    if correction.get("cleared_after") is not None:
        ball, after = smooth_jumps(ball, times, start=correction["cleared_after"])
        jumps += after
    # The ball only changes direction when someone touches it: straighten it between touches.
    contacts = find_contacts(events or [], frame_ms, set(players))
    # How often the logged toucher is within 3 m of the tracked ball: checks the names.
    raw_ball = fill_gaps(paths["raw"])
    near = [math.dist(raw_ball[c["f"]][:2], player_frames[c["f"]][c["p"]]) < 3 for c in contacts
            if raw_ball[c["f"]] is not None and c["p"] in player_frames[c["f"]]]
    if penalty:  # the ball sits on the spot: nothing touches it before the kick
        contacts = [c for c in contacts if c["f"] >= goal_index]
    late_contacts = [c for c in contacts if c["f"] > goal_index]  # possible deflections of the shot
    # Event times can be off by half a second: line each touch up with the tracking (contacts.py).
    contacts, ball, contact_fixes = align_contacts(contacts, ball, tracked, player_frames, times, goal_index)
    # StatsBomb's on-ball actions are the primary touches (statsbomb.merge_touches).
    # The kick: the frame the tracked ball leaves the scorer (ball_rules.find_kick).
    kick0 = goal_index if penalty else find_kick(ball, times, player_frames, goal["scorerId"], goal_index, side=side)
    sb_clip, sb_shift = clip_statsbomb(meta, roster, events, frame_ms, players, player_frames, ball, tracked, goal, side)
    contacts, ball, sb_placed, lofted, sb_counts = merge_touches(
        contacts, [] if penalty else sb_clip, ball, tracked, times, max(goal_index, kick0), goal["scorerId"], player_frames)
    if penalty:  # a shot touch lined up before the kick is the kick itself
        contacts = [{**c, "f": max(c["f"], goal_index)} for c in contacts]
        contacts = [c for n, c in enumerate(contacts) if not any(d["f"] == c["f"] and d["p"] == c["p"]
                                                                  for d in contacts[:n])]
    # Restarts: the ball stops out of play and is put back by the taker (restarts.py).
    restarts = [] if penalty else find_restarts(events or [], frame_ms, goal_index, set(players))
    for r in restarts:
        # The ball is only dead after the last StatsBomb action before the restart.
        live = [c["f"] for c in contacts if c.get("sb") and (r["out"] or 0) < c["f"] < r["f"] - 3
                and c["p"] != r["p"]]
        if live:
            r["out"] = max(live)
    for r in restarts:  # StatsBomb's time for the restart kick, if it has the taker's touch
        sb_touch = [c for c in contacts if c.get("sb") and c["p"] == r["p"] and abs(c["f"] - r["f"]) <= round(1.5 * fps)
                    and (r["out"] is None or c["f"] > r["out"])]
        if sb_touch:
            r["f"] = min(sb_touch, key=lambda c: abs(c["f"] - r["f"]))["f"]
            r["timed"] = True
    restart_info, dead = apply_restarts(ball, times, player_frames, restarts,
                                        lambda f: next((c["f"] for c in contacts if c["f"] > f), None),
                                        lambda f: next((c["f"] for c in reversed(contacts) if c["f"] < f), None),
                                        goal_index)
    dead_ball = {k: ball[k] for a, b in dead for k in range(a, b + 1)}
    contacts = [c for c in contacts if not any(a < c["f"] <= b for a, b in dead)
                and not any(c["p"] == r["p"] and abs(c["f"] - r["f"]) <= 12 for r in restarts)]
    contacts = sorted(contacts + [{"f": r["f"], "p": r["p"], "b": r["b"]} | ({"sb": "Pass"} if r.get("timed") else {})
                                  for r in restarts], key=lambda c: c["f"])
    direct = any(r["f"] >= goal_index - 3 for r in restarts)  # a goal straight from a free kick
    # The shot: PFF can log it after the ball has left his foot (ball_rules.find_kick).
    shot = shot_contact(contacts, goal, goal_index)
    if shot is None:
        shot = {"f": goal_index, "p": goal["scorerId"], "b": "F"}
        contacts = sorted(contacts + [shot], key=lambda c: c["f"])
    if not penalty and not direct:
        kick = find_kick(ball, times, player_frames, shot["p"], shot["f"], shot["b"], side)
        contacts = [c for c in contacts if c is shot or c["f"] < kick]
        shot["f"] = kick
    # The keeper at the shot: where StatsBomb's freeze frame has him (keepers.py).
    gk_ids = [pid for pid, p in players.items() if p["position"] == "GK"]
    defender = keeper if penalty else defending_keeper(player_frames, gk_ids, side)
    sb_keeper = (placement or {}).get("keeper")
    freeze_gap = (ease_to_freeze_frame(player_frames, times, defender, shot["f"], tuple(sb_keeper))
                  if sb_keeper and defender else None)
    touch_frames = [c["f"] for c in contacts]
    kinks_before = count_kinks(ball, times, player_frames, touch_frames, goal_index)
    # Dribbles aren't logged touch by touch: rebuild them as pushes (dribble.py).
    ball, dribble_touches, carries, dribbles = rebuild_dribbles(ball, times, player_frames, contacts,
                                                                0 if penalty else goal_index)
    contacts = sorted(contacts + dribble_touches, key=lambda c: c["f"])
    touch_frames = [c["f"] for c in contacts]
    # Rebuilt dribbles are already real pushes: hold every frame of them in place below.
    held = touch_frames + [k for a, b in dribbles for k in range(a, b + 1)]
    # Between touches the ball flies, bounces and rolls as a real football does
    # (ball_physics.py); anything the solver can't fit is at least kept straight.
    anchors = anchor_frames(ball, player_frames, held)
    # Heights the ball can be at when touched: feet, head or hands.
    reach = {"H": (1.6, 2.3), "X": (0.5, 2.6)}
    touch_heights = {c["f"]: reach.get(c["b"], (0.0, 1.2)) for c in contacts}
    ball, simulated = apply_physics(ball, times, anchors, goal_index, touch_heights, lofted)
    ball, straightened = straighten_free_flight(ball, player_frames, held, goal_index)
    if penalty:  # keep the ball on the spot whatever smoothing does before the kick
        ball = pin_ball(ball, goal_index, side)
    for k, b in dead_ball.items():  # and a dead ball where it was put
        ball[k] = b
    contacts = [c for c in contacts if not any(a < c["f"] < b for a, b in dead)]
    carries = [c for c in carries if not any(c[0] <= b and a <= c[1] for a, b in dead)]
    # The shot: from the shooter's foot, one kick to where it crosses the line (ball_rules.py).
    shooter_team = players[shot["p"]]["team"] if shot["p"] in players else goal["side"]
    deflections = [c for c in late_contacts if players[c["p"]]["team"] != shooter_team]
    # A goal-line clearance needs a defender to clear it: a logged touch by the
    # defending team within a second of the ball's deepest point. Without one,
    # the ball coming back out is the net or the tracking: it's a goal in the net.
    scoring = goal["side"] if not goal["ownGoal"] else ("away" if goal["side"] == "home" else "home")
    cleared_after = correction.get("cleared_after")
    clearer = next((c for c in late_contacts if cleared_after is not None and players[c["p"]]["team"] != scoring
                    and c["p"] != shot["p"] and shot["f"] < c["f"] <= cleared_after + round(fps)), None)
    if clearer is None:
        cleared_after = None
    ball, shot_info = fly_shot(ball, times, player_frames, shot["f"], shot["p"], shot["b"], side, penalty or direct,
                               cleared_after, deflections)
    # After the kick only a deflection with the ball near him touches it (the
    # shot is logged twice, or by a player the ball never reaches).
    # A goal-line clearance: the defender's touch where the ball turns back.
    clearance = [{**clearer, "f": shot_info["line_frame"]}] if clearer and shot_info.get("line_frame") else []
    contacts = sorted([c for c in contacts if c["f"] < shot["f"] or c is shot] + shot_info["deflected"] + clearance,
                      key=lambda c: c["f"])
    carries = [[a, min(b, shot["f"] - 1), p] for a, b, p in carries if a < shot["f"] - 1]
    held = carries + [[a, b, ""] for a, b in dead]  # the touch rule leaves dead balls alone
    rule_before = violations(ball, times, contacts, player_frames, goal_index, held)
    # Before the shot the ball only turns, speeds up or rises at a touch (ball_rules.py).
    keepers = {pid: (1 if player_frames[goal_index][pid][0] > 0 else -1) for pid, p in players.items()
               if p["position"] == "GK" and pid in player_frames[goal_index]}
    # Every touch meets the player: players onto a well-supported ball (ball_rules.meet_touches).
    met, too_far = meet_touches(ball, times, player_frames, [c for c in contacts if c["f"] < shot["f"]] + clearance,
                                len(frames) - 1, skip={r["f"] for r in restarts})
    # Checked and fixed on the clip as written (2 decimals; times to the ms).
    times_out = [round(t, 3) for t in times]
    players_out = [{pid: (round(x, 2), round(y, 2)) for pid, (x, y) in ps.items()} for ps in player_frames]
    redraw = ([r["f"] for r in restarts if r["f"] < shot["f"]] + [f for f in sb_placed if f < shot["f"]]
              + ([shot["f"]] if shot_info["at_foot_moved"] > 0.05 else []))
    ball, contacts, rule_counts = enforce_touch_rule(ball, times_out, players_out, contacts, held, shot["f"],
                                                     keepers, moved=redraw, keep=[r["f"] for r in restarts],
                                                     leave=too_far)
    # How the scorer strikes it: StatsBomb's shot technique (volleys.py).
    final_shot = next((c for c in contacts if (c["f"], c["p"]) == (shot["f"], shot["p"])), None)
    if final_shot is None:
        raise ValueError(f"shot contact {shot} missing from {[(c['f'], c['p']) for c in contacts if c['f'] > shot['f'] - 20]}")
    technique = (placement or {}).get("technique")
    pose, pose_z, pose_angle = shot_pose(final_shot, technique, ball, times, player_frames)
    dive = plan_dive(ball, times, player_frames, defender, shot["f"], side) if defender else None
    redraw_roll_out(ball, times, restart_info)
    ball = [tuple(round(v, 2) for v in b) if b is not None else None for b in ball]
    rule_breaks = violations(ball, times_out, contacts, players_out, goal_index, held)
    line_change = across_the_line(ball, times_out, shot["f"], side)
    kinks_after = count_kinks(ball, times, player_frames, touch_frames, goal_index)
    missing_ball = sum(p is None for p in paths[ball_source])

    # Nobody runs faster than a sprint; touches stay where they are.
    fixed = {}
    for c in contacts:
        fixed.setdefault(c["p"], set()).add(c["f"])
    if defender:
        fixed.setdefault(defender, set()).update(range(shot["f"], len(frames)))  # the keeper's spot and dive
    sped = limit_player_speeds(player_frames, times, fixed)

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
        "penalty": penalty,
        "clock": goal["clock"],
        "period": goal["period"],
        "fps": meta["fps"],
        "goalFrame": goal_index,
        "goalT": out_frames[goal_index]["t"],
        "ballSource": ball_source,
        "ballCorrected": correction["corrected"],
        "ballEstimated": estimated,  # [first, last] frame ranges where the ball position is estimated
        "contacts": contacts,  # "s": 1 marks a touch added for a dribble
        "carries": carries,  # [first, last, player id]: dribbles the viewer holds at the player's feet
        "restarts": restart_info,  # throw-ins, corners, goal kicks, free kicks: see restarts.py
        "cleared": cleared_after is not None,  # a goal-line clearance: the ball comes back out
        "kickFrame": shot["f"],  # the frame the shot leaves his foot (goalFrame is PFF's shot event)
        "keeperDive": dive,  # how the keeper reacts to the shot: see keepers.plan_dive
        "needsReview": correction["needs_review"],
        "teams": {"home": team_meta("home"), "away": team_meta("away")},
        "players": list(players.values()),
        "frames": out_frames,
    }
    # StatsBomb's events in this clip, and the accuracy report on the clip as written (accuracy.py).
    accuracy = check_clip(clip, sb_clip, placement)
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
        "straightened": straightened,
        "simulated": simulated,
        "shot": shot_info,
        "cleared": cleared_after is not None,
        "line_change": line_change,
        "accuracy": accuracy,
        "sb_shift": sb_shift,
        "sb_counts": sb_counts,
        "sb_goal_frame": next((e["f"] for e in sb_clip if e["type"] == "Shot" and e["p"] == goal["scorerId"]
                               and (e["raw"].get("shot") or {}).get("outcome", {}).get("name") == "Goal"), None),
        "met": [(players[c["p"]]["name"], c["f"], round(m, 2)) for c, m in met],
        "too_far": [(players[c["p"]]["name"], c["f"]) for c in too_far],
        "sped": {players[pid]["name"]: round(m, 2) for pid, m in sped.items()},
        "kick_shift": round(times[shot["f"]] - times[goal_index], 2),
        "rule_before": rule_before,
        "rule_breaks": rule_breaks,
        "rule_counts": rule_counts,
        "contact_fixes": contact_fixes,
        "dribbles": len(dribbles),
        "carries": len(carries),
        "kinks": (kinks_before, kinks_after),
        "penalty_moved": moved,
        "aim_source": aim_source,
        "aim": aim,
        "toucher_near": (sum(near), len(near)),
        "restarts": restart_info,
        "keeper_moves": {players[pid]["name"]: m for pid, m in keeper_moves.items()},
        "freeze_gap": freeze_gap,
        "dive": dive,
        "shot_pose": {"technique": technique, "pose": pose, "z": pose_z, "angle": pose_angle},
        "votes": len(votes or {}),
        "swapped": votes is not None,
    }
    return clip, stats


def clip_statsbomb(meta, roster, events, frame_ms, players, player_frames, ball, tracked, goal, side):
    """The clip's StatsBomb events (statsbomb.clip_events), lined up with the
    tracked players and ball (where the feed really had it), and the time
    shift; ([], 0) without StatsBomb."""
    sb = match_events(meta, roster, events or [])
    if not sb:
        return [], 0.0
    team_id = {"home": meta["homeTeam"]["id"], "away": meta["awayTeam"]["id"]}
    pid_of = {(side_, str(r["shirtNumber"])): r["player"]["id"] for side_, tid in team_id.items()
              for r in roster if r["team"]["id"] == tid}
    scoring = goal["side"] if not goal["ownGoal"] else ("away" if goal["side"] == "home" else "home")
    attack = {scoring: side, ("away" if scoring == "home" else "home"): -side}
    secs = [ms / 1000 for ms in frame_ms]

    def where(pid, t):
        if not secs[0] <= t <= secs[-1]:
            return None, None
        k = bisect.bisect_left(secs, t, hi=len(secs) - 1)
        b = ball[k] if tracked[k] and ball[k] is not None else None
        return player_frames[k].get(pid), (b[:2] if b else None)

    return clip_events(sb, frame_ms, lambda s_, n: pid_of.get((s_, n)) if pid_of.get((s_, n)) in players else None,
                       attack, where)


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
    clip, stats = build_clip(meta, roster, goal, frames, goal_index, override, events,
                             load_shot_placement().get(clip_name(goal)))
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
