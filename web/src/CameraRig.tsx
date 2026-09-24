import { useFrame, useThree } from '@react-three/fiber'
import { type RefObject, useEffect, useRef } from 'react'
import * as THREE from 'three'
import type { Preset, View } from './camera'

const TRANSITION_S = 0.6
const FOLLOW_OFFSET = new THREE.Vector3(0, 22, 30) // high, on the near touchline side
const FOLLOW_DAMPING = 6
const DRAG_PX = 4

// The only part of drei's OrbitControls the rig touches.
interface Controls {
  target: THREE.Vector3
}

const easeInOut = (k: number) => (k < 0.5 ? 4 * k * k * k : 1 - (-2 * k + 2) ** 3 / 2)

function presetPose(preset: Preset, side: 1 | -1, ball: THREE.Vector3, pos: THREE.Vector3, target: THREE.Vector3) {
  switch (preset) {
    case 'broadcast':
      pos.set(0, 40, 70)
      target.set(0, 0, 0)
      break
    case 'top':
      // Tiny z offset keeps lookAt well defined when looking straight down.
      pos.set(0, 95, 0.01)
      target.set(0, 0, 0)
      break
    case 'goal':
      pos.set(side * 72, 10, 0)
      target.set(side * 30, 0, 0)
      break
    case 'follow':
      target.copy(ball)
      pos.copy(ball).add(FOLLOW_OFFSET)
      break
  }
}

interface CameraRigProps {
  view: View
  attackSide: 1 | -1
  ball: RefObject<THREE.Mesh | null>
  onManual: () => void
}

// Moves the camera between presets and tracks the ball in follow mode.
// OrbitControls (makeDefault) owns the camera whenever the rig isn't driving it.
export function CameraRig({ view, attackSide, ball, onManual }: CameraRigProps) {
  const camera = useThree((s) => s.camera)
  const gl = useThree((s) => s.gl)
  const controls = useThree((s) => s.controls) as unknown as Controls | null

  const active = useRef<Preset | null>(null)
  const transition = useRef<{ elapsed: number; fromPos: THREE.Vector3; fromTarget: THREE.Vector3 } | null>(null)
  const lastBall = useRef(new THREE.Vector3())
  const pos = useRef(new THREE.Vector3())
  const target = useRef(new THREE.Vector3())

  useEffect(() => {
    active.current = view.preset
    if (!view.preset || !controls) return
    transition.current = { elapsed: 0, fromPos: camera.position.clone(), fromTarget: controls.target.clone() }
  }, [view, camera, controls])

  // A drag or wheel on the canvas hands the camera back to the user.
  useEffect(() => {
    const el = gl.domElement
    let down: { x: number; y: number } | null = null
    const manual = () => {
      transition.current = null
      active.current = null
      onManual()
    }
    const onDown = (e: PointerEvent) => {
      down = { x: e.clientX, y: e.clientY }
    }
    const onMove = (e: PointerEvent) => {
      if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) > DRAG_PX) {
        down = null
        manual()
      }
    }
    const onUp = () => {
      down = null
    }
    el.addEventListener('pointerdown', onDown)
    el.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    el.addEventListener('wheel', manual, { passive: true })
    return () => {
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      el.removeEventListener('wheel', manual)
    }
  }, [gl, onManual])

  useFrame((_, delta) => {
    if (!controls) return
    const b = ball.current
    if (b?.visible) lastBall.current.set(b.position.x, 0, b.position.z) // null ball keeps the last spot

    const preset = active.current
    const t = transition.current
    if (!preset || (!t && preset !== 'follow')) return

    presetPose(preset, attackSide, lastBall.current, pos.current, target.current)
    if (t) {
      t.elapsed += delta
      const k = easeInOut(Math.min(t.elapsed / TRANSITION_S, 1))
      camera.position.lerpVectors(t.fromPos, pos.current, k)
      controls.target.lerpVectors(t.fromTarget, target.current, k)
      if (t.elapsed >= TRANSITION_S) transition.current = null
    } else {
      // Follow: ease toward the ball so scrubbing doesn't snap the camera.
      const a = 1 - Math.exp(-FOLLOW_DAMPING * delta)
      camera.position.lerp(pos.current, a)
      controls.target.lerp(target.current, a)
    }
    camera.lookAt(controls.target)
  })

  return null
}
