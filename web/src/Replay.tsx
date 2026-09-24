import { Html } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { type RefObject, useEffect, useMemo, useRef, useState } from 'react'
import * as THREE from 'three'
import { type Clip, clipDuration, sampleClip } from './clip'
import { kitColors } from './kit'
import type { Playback } from './playback'

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
  const [hovered, setHovered] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const labelled = hovered ?? selected
  const colors = useMemo(() => kitColors(clip.teams), [clip])

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
  })

  return (
    // Any click that misses every player (pitch or sky) clears the selection.
    <group onPointerMissed={() => setSelected(null)}>
      {clip.players.map((p) => (
        <group
          key={p.id}
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
          <mesh position={[0, PLAYER_HEIGHT / 2, 0]}>
            <cylinderGeometry args={[PLAYER_RADIUS, PLAYER_RADIUS, PLAYER_HEIGHT, 20]} />
            <meshStandardMaterial color={colors[p.team]} />
          </mesh>
          <mesh position={[0, PLAYER_HEIGHT / 2 + 0.3, 0]}>
            <cylinderGeometry args={[1, 1, PLAYER_HEIGHT + 0.6, 8]} />
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
      <mesh ref={ball}>
        <sphereGeometry args={[BALL_RADIUS, 20, 16]} />
        <meshStandardMaterial color="white" />
      </mesh>
    </group>
  )
}
