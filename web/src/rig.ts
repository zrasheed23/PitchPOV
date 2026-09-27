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
}

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
