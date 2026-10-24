import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { api, ApiError, type AuthInfo, type Preset, type StatusResponse } from './api/client'
import { useAnalysis } from './analysis/useAnalysis'
import { colorName } from './analysis/format'
import { type Color, type GameState, currentBoard, newGame, nextPlayer } from './game/game'
import { FRESH_ROUND, type KeepRound, type Point, advanceRound } from './game/keepRound'
import { BoardScreen } from './screens/BoardScreen'
import { LoginScreen } from './screens/LoginScreen'
import { PhotoScreen } from './screens/PhotoScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { load, loadRaw, save } from './storage'

type Tab = 'board' | 'photo' | 'settings'

// The script this page was loaded from; null in `npm run dev` (no built assets there).
const OWN_BUILD =
  document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.getAttribute('src') ?? null

interface Prefs {
  presetId: string
  customSeconds: number
  showOwnership: boolean
  autoAnalyze: boolean
  keepMoves: number // Keep color: how many moves in a row the analysis shows
}

const DEFAULT_PRESETS: Preset[] = [
  { id: 'quick', label: 'Quick', seconds: 2 },
  { id: 'standard', label: 'Standard', seconds: 6 },
  { id: 'deep', label: 'Deep', seconds: 20 },
]
const DEFAULT_PREFS: Prefs = { presetId: 'deep', customSeconds: 10, showOwnership: false, autoAnalyze: true, keepMoves: 3 }

// v2 made Deep the default. v1 saved the old default on first load, so a saved
// v1 preset can't be told apart from a choice: carry the other prefs over and
// start on Deep once.
function loadPrefs(): Prefs {
  if (loadRaw<Prefs>('gobot.prefs.v2')) return load('gobot.prefs.v2', DEFAULT_PREFS)
  return { ...load('gobot.prefs.v1', DEFAULT_PREFS), presetId: DEFAULT_PREFS.presetId }
}

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
  // Keep color: while set, every move is played by this colour instead of alternating.
  const [keepColor, setKeepColor] = useState<Color | null>(null)
  const [keepRound, setKeepRound] = useState<KeepRound>(FRESH_ROUND)
  const toMove = keepColor ?? nextPlayer(game)
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs)
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const [auth, setAuth] = useState<AuthInfo | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)

  const toast = useCallback((m: string) => {
    setToastMsg(m)
    window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToastMsg(null), 4500)
  }, [])

  const refreshAuth = useCallback(() => {
    api
      .get<AuthInfo>('/api/auth/info')
      .then((a) => {
        setAuth(a)
        if (!a.required) {
          api
            .get<StatusResponse>('/api/status')
            .then(setStatus)
            .catch(() => undefined)
        }
      })
      .catch(() => undefined)
  }, [])

  const refreshStatus = useCallback(() => {
    api
      .get<StatusResponse>('/api/status')
      .then(setStatus)
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) refreshAuth() // signed out meanwhile
      })
  }, [refreshAuth])

  const analysis = useAnalysis(refreshStatus)

  // Pairing link: https://mac:8000/#pair=TOKEN (fresh load from the QR code, or a hash change when
  // the link is opened in a tab that already shows the app).
  useEffect(() => {
    const tryPair = (): boolean => {
      const m = /#pair=([\w-]+)/.exec(location.hash)
      if (!m) return false
      const finish = () => {
        history.replaceState(null, '', location.pathname)
        refreshAuth()
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
    if (!tryPair()) refreshAuth()
    window.addEventListener('hashchange', tryPair)
    return () => window.removeEventListener('hashchange', tryPair)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Poll engine status while it is starting or failing.
  useEffect(() => {
    if (!auth || auth.required) return
    const st = status?.engine.state
    const delay = st === 'ready' ? 30000 : 3000
    const t = window.setTimeout(refreshStatus, delay)
    return () => window.clearTimeout(t)
  }, [status, auth, refreshStatus])

  // An iPhone home-screen app (and Safari tabs) resume without reloading, so a page can stay on
  // an old build for days. Check the server's build whenever the page comes back.
  useEffect(() => {
    if (!auth || auth.required) return
    const onShow = () => {
      if (document.visibilityState === 'visible') refreshStatus()
    }
    document.addEventListener('visibilitychange', onShow)
    window.addEventListener('pageshow', onShow)
    return () => {
      document.removeEventListener('visibilitychange', onShow)
      window.removeEventListener('pageshow', onShow)
    }
  }, [auth, refreshStatus])
  const newBuild = !!OWN_BUILD && !!status?.app.build && status.app.build !== OWN_BUILD

  // Each tab starts at the top (e.g. after confirming a photo the board should be in view).
  useEffect(() => {
    try {
      window.scrollTo({ top: 0 })
    } catch {
      /* old browsers */
    }
  }, [tab])

  useEffect(() => save('gobot.game.v1', game), [game])
  useEffect(() => save('gobot.prefs.v2', prefs), [prefs])

  const presets = status?.presets ?? DEFAULT_PRESETS
  const seconds =
    prefs.presetId === 'custom' ? prefs.customSeconds : presets.find((p) => p.id === prefs.presetId)?.seconds ?? 6

  const gameRef = useRef(game)
  gameRef.current = game
  const prefsRef = useRef({ seconds, auto: prefs.autoAnalyze })
  prefsRef.current = { seconds, auto: prefs.autoAnalyze }
  // Erase: tapping the board takes stones off; no analysis until it is turned off again.
  const [erasing, setErasingState] = useState(false)
  const erasingRef = useRef(erasing)
  erasingRef.current = erasing
  const keepRef = useRef<{ color: Color; moves: number } | null>(null)
  // KataGo is asked only for the moves left in the current round.
  keepRef.current = keepColor ? { color: keepColor, moves: prefs.keepMoves - keepRound.done } : null

  const setGame = useCallback(
    (g: GameState) => {
      analysis.invalidate()
      setGameState(g)
      if (prefsRef.current.auto && !erasingRef.current) analysis.start(g, prefsRef.current.seconds, keepRef.current)
    },
    [analysis.invalidate, analysis.start],
  )

  // Off: analysis comes back as it was (Auto on → analyse the position now). `resume` false when
  // a setGame right after starts it anyway.
  const setErasing = useCallback(
    (on: boolean, resume = true) => {
      setErasingState(on)
      erasingRef.current = on
      if (on) analysis.invalidate()
      else if (resume && prefsRef.current.auto) analysis.start(gameRef.current, prefsRef.current.seconds, keepRef.current)
    },
    [analysis.invalidate, analysis.start],
  )

  const startAnalysis = useCallback(
    () => analysis.start(gameRef.current, prefsRef.current.seconds, keepRef.current),
    [analysis.start],
  )

  // Same position, different question (who moves, how many in a row): analyse it afresh.
  const changeKeep = useCallback(
    (color: Color | null, moves: number) => {
      setKeepColor(color)
      setKeepRound(FRESH_ROUND)
      keepRef.current = color ? { color, moves } : null
      analysis.invalidate()
      if (prefsRef.current.auto && !erasingRef.current)
        analysis.start(gameRef.current, prefsRef.current.seconds, keepRef.current)
    },
    [analysis.invalidate, analysis.start],
  )

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
        <div class="nav-row" hidden={!!auth?.required}>
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
          {tab === 'board' ? (
            <span class={keepColor ? 'to-play keep' : 'to-play'} data-testid="to-play">
              <span class={`dot ${toMove}`} /> {colorName(toMove)} to play
            </span>
          ) : null}
        </div>
        <span class={`chip ${chipClass}`} title={engineChip?.error ?? ''} data-testid="engine-chip">
          {engineChip
            ? engineChip.state === 'ready'
              ? 'KataGo ready'
              : engineChip.error
                ? 'KataGo error'
                : `KataGo ${engineChip.state}`
            : auth?.required
              ? 'signed out'
              : 'offline'}
        </span>
      </header>

      <main>
        {auth === null ? null : auth.required ? (
          <LoginScreen
            info={auth}
            onSignedIn={() => {
              analysis.reconnect()
              refreshAuth()
            }}
            toast={toast}
          />
        ) : tab === 'board' ? (
          <BoardScreen
            game={game}
            setGame={setGame}
            keepColor={keepColor}
            setKeepColor={(c) => changeKeep(c, prefs.keepMoves)}
            prepareKeep={(color, moves) => {
              // No analysis here: called right before a setGame, which starts it.
              setKeepColor(color)
              setKeepRound(FRESH_ROUND)
              setPrefs({ ...prefs, keepMoves: moves })
              keepRef.current = color ? { color, moves } : null
            }}
            keepRound={keepRound}
            advanceKeep={(shown: Point[], played: Point | null) => {
              // Also called right before a setGame, which asks for the moves left.
              const next = advanceRound(keepRound, shown, played, prefs.keepMoves)
              if (!next) {
                // Moves in a row used up: back to alternating, so the opponent plays next.
                setKeepColor(null)
                setKeepRound(FRESH_ROUND)
                keepRef.current = null
                return
              }
              setKeepRound(next)
              if (keepColor) keepRef.current = { color: keepColor, moves: prefs.keepMoves - next.done }
            }}
            keepMoves={prefs.keepMoves}
            erasing={erasing}
            setErasing={setErasing}
            analysis={analysis}
            engine={status?.engine ?? null}
            presets={presets}
            presetId={prefs.presetId}
            customSeconds={prefs.customSeconds}
            showOwnership={prefs.showOwnership}
            autoAnalyze={prefs.autoAnalyze && !erasing}
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
              setKeepColor(null)
              setKeepRound(FRESH_ROUND)
              setErasing(false, false)
              setTab('board')
              analysis.start(g, prefsRef.current.seconds)
            }}
          />
        ) : (
          <SettingsScreen status={status} auth={auth} refreshStatus={refreshStatus} toast={toast} />
        )}
      </main>

      {newBuild ? (
        <div class="update-bar" role="status" data-testid="update-bar">
          <span>A newer version of the app is available.</span>
          <button class="btn small primary" onClick={() => location.reload()}>
            Reload
          </button>
        </div>
      ) : null}

      {toastMsg ? (
        <div class="toast" role="status" onClick={() => setToastMsg(null)} data-testid="toast">
          {toastMsg}
        </div>
      ) : null}
    </div>
  )
}
