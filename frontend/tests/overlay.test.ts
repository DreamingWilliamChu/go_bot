import GoBoard from '@sabaki/go-board'
import { describe, expect, it } from 'vitest'
import { boardWithLine, lockPrediction, overlayLine } from '../src/board/overlay'
import type { AnalysisResult } from '../src/api/client'

const result = {
  candidates: [
    {
      gtp: 'C3', x: 2, y: 6, pass: false, order: 0, visits: 10, blackWinrate: 0.6, blackScoreLead: 1, prior: 0.2, lcb: null,
      pv: [
        { gtp: 'C3', x: 2, y: 6, pass: false, color: 'B' },
        { gtp: 'D3', x: 3, y: 6, pass: false, color: 'B' },
        { gtp: 'E3', x: 4, y: 6, pass: false, color: 'B' },
      ],
    },
  ],
} as unknown as AnalysisResult

describe('board overlay', () => {
  it("predicts the locked colour's remaining moves and keeps unfilled frozen ones", () => {
    const b = GoBoard.fromDimensions(9)
    expect(lockPrediction(b, result, null, 2)).toEqual([{ x: 2, y: 6 }, { x: 3, y: 6 }])
    const filled = b.set([1, 1], 1)
    expect(lockPrediction(filled, result, [{ x: 1, y: 1 }, { x: 5, y: 5 }], 2)).toEqual([{ x: 5, y: 5 }])
  })

  it('stops predictions at a pass or at the opponent (the turn ended early)', () => {
    const b = GoBoard.fromDimensions(9)
    const early = {
      candidates: [
        {
          ...result.candidates[0],
          pv: [
            { gtp: 'C3', x: 2, y: 6, pass: false, color: 'B' },
            { gtp: 'pass', x: null, y: null, pass: true, color: 'B' },
            { gtp: 'E3', x: 4, y: 6, pass: false, color: 'W' },
          ],
        },
      ],
    } as unknown as AnalysisResult
    expect(lockPrediction(b, early, null, 3, 'B')).toEqual([{ x: 2, y: 6 }])
    const switched = { candidates: [{ ...result.candidates[0], pv: [{ ...result.candidates[0].pv[0], color: 'W' }] }] } as unknown as AnalysisResult
    expect(lockPrediction(b, switched, null, 3, 'B')).toBeNull()
  })

  it('draws predictions see-through and numbered from their place in the turn', () => {
    const b = GoBoard.fromDimensions(9)
    const line = overlayLine(null, null, [{ x: 2, y: 6 }, { x: 3, y: 6 }], 'B', 2)
    const drawn = boardWithLine(b, line)
    expect(drawn.pv.map((m) => m.n)).toEqual([2, 3])
    expect(drawn.pv.every((m) => m.ghost)).toBe(true)
    expect(drawn.signMap[6][2]).toBe(0) // the position itself is unchanged
  })
})
