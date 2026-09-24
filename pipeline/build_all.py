"""Cut a clip for every goal in every match in data/raw/ and write the goal index.

Usage: python pipeline/build_all.py [--jobs N]

Writes clips/{gameId}_{gameEventId}.json per goal and clips/index.json, then
prints a validation report.
"""

import argparse
import os
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cut_clip import RAW, build_clip, load_match, read_windows, write_clip
from goals import build_index, clip_name, find_goals

CLIPS = Path("clips")
EXPECTED_GOALS = 172  # 2022 World Cup goals, excluding shootouts
BIG_SHIFT_M = 3.0


def game_ids():
    """Games with all four raw files present."""
    ids = []
    for meta_path in sorted((RAW / "metadata").glob("*.json")):
        gid = meta_path.stem
        needed = [RAW / "events" / f"{gid}.json", RAW / "rosters" / f"{gid}.json",
                  RAW / "tracking" / f"{gid}.jsonl.bz2"]
        if all(p.exists() for p in needed):
            ids.append(gid)
    return ids


def process_game(game_id):
    """Cut every goal in one match (one pass over its tracking file)."""
    meta, roster, events = load_match(game_id)
    goals, problems = find_goals(events)
    windows = read_windows(RAW / "tracking" / f"{game_id}.jsonl.bz2", [g["gameEventId"] for g in goals])
    results = []
    for goal in goals:
        if goal["gameEventId"] not in windows:
            problems.append(f"game {game_id}: goal {goal['gameEventId']} ({goal['scorer']}) "
                            "not found in tracking, no clip")
            continue
        frames, goal_index = windows[goal["gameEventId"]]
        clip, stats = build_clip(meta, roster, goal, frames, goal_index)
        write_clip(clip, CLIPS / clip_name(goal))
        results.append((goal, stats))
    return meta, goals, results, problems


def report(matches, results, problems, n_games):
    goals = [g for _, gs in matches for g in gs]
    stats = [s for _, s in results]
    print("\n=== Validation report ===")
    print(f"matches processed: {n_games} (64 in the tournament)")
    weeks = Counter(m.get("week") for m, _ in matches)
    # Expected 16/16/16/8/4/2/1/1 if PFF's week is the round (index "stage" relies on it).
    print("matches per PFF week: " + ", ".join(f"{w}: {n}" for w, n in sorted(weeks.items())))
    own = [g for g in goals if g["ownGoal"]]
    print(f"goals found: {len(goals)} ({len(own)} own goals); expected {EXPECTED_GOALS} for the full tournament")
    if n_games < 64:
        print(f"  -> only {n_games} of 64 matches are in data/raw/, so the total can't match yet")
    elif len(goals) != EXPECTED_GOALS:
        print(f"  -> {len(goals) - EXPECTED_GOALS:+d} vs expected: see problems below")
    for g in own:
        print(f"  own goal: {g['scorer']} (game {g['gameId']}, {g['clock']})")
    print(f"clips written: {len(results)}")

    if stats:
        n_frames = [s["frames"] for s in stats]
        print(f"frames per clip: min {min(n_frames)}, median {statistics.median(n_frames):.0f}, max {max(n_frames)}")
        total = sum(n_frames)
        raw_missing = sum(s["missing_ball"] for s in stats)
        final_missing = sum(s["still_missing_ball"] for s in stats)
        print(f"frames with no ball: {100 * raw_missing / total:.1f}% in raw tracking, "
              f"{100 * final_missing / total:.1f}% after gap fill and goal-mouth correction")

        corrected = [(g, s["correction"]) for g, s in results if s["correction"]["corrected"]]
        by_reason = {}
        for _, c in corrected:
            by_reason[c["reason"]] = by_reason.get(c["reason"], 0) + 1
        reasons = ", ".join(f"{n} {r}" for r, n in sorted(by_reason.items()))
        print(f"goal-mouth correction: {len(corrected)} of {len(results)} clips ({reasons or 'none'})")
        big = [(g, c) for g, c in corrected if c["shift"] > BIG_SHIFT_M]
        print(f"shifted more than {BIG_SHIFT_M:.0f} m (check by eye): {len(big)}")
        for g, c in sorted(big, key=lambda gc: -gc[1]["shift"]):
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {c['reason']}, shift {c['shift']:.1f} m")
        long_extrap = [(g, c) for g, c in corrected if c["extrapolated"] > BIG_SHIFT_M]
        if long_extrap:
            print(f"ball extrapolated more than {BIG_SHIFT_M:.0f} m after it vanished (check by eye): {len(long_extrap)}")
            for g, c in sorted(long_extrap, key=lambda gc: -gc[1]["extrapolated"]):
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {c['extrapolated']:.1f} m")
        no_ball = [g for g, s in results if s["correction"]["reason"] == "no ball"]
        for g in no_ball:
            print(f"  no ball at or before the shot, not corrected: {clip_name(g)} {g['scorer']}")

    print(f"problems: {len(problems)}")
    for p in problems:
        print(f"  {p}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()

    ids = game_ids()
    print(f"{len(ids)} matches in {RAW}")
    matches, results, problems = [], [], []
    with ProcessPoolExecutor(max_workers=min(args.jobs, len(ids) or 1)) as pool:
        for game_id, (meta, goals, game_results, game_problems) in zip(ids, pool.map(process_game, ids)):
            print(f"  {game_id} {meta['homeTeam']['shortName']} v {meta['awayTeam']['shortName']}: "
                  f"{len(goals)} goals")
            matches.append((meta, goals))
            results.extend(game_results)
            problems.extend(game_problems)

    # Only list goals that got a clip.
    written = {r[0]["gameEventId"] for r in results}
    index = [e for e in build_index(matches) if e["gameEventId"] in written]
    write_clip(index, CLIPS / "index.json")
    print(f"wrote {CLIPS / 'index.json'} ({len(index)} goals)")
    report(matches, results, problems, len(ids))


if __name__ == "__main__":
    main()
