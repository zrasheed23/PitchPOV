// Demo GIF for the README: one goal, rendered frame by frame (no real-time
// recording) with the camera orbiting from the broadcast side to behind the goal.
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
const SMOOTH_S = 0.7 // the camera aims at the ball's path smoothed this much (Gaussian sigma)
const STILL_AFTER_KICK_S = 0.35 // demo.png: the ball on its way in
const GIF_WIDTH = 900
const GIF_FPS = 15
const GIF_COLORS = 64 // few colours: flat grass that compresses well, no visible banding
const MAX_GIF_BYTES = 8e6
const LOSSY = [60, 80, 100, 130, 160, 200] // gifsicle --lossy levels, tried in order until it fits

// The orbit: from the broadcast side (offset toward +z, the near touchline) round to behind the
// attacked goal, arriving just before the kick. Distances and heights in metres. After the kick the
// camera holds still and its aim eases onto the goal mouth over SETTLE_S.
const ORBIT = { az: [Math.PI / 2, 0.32], dist: [40, 25], height: [22, 8.5] }
const TARGET_MAX_X = 40 // keep the aim in front of the goal so the camera stays inside the stadium
const CAMERA_MAX_X = 63 // pitch x: short of the stand behind the goal
const CAMERA_MAX_Y = 52 // pitch |y|: short of the side stands
const START_CENTRE = 0.5 // at the start the aim is pulled this far from the ball toward the middle
const GOAL_AIM_X = 46
const SETTLE_S = 1

const smootherstep = (k) => {
  const x = Math.min(Math.max(k, 0), 1)
  return x * x * x * (x * (x * 6 - 15) + 10)
}
const lerp = (a, b, k) => a + (b - a) * k

// Ball path on the pitch (x, y), Gaussian-smoothed, sampled at time t. side: +1 attacking +x.
function smoothedBall(clip, side) {
  const pts = clip.frames.filter((f) => f.b).map((f) => ({ t: f.t, x: side * f.b[0], y: f.b[1] }))
  return (t) => {
    let w = 0, x = 0, y = 0
    for (const p of pts) {
      const d = (p.t - t) / SMOOTH_S
      if (d < -3 || d > 3) continue
      const k = Math.exp(-0.5 * d * d)
      w += k
      x += k * p.x
      y += k * p.y
    }
    return { x: x / w, y: y / w }
  }
}

function cameraPath(clip, t0, kickT, side, crossY) {
  const ball = smoothedBall(clip, side)
  const t1 = kickT - 0.25
  // Pitch coordinates for a goal at +x: [x, height, y].
  const orbit = (t) => {
    const k = smootherstep((t - t0 - 0.3) / (t1 - t0 - 0.3))
    const b = ball(t)
    const centre = lerp(START_CENTRE, 1, k)
    const tx = Math.min(b.x, TARGET_MAX_X)
    const ty = b.y * centre
    const az = lerp(ORBIT.az[0], ORBIT.az[1], k)
    const dist = lerp(ORBIT.dist[0], ORBIT.dist[1], k)
    const cx = Math.min(tx + Math.cos(az) * dist, CAMERA_MAX_X)
    const cy = Math.max(ty - Math.sin(az) * dist, -CAMERA_MAX_Y)
    return { pos: [cx, lerp(ORBIT.height[0], ORBIT.height[1], k), cy], target: [tx, 1, ty] }
  }
  const atKick = orbit(kickT)
  const goal = [GOAL_AIM_X, 0.8, crossY]
  return (t) => {
    let p = orbit(t)
    if (t > kickT) {
      const k = smootherstep((t - kickT) / SETTLE_S)
      p = { pos: atKick.pos, target: atKick.target.map((v, i) => lerp(v, goal[i], k)) }
    }
    // three.js: pitch (x, y) is (x, -z), height is y; mirrored back for a goal at -x.
    const three = ([x, h, y]) => [side * x, h, -y]
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
  const path = cameraPath(clip, start, kickT, side, cross ? cross.b[1] : 0)
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
