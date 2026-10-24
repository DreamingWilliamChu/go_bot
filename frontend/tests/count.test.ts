import { describe, expect, it } from 'vitest'
import { countMethod, countSettled } from '../src/game/count'
import type { Stone } from '../src/game/game'

// 5x5: a black wall on column 1 and a white wall on column 3.
// Column 0 (5 points) is Black's, column 4 (5 points) is White's, column 2 touches both.
function walls(): Stone[] {
  const out: Stone[] = []
  for (let y = 0; y < 5; y++) {
    out.push({ color: 'B', x: 1, y })
    out.push({ color: 'W', x: 3, y })
  }
  return out
}

describe('settled count', () => {
  it('picks the method from the rules', () => {
    expect(countMethod('chinese')).toBe('area')
    expect(countMethod('aga')).toBe('area')
    expect(countMethod('new-zealand')).toBe('area')
    expect(countMethod('tromp-taylor')).toBe('area')
    expect(countMethod('japanese')).toBe('territory')
    expect(countMethod('korean')).toBe('territory')
    expect(countMethod('stone-scoring')).toBe('stones')
  })

  it('counts nothing on an empty board', () => {
    const r = countSettled([], 9, 'chinese', 0)
    expect(r.black).toEqual({ stones: 0, area: 0, total: 0 })
    expect(r.white).toEqual({ stones: 0, area: 0, total: 0 })
    expect(r.lead).toBe(0)
    expect(r.owner.every((v) => v === 0)).toBe(true)
  })

  it('area scoring adds stones and settled points', () => {
    const r = countSettled(walls(), 5, 'chinese', 0)
    expect(r.method).toBe('area')
    expect(r.black).toEqual({ stones: 5, area: 5, total: 10 })
    expect(r.white).toEqual({ stones: 5, area: 5, total: 10 })
  })

  it('territory scoring counts settled points only', () => {
    const stones = [...walls(), { color: 'B' as const, x: 2, y: 2 }]
    const r = countSettled(stones, 5, 'japanese', 0)
    expect(r.method).toBe('territory')
    expect(r.black.total).toBe(5)
    expect(r.white.total).toBe(5)
    expect(r.black.stones).toBe(6)
  })

  it('stone scoring counts stones only and marks no empty points', () => {
    const stones = [...walls(), { color: 'B' as const, x: 2, y: 2 }]
    const r = countSettled(stones, 5, 'stone-scoring', 0)
    expect(r.black.total).toBe(6)
    expect(r.white.total).toBe(5)
    expect(r.owner[0]).toBe(0) // (0,0) is empty
    expect(r.owner[1]).toBe(1) // (1,0) is a black stone
  })

  it('gives komi to White', () => {
    const r = countSettled(walls(), 5, 'chinese', 7.5)
    expect(r.komi).toBe(7.5)
    expect(r.lead).toBe(-7.5)
  })

  it('leaves a region bordered by both colours neutral', () => {
    const r = countSettled(walls(), 5, 'chinese', 0)
    for (let y = 0; y < 5; y++) {
      expect(r.owner[y * 5 + 0]).toBe(1)
      expect(r.owner[y * 5 + 2]).toBe(0)
      expect(r.owner[y * 5 + 4]).toBe(-1)
    }
  })

  it('gives a region to the only colour that touches it, however large', () => {
    const r = countSettled([{ color: 'B', x: 4, y: 4 }], 9, 'chinese', 7.5)
    expect(r.black.total).toBe(81)
    expect(r.lead).toBe(73.5)
  })
})
