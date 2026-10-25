// What the board draws on top of a position: a chosen principal variation, or see-through
// predictions for the moves a locked colour (Keep color / a bonus turn) still has. Pure functions,
// shared by the standard and the bonus-game board screens.

import type GoBoard from '@sabaki/go-board'
import type { AnalysisResult, Candidate } from '../api/client'
import { CAND_LABELS, pct, playerWinrate } from '../analysis/format'
import type { PvSelection } from '../analysis/AnalysisPanel'
import type { Point } from '../game/keepRound'
import type { CandidateMark, PvMark, Sign } from './BoardView'

export interface LineStep {
  x: number | null
  y: number | null
  color: 'B' | 'W'
  pass: boolean
}

export interface OverlayLine {
  steps: LineStep[]
  first: number // number drawn on the first step
  ghost: boolean // predictions: drawn see-through over the position as it is
}

export function signMapOf(b: GoBoard): Sign[][] {
  return b.signMap.map((row) => row.map((v) => (v > 0 ? 1 : v < 0 ? -1 : 0)) as Sign[])
}

/** Predictions for the `movesLeft` moves of `color`: ones kept from before (unless filled since),
 *  else the best line of the latest analysis. */
export function lockPrediction(
  board: GoBoard,
  result: AnalysisResult | null,
  frozen: Point[] | null,
  movesLeft: number,
): Point[] | null {
  const kept = frozen?.filter((q) => board.get([q.x, q.y]) === 0)
  if (kept?.length) return kept
  const top = result?.candidates[0]
  if (!top) return null
  return top.pv
    .slice(0, movesLeft)
    .flatMap((s) => (s.pass || s.x === null || s.y === null ? [] : [{ x: s.x, y: s.y }]))
}

export function overlayLine(
  pvCand: Candidate | null,
  pvSel: PvSelection | null,
  prediction: Point[] | null,
  lockColor: 'B' | 'W' | null,
  firstPrediction: number,
): OverlayLine | null {
  if (pvCand && pvSel) return { steps: pvCand.pv.slice(0, pvSel.step), first: 1, ghost: false }
  if (prediction && lockColor)
    return { steps: prediction.map((q) => ({ ...q, color: lockColor, pass: false })), first: firstPrediction, ghost: true }
  return null
}

/** The stones to draw and the numbered PV marks for `line` played out on `board`. */
export function boardWithLine(board: GoBoard, line: OverlayLine | null): { signMap: Sign[][]; pv: PvMark[] } {
  if (!line) return { signMap: signMapOf(board), pv: [] }
  let b = board
  const placed: PvMark[] = []
  line.steps.forEach((s, i) => {
    if (s.pass || s.x === null || s.y === null) return
    try {
      b = b.makeMove(s.color === 'B' ? 1 : -1, [s.x, s.y])
    } catch {
      return
    }
    placed.push({ x: s.x, y: s.y, color: s.color, n: line.first + i, ghost: line.ghost })
  })
  const sm = signMapOf(b)
  // drop numbers of PV stones that were captured later in the line
  const pv = placed.filter((m) => sm[m.y][m.x] === (m.color === 'B' ? 1 : -1))
  return { signMap: line.ghost ? signMapOf(board) : sm, pv }
}

/** A/B/C marks for the top three candidates, with the mover's win estimate on small boards. */
export function candidateMarks(result: AnalysisResult | null, size: number): CandidateMark[] {
  if (!result || !result.currentPlayer) return []
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
      sub: size <= 13 ? pct(playerWinrate(c.blackWinrate, player), 0) : undefined,
    }))
}
