import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { useEffect, useRef, useState } from 'react'
import type { Clip } from './clip'
import { Pitch } from './Pitch'
import { Replay } from './Replay'

const CLIP_URL = '/clips/10517_dimaria.json'

export default function App() {
  const [clip, setClip] = useState<Clip | null>(null)
  const [error, setError] = useState<string | null>(null)
  const timeLabel = useRef<HTMLSpanElement>(null)

  useEffect(() => {
    fetch(CLIP_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Clip>
      })
      .then(setClip)
      .catch((e: Error) => setError(`Could not load ${CLIP_URL}: ${e.message}`))
  }, [])

  return (
    <>
      {/* Broadcast-style view from above the near touchline. */}
      <Canvas camera={{ position: [0, 40, 70], fov: 40, near: 0.5, far: 500 }}>
        <color attach="background" args={['#1b2a1b']} />
        <ambientLight intensity={0.8} />
        <directionalLight position={[30, 60, 40]} intensity={1.8} />
        <Pitch />
        {clip && <Replay clip={clip} timeLabel={timeLabel} />}
        <OrbitControls target={[0, 0, 0]} maxPolarAngle={Math.PI / 2 - 0.05} />
      </Canvas>

      <div className="overlay">
        {error ? (
          <div>{error}</div>
        ) : clip ? (
          <>
            <div className="scorer">{clip.scorer}</div>
            <div>
              {clip.teams.home.shortName} v {clip.teams.away.shortName} · {clip.clock}
            </div>
            <span ref={timeLabel} className="time" />
          </>
        ) : (
          <div>Loading clip…</div>
        )}
      </div>
    </>
  )
}
