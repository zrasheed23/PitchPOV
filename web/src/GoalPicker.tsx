import { useEffect, useMemo, useRef, useState } from 'react'
import { type GoalEntry, fold } from './goals'

// Goal browser: one column per round, group stage on the left to the final on
// the right. Each column lists its matches with their goals underneath. The
// search box narrows every column to goals by that player (or team).

const ROUNDS: { key: string; label: string; match: (stage: string) => boolean }[] = [
  { key: 'group', label: 'Group stage', match: (s) => s.startsWith('Group') },
  { key: 'r16', label: 'Round of 16', match: (s) => s === 'Round of 16' },
  { key: 'qf', label: 'Quarter-finals', match: (s) => s === 'Quarter-final' },
  { key: 'sf', label: 'Semi-finals', match: (s) => s === 'Semi-final' },
  { key: 'final', label: 'Final', match: (s) => s === 'Final' || s === 'Third place' },
]

interface Match {
  gameId: string
  title: string // e.g. "ARG 3–3 FRA": the score after the match's last goal in the full index
  subtitle: string // group matchday, when there is one
  goals: GoalEntry[]
}

// Group goals into matches (in index order, which is by date then time).
function matchesOf(goals: GoalEntry[], titles: Map<string, string>): Match[] {
  const byGame = new Map<string, Match>()
  for (const g of goals) {
    let m = byGame.get(g.gameId)
    if (!m) {
      const md = g.stage?.match(/Matchday (\d)/)
      const subtitle = md ? `Matchday ${md[1]}` : g.stage === 'Third place' ? 'Third place' : ''
      m = { gameId: g.gameId, title: titles.get(g.gameId) ?? '', subtitle, goals: [] }
      byGame.set(g.gameId, m)
    }
    m.goals.push(g)
  }
  // The final goes above the third-place match in the last column.
  return [...byGame.values()].sort((a, b) => Number(a.subtitle === 'Third place') - Number(b.subtitle === 'Third place'))
}

interface GoalPickerProps {
  index: GoalEntry[]
  current: GoalEntry | null
  onPick: (goal: GoalEntry) => void
  onClose: () => void
  open: boolean
}

export function GoalPicker({ index, current, onPick, onClose, open }: GoalPickerProps) {
  const [query, setQuery] = useState('')
  const [reviewOnly, setReviewOnly] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const flagged = useMemo(() => index.filter((g) => g.review.length > 0), [index])
  const titles = useMemo(() => {
    const t = new Map<string, string>()
    for (const g of index) {
      const { home, away, homeShort, awayShort } = g.score
      t.set(g.gameId, `${homeShort} ${home}–${away} ${awayShort}`) // later goals overwrite earlier ones
    }
    return t
  }, [index])

  useEffect(() => {
    if (!open) return
    input.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const columns = useMemo(() => {
    const q = fold(query.trim())
    const keep = (g: GoalEntry) =>
      (!reviewOnly || g.review.length > 0) &&
      (!q || fold(g.scorer).includes(q) || fold(g.team).includes(q) || fold(g.teamShort).includes(q))
    return ROUNDS.map((r) => {
      const goals = index.filter((g) => g.stage && r.match(g.stage) && keep(g))
      return { ...r, count: goals.length, matches: matchesOf(goals, titles) }
    })
  }, [index, query, reviewOnly, titles])

  const total = columns.reduce((n, c) => n + c.count, 0)

  return (
    <div className="browser" hidden={!open} role="dialog" aria-label="Pick a goal">
      <div className="browser-bar">
        <input
          ref={input}
          type="search"
          className="search"
          placeholder="Search a player or team"
          aria-label="Search a player or team"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <span className="muted browser-count">
          {total} {total === 1 ? 'goal' : 'goals'}
        </span>
        {flagged.length > 0 && (
          <button type="button" aria-pressed={reviewOnly} onClick={() => setReviewOnly((r) => !r)}>
            <span className="review-dot" aria-hidden="true" />
            Needs review ({flagged.length})
          </button>
        )}
        <button type="button" className="browser-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </div>

      <div className="rounds">
        {/* While searching, drop the rounds with no matching goals. */}
        {columns.filter((col) => col.count > 0 || (!query.trim() && !reviewOnly)).map((col) => (
          <section key={col.key} className="round" aria-label={col.label}>
            <h3>
              {col.label} <span className="muted">{col.count}</span>
            </h3>
            <div className="round-list">
              {col.matches.map((m, i) => (
                <div key={m.gameId} className="match">
                  {m.subtitle && m.subtitle !== col.matches[i - 1]?.subtitle && <div className="matchday">{m.subtitle}</div>}
                  <div className="match-title">{m.title}</div>
                  {m.goals.map((g) => (
                    <button
                      key={g.gameEventId}
                      type="button"
                      className="goal-row"
                      aria-pressed={current?.gameEventId === g.gameEventId}
                      onClick={() => onPick(g)}
                      title={g.review.length ? g.review.join('\n') : undefined}
                    >
                      <span className="goal-min">{g.minute}</span>
                      <span className="goal-scorer">
                        {g.review.length > 0 && <span className="review-dot" aria-hidden="true" />}
                        {g.scorer}
                        {g.ownGoal && <span className="tag">OG</span>}
                      </span>
                      <span className="goal-team muted">{g.teamShort}</span>
                    </button>
                  ))}
                </div>
              ))}
              {col.matches.length === 0 && <div className="muted empty">No goals</div>}
            </div>
          </section>
        ))}
      </div>
    </div>
  )
}
