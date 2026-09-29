// Pivot groups of a player model (see Player.tsx) and the running pose.

import type * as THREE from 'three'

export const PLAYER_HEIGHT = 1.8

export interface Rig {
  body: THREE.Group // leans forward when running
  hipL: THREE.Group
  hipR: THREE.Group
  kneeL: THREE.Group
  kneeR: THREE.Group
  shoulderL: THREE.Group
  shoulderR: THREE.Group
  elbowL: THREE.Group
  elbowR: THREE.Group
  footL: THREE.Object3D // boots and head: where the ball meets the player
  footR: THREE.Object3D
  head: THREE.Object3D
}

export const RIG_PARTS = 12

// Pose the rig for a running phase (radians) at a given speed (m/s).
export function animateRig(rig: Rig, phase: number, speed: number) {
  const run = Math.min(speed / 7, 1) // 0 standing, 1 sprinting
  const walk = Math.min(speed / 2, 1)
  const amp = 0.25 * walk + 0.6 * run
  const s = Math.sin(phase)
  // Legs swing opposite each other; the trailing leg bends at the knee.
  rig.hipL.rotation.x = s * amp
  rig.hipR.rotation.x = -s * amp
  // Forward is -z; a positive x rotation swings a limb forward, negative bends a shin back.
  rig.kneeL.rotation.x = -(Math.max(0, -s) * amp * 1.5 + 0.05)
  rig.kneeR.rotation.x = -(Math.max(0, s) * amp * 1.5 + 0.05)
  // Arms swing against the legs, elbows bent more when running.
  rig.shoulderL.rotation.x = -s * amp * 0.8
  rig.shoulderR.rotation.x = s * amp * 0.8
  rig.elbowL.rotation.x = 0.25 + 0.9 * run
  rig.elbowR.rotation.x = 0.25 + 0.9 * run
  // Lean into the run and bob slightly each stride.
  rig.body.rotation.x = -0.18 * run
  rig.body.position.y = Math.abs(Math.cos(phase)) * 0.05 * run
  // Undo anything a dive or a keeper's stance set.
  rig.hipL.rotation.z = rig.hipR.rotation.z = 0
  rig.body.rotation.z = 0
  rig.body.position.x = 0
  rig.body.position.z = 0
  rig.shoulderL.rotation.z = -ARM_REST_Z
  rig.shoulderR.rotation.z = ARM_REST_Z
}

const ARM_REST_Z = 0.12 // arms hang slightly away from the body

// Goalkeeper dive, k seconds after take-off. `side` is +1 to dive toward the
// keeper's right (local +x), -1 to his left; `strength` 0..1 scales how far.
// Load (0-0.12 s), fly (0.12-0.5 s), land and lie (to 1.6 s), get up (to 2.4 s).
export const DIVE_LENGTH_S = 2.4

const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x))

// height: the ball's height where he meets it (a low ball is a low dive along
// the grass, a high one a leap with the arms up).
export function animateDive(rig: Rig, k: number, side: 1 | -1, strength: number, height = 0.8) {
  const load = ease(k / 0.12) * (1 - ease((k - 0.12) / 0.15))
  const fly = ease((k - 0.12) / 0.38)
  const up = 1 - ease((k - 1.6) / 0.8) // 1 while down, back to 0 once standing
  const roll = fly * up * (0.55 + 0.95 * strength) // up to ~1.5 rad: close to lying flat
  rig.hipL.rotation.z = rig.hipR.rotation.z = 0
  const high = Math.min(Math.max((height - 0.5) / 1.5, 0), 1)
  const lift = Math.sin(Math.min(Math.max((k - 0.12) / 0.38, 0), 1) * Math.PI) * (0.12 + 0.6 * high) * Math.max(strength, 0.4)

  // Roll sideways about the feet; shift the body so its middle stays on the
  // tracked position instead of the feet.
  rig.body.rotation.z = -side * roll
  rig.body.rotation.x = 0
  rig.body.position.x = -side * Math.sin(roll) * 0.2 + side * fly * up * (0.3 + 1.0 * strength)
  rig.body.position.y = lift + Math.sin(roll) * 0.12 - load * 0.12

  // Arms reach up past the head in the direction of the dive.
  // Both arms swing up over the head toward the dive side (+z rotation lifts an
  // arm toward +x), so neither points at the sky once he's horizontal.
  const reach = Math.min(fly, up) * Math.PI * 0.85
  rig.shoulderL.rotation.z = (1 - Math.min(fly, up)) * -ARM_REST_Z + side * reach
  rig.shoulderR.rotation.z = (1 - Math.min(fly, up)) * ARM_REST_Z + side * reach
  rig.shoulderL.rotation.x = 0
  rig.shoulderR.rotation.x = 0
  rig.elbowL.rotation.x = 0.15
  rig.elbowR.rotation.x = 0.15

  // Crouch to load, then the legs trail slightly bent.
  const bend = load * 0.9 + fly * up * 0.35
  rig.hipL.rotation.x = bend * 0.8
  rig.hipR.rotation.x = bend * 0.6
  rig.kneeL.rotation.x = -bend * 1.4
  rig.kneeR.rotation.x = -bend * 1.1
}

// A touch of the ball, `k` seconds from the moment of contact (negative before),
// within a window of `w` seconds either side. Layered on top of the running pose.
// part: R/L/F foot (F = either; `side` picks the leg), H head, X hands.
// style: "volley" (the ball in the air: leg swings high, leaning back a little)
// or "half" (just after the bounce: over the ball, a short sharp swing).
export function animateTouch(rig: Rig, k: number, w: number, part: string, side: 1 | -1, style?: string) {
  const s = Math.min(Math.max(k / w, -1), 1) // -1 .. 0 (contact) .. 1
  const e = 1 - s * s // 0 at the window edges, 1 at contact
  if (part === 'H') {
    // Jump into it and nod through the ball.
    rig.body.position.y += 0.28 * e
    rig.body.rotation.x = -0.35 * e * (s < 0 ? 1 + s : 1 - s * 0.5)
    rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = -0.4 * e
    return
  }
  if (part === 'X') {
    // Hands out in front (throw-in, keeper).
    rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = 1.5 * e
    rig.elbowL.rotation.x = rig.elbowR.rotation.x = 0.3 * e
    return
  }
  // Foot: back-swing, then swing through the ball; the standing leg stays planted.
  const kick = side > 0 ? rig.hipR : rig.hipL
  const kickKnee = side > 0 ? rig.kneeR : rig.kneeL
  const plant = side > 0 ? rig.hipL : rig.hipR
  const plantKnee = side > 0 ? rig.kneeL : rig.kneeR
  const swing = 0.3 + 1.0 * s // behind the body before contact, just ahead of it at contact, through after
  const high = style === 'volley' ? 0.6 : style === 'half' ? -0.15 : 0 // how much higher the leg comes through
  kick.rotation.x = kick.rotation.x * (1 - e) + (swing + high * Math.max(0, 1 + s)) * e
  if (style === 'volley') rig.body.rotation.x += 0.18 * e // lean back to get the leg up
  if (style === 'half') rig.body.rotation.x -= 0.15 * e // head over the ball
  kickKnee.rotation.x = kickKnee.rotation.x * (1 - e) - Math.max(0, -s) * 1.4 * e
  plant.rotation.x *= 1 - e
  plantKnee.rotation.x = plantKnee.rotation.x * (1 - e) - 0.15 * e
  // Arms out for balance.
  rig.shoulderL.rotation.z = -0.12 - 0.5 * e
  rig.shoulderR.rotation.z = 0.12 + 0.5 * e
}

// Throw-in, k seconds from the release (negative before). `hold` is how long he
// holds the ball above his head first. Lift it up (0.3 s), hold it, wind back
// behind the head, then whip both arms forward and follow through (to +0.6 s).
export const THROW_AFTER_S = 0.6

export function animateThrow(rig: Rig, k: number, hold: number) {
  const up = ease((k + hold + 0.3) / 0.3) // arms rise from the ball at his feet to over his head
  const wind = ease((k + 0.3) / 0.25) * (1 - ease(k / 0.12)) // back behind the head just before the release
  const through = ease(k / 0.15) * (1 - ease((k - 0.2) / 0.4)) // arms forward after it
  const done = ease((k - 0.2) / 0.4) // back to the standing pose
  const arm = (1 - done) * (up * 2.9 - through * 1.5) // radians forward-and-over: ~2.9 is straight up
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = arm
  rig.shoulderL.rotation.z = -ARM_REST_Z * (1 - up * (1 - done))
  rig.shoulderR.rotation.z = ARM_REST_Z * (1 - up * (1 - done))
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = (1 - done) * (up * 0.5 + wind * 0.9 - through * 0.4) + done * 0.25
  // Lean back into the wind-up, forward through the throw; feet stay down.
  rig.body.rotation.x = wind * 0.22 - through * 0.25
  rig.body.position.y = 0
  rig.hipL.rotation.x = rig.hipR.rotation.x = 0
  rig.kneeL.rotation.x = rig.kneeR.rotation.x = -0.05 - wind * 0.15
}

// Bicycle (overhead) kick, k seconds from the contact (negative before).
// `side` +1 kicks with the right leg. Take off (from -0.45 s), body back to
// horizontal with the hips at ~1 m at the contact, kicking leg swinging up and
// over while the other drops, fall onto the back (to +0.45 s), lie, get up
// (+1.0 to +1.8 s). The body pivots at the feet, so it is shifted to keep its
// middle on the tracked position. (The scissor kick is animateScissor.)
export const BICYCLE_BEFORE_S = 0.45
export const BICYCLE_AFTER_S = 1.8

export function animateBicycle(rig: Rig, k: number, side: 1 | -1) {
  const load = ease((k + 0.45) / 0.08) * (1 - ease((k + 0.37) / 0.08))
  const air = ease((k + 0.4) / 0.4) // leaving the ground, tipping back
  const up = 1 - ease((k - 1.0) / 0.8) // 1 until he gets up
  // Flat on his back, square, the leg going straight over his head.
  const tilt = Math.min(air, up) * 1.6
  const fall = ease((k - 0.05) / 0.4) // after the contact he drops onto his back
  const hips = 1.0 * (1 - fall) + 0.12 * fall
  rig.body.rotation.x = tilt
  rig.body.rotation.z = 0
  // Hips at `hips` metres: the pivot is at the feet, so lift the body by the
  // hips' drop and move it forward by half its swing back.
  rig.body.position.y = Math.max(0, (hips - 0.92 * Math.cos(tilt)) * Math.min(air, up) - load * 0.12)
  rig.body.position.x = 0
  rig.body.position.z = -0.45 * Math.sin(tilt)

  // Kicking leg: loaded back, swung up and over (past straight up at contact), then down.
  const kick = side > 0 ? rig.hipR : rig.hipL
  const kickKnee = side > 0 ? rig.kneeR : rig.kneeL
  const other = side > 0 ? rig.hipL : rig.hipR
  const otherKnee = side > 0 ? rig.kneeL : rig.kneeR
  const swing = ease((k + 0.25) / 0.3) // 0 before, 1 at +0.05 s
  const settle = ease((k - 0.15) / 0.5)
  kick.rotation.x = up * ((-0.4 + 2.9 * swing) * (1 - settle) + 0.4 * settle)
  kickKnee.rotation.x = -up * (0.9 * (1 - swing) + 0.1) * (1 - settle * 0.5)
  // The other leg goes up first (the scissor), then drops as the kick comes through.
  const lead = ease((k + 0.4) / 0.2) * (1 - ease((k + 0.15) / 0.25))
  other.rotation.x = up * (1.3 * lead + 0.3 * settle)
  otherKnee.rotation.x = -up * (0.5 * lead + 0.3)
  // Arms out wide in the air, down to break the fall.
  const wide = Math.min(air, 1 - fall * 0.6) * up
  rig.shoulderL.rotation.z = -ARM_REST_Z - 1.2 * wide
  rig.shoulderR.rotation.z = ARM_REST_Z + 1.2 * wide
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = 0.6 * fall * up
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 0.3
}

// A shot close to the keeper: no dive, he reacts toward it and it beats him.
// k seconds from his reaction; `side` +1 toward his right; height: the ball's
// height where it reaches him, `arrive` seconds after his reaction; through:
// "legs" (a low ball between his feet), "side" (past his hand) or "over";
// gap: metres from his centre to the ball as it passes.
// He shifts his weight toward it and reaches for it (hands down in front for a
// low ball, up to its height for a higher one, legs apart for one through his
// legs), but he's beaten: his hand stops short of the ball (HAND_SHORT_M), and
// once it's past he straightens up and his arms drop. He never ducks.
export const BLOCK_LENGTH_S = 1.6
const HAND_SHORT_M = 0.1
const BALL_R = 0.11
const SHOULDER_X = 0.24 // from his centre (Player.tsx)
const ARM_M = 0.6

export function animateBlock(rig: Rig, k: number, height: number, side: 1 | -1, arrive = 0.3, through = 'side', gap = 1) {
  const e = ease(k / 0.15) * (1 - ease((k - arrive - 0.9) / 0.5)) // in, hold, out
  const reach = ease(k / Math.max(arrive, 0.12)) // arms get there as the ball does
  const after = ease((k - arrive) / 0.35) // it's past
  const low = 1 - Math.min(Math.max((height - 0.4) / 0.8, 0), 1)
  const legs = through === 'legs' ? 1 : 0
  // How far toward the ball his hand may get: short of it, never through it.
  const room = Math.max(gap - BALL_R - HAND_SHORT_M, 0)
  const sway = Math.min(0.18, Math.max(room - SHOULDER_X - ARM_M * 0.5, 0)) * (1 - legs)
  const out = Math.min(Math.max((room - sway - SHOULDER_X) / ARM_M, 0), 1)
  const ballArm = Math.min(0.7, Math.max(Math.asin(out) - ARM_REST_Z, 0)) * reach * e
  // Weight toward the ball (only as far as there's room), lower for a low ball.
  rig.body.position.x = side * sway * e
  rig.body.position.y = -0.22 * low * e * (1 - after)
  rig.body.rotation.x = -0.3 * low * e * (1 - after)
  rig.body.rotation.z = -side * sway * reach * e
  // Legs: bent; apart for a ball between them.
  rig.hipL.rotation.x = rig.hipR.rotation.x = 0.5 * low * e
  rig.hipL.rotation.z = -(0.05 + 0.3 * legs) * e
  rig.hipR.rotation.z = (0.05 + 0.3 * legs) * e
  rig.kneeL.rotation.x = rig.kneeR.rotation.x = -(0.9 * low + 0.2) * e - 0.05
  // Arms: toward the ball's height, the one on its side reaching out as far as it can.
  const up = 0.4 + Math.min(Math.max(height / 2.2, 0), 1) * 2.3
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = up * reach * e * (1 - 0.6 * after)
  rig.shoulderL.rotation.z = -ARM_REST_Z - (side < 0 ? ballArm : 0.15 * reach * e)
  rig.shoulderR.rotation.z = ARM_REST_Z + (side > 0 ? ballArm : 0.15 * reach * e)
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 0.15 + 0.3 * after
}

// A keeper's ready stance, layered on the running pose when he's moving slowly:
// knees bent, arms out, short side-steps instead of a stride (phase: radians
// through his step cycle), and a small set hop just before the shot (hop: seconds
// from the kick, or null).
export function animateKeeper(rig: Rig, phase: number, speed: number, hop: number | null) {
  const ready = 1 - Math.min(Math.max((speed - 2.5) / 1.5, 0), 1) // gone by 4 m/s: he's running
  if (ready <= 0) return
  const s = Math.sin(phase)
  const step = Math.min(speed / 1.5, 1) * 0.18 // feet apart and together, not a stride
  rig.hipL.rotation.x *= 1 - ready
  rig.hipR.rotation.x *= 1 - ready
  rig.hipL.rotation.z = -(0.08 + step * Math.max(0, s)) * ready
  rig.hipR.rotation.z = (0.08 + step * Math.max(0, -s)) * ready
  rig.kneeL.rotation.x = rig.kneeL.rotation.x * (1 - ready) - 0.35 * ready
  rig.kneeR.rotation.x = rig.kneeR.rotation.x * (1 - ready) - 0.35 * ready
  rig.body.rotation.x = rig.body.rotation.x * (1 - ready) - 0.15 * ready
  rig.body.position.y = rig.body.position.y * (1 - ready) - 0.08 * ready
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = 0.5 * ready
  rig.shoulderL.rotation.z = -ARM_REST_Z - 0.35 * ready
  rig.shoulderR.rotation.z = ARM_REST_Z + 0.35 * ready
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 0.5 * ready
  if (hop !== null && hop > -0.3 && hop < 0.05) {
    // Up and down on the balls of his feet as the shot is struck.
    rig.body.position.y += Math.sin(((hop + 0.3) / 0.3) * Math.PI) * 0.1
  }
}

// Scissor kick, k seconds from the contact (negative before). `side` +1 kicks
// with the right leg. Side-on to the shot: the body rolls sideways about the
// forward axis (away from the kicking leg) and leans back a little, 45-60
// degrees from vertical at most, never flat. The other leg lifts first
// (-0.3 s), then the kicking leg swings across in front of him at
// hip-to-chest height and meets the ball side-on at k = 0, both feet off the
// ground; he lands on his hip and side (to +0.4 s), lies briefly, gets up (to +1.6 s).
export const SCISSOR_BEFORE_S = 0.4
export const SCISSOR_AFTER_S = 1.6

export function animateScissor(rig: Rig, k: number, side: 1 | -1) {
  const load = ease((k + 0.4) / 0.08) * (1 - ease((k + 0.32) / 0.08))
  const air = ease((k + 0.32) / 0.32) // leaves the ground, rolling onto his side
  const land = ease((k - 0.05) / 0.35) // drops onto his hip after the contact
  const up = 1 - ease((k - 0.9) / 0.7) // 1 until he gets up
  const on = Math.min(air, up)
  // Sideways roll grows as he goes over; the lean back stays small.
  const roll = on * (0.85 + 0.35 * land) // ~49 deg in the air, ~69 deg lying on his side
  const back = on * 0.35 * (1 - 0.5 * land)
  rig.body.rotation.z = side * roll // right-footed: tips to his left
  rig.body.rotation.x = back
  // Both feet off the ground at the contact (~0.45 m), then down on his hip.
  const lift = Math.sin(Math.min(Math.max((k + 0.32) / 0.5, 0), 1) * Math.PI) * 0.45
  rig.body.position.y = Math.max(0, lift * (1 - land) + 0.1 * land * up - load * 0.1)
  rig.body.position.x = -side * Math.sin(roll) * 0.35 // keep his middle over the tracked spot
  rig.body.position.z = 0

  const kick = side > 0 ? rig.hipR : rig.hipL
  const kickKnee = side > 0 ? rig.kneeR : rig.kneeL
  const other = side > 0 ? rig.hipL : rig.hipR
  const otherKnee = side > 0 ? rig.kneeL : rig.kneeR
  // The other leg lifts first, then drops as the kicking leg comes through (the scissor).
  const lead = ease((k + 0.35) / 0.15) * (1 - ease((k + 0.1) / 0.2))
  other.rotation.x = up * (1.2 * lead + 0.3 * land)
  other.rotation.z = 0
  otherKnee.rotation.x = -up * (0.7 * lead + 0.4 * land + 0.1)
  // Kicking leg: cocked back, then across the front of the body at hip-to-chest height.
  const swing = ease((k + 0.22) / 0.27) // through the ball at k ~ +0.05
  const after = ease((k - 0.1) / 0.4)
  kick.rotation.x = up * ((-0.4 + 1.9 * swing) * (1 - after) + 0.5 * after)
  kick.rotation.z = -side * up * 0.6 * swing * (1 - after) // across to the other side
  kickKnee.rotation.x = -up * (1.0 * (1 - swing) + 0.15) * (1 - 0.5 * after)
  // Arms out for balance, then the lower arm braces the landing.
  rig.shoulderL.rotation.z = -ARM_REST_Z - 1.0 * on
  rig.shoulderR.rotation.z = ARM_REST_Z + 1.0 * on
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = 0.4 * land * up
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 0.3
}

// Defenders (the pipeline's `defense`: StatsBomb blocks, clearances, tackles).
// k seconds from the contact (negative before). He drops into it from
// SLIDE_BEFORE_S, is down at the contact, slides on (the tracked position
// carries him), and gets up by SLIDE_AFTER_S. The body pivots at the feet, so
// it's shifted to keep his middle on the tracked position.
export const SLIDE_BEFORE_S = 0.35
export const SLIDE_AFTER_S = 1.4

// Slide tackle: leaning back on one hip, the leading leg straight out along the
// ground at the ball, the other folded under him, arms back for balance.
// `side` +1 leads with the right leg.
export function animateSlide(rig: Rig, k: number, side: 1 | -1) {
  const down = ease((k + SLIDE_BEFORE_S) / 0.3) // dropping into it
  const up = 1 - ease((k - 0.7) / 0.7) // back on his feet by 1.4 s
  const on = Math.min(down, up)
  const tilt = on * 1.05 // leaning back ~60 degrees
  rig.body.rotation.x = tilt
  rig.body.rotation.z = -side * on * 0.35 // onto the hip of the folded leg
  // Hips near the grass: the pivot is at the feet, so lift by the hips' drop and pull forward.
  rig.body.position.y = Math.max(0, (0.2 - 0.92 * Math.cos(tilt)) * on)
  rig.body.position.z = -0.4 * Math.sin(tilt)
  rig.body.position.x = side * 0.1 * on
  const lead = side > 0 ? rig.hipR : rig.hipL
  const leadKnee = side > 0 ? rig.kneeR : rig.kneeL
  const fold = side > 0 ? rig.hipL : rig.hipR
  const foldKnee = side > 0 ? rig.kneeL : rig.kneeR
  lead.rotation.x = on * 1.2 // straight out in front along the grass
  leadKnee.rotation.x = -0.05
  fold.rotation.x = on * 0.6
  foldKnee.rotation.x = -on * 1.8 // tucked under
  lead.rotation.z = fold.rotation.z = 0
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = -on * 0.7 // arms back, hands to the ground
  rig.shoulderL.rotation.z = -ARM_REST_Z - on * 0.5
  rig.shoulderR.rotation.z = ARM_REST_Z + on * 0.5
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 0.2
}

// Sliding block: down on his side across the ball's path, the leading leg
// stretched out toward it (sideways), arms tucked in (no handball).
export function animateSlideBlock(rig: Rig, k: number, side: 1 | -1) {
  const down = ease((k + SLIDE_BEFORE_S) / 0.3)
  const up = 1 - ease((k - 0.7) / 0.7)
  const on = Math.min(down, up)
  const roll = on * 0.95 // over onto his side, facing the ball
  rig.body.rotation.z = -side * roll
  rig.body.rotation.x = on * 0.25
  rig.body.position.y = Math.max(0, (0.25 - 0.92 * Math.cos(roll)) * on)
  rig.body.position.x = side * Math.sin(roll) * 0.45
  rig.body.position.z = 0
  const lead = side > 0 ? rig.hipR : rig.hipL
  const leadKnee = side > 0 ? rig.kneeR : rig.kneeL
  const other = side > 0 ? rig.hipL : rig.hipR
  const otherKnee = side > 0 ? rig.kneeL : rig.kneeR
  lead.rotation.x = on * 0.9
  lead.rotation.z = side * on * 0.5 // swung out toward the ball
  leadKnee.rotation.x = -0.05
  other.rotation.x = on * 0.4
  other.rotation.z = 0
  otherKnee.rotation.x = -on * 1.2
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = -on * 0.3
  rig.shoulderL.rotation.z = -ARM_REST_Z + on * 0.05 // tucked to his sides
  rig.shoulderR.rotation.z = ARM_REST_Z - on * 0.05
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 1.4 * on
}

// Standing block: feet planted wide, knees bent, leaning into the ball's path,
// arms behind his back (no handball). In from -0.3 s, out by +0.6 s.
export const STAND_BLOCK_BEFORE_S = 0.3
export const STAND_BLOCK_AFTER_S = 0.6

export function animateStandBlock(rig: Rig, k: number, side: 1 | -1) {
  const on = ease((k + STAND_BLOCK_BEFORE_S) / 0.2) * (1 - ease((k - 0.25) / 0.35))
  rig.hipL.rotation.x = rig.hipR.rotation.x = 0.35 * on
  rig.hipL.rotation.z = -0.3 * on
  rig.hipR.rotation.z = 0.3 * on
  rig.kneeL.rotation.x = rig.kneeR.rotation.x = -0.6 * on - 0.05
  rig.body.rotation.x = -0.3 * on
  rig.body.rotation.z = -side * 0.12 * on // into the ball's side
  rig.body.position.y = -0.12 * on
  rig.body.position.x = side * 0.08 * on
  rig.shoulderL.rotation.x = rig.shoulderR.rotation.x = -0.6 * on // hands behind his back
  rig.shoulderL.rotation.z = -ARM_REST_Z + 0.08 * on
  rig.shoulderR.rotation.z = ARM_REST_Z - 0.08 * on
  rig.elbowL.rotation.x = rig.elbowR.rotation.x = 1.0 * on
}
