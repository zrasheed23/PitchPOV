"""Regression tests against outcomes confirmed by watching the clips.

tests/fixtures/confirmed_by_eye.json lists them; extend it as clips are
checked. These read the built clips in clips/ (run build_all.py first) and
are skipped if there are none.
"""
import json
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


@pytest.mark.parametrize("name", sorted(CONFIRMED["poses"]))
def test_confirmed_poses(name):
    want = CONFIRMED["poses"][name]
    clip = json.loads((CLIPS / name).read_text())
    assert clip_pose(clip) == want["pose"], f"{want['who']}: expected {want['pose']}"


def test_confirmed_pose_counts():
    counts = Counter(clip_pose(json.loads(p.read_text())) for p in CLIPS.glob("*_*.json"))
    for pose, n in CONFIRMED["pose_counts"].items():
        assert counts[pose] == n, f"{pose}: {counts[pose]} clips, expected {n}"
