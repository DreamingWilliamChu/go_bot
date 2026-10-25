// Shapes of the bonus-turn Go API (src/gobot/web/variant.py, src/gobot/variant/state.py public_view).

export type Color = 'B' | 'W'
export type PerColor<T> = { B: T; W: T }

export interface VariantConfig {
  size: number
  komi: number
  rules: 'chinese'
  black_bonuses: number
  white_bonuses: number
  moves_per_bonus: number
  horizon: number
  max_placements: number | null
}

export interface ScoreResult {
  black_stones: number
  white_stones: number
  black_area: number
  white_area: number
  komi: number
  black_total: number
  white_total: number
  lead: number
  owner: number[]
  winner: Color | null
}

export interface GameResult {
  reason: 'passes' | 'truncated'
  truncated: boolean
  score: ScoreResult
}

export interface VariantState {
  config: VariantConfig
  size: number
  grid: number[] // row-major: 0 empty, 1 Black, 2 White
  toMove: Color
  phase: 'in_turn' | 'turn_start' | 'terminal'
  movesLeft: number
  turnMoves: number
  bonusActive: boolean
  placedThisTurn: number
  remaining: PerColor<number>
  turnsStarted: PerColor<number>
  eligibleTurnsLeft: PerColor<number>
  nextTurnBonusProbability: PerColor<number>
  passStreak: number
  placements: number
  captures: PerColor<number>
  lastPlacement: { x: number; y: number; color: Color } | null
  historyKnown: boolean
  legal: number[]
  result: GameResult | null
}

export type VariantEvent =
  | { type: 'turn_start'; player: Color; turnIndex: number; bonus: boolean; moves: number; probability: number }
  | { type: 'move'; player: Color; x?: number; y?: number; pass?: true; captured?: number; turnEnded: boolean }
  | ({ type: 'game_end' } & GameResult)

export interface VariantView {
  gameId: string
  revision: number
  provenance: 'new' | 'photo' | 'branch' | 'import'
  commitment: string
  state: VariantState
  events: VariantEvent[]
  newEvents: VariantEvent[]
  actions: ([number, number] | 'pass')[]
  canUndo: boolean
  canRedo: boolean
  reveal: { salt: string; schedule: PerColor<number[]> } | null
}

/** Settings for the *next* bonus game (never applied to a game in progress). */
export interface BonusPrefs {
  size: number
  komi: number
  blackBonuses: number
  whiteBonuses: number
  movesPerBonus: number
  horizon: number // 0: the default for the board size
}

export const DEFAULT_HORIZON: Record<number, number> = { 9: 20, 11: 30, 13: 45, 19: 90 }

export function horizonFor(p: Pick<BonusPrefs, 'size' | 'horizon'>): number {
  return p.horizon > 0 ? p.horizon : DEFAULT_HORIZON[p.size] ?? Math.max(1, Math.round((p.size * p.size) / 4))
}

export const DEFAULT_BONUS_PREFS: BonusPrefs = {
  size: 11,
  komi: 7.5,
  blackBonuses: 2,
  whiteBonuses: 2,
  movesPerBonus: 3,
  horizon: 0,
}

/** Explicit public bonus state for a position without history (photo, analysis branch). */
export interface BonusStateInput {
  toMove: Color
  remaining: PerColor<number>
  turnsStarted: PerColor<number>
  bonusActive: boolean
  movesLeft: number
  passStreak: number
}
