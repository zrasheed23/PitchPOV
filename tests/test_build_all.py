import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from build_all import review_reasons


def stats(source="raw", distance=2.0, needs_review=False, reason=None, shift=0.0, carried=0.0, track_shift=None):
    return {
        "ball_source": source,
        "raw_distance": distance,
        "correction": {"needs_review": needs_review, "reason": reason, "shift": shift, "carried": carried,
                       "track_shift": shift if track_shift is None else track_shift},
    }


def test_clean_clip_needs_no_review():
    assert review_reasons(stats()) == []
    assert review_reasons(stats(distance=5.9, reason="wide", shift=3.0)) == []
    assert review_reasons(stats(source="smoothed", distance=7.0)) == []  # e.g. an override
    # Moving the ball to the logged height or a hand-set aim is not a tracking fix.
    assert review_reasons(stats(reason="aimed", shift=3.4, track_shift=0.0)) == []
    assert review_reasons(stats(reason="wide", shift=3.1, track_shift=2.9)) == []


def test_review_reasons():
    assert review_reasons(stats(needs_review=True, reason="not near goal")) == ["ball doesn't go in (not near goal)"]
    assert review_reasons(stats(reason="wide", shift=12.9)) == ["wide correction shifted 12.9 m"]
    assert review_reasons(stats(distance=7.7)) == ["raw ball 7.7 m from the scoring team"]
    assert len(review_reasons(stats(distance=6.0, needs_review=True, reason="not near goal"))) == 2
    assert review_reasons(stats(reason="synthesized", carried=9.5)) == ["shot path synthesized (9.5 m)"]
