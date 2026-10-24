import { useMemo, useRef, useState } from 'preact/hooks'
import { api, type StatusResponse } from '../api/client'
import { colorName, leadText } from '../analysis/format'
import { BoardView, type Sign } from '../board/BoardView'
import { SIZES, type BoardSize } from '../game/coords'
import { countSettled } from '../game/count'
import { type Color, RULES, type Rules, type Setup, deadGroupsInSetup } from '../game/game'
import { GameSettingsForm } from '../game/GameSettingsForm'
import { CornerEditor } from '../photo/CornerEditor'
import { defaultCorners, isConvex, type Pt } from '../photo/homography'
import { LiveCamera } from '../photo/LiveCamera'
import { IDENTITY, type Orient, boardToDisplay, mirror, rotateCW, toDisplay, toPhoto } from '../photo/transform'

interface Props {
  defaults: Setup
  status: StatusResponse | null
  toast: (m: string) => void
  onConfirmed: (setup: Setup) => void
}

interface Photo {
  id: string
  url: string
  width: number
  height: number
  warnings: string[]
}

interface Recognition {
  status: 'ok' | 'needs_corners'
  size: number
  corners: Pt[] | null
  cornerSource: string | null
  gridScore: number | null
  board: number[][] | null
  confidence: number[][] | null
  uncertain: [number, number][]
  messages: string[]
  sizeSuggestion: number | null
  detectors: string[]
  timings: Record<string, number>
}

type Step = 'capture' | 'working' | 'corners' | 'review'

export function PhotoScreen({ defaults, status, toast, onConfirmed }: Props) {
  const [step, setStep] = useState<Step>('capture')
  const [size, setSize] = useState<BoardSize>(defaults.size)
  const [photo, setPhoto] = useState<Photo | null>(null)
  const [rec, setRec] = useState<Recognition | null>(null)
  const [corners, setCorners] = useState<Pt[]>([])
  const [board, setBoard] = useState<number[][] | null>(null) // photo orientation
  const [history, setHistory] = useState<number[][][]>([])
  const [uncertain, setUncertain] = useState<Set<string>>(new Set()) // photo-space "x,y"
  const [orient, setOrient] = useState<Orient>(IDENTITY)
  const [player, setPlayer] = useState<Color | null>(null)
  const [rules, setRules] = useState<Rules>(defaults.rules)
  const [komi, setKomi] = useState<number>(defaults.komi)
  const [saveCase, setSaveCase] = useState(false)
  const [busyMsg, setBusyMsg] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [live, setLive] = useState(false)
  const takeRef = useRef<HTMLInputElement>(null)
  const pickRef = useRef<HTMLInputElement>(null)

  const secure = typeof window !== 'undefined' && window.isSecureContext && !!navigator.mediaDevices?.getUserMedia

  async function upload(file: Blob, name = 'photo.jpg') {
    setError(null)
    setStep('working')
    setBusyMsg('Uploading photo to the Mac…')
    try {
      const fd = new FormData()
      fd.append('file', file, name)
      const p = await api.upload<Photo>('/api/photos', fd)
      setPhoto(p)
      await recognize(p, size, null)
    } catch (e) {
      setError((e as Error).message)
      setStep('capture')
    }
  }

  async function recognize(p: Photo, n: number, manual: Pt[] | null) {
    setStep('working')
    setBusyMsg(manual ? 'Reading stones with your corners…' : 'Finding the board and stones…')
    try {
      const r = await api.post<Recognition>(`/api/photos/${p.id}/recognize`, { size: n, corners: manual })
      setRec(r)
      if (r.status === 'ok' && r.board && r.corners) {
        setCorners(r.corners)
        setBoard(r.board)
        setHistory([])
        setUncertain(new Set(r.uncertain.map(([x, y]) => `${x},${y}`)))
        setOrient(IDENTITY)
        setPlayer(null)
        setStep('review')
      } else {
        setCorners(r.corners && isConvex(r.corners) ? r.corners : defaultCorners(p.width, p.height))
        setStep('corners')
      }
    } catch (e) {
      setError((e as Error).message)
      setStep(photo || p ? 'corners' : 'capture')
      if (!corners.length) setCorners(defaultCorners(p.width, p.height))
    }
  }

  const n = rec?.size ?? size
  const displayBoard: Sign[][] | null = useMemo(
    () => (board ? (boardToDisplay(board, n, orient) as Sign[][]) : null),
    [board, n, orient],
  )
  const displayUncertain = useMemo(() => {
    const s = new Set<string>()
    for (const k of uncertain) {
      const [x, y] = k.split(',').map(Number)
      const [dx, dy] = toDisplay([x, y], n, orient)
      s.add(`${dx},${dy}`)
    }
    return s
  }, [uncertain, n, orient])

  const setupDraft: Setup | null = useMemo(() => {
    if (!displayBoard) return null
    const stones = []
    for (let y = 0; y < n; y++)
      for (let x = 0; x < n; x++) {
        const v = displayBoard[y][x]
        if (v === 1) stones.push({ color: 'B' as const, x, y })
        else if (v === -1) stones.push({ color: 'W' as const, x, y })
      }
    return {
      size: n as BoardSize,
      initialStones: stones,
      initialPlayer: player ?? 'B',
      rules,
      komi,
      source: 'photo',
    }
  }, [displayBoard, n, player, rules, komi])

  const dead = useMemo(() => (setupDraft ? deadGroupsInSetup(setupDraft) : []), [setupDraft])
  const counts = useMemo(() => {
    let b = 0
    let w = 0
    for (const s of setupDraft?.initialStones ?? []) s.color === 'B' ? b++ : w++
    return { b, w }
  }, [setupDraft])
  const settled = useMemo(
    () => (setupDraft ? countSettled(setupDraft.initialStones, setupDraft.size, rules, komi) : null),
    [setupDraft, rules, komi],
  )

  function tapPoint(dx: number, dy: number) {
    if (!board) return
    const [x, y] = toPhoto([dx, dy], n, orient)
    const next = board.map((r) => r.slice())
    const v = next[y][x]
    next[y][x] = v === 0 ? 1 : v === 1 ? -1 : 0
    setHistory([...history, board])
    setBoard(next)
    const u = new Set(uncertain)
    u.delete(`${x},${y}`)
    setUncertain(u)
  }

  function undoEdit() {
    if (!history.length) return
    setBoard(history[history.length - 1])
    setHistory(history.slice(0, -1))
  }

  async function confirm() {
    if (!setupDraft || !player || !board || !photo) return
    if (dead.length) {
      toast('Some stones have no liberties. Correct them first (highlighted).')
      return
    }
    if (saveCase) {
      try {
        await api.post(`/api/photos/${photo.id}/testcase`, {
          size: n,
          corners,
          board,
          next_player: player,
          note: `orientation rot=${orient.rot} flip=${orient.flip}`,
        })
        toast('Saved as a test case on the Mac')
      } catch (e) {
        toast(`Could not save test case: ${(e as Error).message}`)
      }
    }
    onConfirmed({ ...setupDraft, initialPlayer: player })
  }

  // ------------------------------------------------------------------ render
  const fileInputs = (
    <>
      <input
        ref={takeRef}
        type="file"
        accept="image/*"
        capture="environment"
        hidden
        data-testid="take-photo-input"
        onChange={(e) => {
          const f = (e.target as HTMLInputElement).files?.[0]
          if (f) void upload(f, f.name)
          ;(e.target as HTMLInputElement).value = ''
        }}
      />
      <input
        ref={pickRef}
        type="file"
        accept="image/*,.heic,.heif"
        hidden
        data-testid="choose-photo-input"
        onChange={(e) => {
          const f = (e.target as HTMLInputElement).files?.[0]
          if (f) void upload(f, f.name)
          ;(e.target as HTMLInputElement).value = ''
        }}
      />
    </>
  )

  const steps = (
    <div class="steps" aria-label="Steps">
      {(['1 Photo', '2 Corners', '3 Check stones', '4 Confirm'] as const).map((s, i) => (
        <span key={s} class={(step === 'capture' && i === 0) || (step === 'corners' && i === 1) || (step === 'review' && i >= 2) ? 'on' : ''}>
          {s}
        </span>
      ))}
    </div>
  )

  if (step === 'capture' || step === 'working') {
    return (
      <div class="screen photo-screen">
        {fileInputs}
        <section class="panel">
          {steps}
          <h2>Photograph the board</h2>
          <fieldset class="field">
            <legend>Board size</legend>
            <div class="seg">
              {SIZES.map((s) => (
                <button key={s} class={size === s ? 'on' : ''} aria-pressed={size === s} onClick={() => setSize(s)} data-testid={`photo-size-${s}`}>
                  {s}×{s}
                </button>
              ))}
            </div>
          </fieldset>
          <div class="photo-actions">
            <button class="btn big primary" onClick={() => takeRef.current?.click()} disabled={step === 'working'} data-testid="take-photo">
              📷 Take a photo
            </button>
            <button class="btn big" onClick={() => pickRef.current?.click()} disabled={step === 'working'} data-testid="choose-photo">
              🖼 Choose a photo
            </button>
          </div>
          {secure ? (
            <p class="small">
              <button class="linkish" onClick={() => setLive(true)}>
                Use live camera view
              </button>
            </p>
          ) : (
            <p class="muted tiny">
              Live camera preview needs HTTPS (see the phone setup guide). Taking or choosing a photo works without it.
            </p>
          )}
          {step === 'working' ? (
            <p class="note" role="status" data-testid="photo-busy">
              {busyMsg}
            </p>
          ) : null}
          {error ? (
            <p class="note error" role="alert" data-testid="photo-error">
              {error}
            </p>
          ) : null}
          <ul class="small muted">
            <li>Get the whole board in the photo, all four corners visible.</li>
            <li>Shoot from above the board (a moderate angle is fine), in even light without glare on the stones.</li>
            <li>The Mac reads the photo locally; nothing is sent to the internet.</li>
          </ul>
        </section>
        {live ? (
          <LiveCamera
            onCapture={(b) => {
              setLive(false)
              void upload(b, 'camera.jpg')
            }}
            onClose={() => setLive(false)}
          />
        ) : null}
      </div>
    )
  }

  const sizeHint =
    rec?.sizeSuggestion && photo ? (
      <p class="note small" data-testid="size-suggestion">
        The photo looks more like a {rec.sizeSuggestion}×{rec.sizeSuggestion} board.{' '}
        <button
          class="linkish"
          onClick={() => {
            const s = rec.sizeSuggestion as BoardSize
            setSize(s)
            void recognize(photo, s, null)
          }}
        >
          Try {rec.sizeSuggestion}×{rec.sizeSuggestion}
        </button>
      </p>
    ) : null

  if (step === 'corners' && photo) {
    return (
      <div class="screen photo-screen">
        {fileInputs}
        <section class="panel">
          {steps}
          <h2>Mark the grid corners</h2>
          {rec?.messages.map((m) => (
            <p key={m} class="note warn small">
              {m}
            </p>
          ))}
          {photo.warnings.map((m) => (
            <p key={m} class="note warn small">
              {m}
            </p>
          ))}
          {sizeHint}
          <p class="small">
            Drag the four red handles onto the <b>outermost line crossings</b> of the {size}×{size} grid. The blue grid
            should then lie on the board lines.
          </p>
          <CornerEditor url={photo.url} width={photo.width} height={photo.height} size={size} corners={corners} onChange={setCorners} editable />
          <div class="row" style={{ marginTop: '10px' }}>
            <button class="btn primary" disabled={!isConvex(corners)} onClick={() => recognize(photo, size, corners)} data-testid="use-corners">
              Read stones
            </button>
            <button class="btn" onClick={() => recognize(photo, size, null)}>
              Auto-detect again
            </button>
            <button class="btn" onClick={() => setStep('capture')}>
              New photo
            </button>
          </div>
        </section>
      </div>
    )
  }

  if (step === 'review' && photo && rec && displayBoard) {
    const deadSet = new Set(dead.map((s) => `${s.x},${s.y}`))
    return (
      <div class="screen photo-screen two-col">
        {fileInputs}
        <section class="panel">
          {steps}
          <h2>Check the photo</h2>
          <CornerEditor
            url={photo.url}
            width={photo.width}
            height={photo.height}
            size={n}
            corners={corners}
            editable={false}
            stones={board!.flatMap((row, y) =>
              row.flatMap((v, x) => (v === 1 || v === -1 ? [{ x, y, color: v as 1 | -1 }] : [])),
            )}
            uncertain={[...uncertain].map((k) => k.split(',').map(Number) as [number, number])}
          />
          <p class="muted tiny">
            Grid from {rec.cornerSource === 'manual' ? 'your corners' : `automatic detection (${rec.cornerSource})`}
            {rec.gridScore !== null ? `, fit ${rec.gridScore.toFixed(2)}` : ''} · detectors: {rec.detectors.join(' + ')}
          </p>
          <div class="row">
            <button class="btn small" onClick={() => setStep('corners')} data-testid="adjust-corners">
              Adjust corners
            </button>
            <button class="btn small" onClick={() => setStep('capture')}>
              Retake photo
            </button>
          </div>
        </section>
        <section class="panel">
          <h2>Correct the position</h2>
          {rec.messages.map((m) => (
            <p key={m} class="note warn small">
              {m}
            </p>
          ))}
          {sizeHint}
          <p class="small">
            Tap a point to cycle <b>black → white → empty</b>. Points marked <b>?</b> need a look.
          </p>
          <BoardView
            size={n}
            signMap={displayBoard}
            uncertain={displayUncertain}
            highlight={deadSet}
            ownership={settled?.owner}
            onPoint={tapPoint}
            ariaLabel="Reconstructed position"
          />
          <div class="row" style={{ marginTop: '8px' }}>
            <button class="btn small" onClick={undoEdit} disabled={!history.length} data-testid="undo-edit">
              Undo
            </button>
            <button class="btn small" onClick={() => setOrient(rotateCW(orient))} aria-label="Rotate board clockwise">
              ⟳ Rotate
            </button>
            <button class="btn small" onClick={() => setOrient(mirror(orient))}>
              ⇋ Mirror
            </button>
            <span class="small counts" data-testid="stone-counts">
              <span>
                <span class="dot B" /> {counts.b}
              </span>
              <span>
                <span class="dot W" /> {counts.w}
              </span>
              <span class={uncertain.size ? 'muted' : 'muted'} data-testid="uncertain-count">
                ? {uncertain.size}
              </span>
            </span>
          </div>
          {dead.length ? (
            <p class="note error small">{dead.length} stone(s) have no liberties; please correct them.</p>
          ) : null}
          <h3>Game settings</h3>
          <GameSettingsForm
            value={{ size: n as BoardSize, player, rules, komi }}
            sizeLocked
            idPrefix="photo"
            onChange={(v) => {
              setPlayer(v.player)
              setRules(v.rules)
              setKomi(v.komi)
            }}
          />
          {player === null ? (
            <p class="note warn small" data-testid="choose-player">
              Who plays next? Tap Black or White above. (Stone counts: Black {counts.b}, White {counts.w}
              {counts.b === counts.w ? ' – usually Black to play' : counts.b === counts.w + 1 ? ' – usually White to play' : ''}.)
            </p>
          ) : null}
          {settled ? <SettledCountBox count={settled} rules={rules} /> : null}
          <p class="muted tiny">
            One photo cannot show earlier moves, captures or a ko that is currently forbidden. KataGo will analyse this
            as a fresh position; moves you enter afterwards are kept.
          </p>
          <label class="toggle small">
            <input type="checkbox" checked={saveCase} onChange={() => setSaveCase(!saveCase)} /> Save photo + corrected
            position on the Mac as a recognition test case
          </label>
          <div class="row" style={{ marginTop: '10px' }}>
            <button class="btn primary big" disabled={!player || dead.length > 0} onClick={confirm} data-testid="confirm-position">
              Confirm position &amp; analyze
            </button>
          </div>
        </section>
      </div>
    )
  }

  return (
    <div class="screen">
      {fileInputs}
      <p>Loading… {status ? '' : '(not connected)'}</p>
    </div>
  )
}

function SettledCountBox({ count, rules }: { count: ReturnType<typeof countSettled>; rules: Rules }) {
  const { black, white, komi, lead } = count
  const even = Math.abs(lead) < 0.05
  const winner = lead > 0 ? 'B' : 'W'
  const whiteTotal = white.total + komi
  const label = (RULES.find((r) => r.id === rules)?.label ?? rules).replace(/ \(.*\)$/, '') // "Chinese (area)" → "Chinese"
  return (
    <div class="note small" data-testid="settled-count">
      <b>Count now:</b> Black {black.total} · White {white.total}
      {komi ? ` ${komi < 0 ? '−' : '+'} ${Math.abs(komi)} komi = ${whiteTotal}` : ''} · {leadText(lead)}
      {even ? ' → a draw on this count' : ` → ${colorName(winner)} wins on this count`}
      <div class="muted tiny" style={{ marginTop: '4px' }}>
        {count.method === 'area'
          ? `Area count (${label}): stones on the board + empty points surrounded by one colour.`
          : count.method === 'territory'
            ? `Territory count (${label}): empty points surrounded by one colour. Captures before the photo aren't known and count as 0.`
            : `Stone scoring: stones on the board only.`}{' '}
        Dead stones and unsettled areas aren't counted, so mid-game many points count for nobody.
        {count.method === 'stones' ? '' : ' Shaded points on the board are the settled ones.'}
      </div>
    </div>
  )
}
