import { useState } from 'preact/hooks'
import { SIZES } from '../game/coords'
import { validKomi } from '../game/game'
import { type BonusPrefs, horizonFor } from './types'

interface Props {
  value: BonusPrefs
  onChange: (v: BonusPrefs) => void
  idPrefix?: string
}

function clampInt(text: string, lo: number, hi: number): number | null {
  const n = Number(text)
  return Number.isInteger(n) && n >= lo && n <= hi ? n : null
}

/** Settings for the next bonus game. They never change a game in progress. */
export function BonusSettings({ value, onChange, idPrefix = 'bs' }: Props) {
  const [komiText, setKomiText] = useState(String(value.komi))
  const h = horizonFor(value)
  const judge = value.trigger === 'judge'
  const limit = judge ? MAX_JUDGE_BONUSES : h
  const tooMany = value.blackBonuses > limit || value.whiteBonuses > limit

  const num = (label: string, key: 'blackBonuses' | 'whiteBonuses', testid: string) => (
    <label class="field">
      {label}
      <input
        type="number"
        inputMode="numeric"
        min={0}
        max={limit}
        value={value[key]}
        data-testid={`${idPrefix}-${testid}`}
        onInput={(e) => {
          const n = clampInt((e.target as HTMLInputElement).value, 0, 1000)
          if (n !== null) onChange({ ...value, [key]: n })
        }}
      />
    </label>
  )

  return (
    <div class="bonus-settings">
      <fieldset class="field">
        <legend>Bonus turns</legend>
        <div class="seg" role="radiogroup" aria-label="Who decides bonus turns">
          {(
            [
              ['judge', 'Judge decides'],
              ['random', 'Random (the Mac)'],
            ] as const
          ).map(([t, label]) => (
            <button
              key={t}
              type="button"
              role="radio"
              class={value.trigger === t ? 'on' : ''}
              aria-checked={value.trigger === t}
              data-testid={`${idPrefix}-trigger-${t}`}
              onClick={() => onChange({ ...value, trigger: t })}
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>
      <fieldset class="field">
        <legend>Board size</legend>
        <div class="seg">
          {SIZES.map((s) => (
            <button
              key={s}
              type="button"
              class={value.size === s ? 'on' : ''}
              aria-pressed={value.size === s}
              data-testid={`${idPrefix}-size-${s}`}
              onClick={() => onChange({ ...value, size: s, horizon: 0 })}
            >
              {s}×{s}
            </button>
          ))}
        </div>
      </fieldset>
      <div class="field-row three">
        {num('Black bonuses', 'blackBonuses', 'black')}
        {num('White bonuses', 'whiteBonuses', 'white')}
        <label class="field">
          Komi
          <input
            type="text"
            inputMode="decimal"
            value={komiText}
            aria-invalid={!validKomi(parseFloat(komiText))}
            onInput={(e) => {
              const t = (e.target as HTMLInputElement).value
              setKomiText(t)
              const k = parseFloat(t)
              if (validKomi(k)) onChange({ ...value, komi: k })
            }}
          />
        </label>
      </div>
      <fieldset class="field">
        <legend>Moves per bonus turn (in total)</legend>
        <div class="seg">
          {[2, 3, 4, 5, 6].map((n) => (
            <button
              key={n}
              type="button"
              class={value.movesPerBonus === n ? 'on' : ''}
              aria-pressed={value.movesPerBonus === n}
              data-testid={`${idPrefix}-moves-${n}`}
              onClick={() => onChange({ ...value, movesPerBonus: n })}
            >
              {n}
            </button>
          ))}
        </div>
      </fieldset>
      <label class="field">
          {judge
            ? 'Horizon H: the analysis models the judge’s bonuses as random personal turns 1–H'
            : 'Horizon H: bonuses fall on random personal turns 1–H'}
          <input
            type="number"
            inputMode="numeric"
            min={1}
            max={400}
            value={value.horizon || ''}
            placeholder={`${h} (default for ${value.size}×${value.size})`}
            data-testid={`${idPrefix}-horizon`}
            onInput={(e) => {
              const t = (e.target as HTMLInputElement).value
              if (t === '') onChange({ ...value, horizon: 0 })
              const n = clampInt(t, 1, 400)
              if (n !== null) onChange({ ...value, horizon: n })
            }}
          />
        </label>
      {tooMany ? (
        <p class="note error small">
          Each allocation must be between 0 and {judge ? MAX_JUDGE_BONUSES : `H (${h})`}.
        </p>
      ) : judge ? (
        <p class="muted tiny">
          A judge decides when bonuses happen: before the first move of a turn, the judge taps Keep color (Grant
          bonus). The player then makes {value.movesPerBonus} moves in a row. Neither the players nor the analysis
          choose bonuses: the analysis treats them as random, spread over each player's personal turns 1–{h}. Unused
          bonuses lapse when the game ends.
        </p>
      ) : (
        <p class="muted tiny">
          Each player's bonus turns are drawn at random, without repeats, from their personal turns 1–{h}. Unused
          bonuses lapse when the game ends. Equal allocations can still differ in value: timing is luck.
        </p>
      )}
    </div>
  )
}

export const MAX_JUDGE_BONUSES = 50

export function bonusPrefsValid(p: BonusPrefs): boolean {
  const limit = p.trigger === 'judge' ? MAX_JUDGE_BONUSES : horizonFor(p)
  return validKomi(p.komi) && p.blackBonuses <= limit && p.whiteBonuses <= limit
}
