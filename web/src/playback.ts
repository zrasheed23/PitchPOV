// Mutable playback clock shared by the scene and the controls through a ref.
// It changes every frame, so it never lives in React state.
export interface Playback {
  time: number
  playing: boolean
  speed: number
  scrubbing: boolean
}

export const SPEEDS = [0.25, 0.5, 1] as const
