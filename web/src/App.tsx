import { OrbitControls } from '@react-three/drei'
import { Canvas } from '@react-three/fiber'
import { useCallback, useEffect, useRef, useState } from 'react'
import type * as THREE from 'three'
import { type Clip, attackingSide, clipDuration } from './clip'
import { PRESETS, type View } from './camera'
import { CameraRig } from './CameraRig'
import { Controls } from './Controls'
import { GoalPicker } from './GoalPicker'
import { type GoalEntry, scoreLabel } from './goals'
import { Pitch } from './Pitch'
import type { Playback } from './playback'
import { Replay } from './Replay'

const CLIPS_URL = `${import.meta.env.BASE_URL}clips/`

async function fetchJson<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`Could not load ${url}: ${r.status} ${r.statusText}`)
  return r.json() as Promise<T>
}

export default function App() {
  const [index, setIndex] = useState<GoalEntry[]>([])
  const [goal, setGoal] = useState<GoalEntry | null>(null)
  const [clip, setClip] = useState<Clip | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const request = useRef(0) // only the latest clip request may land

  // The clock itself lives in this ref; the state below only changes on clicks
  // (and once when the clip ends), never per frame.
  const playback = useRef<Playback>({ time: 0, playing: true, speed: 1, scrubbing: false })
  const [playing, setPlaying] = useState(true)
  const [ended, setEnded] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [view, setView] = useState<View>({ preset: 'broadcast', seq: 0 })
  const ball = useRef<THREE.Mesh>(null)

  // Load a goal's clip, then start it from the top with the default camera.
  const loadGoal = useCallback((g: GoalEntry) => {
    const id = ++request.current
    setGoal(g)
    setLoading(true)
    setError(null)
    fetchJson<Clip>(CLIPS_URL + g.clip)
      .then((c) => {
        if (id !== request.current) return
        playback.current.time = 0
        playback.current.playing = true
        playback.current.scrubbing = false
        setPlaying(true)
        setEnded(false)
        setView((v) => ({ preset: 'broadcast', seq: v.seq + 1 }))
        setClip(c)
      })
      .catch((e: Error) => id === request.current && setError(e.message))
      .finally(() => id === request.current && setLoading(false))
  }, [])

  useEffect(() => {
    fetchJson<GoalEntry[]>(CLIPS_URL + 'index.json')
      .then((idx) => {
        setIndex(idx)
        if (idx.length) loadGoal(idx[0])
        else setLoading(false)
      })
      .catch((e: Error) => {
        setError(e.message)
        setLoading(false)
      })
  }, [loadGoal])

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
    const typing = (e: KeyboardEvent) => e.target instanceof HTMLInputElement
    const down = (e: KeyboardEvent) => {
      if (!isSpace(e) || typing(e)) return
      // Stop the page scrolling and a focused button from also "clicking".
      e.preventDefault()
      if (!e.repeat) togglePlay()
    }
    const up = (e: KeyboardEvent) => {
      if (isSpace(e) && !typing(e)) e.preventDefault()
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
            <Replay key={clip.gameEventId} clip={clip} playback={playback} ball={ball} onEnded={onEnded} />
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

      <div className="panel">
        <div className="now">
          {goal && (
            <>
              <div className="scorer">
                {goal.scorer}
                {goal.ownGoal && <span className="tag">OG</span>}
              </div>
              <div>
                {scoreLabel(goal)} · {goal.minute}
              </div>
            </>
          )}
          {error ? <div className="error">{error}</div> : loading && <div className="muted">Loading…</div>}
        </div>
        {index.length > 0 && <GoalPicker index={index} current={goal} onPick={loadGoal} />}
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
