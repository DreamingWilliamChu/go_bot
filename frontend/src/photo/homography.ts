// 4-point homography (grid coordinates -> image pixels) for drawing grid overlays on photos.

export type Pt = [number, number]

function solve(A: number[][], b: number[]): number[] {
  const n = b.length
  const M = A.map((row, i) => [...row, b[i]])
  for (let c = 0; c < n; c++) {
    let p = c
    for (let r = c + 1; r < n; r++) if (Math.abs(M[r][c]) > Math.abs(M[p][c])) p = r
    if (Math.abs(M[p][c]) < 1e-12) throw new Error('degenerate homography')
    ;[M[c], M[p]] = [M[p], M[c]]
    for (let r = 0; r < n; r++) {
      if (r === c) continue
      const f = M[r][c] / M[c][c]
      for (let k = c; k <= n; k++) M[r][k] -= f * M[c][k]
    }
  }
  return M.map((row, i) => row[n] / row[i])
}

/** Homography H (3x3, row-major) with H * [x, y, 1] ~ dst for the 4 correspondences. */
export function homography(src: Pt[], dst: Pt[]): number[] {
  const A: number[][] = []
  const b: number[] = []
  for (let i = 0; i < 4; i++) {
    const [x, y] = src[i]
    const [u, v] = dst[i]
    A.push([x, y, 1, 0, 0, 0, -u * x, -u * y])
    b.push(u)
    A.push([0, 0, 0, x, y, 1, -v * x, -v * y])
    b.push(v)
  }
  return [...solve(A, b), 1]
}

export function apply(H: number[], [x, y]: Pt): Pt {
  const w = H[6] * x + H[7] * y + H[8]
  return [(H[0] * x + H[1] * y + H[2]) / w, (H[3] * x + H[4] * y + H[5]) / w]
}

/** Grid (0..n-1) -> image, from corners [TL, TR, BR, BL]. */
export function gridToImage(corners: Pt[], n: number): number[] {
  const m = n - 1
  return homography(
    [
      [0, 0],
      [m, 0],
      [m, m],
      [0, m],
    ],
    corners,
  )
}

export function isConvex(q: Pt[]): boolean {
  let sign = 0
  for (let i = 0; i < 4; i++) {
    const [a, b, c] = [q[i], q[(i + 1) % 4], q[(i + 2) % 4]]
    const cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
    if (Math.abs(cross) < 1e-9) return false
    const s = Math.sign(cross)
    if (sign === 0) sign = s
    else if (s !== sign) return false
  }
  return true
}

export function defaultCorners(w: number, h: number): Pt[] {
  const s = Math.min(w, h) * 0.32
  const cx = w / 2
  const cy = h / 2
  return [
    [cx - s, cy - s],
    [cx + s, cy - s],
    [cx + s, cy + s],
    [cx - s, cy + s],
  ]
}
