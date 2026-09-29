"""Regression tests against outcomes confirmed by watching the clips.

tests/fixtures/confirmed_by_eye.json lists them; extend it as clips are
checked. An entry with "xfail" is a known failure (strict: the test fails
once it passes, so the entry gets flipped). These read the built clips in
clips/ (run build_all.py first) and are skipped if there are none.
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

from build_all import clip_pose

CLIPS = ROOT / "clips"
CONFIRMED = json.loads((Path(__file__).parent / "fixtures" / "confirmed_by_eye.json").read_text())
built = CLIPS.exists() and any(CLIPS.glob("*_*.json"))
pytestmark = pytest.mark.skipif(not built, reason="no built clips (run pipeline/build_all.py)")
POST_Y, LINE_X = 3.66, 52.5


def cases(section):
    return [pytest.param(name, marks=pytest.mark.xfail(reason=want["xfail"], strict=True)) if "xfail" in want
            else name for name, want in sorted(CONFIRMED[section].items())]


def load(name):
    return json.loads((CLIPS / name).read_text())


@pytest.mark.parametrize("name", cases("poses"))
def test_confirmed_poses(name):
    want = CONFIRMED["poses"][name]
    assert clip_pose(load(name)) == want["pose"], f"{want['who']}: expected {want['pose']}"


def test_confirmed_pose_counts():
    counts = Counter(clip_pose(json.loads(p.read_text())) for p in CLIPS.glob("*_*.json"))
    for pose, n in CONFIRMED["pose_counts"].items():
        assert counts[pose] == n, f"{pose}: {counts[pose]} clips, expected {n}"


def touch_sequence(clip):
    """Who touches the ball up to the kick, in order, a player's touches in a row counted once."""
    names = {p["id"]: p["name"] for p in clip["players"]}
    kick = clip.get("kickFrame", clip["goalFrame"])
    out = []
    for c in clip["contacts"]:
        if c["f"] <= kick and (not out or out[-1] != names[c["p"]]):
            out.append(names[c["p"]])
    return out


@pytest.mark.parametrize("name", cases("sequences"))
def test_confirmed_sequences(name):
    want = CONFIRMED["sequences"][name]
    clip = load(name)
    assert touch_sequence(clip) == want["touches"], want["who"]
    if "last_parts" in want:  # body parts of the last touches up to the kick, in order
        kick = clip.get("kickFrame", clip["goalFrame"])
        parts = [c.get("b") for c in clip["contacts"] if c["f"] <= kick][-len(want["last_parts"]):]
        assert parts == want["last_parts"], f"{want['who']}: body parts {parts}"
    if "shot_part" in want:
        kick = clip.get("kickFrame", clip["goalFrame"])
        shot = next(c for c in clip["contacts"] if c["f"] == kick and c["p"] == clip["scorerId"])
        assert shot.get("b") == want["shot_part"], f"{want['who']}: shot with {shot.get('b')}"


@pytest.mark.parametrize("name", cases("shots"))
def test_confirmed_shots(name):
    want = CONFIRMED["shots"][name]
    clip = load(name)
    kick = clip.get("kickFrame", clip["goalFrame"])
    ball, shooter = clip["frames"][kick]["b"], clip["frames"][kick]["p"][clip["scorerId"]]
    assert math.dist(ball[:2], shooter) <= want["shooter_m"], f"{want['who']}: shooter not at the ball"
    if "post" in want:
        side = 1 if ball[0] > 0 else -1  # the goal he attacks
        right = -side if want["post"] == "right" else side  # attacking +x, his right is -y
        post = (side * LINE_X, right * POST_Y)
        assert math.dist(ball[:2], post) <= want["post_m"], \
            f"{want['who']}: kicked {math.dist(ball[:2], post):.1f} m from the {want['post']} post"


def at_kick(clip):
    kick = clip.get("kickFrame", clip["goalFrame"])
    return kick, clip["frames"][kick]


@pytest.mark.parametrize("name", cases("shooters"))
def test_confirmed_shooters(name):
    want = CONFIRMED["shooters"][name]
    clip = load(name)
    names = {p["id"]: p["name"] for p in clip["players"]}
    kick, f = at_kick(clip)
    kicker = [names[c["p"]] for c in clip["contacts"] if c["f"] == kick]
    nearest = min(f["p"], key=lambda q: math.dist(f["p"][q], f["b"][:2]))
    assert kicker == [want["shooter"]], f"{want['who']}: kicked by {kicker}"
    assert names[nearest] == want["shooter"], f"{want['who']}: {names[nearest]} is at the ball"


@pytest.mark.parametrize("name", cases("shot_spots"))
def test_confirmed_shot_spots(name):
    want = CONFIRMED["shot_spots"][name]
    clip = load(name)
    _, f = at_kick(clip)
    assert clip.get("shotSpot"), f"{want['who']}: no StatsBomb shot location"
    d = math.dist(f["b"][:2], clip["shotSpot"])
    assert d <= want["within_m"], f"{want['who']}: kicked {d:.1f} m from StatsBomb's shot location"
    assert math.dist(f["p"][clip["scorerId"]], clip["shotSpot"]) <= want["within_m"] + 0.5


def test_every_shot_is_the_scorers_from_statsbombs_spot():
    """Every clip: the credited scorer kicks it, at the ball, within 2 m of
    StatsBomb's shot location; or the clip is on the review list for it."""
    index = {e["clip"]: e for e in json.loads((CLIPS / "index.json").read_text())}
    bad = []
    for name, entry in sorted(index.items()):
        clip = load(name)
        kick, f = at_kick(clip)
        kicker = {c["p"] for c in clip["contacts"] if c["f"] == kick}
        scorer = f["p"].get(clip["scorerId"])
        ok = kicker == {clip["scorerId"]} and scorer is not None and f["b"] and math.dist(f["b"][:2], scorer) <= 1.2
        if clip.get("shotSpot") and f["b"]:
            ok = ok and math.dist(f["b"][:2], clip["shotSpot"]) <= 2.0
        flagged = any("shooter" in r or "shot spot" in r for r in entry["review"])
        if not ok and not flagged:
            bad.append(f"{entry['scorer']} {entry['clock']} ({name})")
    assert not bad, f"shot not the scorer's at StatsBomb's spot, and not flagged: {bad}"
