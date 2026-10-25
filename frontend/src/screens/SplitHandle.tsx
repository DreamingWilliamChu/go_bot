import { useLayoutEffect, useRef, useState } from 'preact/hooks'
import { loadRaw, save } from '../storage'

// The draggable bar between the two columns of a wide (laptop) layout. It sits as the middle grid item of its
// parent and sets the parent's column shares (--split-l / --split-r); below 900px it is hidden and the columns
// stack. The share is remembered per layout on this device; double-click resets it.

const DEFAULT = 1.25 / 2.25 // the stylesheet's 1.25fr / 1fr
const MIN_LEFT = 280 // px, as in the stylesheet
const MIN_RIGHT = 320
const STEP = 0.02

function apply(el: HTMLElement | null, f: number | null) {
  if (!el) return
  if (f === null) {
    el.style.removeProperty('--split-l')
    el.style.removeProperty('--split-r')
  } else {
    el.style.setProperty('--split-l', `${f}fr`)
    el.style.setProperty('--split-r', `${1 - f}fr`)
  }
}

export function SplitHandle({ name }: { name: string }) {
  const key = `gobot.split.${name}.v1`
  const ref = useRef<HTMLDivElement>(null)
  const [share, setShare] = useState<number | null>(() => {
    const f = loadRaw<number>(key)
    return typeof f === 'number' && f > 0 && f < 1 ? f : null
  })
  const [dragging, setDragging] = useState(false)
  const drag = useRef<{ moved: boolean } | null>(null)

  useLayoutEffect(() => apply(ref.current?.parentElement ?? null, share), [share])

  /** The left share for a bar centred at clientX, kept inside both columns' minimum widths. */
  function shareAt(clientX: number): number | null {
    const bar = ref.current
    const grid = bar?.parentElement
    if (!bar || !grid) return null
    const cs = getComputedStyle(grid)
    const rect = grid.getBoundingClientRect()
    const left = rect.left + grid.clientLeft + parseFloat(cs.paddingLeft)
    const tracks = grid.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - bar.offsetWidth
    if (tracks <= MIN_LEFT + MIN_RIGHT) return null
    const w = Math.min(Math.max(clientX - left - bar.offsetWidth / 2, MIN_LEFT), tracks - MIN_RIGHT)
    return w / tracks
  }

  function commit(f: number | null) {
    setShare(f)
    save(key, f)
  }

  function onPointerDown(e: PointerEvent) {
    if (e.button !== 0) return
    e.preventDefault()
    ref.current?.setPointerCapture(e.pointerId)
    drag.current = { moved: false }
    setDragging(true)
  }

  function onPointerMove(e: PointerEvent) {
    if (!drag.current) return
    const f = shareAt(e.clientX)
    // during the drag only the style changes; the share is stored on release
    if (f !== null) {
      drag.current.moved = true
      apply(ref.current?.parentElement ?? null, f)
    }
  }

  function onPointerUp(e: PointerEvent) {
    const d = drag.current
    if (!d) return
    drag.current = null
    setDragging(false)
    ref.current?.releasePointerCapture(e.pointerId)
    const f = d.moved ? shareAt(e.clientX) : null
    if (f !== null) commit(f)
  }

  function onKeyDown(e: KeyboardEvent) {
    const d = e.key === 'ArrowLeft' ? -STEP : e.key === 'ArrowRight' ? STEP : 0
    if (!d) return
    e.preventDefault()
    const bar = ref.current
    const grid = bar?.parentElement
    const first = grid && bar?.previousElementSibling
    if (!first) return
    // step from where the bar is now, then clamp like a drag
    const at = first.getBoundingClientRect().right + bar.offsetWidth / 2
    const f = shareAt(at + d * grid.clientWidth)
    if (f !== null) commit(f)
  }

  const now = share ?? DEFAULT
  return (
    <div
      ref={ref}
      class={`split-handle${dragging ? ' dragging' : ''}`}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize the two panes"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(now * 100)}
      title="Drag to resize the panes · double-click to reset"
      tabIndex={0}
      data-testid={`split-${name}`}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      onPointerCancel={onPointerUp}
      onDblClick={() => commit(null)}
      onKeyDown={onKeyDown}
    >
      <span class="grip" />
    </div>
  )
}
