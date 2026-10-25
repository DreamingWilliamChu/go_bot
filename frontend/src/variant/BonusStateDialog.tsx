import { useMemo, useState } from 'preact/hooks'
import { colorName } from '../analysis/format'
import type { Stone } from '../game/game'
import { type BonusPrefs, type BonusStateInput, horizonFor } from './types'

interface Props {
  title: string
  stones: Stone[]
  toMove: 'B' | 'W'
  prefs: BonusPrefs // size, komi, moves per bonus and H of the new game
  provenance: 'photo' | 'branch'
  busy?: boolean
  onStart: (s: BonusStateInput) => void
  onCancel: () => void
  onStandard?: () => void // photo: analyse as ordinary Go instead
}

/** Explicit bonus state for a position whose history is unknown (a photo or an edited position). */
export function BonusStateDialog(p: Props) {
  const h = horizonFor(p.prefs)
  const k = p.prefs.movesPerBonus
  const counts = useMemo(() => {
    const b = p.stones.filter((s) => s.color === 'B').length
    const w = p.stones.filter((s) => s.color === 'W').length
    return { B: b, W: w }
  }, [p.stones])
  // A starting guess from the stone counts; the dialog says it must be checked.
  const guess = (c: 'B' | 'W') => Math.min(h, Math.max(c === p.toMove ? 1 : 0, counts[c] + (c === p.toMove ? 1 : 0)))
  const [toMove, setToMove] = useState<'B' | 'W'>(p.toMove)
  const [turns, setTurns] = useState({ B: guess('B'), W: guess('W') })
  const [remaining, setRemaining] = useState({ B: Math.min(p.prefs.blackBonuses, h), W: Math.min(p.prefs.whiteBonuses, h) })
  const [bonusActive, setBonusActive] = useState(false)
  const [movesLeft, setMovesLeft] = useState(k)
  const [passStreak, setPassStreak] = useState(0)

  const problems: string[] = []
  for (const c of ['B', 'W'] as const) {
    const eligible = h - turns[c]
    if (remaining[c] > Math.max(0, eligible))
      problems.push(`${colorName(c)}: ${remaining[c]} bonuses left but only ${Math.max(0, eligible)} eligible turns remain (H ${h} − ${turns[c]} started).`)
  }
  if (turns[toMove] < 1) problems.push(`${colorName(toMove)}'s current turn counts as started: at least 1.`)

  const intField = (label: string, val: number, set: (n: number) => void, lo: number, hi: number, testid: string) => (
    <label class="field">
      {label}
      <input
        type="number"
        inputMode="numeric"
        min={lo}
        max={hi}
        value={val}
        data-testid={testid}
        onInput={(e) => {
          const n = Number((e.target as HTMLInputElement).value)
          if (Number.isInteger(n) && n >= lo && n <= hi) set(n)
        }}
      />
    </label>
  )

  return (
    <div class="modal-backdrop" role="dialog" aria-modal="true" aria-label={p.title}>
      <div class="modal panel" data-testid="bonus-state-dialog">
        <h2>{p.title}</h2>
        <p class="note warn small">
          A {p.provenance === 'photo' ? 'photo' : 'position'} cannot show the bonus history. Enter the bonus state
          yourself. History unknown: superko only checks positions from here on.
        </p>
        <fieldset class="field">
          <legend>Player in turn now</legend>
          <div class="seg">
            {(['B', 'W'] as const).map((c) => (
              <button key={c} type="button" class={toMove === c ? 'on' : ''} aria-pressed={toMove === c} onClick={() => setToMove(c)}>
                <span class={`dot ${c}`} /> {colorName(c)}
              </button>
            ))}
          </div>
        </fieldset>
        <div class="field-row">
          {intField('Black bonuses left', remaining.B, (n) => setRemaining({ ...remaining, B: n }), 0, h, 'bsd-remaining-B')}
          {intField('White bonuses left', remaining.W, (n) => setRemaining({ ...remaining, W: n }), 0, h, 'bsd-remaining-W')}
        </div>
        <div class="field-row">
          {intField('Black turns started', turns.B, (n) => setTurns({ ...turns, B: n }), 0, 1000, 'bsd-turns-B')}
          {intField('White turns started', turns.W, (n) => setTurns({ ...turns, W: n }), 0, 1000, 'bsd-turns-W')}
        </div>
        <p class="muted tiny">
          Turns started: every personal turn so far, normal or bonus, the current one included. The values above
          are a guess from the stone counts ({counts.B} black, {counts.W} white); check them. H = {h}.
        </p>
        <fieldset class="field">
          <legend>Current turn</legend>
          <div class="seg">
            <button type="button" class={!bonusActive ? 'on' : ''} aria-pressed={!bonusActive} onClick={() => setBonusActive(false)}>
              Normal (1 move)
            </button>
            <button type="button" class={bonusActive ? 'on' : ''} aria-pressed={bonusActive} onClick={() => setBonusActive(true)} data-testid="bsd-bonus-active">
              Bonus turn
            </button>
          </div>
        </fieldset>
        {bonusActive ? (
          <fieldset class="field">
            <legend>Moves left in this bonus turn</legend>
            <div class="seg">
              {Array.from({ length: k }, (_, i) => i + 1).map((n) => (
                <button key={n} type="button" class={movesLeft === n ? 'on' : ''} aria-pressed={movesLeft === n} onClick={() => setMovesLeft(n)}>
                  {n}
                </button>
              ))}
            </div>
          </fieldset>
        ) : null}
        <label class="toggle small">
          <input type="checkbox" checked={passStreak === 1} onChange={(e) => setPassStreak((e.target as HTMLInputElement).checked ? 1 : 0)} />
          The opponent's last turn was only a pass
        </label>
        {problems.map((m) => (
          <p key={m} class="note error small">
            {m}
          </p>
        ))}
        <div class="row">
          <button class="btn" onClick={p.onCancel}>
            Cancel
          </button>
          {p.onStandard ? (
            <button class="btn" onClick={p.onStandard} data-testid="bsd-standard">
              Analyse as standard Go
            </button>
          ) : null}
          <button
            class="btn primary"
            disabled={problems.length > 0 || p.busy}
            data-testid="bsd-start"
            onClick={() =>
              p.onStart({ toMove, remaining, turnsStarted: turns, bonusActive, movesLeft: bonusActive ? movesLeft : 1, passStreak })
            }
          >
            Start bonus game
          </button>
        </div>
      </div>
    </div>
  )
}
