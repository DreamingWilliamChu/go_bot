// Undo / Pass / Redo, and below them Keep color with Bonus Mode beside it. Shared by the standard
// board and the bonus-game board, so the controls sit in the same place in both.

export interface LockButton {
  color: 'B' | 'W' // the colour the lock holds (or would hold)
  on: boolean
  label: string
  title: string
  readOnly?: boolean // Bonus Mode: only a bonus turn turns it on
  onClick: () => void
}

interface Props {
  canUndo: boolean
  canRedo: boolean
  busy?: boolean
  onUndo: () => void
  onPass: () => void
  onRedo: () => void
  keep: LockButton
  bonusOn: boolean
  onBonus: () => void
}

export function MovesToolbar(p: Props) {
  const k = p.keep
  return (
    <>
      <div class="toolbar moves">
        <button class="btn" onClick={p.onUndo} disabled={!p.canUndo || p.busy}>
          Undo
        </button>
        <button class="btn" onClick={p.onPass} disabled={p.busy} data-testid="pass">
          Pass
        </button>
        <button class="btn" onClick={p.onRedo} disabled={!p.canRedo || p.busy}>
          Redo
        </button>
      </div>
      <div class="toolbar locks">
        <button
          class={`btn keep-color ${k.color}${k.on ? ' on' : ''}${k.readOnly ? ' read-only' : ''}`}
          onClick={k.onClick}
          aria-pressed={k.on}
          title={k.title}
          data-testid="keep-color"
        >
          {k.label}
        </button>
        <button
          class={`btn bonus-mode${p.bonusOn ? ' on' : ''}`}
          onClick={p.onBonus}
          aria-pressed={p.bonusOn}
          title={p.bonusOn ? 'Bonus Mode is on: random bonus turns. Tap to go back to standard Go' : 'Play bonus-turn Go (random bonus turns of several moves)'}
          data-testid="bonus-mode"
        >
          {p.bonusOn ? 'Bonus Mode · on' : 'Bonus Mode'}
        </button>
      </div>
    </>
  )
}
