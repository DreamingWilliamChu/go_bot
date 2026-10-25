import { ApiError, api } from '../api/client'
import type { BonusPrefs, BonusStateInput, VariantView } from './types'

/** The game changed elsewhere (another device): here is the current one. */
export class StaleGame extends Error {
  constructor(public game: VariantView) {
    super('The game changed on another device; showing the current position.')
  }
}

export type VariantAction =
  | { type: 'place'; x: number; y: number }
  | { type: 'pass' }
  | { type: 'bonus' } // judge trigger: the judge grants a bonus on this turn
  | { type: 'undo' }
  | { type: 'redo' }

function configBody(p: BonusPrefs) {
  return {
    trigger: p.trigger === 'random' ? 'random' : 'judge',
    size: p.size,
    komi: p.komi,
    black_bonuses: p.blackBonuses,
    white_bonuses: p.whiteBonuses,
    moves_per_bonus: p.movesPerBonus,
    horizon: p.horizon > 0 ? p.horizon : null,
  }
}

// Variant endpoints put {message, reason} or {message, game} in `detail`; ApiError carries it as JSON text.
function rethrow(e: unknown): never {
  if (e instanceof ApiError) {
    let d: { message?: string; game?: VariantView } | null = null
    try {
      d = JSON.parse(e.message) as { message?: string; game?: VariantView }
    } catch {
      /* a plain-text message */
    }
    if (d && e.status === 409 && d.game) throw new StaleGame(d.game)
    if (d && d.message) throw new ApiError(e.status, d.message)
  }
  throw e
}

export const variantApi = {
  create: (p: BonusPrefs) => api.post<VariantView>('/api/variant/games', { config: configBody(p) }).catch(rethrow),
  get: (id: string) => api.get<VariantView>(`/api/variant/games/${id}`).catch(rethrow),
  act: (v: VariantView, action: VariantAction) =>
    api
      .post<VariantView>(`/api/variant/games/${v.gameId}/actions`, { expectedRevision: v.revision, action })
      .catch(rethrow),
  importPosition: (
    p: BonusPrefs,
    stones: { color: 'B' | 'W'; x: number; y: number }[],
    s: BonusStateInput,
    provenance: 'photo' | 'branch',
  ) => api.post<VariantView>('/api/variant/games/import', { config: configBody(p), stones, ...s, provenance }).catch(rethrow),
  open: (record: unknown) => api.post<VariantView>('/api/variant/games/open', record).catch(rethrow),
  exportUrl: (id: string) => `/api/variant/games/${id}/export`,
}
