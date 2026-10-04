# PitchPOV (Goal Replay): where things stand

Updated Oct 3, 2026 (handoff for a new chat)

Repo: `~/Goal-Replay/Goal-Replay` on Zayd's Mac. GitHub: `zrasheed23/PitchPOV` (public). Work on branch `weekend-2-3d`. GitHub `main` only has a README-only commit; merge pending. Leftover scratch worktree: `git worktree prune`.

Zayd makes code changes with Claude Code in the terminal on the Mac; this chat writes the prompts and reviews the reports. `CLAUDE.md` in the repo has the pipeline order and rules.

## Principles
- Accuracy first; general rules only (named clips = test cases); prefer measured data (StatsBomb, tracked ball); never invent actions; acrobatic goals are rare.
- Goal (Zayd, Sep 29): as real-life accurate as possible, "like a FIFA game". Ball movement smooth everywhere; the ball never jerks or changes direction abruptly unless something touches it. Player graphics need to be developed.
- Confirmed-by-eye outcomes pinned in `tests/fixtures/confirmed_by_eye.json`.
- Quality report per clip: `clips/quality_report.md` (ranked) and `clips/quality.json`. Visual review: `clips/visual_review.md` + contact sheets (local only, clips/ is gitignored).

## Last run (Oct 3): 0 problems, 165 tests, typecheck clean
- Clips passing every quality check: 49 → 69 (carry check now 2.5 m, stricter). Accuracy review list: 9 → 3 (Kane 47:16, Doan 74:46, Richarlison 72:54: StatsBomb timing/location conflicts).
- StatsBomb 360: a frame that names a player counts on its own; anonymous matches that contradict it are dropped. Tracks corrected 609 → 719 (138 clips); 85 label swaps in 50 clips, 0 across teams. The 8 listed far touches (Messi, Diatta, Dumfries, Klaassen x2, Stones x2) all within 0.7 m now.
- The smoothed ball feed (pinned to PFF's own player) no longer counts as "the tracked ball agrees with the player". Dia 40:20: the raw feed has no ball for 5 s; Diatta's receipt is at StatsBomb's spot now.
- Guerreiro 54:55: the snap was the viewer pulling the ball 1 m onto his foot in 0.12 s (ball 1.12 m behind him at his receipt), not the Félix blend. New rule: every toucher meets the ball at his foot once the ball path is final (no faster than 9 m/s). Snapping touches 383 → 38 of ~2,070.
- Carries: a StatsBomb carry is followed when the tracked ball is at StatsBomb's start or at the carrier's feet; viewer carries keep the ball at his feet in the data. Clips with the ball > 2.5 m from a carrier: 18 → 5.
- Leão 79:21: PFF had Bruno Fernandes 14–20 m behind the (measured, correct) ball. 360 frames at his receipt and pass correct his whole track; the 14.2 m blend is gone; he stays within 0.65 m of the ball. The defenders do run back (5–9 m/s, closing 10 → 3.6 m); they looked frozen because the ball ran next to them with no carrier.
- Tried and reverted (net worse): pinning the goal's freeze frame to the kick; median-filtering carry offsets.

## Previous run (Sep 30): 0 problems, 163 tests (typecheck not rerun, no viewer changes)
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
1. Watch: Guerreiro 54:55 (first touch), Leão 79:21 (Bruno's carry), Gnabry 09:31 (Musiala's carry), Mac Allister 45:51 (Molina), Al-Shehri 47:39 (Messi), Dia 40:20 (Diatta), de Jong 48:55 (Dumfries, Klaassen), Rashford 67:40 (Stones), Doan 74:46 (Mitoma's carry). Pin the good ones in `confirmed_by_eye.json`.
2. Candidate rules: StatsBomb events at the same instant (Kane 47:16: block and receipt 17 m apart); a touch timed to "ball nearest StatsBomb's spot" landing mid-flight (Vinícius in Richarlison 72:54); the goal's freeze frame riding the clip-wide 360 time fit.
3. Then roadmap A.

## Known open issues
- 88 clips still have some separation-rule frames (mostly locked pairs); 11 clips have <0.4 m overlap near the kick.
- Accuracy review list: 3 clips (Kane 47:16, Doan 74:46, Richarlison 72:54).
- 38 snapping touches left (29 would need a sprint to meet; 9 turn 2 frames before contact).
- Horta 04:52 unconfirmed.

## Roadmap
Order: motion first, looks second. Graphics on top of wrong motion is wasted work.
- A. Ball physics and smoothness. Physics flight between contacts (gravity, drag, spin/curve, bounce, rolling friction), fitted to the tracked ball and StatsBomb start/end. Direction or speed can change sharply only at a contact (player, post, keeper, ground bounce). Ball spin matches its motion (rolls, doesn't slide). New quality metric: count of ball jerks (acceleration/turn spikes with nobody within ~1 m, bounce excluded); gate at 0.
- B. Player motion. Speed/acceleration caps, turn-rate limits, facing follows movement or the ball, no teleports. Metrics: max speed, max acceleration, turn spikes.
- C. Player graphics. Rigged humanoid models with a locomotion blend (idle/walk/jog/sprint) and stride matched to speed (no foot sliding). Action animations (pass, shot, volley, header, slide, block, keeper dive) timed so the contact frame lands on the ball contact time. Kits in team colours with numbers and names; no real faces or crests.
- D. Environment and camera. Grass with mowing stripes, lighting, shadows, stadium/crowd basics, smoothed broadcast camera.
- E. Then full 166 visual review, README credits StatsBomb, merge into main, Weekend 5 (mobile check, Vercel deploy, demo GIF, personal site).
