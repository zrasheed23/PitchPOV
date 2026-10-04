import { useFrame, useThree } from '@react-three/fiber'
import { useEffect, useMemo, useRef } from 'react'
import * as THREE from 'three'

// Everything around the pitch lines: striped grass, ad boards, a bowl of stands
// with rounded corners and a crowd, a roof edge with floodlights. All built in code, no model files.
// Pitch x -> three x, pitch y -> three -z, up is +y (same as Pitch.tsx).

const HALF_L = 52.5
const HALF_W = 34
const RUNOFF_X = 7 // grass beyond the goal lines
const RUNOFF_Z = 6 // grass beyond the touchlines
const BOARD_H = 0.9
const STAND_GAP = 5 // from the ad boards to the first row
const CORNER_R = 16 // radius of the rounded corners of the bowl
const ROWS = 30
const ROW_DEPTH = 0.85
const ROW_RISE = 0.5
const SEAT_W = 0.55
const FRONT_H = 1.6 // height of the wall in front of row 1
const STAND_DEPTH = ROWS * ROW_DEPTH
const STAND_TOP = FRONT_H + ROWS * ROW_RISE

// --- Grass ------------------------------------------------------------------

function grassTexture(): THREE.CanvasTexture {
  const stripes = 18
  const c = document.createElement('canvas')
  c.width = 1024
  c.height = 256
  const ctx = c.getContext('2d')!
  const w = c.width / stripes
  for (let i = 0; i < stripes; i++) {
    ctx.fillStyle = i % 2 ? '#2f7a33' : '#378a3b'
    ctx.fillRect(i * w, 0, w + 1, c.height)
  }
  // Fine speckle so it doesn't look like flat plastic.
  const img = ctx.getImageData(0, 0, c.width, c.height)
  for (let i = 0; i < img.data.length; i += 4) {
    const n = (Math.random() - 0.5) * 14
    img.data[i] += n
    img.data[i + 1] += n
    img.data[i + 2] += n
  }
  ctx.putImageData(img, 0, 0)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.anisotropy = 8
  return tex
}

function Grass() {
  const tex = useMemo(() => grassTexture(), [])
  useEffect(() => () => tex.dispose(), [tex])
  const w = (HALF_L + RUNOFF_X) * 2
  const h = (HALF_W + RUNOFF_Z) * 2
  return (
    <mesh rotation-x={-Math.PI / 2} receiveShadow>
      <planeGeometry args={[w, h]} />
      <meshStandardMaterial map={tex} roughness={0.95} />
    </mesh>
  )
}

// --- Ad boards --------------------------------------------------------------

function boardTexture(): THREE.CanvasTexture {
  const c = document.createElement('canvas')
  c.width = 1024
  c.height = 64
  const ctx = c.getContext('2d')!
  const g = ctx.createLinearGradient(0, 0, c.width, 0)
  g.addColorStop(0, '#0b2a6b')
  g.addColorStop(0.5, '#123f9c')
  g.addColorStop(1, '#0b2a6b')
  ctx.fillStyle = g
  ctx.fillRect(0, 0, c.width, c.height)
  ctx.fillStyle = '#ffffff'
  ctx.font = 'bold 38px system-ui, sans-serif'
  ctx.textBaseline = 'middle'
  ctx.fillText('GOAL REPLAY', 40, 34)
  ctx.fillStyle = '#7fb4ff'
  ctx.fillText('2022', 330, 34)
  ctx.fillStyle = '#ffffff'
  ctx.fillText('GOAL REPLAY', 560, 34)
  ctx.fillStyle = '#7fb4ff'
  ctx.fillText('2022', 850, 34)
  const tex = new THREE.CanvasTexture(c)
  tex.colorSpace = THREE.SRGBColorSpace
  tex.wrapS = THREE.RepeatWrapping
  return tex
}

function Boards() {
  const tex = useMemo(() => boardTexture(), [])
  useEffect(() => () => tex.dispose(), [tex])
  const bx = HALF_L + RUNOFF_X - 0.5
  const bz = HALF_W + RUNOFF_Z - 0.5
  const sides: { pos: [number, number, number]; rotY: number; len: number }[] = [
    { pos: [0, BOARD_H / 2, -bz], rotY: 0, len: bx * 2 },
    { pos: [0, BOARD_H / 2, bz], rotY: Math.PI, len: bx * 2 },
    { pos: [-bx, BOARD_H / 2, 0], rotY: Math.PI / 2, len: bz * 2 },
    { pos: [bx, BOARD_H / 2, 0], rotY: -Math.PI / 2, len: bz * 2 },
  ]
  return (
    <group>
      {sides.map((s, i) => {
        const t = tex.clone()
        t.repeat.set(s.len / 20, 1)
        t.needsUpdate = true
        return (
          <mesh key={i} position={s.pos} rotation-y={s.rotY} castShadow>
            <boxGeometry args={[s.len, BOARD_H, 0.15]} />
            {/* Faces: +x, -x, +y, -y, +z (toward the pitch), -z */}
            <meshStandardMaterial attach="material-0" color="#0a1a3a" />
            <meshStandardMaterial attach="material-1" color="#0a1a3a" />
            <meshStandardMaterial attach="material-2" color="#0a1a3a" />
            <meshStandardMaterial attach="material-3" color="#0a1a3a" />
            <meshStandardMaterial attach="material-4" map={t} emissive="#ffffff" emissiveMap={t} emissiveIntensity={0.9} />
            <meshStandardMaterial attach="material-5" color="#0a1a3a" />
          </mesh>
        )
      })}
    </group>
  )
}

// --- Stands and crowd -------------------------------------------------------
//
// The bowl follows a rounded rectangle around the pitch: four straight sides
// joined by quarter circles. It's built as 8 segments so any segment between
// the camera and the pitch can be hidden, like a TV cutaway.

interface Sample {
  x: number // world x of the front edge
  z: number // world z of the front edge
  nx: number // outward normal (away from the pitch)
  nz: number
}

interface Segment {
  key: string
  samples: Sample[] // along the front edge; rows step outward along the normals
  mid: Sample // for the cutaway test
}

function bowlSegments(): Segment[] {
  const X = HALF_L + RUNOFF_X + STAND_GAP
  const Z = HALF_W + RUNOFF_Z + STAND_GAP
  const R = CORNER_R
  const straight = (key: string, a: [number, number], b: [number, number], n: [number, number]): Segment => {
    const samples = [
      { x: a[0], z: a[1], nx: n[0], nz: n[1] },
      { x: b[0], z: b[1], nx: n[0], nz: n[1] },
    ]
    return { key, samples, mid: { x: (a[0] + b[0]) / 2, z: (a[1] + b[1]) / 2, nx: n[0], nz: n[1] } }
  }
  const arc = (key: string, cx: number, cz: number, from: number): Segment => {
    const steps = 14
    const samples: Sample[] = []
    for (let i = 0; i <= steps; i++) {
      const a = from + (i / steps) * (Math.PI / 2)
      samples.push({ x: cx + R * Math.cos(a), z: cz + R * Math.sin(a), nx: Math.cos(a), nz: Math.sin(a) })
    }
    const m = from + Math.PI / 4
    return { key, samples, mid: { x: cx + R * Math.cos(m), z: cz + R * Math.sin(m), nx: Math.cos(m), nz: Math.sin(m) } }
  }
  const x = X - R
  const z = Z - R
  // Counter-clockwise seen from above (x right, z toward the camera).
  return [
    straight('south', [-x, Z], [x, Z], [0, 1]),
    arc('south-east', x, z, 0),
    straight('east', [X, z], [X, -z], [1, 0]),
    arc('north-east', x, -z, -Math.PI / 2),
    straight('north', [x, -Z], [-x, -Z], [0, -1]),
    arc('north-west', -x, -z, Math.PI),
    straight('west', [-X, -z], [-X, z], [-1, 0]),
    arc('south-west', -x, z, Math.PI / 2),
  ]
}

const ROOF_Y = STAND_TOP + 7.5
const ROOF_OVERHANG = 6
const BACK_H = STAND_TOP + 8

// Offset a front-edge sample outward by d metres.
const out = (s: Sample, d: number): [number, number] => [s.x + s.nx * d, s.z + s.nz * d]

// Triangles for one segment's terraces, walls and roof, as a flat-shaded mesh.
function segmentGeometry(seg: Segment): { steps: THREE.BufferGeometry; walls: THREE.BufferGeometry; roof: THREE.BufferGeometry; lights: THREE.BufferGeometry } {
  const quad = (arr: number[], a: number[], b: number[], c: number[], d: number[]) => {
    // a-b-c-d counter-clockwise when seen from the side that should be lit.
    arr.push(...a, ...b, ...c, ...a, ...c, ...d)
  }
  const build = (arr: number[]) => {
    const g = new THREE.BufferGeometry()
    g.setAttribute('position', new THREE.Float32BufferAttribute(arr, 3))
    g.computeVertexNormals()
    return g
  }
  const steps: number[] = []
  const walls: number[] = []
  const roof: number[] = []
  const lights: number[] = []
  const S = seg.samples
  for (let i = 0; i < S.length - 1; i++) {
    const s0 = S[i]
    const s1 = S[i + 1]
    for (let r = 0; r < ROWS; r++) {
      const d0 = r * ROW_DEPTH
      const d1 = (r + 1) * ROW_DEPTH
      const hPrev = FRONT_H + r * ROW_RISE
      const h = FRONT_H + (r + 1) * ROW_RISE
      const [ax, az] = out(s0, d0)
      const [bx, bz] = out(s1, d0)
      const [cx, cz] = out(s1, d1)
      const [dx, dz] = out(s0, d1)
      // Riser facing the pitch, then the tread on top.
      quad(steps, [ax, hPrev, az], [ax, h, az], [bx, h, bz], [bx, hPrev, bz])
      quad(steps, [ax, h, az], [dx, h, dz], [cx, h, cz], [bx, h, bz])
    }
    // Front wall (below row 1) and back wall.
    const [fx0, fz0] = out(s0, 0)
    const [fx1, fz1] = out(s1, 0)
    quad(walls, [fx0, 0, fz0], [fx0, FRONT_H, fz0], [fx1, FRONT_H, fz1], [fx1, 0, fz1])
    const [bx0, bz0] = out(s0, STAND_DEPTH)
    const [bx1, bz1] = out(s1, STAND_DEPTH)
    quad(walls, [bx0, 0, bz0], [bx0, BACK_H, bz0], [bx1, BACK_H, bz1], [bx1, 0, bz1])
    // Roof from behind the back wall to an overhang above the front rows.
    const [rx0, rz0] = out(s0, -ROOF_OVERHANG)
    const [rx1, rz1] = out(s1, -ROOF_OVERHANG)
    const [ox0, oz0] = out(s0, STAND_DEPTH + 1)
    const [ox1, oz1] = out(s1, STAND_DEPTH + 1)
    quad(roof, [rx0, ROOF_Y, rz0], [ox0, ROOF_Y + 1.5, oz0], [ox1, ROOF_Y + 1.5, oz1], [rx1, ROOF_Y, rz1])
    // Floodlight strip under the roof edge.
    const [lx0, lz0] = out(s0, -ROOF_OVERHANG + 0.3)
    const [lx1, lz1] = out(s1, -ROOF_OVERHANG + 0.3)
    quad(lights, [lx0, ROOF_Y - 0.5, lz0], [lx0, ROOF_Y - 0.1, lz0], [lx1, ROOF_Y - 0.1, lz1], [lx1, ROOF_Y - 0.5, lz1])
  }
  return { steps: build(steps), walls: build(walls), roof: build(roof), lights: build(lights) }
}

// A seeded random so the crowd doesn't reshuffle on every reload.
function rng(seed: number) {
  let s = seed >>> 0
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0
    return s / 4294967296
  }
}

const CROWD_NEUTRAL = ['#d4d4d4', '#2b2b2b', '#4b5563', '#9ca3af', '#6b4f3a', '#1e3a5f', '#7f1d1d', '#e7e5e4', '#374151', '#1f2937']
const SKIN_TONES = ['#e0b99a', '#c9956f', '#a86f4c', '#7a4c32', '#5a3825']

interface Seat {
  body: THREE.Matrix4
  head: THREE.Matrix4
  shirt: THREE.Color
  skin: THREE.Color
}

// Walk each row of a segment and put a spectator every SEAT_W metres.
function seatsFor(seg: Segment, colors: string[], seed: number): Seat[] {
  const rand = rng(seed)
  const palette = [...colors, ...CROWD_NEUTRAL, ...CROWD_NEUTRAL.slice(0, 5)]
  const seats: Seat[] = []
  const q = new THREE.Quaternion()
  const up = new THREE.Vector3(0, 1, 0)
  for (let r = 0; r < ROWS; r++) {
    const d = r * ROW_DEPTH + ROW_DEPTH * 0.5
    const floor = FRONT_H + (r + 1) * ROW_RISE
    // Row polyline and its cumulative length.
    const pts = seg.samples.map((s) => ({ p: out(s, d), n: [s.nx, s.nz] as [number, number] }))
    const cum = [0]
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i].p[0] - pts[i - 1].p[0], pts[i].p[1] - pts[i - 1].p[1]))
    const len = cum[cum.length - 1]
    let j = 0
    for (let at = SEAT_W / 2; at < len; at += SEAT_W) {
      while (j < cum.length - 2 && cum[j + 1] < at) j++
      if (rand() < 0.1) continue // empty seats
      const k = (at - cum[j]) / (cum[j + 1] - cum[j] || 1)
      const x = pts[j].p[0] + (pts[j + 1].p[0] - pts[j].p[0]) * k + (rand() - 0.5) * 0.1
      const z = pts[j].p[1] + (pts[j + 1].p[1] - pts[j].p[1]) * k
      const nx = pts[j].n[0] + (pts[j + 1].n[0] - pts[j].n[0]) * k
      const nz = pts[j].n[1] + (pts[j + 1].n[1] - pts[j].n[1]) * k
      const standing = rand() < 0.35
      const h = standing ? 0.95 + rand() * 0.15 : 0.6 + rand() * 0.1
      q.setFromAxisAngle(up, Math.atan2(nx, nz) + (rand() - 0.5) * 0.5)
      const body = new THREE.Matrix4().compose(new THREE.Vector3(x, floor + h / 2, z), q, new THREE.Vector3(0.4, h, 0.26))
      const head = new THREE.Matrix4().compose(new THREE.Vector3(x, floor + h + 0.12, z), q, new THREE.Vector3(0.2, 0.24, 0.2))
      const shirt = new THREE.Color(palette[Math.floor(rand() * palette.length)]).multiplyScalar(0.55 + rand() * 0.35)
      const skin = new THREE.Color(SKIN_TONES[Math.floor(rand() * SKIN_TONES.length)])
      seats.push({ body, head, shirt, skin })
    }
  }
  return seats
}

// Spectators: a body (box) and a head (sphere) per seat, as two instanced meshes.
function Crowd({ seats }: { seats: Seat[] }) {
  const bodies = useRef<THREE.InstancedMesh>(null)
  const heads = useRef<THREE.InstancedMesh>(null)
  useEffect(() => {
    const b = bodies.current
    const h = heads.current
    if (!b || !h) return
    seats.forEach((seat, i) => {
      b.setMatrixAt(i, seat.body)
      b.setColorAt(i, seat.shirt)
      h.setMatrixAt(i, seat.head)
      h.setColorAt(i, seat.skin)
    })
    for (const m of [b, h]) {
      m.instanceMatrix.needsUpdate = true
      if (m.instanceColor) m.instanceColor.needsUpdate = true
      m.computeBoundingSphere() // so frustum culling uses where the seats actually are
    }
  }, [seats])
  return (
    <group key={seats.length}>
      <instancedMesh ref={bodies} args={[undefined, undefined, seats.length]}>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial roughness={1} />
      </instancedMesh>
      <instancedMesh ref={heads} args={[undefined, undefined, seats.length]}>
        <sphereGeometry args={[0.5, 6, 4]} />
        <meshStandardMaterial roughness={1} />
      </instancedMesh>
    </group>
  )
}

const stepMat = new THREE.MeshStandardMaterial({ color: '#3a3f4a', roughness: 0.9, side: THREE.DoubleSide })
const wallMat = new THREE.MeshStandardMaterial({ color: '#1c2130', side: THREE.DoubleSide })
const roofMat = new THREE.MeshStandardMaterial({ color: '#20252f', side: THREE.DoubleSide })
const lightMat = new THREE.MeshStandardMaterial({ color: '#ffffff', emissive: '#fff6dd', emissiveIntensity: 2.5, toneMapped: false, side: THREE.DoubleSide })

function StandSegment({ seg, colors, index, groupRef }: { seg: Segment; colors: string[]; index: number; groupRef: (g: THREE.Group | null) => void }) {
  const geo = useMemo(() => segmentGeometry(seg), [seg])
  useEffect(() => () => Object.values(geo).forEach((g) => g.dispose()), [geo])
  const seats = useMemo(() => seatsFor(seg, colors, 2022 + index), [seg, colors, index])
  return (
    <group ref={groupRef}>
      <mesh geometry={geo.steps} material={stepMat} receiveShadow />
      <mesh geometry={geo.walls} material={wallMat} />
      <mesh geometry={geo.roof} material={roofMat} />
      <mesh geometry={geo.lights} material={lightMat} />
      <Crowd seats={seats} />
    </group>
  )
}

// Hide any segment that sits between the camera and the pitch.
function useCutaway(groups: React.RefObject<Record<string, THREE.Group | null>>, segs: Segment[]) {
  const camera = useThree((s) => s.camera)
  useFrame(() => {
    for (const seg of segs) {
      const g = groups.current[seg.key]
      if (!g) continue
      const { x, z, nx, nz } = seg.mid
      // How far the camera is past this segment's front edge, measured outward.
      g.visible = (camera.position.x - x) * nx + (camera.position.z - z) * nz < 2
    }
  })
}

export function Stadium({ crowdColors }: { crowdColors: string[] }) {
  const segs = useMemo(() => bowlSegments(), [])
  const groups = useRef<Record<string, THREE.Group | null>>({})
  // Rebuild the crowd only when the teams' colours change, not on every render.
  const colorsKey = crowdColors.join(',')
  const colors = useMemo(() => (colorsKey ? colorsKey.split(',') : []), [colorsKey])
  useCutaway(groups, segs)

  return (
    <group>
      <Grass />
      <Boards />
      {segs.map((seg, i) => (
        <StandSegment
          key={seg.key}
          seg={seg}
          colors={colors}
          index={i}
          groupRef={(g) => {
            groups.current[seg.key] = g
          }}
        />
      ))}
      {/* Dark ground beyond the stands */}
      <mesh rotation-x={-Math.PI / 2} position={[0, -0.02, 0]}>
        <planeGeometry args={[400, 400]} />
        <meshStandardMaterial color="#141820" />
      </mesh>
    </group>
  )
}
