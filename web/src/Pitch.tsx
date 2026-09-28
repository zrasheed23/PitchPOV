import type { JSX } from 'react'
import { useMemo } from 'react'
import * as THREE from 'three'

// Pitch in three.js space: pitch x -> three x, pitch y -> three -z, up is +y.

const LENGTH = 105
const WIDTH = 68
const HALF_L = LENGTH / 2
const HALF_W = WIDTH / 2
const LINE = 0.12
const LINE_Y = 0.01 // lift lines off the grass to avoid z-fighting

const BOX_DEPTH = 16.5
const BOX_WIDTH = 40.32
const SIX_DEPTH = 5.5
const SIX_WIDTH = 18.32
const SPOT_DIST = 11
const CIRCLE_R = 9.15

const GOAL_WIDTH = 7.32
const GOAL_HEIGHT = 2.44
const POST_R = 0.06
const NET_DEPTH = 2.2 // the pipeline rests the ball 2 m behind the line

const lineMat = <meshBasicMaterial color="white" />

// Net mesh drawn on a canvas: white cords over a faint white fill. The fill keeps
// the net readable from far cameras, where the cords shrink below a pixel.
function netTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = c.height = 64
  const ctx = c.getContext('2d')!
  ctx.fillStyle = 'rgba(255,255,255,0.14)'
  ctx.fillRect(0, 0, 64, 64)
  ctx.strokeStyle = 'rgba(255,255,255,0.95)'
  ctx.lineWidth = 5
  ctx.strokeRect(0, 0, 64, 64)
  const tex = new THREE.CanvasTexture(c)
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping
  tex.anisotropy = 8
  return tex
}
const NET_CELL = 0.12 // metres per mesh square
let netTex: THREE.CanvasTexture | null = null

// Blended, not alpha-tested: an alpha test drops the mipmapped (far away) net entirely.
function NetPanel({ w, h, ...props }: { w: number; h: number } & JSX.IntrinsicElements['mesh']) {
  const tex = useMemo(() => {
    netTex ??= netTexture()
    const t = netTex.clone()
    t.repeat.set(w / NET_CELL, h / NET_CELL)
    t.needsUpdate = true
    return t
  }, [w, h])
  return (
    <mesh {...props}>
      <planeGeometry args={[w, h]} />
      <meshBasicMaterial map={tex} transparent side={THREE.DoubleSide} depthWrite={false} />
    </mesh>
  )
}

// A rope along the net's edge, from a to b (three.js coordinates).
const ROPE_R = 0.035
function Rope({ a, b }: { a: [number, number, number]; b: [number, number, number] }) {
  const from = new THREE.Vector3(...a)
  const to = new THREE.Vector3(...b)
  const len = from.distanceTo(to)
  const mid = from.clone().add(to).multiplyScalar(0.5)
  const quat = new THREE.Quaternion().setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.sub(from).normalize())
  return (
    <mesh position={mid} quaternion={quat}>
      <cylinderGeometry args={[ROPE_R, ROPE_R, len, 6]} />
      <meshBasicMaterial color="white" />
    </mesh>
  )
}

// Axis-aligned line segment between two pitch points.
function Segment({ x1, y1, x2, y2 }: { x1: number; y1: number; x2: number; y2: number }) {
  const sx = Math.abs(x2 - x1) + LINE
  const sy = Math.abs(y2 - y1) + LINE
  return (
    <mesh position={[(x1 + x2) / 2, LINE_Y, -(y1 + y2) / 2]} rotation-x={-Math.PI / 2}>
      <planeGeometry args={[sx, sy]} />
      {lineMat}
    </mesh>
  )
}

function Arc({ x, radius, start = 0, length = Math.PI * 2 }: { x: number; radius: number; start?: number; length?: number }) {
  return (
    <mesh position={[x, LINE_Y, 0]} rotation-x={-Math.PI / 2}>
      <ringGeometry args={[radius - LINE / 2, radius + LINE / 2, 64, 1, start, length]} />
      {lineMat}
    </mesh>
  )
}

function Spot({ x }: { x: number }) {
  return (
    <mesh position={[x, LINE_Y, 0]} rotation-x={-Math.PI / 2}>
      <circleGeometry args={[0.15, 16]} />
      {lineMat}
    </mesh>
  )
}

// Rectangle open toward the goal line at x = end (+-HALF_L).
function Box({ side, depth, width }: { side: 1 | -1; depth: number; width: number }) {
  const inner = side * (HALF_L - depth)
  const end = side * HALF_L
  const hw = width / 2
  return (
    <>
      <Segment x1={inner} y1={-hw} x2={inner} y2={hw} />
      <Segment x1={inner} y1={hw} x2={end} y2={hw} />
      <Segment x1={inner} y1={-hw} x2={end} y2={-hw} />
    </>
  )
}

function Goal({ side }: { side: 1 | -1 }) {
  const x = side * HALF_L
  const bx = x + side * NET_DEPTH // back of the net
  const hw = GOAL_WIDTH / 2
  return (
    <group>
      {[hw, -hw].map((z) => (
        <mesh key={z} position={[x, GOAL_HEIGHT / 2, z]} castShadow>
          <cylinderGeometry args={[POST_R, POST_R, GOAL_HEIGHT, 12]} />
          <meshStandardMaterial color="white" roughness={0.4} />
        </mesh>
      ))}
      <mesh position={[x, GOAL_HEIGHT, 0]} rotation-x={Math.PI / 2} castShadow>
        <cylinderGeometry args={[POST_R, POST_R, GOAL_WIDTH + POST_R * 2, 12]} />
        <meshStandardMaterial color="white" />
      </mesh>
      {/* Net: back, roof and sides */}
      <NetPanel w={GOAL_WIDTH} h={GOAL_HEIGHT} position={[bx, GOAL_HEIGHT / 2, 0]} rotation-y={Math.PI / 2} />
      <NetPanel w={NET_DEPTH} h={GOAL_WIDTH} position={[x + (side * NET_DEPTH) / 2, GOAL_HEIGHT, 0]} rotation-x={Math.PI / 2} />
      {[hw, -hw].map((z) => (
        <NetPanel key={z} w={NET_DEPTH} h={GOAL_HEIGHT} position={[x + (side * NET_DEPTH) / 2, GOAL_HEIGHT / 2, z]} />
      ))}
      {/* Ropes on the net's edges so its shape reads from any distance */}
      {[hw, -hw].map((z) => (
        <group key={z}>
          <Rope a={[x, GOAL_HEIGHT, z]} b={[bx, GOAL_HEIGHT, z]} />
          <Rope a={[bx, GOAL_HEIGHT, z]} b={[bx, 0, z]} />
          <Rope a={[x, ROPE_R, z]} b={[bx, ROPE_R, z]} />
        </group>
      ))}
      <Rope a={[bx, GOAL_HEIGHT, hw]} b={[bx, GOAL_HEIGHT, -hw]} />
      <Rope a={[bx, ROPE_R, hw]} b={[bx, ROPE_R, -hw]} />
    </group>
  )
}

export function Pitch() {
  // Half-angle of the penalty arc that sits outside the box.
  const dAngle = Math.acos((BOX_DEPTH - SPOT_DIST) / CIRCLE_R)
  return (
    <group>
      {/* Touchlines, goal lines, halfway line */}
      <Segment x1={-HALF_L} y1={HALF_W} x2={HALF_L} y2={HALF_W} />
      <Segment x1={-HALF_L} y1={-HALF_W} x2={HALF_L} y2={-HALF_W} />
      <Segment x1={-HALF_L} y1={-HALF_W} x2={-HALF_L} y2={HALF_W} />
      <Segment x1={HALF_L} y1={-HALF_W} x2={HALF_L} y2={HALF_W} />
      <Segment x1={0} y1={-HALF_W} x2={0} y2={HALF_W} />

      <Arc x={0} radius={CIRCLE_R} />
      <Spot x={0} />

      {([1, -1] as const).map((side) => (
        <group key={side}>
          <Box side={side} depth={BOX_DEPTH} width={BOX_WIDTH} />
          <Box side={side} depth={SIX_DEPTH} width={SIX_WIDTH} />
          <Spot x={side * (HALF_L - SPOT_DIST)} />
          <Arc
            x={side * (HALF_L - SPOT_DIST)}
            radius={CIRCLE_R}
            start={side === 1 ? Math.PI - dAngle : -dAngle}
            length={dAngle * 2}
          />
          <Goal side={side} />
        </group>
      ))}
    </group>
  )
}
