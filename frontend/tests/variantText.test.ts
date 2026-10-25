import { describe, expect, it } from 'vitest'
import { announcementText, bonusesLeftText, chanceText, lockText, resultText, turnText } from '../src/variant/text'
import type { VariantState, VariantView } from '../src/variant/types'

function view(s: Partial<VariantState>): VariantView {
  const state: VariantState = {
    config: { size: 11, komi: 7.5, rules: 'chinese', black_bonuses: 2, white_bonuses: 3, moves_per_bonus: 3, trigger: 'random', horizon: 30, max_placements: null },
    size: 11,
    grid: new Array(121).fill(0),
    toMove: 'B',
    phase: 'in_turn',
    movesLeft: 1,
    turnMoves: 1,
    bonusActive: false,
    placedThisTurn: 0,
    remaining: { B: 2, W: 3 },
    turnsStarted: { B: 3, W: 2 },
    eligibleTurnsLeft: { B: 27, W: 28 },
    nextTurnBonusProbability: { B: 2 / 27, W: 3 / 28 },
    passStreak: 0,
    placements: 4,
    captures: { B: 0, W: 0 },
    lastPlacement: null,
    historyKnown: true,
    legal: [],
    canUseBonus: false,
    result: null,
    ...s,
  }
  return { gameId: 'g', revision: 1, provenance: 'new', commitment: 'c', state, events: [], newEvents: [], actions: [], canUndo: false, canRedo: false, reveal: null }
}

describe('bonus status text', () => {
  it('separates bonus opportunities from moves left in the turn', () => {
    const v = view({ bonusActive: true, movesLeft: 2, turnMoves: 3, remaining: { B: 1, W: 3 } })
    expect(bonusesLeftText(v)).toBe('Bonuses left — Black: 1 · White: 3')
    expect(turnText(v)).toBe('Black bonus turn — 2 moves remaining')
    expect(turnText(view({ bonusActive: true, movesLeft: 1, turnMoves: 3 }))).toBe('Black bonus turn — 1 move remaining')
    expect(turnText(view({ toMove: 'W' }))).toBe('White to play — normal turn (1 move)')
  })

  it('names the lock source: only a bonus turn locks in Bonus Mode', () => {
    expect(lockText(view({ bonusActive: true, movesLeft: 2, turnMoves: 3 }))).toMatchObject({ on: true, label: 'Keep Black · bonus 2/3' })
    expect(lockText(view({}))).toMatchObject({ on: false, label: 'Keep · bonus only' })
  })

  it('announces a bonus that has just triggered, before its first move only', () => {
    const triggered = { type: 'turn_start' as const, player: 'B' as const, turnIndex: 4, bonus: true, moves: 3, probability: 0.1 }
    const fresh = { ...view({ bonusActive: true, movesLeft: 3, turnMoves: 3 }), events: [triggered] }
    expect(announcementText(fresh)).toBe('Bonus! Black plays 3 moves in a row this turn.')
    expect(announcementText({ ...fresh, state: { ...fresh.state, movesLeft: 2 } })).toBeNull()
    // an imported position already in a bonus turn has no trigger to announce
    expect(announcementText(view({ bonusActive: true, movesLeft: 3, turnMoves: 3 }))).toBeNull()
    expect(announcementText(view({}))).toBeNull()
  })

  it('shows the public next-turn chances', () => {
    expect(chanceText(view({}))).toBe('Next-turn bonus chance — Black 2/27 (7%) · White 3/28 (11%)')
    expect(chanceText(view({ eligibleTurnsLeft: { B: 0, W: 2 }, remaining: { B: 0, W: 2 }, nextTurnBonusProbability: { B: 0, W: 1 } }))).toBe(
      'Next-turn bonus chance — Black none · White 2/2 (100%)',
    )
  })

  it('describes results without double counting and flags truncation', () => {
    const score = { black_stones: 40, white_stones: 38, black_area: 62, white_area: 59, komi: 7.5, black_total: 62, white_total: 66.5, lead: -4.5, owner: [], winner: 'W' as const }
    expect(resultText({ reason: 'passes', truncated: false, score })).toBe('White wins by 4.5, scored as the board stands: Black 62 · White 59 + 7.5 komi.')
    expect(resultText({ reason: 'truncated', truncated: true, score })).toMatch(/no result/)
  })
})

describe('bonuses chosen by the player', () => {
  const choice = (s: Partial<VariantState>) => {
    const v = view(s)
    return { ...v, state: { ...v.state, config: { ...v.state.config, trigger: 'choice' as const }, ...s } }
  }

  it('turns Keep color into the Use bonus control before the first move', () => {
    const lock = lockText(choice({ canUseBonus: true, remaining: { B: 2, W: 0 } }))
    expect(lock).toMatchObject({ canUse: true, on: false, label: 'Use bonus · 2 left' })
    expect(chanceText(choice({ canUseBonus: true }))).toMatch(/Black may use a bonus now/)
  })

  it('cannot be used without a bonus left, and locks during the bonus turn', () => {
    expect(lockText(choice({ canUseBonus: false, remaining: { B: 0, W: 1 } }))).toMatchObject({
      canUse: false,
      label: 'Keep · Black has no bonus',
    })
    const active = lockText(choice({ bonusActive: true, movesLeft: 2, turnMoves: 3 }))
    expect(active).toMatchObject({ canUse: false, on: true, label: 'Keep Black · bonus 2/3' })
  })

  it('announces a bonus the player just chose', () => {
    const v = choice({ bonusActive: true, movesLeft: 3, turnMoves: 3 })
    const used = { ...v, events: [{ type: 'bonus_used' as const, player: 'B' as const, turnIndex: 4, moves: 3 }] }
    expect(announcementText(used)).toBe('Bonus! Black plays 3 moves in a row this turn.')
  })
})
