import { BonusSettings, bonusPrefsValid } from './BonusSettings'
import type { BonusPrefs } from './types'

interface Props {
  prefs: BonusPrefs
  setPrefs: (p: BonusPrefs) => void
  canResume: boolean
  canBranch: boolean // the standard board has a position worth branching from
  busy?: boolean
  onResume: () => void
  onNew: () => void
  onBranch: () => void
  onCancel: () => void
}

/** Turning Bonus Mode on never changes the standard game: it starts or resumes a separate bonus game. */
export function BonusEntryDialog(p: Props) {
  const ok = bonusPrefsValid(p.prefs)
  return (
    <div class="modal-backdrop" role="dialog" aria-modal="true" aria-label="Bonus Mode">
      <div class="modal panel" data-testid="bonus-entry-dialog">
        <h2>Bonus Mode</h2>
        <p class="small">
          Bonus-turn Go: each player has a few bonus turns of several moves in a row,{' '}
          {p.prefs.trigger === 'judge'
            ? 'granted by a judge at the start of a turn. To the players and the analysis their timing is random.'
            : 'timed at random and kept secret by the Mac until they start.'}{' '}
          Your standard game stays as it is and comes back when you turn Bonus Mode off.
        </p>
        {p.canResume ? (
          <button class="btn primary big wide" onClick={p.onResume} disabled={p.busy} data-testid="bonus-resume">
            Resume the bonus game
          </button>
        ) : null}
        <BonusSettings value={p.prefs} onChange={p.setPrefs} idPrefix="entry" />
        <div class="row">
          <button class="btn" onClick={p.onCancel}>
            Cancel
          </button>
          <button
            class="btn"
            onClick={p.onBranch}
            disabled={!p.canBranch || !ok || p.busy}
            title="Analyse the current position as a bonus game; you enter the bonus state"
            data-testid="bonus-branch"
          >
            Bonus analysis of this position
          </button>
          <button class={p.canResume ? 'btn' : 'btn primary'} onClick={p.onNew} disabled={!ok || p.busy} data-testid="bonus-new">
            New bonus game
          </button>
        </div>
      </div>
    </div>
  )
}
