"""Every touch of the ball in a clip, from PFF's event data.

PFF logs each on-ball action with its video time, the player and the body
part: passes, shots, crosses, clearances, rebounds, touches, and the first
touch when receiving (IT, with initialTouch.initialBodyType). The viewer uses
these to make the player's foot, head or hands actually meet the ball at that
moment, so contacts are visible instead of the ball floating past people.
"""

ACTIONS = {"PA", "SH", "CR", "CL", "RE", "TC", "IT"}  # skip CH (challenge) and BC (carry): no single touch

# PFF body types -> R/L foot, H head, X hands (throw-ins, keeper), F foot (side unknown).
BODY = {"R": "R", "L": "L", "HE": "H", "TWOHANDS": "X", "RH": "X", "LH": "X", "HA": "X"}

MIN_GAP_FRAMES = 4  # drop repeat logs of the same touch


def body_part(event):
    pe = event.get("possessionEvents") or {}
    raw = pe.get("bodyType")
    if not raw and pe.get("possessionEventType") == "IT":
        raw = (event.get("initialTouch") or {}).get("initialBodyType")
    return BODY.get(raw or "", "F")


def find_contacts(events, frame_ms, player_ids):
    """events: the match's event list. frame_ms: videoTimeMs of each clip frame.
    player_ids: ids (str) of players in the clip.
    Returns [{"f": frame index, "p": player id, "b": body part}] in time order."""
    if not frame_ms:
        return []
    lo, hi = frame_ms[0], frame_ms[-1]
    found = []
    for e in events:
        pe = e.get("possessionEvents") or {}
        if pe.get("possessionEventType") not in ACTIONS:
            continue
        t = e.get("eventTime")
        pid = (e.get("gameEvents") or {}).get("playerId")
        if t is None or pid is None:
            continue
        ms = t * 1000
        if not lo <= ms <= hi:
            continue
        pid = str(pid)
        if pid not in player_ids:
            continue
        f = min(range(len(frame_ms)), key=lambda i: abs(frame_ms[i] - ms))
        found.append({"f": f, "p": pid, "b": body_part(e)})
    found.sort(key=lambda c: c["f"])
    out = []
    for c in found:
        if out and out[-1]["p"] == c["p"] and c["f"] - out[-1]["f"] < MIN_GAP_FRAMES:
            continue
        out.append(c)
    return out
