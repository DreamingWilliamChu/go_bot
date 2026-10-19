// Board coordinates. Canonical form is (x, y): x = column from the left, y = row from the TOP,
// both 0-based. This matches SGF ("aa" = top-left), Sabaki's vertices, KataGo's ownership order
// and rectified photo grids. GTP/KataGo letters skip "I" and count rows from the bottom.
// Kept in lock-step with src/gobot/go/coords.py (both are tested against tests/fixtures/coords.json).

export const GTP_COLUMNS = 'ABCDEFGHJKLMNOPQRSTUVWXYZ'
export const SIZES = [9, 13, 19] as const
export type BoardSize = (typeof SIZES)[number]

export function inBounds(x: number, y: number, size: number): boolean {
  return Number.isInteger(x) && Number.isInteger(y) && x >= 0 && y >= 0 && x < size && y < size
}

export function xyToGtp(x: number, y: number, size: number): string {
  if (!inBounds(x, y, size)) throw new Error(`point (${x}, ${y}) is off a ${size}x${size} board`)
  return `${GTP_COLUMNS[x]}${size - y}`
}

/** Parses a GTP vertex; returns null for "pass". Throws on malformed/off-board input. */
export function gtpToXy(move: string, size: number): [number, number] | null {
  const s = move.trim().toUpperCase()
  if (s === 'PASS') return null
  const m = /^([A-Z])(\d+)$/.exec(s)
  if (!m) throw new Error(`bad GTP vertex ${move}`)
  const x = GTP_COLUMNS.indexOf(m[1])
  const y = size - parseInt(m[2], 10)
  if (x < 0 || !inBounds(x, y, size)) throw new Error(`GTP vertex ${move} is off a ${size}x${size} board`)
  return [x, y]
}

export function xyToSgf(x: number, y: number, size: number): string {
  if (!inBounds(x, y, size)) throw new Error(`point (${x}, ${y}) is off a ${size}x${size} board`)
  return String.fromCharCode(97 + x) + String.fromCharCode(97 + y)
}

/** Parses an SGF point; returns null for a pass ("" or "tt" on boards up to 19x19). */
export function sgfToXy(value: string, size: number): [number, number] | null {
  if (value === '' || (value === 'tt' && size <= 19)) return null
  if (!/^[a-z]{2}$/.test(value)) throw new Error(`bad SGF point ${value}`)
  const x = value.charCodeAt(0) - 97
  const y = value.charCodeAt(1) - 97
  if (!inBounds(x, y, size)) throw new Error(`SGF point ${value} is off a ${size}x${size} board`)
  return [x, y]
}

export function ownershipIndex(x: number, y: number, size: number): number {
  return y * size + x
}

export function columnLabel(x: number): string {
  return GTP_COLUMNS[x]
}

export function rowLabel(y: number, size: number): string {
  return String(size - y)
}

export function starPoints(size: number): [number, number][] {
  if (size === 19)
    return [3, 9, 15].flatMap((x) => [3, 9, 15].map((y) => [x, y] as [number, number]))
  if (size === 13) return [[3, 3], [9, 3], [3, 9], [9, 9], [6, 6]]
  if (size === 9) return [[2, 2], [6, 2], [2, 6], [6, 6], [4, 4]]
  return []
}
