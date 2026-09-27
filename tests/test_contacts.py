import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from contacts import find_contacts


def ev(kind, t, pid, body=None, initial=None):
    e = {"eventTime": t, "gameEvents": {"playerId": pid}, "possessionEvents": {"possessionEventType": kind, "bodyType": body}}
    if initial:
        e["initialTouch"] = {"initialBodyType": initial}
    return e


def test_find_contacts_maps_events_to_frames_and_body_parts():
    frame_ms = [1000 + i * 100 for i in range(50)]  # 10 fps from t = 1 s
    events = [
        ev("IT", 1.5, 7, initial="L"),  # first touch with the left foot
        ev("PA", 2.0, 7, body="R"),  # pass with the right
        ev("CH", 2.2, 9),  # challenge: not a touch
        ev("SH", 3.0, 9, body="HE"),  # header
        ev("PA", 9.0, 7, body="R"),  # outside the clip
        ev("PA", 2.5, 99, body="R"),  # player not in the clip
    ]
    assert find_contacts(events, frame_ms, {"7", "9"}) == [
        {"f": 5, "p": "7", "b": "L"},
        {"f": 10, "p": "7", "b": "R"},
        {"f": 20, "p": "9", "b": "H"},
    ]
