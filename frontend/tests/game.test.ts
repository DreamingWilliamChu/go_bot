import { describe, expect, it } from 'vitest'
import {
  captures,
  collapseToSetup,
  currentBoard,
  eraseStone,
  fromSetup,
  moveCount,
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

  it('lets one colour play several moves in a row', () => {
    let g = newGame(9)
    for (const x of [2, 3, 4]) {
      const r = play(g, x, 4, 'B')
      if (!r.ok) throw new Error(r.reason)
      g = r.game
    }
    g = pass(g, 'B')
    expect(g.moves.map((m) => m.color)).toEqual(['B', 'B', 'B', 'B'])
    expect(nextPlayer(g)).toBe('W')
    expect(currentBoard(g).get([3, 4])).toBe(1)
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

  it('erases a stone as a history step that undo/redo replay', () => {
    let g = mustPlay(newGame(9), 4, 4) // B
    g = mustPlay(g, 2, 2) // W
    expect(eraseStone(g, 0, 0)).toBeNull()
    const e = eraseStone(g, 4, 4)!
    expect(e.revision).toBe(g.revision + 1)
    expect(e.moves[2]).toEqual({ color: 'B', x: 4, y: 4, erase: true })
    expect(currentBoard(e).get([4, 4])).toBe(0)
    expect(nextPlayer(e)).toBe('B') // erasing does not pass the turn
    expect(moveCount(e)).toBe(2)
    expect(captures(e)).toEqual({ B: 0, W: 0 })
    const u = undo(e)
    expect(u.moves).toEqual(g.moves)
    expect(currentBoard(u).get([4, 4])).toBe(1)
    expect(currentBoard(redo(u)).get([4, 4])).toBe(0)
    // the point is free again
    expect(play(e, 4, 4).ok).toBe(true)
  })

  it('folds history up to the last erase into the API start position', () => {
    let g = mustPlay(newGame(9), 4, 4) // B
    g = mustPlay(g, 2, 2) // W
    g = eraseStone(g, 2, 2)!
    g = mustPlay(g, 6, 6) // B
    const api = toApiPosition(g)
    expect(api.initial_stones).toEqual([{ color: 'B', x: 4, y: 4 }])
    expect(api.initial_player).toBe('B')
    expect(api.moves).toEqual([{ color: 'B', x: 6, y: 6 }])
    // no erase: unchanged payload
    expect(toApiPosition(undo(undo(g))).moves.length).toBe(2)
  })

  it('validates komi', () => {
    expect(validKomi(7.5)).toBe(true)
    expect(validKomi(0)).toBe(true)
    expect(validKomi(7.25)).toBe(false)
    expect(validKomi(NaN)).toBe(false)
  })
})
