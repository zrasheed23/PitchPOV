# Goal Replay

A static web app that replays every 2022 World Cup goal in 3D from real tracking data. Pick a player, pick a goal, and watch a ~20-second clip (15 s before the goal, 5 s after) with pause, scrub, 0.25x/0.5x/1x speed, and an orbit camera with presets. Full brief: [docs/brief.md](docs/brief.md).

**Status:** Weekend 1 done. The Di María clip (game 10517, 35:21) is at `clips/10517_dimaria.json`.

## Current phase: Weekend 1 (data)

Get dataset access, load the final (Argentina vs France) with kloppy, find Messi's first goal, cut 20 seconds, and plot a few frames in matplotlib to sanity-check positions.
**Done when:** clip JSON for one goal exists and its frames look right on a 2D plot.

Things to check once the data is in hand: usage terms (public app allowed? how to credit), frame rate, whether the ball has a height (z) value, how often players drop out off-camera, and the total goal count (~170).

## Architecture

- **Pipeline (Python, offline):** find goal events → cut the tracking frames for the clip window → convert to a standard pitch (105 × 68 m, centered at 0,0) → fill small gaps → write one compact JSON clip per goal plus one goal index JSON.
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

## Setup

```sh
source .venv/bin/activate   # Python 3.13 venv with kloppy, pandas, matplotlib
```

## Scope rules

- v1 non-goals: animated human models (players are simple shapes with numbers), full-match playback, LLM features, stats dashboards, other tournaments, accounts, or any server.
- Get one goal working well before scaling to all of them.
- Tests: pytest for the pipeline (coordinate conversion and clip cutting).
