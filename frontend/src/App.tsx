import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api, ApiError, type Preset, type StatusResponse } from './api/client'
import { useAnalysis } from './analysis/useAnalysis'
import { type GameState, currentBoard, newGame } from './game/game'
import { BoardScreen } from './screens/BoardScreen'
import { PhotoScreen } from './screens/PhotoScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { load, loadRaw, save } from './storage'

type Tab = 'board' | 'photo' | 'settings'

interface Prefs {
  presetId: string
  customSeconds: number
  showOwnership: boolean
  autoAnalyze: boolean
}

const DEFAULT_PRESETS: Preset[] = [
  { id: 'quick', label: 'Quick', seconds: 2 },
  { id: 'standard', label: 'Standard', seconds: 6 },
  { id: 'deep', label: 'Deep', seconds: 20 },
]
const DEFAULT_PREFS: Prefs = { presetId: 'standard', customSeconds: 10, showOwnership: false, autoAnalyze: true }

function loadGame(): GameState {
  const g = loadRaw<GameState>('gobot.game.v1')
  if (g && g.setup && Array.isArray(g.moves)) {
    try {
      currentBoard(g) // validate stored history
      return { ...g, redo: g.redo ?? [] }
    } catch {
      /* corrupt: start fresh */
    }
  }
  return newGame(19)
}

export function App() {
  const [tab, setTab] = useState<Tab>(() => (location.hash.startsWith('#photo') ? 'photo' : 'board'))
  const [game, setGameState] = useState<GameState>(loadGame)
  const [prefs, setPrefs] = useState<Prefs>(() => load('gobot.prefs.v1', DEFAULT_PREFS))
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const [paired, setPaired] = useState<'unknown' | 'ok' | 'needed'>('unknown')
  const toastTimer = useRef<number | undefined>(undefined)

  const toast = useCallback((m: string) => {
    setToastMsg(m)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToastMsg(null), 4500)
  }, [])

  const refreshStatus = useCallback(() => {
    api
      .get<StatusResponse>('/api/status')
      .then((s) => {
        setStatus(s)
        setPaired('ok')
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) setPaired('needed')
      })
  }, [])

  const analysis = useAnalysis(refreshStatus)

  // Pairing link: https://mac:8000/#pair=TOKEN (fresh load from the QR code, or a hash change when
  // the link is opened in a tab that already shows the app).
  useEffect(() => {
    const tryPair = (): boolean => {
      const m = /#pair=([\w-]+)/.exec(location.hash)
      if (!m) return false
      const finish = () => {
        history.replaceState(null, '', location.pathname)
        refreshStatus()
      }
      api
        .post('/api/pair', { token: m[1] })
        .then(() => {
          toast('Paired with the Mac')
          analysis.reconnect()
          finish()
        })
        .catch((e) => {
          toast((e as Error).message)
          finish()
        })
      return true
    }
    if (!tryPair()) refreshStatus()
    window.addEventListener('hashchange', tryPair)
    return () => window.removeEventListener('hashchange', tryPair)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll engine status while it is starting or failing.
  useEffect(() => {
    const st = status?.engine.state
    const delay = st === 'ready' ? 30000 : 3000
    const t = window.setTimeout(refreshStatus, delay)
    return () => window.clearTimeout(t)
  }, [status, refreshStatus])

  useEffect(() => save('gobot.game.v1', game), [game])
  useEffect(() => save('gobot.prefs.v1', prefs), [prefs])

  const presets = status?.presets ?? DEFAULT_PRESETS
  const seconds =
    prefs.presetId === 'custom' ? prefs.customSeconds : presets.find((p) => p.id === prefs.presetId)?.seconds ?? 6

  const gameRef = useRef(game)
  gameRef.current = game
  const prefsRef = useRef({ seconds, auto: prefs.autoAnalyze })
  prefsRef.current = { seconds, auto: prefs.autoAnalyze }

  const setGame = useCallback(
    (g: GameState) => {
      analysis.invalidate()
      setGameState(g)
      if (prefsRef.current.auto) analysis.start(g, prefsRef.current.seconds)
    },
    [analysis.invalidate, analysis.start],
  )

  const startAnalysis = useCallback(() => analysis.start(gameRef.current, prefsRef.current.seconds), [analysis.start])

  const engineChip = status?.engine
  const chipClass = !engineChip ? 'off' : engineChip.state === 'ready' ? 'ok' : engineChip.error ? 'bad' : 'wait'

  return (
    <div class="app">
      <header class="topbar">
        <div class="brand">
          <span class="logo" aria-hidden="true" />
          <span>
            Go Analysis <span class="muted">围棋</span>
          </span>
        </div>
        <nav class="tabs" role="tablist">
          {(
            [
              ['board', 'Board'],
              ['photo', 'Photo'],
              ['settings', 'Engine'],
            ] as [Tab, string][]
          ).map(([id, label]) => (
            <button key={id} role="tab" aria-selected={tab === id} class={tab === id ? 'on' : ''} onClick={() => setTab(id)} data-testid={`tab-${id}`}>
              {label}
            </button>
          ))}
        </nav>
        <span class={`chip ${chipClass}`} title={engineChip?.error ?? ''} data-testid="engine-chip">
          {engineChip
            ? engineChip.state === 'ready'
              ? 'KataGo ready'
              : engineChip.error
                ? 'KataGo error'
                : `KataGo ${engineChip.state}`
            : 'offline'}
        </span>
      </header>

      {paired === 'needed' ? (
        <div class="panel pairing" role="alert">
          <h2>Pair this device</h2>
          <p>
            This Mac only answers devices that scanned its pairing QR code. On the Mac, run{' '}
            <code>./scripts/start.sh --lan</code> and scan the QR code shown in the terminal (or under "Engine"
            on the Mac).
          </p>
          <PairForm
            onPaired={() => {
              toast('Paired')
              analysis.reconnect()
              refreshStatus()
            }}
            toast={toast}
          />
        </div>
      ) : null}

      <main>
        {tab === 'board' ? (
          <BoardScreen
            game={game}
            setGame={setGame}
            analysis={analysis}
            engine={status?.engine ?? null}
            presets={presets}
            presetId={prefs.presetId}
            customSeconds={prefs.customSeconds}
            showOwnership={prefs.showOwnership}
            autoAnalyze={prefs.autoAnalyze}
            onPreset={(id) => setPrefs({ ...prefs, presetId: id })}
            onCustomSeconds={(s) => setPrefs({ ...prefs, customSeconds: s })}
            onAnalyze={startAnalysis}
            onStop={analysis.stop}
            onToggleOwnership={() => setPrefs({ ...prefs, showOwnership: !prefs.showOwnership })}
            onToggleAuto={() => setPrefs({ ...prefs, autoAnalyze: !prefs.autoAnalyze })}
            toast={toast}
          />
        ) : tab === 'photo' ? (
          <PhotoScreen
            defaults={game.setup}
            status={status}
            toast={toast}
            onConfirmed={(setup) => {
              const g: GameState = { setup, moves: [], redo: [], revision: game.revision + 1 }
              analysis.invalidate()
              setGameState(g)
              setTab('board')
              analysis.start(g, prefsRef.current.seconds)
            }}
          />
        ) : (
          <SettingsScreen status={status} refreshStatus={refreshStatus} toast={toast} />
        )}
      </main>

      {toastMsg ? (
        <div class="toast" role="status" onClick={() => setToastMsg(null)} data-testid="toast">
          {toastMsg}
        </div>
      ) : null}
    </div>
  )
}

function PairForm({ onPaired, toast }: { onPaired: () => void; toast: (m: string) => void }) {
  const [code, setCode] = useState('')
  return (
    <form
      class="row"
      onSubmit={(e) => {
        e.preventDefault()
        api
          .post('/api/pair', { token: code.trim() })
          .then(onPaired)
          .catch((err) => toast((err as Error).message))
      }}
    >
      <input
        type="text"
        placeholder="Pairing code"
        value={code}
        autoComplete="off"
        autoCapitalize="off"
        onInput={(e) => setCode((e.target as HTMLInputElement).value)}
      />
      <button class="btn primary" type="submit">
        Pair
      </button>
    </form>
  )
}
