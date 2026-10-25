// Plain-language status lines for bonus-turn games. Bonus *opportunities* (turns still to come) and the
// *moves* left in the current turn are different counts and are always named differently.

import { colorName } from '../analysis/format'
import type { GameResult, VariantView } from './types'

export function bonusesLeftText(v: VariantView): string {
  const r = v.state.remaining
  return `Bonuses left — Black: ${r.B} · White: ${r.W}`
}

export function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

export function turnText(v: VariantView): string {
  const s = v.state
  if (s.phase === 'terminal') return 'Game over'
  const who = colorName(s.toMove)
  if (s.bonusActive) return `${who} bonus turn — ${plural(s.movesLeft, 'move')} remaining`
  return `${who} to play — normal turn (1 move)`
}

export function pctText(p: number): string {
  if (p <= 0) return '0%'
  if (p >= 1) return '100%'
  return `${Math.round(p * 100)}%`
}

/** The public chance that each player's *next* turn is a bonus turn: the Mac's random schedule, or the
 *  model of the judge's timing (random to the players and the program). */
export function chanceText(v: VariantView): string {
  const s = v.state
  const judge = s.config.trigger === 'judge'
  const one = (c: 'B' | 'W') => {
    const r = s.remaining[c]
    if (r <= 0) return 'none'
    const left = judge ? Math.max(s.eligibleTurnsLeft[c], r) : s.eligibleTurnsLeft[c]
    return left > 0 ? `${r}/${left} (${pctText(s.nextTurnBonusProbability[c])})` : 'none'
  }
  const line = `Black ${one('B')} · White ${one('W')}`
  return judge ? `The judge decides; modelled next-turn chance — ${line}` : `Next-turn bonus chance — ${line}`
}

/** Who Keep color holds, and why. In a bonus game the lock only ever comes from a bonus turn. With the
 *  judge trigger the button is how the judge grants a bonus (``canUse``): before the turn's first move. */
export function lockText(v: VariantView): { on: boolean; label: string; title: string; canUse: boolean } {
  const s = v.state
  const who = colorName(s.toMove)
  if (s.phase !== 'terminal' && s.bonusActive)
    return {
      on: true,
      canUse: false,
      label: `Keep ${who} · bonus ${s.turnMoves - s.movesLeft + 1}/${s.turnMoves}`,
      title: `${who} is locked by a bonus turn: ${plural(s.movesLeft, 'move')} left. It releases by itself.`,
    }
  if (s.canGrantBonus) {
    const left = s.remaining[s.toMove]
    return {
      on: false,
      canUse: true,
      label: 'Grant bonus',
      title: `For the judge only: grant ${who} one of ${left} bonus${left === 1 ? '' : 'es'}. ${who} then plays ${s.config.moves_per_bonus} moves in a row this turn, and Keep color stays locked until they are used.`,
    }
  }
  if (s.config.trigger === 'judge')
    return {
      on: false,
      canUse: false,
      label: s.phase === 'terminal' ? 'Keep · game over' : `Keep · ${who} has no bonus`,
      title: 'The judge can grant a bonus only before the first move of a turn, while the player has one left.',
    }
  return {
    on: false,
    canUse: false,
    label: 'Keep · bonus only',
    title: 'In Bonus Mode only a bonus turn turns Keep color on. It cannot be switched on by hand.',
  }
}

/** Shown before the player chooses a move: a bonus has just triggered and none of its moves is used. */
export function announcementText(v: VariantView): string | null {
  const s = v.state
  if (s.phase !== 'in_turn' || !s.bonusActive || s.movesLeft !== s.turnMoves) return null
  const last = v.events[v.events.length - 1]
  // An imported position starts in its bonus turn without a trigger event: nothing to announce.
  const started = last && ((last.type === 'turn_start' && last.bonus) || last.type === 'bonus_granted')
  if (!started || last.player !== s.toMove) return null
  return `Bonus! ${colorName(s.toMove)} plays ${plural(s.turnMoves, 'move')} in a row this turn.`
}

export function resultText(r: GameResult): string {
  const s = r.score
  const lead = s.lead
  const who = lead > 0 ? `Black wins by ${lead}` : lead < 0 ? `White wins by ${-lead}` : 'A draw'
  const counts = `Black ${s.black_area} · White ${s.white_area} + ${s.komi} komi`
  if (r.truncated) return `Stopped at the move limit (no result). Board as it stands: ${counts}.`
  return `${who}, scored as the board stands: ${counts}.`
}

export function provenanceText(v: VariantView): string | null {
  if (v.state.historyKnown) return null
  const what = v.provenance === 'photo' ? 'a photo' : 'an edited position'
  return `Started from ${what} with the bonus state you entered. History unknown: superko only checks positions from here, and earlier bonus timing is not known.`
}
