// "Follow play" camera: a path worked out once per clip from the whole ball track.
//
// The camera sits at medium height behind the play, looking toward the goal being
// attacked, so you see the ball, the runners ahead of it and the goal. It follows
// where the ball is going to be over the next half second (not where it is this
// instant), so quick passes don't jerk it around. As the play gets near the box it
// comes in closer and lower, and swings out toward the wing the ball is on, so the
// cross, the finish and the goal mouth are all in frame.

import { type BallTrack, sampleBall } from './ballTrack'
import { type Clip, clipDuration, sampleClip } from './clip'

const STEP = 1 / 30
const LEAD_S = 0.45 // look this far ahead in the play
const FOCUS_SIGMA_S = 0.5 // how much the ball path is averaged
const PATH_SIGMA_S = 0.35 // final smoothing of the camera path
const BALL_SIGMA_S = 0.15 // light smoothing of where the ball is now
const HALF_FOV = (20 * Math.PI) / 180 // the scene camera's vertical fov is 40 degrees
const MIN_ASPECT = 1.4 // assume a window at least this wide for left-right framing
const FRAME_MARGIN = 0.72 // keep the ball within this share of the half-height of the frame
const GOAL_X = 52.5
// Far from goal -> near goal
const FAR_M = 40
const NEAR_M = 14
const DIST = [25, 16] // metres behind the play
const HEIGHT = [11.5, 8] // metres up
const WING = [5, 11] // metres out toward the ball's wing
const AHEAD = [0.3, 0.55] // how far toward the goal the camera aims
const MAX_AHEAD_M = 20
// Keep the camera inside the ad boards.
const MAX_X = 58
const MAX_Y = 39

export interface PlayCam {
  pos: Float32Array // x, y, z per step, in three.js coordinates
  target: Float32Array
  fov?: Float32Array // vertical fov per step (degrees), for cameras that zoom
  steps: number
}

// Around the shot the camera drops in behind the scorer, a little up, looking
// along the shot, then eases back out once the ball is in.
const SHOT_BACK_M = 7.5
const SHOT_UP_M = 3
const SHOT_SIDE_M = 1.6 // over the shoulder, toward the outside of the pitch
const SHOT_AHEAD_MAX_M = 14
const SHOT_IN_MAX_S = 1.8 // start moving in at most this long before the shot
const SHOT_IN_MIN_S = 0.8 // and at least this long before it
const SHOT_HOLD_AFTER_S = 0.8 // stay after the ball crosses the line
const SHOT_OUT_S = 1.5

// TV main camera: high in the near stand on the halfway line. It stays put and
// pans (and zooms) to follow the ball, like the wide shot on a broadcast.
const TV_POS = [0, 18, 60] // three.js x, height, z (near stand, behind the touchline)
const TV_LEAD_S = 0.25
const TV_SIGMA_S = 0.4
const TV_VIEW_H = 24 // metres of pitch height kept in view at the ball -> sets the zoom
const TV_FOV = [14, 36]

const angleDiff = (from: number, to: number) => Math.atan2(Math.sin(to - from), Math.cos(to - from))
const lerp = (a: number, b: number, k: number) => a + (b - a) * k
const clamp = (x: number, lo: number, hi: number) => Math.min(Math.max(x, lo), hi)

// Gaussian average of `src` (stride values per step) around each step, shifted by `lead` steps.
function blur(src: Float32Array, stride: number, steps: number, sigma: number, lead: number): Float32Array {
  const out = new Float32Array(src.length)
  const r = Math.ceil(sigma * 3)
  for (let i = 0; i < steps; i++) {
    const c = i + lead
    for (let d = 0; d < stride; d++) {
      let sum = 0
      let wsum = 0
      for (let j = Math.round(c) - r; j <= Math.round(c) + r; j++) {
        const k = clamp(j, 0, steps - 1)
        const w = Math.exp(-((j - c) ** 2) / (2 * sigma * sigma))
        sum += src[k * stride + d] * w
        wsum += w
      }
      out[i * stride + d] = sum / wsum
    }
  }
  return out
}

// Ball on the ground plane per step, holding the last known spot where it's missing.
function ballPlane(clip: Clip, track: BallTrack, steps: number): Float32Array {
  const ball = new Float32Array(steps * 2)
  let last: [number, number] | null = null
  const missing: number[] = []
  for (let i = 0; i < steps; i++) {
    const b = sampleBall(track, clip.frames, i * STEP)
    if (b) last = [b[0], b[1]]
    if (last) {
      ball[i * 2] = last[0]
      ball[i * 2 + 1] = last[1]
    } else missing.push(i)
  }
  if (!last) last = [0, 0]
  const first = missing.length < steps ? [ball[missing.length * 2], ball[missing.length * 2 + 1]] : last
  for (const i of missing) {
    ball[i * 2] = first[0]
    ball[i * 2 + 1] = first[1]
  }

  return ball
}

export function buildPlayCam(clip: Clip, track: BallTrack, side: 1 | -1): PlayCam {
  const steps = Math.floor(clipDuration(clip) / STEP) + 1
  const ball = ballPlane(clip, track, steps)
  const focus = blur(ball, 2, steps, FOCUS_SIGMA_S / STEP, LEAD_S / STEP)
  const now = blur(ball, 2, steps, BALL_SIGMA_S / STEP, 0)
  const pos = new Float32Array(steps * 3)
  const target = new Float32Array(steps * 3)
  const gx = side * GOAL_X
  const shot = planShot(clip, side)
  for (let i = 0; i < steps; i++) {
    // Aim at where the play is heading, but never set up ahead of where the ball
    // is now, or a shot or a pass back leaves the ball under the camera.
    const fx = side > 0 ? Math.min(focus[i * 2], now[i * 2]) : Math.max(focus[i * 2], now[i * 2])
    const fy = focus[i * 2 + 1]
    // Measure toward the goal from no closer than 6 m out, so a ball in the net
    // (behind the line) doesn't flip the camera round to the other side.
    const toGoalX = gx - (side > 0 ? Math.min(fx, GOAL_X - 6) : Math.max(fx, 6 - GOAL_X))
    const toGoalY = -fy
    const d = Math.hypot(toGoalX, toGoalY)
    // Mostly toward the goal, partly straight down the pitch, so the camera
    // doesn't swing round when the ball is out wide near the byline.
    let dx = d > 1 ? (toGoalX / d) * 0.7 + side * 0.3 : side
    let dy = d > 1 ? (toGoalY / d) * 0.7 : 0
    const dl = Math.hypot(dx, dy)
    dx /= dl
    dy /= dl
    const near = clamp((FAR_M - d) / (FAR_M - NEAR_M), 0, 1)
    const dist = lerp(DIST[0], DIST[1], near)
    const wing = clamp(fy / 22, -1, 1) * lerp(WING[0], WING[1], near)
    const cx = clamp(fx - dx * dist, -MAX_X, MAX_X)
    const cy = clamp(fy - dy * dist + wing, -MAX_Y, MAX_Y)
    const ahead = Math.min(lerp(AHEAD[0], AHEAD[1], near) * d, MAX_AHEAD_M)
    const tx = fx + (d > 1 ? (toGoalX / d) * ahead : 0)
    const ty = fy + (d > 1 ? (toGoalY / d) * ahead : 0)
    // Pitch (x, y) -> three.js (x, height, -y).
    pos.set([cx, lerp(HEIGHT[0], HEIGHT[1], near), -cy], i * 3)
    target.set([tx, 1, -ty], i * 3)
    if (shot) blendShot(shot, clip, i * STEP, pos, target, i)
  }
  const cam = {
    pos: blur(pos, 3, steps, PATH_SIGMA_S / STEP, 0),
    target: blur(target, 3, steps, PATH_SIGMA_S / STEP, 0),
    steps,
  }
  // Twice: smoothing the first correction softens it a little.
  keepBallInFrame(cam, clip, track)
  keepBallInFrame(cam, clip, track)
  return cam
}

// Where the ball would still fall out of frame (a high ball, one close under the
// camera, a cross coming in from the side), turn the aim toward it just enough,
// then smooth the aim again so the correction eases in and out.
function keepBallInFrame(cam: PlayCam, clip: Clip, track: BallTrack) {
  const t = cam.target
  const limit = HALF_FOV * FRAME_MARGIN
  const yawLimit = Math.atan(Math.tan(HALF_FOV) * MIN_ASPECT) * FRAME_MARGIN
  for (let i = 0; i < cam.steps; i++) {
    const b = sampleBall(track, clip.frames, i * STEP)
    if (!b) continue
    const px = cam.pos[i * 3], py = cam.pos[i * 3 + 1], pz = cam.pos[i * 3 + 2]
    const bx = b[0] - px, by = Math.max(b[2], 0) - py, bz = -b[1] - pz
    // Left-right first: swing the aim point round the camera.
    {
      const tx = t[i * 3] - px, tz = t[i * 3 + 2] - pz
      const yawOff = angleDiff(Math.atan2(tz, tx), Math.atan2(bz, bx))
      if (Math.abs(yawOff) > yawLimit) {
        const turn = yawOff - Math.sign(yawOff) * yawLimit
        const c = Math.cos(turn), sn = Math.sin(turn)
        t[i * 3] = px + tx * c - tz * sn
        t[i * 3 + 2] = pz + tx * sn + tz * c
      }
    }
    const tx = t[i * 3] - px, ty = t[i * 3 + 1] - py, tz = t[i * 3 + 2] - pz
    const pitchBall = Math.atan2(by, Math.hypot(bx, bz))
    const pitchAim = Math.atan2(ty, Math.hypot(tx, tz))
    const off = pitchBall - pitchAim
    if (Math.abs(off) <= limit) continue
    // Tilt the aim point up or down (same direction on the ground, new height).
    const want = pitchBall - Math.sign(off) * limit
    t[i * 3 + 1] = py + Math.tan(want) * Math.hypot(tx, tz)
  }
  cam.target = blur(t, 3, cam.steps, PATH_SIGMA_S / STEP, 0)
}

export function samplePlayCam(cam: PlayCam, time: number, pos: { set: (x: number, y: number, z: number) => void }, target: typeof pos) {
  const f = clamp(time / STEP, 0, cam.steps - 1)
  const i = Math.floor(f)
  const j = Math.min(i + 1, cam.steps - 1)
  const k = f - i
  const at = (a: Float32Array, d: number) => lerp(a[i * 3 + d], a[j * 3 + d], k)
  pos.set(at(cam.pos, 0), at(cam.pos, 1), at(cam.pos, 2))
  target.set(at(cam.target, 0), at(cam.target, 1), at(cam.target, 2))
  return cam.fov ? lerp(cam.fov[i], cam.fov[j], k) : null
}

interface Shot {
  shooter: string
  t: number // clip time of the shot
  inStart: number
  outStart: number
  at: [number, number] // shooter's spot at the shot
  dir: [number, number] // unit, along the shot
  side: [number, number] // unit, over-the-shoulder offset
  ahead: number
}

const smoothstep = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x))

function planShot(clip: Clip, side: 1 | -1): Shot | null {
  const { frames, goalFrame, goalT } = clip
  const contacts = clip.contacts ?? []
  // The shooter: the scorer if he's on the ball at the shot, else whoever is
  // nearest it (own goals, or a scorer id that doesn't match), else the last touch.
  // (Contact times can be logged late, so the ball position is trusted first.)
  const players = sampleClip(clip, goalT).players
  const ballAt = frames[goalFrame].b
  const gap = (id: string) => (ballAt && players[id] ? Math.hypot(players[id][0] - ballAt[0], players[id][1] - ballAt[1]) : Infinity)
  const nearest = Object.keys(players).sort((a, b) => gap(a) - gap(b))[0]
  const last = [...contacts].reverse().find((c) => c.f <= goalFrame + 3)
  const shooter =
    gap(clip.scorerId) < 2 ? clip.scorerId : nearest && gap(nearest) < 3 ? nearest : (last?.p ?? clip.scorerId)
  const at = players[shooter]
  if (!at) return null
  // Aim along where the ball actually crosses the line.
  const cross = frames.find((f, i) => i >= goalFrame && f.b && side * f.b[0] >= GOAL_X)?.b
  const cx = cross ? cross[0] : side * GOAL_X
  const cy = cross ? cross[1] : 0
  let dx = cx - at[0]
  let dy = cy - at[1]
  const d = Math.hypot(dx, dy) || 1
  dx /= d
  dy /= d
  // Offset toward the touchline the shooter is nearer.
  const out = at[1] >= 0 ? 1 : -1
  let sx = -dy
  let sy = dx
  if (sy * out < 0) {
    sx = -sx
    sy = -sy
  }
  // Start moving in when the final pass/cross is played, within limits.
  const pass = [...contacts].reverse().find((c) => c.f < goalFrame && c.p !== shooter)
  const passT = pass ? frames[pass.f].t : goalT - SHOT_IN_MAX_S
  const inStart = clamp(passT - 0.2, goalT - SHOT_IN_MAX_S, goalT - SHOT_IN_MIN_S)
  const crossT = cross ? frames[frames.findIndex((f) => f.b === cross)].t : goalT + 1
  return {
    shooter,
    t: goalT,
    inStart,
    outStart: crossT + SHOT_HOLD_AFTER_S,
    at: [at[0], at[1]],
    dir: [dx, dy],
    side: [sx, sy],
    ahead: Math.min(d * 0.45, SHOT_AHEAD_MAX_M),
  }
}

function blendShot(shot: Shot, clip: Clip, t: number, pos: Float32Array, target: Float32Array, i: number) {
  const w =
    t < shot.t
      ? smoothstep((t - shot.inStart) / (shot.t - 0.2 - shot.inStart))
      : 1 - smoothstep((t - shot.outStart) / SHOT_OUT_S)
  if (w <= 0) return
  // Follow the shooter until the shot, then hold still and watch it go in.
  const s = t < shot.t ? (sampleClip(clip, t).players[shot.shooter] ?? shot.at) : shot.at
  const [dx, dy] = shot.dir
  const px = s[0] - dx * SHOT_BACK_M + shot.side[0] * SHOT_SIDE_M
  const py = s[1] - dy * SHOT_BACK_M + shot.side[1] * SHOT_SIDE_M
  const tx = s[0] + dx * shot.ahead
  const ty = s[1] + dy * shot.ahead
  const k = i * 3
  pos[k] = lerp(pos[k], clamp(px, -MAX_X, MAX_X), w)
  pos[k + 1] = lerp(pos[k + 1], SHOT_UP_M, w)
  pos[k + 2] = lerp(pos[k + 2], -clamp(py, -MAX_Y, MAX_Y), w)
  target[k] = lerp(target[k], tx, w)
  target[k + 1] = lerp(target[k + 1], 1.2, w)
  target[k + 2] = lerp(target[k + 2], -ty, w)
}

// The TV wide shot: fixed position, pans to the ball and zooms so about the same
// stretch of pitch is in view wherever the ball is.
export function buildBroadcastCam(clip: Clip, track: BallTrack): PlayCam {
  const steps = Math.floor(clipDuration(clip) / STEP) + 1
  const ball = ballPlane(clip, track, steps)
  const height = new Float32Array(steps)
  for (let i = 0; i < steps; i++) {
    const b = sampleBall(track, clip.frames, i * STEP)
    height[i] = b ? Math.max(b[2], 0) : i > 0 ? height[i - 1] : 0
  }
  const aim = blur(ball, 2, steps, TV_SIGMA_S / STEP, TV_LEAD_S / STEP)
  const aimY = blur(height, 1, steps, TV_SIGMA_S / STEP, TV_LEAD_S / STEP)
  const pos = new Float32Array(steps * 3)
  const target = new Float32Array(steps * 3)
  const fov = new Float32Array(steps)
  for (let i = 0; i < steps; i++) {
    const tx = aim[i * 2]
    const tz = -aim[i * 2 + 1]
    const ty = aimY[i] * 0.5
    pos.set(TV_POS, i * 3)
    target.set([tx, ty, tz], i * 3)
    const d = Math.hypot(tx - TV_POS[0], ty - TV_POS[1], tz - TV_POS[2])
    fov[i] = clamp((2 * Math.atan(TV_VIEW_H / 2 / d) * 180) / Math.PI, TV_FOV[0], TV_FOV[1])
  }
  return { pos, target, fov: blur(fov, 1, steps, TV_SIGMA_S / STEP, 0), steps }
}
