// Keep color plays in rounds of `total` moves by one colour. The board shows predictions for the
// moves left in the round, numbered by their place in it (after one move: 2 and 3).

export interface Point {
  x: number
  y: number
}

export interface KeepRound {
  done: number // moves already played in this round
  frozen: Point[] | null // predictions kept from before; null: use the live analysis
}

export const FRESH_ROUND: KeepRound = { done: 0, frozen: null }

/**
 * After a move (`played`, or null for a pass): playing one of the `shown` predictions keeps the
 * others; any other move asks for new predictions for the rest of the round. After the last move
 * of a round: null (the moves in a row are used up).
 */
export function advanceRound(round: KeepRound, shown: Point[], played: Point | null, total: number): KeepRound | null {
  const done = round.done + 1
  if (done >= total) return null
  const hit = played && shown.some((p) => p.x === played.x && p.y === played.y)
  if (!hit) return { done, frozen: null }
  return { done, frozen: shown.filter((p) => !(p.x === played.x && p.y === played.y)) }
}
