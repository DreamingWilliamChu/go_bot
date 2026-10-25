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

  it('starts a fresh round after the last move of a round (the lock has no move limit)', () => {
    expect(advanceRound({ done: 2, frozen: [shown[2]] }, [shown[2]], { x: 3, y: 15 }, 3)).toEqual(FRESH_ROUND)
    expect(advanceRound(FRESH_ROUND, shown, null, 1)).toEqual(FRESH_ROUND)
    // many moves in a row: never released, rounds keep cycling
    let r = FRESH_ROUND
    for (let i = 0; i < 20; i++) r = advanceRound(r, [], { x: i % 9, y: 0 }, 3)
    expect(r).toEqual({ done: 20 % 3, frozen: null })
  })
})
