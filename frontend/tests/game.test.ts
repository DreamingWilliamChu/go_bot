import { describe, expect, it } from 'vitest'
import {
  captures,
  collapseToSetup,
  currentBoard,
  fromSetup,
  newGame,
  nextPlayer,
  pass,
  play,
  redo,
  setPoint,
  toApiPosition,
  undo,
  validKomi,
  deadGroupsInSetup,
  type GameState,
} from '../src/game/game'

function mustPlay(g: GameState, x: number, y: number): GameState {
  const r = play(g, x, y)
  if (!r.ok) throw new Error(r.reason)
  return r.game
}

describe('game rules', () => {
  it('alternates players and supports pass', () => {
    let g = newGame(9)
    expect(nextPlayer(g)).toBe('B')
    g = mustPlay(g, 4, 4)
    expect(nextPlayer(g)).toBe('W')
    g = pass(g)
    expect(nextPlayer(g)).toBe('B')
    expect(g.moves[1]).toEqual({ color: 'W', x: null, y: null })
  })

  it('captures a stone and counts prisoners', () => {
    let g = newGame(9)
    // Black surrounds white at (1,1)
    for (const [b, w] of [
      [[1, 0], [1, 1]],
      [[0, 1], [8, 8]],
      [[2, 1], [8, 7]],
    ] as [number, number][][]) {
      g = mustPlay(g, b[0], b[1])
      g = mustPlay(g, w[0], w[1])
    }
    g = mustPlay(g, 1, 2)
    expect(currentBoard(g).get([1, 1])).toBe(0)
    expect(captures(g)).toEqual({ B: 1, W: 0 })
  })

  it('rejects occupied points and suicide', () => {
    let g = newGame(9)
    g = mustPlay(g, 0, 1) // B
    g = mustPlay(g, 5, 5) // W
    g = mustPlay(g, 1, 0) // B
    const occ = play(g, 0, 1)
    expect(occ.ok).toBe(false)
    const suicide = play(g, 0, 0) // W into a point with no liberties
    expect(suicide.ok).toBe(false)
    if (!suicide.ok) expect(suicide.reason).toMatch(/Suicide/)
  })

  it('forbids immediate ko recapture but allows it after a ko threat/pass', () => {
    // Classic ko shape on 9x9 set up as an initial position.
    const setup = {
      size: 9 as const,
      initialStones: [
        { color: 'B' as const, x: 1, y: 0 },
        { color: 'B' as const, x: 0, y: 1 },
        { color: 'B' as const, x: 1, y: 2 },
        { color: 'W' as const, x: 2, y: 0 },
        { color: 'W' as const, x: 3, y: 1 },
        { color: 'W' as const, x: 2, y: 2 },
        { color: 'W' as const, x: 1, y: 1 },
      ],
      initialPlayer: 'B' as const,
      rules: 'chinese' as const,
      komi: 7.5,
      source: 'manual' as const,
    }
    let g = fromSetup(setup)
    g = mustPlay(g, 2, 1) // B captures W(1,1)
    expect(currentBoard(g).get([1, 1])).toBe(0)
    const retake = play(g, 1, 1) // W immediate recapture: ko
    expect(retake.ok).toBe(false)
    if (!retake.ok) expect(retake.reason).toMatch(/Ko/)
    g = pass(g) // W passes (ko threat elsewhere)
    g = pass(g) // B passes
    expect(play(g, 1, 1).ok).toBe(true)
  })

  it('undo/redo restores state and bumps revision', () => {
    let g = newGame(9)
    g = mustPlay(g, 2, 2)
    g = mustPlay(g, 3, 3)
    const r0 = g.revision
    g = undo(g)
    expect(g.moves.length).toBe(1)
    expect(g.revision).toBe(r0 + 1)
    g = redo(g)
    expect(g.moves.length).toBe(2)
    expect(nextPlayer(g)).toBe('B')
    g = undo(g)
    g = mustPlay(g, 6, 6)
    expect(g.redo.length).toBe(0)
  })

  it('setup edits, initial player and API payload', () => {
    let s = newGame(13).setup
    s = setPoint(s, 3, 3, 'B')
    s = setPoint(s, 9, 9, 'W')
    s = setPoint(s, 3, 3, 'W')
    s = setPoint(s, 9, 9, null)
    s = { ...s, initialPlayer: 'W', komi: 6.5, rules: 'japanese' }
    const g = fromSetup(s)
    expect(nextPlayer(g)).toBe('W')
    expect(toApiPosition(g)).toEqual({
      size: 13,
      initial_stones: [{ color: 'W', x: 3, y: 3 }],
      initial_player: 'W',
      moves: [],
      rules: 'japanese',
      komi: 6.5,
    })
  })

  it('detects groups without liberties in a setup', () => {
    let s = newGame(9).setup
    s = setPoint(s, 0, 0, 'W')
    s = setPoint(s, 1, 0, 'B')
    s = setPoint(s, 0, 1, 'B')
    expect(deadGroupsInSetup(s)).toEqual([{ color: 'W', x: 0, y: 0 }])
  })

  it('collapses moves into a new setup', () => {
    let g = newGame(9)
    g = mustPlay(g, 4, 4)
    g = mustPlay(g, 3, 3)
    const s = collapseToSetup(g)
    expect(s.initialStones.length).toBe(2)
    expect(s.initialPlayer).toBe('B')
  })

  it('validates komi', () => {
    expect(validKomi(7.5)).toBe(true)
    expect(validKomi(0)).toBe(true)
    expect(validKomi(7.25)).toBe(false)
    expect(validKomi(NaN)).toBe(false)
  })
})
