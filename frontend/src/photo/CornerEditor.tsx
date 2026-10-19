import { useMemo, useRef, useState } from 'preact/hooks'
import type { JSX } from 'preact'
import { apply, gridToImage, isConvex, type Pt } from './homography'

interface Props {
  url: string
  width: number
  height: number
  size: number
  corners: Pt[]
  onChange?: (c: Pt[]) => void
  editable: boolean
  stones?: { x: number; y: number; color: 1 | -1 }[] // photo-space grid points
  uncertain?: [number, number][]
}

const LABELS = ['TL', 'TR', 'BR', 'BL']

/** Photo with the fitted grid drawn on top; in edit mode the 4 grid corners can be dragged. */
export function CornerEditor(p: Props) {
  const svgRef = useRef<SVGSVGElement>(null)
  const stageRef = useRef<HTMLDivElement>(null)
  const [drag, setDrag] = useState<number | null>(null)
  const [loupe, setLoupe] = useState<{ left: number; top: number; bg: string } | null>(null)
  const r = Math.max(p.width, p.height) * 0.018

  const H = useMemo(() => {
    if (!isConvex(p.corners)) return null
    try {
      return gridToImage(p.corners, p.size)
    } catch {
      return null
    }
  }, [p.corners, p.size])

  const lines: JSX.Element[] = []
  if (H) {
    for (let i = 0; i < p.size; i++) {
      const a = apply(H, [i, 0])
      const b = apply(H, [i, p.size - 1])
      const c = apply(H, [0, i])
      const d = apply(H, [p.size - 1, i])
      lines.push(<line key={`v${i}`} x1={a[0]} y1={a[1]} x2={b[0]} y2={b[1]} class="gridline" vector-effect="non-scaling-stroke" />)
      lines.push(<line key={`h${i}`} x1={c[0]} y1={c[1]} x2={d[0]} y2={d[1]} class="gridline" vector-effect="non-scaling-stroke" />)
    }
  }
  const marks: JSX.Element[] = []
  if (H && !p.editable) {
    const cellPx = Math.hypot(p.corners[1][0] - p.corners[0][0], p.corners[1][1] - p.corners[0][1]) / (p.size - 1)
    for (const s of p.stones ?? []) {
      const [x, y] = apply(H, [s.x, s.y])
      marks.push(<circle key={`s${s.x},${s.y}`} cx={x} cy={y} r={cellPx * 0.22} class={s.color === 1 ? 'pt-b' : 'pt-w'} vector-effect="non-scaling-stroke" />)
    }
    for (const [ux, uy] of p.uncertain ?? []) {
      const [x, y] = apply(H, [ux, uy])
      marks.push(<circle key={`u${ux},${uy}`} cx={x} cy={y} r={cellPx * 0.45} class="pt-u" vector-effect="non-scaling-stroke" />)
    }
  }

  function svgPoint(ev: PointerEvent): Pt | null {
    const svg = svgRef.current
    const ctm = svg?.getScreenCTM()
    if (!svg || !ctm) return null
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse())
    return [Math.min(p.width, Math.max(0, pt.x)), Math.min(p.height, Math.max(0, pt.y))]
  }

  function updateLoupe(ev: PointerEvent, pt: Pt) {
    const stage = stageRef.current
    if (!stage) return
    const rect = stage.getBoundingClientRect()
    const scaleDisp = rect.width / p.width
    const zoom = 3
    const bgW = rect.width * zoom
    const bgH = rect.height * zoom
    const lx = ev.clientX - rect.left
    const ly = ev.clientY - rect.top
    const size = 120
    const bx = -(pt[0] * scaleDisp * zoom - size / 2)
    const by = -(pt[1] * scaleDisp * zoom - size / 2)
    const top = ly - size - 40 < 0 ? ly + 40 : ly - size - 40
    setLoupe({
      left: Math.min(rect.width - size, Math.max(0, lx - size / 2)),
      top,
      bg: `url(${p.url}) ${bx}px ${by}px / ${bgW}px ${bgH}px no-repeat`,
    })
  }

  const onDown = (i: number) => (ev: JSX.TargetedPointerEvent<SVGCircleElement>) => {
    if (!p.editable) return
    ev.preventDefault()
    ;(ev.currentTarget as Element).setPointerCapture(ev.pointerId)
    setDrag(i)
    const pt = svgPoint(ev as unknown as PointerEvent)
    if (pt) updateLoupe(ev as unknown as PointerEvent, pt)
  }
  const onMove = (ev: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    if (drag === null || !p.onChange) return
    const pt = svgPoint(ev as unknown as PointerEvent)
    if (!pt) return
    const next = p.corners.map((c, j) => (j === drag ? pt : c)) as Pt[]
    p.onChange(next)
    updateLoupe(ev as unknown as PointerEvent, pt)
  }
  const onUp = () => {
    setDrag(null)
    setLoupe(null)
  }

  return (
    <div class="photo-stage" ref={stageRef} data-testid="photo-stage">
      <img src={p.url} width={p.width} height={p.height} alt="Board photo" />
      <svg
        ref={svgRef}
        viewBox={`0 0 ${p.width} ${p.height}`}
        preserveAspectRatio="none"
        onPointerMove={onMove}
        onPointerUp={onUp}
        onPointerCancel={onUp}
      >
        <g>{lines}</g>
        <g>{marks}</g>
        {p.editable ? (
          <>
            <polygon points={p.corners.map((c) => c.join(',')).join(' ')} class="quad" vector-effect="non-scaling-stroke" />
            {p.corners.map((c, i) => (
              <g key={i}>
                <circle
                  cx={c[0]}
                  cy={c[1]}
                  r={r * 2.6}
                  fill="transparent"
                  style={{ touchAction: 'none', cursor: 'grab' }}
                  onPointerDown={onDown(i)}
                  data-testid={`corner-${i}`}
                  aria-label={`Corner ${LABELS[i]}`}
                />
                <circle cx={c[0]} cy={c[1]} r={r} class="handle" vector-effect="non-scaling-stroke" pointer-events="none" />
              </g>
            ))}
          </>
        ) : null}
      </svg>
      {loupe ? <div class="loupe" style={{ left: `${loupe.left}px`, top: `${loupe.top}px`, background: loupe.bg }} /> : null}
    </div>
  )
}
