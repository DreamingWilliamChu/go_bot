import { useState } from 'preact/hooks'
import { SIZES, type BoardSize } from './coords'
import { type Color, DEFAULT_KOMI, RULES, type Rules, validKomi } from './game'

export interface GameSettingsValue {
  size: BoardSize
  player: Color | null // null = not chosen yet (photo flow requires an explicit choice)
  rules: Rules
  komi: number
}

interface Props {
  value: GameSettingsValue
  onChange: (v: GameSettingsValue) => void
  sizeLocked?: boolean
  idPrefix?: string
}

export function GameSettingsForm({ value, onChange, sizeLocked, idPrefix = 'gs' }: Props) {
  const [komiText, setKomiText] = useState(String(value.komi))
  const komiOk = validKomi(parseFloat(komiText))

  return (
    <div class="game-settings">
      <fieldset class="field">
        <legend>Next to play</legend>
        <div class="seg">
          {(['B', 'W'] as Color[]).map((c) => (
            <button
              key={c}
              type="button"
              class={value.player === c ? 'on' : ''}
              aria-pressed={value.player === c}
              data-testid={`${idPrefix}-player-${c}`}
              onClick={() => onChange({ ...value, player: c })}
            >
              <span class={`dot ${c}`} /> {c === 'B' ? 'Black' : 'White'}
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
              disabled={sizeLocked && value.size !== s}
              aria-pressed={value.size === s}
              onClick={() => onChange({ ...value, size: s })}
            >
              {s}×{s}
            </button>
          ))}
        </div>
      </fieldset>
      <div class="field-row">
        <label class="field">
          Rules
          <select
            value={value.rules}
            onChange={(e) => {
              const r = (e.target as HTMLSelectElement).value as Rules
              onChange({ ...value, rules: r })
            }}
          >
            {RULES.map((r) => (
              <option key={r.id} value={r.id}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label class="field">
          Komi
          <input
            type="text"
            inputMode="decimal"
            value={komiText}
            aria-invalid={!komiOk}
            onInput={(e) => {
              const t = (e.target as HTMLInputElement).value
              setKomiText(t)
              const k = parseFloat(t)
              if (validKomi(k)) onChange({ ...value, komi: k })
            }}
          />
        </label>
      </div>
      {!komiOk ? <p class="note error small">Komi must be a multiple of 0.5 (e.g. 7.5 or 6.5).</p> : null}
      {value.komi !== DEFAULT_KOMI[value.rules] ? (
        <p class="muted tiny">
          Usual komi for {RULES.find((r) => r.id === value.rules)?.label}: {DEFAULT_KOMI[value.rules]}.{' '}
          <button
            type="button"
            class="linkish"
            onClick={() => {
              setKomiText(String(DEFAULT_KOMI[value.rules]))
              onChange({ ...value, komi: DEFAULT_KOMI[value.rules] })
            }}
          >
            Use it
          </button>
        </p>
      ) : null}
    </div>
  )
}
