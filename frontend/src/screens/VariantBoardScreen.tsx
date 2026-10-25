import type { ComponentChildren } from 'preact'
import { useMemo, useRef, useState } from 'preact/hooks'
import GoBoard from '@sabaki/go-board'
import { colorName } from '../analysis/format'
import { BoardView } from '../board/BoardView'
import { MovesToolbar } from '../board/MovesToolbar'
import { boardWithLine } from '../board/overlay'
import type { VariantAction } from '../variant/api'
import { BonusSettings, bonusPrefsValid } from '../variant/BonusSettings'
import {
  announcementText,
  bonusesLeftText,
  chanceText,
  lockText,
  provenanceText,
  resultText,
  turnText,
} from '../variant/text'
import type { BonusPrefs, VariantView } from '../variant/types'

interface Props {
  view: VariantView
  busy: boolean
  act: (a: VariantAction) => Promise<VariantView | null>
  prefs: BonusPrefs
  setPrefs: (p: BonusPrefs) => void
  onNewGame: () => void // with `prefs`
  onLeave: () => void // Bonus Mode off
  onOpenRecord: (file: File) => void
  toast: (msg: string) => void
  analysis?: ComponentChildren
}

export function goBoardOf(v: VariantView): GoBoard {
  const n = v.state.size
  let b = GoBoard.fromDimensions(n)
  v.state.grid.forEach((c, i) => {
    if (c) b = b.set([i % n, Math.floor(i / n)], c === 1 ? 1 : -1)
  })
  return b
}

export function VariantBoardScreen(p: Props) {
  const { view, act } = p
  const s = view.state
  const cfg = s.config
  const [settingsOpen, setSettingsOpen] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const board = useMemo(() => goBoardOf(view), [view])
  const drawn = useMemo(() => boardWithLine(board, null), [board])
  const lock = lockText(view)
  const announcement = announcementText(view)
  const over = s.phase === 'terminal'
  const note = provenanceText(view)
  const last = s.lastPlacement && s.grid[s.lastPlacement.y * s.size + s.lastPlacement.x] ? s.lastPlacement : null

  function onPoint(x: number, y: number) {
    if (over) {
      p.toast('The game is over. Undo to continue, or start a new bonus game.')
      return
    }
    void act({ type: 'place', x, y })
  }

  function newGame() {
    if (view.actions.length && !over && !confirm('Start a new bonus game? This one is replaced.')) return
    setSettingsOpen(false)
    p.onNewGame()
  }

  function save() {
    const a = document.createElement('a')
    a.href = `/api/variant/games/${view.gameId}/export`
    a.download = `bonus-go-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.json`
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  return (
    <div class="screen board-screen">
      <div class="board-col">
        {/* Always the same height: the board must not move between taps (a vanishing banner made
            the next tap land a row off). */}
        <div
          class={`bonus-banner${announcement ? ' announce' : s.bonusActive && !over ? ' active' : ''}`}
          role="status"
          data-testid="bonus-turn"
        >
          <span class={`dot ${s.toMove}`} />{' '}
          {announcement ? <span data-testid="bonus-announcement">{announcement}</span> : turnText(view)}
        </div>
        <div class="board-wrap">
          <BoardView
            size={s.size}
            signMap={drawn.signMap}
            lastMove={last ? [last.x, last.y] : null}
            pv={drawn.pv}
            onPoint={onPoint}
            ariaLabel={`Bonus Go board, ${colorName(s.toMove)} to play`}
          />
        </div>

        <div class={`bonus-status${s.bonusActive && !over ? ' active' : ''}`} data-testid="bonus-status">
          <div data-testid="bonuses-left">{bonusesLeftText(view)}</div>
          {!over ? (
            <div class="muted small" data-testid="bonus-chance">
              {chanceText(view)}
            </div>
          ) : null}
        </div>

        <div class="game-line small">
          <span>
            {cfg.size}×{cfg.size} · chinese · komi {cfg.komi} · {cfg.moves_per_bonus} moves per bonus · H {cfg.horizon}
          </span>
          <span>
            Move {s.placements} · captures B {s.captures.B} / W {s.captures.W}
          </span>
        </div>

        <MovesToolbar
          canUndo={view.canUndo}
          canRedo={view.canRedo}
          busy={p.busy}
          onUndo={() => void act({ type: 'undo' })}
          onPass={() => (over ? p.toast('The game is over.') : void act({ type: 'pass' }))}
          onRedo={() => void act({ type: 'redo' })}
          keep={{
            color: s.toMove,
            on: lock.on,
            label: lock.label,
            title: lock.title,
            readOnly: true,
            onClick: () =>
              p.toast(
                lock.on
                  ? `${lock.title} Taps cannot change it.`
                  : 'Bonus Mode: Keep color turns on only for a bonus turn. For an unlimited manual Keep, turn Bonus Mode off.',
              ),
          }}
          bonusOn
          onBonus={p.onLeave}
        />
        <div class="toolbar">
          <button class="btn" onClick={newGame} data-testid="bonus-new-game">
            New bonus game
          </button>
          <button
            class={settingsOpen ? 'btn on-soft' : 'btn'}
            onClick={() => setSettingsOpen(!settingsOpen)}
            aria-expanded={settingsOpen}
            data-testid="bonus-settings-toggle"
          >
            Bonus settings
          </button>
          <button class="btn" onClick={save}>
            Save game
          </button>
          <button class="btn" onClick={() => fileRef.current?.click()}>
            Open game
          </button>
          <input
            ref={fileRef}
            type="file"
            accept=".json,application/json"
            hidden
            onChange={(e) => {
              const f = (e.target as HTMLInputElement).files?.[0]
              if (f) p.onOpenRecord(f)
              ;(e.target as HTMLInputElement).value = ''
            }}
          />
        </div>
        {settingsOpen ? (
          <div class="panel" data-testid="bonus-settings">
            <p class="note small">These settings apply to the next game (or a restart), never to the counts of this one.</p>
            <BonusSettings value={p.prefs} onChange={p.setPrefs} idPrefix="next" />
            <div class="row">
              <button class="btn primary" onClick={newGame} disabled={!bonusPrefsValid(p.prefs)} data-testid="bonus-restart">
                Restart with these settings
              </button>
            </div>
          </div>
        ) : null}
        {note ? (
          <p class="note small" data-testid="bonus-provenance">
            {note}
          </p>
        ) : null}
        {over && s.result ? (
          <div class="panel bonus-result" data-testid="bonus-result">
            <b>{resultText(s.result)}</b>
            <p class="muted tiny">
              Dead stones are not removed: capture them before passing. Two turns in a row that were only a pass end
              the game.
            </p>
            {view.reveal ? (
              <p class="muted tiny">
                Bonus schedule (now revealed): Black turns {view.reveal.schedule.B.join(', ') || 'none'}; White turns{' '}
                {view.reveal.schedule.W.join(', ') || 'none'}. Commitment {view.commitment.slice(0, 16)}…
              </p>
            ) : null}
          </div>
        ) : null}
      </div>

      <div class="side-col">
        {p.analysis ?? (
          <div class="panel">
            <h2>Bonus game</h2>
            <p class="small">
              Bonus turns are hidden and random. When one starts, it is announced before you move, Keep color locks the
              player for that many moves, then releases by itself.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
