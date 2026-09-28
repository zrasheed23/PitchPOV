"""How the scorer strikes the ball, from StatsBomb's shot technique.

Acrobatic goals are rare, so only the goal's shot is ever acrobatic, and only
when StatsBomb says so:
- Normal, Lob, Diving Header: an ordinary kick or header (no pose).
- Half Volley: a standing half-volley pose ("half").
- Volley: a standing volley ("volley"), or a scissor kick ("scissor") if the
  ball is between SCISSOR_Z as it arrives and he's side-on to where he sends
  it (SIDE_ON_DEG either side of square).
- Overhead Kick: a bicycle kick ("bicycle"). No 2022 goal has it.
The pose goes on the shot contact as "v". Other touches never get one.

Facing: where he's running over the last FACING_S before the touch if he's
moving, else toward where the ball comes from (players face the ball).
"""

import math

SCISSOR_Z = (0.9, 1.4)  # higher than this at a foot touch, the tracked height is off (a foot reaches ~1.2 m)
SIDE_ON_DEG = 25.0  # facing 90 +- this many degrees from the shot's direction: side-on
FACING_S = 0.3
MOVING_MPS = 1.5
ARRIVE_FRAMES = 2
POSES = {"Half Volley": "half", "Volley": "volley", "Overhead Kick": "bicycle"}


def facing(player_frames, ball, times, pid, f):
    """Unit (x, y) the player faces at frame f, or None."""
    p = player_frames[f].get(pid)
    back = next((k for k in range(f, -1, -1) if times[f] - times[k] >= FACING_S), 0)
    q = player_frames[back].get(pid)
    if p is None:
        return None
    if q is not None and times[f] > times[back]:
        vx, vy = (p[0] - q[0]) / (times[f] - times[back]), (p[1] - q[1]) / (times[f] - times[back])
        if math.hypot(vx, vy) >= MOVING_MPS:
            n = math.hypot(vx, vy)
            return vx / n, vy / n
    b = ball[back]
    if b is None:
        return None
    dx, dy = b[0] - p[0], b[1] - p[1]
    n = math.hypot(dx, dy)
    return (dx / n, dy / n) if n > 1e-6 else None


def shot_pose(shot, technique, ball, times, player_frames):
    """Set shot["v"] from the StatsBomb technique (in place). Returns
    (pose or None, ball height as it arrives, degrees between his facing and
    the shot's direction or None)."""
    f, pid = shot["f"], shot["p"]
    pose = POSES.get(technique)
    z = max((ball[k][2] for k in range(max(f - ARRIVE_FRAMES, 0), f + 1) if ball[k] is not None), default=0.0)
    angle = None
    after = next((k for k in range(f + 1, len(ball)) if times[k] - times[f] >= 0.2 and ball[k] is not None), None)
    face = facing(player_frames, ball, times, pid, f)
    if after is not None and face is not None and ball[f] is not None:
        dx, dy = ball[after][0] - ball[f][0], ball[after][1] - ball[f][1]
        n = math.hypot(dx, dy)
        if n > 1e-6:
            angle = math.degrees(math.acos(max(-1.0, min(1.0, (face[0] * dx + face[1] * dy) / n))))
    if (pose == "volley" and SCISSOR_Z[0] <= z <= SCISSOR_Z[1] and angle is not None
            and abs(angle - 90) <= SIDE_ON_DEG):
        pose = "scissor"
    if pose:
        shot["v"] = pose
    else:
        shot.pop("v", None)
    return pose, z, angle
