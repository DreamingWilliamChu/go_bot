import type { AnalysisResult, Candidate } from '../api/client'

export const colorName = (c: 'B' | 'W') => (c === 'B' ? 'Black' : 'White')

export function pct(p: number | undefined | null, digits = 1): string {
  if (p === undefined || p === null || !Number.isFinite(p)) return '–'
  return `${(p * 100).toFixed(digits)}%`
}

/** Score lead from Black's perspective → "Black +2.3" / "White +0.5" / "Even". */
export function leadText(blackLead: number | undefined | null): string {
  if (blackLead === undefined || blackLead === null || !Number.isFinite(blackLead)) return '–'
  if (Math.abs(blackLead) < 0.05) return 'Even'
  return blackLead > 0 ? `Black +${blackLead.toFixed(1)}` : `White +${(-blackLead).toFixed(1)}`
}

export function playerWinrate(blackWinrate: number, player: 'B' | 'W'): number {
  return player === 'B' ? blackWinrate : 1 - blackWinrate
}

export function visitsText(v: number | undefined): string {
  return v === undefined ? '–' : v.toLocaleString('en-US')
}

export const CAND_LABELS = ['A', 'B', 'C']

/**
 * A short plain-language summary built only from KataGo's numbers (no external model,
 * no invented reasoning). Returns null when there is nothing to summarise.
 */
export function summarize(r: AnalysisResult, modelName: string | null): string | null {
  if (!r.candidates.length || r.blackWinrate === undefined || !r.currentPlayer) return null
  const p = r.currentPlayer
  const top: Candidate = r.candidates[0]
  const parts: string[] = []
  parts.push(
    `KataGo${modelName ? ` (${modelName})` : ''}, ${visitsText(r.rootVisits)} visits: ${colorName(p)} to play.`,
  )
  parts.push(
    `Black's win probability ${pct(r.blackWinrate)}; estimated score ${leadText(r.blackScoreLead)}.`,
  )
  if (top.pass) {
    parts.push(`KataGo's top choice for ${colorName(p)} is to pass.`)
  } else {
    parts.push(
      `Top choice for ${colorName(p)}: ${top.gtp} (${colorName(p)} win ${pct(
        playerWinrate(top.blackWinrate, p),
      )}, ${visitsText(top.visits)} visits).`,
    )
  }
  const second = r.candidates[1]
  if (second) {
    const sign = p === 'B' ? 1 : -1
    const diff = sign * (second.blackScoreLead - top.blackScoreLead)
    const close = Math.abs(diff) < 0.5
    parts.push(
      `${second.gtp} is ${close ? 'about as good' : `${Math.abs(diff).toFixed(1)} points ${diff < 0 ? 'worse' : 'better'}`} by KataGo's score estimate.`,
    )
  }
  if ((r.rootVisits ?? 0) < 100)
    parts.push('Few visits so far: treat these numbers as rough.')
  return parts.join(' ')
}
