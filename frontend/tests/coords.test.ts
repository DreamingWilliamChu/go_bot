import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { gtpToXy, ownershipIndex, sgfToXy, starPoints, xyToGtp, xyToSgf } from '../src/game/coords'

const fixture = JSON.parse(
  readFileSync(fileURLToPath(new URL('../../tests/fixtures/coords.json', import.meta.url)), 'utf8'),
)

describe('coordinates (shared fixture with Python)', () => {
  it('round-trips every fixture point', () => {
    for (const p of fixture.points) {
      expect(xyToGtp(p.x, p.y, p.size)).toBe(p.gtp)
      expect(gtpToXy(p.gtp, p.size)).toEqual([p.x, p.y])
      expect(xyToSgf(p.x, p.y, p.size)).toBe(p.sgf)
      expect(sgfToXy(p.sgf, p.size)).toEqual([p.x, p.y])
      expect(ownershipIndex(p.x, p.y, p.size)).toBe(p.ownershipIndex)
    }
  })
  it('parses passes', () => {
    for (const p of fixture.passes) {
      expect(gtpToXy(p.gtp, p.size)).toBeNull()
      expect(sgfToXy(p.sgf, p.size)).toBeNull()
    }
  })
  it('rejects invalid coordinates', () => {
    for (const p of fixture.invalid) {
      if (p.gtp) expect(() => gtpToXy(p.gtp, p.size)).toThrow()
      else expect(() => sgfToXy(p.sgf, p.size)).toThrow()
    }
  })
  it('covers every point of 9/11/13/19 boards uniquely', () => {
    for (const n of [9, 11, 13, 19]) {
      const seen = new Set<string>()
      for (let y = 0; y < n; y++)
        for (let x = 0; x < n; x++) {
          const g = xyToGtp(x, y, n)
          expect(g.startsWith('I')).toBe(false)
          expect(gtpToXy(g, n)).toEqual([x, y])
          expect(sgfToXy(xyToSgf(x, y, n), n)).toEqual([x, y])
          seen.add(g)
        }
      expect(seen.size).toBe(n * n)
    }
  })
})

describe('star points', () => {
  it('puts the 11x11 corner dots on the 3-3 points', () => {
    expect(starPoints(11).map(([x, y]) => xyToGtp(x, y, 11))).toEqual(['C9', 'J9', 'C3', 'J3', 'F6'])
  })
})
