import { useFrame } from '@react-three/fiber'
import { type RefObject, useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'
import { type Clip, clipDuration, sampleClip } from './clip'

const PLAYER_HEIGHT = 1.8
const PLAYER_RADIUS = 0.35
const BALL_RADIUS = 0.2 // a bit larger than a real ball (0.11) so it reads from afar
const LABEL_Y = PLAYER_HEIGHT + 0.7

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

function PlayerLabel({ number }: { number: number }) {
  const texture = useMemo(() => numberTexture(number), [number])
  useEffect(() => () => texture.dispose(), [texture])
  return (
    <sprite position={[0, LABEL_Y, 0]} scale={[1.6, 1.6, 1]}>
      <spriteMaterial map={texture} depthWrite={false} />
    </sprite>
  )
}

interface ReplayProps {
  clip: Clip
  timeLabel: RefObject<HTMLSpanElement | null>
}

export function Replay({ clip, timeLabel }: ReplayProps) {
  const duration = clipDuration(clip)
  const time = useRef(0)
  const ball = useRef<THREE.Mesh>(null)
  const players = useRef<Record<string, THREE.Group | null>>({})

  useFrame((_, delta) => {
    time.current = (time.current + delta) % duration
    const s = sampleClip(clip, time.current)

    for (const id in s.players) {
      const g = players.current[id]
      if (!g) continue
      const [x, y] = s.players[id]
      g.position.set(x, 0, -y)
    }

    if (ball.current) {
      ball.current.visible = s.ball !== null
      if (s.ball) {
        const [x, y, z] = s.ball
        ball.current.position.set(x, Math.max(z, 0) + BALL_RADIUS, -y)
      }
    }

    if (timeLabel.current) {
      timeLabel.current.textContent = `${time.current.toFixed(1)} / ${duration.toFixed(1)} s`
    }
  })

  return (
    <group>
      {clip.players.map((p) => (
        <group
          key={p.id}
          ref={(g) => {
            players.current[p.id] = g
          }}
        >
          <mesh position={[0, PLAYER_HEIGHT / 2, 0]}>
            <cylinderGeometry args={[PLAYER_RADIUS, PLAYER_RADIUS, PLAYER_HEIGHT, 20]} />
            <meshStandardMaterial color={clip.teams[p.team].color} />
          </mesh>
          <PlayerLabel number={p.number} />
        </group>
      ))}
      <mesh ref={ball}>
        <sphereGeometry args={[BALL_RADIUS, 20, 16]} />
        <meshStandardMaterial color="white" />
      </mesh>
    </group>
  )
}
