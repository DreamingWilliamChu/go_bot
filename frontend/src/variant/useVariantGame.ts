import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import { StaleGame, type VariantAction, variantApi } from './api'
import type { VariantView } from './types'

/**
 * The current bonus game as the server last described it. Actions are sent one at a time with the
 * revision they were made on; a reply for a game that is no longer current is ignored.
 */
export function useVariantGame(gameId: string | null, toast: (m: string) => void, onGone: () => void) {
  const [view, setViewState] = useState<VariantView | null>(null)
  const [busy, setBusy] = useState(false)
  const viewRef = useRef<VariantView | null>(null)
  const busyRef = useRef(false)
  const idRef = useRef(gameId)
  idRef.current = gameId

  const setView = useCallback((v: VariantView | null) => {
    viewRef.current = v
    setViewState(v)
  }, [])

  useEffect(() => {
    if (!gameId) {
      setView(null)
      return
    }
    if (viewRef.current?.gameId === gameId) return
    setView(null)
    variantApi
      .get(gameId)
      .then((v) => {
        if (idRef.current === gameId) setView(v)
      })
      .catch((e: Error) => {
        if (idRef.current !== gameId) return
        toast(e.message)
        onGone()
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gameId])

  /** Resolves to the new view, or null if nothing changed (busy, error, stale). */
  const act = useCallback(
    async (action: VariantAction): Promise<VariantView | null> => {
      const v = viewRef.current
      if (!v || busyRef.current) return null
      busyRef.current = true
      setBusy(true)
      try {
        const next = await variantApi.act(v, action)
        if (idRef.current === next.gameId) setView(next)
        return next
      } catch (e) {
        if (e instanceof StaleGame) {
          if (idRef.current === e.game.gameId) setView(e.game)
          toast(e.message)
        } else toast((e as Error).message)
        return null
      } finally {
        busyRef.current = false
        setBusy(false)
      }
    },
    [setView, toast],
  )

  return { view, setView, busy, act }
}
