// Camera preset definitions, shared by the rig and the preset buttons.

export type Preset = 'broadcast' | 'top' | 'goal' | 'follow'

// seq changes on every preset click, so re-clicking the active preset re-applies it.
export interface View {
  preset: Preset | null // null = the user has moved the camera by hand
  seq: number
}

export const PRESETS: { id: Preset; label: string }[] = [
  { id: 'broadcast', label: 'Broadcast' },
  { id: 'top', label: 'Top-down' },
  { id: 'goal', label: 'Behind goal' },
  { id: 'follow', label: 'Follow ball' },
]
