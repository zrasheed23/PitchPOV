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
import json
import os
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from cut_clip import (BALL_SOURCES, OVERRIDES, RAW, RAW_BALL_MAX_M, SHOT_PLACEMENT, build_clip, load_match,
                      load_overrides, load_shot_placement, read_windows, write_clip)
from goals import build_index, clip_name, find_goals
from accuracy import CHECKS
from quality import CHECKS as QUALITY_CHECKS
from restarts import KINDS

CLIPS = Path("clips")
QUALITY_REPORT = CLIPS / "quality_report.md"
QUALITY_JSON = CLIPS / "quality.json"  # every finding per clip, for scripts (no "_": not a clip name)
TOP_WORST = 15
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


def clip_pose(clip):
    """How a built clip's goal is struck: its shotPose, or (older clips) the
    "v" on the scorer's touch at the kick; "none" if there's none."""
    if "shotPose" in clip:
        return clip["shotPose"]
    kick = clip.get("kickFrame", clip["goalFrame"])
    shot = next((c for c in clip.get("contacts", []) if c["f"] == kick and c["p"] == clip["scorerId"]), {})
    v = shot.get("v")
    return v if isinstance(v, str) else "none"


def previous_poses(folder=CLIPS):
    """{clip file name: pose} from the clips already on disk (the last build)."""
    out = {}
    for path in folder.glob("*.json"):
        if path.name in ("index.json", QUALITY_JSON.name):
            continue
        try:
            out[path.name] = clip_pose(json.loads(path.read_text()))
        except (ValueError, KeyError):
            continue
    return out


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
    flagged = [c for c, found in (stats.get("accuracy") or {}).items() if found]
    if flagged:
        reasons.append("accuracy: " + ", ".join(flagged))
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


def quality_lines(results):
    """The quality report (quality.py) as lines: findings per check, how many
    clips pass everything, and every clip ranked worst first with its reasons."""
    rows = sorted(results, key=lambda gs: -gs[1]["quality"]["score"])
    passing = [g for g, s in rows if not any(s["quality"]["checks"].values())]
    lines = ["# Clip quality report", "",
             "Every clip checked against measured data (StatsBomb events, freeze frames, end locations) and "
             "physics (pipeline/quality.py). Score: each failing check's worst severity (1 = just over its "
             "threshold, capped at 3), weighted; higher is worse.", "",
             f"Clips: {len(rows)}; passing every check: {len(passing)}", "",
             "| check | clips | findings |", "|---|---:|---:|"]
    for check in QUALITY_CHECKS:
        hits = [s["quality"]["checks"][check] for _, s in rows if s["quality"]["checks"][check]]
        lines.append(f"| {check} | {len(hits)} | {sum(len(h) for h in hits)} |")
    freeze = [s["quality"]["freeze"] for _, s in rows if s["quality"]["freeze"]]
    if freeze:
        meds = sorted(f["median"] for f in freeze)
        lines += ["", f"Freeze frame vs tracking at the shot ({len(freeze)} clips): median of clip medians "
                      f"{statistics.median(meds):.2f} m, worst clip median {meds[-1]:.2f} m, worst single player "
                      f"{max(f['max'] for f in freeze):.1f} m."]
    lines += ["", "## Ranked (worst first)", "", "| # | clip | goal | score | reasons |", "|---:|---|---|---:|---|"]
    for n, (g, s) in enumerate(rows, 1):
        q = s["quality"]
        why = "; ".join(f"{c}: {f[0]}" + (f" (+{len(f) - 1})" if len(f) > 1 else "")
                        for c, f in sorted(q["checks"].items(), key=lambda cf: -q["severity"].get(cf[0], 0)) if f)
        lines.append(f"| {n} | {clip_name(g)} | {g['scorer']} {g['clock']} | {q['score']:.1f} | {why or 'passes'} |")
    return lines


def report(matches, results, problems, skipped, n_games, last_poses=None):
    last_poses = last_poses or {}
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
        kmoves = sorted(((m, name, g) for g, s in results for name, m in s["keeper_moves"].items()),
                        key=lambda x: -x[0])
        print(f"keepers corrected where PFF's track is implausible (out of his area, outside the posts, off the "
              f"ball-goal line): {sum(1 for m, _, _ in kmoves if m > 0.05)} of {len(kmoves)} keeper tracks, "
              f"{sum(1 for m, _, _ in kmoves if m > 5)} by more than 5 m; biggest:")
        for m, name, g in kmoves[:6]:
            print(f"  {m:4.1f} m  {name}  in {clip_name(g)}  {g['scorer']} {g['clock']}")
        gaps = sorted(((s["freeze_gap"], g) for g, s in results if s["freeze_gap"] is not None), key=lambda x: -x[0])
        print(f"keeper eased onto StatsBomb's freeze-frame spot at the shot: {len(gaps)} clips; PFF had him "
              f"{statistics.median(x for x, _ in gaps):.1f} m away (median), per clip:")
        print("  " + "; ".join(f"{g['scorer'].split()[-1]} {g['clock']} {x:.1f}" for x, g in gaps))
        dives = Counter((s["dive"] or {}).get("kind", "none") for _, s in results)
        starts = [s["dive"]["start_s"] for _, s in results if (s["dive"] or {}).get("kind") == "dive"]
        if starts:
            print(f"dives take off {min(starts):.2f} s (min) / {statistics.median(starts):.2f} s (median) after the kick")
        print("keeper at the shot: " + ", ".join(f"{k} {n}" for k, n in sorted(dives.items()))
              + f"; dives that get to the ball: {sum(1 for _, s in results if (s['dive'] or {}).get('kind') == 'dive' and s['dive']['reached'])}")
        changed = [(g, last_poses.get(clip_name(g)), s["shot_pose"]["pose"] or "none") for g, s in results
                   if clip_name(g) in last_poses and last_poses[clip_name(g)] != (s["shot_pose"]["pose"] or "none")]
        print(f"pose changes since the last build ({len(last_poses)} clips on disk before): {len(changed)}"
              + ("  <- check these by eye" if changed else ""))
        for g, old_pose, new_pose in changed:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {old_pose} -> {new_pose}")
        poses = Counter(s["shot_pose"]["pose"] or "none" for _, s in results)
        print("how the goal is struck (StatsBomb technique): " + ", ".join(f"{k} {n}" for k, n in sorted(poses.items())))
        for g, s in results:
            sp = s["shot_pose"]
            if sp["pose"] in ("scissor", "bicycle") or sp["technique"] == "Volley":
                ang = "?" if sp["angle"] is None else f"{sp['angle']:.0f}"
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {sp['technique']} -> {sp['pose']}: "
                      f"measured ball {'none' if sp['z'] is None else f"{sp['z']:.2f} m"}, "
                      f"facing {ang} deg from the shot")
        lines = [(g, s["line_change"]) for g, s in results if s["line_change"] and not s["cleared"]]
        free = [(g, lc) for g, lc in lines if not lc[2]]
        print(f"ball across the goal line (0.07 s either side, on the ground plane; the net simulated from the "
              f"crossing): {len(lines)} clips, {len(lines) - len(free)} caught by the netting straight away; the rest turn at most "
              f"{max((lc[0] for _, lc in free), default=0):.1f} deg and change speed "
              f"{100 * min((lc[1] for _, lc in free), default=0):+.0f}% to {100 * max((lc[1] for _, lc in free), default=0):+.0f}%")
        for g, lc in sorted(free, key=lambda x: -x[1][0])[:3]:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {lc[0]:.1f} deg, speed {100 * lc[1]:+.0f}%")
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
        sbc = [(g, s["sb_counts"]) for g, s in results]
        print(f"StatsBomb touches merged: {sum(c['statsbomb'] for _, c in sbc)} "
              f"({sum(c['added'] for _, c in sbc)} actions PFF lacked, in {sum(1 for _, c in sbc if c['added'])} clips; "
              f"{sum(c['pff_replaced'] for _, c in sbc)} PFF touches replaced; ball re-drawn through "
              f"{sum(c['placed'] for _, c in sbc)} StatsBomb spots); most changed:")
        for g, c in sorted(sbc, key=lambda gc: -(gc[1]["added"] + gc[1]["placed"]))[:6]:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {c['added']} added, {c['placed']} re-drawn")
        def top(rows, key, label, n=5):
            for g, s in sorted(rows, key=lambda gs: -key(gs[1]))[:n]:
                print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {label(s)}")

        met = [(g, s) for g, s in results if s["met"]]
        print(f"touches meeting the player: {sum(len(s['met']) for _, s in met)} players moved onto a well-supported "
              f"ball in {len(met)} clips (max {max((m for _, s in met for _, _, m in s['met']), default=0):.1f} m, "
              f"never over 5 m); {sum(len(s['too_far']) for _, s in results)} left flagged (more than 5 m off with "
              f"the tracked ball backing StatsBomb, no teammate there); most changed:")
        top(met, lambda s: max(m for _, _, m in s["met"]),
            lambda s: ", ".join(f"{n} {m:.1f} m" for n, _, m in sorted(s["met"], key=lambda x: -x[2])[:2]))
        moved_kick = [(g, s) for g, s in results if abs(s["kick_shift"]) > 0.3]
        print(f"shot frame from StatsBomb's shot location (or the tracking), more than 0.3 s from PFF's shot event: "
              f"{len(moved_kick)} clips; most:")
        top(moved_kick, lambda s: abs(s["kick_shift"]), lambda s: f"{s['kick_shift']:+.2f} s")
        shot_moved = [(g, s) for g, s in results if s["shot_move"]]
        print(f"shooter moved onto StatsBomb's shot spot (at most 5 m): {len(shot_moved)} clips; most:")
        top(shot_moved, lambda s: s["shot_move"], lambda s: f"{s['shot_move']:.1f} m")
        swapped_ids = [(g, s) for g, s in results if s["swaps"]]
        print(f"PFF identity mix-ups swapped (teammate at a well-supported touch, StatsBomb fits better): "
              f"{sum(len(s['swaps']) for _, s in swapped_ids)} in {len(swapped_ids)} clips; longest:")
        top(swapped_ids, lambda s: max(b - a for _, _, a, b in s["swaps"]),
            lambda s: "; ".join(f"{x} <-> {y} frames {a}-{b}" for x, y, a, b in s["swaps"][:2]))
        untracked = [(g, s) for g, s in results if s.get("shot_reach") is not None and s["shot_reach"] > 2.0
                     and s["shot_reach"] != float("inf")]
        print(f"shots the tracked ball never reaches (> 2 m from StatsBomb's shot spot; taken there, the last action "
              f"re-flown): {len(untracked)} clips; farthest:")
        top(untracked, lambda s: s["shot_reach"], lambda s: f"tracked ball {s['shot_reach']:.1f} m from the spot")
        beaten_src = [(g, s) for g, s in results if s.get("override_source_beaten")]
        if beaten_src:
            print(f"ballSource overrides overruled by StatsBomb's shot spot: "
                  + ", ".join(f"{clip_name(g)} -> {s['override_source_beaten']}" for g, s in beaten_src))
        ts = [(g, s) for g, s in results if s["three_sixty"]["frames"]]
        print(f"StatsBomb 360: {sum(s['three_sixty']['frames'] for _, s in ts)} frames in {len(ts)} clips; "
              f"fit to the tracking (median per clip, m): "
              f"{statistics.median(s['three_sixty']['cost'][0] for _, s in ts if s['three_sixty']['cost'][0]):.2f} -> "
              f"{statistics.median(s['three_sixty']['cost'][1] for _, s in ts if s['three_sixty']['cost'][1]):.2f}")
        moved = [(g, s) for g, s in results if s["three_sixty"]["moved"]]
        n_moved = sum(len(s["three_sixty"]["moved"]) for _, s in moved)
        big = sum(1 for _, s in moved for m in s["three_sixty"]["moved"].values() if m > 5)
        print(f"  tracks corrected onto 360 spots: {n_moved} in {len(moved)} clips ({big} by more than 5 m); most:")
        top(moved, lambda s: max(s["three_sixty"]["moved"].values()),
            lambda s: ", ".join(f"{n} {m:.1f} m" for n, m in sorted(s["three_sixty"]["moved"].items(),
                                                                       key=lambda x: -x[1])[:3]))
        sw = [(g, s) for g, s in results if s["three_sixty"]["swaps"]]
        across = sum(1 for _, s in sw for *_, a in s["three_sixty"]["swaps"] if a)
        print(f"  labels swapped from 360 (named player's spot on another track): "
              f"{sum(len(s['three_sixty']['swaps']) for _, s in sw)} in {len(sw)} clips ({across} across teams):")
        top(sw, lambda s: len(s["three_sixty"]["swaps"]),
            lambda s: "; ".join(f"{x} <-> {y} {a}-{b}" for x, y, a, b, _ in s["three_sixty"]["swaps"][:3]), n=8)
        smooth = [(g, s) for g, s in results if s["accel_smoothed"]]
        print(f"corrections smoothed so no one accelerates harder than 10 m/s² (7.5 after the goal) where PFF's own "
              f"track doesn't: {sum(len(s['accel_smoothed']) for _, s in smooth)} players in {len(smooth)} clips; most:")
        top(smooth, lambda s: max(s["accel_smoothed"].values()),
            lambda s: ", ".join(f"{n} {m:.1f} m" for n, m in sorted(s["accel_smoothed"].items(), key=lambda x: -x[1])[:2]))
        nudged = [(g, s) for g, s in results if s["nudges"]]
        print(f"players nudged so the ball doesn't pass through them: {sum(len(s['nudges']) for _, s in nudged)} in "
              f"{len(nudged)} clips; most:")
        top(nudged, lambda s: max(m for _, _, m in s["nudges"]),
            lambda s: f"{max(m for _, _, m in s['nudges']):.2f} m")
        beaten = [(g, s) for g, s in results if (s["dive"] or {}).get("shifted")]
        print(f"keeper stands aside so a close shot beats him (at most 1 m from StatsBomb's spot): {len(beaten)} clips; "
              f"through the legs: {sum(1 for _, s in results if (s['dive'] or {}).get('through') == 'legs')}; most:")
        top(beaten, lambda s: s["dive"]["shifted"], lambda s: f"{s['dive']['shifted']:.2f} m")
        sped = [(g, s) for g, s in results if s["sped"]]
        print(f"players held to a 9.5 m/s sprint: {sum(len(s['sped']) for _, s in sped)} tracks in {len(sped)} clips; most:")
        top(sped, lambda s: max(s["sped"].values()),
            lambda s: ", ".join(f"{n} {m:.1f} m" for n, m in sorted(s["sped"].items(), key=lambda x: -x[1])[:2]))
        print("accuracy report (clips flagged / findings per check):")
        for check in CHECKS:
            hits = [(g, s["accuracy"][check]) for g, s in results if s["accuracy"][check]]
            print(f"  {check:18} {len(hits):3} clips, {sum(len(f) for _, f in hits):4} findings")
        flagged = [(g, s) for g, s in results if any(s["accuracy"].values())]
        print(f"  clips flagged by any check: {len(flagged)} of {len(results)}")
        for g, s in flagged:
            print(f"    {clip_name(g)}  {g['scorer']} {g['clock']}: "
                  + "; ".join(f"{c}: {f[0]}" + (f" (+{len(f) - 1})" if len(f) > 1 else "")
                              for c, f in s["accuracy"].items() if f))
        review = [(g, review_reasons(s)) for g, s in results if review_reasons(s)]
        print(f"review list (needsReview, shift > {BIG_SHIFT_M:.0f} m, or raw at "
              f"{REVIEW_RAW_M:.0f}-{RAW_BALL_MAX_M:.0f} m): {len(review)}")
        for g, reasons in review:
            print(f"  {clip_name(g)}  {g['scorer']} {g['clock']}  {'; '.join(reasons)}")

    if results:
        lines = quality_lines(results)
        QUALITY_REPORT.write_text("\n".join(lines) + "\n")
        write_clip({clip_name(g): s["quality"] for g, s in results}, QUALITY_JSON)
        rows = sorted(results, key=lambda gs: -gs[1]["quality"]["score"])
        print(f"quality report ({QUALITY_REPORT}): clips / findings per check:")
        for check in QUALITY_CHECKS:
            hits = [s["quality"]["checks"][check] for _, s in results if s["quality"]["checks"][check]]
            print(f"  {check:18} {len(hits):3} clips, {sum(len(h) for h in hits):4} findings")
        print(f"  passing every check: {sum(1 for _, s in results if not any(s['quality']['checks'].values()))} "
              f"of {len(results)}; worst {TOP_WORST}:")
        for g, s in rows[:TOP_WORST]:
            q = s["quality"]
            top = sorted((c for c, f in q["checks"].items() if f), key=lambda c: -q["severity"].get(c, 0))[:3]
            print(f"    {q['score']:5.1f}  {clip_name(g)}  {g['scorer']} {g['clock']}: "
                  + "; ".join(f"{c}: {q['checks'][c][0]}" for c in top))

    print(f"problems: {len(problems)}")
    for p in problems:
        print(f"  {p}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()

    ids = game_ids()
    last_poses = previous_poses()  # to report pose changes against the last build
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
    keep = {e["clip"] for e in index} | {"index.json", QUALITY_JSON.name}
    stale = sorted(p for p in CLIPS.glob("*.json") if p.name not in keep)
    for p in stale:
        p.unlink()
    print(f"deleted {len(stale)} clip files not in the index" + (": " + ", ".join(p.name for p in stale) if stale else ""))
    report(matches, results, problems, skipped, len(ids), last_poses)


if __name__ == "__main__":
    main()
