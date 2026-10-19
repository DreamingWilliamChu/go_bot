import { describe, expect, it } from 'vitest'
import { apply, gridToImage, homography, isConvex, type Pt } from '../src/photo/homography'
import { boardToDisplay, mirror, rotateCW, toDisplay, toPhoto, IDENTITY, type Orient } from '../src/photo/transform'

describe('homography', () => {
  it('maps the four correspondences exactly', () => {
    const src: Pt[] = [[0, 0], [1, 0], [1, 1], [0, 1]]
    const dst: Pt[] = [[100, 120], [480, 90], [520, 470], [80, 430]]
    const H = homography(src, dst)
    src.forEach((p, i) => {
      const q = apply(H, p)
      expect(q[0]).toBeCloseTo(dst[i][0], 6)
      expect(q[1]).toBeCloseTo(dst[i][1], 6)
    })
  })
  it('grid corners land on image corners and lines stay straight', () => {
    const c: Pt[] = [[10, 10], [300, 20], [310, 320], [5, 300]]
    const H = gridToImage(c, 19)
    expect(apply(H, [18, 18])[0]).toBeCloseTo(310, 6)
    const a = apply(H, [0, 5]), b = apply(H, [9, 5]), d = apply(H, [18, 5])
    const cross = (b[0] - a[0]) * (d[1] - a[1]) - (b[1] - a[1]) * (d[0] - a[0])
    expect(Math.abs(cross)).toBeLessThan(1e-6)
  })
  it('detects convexity', () => {
    expect(isConvex([[0, 0], [10, 0], [10, 10], [0, 10]])).toBe(true)
    expect(isConvex([[0, 0], [10, 10], [10, 0], [0, 10]])).toBe(false)
  })
})

describe('orientation transforms', () => {
  const orients: Orient[] = []
  for (const flip of [false, true]) for (const rot of [0, 1, 2, 3] as const) orients.push({ rot, flip })
  it('toPhoto inverts toDisplay for all 8 orientations', () => {
    for (const o of orients)
      for (const n of [9, 13, 19])
        for (let y = 0; y < n; y += 3)
          for (let x = 0; x < n; x += 2) expect(toPhoto(toDisplay([x, y], n, o), n, o)).toEqual([x, y])
  })
  it('rotates clockwise: top-left goes to top-right', () => {
    expect(toDisplay([0, 0], 19, { rot: 1, flip: false })).toEqual([18, 0])
    expect(toDisplay([18, 0], 19, { rot: 1, flip: false })).toEqual([18, 18])
  })
  it('board transform keeps stone count and mirror is an involution', () => {
    const n = 9
    const b = Array.from({ length: n }, (_, y) => Array.from({ length: n }, (_, x) => (x === 1 && y === 2 ? 1 : 0)))
    let o = rotateCW(IDENTITY)
    const d = boardToDisplay(b, n, o)
    expect(d[1][6]).toBe(1) // (1,2) rotated cw on 9x9 -> (6,1)
    o = mirror(mirror(o))
    expect(o).toEqual({ rot: 1, flip: false })
    const m = mirror(IDENTITY)
    expect(toDisplay([0, 0], n, m)).toEqual([8, 0])
  })
})
