import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { AnalysisSocket, type AnalysisResult, type SocketMessage, type SocketState } from '../api/client'
import { type GameState, toApiPosition } from '../game/game'
import type { AnalysisStatus } from './AnalysisPanel'

/**
 * Owns the analysis websocket. Only the most recent request may update the UI: every response
 * carries the requestId it belongs to, and anything else is dropped. Each request also records
 * the game revision it was made for, so results can never be shown on a different position.
 */
export function useAnalysis(onFinished?: () => void) {
  const [status, setStatus] = useState<AnalysisStatus>('idle')
  const [result, setResult] = useState<AnalysisResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [warning, setWarning] = useState<string | null>(null)
  const [connection, setConnection] = useState<SocketState>('connecting')
  const [forRevision, setForRevision] = useState<number | null>(null)
  const current = useRef<{ requestId: string; revision: number } | null>(null)
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
          setForRevision(cur.revision)
          setStatus('running')
          break
        case 'final':
          if (!m.noResults) {
            setResult(m)
            setForRevision(cur.revision)
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

  const start = useCallback((game: GameState, seconds: number) => {
    const requestId = `r${++seq.current}-${Date.now() % 100000}`
    current.current = { requestId, revision: game.revision }
    setResult(null)
    setForRevision(null)
    setError(null)
    setWarning(null)
    setStatus('running')
    sock.current?.analyze(requestId, toApiPosition(game), {
      max_time: seconds,
      include_ownership: true,
      report_every: 0.5,
      pv_len: 20,
    })
  }, [])

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
    setForRevision(null)
    setError(null)
    setWarning(null)
    setStatus('idle')
  }, [])

  const reconnect = useCallback(() => sock.current?.reconnect(), [])

  return { status, result, error, warning, connection, forRevision, start, stop, invalidate, reconnect }
}
