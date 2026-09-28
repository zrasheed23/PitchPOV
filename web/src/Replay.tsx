import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { type RefObject, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { type BallTrack, sampleBall } from './ballTrack'
import { type Clip, attackingSide, clipDuration, frameIndexAt, sampleClip } from './clip'
import { colorDistance, kitColors } from './kit'
import { distanceAt, runDistances, velocities } from './motion'
import type { Playback } from './playback'
import { type Kit, PlayerBody } from './Player'
import { DIVE_LENGTH_S, PLAYER_HEIGHT, THROW_AFTER_S, type Rig, animateDive, animateRig, animateThrow, animateTouch } from './rig'

const TOUCH_WINDOW_S = 0.25 // a touch is animated this long either side
const TOUCH_PULL_S = 0.12 // the ball is shifted onto the foot/head this long either side
const MAX_PULL_M = 1.5 // ...but only by up to this much
const touchOffset = new THREE.Vector3()
// Carry fallback: where the pipeline couldn't rebuild a dribble as pushes, the
// ball is held just in front of the player's feet, easing out and back in with
// each stride, instead of drawing the tracked ball wandering around him.
const CARRY_BLEND_S = 0.15
const CARRY_AHEAD_M = [0.4, 0.75] // nearest and farthest from him over a stride
const carryPoint = new THREE.Vector3()
const touchPoint = new THREE.Vector3()
const ballPoint = new THREE.Vector3()
const smooth = (x: number) => (x <= 0 ? 0 : x >= 1 ? 1 : x * x * (3 - 2 * x))
// A football texture: white with dark panels, so you can see it roll.
function ballTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = 256
  c.height = 128
  const ctx = c.getContext('2d')!
  ctx.fillStyle = '#f4f4f4'
  ctx.fillRect(0, 0, 256, 128)
  ctx.fillStyle = '#1b1b1b'
  const spots: [number, number, number][] = [
    [32, 64, 14], [96, 30, 12], [96, 98, 12], [160, 64, 14], [224, 30, 12], [224, 98, 12], [128, 8, 10], [128, 120, 10],
  ]
  for (const [x, y, r] of spots) {
    ctx.beginPath()
    for (let i = 0; i < 5; i++) {
      const a = (i / 5) * Math.PI * 2 - Math.PI / 2
      ctx.lineTo(x + Math.cos(a) * r, y + Math.sin(a) * r)
    }
    ctx.closePath()
    ctx.fill()
  }
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}
const rollAxis = new THREE.Vector3()
// On the ground the ball turns exactly as fast as it rolls. In the air nothing
// drives it, so it keeps a gentle spin that slowly dies away, instead of the
// fast "rolling in mid-air" spin a shot or cross used to get.
const ON_GROUND_M = 0.06 // ball bottom this close to the grass counts as rolling
const AIR_SPIN_MAX = 6 // rad/s
const AIR_SPIN_DECAY = 0.6 // per second
const ESTIMATED_BALL_OPACITY = 0.55 // ball drawn this faded where its position is estimated
const BALL_RADIUS = 0.16 // a bit larger than a real ball (0.11) so it reads from afar
const LABEL_Y = PLAYER_HEIGHT + 0.75
const STRIDE_M = 2.4 // metres per full running cycle (two steps)
const TURN_DAMPING = 10
const FACE_BALL_MPS = 1.2 // slower than this, a player turns to watch the ball
const GK_SHIRTS = { home: '#e8e14a', away: '#35c46a' }

// Shirt, shorts and socks for each side, plus goalkeepers in their own colour.
function kits(clip: Clip): Record<string, Kit> {
  const shirts = kitColors(clip.teams)
  const out: Record<string, Kit> = {}
  for (const side of ['home', 'away'] as const) {
    const team = clip.teams[side]
    const shirt = shirts[side]
    const shorts = team.secondaryColor && colorDistance(team.secondaryColor, shirt) > 10 ? team.secondaryColor : shirt
    const readable = (c: string, on: string) => (c && colorDistance(c, on) > 45 ? c : colorDistance('#ffffff', on) > 45 ? '#ffffff' : '#111111')
    out[side] = { shirt, shorts, socks: shirt, number: readable(team.textColor, shirt) }
    const gk = GK_SHIRTS[side]
    out[`${side}-gk`] = { shirt: gk, shorts: gk, socks: gk, number: readable('#111111', gk) }
  }
  return out
}

// The tracking only has positions, so a keeper's dive is inferred from where
// the ball goes in: if it crosses the line more than ~1 m to one side of the
// conceding keeper, and he's near his line, he dives that way just before it
// gets there. A ball straight at him, or a keeper far off his line, gets no dive.
interface Dive {
  keeperId: string
  start: number // clip time of take-off
  yaw: number // facing, frozen for the dive
  side: 1 | -1 // toward the keeper's right (+1) or left (-1)
  strength: number // 0..1
}

const DIVE_MIN_LATERAL_M = 0.9
const DIVE_MAX_OFF_LINE_M = 9
const REACTION_S = 0.2

function planDive(clip: Clip): Dive | null {
  const { frames, goalFrame } = clip
  const side = attackingSide(clip)
  const lineX = side * 52.5
  const keepers = clip.players.filter((p) => p.position === 'GK')
  const atShot = frames[goalFrame].p
  const keeper = keepers
    .filter((p) => atShot[p.id])
    .sort((a, b) => Math.abs(atShot[a.id][0] - lineX) - Math.abs(atShot[b.id][0] - lineX))[0]
  if (!keeper) return null
  const cross = frames.findIndex((f, i) => i >= goalFrame && f.b !== null && side * f.b[0] >= 52.5)
  if (cross < 0) return null
  const ball = frames[cross].b!
  const k = frames[cross].p[keeper.id] ?? atShot[keeper.id]
  const lateral = ball[1] - k[1] // pitch y
  if (Math.abs(lateral) < DIVE_MIN_LATERAL_M || Math.abs(lineX - k[0]) > DIVE_MAX_OFF_LINE_M) return null

  // Face the ball at the moment of the shot.
  const shotBall = frames[goalFrame].b ?? ball
  const pk = atShot[keeper.id]
  const dx = shotBall[0] - pk[0]
  const dz = -(shotBall[1] - pk[1])
  const yaw = Math.atan2(-dx, -dz)
  // His right in world space is (cos yaw, 0, -sin yaw); the ball's side is pitch y = three -z.
  const toward = Math.sin(yaw) * Math.sign(lateral)
  return {
    keeperId: keeper.id,
    start: Math.max(clip.goalT + REACTION_S, frames[cross].t - 0.4),
    yaw,
    side: toward >= 0 ? 1 : -1,
    strength: Math.min(Math.max((Math.abs(lateral) - DIVE_MIN_LATERAL_M) / 2.5, 0.35), 1),
  }
}

// Shortest signed difference between two angles.
function angleDelta(from: number, to: number) {
  return Math.atan2(Math.sin(to - from), Math.cos(to - from))
}

// Jersey number drawn onto a canvas; sprites always face the camera.
function numberTexture(n: number): THREE.CanvasTexture {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 128
  const ctx = canvas.getContext('2d')!
  ctx.font = 'bold 88px system-ui, sans-serif'
  ctx.textAlign = 'center'
  ctx.textBaseline = 'middle'
  ctx.lineWidth = 12
  ctx.strokeStyle = 'black'
  ctx.strokeText(String(n), 64, 68)
  ctx.fillStyle = 'white'
  ctx.fillText(String(n), 64, 68)
  const tex = new THREE.CanvasTexture(canvas)
  tex.colorSpace = THREE.SRGBColorSpace
  return tex
}

// Floating number for reading the play from far away; fades out up close,
// where the number on the shirt takes over.
const LABEL_FADE_NEAR = 22
const LABEL_FADE_FAR = 40
const labelPos = new THREE.Vector3()

function PlayerLabel({ number }: { number: number }) {
  const texture = useMemo(() => numberTexture(number), [number])
  useEffect(() => () => texture.dispose(), [texture])
  const sprite = useRef<THREE.Sprite>(null)
  useFrame(({ camera }) => {
    const sp = sprite.current
    if (!sp) return
    const d = camera.position.distanceTo(sp.getWorldPosition(labelPos))
    const k = Math.min(Math.max((d - LABEL_FADE_NEAR) / (LABEL_FADE_FAR - LABEL_FADE_NEAR), 0), 1)
    sp.material.opacity = k
    sp.visible = k > 0.01
  })
  return (
    <sprite ref={sprite} position={[0, LABEL_Y, 0]} scale={[1.1, 1.1, 1]}>
      <spriteMaterial map={texture} depthWrite={false} transparent />
    </sprite>
  )
}

// Invisible, wider hit area so thin cylinders are easy to hover and tap.
const hitMaterial = <meshBasicMaterial transparent opacity={0} depthWrite={false} />

interface ReplayProps {
  clip: Clip
  track: BallTrack
  playback: RefObject<Playback>
  ball: RefObject<THREE.Mesh | null>
  onEnded: () => void
}

export function Replay({ clip, track, playback, ball, onEnded }: ReplayProps) {
  const duration = clipDuration(clip)
  const players = useRef<Record<string, THREE.Group | null>>({})
  const rigs = useRef<Record<string, Rig | null>>({})
  const yaw = useRef<Record<string, number>>({})
  const dist = useMemo(() => runDistances(clip), [clip])
  const playerKits = useMemo(() => kits(clip), [clip])
  const dive = useMemo(() => planDive(clip), [clip])
  const ballTex = useMemo(() => ballTexture(), [])
  useEffect(() => () => ballTex.dispose(), [ballTex])
  const lastBall = useRef<THREE.Vector3 | null>(null)
  const spin = useRef({ axis: new THREE.Vector3(1, 0, 0), rate: 0 })
  // Touches with their clip times, and which way each player faces for it:
  // toward where the ball goes next, or where it came from if it stops there.
  const touches = useMemo(
    () =>
      (clip.contacts ?? []).map((c) => {
        const t = clip.frames[c.f].t
        const at = sampleBall(track, clip.frames, t)
        const next = sampleBall(track, clip.frames, Math.min(t + 0.25, duration))
        const prev = sampleBall(track, clip.frames, Math.max(t - 0.25, 0))
        let dir: [number, number] | null = null
        if (at && next && Math.hypot(next[0] - at[0], next[1] - at[1]) > 0.8) dir = [next[0] - at[0], next[1] - at[1]]
        else if (at && prev) dir = [prev[0] - at[0], prev[1] - at[1]]
        return { ...c, t, dir, at }
      }),
    [clip, track, duration],
  )
  // Frames where the pipeline estimated the ball; it's drawn slightly faded there.
  const estimated = useMemo(() => {
    const flags = new Uint8Array(clip.frames.length)
    for (const [a, b] of clip.ballEstimated ?? []) flags.fill(1, a, b + 1)
    return flags
  }, [clip])
  const carries = useMemo(
    () => (clip.carries ?? []).map(([a, b, p]) => ({ t0: clip.frames[a].t, t1: clip.frames[b].t, p })),
    [clip],
  )
  // Throw-ins: the thrower lifts the ball over his head, holds it, throws it,
  // facing where it goes.
  const throws = useMemo(
    () =>
      (clip.restarts ?? []).flatMap((r) => {
        if (r.type !== 'T' || !r.hold) return []
        const t = clip.frames[r.f].t
        const at = sampleBall(track, clip.frames, t)
        const next = sampleBall(track, clip.frames, Math.min(t + 0.3, duration))
        const dir: [number, number] = at && next ? [next[0] - at[0], next[1] - at[1]] : [0, 0]
        return [{ p: r.p, t, hold: t - clip.frames[r.hold[0]].t, dir }]
      }),
    [clip, track, duration],
  )
  const [hovered, setHovered] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const labelled = hovered ?? selected

  useEffect(() => {
    document.body.style.cursor = hovered ? 'pointer' : ''
  }, [hovered])

  useFrame((_, delta) => {
    const pb = playback.current
    if (pb.playing && !pb.scrubbing) {
      // Cap delta so returning to a background tab doesn't skip ahead.
      pb.time += Math.min(delta, 0.1) * pb.speed
      if (pb.time >= duration) {
        pb.time = duration
        pb.playing = false
        onEnded()
      }
    }
    const s = sampleClip(clip, pb.time)
    s.ball = sampleBall(track, clip.frames, pb.time)

    // The touch nearest to now, if one is within the window.
    let touch: (typeof touches)[number] | null = null
    for (const c of touches) {
      if (Math.abs(pb.time - c.t) < TOUCH_WINDOW_S && (!touch || Math.abs(pb.time - c.t) < Math.abs(pb.time - touch.t))) touch = c
    }
    let touchSide: 1 | -1 = 1

    const vel = velocities(clip, pb.time)
    const turn = 1 - Math.exp(-TURN_DAMPING * Math.min(delta, 0.1))
    for (const id in s.players) {
      const g = players.current[id]
      if (!g) continue
      const [x, y] = s.players[id]
      g.position.set(x, 0, -y)

      // Face where they're running; when nearly still, turn to watch the ball.
      // The model faces -z, and pitch y is three -z.
      const [vx, vy] = vel[id] ?? [0, 0]
      const speed = Math.hypot(vx, vy)
      let dx = vx
      let dz = -vy
      if (speed < FACE_BALL_MPS && s.ball) {
        dx = s.ball[0] - x
        dz = -(s.ball[1] - y)
      }
      const current = yaw.current[id] ?? Math.atan2(-dx, -dz)
      const target = dx || dz ? Math.atan2(-dx, -dz) : current
      yaw.current[id] = current + angleDelta(current, target) * (pb.scrubbing || !pb.playing ? 1 : turn)
      g.rotation.y = yaw.current[id]

      const rig = rigs.current[id]
      if (!rig) continue
      const k = dive && dive.keeperId === id ? pb.time - dive.start : -1
      const th = throws.find((w) => w.p === id && pb.time > w.t - w.hold - 0.3 && pb.time < w.t + THROW_AFTER_S)
      if (dive && k >= 0 && k < DIVE_LENGTH_S) {
        g.rotation.y = yaw.current[id] = dive.yaw
        animateDive(rig, k, dive.side, dive.strength)
      } else if (th) {
        if (th.dir[0] || th.dir[1]) g.rotation.y = yaw.current[id] = Math.atan2(-th.dir[0], th.dir[1])
        animateRig(rig, 0, 0)
        animateThrow(rig, pb.time - th.t, th.hold)
      } else {
        animateRig(rig, (distanceAt(clip, dist[id], pb.time) / STRIDE_M) * Math.PI * 2, speed)
        if (touch && touch.p === id) {
          const k = pb.time - touch.t
          const e = 1 - (k / TOUCH_WINDOW_S) ** 2
          // Turn to play the ball.
          if (touch.dir) {
            const want = Math.atan2(-touch.dir[0], touch.dir[1])
            g.rotation.y = yaw.current[id] + angleDelta(yaw.current[id], want) * e
          }
          // Which foot: the one PFF logged, else the one on the ball's side.
          touchSide = touch.b === 'L' ? -1 : 1
          if (touch.b === 'F' && s.ball) {
            const rx = Math.cos(g.rotation.y)
            const rz = -Math.sin(g.rotation.y)
            touchSide = (s.ball[0] - x) * rx + -(s.ball[1] - y) * rz >= 0 ? 1 : -1
          }
          animateTouch(rig, k, TOUCH_WINDOW_S, touch.b, touchSide)
        }
      }
    }

    if (ball.current) {
      ball.current.visible = s.ball !== null
      const mat = ball.current.material as THREE.MeshStandardMaterial
      mat.opacity = estimated[frameIndexAt(clip.frames, pb.time)] ? ESTIMATED_BALL_OPACITY : 1
      if (s.ball) {
        const [x, y, z] = s.ball
        ball.current.position.set(x, Math.max(z, 0) + BALL_RADIUS, -y)
      }

      const carry = carries.find((c) => pb.time > c.t0 - CARRY_BLEND_S && pb.time < c.t1 + CARRY_BLEND_S)
      const carrier = carry && players.current[carry.p]
      if (carry && carrier) {
        const w = smooth(Math.min(pb.time - (carry.t0 - CARRY_BLEND_S), carry.t1 + CARRY_BLEND_S - pb.time) / CARRY_BLEND_S)
        const stride = (distanceAt(clip, dist[carry.p], pb.time) / STRIDE_M) % 1
        const ahead = CARRY_AHEAD_M[0] + (CARRY_AHEAD_M[1] - CARRY_AHEAD_M[0]) * Math.sin(stride * Math.PI)
        carryPoint.set(
          carrier.position.x - Math.sin(carrier.rotation.y) * ahead,
          BALL_RADIUS,
          carrier.position.z - Math.cos(carrier.rotation.y) * ahead,
        )
        ball.current.position.lerp(carryPoint, w)
        ball.current.visible = true
      }

      // Roll the ball: turn it by the distance it moved over its radius.
      const now = ball.current.position
      const prev = lastBall.current
      if (prev) {
        const dx = now.x - prev.x
        const dz = now.z - prev.z
        const d = Math.hypot(dx, dz)
        const dt = Math.min(delta, 0.1) * (pb.playing && !pb.scrubbing ? pb.speed : 0)
        const sp = spin.current
        if (d > 1e-4 && d < 3 && now.y - BALL_RADIUS < ON_GROUND_M) {
          rollAxis.set(dz, 0, -dx).normalize()
          ball.current.rotateOnWorldAxis(rollAxis, d / BALL_RADIUS)
          sp.axis.copy(rollAxis)
          sp.rate = dt > 0 ? Math.min(d / BALL_RADIUS / dt, AIR_SPIN_MAX) : sp.rate
        } else if (d > 1e-4 && d < 3) {
          if (sp.rate === 0) sp.axis.set(dz, 0, -dx).normalize()
          sp.rate = Math.max(sp.rate, 2) * Math.exp(-AIR_SPIN_DECAY * dt)
          ball.current.rotateOnWorldAxis(sp.axis, sp.rate * dt)
        }
        prev.copy(now)
      } else {
        lastBall.current = now.clone()
      }

      // Put the ball on the foot, head or hands of whoever touches it, so the
      // contact is visible. The ball is shifted by the gap between the foot and
      // where the ball was at the touch, fading out either side, so it keeps its
      // real speed instead of being dragged back to the foot.
      const pull = touch && Math.abs(pb.time - touch.t) < TOUCH_PULL_S ? touch : null
      const g = pull && players.current[pull.p]
      const rig = pull && rigs.current[pull.p]
      if (pull && g && rig) {
        g.updateMatrixWorld(true)
        const fx = -Math.sin(g.rotation.y)
        const fz = -Math.cos(g.rotation.y)
        if (pull.b === 'H') {
          rig.head.getWorldPosition(touchPoint)
          touchPoint.y += 0.11 + BALL_RADIUS
        } else if (pull.b === 'X') {
          touchPoint.set(g.position.x + fx * 0.45, 1.35, g.position.z + fz * 0.45)
        } else {
          ;(touchSide > 0 ? rig.footR : rig.footL).getWorldPosition(touchPoint)
          touchPoint.x += fx * 0.16
          touchPoint.z += fz * 0.16
          touchPoint.y = Math.max(touchPoint.y, 0) + BALL_RADIUS * 0.8
        }
        const w = smooth(1 - Math.abs(pb.time - pull.t) / TOUCH_PULL_S)
        if (s.ball && pull.at) {
          ballPoint.set(pull.at[0], Math.max(pull.at[2], 0) + BALL_RADIUS, -pull.at[1])
          touchOffset.subVectors(touchPoint, ballPoint)
          // A touch whose ball is metres away is mistimed: never drag the ball back to the foot.
          if (touchOffset.length() <= MAX_PULL_M) ball.current.position.addScaledVector(touchOffset, w)
        } else {
          ballPoint.copy(s.ball ? ball.current.position : touchPoint)
          ball.current.position.lerpVectors(ballPoint, touchPoint, w)
        }
        ball.current.visible = true
      }
    }
  })

  return (
    // Any click that misses every player (pitch or sky) clears the selection.
    <group onPointerMissed={() => setSelected(null)}>
      {clip.players.map((p) => (
        <group
          key={p.id}
          name={`player-${p.id}`}
          userData={{ playerId: p.id }}
          ref={(g) => {
            players.current[p.id] = g
          }}
          onPointerOver={(e) => {
            e.stopPropagation()
            if (e.pointerType === 'mouse') setHovered(p.id)
          }}
          onPointerOut={() => setHovered((h) => (h === p.id ? null : h))}
          onClick={(e) => {
            e.stopPropagation()
            setSelected(p.id)
          }}
        >
          <PlayerBody
            kit={playerKits[p.position === 'GK' ? `${p.team}-gk` : p.team]}
            number={p.number}
            rigRef={(r) => {
              rigs.current[p.id] = r
            }}
          />
          <mesh position={[0, PLAYER_HEIGHT / 2 + 0.3, 0]}>
            <cylinderGeometry args={[0.8, 0.8, PLAYER_HEIGHT + 0.6, 8]} />
            {hitMaterial}
          </mesh>
          <PlayerLabel number={p.number} />
          {labelled === p.id && (
            <Html position={[0, LABEL_Y + 1.1, 0]} center className="player-name">
              {p.name}
            </Html>
          )}
        </group>
      ))}
      <mesh ref={ball} castShadow>
        <sphereGeometry args={[BALL_RADIUS, 24, 16]} />
        <meshStandardMaterial map={ballTex} roughness={0.35} transparent />
      </mesh>
    </group>
  )
}
