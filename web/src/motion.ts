// Per-player movement derived from the clip, for animating the player models:
// how far each player has run (drives the stride) and their current velocity.

import { type Clip, frameIndexAt, sampleClip } from './clip'

const STILL_MPS = 0.8 // below this, tracking jitter isn't counted as running
const VEL_WINDOW_S = 0.2 // velocity from positions this far either side of t

export type Distances = Record<string, Float32Array>

// Cumulative distance run per player, frame by frame.
export function runDistances(clip: Clip): Distances {
  const out: Distances = {}
  const { frames } = clip
  for (const p of clip.players) {
    const d = new Float32Array(frames.length)
    for (let i = 1; i < frames.length; i++) {
      const a = frames[i - 1].p[p.id]
      const b = frames[i].p[p.id]
      const dt = frames[i].t - frames[i - 1].t
      let step = 0
      if (a && b && dt > 0) {
        step = Math.hypot(b[0] - a[0], b[1] - a[1])
        if (step / dt < STILL_MPS) step = 0
      }
      d[i] = d[i - 1] + step
    }
    out[p.id] = d
  }
  return out
}

export function distanceAt(clip: Clip, d: Float32Array, t: number): number {
  const { frames } = clip
  const i = frameIndexAt(frames, t)
  const j = Math.min(i + 1, frames.length - 1)
  const span = frames[j].t - frames[i].t
  const k = span > 0 ? Math.min(Math.max((t - frames[i].t) / span, 0), 1) : 0
  return d[i] + (d[j] - d[i]) * k
}

// Velocity (pitch m/s) of every player at time t, from a centred difference.
export function velocities(clip: Clip, t: number): Record<string, [number, number]> {
  const end = clip.frames[clip.frames.length - 1].t
  const t0 = Math.max(0, t - VEL_WINDOW_S)
  const t1 = Math.min(end, t + VEL_WINDOW_S)
  const a = sampleClip(clip, t0).players
  const b = sampleClip(clip, t1).players
  const dt = t1 - t0 || 1
  const out: Record<string, [number, number]> = {}
  for (const id in a) {
    const pb = b[id] ?? a[id]
    out[id] = [(pb[0] - a[id][0]) / dt, (pb[1] - a[id][1]) / dt]
  }
  return out
}
