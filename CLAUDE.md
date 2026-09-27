# Goal Replay

A static web app that replays every 2022 World Cup goal in 3D from real tracking data. Pick a player, pick a goal, and watch a ~21.5-second clip (15 s before the shot, 6.5 s after) with pause, scrub, 0.25x/0.5x/1x speed, and an orbit camera with presets. Full brief: [docs/brief.md](docs/brief.md).

**Status:** Weekends 1–4 done: 166 goals play in the 3D viewer (branch `weekend-2-3d`, not merged). The Sep 27 work below is committed. Next: fix what Zayd reports from watching clips, merge, then Weekend 5 (mobile check, Vercel deploy, README, demo GIF).

## How Zayd works

- He watches clips in `cd web && npm run dev` and reports problems by player and minute. Short, direct answers, no buzzwords.
- After pipeline changes: `python pipeline/build_all.py` then `python -m pytest`. The report's review list should be 0 and problems 0.

## Pipeline order (`build_clip` in `pipeline/cut_clip.py`)

`drop_out_of_play` → `pick_ball_source`, `borrow_gaps` (→ `tracked` flags: the feed really had the ball) → `estimate_gaps` → penalties only: `pin_ball`, `place_players` → `correct_goal_mouth` (with `aim`) → `smooth_jumps` → `find_contacts` → `align_contacts` → `rebuild_dribbles` → `anchor_frames` (dribble frames held) → `apply_physics` (with `touch_heights`) → `physics_shot` (with `retime_shot`) → `straighten_free_flight`.

## Sep 27 changes

- **Ball physics** (`ball_physics.py`, `ball_flight.py`): between touches the ball is a simulated kick (gravity, drag, bounces, rolling), solved to arrive at the next touch at the right time and height.
- **Touches lined up with the tracking** (`align_contacts` in `contacts.py`): PFF event times are often off by ~0.5 s. A touch is kept if the ball is within 1.5 m of the player, else moved within ±0.4 s to where the tracked ball is at his feet, else (if the feed had a gap there) the ball is placed at his feet, else dropped. Shot touches are always kept; touches after the shot are dropped.
- **Dribbles** (`dribble.py`): PFF logs a dribble as one carry. Stretches where one player keeps a low ball get a touch every stride (contacts with `"s": 1`), pushes rolled with physics, the carrier's track smoothed. If the rebuild isn't believable, the stretch is written as `carries` and the viewer holds the ball at his feet.
- **Shot speed** (`retime_shot` in `cut_clip.py`): the tracking loses hard shots and the gap fill glided them in at ~11 m/s. A shot slower than 20 m/s average (headers 11, hands 8) gets an earlier crossing at that speed; the ball in the net plays on from there.
- **Viewer**: `web/src/ballTrack.ts` smooths the ball (fills held coordinates, quadratic fit between touches, cubic interpolation with sharp corners at touches/bounces). Touch pull ±0.12 s, never more than 1.5 m. The ball rolls with its speed on the ground and only spins gently in the air. Cameras (`playCam.ts`): Broadcast = fixed TV wide shot that pans and zooms (default); Follow play = medium height behind the play with a shot cam behind the shooter. Carry fallback in `Replay.tsx`.
- **Penalties** (`penalty.py`): PFF marks them with `gameEvents.setpieceType == "P"` on the shot (16 clips). Until the kick (`goalFrame`, which matches the ball leaving the spot to ~0.1 s) the ball sits on the spot at z 0; everyone but the taker and keeper is moved to the nearest point outside the area and 9.15 m from the spot (+0.25 m); the keeper stands on his line between the posts; the taker is eased onto the ball (0.5 m) over a 2 s run-up. The legal point follows each player frame to frame (a player PFF puts on the spot would otherwise flip around the arc). After the kick everyone blends back over max(0.5 s, move / 5 m/s). No touches, dribbles or carries before the kick. The keeper is whichever GK is within 6 m of the goal line: PFF swaps the teams' labels at Mbappé 117' in the final (10517_6739370: "Messi" at the spot, "Lloris" in goal).
- **Shot placement** (`aim_point` in `goal_mouth.py`): PFF events have no end location in the goal mouth. `shotInitialHeightType` gives the height third (BOTTOMTHIRD, MIDDLETHIRD, TOPTHIRD, G = ground, U = unknown); the crossing height is moved into that band ("aimed"). Left/right can only be set by hand: `"aim": {"y": ..., "z": ...}` in `overrides.json` (Mbappé 80:58 volley: y -3.2, far corner). The review list's 3 m check uses `track_shift` (wide/high/clearance only), not moves to the logged height or a hand-set aim. `physics_shot` flies to the last frame before the line so the ball crosses where the correction put it.
- Ferran Torres 53:41 v Costa Rica is excluded (neither feed has the shot). Messi 107:57 v France has logged players nowhere near the tracked ball; a candidate for exclusion.
- Known side effect: "ball turning with nobody near it" went 127 → 301 after dropping mistimed touches.

## Architecture

- **Pipeline (Python, offline):** find goal events → cut the tracking frames for the clip window (one pass per tracking file) → convert to a standard pitch (105 × 68 m, centered at 0,0) → fill small gaps → goal-mouth correction → write one compact JSON clip per goal plus one goal index JSON. `pipeline/build_all.py` runs everything; `pipeline/cut_clip.py` cuts a single goal.
- **Clip format:** metadata (teams, colors, players) plus frames of `{t, ball x/y/z, player x/y by id}`, rounded to 2 decimals.
- **Viewer (browser):** React + Vite + TypeScript, three.js via react-three-fiber + drei. Interpolates between the two nearest frames on every animation frame.
- No backend, database, or API keys. Hosted statically (Vercel or GitHub Pages).

## Data

- Source: PFF FC (now Gradient Sports) free 2022 World Cup dataset: tracking, events, metadata, and rosters for all 64 matches.
- Raw files go in `data/raw/`. All of `data/` is gitignored; never commit dataset files.

## Data notes

Findings from game 10517 (the final):

- Tracking runs at 29.97 fps (about 255k frames per match).
- The ball has a height (z). Some values are slightly negative, so clamp z to 0 or above.
- Match events to tracking frames with `game_event_id` on the tracking frame, which equals `gameEventId` on the event. Don't match on timestamps.
- Goals are shots with `possessionEvents.shotOutcomeType == "G"`. Penalty-shootout kicks are also labeled period 4, but so are real extra-time goals, so don't filter by period. A shootout kick is any goal whose `eventTime` comes after the last `gameEventType == "END"` event.
- All 22 players are present in every frame, since PFF estimates off-camera players. Only the ball goes missing (about 7% of in-play frames).
- Coordinates are already in meters on a 105 × 68 pitch centered at (0, 0).
- `goalT` (the goal event's tracking frame) is the moment of the shot, not the ball crossing the line. For Di María the ball crosses about 1.3 s later, so clips run 6.5 s past `goalT` (`AFTER_S`).
- After every goal PFF writes an `OUT` event with `outType` `H` or `A` (a goal for the home or away team), followed by the conceding team's kickoff (`setpieceType` `K`). An `OUT` goal marker with no goal shot from the scoring team is treated as an own goal, credited to the conceding team's last player on the ball (`pipeline/goals.py`). Across all 64 matches this finds 3 own goals (Enzo Fernández, Aguerd, Neuer).
- Goal-mouth correction (`pipeline/goal_mouth.py`): PFF loses the ball after most shots, and the tracked path can then miss the goal or stop short of it. After `goalFrame` there are three cases. (1) Goal-line clearance: the ball gets within 1.5 m of the line between the posts, then moves back at least 1 m before it crosses. This is Messi 108', where Koundé cleared it from behind the line and raw `balls` peaks ~0.9 m short. The path is shifted locally in x so its closest point is 0.3 m over the line, blending back to the real path over 0.5 s each side (never before the shot). The clearance is kept and the ball is not put in the net. (2) Crosses the line wide of the posts (|y| > 3.66) or over the bar (z > 2.44): the path is shifted to cross 0.3 m inside the nearest post and under the bar. The shift ramps linearly from 0 at the shot, so nothing jumps. After any crossing the ball eases 2 m into the net and rests on the ground there. (3) Never crosses: if the ball vanishes within 5 m of the goal mouth, it is carried straight in at 15 m/s. Otherwise the path is left as is and the clip gets `"needsReview": true`, with no invented long flights. The old velocity extrapolation from the last ball in the clip is gone; it flew Messi 108' ~45 m into the net in the last 0.3 s. Clips carry `"ballCorrected"` and `"needsReview"`.
- `ballsSmoothed` has no ball during a penalty run-up (ball on the spot) and can badly lag the raw `balls` field. At Messi's 108' goal in the final it sits on Messi's own (wrongly estimated, ~30 m off) position at the shot, while raw `balls` has it on the goal line. Raw `balls` is far more complete in the clip windows.
- Ball source is picked per clip (`pipeline/cut_clip.py`, `choose_ball_source`). Raw `balls` is used by default. The fallback is `ballsSmoothed` when the gap-filled raw ball at `goalFrame` is missing or more than `RAW_BALL_MAX_M` (8 m) from every player on the scorer's team. For an own goal that is the conceding team, whose player is on the ball. In the final that sends only Di María to smoothed: his raw ball is 13.3 m from any Argentina player. The others are 2.0–7.7 m. The report prints this distance for every clip, sorted, to check the threshold once all matches are in. Both feeds get the same gap fill, z clamp and pitch conversion, and goal-mouth correction runs on the chosen path. Clips carry `"ballSource": "smoothed" | "raw"`. `balls` is a list that holds 0 or 1 ball, and both feeds share the same z.
- Overrides (`pipeline/overrides.json`): a map of clip file name to `{"ballSource": "smoothed" | "raw", "note": "..."}`, which wins over the automatic source choice, or to `{"exclude": true, "note": "..."}`, which leaves the goal out of the clips and the index. Use it for clips the 8 m rule gets wrong, and keep the threshold as it is. Every entry needs a note plus a valid `ballSource` or `"exclude": true`, or the pipeline stops. The report marks overridden clips (with the automatic choice), lists overrides and exclusions, and an entry that matches no goal in a loaded match shows up under problems. Excluded goals still count toward the running score in the index. Current entry: `10517_6738451.json` (Mbappé 80:58 volley) is set to smoothed. Its raw ball is 7.7 m from the nearest France player, just under the threshold, and about 8 m from Mbappé at the shot, so it flew off without him.
- Disallowed goals: a goal shot (`shotOutcomeType == "G"`) with no `OUT` goal marker is a goal ruled out by VAR or offside. There are 24 across the tournament. They are left out of the clips and index and listed in the report under "disallowed (excluded)". This brings the total to the official 172.
- A goal can have no shooter: Bruno Fernandes v Uruguay (`3843_6578657`) is a cross (`CR`) marked `G` with `shooterPlayerId` empty. The scorer then falls back to the player on the ball (`gameEvents.playerId` / `playerName`).
- Game 10510 (CRO v BRA) has no extra-time tracking: frames stop at the end of period 2. Neymar 105+1' and Petković 117' are listed as "no tracking" (no clip, still counted in the running score). `read_windows` collects the periods it saw, so the report can tell "tracking ends before the goal's period" apart from "game_event_id on no frame". No goal needs a clock-based fallback yet.
- PFF `week` is the round: 1–3 group matchdays, 4 Round of 16, 5 Quarter-final, 6 Semi-final, 7 Third place, 8 Final. This is confirmed by the match counts, 16/16/16/8/4/2/1/1.
- Review queue: each index entry has a `review` list of reasons, empty if the clip looks fine. A clip is flagged if the ball doesn't go in (`needsReview`), a goal-mouth correction shifted it more than 3 m (`BIG_SHIFT_M`), or the raw source was chosen with the ball 6–8 m from the scoring team (`REVIEW_RAW_M` to `RAW_BALL_MAX_M`). The viewer shows a dot on flagged goals and has a "Needs review" filter. `build_all.py` deletes any clip JSON in `clips/` that isn't in the index.
- `ballsSmoothed` is pinned exactly onto the player on the ball at every event frame. It was 0.0 m from the scorer at `goalFrame` in all six goals in the final, and it runs in straight lines between events. That is why ball-to-shooter distance can't be used to judge the smoothed feed. The player positions are PFF estimates too: at penalty kicks the taker is 2–5 m from a raw ball that sits on the spot.

## Setup

```sh
source .venv/bin/activate   # Python 3.13 venv with kloppy, pandas, matplotlib
```

## Scope rules

- v1 non-goals: animated human models (players are simple shapes with numbers), full-match playback, LLM features, stats dashboards, other tournaments, accounts, or any server.
- Get one goal working well before scaling to all of them.
- Tests: pytest for the pipeline (coordinate conversion, clip cutting, goal detection, index building, goal-mouth correction).
