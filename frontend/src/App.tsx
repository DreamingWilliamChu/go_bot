import { useCallback, useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { api, ApiError, type AuthInfo, type Preset, type StatusResponse } from './api/client'
import { type BonusSource, useAnalysis } from './analysis/useAnalysis'
import { colorName } from './analysis/format'
import { type Color, type GameState, type Setup, type Stone, collapseToSetup, currentBoard, newGame, nextPlayer } from './game/game'
import { FRESH_ROUND, type KeepRound, type Point, advanceRound } from './game/keepRound'
import { BoardScreen } from './screens/BoardScreen'
import { type SourceOption, VariantBoardScreen } from './screens/VariantBoardScreen'
import { variantApi } from './variant/api'
import { BonusEntryDialog } from './variant/BonusEntryDialog'
import { BonusStateDialog } from './variant/BonusStateDialog'
import { DEFAULT_BONUS_PREFS, type BonusPrefs, type BonusStateInput, type VariantView } from './variant/types'
import { useVariantGame } from './variant/useVariantGame'
import { LoginScreen } from './screens/LoginScreen'
import { PhotoScreen } from './screens/PhotoScreen'
import { SettingsScreen } from './screens/SettingsScreen'
import { load, loadRaw, save } from './storage'

type Tab = 'board' | 'photo' | 'settings'

// Which game the Board tab shows. The standard game (gobot.game.v1) is never changed by Bonus Mode.
type Session = { kind: 'standard' } | { kind: 'variant'; gameId: string }

function loadSession(): Session {
  const s = loadRaw<Session>('gobot.session.v1')
  return s && s.kind === 'variant' && typeof s.gameId === 'string' ? s : { kind: 'standard' }
}

// v2 made 3 bonuses each the default. v1 saved the whole settings object, so a stored 2/2 is the old
// default rather than a choice: carry the other settings over and start on the new default once.
function loadBonusPrefs(): BonusPrefs {
  if (loadRaw<BonusPrefs>('gobot.bonusPrefs.v2')) return load('gobot.bonusPrefs.v2', DEFAULT_BONUS_PREFS)
  const old = load('gobot.bonusPrefs.v1', DEFAULT_BONUS_PREFS)
  const wasDefault = old.blackBonuses === 2 && old.whiteBonuses === 2
  return wasDefault ? { ...old, blackBonuses: 3, whiteBonuses: 3 } : old
}

// Settings saved while the judge trigger was called "choice" ("you choose").
function withTrigger(p: BonusPrefs): BonusPrefs {
  return (p.trigger as string) === 'choice' ? { ...p, trigger: 'judge' } : p
}

// A position without history waiting for its bonus state (photo, or a branch of the standard game).
interface PendingImport {
  setup: Setup
  provenance: 'photo' | 'branch'
}

// The script this page was loaded from; null in `npm run dev` (no built assets there).
const OWN_BUILD =
  document.querySelector<HTMLScriptElement>('script[type="module"][src*="/assets/"]')?.getAttribute('src') ?? null

interface Prefs {
  presetId: string
  customSeconds: number
  showOwnership: boolean
  autoAnalyze: boolean
  keepMoves: number // Keep color: how many moves ahead the analysis shows (look-ahead window)
  bonusSource: BonusSource // analysis source for bonus games
}

interface VariantModelInfo {
  id: string
  label: string
  sizes: number[]
}

interface EvaluatorsResponse {
  sources: { id: string; label: string; available: boolean }[]
  models: VariantModelInfo[]
}

const DEFAULT_PRESETS: Preset[] = [
  { id: 'quick', label: 'Quick', seconds: 2 },
  { id: 'standard', label: 'Standard', seconds: 6 },
  { id: 'deep', label: 'Deep', seconds: 20 },
]
const DEFAULT_PREFS: Prefs = {
  presetId: 'deep',
  customSeconds: 10,
  showOwnership: false,
  autoAnalyze: true,
  keepMoves: 3,
  bonusSource: 'katago-net',
}

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
  // Keep color: while set, every move is played by this colour instead of alternating, until the
  // button is tapped again (a manual lock without a move limit; kept across reloads).
  const [keepColor, setKeepColorState] = useState<Color | null>(() => {
    const k = loadRaw<{ color: Color | null }>('gobot.keep.v1')?.color
    return k === 'B' || k === 'W' ? k : null
  })
  const setKeepColor = useCallback((c: Color | null) => {
    setKeepColorState(c)
    save('gobot.keep.v1', { color: c })
  }, [])
  const [keepRound, setKeepRound] = useState<KeepRound>(FRESH_ROUND)
  const toMove = keepColor ?? nextPlayer(game)
  const [prefs, setPrefs] = useState<Prefs>(loadPrefs)
  const [status, setStatus] = useState<StatusResponse | null>(null)
  const [toastMsg, setToastMsg] = useState<string | null>(null)
  const [auth, setAuth] = useState<AuthInfo | null>(null)
  const toastTimer = useRef<number | undefined>(undefined)
  const [session, setSessionState] = useState<Session>(loadSession)
  const [bonusPrefs, setBonusPrefsState] = useState<BonusPrefs>(() => withTrigger(loadBonusPrefs()))
  const [lastBonusGame, setLastBonusGame] = useState<string | null>(() => loadRaw<{ id: string }>('gobot.bonusLast.v1')?.id ?? null)
  const [entryOpen, setEntryOpen] = useState(false)
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null)
  const [bonusBusy, setBonusBusy] = useState(false)

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

  const setSession = useCallback((s: Session) => {
    setSessionState(s)
    save('gobot.session.v1', s)
    if (s.kind === 'variant') {
      setLastBonusGame(s.gameId)
      save('gobot.bonusLast.v1', { id: s.gameId })
    }
  }, [])
  const setBonusPrefs = useCallback((p: BonusPrefs) => {
    setBonusPrefsState(p)
    save('gobot.bonusPrefs.v2', p)
  }, [])
  const variant = useVariantGame(session.kind === 'variant' ? session.gameId : null, toast, () => {
    setSession({ kind: 'standard' })
    setLastBonusGame(null)
    save('gobot.bonusLast.v1', null)
  })
  const inBonus = session.kind === 'variant'
  const [evaluators, setEvaluators] = useState<EvaluatorsResponse | null>(null)
  useEffect(() => {
    if (!inBonus || evaluators || !auth || auth.required) return
    api
      .get<EvaluatorsResponse>('/api/variant/evaluators')
      .then(setEvaluators)
      .catch(() => undefined)
  }, [inBonus, evaluators, auth])

  // Bonus game created or opened: show it.
  const enterBonus = useCallback(
    (v: VariantView) => {
      analysis.invalidate()
      variant.setView(v)
      setSession({ kind: 'variant', gameId: v.gameId })
      setEntryOpen(false)
      setPendingImport(null)
      setTab('board')
    },
    [analysis.invalidate, variant.setView, setSession],
  )

  const runBonus = useCallback(
    async (f: () => Promise<VariantView>) => {
      setBonusBusy(true)
      try {
        enterBonus(await f())
      } catch (e) {
        toast((e as Error).message)
      } finally {
        setBonusBusy(false)
      }
    },
    [enterBonus, toast],
  )

  const startImport = useCallback(
    (pi: PendingImport, st: BonusStateInput) => {
      const prefs = { ...bonusPrefs, size: pi.setup.size, komi: pi.setup.komi }
      const stones: Stone[] = pi.setup.initialStones
      void runBonus(() => variantApi.importPosition(prefs, stones, st, pi.provenance))
    },
    [bonusPrefs, runBonus],
  )

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

  // Analysis sources for the current bonus game; a trained network only for sizes it supports.
  const bonusSize = variant.view?.state.size ?? 0
  const sources: SourceOption[] = useMemo(() => {
    const has = (id: string) => evaluators?.sources.find((s) => s.id === id)?.available ?? false
    const list: SourceOption[] = [
      { id: 'katago', label: 'KataGo (standard Go)', available: has('katago'), why: 'KataGo is not available' },
      { id: 'katago-net', label: 'Bonus search · KataGo net', available: has('katago-net'), why: 'KataGo is not available' },
      { id: 'heuristic', label: 'Bonus search · heuristic', available: true },
    ]
    for (const m of evaluators?.models ?? [])
      list.push({
        id: `net:${m.id}`,
        label: `Bonus search · ${m.label}`,
        available: m.sizes.includes(bonusSize),
        why: `Trained for ${m.sizes.map((n) => `${n}×${n}`).join(', ')} only`,
      })
    return list
  }, [evaluators, bonusSize])
  const bonusSource: BonusSource = sources.find((o) => o.id === prefs.bonusSource && o.available)
    ? prefs.bonusSource
    : (sources.find((o) => o.available && o.id !== 'katago')?.id ?? 'heuristic')

  // Bonus games: analyse each new position (Auto) with the chosen source; leaving Bonus Mode stops it.
  const bonusAsked = useRef<string | null>(null)
  const startBonus = useCallback(() => {
    const v = variant.view
    if (!v || v.state.phase === 'terminal') return
    analysis.startBonus(v.gameId, v.revision, bonusSource, seconds)
  }, [variant.view, analysis.startBonus, bonusSource, seconds])
  useEffect(() => {
    const v = variant.view
    if (!inBonus) {
      if (bonusAsked.current !== null) analysis.invalidate()
      bonusAsked.current = null
      return
    }
    if (!v || !evaluators) return
    const key = `${v.gameId}:${v.revision}:${bonusSource}`
    if (bonusAsked.current === key) return
    bonusAsked.current = key
    analysis.invalidate()
    if (prefs.autoAnalyze && v.state.phase !== 'terminal') startBonus()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [inBonus, variant.view?.gameId, variant.view?.revision, bonusSource, evaluators])

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
          {tab === 'board' && inBonus && variant.view ? (
            <span class={variant.view.state.bonusActive ? 'to-play keep' : 'to-play'} data-testid="to-play">
              <span class={`dot ${variant.view.state.toMove}`} /> {colorName(variant.view.state.toMove)} to play
              {variant.view.state.bonusActive ? (
                <span class="lock-note">
                  {' '}
                  · bonus {variant.view.state.turnMoves - variant.view.state.movesLeft + 1}/{variant.view.state.turnMoves}
                </span>
              ) : null}
            </span>
          ) : tab === 'board' && !inBonus ? (
            <span class={keepColor ? 'to-play keep' : 'to-play'} data-testid="to-play">
              <span class={`dot ${toMove}`} /> {colorName(toMove)} to play
              {keepColor ? <span class="lock-note"> · kept (manual)</span> : null}
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
        ) : tab === 'board' && inBonus ? (
          variant.view ? (
            <VariantBoardScreen
              view={variant.view}
              busy={variant.busy || bonusBusy}
              act={variant.act}
              prefs={bonusPrefs}
              setPrefs={setBonusPrefs}
              onNewGame={() => void runBonus(() => variantApi.create(bonusPrefs))}
              onLeave={() => {
                if (!confirm('Turn Bonus Mode off? The bonus game is kept (Bonus Mode → Resume), and your standard game comes back.'))
                  return
                setSession({ kind: 'standard' })
              }}
              onOpenRecord={(file) =>
                void runBonus(async () => variantApi.open(JSON.parse(await file.text()) as unknown))
              }
              toast={toast}
              analysis={analysis}
              engine={status?.engine ?? null}
              presets={presets}
              presetId={prefs.presetId}
              customSeconds={prefs.customSeconds}
              autoAnalyze={prefs.autoAnalyze}
              onPreset={(id) => setPrefs({ ...prefs, presetId: id })}
              onCustomSeconds={(sec) => setPrefs({ ...prefs, customSeconds: sec })}
              onAnalyze={startBonus}
              onStop={analysis.stop}
              onToggleAuto={() => setPrefs({ ...prefs, autoAnalyze: !prefs.autoAnalyze })}
              source={bonusSource}
              sources={sources}
              onSource={(src) => setPrefs({ ...prefs, bonusSource: src })}
            />
          ) : (
            <div class="panel">
              <p>Loading the bonus game…</p>
            </div>
          )
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
              // Also called right before a setGame, which asks for the moves left in the round. The
              // lock stays on (no move limit); only the look-ahead round starts again.
              const next = advanceRound(keepRound, shown, played, prefs.keepMoves)
              setKeepRound(next)
              if (keepColor) keepRef.current = { color: keepColor, moves: prefs.keepMoves - next.done }
            }}
            keepMoves={prefs.keepMoves}
            onBonusMode={() => setEntryOpen(true)}
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
              if (inBonus) {
                // A photo cannot show the bonus history: ask for the bonus state first.
                setPendingImport({ setup, provenance: 'photo' })
                return
              }
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

      {entryOpen ? (
        <BonusEntryDialog
          prefs={bonusPrefs}
          setPrefs={setBonusPrefs}
          canResume={!!lastBonusGame}
          canBranch={game.moves.length > 0 || game.setup.initialStones.length > 0}
          busy={bonusBusy}
          onResume={() => {
            if (!lastBonusGame) return
            analysis.invalidate()
            setEntryOpen(false)
            setSession({ kind: 'variant', gameId: lastBonusGame })
          }}
          onNew={() => void runBonus(() => variantApi.create(bonusPrefs))}
          onBranch={() => {
            setEntryOpen(false)
            setPendingImport({ setup: { ...collapseToSetup(game), initialPlayer: toMove }, provenance: 'branch' })
          }}
          onCancel={() => setEntryOpen(false)}
        />
      ) : null}

      {pendingImport ? (
        <BonusStateDialog
          title={pendingImport.provenance === 'photo' ? 'Bonus state of the photographed position' : 'Bonus analysis of this position'}
          stones={pendingImport.setup.initialStones}
          toMove={pendingImport.setup.initialPlayer}
          prefs={{ ...bonusPrefs, size: pendingImport.setup.size, komi: pendingImport.setup.komi }}
          provenance={pendingImport.provenance}
          busy={bonusBusy}
          onStart={(st) => startImport(pendingImport, st)}
          onCancel={() => setPendingImport(null)}
          onStandard={
            pendingImport.provenance === 'photo'
              ? () => {
                  // Analyse the photo as ordinary Go (the previous behaviour), leaving Bonus Mode.
                  const pi = pendingImport
                  setPendingImport(null)
                  setSession({ kind: 'standard' })
                  const g: GameState = { setup: pi.setup, moves: [], redo: [], revision: game.revision + 1 }
                  analysis.invalidate()
                  setGameState(g)
                  setKeepColor(null)
                  setKeepRound(FRESH_ROUND)
                  setErasing(false, false)
                  setTab('board')
                  analysis.start(g, prefsRef.current.seconds)
                }
              : undefined
          }
        />
      ) : null}

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
