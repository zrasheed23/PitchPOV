// Goal index written by the pipeline (clips/index.json), plus player search.

export interface GoalEntry {
  gameId: string
  gameEventId: number
  clip: string // file name in clips/
  date: string
  stage: string | null
  scorer: string
  scorerId: string
  team: string
  teamShort: string
  opponent: string
  opponentShort: string
  period: number
  clock: string
  minute: string // e.g. "36'" or "45+2'"
  score: { home: number; away: number; homeShort: string; awayShort: string } // right after the goal
  ownGoal: boolean
}

export interface Scorer {
  id: string
  name: string
  teamShort: string
  goals: GoalEntry[]
}

// Lowercase and strip accents so "mbappe" matches "Mbappé". NFD splits most
// accented letters; a few letters have no decomposition and are mapped by hand.
const EXTRA: Record<string, string> = { ø: 'o', ł: 'l', đ: 'd', ß: 'ss', æ: 'ae', œ: 'oe', ı: 'i' }

export function fold(s: string): string {
  return s
    .normalize('NFD')
    .replace(/\p{M}/gu, '')
    .toLowerCase()
    .replace(/[øłđßæœı]/g, (c) => EXTRA[c])
}

// Group the index by scorer, most goals first, then by name.
export function groupScorers(index: GoalEntry[]): Scorer[] {
  const byId = new Map<string, Scorer>()
  for (const g of index) {
    let s = byId.get(g.scorerId)
    if (!s) {
      s = { id: g.scorerId, name: g.scorer, teamShort: g.teamShort, goals: [] }
      byId.set(g.scorerId, s)
    }
    s.goals.push(g)
  }
  return [...byId.values()].sort((a, b) => b.goals.length - a.goals.length || a.name.localeCompare(b.name))
}

export function searchScorers(scorers: Scorer[], query: string): Scorer[] {
  const q = fold(query.trim())
  if (!q) return scorers
  return scorers.filter((s) => fold(s.name).includes(q))
}

export function scoreLabel(g: GoalEntry): string {
  const { home, away, homeShort, awayShort } = g.score
  return `${homeShort} ${home}–${away} ${awayShort}`
}
