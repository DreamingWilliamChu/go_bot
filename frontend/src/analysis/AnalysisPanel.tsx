import type { AnalysisResult, EngineStatus, Preset } from '../api/client'
import {
  CAND_LABELS,
  colorName,
  leadText,
  pct,
  playerWinrate,
  summarize,
  visitsLabel,
  visitsText,
} from './format'

export type AnalysisStatus = 'idle' | 'starting' | 'running' | 'done' | 'cancelled' | 'error'

export interface PvSelection {
  cand: number
  step: number // number of PV moves shown (1..pv.length)
}

interface Props {
  status: AnalysisStatus
  result: AnalysisResult | null
  error: string | null
  warning: string | null
  engine: EngineStatus | null
  presets: Preset[]
  presetId: string
  customSeconds: number
  showOwnership: boolean
  autoAnalyze: boolean
  paused?: boolean // Erase is on: no analysis until it is turned off
  pvSel: PvSelection | null
  connection: string
  onPreset: (id: string) => void
  onCustomSeconds: (s: number) => void
  onAnalyze: () => void
  onStop: () => void
  onToggleOwnership: () => void
  onToggleAuto: () => void
  onPv: (sel: PvSelection | null) => void
}

export function AnalysisPanel(p: Props) {
  const r = p.result
  const running = p.status === 'running' || p.status === 'starting'
  const player = r?.currentPlayer
  const cands = r?.candidates.slice(0, 3) ?? []
  const pvCand = p.pvSel ? r?.candidates[p.pvSel.cand] : null
  const summary = r && r.type === 'final' ? summarize(r, p.engine?.modelInternalName ?? p.engine?.modelId ?? null) : null

  return (
    <section class="panel analysis" aria-label="KataGo analysis">
      <div class="analysis-controls">
        <div class="seg" role="radiogroup" aria-label="Thinking time">
          {p.presets.map((pr) => (
            <button
              key={pr.id}
              role="radio"
              aria-checked={p.presetId === pr.id}
              class={p.presetId === pr.id ? 'on' : ''}
              onClick={() => p.onPreset(pr.id)}
            >
              {pr.label}
              <small>{pr.seconds}s</small>
            </button>
          ))}
          <button
            role="radio"
            aria-checked={p.presetId === 'custom'}
            class={p.presetId === 'custom' ? 'on' : ''}
            onClick={() => p.onPreset('custom')}
          >
            Custom
            <small>{p.customSeconds}s</small>
          </button>
        </div>
        {p.presetId === 'custom' ? (
          <label class="custom-time">
            Seconds
            <input
              type="number"
              min={0.5}
              max={600}
              step={0.5}
              value={p.customSeconds}
              onChange={(e) => {
                const v = parseFloat((e.target as HTMLInputElement).value)
                if (Number.isFinite(v) && v >= 0.5 && v <= 600) p.onCustomSeconds(v)
              }}
            />
          </label>
        ) : null}
        <div class="row">
          {running ? (
            <button class="btn stop" onClick={p.onStop} data-testid="stop">
              Stop
            </button>
          ) : (
            <button class="btn primary" onClick={p.onAnalyze} disabled={p.paused} data-testid="analyze">
              Analyze
            </button>
          )}
          <label class="toggle">
            <input type="checkbox" checked={p.showOwnership} onChange={p.onToggleOwnership} /> Territory
          </label>
          <label class="toggle">
            <input type="checkbox" checked={p.autoAnalyze} disabled={p.paused} onChange={p.onToggleAuto} /> Auto
          </label>
        </div>
      </div>

      {p.paused ? <p class="note">Analysis paused while erasing stones.</p> : null}
      {p.connection !== 'open' ? (
        <p class="note warn">
          {p.connection === 'unauthorized'
            ? 'Signed out. Reload the page and sign in again.'
            : 'Connecting to the Mac…'}
        </p>
      ) : null}
      {p.status === 'starting' ? <p class="note">Loading KataGo and the neural network…</p> : null}
      {p.error ? (
        <p class="note error" role="alert" data-testid="analysis-error">
          {p.error}
        </p>
      ) : null}
      {p.warning ? <p class="note warn">{p.warning}</p> : null}

      {r && r.blackWinrate !== undefined && player ? (
        <div class="overview" data-testid="overview">
          <div class="to-play">
            <span class={`dot ${player}`} /> {colorName(player)} to play
            {r.type === 'partial' ? <span class="live">searching…</span> : null}
            {r.cancelled ? <span class="live">stopped</span> : null}
          </div>
          <div class="wr">
            <div class="wr-label">
              Black's win probability <b data-testid="black-winrate">{pct(r.blackWinrate)}</b>
              <span class="muted"> · White {pct(1 - r.blackWinrate)}</span>
            </div>
            <div class="wr-bar" aria-hidden="true">
              <div class="wr-b" style={{ width: `${(r.blackWinrate * 100).toFixed(1)}%` }} />
            </div>
          </div>
          <div class="lead">
            Score estimate: <b data-testid="score-lead">{leadText(r.blackScoreLead)}</b>
            <span class="muted"> (points ahead)</span>
          </div>
        </div>
      ) : null}

      {cands.length ? (
        <ol class="cands" data-testid="candidates">
          {cands.map((c, i) => (
            <li key={c.gtp} class={p.pvSel?.cand === i ? 'sel' : ''}>
              <span class={`badge r${i}`}>{CAND_LABELS[i]}</span>
              <div class="cand-main">
                <div>
                  <b class="mv">{c.pass ? 'Pass' : c.gtp}</b>{' '}
                  <span>
                    {player ? `${colorName(player)} win ${pct(playerWinrate(c.blackWinrate, player))}` : ''}
                  </span>
                </div>
                <div class="muted small">
                  Score {leadText(c.blackScoreLead)} · {visitsLabel(c.visits)}
                </div>
              </div>
              <button
                class="btn small"
                disabled={!c.pv.length}
                onClick={() =>
                  p.onPv(p.pvSel?.cand === i ? null : { cand: i, step: Math.min(c.pv.length, 1) })
                }
                aria-pressed={p.pvSel?.cand === i}
              >
                {p.pvSel?.cand === i ? 'Hide' : 'Line'}
              </button>
            </li>
          ))}
        </ol>
      ) : null}

      {pvCand && p.pvSel ? (
        <div class="pv" data-testid="pv">
          <div class="pv-head">
            Line {CAND_LABELS[p.pvSel.cand]}: move {p.pvSel.step} / {pvCand.pv.length}
          </div>
          <div class="pv-moves">
            {pvCand.pv.map((s, i) => (
              <button
                key={i}
                class={`pvm ${s.color} ${i < p.pvSel!.step ? 'shown' : ''}`}
                onClick={() => p.onPv({ cand: p.pvSel!.cand, step: i + 1 })}
              >
                {i + 1}.{s.pass ? 'pass' : s.gtp}
              </button>
            ))}
          </div>
          <div class="row">
            <button class="btn small" onClick={() => p.onPv({ ...p.pvSel!, step: 0 })} aria-label="first">
              ⏮
            </button>
            <button
              class="btn small"
              onClick={() => p.onPv({ ...p.pvSel!, step: Math.max(0, p.pvSel!.step - 1) })}
              aria-label="previous"
            >
              ◀
            </button>
            <button
              class="btn small"
              onClick={() => p.onPv({ ...p.pvSel!, step: Math.min(pvCand.pv.length, p.pvSel!.step + 1) })}
              aria-label="next"
              data-testid="pv-next"
            >
              ▶
            </button>
            <button
              class="btn small"
              onClick={() => p.onPv({ ...p.pvSel!, step: pvCand.pv.length })}
              aria-label="last"
            >
              ⏭
            </button>
            <button class="btn small" onClick={() => p.onPv(null)}>
              Close
            </button>
          </div>
        </div>
      ) : null}

      {summary ? <p class="summary small">{summary}</p> : null}

      <dl class="engine-info small" data-testid="engine-info">
        <div>
          <dt>Model</dt>
          <dd>{p.engine?.modelInternalName ?? p.engine?.modelLabel ?? p.engine?.modelId ?? '–'}</dd>
        </div>
        <div>
          <dt>KataGo</dt>
          <dd>{p.engine?.version ? `v${p.engine.version}` : '–'}</dd>
        </div>
        <div>
          <dt>Backend</dt>
          <dd>{p.engine?.backend ?? '–'}</dd>
        </div>
        <div>
          <dt>Time</dt>
          <dd>{r ? `${r.elapsed.toFixed(1)} s` : '–'}</dd>
        </div>
        <div>
          <dt>Visits</dt>
          <dd data-testid="visits">{visitsText(r?.rootVisits)}</dd>
        </div>
      </dl>
      {p.engine?.fallbackNote ? <p class="note warn small">{p.engine.fallbackNote}</p> : null}
      <p class="muted tiny">
        All numbers and suggestions come from KataGo. Win probability and score are shown for the player named.
      </p>
    </section>
  )
}
