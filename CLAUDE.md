# Goal Replay

A static web app that replays every 2022 World Cup goal in 3D from real tracking data. Pick a player, pick a goal, and watch a ~21.5-second clip (15 s before the shot, 6.5 s after) with pause, scrub, 0.25x/0.5x/1x speed, and an orbit camera with presets. Full brief: [docs/brief.md](docs/brief.md).

**Status:** Weekends 1–3 done (one goal plays in the 3D viewer with full controls). Weekend 4 in progress.

## Current phase: Weekend 4 (scale up)

Run the pipeline on all 64 matches (`python pipeline/build_all.py`), which writes `clips/{gameId}_{gameEventId}.json` per goal plus `clips/index.json` and prints a validation report. The viewer loads the index and has a player search and goal list.
**Done when:** any goal in the tournament loads and plays with the ball going in.

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
- After every goal PFF writes an `OUT` event with `outType` `H` or `A` (a goal for the home or away team), followed by the conceding team's kickoff (`setpieceType` `K`). An `OUT` goal marker with no goal shot from the scoring team is treated as an own goal, credited to the conceding team's last player on the ball (`pipeline/goals.py`). The final has no own goals, so this rule hasn't been checked against real own goals yet.
- Goal-mouth correction (`pipeline/goal_mouth.py`): PFF loses the ball after most shots, and `ballsSmoothed` then carries it on in a straight line that can miss the goal. Di María's crosses the line ~1 m wide and then goes null. After `goalFrame`, if the path crosses wide of the posts (|y| > 3.66), over the bar (z > 2.44), or never reaches the line, it is shifted to cross 0.3 m inside the nearest post and under the bar. The shift ramps linearly from 0 at the shot, so nothing jumps. After the crossing the ball eases 2 m into the net and rests on the ground there. Clips carry `"ballCorrected": true/false`.
- `ballsSmoothed` has no ball during a penalty run-up (ball on the spot) and can badly lag the raw `balls` field. At Messi's 108' goal in the final it sits on Messi's own (wrongly estimated, ~30 m off) position at the shot, while raw `balls` has it on the goal line. Raw `balls` is far more complete in the clip windows.

## Setup

```sh
source .venv/bin/activate   # Python 3.13 venv with kloppy, pandas, matplotlib
```

## Scope rules

- v1 non-goals: animated human models (players are simple shapes with numbers), full-match playback, LLM features, stats dashboards, other tournaments, accounts, or any server.
- Get one goal working well before scaling to all of them.
- Tests: pytest for the pipeline (coordinate conversion, clip cutting, goal detection, index building, goal-mouth correction).
