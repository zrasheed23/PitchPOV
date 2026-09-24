import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { useCallback, useEffect, useRef, useState } from 'react'
import type * as THREE from 'three'
import { type Clip, attackingSide, clipDuration } from './clip'
import { PRESETS, type View } from './camera'
import { CameraRig } from './CameraRig'
import { Controls } from './Controls'
import { Pitch } from './Pitch'
import type { Playback } from './playback'
import { Replay } from './Replay'

const CLIP_URL = '/clips/10517_dimaria.json'

export default function App() {
  const [clip, setClip] = useState<Clip | null>(null)
  const [error, setError] = useState<string | null>(null)

  // The clock itself lives in this ref; the state below only changes on clicks
  // (and once when the clip ends), never per frame.
  const playback = useRef<Playback>({ time: 0, playing: true, speed: 1, scrubbing: false })
  const [playing, setPlaying] = useState(true)
  const [ended, setEnded] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [view, setView] = useState<View>({ preset: 'broadcast', seq: 0 })
  const ball = useRef<THREE.Mesh>(null)

  useEffect(() => {
    fetch(CLIP_URL)
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Clip>
      })
      .then(setClip)
      .catch((e: Error) => setError(`Could not load ${CLIP_URL}: ${e.message}`))
  }, [])

  const play = useCallback((p: boolean) => {
    playback.current.playing = p
    setPlaying(p)
  }, [])

  const togglePlay = useCallback(() => {
    if (ended) {
      playback.current.time = 0
      setEnded(false)
      play(true)
    } else {
      play(!playback.current.playing)
    }
  }, [ended, play])

  const onEnded = useCallback(() => {
    setPlaying(false)
    setEnded(true)
  }, [])

  const seek = useCallback(
    (t: number) => {
      if (!clip) return
      const duration = clipDuration(clip)
      playback.current.time = Math.min(Math.max(t, 0), duration)
      if (playback.current.time < duration) setEnded(false)
    },
    [clip],
  )

  const changeSpeed = useCallback((s: number) => {
    playback.current.speed = s
    setSpeed(s)
  }, [])

  const onManualCamera = useCallback(() => {
    setView((v) => (v.preset ? { ...v, preset: null } : v))
  }, [])

  useEffect(() => {
    const isSpace = (e: KeyboardEvent) => e.code === 'Space' || e.key === ' '
    const down = (e: KeyboardEvent) => {
      if (!isSpace(e)) return
      // Stop the page scrolling and a focused button from also "clicking".
      e.preventDefault()
      if (!e.repeat) togglePlay()
    }
    const up = (e: KeyboardEvent) => {
      if (isSpace(e)) e.preventDefault()
    }
    window.addEventListener('keydown', down)
    window.addEventListener('keyup', up)
    return () => {
      window.removeEventListener('keydown', down)
      window.removeEventListener('keyup', up)
    }
  }, [togglePlay])

  return (
    <>
      <Canvas camera={{ position: [0, 40, 70], fov: 40, near: 0.5, far: 500 }}>
        <color attach="background" args={['#1b2a1b']} />
        <ambientLight intensity={0.8} />
        <directionalLight position={[30, 60, 40]} intensity={1.8} />
        <Pitch />
        {clip && (
          <>
            <Replay clip={clip} playback={playback} ball={ball} onEnded={onEnded} />
            <CameraRig view={view} attackSide={attackingSide(clip)} ball={ball} onManual={onManualCamera} />
          </>
        )}
        <OrbitControls
          makeDefault
          target={[0, 0, 0]}
          maxPolarAngle={Math.PI / 2 - 0.05}
          zoomSpeed={0.35}
          rotateSpeed={0.6}
          minDistance={8}
          maxDistance={130}
        />
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
          </>
        ) : (
          <div>Loading clip…</div>
        )}
      </div>

      <div className="presets" role="group" aria-label="Camera">
        {PRESETS.map((p) => (
          <button
            key={p.id}
            type="button"
            aria-pressed={view.preset === p.id}
            onClick={() => setView((v) => ({ preset: p.id, seq: v.seq + 1 }))}
          >
            {p.label}
          </button>
        ))}
      </div>

      {clip && (
        <Controls
          clip={clip}
          playback={playback}
          playing={playing}
          ended={ended}
          speed={speed}
          onTogglePlay={togglePlay}
          onSpeed={changeSpeed}
          onSeek={seek}
        />
      )}
    </>
  )
}
