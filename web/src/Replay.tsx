import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { type RefObject, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { type Clip, attackingSide, clipDuration, sampleClip } from './clip'
import { colorDistance, kitColors } from './kit'
import { distanceAt, runDistances, velocities } from './motion'
import type { Playback } from './playback'
import { type Kit, PlayerBody } from './Player'
import { DIVE_LENGTH_S, PLAYER_HEIGHT, type Rig, animateDive, animateRig } from './rig'

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
  playback: RefObject<Playback>
  ball: RefObject<THREE.Mesh | null>
  onEnded: () => void
}

export function Replay({ clip, playback, ball, onEnded }: ReplayProps) {
  const duration = clipDuration(clip)
  const players = useRef<Record<string, THREE.Group | null>>({})
  const rigs = useRef<Record<string, Rig | null>>({})
  const yaw = useRef<Record<string, number>>({})
  const dist = useMemo(() => runDistances(clip), [clip])
  const playerKits = useMemo(() => kits(clip), [clip])
  const dive = useMemo(() => planDive(clip), [clip])
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
      if (dive && k >= 0 && k < DIVE_LENGTH_S) {
        g.rotation.y = yaw.current[id] = dive.yaw
        animateDive(rig, k, dive.side, dive.strength)
      } else {
        animateRig(rig, (distanceAt(clip, dist[id], pb.time) / STRIDE_M) * Math.PI * 2, speed)
      }
    }

    if (ball.current) {
      ball.current.visible = s.ball !== null
      if (s.ball) {
        const [x, y, z] = s.ball
        ball.current.position.set(x, Math.max(z, 0) + BALL_RADIUS, -y)
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
        <meshStandardMaterial color="white" roughness={0.35} />
      </mesh>
    </group>
  )
}
