# PitchPOV (Goal Replay): where things stand

Updated Sep 30, 2026 (handoff for a new chat)

Repo: `~/Goal-Replay/Goal-Replay` on Zayd's Mac. GitHub: `zrasheed23/PitchPOV` (public). Work on branch `weekend-2-3d`. GitHub `main` only has a README-only commit; merge pending. Leftover scratch worktree: `git worktree prune`.

Zayd makes code changes with Claude Code in the terminal on the Mac; this chat writes the prompts and reviews the reports. `CLAUDE.md` in the repo has the pipeline order and rules.

## Principles
- Accuracy first; general rules only (named clips = test cases); prefer measured data (StatsBomb, tracked ball); never invent actions; acrobatic goals are rare.
- Goal (Zayd, Sep 29): as real-life accurate as possible, "like a FIFA game". Ball movement smooth everywhere; the ball never jerks or changes direction abruptly unless something touches it. Player graphics need to be developed.
- Confirmed-by-eye outcomes pinned in `tests/fixtures/confirmed_by_eye.json`.
- Quality report per clip: `clips/quality_report.md` (ranked) and `clips/quality.json`. Visual review: `clips/visual_review.md` + contact sheets (local only, clips/ is gitignored).

## Last run (Sep 30): 0 problems, 163 tests (typecheck not rerun, no viewer changes)
- Players far from own touch (>5 m, tracked ball backs StatsBomb): 23 touches. 0 label swaps (existing swap rule runs first), 15 blended onto the ball (12 clips; shortest 1–3 s window keeping him ≤9 m/s and onside), 8 left and listed.
  - Guerreiro 54:55: João Félix blended at both touches (7.7–7.8 m).
  - Leão 79:21: Bruno Fernandes blended at his receipt (14.2 m). His 11.1 m/s sprint was there before this change.
  - Listed (would need a 15–28 m sprint): Messi in Al-Shehri 47:39 (19 m, 2 touches), Diatta in Dia 40:20 (23 m), Dumfries/Klaassen in de Jong 48:55 (15–18 m), Stones in Rashford 67:40 (21–28 m, 2 touches). Candidates for the 360 run.
- Player separation: centres ≥0.6 m apart, 0.4 m for two opponents both within 1.5 m of the ball. Less reliable player moves, smoothed. Touches, shooter at kick, keeper dive and 360-placed positions never move.
  - Clips with centres <0.4 m within 0.5 s of the kick: 66 → 11.
  - Frames breaking the rule: 30,227 → 1,242 (165 → 88 clips; rest mostly two locked players).
- Shot spot: ball goes on StatsBomb's shot location when tracked ball is >1 m off (was kept within 2 m). Shot origin failures 15 → 0.
  - Valencia 48:44 wasn't a separation problem: shot was 1.3 m off toward Noppert. Now 1.28 m apart at the kick as StatsBomb has them; now a test.
- Clips passing every quality check: 45 → 49. Accuracy review list: 21 → 9.

## Previous run (Sep 29): 0 problems, 158 tests
- Actions follow StatsBomb events; header if no-body-part touch and ball >1.6 m; "Lob" = chip peaking ~3 m (5 goals); 124 PFF touches removed before the kick.
- Mbappé 80:58 accepted after watching. Aboubakar 91:47 v Brazil is a header; the chip was v Serbia 62:51.
- 48 defensive animations in 38 clips. Offside at assist 4 → 0. Low shots skim the grass (47 clips).
- Visual review script (headless Chrome, 2 cameras, contact sheets): 22 clips → 17 pass / 1 check / 4 fail. Full 166 ≈ 24 min + ~350k image tokens.
- Zayd's watch: all goals looked good.

## Next session
1. Watch: Guerreiro 54:55 (Félix blend), Leão 79:21 (Bruno blend, 11.1 m/s run), Valencia 48:44 (shot starts 1.3 m further back). Pin the good ones in `confirmed_by_eye.json`.
2. Decide on the 8 listed touches (likely via StatsBomb 360 run).
3. Then StatsBomb 360 run or roadmap A.

## Known open issues
- 88 clips still have some separation-rule frames (mostly locked pairs); 11 clips have <0.4 m overlap near the kick.
- 8 listed touches above (5 clips).
- Accuracy review list: 9 clips.
- Horta 04:52 unconfirmed.

## Roadmap
Order: motion first, looks second. Graphics on top of wrong motion is wasted work.
- A. Ball physics and smoothness. Physics flight between contacts (gravity, drag, spin/curve, bounce, rolling friction), fitted to the tracked ball and StatsBomb start/end. Direction or speed can change sharply only at a contact (player, post, keeper, ground bounce). Ball spin matches its motion (rolls, doesn't slide). New quality metric: count of ball jerks (acceleration/turn spikes with nobody within ~1 m, bounce excluded); gate at 0.
- B. Player motion. Speed/acceleration caps, turn-rate limits, facing follows movement or the ball, no teleports. Metrics: max speed, max acceleration, turn spikes.
- C. Player graphics. Rigged humanoid models with a locomotion blend (idle/walk/jog/sprint) and stride matched to speed (no foot sliding). Action animations (pass, shot, volley, header, slide, block, keeper dive) timed so the contact frame lands on the ball contact time. Kits in team colours with numbers and names; no real faces or crests.
- D. Environment and camera. Grass with mowing stripes, lighting, shadows, stadium/crowd basics, smoothed broadcast camera.
- E. Then full 166 visual review, README credits StatsBomb, merge into main, Weekend 5 (mobile check, Vercel deploy, demo GIF, personal site).
