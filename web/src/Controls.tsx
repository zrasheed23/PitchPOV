import { type PointerEvent, type RefObject, useEffect, useRef } from 'react'
import { type Clip, clipDuration } from './clip'
import { type Playback, SPEEDS } from './playback'

const SHOT_LEAD_S = 2
const KEY_STEP_S = 0.5

interface ControlsProps {
  clip: Clip
  playback: RefObject<Playback>
  playing: boolean
  ended: boolean
  speed: number
  onTogglePlay: () => void
  onSpeed: (speed: number) => void
  onSeek: (t: number) => void
}

export function Controls({ clip, playback, playing, ended, speed, onTogglePlay, onSpeed, onSeek }: ControlsProps) {
  const duration = clipDuration(clip)
  const track = useRef<HTMLDivElement>(null)
  const fill = useRef<HTMLDivElement>(null)
  const handle = useRef<HTMLDivElement>(null)
  const label = useRef<HTMLSpanElement>(null)

  // Mirror the playback clock into the DOM every animation frame, outside React.
  useEffect(() => {
    let raf = 0
    const tick = () => {
      const t = playback.current.time
      const frac = t / duration
      if (fill.current) fill.current.style.transform = `scaleX(${frac})`
      if (handle.current) handle.current.style.left = `${frac * 100}%`
      if (track.current) track.current.setAttribute('aria-valuenow', t.toFixed(2))
      if (label.current) label.current.textContent = `${t.toFixed(1)} / ${duration.toFixed(1)} s`
      raf = requestAnimationFrame(tick)
    }
    tick()
    return () => cancelAnimationFrame(raf)
  }, [playback, duration])

  const timeAt = (e: PointerEvent) => {
    const r = e.currentTarget.getBoundingClientRect()
    return (Math.min(Math.max(e.clientX - r.left, 0), r.width) / r.width) * duration
  }

  // While scrubbing the clock holds still; `playing` is untouched, so playback
  // resumes on release only if it was playing before.
  const scrubStart = (e: PointerEvent) => {
    e.currentTarget.setPointerCapture(e.pointerId)
    playback.current.scrubbing = true
    onSeek(timeAt(e))
  }
  const scrubMove = (e: PointerEvent) => {
    if (playback.current.scrubbing) onSeek(timeAt(e))
  }
  const scrubEnd = () => {
    playback.current.scrubbing = false
  }

  return (
    <div className="controls">
      <button type="button" className="play" onClick={onTogglePlay} aria-label={ended ? 'Replay' : playing ? 'Pause' : 'Play'}>
        {ended ? '↻' : playing ? '❚❚' : '▶'}
      </button>

      <div
        ref={track}
        className="scrub"
        role="slider"
        tabIndex={0}
        aria-label="Clip time"
        aria-valuemin={0}
        aria-valuemax={duration}
        onPointerDown={scrubStart}
        onPointerMove={scrubMove}
        onPointerUp={scrubEnd}
        onPointerCancel={scrubEnd}
        onKeyDown={(e) => {
          const step = e.key === 'ArrowRight' ? KEY_STEP_S : e.key === 'ArrowLeft' ? -KEY_STEP_S : 0
          if (step) {
            e.preventDefault()
            onSeek(playback.current.time + step)
          }
        }}
      >
        <div className="scrub-rail">
          <div ref={fill} className="scrub-fill" />
          <div className="scrub-shot" style={{ left: `${(clip.goalT / duration) * 100}%` }} title="Shot" />
        </div>
        <div ref={handle} className="scrub-handle" />
      </div>

      <span ref={label} className="time" />

      <div className="speeds" role="group" aria-label="Speed">
        {SPEEDS.map((s) => (
          <button key={s} type="button" aria-pressed={s === speed} onClick={() => onSpeed(s)}>
            {s}x
          </button>
        ))}
      </div>

      <button type="button" onClick={() => onSeek(Math.max(0, clip.goalT - SHOT_LEAD_S))}>
        Jump to shot
      </button>
    </div>
  )
}
