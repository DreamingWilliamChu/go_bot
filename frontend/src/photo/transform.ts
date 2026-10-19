// Board orientation: the recognised board is kept in photo orientation; the user may rotate or
// mirror the digital board. Display (x, y) = rotate^rot(flip(photo (x, y))), rotation clockwise.

export interface Orient {
  rot: 0 | 1 | 2 | 3
  flip: boolean
}

export const IDENTITY: Orient = { rot: 0, flip: false }

export function toDisplay([x, y]: [number, number], n: number, o: Orient): [number, number] {
  let p: [number, number] = o.flip ? [n - 1 - x, y] : [x, y]
  for (let i = 0; i < o.rot; i++) p = [n - 1 - p[1], p[0]]
  return p
}

export function toPhoto([x, y]: [number, number], n: number, o: Orient): [number, number] {
  let p: [number, number] = [x, y]
  for (let i = 0; i < o.rot; i++) p = [p[1], n - 1 - p[0]]
  return o.flip ? [n - 1 - p[0], p[1]] : p
}

/** board[y][x] in photo orientation -> board in display orientation. */
export function boardToDisplay<T>(board: T[][], n: number, o: Orient): T[][] {
  const out: T[][] = Array.from({ length: n }, () => new Array<T>(n))
  for (let y = 0; y < n; y++)
    for (let x = 0; x < n; x++) {
      const [dx, dy] = toDisplay([x, y], n, o)
      out[dy][dx] = board[y][x]
    }
  return out
}

export function rotateCW(o: Orient): Orient {
  return { ...o, rot: ((o.rot + 1) % 4) as Orient['rot'] }
}

export function mirror(o: Orient): Orient {
  // mirroring the displayed board horizontally
  const rot = ((4 - o.rot) % 4) as Orient['rot']
  return { rot, flip: !o.flip }
}
