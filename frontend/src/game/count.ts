// A quick count of a position without the engine: stones plus empty regions that only one colour
// touches ("settled" points). Dead stones and open areas are not judged, so mid-game most empty
// points count for nobody. Captures before the position are unknown and count as 0.

import type { Rules, Stone } from './game'

export type CountMethod = 'area' | 'territory' | 'stones'

export interface SideCount {
  stones: number
  area: number // settled empty points
  total: number // what counts for this side under the method (before komi)
}

export interface SettledCount {
  method: CountMethod
  black: SideCount
  white: SideCount
  komi: number
  lead: number // Black total − (White total + komi); positive = Black ahead
  // Row-major from top-left: +1 Black, -1 White, 0 nobody. Stones carry their own colour; empty
  // points are marked only when the method counts them (not under stone scoring).
  owner: number[]
}

export function countMethod(rules: Rules): CountMethod {
  if (rules === 'japanese' || rules === 'korean') return 'territory'
  if (rules === 'stone-scoring') return 'stones'
  return 'area'
}

export function countSettled(stones: Stone[], size: number, rules: Rules, komi: number): SettledCount {
  const method = countMethod(rules)
  const grid = new Array<number>(size * size).fill(0)
  for (const s of stones) grid[s.y * size + s.x] = s.color === 'B' ? 1 : -1

  // Each empty region belongs to a colour only if every stone bordering it is that colour.
  const region = new Array<number>(size * size).fill(0)
  const seen = new Array<boolean>(size * size).fill(false)
  for (let start = 0; start < size * size; start++) {
    if (grid[start] !== 0 || seen[start]) continue
    const points = [start]
    seen[start] = true
    let touchesB = false
    let touchesW = false
    for (let i = 0; i < points.length; i++) {
      const p = points[i]
      const x = p % size
      const y = (p - x) / size
      const nbrs = []
      if (x > 0) nbrs.push(p - 1)
      if (x < size - 1) nbrs.push(p + 1)
      if (y > 0) nbrs.push(p - size)
      if (y < size - 1) nbrs.push(p + size)
      for (const q of nbrs) {
        if (grid[q] === 1) touchesB = true
        else if (grid[q] === -1) touchesW = true
        else if (!seen[q]) {
          seen[q] = true
          points.push(q)
        }
      }
    }
    const v = touchesB && !touchesW ? 1 : touchesW && !touchesB ? -1 : 0
    for (const p of points) region[p] = v
  }

  const side = (v: 1 | -1): SideCount => {
    const st = grid.filter((g) => g === v).length
    const area = region.filter((r) => r === v).length
    const total = method === 'area' ? st + area : method === 'territory' ? area : st
    return { stones: st, area, total }
  }
  const black = side(1)
  const white = side(-1)
  const owner = grid.map((g, i) => (g !== 0 ? g : method === 'stones' ? 0 : region[i]))
  return { method, black, white, komi, lead: black.total - (white.total + komi), owner }
}
