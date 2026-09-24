// Team colors for the players, with a fallback when the two kits clash.

import type { Team, TeamSide } from './clip'

// CIE76 color difference below which two kits are hard to tell apart.
const CLASH_DELTA_E = 25
const FALLBACKS = ['#ffd23f', '#ff4fa3', '#22d3ee', '#111111']

function toLab(hex: string): [number, number, number] {
  const n = parseInt(hex.replace('#', ''), 16)
  const lin = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map((c) => {
    const v = c / 255
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  })
  const [r, g, b] = lin
  // sRGB -> XYZ (D65), normalized by the white point.
  const xyz = [
    (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047,
    0.2126 * r + 0.7152 * g + 0.0722 * b,
    (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883,
  ].map((v) => (v > 0.008856 ? Math.cbrt(v) : 7.787 * v + 16 / 116))
  const [x, y, z] = xyz
  return [116 * y - 16, 500 * (x - y), 200 * (y - z)]
}

export function colorDistance(a: string, b: string): number {
  const la = toLab(a)
  const lb = toLab(b)
  return Math.hypot(la[0] - lb[0], la[1] - lb[1], la[2] - lb[2])
}

const clashes = (a: string, b: string) => colorDistance(a, b) < CLASH_DELTA_E

// Home keeps its kit. If away's clashes with it, try away's secondary color,
// then a fixed list of bright fallbacks.
export function kitColors(teams: Record<TeamSide, Team>): Record<TeamSide, string> {
  const home = teams.home.color
  const away = [teams.away.color, teams.away.secondaryColor, ...FALLBACKS].find((c) => c && !clashes(home, c))
  return { home, away: away ?? FALLBACKS[0] }
}
