import { describe, expect, it } from 'vitest'
import { currentBoard, eraseStone, fromSetup, newGame, nextPlayer, pass, play, type GameState } from '../src/game/game'
import { exportSgf, importSgf } from '../src/game/sgf'

function mustPlay(g: GameState, x: number, y: number): GameState {
  const r = play(g, x, y)
  if (!r.ok) throw new Error(r.reason)
  return r.game
}
const D = { rules: 'chinese' as const, komi: 7.5 }

describe('SGF', () => {
  it('round-trips a photo position with moves and passes', () => {
    let g = fromSetup({
      size: 19,
      initialStones: [
        { color: 'B', x: 15, y: 3 },
        { color: 'W', x: 3, y: 15 },
        { color: 'B', x: 3, y: 3 },
      ],
      initialPlayer: 'W',
      rules: 'japanese',
      komi: 6.5,
      source: 'photo',
    })
    g = mustPlay(g, 16, 16)
    g = pass(g)
    g = mustPlay(g, 2, 2)
    const text = exportSgf(g)
    expect(text).toContain('SZ[19]')
    expect(text).toContain('KM[6.5]')
    expect(text).toContain('RU[Japanese]')
    expect(text).toContain('PL[W]')
    expect(text).toMatch(/AB\[(pd|dd)\]\[(dd|pd)\]/)
    expect(text).toContain('AW[dp]')
    expect(text).toContain(';W[qq]')
    expect(text).toContain(';B[]')
    expect(text).toContain('photo')
    const { game: back, warnings } = importSgf(text, D)
    expect(warnings).toEqual([])
    expect(back.setup.size).toBe(19)
    expect(back.setup.komi).toBe(6.5)
    expect(back.setup.rules).toBe('japanese')
    expect(back.setup.initialPlayer).toBe('W')
    expect(new Set(back.setup.initialStones.map((s) => `${s.color}${s.x},${s.y}`))).toEqual(
      new Set(g.setup.initialStones.map((s) => `${s.color}${s.x},${s.y}`)),
    )
    expect(back.moves).toEqual(g.moves)
    expect(exportSgf(back)).toBe(text)
    expect(back.setup.source).toBe('photo')
  })

  it('imports a normal game record with captures', () => {
    const sgf = '(;GM[1]FF[4]SZ[9]KM[7]RU[Chinese];B[ba];W[bb];B[ab];W[ee];B[cb];W[ff];B[bc])'
    const { game } = importSgf(sgf, D)
    expect(game.moves.length).toBe(7)
    expect(game.setup.komi).toBe(7)
    expect(nextPlayer(game)).toBe('W')
  })

  it('handles handicap setup and consecutive moves', () => {
    const sgf = '(;GM[1]SZ[19]HA[2]AB[dd][pp]KM[0.5];W[dp];W[pd];B[qq])'
    const { game, warnings } = importSgf(sgf, D)
    expect(warnings.some((w) => w.includes('repeats'))).toBe(true)
    expect(game.setup.initialStones.length).toBe(3)
    expect(game.moves.length).toBe(2)
  })

  it('handicap without moves gives White the move', () => {
    const { game } = importSgf('(;GM[1]SZ[13]HA[2]AB[dd][jj])', D)
    expect(game.setup.initialPlayer).toBe('W')
  })

  it('expands compressed point lists and rejects bad files', () => {
    const { game } = importSgf('(;SZ[9]AB[aa:bb])', D)
    expect(game.setup.initialStones.length).toBe(4)
    expect(() => importSgf('(;SZ[15])', D)).toThrow(/use 9, 11, 13 or 19/)
    expect(() => importSgf('(;GM[2]SZ[9])', D)).toThrow(/not a Go game/)
    expect(() => importSgf('(;SZ[9];B[aa];W[aa])', D)).toThrow(/Illegal move 2/)
  })

  it('folds setup after moves into the start position', () => {
    const { game, warnings } = importSgf('(;SZ[9];B[ee];W[cc];AB[gg]PL[W])', D)
    expect(warnings.length).toBe(1)
    expect(game.moves.length).toBe(0)
    expect(game.setup.initialStones.length).toBe(3)
  })

  it('round-trips erased stones as AE nodes, keeping the moves', () => {
    let g = mustPlay(newGame(9), 4, 4)
    g = mustPlay(g, 2, 2)
    g = eraseStone(g, 4, 4)!
    g = mustPlay(g, 6, 6)
    const t = exportSgf(g)
    expect(t).toContain(';AE[ee]')
    const { game, warnings } = importSgf(t, D)
    expect(warnings).toEqual([])
    expect(game.moves).toEqual(g.moves)
    expect(currentBoard(game).signMap).toEqual(currentBoard(g).signMap)
  })

  it('unknown rules and bad komi fall back with warnings', () => {
    const { game, warnings } = importSgf('(;SZ[9]RU[Weird]KM[7.3])', D)
    expect(game.setup.rules).toBe('chinese')
    expect(game.setup.komi).toBe(7.5)
    expect(warnings.length).toBe(2)
  })

  it('imports 11x11 games', () => {
    const { game } = importSgf('(;GM[1]SZ[11];B[ff];W[dd];B[kk])', D)
    expect(game.setup.size).toBe(11)
    expect(game.moves.length).toBe(3)
    expect(importSgf(exportSgf(game), D).game.moves.length).toBe(3)
  })

  it('empty game exports minimal SGF', () => {
    const t = exportSgf(newGame(9))
    expect(importSgf(t, D).game.setup.size).toBe(9)
  })
})
