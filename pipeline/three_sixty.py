"""StatsBomb 360: every visible player at each event, to check PFF's tracks.

StatsBomb's 360 data (data/statsbomb/three-sixty/{match}.json, fetch with
curl) has a freeze frame for most events: every player in the broadcast
view, with a teammate flag (the actor's team or not), the actor and the
keepers marked, and the visible area. Only the actor (the event's player)
and the keepers are named; the rest are anonymous.

The frames ride on the clip's StatsBomb events (statsbomb.clip_events), which
are already lined up with the tracking; `align` then fine-tunes the time
shift on the players' positions (they're the evidence here). Each frame's
anonymous players are matched to the tracked players of their team (optimal
assignment, the named ones fixed). From those matches:

- fix_labels: the named actor far from his own track while another tracked
  player (either team) sits on the actor's spot means PFF gave them each
  other's labels; swapped over the stretch bounded by where the two tracks
  meet (identity._stretch), only if the 360 frames there fit the swapped
  labels better, and never taking the scorer off the shot (identity.keeps_shot).
- correct_tracks: a player whose track is more than OFF_M from his matched
  360 spot in at least MIN_FRAMES frames of a run is moved onto those spots,
  the correction smoothed between frames and eased in and out gently. A
  frozen track (PFF parks players it can't see; slower than FROZEN_MPS on
  average) is rebuilt the same way from any frame it's off in. Keepers are
  left to keepers.py.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

from identity import ACCEL, _stretch, blend_swap, keeps_shot
from statsbomb import CACHE, _match_id, to_pitch

MATCH_MAX_M = 12.0  # a 360 player farther than this from every free track of his team isn't matched
OFF_M = 3.0  # a track this far from his 360 spot is off
MIN_FRAMES = 2  # ...in at least this many 360 frames in a row before it's corrected
ALIGN_S = 1.0  # fine-tune the events' time shift this far either side, on the players
ACTOR_AT_M = 2.0  # another track this close to the actor's spot may be the actor
SWAP_GAIN_M = 3.0  # the swapped labels must fit the 360 frames better by this much in total
ACROSS_FRAMES = 3  # frames backing a swap across teams (the kits tell them apart: it takes more than one)
SMOOTH_S = 0.4
KEY_ACCEL = 8.0  # m/s²: the most a correction changes between two 360 frames (smoothing takes it to about ACCEL)
FROZEN_MPS = 0.8  # a track moving slower than this on average (up to his last 360 frame) is frozen
SHIFT_ROUNDS = 3
ACTOR_EVENT_M = 5.0  # a 360 frame's actor this far from the event's own location is the wrong way round
MIN_SHIFT_PLAYERS = 6  # fewer matched players than this: the frame's shift can't be fitted (left at 0)


@lru_cache(maxsize=None)
def _frames(sb_id):
    path = CACHE / "three-sixty" / f"{sb_id}.json"
    if sb_id is None or not path.exists():
        return {}
    return {f["event_uuid"]: f for f in json.loads(path.read_text())}


def match_frames(meta):
    """{StatsBomb event id: 360 frame} for a PFF match's metadata ({} without the file)."""
    return _frames(_match_id(meta["homeTeam"]["name"], meta["awayTeam"]["name"], meta["date"]))


def clip_frames(meta, events, players):
    """The 360 frames of a clip's StatsBomb events (statsbomb.clip_events
    output), on our pitch: [{"f", "actor" (our id), "points": [(side, (x, y),
    named id or None)], "keeper": {side: (x, y)}}], one per frame (the first
    event of each; a shot's freeze frame from the events file instead, with
    everyone named, "named": True)."""
    frames = match_frames(meta)
    other = {"home": "away", "away": "home"}
    keepers = {side: next((pid for pid, p in players.items() if p["team"] == side and p["position"] == "GK"), None)
               for side in ("home", "away")}
    out, seen = [], set()
    for e in sorted(events, key=lambda e: e["f"]):
        if e.get("ff") and e["type"] == "Shot":
            # A shot's own freeze frame names everyone in it.
            points = [(side, xy, pid) for pid, side, xy, _ in e["ff"]] + [(e["side"], e["xy"], e["p"])]
            out.append({"f": e["f"], "actor": e["p"], "points": points, "named": True})
            seen.add(e["f"])
            continue
        fr = frames.get(e["raw"].get("id"))
        if fr is None or e["f"] in seen:
            continue
        seen.add(e["f"])
        side = e["side"]
        points = []
        for q in fr["freeze_frame"]:
            s = side if q["teammate"] else other[side]
            xy = to_pitch(q["location"], e["att"])
            named = e["p"] if q.get("actor") else (keepers[s] if q.get("keeper") else None)
            points.append((s, xy, named))
        # The actor is where the event is. Some frames come the other way round
        # (every spot mirrored through the centre spot): turned back; a frame that
        # fits neither way is left out.
        actor = next((xy for _, xy, n in points if n == e["p"]), None)
        if actor is not None and math.dist(actor, e["xy"]) > ACTOR_EVENT_M:
            if math.dist((-actor[0], -actor[1]), e["xy"]) > ACTOR_EVENT_M:
                continue
            points = [(s, (-xy[0], -xy[1]), n) for s, xy, n in points]
        out.append({"f": e["f"], "actor": e["p"], "points": points})
    return out


def hungarian(cost):
    """Minimum-cost assignment of rows to distinct columns (rows <= columns).
    Returns the column for each row."""
    n, m = len(cost), len(cost[0]) if cost else 0
    if n == 0:
        return []
    INF = float("inf")
    u, v, p, way = [0.0] * (n + 1), [0.0] * (m + 1), [0] * (m + 1), [0] * (m + 1)
    for i in range(1, n + 1):
        p[0], j0 = i, 0
        minv, used = [INF] * (m + 1), [False] * (m + 1)
        while True:
            used[j0] = True
            i0, delta, j1 = p[j0], INF, 0
            for j in range(1, m + 1):
                if not used[j]:
                    cur = cost[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j], way[j] = cur, j0
                    if minv[j] < delta:
                        delta, j1 = minv[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break
    col = [0] * n
    for j in range(1, m + 1):
        if p[j]:
            col[p[j] - 1] = j - 1
    return col


def _assign(frame, at, players, shift):
    """{player id: his 360 spot + shift} (see match_frame) for a given shift."""
    pts = [(side, (xy[0] + shift[0], xy[1] + shift[1]), n) for side, xy, n in frame["points"]]
    out = {n: xy for _, xy, n in pts if n is not None and n in at}
    for side in ("home", "away"):
        free = [xy for s, xy, n in pts if s == side and n is None]
        tracks = [pid for pid in at if players[pid]["team"] == side and pid not in out]
        if not free or not tracks:
            continue
        if len(free) > len(tracks):  # more players seen than tracked (a substitution frame): the nearest ones
            free = sorted(free, key=lambda xy: min(math.dist(xy, at[q]) for q in tracks))[:len(tracks)]
        cost = [[min(math.dist(xy, at[q]), 2 * MATCH_MAX_M) ** 2 for q in tracks] for xy in free]
        for xy, j in zip(free, hungarian(cost)):
            if math.dist(xy, at[tracks[j]]) <= MATCH_MAX_M:
                out[tracks[j]] = xy
    return out


def _median(values):
    v = sorted(values)
    return (v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2


def match_frame(frame, at, players):
    """{player id: his 360 spot} for one 360 frame against the tracked players
    `at` ({id: (x, y)}): named players are theirs; the rest of each team go
    to that team's other tracks by optimal assignment (squared distance, so
    one big miss costs more than two small ones), dropping pairs farther
    than MATCH_MAX_M apart. A 360 frame is placed from the broadcast camera
    and is often shifted as a whole (3-6 m against the tracking, the same
    for every player in it): the spots are moved by the frame's median
    offset to the tracks it matches (refit with the matching)."""
    shift = (0.0, 0.0)
    for _ in range(SHIFT_ROUNDS):
        m = _assign(frame, at, players, shift)
        if len(m) < MIN_SHIFT_PLAYERS:
            break
        shift = (shift[0] + _median(at[p][0] - xy[0] for p, xy in m.items()),
                 shift[1] + _median(at[p][1] - xy[1] for p, xy in m.items()))
    frame["shift"] = shift
    return _assign(frame, at, players, shift)


def frame_cost(frame, at, players):
    """How well the tracked players `at` fit a 360 frame: mean distance of the
    matched pairs (capped at MATCH_MAX_M), unmatched points counted as MATCH_MAX_M."""
    matched = match_frame(frame, at, players)
    ds = [min(math.dist(xy, at[pid]), MATCH_MAX_M) for pid, xy in matched.items() if pid in at]
    ds += [MATCH_MAX_M] * (len(frame["points"]) - len(ds))
    return sum(ds) / len(ds) if ds else math.inf


def align(frames, player_frames, times, players):
    """Shift the 360 frames (in place, by whole frames within ALIGN_S) to where
    the tracked players fit them best, all together; returns the shift in s."""
    if not frames:
        return 0.0
    dt = (times[-1] - times[0]) / max(len(times) - 1, 1)
    reach = int(round(ALIGN_S / dt))
    n = len(player_frames)

    def cost(sh):
        cs = [frame_cost(fr, player_frames[fr["f"] + sh], players)
              for fr in frames if 0 <= fr["f"] + sh < n]
        return sum(cs) / len(cs) if cs else math.inf

    best = min(range(-reach, reach + 1, 3), key=lambda sh: (cost(sh), abs(sh)))
    best = min(range(best - 2, best + 3), key=lambda sh: (cost(sh), abs(sh)))
    for fr in frames:
        fr["f"] = min(max(fr["f"] + best, 0), n - 1)
    return best * dt


def _swap(player_frames, a, b, lo, hi):
    for k in range(lo, hi + 1):
        pa, pb = player_frames[k].get(a), player_frames[k].get(b)
        if pa is not None and pb is not None:
            player_frames[k][a], player_frames[k][b] = pb, pa


def fix_labels(frames, player_frames, times, players, shot=None):
    """Swap two players' labels (in place) where a named player's spot (the
    actor's, or anyone's in a shot's freeze frame) has another track on it
    and his own track is off it (see the module doc). shot: (shooter id,
    kick frame, ball xy). Returns [(named player, other, first, last, across teams)]."""
    swaps = []
    for _ in range(3):  # a few labels can be passed round in a ring: settle them pairwise
        made = False
        for fr in frames:
            at = player_frames[fr["f"]]
            match_frame(fr, at, players)  # fits the frame's shift
            sx, sy = fr.get("shift", (0.0, 0.0))
            for _, xy, pid in fr["points"]:
                if pid is None or pid not in at or players[pid]["position"] == "GK":
                    continue
                spot = (xy[0] + sx, xy[1] + sy)
                if math.dist(at[pid], spot) <= OFF_M:
                    continue
                near = [(math.dist(q_xy, spot), q) for q, q_xy in at.items()
                        if q != pid and players[q]["position"] != "GK"]
                if not near or min(near)[0] > ACTOR_AT_M:
                    continue
                other = min(near)[1]
                lo, hi = _stretch(player_frames, pid, other, fr["f"])
                if not keeps_shot(player_frames, pid, other, lo, hi, shot):
                    continue
                inside = [g for g in frames if lo <= g["f"] <= hi]
                support = sum(_supports(g, player_frames[g["f"]], pid, other) for g in inside)
                across = players[pid]["team"] != players[other]["team"]
                if across and support < ACROSS_FRAMES:
                    continue  # a mix-up across teams (kits) needs more than one moment's evidence
                if support < MIN_FRAMES and not (support and fr.get("named")):
                    continue
                before = sum(frame_cost(g, player_frames[g["f"]], players) * len(g["points"]) for g in inside)
                saved = [dict(f) for f in player_frames]
                _swap(player_frames, pid, other, lo, hi)
                after = sum(frame_cost(g, player_frames[g["f"]], players) * len(g["points"]) for g in inside)
                if after > before - SWAP_GAIN_M:
                    player_frames[:] = saved
                    continue
                blend_swap(player_frames, times, pid, other, lo, hi)
                swaps.append((pid, other, lo, hi, across))
                made = True
                at = player_frames[fr["f"]]
        if not made:
            break
    return swaps


def _supports(frame, at, a, b):
    """1 if this frame names a or b at a spot where the other one's track is
    (and his own isn't), else 0."""
    sx, sy = frame.get("shift", (0.0, 0.0))
    for _, xy, n in frame["points"]:
        if n in (a, b) and n in at:
            m = b if n == a else a
            spot = (xy[0] + sx, xy[1] + sy)
            if m in at and math.dist(at[m], spot) <= ACTOR_AT_M < math.dist(at[n], spot):
                return 1
    return 0


def _smooth(values, times, sigma):
    """Gaussian smoothing over time (sigma in s)."""
    out = []
    dt = (times[-1] - times[0]) / max(len(times) - 1, 1)
    reach = int(3 * sigma / dt) + 1
    for i, t in enumerate(times):
        w = [(math.exp(-((times[j] - t) / sigma) ** 2 / 2), values[j])
             for j in range(max(0, i - reach), min(len(times), i + reach + 1))]
        tot = sum(a for a, _ in w)
        out.append(tuple(sum(a * v[d] for a, v in w) / tot for d in range(2)))
    return out


def correct_tracks(frames, player_frames, times, players, fixed=None):
    """Move players onto their 360 spots where their tracks are off (see the
    module doc). fixed: {player id: frames to leave where they are}. In
    place; returns {player id: largest correction (m)}."""
    fixed = fixed or {}
    seen = {}  # pid -> [(frame, 360 spot)]
    for fr in frames:
        for pid, xy in match_frame(fr, player_frames[fr["f"]], players).items():
            if players[pid]["position"] != "GK":  # keepers: keepers.py, on StatsBomb's freeze frame
                seen.setdefault(pid, []).append((fr["f"], xy))
    moved = {}
    n = len(player_frames)
    for pid, obs in seen.items():
        obs.sort()
        off = [(f, (xy[0] - player_frames[f][pid][0], xy[1] - player_frames[f][pid][1])) for f, xy in obs
               if pid in player_frames[f]]
        # A frozen track (PFF parks players it can't see) is rebuilt from any
        # frame it's off in; a moving one needs MIN_FRAMES in a row.
        last = obs[-1][0]
        path = [player_frames[k][pid] for k in range(0, last + 1) if pid in player_frames[k]]
        span = times[last] - times[0]
        frozen = span > 0 and sum(math.dist(a, b) for a, b in zip(path[::15], path[15::15])) / span < FROZEN_MPS
        good, run = set(), []
        for f, o in off + [(None, None)]:
            if f is not None and math.hypot(*o) > OFF_M:
                run.append(f)
                continue
            if len(run) >= (1 if frozen else MIN_FRAMES):
                good.update(run)
            run = []
        if not good:
            continue
        # Every 360 frame he's matched in is a key: his correction there, or none
        # where he's close (or off for too short a run to trust).
        keys = [(f, o if f in good else (0.0, 0.0)) for f, o in off]
        # Each key's correction differs from the last by no more than a gentle ease
        # (ACCEL) can cover in the time between: 360 spots jitter, players don't.
        limited = [keys[0]]
        for f, o in keys[1:]:
            pf_, po = limited[-1]
            gap = times[f] - times[pf_]
            d = math.hypot(o[0] - po[0], o[1] - po[1])
            most = KEY_ACCEL * gap * gap / 6
            if d > most:
                o = (po[0] + (o[0] - po[0]) * most / d, po[1] + (o[1] - po[1]) * most / d)
            limited.append((f, o))
        keys = limited
        # The correction: eased between keys; before the first it eases in
        # (held from the start for a frozen track), after the last it holds
        # (his track was last seen wrong; after the goal settle_after_goal fades it).
        first, last = keys[0], keys[-1]
        corr = []
        for k in range(n):
            t = times[k]
            if t >= times[last[0]]:
                o = last[1]
            elif t < times[first[0]]:
                u = 1.0
                if not frozen:
                    ease = math.sqrt(6 * math.hypot(*first[1]) / ACCEL)
                    u = max(1 - (times[first[0]] - t) / ease, 0.0) if ease > 0 else 1.0
                w = u * u * (3 - 2 * u)
                o = (first[1][0] * w, first[1][1] * w)
            else:
                j = next(i for i in range(1, len(keys)) if times[keys[i][0]] >= t)
                (fa, oa), (fb, ob) = keys[j - 1], keys[j]
                s = (t - times[fa]) / (times[fb] - times[fa]) if times[fb] > times[fa] else 1.0
                s = s * s * (3 - 2 * s)
                o = (oa[0] + (ob[0] - oa[0]) * s, oa[1] + (ob[1] - oa[1]) * s)
            corr.append(o)
        corr = _smooth(corr, times, SMOOTH_S)
        worst = 0.0
        for k in range(n):
            if pid not in player_frames[k] or k in fixed.get(pid, ()):
                continue
            x, y = player_frames[k][pid]
            player_frames[k][pid] = (x + corr[k][0], y + corr[k][1])
            worst = max(worst, math.hypot(*corr[k]))
        if worst > 0.05:
            moved[pid] = worst
    return moved
