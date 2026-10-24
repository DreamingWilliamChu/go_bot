import { useRef } from 'preact/hooks'
import type { JSX } from 'preact'
import { columnLabel, rowLabel, starPoints } from '../game/coords'

export type Sign = 0 | 1 | -1

export interface CandidateMark {
  x: number
  y: number
  label: string
  rank: number // 0 = best
  sub?: string
}

export interface PvMark {
  x: number
  y: number
  color: 'B' | 'W'
  n: number
  ghost?: boolean // a prediction, drawn see-through
}

export interface BoardViewProps {
  size: number
  signMap: Sign[][] // [y][x]
  lastMove?: [number, number] | null
  candidates?: CandidateMark[]
  pv?: PvMark[]
  ownership?: number[] | null // black-positive, row-major from top-left
  uncertain?: Set<string> // "x,y"
  highlight?: Set<string> // e.g. dead groups in setup
  ghost?: 'B' | 'W' | null
  showCoords?: boolean
  dimStones?: boolean
  onPoint?: (x: number, y: number) => void
  ariaLabel?: string
}

const PAD = 0.95
const RANK_COLORS = ['#1e7bd8', '#2f9e44', '#e8590c']

export function BoardView(props: BoardViewProps) {
  const { size, signMap } = props
  const svgRef = useRef<SVGSVGElement>(null)
  const down = useRef<{ x: number; y: number } | null>(null)
  const span = size - 1 + 2 * PAD
  const c = (i: number) => PAD + i

  function toPoint(ev: PointerEvent): [number, number] | null {
    const svg = svgRef.current
    if (!svg) return null
    const ctm = svg.getScreenCTM()
    if (!ctm) return null
    const pt = new DOMPoint(ev.clientX, ev.clientY).matrixTransform(ctm.inverse())
    const x = Math.round(pt.x - PAD)
    const y = Math.round(pt.y - PAD)
    if (x < 0 || y < 0 || x >= size || y >= size) return null
    if (Math.hypot(pt.x - PAD - x, pt.y - PAD - y) > 0.62) return null
    return [x, y]
  }

  const onPointerDown = (ev: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    down.current = { x: ev.clientX, y: ev.clientY }
  }
  const onPointerUp = (ev: JSX.TargetedPointerEvent<SVGSVGElement>) => {
    if (!props.onPoint) return
    const d = down.current
    down.current = null
    if (d && Math.hypot(ev.clientX - d.x, ev.clientY - d.y) > 12) return // a scroll/drag, not a tap
    const p = toPoint(ev as unknown as PointerEvent)
    if (p) props.onPoint(p[0], p[1])
  }

  const lines: JSX.Element[] = []
  for (let i = 0; i < size; i++) {
    lines.push(<line key={`h${i}`} x1={c(0)} y1={c(i)} x2={c(size - 1)} y2={c(i)} />)
    lines.push(<line key={`v${i}`} x1={c(i)} y1={c(0)} x2={c(i)} y2={c(size - 1)} />)
  }

  const coords: JSX.Element[] = []
  if (props.showCoords !== false) {
    for (let i = 0; i < size; i++) {
      coords.push(
        <text key={`ct${i}`} x={c(i)} y={PAD * 0.42} class="coord">
          {columnLabel(i)}
        </text>,
        <text key={`cb${i}`} x={c(i)} y={span - PAD * 0.28} class="coord">
          {columnLabel(i)}
        </text>,
        <text key={`rl${i}`} x={PAD * 0.4} y={c(i) + 0.16} class="coord">
          {rowLabel(i, size)}
        </text>,
        <text key={`rr${i}`} x={span - PAD * 0.4} y={c(i) + 0.16} class="coord">
          {rowLabel(i, size)}
        </text>,
      )
    }
  }

  const own: JSX.Element[] = []
  const dead: JSX.Element[] = []
  if (props.ownership && props.ownership.length === size * size) {
    for (let y = 0; y < size; y++)
      for (let x = 0; x < size; x++) {
        const v = props.ownership[y * size + x]
        const s = signMap[y]?.[x] ?? 0
        if (Math.abs(v) < 0.08) continue
        const black = v > 0
        if (s !== 0) {
          // stone predicted to die: small square of the owner's colour on top
          if ((s > 0) !== black && Math.abs(v) > 0.35)
            dead.push(
              <rect
                key={`od${x},${y}`}
                x={c(x) - 0.2}
                y={c(y) - 0.2}
                width={0.4}
                height={0.4}
                class={black ? 'own-dead-b' : 'own-dead-w'}
                opacity={Math.min(1, Math.abs(v))}
              />,
            )
          continue
        }
        own.push(
          <rect
            key={`o${x},${y}`}
            x={c(x) - 0.5}
            y={c(y) - 0.5}
            width={1}
            height={1}
            fill={black ? '#000' : '#fff'}
            opacity={Math.abs(v) * 0.55}
          />,
        )
      }
  }

  const stones: JSX.Element[] = []
  for (let y = 0; y < size; y++)
    for (let x = 0; x < size; x++) {
      const s = signMap[y]?.[x] ?? 0
      if (!s) continue
      stones.push(
        <circle
          key={`s${x},${y}`}
          cx={c(x)}
          cy={c(y)}
          r={0.47}
          class={s > 0 ? 'stone-b' : 'stone-w'}
          opacity={props.dimStones ? 0.45 : 1}
        />,
      )
    }

  const marks: JSX.Element[] = []
  if (props.lastMove) {
    const [x, y] = props.lastMove
    const s = signMap[y]?.[x] ?? 0
    marks.push(<circle key="last" cx={c(x)} cy={c(y)} r={0.2} class={s > 0 ? 'last-on-b' : 'last-on-w'} />)
  }
  for (const k of props.highlight ?? []) {
    const [x, y] = k.split(',').map(Number)
    marks.push(<rect key={`hl${k}`} x={c(x) - 0.48} y={c(y) - 0.48} width={0.96} height={0.96} class="hl" />)
  }
  for (const k of props.uncertain ?? []) {
    const [x, y] = k.split(',').map(Number)
    marks.push(
      <g key={`u${k}`} class="uncertain">
        <circle cx={c(x)} cy={c(y)} r={0.5} />
        <text x={c(x) + 0.32} y={c(y) - 0.22}>?</text>
      </g>,
    )
  }
  for (const m of props.candidates ?? []) {
    const col = RANK_COLORS[m.rank] ?? '#666'
    marks.push(
      <g key={`cand${m.x},${m.y}`} class="cand">
        <circle cx={c(m.x)} cy={c(m.y)} r={0.46} fill={col} opacity={0.88} />
        <text x={c(m.x)} y={c(m.y) + (m.sub ? -0.02 : 0.17)} class="cand-label">
          {m.label}
        </text>
        {m.sub ? (
          <text x={c(m.x)} y={c(m.y) + 0.3} class="cand-sub">
            {m.sub}
          </text>
        ) : null}
      </g>,
    )
  }
  for (const p of props.pv ?? []) {
    marks.push(
      <g key={`pv${p.n}`}>
        <circle
          cx={c(p.x)}
          cy={c(p.y)}
          r={0.47}
          class={`${p.color === 'B' ? 'stone-b' : 'stone-w'} ${p.ghost ? 'pv ghost' : 'pv'}`}
        />
        <text x={c(p.x)} y={c(p.y) + 0.15} class={p.color === 'B' ? 'pv-n-b' : 'pv-n-w'}>
          {p.n}
        </text>
      </g>,
    )
  }

  const stars = starPoints(size).map(([x, y]) => <circle key={`st${x},${y}`} cx={c(x)} cy={c(y)} r={0.09} class="star" />)

  return (
    <svg
      ref={svgRef}
      class="board"
      viewBox={`0 0 ${span} ${span}`}
      role="img"
      aria-label={props.ariaLabel ?? `${size} by ${size} Go board`}
      onPointerDown={onPointerDown}
      onPointerUp={onPointerUp}
      // Safari ignores touch-action inside an SVG, so two quick taps zoomed the page. Taps are
      // handled on pointerup above; cancelling the touch's default only drops the zoom and the click.
      onTouchEnd={(ev) => ev.preventDefault()}
      data-size={size}
    >
      <defs>
        <radialGradient id="gb" cx="0.35" cy="0.3" r="0.75">
          <stop offset="0" stop-color="#5a5a5a" />
          <stop offset="1" stop-color="#0b0b0b" />
        </radialGradient>
        <radialGradient id="gw" cx="0.35" cy="0.3" r="0.8">
          <stop offset="0" stop-color="#ffffff" />
          <stop offset="1" stop-color="#d6d4cc" />
        </radialGradient>
      </defs>
      <rect x={0} y={0} width={span} height={span} class="wood" rx={0.25} />
      <g class="grid">{lines}</g>
      {stars}
      <g>{own}</g>
      <g>{stones}</g>
      <g>{dead}</g>
      <g>{marks}</g>
      <g>{coords}</g>
    </svg>
  )
}
