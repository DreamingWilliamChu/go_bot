import { describe, expect, it } from 'vitest'
import { FRESH_ROUND, advanceRound } from '../src/game/keepRound'

const shown = [
  { x: 3, y: 3 },
  { x: 15, y: 3 },
  { x: 3, y: 15 },
]

describe('keep color rounds', () => {
  it('keeps the other predictions when one of them is played', () => {
    const r = advanceRound(FRESH_ROUND, shown, { x: 3, y: 3 }, 3)
    expect(r).toEqual({ done: 1, frozen: [shown[1], shown[2]] })
    // playing a later prediction first keeps the earlier one
    expect(advanceRound(r!, r!.frozen!, { x: 3, y: 15 }, 3)).toEqual({ done: 2, frozen: [shown[1]] })
  })

  it('asks again for the rest of the round after any other move or a pass', () => {
    expect(advanceRound(FRESH_ROUND, shown, { x: 9, y: 9 }, 3)).toEqual({ done: 1, frozen: null })
    expect(advanceRound(FRESH_ROUND, shown, null, 3)).toEqual({ done: 1, frozen: null })
  })

  it('is used up after the last move of a round', () => {
    expect(advanceRound({ done: 2, frozen: [shown[2]] }, [shown[2]], { x: 3, y: 15 }, 3)).toBeNull()
    expect(advanceRound(FRESH_ROUND, shown, null, 1)).toBeNull()
  })
})
