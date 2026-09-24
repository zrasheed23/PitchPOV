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

const lineMat = <meshBasicMaterial color="white" />

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
  const hw = GOAL_WIDTH / 2
  return (
    <group>
      {[hw, -hw].map((z) => (
        <mesh key={z} position={[x, GOAL_HEIGHT / 2, z]}>
          <cylinderGeometry args={[POST_R, POST_R, GOAL_HEIGHT, 12]} />
          <meshStandardMaterial color="white" />
        </mesh>
      ))}
      <mesh position={[x, GOAL_HEIGHT, 0]} rotation-x={Math.PI / 2}>
        <cylinderGeometry args={[POST_R, POST_R, GOAL_WIDTH + POST_R * 2, 12]} />
        <meshStandardMaterial color="white" />
      </mesh>
    </group>
  )
}

export function Pitch() {
  // Half-angle of the penalty arc that sits outside the box.
  const dAngle = Math.acos((BOX_DEPTH - SPOT_DIST) / CIRCLE_R)
  return (
    <group>
      <mesh rotation-x={-Math.PI / 2}>
        <planeGeometry args={[LENGTH + 16, WIDTH + 12]} />
        <meshStandardMaterial color="#3a7d3a" />
      </mesh>

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
