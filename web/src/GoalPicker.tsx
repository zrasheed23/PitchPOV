import { useMemo, useState } from 'react'
import { type GoalEntry, groupScorers, scoreLabel, searchScorers } from './goals'

interface GoalPickerProps {
  index: GoalEntry[]
  current: GoalEntry | null
  onPick: (goal: GoalEntry) => void
}

// Search box -> matching scorers -> that scorer's goals.
export function GoalPicker({ index, current, onPick }: GoalPickerProps) {
  const scorers = useMemo(() => groupScorers(index), [index])
  const [query, setQuery] = useState('')
  const [playerId, setPlayerId] = useState<string | null>(null)
  const player = scorers.find((s) => s.id === playerId) ?? null
  const matches = useMemo(() => searchScorers(scorers, query), [scorers, query])

  return (
    <div className="picker">
      {player ? (
        <>
          <button type="button" className="back" onClick={() => setPlayerId(null)}>
            ← All scorers
          </button>
          <div className="picker-title">
            {player.name} <span className="muted">{player.teamShort}</span>
          </div>
          <ul className="list">
            {player.goals.map((g) => (
              <li key={g.gameEventId}>
                <button
                  type="button"
                  aria-pressed={current?.gameEventId === g.gameEventId}
                  onClick={() => onPick(g)}
                >
                  <span>
                    vs {g.opponent} · {g.minute}
                    {g.ownGoal && <span className="tag">OG</span>}
                  </span>
                  <span className="muted">
                    {scoreLabel(g)}
                    {g.stage && ` · ${g.stage}`}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <>
          <input
            type="search"
            className="search"
            placeholder="Search scorers"
            aria-label="Search scorers"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
          <ul className="list">
            {matches.map((s) => (
              <li key={s.id}>
                <button type="button" onClick={() => setPlayerId(s.id)}>
                  <span>
                    {s.name} <span className="muted">{s.teamShort}</span>
                  </span>
                  <span className="muted">
                    {s.goals.length} {s.goals.length === 1 ? 'goal' : 'goals'}
                  </span>
                </button>
              </li>
            ))}
            {matches.length === 0 && <li className="muted empty">No scorer matches “{query}”</li>}
          </ul>
        </>
      )}
    </div>
  )
}
