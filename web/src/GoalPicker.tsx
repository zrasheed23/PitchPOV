import { useMemo, useState } from 'react'
import { type GoalEntry, groupScorers, scoreLabel, searchScorers } from './goals'

interface GoalPickerProps {
  index: GoalEntry[]
  current: GoalEntry | null
  onPick: (goal: GoalEntry) => void
}

interface GoalRowProps {
  goal: GoalEntry
  current: GoalEntry | null
  onPick: (goal: GoalEntry) => void
  showScorer?: boolean
}

function GoalRow({ goal: g, current, onPick, showScorer = false }: GoalRowProps) {
  return (
    <li>
      <button type="button" aria-pressed={current?.gameEventId === g.gameEventId} onClick={() => onPick(g)}>
        <span>
          {g.review.length > 0 && (
            <span className="review-dot" role="img" aria-label="Needs review" title={g.review.join('\n')} />
          )}
          {showScorer && `${g.scorer} `}vs {g.opponent} · {g.minute}
          {g.ownGoal && <span className="tag">OG</span>}
        </span>
        <span className="muted">
          {scoreLabel(g)}
          {g.stage && ` · ${g.stage}`}
        </span>
      </button>
    </li>
  )
}

// Search box -> matching scorers -> that scorer's goals. The "Needs review"
// filter swaps the scorer list for every flagged goal, in index order.
export function GoalPicker({ index, current, onPick }: GoalPickerProps) {
  const scorers = useMemo(() => groupScorers(index), [index])
  const flagged = useMemo(() => index.filter((g) => g.review.length > 0), [index])
  const [query, setQuery] = useState('')
  const [reviewOnly, setReviewOnly] = useState(false)
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
              <GoalRow key={g.gameEventId} goal={g} current={current} onPick={onPick} />
            ))}
          </ul>
        </>
      ) : (
        <>
          <div className="picker-bar">
            <input
              type="search"
              className="search"
              placeholder="Search scorers"
              aria-label="Search scorers"
              value={query}
              disabled={reviewOnly}
              onChange={(e) => setQuery(e.target.value)}
            />
            <button type="button" aria-pressed={reviewOnly} onClick={() => setReviewOnly((r) => !r)}>
              <span className="review-dot" aria-hidden="true" />
              Needs review ({flagged.length})
            </button>
          </div>
          {reviewOnly ? (
            <ul className="list">
              {flagged.map((g) => (
                <GoalRow key={g.gameEventId} goal={g} current={current} onPick={onPick} showScorer />
              ))}
              {flagged.length === 0 && <li className="muted empty">No goals need review</li>}
            </ul>
          ) : (
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
          )}
        </>
      )}
    </div>
  )
}
