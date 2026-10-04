import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from relabel import window_votes


def test_a_slot_takes_a_name_only_with_a_clear_majority():
    # 105 x 68 pitch; StatsBomb (60, 40) is the centre spot. Home attacks +x.
    lists = {"home": [("7", 0.0, 0.0), ("9", 20.0, 0.0)], "away": [("3", -20.0, 10.0)]}
    sb = [(3, 1.0, ("home", "11"), [60, 40]), (3, 2.0, ("home", "11"), [60.5, 40]),  # two votes: clear
          (3, 3.0, ("home", "5"), [60 + 20 * 120 / 105, 40]),  # one vote: not enough
          (3, 4.0, ("away", "4"), [60 + 20 * 120 / 105, 40 + 10 * 80 / 68]),  # away attacks -x: flipped
          (3, 5.0, ("away", "4"), [60 + 20 * 120 / 105, 40 + 10 * 80 / 68]),
          (4, 2.0, ("home", "2"), [60, 40]), (4, 2.5, ("home", "2"), [60, 40])]  # other period
    votes = window_votes(sb, 3, [0.0, 2.0, 4.0, 6.0], lambda i: lists, 105, 68)
    assert votes == {("home", "7"): "11", ("away", "3"): "4"}
