// Demo GIF for the README: one goal, rendered frame by frame (no real-time
// recording) with the camera following the ball from behind the scorer.
//
// Usage (from web/): node scripts/capture_demo.mjs [CLIP]   (default 10517_6738451, Mbappé 81')
// Writes ../docs/demo.gif and ../docs/demo.png. Needs ffmpeg and gifsicle on the PATH (or
// FFMPEG=/path/to/ffmpeg GIFSICLE=/path/to/gifsicle). gifsicle's lossy mode keeps the GIF under
// MAX_GIF_BYTES: the moving camera changes every pixel of the grass, which plain GIF stores badly.
// Runs the dev server and the installed Google Chrome headless, like visual_review.mjs, and loads the
// viewer with ?capture so the script steps the render loop itself (see CAPTURE in src/App.tsx).

import { spawn, spawnSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..')
const CLIPS = join(WEB, '..', 'clips')
const DOCS = join(WEB, '..', 'docs')
const PORT = 5198
const FFMPEG = process.env.FFMPEG || 'ffmpeg'
const GIFSICLE = process.env.GIFSICLE || 'gifsicle'
const SIZE = { width: 1800, height: 1000 }
const FPS = 30
const LENGTH_S = 10 // ending AFTER_CROSS_S after the ball crosses the line
const AFTER_CROSS_S = 1.5
const WARMUP_FRAMES = 30 // let poses and turning settle before the first captured frame
const STILL_AFTER_KICK_S = 0.35 // demo.png: the ball on its way in
const GIF_WIDTH = 900
const GIF_FPS = 15
const GIF_COLORS = 64 // few colours: flat grass that compresses well, no visible banding
const MAX_GIF_BYTES = 8e6
const LOSSY = [60, 80, 100, 130, 160, 200] // gifsicle --lossy levels, tried in order until it fits

// The camera: behind the scorer, a little above head height, on the line from him to the ball, so
// we see what he sees as the play builds; aimed at the ball, through the shot and into the net.
// Position and aim are critically damped springs and the aim can only turn so fast, so nothing
// snaps. Pitch metres; the scorer attacks +x here (mirrored back for a goal at -x).
const BACK_M = 7.5
const HEIGHT_M = 3.2 // over every player's head (1.8 m) and the crossbar (2.44 m)
const GOAL_PULL_M = 8 // the line is pulled toward goal by this much: steady when the ball is at his feet
const SCORER_SMOOTH_S = 0.35 // Gaussian sigma on his track (strides and small corrections)
const BALL_SMOOTH_S = 0.08
const POS_SMOOTH_S = 0.7 // spring smoothing times
const AIM_SMOOTH_S = 0.3
const MAX_TURN = (60 * Math.PI) / 180 // rad/s
// Tilt: the aim tilts down from the ball, if needed, to keep his knees within MAX_OFF of the centre,
// but never so far that the ball goes more than MAX_OFF above it (the ball wins). The lens shows
// 20 degrees either side of centre vertically, so both fit.
const SCORER_LOW_M = 0.4
const MAX_OFF = (13 * Math.PI) / 180
const PREROLL_S = 3 // springs run this long before the window, so they've settled
const STEP_S = 1 / 240
const INSIDE = { x: 58, y: 38 } // inside the ad boards
const GOAL_X = 52.5

// Gaussian-smoothed track of [x, y] or [x, y, z] samples: t -> point.
function smoothTrack(pts, sigma) {
  return (t) => {
    let w = 0
    const acc = pts[0].v.map(() => 0)
    for (const p of pts) {
      const d = (p.t - t) / sigma
      if (d < -3 || d > 3) continue
      const k = Math.exp(-0.5 * d * d)
      w += k
      p.v.forEach((v, i) => (acc[i] += k * v))
    }
    return w ? acc.map((v) => v / w) : null
  }
}

// Unity-style SmoothDamp on one value; state {v} carries the velocity.
function damp(cur, goal, state, smooth, dt) {
  const o = 2 / smooth
  const x = o * dt
  const e = 1 / (1 + x + 0.48 * x * x + 0.235 * x * x * x)
  const change = cur - goal
  const temp = (state.v + o * change) * dt
  state.v = (state.v - o * temp) * e
  return goal + (change + temp) * e
}

const norm = (v) => {
  const n = Math.hypot(...v) || 1
  return v.map((c) => c / n)
}

function keepInFrame(aim, from, point, maxOff) {
  const below = Math.asin(norm(point.map((c, i) => c - from[i]))[1])
  const tilt = Math.asin(aim[1])
  const e = Math.max(Math.min(tilt, below + maxOff), tilt - maxOff)
  if (e === tilt) return aim
  const h = Math.hypot(aim[0], aim[2]) || 1
  return [(aim[0] / h) * Math.cos(e), Math.sin(e), (aim[2] / h) * Math.cos(e)]
}

// Turn unit vector a toward unit vector b by at most maxAngle.
function turnToward(a, b, maxAngle) {
  const ang = Math.acos(Math.min(Math.max(a[0] * b[0] + a[1] * b[1] + a[2] * b[2], -1), 1))
  if (ang <= maxAngle || ang < 1e-6) return b
  const k = maxAngle / ang
  return norm(a.map((c, i) => c + (b[i] - c) * k)) // close enough to slerp for the small steps here
}

function cameraPath(clip, t0, t1, side) {
  const id = clip.scorerId
  const scorer = smoothTrack(
    clip.frames.filter((f) => f.p[id]).map((f) => ({ t: f.t, v: [side * f.p[id][0], f.p[id][1]] })),
    SCORER_SMOOTH_S,
  )
  const ball = smoothTrack(
    clip.frames.filter((f) => f.b).map((f) => ({ t: f.t, v: [side * f.b[0], f.b[1], Math.max(f.b[2], 0.11)] })),
    BALL_SMOOTH_S,
  )
  const wanted = (t) => {
    const m = scorer(t)
    const b = ball(t)
    const g = norm([GOAL_X - m[0], -m[1]])
    const d = norm([b[0] - m[0] + GOAL_PULL_M * g[0], b[1] - m[1] + GOAL_PULL_M * g[1]])
    const x = Math.min(Math.max(m[0] - d[0] * BACK_M, -INSIDE.x), INSIDE.x)
    const y = Math.min(Math.max(m[1] - d[1] * BACK_M, -INSIDE.y), INSIDE.y)
    return { pos: [x, HEIGHT_M, y], look: [b[0], b[2], b[1]] } // [x, height, y]
  }
  let { pos, look } = wanted(t0 - PREROLL_S)
  const pv = pos.map(() => ({ v: 0 }))
  const lv = look.map(() => ({ v: 0 }))
  let aim = norm(look.map((c, i) => c - pos[i]))
  const poses = []
  const n = Math.round((t1 - t0 + PREROLL_S) / STEP_S)
  const every = Math.round(1 / FPS / STEP_S)
  for (let i = 0; i <= n; i++) {
    const t = t0 - PREROLL_S + i * STEP_S
    const w = wanted(t)
    pos = pos.map((c, k) => damp(c, w.pos[k], pv[k], POS_SMOOTH_S, STEP_S))
    look = look.map((c, k) => damp(c, w.look[k], lv[k], AIM_SMOOTH_S, STEP_S))
    aim = turnToward(aim, norm(look.map((c, k) => c - pos[k])), MAX_TURN * STEP_S)
    if (i % every === 0 && t >= t0 - 1e-6) {
      const m = scorer(t)
      const shown = keepInFrame(aim, pos, [m[0], SCORER_LOW_M, m[1]], MAX_OFF)
      const dist = Math.hypot(...look.map((c, k) => c - pos[k]))
      poses.push({ t, pos, target: pos.map((c, k) => c + shown[k] * dist) })
    }
  }
  // three.js: pitch (x, y) is (x, -z), height is y; mirrored back for a goal at -x.
  const three = ([x, h, y]) => [side * x, h, -y]
  return (t) => {
    const p = poses[Math.min(Math.max(Math.round((t - t0) * FPS), 0), poses.length - 1)]
    return { pos: three(p.pos), target: three(p.target) }
  }
}

async function waitForServer(url) {
  for (let i = 0; i < 100; i++) {
    try {
      if ((await fetch(url)).ok) return
    } catch {}
    await new Promise((r) => setTimeout(r, 200))
  }
  throw new Error(`dev server not up at ${url}`)
}

function ffmpeg(args) {
  const r = spawnSync(FFMPEG, ['-hide_banner', '-loglevel', 'error', '-y', ...args], { stdio: 'inherit' })
  if (r.status !== 0) throw new Error(`ffmpeg failed (${FFMPEG} ${args.join(' ')})`)
}

async function main() {
  const name = (process.argv[2] ?? '10517_6738451').replace(/\.json$/, '')
  const clip = JSON.parse(readFileSync(join(CLIPS, `${name}.json`), 'utf8'))
  const kickT = clip.frames[clip.kickFrame ?? clip.goalFrame].t
  const side = clip.frames[clip.kickFrame ?? clip.goalFrame].b[0] < 0 ? -1 : 1
  const cross = clip.frames.find((f, i) => i > (clip.kickFrame ?? clip.goalFrame) && f.b && side * f.b[0] >= 52.5)
  const end = (cross ? cross.t : kickT + 1) + AFTER_CROSS_S
  const start = end - LENGTH_S
  const frames = Math.round(LENGTH_S * FPS)
  const stillFrame = Math.round((kickT + STILL_AFTER_KICK_S - start) * FPS)
  const path = cameraPath(clip, start, end, side)
  const dir = mkdtempSync(join(tmpdir(), 'pitchpov-capture-'))
  mkdirSync(DOCS, { recursive: true })

  const server = spawn('npx', ['vite', '--port', String(PORT), '--strictPort'], { cwd: WEB, stdio: 'ignore' })
  try {
    await waitForServer(`http://localhost:${PORT}/`)
    const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-unsafe-swiftshader'] })
    const page = await browser.newPage({ viewport: SIZE, deviceScaleFactor: 1 })
    page.on('pageerror', (e) => console.error('page error:', e.message))
    await page.goto(`http://localhost:${PORT}/?capture#${name}`)
    await page.waitForFunction((n) => window.__capture?.clip === n && window.__capture.state()?.controls, name, {
      timeout: 60000,
    })
    // Only the 3D view: hide the panel, camera buttons and playback controls.
    await page.addStyleTag({ content: '#root > :not(:first-child) { display: none !important }' })
    await page.evaluate(() => window.__capture.handOver())
    await page.waitForTimeout(200)
    const base = await page.evaluate(
      ({ t }) => {
        const pb = window.__capture.playback
        Object.assign(pb, { time: t, playing: true, speed: 1, scrubbing: false })
        return window.__capture.state().clock.elapsedTime
      },
      { t: start - WARMUP_FRAMES / FPS },
    )
    for (let i = -WARMUP_FRAMES; i < frames; i++) {
      const n = i + WARMUP_FRAMES + 1
      const t = start + (i + 1) / FPS // the clip time this frame shows
      const pose = path(Math.max(t, start))
      await page.evaluate(
        ({ ts, pose }) => {
          const { camera, controls } = window.__capture.state()
          camera.position.set(...pose.pos)
          controls.target.set(...pose.target)
          camera.lookAt(...pose.target)
          window.__capture.advance(ts)
        },
        { ts: base + n / FPS, pose },
      )
      if (i >= 0) await page.screenshot({ path: join(dir, `f${String(i).padStart(4, '0')}.png`) })
    }
    await browser.close()
  } finally {
    server.kill()
  }

  const input = ['-framerate', String(FPS), '-i', join(dir, 'f%04d.png')]
  const scale = `fps=${GIF_FPS},scale=${GIF_WIDTH}:-1:flags=lanczos`
  ffmpeg([...input, '-vf', `${scale},palettegen=max_colors=${GIF_COLORS}:stats_mode=diff`, join(dir, 'palette.png')])
  const raw = join(dir, 'raw.gif')
  ffmpeg([
    ...input,
    '-i', join(dir, 'palette.png'),
    '-lavfi', `${scale}[x];[x][1:v]paletteuse=dither=none:diff_mode=rectangle`,
    raw,
  ])
  const gif = join(DOCS, 'demo.gif')
  let lossy
  for (lossy of LOSSY) {
    const r = spawnSync(GIFSICLE, ['-O3', `--lossy=${lossy}`, raw, '-o', gif], { stdio: 'inherit' })
    if (r.status !== 0) throw new Error(`gifsicle failed (${GIFSICLE})`)
    if (statSync(gif).size <= MAX_GIF_BYTES) break
  }
  ffmpeg(['-i', join(dir, `f${String(stillFrame).padStart(4, '0')}.png`), join(DOCS, 'demo.png')])
  const mb = (statSync(gif).size / 1e6).toFixed(1)
  console.log(`${clip.scorer} ${clip.clock}: ${start.toFixed(2)}-${end.toFixed(2)} s, ${frames} frames -> docs/demo.gif ` +
    `(${mb} MB, lossy ${lossy}), docs/demo.png at ${(kickT + STILL_AFTER_KICK_S).toFixed(2)} s`)
  if (process.env.KEEP_FRAMES) console.log(`frames kept in ${dir}`)
  else rmSync(dir, { recursive: true })
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
