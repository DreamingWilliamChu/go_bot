import { useMemo, useRef, useState } from 'preact/hooks'
import type GoBoard from '@sabaki/go-board'
import type { AnalysisResult, EngineStatus, Preset } from '../api/client'
import { AnalysisPanel, type AnalysisStatus, type PvSelection } from '../analysis/AnalysisPanel'
import { CAND_LABELS, colorName, pct, playerWinrate } from '../analysis/format'
import { BoardView, type CandidateMark, type PvMark, type Sign } from '../board/BoardView'
import { GameSettingsForm } from '../game/GameSettingsForm'
import {
  type Color,
  type GameState,
  type Setup,
  captures,
  collapseToSetup,
  currentBoard,
  deadGroupsInSetup,
  fromSetup,
  newGame,
  nextPlayer,
  pass,
  play,
  redo,
  setPoint,
  sign,
  undo,
} from '../game/game'
import { exportSgf, importSgf } from '../game/sgf'

type Palette = 'B' | 'W' | 'erase' | 'cycle'

export interface AnalysisBundle {
  status: AnalysisStatus
  result: AnalysisResult | null
  error: string | null
  warning: string | null
  connection: string
  forRevision: number | null
}

interface Props {
  game: GameState
  setGame: (g: GameState) => void
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

function signMapOf(b: GoBoard): Sign[][] {
  return b.signMap.map((row) => row.map((v) => (v > 0 ? 1 : v < 0 ? -1 : 0)) as Sign[])
}

export function BoardScreen(p: Props) {
  const { game, setGame } = p
  const [mode, setMode] = useState<'play' | 'setup'>('play')
  const [palette, setPalette] = useState<Palette>('cycle')
  const [draft, setDraft] = useState<Setup | null>(null)
  const [pvSel, setPvSel] = useState<PvSelection | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const board = useMemo(() => currentBoard(game), [game])
  const toMove = nextPlayer(game)
  const caps = useMemo(() => captures(game), [game])
  const lastMove = useMemo(() => {
    const m = game.moves[game.moves.length - 1]
    return m && m.x !== null && m.y !== null ? ([m.x, m.y] as [number, number]) : null
  }, [game])

  // Only show analysis that belongs to this exact position.
  const result = p.analysis.forRevision === game.revision ? p.analysis.result : null
  const resultPv = pvSel && result ? result.candidates[pvSel.cand] : null

  const view = useMemo(() => {
    if (mode === 'setup' && draft) {
      const g = fromSetup(draft)
      return { signMap: signMapOf(currentBoard(g)), pv: [] as PvMark[] }
    }
    if (resultPv && pvSel) {
      let b = board
      const placed: PvMark[] = []
      resultPv.pv.slice(0, pvSel.step).forEach((s, i) => {
        if (s.pass || s.x === null || s.y === null) return
        try {
          b = b.makeMove(sign(s.color), [s.x, s.y])
        } catch {
          return
        }
        placed.push({ x: s.x, y: s.y, color: s.color, n: i + 1 })
      })
      const sm = signMapOf(b)
      // drop numbers of PV stones that were captured later in the line
      const pv = placed.filter((m) => sm[m.y][m.x] === (m.color === 'B' ? 1 : -1))
      return { signMap: sm, pv }
    }
    return { signMap: signMapOf(board), pv: [] as PvMark[] }
  }, [mode, draft, board, resultPv, pvSel])

  const candMarks: CandidateMark[] = useMemo(() => {
    if (!result || pvSel || mode !== 'play' || !result.currentPlayer) return []
    const player = result.currentPlayer
    return result.candidates
      .slice(0, 3)
      .map((c, i) => ({ c, i }))
      .filter(({ c }) => !c.pass && c.x !== null && c.y !== null)
      .map(({ c, i }) => ({
        x: c.x as number,
        y: c.y as number,
        label: CAND_LABELS[i],
        rank: i,
        sub: game.setup.size <= 13 ? pct(playerWinrate(c.blackWinrate, player), 0) : undefined,
      }))
  }, [result, pvSel, mode, game.setup.size])

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
    const r = play(game, x, y)
    if (!r.ok) p.toast(r.reason)
    else setGame(r.game)
  }

  function enterSetup() {
    if (game.moves.length && !confirm('Editing turns the moves played so far into the starting position. Continue?'))
      return
    setDraft(collapseToSetup(game, game.moves.length ? 'manual' : game.setup.source))
    setPvSel(null)
    setMode('setup')
  }

  function finishSetup() {
    if (!draft) return
    const dead = deadGroupsInSetup(draft)
    if (dead.length) {
      p.toast(`${dead.length} stone(s) have no liberties. Remove them or add liberties first.`)
      return
    }
    setGame(fromSetup(draft, game.revision + 1))
    setDraft(null)
    setMode('play')
  }

  function onExport() {
    const text = exportSgf(game)
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
            ariaLabel={`Go board, ${colorName(toMove)} to play`}
          />
        </div>

        {mode === 'play' ? (
          <>
            <div class="game-line small">
              <span>
                <span class={`dot ${toMove}`} /> <b>{colorName(toMove)}</b> to play
              </span>
              <span>
                {s.size}×{s.size} · {s.rules} · komi {s.komi}
              </span>
              <span>
                Move {game.moves.length} · captures B {caps.B} / W {caps.W}
              </span>
            </div>
            <div class="toolbar">
              <button class="btn" onClick={() => setGame(undo(game))} disabled={!game.moves.length}>
                Undo
              </button>
              <button class="btn" onClick={() => setGame(redo(game))} disabled={!game.redo.length}>
                Redo
              </button>
              <button class="btn" onClick={() => setGame(pass(game))} data-testid="pass">
                Pass
              </button>
              <button class="btn" onClick={enterSetup} data-testid="edit-position">
                Edit position
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
            {deadInDraft && deadInDraft.size ? (
              <p class="note error small">Highlighted stones have no liberties.</p>
            ) : null}
            <div class="row">
              <button class="btn" onClick={() => setDraft({ ...draft, initialStones: [] })}>
                Clear board
              </button>
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
              starts from this position without any move history.
            </p>
            <button
              class="btn"
              onClick={() => {
                if (confirm('Start a new empty game?')) {
                  setGame({ ...newGame(draft?.size ?? 19, draft?.rules ?? 'chinese', draft?.komi ?? 7.5), revision: game.revision + 1 })
                  setDraft(null)
                  setMode('play')
                }
              }}
            >
              New empty game
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
