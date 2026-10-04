"""Defenders' actions from StatsBomb: blocks, clearances and tackles.

StatsBomb names the defender and the kind of action (Block, Clearance, Duel
of type Tackle) but not how it looked. That comes from the measured data at
the event: a low ball (the tracked feed) beyond his standing reach on PFF's
own track while he runs into it, or well beyond it, is a slide; otherwise
he stays on his feet. A headed clearance is just a header (the touch). The action failed if
the next on-ball action by anyone else is the other team's.

The viewer plays them (rig.ts: animateSlide, animateSlideBlock,
animateStandBlock) timed to his touch, or to the event if he doesn't get one.
"""

import math

LOW_Z = 0.5  # a ball lower than this can be slid for
REACH_M = 1.0  # farther than this from his centre (PFF's track) and running into it, a low ball needs a slide
RUN_MPS = 3.0
FAR_M = 1.8  # this far, a slide however fast he's going (PFF's tracks are ~1 m off: a shorter gap alone isn't enough)
TOUCH_S = 0.4  # his touch this close to the event is the action
NEXT_S = 2.0  # the next on-ball action within this long says whether it worked
KINDS = {"Block", "Clearance", "Duel"}
FEED_AT_EVENT_M = 5.0  # the measured ball farther than this from StatsBomb's location isn't this action's


def _speed(track, times, k, half=3):
    a, b = max(k - half, 0), min(k + half, len(track) - 1)
    if track[a] is None or track[b] is None or times[b] <= times[a]:
        return 0.0
    return math.dist(track[a], track[b]) / (times[b] - times[a])


def plan_defense(events, contacts, ball, tracked, times, reference, players, end, clip_ball=None):
    """[{"f", "p", "kind": "slide" | "slideBlock" | "block", "ok", "dir"}] for
    the clip's StatsBomb defensive actions up to frame `end`. events: the
    clip's StatsBomb events; contacts: the clip's touches; ball: the clip's
    ball feed, tracked[k] where it really has the ball; clip_ball: the clip's
    ball (used where the feed isn't at StatsBomb's location); reference: PFF's tracks."""
    fps_dt = (times[-1] - times[0]) / max(len(times) - 1, 1)
    out = []
    for e in sorted(events, key=lambda e: e["f"]):
        if e["type"] not in KINDS or e["f"] > end or e["p"] not in players:
            continue
        raw = e["raw"]
        if e["type"] == "Duel" and (raw.get("duel") or {}).get("type", {}).get("name") != "Tackle":
            continue
        if e["type"] == "Clearance" and e["b"] == "H":
            continue
        pid = e["p"]
        touch = min((c for c in contacts if c["p"] == pid and abs(c["f"] - e["f"]) * fps_dt <= TOUCH_S),
                    key=lambda c: abs(c["f"] - e["f"]), default=None)
        f = touch["f"] if touch else e["f"]
        # The measured ball near the moment (the feed where it really has it).
        near = [k for k in range(max(0, f - 3), min(len(ball), f + 4)) if tracked[k] and ball[k] is not None]
        b = ball[min(near, key=lambda k: abs(k - f))] if near else None
        if (b is None or math.dist(b[:2], e["xy"]) > FEED_AT_EVENT_M) and clip_ball is not None:
            b = clip_ball[f]  # the feed isn't at StatsBomb's spot: the clip's ball (StatsBomb-placed)
        me = reference[f].get(pid)
        if b is None or me is None:
            continue
        gap = math.dist(me, b[:2])
        run = _speed([r.get(pid) for r in reference], times, f)
        slide = b[2] < LOW_Z and ((gap >= REACH_M and run >= RUN_MPS) or gap >= FAR_M)
        if e["type"] == "Block":
            kind = "slideBlock" if slide else "block"
        elif e["type"] == "Duel":
            kind = "slide" if slide else "block"
        else:
            if not slide:
                continue  # a clearance on his feet is an ordinary kick (the touch)
            kind = "slide"
        team = players[pid]["team"]
        nxt = next((x for x in sorted(events, key=lambda x: x["f"]) if x["on_ball"] and x["p"] != pid
                    and x["f"] > e["f"] and (x["f"] - e["f"]) * fps_dt <= NEXT_S), None)
        ok = nxt is None or players.get(nxt["p"], {}).get("team") == team
        if e["type"] == "Duel":
            outcome = (raw.get("duel") or {}).get("outcome", {}).get("name", "")
            ok = ok and not outcome.startswith("Lost")
        out.append({"f": f, "p": pid, "kind": kind, "ok": bool(ok), "type": e["type"],
                    "dir": [round(b[0] - me[0], 2), round(b[1] - me[1], 2)]})
    return out
