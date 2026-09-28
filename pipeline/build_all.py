"""Cut a clip for every goal in every match in data/raw/ and write the goal index.

Usage: python pipeline/build_all.py [--jobs N]

Writes clips/{gameId}_{gameEventId}.json per goal and clips/index.json, then
prints a validation report.

Shot placement comes from pipeline/shot_placement.json, which
shot_placement.py writes from clips/index.json and the clips. On a fresh
checkout (or after goals are added or removed) run:
    python pipeline/build_all.py
    python pipeline/shot_placement.py
    python pipeline/build_all.py
The report lists clips with no placement.
"""

import argparse
import os
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cut_clip import (BALL_SOURCES, OVERRIDES, RAW, RAW_BALL_MAX_M, SHOT_PLACEMENT, build_clip, load_match,
                      load_overrides, load_shot_placement, read_windows, write_clip)
from goals import build_index, clip_name, find_goals
from restarts import KINDS

CLIPS = Path("clips")
EXPECTED_GOALS = 172  # 2022 World Cup goals, excluding shootouts
BIG_SHIFT_M = 3.0
REVIEW_RAW_M = 6.0  # raw source chosen this close to RAW_BALL_MAX_M: worth a look
FAST_BALL_MPS = 55.0  # faster than any real kick: the ball will zip unnaturally


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


def process_game(game_id, overrides, placements):
    """Cut every goal in one match (one pass over its tracking file).

    Returns (meta, goals, results, problems, skipped); skipped lists
    (goal, kind, reason) for disallowed, excluded and no-tracking goals.
    """
    meta, roster, events = load_match(game_id)
    goals, problems, disallowed = find_goals(events)
    skipped = [(g, "disallowed", "goal shot with no OUT goal marker") for g in disallowed]
    to_cut = []
    for goal in goals:
        override = overrides.get(clip_name(goal)) or {}
        if override.get("exclude"):
            skipped.append((goal, "excluded", override["note"]))
        else:
            to_cut.append(goal)
    periods = set()
    windows = read_windows(RAW / "tracking" / f"{game_id}.jsonl.bz2", [g["gameEventId"] for g in to_cut],
                           periods=periods)
    results = []
    for goal in to_cut:
        if goal["gameEventId"] not in windows:
            last = max((p for p in periods if p is not None), default=None)
            if last is not None and goal["period"] > last:
                reason = f"tracking ends in period {last}, goal is in period {goal['period']}"
            else:
                reason = "game_event_id is on no tracking frame"
            skipped.append((goal, "no tracking", reason))
            continue
        frames, goal_index = windows[goal["gameEventId"]]
        name = clip_name(goal)
        clip, stats = build_clip(meta, roster, goal, frames, goal_index, overrides.get(name), events,
                                 placements.get(name))
        write_clip(clip, CLIPS / clip_name(goal))
        results.append((goal, stats))
    return meta, goals, results, problems, skipped


def review_reasons(stats):
    """Why a clip needs a look by eye (empty if it doesn't).

    A clip marked "reviewed": true in overrides.json stays off the list unless its ball still doesn't go in."""
    c = stats["correction"]
    reasons = []
    if c["needs_review"]:
        reasons.append(f"ball doesn't go in ({c['reason']})")
    if stats.get("rule_breaks"):
        kinds = Counter(k for _, k in stats["rule_breaks"])
        reasons.append("ball changes course with no touch: " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    if (stats.get("override") or {}).get("reviewed"):
        return reasons
    if stats.get("max_ball_speed", 0) > FAST_BALL_MPS:
        reasons.append(f"ball moves at {stats['max_ball_speed']:.0f} m/s")
    if c["reason"] == "synthesized":
        reasons.append(f"shot path synthesized ({c['carried']:.1f} m)")
    if c["track_shift"] > BIG_SHIFT_M:
        reasons.append(f"{c['reason']} correction shifted {c['track_shift']:.1f} m")
    d = stats["raw_distance"]
    if stats["ball_source"] == "raw" and d is not None and REVIEW_RAW_M <= d <= RAW_BALL_MAX_M:
        reasons.append(f"raw ball {d:.1f} m from the scoring team")
    return reasons


def report(matches, results, problems, skipped, n_games):
    goals = [g for _, gs in matches for g in gs]
    stats = [s for _, s in results]
    print("\n=== Validation report ===")
    print(f"matches processed: {n_games} (64 in the tournament)")
    weeks = Counter(m.get("week") for m, _ in matches)
    print("matches per PFF week: " + ", ".join(f"{w}: {n}" for w, n in sorted(weeks.items())))
    own = [g for g in goals if g["ownGoal"]]
    print(f"goals found: {len(goals)} ({len(own)} own goals); expected {EXPECTED_GOALS} for the full tournament")
    if n_games < 64:
        print(f"  -> only {n_games} of 64 matches are in data/raw/, so the total can't match yet")
    elif len(goals) != EXPECTED_GOALS:
        print(f"  -> {len(goals) - EXPECTED_GOALS:+d} vs expected: see problems below")
    else:
        print("  -> matches")
    for g in own:
        print(f"  own goal: {g['scorer']} (game {g['gameId']}, {g['clock']})")
    print(f"clips written: {len(results)}")
    for kind in ("disallowed", "no tracking", "excluded"):
        rows = [(g, reason) for g, k, reason in skipped if k == kind]
        label = {"disallowed": "disallowed (excluded)", "no tracking": "no tracking (no clip)",
                 "excluded": f"excluded in {OVERRIDES.name}"}[kind]
        print(f"{label}: {len(rows)}")
        for g, reason in rows:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']} (period {g['period']})  {reason}")

    if stats:
        n_frames = [s["frames"] for s in stats]
        print(f"frames per clip: min {min(n_frames)}, median {statistics.median(n_frames):.0f}, max {max(n_frames)}")
        total = sum(n_frames)
        raw_missing = sum(s["missing_ball"] for s in stats)
        final_missing = sum(s["still_missing_ball"] for s in stats)
        print(f"frames with no ball: {100 * raw_missing / total:.1f}% in raw tracking, "
              f"{100 * final_missing / total:.1f}% after gap fill and goal-mouth correction")

        print(f"raw ball to nearest scoring-team player at the shot, sorted "
              f"(raw if <= {RAW_BALL_MAX_M:.0f} m, else smoothed):")
        by_distance = sorted(results, key=lambda r: (r[1]["raw_distance"] is None, r[1]["raw_distance"] or 0))
        for g, s in by_distance:
            d = s["raw_distance"]
            cov = ", ".join(f"{src} {100 * s['coverage'][src]:.0f}%" for src in BALL_SOURCES)
            c = s["correction"]
            fix = f", corrected ({c['reason']}, {c['shift']:.1f} m)" if c["corrected"] else ""
            mark = f"  OVERRIDE (auto {s['auto_source']})" if s["override"] else ""
            print(f"  {'missing' if d is None else f'{d:5.1f} m'}  -> {s['ball_source']:8} {clip_name(g)}  "
                  f"{g['scorer']} {g['clock']}  (ball in {cov} of frames){fix}{mark}")
        per_source = Counter(s["ball_source"] for s in stats)
        print("clips per ball source: " + ", ".join(f"{src} {per_source[src]}" for src in BALL_SOURCES))
        overridden = [(g, s) for g, s in results if s["override"]]
        print(f"overridden in {OVERRIDES.name}: {len(overridden)}")
        for g, s in overridden:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {s['auto_source']} -> {s['ball_source']}: "
                  f"{s['override']['note']}")

        corrected = [(g, s["correction"]) for g, s in results if s["correction"]["corrected"]]
        by_reason = {}
        for _, c in corrected:
            by_reason[c["reason"]] = by_reason.get(c["reason"], 0) + 1
        reasons = ", ".join(f"{n} {r}" for r, n in sorted(by_reason.items()))
        print(f"goal-mouth correction: {len(corrected)} of {len(results)} clips ({reasons or 'none'})")
        big = [(g, c) for g, c in corrected if c["track_shift"] > BIG_SHIFT_M]
        print(f"tracking shifted more than {BIG_SHIFT_M:.0f} m (check by eye): {len(big)}")
        for g, c in sorted(big, key=lambda gc: -gc[1]["track_shift"]):
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {c['reason']}, shift {c['track_shift']:.1f} m"
                  + (f" ({c['shift']:.1f} m with the logged height)" if c["shift"] > c["track_shift"] + 0.05 else ""))
        carried = [(g, c) for g, c in corrected if c["carried"] > 0]
        if carried:
            print(f"ball carried in after it vanished near goal: {len(carried)}")
            for g, c in sorted(carried, key=lambda gc: -gc[1]["carried"]):
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {c['carried']:.1f} m")
        aim_sources = Counter(s["aim_source"] or "none" for _, s in results)
        print("crossing aimed from: " + ", ".join(f"{k} {v}" for k, v in sorted(aim_sources.items())))
        for g, s in results:
            if s["aim_source"] == "override":
                print(f"  set in {OVERRIDES.name}: {clip_name(g)}  {g['scorer']} {g['clock']}  {s['aim']}, "
                      f"moved {s['correction']['shift']:.1f} m")
        unplaced = [g for g, s in results if s["aim_source"] != "statsbomb"]
        print(f"no StatsBomb placement in {SHOT_PLACEMENT.name}: {len(unplaced)}"
              + (" (own goals have none; run shot_placement.py if goals changed)" if unplaced else ""))
        for g in unplaced:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}{'  (own goal)' if g['ownGoal'] else ''}")
        pens = [(g, s) for g, s in results if g.get("penalty")]
        print(f"penalties (ball on the spot, players set up legally until the kick): {len(pens)}")
        for g, s in pens:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  largest player move {s['penalty_moved']:.1f} m")
        est = [(g, s["estimated_frames"]) for g, s in results if s.get("estimated_frames")]
        total_frames = sum(s["frames"] for _, s in results)
        print(f"ball estimated where the tracking lost it: {len(est)} clips, "
              f"{100 * sum(n for _, n in est) / total_frames:.1f}% of all frames")
        kb = sum(s["kinks"][0] for _, s in results)
        ka = sum(s["kinks"][1] for _, s in results)
        print(f"ball turning with nobody near it (before the shot): {kb} -> {ka} "
              f"({sum(s['simulated'] for _, s in results)} free-flight stretches simulated with ball physics, "
              f"{sum(s['straightened'] for _, s in results)} straightened)")
        fixes = {k: sum(s["contact_fixes"][k] for _, s in results) for k in results[0][1]["contact_fixes"]} if results else {}
        print("logged touches vs tracking: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in fixes.items()))
        print(f"dribbles rebuilt as pushes: {sum(s.get('dribbles', 0) for _, s in results)}, "
              f"left to the viewer as carries: {sum(s.get('carries', 0) for _, s in results)}")
        before = Counter(k for _, s in results for _, k in s["rule_before"])
        after = Counter(k for _, s in results for _, k in s["rule_breaks"])
        fixes = Counter()
        for _, s in results:
            fixes.update(s["rule_counts"])
        print(f"ball changing course with no touch (turn, sudden speed change, lift, touch out of reach), "
              f"before the shot: {sum(before.values())} -> {sum(after.values())} "
              f"({', '.join(f'{k} {n}' for k, n in sorted(before.items()))} before the fixes)")
        print(f"  touches added where a player was within reach: {fixes['touches_added']}, stretches flown as one "
              f"kick between touches: {fixes['joined']}, balls moved to the toucher: {fixes['moved_to_foot']}, "
              f"straight-line flights (no kick fits): {fixes['straight']}")
        for g, s in results:
            if s["rule_breaks"]:
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {s['rule_breaks']}")
        shots = [s["shot"] for _, s in results]
        moved = sorted(x["at_foot_moved"] for x in shots)
        print(f"shots: ball moved to the shooter's foot at the kick: median {statistics.median(moved):.1f} m, "
              f"max {moved[-1]:.1f} m; shooter moved onto the ball (tracked > 3 m from it): "
              f"{sum(1 for x in shots if x['shooter_moved'])} clips, most "
              f"{max(x['shooter_moved'] for x in shots):.1f} m; deflections kept: {sum(len(x['deflected']) for x in shots)}")
        restarts = [(g, r) for g, s in results for r in s["restarts"]]
        kinds = Counter(r["type"] for _, r in restarts)
        print(f"restarts before the shot (ball still at the spot / in the thrower's hands until taken): "
              f"{len(restarts)} in {len({clip_name(g) for g, _ in restarts})} clips ("
              + ", ".join(f"{n} {KINDS[k]}" for k, n in sorted(kinds.items())) + "); "
              f"ball went out in the clip: {sum(r['out'] is not None for _, r in restarts)}, "
              f"hidden while out of play: {sum(bool(r['hidden']) for _, r in restarts)}")
        for g, r in restarts:
            if r["out"] is not None:
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {KINDS[r['type']]}: out at frame {r['out']}, "
                      f"taken at frame {r['f']}")
        swapped = [(g, s) for g, s in results if s["swapped"]]
        if swapped:
            ok, n = (sum(s["toucher_near"][k] for _, s in swapped) for k in (0, 1))
            print(f"extra-time label swap, slots named from StatsBomb: {len(swapped)} clips, "
                  f"logged toucher within 3 m of the ball {ok}/{n}")
            for g, s in swapped:
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {s['votes']} slots voted, "
                      f"toucher within 3 m {s['toucher_near'][0]}/{s['toucher_near'][1]}")
        ok, n = (sum(s["toucher_near"][k] for _, s in results if not s["swapped"]) for k in (0, 1))
        print(f"logged toucher within 3 m of the ball, other clips: {ok}/{n} ({100 * ok / max(n, 1):.0f}%)")
        review = [(g, review_reasons(s)) for g, s in results if review_reasons(s)]
        print(f"review list (needsReview, shift > {BIG_SHIFT_M:.0f} m, or raw at "
              f"{REVIEW_RAW_M:.0f}-{RAW_BALL_MAX_M:.0f} m): {len(review)}")
        for g, reasons in review:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {'; '.join(reasons)}")

    print(f"problems: {len(problems)}")
    for p in problems:
        print(f"  {p}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()

    ids = game_ids()
    overrides = load_overrides()
    placements = load_shot_placement()
    print(f"{len(ids)} matches in {RAW}")
    matches, results, problems, skipped = [], [], [], []
    with ProcessPoolExecutor(max_workers=min(args.jobs, len(ids) or 1)) as pool:
        per_game = pool.map(process_game, ids, [overrides] * len(ids), [placements] * len(ids))
        for game_id, (meta, goals, game_results, game_problems, game_skipped) in zip(ids, per_game):
            print(f"  {game_id} {meta['homeTeam']['shortName']} v {meta['awayTeam']['shortName']}: "
                  f"{len(goals)} goals")
            matches.append((meta, goals))
            results.extend(game_results)
            problems.extend(game_problems)
            skipped.extend(game_skipped)

    names = {clip_name(g) for _, gs in matches for g in gs}
    loaded = set(ids)
    for name in overrides:
        if name not in names and name.split("_")[0] in loaded:
            problems.append(f"{OVERRIDES.name}: {name} matches no goal")

    # Only list goals that got a clip. Excluded and no-tracking goals still
    # count toward the running score.
    reviews = {g["gameEventId"]: review_reasons(s) for g, s in results}
    index = [e for e in build_index(matches) if e["gameEventId"] in reviews]
    for e in index:
        e["review"] = reviews[e["gameEventId"]]
    write_clip(index, CLIPS / "index.json")
    print(f"wrote {CLIPS / 'index.json'} ({len(index)} goals)")
    keep = {e["clip"] for e in index} | {"index.json"}
    stale = sorted(p for p in CLIPS.glob("*.json") if p.name not in keep)
    for p in stale:
        p.unlink()
    print(f"deleted {len(stale)} clip files not in the index" + (": " + ", ".join(p.name for p in stale) if stale else ""))
    report(matches, results, problems, skipped, len(ids))


if __name__ == "__main__":
    main()
