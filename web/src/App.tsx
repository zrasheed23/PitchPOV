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
import { Stadium } from './Stadium'
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
  const [pickerOpen, setPickerOpen] = useState(false)
  const [povId, setPovId] = useState<string | null>(null)
  const closePicker = useCallback(() => setPickerOpen(false), [])
  const ball = useRef<THREE.Mesh>(null)
  if (import.meta.env.DEV) (window as unknown as { __playback: unknown }).__playback = playback

  // Load a goal's clip, then start it from the top with the default camera.
  const loadGoal = useCallback((g: GoalEntry) => {
    const id = ++request.current
    setGoal(g)
    // Link straight to this goal: /#10517_6738550
    history.replaceState(null, '', `#${g.clip.replace(/\.json$/, '')}`)
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
        const linked = idx.find((g) => `#${g.clip.replace(/\.json$/, '')}` === location.hash)
        if (idx.length) loadGoal(linked ?? idx[0])
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
      <Canvas shadows dpr={[1, 2]} camera={{ position: [0, 40, 70], fov: 40, near: 0.3, far: 600 }}>
        <color attach="background" args={['#0c1424']} />
        <fog attach="fog" args={['#0c1424', 140, 320]} />
        <hemisphereLight args={['#dfe8ff', '#2a3a2a', 0.9]} />
        {/* Floodlights: one strong key light for shadows, one softer fill from the other side. */}
        <directionalLight
          position={[-40, 90, 50]}
          intensity={2.2}
          castShadow
          shadow-mapSize={[2048, 2048]}
          shadow-camera-left={-70}
          shadow-camera-right={70}
          shadow-camera-top={50}
          shadow-camera-bottom={-50}
          shadow-camera-near={10}
          shadow-camera-far={250}
          shadow-bias={-0.0004}
          shadow-normalBias={0.02}
        />
        <directionalLight position={[50, 70, -40]} intensity={0.7} />
        <Stadium crowdColors={clip ? [clip.teams.home.color, clip.teams.away.color] : []} />
        <Pitch />
        {clip && (
          <>
            <Replay key={clip.gameEventId} clip={clip} playback={playback} ball={ball} onEnded={onEnded} />
            <CameraRig
              view={view}
              attackSide={attackingSide(clip)}
              ball={ball}
              onManual={onManualCamera}
              onPlayerView={setPovId}
            />
          </>
        )}
        <OrbitControls
          makeDefault
          target={[0, 0, 0]}
          maxPolarAngle={Math.PI / 2 - 0.05}
          zoomSpeed={0.35}
          rotateSpeed={0.6}
          minDistance={8}
          maxDistance={160}
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
        {index.length > 0 && (
          <button type="button" className="toggle-picker" onClick={() => setPickerOpen((o) => !o)}>
            Browse goals
          </button>
        )}
      </div>

      {index.length > 0 && (
        // Hidden rather than unmounted, so the search survives closing.
        <GoalPicker
          index={index}
          current={goal}
          open={pickerOpen}
          onClose={closePicker}
          onPick={(g) => {
            setPickerOpen(false)
            loadGoal(g)
          }}
        />
      )}

      {povId && clip && (
        <div className="pov-note">
          Player view: {clip.players.find((p) => p.id === povId)?.name ?? 'player'} · drag or pick a camera to exit
        </div>
      )}

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
