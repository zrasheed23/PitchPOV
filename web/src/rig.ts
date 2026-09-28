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
  // Undo anything a dive set.
  rig.body.rotation.z = 0
  rig.body.position.x = 0
  rig.shoulderL.rotation.z = -ARM_REST_Z
  rig.shoulderR.rotation.z = ARM_REST_Z
}

const ARM_REST_Z = 0.12 // arms hang slightly away from the body

// Goalkeeper dive, k seconds after take-off. `side` is +1 to dive toward the
// keeper's right (local +x), -1 to his left; `strength` 0..1 scales how far.
// Load (0-0.12 s), fly (0.12-0.5 s), land and lie (to 1.6 s), get up (to 2.4 s).
export const DIVE_LENGTH_S = 2.4

const ease = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x))

export function animateDive(rig: Rig, k: number, side: 1 | -1, strength: number) {
  const load = ease(k / 0.12) * (1 - ease((k - 0.12) / 0.15))
  const fly = ease((k - 0.12) / 0.38)
  const up = 1 - ease((k - 1.6) / 0.8) // 1 while down, back to 0 once standing
  const roll = fly * up * (0.55 + 0.95 * strength) // up to ~1.5 rad: close to lying flat
  const lift = Math.sin(Math.min(Math.max((k - 0.12) / 0.38, 0), 1) * Math.PI) * 0.35 * strength

  // Roll sideways about the feet; shift the body so its middle stays on the
  // tracked position instead of the feet.
  rig.body.rotation.z = -side * roll
  rig.body.rotation.x = 0
  rig.body.position.x = -side * Math.sin(roll) * 0.2 + side * fly * up * 0.5 * strength
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
export function animateTouch(rig: Rig, k: number, w: number, part: string, side: 1 | -1) {
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
  kick.rotation.x = kick.rotation.x * (1 - e) + swing * e
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

// Scissor / overhead kick, k seconds from the contact (negative before).
// `side` +1 kicks with the right leg. Take off (from -0.45 s), body back to
// horizontal with the hips at ~1 m at the contact, kicking leg swinging up and
// over while the other drops (the scissor), fall onto the back (to +0.45 s),
// lie, get up (+1.0 to +1.8 s). The body pivots at the feet, so it is shifted
// to keep its middle on the tracked position.
export const VOLLEY_BEFORE_S = 0.45
export const VOLLEY_AFTER_S = 1.8

export function animateVolley(rig: Rig, k: number, side: 1 | -1) {
  const load = ease((k + 0.45) / 0.08) * (1 - ease((k + 0.37) / 0.08))
  const air = ease((k + 0.4) / 0.4) // leaving the ground, tipping back
  const up = 1 - ease((k - 1.0) / 0.8) // 1 until he gets up
  const tilt = Math.min(air, up) * 1.45 // ~83 degrees back: horizontal
  const fall = ease((k - 0.05) / 0.4) // after the contact he drops onto his back
  const hips = 1.0 * (1 - fall) + 0.12 * fall
  rig.body.rotation.x = tilt
  rig.body.rotation.z = side * 0.35 * Math.min(air, up) // side-on: rolled a little away from the kicking leg
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
