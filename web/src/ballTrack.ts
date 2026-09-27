// A smooth ball path for the viewer, built once per clip.
//
// Two things made the ball look stuttery when drawn straight from the frames:
// 1. The ball feed often holds a coordinate for a frame (height especially, which
//    updates at ~15 Hz in places), so the ball moved, stopped, moved, stopped.
//    Those held values are filled in by interpolating to the next real update.
//    The feed also has one-frame pops (the height jumping a metre and back), so
//    between touches each frame is replaced by a quadratic fitted to the frames
//    around it. A ball in flight moves on a (near) parabola, so this keeps real
//    flights and only removes what a ball can't do.
// 2. Straight-line interpolation between 30 Hz frames changes speed abruptly at
//    every frame. A cubic (Catmull-Rom) curve through the frames keeps the speed
//    continuous, except at touches and bounces, where the ball really does change
//    direction in an instant, so the curve keeps a sharp corner there.

import { type Clip, type Vec3, frameIndexAt } from './clip'

const HOLD_MAX = 3 // a coordinate repeated for up to this many frames is a hold, not the ball stopping
const BOUNCE_Z = 0.35 // a height minimum this close to the ground is a bounce
const FIT_RADIUS = 4 // frames either side in each quadratic fit (~0.3 s window)
const OUTLIER_M = 0.25 // a frame this far off the first fit is left out of the second

export interface BallTrack {
  t: number[]
  p: (Vec3 | null)[]
  mL: Vec3[] // tangent arriving at each frame
  mR: Vec3[] // tangent leaving each frame
}

export function buildBallTrack(clip: Clip): BallTrack {
  const { frames } = clip
  const n = frames.length
  const t = frames.map((f) => f.t)
  const p: (Vec3 | null)[] = frames.map((f) => (f.b ? [f.b[0], f.b[1], f.b[2]] : null))
  const touch = new Uint8Array(n)
  for (const c of clip.contacts ?? []) if (c.f >= 0 && c.f < n) touch[c.f] = 1

  // 1. Fill held coordinates.
  for (let ax = 0; ax < 3; ax++) {
    let s = 0
    while (s < n - 1) {
      const a = p[s]
      if (!a) {
        s++
        continue
      }
      let e = s
      while (e + 1 < n && p[e + 1] && p[e + 1]![ax] === a[ax]) e++
      const next = p[e + 1]
      const runLen = e - s + 1
      if (e > s && e + 1 < n && next && runLen <= HOLD_MAX && !(ax === 2 && a[2] === 0)) {
        let touched = false
        let moving = false
        for (let k = s; k <= e + 1; k++) {
          if (touch[k]) touched = true
          for (const o of [0, 1, 2]) if (o !== ax && k > s && p[k]![o] !== p[k - 1]![o]) moving = true
        }
        if (!touched && moving) {
          for (let k = s + 1; k <= e; k++) {
            const w = (t[k] - t[s]) / (t[e + 1] - t[s])
            p[k]![ax] = a[ax] + (next[ax] - a[ax]) * w
          }
        }
      }
      s = e > s ? e : s + 1
    }
  }

  // 2. Fit quadratics between breaks: touches, bounces and gaps in the ball.
  const isBounce = (i: number) =>
    i > 0 && i < n - 1 && !!p[i] && !!p[i - 1] && !!p[i + 1] && p[i]![2] < BOUNCE_Z && p[i - 1]![2] > p[i]![2] && p[i + 1]![2] >= p[i]![2]
  const brk = new Uint8Array(n)
  for (let i = 0; i < n; i++) if (touch[i] || isBounce(i)) brk[i] = 1
  const fitted: (Vec3 | null)[] = p.map((v) => v && [...v])
  let s = 0
  while (s < n) {
    if (!p[s]) {
      s++
      continue
    }
    let e = s
    while (e + 1 < n && p[e + 1] && !(e > s && brk[e])) e++
    for (let i = s; i <= e; i++) {
      if (brk[i]) continue // keep touches and bounces exact
      const lo = Math.max(s, i - FIT_RADIUS)
      const hi = Math.min(e, i + FIT_RADIUS)
      if (hi - lo < 3) continue
      for (let ax = 0; ax < 3; ax++) {
        const v = fitAt(t, p, ax, lo, hi, i)
        fitted[i]![ax] = ax === 2 ? Math.max(v, 0) : v
      }
    }
    s = e > s ? e : s + 1
    if (e === s && !brk[e]) s++
  }
  for (let i = 0; i < n; i++) p[i] = fitted[i]

  // 3. Tangents: centred difference normally, one-sided at touches, gaps and bounces.
  const zero: Vec3 = [0, 0, 0]
  const mL: Vec3[] = []
  const mR: Vec3[] = []
  const diff = (i: number, j: number): Vec3 => {
    const a = p[i]!
    const b = p[j]!
    const dt = t[j] - t[i] || 1
    return [(b[0] - a[0]) / dt, (b[1] - a[1]) / dt, (b[2] - a[2]) / dt]
  }
  for (let i = 0; i < n; i++) {
    const cur = p[i]
    if (!cur) {
      mL.push(zero)
      mR.push(zero)
      continue
    }
    const hasPrev = i > 0 && p[i - 1] !== null
    const hasNext = i < n - 1 && p[i + 1] !== null
    const back = hasPrev ? diff(i - 1, i) : null
    const fwd = hasNext ? diff(i, i + 1) : null
    const centre = hasPrev && hasNext ? diff(i - 1, i + 1) : (back ?? fwd ?? zero)
    let l: Vec3 = touch[i] ? (back ?? centre) : centre
    let r: Vec3 = touch[i] ? (fwd ?? centre) : centre
    if (!hasPrev) l = fwd ?? zero
    if (!hasNext) r = back ?? zero
    if (back && fwd && cur[2] < BOUNCE_Z && p[i - 1]![2] > cur[2] && p[i + 1]![2] > cur[2]) {
      l = [l[0], l[1], back[2]]
      r = [r[0], r[1], fwd[2]]
    }
    mL.push(l)
    mR.push(r)
  }
  return { t, p, mL, mR }
}

// Least-squares quadratic through frames lo..hi on one axis, as coefficients of
// (time - tc): value at tc, slope, curvature. Frames where skip() is true are left out.
function fitQuad(t: number[], p: (Vec3 | null)[], ax: number, lo: number, hi: number, tc: number, skip?: (k: number) => boolean) {
  let s0 = 0, s1 = 0, s2 = 0, s3 = 0, s4 = 0, y0 = 0, y1 = 0, y2 = 0
  for (let k = lo; k <= hi; k++) {
    if (skip?.(k)) continue
    const x = t[k] - tc
    const y = p[k]![ax]
    const x2 = x * x
    s0 += 1; s1 += x; s2 += x2; s3 += x2 * x; s4 += x2 * x2
    y0 += y; y1 += y * x; y2 += y * x2
  }
  if (s0 < 4) return null
  // Cramer's rule on the 3x3 normal equations.
  const det3 = (a: number, b: number, c: number, d: number, e: number, f: number, g: number, h: number, i: number) =>
    a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
  const det = det3(s0, s1, s2, s1, s2, s3, s2, s3, s4)
  if (Math.abs(det) < 1e-12) return null
  return [
    det3(y0, s1, s2, y1, s2, s3, y2, s3, s4) / det,
    det3(s0, y0, s2, s1, y1, s3, s2, y2, s4) / det,
    det3(s0, s1, y0, s1, s2, y1, s2, s3, y2) / det,
  ]
}

// The fitted value at frame i: fit once, drop frames the fit misses by too much, fit again.
function fitAt(t: number[], p: (Vec3 | null)[], ax: number, lo: number, hi: number, i: number): number {
  const tc = t[i]
  const first = fitQuad(t, p, ax, lo, hi, tc)
  if (!first) return p[i]![ax]
  const off = (k: number) => {
    const x = t[k] - tc
    return Math.abs(first[0] + first[1] * x + first[2] * x * x - p[k]![ax]) > OUTLIER_M
  }
  const second = fitQuad(t, p, ax, lo, hi, tc, off)
  return (second ?? first)[0]
}

// Ball position at time t (pitch coordinates), or null where it's hidden.
export function sampleBall(track: BallTrack, frames: Clip['frames'], time: number): Vec3 | null {
  const i = frameIndexAt(frames, time)
  const j = Math.min(i + 1, track.p.length - 1)
  const a = track.p[i]
  const b = track.p[j]
  const h = track.t[j] - track.t[i]
  const s = h > 0 ? Math.min(Math.max((time - track.t[i]) / h, 0), 1) : 0
  if (!a || !b) return s < 0.5 ? a && [...a] : b && [...b]
  const s2 = s * s
  const s3 = s2 * s
  const h00 = 2 * s3 - 3 * s2 + 1
  const h10 = s3 - 2 * s2 + s
  const h01 = -2 * s3 + 3 * s2
  const h11 = s3 - s2
  const ma = track.mR[i]
  const mb = track.mL[j]
  return [0, 1, 2].map((k) => h00 * a[k] + h10 * h * ma[k] + h01 * b[k] + h11 * h * mb[k]) as Vec3
}
