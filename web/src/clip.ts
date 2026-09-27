// Clip format written by the Python pipeline (see pipeline output in clips/).
// Pitch coordinates are meters on a 105 x 68 pitch centered at (0, 0).

export type TeamSide = 'home' | 'away'

export interface Team {
  id: string
  name: string
  shortName: string
  color: string
  textColor: string
  secondaryColor: string
}

export interface Player {
  id: string
  name: string
  number: number
  team: TeamSide
  position: string
}

export type Vec2 = [number, number]
export type Vec3 = [number, number, number]

export interface Frame {
  t: number
  b: Vec3 | null
  p: Record<string, Vec2>
}

export interface Clip {
  gameId: string
  gameEventId: number
  scorer: string
  scorerId: string
  scorerTeam: TeamSide
  ownGoal: boolean
  clock: string
  period: number
  fps: number
  goalFrame: number
  goalT: number
  ballSource: 'smoothed' | 'raw' // which PFF ball feed the pipeline picked
  ballCorrected: boolean // the pipeline moved the post-shot ball path so it goes in
  contacts?: { f: number; p: string; b: string; s?: 1 }[] // every touch: frame, player id, body part (R/L/F foot, H head, X hands); s = added for a dribble
  carries?: [number, number, string][] // dribbles the pipeline couldn't rebuild: first frame, last frame, player id
  ballEstimated?: [number, number][] // frame ranges where the tracking lost the ball and the pipeline estimated it
  needsReview: boolean // the ball never gets near the goal and was left as tracked
  teams: Record<TeamSide, Team>
  players: Player[]
  frames: Frame[]
}

export interface Sample {
  ball: Vec3 | null
  players: Record<string, Vec2>
}

export function clipDuration(clip: Clip): number {
  return clip.frames[clip.frames.length - 1].t
}

// Which goal is being attacked (+1 = the goal at x = +52.5): the half the ball
// is in at goalT, using the nearest frame with a ball if it's missing there.
export function attackingSide(clip: Clip): 1 | -1 {
  const { frames, goalFrame } = clip
  for (let d = 0; d < frames.length; d++) {
    for (const i of [goalFrame - d, goalFrame + d]) {
      const b = frames[i]?.b
      if (b) return b[0] >= 0 ? 1 : -1
    }
  }
  return 1
}

// Index of the last frame with frame.t <= t (clamped to the clip).
export function frameIndexAt(frames: Frame[], t: number): number {
  let lo = 0
  let hi = frames.length - 1
  if (t <= frames[lo].t) return lo
  if (t >= frames[hi].t) return hi
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (frames[mid].t <= t) lo = mid
    else hi = mid
  }
  return lo
}

const lerp = (a: number, b: number, k: number) => a + (b - a) * k

// Linearly interpolate between the two frames around t. If the ball is missing
// in one of them, use the nearer frame's ball (which may be null = hidden).
export function sampleClip(clip: Clip, t: number): Sample {
  const { frames } = clip
  const i = frameIndexAt(frames, t)
  const a = frames[i]
  const b = frames[Math.min(i + 1, frames.length - 1)]
  const span = b.t - a.t
  const k = span > 0 ? Math.min(Math.max((t - a.t) / span, 0), 1) : 0

  let ball: Vec3 | null
  if (a.b && b.b) {
    ball = [lerp(a.b[0], b.b[0], k), lerp(a.b[1], b.b[1], k), lerp(a.b[2], b.b[2], k)]
  } else {
    ball = k < 0.5 ? a.b : b.b
  }

  const players: Record<string, Vec2> = {}
  for (const id in a.p) {
    const pa = a.p[id]
    const pb = b.p[id] ?? pa
    players[id] = [lerp(pa[0], pb[0], k), lerp(pa[1], pb[1], k)]
  }

  return { ball, players }
}
