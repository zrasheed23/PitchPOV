import { useFrame, useThree } from '@react-three/fiber'
import { type RefObject, useEffect, useRef } from 'react'
import * as THREE from 'three'
import type { Preset, View } from './camera'

const TRANSITION_S = 0.6
const FOLLOW_OFFSET = new THREE.Vector3(0, 22, 30) // high, on the near touchline side
const FOLLOW_DAMPING = 6
const DRAG_PX = 4
const FOCUS_ZOOM = 0.5 // each double-click halves the distance to the clicked spot
const FOCUS_MIN_DIST = 12
const GROUND = new THREE.Plane(new THREE.Vector3(0, 1, 0), 0)
const EYE_HEIGHT = 1.65
const LOOK_MIN_Y = 0.8 // don't stare straight down at a ball at the player's feet
const LOOK_DAMPING = 8

// Walk up from a raycast hit to the player group Replay tagged with userData.playerId.
function playerIdOf(obj: THREE.Object3D | null): string | null {
  for (let o = obj; o; o = o.parent) if (typeof o.userData.playerId === 'string') return o.userData.playerId
  return null
}

// The only part of drei's OrbitControls the rig touches.
interface Controls {
  target: THREE.Vector3
  enabled: boolean
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
  onPlayerView: (playerId: string | null) => void
}

// Moves the camera between presets and tracks the ball in follow mode.
// OrbitControls (makeDefault) owns the camera whenever the rig isn't driving it.
export function CameraRig({ view, attackSide, ball, onManual, onPlayerView }: CameraRigProps) {
  const camera = useThree((s) => s.camera)
  const scene = useThree((s) => s.scene)
  const gl = useThree((s) => s.gl)
  const controls = useThree((s) => s.controls) as unknown as Controls | null

  const active = useRef<Preset | null>(null)
  const transition = useRef<{ elapsed: number; fromPos: THREE.Vector3; fromTarget: THREE.Vector3 } | null>(null)
  const lastBall = useRef(new THREE.Vector3())
  const pos = useRef(new THREE.Vector3())
  const target = useRef(new THREE.Vector3())
  // Double-click zoom: fly from -> to, keeping the current viewing angle.
  const focus = useRef<{
    elapsed: number
    fromPos: THREE.Vector3
    fromTarget: THREE.Vector3
    toPos: THREE.Vector3
    toTarget: THREE.Vector3
  } | null>(null)
  // Player's-eye view: camera at the player's head, looking toward the ball.
  const pov = useRef<{ id: string; group: THREE.Group; elapsed: number; fromPos: THREE.Vector3; look: THREE.Vector3 } | null>(
    null,
  )
  const endPov = () => {
    if (!pov.current) return
    pov.current.group.visible = true
    pov.current = null
    if (controls) controls.enabled = true
    onPlayerView(null)
  }

  useEffect(() => {
    active.current = view.preset
    if (!view.preset || !controls) return
    focus.current = null // a preset click cancels a double-click zoom in flight
    endPov()
    transition.current = { elapsed: 0, fromPos: camera.position.clone(), fromTarget: controls.target.clone() }
  }, [view, camera, controls])

  // A drag or wheel on the canvas hands the camera back to the user.
  useEffect(() => {
    const el = gl.domElement
    let down: { x: number; y: number } | null = null
    const manual = () => {
      transition.current = null
      focus.current = null
      endPov()
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

    // Double-click a spot on the pitch to fly closer to it.
    const raycaster = new THREE.Raycaster()
    const ndc = new THREE.Vector2()
    const hit = new THREE.Vector3()
    const onDoubleClick = (e: MouseEvent) => {
      if (!controls) return
      const rect = el.getBoundingClientRect()
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1)
      raycaster.setFromCamera(ndc, camera)

      // Double-clicking a player switches to their view.
      const hits = raycaster.intersectObjects(scene.children, true)
      const id = hits.length ? playerIdOf(hits[0].object) : null
      const group = id ? (scene.getObjectByName(`player-${id}`) as THREE.Group | undefined) : undefined
      if (id && group) {
        manual()
        group.visible = false // the camera sits inside this player's cylinder
        controls.enabled = false // OrbitControls would clamp the camera back out to minDistance
        pov.current = { id, group, elapsed: 0, fromPos: camera.position.clone(), look: controls.target.clone() }
        onPlayerView(id)
        return
      }

      if (!raycaster.ray.intersectPlane(GROUND, hit)) return // clicked the sky
      manual()
      const offset = camera.position.clone().sub(controls.target)
      const dist = Math.max(offset.length() * FOCUS_ZOOM, FOCUS_MIN_DIST)
      focus.current = {
        elapsed: 0,
        fromPos: camera.position.clone(),
        fromTarget: controls.target.clone(),
        toTarget: hit.clone(),
        toPos: hit.clone().add(offset.setLength(dist)),
      }
    }
    el.addEventListener('dblclick', onDoubleClick)
    return () => {
      el.removeEventListener('dblclick', onDoubleClick)
      el.removeEventListener('pointerdown', onDown)
      el.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      el.removeEventListener('wheel', manual)
    }
  }, [gl, camera, scene, controls, onManual, onPlayerView])

  useFrame((_, delta) => {
    if (!controls) return
    const b = ball.current
    if (b?.visible) lastBall.current.set(b.position.x, 0, b.position.z) // null ball keeps the last spot

    const v = pov.current
    if (v) {
      if (!v.group.parent) {
        endPov() // the clip changed and this player is gone
        return
      }
      // Look toward the ball (tracking has no body orientation), smoothed so it doesn't jitter.
      const goal = b?.visible ? b.position : lastBall.current
      target.current.set(goal.x, Math.max(goal.y, LOOK_MIN_Y), goal.z)
      v.look.lerp(target.current, 1 - Math.exp(-LOOK_DAMPING * delta))
      pos.current.set(v.group.position.x, EYE_HEIGHT, v.group.position.z)
      v.elapsed += delta
      const k = easeInOut(Math.min(v.elapsed / TRANSITION_S, 1))
      camera.position.lerpVectors(v.fromPos, pos.current, k)
      controls.target.copy(v.look)
      camera.lookAt(v.look)
      return
    }

    const f = focus.current
    if (f) {
      f.elapsed += delta
      const k = easeInOut(Math.min(f.elapsed / TRANSITION_S, 1))
      camera.position.lerpVectors(f.fromPos, f.toPos, k)
      controls.target.lerpVectors(f.fromTarget, f.toTarget, k)
      camera.lookAt(controls.target)
      if (f.elapsed >= TRANSITION_S) focus.current = null
      return
    }

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
