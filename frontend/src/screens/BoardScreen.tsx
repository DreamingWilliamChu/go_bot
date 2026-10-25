import { useMemo, useRef, useState } from 'preact/hooks'
import type { AnalysisResult, EngineStatus, Preset } from '../api/client'
import { AnalysisPanel, type AnalysisStatus, type PvSelection } from '../analysis/AnalysisPanel'
import { colorName } from '../analysis/format'
import { BoardView, type CandidateMark, type PvMark } from '../board/BoardView'
import { MovesToolbar } from '../board/MovesToolbar'
import { boardWithLine, candidateMarks, lockPrediction, overlayLine, signMapOf } from '../board/overlay'
import { GameSettingsForm } from '../game/GameSettingsForm'
import {
  type Color,
  type GameState,
  type Setup,
  captures,
  collapseToSetup,
  currentBoard,
  deadGroupsInSetup,
  eraseStone,
  fromSetup,
  moveCount,
  newGame,
  nextPlayer,
  pass,
  play,
  redo,
  setPoint,
  sign,
  undo,
} from '../game/game'
import type { KeepRound, Point } from '../game/keepRound'
import { exportSgf, importSgf } from '../game/sgf'
import { stdKey } from '../analysis/useAnalysis'

type Palette = 'B' | 'W' | 'erase' | 'cycle'

export interface AnalysisBundle {
  status: AnalysisStatus
  result: AnalysisResult | null
  error: string | null
  warning: string | null
  connection: string
  forKey: string | null
}

interface Props {
  game: GameState
  setGame: (g: GameState) => void
  keepColor: Color | null
  setKeepColor: (c: Color | null) => void
  prepareKeep: (c: Color | null, moves: number) => void
  keepRound: KeepRound
  advanceKeep: (shown: Point[], played: Point | null) => void
  keepMoves: number
  onBonusMode: () => void
  erasing: boolean
  setErasing: (on: boolean, resume?: boolean) => void
  analysis: AnalysisBundle
  engine: EngineStatus | null
  presets: Preset[]
  presetId: string
  customSeconds: number
  showOwnership: boolean
  autoAnalyze: boolean
  onPreset: (id: string) => void
  onCustomSeconds: (s: number) => void
  onAnalyze: () => void
  onStop: () => void
  onToggleOwnership: () => void
  onToggleAuto: () => void
  toast: (msg: string) => void
}

export function BoardScreen(p: Props) {
  const { game, setGame, keepColor, setKeepColor, prepareKeep } = p
  const [mode, setMode] = useState<'play' | 'setup'>('play')
  const [palette, setPalette] = useState<Palette>('cycle')
  const [draft, setDraft] = useState<Setup | null>(null)
  const [pvSel, setPvSel] = useState<PvSelection | null>(null)
  const [draftKeepMoves, setDraftKeepMoves] = useState(p.keepMoves)
  const fileRef = useRef<HTMLInputElement>(null)
  const draftBase = useRef<Setup | null>(null) // the draft as Edit position opened it

  const board = useMemo(() => currentBoard(game), [game])
  const toMove = keepColor ?? nextPlayer(game)
  const caps = useMemo(() => captures(game), [game])
  // The last move played (not an erase), while its stone is still there.
  const lastMove = useMemo(() => {
    const m = [...game.moves].reverse().find((r) => !r.erase)
    if (!m || m.x === null || m.y === null) return null
    return board.get([m.x, m.y]) === sign(m.color) ? ([m.x, m.y] as [number, number]) : null
  }, [game, board])

  // Only show analysis that belongs to this exact position.
  const result = p.analysis.forKey === stdKey(game.revision) ? p.analysis.result : null
  const resultPv = pvSel && result ? result.candidates[pvSel.cand] : null
  // Keep color: predictions for the moves left in this round. Ones kept from before stay where
  // they were (unless filled since); otherwise the best line of the latest analysis.
  const prediction: Point[] | null = useMemo(() => {
    if (!keepColor || mode !== 'play' || p.erasing) return null
    return lockPrediction(board, result, p.keepRound.frozen, p.keepMoves - p.keepRound.done)
  }, [keepColor, mode, p.erasing, p.keepRound, p.keepMoves, board, result])

  // What the board draws on top of the position: a chosen line, else the Keep color predictions
  // (see-through, numbered by their place in the round).
  const line = useMemo(
    () => overlayLine(resultPv, pvSel, prediction, keepColor, p.keepRound.done + 1),
    [resultPv, pvSel, prediction, keepColor, p.keepRound.done],
  )

  const view = useMemo((): { signMap: ReturnType<typeof signMapOf>; pv: PvMark[] } => {
    if (mode === 'setup' && draft) {
      const g = fromSetup(draft)
      return { signMap: signMapOf(currentBoard(g)), pv: [] }
    }
    return boardWithLine(board, line)
  }, [mode, draft, board, line])

  const candMarks: CandidateMark[] = useMemo(() => {
    if (pvSel || keepColor || mode !== 'play') return []
    return candidateMarks(result, game.setup.size)
  }, [result, pvSel, keepColor, mode, game.setup.size])

  const deadInDraft = useMemo(() => {
    if (mode !== 'setup' || !draft) return undefined
    return new Set(deadGroupsInSetup(draft).map((s) => `${s.x},${s.y}`))
  }, [mode, draft])

  function onPoint(x: number, y: number) {
    if (mode === 'setup' && draft) {
      const cur = draft.initialStones.find((s) => s.x === x && s.y === y)?.color ?? null
      let next: Color | null
      if (palette === 'B') next = cur === 'B' ? null : 'B'
      else if (palette === 'W') next = cur === 'W' ? null : 'W'
      else if (palette === 'erase') next = null
      else next = cur === null ? 'B' : cur === 'B' ? 'W' : null
      setDraft(setPoint(draft, x, y, next))
      return
    }
    if (pvSel) setPvSel(null)
    if (p.erasing) {
      const next = eraseStone(game, x, y)
      if (!next) return
      if (keepColor) prepareKeep(keepColor, p.keepMoves)
      setGame(next)
      return
    }
    const r = play(game, x, y, toMove)
    if (!r.ok) {
      p.toast(r.reason)
      return
    }
    if (keepColor) p.advanceKeep(prediction ?? [], { x, y })
    setGame(r.game)
  }

  // Undo / Redo: the round's predictions no longer fit, start a fresh round.
  function onHistory(g: GameState) {
    if (keepColor) prepareKeep(keepColor, p.keepMoves)
    setGame(g)
  }

  // No confirm: the game only changes on Done, and Cancel keeps the moves.
  function enterSetup() {
    const d = { ...collapseToSetup(game, game.moves.length ? 'manual' : game.setup.source), initialPlayer: toMove }
    p.setErasing(false, false)
    draftBase.current = d
    setDraft(d)
    setPvSel(null)
    setDraftKeepMoves(p.keepMoves)
    setMode('setup')
  }

  function finishSetup() {
    if (!draft) return
    const dead = deadGroupsInSetup(draft)
    if (dead.length) {
      p.toast(`${dead.length} stone(s) have no liberties. Remove them or add liberties first.`)
      return
    }
    // With Keep color on, the "to play" choice is the colour that keeps playing.
    prepareKeep(keepColor ? draft.initialPlayer : null, draftKeepMoves)
    setGame(fromSetup(draft, game.revision + 1))
    setDraft(null)
    setMode('play')
  }

  function onNewGame() {
    const s = game.setup
    if ((game.moves.length || s.initialStones.length) && !confirm('Start a new empty game?')) return
    p.setErasing(false, false)
    prepareKeep(null, p.keepMoves)
    setGame({ ...newGame(s.size, s.rules, s.komi), revision: game.revision + 1 })
    setPvSel(null)
  }

  // Untouched draft: save the game with its moves; edited: the position as it is on the board.
  function onExport() {
    const text = exportSgf(draft && draft !== draftBase.current ? fromSetup(draft) : game)
    const blob = new Blob([text], { type: 'application/x-go-sgf' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `go-${new Date().toISOString().slice(0, 16).replace(/[:T]/g, '-')}.sgf`
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(url), 5000)
  }

  async function onImport(file: File) {
    try {
      const text = await file.text()
      const { game: g, warnings } = importSgf(text, { rules: game.setup.rules, komi: game.setup.komi })
      prepareKeep(null, p.keepMoves)
      setGame({ ...g, revision: game.revision + 1 })
      setMode('play')
      setPvSel(null)
      p.toast(warnings.length ? warnings.join(' ') : `Loaded ${file.name}`)
    } catch (e) {
      p.toast((e as Error).message)
    }
  }

  const s = game.setup
  const photoNote = s.source === 'photo' && game.moves.length === 0

  return (
    <div class="screen board-screen">
      <div class="board-col">
        <div class="board-wrap">
          <BoardView
            size={mode === 'setup' && draft ? draft.size : s.size}
            signMap={view.signMap}
            lastMove={mode === 'play' && !pvSel ? lastMove : null}
            candidates={candMarks}
            pv={view.pv}
            ownership={p.showOwnership && !pvSel && mode === 'play' ? result?.blackOwnership ?? null : null}
            highlight={deadInDraft}
            onPoint={onPoint}
            ariaLabel={p.erasing ? 'Go board, tap a stone to erase it' : `Go board, ${colorName(toMove)} to play`}
          />
        </div>

        {mode === 'play' ? (
          <>
            <div class="game-line small">
              <span>
                {s.size}×{s.size} · {s.rules} · komi {s.komi}
              </span>
              <span>
                Move {moveCount(game)} · captures B {caps.B} / W {caps.W}
              </span>
            </div>
            <MovesToolbar
              canUndo={game.moves.length > 0}
              canRedo={game.redo.length > 0}
              onUndo={() => onHistory(undo(game))}
              onPass={() => {
                if (keepColor) p.advanceKeep(prediction ?? [], null)
                setGame(pass(game, toMove))
              }}
              onRedo={() => onHistory(redo(game))}
              keep={{
                color: keepColor ?? toMove,
                on: keepColor !== null,
                label: keepColor ? `Keep ${colorName(keepColor)} · manual` : `Keep ${colorName(toMove)}`,
                title: keepColor
                  ? `${colorName(keepColor)} keeps playing until you tap again (no move limit). Tap to turn colours back to alternating`
                  : `Let ${colorName(toMove)} keep playing, move after move, until you tap again`,
                onClick: () => setKeepColor(keepColor ? null : toMove),
              }}
              bonusOn={false}
              onBonus={p.onBonusMode}
            />
            <div class="toolbar">
              <button class="btn" onClick={enterSetup} data-testid="edit-position">
                Edit position
              </button>
              <button
                class={p.erasing ? 'btn erase on' : 'btn erase'}
                onClick={() => p.setErasing(!p.erasing)}
                aria-pressed={p.erasing}
                title={p.erasing ? 'Stop erasing and analyse again' : 'Tap stones to take them off the board'}
                data-testid="erase"
              >
                {p.erasing ? 'Erasing' : 'Erase'}
              </button>
              <button class="btn" onClick={onNewGame} data-testid="new-game">
                New game
              </button>
            </div>
            {photoNote ? (
              <p class="note small">
                Position from a photo. One photo cannot show earlier moves, captures or a current ko ban, so KataGo
                treats this as a fresh position with {colorName(s.initialPlayer)} to play. Moves you enter from here
                on are kept and used in the analysis.
              </p>
            ) : null}
          </>
        ) : draft ? (
          <div class="panel setup-panel">
            <div class="seg palette" role="radiogroup" aria-label="Stone to place">
              {(
                [
                  ['cycle', 'Tap: ● → ○ → ·'],
                  ['B', 'Black'],
                  ['W', 'White'],
                  ['erase', 'Erase'],
                ] as [Palette, string][]
              ).map(([id, label]) => (
                <button key={id} class={palette === id ? 'on' : ''} onClick={() => setPalette(id)} aria-checked={palette === id} role="radio">
                  {label}
                </button>
              ))}
            </div>
            <GameSettingsForm
              value={{ size: draft.size, player: draft.initialPlayer, rules: draft.rules, komi: draft.komi }}
              onChange={(v) => {
                if (v.size !== draft.size) {
                  if (draft.initialStones.length && !confirm('Changing the board size clears the board. Continue?')) return
                  setDraft({ ...draft, size: v.size, initialStones: [], initialPlayer: v.player ?? 'B', rules: v.rules, komi: v.komi })
                } else setDraft({ ...draft, initialPlayer: v.player ?? 'B', rules: v.rules, komi: v.komi })
              }}
              idPrefix="setup"
            />
            <fieldset class="field">
              <legend>Keep color: moves to look ahead in the analysis</legend>
              <div class="seg">
                {[1, 2, 3, 4, 5, 6].map((n) => (
                  <button
                    key={n}
                    type="button"
                    class={draftKeepMoves === n ? 'on' : ''}
                    aria-pressed={draftKeepMoves === n}
                    data-testid={`keep-moves-${n}`}
                    onClick={() => setDraftKeepMoves(n)}
                  >
                    {n}
                  </button>
                ))}
              </div>
            </fieldset>
            {deadInDraft && deadInDraft.size ? (
              <p class="note error small">Highlighted stones have no liberties.</p>
            ) : null}
            <div class="row">
              <button class="btn" onClick={() => setDraft({ ...draft, initialStones: [] })}>
                Clear board
              </button>
              <button class="btn" onClick={onExport}>
                Save SGF
              </button>
              <button class="btn" onClick={() => fileRef.current?.click()}>
                Open SGF
              </button>
              <input
                ref={fileRef}
                type="file"
                accept=".sgf,application/x-go-sgf,text/plain"
                hidden
                onChange={(e) => {
                  const f = (e.target as HTMLInputElement).files?.[0]
                  if (f) void onImport(f)
                  ;(e.target as HTMLInputElement).value = ''
                }}
              />
              <button
                class="btn"
                onClick={() => {
                  setDraft(null)
                  setMode('play')
                }}
              >
                Cancel
              </button>
              <button class="btn primary" onClick={finishSetup} data-testid="setup-done">
                Done
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div class="side-col">
        {mode === 'play' ? (
          <AnalysisPanel
            status={p.analysis.status}
            result={result}
            error={p.analysis.error}
            warning={p.analysis.warning}
            engine={p.engine}
            presets={p.presets}
            presetId={p.presetId}
            customSeconds={p.customSeconds}
            showOwnership={p.showOwnership}
            autoAnalyze={p.autoAnalyze}
            paused={p.erasing}
            pvSel={result ? pvSel : null}
            connection={p.analysis.connection}
            onPreset={p.onPreset}
            onCustomSeconds={p.onCustomSeconds}
            onAnalyze={() => {
              setPvSel(null)
              p.onAnalyze()
            }}
            onStop={p.onStop}
            onToggleOwnership={p.onToggleOwnership}
            onToggleAuto={p.onToggleAuto}
            onPv={setPvSel}
          />
        ) : (
          <div class="panel">
            <h2>Edit position</h2>
            <p class="small">
              Tap points to place or remove stones. Choose who plays next, the rules and komi, then press Done. Analysis
              starts from this position without any move history; Cancel keeps the moves played so far.
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
