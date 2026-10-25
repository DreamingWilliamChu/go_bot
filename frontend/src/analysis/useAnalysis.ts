import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { AnalysisSocket, type AnalysisResult, type SocketMessage, type SocketState } from '../api/client'
import { type Color, type GameState, toApiPosition } from '../game/game'
import type { AnalysisStatus } from './AnalysisPanel'

/** What a bonus-game analysis asks for (docs/BONUS_GO.md §8). */
export type BonusSource = 'katago' | 'katago-net' | 'heuristic' | `net:${string}`

/** Key of the position a result belongs to: standard games by revision, bonus games by id and revision. */
export const stdKey = (revision: number) => `std:${revision}`
export const bonusKey = (gameId: string, revision: number) => `var:${gameId}:${revision}`

/**
 * Owns the analysis websocket. Only the most recent request may update the UI: every response
 * carries the requestId it belongs to, and anything else is dropped. Each request also records
 * the key of the position it was made for (``std:<revision>`` or ``var:<game>:<revision>``), so a
 * result can never be shown on a different position, or on the other kind of game.
 */
export function useAnalysis(onFinished?: () => void) {
  const [status, setStatus] = useState<AnalysisStatus>('idle')
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [connection, setConnection] = useState<SocketState>('connecting')
  const [forKey, setForKey] = useState<string | null>(null)
  const current = useRef<{ requestId: string; key: string } | null>(null)
  const sock = useRef<AnalysisSocket | null>(null)
  const seq = useRef(0)
  const finished = useRef(onFinished)
  finished.current = onFinished

  useEffect(() => {
    const onMsg = (m: SocketMessage) => {
      if (!('requestId' in m)) return
      const cur = current.current
      if (!cur || m.requestId !== cur.requestId) return // stale: superseded or cancelled position
      switch (m.type) {
        case 'engine-starting':
          setStatus('starting')
          break
        case 'partial':
          setResult(m)
          setForKey(cur.key)
          setStatus('running')
          break
        case 'final':
          if (!m.noResults) {
            setResult(m)
            setForKey(cur.key)
          }
          setStatus(m.cancelled ? 'cancelled' : 'done')
          finished.current?.()
          break
        case 'error':
          setError(m.message)
          setStatus('error')
          finished.current?.()
          break
        case 'warning':
          setWarning(m.message)
          break
      }
    }
    const s = new AnalysisSocket(onMsg, setConnection)
    sock.current = s
    return () => s.close()
  }, [])

  const send = useCallback((key: string, target: Record<string, unknown>, params: Record<string, unknown>) => {
    const requestId = `r${++seq.current}-${Date.now() % 100000}`
    current.current = { requestId, key }
    setResult(null)
    setForKey(null)
    setError(null)
    setWarning(null)
    setStatus('running')
    sock.current?.analyze(requestId, target, params)
  }, [])

  /** `keep`: one colour plays `moves` moves in a row (Keep color); KataGo's lines are those moves. */
  const start = useCallback(
    (game: GameState, seconds: number, keep?: { color: Color; moves: number } | null) =>
      send(
        stdKey(game.revision),
        { position: toApiPosition(game, keep?.color) },
        {
          max_time: seconds,
          include_ownership: true,
          report_every: 0.5,
          pv_len: 20,
          same_color_moves: keep ? keep.moves : 1,
        },
      ),
    [send],
  )

  /** A bonus game, analysed by the server from its own record of the game (never a client copy). */
  const startBonus = useCallback(
    (gameId: string, revision: number, source: BonusSource, seconds: number) =>
      send(
        bonusKey(gameId, revision),
        {
          variant: { gameId, revision },
          source: source === 'katago' ? 'katago' : 'variant',
          ...(source === 'katago' ? {} : { evaluator: source }),
        },
        { max_time: seconds, include_ownership: source === 'katago', report_every: 0.5, pv_len: 20 },
      ),
    [send],
  )

  const stop = useCallback(() => {
    const cur = current.current
    if (cur) sock.current?.cancel(cur.requestId)
  }, [])

  /** The position changed: drop the old result and stop any search for it. */
  const invalidate = useCallback(() => {
    const cur = current.current
    if (cur) sock.current?.cancel(cur.requestId)
    current.current = null
    setResult(null)
    setForKey(null)
    setError(null)
    setWarning(null)
    setStatus('idle')
  }, [])

  const reconnect = useCallback(() => sock.current?.reconnect(), [])

  return { status, result, error, warning, connection, forKey, start, startBonus, stop, invalidate, reconnect }
}
