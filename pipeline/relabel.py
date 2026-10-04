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

StatsBomb events come from statsbomb.py, on PFF's clock.
"""

import math
from collections import Counter, defaultdict

from statsbomb import match_events

VOTE_M = 5.0
MIN_VOTES = 2
MIN_SHARE = 0.6


def statsbomb_events(meta, roster, events):
    """[(period, video time s, (side, shirt number), (x, y) in StatsBomb units)]
    for every StatsBomb event with a player and a location (statsbomb.py), or
    None if the match isn't in the cache."""
    sb = match_events(meta, roster, events)
    if sb is None:
        return None
    return [(e["period"], e["t"], (e["side"], e["number"]), e["loc"]) for e in sb]


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
