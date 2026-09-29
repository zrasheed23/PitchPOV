// Visual review: screenshots of each clip at the moments that matter, labelled
// with what should be happening, for checking by eye (or by Claude).
//
// Usage (from web/): node scripts/visual_review.mjs CLIP [CLIP ...]   (e.g. 10517_6738451)
//        node scripts/visual_review.mjs --all
// Writes ../clips/visual_review/<clip>/NN_<camera>.png and manifest.json;
// pipeline/review_sheets.py then puts each clip on one contact sheet.
//
// Moments: every touch (StatsBomb's actor and action where it has one; not
// dribble pushes) and defensive action in the last WINDOW_S before the kick,
// the kick, and the goal-line crossing. Cameras: the broadcast path, and one
// behind the player on the ball looking at it (behind the shooter toward goal
// at the kick). Runs the dev server and the installed Google Chrome headless.

import { spawn } from 'node:child_process'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright-core'

const WEB = join(dirname(fileURLToPath(import.meta.url)), '..')
const CLIPS = join(WEB, '..', 'clips')
const OUT = join(CLIPS, 'visual_review')
const PORT = 5199
const WINDOW_S = 8
const SIDE_RAD = 0.6 // the behind camera sits this far round to one side
const SETTLE_MS = 700 // camera paths are damped: let them arrive
const PARTS = { R: 'right foot', L: 'left foot', F: 'foot', H: 'head', X: 'hands' }
const DEFENSE = { slide: 'slide tackle', slideBlock: 'sliding block', block: 'standing block' }

function moments(clip) {
  const names = Object.fromEntries(clip.players.map((p) => [p.id, p.name]))
  const kick = clip.kickFrame ?? clip.goalFrame
  const t0 = clip.frames[kick].t - WINDOW_S
  const out = []
  for (const c of clip.contacts ?? []) {
    if (c.s === 1 || c.f >= kick || clip.frames[c.f].t < t0) continue
    const what = c.sb ? c.sb.replace('*', '') : 'touch'
    out.push({ f: c.f, p: c.p, label: `${names[c.p]}, ${PARTS[c.b] ?? c.b}, ${what}` })
  }
  for (const d of clip.defense ?? []) {
    if (d.f >= kick || clip.frames[d.f].t < t0) continue
    const label = `${names[d.p]}, ${DEFENSE[d.kind]} (${d.type}${d.ok ? '' : ', fails'})`
    const same = out.find((m) => m.f === d.f && m.p === d.p) // his touch is the action
    if (same) same.label = label
    else out.push({ f: d.f, p: d.p, label })
  }
  const shot = (clip.contacts ?? []).find((c) => c.f === kick && c.p === clip.scorerId)
  const pose = clip.shotPose && clip.shotPose !== 'none' ? `, ${clip.shotPose}` : ''
  out.push({
    f: kick,
    p: clip.scorerId,
    label: `${names[clip.scorerId]}, ${PARTS[shot?.b] ?? 'foot'}, shot${pose}${clip.penalty ? ' (penalty)' : ''}`,
    shot: true,
  })
  const side = clip.frames[kick].b && clip.frames[kick].b[0] < 0 ? -1 : 1
  const cross = clip.frames.findIndex((fr, i) => i > kick && fr.b && side * fr.b[0] >= 52.5)
  if (cross > 0) {
    const kd = clip.keeperDive
    const keeper = kd ? `keeper ${names[kd.keeper]} ${kd.kind}${kd.through ? ` (beaten ${kd.through})` : ''}` : 'no keeper plan'
    out.push({ f: cross, p: clip.scorerId, label: `ball crosses the line; ${keeper}`, cross: true, from: kick })
  }
  return out.sort((a, b) => a.f - b.f)
}

// A camera behind the player, looking at the ball (at the kick: at the goal; at the crossing: from the kick spot).
function behind(clip, m) {
  const fr = clip.frames[m.f]
  const at = m.cross ? clip.frames[m.from].p[m.p] : fr.p[m.p]
  const ball = fr.b ?? [at[0], at[1], 0]
  const kick = clip.frames[clip.kickFrame ?? clip.goalFrame].b
  const side = kick && kick[0] < 0 ? -1 : 1
  let aim = m.shot || m.cross ? [side * 52.5, 0] : [ball[0], ball[1]]
  if (!m.shot && !m.cross && Math.hypot(aim[0] - at[0], aim[1] - at[1]) < 1) {
    const later = clip.frames[Math.min(m.f + 15, clip.frames.length - 1)].b
    if (later) aim = [later[0], later[1]]
  }
  let dx = aim[0] - at[0]
  let dy = aim[1] - at[1]
  const n = Math.hypot(dx, dy) || 1
  dx /= n
  dy /= n
  const back = m.cross ? 4 : 7
  // Off to one side (SIDE_RAD), so his body doesn't hide a ball at his feet.
  const bx = dx * Math.cos(SIDE_RAD) - dy * Math.sin(SIDE_RAD)
  const by = dx * Math.sin(SIDE_RAD) + dy * Math.cos(SIDE_RAD)
  // Three.js: pitch (x, y) is (x, -z), height is y.
  return {
    pos: [at[0] - bx * back, m.cross ? 2.2 : 3.4, -(at[1] - by * back)],
    target: m.shot || m.cross ? [ball[0] + dx * 4, 0.8, -(ball[1] + dy * 4)] : [ball[0], Math.max(ball[2], 0.6), -ball[1]],
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

async function main() {
  let names = process.argv.slice(2).map((a) => a.replace(/\.json$/, ''))
  if (names[0] === '--all') names = JSON.parse(readFileSync(join(CLIPS, 'index.json'), 'utf8')).map((g) => g.clip.replace(/\.json$/, ''))
  if (!names.length) throw new Error('usage: node scripts/visual_review.mjs CLIP [CLIP ...] | --all')
  const server = spawn('npx', ['vite', '--port', String(PORT), '--strictPort'], { cwd: WEB, stdio: 'ignore' })
  const started = Date.now()
  try {
    await waitForServer(`http://localhost:${PORT}/`)
    const browser = await chromium.launch({ channel: 'chrome', headless: true, args: ['--enable-unsafe-swiftshader'] })
    const page = await browser.newPage({ viewport: { width: 960, height: 540 } })
    page.on('pageerror', (e) => console.error('page error:', e.message))
    const manifest = {}
    for (const name of names) {
      const clip = JSON.parse(readFileSync(join(CLIPS, `${name}.json`), 'utf8'))
      const dir = join(OUT, name)
      mkdirSync(dir, { recursive: true })
      await page.goto(`http://localhost:${PORT}/?clip=${name}#${name}`) // the query forces a reload (the app reads the hash once)
      await page.waitForFunction((n) => window.__review?.clip === n && window.__playback && window.__view, name, { timeout: 60000 })
      await page.addStyleTag({ content: '#root > :not(:first-child) { display: none !important } #vr-label { position: fixed; left: 8px; top: 8px; z-index: 9; font: 600 17px system-ui; color: #fff; background: rgba(0,0,0,.65); padding: 4px 8px; border-radius: 4px }' })
      await page.evaluate(() => {
        window.__playback.current.playing = false
        const d = document.createElement('div')
        d.id = 'vr-label'
        document.body.appendChild(d)
      })
      const ms = moments(clip)
      const shots = []
      for (const cam of ['broadcast', 'behind']) {
        await page.evaluate((p) => window.__review.setPreset(p), cam === 'broadcast' ? 'broadcast' : null)
        for (const [i, m] of ms.entries()) {
          const t = clip.frames[m.f].t
          const pose = cam === 'behind' ? behind(clip, m) : null
          await page.evaluate(
            ({ t, label, pose }) => {
              window.__playback.current.time = t
              document.getElementById('vr-label').textContent = label
              if (pose) {
                const { camera, controls } = window.__view
                camera.position.set(...pose.pos)
                controls.target.set(...pose.target)
                camera.lookAt(...pose.target)
              }
            },
            { t, label: `${t.toFixed(2)} s · ${m.label} · ${cam}`, pose },
          )
          await page.waitForTimeout(cam === 'broadcast' ? SETTLE_MS : 250)
          const file = `${String(i).padStart(2, '0')}_${cam}.png`
          await page.screenshot({ path: join(dir, file) })
          shots.push({ i, cam, file, f: m.f, t, label: m.label })
        }
      }
      manifest[name] = { scorer: clip.scorer, clock: clip.clock, moments: ms.map((m) => ({ f: m.f, label: m.label })), shots }
      console.log(`${name} ${clip.scorer} ${clip.clock}: ${ms.length} moments`)
    }
    let all = {}
    try {
      all = JSON.parse(readFileSync(join(OUT, 'manifest.json'), 'utf8'))
    } catch {}
    writeFileSync(join(OUT, 'manifest.json'), JSON.stringify({ ...all, ...manifest }, null, 1))
    await browser.close()
    console.log(`${names.length} clips in ${((Date.now() - started) / 1000).toFixed(0)} s`)
  } finally {
    server.kill()
  }
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
